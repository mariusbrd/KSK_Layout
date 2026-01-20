"""
MAK/Euro Toggle-Komponente.

Globaler Toggle der alle Charts und KPIs beeinflusst.
"""

import streamlit as st


def render_view_mode_toggle():
    """
    Rendert den MAK/Euro Toggle und aktualisiert Session State.

    Der Toggle sollte an prominenter Stelle platziert werden (z.B. im Header).
    """
    # Aktueller Modus
    current_mode = st.session_state.get("view_mode", "MAK")

    # Toggle mit Radio Buttons (horizontal)
    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        view_mode = st.radio(
            "Ansichtsmodus",
            options=["MAK", "Euro"],
            index=0 if current_mode == "MAK" else 1,
            horizontal=True,
            key="view_mode_radio",
            label_visibility="collapsed"
        )

        # Update session state
        st.session_state["view_mode"] = view_mode

    return view_mode


def get_view_mode() -> str:
    """
    Gibt den aktuellen View-Mode zurück.

    Returns:
        "MAK" oder "Euro"
    """
    return st.session_state.get("view_mode", "MAK")


def format_value(value: float, mode: str = None) -> str:
    """
    Formatiert einen Wert basierend auf dem View-Mode.

    Args:
        value: Numerischer Wert
        mode: "MAK" oder "Euro" (optional, nutzt session_state wenn None)

    Returns:
        Formatierter String
    """
    if mode is None:
        mode = get_view_mode()

    if mode == "MAK":
        # MAK (FTE) mit 2 Dezimalstellen
        return f"{value:.2f}"
    else:
        # Euro mit Tausender-Trennung
        if value >= 1_000_000:
            return f"€ {value/1_000_000:.1f} Mio."
        elif value >= 1_000:
            return f"€ {value/1_000:.0f}k"
        else:
            return f"€ {value:,.0f}".replace(",", ".")


def get_value_for_mode(row: dict, mode: str = None) -> float:
    """
    Extrahiert den richtigen Wert aus einer Zeile basierend auf dem Mode.

    Args:
        row: DataFrame-Zeile oder Dictionary mit 'FTE_assigned' und 'Total_Cost_Year'
        mode: "MAK" oder "Euro" (optional)

    Returns:
        Numerischer Wert
    """
    if mode is None:
        mode = get_view_mode()

    if mode == "MAK":
        return row.get("FTE_assigned", 0)
    else:
        return row.get("Total_Cost_Year", 0)
