"""Exact parity of the optional OE stock metric provider in synthetic cases."""
from __future__ import annotations

import pandas as pd
import pytest

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from probe_oe_stock_preaggregation import grouped_metrics, reference_metrics
from test_monte_carlo_yearly_reference import SCENARIOS, _frame_signature
from test_org_simulation_reconciliation import _bridge_context
from utils.org_simulation_events import build_simulation_event_summary, normalize_simulation_events
from utils.org_simulation_reconciliation import build_departure_stock_bridge


@pytest.mark.parametrize("order", ([0, 1, 2, 3, 4, 5, 6], [6, 3, 1, 5, 0, 4, 2], [2, 4, 6, 0, 5, 1, 3]))
def test_group_metrics_preserve_multiple_positions_and_row_order(order: list[int]) -> None:
    frame = pd.DataFrame({
        "PersNr": ["000001", "000001", "000002", None, "000003", "000003", "000004"],
        "Is_Vacant": [False, False, False, False, True, False, True],
        "Organisationseinheit": ["A", "A", "B", "B", "C", "C", "D"],
        "OE-Cluster": ["X", "X", "X", "X", "Y", "Y", "Z"],
        "MAK_Reporting": [0.1, 0.2, 0.35, 0.7, 0.5, 0.4, 1.0],
    }).iloc[order].reset_index(drop=True)
    for dimension in ("Organisationseinheit", "OE-Cluster"):
        assert grouped_metrics(frame, dimension) == reference_metrics(frame, dimension)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_optional_stock_metrics_preserve_oe_bridge(scenario: str) -> None:
    context = _bridge_context(scenario)
    events = normalize_simulation_events(context, expected_run_signature="a")
    baseline = build_departure_stock_bridge(context, events, expected_run_signature="a")
    candidate = build_departure_stock_bridge(context, events, expected_run_signature="a",
                                             stock_metric_provider=grouped_metrics)
    assert candidate.keys() == baseline.keys()
    for key in baseline:
        left, right = baseline[key], candidate[key]
        if isinstance(left, pd.DataFrame):
            assert _frame_signature(right) == _frame_signature(left), (scenario, key)
        else:
            assert right == left, (scenario, key)

    summary = build_simulation_event_summary(context, expected_run_signature="a")
    candidate_summary = {**summary, "stock_reconciliation": candidate}
    for dimension in ("Jobfamily", "JF-Cluster"):
        original_view = build_jobfamily_yearly_view(context, summary, dimension=dimension)
        candidate_view = build_jobfamily_yearly_view(context, candidate_summary, dimension=dimension)
        assert candidate_view.keys() == original_view.keys()
        for key in original_view:
            left, right = original_view[key], candidate_view[key]
            if isinstance(left, pd.DataFrame):
                assert _frame_signature(right) == _frame_signature(left), (scenario, dimension, key)
            else:
                assert right == left, (scenario, dimension, key)
