"""Checks that stock/event reconciliation preserves person and group totals."""
from __future__ import annotations

import pandas as pd
import pytest
import numpy as np

from utils.compact_regular_stock import (
    _grouped_stock_metrics_indexed,
    project_regular_group_stock_from_events, project_regular_position_stocks,
    reconcile_regular_year_stock,
)
from utils.org_simulation_reconciliation import grouped_stock_metrics


def test_indexed_grouping_matches_product_on_mixed_position_rows() -> None:
    rng = np.random.default_rng(391)
    for _ in range(20):
        frame = pd.DataFrame({
            "PersNr": rng.choice(["1", "2", "3", "4", None], size=80),
            "Is_Vacant": rng.choice([False, True], size=80, p=[0.8, 0.2]),
            "MAK_Reporting": rng.choice(
                [0.0, 0.1, 0.2, 0.3, 0.6154, 1e-12, np.nan], size=80),
            "Organisationseinheit": rng.choice(["A", "B", "C", None], size=80),
        })
        assert _grouped_stock_metrics_indexed(
            frame, "Organisationseinheit") == grouped_stock_metrics(
                frame, "Organisationseinheit")


def test_reconciliation_identifies_undated_atz_capacity_change() -> None:
    before = pd.DataFrame([
        {"PersNr": "1", "Is_Vacant": False, "MAK_Reporting": 0.0,
         "Organisationseinheit": "A", "ist_atz_fr": True},
        {"PersNr": "2", "Is_Vacant": False, "MAK_Reporting": 0.5,
         "Organisationseinheit": "A", "ist_atz_fr": False},
    ])
    after = before.copy()
    after.loc[0, "MAK_Reporting"] = 0.5
    after.loc[0, "ist_atz_fr"] = False
    events = pd.DataFrame([{
        "persnr": "2", "event_date": pd.Timestamp("2027-03-01"),
        "Organisationseinheit": "A", "MAK-Veränderung": -0.2,
        "Köpfe-Veränderung": 0,
    }])
    after.loc[1, "MAK_Reporting"] = 0.3
    detail = reconcile_regular_year_stock(before, after, events, year=2027)
    by_person = detail.set_index("persnr")
    assert abs(by_person.at["000001", "residual_mak"] - 0.5) < 1e-12
    assert by_person.at["000001", "cause_hint"] == "atz_phase_change"
    assert abs(by_person.at["000002", "residual_mak"]) < 1e-12
    assert abs(detail["residual_mak"].sum() - 0.5) < 1e-12


def test_cross_oe_departure_event_is_not_a_cross_oe_stock_change() -> None:
    before = pd.DataFrame([
        {"PersNr": "1", "Is_Vacant": False, "MAK_Reporting": 0.49,
         "Organisationseinheit": "A"},
        {"PersNr": "1", "Is_Vacant": False, "MAK_Reporting": 0.51,
         "Organisationseinheit": "B"},
    ])
    after = before.copy()
    after["PersNr"] = pd.NA
    after["Is_Vacant"] = True
    after["MAK_Reporting"] = 0.0
    events = pd.DataFrame([{
        "persnr": "1", "event_date": pd.Timestamp("2027-06-01"),
        "Organisationseinheit": "A", "MAK-Veränderung": -1.0,
        "Köpfe-Veränderung": -1, "Ereignisquelle": "departure_events",
    }])
    detail = reconcile_regular_year_stock(before, after, events, year=2027)
    residual = detail.set_index("Organisationseinheit")["residual_mak"]
    assert abs(residual["A"] - 0.51) < 1e-12
    assert abs(residual["B"] + 0.51) < 1e-12
    assert set(detail["cause_hint"]) == {"cross_group_departure_allocation"}
    projected = project_regular_group_stock_from_events(
        before, events, base_date=pd.Timestamp("2026-12-31"),
        target_date=pd.Timestamp("2027-12-31"))
    assert projected.set_index("Organisationseinheit")["MAK"].to_dict() == {
        "A": 0.0, "B": 0.0}


def test_position_projection_rejects_training_accessions() -> None:
    baseline = pd.DataFrame([{
        "PersNr": pd.NA, "Is_Vacant": True, "MAK_Reporting": 0.0,
        "Organisationseinheit": "A", "OE-Cluster": "A",
        "Jobfamily": "A", "JF-Cluster": "A",
    }])
    with pytest.raises(ValueError, match="only NH_ hires"):
        project_regular_position_stocks(
            baseline, baseline, pd.DataFrame(), [{"PersNr": "AZ_1"}],
            base_date=pd.Timestamp("2025-12-31"),
            target_date=pd.Timestamp("2026-12-31"))


def test_dormant_return_on_multiple_positions_requires_reference() -> None:
    baseline = pd.DataFrame([
        {"PersNr": "1", "Is_Vacant": False, "MAK_Reporting": 0.0,
         "Personen_MAK_Source": 0.6,
         "Status kundenindividuell": "Ruhendes Beschäftigungsverhältnis",
         "Organisationseinheit": "A", "OE-Cluster": "A",
         "Jobfamily": "J", "JF-Cluster": "J"},
        {"PersNr": "1", "Is_Vacant": False, "MAK_Reporting": 0.0,
         "Personen_MAK_Source": 0.6,
         "Status kundenindividuell": "Ruhendes Beschäftigungsverhältnis",
         "Organisationseinheit": "B", "OE-Cluster": "B",
         "Jobfamily": "J", "JF-Cluster": "J"},
    ])
    events = pd.DataFrame([{
        "persnr": "1", "event_date": pd.Timestamp("2026-06-30"),
        "reason_code": "RUHEND_RETURN", "headcount_change": 0,
        "mak_change": 0.0,
    }])
    with pytest.raises(ValueError, match="multiple original positions"):
        project_regular_position_stocks(
            baseline, baseline, events, [],
            base_date=pd.Timestamp("2025-12-31"),
            target_date=pd.Timestamp("2026-12-31"))
