import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date
from typing import Any
from pathlib import Path
import sys
import os

# Add project root or src to path
BASE_PATH = Path(__file__).resolve().parents[1]
SRC_PATH = BASE_PATH / "src"
if SRC_PATH.exists():
    sys.path.append(str(SRC_PATH))
else:
    sys.path.append(str(BASE_PATH))

from dataloader.loader import load_and_prepare_data, calculate_cost_vectorized, load_original_data, load_atz_data_cached
from dataloader.cluster_manager import is_clustering_active
from kpi_reference import get_current_stichtag
from config.settings import COLORS, TARIFF_GROUPS
from abgaenge.forecast import run_forecast_abgaenge, aggregate_forecast_results
from abgaenge.params import default_params as default_abgaenge_params, build_params_from_ui as build_abgaenge_params_from_ui
from zugaenge.params import default_params as default_zugaenge_params, get_strategies
from zugaenge.forecast import run_forecast_zugaenge

def main():
    st.title("📈 Prognose: Zugänge")
    st.write("Prognose von Neueinstellungen (Azubis, Trainees, Externe) und deren Auswirkung auf Headcount/MAK.")

    try:
        # 1. Load Central Data (Consistent with Abgänge/Kompakt)
        snapshot_df_raw, history_df, _, _ = load_and_prepare_data()

        # 2. Render Sidebar Filters (Output only, apply later)
        from components.sidebar import render_global_filters, apply_filters
        render_global_filters(snapshot_df_raw, history_df)
        
        # 3. Global Preprocessing (Calc MAK etc. on FULL Dataset)
        df_ma_global = snapshot_df_raw.copy()
        
        # 4. Load ATZ Details (needed for engine phases)
        global_uploads = st.session_state.get("global_uploads", {})
        up_ma_arg = global_uploads.get("Mitarbeiter")
        up_atz_arg = global_uploads.get("ATZ")
        up_pl_arg = global_uploads.get("Planstellen")
        
        # Reset buffer positions
        if up_ma_arg: up_ma_arg.seek(0)
        if up_atz_arg: up_atz_arg.seek(0)
        if up_pl_arg: up_pl_arg.seek(0)
        
        df_atz = load_atz_data_cached(str(BASE_PATH), up_ma_arg, up_atz_arg, up_pl_arg)
        
        # 5. Preprocessing (Align with Abgänge/Kompakt)
        # Remove Vacancies
        df_ma_global = df_ma_global.dropna(subset=["PersNr"])
        
        # Get ATZ FR employees if available (for MAK calculation)
        atz_fr_persnr_set = set()
        if not df_atz.empty:
            if "PersNr" in df_atz.columns and "Phase" in df_atz.columns:
                stichtag_ts = pd.Timestamp(get_current_stichtag())
                atz_fr = df_atz[
                    (df_atz["Phase"] == "FR") &
                    (df_atz["Beginn"] <= stichtag_ts) &
                    (df_atz["Ende"] >= stichtag_ts)
                ]
                if not atz_fr.empty:
                    atz_fr_persnr_set = set(atz_fr["PersNr"].dropna().astype(str).unique())
        
        # Calculate MAK Vectorized (Global)
        from dataloader.loader import calculate_mak_vectorized
        df_ma_global = calculate_mak_vectorized(df_ma_global, atz_fr_persnr_set)
        
        # Aggregate by employee (Global)
        agg_dict = {
            "MAK_Calculated": "sum",
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Sollarbeitszeit": "sum",
            "Organisationseinheit": "first",
            "Jobfamily": "first",
            "TrfGr": "first",
            "St": "first",
        }
        
        # Include Geschlecht/Planstelle if present
        for col in ["Geschlecht", "Planstelle", "active", "OE-Cluster", "JF-Cluster"]:
            if col in df_ma_global.columns:
                agg_dict[col] = "first"
        
        df_employee_agg_global = df_ma_global.groupby("PersNr", as_index=False).agg(agg_dict)
        
        # Backcalculate BsGrd for engine compatibility
        df_employee_agg_global["Sollarbeitszeit"] = 39.0
        df_employee_agg_global["BsGrd"] = df_employee_agg_global["MAK_Calculated"] * 100.0
        df_employee_agg_global["mak"] = df_employee_agg_global["MAK_Calculated"]
        df_employee_agg_global["active"] = True  # Snapshot assumption
        
        snapshot_df = df_employee_agg_global # Use Global for Forecast logic

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
            
        # Optional: Salary Config
        c4, c5 = st.columns(2)
        az_tarif = c4.selectbox("Übernahme-Tarif", TARIFF_GROUPS, index=TARIFF_GROUPS.index(params["azubi"]["entry_tariff_group"]) if params["azubi"]["entry_tariff_group"] in TARIFF_GROUPS else 5, key="az_tarif")
        az_step = c5.number_input("Übernahme-Stufe", 1, 6, params["azubi"]["entry_step"], key="az_step")
            
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
        
    # Logic: Run calculation OR Load from SessState
    res = None
    vacancies = []
    abg_kpis = pd.DataFrame()

    if submit:
        # Build Params
        run_params = {
            "azubi": {
                "retention_rate": retention,
                "duration_years": duration,
                "strategy": az_strat,
                "target_org_unit": azubi_target,
                "entry_tariff_group": az_tarif,
                "entry_step": az_step,
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
        
        # ── 1. Abgangs-Prognose (Basis für Netto-Betrachtung) ──
        # Wir berechnen IMMER die Abgänge, um den Netto-Headcount zeigen zu können.
        
        with st.spinner("Berechne Basisszenario (Abgänge)..."):
            try:
                abg_res = run_forecast_abgaenge(
                    df_ma=snapshot_df, # Use aggregated DF!
                    df_atz=df_atz,
                    start_date=pd.Timestamp(start_date),
                    end_date=pd.Timestamp(end_date),
                    freq="M",
                    params=st.session_state.get("abgaenge_params", default_abgaenge_params())
                )
                abg_kpis = abg_res["forecast_kpis"]
                
                # Extract Vacancies if needed
                if hire_strat == "Fill Vacancies":
                    events = abg_res["events_person_level"]
                    exits = events[events["headcount_change"] < 0]
                    for _, row in exits.iterrows():
                        vacancies.append({
                            "date": row["event_date"],
                            "org_unit": row.get("Organisationseinheit", "Unbekannt"),
                            "planstelle": row.get("Planstelle", "Unbekannt"),
                            "persnr": row["persnr"]
                        })

            except Exception as e:
                st.error(f"Fehler bei Abgangs-Berechnung: {e}")
                
        # ── 2. Zugangs-Prognose ──
        res = run_forecast_zugaenge(
            df_snapshot=snapshot_df,
            start_date=pd.Timestamp(start_date),
            periods_years=(end_date.year - start_date.year) + 1, # Approx
            params=run_params,
            vacancies=vacancies
        )
        # Store Result
        st.session_state["zugaenge_global_result"] = res
        st.session_state["zugaenge_vacancies"] = vacancies
    
    elif "zugaenge_global_result" in st.session_state:
        res = st.session_state["zugaenge_global_result"]
        vacancies = st.session_state.get("zugaenge_vacancies", [])
    else:
        st.info("⬆️ Parameter einstellen und Prognose berechnen.")
        
    
    if res is not None:
        events_df = res["events"]
        # zog_kpis is not used here, we re-aggregate later.

        # ── 3. Ergebnis-Aufbereitung ──
        
        # --- Feature: Enrich events with OE-Cluster & JF-Cluster (MOVED UP) ---
        # Must happen BEFORE filtering so we can filter by these clusters.
        if not events_df.empty:
            if "org_unit" in events_df.columns and "Organisationseinheit" not in events_df.columns:
                events_df = events_df.rename(columns={"org_unit": "Organisationseinheit"})

            # Pre-prep normalization
            events_df["persnr_str"] = events_df["persnr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            snapshot_df["PersNr_str"] = snapshot_df["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            snapshot_unique = snapshot_df.drop_duplicates(subset=["PersNr_str"])

            # 1. OE-Cluster
            if "OE-Cluster" in snapshot_df.columns:
                oe_cluster_map = snapshot_unique.set_index("PersNr_str")["OE-Cluster"].to_dict()
                events_df["OE-Cluster"] = events_df["persnr_str"].map(oe_cluster_map).fillna("Unclustered")
                
                # FALLBACK: If PersNr mapping failed (New Hires have fake IDs), try map via OrgUnit
                org_cluster_map = snapshot_df.set_index("Organisationseinheit")["OE-Cluster"].to_dict()
                mask_unclustered = events_df["OE-Cluster"] == "Unclustered"
                if mask_unclustered.any() and "Organisationseinheit" in events_df.columns:
                    events_df.loc[mask_unclustered, "OE-Cluster"] = events_df.loc[mask_unclustered, "Organisationseinheit"].map(org_cluster_map).fillna("Unclustered")
            else:
                events_df["OE-Cluster"] = "Unclustered"

            # 2. JF-Cluster
            if "JF-Cluster" in snapshot_df.columns:
                jf_cluster_map = snapshot_unique.set_index("PersNr_str")["JF-Cluster"].to_dict()
                events_df["JF-Cluster"] = events_df["persnr_str"].map(jf_cluster_map).fillna("Unclustered")
                
                # FALLBACK for New Hires: via (OrgUnit, Planstelle) or Planstelle
                mask_unclustered_jf = events_df["JF-Cluster"] == "Unclustered"
                if mask_unclustered_jf.any():
                    from dataloader.cluster_manager import load_cluster_mappings
                    _, jf_map = load_cluster_mappings()
                    
                    if jf_map:
                        first_key = next(iter(jf_map.keys()), None)
                        if isinstance(first_key, tuple):
                             # Combination mapping
                             if "Organisationseinheit" in events_df.columns and "Planstelle" in events_df.columns:
                                 s_org = events_df.loc[mask_unclustered_jf, "Organisationseinheit"].astype(str).str.strip()
                                 s_pos = events_df.loc[mask_unclustered_jf, "Planstelle"].astype(str).str.strip()
                                 keys = list(zip(s_org, s_pos))
                                 events_df.loc[mask_unclustered_jf, "JF-Cluster"] = [jf_map.get(k, "Unclustered") for k in keys]
                        elif "Planstelle" in events_df.columns:
                             events_df.loc[mask_unclustered_jf, "JF-Cluster"] = events_df.loc[mask_unclustered_jf, "Planstelle"].map(jf_map).fillna("Unclustered")
            else:
                events_df["JF-Cluster"] = "Unclustered"

            events_df.drop(columns=["persnr_str"], inplace=True)
            snapshot_df.drop(columns=["PersNr_str"], inplace=True, errors="ignore")
        
        # Ensure cluster columns exist even if empty
        if "OE-Cluster" not in events_df.columns:
            events_df["OE-Cluster"] = pd.Series(dtype="object")
        if "JF-Cluster" not in events_df.columns:
            events_df["JF-Cluster"] = pd.Series(dtype="object")


        # P01: Apply Sidebar Filters to Snapshot (to get PersNr list for non-attribute filters)
        df_filtered_rows = apply_filters(snapshot_df)
        
        if df_filtered_rows.empty:
            st.warning("⚠️ Keine Daten nach Filterung verfügbar.")
            events_df = pd.DataFrame() # Clear events
        else:
            # P02: Filter Events
            events_view = events_df.copy()
            
            # --- Attribute Filtering (OrgUnit, JF, Clusters) ---
            # These attributes exist on generated events, so we can filter directly.
            
            # Org Unit
            selected_orgs = st.session_state.get("selected_org_units", [])
            if selected_orgs:
                if "Organisationseinheit" in events_view.columns:
                    events_view = events_view[events_view["Organisationseinheit"].isin(selected_orgs)]
            
            # Job Family
            selected_jf = st.session_state.get("selected_jobfamilies", [])
            if selected_jf:
                if "Jobfamily" in events_view.columns:
                    events_view = events_view[events_view["Jobfamily"].isin(selected_jf)]

            # OE-Cluster (NEW)
            selected_oe_c = st.session_state.get("selected_oe_cluster", [])
            if selected_oe_c:
                if "OE-Cluster" in events_view.columns:
                    events_view = events_view[events_view["OE-Cluster"].isin(selected_oe_c)]
                    
            # JF-Cluster (NEW)
            selected_jf_c = st.session_state.get("selected_jf_cluster", [])
            if selected_jf_c:
                if "JF-Cluster" in events_view.columns:
                    events_view = events_view[events_view["JF-Cluster"].isin(selected_jf_c)]
                    
            # --- Demographic Filtering (Gender, etc.) ---
            # Fallback to Snapshot Matching for attributes NOT in events
            
            has_demographic_filters = any([
                st.session_state.get("selected_genders", []),
                st.session_state.get("selected_employment", []),
                st.session_state.get("selected_atz_status", []),
                st.session_state.get("selected_cohorts", []),
                st.session_state.get("selected_education", []),
            ])
            
            if has_demographic_filters:
                # 1. Filter Existing People (Azubis, etc in Snapshot)
                valid_ids = set(df_filtered_rows["PersNr"].astype(str))
                
                # 2. Handle New Hires?
                # Keep if (In Filtered Snapshot) OR (Not In Original Snapshot at all)
                all_existing_ids = set(snapshot_df["PersNr"].astype(str))
                
                mask_is_filtered_existing = events_view["persnr"].isin(valid_ids)
                mask_is_new_hire = ~events_view["persnr"].isin(all_existing_ids)
                
                # Keep either matched existing OR new hires
                events_view = events_view[mask_is_filtered_existing | mask_is_new_hire]
                
            events_df = events_view # Update View

        # P03: Re-Aggregate KPIs for View
        # We need a base DF for aggregation.
        # For Zugänge, "Brutto" view: Base is 0? 
        # No, dashboard shows "Netto" effect usually?
        # Page 4 currently shows:
        # "Gesamt Zugänge" (Count from Events)
        # "Evo Headcount" -> this implies adding events to a baseline.
        # If we want to show ONLY Accretion, base is 0.
        # If we want to show "Future State of Filtered Group", base is Filtered Snapshot.
        
        # Current logic (lines 245+):
        # zug_kpis = res["forecast_kpis"] -> This was Global.
        
        # We must RE-CALCULATE KPIs based on Filtered Events + Filtered Baseline.
        if not df_filtered_rows.empty:
            # P04: Ensure 'event_date' exists (Accession events use 'date', aggregation expects 'event_date')
            if "event_date" not in events_df.columns and "date" in events_df.columns:
                events_df["event_date"] = events_df["date"]
            
            # P05: Ensure 'mak_change' exists (Aggregation expects 'mak_change', Accession provides 'mak')
            if "mak_change" not in events_df.columns and "mak" in events_df.columns:
                events_df["mak_change"] = events_df["mak"]

            # P06: Ensure 'headcount_change' exists (Aggregation expects 'headcount_change', Accession provides 'count')
            if "headcount_change" not in events_df.columns and "count" in events_df.columns:
                events_df["headcount_change"] = events_df["count"]
                
            view_kpis = aggregate_forecast_results(
                df_initial=df_filtered_rows,
                events_df=events_df,
                start_date=pd.Timestamp(start_date),
                end_date=pd.Timestamp(end_date),
                freq="M",
                params=None 
            )
            forecast_kpis = view_kpis
        else:
            forecast_kpis = pd.DataFrame()
        
        if events_df.empty and (hire_count > 0 or not vacancies):
            if hire_count > 0: st.info("Nach Filterung keine Zugänge sichtbar (oder keine generiert).")

        # ── Ergebnisse ──────────────────────────────────────────────────
        st.divider()

        # KPI Metrics Header
        if not forecast_kpis.empty:
            first = forecast_kpis.iloc[0]
            last = forecast_kpis.iloc[-1]
            
            
            start_hc = int(df_filtered_rows["active"].sum() if "active" in df_filtered_rows.columns else len(df_filtered_rows))
            end_hc = int(last["headcount_end"])
            # 3. Calculate Gross Metrics (Entries only, ignore internal exits like Azubi_Exit)
            # "Zugänge" page should show who IS COMING (Gross), even if it's an internal takeover.
            mask_pos = events_df["count"] > 0
            gross_entries = int(events_df.loc[mask_pos, "count"].sum())
            gross_mak_add = events_df.loc[mask_pos, "mak"].sum()
            
            # KPI: Net Change (Headcount Delta) vs Gross Entries ("Zugänge")
            total_net_hc_change = int(events_df["count"].sum()) # For delta metric
            
            # Create a view for Charts (Positive Entries Only)
            events_pos = events_df[mask_pos].copy()

            # Cost Impact (Gross Additions)
            if not events_pos.empty:
                cost_df = events_pos.copy()
                # Use MAK as FTE estimate if available, else 1.0
                if "mak" in cost_df.columns:
                    cost_df["FTE_person"] = cost_df["mak"].fillna(1.0)
                else:
                    cost_df["FTE_person"] = 1.0
                
                cost_df = calculate_cost_vectorized(cost_df, tvoed_lookup=None)
                # Cost is annualized
                cost_df["Cost_Impact"] = cost_df["Total_Cost_Year"] * cost_df.get("count", 1)
                total_added_cost = cost_df["Cost_Impact"].sum()
            else:
                total_added_cost = 0

            st.markdown("### 🏆 Kennzahlen (Management-Summary)")
            m1, m2, m3 = st.columns(3)
            with m1:
                # Show Gross Entries as "Zugänge"
                st.metric("Gesamt Zugänge (Köpfe)", f"{gross_entries}")
            with m2:
                # Show Net Growth as Delta
                st.metric("Δ Personalvolumen (Ende)", f"{last['mak_end'] - first['mak_start']:+.1f} FTE")
            with m3:
                st.metric("Δ Personalkosten (Jahr)", f"{total_added_cost:,.0f} €")
            
            # DEBUG OUTPUT
            if st.session_state.get("debug_active", False):
                with st.expander("🐞 Debug-Daten (Zugänge)", expanded=True):
                    st.write(f"Global Events (Raw): {len(events_df)}")
                    st.write(f"Gross Entries: {gross_entries}")
                    st.write(f"Net Count Sum: {events_df['count'].sum()}")
                    st.dataframe(events_df.head(20))
            
            with st.expander("🔍 Details: Bestandsentwicklung (Brutto-Zuwachs)", expanded=False):
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Headcount Start", f"{start_hc}")
                d2.metric("Headcount Ende", f"{end_hc}", delta=f"{total_net_hc_change:+}")
                d3.metric("MAK Start", f"{first['mak_start']:.1f}")
                d4.metric("MAK Ende", f"{last['mak_end']:.1f}", delta=f"{last['mak_end'] - first['mak_start']:.1f}")
            
            # ── Tabs ───────────────────────────────────────────────────────
            tab_overview, tab_details, tab_cost = st.tabs(["📊 Überblick & Trends", "📋 Personenliste / Details", "💰 Kostenanalyse"])
            
            # Helper for Debug Metrics
            def _render_debug_metric(label, chart_val, global_val, unit=""):
                if not st.session_state.get("debug_active", False):
                    return
                
                diff = chart_val - global_val
                is_ok = abs(diff) < 0.1
                color = "green" if is_ok else "red"
                icon = "✅" if is_ok else "❌"
                
                st.markdown(
                    f"<small>{icon} **Debug ({label})**: Chart={chart_val:,.1f}{unit} | Global={global_val:,.1f}{unit} | Diff=:{color}[{diff:+.2f}]</small>", 
                    unsafe_allow_html=True
                )
            
            with tab_overview:
                # Section 1: Entwicklung (Show Net Evolution)
                st.markdown("### 📈 Entwicklung Headcount & MAK")
                fig_evo = px.line(
                    forecast_kpis, 
                    x="period_end", 
                    y=["headcount_end", "mak_end"],
                    labels={"value": "Anzahl", "period_end": "Datum", "variable": "Metrik"},
                    color_discrete_map={"headcount_end": COLORS["accent_blue"], "mak_end": COLORS["accent_green"]}
                )
                fig_evo.update_layout(title=None)
                st.plotly_chart(fig_evo, use_container_width=True)
                # Debug Timeline (Checks Net State)
                if not forecast_kpis.empty:
                    _render_debug_metric("Timeline End Headcount", float(last["headcount_end"]), float(end_hc), "")
                st.caption("Brutto-Entwicklung durch Neueinstellungen (ohne Berücksichtigung von Abgängen).")
                
                st.divider()
                
                # Section 2: Struktur (Show Gross Entries)
                st.markdown("### 📥 Zugänge nach Quelle")
                if not events_pos.empty:
                    fig_hist = px.histogram(
                        events_pos, 
                        x="date", 
                        color="source", 
                        text_auto=True,
                        color_discrete_map={"Azubi": COLORS["accent_blue"], "Trainee": COLORS["accent_green"], "NewHire": COLORS["accent_red"]}
                    )
                    fig_hist.update_layout(title=None)
                    st.plotly_chart(fig_hist, use_container_width=True)
                    # Debug Source Hist (Check against Gross Entries)
                    _render_debug_metric("Source Chart Sum", len(events_pos), gross_entries, "")
                else:
                    st.info("Keine Zugangs-Events vorhanden.")
                
                st.divider()
                
                # Section 3: Cluster-Struktur (OE)
                st.markdown("### 🧩 Zugänge nach OE-Clustern")
                
                if is_clustering_active():
                    if "OE-Cluster" in events_pos.columns:
                        # Get full set of clusters from FILTERED data for Zoom effect
                        all_clusters = sorted(df_filtered_rows["OE-Cluster"].unique().tolist())
                        # Add Unclustered if present in data
                        if "Unclustered" in events_pos["OE-Cluster"].unique() and "Unclustered" not in all_clusters:
                            all_clusters.append("Unclustered")
                        
                        # Chart 1: Kopfzugänge
                        st.markdown("#### 👤 Zugänge nach Personen (OE)")
                        c_stats_h = events_pos.groupby("OE-Cluster").size().reindex(all_clusters, fill_value=0).reset_index(name="Zugänge")
                        c_stats_h = c_stats_h.sort_values("Zugänge", ascending=True)
                        
                        fig_h = px.bar(
                            c_stats_h,
                            x="Zugänge",
                            y="OE-Cluster",
                            orientation="h",
                            title="Kopfzugänge (Anzahl Personen)",
                            text="Zugänge",
                            color="Zugänge",
                            color_continuous_scale="Blues"
                        )
                        fig_h.update_layout(yaxis_title=None, showlegend=False, height=600)
                        st.plotly_chart(fig_h, use_container_width=True)
                        # Debug OE Headcount
                        _render_debug_metric("OE Cluster Headcount", c_stats_h["Zugänge"].sum(), gross_entries, "")
                        
                        st.divider()

                        # Chart 2: MAK-Zuwachs
                        st.markdown("#### 📊 Zugänge nach Kapazität (MAK) (OE)")
                        if "mak" in events_pos.columns:
                            c_stats_m = events_pos.groupby("OE-Cluster")["mak"].sum().reindex(all_clusters, fill_value=0.0).reset_index(name="MAK-Zuwachs")
                            c_stats_m = c_stats_m.sort_values("MAK-Zuwachs", ascending=True)
                            
                            fig_m = px.bar(
                                c_stats_m,
                                x="MAK-Zuwachs",
                                y="OE-Cluster",
                                orientation="h",
                                title="Kapazitätszuwachs (MAK)",
                                text_auto=".1f",
                                color="MAK-Zuwachs",
                                color_continuous_scale="Blues"
                            )
                            fig_m.update_layout(yaxis_title=None, showlegend=False, height=600)
                            st.plotly_chart(fig_m, use_container_width=True)
                            # Debug OE MAK
                            _render_debug_metric("OE Cluster MAK", c_stats_m["MAK-Zuwachs"].sum(), gross_mak_add, " MAK")
                        else:
                            st.info("MAK-Daten für Cluster nicht verfügbar.")
                    else:
                        st.warning("OE-Cluster Spalte nicht im Datensatz gefunden.")

                    st.divider()

                    # Section 4: Cluster-Struktur (JF)
                    st.markdown("### 🧩 Zugänge nach Job-Family-Clustern")
                    if "JF-Cluster" in events_pos.columns:
                        all_jf_clusters = sorted(df_filtered_rows["JF-Cluster"].unique().tolist())
                        if "Unclustered" in events_pos["JF-Cluster"].unique() and "Unclustered" not in all_jf_clusters:
                             all_jf_clusters.append("Unclustered")
                        
                        # Chart 1: Kopfzugänge JF
                        st.markdown("#### 👤 Zugänge nach Personen (JF)")
                        c_stats_h_jf = events_pos.groupby("JF-Cluster").size().reindex(all_jf_clusters, fill_value=0).reset_index(name="Zugänge")
                        c_stats_h_jf = c_stats_h_jf.sort_values("Zugänge", ascending=True)
                        
                        fig_h_jf = px.bar(
                            c_stats_h_jf,
                            x="Zugänge",
                            y="JF-Cluster",
                            orientation="h",
                            title="Kopfzugänge Job-Family (Anzahl Personen)",
                            text="Zugänge",
                            color="Zugänge",
                            color_continuous_scale="Blues"
                        )
                        fig_h_jf.update_layout(yaxis_title=None, showlegend=False, height=600)
                        st.plotly_chart(fig_h_jf, use_container_width=True)
                        # Debug JF Headcount
                        _render_debug_metric("JF Cluster Headcount", c_stats_h_jf["Zugänge"].sum(), gross_entries, "")

                        st.divider()

                        # Chart 2: MAK-Zuwachs JF
                        st.markdown("#### 📊 Zugänge nach Kapazität (MAK) (JF)")
                        if "mak" in events_pos.columns:
                            c_stats_m_jf = events_pos.groupby("JF-Cluster")["mak"].sum().reindex(all_jf_clusters, fill_value=0.0).reset_index(name="MAK-Zuwachs")
                            c_stats_m_jf = c_stats_m_jf.sort_values("MAK-Zuwachs", ascending=True)
                            
                            fig_m_jf = px.bar(
                                c_stats_m_jf,
                                x="MAK-Zuwachs",
                                y="JF-Cluster",
                                orientation="h",
                                title="Kapazitätszuwachs Job-Family (MAK)",
                                text_auto=".1f",
                                color="MAK-Zuwachs",
                                color_continuous_scale="Blues"
                            )
                            fig_m_jf.update_layout(yaxis_title=None, showlegend=False, height=600)
                            st.plotly_chart(fig_m_jf, use_container_width=True)
                            # Debug JF MAK
                            _render_debug_metric("JF Cluster MAK", c_stats_m_jf["MAK-Zuwachs"].sum(), gross_mak_add, " MAK")
                    else:
                        st.warning("JF-Cluster Spalte nicht im Datensatz gefunden.")
                else:
                    st.info("💡 **Hinweis:** Keine benutzerdefinierten Cluster geladen. Sie können diese in den Einstellungen definieren.")

            with tab_details:
                st.markdown("### 📋 Detaillierte Liste der Zugänge")
                st.dataframe(events_df, use_container_width=True)
                
            with tab_cost:
                st.markdown("### 💰 Kosten-Impact")
                if not events_df.empty:
                    cost_df["Month"] = cost_df["date"].dt.to_period("M").astype(str)
                    cost_agg = cost_df.groupby(["Month", "source"])["Cost_Impact"].sum().reset_index()
                         
                    fig_cost = px.bar(
                        cost_agg, x="Month", y="Cost_Impact", color="source",
                        color_discrete_map={"Azubi": COLORS["accent_blue"], "Trainee": COLORS["accent_green"], "NewHire": COLORS["accent_red"]}
                    )
                    fig_cost.update_layout(title=None, yaxis_title="Kosten-Impact (Jahr) in €")
                    st.plotly_chart(fig_cost, use_container_width=True)
                    
                    st.markdown("#### Detail-Tabelle Kosten")
                    st.dataframe(cost_df[["date", "source", "TrfGr", "St", "Total_Cost_Year", "Cost_Impact"]], use_container_width=True)
                else:
                    st.info("Keine Kostendaten verfügbar (da keine Zugänge).")

if __name__ == "__main__":
    main()
