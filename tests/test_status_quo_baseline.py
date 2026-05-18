from __future__ import annotations

import pandas as pd

from utils.status_quo_baseline import (
    build_forecast_vs_status_quo_jobfamily,
    build_status_quo_jobfamily_summary,
    build_status_quo_snapshot,
)


def test_status_quo_snapshot_uses_mak_reporting_and_headcount_unique_persnr() -> None:
    df = pd.DataFrame(
        {
            "PersNr": ["0001", "0001", "0002"],
            "Jobfamily": ["A", "A", "B"],
            "BsGrd_Source": [100.0, 100.0, 80.0],
            "Personen_MAK_Source": [1.0, 1.0, 0.8],
            "BsGrd": [100.0, 100.0, 80.0],
            "MAK_Calculated": [1.0, 1.0, 0.8],
            "Total_Cost_Year": [60000.0, 60000.0, 40000.0],
            "Sollarbeitszeit": [19.5, 19.5, 39.0],
            "Is_Vacant": [False, False, False],
        }
    )

    snapshot = build_status_quo_snapshot(df, pd.Timestamp("2025-12-31"))
    summary = build_status_quo_jobfamily_summary(snapshot, pd.Timestamp("2025-12-31"))
    total = summary[summary["Jobfamily"] == "Gesamt"].iloc[0]

    assert snapshot["is_status_quo"].all()
    assert total["Köpfe_StatusQuo"] == 2
    assert total["MAK_StatusQuo"] == 1.8
    assert total["Technical_MAK_StatusQuo"] == 2.8
    assert total["MAK_Adjustment_StatusQuo"] == 1.0


def test_forecast_vs_status_quo_handles_missing_jobfamilies_and_deltas() -> None:
    status = pd.DataFrame(
        {
            "PersNr": ["0001", "0002"],
            "Jobfamily": ["A", "Only_Status"],
            "MAK_Reporting": [1.0, 0.5],
            "EUR_Reporting": [100.0, 50.0],
            "MAK_Technical_Uncorrected": [1.0, 0.5],
            "MAK_Adjustment_Delta": [0.0, 0.0],
            "Allocation_Weight": [1.0, 1.0],
            "Anzahl_Planstellen": [1, 1],
            "Personen_MAK_Source": [1.0, 0.5],
            "Is_Vacant": [False, False],
        }
    )
    forecast = pd.DataFrame(
        {
            "PersNr": ["0001", "0003", "0004"],
            "Jobfamily": ["A", "Only_Forecast", "Only_Forecast"],
            "MAK_Reporting": [1.2, 1.0, 0.8],
            "EUR_Reporting": [120.0, 100.0, 80.0],
            "Is_Vacant": [False, False, False],
        }
    )

    comparison = build_forecast_vs_status_quo_jobfamily(status, forecast, pd.Timestamp("2025-12-31"))
    by_jf = comparison.set_index("Jobfamily")

    assert by_jf.loc["A", "Delta_Köpfe"] == 0
    assert round(float(by_jf.loc["A", "Delta_MAK"]), 6) == 0.2
    assert by_jf.loc["Only_Status", "Köpfe_Forecast"] == 0
    assert by_jf.loc["Only_Forecast", "Köpfe_StatusQuo"] == 0
    assert by_jf.loc["Only_Forecast", "Köpfe_Forecast"] == 2


def test_status_quo_summary_does_not_require_eur_columns() -> None:
    df = pd.DataFrame(
        {
            "PersNr": ["0001"],
            "Jobfamily": ["A"],
            "MAK_Reporting": [1.0],
            "MAK_Technical_Uncorrected": [1.0],
            "Allocation_Weight": [1.0],
            "Is_Vacant": [False],
        }
    )

    summary = build_status_quo_jobfamily_summary(df, pd.Timestamp("2025-12-31"))

    assert float(summary.loc[summary["Jobfamily"] == "A", "EUR_StatusQuo"].iloc[0]) == 0.0
