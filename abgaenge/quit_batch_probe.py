"""Isolated, quit-only multi-seed probe; never called by the dashboard.

The input is prepared once. Each seed still consumes the same component RNG
stream and the same number of draws per period as the reference forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .forecast import _annual_to_period_rates, _calc_age, _periods, _select_quit_probs_frame
from .schemas import (COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_PHASE,
                      COL_BSGRD, COL_GEB, COL_PERSNR, COL_SOLL, COL_STATUS,
                      REASON_LABELS, REASON_QUIT)

EMPTY_EVENT_COLUMNS = [
    "period_label", "period_start", "period_end", "event_date", "persnr",
    "reason_code", "reason_label", "headcount_change", "mak_change", "age",
    "tenure", "Organisationseinheit", "Kürzel OrgEinheit", "Jobfamily",
    "Planstelle", "TrfGr", "St",
]


@dataclass(frozen=True)
class PreparedQuitBatch:
    ids: np.ndarray
    mak: np.ndarray
    org: np.ndarray
    jobfamily: np.ndarray
    periods: tuple
    eligible_positions: np.ndarray
    ages: tuple[np.ndarray, ...]
    annual_probs: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class QuitSeedResult:
    events: pd.DataFrame
    active: np.ndarray
    mak: np.ndarray


def prepare_quit_batch(df_ma: pd.DataFrame, df_atz: pd.DataFrame,
                       start_date: pd.Timestamp, end_date: pd.Timestamp,
                       params: dict[str, Any], freq: str = "M") -> PreparedQuitBatch:
    """Prepare immutable person/period data for an isolated quit-only scenario."""
    components = params.get("components", {})
    if components != {"atz": False, "retirement": False, "quit": True, "ruhend": False}:
        raise ValueError("The probe supports only explicitly quit-only components")
    start_date, end_date = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if end_date <= start_date:
        raise ValueError("end_date must be after start_date")
    state = df_ma.copy()
    state[COL_PERSNR] = state[COL_PERSNR].astype(str).str.split(".").str[0].str.zfill(6)
    state = state.set_index(COL_PERSNR, drop=True)
    state = state[~state.index.duplicated(keep="first")].sort_index()
    ids = state.index.to_numpy(copy=True)
    atz_ids = (set(df_atz[COL_PERSNR].dropna().astype(str).str.split(".").str[0].str.zfill(6))
               if not df_atz.empty else set())
    eligible_positions = np.flatnonzero(~state.index.isin(atz_ids))
    bsgrd = pd.to_numeric(state[COL_BSGRD], errors="coerce").fillna(0.0)
    if COL_SOLL in state.columns:
        hours = pd.to_numeric(state[COL_SOLL], errors="coerce").fillna(0.0)
        factor = np.where(hours <= 0.01, 39.0, hours) / 39.0
    else:
        factor = 1.0
    if "MAK_Calculated" in state.columns:
        mak = pd.to_numeric(state["MAK_Calculated"], errors="coerce").fillna(0.0).to_numpy(copy=True)
    else:
        raw = (bsgrd / 100.0) * factor
        ruhend = state[COL_STATUS].eq("Ruhendes Beschäftigungsverhältnis").to_numpy()
        mak = np.where(ruhend, 0.0, raw).astype(float)
    if not df_atz.empty and COL_ATZ_PHASE in df_atz.columns:
        phases = df_atz[COL_ATZ_PHASE].astype(str).str.strip().str.casefold()
        fr_rows = df_atz[phases.isin(("fr", "freistellungsphase", "passivphase"))]
        fr_begin = pd.to_datetime(fr_rows[COL_ATZ_BEGINN], errors="coerce")
        fr_end = pd.to_datetime(fr_rows[COL_ATZ_ENDE], errors="coerce")
        fr_active = fr_rows[(fr_begin <= start_date) & (fr_end >= start_date)]
        fr_ids = set(fr_active[COL_PERSNR].dropna().astype(str).str.split(".").str[0].str.zfill(6))
        mak[state.index.isin(fr_ids)] = 0.0
    periods = tuple(_periods(start_date, end_date, freq))
    ages, annual_probs = [], []
    eligible = state.iloc[eligible_positions].copy()
    for period in periods:
        age = _calc_age(state[COL_GEB], period.start).to_numpy()
        eligible["age"] = age[eligible_positions]
        ages.append(age)
        annual_probs.append(_select_quit_probs_frame(eligible, params, period.start.year))
    return PreparedQuitBatch(ids, mak, state["Organisationseinheit"].to_numpy(),
                             state["Jobfamily"].to_numpy(), periods, eligible_positions,
                             tuple(ages), tuple(annual_probs))


def run_quit_batch(prepared: PreparedQuitBatch, seeds: Iterable[int]) -> dict[int, QuitSeedResult]:
    """Run seeds independently over the shared prepared arrays; preserve seed order."""
    results: dict[int, QuitSeedResult] = {}
    for seed in seeds:
        seed = int(seed)
        if seed in results:
            raise ValueError(f"duplicate seed: {seed}")
        rng = np.random.default_rng(np.random.SeedSequence(seed).spawn(5)[2])
        active = np.ones(len(prepared.ids), dtype=bool)
        mak = prepared.mak.copy()
        events = []
        for period, ages, annual in zip(prepared.periods, prepared.ages, prepared.annual_probs):
            eligible_positions = prepared.eligible_positions
            eligible = eligible_positions[active[eligible_positions]]
            if len(eligible) == 0:
                continue
            annual_active = annual[active[eligible_positions]]
            days = max(1, (period.end - period.start).days) + 1
            probs = _annual_to_period_rates(annual_active, days)
            selected = eligible[rng.random(len(eligible)) < probs]
            for pos in selected:
                events.append({
                    "period_label": period.label,
                    "period_start": period.start,
                    "period_end": period.end,
                    "event_date": period.end,
                    "persnr": prepared.ids[pos],
                    "reason_code": REASON_QUIT,
                    "reason_label": REASON_LABELS[REASON_QUIT],
                    "Organisationseinheit": str(prepared.org[pos]),
                    "Jobfamily": str(prepared.jobfamily[pos]),
                    "headcount_change": -1,
                    "mak_change": -float(mak[pos]),
                    "age": float(ages[pos]),
                })
                active[pos] = False
                mak[pos] = 0.0
        results[seed] = QuitSeedResult(
            pd.DataFrame(events) if events else pd.DataFrame(columns=EMPTY_EVENT_COLUMNS),
            active, mak,
        )
    return results
