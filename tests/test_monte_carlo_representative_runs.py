"""Representative views must identify existing complete runs, not stitched years."""
from __future__ import annotations

import pytest

from utils.monte_carlo_representative_runs import select_representative_runs


def _ensemble():
    return {
        "status": "complete", "count": 3,
        "seed_pairs": ((42, 71), (43, 72), (44, 73)),
        "statistics": {
            "status": "complete", "requested_count": 3,
            "annual_event_samples": [
                (2026, "Organisationseinheit", "A", "QUIT", "Abgänge", [0, 8, 3]),
                (2027, "Organisationseinheit", "A", "QUIT", "Abgänge", [9, 0, 3]),
                # Other exit types cannot change the voluntary-turnover order.
                (2026, "Organisationseinheit", "A", "RETIREMENT", "Abgänge", [50, 0, 0]),
            ],
            "annual_stock_samples": [
                ("2026-12-31", "Organisationseinheit", "A", "heads", [100, 80, 90]),
                ("2026-12-31", "Organisationseinheit", "A", "mak", [80, 60, 70]),
                ("2027-12-31", "Organisationseinheit", "A", "heads", [80, 100, 90]),
                ("2027-12-31", "Organisationseinheit", "A", "mak", [60, 80, 70]),
            ],
        },
    }


def test_scenarios_select_one_complete_path_with_stable_seed_pairs():
    selected = select_representative_runs(_ensemble())
    assert {name: value["index"] for name, value in selected.items()} == {
        "best": 2, "worst": 0, "typical": 2}
    assert selected["best"]["seed_pair"] == (44, 73)
    assert selected["worst"]["quit_departures"] == 9.0
    assert selected["typical"]["mean_path_distance"] < selected["worst"]["mean_path_distance"]


def test_ties_choose_lowest_run_index_and_do_not_stitch_years():
    ensemble = _ensemble()
    ensemble["statistics"]["annual_event_samples"][:2] = [
        (2026, "Organisationseinheit", "A", "QUIT", "Abgänge", [0, 4, 2]),
        (2027, "Organisationseinheit", "A", "QUIT", "Abgänge", [4, 0, 2]),
    ]
    selected = select_representative_runs(ensemble)
    assert selected["best"]["index"] == 0
    assert selected["worst"]["index"] == 0
    assert selected["typical"]["index"] == 2


def test_incomplete_contract_fails_closed():
    ensemble = _ensemble()
    ensemble["statistics"]["annual_stock_samples"].pop()
    with pytest.raises(ValueError, match="Jahresbestände"):
        select_representative_runs(ensemble)
    ensemble = _ensemble()
    ensemble["statistics"]["annual_event_samples"][0] = (
        2026, "Organisationseinheit", "A", "QUIT", "Abgänge", [0, 8])
    with pytest.raises(ValueError, match="Unvollständige Laufwerte"):
        select_representative_runs(ensemble)
