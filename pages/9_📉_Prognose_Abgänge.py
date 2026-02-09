"""
Streamlit page: Abgänge Prognose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import date

import pandas as pd
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
    load_inputs,
    # preprocess_inputs, # Removed, done by loader
    default_params,
    build_params_from_ui,
    run_forecast_abgaenge,
    validate_outputs,
    build_charts,
    to_csv_bytes,
)

# Shared Components
from dataloader.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters


@st.cache_data
def _load_atz_cached(base_path_str: str):
    """Loads only ATZ data needed for forecast engine details."""
    _, df_atz = load_inputs(Path(base_path_str))
    return df_atz


def main():
    st.title("📉 Prognose: Abgänge")
    st.write("Prognose von Abgängen (ATZ, Rente, Kündigung, Ruhend) mit klarer Trennung von MAK und Headcount.")

    try:
        # 1. Load Central Data (Consistent with Kompakt)
        snapshot_df, history_df, _, _ = load_and_prepare_data()

        # 2. Render Sidebar Filters (Standard Dashboard Logic)
        render_global_filters(snapshot_df, history_df)
        
        # 3. Apply Filters (Settings exclusions + Sidebar selections)
        df_ma_filtered = apply_filters(snapshot_df)
        
        # 4. Load ATZ Details (needed for engine phases)
        df_atz = _load_atz_cached(str(BASE_PATH))
        
        # 5. Preprocessing for Forecast Engine (CRITICAL fix)
        # The snapshot_df is position-level data. Employees with multiple positions
        # contribute MAK from each position (e.g., 2x 50% = 1.0 FTE total).
        # The forecast engine expects employee-level data (1 row per person).
        # Solution: Pre-aggregate MAK by employee to match Kompakt page logic.
        
        # Remove Vacancies
        df_ma_filtered = df_ma_filtered.dropna(subset=["PersNr"])
        
        # Calculate MAK for each position (same logic as Kompakt)
        # Use berechne_mak function from loader
        from dataloader.loader import berechne_mak
        
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
        # Replaces slow apply(lambda...) with calculate_mak_vectorized
        from dataloader.loader import calculate_mak_vectorized
        df_ma_filtered = calculate_mak_vectorized(df_ma_filtered, atz_fr_persnr_set)
        
        # Aggregate by employee: sum MAK, keep first occurrence of other attributes
        agg_dict = {
            "MAK_Calculated": "sum",  # Sum MAK across all positions
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Sollarbeitszeit": "sum",  # Sum work hours across positions
            "Organisationseinheit": "first", # Preserve OrgUnit for Analytics
        }
        
        # Optional: include other columns if they exist
        for col in ["Geschlecht", "Organisationseinheit", "Planstelle"]:
            if col in df_ma_filtered.columns:
                agg_dict[col] = "first"
        
        df_employee_agg = df_ma_filtered.groupby("PersNr", as_index=False).agg(agg_dict)
        
        # Backcalculate BsGrd from aggregated MAK for engine compatibility
        # Forecast Engine Logic: MAK = (BsGrd/100) * (Soll/39)
        # We want: MAK = MAK_Calculated
        # So we set Soll = 39.0 (Factor=1) and BsGrd = MAK_Calculated * 100
        import numpy as np
        
        # 1. Neutralize Soll-Factor in Engine
        df_employee_agg["Sollarbeitszeit"] = 39.0
        
        # 2. Set BsGrd to match desired MAK exactly
        df_employee_agg["BsGrd"] = df_employee_agg["MAK_Calculated"] * 100.0
        
        # Use aggregated data for forecast
        df_ma = df_employee_agg
        
    except FileNotFoundError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(f"Fehler beim Laden/Filtern der Daten: {e}")
        # st.json(e) # Debug
        return
    
    # Use the filtered dataframe as input for the forecast
    df_ma = df_ma_filtered

    params = default_params()

    default_start = get_current_stichtag().date()
    default_end = date(default_start.year + 2, default_start.month, default_start.day)

    with st.sidebar.form("abgaenge_form"):
        st.markdown("### Basis")
        ist_stichtag = st.date_input("Ist-Stichtag", value=default_start)
        forecast_end_date = st.date_input("Prognose-Ende", value=default_end)
        freq_label = st.selectbox("Frequenz", options=["Monat", "Quartal"], index=0)
        random_seed = st.number_input("Random Seed", value=int(params["random_seed"]), step=1)

        st.markdown("### Komponenten")
        comp_atz = st.checkbox("ATZ", value=params["components"]["atz"])
        comp_ret = st.checkbox("Rente", value=params["components"]["retirement"])
        comp_quit = st.checkbox("Kündigung", value=params["components"]["quit"])
        comp_ruhend = st.checkbox("Ruhend", value=params["components"]["ruhend"])

        with st.expander("ATZ-Parameter"):
            new_atz = st.number_input("Neue ATZ-Fälle pro Jahr", value=int(params["atz"]["new_atz_cases_per_year"]), step=1)
            eligible_age = st.number_input("ATZ-Mindestalter", value=int(params["atz"]["atz_eligible_age_min"]), step=1)
            ar_years = st.number_input("AR-Dauer (Jahre)", value=float(params["atz"]["atz_duration_ar_years"]), step=0.5)
            fr_years = st.number_input("FR-Dauer (Jahre)", value=float(params["atz"]["atz_duration_fr_years"]), step=0.5)

        with st.expander("Renten-Parameter"):
            rent65 = st.slider("Renteneintritt 65+", min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_65"]), step=0.05)
            rent60 = st.slider("Frühverrentung 60-64", min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_60_65"]), step=0.05)

        with st.expander("Kündigungs-Parameter"):
            quit_base = st.slider("Basisrate p.a.", min_value=0.0, max_value=0.5, value=float(params["quit"]["quit_rate_base"]), step=0.01)
            use_matrix = st.checkbox("Matrix Alter x Tenure verwenden", value=params["quit"]["use_quit_matrix"])
            matrix_json = st.text_area(
                "Optional: Matrix als JSON (überschreibt Defaults)",
                value=json.dumps(params["quit"]["quit_rate_matrix"], ensure_ascii=False, indent=2),
                height=200,
            )

        with st.expander("Ruhend-Parameter"):
            ruhend_new = st.number_input("Neue Ruhend-Fälle pro Jahr", value=int(params["ruhend"]["ruhend_new_cases_per_year"]), step=1)
            ruhend_return = st.slider("Rückkehrquote p.a.", min_value=0.0, max_value=1.0, value=float(params["ruhend"]["ruhend_return_rate"]), step=0.05)
            ruhend_duration = st.number_input("Ø Dauer (Monate)", value=int(params["ruhend"]["ruhend_avg_duration_months"]), step=1)

        submit = st.form_submit_button("Prognose berechnen")

    if not submit:
        st.info("Parameter wählen und Prognose berechnen.")
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
            "new_atz_cases_per_year": new_atz,
            "atz_eligible_age_min": eligible_age,
            "atz_duration_ar_years": ar_years,
            "atz_duration_fr_years": fr_years,
        },
        "retirement": {
            "rent_rate_65": rent65,
            "rent_rate_60_65": rent60,
        },
        "quit": {
            "quit_rate_base": quit_base,
            "use_quit_matrix": use_matrix,
        },
        "ruhend": {
            "ruhend_new_cases_per_year": ruhend_new,
            "ruhend_return_rate": ruhend_return,
            "ruhend_avg_duration_months": ruhend_duration,
        },
        "random_seed": random_seed,
        "quit_matrix_json": matrix_json,
    }

    params = build_params_from_ui(ui_state)
    freq = "M" if freq_label == "Monat" else "Q"

    try:
        result = run_forecast_abgaenge(
            df_ma=df_ma,
            df_atz=df_atz,
            start_date=pd.Timestamp(ist_stichtag),
            end_date=pd.Timestamp(forecast_end_date),
            freq=freq,
            params=params,
        )
    except Exception as e:
        st.error(f"Fehler in der Prognose: {e}")
        return

    forecast_kpis = result["forecast_kpis"]
    events = result["events_person_level"]
    
    # Feature: Enrich events with Organisationseinheit (Last Known)
    # df_ma has aggregated info per employee, including OrgUnit (added to agg_dict)
    if not events.empty and "Organisationseinheit" in df_ma.columns:
        # Ensure join keys are compatible (str)
        events["persnr_str"] = events["persnr"].astype(str)
        df_ma["PersNr_str"] = df_ma["PersNr"].astype(str)
        
        events = events.merge(
            df_ma[["PersNr_str", "Organisationseinheit"]],
            left_on="persnr_str",
            right_on="PersNr_str",
            how="left"
        )
        events["Organisationseinheit"] = events["Organisationseinheit"].fillna("Unbekannt")
        # Cleanup temp cols
        events.drop(columns=["persnr_str", "PersNr_str"], inplace=True)

    # KPI Metrics
    if not forecast_kpis.empty:
        first = forecast_kpis.iloc[0]
        last = forecast_kpis.iloc[-1]

        exits_total = int(forecast_kpis["exit_count"].sum())
        mak_loss_total = float(forecast_kpis["mak_loss_gross"].sum())
        avg_headcount = float(forecast_kpis[["headcount_start", "headcount_end"]].mean(axis=1).mean())
        abgangsquote = (exits_total / avg_headcount) if avg_headcount > 0 else 0.0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Headcount Start", f"{int(first['headcount_start'])}")
        col2.metric("Headcount Ende", f"{int(last['headcount_end'])}", delta=int(last["headcount_delta"]))
        col3.metric("MAK Start", f"{first['mak_start']:.1f}")
        col4.metric("MAK Ende", f"{last['mak_end']:.1f}", delta=f"{last['mak_delta']:.1f}")

        col5, col6, col7 = st.columns(3)
        col5.metric("Abgänge gesamt", f"{exits_total}")
        col6.metric("MAK Verlust gesamt", f"{mak_loss_total:.1f}")
        col7.metric("Abgangsquote", f"{abgangsquote*100:.1f}%")

    charts = build_charts(forecast_kpis, events)

    tab1, tab2, tab3 = st.tabs(["Überblick", "Treiber Details", "Personenlisten Export"])

    with tab1:
        st.plotly_chart(charts.get("line_headcount_mak"), use_container_width=True)
        st.plotly_chart(charts.get("bar_abgaenge_reasons"), use_container_width=True)
        
        # New OrgUnit Chart
        if "Organisationseinheit" in events.columns:
            st.markdown("### 🏢 Prognostizierte Abgänge nach Organisationseinheit")
            
            # Aggregate
            exclude_units = ["Unbekannt", None]
            org_events = events[~events["Organisationseinheit"].isin(exclude_units)]
            if not org_events.empty:
                org_stats = org_events.groupby("Organisationseinheit").size().reset_index(name="Abgänge")
                # Top 10 desc
                org_stats = org_stats.sort_values("Abgänge", ascending=True).tail(15) 
                
                import plotly.express as px
                fig_org = px.bar(
                    org_stats, 
                    x="Abgänge", 
                    y="Organisationseinheit", 
                    orientation="h",
                    title="Top 15 Organisationseinheiten nach Abgängen",
                    text="Abgänge",
                    color="Abgänge",
                    color_continuous_scale="Reds"
                )
                fig_org.update_layout(yaxis_title=None, showlegend=False)
                st.plotly_chart(fig_org, use_container_width=True)

        st.dataframe(forecast_kpis, use_container_width=True)

    with tab2:
        for key, fig in charts.items():
            if key.startswith("driver_"):
                st.plotly_chart(fig, use_container_width=True)

        tables = result.get("tables", {})
        if events.empty or not tables:
            st.info("Keine Treiber-Events vorhanden.")
        else:
            for name, df in tables.items():
                if df is None or df.empty:
                    continue
                st.markdown(f"**{name.capitalize()}**")
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
