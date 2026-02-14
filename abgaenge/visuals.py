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


def build_charts(
    forecast_kpis: pd.DataFrame, 
    events_person_level: pd.DataFrame,
    metric_type: str = "Köpfe"
) -> Dict[str, go.Figure]:
    charts: Dict[str, go.Figure] = {}

    if forecast_kpis is None or forecast_kpis.empty:
        charts["line_headcount_mak"] = _empty_fig("Personalstand (Köpfe & MAK)")
        charts["bar_abgaenge_reasons"] = _empty_fig("Abgangsgründe")
        return charts

    # 1. Line chart: Headcount & MAK (Trend)
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=forecast_kpis["period_label"],
        y=forecast_kpis["headcount_end"],
        mode="lines+markers",
        name="Köpfe (Headcount)",
        line=dict(color="#1f77b4")
    ))
    fig_line.add_trace(go.Scatter(
        x=forecast_kpis["period_label"],
        y=forecast_kpis["mak_end"],
        mode="lines+markers",
        name="MAK (Kapazität)",
        line=dict(color="#2ca02c")
    ))
    fig_line.update_layout(
        title="Entwicklung Personalbestand (Brutto-Verlust)", 
        xaxis_title="Zeitraum", 
        yaxis_title="Anzahl / MAK",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5)
    )
    charts["line_headcount_mak"] = fig_line

    # 2. Preparation for Reason-based charts
    charts["bar_abgaenge_reasons"] = _empty_fig("Abgangsursachen im Zeitverlauf")
    charts["bar_reasons_total"] = _empty_fig("Gesamtverteilung nach Ursache")
    
    if events_person_level is not None and not events_person_level.empty:
        df = events_person_level.copy()
        
        # Determine value column based on metric type
        val_col = "headcount_change" if metric_type == "Köpfe" else "mak_change"
        val_label = "Anzahl (Köpfe)" if metric_type == "Köpfe" else "Kapazität (MAK)"
        unit = "" if metric_type == "Köpfe" else " MAK"
        
        # We only want to visualize "losses" or meaningful events for this view
        # Ensure we take the absolute value for bar heights (loss is positive magnitude in chart)
        df["vis_value"] = df[val_col].abs()
        df = df[df["vis_value"] > 0]
        
        if not df.empty:
            # 2.1 Stacked Bar (Time Series)
            pivot = df.pivot_table(
                index="period_label",
                columns="reason_code",
                values="vis_value",
                aggfunc="sum",
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
                title=f"Verlauf {val_label} nach Ursache",
                xaxis_title="Zeitraum",
                yaxis_title=val_label,
                legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5)
            )
            charts["bar_abgaenge_reasons"] = fig_bar

            # 2.2 Total Bar (Aggregated)
            total_stats = df.groupby("reason_code")["vis_value"].sum().reset_index(name="sum_val")
            total_stats["reason_label"] = total_stats["reason_code"].map(REASON_LABELS)
            total_stats = total_stats.sort_values("sum_val", ascending=True)
            
            fig_total = go.Figure()
            fig_total.add_trace(go.Bar(
                x=total_stats["sum_val"],
                y=total_stats["reason_label"],
                orientation="h",
                text=total_stats["sum_val"].apply(lambda x: f"{x:,.1f}{unit}" if metric_type == "MAK" else f"{int(x)}"),
                textposition="auto",
                marker_color="#37536d"
            ))
            fig_total.update_layout(
                title=f"Gesamt-Verlust {val_label} nach Ursache",
                xaxis_title=val_label,
                yaxis_title=None,
                height=400,
                margin=dict(l=0, r=0, t=50, b=50)
            )
            charts["bar_reasons_total"] = fig_total

    # 3. Driver details charts (individual bars)
    if events_person_level is not None and not events_person_level.empty:
        df = events_person_level.copy()
        val_col = "headcount_change" if metric_type == "Köpfe" else "mak_change"
        val_label = "Anzahl (Köpfe)" if metric_type == "Köpfe" else "Kapazität (MAK)"
        
        for reason_code, label in REASON_LABELS.items():
            sub = df[df["reason_code"] == reason_code].copy()
            sub["vis_value"] = sub[val_col].abs()
            sub = sub[sub["vis_value"] > 0]
            
            if sub.empty:
                continue
            sums = sub.groupby("period_label")["vis_value"].sum().reset_index(name="sum_val")
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=sums["period_label"],
                y=sums["sum_val"],
                name=label,
                marker_color="#1f77b4"
            ))
            fig.update_layout(
                title=f"{label} ({val_label}) pro Periode", 
                xaxis_title="Zeitraum", 
                yaxis_title=val_label
            )
            charts[f"driver_{reason_code}"] = fig

    return charts
