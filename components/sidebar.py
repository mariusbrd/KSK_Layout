"""
Globale Filter-Sidebar für HR Pulse Dashboard.

Rendert alle Filter und wendet sie auf den DataFrame an.
"""
from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime
import sys
import os
import re

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DEFAULT_COHORTS, COLORS


# -----------------------------
# UI Helper
# -----------------------------
def _smart_label(base: str, selected, total: int | None = None) -> str:
    """
    Creates a dynamic label like:
    - "Qualifikation (Alle)" when none selected or all selected
    - "Qualifikation (2 gewählt)" when some selected
    """
    try:
        n = len(selected) if selected else 0
    except TypeError:
        n = 0

    if total is not None and total > 0:
        if n == 0 or n == total:
            return f"{base} (Alle)"
        return f"{base} ({n} gewählt)"

    # If total unknown, use "Alle" when none selected
    if n == 0:
        return f"{base} (Alle)"
    return f"{base} ({n} gewählt)"


def _render_select_all_reset_row(
    *,
    select_all_key: str,
    reset_key: str,
    on_select_all,
    on_reset,
):
    """
    Renders two small helper buttons side-by-side.
    """
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Alle auswählen", key=select_all_key, use_container_width=True):
            on_select_all()
            st.rerun()
    with c2:
        if st.button("Zurücksetzen", key=reset_key, use_container_width=True):
            on_reset()
            st.rerun()


def _segmented_single(label: str, options: list[str], value: str, key: str):
    """
    Streamlit >= 1.31: st.segmented_control
    Fallback: st.radio
    """
    if hasattr(st, "segmented_control"):
        return st.segmented_control(
            label,
            options=options,
            default=value,
            key=key,
            label_visibility="collapsed",
        )
    return st.radio(
        label,
        options=options,
        index=options.index(value) if value in options else 0,
        horizontal=True,
        key=key,
        label_visibility="collapsed",
    )


def _segmented_multi_gender(label: str, options: list[str], default: list[str], key: str):
    """
    Uses st.segmented_control in multi-select mode if available.
    Fallback: st.multiselect.
    """
    if hasattr(st, "segmented_control"):
        # Some Streamlit versions support selection_mode="multi".
        # If not supported, we fall back gracefully.
        try:
            return st.segmented_control(
                label,
                options=options,
                default=default,
                selection_mode="multi",
                key=key,
                label_visibility="collapsed",
            )
        except TypeError:
            pass

    return st.multiselect(
        label,
        options=options,
        default=default,
        key=key,
        label_visibility="collapsed",
    )


