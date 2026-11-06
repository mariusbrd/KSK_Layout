"""Time the exact fixed-plan seed stream; outputs contain no employee data."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from time import perf_counter

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--target", default="2026-12-31")
    parser.add_argument("--compare-regular", action="store_true")
    parser.add_argument("--complete-regular", action="store_true")
    parser.add_argument("--annual-events", action="store_true")
    parser.add_argument("--annual-stocks", action="store_true")
    parser.add_argument("--annual-position-stocks", action="store_true")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")
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
    if args.annual_position_stocks:
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
        started = perf_counter()
        stock_seconds = 0.0
        count = annual_rows = 0
        for _, departures, _, _, new_people in iter_fixed_plan_regular_coupled_seeds(
                snapshot, people, atz, start, target, dep, hire,
                range(args.seeds)):
            stock_started = perf_counter()
            stocks = project_regular_position_stocks(
                baseline, mapped, departures, new_people,
                base_date=start, target_date=target)
            annual_rows += sum(len(stock) for stock in stocks.values())
            stock_seconds += perf_counter() - stock_started
            count += 1
        elapsed = perf_counter() - started
        print(f"regular_position_stock_seeds={count}; target={target.date()}; "
              f"total_s={elapsed:.3f}; stock_s={stock_seconds:.3f}; "
              f"per_seed_s={elapsed / count:.3f}; rows={annual_rows}; "
              "scope=departures_hires_four_position_year_end_stock_views")
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
