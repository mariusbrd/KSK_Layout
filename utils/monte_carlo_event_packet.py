"""Event-only packet prototype from one already simulated CompactPlus run.

Skips OE/JF stock bridges and display audits. It still requires the saved
simulation context and prepared final totals, so it is not a fast simulator.
"""
from __future__ import annotations

from collections.abc import Mapping

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.monte_carlo_metric_packet import LEVELS, make_metric_packet
from utils.org_simulation_events import aggregate_simulation_events, normalize_simulation_events


def build_event_only_packet(context: dict, *, target_totals: Mapping[str, int | float],
                            baseline_cache: dict | None = None) -> dict:
    signature = context.get("run_signature")
    events = normalize_simulation_events(context, expected_run_signature=signature)
    annual = {
        "Organisationseinheit": aggregate_simulation_events(events, dimension="Organisationseinheit"),
        "OE-Cluster": aggregate_simulation_events(events, dimension="OE-Cluster"),
    }
    minimal_summary = {"run_signature": signature, "events": events,
                       "metadata": context["metadata"]}
    for level in ("Jobfamily", "JF-Cluster"):
        annual[level] = build_jobfamily_yearly_view(
            context, minimal_summary, dimension=level, include_stock=False,
            baseline_cache=baseline_cache)["annual"]
    return make_metric_packet(target_totals=target_totals, annual_tables=annual,
                              stock_status={level: "Nicht berechnet" for level in LEVELS})
