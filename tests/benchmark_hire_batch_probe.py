"""Read-only productive parity and timing for the single-month hire probe."""
from __future__ import annotations

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
    _apply_attrition_events_to_employee_state, _build_vacancies_from_attrition,
    _prepare_employee_forecast_base,
)
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as departure_defaults
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.hire_batch_probe import prepare_hire_month, iter_hire_seed_records
from zugaenge.params import default_params as accession_defaults


def main() -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    target = (pd.Timestamp(FROZEN_STICHTAG) + pd.offsets.MonthEnd(1)).normalize()
    month_start = target.to_period("M").start_time
    base = _prepare_employee_forecast_base(snapshot, atz, FROZEN_STICHTAG)
    departure = run_forecast_abgaenge(
        base, atz, FROZEN_STICHTAG, target, "M", departure_defaults(),
        include_diagnostics=False,
    )
    after_departure = _apply_attrition_events_to_employee_state(
        base, departure["events_person_level"])
    vacancies = _build_vacancies_from_attrition(base, departure)
    params = accession_defaults()
    params["new_hires"].update(active=True, count_per_year=240,
                                strategy="Fill Vacancies")
    started = perf_counter()
    prepared = prepare_hire_month(after_departure, month_start,
                                  target, params, vacancies)
    prepare_seconds = perf_counter() - started
    for seed, records, new_rows in iter_hire_seed_records(prepared, (42, 7, 123, 999)):
        reference = run_forecast_zugaenge(
            after_departure, month_start, end_date=target,
            params={**params, "random_seed": seed}, vacancies=vacancies,
        )
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference["events"],
                                      check_exact=True)
        assert len(new_rows) == len(records), seed
    started = perf_counter()
    event_count = sum(len(records) for _, records, _ in
                      iter_hire_seed_records(prepared, range(1000)))
    stream_seconds = perf_counter() - started
    print(f"scope=one_month_new_hires_only; seeds=1000; "
          f"preparation={prepare_seconds:.4f}s; stream={stream_seconds:.4f}s; "
          f"events={event_count}; productive_seed_parity=4")


if __name__ == "__main__":
    main()
