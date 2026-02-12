
import pandas as pd
import numpy as np
import sys
import os

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zugaenge.forecast import _simulate_hires, PeriodInfo

def repro_matrix_fail():
    print("--- Reproducing Matrix Allocation Failure ---")
    
    rng = np.random.RandomState(42)
    period = PeriodInfo(
        start=pd.Timestamp("2025-01-01"),
        end=pd.Timestamp("2025-12-31"),
        label="2025"
    )
    
    # 1. Setup Matrix Param
    target_jf = "Matrix_JF"
    target_cluster = "Matrix_Cluster"
    
    dist_config = [
        {"Jobfamily": target_jf, "OE-Cluster": target_cluster, "Share %": 1.0}
    ]
    
    params = {
        "new_hires": {
            "count_per_year": 10,
            "strategy": "Random", # Force matrix usage (no vacancies)
            "distribution": dist_config
        }
    }
    
    # 2. Simulate Hires
    events = []
    _simulate_hires(pd.DataFrame(), params, period, rng, events, ["Org_Random"], [])
    
    events_df = pd.DataFrame(events)
    print(f"\n[Step 1] Simulated {len(events_df)} events.")
    
    # Check if Matrix was applied at generation time
    if not events_df.empty:
        sample = events_df.iloc[0]
        print(f"  Sample Generation: JF='{sample.get('Jobfamily')}', Cluster='{sample.get('OE-Cluster')}'")
        
        if sample.get("Jobfamily") != target_jf:
            print("  [FAILURE] Matrix ignored at generation time!")
            return
    else:
        print("  [FAILURE] No events generated.")
        return

    # 3. Simulate Page 4 Enrichment (The Suspect)
    print("\n[Step 2] Simulating Page 4 Enrichment...")
    
    # Create Dummy Snapshot (Simulation of existing employees)
    snapshot_df = pd.DataFrame({
        "PersNr": ["100", "200"],
        "Jobfamily": ["Old_JF", "Old_JF"],
        "OE-Cluster": ["Old_Cluster", "Old_Cluster"],
        "Organisationseinheit": ["Org_Random", "Org_Random"]
    })
    
    # Add fake persnr_str cols
    events_df["persnr_str"] = events_df["persnr"].astype(str)
    snapshot_df["PersNr_str"] = snapshot_df["PersNr"].astype(str)
    
    # --- Enrichment Logic Copy-Paste from Page 4 (simplified) ---
    
    # OE-Cluster Logic
    # 1. Map from Snapshot (should fail for new hires)
    snapshot_unique = snapshot_df.drop_duplicates(subset=["PersNr_str"])
    oe_cluster_map = snapshot_unique.set_index("PersNr_str")["OE-Cluster"].to_dict()
    mapped_oe = events_df["persnr_str"].map(oe_cluster_map)
    
    # Apply combine_first logic (The Fix)
    if "OE-Cluster" in events_df.columns:
         events_df["OE-Cluster"] = events_df["OE-Cluster"].fillna(mapped_oe).fillna("Unclustered")
    else:
         events_df["OE-Cluster"] = mapped_oe.fillna("Unclustered")
         
    # Fallback via OrgUnit
    org_cluster_map = snapshot_df.set_index("Organisationseinheit")["OE-Cluster"].to_dict()
    mask_unclustered = (events_df["OE-Cluster"] == "Unclustered") | (events_df["OE-Cluster"].isna())
    
    if mask_unclustered.any():
        print(f"  Warning: {mask_unclustered.sum()} rows considered Unclustered/NaN after step 1.")
        # This is where it might go wrong if "Matrix_Cluster" is overwritten by "Old_Cluster" via OrgUnit mapping?
        # WAIT: The mask checks for "Unclustered". If "Matrix_Cluster" is there, it is NOT "Unclustered", so mask is False.
        # UNLESS "Matrix_Cluster" was somehow lost or treated as NaN.
        
        fill_vals = events_df.loc[mask_unclustered, "Organisationseinheit"].map(org_cluster_map)
        events_df.loc[mask_unclustered, "OE-Cluster"] = fill_vals.fillna("Unclustered")

    # Check Result
    final_sample = events_df.iloc[0]
    print(f"  Final Result: Cluster='{final_sample['OE-Cluster']}'")
    
    if final_sample["OE-Cluster"] == target_cluster:
        print("  [SUCCESS] Matrix Cluster persisted.")
    elif final_sample["OE-Cluster"] == "Old_Cluster":
        print("  [FAILURE] Overwritten by OrgUnit Mapping!")
    else:
        print(f"  [FAILURE] Got '{final_sample['OE-Cluster']}'")

    # 4. Simulate Chart Logic (The Reindex Issue)
    print("\n[Step 3] Simulating Chart Visualization...")
    
    # Snapshot only has "Old_Cluster"
    # Events have "Matrix_Cluster"
    
    # "Zoom" Logic from Page 4: uses df_filtered_rows (snapshot)
    # logic updated to include Union of Snapshot + Events
    snap_clusters = snapshot_df["OE-Cluster"].unique().tolist()
    evt_clusters = events_df["OE-Cluster"].unique().tolist()
    all_clusters = sorted(list(set(snap_clusters + evt_clusters)))
    
    # Remove NaNs or empty strings if any
    all_clusters = [c for c in all_clusters if pd.notna(c) and str(c).strip() != ""]

    print(f"  Y-Axis Categories: {all_clusters}")
    
    # GroupBy and Reindex
    c_stats = events_df.groupby("OE-Cluster").size().reindex(all_clusters, fill_value=0).reset_index(name="Zugänge")
    
    print("  Chart Data:")
    print(c_stats)
    
    if target_cluster in c_stats["OE-Cluster"].values:
        row = c_stats[c_stats["OE-Cluster"] == target_cluster].iloc[0]
        if row["Zugänge"] > 0:
            print("  [SUCCESS] Matrix Cluster appears in Chart.")
        else:
            print("  [FAILURE] Matrix Cluster found but count is 0 (Unexpected).")
    else:
        print(f"  [FAILURE] Matrix Cluster '{target_cluster}' is MISSING from Chart!")

if __name__ == "__main__":
    repro_matrix_fail()
