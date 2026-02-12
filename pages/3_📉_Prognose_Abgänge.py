"""
Streamlit page: Abgänge Prognose.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Dict
import sys
import os
import re
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

# Add project root or src to path
BASE_PATH = Path(__file__).resolve().parents[1]
SRC_PATH = BASE_PATH / "src"
if SRC_PATH.exists():
    sys.path.append(str(SRC_PATH))
else:
    sys.path.append(str(BASE_PATH))

from kpi_reference import get_current_stichtag  # Import dynamic Stichtag

from abgaenge import (
    default_params,
    build_params_from_ui,
    run_forecast_abgaenge,
    aggregate_forecast_results,
    validate_outputs,
    build_charts,
    to_csv_bytes,
)

# Shared Components
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from dataloader.cluster_manager import is_clustering_active
from components.sidebar import render_global_filters, apply_filters


def main():
    st.title("📉 Prognose: Abgänge")
    st.write("Prognose von Abgängen (ATZ, Rente, Kündigung, Ruhend) mit klarer Trennung von MAK und Headcount.")

    try:
        # 1. Load Central Data (Consistent with Kompakt)
        # Loader automatically picks up 'global_uploads' from session_state (see loader.py)
        snapshot_df, history_df, _, _ = load_and_prepare_data()

        # 2. Render Sidebar Filters (Standard Dashboard Logic)
        render_global_filters(snapshot_df, history_df)
        
        # 3. GLOBAL DATA PREPARATION (Filters applied LATER for View)
        # We skip apply_filters(snapshot_df) here to ensure Global Forecast.
        df_ma = snapshot_df.copy()

        # 4. Load ATZ Details (needed for engine phases)
        # Get uploads from session_state for specific loading
        global_uploads = st.session_state.get("global_uploads", {})
        up_ma_arg = global_uploads.get("Mitarbeiter")
        up_atz_arg = global_uploads.get("ATZ")
        up_pl_arg = global_uploads.get("Planstellen")
        
        if up_ma_arg: up_ma_arg.seek(0)
        if up_atz_arg: up_atz_arg.seek(0)
        if up_pl_arg: up_pl_arg.seek(0)
        
        df_atz = load_atz_data_cached(str(BASE_PATH), up_ma_arg, up_atz_arg, up_pl_arg)
        
        # 5. Preprocessing for Forecast Engine (GLOBAL)
        # The snapshot_df is position-level data. Employees with multiple positions
        # contribute MAK from each position (e.g., 2x 50% = 1.0 FTE total).
        # The forecast engine expects employee-level data (1 row per person).
        
        # Remove Vacancies
        df_ma = df_ma.dropna(subset=["PersNr"])
        
        # Calculate MAK for each position (same logic as Kompakt)
        from dataloader.loader import berechne_mak, calculate_mak_vectorized
        
        # Get ATZ FR employees if available (for MAK calculation)
        atz_fr_persnr_set = set()
        if not df_atz.empty:
            if "PersNr" in df_atz.columns and "Phase" in df_atz.columns:
                # People currently in Freistellungsphase have MAK = 0
                stichtag_ts = pd.Timestamp(get_current_stichtag())
                atz_fr = df_atz[
                    (df_atz["Phase"] == "FR") &
                    (df_atz["Beginn"] <= stichtag_ts) &
                    (df_atz["Ende"] >= stichtag_ts)
                ]
                if not atz_fr.empty:
                    atz_fr_persnr_set = set(atz_fr["PersNr"].dropna().astype(str).unique())
        
        # Calculate MAK for each row (position) - Vectorized!
        df_ma = calculate_mak_vectorized(df_ma, atz_fr_persnr_set)
        
        # Aggregate by employee: sum MAK, keep first occurrence of other attributes
        agg_dict = {
            "MAK_Calculated": "sum",  # Sum MAK across all positions (from calculate_mak_vectorized)
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Sollarbeitszeit": "sum",  # Sum work hours across positions
            "Organisationseinheit": "first", # Preserve OrgUnit for Analytics
        }

        # Optional: include other columns if they exist
        for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St", "OE-Cluster", "JF-Cluster"]:
            if col in df_ma.columns:
                agg_dict[col] = "first"
        
        # Performance Optimization: Aggregate using groupby
        df_employee_agg = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
        
        # Standardize column name for subsequent logic
        df_employee_agg["mak"] = df_employee_agg["MAK_Calculated"]
        # IMPORTANT: Keep MAK_Calculated for forecast engine (it checks for this specific column)
        # df_employee_agg = df_employee_agg.rename(columns={"MAK_Calculated": "mak"})
        
        # 1. Ensure Sollarbeitszeit is present (fallback 39.0)
        df_employee_agg["Sollarbeitszeit"] = df_employee_agg["Sollarbeitszeit"].fillna(39.0)
        
        # Backcalculate BsGrd from aggregated MAK for engine compatibility
        # Forecast Engine Logic: MAK = (BsGrd/100) * (Soll/39)
        # We want: MAK = MAK_Calculated
        # So we set Soll = 39.0 (Factor=1) and BsGrd = MAK_Calculated * 100
        
        # 1. Neutralize Soll-Factor in Engine
        df_employee_agg["Sollarbeitszeit"] = 39.0
        
        # 2. Set BsGrd to match desired MAK exactly
        # FIX: If 'mak' is 0.0 (e.g. Is_Vacant), try to derive from BsGrd/Soll if available
        # This handles cases where user wants to see "Potential" loss of a slot even if currently vacant/ruhend
        mask_zero = (df_employee_agg["mak"] <= 0)
        if mask_zero.any():
            if "BsGrd" in df_employee_agg.columns:
                 potential_mak = df_employee_agg.loc[mask_zero, "BsGrd"] / 100.0
                 df_employee_agg.loc[mask_zero, "mak"] = potential_mak.fillna(0.0)
                 # CRITICAL: Also update MAK_Calculated because forecast.py checks this specifically!
                 df_employee_agg.loc[mask_zero, "MAK_Calculated"] = df_employee_agg.loc[mask_zero, "mak"]

        df_employee_agg["BsGrd"] = df_employee_agg["mak"] * 100.0
        
        df_employee_agg["BsGrd"] = df_employee_agg["mak"] * 100.0
        
        # Use aggregated data for forecast
        df_ma = df_employee_agg
        
    except FileNotFoundError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(f"Fehler beim Laden/Filtern der Daten: {e}")
        return
    
    # df_ma is now the GLOBAL Aggregated Dataset.

    default_start = get_current_stichtag().date()
    default_end = date(default_start.year + 2, default_start.month, default_start.day)

    # ── Parametrierung (kompakt) ──
    params = default_params()

    # ── Settings Accordion ──────────────────────────────────────────
    with st.expander("⚙️ Prognose-Einstellungen", expanded=True):
        # ── Row 1: Base Settings (horizontal) ──
        st.markdown("##### 📅 Zeitraum & Basis")
        submit = False
        bc1, bc2, bc3, bc4 = st.columns(4)
        with bc1:
            ist_stichtag = st.date_input("Ist-Stichtag", value=default_start)
        with bc2:
            forecast_end_date = st.date_input("Prognose-Ende", value=default_end)
        with bc3:
            freq_label = st.selectbox("Frequenz", options=["Monat", "Quartal"], index=0)
        with bc4:
            random_seed = st.number_input("Random Seed", value=int(params["random_seed"]), step=1)

        st.markdown("---")

        # ── Row 2: Component Toggles (horizontal) ──
        st.markdown("##### 🧩 Aktive Komponenten")
        cc1, cc2, cc3, cc4 = st.columns(4)
        with cc1:
            comp_atz = st.checkbox("ATZ", value=params["components"]["atz"])
        with cc2:
            comp_ret = st.checkbox("Rente", value=params["components"]["retirement"])
        with cc3:
            comp_quit = st.checkbox("Kündigung", value=params["components"]["quit"])
        with cc4: # Corrected col index
            comp_ruhend = st.checkbox("Ruhend", value=params["components"]["ruhend"])

        st.markdown("---")

        # ── Row 3: Detail Parameters (sub-expanders) - OUTSIDE FORM for interactivity ──
        st.markdown("##### 🔧 Detail-Parameter")

        with st.expander("ATZ-Parameter"):
            # ── ATZ Row 1: General Constraints ──
            ac1, ac2, ac3, ac4, ac5 = st.columns(5)
            with ac1:
                new_atz_base = st.slider("Neue Fälle (Basis)", min_value=0.0, max_value=0.5, value=float(params["atz"].get("new_atz_rate", 0.05)), step=0.005, format="%.3f", help="Basis-Anteil der berechtigten Mitarbeiter, die pro Jahr in ATZ gehen.", key="slide_atz_base")
                params["atz"]["new_atz_rate"] = new_atz_base
            with ac2:
                eligible_age = st.number_input("Mindestalter", value=int(params["atz"]["atz_eligible_age_min"]), step=1, key="num_atz_age_min")
                params["atz"]["atz_eligible_age_min"] = eligible_age
            with ac3:
                eligible_age_max = st.number_input("Höchstalter", value=int(params["atz"]["atz_eligible_age_max"]), step=1, key="num_atz_age_max")
                params["atz"]["atz_eligible_age_max"] = eligible_age_max
            with ac4:
                ar_years = st.number_input("AR-Dauer (Jahre)", value=float(params["atz"]["atz_duration_ar_years"]), step=0.5, key="num_atz_ar_years")
                params["atz"]["atz_duration_ar_years"] = ar_years
            with ac5:
                fr_years = st.number_input("FR-Dauer (Jahre)", value=float(params["atz"]["atz_duration_fr_years"]), step=0.5, key="num_atz_fr_years")
                params["atz"]["atz_duration_fr_years"] = fr_years

            st.divider()

            # ── ATZ Row 2: Matrix Controls ──
            bc1, bc2 = st.columns([1, 1])
            with bc1:
                use_atz_matrix = st.checkbox(
                    "Detaillierte ATZ-Matrix verwenden", 
                    value=params["atz"].get("use_atz_matrix", False),
                    help="Wenn aktiviert, wird die nachfolgende Matrix für die Eintrittswahrscheinlichkeiten genutzt.",
                    key="chk_use_atz_matrix_live"
                )
                params["atz"]["use_atz_matrix"] = use_atz_matrix
            with bc2:
                atz_dim = st.radio(
                    "Dimension für ATZ",
                    options=["JobFamily", "OrgUnit"],
                    index=0 if params["atz"].get("atz_dimension", "JobFamily") == "JobFamily" else 1,
                    help="Wählen Sie die Dimension für die ATZ-Eintrittswahrscheinlichkeiten.",
                    disabled=not use_atz_matrix,
                    horizontal=True,
                    key="rad_atz_dim_live"
                )
                params["atz"]["atz_dimension"] = atz_dim

            # ── ATZ Row 3: Matrix Editor ──
            st.caption(f"Matrix: {atz_dim} (Eintrittswahrscheinlichkeit für berechtigte MA)")
            
            if atz_dim == "OrgUnit":
                atz_col_name = "Organisationseinheit"
            else:
                atz_col_name = "Jobfamily"
            
            atz_unique_vals = []
            if atz_col_name in df_ma.columns:
                atz_unique_vals = sorted([str(x) for x in df_ma[atz_col_name].dropna().unique()])
            
            current_atz_matrix = params["atz"].get("atz_matrix", {})
            atz_dim_items = ["Default"] + atz_unique_vals
            atz_editor_data = []

            for val in atz_dim_items:
                # Try to get value from old cohort-based structure or new flat structure
                rate = current_atz_matrix.get(str(val))
                if rate is None:
                    # Fallback to "alter_55_plus" if it was old matrix
                    rate = current_atz_matrix.get("alter_55_plus", {}).get(str(val))
                if rate is None:
                    rate = current_atz_matrix.get("Default", new_atz_base)
                
                atz_editor_data.append({
                    atz_dim: val,
                    "Wahrscheinlichkeit": float(rate)
                })
            
            df_atz_matrix = pd.DataFrame(atz_editor_data).set_index(atz_dim)
            
            edited_atz_df = st.data_editor(
                df_atz_matrix,
                use_container_width=True,
                height=min(400, 50 + len(atz_dim_items) * 35),
                key="atz_matrix_editor_live",
                disabled=not use_atz_matrix,
                column_config={
                    "Wahrscheinlichkeit": st.column_config.NumberColumn(
                        "Wahrscheinlichkeit",
                        min_value=0.0, max_value=1.0, step=0.01, format="%.2f"
                    )
                }
            )
            
            new_atz_matrix = {}
            for dim_val, row in edited_atz_df.iterrows():
                new_atz_matrix[str(dim_val)] = float(row["Wahrscheinlichkeit"])
            params["atz"]["atz_matrix"] = new_atz_matrix

        with st.expander("Renten-Parameter"):
            rc1, rc2 = st.columns(2)
            with rc1:
                rent65 = st.slider("Renteneintritt 65+", min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_65"]), step=0.05, key="slide_rent_65")
                params["retirement"]["rent_rate_65"] = rent65
            with rc2:
                rent60 = st.slider("Frühverrentung 60-64", min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_60_65"]), step=0.05, key="slide_rent_60")
                params["retirement"]["rent_rate_60_65"] = rent60

        with st.expander("Kündigungs-Parameter", expanded=False):
            # ── Controls Row ──
            c1, c2, c3 = st.columns([3, 3, 2])
            
            with c1:
                quit_base = st.slider(
                    "Basisrate p.a.", 
                    min_value=0.0, max_value=0.5, 
                    value=float(params["quit"]["quit_rate_base"]), 
                    step=0.01, 
                    help="Globale Kündigungsrate pro Jahr.",
                    key="slide_quit_base_live"
                )
                params["quit"]["quit_rate_base"] = quit_base
            with c2:
                st.write("") # Alignment
                use_quit_matrix = st.checkbox(
                    "Detaillierte Kündigungsmatrix verwenden", 
                    value=params["quit"].get("use_quit_matrix", True),
                    help="Wenn aktiviert, wird die nachfolgende Matrix für die Kündigungswahrscheinlichkeiten genutzt.",
                    key="chk_use_quit_matrix_live"
                )
                params["quit"]["use_quit_matrix"] = use_quit_matrix
            with c3:
                quit_dim = st.radio(
                    "Dimension",
                    options=["JobFamily", "OrgUnit"],
                    index=0 if params["quit"].get("quit_dimension", "JobFamily") == "JobFamily" else 1,
                    help="Wählen Sie die Dimension für die Kündigungswahrscheinlichkeiten.",
                    disabled=not use_quit_matrix,
                    horizontal=True,
                    key="rad_quit_dim_live"
                )
                params["quit"]["quit_dimension"] = quit_dim

            st.divider()

            # ── Matrix Row ──
            st.caption(f"Matrix: {quit_dim} × Alter")
            
            # 1. Determine dimension values
            if quit_dim == "OrgUnit":
                col_name = "Organisationseinheit"
            else:
                col_name = "Jobfamily"
            
            unique_vals = []
            if col_name in df_ma.columns:
                unique_vals = sorted([str(x) for x in df_ma[col_name].dropna().unique()])
            
            age_cohorts = ["alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus"]
            age_labels = {"alter_unter_30": "u30", "alter_30_45": "30-45", "alter_45_55": "45-55", "alter_55_plus": "ü55"}

            # 2. Build DataFrame
            current_quit_matrix = params["quit"].get("quit_matrix", {})
            dim_items = ["Default"] + unique_vals
            editor_data = []

            for val in dim_items:
                row_data = {quit_dim: val}
                for cohort in age_cohorts:
                    # Access the nested dictionary for the cohort, then the value for the dimension item
                    # Fallback to 'Default' for the cohort if item not found, then to quit_base
                    cohort_rates = current_quit_matrix.get(cohort, {})
                    rate = cohort_rates.get(str(val), cohort_rates.get("Default", quit_base))
                    row_data[cohort] = float(rate)
                editor_data.append(row_data)
            
            df_matrix = pd.DataFrame(editor_data).set_index(quit_dim)
            
            # 3. Render Editor
            # Correct explicit syntax
            col_conf = {
                quit_dim: st.column_config.TextColumn(f"{quit_dim}", disabled=True)
            }
            for c in age_cohorts:
                col_conf[c] = st.column_config.NumberColumn(
                    age_labels.get(c, c),
                    min_value=0.0, max_value=1.0, step=0.01, format="%.2f"
                )
            
            edited_df = st.data_editor(
                df_matrix,
                use_container_width=True,
                height=min(400, 50 + len(dim_items) * 35),
                disabled=not use_quit_matrix,
                key="quit_matrix_editor_live_fixed",
                column_config=col_conf
            )
            
            # 4. Save back
            new_quit_matrix = {c: {} for c in age_cohorts}
            for dim_val, row in edited_df.iterrows():
                for c in age_cohorts:
                    new_quit_matrix[c][str(dim_val)] = float(row[c])
            params["quit"]["quit_matrix"] = new_quit_matrix

        with st.expander("Ruhend-Parameter"):
            hc1, hc2, hc3 = st.columns(3)
            with hc1:
                ruhend_new = st.number_input("Neue Fälle / Jahr", value=int(params["ruhend"]["ruhend_new_cases_per_year"]), step=1, key="num_ruhend_new")
                params["ruhend"]["ruhend_new_cases_per_year"] = ruhend_new
            with hc2:
                ruhend_return = st.slider("Rückkehrquote p.a.", min_value=0.0, max_value=1.0, value=float(params["ruhend"]["ruhend_return_rate"]), step=0.05, key="slide_ruhend_ret_live")
                params["ruhend"]["ruhend_return_rate"] = ruhend_return
            with hc3:
                ruhend_duration = st.number_input("Ø Dauer (Monate)", value=int(params["ruhend"]["ruhend_avg_duration_months"]), step=1, key="num_ruhend_dur_live")
                params["ruhend"]["ruhend_avg_duration_months"] = ruhend_duration

    # ── Lower Action Button ──
    st.write("")
    if st.button("🚀 Prognose mit diesen Parametern berechnen", use_container_width=True, key="btn_run_bottom"):
        submit = True # Override submit for this run


    has_global_result = "abgaenge_global_result" in st.session_state

    if not submit and not has_global_result:
        st.info("⬆️ Parameter oben einstellen und Prognose berechnen.")
        return

    if forecast_end_date <= ist_stichtag:
        st.error("Prognose-Ende muss nach dem Ist-Stichtag liegen.")
        return

    ui_state = {
        "components": {
            "atz": comp_atz,
            "retirement": comp_ret,
            "quit": comp_quit,
            "ruhend": comp_ruhend,
        },
        "atz": {
            "new_atz_rate": new_atz_base,
            "atz_eligible_age_min": eligible_age,
            "atz_eligible_age_max": eligible_age_max,
            "atz_duration_ar_years": ar_years,
            "atz_duration_fr_years": fr_years,
            "use_atz_matrix": use_atz_matrix,
            "atz_dimension": atz_dim,
            "atz_matrix": new_atz_matrix,
        },
        "retirement": {
            "rent_rate_65": rent65,
            "rent_rate_60_65": rent60,
        },
        "quit": {
            "quit_rate_base": quit_base,
            "use_quit_matrix": use_quit_matrix,
            "quit_dimension": quit_dim,
            "quit_matrix": new_quit_matrix,
        },
        "ruhend": {
            "ruhend_new_cases_per_year": ruhend_new,
            "ruhend_return_rate": ruhend_return,
            "ruhend_avg_duration_months": ruhend_duration,
        },
        "random_seed": random_seed,
    }

    params = build_params_from_ui(ui_state)
    # Save params for other pages (e.g. Zugänge: Fill Vacancies)
    st.session_state["abgaenge_params"] = params
    freq = "M" if freq_label == "Monat" else "Q"
    
    # ── Prognose Start ──────────────────────────────────────────────
    try:
        # P01: Run Global Forecast
        # P01: Run Global Forecast or Load from Session
        if submit:
             # Run fresh forecast
             global_result = run_forecast_abgaenge(
                 df_ma=df_ma, 
                 df_atz=df_atz,
                 start_date=pd.Timestamp(ist_stichtag),
                 end_date=pd.Timestamp(forecast_end_date),
                 freq=freq,
                 params=params,
             )
             # Save to Session State (for other pages like Zugänge)
             st.session_state["abgaenge_global_result"] = global_result
             st.session_state["abgaenge_params"] = params
        else:
             global_result = st.session_state["abgaenge_global_result"]
             # Note: params are re-built from UI but forecast result uses old params.
             # This is acceptable for "View Filtering".
             # If consistency becomes an issue, we could store params with result and warn if diff.
        
        # P02: Apply Sidebar Filters (View Only)
        # Use snapshot_df (Position Level) to allow filtering by position attributes
        # (e.g. OrgUnit, Planstelle) even if aggregation picked "First".
        df_filtered_rows = apply_filters(snapshot_df)

        if df_filtered_rows.empty:
            st.warning("⚠️ Keine Daten nach Filterung verfügbar.")
            return

        # P03: Re-Aggregate Filtered Data (Person Level for View)
        # Same aggregation logic as global to ensure consistent MAK sum
        # We must use the SAME agg_dict defined above.
        # Check if agg_dict is available in scope (it is defined in 'main', but scope is tricky).
        # We re-define minimal agg_dict or rely on it being present.
        # Since agg_dict was in a try/except earlier, it might be safer to rebuild it.
        
        view_agg_dict = {
            "MAK_Calculated": "sum",
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Sollarbeitszeit": "sum",
            "Organisationseinheit": "first",
        }
        for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St"]:
             if col in df_filtered_rows.columns:
                 view_agg_dict[col] = "first"

        df_view_agg = df_filtered_rows.groupby("PersNr", as_index=False).agg(view_agg_dict)
        
        # Prepare for Aggregator (needs 'mak' column)
        df_view_agg["mak"] = pd.to_numeric(df_view_agg["MAK_Calculated"], errors="coerce").fillna(0.0)

        # P04: Filter Events – Direct Attribute Filtering ("Zoom")
        # Events carry their own OrgUnit/Jobfamily attributes from the simulation.
        # We filter directly on these attributes (not via PersNr matching from snapshot),
        # ensuring the filtered view shows exactly the same events visible in the global charts.
        events_global = global_result["events_person_level"]
        
        if events_global.empty:
            events_view = pd.DataFrame()
        else:
            events_view = events_global.copy()
            
            # 1. OrgUnit Filter: direct on event attribute (Organisationseinheit = unique name)
            # FIX: Kürzel OrgEinheit is NOT unique (e.g. 591 = "Beratungs-Center Herrenberg"
            # AND "Akquisepool Herrenberg"). Use Organisationseinheit for exact matching.
            selected_orgs = st.session_state.get("selected_org_units", [])
            if selected_orgs and "Organisationseinheit" in events_view.columns:
                events_view = events_view[events_view["Organisationseinheit"].isin(selected_orgs)]
            
            # 2. Jobfamily Filter: direct on event attribute
            selected_jf = st.session_state.get("selected_jobfamilies", [])
            if selected_jf and "Jobfamily" in events_view.columns:
                events_view = events_view[events_view["Jobfamily"].isin(selected_jf)]
            
            # 3. Other filters (Geschlecht, Arbeitszeit, ATZ-Status etc.) are NOT
            #    present in event attributes. For these, fall back to PersNr matching
            #    from the filtered snapshot.
            has_non_attr_filters = any([
                st.session_state.get("selected_genders", []),
                st.session_state.get("selected_employment", []),
                st.session_state.get("selected_atz_status", []),
                st.session_state.get("selected_cohorts", []),
                st.session_state.get("selected_education", []),
            ])
            if has_non_attr_filters:
                valid_ids = set(df_view_agg["PersNr"].astype(str))
                events_view = events_view[events_view["persnr"].isin(valid_ids)]


            
        # P05: Adjust Events (Clamp MAK Loss to View Reality)
        # If Global Event implies -1.0 loss, but View MAK is 0.5, set change to -0.5.
        if not events_view.empty:
            # Create Lookup Map for View MAK
            # Use Index for speed
            df_view_agg_indexed = df_view_agg.set_index("PersNr")
            if "mak" in df_view_agg_indexed.columns:
                pmak_map = df_view_agg_indexed["mak"].to_dict()
                
                # Iterate and adjust 'mak_change' if negative (Exit/Transition)
                # We do this row by row or apply. Row by row on events (usually small n) is readable.
                # But we use apply for conciseness.
                def adjust_mak_loss(row):
                    pid = str(row["persnr"])
                    if row["mak_change"] < 0: # Is a loss event
                        current_view_mak = float(pmak_map.get(pid, 0.0))
                        # Return negative of current view mak (Full Exit from View)
                        # Ensure we don't gain MAK (min(0, ...)? No, return -value)
                        return -abs(current_view_mak)
                    return row["mak_change"]

                events_view["mak_change"] = events_view.apply(adjust_mak_loss, axis=1)

        # P06: Re-Aggregate KPIs for View
        forecast_kpis = aggregate_forecast_results(
            df_initial=df_view_agg,
            events_df=events_view,
            start_date=pd.Timestamp(ist_stichtag),
            end_date=pd.Timestamp(forecast_end_date),
            freq=freq,
            params=params
        )

        # P07: Finalize Result for Charts
        result = global_result.copy()
        result["forecast_kpis"] = forecast_kpis
        result["events_person_level"] = events_view
        
    except Exception as e:
        st.error(f"Fehler in der Prognose calculation: {e}")
        return

    # Use 'result' for subsequent chart rendering (lines below use forecast_kpis, events)
    forecast_kpis = result["forecast_kpis"]

    events = result["events_person_level"]
    
    # Feature: Enrich events with Organisationseinheit (Last Known)
    # Feature: Enrich events with Organisationseinheit (Last Known)
    # df_ma has aggregated info per employee, including OrgUnit (added to agg_dict)
    
    if not events.empty:
        # Check if already present (from forecast)
        if "Organisationseinheit" not in events.columns and "Organisationseinheit" in df_ma.columns:
            # P12: Robust enrichment with deduplication
            lookup_oe = df_ma[["PersNr", "Organisationseinheit"]].copy()
            lookup_oe["key"] = lookup_oe["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            lookup_oe = lookup_oe.drop_duplicates(subset=["key"])
            
            events["key"] = events["persnr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            
            events = events.merge(
                lookup_oe[["key", "Organisationseinheit"]],
                on="key",
                how="left"
            )
            events.drop(columns=["key"], inplace=True)

        if "Organisationseinheit" in events.columns:
            events["Organisationseinheit"] = events["Organisationseinheit"].fillna("Unbekannt")
            
        # Feature: Enrich events with OE-Cluster
        if "OE-Cluster" not in events.columns and "OE-Cluster" in df_ma.columns:
            # P12: Robust enrichment with deduplication
            lookup_cluster = df_ma[["PersNr", "OE-Cluster"]].copy()
            lookup_cluster["key"] = lookup_cluster["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            lookup_cluster = lookup_cluster.drop_duplicates(subset=["key"])
            
            # Ensure key is present (might have been dropped above)
            events["key"] = events["persnr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            
            events = events.merge(
                lookup_cluster[["key", "OE-Cluster"]],
                on="key",
                how="left"
            )
            events.drop(columns=["key"], inplace=True)
            events["OE-Cluster"] = events["OE-Cluster"].fillna("Unclustered")

        # Feature: Enrich events with JF-Cluster
        if "JF-Cluster" not in events.columns and "JF-Cluster" in df_ma.columns:
            # P12: Robust enrichment with deduplication
            lookup_cluster_jf = df_ma[["PersNr", "JF-Cluster"]].copy()
            lookup_cluster_jf["key"] = lookup_cluster_jf["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            lookup_cluster_jf = lookup_cluster_jf.drop_duplicates(subset=["key"])
            
            events["key"] = events["persnr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            
            events = events.merge(
                lookup_cluster_jf[["key", "JF-Cluster"]],
                on="key",
                how="left"
            )
            events.drop(columns=["key"], inplace=True)
            events["JF-Cluster"] = events["JF-Cluster"].fillna("Unclustered")
            
            # FALLBACK: If PersNr mapping failed, try map via (Org, Pos) or Pos
            mask_unclustered_jf = events["JF-Cluster"] == "Unclustered"
            if mask_unclustered_jf.any():
                from dataloader.cluster_manager import load_cluster_mappings
                _, jf_map = load_cluster_mappings()
                if jf_map:
                    first_key = next(iter(jf_map.keys()), None)
                    if isinstance(first_key, tuple):
                         # Combination mapping
                         if "Organisationseinheit" in events.columns and "Planstelle" in events.columns:
                             s_org = events.loc[mask_unclustered_jf, "Organisationseinheit"].astype(str).str.strip()
                             s_pos = events.loc[mask_unclustered_jf, "Planstelle"].astype(str).str.strip()
                             keys = list(zip(s_org, s_pos))
                             events.loc[mask_unclustered_jf, "JF-Cluster"] = [jf_map.get(k, "Unclustered") for k in keys]
                    elif "Planstelle" in events.columns:
                         events.loc[mask_unclustered_jf, "JF-Cluster"] = events.loc[mask_unclustered_jf, "Planstelle"].map(jf_map).fillna("Unclustered")

        # Fix Arrow Error: Mixed types in persnr
        if "persnr" in events.columns:
            events["persnr"] = events["persnr"].astype(str)

    # ── Ergebnisse ──────────────────────────────────────────────────
    st.divider()

    # KPI Metrics Header
    if not forecast_kpis.empty:
        first = forecast_kpis.iloc[0]
        last = forecast_kpis.iloc[-1]

        exits_total = int(forecast_kpis["exit_count"].sum())
        mak_loss_total = float(forecast_kpis["mak_loss_gross"].sum())
        avg_headcount = float(forecast_kpis[["headcount_start", "headcount_end"]].mean(axis=1).mean())
        abgangsquote = (exits_total / avg_headcount) if avg_headcount > 0 else 0.0

        st.markdown("### 🏆 Kennzahlen (Management-Summary)")
        m1, m2, m3 = st.columns([1, 1, 1])
        with m1:
            st.metric("Abgänge gesamt (Köpfe)", f"{exits_total}")
        with m2:
            st.metric("Kapazitätsverlust (MAK)", f"{mak_loss_total:.1f}")
        with m3:
            st.metric("Prognostizierte Fluktuation", f"{abgangsquote*100:.1f}%")
        
        with st.expander("🔍 Details: Bestandsentwicklung (Start vs. Ende)", expanded=False):
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Headcount Start", f"{int(first['headcount_start'])}")
            d2.metric("Headcount Ende", f"{int(last['headcount_end'])}", delta=int(last["headcount_delta"]))
            d3.metric("MAK Start", f"{first['mak_start']:.1f}")
            d4.metric("MAK Ende", f"{last['mak_end']:.1f}", delta=f"{last['mak_delta']:.1f}")

    charts = build_charts(forecast_kpis, events)

    tab1, tab2, tab3 = st.tabs(["📊 Überblick & Trends", "🎯 Treiber Details", "📋 Personenlisten / Export"])

    with tab1:
        # ── Section 1: zeitliche Entwicklung & Gesamt-Struktur ──
        st.markdown("### 📈 Bestandsentwicklung (Trend)")
        st.plotly_chart(charts.get("line_headcount_mak"), use_container_width=True)
        st.caption("Die obige Grafik zeigt die Entwicklung von Headcount (Anzahl Personen) und MAK (Kapazität) über den Prognosezeitraum.")

        st.plotly_chart(charts.get("bar_reasons_total"), use_container_width=True)
        st.caption("Gesamtanzahl der prognostizierten Abgänge nach Grund für den gesamten Zeitraum.")

        st.divider()

        # ── Section 2: Struktur der Abgänge ──
        st.markdown("### 🧬 Abgangsgründe")
        st.plotly_chart(charts.get("bar_abgaenge_reasons"), use_container_width=True)
        st.caption("Verteilung der Abgänge nach Ursache (Kündigung, Rente, ATZ etc.).")

        st.divider()

        if "Organisationseinheit" in events.columns:
            st.markdown("### 🏢 Top 15 Organisationseinheiten")
            
            # Aggregate
            exclude_units = ["Unbekannt", None]
            org_events = events[~events["Organisationseinheit"].isin(exclude_units)]
            if not org_events.empty:
                org_stats = org_events.groupby("Organisationseinheit").size().reset_index(name="Abgänge")
                org_stats = org_stats.sort_values("Abgänge", ascending=True).tail(15) 
                
                fig_org = px.bar(
                    org_stats, 
                    x="Abgänge", 
                    y="Organisationseinheit", 
                    orientation="h",
                    title="Abgänge nach Organisationseinheit (Top 15)",
                    text="Abgänge",
                    color="Abgänge",
                    color_continuous_scale="Reds"
                )
                fig_org.update_layout(yaxis_title=None, showlegend=False, height=600)
                st.plotly_chart(fig_org, use_container_width=True)

        st.divider()

        # ── Section 4: Cluster-Analyse (OE) ──
        st.markdown("### 🧩 Analyse nach OE-Clustern")
        if is_clustering_active():
            if "OE-Cluster" in events.columns:
                # Get full set of clusters for consistent Y-axis
                all_clusters = sorted(df_ma["OE-Cluster"].unique().tolist())
                
                # Filter for Headcount departures (Upper Chart)
                cluster_events_h = events[events["headcount_change"] < 0].copy()
                
                # Filter for MAK losses (Lower Chart) - captures ATZ-FR etc.
                cluster_events_m = events[events["mak_change"] < 0].copy()
                
                # Layout: Vertical (untereinander)
                
                # Chart 1: Kopfabgänge
                st.markdown("#### 👤 Abgänge nach Personen (OE)")
                c_stats_h = cluster_events_h.groupby("OE-Cluster").size().reindex(all_clusters, fill_value=0).reset_index(name="Abgänge")
                c_stats_h = c_stats_h.sort_values("Abgänge", ascending=True)
                
                fig_h = px.bar(
                    c_stats_h,
                    x="Abgänge",
                    y="OE-Cluster",
                    orientation="h",
                    title="Kopfabgänge (Anzahl Personen)",
                    text="Abgänge",
                    color="Abgänge",
                    color_continuous_scale="Reds"
                )
                fig_h.update_layout(yaxis_title=None, showlegend=False, height=600)
                st.plotly_chart(fig_h, use_container_width=True)

                st.divider()

                # Chart 2: MAK-Abgänge (Capacity Loss)
                st.markdown("#### 📊 Abgänge nach Kapazität (MAK) (OE)")
                
                if "mak_change" in cluster_events_m.columns:
                    cluster_events_m["mak_loss"] = cluster_events_m["mak_change"].abs()
                    c_stats_m = cluster_events_m.groupby("OE-Cluster")["mak_loss"].sum().reindex(all_clusters, fill_value=0.0).reset_index(name="MAK-Verlust")
                    c_stats_m = c_stats_m.sort_values("MAK-Verlust", ascending=True)
                    

                    fig_m = px.bar(
                        c_stats_m,
                        x="MAK-Verlust",
                        y="OE-Cluster",
                        orientation="h",
                        title="Kapazitätsverlust (MAK)",
                        text_auto=".1f",
                        color="MAK-Verlust",
                        color_continuous_scale="Reds"
                    )
                    fig_m.update_layout(yaxis_title=None, showlegend=False, height=600)
                    st.plotly_chart(fig_m, use_container_width=True)
                else:
                    st.info("MAK-Daten für Cluster nicht verfügbar.")
            else:
                st.warning("OE-Cluster Spalte nicht im Datensatz gefunden.")

            st.divider()

            # ── Section 5: Cluster-Analyse (JF) ──
            st.markdown("### 🧩 Analyse nach Job-Family-Clustern")
            if "JF-Cluster" in events.columns:
                # Get full set of clusters for consistent Y-axis
                all_jf_clusters = sorted(df_ma["JF-Cluster"].unique().tolist())
                
                # Filter for Headcount departures 
                cluster_events_h_jf = events[events["headcount_change"] < 0].copy()
                
                # Filter for MAK losses
                cluster_events_m_jf = events[events["mak_change"] < 0].copy()

                # Chart 1: Kopfabgänge JF
                st.markdown("#### 👤 Abgänge nach Personen (JF)")
                c_stats_h_jf = cluster_events_h_jf.groupby("JF-Cluster").size().reindex(all_jf_clusters, fill_value=0).reset_index(name="Abgänge")
                c_stats_h_jf = c_stats_h_jf.sort_values("Abgänge", ascending=True)
                
                fig_h_jf = px.bar(
                    c_stats_h_jf,
                    x="Abgänge",
                    y="JF-Cluster",
                    orientation="h",
                    title="Kopfabgänge Job-Family (Anzahl Personen)",
                    text="Abgänge",
                    color="Abgänge",
                    color_continuous_scale="Reds"
                )
                fig_h_jf.update_layout(yaxis_title=None, showlegend=False, height=600)
                st.plotly_chart(fig_h_jf, use_container_width=True)

                st.divider()

                # Chart 2: MAK-Abgänge JF
                st.markdown("#### 📊 Abgänge nach Kapazität (MAK) (JF)")
                if "mak_change" in cluster_events_m_jf.columns:
                    cluster_events_m_jf["mak_loss"] = cluster_events_m_jf["mak_change"].abs()
                    c_stats_m_jf = cluster_events_m_jf.groupby("JF-Cluster")["mak_loss"].sum().reindex(all_jf_clusters, fill_value=0.0).reset_index(name="MAK-Verlust")
                    c_stats_m_jf = c_stats_m_jf.sort_values("MAK-Verlust", ascending=True)
                    
                    fig_m_jf = px.bar(
                        c_stats_m_jf,
                        x="MAK-Verlust",
                        y="JF-Cluster",
                        orientation="h",
                        title="Kapazitätsverlust Job-Family (MAK)",
                        text_auto=".1f",
                        color="MAK-Verlust",
                        color_continuous_scale="Reds"
                    )
                    fig_m_jf.update_layout(yaxis_title=None, showlegend=False, height=600)
                    st.plotly_chart(fig_m_jf, use_container_width=True)
            else:
                st.warning("JF-Cluster Spalte nicht im Datensatz gefunden.")

        else:
            st.info("💡 **Hinweis:** Keine benutzerdefinierten Cluster geladen. Sie können diese in den Einstellungen definieren.")

        st.divider()

        # ── Section 3: Datengrundlage ──
        with st.expander("📄 Detaillierte Kennzahlentabelle (Rohdaten)", expanded=False):
            st.dataframe(forecast_kpis, use_container_width=True)

    with tab2:
        if events.empty:
            st.info("Keine Treiber-Events vorhanden.")
        else:
            # ── Section 1: Management Summary ──
            st.markdown("### 📊 Zusammenfassung der Abgangs-Treiber")
            
            st.info("""
            **💡 Interpretationshilfe: Headcount vs. Kapazität**
            
            **A) Headcount-Abgänge:** Zählen Personen, die die Bank verlassen (z.B. Kündigung, Rente). 
            Ein Wechsel in die ATZ-Freistellung ist *kein* Headcount-Abgang.
            
            **B) Kapazitäts-Abgänge (MAK):** Messen den Verlust an Arbeitskraft in FTE. 
            Hier zählen auch ATZ-Wechsel (AR→FR) und Ruhens-Starts, da diese die verfügbare Kapazität sofort reduzieren.
            """)

            # Data Prep
            summary_df = events.copy()
            summary_df["event_date"] = pd.to_datetime(summary_df["event_date"])
            summary_df["Jahr"] = summary_df["event_date"].dt.to_period("Y").astype(str)
            
            # --- Table A: Headcount ---
            st.markdown("##### A) Headcount-Abgänge (Personen)")
            hc_exits = summary_df[summary_df["headcount_change"] < 0]
            if not hc_exits.empty:
                hc_pivot = hc_exits.pivot_table(index="Jahr", columns="reason_label", values="persnr", aggfunc="count", fill_value=0)
                hc_pivot["Gesamt"] = hc_pivot.sum(axis=1)
                st.dataframe(hc_pivot, use_container_width=True)
            else:
                st.info("Keine Headcount-Abgänge.")

            # --- Table B: MAK ---
            st.markdown("##### B) Kapazitäts-Abgänge (FTE-Volumen)")
            mak_exits = summary_df[summary_df["mak_change"] < -0.001].copy()
            if not mak_exits.empty:
                mak_exits["MAK_Verlust"] = mak_exits["mak_change"].abs()
                mak_pivot = mak_exits.pivot_table(index="Jahr", columns="reason_label", values="MAK_Verlust", aggfunc="sum", fill_value=0.0)
                mak_pivot["Gesamt"] = mak_pivot.sum(axis=1)
                st.dataframe(mak_pivot.round(2), use_container_width=True)
            else:
                st.info("Keine Kapazitäts-Abgänge.")

            st.divider()

            # ── Section 2: Visual Analysis ──
            st.markdown("### 📈 Grafische Analyse")
            for key, fig in charts.items():
                if key.startswith("driver_"):
                    st.plotly_chart(fig, use_container_width=True)

            st.divider()

            # ── Section 3: Detailed Data Tables ──
            st.markdown("### 📋 Detail-Tabellen nach Treiber")
            tables = result.get("tables", {})
            if not tables:
                st.info("Keine detaillierten Tabellen verfügbar.")
            else:
                for name, df in tables.items():
                    if df is None or df.empty:
                        continue
                    with st.expander(f"Details: {name.capitalize()}", expanded=False):
                        st.dataframe(df, use_container_width=True)

    with tab3:
        if events.empty:
            st.info("Keine Personenlisten vorhanden.")
        else:
            for reason in sorted(events["reason_label"].unique().tolist()):
                reason_df = events[events["reason_label"] == reason]
                safe_reason = "".join([c if c.isalnum() else "_" for c in reason]).strip("_").lower()
                with st.expander(f"{reason} ({len(reason_df)})", expanded=False):
                    st.dataframe(reason_df, use_container_width=True)
                    st.download_button(
                        label=f"CSV Export {reason}",
                        data=to_csv_bytes(reason_df),
                        file_name=f"abgaenge_{safe_reason}.csv",
                        mime="text/csv",
                    )

    with st.expander("Plausibilitätschecks und Parameter"):
        checks = validate_outputs(result)
        st.write("**Checks**")
        st.json(checks)
        st.write("**Parameter**")
        st.json(params)


main()
