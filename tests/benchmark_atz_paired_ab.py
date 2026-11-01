"""Paired, read-only timing of the ATZ probe before and after offset batching.

Run: py tests/benchmark_atz_paired_ab.py
The baseline is loaded from a committed Git blob; no checkout is changed.
"""
from __future__ import annotations

import statistics
import subprocess
import sys
import types
from pathlib import Path
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state
from dataloader import loader
from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
from abgaenge.params import default_params
from abgaenge import quit_batch_probe as current

BASELINE_REVISION = "1ef964b"
SEED_COUNT = 1000
PAIR_COUNT = 6


def _baseline_module() -> types.ModuleType:
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_REVISION}:abgaenge/quit_batch_probe.py"],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    name = "abgaenge._quit_batch_probe_paired_baseline"
    module = types.ModuleType(name)
    module.__package__ = "abgaenge"
    sys.modules[name] = module
    exec(compile(source, f"{BASELINE_REVISION}:quit_batch_probe.py", "exec"), module.__dict__)
    return module


def main() -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "atz_paired_cluster_page")
    _, bundle, _ = page._get_simulation_cluster_context(source_summary)
    people = _prepare_employee_forecast_base(snapshot, atz, FROZEN_STICHTAG)
    params = default_params()
    params["components"] = {"atz": True, "retirement": True,
                            "quit": True, "ruhend": True}
    params["atz"].update(new_atz_rate=0.5, use_atz_matrix=False)
    params["ruhend"].update(ruhend_new_cases_per_year=8,
                            ruhend_return_rate=0.95,
                            ruhend_avg_duration_months=3)
    target = pd.Timestamp("2026-12-31")
    baseline = _baseline_module()
    variants = {"baseline": baseline, "current": current}
    prepared = {name: module.prepare_quit_batch(people, atz, FROZEN_STICHTAG,
                                                target, params)
                for name, module in variants.items()}
    yearly = {name: module.prepare_quit_yearly(snapshot, bundle.oe_map)
              for name, module in variants.items()}

    for seed in (7, 42, 123, 999):
        records = {}
        annual = {}
        for name, module in variants.items():
            records[name] = next(module.iter_quit_seed_records(prepared[name], [seed]))[1]
            annual[name] = module.departure_annual_event_values(records[name], yearly[name])
        pd.testing.assert_frame_equal(pd.DataFrame(records["baseline"]),
                                      pd.DataFrame(records["current"]), check_exact=True)
        assert annual["baseline"] == annual["current"], seed

    measurements: dict[str, list[float]] = {name: [] for name in variants}
    for pair in range(PAIR_COUNT):
        order = ("baseline", "current") if pair % 2 == 0 else ("current", "baseline")
        totals = {}
        for name in order:
            module = variants[name]
            started = perf_counter()
            event_count = annual_key_count = 0
            for _, records, _, _ in module.iter_quit_seed_records(
                    prepared[name], range(SEED_COUNT)):
                event_count += len(records)
                annual_key_count += len(module.departure_annual_event_values(
                    records, yearly[name]))
            duration = perf_counter() - started
            measurements[name].append(duration)
            totals[name] = (event_count, annual_key_count)
        assert totals["baseline"] == totals["current"], pair
        print(f"pair={pair + 1}; order={','.join(order)}; "
              f"baseline={measurements['baseline'][-1]:.4f}s; "
              f"current={measurements['current'][-1]:.4f}s; "
              f"events={totals['current'][0]}; annual_keys={totals['current'][1]}")
    differences = [new - old for new, old in zip(measurements["current"],
                                                 measurements["baseline"])]
    print(f"pairs={PAIR_COUNT}; seeds_per_variant={SEED_COUNT}; "
          f"baseline_median={statistics.median(measurements['baseline']):.4f}s; "
          f"current_median={statistics.median(measurements['current']):.4f}s; "
          f"paired_difference_median={statistics.median(differences):+.4f}s; "
          f"paired_differences={[round(value, 4) for value in differences]}")


if __name__ == "__main__":
    main()
