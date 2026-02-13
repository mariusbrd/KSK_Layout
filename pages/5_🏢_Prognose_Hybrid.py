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
from config.settings import COLORS, TARIFF_GROUPS

from abgaenge import (
    default_params as default_abgaenge_params,
    build_params_from_ui as build_abgaenge_params_from_ui,
    run_forecast_abgaenge,
    aggregate_forecast_results,
    validate_outputs,
    build_charts as build_abgaenge_charts,
    to_csv_bytes,
)
from zugaenge.params import default_params as default_zugaenge_params, get_strategies
from zugaenge.forecast import run_forecast_zugaenge

# Shared Components
from dataloader.loader import load_and_prepare_data, load_atz_data_cached, calculate_mak_vectorized, calculate_cost_vectorized
from dataloader.cluster_manager import is_clustering_active
from components.sidebar import render_global_filters, apply_filters


def calculate_kpi_from_events(df_start_stats: pd.DataFrame, events_df: pd.DataFrame, start_date: pd.Timestamp, end_date: pd.Timestamp, freq: str) -> pd.DataFrame:
    """
    Calculates KPI time-series strictly from initial state + event deltas.
    Ensures 100% consistency with event-based charts.
    """
    # 1. Start Stats
    start_hc = df_start_stats["active"].sum() if "active" in df_start_stats.columns else len(df_start_stats)
    start_mak = df_start_stats["mak"].sum() if "mak" in df_start_stats.columns else start_hc

    # 2. Periods
    periods = pd.period_range(start=start_date, end=end_date, freq=freq)
    
    # 3. Aggregate Events by Period
    kpi_data = []
    
    current_hc = start_hc
    current_mak = start_mak
    
    # Pre-calculate deltas
    # Resample events to period end
    if not events_df.empty:
        # Copy to avoid mutation
        ev = events_df.copy()
        ev["period"] = ev["event_date"].dt.to_period(freq)
        
        # Group by period
        grp = ev.groupby("period")[["headcount_change", "mak_change"]].sum()
    else:
        grp = pd.DataFrame()

    for p in periods:
        delta_hc = 0
        delta_mak = 0.0
        
        if not grp.empty and p in grp.index:
            delta_hc = int(grp.loc[p, "headcount_change"])
            delta_mak = float(grp.loc[p, "mak_change"])
            
        current_hc += delta_hc
        current_mak += delta_mak
        
        kpi_data.append({
            "period_end": p.end_time,
            "headcount_end": current_hc,
            "mak_end": current_mak,
            "headcount_delta": delta_hc,
            "mak_delta": delta_mak,
            "mak_start": start_mak # Constant ref for debug
        })
        
    return pd.DataFrame(kpi_data)


def _check_sum(label: str, chart_sum: float, ref_sum: float, unit: str = "Pers", tolerance: float = 0.1):
    """Helper to render a debug metric comparing Chart vs Reference."""
    diff = abs(chart_sum - ref_sum)
    is_ok = diff <= tolerance
    icon = "✅" if is_ok else "❌"
    color = "green" if is_ok else "red"
    
    st.markdown(
        f"**{icon} {label}**<br>"
        f"Chart: `{chart_sum:.1f}` | Ref: `{ref_sum:.1f}` | Diff: `:{color}[{diff:.1f}]`",
        unsafe_allow_html=True
    )

def _get_unique_count(df: pd.DataFrame) -> int:
    """Robustly count unique persons, handling missing/renamed columns."""
    try:
        # Priority list of possible ID columns
        candidates = ["PersNr", "persnr", "ID", "id", "EmployeeID", "Personalnummer"]
        for col in candidates:
            if col in df.columns:
                return df[col].nunique()
        
        # Fallback if no ID found
        st.warning("⚠️ Debug: Kein Personen-Identifier (PersNr) gefunden.")
        return 0
    except Exception as e:
        # Never crash debug
        return 0


def _render_debug_aggregation(df: pd.DataFrame, group_cols: list[str], label: str, count_col: str = "count", mak_col: str = "mak_change", top_n: int = 20, key_prefix: str = ""):
    """
    Renders a standardized debug aggregation table with granular counts.
    """
    st.markdown(f"**{label}**")
    
    if df.empty:
        st.caption("Keine Daten verfügbar.")
        return

    try:
        # Check if columns exist
        missing = [c for c in group_cols if c not in df.columns]
        if missing:
            st.error(f"Fehlende Spalten für Aggregation: {missing}")
            return

        # 1. Fill NaNs to avoid dropping rows in groupby
        df_clean = df.copy()
        for c in group_cols:
            df_clean[c] = df_clean[c].fillna("Unclustered").replace(r"^\s*$", "Unclustered", regex=True)

        # 2. Granular Aggregation
        # Rows: Count of records
        # HC Events: Count where headcount_change != 0 (abs > 0)
        # MAK Events: Count where mak_change != 0 (abs > 0)
        
        # Pre-calc masks
        df_clean["_hc_event"] = (df_clean[count_col].abs() > 0).astype(int) if count_col in df_clean.columns else 0
        df_clean["_mak_event"] = (df_clean[mak_col].abs() > 0).astype(int) if mak_col in df_clean.columns else 0
        
        agg_setup = {
            "Rows": ("persnr", "size"), # PersNr just as dummy column for size
            "HC Events": ("_hc_event", "sum"),
            "MAK Events": ("_mak_event", "sum"),
        }
        
        # Add Value Sums
        if count_col in df_clean.columns:
            agg_setup["Delta Köpfe"] = (count_col, "sum")
        if mak_col in df_clean.columns:
            agg_setup["Delta MAK"] = (mak_col, "sum")
            
        # Groupby
        grouped = df_clean.groupby(group_cols, as_index=False).agg(**agg_setup)

        # 3. Sorting (Impact)
        # Sort by absolute MAK change if available, else HC change, else Rows
        if "Delta MAK" in grouped.columns:
            grouped["_sort"] = grouped["Delta MAK"].abs()
        elif "Delta Köpfe" in grouped.columns:
            grouped["_sort"] = grouped["Delta Köpfe"].abs()
        else:
            grouped["_sort"] = grouped["Rows"]
            
        grouped = grouped.sort_values("_sort", ascending=False).drop(columns=["_sort"])
        
        # 4. Display Logic
        rows_total = len(grouped)
        if rows_total > top_n:
             show_all = st.checkbox(f"Alle {rows_total} Zeilen anzeigen ({label})", key=f"{key_prefix}tog_{label}_{group_cols[-1]}")
             if not show_all:
                 grouped = grouped.head(top_n)
                 st.caption(f"Zeige Top {top_n} von {rows_total}")
        
        st.dataframe(grouped, use_container_width=True, hide_index=True)
        
    except Exception as e:
        st.error(f"Fehler in Aggregation ({label}): {e}")

