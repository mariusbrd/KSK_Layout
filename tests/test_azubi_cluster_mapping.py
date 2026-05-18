import pytest
import pandas as pd
import numpy as np
from zugaenge.forecast import _simulate_azubis, PeriodInfo

def test_simulate_azubis_jf_cluster_mapping():
    """
    Verify that Azubi takeovers receive the correct JF-Cluster
    from the provided mapping param.
    """
    # 1. Setup mock Azubis
    df_snapshot = pd.DataFrame([
        {
            "PersNr": "AZ_TEST", 
            "Jobfamily": "Azubi", 
            "TrfGr": "TVA", 
            "Organisationseinheit": "Source", 
            "Eintritt": pd.Timestamp("2022-08-01"), 
            "active": True, 
            "mak": 0.0,
            "OE-Cluster": "OriginalOECluster",
            "JF-Cluster": "OriginalJFCluster" # Should be overwritten on takeover
        }
    ]).set_index("PersNr", drop=False)
    
    # 2. Params with Matrix + Mapping
    params = {
        "azubi": {
            "active": True,
            "retention_rate": 1.0,
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {"TargetJF": 1.0},
            "jf_to_cluster_map": {"TargetJF": "MappedSuccessCluster"},
            "duration_years": 3.0,
            "azubi_conversion_month": 8
        }
    }
    
    period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
    rng = np.random.default_rng(42)
    events = []
    
    # 3. Simulate
    _simulate_azubis(df_snapshot, params, period, rng, events, ["Source"])
    
    # 4. Verify
    in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
    assert len(in_events) == 1
    ev = in_events[0]
    
    assert ev["Jobfamily"] == "TargetJF"
    assert ev["JF-Cluster"] == "MappedSuccessCluster", f"Expected MappedSuccessCluster, got {ev['JF-Cluster']}"
    assert ev["OE-Cluster"] == "OriginalOECluster" # Inherited from AZ_TEST


def test_simulate_azubis_orgunit_mode_uses_explicit_orgunit_cluster_map():
    df_snapshot = pd.DataFrame([
        {
            "PersNr": "AZ_TEST",
            "Jobfamily": "Azubi",
            "TrfGr": "TVA",
            "Organisationseinheit": "Source",
            "Eintritt": pd.Timestamp("2022-08-01"),
            "active": True,
            "mak": 0.0,
            "OE-Cluster": "OriginalOECluster",
            "JF-Cluster": "OriginalJFCluster",
        }
    ]).set_index("PersNr", drop=False)

    params = {
        "azubi": {
            "active": True,
            "retention_rate": 1.0,
            "use_takeover_matrix": True,
            "takeover_dimension": "OrgUnit",
            "takeover_matrix": {"Target OE": 1.0},
            "jf_to_cluster_map": {},
            "duration_years": 3.0,
            "azubi_conversion_month": 8,
        },
        "org_unit_to_cluster_map": {"Target OE": "Mapped OE Cluster"},
    }

    period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
    rng = np.random.default_rng(42)
    events = []

    _simulate_azubis(df_snapshot, params, period, rng, events, ["Target OE"])

    in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
    assert len(in_events) == 1
    ev = in_events[0]

    assert ev["org_unit"] == "Target OE"
    assert ev["OE-Cluster"] == "Mapped OE Cluster"
    assert ev["JF-Cluster"] == "Sonstiges"
