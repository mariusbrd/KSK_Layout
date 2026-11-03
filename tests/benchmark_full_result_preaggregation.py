"""Compare full saved-run OE/JF build, alternating historical and grouped paths.

Run: py tests/benchmark_full_result_preaggregation.py
Simulation and loading are setup only; neither is included in the timed result build.
"""
from __future__ import annotations

import json
import statistics
import sys
import time

import pandas as pd

from test_monte_carlo_production_yearly_reference import _payload as _one_year_payload
from test_monte_carlo_production_multi_year_reference import _payload as _multi_year_payload
from test_monte_carlo_yearly_reference import _frame_signature
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.org_simulation_events import build_simulation_event_summary


def _fingerprint(value):
    if isinstance(value, pd.DataFrame):
        return _frame_signature(value)
    if isinstance(value, dict):
        return {key: _fingerprint(item) for key, item in value.items()}
    if isinstance(value, set):
        return sorted(value)
    return value


def main() -> None:
    multi_year = sys.argv[1:] == ["--multi-year"]
    if sys.argv[1:] not in ([], ["--multi-year"]):
        raise SystemExit("Usage: py tests/benchmark_full_result_preaggregation.py [--multi-year]")
    output = {}

    def measure(context):
        signature = context["run_signature"]
        case = signature.split("::", 1)[-1] if multi_year else "one_year"
        samples = {mode: [] for mode in ("standard", "preaggregated")}
        reference = None
        for pair in range(2 if multi_year else 3):
            order = ("standard", "preaggregated") if pair % 2 == 0 else ("preaggregated", "standard")
            for mode in order:
                start = time.perf_counter()
                summary = build_simulation_event_summary(
                    context, expected_run_signature=signature,
                    use_preaggregated_stock=mode == "preaggregated")
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
        output[case] = {"samples": samples, "median_seconds": {
            mode: {key: round(statistics.median(sample[key] for sample in values), 4)
                   for key in ("oe_summary_seconds", "full_result_seconds")}
            for mode, values in samples.items()}, "exact_result_parity": True,
            "pairs": 2 if multi_year else 3}

    (_multi_year_payload if multi_year else _one_year_payload)(probe=measure)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
