"""
Simulation-Modul für HR Pulse Dashboard.

Workforce Forecasting mit Szenariovergleich und Monte-Carlo-Simulation.
"""

import streamlit as st
import pandas as pd
import sys
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta

# Import components
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from components.kpi_card import kpi_card
from components.charts import create_line_chart
from config.settings import COLORS, DEFAULT_COHORTS
from utils.simulation import (
    SimulationParams,
    simulate_workforce,
    run_monte_carlo
)

# Page Config
st.set_page_config(
    page_title="Simulation | HR Pulse",
    page_icon="📈",
    layout="wide"
)

# Load CSS
css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "style.css")
if os.path.exists(css_path):
    with open(css_path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Initialize page-specific session state
if "scenario_a_params" not in st.session_state:
    st.session_state["scenario_a_params"] = None

if "scenario_b_params" not in st.session_state:
    st.session_state["scenario_b_params"] = None

if "simulation_results_a" not in st.session_state:
    st.session_state["simulation_results_a"] = None

if "simulation_results_b" not in st.session_state:
    st.session_state["simulation_results_b"] = None

# Load data
snapshot_df, history_df, org_df, summary = load_and_prepare_data()

# Render global filters
render_global_filters(snapshot_df, history_df)

# Apply filters
filtered_df = apply_filters(snapshot_df)

# Title
st.title("📈 Workforce Simulation")

# View Mode aus Session State lesen
view_mode = st.session_state.get("view_mode", "MAK")

# Filter summary
filter_info = get_filter_summary()
st.markdown(f"<div class='filter-summary'>{filter_info}</div>", unsafe_allow_html=True)

st.divider()


def render_parameter_editor(scenario_name: str, key_prefix: str) -> SimulationParams:
    """
    Rendert den Parameter-Editor für ein Szenario.

    Args:
        scenario_name: Name des Szenarios
        key_prefix: Präfix für Session State Keys

    Returns:
        SimulationParams Objekt
    """
    st.markdown(f"#### 🎯 Szenario: {scenario_name}")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### ⏱️ Zeithorizont")
        horizon_months = st.slider(
            "Prognosehorizont (Monate)",
            min_value=6,
            max_value=120,
            value=36,
            step=6,
            key=f"{key_prefix}_horizon"
        )

        st.markdown("##### 👴 Renteneintritte")
        retirement_age = st.number_input(
            "Reguläres Rentenalter",
            min_value=60,
            max_value=70,
            value=67,
            key=f"{key_prefix}_retirement_age"
        )

        early_retirement_age = st.number_input(
            "Frühverrentungs-Alter",
            min_value=60,
            max_value=67,
            value=63,
            key=f"{key_prefix}_early_retirement_age"
        )

        early_retirement_rate = st.slider(
            "Frühverrentungs-Rate (%)",
            min_value=0,
            max_value=50,
            value=15,
            step=5,
            key=f"{key_prefix}_early_retirement_rate"
        ) / 100

        st.markdown("##### 🔄 ATZ-Parameter")
        atz_arbeitsphase_months = st.number_input(
            "Dauer Arbeitsphase (Monate)",
            min_value=12,
            max_value=60,
            value=36,
            step=6,
            key=f"{key_prefix}_atz_duration"
        )

    with col2:
        st.markdown("##### 📉 Fluktuation nach Alterskohorte")

        cohort_defs = st.session_state.get("cohort_definitions", DEFAULT_COHORTS)
        attrition_rates = {}

        default_rates = {
            "Azubis": 12,
            "Young Professionals": 10,
            "Mid Career": 6,
            "Senior": 4,
            "Pre-Retirement": 2,
            "Retirement Ready": 1
        }

        for cohort_name in cohort_defs.keys():
            default_rate = default_rates.get(cohort_name, 5)
            rate = st.slider(
                f"{cohort_name} (%/Jahr)",
                min_value=0,
                max_value=20,
                value=default_rate,
                step=1,
                key=f"{key_prefix}_attrition_{cohort_name}"
            )
            attrition_rates[cohort_name] = rate / 100

        st.markdown("##### 👥 Neueinstellungen")
        hiring_enabled = st.checkbox(
            "Neueinstellungen aktivieren",
            value=True,
            key=f"{key_prefix}_hiring_enabled"
        )

        if hiring_enabled:
            hiring_rate = st.slider(
                "Hiring-Rate (% Vakanzen/Monat)",
                min_value=0,
                max_value=100,
                value=30,
                step=5,
                key=f"{key_prefix}_hiring_rate"
            ) / 100

            time_to_fill = st.number_input(
                "Time-to-Fill (Monate)",
                min_value=1,
                max_value=12,
                value=3,
                key=f"{key_prefix}_time_to_fill"
            )
        else:
            hiring_rate = 0.0
            time_to_fill = 3

        st.markdown("##### 🎓 Azubi-Übernahme")
        azubi_takeover_rate = st.slider(
            "Übernahmequote (%)",
            min_value=0,
            max_value=100,
            value=85,
            step=5,
            key=f"{key_prefix}_azubi_takeover"
        ) / 100

    # Monte-Carlo Optionen
    with st.expander("🎲 Monte-Carlo Simulation (Unsicherheiten)"):
        enable_mc = st.checkbox(
            "Monte-Carlo aktivieren",
            value=False,
            key=f"{key_prefix}_mc_enabled"
        )

        if enable_mc:
            mc_iterations = st.number_input(
                "Anzahl Iterationen",
                min_value=10,
                max_value=1000,
                value=100,
                step=10,
                key=f"{key_prefix}_mc_iterations"
            )
        else:
            mc_iterations = 100

    # Erstelle SimulationParams
    params = SimulationParams(
        horizon_months=horizon_months,
        start_date=datetime.now(),
        retirement_age=retirement_age,
        early_retirement_age=early_retirement_age,
        early_retirement_rate=early_retirement_rate,
        attrition_rates=attrition_rates,
        hiring_enabled=hiring_enabled,
        hiring_rate=hiring_rate,
        time_to_fill_months=int(time_to_fill),
        atz_arbeitsphase_duration_months=int(atz_arbeitsphase_months),
        azubi_takeover_rate=azubi_takeover_rate,
        enable_monte_carlo=enable_mc,
        monte_carlo_iterations=int(mc_iterations)
    )

    return params


def render_simulation_results(results_df: pd.DataFrame, scenario_name: str, view_mode: str):
    """
    Rendert Simulationsergebnisse.

    Args:
        results_df: Ergebnis-DataFrame
        scenario_name: Name des Szenarios
        view_mode: "MAK" oder "Euro"
    """
    st.markdown(f"### 📊 Ergebnisse: {scenario_name}")

    # KPI Row
    col1, col2, col3, col4 = st.columns(4)

    current_values = results_df.iloc[0]
    final_values = results_df.iloc[-1]

    with col1:
        delta = final_values["Headcount"] - current_values["Headcount"]
        kpi_card(
            title="Headcount Prognose",
            value=f"{final_values['Headcount']:.0f}",
            subtitle=f"Δ {delta:+.0f} vs. heute",
            icon="👥"
        )

    with col2:
        if view_mode == "MAK":
            delta_fte = final_values["FTE"] - current_values["FTE"]
            kpi_card(
                title="FTE Prognose",
                value=f"{final_values['FTE']:.1f}",
                subtitle=f"Δ {delta_fte:+.1f} vs. heute",
                icon="📊"
            )
        else:  # Euro
            delta_cost = final_values["Total_Cost"] - current_values["Total_Cost"]
            kpi_card(
                title="Kosten Prognose",
                value=f"{final_values['Total_Cost'] / 1_000_000:.1f}M €",
                subtitle=f"Δ {delta_cost / 1_000:.0f}k € vs. heute",
                icon="💰"
            )

    with col3:
        delta_vac = final_values["Vacancies"] - current_values["Vacancies"]
        status = "good" if delta_vac < 0 else ("warning" if delta_vac < 10 else "critical")
        kpi_card(
            title="Vakanzen Prognose",
            value=f"{final_values['Vacancies']:.0f}",
            subtitle=f"Δ {delta_vac:+.0f} vs. heute",
            icon="📭",
            status=status
        )

    with col4:
        if view_mode == "MAK":
            # Durchschnitts-FTE pro Kopf
            avg_fte_current = current_values["FTE"] / current_values["Headcount"] if current_values["Headcount"] > 0 else 0
            avg_fte_final = final_values["FTE"] / final_values["Headcount"] if final_values["Headcount"] > 0 else 0
            delta_avg = avg_fte_final - avg_fte_current
            kpi_card(
                title="Ø FTE/Kopf",
                value=f"{avg_fte_final:.2f}",
                subtitle=f"Δ {delta_avg:+.3f} vs. heute",
                icon="📈"
            )
        else:  # Euro
            # Durchschnittskosten pro Kopf
            avg_cost_current = current_values["Total_Cost"] / current_values["Headcount"] if current_values["Headcount"] > 0 else 0
            avg_cost_final = final_values["Total_Cost"] / final_values["Headcount"] if final_values["Headcount"] > 0 else 0
            delta_avg = avg_cost_final - avg_cost_current
            kpi_card(
                title="Ø Kosten/Kopf",
                value=f"{avg_cost_final / 1_000:.0f}k €",
                subtitle=f"Δ {delta_avg / 1_000:+.0f}k € vs. heute",
                icon="📈"
            )

    st.divider()

    # Charts
    col1, col2 = st.columns(2)

    with col1:
        if view_mode == "MAK":
            st.markdown("#### 📊 FTE Entwicklung")
            y_col = "FTE"
        else:
            st.markdown("#### 💰 Kosten Entwicklung")
            y_col = "Total_Cost"

        fig_main = create_line_chart(
            df=results_df,
            x_col="Date",
            y_col=y_col,
            title="",
            height=400
        )
        st.plotly_chart(fig_main, use_container_width=True)

    with col2:
        st.markdown("#### 📭 Vakanzen-Entwicklung")

        fig_vac = create_line_chart(
            df=results_df,
            x_col="Date",
            y_col="Vacancies",
            title="",
            height=400
        )
        st.plotly_chart(fig_vac, use_container_width=True)

    # Events Timeline (if available)
    if "retirements" in results_df.columns:
        st.markdown("#### 🔄 Bewegungen (kumuliert)")

        events_df = results_df[["Date", "retirements", "attrition", "hires"]].copy()
        events_df.columns = ["Datum", "Renteneintritte", "Fluktuation", "Neueinstellungen"]

        # Kumuliere Events
        events_df["Renteneintritte"] = events_df["Renteneintritte"].cumsum()
        events_df["Fluktuation"] = events_df["Fluktuation"].cumsum()
        events_df["Neueinstellungen"] = events_df["Neueinstellungen"].cumsum()

        fig_events = create_line_chart(
            df=events_df.melt(id_vars="Datum", var_name="Typ", value_name="Anzahl"),
            x_col="Datum",
            y_col="Anzahl",
            group_col="Typ",
            title="",
            height=400
        )
        st.plotly_chart(fig_events, use_container_width=True)


def render_monte_carlo_results(mc_results: dict, scenario_name: str, view_mode: str = "MAK"):
    """
    Rendert Monte-Carlo Ergebnisse mit Konfidenzbändern.

    Args:
        mc_results: Dict mit mean, p10, p90 DataFrames
        scenario_name: Name des Szenarios
        view_mode: "MAK" oder "Euro"
    """
    st.markdown(f"### 🎲 Monte-Carlo Analyse: {scenario_name}")

    st.info("💡 Die Simulation wurde mit Zufallsvariationen durchgeführt. "
            "Das schattierte Band zeigt die Unsicherheit (10.-90. Perzentil).")

    # Headcount mit Konfidenzbändern
    st.markdown("#### 👥 Headcount Prognose mit Unsicherheit")

    import plotly.graph_objects as go

    fig = go.Figure()

    # Konfidenzband (P10-P90)
    fig.add_trace(go.Scatter(
        x=mc_results["p90"]["Date"],
        y=mc_results["p90"]["Headcount"],
        mode="lines",
        line=dict(width=0),
        showlegend=False,
        hoverinfo="skip"
    ))

    fig.add_trace(go.Scatter(
        x=mc_results["p10"]["Date"],
        y=mc_results["p10"]["Headcount"],
        mode="lines",
        line=dict(width=0),
        fillcolor="rgba(0, 136, 222, 0.2)",
        fill="tonexty",
        name="10.-90. Perzentil",
        hoverinfo="skip"
    ))

    # Mittelwert
    fig.add_trace(go.Scatter(
        x=mc_results["mean"]["Date"],
        y=mc_results["mean"]["Headcount"],
        mode="lines",
        line=dict(color=COLORS["accent_teal"], width=3),
        name="Erwartungswert"
    ))

    fig.update_layout(
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(255,255,255,0)",
        height=500,
        hovermode="x unified",
        font={"color": COLORS["text_primary"]},
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.15,
            xanchor="center",
            x=0.5,
            bgcolor="#FFFFFF",
            bordercolor=COLORS["card_border"],
            borderwidth=1
        )
    )

    st.plotly_chart(fig, use_container_width=True)

    # FTE/Euro mit Konfidenzbändern
    if view_mode == "MAK":
        st.markdown("#### 📊 FTE Prognose mit Unsicherheit")
        y_metric = "FTE"
    else:
        st.markdown("#### 💰 Kosten Prognose mit Unsicherheit")
        y_metric = "Total_Cost"

    fig_fte = go.Figure()

    fig_fte.add_trace(go.Scatter(
        x=mc_results["p90"]["Date"],
        y=mc_results["p90"][y_metric],
        mode="lines",
        line=dict(width=0),
        showlegend=False,
        hoverinfo="skip"
    ))

    fig_fte.add_trace(go.Scatter(
        x=mc_results["p10"]["Date"],
        y=mc_results["p10"][y_metric],
        mode="lines",
        line=dict(width=0),
        fillcolor="rgba(0, 136, 222, 0.2)",
        fill="tonexty",
        name="10.-90. Perzentil",
        hoverinfo="skip"
    ))

    fig_fte.add_trace(go.Scatter(
        x=mc_results["mean"]["Date"],
        y=mc_results["mean"][y_metric],
        mode="lines",
        line=dict(color=COLORS["accent_teal"], width=3),
        name="Erwartungswert"
    ))

    fig_fte.update_layout(
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(255,255,255,0)",
        height=500,
        hovermode="x unified",
        font={"color": COLORS["text_primary"]},
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.15,
            xanchor="center",
            x=0.5,
            bgcolor="#FFFFFF",
            bordercolor=COLORS["card_border"],
            borderwidth=1
        )
    )

    st.plotly_chart(fig_fte, use_container_width=True)


