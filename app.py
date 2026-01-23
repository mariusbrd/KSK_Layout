"""
HR Pulse Dashboard - Haupteinstiegspunkt

Streamlit-basiertes HR-Analytics-Dashboard für eine süddeutsche Sparkasse/Bank.
"""

import streamlit as st
from config.settings import PAGE_CONFIG, DEFAULT_COHORTS

# =============================================================================
# PAGE CONFIGURATION
# =============================================================================

st.set_page_config(**PAGE_CONFIG)

# =============================================================================
# SESSION STATE INITIALIZATION
# =============================================================================

def initialize_session_state():
    """Initialisiert Session State mit Defaults."""

    # Kohorten-Definitionen
    if "cohort_definitions" not in st.session_state:
        st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()

    # View Mode (MAK oder Euro)
    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "MAK"

    # Filter-Defaults
    if "selected_genders" not in st.session_state:
        st.session_state["selected_genders"] = ["m", "w"]

    if "selected_employment" not in st.session_state:
        st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit"]

    if "selected_atz_status" not in st.session_state:
        st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]

    # Weitere Filter (werden in Sidebar dynamisch gesetzt)
    if "selected_org_units" not in st.session_state:
        st.session_state["selected_org_units"] = []

    if "selected_cohorts" not in st.session_state:
        st.session_state["selected_cohorts"] = []

    if "selected_education" not in st.session_state:
        st.session_state["selected_education"] = []

    if "date_range" not in st.session_state:
        st.session_state["date_range"] = None


# =============================================================================
# MAIN APP
# =============================================================================

def main():
    """Hauptfunktion."""

    # Initialisiere Session State
    initialize_session_state()

    # Navigation Setup mit st.navigation
    pages = {
        "Dashboard": [
            st.Page("pages/1_🏠_Uebersicht.py", title="Überblick", icon="🏠"),
            st.Page("pages/7_⚡_Kompakt.py", title="Kompakt", icon="⚡"),
        ],
        "Analysen": [
            st.Page("pages/2_👥_Demografie.py", title="Demografie", icon="👥"),
            st.Page("pages/3_🔄_Altersteilzeit.py", title="Altersteilzeit", icon="🔄"),
            st.Page("pages/4_🏢_Organisationseinheiten.py", title="Organisationseinheiten", icon="🏢"),
            st.Page("pages/5_💼_Jobfamilies.py", title="Jobfamilies", icon="💼"),
        ],
        "Planung": [
            st.Page("pages/6_📈_Simulation.py", title="Simulation", icon="📈"),
        ],
    }

    pg = st.navigation(pages)

    # Header
    st.markdown(
        """
        <div style='text-align: center; padding: 1rem 0 2rem 0;'>
            <h1 style='color: #14b8a6; margin-bottom: 0.5rem;'>📊 HR Pulse</h1>
            <p style='color: #94a3b8; font-size: 1.1rem;'>
                HR-Analytics Dashboard
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Führe aktuelle Seite aus
    pg.run()


if __name__ == "__main__":
    main()
