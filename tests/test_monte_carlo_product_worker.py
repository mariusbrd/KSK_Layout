"""Product packets agree between serial execution and spawned processes."""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
import kpi_reference
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from abgaenge.params import default_params as departure_defaults
from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _reset_state,
)
from dataloader import loader
import dataloader.compact_simulation_engine as engine
from dataloader.compact_simulation_engine import _resolve_simulation_cluster_context
from components.jobfamily_simulation_yearly import _dimension_values
from utils.org_simulation_reconciliation import grouped_stock_metrics
from utils.monte_carlo_ensemble import aggregate_packets
from utils.monte_carlo_orchestrator import execute_ensemble
from utils.monte_carlo_product_worker import (
    _compact_prepare, freeze_product_worker_input, product_input_fingerprint,
    run_product_seed,
)
from utils.monte_carlo_run_contract import build_run_plan
from zugaenge.params import default_params as hire_defaults


@pytest.fixture
def frozen_product_context():
    originals = (
        loader.get_setting, kpi_reference.get_setting,
        loader.get_current_stichtag, kpi_reference.get_current_stichtag,
        np.random.normal, deepcopy(FROZEN_SETTINGS),
    )
    _freeze_context()
    _reset_state()
    exclusions = deepcopy(FROZEN_SETTINGS["exclusions"])
    exclusions.update(vorstand=False, ruhend_bv=False, org_units=[])
    FROZEN_SETTINGS["exclusions"] = exclusions
    try:
        yield exclusions
    finally:
        (loader.get_setting, kpi_reference.get_setting,
         loader.get_current_stichtag, kpi_reference.get_current_stichtag,
         np.random.normal) = originals[:5]
        FROZEN_SETTINGS.clear()
        FROZEN_SETTINGS.update(originals[5])


