"""Frozen hashes of the local CompactPlus data path before optimization.

Capture once: py tests/test_monte_carlo_production_yearly_reference.py --capture
The fixture contains hashes and metadata, never employee rows.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state
from test_monte_carlo_yearly_reference import _frame_signature
from abgaenge.params import default_params as abg_defaults
from zugaenge.params import default_params as zug_defaults
from dataloader import loader
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from utils.org_simulation_events import build_simulation_event_summary
from utils.status_quo_baseline import build_status_quo_snapshot
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view

REFERENCE = ROOT / "tests" / "fixtures" / "monte_carlo_production_yearly_reference.json"
TARGET = pd.Timestamp("2026-12-31")


def _payload(probe=None) -> dict:
    _freeze_context()
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "reference_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "reference_compact_plus")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, source_signature = page._get_simulation_cluster_context(source_summary)
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(snapshot, FROZEN_STICHTAG))
    cases = {}
    for label, hires in (("no_entries", 0), ("new_hires_20_per_year", 20)):
        abg, zug = abg_defaults(), zug_defaults()
        if hires:
            zug["new_hires"].update(active=True, count_per_year=hires, strategy="Fill Vacancies")
        result = simulate_compact_snapshot(
            snapshot_df=snapshot, df_atz=atz, target_date=TARGET, base_date=FROZEN_STICHTAG,
            abgaenge_params=deepcopy(abg), zugaenge_params=deepcopy(zug),
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=source_signature,
        )
        final = compact.prepare_compact_data(result.future_snapshot_df)
        context = page._build_simulation_event_context(
            result, bundle, f"phase0::{label}", baseline_snapshot=baseline,
            final_snapshot=final, zugaenge_params=zug, abgaenge_params=abg,
            df_atz=atz, run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
            source_snapshot=snapshot,
        )
        if probe is not None and label == "no_entries":
            probe(context)
        summary = build_simulation_event_summary(context, expected_run_signature=f"phase0::{label}")
        bridge = summary["stock_reconciliation"]
        frames = {
            "final_snapshot": final,
            "departure_events": context["departure_events"],
            "accession_events": context["accession_events"],
            "events": summary["events"],
            "oe_annual": summary["annual_oe"],
            "oe_cluster_annual": summary["annual_cluster"],
            "oe_stock_annual": bridge["annual"],
            "oe_stock_checks": bridge["checks"],
            "oe_person_diagnostics": bridge["person_diagnostics"],
        }
        for dimension, key in (("Jobfamily", "jf"), ("JF-Cluster", "jf_cluster")):
            view = build_jobfamily_yearly_view(context, summary, dimension=dimension)
            for name in ("annual", "stock", "stock_checks"):
                frames[f"{key}_{name}"] = view[name]
            cases.setdefault(label, {})[f"{key}_stock_reason"] = view["stock_reason"]
        cases[label].update({"oe_stock_status": bridge["status"], "oe_stock_reason": bridge["reason"],
                             "frames": {name: _frame_signature(frame) for name, frame in frames.items()}})
    return {
        "input": {"snapshot": _frame_signature(snapshot), "atz": _frame_signature(atz)},
        "settings": FROZEN_SETTINGS,
        "base_date": str(FROZEN_STICHTAG.date()), "target_date": str(TARGET.date()),
        "cases": cases,
    }


def test_frozen_production_yearly_outputs() -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert _payload() == reference["payload"]


if __name__ == "__main__":
    if sys.argv[1:] != ["--capture"]:
        raise SystemExit("Use --capture only for the initial reference capture.")
    if REFERENCE.exists():
        raise SystemExit("Reference exists; refusing to overwrite.")
    payload = {"description": "Hash-only local production input and yearly outputs before Monte Carlo optimization.",
               "source_commit": "9e71aba", "python": sys.version.split()[0],
               "pandas": pd.__version__, "payload": _payload()}
    REFERENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(payload['payload']['cases'])} local production reference cases: {REFERENCE.name}")
