"""Time many full fixed-plan runs plus exact annual OE/JF stock construction."""
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from statistics import median
from time import perf_counter, process_time
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import dataloader.compact_simulation_engine as simulation_engine
from abgaenge.params import default_params as departure_defaults
from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state,
)
from benchmark_special_annual_bridge import _fingerprint
from components.jobfamily_simulation_yearly import _dimension_values
from dataloader import loader
from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state,
    _apply_salary_automation_to_employee_state,
    _fill_reserved_plan_positions, _finalize_future_snapshot,
    _normalize_persnr_series, _prepare_employee_forecast_base,
    _resolve_atz_status_map, _resolve_simulation_cluster_context,
    _update_existing_rows, simulate_compact_snapshot,
)
from utils.compact_regular_stock import (
    atz_affected_position_mask, project_regular_position_stocks,
    project_special_annual_position_stocks,
    rebase_existing_atz_status, sync_original_position_oe,
    update_compact_for_atz_changes, update_stock_only_for_atz_changes,
)
from utils.compact_special_position_events import special_position_transitions
from utils.monte_carlo_complete_packet import build_complete_metric_packet
from utils.monte_carlo_ensemble import aggregate_packets
from utils.monte_carlo_event_packet import build_event_only_packet
from utils.org_simulation_reconciliation import grouped_stock_metrics
from utils.status_quo_baseline import build_status_quo_snapshot
from zugaenge.params import default_params as hire_defaults


def _frame_digest(frame: pd.DataFrame) -> str:
    """Include schema, index, row order and all cell values in a process-stable digest."""
    digest = hashlib.sha256()
    schema = [(str(name), str(dtype)) for name, dtype in frame.dtypes.items()]
    digest.update(repr((frame.shape, schema, str(frame.index.dtype),
                        frame.index.names)).encode("utf-8"))
    hashable = frame.copy()
    for column in hashable.select_dtypes(include="object").columns:
        hashable[column] = hashable[column].map(repr)
    digest.update(pd.util.hash_pandas_object(hashable, index=True,
                                             categorize=False).to_numpy().tobytes())
    return digest.hexdigest()


def _parity_check(snapshot, annual_state, accession_events, atz_pivot,
                  date, source, bundle, compact, projected):
    annual_events = accession_events.loc[
        pd.to_datetime(accession_events["date"]).dt.normalize().le(date)].copy()
    annual_people = annual_state.copy()
    annual_people["PersNr"] = _normalize_persnr_series(annual_people["PersNr"])
    annual_people = _apply_salary_automation_to_employee_state(
        annual_people, target_date=date, events_df=annual_events)
    status = _resolve_atz_status_map(atz_pivot, date)
    annual_snapshot = _update_existing_rows(snapshot, annual_people, date, status)
    annual_snapshot = _fill_reserved_plan_positions(
        annual_snapshot, snapshot, annual_people)
    annual_snapshot = _finalize_future_snapshot(
        annual_snapshot, date, active_cluster_source=source,
        cluster_mapping_bundle=bundle)
    annual_frame = compact.prepare_compact_data(annual_snapshot)
    for dimension, group_frame in projected.items():
        work = (annual_frame if dimension in ("Organisationseinheit", "OE-Cluster")
                else annual_frame.assign(**{
                    dimension: _dimension_values(annual_frame, dimension)}))
        actual = {row[dimension]: (row["K\u00f6pfe"], row["MAK"])
                  for _, row in group_frame.loc[group_frame["Stichtag"].eq(date)].iterrows()}
        expected = grouped_stock_metrics(work, dimension)
        if actual != expected:
            mismatch_count = sum(actual.get(key) != expected.get(key)
                                 for key in actual.keys() | expected.keys())
            raise AssertionError(
                f"Annual parity failed at {date.date()}, {dimension}: "
                f"{mismatch_count} differing groups")


