from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dataloader import synthetic
from dataloader import soll_ist_koepfe_engine as engine
from dataloader.loader import create_combined_snapshot


def test_synthetic_files_validate_as_business_plausible():
    files = synthetic.generate_all_files()
    report = synthetic.validate_synthetic_files(files)

    assert report["is_valid"], report["issues"]

    diagnostics = report["diagnostics"]
    assert diagnostics["regular_positions"] > 700
    assert diagnostics["matrix_occupied"] > 500
    assert 0.08 < diagnostics["regular_vacancy_share"] < 0.35
    assert diagnostics["exact_match_share"] > 0.30
    assert diagnostics["in_band_share"] > 0.08
    assert diagnostics["overgraded_share"] > 0.08
    assert diagnostics["undergraded_share"] > 0.04
    assert diagnostics["technical_positions_001"] > 80
    assert diagnostics["technical_only_people"] > 10
    assert diagnostics["technical_plus_regular_people"] > 20
    assert diagnostics["regular_no_soll"] > 10
    assert len(diagnostics["soll_grade_distribution"]) >= 6
    assert len(diagnostics["ist_grade_distribution"]) >= 6


def test_synthetic_files_are_snapshot_compatible():
    files = synthetic.generate_all_files()

    snapshot = create_combined_snapshot(
        files["Mitarbeiter"],
        files["Planstellen"],
        files["ATZ"],
        files["Ausbildung"],
        stichtag=pd.Timestamp("2025-12-31"),
    )

    required_columns = {
        "PersNr",
        "Personalnummer",
        "Is_Vacant",
        "Soll_FTE",
        "FTE_assigned",
        "TrfGr",
        "Ausbildung",
        "Total_Cost_Year",
    }
    assert required_columns.issubset(snapshot.columns)
    assert snapshot["PersNr"].notna().sum() > 500
    assert snapshot["Organisationseinheit"].nunique() >= 10
    assert (snapshot["Ist_Azubi"] == True).sum() > 0


def test_synthetic_data_produces_nontrivial_soll_ist_engine_output(monkeypatch):
    files = synthetic.generate_all_files()

    engine._load_raw_source_data_cached.clear()
    engine._load_soll_ist_koepfe_basis_cached.clear()
    monkeypatch.setattr(engine.st, "session_state", {})
    monkeypatch.setattr(
        engine,
        "load_original_data",
        lambda *args, **kwargs: {
            "mitarbeiter": files["Mitarbeiter"],
            "planstellen": files["Planstellen"],
            "atz": files["ATZ"],
            "ausbildung": files["Ausbildung"],
        },
    )
    monkeypatch.setattr(engine, "get_file_signature", lambda path: None)

    result = engine.build_soll_ist_koepfe_result(exclusions={})
    summary = result["summary"]

    assert summary["regular_total"] > 700
    assert summary["matrix_total"] > 650
    assert summary["matrix_occupied"] > 500
    assert summary["matrix_unbesetzt"] > 50
    assert summary["technical_total"] > 80
    assert summary["technical_non9xxx_occupied"] > 20
    assert summary["regular_no_soll_eg_total"] > 10

    pivot = result["pivot"]
    diag_sum = sum(int(pivot.loc[eg, eg]) for eg in pivot.index if eg in pivot.columns)
    occupied_sum = int(pivot.drop(columns=["Unbesetzt", "Nicht gefunden", "Gesamt"], errors="ignore").sum().sum())
    assert occupied_sum > diag_sum
    assert result["no_soll_eg_row"].sum() > 0
