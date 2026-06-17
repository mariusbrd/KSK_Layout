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
    "20-24": "#D6EEFF",  # heller Vorlauf (HSL ~205°, 92% L)
    "25-29": "#C2E6FF",  # heller Vorlauf (HSL ~205°, 88% L)
    "30-34": "#B3E0FF",  # = Vergütungsklassen Stufe 1
    "35-39": "#66C2FF",  # = Vergütungsklassen Stufe 2
    "40-44": "#33AAFF",  # = Vergütungsklassen Stufe 3
    "45-49": "#0088DE",  # = Vergütungsklassen Stufe 4
    "50-54": "#0066A8",  # = Vergütungsklassen Stufe 5
    "55-59": "#004471",  # = Vergütungsklassen Stufe 6
    "60-64": "#003052",  # dunkler Nachlauf (HSL ~205°, 16% L)
    "65-69": "#001E33",  # dunkler Nachlauf (HSL ~205°, 10% L)
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