# -----------------------------
# Public API
# -----------------------------
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
        st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit", "Inaktiv"]

    if "selected_education" not in st.session_state:
        st.session_state["selected_education"] = []

    if "selected_atz_status" not in st.session_state:
        st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]

    if "selected_jobfamilies" not in st.session_state:
        st.session_state["selected_jobfamilies"] = []

    if "selected_oe_clusters" not in st.session_state:
        st.session_state["selected_oe_clusters"] = []
    
    if "selected_jf_clusters" not in st.session_state:
        st.session_state["selected_jf_clusters"] = []

    if "exclude_auszubildende" not in st.session_state:
        st.session_state["exclude_auszubildende"] = False
    if "exclude_elternzeit" not in st.session_state:
        st.session_state["exclude_elternzeit"] = False
    if "exclude_erziehungszeit" not in st.session_state:
        st.session_state["exclude_erziehungszeit"] = False

    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "MAK"

    with st.sidebar:
        # -----------------------------
        # Data Source Indicator
        # -----------------------------
        from components.data_source_indicator import show_data_source_badge
        show_data_source_badge()

        st.markdown("---")

        # -----------------------------
        # Header / Summary
        # -----------------------------
        st.markdown("## 🎛️ Dashboard Steuerung")
        st.caption(get_filter_summary())

        # -----------------------------
        # View Mode (MAK/EUR) - compact pills
        # -----------------------------
        st.markdown("### 💡 Ansicht")
        view_mode = _segmented_single(
            "Darstellungsart",
            options=["MAK", "EUR"],
            value=st.session_state.get("view_mode", "MAK"),
            key="view_mode_toggle",
        )
        st.session_state["view_mode"] = view_mode

        st.markdown("---")

        # -----------------------------
        # Primary Filters (always visible)
        # -----------------------------
        st.markdown("### 🎯 Primäre Filter")

        # Zeitraum (aus History)
        if not history_df.empty and "Date" in history_df.columns:
            min_date = history_df["Date"].min().date()
            max_date = history_df["Date"].max().date()

            st.markdown("**📅 Zeitraum**")
            date_range = st.date_input(
                "Zeitraum auswählen",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
                key="date_range_input",
                label_visibility="collapsed",
            )

            # Update session state
            if isinstance(date_range, tuple) and len(date_range) == 2:
                st.session_state["date_range"] = date_range
            else:
                st.session_state["date_range"] = (min_date, max_date)

        # Organisationseinheiten
        # FIX: Use Organisationseinheit (full name) as key, since Kürzel OrgEinheit
        # is NOT unique (e.g. 591 = "Beratungs-Center Herrenberg" AND "Akquisepool Herrenberg").
        st.markdown("**🏢 Organisationseinheiten**")
        if "Organisationseinheit" in snapshot_df.columns:
            org_units = sorted(snapshot_df["Organisationseinheit"].dropna().unique())
        else:
            org_units = []

        org_unit_display = {}
        if {"Kürzel OrgEinheit", "Organisationseinheit"}.issubset(snapshot_df.columns):
            org_unit_pairs = snapshot_df[["Kürzel OrgEinheit", "Organisationseinheit"]].drop_duplicates()
            org_unit_display = {
                row["Organisationseinheit"]: f"{row['Kürzel OrgEinheit']} - {row['Organisationseinheit']}"
                for _, row in org_unit_pairs.iterrows()
            }

        selected_orgs = st.multiselect(
            "Einheiten auswählen",
            options=org_units,
            default=st.session_state.get("selected_org_units", []),
            format_func=lambda x: org_unit_display.get(x, x),
            key="org_units_select",
            label_visibility="collapsed",
        )
        st.session_state["selected_org_units"] = selected_orgs

        # Job Families
        st.markdown("**💼 Job Families**")
        if "Jobfamily" in snapshot_df.columns:
            jobfamilies = sorted(
                snapshot_df[snapshot_df["Jobfamily"] != "UNMAPPED"]["Jobfamily"].dropna().unique()
            )
        else:
            jobfamilies = []

        # Helper row for jobfamily multiselect (Select all / reset)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Alle auswählen", key="jf_select_all", use_container_width=True):
                st.session_state["selected_jobfamilies"] = jobfamilies.copy()
                st.rerun()
        with c2:
            if st.button("Zurücksetzen", key="jf_reset", use_container_width=True):
                st.session_state["selected_jobfamilies"] = []
                st.rerun()

        selected_jf = st.multiselect(
            "Job Families auswählen",
            options=jobfamilies,
            default=st.session_state.get("selected_jobfamilies", []),
            key="jobfamily_select",
            label_visibility="collapsed",
        )
        st.session_state["selected_jobfamilies"] = selected_jf

        # Custom Clusters (Optional)
        has_oe_clusters = "OE-Cluster" in snapshot_df.columns and snapshot_df["OE-Cluster"].nunique() > 1
        has_jf_clusters = "JF-Cluster" in snapshot_df.columns and snapshot_df["JF-Cluster"].nunique() > 1
        
        if has_oe_clusters or has_jf_clusters:
            st.markdown("### 🧩 Cluster-Filter")
            
            if has_oe_clusters:
                oe_clusters = sorted(snapshot_df["OE-Cluster"].dropna().unique())
                st.session_state["selected_oe_clusters"] = st.multiselect(
                    "OE-Cluster auswählen",
                    options=oe_clusters,
                    default=st.session_state.get("selected_oe_clusters", []),
                    key="oe_cluster_select"
                )
                
            if has_jf_clusters:
                jf_clusters = sorted(snapshot_df["JF-Cluster"].dropna().unique())
                st.session_state["selected_jf_clusters"] = st.multiselect(
                    "JF-Cluster auswählen",
                    options=jf_clusters,
                    default=st.session_state.get("selected_jf_clusters", []),
                    key="jf_cluster_select"
                )

        # Alterskohorten (Auswahl + Editor in Popover)
        st.markdown("**👥 Alterskohorten**")
        cohorts = list(st.session_state["cohort_definitions"].keys())

        # Helper row for cohort multiselect (Select all / reset)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Alle auswählen", key="cohorts_select_all", use_container_width=True):
                st.session_state["selected_cohorts"] = cohorts.copy()
                st.rerun()
        with c2:
            if st.button("Zurücksetzen", key="cohorts_reset", use_container_width=True):
                st.session_state["selected_cohorts"] = []
                st.rerun()

        selected_cohorts = st.multiselect(
            "Kohorten auswählen",
            options=cohorts,
            default=st.session_state.get("selected_cohorts", []),
            key="cohorts_select",
            label_visibility="collapsed",
        )
        st.session_state["selected_cohorts"] = selected_cohorts

        # Cohort editor in popover (configuration, not daily filter)
        if hasattr(st, "popover"):
            with st.popover("⚙️ Kohorten bearbeiten", use_container_width=True):
                render_cohort_editor()
        else:
            with st.expander("⚙️ Kohorten bearbeiten"):
                render_cohort_editor()

        st.markdown("---")

        # -----------------------------
        # Demography (compact)
        # -----------------------------
        st.markdown("### 👤 Demografie")

        # Geschlecht - compact pills (multi)
        # Keep values "m"/"w" in session state, same as before.
        # Show as "Männlich/Weiblich" via options mapping.
        gender_map = {"m": "Männlich", "w": "Weiblich"}
        # We keep internal codes but render readable labels by mapping the segmented options.
        # segmented_control returns selected option strings; we convert back to codes if needed.
        # To keep behavior stable, we expose options as codes and rely on format elsewhere.
        # For segmented_control we can't pass format_func, so we use readable labels as options and map back.
        gender_display_to_code = {v: k for k, v in gender_map.items()}
        gender_code_to_display = {k: v for k, v in gender_map.items()}

        default_gender_displays = [gender_code_to_display.get(x, x) for x in st.session_state.get("selected_genders", ["m", "w"])]
        selected_gender_displays = _segmented_multi_gender(
            "Geschlecht",
            options=[gender_map["m"], gender_map["w"]],
            default=default_gender_displays,
            key="gender_segmented",
        )
        # Normalize selection back to codes
        if isinstance(selected_gender_displays, str):
            selected_gender_displays = [selected_gender_displays]
        selected_genders = [gender_display_to_code.get(x, x) for x in (selected_gender_displays or [])]
        st.session_state["selected_genders"] = selected_genders

        # Arbeitszeit (kept as multiselect; still primary for many HR dashboards)
        st.markdown("**⏰ Arbeitszeit**")
        selected_employment = st.multiselect(
            "Arbeitszeit auswählen",
            options=["Vollzeit", "Teilzeit", "Inaktiv"],
            default=st.session_state.get("selected_employment", ["Vollzeit", "Teilzeit", "Inaktiv"]),
            key="employment_select",
            label_visibility="collapsed",
        )
        st.session_state["selected_employment"] = selected_employment

        st.markdown("---")

        # -----------------------------
        # Secondary Filters (accordion principle)
        # -----------------------------
        st.markdown("### 🧩 Weitere Filter")

        # Qualifikation (Expander with smart label + buttons)
        education_options = []
        if "Ausbildung" in snapshot_df.columns:
            education_options = sorted(snapshot_df["Ausbildung"].dropna().unique())

        edu_selected = st.session_state.get("selected_education", [])
        edu_label = _smart_label("🎓 Qualifikation", edu_selected, total=len(education_options))

        with st.expander(edu_label, expanded=False):
            _render_select_all_reset_row(
                select_all_key="edu_select_all",
                reset_key="edu_reset",
                on_select_all=lambda: st.session_state.__setitem__("selected_education", education_options.copy()),
                on_reset=lambda: st.session_state.__setitem__("selected_education", []),
            )

            selected_education = st.multiselect(
                "Qualifikation auswählen",
                options=education_options,
                default=edu_selected,
                key="education_select",
                label_visibility="collapsed",
            )
            st.session_state["selected_education"] = selected_education

        # ATZ-Status (Expander with smart label + buttons)
        atz_options = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]
        atz_selected = st.session_state.get("selected_atz_status", atz_options)
        atz_label = _smart_label("🔄 Altersteilzeit", atz_selected, total=len(atz_options))

        with st.expander(atz_label, expanded=False):
            _render_select_all_reset_row(
                select_all_key="atz_select_all",
                reset_key="atz_reset",
                on_select_all=lambda: st.session_state.__setitem__("selected_atz_status", atz_options.copy()),
                on_reset=lambda: st.session_state.__setitem__("selected_atz_status", []),
            )

            selected_atz = st.multiselect(
                "ATZ-Status auswählen",
                options=atz_options,
                default=atz_selected,
                key="atz_select",
                label_visibility="collapsed",
            )
            st.session_state["selected_atz_status"] = selected_atz

        st.markdown("---")

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
                key=f"cohort_min_{cohort_name}",
            )
        with col2:
            new_max = st.number_input(
                f"{cohort_name} (Max)",
                min_value=0,
                max_value=99,
                value=max_age,
                key=f"cohort_max_{cohort_name}",
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
    if df.empty:
        return df

    filtered = df.copy()

    # Filter-Mapping: session_state key -> DataFrame column
    # FIX: Use Organisationseinheit (unique per sub-unit) instead of Kürzel OrgEinheit
    filter_mapping = {
        "selected_org_units": "Organisationseinheit",
        "selected_jobfamilies": "Jobfamily",
        "selected_cohorts": "Alterskohorte",
        "selected_genders": "Geschlecht",
        "selected_employment": "Arbeitszeit",
        "selected_education": "Ausbildung",
        "selected_atz_status": "ATZ_Status",
        "selected_oe_clusters": "OE-Cluster",
        "selected_jf_clusters": "JF-Cluster",
    }

    for state_key, column_name in filter_mapping.items():
        filter_values = st.session_state.get(state_key)
        if filter_values and column_name in filtered.columns:
            # P07: Robust filtering (handle mixed types and .0 floats)
            # Create a normalized temporary series for filtering
            s_norm = filtered[column_name].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            
            # Ensure filter values are also normalized (just strings)
            # P10: Fix for mixed types (612 vs 612.0)
            filter_vals_norm = [re.sub(r"\.0$", "", str(v).strip()) for v in filter_values]
            
            mask = s_norm.isin(filter_vals_norm)
            filtered = filtered.loc[mask]

    # --- Gruppen-Ausschlüsse (konfiguriert in Einstellungen) ---
    from utils.settings_loader import get_setting
    ex_config = get_setting("exclusions", {})
    
    exclusion_mask = pd.Series(False, index=filtered.index)
    
    # 1. Status-basierte Ausschlüsse
    if ex_config.get("vorstand"):
        if "MitarbGruppenbez." in filtered.columns:
            exclusion_mask |= (filtered["MitarbGruppenbez."] == "Vorstand")
            
    if ex_config.get("ruhend_bv"):
        if "Status kundenindividuell" in filtered.columns:
            exclusion_mask |= (filtered["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis")
            
    # 2. Bereichs-basierte Ausschlüsse (99XX)
    ex_org_units = ex_config.get("org_units", [])
    if ex_org_units and "Kürzel OrgEinheit" in filtered.columns:
        # P07: Robust normalization (handle 9990.0 -> "9990")
        s_ou = filtered["Kürzel OrgEinheit"].astype(str).str.strip()
        # Remove trailing .0 if present (common pandas issue with mixed types)
        s_ou = s_ou.str.replace(r"\.0$", "", regex=True)
        exclusion_mask |= (s_ou.isin(ex_org_units))

    # 3. "Treat as Vacant" Logik
    # Wir löschen die Zeilen nicht, sondern "entfernen" die Person von der Planstelle.
    # Dadurch bleibt die Soll-Kapa (Planstelle) erhalten, aber das Ist (Person) verschwindet.
    if exclusion_mask.any():
        person_fields = [
            "Personalnummer", "PersNr", "Personalnachname", "Personalvorname", 
            "Name", "Vorname", "Nachname", "GebDatum", "Eintritt", "Austritt", 
            "Alter", "BsGrd", "Alter_Jahre", "Ist_Azubi"
        ]
        
        # Vorhandene Spalten identifizieren
        existing_fields = [f for f in person_fields if f in filtered.columns]
        
        # Auf Maske anwenden
        filtered["Is_Vacant"] = filtered["Is_Vacant"].astype("boolean")
        filtered.loc[exclusion_mask, "Is_Vacant"] = True
        filtered.loc[exclusion_mask, existing_fields] = pd.NA
        
        # Kennzahlen auf 0 setzen
        for num_col in ["MAK", "FTE_person", "FTE_assigned", "Total_Cost_Year"]:
            if num_col in filtered.columns:
                filtered.loc[exclusion_mask, num_col] = 0.0

    return filtered


def reset_filters():
    """Setzt alle Filter auf ihre Defaults zurück."""
    st.session_state["selected_org_units"] = []
    st.session_state["selected_jobfamilies"] = []
    st.session_state["selected_cohorts"] = []
    st.session_state["selected_genders"] = ["m", "w"]
    st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit", "Inaktiv"]
    st.session_state["selected_education"] = []
    st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]
    st.session_state["selected_oe_clusters"] = []
    st.session_state["selected_jf_clusters"] = []
    st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
    
    # Reset advanced exclusions in session state
    st.session_state["exclude_vorstand"] = False
    st.session_state["exclude_ruhend"] = False
    st.session_state["exclude_org_units"] = []


def get_filter_summary() -> str:
    """
    Erstellt eine Zusammenfassung der aktiven Filter.

    Returns:
        String mit Filter-Zusammenfassung
    """
    from utils.settings_loader import get_setting
    ex_config = get_setting("exclusions", {})
    
    active_filters = []

    if st.session_state.get("selected_org_units"):
        active_filters.append(f"{len(st.session_state['selected_org_units'])} Org-Einheiten")

    if st.session_state.get("selected_jobfamilies"):
        active_filters.append(f"{len(st.session_state['selected_jobfamilies'])} Job Families")

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

    if st.session_state.get("selected_oe_clusters"):
        active_filters.append(f"{len(st.session_state['selected_oe_clusters'])} OE-Cluster")

    if st.session_state.get("selected_jf_clusters"):
        active_filters.append(f"{len(st.session_state['selected_jf_clusters'])} JF-Cluster")

    # Gruppen-Ausschlüsse (Neu: Aus JSON-Config)
    exclusion_labels = []
    if ex_config.get("vorstand"):
        exclusion_labels.append("Vorstand")
    if ex_config.get("ruhend_bv"):
        exclusion_labels.append("Ruhend")
    
    ex_units = ex_config.get("org_units", [])
    if ex_units:
        exclusion_labels.append(f"{len(ex_units)} Bereiche")
        
    if exclusion_labels:
        active_filters.append(f"Ausschl.: {', '.join(exclusion_labels)}")

    if active_filters:
        return f"🎯 {len(active_filters)} Filter aktiv: " + ", ".join(active_filters)
    else:
        return "Alle Daten angezeigt (keine Filter aktiv)"
