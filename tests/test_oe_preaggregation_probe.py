"""Exact parity of the optional OE stock metric provider in synthetic cases."""
from __future__ import annotations

import pandas as pd
import pytest

from probe_oe_stock_preaggregation import grouped_metrics
from test_monte_carlo_yearly_reference import SCENARIOS, _frame_signature
from test_org_simulation_reconciliation import _bridge_context
from utils.org_simulation_events import normalize_simulation_events
from utils.org_simulation_reconciliation import build_departure_stock_bridge


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
