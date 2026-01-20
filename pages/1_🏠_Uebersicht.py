"""
Modul 1: Überblick

Zeigt zentrale KPIs und Zusammenfassung der HR-Daten.
"""

import streamlit as st
import pandas as pd
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.loader import load_and_prepare_data
from config.settings import format_number, format_currency, format_percent, get_status_color, THRESHOLDS
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from components.toggle import format_value
from components.kpi_card import kpi_card, kpi_row
from components.charts import (
    create_donut_chart, create_bar_chart, create_stacked_area_chart,
    create_line_chart, create_gauge_chart
)
from utils.ui_helpers import metric_info, section_header


def load_custom_css():
    """Lädt Custom CSS."""
    css_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "style.css")
    if os.path.exists(css_path):
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def main():
    # Custom CSS laden
    load_custom_css()

    st.title("🏠 Überblick")

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

        # Neuberechnung der Summary für gefilterte Daten
        filtered_summary = calculate_summary(filtered_df)

        # KPI Row mit Custom Cards
        section_header(
            "Zentrale Kennzahlen",
            "Überblick über die wichtigsten Personalkapazitäts- und Besetzungsmetriken",
            "📈"
        )

        if view_mode == "MAK":
            metric_info("MAK (Mitarbeiterkapazität)",
                       "Die Gesamtkapazität in Vollzeitäquivalenten (FTE). 1 MAK = 1 Vollzeitkraft. Teilzeitkräfte werden anteilig gezählt.")
        else:
            metric_info("Gesamtkosten",
                       "Summe aller Personalkosten inkl. Sozialabgaben und Arbeitgeberkosten. Basis für Budgetplanung und Kostenkontrolle.")

        # Werte basierend auf View Mode
        if view_mode == "MAK":
            main_value = format_value(filtered_summary["total_fte"], "MAK")
            main_subtitle = f"{filtered_summary['total_employees']:,} Mitarbeitende".replace(",", ".")
        else:
            main_value = format_value(filtered_summary["total_cost"], "Euro")
            main_subtitle = f"{filtered_summary['total_fte']:.1f} FTE"

        # Besetzungsgrad Status
        besetzungsgrad_status = "good" if filtered_summary["besetzungsgrad"] >= THRESHOLDS["besetzungsgrad"]["good"] else \
                               "warning" if filtered_summary["besetzungsgrad"] >= THRESHOLDS["besetzungsgrad"]["warning"] else \
                               "critical"

        # ATZ Status
        atz_status = "good" if filtered_summary["atz_rate"] <= THRESHOLDS["atz_quote"]["good"] else \
                    "warning" if filtered_summary["atz_rate"] <= THRESHOLDS["atz_quote"]["warning"] else \
                    "critical"

        kpis = [
            {
                "title": "Gesamt-MAK" if view_mode == "MAK" else "Gesamtkosten",
                "value": main_value,
                "subtitle": main_subtitle,
                "icon": "📊",
                "status": "good"
            },
            {
                "title": "Besetzungsgrad",
                "value": format_percent(filtered_summary["besetzungsgrad"]),
                "subtitle": f"{filtered_summary['vacancy_count']} Vakanzen",
                "icon": "✅",
                "status": besetzungsgrad_status
            },
            {
                "title": "ATZ-Quote",
                "value": format_percent(filtered_summary["atz_rate"]),
                "subtitle": f"{filtered_summary['atz_count']} Personen in ATZ",
                "icon": "🔄",
                "status": atz_status
            },
            {
                "title": "Durchschnittsalter",
                "value": f"{filtered_summary['avg_age']:.1f}",
                "subtitle": f"Ø {filtered_summary['avg_tenure']:.1f} Jahre Betriebszugehörigkeit",
                "icon": "👥"
            }
        ]

        kpi_row(kpis)

        st.divider()

        # Charts Section
        section_header(
            "Visualisierungen",
            "Detaillierte Analysen der Personalstruktur und -entwicklung",
            "📉"
        )

        # Nur besetzte Stellen für Analysen
        active_df = filtered_df[~filtered_df["Is_Vacant"]]

        # Row 1: Zeitreihe
        if not history_df.empty:
            st.markdown("#### 📈 Kapazitätsentwicklung über Zeit")
            metric_info("Zeitreihenanalyse",
                       "Zeigt die historische Entwicklung der Personalkapazität. Trends und saisonale Muster werden sichtbar.")

            # Filter History nach Datumsbereich
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
                "FTE": "sum",
                "Total_Cost": "sum",
                "Headcount": "sum"
            }).reset_index()

            y_col = "FTE" if view_mode == "MAK" else "Total_Cost"
            fig_timeline = create_line_chart(
                time_series,
                x_col="Date",
                y_col=y_col,
                title=f"{'Kapazität (FTE)' if view_mode == 'MAK' else 'Gesamtkosten (€)'} - Zeitverlauf"
            )
            st.plotly_chart(fig_timeline, use_container_width=True)

        # Row 2: Donut Charts
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### Verteilung nach Geschlecht")
            gender_dist = active_df.groupby("Geschlecht").agg({
                "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year": "sum"
            }).reset_index()
            gender_dist.columns = ["Geschlecht", "Wert"]
            gender_dist["Geschlecht"] = gender_dist["Geschlecht"].map({"m": "Männlich", "w": "Weiblich"})

            fig_gender = create_donut_chart(
                gender_dist,
                values_col="Wert",
                names_col="Geschlecht",
                title=""
            )
            st.plotly_chart(fig_gender, use_container_width=True)

        with col2:
            st.markdown("#### Verteilung nach Arbeitszeit")
            employment_dist = active_df.groupby("Arbeitszeit").agg({
                "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year": "sum"
            }).reset_index()
            employment_dist.columns = ["Arbeitszeit", "Wert"]

            fig_employment = create_donut_chart(
                employment_dist,
                values_col="Wert",
                names_col="Arbeitszeit",
                title=""
            )
            st.plotly_chart(fig_employment, use_container_width=True)

        # Row 3: Top Organisationseinheiten
        st.markdown("#### Top 10 Organisationseinheiten")
        org_agg = active_df.groupby("Organisationseinheit").agg({
            "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year": "sum"
        }).reset_index()
        org_agg.columns = ["Organisation", "Wert"]
        org_agg = org_agg.nlargest(10, "Wert")

        fig_org = create_bar_chart(
            org_agg,
            x_col="Wert",
            y_col="Organisation",
            orientation="h",
            title=""
        )
        st.plotly_chart(fig_org, use_container_width=True)

        # Row 4: Alterskohorten
        st.markdown("#### Verteilung nach Alterskohorte")
        cohort_agg = active_df.groupby("Alterskohorte").agg({
            "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year": "sum"
        }).reset_index()
        cohort_agg.columns = ["Kohorte", "Wert"]

        fig_cohort = create_bar_chart(
            cohort_agg,
            x_col="Kohorte",
            y_col="Wert",
            title=""
        )
        st.plotly_chart(fig_cohort, use_container_width=True)

        # Debug-Info (expandable)
        with st.expander("🔍 Debug: Daten-Übersicht"):
            st.write("**Gefilterte Daten:**")
            st.write(f"Zeilen (gesamt): {len(snapshot_df)}")
            st.write(f"Zeilen (gefiltert): {len(filtered_df)}")
            st.write(f"Aktive Mitarbeitende: {len(active_df)}")

            st.write("**Zusammenfassung:**")
            st.json(filtered_summary)

            st.write("**Filter-Status:**")
            st.json({
                "view_mode": view_mode,
                "selected_org_units": st.session_state.get("selected_org_units", []),
                "selected_cohorts": st.session_state.get("selected_cohorts", []),
            })

    except FileNotFoundError as e:
        st.error(
            "❌ **Testdaten nicht gefunden!**\n\n"
            "Bitte generiere zuerst die Testdaten mit:\n"
            "```\npython data/synthetic.py\n```"
        )
    except Exception as e:
        st.error(f"Fehler beim Laden der Daten: {str(e)}")
        import traceback
        st.code(traceback.format_exc())


