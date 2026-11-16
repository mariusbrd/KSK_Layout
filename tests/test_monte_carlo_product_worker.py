"""Product packets agree between serial execution and spawned processes."""
from __future__ import annotations

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
    run_product_seed, run_product_seed_with_ist_charts,
)


@pytest.mark.parametrize("target_date", ["2026-12-31", "2027-12-31"])
def test_app_packet_carries_certified_departure_stock_separately(
        frozen_product_context, target_date):
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), hire_defaults()
    payload = freeze_product_worker_input(
        snapshot=snapshot, atz=atz, base_date=FROZEN_STICHTAG,
        target_date=pd.Timestamp(target_date),
        salary_automation={}, exclusions=frozen_product_context,
        session_values={
            key: deepcopy(st.session_state[key]) for key in (
                "cohort_definitions", "azubi_salaries", "vorstand_jahresgehalt",
                "employer_cost_factor", "tvoed_lookup") if key in st.session_state},
        active_cluster_source=source, cluster_mapping_bundle=bundle,
        cluster_source_signature=signature)
    packet = run_product_seed_with_ist_charts(0, dep, hire, payload)
    from utils.monte_carlo_annual_event_rows import decode_annual_event_rows
    from utils.monte_carlo_annual_event_rows import VERSION as ANNUAL_EVENT_ROWS_VERSION
    assert packet["annual_event_rows_version"] == ANNUAL_EVENT_ROWS_VERSION
    annual_events = decode_annual_event_rows(packet["annual_event_rows"])
    assert set(annual_events["Ereignisquelle"]).issubset(
        {"departure_events", "accession_events"})
    from utils.monte_carlo_annual_positions import decode_annual_positions
    annual_positions = decode_annual_positions(packet["annual_positions"])
    assert annual_positions
    assert [frame["Stichtag"].iloc[0] for frame in annual_positions] == sorted(
        frame["Stichtag"].iloc[0] for frame in annual_positions)
    assert annual_events[["Alterskohorte", "Geschlecht", "Arbeitszeit",
                          "Ausbildung", "ATZ_Status"]].notna().all().all(), (
        annual_events[["Ereignisart", "Alterskohorte", "Geschlecht", "Arbeitszeit",
                       "Ausbildung", "ATZ_Status"]].isna().sum().to_dict())
    from utils.monte_carlo_filtered_annual_events import filtered_annual_event_view
    from utils.org_simulation_events import aggregate_simulation_events
    sample_lookup = {}
    for year, level, group, category, kind, metric, value in packet["annual_values"]:
        if level in {"Organisationseinheit", "OE-Cluster"}:
            key = (year, level, group, kind, metric)
            sample_lookup[key] = sample_lookup.get(key, 0.0) + float(value)
    ensemble = {
        "count": 2, "annual_event_rows_version": ANNUAL_EVENT_ROWS_VERSION,
        "annual_event_rows": [packet["annual_event_rows"]] * 2,
        "statistics": {"annual_event_samples": [(*key, [value, value])
                                                 for key, value in sample_lookup.items()]},
    }
    summary = {"events": annual_events,
               "annual_oe": aggregate_simulation_events(annual_events),
               "annual_cluster": aggregate_simulation_events(
                   annual_events, dimension="OE-Cluster")}
    filtered, adjusted = filtered_annual_event_view(
        summary, ensemble, {"selected_genders": ["w"]})
    assert filtered["events"]["Geschlecht"].eq("w").all()
    assert all(values[0] == values[1] for *_, values in
               adjusted["statistics"]["annual_event_samples"])
    from utils.monte_carlo_filtered_annual_positions import filtered_annual_position_samples
    stock_samples = [(*record[:4], [record[4], record[4]])
                     for record in packet["annual_stock_values"]]
    stock_ensemble = {
        "count": 2, "band_level": .8, "annual_positions_version": 1,
        "annual_positions": [packet["annual_positions"]] * 2,
        "statistics": {"annual_stock_samples": stock_samples},
    }
    filtered_stock = filtered_annual_position_samples(
        stock_ensemble, {"selected_genders": ["w"]})
    assert filtered_stock
    assert all(values[0] == values[1] for *_, values, stats in filtered_stock)
    assert packet["departure_stock_values"]
    assert any(row[1] == "Organisationseinheit" and row[3] == "mak"
               for row in packet["departure_stock_values"])
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


@pytest.mark.parametrize("count,target_year", [(2, 2026), (10, 2028)])
def test_product_packets_match_spawned_ensemble_for_all_annual_levels(
        frozen_product_context, count, target_year):
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
