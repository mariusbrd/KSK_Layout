"""Combine one run's existing event views with certified annual stock values."""
from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.monte_carlo_metric_packet import LEVELS, make_metric_packet
from utils.org_simulation_events import aggregate_simulation_events, normalize_simulation_events


def build_complete_metric_packet(
    context: dict,
    *,
    target_totals: Mapping[str, int | float],
    annual_stock_tables: Mapping[str, pd.DataFrame],
    baseline_cache: dict | None = None,
    departure_rate_inputs: Mapping[str, dict] | None = None,
    certified_rate_summary: dict | None = None,
) -> dict:
    """Use the same saved run for OE/JF events and certified year stocks."""
    signature = context.get("run_signature")
    if departure_rate_inputs is not None and certified_rate_summary is not None:
        raise ValueError("Supply either explicit rate inputs or a certified summary")
    if certified_rate_summary is not None:
        from utils.monte_carlo_departure_rate import certified_rate_inputs
        departure_rate_inputs = certified_rate_inputs(context, certified_rate_summary)
    events = normalize_simulation_events(context, expected_run_signature=signature)
    annual = {
        "Organisationseinheit": aggregate_simulation_events(
            events, dimension="Organisationseinheit"),
        "OE-Cluster": aggregate_simulation_events(
            events, dimension="OE-Cluster"),
    }
    summary = {"run_signature": signature, "events": events,
               "metadata": context["metadata"]}
    for level in ("Jobfamily", "JF-Cluster"):
        annual[level] = build_jobfamily_yearly_view(
            context, summary, dimension=level, include_stock=False,
            baseline_cache=baseline_cache)["annual"]
    return make_metric_packet(
        target_totals=target_totals, annual_tables=annual,
        stock_status={level: "Abgestimmt" for level in LEVELS},
        annual_stock_tables=annual_stock_tables,
        departure_rate_inputs=departure_rate_inputs)
