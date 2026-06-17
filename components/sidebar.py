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
import html

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DEFAULT_COHORTS, COLORS, DATA_PATH, BASE_DIR
from dataloader.source_service import SourceService, DataSourceOrigin
from components.ui_shell import inject_ui_theme
from utils.i18n import (
    get_language,
    get_language_name,
    get_next_language,
    initialize_language_state,
    t,
    toggle_language,
)
from utils.text_normalization import normalize_dashboard_text

GLOBAL_METRIC_OPTIONS = ["Köpfe", "MAK", "EUR"]
_GLOBAL_METRIC_ALIASES = {
    "Köpfe": "Köpfe",
    "Koepfe": "Köpfe",
    "Kopfe": "Köpfe",
    "MAK": "MAK",
    "EUR": "EUR",
    "Euro": "EUR",
}


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
            return f"{base} ({t('sidebar.value.all')})"
        return f"{base} ({t('sidebar.value.selected_count', count=n)})"

    # If total unknown, use "Alle" when none selected
    if n == 0:
        return f"{base} ({t('sidebar.value.all')})"
    return f"{base} ({t('sidebar.value.selected_count', count=n)})"


def _segmented_single(label: str, options: list[str], value: str, key: str):
    """
    Streamlit >= 1.31: st.segmented_control
    Fallback: st.radio
    """
    if hasattr(st, "segmented_control"):
        widget_kwargs = {
            "key": key,
            "label_visibility": "collapsed",
        }
        if key not in st.session_state and value in options:
            widget_kwargs["default"] = value
        return st.segmented_control(
            label,
            options=options,
            **widget_kwargs,
        )

    radio_kwargs = {
        "horizontal": True,
        "key": key,
        "label_visibility": "collapsed",
    }
    if key not in st.session_state and value in options:
        radio_kwargs["index"] = options.index(value)
    return st.radio(
        label,
        options=options,
        **radio_kwargs,
    )


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
        if st.button(t("sidebar.action.select_all"), key=select_all_key, use_container_width=True):
            on_select_all()
            st.rerun()
    with c2:
        if st.button(t("sidebar.action.reset"), key=reset_key, use_container_width=True):
            on_reset()
            st.rerun()


def render_language_switcher() -> None:
    """Render the global language switcher at the bottom of the sidebar."""
    initialize_language_state()
    current_language = get_language()
    next_language = get_next_language(current_language)

    st.markdown(f"### 🌐 {t('sidebar.language.section')}")
    st.caption(
        t(
            "sidebar.language.current",
            language_name=get_language_name(current_language),
        )
    )
    if st.button(
        t(
            "sidebar.language.toggle",
            language_name=get_language_name(next_language),
        ),
        key="global_language_switch",
        use_container_width=True,
    ):
        toggle_language()
        st.rerun()


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

    return _multiselect_with_placeholder(
        label,
        options=options,
        default=default,
        key=key,
        label_visibility="collapsed",
    )


def _multiselect_with_placeholder(label: str, options: list, default=None, **kwargs):
    """Render a multiselect with localized placeholder where supported."""
    placeholder = kwargs.pop("placeholder", t("sidebar.multiselect.placeholder"))
    try:
        return st.multiselect(
            label,
            options=options,
            default=default,
            placeholder=placeholder,
            **kwargs,
        )
    except TypeError:
        return st.multiselect(
            label,
            options=options,
            default=default,
            **kwargs,
        )


def _localized_working_time_option(value: str) -> str:
    labels = {
        "Vollzeit": t("sidebar.option.full_time"),
        "Teilzeit": t("sidebar.option.part_time"),
        "Inaktiv": t("sidebar.option.inactive"),
    }
    return labels.get(value, value)


def _localized_atz_option(value: str) -> str:
    labels = {
        "Kein ATZ": t("sidebar.option.atz.none"),
        "Arbeitsphase": t("sidebar.option.atz.work_phase"),
        "Freistellungsphase": t("sidebar.option.atz.release_phase"),
    }
    return labels.get(value, value)


