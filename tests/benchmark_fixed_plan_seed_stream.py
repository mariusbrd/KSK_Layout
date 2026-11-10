"""Time the exact fixed-plan seed stream; outputs contain no employee data."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from time import perf_counter, process_time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module,
    _reset_state,
)
from dataloader import loader
from dataloader.compact_simulation_engine import (
    CompactSimulationResult, _prepare_employee_forecast_base,
    _resolve_simulation_cluster_context,
)
from abgaenge.params import default_params as departure_defaults
from zugaenge.enrichment import build_jf_to_cluster_map
from zugaenge.fixed_plan_seed_stream import (
    _prepare_employee_after_departures, iter_fixed_plan_coupled_seeds,
    iter_fixed_plan_regular_coupled_seeds, rebuild_fixed_plan_regular_snapshot,
)
from zugaenge.params import default_params as hire_defaults


def _peak_working_set_mib() -> float | None:
    """OS-reported process peak working set; unavailable platforms return None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    size_t = ctypes.c_size_t
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", size_t), ("WorkingSetSize", size_t),
            ("QuotaPeakPagedPoolUsage", size_t), ("QuotaPagedPoolUsage", size_t),
            ("QuotaPeakNonPagedPoolUsage", size_t), ("QuotaNonPagedPoolUsage", size_t),
            ("PagefileUsage", size_t), ("PeakPagefileUsage", size_t),
        ]
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
    ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        handle, ctypes.byref(counters), counters.cb)
    return counters.PeakWorkingSetSize / 2**20 if ok else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--target", default="2026-12-31")
    parser.add_argument("--compare-regular", action="store_true")
    parser.add_argument("--complete-regular", action="store_true")
    parser.add_argument("--annual-events", action="store_true")
    parser.add_argument("--annual-stocks", action="store_true")
    parser.add_argument("--annual-position-stocks", action="store_true")
    parser.add_argument("--compare-groupings", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--grouping-method", choices=("product", "indexed"),
                        default="indexed")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")
    if args.repeat < 1 or args.warmup < 0:
        parser.error("--repeat must be positive and --warmup nonnegative")
    cluster_file = ROOT / "config" / "cluster_mapping.xlsx"
    cluster_hash_before = hashlib.sha256(cluster_file.read_bytes()).hexdigest()
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    start, target = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(args.target)
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=True, count_per_year=20,
                              strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0,
                         jf_to_cluster_map=build_jf_to_cluster_map(snapshot))
    hire["trainee"].update(active=True, new_cases_per_year=4)
    if args.annual_position_stocks or args.compare_groupings:
        from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
        from utils.compact_regular_stock import project_regular_position_stocks
        from utils.status_quo_baseline import build_status_quo_snapshot

        hire["azubi"]["active"] = False
        hire["trainee"]["active"] = False
        compact = _load_page_module("*_Kompakt.py", "regular_position_benchmark_compact")
        baseline = compact.prepare_compact_data(
            build_status_quo_snapshot(snapshot, start))
        source, bundle, _ = _resolve_simulation_cluster_context()
        mapped = apply_clusters_to_snapshot_from_source(
            baseline, source, mapping_bundle=bundle)
        if args.compare_groupings:
            times = {"product": 0.0, "indexed": 0.0}
            count = 0
            for seed, departures, _, _, new_people in iter_fixed_plan_regular_coupled_seeds(
                    snapshot, people, atz, start, target, dep, hire,
                    range(args.seeds)):
                results = {}
                order = ("product", "indexed") if seed % 2 == 0 else ("indexed", "product")
                for method in order:
                    run_started = perf_counter()
                    results[method] = project_regular_position_stocks(
                        baseline, mapped, departures, new_people,
                        base_date=start, target_date=target,
                        grouping_method=method)
                    times[method] += perf_counter() - run_started
                for dimension in results["product"]:
                    pd.testing.assert_frame_equal(
                        results["product"][dimension], results["indexed"][dimension],
                        check_exact=True)
                count += 1
            cluster_hash_after = hashlib.sha256(cluster_file.read_bytes()).hexdigest()
            if cluster_hash_before != cluster_hash_after:
                raise RuntimeError("Cluster mapping changed during benchmark")
            print(f"paired_grouping_seeds={count}; target={target.date()}; "
                  f"product_s={times['product']:.3f}; indexed_s={times['indexed']:.3f}; "
                  f"ratio={times['indexed'] / times['product']:.3f}; "
                  f"cluster_mapping_sha256={cluster_hash_after[:16]}; "
                  "scope=four_exact_year_end_stock_views_per_seed")
            return
        measured: list[float] = []
        for repetition in range(args.warmup + args.repeat):
            started = perf_counter()
            cpu_started = process_time()
            stock_seconds = 0.0
            stock_profile: dict[str, float] = {}
            count = annual_rows = 0
            for _, departures, _, _, new_people in iter_fixed_plan_regular_coupled_seeds(
                    snapshot, people, atz, start, target, dep, hire,
                    range(args.seeds)):
                stock_started = perf_counter()
                stocks = project_regular_position_stocks(
                    baseline, mapped, departures, new_people,
                    base_date=start, target_date=target, profile=stock_profile,
                    grouping_method=args.grouping_method)
                annual_rows += sum(len(stock) for stock in stocks.values())
                stock_seconds += perf_counter() - stock_started
                count += 1
            elapsed = perf_counter() - started
            cpu_elapsed = process_time() - cpu_started
            if repetition < args.warmup:
                print(f"regular_position_stock_warmup_s={elapsed:.3f}; cpu_s={cpu_elapsed:.3f}")
                continue
            measured.append(elapsed)
            print(f"regular_position_stock_seeds={count}; repetition={len(measured)}; "
                  f"target={target.date()}; total_s={elapsed:.3f}; "
                  f"cpu_s={cpu_elapsed:.3f}; "
                  f"grouping_method={args.grouping_method}; "
                  f"stock_s={stock_seconds:.3f}; per_seed_s={elapsed / count:.3f}; "
                  f"rows={annual_rows}; "
                  "scope=departures_hires_four_position_year_end_stock_views")
            for phase, seconds in stock_profile.items():
                print(f"{phase}={seconds:.3f}s ({seconds / stock_seconds:.1%} of stock)")
        if measured:
            import statistics
            print(f"regular_position_stock_median_s={statistics.median(measured):.3f}; "
                  f"min_s={min(measured):.3f}; max_s={max(measured):.3f}")
        peak = _peak_working_set_mib()
        if peak is not None:
            print(f"process_peak_working_set_mib={peak:.1f}")
        cluster_hash_after = hashlib.sha256(cluster_file.read_bytes()).hexdigest()
        print(f"cluster_mapping_sha256={cluster_hash_after[:16]}")
        if cluster_hash_before != cluster_hash_after:
            raise RuntimeError("Cluster mapping changed during benchmark")
        return
    if args.annual_stocks:
        from utils.compact_regular_annual import build_regular_year_end_stocks

        hire["azubi"]["active"] = False
        hire["trainee"]["active"] = False
        source, bundle, signature = _resolve_simulation_cluster_context()
        template = _prepare_employee_after_departures(people)
        started = perf_counter()
        stock_seconds = 0.0
        count = annual_rows = 0
        for _, departures, atz_pivot, hires, new_people in (
                iter_fixed_plan_regular_coupled_seeds(
                    snapshot, people, atz, start, target, dep, hire,
                    range(args.seeds))):
            stock_started = perf_counter()
            stocks = build_regular_year_end_stocks(
                snapshot, people, departures, atz_pivot, hires, new_people,
                hire, base_date=start, target_date=target,
                active_cluster_source=source,
                cluster_mapping_bundle=bundle,
                cluster_source_signature=signature,
                prepared_employee_template=template)
            stock_seconds += perf_counter() - stock_started
            count += 1
            annual_rows += sum(len(frame) for frame in stocks.values())
        elapsed = perf_counter() - started
        print(f"regular_annual_stock_seeds={count}; target={target.date()}; "
              f"total_s={elapsed:.3f}; stock_s={stock_seconds:.3f}; "
              f"per_seed_s={elapsed / count:.3f}; rows={annual_rows}; "
              "scope=departures_hires_four_year_end_stock_views")
        return
    if args.annual_events:
        from components.jobfamily_simulation_yearly import prepare_jobfamily_baseline
        from utils.compact_regular_annual import build_compact_regular_annual_events
        from utils.status_quo_baseline import build_status_quo_snapshot

        hire["azubi"]["active"] = False
        hire["trainee"]["active"] = False
        compact = _load_page_module("*_Kompakt.py", "regular_event_benchmark_compact")
        baseline = compact.prepare_compact_data(
            build_status_quo_snapshot(snapshot, start))
        jf_baseline = prepare_jobfamily_baseline(baseline)
        _, bundle, signature = _resolve_simulation_cluster_context()
        started = perf_counter()
        count = annual_rows = 0
        for seed, departures, _, hires, _ in iter_fixed_plan_regular_coupled_seeds(
                snapshot, people, atz, start, target, dep, hire,
                range(args.seeds)):
            annual = build_compact_regular_annual_events(
                baseline, snapshot, departures, hires, hire, dep,
                base_date=start, target_date=target,
                cluster_mapping_bundle=bundle,
                cluster_source_signature=signature,
                run_signature=f"regular-event-benchmark::{seed}",
                prepared_jobfamily_baseline=jf_baseline)
            count += 1
            annual_rows += sum(len(frame) for frame in annual.values())
        elapsed = perf_counter() - started
        print(f"regular_annual_event_seeds={count}; total_s={elapsed:.3f}; "
              f"per_seed_s={elapsed / count:.3f}; annual_rows={annual_rows}; "
              "scope=departures_hires_four_annual_event_views")
        return
    if args.complete_regular:
        from copy import deepcopy
        from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
        from utils.org_simulation_events import build_simulation_event_summary
        from utils.status_quo_baseline import build_status_quo_snapshot

        hire["azubi"]["active"] = False
        hire["trainee"]["active"] = False
        compact = _load_page_module("*_Kompakt.py", "regular_complete_benchmark_compact")
        page = _load_page_module("*_Kompakt_plus_Simulation.py", "regular_complete_benchmark_page")
        baseline = compact.prepare_compact_data(
            build_status_quo_snapshot(snapshot, start))
        source, bundle, signature = _resolve_simulation_cluster_context()
        template = _prepare_employee_after_departures(people)
        phases: dict[str, float] = {}
        total_start = perf_counter()
        count = 0
        for seed, departures, atz_pivot, hires, new_people in (
                iter_fixed_plan_regular_coupled_seeds(
                    snapshot, people, atz, start, target, dep, hire,
                    range(args.seeds), phases)):
            count += 1
            rebuilt = rebuild_fixed_plan_regular_snapshot(
                snapshot, people, departures, atz_pivot, hires, new_people,
                target, hire, active_cluster_source=source,
                cluster_mapping_bundle=bundle,
                cluster_source_signature=signature,
                prepared_employee_template=template, profile=phases)
            started = perf_counter()
            final = compact.prepare_compact_data(rebuilt.future_snapshot)
            phases["compact_prepare"] = phases.get("compact_prepare", 0.0) + perf_counter() - started
            result = CompactSimulationResult(
                rebuilt.future_snapshot, rebuilt.future_employee,
                {"events_person_level": departures,
                 "tables": {"atz_pivot": atz_pivot}},
                {"events": rebuilt.accession_events,
                 "final_state": rebuilt.raw_employee_state},
                {"base_date": start, "target_date": target,
                 "cluster_source_signature": signature}, {})
            run_signature = f"regular-complete-benchmark::{seed}"
            started = perf_counter()
            context = page._build_simulation_event_context(
                result, bundle, run_signature,
                baseline_snapshot=baseline, final_snapshot=final,
                zugaenge_params=hire, abgaenge_params=dep, df_atz=atz,
                run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
                source_snapshot=snapshot)
            phases["event_context"] = phases.get("event_context", 0.0) + perf_counter() - started
            started = perf_counter()
            summary = build_simulation_event_summary(
                context, expected_run_signature=run_signature)
            phases["oe_yearly"] = phases.get("oe_yearly", 0.0) + perf_counter() - started
            started = perf_counter()
            for dimension in ("Jobfamily", "JF-Cluster"):
                build_jobfamily_yearly_view(context, summary, dimension=dimension)
            phases["jf_yearly"] = phases.get("jf_yearly", 0.0) + perf_counter() - started
        elapsed = perf_counter() - total_start
        print(f"complete_regular_seeds={count}; target={target.date()}; "
              f"total_s={elapsed:.3f}; per_seed_s={elapsed / count:.3f}; "
              "scope=departures_snapshot_oe_jf")
        for phase, seconds in phases.items():
            print(f"complete_{phase}={seconds:.3f}s ({seconds / elapsed:.1%})")
        return
    if args.compare_regular:
        hire["azubi"]["active"] = False
        hire["trainee"]["active"] = False
        started = perf_counter()
        reference_hires = sum(
            len(accessions["events"])
            for _, _, _, accessions in iter_fixed_plan_coupled_seeds(
                snapshot, people, atz, start, target, dep, hire,
                range(args.seeds)))
        reference_seconds = perf_counter() - started
        started = perf_counter()
        compact_profile: dict[str, float] = {}
        compact_hires = sum(
            len(hires)
            for _, _, _, hires, _ in iter_fixed_plan_regular_coupled_seeds(
                snapshot, people, atz, start, target, dep, hire,
                range(args.seeds), compact_profile))
        compact_seconds = perf_counter() - started
        assert compact_hires == reference_hires
        print(f"regular_coupled_seeds={args.seeds}; reference_s={reference_seconds:.3f}; "
              f"compact_s={compact_seconds:.3f}; hires={compact_hires}; "
              "scope=departures_and_raw_regular_hires")
        for phase, seconds in compact_profile.items():
            print(f"compact_{phase}={seconds:.3f}s ({seconds / compact_seconds:.1%})")
        return
    profile: dict[str, float] = {}
    started = perf_counter()
    count = departure_count = accession_count = 0
    for _, departures, _, accessions in iter_fixed_plan_coupled_seeds(
            snapshot, people, atz, start, target, dep, hire,
            range(args.seeds), profile):
        count += 1
        departure_count += len(departures)
        accession_count += len(accessions["events"])
    elapsed = perf_counter() - started
    print(f"seeds={count}; target={target.date()}; total_s={elapsed:.3f}; "
          f"per_seed_s={elapsed / count:.3f}; departures={departure_count}; "
          f"accessions={accession_count}")
    for phase, seconds in profile.items():
        print(f"{phase}={seconds:.3f}s ({seconds / elapsed:.1%})")


if __name__ == "__main__":
    main()
