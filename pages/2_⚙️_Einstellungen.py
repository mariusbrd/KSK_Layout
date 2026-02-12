"""
Modul 8: Einstellungen

Konfigurationsseite für Loader-spezifische Parameter wie
Sonderfall-Gehälter (Azubis, Vorstand) und Arbeitgeber-Kostenfaktor.
"""

import io
import streamlit as st
import pandas as pd
import sys
import os

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    COLORS, EMPLOYER_COST_FACTOR,
    DEFAULT_AZUBI_JAHRESGEHALT, DEFAULT_VORSTAND_JAHRESGEHALT,
    format_currency,
)
from kpi_reference import STICHTAG_DEFAULT
from dataloader.loader import load_and_prepare_data
from dataloader.jobfamily_matcher import load_jobfamily_definitions
from dataloader.cluster_manager import generate_template_bytes, validate_and_save_clusters, load_cluster_mappings


def render_settings_page():
    st.header("Einstellungen")
    st.caption("Loader-spezifische Parameter für Kostenberechnung")

    st.divider()

    # --- Datenmanagement ---
    st.subheader("Datenmanagement")
    st.caption("Eigene Excel-Dateien hochladen (überschreibt Original-Daten für die Sitzung).")
    
    with st.expander("📁 Dateien hochladen"):
        if "global_uploads" not in st.session_state:
            st.session_state["global_uploads"] = {}
            
        col_up1, col_up2 = st.columns(2)
        with col_up1:
            up_ma = st.file_uploader("Mitarbeiter.xlsx", type=["xlsx"], key="set_up_ma")
            if up_ma:
                # Store as BytesIO for persistence
                st.session_state["global_uploads"]["Mitarbeiter"] = io.BytesIO(up_ma.getvalue())
            elif "Mitarbeiter" in st.session_state["global_uploads"] and not up_ma:
                # If widget cleared, keep old? Or clear? 
                # Standard behavior: clear. But we want persistence across pages.
                # If user clears widget manually, we remove.
                # But looking at widget key: if page reloads, key is gone?
                pass

        with col_up2:
            up_pl = st.file_uploader("Planstellen.xlsx", type=["xlsx"], key="set_up_pl")
            if up_pl:
                st.session_state["global_uploads"]["Planstellen"] = io.BytesIO(up_pl.getvalue())

        col_up3, col_up4 = st.columns(2)
        with col_up3:
            up_atz = st.file_uploader("ATZ.xlsx", type=["xlsx"], key="set_up_atz")
            if up_atz:
                st.session_state["global_uploads"]["ATZ"] = io.BytesIO(up_atz.getvalue())

        with col_up4:
            up_edu = st.file_uploader("Ausbildung.xlsx", type=["xlsx"], key="set_up_edu")
            if up_edu:
                st.session_state["global_uploads"]["Ausbildung"] = io.BytesIO(up_edu.getvalue())

        col_up5, col_up6 = st.columns(2)
        with col_up5:
            up_tvoed = st.file_uploader("TVÖD.xlsx (optional)", type=["xlsx"], key="set_up_tvoed")
            if up_tvoed:
                st.session_state["global_uploads"]["TVÖD"] = io.BytesIO(up_tvoed.getvalue())

        if st.session_state["global_uploads"]:
            st.success(f"✅ {len(st.session_state['global_uploads'])} Dateien aktiv.")
            if st.button("Alle Uploads löschen"):
                 st.session_state["global_uploads"] = {}
                 st.rerun()

    st.divider()

    # --- Cluster-Management ---
    st.subheader("Cluster-Management")
    st.caption("Definition von benutzerdefinierten Gruppen für OE und Job-Families.")
    
    with st.expander("🧩 Custom Clusters (Excel-Mapping)"):
        c_col1, c_col2 = st.columns(2)
        
        with c_col1:
            st.markdown("**1. Template erstellen**")
            st.caption("Lädt alle aktuellen OEs und JFs in eine Excel-Datei.")
            if st.button("📥 Template generieren"):
                # Load current data to get unique names/keys
                with st.spinner("Lade aktuelle Stammdaten..."):
                    df_ma, _, _, _ = load_and_prepare_data()
                    jf_defs = load_jobfamily_definitions()
                    template_bytes = generate_template_bytes(df_ma, jf_defs)
                    
                st.download_button(
                    label="📂 Cluster-Template.xlsx herunterladen",
                    data=template_bytes,
                    file_name="Cluster-Template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_cluster_template"
                )

        with c_col2:
            st.markdown("**2. Definitionen hochladen**")
            st.caption("Laden Sie das bearbeitete Template hier hoch.")
            up_cluster = st.file_uploader("Mapping-Datei hochladen (.xlsx)", type=["xlsx"], key="up_cluster_mappings")
            
            if up_cluster:
                success, msg = validate_and_save_clusters(up_cluster)
                if success:
                    st.success(msg)
                    if st.button("Änderungen jetzt anwenden (Cache leeren)"):
                        st.cache_data.clear()
                        st.rerun()
                else:
                    st.error(msg)
                    
        # Check if file exists
        oe_map, jf_map = load_cluster_mappings()
        if oe_map or jf_map:
            st.info(f"✅ Aktive Mappings: {len(oe_map)} OE-Clusters, {len(jf_map)} JF-Clusters.")

    st.divider()

    # --- Allgemeine Einstellungen (Stichtag) ---
    st.subheader("Allgemeine Einstellungen")

    from utils.settings_loader import get_setting, set_setting, save_user_settings, load_user_settings

    # Stichtag
    current_stichtag_str = get_setting("stichtag", STICHTAG_DEFAULT)
    try:
        current_stichtag = pd.to_datetime(current_stichtag_str).date()
    except Exception:
        current_stichtag = pd.to_datetime(STICHTAG_DEFAULT).date()
        
    new_stichtag = st.date_input(
        "Stichtag (Reference Date)",
        value=current_stichtag,
        help="Bestimmt das Datum für alle Berechnungen (Alter, Dienstjahre, Status).",
    )
    
    if new_stichtag != current_stichtag:
        if st.button("Stichtag speichern"):
            set_setting("stichtag", str(new_stichtag))
            st.success(f"Stichtag auf {new_stichtag} geändert. Bitte Daten neu laden.")
            
    # Zukünftige Eintritte
    include_future_hires = get_setting("include_future_hires", False)
    include_future_cb = st.checkbox(
        "Zukünftige Eintritte berücksichtigen?",
        value=include_future_hires,
        help="Wenn aktiviert, werden Mitarbeiter mit Eintrittsdatum > Stichtag im Headcount mitgezählt.",
    )
    
    if include_future_cb != include_future_hires:
        set_setting("include_future_hires", include_future_cb)
        st.rerun()

    # Statistik anzeigen
    if "stats_future_hires" in st.session_state:
        future_count = st.session_state["stats_future_hires"]
        status_text = "enthalten" if include_future_cb else "ausgefiltert"
        if future_count > 0:
            st.info(f"ℹ️ {future_count} Mitarbeiter mit Eintrittsdatum > {current_stichtag} gefunden (aktuell {status_text}).")
        else:
            st.caption(f"Keine Mitarbeiter mit Eintrittsdatum > {current_stichtag} gefunden.")

    st.divider()

    # --- Simulations-Parameter ---
    st.subheader("Simulations-Parameter")
    st.caption("Standardwerte für Prognosen und Szenarien.")

    sim_settings = get_setting("simulation", {})
    
    with st.form("simulation_settings_form"):
        c1, c2 = st.columns(2)
        with c1:
            s_horizon = st.number_input("Prognose-Horizont (Monate)", value=sim_settings.get("horizon_months", 60), min_value=12, max_value=120)
            s_retire_age = st.number_input("Regelaltersgrenze", value=sim_settings.get("retirement_age", 67), min_value=60, max_value=70)
            s_early_retire = st.number_input("Frühverrentungs-Quote", value=sim_settings.get("early_retirement_share", 0.10), min_value=0.0, max_value=1.0, format="%.2f")
        
        with c2:
            s_hiring_rate = st.number_input("Nachbesetzungs-Quote (p.a.)", value=sim_settings.get("hiring_rate_pa", 0.04), min_value=0.0, max_value=1.0, format="%.2f")
            s_time_to_fill = st.number_input("Time-to-Fill (Monate)", value=sim_settings.get("time_to_fill_months", 3), min_value=1, max_value=24)
            s_azubi_intake = st.number_input("Azubi-Neueinstellungen (p.a.)", value=sim_settings.get("azubi_intake_per_year", 40), min_value=0)

        if st.form_submit_button("Simulations-Parameter speichern"):
            new_sim_settings = {
                "horizon_months": s_horizon,
                "retirement_age": s_retire_age,
                "early_retirement_share": s_early_retire,
                "hiring_rate_pa": s_hiring_rate,
                "time_to_fill_months": s_time_to_fill,
                "azubi_intake_per_year": s_azubi_intake,
                # Preserve other keys if they exist in defaults but not here
                "azubi_duration_months": sim_settings.get("azubi_duration_months", 36),
                "azubi_takeover_rate": sim_settings.get("azubi_takeover_rate", 0.70),
            }
            set_setting("simulation", new_sim_settings)
            st.success("Simulations-Parameter gespeichert. Bitte Layout/Daten neu laden.")

    st.divider()

    # --- Gruppen-Ausschlüsse ---
    st.subheader("Gruppen-Ausschlüsse")
    st.caption(
        "Bestimmte Personengruppen können ausgeklammert werden. "
        "WICHTIG: Ausgeschlossene Personen zählen nicht zum Headcount (Ist), "
        "die Soll-Kapa der Planstelle bleibt aber als Bedarf erhalten (Vakanz)."
    )

    from config.settings import EXCLUSION_ORG_UNITS
    
    # Persistierte Exclusions laden
    current_ex = get_setting("exclusions", {
        "vorstand": False,
        "ruhend_bv": False,
        "org_units": []
    })

    col_ex1, col_ex2 = st.columns(2)
    with col_ex1:
        new_ex_vorstand = st.checkbox(
            "Vorstandsmitglieder ausschließen", 
            value=current_ex.get("vorstand", False),
            help="Basiert auf MitarbGruppenbez. = 'Vorstand'"
        )
    with col_ex2:
        new_ex_ruhend = st.checkbox(
            "Ruhendes BV ausschließen", 
            value=current_ex.get("ruhend_bv", False),
            help="Basiert auf Status = 'Ruhendes Beschäftigungsverhältnis'"
        )

    with st.expander("📁 Spezifische PA-Bereiche ausschließen (99XX)"):
        st.info("Markierte Bereiche werden als Vakanzen behandelt (Soll-Kapa bleibt erhalten).")
        
        btn_col1, btn_col2, _ = st.columns([1, 1, 2])
        if btn_col1.button("Alle auswählen", key="btn_select_all_99"):
            current_ex["org_units"] = list(EXCLUSION_ORG_UNITS.keys())
            for code in EXCLUSION_ORG_UNITS.keys():
                st.session_state[f"chk_ex_{code}"] = True
            set_setting("exclusions", current_ex)
            st.session_state["exclude_org_units"] = current_ex["org_units"]
            st.rerun()
        if btn_col2.button("Alle abwählen", key="btn_deselect_all_99"):
            current_ex["org_units"] = []
            for code in EXCLUSION_ORG_UNITS.keys():
                st.session_state[f"chk_ex_{code}"] = False
            set_setting("exclusions", current_ex)
            st.session_state["exclude_org_units"] = []
            st.rerun()
            
        cols_99 = st.columns(2)
        ex_99_list = current_ex.get("org_units", [])
        new_ex_99 = []
        
        for i, (code, label) in enumerate(EXCLUSION_ORG_UNITS.items()):
            c_idx = i % 2
            # Initialisiere session_state key falls nicht vorhanden, um Konsistenz zu wahren
            key = f"chk_ex_{code}"
            if key not in st.session_state:
                st.session_state[key] = code in ex_99_list
                
            with cols_99[c_idx]:
                if st.checkbox(f"{code} ({label})", key=key):
                    new_ex_99.append(code)

    # Speichern-Logik für Exclusions
    if (new_ex_vorstand != current_ex.get("vorstand") or 
        new_ex_ruhend != current_ex.get("ruhend_bv") or 
        set(new_ex_99) != set(ex_99_list)):
        
        updated_ex = {
            "vorstand": new_ex_vorstand,
            "ruhend_bv": new_ex_ruhend,
            "org_units": new_ex_99
        }
        set_setting("exclusions", updated_ex)
        # Session state für Sidebar-Logik (Sofort-Wirkung ohne Full Reload wenn möglich)
        st.session_state["exclude_vorstand"] = new_ex_vorstand
        st.session_state["exclude_ruhend"] = new_ex_ruhend
        st.session_state["exclude_org_units"] = new_ex_99
        st.rerun()

    st.divider()

    # --- TVÖD-Status ---
    st.subheader("Entgelt & Kosten")
    st.caption("Konfiguration für Gehaltsberechnungen und TVöD.")

    tvoed_available = st.session_state.get("tvoed_available", False)
    tvoed_lookup = st.session_state.get("tvoed_lookup", {})

    if tvoed_available:
        st.success(
            f"TVöD-Tabelle geladen: {len(tvoed_lookup)} Einträge "
            f"(Gruppe x Stufe Kombinationen)"
        )
        with st.expander("Geladene Tarifgruppen anzeigen"):
            groups = sorted(set(g for g, _ in tvoed_lookup.keys()))
            st.write(", ".join(groups))
    else:
        st.warning(
            "TVöD-Tabelle nicht verfügbar. "
            "Kosten werden aus approximierten Fallback-Werten berechnet. "
            "Legen Sie die Datei TVÖD.xlsx im Ordner Original-Daten ab, "
            "um exakte Werte zu verwenden."
        )
    
    st.markdown("##### Arbeitgeber-Kostenfaktor (Lohnnebenkosten)")
    st.caption("Dieser Faktor wird auf das Bruttogehalt aufgeschlagen, um die tatsächlichen Arbeitgeberkosten abzubilden (z. B. 1,25 = +25%).")
    employer_factor = st.number_input(
        "Arbeitgeber-Kostenfaktor",
        min_value=1.0,
        max_value=2.0,
        value=st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR),
        step=0.01,
        format="%.2f",
        key="input_employer_factor",
        help="Standardmäßig 1,25 (entspricht ca. 20-25% Sozialabgaben-Aufschlag)",
        label_visibility="collapsed"
    )
    st.session_state["employer_cost_factor"] = employer_factor

    st.markdown("##### Sonderfall-Gehälter")
    st.caption("Manuelle Jahresgehälter für nicht-TVöD Gruppen.")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Auszubildende** (TrfGr = TVAÖD)")
        st.caption("Lehrjahr-abhängige Jahresgehälter (brutto)")
        
        from config.settings import DEFAULT_AZUBI_SALARIES
        current_azubi_salaries = st.session_state.get("azubi_salaries", DEFAULT_AZUBI_SALARIES)
        new_azubi_salaries = {}
        
        az_cols = st.columns(2)
        for year in range(1, 5):
            c_idx = (year - 1) % 2
            with az_cols[c_idx]:
                new_val = st.number_input(
                    f"Gehalt {year}. Lehrjahr",
                    min_value=0.0,
                    value=float(current_azubi_salaries.get(str(year), current_azubi_salaries.get(year, DEFAULT_AZUBI_SALARIES[year]))),
                    step=100.0,
                    format="%.2f",
                    key=f"az_sal_{year}",
                    help=f"Jährliches Bruttogehalt für Auszubildende im {year}. Jahr"
                )
                new_azubi_salaries[year] = new_val
        
        st.session_state["azubi_salaries"] = new_azubi_salaries

    with col2:
        st.markdown("**Vorstand** (TrfGr = 1)")
        vorstand_input = st.number_input(
            "Vorstand-Jahresgehalt (brutto)",
            min_value=0.0,
            max_value=1000000.0,
            value=None,
            placeholder="Individueller Vertrag...",
            step=1000.0,
            key="input_vorstand_gehalt",
            help="Vorstandsvergütung ist nicht im TVöD geregelt. Falls leer, wird mit dem System-Default gerechnet.",
        )
        if vorstand_input is not None:
            st.session_state["vorstand_jahresgehalt"] = vorstand_input
        else:
            # Wenn leer, Override entfernen -> Loader nutzt Default (200k)
            if "vorstand_jahresgehalt" in st.session_state:
                del st.session_state["vorstand_jahresgehalt"]

    st.divider()

    # --- Hinweis zum Neuladen ---
    st.info(
        "Änderungen an diesen Einstellungen werden erst nach einem "
        "Neuladen der Daten wirksam. Nutzen Sie dazu den Button unten "
        "oder laden Sie die Seite neu (F5)."
    )

    if st.button("Daten neu laden", type="primary"):
        st.cache_data.clear()
        st.rerun()


# --- Page Entry Point ---
render_settings_page()
