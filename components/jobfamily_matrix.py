"""
Job Family Assignment Matrix Komponente

Interaktive Matrix für manuelle Job-Zuordnung zu Job Families.
Layout: Zeilen = Jobs, Spalten = Job Families, Zellen = Checkboxen
"""

import streamlit as st
import pandas as pd
from typing import Dict, List, Optional, Tuple
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataloader.jobfamily_matcher import (
    add_manual_assignment,
    remove_manual_assignment,
    match_jobfamily,
    get_assignment_info,
    save_jobfamily_definitions
)
from config.settings import COLORS


def render_assignment_matrix(
    df: pd.DataFrame,
    jobfamily_definitions: Dict,
    on_change_callback: Optional[callable] = None,
    page_size: int = 50
) -> Dict:
    """
    Rendert die Job Family Assignment Matrix.

    Args:
        df: DataFrame mit Planstellen (muss 'Planstellennr' und 'Planstelle' enthalten)
        jobfamily_definitions: v2 Jobfamily-Definitionen
        on_change_callback: Optional Callback bei Änderungen
        page_size: Anzahl Jobs pro Seite (Pagination)

    Returns:
        Dict mit Änderungen {'planstellennr': 'jobfamily'} oder {}
    """
    # Initialize session state
    if "jf_matrix_search" not in st.session_state:
        st.session_state["jf_matrix_search"] = ""
    if "jf_matrix_page" not in st.session_state:
        st.session_state["jf_matrix_page"] = 0
    if "jf_matrix_filter_unmapped" not in st.session_state:
        st.session_state["jf_matrix_filter_unmapped"] = False
    if "jf_pending_changes" not in st.session_state:
        st.session_state["jf_pending_changes"] = {}

    # Extract job families
    families = list(jobfamily_definitions.get("jobfamilies", {}).keys())

    if not families:
        st.warning("⚠️ Keine Job Families definiert. Bitte erstellen Sie zunächst Job Families im 'Definitionen' Tab.")
        return {}

    # Ensure required columns
    if "Planstellennr" not in df.columns or "Planstelle" not in df.columns:
        st.error("❌ DataFrame muss 'Planstellennr' und 'Planstelle' Spalten enthalten.")
        return {}

    # Header with stats
    st.markdown("### 📋 Job-Zuordnungs-Matrix")

    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        st.metric(
            "Gesamt Jobs",
            f"{len(df):,}",
            help="Anzahl aller Planstellen in gefilterten Daten"
        )

    with col2:
        # Count manually assigned
        manual_count = 0
        for _, row in df.iterrows():
            info = get_assignment_info(str(row["Planstellennr"]), jobfamily_definitions)
            if info["has_manual_assignment"] or info["has_manual_override"]:
                manual_count += 1

        st.metric(
            "Manuell zugeordnet",
            f"{manual_count:,}",
            help="Anzahl Planstellen mit manueller Zuordnung"
        )

    with col3:
        st.metric(
            "Job Families",
            f"{len(families)}",
            help="Anzahl definierter Job Families"
        )

    st.divider()

    # Filters and search
    col1, col2, col3 = st.columns([3, 2, 2])

    with col1:
        search_query = st.text_input(
            "🔍 Suche nach Planstellen-Bezeichnung",
            value=st.session_state["jf_matrix_search"],
            key="matrix_search_input",
            placeholder="z.B. 'IT-Administrator', 'Berater'..."
        )
        st.session_state["jf_matrix_search"] = search_query

    with col2:
        filter_unmapped = st.checkbox(
            "Nur nicht zugeordnete Jobs anzeigen",
            value=st.session_state["jf_matrix_filter_unmapped"],
            key="matrix_filter_unmapped",
            help="Zeigt nur Jobs ohne Pattern-Match (UNMAPPED)"
        )
        st.session_state["jf_matrix_filter_unmapped"] = filter_unmapped

    with col3:
        if st.button("🔄 Filter zurücksetzen", use_container_width=True):
            st.session_state["jf_matrix_search"] = ""
            st.session_state["jf_matrix_filter_unmapped"] = False
            st.session_state["jf_matrix_page"] = 0
            st.rerun()

    # Apply filters
    filtered_df = df.copy()

    # Search filter
    if search_query:
        mask = filtered_df["Planstelle"].str.contains(search_query, case=False, na=False)
        filtered_df = filtered_df[mask]

    # Unmapped filter
    if filter_unmapped:
        unmapped_mask = []
        for _, row in filtered_df.iterrows():
            family, match_type = match_jobfamily(
                row.get("Planstelle", ""),
                str(row.get("Planstellennr", "")),
                jobfamily_definitions
            )
            unmapped_mask.append(family == "UNMAPPED" and match_type == "unmapped")
        filtered_df = filtered_df[unmapped_mask]

    total_filtered = len(filtered_df)

    if total_filtered == 0:
        st.info("ℹ️ Keine Jobs gefunden. Bitte passen Sie die Filter an.")
        return {}

    st.caption(f"📊 {total_filtered:,} Jobs gefunden")

    # Pagination
    total_pages = (total_filtered + page_size - 1) // page_size
    current_page = st.session_state["jf_matrix_page"]

    if total_pages > 1:
        col1, col2, col3 = st.columns([1, 2, 1])

        with col1:
            if st.button("⬅️ Vorherige", disabled=current_page == 0, use_container_width=True):
                st.session_state["jf_matrix_page"] = max(0, current_page - 1)
                st.rerun()

        with col2:
            st.markdown(f"**Seite {current_page + 1} von {total_pages}**")

        with col3:
            if st.button("Nächste ➡️", disabled=current_page >= total_pages - 1, use_container_width=True):
                st.session_state["jf_matrix_page"] = min(total_pages - 1, current_page + 1)
                st.rerun()

    # Paginate
    start_idx = current_page * page_size
    end_idx = min(start_idx + page_size, total_filtered)
    page_df = filtered_df.iloc[start_idx:end_idx]

    st.caption(f"Zeige Jobs {start_idx + 1}-{end_idx} von {total_filtered:,}")

    st.divider()

    # Render matrix
    changes = _render_matrix_table(page_df, families, jobfamily_definitions)

    # Save button
    if changes or st.session_state.get("jf_pending_changes"):
        st.divider()

        col1, col2, col3 = st.columns([2, 1, 1])

        with col1:
            change_count = len(st.session_state.get("jf_pending_changes", {}))
            st.info(f"✏️ {change_count} ungespeicherte Änderungen")

        with col2:
            if st.button("❌ Verwerfen", use_container_width=True):
                st.session_state["jf_pending_changes"] = {}
                st.rerun()

        with col3:
            if st.button("💾 Speichern", type="primary", use_container_width=True):
                _save_pending_changes(jobfamily_definitions, on_change_callback)
                st.success("✅ Änderungen gespeichert!")
                st.session_state["jf_pending_changes"] = {}
                st.rerun()

    return changes


