"""Productive parity/timing for isolated departure-to-hire seed coupling."""
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
    _prepare_employee_forecast_base, _apply_salary_automation_to_employee_state,
    _append_new_people_rows, _finalize_future_snapshot, _normalize_persnr_series,
    _resolve_atz_status_map, _update_existing_rows, simulate_compact_snapshot,
)
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as departure_defaults
from zugaenge.coupled_batch_probe import (
    prepare_coupled_batch, iter_coupled_seed_records,
    replay_final_employee_state,
)
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as hire_defaults


def main() -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    people = _prepare_employee_forecast_base(snapshot, atz, FROZEN_STICHTAG)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp("2026-12-31")
    departure_params, hire_params = departure_defaults(), hire_defaults()
    hire_params["new_hires"].update(active=True, count_per_year=20,
                                    strategy="Fill Vacancies")
    if "--snapshot-matrix" in sys.argv:
        check_snapshot_matrix(snapshot, atz, people, start, end)
        return
    started = perf_counter()
    prepared = prepare_coupled_batch(people, atz, start, end,
                                     departure_params, hire_params)
    preparation_seconds = perf_counter() - started
    parity_seeds = (range(20) if "--parity-20" in sys.argv else (42, 7, 123, 999))
    parity_count = 0
    for seed, dep_records, vacancies, hire_records, new_people in (
            iter_coupled_seed_records(prepared, parity_seeds)):
        reference_dep = run_forecast_abgaenge(
            people, atz, start, end, "M",
            {**departure_params, "random_seed": seed}, include_diagnostics=False)
        pd.testing.assert_frame_equal(pd.DataFrame(dep_records),
                                      reference_dep["events_person_level"], check_exact=True)
        expected_vacancies = _build_vacancies_from_attrition(people, reference_dep)
        pd.testing.assert_frame_equal(pd.DataFrame(vacancies),
                                      pd.DataFrame(expected_vacancies), check_exact=True)
        after_dep = _apply_attrition_events_to_employee_state(
            people, reference_dep["events_person_level"])
        reference_hires = run_forecast_zugaenge(
            after_dep, start, end_date=end,
            params={**hire_params, "random_seed": seed},
            vacancies=expected_vacancies)
        pd.testing.assert_frame_equal(pd.DataFrame(hire_records),
                                      reference_hires["events"], check_exact=True)
        pd.testing.assert_frame_equal(
            replay_final_employee_state(people, dep_records, new_people),
            reference_hires["final_state"], check_exact=True)
        assert len(new_people) == len(hire_records)
        parity_count += 1
    seed_count = (1000 if "--stress-1000" in sys.argv else
                  100 if "--stress-100" in sys.argv else 20)
    started = perf_counter()
    total_departures = total_hires = 0
    for _, departures, _, hires, _ in iter_coupled_seed_records(prepared, range(seed_count)):
        total_departures += len(departures)
        total_hires += len(hires)
    print(f"coupled_seeds={seed_count}; preparation={preparation_seconds:.4f}s; "
          f"stream={perf_counter() - started:.4f}s; "
          f"departures={total_departures}; hires={total_hires}; "
          f"productive_seed_parity={parity_count}")
    if "--replay-100" in sys.argv:
        started = perf_counter()
        for _, departures, _, _, new_people in iter_coupled_seed_records(
                prepared, range(100)):
            replay_final_employee_state(people, departures, new_people)
        print(f"coupled_replay_100={perf_counter() - started:.4f}s")


def check_snapshot_matrix(snapshot, atz, people, start, end) -> None:
    """Check the snapshot chain with reference ATZ pivot and enriched events."""
    cases = [
        ("vacancy_quit", "Fill Vacancies", 20, 0.05, 0.05),
        ("random_high_quit", "Random", 30, 0.25, 0.05),
        ("org_no_quit_atz", "OrgUnit", 12, 0.0, 0.0),
    ]
    for name, strategy, annual_hires, quit_rate, atz_rate in cases:
        departure_params, hire_params = departure_defaults(), hire_defaults()
        departure_params["quit"].update(quit_rate_base=quit_rate,
                                        use_quit_matrix=False)
        departure_params["atz"].update(new_atz_rate=atz_rate,
                                       use_atz_matrix=False)
        hire_params["new_hires"].update(active=annual_hires > 0,
                                        count_per_year=annual_hires,
                                        strategy=strategy,
                                        target_org_unit=str(people["Organisationseinheit"].dropna().iloc[0])
                                        if strategy == "OrgUnit" else None)
        prepared = prepare_coupled_batch(people, atz, start, end,
                                         departure_params, hire_params)
        for seed, departures, _, _, new_people in iter_coupled_seed_records(
                prepared, (7, 42)):
            reference = simulate_compact_snapshot(
                snapshot, atz, end, base_date=start,
                abgaenge_params={**departure_params, "random_seed": seed},
                zugaenge_params={**hire_params, "random_seed": seed},
                include_audits=False)
            pd.testing.assert_frame_equal(
                pd.DataFrame(departures),
                reference.abgaenge_result["events_person_level"], check_exact=True)
            reference_people = reference.zugaenge_result["final_state"]
            reconstructed_people = replay_final_employee_state(
                people, departures, new_people)
            pd.testing.assert_frame_equal(reconstructed_people,
                                          reference_people, check_exact=True)
            final_people = reconstructed_people
            final_people["PersNr"] = _normalize_persnr_series(final_people["PersNr"])
            final_people = _apply_salary_automation_to_employee_state(
                final_people, target_date=end,
                events_df=reference.zugaenge_result["events"])
            pd.testing.assert_frame_equal(final_people,
                                          reference.future_employee_df, check_exact=True)
            atz_status = _resolve_atz_status_map(
                reference.abgaenge_result["tables"]["atz_pivot"], end)
            future = _update_existing_rows(snapshot, final_people, end, atz_status)
            future = _append_new_people_rows(
                future, snapshot, final_people, reference.zugaenge_result)
            future = _finalize_future_snapshot(future, end)
            pd.testing.assert_frame_equal(future, reference.future_snapshot_df,
                                          check_exact=True)
            print(f"snapshot_parity={name}; seed={seed}; rows={len(future)}")


if __name__ == "__main__":
    main()