def calculate_summary(df: pd.DataFrame) -> dict:
    """Berechnet Zusammenfassungsstatistiken für gefilterten DataFrame."""
    besetzt = df[~df["Is_Vacant"]]

    return {
        "total_planstellen": len(df),
        "total_employees": besetzt["PersNr"].nunique() if "PersNr" in besetzt.columns else len(besetzt),
        "total_fte": df["FTE_assigned"].sum(),
        "total_soll_fte": df["Soll_FTE"].sum(),
        "total_cost": df["Total_Cost_Year"].sum(),
        "vacancy_count": df["Is_Vacant"].sum(),
        "vacancy_rate": df["Is_Vacant"].mean(),
        "besetzungsgrad": 1 - df["Is_Vacant"].mean() if len(df) > 0 else 0,
        "avg_age": besetzt["Alter"].mean() if len(besetzt) > 0 else 0,
        "avg_tenure": besetzt["Betriebszugehörigkeit_Jahre"].mean() if len(besetzt) > 0 else 0,
        "teilzeit_count": (besetzt["Arbeitszeit"] == "Teilzeit").sum() if len(besetzt) > 0 else 0,
        "teilzeit_rate": (besetzt["Arbeitszeit"] == "Teilzeit").mean() if len(besetzt) > 0 else 0,
        "atz_count": (besetzt["ATZ_Status"] != "Kein ATZ").sum() if len(besetzt) > 0 else 0,
        "atz_rate": (besetzt["ATZ_Status"] != "Kein ATZ").mean() if len(besetzt) > 0 else 0,
        "female_count": (besetzt["Geschlecht"] == "w").sum() if len(besetzt) > 0 else 0,
        "female_rate": (besetzt["Geschlecht"] == "w").mean() if len(besetzt) > 0 else 0,
    }


if __name__ == "__main__":
    main()
