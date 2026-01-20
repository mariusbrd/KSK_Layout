"""
Chart-Factory für alle Plotly-Visualisierungen.

Konsistente, wiederverwendbare Chart-Funktionen.
"""

import plotly.graph_objects as go
import pandas as pd
from typing import Optional, List
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    COLORS, COLOR_SEQUENCE, COHORT_COLORS,
    CHART_HEIGHTS
)


def get_base_layout(title: str = "", show_legend: bool = True) -> dict:
    """
    Basis-Layout für alle Charts.

    Args:
        title: Chart-Titel
        show_legend: Ob Legende angezeigt werden soll

    Returns:
        Layout-Dictionary
    """
    return {
        "title": {
            "text": title,
            "font": {"size": 16, "color": COLORS["text_primary"]},
            "x": 0.5,
            "xanchor": "center"
        },
        "plot_bgcolor": "rgba(255,255,255,0)",
        "paper_bgcolor": "rgba(255,255,255,0)",
        "font": {"color": COLORS["text_primary"], "size": 12},
        "margin": dict(l=60, r=40, t=90, b=60),
        "hovermode": "closest",
        "showlegend": show_legend,
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "center",
            "x": 0.5,
            "bgcolor": "#FFFFFF",
            "bordercolor": "rgba(0,0,0,0)",
            "borderwidth": 0
        }
    }


