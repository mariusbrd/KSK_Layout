"""
HR Pulse Dashboard - Haupteinstiegspunkt

Streamlit-basiertes HR-Analytics-Dashboard fuer Banken und Finanzdienstleister.
"""

import streamlit as st
import json
from pathlib import Path

from components.ui_shell import inject_ui_theme
from config.settings import DEFAULT_COHORTS, JOBFAMILIES_CONFIG_PATH, PAGE_CONFIG
from utils.cache_utils import ensure_cache_bootstrap
from utils.i18n import initialize_language_state, t

SIDEBAR_BRAND_LOGO = Path(__file__).resolve().parent / "assets" / "sidebar_brand.svg"
APP_DIR = Path(__file__).resolve().parent


def needs_setup() -> bool:
    """Lightweight setup check that avoids importing the full wizard at app startup."""
    if st.session_state.get("skip_wizard", False):
        return False

    config_path = APP_DIR / JOBFAMILIES_CONFIG_PATH
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            definitions = json.load(handle)
    except Exception:
        return True

    return not bool((definitions or {}).get("jobfamilies"))


def render_setup_wizard() -> bool:
    from components.setup_wizard import render_setup_wizard as _render_setup_wizard

    return _render_setup_wizard()

# =============================================================================
# PAGE CONFIGURATION
# =============================================================================

st.set_page_config(**PAGE_CONFIG)

# Wide Mode erzwingen (ueberschreibt Browser-Praeferenz und Cloud-Defaults)
inject_ui_theme()
st.logo(str(SIDEBAR_BRAND_LOGO), size="large")

# Cache einmalig leeren nach KPI-Engine-Update (kann nach erstem Start entfernt werden)
if "kpi_engine_cache_cleared" not in st.session_state:
    ensure_cache_bootstrap("data_prep", "2026-03-31-kpi-engine-v1")
    st.session_state["kpi_engine_cache_cleared"] = True


# =============================================================================
# SESSION STATE INITIALIZATION
# =============================================================================

def initialize_session_state():
    """Initialisiert Session State mit Defaults."""

    initialize_language_state()

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
        st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit", "Inaktiv"]

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


def build_pages():
    """Build the localized page navigation configuration."""
    return {
        "Analyse": [
            st.Page("pages/1_⚡_Kompakt.py", title=t("navigation.compact")),
            st.Page("pages/8_💼_Jobfamily_Analyse.py", title="Jobgruppen-Analyse"),
        ],
        "Prognosen": [
            st.Page("pages/3_📉_Prognose_Abgänge.py", title=t("navigation.attrition_forecast")),
            st.Page("pages/4_📈_Prognose_Zugänge.py", title=t("navigation.hiring_forecast")),
            st.Page("pages/5_🏢_Prognose_Hybrid.py", title=t("navigation.hybrid_forecast")),
        ],
        "Simulation": [
            st.Page("pages/8_⚙️_Simulationsparameter.py", title="Simulationsparameter", icon="⚙️"),
            st.Page("pages/7_⚡_Kompakt_plus_Simulation.py", title=t("navigation.compact_plus_simulation")),
        ],
        "Administration": [
            st.Page("pages/2_⚙️_Einstellungen.py", title=t("navigation.settings")),
            st.Page("pages/6_🔎_Deep_Dive_Exklusionsgruppen.py", title=t("navigation.exclusion_groups")),
        ],
    }


# =============================================================================
# MAIN APP
# =============================================================================

def main():
    """Hauptfunktion."""

    # Initialisiere Session State
    initialize_session_state()

    # Pruefe ob Setup-Wizard benoetigt wird
    if needs_setup():
        wizard_complete = render_setup_wizard()
        if not wizard_complete:
            return

    pg = st.navigation(build_pages())
    pg.run()


if __name__ == "__main__":
    main()
