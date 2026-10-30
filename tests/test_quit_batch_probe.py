"""Exact per-seed parity gate for the isolated quit-only batch probe."""
from __future__ import annotations

import pandas as pd
import pytest

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.quit_batch_probe import (iter_quit_seed_records, prepare_quit_batch,
                                       prepare_quit_yearly,
                                       quit_annual_event_values, departure_annual_event_values,
                                       run_quit_batch)
from test_kuendigung_forecast import _atz_df, _make_employee, _params_only_quit, EMPTY_ATZ


@pytest.mark.parametrize("matrix", [False, True])
@pytest.mark.parametrize("freq", ["M", "Q"])
def test_quit_batch_matches_reference_event_order_and_final_state(matrix: bool, freq: str) -> None:
    people = pd.DataFrame([
        _make_employee(str(100 + i), age_years=24 + i * 4, mak=0.5 + i / 10,
                       org=f"OE-{i % 3}", jobfamily=f"JF-{i % 2}")
        for i in range(12)
    ])
    people = pd.concat([people, people.iloc[[2]]], ignore_index=True)
    people.loc[12, "MAK_Calculated"] = 8.0  # duplicate ID must keep first
    atz = _atz_df("101", pd.Timestamp("2026-01-01"))
    params = _params_only_quit(
        rate_base=0.35, use_matrix=matrix,
        matrix={"alter_unter_30": {"JF-0": 0.8, "Default": 0.2},
                "alter_30_45": {"JF-1": 0.6, "Default": 0.3},
                "alter_45_55": {"Default": 0.4},
                "alter_55_plus": {"Default": 0.5}},
        adjustments={"more": {"JF-0": [2027]}, "less": {"JF-1": [2026]}},
    )
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2027-12-31")
    prepared = prepare_quit_batch(people, atz, start, end, params, freq=freq)
    batch = run_quit_batch(prepared, (7, 42, 123, 999))
    streamed = list(iter_quit_seed_records(prepared, (7, 42, 123, 999)))
    assert [seed for seed, _, _, _ in streamed] == [7, 42, 123, 999]
    for seed, result in batch.items():
        reference = run_forecast_abgaenge(
            people, atz, start, end, freq,
            {**params, "random_seed": seed}, include_diagnostics=False,
        )["events_person_level"]
        pd.testing.assert_frame_equal(result.events, reference, check_exact=True)
        records = next(records for current_seed, records, _, _ in streamed if current_seed == seed)
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference, check_exact=True)
        expected_annual = {}
        for dimension in ("Organisationseinheit", "Jobfamily"):
            yearly = reference.assign(year=reference["event_date"].dt.year)
            for (year, group), row in yearly.groupby(["year", dimension], sort=True)[
                    ["headcount_change", "mak_change"]].sum().iterrows():
                expected_annual[(year, dimension, group)] = (
                    int(row["headcount_change"]), float(row["mak_change"]))
        assert quit_annual_event_values(records) == expected_annual
        departed = set(reference["persnr"])
        expected_active = [person not in departed for person in prepared.ids]
        assert result.active.tolist() == expected_active
        assert result.mak.tolist() == [float(m) if a else 0.0
                                       for m, a in zip(prepared.mak, expected_active)]


