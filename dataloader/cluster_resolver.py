from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from config.settings import BASE_DIR
from utils.cache_utils import coerce_file_bytes


STATUS_UPLOADED_NOT_APPLIED = "uploaded_not_applied"
STATUS_ACTIVE = "active"
STATUS_FALLBACK = "fallback"
STATUS_INVALID = "invalid"
STATUS_MISSING = "missing"
STATUS_AVAILABLE = "available"

MODE_SYNTHETIC = "synthetic"
MODE_INPUT_FOLDER = "input_folder"
MODE_UI_UPLOAD = "ui_upload"

SUBTYPE_UI_UPLOAD_SESSION = "ui_upload.session"
SUBTYPE_UI_UPLOAD_PERSISTED = "ui_upload.persisted_local_copy"
SUBTYPE_INPUT_EXTERNAL = "input_folder.external_file"
SUBTYPE_SYNTHETIC_FALLBACK = "synthetic.default_fallback"

DEFAULT_PERSISTED_LOCAL_PATH = os.path.join(BASE_DIR, "config", "cluster_mapping.xlsx")
CLUSTER_DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "Cluster-Daten"))
# Historischer Dateiname, nur noch als letzter Rueckfall relevant (siehe
# resolve_default_external_cluster_file_path unten).
_LEGACY_EXTERNAL_FILE_PATH = os.path.join(CLUSTER_DATA_DIR, "OE_Cluster.xlsx")


def resolve_default_external_cluster_file_path() -> str:
    """
    Waehlt die zuletzt geaenderte gueltige OE_Cluster*.xlsx-Datei im Cluster-Daten-Ordner
    aus, statt immer denselben hartcodierten Dateinamen (OE_Cluster.xlsx) zu verwenden.

    Vorher zeigte der externe Fallback-Pfad permanent auf OE_Cluster.xlsx, selbst wenn im
    selben Ordner laengst neuere Versionen (z.B. OE_Cluster_Update_V03.xlsx) abgelegt
    waren - wer die persistierte Kopie in config/cluster_mapping.xlsx verlor, landete
    dadurch stillschweigend bei einer veralteten Zuordnung.

    Dateien mit 'backup' im Namen werden ausgeschlossen. Faellt auf den historischen
    Namen zurueck, wenn das Verzeichnis fehlt oder keine passende Datei gefunden wird.
    """
    try:
        candidates = [
            os.path.join(CLUSTER_DATA_DIR, name)
            for name in os.listdir(CLUSTER_DATA_DIR)
            if name.lower().startswith("oe_cluster")
            and name.lower().endswith(".xlsx")
            and "backup" not in name.lower()
        ]
    except OSError:
        return _LEGACY_EXTERNAL_FILE_PATH
    if not candidates:
        return _LEGACY_EXTERNAL_FILE_PATH
    return max(candidates, key=os.path.getmtime)


# Rueckwaertskompatibler Modulname fuer bestehende Importe (z.B. source_service.py).
# Wird beim Modul-Import einmal aufgeloest. Aufrufer, die garantiert die zum
# Aufrufzeitpunkt aktuellste Datei sehen wollen, rufen stattdessen
# resolve_default_external_cluster_file_path() direkt auf (so macht es
# discover_cluster_sources() selbst).
DEFAULT_EXTERNAL_FILE_PATH = resolve_default_external_cluster_file_path()

PRIORITY_BY_SUBTYPE = {
    SUBTYPE_UI_UPLOAD_SESSION: 1,
    SUBTYPE_UI_UPLOAD_PERSISTED: 2,
    SUBTYPE_INPUT_EXTERNAL: 3,
    SUBTYPE_SYNTHETIC_FALLBACK: 4,
}

CLUSTER_DEPENDENT_SESSION_KEYS = [
    "abgaenge_results",
    "abgaenge_global_result",
    "abgaenge_params",
    "abgaenge_params_cluster_signature",
    "abgaenge_ui_state",
    "abgaenge_timestamp",
    "abgaenge_cluster_source_signature",
    "zugaenge_global_result",
    "zugaenge_vacancies",
    "zugaenge_start_date",
    "zugaenge_end_date",
    "zugaenge_use_azubis",
    "zugaenge_use_trainees",
    "zugaenge_use_newhires",
    "zugaenge_cluster_source_signature",
    "hybrid_abg_res",
    "hybrid_abg_params",
    "hybrid_zug_res",
    "hybrid_zug_params",
    "hybrid_cluster_source_signature",
    "compact_sim_signature",
    "compact_sim_cluster_source_signature",
    "compact_sim_prepared_df",
    "compact_sim_metadata",
    "compact_sim_target_date_cached",
    "ui_matrix_snapshot",
    "atz_matrix_editor_live",        # legacy exact-key fallback
    "quit_matrix_editor_live_fixed", # legacy exact-key fallback
    "az_takeover_matrix_editor",
    "hire_dist_matrix",
    "hy_atz_editor",
    "hy_quit_editor",
    "hy_az_takeover_matrix_editor",
    "hy_hire_dist_mat",
]

