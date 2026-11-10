"""App-facing Monte Carlo request and session contract.

The existing CompactPlus N=1 calculation and its compact_sim_* objects stay
the concrete reference run. This module only handles N>1 statistics.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, MutableMapping

import pandas as pd

from utils.monte_carlo_orchestrator import execute_ensemble
from utils.monte_carlo_input_fingerprint import frame_fingerprint
from utils.monte_carlo_product_worker import (
    freeze_product_worker_input, product_input_fingerprint, run_product_seed_with_ist_charts,
)
from utils.monte_carlo_run_contract import RunPlan, build_run_plan
from utils.settings_loader import get_setting
from utils.simulation_validation import value_fingerprint


ENSEMBLE_KEY = "monte_carlo_ensemble_result"


@dataclass(frozen=True)
class EnsembleRequest:
    plan: RunPlan
    frozen_input: dict[str, Any]
    include_future_hires: bool


def prepare_ensemble_request(*, snapshot: pd.DataFrame, atz: pd.DataFrame,
                             base_date, target_date, attrition_params: dict,
                             accession_params: dict, active_cluster_source,
                             cluster_mapping_bundle, cluster_source_signature,
                             count: int, band_level: float = 0.8,
                             salary_automation=None, exclusions=None,
                             include_future_hires: bool | None = None,
                             session_values=None) -> EnsembleRequest:
    """Build the signed plan and worker payload from one captured app state."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise ValueError("N=1 must use the existing CompactPlus page path")
    payload = freeze_product_worker_input(
        snapshot=snapshot, atz=atz, base_date=base_date, target_date=target_date,
        active_cluster_source=active_cluster_source,
        cluster_mapping_bundle=cluster_mapping_bundle,
        cluster_source_signature=cluster_source_signature,
        salary_automation=salary_automation, exclusions=exclusions,
        session_values=session_values)
    if include_future_hires is None:
        include_future_hires = bool(get_setting("include_future_hires", False))
    plan = build_run_plan(
        base_date=payload["base_date"], target_date=payload["target_date"],
        attrition_params=attrition_params, accession_params=accession_params,
        exclusions=payload["settings"]["exclusions"],
        include_future_hires=include_future_hires,
        input_fingerprint=product_input_fingerprint(payload),
        cluster_source_signature=payload["cluster_source_signature"],
        count=count, band_level=band_level)
    return EnsembleRequest(plan, payload, include_future_hires)


def invalidate_ensemble(state: MutableMapping[str, Any]) -> None:
    state.pop(ENSEMBLE_KEY, None)


