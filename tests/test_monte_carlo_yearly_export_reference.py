"""Frozen annual export tables before Monte Carlo optimization.

Capture once: py tests/test_monte_carlo_yearly_export_reference.py --capture
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from components.org_simulation_yearly import build_yearly_summary_table, build_yearly_view
from test_monte_carlo_yearly_reference import _frame_signature
from test_org_simulation_reconciliation import _bridge_context
from utils.jobfamily_yearly_stock_export import build_jobfamily_stock_export_tables
from utils.org_simulation_events import build_simulation_event_summary
from utils.org_yearly_view_export import prepare_yearly_view_export


REFERENCE = ROOT / "tests" / "fixtures" / "monte_carlo_yearly_export_reference.json"
SCENARIOS = ("departures", "atz_ruhend", "combined")


def _payload(scenario: str) -> dict:
    context = _bridge_context(scenario)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    start_year = int(summary["metadata"]["base_date"].year)
    end_year = int(summary["metadata"]["target_date"].year)
    years = list(range(start_year, end_year + 1))
    orgs = sorted(context["baseline_snapshot"].Organisationseinheit.dropna().unique().tolist())
    event_types = sorted(summary["events"].Ereignisart.dropna().unique().tolist())
    selected = build_yearly_view(
        summary, dimension="Organisationseinheit", years=years,
        organizations=orgs[:1], event_types=event_types,
        metric="MAK-Veränderung",
    )
    selected["summary_table"] = build_yearly_summary_table(selected)
    tables, _ = prepare_yearly_view_export(summary, selected)
    output = {f"oe_{name}": _frame_signature(frame) for name, frame in tables.items()}

    for dimension, label in (("Jobfamily", "jf"), ("JF-Cluster", "jf_cluster")):
        view = build_jobfamily_yearly_view(context, summary, dimension=dimension)
        if view["stock"].empty:
            continue
        groups = sorted(view["stock"][dimension].dropna().unique().tolist())
        types = sorted(view["annual"].Ereignisart.dropna().unique().tolist())
        jf_tables = build_jobfamily_stock_export_tables(
            view, dimension=dimension, groups=groups, years=years,
            metric="MAK", event_types=types,
        )
        output.update({f"{label}_{name}": _frame_signature(frame)
                       for name, frame in jf_tables.items()})
    return output


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_frozen_yearly_export_tables(scenario: str) -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert _payload(scenario) == reference["scenarios"][scenario]


if __name__ == "__main__":
    if sys.argv[1:] != ["--capture"]:
        raise SystemExit("Use --capture only for the initial reference capture.")
    if REFERENCE.exists():
        raise SystemExit("Reference exists; refusing to overwrite.")
    payload = {"description": "Exact annual OE and jobfamily Excel source tables from the unoptimized code.",
               "source_commit": "2b60965",
               "scenarios": {scenario: _payload(scenario) for scenario in SCENARIOS}}
    REFERENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(SCENARIOS)} annual export reference scenarios: {REFERENCE.name}")
