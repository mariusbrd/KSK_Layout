"""Frozen OE and jobfamily annual outputs before Monte Carlo optimization.

Capture only once from the untouched reference implementation:
    py tests/test_monte_carlo_yearly_reference.py --capture
Normal pytest runs compare and never rewrite the fixture.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from test_org_simulation_reconciliation import _bridge_context
from utils.org_simulation_events import build_simulation_event_summary


REFERENCE = ROOT / "tests" / "fixtures" / "monte_carlo_yearly_reference.json"
SCENARIOS = (
    "identity", "no_events", "departures", "entries", "atz_ruhend",
    "azubi_transfer", "combined",
)


def _frame_signature(frame: pd.DataFrame) -> dict:
    """Hash exact values, row order, index, columns and dtypes."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Expected a DataFrame")
    digest = hashlib.sha256()
    digest.update(json.dumps([str(value) for value in frame.columns], ensure_ascii=False).encode("utf-8"))
    digest.update(json.dumps([str(dtype) for dtype in frame.dtypes]).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy(dtype="uint64").tobytes())
    return {"rows": len(frame), "columns": [str(value) for value in frame.columns],
            "dtypes": [str(dtype) for dtype in frame.dtypes], "sha256": digest.hexdigest()}


def _payload(scenario: str) -> dict:
    context = _bridge_context(scenario)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    bridge = summary["stock_reconciliation"]
    frames = {
        "events": summary["events"],
        "oe_annual": summary["annual_oe"],
        "oe_cluster_annual": summary["annual_cluster"],
        "oe_stock_annual": bridge["annual"],
        "oe_stock_checks": bridge["checks"],
    }
    for dimension, label in (("Jobfamily", "jf"), ("JF-Cluster", "jf_cluster")):
        view = build_jobfamily_yearly_view(context, summary, dimension=dimension)
        frames[f"{label}_annual"] = view["annual"]
        frames[f"{label}_stock"] = view["stock"]
        frames[f"{label}_stock_checks"] = view["stock_checks"]
    return {"oe_stock_status": bridge["status"],
            "frames": {name: _frame_signature(frame) for name, frame in frames.items()}}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_frozen_yearly_outputs(scenario: str) -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert _payload(scenario) == reference["scenarios"][scenario]


if __name__ == "__main__":
    if sys.argv[1:] != ["--capture"]:
        raise SystemExit("Use --capture only for the initial reference capture.")
    if REFERENCE.exists():
        raise SystemExit("Reference exists; refusing to overwrite.")
    payload = {
        "description": "Exact annual table signatures from the unoptimized CompactPlus code; synthetic seeds and input from existing yearly reference cases.",
        "source_commit": "b848d6b",
        "python": sys.version.split()[0],
        "pandas": pd.__version__,
        "scenarios": {scenario: _payload(scenario) for scenario in SCENARIOS},
    }
    REFERENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(SCENARIOS)} annual reference scenarios: {REFERENCE.name}")
