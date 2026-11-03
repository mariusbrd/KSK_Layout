"""Focused target-stock cases outside the productive seed matrix."""
from __future__ import annotations

import pandas as pd

from utils.monte_carlo_departure_annual import target_head_mak_with_hires


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

    # An ATZ_END event makes the person inactive; both original IST shares
    # disappear even when they belong to different organisation units.
    atz_ended = active.copy()
    atz_ended.loc[atz_ended["PersNr"] == "101", ["active", "mak"]] = [False, 0.0]
    assert target_head_mak_with_hires(positions, atz_ended) == {
        "heads": 0, "mak": 0.0}
