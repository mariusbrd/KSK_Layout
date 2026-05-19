from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import streamlit as st

from config.settings import BASE_DIR
from dataloader.cluster_resolver import (
    ActiveClusterSource,
    ClusterMappingBundle,
    MODE_INPUT_FOLDER,
    MODE_SYNTHETIC,
    MODE_UI_UPLOAD,
    STATUS_ACTIVE,
    STATUS_FALLBACK,
    STATUS_INVALID,
    STATUS_MISSING,
    SUBTYPE_INPUT_EXTERNAL,
    SUBTYPE_SYNTHETIC_FALLBACK,
    SUBTYPE_UI_UPLOAD_PERSISTED,
    SUBTYPE_UI_UPLOAD_SESSION,
    get_active_cluster_source,
)
from utils.cache_utils import coerce_file_bytes, get_file_signature


def _normalize_plan_id(value) -> str:
    text = "" if pd.isna(value) else str(value).strip()
    return text[:-2] if text.endswith(".0") else text


# Path for persistent cluster mapping
# BASE_DIR imported from config.settings
# Technical role in the target model: ui_upload.persisted_local_copy
CLUSTER_FILE = os.path.join(BASE_DIR, "config", "cluster_mapping.xlsx")
# Input-folder source in the target model: input_folder.external_file
EXTERNAL_CLUSTER_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "Cluster-Daten", "OE_Cluster.xlsx"))


