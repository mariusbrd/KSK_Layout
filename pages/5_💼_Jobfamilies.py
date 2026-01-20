"""
Jobfamilies-Modul für HR Pulse Dashboard.

Pattern-basiertes Jobfamily-Mapping, Analysen und Qualifikations-Gaps.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
import os

# Import components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from components.toggle import format_value
from components.kpi_card import kpi_card
from components.charts import create_heatmap
from config.settings import COLORS, format_percent, EDUCATION_HIERARCHY
from utils.jobfamily_matcher import (
    load_jobfamily_definitions,
    save_jobfamily_definitions,
    assign_jobfamilies,
    get_jobfamily_stats,
    get_qualification_gaps,
    get_unmapped_planstellen
)

# Page Config
st.set_page_config(
    page_title="Jobfamilies | HR Pulse",
    page_icon="💼",
    layout="wide"
)

# Load CSS
css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "style.css")
if os.path.exists(css_path):
    with open(css_path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Initialize session state for jobfamily definitions
if "jobfamily_definitions" not in st.session_state:
    st.session_state["jobfamily_definitions"] = load_jobfamily_definitions()

# Load data
snapshot_df, history_df, org_df, summary = load_and_prepare_data()

# Render global filters
render_global_filters(snapshot_df, history_df)

# Apply filters
filtered_df = apply_filters(snapshot_df)

# Assign Jobfamilies
filtered_df = assign_jobfamilies(filtered_df, st.session_state["jobfamily_definitions"])

# Title
st.title("💼 Jobfamilies")

# View Mode aus Session State lesen
view_mode = st.session_state.get("view_mode", "MAK")

# Filter summary
filter_info = get_filter_summary()
st.markdown(f"<div class='filter-summary'>{filter_info}</div>", unsafe_allow_html=True)

st.divider()

# Jobfamily Stats
stats = get_jobfamily_stats(filtered_df)

# KPI Row
col1, col2, col3, col4 = st.columns(4)

with col1:
    kpi_card(
        title="Gesamt Planstellen",
        value=f"{stats.get('total_planstellen', 0):,}".replace(",", "."),
        subtitle="Alle Stellen",
        icon="📋"
    )

with col2:
    mapping_rate = stats.get("mapping_rate", 0)
    status = "good" if mapping_rate >= 0.9 else ("warning" if mapping_rate >= 0.7 else "critical")
    kpi_card(
        title="Mapping-Quote",
        value=format_percent(mapping_rate),
        subtitle=f"{stats.get('mapped_planstellen', 0)} zugeordnet",
        icon="✅",
        status=status
    )

with col3:
    kpi_card(
        title="Jobfamilies",
        value=f"{stats.get('unique_families', 0)}",
        subtitle="Definiert",
        icon="💼"
    )

with col4:
    unmapped_count = stats.get("unmapped_planstellen", 0)
    status = "good" if unmapped_count == 0 else ("warning" if unmapped_count < 50 else "critical")
    kpi_card(
        title="Nicht zugeordnet",
        value=f"{unmapped_count}",
        subtitle="Planstellen ohne Mapping",
        icon="⚠️",
        status=status
    )

st.divider()


def render_analysis_tab(df: pd.DataFrame, view_mode: str, stats: dict):
    """Rendert den Analyse-Tab mit Treemap und Heatmap."""

    st.markdown("### 📊 Jobfamily-Verteilung")

    # Treemap
    st.markdown("#### Treemap: Jobfamilies nach Größe")

    value_col = "FTE_assigned" if view_mode == "MAK" else "Total_Cost_Year"

    family_agg = df[df["Jobfamily"] != "UNMAPPED"].groupby("Jobfamily").agg({
        value_col: "sum",
        "Planstellennr": "count"
    }).reset_index()
    family_agg.columns = ["Jobfamily", "Wert", "Anzahl"]

    if not family_agg.empty:
        fig_treemap = go.Figure(go.Treemap(
            labels=family_agg["Jobfamily"],
            parents=[""] * len(family_agg),
            values=family_agg["Wert"],
            text=family_agg["Anzahl"].apply(lambda x: f"{x} Stellen"),
            textposition="middle center",
            marker=dict(
                colorscale="Teal",
                line=dict(color=COLORS["background"], width=2)
            ),
            hovertemplate="<b>%{label}</b><br>Anzahl: %{text}<br>Wert: %{value:,.0f}<extra></extra>"
        ))

        fig_treemap.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            height=500
        )
        st.plotly_chart(fig_treemap, use_container_width=True)
    else:
        st.info("Keine zugeordneten Jobfamilies vorhanden")

    st.divider()

    # Heatmap: Jobfamily × Org-Einheit
    st.markdown("#### Heatmap: Jobfamilies nach Org-Einheit")

    # Top 10 Jobfamilies und Org-Einheiten
    top_families = df[df["Jobfamily"] != "UNMAPPED"]["Jobfamily"].value_counts().head(10).index.tolist()
    top_orgs = df["Kürzel OrgEinheit"].value_counts().head(10).index.tolist()

    heatmap_data = df[
        (df["Jobfamily"].isin(top_families)) &
        (df["Kürzel OrgEinheit"].isin(top_orgs))
    ]

    if not heatmap_data.empty:
        fig_heatmap = create_heatmap(
            heatmap_data,
            x_col="Kürzel OrgEinheit",
            y_col="Jobfamily",
            value_col="Planstellennr",
            title=""
        )
        st.plotly_chart(fig_heatmap, use_container_width=True)
        st.caption("💡 Top 10 Jobfamilies × Top 10 Org-Einheiten")
    else:
        st.info("Nicht genügend Daten für Heatmap")

    # Stacked Bar: Jobfamily nach Geschlecht
    st.markdown("#### Jobfamilies nach Geschlecht")

    active_df = df[~df["Is_Vacant"]]
    top_5_families = active_df[active_df["Jobfamily"] != "UNMAPPED"]["Jobfamily"].value_counts().head(5).index.tolist()
    family_gender_data = active_df[active_df["Jobfamily"].isin(top_5_families)]

    if not family_gender_data.empty:
        family_gender = family_gender_data.groupby(["Jobfamily", "Geschlecht"]).size().reset_index(name="Anzahl")

        fig_gender = go.Figure()
        for gender in ["w", "m"]:
            gender_data = family_gender[family_gender["Geschlecht"] == gender]
            fig_gender.add_trace(go.Bar(
                y=gender_data["Jobfamily"],
                x=gender_data["Anzahl"],
                name="Weiblich" if gender == "w" else "Männlich",
                orientation="h",
                marker_color=COLORS["gender_female"] if gender == "w" else COLORS["gender_male"]
            ))

        fig_gender.update_layout(
            barmode="stack",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            height=400,
            margin=dict(l=60, r=40, t=90, b=60),
            xaxis=dict(gridcolor=COLORS["card_border"], title="Anzahl Mitarbeitende"),
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
        st.plotly_chart(fig_gender, use_container_width=True)
        st.caption("💡 Top 5 Jobfamilies nach Geschlechterverteilung")
    else:
        st.info("Keine Daten für Geschlechterverteilung")


def render_definitions_tab(df: pd.DataFrame):
    """Rendert den Definitionen-Tab mit Editor und Unmapped-Liste."""

    st.markdown("### ⚙️ Jobfamily-Definitionen")

    # Editor
    st.markdown("#### Editor")

    with st.expander("➕ Neue Jobfamily hinzufügen", expanded=False):
        col1, col2 = st.columns(2)

        with col1:
            new_name = st.text_input("Name der Jobfamily", key="new_family_name")
            new_desc = st.text_area("Beschreibung", key="new_family_desc")

        with col2:
            new_patterns = st.text_area(
                "Patterns (ein Pattern pro Zeile)",
                help="Wildcards mit * möglich, z.B. *Berater*",
                key="new_family_patterns"
            )
            new_min_qual = st.selectbox(
                "Mindestqualifikation",
                options=list(EDUCATION_HIERARCHY.keys()),
                key="new_family_qual"
            )

        if st.button("💾 Jobfamily speichern", key="save_new_family"):
            if new_name and new_patterns:
                patterns_list = [p.strip() for p in new_patterns.split("\n") if p.strip()]

                st.session_state["jobfamily_definitions"][new_name] = {
                    "patterns": patterns_list,
                    "min_qualification": new_min_qual,
                    "description": new_desc,
                    "version": "v1",
                    "valid_from": pd.Timestamp.now().strftime("%Y-%m-%d")
                }

                save_jobfamily_definitions(st.session_state["jobfamily_definitions"])
                st.success(f"✓ Jobfamily '{new_name}' gespeichert")
                st.rerun()
            else:
                st.error("Name und mindestens ein Pattern erforderlich")

    st.divider()

    # Bestehende Jobfamilies
    st.markdown("#### Bestehende Jobfamilies")

    definitions = st.session_state["jobfamily_definitions"]

    for family_name, family_def in definitions.items():
        with st.expander(f"📋 {family_name}", expanded=False):
            col1, col2 = st.columns([3, 1])

            with col1:
                st.write(f"**Beschreibung:** {family_def.get('description', 'Keine Beschreibung')}")
                st.write(f"**Mindestqualifikation:** {family_def.get('min_qualification', 'Nicht definiert')}")
                st.write(f"**Patterns:**")
                for pattern in family_def.get("patterns", []):
                    st.write(f"  - `{pattern}`")

                # Anzahl zugeordneter Stellen
                count = (df["Jobfamily"] == family_name).sum()
                st.write(f"**Zugeordnete Stellen:** {count}")

            with col2:
                if st.button("🗑️ Löschen", key=f"delete_{family_name}"):
                    del st.session_state["jobfamily_definitions"][family_name]
                    save_jobfamily_definitions(st.session_state["jobfamily_definitions"])
                    st.success(f"✓ '{family_name}' gelöscht")
                    st.rerun()

    st.divider()

    # Unmapped Planstellen
    st.markdown("#### 🔍 Nicht zugeordnete Planstellen")

    unmapped = get_unmapped_planstellen(df)

    if not unmapped.empty:
        st.dataframe(
            unmapped,
            use_container_width=True,
            hide_index=True,
            height=400
        )

        # Export
        csv = unmapped.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Unmapped-Liste als CSV exportieren",
            data=csv,
            file_name="jobfamilies_unmapped.csv",
            mime="text/csv"
        )

        st.caption(f"💡 {len(unmapped)} verschiedene Planstellen-Bezeichnungen nicht zugeordnet")
    else:
        st.success("✓ Alle Planstellen sind Jobfamilies zugeordnet!")


def render_qualifications_tab(df: pd.DataFrame):
    """Rendert den Qualifikationen-Tab mit Gap-Analyse."""

    st.markdown("### 🎓 Qualifikations-Gap-Analyse")

    # Gap-Analyse durchführen
    gaps_df = get_qualification_gaps(df, st.session_state["jobfamily_definitions"])

    if gaps_df.empty:
        st.info("Keine Daten für Gap-Analyse verfügbar")
        return

    # Nur Mitarbeitende mit Gap
    gaps_only = gaps_df[gaps_df["Gap"] == True]

    # KPIs
    col1, col2, col3 = st.columns(3)

    with col1:
        total_employees = len(gaps_df)
        kpi_card(
            title="Mitarbeitende analysiert",
            value=f"{total_employees}",
            subtitle="In Jobfamilies",
            icon="👥"
        )

    with col2:
        gap_count = len(gaps_only)
        gap_rate = gap_count / total_employees if total_employees > 0 else 0
        status = "good" if gap_rate < 0.1 else ("warning" if gap_rate < 0.25 else "critical")
        kpi_card(
            title="Qualifikationslücken",
            value=f"{gap_count}",
            subtitle=format_percent(gap_rate),
            icon="⚠️",
            status=status
        )

    with col3:
        avg_gap_points = gaps_only["Gap_Points"].mean() if len(gaps_only) > 0 else 0
        kpi_card(
            title="Ø Gap-Größe",
            value=f"{avg_gap_points:.1f}",
            subtitle="Qualifikationsstufen",
            icon="📊"
        )

    st.divider()

    # Gap-Verteilung nach Jobfamily
    st.markdown("#### Gaps nach Jobfamily")

    gap_by_family = gaps_df.groupby("Jobfamily").agg({
        "Gap": lambda x: (x == True).sum(),
        "Planstelle": "count"
    }).reset_index()
    gap_by_family.columns = ["Jobfamily", "Mit Gap", "Gesamt"]
    gap_by_family["Gap-Quote %"] = (gap_by_family["Mit Gap"] / gap_by_family["Gesamt"] * 100).round(1)
    gap_by_family = gap_by_family[gap_by_family["Mit Gap"] > 0].sort_values("Gap-Quote %", ascending=False)

    if not gap_by_family.empty:
        fig_gap = go.Figure()

        fig_gap.add_trace(go.Bar(
            y=gap_by_family["Jobfamily"],
            x=gap_by_family["Mit Gap"],
            name="Mit Gap",
            orientation="h",
            marker_color=COLORS["status_critical"]
        ))

        fig_gap.add_trace(go.Bar(
            y=gap_by_family["Jobfamily"],
            x=gap_by_family["Gesamt"] - gap_by_family["Mit Gap"],
            name="Ohne Gap",
            orientation="h",
            marker_color=COLORS["status_good"]
        ))

        fig_gap.update_layout(
            barmode="stack",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color=COLORS["text_primary"]),
            height=400,
            margin=dict(l=60, r=40, t=90, b=60),
            xaxis=dict(gridcolor=COLORS["card_border"], title="Anzahl Mitarbeitende"),
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
        st.plotly_chart(fig_gap, use_container_width=True)
    else:
        st.success("✓ Keine Qualifikationslücken gefunden!")

    st.divider()

    # Detail-Tabelle
    st.markdown("#### Detail-Liste: Mitarbeitende mit Qualifikationslücken")

    if not gaps_only.empty:
        display_df = gaps_only[[
            "Jobfamily",
            "Planstelle",
            "Ist_Qualifikation",
            "Soll_Qualifikation",
            "Gap_Points"
        ]].copy()

        display_df.columns = [
            "Jobfamily",
            "Planstelle",
            "Ist-Qualifikation",
            "Soll-Qualifikation",
            "Gap (Stufen)"
        ]

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=400
        )

        # Export
        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Gap-Analyse als CSV exportieren",
            data=csv,
            file_name="qualifikations_gaps.csv",
            mime="text/csv"
        )
    else:
        st.success("✓ Keine Mitarbeitenden mit Qualifikationslücken!")


# Tabs
tab1, tab2, tab3 = st.tabs([
    "📊 Analyse",
    "⚙️ Definitionen",
    "🎓 Qualifikationen"
])

# =====================================================================
# TAB 1: ANALYSE
# =====================================================================
with tab1:
    render_analysis_tab(filtered_df, view_mode, stats)

# =====================================================================
# TAB 2: DEFINITIONEN
# =====================================================================
with tab2:
    render_definitions_tab(filtered_df)

# =====================================================================
# TAB 3: QUALIFIKATIONEN
# =====================================================================
with tab3:
    render_qualifications_tab(filtered_df)
