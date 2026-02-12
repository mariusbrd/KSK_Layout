import pandas as pd
import numpy as np
import sys
import os

# Add parent directory to path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.schemas import COL_PERSNR, COL_GEB, COL_EINTRITT, COL_STATUS, COL_BSGRD

def test_rent_pool():
    print("=== TEST: Rente Pool Composition (Ruhend included?) ===")
    
    start_date = pd.Timestamp("2026-01-01")
    
    # Setup 3 Persons
    data = {
        COL_PERSNR: ["P_Active", "P_Ruhend", "P_ATZ"],
        COL_GEB: [pd.Timestamp("1961-01-01")] * 3, # 65 years old in 2026
        COL_EINTRITT: [pd.Timestamp("2000-01-01")] * 3,
        COL_STATUS: [
            "Aktives Beschäftigungsverhältnis", 
            "Ruhendes Beschäftigungsverhältnis",
            "Aktives Beschäftigungsverhältnis" # ATZ will be set via df_atz
        ],
        COL_BSGRD: [100.0] * 3,
        # Optional columns
        "Jobfamily": ["Test"] * 3,
        "Organisationseinheit": ["TestOrg"] * 3,
        "TrfGr": ["E13"] * 3,
        "St": [4] * 3
    }
    df_ma = pd.DataFrame(data)
    
    # Setup ATZ DataFrame for P_ATZ
    # Use internal column names as defined in schemas.py (bypassing loader renaming)
    df_atz = pd.DataFrame({
        COL_PERSNR: ["P_ATZ"],
        "Phase": ["AR"],
        "Beginn": [pd.Timestamp("2025-01-01")],
        "Ende": [pd.Timestamp("2027-12-31")],
        "Ende ATZ Vertrag": [pd.Timestamp("2030-12-31")]
    })
    
    # Params: Force Retirement (100%)
    params = {
        "random_seed": 42,
        "components": {
            "atz": True, # Needed to recognize P_ATZ
            "retirement": True,
            "quit": False
        },
        "atz": {
            "new_atz_rate": 0.0, # Disable NEW ATZ to isolate pool check
            "atz_duration_ar_years": 3.0,
            "atz_duration_fr_years": 3.0
        },
        "retirement": {
            "rent_rate_65": 1.0,         # 100% Probability -> Everyone eligible MUST retire
            "rent_rate_60_65": 1.0
        },
        "global": {
            "start_date": "2026-01-01",
            "end_date": "2026-12-31"
        }
    }
    
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
    
    print(f"\nTotal Events: {len(events)}")
    if not events.empty:
        # Filter for Retirement events
        retirements = events[events["reason_code"] == "RETIREMENT"]
        retired_ids = retirements["persnr"].unique().tolist()
        print(f"Retired Person IDs: {retired_ids}")
        
        # Check P_Active
        if "P_Active" in retired_ids:
            print("✅ P_Active: Retired (Expected)")
        else:
            print("❌ P_Active: NOT Retired (Unexpected - should be in pool)")

        # Check P_Ruhend
        if "P_Ruhend" in retired_ids:
            print("✅ P_Ruhend: Retired (Expected: Ruhend is INCLUDED in pool)")
        else:
            print("❌ P_Ruhend: NOT Retired (Means Ruhend is EXCLUDED from pool)")
            
        # Check P_ATZ
        if "P_ATZ" in retired_ids:
            print("❌ P_ATZ: Retired (Unexpected - should be excluded due to existing ATZ)")
        else:
            print("✅ P_ATZ: NOT Retired (Expected)")
            
    else:
        print("❌ FAILURE: No events generated at all!")

if __name__ == "__main__":
    test_rent_pool()
