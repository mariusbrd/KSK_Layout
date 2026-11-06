"""Product snapshot parity for the coupled fixed-plan seed stream."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module,
    _reset_state,
)
from dataloader import loader
from dataloader.compact_simulation_engine import (
    _apply_salary_automation_to_employee_state, _fill_reserved_plan_positions,
    _finalize_future_snapshot, _normalize_persnr_series,
    _prepare_employee_forecast_base, _resolve_atz_status_map,
    _resolve_simulation_cluster_context, _update_existing_rows,
    simulate_compact_snapshot,
)
from abgaenge.params import default_params as departure_defaults
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events
from zugaenge.fixed_plan_seed_stream import iter_fixed_plan_coupled_seeds
from zugaenge.params import default_params as hire_defaults
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.org_simulation_events import build_simulation_event_summary
from utils.status_quo_baseline import build_status_quo_snapshot


@pytest.mark.parametrize("target_label,annual_hires", [
    ("2026-12-31", 20), ("2030-12-31", 0),
])
def test_coupled_seeds_rebuild_product_positions_and_person_state(
        target_label: str, annual_hires: int) -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    compact = _load_page_module("*_Kompakt.py", "fixed_plan_parity_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "fixed_plan_parity_page")
    atz = loader.load_atz_data_cached(
        str(Path(__file__).resolve().parents[1]), None, None, None)
    start, target = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(target_label)
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(snapshot, start))
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=annual_hires > 0, count_per_year=annual_hires,
                              strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4,
                         retention_rate=1.0)
    hire["trainee"].update(active=True, new_cases_per_year=4)
    hire_for_stream = deepcopy(hire)
    hire_for_stream["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(snapshot)

    for seed, departure_events, atz_pivot, accession in iter_fixed_plan_coupled_seeds(
            snapshot, people, atz, start, target, dep, hire_for_stream, [7, 42]):
        reference = simulate_compact_snapshot(
            snapshot, atz, target, base_date=start,
            abgaenge_params={**deepcopy(dep), "random_seed": seed},
            zugaenge_params={**deepcopy(hire), "random_seed": seed},
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature, include_audits=False)
        pd.testing.assert_frame_equal(
            departure_events, reference.abgaenge_result["events_person_level"],
            check_exact=True)
        pd.testing.assert_frame_equal(
            atz_pivot, reference.abgaenge_result["tables"]["atz_pivot"],
            check_exact=True)
        pd.testing.assert_frame_equal(
            accession["final_state"], reference.zugaenge_result["final_state"],
            check_exact=True)
        enriched = enrich_zugaenge_events(
            accession["events"], snapshot, hire_for_stream,
            cluster_mapping_bundle=bundle,
            active_cluster_source_signature=signature)
        pd.testing.assert_frame_equal(
            enriched, reference.zugaenge_result["events"], check_exact=True)
        if target.year == 2030:
            assert enriched["type"].eq("Azubi_Conversion_In").any()

        final_people = accession["final_state"].copy()
        final_people["PersNr"] = _normalize_persnr_series(final_people["PersNr"])
        final_people = _apply_salary_automation_to_employee_state(
            final_people, target_date=target, events_df=enriched)
        pd.testing.assert_frame_equal(
            final_people, reference.future_employee_df, check_exact=True)
        future = _update_existing_rows(
            snapshot, final_people, target, _resolve_atz_status_map(atz_pivot, target))
        future = _fill_reserved_plan_positions(future, snapshot, final_people)
        future = _finalize_future_snapshot(
            future, target, active_cluster_source=source,
            cluster_mapping_bundle=bundle)
        pd.testing.assert_frame_equal(
            future, reference.future_snapshot_df, check_exact=True)
        assert len(future) == len(snapshot)
        pd.testing.assert_series_equal(future["Soll_FTE"], snapshot["Soll_FTE"])

        streamed_result = replace(
            reference, future_snapshot_df=future,
            future_employee_df=final_people,
            zugaenge_result={**reference.zugaenge_result,
                             "events": enriched, "final_state": accession["final_state"]})
        annual_outputs = []
        for result in (reference, streamed_result):
            context = page._build_simulation_event_context(
                result, bundle, f"fixed-plan-parity::{seed}",
                baseline_snapshot=baseline,
                final_snapshot=compact.prepare_compact_data(result.future_snapshot_df),
                zugaenge_params=hire, abgaenge_params=dep, df_atz=atz,
                run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
                source_snapshot=snapshot)
            summary = build_simulation_event_summary(
                context, expected_run_signature=f"fixed-plan-parity::{seed}")
            annual_outputs.append((summary, {
                dimension: build_jobfamily_yearly_view(
                    context, summary, dimension=dimension)
                for dimension in ("Jobfamily", "JF-Cluster")
            }))
        (expected_summary, expected_jf), (actual_summary, actual_jf) = annual_outputs
        for key in ("events", "annual_oe", "annual_cluster"):
            pd.testing.assert_frame_equal(actual_summary[key], expected_summary[key],
                                          check_exact=True)
        for key in ("annual", "checks"):
            pd.testing.assert_frame_equal(
                actual_summary["stock_reconciliation"][key],
                expected_summary["stock_reconciliation"][key], check_exact=True)
        for dimension in ("Jobfamily", "JF-Cluster"):
            for key in ("annual", "stock", "stock_checks"):
                pd.testing.assert_frame_equal(
                    actual_jf[dimension][key], expected_jf[dimension][key],
                    check_exact=True)
