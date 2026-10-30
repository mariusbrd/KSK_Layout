"""Compare full saved-run OE/JF result build, alternating standard and probe.

Run: py tests/benchmark_full_result_preaggregation.py
Simulation and loading are setup only; neither is included in the timed result build.
"""
from __future__ import annotations

import json
import statistics
import time
from unittest.mock import patch

import pandas as pd

from probe_oe_stock_preaggregation import grouped_metrics
from test_monte_carlo_production_yearly_reference import _payload
from test_monte_carlo_yearly_reference import _frame_signature
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.org_simulation_events import build_simulation_event_summary
from utils import org_simulation_reconciliation as reconciliation


def _fingerprint(value):
    if isinstance(value, pd.DataFrame):
        return _frame_signature(value)
    if isinstance(value, dict):
        return {key: _fingerprint(item) for key, item in value.items()}
    if isinstance(value, set):
        return sorted(value)
    return value


def main() -> None:
    output = {}

    def measure(context):
        signature = context["run_signature"]
        original = reconciliation.build_departure_stock_bridge
        samples = {mode: [] for mode in ("standard", "preaggregated")}
        reference = None
        for pair in range(3):
            order = ("standard", "preaggregated") if pair % 2 == 0 else ("preaggregated", "standard")
            for mode in order:
                def optimized_bridge(ctx, events, *, expected_run_signature):
                    return original(ctx, events, expected_run_signature=expected_run_signature,
                                    stock_metric_provider=grouped_metrics)

                start = time.perf_counter()
                if mode == "preaggregated":
                    with patch.object(reconciliation, "build_departure_stock_bridge", optimized_bridge):
                        summary = build_simulation_event_summary(context, expected_run_signature=signature)
                else:
                    summary = build_simulation_event_summary(context, expected_run_signature=signature)
                oe_seconds = time.perf_counter() - start
                views = {}
                for dimension in ("Jobfamily", "JF-Cluster"):
                    views[dimension] = build_jobfamily_yearly_view(context, summary, dimension=dimension)
                total_seconds = time.perf_counter() - start
                digest = _fingerprint({"summary": summary, "views": views})
                if reference is None:
                    reference = digest
                else:
                    assert digest == reference, f"Result mismatch in pair {pair}, mode {mode}"
                samples[mode].append({"oe_summary_seconds": round(oe_seconds, 4),
                                      "full_result_seconds": round(total_seconds, 4)})
        output.update({"samples": samples, "median_seconds": {
            mode: {key: round(statistics.median(sample[key] for sample in values), 4)
                   for key in ("oe_summary_seconds", "full_result_seconds")}
            for mode, values in samples.items()}, "exact_result_parity": True, "pairs": 3})

    _payload(probe=measure)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
