"""The multi-seed reference must reproduce independent fixed-plan runs."""
from __future__ import annotations

import pandas as pd
import pytest

from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state,
    _build_fixed_plan_vacancy_context,
)
from zugaenge.fixed_plan_seed_stream import (
    _prepare_employee_after_departures, _replay_departures_prepared,
    iter_fixed_plan_accession_seeds,
)
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params


def test_seed_stream_matches_independent_position_runs_and_keeps_source() -> None:
    base, target = pd.Timestamp("2025-12-31"), pd.Timestamp("2026-12-31")
    positions = pd.DataFrame([
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 0.6,
         "Kürzel OrgEinheit": "1000", "Organisationseinheit": "Beratung",
         "Planstelle": "Berater", "Planstellennr": "REG-1"},
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 1.0,
         "Kürzel OrgEinheit": "9910", "Organisationseinheit": "Ausbildung",
         "Planstelle": "Bankkaufleute", "Planstellennr": "AZ-1"},
        {"PersNr": "100", "Is_Plan_Vacant": False, "Soll_FTE": 0.4,
         "Kürzel OrgEinheit": "1000", "Organisationseinheit": "Beratung",
         "Planstelle": "Berater", "Planstellennr": "REG-2"},
    ])
    people = pd.DataFrame([{
        "PersNr": "100", "active": True, "Jobfamily": "Angestellte",
        "Organisationseinheit": "Beratung", "Planstelle": "Berater",
        "Eintritt": pd.Timestamp("2020-01-01"), "mak": 0.4,
    }])
    empty = pd.DataFrame(columns=["persnr", "event_date", "headcount_change"])
    departure = pd.DataFrame([{
        "persnr": "100", "event_date": pd.Timestamp("2026-07-01"),
        "headcount_change": -1,
    }])
    params = default_params()
    params["azubi"].update(active=True, new_cases_per_year=12)
    params["new_hires"].update(active=True, count_per_year=24,
                                strategy="Fill Vacancies")
    cases = [(7, empty), (42, departure)]
    streamed = list(iter_fixed_plan_accession_seeds(
        positions, people, cases, base, target, params))
    assert [seed for seed, _ in streamed] == [7, 42]
    for (seed, events), (_, actual) in zip(cases, streamed):
        pool, original, _ = _build_fixed_plan_vacancy_context(positions, events, base)
        expected_params = {**params, "random_seed": seed}
        expected = run_forecast_zugaenge(
            _apply_attrition_events_to_employee_state(people, events), base,
            end_date=target, params=expected_params, vacancies=[],
            vacancy_pool=pool, original_positions=original)
        pd.testing.assert_frame_equal(actual["events"], expected["events"],
                                      check_exact=True)
        pd.testing.assert_frame_equal(actual["final_state"], expected["final_state"],
                                      check_exact=True)
        hires = actual["events"]
        hires = hires[hires["type"].isin(["New_Hire", "Azubi_Hire"])]
        assert hires["position_index"].is_unique
        assert set(hires["position_index"].astype(int)).issubset({0, 1, 2})
    assert params["random_seed"] == default_params()["random_seed"]
    assert positions["Soll_FTE"].tolist() == [0.6, 1.0, 0.4]


def test_seed_stream_rejects_duplicate_seed() -> None:
    positions = pd.DataFrame([{
        "PersNr": "100", "Is_Plan_Vacant": False, "Soll_FTE": 1.0,
        "Organisationseinheit": "Beratung", "Planstelle": "Berater",
    }])
    people = pd.DataFrame([{
        "PersNr": "100", "active": True, "Jobfamily": "Angestellte",
        "Organisationseinheit": "Beratung", "Eintritt": pd.Timestamp("2020-01-01"),
        "mak": 1.0,
    }])
    empty = pd.DataFrame(columns=["persnr", "event_date", "headcount_change"])
    with pytest.raises(ValueError, match="duplicate seed"):
        list(iter_fixed_plan_accession_seeds(
            positions, people, [(7, empty), (7, empty)],
            pd.Timestamp("2025-12-31"), pd.Timestamp("2025-12-31"),
            default_params()))


def test_prepared_departure_replay_preserves_multiple_status_events() -> None:
    people = pd.DataFrame([{
        "PersNr": "100", "active": True, "mak": 0.8,
        "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
    }, {
        "PersNr": "200", "active": True, "mak": 0.5,
        "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
    }])
    events = pd.DataFrame([
        {"persnr": "100", "event_date": pd.Timestamp("2026-03-01"),
         "headcount_change": 0, "mak_change": -0.2, "reason_code": "RUHEND_START"},
        {"persnr": "100", "event_date": pd.Timestamp("2026-06-01"),
         "headcount_change": 0, "mak_change": 0.2, "reason_code": "RUHEND_RETURN"},
        {"persnr": "200", "event_date": pd.Timestamp("2026-07-01"),
         "headcount_change": 0, "mak_change": -0.5, "reason_code": "ATZ_AR_TO_FR"},
        {"persnr": "100", "event_date": pd.Timestamp("2026-10-01"),
         "headcount_change": -1, "mak_change": -0.8, "reason_code": "QUIT"},
    ])
    from dataloader.compact_simulation_engine import _apply_attrition_events_to_employee_state
    expected = _apply_attrition_events_to_employee_state(people, events)
    template = _prepare_employee_after_departures(people)
    actual = _replay_departures_prepared(template, events)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    pd.testing.assert_frame_equal(
        _replay_departures_prepared(template, pd.DataFrame()),
        _apply_attrition_events_to_employee_state(people, pd.DataFrame()),
        check_exact=True)
