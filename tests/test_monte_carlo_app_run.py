"""The app stores only complete statistics tied to the exact reference run."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd
import pytest

from utils.monte_carlo_app_run import (
    ENSEMBLE_KEY, calculate_ensemble, current_ensemble, invalidate_ensemble,
    prepare_ensemble_request, publish_ensemble,
)
from utils.simulation_validation import register_new_run, value_fingerprint


@dataclass
class _Bundle:
    mapping: dict

    def to_dict(self):
        return self.mapping


def _request(*, count=2, mak=1.0, salary=None):
    snapshot = pd.DataFrame({"PersNr": ["A"], "MAK": [mak]})
    atz = pd.DataFrame({"PersNr": ["A"], "Phase": ["AR"]})
    return prepare_ensemble_request(
        snapshot=snapshot, atz=atz,
        base_date="2025-12-31", target_date="2027-06-15",
        attrition_params={"random_seed": 7, "quit": {"rate": 0.1}},
        accession_params={"random_seed": 42, "new_hires": {"active": True}},
        active_cluster_source=None, cluster_mapping_bundle=_Bundle({"A": "North"}),
        cluster_source_signature="cluster-v1", count=count,
        salary_automation={} if salary is None else salary,
        exclusions={"org_units": []}, include_future_hires=True,
        session_values={"tvoed_lookup": {"E9": 100.0}})


def _state(request):
    plan, payload = request.plan, request.frozen_input
    attrition, accession = plan.params_for_index(0)
    legacy = {
        "target_date": plan.target_date, "base_date": plan.base_date,
        "abgaenge_params": attrition, "zugaenge_params": accession,
        "exclusions": payload["settings"]["exclusions"],
        "include_future_hires": True,
        "cluster_source_signature": plan.cluster_source_signature,
    }
    return {
        "compact_sim_signature": json.dumps(legacy, sort_keys=True, default=str),
        "simulation_validation_instance": 1,
        "compact_sim_prepared_df": pd.DataFrame({"PersNr": ["A"]}),
        "compact_sim_event_context": {
            "source_snapshot": payload["snapshot"].copy(),
            "df_atz": payload["atz"].copy(),
            "cluster_mapping": payload["cluster_mapping_bundle"].to_dict(),
            "cost_lookup_fingerprint": value_fingerprint(
                payload["session_values"]["tvoed_lookup"]),
        },
        "compact_sim_export_context": {
            "salary_automation": payload["settings"]["salary_automation"]},
    }


def _result(request):
    plan = request.plan
    return {
        "status": "complete", "signature": plan.signature,
        "input_fingerprint": plan.input_fingerprint,
        "requested_count": plan.count, "seed_pairs": plan.seed_pairs,
        "statistics": {"status": "complete", "target": {"heads": {"mean": 1.0}}},
        "seconds": 1.0, "run_seconds": {0: 0.5, 1: 0.5},
    }


def test_publish_and_stale_detection_bind_reference_and_inputs():
    request = _request()
    state = _state(request)
    publish_ensemble(state, request, _result(request))
    saved = current_ensemble(state, request)
    assert saved is state[ENSEMBLE_KEY]
    assert "snapshot" not in saved and "atz" not in saved
    assert current_ensemble(state, _request(count=3)) is None
    assert current_ensemble(state, _request(mak=0.8)) is None
    assert current_ensemble(state, _request(salary={"enabled": True})) is None
    state["simulation_validation_instance"] += 1
    assert current_ensemble(state, request) is None


def test_reference_mismatch_and_incomplete_result_never_publish():
    request = _request()
    state = _state(request)
    state["compact_sim_event_context"]["source_snapshot"].loc[0, "MAK"] = 0.5
    with pytest.raises(ValueError, match="matching concrete"):
        publish_ensemble(state, request, _result(request))
    assert ENSEMBLE_KEY not in state
    state = _state(request)
    incomplete = _result(request)
    incomplete["status"] = "incomplete"
    with pytest.raises(ValueError, match="Incomplete"):
        publish_ensemble(state, request, incomplete)
    assert ENSEMBLE_KEY not in state


def test_recompute_invalidates_ensemble_without_changing_legacy_values():
    request = _request()
    state = _state(request)
    old_signature = state["compact_sim_signature"]
    publish_ensemble(state, request, _result(request))
    register_new_run(state)
    assert ENSEMBLE_KEY not in state
    assert state["compact_sim_signature"] == old_signature
    assert state["simulation_validation_instance"] == 2
    publish_ensemble(state, request, _result(request))
    invalidate_ensemble(state)
    assert current_ensemble(state, request) is None


def test_single_run_is_rejected_before_request_building():
    with pytest.raises(ValueError, match="N=1"):
        _request(count=1)


def test_app_calculation_checks_reference_before_start_and_publishes_only_complete(monkeypatch):
    request = _request()
    state = _state(request)
    calls = []
    monkeypatch.setattr("utils.monte_carlo_app_run.execute_ensemble",
                        lambda *args, **kwargs: calls.append(kwargs) or _result(request))
    result = calculate_ensemble(state, request, workers=2)
    assert result["status"] == "complete"
    assert len(calls) == 1 and calls[0]["workers"] == 2
    assert current_ensemble(state, request) is not None
    state["compact_sim_event_context"]["source_snapshot"].loc[0, "MAK"] = 0.2
    with pytest.raises(ValueError, match="matching concrete"):
        calculate_ensemble(state, request)
    assert len(calls) == 1
