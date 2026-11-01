"""Isolated departure/new-hire seed coupling; never called by the dashboard.

Vacancies are built from each seed's exact departure events using a prepared
person lookup. No per-seed departure DataFrame or person copy is needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

import numpy as np
import pandas as pd

from abgaenge.quit_batch_probe import (PreparedQuitBatch, iter_quit_seed_records,
                                       prepare_quit_batch)
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _normalize_persnr_series,
)
from .hire_batch_probe import (PreparedHireBatch, iter_hire_seed_records,
                               prepare_hire_batch)


@dataclass(frozen=True)
class PreparedCoupledBatch:
    employee_base: pd.DataFrame
    departures: PreparedQuitBatch
    hires: PreparedHireBatch
    donor_lookup: dict[str, tuple[Any, Any]]


def _donor_lookup(employee_base: pd.DataFrame) -> dict[str, tuple[Any, Any]]:
    people = employee_base.copy()
    people["PersNr"] = _normalize_persnr_series(people["PersNr"])
    lookup: dict[str, tuple[Any, Any]] = {}
    for _, row in people.iterrows():
        person = row["PersNr"]
        if person not in lookup:
            lookup[person] = (row.get("Jobfamily", "Angestellte"),
                              row.get("OE-Cluster", "Sonstiges"))
    return lookup


def vacancies_from_departure_records(
        records: list[dict[str, Any]], donor_lookup: dict[str, tuple[Any, Any]],
) -> list[dict[str, Any]]:
    """Reproduce the reference vacancy rows without per-seed DataFrames."""
    if not records:
        return []
    event_columns = set().union(*(record.keys() for record in records))
    vacancies = []
    for event in records:
        if event.get("headcount_change", 0) >= 0:
            continue
        person = str(event.get("persnr", "")).strip()
        if person.endswith(".0"):
            person = person[:-2]
        jobfamily, cluster = donor_lookup.get(person, ("Angestellte", "Sonstiges"))
        org_default = np.nan if "Organisationseinheit" in event_columns else "Unbekannt"
        position_default = np.nan if "Planstelle" in event_columns else "Nachbesetzung"
        date = event.get("event_date")
        vacancies.append({
            "date": date if isinstance(date, pd.Timestamp) else
                    pd.to_datetime(date, errors="coerce"),
            "org_unit": event.get("Organisationseinheit", org_default),
            "planstelle": event.get("Planstelle", position_default),
            "persnr": person,
            "Jobfamily": jobfamily,
            "OE-Cluster": cluster,
        })
    return vacancies


def prepare_coupled_batch(employee_base: pd.DataFrame, df_atz: pd.DataFrame,
                          start_date: pd.Timestamp, end_date: pd.Timestamp,
                          departure_params: dict[str, Any],
                          hire_params: dict[str, Any]) -> PreparedCoupledBatch:
    """Prepare the shared person input for departure and new-hire probes."""
    return PreparedCoupledBatch(
        employee_base,
        prepare_quit_batch(employee_base, df_atz, start_date, end_date,
                           departure_params),
        prepare_hire_batch(employee_base, start_date, end_date, hire_params),
        _donor_lookup(employee_base),
    )


def iter_coupled_seed_records(
        prepared: PreparedCoupledBatch, seeds: Iterable[int],
) -> Iterator[tuple[int, list[dict[str, Any]], list[dict[str, Any]],
                    list[dict[str, Any]], list[dict[str, Any]]]]:
    """Yield departure records, seed vacancies, hire records and new people."""
    for seed, departures, _, _ in iter_quit_seed_records(prepared.departures, seeds):
        vacancies = vacancies_from_departure_records(departures, prepared.donor_lookup)
        _, hires, new_people = next(iter_hire_seed_records(
            prepared.hires, [seed],
            vacancy_provider=lambda _seed: vacancies))
        yield seed, departures, vacancies, hires, new_people


def replay_final_employee_state(
        employee_base: pd.DataFrame,
        departure_records: list[dict[str, Any]],
        new_people: list[dict[str, Any]],
) -> pd.DataFrame:
    """Rebuild the reference person state from exact coupled seed records.

    This is a parity bridge, not the optimized many-seed stock calculation.
    """
    state = _apply_attrition_events_to_employee_state(
        employee_base, pd.DataFrame(departure_records))
    if not state.empty and "PersNr" in state.columns:
        state.set_index("PersNr", drop=False, inplace=True)
        state.sort_index(inplace=True)
    if new_people:
        additions = pd.DataFrame(new_people)
        additions.set_index("PersNr", drop=False, inplace=True)
        for column in additions.columns:
            if column not in state.columns:
                state[column] = pd.NA
        additions = additions.reindex(columns=state.columns)
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*empty or all-NA.*",
                                    category=FutureWarning)
            state = pd.concat([state, additions])
    return state
