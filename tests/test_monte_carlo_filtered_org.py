import pickle
import zlib

import pandas as pd
import pytest

from utils.monte_carlo_filtered_org import (
    filtered_org_composition_statistics,
    filtered_org_departure_reasons, filtered_org_kpi_statistics,
    filtered_org_rank_statistics, filtered_org_statistics)


def test_filtered_statistics_filter_each_seed_before_quantiles():
    def blob(rows):
        return zlib.compress(pickle.dumps(pd.DataFrame(rows), protocol=pickle.HIGHEST_PROTOCOL))

    common = {"Is_Vacant": False, "Geschlecht": "w", "MAK_Reporting": 0.5,
              "EUR_Reporting": 100.0}
    first = blob([{**common, "PersNr": 1, "Organisationseinheit": "A", "Jobfamily": "X"},
                  {**common, "PersNr": 1, "Organisationseinheit": "A", "Jobfamily": "X"},
                  {**common, "PersNr": 2, "Organisationseinheit": "B", "Jobfamily": "Y"}])
    second = blob([{**common, "PersNr": 1, "Organisationseinheit": "A", "Jobfamily": "X"},
                   {**common, "PersNr": 3, "Organisationseinheit": "A", "Jobfamily": "X"}])
    ensemble = {"org_filter_snapshot_version": 1, "org_filter_snapshots": [first, second],
                "count": 2, "band_level": 0.8}
    values = filtered_org_statistics(ensemble, {"selected_jobfamilies": ["X"]})
    assert values[("Organisationseinheit", "A", "heads")] == {
        "mean": 1.5, "lower": 1.1, "upper": 1.9}
    assert values[("Organisationseinheit", "A", "mak")]["mean"] == 1.0
    assert ("Organisationseinheit", "B", "heads") not in values


def test_kpi_ratios_are_calculated_within_each_filtered_seed():
    class Compact:
        @staticmethod
        def get_ist_koepfe(frame):
            from dataloader.kpi_engine import compute_headcount
            return compute_headcount(frame)

        @staticmethod
        def get_ist_mak(frame):
            from dataloader.kpi_engine import compute_fte_effektiv
            return compute_fte_effektiv(frame)

        @staticmethod
        def get_ist_eur(frame):
            from dataloader.kpi_engine import get_unique_employees
            return get_unique_employees(frame)["EUR_Reporting"].sum()

    def blob(rows):
        return zlib.compress(pickle.dumps(pd.DataFrame(rows), protocol=pickle.HIGHEST_PROTOCOL))

    base = {"Is_Vacant": False, "Organisationseinheit": "A", "Jobfamily": "X",
            "MAK_Reporting": 0.5, "EUR_Reporting": 100.0,
            "BsGrd": 50.0, "Geschlecht": "w", "ATZ_Status": "Keine ATZ"}
    first = blob([{**base, "PersNr": 1}])
    second = blob([{**base, "PersNr": 1}, {**base, "PersNr": 2, "Geschlecht": "m"}])
    ensemble = {"org_filter_snapshot_version": 1, "org_filter_snapshots": [first, second],
                "count": 2, "band_level": 0.8}
    values = filtered_org_kpi_statistics(ensemble, {"selected_jobfamilies": ["X"]}, Compact())
    assert values["heads"]["mean"] == 1.5
    assert values["female_share"]["mean"] == 0.75
    assert values["eur_per_head"]["mean"] == 100.0
    assert values["seed_zero"]["heads"] == 1


def test_ranking_and_departure_drivers_use_same_filter_in_each_seed():
    def blob(rows):
        return zlib.compress(pickle.dumps(pd.DataFrame(rows), protocol=pickle.HIGHEST_PROTOCOL))

    def target(person, org, family, mak):
        return {"PersNr": person, "Organisationseinheit": org, "Jobfamily": family,
                "Is_Vacant": False, "MAK_Reporting": mak, "EUR_Reporting": 100 * mak}

    def event(person, org, family, mak):
        return {"persnr": person, "Organisationseinheit": org, "Jobfamily": family,
                "headcount_change": -1, "mak_change": -mak,
                "reason_label": "Kündigung"}

    ensemble = {
        "count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
        "org_departure_events_version": 1,
        "org_filter_snapshots": [
            blob([target(1, "A", "X", 0.5), target(1, "A", "X", 0.5),
                  target(2, "B", "Y", 1.0)]),
            blob([target(3, "A", "X", 1.0), target(4, "A", "X", 1.0)]),
        ],
        "org_departure_events": [
            blob([event(1, "A", "X", 1.0), event(2, "B", "Y", 1.0)]),
            blob([event(3, "A", "X", 1.0), event(4, "A", "X", 1.0)]),
        ],
    }
    stats = filtered_org_rank_statistics(ensemble, {"selected_jobfamilies": ["X"]})
    assert stats["heads"]["A"]["mean"] == 1.5
    assert stats["mak"]["A"]["mean"] == 1.5
    assert stats["departures"]["A"]["mean"] == 1.5
    assert "B" not in stats["departures"]
    assert stats["heads"]["A"]["seed_zero"] == 1
    reasons = filtered_org_departure_reasons(
        ensemble, {"selected_jobfamilies": ["X"]})
    assert reasons.loc[0, "Abgänge"] == 1.5
    assert reasons.loc[0, "Abgänge P10"] == 1.1


def test_composition_segments_and_total_bands_are_computed_per_seed():
    def blob(rows):
        return zlib.compress(pickle.dumps(pd.DataFrame(rows), protocol=pickle.HIGHEST_PROTOCOL))

    def row(person, gender, mak):
        return {"PersNr": person, "Organisationseinheit": "A", "Jobfamily": "X",
                "Geschlecht": gender, "Alterskohorte": "40–49",
                "Beschäftigungsstatus": "Aktiv", "Is_Vacant": False,
                "MAK_Reporting": mak, "EUR_Reporting": 100 * mak}

    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [
                    blob([row(1, "w", 0.5), row(1, "w", 0.5)]),
                    blob([row(2, "w", 1.0), row(3, "m", 1.0)])]}
    result = filtered_org_composition_statistics(
        ensemble, {"selected_jobfamilies": ["X"]}, ["A"], "Köpfe",
        ("Geschlecht", "Alterskohorte", "Beschäftigungsstatus"))
    gender = result["Geschlecht"]["segments"].set_index("Geschlecht")
    assert gender.loc["w", "Wert"] == 1.0
    assert gender.loc["w", "Seed 0"] == 1.0
    assert gender.loc["m", "P10"] == pytest.approx(0.1)
    total = result["Geschlecht"]["totals"].iloc[0]
    assert total["Wert"] == 1.5
    assert total["P10"] == 1.1
    money = filtered_org_composition_statistics(
        ensemble, {"selected_jobfamilies": ["X"]}, ["A"], "EUR",
        ("Geschlecht",))["Geschlecht"]["segments"].set_index("Geschlecht")
    assert money.loc["w", "Wert"] == 100.0
    assert money.loc["m", "P90"] == pytest.approx(90.0)
