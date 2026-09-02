from __future__ import annotations

import io

import pandas as pd

from utils.compact_simulation_export import (
    build_compact_simulation_export_bytes,
    build_jobfamily_demographics,
    build_jobfamily_summary,
)


def _sample_prepared_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "PersNr": "1",
                "Is_Vacant": False,
                "Jobfamily": "Beratung",
                "Geschlecht": "W",
                "Alterskohorte": "30-39",
                "Beschäftigungsstatus": "Aktiv",
                "Beschäftigungsgrad_Kat": "Vollzeit",
                "Ausbildung": "Bachelor",
                "Betriebszugehörigkeit_Bin": "0-5 Jahre",
                "ATZ_Status": "Kein ATZ",
                "MAK_Calculated": 0.4,
                "Total_Cost_Year": 40000.0,
            },
            {
                "PersNr": "1",
                "Is_Vacant": False,
                "Jobfamily": "Beratung",
                "Geschlecht": "W",
                "Alterskohorte": "30-39",
                "Beschäftigungsstatus": "Aktiv",
                "Beschäftigungsgrad_Kat": "Vollzeit",
                "Ausbildung": "Bachelor",
                "Betriebszugehörigkeit_Bin": "0-5 Jahre",
                "ATZ_Status": "Kein ATZ",
                "MAK_Calculated": 0.6,
                "Total_Cost_Year": 60000.0,
            },
            {
                "PersNr": "2",
                "Is_Vacant": False,
                "Jobfamily": "IT",
                "Geschlecht": "M",
                "Alterskohorte": "40-49",
                "Beschäftigungsstatus": "Aktiv",
                "Beschäftigungsgrad_Kat": "Teilzeit",
                "Ausbildung": "Master",
                "Betriebszugehörigkeit_Bin": "5-10 Jahre",
                "ATZ_Status": "Kein ATZ",
                "MAK_Calculated": 0.8,
                "Total_Cost_Year": 80000.0,
            },
            {
                "PersNr": None,
                "Is_Vacant": True,
                "Jobfamily": "IT",
                "Geschlecht": None,
                "Alterskohorte": None,
                "Beschäftigungsstatus": None,
                "Beschäftigungsgrad_Kat": None,
                "Ausbildung": None,
                "Betriebszugehörigkeit_Bin": None,
                "ATZ_Status": None,
                "MAK_Calculated": 1.0,
                "Total_Cost_Year": 90000.0,
            },
        ]
    )


def test_jobfamily_summary_uses_unique_employee_level() -> None:
    summary = build_jobfamily_summary(_sample_prepared_df())

    beratung = summary.loc[summary["Jobfamily"].eq("Beratung")].iloc[0]
    it = summary.loc[summary["Jobfamily"].eq("IT")].iloc[0]

    assert beratung["Köpfe"] == 1
    assert beratung["MAK"] == 1.0
    assert beratung["EUR"] == 100000.0
    assert it["Köpfe"] == 1
    assert it["MAK"] == 0.8
    assert it["EUR"] == 80000.0


def test_jobfamily_demographics_breaks_down_heads_mak_and_eur() -> None:
    demographics = build_jobfamily_demographics(_sample_prepared_df(), dimensions=["Geschlecht"])

    beratung_w = demographics[
        demographics["Jobfamily"].eq("Beratung")
        & demographics["Dimension"].eq("Geschlecht")
        & demographics["Ausprägung"].eq("W")
    ].iloc[0]

    assert beratung_w["Köpfe"] == 1
    assert beratung_w["MAK"] == 1.0
    assert beratung_w["EUR"] == 100000.0
    assert beratung_w["Köpfe Anteil in Jobfamily"] == 1.0


def test_jobfamily_demographics_handles_missing_categorical_values() -> None:
    prepared_df = _sample_prepared_df()
    prepared_df["DimCat"] = pd.Categorical(
        ["A", "A", None, None],
        categories=["A", "B"],
        ordered=True,
    )

    demographics = build_jobfamily_demographics(prepared_df, dimensions=["DimCat"])

    unknown_it = demographics[
        demographics["Jobfamily"].eq("IT")
        & demographics["Dimension"].eq("DimCat")
        & demographics.iloc[:, 2].eq("(unbekannt)")
    ].iloc[0]

    assert unknown_it.iloc[3] == 1
    assert unknown_it["MAK"] == 0.8


def test_export_workbook_contains_parameters_and_result_sheets() -> None:
    payload = build_compact_simulation_export_bytes(
        prepared_df=_sample_prepared_df(),
        abgaenge_params={
            "atz": {"new_atz_rate": 0.07},
            "quit": {"quit_rate_base": 0.03},
        },
        zugaenge_params={"azubi": {"retention_rate": 0.8}},
        metadata={
            "base_date": pd.Timestamp("2026-01-01"),
            "target_date": pd.Timestamp("2028-01-01"),
            "used_simulation": True,
            "abgaenge_events": 4,
            "zugaenge_events": 2,
        },
    )

    workbook = pd.ExcelFile(io.BytesIO(payload))
    assert set(workbook.sheet_names) >= {
        "Parameter",
        "Jobfamily_Summary",
        "Jobfamily_Demografie",
        "MAK_Personen_Audit",
        "Validation_Checks",
    }

    parameter_df = pd.read_excel(workbook, sheet_name="Parameter")
    summary_df = pd.read_excel(workbook, sheet_name="Jobfamily_Summary")

    assert "quit.quit_rate_base" in set(parameter_df["Parameter"])
    assert "atz.new_atz_rate" in set(parameter_df["Parameter"])
    assert "azubi.retention_rate" in set(parameter_df["Parameter"])
    assert set(summary_df["Jobfamily"]) == {"Beratung", "IT"}


