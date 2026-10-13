"""Shared Excel controls around the existing quit matrix editors."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from utils.quit_matrix_excel import (
    as_table, export_quit_template, has_age_differences, merge_quit_updates,
    parse_quit_upload, preview_quit_updates,
)


def render_quit_matrix_with_excel(frame: pd.DataFrame, *, dimension: str, key: str,
                                  disabled: bool = False, **editor_kwargs) -> pd.DataFrame:
    state_key = f"{key}_excel_draft"
    revision_key = f"{key}_excel_revision"
    pending_key = f"{key}_excel_pending"
    last_frame_key = f"{key}_excel_last_frame"
    revision = st.session_state.get(revision_key, 0)
    draft = st.session_state.get(state_key)
    # Preserve the original editor key and input until an import is explicitly applied.
    editor_key = key if not revision else f"{key}_import_{revision}"
    # After leaving a page Streamlit removes widget state. Use the page's saved
    # parameters on return rather than replaying an obsolete imported draft.
    last_frame = st.session_state.get(last_frame_key)
    saved_matches = last_frame is not None and (
        as_table(frame, dimension).reset_index(drop=True).round(10).equals(
            as_table(last_frame, dimension).reset_index(drop=True).round(10))
    )
    if draft is not None and editor_key not in st.session_state and not st.session_state.get(pending_key) and saved_matches:
        st.session_state.pop(state_key, None)
        draft = None
    source = draft if draft is not None else frame
    edited = st.data_editor(source, key=editor_key, disabled=disabled, **editor_kwargs)
    st.session_state[last_frame_key] = edited.copy()
    st.session_state[pending_key] = False
    with st.expander("Quoten mit Excel bearbeiten", expanded=False):
        st.caption("Vorlage herunterladen → Quoten in Excel ändern → hochladen → übernehmen. "
                   "Alle Angaben gelten pro Jahr: 3 = 3 %, 0,5 = 0,5 %.")
        detailed_required = has_age_differences(edited, dimension)
        mode = st.radio("Vorlage", ["Eine Quote je Gruppe", "Quoten nach Altersgruppe"],
                        index=1 if detailed_required else 0,
                        key=f"{key}_excel_mode_{detailed_required}", horizontal=True, disabled=disabled)
        detailed = mode == "Quoten nach Altersgruppe"
        if detailed_required and not detailed:
            st.info("Bei Gruppen mit unterschiedlichen Altersquoten bleibt die einfache Quote in Excel leer. "
                    "Tragen Sie dort die gewünschte Jahresquote ein; sie gilt anschließend für alle Altersgruppen.")
        st.download_button("Excel-Vorlage herunterladen",
                           data=export_quit_template(edited, dimension, detailed),
                           file_name=f"Kuendigungsquoten_{dimension}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key=f"{key}_excel_download", disabled=disabled)
        st.caption("Nur enthaltene Gruppen werden aktualisiert. Andere Werte bleiben erhalten. "
                   "Gruppennamen unverändert lassen und nicht benötigte Zeilen vollständig entfernen.")
        upload = st.file_uploader("Ausgefüllte Excel-Datei hochladen", type=["xlsx"],
                                  key=f"{key}_excel_upload", disabled=disabled)
        if upload is not None and not disabled:
            try:
                names = as_table(edited, dimension)[dimension].astype(str).tolist()
                updates = parse_quit_upload(upload.getvalue(), dimension, names)
                preview = preview_quit_updates(edited, updates, dimension)
            except ValueError as exc:
                st.error(str(exc))
            else:
                if preview.empty:
                    st.info("Die Datei enthält keine Änderungen gegenüber der angezeigten Matrix.")
                else:
                    count = preview[dimension].nunique()
                    st.write(f"{count} Gruppe(n) werden geändert. Die Vorschau zeigt nur geänderte Quoten.")
                    st.dataframe(preview, hide_index=True, use_container_width=True)
                    if st.button("Quoten übernehmen", key=f"{key}_excel_apply", type="primary"):
                        st.session_state[state_key] = merge_quit_updates(edited, updates, dimension)
                        st.session_state[revision_key] = revision + 1
                        st.session_state[pending_key] = True
                        st.session_state[f"{key}_excel_applied"] = True
                        st.rerun()
        if st.session_state.get(f"{key}_excel_applied"):
            st.success("Quoten in die Matrix übernommen. Anschließend wie gewohnt berechnen bzw. Parameter speichern.")
    return edited
