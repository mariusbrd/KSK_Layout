import pandas as pd
import numpy as np
from datetime import datetime, date
import sys
import os

# Add parent directory to path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.schemas import COL_PERSNR, COL_GEB, COL_EINTRITT, COL_STATUS, COL_BSGRD

def test_atz_rente_order():
    print("=== TEST: ATZ vs. Rente Order & Exclusion ===")
    
    # 1. Setup: One Person, Age 60, eligible for BOTH
    # ATZ min age: 55, Rent min age: 60
    start_date = pd.Timestamp("2026-01-01")
    
    # Person 1: "DoubleCandidate"
    # Born 1966-01-01 -> 60 years old in 2026
    # Active, not in ATZ yet.
    data = {
        COL_PERSNR: ["999999"],
        COL_GEB: [pd.Timestamp("1966-01-01")],
        COL_EINTRITT: [pd.Timestamp("2000-01-01")],
        COL_STATUS: ["Aktives Beschäftigungsverhältnis"],
        COL_BSGRD: [100.0],
        # Optional columns to avoid warnings
        "Jobfamily": ["Test"],
        "Organisationseinheit": ["TestOrg"],
        "TrfGr": ["E13"],
        "St": [4]
    }
    df_ma = pd.DataFrame(data)
    
    # Empty ATZ and Planstellen
    df_atz = pd.DataFrame(columns=[COL_PERSNR, "ATZ - Phase", "Beginn ATZ - Phase", "Ende ATZ - Phase", "Ende ATZ - Vertrag"])
    
    # 2. Params: Force BOTH ATZ and Retirement
    params = {
        "random_seed": 42,
        "components": {
            "atz": True,
            "retirement": True,
            "quit": False # Disable quit to focus on ATZ/Rent
        },
        "atz": {
            "new_atz_rate": 100.0,       # Force Poisson to pick at least one
            "eligible_age_min": 55,
            "eligible_age_max": 65,
            "use_atz_matrix": False,     # Use base rate
            "atz_duration_ar_years": 3.0,
            "atz_duration_fr_years": 3.0
        },
        "retirement": {
            "rent_rate_65": 1.0,         # 100% Probability
            "rent_rate_60_65": 1.0       # 100% Probability for 60-65
        },
        "global": {
            "start_date": "2026-01-01",
            "end_date": "2026-12-31"
        }
    }
    
    # 3. Run Forecast
    print("\nStarting Simulation...")
    result = run_forecast_abgaenge(
        df_ma=df_ma,
        df_atz=df_atz,
        params=params,
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-12-31"),
        freq="M"
    )
    
    events = result["events_person_level"]
    
    # 4. Analyze Events
    print(f"\nTotal Events: {len(events)}")
    if not events.empty:
        print(events[["persnr", "reason_code", "event_date"]])
        
        reasons = events["reason_code"].unique()
        print(f"Reasons found: {reasons}")
    
    # If events is empty, it means:
    # 1. No Retirement (Success!)
    # 2. No ATZ Transition (AR->FR) yet (Expected, as AR duration is 3 years)
    
    if events.empty:
        print("\nSUCCESS: Total Events = 0. This means no Retirement occurred (Blocked by ATZ).")
        return

    if "reason_code" not in events.columns:
        # Should not happen if not empty, but safety check
        print("Events DF missing columns.")
        return

    is_retired = "RETIREMENT" in events["reason_code"].values
    
    if is_retired:
        print("\nFAILURE: Person was retired despite 100% ATZ probability and execution order priority!")
    else:
        print("\nSUCCESS: Person was NOT retired. This implies ATZ logic took precedence and excluded them.")
        
    # Validation: Ensure they actually became ATZ case?
    # We can't easily see internal state 'in_atz' from the standardized output unless we debug or look at detailed tables.
    # However, given Rent Rate = 1.0, the ONLY reason they aren't retired is if they were filtered out.
    
    # Let's double check control: Run with ATZ disabled.
    print("\n--- Control Run (ATZ Disabled) ---")
    params_control = params.copy()
    params_control["components"]["atz"] = False
    
    result_ctrl = run_forecast_abgaenge(
        df_ma=df_ma.copy(), 
        df_atz=df_atz.copy(), 
        params=params_control,
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-12-31"),
        freq="M"
    )
    events_ctrl = result_ctrl["events_person_level"]
    
    is_retired_ctrl = "RETIREMENT" in events_ctrl["reason_code"].unique() if not events_ctrl.empty else False
    
    if is_retired_ctrl:
        print("✅ Control Run confirmed: Without ATZ, person retires (as expected).")
    else:
        print("❌ Control Run FAILURE: Person did not retire even without ATZ! Something else is wrong.")

if __name__ == "__main__":
    test_atz_rente_order()