def _render_matrix_table(
    df: pd.DataFrame,
    families: List[str],
    definitions: Dict
) -> Dict:
    """
    Rendert die eigentliche Matrix-Tabelle.

    Args:
        df: DataFrame mit Jobs (aktueller Seiten-Ausschnitt)
        families: Liste der Job Families
        definitions: Jobfamily-Definitionen

    Returns:
        Dict mit Änderungen
    """
    changes = {}

    # Table header
    header_cols = ["Job"] + families
    cols = st.columns([3] + [1] * len(families))

    # Header row
    with cols[0]:
        st.markdown("**Planstelle**")

    for i, family in enumerate(families):
        with cols[i + 1]:
            # Truncate long family names
            display_name = family[:15] + "..." if len(family) > 15 else family
            st.markdown(f"**{display_name}**")

    st.divider()

    # Data rows
    for idx, row in df.iterrows():
        planstellennr = str(row["Planstellennr"])
        planstelle = row["Planstelle"]

        # Get current assignment info
        info = get_assignment_info(planstellennr, definitions)
        family, match_type = match_jobfamily(planstelle, planstellennr, definitions)

        cols = st.columns([3] + [1] * len(families))

        # Job name column with match type indicator
        with cols[0]:
            match_icon = _get_match_type_icon(match_type)
            display_planstelle = planstelle[:40] + "..." if len(planstelle) > 40 else planstelle

            st.markdown(
                f"{match_icon} {display_planstelle}",
                help=f"Planstellennr: {planstellennr}\nAktuell: {family} ({match_type})"
            )

        # Checkbox columns for each family
        for i, fam in enumerate(families):
            with cols[i + 1]:
                # Check if currently assigned
                is_assigned = (
                    (fam == info.get("override_family")) or
                    (fam == info.get("assignment_family")) or
                    (fam == family and match_type == "pattern")
                )

                # Check for pending changes
                pending_family = st.session_state.get("jf_pending_changes", {}).get(planstellennr)
                if pending_family is not None:
                    is_assigned = (pending_family == fam)

                # Checkbox
                checked = st.checkbox(
                    "✓",
                    value=is_assigned,
                    key=f"matrix_{planstellennr}_{fam}_{idx}",
                    label_visibility="collapsed",
                    help=f"Zuordnen zu '{fam}'"
                )

                # Handle change
                if checked and not is_assigned:
                    # Add assignment
                    st.session_state["jf_pending_changes"][planstellennr] = fam
                    changes[planstellennr] = fam
                elif not checked and is_assigned:
                    # Remove assignment
                    st.session_state["jf_pending_changes"][planstellennr] = None
                    changes[planstellennr] = None

        # Add thin divider between rows
        st.markdown("<hr style='margin: 2px 0; border: 0; border-top: 1px solid #eee;'>", unsafe_allow_html=True)

    return changes


