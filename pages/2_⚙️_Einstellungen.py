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
from dataloader.source_service import SourceService, DataSourceOrigin
from config.settings import BASE_DIR
from components.sidebar import render_metric_selector_only, set_metric_page_hint


SALARY_AUTOMATION_DEFAULTS = {
    "enabled": False,
    "scope": "new_hires_only",
    "fallback_step": 4,
    "e1_entry_step": 2,
    "e2_plus_default_entry_step": 1,
    "e1_progression_years": [4, 4, 4, 4],
    "e2_plus_progression_years": [1, 2, 3, 4, 5],
    "use_tenure_as_step_proxy_for_existing_staff": False,
}


def render_settings_page():
    set_metric_page_hint(
        "Diese Seite dient der Konfiguration. "
        "Die globale Pille hat hier derzeit keine fachliche Wirkung."
    )
    render_metric_selector_only()

    st.title("Einstellungen")
    st.caption("Loader-spezifische Parameter für Kostenberechnung")

    st.divider()

    # --- Success Message Helper ---
    if st.session_state.get("show_reload_success"):
        uploads = st.session_state.get("global_uploads", {})
        original_dir = os.path.join(BASE_DIR, "..", "Original-Daten")
        cluster_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "Cluster-Daten"))
        
        with st.container():
            st.success("✅ **Daten erfolgreich neu geladen!**")
            
            diag_col1, diag_col2 = st.columns(2)
            
            with diag_col1:
                st.markdown("**Datenquellen Status:**")
                for group in SourceService.GROUPS.keys():
                    status = SourceService.derive_group_status(group, uploads, original_dir, cluster_dir)
                    st.markdown(f"- **{group}**: {status.origin.value} ({status.completeness_label})")
            
            with diag_col2:
                st.markdown("**Aktive Einstellungen:**")
                oe_map, jf_map = load_cluster_mappings()
                st.markdown(f"- **Cluster**: {len(oe_map)} OE / {len(jf_map)} JF Mappings")
                tvoed_ok = st.session_state.get("tvoed_available", False)
                st.markdown(f"- **Entgelttabelle**: {'Aktiv' if tvoed_ok else 'Fallback-Modus'}")
                
            st.divider()
            # Reset flag after rendering once
            st.session_state["show_reload_success"] = False

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
                    # Register in session state for SourceService and Loader
                    if "global_uploads" not in st.session_state:
                         st.session_state["global_uploads"] = {}
                    st.session_state["global_uploads"]["Cluster"] = up_cluster.getvalue()
                    
                    st.success(msg)
                    if st.button("Änderungen jetzt anwenden (Cache leeren)"):
                        st.cache_data.clear()
                        st.session_state["show_reload_success"] = True
                        st.rerun()
                else:
                    st.error(msg)
                    
        # Check if file exists (considering session override)
        cluster_override = st.session_state.get("global_uploads", {}).get("Cluster")
        oe_map, jf_map = load_cluster_mappings(cluster_override)
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

    # --- Gruppen-Ausschlüsse (verschoben) ---
    st.subheader("Gruppen-Ausschlüsse")
    st.info(
        "Die Konfiguration der Exklusionsgruppen (inkl. Scope Mitarbeiter vs. gesamtes Dashboard) "
        "wurde in die Deep-Dive-Seite verschoben. "
        "Dort können alle Gruppen mit Checkboxen ein- und ausgeschlossen werden — "
        "inklusive Planstellen-Übersicht, Kapazitätsanalyse und Drilldown pro Gruppe.\n\n"
        "👉 **Navigiere zu: 🔎 Exklusionsgruppen**"
    )

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

    st.markdown("##### Stufenautomatik / Gehaltsautomatik")
    st.caption(
        "Transparenz über die aktuelle TVöD-Stufenlogik und vorbereitende "
        "Konfiguration für eine explizite Stufenautomatik."
    )

    salary_automation = get_setting("salary_automation", {})
    salary_automation = {**SALARY_AUTOMATION_DEFAULTS, **salary_automation}

    current_source = "TVöD-Tabelle" if tvoed_available else "Fallback-Werte"
    scope_label = {
        "new_hires_only": "nur simulierte Zugänge",
        "all_staff": "simulierte Zugänge und Bestand",
    }.get(salary_automation.get("scope"), "nur simulierte Zugänge")

    sa_info_col1, sa_info_col2 = st.columns(2)
    with sa_info_col1:
        st.markdown("**Aktuelle Berechnungslogik**")
        st.markdown(f"- **Gehaltsquelle**: {current_source}")
        st.markdown("- **Stufenquelle**: vorhandener Datenwert aus `St`")
        st.markdown(
            f"- **Technischer System-Fallback bei fehlender Stufe**: aktuell Stufe {salary_automation['fallback_step']}"
        )
        st.markdown(
            "- **Automatische Stufenfortschreibung**: wird in `Kompakt plus Simulation` "
            "bei aktivierter Konfiguration für TVöD-Stufen innerhalb derselben Entgeltgruppe berücksichtigt"
        )
    with sa_info_col2:
        st.markdown("**Vorbereitete Automatik-Konfiguration**")
        st.markdown(
            f"- **Konfigurationsstatus**: {'aktiviert' if salary_automation['enabled'] else 'deaktiviert'}"
        )
        st.markdown(f"- **Vorgesehener Scope**: {scope_label}")
        st.markdown(
            f"- **E1 Einstieg**: Stufe {salary_automation['e1_entry_step']} | "
            f"**E2-E15 Einstieg**: Stufe {salary_automation['e2_plus_default_entry_step']}"
        )
        st.markdown(
            "- **Hinweis**: Die Parameter wirken aktuell auf die Zukunftsfortschreibung "
            "der TVöD-Stufen in `Kompakt plus Simulation`. "
            "Entgeltgruppenwechsel werden dabei noch nicht simuliert."
        )

    rules_df = pd.DataFrame(
        [
            {
                "Bereich": "E1",
                "Einstieg": "Stufe 2",
                "Stufenlaufzeit": "4 / 4 / 4 / 4 Jahre",
                "Status im System": "fachliche Referenz",
            },
            {
                "Bereich": "E2-E15",
                "Einstieg": "im Regelfall Stufe 1",
                "Stufenlaufzeit": "1 / 2 / 3 / 4 / 5 Jahre",
                "Status im System": "fachliche Referenz",
            },
            {
                "Bereich": "Bestand heute",
                "Einstieg": "aus Datenbestand",
                "Stufenlaufzeit": "keine automatische Fortschreibung",
                "Status im System": "aktiver Rechenmodus",
            },
            {
                "Bereich": "Simulation",
                "Einstieg": "teilweise parametriert",
                "Stufenlaufzeit": "optional aktivierbar",
                "Status im System": "wirkt auf TVöD-Stufenfortschreibung innerhalb der Entgeltgruppe",
            },
        ]
    )

    with st.expander("TVöD-Regeln und heutiger Systemstatus"):
        st.dataframe(rules_df, use_container_width=True, hide_index=True)
        st.info(
            "Die TVöD-Regeln werden hier explizit dokumentiert. "
            "Die operative Nutzung für eine automatische Stufenfortschreibung "
            "folgt in einem separaten Implementierungsschritt."
        )

    with st.form("salary_automation_form"):
        sa_col1, sa_col2 = st.columns(2)

        with sa_col1:
            sa_enabled = st.checkbox(
                "Stufenautomatik vorbereiten",
                value=bool(salary_automation["enabled"]),
                help="Speichert die gewünschte Konfiguration für eine explizite Stufenautomatik.",
            )
            sa_scope_label = st.selectbox(
                "Automatik anwenden auf",
                options=["nur simulierte Zugänge", "simulierte Zugänge und Bestand"],
                index=0 if salary_automation["scope"] == "new_hires_only" else 1,
                help="Der zweite Modus wäre für den Bestand nur als vereinfachende Heuristik belastbar.",
            )
            sa_fallback_step = st.number_input(
                "Technischer System-Fallback bei fehlender Stufe",
                min_value=1,
                max_value=6,
                value=int(salary_automation["fallback_step"]),
                step=1,
                help="Kein TVöD-Regelwert, sondern nur der aktuelle System-Fallback bei fehlender oder unklarer Stufe.",
            )
            sa_existing_proxy = st.checkbox(
                "Betriebszugehörigkeit als Proxy für Bestands-Stufenlaufzeit vormerken",
                value=bool(salary_automation["use_tenure_as_step_proxy_for_existing_staff"]),
                help="Nur vorbereitend. Würde im Bestand eine vereinfachende Heuristik bedeuten.",
            )

        with sa_col2:
            st.text_input(
                "E1 Einstieg (TVöD-Referenz)",
                value=f"Stufe {salary_automation['e1_entry_step']}",
                disabled=True,
                help="Für E1 gilt tariflich der Einstieg in Stufe 2. Dieser Wert wird hier bewusst nur referenziert.",
            )
            sa_e2_entry = st.number_input(
                "E2-E15 Standard-Einstieg (Regelfall)",
                min_value=1,
                max_value=6,
                value=int(salary_automation["e2_plus_default_entry_step"]),
                step=1,
                help="Regelfall ohne explizit anrechenbare Berufserfahrung.",
            )
            st.caption("Stufenlaufzeiten in Jahren")
            e1_cols = st.columns(4)
            e1_progression_years = []
            for idx, col in enumerate(e1_cols):
                with col:
                    e1_progression_years.append(
                        int(
                            st.number_input(
                                f"E1 -> {idx + 3}",
                                min_value=1,
                                max_value=10,
                                value=int(salary_automation["e1_progression_years"][idx]),
                                step=1,
                                key=f"salary_auto_e1_{idx}",
                            )
                        )
                    )

            e2_cols = st.columns(5)
            e2_progression_years = []
            for idx, col in enumerate(e2_cols):
                with col:
                    e2_progression_years.append(
                        int(
                            st.number_input(
                                f"E2+ -> {idx + 2}",
                                min_value=1,
                                max_value=10,
                                value=int(salary_automation["e2_plus_progression_years"][idx]),
                                step=1,
                                key=f"salary_auto_e2_{idx}",
                            )
                        )
                    )

        if st.form_submit_button("Stufenautomatik-Konfiguration speichern"):
            new_salary_automation = {
                "enabled": sa_enabled,
                "scope": "new_hires_only" if sa_scope_label == "nur simulierte Zugänge" else "all_staff",
                "fallback_step": int(sa_fallback_step),
                "e1_entry_step": int(salary_automation["e1_entry_step"]),
                "e2_plus_default_entry_step": int(sa_e2_entry),
                "e1_progression_years": e1_progression_years,
                "e2_plus_progression_years": e2_progression_years,
                "use_tenure_as_step_proxy_for_existing_staff": bool(sa_existing_proxy),
            }
            set_setting("salary_automation", new_salary_automation)
            st.success(
                "Stufenautomatik-Konfiguration gespeichert. "
                "Die Parameter werden jetzt in `Kompakt plus Simulation` "
                "für die TVöD-Stufenfortschreibung berücksichtigt."
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
        st.session_state["show_reload_success"] = True
        st.rerun()


# --- Page Entry Point ---
render_settings_page()
