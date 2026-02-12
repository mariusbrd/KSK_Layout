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
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as default_abgaenge_params, build_params_from_ui as build_abgaenge_params_from_ui
from zugaenge.params import default_params as default_zugaenge_params, get_strategies
from zugaenge.forecast import run_forecast_zugaenge

def main():
    st.title("📈 Prognose: Zugänge")
    st.write("Prognose von Neueinstellungen (Azubis, Trainees, Externe) und deren Auswirkung auf Headcount/MAK.")

    try:
        # 1. Load Central Data (Consistent with Abgänge/Kompakt)
        snapshot_df_raw, history_df, _, _ = load_and_prepare_data()

        # 2. Render Sidebar Filters
        from components.sidebar import render_global_filters, apply_filters
        render_global_filters(snapshot_df_raw, history_df)
        
        # 3. Apply Filters
        df_ma_filtered = apply_filters(snapshot_df_raw)
        
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
        df_ma_filtered = df_ma_filtered.dropna(subset=["PersNr"])
        
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
        
        # Calculate MAK Vectorized
        from dataloader.loader import calculate_mak_vectorized
        df_ma_filtered = calculate_mak_vectorized(df_ma_filtered, atz_fr_persnr_set)
        
        # Aggregate by employee: sum MAK, keep first occurrence of other attributes
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
            if col in df_ma_filtered.columns:
                agg_dict[col] = "first"
        
        df_employee_agg = df_ma_filtered.groupby("PersNr", as_index=False).agg(agg_dict)
        
        # Backcalculate BsGrd for engine compatibility
        df_employee_agg["Sollarbeitszeit"] = 39.0
        df_employee_agg["BsGrd"] = df_employee_agg["MAK_Calculated"] * 100.0
        df_employee_agg["mak"] = df_employee_agg["MAK_Calculated"]
        df_employee_agg["active"] = True  # Snapshot assumption
        
        snapshot_df = df_employee_agg

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
        abg_kpis = pd.DataFrame()
        vacancies = []
        
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
        
        events_df = res["events"]
        zug_kpis = res["forecast_kpis"]
        
        # ── 3. Ergebnis-Aufbereitung ──
        # Wir zeigen HIER nur die Zugänge (Brutto), keine Verrechnung mit Abgängen.
        # Die Abgänge wurden nur genutzt, um die "Lücken" (OrgUnits) zu finden.
        
        forecast_kpis = zug_kpis
        
        if events_df.empty and (hire_count > 0 or not vacancies):
             if hire_count > 0: st.warning("Keine Zugänge generiert.")

        # --- Feature: Enrich events with OE-Cluster & JF-Cluster ---
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

            # Cleanup
            events_df.drop(columns=["persnr_str"], inplace=True)
            snapshot_df.drop(columns=["PersNr_str"], inplace=True, errors="ignore")
        
        # ── Ergebnisse ──────────────────────────────────────────────────
        st.divider()

        # KPI Metrics Header
        if not forecast_kpis.empty:
            first = forecast_kpis.iloc[0]
            last = forecast_kpis.iloc[-1]
            
            start_hc = int(snapshot_df["active"].sum() if "active" in snapshot_df.columns else len(snapshot_df))
            end_hc = int(last["headcount_end"])
            total_entries = int(events_df["count"].sum()) if not events_df.empty else 0
                
            # Cost Impact
            if not events_df.empty:
                cost_df = events_df.copy()
                cost_df["FTE_person"] = 1.0 
                cost_df = calculate_cost_vectorized(cost_df, tvoed_lookup=None)
                cost_df["Cost_Impact"] = cost_df["Total_Cost_Year"] * cost_df.get("count", 1)
                total_added_cost = cost_df["Cost_Impact"].sum()
            else:
                total_added_cost = 0

            st.markdown("### 🏆 Kennzahlen (Management-Summary)")
            m1, m2, m3 = st.columns(3)
            with m1:
                st.metric("Gesamt Zugänge (Köpfe)", f"{total_entries}")
            with m2:
                st.metric("Δ Personalvolumen (Ende)", f"{last['mak_end'] - first['mak_start']:+.1f} FTE")
            with m3:
                st.metric("Δ Personalkosten (Jahr)", f"{total_added_cost:,.0f} €")
            
            with st.expander("🔍 Details: Bestandsentwicklung (Brutto-Zuwachs)", expanded=False):
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Headcount Start", f"{start_hc}")
                d2.metric("Headcount Ende", f"{end_hc}", delta=f"+{total_entries}")
                d3.metric("MAK Start", f"{first['mak_start']:.1f}")
                d4.metric("MAK Ende", f"{last['mak_end']:.1f}", delta=f"{last['mak_end'] - first['mak_start']:.1f}")
            
            # ── Tabs ───────────────────────────────────────────────────────
            tab_overview, tab_details, tab_cost = st.tabs(["📊 Überblick & Trends", "📋 Personenliste / Details", "💰 Kostenanalyse"])
            
            with tab_overview:
                # Section 1: Entwicklung
                st.markdown("### 📈 Entwicklung Headcount & MAK")
                fig_evo = px.line(
                    forecast_kpis, 
                    x="date", 
                    y=["headcount_end", "mak_end"],
                    labels={"value": "Anzahl", "date": "Datum", "variable": "Metrik"},
                    color_discrete_map={"headcount_end": COLORS["accent_blue"], "mak_end": COLORS["accent_green"]}
                )
                fig_evo.update_layout(title=None)
                st.plotly_chart(fig_evo, use_container_width=True)
                st.caption("Brutto-Entwicklung durch Neueinstellungen (ohne Berücksichtigung von Abgängen).")
                
                st.divider()
                
                # Section 2: Struktur
                st.markdown("### 📥 Zugänge nach Quelle")
                if not events_df.empty:
                    fig_hist = px.histogram(
                        events_df, 
                        x="date", 
                        color="source", 
                        text_auto=True,
                        color_discrete_map={"Azubi": COLORS["accent_blue"], "Trainee": COLORS["accent_green"], "NewHire": COLORS["accent_red"]}
                    )
                    fig_hist.update_layout(title=None)
                    st.plotly_chart(fig_hist, use_container_width=True)
                else:
                    st.info("Keine Zugangs-Events vorhanden.")
                
                st.divider()
                
                # Section 3: Cluster-Struktur (OE)
                st.markdown("### 🧩 Zugänge nach OE-Clustern")
                
                if is_clustering_active():
                    if "OE-Cluster" in events_df.columns:
                        # Get full set of clusters for consistent Y-axis
                        all_clusters = sorted(snapshot_df["OE-Cluster"].unique().tolist())
                        
                        # Chart 1: Kopfzugänge
                        st.markdown("#### 👤 Zugänge nach Personen (OE)")
                        c_stats_h = events_df.groupby("OE-Cluster").size().reindex(all_clusters, fill_value=0).reset_index(name="Zugänge")
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
                        
                        st.divider()

                        # Chart 2: MAK-Zuwachs
                        st.markdown("#### 📊 Zugänge nach Kapazität (MAK) (OE)")
                        if "mak" in events_df.columns:
                            c_stats_m = events_df.groupby("OE-Cluster")["mak"].sum().reindex(all_clusters, fill_value=0.0).reset_index(name="MAK-Zuwachs")
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
                        else:
                            st.info("MAK-Daten für Cluster nicht verfügbar.")
                    else:
                        st.warning("OE-Cluster Spalte nicht im Datensatz gefunden.")

                    st.divider()

                    # Section 4: Cluster-Struktur (JF)
                    st.markdown("### 🧩 Zugänge nach Job-Family-Clustern")
                    if "JF-Cluster" in events_df.columns:
                        all_jf_clusters = sorted(snapshot_df["JF-Cluster"].unique().tolist())
                        
                        # Chart 1: Kopfzugänge JF
                        st.markdown("#### 👤 Zugänge nach Personen (JF)")
                        c_stats_h_jf = events_df.groupby("JF-Cluster").size().reindex(all_jf_clusters, fill_value=0).reset_index(name="Zugänge")
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

                        st.divider()

                        # Chart 2: MAK-Zuwachs JF
                        st.markdown("#### 📊 Zugänge nach Kapazität (MAK) (JF)")
                        if "mak" in events_df.columns:
                            c_stats_m_jf = events_df.groupby("JF-Cluster")["mak"].sum().reindex(all_jf_clusters, fill_value=0.0).reset_index(name="MAK-Zuwachs")
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
