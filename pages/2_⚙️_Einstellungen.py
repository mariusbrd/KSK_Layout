"""
Modul 8: Einstellungen

Konfigurationsseite für Loader-spezifische Parameter wie
Sonderfall-Gehälter (Azubis, Vorstand) und Arbeitgeber-Kostenfaktor.
"""

import io
import streamlit as st
import pandas as pd
import sys
import os
from datetime import datetime, timezone

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    COLORS, EMPLOYER_COST_FACTOR,
    DEFAULT_AZUBI_JAHRESGEHALT, DEFAULT_VORSTAND_JAHRESGEHALT,
    format_currency,
)
from kpi_reference import STICHTAG_DEFAULT
from dataloader.loader import load_and_prepare_data
from dataloader.jobfamily_matcher import load_jobfamily_definitions
from dataloader.cluster_manager import (
    delete_persisted_cluster_upload,
    generate_template_bytes,
    persist_cluster_upload_bytes,
    validate_cluster_upload,
)
from dataloader.cluster_resolver import (
    SUBTYPE_INPUT_EXTERNAL,
    SUBTYPE_SYNTHETIC_FALLBACK,
    SUBTYPE_UI_UPLOAD_PERSISTED,
    clear_active_cluster_source_from_session,
    get_active_cluster_source,
    invalidate_cluster_dependent_state,
    store_active_cluster_source_in_session,
)
from dataloader.source_service import SourceService, DataSourceOrigin
from config.settings import BASE_DIR
from components.sidebar import render_metric_selector_only, set_metric_page_hint
from utils.cache_utils import bump_cache_version
from utils.i18n import t


SALARY_AUTOMATION_DEFAULTS = {
    "enabled": False,
    "scope": "new_hires_only",
    "fallback_step": 4,
    "e1_entry_step": 2,
    "e2_plus_default_entry_step": 1,
    "e1_progression_years": [4, 4, 4, 4],
    "e2_plus_progression_years": [1, 2, 3, 4, 5],
    "use_tenure_as_step_proxy_for_existing_staff": False,
}