def test_export_workbook_contains_ordered_management_sheets() -> None:
    status_quo_df = _sample_prepared_df().copy()
    mitarbeiter_df = pd.DataFrame(
        [
            {"PersNr": "1", "Eintritt": "2020-01-01", "Austritt": None, "BsGrd": 100, "Vertragsart": "Unbefristet"},
            {"PersNr": "2", "Eintritt": "2020-01-01", "Austritt": None, "BsGrd": 80, "Vertragsart": "Unbefristet"},
            {"PersNr": "3", "Eintritt": "2020-01-01", "Austritt": None, "BsGrd": 100, "Vertragsart": "Ausbildung"},
        ]
    )
    planstellen_df = pd.DataFrame(
        [
            {"Personalnummer": "1", "Planstelle": "Beratung"},
            {"Personalnummer": "2", "Planstelle": "IT"},
            {"Personalnummer": "3", "Planstelle": "Bankkaufleute"},
        ]
    )

    payload = build_compact_simulation_export_bytes(
        prepared_df=_sample_prepared_df(),
        abgaenge_params={},
        zugaenge_params={},
        metadata={"base_date": pd.Timestamp("2026-01-01"), "target_date": pd.Timestamp("2028-01-01")},
        status_quo_df=status_quo_df,
        status_quo_date=pd.Timestamp("2026-01-01"),
        status_quo_mitarbeiter_df=mitarbeiter_df,
        status_quo_planstellen_df=planstellen_df,
    )

    workbook = pd.ExcelFile(io.BytesIO(payload))
    expected_prefix = [
        "00_Readme",
        "01_Executive_Summary",
        "02_Scope_Check",
        "03_JF_Vergleich",
        "04_Full_Workforce_Vgl",
        "05_Demografie_JF",
        "06_Demografie_Full",
        "07_Demografie_Auff",
        "08_MAK_Allocation",
        "09_Missing_Persons",
        "10_Sonstiges_Audit",
        "11_Azubi_Audit",
        "12_65plus_Audit",
        "13_Validation",
    ]

    assert workbook.sheet_names[: len(expected_prefix)] == expected_prefix
    missing_df = pd.read_excel(workbook, sheet_name="09_Missing_Persons")
    full_df = pd.read_excel(workbook, sheet_name="04_Full_Workforce_Vgl")

    assert "MAK_Reporting" in pd.read_excel(workbook, sheet_name="08_MAK_Allocation").columns
    assert len(missing_df) == 1
    assert int(full_df["Köpfe_StatusQuo_Full"].sum()) == 3


def test_export_workbook_contains_jobfamily_profile_and_attrition_person_list() -> None:
    audit_tables = {
        "Abgaenge_Events_Raw": pd.DataFrame(
            [
                {
                    "persnr": "1",
                    "event_date": pd.Timestamp("2027-03-31"),
                    "reason_code": "QUIT",
                    "reason_label": "Kündigung",
                    "headcount_change": -1,
                    "mak_change": -1.0,
                    "Jobfamily": "Beratung",
                    "Organisationseinheit": "Markt",
                    "age": 37.2,
                    "tenure": 7.1,
                }
            ]
        ),
        "MAK_Abgaenge_Check": pd.DataFrame(
            [
                {
                    "PersNr": "1",
                    "Jobfamily_before": "Beratung",
                    "MAK_before": 1.0,
                    "MAK_after_abgang": 0.0,
                    "row_removed_or_deactivated": True,
                }
            ]
        ),
    }

    payload = build_compact_simulation_export_bytes(
        prepared_df=_sample_prepared_df(),
        abgaenge_params={},
        zugaenge_params={},
        metadata={"base_date": pd.Timestamp("2026-01-01"), "target_date": pd.Timestamp("2028-01-01")},
        status_quo_df=_sample_prepared_df(),
        status_quo_date=pd.Timestamp("2026-01-01"),
        audit_tables=audit_tables,
    )

    workbook = pd.ExcelFile(io.BytesIO(payload))

    assert {
        "14_JF_Profil_Vor_Nach",
        "15_Abgaenge_Personenliste",
        "16_Abgaenge_Grund",
        "17_Abgaenge_Grund_Jahr",
        "18_Abgaenge_Grund_JF",
    }.issubset(set(workbook.sheet_names))

    person_list = pd.read_excel(workbook, sheet_name="15_Abgaenge_Personenliste")
    profile = pd.read_excel(workbook, sheet_name="14_JF_Profil_Vor_Nach")

    assert str(person_list.loc[0, "PersNr"]) == "1"
    assert person_list.loc[0, "Jobfamily_before"] == "Beratung"
    assert person_list.loc[0, "Abgangsgrund"] == "Kündigung"
    assert "GESAMT" in set(profile["Jobfamily"])
