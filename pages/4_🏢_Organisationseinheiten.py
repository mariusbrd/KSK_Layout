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
from dataloader.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from components.kpi_card import kpi_card
from components.charts import (
    create_sunburst,
    create_waterfall,
    create_diverging_bar,
    create_donut_chart,
    create_bar_chart
)
from config.settings import COLORS, DEFAULT_COHORTS, format_currency, format_number
from dataloader.kpi_engine import (
    get_unique_employees, compute_headcount, compute_fte_effektiv,
    compute_atz_kpis, compute_teilzeit_kpis, compute_alter_kpis,
    compute_planstellen_kpis
)
import plotly.graph_objects as go
import numpy as np

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
    Berechnet erweiterte Kennzahlen für eine Org-Einheit.

    WICHTIG: Alle Metriken werden sowohl für MAK- als auch EUR-Ansicht berechnet.
    Metriken mit dualem Charakter haben zwei Varianten:
    - vollzeit_potential_fte: Kapazitätsgewinn in FTE (MAK)
    - vollzeit_potential_kosten: Kosteneinsparung bei Vollzeit in EUR (EUR)

    Args:
        df: Gefilterte Daten
        org_unit: Kürzel der Org-Einheit (None = Gesamt)

    Returns:
        Dict mit 30 Kennzahlen (Basis, Demografie, Teilzeit, ATZ, Kosten, Stabilität)
        - Ansichts-neutrale Metriken: Alter, Quoten, Prozente
        - Duale Metriken: FTE und EUR Varianten wo relevant
    """
    if org_unit:
        df_org = df[df["Kürzel OrgEinheit"] == org_unit]
    else:
        df_org = df

    # Nur aktive Mitarbeitende (keine Vakanzen)
    df_active = df_org[~df_org["Is_Vacant"]]

    # === Unique Mitarbeitende via kpi_engine ===
    emp = get_unique_employees(df_org)
    headcount = len(emp)

    # === BASIS-KENNZAHLEN (kpi_engine) ===
    soll = df_org["Soll_FTE"].sum()
    ist = compute_fte_effektiv(df_org)  # MAK (Readme-konform)
    varianz_absolut = ist - soll
    varianz_relativ = (varianz_absolut / soll * 100) if soll > 0 else 0
    plan_kpis = compute_planstellen_kpis(df_org)
    besetzte_stellen = plan_kpis["besetzt"]
    alle_stellen = plan_kpis["total"]
    besetzungsgrad = plan_kpis["besetzungsquote"]
    kosten = df_active["Total_Cost_Year"].sum() if "Total_Cost_Year" in df_active.columns else 0

    # Kosten für Soll-Kapazität (theoretisch bei voller Besetzung)
    kosten_soll = df_org["Total_Cost_Year"].sum() if "Total_Cost_Year" in df_org.columns else 0

    # === DEMOGRAFISCHE KENNZAHLEN (kpi_engine, unique Köpfe) ===
    if headcount > 0:
        alter_col = "Alter_Jahre" if "Alter_Jahre" in emp.columns else "Alter"
        durchschnittsalter = emp[alter_col].mean()
        anteil_55_plus = (emp[alter_col] >= 55).sum() / headcount * 100
        anteil_retirement_ready = (emp[alter_col] >= 63).sum() / headcount * 100
        risk_score = anteil_55_plus * 0.6 + anteil_retirement_ready * 0.4
    else:
        durchschnittsalter = 0
        anteil_55_plus = 0
        anteil_retirement_ready = 0
        risk_score = 0

    # === TEILZEIT-KENNZAHLEN (kpi_engine, unique Köpfe) ===
    if headcount > 0:
        tz = compute_teilzeit_kpis(df_org)
        teilzeitquote = tz["quote_pct"]
        durchschnitt_beschaeftigungsgrad = emp["BsGrd"].mean() if "BsGrd" in emp.columns else 0

        # MAK: FTE-Potential
        vollzeit_potential_fte = (df_active["Soll_FTE"].sum() - df_active["FTE_person"].sum()) if "Soll_FTE" in df_active.columns else 0

        # EUR: Kosteneinsparung-Potential bei Vollzeit
        avg_cost_per_fte = kosten / ist if ist > 0 else 0
        vollzeit_potential_kosten = vollzeit_potential_fte * avg_cost_per_fte
    else:
        teilzeitquote = 0
        durchschnitt_beschaeftigungsgrad = 0
        vollzeit_potential_fte = 0
        vollzeit_potential_kosten = 0

    # === ATZ-KENNZAHLEN (kpi_engine, unique Köpfe) ===
    atz = compute_atz_kpis(df_org)
    atz_quote = atz["quote_headcount_pct"]
    atz_arbeitsphase = atz["arbeitsphase"]
    atz_freistellung = atz["freistellung"]

    # MAK: FTE in ATZ (Planstellen-Summe für ATZ-Personen)
    atz_mask = df_active["ATZ_Status"].isin(["Arbeitsphase", "Freistellungsphase"]) if "ATZ_Status" in df_active.columns else pd.Series(False, index=df_active.index)
    atz_fte = df_active.loc[atz_mask, "FTE_assigned"].sum() if "FTE_assigned" in df_active.columns else 0
    atz_kosten = df_active.loc[atz_mask, "Total_Cost_Year"].sum() if "Total_Cost_Year" in df_active.columns else 0

    # === KOSTEN-EFFIZIENZ ===
    kosten_pro_fte = (kosten / ist) if ist > 0 else 0
    kosten_pro_headcount = (kosten / headcount) if headcount > 0 else 0

    # Vakanz-Kosten: Entgangene Produktivität (hypothetisch)
    vakanz_count = plan_kpis["vakanzen"]
    vakanz_fte = df_org[df_org["Is_Vacant"]]["Soll_FTE"].sum() if "Soll_FTE" in df_org.columns else 0
    vakanz_kosten_potential = vakanz_fte * kosten_pro_fte if kosten_pro_fte > 0 else 0

    # === STABILITÄT (unique Köpfe) ===
    if headcount > 0:
        durchschnitt_betriebszugehoerigkeit = emp["Betriebszugehörigkeit_Jahre"].mean() if "Betriebszugehörigkeit_Jahre" in emp.columns else 0
        anteil_ueber_5_jahre = (emp["Betriebszugehörigkeit_Jahre"] >= 5).sum() / headcount * 100 if "Betriebszugehörigkeit_Jahre" in emp.columns else 0
    else:
        durchschnitt_betriebszugehoerigkeit = 0
        anteil_ueber_5_jahre = 0

    return {
        # === BASIS (8 Metriken) ===
        "soll": soll,                           # FTE Soll-Kapazität
        "ist": ist,                             # FTE Ist-Kapazität
        "varianz_absolut": varianz_absolut,     # FTE Differenz
        "varianz_relativ": varianz_relativ,     # % Differenz
        "besetzungsgrad": besetzungsgrad,       # % besetzte Stellen
        "kosten": kosten,                       # EUR Ist-Kosten
        "kosten_soll": kosten_soll,             # EUR Soll-Kosten (bei voller Besetzung)
        "besetzte_stellen": besetzte_stellen,   # Anzahl Köpfe
        "alle_stellen": alle_stellen,           # Anzahl Planstellen

        # === DEMOGRAFIE (4 Metriken - Ansichts-neutral) ===
        "durchschnittsalter": durchschnittsalter,
        "anteil_55_plus": anteil_55_plus,
        "anteil_retirement_ready": anteil_retirement_ready,
        "risk_score": risk_score,

        # === TEILZEIT (5 Metriken - 2 neutral + 2 dual) ===
        "teilzeitquote": teilzeitquote,                           # % (neutral)
        "durchschnitt_beschaeftigungsgrad": durchschnitt_beschaeftigungsgrad,  # % (neutral)
        "vollzeit_potential_fte": vollzeit_potential_fte,         # FTE (MAK)
        "vollzeit_potential_kosten": vollzeit_potential_kosten,   # EUR (EUR)

        # === ATZ (5 Metriken - 3 neutral + 2 dual) ===
        "atz_quote": atz_quote,                 # % (neutral)
        "atz_arbeitsphase": atz_arbeitsphase,   # Anzahl (neutral)
        "atz_freistellung": atz_freistellung,   # Anzahl (neutral)
        "atz_fte": atz_fte,                     # FTE (MAK)
        "atz_kosten": atz_kosten,               # EUR (EUR)

        # === KOSTEN-EFFIZIENZ (5 Metriken - primär EUR) ===
        "kosten_pro_fte": kosten_pro_fte,               # EUR/FTE
        "kosten_pro_headcount": kosten_pro_headcount,   # EUR/Kopf
        "vakanz_count": vakanz_count,                   # Anzahl
        "vakanz_fte": vakanz_fte,                       # FTE
        "vakanz_kosten_potential": vakanz_kosten_potential,  # EUR

        # === STABILITÄT (2 Metriken - Ansichts-neutral) ===
        "durchschnitt_betriebszugehoerigkeit": durchschnitt_betriebszugehoerigkeit,
        "anteil_ueber_5_jahre": anteil_ueber_5_jahre
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


# Main Content - Tab Navigation
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📊 Übersicht",
    "👥 Demografie",
    "🎓 Qualifikation",
    "📈 Trends",
    "💰 Effizienz",
    "⏰ Arbeitszeit & ATZ",
    "🎯 Benchmarking"
])

with tab1:
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

with tab2:
    st.subheader("👥 Demografische Risiko-Analyse")
    st.markdown("Analyse der Altersstruktur und demografischen Risiken pro Organisationseinheit")

    # Berechne Metriken für alle Org-Einheiten
    org_units_list = sorted(filtered_df["Kürzel OrgEinheit"].unique())

    # Sammle Metriken für alle Einheiten
    demo_data = []
    for org in org_units_list:
        metrics = calculate_org_metrics(filtered_df, org)
        org_name = filtered_df[filtered_df["Kürzel OrgEinheit"] == org]["Organisationseinheit"].iloc[0]
        demo_data.append({
            "Kürzel": org,
            "Name": org_name,
            "Durchschnittsalter": metrics["durchschnittsalter"],
            "Anteil_55+": metrics["anteil_55_plus"],
            "Anteil_63+": metrics["anteil_retirement_ready"],
            "Risk_Score": metrics["risk_score"],
            "Betriebszugehörigkeit": metrics["durchschnitt_betriebszugehoerigkeit"],
            "Headcount": metrics["besetzte_stellen"]
        })

    demo_df = pd.DataFrame(demo_data)

    # === KPI-ROW ===
    st.markdown("#### 📊 Gesamt-Kennzahlen")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        avg_age = demo_df["Durchschnittsalter"].mean()
        kpi_card(
            title="⌀ Alter",
            value=f"{avg_age:.1f} Jahre",
            subtitle="Durchschnitt über alle Einheiten"
        )

    with col2:
        avg_55_plus = demo_df["Anteil_55+"].mean()
        kpi_card(
            title="Anteil 55+",
            value=f"{avg_55_plus:.1f}%",
            subtitle="Durchschnittlicher Anteil"
        )

    with col3:
        avg_risk = demo_df["Risk_Score"].mean()
        status = "critical" if avg_risk > 35 else "warning" if avg_risk > 25 else "good"
        kpi_card(
            title="Risk Score",
            value=f"{avg_risk:.1f}",
            subtitle="Gewichteter Alterungsindex",
            status=status
        )

    with col4:
        avg_tenure = demo_df["Betriebszugehörigkeit"].mean()
        kpi_card(
            title="⌀ Betriebszugehörigkeit",
            value=f"{avg_tenure:.1f} Jahre",
            subtitle="Durchschnitt über alle Einheiten"
        )

    st.divider()

    # === HEATMAP: ORG-EINHEITEN × ALTERSKOHORTEN ===
    st.markdown("#### 🔥 Altersverteilung nach Org-Einheiten")

    # Bereite Daten für Heatmap vor
    cohort_definitions = st.session_state.get("cohort_definitions", DEFAULT_COHORTS)

    # Berechne Alterskohortenverteilung für jede Org-Einheit
    heatmap_data = []
    for org in org_units_list[:15]:  # Limit auf Top 15 für Lesbarkeit
        df_org = filtered_df[
            (filtered_df["Kürzel OrgEinheit"] == org) &
            (~filtered_df["Is_Vacant"])
        ]

        if len(df_org) == 0:
            continue

        org_name = df_org["Organisationseinheit"].iloc[0]
        row_data = {"Org-Einheit": f"{org} - {org_name[:20]}"}

        for cohort_name, (min_age, max_age) in cohort_definitions.items():
            count = ((df_org["Alter"] >= min_age) & (df_org["Alter"] <= max_age)).sum()
            percentage = (count / len(df_org) * 100) if len(df_org) > 0 else 0
            row_data[cohort_name] = percentage

        heatmap_data.append(row_data)

    heatmap_df = pd.DataFrame(heatmap_data)

    if len(heatmap_df) > 0:
        # Erstelle Heatmap
        cohort_cols = [col for col in heatmap_df.columns if col != "Org-Einheit"]
        z_values = heatmap_df[cohort_cols].values

        fig_heatmap = go.Figure(data=go.Heatmap(
            z=z_values,
            x=cohort_cols,
            y=heatmap_df["Org-Einheit"],
            colorscale=[
                [0, COLORS["status_good"]],
                [0.5, COLORS["accent_amber"]],
                [1, COLORS["status_critical"]]
            ],
            text=np.round(z_values, 1),
            texttemplate='%{text}%',
            textfont={"size": 10},
            colorbar=dict(title="Anteil %"),
            hovertemplate='<b>%{y}</b><br>%{x}: %{z:.1f}%<extra></extra>'
        ))

        fig_heatmap.update_layout(
            title="",
            height=max(400, len(heatmap_df) * 30),
            xaxis_title="Alterskohorte",
            yaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            margin=dict(l=200, r=20, t=20, b=80)
        )

        st.plotly_chart(fig_heatmap, use_container_width=True)
        st.caption("💡 Farbskala: Grün = niedriger Anteil, Rot = hoher Anteil | Zeigt Top 15 Einheiten")

    st.divider()

    # === RANKINGS ===
    st.markdown("#### 🏆 Rankings")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### 🔴 Höchstes Demografie-Risiko (Top 10)")

        top_risk = demo_df.nlargest(10, "Risk_Score")[["Kürzel", "Name", "Risk_Score", "Anteil_55+", "Durchschnittsalter"]]

        # Bar Chart
        fig_risk = go.Figure()

        fig_risk.add_trace(go.Bar(
            y=top_risk["Kürzel"],
            x=top_risk["Risk_Score"],
            orientation='h',
            marker=dict(
                color=top_risk["Risk_Score"],
                colorscale=[
                    [0, COLORS["status_good"]],
                    [0.5, COLORS["accent_amber"]],
                    [1, COLORS["status_critical"]]
                ],
                showscale=False
            ),
            text=top_risk["Risk_Score"].round(1),
            textposition='auto',
            hovertemplate='<b>%{y}</b><br>Risk Score: %{x:.1f}<extra></extra>'
        ))

        fig_risk.update_layout(
            height=350,
            xaxis_title="Risk Score",
            yaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            margin=dict(l=80, r=20, t=20, b=60),
            yaxis=dict(autorange="reversed")
        )

        st.plotly_chart(fig_risk, use_container_width=True)
        st.caption(f"💡 Risk Score = Anteil 55+ × 0.6 + Anteil 63+ × 0.4")

    with col2:
        st.markdown("##### 🟢 Jüngste Org-Einheiten (Top 10)")

        youngest = demo_df.nsmallest(10, "Durchschnittsalter")[["Kürzel", "Name", "Durchschnittsalter", "Anteil_55+"]]

        # Bar Chart
        fig_young = go.Figure()

        fig_young.add_trace(go.Bar(
            y=youngest["Kürzel"],
            x=youngest["Durchschnittsalter"],
            orientation='h',
            marker=dict(
                color=youngest["Durchschnittsalter"],
                colorscale=[
                    [0, COLORS["status_good"]],
                    [0.5, COLORS["accent_amber"]],
                    [1, COLORS["status_critical"]]
                ],
                showscale=False,
                reversescale=True
            ),
            text=youngest["Durchschnittsalter"].round(1),
            textposition='auto',
            hovertemplate='<b>%{y}</b><br>⌀ Alter: %{x:.1f} Jahre<extra></extra>'
        ))

        fig_young.update_layout(
            height=350,
            xaxis_title="Durchschnittsalter (Jahre)",
            yaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            margin=dict(l=80, r=20, t=20, b=60),
            yaxis=dict(autorange="reversed")
        )

        st.plotly_chart(fig_young, use_container_width=True)
        st.caption("💡 Niedrigeres Durchschnittsalter = geringeres demografisches Risiko")

    st.divider()

    # === DETAILTABELLE ===
    st.markdown("#### 📋 Detaillierte Übersicht")

    display_df = demo_df.copy()
    display_df["Durchschnittsalter"] = display_df["Durchschnittsalter"].round(1)
    display_df["Anteil_55+"] = display_df["Anteil_55+"].round(1).astype(str) + "%"
    display_df["Anteil_63+"] = display_df["Anteil_63+"].round(1).astype(str) + "%"
    display_df["Risk_Score"] = display_df["Risk_Score"].round(1)
    display_df["Betriebszugehörigkeit"] = display_df["Betriebszugehörigkeit"].round(1)

    display_df = display_df.sort_values("Risk_Score", ascending=False)

    st.dataframe(
        display_df,
        use_container_width=True,
        height=400,
        hide_index=True,
        column_config={
            "Kürzel": st.column_config.TextColumn("Kürzel", width="small"),
            "Name": st.column_config.TextColumn("Organisationseinheit", width="large"),
            "Durchschnittsalter": st.column_config.NumberColumn("⌀ Alter", format="%.1f Jahre"),
            "Anteil_55+": st.column_config.TextColumn("Anteil 55+"),
            "Anteil_63+": st.column_config.TextColumn("Anteil 63+"),
            "Risk_Score": st.column_config.NumberColumn("Risk Score", format="%.1f"),
            "Betriebszugehörigkeit": st.column_config.NumberColumn("⌀ Zugehörigkeit", format="%.1f Jahre"),
            "Headcount": st.column_config.NumberColumn("Headcount", format="%d")
        }
    )

    # Export
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Demografische Analyse als CSV exportieren",
        data=csv,
        file_name="org_einheiten_demografie.csv",
        mime="text/csv"
    )

with tab3:
    st.subheader("🎓 Qualifikations-Portfolio")
    st.markdown("Analyse der Qualifikationsstruktur und des Kompetenz-Niveaus pro Organisationseinheit")

    # Berechne Qualifikationsdaten für alle Org-Einheiten
    org_units_list = sorted(filtered_df["Kürzel OrgEinheit"].unique())

    # === HEATMAP: ORG-EINHEITEN × QUALIFIKATIONEN ===
    st.markdown("#### 🎯 Qualifikationsverteilung nach Org-Einheiten")

    # Top 8 Qualifikationen ermitteln
    top_qualifications = filtered_df[~filtered_df["Is_Vacant"]]["Ausbildung"].value_counts().head(8).index.tolist()

    # Bereite Daten für Heatmap vor
    qual_heatmap_data = []
    for org in org_units_list[:15]:  # Limit auf Top 15
        df_org = filtered_df[
            (filtered_df["Kürzel OrgEinheit"] == org) &
            (~filtered_df["Is_Vacant"])
        ]

        if len(df_org) == 0:
            continue

        org_name = df_org["Organisationseinheit"].iloc[0]
        row_data = {"Org-Einheit": f"{org} - {org_name[:20]}"}

        for qual in top_qualifications:
            count = (df_org["Ausbildung"] == qual).sum()
            percentage = (count / len(df_org) * 100) if len(df_org) > 0 else 0
            row_data[qual] = percentage

        qual_heatmap_data.append(row_data)

    qual_heatmap_df = pd.DataFrame(qual_heatmap_data)

    if len(qual_heatmap_df) > 0:
        # Erstelle Heatmap
        qual_cols = [col for col in qual_heatmap_df.columns if col != "Org-Einheit"]
        z_values = qual_heatmap_df[qual_cols].values

        fig_qual_heatmap = go.Figure(data=go.Heatmap(
            z=z_values,
            x=qual_cols,
            y=qual_heatmap_df["Org-Einheit"],
            colorscale=[
                [0, "#F5F5F5"],
                [0.5, COLORS["accent_blue"]],
                [1, COLORS["accent_blue"]]
            ],
            text=np.round(z_values, 1),
            texttemplate='%{text}%',
            textfont={"size": 9},
            colorbar=dict(title="Anteil %"),
            hovertemplate='<b>%{y}</b><br>%{x}: %{z:.1f}%<extra></extra>'
        ))

        fig_qual_heatmap.update_layout(
            title="",
            height=max(400, len(qual_heatmap_df) * 30),
            xaxis_title="Qualifikation",
            yaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            margin=dict(l=200, r=20, t=20, b=120),
            xaxis=dict(tickangle=-45)
        )

        st.plotly_chart(fig_qual_heatmap, use_container_width=True)
        st.caption("💡 Zeigt Top 8 Qualifikationen für Top 15 Org-Einheiten")

    st.divider()

    # === RANKINGS ===
    st.markdown("#### 🏆 Qualifikations-Rankings")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### 🥇 Höchstes Qualifikationsniveau (Top 10)")

        # Berechne durchschnittliche Tarifgruppe als Proxy für Qualifikationsniveau
        from config.settings import TARIFF_GROUPS

        tariff_mapping = {tg: idx for idx, tg in enumerate(TARIFF_GROUPS)}

        qual_level_data = []
        for org in org_units_list:
            df_org = filtered_df[
                (filtered_df["Kürzel OrgEinheit"] == org) &
                (~filtered_df["Is_Vacant"])
            ]

            if len(df_org) == 0:
                continue

            org_name = df_org["Organisationseinheit"].iloc[0]

            # Berechne durchschnittliche Tarifgruppe
            df_org_tariff = df_org[df_org["TrfGr"].notna()]
            if len(df_org_tariff) > 0:
                avg_tariff_numeric = df_org_tariff["TrfGr"].map(tariff_mapping).mean()
                avg_tariff_group = df_org_tariff["TrfGr"].mode()[0] if len(df_org_tariff["TrfGr"].mode()) > 0 else "N/A"
            else:
                avg_tariff_numeric = 0
                avg_tariff_group = "N/A"

            qual_level_data.append({
                "Kürzel": org,
                "Name": org_name,
                "Avg_Tariff_Numeric": avg_tariff_numeric,
                "Avg_Tariff_Group": avg_tariff_group,
                "Headcount": len(df_org)
            })

        qual_level_df = pd.DataFrame(qual_level_data)
        top_qual = qual_level_df.nlargest(10, "Avg_Tariff_Numeric")

        # Bar Chart
        fig_qual_level = go.Figure()

        fig_qual_level.add_trace(go.Bar(
            y=top_qual["Kürzel"],
            x=top_qual["Avg_Tariff_Numeric"],
            orientation='h',
            marker=dict(
                color=COLORS["accent_blue"],
                opacity=0.8
            ),
            text=top_qual["Avg_Tariff_Group"],
            textposition='auto',
            hovertemplate='<b>%{y}</b><br>⌀ Tarifgruppe: %{text}<extra></extra>'
        ))

        fig_qual_level.update_layout(
            height=350,
            xaxis_title="Qualifikations-Index",
            yaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            margin=dict(l=80, r=20, t=20, b=60),
            yaxis=dict(autorange="reversed")
        )

        st.plotly_chart(fig_qual_level, use_container_width=True)
        st.caption("💡 Basiert auf durchschnittlicher Tarifgruppe der Mitarbeitenden")

    with col2:
        st.markdown("##### 📚 Qualifikations-Mix Top 5 Einheiten")

        # Top 5 Einheiten nach Größe
        top_5_orgs = filtered_df[~filtered_df["Is_Vacant"]].groupby("Kürzel OrgEinheit").size().nlargest(5).index.tolist()

        # Erstelle Stacked Bar für Top 5
        qual_mix_data = []
        for org in top_5_orgs:
            df_org = filtered_df[
                (filtered_df["Kürzel OrgEinheit"] == org) &
                (~filtered_df["Is_Vacant"])
            ]

            qual_counts = df_org["Ausbildung"].value_counts().head(5)
            total = len(df_org)

            for qual, count in qual_counts.items():
                qual_mix_data.append({
                    "Org": org,
                    "Qualifikation": qual,
                    "Anzahl": count,
                    "Prozent": (count / total * 100) if total > 0 else 0
                })

        qual_mix_df = pd.DataFrame(qual_mix_data)

        if len(qual_mix_df) > 0:
            fig_qual_mix = go.Figure()

            qualifications_unique = qual_mix_df["Qualifikation"].unique()

            for i, qual in enumerate(qualifications_unique):
                df_qual = qual_mix_df[qual_mix_df["Qualifikation"] == qual]

                fig_qual_mix.add_trace(go.Bar(
                    name=qual,
                    y=df_qual["Org"],
                    x=df_qual["Prozent"],
                    orientation='h',
                    marker=dict(color=COLORS["accent_blue"] if i == 0 else None),
                    hovertemplate='<b>%{y}</b><br>' + qual + ': %{x:.1f}%<extra></extra>'
                ))

            fig_qual_mix.update_layout(
                barmode='stack',
                height=350,
                xaxis_title="Anteil (%)",
                yaxis_title="",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color=COLORS["text_primary"]),
                margin=dict(l=80, r=20, t=20, b=60),
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.2,
                    xanchor="center",
                    x=0.5,
                    bgcolor="#FFFFFF",
                    bordercolor=COLORS["card_border"],
                    borderwidth=1
                )
            )

            st.plotly_chart(fig_qual_mix, use_container_width=True)
            st.caption("💡 Zeigt Top 5 Qualifikationen pro Org-Einheit (nur Top 5 Einheiten)")

    st.divider()

    # === DETAILTABELLE ===
    st.markdown("#### 📋 Qualifikations-Übersicht")

    # Erstelle Übersichtstabelle
    qual_summary_data = []
    for org in org_units_list:
        df_org = filtered_df[
            (filtered_df["Kürzel OrgEinheit"] == org) &
            (~filtered_df["Is_Vacant"])
        ]

        if len(df_org) == 0:
            continue

        org_name = df_org["Organisationseinheit"].iloc[0]

        # Top Qualifikation
        top_qual = df_org["Ausbildung"].mode()[0] if len(df_org["Ausbildung"].mode()) > 0 else "N/A"
        top_qual_count = (df_org["Ausbildung"] == top_qual).sum()
        top_qual_pct = (top_qual_count / len(df_org) * 100) if len(df_org) > 0 else 0

        # Durchschnittliche Tarifgruppe
        df_org_tariff = df_org[df_org["TrfGr"].notna()]
        avg_tariff = df_org_tariff["TrfGr"].mode()[0] if len(df_org_tariff["TrfGr"].mode()) > 0 else "N/A"

        # Anzahl verschiedener Qualifikationen
        unique_quals = df_org["Ausbildung"].nunique()

        qual_summary_data.append({
            "Kürzel": org,
            "Name": org_name,
            "Headcount": len(df_org),
            "Top_Qualifikation": top_qual,
            "Top_Qual_Anteil": top_qual_pct,
            "Ø_Tarifgruppe": avg_tariff,
            "Qualifikations_Vielfalt": unique_quals
        })

    qual_summary_df = pd.DataFrame(qual_summary_data)

    display_qual_df = qual_summary_df.copy()
    display_qual_df["Top_Qual_Anteil"] = display_qual_df["Top_Qual_Anteil"].round(1).astype(str) + "%"

    display_qual_df = display_qual_df.sort_values("Headcount", ascending=False)

    st.dataframe(
        display_qual_df,
        use_container_width=True,
        height=400,
        hide_index=True,
        column_config={
            "Kürzel": st.column_config.TextColumn("Kürzel", width="small"),
            "Name": st.column_config.TextColumn("Organisationseinheit", width="large"),
            "Headcount": st.column_config.NumberColumn("Headcount", format="%d"),
            "Top_Qualifikation": st.column_config.TextColumn("Häufigste Qualifikation", width="medium"),
            "Top_Qual_Anteil": st.column_config.TextColumn("Anteil Top Qual."),
            "Ø_Tarifgruppe": st.column_config.TextColumn("⌀ Tarifgruppe"),
            "Qualifikations_Vielfalt": st.column_config.NumberColumn("Anz. Qualifikationen", format="%d")
        }
    )

    # Export
    csv = display_qual_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Qualifikations-Analyse als CSV exportieren",
        data=csv,
        file_name="org_einheiten_qualifikationen.csv",
        mime="text/csv"
    )

with tab4:
    st.subheader("📈 Kapazitäts-Trends")
    st.markdown("Entwicklung von FTE, Headcount und Vakanzen über die letzten 24 Monate")

    # Prüfe ob History-Daten verfügbar sind
    if history_df is None or len(history_df) == 0:
        st.warning("⚠️ Keine historischen Daten verfügbar. Diese Analyse benötigt History-Daten.")
    else:
        # Letzte 24 Monate
        latest_date = history_df["Date"].max()
        start_date = latest_date - pd.DateOffset(months=24)
        history_filtered = history_df[history_df["Date"] >= start_date].copy()

        # View Mode
        view_mode = st.session_state.get("view_mode", "MAK")

        # === ORG-EINHEIT AUSWAHL ===
        st.markdown("#### 🎯 Org-Einheiten auswählen")

        org_units_list = sorted(history_filtered["Kürzel OrgEinheit"].unique())

        # Top 10 Einheiten nach aktueller Größe vorauswählen
        latest_snapshot = history_filtered[history_filtered["Date"] == latest_date]
        if view_mode == "MAK":
            top_10_orgs = latest_snapshot.nlargest(10, "FTE")["Kürzel OrgEinheit"].tolist()
        else:
            top_10_orgs = latest_snapshot.nlargest(10, "Total_Cost")["Kürzel OrgEinheit"].tolist()

        selected_orgs = st.multiselect(
            "Wähle Org-Einheiten für Vergleich (max. 10)",
            options=org_units_list,
            default=top_10_orgs[:10],
            max_selections=10
        )

        if len(selected_orgs) == 0:
            st.info("👆 Bitte wähle mindestens eine Org-Einheit aus")
        else:
            history_selected = history_filtered[history_filtered["Kürzel OrgEinheit"].isin(selected_orgs)]

            st.divider()

            # === LINE CHARTS ===
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("##### 📊 FTE / Kosten-Entwicklung")

                fig_capacity = go.Figure()

                for org in selected_orgs:
                    df_org_history = history_selected[history_selected["Kürzel OrgEinheit"] == org].sort_values("Date")

                    if view_mode == "MAK":
                        y_values = df_org_history["FTE"]
                        y_label = "FTE"
                    else:
                        y_values = df_org_history["Total_Cost"] / 1000  # In Tausend
                        y_label = "Kosten (T€)"

                    fig_capacity.add_trace(go.Scatter(
                        x=df_org_history["Date"],
                        y=y_values,
                        mode='lines+markers',
                        name=str(org),
                        hovertemplate='<b>%{fullData.name}</b><br>%{x|%b %Y}<br>' + y_label + ': %{y:.1f}<extra></extra>'
                    ))

                fig_capacity.update_layout(
                    height=400,
                    xaxis_title="Monat",
                    yaxis_title=y_label,
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=COLORS["text_primary"]),
                    hovermode="x unified",
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.2,
                        xanchor="center",
                        x=0.5,
                        bgcolor="#FFFFFF",
                        bordercolor=COLORS["card_border"],
                        borderwidth=1
                    )
                )

                st.plotly_chart(fig_capacity, use_container_width=True)
                st.caption(f"💡 Entwicklung der {'Kapazität (FTE)' if view_mode == 'MAK' else 'Kosten'} über 24 Monate")

            with col2:
                st.markdown("##### 👥 Headcount-Entwicklung")

                fig_headcount = go.Figure()

                for org in selected_orgs:
                    df_org_history = history_selected[history_selected["Kürzel OrgEinheit"] == org].sort_values("Date")

                    fig_headcount.add_trace(go.Scatter(
                        x=df_org_history["Date"],
                        y=df_org_history["Headcount"],
                        mode='lines+markers',
                        name=str(org),
                        hovertemplate='<b>%{fullData.name}</b><br>%{x|%b %Y}<br>Headcount: %{y}<extra></extra>'
                    ))

                fig_headcount.update_layout(
                    height=400,
                    xaxis_title="Monat",
                    yaxis_title="Headcount",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color=COLORS["text_primary"]),
                    hovermode="x unified",
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.2,
                        xanchor="center",
                        x=0.5,
                        bgcolor="#FFFFFF",
                        bordercolor=COLORS["card_border"],
                        borderwidth=1
                    )
                )

                st.plotly_chart(fig_headcount, use_container_width=True)
                st.caption("💡 Entwicklung der Mitarbeiteranzahl über 24 Monate")

            st.divider()

            # === VAKANZ-ENTWICKLUNG ===
            st.markdown("##### 📉 Vakanz-Entwicklung")

            fig_vacancy = go.Figure()

            for org in selected_orgs:
                df_org_history = history_selected[history_selected["Kürzel OrgEinheit"] == org].sort_values("Date")

                fig_vacancy.add_trace(go.Scatter(
                    x=df_org_history["Date"],
                    y=df_org_history["Vacancy_Count"],
                    mode='lines+markers',
                    name=str(org),
                    hovertemplate='<b>%{fullData.name}</b><br>%{x|%b %Y}<br>Vakanzen: %{y}<extra></extra>'
                ))

            fig_vacancy.update_layout(
                height=400,
                xaxis_title="Monat",
                yaxis_title="Anzahl Vakanzen",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color=COLORS["text_primary"]),
                hovermode="x unified",
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.2,
                    xanchor="center",
                    x=0.5,
                    bgcolor="#FFFFFF",
                    bordercolor=COLORS["card_border"],
                    borderwidth=1
                )
            )

            st.plotly_chart(fig_vacancy, use_container_width=True)
            st.caption("💡 Entwicklung der unbesetzten Stellen über 24 Monate")

            st.divider()

            # === YOY VERÄNDERUNGEN ===
            st.markdown("#### 📊 Year-over-Year Veränderungen")

            # Berechne YoY für ausgewählte Einheiten
            yoy_data = []
            for org in selected_orgs:
                df_org_history = history_selected[history_selected["Kürzel OrgEinheit"] == org].sort_values("Date")

                if len(df_org_history) >= 12:
                    # Aktuellster Monat vs. vor 12 Monaten
                    latest = df_org_history.iloc[-1]
                    year_ago = df_org_history.iloc[-13] if len(df_org_history) >= 13 else df_org_history.iloc[0]

                    fte_change = latest["FTE"] - year_ago["FTE"]
                    fte_change_pct = (fte_change / year_ago["FTE"] * 100) if year_ago["FTE"] > 0 else 0

                    hc_change = latest["Headcount"] - year_ago["Headcount"]
                    hc_change_pct = (hc_change / year_ago["Headcount"] * 100) if year_ago["Headcount"] > 0 else 0

                    cost_change = latest["Total_Cost"] - year_ago["Total_Cost"]
                    cost_change_pct = (cost_change / year_ago["Total_Cost"] * 100) if year_ago["Total_Cost"] > 0 else 0

                    vacancy_change = latest["Vacancy_Count"] - year_ago["Vacancy_Count"]

                    yoy_data.append({
                        "Kürzel": org,
                        "FTE_Änderung": fte_change,
                        "FTE_Änderung_%": fte_change_pct,
                        "HC_Änderung": hc_change,
                        "HC_Änderung_%": hc_change_pct,
                        "Kosten_Änderung": cost_change,
                        "Kosten_Änderung_%": cost_change_pct,
                        "Vakanz_Änderung": vacancy_change
                    })

            if len(yoy_data) > 0:
                yoy_df = pd.DataFrame(yoy_data)

                display_yoy = yoy_df.copy()
                display_yoy["FTE_Änderung"] = display_yoy["FTE_Änderung"].round(2)
                display_yoy["FTE_Änderung_%"] = display_yoy["FTE_Änderung_%"].round(1).astype(str) + "%"
                display_yoy["HC_Änderung_%"] = display_yoy["HC_Änderung_%"].round(1).astype(str) + "%"
                display_yoy["Kosten_Änderung"] = display_yoy["Kosten_Änderung"].apply(lambda x: f"{x:,.0f}€")
                display_yoy["Kosten_Änderung_%"] = display_yoy["Kosten_Änderung_%"].round(1).astype(str) + "%"

                st.dataframe(
                    display_yoy,
                    use_container_width=True,
                    height=300,
                    hide_index=True,
                    column_config={
                        "Kürzel": st.column_config.TextColumn("Org-Einheit", width="small"),
                        "FTE_Änderung": st.column_config.NumberColumn("FTE Δ", format="%.2f"),
                        "FTE_Änderung_%": st.column_config.TextColumn("FTE Δ %"),
                        "HC_Änderung": st.column_config.NumberColumn("HC Δ", format="%d"),
                        "HC_Änderung_%": st.column_config.TextColumn("HC Δ %"),
                        "Kosten_Änderung": st.column_config.TextColumn("Kosten Δ"),
                        "Kosten_Änderung_%": st.column_config.TextColumn("Kosten Δ %"),
                        "Vakanz_Änderung": st.column_config.NumberColumn("Vakanz Δ", format="%d")
                    }
                )

                st.caption("💡 Veränderung zum Vorjahresmonat (12 Monate zurück)")

                # Export
                csv = display_yoy.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 YoY-Analyse als CSV exportieren",
                    data=csv,
                    file_name="org_einheiten_yoy_trends.csv",
                    mime="text/csv"
                )
            else:
                st.info("Nicht genügend historische Daten für YoY-Vergleich (mind. 12 Monate benötigt)")

with tab5:
    st.info("💰 Kosten-Effizienz-Analysen folgen in Phase 5")

with tab6:
    st.info("⏰ Teilzeit & ATZ-Analysen folgen in Phase 6")

with tab7:
    st.info("🎯 Benchmark-Matrix folgt in Phase 7")
