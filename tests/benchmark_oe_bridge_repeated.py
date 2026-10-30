"""Repeat OE bridge timings with alternating order on one frozen local run.

Run: py tests/benchmark_oe_bridge_repeated.py
The setup loads and simulates once; only the bridge is timed repeatedly.
"""
from __future__ import annotations

import json
import statistics
import time

import pandas as pd

from probe_oe_stock_preaggregation import grouped_metrics
from test_monte_carlo_production_yearly_reference import _payload
from test_monte_carlo_yearly_reference import _frame_signature
from utils.org_simulation_events import normalize_simulation_events
from utils.org_simulation_reconciliation import build_departure_stock_bridge


def main() -> None:
    output = {}

    def measure(context):
        signature = context["run_signature"]
        events = normalize_simulation_events(context, expected_run_signature=signature)
        runs = {"standard": [], "preaggregated": []}
        references = {}
        for pair in range(3):
            order = ("standard", "preaggregated") if pair % 2 == 0 else ("preaggregated", "standard")
            for mode in order:
                start = time.perf_counter()
                bridge = build_departure_stock_bridge(
                    context, events, expected_run_signature=signature,
                    stock_metric_provider=grouped_metrics if mode == "preaggregated" else None)
                runs[mode].append(round(time.perf_counter() - start, 4))
                reference = {key: _frame_signature(value) if isinstance(value, pd.DataFrame) else value
                             for key, value in bridge.items()}
                if mode in references:
                    assert reference == references[mode]
                else:
                    references[mode] = reference
        assert references["standard"] == references["preaggregated"]
        output.update({"seconds": runs, "median_seconds": {key: round(statistics.median(value), 4)
                                                      for key, value in runs.items()},
                       "exact_bridge_parity": True, "pairs": 3})

    _payload(probe=measure)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
