"""Read-only productive parity and timing probe for the quit-only batch.

Run: py tests/benchmark_quit_batch_probe.py
No person records are printed.
"""
from __future__ import annotations

from pathlib import Path
import sys
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state
from dataloader import loader
from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params
from abgaenge.quit_batch_probe import (iter_quit_seed_records, prepare_quit_batch,
                                       prepare_quit_yearly,
                                       quit_annual_event_values, departure_annual_event_values,
                                       run_quit_batch)
from test_monte_carlo_yearly_reference import _frame_signature
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.org_simulation_events import aggregate_simulation_events, normalize_simulation_events


def _year_slices(events: pd.DataFrame) -> dict[str, dict]:
    if events.empty:
        return {key: {} for key in ("Organisationseinheit", "Jobfamily")}
    result = {}
    for key in ("Organisationseinheit", "Jobfamily"):
        frame = events.assign(year=events["event_date"].dt.year)
        annual = frame.groupby(["year", key], sort=True, dropna=False)[
            ["headcount_change", "mak_change"]].sum()
        result[key] = _frame_signature(annual)
    return result


def main() -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "quit_batch_cluster_page")
    _, bundle, _ = page._get_simulation_cluster_context(source_summary)
    base = _prepare_employee_forecast_base(snapshot, atz, FROZEN_STICHTAG)
    params = default_params()
    with_retirement = "--retirement" in sys.argv
    with_ruhend = "--ruhend" in sys.argv
    params["components"] = {"atz": False, "retirement": with_retirement,
                            "quit": True, "ruhend": with_ruhend}
    if with_ruhend:
        params["ruhend"].update(ruhend_new_cases_per_year=8,
                                ruhend_return_rate=0.95,
                                ruhend_avg_duration_months=3)
    def annual_values(records):
        return (departure_annual_event_values(records, yearly) if with_retirement or with_ruhend
                else quit_annual_event_values(records, yearly))
    target = pd.Timestamp("2026-12-31")
    seeds = (42, 7, 123, 999)
    start = perf_counter()
    prepared = prepare_quit_batch(base, atz, FROZEN_STICHTAG, target, params)
    yearly = prepare_quit_yearly(snapshot, bundle.oe_map)
    prepare_seconds = perf_counter() - start
    start = perf_counter()
    batch = run_quit_batch(prepared, seeds)
    batch_seconds = perf_counter() - start
    start = perf_counter()
    for seed in seeds:
        reference = run_forecast_abgaenge(
            base, atz, FROZEN_STICHTAG, target, "M",
            {**params, "random_seed": seed}, include_diagnostics=False,
        )["events_person_level"]
        assert _frame_signature(batch[seed].events) == _frame_signature(reference), seed
        assert _year_slices(batch[seed].events) == _year_slices(reference), seed
        stream = next(records for current_seed, records, _, _ in
                      iter_quit_seed_records(prepared, [seed]) if current_seed == seed)
        assert _frame_signature(pd.DataFrame(stream)) == _frame_signature(reference), seed
        annual = annual_values(stream)
        context = {"run_signature": "quit-probe", "baseline_snapshot": snapshot,
                   "final_snapshot": snapshot, "cluster_mapping": {"oe_map": bundle.oe_map},
                   "departure_events": reference, "accession_events": pd.DataFrame(),
                   "metadata": {"base_date": FROZEN_STICHTAG, "target_date": target},
                   "zugaenge_params": {key: {"active": False} for key in
                                       ("azubi", "trainee", "new_hires")}}
        normalized = normalize_simulation_events(context, expected_run_signature="quit-probe")
        summary = {"run_signature": "quit-probe", "events": normalized,
                   "metadata": context["metadata"]}
        expected = {}
        for dimension in ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"):
            table = (aggregate_simulation_events(normalized, dimension=dimension)
                     if dimension in ("Organisationseinheit", "OE-Cluster")
                     else build_jobfamily_yearly_view(
                         context, summary, dimension=dimension, include_stock=False)["annual"])
            for _, row in table.iterrows():
                key = (int(row["Jahr"]), dimension, str(row[dimension]))
                if with_retirement or with_ruhend:
                    key = (*key, str(row["Ereignisart"]))
                expected[key] = (
                    int(row["Köpfe-Veränderung"]), float(row["MAK-Veränderung"]))
        assert annual == expected, seed
        departed = set(reference.loc[reference["headcount_change"].eq(-1), "persnr"])
        expected_active = [person not in departed for person in prepared.ids]
        assert batch[seed].active.tolist() == expected_active, seed
        expected_mak = prepared.mak.copy()
        person_positions = {person: index for index, person in enumerate(prepared.ids)}
        for event in reference.itertuples():
            position = person_positions[event.persnr]
            if event.reason_code in ("RETIREMENT", "QUIT", "RUHEND_START"):
                expected_mak[position] = 0.0
            elif event.reason_code == "RUHEND_RETURN":
                expected_mak[position] = event.mak_change
        assert batch[seed].mak.tolist() == expected_mak.tolist(), seed
        expected_status = prepared.initial_ruhend.copy()
        for event in reference.itertuples():
            position = person_positions[event.persnr]
            if event.reason_code == "RUHEND_START":
                expected_status[position] = True
            elif event.reason_code == "RUHEND_RETURN":
                expected_status[position] = False
        assert batch[seed].status_ruhend.tolist() == expected_status.tolist(), seed
    reference_seconds = perf_counter() - start
    print(f"retirement={with_retirement}; ruhend={with_ruhend}; seeds={len(seeds)}; "
          f"preparation={prepare_seconds:.4f}s; "
          f"batch={batch_seconds:.4f}s; reference={reference_seconds:.4f}s; "
          "exact event, state, four-level annual-event parity=True")
    if "--stress-1000" in sys.argv:
        start = perf_counter()
        stress = run_quit_batch(prepared, range(1000))
        print(f"stress_seeds={len(stress)}; batch_seconds={perf_counter() - start:.4f}s; "
              "scope=quit events and final active/MAK only")
        start = perf_counter()
        stream_count = sum(len(annual_values(records)) for _, records, _, _ in
                           iter_quit_seed_records(prepared, range(1000)))
        print(f"stream_seeds=1000; annual_keys={stream_count}; "
              f"stream_seconds={perf_counter() - start:.4f}s; no per-seed DataFrames retained")
    if "--stress-10000" in sys.argv:
        start = perf_counter()
        stream_count = sum(len(annual_values(records)) for _, records, _, _ in
                           iter_quit_seed_records(prepared, range(10000)))
        print(f"stream_seeds=10000; annual_keys={stream_count}; "
              f"stream_seconds={perf_counter() - start:.4f}s; no per-seed DataFrames retained")
    if "--profile-1000" in sys.argv:
        stream = iter_quit_seed_records(prepared, range(1000))
        generation_seconds = aggregation_seconds = 0.0
        record_count = annual_key_count = 0
        for _ in range(1000):
            start = perf_counter()
            _, records, _, _ = next(stream)
            generation_seconds += perf_counter() - start
            record_count += len(records)
            start = perf_counter()
            annual_key_count += len(annual_values(records))
            aggregation_seconds += perf_counter() - start
        print(f"profile_seeds=1000; events={record_count}; annual_keys={annual_key_count}; "
              f"generation_seconds={generation_seconds:.4f}; "
              f"aggregation_seconds={aggregation_seconds:.4f}")
    if "--cprofile-1000" in sys.argv:
        import cProfile
        import pstats
        profiler = cProfile.Profile()
        profiler.enable()
        count = sum(len(annual_values(records)) for _, records, _, _ in
                    iter_quit_seed_records(prepared, range(1000)))
        profiler.disable()
        rows = []
        for (filename, line, name), (_, calls, own, cumulative, _) in pstats.Stats(profiler).stats.items():
            if ROOT not in Path(filename).parents:
                continue
            rows.append((cumulative, own, calls, Path(filename).name, line, name))
        for cumulative, own, calls, filename, line, name in sorted(rows, reverse=True)[:12]:
            print(f"cprofile cumulative={cumulative:.4f}s own={own:.4f}s "
                  f"calls={calls} {filename}:{line} {name}")
        print(f"cprofile_annual_keys={count}; times are instrumented")


if __name__ == "__main__":
    main()
