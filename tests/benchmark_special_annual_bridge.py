"""Profile the parity bridge for annual OE/JF stocks; no product mutations."""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import logging
from pathlib import Path
from statistics import median
from time import perf_counter
import sys

import pandas as pd

logging.getLogger("streamlit").setLevel(logging.CRITICAL)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import dataloader.compact_simulation_engine as simulation_engine
from abgaenge.params import default_params as departure_defaults
from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state,
)
from dataloader import loader
from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _finalize_future_snapshot,
    _prepare_employee_forecast_base, _resolve_atz_status_map,
    _resolve_simulation_cluster_context, _update_existing_rows,
    simulate_compact_snapshot,
)
from utils.compact_regular_stock import project_regular_position_stocks, sync_original_position_oe
from utils.compact_special_position_events import special_position_transitions
from zugaenge.params import default_params as hire_defaults


def _fingerprint(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()[:12]


def _measure_case(seed: int, open_exclusions: bool, repeats: int) -> None:
    if open_exclusions:
        exclusions = deepcopy(FROZEN_SETTINGS["exclusions"])
        exclusions.update(vorstand=False, ruhend_bv=False, org_units=[])
        FROZEN_SETTINGS["exclusions"] = exclusions
        simulation_engine.get_setting = lambda key, default=None: deepcopy(
            FROZEN_SETTINGS.get(key, default))
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(Path(__file__).resolve().parents[1]), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp("2030-12-31")
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    compact = _load_page_module("*_Kompakt.py", f"special_bridge_benchmark_{seed}")
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=True, count_per_year=20, strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0)
    hire["trainee"].update(active=True, new_cases_per_year=4)
    observed = {}
    product_start = perf_counter()
    product = simulate_compact_snapshot(
        snapshot, atz, end, base_date=start,
        abgaenge_params={**deepcopy(dep), "random_seed": seed},
        zugaenge_params={**deepcopy(hire), "random_seed": seed},
        active_cluster_source=source, cluster_mapping_bundle=bundle,
        cluster_source_signature=signature, include_audits=False,
        year_end_observer=lambda date, state, events: observed.setdefault(date, state))
    product_time = perf_counter() - product_start
    departures = product.abgaenge_result["events_person_level"]
    atz_pivot = product.abgaenge_result["tables"]["atz_pivot"]
    accession_events = product.zugaenge_result["events"]
    timing = defaultdict(list)
    dates = [pd.Timestamp(year, 12, 31) for year in range(2026, 2030)]
    for _ in range(repeats):
        repeat_start = perf_counter()
        t = perf_counter()
        preapplied_people = _apply_attrition_events_to_employee_state(people, departures)
        timing["preapply_departures"].append(perf_counter() - t)
        t = perf_counter()
        transitions = special_position_transitions(snapshot, accession_events)
        timing["extract_transitions"].append(perf_counter() - t)
        for date in dates:
            status = _resolve_atz_status_map(atz_pivot, date)
            t = perf_counter()
            base = _update_existing_rows(snapshot, preapplied_people, date, status)
            timing["update_existing"].append(perf_counter() - t)
            t = perf_counter()
            base = _finalize_future_snapshot(
                base, date, active_cluster_source=source,
                cluster_mapping_bundle=bundle, stock_only=True)
            timing["finalize_stock_only"].append(perf_counter() - t)
            t = perf_counter()
            baseline = compact.prepare_compact_data(base)
            timing["compact_prepare"].append(perf_counter() - t)
            t = perf_counter()
            baseline = sync_original_position_oe(baseline, snapshot, observed[date])
            timing["sync_original_oe"].append(perf_counter() - t)
            t = perf_counter()
            mapped = apply_clusters_to_snapshot_from_source(
                baseline, source, mapping_bundle=bundle)
            baseline["OE-Cluster"] = mapped["OE-Cluster"].to_numpy()
            timing["cluster_mapping"].append(perf_counter() - t)
            t = perf_counter()
            profile = {}
            result = project_regular_position_stocks(
                baseline, mapped, pd.DataFrame(), [], base_date=start,
                target_date=date, special_transitions=transitions,
                profile=profile)
            timing["position_projection"].append(perf_counter() - t)
            timing["result_rows"].append(sum(len(frame) for frame in result.values()))
            for key, value in profile.items():
                timing[key].append(value)
        timing["observed_bridge_total"].append(perf_counter() - repeat_start)
    print(f"seed={seed} open_exclusions={open_exclusions} repeats={repeats} product_once={product_time:.3f}s")
    for key, values in sorted(timing.items()):
        if key == "result_rows":
            print(f"  {key}: {int(median(values))} per annual rebuild")
        else:
            print(f"  {key}: median={median(values):.4f}s total_per_repeat={sum(values)/repeats:.4f}s")
    measured = [key for key in timing if key not in {"result_rows", "observed_bridge_total"}
                and not key.startswith("position_")]
    # The position_* detail is included in position_projection and must not be added twice.
    measured.append("position_projection")
    print(f"  bridge_total_per_repeat={sum(sum(timing[key]) for key in measured)/repeats:.3f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    mapping_path = Path(__file__).resolve().parents[1] / "config" / "cluster_mapping.xlsx"
    before = _fingerprint(mapping_path)
    print(f"cluster_mapping_sha256_prefix={before}")
    _measure_case(42, True, args.repeats)
    after = _fingerprint(mapping_path)
    if before != after:
        raise RuntimeError("Cluster mapping changed during benchmark")