def _get_match_type_icon(match_type: str) -> str:
    """
    Gibt Icon für Match-Type zurück.

    Args:
        match_type: "pattern", "manual_assignment", "manual_override", "unmapped"

    Returns:
        Emoji-Icon
    """
    icons = {
        "pattern": "🤖",
        "manual_assignment": "👤",
        "manual_override": "⭐",
        "unmapped": "❓"
    }
    return icons.get(match_type, "")


def _save_pending_changes(definitions: Dict, callback: Optional[callable] = None):
    """
    Speichert alle pending changes.

    Args:
        definitions: Jobfamily-Definitionen
        callback: Optional Callback-Funktion
    """
    pending = st.session_state.get("jf_pending_changes", {})

    for planstellennr, jobfamily in pending.items():
        if jobfamily is None:
            # Remove assignment
            remove_manual_assignment(planstellennr, definitions)
        else:
            # Add assignment (as manual_assignment, not override)
            add_manual_assignment(planstellennr, jobfamily, definitions, as_override=False)

    # Save to file
    save_jobfamily_definitions(definitions)

    # Call callback
    if callback:
        callback(definitions)


def render_bulk_actions(
    df: pd.DataFrame,
    jobfamily_definitions: Dict,
    on_change_callback: Optional[callable] = None
) -> None:
    """
    Rendert Bulk-Actions für Matrix.

    Args:
        df: DataFrame mit Planstellen
        jobfamily_definitions: Jobfamily-Definitionen
        on_change_callback: Optional Callback
    """
    st.markdown("### ⚡ Bulk-Actions")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 🤖 Pattern-basierte Zuordnung")
        st.caption("Wendet Pattern-Matching auf alle Jobs an und speichert als manuelle Zuordnungen")

        unmapped_count = 0
        pattern_matches = 0

        for _, row in df.iterrows():
            planstellennr = str(row.get("Planstellennr", ""))
            planstelle = row.get("Planstelle", "")

            family, match_type = match_jobfamily(planstelle, planstellennr, jobfamily_definitions)

            if match_type == "pattern":
                pattern_matches += 1
            elif match_type == "unmapped":
                unmapped_count += 1

        st.info(f"📊 {pattern_matches:,} Jobs würden via Pattern zugeordnet")

        if st.button(
            f"🤖 Pattern auf alle anwenden ({pattern_matches} Jobs)",
            use_container_width=True,
            disabled=pattern_matches == 0
        ):
            _apply_patterns_as_manual(df, jobfamily_definitions, on_change_callback)

    with col2:
        st.markdown("#### 🗑️ Manuelle Zuordnungen löschen")
        st.caption("Entfernt alle manuellen Zuordnungen (Pattern-Matches bleiben)")

        manual_count = 0
        for _, row in df.iterrows():
            info = get_assignment_info(str(row.get("Planstellennr", "")), jobfamily_definitions)
            if info["has_manual_assignment"] or info["has_manual_override"]:
                manual_count += 1

        st.info(f"📊 {manual_count:,} Jobs mit manueller Zuordnung")

        if st.button(
            f"🗑️ Alle manuellen Zuordnungen löschen ({manual_count} Jobs)",
            use_container_width=True,
            type="secondary",
            disabled=manual_count == 0
        ):
            _clear_all_manual(df, jobfamily_definitions, on_change_callback)


