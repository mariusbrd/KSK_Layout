import pandas as pd
import numpy as np
import sys
import os

# Adjust path to include root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from abgaenge.forecast import run_forecast_abgaenge, aggregate_forecast_results

def test_forecast_separation():
    print("[TEST] Starting Verification: Forecast Separation Logic")
    
    # 1. Setup Dummy Data
    df_ma = pd.DataFrame({
        "PersNr": ["P01", "P02", "P03", "P04", "P05"],
        "GebDatum": [pd.Timestamp("1960-01-01")] * 5,
        "Eintritt": [pd.Timestamp("2000-01-01")] * 5,
        "BsGrd": [100.0] * 5,
        "Status kundenindividuell": ["Aktiv"] * 5,
        "Organisationseinheit": ["Dept A", "Dept A", "Dept A", "Dept B", "Dept B"],
        "Sollarbeitszeit": [39.0] * 5,
        "MAK_Calculated": [1.0] * 5
    })

    print(f"[DEBUG] df_ma columns: {df_ma.columns.tolist()}")

    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende"])
    
    params = {
        "random_seed": 42,
        "retirement": {"rent_rate_60_65": 1.0},
        "components": {"retirement": True, "atz": False, "quit": False, "ruhend": False}
    }
    
    start = pd.Timestamp("2024-01-01")
    end = pd.Timestamp("2024-12-31")
    
    # 2. Run Global Forecast
    print("[TEST] Running Global Forecast...")
    try:
        result_global = run_forecast_abgaenge(df_ma, df_atz, start, end, "M", params)
    except Exception as e:
        print(f"[ERROR] Forecast failed: {e}")
        return

    events_global = result_global["events_person_level"]
    print(f"   -> Global Events Generated: {len(events_global)}")
    
    # Verify events
    if len(events_global) == 0:
        print("[ERROR] No events generated.")
        return

    # Check Columns before merge
    print(f"[DEBUG] df_ma columns BEFORE MERGE: {df_ma.columns.tolist()}")
    if "Organisationseinheit" not in df_ma.columns:
        print("[FATAL] Organisationseinheit missing from df_ma!")
        return

    # Merge
    try:
        merged = events_global.merge(df_ma[["PersNr", "Organisationseinheit"]], left_on="persnr", right_on="PersNr")
        depts_in_events = merged["Organisationseinheit_y"].unique() # _y because events also has it
        print(f"   -> Events found for Depts: {sorted(depts_in_events)}")
    except KeyError as e:
        print(f"[ERROR] Merge failed: {e}")
        return

    # 3. Simulate View Filtering
    print("\n[TEST] Simulating View Filter: Dept A...")
    df_view = df_ma[df_ma["Organisationseinheit"] == "Dept A"].copy()
    
    valid_ids = set(df_view["PersNr"])
    events_view = events_global[events_global["persnr"].isin(valid_ids)].copy()
    
    print(f"   -> Valid IDs in View: {valid_ids}")
    print(f"   -> Filtered Events Count: {len(events_view)}")
    
    # 4. Re-Aggregate KPIs
    print("[TEST] Re-Aggregating KPIs for View...")
    kpis_view = aggregate_forecast_results(df_view, events_view, start, end, "M", params)
    
    final_headcount = kpis_view.iloc[-1]["headcount_end"]
    print(f"   -> Final Headcount (View): {final_headcount}")
    
    exits_in_view = len(events_view[events_view["headcount_change"] < 0])
    expected_hc = 3 - exits_in_view
    
    if final_headcount == expected_hc:
        print(f"[SUCCESS] KPI Headcount ({final_headcount}) matches Expected ({expected_hc})")
    else:
        print(f"[FAILURE] KPI Headcount ({final_headcount}) != Expected ({expected_hc})")

if __name__ == "__main__":
    test_forecast_separation()
