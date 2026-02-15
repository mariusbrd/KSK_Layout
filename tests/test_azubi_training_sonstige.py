import pytest
import pandas as pd
import numpy as np
from zugaenge.forecast import _simulate_azubis, _simulate_new_azubis, PeriodInfo

def test_simulate_new_azubis_sets_sonstige():
    """Verify that newly hired Azubis get 'Sonstige' Job Family."""
    df_snapshot = pd.DataFrame(columns=["PersNr", "Organisationseinheit", "Jobfamily", "TrfGr", "active"])
    
    params = {
        "azubi": {
            "new_cases_per_year": 12,
            "duration_years": 3,
            "retention_rate": 1.0,
            "strategy": "Random",
            "jf_to_cluster_map": {"Sonstige": "Cluster_Sonstige"}
        }
    }
    
    period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
    rng = np.random.default_rng(42)
    events = []
    
    # 1. Simulate Hires
    _simulate_new_azubis(df_snapshot, params, period, rng, events, ["Source"], num_cases=1)
    
    # 2. Verify
    hire_events = [e for e in events if e["type"] == "Azubi_Hire"]
    assert len(hire_events) == 1
    ev = hire_events[0]
    assert ev["Jobfamily"] == "Sonstige"
    assert ev["JF-Cluster"] == "Cluster_Sonstige"

def test_simulate_azubis_baseline_sets_sonstige():
    """Verify that baseline Azubis are labeled 'Sonstige' during training."""
    df_snapshot = pd.DataFrame([
        {
            "PersNr": "AZ_TEST",
            "Organisationseinheit": "Source",
            "Jobfamily": "Azubi",
            "TrfGr": "TVAöD",
            "active": True,
            "Eintritt": pd.Timestamp("2024-08-01"),
            "mak": 0.0
        }
    ]).set_index("PersNr")
    
    params = {
        "azubi": {
            "retention_rate": 1.0,
            "duration_years": 3.0,
            "strategy": "Random"
        }
    }
    
    # Period DOES NOT contain graduation, so they stay in training
    period = PeriodInfo(pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-31"), "Jan 2025")
    rng = np.random.default_rng(42)
    events = []
    
    # 1. Simulate
    # Note: _simulate_azubis modifies df_state inplace for labels
    _simulate_azubis(df_snapshot, params, period, rng, events, ["Source"])
    
    # 2. Verify state change
    assert df_snapshot.loc["AZ_TEST", "Jobfamily"] == "Sonstige"

def test_takeover_matrix_regression():
    """Ensure matrix still overrides 'Sonstige' during takeover."""
    df_snapshot = pd.DataFrame([
        {
            "PersNr": "AZ_GRAD",
            "Organisationseinheit": "Source",
            "Jobfamily": "Azubi",
            "TrfGr": "TVAöD",
            "active": True,
            "Eintritt": pd.Timestamp("2022-08-01"),
            "GraduationDate": pd.Timestamp("2025-08-15"),
            "mak": 0.0
        }
    ]).set_index("PersNr")
    
    params = {
        "azubi": {
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {"TargetJF": 100},
            "retention_rate": 1.0,
            "duration_years": 3.0,
            "strategy": "Random",
            "jf_to_cluster_map": {"TargetJF": "MappedCluster"}
        }
    }
    
    period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
    rng = np.random.default_rng(42)
    events = []
    
    _simulate_azubis(df_snapshot, params, period, rng, events, ["Source"])
    
    # Verify In-Event
    in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
    assert len(in_events) == 1
    ev = in_events[0]
    assert ev["Jobfamily"] == "TargetJF" # Matrix wins
    assert ev["JF-Cluster"] == "MappedCluster"