def render_scenario_comparison():
    """Rendert Szenariovergleich A vs. B."""
    st.markdown("### ⚖️ Szenariovergleich")

    if (st.session_state["simulation_results_a"] is None or
        st.session_state["simulation_results_b"] is None):
        st.info("Führe beide Szenarien aus, um den Vergleich zu sehen.")
        return

    results_a = st.session_state["simulation_results_a"]
    results_b = st.session_state["simulation_results_b"]

    # Vergleichs-KPIs
    st.markdown("#### 📊 Vergleich Final-Werte")

    col1, col2, col3, col4 = st.columns(4)

    final_a = results_a.iloc[-1]
    final_b = results_b.iloc[-1]

    with col1:
        delta = final_b["Headcount"] - final_a["Headcount"]
        st.metric(
            label="Headcount",
            value=f"A: {final_a['Headcount']:.0f}",
            delta=f"B: {delta:+.0f}"
        )

    with col2:
        delta_fte = final_b["FTE"] - final_a["FTE"]
        st.metric(
            label="FTE",
            value=f"A: {final_a['FTE']:.1f}",
            delta=f"B: {delta_fte:+.1f}"
        )

    with col3:
        delta_vac = final_b["Vacancies"] - final_a["Vacancies"]
        st.metric(
            label="Vakanzen",
            value=f"A: {final_a['Vacancies']:.0f}",
            delta=f"B: {delta_vac:+.0f}",
            delta_color="inverse"
        )

    with col4:
        delta_cost = final_b["Total_Cost"] - final_a["Total_Cost"]
        st.metric(
            label="Kosten",
            value=f"A: {final_a['Total_Cost'] / 1_000_000:.1f}M €",
            delta=f"B: {delta_cost / 1_000_000:+.1f}M €"
        )

    st.divider()

    # Side-by-Side Charts
    import plotly.graph_objects as go

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 👥 Headcount Vergleich")

        fig_comp_hc = go.Figure()

        fig_comp_hc.add_trace(go.Scatter(
            x=results_a["Date"],
            y=results_a["Headcount"],
            mode="lines",
            line=dict(color=COLORS["accent_teal"], width=3),
            name="Szenario A"
        ))

        fig_comp_hc.add_trace(go.Scatter(
            x=results_b["Date"],
            y=results_b["Headcount"],
            mode="lines",
            line=dict(color=COLORS["accent_amber"], width=3),
            name="Szenario B"
        ))

        fig_comp_hc.update_layout(
            paper_bgcolor="rgba(255,255,255,0)",
            plot_bgcolor="rgba(255,255,255,0)",
            height=400,
            hovermode="x unified",
            font={"color": COLORS["text_primary"]},
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.15,
                xanchor="center",
                x=0.5,
                bgcolor="#FFFFFF",
                bordercolor=COLORS["card_border"],
                borderwidth=1
            )
        )

        st.plotly_chart(fig_comp_hc, use_container_width=True)

    with col2:
        st.markdown("#### 📊 FTE Vergleich")

        fig_comp_fte = go.Figure()

        fig_comp_fte.add_trace(go.Scatter(
            x=results_a["Date"],
            y=results_a["FTE"],
            mode="lines",
            line=dict(color=COLORS["accent_teal"], width=3),
            name="Szenario A"
        ))

        fig_comp_fte.add_trace(go.Scatter(
            x=results_b["Date"],
            y=results_b["FTE"],
            mode="lines",
            line=dict(color=COLORS["accent_amber"], width=3),
            name="Szenario B"
        ))

        fig_comp_fte.update_layout(
            paper_bgcolor="rgba(255,255,255,0)",
            plot_bgcolor="rgba(255,255,255,0)",
            height=400,
            hovermode="x unified",
            font={"color": COLORS["text_primary"]},
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.15,
                xanchor="center",
                x=0.5,
                bgcolor="#FFFFFF",
                bordercolor=COLORS["card_border"],
                borderwidth=1
            )
        )

        st.plotly_chart(fig_comp_fte, use_container_width=True)