def _render_sidebar_heading(text: str):
    st.markdown(f'<div class="dashboard-sidebar-heading">{html.escape(text)}</div>', unsafe_allow_html=True)


def _render_sidebar_section(text: str):
    st.markdown(f'<div class="dashboard-sidebar-section">{html.escape(text)}</div>', unsafe_allow_html=True)


def _render_sidebar_summary(text: str):
    st.markdown(
        f'<div class="dashboard-sidebar-summary">{html.escape(text)}</div>',
        unsafe_allow_html=True,
    )


def _render_sidebar_note(text: str):
    st.markdown(
        f'<div class="dashboard-sidebar-note">{html.escape(text)}</div>',
        unsafe_allow_html=True,
    )


def _render_sidebar_caption(text: str):
    st.caption(text)


def _render_sidebar_block_intro(title: str, caption: str | None = None, icon: str | None = None):
    heading = f"{icon} {title}" if icon else title
    _render_sidebar_heading(heading)
    if caption:
        _render_sidebar_caption(caption)


def render_data_status(show_title: bool = True):
    """Renders the structured data status in an ultra-compact Ampel-format."""
    uploads = st.session_state.get("global_uploads", {})
    original_dir = os.path.join(BASE_DIR, "..", "Original-Daten")
    cluster_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "Cluster-Daten"))
    
    group_labels = {
        "Mitarbeiterinformationen": t("sidebar.data_group.staff"),
        "Entgeltinformationen": t("sidebar.data_group.compensation"),
        "Clusterinformationen": t("sidebar.data_group.cluster"),
    }

    # Ampel-Logic Mapping
    ampel_cfg = {
        DataSourceOrigin.UPLOADED: ("#10b981", t("sidebar.data_origin.upload")),
        DataSourceOrigin.ORIGINAL: ("#f59e0b", t("sidebar.data_origin.standard")),
        DataSourceOrigin.SYNTHETIC: ("#ef4444", t("sidebar.data_origin.synthetic")),
    }

    # Start HTML block
    html_parts = ["<div style='margin-bottom: 8px;'>"]
    if show_title:
        html_parts.append(
            f"<div style='margin: 0 0 4px 0; font-size: 13px; font-weight: bold;'>📊 {html.escape(t('sidebar.data_status.title'))}</div>"
        )
    html_parts.append("<div style='border-left: 2px solid #e5e7eb; padding-left: 8px;'>")
    
    for group_name in SourceService.GROUPS.keys():
        status = SourceService.derive_group_status(
            group_name,
            uploads,
            original_dir,
            cluster_dir,
            session_state=st.session_state,
        )
        color, short_origin = ampel_cfg[status.origin]
        
        # Build compact row without leading whitespace to avoid markdown code blocks
        row = (
            f"<div style='display: flex; align-items: center; gap: 6px; line-height: 1.1; margin-bottom: 2px; font-size: 11px;'>"
            f"<span style='height: 8px; width: 8px; background-color: {color}; border-radius: 50%; display: inline-block; flex-shrink: 0;'></span>"
            f"<span style='font-weight: 500; color: #374151;'>{group_labels[group_name]}:</span>"
            f"<span style='color: #6b7280;' title='{status.completeness_label}'>{short_origin}</span>"
            f"</div>"
        )
        html_parts.append(row)

    html_parts.append("</div></div>")
    
    st.markdown("".join(html_parts), unsafe_allow_html=True)

    data_status_message = st.session_state.get("data_status_message")
    data_status_level = st.session_state.get("data_status_level", "info")
    if data_status_message:
        prefix = "⚠️ " if data_status_level == "warning" else "ℹ️ "
        _render_sidebar_note(f"{prefix}{data_status_message}")


def _sync_legacy_view_mode():
    """Keep the legacy MAK/EUR session key aligned for older consumers."""
    metric_view = normalize_global_metric_view(st.session_state.get("global_metric_view", "MAK"))
    if metric_view in ("MAK", "EUR"):
        st.session_state["view_mode"] = metric_view
    else:
        st.session_state["view_mode"] = "MAK"


