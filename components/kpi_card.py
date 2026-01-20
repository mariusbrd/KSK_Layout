"""
Wiederverwendbare KPI-Card Komponente.

Styled KPI-Karten mit Wert, Trend und optionalem Sparkline.
"""

import streamlit as st
from typing import Optional, List, Tuple
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import COLORS


def kpi_card(
    title: str,
    value: str,
    subtitle: str = "",
    trend: Optional[Tuple[float, str]] = None,
    sparkline_data: Optional[List[float]] = None,
    status: Optional[str] = None,
    icon: str = ""
):
    """
    Erstellt eine gestylte KPI-Card mit Streamlit Container.

    Args:
        title: Überschrift der Card
        value: Hauptwert (bereits formatiert)
        subtitle: Zusatzinformation unter dem Wert
        trend: Optional (Wert, Label) z.B. (5.2, "vs VJ")
        sparkline_data: Optional Liste von Werten für Mini-Chart
        status: Optional "good", "warning", "critical" für Farbkodierung
        icon: Optional Emoji/Icon
    """
    # Status-Farbe bestimmen
    if status == "good":
        border_color = COLORS["status_good"]
        bg_color = COLORS["card_bg"]
    elif status == "warning":
        border_color = COLORS["status_warning"]
        bg_color = COLORS["card_bg"]
    elif status == "critical":
        border_color = COLORS["status_critical"]
        bg_color = COLORS["card_bg"]
    else:
        border_color = COLORS["card_border"]
        bg_color = COLORS["card_bg"]

    # CSS für Container
    container_style = f"""
        background: {bg_color};
        border-left: 4px solid {border_color};
        padding: 1.5rem;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        height: 100%;
    """

    # Verwende Streamlit Container statt rohem HTML
    with st.container():
        st.markdown(f"""
        <div style="{container_style}">
            <div style="color: {COLORS["text_secondary"]}; font-size: 0.85rem; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 0.5rem;">
                {icon} {title}
            </div>
            <div style="color: {COLORS["text_primary"]}; font-size: 2rem; font-weight: 700; margin: 0.5rem 0;">
                {value}
            </div>
            <div style="color: {COLORS["text_secondary"]}; font-size: 0.85rem;">
                {subtitle}
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Trend separat als Streamlit Metric
        if trend:
            trend_value, trend_label = trend
            delta_color = "normal" if trend_value > 0 else "inverse"
            st.caption(f"{trend_value:+.1f}% {trend_label}")


def create_metric_card(
    label: str,
    value: float,
    format_func=None,
    delta: Optional[float] = None,
    delta_label: str = "vs VJ",
    status: Optional[str] = None,
    icon: str = ""
):
    """
    Vereinfachte Wrapper-Funktion für schnelle KPI-Card-Erstellung.

    Args:
        label: KPI-Label
        value: Numerischer Wert
        format_func: Funktion zum Formatieren des Werts
        delta: Optional Veränderungswert (in %)
        delta_label: Label für Delta
        status: "good", "warning", "critical"
        icon: Emoji/Icon
    """
    # Standardformatierung
    if format_func is None:
        if value >= 1000:
            formatted_value = f"{value:,.0f}".replace(",", ".")
        else:
            formatted_value = f"{value:.1f}"
    else:
        formatted_value = format_func(value)

    # Trend
    trend = (delta, delta_label) if delta is not None else None

    # Subtitle
    subtitle = ""

    kpi_card(
        title=label,
        value=formatted_value,
        subtitle=subtitle,
        trend=trend,
        status=status,
        icon=icon
    )


def kpi_row(kpis: List[dict]):
    """
    Erstellt eine Zeile mit mehreren KPI-Cards.

    Args:
        kpis: Liste von Dictionaries mit KPI-Parametern
              [{"title": "...", "value": "...", ...}, ...]
    """
    cols = st.columns(len(kpis))

    for col, kpi_params in zip(cols, kpis):
        with col:
            kpi_card(**kpi_params)
