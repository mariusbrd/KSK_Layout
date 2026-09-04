"""
Modul 8: Einstellungen

Konfigurationsseite für Loader-spezifische Parameter: Datenmanagement,
Cluster-Management, Stichtag/Filter, Simulationsparameter und Soll-Korrekturen.
"""

import io
import hashlib
import streamlit as st
import pandas as pd
import sys
import os
from datetime import datetime, timezone

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import COLORS
from kpi_reference import STICHTAG_DEFAULT
from dataloader.loader import load_and_prepare_data
from dataloader.jobfamily_matcher import load_jobfamily_definitions
from dataloader.cluster_manager import (
    delete_persisted_cluster_upload,
    generate_template_bytes,
    persist_cluster_upload_bytes,
    validate_cluster_upload,
)
from dataloader.upload_templates import (
    generate_tvoed_template_bytes,
    generate_upload_template_bytes,
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
from dataloader.data_integrity import (
    check_mitarbeiter_planstellen_integrity,
    build_integrity_report_excel,
)
from config.settings import BASE_DIR
from components.sidebar import render_metric_selector_only, set_metric_page_hint
from utils.cache_utils import bump_cache_version
from utils.i18n import t
from utils.lineage import build_complete_glossary_workbook_bytes
from components.ui_compat import lazy_excel_download_button_compat
from components.ui_shell import render_context_box, render_page_header, render_section_intro


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

CLUSTER_ACTIVE_SESSION_KEYS = (
    "cluster_upload_active_bytes",
    "cluster_upload_active_filename",
)

_EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_upload_exception(prefix: str, exc: Exception) -> str:
    return f"{prefix}: {type(exc).__name__}: {exc}"


@st.cache_data(show_spinner=False)
def _run_data_integrity_check_cached(ma_bytes: bytes, pl_bytes: bytes):
    ma_df = pd.read_excel(io.BytesIO(ma_bytes))
    pl_df = pd.read_excel(io.BytesIO(pl_bytes))
    return check_mitarbeiter_planstellen_integrity(ma_df, pl_df)


def _render_data_integrity_section() -> None:
    """
    Prüft beim Upload eigener Mitarbeiter.xlsx/Planstellen.xlsx, ob beide Dateien
    deckungsgleiche Personalnummern verwenden (siehe Blocker B17: Abweichungen
    fallen im Dashboard sauber aus den personenbezogenen Kennzahlen heraus, statt
    fehlerhaft mitgezählt zu werden - aber sie müssen für den Nutzer sichtbar sein).
    """
    uploads = st.session_state.get("global_uploads", {})
    if "Mitarbeiter" not in uploads or "Planstellen" not in uploads:
        return

    try:
        ma_bytes = uploads["Mitarbeiter"].getvalue()
        pl_bytes = uploads["Planstellen"].getvalue()
        report = _run_data_integrity_check_cached(ma_bytes, pl_bytes)
    except Exception as exc:
        st.error(_format_upload_exception("Datenintegritäts-Prüfung fehlgeschlagen", exc))
        return

    st.markdown("**Datenintegrität: Mitarbeiter.xlsx ↔ Planstellen.xlsx**")

    if report.is_clean:
        st.success(
            "✅ Personalnummern stimmen überein: jede besetzte Planstelle hat einen passenden "
            "Mitarbeiter-Datensatz und umgekehrt."
        )
        return

    if report.error_count:
        st.error(
            f"⚠️ {report.error_count} Datenintegritäts-Fehler gefunden. Betroffene Personen "
            "werden im Dashboard sauber aus den personenbezogenen Kennzahlen ausgeschlossen "
            "(z. B. Kompakt „Gesamt Köpfe“), fehlen dort also – auch wenn es echte, besetzte "
            "Stellen sind. Das deutet auf einen Fehler im Datenlieferungsprozess hin."
        )
    if report.warning_count:
        st.warning(f"ℹ️ {report.warning_count} weitere Abweichungen gefunden (siehe Details unten).")

    for check in report.checks:
        if check.count == 0:
            continue
        icon = "🔴" if check.severity == "error" else "🟡"
        with st.expander(f"{icon} {check.title} ({check.count})", expanded=False):
            st.caption(check.description)
            st.dataframe(check.detail, use_container_width=True, hide_index=True)

    lazy_excel_download_button_compat(
        label="📥 Evaluations-Excel herunterladen",
        data_builder=lambda: build_integrity_report_excel(report),
        file_name=f"Datenintegritaet_Evaluation_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_data_integrity_report",
        fingerprint=(
            "data_integrity_report",
            report.error_count,
            report.warning_count,
            tuple((check.title, check.severity, check.count) for check in report.checks),
        ),
        help="Detaillierte Auflistung aller gefundenen Abweichungen je Prüfung, als Excel-Arbeitsmappe.",
    )


def _get_cluster_uploader_key() -> str:
    nonce = int(st.session_state.get("cluster_upload_uploader_nonce", 0) or 0)
    return f"up_cluster_mappings_{nonce}"


def _reset_cluster_uploader_widget() -> None:
    st.session_state["cluster_upload_uploader_nonce"] = int(
        st.session_state.get("cluster_upload_uploader_nonce", 0) or 0
    ) + 1


def _cluster_debug_log(event: str, **fields) -> None:
    history = list(st.session_state.get("cluster_upload_debug_history", []) or [])
    run_no = int(st.session_state.get("cluster_upload_debug_run_no", 0) or 0)
    entry = {
        "run_no": run_no,
        "ts": _now_iso(),
        "event": event,
        "uploader_key": _get_cluster_uploader_key(),
        "uploader_nonce": int(st.session_state.get("cluster_upload_uploader_nonce", 0) or 0),
        "staged_name": st.session_state.get("cluster_upload_staged_filename"),
        "staged_hash": st.session_state.get("cluster_upload_staged_hash"),
        "active_source_subtype": st.session_state.get("active_cluster_source_subtype"),
        "active_source_signature": st.session_state.get("active_cluster_source_signature"),
        "cluster_override_active": bool(st.session_state.get("cluster_override_active", False)),
        "cluster_upload_ignore_hash": st.session_state.get("cluster_upload_ignore_hash"),
        "cluster_processing": st.session_state.get("cluster_upload_processing"),
        "cluster_busy": st.session_state.get("cluster_upload_busy"),
        "cluster_disabled": st.session_state.get("cluster_upload_disabled"),
    }
    entry.update(fields)
    history.append(entry)
    st.session_state["cluster_upload_debug_history"] = history[-20:]


def _cluster_rerun(reason: str) -> None:
    _cluster_debug_log("rerun_called", reason=reason)
    st.rerun()


def _safe_upload_debug_meta(uploaded_file) -> dict:
    if uploaded_file is None:
        return {
            "uploader_has_file": False,
            "upload_name": None,
            "upload_size": 0,
            "upload_hash": None,
        }
    try:
        raw = uploaded_file.getvalue()
    except Exception as exc:
        return {
            "uploader_has_file": True,
            "upload_name": getattr(uploaded_file, "name", None),
            "upload_size": None,
            "upload_hash": None,
            "upload_meta_error": _format_upload_exception("Upload-Metadaten konnten nicht gelesen werden", exc),
        }
    return {
        "uploader_has_file": True,
        "upload_name": getattr(uploaded_file, "name", None),
        "upload_size": len(raw),
        "upload_hash": validate_cluster_upload(raw).content_hash if raw else None,
    }


def _render_cluster_debug_panel() -> None:
    history = list(st.session_state.get("cluster_upload_debug_history", []) or [])
    with st.expander("Cluster Upload Debug", expanded=False):
        if not history:
            st.caption("Noch keine Cluster-Upload-Debug-Einträge.")
            return
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)


