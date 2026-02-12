
import pandas as pd
import sys
import os

# Add parent dir to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock streamlit session state
class MockSessionState(dict):
    def __getattr__(self, key):
        return self.get(key)
    def __setattr__(self, key, value):
        self[key] = value

# Mock streamlit
import streamlit as st
if not hasattr(st, "session_state"):
    st.session_state = MockSessionState()

def verify_filter_zoom():
    print("--- Verifying Page 4 Filter Logic (Zoom) ---")
    
    # 1. Setup Mock Data
    snapshot_df = pd.DataFrame({
        "PersNr": [1, 2, 3],
        "OE-Cluster": ["Cluster A", "Cluster A", "Cluster B"],
        "Organisationseinheit": ["Org A", "Org A", "Org B"],
        "Jobfamily": ["JF1", "JF1", "JF2"]
    })
    
    # ensure string type for PersNr
    snapshot_df["PersNr"] = snapshot_df["PersNr"].astype(str)
    
    # Mock Forecast Result (Events)
    # Event 1: New Hire in Cluster A (simulated via Org A)
    # Event 2: New Hire in Cluster B
    events_df = pd.DataFrame({
        "persnr": ["NH_1", "NH_2"],
        "org_unit": ["Org A", "Org B"], # Will be renamed
        "count": [1, 1]
    })
    
    print("\n[Input] Events Global:")
    print(events_df)
    
    # 2. Simulate Page 4 Logic (Enrichment + Filtering)
    
    # --- A. Enrichment (Moving Block) ---
    print("\n... Applying Enrichment Logic ...")
    if "org_unit" in events_df.columns and "Organisationseinheit" not in events_df.columns:
        events_df = events_df.rename(columns={"org_unit": "Organisationseinheit"})

    events_df["persnr_str"] = events_df["persnr"].astype(str)
    snapshot_df["PersNr_str"] = snapshot_df["PersNr"].astype(str)
    
    # OE-Cluster Map via OrgUnit (since NH IDs are fake)
    org_cluster_map = snapshot_df.set_index("Organisationseinheit")["OE-Cluster"].to_dict()
    events_df["OE-Cluster"] = events_df["Organisationseinheit"].map(org_cluster_map).fillna("Unclustered")
    
    print("[Intermediate] Events Enriched:")
    print(events_df[["persnr", "Organisationseinheit", "OE-Cluster"]])
    
    # --- B. Simulation of Filter Selection ---
    print("\n... Simulating Filter: OE-Cluster = ['Cluster A'] ...")
    st.session_state["selected_oe_cluster"] = ["Cluster A"]
    
    # --- C. Filtering Logic ---
    events_view = events_df.copy()
    
    selected_oe_c = st.session_state.get("selected_oe_cluster", [])
    if selected_oe_c:
        if "OE-Cluster" in events_view.columns:
            events_view = events_view[events_view["OE-Cluster"].isin(selected_oe_c)]
            
    print("\n[Output] Filtered Events (View):")
    print(events_view)
    
    # 3. Assertions
    if len(events_view) == 1 and events_view.iloc[0]["OE-Cluster"] == "Cluster A":
        print("\n[SUCCESS] Filter correctly zoomed into Cluster A.")
    else:
        print("\n[FAILURE] Filter did not work as expected.")
        # print details
        
if __name__ == "__main__":
    verify_filter_zoom()
