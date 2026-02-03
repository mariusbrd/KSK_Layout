"""
Modul 8: Einstellungen

Konfigurationsseite für Loader-spezifische Parameter wie
Sonderfall-Gehälter (Azubis, Vorstand) und Arbeitgeber-Kostenfaktor.
"""

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


def render_settings_page():
    st.header("Einstellungen")
    st.caption("Loader-spezifische Parameter für Kostenberechnung")

    st.divider()

    # --- TVÖD-Status ---
    st.subheader("TVöD-Entgelttabelle")

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

    st.divider()

    # --- Sonderfall-Gehälter ---
    st.subheader("Sonderfall-Gehälter")
    st.caption(
        "Für Tarifgruppen, die nicht in der TVöD-Tabelle enthalten sind, "
        "können hier manuelle Jahresgehälter festgelegt werden."
    )

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

    # --- Arbeitgeber-Kostenfaktor ---
    st.subheader("Arbeitgeber-Kostenfaktor")
    st.caption(
        "Aufschlag auf das Bruttogehalt für Sozialabgaben, "
        "Zusatzversorgung und sonstige Arbeitgeberkosten."
    )

    employer_factor = st.number_input(
        "Kostenfaktor",
        min_value=1.0,
        max_value=2.0,
        value=st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR),
        step=0.01,
        format="%.2f",
        key="input_employer_factor",
        help="1.25 bedeutet 25% Aufschlag auf das Bruttogehalt",
    )
    st.session_state["employer_cost_factor"] = employer_factor

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