# Main Content

# Tabs
tab1, tab2, tab3 = st.tabs(["🎯 Szenario A", "🎯 Szenario B", "⚖️ Vergleich"])

with tab1:
    st.markdown("## Szenario A: Basis-Szenario")

    params_a = render_parameter_editor("Basis-Szenario", "scenario_a")
    st.session_state["scenario_a_params"] = params_a

    st.divider()

    if st.button("▶️ Simulation A starten", key="run_scenario_a", type="primary"):
        with st.spinner("Simuliere Szenario A..."):
            cohort_defs = st.session_state.get("cohort_definitions", DEFAULT_COHORTS)

            if params_a.enable_monte_carlo:
                mc_results = run_monte_carlo(filtered_df, params_a, cohort_defs)
                st.session_state["simulation_results_a"] = mc_results["mean"]
                st.session_state["mc_results_a"] = mc_results
                st.success("✅ Monte-Carlo Simulation abgeschlossen!")
            else:
                results = simulate_workforce(filtered_df, params_a, cohort_defs)
                st.session_state["simulation_results_a"] = results
                st.session_state["mc_results_a"] = None
                st.success("✅ Simulation abgeschlossen!")

    st.divider()

    if st.session_state["simulation_results_a"] is not None:
        if st.session_state.get("mc_results_a") is not None:
            render_monte_carlo_results(st.session_state["mc_results_a"], "Szenario A", view_mode)
        else:
            render_simulation_results(
                st.session_state["simulation_results_a"],
                "Szenario A",
                view_mode
            )

        # Export
        csv = st.session_state["simulation_results_a"].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Ergebnisse als CSV exportieren",
            data=csv,
            file_name="simulation_szenario_a.csv",
            mime="text/csv"
        )

