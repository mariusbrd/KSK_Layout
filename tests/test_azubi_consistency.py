import pytest
import pandas as pd
import sys
import os
import numpy as np

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from zugaenge.forecast import run_forecast_zugaenge

def test_azubi_conversion_consistency():
    """
    Verifies that Baseline Azubis retain their OrgUnit and Cluster upon conversion.
    """
    print("\n--- Testing Azubi Conversion Consistency ---")
    
    # 1. Setup Mock Snapshot with Baseline Azubis
    # We create a baseline Azubi in "Unit A" (Cluster A)
    # We set duration such that they graduate in the forecast period.
    
    start_date = pd.Timestamp("2024-01-01")
    # Graduation: Entry + 3 years. 
    # Let's say Entry = 2021-09-01 -> Grad = Aug 2024 (in scope)
    entry_date = pd.Timestamp("2021-09-01") 
    
    df_snapshot = pd.DataFrame({
        "PersNr": ["101"],
        "active": [True],
        "Jobfamily": ["Azubi"],
        "TrfGr": ["TVAöD"],
        "Eintritt": [entry_date],
        "Organisationseinheit": ["Filiale Dorf"],
        "OE-Cluster": ["Privatkunden"],
        "mak": [0.0], # Correctly zeroed
        "is_forecast": [False]
    })
    
    # Params: 100% retention to ensure conversion event
    params = {
        "azubi": {
            "active": True,
            "retention_rate": 1.0,
            "duration_years": 3.0,
            "strategy": "Random", # Should be ignored for Baseline!
            "new_cases_per_year": 0, # No new forecasts to confuse things
            "exclude_baseline_azubis": False
        },
        "trainee": {"active": False, "new_cases_per_year": 0},
        "new_hires": {"active": False, "count_per_year": 0},
        "random_seed": 42
    }
    
    # 2. Run Forecast
    print(f"Running forecast for Baseline Azubi (Unit: Filiale Dorf)...")
    result = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=start_date,
        end_date=pd.Timestamp("2025-12-31"),
        freq="M",
        params=params
    )
    
    events = result["events"]
    
    # 3. Analyze Events
    print("Events generated:")
    print(events[["date", "type", "persnr", "Organisationseinheit", "OE-Cluster"]])
    
    # Check for Out/In Pair
    out_event = events[events["type"] == "Azubi_Conversion_Out"]
    in_event = events[events["type"] == "Azubi_Conversion_In"]
    
    assert len(out_event) == 1, "Should have 1 Conversion Out"
    assert len(in_event) == 1, "Should have 1 Conversion In"
    
    # 4. Consistency Check (The Core Requirement)
    # Both must match the SOURCE ("Filiale Dorf")
    
    unit_out = out_event.iloc[0]["Organisationseinheit"]
    unit_in = in_event.iloc[0]["Organisationseinheit"]
    
    cluster_out = out_event.iloc[0].get("OE-Cluster", "Missing")
    cluster_in = in_event.iloc[0].get("OE-Cluster", "Missing")
    
    print(f"\nUnit Check: Out='{unit_out}' vs In='{unit_in}'")
    print(f"Cluster Check: Out='{cluster_out}' vs In='{cluster_in}'")
    
    assert unit_out == "Filiale Dorf", "Out-Event must preserve source unit"
    assert unit_in == "Filiale Dorf", "In-Event must preserve source unit (Baseline Rule)"
    assert unit_out == unit_in, "Units must match"
    
    assert cluster_out == "Privatkunden", "Out-Event must preserve source cluster"
    assert cluster_in == "Privatkunden", "In-Event must preserve source cluster"
    assert cluster_out == cluster_in, "Clusters must match"
    
    print("\nSUCCESS: Baseline Azubi preserved Unit and Cluster correctly.")

if __name__ == "__main__":
    test_azubi_conversion_consistency()
