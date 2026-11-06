"""Exact, deliberately unoptimized multi-seed reference for fixed plan accessions.

The old hire batch probe uses person-level vacancies. This stream instead
rebuilds the product's position pool for each seed and calls its accession
forecast. It is a parity baseline for a later optimized implementation.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Iterator

import pandas as pd

from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state,
    _build_fixed_plan_vacancy_context,
)
from .forecast import run_forecast_zugaenge


def iter_fixed_plan_accession_seeds(
    position_snapshot: pd.DataFrame,
    employee_base: pd.DataFrame,
    departure_events_by_seed: Iterable[tuple[int, pd.DataFrame]],
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    hire_params: dict[str, Any],
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield exact accession results using independent position pools per seed.

    ``departure_events_by_seed`` contains the product's person-level departure
    frames. The caller supplies the same hire parameters as the product path,
    including its jobfamily-to-cluster mapping.
    """
    seen: set[int] = set()
    for raw_seed, departure_events in departure_events_by_seed:
        seed = int(raw_seed)
        if seed in seen:
            raise ValueError(f"duplicate seed: {seed}")
        seen.add(seed)
        employee_after_departures = _apply_attrition_events_to_employee_state(
            employee_base, departure_events)
        pool, original_positions, _ = _build_fixed_plan_vacancy_context(
            position_snapshot, departure_events, pd.Timestamp(base_date))
        params = deepcopy(hire_params)
        params["random_seed"] = seed
        yield seed, run_forecast_zugaenge(
            df_snapshot=employee_after_departures,
            start_date=pd.Timestamp(base_date),
            end_date=pd.Timestamp(target_date),
            freq="M",
            params=params,
            vacancies=[],
            vacancy_pool=pool,
            original_positions=original_positions,
        )
