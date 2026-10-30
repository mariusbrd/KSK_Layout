"""Isolated retirement/quit multi-seed probe; never called by the dashboard.

The input is prepared once. Each seed still consumes the same component RNG
stream and the same number of draws per period as the reference forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

import numpy as np
import pandas as pd

from .forecast import (_annual_to_period_rate, _annual_to_period_rates, _calc_age,
                       _calc_tenure, _periods, _select_quit_probs_frame,
                       _build_atz_pivot, _enforce_min_ar_duration,
                       _get_atz_events_from_schedule, _select_atz_probs_frame)
from .params import get_retirement_ages
from .schemas import (COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_PHASE,
                      COL_BSGRD, COL_EINTRITT, COL_GEB, COL_PERSNR, COL_SOLL, COL_STATUS,
                      REASON_LABELS, REASON_QUIT, REASON_RETIREMENT,
                      REASON_RUHEND_RETURN, REASON_RUHEND_START,
                      REASON_ATZ_AR_TO_FR, REASON_ATZ_END, COL_OE_CODE,
                      EXCLUDED_OE_CODES)
from dataloader.jobfamily_service import normalize_jobfamily_value
from .schemas import normalize_persnr

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
    retirement_probs: tuple[np.ndarray, ...]
    include_quit: bool
    include_retirement: bool
    initial_ruhend: np.ndarray
    return_probs: tuple[float, ...]
    new_ruhend_expected: tuple[float, ...]
    ruhend_months: int
    include_ruhend: bool
    start_date: pd.Timestamp
    initial_ruhend_until: np.ndarray
    new_ruhend_until: tuple[pd.Timestamp, ...]
    atz_transitions: tuple[tuple[np.ndarray, np.ndarray], ...]
    initial_atz_fr: np.ndarray
    tenures: tuple[np.ndarray, ...]
    include_atz: bool
    initial_in_atz: np.ndarray
    atz_probabilities: tuple[np.ndarray, ...]
    atz_age_eligible: tuple[np.ndarray, ...]
    atz_oe_eligible: np.ndarray
    atz_ar_months: int
    atz_fr_months: int


@dataclass(frozen=True)
class QuitSeedResult:
    events: pd.DataFrame
    active: np.ndarray
    mak: np.ndarray
    status_ruhend: np.ndarray


@dataclass(frozen=True)
class PreparedQuitYearly:
    oe_map: dict[str, str]
    org_assignments: dict[str, str]
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
    original = baseline.copy(deep=True)
    if "Is_Vacant" in original:
        original = original.loc[~original["Is_Vacant"].fillna(True).astype(bool)].copy()
    original["_person"] = normalize_persnr(original["PersNr"])
    original["_org"] = original["Organisationseinheit"].fillna("").astype(str).str.strip()
    original = original.loc[original["_person"].notna() & original["_org"].ne("")]
    org_choices = original.groupby("_person")["_org"].agg(lambda values: sorted(set(values)))
    org_assignments = {person: values[0] for person, values in org_choices.items()
                       if len(values) == 1}
    return PreparedQuitYearly(
        {str(key).strip(): str(value).strip() if pd.notna(value) and str(value).strip()
         else "Nicht zugeordnet" for key, value in oe_map.items()},
        org_assignments,
        prepared["Jobfamily"]["assignments"], prepared["Jobfamily"]["members"],
        prepared["JF-Cluster"]["assignments"], prepared["JF-Cluster"]["members"],
        cluster_map,
    )


def prepare_quit_batch(df_ma: pd.DataFrame, df_atz: pd.DataFrame,
                       start_date: pd.Timestamp, end_date: pd.Timestamp,
                       params: dict[str, Any], freq: str = "M") -> PreparedQuitBatch:
    """Prepare immutable person/period data for retirement, quits and Ruhend."""
    components = params.get("components", {})
    if (not any(components.get(key) is True for key in ("atz", "retirement", "quit", "ruhend"))
            or set(components) != {"atz", "retirement", "quit", "ruhend"}):
        raise ValueError("The probe requires explicit ATZ/retirement/quit/Ruhend components")
    atz_params = params.get("atz", {})
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
    fr_ids: set[str] = set()
    if not df_atz.empty and COL_ATZ_PHASE in df_atz.columns:
        phases = df_atz[COL_ATZ_PHASE].astype(str).str.strip().str.casefold()
        fr_rows = df_atz[phases.isin(("fr", "freistellungsphase", "passivphase"))]
        fr_begin = pd.to_datetime(fr_rows[COL_ATZ_BEGINN], errors="coerce")
        fr_end = pd.to_datetime(fr_rows[COL_ATZ_ENDE], errors="coerce")
        fr_active = fr_rows[(fr_begin <= start_date) & (fr_end >= start_date)]
        fr_ids = set(fr_active[COL_PERSNR].dropna().astype(str).str.split(".").str[0].str.zfill(6))
        mak[state.index.isin(fr_ids)] = 0.0
    periods = tuple(_periods(start_date, end_date, freq))
    ages, tenures, period_probs, retirement_probs = [], [], [], []
    return_probs, new_ruhend_expected, new_ruhend_until = [], [], []
    ruhend_params = params.get("ruhend", {})
    ruhend_months = int(ruhend_params.get("ruhend_avg_duration_months", 12))
    eligible = state.copy()
    atz_probabilities, atz_age_eligible = [], []
    atz_min = int(atz_params.get("atz_eligible_age_min", 55))
    atz_max = int(atz_params.get("atz_eligible_age_max", 60))
    atz_oe_eligible = (~state[COL_OE_CODE].astype(str).isin(EXCLUDED_OE_CODES).to_numpy()
                       if COL_OE_CODE in state.columns else np.ones(len(state), dtype=bool))
    if components["retirement"]:
        retirement_age, early_retirement_age = get_retirement_ages(params.get("retirement", {}))
    for period in periods:
        age = _calc_age(state[COL_GEB], period.start).to_numpy()
        tenures.append(_calc_tenure(state[COL_EINTRITT], period.start).to_numpy())
        eligible["age"] = age
        ages.append(age)
        atz_age_eligible.append((age >= atz_min) & (age <= atz_max))
        atz_probabilities.append(_select_atz_probs_frame(
            eligible, params, (period.end - period.start).days / 365.25)
            if components["atz"] else np.zeros(len(state), dtype=float))
        if components["quit"]:
            annual = _select_quit_probs_frame(eligible, params, period.start.year)
            days = max(1, (period.end - period.start).days) + 1
            period_probs.append(_annual_to_period_rates(annual, days))
        else:
            period_probs.append(np.zeros(len(state), dtype=float))
        if components["retirement"]:
            period_days = max(1, (period.end - period.start).days)
            p65 = _annual_to_period_rate(params.get("retirement", {}).get("rent_rate_65", 0.9), period_days)
            p60 = _annual_to_period_rate(params.get("retirement", {}).get("rent_rate_60_65", 0.1), period_days)
            eligible_ages = age
            retirement_probs.append(np.where(
                eligible_ages >= retirement_age, p65,
                np.where((eligible_ages >= early_retirement_age) &
                         (eligible_ages < retirement_age), p60, 0.0)))
        else:
            retirement_probs.append(np.zeros(len(state), dtype=float))
        period_days = max(1, (period.end - period.start).days)
        return_probs.append(_annual_to_period_rate(
            float(ruhend_params.get("ruhend_return_rate", 0.95)), period_days))
        new_ruhend_expected.append(
            float(ruhend_params.get("ruhend_new_cases_per_year", 0)) *
            (period_days / 365.25))
        new_ruhend_until.append(period.start + pd.DateOffset(months=ruhend_months))
    initial_ruhend = state[COL_STATUS].eq("Ruhendes Beschäftigungsverhältnis").to_numpy()
    initial_until = np.full(len(state), pd.NaT, dtype=object)
    initial_until[initial_ruhend] = start_date + pd.DateOffset(months=ruhend_months)
    atz_transitions = []
    if components["atz"]:
        pivot = _build_atz_pivot(df_atz)
        if not pivot.empty:
            pivot[COL_PERSNR] = pivot[COL_PERSNR].astype(str).str.split(".").str[0].str.zfill(6)
            pivot = pivot[pivot[COL_PERSNR].isin(set(ids))].copy()
            pivot["fr_begin"] = pd.to_datetime(pivot["fr_begin"], errors="coerce")
            pivot["contract_end"] = pd.to_datetime(pivot["contract_end"], errors="coerce")
        try:
            min_ar_months = int(round(float(atz_params.get("atz_duration_ar_years", 2.5)) * 12))
        except (ValueError, TypeError):
            min_ar_months = 30
        if min_ar_months < 6:
            min_ar_months = 30
        min_fr_months = int(round(float(atz_params.get("atz_duration_fr_years", 2.5)) * 12))
        pivot = _enforce_min_ar_duration(pivot, min_ar_months, min_fr_months, fr_ids)
        id_positions = {person: index for index, person in enumerate(ids)}
        for period in periods:
            ar_to_fr, atz_end = _get_atz_events_from_schedule(pivot, period.start, period.end)
            atz_transitions.append((
                np.array([id_positions[person] for person in sorted(ar_to_fr)], dtype=int),
                np.array([id_positions[person] for person in sorted(atz_end)], dtype=int),
            ))
    else:
        atz_transitions = [(np.empty(0, dtype=int), np.empty(0, dtype=int)) for _ in periods]
    return PreparedQuitBatch(ids, mak, state["Organisationseinheit"].to_numpy(),
                             state["Jobfamily"].to_numpy(), periods, eligible_positions,
                             tuple(ages), tuple(period_probs), tuple(retirement_probs),
                             bool(components["quit"]), bool(components["retirement"]),
                             initial_ruhend,
                             tuple(return_probs), tuple(new_ruhend_expected),
                             ruhend_months, bool(components["ruhend"]), start_date,
                             initial_until, tuple(new_ruhend_until), tuple(atz_transitions),
                             state.index.isin(fr_ids), tuple(tenures), bool(components["atz"]),
                             state.index.isin(atz_ids), tuple(atz_probabilities),
                             tuple(atz_age_eligible), atz_oe_eligible,
                             int(round(float(atz_params.get("atz_duration_ar_years", 2.5)) * 12)),
                             int(round(float(atz_params.get("atz_duration_fr_years", 2.5)) * 12)))


def _iter_departure_seed_states(
    prepared: PreparedQuitBatch, seeds: Iterable[int],
) -> Iterator[tuple[int, list[dict[str, Any]], np.ndarray, np.ndarray, np.ndarray]]:
    """Yield one seed's full mutable state; no per-seed DataFrame is created."""
    seen: set[int] = set()
    for seed in seeds:
        seed = int(seed)
        if seed in seen:
            raise ValueError(f"duplicate seed: {seed}")
        seen.add(seed)
        component_seeds = np.random.SeedSequence(seed).spawn(5)
        rng_atz = np.random.default_rng(component_seeds[0])
        rng_ret = np.random.default_rng(component_seeds[1])
        rng = np.random.default_rng(component_seeds[2])
        rng_ruh_new = np.random.default_rng(component_seeds[3])
        rng_ruh_ret = np.random.default_rng(component_seeds[4])
        active = np.ones(len(prepared.ids), dtype=bool)
        mak = prepared.mak.copy()
        status_ruhend = prepared.initial_ruhend.copy()
        atz_fr_active = prepared.initial_atz_fr.copy()
        in_atz = prepared.initial_in_atz.copy()
        atz_plans = [[list(fr), list(end)] for fr, end in prepared.atz_transitions]
        mak_before_ruhend = mak.copy()
        ruhend_until = (prepared.initial_ruhend_until.copy() if prepared.include_ruhend
                        else np.empty(0, dtype=object))
        events = []
        for period_index, (period, ages, tenure, all_probs, ret_probs, return_prob, new_expected, new_until, atz_probs, atz_age) in enumerate(zip(
            prepared.periods, prepared.ages, prepared.tenures,
            prepared.period_probs, prepared.retirement_probs,
            prepared.return_probs, prepared.new_ruhend_expected, prepared.new_ruhend_until,
            prepared.atz_probabilities, prepared.atz_age_eligible,
        )):
            if prepared.include_atz:
                atz_candidates = np.flatnonzero(
                    active & ~in_atz & ~status_ruhend & atz_age & prepared.atz_oe_eligible)
                if len(atz_candidates):
                    probs = atz_probs[atz_candidates]
                    expected = float(np.sum(probs))
                    if expected > 0:
                        count = int(rng_atz.poisson(lam=expected))
                        positive = probs > 0
                        candidates = atz_candidates[positive]
                        count = min(count, len(candidates))
                        if count > 0:
                            positive_probs = probs[positive]
                            weights = positive_probs / float(np.sum(positive_probs))
                            chosen = rng_atz.choice(len(candidates), size=count,
                                                    replace=False, p=weights)
                            for pos in candidates[chosen]:
                                offset_days = rng_atz.integers(
                                    0, max(1, (period.end - period.start).days + 1))
                                ar_start = period.start + pd.Timedelta(days=int(offset_days))
                                fr_start = (ar_start + pd.DateOffset(
                                    months=prepared.atz_ar_months) + pd.Timedelta(days=1))
                                contract_end = fr_start + pd.DateOffset(
                                    months=prepared.atz_fr_months)
                                in_atz[pos] = True
                                for future_index in range(period_index, len(prepared.periods)):
                                    future = prepared.periods[future_index]
                                    if future.start <= fr_start <= future.end:
                                        atz_plans[future_index][0].append(int(pos))
                                    if future.start <= contract_end <= future.end:
                                        atz_plans[future_index][1].append(int(pos))
                ar_to_fr, atz_end = atz_plans[period_index]
                for pos in sorted(set(ar_to_fr)):
                    if active[pos] and not atz_fr_active[pos]:
                        change = -float(mak[pos])
                        atz_fr_active[pos] = True
                        mak[pos] = 0.0
                        events.append({
                            "period_label": period.label,
                            "period_start": period.start,
                            "period_end": period.end,
                            "event_date": period.end,
                            "persnr": prepared.ids[pos],
                            "reason_code": REASON_ATZ_AR_TO_FR,
                            "reason_label": REASON_LABELS[REASON_ATZ_AR_TO_FR],
                            "headcount_change": 0,
                            "mak_change": change,
                            "age": float(ages[pos]),
                            "tenure": float(tenure[pos]),
                            "Organisationseinheit": str(prepared.org[pos]),
                            "Jobfamily": str(prepared.jobfamily[pos]),
                        })
                for pos in sorted(set(atz_end)):
                    if active[pos]:
                        change = -float(mak[pos])
                        mak[pos] = 0.0
                        active[pos] = False
                        events.append({
                            "period_label": period.label,
                            "period_start": period.start,
                            "period_end": period.end,
                            "event_date": period.end,
                            "persnr": prepared.ids[pos],
                            "reason_code": REASON_ATZ_END,
                            "reason_label": REASON_LABELS[REASON_ATZ_END],
                            "headcount_change": -1,
                            "mak_change": change,
                            "age": float(ages[pos]),
                        })
            if prepared.include_retirement:
                eligible_positions = np.flatnonzero(~in_atz)
                eligible_mask = active[eligible_positions]
                eligible = eligible_positions[eligible_mask]
                if len(eligible):
                    selected = eligible[rng_ret.random(len(eligible)) < ret_probs[eligible]]
                    for pos in selected:
                        events.append({
                            "period_label": period.label,
                            "period_start": period.start,
                            "period_end": period.end,
                            "event_date": period.end,
                            "persnr": prepared.ids[pos],
                            "reason_code": REASON_RETIREMENT,
                            "reason_label": REASON_LABELS[REASON_RETIREMENT],
                            "Organisationseinheit": str(prepared.org[pos]),
                            "Jobfamily": str(prepared.jobfamily[pos]),
                            "headcount_change": -1,
                            "mak_change": -float(mak[pos]),
                            "age": float(ages[pos]),
                        })
                        active[pos] = False
                        mak[pos] = 0.0
            if prepared.include_quit:
                eligible_positions = np.flatnonzero(~in_atz)
                eligible_mask = active[eligible_positions]
                eligible = eligible_positions[eligible_mask]
                if len(eligible):
                    probs = all_probs[eligible]
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
            if prepared.include_ruhend:
                eligible_positions = np.flatnonzero(~in_atz)
                return_candidates = np.flatnonzero(active & status_ruhend)
                can_return = [pos for pos in return_candidates
                              if pd.notna(ruhend_until[pos]) and ruhend_until[pos] <= period.start]
                if can_return:
                    selected = np.asarray(can_return)[
                        rng_ruh_ret.random(len(can_return)) < return_prob]
                    for pos in selected:
                        restored_mak = float(mak_before_ruhend[pos])
                        status_ruhend[pos] = False
                        mak[pos] = restored_mak
                        ruhend_until[pos] = pd.NaT
                        events.append({
                            "period_label": period.label,
                            "event_date": period.end,
                            "persnr": prepared.ids[pos],
                            "reason_code": REASON_RUHEND_RETURN,
                            "reason_label": REASON_LABELS[REASON_RUHEND_RETURN],
                            "Organisationseinheit": str(prepared.org[pos]),
                            "Jobfamily": str(prepared.jobfamily[pos]),
                            "headcount_change": 0,
                            "mak_change": restored_mak,
                        })
                if new_expected > 0:
                    count = int(rng_ruh_new.poisson(lam=new_expected))
                    if count > 0:
                        candidates = eligible_positions[
                            active[eligible_positions] & ~status_ruhend[eligible_positions]]
                        if len(candidates):
                            chosen = candidates[rng_ruh_new.choice(
                                len(candidates), size=min(count, len(candidates)), replace=False)]
                            for pos in chosen:
                                current_mak = float(mak[pos])
                                mak_before_ruhend[pos] = current_mak
                                status_ruhend[pos] = True
                                mak[pos] = 0.0
                                ruhend_until[pos] = new_until
                                events.append({
                                    "period_label": period.label,
                                    "event_date": period.end,
                                    "persnr": prepared.ids[pos],
                                    "reason_code": REASON_RUHEND_START,
                                    "reason_label": REASON_LABELS[REASON_RUHEND_START],
                                    "Organisationseinheit": str(prepared.org[pos]),
                                    "Jobfamily": str(prepared.jobfamily[pos]),
                                    "headcount_change": 0,
                                    "mak_change": -current_mak,
                                })
        yield seed, events, active, mak, status_ruhend


