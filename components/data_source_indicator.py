"""
Data Source Indicator Component.

Zeigt an, welche Datenquelle (Original oder Synthetisch) verwendet wird.
"""

import streamlit as st
import os


def show_data_source_indicator():
    """
    Zeigt einen dezenten Indikator für die verwendete Datenquelle.

    Prüft ob Original-Daten existieren und zeigt entsprechenden Status.
    """
    # Prüfe ob Original-Daten existieren
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    original_dir = os.path.join(base_dir, "..", "Original-Daten")
    mitarbeiter_path = os.path.join(original_dir, "Mitarbeiter.xlsx")

    is_original = os.path.exists(mitarbeiter_path)

    # CSS für den Indikator
    if is_original:
        bg_color = "#d1fae5"  # Hellgrün
        border_color = "#10b981"  # Grün
        icon = "🔐"
        title = "Original-Daten"
        description = "Es werden die echten HR-Daten verwendet"
    else:
        bg_color = "#fef3c7"  # Hellgelb
        border_color = "#f59e0b"  # Orange
        icon = "🧪"
        title = "Synthetische Testdaten"
        description = "Es werden generierte Testdaten verwendet"

    # Render Indikator
    st.markdown(
        f"""
        <div style='
            background-color: {bg_color};
            border-left: 4px solid {border_color};
            padding: 0.75rem 1rem;
            border-radius: 0.375rem;
            margin-bottom: 1rem;
            font-size: 0.875rem;
        '>
            <div style='display: flex; align-items: center; gap: 0.5rem;'>
                <span style='font-size: 1.25rem;'>{icon}</span>
                <div>
                    <strong>{title}</strong> · {description}
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    return is_original


def show_data_source_badge():
    """
    Zeigt einen kleinen Badge in der Sidebar für die Datenquelle.
    """
    # Prüfe ob Original-Daten existieren
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    original_dir = os.path.join(base_dir, "..", "Original-Daten")
    mitarbeiter_path = os.path.join(original_dir, "Mitarbeiter.xlsx")

    is_original = os.path.exists(mitarbeiter_path)

    if is_original:
        st.sidebar.success("🔐 Original-Daten aktiv", icon="✅")
    else:
        st.sidebar.warning("🧪 Testdaten aktiv", icon="⚠️")

    return is_original
