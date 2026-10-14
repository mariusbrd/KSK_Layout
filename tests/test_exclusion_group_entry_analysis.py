"""Display-only explanation of affected exclusion-group entries."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PAGE = next((ROOT / "pages").glob("*_Deep_Dive_Exklusionsgruppen.py"))
spec = importlib.util.spec_from_file_location("exclusion_entries_page", PAGE)
page = importlib.util.module_from_spec(spec)
page._UNIT_TESTING = True
spec.loader.exec_module(page)


def test_entry_analysis_explains_soll_ist_and_preserved_capacity_without_mutation():
    source = pd.DataFrame({
        "Planstellennr": ["50001903", "50002084", "5001"],
        "Planstelle": ["Zusatz", "Regulär", "Leer"],
        "Organisationseinheit": ["OE A"] * 3,
        "Sollarbeitszeit": [0.01, 39.0, 39.0],
        "Soll_FTE": [0.0, 1.0, 1.0],
        "MAK_Calculated": [1.0, 1.0, 0.0],
        "MAK_Reporting": [0.8, 1.0, 0.0],
        "Is_Excluded": [True, True, False],
        "Is_IST_Preserved_Despite_Exclusion": [True, False, False],
        "Is_Vacant": [False, True, True],
        "BsGrd_Source": [80.0, 100.0, pd.NA],
    })
    before = source.copy(deep=True)
    result = page._build_group_entry_analysis(source, "jobfamily_validation_special_positions")

    assert result.loc[0, "Soll-Typ"] == "Technische Platzhalterstelle (0,01 Std.)"
    assert result.loc[0, "Beschäftigungsstatus"] == "Besetzt – IST trotz Ausschluss erhalten"
    assert result.loc[0, "SOLL-MAK nach Ausschluss"] == 0.0
    assert result.loc[0, "IST-MAK Reporting"] == 0.8
    assert result.loc[1, "Soll-Typ"] == "Reguläre Sollstelle"
    assert "fachlich prüfen" in result.loc[1, "Prüfhinweis"]
    assert result.loc[1, "Beschäftigungsstatus"] == "Ausgeschlossen – IST entfernt"
    assert result.loc[2, "Ausschlusswirkung"] == "Nicht ausgeschlossen"
    pd.testing.assert_frame_equal(source, before)


def test_entry_analysis_handles_missing_reporting_columns():
    source = pd.DataFrame({"Soll_FTE": [0.5], "Is_Vacant": [False]})
    result = page._build_group_entry_analysis(source, "other")
    assert result.loc[0, "IST-MAK Reporting"] == 0.0
    assert result.loc[0, "SOLL-MAK (roh)"] == 0.5
