"""Event-year views for regular-only seeds without a target snapshot.

This is an isolated Monte Carlo probe. Annual stock certification still uses
the product snapshot path until a compact stock calculation is proven exact.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from components.jobfamily_simulation_yearly import (
    build_jobfamily_yearly_view, prepare_jobfamily_baseline,
)
from utils.org_simulation_events import (
    aggregate_simulation_events, normalize_simulation_events,
)
from zugaenge.enrichment import enrich_zugaenge_events


def build_compact_regular_annual_events(
    baseline_snapshot: pd.DataFrame,
    source_snapshot: pd.DataFrame,
    departure_events: pd.DataFrame,
    hire_records: list[dict[str, Any]],
    hire_params: dict[str, Any],
    departure_params: dict[str, Any],
    *,
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    cluster_mapping_bundle,
    cluster_source_signature: str | None,
    run_signature: str,
    prepared_jobfamily_baseline: dict | None = None,
) -> dict[str, pd.DataFrame]:
    """Build exact OE/JF event-year frames from the seed's saved events."""
    hires = pd.DataFrame(hire_records)
    if not hires.empty:
        hires = enrich_zugaenge_events(
            hires, source_snapshot, hire_params,
            cluster_mapping_bundle=cluster_mapping_bundle,
            active_cluster_source_signature=cluster_source_signature)
    # Existing people keep their Jobfamily in this regular-only mode. New
    # persons need only their group assignment for the JF event allocator.
    final_groups = baseline_snapshot[["PersNr", "Is_Vacant", "Jobfamily", "JF-Cluster"]].copy()
    if not hires.empty:
        new_groups = hires[["PersNr", "Jobfamily", "JF-Cluster"]].copy()
        new_groups["Is_Vacant"] = False
        final_groups = pd.concat(
            [final_groups, new_groups.reindex(columns=final_groups.columns)],
            ignore_index=True)
    context = {
        "run_signature": run_signature,
        "cluster_mapping": cluster_mapping_bundle.to_dict(),
        "departure_events": departure_events,
        "accession_events": hires,
        "baseline_snapshot": baseline_snapshot,
        "final_snapshot": final_groups,
        "zugaenge_params": hire_params,
    }
    events = normalize_simulation_events(
        context, expected_run_signature=run_signature)
    summary = {
        "run_signature": run_signature,
        "events": events,
        "metadata": {"base_date": pd.Timestamp(base_date),
                     "target_date": pd.Timestamp(target_date)},
        "abgaenge_params": departure_params,
        "stock_reconciliation": {"status": "Nicht verfügbar"},
    }
    output = {
        "oe_annual": aggregate_simulation_events(
            events, dimension="Organisationseinheit"),
        "oe_cluster_annual": aggregate_simulation_events(
            events, dimension="OE-Cluster"),
    }
    baseline_cache = (prepared_jobfamily_baseline if prepared_jobfamily_baseline is not None
                      else prepare_jobfamily_baseline(baseline_snapshot))
    for dimension, key in (("Jobfamily", "jf_annual"),
                           ("JF-Cluster", "jf_cluster_annual")):
        output[key] = build_jobfamily_yearly_view(
            context, summary, dimension=dimension,
            include_stock=False,
            baseline_cache=baseline_cache)["annual"]
    return output