CLUSTER_STAGED_KEYS = (
    "cluster_upload_staged_bytes",
    "cluster_upload_staged_filename",
    "cluster_upload_staged_hash",
    "cluster_upload_staged_valid",
    "cluster_upload_staged_errors",
    "cluster_upload_staged_uploaded_at",
    "cluster_upload_staged_oe_mapping_count",
    "cluster_upload_staged_jf_mapping_count",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_global_uploads() -> dict:
    if "global_uploads" not in st.session_state or not isinstance(st.session_state.get("global_uploads"), dict):
        st.session_state["global_uploads"] = {}
    return st.session_state["global_uploads"]


def _set_cluster_feedback(level: str, message: str) -> None:
    st.session_state["cluster_action_level"] = level
    st.session_state["cluster_action_message"] = message


def _render_cluster_feedback() -> None:
    level = st.session_state.pop("cluster_action_level", None)
    message = st.session_state.pop("cluster_action_message", None)
    if not message:
        return
    if level == "error":
        st.error(message)
    elif level == "warning":
        st.warning(message)
    elif level == "info":
        st.info(message)
    else:
        st.success(message)


def _clear_staged_cluster_state() -> None:
    for key in CLUSTER_STAGED_KEYS:
        st.session_state.pop(key, None)


def _get_staged_cluster_state() -> dict:
    return {
        "bytes": st.session_state.get("cluster_upload_staged_bytes"),
        "filename": st.session_state.get("cluster_upload_staged_filename"),
        "hash": st.session_state.get("cluster_upload_staged_hash"),
        "is_valid": bool(st.session_state.get("cluster_upload_staged_valid", False)),
        "errors": list(st.session_state.get("cluster_upload_staged_errors", []) or []),
        "uploaded_at": st.session_state.get("cluster_upload_staged_uploaded_at"),
        "oe_mapping_count": int(st.session_state.get("cluster_upload_staged_oe_mapping_count", 0) or 0),
        "jf_mapping_count": int(st.session_state.get("cluster_upload_staged_jf_mapping_count", 0) or 0),
    }


def _refresh_active_cluster_source_state(
    *,
    persisted_local_path: str | None = None,
    external_file_path: str | None = None,
):
    active_source = get_active_cluster_source(
        session_state=st.session_state,
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )
    store_active_cluster_source_in_session(st.session_state, active_source)
    if active_source.subtype == SUBTYPE_UI_UPLOAD_PERSISTED:
        st.session_state["cluster_override_active"] = True
        st.session_state["cluster_override_activated_at"] = (
            st.session_state.get("cluster_override_activated_at")
            or active_source.activated_at
            or active_source.last_modified_at
        )
    else:
        st.session_state["cluster_override_active"] = False
    return active_source


def _stage_cluster_upload(
    uploaded_file,
    *,
    persisted_local_path: str | None = None,
    external_file_path: str | None = None,
) -> dict:
    if uploaded_file is None:
        return {"status": "no_upload"}

    validation = validate_cluster_upload(uploaded_file)
    uploaded_bytes = uploaded_file.getvalue()
    ignored_hash = st.session_state.get("cluster_upload_ignore_hash")
    if validation.content_hash and ignored_hash and validation.content_hash == ignored_hash:
        return {"status": "ignored_same_upload", "validation": validation}
    if validation.content_hash and ignored_hash and validation.content_hash != ignored_hash:
        st.session_state.pop("cluster_upload_ignore_hash", None)
    active_source = get_active_cluster_source(
        session_state=st.session_state,
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )

    if (
        validation.is_valid
        and active_source.subtype == SUBTYPE_UI_UPLOAD_PERSISTED
        and active_source.content_hash
        and active_source.content_hash == validation.content_hash
    ):
        _clear_staged_cluster_state()
        return {
            "status": "matches_active",
            "validation": validation,
            "active_source": active_source,
        }

    if validation.is_valid:
        st.session_state["cluster_upload_staged_bytes"] = uploaded_bytes
        st.session_state["cluster_upload_staged_filename"] = getattr(uploaded_file, "name", "Cluster-Upload.xlsx")
        st.session_state["cluster_upload_staged_hash"] = validation.content_hash
        st.session_state["cluster_upload_staged_valid"] = True
        st.session_state["cluster_upload_staged_errors"] = []
        st.session_state["cluster_upload_staged_uploaded_at"] = _now_iso()
        st.session_state["cluster_upload_staged_oe_mapping_count"] = validation.oe_mapping_count
        st.session_state["cluster_upload_staged_jf_mapping_count"] = validation.jf_mapping_count
        return {"status": "staged", "validation": validation, "active_source": active_source}

    st.session_state["cluster_upload_staged_bytes"] = None
    st.session_state["cluster_upload_staged_filename"] = getattr(uploaded_file, "name", "Cluster-Upload.xlsx")
    st.session_state["cluster_upload_staged_hash"] = validation.content_hash
    st.session_state["cluster_upload_staged_valid"] = False
    st.session_state["cluster_upload_staged_errors"] = list(validation.errors)
    st.session_state["cluster_upload_staged_uploaded_at"] = _now_iso()
    st.session_state["cluster_upload_staged_oe_mapping_count"] = validation.oe_mapping_count
    st.session_state["cluster_upload_staged_jf_mapping_count"] = validation.jf_mapping_count
    return {"status": "invalid", "validation": validation, "active_source": active_source}


def _apply_staged_cluster_upload(
    *,
    persisted_local_path: str | None = None,
    external_file_path: str | None = None,
) -> dict:
    staged = _get_staged_cluster_state()
    if not staged["bytes"] or not staged["is_valid"]:
        return {"success": False, "message": "Es liegt kein gueltiger staged Upload zum Anwenden vor."}

    persist_result = persist_cluster_upload_bytes(staged["bytes"], target_path=persisted_local_path)
    if not persist_result.get("success"):
        return {
            "success": False,
            "message": f"Cluster-Upload konnte nicht gespeichert werden: {persist_result.get('error', 'unbekannt')}",
            "persist_result": persist_result,
        }

    st.session_state["cluster_upload_ignore_hash"] = staged["hash"]
    st.session_state["cluster_override_active"] = True
    st.session_state["cluster_override_activated_at"] = persist_result.get("written_at") or _now_iso()
    st.session_state["active_cluster_source_mode"] = "ui_upload"
    st.session_state["active_cluster_source_subtype"] = SUBTYPE_UI_UPLOAD_PERSISTED

    invalidation = invalidate_cluster_dependent_state(st.session_state, reason="cluster_apply_now")
    _clear_staged_cluster_state()
    active_source = _refresh_active_cluster_source_state(
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )

    return {
        "success": True,
        "message": "Cluster-Upload wurde aktiviert und lokal gespeichert.",
        "persist_result": persist_result,
        "invalidation": invalidation,
        "active_source": active_source,
    }


def _delete_cluster_uploads(
    *,
    persisted_local_path: str | None = None,
    external_file_path: str | None = None,
) -> dict:
    staged = _get_staged_cluster_state()
    current_active = get_active_cluster_source(
        session_state=st.session_state,
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )
    if staged["hash"]:
        st.session_state["cluster_upload_ignore_hash"] = staged["hash"]
    elif current_active.content_hash:
        st.session_state["cluster_upload_ignore_hash"] = current_active.content_hash
    _clear_staged_cluster_state()

    delete_result = delete_persisted_cluster_upload(target_path=persisted_local_path)
    st.session_state["cluster_override_active"] = False
    st.session_state.pop("cluster_override_activated_at", None)
    clear_active_cluster_source_from_session(st.session_state)
    invalidation = invalidate_cluster_dependent_state(st.session_state, reason="cluster_delete_uploads")
    active_source = _refresh_active_cluster_source_state(
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )

    return {
        "success": delete_result.get("success", False),
        "delete_result": delete_result,
        "invalidation": invalidation,
        "active_source": active_source,
    }


def _render_active_cluster_source(active_source) -> None:
    st.markdown("**Aktive Clusterquelle**")
    st.markdown(f"- Modus: `{active_source.mode}`")
    st.markdown(f"- Subtyp: `{active_source.subtype}`")
    st.markdown(f"- Status: `{active_source.status}`")
    st.markdown(f"- Label: {active_source.display_label or '-'}")
    if active_source.filename or active_source.source_path:
        st.markdown(f"- Datei: `{active_source.filename or active_source.source_path}`")
    st.markdown(f"- OE-Mappings: `{active_source.oe_mapping_count}`")
    st.markdown(f"- JF-Mappings: `{active_source.jf_mapping_count}`")
    if active_source.subtype == SUBTYPE_UI_UPLOAD_PERSISTED:
        st.caption("Aktiv aus persistierter lokaler Kopie des UI-Uploads.")
    elif active_source.subtype == SUBTYPE_INPUT_EXTERNAL:
        st.caption("Aktiv aus externer Input-Ordner-Datei.")
    elif active_source.subtype == SUBTYPE_SYNTHETIC_FALLBACK:
        st.caption("Kein realer Clusterstand aktiv. Synthetic-Fallback ist aktiv.")
    elif active_source.source_path:
        st.caption(f"Quelle: {active_source.source_path}")


def _render_staged_cluster_state() -> None:
    staged = _get_staged_cluster_state()
    if not staged["filename"] and not staged["errors"]:
        return

    st.markdown("**Staged Upload**")
    st.markdown(f"- Datei: `{staged['filename'] or '-'}`")
    st.markdown(
        f"- Status: `{'gueltig, noch nicht aktiv' if staged['is_valid'] and staged['bytes'] else 'ungueltig / nicht aktiv'}`"
    )
    st.markdown(f"- OE-Mappings: `{staged['oe_mapping_count']}`")
    st.markdown(f"- JF-Mappings: `{staged['jf_mapping_count']}`")
    st.caption("Der staged Upload wird erst nach Klick auf 'Apply now' aktiv.")
    if staged["errors"]:
        for err in staged["errors"]:
            st.error(err)


def render_settings_page():
    _ensure_global_uploads()
    active_cluster_source = _refresh_active_cluster_source_state()

    set_metric_page_hint(
        t("settings.metric_hint")
    )
    render_metric_selector_only()

    st.title(t("settings.title"))
    st.caption(t("settings.subtitle"))
    _render_cluster_feedback()

    st.divider()

    # --- Success Message Helper ---
    if st.session_state.get("show_reload_success"):
        uploads = st.session_state.get("global_uploads", {})
        original_dir = os.path.join(BASE_DIR, "..", "Original-Daten")
        cluster_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "Cluster-Daten"))
        
        with st.container():
            st.success(f"✅ **{t('settings.reload_success')}**")
            
            diag_col1, diag_col2 = st.columns(2)
            
            with diag_col1:
                st.markdown(f"**{t('settings.data_sources_status')}**")
                for group in SourceService.GROUPS.keys():
                    if group == "Clusterinformationen":
                        st.markdown(
                            f"- **{group}**: {active_cluster_source.display_label} "
                            f"(`{active_cluster_source.subtype}`, {active_cluster_source.status})"
                        )
                    else:
                        status = SourceService.derive_group_status(group, uploads, original_dir, cluster_dir)
                        st.markdown(f"- **{group}**: {status.origin.value} ({status.completeness_label})")
            
            with diag_col2:
                st.markdown(f"**{t('settings.active_settings')}**")
                st.markdown(
                    f"- **{t('settings.cluster_mappings', oe=active_cluster_source.oe_mapping_count, jf=active_cluster_source.jf_mapping_count)}**"
                )
                tvoed_ok = st.session_state.get("tvoed_available", False)
                st.markdown(
                    f"- **{t('settings.pay_table_status', status=t('settings.pay_table_active') if tvoed_ok else t('settings.pay_table_fallback'))}**"
                )
                
            st.divider()
            # Reset flag after rendering once
            st.session_state["show_reload_success"] = False

    # --- Datenmanagement ---
    st.subheader(t("settings.data_management"))
    st.caption(t("settings.data_management.caption"))
    
    with st.expander(f"📁 {t('settings.uploads_expander')}"):
        if "global_uploads" not in st.session_state:
            st.session_state["global_uploads"] = {}
            
        col_up1, col_up2 = st.columns(2)
        with col_up1:
            up_ma = st.file_uploader("Mitarbeiter.xlsx", type=["xlsx"], key="set_up_ma")
            if up_ma:
                # Store as BytesIO for persistence
                st.session_state["global_uploads"]["Mitarbeiter"] = io.BytesIO(up_ma.getvalue())
            elif "Mitarbeiter" in st.session_state["global_uploads"] and not up_ma:
                # If widget cleared, keep old? Or clear? 
                # Standard behavior: clear. But we want persistence across pages.
                # If user clears widget manually, we remove.
                # But looking at widget key: if page reloads, key is gone?
                pass

        with col_up2:
            up_pl = st.file_uploader("Planstellen.xlsx", type=["xlsx"], key="set_up_pl")
            if up_pl:
                st.session_state["global_uploads"]["Planstellen"] = io.BytesIO(up_pl.getvalue())

        col_up3, col_up4 = st.columns(2)
        with col_up3:
            up_atz = st.file_uploader("ATZ.xlsx", type=["xlsx"], key="set_up_atz")
            if up_atz:
                st.session_state["global_uploads"]["ATZ"] = io.BytesIO(up_atz.getvalue())

        with col_up4:
            up_edu = st.file_uploader("Ausbildung.xlsx", type=["xlsx"], key="set_up_edu")
            if up_edu:
                st.session_state["global_uploads"]["Ausbildung"] = io.BytesIO(up_edu.getvalue())

        col_up5, col_up6 = st.columns(2)
        with col_up5:
            up_tvoed = st.file_uploader(t("settings.tvoed_optional"), type=["xlsx"], key="set_up_tvoed")
            if up_tvoed:
                st.session_state["global_uploads"]["TVÖD"] = io.BytesIO(up_tvoed.getvalue())

        if st.session_state["global_uploads"]:
            st.success(f"✅ {t('settings.uploads_active', count=len(st.session_state['global_uploads']))}")
            if st.button(t("settings.delete_uploads")):
                 delete_result = _delete_cluster_uploads()
                 st.session_state["global_uploads"] = {}
                 fallback = delete_result["active_source"].display_label
                 _set_cluster_feedback("info", f"Alle Uploads wurden entfernt. Aktive Clusterquelle: {fallback}.")
                 st.rerun()

    st.divider()

    # --- Cluster-Management ---
    st.subheader(t("settings.cluster_management"))
    st.caption(t("settings.cluster_management.caption"))
    
    with st.expander(f"🧩 {t('settings.cluster_expander')}"):
        c_col1, c_col2 = st.columns(2)
        
        with c_col1:
            st.markdown(f"**{t('settings.cluster_step1')}**")
            st.caption(t("settings.cluster_step1.caption"))
            if st.button(f"📥 {t('settings.cluster_generate_template')}"):
                # Load current data to get unique names/keys
                with st.spinner(t("settings.cluster_loading_masterdata")):
                    df_ma, _, _, _ = load_and_prepare_data()
                    jf_defs = load_jobfamily_definitions()
                    template_bytes = generate_template_bytes(df_ma, jf_defs)
                    
                st.download_button(
                    label=f"📂 {t('settings.cluster_download_template')}",
                    data=template_bytes,
                    file_name="Cluster-Template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_cluster_template"
                )

        with c_col2:
            st.markdown(f"**{t('settings.cluster_step2')}**")
            st.caption(t("settings.cluster_step2.caption"))
            up_cluster = st.file_uploader(t("settings.cluster_upload_mapping"), type=["xlsx"], key="up_cluster_mappings")
            
            if up_cluster:
                stage_result = _stage_cluster_upload(up_cluster)
                if stage_result["status"] == "matches_active":
                    st.info("Die ausgewaehlte Clusterdatei ist bereits als aktive Quelle gespeichert.")

            _render_staged_cluster_state()

            staged = _get_staged_cluster_state()
            button_col1, button_col2 = st.columns(2)
            with button_col1:
                apply_disabled = not (staged["bytes"] and staged["is_valid"])
                if st.button(t("settings.cluster_apply_now"), disabled=apply_disabled, key="cluster_apply_now_button"):
                    apply_result = _apply_staged_cluster_upload()
                    if apply_result["success"]:
                        bump_cache_version("data_prep")
                        _set_cluster_feedback("success", apply_result["message"])
                    else:
                        _set_cluster_feedback("error", apply_result["message"])
                    st.rerun()
            with button_col2:
                delete_disabled = (
                    not staged["filename"]
                    and not st.session_state.get("cluster_override_active", False)
                    and not os.path.exists(os.path.join(BASE_DIR, "config", "cluster_mapping.xlsx"))
                )
                if st.button(t("settings.delete_uploads"), disabled=delete_disabled, key="cluster_delete_uploads_button"):
                    delete_result = _delete_cluster_uploads()
                    if delete_result["success"]:
                        fallback_label = delete_result["active_source"].display_label
                        _set_cluster_feedback("success", f"User-Override entfernt. Neue aktive Clusterquelle: {fallback_label}.")
                    else:
                        _set_cluster_feedback("error", "Cluster-Upload konnte nicht vollstaendig entfernt werden.")
                    st.rerun()

        active_cluster_source = _refresh_active_cluster_source_state()
        _render_active_cluster_source(active_cluster_source)
        if active_cluster_source.oe_mapping_count or active_cluster_source.jf_mapping_count:
            st.info(
                t(
                    "settings.cluster_active_mappings",
                    oe=active_cluster_source.oe_mapping_count,
                    jf=active_cluster_source.jf_mapping_count,
                )
            )

    st.divider()

    # --- Allgemeine Einstellungen (Stichtag) ---
    st.subheader(t("settings.general"))

    from utils.settings_loader import get_setting, set_setting, save_user_settings, load_user_settings

    # Stichtag
    current_stichtag_str = get_setting("stichtag", STICHTAG_DEFAULT)
    try:
        current_stichtag = pd.to_datetime(current_stichtag_str).date()
    except Exception:
        current_stichtag = pd.to_datetime(STICHTAG_DEFAULT).date()
        
    new_stichtag = st.date_input(
        t("settings.reference_date"),
        value=current_stichtag,
        help=t("settings.reference_date.help"),
    )
    
    if new_stichtag != current_stichtag:
        if st.button(t("settings.reference_date.save")):
            set_setting("stichtag", str(new_stichtag))
            st.success(t("settings.reference_date.saved", date=new_stichtag))
            
    # Zukünftige Eintritte
    include_future_hires = get_setting("include_future_hires", False)
    include_future_cb = st.checkbox(
        t("settings.include_future_hires"),
        value=include_future_hires,
        help=t("settings.include_future_hires.help"),
    )
    
    if include_future_cb != include_future_hires:
        set_setting("include_future_hires", include_future_cb)
        st.rerun()

    # Statistik anzeigen
    if "stats_future_hires" in st.session_state:
        future_count = st.session_state["stats_future_hires"]
        status_text = t("settings.future_hires.included") if include_future_cb else t("settings.future_hires.filtered")
        if future_count > 0:
            st.info(t("settings.future_hires.info", count=future_count, date=current_stichtag, status=status_text))
        else:
            st.caption(t("settings.future_hires.none", date=current_stichtag))

    st.divider()

    # --- Simulations-Parameter ---
    st.subheader(t("settings.simulation"))
    st.caption(t("settings.simulation.caption"))

    sim_settings = get_setting("simulation", {})
    
    with st.form("simulation_settings_form"):
        c1, c2 = st.columns(2)
        with c1:
            s_horizon = st.number_input(t("settings.simulation.horizon"), value=sim_settings.get("horizon_months", 60), min_value=12, max_value=120)
            s_retire_age = st.number_input(t("settings.simulation.retirement_age"), value=sim_settings.get("retirement_age", 67), min_value=60, max_value=70)
            s_early_retire = st.number_input(t("settings.simulation.early_retirement"), value=sim_settings.get("early_retirement_share", 0.10), min_value=0.0, max_value=1.0, format="%.2f")
        
        with c2:
            s_hiring_rate = st.number_input(t("settings.simulation.hiring_rate"), value=sim_settings.get("hiring_rate_pa", 0.04), min_value=0.0, max_value=1.0, format="%.2f")
            s_time_to_fill = st.number_input(t("settings.simulation.time_to_fill"), value=sim_settings.get("time_to_fill_months", 3), min_value=1, max_value=24)
            s_azubi_intake = st.number_input(t("settings.simulation.azubi_intake"), value=sim_settings.get("azubi_intake_per_year", 40), min_value=0)

        if st.form_submit_button(t("settings.simulation.save")):
            new_sim_settings = {
                "horizon_months": s_horizon,
                "retirement_age": s_retire_age,
                "early_retirement_share": s_early_retire,
                "hiring_rate_pa": s_hiring_rate,
                "time_to_fill_months": s_time_to_fill,
                "azubi_intake_per_year": s_azubi_intake,
                # Preserve other keys if they exist in defaults but not here
                "azubi_duration_months": sim_settings.get("azubi_duration_months", 36),
                "azubi_takeover_rate": sim_settings.get("azubi_takeover_rate", 0.70),
            }
            set_setting("simulation", new_sim_settings)
            st.success(t("settings.simulation.saved"))

    st.divider()

    # --- Gruppen-Ausschlüsse (verschoben) ---
    st.subheader(t("settings.exclusion_groups"))
    st.info(t("settings.exclusion_groups.info"))

    st.divider()

    # --- TVÖD-Status ---
    st.subheader(t("settings.compensation"))
    st.caption(t("settings.compensation.caption"))

    tvoed_available = st.session_state.get("tvoed_available", False)
    tvoed_lookup = st.session_state.get("tvoed_lookup", {})

    if tvoed_available:
        st.success(t("settings.tvoed.loaded", count=len(tvoed_lookup)))
        with st.expander(t("settings.tvoed.show_groups")):
            groups = sorted(set(g for g, _ in tvoed_lookup.keys()))
            st.write(", ".join(groups))
    else:
        st.warning(t("settings.tvoed.missing"))

    st.markdown(f"##### {t('settings.salary_automation.heading')}")
    st.caption(t("settings.salary_automation.caption"))

    salary_automation = get_setting("salary_automation", {})
    salary_automation = {**SALARY_AUTOMATION_DEFAULTS, **salary_automation}

    current_source = "TVöD-Tabelle" if tvoed_available else t("settings.pay_table_fallback")
    scope_label = {
        "new_hires_only": t("settings.salary_automation.scope.new_hires_only"),
        "all_staff": t("settings.salary_automation.scope.all_staff"),
    }.get(salary_automation.get("scope"), t("settings.salary_automation.scope.new_hires_only"))

    sa_info_col1, sa_info_col2 = st.columns(2)
    with sa_info_col1:
        st.markdown(f"**{t('settings.salary_automation.current_logic')}**")
        st.markdown(f"- **{t('settings.salary_automation.salary_source', source=current_source)}**")
        st.markdown(f"- **{t('settings.salary_automation.step_source')}**")
        st.markdown(f"- **{t('settings.salary_automation.system_fallback', step=salary_automation['fallback_step'])}**")
        st.markdown(f"- **{t('settings.salary_automation.progression')}**")
    with sa_info_col2:
        st.markdown(f"**{t('settings.salary_automation.prepared_config')}**")
        st.markdown(
            f"- **{t('settings.salary_automation.config_status', status=t('settings.salary_automation.enabled') if salary_automation['enabled'] else t('settings.salary_automation.disabled'))}**"
        )
        st.markdown(f"- **{t('settings.salary_automation.scope', scope=scope_label)}**")
        st.markdown(
            f"- **{t('settings.salary_automation.entry', e1=salary_automation['e1_entry_step'], e2=salary_automation['e2_plus_default_entry_step'])}**"
        )
        st.markdown(f"- **{t('settings.salary_automation.note')}**")

    rules_df = pd.DataFrame(
        [
            {
                "Bereich": "E1",
                "Einstieg": "Stufe 2",
                "Stufenlaufzeit": "4 / 4 / 4 / 4 Jahre",
                "Status im System": "fachliche Referenz",
            },
            {
                "Bereich": "E2-E15",
                "Einstieg": "im Regelfall Stufe 1",
                "Stufenlaufzeit": "1 / 2 / 3 / 4 / 5 Jahre",
                "Status im System": "fachliche Referenz",
            },
            {
                "Bereich": "Bestand heute",
                "Einstieg": "aus Datenbestand",
                "Stufenlaufzeit": "keine automatische Fortschreibung",
                "Status im System": "aktiver Rechenmodus",
            },
            {
                "Bereich": "Simulation",
                "Einstieg": "teilweise parametriert",
                "Stufenlaufzeit": "optional aktivierbar",
                "Status im System": "wirkt auf TVöD-Stufenfortschreibung innerhalb der Entgeltgruppe",
            },
        ]
    )

    with st.expander(t("settings.salary_automation.rules_expander")):
        st.dataframe(rules_df, use_container_width=True, hide_index=True)
        st.info(t("settings.salary_automation.rules_info"))

    with st.form("salary_automation_form"):
        sa_col1, sa_col2 = st.columns(2)

        with sa_col1:
            sa_enabled = st.checkbox(
                t("settings.salary_automation.prepare"),
                value=bool(salary_automation["enabled"]),
                help=t("settings.salary_automation.prepare.help"),
            )
            scope_options = [
                t("settings.salary_automation.scope.new_hires_only"),
                t("settings.salary_automation.scope.all_staff"),
            ]
            sa_scope_label = st.selectbox(
                t("settings.salary_automation.apply_to"),
                options=scope_options,
                index=0 if salary_automation["scope"] == "new_hires_only" else 1,
                help=t("settings.salary_automation.apply_to.help"),
            )
            sa_fallback_step = st.number_input(
                t("settings.salary_automation.fallback_step"),
                min_value=1,
                max_value=6,
                value=int(salary_automation["fallback_step"]),
                step=1,
                help=t("settings.salary_automation.fallback_step.help"),
            )
            sa_existing_proxy = st.checkbox(
                t("settings.salary_automation.tenure_proxy"),
                value=bool(salary_automation["use_tenure_as_step_proxy_for_existing_staff"]),
                help=t("settings.salary_automation.tenure_proxy.help"),
            )

        with sa_col2:
            st.text_input(
                t("settings.salary_automation.e1_entry"),
                value=f"Stufe {salary_automation['e1_entry_step']}",
                disabled=True,
                help=t("settings.salary_automation.e1_entry.help"),
            )
            sa_e2_entry = st.number_input(
                t("settings.salary_automation.e2_entry"),
                min_value=1,
                max_value=6,
                value=int(salary_automation["e2_plus_default_entry_step"]),
                step=1,
                help=t("settings.salary_automation.e2_entry.help"),
            )
            st.caption(t("settings.salary_automation.progression_years"))
            e1_cols = st.columns(4)
            e1_progression_years = []
            for idx, col in enumerate(e1_cols):
                with col:
                    e1_progression_years.append(
                        int(
                            st.number_input(
                                f"E1 -> {idx + 3}",
                                min_value=1,
                                max_value=10,
                                value=int(salary_automation["e1_progression_years"][idx]),
                                step=1,
                                key=f"salary_auto_e1_{idx}",
                            )
                        )
                    )

            e2_cols = st.columns(5)
            e2_progression_years = []
            for idx, col in enumerate(e2_cols):
                with col:
                    e2_progression_years.append(
                        int(
                            st.number_input(
                                f"E2+ -> {idx + 2}",
                                min_value=1,
                                max_value=10,
                                value=int(salary_automation["e2_plus_progression_years"][idx]),
                                step=1,
                                key=f"salary_auto_e2_{idx}",
                            )
                        )
                    )

        if st.form_submit_button(t("settings.salary_automation.save")):
            new_salary_automation = {
                "enabled": sa_enabled,
                "scope": "new_hires_only" if sa_scope_label == t("settings.salary_automation.scope.new_hires_only") else "all_staff",
                "fallback_step": int(sa_fallback_step),
                "e1_entry_step": int(salary_automation["e1_entry_step"]),
                "e2_plus_default_entry_step": int(sa_e2_entry),
                "e1_progression_years": e1_progression_years,
                "e2_plus_progression_years": e2_progression_years,
                "use_tenure_as_step_proxy_for_existing_staff": bool(sa_existing_proxy),
            }
            set_setting("salary_automation", new_salary_automation)
            st.success(t("settings.salary_automation.saved"))
    
    st.markdown(f"##### {t('settings.employer_cost.heading')}")
    st.caption(t("settings.employer_cost.caption"))
    employer_factor = st.number_input(
        t("settings.employer_cost.label"),
        min_value=1.0,
        max_value=2.0,
        value=st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR),
        step=0.01,
        format="%.2f",
        key="input_employer_factor",
        help=t("settings.employer_cost.help"),
        label_visibility="collapsed"
    )
    st.session_state["employer_cost_factor"] = employer_factor

    st.markdown(f"##### {t('settings.special_salaries.heading')}")
    st.caption(t("settings.special_salaries.caption"))

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"**{t('settings.special_salaries.trainees')}**")
        st.caption(t("settings.special_salaries.trainees.caption"))
        
        from config.settings import DEFAULT_AZUBI_SALARIES
        current_azubi_salaries = st.session_state.get("azubi_salaries", DEFAULT_AZUBI_SALARIES)
        new_azubi_salaries = {}
        
        az_cols = st.columns(2)
        for year in range(1, 5):
            c_idx = (year - 1) % 2
            with az_cols[c_idx]:
                new_val = st.number_input(
                    t("settings.special_salaries.trainee_salary", year=year),
                    min_value=0.0,
                    value=float(current_azubi_salaries.get(str(year), current_azubi_salaries.get(year, DEFAULT_AZUBI_SALARIES[year]))),
                    step=100.0,
                    format="%.2f",
                    key=f"az_sal_{year}",
                    help=t("settings.special_salaries.trainee_salary.help", year=year)
                )
                new_azubi_salaries[year] = new_val
        
        st.session_state["azubi_salaries"] = new_azubi_salaries

    with col2:
        st.markdown(f"**{t('settings.special_salaries.board')}**")
        vorstand_input = st.number_input(
            t("settings.special_salaries.board_salary"),
            min_value=0.0,
            max_value=1000000.0,
            value=None,
            placeholder=t("settings.special_salaries.board_placeholder"),
            step=1000.0,
            key="input_vorstand_gehalt",
            help=t("settings.special_salaries.board_help"),
        )
        if vorstand_input is not None:
            st.session_state["vorstand_jahresgehalt"] = vorstand_input
        else:
            # Wenn leer, Override entfernen -> Loader nutzt Default (200k)
            if "vorstand_jahresgehalt" in st.session_state:
                del st.session_state["vorstand_jahresgehalt"]

    st.divider()

    # --- Hinweis zum Neuladen ---
    st.info(t("settings.reload_required"))

    if st.button(t("settings.reload_data"), type="primary"):
        bump_cache_version("data_prep")
        st.session_state["show_reload_success"] = True
        st.rerun()


# --- Page Entry Point ---
if not globals().get("_UNIT_TESTING"):
    render_settings_page()