def _apply_patterns_as_manual(
    df: pd.DataFrame,
    definitions: Dict,
    callback: Optional[callable] = None
):
    """
    Wendet Pattern-Matching an und speichert als manuelle Zuordnungen.
    """
    count = 0

    with st.spinner("Wende Pattern-Matching an..."):
        for _, row in df.iterrows():
            planstellennr = str(row.get("Planstellennr", ""))
            planstelle = row.get("Planstelle", "")

            family, match_type = match_jobfamily(planstelle, planstellennr, definitions)

            if match_type == "pattern":
                add_manual_assignment(planstellennr, family, definitions, as_override=False)
                count += 1

        # Save
        save_jobfamily_definitions(definitions)

        if callback:
            callback(definitions)

    st.success(f"✅ {count:,} Jobs als manuelle Zuordnungen gespeichert!")
    st.rerun()


def _clear_all_manual(
    df: pd.DataFrame,
    definitions: Dict,
    callback: Optional[callable] = None
):
    """
    Löscht alle manuellen Zuordnungen für Jobs im DataFrame.
    """
    count = 0

    with st.spinner("Lösche manuelle Zuordnungen..."):
        for _, row in df.iterrows():
            planstellennr = str(row.get("Planstellennr", ""))
            info = get_assignment_info(planstellennr, definitions)

            if info["has_manual_assignment"] or info["has_manual_override"]:
                remove_manual_assignment(planstellennr, definitions)
                count += 1

        # Save
        save_jobfamily_definitions(definitions)

        if callback:
            callback(definitions)

    st.success(f"✅ {count:,} manuelle Zuordnungen gelöscht!")
    st.rerun()


def render_quick_add_family(
    definitions: Dict,
    on_add_callback: Optional[callable] = None
) -> Optional[str]:
    """
    Quick-Add Widget für neue Job Family direkt aus Matrix.

    Args:
        definitions: Jobfamily-Definitionen
        on_add_callback: Optional Callback bei Hinzufügen

    Returns:
        Name der neuen Family oder None
    """
    with st.expander("➕ Schnell neue Job Family hinzufügen", expanded=False):
        st.caption("Erstellen Sie eine neue Job Family direkt aus der Matrix")

        col1, col2 = st.columns([2, 1])

        with col1:
            new_name = st.text_input(
                "Name der Job Family",
                key="quick_add_family_name",
                placeholder="z.B. 'IT & Digitalisierung'"
            )

        with col2:
            if st.button("➕ Hinzufügen", use_container_width=True, type="primary"):
                if new_name and new_name.strip():
                    # Check if already exists
                    if new_name in definitions.get("jobfamilies", {}):
                        st.error(f"❌ Job Family '{new_name}' existiert bereits!")
                        return None

                    # Add new family
                    if "jobfamilies" not in definitions:
                        definitions["jobfamilies"] = {}

                    definitions["jobfamilies"][new_name] = {
                        "patterns": [],
                        "min_qualification": "",
                        "description": f"Erstellt via Quick-Add am {pd.Timestamp.now().strftime('%Y-%m-%d')}",
                        "version": "v2",
                        "manual_assignments": []
                    }

                    # Save
                    save_jobfamily_definitions(definitions)

                    if on_add_callback:
                        on_add_callback(definitions)

                    st.success(f"✅ Job Family '{new_name}' erstellt!")
                    st.rerun()

                    return new_name
                else:
                    st.error("❌ Bitte geben Sie einen Namen ein!")

    return None


def render_matrix_legend():
    """
    Rendert Legende für Match-Type Icons.
    """
    st.markdown("### 📖 Legende")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown("🤖 **Pattern-Match**")
        st.caption("Automatisch via Wildcard")

    with col2:
        st.markdown("👤 **Manual Assignment**")
        st.caption("Manuell zugeordnet")

    with col3:
        st.markdown("⭐ **Manual Override**")
        st.caption("Override (höchste Priorität)")

    with col4:
        st.markdown("❓ **Unmapped**")
        st.caption("Keine Zuordnung")
