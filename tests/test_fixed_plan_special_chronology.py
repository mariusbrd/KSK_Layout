"""Independent position chronology checks for the full fixed-plan reference."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _reset_state
from dataloader import loader
from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
from abgaenge.params import default_params as departure_defaults
from zugaenge.enrichment import build_jf_to_cluster_map
from zugaenge.fixed_plan_seed_stream import iter_fixed_plan_coupled_seeds, prepare_positions
from zugaenge.params import default_params as hire_defaults
from zugaenge.forecast import run_forecast_zugaenge
from utils.compact_special_position_events import special_position_transitions
from utils.fixed_plan_vacancies import FixedPlanVacancyPool, _person_id, vacancy_matches_accession


@pytest.mark.parametrize("seed", [7, 42])
def test_special_accessions_reserve_only_free_matching_positions(seed: int) -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(Path(__file__).resolve().parents[1]), None, None, None)
    start, target = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp("2030-12-31")
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    departures, hires = departure_defaults(), hire_defaults()
    departures["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    departures["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hires["new_hires"].update(active=True, count_per_year=20, strategy="Fill Vacancies")
    hires["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0)
    hires["trainee"].update(active=True, new_cases_per_year=4)
    hires["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(snapshot)
    prepared = prepare_positions(snapshot, start)
    metadata = {row["position_index"]: row for row in (*prepared.initial, *(row for _, row in prepared.occupied))}

    _, departure_events, _, accession = next(iter_fixed_plan_coupled_seeds(
        snapshot, people, atz, start, target, departures, deepcopy(hires), [seed]))
    free_at = {row["position_index"]: pd.Timestamp(row["date"]) for row in prepared.initial}
    occupied = {}
    for person, row in prepared.occupied:
        occupied.setdefault(person, set()).add(row["position_index"])
    releases = sorted((pd.Timestamp(row["event_date"]), _person_id(row["persnr"]))
                      for row in departure_events.to_dict("records")
                      if int(row.get("headcount_change", 0) or 0) < 0)
    release_cursor = 0
    counts = {name: 0 for name in ("Azubi_Hire", "Trainee_Hire", "New_Hire", "Azubi_Conversion_In")}
    for event in accession["events"].to_dict("records"):
        date = pd.Timestamp(event["date"])
        while release_cursor < len(releases) and releases[release_cursor][0] <= date:
            release_date, person = releases[release_cursor]
            for position in occupied.pop(person, set()):
                assert position not in free_at
                free_at[position] = release_date
            release_cursor += 1
        kind = event["type"]
        person = _person_id(event["persnr"])
        if kind in {"Azubi_Conversion_Out", "Azubi_Exit"}:
            for position in occupied.pop(person, set()):
                assert vacancy_matches_accession(metadata[position], "azubi")
                assert position not in free_at
                free_at[position] = date
        if kind not in counts:
            continue
        counts[kind] += 1
        position = int(event["position_index"])
        assert position in free_at, (seed, kind, date, position)
        assert free_at[position] <= date
        expected_kind = ("azubi" if kind == "Azubi_Hire" else
                         "trainee" if kind == "Trainee_Hire" else "regular")
        assert vacancy_matches_accession(metadata[position], expected_kind)
        if kind == "Azubi_Hire":
            assert float(event["mak"]) == 0.0
        occupied.setdefault(person, set()).add(position)
        del free_at[position]
    assert counts["Azubi_Hire"] > 0
    assert counts["New_Hire"] > 0
    assert counts["Azubi_Conversion_In"] > 0
    # The production plan currently has no available designated trainee row.
    assert counts["Trainee_Hire"] == 0


@pytest.mark.parametrize("retention_rate,expected_end", [
    (1.0, "Azubi_Conversion_In"), (0.0, "Azubi_Exit"),
])
def test_synthetic_special_slots_are_released_and_reused(
        retention_rate: float, expected_end: str) -> None:
    start, graduation, target = map(pd.Timestamp, (
        "2026-01-01", "2026-08-01", "2026-12-31"))
    training = {"position_index": 0, "date": start, "org_code": "9910",
                "org_unit": "Ausbildung", "planstelle": "Bankkaufleute", "soll_mak": 1.0}
    trainee = {"position_index": 1, "date": start, "org_code": "9920",
               "org_unit": "Nachwuchs", "planstelle": "Trainee", "soll_mak": 0.8}
    regular = {"position_index": 2, "date": start, "org_code": "1000",
               "org_unit": "Beratung", "planstelle": "Berater", "soll_mak": 0.6}
    person = pd.DataFrame([{
        "PersNr": "100", "active": True, "Jobfamily": "Azubi",
        "Organisationseinheit": "Ausbildung", "Planstelle": "Bankkaufleute",
        "Eintritt": pd.Timestamp("2023-08-01"), "GraduationDate": graduation,
        "mak": 0.0, "Ausbildung": "derzeit Berufsausbildung",
    }])
    params = hire_defaults()
    params["random_seed"] = 7
    params["azubi"].update(active=True, new_cases_per_year=24,
                            retention_rate=retention_rate)
    params["trainee"].update(active=True, new_cases_per_year=24)
    params["new_hires"].update(active=True,
                               count_per_year=0 if retention_rate == 1.0 else 24,
                               strategy="Fill Vacancies")
    result = run_forecast_zugaenge(
        person, start, end_date=target, params=params,
        vacancy_pool=FixedPlanVacancyPool([trainee, regular]),
        original_positions={"100": [training],
                            "_by_index": {0: training, 1: trainee, 2: regular}})
    monthly_states = {}
    observed_result = run_forecast_zugaenge(
        person, start, end_date=target, params=params,
        vacancy_pool=FixedPlanVacancyPool([trainee, regular]),
        original_positions={"100": [training],
                            "_by_index": {0: training, 1: trainee, 2: regular}},
        month_end_observer=lambda date, state, events: monthly_states.update({date: state}))
    pd.testing.assert_frame_equal(result["events"], observed_result["events"], check_exact=True)
    pd.testing.assert_frame_equal(result["final_state"], observed_result["final_state"], check_exact=True)
    assert pd.Timestamp("2026-06-30") in monthly_states
    events = result["events"]
    assert expected_end in set(events["type"])
    assert len(events.loc[events["type"] == "Trainee_Hire"]) == 1
    trainee_hire = events.loc[events["type"] == "Trainee_Hire"].iloc[0]
    assert int(trainee_hire["position_index"]) == 1
    assert float(trainee_hire["mak"]) == 0.8
    azubi_hires = events.loc[events["type"] == "Azubi_Hire"]
    assert len(azubi_hires) == 1
    assert int(azubi_hires.iloc[0]["position_index"]) == 0
    assert pd.Timestamp(azubi_hires.iloc[0]["date"]) >= graduation
    assert float(azubi_hires.iloc[0]["mak"]) == 0.0
    regular_entries = events.loc[events["type"].isin(
        ["New_Hire", "Azubi_Conversion_In"])]
    assert len(regular_entries) == 1
    assert int(regular_entries.iloc[0]["position_index"]) == 2
    if retention_rate == 1.0:
        assert regular_entries.iloc[0]["type"] == "Azubi_Conversion_In"
        assert pd.Timestamp(regular_entries.iloc[0]["date"]) == graduation
        assert float(regular_entries.iloc[0]["mak"]) == 0.6
    else:
        assert regular_entries.iloc[0]["type"] == "New_Hire"
    position_snapshot = pd.DataFrame([
        {"PersNr": "100"}, {"PersNr": pd.NA}, {"PersNr": pd.NA},
    ])
    transitions = special_position_transitions(position_snapshot, events)
    training_steps = [(step.action, step.event_type, step.position_index)
                      for step in transitions if step.position_index == 0]
    assert training_steps == [
        ("release", "Azubi_Conversion_Out" if retention_rate == 1.0
         else "Azubi_Exit", 0),
        ("reserve", "Azubi_Hire", 0),
    ]
    assert [(step.action, step.event_type, step.position_index)
            for step in transitions if step.position_index == 1] == [
                ("reserve", "Trainee_Hire", 1)]
    assert [(step.action, step.event_type, step.position_index)
            for step in transitions if step.position_index == 2] == [
                ("reserve", "Azubi_Conversion_In" if retention_rate == 1.0
                 else "New_Hire", 2)]


def test_transition_bridge_releases_all_training_positions() -> None:
    positions = pd.DataFrame({"PersNr": ["100", "100", pd.NA]})
    events = pd.DataFrame([
        {"date": pd.Timestamp("2026-08-01"), "type": "Azubi_Conversion_Out",
         "persnr": "100"},
        {"date": pd.Timestamp("2026-08-01"), "type": "Azubi_Conversion_In",
         "persnr": "100", "position_index": 2, "mak": 0.5},
    ])
    transitions = special_position_transitions(positions, events)
    assert [(item.action, item.position_index) for item in transitions] == [
        ("release", 0), ("release", 1), ("reserve", 2)]
