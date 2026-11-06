"""Regular-only compact hire decisions match the fixed-plan product path."""
from __future__ import annotations

import pandas as pd
import pytest

from utils.fixed_plan_vacancies import FixedPlanVacancyPool, available_position_vacancies
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.hire_batch_probe import prepare_hire_batch, iter_hire_seed_records
from zugaenge.params import default_params
from zugaenge.fixed_plan_seed_stream import (
    _prepare_employee_after_departures,
    iter_fixed_plan_regular_coupled_seeds,
    rebuild_fixed_plan_regular_snapshot,
)


def test_productive_regular_batch_events_match_fixed_plan_forecast() -> None:
    from pathlib import Path
    from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _reset_state
    from dataloader import loader
    from dataloader.compact_simulation_engine import (
        _apply_attrition_events_to_employee_state, _build_fixed_plan_vacancy_context,
        _prepare_employee_forecast_base,
    )
    from abgaenge.forecast import run_forecast_abgaenge
    from abgaenge.params import default_params as departure_defaults

    _freeze_context()
    _reset_state()
    root = Path(__file__).resolve().parents[1]
    positions, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(root), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp("2026-12-31")
    people = _prepare_employee_forecast_base(positions, atz, start)
    departure_params = departure_defaults()
    departure_params["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    params = default_params()
    params["azubi"]["active"] = False
    params["trainee"]["active"] = False
    params["new_hires"].update(active=True, count_per_year=20,
                                strategy="Fill Vacancies")
    for seed in (7, 42):
        departures = run_forecast_abgaenge(
            people, atz, start, end, "M",
            {**departure_params, "random_seed": seed}, include_diagnostics=False)
        events = departures["events_person_level"]
        after_departures = _apply_attrition_events_to_employee_state(people, events)
        pool, original, vacancies = _build_fixed_plan_vacancy_context(
            positions, events, start)
        expected = run_forecast_zugaenge(
            after_departures, start, end_date=end,
            params={**params, "random_seed": seed},
            vacancy_pool=pool, original_positions=original)
        prepared = prepare_hire_batch(after_departures, start, end, params)
        _, compact_events, _ = next(iter_hire_seed_records(
            prepared, [seed], fixed_plan_vacancy_provider=lambda _: vacancies))
        pd.testing.assert_frame_equal(
            pd.DataFrame(compact_events), expected["events"], check_exact=True)
        assert len(compact_events) > 0


@pytest.mark.parametrize("strategy,annual_hires", [
    ("Fill Vacancies", 20), ("Random", 30), ("OrgUnit", 12),
])
@pytest.mark.parametrize("target_label", ["2026-12-31", "2027-12-31"])
def test_seed_specific_regular_coupling_matches_product(
        strategy: str, annual_hires: int, target_label: str) -> None:
    from copy import deepcopy
    from pathlib import Path
    from benchmark_page_rerun_paths import (
        FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module,
        _reset_state,
    )
    from dataloader import loader
    from dataloader.compact_simulation_engine import (
        CompactSimulationResult, _prepare_employee_forecast_base,
        _resolve_simulation_cluster_context,
        simulate_compact_snapshot,
    )
    from abgaenge.params import default_params as departure_defaults
    from zugaenge.enrichment import build_jf_to_cluster_map
    from components.jobfamily_simulation_yearly import (
        build_jobfamily_yearly_view, prepare_jobfamily_baseline,
    )
    from utils.org_simulation_events import build_simulation_event_summary
    from utils.status_quo_baseline import build_status_quo_snapshot
    from utils.compact_regular_annual import (
        build_compact_regular_annual_events, build_regular_year_end_stocks,
    )
    from utils.org_simulation_reconciliation import grouped_stock_metrics
    from components.jobfamily_simulation_yearly import _dimension_values

    _freeze_context()
    _reset_state()
    root = Path(__file__).resolve().parents[1]
    positions, _, _, _ = loader.load_and_prepare_data()
    compact = _load_page_module("*_Kompakt.py", "regular_batch_parity_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "regular_batch_parity_page")
    atz = loader.load_atz_data_cached(str(root), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(target_label)
    people = _prepare_employee_forecast_base(positions, atz, start)
    employee_template = _prepare_employee_after_departures(people)
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(positions, start))
    jf_baseline = prepare_jobfamily_baseline(baseline)
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), default_params()
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["azubi"]["active"] = False
    hire["trainee"]["active"] = False
    hire["new_hires"].update(active=True, count_per_year=annual_hires,
                              strategy=strategy,
                              target_org_unit=(str(people["Organisationseinheit"].dropna().iloc[0])
                                               if strategy == "OrgUnit" else None))
    hire["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(positions)
    for seed, departures, atz_pivot, hires, new_people in (
            iter_fixed_plan_regular_coupled_seeds(
                positions, people, atz, start, end, dep, hire, [7, 42])):
        reference = simulate_compact_snapshot(
            positions, atz, end, base_date=start,
            abgaenge_params={**dep, "random_seed": seed},
            zugaenge_params={**hire, "random_seed": seed},
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature,
            include_audits=False)
        pd.testing.assert_frame_equal(
            departures, reference.abgaenge_result["events_person_level"],
            check_exact=True)
        pd.testing.assert_frame_equal(
            atz_pivot, reference.abgaenge_result["tables"]["atz_pivot"],
            check_exact=True)
        rebuilt = rebuild_fixed_plan_regular_snapshot(
            positions, people, departures, atz_pivot, hires, new_people,
            end, hire, active_cluster_source=source,
            cluster_mapping_bundle=bundle,
            cluster_source_signature=signature,
            prepared_employee_template=employee_template)
        enriched = rebuilt.accession_events
        pd.testing.assert_frame_equal(
            enriched, reference.zugaenge_result["events"], check_exact=True)
        replay = rebuilt.raw_employee_state
        pd.testing.assert_frame_equal(
            replay, reference.zugaenge_result["final_state"], check_exact=True)
        final_people = rebuilt.future_employee
        pd.testing.assert_frame_equal(
            final_people, reference.future_employee_df, check_exact=True)
        future = rebuilt.future_snapshot
        pd.testing.assert_frame_equal(
            future, reference.future_snapshot_df, check_exact=True)
        pd.testing.assert_series_equal(future["Soll_FTE"], positions["Soll_FTE"])
        compact_result = CompactSimulationResult(
            future_snapshot_df=future, future_employee_df=final_people,
            abgaenge_result={"events_person_level": departures,
                             "tables": {"atz_pivot": atz_pivot}},
            zugaenge_result={"events": enriched, "final_state": replay},
            metadata={"base_date": start, "target_date": end,
                      "cluster_source_signature": signature},
            audit_tables={})
        annual = []
        for result in (reference, compact_result):
            context = page._build_simulation_event_context(
                result, bundle, f"regular-batch::{strategy}::{seed}",
                baseline_snapshot=baseline,
                final_snapshot=compact.prepare_compact_data(result.future_snapshot_df),
                zugaenge_params=hire, abgaenge_params=dep, df_atz=atz,
                run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
                source_snapshot=positions)
            summary = build_simulation_event_summary(
                context, expected_run_signature=f"regular-batch::{strategy}::{seed}")
            annual.append((summary, {
                dimension: build_jobfamily_yearly_view(
                    context, summary, dimension=dimension)
                for dimension in ("Jobfamily", "JF-Cluster")
            }))
        (expected_summary, expected_jf), (actual_summary, actual_jf) = annual
        compact_years = build_compact_regular_annual_events(
            baseline, positions, departures, hires, hire, dep,
            base_date=start, target_date=end,
            cluster_mapping_bundle=bundle,
            cluster_source_signature=signature,
            run_signature=f"regular-event-only::{strategy}::{seed}",
            prepared_jobfamily_baseline=jf_baseline)
        year_stocks = build_regular_year_end_stocks(
            positions, people, departures, atz_pivot, hires, new_people, hire,
            base_date=start, target_date=end,
            prepare_snapshot=compact.prepare_compact_data,
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature,
            prepared_employee_template=employee_template)
        for year_end in sorted(year_stocks["oe"]["Stichtag"].unique()):
            year_end = pd.Timestamp(year_end)
            year_reference = (reference if year_end == end else simulate_compact_snapshot(
                positions, atz, year_end, base_date=start,
                abgaenge_params={**dep, "random_seed": seed},
                zugaenge_params={**hire, "random_seed": seed},
                active_cluster_source=source, cluster_mapping_bundle=bundle,
                cluster_source_signature=signature, include_audits=False))
            year_frame = compact.prepare_compact_data(year_reference.future_snapshot_df)
            for key, dimension in (("oe", "Organisationseinheit"),
                                   ("oe_cluster", "OE-Cluster"),
                                   ("jf", "Jobfamily"),
                                   ("jf_cluster", "JF-Cluster")):
                work = year_frame if key in ("oe", "oe_cluster") else year_frame.assign(
                    **{dimension: _dimension_values(year_frame, dimension)})
                expected = grouped_stock_metrics(work, dimension)
                observed = year_stocks[key].loc[year_stocks[key]["Stichtag"].eq(year_end)]
                actual = {row[dimension]: (row["Köpfe"], row["MAK"])
                          for _, row in observed.iterrows()}
                assert actual == expected
        for key, expected in (
            ("oe_annual", expected_summary["annual_oe"]),
            ("oe_cluster_annual", expected_summary["annual_cluster"]),
            ("jf_annual", expected_jf["Jobfamily"]["annual"]),
            ("jf_cluster_annual", expected_jf["JF-Cluster"]["annual"]),
        ):
            pd.testing.assert_frame_equal(compact_years[key], expected,
                                          check_exact=True)
        for key in ("events", "annual_oe", "annual_cluster"):
            pd.testing.assert_frame_equal(
                actual_summary[key], expected_summary[key], check_exact=True)
        for key in ("annual", "checks"):
            pd.testing.assert_frame_equal(
                actual_summary["stock_reconciliation"][key],
                expected_summary["stock_reconciliation"][key], check_exact=True)
        for dimension in ("Jobfamily", "JF-Cluster"):
            for key in ("annual", "stock", "stock_checks"):
                pd.testing.assert_frame_equal(
                    actual_jf[dimension][key], expected_jf[dimension][key],
                    check_exact=True)


@pytest.mark.parametrize("annual_hires", [12, 48])
def test_fixed_plan_regular_batch_events_match_product(annual_hires: int) -> None:
    start, end = pd.Timestamp("2025-12-31"), pd.Timestamp("2027-12-31")
    positions = pd.DataFrame([
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 0.6,
         "Kürzel OrgEinheit": "1000", "Organisationseinheit": "A",
         "Planstelle": "Beratung", "Planstellennr": "A-1"},
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 0.8,
         "Kürzel OrgEinheit": "1001", "Organisationseinheit": "B",
         "Planstelle": "Markt", "Planstellennr": "B-1"},
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 1.0,
         "Kürzel OrgEinheit": "9910", "Organisationseinheit": "Ausbildung",
         "Planstelle": "Bankkaufleute", "Planstellennr": "AZ-1"},
    ])
    people = pd.DataFrame([{
        "PersNr": "100", "active": True, "Organisationseinheit": "A",
        "OE-Cluster": "C-A", "Jobfamily": "Angestellte",
        "Ausbildung": "Bachelor FH", "Geschlecht": "m",
        "Eintritt": pd.Timestamp("2020-01-01"), "mak": 1.0,
    }])
    params = default_params()
    params["azubi"]["active"] = False
    params["trainee"]["active"] = False
    params["new_hires"].update(active=True, count_per_year=annual_hires,
                                strategy="Fill Vacancies")
    vacancies = available_position_vacancies(
        positions, pd.DataFrame(), start, include_report_excluded=True)
    prepared = prepare_hire_batch(people, start, end, params)
    stream = iter_hire_seed_records(
        prepared, [7, 42], fixed_plan_vacancy_provider=lambda seed: vacancies)
    for seed, compact_events, new_people in stream:
        expected = run_forecast_zugaenge(
            people, start, end_date=end,
            params={**params, "random_seed": seed},
            vacancy_pool=FixedPlanVacancyPool(vacancies))
        pd.testing.assert_frame_equal(
            pd.DataFrame(compact_events), expected["events"], check_exact=True)
        assert len(new_people) == len(compact_events) == 2
        assert sorted(event["mak"] for event in compact_events) == [0.6, 0.8]
