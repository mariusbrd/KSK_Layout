"""Freeze the first compact metric contract on complete seeded reference runs.

Capture once: py tests/test_monte_carlo_metric_packet.py --capture
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_compact_sim_cluster_source_consistency import _load_page7_module
from test_org_simulation_reconciliation import _bridge_context
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.monte_carlo_metric_packet import LEVELS, make_metric_packet
from utils.org_simulation_events import build_simulation_event_summary

REFERENCE = ROOT / "tests" / "fixtures" / "monte_carlo_metric_packet.json"
CASES = (("departures", 7), ("departures", 123), ("combined", 7), ("combined", 123))


def _packet(scenario: str, seed: int) -> dict:
    context = _bridge_context(scenario, seed=seed)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    views = {level: build_jobfamily_yearly_view(context, summary, dimension=level)
             for level in ("Jobfamily", "JF-Cluster")}
    page = _load_page7_module()
    annual = {"Organisationseinheit": summary["annual_oe"],
              "OE-Cluster": summary["annual_cluster"],
              **{level: views[level]["annual"] for level in views}}
    status = {"Organisationseinheit": summary["stock_reconciliation"]["status"],
              "OE-Cluster": summary["stock_reconciliation"]["status"],
              **{level: "Verfügbar" if not views[level]["stock"].empty
                 else "Nicht verfügbar" for level in views}}
    packet = make_metric_packet(target_totals=page._metric_totals(context["final_snapshot"]),
                                annual_tables=annual, stock_status=status)
    assert set(packet["stock_status"]) == set(LEVELS)
    assert len(packet["annual_values"]) == 4 * sum(len(frame) for frame in annual.values())
    assert not any("PersNr" in str(key) for key in packet)
    return packet


def _signature(packet: dict) -> dict:
    data = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"sha256": hashlib.sha256(data).hexdigest(), "annual_values": len(packet["annual_values"]),
            "target": packet["target"], "stock_status": packet["stock_status"]}


@pytest.mark.parametrize("scenario,seed", CASES)
def test_metric_packet_matches_frozen_full_run(scenario: str, seed: int) -> None:
    expected = json.loads(REFERENCE.read_text(encoding="utf-8"))["cases"][f"{scenario}:{seed}"]
    assert _signature(_packet(scenario, seed)) == expected


if __name__ == "__main__":
    if sys.argv[1:] != ["--capture"]:
        raise SystemExit("Use --capture only for the initial reference capture.")
    if REFERENCE.exists():
        raise SystemExit("Reference exists; refusing to overwrite.")
    payload = {"description": "Compact annual-value packet hashes from complete seeded synthetic runs; no employee rows.",
               "packet_version": 1,
               "cases": {f"{scenario}:{seed}": _signature(_packet(scenario, seed))
                         for scenario, seed in CASES}}
    REFERENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(CASES)} metric packets")
