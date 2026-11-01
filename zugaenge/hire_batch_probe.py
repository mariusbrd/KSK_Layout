"""Isolated multi-seed new-hire probe; never called by the dashboard.

The existing hire decision helper remains the reference for RNG and attributes.
This probe prepares the unchanged input state and distributions once per batch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator
import warnings

import numpy as np
import pandas as pd

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
    params: dict[str, Any]
    periods: tuple[PeriodInfo, ...]
    org_units: list[str]
    vacancies: tuple[dict[str, Any], ...]
    counts: tuple[int, ...]
    start_date: pd.Timestamp
    gender_distribution: tuple[list[str], list[float]]
    education_distribution: tuple[list[str], list[float]]


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
    return PreparedHireBatch(state, params, tuple(periods), org_units,
                             tuple(sorted(vacancies or [], key=lambda row: row["date"])),
                             tuple(counts), start_date, genders, education)


def iter_hire_seed_records(prepared: PreparedHireBatch, seeds: Iterable[int]
                           ) -> Iterator[tuple[int, list[dict[str, Any]], list[dict[str, Any]]]]:
    """Yield exact hire events and generated person rows for each seed."""
    seen: set[int] = set()
    for seed in seeds:
        seed = int(seed)
        if seed in seen:
            raise ValueError(f"duplicate seed: {seed}")
        seen.add(seed)
        rng = np.random.default_rng(np.random.SeedSequence(seed).spawn(1)[0])
        events: list[dict[str, Any]] = []
        vacancy_state = [row.copy() for row in prepared.vacancies]
        state = prepared.state.copy() if len(prepared.periods) > 1 else prepared.state
        for period_index, (period, count) in enumerate(zip(prepared.periods, prepared.counts)):
            period_events: list[dict[str, Any]] = []
            if count > 0:
                genders = (prepared.gender_distribution if period_index == 0 else
                           _build_gender_distribution(state, _normalized_gender_series(state)))
                education = (prepared.education_distribution if period_index == 0 else
                             _build_education_distribution(
                                 state, excluded_values=[AZUBI_TRAINING_EDUCATION],
                                 fallback_choices=[DEFAULT_NEW_HIRE_EDUCATION]))
                _simulate_hires(
                    state, prepared.params, period, rng, period_events,
                    prepared.org_units, vacancy_state, count, prepared.start_date,
                    genders, education,
                )
            if len(prepared.periods) > 1 and period_events:
                df_new = pd.DataFrame([event["_new_row"] for event in period_events])
                df_new.set_index("PersNr", drop=False, inplace=True)
                for column in df_new.columns:
                    if column not in state.columns:
                        state[column] = pd.NA
                df_new = df_new.reindex(columns=state.columns)
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*empty or all-NA.*",
                                            category=FutureWarning)
                    state = pd.concat([state, df_new])
            events.extend(period_events)
        new_rows = [{key: value for key, value in event["_new_row"].items()
                     if key not in (INTERNAL_EDUCATION_COL, INTERNAL_AZUBI_FLAG_COL)}
                    for event in events]
        for event in events:
            event.pop("_new_row", None)
            event["PersNr"] = event["persnr"]
            event["Organisationseinheit"] = event["org_unit"]
            event["month"] = event["date"].to_period("M")
        yield seed, events, new_rows
