"""
Modul 2: Demografie

Analysen zu Alter, Geschlecht, Qualifikation und Arbeitszeit.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataloader.loader import load_and_prepare_data
from config.settings import format_percent, COLORS, COHORT_COLORS
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from components.toggle import format_value
from components.kpi_card import kpi_card, kpi_row
from components.charts import (
    create_donut_chart, create_bar_chart, create_population_pyramid,
    create_heatmap
)
from utils.ui_helpers import metric_info, section_header
from dataloader.kpi_engine import get_unique_employees


def load_custom_css():
    """Lädt Custom CSS."""
    css_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "style.css")
    if os.path.exists(css_path):
        with open(css_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def main():
    load_custom_css()
    st.title("👥 Demografie")

    try:
        # Lade Daten
        snapshot_df, history_df, org_df, summary = load_and_prepare_data()

        # Globale Filter-Sidebar
        render_global_filters(snapshot_df, history_df)

        # Filter anwenden
        filtered_df = apply_filters(snapshot_df)

        # Filter-Summary
        filter_info = get_filter_summary()
        st.markdown(f"<div class='filter-summary'>{filter_info}</div>", unsafe_allow_html=True)

        # View Mode aus Session State lesen
        view_mode = st.session_state.get("view_mode", "MAK")

        st.divider()

        # Unique Mitarbeitende (dedupliziert nach PersNr, Readme-konform)
        unique_emp = get_unique_employees(filtered_df)

        if len(unique_emp) == 0:
            st.warning("Keine Daten für die aktuellen Filter verfügbar.")
            return

        # MAK-Spalte als korrekte FTE-Metrik
        mak_col = "MAK" if "MAK" in unique_emp.columns else "FTE_assigned"

        # Tabs
        tab1, tab2, tab3, tab4 = st.tabs([
            "📊 Alter",
            "⚧ Geschlecht",
            "🎓 Qualifikation",
            "⏰ Arbeitszeit"
        ])

        # =====================================================================
        # TAB 1: ALTER
        # =====================================================================
        with tab1:
            render_age_tab(unique_emp, view_mode, mak_col)

        # =====================================================================
        # TAB 2: GESCHLECHT
        # =====================================================================
        with tab2:
            render_gender_tab(unique_emp, view_mode, mak_col)

        # =====================================================================
        # TAB 3: QUALIFIKATION
        # =====================================================================
        with tab3:
            render_education_tab(unique_emp, view_mode, mak_col)

        # =====================================================================
        # TAB 4: ARBEITSZEIT
        # =====================================================================
        with tab4:
            render_worktime_tab(unique_emp, view_mode, mak_col)

    except FileNotFoundError:
        st.error("❌ Testdaten nicht gefunden! Bitte generiere zuerst die Testdaten.")
    except Exception as e:
        st.error(f"Fehler: {str(e)}")
        import traceback
        st.code(traceback.format_exc())


def render_age_tab(df: pd.DataFrame, view_mode: str, mak_col: str = "MAK"):
    """Rendert den Alter-Tab mit Population Pyramid und Kohorten-Analyse."""

    section_header(
        "Altersstruktur",
        "Analyse der Altersverteilung zur Identifikation demografischer Risiken und Nachfolgebedarfe",
        "📊"
    )

    metric_info(
        "Altersanalyse",
        "Die Altersstruktur zeigt, wie die Belegschaft über verschiedene Altersgruppen verteilt ist. "
        "Hohe Anteile bei 55+ Jahren signalisieren Ruhestandswellen, niedrige Anteile unter 30 Jahren deuten auf Nachwuchsprobleme hin."
    )

    # KPIs
    col1, col2, col3 = st.columns(3)

    with col1:
        # Alter_Jahre (float) für präzise Berechnung, Alter (int) als Fallback
        alter_col = "Alter_Jahre" if "Alter_Jahre" in df.columns else "Alter"
        avg_age = df[alter_col].mean()
        median_age = df[alter_col].median()
        kpi_card(
            title="Durchschnittsalter",
            value=f"{avg_age:.1f} Jahre",
            subtitle=f"Median: {median_age:.1f} Jahre",
            icon="👤"
        )

    with col2:
        age_55plus = (df["Alter"] >= 55).sum()
        age_55plus_rate = age_55plus / len(df)
        status = "warning" if age_55plus_rate > 0.35 else "good"
        kpi_card(
            title="55+ Jahre",
            value=f"{age_55plus}",
            subtitle=f"{format_percent(age_55plus_rate)} der Belegschaft",
            icon="👴",
            status=status
        )

    with col3:
        age_under_30 = (df["Alter"] < 30).sum()
        age_under_30_rate = age_under_30 / len(df)
        kpi_card(
            title="Unter 30 Jahre",
            value=f"{age_under_30}",
            subtitle=f"{format_percent(age_under_30_rate)} der Belegschaft",
            icon="👶",
            status="good" if age_under_30_rate > 0.2 else "warning"
        )

    st.divider()

    # Population Pyramid
    st.markdown("#### 📊 Alterspyramide")
    metric_info(
        "Population Pyramid",
        "Visualisiert die Altersverteilung nach Geschlecht. Breite Basis = viele junge Mitarbeitende, "
        "breite Spitze = Überalterung. Ideal ist eine relativ gleichmäßige Verteilung."
    )

    value_col = mak_col if view_mode == "MAK" else "Total_Cost_Year"
    fig_pyramid = create_population_pyramid(
        df,
        age_col="Alter",
        gender_col="Geschlecht",
        value_col=value_col,
        title=""
    )
    st.plotly_chart(fig_pyramid, use_container_width=True)

    # Kohorten-Analyse
    st.markdown("#### Verteilung nach Alterskohorten")

    cohort_data = df.groupby("Alterskohorte").agg({
        "PersNr": "count",
        mak_col: "sum",
        "Total_Cost_Year": "sum",
        "Alter": "mean"
    }).reset_index()
    cohort_data.columns = ["Kohorte", "Anzahl", "MAK", "Kosten", "Ø Alter"]

    # Sortiere nach Altersgruppen-Logik
    cohort_order = list(st.session_state.get("cohort_definitions", {}).keys())
    cohort_data["sort_key"] = cohort_data["Kohorte"].apply(
        lambda x: cohort_order.index(x) if x in cohort_order else 999
    )
    cohort_data = cohort_data.sort_values("sort_key").drop("sort_key", axis=1)

    # Berechne Anteile
    cohort_data["Anteil"] = cohort_data["Anzahl"] / cohort_data["Anzahl"].sum()

    # Formatiere Tabelle
    cohort_display = cohort_data.copy()
    cohort_display["Anteil"] = cohort_display["Anteil"].apply(lambda x: format_percent(x))
    cohort_display["MAK"] = cohort_display["MAK"].apply(lambda x: f"{x:.1f}")
    cohort_display["Kosten"] = cohort_display["Kosten"].apply(lambda x: format_value(x, "Euro"))
    cohort_display["Ø Alter"] = cohort_display["Ø Alter"].apply(lambda x: f"{x:.1f}")

    st.dataframe(
        cohort_display,
        hide_index=True,
        use_container_width=True
    )

    # Kohorten-Chart
    col1, col2 = st.columns(2)

    with col1:
        # Stacked Bar: Kohorte × Geschlecht
        st.markdown("##### Kohorten nach Geschlecht")
        cohort_gender = df.groupby(["Alterskohorte", "Geschlecht"]).size().reset_index(name="Anzahl")

        fig = go.Figure()
        for gender in ["m", "w"]:
            gender_data = cohort_gender[cohort_gender["Geschlecht"] == gender]
            fig.add_trace(go.Bar(
                x=gender_data["Alterskohorte"],
                y=gender_data["Anzahl"],
                name="Männlich" if gender == "m" else "Weiblich",
                marker_color=COLORS["gender_male"] if gender == "m" else COLORS["gender_female"]
            ))

        fig.update_layout(
            barmode="stack",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            height=350,
            margin=dict(l=60, r=40, t=90, b=60),
            xaxis=dict(gridcolor=COLORS["card_border"]),
            yaxis=dict(gridcolor=COLORS["card_border"]),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                bgcolor="#FFFFFF",
                bordercolor="rgba(0,0,0,0)",
                borderwidth=0
            )
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Qualifikation × Alter - Stacked Bar
        st.markdown("##### Top Qualifikationen nach Kohorte")

        # Top 5 Qualifikationen
        top_edu = df["Ausbildung"].value_counts().head(5).index.tolist()
        qual_age_data = df[df["Ausbildung"].isin(top_edu)]

        qual_cohort = qual_age_data.groupby(["Alterskohorte", "Ausbildung"]).size().reset_index(name="Anzahl")

        fig2 = go.Figure()
        for i, edu in enumerate(top_edu):
            edu_data = qual_cohort[qual_cohort["Ausbildung"] == edu]
            from config.settings import COLOR_SEQUENCE
            fig2.add_trace(go.Bar(
                x=edu_data["Alterskohorte"],
                y=edu_data["Anzahl"],
                name=edu,
                marker_color=COLOR_SEQUENCE[i % len(COLOR_SEQUENCE)]
            ))

        fig2.update_layout(
            barmode="stack",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            height=350,
            margin=dict(l=60, r=40, t=90, b=60),
            xaxis=dict(gridcolor=COLORS["card_border"]),
            yaxis=dict(gridcolor=COLORS["card_border"]),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                bgcolor="#FFFFFF",
                bordercolor="rgba(0,0,0,0)",
                borderwidth=0
            )
        )
        st.plotly_chart(fig2, use_container_width=True)


def render_gender_tab(df: pd.DataFrame, view_mode: str):
    """Rendert den Geschlecht-Tab."""

    st.markdown("### ⚧ Geschlechterverteilung")

    # KPIs
    gender_counts = df["Geschlecht"].value_counts()
    female_count = gender_counts.get("w", 0)
    male_count = gender_counts.get("m", 0)
    total = len(df)

    female_rate = female_count / total if total > 0 else 0
    male_rate = male_count / total if total > 0 else 0

    col1, col2, col3 = st.columns(3)

    with col1:
        kpi_card(
            title="Weiblich",
            value=f"{female_count}",
            subtitle=format_percent(female_rate),
            icon="♀",
            status="good"
        )

    with col2:
        kpi_card(
            title="Männlich",
            value=f"{male_count}",
            subtitle=format_percent(male_rate),
            icon="♂"
        )

    with col3:
        # Benchmark Banken (63% weiblich ist typisch)
        benchmark = 0.63
        diff = female_rate - benchmark
        kpi_card(
            title="vs. Benchmark",
            value=f"{diff*100:+.1f}%",
            subtitle=f"Ziel: {format_percent(benchmark)} weiblich",
            icon="🎯",
            status="good" if abs(diff) < 0.05 else "warning"
        )

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        # Donut Chart
        st.markdown("#### Gesamtverteilung")
        gender_dist = df.groupby("Geschlecht").agg({
            "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year": "sum"
        }).reset_index()
        gender_dist.columns = ["Geschlecht", "Wert"]
        gender_dist["Geschlecht"] = gender_dist["Geschlecht"].map({"m": "Männlich", "w": "Weiblich"})

        fig_gender = create_donut_chart(
            gender_dist,
            values_col="Wert",
            names_col="Geschlecht",
            colors=[COLORS["gender_male"], COLORS["gender_female"]]
        )
        st.plotly_chart(fig_gender, use_container_width=True)

    with col2:
        # Grouped Bar: Geschlecht × Kohorte
        st.markdown("#### Geschlecht nach Alterskohorte")

        cohort_gender = df.groupby(["Alterskohorte", "Geschlecht"]).size().reset_index(name="Anzahl")

        fig = go.Figure()
        for gender in ["w", "m"]:
            gender_data = cohort_gender[cohort_gender["Geschlecht"] == gender]
            fig.add_trace(go.Bar(
                x=gender_data["Alterskohorte"],
                y=gender_data["Anzahl"],
                name="Weiblich" if gender == "w" else "Männlich",
                marker_color=COLORS["gender_female"] if gender == "w" else COLORS["gender_male"]
            ))

        fig.update_layout(
            barmode="group",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            height=350,
            margin=dict(l=60, r=40, t=90, b=60),
            xaxis=dict(gridcolor=COLORS["card_border"]),
            yaxis=dict(gridcolor=COLORS["card_border"]),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                bgcolor="#FFFFFF",
                bordercolor="rgba(0,0,0,0)",
                borderwidth=0
            )
        )
        st.plotly_chart(fig, use_container_width=True)

    # Heatmap: Geschlecht × Ausbildung
    st.markdown("#### Geschlecht nach Qualifikation")

    # Top 8 Qualifikationen
    top_edu = df["Ausbildung"].value_counts().head(8).index.tolist()
    heatmap_data = df[df["Ausbildung"].isin(top_edu)]

    fig_heat = create_heatmap(
        heatmap_data,
        x_col="Geschlecht",
        y_col="Ausbildung",
        value_col="PersNr"
    )
    st.plotly_chart(fig_heat, use_container_width=True)


def render_education_tab(df: pd.DataFrame, view_mode: str):
    """Rendert den Qualifikations-Tab."""

    st.markdown("### 🎓 Qualifikationsstruktur")

    # KPIs
    total = len(df)
    akademiker = df[df["Ausbildung"].str.contains("Bachelor|Master", case=False, na=False)].shape[0]
    akademiker_rate = akademiker / total if total > 0 else 0

    azubis = df[df["Ausbildung"].str.contains("Berufsausbildung", case=False, na=False)].shape[0]
    azubis_rate = azubis / total if total > 0 else 0

    col1, col2, col3 = st.columns(3)

    with col1:
        kpi_card(
            title="Akademiker",
            value=f"{akademiker}",
            subtitle=format_percent(akademiker_rate),
            icon="🎓",
            status="good" if akademiker_rate > 0.15 else "warning"
        )

    with col2:
        kpi_card(
            title="In Ausbildung",
            value=f"{azubis}",
            subtitle=format_percent(azubis_rate),
            icon="📚"
        )

    with col3:
        # Durchschnittliche Qualifikation (basierend auf Hierarchy)
        from config.settings import EDUCATION_HIERARCHY
        avg_level = df["Ausbildung"].map(EDUCATION_HIERARCHY).mean()
        kpi_card(
            title="Ø Qualifikationslevel",
            value=f"{avg_level:.1f}",
            subtitle="Skala 0-9",
            icon="📊"
        )

    st.divider()

    # Treemap
    st.markdown("#### Qualifikationsverteilung (Treemap)")

    value_col = "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year"
    edu_agg = df.groupby("Ausbildung").agg({
        value_col: "sum",
        "PersNr": "count"
    }).reset_index()
    edu_agg.columns = ["Qualifikation", "Wert", "Anzahl"]

    fig = go.Figure(go.Treemap(
        labels=edu_agg["Qualifikation"],
        parents=[""] * len(edu_agg),
        values=edu_agg["Wert"],
        text=edu_agg["Anzahl"].apply(lambda x: f"{x} MA"),
        textposition="middle center",
        marker=dict(
            colorscale="Teal",
            line=dict(color=COLORS["background"], width=2)
        ),
        hovertemplate="<b>%{label}</b><br>Anzahl: %{text}<br>Wert: %{value:,.0f}<extra></extra>"
    ))

    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COLORS["text_primary"]),
        height=400
    )
    st.plotly_chart(fig, use_container_width=True)

    # Stacked Bar: Qualifikation × Alter
    st.markdown("#### Qualifikation nach Alterskohorte")

    # Top 6 Qualifikationen
    top_edu = df["Ausbildung"].value_counts().head(6).index.tolist()
    qual_cohort_data = df[df["Ausbildung"].isin(top_edu)]

    qual_cohort = qual_cohort_data.groupby(["Alterskohorte", "Ausbildung"]).size().reset_index(name="Anzahl")

    fig2 = go.Figure()
    from config.settings import COLOR_SEQUENCE
    for i, edu in enumerate(top_edu):
        edu_data = qual_cohort[qual_cohort["Ausbildung"] == edu]
        fig2.add_trace(go.Bar(
            y=edu_data["Alterskohorte"],
            x=edu_data["Anzahl"],
            name=edu,
            orientation="h",
            marker_color=COLOR_SEQUENCE[i % len(COLOR_SEQUENCE)]
        ))

    fig2.update_layout(
        barmode="stack",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COLORS["text_primary"]),
        height=400,
        margin=dict(l=60, r=40, t=90, b=60),
        xaxis=dict(gridcolor=COLORS["card_border"]),
        yaxis=dict(gridcolor=COLORS["card_border"], autorange="reversed"),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            bgcolor="#FFFFFF",
            bordercolor="rgba(0,0,0,0)",
            borderwidth=0
        )
    )
    st.plotly_chart(fig2, use_container_width=True)


def render_worktime_tab(df: pd.DataFrame, view_mode: str):
    """Rendert den Arbeitszeit-Tab."""

    st.markdown("### ⏰ Arbeitszeitmodelle")

    # KPIs (unique Köpfe via kpi_engine)
    from dataloader.kpi_engine import compute_teilzeit_kpis, get_unique_employees, compute_headcount
    _emp = get_unique_employees(df) if "Is_Vacant" in df.columns else df
    _tz = compute_teilzeit_kpis(df) if "Is_Vacant" in df.columns else {"count": 0, "quote_pct": 0}
    _hc = len(_emp)

    tz_count = _tz["count"]
    vz_count = _hc - tz_count  # Vollzeit = Headcount minus Teilzeit
    # Korrektur: Inaktive (BsGrd=0) abziehen
    _inaktiv = int((_emp["BsGrd"] == 0).sum()) if "BsGrd" in _emp.columns else 0
    vz_count = max(0, vz_count - _inaktiv)
    total = _hc

    vz_rate = vz_count / total if total > 0 else 0
    tz_rate = tz_count / total if total > 0 else 0

    avg_fte = _emp["FTE_person"].mean() if "FTE_person" in _emp.columns else (df["FTE_person"].mean() if "FTE_person" in df.columns else 0)

    col1, col2, col3 = st.columns(3)

    with col1:
        kpi_card(
            title="Vollzeit",
            value=f"{vz_count}",
            subtitle=format_percent(vz_rate),
            icon="⏰",
            status="good"
        )

    with col2:
        kpi_card(
            title="Teilzeit",
            value=f"{tz_count}",
            subtitle=format_percent(tz_rate),
            icon="🕐"
        )

    with col3:
        kpi_card(
            title="Ø Beschäftigungsgrad",
            value=f"{avg_fte:.2%}",
            subtitle=f"{avg_fte * 39:.1f} Std./Woche",
            icon="📊"
        )

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        # Donut VZ/TZ
        st.markdown("#### Vollzeit vs. Teilzeit")

        vz_tz_dist = df.groupby("Arbeitszeit").agg({
            "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year": "sum"
        }).reset_index()
        vz_tz_dist.columns = ["Arbeitszeit", "Wert"]

        fig_vz_tz = create_donut_chart(
            vz_tz_dist,
            values_col="Wert",
            names_col="Arbeitszeit"
        )
        st.plotly_chart(fig_vz_tz, use_container_width=True)

    with col2:
        # Grouped Bar: Arbeitszeit nach Kohorte
        st.markdown("#### Arbeitszeit nach Alterskohorte")

        work_cohort = df.groupby(["Alterskohorte", "Arbeitszeit"]).size().reset_index(name="Anzahl")

        fig = go.Figure()
        for work_type in ["Vollzeit", "Teilzeit"]:
            work_data = work_cohort[work_cohort["Arbeitszeit"] == work_type]
            fig.add_trace(go.Bar(
                x=work_data["Alterskohorte"],
                y=work_data["Anzahl"],
                name=work_type,
                marker_color=COLORS["accent_teal"] if work_type == "Vollzeit" else COLORS["accent_amber"]
            ))

        fig.update_layout(
            barmode="group",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            height=350,
            margin=dict(l=60, r=40, t=90, b=60),
            xaxis=dict(gridcolor=COLORS["card_border"]),
            yaxis=dict(gridcolor=COLORS["card_border"]),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                bgcolor="#FFFFFF",
                bordercolor="rgba(0,0,0,0)",
                borderwidth=0
            )
        )
        st.plotly_chart(fig, use_container_width=True)

    # Scatter: Alter vs. Beschäftigungsgrad
    st.markdown("#### Beschäftigungsgrad nach Alter")

    fig_scatter = go.Figure()

    # Scatter für Männer und Frauen getrennt
    for gender, color in [("w", COLORS["gender_female"]), ("m", COLORS["gender_male"])]:
        gender_data = df[df["Geschlecht"] == gender]
        fig_scatter.add_trace(go.Scatter(
            x=gender_data["Alter"],
            y=gender_data["FTE_person"] * 100,
            mode="markers",
            name="Weiblich" if gender == "w" else "Männlich",
            marker=dict(
                color=color,
                size=8,
                opacity=0.6,
                line=dict(color=COLORS["background"], width=1)
            ),
            hovertemplate="Alter: %{x}<br>Beschäftigungsgrad: %{y:.0f}%<extra></extra>"
        ))

    fig_scatter.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COLORS["text_primary"]),
        height=400,
        margin=dict(l=60, r=40, t=90, b=60),
        xaxis=dict(
            title="Alter (Jahre)",
            gridcolor=COLORS["card_border"]
        ),
        yaxis=dict(
            title="Beschäftigungsgrad (%)",
            gridcolor=COLORS["card_border"]
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            bgcolor="#FFFFFF",
            bordercolor="rgba(0,0,0,0)",
            borderwidth=0
        ),
        hovermode="closest"
    )
    st.plotly_chart(fig_scatter, use_container_width=True)


if __name__ == "__main__":
    main()
