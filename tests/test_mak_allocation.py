from __future__ import annotations

import pandas as pd

from dataloader.mak_allocation import (
    apply_person_mak_allocation,
    build_mak_allocation_validation_summary,
)


def test_single_person_single_position_reports_person_mak() -> None:
    df = pd.DataFrame(
        {
            "PersNr": ["0001"],
            "BsGrd": [100.0],
            "Sollarbeitszeit": [39.0],
            "MAK_Calculated": [1.0],
            "Total_Cost_Year": [60000.0],
            "Is_Vacant": [False],
        }
    )

    out = apply_person_mak_allocation(df)

    assert out["MAK_Technical_Uncorrected"].sum() == 1.0
    assert out["MAK_Reporting"].sum() == 1.0
    assert out["Allocation_Weight"].sum() == 1.0
    assert out["MAK_Allocation_Flag"].iloc[0] == "single_position"


def test_multi_position_uses_planstellen_soll_as_weight_without_increasing_person_mak() -> None:
    df = pd.DataFrame(
        {
            "PersNr": ["0001", "0001"],
            "BsGrd": [100.0, 100.0],
            "Sollarbeitszeit": [19.5, 19.5],
            "MAK_Calculated": [1.0, 1.0],
            "Total_Cost_Year": [60000.0, 60000.0],
            "Planstelle": ["A", "B"],
            "Is_Vacant": [False, False],
        }
    )

    out = apply_person_mak_allocation(df)

    assert out["MAK_Technical_Uncorrected"].sum() == 2.0
    assert out["MAK_Reporting"].tolist() == [0.5, 0.5]
    assert out["MAK_Reporting"].sum() == 1.0
    assert out["MAK_Adjustment_Delta"].sum() == 1.0
    assert set(out["MAK_Allocation_Flag"]) == {"exception_required_mak_gt_1"}


def test_multi_position_without_planstellen_soll_uses_equal_weight() -> None:
    df = pd.DataFrame(
        {
            "PersNr": ["0001", "0001"],
            "BsGrd": [100.0, 100.0],
            "MAK_Calculated": [1.0, 1.0],
            "Planstelle": ["A", "B"],
            "Is_Vacant": [False, False],
        }
    )

    out = apply_person_mak_allocation(df)

    assert out["Allocation_Weight"].tolist() == [0.5, 0.5]
    assert out["MAK_Reporting"].sum() == 1.0


def test_personen_mak_source_overrides_snapshot_bsgrd_and_existing_personen_mak() -> None:
    df = pd.DataFrame(
        {
            "PersNr": ["0001"],
            "Personen_MAK_Source": [1.0],
            "BsGrd_Source": [100.0],
            "BsGrd": [200.0],
            "Personen_MAK": [2.0],
            "MAK_Calculated": [2.0],
            "Is_Vacant": [False],
        }
    )

    out = apply_person_mak_allocation(df)

    assert out["Personen_MAK"].iloc[0] == 1.0
    assert out["person_mak_source"].iloc[0] == "Personen_MAK_Source"
    assert out["MAK_Reporting"].iloc[0] == 1.0


def test_multi_position_part_time_sums_to_person_mak() -> None:
    df = pd.DataFrame(
        {
            "PersNr": ["0001", "0001"],
            "BsGrd": [80.0, 80.0],
            "Sollarbeitszeit": [30.0, 10.0],
            "MAK_Calculated": [0.8, 0.8],
            "Is_Vacant": [False, False],
        }
    )

    out = apply_person_mak_allocation(df)

    assert round(float(out["MAK_Reporting"].sum()), 6) == 0.8
    assert round(float(out["Allocation_Weight"].sum()), 6) == 1.0


def test_known_15_case_pattern_keeps_technical_and_reports_person_capacity() -> None:
    cases = [f"{idx:06d}" for idx in range(1, 16)]
    rows = []
    for persnr in cases:
        rows.extend(
            [
                {"PersNr": persnr, "BsGrd": 100.0, "MAK_Calculated": 1.0, "Planstelle": "A", "Is_Vacant": False},
                {"PersNr": persnr, "BsGrd": 100.0, "MAK_Calculated": 1.0, "Planstelle": "B", "Is_Vacant": False},
            ]
        )

    out = apply_person_mak_allocation(pd.DataFrame(rows))

    assert out["MAK_Technical_Uncorrected"].sum() == 30.0
    assert out["MAK_Reporting"].sum() == 15.0
    assert out["MAK_Adjustment_Delta"].sum() == 15.0


def test_fuehrung_vertrieb_synthetic_summary_moves_from_29_to_21() -> None:
    rows = []
    for idx in range(8):
        rows.extend(
            [
                {
                    "PersNr": f"FV{idx:02d}",
                    "Jobfamily": "Führung Vertrieb",
                    "BsGrd": 100.0,
                    "MAK_Calculated": 1.0,
                    "Planstelle": "A",
                    "Is_Vacant": False,
                },
                {
                    "PersNr": f"FV{idx:02d}",
                    "Jobfamily": "Führung Vertrieb",
                    "BsGrd": 100.0,
                    "MAK_Calculated": 1.0,
                    "Planstelle": "B",
                    "Is_Vacant": False,
                },
            ]
        )
    for idx in range(15):
        mak = 0.0 if idx >= 13 else 1.0
        rows.append(
            {
                "PersNr": f"FV_SINGLE{idx:02d}",
                "Jobfamily": "Führung Vertrieb",
                "BsGrd": mak * 100.0,
                "MAK_Calculated": mak,
                "Planstelle": "A",
                "Is_Vacant": False,
            }
        )

    out = apply_person_mak_allocation(pd.DataFrame(rows))
    validation = build_mak_allocation_validation_summary(out).set_index("Check")

    assert out["PersNr"].nunique() == 23
    assert out["MAK_Technical_Uncorrected"].sum() == 29.0
    assert out["MAK_Reporting"].sum() == 21.0
    assert validation.loc["Führung_Vertrieb_Adjustment", "Wert"] == 8.0
