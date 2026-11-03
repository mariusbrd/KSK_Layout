"""Focused target-stock cases outside the productive seed matrix."""
from __future__ import annotations

import pandas as pd

from utils.monte_carlo_departure_annual import (
    prepare_target_array_model,
    prepare_target_people,
    target_head_mak_from_array_model,
    target_head_mak_with_hires,
)


def test_multi_position_split_and_atz_end_vacates_both_positions() -> None:
    positions = pd.DataFrame([
        {"PersNr": "101", "Organisationseinheit": "OE-A", "Planstelle": "A",
         "MAK_Reporting": 0.4},
        {"PersNr": "101", "Organisationseinheit": "OE-B", "Planstelle": "B",
         "MAK_Reporting": 0.6},
        {"PersNr": "102", "Organisationseinheit": "OE-C", "Planstelle": "C",
         "MAK_Reporting": 0.8},
    ])
    active = pd.DataFrame([
        {"PersNr": "101", "active": True, "mak": 0.5},
        {"PersNr": "102", "active": False, "mak": 0.0},
    ])
    assert target_head_mak_with_hires(positions, active) == {
        "heads": 1, "mak": 0.5}
    model = prepare_target_array_model(positions)
    state = {row["PersNr"]: row for row in active.to_dict("records")}
    assert target_head_mak_from_array_model(model, state) == {
        "heads": 1, "mak": 0.5}

    # An ATZ_END event makes the person inactive; both original IST shares
    # disappear even when they belong to different organisation units.
    atz_ended = active.copy()
    atz_ended.loc[atz_ended["PersNr"] == "101", ["active", "mak"]] = [False, 0.0]
    assert target_head_mak_with_hires(positions, atz_ended) == {
        "heads": 0, "mak": 0.0}
    ended_state = {row["PersNr"]: row for row in atz_ended.to_dict("records")}
    assert target_head_mak_from_array_model(model, ended_state) == {
        "heads": 0, "mak": 0.0}


def test_forecast_training_exclusion_removes_head_from_both_target_paths() -> None:
    positions = pd.DataFrame([{
        "PersNr": None, "Organisationseinheit": "OE-A", "Planstelle": "Azubi",
        "MAK_Reporting": 0.0,
    }])
    people = pd.DataFrame([{
        "PersNr": "AZ_2026_01", "active": True, "mak": 0.0,
        "is_forecast": True, "Organisationseinheit": "OE-A", "Planstelle": "Azubi",
        "Vertragsart": "Auszubildende",
    }])
    excluded = {"AZ_2026_01"}
    assert target_head_mak_with_hires(
        positions, people, excluded_new_people=excluded) == {"heads": 0, "mak": 0.0}
    assert target_head_mak_from_array_model(
        prepare_target_array_model(positions), prepare_target_people(people),
        excluded_new_people=excluded) == {"heads": 0, "mak": 0.0}