# Widget-State-Keys, die das Cluster-Signatur-Suffix tragen.
# Wird für präfixbasierte Löschung in invalidate_cluster_dependent_state verwendet.
CLUSTER_DEPENDENT_SESSION_KEY_PREFIXES: list[str] = [
    "atz_matrix_editor_live_",
    "quit_matrix_editor_live_fixed_",
]


@dataclass
class DiscoveredClusterSource:
    mode: str
    subtype: str
    status: str = STATUS_MISSING
    is_valid: bool = False
    priority_rank: int = 99
    display_label: str = ""
    description: str = ""
    source_path: Optional[str] = None
    session_key: Optional[str] = None
    filename: Optional[str] = None
    file_exists: bool = False
    content_hash: Optional[str] = None
    source_signature: Optional[str] = None
    last_modified_at: Optional[str] = None
    oe_mapping_count: int = 0
    jf_mapping_count: int = 0
    validation_errors: list[str] = field(default_factory=list)
    debug_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActiveClusterSource:
    mode: str
    subtype: str
    status: str = STATUS_FALLBACK
    is_active: bool = True
    is_valid: bool = True
    priority_rank: int = 99
    display_label: str = ""
    description: str = ""
    source_path: Optional[str] = None
    session_key: Optional[str] = None
    persisted_local_path: Optional[str] = None
    filename: Optional[str] = None
    file_exists: bool = False
    content_hash: Optional[str] = None
    source_signature: Optional[str] = None
    activated_at: Optional[str] = None
    last_modified_at: Optional[str] = None
    oe_mapping_count: int = 0
    jf_mapping_count: int = 0
    resolution_reason: str = ""
    validation_errors: list[str] = field(default_factory=list)
    fallback_from: Optional[str] = None
    debug_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClusterMappingBundle:
    oe_map: dict[str, str] = field(default_factory=dict)
    jf_map: dict[Any, str] = field(default_factory=dict)
    source_signature: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_get(session_state: Any, key: str, default: Any = None) -> Any:
    if session_state is None:
        return default
    if hasattr(session_state, "get"):
        try:
            return session_state.get(key, default)
        except Exception:
            return default
    try:
        return session_state[key]
    except Exception:
        return default


def _hash_bytes(data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _get_last_modified_at(path: Optional[str]) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(path)).replace(microsecond=0).isoformat()


def _get_file_debug_meta(path: Optional[str]) -> dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    stat = os.stat(path)
    return {
        "mtime_ns": int(stat.st_mtime_ns),
        "size_bytes": int(stat.st_size),
    }


def _count_nonempty(series: pd.Series) -> int:
    return int(series.fillna("").astype(str).str.strip().ne("").sum())


def _inspect_cluster_excel_bytes(data: bytes) -> dict[str, Any]:
    errors: list[str] = []
    oe_count = 0
    jf_count = 0

    try:
        xls = pd.ExcelFile(io.BytesIO(data))
    except Exception as exc:
        return {
            "is_valid": False,
            "oe_mapping_count": 0,
            "jf_mapping_count": 0,
            "validation_errors": [f"Excel-Datei konnte nicht gelesen werden: {exc}"],
        }

    required_sheets = {"OrgUnits", "JobFamilies"}
    missing_sheets = sorted(required_sheets - set(xls.sheet_names))
    if missing_sheets:
        errors.append(
            "Fehlende Tabellenblätter: " + ", ".join(missing_sheets)
        )
        return {
            "is_valid": False,
            "oe_mapping_count": 0,
            "jf_mapping_count": 0,
            "validation_errors": errors,
        }

    try:
        df_oe = pd.read_excel(xls, sheet_name="OrgUnits")
        df_jf = pd.read_excel(xls, sheet_name="JobFamilies")
    except Exception as exc:
        return {
            "is_valid": False,
            "oe_mapping_count": 0,
            "jf_mapping_count": 0,
            "validation_errors": [f"Excel-Sheets konnten nicht gelesen werden: {exc}"],
        }

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

    if errors:
        return {
            "is_valid": False,
            "oe_mapping_count": 0,
            "jf_mapping_count": 0,
            "validation_errors": errors,
        }

    oe_count = _count_nonempty(df_oe["Cluster"]) if "Cluster" in df_oe.columns else 0
    if has_new:
        jf_count = _count_nonempty(df_jf["Jobfamily Cluster"])
    elif has_old:
        jf_count = _count_nonempty(df_jf["Cluster"])

    return {
        "is_valid": True,
        "oe_mapping_count": oe_count,
        "jf_mapping_count": jf_count,
        "validation_errors": [],
    }