def test_quit_batch_rejects_other_components() -> None:
    params = _params_only_quit()
    params["components"]["atz"] = True
    with pytest.raises(ValueError, match="ATZ off"):
        prepare_quit_batch(pd.DataFrame([_make_employee("101")]), EMPTY_ATZ,
                           pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31"), params)


def test_quit_batch_matches_empty_event_schema() -> None:
    people = pd.DataFrame([_make_employee("101")])
    params = _params_only_quit(rate_base=0.0)
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31")
    prepared = prepare_quit_batch(people, EMPTY_ATZ, start, end, params)
    actual = run_quit_batch(prepared, [7])[7].events
    expected = run_forecast_abgaenge(people, EMPTY_ATZ, start, end, "M",
                                     {**params, "random_seed": 7})["events_person_level"]
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def test_quit_batch_four_annual_levels_match_reference_views() -> None:
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
    from test_org_simulation_reconciliation import _bridge_context
    from utils.org_simulation_events import aggregate_simulation_events, normalize_simulation_events

    context = _bridge_context("identity")
    baseline = context["baseline_snapshot"]
    baseline = pd.concat([baseline, baseline.loc[baseline["PersNr"].eq("000003")].copy()],
                         ignore_index=True)
    baseline.loc[len(baseline) - 1, "Jobfamily"] = "Additional JF"
    baseline.loc[len(baseline) - 1, "JF-Cluster"] = "Additional Cluster"
    context["baseline_snapshot"] = baseline
    context["final_snapshot"] = baseline.copy()
    start, end = pd.Timestamp("2025-12-31"), pd.Timestamp("2027-12-31")
    context["metadata"] = {"base_date": start, "target_date": end}
    people = _prepare_employee_forecast_base(baseline, EMPTY_ATZ, start)
    params = _params_only_quit(rate_base=0.75)
    prepared = prepare_quit_batch(people, EMPTY_ATZ, start, end, params)
    yearly = prepare_quit_yearly(baseline, context["cluster_mapping"]["oe_map"])
    for seed, records, _, _ in iter_quit_seed_records(prepared, (7, 42, 123)):
        reference = run_forecast_abgaenge(
            people, EMPTY_ATZ, start, end, "M", {**params, "random_seed": seed},
            include_diagnostics=False)["events_person_level"]
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference, check_exact=True)
        context["departure_events"] = reference
        normalized = normalize_simulation_events(context, expected_run_signature="a")
        summary = {"run_signature": "a", "events": normalized, "metadata": context["metadata"]}
        actual = quit_annual_event_values(records, yearly)
        expected = {}
        for dimension in ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"):
            if dimension in ("Organisationseinheit", "OE-Cluster"):
                table = aggregate_simulation_events(normalized, dimension=dimension)
            else:
                table = build_jobfamily_yearly_view(
                    context, summary, dimension=dimension, include_stock=False)["annual"]
            for _, row in table.iterrows():
                expected[(int(row["Jahr"]), dimension, str(row[dimension]))] = (
                    int(row["Köpfe-Veränderung"]), float(row["MAK-Veränderung"]))
        assert actual == expected, seed


@pytest.mark.parametrize("with_quit", [False, True])
@pytest.mark.parametrize("freq", ["M", "Q"])
def test_retirement_stream_matches_reference_and_four_yearly_levels(
    with_quit: bool, freq: str,
) -> None:
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
    from test_org_simulation_reconciliation import _bridge_context
    from utils.org_simulation_events import aggregate_simulation_events, normalize_simulation_events

    context = _bridge_context("identity")
    baseline = context["baseline_snapshot"]
    start, end = pd.Timestamp("2025-12-31"), pd.Timestamp("2027-12-31")
    context["metadata"] = {"base_date": start, "target_date": end}
    people = _prepare_employee_forecast_base(baseline, EMPTY_ATZ, start)
    params = _params_only_quit(rate_base=0.45)
    params["components"].update(retirement=True, quit=with_quit)
    params["retirement"] = {"retirement_age": 65, "early_retirement_age": 60,
                            "rent_rate_65": 0.8, "rent_rate_60_65": 0.3}
    prepared = prepare_quit_batch(people, EMPTY_ATZ, start, end, params, freq=freq)
    yearly = prepare_quit_yearly(baseline, context["cluster_mapping"]["oe_map"])
    for seed, records, active, mak in iter_quit_seed_records(prepared, (7, 42, 123)):
        reference = run_forecast_abgaenge(
            people, EMPTY_ATZ, start, end, freq, {**params, "random_seed": seed},
            include_diagnostics=False)["events_person_level"]
        actual_events = pd.DataFrame(records) if records else pd.DataFrame(columns=reference.columns)
        pd.testing.assert_frame_equal(actual_events, reference, check_exact=True)
        departed = set(reference["persnr"]) if not reference.empty else set()
        expected_active = [person not in departed for person in prepared.ids]
        assert active.tolist() == expected_active
        assert mak.tolist() == [float(value) if still_active else 0.0
                                for value, still_active in zip(prepared.mak, expected_active)]
        context["departure_events"] = reference
        normalized = normalize_simulation_events(context, expected_run_signature="a")
        summary = {"run_signature": "a", "events": normalized, "metadata": context["metadata"]}
        expected = {}
        for dimension in ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"):
            table = (aggregate_simulation_events(normalized, dimension=dimension)
                     if dimension in ("Organisationseinheit", "OE-Cluster")
                     else build_jobfamily_yearly_view(
                         context, summary, dimension=dimension, include_stock=False)["annual"])
            for _, row in table.iterrows():
                expected[(int(row["Jahr"]), dimension, str(row[dimension]),
                          str(row["Ereignisart"]))] = (
                    int(row["Köpfe-Veränderung"]), float(row["MAK-Veränderung"]))
        assert departure_annual_event_values(records, yearly) == expected


