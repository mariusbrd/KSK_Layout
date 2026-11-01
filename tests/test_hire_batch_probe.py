"""Exact single-month parity gate for the isolated new-hire probe."""
from __future__ import annotations

import pandas as pd
import pytest

from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.hire_batch_probe import (prepare_hire_batch, prepare_hire_month,
                                       iter_hire_seed_records)
from zugaenge.params import default_params


@pytest.mark.parametrize("strategy", ["Random", "OrgUnit", "Fill Vacancies"])
@pytest.mark.parametrize("annual_count", [20, 240])
def test_hire_month_matches_reference(strategy: str, annual_count: int) -> None:
    people = pd.DataFrame([
        {"PersNr": "000001", "Organisationseinheit": "OE-A", "Jobfamily": "JF-A",
         "OE-Cluster": "C-A", "Geschlecht": "m", "Ausbildung": "kfm Berufsabschluss",
         "active": True, "mak": 1.0},
        {"PersNr": "000002", "Organisationseinheit": "OE-B", "Jobfamily": "JF-B",
         "OE-Cluster": "C-B", "Geschlecht": "w", "Ausbildung": "Bachelor FH",
         "active": True, "mak": 0.8},
        {"PersNr": "000003", "Organisationseinheit": "OE-B", "Jobfamily": "JF-B",
         "OE-Cluster": "C-B", "Geschlecht": "w", "Ausbildung": "Bankberufsabschluss",
         "active": True, "mak": 0.6},
    ])
    vacancies = [
        {"date": pd.Timestamp("2026-01-01"), "org_unit": "OE-A",
         "planstelle": "P-A", "Jobfamily": "JF-A", "OE-Cluster": "C-A"},
        {"date": pd.Timestamp("2026-01-15"), "org_unit": "OE-B",
         "planstelle": "P-B", "Jobfamily": "JF-B", "OE-Cluster": "C-B"},
    ]
    params = default_params()
    params["new_hires"].update(active=True, count_per_year=annual_count,
                                strategy=strategy, target_org_unit="OE-A")
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-31")
    prepared = prepare_hire_month(people, start, end, params, vacancies)
    for seed, records, new_rows in iter_hire_seed_records(prepared, (7, 42, 123, 999)):
        reference = run_forecast_zugaenge(
            people, start, end_date=end, params={**params, "random_seed": seed},
            vacancies=vacancies,
        )
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference["events"],
                                      check_exact=True)
        generated = reference["final_state"].loc[
            reference["final_state"]["PersNr"].isin([row["PersNr"] for row in new_rows])]
        assert len(generated) == len(new_rows)
        for row in new_rows:
            matching = generated.loc[generated["PersNr"].eq(row["PersNr"])].iloc[0]
            for column, value in row.items():
                assert matching[column] == value, (seed, column)


def test_hire_probe_rejects_longer_horizon() -> None:
    params = default_params()
    params["new_hires"]["active"] = True
    with pytest.raises(ValueError, match="one monthly period"):
        prepare_hire_month(pd.DataFrame(), pd.Timestamp("2026-01-01"),
                           pd.Timestamp("2026-02-28"), params)


@pytest.mark.parametrize("strategy", ["Random", "OrgUnit", "Fill Vacancies"])
@pytest.mark.parametrize("start", [pd.Timestamp("2026-01-01"),
                                   pd.Timestamp("2025-12-31")])
def test_hire_batch_multiple_months_match_reference(
        strategy: str, start: pd.Timestamp) -> None:
    people = pd.DataFrame([
        {"PersNr": "000001", "Organisationseinheit": "OE-A", "Jobfamily": "JF-A",
         "OE-Cluster": "C-A", "Geschlecht": "m", "Ausbildung": "kfm Berufsabschluss",
         "active": True, "mak": 1.0},
        {"PersNr": "000002", "Organisationseinheit": "OE-B", "Jobfamily": "JF-B",
         "OE-Cluster": "C-B", "Geschlecht": "w", "Ausbildung": "Bachelor FH",
         "active": True, "mak": 0.8},
    ])
    vacancies = [
        {"date": pd.Timestamp("2026-02-01"), "org_unit": "OE-B",
         "planstelle": "P-B", "Jobfamily": "JF-B", "OE-Cluster": "C-B"},
        {"date": pd.Timestamp("2026-05-01"), "org_unit": "OE-A",
         "planstelle": "P-A", "Jobfamily": "JF-A", "OE-Cluster": "C-A"},
    ]
    params = default_params()
    params["new_hires"].update(active=True, count_per_year=24,
                                strategy=strategy, target_org_unit="OE-A")
    end = pd.Timestamp("2026-12-31")
    prepared = prepare_hire_batch(people, start, end, params, vacancies)
    for seed, records, new_rows in iter_hire_seed_records(prepared, (7, 42, 123)):
        reference = run_forecast_zugaenge(
            people, start, end_date=end, params={**params, "random_seed": seed},
            vacancies=vacancies)
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference["events"],
                                      check_exact=True)
        assert len(new_rows) == len(reference["events"])
        generated = reference["final_state"]
        for row in new_rows:
            matching = generated.loc[generated["PersNr"].eq(row["PersNr"])].iloc[0]
            for column, value in row.items():
                assert matching[column] == value, (seed, column)


@pytest.mark.parametrize("missing_distribution", [False, True])
def test_hire_batch_distribution_fallback_and_matrix_match_reference(
        missing_distribution: bool) -> None:
    people = pd.DataFrame([
        {"PersNr": "000001", "Organisationseinheit": "OE-A",
         "OE-Cluster": "C-A", "Jobfamily": "JF-A", "active": True,
         "Geschlecht": None if missing_distribution else "d",
         "Ausbildung": None if missing_distribution else "Bachelor FH"},
    ])
    params = default_params()
    params["new_hires"].update(
        active=True, count_per_year=36, strategy="Random",
        distribution=[
            {"Jobfamily": "JF-A", "OE-Cluster": "C-A", "Share %": 60.0},
            {"Jobfamily": "JF-B", "OE-Cluster": "C-B", "Share %": 40.0},
        ],
    )
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-30")
    prepared = prepare_hire_batch(people, start, end, params)
    for seed, records, _ in iter_hire_seed_records(prepared, (7, 42, 123)):
        reference = run_forecast_zugaenge(
            people, start, end_date=end, params={**params, "random_seed": seed})
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference["events"],
                                      check_exact=True)


def test_hire_batch_builds_missing_org_cluster_map_from_new_people() -> None:
    people = pd.DataFrame([
        {"PersNr": "000001", "Organisationseinheit": "OE-A",
         "Geschlecht": "m", "Ausbildung": "kfm Berufsabschluss",
         "active": True},
    ])
    params = default_params()
    params["new_hires"].update(active=True, count_per_year=36,
                                strategy="OrgUnit", target_org_unit="OE-A")
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-30")
    prepared = prepare_hire_batch(people, start, end, params)
    for seed, records, _ in iter_hire_seed_records(prepared, (7, 42, 123)):
        reference = run_forecast_zugaenge(
            people, start, end_date=end, params={**params, "random_seed": seed})
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference["events"],
                                      check_exact=True)