def _build_discovered_source(
    *,
    mode: str,
    subtype: str,
    status: str,
    display_label: str,
    description: str,
    source_path: Optional[str] = None,
    session_key: Optional[str] = None,
    filename: Optional[str] = None,
    file_exists: bool = False,
    data_bytes: Optional[bytes] = None,
    debug_meta: Optional[dict[str, Any]] = None,
) -> DiscoveredClusterSource:
    debug = dict(debug_meta or {})
    content_hash = _hash_bytes(data_bytes) if data_bytes is not None else _hash_file(source_path)
    last_modified_at = _get_last_modified_at(source_path)
    file_debug = _get_file_debug_meta(source_path)
    debug.update(file_debug)
    if data_bytes is not None:
        debug["size_bytes"] = len(data_bytes)

    if data_bytes is not None:
        inspection = _inspect_cluster_excel_bytes(data_bytes)
    elif file_exists and source_path:
        try:
            with open(source_path, "rb") as handle:
                inspection = _inspect_cluster_excel_bytes(handle.read())
        except Exception as exc:
            inspection = {
                "is_valid": False,
                "oe_mapping_count": 0,
                "jf_mapping_count": 0,
                "validation_errors": [f"Datei konnte nicht gelesen werden: {exc}"],
            }
    else:
        inspection = {
            "is_valid": False,
            "oe_mapping_count": 0,
            "jf_mapping_count": 0,
            "validation_errors": [],
        }

    final_status = status
    if status != STATUS_UPLOADED_NOT_APPLIED:
        if not file_exists and data_bytes is None and mode != MODE_SYNTHETIC:
            final_status = STATUS_MISSING
        elif not inspection["is_valid"]:
            final_status = STATUS_INVALID
        elif status not in (STATUS_ACTIVE, STATUS_FALLBACK):
            final_status = STATUS_AVAILABLE
    elif not inspection["is_valid"]:
        final_status = STATUS_INVALID

    is_valid = inspection["is_valid"]
    signature = _build_source_signature(
        mode=mode,
        subtype=subtype,
        status=final_status,
        content_hash=content_hash,
        source_path=source_path,
    )

    return DiscoveredClusterSource(
        mode=mode,
        subtype=subtype,
        status=final_status,
        is_valid=is_valid,
        priority_rank=PRIORITY_BY_SUBTYPE.get(subtype, 99),
        display_label=display_label,
        description=description,
        source_path=os.path.abspath(source_path) if source_path else None,
        session_key=session_key,
        filename=filename,
        file_exists=file_exists,
        content_hash=content_hash,
        source_signature=signature,
        last_modified_at=last_modified_at,
        oe_mapping_count=inspection["oe_mapping_count"],
        jf_mapping_count=inspection["jf_mapping_count"],
        validation_errors=list(inspection["validation_errors"]),
        debug_meta=debug,
    )


