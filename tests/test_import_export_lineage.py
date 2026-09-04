from __future__ import annotations

import io

import pandas as pd

from utils.import_export import export_mapping_report, export_to_excel


def _sample_definitions() -> dict:
    return {
        "jobfamilies": {
            "Beratung": {
                "description": "Kundenberatung",
                "patterns": ["*Berater*"],
                "manual_assignments": ["Senior Berater/in"],
                "min_qualification": "Ausbildung",
                "color": "#0088DE",
            },
            "IT": {
                "description": "IT",
                "patterns": ["*Admin*"],
                "manual_assignments": [],
                "min_qualification": "",
                "color": "#10b981",
            },
        },
        "manual_overrides": {"Spezialrolle": "Beratung"},
        "metadata": {"version": "2.0", "source": "test"},
    }


def test_jobfamily_definition_excel_contains_lineage_report():
    payload = export_to_excel(_sample_definitions())

    assert payload is not None
    workbook = pd.ExcelFile(io.BytesIO(payload.getvalue()))
    assert {"Uebersicht", "Patterns", "Manuelle Zuordnungen", "Metadata", "Lineage_Report"}.issubset(
        set(workbook.sheet_names)
    )

    lineage = pd.read_excel(workbook, sheet_name="Lineage_Report")
    assert lineage["Lineage-ID"].tolist() == ["2-05"]
    assert "Jobfamilies=2" in lineage.loc[0, "Export-Kontext"]
    assert "Overrides=1" in lineage.loc[0, "Export-Kontext"]


def test_mapping_report_excel_contains_lineage_report():
    df = pd.DataFrame(
        [
            {"Planstelle": "Berater/in", "Jobfamily": "Beratung", "Jobfamily_match_type": "pattern"},
            {"Planstelle": "Unbekannt", "Jobfamily": "UNMAPPED", "Jobfamily_match_type": "unmapped"},
        ]
    )
    payload = export_mapping_report(df, _sample_definitions())

    assert payload is not None
    workbook = pd.ExcelFile(io.BytesIO(payload.getvalue()))
    assert {"Mapping Details", "Statistiken", "Lineage_Report"}.issubset(set(workbook.sheet_names))

    lineage = pd.read_excel(workbook, sheet_name="Lineage_Report")
    assert lineage["Lineage-ID"].tolist() == ["2-06"]
    assert "Zeilen=2" in lineage.loc[0, "Export-Kontext"]
    assert "Nicht zugeordnet=1" in lineage.loc[0, "Export-Kontext"]
