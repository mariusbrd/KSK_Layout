"""Shared colors and layout for observed versus expected yearly bars."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st


OBSERVED_COLORS = ("#1266C3", "#00A087", "#9B59B6", "#E67E22", "#D64550", "#527A9E")
EXPECTED_COLOR = "#9CA3AF"


def comparison_color_map(series: pd.Series) -> dict[str, str]:
    labels = series.dropna().astype(str).unique().tolist()
    observed = sorted(label for label in labels if label.endswith(" · beobachtet"))
    colors = {label: OBSERVED_COLORS[index % len(OBSERVED_COLORS)]
              for index, label in enumerate(observed)}
    colors.update({label: EXPECTED_COLOR for label in labels if label.endswith(" · erwartet")})
    return colors


def render_comparison_bar_chart(data: pd.DataFrame) -> None:
    colors = comparison_color_map(data["Serie"])
    order = sorted(colors, key=lambda label: (label.rsplit(" · ", 1)[0], label.endswith(" · beobachtet")))
    chart = (alt.Chart(data).mark_bar()
             .encode(x=alt.X("Jahr:O", title="Jahr"),
                     xOffset=alt.XOffset("Serie:N", sort=order),
                     y=alt.Y("Anzahl:Q", title="Anzahl"),
                     color=alt.Color("Serie:N", scale=alt.Scale(domain=list(colors), range=list(colors.values())),
                                     legend=alt.Legend(title=None)),
                     tooltip=[alt.Tooltip("Jahr:O"), alt.Tooltip("Serie:N"),
                              alt.Tooltip("Anzahl:Q", format=".2f")])
             .properties(height=360))
    st.altair_chart(chart, use_container_width=True)