def _discover_session_source(session_state: Any) -> Optional[DiscoveredClusterSource]:
    staged_bytes = _state_get(session_state, "cluster_upload_staged_bytes")
    if staged_bytes is not None:
        data = coerce_file_bytes(staged_bytes)
        return _build_discovered_source(
            mode=MODE_UI_UPLOAD,
            subtype=SUBTYPE_UI_UPLOAD_SESSION,
            status=STATUS_UPLOADED_NOT_APPLIED,
            display_label="UI-Upload (staged)",
            description="Hochgeladene Clusterdatei, noch nicht aktiviert.",
            session_key="cluster_upload_staged_bytes",
            filename=_state_get(session_state, "cluster_upload_staged_filename", "Cluster-Upload.xlsx"),
            file_exists=False,
            data_bytes=data,
            debug_meta={
                "activation_approximation": "explicit_staged_key",
                "source_bytes": data,
            },
        )

    active_bytes = _state_get(session_state, "cluster_upload_active_bytes")
    if active_bytes is not None:
        data = coerce_file_bytes(active_bytes)
        return _build_discovered_source(
            mode=MODE_UI_UPLOAD,
            subtype=SUBTYPE_UI_UPLOAD_SESSION,
            status=STATUS_ACTIVE,
            display_label="UI-Upload (Session)",
            description="Aktiver UI-Upload innerhalb der aktuellen Session.",
            session_key="cluster_upload_active_bytes",
            filename=_state_get(session_state, "cluster_upload_active_filename", "Cluster-Upload.xlsx"),
            file_exists=False,
            data_bytes=data,
            debug_meta={
                "activation_approximation": "explicit_active_key",
                "activated_at": _state_get(session_state, "cluster_override_activated_at"),
                "source_bytes": data,
            },
        )

    return None


def _discover_persisted_source(persisted_local_path: str) -> DiscoveredClusterSource:
    path = os.path.abspath(persisted_local_path)
    exists = os.path.exists(path)
    return _build_discovered_source(
        mode=MODE_UI_UPLOAD,
        subtype=SUBTYPE_UI_UPLOAD_PERSISTED,
        status=STATUS_AVAILABLE if exists else STATUS_MISSING,
        display_label="UI-Upload (persistiert)",
        description="Lokale persistierte Kopie eines aktivierten UI-Uploads.",
        source_path=path,
        filename=os.path.basename(path),
        file_exists=exists,
        debug_meta={
            "technical_role": "ui_upload.persisted_local_copy",
        },
    )


def _discover_external_source(external_file_path: str) -> DiscoveredClusterSource:
    path = os.path.abspath(external_file_path)
    exists = os.path.exists(path)
    return _build_discovered_source(
        mode=MODE_INPUT_FOLDER,
        subtype=SUBTYPE_INPUT_EXTERNAL,
        status=STATUS_AVAILABLE if exists else STATUS_MISSING,
        display_label="Input-Ordner",
        description="Externe Clusterdatei aus dem Input-Ordner.",
        source_path=path,
        filename=os.path.basename(path),
        file_exists=exists,
    )


def _discover_synthetic_source() -> DiscoveredClusterSource:
    signature = _build_source_signature(
        mode=MODE_SYNTHETIC,
        subtype=SUBTYPE_SYNTHETIC_FALLBACK,
        status=STATUS_AVAILABLE,
        content_hash="synthetic.default_fallback",
        source_path=None,
    )
    return DiscoveredClusterSource(
        mode=MODE_SYNTHETIC,
        subtype=SUBTYPE_SYNTHETIC_FALLBACK,
        status=STATUS_AVAILABLE,
        is_valid=True,
        priority_rank=PRIORITY_BY_SUBTYPE[SUBTYPE_SYNTHETIC_FALLBACK],
        display_label="Synthetisch / Fallback",
        description="Expliziter Fallback-Kandidat, wenn keine reale Clusterquelle aktiv ist.",
        source_path=None,
        session_key=None,
        filename=None,
        file_exists=False,
        content_hash="synthetic.default_fallback",
        source_signature=signature,
        last_modified_at=None,
        oe_mapping_count=0,
        jf_mapping_count=0,
        validation_errors=[],
        debug_meta={"technical_role": "synthetic.default_fallback"},
    )


def discover_cluster_sources(
    session_state: Any = None,
    persisted_local_path: Optional[str] = None,
    external_file_path: Optional[str] = None,
) -> list[DiscoveredClusterSource]:
    persisted_path = persisted_local_path or DEFAULT_PERSISTED_LOCAL_PATH
    external_path = external_file_path or resolve_default_external_cluster_file_path()

    discovered: list[DiscoveredClusterSource] = []

    session_source = _discover_session_source(session_state)
    if session_source is not None:
        discovered.append(session_source)

    discovered.append(_discover_persisted_source(persisted_path))
    discovered.append(_discover_external_source(external_path))
    discovered.append(_discover_synthetic_source())

    return discovered


