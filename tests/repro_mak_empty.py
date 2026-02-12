
import pandas as pd
import os
import sys
import numpy as np

# Setup Path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dataloader.loader import load_and_prepare_data, calculate_mak_vectorized
from abgaenge.forecast import run_forecast_abgaenge

def default_params():
    return {
        "attrition": {"annual_rate": 0.05, "strategy": "By Tenure"},
        "new_hires": {"count_per_year": 10, "strategy": "Random"},
        "atz": {"prob_retirement_age": 0.5},
        "retirement": {"age": 65}
    }

def debug_mak_empty():
    print("--- Debugging MAK Chart Empty Issue ---")
    
    # 1. Load Data
    snapshot_df, _, _, _ = load_and_prepare_data()
    print(f"[INFO] Snapshot Records: {len(snapshot_df)}")

    # 2. Simulate Page 3 Aggregation
    df_ma = snapshot_df.dropna(subset=["PersNr"])
    df_ma = calculate_mak_vectorized(df_ma, set())
    
    # Aggregation (as in Page 3)
    agg_dict = {
        "MAK_Calculated": "sum",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Sollarbeitszeit": "sum",
        "Organisationseinheit": "first", 
        "Jobfamily": "first",
        "OE-Cluster": "first",
        "JF-Cluster": "first"
    }
    
    # Performance Optimization: Aggregate using groupby
    df_employee_agg = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
    
    # FIX: Preserve MAK_Calculated!
    df_employee_agg["mak"] = df_employee_agg["MAK_Calculated"]
    
    # SIMULATE USER DATA ISSUE: Force MAK to 0.0 for everyone to see if chart is empty
    # This tests the hypothesis that the user's data just has 0 MAK for leavers.
    # SIMULATE USER DATA ISSUE: Mixed MAK (50% 0.0, 50% Real)
    print("--- SIMULATING MIXED MAK DATA ---")
    rows = len(df_employee_agg)
    mask = np.random.choice([True, False], size=rows)
    df_employee_agg.loc[mask, "mak"] = 0.0
    
    df_employee_agg["Sollarbeitszeit"] = df_employee_agg["Sollarbeitszeit"].fillna(39.0)
    df_employee_agg["BsGrd"] = df_employee_agg["mak"] * 100.0
    
    print(f"[INFO] Aggregated Records: {len(df_employee_agg)}")
    print(f"[INFO] Total MAK in Base: {df_employee_agg['mak'].sum()}")
    
    # 3. Run Forecast
    from datetime import date
    start_date = date(2024, 1, 1)
    end_date = date(2025, 12, 31)
    params = default_params()
    
    result = run_forecast_abgaenge(df_employee_agg, pd.DataFrame(), pd.Timestamp(start_date), pd.Timestamp(end_date))
    events = result["events_person_level"]
    
    print(f"[INFO] Events Generated: {len(events)}")
    
    # 4. Filter for Departures (Cluster Chart Logic)
    all_clusters = sorted(df_employee_agg["OE-Cluster"].unique().tolist())
    
    cluster_events = events[events["headcount_change"] < 0].copy()
    print(f"[INFO] Departure Events: {len(cluster_events)}")
    
    if cluster_events.empty:
        print("[WARN] No departures found.")
        return

    # 5. Enrich Logic (Exact Page 3 Logic)
    if "OE-Cluster" not in cluster_events.columns and "OE-Cluster" in df_employee_agg.columns:
        lookup_cluster = df_employee_agg[["PersNr", "OE-Cluster"]].copy()
        lookup_cluster["key"] = lookup_cluster["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        lookup_cluster = lookup_cluster.drop_duplicates(subset=["key"])
        
        cluster_events["key"] = cluster_events["persnr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        
        cluster_events = cluster_events.merge(
            lookup_cluster[["key", "OE-Cluster"]],
            on="key",
            how="left"
        )
        cluster_events.drop(columns=["key"], inplace=True)
        cluster_events["OE-Cluster"] = cluster_events["OE-Cluster"].fillna("Unclustered")
    
    # 6. Check Charts Data
    # Chart 1: Headcount
    c_stats_h = cluster_events.groupby("OE-Cluster").size().reindex(all_clusters, fill_value=0).reset_index(name="Abgänge")
    print(f"[INFO] Total Headcount Loss: {c_stats_h['Abgänge'].sum()}")

    # Chart 2: MAK
    if "mak_change" in cluster_events.columns:
        print(f"[INFO] Mak Change Column Present. Sum: {cluster_events['mak_change'].sum()}")
        cluster_events["mak_loss"] = cluster_events["mak_change"].abs()
        
        # Check aggregation
        c_stats_m = cluster_events.groupby("OE-Cluster")["mak_loss"].sum().reindex(all_clusters, fill_value=0.0).reset_index(name="MAK-Verlust")
        print(f"[INFO] Total MAK Loss in Chart Data: {c_stats_m['MAK-Verlust'].sum()}")
        
        if c_stats_m['MAK-Verlust'].sum() == 0:
             print("[FAIL] MAK Chart is EMPTY (0 sum).")
        else:
             print("[PASS] MAK Chart has data.")
    else:
        print("[FAIL] mak_change column MISSING in cluster_events")

if __name__ == "__main__":
    debug_mak_empty()
