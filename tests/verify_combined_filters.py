# -*- coding: utf-8 -*-
"""Verify combined filter criteria (AND logic)."""
import sys, os
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
import streamlit as st

if not hasattr(st, "session_state"):
    st.session_state = {}

from dataloader.loader import load_and_prepare_data, calculate_mak_vectorized, load_atz_data_cached
from abgaenge.forecast import run_forecast_abgaenge
from kpi_reference import get_current_stichtag

# --- Load data ---
snapshot_df, history_df, _, _ = load_and_prepare_data()

# --- Simulate Page 3 aggregation ---
def get_processed_data(df_snap, df_atz):
    df_ma = df_snap.copy()
    df_ma = df_ma.dropna(subset=["PersNr"])
    
    atz_fr_persnr_set = set()
    if not df_atz.empty:
        stichtag_ts = pd.Timestamp(get_current_stichtag())
        atz_fr = df_atz[
            (df_atz["Phase"] == "FR") &
            (df_atz["Beginn"] <= stichtag_ts) &
            (df_atz["Ende"] >= stichtag_ts)
        ]
        atz_fr_persnr_set = set(atz_fr["PersNr"].dropna().astype(str).unique())
    
    df_ma = calculate_mak_vectorized(df_ma, atz_fr_persnr_set)
    
    agg_dict = {
        "MAK_Calculated": "sum", "GebDatum": "first", "Eintritt": "first",
        "Austritt": "first", "Status kundenindividuell": "first", "Sollarbeitszeit": "sum",
        "Organisationseinheit": "first"
    }
    for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St", "Alterskohorte"]:
        if col in df_ma.columns:
            agg_dict[col] = "first"
    
    df_agg = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
    df_agg["Sollarbeitszeit"] = 39.0
    df_agg["BsGrd"] = df_agg["MAK_Calculated"] * 100.0
    return df_agg

df_atz = load_atz_data_cached(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
df_ma_global = get_processed_data(snapshot_df, df_atz)

# --- Run global forecast ---
params = {
    "random_seed": 42,
    "components": {"atz": True, "retirement": True, "quit": True, "ruhend": True},
    "atz": {"new_atz_rate": 0.05, "atz_eligible_age_min": 55, "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 2.5, "atz_duration_fr_years": 2.5},
    "retirement": {"rent_rate_65": 0.95, "rent_rate_60_65": 0.02},
    "quit": {"quit_rate_base": 0.03},
    "ruhend": {"ruhend_new_cases_per_year": 2, "ruhend_return_rate": 0.5, "ruhend_avg_duration_months": 12},
}

result = run_forecast_abgaenge(df_ma=df_ma_global, df_atz=df_atz,
    start_date=pd.Timestamp("2024-12-31"), end_date=pd.Timestamp("2034-12-31"),
    freq="Q", params=params)

events_global = result["events_person_level"]

# --- Test function for combined filtering ---
def test_combined_filters(test_id, filter_dict, events_global, df_snapshot_all):
    """
    filter_dict: {column: [values]}
    """
    print(f"\n[TEST {test_id}] Combined Criteria")
    for col, vals in filter_dict.items():
        print(f"  - {col}: {vals}")
        
    events_view = events_global.copy()
    
    # 1. Apply Sidebar Snapshot Filtering (for valid_ids fallback)
    df_filtered_snap = df_snapshot_all.copy()
    for col, vals in filter_dict.items():
        df_filtered_snap = df_filtered_snap[df_filtered_snap[col].isin(vals)]
    
    valid_ids = set(df_filtered_snap["PersNr"].astype(str))
    
    # 2. Apply Direct Attribute Filtering (OrgUnit, Jobfamily)
    if "Organisationseinheit" in filter_dict:
        events_view = events_view[events_view["Organisationseinheit"].isin(filter_dict["Organisationseinheit"])]
    
    if "Jobfamily" in filter_dict:
        events_view = events_view[events_view["Jobfamily"].isin(filter_dict["Jobfamily"])]
        
    # 3. Apply Fallback Filtering (Any other criteria)
    other_cols = [c for c in filter_dict.keys() if c not in ["Organisationseinheit", "Jobfamily"]]
    if other_cols:
        events_view = events_view[events_view["persnr"].isin(valid_ids)]
        
    print(f"  Snapshot results: {len(df_filtered_snap)}")
    print(f"  Event results: {len(events_view)}")
    
    # Verification
    if not events_view.empty:
        for _, row in events_view.iterrows():
            sn_row = df_snapshot_all[df_snapshot_all["PersNr"].astype(str) == str(row["persnr"])]
            if sn_row.empty:
                 print(f"  [WARN] PersNr {row['persnr']} not in snapshot")
                 continue
            for col, vals in filter_dict.items():
                if sn_row.iloc[0][col] not in vals:
                    print(f"  [FAIL] PersNr {row['persnr']} has {col}={sn_row.iloc[0][col]}, expected {vals}!")
                    return False
        print("  [PASS] All events fulfill all criteria.")
    else:
        # Check if any global events fulfill all criteria
        global_match = events_global.copy()
        for col, vals in filter_dict.items():
            if col in ["Organisationseinheit", "Jobfamily"]:
                global_match = global_match[global_match[col].isin(vals)]
            else:
                # Fallback check
                global_match = global_match[global_match["persnr"].isin(valid_ids)]
        
        if not global_match.empty:
            print(f"  [FAIL] {len(global_match)} global events match all criteria, but filter returned 0!")
            return False
        else:
            print("  [PASS] No events expected for this combination.")
    return True

# --- Execute Tests ---
success = True

# Find some active categories for testing
active_orgs = events_global["Organisationseinheit"].dropna().unique()
active_jfs = events_global["Jobfamily"].dropna().unique()

# Map events to cohorts
events_with_cohort = events_global.merge(df_ma_global[["PersNr", "Alterskohorte"]], left_on="persnr", right_on="PersNr", how="left")
active_cohorts = events_with_cohort["Alterskohorte"].dropna().unique()

if len(active_orgs) > 0 and len(active_jfs) > 0:
    # Test 1: OrgUnit + JobFamily
    success &= test_combined_filters("OrgUnit + JobFamily", {
        "Organisationseinheit": [active_orgs[0]],
        "Jobfamily": [active_jfs[0]]
    }, events_global, df_ma_global)

if len(active_jfs) > 0 and len(active_cohorts) > 0:
    # Test 2: JobFamily + Age Cohort
    success &= test_combined_filters("JobFamily + Age Cohort", {
        "Jobfamily": [active_jfs[0]],
        "Alterskohorte": [active_cohorts[0]]
    }, events_global, df_ma_global)

if len(active_orgs) > 0 and len(active_jfs) > 0 and len(active_cohorts) > 0:
    # Test 3: OrgUnit + JobFamily + Age Cohort
    success &= test_combined_filters("OrgUnit + JobFamily + Age Cohort", {
        "Organisationseinheit": [active_orgs[0]],
        "Jobfamily": [active_jfs[0]],
        "Alterskohorte": [active_cohorts[0]]
    }, events_global, df_ma_global)

if success:
    print("\n[ALL COMBINED TESTS PASSED]")
else:
    print("\n[SOME COMBINED TESTS FAILED]")
    sys.exit(1)
