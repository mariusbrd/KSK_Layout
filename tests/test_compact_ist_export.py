from __future__ import annotations

import io

import pandas as pd

from utils.compact_ist_export import (
    build_company_demographics,
    build_company_summary,
    build_compact_ist_demographics_export_bytes,
)


def _sample_prepared_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "PersNr": "1",
                "Is_Vacant": False,
                "Jobfamily": "Beratung",
                "Geschlecht": "W",
                "MAK_Reporting": 0.4,
                "EUR_Reporting": 40000.0,
            },
            {
                "PersNr": "1",
                "Is_Vacant": False,
                "Jobfamily": "Beratung",
                "Geschlecht": "W",
                "MAK_Reporting": 0.6,
                "EUR_Reporting": 60000.0,
            },
            {
                "PersNr": "2",
                "Is_Vacant": False,
                "Jobfamily": "IT",
                "Geschlecht": "M",
                "MAK_Reporting": 0.8,
                "EUR_Reporting": 80000.0,
            },
            {
                "PersNr": None,
                "Is_Vacant": True,
                "Jobfamily": "IT",
                "Geschlecht": None,
                "MAK_Reporting": 1.0,
                "EUR_Reporting": 90000.0,
            },
        ]
    )


def test_company_summary_uses_unique_active_employee_scope() -> None:
    summary = build_company_summary(_sample_prepared_df())

    values = dict(zip(summary["Kennzahl"], summary["Wert"]))

    assert values["Köpfe"] == 2
    assert values["MAK"] == 1.8
    assert values["EUR"] == 180000.0


def test_company_demographics_breaks_down_total_company() -> None:
    demographics = build_company_demographics(_sample_prepared_df(), dimensions=["Geschlecht"])

    female = demographics[
        demographics["Dimension"].eq("Geschlecht")
        & demographics["Ausprägung"].eq("W")
    ].iloc[0]

    assert female["Scope"] == "Unternehmen"
    assert female["Köpfe"] == 1
    assert female["MAK"] == 1.0
    assert female["EUR"] == 100000.0
    assert female["Köpfe Anteil Unternehmen"] == 0.5


def test_compact_ist_export_contains_concept_sheets() -> None:
    payload = build_compact_ist_demographics_export_bytes(
        prepared_df=_sample_prepared_df(),
        stichtag=pd.Timestamp("2026-01-01"),
        dimensions=["Geschlecht"],
    )

    workbook = pd.ExcelFile(io.BytesIO(payload))

    assert workbook.sheet_names == [
        "00_Dokumentation",
        "01_Unternehmen",
        "02_Unternehmen_Demografie",
        "03_Jobfamily_Summary",
        "04_Jobfamily_Demografie",
        "Lineage_Report",
        "Input_Lineage",
        "Transformations_Lineage",
    ]
    documentation = pd.read_excel(workbook, sheet_name="00_Dokumentation")
    company = pd.read_excel(workbook, sheet_name="01_Unternehmen")
    jobfamilies = pd.read_excel(workbook, sheet_name="03_Jobfamily_Summary")
    lineage = pd.read_excel(workbook, sheet_name="Lineage_Report")
    input_lineage = pd.read_excel(workbook, sheet_name="Input_Lineage")
    transformation_lineage = pd.read_excel(workbook, sheet_name="Transformations_Lineage")

    assert "Kompakt IST Demografie" in set(documentation["Wert"])
    assert set(company["Kennzahl"]) >= {"Köpfe", "MAK"}
    assert "EUR" not in set(company["Kennzahl"])
    assert not any("EUR" in col or "Kosten" in col for col in jobfamilies.columns)
    assert set(jobfamilies["Jobfamily"]) == {"Beratung", "IT"}
    assert lineage["Lineage-ID"].tolist() == ["8-14", "8-15"]
    assert {"Input-Rolle", "Datei", "Spalten"}.issubset(set(input_lineage.columns))
    assert "Kennzahl je Kategorie zusammenfassen" in set(transformation_lineage["Schritt"])