def initialize_global_metric_view():
    """Initialize the global metric pill with backward compatibility."""
    if "global_metric_view" not in st.session_state:
        legacy_mode = st.session_state.get("view_mode", "MAK")
        if legacy_mode in GLOBAL_METRIC_OPTIONS:
            st.session_state["global_metric_view"] = legacy_mode
        elif legacy_mode == "Euro":
            st.session_state["global_metric_view"] = "EUR"
        else:
            st.session_state["global_metric_view"] = "MAK"
    st.session_state["global_metric_view"] = normalize_global_metric_view(
        st.session_state.get("global_metric_view", "MAK")
    ) or "MAK"
    _sync_legacy_view_mode()


def render_global_metric_selector():
    """Render the native 3-state metric pill in the sidebar."""
    inject_ui_theme()
    initialize_global_metric_view()
    metric_view = _segmented_single(
        t("sidebar.metric.label"),
        options=GLOBAL_METRIC_OPTIONS,
        value=st.session_state.get("global_metric_view", "MAK"),
        key="global_metric_view_toggle",
    )
    st.session_state["global_metric_view"] = metric_view
    _sync_legacy_view_mode()
    page_hint = st.session_state.get("metric_page_hint")
    if page_hint:
        _render_sidebar_note(page_hint)
    return metric_view


def get_global_metric_view(default: str = "MAK") -> str:
    """Return the currently selected global metric view."""
    initialize_global_metric_view()
    return normalize_global_metric_view(st.session_state.get("global_metric_view", default)) or default


def normalize_global_metric_view(metric_view: str | None) -> str | None:
    """Normalize known metric aliases to the canonical global sidebar values."""
    if metric_view is None:
        return None
    text = normalize_dashboard_text(str(metric_view)).strip()
    if not text:
        return text
    return _GLOBAL_METRIC_ALIASES.get(text, text)


def get_effective_metric_view(
    supported_views: list[str] | tuple[str, ...],
    fallback: str | None = None,
) -> tuple[str, str | None]:
    """
    Resolve the global metric view against the subset a page supports.
    """
    selected_view = get_global_metric_view()
    supported = list(supported_views)
    if not supported:
        return selected_view, None

    if selected_view in supported:
        return selected_view, None

    effective = fallback or ("MAK" if "MAK" in supported else supported[0])
    supported_label = " / ".join(supported)
    hint = (
        f"Die globale Ansicht ist auf `{selected_view}` gesetzt. "
        f"Diese Seite unterstützt derzeit `{supported_label}`. "
        f"Angezeigt wird daher `{effective}`."
    )
    return effective, hint


def render_metric_hint(
    *,
    supported_views: list[str] | tuple[str, ...],
    page_scope: str,
    note: str | None = None,
    fallback: str | None = None,
) -> str:
    """
    Render a page-specific note below the title if the selected view is not
    fully available on the current page. Returns the effective view.
    """
    effective_view, hint = get_effective_metric_view(supported_views, fallback=fallback)
    if hint:
        extra = f" {note}" if note else ""
        st.info(t("sidebar.hint.view_scope", page_scope=page_scope, hint=hint, extra=extra))
    elif note:
        st.info(t("sidebar.hint.view_scope", page_scope=page_scope, hint=note, extra=""))
    return effective_view


def set_metric_page_hint(hint_text: str | None):
    """Set or clear the page-specific note shown below the global metric pill."""
    if hint_text:
        st.session_state["metric_page_hint"] = hint_text
    else:
        st.session_state.pop("metric_page_hint", None)