with tab2:
    st.markdown("## Szenario B: Alternatives Szenario")

    params_b = render_parameter_editor("Alternatives Szenario", "scenario_b")
    st.session_state["scenario_b_params"] = params_b

    st.divider()

    if st.button("▶️ Simulation B starten", key="run_scenario_b", type="primary"):
        with st.spinner("Simuliere Szenario B..."):
            cohort_defs = st.session_state.get("cohort_definitions", DEFAULT_COHORTS)

            if params_b.enable_monte_carlo:
                mc_results = run_monte_carlo(filtered_df, params_b, cohort_defs)
                st.session_state["simulation_results_b"] = mc_results["mean"]
                st.session_state["mc_results_b"] = mc_results
                st.success("✅ Monte-Carlo Simulation abgeschlossen!")
            else:
                results = simulate_workforce(filtered_df, params_b, cohort_defs)
                st.session_state["simulation_results_b"] = results
                st.session_state["mc_results_b"] = None
                st.success("✅ Simulation abgeschlossen!")

    st.divider()

    if st.session_state["simulation_results_b"] is not None:
        if st.session_state.get("mc_results_b") is not None:
            render_monte_carlo_results(st.session_state["mc_results_b"], "Szenario B", view_mode)
        else:
            render_simulation_results(
                st.session_state["simulation_results_b"],
                "Szenario B",
                view_mode
            )

        # Export
        csv = st.session_state["simulation_results_b"].to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Ergebnisse als CSV exportieren",
            data=csv,
            file_name="simulation_szenario_b.csv",
            mime="text/csv"
        )

with tab3:
    render_scenario_comparison()
