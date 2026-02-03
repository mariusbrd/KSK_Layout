"""
Visuals for Abgaenge forecast.
"""

from typing import Dict
import pandas as pd
import plotly.graph_objects as go

from .schemas import REASON_LABELS


def _empty_fig(title: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(title=title)
    return fig


def build_charts(forecast_kpis: pd.DataFrame, events_person_level: pd.DataFrame) -> Dict[str, go.Figure]:
    charts: Dict[str, go.Figure] = {}

    if forecast_kpis is None or forecast_kpis.empty:
        charts["line_headcount_mak"] = _empty_fig("Headcount und MAK")
        charts["bar_abgaenge_reasons"] = _empty_fig("Abgänge nach Grund")
        return charts

    # Line chart: Headcount & MAK
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=forecast_kpis["period_label"],
        y=forecast_kpis["headcount_end"],
        mode="lines+markers",
        name="Headcount",
    ))
    fig_line.add_trace(go.Scatter(
        x=forecast_kpis["period_label"],
        y=forecast_kpis["mak_end"],
        mode="lines+markers",
        name="MAK",
    ))
    fig_line.update_layout(title="Headcount und MAK", xaxis_title="Periode", yaxis_title="Wert")
    charts["line_headcount_mak"] = fig_line

    # Stacked bar: Abgänge nach Grund
    if events_person_level is None or events_person_level.empty:
        charts["bar_abgaenge_reasons"] = _empty_fig("Abgänge nach Grund")
    else:
        df = events_person_level.copy()
        df = df[(df["headcount_change"] < 0) | (df["mak_change"] < 0)]
        if df.empty:
            charts["bar_abgaenge_reasons"] = _empty_fig("Abgänge nach Grund")
        else:
            pivot = df.pivot_table(
                index="period_label",
                columns="reason_code",
                values="persnr",
                aggfunc="count",
                fill_value=0,
            )
            fig_bar = go.Figure()
            for reason_code in pivot.columns:
                label = REASON_LABELS.get(reason_code, reason_code)
                fig_bar.add_trace(go.Bar(
                    x=pivot.index,
                    y=pivot[reason_code],
                    name=label,
                ))
            fig_bar.update_layout(
                barmode="stack",
                title="Abgänge nach Grund",
                xaxis_title="Periode",
                yaxis_title="Anzahl",
            )
            charts["bar_abgaenge_reasons"] = fig_bar

    # Driver details charts (simple counts per period)
    if events_person_level is not None and not events_person_level.empty:
        df = events_person_level.copy()
        for reason_code, label in REASON_LABELS.items():
            sub = df[df["reason_code"] == reason_code]
            if sub.empty:
                continue
            counts = sub.groupby("period_label").size().reset_index(name="count")
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=counts["period_label"],
                y=counts["count"],
                name=label,
            ))
            fig.update_layout(title=f"{label} pro Periode", xaxis_title="Periode", yaxis_title="Anzahl")
            charts[f"driver_{reason_code}"] = fig

    return charts