def render_metric_selector_only(caption_text: str | None = None):
    """
    Render only the global metric pill in the sidebar. Useful on pages without
    the full filter sidebar.
    """
    inject_ui_theme()
    initialize_language_state()
    initialize_global_metric_view()
    with st.sidebar:
        _render_sidebar_block_intro(
            t("sidebar.view.section"),
            t("sidebar.view.caption"),
            icon="💡",
        )
        render_global_metric_selector()
        if caption_text:
            _render_sidebar_note(caption_text)
        st.markdown("---")
        render_language_switcher()


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
    inject_ui_theme()
    initialize_language_state()

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

    initialize_global_metric_view()

    with st.sidebar:
        # ── Zone 1: Kennzahl ────────────────────────────────────────────
        st.markdown(f"**{t('sidebar.metric.label')}**")
        render_global_metric_selector()
        st.caption("Die gewählte Kennzahl beeinflusst die darunterliegenden Analysen.")

        st.divider()

        # ── Zone 2: Filter ──────────────────────────────────────────────
        _render_sidebar_heading(t("sidebar.primary_filters.section"))

        # Organisationseinheiten
        # FIX: Use Organisationseinheit (full name) as key, since Kürzel OrgEinheit
        # is NOT unique (e.g. 591 = "Beratungs-Center Herrenberg" AND "Akquisepool Herrenberg").
        st.markdown(f"**{t('sidebar.label.org_units')}**")
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

        selected_orgs = _multiselect_with_placeholder(
            t("sidebar.label.org_units_select"),
            options=org_units,
            default=st.session_state.get("selected_org_units", []),
            format_func=lambda x: org_unit_display.get(x, x),
            key="org_units_select",
            label_visibility="collapsed",
        )
        st.session_state["selected_org_units"] = selected_orgs

        # Arbeitszeit
        st.markdown(f"**{t('sidebar.label.working_time')}**")
        selected_employment = _multiselect_with_placeholder(
            t("sidebar.label.working_time_select"),
            options=["Vollzeit", "Teilzeit", "Inaktiv"],
            default=st.session_state.get("selected_employment", ["Vollzeit", "Teilzeit", "Inaktiv"]),
            format_func=_localized_working_time_option,
            key="employment_select",
            label_visibility="collapsed",
        )
        st.session_state["selected_employment"] = selected_employment

        # Geschlecht ausgeblendet — beide Geschlechter immer aktiv
        st.session_state["selected_genders"] = ["m", "w"]

        # Job Families (collapsible)
        with st.expander(t("sidebar.label.job_families"), expanded=False):
            if "Jobfamily" in snapshot_df.columns:
                jobfamilies = sorted(
                    snapshot_df[snapshot_df["Jobfamily"] != "UNMAPPED"]["Jobfamily"].dropna().unique()
                )
            else:
                jobfamilies = []

            c1, c2 = st.columns(2)
            with c1:
                if st.button(t("sidebar.action.select_all"), key="jf_select_all", use_container_width=True):
                    st.session_state["selected_jobfamilies"] = jobfamilies.copy()
                    st.rerun()
            with c2:
                if st.button(t("sidebar.action.reset"), key="jf_reset", use_container_width=True):
                    st.session_state["selected_jobfamilies"] = []
                    st.rerun()

            selected_jf = _multiselect_with_placeholder(
                t("sidebar.label.job_families_select"),
                options=jobfamilies,
                default=st.session_state.get("selected_jobfamilies", []),
                key="jobfamily_select",
                label_visibility="collapsed",
            )
            st.session_state["selected_jobfamilies"] = selected_jf

        # Alterskohorten (collapsible)
        with st.expander(t("sidebar.label.age_cohorts"), expanded=False):
            cohorts = list(st.session_state["cohort_definitions"].keys())

            c1, c2 = st.columns(2)
            with c1:
                if st.button(t("sidebar.action.select_all"), key="cohorts_select_all", use_container_width=True):
                    st.session_state["selected_cohorts"] = cohorts.copy()
                    st.rerun()
            with c2:
                if st.button(t("sidebar.action.reset"), key="cohorts_reset", use_container_width=True):
                    st.session_state["selected_cohorts"] = []
                    st.rerun()

            selected_cohorts = _multiselect_with_placeholder(
                t("sidebar.label.cohorts_select"),
                options=cohorts,
                default=st.session_state.get("selected_cohorts", []),
                key="cohorts_select",
                label_visibility="collapsed",
            )
            st.session_state["selected_cohorts"] = selected_cohorts

            # Kohorten-Editor (Konfiguration, kein täglicher Filter)
            if hasattr(st, "popover"):
                with st.popover(f"⚙️ {t('sidebar.action.edit_cohorts')}", use_container_width=True):
                    render_cohort_editor()
            else:
                with st.expander(f"⚙️ {t('sidebar.action.edit_cohorts')}"):
                    render_cohort_editor()

        # Weitere Filter ausgeblendet — neutrale Defaults damit keine stille Filterung aktiv ist
        st.session_state["selected_oe_clusters"] = []
        st.session_state["selected_jf_clusters"] = []
        st.session_state["selected_education"] = []
        st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]

        # Aktive Filter (kompakt, einklappbar)
        with st.expander(t("sidebar.active_selection.section"), expanded=False):
            _render_sidebar_summary(get_filter_summary())

        st.divider()

        # ── Zone 3: Datenstatus ─────────────────────────────────────────
        render_data_status(show_title=True)

        st.divider()

        # ── Zone 4: Aktionen ────────────────────────────────────────────
        _render_sidebar_heading(t("sidebar.actions.section"))

        if st.button(f"🔄 {t('sidebar.action.reset_all_filters')}", use_container_width=True):
            reset_filters()
            st.rerun()

        render_language_switcher()

        # Debug Mode Toggle
        if st.checkbox(f"🐞 {t('sidebar.label.debug_mode')}", value=False, key="debug_mode"):
            st.session_state["debug_active"] = True
        else:
            st.session_state["debug_active"] = False


