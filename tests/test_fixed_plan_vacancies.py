"""The fixed staffing plan provides the only capacity for new hires."""
import pandas as pd

from utils.fixed_plan_vacancies import available_position_vacancies


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
