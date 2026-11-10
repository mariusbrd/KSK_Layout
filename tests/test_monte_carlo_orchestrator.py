"""Spawned workers preserve seed contracts and never publish partial means."""
from __future__ import annotations

import multiprocessing

import pandas as pd
import pytest

from utils.monte_carlo_input_fingerprint import fingerprint_simulation_inputs
from utils.monte_carlo_metric_packet import LEVELS
from utils.monte_carlo_orchestrator import execute_ensemble
from utils.monte_carlo_run_contract import build_run_plan


def _frozen_input():
    return {"snapshot": pd.DataFrame({"Person": ["A", "B"], "MAK": [0.5, 1.0]}),
            "atz": pd.DataFrame({"Person": ["A"], "Phase": ["AR"]}),
            "salary": {"enabled": False}, "cost": {"A": 100.0},
            "cluster": {"A": "Region"}}


def _fingerprint(payload):
    return fingerprint_simulation_inputs(
        payload["snapshot"], payload["atz"],
        salary_automation=payload["salary"], cost_lookup=payload["cost"],
        cluster_mapping=payload["cluster"])


def _worker_sensitive_fingerprint(payload):
    if multiprocessing.current_process().name != "MainProcess":
        return "changed-worker-input"
    return _fingerprint(payload)


def _packet(index, attrition, accession, payload):
    assert _fingerprint(payload)
    value = attrition["random_seed"] + accession["random_seed"]
    return {"version": 1, "target": {"heads": value, "mak": float(value),
                                      "eur": 0.0}, "annual_values": [],
            "stock_status": {level: "Nicht verfügbar" for level in LEVELS}}


def _failing_packet(index, attrition, accession, payload):
    if index == 1:
        raise RuntimeError("deliberate seed failure")
    return _packet(index, attrition, accession, payload)


def _invalid_packet(index, attrition, accession, payload):
    packet = _packet(index, attrition, accession, payload)
    if index == 1:
        packet["target"]["mak"] = float("nan")
    return packet


def _plan(payload, count):
    return build_run_plan(
        base_date="2025-12-31", target_date="2035-12-31",
        attrition_params={"random_seed": 7},
        accession_params={"random_seed": 42}, exclusions={},
        include_future_hires=False, input_fingerprint=_fingerprint(payload),
        cluster_source_signature="cluster-v1", count=count)


@pytest.mark.parametrize("count", [2, 10])
def test_spawned_runs_match_seedwise_serial_values(count):
    payload = _frozen_input()
    plan = _plan(payload, count)
    progress = []
    result = execute_ensemble(
        plan, frozen_input=payload, input_fingerprint=_fingerprint,
        run_seed=_packet, workers=3,
        on_progress=lambda completed, total: progress.append((completed, total)))
    expected = [(_packet(index, *plan.params_for_index(index), payload)["target"]["heads"])
                for index in range(count)]
    assert result["status"] == "complete"
    assert result["statistics"]["target"]["heads"]["mean"] == sum(expected) / count
    assert result["seed_pairs"] == plan.seed_pairs
    assert progress == [(index, count) for index in range(1, count + 1)]
    assert set(result["run_seconds"]) == set(range(count))


def test_worker_fingerprint_mismatch_blocks_entire_ensemble():
    payload = _frozen_input()
    result = execute_ensemble(
        _plan(payload, 2), frozen_input=payload,
        input_fingerprint=_worker_sensitive_fingerprint,
        run_seed=_packet, workers=2)
    assert result["status"] == "incomplete"
    assert result["statistics"] is None
    assert len(result["failures"]) == 2
    assert all("fingerprint" in item["error"] for item in result["failures"])


def test_seed_failure_and_changed_parent_input_do_not_publish_means():
    payload = _frozen_input()
    plan = _plan(payload, 2)
    result = execute_ensemble(
        plan, frozen_input=payload, input_fingerprint=_fingerprint,
        run_seed=_failing_packet, workers=2)
    assert result["status"] == "incomplete"
    assert result["statistics"] is None
    assert result["failures"] == [{"index": 1,
                                    "error": "RuntimeError: deliberate seed failure"}]
    payload["snapshot"].loc[0, "MAK"] = 0.8
    with pytest.raises(ValueError, match="Parent input fingerprint"):
        execute_ensemble(plan, frozen_input=payload, input_fingerprint=_fingerprint,
                         run_seed=_packet, workers=2)


def test_single_run_cannot_accidentally_use_parallel_path():
    payload = _frozen_input()
    with pytest.raises(ValueError, match="N=1"):
        execute_ensemble(_plan(payload, 1), frozen_input=payload,
                         input_fingerprint=_fingerprint, run_seed=_packet)


def test_invalid_worker_packet_never_publishes_partial_statistics():
    payload = _frozen_input()
    result = execute_ensemble(
        _plan(payload, 2), frozen_input=payload,
        input_fingerprint=_fingerprint, run_seed=_invalid_packet, workers=2)
    assert result["status"] == "incomplete"
    assert result["statistics"] is None
    assert result["failures"] == [{"seed": 8, "error": "Nonfinite target mak"}]
