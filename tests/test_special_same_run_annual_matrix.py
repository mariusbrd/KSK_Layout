"""Exact OE/JF year stocks from one full fixed-plan simulation per seed."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

import dataloader.compact_simulation_engine as simulation_engine
from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module,
    _reset_state,
)
from dataloader import loader
from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state,
    _apply_salary_automation_to_employee_state,
    _fill_reserved_plan_positions, _finalize_future_snapshot,
    _normalize_persnr_series, _prepare_employee_forecast_base,
    _resolve_atz_status_map, _resolve_simulation_cluster_context,
    _update_existing_rows, simulate_compact_snapshot,
)
from abgaenge.params import default_params as departure_defaults
from zugaenge.params import default_params as hire_defaults
from utils.compact_regular_stock import (
    atz_affected_position_mask, project_regular_position_stocks,
    project_special_annual_position_stocks,
    rebase_existing_atz_status, sync_original_position_oe,
    update_compact_for_atz_changes, update_stock_only_for_atz_changes,
)
from utils.compact_special_position_events import special_position_transitions
from utils.monte_carlo_complete_packet import build_complete_metric_packet
from utils.monte_carlo_ensemble import aggregate_packets
from utils.monte_carlo_event_packet import build_event_only_packet
from utils.monte_carlo_metric_packet import LEVELS, make_metric_packet
from utils.org_simulation_reconciliation import grouped_stock_metrics
from utils.org_simulation_events import METRICS
from utils.status_quo_baseline import build_status_quo_snapshot
from components.jobfamily_simulation_yearly import _dimension_values


@pytest.mark.parametrize("seed,exclusion_mode,target_label,strategy,annual_hires,quit_rate,atz_rate,azubi_cases,trainee_cases,ruh_cases,modified_input", [
    (7, "default", "2030-12-31", "Fill Vacancies", 20, 0.12, 0.35, 4, 4, 0, False),
    (42, "default", "2030-12-31", "Fill Vacancies", 20, 0.12, 0.35, 4, 4, 0, False),
    (42, "open", "2030-12-31", "Fill Vacancies", 20, 0.12, 0.35, 4, 4, 0, False),
    (123, "default", "2028-12-31", "Random", 30, 0.25, 0.0, 4, 4, 0, False),
    (999, "open", "2027-12-31", "OrgUnit", 12, 0.0, 0.35, 4, 4, 0, False),
    (77, "default", "2027-12-31", "Fill Vacancies", 0, 0.12, 0.0, 0, 0, 0, False),
    (77, "selected", "2028-12-31", "Fill Vacancies", 20, 0.12, 0.0, 4, 4, 4, False),
    (7, "default", "2032-12-31", "Fill Vacancies", 0, 0.12, 0.0, 0, 0, 0, False),
    (55, "default", "2027-12-31", "Fill Vacancies", 20, 0.12, 0.0, 4, 4, 0, True),
])
def test_all_annual_slices_match_same_full_run(
        seed: int, exclusion_mode: str, target_label: str, strategy: str,
        annual_hires: int, quit_rate: float, atz_rate: float,
        azubi_cases: int, trainee_cases: int, ruh_cases: int,
        modified_input: bool,
        monkeypatch: pytest.MonkeyPatch) -> None:
    if exclusion_mode in ("open", "selected"):
        exclusions = deepcopy(FROZEN_SETTINGS["exclusions"])
        exclusions.update(vorstand=False, ruhend_bv=False,
                          org_units=([] if exclusion_mode == "open" else ["9910", "9920"]))
        monkeypatch.setitem(FROZEN_SETTINGS, "exclusions", exclusions)
        monkeypatch.setattr(simulation_engine, "get_setting",
                            lambda key, default=None: deepcopy(FROZEN_SETTINGS.get(key, default)))
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    if modified_input:
        snapshot = snapshot.copy(deep=True)
        occupied = snapshot["PersNr"].dropna()
        unique_people = occupied.loc[~occupied.duplicated(keep=False)]
        candidates = snapshot.loc[
            snapshot["PersNr"].isin(unique_people)
            & snapshot["Soll_FTE"].gt(0)
            & snapshot["MAK_Reporting"].ge(0.99)
            & ~snapshot["Ist_Azubi"].fillna(False).astype(bool)]
        assert not candidates.empty
        changed_index = candidates.index[0]
        snapshot.at[changed_index, "BsGrd"] = 80.0
        snapshot.at[changed_index, "Personen_MAK_Source"] = 0.8
        snapshot.at[changed_index, "MAK_Reporting"] = 0.8
        for column in ("MAK_Calculated", "MAK", "mak", "Personen_MAK"):
            if column in snapshot.columns:
                snapshot.at[changed_index, column] = 0.8
    atz = loader.load_atz_data_cached(str(Path(__file__).resolve().parents[1]), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(target_label)
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    compact = _load_page_module("*_Kompakt.py", "special_annual_matrix_compact")
    source, bundle, signature = _resolve_simulation_cluster_context()
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=atz_rate, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=quit_rate, use_quit_matrix=False)
    dep["ruhend"]["ruhend_new_cases_per_year"] = ruh_cases
    hire["new_hires"].update(
        active=annual_hires > 0, count_per_year=annual_hires, strategy=strategy,
        target_org_unit=(str(people["Organisationseinheit"].dropna().iloc[0])
                         if strategy == "OrgUnit" else None))
    hire["azubi"].update(active=azubi_cases > 0,
                         new_cases_per_year=azubi_cases, retention_rate=1.0)
    hire["trainee"].update(active=trainee_cases > 0,
                           new_cases_per_year=trainee_cases)
    observed: dict[pd.Timestamp, pd.DataFrame] = {}
    partial_date = (pd.Timestamp("2027-06-30")
                    if seed == 42 and exclusion_mode == "open" else None)

    def capture(date: pd.Timestamp, state: pd.DataFrame,
                events: pd.DataFrame) -> None:
        observed[date] = state

    product = simulate_compact_snapshot(
        snapshot, atz, end, base_date=start,
        abgaenge_params={**deepcopy(dep), "random_seed": seed},
        zugaenge_params={**deepcopy(hire), "random_seed": seed},
        active_cluster_source=source, cluster_mapping_bundle=bundle,
        cluster_source_signature=signature, include_audits=False,
        year_end_observer=capture,
        month_end_observer=capture if partial_date is not None else None)
    departures = product.abgaenge_result["events_person_level"]
    if target_label == "2032-12-31":
        assert departures["reason_code"].eq("ATZ_END").any()
    if ruh_cases:
        assert departures["reason_code"].astype(str).str.contains("RUH", case=False).any()
    atz_pivot = product.abgaenge_result["tables"]["atz_pivot"]
    accession_events = product.zugaenge_result["events"]
    transitions = special_position_transitions(snapshot, accession_events)
    preapplied_people = _apply_attrition_events_to_employee_state(people, departures)
    dates = [pd.Timestamp(year, 12, 31) for year in range(2026, end.year + 1)]
    if partial_date is not None:
        dates.append(partial_date)
        dates.sort()
    assert all(date in observed for date in dates)

    first_updated = None
    previous_input = None
    previous_stock = None
    previous_compact = None
    annual_baselines = []
    separate_results = {}
    for date in dates:
        annual_events = accession_events.loc[
            pd.to_datetime(accession_events["date"]).dt.normalize().le(date)].copy()
        annual_people = observed[date].copy()
        annual_people["PersNr"] = _normalize_persnr_series(annual_people["PersNr"])
        annual_people = _apply_salary_automation_to_employee_state(
            annual_people, target_date=date, events_df=annual_events)
        atz_status = _resolve_atz_status_map(atz_pivot, date)
        annual_snapshot = _update_existing_rows(snapshot, annual_people,
                                                date, atz_status)
        annual_snapshot = _fill_reserved_plan_positions(
            annual_snapshot, snapshot, annual_people)
        annual_snapshot = _finalize_future_snapshot(
            annual_snapshot, date, active_cluster_source=source,
            cluster_mapping_bundle=bundle)
        annual_frame = compact.prepare_compact_data(annual_snapshot)
        if date == end:
            final_frame = compact.prepare_compact_data(product.future_snapshot_df)
            for target_dimension in ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"):
                observed_target = (annual_frame if target_dimension in
                                   ("Organisationseinheit", "OE-Cluster") else
                                   annual_frame.assign(**{
                                       target_dimension: _dimension_values(annual_frame, target_dimension)}))
                final_target = (final_frame if target_dimension in
                                ("Organisationseinheit", "OE-Cluster") else
                                final_frame.assign(**{
                                    target_dimension: _dimension_values(final_frame, target_dimension)}))
                assert grouped_stock_metrics(observed_target, target_dimension) == (
                    grouped_stock_metrics(final_target, target_dimension))

        direct_update = _update_existing_rows(
            snapshot, preapplied_people, date, atz_status)
        if first_updated is None:
            first_updated = direct_update
            preapplied_snapshot = direct_update
        else:
            preapplied_snapshot = rebase_existing_atz_status(
                first_updated, snapshot, preapplied_people, atz_status)
            pd.testing.assert_frame_equal(
                preapplied_snapshot, direct_update, check_exact=True)
        full_stock = _finalize_future_snapshot(
            preapplied_snapshot, date, active_cluster_source=source,
            cluster_mapping_bundle=bundle, stock_only=True)
        if previous_input is None:
            stock = full_stock
            affected = None
        else:
            affected = atz_affected_position_mask(previous_input, preapplied_snapshot)
            stock = update_stock_only_for_atz_changes(
                previous_input, previous_stock, preapplied_snapshot,
                lambda affected: _finalize_future_snapshot(
                    affected, date, active_cluster_source=source,
                    cluster_mapping_bundle=bundle, stock_only=True))
            pd.testing.assert_frame_equal(stock, full_stock, check_exact=True)
        previous_input, previous_stock = preapplied_snapshot, stock
        direct_compact = compact.prepare_compact_data(stock)
        if previous_compact is None:
            preapplied_frame = direct_compact
        else:
            preapplied_frame = update_compact_for_atz_changes(
                previous_compact, stock, affected, compact.prepare_compact_data)
            pd.testing.assert_frame_equal(
                preapplied_frame, direct_compact, check_exact=True)
        previous_compact = preapplied_frame
        preapplied_frame = sync_original_position_oe(
            preapplied_frame, snapshot, observed[date])
        mapped = apply_clusters_to_snapshot_from_source(
            preapplied_frame, source, mapping_bundle=bundle)
        preapplied_frame["OE-Cluster"] = mapped["OE-Cluster"].to_numpy()
        annual_baselines.append((date, preapplied_frame, mapped))
        projected = project_regular_position_stocks(
            preapplied_frame, mapped, pd.DataFrame(), [],
            base_date=start, target_date=date,
            special_transitions=transitions, target_only=True)
        separate_results[date] = projected
        for dimension, group_frame in projected.items():
            product_frame = (annual_frame if dimension in
                             ("Organisationseinheit", "OE-Cluster") else
                             annual_frame.assign(**{
                                 dimension: _dimension_values(annual_frame, dimension)}))
            actual = {row[dimension]: (row["Köpfe"], row["MAK"])
                      for _, row in group_frame.loc[group_frame["Stichtag"].eq(date)].iterrows()}
            expected = grouped_stock_metrics(product_frame, dimension)
            differences = {key: (actual.get(key), expected.get(key))
                           for key in actual.keys() | expected.keys()
                           if actual.get(key) != expected.get(key)}
            assert not differences, (seed, exclusion_mode, date, dimension, differences)
    chronological = project_special_annual_position_stocks(
        annual_baselines, transitions)
    # Serialize the real, product-checked annual positions into the compact
    # per-run stock contract. Event/target values are supplied by their own
    # adapters; these empty placeholders isolate stock serialization here.
    empty_events = {
        level: pd.DataFrame(columns=["Jahr", level, "Kategorie", "Ereignisart", *METRICS])
        for level in LEVELS}
    if seed == 999 and exclusion_mode == "open":
        from test_compact_sim_cluster_source_consistency import _load_page7_module
        totals = _load_page7_module()._metric_totals(product.future_snapshot_df)
        context = {
            "run_signature": f"full:{seed}", "metadata": product.metadata,
            "baseline_snapshot": build_status_quo_snapshot(snapshot, start),
            "final_snapshot": product.future_snapshot_df,
            "zugaenge_params": hire, "cluster_mapping": bundle.to_dict(),
            "departure_events": departures, "accession_events": accession_events,
        }
        stock_packet = build_complete_metric_packet(
            context, target_totals=totals, annual_stock_tables=chronological)
        event_reference = build_event_only_packet(context, target_totals=totals)
        assert stock_packet["target"] == event_reference["target"]
        assert stock_packet["annual_values"] == event_reference["annual_values"]
        one = aggregate_packets(base_seed=seed, count=1, runs=[
            {"seed": seed, "status": "ok", "packet": stock_packet}])
        assert one["target"]["heads"]["mean"] == totals["heads"]
        assert one["target"]["mak"]["mean"] == totals["mak"]
        assert len(stock_packet["annual_values"]) > 0
    else:
        stock_packet = make_metric_packet(
            target_totals={"heads": 0, "mak": 0.0, "eur": 0.0},
            annual_tables=empty_events,
            stock_status={level: "Abgestimmt" for level in LEVELS},
            annual_stock_tables=chronological)
    packet_stock = {record[:4]: record[4]
                    for record in stock_packet["annual_stock_values"]}
    for date in dates:
        for dimension, frame in chronological.items():
            actual = frame.loc[frame["Stichtag"].eq(date)].reset_index(drop=True)
            pd.testing.assert_frame_equal(
                actual, separate_results[date][dimension], check_exact=True)
            for row in actual.itertuples(index=False, name=None):
                _, group, heads, mak = row
                key = (date.date().isoformat(), dimension, str(group))
                assert packet_stock[(*key, "heads")] == heads
                assert packet_stock[(*key, "mak")] == mak
