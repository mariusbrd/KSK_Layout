"""Paired JF/JF-cluster benchmark of grouped totals and shared projection."""
from __future__ import annotations

import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import components.jobfamily_simulation_yearly as yearly
from dataloader.kpi_engine import compute_headcount
from test_monte_carlo_production_multi_year_reference import _payload
from test_monte_carlo_yearly_reference import _frame_signature
from utils.org_simulation_events import build_simulation_event_summary
from utils.org_simulation_reconciliation import raw_stock_mak


def _historical_group_totals(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    work = frame.copy(deep=True)
    work[dimension] = yearly._dimension_values(work, dimension)
    rows = [{dimension: name, "Köpfe": float(compute_headcount(group)),
             "MAK": raw_stock_mak(group)} for name, group in work.groupby(dimension)]
    return pd.DataFrame(rows).set_index(dimension) if rows else pd.DataFrame(columns=["Köpfe", "MAK"])


def _signature(value):
    return {key: (_frame_signature(item) if isinstance(item, pd.DataFrame) else item)
            for key, item in value.items()}


def main() -> None:
    def measure(context):
        if not context["run_signature"].endswith("departures_only::7"):
            return
        summary = build_simulation_event_summary(
            context, expected_run_signature=context["run_signature"])
        samples = {"historical": [], "grouped": []}
        expected = None
        for pair in range(2):
            for mode in (("historical", "grouped") if pair == 0 else ("grouped", "historical")):
                start = perf_counter()
                if mode == "historical":
                    with patch.object(yearly, "_group_totals", _historical_group_totals):
                        views = {dimension: yearly.build_jobfamily_yearly_view(
                            context, summary, dimension=dimension)
                            for dimension in yearly.DIMENSIONS}
                else:
                    shared = {}
                    views = {dimension: yearly.build_jobfamily_yearly_view(
                        context, summary, dimension=dimension, projection_cache=shared)
                        for dimension in yearly.DIMENSIONS}
                elapsed = perf_counter() - start
                digest = {dimension: _signature(view) for dimension, view in views.items()}
                if expected is None:
                    expected = digest
                else:
                    assert digest == expected, (pair, mode)
                samples[mode].append(round(elapsed, 4))
        print(json.dumps({"samples_seconds": samples,
                          "median_seconds": {mode: statistics.median(values)
                                             for mode, values in samples.items()},
                          "exact_result_parity": True}, indent=2), flush=True)

    _payload(probe=measure)


if __name__ == "__main__":
    main()
