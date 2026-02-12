import pandas as pd
import numpy as np
import sys
import os

# Add parent directory to path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.schemas import COL_PERSNR, COL_GEB, COL_EINTRITT, COL_STATUS, COL_BSGRD

def test_atz_bug_repro():
    print("=== TEST: ATZ Early Transition Bug Repro ===")
    
    # Scenario:
    # Sim Start: 2026-01-01
    # Person ATZ Start (AR): 2025-12-31
    # Person ATZ FR Expected: 2025-12-31 + 2.5 years (30 months) = 2028-06-30 approx.
    # Person ATZ FR in Data (Bug Logic): 2026-02-01 (1 month duration)
    # Expected Behavior: Logic enforces 2.5 years -> Switch should NOT happen in 2026.
    
    start_date = pd.Timestamp("2026-01-01")
    
    # Setup Person
    data = {
        COL_PERSNR: ["BUG_CASE"],
        COL_GEB: [pd.Timestamp("1965-01-01")], # 61 years
        COL_EINTRITT: [pd.Timestamp("2000-01-01")],
        COL_STATUS: ["Aktives Beschäftigungsverhältnis"],
        COL_BSGRD: [100.0],
        "Jobfamily": ["Test"],
        "Organisationseinheit": ["TestOrg"],
        "TrfGr": ["E13"],
        "St": [4]
    }
    df_ma = pd.DataFrame(data)
    
    # Setup ATZ DataFrame with invalid short duration
    # Use internal schemas (Phase, Beginn, Ende) or whatever loader produces.
    # Since run_forecast skips loader renaming if passed directly, we use internal names from schema.py?
    # Actually, run_forecast expects the DF to be ready. 
    # Let's verify what columns forecast expects.
    # forecast.py: _build_atz_pivot uses COL_ATZ_PHASE ("Phase"), COL_ATZ_BEGINN ("Beginn"), etc.
    
    df_atz = pd.DataFrame({
        COL_PERSNR: ["BUG_CASE"],
        "Phase": ["Arbeitsphase"], # Mixed legacy value often found in data
        "Beginn": [pd.Timestamp("2025-12-31")],
        # Let's say Excel defines FR transition very early
        # Note: ATZ file usually has multiple rows per person (one for AR, one for FR)
        # OR just Start/End of current phase?
        # The forecast.py _build_atz_pivot logic expects multiple rows usually to build the full picture?
        # Let's provide both AR and FR rows to simulate the Excel content fully.
        "Ende": [pd.Timestamp("2026-01-31")], # AR ends 2026-01-31 (1 month duration)
        "Ende ATZ Vertrag": [pd.Timestamp("2030-12-31")]
    })
    
    # Add FR row (keeping "FR" or "Freistellungsphase"?)
    # Usually consistent, so let's try "Freistellungsphase" too, or mixed.
    # If the user sees the transition, FR MUST be recognized.
    # But if forecast.py strict on "FR", "Freistellungsphase" wouldn't trigger event either?
    # Let's see loop logic.
    # ar = df[df["Phase"] == "AR"]
    # fr = df[df["Phase"] == "FR"]
    # So if "Freistellungsphase", it is also ignored. 
    # BUT maybe the user has "AR" missing (filtered out) but "FR" present as "FR"?
    # Let's try explicit mismatch: AR="Arbeitsphase", FR="FR".
    
    row_fr = pd.DataFrame({
        COL_PERSNR: ["BUG_CASE"],
        "Phase": ["FR"],
        "Beginn": [pd.Timestamp("2026-02-01")], # Starts FR immediately
        "Ende": [pd.Timestamp("2030-12-31")],
        "Ende ATZ Vertrag": [pd.Timestamp("2030-12-31")]
    })
    df_atz = pd.concat([df_atz, row_fr], ignore_index=True)
    
    # Params
    params = {
        "random_seed": 42,
        "components": { "atz": True, "retirement": False, "quit": False },
        "atz": {
            "new_atz_rate": 0.0,
            "atz_duration_ar_years": 2.5, # Enforce 2.5 years (30 months)
            "atz_duration_fr_years": 2.5
        },
        "global": { "start_date": "2026-01-01", "end_date": "2026-12-31" }
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
        print(events[["persnr", "reason_code", "event_date"]])
        
        # Check for AR->FR transition event
        transitions = events[events["reason_code"] == "ATZ_AR_TO_FR"]
        if not transitions.empty:
            date = transitions.iloc[0]["event_date"]
            print(f"\n❌ FAILURE: AR->FR Transition occurred at {date}!")
            print("Expected: No transition in 2026 (should be in 2028).")
        else:
            print("\n✅ SUCCESS: No AR->FR Transition in 2026.")
    else:
        print("\n✅ SUCCESS: No events (means no early transition).")

if __name__ == "__main__":
    test_atz_bug_repro()