@pytest.mark.parametrize("mode", ["return", "new", "combined"])
def test_ruhend_stream_matches_reference_events_and_mak(mode: str) -> None:
    from test_ruhend_mak_headcount import _params_only_ruhend

    people = pd.DataFrame([
        _make_employee(str(1000 + i), age_years=28 + i * 4, mak=0.5 + i / 10,
                       status="Ruhendes Beschäftigungsverhältnis" if i == 0 else "Aktiv",
                       org=f"OE-{i % 3}", jobfamily=f"JF-{i % 2}")
        for i in range(12)
    ])
    params = _params_only_ruhend(new_cases_per_year=0 if mode == "return" else 6,
                                 return_rate=1.0, avg_duration_months=3)
    if mode == "combined":
        params["components"].update(retirement=True, quit=True)
        params["retirement"] = {"retirement_age": 65, "early_retirement_age": 60,
                                "rent_rate_65": 0.8, "rent_rate_60_65": 0.3}
        params["quit"] = {"quit_rate_base": 0.2, "use_quit_matrix": False}
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2027-12-31")
    prepared = prepare_quit_batch(people, EMPTY_ATZ, start, end, params)
    for seed, records, active, mak in iter_quit_seed_records(prepared, (7, 42, 123)):
        reference = run_forecast_abgaenge(
            people, EMPTY_ATZ, start, end, "M", {**params, "random_seed": seed},
            include_diagnostics=False)["events_person_level"]
        actual = pd.DataFrame(records) if records else pd.DataFrame(columns=reference.columns)
        pd.testing.assert_frame_equal(actual, reference, check_exact=True)
        departed = set(reference.loc[reference["headcount_change"].eq(-1), "persnr"])
        assert active.tolist() == [person not in departed for person in prepared.ids]
        expected_mak = prepared.mak.copy()
        for event in reference.itertuples():
            pos = list(prepared.ids).index(event.persnr)
            if event.reason_code in ("RETIREMENT", "QUIT", "RUHEND_START"):
                expected_mak[pos] = 0.0
            elif event.reason_code == "RUHEND_RETURN":
                expected_mak[pos] = event.mak_change
        assert mak.tolist() == expected_mak.tolist()
        expected_status = prepared.initial_ruhend.copy()
        for event in reference.itertuples():
            pos = list(prepared.ids).index(event.persnr)
            if event.reason_code == "RUHEND_START":
                expected_status[pos] = True
            elif event.reason_code == "RUHEND_RETURN":
                expected_status[pos] = False
        assert run_quit_batch(prepared, [seed])[seed].status_ruhend.tolist() == \
            expected_status.tolist()


def test_ruhend_stream_four_yearly_levels_match_reference() -> None:
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
    from test_org_simulation_reconciliation import _bridge_context
    from test_ruhend_mak_headcount import _params_only_ruhend
    from utils.org_simulation_events import aggregate_simulation_events, normalize_simulation_events

    context = _bridge_context("identity")
    baseline = context["baseline_snapshot"]
    start, end = pd.Timestamp("2025-12-31"), pd.Timestamp("2027-12-31")
    context["metadata"] = {"base_date": start, "target_date": end}
    people = _prepare_employee_forecast_base(baseline, EMPTY_ATZ, start)
    params = _params_only_ruhend(new_cases_per_year=8, return_rate=1.0,
                                 avg_duration_months=3)
    params["components"].update(retirement=True, quit=True)
    params["retirement"] = {"retirement_age": 65, "early_retirement_age": 60,
                            "rent_rate_65": 0.8, "rent_rate_60_65": 0.3}
    params["quit"] = {"quit_rate_base": 0.15, "use_quit_matrix": False}
    prepared = prepare_quit_batch(people, EMPTY_ATZ, start, end, params)
    yearly = prepare_quit_yearly(baseline, context["cluster_mapping"]["oe_map"])
    for seed, records, _, _ in iter_quit_seed_records(prepared, (7, 42, 123)):
        reference = run_forecast_abgaenge(
            people, EMPTY_ATZ, start, end, "M", {**params, "random_seed": seed},
            include_diagnostics=False)["events_person_level"]
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference, check_exact=True)
        context["departure_events"] = reference
        normalized = normalize_simulation_events(context, expected_run_signature="a")
        summary = {"run_signature": "a", "events": normalized, "metadata": context["metadata"]}
        expected = {}
        for dimension in ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"):
            table = (aggregate_simulation_events(normalized, dimension=dimension)
                     if dimension in ("Organisationseinheit", "OE-Cluster")
                     else build_jobfamily_yearly_view(
                         context, summary, dimension=dimension, include_stock=False)["annual"])
            for _, row in table.iterrows():
                expected[(int(row["Jahr"]), dimension, str(row[dimension]),
                          str(row["Ereignisart"]))] = (
                    int(row["Köpfe-Veränderung"]), float(row["MAK-Veränderung"]))
        assert departure_annual_event_values(records, yearly) == expected
