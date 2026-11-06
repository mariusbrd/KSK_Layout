"""Event-year views for regular-only seeds without a target snapshot.

This is an isolated Monte Carlo probe. Annual stock certification still uses
the product snapshot path until a compact stock calculation is proven exact.
"""
from __future__ import annotations

from typing import Any
from collections.abc import Callable

import pandas as pd

from components.jobfamily_simulation_yearly import (
    build_jobfamily_yearly_view, prepare_jobfamily_baseline,
)
from utils.org_simulation_events import (
    aggregate_simulation_events, normalize_simulation_events,
)
from zugaenge.enrichment import enrich_zugaenge_events
from zugaenge.fixed_plan_seed_stream import rebuild_fixed_plan_regular_snapshot
from utils.org_simulation_reconciliation import grouped_stock_metrics
from components.jobfamily_simulation_yearly import _dimension_values


def build_regular_year_end_stocks(
    position_snapshot: pd.DataFrame,
    employee_base: pd.DataFrame,
    departure_events: pd.DataFrame,
    atz_pivot: pd.DataFrame,
    hire_records: list[dict[str, Any]],
    new_people: list[dict[str, Any]],
    hire_params: dict[str, Any],
    *,
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    prepare_snapshot: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    active_cluster_source=None,
    cluster_mapping_bundle=None,
    cluster_source_signature: str | None = None,
    prepared_employee_template: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Replay saved regular decisions at year ends and return exact stock groups.

    This correctness reference avoids new random draws. It still materializes a
    product snapshot per year, so it is not the final fast Monte Carlo kernel.
    The finalized snapshot already contains the stock fields. An optional
    ``prepare_snapshot`` hook can reproduce a page-specific preparation for
    parity checks; the default avoids that additional full-frame pass.
    """
    start, end = pd.Timestamp(base_date), pd.Timestamp(target_date)
    if end < start:
        raise ValueError("target_date must not precede base_date")
    dimensions = {"oe": "Organisationseinheit", "oe_cluster": "OE-Cluster",
                  "jf": "Jobfamily", "jf_cluster": "JF-Cluster"}
    rows = {key: [] for key in dimensions}
    dates = [min(pd.Timestamp(year, 12, 31), end)
             for year in range(start.year, end.year + 1)]
    dates = [date for date in dates if date > start]
    for date in dates:
        departures = (departure_events.loc[
            pd.to_datetime(departure_events["event_date"]).dt.normalize() <= date].copy()
            if not departure_events.empty else departure_events.copy())
        hires = [record for record in hire_records
                 if pd.Timestamp(record["date"]).normalize() <= date]
        people = [person for person in new_people
                  if pd.Timestamp(person["Eintritt"]).normalize() <= date]
        rebuilt = rebuild_fixed_plan_regular_snapshot(
            position_snapshot, employee_base, departures, atz_pivot,
            hires, people, date, hire_params,
            active_cluster_source=active_cluster_source,
            cluster_mapping_bundle=cluster_mapping_bundle,
            cluster_source_signature=cluster_source_signature,
            prepared_employee_template=prepared_employee_template,
            stock_only=prepare_snapshot is None)
        frame = (prepare_snapshot(rebuilt.future_snapshot)
                 if prepare_snapshot is not None else rebuilt.future_snapshot)
        stock_columns = ["PersNr", "Is_Vacant", "MAK_Reporting",
                         "Organisationseinheit", "OE-Cluster",
                         "Jobfamily", "JF-Cluster"]
        stock = frame[stock_columns].copy()
        for dimension in ("Jobfamily", "JF-Cluster"):
            stock[dimension] = _dimension_values(stock, dimension)
        for key, dimension in dimensions.items():
            for group, (heads, mak) in grouped_stock_metrics(stock, dimension).items():
                rows[key].append({"Stichtag": date, dimension: group,
                                  "Köpfe": heads, "MAK": mak})
    return {key: pd.DataFrame(rows[key],
                              columns=["Stichtag", dimension, "Köpfe", "MAK"])
            for key, dimension in dimensions.items()}


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
