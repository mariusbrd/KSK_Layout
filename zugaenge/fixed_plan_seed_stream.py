"""Exact, deliberately unoptimized multi-seed reference for fixed plan accessions.

The old hire batch probe uses person-level vacancies. This stream instead
rebuilds the product's position pool for each seed and calls its accession
forecast. It is a parity baseline for a later optimized implementation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable, Iterator
import warnings

import pandas as pd

from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state,
    _build_fixed_plan_vacancy_context,
    _normalize_persnr_series,
)
from abgaenge.quit_batch_probe import (
    iter_quit_seed_records_with_atz, prepare_atz_pivot_for_probe,
    prepare_quit_batch,
)
from .forecast import run_forecast_zugaenge
from .hire_batch_probe import iter_hire_seed_records, prepare_hire_batch
from utils.fixed_plan_vacancies import (
    FixedPlanVacancyPool, _person_id, available_position_vacancies,
)


@dataclass(frozen=True)
class PreparedPositions:
    initial: tuple[dict[str, Any], ...]
    occupied: tuple[tuple[str, dict[str, Any]], ...]
    original: dict[str, Any]


def prepare_positions(position_snapshot: pd.DataFrame,
                      base_date: pd.Timestamp) -> PreparedPositions:
    """Cache immutable position metadata while preserving product ordering."""
    base_date = pd.Timestamp(base_date)
    empty = pd.DataFrame()
    _, original, initial = _build_fixed_plan_vacancy_context(
        position_snapshot, empty, base_date)
    initially_free = {row["position_index"] for row in initial}
    person_ids = {_person_id(value) for value in position_snapshot.get("PersNr", [])}
    synthetic_exits = pd.DataFrame([
        {"persnr": person, "event_date": base_date, "headcount_change": -1}
        for person in person_ids if person is not None
    ])
    all_eligible = available_position_vacancies(
        position_snapshot, synthetic_exits, base_date,
        include_report_excluded=True)
    occupied = tuple(
        (_person_id(position_snapshot.iloc[row["position_index"]].get("PersNr")), row)
        for row in all_eligible if row["position_index"] not in initially_free
    )
    return PreparedPositions(tuple(initial), occupied, original)


def _vacancies_for_seed(prepared: PreparedPositions,
                        departures: pd.DataFrame) -> list[dict[str, Any]]:
    exits: dict[str, pd.Timestamp] = {}
    if departures is not None and not departures.empty:
        for event in departures.to_dict("records"):
            if int(event.get("headcount_change", 0) or 0) >= 0:
                continue
            person = _person_id(event.get("persnr"))
            date = pd.to_datetime(event.get("event_date"), errors="coerce")
            if person is not None and pd.notna(date):
                exits[person] = min(exits.get(person, date), date)
    vacancies = [row.copy() for row in prepared.initial]
    for person, row in prepared.occupied:
        if person in exits:
            released = row.copy()
            released["date"] = exits[person]
            vacancies.append(released)
    vacancies.sort(key=lambda row: (row["date"], row["position_index"]))
    return vacancies


def _pool_for_seed(prepared: PreparedPositions,
                   departures: pd.DataFrame) -> FixedPlanVacancyPool:
    return FixedPlanVacancyPool(_vacancies_for_seed(prepared, departures))


def _prepare_employee_after_departures(employee_base: pd.DataFrame) -> pd.DataFrame:
    """Build the product's no-event state once for scalar seed event replay."""
    template = _apply_attrition_events_to_employee_state(
        employee_base, pd.DataFrame())
    return template.set_index("PersNr", drop=False)


