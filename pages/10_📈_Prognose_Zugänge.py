import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date
import sys
import os

# Add parent dir to path if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataloader.loader import load_and_prepare_data, calculate_cost_vectorized, load_original_data
from kpi_reference import get_current_stichtag
from config.settings import COLORS, TARIFF_GROUPS
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as default_abgaenge_params
from zugaenge.params import default_params as default_zugaenge_params, get_strategies
from zugaenge.forecast import run_forecast_zugaenge

st.set_page_config(page_title="Prognose: Zugänge", page_icon="📈", layout="wide")

def main():
    st.title("📈 Prognose: Zugänge")
    st.write("Prognose von Neueinstellungen (Azubis, Trainees, Externe) und deren Auswirkung auf Headcount/MAK.")

    try:
        # 1. Load Central Data
        snapshot_df_raw, history_df, _, _ = load_and_prepare_data()
        
        # Load ATZ for correct MAK calculation (Start Stats consistency)
        raw_data = load_original_data()
        df_atz = raw_data.get("atz", pd.DataFrame())
        
        # 2. Render Sidebar Filters
        from components.sidebar import render_global_filters, apply_filters
        render_global_filters(snapshot_df_raw, history_df)
        
        # 3. Apply Filters
        df_ma_filtered = apply_filters(snapshot_df_raw)
        
        # 4. Preprocessing (Align with Abgänge/Kompakt)
        
        # Determine ATZ FR set for MAK calc
        atz_fr_persnr_set = set()
        if not df_atz.empty and "Phase" in df_atz.columns:
            stichtag_ts = pd.Timestamp(get_current_stichtag())
            atz_fr = df_atz[
                (df_atz["Phase"] == "FR") & 
                (df_atz["Beginn"] <= stichtag_ts) & 
                (df_atz["Ende"] >= stichtag_ts)
            ]
            if not atz_fr.empty:
                atz_fr_persnr_set = set(atz_fr["PersNr"].astype(str).unique())

        # Calculate MAK Vectorized
        from dataloader.loader import calculate_mak_vectorized
        df_ma_filtered = calculate_mak_vectorized(df_ma_filtered, atz_fr_persnr_set=atz_fr_persnr_set)
        
        # Aggregate by Employee
        agg_dict = {
            "MAK_Calculated": "sum",
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Organisationseinheit": "first",
            "Jobfamily": "first",
            "TrfGr": "first",
            "St": "first",
            "active": "first" # calculated?
        }
        # Include active check
        if "active" not in df_ma_filtered.columns:
             # Logic from loader: active if no exit or exit > now? 
             # Loader doesn't explicitly set 'active'. Abgänge uses snapshot logic.
             # We assume filtered data is the base.
             pass

        # Group
        # available cols only
        valid_agg = {k: v for k, v in agg_dict.items() if k in df_ma_filtered.columns}
        
        df_employee_agg = df_ma_filtered.groupby("PersNr", as_index=False).agg(valid_agg)
        
        # Set bsgrd/mak for engine
        df_employee_agg["Sollarbeitszeit"] = 39.0
        df_employee_agg["BsGrd"] = df_employee_agg.get("MAK_Calculated", 1.0) * 100.0
        df_employee_agg["mak"] = df_employee_agg.get("MAK_Calculated", 1.0)
        df_employee_agg["active"] = True # Assumption for snapshot
        
        snapshot_df = df_employee_agg.copy()

    except Exception as e:
        st.error(f"Fehler beim Laden der Daten: {e}")
        return

    # 2. Defaults
    params = default_zugaenge_params()
    default_start = get_current_stichtag().date()
    default_end = date(default_start.year + 3, default_start.month, default_start.day)
    
    # 3. Settings UI
    with st.expander("⚙️ Prognose-Einstellungen", expanded=True):
        st.markdown("##### 📅 Zeitraum")
        col_date1, col_date2 = st.columns(2)
        start_date = col_date1.date_input("Startdatum", value=default_start)
        end_date = col_date2.date_input("Enddatum", value=default_end)
        
        st.divider()
        
        # Azubis
        st.subheader("🎓 1. Azubi-Übernahme")
        c1, c2, c3 = st.columns(3)
        retention = c1.slider("Übernahmequote (%)", 0.0, 1.0, params["azubi"]["retention_rate"], 0.05)
        duration = c2.number_input("Ausbildungsdauer (Jahre)", 1.0, 5.0, params["azubi"]["duration_years"], 0.5)
        az_strat = c3.selectbox("Verteilung", ["Random", "OrgUnit"], index=0 if params["azubi"]["strategy"] == "Random" else 1, key="az_strat")
        
        azubi_target = None
        if az_strat == "OrgUnit":
            # Use units from original raw data or aggregated? Aggregated is fine.
            units = sorted(snapshot_df["Organisationseinheit"].dropna().astype(str).unique())
            azubi_target = st.selectbox("Ziel-Einheit (Azubi)", units, key="az_unit")
            
        st.divider()
        
        # Trainees
        st.subheader("🚀 2. Trainee-Programm")
        t1, t2, t3, t4 = st.columns(4)
        trainee_count = t1.number_input("Neue Trainees pro Jahr", 0, 100, params["trainee"]["new_cases_per_year"])
        trainee_dur = t2.number_input("Dauer (Jahre)", 0.5, 3.0, params["trainee"]["duration_years"], 0.5)
        trainee_sal = t3.selectbox("Einstiegsgehalt", TARIFF_GROUPS, index=TARIFF_GROUPS.index(params["trainee"]["salary_group"]) if params["trainee"]["salary_group"] in TARIFF_GROUPS else 12)
        tr_strat = t4.selectbox("Verteilung", ["Random", "OrgUnit"], index=0, key="tr_strat")
        
        trainee_target = None
        if tr_strat == "OrgUnit":
             units = sorted(snapshot_df["Organisationseinheit"].dropna().astype(str).unique())
             trainee_target = st.selectbox("Ziel-Einheit (Trainee)", units, key="tr_unit")
             
        st.divider()
        
        # New Hires
        st.subheader("💼 3. Neueinstellungen")
        h1, h2, h3 = st.columns(3)
        hire_count = h1.number_input("Einstellungen pro Jahr", 0, 500, params["new_hires"]["count_per_year"])
        hire_strat = h2.selectbox(
            "Strategie", 
            ["Random", "OrgUnit", "Fill Vacancies"], 
            index=2, # Default Fill Vacancies
            help="'Fill Vacancies' nutzt die Abgangsprognose (Standard-Parameter), um Lücken zu füllen.",
            key="hi_strat"
        )
        
        hire_target = None
        if hire_strat == "OrgUnit":
             units = sorted(snapshot_df["Organisationseinheit"].dropna().astype(str).unique())
             hire_target = st.selectbox("Ziel-Einheit (Hire)", units, key="hi_unit")
             
        submit = st.button("🚀 Prognose berechnen", use_container_width=True)
        
    if submit:
        # Build Params
        run_params = {
            "azubi": {
                "retention_rate": retention,
                "duration_years": duration,
                "strategy": az_strat,
                "target_org_unit": azubi_target
            },
            "trainee": {
                "new_cases_per_year": trainee_count,
                "duration_years": trainee_dur,
                "salary_group": trainee_sal,
                "strategy": tr_strat,
                "target_org_unit": trainee_target
            },
            "new_hires": {
                "count_per_year": hire_count,
                "strategy": hire_strat,
                "target_org_unit": hire_target
            },
            "random_seed": 42
        }
        
        vacancies = []
        if hire_strat == "Fill Vacancies":
            with st.spinner("Berechne Abgangsprognose für Lückenfüllung..."):
                try:
                    # df_atz is already loaded at start of main
                    
                    abg_res = run_forecast_abgaenge(
                        df_ma=snapshot_df, # Use aggregated DF!
                        df_atz=df_atz,
                        start_date=pd.Timestamp(start_date),
                        end_date=pd.Timestamp(end_date),
                        freq="M",
                        params=st.session_state.get("abgaenge_params", default_abgaenge_params())
                    )
                    
                    # Extract Vacancies
                    events = abg_res["events_person_level"]
                    exits = events[events["headcount_change"] < 0]
                    # Convert to vacancy list
                    for _, row in exits.iterrows():
                        vacancies.append({
                            "date": row["event_date"],
                            "org_unit": row.get("Organisationseinheit", "Unbekannt"),
                            "persnr": row["persnr"]
                        })
                    
                    # Enrich vacancies
                    vac_df = pd.DataFrame(vacancies)
                    if not vac_df.empty:
                        # Join not strictly needed if OrgUnit is in events
                        pass

                except Exception as e:
                    st.warning(f"Konnte Abgänge nicht berechnen ({e}). Nutze Zufallsverteilung.")
                    vacancies = []

        # Run Zugänge Forecast
        res = run_forecast_zugaenge(
            df_snapshot=snapshot_df,
            start_date=pd.Timestamp(start_date),
            periods_years=(end_date.year - start_date.year) + 1, # Approx
            params=run_params,
            vacancies=vacancies
        )
        
        events_df = res["events"]
        forecast_kpis = res["forecast_kpis"]
        
        if events_df.empty:
            st.warning("Keine Zugänge prognostiziert.")
        else:
            # ── Ergebnisse ──────────────────────────────────────────────────
            st.divider()

            # KPI Metrics (Aligned with Abgänge)
            if not forecast_kpis.empty:
                first = forecast_kpis.iloc[0]
                last = forecast_kpis.iloc[-1]
                
                start_hc = int(first["headcount_start"])
                end_hc = int(last["headcount_end"])
                total_entries = int(forecast_kpis["entry_count"].sum())
                
                # Cost Calculation
                # Recalculate cost vector for events
                cost_df = events_df.copy()
                cost_df["FTE_person"] = 1.0 
                cost_df = calculate_cost_vectorized(cost_df, tvoed_lookup=None)
                if "count" in cost_df.columns:
                     cost_df["Cost_Impact"] = cost_df["Total_Cost_Year"] * cost_df["count"]
                else:
                     cost_df["Cost_Impact"] = cost_df["Total_Cost_Year"]
                
                total_added_cost = cost_df["Cost_Impact"].sum()

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Headcount Start", f"{start_hc}")
                col2.metric("Headcount Ende", f"{end_hc}", delta=f"+{total_entries}")
                col3.metric("Gesamt Zugänge", f"{total_entries}")
                col4.metric("Δ Personalkosten (Jahr)", f"{total_added_cost:,.0f} €", help="Summe Jahresgehälter neuer Stellen")
            
            # Tabs (Aligned)
            tab_overview, tab_details, tab_cost = st.tabs(["📊 Überblick", "📋 Details (Tabelle)", "💰 Kosten"])
            
            with tab_overview:
                # 1. Headcount Evolution Chart
                st.markdown("##### 📈 Entwicklung Headcount & MAK")
                
                # Melting for dual line chart? Or two charts? Abgänge uses Facets or Multi-Line.
                # Let's use simple line chart.
                
                fig_evo = px.line(
                    forecast_kpis, 
                    x="date", 
                    y=["headcount_end", "mak_end"],
                    title="Entwicklung Headcount & MAK (inkl. Zugänge)",
                    labels={"value": "Anzahl", "date": "Datum", "variable": "Metrik"},
                    color_discrete_map={
                        "headcount_end": COLORS["accent_blue"],
                        "mak_end": COLORS["accent_green"]
                    }
                )
                st.plotly_chart(fig_evo, use_container_width=True)
                
                # 2. Histogram inputs over time
                st.markdown("##### 📥 Zugänge pro Monat")
                fig_hist = px.histogram(
                        events_df, 
                        x="date", 
                        color="source", 
                        title="Zugänge pro Monat nach Quelle",
                        text_auto=True,
                        color_discrete_map={
                            "Azubi": COLORS["accent_blue"], 
                            "Trainee": COLORS["accent_green"], 
                            "NewHire": COLORS["accent_red"]
                        }
                    )
                st.plotly_chart(fig_hist, use_container_width=True)

            with tab_details:
                st.dataframe(events_df, use_container_width=True)
                
            with tab_cost:
                # Cost Chart from old implementation
                cost_df["Month"] = cost_df["date"].dt.to_period("M").astype(str)
                cost_agg = cost_df.groupby(["Month", "source"])["Cost_Impact"].sum().reset_index()
                     
                fig_cost = px.bar(
                    cost_agg,
                    x="Month",
                    y="Cost_Impact",
                    color="source",
                    title="Zusätzliche Personalkosten (Jahreswert) pro Monat",
                    color_discrete_map={
                        "Azubi": COLORS["accent_blue"], 
                        "Trainee": COLORS["accent_green"], 
                        "NewHire": COLORS["accent_red"]
                    }
                )
                st.plotly_chart(fig_cost, use_container_width=True)
                st.dataframe(cost_df[["date", "source", "TrfGr", "St", "Total_Cost_Year", "Cost_Impact"]], use_container_width=True)

if __name__ == "__main__":
    main()
