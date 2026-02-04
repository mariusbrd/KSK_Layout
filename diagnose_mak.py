
import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np

# Setup path
current_dir = Path(os.getcwd())
sys.path.append(str(current_dir))

from dataloader.loader import load_and_prepare_data, berechne_mak
from kpi_reference import get_current_stichtag
from components.sidebar import apply_filters
import streamlit as st

# Mock streamlit session state for filters
if "selected_org_units" not in st.session_state: st.session_state["selected_org_units"] = []
if "selected_job_families" not in st.session_state: st.session_state["selected_job_families"] = []
if "selected_date_range" not in st.session_state: st.session_state["selected_date_range"] = None
if "selected_genders" not in st.session_state: st.session_state["selected_genders"] = ["m", "w"]
if "selected_employment" not in st.session_state: st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit"]
if "selected_atz_status" not in st.session_state: st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]
if "view_mode" not in st.session_state: st.session_state["view_mode"] = "MAK"

print("--- DIAGNOSE MAK ---")

try:
    snapshot_df, _, _, _ = load_and_prepare_data()
    print(f"1. Snapshot loaded rows: {len(snapshot_df)}")
    
    # Calculate MAK on raw snapshot (Kompakt logic mostly)
    # Note: snapshot_df usually has 'FTE_assigned' or we can calc it.
    # In Kompakt it uses 'FTE_assigned' if available, or re-calcs.
    if "FTE_assigned" in snapshot_df.columns:
        mak_kompakt = snapshot_df["FTE_assigned"].sum()
        print(f"2. Raw Snapshot 'FTE_assigned' Sum: {mak_kompakt:.2f}")
    
    # Calculate using berechne_mak manually on snapshot
    snapshot_df["MAK_Manual"] = snapshot_df.apply(lambda row: berechne_mak(row), axis=1)
    print(f"3. Raw Snapshot 'berechne_mak' Sum: {snapshot_df['MAK_Manual'].sum():.2f}")

    # Apply filters (mimic page)
    df_ma_filtered = apply_filters(snapshot_df)
    print(f"4. Filtered rows: {len(df_ma_filtered)}")
    print(f"5. Filtered MAK Sum: {df_ma_filtered['MAK_Manual'].sum():.2f}")

    # --- FORECAST PREPROCESSING LOGIC ---
    print("\n--- FORECAST PREPROCESSING ---")
    
    # Remove Vacancies
    df_ma_filtered = df_ma_filtered.dropna(subset=["PersNr"])
    print(f"6. After dropping Vacancies (PersNr=NaN): rows={len(df_ma_filtered)}, MAK={df_ma_filtered.apply(lambda r: berechne_mak(r), axis=1).sum():.2f}")

    # Re-calculate MAK for aggregation
    df_ma_filtered["MAK_Calculated"] = df_ma_filtered.apply(lambda row: berechne_mak(row), axis=1)
    
    agg_dict = {
        "MAK_Calculated": "sum",
        "Sollarbeitszeit": "sum",
        "BsGrd": "sum" # Just to see
    }
    
    df_employee_agg = df_ma_filtered.groupby("PersNr", as_index=False).agg(agg_dict)
    print(f"7. Aggregated by PersNr: rows={len(df_employee_agg)}")
    print(f"8. Aggregated MAK Sum: {df_employee_agg['MAK_Calculated'].sum():.2f}")
    
    # Apply Fix Logic
    df_employee_agg["Sollarbeitszeit_Fixed"] = 39.0
    df_employee_agg["BsGrd_Fixed"] = df_employee_agg["MAK_Calculated"] * 100.0
    
    # Simulate Engine Calculation
    # Engine: raw_mak = (bsgrd / 100.0) * (soll / 39.0)
    
    # Scenario A: Using Fixed
    bsgrd = df_employee_agg["BsGrd_Fixed"]
    soll = df_employee_agg["Sollarbeitszeit_Fixed"]
    engine_mak_a = (bsgrd / 100.0) * (soll / 39.0)
    print(f"9. Engine MAK (with Fix): {engine_mak_a.sum():.2f}")

    # Scenario B: Without Fix (Double Discounting simulation)
    # If we had used aggregated sums...
    soll_sum = df_employee_agg["Sollarbeitszeit"]
    # BsGrd from aggregation typically isn't used directly but let's say it was:
    # If we calculated BsGrd = MAK * 100 but kept Soll Sum (e.g. 19.5)
    # Then factor = 19.5/39 = 0.5. 
    # Wait, if aggreg Sollarbeitszeit is SUM, it increases (39+39=78).
    # Discrepancy was LOWER mak.
    # Lower MAK happens if factor < 1.0.
    # Factor < 1.0 happens if Sollarbeitszeit < 39.0.
    # Aggregate Sollarbeitszeit (sum) should be >= individual.
    
    # What if we blindly took the first Sollarbeitszeit?
    # agg_dict = { "Sollarbeitszeit": "first" }
    # Person has 2 jobs: 50% (19.5h) and 50% (19.5h).
    # MAK = 0.5 + 0.5 = 1.0.
    # Soll (first) = 19.5.
    # Engine: (100 / 100) * (19.5 / 39) = 1.0 * 0.5 = 0.5.
    # THIS HALVES THE MAK!
    # This matches the symptom (993 -> ~840 is not exactly half, but huge drop).
    
    # Let's verify what my previous code did. 
    # My aggregated logic (before Fix) had "Sollarbeitszeit": "sum".
    
except Exception as e:
    print(e)
