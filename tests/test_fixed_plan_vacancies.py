"""The fixed staffing plan provides the only capacity for new hires."""
import pandas as pd
import numpy as np

from utils.fixed_plan_vacancies import (
    FixedPlanVacancyPool, available_position_vacancies,
    vacancy_matches_accession,
)
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params
from dataloader.compact_simulation_engine import _fill_reserved_plan_positions


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
         "Is_Plan_Vacant": True,
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


def test_external_accessions_share_finite_position_supply() -> None:
    base = pd.Timestamp("2025-12-31")
    vacancies = [
        {"date": base, "position_index": 0, "org_code": "9910",
         "org_unit": "Ausbildung", "planstelle": "Bankkaufleute", "soll_mak": 0.5},
        {"date": base, "position_index": 1, "org_code": "1000",
         "org_unit": "Beratung", "planstelle": "Berater", "soll_mak": 0.6},
    ]
    params = default_params()
    params["azubi"].update(active=True, new_cases_per_year=24)
    params["trainee"].update(active=True, new_cases_per_year=24)
    params["new_hires"].update(active=True, count_per_year=24)
    result = run_forecast_zugaenge(
        pd.DataFrame([{"PersNr": "100", "active": True,
                       "Jobfamily": "Angestellte", "Organisationseinheit": "Beratung",
                       "Eintritt": pd.Timestamp("2020-01-01")}]),
        pd.Timestamp("2026-01-01"), end_date=pd.Timestamp("2026-12-31"),
        params=params, vacancy_pool=FixedPlanVacancyPool(vacancies))
    hires = result["events"][result["events"]["type"].isin(
        ["Azubi_Hire", "Trainee_Hire", "New_Hire"])]
    assert set(hires["type"]) == {"Azubi_Hire", "New_Hire"}
    assert set(hires["position_index"].astype(int)) == {0, 1}
    assert hires.loc[hires["type"] == "New_Hire", "mak"].iloc[0] == 0.6


def test_azubi_takeover_needs_free_regular_position() -> None:
    base = pd.Timestamp("2025-12-31")
    training = {"date": base, "position_index": 0, "org_code": "9910",
                "org_unit": "Ausbildung", "planstelle": "Bankkaufleute", "soll_mak": 1.0}
    regular = {"date": base, "position_index": 1, "org_code": "1000",
               "org_unit": "Beratung", "planstelle": "Berater", "soll_mak": 0.6}
    person = pd.DataFrame([{
        "PersNr": "100", "active": True, "Jobfamily": "Azubi", "TrfGr": "TVAöD",
        "Organisationseinheit": "Ausbildung", "Planstelle": "Bankkaufleute",
        "Eintritt": pd.Timestamp("2023-08-01"),
        "GraduationDate": pd.Timestamp("2026-08-01"), "mak": 0.0,
        "Ausbildung": "derzeit Berufsausbildung",
    }])
    params = default_params()
    params["azubi"].update(active=True, retention_rate=1.0)
    positions = {"100": [training], "_by_index": {0: training, 1: regular}}
    result = run_forecast_zugaenge(
        person, pd.Timestamp("2026-01-01"), end_date=pd.Timestamp("2026-12-31"),
        params=params, vacancy_pool=FixedPlanVacancyPool([regular]),
        original_positions=positions)
    events = result["events"]
    conversion = events[events["type"] == "Azubi_Conversion_In"].iloc[0]
    assert conversion["position_index"] == 1
    assert conversion["mak"] == 0.6
    assert result["final_state"].set_index("PersNr").at["100", "_plan_position_index"] == 1

    blocked = run_forecast_zugaenge(
        person, pd.Timestamp("2026-01-01"), end_date=pd.Timestamp("2026-12-31"),
        params=params, vacancy_pool=FixedPlanVacancyPool([]),
        original_positions=positions)
    assert "Azubi_Conversion_In" not in set(blocked["events"]["type"])
    assert "Azubi_Exit" in set(blocked["events"]["type"])


