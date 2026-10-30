"""Event-only path must equal complete seeded annual OE/JF event values."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from test_compact_sim_cluster_source_consistency import _load_page7_module
from test_monte_carlo_metric_packet import CASES, _packet
from test_org_simulation_reconciliation import _bridge_context
from utils.monte_carlo_event_packet import build_event_only_packet
from utils.monte_carlo_metric_packet import LEVELS


@pytest.mark.parametrize("scenario,seed", CASES)
def test_event_only_packet_equals_complete_run(scenario: str, seed: int) -> None:
    context = _bridge_context(scenario, seed=seed)
    totals = _load_page7_module()._metric_totals(context["final_snapshot"])
    with patch("utils.org_simulation_reconciliation.build_departure_stock_bridge",
               side_effect=AssertionError("OE stock bridge called")), patch(
                   "components.jobfamily_simulation_yearly.reporting_projection_base",
                   side_effect=AssertionError("JF stock projection called")):
        compact = build_event_only_packet(context, target_totals=totals)
    complete = _packet(scenario, seed, context=context)
    assert compact["version"] == complete["version"]
    assert compact["target"] == complete["target"]
    assert compact["annual_values"] == complete["annual_values"]
    assert compact["stock_status"] == {level: "Nicht berechnet" for level in LEVELS}