def iter_quit_seed_records(
    prepared: PreparedQuitBatch, seeds: Iterable[int],
) -> Iterator[tuple[int, list[dict[str, Any]], np.ndarray, np.ndarray]]:
    """Yield event records, active mask and MAK for one seed at a time."""
    for seed, events, active, mak, _ in _iter_departure_seed_states(prepared, seeds):
        yield seed, events, active, mak


def run_quit_batch(prepared: PreparedQuitBatch, seeds: Iterable[int]) -> dict[int, QuitSeedResult]:
    """Materialize small batches for exact comparison with the reference."""
    results: dict[int, QuitSeedResult] = {}
    for seed, events, active, mak, status_ruhend in _iter_departure_seed_states(prepared, seeds):
        results[seed] = QuitSeedResult(
            pd.DataFrame(events) if events else pd.DataFrame(columns=EMPTY_EVENT_COLUMNS),
            active, mak, status_ruhend,
        )
    return results


def quit_annual_event_values(
    records: list[dict[str, Any]], yearly: PreparedQuitYearly | None = None,
) -> dict[tuple[int, str, str], tuple[int, float]]:
    """Aggregate quit-only annual event values without a per-seed DataFrame."""
    grouped: dict[tuple[int, str, str], list[tuple[int, float]]] = {}
    for event in records:
        year = event["event_date"].year
        person = str(event["persnr"])
        org = event.get("Organisationseinheit")
        if org is None or str(org).strip() in ("", "nan", "None", "Nicht zugeordnet"):
            org = yearly.org_assignments.get(person) if yearly is not None else None
        org = str(org).strip() if org is not None else "Nicht zugeordnet"
        jobfamily = event.get("Jobfamily", "")
        grouped.setdefault((year, "Organisationseinheit", org), []).append(
            (event["headcount_change"], event["mak_change"]))
        if yearly is None:
            grouped.setdefault((year, "Jobfamily", normalize_jobfamily_value(jobfamily)), []).append(
                (event["headcount_change"], event["mak_change"]))
        if yearly is not None:
            oe_cluster = yearly.oe_map.get(org, "Nicht zugeordnet")
            grouped.setdefault((year, "OE-Cluster", oe_cluster), []).append(
                (event["headcount_change"], event["mak_change"]))
            for dimension, assignments, members in (
                ("Jobfamily", yearly.jf_assignments, yearly.jf_members),
                ("JF-Cluster", yearly.cluster_assignments, yearly.cluster_members),
            ):
                if person in members:
                    groups = members[person]
                elif person in assignments:
                    groups = [(assignments[person], 1.0)]
                elif dimension == "Jobfamily":
                    groups = [(normalize_jobfamily_value(jobfamily), 1.0)]
                else:
                    groups = [(yearly.jf_cluster_map.get(str(jobfamily), "UNMAPPED"), 1.0)]
                for group, ratio in groups:
                    grouped.setdefault((year, dimension, str(group)), []).append((
                        event["headcount_change"], float(event["mak_change"]) * ratio))
    def compensated_sum(values: list[float]) -> float:
        total = 0.0
        correction = 0.0
        for value in values:
            adjusted = value - correction
            updated = total + adjusted
            correction = (updated - total) - adjusted
            total = updated
        return total

    return {key: (sum(headcount for headcount, _ in values),
                  compensated_sum([mak for _, mak in values]))
            for key, values in grouped.items()}


def departure_annual_event_values(
    records: list[dict[str, Any]], yearly: PreparedQuitYearly,
) -> dict[tuple[int, str, str, str], tuple[int, float]]:
    """Keep retirement and quit annual groups separate like the reference views."""
    values = {}
    for reason in (REASON_ATZ_AR_TO_FR, REASON_ATZ_END, REASON_RETIREMENT,
                   REASON_QUIT, REASON_RUHEND_RETURN, REASON_RUHEND_START):
        selected = [record for record in records if record["reason_code"] == reason]
        values.update({(*key, reason): value
                       for key, value in quit_annual_event_values(selected, yearly).items()})
    return values