def create_donut_chart(
    df: pd.DataFrame,
    values_col: str,
    names_col: str,
    title: str = "",
    colors: Optional[List[str]] = None,
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt ein Donut-Chart.

    Args:
        df: DataFrame
        values_col: Spalte mit Werten
        names_col: Spalte mit Labels
        title: Chart-Titel
        colors: Optional Liste von Farben
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    fig.add_trace(go.Pie(
        labels=df[names_col],
        values=df[values_col],
        hole=0.4,
        marker=dict(
            colors=colors if colors else COLOR_SEQUENCE,
            line=dict(color=COLORS["background"], width=2)
        ),
        textinfo="label+percent",
        textposition="auto",
        hovertemplate="<b>%{label}</b><br>%{value:,.0f}<br>%{percent}<extra></extra>",
        showlegend=False
    ))

    layout = get_base_layout(title, show_legend=False)
    layout["height"] = height
    fig.update_layout(**layout)

    return fig


def create_bar_chart(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    title: str = "",
    orientation: str = "v",
    color_col: Optional[str] = None,
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt ein Balkendiagramm.

    Args:
        df: DataFrame
        x_col: X-Achsen-Spalte
        y_col: Y-Achsen-Spalte
        title: Chart-Titel
        orientation: "v" (vertikal) oder "h" (horizontal)
        color_col: Optional Spalte für Farbkodierung
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    if color_col and color_col in df.columns:
        # Gruppierte Bars
        for i, category in enumerate(df[color_col].unique()):
            cat_data = df[df[color_col] == category]
            fig.add_trace(go.Bar(
                x=cat_data[x_col] if orientation == "v" else cat_data[y_col],
                y=cat_data[y_col] if orientation == "v" else cat_data[x_col],
                name=str(category),
                orientation=orientation,
                marker_color=COLOR_SEQUENCE[i % len(COLOR_SEQUENCE)],
                hovertemplate="<b>%{x}</b><br>%{y:,.2f}<extra></extra>"
            ))
        show_legend = True
    else:
        # Einfache Bars
        fig.add_trace(go.Bar(
            x=df[x_col] if orientation == "v" else df[y_col],
            y=df[y_col] if orientation == "v" else df[x_col],
            orientation=orientation,
            marker_color=COLORS["accent_teal"],
            hovertemplate="<b>%{x}</b><br>%{y:,.2f}<extra></extra>",
            showlegend=False
        ))
        show_legend = False

    layout = get_base_layout(title, show_legend=show_legend)
    layout["height"] = height
    layout["xaxis"] = {"gridcolor": COLORS["card_border"]}
    layout["yaxis"] = {"gridcolor": COLORS["card_border"]}

    if orientation == "h":
        layout["yaxis"]["autorange"] = "reversed"

    fig.update_layout(**layout)

    return fig


def create_line_chart(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    title: str = "",
    group_col: Optional[str] = None,
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt ein Liniendiagramm.

    Args:
        df: DataFrame
        x_col: X-Achsen-Spalte (meist Datum)
        y_col: Y-Achsen-Spalte
        title: Chart-Titel
        group_col: Optional Gruppierungsspalte (für mehrere Linien)
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    if group_col and group_col in df.columns:
        # Mehrere Linien
        for i, category in enumerate(df[group_col].unique()):
            cat_data = df[df[group_col] == category].sort_values(x_col)
            fig.add_trace(go.Scatter(
                x=cat_data[x_col],
                y=cat_data[y_col],
                name=str(category),
                mode="lines+markers",
                line=dict(
                    color=COLOR_SEQUENCE[i % len(COLOR_SEQUENCE)],
                    width=2
                ),
                marker=dict(size=6),
                hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{y:,.2f}<extra></extra>"
            ))
        show_legend = True
    else:
        # Einzelne Linie
        df_sorted = df.sort_values(x_col)
        fig.add_trace(go.Scatter(
            x=df_sorted[x_col],
            y=df_sorted[y_col],
            mode="lines+markers",
            line=dict(color=COLORS["accent_teal"], width=3),
            marker=dict(size=6),
            hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>",
            showlegend=False
        ))
        show_legend = False

    layout = get_base_layout(title, show_legend=show_legend)
    layout["height"] = height
    layout["xaxis"] = {"gridcolor": COLORS["card_border"]}
    layout["yaxis"] = {"gridcolor": COLORS["card_border"]}
    fig.update_layout(**layout)

    return fig


def create_stacked_area_chart(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    group_col: str,
    title: str = "",
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt ein gestapeltes Flächendiagramm.

    Args:
        df: DataFrame
        x_col: X-Achsen-Spalte (meist Datum)
        y_col: Y-Achsen-Spalte (Werte)
        group_col: Gruppierungsspalte (für Stapel)
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    # Pivot für Stacking
    pivot_df = df.pivot_table(
        index=x_col,
        columns=group_col,
        values=y_col,
        aggfunc="sum"
    ).fillna(0)

    # Kohorten-spezifische Farben wenn möglich
    for i, category in enumerate(pivot_df.columns):
        color = COHORT_COLORS.get(category, COLOR_SEQUENCE[i % len(COLOR_SEQUENCE)])

        fig.add_trace(go.Scatter(
            x=pivot_df.index,
            y=pivot_df[category],
            name=str(category),
            mode="lines",
            stackgroup="one",
            fillcolor=color,
            line=dict(color=color, width=0),
            hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>%{y:,.2f}<extra></extra>"
        ))

    layout = get_base_layout(title)
    layout["height"] = height
    layout["xaxis"] = {"gridcolor": COLORS["card_border"]}
    layout["yaxis"] = {"gridcolor": COLORS["card_border"]}
    fig.update_layout(**layout)

    return fig


def create_heatmap(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    value_col: str,
    title: str = "",
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt eine Heatmap.

    Args:
        df: DataFrame
        x_col: X-Achsen-Spalte
        y_col: Y-Achsen-Spalte
        value_col: Wertspalte
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    # Pivot für Heatmap
    pivot_df = df.pivot_table(
        index=y_col,
        columns=x_col,
        values=value_col,
        aggfunc="sum"
    ).fillna(0)

    fig = go.Figure(data=go.Heatmap(
        z=pivot_df.values,
        x=pivot_df.columns,
        y=pivot_df.index,
        colorscale="Teal",
        hovertemplate="<b>%{y}</b> × <b>%{x}</b><br>%{z:,.0f}<extra></extra>",
        colorbar=dict(
            title=dict(
                text="Anzahl",
                font=dict(color=COLORS["text_primary"])
            ),
            tickfont=dict(color=COLORS["text_primary"])
        )
    ))

    layout = get_base_layout(title)
    layout["height"] = height
    layout["xaxis"] = {"side": "bottom"}
    layout["yaxis"] = {"autorange": "reversed"}
    fig.update_layout(**layout)

    return fig


def create_population_pyramid(
    df: pd.DataFrame,
    age_col: str,
    gender_col: str,
    value_col: str,
    title: str = "Alterspyramide",
    height: int = CHART_HEIGHTS["large"]
) -> go.Figure:
    """
    Erstellt eine Bevölkerungspyramide.

    Args:
        df: DataFrame
        age_col: Altersspalte
        gender_col: Geschlechtsspalte
        value_col: Wertspalte (Anzahl oder FTE)
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    # Daten nach Geschlecht aufteilen
    male_data = df[df[gender_col] == "m"].groupby(age_col)[value_col].sum().sort_index()
    female_data = df[df[gender_col] == "w"].groupby(age_col)[value_col].sum().sort_index()

    # Männlich (links, negative Werte)
    fig.add_trace(go.Bar(
        y=male_data.index,
        x=-male_data.values,
        name="Männlich",
        orientation="h",
        marker_color=COLORS["gender_male"],
        hovertemplate="<b>Männlich</b><br>Alter: %{y}<br>Anzahl: %{x:,.0f}<extra></extra>"
    ))

    # Weiblich (rechts, positive Werte)
    fig.add_trace(go.Bar(
        y=female_data.index,
        x=female_data.values,
        name="Weiblich",
        orientation="h",
        marker_color=COLORS["gender_female"],
        hovertemplate="<b>Weiblich</b><br>Alter: %{y}<br>Anzahl: %{x:,.0f}<extra></extra>"
    ))

    layout = get_base_layout(title)
    layout["height"] = height
    layout["barmode"] = "overlay"
    layout["bargap"] = 0.1
    layout["xaxis"] = {
        "title": "Anzahl",
        "gridcolor": COLORS["card_border"]
    }
    layout["yaxis"] = {
        "title": "Alter",
        "gridcolor": COLORS["card_border"]
    }

    fig.update_layout(**layout)

    return fig


def create_gauge_chart(
    value: float,
    title: str = "",
    max_value: float = 1.0,
    thresholds: dict = None,
    height: int = CHART_HEIGHTS["small"]
) -> go.Figure:
    """
    Erstellt ein Gauge-Chart (Tacho).

    Args:
        value: Aktueller Wert (0-1 für Prozent)
        title: Chart-Titel
        max_value: Maximalwert
        thresholds: Dict mit Schwellenwerten {"good": 0.8, "warning": 0.6}
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    if thresholds is None:
        thresholds = {"good": 0.85, "warning": 0.75}

    # Farbe basierend auf Schwellenwerten
    if value >= thresholds["good"]:
        gauge_color = COLORS["status_good"]
    elif value >= thresholds["warning"]:
        gauge_color = COLORS["status_warning"]
    else:
        gauge_color = COLORS["status_critical"]

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=value * 100,  # Als Prozent
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": title, "font": {"color": COLORS["text_primary"]}},
        number={"suffix": "%", "font": {"size": 40}},
        gauge={
            "axis": {"range": [None, max_value * 100], "tickwidth": 1, "tickcolor": COLORS["text_secondary"]},
            "bar": {"color": gauge_color},
            "bgcolor": COLORS["card_bg"],
            "borderwidth": 2,
            "bordercolor": COLORS["card_border"],
            "steps": [
                {"range": [0, thresholds["warning"] * 100], "color": f"{COLORS['status_critical']}30"},
                {"range": [thresholds["warning"] * 100, thresholds["good"] * 100], "color": f"{COLORS['status_warning']}30"},
                {"range": [thresholds["good"] * 100, max_value * 100], "color": f"{COLORS['status_good']}30"}
            ]
        }
    ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font={"color": COLORS["text_primary"]},
        height=height
    )

    return fig


def create_funnel_chart(
    stages: List[str],
    values: List[float],
    title: str = "",
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt ein Funnel-Chart.

    Args:
        stages: Liste der Stufen-Namen
        values: Liste der Werte pro Stufe
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure(go.Funnel(
        y=stages,
        x=values,
        textposition="inside",
        textinfo="value+percent initial",
        marker=dict(
            color=COLOR_SEQUENCE[:len(stages)],
            line=dict(color=COLORS["background"], width=2)
        ),
        hovertemplate="<b>%{y}</b><br>%{x:,.0f}<br>%{percentInitial}<extra></extra>"
    ))

    layout = get_base_layout(title)
    layout["height"] = height
    layout["showlegend"] = False
    fig.update_layout(**layout)

    return fig


def create_gantt_chart(
    df: pd.DataFrame,
    start_col: str,
    end_col: str,
    task_col: str,
    color_col: Optional[str] = None,
    title: str = "",
    height: int = CHART_HEIGHTS["large"]
) -> go.Figure:
    """
    Erstellt ein Gantt-Chart (Timeline).

    Args:
        df: DataFrame
        start_col: Spalte mit Startdatum
        end_col: Spalte mit Enddatum
        task_col: Spalte mit Task-Namen
        color_col: Optional Spalte für Farbkodierung
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    if color_col and color_col in df.columns:
        for i, category in enumerate(df[color_col].unique()):
            cat_data = df[df[color_col] == category]
            for _, row in cat_data.iterrows():
                # Konvertiere Start und Ende zu Timestamps
                start_ts = pd.to_datetime(row[start_col])
                end_ts = pd.to_datetime(row[end_col])

                fig.add_trace(go.Bar(
                    x=[end_ts],
                    y=[row[task_col]],
                    base=start_ts,
                    orientation="h",
                    marker_color=COLOR_SEQUENCE[i % len(COLOR_SEQUENCE)],
                    name=str(category),
                    showlegend=False if row.name > cat_data.index[0] else True,
                    hovertemplate=f"<b>{row[task_col]}</b><br>Start: {row[start_col]}<br>Ende: {row[end_col]}<extra></extra>"
                ))
    else:
        for _, row in df.iterrows():
            # Konvertiere Start und Ende zu Timestamps
            start_ts = pd.to_datetime(row[start_col])
            end_ts = pd.to_datetime(row[end_col])

            fig.add_trace(go.Bar(
                x=[end_ts],
                y=[row[task_col]],
                base=start_ts,
                orientation="h",
                marker_color=COLORS["accent_teal"],
                showlegend=False,
                hovertemplate=f"<b>{row[task_col]}</b><br>Start: {row[start_col]}<br>Ende: {row[end_col]}<extra></extra>"
            ))

    layout = get_base_layout(title)
    layout["height"] = height
    layout["xaxis"] = {"type": "date", "gridcolor": COLORS["card_border"]}
    layout["yaxis"] = {"autorange": "reversed", "gridcolor": COLORS["card_border"]}
    layout["barmode"] = "overlay"
    fig.update_layout(**layout)

    return fig


def create_sunburst(
    df: pd.DataFrame,
    path_cols: List[str],
    value_col: str,
    color_col: Optional[str] = None,
    title: str = "",
    height: int = CHART_HEIGHTS["large"]
) -> go.Figure:
    """
    Erstellt ein Sunburst-Chart für hierarchische Daten.

    Args:
        df: DataFrame
        path_cols: Liste der Hierarchie-Spalten (z.B. ["Bereich", "Abteilung", "Team"])
        value_col: Wertspalte (z.B. FTE, Kosten)
        color_col: Optional Spalte für Farbwerte
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure(go.Sunburst(
        labels=df[path_cols[-1]] if len(path_cols) == 1 else None,
        parents=df[path_cols[-2]] if len(path_cols) > 1 else [""] * len(df),
        values=df[value_col],
        branchvalues="total",
        marker=dict(
            colorscale="Teal",
            cmid=0.5,
            line=dict(color=COLORS["background"], width=2)
        ),
        hovertemplate="<b>%{label}</b><br>%{value:,.1f}<br>%{percentParent}<extra></extra>"
    ))

    layout = get_base_layout(title)
    layout["height"] = height
    fig.update_layout(**layout)

    return fig


def create_waterfall(
    categories: List[str],
    values: List[float],
    title: str = "",
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt ein Waterfall-Chart.

    Args:
        categories: Liste der Kategorie-Namen
        values: Liste der Werte (positiv/negativ)
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    # Bestimme measure types
    measures = []
    for i, cat in enumerate(categories):
        if i == 0:
            measures.append("absolute")
        elif i == len(categories) - 1:
            measures.append("total")
        else:
            measures.append("relative")

    fig = go.Figure(go.Waterfall(
        x=categories,
        y=values,
        measure=measures,
        text=[f"{v:+.1f}" if v != 0 else "" for v in values],
        textposition="outside",
        connector=dict(line=dict(color=COLORS["card_border"], width=2)),
        increasing=dict(marker=dict(color=COLORS["status_good"])),
        decreasing=dict(marker=dict(color=COLORS["status_critical"])),
        totals=dict(marker=dict(color=COLORS["accent_teal"])),
        hovertemplate="<b>%{x}</b><br>%{y:,.1f}<extra></extra>"
    ))

    layout = get_base_layout(title)
    layout["height"] = height
    layout["xaxis"] = {"gridcolor": COLORS["card_border"]}
    layout["yaxis"] = {"gridcolor": COLORS["card_border"], "title": "FTE"}
    fig.update_layout(**layout)

    return fig


def create_diverging_bar(
    df: pd.DataFrame,
    category_col: str,
    value_col: str,
    title: str = "",
    height: int = CHART_HEIGHTS["medium"]
) -> go.Figure:
    """
    Erstellt ein divergierendes Balkendiagramm (Lollipop/Diverging Bar).

    Args:
        df: DataFrame
        category_col: Kategorie-Spalte
        value_col: Wertspalte (Varianzen, positiv/negativ)
        title: Chart-Titel
        height: Chart-Höhe

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    # Sortiere nach Wert
    df_sorted = df.sort_values(value_col)

    # Farben basierend auf pos/neg
    colors = [COLORS["status_good"] if v >= 0 else COLORS["status_critical"]
              for v in df_sorted[value_col]]

    fig.add_trace(go.Bar(
        y=df_sorted[category_col],
        x=df_sorted[value_col],
        orientation="h",
        marker=dict(color=colors),
        text=df_sorted[value_col].apply(lambda x: f"{x:+.1f}"),
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Varianz: %{x:+.1f}<extra></extra>"
    ))

    layout = get_base_layout(title)
    layout["height"] = height
    layout["xaxis"] = {
        "gridcolor": COLORS["card_border"],
        "title": "Varianz (FTE)",
        "zeroline": True,
        "zerolinecolor": COLORS["text_secondary"],
        "zerolinewidth": 2
    }
    layout["yaxis"] = {"gridcolor": COLORS["card_border"], "autorange": "reversed"}
    layout["showlegend"] = False
    fig.update_layout(**layout)

    return fig
