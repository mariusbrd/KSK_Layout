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

    # Stacked bar & Total bar: Abgänge nach Grund
    # Default to empty figs to ensure keys exist
    charts["bar_abgaenge_reasons"] = _empty_fig("Abgänge nach Grund")
    charts["bar_reasons_total"] = _empty_fig("Gesamtverteilung der Abgänge nach Grund")
    
    if events_person_level is not None and not events_person_level.empty:
        df = events_person_level.copy()
        df = df[(df["headcount_change"] < 0) | (df["mak_change"] < 0)]
        
        if not df.empty:
            # 1. Stacked Bar (Time Series)
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
                title="Abgänge nach Grund (zeitlich)",
                xaxis_title="Periode",
                yaxis_title="Anzahl",
            )
            charts["bar_abgaenge_reasons"] = fig_bar

            # 2. Total Bar (Aggregated)
            total_stats = df.groupby("reason_code").size().reset_index(name="count")
            total_stats["reason_label"] = total_stats["reason_code"].map(REASON_LABELS)
            total_stats = total_stats.sort_values("count", ascending=True)
            
            fig_total = go.Figure()
            fig_total.add_trace(go.Bar(
                x=total_stats["count"],
                y=total_stats["reason_label"],
                orientation="h",
                text=total_stats["count"],
                textposition="auto",
                marker_color="rgb(55, 83, 109)"
            ))
            fig_total.update_layout(
                title="Gesamtverteilung der Abgänge nach Grund",
                xaxis_title="Anzahl Personen",
                yaxis_title=None,
                height=400,
                margin=dict(l=0, r=0, t=30, b=0)
            )
            charts["bar_reasons_total"] = fig_total

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
