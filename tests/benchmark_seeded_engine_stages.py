"""Read-only stage timings of complete CompactPlus runs with changing seeds.

Run: py tests/benchmark_seeded_engine_stages.py
Input loading is outside timers. No employee rows are printed.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state
from test_monte_carlo_yearly_reference import _frame_signature
from abgaenge.params import default_params as abg_defaults
from zugaenge.params import default_params as zug_defaults
from dataloader import loader
from dataloader import compact_simulation_engine as engine

STAGES = ("_prepare_employee_forecast_base", "run_forecast_abgaenge",
          "_apply_attrition_events_to_employee_state", "_build_vacancies_from_attrition",
          "run_forecast_zugaenge", "enrich_zugaenge_events", "_update_existing_rows",
          "_append_new_people_rows", "_finalize_future_snapshot",
          "_build_simulation_audit_tables")


def _measure_run(snapshot, atz, source, bundle, source_signature, abg, zug, *, include_audits=True):
    stages = {}
    with ExitStack() as stack:
        for name in STAGES:
            original = getattr(engine, name)
            def wrapper(*args, _name=name, _original=original, **kwargs):
                start = time.perf_counter()
                try:
                    return _original(*args, **kwargs)
                finally:
                    item = stages.setdefault(_name, {"seconds": 0.0, "calls": 0})
                    item["seconds"] += time.perf_counter() - start
                    item["calls"] += 1
            stack.enter_context(patch.object(engine, name, wrapper))
        start = time.perf_counter()
        result = engine.simulate_compact_snapshot(
            snapshot_df=snapshot, df_atz=atz, target_date=pd.Timestamp("2026-12-31"),
            base_date=FROZEN_STICHTAG, abgaenge_params=deepcopy(abg),
            zugaenge_params=deepcopy(zug), active_cluster_source=source,
            cluster_mapping_bundle=bundle, cluster_source_signature=source_signature,
            include_audits=include_audits)
        elapsed = time.perf_counter() - start
    return result, round(elapsed, 4), {key: {"seconds": round(value["seconds"], 4),
                                       "calls": value["calls"]} for key, value in stages.items()}


def main() -> None:
    _freeze_context()
    _reset_state()
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "seeded_engine_benchmark_page")
    compact = _load_page_module("*_Kompakt.py", "seeded_engine_benchmark_compact")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, source_signature = page._get_simulation_cluster_context(source_summary)
    reference = json.loads((ROOT / "tests" / "fixtures" / "monte_carlo_production_yearly_reference.json")
                           .read_text(encoding="utf-8"))["payload"]["cases"]
    rows = []
    for hires in (0, 20):
        for seed in (42, 7, 123):
            abg, zug = abg_defaults(), zug_defaults()
            abg["random_seed"] = seed
            zug["random_seed"] = seed
            if hires:
                zug["new_hires"].update(active=True, count_per_year=hires, strategy="Fill Vacancies")
            result, seconds, stages = _measure_run(snapshot, atz, source, bundle, source_signature, abg, zug)
            if seed == 42:
                label = "new_hires_20_per_year" if hires else "no_entries"
                prepared = compact.prepare_compact_data(result.future_snapshot_df)
                assert _frame_signature(prepared) == reference[label]["frames"]["final_snapshot"]
            rows.append({"new_hires_per_year": hires, "seed": seed, "seconds": seconds,
                         "departures": result.metadata["abgaenge_events"],
                         "accessions": result.metadata["zugaenge_events"], "stages": stages})
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
