"""The fixed staffing plan provides the only capacity for new hires."""
import pandas as pd
import numpy as np

from utils.fixed_plan_vacancies import (
    FixedPlanVacancyPool, available_position_vacancies,
    vacancy_matches_accession,
)


def test_existing_and_departure_vacancies_keep_position_soll_and_dates() -> None:
    base = pd.Timestamp("2025-12-31")
    positions = pd.DataFrame([
        {"PersNr": None, "Is_Vacant": True, "Is_Excluded": False,
         "Soll_FTE": 0.5, "Organisationseinheit": "OE-A", "Planstelle": "P-A",
         "Planstellennr": "1"},
        {"PersNr": "101", "Is_Vacant": False, "Is_Excluded": False,
         "Soll_FTE": 0.4, "Organisationseinheit": "OE-B", "Planstelle": "P-B",
         "Planstellennr": "2"},
        {"PersNr": "101", "Is_Vacant": False, "Is_Excluded": False,
         "Soll_FTE": 0.6, "Organisationseinheit": "OE-C", "Planstelle": "P-C",
         "Planstellennr": "3"},
        {"PersNr": None, "Is_Vacant": True, "Is_Excluded": False,
         "Soll_FTE": 0.0, "Organisationseinheit": "OE-D", "Planstelle": "P-D",
         "Planstellennr": "4"},
        {"PersNr": None, "Is_Vacant": True, "Is_Excluded": True,
         "Soll_FTE": 1.0, "Organisationseinheit": "OE-E", "Planstelle": "P-E",
         "Planstellennr": "5"},
    ])
    events = pd.DataFrame([{
        "persnr": "101", "headcount_change": -1,
        "event_date": pd.Timestamp("2026-07-01"),
    }])

    result = available_position_vacancies(positions, events, base)
    assert [(row["position_index"], row["date"], row["soll_mak"])
            for row in result] == [
        (0, base, 0.5),
        (1, pd.Timestamp("2026-07-01"), 0.4),
        (2, pd.Timestamp("2026-07-01"), 0.6),
    ]

    pool = FixedPlanVacancyPool(result)
    assert pool.reserve(pd.Timestamp("2026-01-01"), strategy="Fill Vacancies")[
        "position_index"] == 0
    assert pool.reserve(pd.Timestamp("2026-06-30"), strategy="Fill Vacancies") is None
    assert pool.reserve(pd.Timestamp("2026-07-01"), strategy="Random",
                        rng=np.random.default_rng(7),
                        eligible=lambda row: row["org_unit"] == "OE-C")[
        "position_index"] == 2
    assert pool.reserve(pd.Timestamp("2026-07-01"), strategy="Fill Vacancies")[
        "position_index"] == 1
    assert pool.reserve(pd.Timestamp("2026-07-01"), strategy="Fill Vacancies") is None
    with_excluded = available_position_vacancies(
        positions, events, base, include_report_excluded=True)
    assert [(row["position_index"], row["soll_mak"])
            for row in with_excluded] == [(0, 0.5), (4, 1.0), (1, 0.4), (2, 0.6)]


def test_accession_kinds_use_dedicated_plan_positions() -> None:
    vacancies = [
        {"date": pd.Timestamp("2025-12-31"), "position_index": 0,
         "org_code": "9910", "planstelle": "Bankkaufleute", "soll_mak": 0.5},
        {"date": pd.Timestamp("2025-12-31"), "position_index": 1,
         "org_code": "9920", "planstelle": "Nachwuchs", "soll_mak": 1.0},
        {"date": pd.Timestamp("2025-12-31"), "position_index": 2,
         "org_code": "1000", "planstelle": "Trainee Strategie", "soll_mak": 1.0},
        {"date": pd.Timestamp("2025-12-31"), "position_index": 3,
         "org_code": "1000", "planstelle": "Beratung", "soll_mak": 1.0},
    ]
    pool = FixedPlanVacancyPool(vacancies)
    date = pd.Timestamp("2026-01-01")
    assert pool.reserve(date, strategy="Fill Vacancies",
                        eligible=lambda row: vacancy_matches_accession(row, "azubi"))["position_index"] == 0
    assert pool.reserve(date, strategy="Fill Vacancies",
                        eligible=lambda row: vacancy_matches_accession(row, "trainee"))["position_index"] == 1
    assert pool.reserve(date, strategy="Fill Vacancies",
                        eligible=lambda row: vacancy_matches_accession(row, "regular"))["position_index"] == 3
    assert pool.reserve(date, strategy="Fill Vacancies",
                        eligible=lambda row: vacancy_matches_accession(row, "regular")) is None
