
import pandas as pd
import numpy as np
import sys
import os

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def repro_jf_cluster_fail():
    print("--- Reproducing JF-Cluster Assignment Failure ---")

    # 1. Create Dummy Snapshot
    # "Family_A" belongs to "Cluster_A"
    snapshot_df = pd.DataFrame({
        "PersNr": ["100"],
        "Jobfamily": ["Family_A"],
        "JF-Cluster": ["Cluster_A"],
        "active": [True]
    })
    
    # 2. Create New Hire Event (mimic _simulate_hires output)
    # Note: _simulate_hires sets Jobfamily but NOT JF-Cluster
    events_df = pd.DataFrame([{
        "persnr": "NH_123",
        "Jobfamily": "Family_A", # Matrix assigned this
        "OE-Cluster": "Some_OE_C",
        "org_unit": "Unbekannt",
        "planstelle": "Nachbesetzung" # Generic
    }])
    
    print("\n[Step 1] Initial Event:")
    print(events_df[["persnr", "Jobfamily", "planstelle"]])
    
    # 3. Apply Current Page 4 Enrichment Logic (Simplified)
    events_df["persnr_str"] = events_df["persnr"].astype(str)
    snapshot_df["PersNr_str"] = snapshot_df["PersNr"].astype(str)
    snapshot_unique = snapshot_df.drop_duplicates(subset=["PersNr_str"])
    
    # Logic Part 1: Map from PersNr (Fails for NH)
    jf_cluster_map = snapshot_unique.set_index("PersNr_str")["JF-Cluster"].to_dict()
    mapped_jf_c = events_df["persnr_str"].map(jf_cluster_map)
    events_df["JF-Cluster"] = mapped_jf_c.fillna("Unclustered")
    
    print(f"  After PersNr Map: {events_df['JF-Cluster'].iloc[0]}")
    
    # Logic Part 2: Fallback via Planstelle (Fails for generic pos)
    # (Mocking load_cluster_mappings behavior for 'Nachbesetzung' -> Unknown)
    # usually Nachbesetzung is not in the map
    
    print(f"\n[Step 2] Final JF-Cluster: '{events_df['JF-Cluster'].iloc[0]}'")
    
    if events_df['JF-Cluster'].iloc[0] == "Unclustered":
        print("  [FAILURE] JF-Cluster is 'Unclustered' despite valid Jobfamily!")
        print("  Expected: 'Cluster_A' (derived from Family_A)")
    else:
        print(f"  [SUCCESS] Got '{events_df['JF-Cluster'].iloc[0]}'")

    # 4. Preview Fix Logic (Jobfamily -> Cluster Lookup)
    print("\n[Step 3] Previewing Fix...")
    mask_unclustered = events_df["JF-Cluster"] == "Unclustered"
    if mask_unclustered.any():
        # Build Map from snapshot
        jf_to_cluster = snapshot_df.dropna(subset=["Jobfamily", "JF-Cluster"]).set_index("Jobfamily")["JF-Cluster"].to_dict()
        print(f"  Lookup Map: {jf_to_cluster}")
        
        # Apply
        events_df.loc[mask_unclustered, "JF-Cluster"] = events_df.loc[mask_unclustered, "Jobfamily"].map(jf_to_cluster).fillna("Unclustered")
        
        print(f"  After Fix: '{events_df['JF-Cluster'].iloc[0]}'")
        if events_df['JF-Cluster'].iloc[0] == "Cluster_A":
            print("  [SUCCESS] Fix works!")

if __name__ == "__main__":
    repro_jf_cluster_fail()
