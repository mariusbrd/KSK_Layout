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
    # Prüfe Datenquelle
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    original_dir = os.path.join(base_dir, "..", "Original-Daten")
    mitarbeiter_path = os.path.join(original_dir, "Mitarbeiter.xlsx")
    synthetic_marker = os.path.join(original_dir, ".is_synthetic")

    # 1. Uploads (Session)
    has_uploads = len(st.session_state.get("global_uploads", {})) > 0
    
    # 2. Synthetic Marker
    is_synthetic = os.path.exists(synthetic_marker)
    
    # 3. Original Data Exists
    has_original_files = os.path.exists(mitarbeiter_path)

    
    if has_uploads:
        bg_color = "#e0f2fe"  # Hellblau
        border_color = "#0ea5e9"  # Blau
        icon = "📂"
        title = "Eigene Daten (Upload)"
        description = "Es werden temporär hochgeladene Daten verwendet"
        return True # Considered valid
        
    elif is_synthetic:
        bg_color = "#fef3c7"  # Hellgelb
        border_color = "#f59e0b"  # Orange
        icon = "🧪"
        title = "Synthetische Testdaten"
        description = "Generierte Musterdaten (Mitarbeiter.xlsx vorhanden)"
        return False
        
    elif has_original_files:
        bg_color = "#d1fae5"  # Hellgrün
        border_color = "#10b981"  # Grün
        icon = "🔐"
        title = "Original-Daten"
        description = "Es werden die echten HR-Daten verwendet"
        return True
        
    else:
        bg_color = "#fee2e2"  # Rot
        border_color = "#ef4444"  # Rot
        icon = "❌"
        title = "Keine Daten"
        description = "Bitte Daten in 'Original-Daten' ablegen"
        return False
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
    # Prüfe Datenquelle
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    original_dir = os.path.join(base_dir, "..", "Original-Daten")
    mitarbeiter_path = os.path.join(original_dir, "Mitarbeiter.xlsx")
    synthetic_marker = os.path.join(original_dir, ".is_synthetic")

    has_uploads = len(st.session_state.get("global_uploads", {})) > 0
    is_synthetic = os.path.exists(synthetic_marker)
    has_original_files = os.path.exists(mitarbeiter_path)

    if has_uploads:
        st.sidebar.info("📂 Eigene Daten aktiv", icon="ℹ️")
        return True
    elif is_synthetic:
        st.sidebar.warning("🧪 Testdaten aktiv", icon="⚠️")
        return False
    elif has_original_files:
        st.sidebar.success("🔐 Original-Daten aktiv", icon="✅")
        return True
    
    st.sidebar.error("❌ Keine Daten", icon="🚨")
    return False