def render_cohort_editor():
    """
    Editor für Alterskohorten-Definitionen.
    Speichert Änderungen in Session State.
    """
    st.write(f"**{t('sidebar.label.cohort_definitions')}**")

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
        st.info(f"✓ {t('sidebar.hint.cohorts_updated')}")

    # Reset zu Defaults
    if st.button(t("sidebar.action.reset_default_cohorts"), key="reset_cohorts"):
        st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
        st.success(f"✓ {t('sidebar.hint.cohorts_restored')}")
        st.rerun()


def _normalize_filter_values(values) -> list[str]:
    if not values:
        return []
    return [re.sub(r"\.0$", "", str(v).strip()) for v in values]


_EVENT_FILTER_DIMENSIONS = [
    "Organisationseinheit",
    "Kürzel OrgEinheit",
    "OE-Cluster",
    "Jobfamily",
    "JF-Cluster",
    "Alterskohorte",
    "Geschlecht",
    "Arbeitszeit",
    "Ausbildung",
    "ATZ_Status",
    "Planstelle",
    "TrfGr",
    "St",
]
_EVENT_METRIC_CONTEXT_COLUMNS = ["MAK", "MAK_Calculated", "mak"]
_EVENT_UNMAPPED = "UNMAPPED"


def _normalize_person_id_for_events(series: pd.Series) -> pd.Series:
    normalized = (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )
    return normalized.map(lambda value: value.zfill(6) if value.isdigit() else value)


def _fill_missing_event_dimension_values(series: pd.Series) -> pd.Series:
    out = series.copy()
    missing = (
        out.isna()
        | out.astype(str).str.strip().isin(["", "nan", "NaN", "None", "<NA>"])
    )
    return out.where(~missing, _EVENT_UNMAPPED)