def _replay_departures_prepared(template: pd.DataFrame,
                                events_df: pd.DataFrame) -> pd.DataFrame:
    """Replay seed events with scalar writes; preserve product event order."""
    if not template.index.is_unique:
        return _apply_attrition_events_to_employee_state(
            template.reset_index(drop=True), events_df)
    state = template.copy()
    if events_df.empty:
        return state.reset_index(drop=True)
    events = events_df.copy()
    events["persnr"] = _normalize_persnr_series(events["persnr"])
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce")
    events = events.sort_values("event_date")
    for event in events.to_dict("records"):
        pid = event["persnr"]
        if pid not in state.index:
            continue
        headcount_change = int(event.get("headcount_change", 0) or 0)
        mak_change = float(event.get("mak_change", 0.0) or 0.0)
        reason_code = str(event.get("reason_code", "") or "")
        if headcount_change < 0:
            state.at[pid, "active"] = False
            state.at[pid, "mak"] = 0.0
        elif headcount_change > 0:
            state.at[pid, "active"] = True
            state.at[pid, "mak"] = max(0.0, float(state.at[pid, "mak"]) + mak_change)
        else:
            state.at[pid, "mak"] = max(0.0, float(state.at[pid, "mak"]) + mak_change)
        if reason_code == "RUHEND_START":
            state.at[pid, "status_ruhend"] = True
            if "Status kundenindividuell" in state.columns:
                state.at[pid, "Status kundenindividuell"] = "Ruhendes Beschäftigungsverhältnis"
        elif reason_code == "RUHEND_RETURN":
            state.at[pid, "status_ruhend"] = False
            if "Status kundenindividuell" in state.columns:
                state.at[pid, "Status kundenindividuell"] = "Aktives Beschäftigungsverhältnis"
        elif reason_code == "ATZ_AR_TO_FR":
            state.at[pid, "atz_fr_active"] = True
    return state.reset_index(drop=True)


