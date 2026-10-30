"""Freeze additional seeds across final stocks and annual OE/JF results.

Capture once: py tests/test_monte_carlo_seed_reference.py --capture
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from test_monte_carlo_yearly_reference import _frame_signature
from test_org_simulation_reconciliation import _bridge_context
from utils.org_simulation_events import build_simulation_event_summary


REFERENCE = ROOT / "tests" / "fixtures" / "monte_carlo_seed_reference.json"
CASES = (("departures", 7), ("departures", 123), ("combined", 7), ("combined", 123))


def _payload(scenario: str, seed: int) -> dict:
    context = _bridge_context(scenario, seed=seed)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    frames = {
        "final_snapshot": context["final_snapshot"],
        "departure_events": context["departure_events"],
        "accession_events": context["accession_events"],
        "normalized_events": summary["events"],
        "oe_annual": summary["annual_oe"],
        "oe_cluster_annual": summary["annual_cluster"],
        "oe_stock_annual": summary["stock_reconciliation"]["annual"],
        "oe_stock_checks": summary["stock_reconciliation"]["checks"],
    }
    for dimension, label in (("Jobfamily", "jf"), ("JF-Cluster", "jf_cluster")):
        view = build_jobfamily_yearly_view(context, summary, dimension=dimension)
        frames[f"{label}_annual"] = view["annual"]
        frames[f"{label}_stock"] = view["stock"]
        frames[f"{label}_stock_checks"] = view["stock_checks"]
    return {"oe_stock_status": summary["stock_reconciliation"]["status"],
            "frames": {name: _frame_signature(frame) for name, frame in frames.items()}}


@pytest.mark.parametrize("scenario,seed", CASES)
def test_frozen_seed_outputs(scenario: str, seed: int) -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert _payload(scenario, seed) == reference["cases"][f"{scenario}:{seed}"]


if __name__ == "__main__":
    if sys.argv[1:] != ["--capture"]:
        raise SystemExit("Use --capture only for the initial reference capture.")
    if REFERENCE.exists():
        raise SystemExit("Reference exists; refusing to overwrite.")
    payload = {"description": "Exact reference results for nondefault seeds before Monte Carlo optimization.",
               "source_commit": "2b60965", "seed_rule": "Both forecast engines receive the stated seed.",
               "cases": {f"{scenario}:{seed}": _payload(scenario, seed) for scenario, seed in CASES}}
    REFERENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(CASES)} additional seeded reference runs: {REFERENCE.name}")
