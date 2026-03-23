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
from dataloader.jobfamily_service import JobFamilyService
from components.sidebar import render_global_filters, apply_filters, apply_event_filters, render_filter_status, apply_robust_filter, get_effective_metric_view, set_metric_page_hint
from utils.plot_helpers import apply_legend_bottom


def main():
    st.title("📉 Prognose: Abgänge")
    st.caption("Prognose von Abgaengen (ATZ, Rente, Kuendigung) mit klarer Trennung von MAK und Headcount.")
    metric_choice, metric_hint = get_effective_metric_view(["Köpfe", "MAK"], fallback="MAK")
    if metric_hint:
        set_metric_page_hint("EUR ist auf dieser Seite derzeit nicht verfügbar. Die Seite verwendet stattdessen MAK.")
    else:
        set_metric_page_hint(None)

    try:
        # 1. Load Central Data (Consistent with Kompakt)
        # Loader automatically picks up 'global_uploads' from session_state (see loader.py)
        snapshot_df, history_df, _, _ = load_and_prepare_data()

        # 2. Render Sidebar Filters
        render_global_filters(snapshot_df, history_df)
        
        # --- Sidebar Reset Button ---
        with st.sidebar:
            st.divider()
            if st.button("♻️ Ergebnisse zurücksetzen", use_container_width=True, help="Löscht die aktuelle Prognose aus dem Speicher."):
                for key in ["abgaenge_results", "abgaenge_global_result", "abgaenge_params"]:
                    if key in st.session_state:
                         del st.session_state[key]
                st.rerun()
        
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

        # P07: Sync valid Job Families
        valid_jfs = JobFamilyService.get_active_jobfamilies(df_ma)
        
        # Cleanup Session State selections if they are invalid
        if "selected_jobfamilies" in st.session_state:
            st.session_state["selected_jobfamilies"] = JobFamilyService.sanitize_selection(
                st.session_state["selected_jobfamilies"], valid_jfs
            )
    except FileNotFoundError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(f"Fehler beim Laden/Filtern der Daten: {e}")
        return
    
    # df_ma is now the GLOBAL Aggregated Dataset.

    default_start = get_current_stichtag().date()
    default_end = date(default_start.year + 2, default_start.month, default_start.day)

    # ── Parametrierung (Form) ──
    params = default_params()
    # Ruhend bleibt auf dieser Seite vorerst fachlich deaktiviert.
    params["components"]["ruhend"] = False
    ruhend_new = int(params["ruhend"]["ruhend_new_cases_per_year"])
    ruhend_return = float(params["ruhend"]["ruhend_return_rate"])
    ruhend_duration = int(params["ruhend"]["ruhend_avg_duration_months"])
    comp_ruhend = False

    with st.form("abgaenge_forecast_form", clear_on_submit=False):
        st.markdown("### ⚙️ Prognose-Einstellungen")
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
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            comp_atz = st.checkbox("ATZ (inkl. Renteneintritt nach ATZ)", value=params["components"]["atz"])
        with cc2:
            comp_ret = st.checkbox("Rente", value=params["components"]["retirement"])
        with cc3:
            comp_quit = st.checkbox("Kündigung", value=params["components"]["quit"])

        st.markdown("---")
        st.info("💡 **Hinweis zu ATZ:** Wenn 'ATZ' aktiv ist, werden auch die ATZ-Endereignisse automatisch modelliert. Diese erscheinen als 'Rente (nach ATZ)' – unabhängig davon, ob die Komponente 'Rente' separat aktiviert ist.")

        # ── Row 3: Detail Parameters (sub-expanders) - OUTSIDE FORM for interactivity ──
        st.markdown("##### 🔧 Detail-Parameter")

        with st.expander("ATZ-Parameter"):
            # ── ATZ Row 1: General Constraints ──
            ac1, ac2, ac3, ac4, ac5 = st.columns(5)
            with ac1:
                new_atz_base = st.slider("Neue Fälle (Basis): 0.05 (Range 0.00–0.50)", min_value=0.0, max_value=0.5, value=float(params["atz"].get("new_atz_rate", 0.05)), step=0.005, format="%.3f", help="Basis-Anteil der berechtigten Mitarbeiter, die pro Jahr in ATZ gehen.", key="slide_atz_base")
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
            
            atz_unique_vals = []
            if atz_dim == "OrgUnit":
                if "Organisationseinheit" in df_ma.columns:
                    atz_unique_vals = sorted([str(x) for x in df_ma["Organisationseinheit"].dropna().unique()])
            else:
                atz_unique_vals = valid_jfs
            
            from utils.matrix_helpers import migrate_to_percent

            current_atz_matrix = migrate_to_percent(params["atz"].get("atz_matrix", {}))
            atz_dim_items = ["Default"] + atz_unique_vals
            atz_editor_data = []

            for val in atz_dim_items:
                # Try to get value from old cohort-based structure or new flat structure
                rate = current_atz_matrix.get(str(val))
                if rate is None:
                    # Fallback to "alter_55_plus" if it was old matrix
                    rate = current_atz_matrix.get("alter_55_plus", {}).get(str(val))
                if rate is None:
                    rate = current_atz_matrix.get("Default", new_atz_base * 100)

                atz_editor_data.append({
                    atz_dim: val,
                    "Wahrscheinlichkeit (%)": float(rate)
                })

            df_atz_matrix = pd.DataFrame(atz_editor_data).set_index(atz_dim)

            edited_atz_df = st.data_editor(
                df_atz_matrix,
                use_container_width=True,
                height=min(400, 50 + len(atz_dim_items) * 35),
                key="atz_matrix_editor_live",
                disabled=not use_atz_matrix,
                column_config={
                    "Wahrscheinlichkeit (%)": st.column_config.NumberColumn(
                        "Wahrscheinlichkeit (%)",
                        min_value=0.0, max_value=100.0, step=0.5, format="%.1f"
                    )
                }
            )

            new_atz_matrix = {}
            for dim_val, row in edited_atz_df.iterrows():
                new_atz_matrix[str(dim_val)] = float(row["Wahrscheinlichkeit (%)"])
            params["atz"]["atz_matrix"] = new_atz_matrix

        with st.expander("Renten-Parameter"):
            rc1, rc2 = st.columns(2)
            with rc1:
                rent65 = st.slider("Renteneintritt 65+: 0.90 (Range 0.00–1.00)", min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_65"]), step=0.05, key="slide_rent_65")
                params["retirement"]["rent_rate_65"] = rent65
            with rc2:
                rent60 = st.slider("Frühverrentung 60-64: 0.10 (Range 0.00–1.00)", min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_60_65"]), step=0.05, key="slide_rent_60")
                params["retirement"]["rent_rate_60_65"] = rent60

        with st.expander("Kündigungs-Parameter", expanded=False):
            # ── Controls Row ──
            c1, c2, c3 = st.columns([3, 3, 2])
            
            with c1:
                quit_base = st.slider(
                    "Basisrate p.a.: 0.05 (Range 0.00–0.50)", 
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
            unique_vals = []
            if quit_dim == "OrgUnit":
                if "Organisationseinheit" in df_ma.columns:
                    unique_vals = sorted([str(x) for x in df_ma["Organisationseinheit"].dropna().unique()])
            else:
                unique_vals = valid_jfs
            
            age_cohorts = ["alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus"]
            age_labels = {"alter_unter_30": "u30", "alter_30_45": "30-45", "alter_45_55": "45-55", "alter_55_plus": "ü55"}

            # 2. Build DataFrame (percent scale)
            current_quit_matrix = migrate_to_percent(params["quit"].get("quit_matrix", {}))
            dim_items = ["Default"] + unique_vals
            editor_data = []

            for val in dim_items:
                row_data = {quit_dim: val}
                for cohort in age_cohorts:
                    cohort_rates = current_quit_matrix.get(cohort, {})
                    rate = cohort_rates.get(str(val), cohort_rates.get("Default", quit_base * 100))
                    row_data[cohort] = float(rate)
                editor_data.append(row_data)

            df_matrix = pd.DataFrame(editor_data).set_index(quit_dim)

            # 3. Render Editor
            col_conf = {
                quit_dim: st.column_config.TextColumn(f"{quit_dim}", disabled=True)
            }
            for c in age_cohorts:
                col_conf[c] = st.column_config.NumberColumn(
                    age_labels.get(c, c) + " (%)",
                    min_value=0.0, max_value=100.0, step=0.5, format="%.1f"
                )

            edited_df = st.data_editor(
                df_matrix,
                use_container_width=True,
                height=min(400, 50 + len(dim_items) * 35),
                disabled=not use_quit_matrix,
                key="quit_matrix_editor_live_fixed",
                column_config=col_conf
            )

            # 4. Save back (percent scale — conversion to weights in build_params_from_ui)
            new_quit_matrix = {c: {} for c in age_cohorts}
            for dim_val, row in edited_df.iterrows():
                for c in age_cohorts:
                    new_quit_matrix[c][str(dim_val)] = float(row[c])
            params["quit"]["quit_matrix"] = new_quit_matrix

            st.divider()
            st.markdown("##### 📅 Jahresgenaue Anpassungen (Job-Families)")
            st.info("Hier können Sie für spezifische Jahre und Job-Families eine Erhöhung (+50%) oder Reduktion (-50%) der Kündigungsrate festlegen.")
            
            # More/Less selections
            adj_col1, adj_col2 = st.columns(2)
            
            # Load current adjustments from session_state or params
            current_adj = params["quit"].get("quit_adjustments", {"more": {}, "less": {}})
            
            with adj_col1:
                st.caption("📈 Mehr Kündigungen (+50%)")
                selected_more_jf = st.multiselect(
                    "Job-Families wählen", 
                    options=valid_jfs, 
                    default=list(current_adj.get("more", {}).keys()),
                    key="ms_quit_more_jf"
                )
                
                more_years_map = {}
                available_years = JobFamilyService.get_available_years(ist_stichtag.year, (forecast_end_date.year - ist_stichtag.year) + 1)
                
                for jf in selected_more_jf:
                    default_years = current_adj.get("more", {}).get(jf, [])
                    # Sanitize default years to be within available range
                    default_years = [y for y in default_years if y in available_years]
                    
                    years = st.multiselect(
                        f"Jahre für {jf}", 
                        options=available_years,
                        default=default_years,
                        key=f"ms_more_years_{jf}"
                    )
                    if years:
                        more_years_map[jf] = years
            
            with adj_col2:
                st.caption("📉 Weniger Kündigungen (-50%)")
                selected_less_jf = st.multiselect(
                    "Job-Families wählen", 
                    options=valid_jfs, 
                    default=list(current_adj.get("less", {}).keys()),
                    key="ms_quit_less_jf"
                )
                
                less_years_map = {}
                for jf in selected_less_jf:
                    default_years = current_adj.get("less", {}).get(jf, [])
                    default_years = [y for y in default_years if y in available_years]

                    years = st.multiselect(
                        f"Jahre für {jf}", 
                        options=available_years,
                        default=default_years,
                        key=f"ms_less_years_{jf}"
                    )
                    if years:
                        less_years_map[jf] = years
            
            quit_adjustments = {
                "more": more_years_map,
                "less": less_years_map
            }
            params["quit"]["quit_adjustments"] = quit_adjustments

    # ── Action Button ──
        st.write("")
        submit = st.form_submit_button("🚀 Prognose mit diesen Parametern berechnen", use_container_width=True)

    # ── Rendering or Calculation Logic ──
    freq = "M" if freq_label == "Monat" else "Q"

    if submit:
        # Pre-calculation checks
        if forecast_end_date <= ist_stichtag:
            st.error("Prognose-Ende muss nach dem Ist-Stichtag liegen.")
            st.stop()

        ui_state = {
            "ist_stichtag": ist_stichtag.isoformat() if hasattr(ist_stichtag, "isoformat") else str(ist_stichtag),
            "forecast_end_date": forecast_end_date.isoformat() if hasattr(forecast_end_date, "isoformat") else str(forecast_end_date),
            "freq": freq,
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
                "quit_adjustments": quit_adjustments,
            },
            "ruhend": {
                "ruhend_new_cases_per_year": ruhend_new,
                "ruhend_return_rate": ruhend_return,
                "ruhend_avg_duration_months": ruhend_duration,
            },
            "random_seed": random_seed,
        }

        params = build_params_from_ui(ui_state)
    
        # ── Calculation Block ──
        try:
            with st.spinner("Berechne Prognose..."):
                # P01: Run Global Forecast
                global_result = run_forecast_abgaenge(
                    df_ma=df_ma, 
                    df_atz=df_atz,
                    start_date=pd.Timestamp(ist_stichtag),
                    # P01: Using exact date for consistency across pages
                    end_date=pd.Timestamp(forecast_end_date),
                    freq=freq,
                    params=params,
                )
        
            # --- Enrichment: add OrgUnit/Clusters/UID to global events ---
            events_global = global_result["events_person_level"]
            if not events_global.empty:
                if "Organisationseinheit" not in events_global.columns and "Organisationseinheit" in df_ma.columns:
                    lookup_oe = df_ma[["PersNr", "Organisationseinheit"]].drop_duplicates("PersNr").copy()
                    lookup_oe["PersNr"] = lookup_oe["PersNr"].astype(str)
                    events_global = events_global.merge(lookup_oe, left_on="persnr", right_on="PersNr", how="left").drop(columns=["PersNr"])

                for cluster_col in ["OE-Cluster", "JF-Cluster"]:
                    if cluster_col not in events_global.columns and cluster_col in df_ma.columns:
                        lookup_c = df_ma[["PersNr", cluster_col]].drop_duplicates("PersNr").copy()
                        lookup_c["PersNr"] = lookup_c["PersNr"].astype(str)
                        events_global = events_global.merge(lookup_c, left_on="persnr", right_on="PersNr", how="left").drop(columns=["PersNr"])
                        events_global[cluster_col] = events_global[cluster_col].fillna("Sonstiges")

                from abgaenge.schemas import build_event_uid
                events_global["event_uid"] = build_event_uid(events_global)
                if "persnr" in events_global.columns:
                    events_global["persnr"] = events_global["persnr"].astype(str)

                global_result["events_person_level"] = events_global

            # --- Save (unfiltered) ---
            import datetime
            st.session_state["abgaenge_global_result"] = global_result
            st.session_state["abgaenge_params"] = params
            st.session_state["abgaenge_ui_state"] = ui_state
            st.session_state["abgaenge_timestamp"] = datetime.datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
            st.success("Prognose erfolgreich berechnet.")
            st.rerun()
            
        except Exception as e:
            st.error(f"Fehler in der Prognose calculation: {e}")
            st.stop()

    # ── Rendering Logic (View-Only Zoom from Global Result) ──
    global_result = st.session_state.get("abgaenge_global_result")
    if not global_result:
        st.info("ℹ️ Keine Prognose berechnet. Bitte stellen Sie die Parameter ein und klicken Sie auf 'Prognose berechnen'.")
        return

    last_run_params = st.session_state.get("abgaenge_ui_state", {})
    timestamp = st.session_state.get("abgaenge_timestamp", "–")

    # ── View-Only Zoom: apply current sidebar filters to global events ──
    # NOTE: Use df_ma (employee-level, with MAK_Calculated) — NOT snapshot_df
    # (raw position-level without MAK_Calculated).
    events_global = global_result["events_person_level"]

    events_view, n_before, n_after = apply_event_filters(
        events_global, df_ma, mode="attrition"
    )

    # Filtered snapshot for KPI re-aggregation
    df_filtered_rows = apply_filters(df_ma)
    if df_filtered_rows.empty:
        st.warning("⚠️ Keine Daten nach Filterung verfügbar.")
        return

    # df_ma is already employee-level (1 row per PersNr), so groupby is
    # effectively a no-op but keeps the code path consistent.
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
    df_view_agg["mak"] = pd.to_numeric(df_view_agg["MAK_Calculated"], errors="coerce").fillna(0.0)

    # P05: Adjust Events (Clamp MAK Loss to View Reality)
    if not events_view.empty:
        df_view_agg_indexed = df_view_agg.set_index("PersNr")
        if "mak" in df_view_agg_indexed.columns:
            pmak_map = df_view_agg_indexed["mak"].to_dict()

            def adjust_mak_loss(row):
                pid = str(row["persnr"])
                if row["mak_change"] < 0:
                    current_view_mak = float(pmak_map.get(pid, 0.0))
                    return -abs(current_view_mak)
                return row["mak_change"]

            events_view = events_view.copy()
            events_view["mak_change"] = events_view.apply(adjust_mak_loss, axis=1)

    # P06: Re-Aggregate KPIs for current view
    forecast_kpis = aggregate_forecast_results(
        df_initial=df_view_agg,
        events_df=events_view,
        start_date=pd.Timestamp(ist_stichtag),
        end_date=pd.Timestamp(forecast_end_date) + pd.Timedelta(hours=23, minutes=59, seconds=59),
        freq=freq,
        params=build_params_from_ui(last_run_params) if last_run_params else None,
    )

    events = events_view

    # Publish filtered bundle for Page 5 cross-reference (Contract V3 compat)
    import datetime as _dt
    events_chart_df = events[events["headcount_change"] < 0].copy() if not events.empty else pd.DataFrame()
    st.session_state["abgaenge_results"] = {
        "schema_version": 3,
        "forecast_kpis": forecast_kpis,
        "events_chart": events_chart_df,
        "events_raw": events,
        "events": events,
        "params": last_run_params,
        "filters": {
            "org": st.session_state.get("selected_org_units", []),
            "jf": st.session_state.get("selected_jobfamilies", []),
            "gender": st.session_state.get("selected_genders", []),
            "empl": st.session_state.get("selected_employment", []),
            "atz": st.session_state.get("selected_atz_status", []),
        },
        "meta": {
            "rows_chart": len(events_chart_df),
            "rows_raw": len(events),
            "unique_persons_chart": int(events_chart_df["persnr"].nunique()) if not events_chart_df.empty else 0,
            "created_at": _dt.datetime.now().isoformat(),
        },
        "timestamp": timestamp,
    }

    # Drift Detection (forecast parameters only – sidebar filters are view-only)
    curr_ui_state = {
        "ist_stichtag": ist_stichtag.isoformat() if hasattr(ist_stichtag, "isoformat") else str(ist_stichtag),
        "forecast_end_date": forecast_end_date.isoformat() if hasattr(forecast_end_date, "isoformat") else str(forecast_end_date),
        "freq": freq,
        "components": {
            "atz": comp_atz, "retirement": comp_ret, "quit": comp_quit, "ruhend": comp_ruhend
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
        "retirement": { "rent_rate_65": rent65, "rent_rate_60_65": rent60 },
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

    def dict_hash(d):
        return json.dumps(d, sort_keys=True, default=str)

    drift = dict_hash(curr_ui_state) != dict_hash(last_run_params)

    # Header Info
    c1, c2 = st.columns([2, 1])
    with c1:
        st.write(f"⏱️ **Stand der Berechnung:** {timestamp}")
    with c2:
        if drift:
            st.warning("⚠️ Parameter geändert - bitte neu berechnen.")
        else:
            st.success("✅ Ergebnisse aktuell.")

    render_filter_status(n_before, n_after)

    # Ensure columns exist for downstream charts
    for cluster_col in ["OE-Cluster", "JF-Cluster", "Organisationseinheit", "Jobfamily"]:
        if cluster_col not in events.columns:
            events[cluster_col] = "Unbekannt"
        else:
            events[cluster_col] = events[cluster_col].fillna("Unbekannt")
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
        
        # Calculate annualized rate
        forecast_days = (pd.Timestamp(forecast_end_date) - pd.Timestamp(ist_stichtag)).days
        years = max(0.1, forecast_days / 365.25)
        abgangsquote_annual = (1 - (1 - abgangsquote)**(1/years)) if abgangsquote < 1 else (abgangsquote / years)

        st.markdown("### 🏆 Kennzahlen (Management-Summary)")
        m1, m2, m3, m4 = st.columns([1, 1, 1, 1])
        with m1:
            st.metric("Abgänge gesamt (Köpfe)", f"{exits_total}")
        with m2:
            st.metric("Kapazitätsverlust (MAK)", f"{mak_loss_total:.1f}")
        with m3:
            st.metric("Abgangsquote (Zeitraum)", f"{abgangsquote*100:.1f}%", help="Summierte Abgänge im Verhältnis zum Ø Bestand über den gesamten Zeitraum.")
        with m4:
            st.metric("Ø Fluktuation (p.a.)", f"{abgangsquote_annual*100:.1f}%", help="Annualisierte Abgangsquote (geometrischer Durchschnitt pro Jahr).")
        
        st.info("""
        **💡 Interpretationshilfe: Köpfe vs. Kapazität (MAK)**
        * **Abgänge (Köpfe):** Personen, die das Unternehmen verlassen (Rente, Kündigung etc.).
        * **Kapazitätsverlust (MAK):** Summierter Verlust an Arbeitskraft. Hier zählen auch **ATZ-Wechsel (AR→FR)** und Ruhenphasen, da diese die Kapazität reduzieren, ohne dass die Person die Bank verlassen muss.
        """)
        
        # P03: Secure Start/End KPI (Robust extraction from Timeline info)
        with st.expander("🔍 Details: Bestandsentwicklung (Start vs. Ende)", expanded=False):
            d1, d2, d3, d4 = st.columns(4)
            
            # Use total period delta (End - Start) for consistency
            hc_start = float(first['headcount_start']) if not pd.isna(first['headcount_start']) else 0.0
            hc_end = float(last['headcount_end']) if not pd.isna(last['headcount_end']) else 0.0
            mak_start = float(first['mak_start']) if not pd.isna(first['mak_start']) else 0.0
            mak_end = float(last['mak_end']) if not pd.isna(last['mak_end']) else 0.0
            
            total_hc_delta = int(hc_end - hc_start)
            total_mak_delta = float(mak_end - mak_start)
            
            d1.metric("Headcount Start", f"{int(hc_start)}")
            d2.metric("Headcount Ende", f"{int(hc_end)}", 
                      delta=f"{total_hc_delta}", delta_color="normal", help="Δ gesamt im Zeitraum")
            
            d3.metric("MAK Start", f"{mak_start:.1f}")
            d4.metric("MAK Ende", f"{mak_end:.1f}", 
                      delta=f"{total_mak_delta:.1f}", delta_color="normal", help="Δ gesamt im Zeitraum")
            
            # P01: Clearer caption without hardcoded artifacts
            end_date_str = last['period_end'].strftime("%d.%m.%Y") if hasattr(last['period_end'], 'strftime') else last['period_label']
            st.caption(f"Das Delta (**Δ gesamt**) entspricht der Differenz zwischen dem Bestand am Ende des Prognosezeitraums ({end_date_str}) und dem Bestand zu Beginn.")

        # DEBUG OUTPUT
        if st.session_state.get("debug_active", False):
            with st.expander("🐞 Debug-Daten (Abgänge)", expanded=True):
                # 1. Summary Metrics
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write(f"**Events (Raw):** `{len(events)}`")
                    st.write(f"**Abgänge (Köpfe, sum):** `{events['headcount_change'].sum() if not events.empty else 0}`")
                with c2:
                    unique_total = events['persnr'].nunique() if not events.empty else 0
                    unique_hc = events[events['headcount_change'] < 0]['persnr'].nunique() if not events.empty else 0
                    unique_cap = events[(events['headcount_change'] == 0) & (events['mak_change'] < 0)]['persnr'].nunique() if not events.empty else 0
                    
                    st.write(f"**Unique Personen (alle Events):** `{unique_total}`")
                    st.write(f"**- davon mit Headcount-Abgang:** `{unique_hc}`")
                    st.write(f"**- davon nur Kapazität (ohne Austritt):** `{unique_cap}`")
                
                with c3:
                    st.write(f"**MAK-Verlust (sum):** `{events['mak_change'].sum() if not events.empty else 0:.2f}`")
                    st.caption("*(Debug, 2 Dez. Summe über alle Events)*")
                    
                    # Logic Overlap Check (ATZ_END vs RETIREMENT)
                    if not events.empty:
                        from abgaenge.schemas import REASON_ATZ_END, REASON_RETIREMENT
                        pnr_atz_end = set(events[events["reason_code"] == REASON_ATZ_END]["persnr"])
                        pnr_retirement = set(events[events["reason_code"] == REASON_RETIREMENT]["persnr"])
                        overlap = len(pnr_atz_end & pnr_retirement)
                        color = "red" if overlap > 0 else "green"
                        st.markdown(f"**Overlap ATZ_END ∩ Rente (Unique):** :{color}[`{overlap}`]")

                st.divider()
                
                # 2. Impact Table
                st.markdown("**Events nach Wirkung (Abstimmung)**")
                if not events.empty:
                    debug_impact = events.groupby("reason_code").agg(
                        Event_Count=("reason_code", "count"),
                        Unique_Pers=("persnr", "nunique"),
                        Sum_Headcount=("headcount_change", "sum"),
                        Sum_MAK=("mak_change", "sum")
                    ).reset_index()
                    
                    # Labels
                    from abgaenge.schemas import REASON_LABELS
                    debug_impact["Ursache"] = debug_impact["reason_code"].map(REASON_LABELS)
                    st.caption("🔍 **Debug (2 Dez.):** Rohsummen der identifizierten Events.")
                    st.table(debug_impact[["Ursache", "Event_Count", "Unique_Pers", "Sum_Headcount", "Sum_MAK"]].round(2))
                
                st.divider()
                st.markdown("**Stichprobe (Letzte 20 Events)**")
                st.dataframe(events.tail(20))

    # --- Choice of Metric for Charts ---
    st.write("")
    st.caption(f"Kennzahlensicht aus der Sidebar: `{metric_choice}`")

    charts = build_charts(forecast_kpis, events, metric_type=metric_choice)
    
    # Apply standard legend to all charts from builder
    for key, fig in charts.items():
        if fig:
            charts[key] = apply_legend_bottom(fig)

    tab1, tab2, tab3 = st.tabs(["📊 Überblick & Trends", "🎯 Treiber Details", "📋 Personenlisten / Export"])

    # Helper for Debug Metrics
    def _render_debug_metric(label, chart_val, global_val, unit=""):
        if not st.session_state.get("debug_active", False):
            return
        
        diff = chart_val - global_val
        is_ok = abs(diff) < 0.1
        color = "green" if is_ok else "red"
        icon = "✅" if is_ok else "❌"
        
        # UI uses 1 decimal, Debug uses 2
        st.markdown(
            f"<small>{icon} **Debug ({label})**: UI (gerundet, 1 Dez.): **{global_val:,.1f}{unit}** | Debug (2 Dez.): **{global_val:,.2f}{unit}** | Diff (zu Chart): :{color}[{diff:+.3f}]</small>", 
            unsafe_allow_html=True
        )

    with tab1:
        # ── Section 1: zeitliche Entwicklung & Gesamt-Struktur ──
        st.markdown("### 📈 Bestandsentwicklung (Trend)")
        st.plotly_chart(charts.get("line_headcount_mak"), use_container_width=True)
        # Debug Timeline: Last Point vs KPI
        if not forecast_kpis.empty:
            _render_debug_metric("Timeline End MAK", last['mak_end'], last['mak_end'], " MAK")
            
        st.caption("Die Trendlinie zeigt den Bestand am Ende jeder Periode. Ein sinkender MAK-Verlauf bei stabilem Headcount deutet auf ATZ-Übergänge hin.")

        st.plotly_chart(charts.get("bar_reasons_total"), use_container_width=True)
        # Debug Reason Total
        if not events.empty:
            layout_val = events["headcount_change" if metric_choice=="Köpfe" else "mak_change"].sum()
            compare_val = exits_total if metric_choice=="Köpfe" else mak_loss_total
            _render_debug_metric(f"Reason Chart Sum ({metric_choice})", abs(layout_val), compare_val, " MAK" if metric_choice=="MAK" else "")
            
        st.caption(f"Gesamt-Verlust ({metric_choice}) nach Ursache für den gewählten Zeitraum.")

        st.divider()

        # ── Section 2: Struktur der Abgänge ──
        st.markdown(f"### 🧬 {metric_choice} nach Ursache (zeitlich)")
        st.plotly_chart(charts.get("bar_abgaenge_reasons"), use_container_width=True)
        # Debug Detailed Reasons
        if not events.empty:
            _render_debug_metric("Detailed Reason Sum", abs(events["headcount_change" if metric_choice=="Köpfe" else "mak_change"].sum()), exits_total if metric_choice=="Köpfe" else mak_loss_total, " MAK" if metric_choice=="MAK" else "")

        st.caption(f"Diese Grafik zeigt, in welchen Perioden der Kapazitätsverlust ({metric_choice}) durch welche Ursache auftritt.")

        st.divider()

        if "Organisationseinheit" in events.columns:
            st.markdown("### 🏢 Top 15 Organisationseinheiten")
            
            # Aggregate
            exclude_units = ["Unbekannt", None]
            # FIX: Only count actual Headcount Exits (ignore ATZ internal steps for this chart)
            org_events = events[~events["Organisationseinheit"].isin(exclude_units)]
            org_events = org_events[org_events["headcount_change"] < 0]
            
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
                # Debug OrgUnit Sum (Check if we missed anyone)
                if not org_events.empty:
                    # We check sum of ALL units (not just top 15) against global
                    # Note: org_events excludes 'Unbekannt'
                    _render_debug_metric("OrgUnit Sum (Headcount)", len(org_events), exits_total, "")

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
                # Debug Cluster Headcount
                if not cluster_events_h.empty:
                    _render_debug_metric("Cluster Sum (Headcount)", len(cluster_events_h), exits_total, "")

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
                    # Debug Cluster MAK
                    if not cluster_events_m.empty:
                        # mak_loss is positive
                        _render_debug_metric("Cluster Sum (MAK)", cluster_events_m["mak_loss"].sum(), mak_loss_total, " MAK")
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
                # Debug JF Headcount
                if not cluster_events_h_jf.empty:
                    _render_debug_metric("JF Cluster Sum (Headcount)", len(cluster_events_h_jf), exits_total, "")

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
                    # Debug JF MAK
                    if not cluster_events_m_jf.empty:
                         # mak_loss is positive
                         _render_debug_metric("JF Cluster Sum (MAK)", cluster_events_m_jf["mak_loss"].sum(), mak_loss_total, " MAK")
            else:
                st.warning("JF-Cluster Spalte nicht im Datensatz gefunden.")

        else:
            st.info("💡 **Hinweis:** Keine benutzerdefinierten Cluster geladen. Sie können diese in den Einstellungen definieren.")

        st.divider()

        # ── Section 3: Datengrundlage ──
        with st.expander("📄 Detaillierte Kennzahlentabelle (Rohdaten)", expanded=False):
            # Round for raw display (2 digits as requested for non-summary)
            df_display = forecast_kpis.copy()
            for col in ["mak_start", "mak_end", "mak_delta", "mak_loss_gross", "abgangsquote"]:
                if col in df_display.columns:
                    df_display[col] = df_display[col].round(2)
            st.dataframe(df_display, use_container_width=True, height=500)

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
                # Management View: Round to 1 decimal place as requested
                st.dataframe(mak_pivot.round(1), use_container_width=True)
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
            res_global = st.session_state.get("abgaenge_global_result", {})
            tables = res_global.get("tables", {})
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
        res_global = st.session_state.get("abgaenge_global_result")
        if res_global:
            checks = validate_outputs(res_global)
            st.write("**Checks**")
            st.json(checks)
        st.write("**Parameter**")
        st.json(last_run_params)


main()
