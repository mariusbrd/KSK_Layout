import pandas as pd
import sys
import os
from pathlib import Path

# Add parent directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from abgaenge.io import load_inputs
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge import default_params

def debug_pipeline():
    print("=== DEBUG: Full Pipeline with Real Data ===")
    
    base_path = Path(r"f:\Dropbox\Projekte\KSKBöB_HR-Dashboard_Production\KSK_Production")
    
    try:
        # 1. Load Data
        print("Loading Inputs...")
        df_ma, df_atz = load_inputs(base_path)
        print(f"Loaded MA: {df_ma.shape}, ATZ: {df_atz.shape}")
        
        # 2. Setup Params
        params = default_params()
        params["atz"]["atz_duration_ar_years"] = 2.5 # Force 2.5 years
        min_ar_months = 30
        print(f"Params: AR Duration = {params['atz']['atz_duration_ar_years']} years ({min_ar_months} months)")
        
        # 3. Target Case (from inspector: PersNr 4824, AR Start 2025-07-01, FR Start 2026-10-01)
        target_id = "4824"
        
        # Check raw entries for target
        if "PersNr" in df_atz.columns:
             # Normalize target ID format if needed (e.g. pad to 6 chars)
             # df_atz PersNr is normalized by load_inputs to 6 digit string
             target_id_norm = str(int(target_id)).zfill(6)
             
             print(f"\nSearching for PersNr {target_id_norm} in ATZ data...")
             matches = df_atz[df_atz["PersNr"] == target_id_norm]
             print(matches)
             
        # 4. Run Forecast
        print("\nRunning Forecast (2025-01-01 to 2029-12-31)...")
        # Ensure we cover the potential transition dates
        
        result = run_forecast_abgaenge(
            df_ma=df_ma,
            df_atz=df_atz,
            params=params,
            start_date=pd.Timestamp("2025-01-01"),
            end_date=pd.Timestamp("2029-12-31"),
            freq="M"
        )
        
        events = result["events_person_level"]
        
        # 5. Check Target Event
        target_events = events[events["persnr"] == target_id_norm]
        
        if not target_events.empty:
            print("\nEvents for Target Person:")
            print(target_events[["event_date", "reason_code"]]) # Removed label to avoid unicode arrow
            
            ar_to_fr = target_events[target_events["reason_code"] == "ATZ_AR_TO_FR"]
            if not ar_to_fr.empty:
                event_date = ar_to_fr.iloc[0]["event_date"]
                print(f"\nTransition Date: {event_date}")
                
                # Check logic
                # Raw AR Start: 2025-07-01
                # Min FR Start: 2025-07-01 + 30 months = 2028-01-01
                
                if event_date >= pd.Timestamp("2027-12-01"): # Approx check
                    print("SUCCESS: Transition was pushed to 2028 (correct duration enforced).")
                else:
                    print(f"FAILURE: Transition happened too early at {event_date}!")
            else:
                print("No AR->FR event found (maybe already in FR or other issue).")
        else:
            print("No events found for target person.")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_pipeline()