@dataclass
class ValidationResult:
    is_valid: bool = False
    message: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sheet_names: list[str] = field(default_factory=list)
    detected_jobfamily_format: Optional[str] = None
    oe_mapping_count: int = 0
    jf_mapping_count: int = 0
    bytes_size: int = 0
    content_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClusterUIDimensions:
    org_units: list[str] = field(default_factory=list)
    oe_clusters: list[str] = field(default_factory=list)
    job_family_clusters: list[str] = field(default_factory=list)
    org_unit_to_cluster_map: dict[str, str] = field(default_factory=dict)
    source_signature: Optional[str] = None
    is_source_backed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash_bytes(data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def _build_source_signature(
    *,
    mode: str,
    subtype: str,
    status: str,
    content_hash: Optional[str],
    source_path: Optional[str],
) -> str:
    payload = {
        "mode": mode,
        "subtype": subtype,
        "status": status,
        "content_hash": content_hash or "",
        "source_path": os.path.abspath(source_path) if source_path else "",
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _count_nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def _unique_nonempty_strings(
    series: pd.Series,
    *,
    exclude: Optional[set[str]] = None,
) -> list[str]:
    excluded = {str(item).strip().casefold() for item in (exclude or set())}
    values = {
        text
        for text in series.fillna("").astype(str).str.strip()
        if text and text.casefold() not in excluded and text.casefold() not in {"nan", "none"}
    }
    return sorted(values)


def _extract_source_bytes(active_cluster_source: ActiveClusterSource) -> Optional[bytes]:
    debug_meta = getattr(active_cluster_source, "debug_meta", {}) or {}
    for key in ("source_bytes", "session_bytes", "payload_bytes"):
        data = coerce_file_bytes(debug_meta.get(key))
        if data is not None:
            return data
    return None


def _rehydrate_session_cluster_source(active_cluster_source: ActiveClusterSource) -> ActiveClusterSource:
    """Restore session-upload bytes when a serialized source only kept metadata."""
    if active_cluster_source is None:
        return active_cluster_source
    if getattr(active_cluster_source, "subtype", None) != SUBTYPE_UI_UPLOAD_SESSION:
        return active_cluster_source
    if _extract_source_bytes(active_cluster_source) is not None:
        return active_cluster_source

    session_state = getattr(st, "session_state", None)
    if session_state is None:
        return active_cluster_source

    session_key = getattr(active_cluster_source, "session_key", None) or "cluster_upload_active_bytes"
    session_bytes = coerce_file_bytes(session_state.get(session_key))
    if session_bytes is None and session_key != "cluster_upload_active_bytes":
        session_bytes = coerce_file_bytes(session_state.get("cluster_upload_active_bytes"))
    if session_bytes is None:
        current_source = get_active_cluster_source(session_state=session_state)
        if getattr(current_source, "subtype", None) == SUBTYPE_UI_UPLOAD_SESSION:
            session_bytes = _extract_source_bytes(current_source)
    if session_bytes is None:
        return active_cluster_source

    active_cluster_source.debug_meta = dict(getattr(active_cluster_source, "debug_meta", {}) or {})
    active_cluster_source.debug_meta["source_bytes"] = session_bytes
    return active_cluster_source


def _build_active_source_from_bytes(
    upload_bytes: bytes,
    *,
    subtype: str = SUBTYPE_UI_UPLOAD_SESSION,
    session_key: Optional[str] = None,
    source_path: Optional[str] = None,
    status: str = STATUS_ACTIVE,
) -> ActiveClusterSource:
    content_hash = _hash_bytes(upload_bytes)
    signature = _build_source_signature(
        mode=MODE_UI_UPLOAD,
        subtype=subtype,
        status=status,
        content_hash=content_hash,
        source_path=source_path,
    )
    return ActiveClusterSource(
        mode=MODE_UI_UPLOAD,
        subtype=subtype,
        status=status,
        is_active=status == STATUS_ACTIVE,
        is_valid=True,
        priority_rank=1 if subtype == SUBTYPE_UI_UPLOAD_SESSION else 2,
        display_label="Legacy Cluster Source",
        description="Temporary explicit source built by a cluster_manager legacy wrapper.",
        source_path=os.path.abspath(source_path) if source_path else None,
        session_key=session_key,
        persisted_local_path=os.path.abspath(source_path) if subtype == SUBTYPE_UI_UPLOAD_PERSISTED and source_path else None,
        filename=os.path.basename(source_path) if source_path else "Cluster",
        file_exists=bool(source_path and os.path.exists(source_path)),
        content_hash=content_hash,
        source_signature=signature,
        activated_at=_now_iso(),
        last_modified_at=None,
        resolution_reason="Legacy explicit source created inside cluster_manager wrapper.",
        validation_errors=[],
        fallback_from=None,
        debug_meta={
            "source_bytes": upload_bytes,
            "legacy_wrapper": True,
        },
    )


def _build_active_source_from_path(path: Optional[str]) -> ActiveClusterSource:
    normalized_path = os.path.abspath(path) if path else None
    if normalized_path == os.path.abspath(CLUSTER_FILE):
        mode = MODE_UI_UPLOAD
        subtype = SUBTYPE_UI_UPLOAD_PERSISTED
        display_label = "UI-Upload (persistiert)"
        description = "Legacy wrapper path for the persisted local UI-upload copy."
        priority_rank = 2
    elif normalized_path == os.path.abspath(EXTERNAL_CLUSTER_FILE):
        mode = MODE_INPUT_FOLDER
        subtype = SUBTYPE_INPUT_EXTERNAL
        display_label = "Input-Ordner"
        description = "Legacy wrapper path for the external input-folder cluster file."
        priority_rank = 3
    else:
        mode = MODE_INPUT_FOLDER
        subtype = SUBTYPE_INPUT_EXTERNAL
        display_label = "Dateibasierte Clusterquelle"
        description = "Legacy wrapper path for an explicitly supplied cluster file."
        priority_rank = 3

    exists = bool(normalized_path and os.path.exists(normalized_path))
    status = STATUS_ACTIVE if exists else STATUS_MISSING
    if exists:
        with open(normalized_path, "rb") as handle:
            content_hash = _hash_bytes(handle.read())
    else:
        content_hash = None
    signature = _build_source_signature(
        mode=mode,
        subtype=subtype,
        status=status,
        content_hash=content_hash,
        source_path=normalized_path,
    )

    return ActiveClusterSource(
        mode=mode,
        subtype=subtype,
        status=status,
        is_active=exists,
        is_valid=exists,
        priority_rank=priority_rank,
        display_label=display_label,
        description=description,
        source_path=normalized_path,
        session_key=None,
        persisted_local_path=normalized_path if subtype == SUBTYPE_UI_UPLOAD_PERSISTED else None,
        filename=os.path.basename(normalized_path) if normalized_path else None,
        file_exists=exists,
        content_hash=content_hash,
        source_signature=signature,
        activated_at=None,
        last_modified_at=datetime.fromtimestamp(os.path.getmtime(normalized_path)).replace(microsecond=0).isoformat()
        if exists
        else None,
        resolution_reason="Legacy explicit file source created inside cluster_manager wrapper.",
        validation_errors=[],
        fallback_from=None,
        debug_meta={
            "legacy_wrapper": True,
            "file_signature": get_file_signature(normalized_path),
        },
    )


def _read_excel_from_source(
    *,
    source_subtype: str,
    source_path: Optional[str],
    source_bytes: Optional[bytes],
) -> Optional[pd.ExcelFile]:
    try:
        if source_subtype == SUBTYPE_SYNTHETIC_FALLBACK:
            return None
        if source_subtype == SUBTYPE_UI_UPLOAD_SESSION:
            if not source_bytes:
                return None
            return pd.ExcelFile(io.BytesIO(source_bytes))
        if source_path and os.path.exists(source_path):
            return pd.ExcelFile(source_path)
    except Exception as exc:
        print(f"Error reading cluster source ({source_subtype}): {exc}")
    return None


def _build_mapping_bundle_from_excel(
    xls: Optional[pd.ExcelFile],
    *,
    source_signature: Optional[str],
) -> ClusterMappingBundle:
    oe_map: Dict[str, str] = {}
    jf_map: Dict[Any, str] = {}

    if xls is None:
        return ClusterMappingBundle(oe_map=oe_map, jf_map=jf_map, source_signature=source_signature)

    try:
        df_oe = pd.read_excel(xls, sheet_name="OrgUnits")
        df_jf = pd.read_excel(xls, sheet_name="JobFamilies")
    except Exception as exc:
        print(f"Error reading cluster sheets: {exc}")
        return ClusterMappingBundle(oe_map=oe_map, jf_map=jf_map, source_signature=source_signature)

    if not df_oe.empty and {"Organisationseinheit", "Cluster"}.issubset(df_oe.columns):
        df_oe = df_oe.copy()
        df_oe["Organisationseinheit"] = df_oe["Organisationseinheit"].fillna("").astype(str).str.strip()
        df_oe["Cluster"] = df_oe["Cluster"].fillna("Unclustered").astype(str).str.strip()
        valid_oe = df_oe[df_oe["Organisationseinheit"].ne("")]
        oe_map = valid_oe.set_index("Organisationseinheit")["Cluster"].to_dict()

    if not df_jf.empty:
        df_jf = df_jf.copy()
        if "Planstelle" in df_jf.columns and "Jobfamily Cluster" in df_jf.columns:
            df_jf["Planstelle"] = df_jf["Planstelle"].fillna("").astype(str).str.strip()
            df_jf["Jobfamily Cluster"] = df_jf["Jobfamily Cluster"].fillna("").astype(str).str.strip()
            if "Planstellennr" in df_jf.columns and "Organisationseinheit" in df_jf.columns:
                df_jf["Planstellennr"] = df_jf["Planstellennr"].map(_normalize_plan_id)
                df_jf["Organisationseinheit"] = df_jf["Organisationseinheit"].fillna("").astype(str).str.strip()
                for _, row in df_jf.iterrows():
                    value = row["Jobfamily Cluster"]
                    org = row["Organisationseinheit"]
                    pos = row["Planstelle"]
                    plan_id = row["Planstellennr"]
                    if (
                        value in ("", "nan", "Unclustered")
                        or org in ("", "nan")
                        or pos in ("", "nan")
                        or plan_id in ("", "nan")
                    ):
                        continue
                    jf_map[(org, pos, plan_id)] = value
            elif "Organisationseinheit" in df_jf.columns:
                df_jf["Organisationseinheit"] = df_jf["Organisationseinheit"].fillna("").astype(str).str.strip()
                for _, row in df_jf.iterrows():
                    value = row["Jobfamily Cluster"]
                    org = row["Organisationseinheit"]
                    pos = row["Planstelle"]
                    if value in ("", "nan", "Unclustered") or org in ("", "nan") or pos in ("", "nan"):
                        continue
                    jf_map[(org, pos)] = value
            else:
                valid = df_jf[~df_jf["Jobfamily Cluster"].isin(["", "nan", "Unclustered"])]
                valid = valid[valid["Planstelle"].ne("") & valid["Planstelle"].ne("nan")]
                jf_map = valid.set_index("Planstelle")["Jobfamily Cluster"].to_dict()
        elif "Jobfamily" in df_jf.columns and "Cluster" in df_jf.columns:
            df_jf["Jobfamily"] = df_jf["Jobfamily"].fillna("").astype(str).str.strip()
            df_jf["Cluster"] = df_jf["Cluster"].fillna("").astype(str).str.strip()
            valid = df_jf[~df_jf["Cluster"].isin(["", "nan", "Unclustered"])]
            valid = valid[valid["Jobfamily"].ne("") & valid["Jobfamily"].ne("nan")]
            jf_map = valid.set_index("Jobfamily")["Cluster"].to_dict()

    return ClusterMappingBundle(
        oe_map=oe_map,
        jf_map=jf_map,
        source_signature=source_signature,
    )


def _build_cluster_ui_dimensions_from_excel(
    xls: Optional[pd.ExcelFile],
    *,
    source_signature: Optional[str],
    is_source_backed: bool,
) -> ClusterUIDimensions:
    if xls is None:
        return ClusterUIDimensions(
            source_signature=source_signature,
            is_source_backed=is_source_backed,
        )

    try:
        df_oe = pd.read_excel(xls, sheet_name="OrgUnits")
        df_jf = pd.read_excel(xls, sheet_name="JobFamilies")
    except Exception:
        return ClusterUIDimensions(
            source_signature=source_signature,
            is_source_backed=is_source_backed,
        )

    org_units: list[str] = []
    oe_clusters: list[str] = []
    org_unit_to_cluster_map: dict[str, str] = {}
    job_family_clusters: list[str] = []

    if not df_oe.empty and {"Organisationseinheit", "Cluster"}.issubset(df_oe.columns):
        org_units = _unique_nonempty_strings(df_oe["Organisationseinheit"])
        oe_clusters = _unique_nonempty_strings(df_oe["Cluster"])

        valid_oe = df_oe.copy()
        valid_oe["Organisationseinheit"] = valid_oe["Organisationseinheit"].fillna("").astype(str).str.strip()
        valid_oe["Cluster"] = valid_oe["Cluster"].fillna("").astype(str).str.strip()
        valid_oe = valid_oe[(valid_oe["Organisationseinheit"] != "") & (valid_oe["Cluster"] != "")]
        org_unit_to_cluster_map = valid_oe.set_index("Organisationseinheit")["Cluster"].to_dict()

    if not df_jf.empty:
        jf_cluster_col = None
        if "Jobfamily Cluster" in df_jf.columns:
            jf_cluster_col = "Jobfamily Cluster"
        elif "Cluster" in df_jf.columns:
            jf_cluster_col = "Cluster"

        if jf_cluster_col:
            job_family_clusters = _unique_nonempty_strings(df_jf[jf_cluster_col])

    return ClusterUIDimensions(
        org_units=org_units,
        oe_clusters=oe_clusters,
        job_family_clusters=job_family_clusters,
        org_unit_to_cluster_map=org_unit_to_cluster_map,
        source_signature=source_signature,
        is_source_backed=is_source_backed,
    )


def _validate_cluster_bytes(data: Optional[bytes]) -> ValidationResult:
    if data is None:
        return ValidationResult(
            is_valid=False,
            message="Keine Clusterdatei uebergeben.",
            errors=["Keine Clusterdatei uebergeben."],
            bytes_size=0,
            content_hash=None,
        )

    content_hash = _hash_bytes(data)
    try:
        xls = pd.ExcelFile(io.BytesIO(data))
    except Exception as exc:
        return ValidationResult(
            is_valid=False,
            message="Excel-Datei konnte nicht gelesen werden.",
            errors=[f"Excel-Datei konnte nicht gelesen werden: {exc}"],
            bytes_size=len(data),
            content_hash=content_hash,
        )

    sheet_names = list(xls.sheet_names)
    errors: list[str] = []
    required_sheets = {"OrgUnits", "JobFamilies"}
    missing_sheets = sorted(required_sheets - set(sheet_names))
    if missing_sheets:
        errors.append("Die Datei muss die Tabellenblaetter 'OrgUnits' und 'JobFamilies' enthalten.")
        return ValidationResult(
            is_valid=False,
            message=errors[0],
            errors=errors,
            sheet_names=sheet_names,
            bytes_size=len(data),
            content_hash=content_hash,
        )

    try:
        df_oe = pd.read_excel(xls, sheet_name="OrgUnits")
        df_jf = pd.read_excel(xls, sheet_name="JobFamilies")
    except Exception as exc:
        return ValidationResult(
            is_valid=False,
            message="Cluster-Sheets konnten nicht gelesen werden.",
            errors=[f"Cluster-Sheets konnten nicht gelesen werden: {exc}"],
            sheet_names=sheet_names,
            bytes_size=len(data),
            content_hash=content_hash,
        )

    for col in ("Organisationseinheit", "Cluster"):
        if col not in df_oe.columns:
            errors.append(f"Tabelle 'OrgUnits' fehlt die Spalte '{col}'.")

    has_new = "Planstelle" in df_jf.columns and "Jobfamily Cluster" in df_jf.columns
    has_old = "Jobfamily" in df_jf.columns and "Cluster" in df_jf.columns
    if not (has_new or has_old):
        errors.append(
            "Tabelle 'JobFamilies' muss entweder ('Planstelle', 'Jobfamily Cluster') "
            "oder ('Jobfamily', 'Cluster') enthalten."
        )

    if df_oe.empty:
        errors.append("Tabelle 'OrgUnits' darf nicht komplett leer sein.")
    if df_jf.empty:
        errors.append("Tabelle 'JobFamilies' darf nicht komplett leer sein.")

    detected_format = None
    jf_count = 0
    if has_new:
        detected_format = "position_based"
        jf_count = _count_nonempty(df_jf["Jobfamily Cluster"])
    elif has_old:
        detected_format = "legacy_jobfamily"
        jf_count = _count_nonempty(df_jf["Cluster"])

    oe_count = _count_nonempty(df_oe["Cluster"]) if "Cluster" in df_oe.columns else 0
    is_valid = not errors

    return ValidationResult(
        is_valid=is_valid,
        message="Cluster-Definitionen erfolgreich validiert." if is_valid else errors[0],
        errors=errors,
        warnings=[],
        sheet_names=sheet_names,
        detected_jobfamily_format=detected_format,
        oe_mapping_count=oe_count,
        jf_mapping_count=jf_count,
        bytes_size=len(data),
        content_hash=content_hash,
    )


@st.cache_data
def _load_cluster_mappings_from_source_cached(
    source_subtype: str,
    source_signature: Optional[str],
    source_path: Optional[str],
    source_bytes: Optional[bytes],
) -> ClusterMappingBundle:
    xls = _read_excel_from_source(
        source_subtype=source_subtype,
        source_path=source_path,
        source_bytes=source_bytes,
    )
    return _build_mapping_bundle_from_excel(
        xls,
        source_signature=source_signature,
    )


@st.cache_data
def _load_cluster_ui_dimensions_from_source_cached(
    source_subtype: str,
    source_signature: Optional[str],
    source_path: Optional[str],
    source_bytes: Optional[bytes],
) -> ClusterUIDimensions:
    xls = _read_excel_from_source(
        source_subtype=source_subtype,
        source_path=source_path,
        source_bytes=source_bytes,
    )
    return _build_cluster_ui_dimensions_from_excel(
        xls,
        source_signature=source_signature,
        is_source_backed=True,
    )


def get_active_cluster_file() -> str:
    """
    Legacy-only helper.

    This function still exists for compatibility with older code paths and tests.
    It must not be used as a central runtime resolver path. The return value is
    intentionally deterministic and follows the resolver's persisted-local-copy
    preference instead of any timestamp comparison.
    """
    if os.path.exists(CLUSTER_FILE):
        return CLUSTER_FILE

    if os.path.exists(EXTERNAL_CLUSTER_FILE):
        return EXTERNAL_CLUSTER_FILE
    return CLUSTER_FILE


def generate_template_bytes(df_ma: pd.DataFrame, jf_definitions: Dict) -> bytes:
    """
    Generates an Excel template with current OrgUnits and JobFamilies (position-based).
    Returns bytes for download.
    """
    oe_code_col = "Kürzel OrgEinheit" if "Kürzel OrgEinheit" in df_ma.columns else "KÃ¼rzel OrgEinheit"
    oe_cols = [oe_code_col, "Organisationseinheit"]
    if "OrgEinheitNr" in df_ma.columns:
        oe_cols.insert(1, "OrgEinheitNr")

    unique_oes = df_ma[oe_cols].drop_duplicates().sort_values(oe_code_col)
    unique_oes["Cluster"] = ""

    jf_cols = ["Organisationseinheit", "Planstelle"]
    if all(c in df_ma.columns for c in jf_cols):
        unique_pos = df_ma[jf_cols].drop_duplicates().sort_values(["Organisationseinheit", "Planstelle"])
        unique_pos["Jobfamily Cluster ID"] = ""
        unique_pos["Jobfamily Cluster"] = ""
    else:
        unique_pos = pd.DataFrame(
            columns=["Organisationseinheit", "Planstelle", "Jobfamily Cluster ID", "Jobfamily Cluster"]
        )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        unique_oes.to_excel(writer, sheet_name="OrgUnits", index=False)
        unique_pos.to_excel(writer, sheet_name="JobFamilies", index=False)

    return output.getvalue()


def validate_cluster_upload(uploaded_file_or_bytes) -> ValidationResult:
    """Validate a cluster upload without persisting it."""
    try:
        data = coerce_file_bytes(uploaded_file_or_bytes)
    except Exception as exc:
        return ValidationResult(
            is_valid=False,
            message="Clusterdatei konnte nicht gelesen werden.",
            errors=[f"Clusterdatei konnte nicht gelesen werden: {exc}"],
        )
    return _validate_cluster_bytes(data)


def persist_cluster_upload_bytes(upload_bytes, target_path: Optional[str] = None) -> dict[str, Any]:
    """Persist validated upload bytes to the local persisted UI-upload path."""
    try:
        data = coerce_file_bytes(upload_bytes)
    except Exception as exc:
        return {
            "success": False,
            "path": os.path.abspath(target_path or CLUSTER_FILE),
            "bytes_written": 0,
            "content_hash": None,
            "source_signature": None,
            "file_signature": None,
            "error": str(exc),
        }

    if data is None:
        return {
            "success": False,
            "path": os.path.abspath(target_path or CLUSTER_FILE),
            "bytes_written": 0,
            "content_hash": None,
            "source_signature": None,
            "file_signature": None,
            "error": "No upload bytes provided.",
        }

    path = os.path.abspath(target_path or CLUSTER_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        content_hash = _hash_bytes(data)
        source_signature = _build_source_signature(
            mode=MODE_UI_UPLOAD,
            subtype=SUBTYPE_UI_UPLOAD_PERSISTED,
            status=STATUS_ACTIVE,
            content_hash=content_hash,
            source_path=path,
        )
        return {
            "success": True,
            "path": path,
            "bytes_written": len(data),
            "content_hash": content_hash,
            "source_signature": source_signature,
            "file_signature": get_file_signature(path),
            "written_at": _now_iso(),
            "error": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "path": path,
            "bytes_written": 0,
            "content_hash": _hash_bytes(data),
            "source_signature": None,
            "file_signature": None,
            "error": str(exc),
        }


def delete_persisted_cluster_upload(target_path: Optional[str] = None) -> dict[str, Any]:
    """Delete the persisted local UI-upload copy."""
    path = os.path.abspath(target_path or CLUSTER_FILE)
    existed = os.path.exists(path)
    if not existed:
        return {
            "success": True,
            "path": path,
            "existed": False,
            "deleted": False,
            "error": None,
        }

    try:
        os.remove(path)
        return {
            "success": True,
            "path": path,
            "existed": True,
            "deleted": True,
            "error": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "path": path,
            "existed": True,
            "deleted": False,
            "error": str(exc),
        }


def validate_and_save_clusters(uploaded_file) -> Tuple[bool, str]:
    """
    Legacy wrapper kept for compatibility.

    Patch 2 splits validation and persistence cleanly. Later patches will move UI
    flows to validate_cluster_upload() + persist_cluster_upload_bytes().
    """
    validation = validate_cluster_upload(uploaded_file)
    if not validation.is_valid:
        return False, validation.message

    result = persist_cluster_upload_bytes(uploaded_file)
    if result.get("success"):
        return True, "Cluster-Definitionen erfolgreich gespeichert."
    return False, f"Fehler beim Speichern: {result.get('error', 'unbekannt')}"


def load_cluster_mappings_from_source(active_cluster_source: ActiveClusterSource) -> ClusterMappingBundle:
    """
    Load cluster mappings from exactly one explicitly resolved source.

    This function does not perform discovery or resolution. The caller must
    provide a concrete active source from dataloader.cluster_resolver.
    """
    if active_cluster_source is None:
        return ClusterMappingBundle(oe_map={}, jf_map={}, source_signature=None)

    active_cluster_source = _rehydrate_session_cluster_source(active_cluster_source)

    if not getattr(active_cluster_source, "is_valid", False):
        return ClusterMappingBundle(
            oe_map={},
            jf_map={},
            source_signature=getattr(active_cluster_source, "source_signature", None),
        )

    source_subtype = getattr(active_cluster_source, "subtype", SUBTYPE_SYNTHETIC_FALLBACK)
    source_signature = getattr(active_cluster_source, "source_signature", None)
    source_path = getattr(active_cluster_source, "source_path", None)
    source_bytes = _extract_source_bytes(active_cluster_source)

    if source_subtype == SUBTYPE_SYNTHETIC_FALLBACK or getattr(active_cluster_source, "mode", None) == MODE_SYNTHETIC:
        return ClusterMappingBundle(oe_map={}, jf_map={}, source_signature=source_signature)

    return _load_cluster_mappings_from_source_cached(
        source_subtype=source_subtype,
        source_signature=source_signature,
        source_path=os.path.abspath(source_path) if source_path else None,
        source_bytes=source_bytes,
    )


def load_cluster_ui_dimensions_from_source(
    active_cluster_source: ActiveClusterSource,
) -> ClusterUIDimensions:
    """
    Load UI dimension values from one explicitly resolved active cluster source.
    """
    if active_cluster_source is None:
        return ClusterUIDimensions(is_source_backed=False)

    active_cluster_source = _rehydrate_session_cluster_source(active_cluster_source)

    source_signature = getattr(active_cluster_source, "source_signature", None)
    if not getattr(active_cluster_source, "is_valid", False):
        return ClusterUIDimensions(
            source_signature=source_signature,
            is_source_backed=False,
        )

    source_subtype = getattr(active_cluster_source, "subtype", SUBTYPE_SYNTHETIC_FALLBACK)
    source_path = getattr(active_cluster_source, "source_path", None)
    source_bytes = _extract_source_bytes(active_cluster_source)

    if source_subtype == SUBTYPE_SYNTHETIC_FALLBACK or getattr(active_cluster_source, "mode", None) == MODE_SYNTHETIC:
        return ClusterUIDimensions(
            source_signature=source_signature,
            is_source_backed=False,
        )

    return _load_cluster_ui_dimensions_from_source_cached(
        source_subtype=source_subtype,
        source_signature=source_signature,
        source_path=os.path.abspath(source_path) if source_path else None,
        source_bytes=source_bytes,
    )


def resolve_forecast_ui_dimensions(
    df_snapshot: Optional[pd.DataFrame],
    active_cluster_source: ActiveClusterSource,
    *,
    mapping_bundle: Optional[ClusterMappingBundle] = None,
) -> ClusterUIDimensions:
    """
    Resolve the forecast-page UI source of truth.

    If a real cluster file is active, use its OrgUnits / OE-Clusters /
    Jobfamily-Clusters directly. Otherwise retain the existing snapshot-based
    fallback behaviour.
    """
    source_dimensions = load_cluster_ui_dimensions_from_source(active_cluster_source)
    if source_dimensions.is_source_backed:
        return source_dimensions

    from dataloader.jobfamily_service import JobFamilyService

    snapshot = df_snapshot.copy() if df_snapshot is not None else pd.DataFrame()

    org_units = (
        _unique_nonempty_strings(snapshot["Organisationseinheit"])
        if "Organisationseinheit" in snapshot.columns
        else []
    )
    oe_clusters = (
        _unique_nonempty_strings(snapshot["OE-Cluster"], exclude={"Unclustered"})
        if "OE-Cluster" in snapshot.columns
        else []
    )
    job_family_values = JobFamilyService.get_active_jobfamilies(
        snapshot if not snapshot.empty else None,
        cluster_mapping_bundle=mapping_bundle,
    )

    org_unit_to_cluster_map: dict[str, str] = {}
    if {"Organisationseinheit", "OE-Cluster"}.issubset(snapshot.columns):
        valid = snapshot[["Organisationseinheit", "OE-Cluster"]].dropna().copy()
        valid["Organisationseinheit"] = valid["Organisationseinheit"].astype(str).str.strip()
        valid["OE-Cluster"] = valid["OE-Cluster"].astype(str).str.strip()
        valid = valid[(valid["Organisationseinheit"] != "") & (valid["OE-Cluster"] != "")]
        org_unit_to_cluster_map = (
            valid.drop_duplicates("Organisationseinheit")
            .set_index("Organisationseinheit")["OE-Cluster"]
            .to_dict()
        )

    return ClusterUIDimensions(
        org_units=org_units,
        oe_clusters=oe_clusters,
        job_family_clusters=job_family_values,
        org_unit_to_cluster_map=org_unit_to_cluster_map,
        source_signature=source_dimensions.source_signature,
        is_source_backed=False,
    )


@st.cache_data
def _load_cluster_mappings_cached(
    uploaded_file_bytes: Optional[bytes],
    active_file_signature: Optional[Tuple[str, int, int]],
) -> Tuple[Dict[str, str], Dict]:
    """
    Legacy compatibility wrapper.

    This wrapper now routes into the explicit source-based loading functions.
    It remains for older call sites and tests and should be removed after the
    loader/settings migration in later patches.
    """
    if uploaded_file_bytes:
        active_source = _build_active_source_from_bytes(uploaded_file_bytes)
        bundle = load_cluster_mappings_from_source(active_source)
        return bundle.oe_map, bundle.jf_map

    if active_file_signature:
        active_source = _build_active_source_from_path(active_file_signature[0])
        bundle = load_cluster_mappings_from_source(active_source)
        return bundle.oe_map, bundle.jf_map

    return {}, {}


def load_cluster_mappings(uploaded_file: Optional[bytes] = None) -> Tuple[Dict[str, str], Dict]:
    """
    Legacy wrapper for existing callers.

    When explicit source information is not provided by the caller yet, this
    wrapper approximates the active source via dataloader.cluster_resolver.
    """
    uploaded_file_bytes = coerce_file_bytes(uploaded_file) if uploaded_file is not None else None
    if uploaded_file_bytes is not None:
        return _load_cluster_mappings_cached(uploaded_file_bytes, None)

    active_source = get_active_cluster_source(
        session_state=getattr(st, "session_state", None),
        persisted_local_path=CLUSTER_FILE,
        external_file_path=EXTERNAL_CLUSTER_FILE,
    )
    bundle = load_cluster_mappings_from_source(active_source)
    return bundle.oe_map, bundle.jf_map


def enrich_jf_clusters(
    df: pd.DataFrame,
    jf_map: Dict,
    snapshot_jf_map: Optional[Dict] = None,
) -> pd.Series:
    """
    Core logic for JF-Cluster derivation with normalization and aliases.

    Guarantee: Every row with a non-empty Jobfamily gets a deterministic
    JF-Cluster. If no mapping exists the fallback is "Sonstiges" (not
    "Unclustered"). Only rows without any Jobfamily value remain "Sonstiges".
    """
    if "Jobfamily" not in df.columns:
        return pd.Series("Sonstiges", index=df.index)

    alias_map = {
        "sonstige": "Sonstiges",
        "sonstiges": "Sonstiges",
        "trainee": "Sonstiges",
        "azubi": "Sonstiges",
    }

    norm_jf_map = {str(k).strip().lower(): v for k, v in jf_map.items() if not isinstance(k, tuple)}
    norm_snap_map = {str(k).strip().lower(): v for k, v in snapshot_jf_map.items()} if snapshot_jf_map else {}

    s_jf_raw = df["Jobfamily"].astype(str).str.strip()
    s_jf_lower = s_jf_raw.str.lower()
    combined_map = {**alias_map, **norm_snap_map, **norm_jf_map}
    res = s_jf_lower.map(combined_map)
    return res.fillna("Sonstiges")


def apply_clusters_to_snapshot_from_source(
    df: pd.DataFrame,
    active_cluster_source: ActiveClusterSource,
    mapping_bundle: Optional[ClusterMappingBundle] = None,
) -> pd.DataFrame:
    """
    Apply cluster mappings to a snapshot using one explicit source or one explicit
    mapping bundle. No discovery or source guessing happens here.
    """
    result = df.copy()
    bundle = mapping_bundle or load_cluster_mappings_from_source(active_cluster_source)
    oe_map = bundle.oe_map
    jf_map = bundle.jf_map

    if "Organisationseinheit" in result.columns:
        result["OE-Cluster"] = result["Organisationseinheit"].map(oe_map).fillna("Unclustered")

    if not jf_map:
        result["JF-Cluster"] = enrich_jf_clusters(result, {})
    else:
        def _source_alias_cluster(source_cluster_values: set[str]) -> pd.Series:
            alias_target = "Keine Job Family" if "Keine Job Family" in source_cluster_values else None
            alias_cluster = pd.Series(pd.NA, index=result.index)
            if not alias_target:
                return alias_cluster

            text = pd.Series("", index=result.index, dtype="object")
            for alias_col in ["Planstelle", "Jobfamily", "JF-Cluster"]:
                if alias_col in result.columns:
                    text = text.str.cat(result[alias_col].fillna("").astype(str), sep=" ")
            alias_mask = text.str.contains("trainee|azubi|ausbildung", case=False, na=False)
            alias_cluster.loc[alias_mask] = alias_target
            return alias_cluster

        first_key = next(iter(jf_map.keys()))
        if isinstance(first_key, tuple):
            if (
                len(first_key) == 3
                and "Organisationseinheit" in result.columns
                and "Planstelle" in result.columns
                and "Planstellennr" in result.columns
            ):
                s_org = result["Organisationseinheit"].astype(str).str.strip()
                s_pos = result["Planstelle"].astype(str).str.strip()
                s_plan = result["Planstellennr"].map(_normalize_plan_id)
                keys = list(zip(s_org, s_pos, s_plan))
                tuple_res = pd.Series([jf_map.get(k) for k in keys], index=result.index)
                source_cluster_values = {
                    str(value).strip()
                    for value in jf_map.values()
                    if str(value).strip() not in {"", "nan", "Unclustered"}
                }
                if "Jobfamily" in result.columns:
                    existing_jobfamily = result["Jobfamily"].astype(str).str.strip()
                    existing_cluster = existing_jobfamily.where(existing_jobfamily.isin(source_cluster_values))
                else:
                    existing_cluster = pd.Series(pd.NA, index=result.index)
                if "JF-Cluster" in result.columns:
                    existing_jf_cluster = result["JF-Cluster"].astype(str).str.strip()
                    existing_cluster = existing_cluster.fillna(
                        existing_jf_cluster.where(existing_jf_cluster.isin(source_cluster_values))
                    )
                alias_cluster = _source_alias_cluster(source_cluster_values)
                cluster_res = (
                    tuple_res
                    .fillna(existing_cluster)
                    .fillna(alias_cluster)
                    .fillna(enrich_jf_clusters(result, jf_map))
                    .fillna("Sonstiges")
                )
                result["JF-Cluster"] = cluster_res
                result["Jobfamily"] = cluster_res
            elif "Organisationseinheit" in result.columns and "Planstelle" in result.columns:
                s_org = result["Organisationseinheit"].astype(str).str.strip()
                s_pos = result["Planstelle"].astype(str).str.strip()
                keys = list(zip(s_org, s_pos))
                tuple_res = pd.Series([jf_map.get(k) for k in keys], index=result.index)
                source_cluster_values = {
                    str(value).strip()
                    for value in jf_map.values()
                    if str(value).strip() not in {"", "nan", "Unclustered"}
                }
                if "Jobfamily" in result.columns:
                    existing_jobfamily = result["Jobfamily"].astype(str).str.strip()
                    existing_cluster = existing_jobfamily.where(existing_jobfamily.isin(source_cluster_values))
                else:
                    existing_cluster = pd.Series(pd.NA, index=result.index)
                if "JF-Cluster" in result.columns:
                    existing_jf_cluster = result["JF-Cluster"].astype(str).str.strip()
                    existing_cluster = existing_cluster.fillna(
                        existing_jf_cluster.where(existing_jf_cluster.isin(source_cluster_values))
                    )
                alias_cluster = _source_alias_cluster(source_cluster_values)
                cluster_res = (
                    tuple_res
                    .fillna(existing_cluster)
                    .fillna(alias_cluster)
                    .fillna(enrich_jf_clusters(result, jf_map))
                    .fillna("Sonstiges")
                )
                result["JF-Cluster"] = cluster_res
                result["Jobfamily"] = cluster_res
            else:
                result["JF-Cluster"] = enrich_jf_clusters(result, jf_map)
        else:
            source_cluster_values = {
                str(value).strip()
                for value in jf_map.values()
                if str(value).strip() not in {"", "nan", "Unclustered"}
            }
            if "Planstelle" in result.columns:
                s_pos = result["Planstelle"].astype(str).str.strip()
                planstelle_res = s_pos.map(jf_map)
            else:
                planstelle_res = pd.Series(pd.NA, index=result.index)
            if "Jobfamily" in result.columns:
                existing_jobfamily = result["Jobfamily"].astype(str).str.strip()
                existing_cluster = existing_jobfamily.where(existing_jobfamily.isin(source_cluster_values))
            else:
                existing_cluster = pd.Series(pd.NA, index=result.index)
            if "JF-Cluster" in result.columns:
                existing_jf_cluster = result["JF-Cluster"].astype(str).str.strip()
                existing_cluster = existing_cluster.fillna(
                    existing_jf_cluster.where(existing_jf_cluster.isin(source_cluster_values))
                )
            alias_cluster = _source_alias_cluster(source_cluster_values)
            cluster_res = (
                planstelle_res
                .fillna(existing_cluster)
                .fillna(alias_cluster)
                .fillna(enrich_jf_clusters(result, jf_map))
                .fillna("Sonstiges")
            )
            result["JF-Cluster"] = cluster_res
            result["Jobfamily"] = cluster_res

    if "JF-Cluster" in result.columns:
        mask_uncl = (result["JF-Cluster"] == "Unclustered") | result["JF-Cluster"].isna()
        if mask_uncl.any():
            result.loc[mask_uncl, "JF-Cluster"] = "Sonstiges"

    return result


def apply_clusters_to_snapshot(df: pd.DataFrame, uploaded_file: Optional[bytes] = None) -> pd.DataFrame:
    """
    Legacy wrapper kept for existing call sites.

    Legacy wrapper for tests and transitional imports only. Runtime call sites
    should already use apply_clusters_to_snapshot_from_source().
    """
    if uploaded_file is not None:
        uploaded_file_bytes = coerce_file_bytes(uploaded_file)
        active_source = _build_active_source_from_bytes(uploaded_file_bytes)
        return apply_clusters_to_snapshot_from_source(df, active_source)

    active_source = get_active_cluster_source(
        session_state=getattr(st, "session_state", None),
        persisted_local_path=CLUSTER_FILE,
        external_file_path=EXTERNAL_CLUSTER_FILE,
    )
    return apply_clusters_to_snapshot_from_source(df, active_source)


def is_clustering_active_for_source(active_cluster_source: ActiveClusterSource) -> bool:
    """Return True when the explicit source yields any usable OE/JF mappings."""
    if active_cluster_source is None or not getattr(active_cluster_source, "is_valid", False):
        return False
    bundle = load_cluster_mappings_from_source(active_cluster_source)
    return bool(bundle.oe_map or bundle.jf_map)


def is_clustering_active(uploaded_file: Optional[bytes] = None) -> bool:
    """
    Legacy wrapper kept for compatibility.

    Transitional wrapper only. Runtime code should prefer
    is_clustering_active_for_source() with an explicit resolved source.
    """
    if uploaded_file is not None:
        uploaded_file_bytes = coerce_file_bytes(uploaded_file)
        active_source = _build_active_source_from_bytes(uploaded_file_bytes)
        return is_clustering_active_for_source(active_source)

    active_source = get_active_cluster_source(
        session_state=getattr(st, "session_state", None),
        persisted_local_path=CLUSTER_FILE,
        external_file_path=EXTERNAL_CLUSTER_FILE,
    )
    return is_clustering_active_for_source(active_source)


__all__ = [
    "CLUSTER_FILE",
    "ClusterUIDimensions",
    "EXTERNAL_CLUSTER_FILE",
    "ValidationResult",
    "apply_clusters_to_snapshot",
    "apply_clusters_to_snapshot_from_source",
    "delete_persisted_cluster_upload",
    "enrich_jf_clusters",
    "generate_template_bytes",
    "get_active_cluster_file",
    "is_clustering_active",
    "is_clustering_active_for_source",
    "load_cluster_mappings",
    "load_cluster_mappings_from_source",
    "load_cluster_ui_dimensions_from_source",
    "persist_cluster_upload_bytes",
    "resolve_forecast_ui_dimensions",
    "validate_and_save_clusters",
    "validate_cluster_upload",
]
