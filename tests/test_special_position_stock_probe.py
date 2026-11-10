"""Probe the isolated special-position year stocks against product snapshots."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module,
    _reset_state,
)
import dataloader.compact_simulation_engine as simulation_engine
from dataloader import loader
from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state,
    _apply_salary_automation_to_employee_state, _fill_reserved_plan_positions,
    _finalize_future_snapshot, _normalize_persnr_series,
    _prepare_employee_forecast_base, _resolve_atz_status_map,
    _resolve_simulation_cluster_context, _update_existing_rows,
    simulate_compact_snapshot,
)
from abgaenge.params import default_params as departure_defaults
from zugaenge.enrichment import build_jf_to_cluster_map
from zugaenge.fixed_plan_seed_stream import iter_fixed_plan_coupled_seeds
from zugaenge.params import default_params as hire_defaults
from utils.compact_regular_stock import project_regular_position_stocks
from utils.compact_special_position_events import special_position_transitions
from utils.org_simulation_reconciliation import grouped_stock_metrics
from utils.status_quo_baseline import build_status_quo_snapshot
from components.jobfamily_simulation_yearly import _dimension_values


@pytest.mark.parametrize("end_label,seed,strategy,exclusion_mode", [
    ("2026-12-31", 7, "Fill Vacancies", "default"),
    ("2030-12-31", 7, "Fill Vacancies", "default"),
    ("2026-12-31", 42, "Fill Vacancies", "default"),
    ("2030-12-31", 42, "Fill Vacancies", "default"),
    ("2026-06-30", 77, "Fill Vacancies", "default"),
    ("2027-12-31", 123, "Random", "default"),
    ("2027-12-31", 999, "OrgUnit", "default"),
    ("2027-12-31", 42, "Fill Vacancies", "open"),
])
def test_special_position_target_stock_matches_product(
        end_label: str, seed: int, strategy: str, exclusion_mode: str,
        monkeypatch: pytest.MonkeyPatch) -> None:
    if exclusion_mode == "open":
        exclusions = deepcopy(FROZEN_SETTINGS["exclusions"])
        exclusions.update(vorstand=False, ruhend_bv=False, org_units=[])
        monkeypatch.setitem(FROZEN_SETTINGS, "exclusions", exclusions)
        monkeypatch.setattr(simulation_engine, "get_setting",
                            lambda key, default=None: deepcopy(FROZEN_SETTINGS.get(key, default)))
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(Path(__file__).resolve().parents[1]), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(end_label)
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    compact = _load_page_module("*_Kompakt.py", "special_stock_probe_compact")
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(snapshot, start))
    source, bundle, signature = _resolve_simulation_cluster_context()
    mapped = apply_clusters_to_snapshot_from_source(baseline, source, mapping_bundle=bundle)
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(
        active=True, count_per_year=20, strategy=strategy,
        target_org_unit=(str(people["Organisationseinheit"].dropna().iloc[0])
                         if strategy == "OrgUnit" else None))
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0)
    hire["trainee"].update(active=True, new_cases_per_year=4)
    stream_hire = deepcopy(hire)
    stream_hire["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(snapshot)
    _, departures, _, accessions = next(iter_fixed_plan_coupled_seeds(
        snapshot, people, atz, start, end, dep, stream_hire, [seed]))
    transitions = special_position_transitions(snapshot, accessions["events"])
    if exclusion_mode == "open":
        observed = {}
        def capture_year(date: pd.Timestamp, state: pd.DataFrame,
                         events: pd.DataFrame) -> None:
            observed[date] = (state, events)
        short_horizon = simulate_compact_snapshot(
            snapshot.copy(deep=True), atz, pd.Timestamp("2026-12-31"), base_date=start,
            abgaenge_params={**deepcopy(dep), "random_seed": seed},
            zugaenge_params={**deepcopy(hire), "random_seed": seed},
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature, include_audits=False)
        same_horizon = simulate_compact_snapshot(
            snapshot.copy(deep=True), atz, end, base_date=start,
            abgaenge_params={**deepcopy(dep), "random_seed": seed},
            zugaenge_params={**deepcopy(hire), "random_seed": seed},
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature, include_audits=False,
            year_end_observer=capture_year)
        assert departures.reset_index(drop=True).equals(
            same_horizon.abgaenge_result["events_person_level"].reset_index(drop=True)), \
            "departure stream differs from full product under open exclusions"
        keys = ["date", "type", "persnr", "position_index"]
        prefix = accessions["events"][keys]
        product_prefix = same_horizon.zugaenge_result["events"][keys]
        assert prefix.reset_index(drop=True).equals(product_prefix.reset_index(drop=True)), (
            prefix[keys].head(5).to_dict("records"),
            product_prefix[keys].head(5).to_dict("records"))
        earlier = departures.loc[pd.to_datetime(departures["event_date"]).dt.normalize().le(
            pd.Timestamp("2026-12-31"))].reset_index(drop=True)
        earlier_product = same_horizon.abgaenge_result["events_person_level"].loc[
            pd.to_datetime(same_horizon.abgaenge_result["events_person_level"][
                "event_date"]).dt.normalize().le(pd.Timestamp("2026-12-31"))].reset_index(drop=True)
        pd.testing.assert_frame_equal(earlier, earlier_product, check_exact=True)
        _, short_stream_departures, _, short_stream_accessions = next(iter_fixed_plan_coupled_seeds(
            snapshot, people, atz, start, pd.Timestamp("2026-12-31"),
            deepcopy(dep), deepcopy(stream_hire), [seed]))
        pd.testing.assert_frame_equal(
            short_stream_departures.reset_index(drop=True),
            short_horizon.abgaenge_result["events_person_level"].reset_index(drop=True),
            check_exact=True)
        pd.testing.assert_frame_equal(
            earlier, short_stream_departures.reset_index(drop=True), check_exact=True)
        pd.testing.assert_frame_equal(
            earlier, short_horizon.abgaenge_result[
                "events_person_level"].reset_index(drop=True), check_exact=True)
        long_accessions = accessions["events"].loc[
            pd.to_datetime(accessions["events"]["date"]).dt.normalize().le(
                pd.Timestamp("2026-12-31")), keys].reset_index(drop=True)
        short_accessions = short_stream_accessions["events"][keys].reset_index(drop=True)
        assert not long_accessions.equals(short_accessions)
        long_converted = set(long_accessions.loc[
            long_accessions["type"].eq("Azubi_Conversion_In"), "persnr"])
        short_converted = set(short_accessions.loc[
            short_accessions["type"].eq("Azubi_Conversion_In"), "persnr"])
        missing_conversions = short_converted - long_converted
        future_departures = set(departures.loc[
            pd.to_datetime(departures["event_date"]).dt.normalize().gt(
                pd.Timestamp("2026-12-31"))
            & departures["headcount_change"].lt(0), "persnr"])
        assert len(missing_conversions) == 1
        assert missing_conversions <= future_departures
    compact_positions = {}
    projected = project_regular_position_stocks(
        baseline, mapped, departures, [], base_date=start, target_date=end,
        special_transitions=transitions,
        position_observer=lambda date, frame: compact_positions.setdefault(date, frame),
        exclude_ruhend=bool(FROZEN_SETTINGS["exclusions"]["ruhend_bv"]))
    if exclusion_mode == "open":
        pd.testing.assert_frame_equal(
            observed[end][0][same_horizon.zugaenge_result["final_state"].columns],
            same_horizon.zugaenge_result["final_state"],
            check_exact=True)
        annual_date = pd.Timestamp("2026-12-31")
        annual_state, _ = observed[annual_date]
        annual_events = same_horizon.zugaenge_result["events"].loc[
            pd.to_datetime(same_horizon.zugaenge_result["events"]["date"])
            .dt.normalize().le(annual_date)].copy()
        annual_people = annual_state.copy()
        annual_people["PersNr"] = _normalize_persnr_series(annual_people["PersNr"])
        annual_people = _apply_salary_automation_to_employee_state(
            annual_people, target_date=annual_date, events_df=annual_events)
        annual_snapshot = _update_existing_rows(
            snapshot, annual_people, annual_date,
            _resolve_atz_status_map(
                same_horizon.abgaenge_result["tables"]["atz_pivot"], annual_date))
        annual_snapshot = _fill_reserved_plan_positions(
            annual_snapshot, snapshot, annual_people)
        annual_snapshot = _finalize_future_snapshot(
            annual_snapshot, annual_date, active_cluster_source=source,
            cluster_mapping_bundle=bundle)
        annual_frame = compact.prepare_compact_data(annual_snapshot)
        preapplied_people = _apply_attrition_events_to_employee_state(people, departures)
        preapplied_snapshot = _update_existing_rows(
            snapshot, preapplied_people, annual_date,
            _resolve_atz_status_map(
                same_horizon.abgaenge_result["tables"]["atz_pivot"], annual_date))
        preapplied_snapshot = _finalize_future_snapshot(
            preapplied_snapshot, annual_date, active_cluster_source=source,
            cluster_mapping_bundle=bundle, stock_only=True)
        preapplied_baseline = compact.prepare_compact_data(preapplied_snapshot)
        preapplied_mapped = apply_clusters_to_snapshot_from_source(
            preapplied_baseline, source, mapping_bundle=bundle)
        preapplied_projection = project_regular_position_stocks(
            preapplied_baseline, preapplied_mapped, pd.DataFrame(), [],
            base_date=start, target_date=annual_date,
            special_transitions=transitions, exclude_ruhend=False)
        for dimension, group_frame in preapplied_projection.items():
            product_group_frame = (annual_frame if dimension in
                                   ("Organisationseinheit", "OE-Cluster") else
                                   annual_frame.assign(**{
                                       dimension: _dimension_values(annual_frame, dimension)}))
            actual = {row[dimension]: (row["Köpfe"], row["MAK"])
                      for _, row in group_frame.iterrows()}
            expected = grouped_stock_metrics(product_group_frame, dimension)
            assert actual == expected, ("preapplied", dimension, {
                key: (actual.get(key), expected.get(key))
                for key in actual.keys() | expected.keys()
                if actual.get(key) != expected.get(key)})
        compact_rows = compact_positions[annual_date]
        product_rows = annual_frame.reset_index(drop=True)
        compact_occupied = compact_rows["PersNr"].notna() & ~compact_rows["Is_Vacant"].fillna(True)
        product_occupied = product_rows["PersNr"].notna() & ~product_rows["Is_Vacant"].fillna(True)
        compact_mak = pd.to_numeric(compact_rows["MAK_Reporting"], errors="coerce").fillna(0.0)
        product_mak = pd.to_numeric(product_rows["MAK_Reporting"], errors="coerce").fillna(0.0)
        position_diff = compact_occupied.ne(product_occupied) | compact_mak.ne(product_mak)
        assert int(position_diff.sum()) == 176
        assert int((~compact_occupied & product_occupied).sum()) == 0
        future_exit_people = set(departures.loc[
            pd.to_datetime(departures["event_date"]).dt.normalize().gt(annual_date)
            & departures["headcount_change"].lt(0), "persnr"].astype(str))
        compact_only = compact_occupied & ~product_occupied
        compact_only_people = set(compact_rows.loc[compact_only, "PersNr"].astype(str))
        assert int(compact_only.sum()) == 140
        assert len(compact_only_people) == 138
        assert compact_only_people <= future_exit_people
        common_mak = compact_occupied & product_occupied & compact_mak.ne(product_mak)
        assert int(common_mak.sum()) == 36
        assert float(compact_mak.loc[common_mak].sum()) == 0.0
        assert float(product_mak.loc[common_mak].sum()) == 30.841
        common_people = set(product_rows.loc[common_mak, "PersNr"].astype(str))
        future_returns = set(departures.loc[
            pd.to_datetime(departures["event_date"]).dt.normalize().gt(annual_date)
            & departures["reason_code"].eq("RUHEND_RETURN"), "persnr"].astype(str))
        initial_dormant = set(snapshot.loc[
            snapshot["Status kundenindividuell"].astype(str).str.strip().eq(
                "Ruhendes Beschäftigungsverhältnis"), "PersNr"].astype(str))
        assert len(common_people) == 36
        assert common_people <= future_returns
        assert common_people <= initial_dormant
        annual_mismatches = {}
        for dimension, actual_frame in projected.items():
            work = (annual_frame if dimension in ("Organisationseinheit", "OE-Cluster")
                    else annual_frame.assign(
                        **{dimension: _dimension_values(annual_frame, dimension)}))
            actual = {row[dimension]: (row["Köpfe"], row["MAK"])
                      for _, row in actual_frame.loc[
                          actual_frame["Stichtag"].eq(annual_date)].iterrows()}
            expected = grouped_stock_metrics(work, dimension)
            differences = {key: (actual.get(key), expected.get(key))
                           for key in actual.keys() | expected.keys()
                           if actual.get(key) != expected.get(key)}
            if differences:
                annual_mismatches[dimension] = differences
        # The same-run observation exposes a still unresolved annual-stock
        # difference. It must never be mistaken for certified parity.
        assert "Organisationseinheit" in annual_mismatches
    for date in (end,):
        reference = simulate_compact_snapshot(
            snapshot, atz, pd.Timestamp(date), base_date=start,
            abgaenge_params={**deepcopy(dep), "random_seed": seed},
            zugaenge_params={**deepcopy(hire), "random_seed": seed},
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature, include_audits=False)
        if exclusion_mode == "open":
            pd.testing.assert_frame_equal(
                same_horizon.zugaenge_result["final_state"],
                reference.zugaenge_result["final_state"], check_exact=True)
            pd.testing.assert_frame_equal(
                same_horizon.zugaenge_result["events"],
                reference.zugaenge_result["events"], check_exact=True)
            pd.testing.assert_frame_equal(
                same_horizon.future_snapshot_df, reference.future_snapshot_df,
                check_exact=True)
        frame = compact.prepare_compact_data(reference.future_snapshot_df)
        for dimension, actual_frame in projected.items():
            work = frame if dimension in ("Organisationseinheit", "OE-Cluster") else frame.assign(
                **{dimension: _dimension_values(frame, dimension)})
            actual = {row[dimension]: (row["Köpfe"], row["MAK"])
                      for _, row in actual_frame.loc[actual_frame["Stichtag"].eq(date)].iterrows()}
            expected = grouped_stock_metrics(work, dimension)
            differences = {key: (actual.get(key), expected.get(key))
                           for key in actual.keys() | expected.keys()
                           if actual.get(key) != expected.get(key)}
            assert not differences, (date, dimension, differences)


def test_default_exclusions_preserve_earlier_seed_stream_decisions() -> None:
    """With default exclusions, this seed's earlier decisions are stable."""
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(Path(__file__).resolve().parents[1]), None, None, None)
    start = pd.Timestamp(FROZEN_STICHTAG)
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=True, count_per_year=20, strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0,
                         jf_to_cluster_map=build_jf_to_cluster_map(snapshot))
    hire["trainee"].update(active=True, new_cases_per_year=4)
    deadline = pd.Timestamp("2026-12-31")
    events = []
    for target in (deadline, pd.Timestamp("2027-12-31")):
        _, departures, _, accessions = next(iter_fixed_plan_coupled_seeds(
            snapshot, people, atz, start, target, dep, deepcopy(hire), [42]))
        earlier_departures = departures.loc[pd.to_datetime(
            departures["event_date"]).le(deadline)]
        earlier_accessions = accessions["events"].loc[pd.to_datetime(
            accessions["events"]["date"]).le(deadline)]
        events.append((earlier_departures.reset_index(drop=True),
                       earlier_accessions.reset_index(drop=True)))
    pd.testing.assert_frame_equal(events[0][0], events[1][0], check_exact=True)
    pd.testing.assert_frame_equal(events[0][1], events[1][1], check_exact=True)
