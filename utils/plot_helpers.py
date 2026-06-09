from __future__ import annotations

import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Age cohort color scale (blue-gray gradient: lighter = younger, darker = older)
# ---------------------------------------------------------------------------

AGE_COHORT_ORDER: list[str] = [
    "20-24", "25-29", "30-34", "35-39", "40-44",
    "45-49", "50-54", "55-59", "60-64", "65-69",
]

AGE_COHORT_COLOR_MAP: dict[str, str] = {
    "20-24": "#E8EEF5",
    "25-29": "#D6E2EF",
    "30-34": "#C3D5E8",
    "35-39": "#ABC5DD",
    "40-44": "#8EAFCF",
    "45-49": "#6F98BF",
    "50-54": "#527FAC",
    "55-59": "#396797",
    "60-64": "#254F7D",
    "65-69": "#153A5B",
}

UNKNOWN_AGE_COHORT_COLOR = "#B8C0CC"


def get_age_cohort_color_map(values: list[str]) -> dict[str, str]:
    """Return color_discrete_map for the given age cohort values.

    Known cohorts get the defined blue-gray color; unknown ones get the neutral fallback.
    """
    return {v: AGE_COHORT_COLOR_MAP.get(v, UNKNOWN_AGE_COHORT_COLOR) for v in values}


def apply_legend_bottom(fig: go.Figure) -> go.Figure:
    """
    Korrektur der Legendenposition für alle Charts.
    Setzt die Legende horizontal unter das Chart.
    
    Args:
        fig: Das Plotly-Figure-Objekt
        
    Returns:
        Das aktualisierte Figure-Objekt
    """
    fig.update_layout(
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.20,
            xanchor="center",
            x=0.5
        ),
        margin=dict(b=90)
    )
    return fig
