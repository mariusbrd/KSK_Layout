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
from dataloader.compact_simulation_engine import _resolve_simulation_cluster_context
from utils.monte_carlo_ensemble import aggregate_packets
from utils.monte_carlo_orchestrator import execute_ensemble
from utils.monte_carlo_product_worker import (
    freeze_product_worker_input, product_input_fingerprint, run_product_seed,
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


def test_product_packets_match_spawned_ensemble_for_all_annual_levels(frozen_product_context):
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
        base_date=FROZEN_STICHTAG, target_date=pd.Timestamp(2026, 12, 31),
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
        cluster_source_signature=signature, count=2)
    serial = [run_product_seed(i, *plan.params_for_index(i), payload)
              for i in range(2)]
    packet_hashes = [hashlib.sha256(json.dumps(
        packet, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
        for packet in serial]
    assert packet_hashes == [
        "c7dad96da49d3f1c53c7d2038f4028497264a3efb15f294b2bb70dc1486eb3b8",
        "6e603babbac1ca3892914def4b74bf1f9f20b290610b162f2f743d6a4039fd88",
    ]
    expected = aggregate_packets(base_seed=0, count=2, runs=[
        {"seed": i, "status": "ok", "packet": packet}
        for i, packet in enumerate(serial)])
    actual = execute_ensemble(
        plan, frozen_input=payload, input_fingerprint=product_input_fingerprint,
        run_seed=run_product_seed, workers=2)
    assert actual["status"] == "complete", actual["failures"]
    assert actual["statistics"] == expected
    assert {row[1] for row in serial[0]["annual_stock_values"]} == {
        "Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"}