def _ensure_global_uploads() -> dict:
    if "global_uploads" not in st.session_state or not isinstance(st.session_state.get("global_uploads"), dict):
        st.session_state["global_uploads"] = {}
    return st.session_state["global_uploads"]


def _store_global_upload(role: str, uploaded_file) -> None:
    data = uploaded_file.getvalue()
    st.session_state["global_uploads"][role] = io.BytesIO(data)
    metadata = st.session_state.setdefault("global_upload_metadata", {})
    metadata[role] = {
        "file_name": getattr(uploaded_file, "name", f"{role}.xlsx"),
        "size_bytes": len(data),
        "content_hash": hashlib.sha256(data).hexdigest(),
        "uploaded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


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


def _clear_active_cluster_session_state() -> None:
    for key in CLUSTER_ACTIVE_SESSION_KEYS:
        st.session_state.pop(key, None)


def _should_persist_cluster_upload_to_disk() -> bool:
    if str(os.environ.get("KSK_DISABLE_DISK_CLUSTER_UPLOADS", "")).strip().lower() in {"1", "true", "yes"}:
        return False
    return not os.path.abspath(BASE_DIR).replace("\\", "/").startswith("/mount/src/")


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
        _cluster_debug_log("staging_skipped", uploader_has_file=False)
        return {"status": "no_upload"}

    _cluster_debug_log("staging_called", **_safe_upload_debug_meta(uploaded_file))

    try:
        validation = validate_cluster_upload(uploaded_file)
        uploaded_bytes = uploaded_file.getvalue()
    except Exception as exc:
        _clear_staged_cluster_state()
        message = _format_upload_exception("Clusterdatei konnte nicht verarbeitet werden", exc)
        _cluster_debug_log("staging_result", status="exception", message=message)
        return {
            "status": "exception",
            "message": message,
        }

    ignored_hash = st.session_state.get("cluster_upload_ignore_hash")
    if validation.content_hash and ignored_hash and validation.content_hash == ignored_hash:
        _cluster_debug_log("staging_result", status="ignored_same_upload", validation_hash=validation.content_hash)
        return {"status": "ignored_same_upload", "validation": validation}
    if validation.content_hash and ignored_hash and validation.content_hash != ignored_hash:
        st.session_state.pop("cluster_upload_ignore_hash", None)

    staged_hash = st.session_state.get("cluster_upload_staged_hash")
    if validation.content_hash and staged_hash and validation.content_hash == staged_hash:
        is_valid = bool(st.session_state.get("cluster_upload_staged_valid", False))
        status = "already_staged_valid" if is_valid else "already_staged_invalid"
        _cluster_debug_log("staging_result", status=status, validation_hash=validation.content_hash)
        return {
            "status": status,
            "validation": validation,
        }

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
        st.session_state["cluster_upload_ignore_hash"] = validation.content_hash
        _cluster_debug_log("staging_result", status="matches_active", validation_hash=validation.content_hash)
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
        _cluster_debug_log("staging_result", status="staged", validation_hash=validation.content_hash)
        return {"status": "staged", "validation": validation, "active_source": active_source}

    st.session_state["cluster_upload_staged_bytes"] = None
    st.session_state["cluster_upload_staged_filename"] = getattr(uploaded_file, "name", "Cluster-Upload.xlsx")
    st.session_state["cluster_upload_staged_hash"] = validation.content_hash
    st.session_state["cluster_upload_staged_valid"] = False
    st.session_state["cluster_upload_staged_errors"] = list(validation.errors)
    st.session_state["cluster_upload_staged_uploaded_at"] = _now_iso()
    st.session_state["cluster_upload_staged_oe_mapping_count"] = validation.oe_mapping_count
    st.session_state["cluster_upload_staged_jf_mapping_count"] = validation.jf_mapping_count
    _cluster_debug_log("staging_result", status="invalid", validation_hash=validation.content_hash, errors=list(validation.errors))
    return {"status": "invalid", "validation": validation, "active_source": active_source}


def _apply_staged_cluster_upload(
    *,
    persisted_local_path: str | None = None,
    external_file_path: str | None = None,
) -> dict:
    _cluster_debug_log("apply_called")
    staged = _get_staged_cluster_state()
    if not staged["bytes"] or not staged["is_valid"]:
        _cluster_debug_log("apply_result", success=False, reason="no_valid_staged_upload")
        return {"success": False, "message": "Es liegt kein gueltiger staged Upload zum Anwenden vor."}

    persist_result = None
    if _should_persist_cluster_upload_to_disk():
        persist_result = persist_cluster_upload_bytes(staged["bytes"], target_path=persisted_local_path)
        if not persist_result.get("success"):
            _cluster_debug_log("apply_result", success=False, reason="persist_failed", persist_error=persist_result.get("error"))
            return {
                "success": False,
                "message": f"Cluster-Upload konnte nicht gespeichert werden: {persist_result.get('error', 'unbekannt')}",
                "persist_result": persist_result,
            }

    if persist_result:
        _clear_active_cluster_session_state()
    else:
        st.session_state["cluster_upload_active_bytes"] = staged["bytes"]
        st.session_state["cluster_upload_active_filename"] = staged["filename"] or "Cluster-Upload.xlsx"

    st.session_state["cluster_upload_ignore_hash"] = staged["hash"]
    st.session_state["cluster_override_active"] = True
    st.session_state["cluster_override_activated_at"] = (persist_result or {}).get("written_at") or _now_iso()
    st.session_state["active_cluster_source_mode"] = "ui_upload"
    st.session_state["active_cluster_source_subtype"] = (
        SUBTYPE_UI_UPLOAD_PERSISTED if persist_result else "ui_upload.session"
    )

    invalidation = invalidate_cluster_dependent_state(st.session_state, reason="cluster_apply_now")
    _cluster_debug_log("invalidate_called", removed_count=invalidation.get("removed_count", 0), reason="cluster_apply_now")
    _clear_staged_cluster_state()
    active_source = _refresh_active_cluster_source_state(
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )
    _cluster_debug_log(
        "apply_result",
        success=True,
        persisted_path=(persist_result or {}).get("path"),
        persisted_signature=(persist_result or {}).get("source_signature"),
        active_source_signature=getattr(active_source, "source_signature", None),
    )

    return {
        "success": True,
        "message": "Cluster-Upload wurde aktiviert." if not persist_result else "Cluster-Upload wurde aktiviert und lokal gespeichert.",
        "persist_result": persist_result,
        "invalidation": invalidation,
        "active_source": active_source,
    }


def _delete_cluster_uploads(
    *,
    persisted_local_path: str | None = None,
    external_file_path: str | None = None,
) -> dict:
    _cluster_debug_log("delete_called")
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
    _clear_active_cluster_session_state()

    delete_result = delete_persisted_cluster_upload(target_path=persisted_local_path)
    st.session_state["cluster_override_active"] = False
    st.session_state.pop("cluster_override_activated_at", None)
    clear_active_cluster_source_from_session(st.session_state)
    invalidation = invalidate_cluster_dependent_state(st.session_state, reason="cluster_delete_uploads")
    _cluster_debug_log("invalidate_called", removed_count=invalidation.get("removed_count", 0), reason="cluster_delete_uploads")
    active_source = _refresh_active_cluster_source_state(
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )
    _cluster_debug_log("delete_result", success=delete_result.get("success", False), active_source_signature=getattr(active_source, "source_signature", None))

    return {
        "success": delete_result.get("success", False),
        "delete_result": delete_result,
        "invalidation": invalidation,
        "active_source": active_source,
    }


def _render_active_cluster_source(active_source) -> None:
    label = active_source.display_label or active_source.subtype or active_source.mode or "—"
    st.caption(
        f"Clusterquelle: {label} · "
        f"{active_source.oe_mapping_count} OE-Mappings · {active_source.jf_mapping_count} JF-Mappings"
    )


def _render_staged_cluster_state() -> None:
    staged = _get_staged_cluster_state()
    if not staged["filename"] and not staged["errors"]:
        return
    if staged["is_valid"] and staged["bytes"]:
        st.caption(
            f"Bereit zum Aktivieren: {staged['filename']} · "
            f"{staged['oe_mapping_count']} OE · {staged['jf_mapping_count']} JF"
        )
    elif staged["filename"]:
        st.caption(f"Upload ungültig: {staged['filename']}")
    for err in staged.get("errors") or []:
        st.error(err)


def render_settings_page():
    _ensure_global_uploads()
    active_cluster_source = _refresh_active_cluster_source_state()
    st.session_state["cluster_upload_debug_run_no"] = int(st.session_state.get("cluster_upload_debug_run_no", 0) or 0) + 1
    _cluster_debug_log(
        "run_start",
        active_source_path=getattr(active_cluster_source, "source_path", None),
        active_source_status=getattr(active_cluster_source, "status", None),
    )

    set_metric_page_hint(
        t("settings.metric_hint")
    )
    render_metric_selector_only()

    render_page_header(
        t("settings.title"),
        "Zentrale Konfiguration für Datenbasis, Berechnungsparameter, Simulationen und Kostenlogik.",
    )
    st.caption("Konfiguration · wirkt nach Neuladen · dashboardweit")
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
    render_section_intro(t("settings.data_management"), t("settings.data_management.caption"))
    
    if "global_uploads" not in st.session_state:
        st.session_state["global_uploads"] = {}

    def _render_template_download(data_builder, file_name: str, key: str) -> None:
        lazy_excel_download_button_compat(
            label="Vorlage herunterladen",
            data_builder=data_builder,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=key,
            fingerprint=(key, file_name),
            help="Leere Vorlage mit korrekten Spaltennamen und Dropdown-Hilfen herunterladen.",
            type="tertiary",
            icon="📥",
        )

    col_up1, col_up2 = st.columns(2)
    with col_up1:
        up_ma = st.file_uploader("Mitarbeiter.xlsx", type=["xlsx"], key="set_up_ma")
        if up_ma:
            _store_global_upload("Mitarbeiter", up_ma)
        elif "Mitarbeiter" in st.session_state["global_uploads"] and not up_ma:
            pass
        _render_template_download(
            lambda: generate_upload_template_bytes("Mitarbeiter"),
            "Mitarbeiter_Template.xlsx",
            "dl_template_mitarbeiter",
        )

    with col_up2:
        up_pl = st.file_uploader("Planstellen.xlsx", type=["xlsx"], key="set_up_pl")
        if up_pl:
            _store_global_upload("Planstellen", up_pl)
        _render_template_download(
            lambda: generate_upload_template_bytes("Planstellen"),
            "Planstellen_Template.xlsx",
            "dl_template_planstellen",
        )

    col_up3, col_up4 = st.columns(2)
    with col_up3:
        up_atz = st.file_uploader("ATZ.xlsx", type=["xlsx"], key="set_up_atz")
        if up_atz:
            _store_global_upload("ATZ", up_atz)
        _render_template_download(
            lambda: generate_upload_template_bytes("ATZ"),
            "ATZ_Template.xlsx",
            "dl_template_atz",
        )

    with col_up4:
        up_edu = st.file_uploader("Ausbildung.xlsx", type=["xlsx"], key="set_up_edu")
        if up_edu:
            _store_global_upload("Ausbildung", up_edu)
        _render_template_download(
            lambda: generate_upload_template_bytes("Ausbildung"),
            "Ausbildung_Template.xlsx",
            "dl_template_ausbildung",
        )

    col_up5, col_up6 = st.columns(2)
    with col_up5:
        up_tvoed = st.file_uploader(t("settings.tvoed_optional"), type=["xlsx"], key="set_up_tvoed")
        if up_tvoed:
            _store_global_upload("TVÖD", up_tvoed)
        _render_template_download(
            lambda: generate_tvoed_template_bytes(),
            "TVOED_Template.xlsx",
            "dl_template_tvoed",
        )

    if st.session_state["global_uploads"]:
        st.caption(f"{t('settings.uploads_active', count=len(st.session_state['global_uploads']))}")
        if st.button(t("settings.delete_uploads")):
            delete_result = _delete_cluster_uploads()
            st.session_state["global_uploads"] = {}
            st.session_state["global_upload_metadata"] = {}
            fallback = delete_result["active_source"].display_label
            _set_cluster_feedback("info", f"Alle Uploads wurden entfernt. Aktive Clusterquelle: {fallback}.")
            st.rerun()

    _render_data_integrity_section()

    lazy_excel_download_button_compat(
        label="KPI-Glossar herunterladen",
        data_builder=lambda: build_complete_glossary_workbook_bytes(
            export_context={"Exporttyp": "Vollstaendiges KPI- und Berechnungsglossar"}
        ),
        file_name="KPI_Glossar_und_Berechnungslineage.xlsx",
        mime=_EXCEL_MIME,
        key="dl_lineage_glossary",
        fingerprint=("lineage_glossary",),
        help="Vollstaendiges Glossar aller registrierten Dashboard-Elemente inklusive Quellen, Spalten, Formeln, Transformationsschritten und Testnachweisen.",
        type="tertiary",
        icon="📘",
    )

    st.divider()

    # --- Cluster-Management ---
    render_section_intro(t("settings.cluster_management"), t("settings.cluster_management.caption"))
    
    c_col1, c_col2 = st.columns(2)

    with c_col1:
        st.caption(t("settings.cluster_step1"))
        st.caption(t("settings.cluster_step1.caption"))
        if st.button(f"📥 {t('settings.cluster_generate_template')}"):
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
        st.caption(t("settings.cluster_step2"))
        st.caption(t("settings.cluster_step2.caption"))
        up_cluster = st.file_uploader(
            t("settings.cluster_upload_mapping"),
            type=["xlsx"],
            key=_get_cluster_uploader_key(),
        )
        _cluster_debug_log("uploader_observed", **_safe_upload_debug_meta(up_cluster))

        if up_cluster:
            stage_result = _stage_cluster_upload(up_cluster)
            if stage_result["status"] == "matches_active":
                _reset_cluster_uploader_widget()
                _cluster_debug_log("uploader_reset_requested", reason="cluster_upload_matches_active")
                st.info("Die ausgewaehlte Clusterdatei ist bereits aktiv.")
            elif stage_result["status"] == "staged":
                _reset_cluster_uploader_widget()
                _cluster_debug_log("uploader_reset_requested", reason="cluster_upload_staged")
            elif stage_result["status"] == "invalid":
                errors = stage_result.get("validation").errors if stage_result.get("validation") else []
                message = "Clusterdatei ist ungueltig."
                if errors:
                    message = "Clusterdatei ist ungueltig: " + " | ".join(str(err) for err in errors)
                _reset_cluster_uploader_widget()
                _cluster_debug_log("uploader_reset_requested", reason="cluster_upload_invalid")
                st.error(message)
            elif stage_result["status"] == "exception":
                _reset_cluster_uploader_widget()
                _cluster_debug_log("uploader_reset_requested", reason="cluster_upload_exception")
                st.error(stage_result["message"])
            elif stage_result["status"] in {"already_staged_valid", "already_staged_invalid", "ignored_same_upload"}:
                pass

        _render_staged_cluster_state()

        staged = _get_staged_cluster_state()
        button_col1, button_col2 = st.columns(2)
        with button_col1:
            apply_disabled = not (staged["bytes"] and staged["is_valid"])
            if st.button(t("settings.cluster_apply_now"), disabled=apply_disabled, key="cluster_apply_now_button"):
                apply_result = _apply_staged_cluster_upload()
                if apply_result["success"]:
                    bump_cache_version("data_prep")
                    _cluster_debug_log("cache_bump_called", namespace="data_prep")
                    _set_cluster_feedback("success", apply_result["message"])
                else:
                    _set_cluster_feedback("error", apply_result["message"])
                _reset_cluster_uploader_widget()
        with button_col2:
            delete_disabled = (
                not staged["filename"]
                and not st.session_state.get("cluster_override_active", False)
                and not st.session_state.get("cluster_upload_active_bytes")
                and not os.path.exists(os.path.join(BASE_DIR, "config", "cluster_mapping.xlsx"))
            )
            if st.button(t("settings.delete_uploads"), disabled=delete_disabled, key="cluster_delete_uploads_button"):
                delete_result = _delete_cluster_uploads()
                if delete_result["success"]:
                    fallback_label = delete_result["active_source"].display_label
                    _set_cluster_feedback("success", f"User-Override entfernt. Neue aktive Clusterquelle: {fallback_label}.")
                else:
                    _set_cluster_feedback("error", "Cluster-Upload konnte nicht vollstaendig entfernt werden.")
                _reset_cluster_uploader_widget()

    active_cluster_source = _refresh_active_cluster_source_state()
    _cluster_debug_log(
        "post_cluster_refresh",
        active_source_path=getattr(active_cluster_source, "source_path", None),
        active_source_status=getattr(active_cluster_source, "status", None),
    )
    _render_active_cluster_source(active_cluster_source)
    _render_cluster_debug_panel()

    st.divider()

    # --- Allgemeine Einstellungen (Stichtag) ---
    render_section_intro(t("settings.general"), "Stichtag und Filteroptionen für die Datenauswertung.")

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
    render_section_intro(t("settings.simulation"), t("settings.simulation.caption"))

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
    render_section_intro(t("settings.exclusion_groups"))
    render_context_box("Hinweis", t("settings.exclusion_groups.info"), tone="info")

    st.divider()

    # --- Datenkorrekturen (Entgelt-&-Kosten-Bereich ausgeblendet, EUR-Ansicht deaktiviert) ---
    render_section_intro(
        "Datenkorrekturen",
        "Korrekturregeln für die Soll-Kapazitätsberechnung.",
    )

    st.markdown(f"##### {t('settings.soll_correction.heading')}")
    st.caption(t("settings.soll_correction.caption"))
    current_soll_correction = get_setting("occupied_placeholder_soll_correction", False)
    new_soll_correction = st.toggle(
        t("settings.soll_correction.label"),
        value=current_soll_correction,
        key="input_occupied_placeholder_soll_correction",
        help=t("settings.soll_correction.help"),
    )
    if new_soll_correction != current_soll_correction:
        set_setting("occupied_placeholder_soll_correction", new_soll_correction)
        st.rerun()

    st.divider()

    # --- Hinweis zum Neuladen ---
    render_context_box("Neuladen erforderlich", t("settings.reload_required"), tone="info")

    if st.button(t("settings.reload_data"), type="primary"):
        bump_cache_version("data_prep")
        st.session_state["show_reload_success"] = True
        st.rerun()


# --- Page Entry Point ---
if not globals().get("_UNIT_TESTING"):
    render_settings_page()