def iter_fixed_plan_accession_seeds(
    position_snapshot: pd.DataFrame,
    employee_base: pd.DataFrame,
    departure_events_by_seed: Iterable[tuple[int, pd.DataFrame]],
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    hire_params: dict[str, Any],
    profile: dict[str, float] | None = None,
    prepared_positions: PreparedPositions | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield exact accession results using independent position pools per seed.

    ``departure_events_by_seed`` contains the product's person-level departure
    frames. The caller supplies the same hire parameters as the product path,
    including its jobfamily-to-cluster mapping.
    """
    seen: set[int] = set()
    if prepared_positions is None:
        prepared_positions = prepare_positions(position_snapshot, base_date)
    for raw_seed, departure_events in departure_events_by_seed:
        started = perf_counter()
        seed = int(raw_seed)
        if seed in seen:
            raise ValueError(f"duplicate seed: {seed}")
        seen.add(seed)
        employee_after_departures = _apply_attrition_events_to_employee_state(
            employee_base, departure_events)
        after_state = perf_counter()
        pool = _pool_for_seed(prepared_positions, departure_events)
        after_pool = perf_counter()
        params = deepcopy(hire_params)
        params["random_seed"] = seed
        result = run_forecast_zugaenge(
            df_snapshot=employee_after_departures,
            start_date=pd.Timestamp(base_date),
            end_date=pd.Timestamp(target_date),
            freq="M",
            params=params,
            vacancies=[],
            vacancy_pool=pool,
            original_positions=prepared_positions.original,
        )
        if profile is not None:
            for key, seconds in (
                ("employee_after_departures", after_state - started),
                ("position_pool", after_pool - after_state),
                ("accession_forecast", perf_counter() - after_pool),
            ):
                profile[key] = profile.get(key, 0.0) + seconds
        yield seed, result


def iter_fixed_plan_coupled_seeds(
    position_snapshot: pd.DataFrame,
    employee_base: pd.DataFrame,
    atz_data: pd.DataFrame,
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    departure_params: dict[str, Any],
    hire_params: dict[str, Any],
    seeds: Iterable[int],
    profile: dict[str, float] | None = None,
) -> Iterator[tuple[int, pd.DataFrame, pd.DataFrame, dict[str, Any]]]:
    """Couple prepared departure seeds to exact fixed-plan accession runs.

    Outputs are seed, person-level departure events, ATZ pivot and accession
    result. This is the correctness baseline, not the fast stock renderer.
    """
    started = perf_counter()
    prepared = prepare_quit_batch(
        employee_base, atz_data, pd.Timestamp(base_date),
        pd.Timestamp(target_date), departure_params)
    initial_pivot = prepare_atz_pivot_for_probe(prepared, atz_data, departure_params)
    if profile is not None:
        profile["departure_prepare"] = profile.get("departure_prepare", 0.0) + perf_counter() - started
    started = perf_counter()
    prepared_positions = prepare_positions(position_snapshot, base_date)
    if profile is not None:
        profile["position_prepare"] = profile.get("position_prepare", 0.0) + perf_counter() - started
    departure_clock = perf_counter()
    for seed, records, atz_pivot in iter_quit_seed_records_with_atz(
            prepared, seeds, initial_pivot):
        if profile is not None:
            profile["departure_seed"] = profile.get("departure_seed", 0.0) + perf_counter() - departure_clock
        events = pd.DataFrame(records)
        _, accessions = next(iter_fixed_plan_accession_seeds(
            position_snapshot, employee_base, [(seed, events)], base_date,
            target_date, hire_params, profile, prepared_positions))
        yield seed, events, atz_pivot, accessions
        departure_clock = perf_counter()


def iter_fixed_plan_regular_coupled_seeds(
    position_snapshot: pd.DataFrame,
    employee_base: pd.DataFrame,
    atz_data: pd.DataFrame,
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    departure_params: dict[str, Any],
    hire_params: dict[str, Any],
    seeds: Iterable[int],
    profile: dict[str, float] | None = None,
) -> Iterator[tuple[int, pd.DataFrame, pd.DataFrame,
                    list[dict[str, Any]], list[dict[str, Any]]]]:
    """Stream exact regular hire decisions against seed-specific plan vacancies.

    Apprentices and trainees must be inactive. This stage deliberately yields
    raw records; the final snapshot and OE/JF bridge are validated separately.
    """
    if any(hire_params.get(name, {}).get("active", True)
           for name in ("azubi", "trainee")):
        raise ValueError("Regular compact stream requires inactive azubi and trainee")
    started = perf_counter()
    prepared_departures = prepare_quit_batch(
        employee_base, atz_data, pd.Timestamp(base_date),
        pd.Timestamp(target_date), departure_params)
    initial_pivot = prepare_atz_pivot_for_probe(
        prepared_departures, atz_data, departure_params)
    if profile is not None:
        profile["departure_prepare"] = perf_counter() - started
    started = perf_counter()
    positions = prepare_positions(position_snapshot, base_date)
    if profile is not None:
        profile["position_prepare"] = perf_counter() - started
    started = perf_counter()
    prepared_hires = prepare_hire_batch(
        employee_base, pd.Timestamp(base_date),
        pd.Timestamp(target_date), hire_params)
    if profile is not None:
        profile["hire_prepare"] = perf_counter() - started
    departure_clock = perf_counter()
    for seed, records, atz_pivot in iter_quit_seed_records_with_atz(
            prepared_departures, seeds, initial_pivot):
        if profile is not None:
            profile["departure_seed"] = profile.get("departure_seed", 0.0) + perf_counter() - departure_clock
        events = pd.DataFrame(records)
        started = perf_counter()
        vacancies = _vacancies_for_seed(positions, events)
        if profile is not None:
            profile["position_pool"] = profile.get("position_pool", 0.0) + perf_counter() - started
        started = perf_counter()
        _, hires, new_people = next(iter_hire_seed_records(
            prepared_hires, [seed],
            fixed_plan_vacancy_provider=lambda _: vacancies))
        if profile is not None:
            profile["hire_decisions"] = profile.get("hire_decisions", 0.0) + perf_counter() - started
        yield seed, events, atz_pivot, hires, new_people
        departure_clock = perf_counter()


def replay_fixed_plan_regular_employee_state(
    employee_base: pd.DataFrame,
    departure_events: pd.DataFrame,
    new_people: list[dict[str, Any]],
    prepared_employee_template: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Rebuild the regular-only product person state, preserving month order."""
    state = (_replay_departures_prepared(prepared_employee_template, departure_events)
             if prepared_employee_template is not None else
             _apply_attrition_events_to_employee_state(employee_base, departure_events))
    state["_relocated_from_position_indices"] = pd.Series(
        [None] * len(state), index=state.index, dtype=object)
    if not state.empty and "PersNr" in state.columns:
        state.set_index("PersNr", drop=False, inplace=True)
        state.sort_index(inplace=True)
    if not new_people:
        return state
    additions = pd.DataFrame(new_people)
    additions["_entry_month"] = pd.to_datetime(
        additions["Eintritt"]).dt.to_period("M")
    for _, month_rows in additions.groupby("_entry_month", sort=True):
        monthly = month_rows.drop(columns=["_entry_month"])
        monthly.set_index("PersNr", drop=False, inplace=True)
        for column in monthly.columns:
            if column not in state.columns:
                state[column] = pd.NA
        monthly = monthly.reindex(columns=state.columns)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*empty or all-NA.*",
                                    category=FutureWarning)
            state = pd.concat([state, monthly])
    return state