def _to_active_source(
    source: DiscoveredClusterSource,
    *,
    status: str,
    is_active: bool,
    resolution_reason: str,
    fallback_from: Optional[str] = None,
) -> ActiveClusterSource:
    return ActiveClusterSource(
        mode=source.mode,
        subtype=source.subtype,
        status=status,
        is_active=is_active,
        is_valid=source.is_valid,
        priority_rank=source.priority_rank,
        display_label=source.display_label,
        description=source.description,
        source_path=source.source_path,
        session_key=source.session_key,
        persisted_local_path=source.source_path if source.subtype == SUBTYPE_UI_UPLOAD_PERSISTED else None,
        filename=source.filename,
        file_exists=source.file_exists,
        content_hash=source.content_hash,
        source_signature=source.source_signature,
        activated_at=source.debug_meta.get("activated_at"),
        last_modified_at=source.last_modified_at,
        oe_mapping_count=source.oe_mapping_count,
        jf_mapping_count=source.jf_mapping_count,
        resolution_reason=resolution_reason,
        validation_errors=list(source.validation_errors),
        fallback_from=fallback_from,
        debug_meta=dict(source.debug_meta),
    )


def resolve_active_cluster_source(
    discovered_sources: Iterable[DiscoveredClusterSource],
) -> ActiveClusterSource:
    sources = list(discovered_sources)
    if not sources:
        synthetic = _discover_synthetic_source()
        return _to_active_source(
            synthetic,
            status=STATUS_FALLBACK,
            is_active=True,
            resolution_reason="No discovered sources available. Falling back to synthetic.",
            fallback_from="ui_upload.session, ui_upload.persisted_local_copy, input_folder.external_file",
        )

    valid_candidates: list[DiscoveredClusterSource] = []
    skipped: list[str] = []
    for source in sorted(sources, key=lambda item: item.priority_rank):
        if not source.is_valid:
            skipped.append(f"{source.subtype}:invalid")
            continue
        if source.status == STATUS_UPLOADED_NOT_APPLIED:
            skipped.append(f"{source.subtype}:uploaded_not_applied")
            continue
        valid_candidates.append(source)

    chosen = valid_candidates[0] if valid_candidates else None
    if chosen is None:
        synthetic = next(
            (src for src in sources if src.subtype == SUBTYPE_SYNTHETIC_FALLBACK),
            _discover_synthetic_source(),
        )
        return _to_active_source(
            synthetic,
            status=STATUS_FALLBACK,
            is_active=True,
            resolution_reason="No valid real cluster source available. Falling back to synthetic.",
            fallback_from=", ".join(skipped) if skipped else "no_valid_sources",
        )

    if chosen.subtype == SUBTYPE_SYNTHETIC_FALLBACK:
        return _to_active_source(
            chosen,
            status=STATUS_FALLBACK,
            is_active=True,
            resolution_reason="Synthetic fallback selected because no higher-priority valid source is active.",
            fallback_from=", ".join(skipped) if skipped else None,
        )

    return _to_active_source(
        chosen,
        status=STATUS_ACTIVE,
        is_active=True,
        resolution_reason=f"Selected highest-priority valid source: {chosen.subtype}.",
        fallback_from=", ".join(skipped) if skipped else None,
    )


def get_active_cluster_source(
    session_state: Any = None,
    persisted_local_path: Optional[str] = None,
    external_file_path: Optional[str] = None,
) -> ActiveClusterSource:
    discovered = discover_cluster_sources(
        session_state=session_state,
        persisted_local_path=persisted_local_path,
        external_file_path=external_file_path,
    )
    return resolve_active_cluster_source(discovered)


def invalidate_cluster_dependent_state(session_state: Any, reason: str) -> dict[str, Any]:
    removed_keys: list[str] = []
    missing_keys: list[str] = []

    for key in CLUSTER_DEPENDENT_SESSION_KEYS:
        exists = False
        try:
            exists = key in session_state
        except Exception:
            exists = _state_get(session_state, key, None) is not None

        if exists:
            try:
                del session_state[key]
            except Exception:
                try:
                    session_state.pop(key, None)
                except Exception:
                    pass
            removed_keys.append(key)
        else:
            missing_keys.append(key)

    # Präfixbasierte Löschung für Widget-State-Keys mit Cluster-Signatur-Suffix.
    # Die tatsächlichen Session-State-Keys heißen z. B. "atz_matrix_editor_live_<SIG>",
    # nicht der Basis-Name ohne Suffix, der in CLUSTER_DEPENDENT_SESSION_KEYS steht.
    try:
        all_keys = list(session_state.keys())
    except Exception:
        all_keys = []
    for key in all_keys:
        for prefix in CLUSTER_DEPENDENT_SESSION_KEY_PREFIXES:
            if key.startswith(prefix):
                try:
                    del session_state[key]
                    removed_keys.append(key)
                except Exception:
                    try:
                        session_state.pop(key, None)
                        removed_keys.append(key)
                    except Exception:
                        pass
                break

    return {
        "reason": reason,
        "removed_keys": removed_keys,
        "missing_keys": missing_keys,
        "removed_count": len(removed_keys),
    }


