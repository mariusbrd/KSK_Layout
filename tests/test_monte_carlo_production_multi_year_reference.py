"""Frozen hash reference for fixed-plan productive 2025–2030 OE/JF results.

Capture once with --capture. No employee rows are stored.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state,
)
from test_monte_carlo_yearly_reference import _frame_signature
from abgaenge.params import default_params as departure_defaults
from zugaenge.params import default_params as hire_defaults
from dataloader import loader
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from utils.org_simulation_events import build_simulation_event_summary
from utils.status_quo_baseline import build_status_quo_snapshot
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view

REFERENCE = ROOT / "tests" / "fixtures" / "monte_carlo_production_multi_year_reference_v4.json"
CURRENT_REFERENCE = ROOT / "tests" / "fixtures" / "monte_carlo_production_multi_year_reference_v5.json"
REFERENCE_BY_MAPPING = {
    "39a4e67184c832289d28030c85e9d08d6b6cdafaecf43f3994d3224703fe2c2c": REFERENCE,
    "52d18a2dbb46f8f29f2567f1caa3574096d203d0cb9b4481a78d3cc718656625": CURRENT_REFERENCE,
}
TARGET = pd.Timestamp("2028-12-31")


def _payload(*, profile: bool = False, probe=None) -> dict:
    start = perf_counter()
    _freeze_context()
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "multi_year_reference_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "multi_year_reference_page")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, signature = page._get_simulation_cluster_context(source_summary)
    baseline = compact.prepare_compact_data(
        build_status_quo_snapshot(snapshot, FROZEN_STICHTAG))
    if profile:
        print(f"profile load_and_baseline={perf_counter() - start:.3f}s", flush=True)
    cases = {}
    for label, hires, training, case_target in (
        ("departures_only", 0, False, TARGET),
        ("with_hires", 20, False, TARGET),
        ("with_training", 0, True, pd.Timestamp("2030-12-31")),
    ):
        for seed in (7, 42):
            if profile and (label, seed) != ("departures_only", 7):
                continue
            case_start = perf_counter()
            dep, hire = departure_defaults(), hire_defaults()
            dep["random_seed"] = seed
            dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False,
                              atz_duration_ar_years=0.5, atz_duration_fr_years=0.5)
            dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
            if hires:
                hire["new_hires"].update(active=True, count_per_year=hires,
                                         strategy="Fill Vacancies")
            if training:
                hire["azubi"].update(active=True, new_cases_per_year=4,
                                     retention_rate=1.0)
                hire["trainee"].update(active=True, new_cases_per_year=4)
            result = simulate_compact_snapshot(
                snapshot, atz, case_target, base_date=FROZEN_STICHTAG,
                abgaenge_params=deepcopy(dep), zugaenge_params=deepcopy(hire),
                active_cluster_source=source, cluster_mapping_bundle=bundle,
                cluster_source_signature=signature, include_audits=False)
            assert len(result.future_snapshot_df) == len(snapshot)
            pd.testing.assert_series_equal(
                result.future_snapshot_df["Planstellennr"].reset_index(drop=True),
                snapshot["Planstellennr"].reset_index(drop=True))
            pd.testing.assert_series_equal(
                result.future_snapshot_df["Soll_FTE"].reset_index(drop=True),
                snapshot["Soll_FTE"].reset_index(drop=True))
            if training:
                event_types = result.zugaenge_result["events"]["type"]
                assert event_types.eq("Azubi_Hire").any()
                assert event_types.eq("Azubi_Conversion_In").any()
                assert not event_types.eq("Trainee_Hire").any()
            simulated = perf_counter()
            final = compact.prepare_compact_data(result.future_snapshot_df)
            compacted = perf_counter()
            run_signature = f"multi-year-reference::{label}::{seed}"
            context = page._build_simulation_event_context(
                result, bundle, run_signature, baseline_snapshot=baseline,
                final_snapshot=final, zugaenge_params=hire, abgaenge_params=dep,
                df_atz=atz, run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
                source_snapshot=snapshot)
            context_built = perf_counter()
            if probe is not None:
                probe(context)
            summary = build_simulation_event_summary(
                context, expected_run_signature=run_signature)
            oe_built = perf_counter()
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
            reasons = {}
            for dimension, key in (("Jobfamily", "jf"), ("JF-Cluster", "jf_cluster")):
                view = build_jobfamily_yearly_view(context, summary, dimension=dimension)
                for name in ("annual", "stock", "stock_checks"):
                    frames[f"{key}_{name}"] = view[name]
                reasons[f"{key}_stock_reason"] = view["stock_reason"]
            jf_built = perf_counter()
            if profile:
                print(f"profile case={label}__seed_{seed} "
                      f"simulation={simulated - case_start:.3f}s "
                      f"compact={compacted - simulated:.3f}s "
                      f"context={context_built - compacted:.3f}s "
                      f"oe_yearly={oe_built - context_built:.3f}s "
                      f"jf_yearly={jf_built - oe_built:.3f}s "
                      f"total={jf_built - case_start:.3f}s", flush=True)
            cases[f"{label}__seed_{seed}"] = {
                "target_date": str(case_target.date()),
                "departure_parameters": dep,
                "hire_parameters": hire,
                "oe_stock_status": bridge["status"],
                "oe_stock_reason": bridge["reason"],
                **reasons,
                "frames": {name: _frame_signature(frame) for name, frame in frames.items()},
            }
    return {
        "rule_version": "fixed-plan-v1",
        "input": {"snapshot": _frame_signature(snapshot), "atz": _frame_signature(atz)},
        "settings": FROZEN_SETTINGS,
        "base_date": str(FROZEN_STICHTAG.date()),
        "target_date": str(TARGET.date()),
        "cases": cases,
    }


def test_frozen_productive_multi_year_outputs() -> None:
    mapping_hash = hashlib.sha256((ROOT / "config" / "cluster_mapping.xlsx").read_bytes()).hexdigest()
    assert mapping_hash in REFERENCE_BY_MAPPING, "No certified reference for this cluster mapping"
    reference = json.loads(REFERENCE_BY_MAPPING[mapping_hash].read_text(encoding="utf-8"))
    assert _payload() == reference["payload"]


if __name__ == "__main__":
    if sys.argv[1:] == ["--profile"]:
        _payload(profile=True)
        raise SystemExit(0)
    if sys.argv[1:] != ["--capture"]:
        raise SystemExit("Use --capture only for the initial reference capture.")
    if REFERENCE.exists():
        raise SystemExit("Reference exists; refusing to overwrite.")
    payload = {
        "description": "Hash-only fixed-plan productive multi-year OE/JF reference through 2030.",
        "business_rules": ["preserve_multi_position_ist_mak", "preserve_atz_fr_headcount"],
        "python": sys.version.split()[0], "pandas": pd.__version__,
        "payload": _payload(),
    }
    REFERENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(f"Captured {len(payload['payload']['cases'])} productive multi-year cases")
