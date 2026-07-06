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


# ---------------------------------------------------------------------------
# Tariff group color scale — dasselbe Farbschema wie die Alterskohorten
# (identische 10 Verlaufsfarben aus AGE_COHORT_COLOR_MAP, entlang der
# Entgeltgruppen-Reihenfolge abgetastet: lighter = niedrige Entgeltgruppe,
# dunkler = hohe Entgeltgruppe)
# ---------------------------------------------------------------------------


def _interpolate_hex(color_a: str, color_b: str, t: float) -> str:
    """Linear RGB interpolation between two hex colors, t in [0, 1]."""
    a = color_a.lstrip("#")
    b = color_b.lstrip("#")
    r1, g1, b1 = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    r2, g2, b2 = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    bl = round(b1 + (b2 - b1) * t)
    return f"#{r:02X}{g:02X}{bl:02X}"


def _sample_gradient(anchors: list[str], t: float) -> str:
    """Tastet einen mehrstufigen Verlauf (Liste von Hex-Ankerfarben) an Position t in [0, 1] ab.

    Bei genau len(anchors) Kategorien entstehen exakt die Ankerfarben selbst; bei mehr oder
    weniger Kategorien wird zwischen den beiden naechstliegenden Ankern linear interpoliert.
    So bekommt eine beliebige Kategorienanzahl dieselbe Kurvenform wie die 10-stufige
    Alterskohorten-Palette, statt nur deren erste/letzte Farbe zu nutzen.
    """
    if len(anchors) == 1:
        return anchors[0]
    t = min(max(t, 0.0), 1.0)
    scaled = t * (len(anchors) - 1)
    idx = min(int(scaled), len(anchors) - 2)
    local_t = scaled - idx
    return _interpolate_hex(anchors[idx], anchors[idx + 1], local_t)


def get_tariff_group_color_map(values: list[str]) -> dict[str, str]:
    """Return color_discrete_map for the given Tarifgruppe (Entgeltgruppe) values.

    Nutzt dasselbe Farbschema wie get_age_cohort_color_map: dieselben 10 Verlaufsfarben aus
    AGE_COHORT_COLOR_MAP, abgetastet entlang von config.settings.TARIFF_GROUPS (niedrig -> hoch).
    Werte, die nicht in TARIFF_GROUPS vorkommen (z. B. andere Vergütungssysteme,
    Datenpflegefaelle), erhalten dieselbe neutrale Grau-Fallback-Farbe wie unbekannte Alterskohorten.
    """
    from config.settings import TARIFF_GROUPS

    anchors = list(AGE_COHORT_COLOR_MAP.values())
    known_order = [g for g in TARIFF_GROUPS if g in values]
    n = len(known_order)
    color_map = {
        g: _sample_gradient(anchors, i / (n - 1) if n > 1 else 0.0)
        for i, g in enumerate(known_order)
    }
    for v in values:
        if v not in color_map:
            color_map[v] = UNKNOWN_AGE_COHORT_COLOR
    return color_map


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