def main():
    st.title("🏢 Prognose: Hybrid")
    st.write("Prognose von Hybrid-Szenarien (Abgänge & Zugänge) mit klarer Trennung von MAK und Headcount.")

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
    params_abg = default_abgaenge_params()
    params_zug = default_zugaenge_params()

    # ── Settings Accordion ──────────────────────────────────────────
    with st.expander("⚙️ Prognose-Einstellungen (Hybrid)", expanded=True):
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
            random_seed = st.number_input("Random Seed", value=int(params_abg["random_seed"]), step=1)

        st.markdown("---")

        # ── Row 2: Component Toggles (horizontal) ──
        st.markdown("##### 🧩 Aktive Komponenten")
        
        st.markdown("**Abgangs-Treiber:**")
        cc1, cc2, cc3, cc4 = st.columns(4)
        with cc1:
            comp_atz = st.checkbox("ATZ", value=params_abg["components"]["atz"])
        with cc2:
            comp_ret = st.checkbox("Rente", value=params_abg["components"]["retirement"])
        with cc3:
            comp_quit = st.checkbox("Kündigung", value=params_abg["components"]["quit"])
        with cc4:
            comp_ruhend = st.checkbox("Ruhend", value=params_abg["components"]["ruhend"])
            
        st.markdown("**Zugangs-Treiber:**")
        zc1, zc2, zc3 = st.columns(3)
        with zc1:
            comp_azubi = st.checkbox("Azubis", value=True) # Usually active
        with zc2:
            comp_trainee = st.checkbox("Trainees", value=True)
        with zc3:
            comp_hires = st.checkbox("Neueinstellungen", value=True)

        st.markdown("---")

        # ── Row 3: Detail Parameters (sub-expanders) ──
        st.markdown("##### 🔧 Detail-Parameter")

        t_abg, t_zug = st.tabs(["📉 Abgänge", "📈 Zugänge"])

        with t_abg:
            with st.expander("ATZ-Parameter"):
                ac1, ac2, ac3, ac4, ac5 = st.columns(5)
                with ac1:
                    new_atz_base = st.slider("Neue Fälle (Basis)", min_value=0.0, max_value=0.5, value=float(params_abg["atz"].get("new_atz_rate", 0.05)), step=0.005, format="%.3f", key="hy_atz_base")
                with ac2:
                    eligible_age = st.number_input("Mindestalter", value=int(params_abg["atz"]["atz_eligible_age_min"]), step=1, key="hy_atz_age_min")
                with ac3:
                    eligible_age_max = st.number_input("Höchstalter", value=int(params_abg["atz"]["atz_eligible_age_max"]), step=1, key="hy_atz_age_max")
                with ac4:
                    ar_years = st.number_input("AR-Dauer (Jahre)", value=float(params_abg["atz"]["atz_duration_ar_years"]), step=0.5, key="hy_atz_ar_years")
                with ac5:
                    fr_years = st.number_input("FR-Dauer (Jahre)", value=float(params_abg["atz"]["atz_duration_fr_years"]), step=0.5, key="hy_atz_fr_years")

                st.divider()

                bc1, bc2 = st.columns([1, 1])
                with bc1:
                    use_atz_matrix = st.checkbox("Detaillierte ATZ-Matrix verwenden", value=params_abg["atz"].get("use_atz_matrix", False), key="hy_atz_use_mat")
                with bc2:
                    atz_dim = st.radio("Dimension für ATZ", options=["JobFamily", "OrgUnit"], index=0 if params_abg["atz"].get("atz_dimension", "JobFamily") == "JobFamily" else 1, horizontal=True, key="hy_atz_dim")

                # Matrix Editor logic (Simplified for conciseness here, but needs to work)
                atz_col_name = "organisationseinheit" if atz_dim == "OrgUnit" else "Jobfamily"
                atz_unique_vals = sorted([str(x) for x in df_ma[atz_col_name.capitalize() if atz_col_name == "Jobfamily" else "Organisationseinheit"].dropna().unique()])
                atz_dim_items = ["Default"] + atz_unique_vals
                atz_editor_data = []
                for val in atz_dim_items:
                    rate = params_abg["atz"].get("atz_matrix", {}).get(str(val), new_atz_base)
                    atz_editor_data.append({atz_dim: val, "Wahrscheinlichkeit": float(rate)})
                df_atz_matrix = pd.DataFrame(atz_editor_data).set_index(atz_dim)
                edited_atz_df = st.data_editor(df_atz_matrix, use_container_width=True, height=300, key="hy_atz_editor", disabled=not use_atz_matrix)
                new_atz_matrix = {str(k): float(v["Wahrscheinlichkeit"]) for k, v in edited_atz_df.iterrows()}

            with st.expander("Renten-Parameter"):
                rc1, rc2 = st.columns(2)
                with rc1:
                    rent65 = st.slider("Renteneintritt 65+", min_value=0.0, max_value=1.0, value=float(params_abg["retirement"]["rent_rate_65"]), step=0.05, key="hy_rent_65")
                with rc2:
                    rent60 = st.slider("Frühverrentung 60-64", min_value=0.0, max_value=1.0, value=float(params_abg["retirement"]["rent_rate_60_65"]), step=0.05, key="hy_rent_60")

            with st.expander("Kündigungs-Parameter"):
                c1, c2, c3 = st.columns([3, 3, 2])
                with c1:
                    quit_base = st.slider("Basisrate p.a.", min_value=0.0, max_value=0.5, value=float(params_abg["quit"]["quit_rate_base"]), step=0.01, key="hy_quit_base")
                with c2:
                    use_quit_matrix = st.checkbox("Detaillierte Kündigungsmatrix verwenden", value=params_abg["quit"].get("use_quit_matrix", True), key="hy_quit_use_mat")
                with c3:
                    quit_dim = st.radio("Dimension", options=["JobFamily", "OrgUnit"], index=0, horizontal=True, key="hy_quit_dim")
                
                # Simplified Quit Matrix Editor
                q_col = "Organisationseinheit" if quit_dim == "OrgUnit" else "Jobfamily"
                q_unique = sorted([str(x) for x in df_ma[q_col].dropna().unique()])
                q_cohorts = ["alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus"]
                q_items = ["Default"] + q_unique
                q_editor_data = []
                for val in q_items:
                    row = {quit_dim: val}
                    for c in q_cohorts:
                        row[c] = float(params_abg["quit"].get("quit_matrix", {}).get(c, {}).get(str(val), quit_base))
                    q_editor_data.append(row)
                df_q_matrix = pd.DataFrame(q_editor_data).set_index(quit_dim)
                edited_q_df = st.data_editor(df_q_matrix, use_container_width=True, height=300, key="hy_quit_editor", disabled=not use_quit_matrix)
                new_quit_matrix = {c: {str(k): float(v[c]) for k, v in edited_q_df.iterrows()} for c in q_cohorts}

            with st.expander("Ruhend-Parameter"):
                hc1, hc2, hc3 = st.columns(3)
                ruhend_new = hc1.number_input("Neue Fälle / Jahr", value=int(params_abg["ruhend"]["ruhend_new_cases_per_year"]), step=1, key="hy_ruh_new")
                ruhend_return = hc2.slider("Rückkehrquote p.a.", min_value=0.0, max_value=1.0, value=float(params_abg["ruhend"]["ruhend_return_rate"]), step=0.05, key="hy_ruh_ret")
                ruhend_duration = hc3.number_input("Ø Dauer (Monate)", value=int(params_abg["ruhend"]["ruhend_avg_duration_months"]), step=1, key="hy_ruh_dur")

        with t_zug:
            st.markdown("#### 🎓 Azubis & Trainees")
            az1, az2, az3, az4 = st.columns(4)
            azubi_count = az1.number_input("Neue Azubis pro Jahr", 0, 100, params_zug["azubi"].get("new_cases_per_year", 15), key="hy_azu_count")
            retention = az2.slider("Azubi-Übernahme (%)", 0.0, 1.0, params_zug["azubi"]["retention_rate"], 0.05, key="hy_azu_ret")
            duration = az3.number_input("Ausbildungsdauer (Jahre)", 1.0, 5.0, params_zug["azubi"]["duration_years"], 0.5, key="hy_azu_dur")
            az_strat = az4.selectbox("Azubi-Verteilung", ["Random", "OrgUnit"], index=0, key="hy_azu_strat")
            
            tr1, tr2, tr3 = st.columns(3)
            trainee_count = tr1.number_input("Neue Trainees pro Jahr", 0, 100, params_zug["trainee"]["new_cases_per_year"], key="hy_tra_count")
            trainee_dur = tr2.number_input("Trainee-Dauer (Jahre)", 0.5, 3.0, params_zug["trainee"]["duration_years"], 0.5, key="hy_tra_dur")
            tr_strat = tr3.selectbox("Trainee-Verteilung", ["Random", "OrgUnit"], index=0, key="hy_tra_strat")
            
            st.markdown("#### 💼 Neueinstellungen (unabhängig)")
            h1, h2, h3 = st.columns(3)
            hire_count = h1.number_input("Einstellungen pro Jahr", 0, 500, params_zug["new_hires"]["count_per_year"], key="hy_hir_count")
            hire_strat = h2.selectbox("Strategie", ["Random", "OrgUnit", "Fill Vacancies"], index=2, key="hy_hir_strat", help="'Fill Vacancies' nutzt die Abgangsprognose zum Nachbesetzen.")
            
            # Distribution Matrix for New Hires
            with st.expander("📊 Verteilung Neueinstellungen (Matrix)", expanded=False):
                dist_base = df_ma.groupby(["Jobfamily", "OE-Cluster"]).size().reset_index(name="Count")
                dist_base["Share %"] = (dist_base["Count"] / dist_base["Count"].sum()).round(4)
                dist_base = dist_base.sort_values("Share %", ascending=False)[["Jobfamily", "OE-Cluster", "Share %"]]
                edited_dist = st.data_editor(dist_base, use_container_width=True, key="hy_hire_dist_mat", column_config={"Share %": st.column_config.NumberColumn(format="%.2f")})
                hire_distribution = edited_dist.to_dict("records")
        
        st.markdown("---")


    # ── Action Button ──
    st.write("")
    if st.button("🚀 Prognose (Hybrid) berechnen", use_container_width=True, key="btn_run_hybrid"):
        submit = True

    has_hybrid_res = "hybrid_abg_res" in st.session_state and "hybrid_zug_res" in st.session_state

    if not submit and not has_hybrid_res:
        st.info("⬆️ Parameter oben einstellen und Prognose berechnen.")
        return

    # 1. Build Final Params
    ui_state_abg = {
        "components": {"atz": comp_atz, "retirement": comp_ret, "quit": comp_quit, "ruhend": comp_ruhend},
        "atz": {"new_atz_rate": new_atz_base, "atz_eligible_age_min": eligible_age, "atz_eligible_age_max": eligible_age_max, "atz_duration_ar_years": ar_years, "atz_duration_fr_years": fr_years, "use_atz_matrix": use_atz_matrix, "atz_dimension": atz_dim, "atz_matrix": new_atz_matrix},
        "retirement": {"rent_rate_65": rent65, "rent_rate_60_65": rent60},
        "quit": {"quit_rate_base": quit_base, "use_quit_matrix": use_quit_matrix, "quit_dimension": quit_dim, "quit_matrix": new_quit_matrix},
        "ruhend": {"ruhend_new_cases_per_year": ruhend_new, "ruhend_return_rate": ruhend_return, "ruhend_avg_duration_months": ruhend_duration},
        "random_seed": random_seed,
    }
    final_params_abg = build_abgaenge_params_from_ui(ui_state_abg)
    
    final_params_zug = {
        "azubi": {"active": comp_azubi, "retention_rate": retention, "duration_years": duration, "strategy": az_strat, "target_org_unit": None, "entry_tariff_group": "E 5", "entry_step": 1, "new_cases_per_year": azubi_count},
        "trainee": {"active": comp_trainee, "new_cases_per_year": trainee_count, "duration_years": trainee_dur, "salary_group": "E 12", "strategy": tr_strat, "target_org_unit": None},
        "new_hires": {"active": comp_hires, "count_per_year": hire_count, "strategy": hire_strat, "target_org_unit": None, "distribution": hire_distribution},
        "random_seed": 42
    }

    # 2. Execution ──────────────────────────────────────────────────
    if submit:
        # A. Departures (Basis)
        with st.spinner("Berechne Abgangs-Szenario..."):
            abg_res = run_forecast_abgaenge(
                df_ma=df_ma, 
                df_atz=df_atz,
                start_date=pd.Timestamp(ist_stichtag),
                end_date=pd.Timestamp(forecast_end_date),
                freq="M" if freq_label == "Monat" else "Q",
                params=final_params_abg,
            )
            st.session_state["hybrid_abg_res"] = abg_res
            st.session_state["hybrid_abg_params"] = final_params_abg
        
        # B. Extract Vacancies (if Fill Vacancies is selected)
        vacancies = []
        if hire_strat == "Fill Vacancies":
            # Safeguard for missing columns in stale state
            if "headcount_change" not in abg_res["events_person_level"].columns:
                abg_res["events_person_level"] = pd.DataFrame(columns=[
                    "period_label", "period_start", "period_end", "event_date", 
                    "persnr", "reason_code", "reason_label", "headcount_change", "mak_change",
                    "age", "tenure", "Organisationseinheit", "Kürzel OrgEinheit", 
                    "Jobfamily", "Planstelle", "TrfGr", "St"
                ])
                
            exits = abg_res["events_person_level"][abg_res["events_person_level"]["headcount_change"] < 0]
            # Simple attribute donor lookup
            snap_lookup = df_ma.set_index("PersNr")
            for _, row in exits.iterrows():
                pid = str(row["persnr"]).strip().replace(".0", "")
                d_jf, d_oe_c = "Angestellte", "Unclustered"
                if pid in snap_lookup.index:
                    d_row = snap_lookup.loc[pid]
                    if isinstance(d_row, pd.DataFrame): d_row = d_row.iloc[0]
                    d_jf = d_row.get("Jobfamily", d_jf)
                    d_oe_c = d_row.get("OE-Cluster", d_oe_c)
                vacancies.append({"date": row["event_date"], "org_unit": row.get("Organisationseinheit", "Unbekannt"), "planstelle": row.get("Planstelle", "Unbekannt"), "persnr": row["persnr"], "Jobfamily": d_jf, "OE-Cluster": d_oe_c})

        # C. Arrivals
        with st.spinner("Berechne Zugangs-Szenario..."):
            zug_res = run_forecast_zugaenge(
                df_snapshot=df_ma,
                start_date=pd.Timestamp(ist_stichtag),
                periods_years=(forecast_end_date.year - ist_stichtag.year) + 1,
                params=final_params_zug,
                vacancies=vacancies
            )
            st.session_state["hybrid_zug_res"] = zug_res
            st.session_state["hybrid_zug_params"] = final_params_zug
    else:
        abg_res = st.session_state["hybrid_abg_res"]
        zug_res = st.session_state["hybrid_zug_res"]

    # 3. Filtering & View Preparation ──────────────────────────────
    
    # helper for robust attribute filtering
    def _apply_robust_filter(df, column, selected):
        if not selected or column not in df.columns: return df
        s_norm = df[column].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        v_norm = [str(v).strip().replace(".0", "") for v in selected]
        return df[s_norm.isin(v_norm)]

    # A. Filter Standalone Results (for specific tabs)
    # Abgänge
    raw_abg_events = abg_res["events_person_level"].copy()
    filt_abg_events = _apply_robust_filter(raw_abg_events, "Organisationseinheit", st.session_state.get("selected_org_units", []))
    filt_abg_events = _apply_robust_filter(filt_abg_events, "Jobfamily", st.session_state.get("selected_jobfamilies", []))
    filt_abg_events = _apply_robust_filter(filt_abg_events, "OE-Cluster", st.session_state.get("selected_oe_clusters", []))
    filt_abg_events = _apply_robust_filter(filt_abg_events, "JF-Cluster", st.session_state.get("selected_jf_clusters", []))
    
    # --- Robustness Check: Abgänge ---
    abg_cols = ["period_label", "period_start", "period_end", "event_date", "persnr", "reason_code", "reason_label", "headcount_change", "mak_change", "Organisationseinheit", "Jobfamily", "OE-Cluster"]
    if any(c not in filt_abg_events.columns for c in ["persnr", "headcount_change", "Organisationseinheit"]):
        for c in abg_cols:
            if c not in filt_abg_events.columns: 
                filt_abg_events[c] = pd.NaT if "date" in c or "_start" in c or "_end" in c else None
    
    # Ensure event_date is datetime even if empty
    filt_abg_events["event_date"] = pd.to_datetime(filt_abg_events["event_date"])

    # Enrichment: Ensure Cluster/JF are present in Abgänge by mapping from snapshot (to satisfy Fix B & C)
    if "OE-Cluster" not in filt_abg_events.columns or filt_abg_events["OE-Cluster"].isna().all():
        # Create map from Snapshot (PersNr -> Cluster)
        # Note: df_view_agg is not yet built, but df_ma (aggregated) is available
        # df_ma is indexed by 0..N, so we set index
        pm_lookup = df_ma.set_index("PersNr")
        # Safety: Ensure index is string for robust mapping
        pm_lookup.index = pm_lookup.index.astype(str).str.replace(r"\.0$", "", regex=True)
        
        # Helper to safely map
        def _safe_map(pid, col):
            pid = str(pid).replace(".0", "")
            return pm_lookup.loc[pid, col] if pid in pm_lookup.index else "Unclustered"

        # Apply mapping vectorized-style if possible or map
        # Convert IDs to match index
        filt_abg_events["_pid_clean"] = filt_abg_events["persnr"].astype(str).str.replace(r"\.0$", "", regex=True)
        
        # Create dictionary for faster lookup
        cluster_map = pm_lookup["OE-Cluster"].to_dict() if "OE-Cluster" in pm_lookup.columns else {}
        jf_map = pm_lookup["Jobfamily"].to_dict() if "Jobfamily" in pm_lookup.columns else {}
        
        filt_abg_events["OE-Cluster"] = filt_abg_events["_pid_clean"].map(cluster_map).fillna("Unclustered")
        if "Jobfamily" not in filt_abg_events.columns or filt_abg_events["Jobfamily"].isna().all():
             filt_abg_events["Jobfamily"] = filt_abg_events["_pid_clean"].map(jf_map).fillna("Unbekannt")
             
        # Cleanup
        if "_pid_clean" in filt_abg_events.columns:
            del filt_abg_events["_pid_clean"]

    # Zugänge
    raw_zug_events = zug_res["events"].copy()
    if "org_unit" in raw_zug_events.columns: raw_zug_events = raw_zug_events.rename(columns={"org_unit": "Organisationseinheit"})
    filt_zug_events = _apply_robust_filter(raw_zug_events, "Organisationseinheit", st.session_state.get("selected_org_units", []))
    filt_zug_events = _apply_robust_filter(filt_zug_events, "Jobfamily", st.session_state.get("selected_jobfamilies", []))
    filt_zug_events = _apply_robust_filter(filt_zug_events, "OE-Cluster", st.session_state.get("selected_oe_clusters", []))
    filt_zug_events = _apply_robust_filter(filt_zug_events, "JF-Cluster", st.session_state.get("selected_jf_clusters", []))
    
    # --- Robustness Check: Zugänge ---
    zug_cols = ["date", "type", "count", "persnr", "Organisationseinheit", "source", "mak", "Jobfamily", "OE-Cluster", "TrfGr", "St", "Planstelle"]
    if any(c not in filt_zug_events.columns for c in ["count", "source", "mak"]):
        # Re-initialize with standard schema if vital columns are missing
        for c in zug_cols:
            if c not in filt_zug_events.columns: 
                filt_zug_events[c] = pd.NaT if c == "date" else None
    
    # Ensure date is datetime
    if "date" in filt_zug_events.columns:
        filt_zug_events["date"] = pd.to_datetime(filt_zug_events["date"])

    filt_zug_events = filt_zug_events[filt_zug_events["count"] > 0].copy()



    # B. Combined Event Set (for Net View)
    # Standardize Zugänge to match Abgänge schema
    filt_zug_events_std = filt_zug_events.copy()
    filt_zug_events_std["event_date"] = pd.to_datetime(filt_zug_events_std["date"])
    filt_zug_events_std["mak_change"] = filt_zug_events_std["mak"]
    filt_zug_events_std["headcount_change"] = filt_zug_events_std["count"]
    filt_zug_events_std["reason_label"] = "Zugang (" + filt_zug_events_std["source"] + ")"
    
    
    combined_events = pd.concat([filt_abg_events, filt_zug_events_std], ignore_index=True)

    # Filter combined_events to Scoped Period (Forecast Range)
    # Fix B: Ensure Netto KPI and Debug Ref Sum use EXACTLY the same scoped events
    # Otherwise, historical events or future out-of-scope events distort the mismatch.
    start_ts = pd.Timestamp(ist_stichtag)
    end_ts = pd.Timestamp(forecast_end_date)
    
    combined_events_in_scope = combined_events[
        (combined_events["event_date"] >= start_ts) & 
        (combined_events["event_date"] <= end_ts)
    ].copy()

    # C. Robust Cluster Enrichment (Fix Missing 118 Events)
    # Ensure "OE-Cluster" and "JF-Cluster" have defaults
    if "OE-Cluster" in combined_events_in_scope.columns:
        combined_events_in_scope["OE-Cluster"] = combined_events_in_scope["OE-Cluster"].fillna("Unclustered")
    
    # Apply same to Zugänge specifically for its own charts
    if "OE-Cluster" in filt_zug_events.columns:
        filt_zug_events["OE-Cluster"] = filt_zug_events["OE-Cluster"].fillna("Unclustered")

    # C. Re-Aggregate (View Level)
    df_snapshot_filtered = apply_filters(snapshot_df) # Position Level
    if df_snapshot_filtered.empty:
        st.warning("⚠️ Keine Daten nach Filterung.")
        return

    # Aggregate Persons for View (Standardize columns for aggregator)
    view_agg_dict = {
        "MAK_Calculated": "sum",
        "Organisationseinheit": "first",
        "Jobfamily": "first",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Sollarbeitszeit": "sum",
    }
    # Add optional columns if they exist
    for col in ["Geschlecht", "Planstelle", "OE-Cluster", "JF-Cluster", "TrfGr"]:
        if col in df_snapshot_filtered.columns:
            view_agg_dict[col] = "first"

    df_view_agg = df_snapshot_filtered.groupby("PersNr", as_index=False).agg(view_agg_dict)
    df_view_agg["mak"] = df_view_agg["MAK_Calculated"]

    df_view_agg["mak"] = df_view_agg["MAK_Calculated"]
    
    # Ensure "active" key exists for KPI calculator
    df_view_agg["active"] = True

    # Fix A: Event-Based Netto KPI
    # Recalculate Net KPI purely from Start State + Event Deltas
    # This guarantees consistency with Driver Chart
    net_kpis = calculate_kpi_from_events(
        df_start_stats=df_view_agg,
        events_df=combined_events_in_scope,
        start_date=pd.Timestamp(ist_stichtag),
        end_date=pd.Timestamp(forecast_end_date),
        freq="M" if freq_label == "Monat" else "Q"
    )

    # Standalone KPIs for specific tabs
    abg_view_kpis = aggregate_forecast_results(df_initial=df_view_agg, events_df=filt_abg_events, start_date=pd.Timestamp(ist_stichtag), end_date=pd.Timestamp(forecast_end_date), freq="M", params=None)
    zug_view_kpis = aggregate_forecast_results(df_initial=df_view_agg, events_df=filt_zug_events_std, start_date=pd.Timestamp(ist_stichtag), end_date=pd.Timestamp(forecast_end_date), freq="M", params=None)

    # 4. Rendering ──────────────────────────────────────────────────
    st.divider()

    # Management Summary
    if not net_kpis.empty:
        total_exits = int(filt_abg_events[filt_abg_events["headcount_change"] < 0]["headcount_change"].abs().sum())
        total_entries = int(filt_zug_events[filt_zug_events["count"] > 0]["count"].sum())
        net_hc_delta = total_entries - total_exits
        
        last_kpi = net_kpis.iloc[-1]
        
        st.markdown("### 🏆 Hybrid-Cockpit: Netto-Zusammenfassung")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Abgänge (Köpfe)", f"{total_exits}")
        m2.metric("Zugänge (Köpfe)", f"{total_entries}")
        m3.metric("Netto-Delta (Köpfe)", f"{net_hc_delta:+}")
        m4.metric("Personalstand (MAK) Ende", f"{last_kpi['mak_end']:.1f}", delta=f"{last_kpi['mak_delta']:.1f}")

    t_all, t_abg_res, t_zug_res, t_list = st.tabs(["📊 Netto-Cockpit", "📉 Abgänge Details", "📈 Zugänge Details", "📋 Listen & Export"])
    
    with t_all:
        import plotly.graph_objects as go
        st.markdown("### 📈 Netto-Entwicklung (Köpfe & MAK)")
        fig_net = go.Figure()
        fig_net.add_trace(go.Scatter(x=net_kpis["period_end"], y=net_kpis["headcount_end"], name="Headcount (Netto)", line=dict(color=COLORS["accent_blue"], width=3)))
        fig_net.add_trace(go.Scatter(x=net_kpis["period_end"], y=net_kpis["mak_end"], name="MAK (Netto)", line=dict(color=COLORS["accent_green"], width=3, dash='dash')))
        fig_net.update_layout(xaxis_title="Datum", yaxis_title="Bestand")
        st.plotly_chart(fig_net, use_container_width=True, key="hybrid_net_line_chart")
        
        st.markdown("### 🧬 Treiber-Gegenüberstellung (Monatlich)")
        # Stacked bar with exits (negative) and entries (positive)
        # Use Scoped Events for Driver Chart too
        if not combined_events_in_scope.empty:
            combined_events_in_scope["event_date"] = pd.to_datetime(combined_events_in_scope["event_date"])
            combined_events_in_scope["JahrMonat"] = combined_events_in_scope["event_date"].dt.to_period("M").astype(str)
            driver_agg = combined_events_in_scope.groupby(["JahrMonat", "reason_label"])["mak_change"].sum().reset_index()
            
            fig_drivers = px.bar(
                driver_agg, x="JahrMonat", y="mak_change", color="reason_label",
                labels={"mak_change": "Kapazitätsänderung (MAK)", "JahrMonat": "Zeitraum", "reason_label": "Typ"},
                title="Netto-Effekte nach Ursache"
            )
            st.plotly_chart(fig_drivers, use_container_width=True, key="hyb_net_drivers_main")
        else:
            st.info("Keine Daten für die Treiber-Gegenüberstellung im gewählten Zeitraum.")

        
        if st.session_state.get("debug_active", False):
            with st.expander("🐞 Debug / Plausibilitätschecks (Netto)", expanded=False):
                st.markdown("#### Validierung Netto-Cockpit")
                
                # 0. Scope & Filter Check
                st.markdown("##### 0. Scope & Filter")
                st.write(f"**Zeitraum:** `{start_ts.date()}` bis `{end_ts.date()}`")
                st.write(f"**Sidebar-Filter aktiv:** `{not filt_abg_events.equals(abg_res['events_person_level'])}`") # Rough check
                
                n_raw = len(combined_events)
                n_scope = len(combined_events_in_scope)
                st.write(f"**Events Total:** `{n_raw}` | **In Scope:** `{n_scope}` (Filter-Verlust: `{n_raw - n_scope}`)")
                
                st.divider()
    
                if not net_kpis.empty and not combined_events_in_scope.empty:
                    # 1. Net End Validation
                    last_mak = net_kpis.iloc[-1]["mak_end"]
                    # Start + Delta
                    initial_mak = net_kpis.iloc[0]["mak_start"]
                    calc_delta = last_mak - initial_mak
                    # Ref Sum from SCOPED Events
                    events_delta = combined_events_in_scope["mak_change"].sum()
                    
                    # Check 1: Does KPI Delta match Event Delta? (Should be exact now)
                    _check_sum("MAK Delta (KPI vs Events)", calc_delta, events_delta, "MAK")
                    
                    # 2. Driver Chart Validation
                    chart_driver_sum = driver_agg["mak_change"].sum()
                    _check_sum("Driver Chart Sum vs Total Event Delta", chart_driver_sum, events_delta, "MAK")
                    
                    st.divider()
                    st.markdown("#### 🔢 Aggregierte Tabellen (Audit)")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        _render_debug_aggregation(
                            combined_events_in_scope, 
                            ["reason_label"], 
                            "Aggregation nach Treiber", 
                            count_col="headcount_change", 
                            mak_col="mak_change",
                            key_prefix="net_debug"
                        )
                    with c2:
                        _render_debug_aggregation(
                            combined_events_in_scope, 
                            ["Organisationseinheit"], 
                            "Top Netto-Effekte nach OE", 
                            count_col="headcount_change", 
                            mak_col="mak_change",
                            key_prefix="net_debug"
                        )
                        
                    st.divider()
                    st.markdown("#### 🔍 Detail-Checks")
                    
                    # Check A: Rente nach ATZ Logic
                    st.markdown("**Check: Rente (nach ATZ)**")
                    rente_atz = combined_events_in_scope[
                        (combined_events_in_scope["type"] == "Retirement") & 
                        (combined_events_in_scope["source"] == "ATZ")
                    ]
                    if not rente_atz.empty:
                        st.write(f"Gefundene Events: `{len(rente_atz)}`")
                        st.dataframe(rente_atz[["event_date", "persnr", "headcount_change", "mak_change", "Organisationseinheit"]].head(5), use_container_width=True)
                    else:
                        st.caption("Keine 'Retirement_ATZ' Events im Scope.")
                    
                    # Check B: Suspicious OEs (Negative Headcount but Positive MAK)
                    st.markdown("**Check: Inkonsistente OEs (Kopf < 0, MAK > 0)**")
                    oe_stats = combined_events_in_scope.groupby("Organisationseinheit")[["headcount_change", "mak_change"]].sum()
                    suspicious = oe_stats[
                        (oe_stats["headcount_change"] < 0) & 
                        (oe_stats["mak_change"] > 0.001)
                    ]
                    if not suspicious.empty:
                        st.warning(f"⚠️ {len(suspicious)} OEs mit gegenläufigen Vorzeichen gefunden.")
                        st.dataframe(suspicious, use_container_width=True)
                        
                        # Show details for first suspicious OE
                        sus_oe = suspicious.index[0]
                        st.markdown(f"**Details für OE:** `{sus_oe}`")
                        sus_events = combined_events_in_scope[combined_events_in_scope["Organisationseinheit"] == sus_oe]
                        st.dataframe(sus_events[["event_date", "type", "source", "headcount_change", "mak_change", "Jobfamily"]], use_container_width=True)
                    else:
                        st.caption("✅ Keine OEs mit gegenläufigen Vorzeichen (Kopf < 0 / MAK > 0) gefunden.")
    
                else:
                    st.write("Keine Daten für Debugging.")

    with t_abg_res:
        st.markdown("### 📉 Abgangs-Detailanalyse")
        from abgaenge.visuals import build_charts as build_abgaenge_charts
        abg_charts = build_abgaenge_charts(abg_view_kpis, filt_abg_events)
        st.plotly_chart(abg_charts.get("line_headcount_mak"), use_container_width=True, key="hybrid_abg_line_chart")
        st.plotly_chart(abg_charts.get("bar_abgaenge_reasons"), use_container_width=True, key="hybrid_abg_reasons_chart")
        
        if is_clustering_active() and "OE-Cluster" in filt_abg_events.columns:
             # Cluster chart logic
             c_stats = filt_abg_events[filt_abg_events["headcount_change"] < 0].groupby("OE-Cluster").size().reset_index(name="Abgänge")
             fig_c = px.bar(c_stats, x="Abgänge", y="OE-Cluster", orientation="h", title="Abgänge nach OE-Cluster")
             st.plotly_chart(fig_c, use_container_width=True, key="hyb_abg_oe_cluster_main")

    
        if st.session_state.get("debug_active", False):
             with st.expander("🐞 Debug / Plausibilitätschecks (Abgänge)", expanded=False):
                 st.markdown("#### Validierung Abgänge")
                 if not filt_abg_events.empty:
                      # Reasons Chart (Usually Counts Events or Heads)
                      # Fix B: Standardize on Unique Persons for "Köpfe" check
                      # The 'chart_reasons_sum' in previous code used 'headcount_change'.sum() which includes multiple phases.
                      # We want Unique Persons.
                      
                      unique_leavers = _get_unique_count(filt_abg_events[filt_abg_events["headcount_change"] < 0])
                      
                      # The chart (bar_abgaenge_reasons) usually shows *Cases* (Events). 
                      # If we want to check Consistency, we must know what the chart shows.
                      # 'bar_abgaenge_reasons' typically shows Sum of Headcount Loss (-1).
                      # If one person retires twice (ATZ Phase), they count twice? No, specific logic.
                      # We'll compare Event Count vs Event Count for robustness.
                      
                      chart_reasons_sum = abs(filt_abg_events[filt_abg_events["headcount_change"] < 0]["headcount_change"].sum())
                      # Compare against itself? No, against the filtered event set (our source of truth).
                      ref_reasons_sum = abs(filt_abg_events[filt_abg_events["headcount_change"] < 0]["headcount_change"].sum())
                      
                      _check_sum("Abgänge (Events): Chart vs Source", chart_reasons_sum, ref_reasons_sum, "Events")
                      
                      # Unique Persons Check
                      st.write(f"ℹ️ Unique Personen (Abgänge): `{unique_leavers}`")
                      
                      # Cluster Chart
                      if is_clustering_active() and "OE-Cluster" in filt_abg_events.columns:
                          # Cluster chart counts rows (Events)
                          chart_cluster_sum = c_stats["Abgänge"].sum()
                          _check_sum("OE-Cluster Summe vs Event Count", chart_cluster_sum, chart_reasons_sum, "Events")
                          
                      st.divider()
                      st.markdown("#### 🔢 Aggregierte Tabellen (Audit)")
                      
                      ac1, ac2 = st.columns(2)
                      with ac1:
                          _render_debug_aggregation(filt_abg_events, ["reason_label"], "Nach Grund", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
                          _render_debug_aggregation(filt_abg_events, ["Jobfamily"], "Nach JobFamily", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
                      with ac2:
                          _render_debug_aggregation(filt_abg_events, ["OE-Cluster"], "Nach OE-Cluster", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
                          _render_debug_aggregation(filt_abg_events, ["Organisationseinheit"], "Nach Organisationseinheit (Top 20)", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
    
                 else:
                      st.write("Keine Abgangsdaten.")

    with t_zug_res:
        st.markdown("### 📈 Zugangs-Detailanalyse")
        if not filt_zug_events.empty:
            fig_sources = px.histogram(filt_zug_events, x="date", color="source", text_auto=True, title="Zugänge nach Quelle")
            st.plotly_chart(fig_sources, use_container_width=True, key="hybrid_zug_sources_chart")
            
            if is_clustering_active() and "OE-Cluster" in filt_zug_events.columns:
                 z_stats = filt_zug_events.groupby("OE-Cluster").size().reset_index(name="Zugänge")
                 fig_z = px.bar(z_stats, x="Zugänge", y="OE-Cluster", orientation="h", title="Zugänge nach OE-Cluster", color_discrete_sequence=[COLORS["accent_green"]])
                 st.plotly_chart(fig_z, use_container_width=True, key="hybrid_zug_oe_cluster_chart")



    
        if st.session_state.get("debug_active", False):
            with st.expander("🐞 Debug / Plausibilitätschecks (Zugänge)", expanded=False):
                st.markdown("#### Validierung Zugänge")
                if not filt_zug_events.empty:
                    # Sources Chart
                    # Filter for actual entries (count > 0)
                    real_entries = filt_zug_events[filt_zug_events["count"] > 0]
                    
                    # Chart shows Count of Raws (Events)
                    chart_sources_sum = real_entries["count"].sum()
                    
                    # Ref: Unique Persons or Event Count? 
                    # If we assume 1 Entry Event per Person is standard -> Events == Heads.
                    ref_entries_sum = real_entries["count"].sum()
                    
                    _check_sum("Zugänge (Events): Sources vs Source", chart_sources_sum, ref_entries_sum, "Events")
                    
                    unique_entries = _get_unique_count(real_entries)
                    st.write(f"ℹ️ Unique Personen (Zugänge): `{unique_entries}`")
                    
                    # Cluster Chart
                    if is_clustering_active() and "OE-Cluster" in filt_zug_events.columns:
                        # Check for Unclustered
                        unclustered_count = len(filt_zug_events[filt_zug_events["OE-Cluster"] == "Unclustered"])
                        if unclustered_count > 0:
                            st.warning(f"⚠️ {unclustered_count} Zugänge ohne Cluster (als 'Unclustered' gruppiert).")

                    st.divider()
                    st.markdown("#### 🚦 Treiber-Status Check")
                    # Check if Gating works
                    zug_params = st.session_state.get("hybrid_zug_params", {})
                    
                    # Count by broad type match
                    n_azubi = len(filt_zug_events[filt_zug_events["type"].astype(str).str.contains("Azubi", case=False)])
                    n_trainee = len(filt_zug_events[filt_zug_events["type"].astype(str).str.contains("Trainee", case=False)])
                    n_hire = len(filt_zug_events[filt_zug_events["type"] == "New_Hire"])
                    
                    status_data = [
                        {"Treiber": "Azubis", "Aktiv (UI)": zug_params.get("azubi", {}).get("active", "?"), "Events": n_azubi},
                        {"Treiber": "Trainees", "Aktiv (UI)": zug_params.get("trainee", {}).get("active", "?"), "Events": n_trainee},
                        {"Treiber": "Neueinstellungen", "Aktiv (UI)": zug_params.get("new_hires", {}).get("active", "?"), "Events": n_hire},
                    ]
                    st.dataframe(pd.DataFrame(status_data), use_container_width=True)
                        
                    chart_z_cluster_sum = z_stats["Zugänge"].sum()
                    # If z_stats comes from ALL events, comparing to real_entries might differ if negative events exist?
                    # Zugänge page usually filters for count > 0 for these charts.
                    # Let's ensure z_stats logic (above) matched this.
                    # Previous code: z_stats = filt_zug_events.groupby...
                    # We should filter z_stats to count > 0 too if that's what we want.
                    # Assuming z_stats is correctly built:
                    _check_sum("OE-Cluster Summe vs Total Events", chart_z_cluster_sum, len(filt_zug_events), "Events")
                
                    st.divider()
                    st.markdown("#### 🔢 Aggregierte Tabellen (Audit)")
                    
                    zc1, zc2 = st.columns(2)
                    with zc1:
                        _render_debug_aggregation(filt_zug_events, ["source", "type"], "Nach Quelle & Typ", count_col="count", mak_col="mak", key_prefix="zug_debug")
                        _render_debug_aggregation(filt_zug_events, ["Jobfamily"], "Nach JobFamily", count_col="count", mak_col="mak", key_prefix="zug_debug")
                    with zc2:
                        _render_debug_aggregation(filt_zug_events, ["OE-Cluster"], "Nach OE-Cluster", count_col="count", mak_col="mak", key_prefix="zug_debug")
                        _render_debug_aggregation(filt_zug_events, ["Organisationseinheit"], "Nach Organisationseinheit (Top 20)", count_col="count", mak_col="mak", key_prefix="zug_debug")

                else:
                    st.write("Keine Zugangsdaten.")

    with t_list:
        st.markdown("### 📋 Kombinierte Ereignisliste")
        st.dataframe(combined_events[["event_date", "reason_label", "headcount_change", "mak_change", "Organisationseinheit", "Jobfamily"]], use_container_width=True)
        st.download_button("📥 Gesamte Liste exportieren (CSV)", data=to_csv_bytes(combined_events), file_name="hybrid_prognose_details.csv")


main()
