
import sys
import os
import pandas as pd
import numpy as np
from datetime import date

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataloader.loader import load_and_prepare_data
from abgaenge.forecast import run_forecast_abgaenge, aggregate_forecast_results
from abgaenge.params import default_params

def main():
    print("--- 1. Loading Data ---")
    snapshot_df, _, _, _ = load_and_prepare_data()
    
    # Setup Params
    params = default_params()
    ist_stichtag = date(2024, 6, 30) # Example
    end_date = date(2025, 6, 30)
    
    print(f"Snapshot Rows: {len(snapshot_df)}")
    
    print("--- 2. Running Global Forecast ---")
    global_result = run_forecast_abgaenge(
        df_ma=snapshot_df,
        df_atz=pd.DataFrame(), # Empty for sim simplicity unless needed
        start_date=pd.Timestamp(ist_stichtag),
        end_date=pd.Timestamp(end_date),
        freq="M",
        params=params
    )
    
    events_global = global_result["events_person_level"]
    params_used = global_result["assumptions"]
    
    print(f"Global Events Generated: {len(events_global)}")
    print(f"Global MAK Loss (from events): {events_global['mak_change'].sum():.2f}")
    
    global_kpis = global_result["forecast_kpis"]
    first = global_kpis.iloc[0]
    last = global_kpis.iloc[-1]
    print(f"Global KPI Delta: {last['mak_end'] - first['mak_start']:.2f}")

    # --- 3. Simulate Filtered View (e.g. Filter by a large OrgUnit or Cluster) ---
    # Pick a random Org Unit
    unit = snapshot_df["Organisationseinheit"].mode()[0]
    print(f"\n--- 3. Applying Filter: OrgUnit = '{unit}' ---")
    
    # A) Filter Snapshot
    df_filtered_rows = snapshot_df[snapshot_df["Organisationseinheit"] == unit].copy()
    print(f"Filtered Snapshot Rows: {len(df_filtered_rows)}")
    
    # B) Aggregate Snapshot (View Agg)
    # Replicating logic from Page 3
    view_agg_dict = {
        "MAK_Calculated": "sum",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Organisationseinheit": "first",
    }
    # Add other cols if present
    for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St"]:
         if col in df_filtered_rows.columns:
             view_agg_dict[col] = "first"

    df_view_agg = df_filtered_rows.groupby("PersNr", as_index=False).agg(view_agg_dict)
    df_view_agg["mak"] = pd.to_numeric(df_view_agg["MAK_Calculated"], errors="coerce").fillna(0.0)
    print(f"Filtered aggregated valid IDs: {len(df_view_agg)}")
    
    start_mak = df_view_agg["mak"].sum()
    print(f"Start MAK (View): {start_mak:.2f}")

    # C) Filter Global Events
    events_view = events_global.copy()
    
    # Filter by Attribute (OrgUnit) matches Snapshot Filter
    if "Organisationseinheit" in events_view.columns:
        matching_mask = events_view["Organisationseinheit"] == unit
        events_view = events_view[matching_mask]
    
    print(f"Events after Attribute Filter: {len(events_view)}")

    # D) Adjust MAK Loss
    # Create Lookup
    pmak_map = df_view_agg.set_index("PersNr")["mak"].to_dict()
    
    def adjust_mak_loss(row):
        pid = str(row["persnr"])
        # Logic from Page 3: only adjust if negative change
        if row["mak_change"] < 0:
            current_view_mak = float(pmak_map.get(pid, 0.0))
            # If person not in view (e.g. filtered out of snapshot but event passed attribute filter? should not happen if consistent), returns 0 -> -0.0
            return -abs(current_view_mak)
        return row["mak_change"]

    events_view["mak_change_adjusted"] = events_view.apply(adjust_mak_loss, axis=1)
    
    # E) Re-Aggregate KPIs
    view_kpis = aggregate_forecast_results(
        df_initial=df_view_agg,
        events_df=events_view, # It uses 'mak_change_adjusted'? No, aggregate_forecast_results acts on passed DF. 
        # CAUTION: Page 3 modifies 'events_view["mak_change"]' IN PLACE before passing!
        # Here I created a new col. I must rename or pass corrected DF.
        start_date=pd.Timestamp(ist_stichtag),
        end_date=pd.Timestamp(end_date),
        freq="M",
        params=params_used
    )
    
    # Fix: use the adjusted column as 'mak_change' for aggregation
    events_for_kpi = events_view.copy()
    events_for_kpi["mak_change"] = events_for_kpi["mak_change_adjusted"]
    
    view_kpis = aggregate_forecast_results(
        df_initial=df_view_agg,
        events_df=events_for_kpi,
        start_date=pd.Timestamp(ist_stichtag),
        end_date=pd.Timestamp(end_date),
        freq="M",
        params=params_used
    )
    
    v_first = view_kpis.iloc[0]
    v_last = view_kpis.iloc[-1]
    kpi_delta = v_last["mak_end"] - v_first["mak_start"]
    
    print(f"\n--- Comparison ---")
    print(f"View KPI Delta (MAK End - Start): {kpi_delta:.2f}")
    
    sum_events_adjusted = events_view["mak_change_adjusted"].sum()
    print(f"Sum of Events (Adjusted): {sum_events_adjusted:.2f}")
    
    # Check consistency
    diff = abs(kpi_delta - sum_events_adjusted)
    if diff > 0.01:
        print(f"DISCREPANCY: KPI {kpi_delta:.2f} != Sum {sum_events_adjusted:.2f}")
    else:
        print(f"Consistent Global Sum")

    # --- 4. Chart Sums ---
    # Enrich with JF-Cluster (Replicating Page 3 Logic)
    # We need to load mappings
    
    # Mocking enrichment logic or imports
    # Let's import the real loader if possible, or just check 'Jobfamily' if that's what is used
    # Page 3 uses 'JF-Cluster'.
    
    print("\n--- Enriching with JF-Cluster ---")
    if "JF-Cluster" in snapshot_df.columns:
        # Simple map based on PersNr
        # In real app, we use snapshot_unique.set_index("PersNr_str")
        # Here we simplify
        jf_map = snapshot_df.set_index("PersNr")["JF-Cluster"].to_dict()
        def get_jf(p):
            return jf_map.get(p, "Unclustered")
        
        events_for_kpi["JF-Cluster"] = events_for_kpi["persnr"].apply(get_jf)
        
        # Fallback logic (simplify)
        mask_un = events_for_kpi["JF-Cluster"] == "Unclustered"
        if mask_un.any():
            # Try mapping via Planstelle/OrgUnit if available
            # Just mark as fallback for now
            pass
            
    else:
        events_for_kpi["JF-Cluster"] = "Unclustered"

    # Analyze by Reason
    print("\n--- By Reason Chart Sum ---")
    by_reason = events_for_kpi.groupby("reason_label")["mak_change"].sum()
    print(by_reason)
    sum_reason = by_reason.sum()
    print(f"Sum Reason: {sum_reason:.2f}")
    
    if abs(sum_reason - kpi_delta) > 0.01:
         print(f"Chart (Reason) Discrepancy")

    # Analyze by JobCluster
    print("\n--- By JF-Cluster Chart Sum ---")
    by_cluster = events_for_kpi.groupby("JF-Cluster")["mak_change"].sum()
    print(by_cluster)
    sum_cluster = by_cluster.sum()
    print(f"Sum Cluster: {sum_cluster:.2f}")
    if abs(sum_cluster - kpi_delta) > 0.01:
             print(f"Chart (Cluster) Discrepancy")

if __name__ == "__main__":
    main()
