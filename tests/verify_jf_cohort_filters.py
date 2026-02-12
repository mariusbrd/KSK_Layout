# -*- coding: utf-8 -*-
"""Verify JobFamily and Age Cohort consistency fix."""
import sys, os
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
import re
import streamlit as st

# Mock session state for st.cache_data etc. if needed
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

# --- Test function for filtering ---
def test_filter(filter_name, column, selected_values, events_global, df_snapshot_all):
    print(f"\n[TEST] {filter_name} ({selected_values})")
    
    df_filtered_snap = df_snapshot_all[df_snapshot_all[column].isin(selected_values)]
    valid_ids = set(df_filtered_snap["PersNr"].astype(str))
    
    if column in ["Organisationseinheit", "Jobfamily"]:
        events_filtered = events_global[events_global[column].isin(selected_values)]
    else:
        events_filtered = events_global[events_global["persnr"].isin(valid_ids)]
        
    print(f"  Snapshot results: {len(df_filtered_snap)}")
    print(f"  Event results: {len(events_filtered)}")
    
    if not events_filtered.empty:
        for _, row in events_filtered.iterrows():
            sn_row = df_snapshot_all[df_snapshot_all["PersNr"].astype(str) == str(row["persnr"])]
            if sn_row.empty:
                print(f"  [WARN] Event PersNr {row['persnr']} not in global snapshot")
            elif sn_row.iloc[0][column] not in selected_values:
                print(f"  [FAIL] Mismatch: PersNr {row['persnr']} is {sn_row.iloc[0][column]} in snapshot!")
                return False
        print("  [PASS] All events consistent with filter.")
    else:
        if column in ["Organisationseinheit", "Jobfamily"]:
             global_match = events_global[events_global[column].isin(selected_values)]
        else:
             global_match = events_global[events_global["persnr"].isin(valid_ids)]
             
        if not global_match.empty:
            print(f"  [FAIL] {len(global_match)} global events exist for this category, but filter returned 0!")
            return False
        else:
            print("  [PASS] No events expected (none in global result for this cat).")
    return True

# --- Execute Tests ---
success = True

# 1. Job Families
jfs = [x for x in df_ma_global["Jobfamily"].dropna().unique() if x != "UNMAPPED"]
if jfs:
    jf_with_events = events_global["Jobfamily"].dropna().unique()
    if len(jf_with_events) > 0:
        success &= test_filter("Jobfamily (Active)", "Jobfamily", [jf_with_events[0]], events_global, df_ma_global)
    
    no_event_jfs = set(jfs) - set(jf_with_events)
    if no_event_jfs:
        success &= test_filter("Jobfamily (No Events)", "Jobfamily", [list(no_event_jfs)[0]], events_global, df_ma_global)
else:
    # If all are UNMAPPED, test UNMAPPED
    success &= test_filter("Jobfamily (UNMAPPED)", "Jobfamily", ["UNMAPPED"], events_global, df_ma_global)

# 2. Age Cohorts
cohorts = df_ma_global["Alterskohorte"].dropna().unique()
events_with_cohort = events_global.merge(df_ma_global[["PersNr", "Alterskohorte"]], left_on="persnr", right_on="PersNr", how="left")
active_cohorts = events_with_cohort["Alterskohorte"].dropna().unique()

if len(active_cohorts) > 0:
    success &= test_filter("Cohort (Active)", "Alterskohorte", [active_cohorts[0]], events_global, df_ma_global)

no_event_cohorts = set(cohorts) - set(active_cohorts)
if no_event_cohorts:
    success &= test_filter("Cohort (No Events)", "Alterskohorte", [list(no_event_cohorts)[0]], events_global, df_ma_global)

if success:
    print("\n[ALL TESTS PASSED]")
else:
    print("\n[SOME TESTS FAILED]")
    sys.exit(1)
