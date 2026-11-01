"""Isolated multi-seed new-hire probe; never called by the dashboard.

The existing hire decision helper remains the reference for RNG and attributes.
This probe prepares the unchanged input state and distributions once per batch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator
from time import perf_counter
import warnings

import numpy as np
import pandas as pd
from config.settings import EDUCATION_GROUPS

from .forecast import (
    AZUBI_TRAINING_EDUCATION, DEFAULT_NEW_HIRE_EDUCATION,
    INTERNAL_AZUBI_FLAG_COL, INTERNAL_EDUCATION_COL, PeriodInfo,
    _annual_to_period_count, _build_education_distribution,
    _build_gender_distribution, _build_internal_azubi_series,
    _build_internal_education_series, _normalized_gender_series, _simulate_hires,
)


@dataclass(frozen=True)
class PreparedHireBatch:
    state: pd.DataFrame
    decision_state: pd.DataFrame
    params: dict[str, Any]
    periods: tuple[PeriodInfo, ...]
    org_units: list[str]
    vacancies: tuple[dict[str, Any], ...]
    counts: tuple[int, ...]
    start_date: pd.Timestamp
    gender_distribution: tuple[list[str], list[float]]
    education_distribution: tuple[list[str], list[float]]
    initial_genders: tuple[str, ...]
    initial_education: tuple[str, ...]


def _observed_distribution(values: list[str]) -> tuple[list[str], list[float]]:
    counts = pd.Series(values, dtype="object").value_counts(normalize=True)
    return counts.index.tolist(), counts.values.astype(float).tolist()


def prepare_hire_month(df_snapshot: pd.DataFrame, start_date: pd.Timestamp,
                       end_date: pd.Timestamp, params: dict[str, Any],
                       vacancies: list[dict[str, Any]] | None = None) -> PreparedHireBatch:
    """Prepare only one monthly period with new hires as the sole active component."""
    start_date, end_date = pd.Timestamp(start_date), pd.Timestamp(end_date)
    months = pd.period_range(start=start_date, end=end_date, freq="M")
    if len(months) != 1 or end_date < start_date:
        raise ValueError("The hire probe supports exactly one monthly period")
    return prepare_hire_batch(df_snapshot, start_date, end_date, params, vacancies)


def prepare_hire_batch(df_snapshot: pd.DataFrame, start_date: pd.Timestamp,
                       end_date: pd.Timestamp, params: dict[str, Any],
                       vacancies: list[dict[str, Any]] | None = None) -> PreparedHireBatch:
    """Prepare new-hire-only monthly counts and the immutable starting state."""
    start_date, end_date = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if end_date < start_date:
        raise ValueError("end_date must not precede start_date")
    if any(params.get(name, {}).get("active", True) for name in ("azubi", "trainee")):
        raise ValueError("The hire probe requires inactive azubi and trainee components")
    if not params.get("new_hires", {}).get("active", True):
        raise ValueError("The hire probe requires active new hires")
    state = df_snapshot.copy()
    if "active" not in state.columns:
        state["active"] = True
    state[INTERNAL_EDUCATION_COL] = _build_internal_education_series(state)
    state[INTERNAL_AZUBI_FLAG_COL] = _build_internal_azubi_series(state)
    if not state.empty and "PersNr" in state.columns:
        state.set_index("PersNr", drop=False, inplace=True)
        state.sort_index(inplace=True)
    org_units = list(params.get("available_org_units", []) or [])
    if not org_units and "Organisationseinheit" in state.columns:
        org_units = state["Organisationseinheit"].dropna().unique().tolist()
    periods, counts = [], []
    debt = 0.0
    for month in pd.period_range(start=start_date, end=end_date, freq="M"):
        period_start = max(month.start_time, start_date)
        period_end = min(month.end_time, end_date)
        if period_start > period_end:
            continue
        periods.append(PeriodInfo(period_start, period_end, month.strftime("%Y-%m")))
        count, debt = _annual_to_period_count(
            params.get("new_hires", {}).get("count_per_year", 0),
            (period_end - period_start).days + 1, np.random.default_rng(0), debt)
        counts.append(count)
    genders = _build_gender_distribution(state, _normalized_gender_series(state))
    education = _build_education_distribution(
        state, excluded_values=[AZUBI_TRAINING_EDUCATION],
        fallback_choices=[DEFAULT_NEW_HIRE_EDUCATION])
    initial_genders = tuple(_normalized_gender_series(state).tolist())
    initial_education_series = state[INTERNAL_EDUCATION_COL].dropna().astype(str).str.strip()
    initial_education_series = initial_education_series[
        initial_education_series.isin(EDUCATION_GROUPS)]
    initial_education_series = initial_education_series[
        ~initial_education_series.isin((AZUBI_TRAINING_EDUCATION,))]
    initial_education = tuple(initial_education_series.tolist())
    decision_columns = {"PersNr", "Organisationseinheit", "OE-Cluster"}
    decision_state = state[[column for column in state.columns
                            if column in decision_columns]].copy()
    return PreparedHireBatch(state, decision_state, params, tuple(periods), org_units,
                             tuple(sorted(vacancies or [], key=lambda row: row["date"])),
                             tuple(counts), start_date, genders, education,
                             initial_genders, initial_education)


def iter_hire_seed_records(prepared: PreparedHireBatch, seeds: Iterable[int],
                           profile: dict[str, float] | None = None,
                           ) -> Iterator[tuple[int, list[dict[str, Any]], list[dict[str, Any]]]]:
    """Yield exact hire events and generated person rows for each seed."""
    seen: set[int] = set()
    for seed in seeds:
        if profile is not None:
            phase_start = perf_counter()
        seed = int(seed)
        if seed in seen:
            raise ValueError(f"duplicate seed: {seed}")
        seen.add(seed)
        rng = np.random.default_rng(np.random.SeedSequence(seed).spawn(1)[0])
        events: list[dict[str, Any]] = []
        vacancy_state = [row.copy() for row in prepared.vacancies]
        state = prepared.decision_state.copy() if len(prepared.periods) > 1 else prepared.state
        genders_seen = list(prepared.initial_genders)
        education_seen = list(prepared.initial_education)
        if profile is not None:
            profile["seed_setup"] = profile.get("seed_setup", 0.0) + perf_counter() - phase_start
        for period_index, (period, count) in enumerate(zip(prepared.periods, prepared.counts)):
            period_events: list[dict[str, Any]] = []
            if count > 0:
                if profile is not None:
                    phase_start = perf_counter()
                genders = (prepared.gender_distribution if period_index == 0 else
                           _observed_distribution(genders_seen) if genders_seen else
                           (["m", "w"], [0.5, 0.5]))
                education = (prepared.education_distribution if period_index == 0 or
                             not education_seen else
                             _observed_distribution(education_seen))
                if profile is not None:
                    profile["distributions"] = profile.get("distributions", 0.0) + perf_counter() - phase_start
                    phase_start = perf_counter()
                _simulate_hires(
                    state, prepared.params, period, rng, period_events,
                    prepared.org_units, vacancy_state, count, prepared.start_date,
                    genders, education,
                )
                genders_seen.extend(event["Geschlecht"] for event in period_events)
                education_seen.extend(event["Ausbildung"] for event in period_events
                                      if event["Ausbildung"] in EDUCATION_GROUPS and
                                      event["Ausbildung"] != AZUBI_TRAINING_EDUCATION)
                if profile is not None:
                    profile["hire_decisions"] = profile.get("hire_decisions", 0.0) + perf_counter() - phase_start
            if len(prepared.periods) > 1 and period_events:
                if profile is not None:
                    phase_start = perf_counter()
                df_new = pd.DataFrame([
                    {key: value for key, value in event["_new_row"].items()
                     if key in ("PersNr", "Organisationseinheit", "OE-Cluster")}
                    for event in period_events])
                df_new.set_index("PersNr", drop=False, inplace=True)
                for column in df_new.columns:
                    if column not in state.columns:
                        state[column] = pd.NA
                df_new = df_new.reindex(columns=state.columns)
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*empty or all-NA.*",
                                            category=FutureWarning)
                    state = pd.concat([state, df_new])
                if profile is not None:
                    profile["state_append"] = profile.get("state_append", 0.0) + perf_counter() - phase_start
            events.extend(period_events)
        if profile is not None:
            phase_start = perf_counter()
        new_rows = [{key: value for key, value in event["_new_row"].items()
                     if key not in (INTERNAL_EDUCATION_COL, INTERNAL_AZUBI_FLAG_COL)}
                    for event in events]
        for event in events:
            event.pop("_new_row", None)
            event["PersNr"] = event["persnr"]
            event["Organisationseinheit"] = event["org_unit"]
            event["month"] = event["date"].to_period("M")
        if profile is not None:
            profile["record_cleanup"] = profile.get("record_cleanup", 0.0) + perf_counter() - phase_start
        yield seed, events, new_rows
