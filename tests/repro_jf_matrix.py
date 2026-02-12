
import pandas as pd
import numpy as np
import sys
import os

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zugaenge.forecast import _simulate_hires, PeriodInfo

def repro_jf_matrix_fail():
    print("--- Reproducing Job-Family Matrix Failure ---")
    
    rng = np.random.RandomState(42)
    period = PeriodInfo(
        start=pd.Timestamp("2026-01-01"),
        end=pd.Timestamp("2026-12-31"),
        label="2026"
    )
    
    # 1. Setup Matrix Param with 80% "Vermögensberatung"
    target_jf = "Vermögensberatung"
    target_cluster = "Vertrieb"
    
    dist_config = [
        {"Jobfamily": target_jf, "OE-Cluster": target_cluster, "Share %": 0.8},
        {"Jobfamily": "Other", "OE-Cluster": "Other", "Share %": 0.2}
    ]
    
    params = {
        "new_hires": {
            "count_per_year": 100, # Enough for stats
            "strategy": "Random", # Force matrix usage
            "distribution": dist_config
        }
    }
    
    # 2. Simulate Hires
    events = []
    _simulate_hires(pd.DataFrame(), params, period, rng, events, ["Org_Random"], [])
    
    events_df = pd.DataFrame(events)
    print(f"\n[Step 1] Simulated {len(events_df)} events.")
    
    if events_df.empty:
        print("  [FAILURE] No events generated.")
        return

    # Check Generation Stats
    jf_counts = events_df["Jobfamily"].value_counts()
    print("  Generation Stats:")
    print(jf_counts)
    
    count_vb = jf_counts.get(target_jf, 0)
    share_vb = count_vb / len(events_df)
    print(f"  Share '{target_jf}': {share_vb:.2%}")
    
    if share_vb < 0.7: # Allow some variance, but 0.8 should be close
        print("  [FAILURE] Generation share significantly below 80%!")
    else:
        print("  [SUCCESS] Generation share looks plausible.")

    # 3. Simulate Page 4 Enrichment (Job-Family Preservation?)
    print("\n[Step 2] Simulating Page 4 Enrichment (Job-Family)...")
    
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
    
    # --- Enrichment Logic for Job-Family (simplified from Page 4) ---
    # Note: Page 4 logic for JF might be missing the "combine_first" protection!
    
    # 1. Map from Snapshot
    # Note: In Page 4, does it map Jobfamily? Or only Clusters?
    # Let's check if we accidentally overwrite Jobfamily with something else or if we just map JF-Cluster?
    # Usually JF is already in events from forecast.
    
    # IF the script does map Jobfamily from snapshot, it would overwrite new hires with NaN (since they aren't in snapshot).
    if "Jobfamily" in snapshot_df.columns:
        # Simulate the potential bug: blindly mapping
        # jf_map = snapshot_df.set_index("PersNr_str")["Jobfamily"].to_dict()
        # events_df["Jobfamily"] = events_df["persnr_str"].map(jf_map) 
        pass 
        # I need to know what Page 4 actually does. I'll read it after this.
        # For now, let's assume Page 4 does NOT overwrite Jobfamily (hopefully).
        
    # BUT, there is "Job-Family-Cluster".
    # If the user means "Job-Family-Cluster" charts are wrong, that's one thing.
    # If they mean "Job-Family" raw data is wrong, that's another.
    # The user said "Job-Family (bzw. Job-Family-Cluster)".
    
    # Let's assume we need to calculate JF-Cluster from JF.
    # If standard logic is "Map JF-Cluster from Snapshot", it fails for New Hires.
    # We need "Map JF-Cluster from Jobfamily" logic (Lookup Table).
    
    # 4. Simulate Chart Logic (The Reindex Issue for JF)
    print("\n[Step 3] Simulating Chart Visualization (Job-Family)...")
    
    # Correct Logic: Union of Snap + Evt
    snap_jfs = snapshot_df["Jobfamily"].dropna().unique().tolist()
    evt_jfs = events_df["Jobfamily"].dropna().unique().tolist()
    all_jfs = sorted(list(set(snap_jfs + evt_jfs)))
    
    # Reindex
    c_stats = events_df.groupby("Jobfamily").size().reindex(all_jfs, fill_value=0).reset_index(name="Zugänge")
    
    print("  Chart Data (Snapshot Categories Only):")
    print(c_stats)
    
    if target_jf in c_stats["Jobfamily"].values:
        row = c_stats[c_stats["Jobfamily"] == target_jf].iloc[0]
        if row["Zugänge"] > 0:
            print("  [SUCCESS] Target JF appears in Chart.")
        else:
            print("  [FAILURE] Target JF found but count is 0.")
    else:
        print(f"  [FAILURE] Target JF '{target_jf}' dropped by Chart Reindex (Snapshot Filter)!")
        
    # Also check JF-Cluster Chart Logic matches the fix I made for OE-Cluster?
    # I suspect I only fixed OE-Cluster chart logic in previous turn.

if __name__ == "__main__":
    repro_jf_matrix_fail()
