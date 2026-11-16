"""Readable Monte Carlo detail for one OE or OE cluster across annual slices."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def build_annual_band_figure(bands: pd.DataFrame, *, group_col: str,
                             group: str, x_col: str, unit: str) -> go.Figure:
    """Show the outer and inner empirical intervals without changing the mean."""
    selected = bands.loc[bands[group_col].astype(str).eq(group)].sort_values(x_col)
    if selected.empty:
        raise ValueError("Für diese Organisation liegen keine Jahresscheiben vor.")
    x = selected[x_col].tolist()
    low = selected["lower"].astype(float).tolist()
    high = selected["upper"].astype(float).tolist()
    mean = selected["mean"].astype(float).tolist()
    p25 = selected["p25"].astype(float).tolist()
    median = selected["median"].astype(float).tolist()
    p75 = selected["p75"].astype(float).tolist()
    reference = selected["Referenzlauf"].astype(float).tolist()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=high, mode="lines", line=dict(color="rgba(107,114,128,0.45)", width=1),
        name="P90", hovertemplate="%{x}<br>P90: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=low, mode="lines", fill="tonexty",
        fillcolor="rgba(107,114,128,0.14)",
        line=dict(color="rgba(107,114,128,0.45)", width=1),
        name="P10–P90", hovertemplate="%{x}<br>P10: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=p75, mode="lines", line=dict(color="rgba(75,85,99,0.6)", width=1),
        name="P75", hovertemplate="%{x}<br>P75: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=p25, mode="lines", fill="tonexty",
        fillcolor="rgba(75,85,99,0.3)",
        line=dict(color="rgba(75,85,99,0.6)", width=1),
        name="P25–P75", hovertemplate="%{x}<br>P25: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=median, mode="lines+markers", line=dict(color="#374151", width=2),
        marker=dict(size=6), name="Median (P50)",
        hovertemplate="%{x}<br>Median: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=mean, mode="lines+markers", line=dict(color="#1f77b4", width=3),
        marker=dict(size=8), name="Mittelwert",
        hovertemplate="%{x}<br>Mittelwert: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=reference, mode="lines+markers", line=dict(color="#9ca3af", dash="dash", width=2),
        marker=dict(size=6), name="Referenzlauf",
        hovertemplate="%{x}<br>Referenzlauf: %{y:,.2f}<extra></extra>"))
    if len(x) == 1:
        fig.add_trace(go.Scatter(
            x=[x[0], x[0]], y=[low[0], high[0]], mode="lines+markers",
            line=dict(color="#6b7280", width=5), marker=dict(size=8),
            name="P10–P90 am Stichtag", showlegend=False))
    fig.update_layout(
        height=340, margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="Jahr" if x_col == "Jahr" else "Stichtag", yaxis_title=unit,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.2))
    if x_col == "Jahr":
        fig.update_xaxes(tickmode="array", tickvals=x)
    return fig


def build_annual_box_figure(bands: pd.DataFrame, *, group_col: str,
                            group: str, x_col: str, x_value, unit: str) -> go.Figure:
    """Render actual saved run values for one year, including individual points."""
    samples = bands.attrs.get("run_samples", {}).get((x_value, group))
    if not samples:
        raise ValueError("Für diese Jahresscheibe sind keine Einzelwerte gespeichert.")
    fig = go.Figure(go.Box(
        y=samples, name=str(x_value), boxpoints="all", jitter=.3, pointpos=0,
        marker=dict(color="#6b7280", size=6), line=dict(color="#374151"),
        fillcolor="rgba(107,114,128,0.24)", boxmean=True, quartilemethod="linear",
        hovertemplate="Laufwert: %{y:,.2f}<extra></extra>"))
    fig.update_layout(
        height=340, margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="Jahr" if x_col == "Jahr" else "Stichtag", yaxis_title=unit,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    return fig


def render_annual_band_detail(bands: pd.DataFrame, *, group_col: str,
                              x_col: str, unit: str, key: str) -> None:
    """Show every selected year's interval for one chosen organization."""
    if bands.empty:
        return
    with st.expander("Jahresscheiben je OE / OE-Cluster mit Band", expanded=True):
        groups = sorted(bands[group_col].astype(str).unique())
        group = st.selectbox("OE / OE-Cluster auswählen", groups, key=key)
        mode = st.radio("Darstellung", ["Jahresverlauf", "Verteilung je Jahr"],
                        horizontal=True, key=f"{key}_mode")
        if mode == "Verteilung je Jahr":
            periods = sorted(bands.loc[bands[group_col].astype(str).eq(group), x_col].unique())
            period = st.selectbox("Jahresscheibe auswählen", periods, index=len(periods) - 1,
                                  key=f"{key}_period")
            fig = build_annual_box_figure(
                bands, group_col=group_col, group=group, x_col=x_col,
                x_value=period, unit=unit)
            st.caption("Box: P25–P75 und Median; Punkte: einzelne Simulationsläufe. Bei wenigen Läufen sind Quantile nur grobe Orientierung.")
        else:
            fig = build_annual_band_figure(
                bands, group_col=group_col, group=group, x_col=x_col, unit=unit)
            st.caption("Hellgrau: P10–P90. Dunkelgrau: P25–P75. Linien: Median, Mittelwert und Referenzlauf.")
        st.plotly_chart(fig, use_container_width=True, key=f"{key}_chart")
        display = bands.loc[bands[group_col].astype(str).eq(group)].rename(
            columns={"mean": "Mittelwert", "lower": "P10", "upper": "P90",
                     "p25": "P25", "median": "Median (P50)", "p75": "P75"})
        st.dataframe(display, hide_index=True, use_container_width=True)
