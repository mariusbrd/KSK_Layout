import pytest
import pandas as pd
import numpy as np
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from zugaenge.forecast import run_forecast_zugaenge

def test_forecast_azubi_duration():
    """
    Verifies that NEW Forecast Azubis respect the duration_years parameter.
    If entry is 2026-02-01 and duration is 3.0 years, graduation MUST be 2029-08-01 (or close),
    NOT 2026-08-01.
    """
    print("\n--- Testing Forecast Azubi Duration ---")
    
    # Setup
    df_snapshot = pd.DataFrame({
        "PersNr": ["1001"],
        "active": [True],
        "Jobfamily": ["Angestellte"], # Irrelevant dummy
        "TrfGr": ["E13"],
        "Organisationseinheit": ["IT"],
        "Eintritt": [pd.Timestamp("2020-01-01")]
    })
    
    # Config: 3 Years Duration
    params = {
        "azubi": {
            "active": True,
            "new_cases_per_year": 10,
            "duration_years": 3.0, # CRITICAL
            "retention_rate": 1.0,
            "azubi_mak_during_training": 0.0,
            "azubi_conversion_month": 8,
            "azubi_conversion_day": 1
        },
        "new_hires": {"active": False},
        "trainee": {"active": False},
        "random_seed": 42
    }
    
    # Run Forecast (5 Years to cover graduation)
    start_date = pd.Timestamp("2026-01-01")
    end_date = start_date + pd.DateOffset(years=5)
    
    res = run_forecast_zugaenge(df_snapshot, start_date, end_date=end_date, freq="M", params=params)
    events = res["events"]
    
    hires = events[events["type"] == "Azubi_Hire"]
    print(f"Forecast Hires: {len(hires)}")
    
    # Check Duration for each hire
    errors = 0
    for i, row in hires.iterrows():
        entry = row["date"]
        grad = row["graduation_date"]
        
        # Expected Min Date: Entry + 2.5 Years (buffer)
        min_grad = entry + pd.DateOffset(years=2, months=6)
        
        duration_days = (grad - entry).days
        duration_years = duration_days / 365.25
        
        print(f"Hire: {entry.date()} -> Grad: {grad.date()} (Duration: {duration_years:.2f} yrs)")
        
        if duration_years < 2.5:
            print("FAILURE: Graduation too early!")
            errors += 1
            
    assert errors == 0, f"Found {errors} Azubis with too short duration!"
    print("SUCCESS: All Forecast Azubis respect duration.")

if __name__ == "__main__":
    test_forecast_azubi_duration()
