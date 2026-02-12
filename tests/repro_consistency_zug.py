
import sys
import os
import pandas as pd
import numpy as np
from datetime import date

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataloader.loader import load_and_prepare_data
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params
from abgaenge.forecast import run_forecast_abgaenge # needed for vacancies?

def main():
    print("--- 1. Loading Global Data ---")
    snapshot_df, _, _, _ = load_and_prepare_data()
    print(f"Global Snapshot: {len(snapshot_df)} rows")
    
    # Defaults
    ist_stichtag = date(2024, 6, 30)
    end_date = date(2025, 6, 30)
    params = default_params()
    
    # Force specific params to expose issue
    # 10 New Hires, Random Distribution
    params["new_hires"]["count_per_year"] = 10
    params["new_hires"]["strategy"] = "Random"
    
    print("\n--- 2. Global Forecast ---")
    # Vacancies needed? run_forecast_zugaenge needs 'vacancies' list logic inside? 
    # Actually run_forecast_zugaenge takes `vacancies` argument. We can pass empty for Random strategy.
    
    global_res = run_forecast_zugaenge(
        df_snapshot=snapshot_df,
        vacancies=[],
        start_date=pd.Timestamp(ist_stichtag),
        periods_years=1,
        params=params
    )
    global_events = global_res["events"]
    print(f"Global Events Generated: {len(global_events)}")
    print(f"Global Count Sum: {global_events['count'].sum()}")
    
    # --- 3. Filtered Forecast (Dept A) ---
    unit = snapshot_df["Organisationseinheit"].mode()[0]
    print(f"\n--- 3. Filtered View (Simulating Page 4 Logic) for OrgUnit = '{unit}' ---")
    
    # OLD ERROR WAY: Filter Input -> Run Forecast -> 10 Hires
    # NEW WAY: Run Global -> Filter Output
    
    # We already have global_events.
    # We filter them by 'org_unit' (or 'Organisationseinheit' if renamed)
    
    cols = global_events.columns
    print(f"Event Columns: {cols}")
    
    filtered_events = global_events.copy()
    if "org_unit" in filtered_events.columns:
         filtered_events = filtered_events[filtered_events["org_unit"] == unit]
    elif "Organisationseinheit" in filtered_events.columns:
         filtered_events = filtered_events[filtered_events["Organisationseinheit"] == unit]
    
    print(f"Filtered Events (View): {len(filtered_events)}")
    print(f"Filtered Count Sum: {filtered_events['count'].sum()}")
    
    # --- 4. Comparison ---
    # Expected: The view should show a subset of the 10 global hires, likely 0 or 1.
    
    print("\n--- Check ---")
    count = filtered_events['count'].sum()
    if count >= 9:
        print(f"❌ INCONSISTENCY : View still shows full global amount ({count}). Did filtering fail?")
    else:
        print(f"✅ Consistent: View shows subset ({count}) of Global (10).")
        
if __name__ == "__main__":
    main()
