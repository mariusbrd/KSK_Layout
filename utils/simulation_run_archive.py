"""Versioned, complete local archive of an approved CompactPlus run."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import hashlib
import json
import pickle
from zipfile import ZIP_DEFLATED, ZipFile, BadZipFile

import pandas as pd

from utils.monte_carlo_app_run import ENSEMBLE_KEY, REQUEST_KEY, EnsembleRequest, current_ensemble
from utils.simulation_validation import (
    APPROVAL_KEY, DRAFT_KEY, HISTORY_KEY, INSTANCE_KEY, current_view,
)


ARCHIVE_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024
RUN_KEYS = (
    "compact_sim_signature", "compact_sim_cluster_source_signature",
    "compact_sim_prepared_df", "compact_sim_status_quo_df",
    "compact_sim_status_quo_signature", "compact_sim_metadata",
    "compact_sim_audit_tables", "compact_sim_event_context",
    "compact_sim_event_summary", "compact_sim_target_date_cached",
    "compact_sim_abgaenge_params", "compact_sim_zugaenge_params",
    "compact_sim_active_cluster_source", "compact_sim_export_context",
    "compact_plus_mode", "simulation_params", INSTANCE_KEY, APPROVAL_KEY,
    DRAFT_KEY, HISTORY_KEY, ENSEMBLE_KEY, REQUEST_KEY,
    "org_sim_representative_details",
)


def _validate_state(state: dict) -> None:
    view = current_view(state)
    if view is None:
        raise ValueError("Kein vollständiger Simulationslauf vorhanden")
    if not isinstance(state.get("simulation_params"), dict):
        raise ValueError("Gespeicherte Simulationsparameter fehlen")
    metadata = view.metadata
    base = pd.Timestamp(metadata["base_date"]).normalize()
    target = pd.Timestamp(metadata["target_date"]).normalize()
    if target < base or pd.Timestamp(state["compact_sim_target_date_cached"]).normalize() != target:
        raise ValueError("Zieldatum im Laufarchiv ist widersprüchlich")
    params = state["simulation_params"]
    if pd.Timestamp(params["compact_plus"]["target_date"]).normalize() != target:
        raise ValueError("Zieldatum in den Parametern stimmt nicht mit dem Lauf überein")
    count = int(params["compact_plus"]["run_count"])
    if count > 1:
        request = state.get(REQUEST_KEY)
        if not isinstance(request, EnsembleRequest) or current_ensemble(state, request) is None:
            raise ValueError("Monte-Carlo-Ergebnis und gespeicherte Laufanforderung passen nicht zusammen")
        if request.plan.count != count or pd.Timestamp(request.plan.target_date).normalize() != target:
            raise ValueError("Laufzahl oder Zieldatum des Ensembles ist widersprüchlich")
    elif state.get(ENSEMBLE_KEY) is not None:
        raise ValueError("Einzellauf enthält unerwartete Monte-Carlo-Daten")


def build_run_archive(state) -> bytes:
    """Archive exact result objects and frozen worker input without rerunning seeds."""
    selected = {key: state[key] for key in RUN_KEYS if key in state}
    if isinstance(selected.get("simulation_params"), dict):
        selected["simulation_params"] = deepcopy(selected["simulation_params"])
        selected["simulation_params"].setdefault("compact_plus", {})["target_date"] = state.get(
            "compact_sim_target_date_cached")
    _validate_state(selected)
    if selected["simulation_params"]["compact_plus"]["run_count"] > 1:
        from utils.monte_carlo_representative_runs import select_representative_runs
        from utils.monte_carlo_representative_data import materialize_representative_seed_detail

        ensemble = selected[ENSEMBLE_KEY]
        request = selected[REQUEST_KEY]
        cache = dict(selected.get("org_sim_representative_details") or {})
        pending = state.get("org_sim_representative_prefetch", {}) or {}
        for index in sorted({item["index"] for item in select_representative_runs(ensemble).values()} - {0}):
            key = (ensemble["plan_signature"], selected["compact_sim_signature"],
                   selected[INSTANCE_KEY], index)
            if key not in cache:
                future = pending.get(key)
                cache[key] = (future.result() if future is not None else
                              materialize_representative_seed_detail(request, ensemble, index))
        selected["org_sim_representative_details"] = cache
    payload = pickle.dumps(selected, protocol=pickle.HIGHEST_PROTOCOL)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("Laufarchiv ist für den Export zu groß")
    manifest = {
        "format": "ksk-compactplus-run", "version": ARCHIVE_VERSION,
        "sha256": hashlib.sha256(payload).hexdigest(), "payload_bytes": len(payload),
        "base_date": str(pd.Timestamp(selected["compact_sim_metadata"]["base_date"]).date()),
        "target_date": str(pd.Timestamp(selected["compact_sim_metadata"]["target_date"]).date()),
        "run_count": selected["simulation_params"]["compact_plus"]["run_count"],
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("run.pkl", payload)
    return output.getvalue()


def read_run_archive(raw: bytes) -> dict:
    """Read only archives created by this application. Pickle requires trusted files."""
    if not isinstance(raw, bytes) or len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("Ungültige oder zu große Archivdatei")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            if set(archive.namelist()) != {"manifest.json", "run.pkl"}:
                raise ValueError("Laufarchiv hat unerwartete Dateien")
            if archive.getinfo("run.pkl").file_size > MAX_PAYLOAD_BYTES:
                raise ValueError("Laufarchiv ist zu groß")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "ksk-compactplus-run" or manifest.get("version") != ARCHIVE_VERSION:
                raise ValueError("Unbekanntes Laufarchiv-Format")
            payload = archive.read("run.pkl")
    except (BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Laufarchiv ist beschädigt") from exc
    if (len(payload) != manifest.get("payload_bytes")
            or hashlib.sha256(payload).hexdigest() != manifest.get("sha256")):
        raise ValueError("Prüfsumme des Laufarchivs stimmt nicht")
    try:
        state = pickle.loads(payload)
    except (pickle.UnpicklingError, AttributeError, EOFError, ImportError) as exc:
        raise ValueError("Gespeicherter Lauf ist mit dieser Programmversion nicht lesbar") from exc
    if not isinstance(state, dict):
        raise ValueError("Laufarchiv enthält keinen gültigen Zustand")
    _validate_state(state)
    if (str(pd.Timestamp(state["compact_sim_metadata"]["base_date"]).date()) != manifest["base_date"]
            or str(pd.Timestamp(state["compact_sim_metadata"]["target_date"]).date()) != manifest["target_date"]
            or state["simulation_params"]["compact_plus"]["run_count"] != manifest["run_count"]):
        raise ValueError("Archivbeschreibung und Laufdaten stimmen nicht überein")
    return state


def restore_run_archive(state, raw: bytes) -> dict:
    """Validate completely before mutating the live session."""
    restored = read_run_archive(raw)
    pending = state.get("org_sim_representative_prefetch") or {}
    for future in pending.values():
        future.cancel()
    for key in (
        "org_sim_representative_prefetch", "jf_yearly_view_cache",
        "jf_yearly_prepared_export", "simulation_approval_candidates_cache",
    ):
        state.pop(key, None)
    for key in RUN_KEYS:
        state.pop(key, None)
    for key in list(state):
        if key.startswith("sim_params_") or key in {"compact_sim_target_date", "compact_sim_target_date_seen"}:
            state.pop(key, None)
    state.update(restored)
    state["compact_sim_target_date"] = pd.Timestamp(restored["compact_sim_target_date_cached"]).date()
    state["compact_sim_target_date_seen"] = state["compact_sim_target_date"]
    state["compact_sim_imported_archive"] = True
    return restored