def store_active_cluster_source_in_session(
    session_state: Any,
    active_cluster_source: ActiveClusterSource,
) -> dict[str, Any]:
    payload = serialize_active_cluster_source(active_cluster_source)
    session_state["active_cluster_source"] = payload
    session_state["active_cluster_source_signature"] = active_cluster_source.source_signature
    session_state["active_cluster_source_status"] = active_cluster_source.status
    session_state["active_cluster_source_mode"] = active_cluster_source.mode
    session_state["active_cluster_source_subtype"] = active_cluster_source.subtype
    return payload


def clear_active_cluster_source_from_session(session_state: Any) -> list[str]:
    removed: list[str] = []
    for key in (
        "active_cluster_source",
        "active_cluster_source_signature",
        "active_cluster_source_status",
        "active_cluster_source_mode",
        "active_cluster_source_subtype",
    ):
        if hasattr(session_state, "get"):
            exists = session_state.get(key) is not None or key in session_state
        else:
            exists = False
            try:
                exists = key in session_state
            except Exception:
                exists = False
        if exists:
            try:
                del session_state[key]
            except Exception:
                try:
                    session_state.pop(key, None)
                except Exception:
                    pass
            removed.append(key)
    return removed


def serialize_active_cluster_source(
    active_cluster_source: ActiveClusterSource,
    *,
    include_source_bytes: bool = False,
) -> dict[str, Any]:
    payload = active_cluster_source.to_dict()
    debug_meta = dict(payload.get("debug_meta", {}) or {})
    if not include_source_bytes:
        for key in ("source_bytes", "session_bytes", "payload_bytes"):
            debug_meta.pop(key, None)
    payload["debug_meta"] = debug_meta
    return payload


def deserialize_active_cluster_source(
    payload: dict[str, Any] | ActiveClusterSource | None,
    *,
    source_bytes: Optional[bytes] = None,
) -> Optional[ActiveClusterSource]:
    if payload is None:
        return None
    if isinstance(payload, ActiveClusterSource):
        active_source = payload
    else:
        data = dict(payload)
        debug_meta = dict(data.get("debug_meta", {}) or {})
        if source_bytes is not None:
            debug_meta["source_bytes"] = source_bytes
        data["debug_meta"] = debug_meta
        active_source = ActiveClusterSource(**data)
    if source_bytes is not None:
        active_source.debug_meta["source_bytes"] = source_bytes
    return active_source


def get_active_cluster_source_from_session(session_state: Any) -> Optional[ActiveClusterSource]:
    payload = _state_get(session_state, "active_cluster_source")
    return deserialize_active_cluster_source(payload)


__all__ = [
    "ActiveClusterSource",
    "CLUSTER_DEPENDENT_SESSION_KEYS",
    "CLUSTER_DEPENDENT_SESSION_KEY_PREFIXES",
    "clear_active_cluster_source_from_session",
    "ClusterMappingBundle",
    "deserialize_active_cluster_source",
    "DiscoveredClusterSource",
    "DEFAULT_EXTERNAL_FILE_PATH",
    "DEFAULT_PERSISTED_LOCAL_PATH",
    "CLUSTER_DATA_DIR",
    "resolve_default_external_cluster_file_path",
    "STATUS_ACTIVE",
    "STATUS_AVAILABLE",
    "STATUS_FALLBACK",
    "STATUS_INVALID",
    "STATUS_MISSING",
    "STATUS_UPLOADED_NOT_APPLIED",
    "discover_cluster_sources",
    "get_active_cluster_source",
    "get_active_cluster_source_from_session",
    "invalidate_cluster_dependent_state",
    "resolve_active_cluster_source",
    "serialize_active_cluster_source",
    "store_active_cluster_source_in_session",
]
