"""
Modul 3: Altersteilzeit

Analysen und Planung für Altersteilzeit (ATZ).
"""

import streamlit as st
import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.loader import load_and_prepare_data
from config.settings import format_number, format_currency, format_percent, get_status_color, THRESHOLDS
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from components.toggle import format_value
from components.kpi_card import kpi_card, kpi_row
from components.charts import (
    create_donut_chart, create_bar_chart, create_line_chart,
    create_funnel_chart, create_gantt_chart
)
from utils.ui_helpers import metric_info, section_header


def load_custom_css():
    """Lädt Custom CSS."""
    css_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "style.css")
    if os.path.exists(css_path):
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def calculate_atz_end_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Berechnet geschätzte ATZ-Enddaten basierend auf Alter und Phase.

    Annahmen:
    - ATZ läuft bis Renteneintritt mit 67
    - Arbeitsphase und Freistellungsphase sind gleich lang
    """
    df = df.copy()

    # Nur ATZ-Mitarbeitende
    atz_df = df[df["ATZ_Status"] != "Kein ATZ"].copy()

    if len(atz_df) == 0:
        return pd.DataFrame()

    # Berechne Renteneintritt (67 Jahre)
    atz_df["Renteneintritt"] = pd.to_datetime(atz_df["GebDatum"]) + pd.DateOffset(years=67)

    # Berechne ATZ-Start (geschätzt 5 Jahre vor Renteneintritt)
    atz_df["ATZ_Start"] = atz_df["Renteneintritt"] - pd.DateOffset(years=5)

    # Berechne Phasenende (Mitte zwischen Start und Renteneintritt)
    atz_df["Phasen_Wechsel"] = atz_df["ATZ_Start"] + (atz_df["Renteneintritt"] - atz_df["ATZ_Start"]) / 2

    return atz_df


def render_funnel_section(df: pd.DataFrame):
    """Rendert den ATZ-Funnel."""
    st.markdown("#### 📊 ATZ-Funnel: Vom Gesamtbestand zur Freistellungsphase")

    # Nur besetzte Stellen
    active_df = df[~df["Is_Vacant"]]

    # Funnel-Stufen
    total_employees = len(active_df)
    atz_berechtigt = len(active_df[active_df["Alter"] >= 55])  # 55+ berechtigt
    in_atz = len(active_df[active_df["ATZ_Status"] != "Kein ATZ"])
    arbeitsphase = len(active_df[active_df["ATZ_Status"] == "Arbeitsphase"])
    freistellung = len(active_df[active_df["ATZ_Status"] == "Freistellungsphase"])

    stages = [
        "Gesamtbelegschaft",
        "ATZ-Berechtigt (55+)",
        "In ATZ",
        "Arbeitsphase",
        "Freistellungsphase"
    ]

    values = [total_employees, atz_berechtigt, in_atz, arbeitsphase, freistellung]

    fig_funnel = create_funnel_chart(
        stages=stages,
        values=values,
        title=""
    )

    st.plotly_chart(fig_funnel, use_container_width=True)

    # Konversionsraten
    col1, col2, col3 = st.columns(3)

    with col1:
        if atz_berechtigt > 0:
            conversion_rate = (in_atz / atz_berechtigt) * 100
            st.metric(
                "ATZ-Aufnahmequote",
                f"{conversion_rate:.1f}%",
                help="Anteil der Berechtigten, die in ATZ sind"
            )

    with col2:
        if in_atz > 0:
            phase_split = (freistellung / in_atz) * 100
            st.metric(
                "In Freistellung",
                f"{phase_split:.1f}%",
                help="Anteil ATZ in Freistellungsphase"
            )

    with col3:
        if total_employees > 0:
            atz_total_rate = (in_atz / total_employees) * 100
            st.metric(
                "Gesamt-ATZ-Quote",
                f"{atz_total_rate:.1f}%",
                help="ATZ-Anteil an Gesamtbelegschaft"
            )


def render_timeline_section(df: pd.DataFrame):
    """Rendert Gantt-Timeline der ATZ-Verläufe."""
    st.markdown("#### 📅 ATZ-Timeline: Phasenübergänge")

    atz_timeline = calculate_atz_end_dates(df)

    if len(atz_timeline) == 0:
        st.info("Keine ATZ-Mitarbeitenden im ausgewählten Bereich.")
        return

    # Top 20 für bessere Lesbarkeit
    atz_timeline = atz_timeline.nlargest(20, "Alter")

    # Erstelle Gantt-Daten
    gantt_data = []

    for _, row in atz_timeline.iterrows():
        name = f"{row.get('Vorname', 'N/A')} {row.get('Nachname', 'N/A')} ({int(row['Alter'])})"

        # Arbeitsphase
        gantt_data.append({
            "Name": name,
            "Start": row["ATZ_Start"].strftime("%Y-%m-%d"),
            "Ende": row["Phasen_Wechsel"].strftime("%Y-%m-%d"),
            "Phase": "Arbeitsphase"
        })

        # Freistellungsphase
        gantt_data.append({
            "Name": name,
            "Start": row["Phasen_Wechsel"].strftime("%Y-%m-%d"),
            "Ende": row["Renteneintritt"].strftime("%Y-%m-%d"),
            "Phase": "Freistellungsphase"
        })

    gantt_df = pd.DataFrame(gantt_data)

    fig_gantt = create_gantt_chart(
        gantt_df,
        start_col="Start",
        end_col="Ende",
        task_col="Name",
        color_col="Phase",
        title="",
        height=600
    )

    st.plotly_chart(fig_gantt, use_container_width=True)


def render_org_breakdown(df: pd.DataFrame, view_mode: str):
    """Rendert ATZ-Verteilung nach Organisation."""
    st.markdown("#### 🏢 ATZ-Verteilung nach Organisationseinheit")

    # Nur ATZ-Mitarbeitende
    atz_df = df[(~df["Is_Vacant"]) & (df["ATZ_Status"] != "Kein ATZ")]

    if len(atz_df) == 0:
        st.info("Keine ATZ-Mitarbeitenden im ausgewählten Bereich.")
        return

    # Aggregiere nach Org und Phase
    org_phase = atz_df.groupby(["Organisationseinheit", "ATZ_Status"]).agg({
        "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year": "sum"
    }).reset_index()

    org_phase.columns = ["Organisation", "Phase", "Wert"]

    # Top 10 Organisationen
    top_orgs = org_phase.groupby("Organisation")["Wert"].sum().nlargest(10).index
    org_phase_filtered = org_phase[org_phase["Organisation"].isin(top_orgs)]

    fig_org = create_bar_chart(
        org_phase_filtered,
        x_col="Wert",
        y_col="Organisation",
        color_col="Phase",
        orientation="h",
        title=""
    )

    st.plotly_chart(fig_org, use_container_width=True)


def render_history_section(history_df: pd.DataFrame):
    """Rendert ATZ-Quote Entwicklung über Zeit."""
    st.markdown("#### 📈 ATZ-Quote Entwicklung")

    if history_df.empty:
        st.info("Keine historischen Daten verfügbar.")
        return

    # Filter nach Datumsbereich
    if st.session_state.get("date_range"):
        date_range = st.session_state["date_range"]
        history_filtered = history_df[
            (history_df["Date"] >= pd.to_datetime(date_range[0])) &
            (history_df["Date"] <= pd.to_datetime(date_range[1]))
        ]
    else:
        history_filtered = history_df

    # Aggregiere über alle Org-Einheiten
    time_series = history_filtered.groupby("Date").agg({
        "Headcount": "sum",
        "FTE": "sum"
    }).reset_index()

    # Berechne ATZ-Quote (Näherung: angenommen konstant aus aktuellen Daten)
    # In echten Daten müsste ATZ-Count im History_Cube enthalten sein

    st.info(
        "💡 **Hinweis**: ATZ-Quote über Zeit benötigt erweiterte History-Daten. "
        "Aktuell wird die Kapazitätsentwicklung angezeigt."
    )

    fig_timeline = create_line_chart(
        time_series,
        x_col="Date",
        y_col="FTE",
        title="Kapazitätsentwicklung (FTE)"
    )

    st.plotly_chart(fig_timeline, use_container_width=True)


def render_detail_table(df: pd.DataFrame, view_mode: str):
    """Rendert Detail-Tabelle mit Export."""
    st.markdown("#### 📋 ATZ-Detailliste")

    # Nur ATZ-Mitarbeitende
    atz_df = df[(~df["Is_Vacant"]) & (df["ATZ_Status"] != "Kein ATZ")].copy()

    if len(atz_df) == 0:
        st.info("Keine ATZ-Mitarbeitenden im ausgewählten Bereich.")
        return

    # Berechne Enddaten
    atz_timeline = calculate_atz_end_dates(atz_df)

    # Auswahl der Spalten
    display_cols = [
        "PersNr",
        "Organisationseinheit",
        "Alter",
        "Geschlecht",
        "ATZ_Status",
        "FTE_assigned",
        "Total_Cost_Year",
        "ATZ_Start",
        "Phasen_Wechsel",
        "Renteneintritt"
    ]

    # Filtere vorhandene Spalten
    available_cols = [col for col in display_cols if col in atz_timeline.columns]
    display_df = atz_timeline[available_cols].copy()

    # Formatierung
    display_df["Alter"] = display_df["Alter"].astype(int)
    display_df["FTE_assigned"] = display_df["FTE_assigned"].round(2)

    if "Total_Cost_Year" in display_df.columns:
        display_df["Total_Cost_Year"] = display_df["Total_Cost_Year"].apply(lambda x: f"{x:,.0f} €")

    # Datumsformatierung
    for col in ["ATZ_Start", "Phasen_Wechsel", "Renteneintritt"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].dt.strftime("%Y-%m-%d")

    # Spalten umbenennen
    rename_map = {
        "PersNr": "Pers.Nr.",
        "Organisationseinheit": "Organisation",
        "Geschlecht": "Geschl.",
        "ATZ_Status": "Phase",
        "FTE_assigned": "FTE",
        "Total_Cost_Year": "Kosten/Jahr",
        "ATZ_Start": "ATZ Start",
        "Phasen_Wechsel": "Phasenwechsel",
        "Renteneintritt": "Rente"
    }

    display_df = display_df.rename(columns=rename_map)

    # Tabelle anzeigen
    st.dataframe(
        display_df,
        use_container_width=True,
        height=400
    )

    # Export-Button
    col1, col2 = st.columns([3, 1])

    with col2:
        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 CSV Export",
            data=csv,
            file_name=f"atz_detailliste_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )


def main():
    # Custom CSS laden
    load_custom_css()

    st.title("🔄 Altersteilzeit")

    try:
        # Lade Daten
        snapshot_df, history_df, org_df, summary = load_and_prepare_data()

        # Globale Filter-Sidebar
        render_global_filters(snapshot_df, history_df)

        # Filter anwenden
        filtered_df = apply_filters(snapshot_df)

        # Filter-Summary anzeigen
        filter_info = get_filter_summary()
        st.markdown(f"<div class='filter-summary'>{filter_info}</div>", unsafe_allow_html=True)

        # View Mode aus Session State lesen
        view_mode = st.session_state.get("view_mode", "MAK")

        st.divider()

        # Nur besetzte Stellen für Analysen
        active_df = filtered_df[~filtered_df["Is_Vacant"]]

        # KPI Row
        st.markdown("### 📈 Zentrale Kennzahlen")

        atz_gesamt = len(active_df[active_df["ATZ_Status"] != "Kein ATZ"])
        atz_arbeitsphase = len(active_df[active_df["ATZ_Status"] == "Arbeitsphase"])
        atz_freistellung = len(active_df[active_df["ATZ_Status"] == "Freistellungsphase"])
        atz_berechtigt = len(active_df[active_df["Alter"] >= 55])

        if view_mode == "MAK":
            atz_fte = active_df[active_df["ATZ_Status"] != "Kein ATZ"]["FTE_assigned"].sum()
            atz_value = format_value(atz_fte, "MAK")
        else:
            atz_cost = active_df[active_df["ATZ_Status"] != "Kein ATZ"]["Total_Cost_Year"].sum()
            atz_value = format_value(atz_cost, "Euro")

        # ATZ-Quote Status
        atz_quote = (atz_gesamt / len(active_df)) if len(active_df) > 0 else 0
        atz_status = "good" if atz_quote <= THRESHOLDS["atz_quote"]["good"] else \
                     "warning" if atz_quote <= THRESHOLDS["atz_quote"]["warning"] else \
                     "critical"

        kpis = [
            {
                "title": "ATZ Gesamt",
                "value": str(atz_gesamt),
                "subtitle": atz_value,
                "icon": "🔄",
                "status": atz_status
            },
            {
                "title": "Arbeitsphase",
                "value": str(atz_arbeitsphase),
                "subtitle": f"{(atz_arbeitsphase/atz_gesamt*100) if atz_gesamt > 0 else 0:.1f}% der ATZ",
                "icon": "💼",
                "status": "good"
            },
            {
                "title": "Freistellungsphase",
                "value": str(atz_freistellung),
                "subtitle": f"{(atz_freistellung/atz_gesamt*100) if atz_gesamt > 0 else 0:.1f}% der ATZ",
                "icon": "🏖️",
                "status": "good"
            },
            {
                "title": "ATZ-Berechtigt (55+)",
                "value": str(atz_berechtigt),
                "subtitle": f"{(atz_berechtigt/len(active_df)*100) if len(active_df) > 0 else 0:.1f}% der Belegschaft",
                "icon": "👥"
            }
        ]

        kpi_row(kpis)

        st.divider()

        # Funnel Section
        render_funnel_section(filtered_df)

        st.divider()

        # 2-Column: Timeline + Org Breakdown
        col1, col2 = st.columns([1.2, 1])

        with col1:
            render_timeline_section(filtered_df)

        with col2:
            render_org_breakdown(filtered_df, view_mode)

        st.divider()

        # History Section
        render_history_section(history_df)

        st.divider()

        # Detail Table
        render_detail_table(filtered_df, view_mode)

        # Debug-Info (expandable)
        with st.expander("🔍 Debug: ATZ-Statistiken"):
            st.write("**ATZ-Verteilung:**")
            st.write(active_df["ATZ_Status"].value_counts())

            st.write("**ATZ nach Alter:**")
            atz_age = active_df[active_df["ATZ_Status"] != "Kein ATZ"]["Alter"].describe()
            st.write(atz_age)

    except FileNotFoundError:
        st.error(
            "❌ **Testdaten nicht gefunden!**\n\n"
            "Bitte generiere zuerst die Testdaten mit:\n"
            "```\npython data/synthetic.py\n```"
        )
    except Exception as e:
        st.error(f"Fehler beim Laden der Daten: {str(e)}")
        import traceback
        st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
