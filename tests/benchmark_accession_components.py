"""Read-only timing of accession components on one frozen departure state.

Run: py tests/benchmark_accession_components.py
Only timings and event counts are printed, never person rows.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import statistics
import sys
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _reset_state
from dataloader import loader
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _build_vacancies_from_attrition,
    _prepare_employee_forecast_base,
)
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as departure_defaults
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as accession_defaults

SEEDS = (42, 7, 123, 999, 2026)


def main() -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    target = pd.Timestamp("2026-12-31")
    base = _prepare_employee_forecast_base(snapshot, atz, FROZEN_STICHTAG)
    departure = run_forecast_abgaenge(
        base, atz, FROZEN_STICHTAG, target, "M", departure_defaults(),
        include_diagnostics=False,
    )
    after_departure = _apply_attrition_events_to_employee_state(
        base, departure["events_person_level"])
    vacancies = _build_vacancies_from_attrition(base, departure)
    print(f"baseline_rows={len(after_departure)}; vacancies={len(vacancies)}; "
          f"periods=13; seeds={len(SEEDS)}")

    scenarios = {
        "inactive": {},
        "new_hires": {"new_hires": ("count_per_year", 20)},
        "trainee": {"trainee": ("new_cases_per_year", 20)},
        "azubi": {"azubi": ("new_cases_per_year", 20)},
        "combined": {"new_hires": ("count_per_year", 20),
                     "trainee": ("new_cases_per_year", 20),
                     "azubi": ("new_cases_per_year", 20)},
    }
    for name, settings in scenarios.items():
        times = []
        event_counts = []
        final_counts = []
        for seed in SEEDS:
            params = deepcopy(accession_defaults())
            params["random_seed"] = seed
            for component, (count_key, count) in settings.items():
                params[component].update(active=True, **{count_key: count})
            start = perf_counter()
            result = run_forecast_zugaenge(
                after_departure, FROZEN_STICHTAG, end_date=target,
                params=params, vacancies=vacancies,
            )
            times.append(perf_counter() - start)
            event_counts.append(len(result["events"]))
            final_counts.append(len(result["final_state"]))
        print(f"scenario={name}; median_seconds={statistics.median(times):.4f}; "
              f"seconds={[round(value, 4) for value in times]}; "
              f"event_counts={event_counts}; final_rows={final_counts}")


if __name__ == "__main__":
    main()
