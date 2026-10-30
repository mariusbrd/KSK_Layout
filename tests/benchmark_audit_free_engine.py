"""Compare full and audit-free seeded engine runs on frozen local inputs.

Run: py tests/benchmark_audit_free_engine.py
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import _freeze_context, _load_page_module, _reset_state
from benchmark_seeded_engine_stages import _measure_run
from test_monte_carlo_yearly_reference import _frame_signature
from abgaenge.params import default_params as abg_defaults
from zugaenge.params import default_params as zug_defaults
from dataloader import loader


def _signature(result):
    return {"metadata": result.metadata,
            "final_snapshot": _frame_signature(result.future_snapshot_df),
            "final_people": _frame_signature(result.future_employee_df),
            "departures": _frame_signature(result.abgaenge_result.get("events_person_level", pd.DataFrame())),
            "accessions": _frame_signature(result.zugaenge_result.get("events", pd.DataFrame()))}


def main() -> None:
    _freeze_context()
    _reset_state()
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "audit_free_benchmark_page")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, source_signature = page._get_simulation_cluster_context(source_summary)
    rows = []
    for hires in (0, 20):
        for seed in (42, 7, 123):
            abg, zug = abg_defaults(), zug_defaults()
            abg["random_seed"] = seed
            zug["random_seed"] = seed
            if hires:
                zug["new_hires"].update(active=True, count_per_year=hires, strategy="Fill Vacancies")
            measurements = {}
            order = (True, False) if seed != 7 else (False, True)
            for with_audits in order:
                result, seconds, stages = _measure_run(snapshot, atz, source, bundle,
                                                        source_signature, abg, zug,
                                                        include_audits=with_audits)
                measurements[with_audits] = {"seconds": seconds, "signature": _signature(result),
                                             "audit_seconds": stages.get("_build_simulation_audit_tables", {})
                                             .get("seconds", 0.0)}
                assert bool(result.audit_tables) == with_audits
            assert measurements[True]["signature"] == measurements[False]["signature"]
            rows.append({"new_hires_per_year": hires, "seed": seed,
                         "full_seconds": measurements[True]["seconds"],
                         "audit_free_seconds": measurements[False]["seconds"],
                         "audit_step_seconds": measurements[True]["audit_seconds"],
                         "exact_state_event_parity": True})
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