def run_many(seeds: int, parity_seeds: set[int], paired_baseline: bool = False,
             metric_packets: bool = False, verify_each_seed: bool = True,
             phase_per_seed: bool = False, seed_start: int = 0,
             target_year: int = 2030, packet_output: Path | None = None,
             frame_digests: bool = False) -> None:
    exclusions = deepcopy(FROZEN_SETTINGS["exclusions"])
    exclusions.update(vorstand=False, ruhend_bv=False, org_units=[])
    FROZEN_SETTINGS["exclusions"] = exclusions
    simulation_engine.get_setting = lambda key, default=None: deepcopy(
        FROZEN_SETTINGS.get(key, default))
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(target_year, 12, 31)
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    compact = _load_page_module("*_Kompakt.py", "special_annual_many_seed_benchmark")
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=True, count_per_year=20, strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0)
    hire["trainee"].update(active=True, new_cases_per_year=4)
    dates = [pd.Timestamp(year, 12, 31) for year in range(
        2026, target_year + 1 if metric_packets else target_year)]
    if metric_packets:
        from test_compact_sim_cluster_source_consistency import _load_page7_module
        metric_totals = _load_page7_module()._metric_totals
        opening_snapshot = build_status_quo_snapshot(snapshot, start)
    packet_runs = []
    phase_times = {"product": [], "annual_bridge": [],
                   "legacy_bridge": [], "verification": [], "metric_packet": []}
    failures = []
    total_start = perf_counter()
    for index, seed in enumerate(range(seed_start, seed_start + seeds), start=1):
        seed_wall_start = perf_counter()
        seed_cpu_start = process_time()
        try:
            observed = {}
            t = perf_counter()
            product_cpu_start = process_time()
            product = simulate_compact_snapshot(
                snapshot, atz, end, base_date=start,
                abgaenge_params={**deepcopy(dep), "random_seed": seed},
                zugaenge_params={**deepcopy(hire), "random_seed": seed},
                active_cluster_source=source, cluster_mapping_bundle=bundle,
                cluster_source_signature=signature, include_audits=False,
                year_end_observer=lambda date, state, events: observed.setdefault(date, state))
            product_time = perf_counter() - t
            product_cpu = process_time() - product_cpu_start
            departures = product.abgaenge_result["events_person_level"]
            atz_pivot = product.abgaenge_result["tables"]["atz_pivot"]
            accession_events = product.zugaenge_result["events"]
            t = perf_counter()
            bridge_cpu_start = process_time()
            preapplied = _apply_attrition_events_to_employee_state(people, departures)
            transitions = special_position_transitions(snapshot, accession_events)
            rebuild_inputs = {}
            annual_baselines = []
            first_updated = None
            previous_input = None
            previous_stock = None
            previous_compact = None
            for date in dates:
                status = _resolve_atz_status_map(atz_pivot, date)
                if first_updated is None:
                    base = _update_existing_rows(snapshot, preapplied, date, status)
                    first_updated = base
                else:
                    base = rebase_existing_atz_status(
                        first_updated, snapshot, preapplied, status)
                cached_update = base
                if previous_input is None:
                    affected = None
                    stock = _finalize_future_snapshot(
                        base, date, active_cluster_source=source,
                        cluster_mapping_bundle=bundle, stock_only=True)
                else:
                    affected = atz_affected_position_mask(previous_input, base)
                    stock = update_stock_only_for_atz_changes(
                        previous_input, previous_stock, base,
                        lambda affected: _finalize_future_snapshot(
                            affected, date, active_cluster_source=source,
                            cluster_mapping_bundle=bundle, stock_only=True))
                previous_input, previous_stock = base, stock
                if previous_compact is None:
                    raw_compact = compact.prepare_compact_data(stock)
                else:
                    raw_compact = update_compact_for_atz_changes(
                        previous_compact, stock, affected,
                        compact.prepare_compact_data)
                previous_compact = raw_compact
                baseline = raw_compact
                baseline = sync_original_position_oe(baseline, snapshot, observed[date])
                mapped = apply_clusters_to_snapshot_from_source(
                    baseline, source, mapping_bundle=bundle)
                baseline["OE-Cluster"] = mapped["OE-Cluster"].to_numpy()
                annual_baselines.append((date, baseline, mapped))
                if verify_each_seed:
                    rebuild_inputs[date] = (
                        cached_update, stock, raw_compact, baseline, mapped)
            chronological = project_special_annual_position_stocks(
                annual_baselines, transitions)
            bridge_time = perf_counter() - t
            bridge_cpu = process_time() - bridge_cpu_start
            legacy_time = None
            if paired_baseline:
                t = perf_counter()
                previous_preapplied = _apply_attrition_events_to_employee_state(
                    people, departures)
                previous_transitions = special_position_transitions(
                    snapshot, accession_events)
                previous_first = None
                for date in dates:
                    status = _resolve_atz_status_map(atz_pivot, date)
                    if previous_first is None:
                        full_base = _update_existing_rows(
                            snapshot, previous_preapplied, date, status)
                        previous_first = full_base
                    else:
                        full_base = rebase_existing_atz_status(
                            previous_first, snapshot, previous_preapplied, status)
                    full_stock = _finalize_future_snapshot(
                        full_base, date, active_cluster_source=source,
                        cluster_mapping_bundle=bundle, stock_only=True)
                    full_compact = compact.prepare_compact_data(full_stock)
                    full_compact = sync_original_position_oe(
                        full_compact, snapshot, observed[date])
                    full_mapped = apply_clusters_to_snapshot_from_source(
                        full_compact, source, mapping_bundle=bundle)
                    full_compact["OE-Cluster"] = full_mapped["OE-Cluster"].to_numpy()
                    project_regular_position_stocks(
                        full_compact, full_mapped, pd.DataFrame(), [],
                        base_date=start, target_date=date,
                        special_transitions=previous_transitions, target_only=True)
                legacy_time = perf_counter() - t
            t = perf_counter()
            if verify_each_seed:
                for date, (cached_update, stock, raw_compact, baseline, mapped) in rebuild_inputs.items():
                    direct_update = _update_existing_rows(
                        snapshot, preapplied, date, _resolve_atz_status_map(atz_pivot, date))
                    pd.testing.assert_frame_equal(
                        cached_update, direct_update, check_exact=True)
                    direct_stock = _finalize_future_snapshot(
                        direct_update, date, active_cluster_source=source,
                        cluster_mapping_bundle=bundle, stock_only=True)
                    pd.testing.assert_frame_equal(stock, direct_stock, check_exact=True)
                    direct_compact = compact.prepare_compact_data(direct_stock)
                    pd.testing.assert_frame_equal(
                        raw_compact, direct_compact, check_exact=True)
                    previous = project_regular_position_stocks(
                        baseline, mapped, pd.DataFrame(), [], base_date=start,
                        target_date=date, special_transitions=transitions)
                    for dimension, frame in chronological.items():
                        actual = frame.loc[frame["Stichtag"].eq(date)].reset_index(drop=True)
                        expected = previous[dimension].loc[
                            previous[dimension]["Stichtag"].eq(date)].reset_index(drop=True)
                        pd.testing.assert_frame_equal(
                            actual, expected, check_exact=True)
            if seed in parity_seeds:
                for date in dates:
                    projected = {dimension: frame.loc[
                        frame["Stichtag"].eq(date)].reset_index(drop=True)
                        for dimension, frame in chronological.items()}
                    _parity_check(snapshot, observed[date], accession_events,
                                  atz_pivot, date, source, bundle, compact, projected)
            verification_time = perf_counter() - t
            if metric_packets:
                t = perf_counter()
                totals = metric_totals(product.future_snapshot_df)
                context = {
                    "run_signature": f"seed:{seed}", "metadata": product.metadata,
                    "baseline_snapshot": opening_snapshot,
                    "final_snapshot": product.future_snapshot_df,
                    "zugaenge_params": hire, "cluster_mapping": bundle.to_dict(),
                    "departure_events": departures,
                    "accession_events": accession_events,
                }
                packet = build_complete_metric_packet(
                    context, target_totals=totals,
                    annual_stock_tables=chronological)
                if verify_each_seed:
                    reference = build_event_only_packet(context, target_totals=totals)
                    if (packet["target"] != reference["target"]
                            or packet["annual_values"] != reference["annual_values"]):
                        raise AssertionError(f"Event packet differs for seed {seed}")
                # This combined run has active accessions. The existing
                # product annual tiles have no certified departure-rate view.
                if "departure_rate_inputs" in packet:
                    raise AssertionError(f"Uncertified rate inputs for seed {seed}")
                run = {"seed": seed, "status": "ok", "packet": packet}
                if frame_digests:
                    run["frame_digests"] = {
                        "future_snapshot": _frame_digest(product.future_snapshot_df),
                        "future_employee": _frame_digest(product.future_employee_df),
                        "departure_events": _frame_digest(departures),
                        "accession_events": _frame_digest(accession_events),
                        "annual_observed": {str(date.date()): _frame_digest(observed[date])
                                            for date in dates},
                        "annual_stocks": {dimension: _frame_digest(frame)
                                          for dimension, frame in chronological.items()},
                    }
                packet_runs.append(run)
                phase_times["metric_packet"].append(perf_counter() - t)
            phase_times["product"].append(product_time)
            phase_times["annual_bridge"].append(bridge_time)
            if legacy_time is not None:
                phase_times["legacy_bridge"].append(legacy_time)
            phase_times["verification"].append(verification_time)
            if phase_per_seed:
                print(f"seed_phase seed={seed} product_wall={product_time:.3f} "
                      f"product_cpu={product_cpu:.3f} bridge_wall={bridge_time:.3f} "
                      f"bridge_cpu={bridge_cpu:.3f} "
                      f"packet_wall={phase_times['metric_packet'][-1] if metric_packets else 0:.3f} "
                      f"total_wall={perf_counter()-seed_wall_start:.3f} "
                      f"total_cpu={process_time()-seed_cpu_start:.3f} "
                      f"departures={len(departures)} "
                      f"accessions={len(accession_events)}", flush=True)
        except Exception as exc:
            failures.append((seed, type(exc).__name__, str(exc)[:180]))
            if metric_packets:
                packet_runs.append({"seed": seed, "status": "failed",
                                    "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAILED seed={seed}: {type(exc).__name__}: {str(exc)[:180]}", flush=True)
        if index % 10 == 0 or index == seeds:
            print(f"progress={index}/{seeds} elapsed={perf_counter()-total_start:.1f}s "
                  f"completed={len(phase_times['product'])} failures={len(failures)}", flush=True)
    completed = len(phase_times["product"])
    print(f"completed={completed}/{seeds} failures={len(failures)} "
          f"wall_total={perf_counter()-total_start:.3f}s")
    for phase, values in phase_times.items():
        if values:
            print(f"{phase}: total={sum(values):.3f}s mean={sum(values)/len(values):.3f}s "
                  f"median={median(values):.3f}s max={max(values):.3f}s")
    print(f"all_seeds_checked_against_previous_path={verify_each_seed} "
          f"product_parity_seeds={sorted(parity_seeds)} "
          f"checked_years=2026-{target_year if metric_packets else target_year-1} "
          "dimensions=Organisationseinheit,OE-Cluster,Jobfamily,JF-Cluster")
    if metric_packets:
        t = perf_counter()
        ensemble = aggregate_packets(base_seed=seed_start, count=seeds, runs=packet_runs)
        print(f"ensemble_status={ensemble['status']} "
              f"successful={ensemble['successful_count']}/{seeds} "
              f"failures={len(ensemble['failures'])} "
              f"annual_events={len(ensemble['annual_values'] or [])} "
              f"annual_stocks={len(ensemble['annual_stock_values'] or [])} "
              f"aggregation_seconds={perf_counter()-t:.3f}", flush=True)
        if ensemble["status"] != "complete":
            raise AssertionError(f"Incomplete ensemble: {ensemble['failures'][:5]}")
        if packet_output is not None:
            payload = [{key: run[key] for key in ("seed", "packet", "frame_digests")
                        if key in run}
                       for run in packet_runs]
            with packet_output.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))
    if failures:
        raise AssertionError(f"{len(failures)} seeds failed; first failures: {failures[:5]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--parity-seeds", default="0,42,99")
    parser.add_argument("--paired-baseline", action="store_true")
    parser.add_argument("--metric-packets", action="store_true")
    parser.add_argument("--skip-full-verification", action="store_true")
    parser.add_argument("--phase-per-seed", action="store_true")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--target-year", type=int, default=2030)
    parser.add_argument("--packet-output", type=Path)
    parser.add_argument("--frame-digests", action="store_true")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")
    parity_seeds = {int(value) for value in args.parity_seeds.split(",") if value.strip()}
    if args.seed_start < 0:
        parser.error("--seed-start must be nonnegative")
    if args.target_year < 2026:
        parser.error("--target-year must be 2026 or later")
    if args.packet_output is not None and not args.metric_packets:
        parser.error("--packet-output requires --metric-packets")
    if args.frame_digests and args.packet_output is None:
        parser.error("--frame-digests requires --packet-output")
    if any(seed < args.seed_start or seed >= args.seed_start + args.seeds for seed in parity_seeds):
        parser.error("parity seeds must be within the measured seed range")
    mapping_path = ROOT / "config" / "cluster_mapping.xlsx"
    before = _fingerprint(mapping_path)
    print(f"cluster_mapping_sha256_prefix={before}", flush=True)
    run_many(args.seeds, parity_seeds, args.paired_baseline, args.metric_packets,
             verify_each_seed=not args.skip_full_verification,
             phase_per_seed=args.phase_per_seed, seed_start=args.seed_start,
             target_year=args.target_year, packet_output=args.packet_output,
             frame_digests=args.frame_digests)
    if _fingerprint(mapping_path) != before:
        raise RuntimeError("Cluster mapping changed during benchmark")
