import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from zugaenge.forecast import run_forecast_zugaenge, PeriodInfo, _simulate_azubis

def test_azubi_takeover_matrix_orgunit():
    # Setup
    df_snapshot = pd.DataFrame([
        {
            "PersNr": "AZ1",
            "Jobfamily": "Azubi",
            "TrfGr": "TVA",
            "Organisationseinheit": "SourceUnit",
            "Eintritt": pd.Timestamp("2022-08-01"),
            "active": True,
            "mak": 0.0,
            "OE-Cluster": "SourceCluster"
        },
        {
            "PersNr": "P1",
            "Jobfamily": "Angestellte",
            "TrfGr": "E9",
            "Organisationseinheit": "TargetUnit",
            "Eintritt": pd.Timestamp("2020-01-01"),
            "active": True,
            "mak": 1.0,
            "OE-Cluster": "TargetCluster"
        }
    ]).set_index("PersNr", drop=False)
    
    params = {
        "azubi": {
            "active": True,
            "retention_rate": 1.0,
            "use_takeover_matrix": True,
            "takeover_dimension": "OrgUnit",
            "takeover_matrix": {
                "TargetUnit": 1.0,
                "SourceUnit": 0.0
            }
        },
        "random_seed": 42
    }
    
    # Run simulation for August (takeover month)
    start_date = pd.Timestamp("2025-08-01")
    end_date = pd.Timestamp("2025-08-31")
    
    res = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=start_date,
        end_date=end_date,
        params=params
    )
    
    events = res["events"]
    # We expect Azubi_Conversion_In to have TargetUnit
    takeover_event = events[events["type"] == "Azubi_Conversion_In"]
    assert not takeover_event.empty
    assert takeover_event.iloc[0]["org_unit"] == "TargetUnit"

def test_azubi_takeover_matrix_jobfamily():
    # Setup
    df_snapshot = pd.DataFrame([
        {
            "PersNr": "AZ1",
            "Jobfamily": "Azubi",
            "TrfGr": "TVA",
            "Organisationseinheit": "UnitA",
            "Eintritt": pd.Timestamp("2022-08-01"),
            "active": True,
            "mak": 0.0,
            "OE-Cluster": "ClusterA"
        },
        {
            "PersNr": "P1",
            "Jobfamily": "TargetJF",
            "TrfGr": "E9",
            "Organisationseinheit": "UnitB",
            "Eintritt": pd.Timestamp("2020-01-01"),
            "active": True,
            "mak": 1.0,
            "OE-Cluster": "ClusterB"
        }
    ]).set_index("PersNr", drop=False)
    
    params = {
        "azubi": {
            "active": True,
            "retention_rate": 1.0,
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {
                "TargetJF": 1.0,
                "Angestellte": 0.0
            }
        },
        "random_seed": 42
    }
    
    start_date = pd.Timestamp("2025-08-01")
    
    res = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=start_date,
        end_date=pd.Timestamp("2025-08-31"),
        params=params
    )
    
    events = res["events"]
    takeover_event = events[events["type"] == "Azubi_Conversion_In"]
    assert not takeover_event.empty
    assert takeover_event.iloc[0]["Jobfamily"] == "TargetJF"
