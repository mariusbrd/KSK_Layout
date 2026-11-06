"""Time regular-only compact decisions on a frozen productive departure state."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _reset_state
from dataloader import loader
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _build_fixed_plan_vacancy_context,
    _prepare_employee_forecast_base,
)
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as departure_defaults
from zugaenge.hire_batch_probe import prepare_hire_batch, iter_hire_seed_records
from zugaenge.params import default_params as hire_defaults


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=100)
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")
    _freeze_context()
    _reset_state()
    positions, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp("2026-12-31")
    people = _prepare_employee_forecast_base(positions, atz, start)
    dep = departure_defaults()
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    departure = run_forecast_abgaenge(
        people, atz, start, end, "M", {**dep, "random_seed": 7},
        include_diagnostics=False)["events_person_level"]
    after = _apply_attrition_events_to_employee_state(people, departure)
    _, _, vacancies = _build_fixed_plan_vacancy_context(positions, departure, start)
    hire = hire_defaults()
    hire["azubi"]["active"] = False
    hire["trainee"]["active"] = False
    hire["new_hires"].update(active=True, count_per_year=20,
                              strategy="Fill Vacancies")
    prepared = prepare_hire_batch(after, start, end, hire)
    started = perf_counter()
    count = 0
    total_hires = 0
    for _, events, _ in iter_hire_seed_records(
            prepared, range(args.seeds),
            fixed_plan_vacancy_provider=lambda _: vacancies):
        count += 1
        total_hires += len(events)
    elapsed = perf_counter() - started
    print(f"fixed_regular_seeds={count}; decision_s={elapsed:.3f}; "
          f"per_seed_s={elapsed / count:.4f}; hires={total_hires}; "
          "departure_state=frozen")


if __name__ == "__main__":
    main()
