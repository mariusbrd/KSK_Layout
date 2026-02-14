import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date
from utils.plot_helpers import apply_legend_bottom
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
    
    # 3. Sidebar Actions (Reset)
    if st.sidebar.button("♻️ Ergebnisse zurücksetzen", use_container_width=True):
        st.session_state.pop("zugaenge_global_result", None)
        st.session_state.pop("zugaenge_vacancies", None)
        st.rerun()

    # 4. Settings UI
    with st.expander("⚙️ Prognose-Einstellungen", expanded=True):
        with st.form("forecast_inputs"):
            st.markdown("##### 📅 Zeitraum")
            col_date1, col_date2 = st.columns(2)
            start_date = col_date1.date_input("Startdatum", value=default_start)
            end_date = col_date2.date_input("Enddatum", value=default_end)
            
            st.divider()

            # --- Row 2: Component Toggles (horizontal) ---
            st.markdown("##### 🧩 Aktive Komponenten (Zugänge)")
            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                use_azubis = st.checkbox("Azubis", value=True, help="Azubi-Einstellungen + Übernahmen (MAK-wirksam bei Übernahme)")
            with cc2:
                use_trainees = st.checkbox("Trainees", value=True, help="Trainee-Programm inkl. Einstellungen")
            with cc3:
                use_newhires = st.checkbox("Neueinstellungen", value=True, help="Externe Einstellungen / Nachbesetzung")

            st.divider()
            
            # Azubis
            st.subheader("🎓 1. Azubi-Übernahme & Neueinstellungen")
            c1, c2, c3, c4 = st.columns(4)
            azubi_count = c1.number_input("Neue Azubis pro Jahr", 0, 100, params["azubi"].get("new_cases_per_year", 15), key="az_count")
            retention = c2.slider("Übernahmequote (%)", 0.0, 1.0, float(params["azubi"]["retention_rate"]), 0.05)
            duration = c3.number_input("Ausbildungsdauer (Jahre)", 1.0, 5.0, params["azubi"]["duration_years"], 0.5)
            az_strat = c4.selectbox("Verteilung", ["Random", "OrgUnit"], index=0 if params["azubi"]["strategy"] == "Random" else 1, key="az_strat")
            
            azubi_target = None
            if az_strat == "OrgUnit":
                # Use units from original raw data or aggregated? Aggregated is fine.
                units = sorted(snapshot_df["Organisationseinheit"].dropna().astype(str).unique())
                azubi_target = st.selectbox("Ziel-Einheit (Azubi)", units, key="az_unit")
                
            # Optional: Salary Config
            c5, c6 = st.columns(2)
            az_tarif = c5.selectbox("Übernahme-Tarif", TARIFF_GROUPS, index=TARIFF_GROUPS.index(params["azubi"]["entry_tariff_group"]) if params["azubi"]["entry_tariff_group"] in TARIFF_GROUPS else 5, key="az_tarif")
            az_step = c6.number_input("Übernahme-Stufe", 1, 6, params["azubi"]["entry_step"], key="az_step")
                
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

            # --- New Hire Distribution Matrix ---
            with st.expander("📊 Verteilung Neueinstellungen (Matrix)", expanded=False):
                st.caption("Steuern Sie, in welchen Bereichen neue Stellen (ohne Nachbesetzung) entstehen.")
                
                # 1. Calculate Default Distribution from Snapshot
                # Group by JobFamily and OE-Cluster
                if "Jobfamily" in snapshot_df.columns and "OE-Cluster" in snapshot_df.columns:
                    dist_base = snapshot_df.groupby(["Jobfamily", "OE-Cluster"]).size().reset_index(name="Count")
                    total_n = dist_base["Count"].sum()
                    dist_base["Share %"] = (dist_base["Count"] / total_n).round(4)
                    
                    # Sort by Share desc
                    dist_base = dist_base.sort_values("Share %", ascending=False).reset_index(drop=True)
                    dist_base = dist_base[["Jobfamily", "OE-Cluster", "Share %"]]
                else:
                    # Fallback if columns missing
                    dist_base = pd.DataFrame([
                        {"Jobfamily": "Angestellte", "OE-Cluster": "Unclustered", "Share %": 1.0}
                    ])

                # 2. Render Editor
                edited_dist = st.data_editor(
                    dist_base,
                    column_config={
                        "Share %": st.column_config.NumberColumn(
                            "Anteil (0.0 - 1.0)",
                            min_value=0.0,
                            max_value=1.0,
                            step=0.01,
                            format="%.2f"
                        )
                    },
                    use_container_width=True,
                    num_rows="dynamic",
                    key="hire_dist_matrix"
                )
                
                # 3. Normalize check (visual feedback)
                total_share = edited_dist["Share %"].sum()
                if not (0.99 <= total_share <= 1.01):
                    st.warning(f"⚠️ Summe der Anteile ist {total_share:.2%} (sollte 100% sein). Werte werden bei der Simulation normalisiert.")
                
                # Convert to list of dicts for backend
                hire_distribution = edited_dist.to_dict("records")
            

            submit = st.form_submit_button("🚀 Prognose berechnen", use_container_width=True)
        
    # Logic: Run calculation OR Load from SessState
    res = None
    vacancies = []
    abg_kpis = pd.DataFrame()

    if submit:
        # Build Params
        run_params = {
            "azubi": {
                "active": use_azubis,
                "new_cases_per_year": azubi_count,
                "retention_rate": retention,
                "duration_years": duration,
                "strategy": az_strat,
                "target_org_unit": azubi_target,
                "entry_tariff_group": az_tarif,
                "entry_step": az_step,
            },
            "trainee": {
                "active": use_trainees,
                "new_cases_per_year": trainee_count,
                "duration_years": trainee_dur,
                "salary_group": trainee_sal,
                "strategy": tr_strat,
                "target_org_unit": trainee_target
            },
            "new_hires": {
                "active": use_newhires,
                "count_per_year": hire_count,
                "strategy": hire_strat,
                "target_org_unit": hire_target,
                "distribution": hire_distribution # Pass the matrix
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
                    
                    # Prepare lookup for Leaver Attributes
                    # Note: events usually only have Date, Type, Count. Attributes need to be looked up.
                    # We use snapshot_df for lookup of initial attributes.
                    # Caveat: If person changed attributes during simulation (not supported yet), snapshot is still best proxy.
                    
                    # Ensure snapshot has string index for lookup
                    snap_lookup = snapshot_df.copy()
                    snap_lookup["pid_str"] = snap_lookup["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
                    snap_lookup = snap_lookup.set_index("pid_str")
                    
                    for _, row in exits.iterrows():
                        pid = str(row["persnr"]).strip().replace(".0", "")
                        
                        # Lookup attributes
                        leaver_jf = "Angestellte"
                        leaver_oe_c = "Unclustered"
                        
                        if pid in snap_lookup.index:
                            leaver_data = snap_lookup.loc[pid]
                            if isinstance(leaver_data, pd.DataFrame): leaver_data = leaver_data.iloc[0] # handle dupes
                            
                            leaver_jf = leaver_data.get("Jobfamily", "Angestellte")
                            leaver_oe_c = leaver_data.get("OE-Cluster", "Unclustered")
                            
                        vacancies.append({
                            "date": row["event_date"],
                            "org_unit": row.get("Organisationseinheit", "Unbekannt"),
                            "planstelle": row.get("Planstelle", "Unbekannt"),
                            "persnr": row["persnr"], # Leaver ID (for debug)
                            "Jobfamily": leaver_jf,     # <--- ENRICHED
                            "OE-Cluster": leaver_oe_c   # <--- ENRICHED
                        })

            except Exception as e:
                st.error(f"Fehler bei Abgangs-Berechnung: {e}")
                
        # ── 2. Zugangs-Prognose ──
        res = run_forecast_zugaenge(
            df_snapshot=snapshot_df,
            start_date=pd.Timestamp(start_date),
            end_date=pd.Timestamp(end_date),
            freq="M",
            params={**run_params, "random_seed": 42},
            vacancies=vacancies
        )
        
        # ── 3. Ergebnis-Aufbereitung (Enrichment) ──
        events_df = res["events"]
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
                mapped_oe = events_df["persnr_str"].map(oe_cluster_map)
                
                if "OE-Cluster" in events_df.columns:
                    events_df["OE-Cluster"] = events_df["OE-Cluster"].fillna(mapped_oe).fillna("Unclustered")
                else:
                    events_df["OE-Cluster"] = mapped_oe.fillna("Unclustered")
                
                org_cluster_map = snapshot_df.set_index("Organisationseinheit")["OE-Cluster"].to_dict()
                mask_unclustered = (events_df["OE-Cluster"] == "Unclustered") | (events_df["OE-Cluster"].isna())
                
                if mask_unclustered.any() and "Organisationseinheit" in events_df.columns:
                    fill_vals = events_df.loc[mask_unclustered, "Organisationseinheit"].map(org_cluster_map)
                    events_df.loc[mask_unclustered, "OE-Cluster"] = fill_vals.fillna("Unclustered")
            else:
                if "OE-Cluster" not in events_df.columns:
                    events_df["OE-Cluster"] = "Unclustered"

            # 2. JF-Cluster
            if "JF-Cluster" in snapshot_df.columns:
                jf_cluster_map = snapshot_unique.set_index("PersNr_str")["JF-Cluster"].to_dict()
                mapped_jf = events_df["persnr_str"].map(jf_cluster_map)
                
                if "JF-Cluster" in events_df.columns:
                    events_df["JF-Cluster"] = events_df["JF-Cluster"].fillna(mapped_jf).fillna("Unclustered")
                else:
                    events_df["JF-Cluster"] = mapped_jf.fillna("Unclustered")
                
                mask_unclustered_jf = (events_df["JF-Cluster"] == "Unclustered") | (events_df["JF-Cluster"].isna())
                if mask_unclustered_jf.any():
                    from dataloader.cluster_manager import load_cluster_mappings
                    _, jf_map = load_cluster_mappings()
                    
                    if jf_map:
                        first_key = next(iter(jf_map.keys()), None)
                        if isinstance(first_key, tuple):
                             if "Organisationseinheit" in events_df.columns and "Planstelle" in events_df.columns:
                                 s_org = events_df.loc[mask_unclustered_jf, "Organisationseinheit"].astype(str).str.strip()
                                 s_pos = events_df.loc[mask_unclustered_jf, "Planstelle"].astype(str).str.strip()
                                 keys = list(zip(s_org, s_pos))
                                 events_df.loc[mask_unclustered_jf, "JF-Cluster"] = [jf_map.get(k, "Unclustered") for k in keys]
                        elif "Planstelle" in events_df.columns:
                             events_df.loc[mask_unclustered_jf, "JF-Cluster"] = events_df.loc[mask_unclustered_jf, "Planstelle"].map(jf_map).fillna("Unclustered")
            
            if "JF-Cluster" in events_df.columns and "Jobfamily" in events_df.columns:
                mask_still_unclustered = (events_df["JF-Cluster"] == "Unclustered") | (events_df["JF-Cluster"].isna())
                if mask_still_unclustered.any():
                    snap_jf_map = snapshot_df.dropna(subset=["Jobfamily", "JF-Cluster"]).drop_duplicates("Jobfamily").set_index("Jobfamily")["JF-Cluster"].to_dict()
                    fill_vals_jf = events_df.loc[mask_still_unclustered, "Jobfamily"].map(snap_jf_map)
                    events_df.loc[mask_still_unclustered, "JF-Cluster"] = fill_vals_jf.fillna("Unclustered")
            else:
                if "JF-Cluster" not in events_df.columns:
                    events_df["JF-Cluster"] = "Unclustered"

            events_df.drop(columns=["persnr_str"], inplace=True)
            snapshot_df.drop(columns=["PersNr_str"], inplace=True, errors="ignore")
            
            # Save enriched events back to result
            res["events"] = events_df

        # Store Result
        st.session_state["zugaenge_global_result"] = res
        st.session_state["zugaenge_vacancies"] = vacancies
        st.session_state["zugaenge_start_date"] = start_date
        st.session_state["zugaenge_end_date"] = end_date
        st.session_state["zugaenge_use_azubis"] = use_azubis
        st.session_state["zugaenge_use_trainees"] = use_trainees
        st.session_state["zugaenge_use_newhires"] = use_newhires
    
    elif "zugaenge_global_result" in st.session_state:
        res = st.session_state["zugaenge_global_result"]
        vacancies = st.session_state.get("zugaenge_vacancies", [])
        start_date = st.session_state.get("zugaenge_start_date", start_date)
        end_date = st.session_state.get("zugaenge_end_date", end_date)
        use_azubis = st.session_state.get("zugaenge_use_azubis", use_azubis)
        use_trainees = st.session_state.get("zugaenge_use_trainees", use_trainees)
        use_newhires = st.session_state.get("zugaenge_use_newhires", use_newhires)
    else:
        st.info("⬆️ Parameter einstellen und Prognose berechnen.")
        
    
    if res is not None:
        events_df = res["events"]
        # zog_kpis is not used here, we re-aggregate later.

        # ── 3. Ergebnis-Aufbereitung ──
        
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
            
            # --- Attribute Filtering (OrgUnit, JF, Clusters) ---
            # These attributes exist on generated events, so we can filter directly.
            
            # Helper for robust filtering (case-insensitive, strip, handles 123 vs 123.0)
            def _apply_robust_filter(df, column, selected):
                if not selected or column not in df.columns:
                    return df
                # Normalize data strings
                s_norm = df[column].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
                # Normalize filter strings
                vals_norm = [str(v).strip().replace(".0", "") for v in selected]
                return df[s_norm.isin(vals_norm)]

            # Org Unit
            events_view = _apply_robust_filter(events_view, "Organisationseinheit", st.session_state.get("selected_org_units", []))
            
            # Job Family
            events_view = _apply_robust_filter(events_view, "Jobfamily", st.session_state.get("selected_jobfamilies", []))

            # OE-Cluster (Sync with sidebar plural key)
            events_view = _apply_robust_filter(events_view, "OE-Cluster", st.session_state.get("selected_oe_clusters", []))
                    
            # JF-Cluster (Sync with sidebar plural key)
            events_view = _apply_robust_filter(events_view, "JF-Cluster", st.session_state.get("selected_jf_clusters", []))
                    
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
            
            # Dynamic Info Text based on active components
            info_parts = []
            if use_azubis:
                if use_trainees or use_newhires:
                    info_parts.append("Trainee- und externe Einstellungen sind sofort MAK-wirksam.")
                    info_parts.append("Auszubildende erhöhen zunächst nur Köpfe; MAK wird erst bei der Übernahme wirksam (zeitverzögert).")
                else:
                    info_parts.append("Auszubildende erhöhen zunächst nur den Personalbestand (Köpfe).")
                    info_parts.append("MAK/FTE werden erst bei der Übernahme nach Ausbildungsende wirksam (zeitverzögert).")
            elif use_trainees or use_newhires:
                info_parts.append("Trainee- und externe Einstellungen sind i.d.R. sofort MAK/FTE-wirksam und erhöhen Köpfe und MAK gleichzeitig.")
            
            if info_parts:
                st.info(f"💡 **Hinweis:** {' '.join(info_parts)}")
            
            # DEBUG OUTPUT
            if st.session_state.get("debug_active", False):
                with st.expander("🐞 Debug-Daten (Zugänge)", expanded=True):
                    show_technical_debug = st.toggle(
                        "🔧 Technische Audit-Kennzahlen anzeigen",
                        value=False,
                        help="Zeigt interne Berechnungs- und Lifecycle-Events zur Validierung der Prognoselogik."
                    )
                    
                    if show_technical_debug:
                        st.markdown("**Technische Audit-Kennzahlen (nicht managementrelevant)**")
                        st.caption(
                            "Diese Werte enthalten zusätzlich interne Lifecycle- und Hilfs-Events "
                            "zur Validierung der Prognoselogik und entsprechen nicht den tatsächlichen Personalzugängen."
                        )
                        st.write(f"Global Events (Raw): {len(events_df)}")
                        st.write(f"Net Count Sum: {events_df['count'].sum()}")
                        st.divider()

                    st.write(f"Gross Entries: {gross_entries}")
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
                    labels={
                        "value": "Anzahl", 
                        "period_end": "Datum", 
                        "variable": "Metrik"
                    },
                    color_discrete_map={"headcount_end": COLORS["accent_blue"], "mak_end": COLORS["accent_green"]}
                )
                # Explicitly Rename Traces (Fixes "undefiniert")
                fig_evo.update_traces(name="Köpfe (Headcount)", selector=dict(name="headcount_end"))
                fig_evo.update_traces(name="MAK (FTE)", selector=dict(name="mak_end"))
                
                def build_entries_caption(use_azubis, use_trainees, use_newhires):
                    base = "Brutto-Entwicklung durch Zugänge (ohne Berücksichtigung von Abgängen)."
                    if use_azubis:
                        if use_trainees or use_newhires:
                            ext = "(Mix: Trainees/Neueinstellungen sofort MAK-wirksam; Azubis zeitverzögert)"
                        else:
                            ext = "(Azubis: MAK-Wirkung erst bei Übernahme)"
                    else:
                        ext = "(Zugänge sind sofort MAK-wirksam)"
                    return f"{base} {ext}"

                fig_evo.update_layout(title=None, hovermode="x unified")
                fig_evo = apply_legend_bottom(fig_evo)
                st.plotly_chart(fig_evo, use_container_width=True)
                # Debug Timeline (Checks Net State)
                if not forecast_kpis.empty:
                    _render_debug_metric("Timeline End Headcount", float(last["headcount_end"]), float(end_hc), "")
                
                st.caption(build_entries_caption(use_azubis, use_trainees, use_newhires))
                
                st.divider()
                
                # Section 2: Struktur (Show Gross Entries)
                st.markdown("### 📥 Zugänge nach Quelle")
                
                # 1. Definiere explizite Teilmengen (Strikte Trennung)
                # A) Alle Azubi-Events (inkl. Statuswechsel Out)
                events_all_azubi = events_pos[events_pos["type"].astype(str).str.contains("Azubi")]
                
                # B) Echte Zugänge (Inflows): headcount_change > 0 -> Hire, Conversion_In, New_Hire, Trainee_Hire
                valid_types = ["Azubi_Hire", "Azubi_Conversion_In", "New_Hire", "Trainee_Hire"]
                if not events_pos.empty:
                    events_inflows = events_pos[events_pos["type"].isin(valid_types)].copy()
                else:
                    events_inflows = pd.DataFrame()

                # C) Teilmengen für Debug
                # Externe Azubi-Einstellungen
                events_external_azubi = events_inflows[events_inflows["type"] == "Azubi_Hire"]
                # Interne Übernahmen
                events_internal = events_inflows[events_inflows["type"] == "Azubi_Conversion_In"]
                # Neueinstellungen extern
                events_external_hire = events_inflows[events_inflows["type"] == "New_Hire"]
                # Trainees
                events_trainee = events_inflows[events_inflows["type"] == "Trainee_Hire"]
                
                # Statuswechsel (Out) Disclaimer
                events_status_change_out = events_all_azubi[events_all_azubi["type"] == "Azubi_Conversion_Out"]

                if not events_inflows.empty:
                    # 2. Deutschsprachige Labels für Chart und Legende
                    label_map = {
                        "Azubi_Hire": "Neue Auszubildende",
                        "Azubi_Conversion_In": "Übernahme aus Ausbildung", 
                        "New_Hire": "Neueinstellung",
                        "Trainee_Hire": "Trainee"
                    }
                    events_inflows["Quelle"] = events_inflows["type"].map(label_map)
                    
                    # 3. Chart mit stikt gefilterten Daten (Inflows only)
                    fig_hist = px.histogram(
                        events_inflows, 
                        x="date", 
                        color="Quelle", 
                        text_auto=True,
                        labels={"date": "Datum", "count": "Anzahl (Zugänge)", "Quelle": "Quelle"},
                        # Use specific colors for clarity
                        color_discrete_map={
                            "Neue Auszubildende": COLORS.get("accent_blue", "#1f77b4"),
                            "Übernahme aus Ausbildung": "#9467bd", # Purple (Transformation)
                            "Neueinstellung": COLORS.get("accent_green", "#2ca02c"),
                            "Trainee": COLORS.get("accent_orange", "#ff7f0e")
                        }
                    )
                    def build_source_caption(use_azubis, use_trainees, use_newhires):
                        if use_azubis:
                            if use_trainees or use_newhires:
                                return "Übernahmen erhöhen den MAK (interne Stellenbesetzung). Auszubildende zählen während der Ausbildung nur als Köpfe; Trainees und externe Neueinstellungen sind i.d.R. sofort MAK-wirksam."
                            else:
                                return "Übernahmen (Azubi-Übernahme) sind interne Stellenbesetzungen und erhöhen den MAK. Neue Auszubildende zählen während der Ausbildung nur als Köpfe; MAK wird erst bei Übernahme wirksam."
                        else:
                            if use_trainees and use_newhires:
                                return "Trainee- und externe Neueinstellungen sind i.d.R. sofort MAK-wirksam und erhöhen Köpfe und MAK gleichzeitig."
                            elif use_trainees:
                                return "Trainee-Einstellungen sind i.d.R. sofort MAK-wirksam und erhöhen Köpfe und MAK gleichzeitig."
                            elif use_newhires:
                                return "Externe Neueinstellungen sind i.d.R. sofort MAK-wirksam und erhöhen Köpfe und MAK gleichzeitig."
                            return ""

                    fig_hist.update_layout(title=None, xaxis_title="Datum", yaxis_title="Anzahl (Zugänge)")
                    fig_hist = apply_legend_bottom(fig_hist)
                    st.plotly_chart(fig_hist, use_container_width=True)
                    
                    # 4. Erklärung
                    st.caption(build_source_caption(use_azubis, use_trainees, use_newhires))
                    
                    # Debug Source Hist (Check against Gross Entries)
                    if st.session_state.get("debug_active", False):
                        st.markdown("#### 🐞 Debug-Analyse (Zugänge)")
                        
                        # Treiber-Status Tabelle
                        driver_data = [
                            {
                                "Treiber": "Azubis – Neueinstellungen", 
                                "Aktiv (UI)": use_azubis, 
                                "Events (technisch)": len(events_external_azubi),
                                "Inflow-Events (gezählt)": len(events_external_azubi)
                            },
                            {
                                "Treiber": "Azubis – Übernahmen", 
                                "Aktiv (UI)": use_azubis, 
                                "Events (technisch)": len(events_internal) + len(events_status_change_out),
                                "Inflow-Events (gezählt)": len(events_internal)
                            },
                            {
                                "Treiber": "Trainees", 
                                "Aktiv (UI)": use_trainees, 
                                "Events (technisch)": len(events_trainee),
                                "Inflow-Events (gezählt)": len(events_trainee)
                            },
                            {
                                "Treiber": "Neueinstellungen", 
                                "Aktiv (UI)": use_newhires, 
                                "Events (technisch)": len(events_external_hire),
                                "Inflow-Events (gezählt)": len(events_external_hire)
                            },
                        ]
                        st.table(pd.DataFrame(driver_data))
                        
                        # Unique Counts
                        unique_inflows = events_inflows["persnr"].nunique()
                        unique_ext_azubi = events_external_azubi["persnr"].nunique()
                        unique_int_conv = events_internal["persnr"].nunique()
                        unique_ext_hire = events_external_hire["persnr"].nunique()
                        unique_trainee = events_trainee["persnr"].nunique()
                        
                        col_d1, col_d2 = st.columns(2)
                        with col_d1:
                            st.markdown("**Event-Anzahl (Zeilen)**")
                            st.write(f"Zugänge gesamt (Inflows): `{len(events_inflows)}`")
                            st.write(f"- Neue Auszubildende: `{len(events_external_azubi)}`")
                            st.write(f"- Übernahmen (intern): `{len(events_internal)}`")
                            st.write(f"- Neueinstellungen (extern): `{len(events_external_hire)}`")
                            st.write(f"- Trainee-Einstellungen: `{len(events_trainee)}`")
                            
                            st.divider()
                            st.caption("*(Hinweis: Die Zugänge-Seite zählt nur Inflows. Ein 'Conversion_Out' kann als technischer Statuswechsel zur Paarbildung auftreten, wird aber nicht als Abgang bewertet.)*")
                        
                        with col_d2:
                            st.markdown("**Unique Personen (Köpfe)**")
                            st.write(f"Unique Personen (Inflows): `{unique_inflows}`")
                            st.write(f"- Unique Auszubildende: `{unique_ext_azubi}`")
                            st.write(f"- Unique Übernahmen: `{unique_int_conv}`")
                            st.write(f"- Unique Neueinstellungen: `{unique_ext_hire}`")
                            st.write(f"- Unique Trainees: `{unique_trainee}`")
                            
                            # Summencheck
                            sum_events = len(events_external_azubi) + len(events_internal) + len(events_external_hire) + len(events_trainee)
                            if sum_events == len(events_inflows):
                                st.success(f"✅ Summencheck: {sum_events} = {len(events_inflows)}")
                            else:
                                st.error(f"❌ Summencheck: {sum_events} != {len(events_inflows)}")

                        _render_debug_metric("Zugänge (Events): Chart vs Inflows", len(events_inflows), len(events_inflows), "")
                else:
                    st.info("Keine Zugangs-Events vorhanden (nach Filterung).")
                
                st.divider()
                
                # Section 3: Cluster-Struktur (OE)
                st.markdown("### 🧩 Zugänge nach OE-Clustern")
                
                if is_clustering_active():
                    if "OE-Cluster" in events_inflows.columns:
                        # Get full set of clusters for Zoom effect AND New Hires (Union of Snapshot + Events)
                        # Fix: Ensure categories present in Inflows (e.g. Matrix assigned) are shown even if not in Snapshot
                        snap_clusters = df_filtered_rows["OE-Cluster"].unique().tolist() if "OE-Cluster" in df_filtered_rows.columns else []
                        evt_clusters = events_inflows["OE-Cluster"].unique().tolist()
                        all_clusters = sorted(list(set(snap_clusters + evt_clusters)))
                        
                        # Remove NaNs or empty strings if any
                        all_clusters = [c for c in all_clusters if pd.notna(c) and str(c).strip() != ""]
                        
                        # Chart 1: Kopfzugänge
                        st.markdown("#### 👤 Zugänge nach Personen (OE)")
                        c_stats_h = events_inflows.groupby("OE-Cluster").size().reindex(all_clusters, fill_value=0).reset_index(name="Zugänge")
                        c_stats_h = c_stats_h.sort_values("Zugänge", ascending=True)
                        
                        fig_h = px.bar(
                            c_stats_h,
                            x="Zugänge",
                            y="OE-Cluster",
                            orientation="h",
                            title=None,
                            labels={"OE-Cluster": "Bereich (OE)", "Zugänge": "Anzahl Personen"},
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
                        if "mak" in events_inflows.columns:
                            c_stats_m = events_inflows.groupby("OE-Cluster")["mak"].sum().reindex(all_clusters, fill_value=0.0).reset_index(name="MAK-Zuwachs")
                            c_stats_m = c_stats_m.sort_values("MAK-Zuwachs", ascending=True)
                            
                            fig_m = px.bar(
                                c_stats_m,
                                x="MAK-Zuwachs",
                                y="OE-Cluster",
                                orientation="h",
                                title=None,
                                labels={"OE-Cluster": "Bereich (OE)", "MAK-Zuwachs": "MAK-Zuwachs (FTE)"},
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
                    if "JF-Cluster" in events_inflows.columns:
                        snap_jf = df_filtered_rows["JF-Cluster"].unique().tolist() if "JF-Cluster" in df_filtered_rows.columns else []
                        evt_jf = events_inflows["JF-Cluster"].unique().tolist()
                        all_jf_clusters = sorted(list(set(snap_jf + evt_jf)))
                        
                        all_jf_clusters = [c for c in all_jf_clusters if pd.notna(c) and str(c).strip() != ""]

                        # Chart 1: Kopfzugänge JF
                        st.markdown("#### 👤 Zugänge nach Personen (JF)")
                        c_stats_h_jf = events_inflows.groupby("JF-Cluster").size().reindex(all_jf_clusters, fill_value=0).reset_index(name="Zugänge")
                        c_stats_h_jf = c_stats_h_jf.sort_values("Zugänge", ascending=True)
                        
                        fig_h_jf = px.bar(
                            c_stats_h_jf,
                            x="Zugänge",
                            y="JF-Cluster",
                            orientation="h",
                            title=None,
                            labels={"JF-Cluster": "Berufsgruppe (JF)", "Zugänge": "Anzahl Personen"},
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
                        if "mak" in events_inflows.columns:
                            c_stats_m_jf = events_inflows.groupby("JF-Cluster")["mak"].sum().reindex(all_jf_clusters, fill_value=0.0).reset_index(name="MAK-Zuwachs")
                            c_stats_m_jf = c_stats_m_jf.sort_values("MAK-Zuwachs", ascending=True)
                            
                            fig_m_jf = px.bar(
                                c_stats_m_jf,
                                x="MAK-Zuwachs",
                                y="JF-Cluster",
                                orientation="h",
                                title=None,
                                labels={"JF-Cluster": "Berufsgruppe (JF)", "MAK-Zuwachs": "MAK-Zuwachs (FTE)"},
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
                    
                    # Translation Map for Source
                    label_map_src = {
                        "Azubi": "Auszubildende",
                        "Trainee": "Trainee",
                        "NewHire": "Externe Neueinstellung"
                    }
                    cost_df["Quelle"] = cost_df["source"].map(label_map_src).fillna("Unbekannt")
                    
                    cost_agg = cost_df.groupby(["Month", "Quelle"])["Cost_Impact"].sum().reset_index()
                         
                    fig_cost = px.bar(
                        cost_agg, x="Month", y="Cost_Impact", color="Quelle",
                        color_discrete_map={
                            "Auszubildende": COLORS["accent_blue"], 
                            "Trainee": COLORS["accent_green"], 
                            "Externe Neueinstellung": COLORS["accent_red"]
                        },
                        labels={"Month": "Monat", "Cost_Impact": "Kosten-Impact (Jahr) in €", "Quelle": "Quelle"}
                    )
                    fig_cost.update_layout(title=None)
                    fig_cost = apply_legend_bottom(fig_cost)
                    st.plotly_chart(fig_cost, use_container_width=True)
                    
                    st.markdown("#### Detail-Tabelle Kosten")
                    st.dataframe(cost_df[["date", "source", "TrfGr", "St", "Total_Cost_Year", "Cost_Impact"]], use_container_width=True)
                else:
                    st.info("Keine Kostendaten verfügbar (da keine Zugänge).")

if __name__ == "__main__":
    main()
