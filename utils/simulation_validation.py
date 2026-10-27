"""Approval state for a saved simulation run.

The forecast's compact_sim_* artifacts remain the immutable source.  In the
no-adjustment case the approved view returns those exact objects, without a
second forecast, conversion, or aggregation pass.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import pickle
from typing import Any, MutableMapping

import pandas as pd


INSTANCE_KEY = "simulation_validation_instance"
APPROVAL_KEY = "simulation_validation_approval"
DRAFT_KEY = "simulation_validation_draft"
HISTORY_KEY = "simulation_validation_history"


def value_fingerprint(value: Any) -> str | None:
    """Detect changes to saved auxiliary inputs used by deterministic adjustment."""
    try:
        return hashlib.sha256(pickle.dumps(value, protocol=4)).hexdigest()
    except Exception:
        # A non-serializable lookup may disable adjustment, never the forecast.
        return None


@dataclass(frozen=True)
class SimulationView:
    signature: str
    instance: int
    prepared_df: Any
    status_quo_df: Any
    metadata: dict
    audit_tables: dict
    event_context: dict
    event_summary: dict
    export_context: dict
    version: str
    approved: bool


def register_new_run(state: MutableMapping[str, Any]) -> None:
    """A recomputation requires a new approval even with identical parameters."""
    state[INSTANCE_KEY] = int(state.get(INSTANCE_KEY, 0)) + 1
    state.pop(APPROVAL_KEY, None)
    state.pop(DRAFT_KEY, None)


def invalidate_approval(state: MutableMapping[str, Any]) -> None:
    state.pop(APPROVAL_KEY, None)


def current_view(state: MutableMapping[str, Any], *, require_approval: bool = False,
                 use_approved: bool = True) -> SimulationView | None:
    signature = state.get("compact_sim_signature")
    prepared = state.get("compact_sim_prepared_df")
    context = state.get("compact_sim_event_context")
    summary = state.get("compact_sim_event_summary")
    if not signature or prepared is None or not isinstance(context, dict) or not isinstance(summary, dict):
        return None
    if context.get("run_signature") != signature or summary.get("run_signature") != signature:
        return None
    instance = int(state.get(INSTANCE_KEY, 0))
    approval = state.get(APPROVAL_KEY)
    original_version = f"{signature}::{instance}::original"
    approved_original = bool(
        isinstance(approval, dict)
        and approval.get("signature") == signature
        and approval.get("instance") == instance
        and approval.get("version") == original_version
    )
    original = SimulationView(
        signature=signature,
        instance=instance,
        prepared_df=prepared,
        status_quo_df=state.get("compact_sim_status_quo_df"),
        metadata=state.get("compact_sim_metadata", {}),
        audit_tables=state.get("compact_sim_audit_tables", {}),
        event_context=context,
        event_summary=summary,
        export_context=state.get("compact_sim_export_context", {}),
        version=original_version,
        approved=approved_original,
    )
    draft = state.get(DRAFT_KEY)
    if use_approved and (
        isinstance(draft, SimulationView)
        and draft.signature == signature and draft.instance == instance
        and isinstance(approval, dict)
        and approval.get("version") == draft.version
        and approval.get("signature") == signature
        and approval.get("instance") == instance
    ):
        return replace(draft, approved=True)
    if require_approval and not approved_original:
        return None
    return original


def approve_original(state: MutableMapping[str, Any], *, approved_at: str) -> SimulationView:
    view = current_view(state, use_approved=False)
    if view is None:
        raise ValueError("Es liegt kein vollständiger Simulationslauf zur Freigabe vor.")
    if (not isinstance(view.event_context.get("baseline_snapshot"), pd.DataFrame)
            or not isinstance(view.event_context.get("final_snapshot"), pd.DataFrame)
            or not isinstance(view.event_summary.get("events"), pd.DataFrame)
            or not isinstance(view.event_summary.get("stock_reconciliation"), dict)):
        raise ValueError("Ausgangsbestand, Zielbestand oder Ereignisprüfung des Laufs fehlen.")
    if view.event_summary.get("stock_reconciliation", {}).get("status") == "Abweichung":
        raise ValueError("Die OE-Bestandsüberleitung des Originallaufs ist nicht abgestimmt.")
    state.pop(DRAFT_KEY, None)
    approval = {
        "signature": view.signature,
        "instance": view.instance,
        "version": view.version,
        "approved_at": approved_at,
        "kind": "original",
    }
    state[APPROVAL_KEY] = approval
    state[HISTORY_KEY] = [*state.get(HISTORY_KEY, []), approval.copy()]
    return current_view(state, require_approval=True)


def stage_adjustment(state: MutableMapping[str, Any], draft: SimulationView) -> None:
    original = current_view(state, use_approved=False)
    if original is None or original.signature != draft.signature or original.instance != draft.instance:
        raise ValueError("Die Justierung gehört nicht zum aktuellen Lauf.")
    if not draft.version.startswith(f"{original.signature}::{original.instance}::adjusted::"):
        raise ValueError("Ungültige Ergebnisversion der Justierung.")
    if (draft.event_context.get("run_signature") != draft.version
            or draft.event_summary.get("run_signature") != draft.version):
        raise ValueError("Bestand und Ereignisse der Justierung gehören nicht zur selben Ergebnisversion.")
    state[DRAFT_KEY] = draft
    invalidate_approval(state)


def approve_adjustment(state: MutableMapping[str, Any], *, approved_at: str) -> SimulationView:
    draft = state.get(DRAFT_KEY)
    original = current_view(state, use_approved=False)
    if not isinstance(draft, SimulationView) or original is None:
        raise ValueError("Es liegt keine geprüfte Justierung vor.")
    if draft.signature != original.signature or draft.instance != original.instance:
        raise ValueError("Die Justierung gehört nicht zum aktuellen Lauf.")
    if draft.event_summary.get("stock_reconciliation", {}).get("status") != "Abgestimmt":
        raise ValueError("Die korrigierte Bestandsüberleitung ist nicht abgestimmt.")
    approval = {
        "signature": draft.signature,
        "instance": draft.instance,
        "version": draft.version,
        "approved_at": approved_at,
        "kind": "adjusted",
        "adjustment": draft.metadata.get("adjustment"),
    }
    state[APPROVAL_KEY] = approval
    state[HISTORY_KEY] = [*state.get(HISTORY_KEY, []), approval.copy()]
    return current_view(state, require_approval=True)


def clear_adjustment(state: MutableMapping[str, Any]) -> None:
    state.pop(DRAFT_KEY, None)
    invalidate_approval(state)
