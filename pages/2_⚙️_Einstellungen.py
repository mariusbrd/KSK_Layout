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

        if st.session_state["global_uploads"]:
            st.success(f"✅ {len(st.session_state['global_uploads'])} Dateien aktiv.")
            if st.button("Alle Uploads löschen"):
                 st.session_state["global_uploads"] = {}
                 st.rerun()

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
        "Bestimmte Personengruppen können global aus allen Auswertungen "
        "und Kennzahlen ausgeschlossen werden. Die Ausschlüsse wirken auf allen Seiten."
    )

    # Zählung aus ungefiltertem Snapshot
    try:
        from dataloader.loader import load_and_prepare_data
        snapshot_df, _, _, _ = load_and_prepare_data()

        kuerzel_col = "Kürzel OrgEinheit"
        kuerzel_str = (
            snapshot_df[kuerzel_col].astype(str)
            if kuerzel_col in snapshot_df.columns
            else pd.Series(dtype=str)
        )

        # Auszubildende: Ist_Azubi OR OrgEinheit 9910
        azubi_mask = pd.Series(False, index=snapshot_df.index)
        if "Ist_Azubi" in snapshot_df.columns:
            azubi_mask = azubi_mask | (snapshot_df["Ist_Azubi"] == True)
        if kuerzel_col in snapshot_df.columns:
            azubi_mask = azubi_mask | (kuerzel_str == "9910")
        n_azubi = int(azubi_mask.sum())

        # Elternzeit: OrgEinheit 9971
        n_elternzeit = int((kuerzel_str == "9971").sum()) if kuerzel_col in snapshot_df.columns else 0

        # Erziehungszeit: OrgEinheit 9975
        n_erziehungszeit = int((kuerzel_str == "9975").sum()) if kuerzel_col in snapshot_df.columns else 0
    except Exception:
        n_azubi = "?"
        n_elternzeit = "?"
        n_erziehungszeit = "?"

    st.checkbox(
        f"Auszubildende ausschließen (n={n_azubi})",
        value=st.session_state.get("exclude_auszubildende", False),
        key="cb_exclude_auszubildende",
        help="Schließt Auszubildende aus (MitarbGruppenbez. = 'Auszubildende' oder OrgEinheit 9910)",
    )
    st.session_state["exclude_auszubildende"] = st.session_state["cb_exclude_auszubildende"]

    st.checkbox(
        f"Elternzeit ausschließen (n={n_elternzeit})",
        value=st.session_state.get("exclude_elternzeit", False),
        key="cb_exclude_elternzeit",
        help="Schließt Personen in Elternzeit aus (OrgEinheit 9971)",
    )
    st.session_state["exclude_elternzeit"] = st.session_state["cb_exclude_elternzeit"]

    st.checkbox(
        f"Erziehungszeit ausschließen (n={n_erziehungszeit})",
        value=st.session_state.get("exclude_erziehungszeit", False),
        key="cb_exclude_erziehungszeit",
        help="Schließt Personen in Erziehungszeit aus (OrgEinheit 9975)",
    )
    st.session_state["exclude_erziehungszeit"] = st.session_state["cb_exclude_erziehungszeit"]

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
    
    st.markdown("##### Arbeitgeber-Kostenfaktor")
    employer_factor = st.number_input(
        "Kostenfaktor",
        min_value=1.0,
        max_value=2.0,
        value=st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR),
        step=0.01,
        format="%.2f",
        key="input_employer_factor",
        help="1.25 bedeutet 25% Aufschlag auf das Bruttogehalt",
        label_visibility="collapsed"
    )
    st.session_state["employer_cost_factor"] = employer_factor
    st.caption("Aufschlag für Sozialabgaben (z.B. 1.25 = +25%)")

    st.markdown("##### Sonderfall-Gehälter")
    st.caption("Manuelle Jahresgehälter für nicht-TVöD Gruppen.")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Auszubildende** (TrfGr = TVAÖD)")
        azubi_gehalt = st.number_input(
            "Azubi-Jahresgehalt (brutto)",
            min_value=0.0,
            max_value=100000.0,
            value=st.session_state.get("azubi_jahresgehalt", DEFAULT_AZUBI_JAHRESGEHALT),
            step=100.0,
            format="%.2f",
            key="input_azubi_gehalt",
            help="Typisches Azubi-Gehalt im TVöD liegt bei ca. 1.200 EUR/Monat (14.400 EUR/Jahr)",
        )
        st.session_state["azubi_jahresgehalt"] = azubi_gehalt

    with col2:
        st.markdown("**Vorstand** (TrfGr = 1)")
        vorstand_gehalt = st.number_input(
            "Vorstand-Jahresgehalt (brutto)",
            min_value=0.0,
            max_value=1000000.0,
            value=st.session_state.get("vorstand_jahresgehalt", DEFAULT_VORSTAND_JAHRESGEHALT),
            step=1000.0,
            format="%.2f",
            key="input_vorstand_gehalt",
            help="Vorstandsvergütung ist nicht im TVöD geregelt",
        )
        st.session_state["vorstand_jahresgehalt"] = vorstand_gehalt

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