def test_reserved_hire_keeps_original_position_and_soll() -> None:
    positions = pd.DataFrame([{
        "PersNr": pd.NA, "Personalnummer": pd.NA, "Is_Vacant": True,
        "Planstellennr": "P-1", "Organisationseinheit": "Beratung",
        "Planstelle": "Berater", "Soll_FTE": 0.6, "Sollarbeitszeit": 23.4,
        "MAK": 0.0, "MAK_Calculated": 0.0, "BsGrd": 0.0,
        "Personen_MAK_Source": pd.NA, "BsGrd_Source": pd.NA,
    }])
    people = pd.DataFrame([{
        "PersNr": "NH_1", "active": True, "_plan_position_index": 0,
        "mak": 0.6, "Organisationseinheit": "Andere OE",
        "Planstelle": "Andere Stelle", "Eintritt": pd.Timestamp("2026-01-01"),
    }])
    result = _fill_reserved_plan_positions(positions, positions, people)
    assert len(result) == 1
    assert result.at[0, "Planstellennr"] == "P-1"
    assert result.at[0, "Organisationseinheit"] == "Beratung"
    assert result.at[0, "Planstelle"] == "Berater"
    assert result.at[0, "Soll_FTE"] == 0.6
    assert result.at[0, "Sollarbeitszeit"] == 23.4
    assert result.at[0, "PersNr"] == "NH_1"
    assert result.at[0, "Personen_MAK_Source"] == 0.6


def test_reporting_exclusion_cannot_create_hireable_vacancy() -> None:
    base = pd.Timestamp("2025-12-31")
    positions = pd.DataFrame([
        {"PersNr": pd.NA, "Is_Vacant": True, "Is_Excluded": True,
         "Is_Plan_Vacant": False, "Soll_FTE": 1.0,
         "Kürzel OrgEinheit": "9910", "Planstelle": "Bankkaufleute"},
        {"PersNr": pd.NA, "Is_Vacant": True, "Is_Excluded": True,
         "Is_Plan_Vacant": True, "Soll_FTE": 1.0,
         "Kürzel OrgEinheit": "9910", "Planstelle": "Bankkaufleute"},
    ])
    vacancies = available_position_vacancies(
        positions, pd.DataFrame(), base, include_report_excluded=True)
    assert [item["position_index"] for item in vacancies] == [1]


def test_zero_soll_only_eligible_for_dedicated_training_position() -> None:
    base = pd.Timestamp("2025-12-31")
    positions = pd.DataFrame([
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 0.0,
         "Kürzel OrgEinheit": "9910", "Planstelle": "Ausbildung"},
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 0.0,
         "Kürzel OrgEinheit": "1000", "Planstelle": "Beratung"},
    ])
    vacancies = available_position_vacancies(
        positions, pd.DataFrame(), base, include_report_excluded=True)
    assert [row["position_index"] for row in vacancies] == [0]
    assert vacancy_matches_accession(vacancies[0], "azubi")
    assert not vacancy_matches_accession(vacancies[0], "regular")


def test_orgunit_hires_only_within_selected_unit() -> None:
    base = pd.Timestamp("2025-12-31")
    vacancies = [
        {"date": base, "position_index": 0, "org_code": "1000",
         "org_unit": "A", "planstelle": "Berater", "soll_mak": 1.0},
        {"date": base, "position_index": 1, "org_code": "1001",
         "org_unit": "B", "planstelle": "Berater", "soll_mak": 0.5},
    ]
    params = default_params()
    params["new_hires"].update(
        active=True, count_per_year=24, strategy="OrgUnit", target_org_unit="B")
    result = run_forecast_zugaenge(
        pd.DataFrame([{"PersNr": "100", "active": True,
                       "Jobfamily": "Angestellte", "Organisationseinheit": "A",
                       "Eintritt": pd.Timestamp("2020-01-01")}]),
        pd.Timestamp("2026-01-01"), end_date=pd.Timestamp("2026-12-31"),
        params=params, vacancy_pool=FixedPlanVacancyPool(vacancies))
    hires = result["events"][result["events"]["type"] == "New_Hire"]
    assert len(hires) == 1
    assert hires.iloc[0]["position_index"] == 1
    assert hires.iloc[0]["mak"] == 0.5


