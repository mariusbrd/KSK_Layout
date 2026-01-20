"""
Globale Filter-Sidebar für HR Pulse Dashboard.

Rendert alle Filter und wendet sie auf den DataFrame an.
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DEFAULT_COHORTS, COLORS


def render_global_filters(snapshot_df: pd.DataFrame, history_df: pd.DataFrame):
    """
    Rendert die komplette Filter-Sidebar und aktualisiert Session State.

    Args:
        snapshot_df: Snapshot DataFrame (für Filter-Optionen)
        history_df: History DataFrame (für Datumsbereich)
    """
    # Initialize session state defaults BEFORE rendering
    if "cohort_definitions" not in st.session_state:
        st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()

    if "selected_org_units" not in st.session_state:
        st.session_state["selected_org_units"] = []

    if "selected_cohorts" not in st.session_state:
        st.session_state["selected_cohorts"] = []

    if "selected_genders" not in st.session_state:
        st.session_state["selected_genders"] = ["m", "w"]

    if "selected_employment" not in st.session_state:
        st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit"]

    if "selected_education" not in st.session_state:
        st.session_state["selected_education"] = []

    if "selected_atz_status" not in st.session_state:
        st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]

    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "MAK"

    with st.sidebar:
        # MAK/EUR Toggle ganz oben
        st.markdown("### 💡 Ansicht")
        view_mode = st.radio(
            "Darstellungsart",
            options=["MAK", "EUR"],
            index=0 if st.session_state.get("view_mode", "MAK") == "MAK" else 1,
            horizontal=True,
            key="view_mode_toggle",
            label_visibility="collapsed"
        )
        st.session_state["view_mode"] = view_mode

        st.divider()

        st.header("🎯 Filter")

        # Datumsbereich (aus History)
        if not history_df.empty:
            min_date = history_df["Date"].min().date()
            max_date = history_df["Date"].max().date()

            st.subheader("📅 Zeitraum")
            date_range = st.date_input(
                "Zeitraum auswählen",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
                key="date_range_input",
                label_visibility="collapsed"
            )

            # Update session state
            if isinstance(date_range, tuple) and len(date_range) == 2:
                st.session_state["date_range"] = date_range
            else:
                st.session_state["date_range"] = (min_date, max_date)

        st.divider()

        # Organisationseinheiten
        st.subheader("🏢 Organisationseinheiten")
        org_units = sorted(snapshot_df["Kürzel OrgEinheit"].dropna().unique())
        org_unit_names = snapshot_df[["Kürzel OrgEinheit", "Organisationseinheit"]].drop_duplicates()
        org_unit_options = {
            row["Kürzel OrgEinheit"]: f"{row['Kürzel OrgEinheit']} - {row['Organisationseinheit']}"
            for _, row in org_unit_names.iterrows()
        }

        selected_orgs = st.multiselect(
            "Einheiten auswählen",
            options=org_units,
            default=st.session_state.get("selected_org_units", []),
            format_func=lambda x: org_unit_options.get(x, x),
            key="org_units_select",
            label_visibility="collapsed"
        )
        st.session_state["selected_org_units"] = selected_orgs

        st.divider()

        # Alterskohorten
        st.markdown("#### 👥 Alterskohorten")

        # Kohorte-Editor (expandable)
        with st.expander("⚙️ Kohorten bearbeiten"):
            render_cohort_editor()

        # Kohortenauswahl
        cohorts = list(st.session_state["cohort_definitions"].keys())
        selected_cohorts = st.multiselect(
            "Kohorten auswählen",
            options=cohorts,
            default=st.session_state.get("selected_cohorts", []),
            key="cohorts_select",
            label_visibility="collapsed"
        )
        st.session_state["selected_cohorts"] = selected_cohorts

        st.divider()

        # Geschlecht
        st.subheader("⚧ Geschlecht")
        selected_genders = st.multiselect(
            "Geschlecht auswählen",
            options=["m", "w"],
            default=st.session_state.get("selected_genders", ["m", "w"]),
            format_func=lambda x: "Männlich" if x == "m" else "Weiblich",
            key="gender_select",
            label_visibility="collapsed"
        )
        st.session_state["selected_genders"] = selected_genders

        st.divider()

        # Arbeitszeit
        st.subheader("⏰ Arbeitszeit")
        selected_employment = st.multiselect(
            "Arbeitszeit auswählen",
            options=["Vollzeit", "Teilzeit"],
            default=st.session_state.get("selected_employment", ["Vollzeit", "Teilzeit"]),
            key="employment_select",
            label_visibility="collapsed"
        )
        st.session_state["selected_employment"] = selected_employment

        st.divider()

        # Qualifikation
        st.subheader("🎓 Qualifikation")
        education_options = sorted(snapshot_df["Ausbildung"].dropna().unique())
        selected_education = st.multiselect(
            "Qualifikation auswählen",
            options=education_options,
            default=st.session_state.get("selected_education", []),
            key="education_select",
            label_visibility="collapsed"
        )
        st.session_state["selected_education"] = selected_education

        st.divider()

        # ATZ-Status
        st.subheader("🔄 Altersteilzeit")
        selected_atz = st.multiselect(
            "ATZ-Status auswählen",
            options=["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
            default=st.session_state.get("selected_atz_status", ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]),
            key="atz_select",
            label_visibility="collapsed"
        )
        st.session_state["selected_atz_status"] = selected_atz

        st.divider()

        # Reset Button
        if st.button("🔄 Alle Filter zurücksetzen", use_container_width=True):
            reset_filters()
            st.rerun()


def render_cohort_editor():
    """
    Editor für Alterskohorten-Definitionen.
    Speichert Änderungen in Session State.
    """
    st.write("**Kohorten-Definitionen**")

    cohorts = st.session_state["cohort_definitions"]
    modified = False

    for cohort_name, (min_age, max_age) in cohorts.items():
        col1, col2 = st.columns(2)
        with col1:
            new_min = st.number_input(
                f"{cohort_name} (Min)",
                min_value=0,
                max_value=99,
                value=min_age,
                key=f"cohort_min_{cohort_name}"
            )
        with col2:
            new_max = st.number_input(
                f"{cohort_name} (Max)",
                min_value=0,
                max_value=99,
                value=max_age,
                key=f"cohort_max_{cohort_name}"
            )

        if new_min != min_age or new_max != max_age:
            cohorts[cohort_name] = (new_min, new_max)
            modified = True

    if modified:
        st.session_state["cohort_definitions"] = cohorts
        st.info("✓ Kohorten aktualisiert. Daten werden neu berechnet.")

    # Reset zu Defaults
    if st.button("Zurück zu Standard-Kohorten", key="reset_cohorts"):
        st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
        st.success("✓ Standard-Kohorten wiederhergestellt")
        st.rerun()


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wendet alle aktiven Filter auf den DataFrame an.

    Args:
        df: Snapshot DataFrame

    Returns:
        Gefilterter DataFrame
    """
    filtered = df.copy()

    # Nur besetzte Stellen (Vakanten rausfiltern für Mitarbeiter-Analysen)
    # WICHTIG: Für Planstellen-Analysen muss dies optional sein
    # filtered = filtered[~filtered["Is_Vacant"]]

    # Organisationseinheiten
    if st.session_state.get("selected_org_units"):
        filtered = filtered[
            filtered["Kürzel OrgEinheit"].isin(st.session_state["selected_org_units"])
        ]

    # Alterskohorten
    if st.session_state.get("selected_cohorts"):
        filtered = filtered[
            filtered["Alterskohorte"].isin(st.session_state["selected_cohorts"])
        ]

    # Geschlecht
    if st.session_state.get("selected_genders"):
        filtered = filtered[
            filtered["Geschlecht"].isin(st.session_state["selected_genders"])
        ]

    # Arbeitszeit
    if st.session_state.get("selected_employment"):
        filtered = filtered[
            filtered["Arbeitszeit"].isin(st.session_state["selected_employment"])
        ]

    # Qualifikation
    if st.session_state.get("selected_education"):
        filtered = filtered[
            filtered["Ausbildung"].isin(st.session_state["selected_education"])
        ]

    # ATZ-Status
    if st.session_state.get("selected_atz_status"):
        filtered = filtered[
            filtered["ATZ_Status"].isin(st.session_state["selected_atz_status"])
        ]

    return filtered


