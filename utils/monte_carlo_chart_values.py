"""Certified CompactPlus IST chart values and per-category run statistics."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import pandas as pd

from utils.monte_carlo_ensemble import _statistics


CHART_VERSION = 1
IST_DIMENSIONS = (
    "Geschlecht", "Alterskohorte", "Ausbildung", "Beschäftigungsgrad_Kat",
    "Beschäftigungsstatus", "Betriebszugehörigkeit_Bin", "ATZ_Status",
    "Vergütungsklasse",
)


def extract_ist_chart_values(prepared: pd.DataFrame, compact_module) -> list[tuple]:
    """Read exactly the tables used by the existing IST heads and MAK plots."""
    occupied = prepared.loc[prepared["Is_Vacant"] != True] if "Is_Vacant" in prepared else prepared
    mak_col = next((name for name in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK")
                    if name in occupied.columns), "FTE_assigned")
    records = []
    for dimension in IST_DIMENSIONS:
        if dimension != "Vergütungsklasse" and dimension not in occupied.columns:
            continue
        for metric, value_col in (("heads", "Headcount"), ("mak", mak_col)):
            if dimension == "Vergütungsklasse":
                table = compact_module.create_stacked_tariff_breakdown_table(occupied, value_col)
                group_col, metric_col = "Entgeltgruppe", "Gesamt"
            else:
                table = compact_module.create_breakdown_table(occupied, dimension, value_col)
                group_col, metric_col = dimension, "IST"
            if "Hinweis" in table or group_col not in table or metric_col not in table:
                continue
            for group, value in table[[group_col, metric_col]].itertuples(index=False, name=None):
                records.append((metric, dimension, str(group), float(value)))
    return records


def aggregate_ist_chart_values(packets: Sequence[Mapping], *, band_level: float) -> dict:
    """Reject missing chart packets; zero-fill absent additive groups per seed."""
    if len(packets) < 2:
        raise ValueError("Chart bands require at least two runs")
    checked = []
    for packet in packets:
        if packet.get("compact_ist_version") != CHART_VERSION or "compact_ist_values" not in packet:
            raise ValueError("Missing certified CompactPlus IST chart packet")
        values = {}
        for record in packet["compact_ist_values"]:
            if len(record) != 4:
                raise ValueError("Invalid CompactPlus IST chart record")
            metric, dimension, group, raw_value = record
            if metric not in {"heads", "mak"} or dimension not in IST_DIMENSIONS:
                raise ValueError("Unknown CompactPlus IST chart key")
            key = (metric, dimension, str(group))
            value = float(raw_value)
            if key in values or not math.isfinite(value) or value < 0:
                raise ValueError("Duplicate or invalid CompactPlus IST chart value")
            values[key] = value
        checked.append(values)
    keys = sorted(set().union(*(set(values) for values in checked)))
    return {
        "version": CHART_VERSION,
        "values": [(*key, {**_statistics(samples := [values.get(key, 0.0) for values in checked], band_level),
                            "samples": samples}) for key in keys],
    }


def overlay_ist_band(fig, chart_statistics: Mapping, *, metric: str,
                     dimension: str):
    """Return a copy of the reference bar plot with magenta P10/P90 marks."""
    import plotly.graph_objects as go

    if chart_statistics.get("version") != CHART_VERSION:
        return fig
    lookup = {(record_metric, record_dimension, group): values
              for record_metric, record_dimension, group, values
              in chart_statistics.get("values", ())}
    if not fig.data or fig.data[0].type != "bar":
        return fig
    categories = [str(value) for value in fig.data[0].y]
    if not categories or any((metric, dimension, category) not in lookup for category in categories):
        return fig
    overlaid = go.Figure(fig)
    low = [lookup[(metric, dimension, category)]["lower"] for category in categories]
    high = [lookup[(metric, dimension, category)]["upper"] for category in categories]
    overlaid.add_trace(go.Scatter(
        x=[value for pair in zip(low, high) for value in (*pair, None)],
        y=[value for category in categories for value in (category, category, None)],
        mode="lines", line=dict(color="rgba(190, 24, 93, 0.65)", width=7),
        name="P10–P90", hoverinfo="skip", legendgroup="monte_carlo",
    ))
    for name, values, symbol in (("P10", low, "line-ns-open"), ("P90", high, "line-ns-open")):
        overlaid.add_trace(go.Scatter(
            x=values, y=categories, mode="markers", name=name,
            marker=dict(color="#be185d", size=14, symbol=symbol, line=dict(width=2)),
            hovertemplate=f"<b>%{{y}}</b><br>{name}: %{{x:,.2f}}<extra></extra>",
            legendgroup="monte_carlo",
        ))
    overlaid.update_layout(showlegend=True)
    return overlaid
