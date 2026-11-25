"""Three chart views of the same stored annual run values."""
from __future__ import annotations

import plotly.graph_objects as go


CHART_TYPES = ("Linien", "Boxplots je Jahr", "Punktewolke")
COLORS = {
    "Geringste Fluktuation": "#16a34a",
    "Höchste Fluktuation": "#dc2626",
    "Median-naher Bestandsverlauf": "#2563eb",
    "Typischer Verlauf (bestehende OE-Auswahl)": "#9333ea",
}


def build_profiler_figure(table, highlights: dict[str, int], chart_type: str,
                          metric_label: str) -> go.Figure:
    if chart_type not in CHART_TYPES:
        raise ValueError("Unbekannte Grafikart")
    dates = sorted(table["Datum"].unique())
    date_positions = {date: position for position, date in enumerate(dates)}
    count = int(table["Laufindex"].nunique())
    fig = go.Figure()
    if chart_type == "Boxplots je Jahr":
        for date in dates:
            rows = table.loc[table["Datum"].eq(date)]
            fig.add_trace(go.Box(
                x=[date] * len(rows), y=rows["Wert"], name=date,
                marker=dict(color="rgba(107,114,128,0.45)", size=4),
                line=dict(color="#6b7280"), fillcolor="rgba(107,114,128,0.12)",
                boxpoints="all", jitter=0.3, pointpos=0, showlegend=False,
                customdata=rows["Laufindex"].to_numpy() + 1,
                hovertemplate="Lauf %{customdata}<br>%{x}: %{y:,.2f}<extra></extra>",
            ))
    else:
        for index, rows in table.groupby("Laufindex", sort=True):
            index = int(index)
            if index in highlights.values():
                continue
            x = ([date_positions[date] + ((index * 37 % count) / max(count - 1, 1) - .5) * .55
                  for date in rows["Datum"]] if chart_type == "Punktewolke"
                 else rows["Datum"].tolist())
            fig.add_trace(go.Scatter(
                x=x, y=rows["Wert"], mode="markers" if chart_type == "Punktewolke" else "lines",
                line=dict(color="rgba(107,114,128,0.20)", width=1),
                marker=dict(color="rgba(107,114,128,0.35)", size=5),
                name="Übrige Läufe", showlegend=False,
                customdata=rows["Datum"].tolist(),
                hovertemplate=f"Lauf {index + 1}<br>%{{customdata}}: %{{y:,.2f}}<extra></extra>",
            ))
    first_label_by_index = {}
    for label, index in highlights.items():
        first_label_by_index.setdefault(index, label)
    for index, first_label in first_label_by_index.items():
        rows = table.loc[table["Laufindex"].eq(index)]
        label = " / ".join(label for label, selected in highlights.items() if selected == index)
        x = ([date_positions[date] for date in rows["Datum"]]
             if chart_type == "Punktewolke" else rows["Datum"].tolist())
        fig.add_trace(go.Scatter(
            x=x, y=rows["Wert"],
            mode="markers" if chart_type == "Punktewolke" else "lines+markers",
            line=dict(color=COLORS[first_label], width=3),
            marker=dict(color=COLORS[first_label], size=9),
            opacity=1 if chart_type == "Linien" else 0.45,
            name=f"{label} · Lauf {index + 1}",
            customdata=rows["Datum"].tolist(),
            hovertemplate=f"{label} · Lauf {index + 1}<br>%{{customdata}}: %{{y:,.2f}}<extra></extra>",
        ))
    if chart_type == "Punktewolke":
        fig.update_xaxes(tickmode="array", tickvals=list(range(len(dates))), ticktext=dates,
                         range=[-.5, len(dates) - .5])
    fig.update_layout(height=620, xaxis_title="Jahresstichtag",
                      yaxis_title=f"Verbleibender heutiger Bestand ({metric_label})",
                      hovermode="closest", legend=dict(orientation="h", y=-0.2))
    return fig
