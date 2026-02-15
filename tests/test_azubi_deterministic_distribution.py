import pytest
import pandas as pd
import numpy as np
from zugaenge.forecast import distribute_deterministic, _simulate_azubis, PeriodInfo

def test_distribute_deterministic_simple():
    # 10 items, 50/50 split
    weights = {"A": 0.5, "B": 0.5}
    result = distribute_deterministic(10, weights)
    assert len(result) == 10
    assert result.count("A") == 5
    assert result.count("B") == 5

def test_distribute_deterministic_remainders():
    # 3 items, 50/50 split -> 1.5 each. 
    # Floor is 1 each, sum is 2. 1 remainder to distribute.
    # Remainders: (A, 0.5), (B, 0.5). 
    # Sorting by remainder (0.5 == 0.5) then key (A < B).
    # Since we sort descending by remainder and then key, A should get the extra one.
    weights = {"A": 0.5, "B": 0.5}
    result = distribute_deterministic(3, weights)
    assert len(result) == 3
    assert result.count("A") == 2
    assert result.count("B") == 1

def test_distribute_deterministic_unbalanced():
    # 10 items, 60/40 split
    weights = {"A": 0.6, "B": 0.4}
    result = distribute_deterministic(10, weights)
    assert result.count("A") == 6
    assert result.count("B") == 4

def test_distribute_deterministic_rounding_edge():
    # 10 items, 33/33/34 split
    weights = {"A": 0.33, "B": 0.33, "C": 0.34}
    result = distribute_deterministic(10, weights)
    # Expected: 3.3, 3.3, 3.4 -> Floors: 3, 3, 3 -> Sum 9 -> Remainder 1.
    # Remainders: (A, 0.3), (B, 0.3), (C, 0.4).
    # C should get the extra one.
    assert result.count("A") == 3
    assert result.count("B") == 3
    assert result.count("C") == 4

def test_distribute_deterministic_stable_sorting():
    # 100 items, many categories
    weights = {f"U{i}": 1.0/17 for i in range(17)}
    result = distribute_deterministic(100, weights)
    assert len(result) == 100
    # 100 / 17 = 5.882...
    # Floor: 5 * 17 = 85.
    # Remainder 15 to distribute. 15 categories get 6, 2 categories get 5.
    counts = [result.count(f"U{i}") for i in range(17)]
    assert sum(counts) == 100
    assert counts.count(6) == 15
    assert counts.count(5) == 2

def test_simulate_azubis_cohort_distribution():
    # Verify that multiple azubis graduating in same month are distributed per matrix
    df_snapshot = pd.DataFrame([
        {"PersNr": f"AZ{i}", "Jobfamily": "Azubi", "TrfGr": "TVA", "Organisationseinheit": "Source", "Eintritt": pd.Timestamp("2022-08-01"), "active": True, "mak": 0.0}
        for i in range(10)
    ]).set_index("PersNr", drop=False)
    
    params = {
        "azubi": {
            "active": True,
            "retention_rate": 1.0,
            "use_takeover_matrix": True,
            "takeover_dimension": "OrgUnit",
            "takeover_matrix": {"UnitA": 0.7, "UnitB": 0.3},
            "duration_years": 3.0,
            "azubi_conversion_month": 8
        },
        "random_seed": 42
    }
    
    period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
    rng = np.random.default_rng(42)
    events = []
    
    _simulate_azubis(df_snapshot, params, period, rng, events, ["UnitA", "UnitB"])
    
    # Check that 10 were taken over
    takeover_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
    assert len(takeover_events) == 10
    
    # Check distribution: 7 in UnitA, 3 in UnitB
    units = [e["org_unit"] for e in takeover_events]
    assert units.count("UnitA") == 7
    assert units.count("UnitB") == 3