@pytest.mark.parametrize("count,target_year,reference_hashes", [
    (2, 2026, (
        "c7dad96da49d3f1c53c7d2038f4028497264a3efb15f294b2bb70dc1486eb3b8",
        "6e603babbac1ca3892914def4b74bf1f9f20b290610b162f2f743d6a4039fd88",
    )),
    (10, 2028, (
        "c5a4b405f81dd3e48c906a57161225dc89307c70505fb6fced1e97a0e580c6f2",
        "c2a40adef4b76fad94f059380667b57038fe08bc3824a2ab646377098e68b13e",
        "950452fa1946275eb6e6f34db509f3339c3401085b338d4450bca5acf418efbb",
        "3766e1a3ed70f168a61bf53fb43a6b092d7ecda91aa537384137af2d3edf91fa",
        "4d94692380cfe9ea6dfc364d58b881dfdee1c8349fd58c5036b7a339be9ce93a",
        "eda26f5d477869dc93e7d64b839c1c9eaf526027bea0b1b7370c32bd56375085",
        "19a10f8b35cab12bb10049e0961a5f3f66dd0921e1b696179d1d15e926f5233e",
        "2ad61e033a330192dac949e84a37c59a60319d837ef0395f4fcb3d9ade8424e4",
        "40b6a6176d5784f07b50588e507af2d3110b3f707878d24d97d4ee16a4826270",
        "471bf86a96a61b9465d8e4e5c6bdd62021aff1c3549ca69e45ecc07814598481",
    )),
])
def test_product_packets_match_spawned_ensemble_for_all_annual_levels(
        frozen_product_context, count, target_year, reference_hashes):
    exclusions = frozen_product_context
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=True, count_per_year=20, strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0)
    hire["trainee"].update(active=True, new_cases_per_year=4)
    dep["random_seed"] = hire["random_seed"] = 0
    payload = freeze_product_worker_input(
        snapshot=snapshot, atz=atz,
        base_date=FROZEN_STICHTAG, target_date=pd.Timestamp(target_year, 12, 31),
        salary_automation={}, exclusions=exclusions,
        session_values={
            key: deepcopy(st.session_state[key]) for key in (
                "cohort_definitions", "azubi_salaries", "vorstand_jahresgehalt",
                "employer_cost_factor", "tvoed_lookup") if key in st.session_state},
        active_cluster_source=source, cluster_mapping_bundle=bundle,
        cluster_source_signature=signature)
    plan = build_run_plan(
        base_date=FROZEN_STICHTAG, target_date=payload["target_date"],
        attrition_params=dep, accession_params=hire, exclusions=exclusions,
        include_future_hires=True,
        input_fingerprint=product_input_fingerprint(payload),
        cluster_source_signature=signature, count=count)
    serial = [run_product_seed(i, *plan.params_for_index(i), payload)
              for i in range(count)]
    packet_hashes = [hashlib.sha256(json.dumps(
        packet, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
        for packet in serial]
    assert packet_hashes == list(reference_hashes)
    expected = aggregate_packets(base_seed=0, count=count, runs=[
        {"seed": i, "status": "ok", "packet": packet}
        for i, packet in enumerate(serial)])
    actual = execute_ensemble(
        plan, frozen_input=payload, input_fingerprint=product_input_fingerprint,
        run_seed=run_product_seed, workers=min(count, 3))
    assert actual["status"] == "complete", actual["failures"]
    assert actual["statistics"] == expected
    assert {row[1] for row in serial[0]["annual_stock_values"]} == {
        "Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"}


def test_partial_year_stock_matches_product_target_for_all_levels(frozen_product_context):
    exclusions = frozen_product_context
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=True, count_per_year=20, strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0)
    hire["trainee"].update(active=True, new_cases_per_year=4)
    dep["random_seed"] = hire["random_seed"] = 0
    target = pd.Timestamp(2027, 6, 15)
    payload = freeze_product_worker_input(
        snapshot=snapshot, atz=atz, base_date=FROZEN_STICHTAG,
        target_date=target, salary_automation={}, exclusions=exclusions,
        active_cluster_source=source, cluster_mapping_bundle=bundle,
        cluster_source_signature=signature)
    packet = run_product_seed(0, deepcopy(dep), deepcopy(hire), payload)
    assert {row[0] for row in packet["annual_stock_values"]} == {
        "2026-12-31", "2027-06-15"}
    original_setting = engine.get_setting
    try:
        engine.get_setting = lambda key, default=None: deepcopy(
            payload["settings"].get(key, default))
        product = engine.simulate_compact_snapshot(
            snapshot, atz, target, base_date=FROZEN_STICHTAG,
            abgaenge_params=deepcopy(dep), zugaenge_params=deepcopy(hire),
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature, include_audits=False)
    finally:
        engine.get_setting = original_setting
    compact = _compact_prepare()(product.future_snapshot_df)
    for level in ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"):
        frame = (compact if level in ("Organisationseinheit", "OE-Cluster")
                 else compact.assign(**{level: _dimension_values(compact, level)}))
        expected = grouped_stock_metrics(frame, level)
        values = {row[2]: {} for row in packet["annual_stock_values"]
                  if row[0] == "2027-06-15" and row[1] == level}
        for date, dimension, group, metric, value in packet["annual_stock_values"]:
            if date == "2027-06-15" and dimension == level:
                values[group][metric] = value
        actual = {group: (metrics["heads"], metrics["mak"])
                  for group, metrics in values.items()}
        assert actual == expected, level
    second = run_product_seed(1, {**deepcopy(dep), "random_seed": 1},
                              {**deepcopy(hire), "random_seed": 1}, payload)
    plan = build_run_plan(
        base_date=FROZEN_STICHTAG, target_date=target,
        attrition_params=dep, accession_params=hire, exclusions=exclusions,
        include_future_hires=True,
        input_fingerprint=product_input_fingerprint(payload),
        cluster_source_signature=signature, count=2)
    expected_ensemble = aggregate_packets(base_seed=0, count=2, runs=[
        {"seed": seed, "status": "ok", "packet": result}
        for seed, result in enumerate((packet, second))])
    actual_ensemble = execute_ensemble(
        plan, frozen_input=payload, input_fingerprint=product_input_fingerprint,
        run_seed=run_product_seed, workers=2)
    assert actual_ensemble["status"] == "complete", actual_ensemble["failures"]
    assert actual_ensemble["statistics"] == expected_ensemble
