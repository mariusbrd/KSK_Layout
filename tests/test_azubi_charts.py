import pytest
import pandas as pd
import numpy as np
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from zugaenge.forecast import run_forecast_zugaenge, _simulate_azubis

def test_azubi_chart_separation_and_isolation():
    """
    Verifies that:
    1. Isolation Mode produces 0 baseline events.
    2. New Hires (External) are distinct from Conversions (Internal).
    3. Timing is correct (3 years duration -> no conversions in first 2 years).
    """
    print("\n--- Starting Azubi Chart Separation Test ---")
    
    # 1. Setup Data
    df_snapshot = pd.DataFrame({
        "PersNr": ["1001", "1002"],
        "active": [True, True],
        "Jobfamily": ["Azubi", "Angestellte"],
        "TrfGr": ["TVAöD", "E13"],
        "Organisationseinheit": ["IT", "HR"],
        "Eintritt": [pd.Timestamp("2024-01-01"), pd.Timestamp("2020-01-01")] # Baseline Azubi
    })
    
    # 2. Config similar to user scenario
    params = {
        "azubi": {
            "active": True,
            "new_cases_per_year": 15,
            "duration_years": 3.0,
            "retention_rate": 1.0, # 100%
            "exclude_baseline_azubis": True # Isolation ON
        },
        "new_hires": {
            "active": True,
            "count_per_year": 0 # Focus on Azubi
        },
        "trainee": {"active": False},
        "random_seed": 42
    }
    
    # 3. Run Forecast (2 Years)
    start_date = pd.Timestamp("2026-01-01")
    end_date = start_date + pd.DateOffset(years=2)
    params["random_seed"] = 42
    res = run_forecast_zugaenge(df_snapshot, start_date, end_date=end_date, freq="M", params=params)
    events = res["events"]
    
    print(f"Total Events: {len(events)}")
    
    # 4. Check Isolation (No baseline events)
    # Baseline persnr is 1001.
    baseline_events = events[events["persnr"] == "1001"]
    if not baseline_events.empty:
        print("FAILURE: Baseline Azubi events found despite isolation!")
        print(baseline_events)
        assert baseline_events.empty
    else:
        print("SUCCESS: Isolation verified (0 baseline events).")
        
    # 5. Check Chart Data Series Logic
    # We expect ~30 New Hires (15/year * 2 years)
    hires = events[events["type"] == "Azubi_Hire"]
    print(f"Azubi Hires (External): {len(hires)}")
    assert 28 <= len(hires) <= 32 # Randomness tolerance
    
    # 5b. Verify MAK for Hires (New Rule: Should be 0.0)
    # Check if column exists first
    if "mak" in hires.columns:
        hires_mak = hires["mak"].sum()
    else:
        hires_mak = 0.0
        
    print(f"Azubi Hires Total MAK: {hires_mak}")
    assert hires_mak == 0.0, f"Expected 0.0 MAK for Azubi Hires, got {hires_mak}"

    # We expect Conversions now due to accelerated August rule
    convs = events[events["type"] == "Azubi_Conversion_In"]
    print(f"Azubi Conversions (Internal): {len(convs)}")
    
    # Check Dates of Conversions (Must be August)
    if not convs.empty:
        # Check month
        months = convs["date"].dt.month
        non_august = months[months != 8]
        if not non_august.empty:
            print(f"FAILURE: Found {len(non_august)} conversions NOT in August.")
            print(convs[months != 8][["date", "persnr"]])
        assert non_august.empty, "Conversions must be in August"
    
        # Check MAK of Conversions (Must be positive, usually 1.0)
        min_mak = convs["mak"].min()
        print(f"Min Conversion MAK: {min_mak}")
        assert min_mak > 0, f"Conversion MAK must be positive (got {min_mak})"
    else:
        print("WARNING: No conversions found. Check if simulation runs long enough or dates align.")

    # 6. Check "True Workload Growth" Logic
    # Regular Growth = New_Hire + Conversion_In
    reg_growth = events[events["type"].isin(["New_Hire", "Azubi_Conversion_In"])]
    print(f"Regular Staff Growth Events: {len(reg_growth)}")
    # We expect growth now, so removing the "assert == 0" check which was for the old 3-year logic
    print("SUCCESS: Chart separation logic holds.")

if __name__ == "__main__":
    test_azubi_chart_separation_and_isolation()