def test_takeover_vacates_training_row_and_fills_regular_row() -> None:
    positions = pd.DataFrame([
        {"PersNr": "100", "Is_Vacant": False, "Is_Plan_Vacant": False,
         "Planstellennr": "AZ-1", "Organisationseinheit": "Ausbildung",
         "Planstelle": "Bankkaufleute", "Soll_FTE": 1.0},
        {"PersNr": pd.NA, "Is_Vacant": True, "Is_Plan_Vacant": True,
         "Planstellennr": "REG-1", "Organisationseinheit": "Beratung",
         "Planstelle": "Berater", "Soll_FTE": 0.6},
    ])
    people = pd.DataFrame([{
        "PersNr": "100", "active": True, "_plan_position_index": 1,
        "_relocated_from_position_indices": [0], "mak": 0.6,
    }])
    result = _fill_reserved_plan_positions(positions, positions, people)
    assert len(result) == 2
    assert bool(result.at[0, "Is_Plan_Vacant"])
    assert pd.isna(result.at[0, "PersNr"])
    assert not bool(result.at[1, "Is_Plan_Vacant"])
    assert result.at[1, "PersNr"] == "100"
    assert result["Planstellennr"].tolist() == ["AZ-1", "REG-1"]
    assert result["Soll_FTE"].tolist() == [1.0, 0.6]


def test_earlier_regular_entry_wins_and_tie_favors_takeover() -> None:
    start, end = pd.Timestamp("2026-08-01"), pd.Timestamp("2026-08-31")
    training = {"date": start, "position_index": 0, "org_code": "9910",
                "org_unit": "Ausbildung", "planstelle": "Ausbildung", "soll_mak": 1.0}
    regular = {"date": start, "position_index": 1, "org_code": "1000",
               "org_unit": "Beratung", "planstelle": "Berater", "soll_mak": 0.6}
    positions = {"100": [training], "_by_index": {0: training, 1: regular}}
    params = default_params()
    params["azubi"].update(active=True, retention_rate=1.0)
    params["new_hires"].update(active=True, count_per_year=12)

    def run(graduation: pd.Timestamp, seed: int) -> pd.DataFrame:
        params["random_seed"] = seed
        person = pd.DataFrame([{
            "PersNr": "100", "active": True, "Jobfamily": "Azubi",
            "TrfGr": "TVAöD", "Organisationseinheit": "Ausbildung",
            "Eintritt": pd.Timestamp("2023-08-01"),
            "GraduationDate": graduation, "GraduationModus": "Takeover",
            "mak": 0.0, "Ausbildung": "derzeit Berufsausbildung",
        }])
        return run_forecast_zugaenge(
            person, start, end_date=end, params=params,
            vacancy_pool=FixedPlanVacancyPool([regular]),
            original_positions=positions)["events"]

    seed, hire_date = next(
        (seed, date) for seed in range(100)
        for date in run(pd.Timestamp("2026-09-01"), seed)
        .loc[lambda frame: frame["type"].eq("New_Hire"), "date"]
        if start + pd.Timedelta(days=2) <= date <= end - pd.Timedelta(days=2)
    )
    earlier_takeover = run(hire_date - pd.Timedelta(days=1), seed)
    tied = run(hire_date, seed)
    earlier_hire = run(hire_date + pd.Timedelta(days=1), seed)
    assert "Azubi_Conversion_In" in set(earlier_takeover["type"])
    assert "New_Hire" not in set(earlier_takeover["type"])
    assert "Azubi_Conversion_In" in set(tied["type"])
    assert "New_Hire" not in set(tied["type"])
    assert "New_Hire" in set(earlier_hire["type"])
    assert "Azubi_Conversion_In" not in set(earlier_hire["type"])