def _reference_matches_request(state: MutableMapping[str, Any],
                               request: EnsembleRequest) -> bool:
    """Bind the saved legacy reference to the exact inputs of seed zero."""
    payload, plan = request.frozen_input, request.plan
    try:
        legacy = json.loads(state["compact_sim_signature"])
        expected = {
            "target_date": plan.target_date,
            "base_date": plan.base_date,
            "abgaenge_params": plan.params_for_index(0)[0],
            "zugaenge_params": plan.params_for_index(0)[1],
            "exclusions": payload["settings"]["exclusions"],
            "include_future_hires": request.include_future_hires,
            "cluster_source_signature": plan.cluster_source_signature,
        }
        if legacy != json.loads(json.dumps(expected, sort_keys=True, default=str)):
            return False
        context = state["compact_sim_event_context"]
        if (frame_fingerprint(context["source_snapshot"])
                != frame_fingerprint(payload["snapshot"])
                or frame_fingerprint(context["df_atz"])
                != frame_fingerprint(payload["atz"])):
            return False
        if context.get("cluster_mapping") != payload["cluster_mapping_bundle"].to_dict():
            return False
        if context.get("cost_lookup_fingerprint") != value_fingerprint(
                payload["session_values"].get("tvoed_lookup", {})):
            return False
        export = state["compact_sim_export_context"]
        return export.get("salary_automation", {}) == payload["settings"]["salary_automation"]
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def publish_ensemble(state: MutableMapping[str, Any], request: EnsembleRequest,
                     result: dict[str, Any]) -> None:
    """Store only complete statistics bound to the current concrete reference."""
    plan = request.plan
    if product_input_fingerprint(request.frozen_input) != plan.input_fingerprint:
        raise ValueError("Ensemble input changed after request preparation")
    if (result.get("status") != "complete"
            or result.get("signature") != plan.signature
            or result.get("input_fingerprint") != plan.input_fingerprint
            or result.get("requested_count") != plan.count
            or tuple(map(tuple, result.get("seed_pairs", ()))) != plan.seed_pairs
            or not isinstance(result.get("statistics"), dict)
            or result["statistics"].get("status") != "complete"):
        raise ValueError("Incomplete or mismatched ensemble cannot be published")
    reference_signature = state.get("compact_sim_signature")
    reference_instance = state.get("simulation_validation_instance")
    if (not reference_signature or not isinstance(reference_instance, int)
            or state.get("compact_sim_prepared_df") is None
            or not _reference_matches_request(state, request)):
        raise ValueError("A matching concrete CompactPlus reference run is required")
    state[ENSEMBLE_KEY] = {
        "contract_version": 1,
        "plan_signature": plan.signature,
        "input_fingerprint": plan.input_fingerprint,
        "reference_signature": reference_signature,
        "reference_instance": reference_instance,
        "count": plan.count,
        "seed_pairs": plan.seed_pairs,
        "band_level": plan.band_level,
        "status": "complete",
        "statistics": result["statistics"],
        "chart_statistics": result.get("chart_statistics"),
        "seconds": result.get("seconds"),
        "run_seconds": result.get("run_seconds"),
    }


def current_ensemble(state: MutableMapping[str, Any],
                     request: EnsembleRequest) -> dict[str, Any] | None:
    """Return a complete current ensemble or None; never return stale means."""
    saved = state.get(ENSEMBLE_KEY)
    if not isinstance(saved, dict) or saved.get("status") != "complete":
        return None
    plan = request.plan
    if (saved.get("plan_signature") != plan.signature
            or saved.get("input_fingerprint") != plan.input_fingerprint
            or product_input_fingerprint(request.frozen_input) != plan.input_fingerprint
            or saved.get("reference_signature") != state.get("compact_sim_signature")
            or saved.get("reference_instance") != state.get("simulation_validation_instance")
            or saved.get("count") != plan.count
            or tuple(map(tuple, saved.get("seed_pairs", ()))) != plan.seed_pairs
            or not isinstance(saved.get("statistics"), dict)
            or saved["statistics"].get("status") != "complete"
            or not _reference_matches_request(state, request)):
        return None
    return saved


def calculate_ensemble(state: MutableMapping[str, Any], request: EnsembleRequest,
                       *, workers: int = 3,
                       on_progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Run N>1 and publish only if the concrete reference did not change."""
    if (state.get("compact_sim_prepared_df") is None
            or not isinstance(state.get("simulation_validation_instance"), int)
            or not _reference_matches_request(state, request)):
        raise ValueError("A matching concrete CompactPlus reference run is required")
    reference = (state.get("compact_sim_signature"),
                 state.get("simulation_validation_instance"))
    invalidate_ensemble(state)
    result = execute_ensemble(
        request.plan, frozen_input=request.frozen_input,
        input_fingerprint=product_input_fingerprint,
        run_seed=run_product_seed_with_ist_charts, workers=workers,
        require_ist_charts=True,
        on_progress=on_progress)
    if result["status"] == "complete":
        if reference != (state.get("compact_sim_signature"),
                         state.get("simulation_validation_instance")):
            raise ValueError("Concrete reference changed during ensemble calculation")
        publish_ensemble(state, request, result)
    return result
