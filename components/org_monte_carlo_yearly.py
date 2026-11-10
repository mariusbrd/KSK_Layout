"""Readable Monte Carlo detail for one OE or OE cluster across annual slices."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def build_annual_band_figure(bands: pd.DataFrame, *, group_col: str,
                             group: str, x_col: str, unit: str) -> go.Figure:
    """Keep the concrete reference visible beneath the gray per-year band."""
    selected = bands.loc[bands[group_col].astype(str).eq(group)].sort_values(x_col)
    if selected.empty:
        raise ValueError("Für diese Organisation liegen keine Jahresscheiben vor.")
    x = selected[x_col].tolist()
    low = selected["lower"].astype(float).tolist()
    high = selected["upper"].astype(float).tolist()
    mean = selected["mean"].astype(float).tolist()
    reference = selected["Referenzlauf"].astype(float).tolist()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=high, mode="lines", line=dict(color="rgba(107,114,128,0.45)", width=1),
        name="P90", hovertemplate="%{x}<br>P90: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=low, mode="lines", fill="tonexty",
        fillcolor="rgba(107,114,128,0.22)",
        line=dict(color="rgba(107,114,128,0.45)", width=1),
        name="P10–P90", hovertemplate="%{x}<br>P10: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=mean, mode="lines+markers", line=dict(color="#6b7280", dash="dash", width=2),
        marker=dict(size=7), name="Mittelwert",
        hovertemplate="%{x}<br>Mittelwert: %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=x, y=reference, mode="lines+markers", line=dict(color="#1f77b4", width=3),
        marker=dict(size=8), name="Referenzlauf",
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


def render_annual_band_detail(bands: pd.DataFrame, *, group_col: str,
                              x_col: str, unit: str, key: str) -> None:
    """Show every selected year's interval for one chosen organization."""
    if bands.empty:
        return
    with st.expander("Jahresscheiben je OE / OE-Cluster mit Band", expanded=True):
        groups = sorted(bands[group_col].astype(str).unique())
        group = st.selectbox("OE / OE-Cluster auswählen", groups, key=key)
        fig = build_annual_band_figure(
            bands, group_col=group_col, group=group, x_col=x_col, unit=unit)
        st.plotly_chart(fig, use_container_width=True, key=f"{key}_chart")
        display = bands.loc[bands[group_col].astype(str).eq(group)].rename(
            columns={"mean": "Mittelwert", "lower": "P10", "upper": "P90"})
        st.dataframe(display, hide_index=True, use_container_width=True)
