"""Isolated, quit-only multi-seed probe; never called by the dashboard.

The input is prepared once. Each seed still consumes the same component RNG
stream and the same number of draws per period as the reference forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

import numpy as np
import pandas as pd

from .forecast import _annual_to_period_rates, _calc_age, _periods, _select_quit_probs_frame
from .schemas import (COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_PHASE,
                      COL_BSGRD, COL_GEB, COL_PERSNR, COL_SOLL, COL_STATUS,
                      REASON_LABELS, REASON_QUIT)
from dataloader.jobfamily_service import normalize_jobfamily_value

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
    period_probs: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class QuitSeedResult:
    events: pd.DataFrame
    active: np.ndarray
    mak: np.ndarray


@dataclass(frozen=True)
class PreparedQuitYearly:
    oe_map: dict[str, str]
    jf_assignments: dict[str, str]
    jf_members: dict[str, list[tuple[str, float]]]
    cluster_assignments: dict[str, str]
    cluster_members: dict[str, list[tuple[str, float]]]
    jf_cluster_map: dict[str, str]


def prepare_quit_yearly(baseline: pd.DataFrame, oe_map: dict[str, str]) -> PreparedQuitYearly:
    """Prepare the baseline assignments used by the existing OE/JF yearly views."""
    from components.jobfamily_simulation_yearly import prepare_jobfamily_baseline

    prepared = prepare_jobfamily_baseline(baseline)
    names = baseline[["Jobfamily", "JF-Cluster"]].dropna().drop_duplicates()
    clusters = names.groupby("Jobfamily")["JF-Cluster"].agg(lambda values: set(values))
    cluster_map = {str(name): next(iter(values)) for name, values in clusters.items()
                   if len(values) == 1}
    return PreparedQuitYearly(
        {str(key).strip(): str(value).strip() if pd.notna(value) and str(value).strip()
         else "Nicht zugeordnet" for key, value in oe_map.items()},
        prepared["Jobfamily"]["assignments"], prepared["Jobfamily"]["members"],
        prepared["JF-Cluster"]["assignments"], prepared["JF-Cluster"]["members"],
        cluster_map,
    )


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
    ages, period_probs = [], []
    eligible = state.iloc[eligible_positions].copy()
    for period in periods:
        age = _calc_age(state[COL_GEB], period.start).to_numpy()
        eligible["age"] = age[eligible_positions]
        ages.append(age)
        annual = _select_quit_probs_frame(eligible, params, period.start.year)
        days = max(1, (period.end - period.start).days) + 1
        period_probs.append(_annual_to_period_rates(annual, days))
    return PreparedQuitBatch(ids, mak, state["Organisationseinheit"].to_numpy(),
                             state["Jobfamily"].to_numpy(), periods, eligible_positions,
                             tuple(ages), tuple(period_probs))


def iter_quit_seed_records(
    prepared: PreparedQuitBatch, seeds: Iterable[int],
) -> Iterator[tuple[int, list[dict[str, Any]], np.ndarray, np.ndarray]]:
    """Yield one seed at a time, without creating or retaining event DataFrames."""
    seen: set[int] = set()
    for seed in seeds:
        seed = int(seed)
        if seed in seen:
            raise ValueError(f"duplicate seed: {seed}")
        seen.add(seed)
        rng = np.random.default_rng(np.random.SeedSequence(seed).spawn(5)[2])
        active = np.ones(len(prepared.ids), dtype=bool)
        mak = prepared.mak.copy()
        events = []
        for period, ages, all_probs in zip(prepared.periods, prepared.ages, prepared.period_probs):
            eligible_positions = prepared.eligible_positions
            eligible = eligible_positions[active[eligible_positions]]
            if len(eligible) == 0:
                continue
            probs = all_probs[active[eligible_positions]]
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
        yield seed, events, active, mak


def run_quit_batch(prepared: PreparedQuitBatch, seeds: Iterable[int]) -> dict[int, QuitSeedResult]:
    """Materialize small batches for exact comparison with the reference."""
    results: dict[int, QuitSeedResult] = {}
    for seed, events, active, mak in iter_quit_seed_records(prepared, seeds):
        results[seed] = QuitSeedResult(
            pd.DataFrame(events) if events else pd.DataFrame(columns=EMPTY_EVENT_COLUMNS),
            active, mak,
        )
    return results


def quit_annual_event_values(
    records: list[dict[str, Any]], yearly: PreparedQuitYearly | None = None,
) -> dict[tuple[int, str, str], tuple[int, float]]:
    """Aggregate quit-only annual event values without a per-seed DataFrame."""
    grouped: dict[tuple[int, str, str], list[float]] = {}
    for event in records:
        year = event["event_date"].year
        grouped.setdefault((year, "Organisationseinheit", event["Organisationseinheit"]), []).append(
            event["mak_change"])
        if yearly is None:
            grouped.setdefault((year, "Jobfamily", event["Jobfamily"]), []).append(
                event["mak_change"])
        if yearly is not None:
            person = str(event["persnr"])
            org = str(event["Organisationseinheit"]).strip() or "Nicht zugeordnet"
            oe_cluster = yearly.oe_map.get(org, "Nicht zugeordnet")
            grouped.setdefault((year, "OE-Cluster", oe_cluster), []).append(event["mak_change"])
            for dimension, assignments, members in (
                ("Jobfamily", yearly.jf_assignments, yearly.jf_members),
                ("JF-Cluster", yearly.cluster_assignments, yearly.cluster_members),
            ):
                if person in members:
                    groups = members[person]
                elif person in assignments:
                    groups = [(assignments[person], 1.0)]
                elif dimension == "Jobfamily":
                    groups = [(normalize_jobfamily_value(event["Jobfamily"]), 1.0)]
                else:
                    groups = [(yearly.jf_cluster_map.get(str(event["Jobfamily"]), "UNMAPPED"), 1.0)]
                for group, ratio in groups:
                    grouped.setdefault((year, dimension, str(group)), []).append(
                        float(event["mak_change"]) * ratio)
    def compensated_sum(values: list[float]) -> float:
        total = 0.0
        correction = 0.0
        for value in values:
            adjusted = value - correction
            updated = total + adjusted
            correction = (updated - total) - adjusted
            total = updated
        return total

    return {key: (-len(values), compensated_sum(values)) for key, values in grouped.items()}