def _enrich_event_filter_dimensions(
    events_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Ergänzt Eventzeilen vor der Filterung um Stammdaten-Dimensionen.

    Abgangsevents entstehen an verschiedenen Stellen der Forecast-Engine. Nicht
    jeder Eventtyp trägt dort bereits alle Dashboard-Filterdimensionen. Für die
    View-Filter muss deshalb vor dem direkten Dimensionsfilter per PersNr
    konsistent angereichert werden.
    """
    result = events_df.copy()

    for col in _EVENT_FILTER_DIMENSIONS + _EVENT_METRIC_CONTEXT_COLUMNS:
        if col not in result.columns:
            result[col] = pd.NA

    if (
        result.empty
        or snapshot_df is None
        or snapshot_df.empty
        or "persnr" not in result.columns
        or "PersNr" not in snapshot_df.columns
    ):
        for col in _EVENT_FILTER_DIMENSIONS:
            result[col] = _fill_missing_event_dimension_values(result[col])
        return result

    source = snapshot_df.copy()
    source["_event_persnr"] = _normalize_person_id_for_events(source["PersNr"])

    if "MAK" not in source.columns:
        for fallback_col in ["MAK_Calculated", "mak"]:
            if fallback_col in source.columns:
                source["MAK"] = source[fallback_col]
                break

    lookup_cols = [
        col
        for col in _EVENT_FILTER_DIMENSIONS + _EVENT_METRIC_CONTEXT_COLUMNS
        if col in source.columns
    ]
    if not lookup_cols:
        for col in _EVENT_FILTER_DIMENSIONS:
            result[col] = _fill_missing_event_dimension_values(result[col])
        return result

    lookup = (
        source[["_event_persnr"] + lookup_cols]
        .drop_duplicates("_event_persnr")
        .set_index("_event_persnr")
    )
    event_ids = _normalize_person_id_for_events(result["persnr"])

    for col in lookup_cols:
        mapped = event_ids.map(lookup[col])
        missing = (
            result[col].isna()
            | result[col].astype(str).str.strip().isin(["", "nan", "NaN", "None", "<NA>"])
        )
        result.loc[missing, col] = mapped.loc[missing]

    for col in _EVENT_FILTER_DIMENSIONS:
        result[col] = _fill_missing_event_dimension_values(result[col])

    return result


def _apply_filters_uncached(df: pd.DataFrame, active_filters: dict | None = None) -> pd.DataFrame:
    if df.empty:
        return df

    filtered = df.copy()
    filters = active_filters or {}

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
        filter_values = filters.get(state_key)
        if filter_values and column_name in filtered.columns:
            s_norm = filtered[column_name].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            mask = s_norm.isin(_normalize_filter_values(filter_values))
            filtered = filtered.loc[mask]

    return filtered


@st.cache_data
def filter_dataframe_by_view_filters(df: pd.DataFrame, active_filters: dict | None = None) -> pd.DataFrame:
    """Pure, cacheable dataframe filtering for the current sidebar view state."""
    return _apply_filters_uncached(df, active_filters)


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wendet alle aktiven Filter auf den DataFrame an.

    Args:
        df: Snapshot DataFrame

    Returns:
        Gefilterter DataFrame
    """
    return filter_dataframe_by_view_filters(df, get_active_view_filters())
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
        active_filters.append(
            t("sidebar.summary.org_units", count=len(st.session_state["selected_org_units"]))
        )

    if st.session_state.get("selected_jobfamilies"):
        active_filters.append(
            t("sidebar.summary.job_families", count=len(st.session_state["selected_jobfamilies"]))
        )

    if st.session_state.get("selected_cohorts"):
        active_filters.append(
            t("sidebar.summary.cohorts", count=len(st.session_state["selected_cohorts"]))
        )

    if len(st.session_state.get("selected_genders", [])) < 2:
        active_filters.append(t("sidebar.summary.gender"))

    if len(st.session_state.get("selected_employment", [])) < 2:
        active_filters.append(t("sidebar.summary.working_time"))

    if st.session_state.get("selected_education"):
        active_filters.append(t("sidebar.summary.education"))

    if len(st.session_state.get("selected_atz_status", [])) < 3:
        active_filters.append(t("sidebar.summary.atz"))

    if st.session_state.get("selected_oe_clusters"):
        active_filters.append(
            t("sidebar.summary.oe_clusters", count=len(st.session_state["selected_oe_clusters"]))
        )

    if st.session_state.get("selected_jf_clusters"):
        active_filters.append(
            t("sidebar.summary.jf_clusters", count=len(st.session_state["selected_jf_clusters"]))
        )

    # Gruppen-Ausschlüsse (Neu: Aus JSON-Config)
    exclusion_labels = []
    if ex_config.get("vorstand"):
        exclusion_labels.append(t("sidebar.summary.exclusion_board"))
    if ex_config.get("ruhend_bv"):
        exclusion_labels.append(t("sidebar.summary.exclusion_dormant"))
    
    ex_units = ex_config.get("org_units", [])
    if ex_units:
        exclusion_labels.append(t("sidebar.summary.exclusion_areas", count=len(ex_units)))
        
    if exclusion_labels:
        active_filters.append(
            t("sidebar.summary.exclusions", labels=", ".join(exclusion_labels))
        )

    if active_filters:
        return t(
            "sidebar.summary.filter_prefix",
            count=len(active_filters),
            details=", ".join(active_filters),
        )
    return t("sidebar.summary.all_data")


# ---------------------------------------------------------------------------
# Zentrale Event-Filter-Funktionen (View-Only Zoom)
# ---------------------------------------------------------------------------

def apply_robust_filter(df: pd.DataFrame, column: str, selected: list) -> pd.DataFrame:
    """
    Apply a single column filter with robust type normalization.

    Handles mixed types (612 vs 612.0), whitespace, and trailing .0 floats.
    Returns the original DataFrame unchanged if *selected* is empty or the
    column does not exist.
    """
    if not selected or column not in df.columns:
        return df
    s_norm = df[column].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    v_norm = [str(v).strip().replace(".0", "") for v in selected]
    return df[s_norm.isin(v_norm)]


def _apply_event_filters_uncached(
    events_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    *,
    active_filters: dict | None = None,
    mode: str = "accession",
) -> tuple:
    if events_df.empty:
        return events_df, 0, 0

    n_before = len(events_df)
    result = _enrich_event_filter_dimensions(events_df, snapshot_df)
    filters = active_filters or {}

    result = apply_robust_filter(result, "Organisationseinheit", filters.get("selected_org_units", []))
    result = apply_robust_filter(result, "Jobfamily", filters.get("selected_jobfamilies", []))
    result = apply_robust_filter(result, "OE-Cluster", filters.get("selected_oe_clusters", []))
    result = apply_robust_filter(result, "JF-Cluster", filters.get("selected_jf_clusters", []))

    if snapshot_df is None or snapshot_df.empty:
        return result, n_before, len(result)

    filtered_snapshot = filter_dataframe_by_view_filters(snapshot_df, filters)
    if filtered_snapshot.empty:
        return pd.DataFrame(columns=result.columns), n_before, 0

    valid_ids = set(_normalize_person_id_for_events(filtered_snapshot["PersNr"]))

    if "persnr" not in result.columns:
        return result, n_before, len(result)

    pid_clean = _normalize_person_id_for_events(result["persnr"])

    if mode == "attrition":
        result = result[pid_clean.isin(valid_ids)]
    else:
        all_existing_ids = set(_normalize_person_id_for_events(snapshot_df["PersNr"]))
        mask_is_filtered_existing = pid_clean.isin(valid_ids)
        mask_is_new_hire = ~pid_clean.isin(all_existing_ids)
        result = result[mask_is_filtered_existing | mask_is_new_hire]

    n_after = len(result)
    return result, n_before, n_after


@st.cache_data
def apply_event_filters_with_state(
    events_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    *,
    active_filters: dict | None = None,
    mode: str = "accession",
) -> tuple:
    """Pure, cacheable event filtering for forecast views."""
    return _apply_event_filters_uncached(
        events_df,
        snapshot_df,
        active_filters=active_filters,
        mode=mode,
    )


def apply_event_filters(
    events_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    *,
    mode: str = "accession",
) -> tuple:
    """
    Apply all active sidebar view-filters to an events DataFrame.

    This is the single entry-point for filtering forecast events by the
    current sidebar selection.  It combines:
      1. Attribute filters (OrgUnit, JF, OE-Cluster, JF-Cluster) applied
         directly on event columns.
      2. Demographic / PersNr-based filters (gender, employment, ATZ,
         cohort, education) resolved via the filtered snapshot.

    Args:
        events_df:    Raw (unfiltered) events from the forecast engine.
        snapshot_df:  Full (unfiltered) snapshot for PersNr matching.
        mode:
            ``"accession"`` – preserves new-hire events whose PersNr does
            not appear in the original snapshot (Zugänge behaviour).
            ``"attrition"`` – restricts **all** events to PersNr values
            present in the filtered snapshot (Abgänge behaviour).

    Returns:
        ``(filtered_df, n_before, n_after)``
    """
    return apply_event_filters_with_state(
        events_df,
        snapshot_df,
        active_filters=get_active_view_filters(),
        mode=mode,
    )
    if events_df.empty:
        return events_df, 0, 0

    n_before = len(events_df)
    result = events_df.copy()

    # 1. Attribute filters (direct on event columns)
    result = apply_robust_filter(result, "Organisationseinheit",
                                 st.session_state.get("selected_org_units", []))
    result = apply_robust_filter(result, "Jobfamily",
                                 st.session_state.get("selected_jobfamilies", []))
    result = apply_robust_filter(result, "OE-Cluster",
                                 st.session_state.get("selected_oe_clusters", []))
    result = apply_robust_filter(result, "JF-Cluster",
                                 st.session_state.get("selected_jf_clusters", []))

    # 2. Demographic / PersNr-based filters
    if snapshot_df is None or snapshot_df.empty:
        return result, n_before, len(result)

    filtered_snapshot = apply_filters(snapshot_df)
    if filtered_snapshot.empty:
        return pd.DataFrame(columns=result.columns), n_before, 0

    valid_ids = set(
        filtered_snapshot["PersNr"].astype(str)
        .str.strip().str.replace(r"\.0$", "", regex=True)
    )

    if "persnr" not in result.columns:
        return result, n_before, len(result)

    pid_clean = (
        result["persnr"].astype(str)
        .str.strip().str.replace(r"\.0$", "", regex=True)
    )

    if mode == "attrition":
        # Restrict ALL events to persons in the filtered snapshot
        result = result[pid_clean.isin(valid_ids)]
    else:
        # Accession: keep filtered-existing OR genuinely new hires
        all_existing_ids = set(
            snapshot_df["PersNr"].astype(str)
            .str.strip().str.replace(r"\.0$", "", regex=True)
        )
        mask_is_filtered_existing = pid_clean.isin(valid_ids)
        mask_is_new_hire = ~pid_clean.isin(all_existing_ids)
        result = result[mask_is_filtered_existing | mask_is_new_hire]

    n_after = len(result)
    return result, n_before, n_after


def render_filter_status(n_before: int, n_after: int) -> None:
    """Show a compact filter-status caption in the UI."""
    summary = get_filter_summary()
    if n_before == n_after:
        st.caption(f"🔍 {t('sidebar.hint.filter_unchanged', summary=summary)}")
    else:
        st.caption(
            f"🔍 {t('sidebar.hint.filter_changed', before=n_before, after=n_after, summary=summary)}"
        )


def get_active_view_filters(state: dict | None = None) -> dict:
    """
    Return a dict of currently active view-filter values.

    Pure function – pass an explicit *state* dict for unit-testing without
    Streamlit.  When *state* is ``None`` the function reads from
    ``st.session_state``.
    """
    if state is None:
        state = dict(st.session_state)
    return {
        "selected_org_units": state.get("selected_org_units", []),
        "selected_jobfamilies": state.get("selected_jobfamilies", []),
        "selected_oe_clusters": state.get("selected_oe_clusters", []),
        "selected_jf_clusters": state.get("selected_jf_clusters", []),
        "selected_genders": state.get("selected_genders", []),
        "selected_employment": state.get("selected_employment", []),
        "selected_atz_status": state.get("selected_atz_status", []),
        "selected_cohorts": state.get("selected_cohorts", []),
        "selected_education": state.get("selected_education", []),
    }