def reset_filters():
    """Setzt alle Filter auf ihre Defaults zurück."""
    st.session_state["selected_org_units"] = []
    st.session_state["selected_cohorts"] = []
    st.session_state["selected_genders"] = ["m", "w"]
    st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit"]
    st.session_state["selected_education"] = []
    st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]
    st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()


def get_filter_summary() -> str:
    """
    Erstellt eine Zusammenfassung der aktiven Filter.

    Returns:
        String mit Filter-Zusammenfassung
    """
    active_filters = []

    if st.session_state.get("selected_org_units"):
        active_filters.append(f"{len(st.session_state['selected_org_units'])} Org-Einheiten")

    if st.session_state.get("selected_cohorts"):
        active_filters.append(f"{len(st.session_state['selected_cohorts'])} Kohorten")

    if len(st.session_state.get("selected_genders", [])) < 2:
        active_filters.append("Geschlecht")

    if len(st.session_state.get("selected_employment", [])) < 2:
        active_filters.append("Arbeitszeit")

    if st.session_state.get("selected_education"):
        active_filters.append("Qualifikation")

    if len(st.session_state.get("selected_atz_status", [])) < 3:
        active_filters.append("ATZ-Status")

    if active_filters:
        return f"🎯 {len(active_filters)} Filter aktiv: " + ", ".join(active_filters)
    else:
        return "Alle Daten angezeigt (keine Filter aktiv)"
