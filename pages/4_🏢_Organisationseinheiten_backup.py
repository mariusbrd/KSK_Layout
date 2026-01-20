"""
Organisationseinheiten-Modul für HR Pulse Dashboard.

Zeigt hierarchische Org-Struktur, Soll/Ist-Analysen und Drill-down Details.
"""

import streamlit as st
import pandas as pd
import sys
import os

# Import components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from components.kpi_card import kpi_card
from components.charts import (
    create_sunburst,
    create_waterfall,
    create_diverging_bar,
    create_donut_chart,
    create_bar_chart
)
from config.settings import COLORS, DEFAULT_COHORTS

# Page Config
st.set_page_config(
    page_title="Organisationseinheiten | HR Pulse",
    page_icon="🏢",
    layout="wide"
)

# Load CSS
css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "style.css")
if os.path.exists(css_path):
    with open(css_path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Initialize page-specific session state
if "selected_org_unit" not in st.session_state:
    st.session_state["selected_org_unit"] = None

# Load data
snapshot_df, history_df, org_df, summary = load_and_prepare_data()

# Render global filters
render_global_filters(snapshot_df, history_df)

# Apply filters
filtered_df = apply_filters(snapshot_df)

# Title
st.title("🏢 Organisationseinheiten")

# View Mode aus Session State lesen
view_mode = st.session_state.get("view_mode", "MAK")

# Filter summary
filter_info = get_filter_summary()
st.markdown(f"<div class='filter-summary'>{filter_info}</div>", unsafe_allow_html=True)

st.divider()


def render_breadcrumb_navigation(selected_unit: str = None):
    """Rendert Breadcrumb-Navigation für Drill-down."""
    if selected_unit:
        col1, col2 = st.columns([1, 11])
        with col1:
            if st.button("⬅️ Zurück", key="back_button"):
                st.session_state["selected_org_unit"] = None
                st.rerun()
        with col2:
            org_name = filtered_df[filtered_df["Kürzel OrgEinheit"] == selected_unit]["Organisationseinheit"].iloc[0]
            st.markdown(f"**Alle Einheiten** > **{selected_unit} - {org_name}**")
    else:
        st.markdown("**Alle Einheiten**")


def calculate_org_metrics(df: pd.DataFrame, org_unit: str = None) -> dict:
    """
    Berechnet Kennzahlen für eine Org-Einheit.

    Args:
        df: Gefilterte Daten
        org_unit: Kürzel der Org-Einheit (None = Gesamt)

    Returns:
        Dict mit Kennzahlen
    """
    if org_unit:
        df_org = df[df["Kürzel OrgEinheit"] == org_unit]
    else:
        df_org = df

    # Soll-Kapazität
    soll = df_org["Soll_FTE"].sum()

    # Ist-Kapazität (nur besetzte Stellen)
    ist = df_org[~df_org["Is_Vacant"]]["FTE_assigned"].sum()

    # Varianz
    varianz_absolut = ist - soll
    varianz_relativ = (varianz_absolut / soll * 100) if soll > 0 else 0

    # Besetzungsgrad
    besetzte_stellen = df_org[~df_org["Is_Vacant"]].shape[0]
    alle_stellen = df_org.shape[0]
    besetzungsgrad = (besetzte_stellen / alle_stellen * 100) if alle_stellen > 0 else 0

    # Kosten
    kosten = df_org[~df_org["Is_Vacant"]]["Total_Cost_Year"].sum()

    return {
        "soll": soll,
        "ist": ist,
        "varianz_absolut": varianz_absolut,
        "varianz_relativ": varianz_relativ,
        "besetzungsgrad": besetzungsgrad,
        "kosten": kosten,
        "besetzte_stellen": besetzte_stellen,
        "alle_stellen": alle_stellen
    }


def render_overview_section():
    """Rendert Übersicht aller Org-Einheiten."""
    st.subheader("📊 Gesamtübersicht")

    # Aggregiere Daten pro Org-Einheit
    org_summary = filtered_df.groupby("Kürzel OrgEinheit").agg({
        "Soll_FTE": "sum",
        "FTE_assigned": "sum",
        "Total_Cost_Year": "sum",
        "Planstellennr": "count",
        "Is_Vacant": "sum",
        "Organisationseinheit": "first"
    }).reset_index()

    org_summary.columns = ["Kürzel", "Soll_FTE", "Ist_FTE", "Kosten", "Planstellen", "Vakanzen", "Name"]
    org_summary["Varianz"] = org_summary["Ist_FTE"] - org_summary["Soll_FTE"]
    org_summary["Varianz_%"] = (org_summary["Varianz"] / org_summary["Soll_FTE"] * 100).round(1)
    org_summary["Besetzungsgrad_%"] = ((org_summary["Planstellen"] - org_summary["Vakanzen"]) / org_summary["Planstellen"] * 100).round(1)

    # Sunburst Chart
    view_mode = st.session_state.get("view_mode", "MAK")
    value_col = "Ist_FTE" if view_mode == "MAK" else "Kosten"

    # Bereite Daten für Sunburst vor
    sunburst_df = org_summary.copy()
    sunburst_df["path"] = sunburst_df["Kürzel"].astype(str) + " - " + sunburst_df["Name"]

    fig_sunburst = create_sunburst(
        df=sunburst_df,
        path_cols=["path"],
        value_col=value_col,
        color_col="Besetzungsgrad_%",
        title=f"Org-Hierarchie ({view_mode})",
        height=600
    )

    st.plotly_chart(fig_sunburst, use_container_width=True)

    st.caption("💡 Größe = Kapazität/Kosten | Farbe = Besetzungsgrad | Klick auf Segment für Details")

    # Top/Flop Einheiten
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### 📈 Größte Einheiten")
        top_orgs = org_summary.nlargest(5, value_col)
        fig_top = create_bar_chart(
            df=top_orgs,
            x_col=value_col,
            y_col="Kürzel",
            orientation="h",
            title="",
            height=300
        )
        st.plotly_chart(fig_top, use_container_width=True)

    with col2:
        st.markdown("##### ⚠️ Höchste Unterdeckung")
        flop_orgs = org_summary.nsmallest(5, "Varianz_%")

        # Formatiere Labels
        flop_orgs_display = flop_orgs.copy()
        flop_orgs_display["Label"] = flop_orgs_display["Kürzel"]

        fig_flop = create_diverging_bar(
            df=flop_orgs_display,
            category_col="Label",
            value_col="Varianz_%",
            title="Varianz in %",
            height=300
        )
        st.plotly_chart(fig_flop, use_container_width=True)


def render_unit_detail_section(org_unit: str):
    """
    Rendert Detail-Ansicht einer einzelnen Org-Einheit.

    Args:
        org_unit: Kürzel der Org-Einheit
    """
    metrics = calculate_org_metrics(filtered_df, org_unit)
    view_mode = st.session_state.get("view_mode", "MAK")

    # Org-Name
    org_name = filtered_df[filtered_df["Kürzel OrgEinheit"] == org_unit]["Organisationseinheit"].iloc[0]
    st.subheader(f"📋 {org_unit} - {org_name}")

    # KPI Row
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if view_mode == "MAK":
            kpi_card(
                title="Soll-Kapazität",
                value=f"{metrics['soll']:.1f}",
                subtitle="FTE",
                icon="📊"
            )
        else:
            kpi_card(
                title="Soll-Kosten",
                value=f"{metrics['kosten']:,.0f} €",
                subtitle="Hochrechnung",
                icon="💶"
            )

    with col2:
        if view_mode == "MAK":
            kpi_card(
                title="Ist-Kapazität",
                value=f"{metrics['ist']:.1f}",
                subtitle="FTE",
                icon="✅"
            )
        else:
            kpi_card(
                title="Ist-Kosten",
                value=f"{metrics['kosten']:,.0f} €",
                subtitle="p.a.",
                icon="💰"
            )

    with col3:
        status = "critical" if metrics["varianz_absolut"] < -5 else ("warning" if metrics["varianz_absolut"] < 0 else "good")
        kpi_card(
            title="Varianz",
            value=f"{metrics['varianz_absolut']:+.1f}",
            subtitle=f"{metrics['varianz_relativ']:+.1f}%",
            icon="📉" if metrics["varianz_absolut"] < 0 else "📈",
            status=status
        )

    with col4:
        status = "good" if metrics["besetzungsgrad"] >= 85 else ("warning" if metrics["besetzungsgrad"] >= 70 else "critical")
        kpi_card(
            title="Besetzungsgrad",
            value=f"{metrics['besetzungsgrad']:.1f}%",
            subtitle=f"{metrics['besetzte_stellen']}/{metrics['alle_stellen']} Stellen",
            icon="🎯",
            status=status
        )

    st.divider()

    # Waterfall & Diverging Bar
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 🌊 Soll → Ist Überleitung")

        # Berechne Waterfall-Daten
        vakanzen_fte = metrics["soll"] - metrics["ist"]

        waterfall_categories = ["Soll", "Vakanzen", "Ist"]
        waterfall_values = [metrics["soll"], -vakanzen_fte, metrics["ist"]]

        fig_waterfall = create_waterfall(
            categories=waterfall_categories,
            values=waterfall_values,
            title="",
            height=400
        )
        st.plotly_chart(fig_waterfall, use_container_width=True)

    with col2:
        st.markdown("#### 📊 Verteilung nach Geschlecht")

        df_org = filtered_df[filtered_df["Kürzel OrgEinheit"] == org_unit]
        df_org_active = df_org[~df_org["Is_Vacant"]]

        if not df_org_active.empty:
            gender_dist = df_org_active.groupby("Geschlecht").agg({
                "FTE_assigned": "sum" if view_mode == "MAK" else "count",
                "Total_Cost_Year": "sum"
            }).reset_index()

            value_col = "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year"

            fig_gender = create_donut_chart(
                df=gender_dist,
                values_col=value_col,
                names_col="Geschlecht",
                title="",
                height=400
            )
            st.plotly_chart(fig_gender, use_container_width=True)
        else:
            st.info("Keine besetzten Stellen vorhanden")

    st.divider()

    # Detail-Tabelle
    st.markdown("#### 📋 Planstellen-Detail")

    df_org = filtered_df[filtered_df["Kürzel OrgEinheit"] == org_unit].copy()

    # Formatiere Tabelle
    display_cols = [
        "Planstellennr",
        "Planstelle",
        "Soll_FTE",
        "FTE_assigned",
        "Bewertung Tarifgruppe",
        "Geschlecht",
        "BsGrd",
        "Total_Cost_Year",
        "Is_Vacant"
    ]

    df_display = df_org[display_cols].copy()
    df_display.columns = [
        "Planstelle Nr.",
        "Bezeichnung",
        "Soll FTE",
        "Ist FTE",
        "Tarifgruppe",
        "Geschlecht",
        "Beschäftigungsgrad %",
        "Kosten p.a. €",
        "Vakant"
    ]

    # Formatiere Werte
    df_display["Soll FTE"] = df_display["Soll FTE"].round(2)
    df_display["Ist FTE"] = df_display["Ist FTE"].round(2)
    df_display["Kosten p.a. €"] = df_display["Kosten p.a. €"].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "")
    df_display["Vakant"] = df_display["Vakant"].map({True: "✓", False: ""})
    df_display["Geschlecht"] = df_display["Geschlecht"].fillna("")

    st.dataframe(
        df_display,
        use_container_width=True,
        height=400,
        hide_index=True
    )

    # Export
    csv = df_display.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Tabelle als CSV exportieren",
        data=csv,
        file_name=f"org_einheit_{org_unit}_detail.csv",
        mime="text/csv"
    )


# Main Content
render_breadcrumb_navigation(st.session_state.get("selected_org_unit"))

st.divider()

if st.session_state.get("selected_org_unit"):
    # Detail-Ansicht
    render_unit_detail_section(st.session_state["selected_org_unit"])
else:
    # Übersicht
    render_overview_section()

    # Unit-Auswahl für Drill-down
    st.divider()
    st.markdown("### 🔍 Einheit für Detailanalyse auswählen")

    org_units = sorted(filtered_df["Kürzel OrgEinheit"].unique())
    org_unit_names = filtered_df[["Kürzel OrgEinheit", "Organisationseinheit"]].drop_duplicates()
    org_unit_options = {
        row["Kürzel OrgEinheit"]: f"{row['Kürzel OrgEinheit']} - {row['Organisationseinheit']}"
        for _, row in org_unit_names.iterrows()
    }

    selected = st.selectbox(
        "Organisationseinheit wählen",
        options=org_units,
        format_func=lambda x: org_unit_options.get(x, x),
        key="org_unit_selector"
    )

    if st.button("📊 Details anzeigen", key="show_details"):
        st.session_state["selected_org_unit"] = selected
        st.rerun()
