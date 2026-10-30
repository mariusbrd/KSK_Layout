"""Read-only trial of grouped stock metrics against the exact current path.

Run: py tests/probe_oe_stock_preaggregation.py
This does not patch or change production code.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_monte_carlo_production_yearly_reference import _payload
from dataloader.kpi_engine import compute_headcount
from utils.org_simulation_reconciliation import raw_stock_mak, reporting_projection_base


def grouped_metrics(frame: pd.DataFrame, dimension: str) -> dict:
    """Candidate formula; tested here, never substituted into the bridge."""
    capacity = next((c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in frame), None)
    occupied = frame.loc[~frame.Is_Vacant].dropna(subset=["PersNr"])
    first = occupied.drop_duplicates(subset=[dimension, "PersNr"], keep="first")[[dimension, "PersNr"]]
    person_capacity = occupied.groupby([dimension, "PersNr"], sort=True)[capacity].sum().rename("_sum").reset_index()
    ordered = first.merge(person_capacity, on=[dimension, "PersNr"], how="left", sort=False)
    populated = {name: (len(group), float(group["_sum"].fillna(0).sum()))
                 for name, group in ordered.groupby(dimension, sort=True)}
    return {name: populated.get(name, (0, 0.0)) for name in frame[dimension].dropna().unique()}


def reference_metrics(frame: pd.DataFrame, dimension: str) -> dict:
    return {name: (compute_headcount(group), raw_stock_mak(group))
            for name, group in frame.groupby(dimension, sort=True)}


def main() -> None:
    results = []

    def probe(context):
        original = context["baseline_snapshot"]
        final = context["final_snapshot"]
        projection = reporting_projection_base(context, original)
        for frame_name, frame in (("baseline", original), ("projection", projection), ("final", final)):
            for dimension in ("Organisationseinheit", "OE-Cluster"):
                start = time.perf_counter()
                expected = reference_metrics(frame, dimension)
                reference_seconds = time.perf_counter() - start
                start = time.perf_counter()
                actual = grouped_metrics(frame, dimension)
                candidate_seconds = time.perf_counter() - start
                differences = [(name, expected.get(name), actual.get(name)) for name in expected.keys() | actual.keys()
                               if expected.get(name) != actual.get(name)]
                results.append({"frame": frame_name, "dimension": dimension,
                                "groups": len(expected), "different_groups": len(differences),
                                "reference_seconds": round(reference_seconds, 4),
                                "candidate_seconds": round(candidate_seconds, 4),
                                "different_headcounts": sum(a[0] != b[0] for _, a, b in differences if a and b),
                                "different_mak": sum(a[1] != b[1] for _, a, b in differences if a and b),
                                "max_abs_mak_difference": max((abs(a[1] - b[1]) for _, a, b in differences if a and b), default=0.0)})

    _payload(probe=probe)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(item["different_groups"] for item in results):
        raise AssertionError("Grouped stock metrics are not exactly equal to the current implementation")


if __name__ == "__main__":
    main()
