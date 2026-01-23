"""
Modul 7: Kompakt-Dashboard

Kondensierte Auswertungsansicht mit allen wichtigen IST und IST vs SOLL Analysen
in einer einzigen Seite mit Tabs.

Auswertungen gemäß Anforderungskatalog:
1. IST-MAK (FTE)
2. IST-Köpfe (Headcount)
3. IST-EUR (Jahreskosten)
4. IST vs SOLL MAK
5. IST vs SOLL EUR
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
import os

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from config.settings import (
    COLORS, COLOR_SEQUENCE, CHART_HEIGHTS,
    format_number, format_currency, format_percent,
    BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR, DEFAULT_COHORTS
)
from utils.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions


# =============================================================================
# KONSTANTEN
# =============================================================================

BREAKDOWN_DIMENSIONS_IST = {
    "Geschlecht": "Geschlecht",
    "Alterskohorten": "Alterskohorte",
    "Qualifikation": "Ausbildung",
    "ATZ-Status": "ATZ_Status",
    "Dauer im Unternehmen": "Betriebszugehörigkeit_Bin",
    "Vergütungsklassen": "Vergütungsklasse",
    "Beschäftigungsgrad": "Beschäftigungsgrad_Kat",
    "Beschäftigungsstatus": "Beschäftigungsstatus",
}

BREAKDOWN_DIMENSIONS_SOLL = {
    "Qualifikation": "Ausbildung",
    "Vergütungsklassen": "Vergütungsklasse",
}

# Themenfeld-Gruppierung für die Darstellung untereinander
THEMENFELDER_IST = {
    "👥 Demografie": [
        ("Geschlecht", "Geschlecht"),
        ("Alterskohorten", "Alterskohorte"),
    ],
    "🎓 Qualifikation & Beschäftigung": [
        ("Qualifikation", "Ausbildung"),
        ("Beschäftigungsgrad", "Beschäftigungsgrad_Kat"),
        ("Beschäftigungsstatus", "Beschäftigungsstatus"),
    ],
    "🏢 Unternehmenszugehörigkeit": [
        ("Dauer im Unternehmen", "Betriebszugehörigkeit_Bin"),
        ("ATZ-Status", "ATZ_Status"),
    ],
    "💰 Vergütung": [
        ("Vergütungsklassen", "Vergütungsklasse"),
    ],
}

THEMENFELDER_SOLL = {
    "🎓 Qualifikation": [
        ("Qualifikation", "Ausbildung"),
    ],
    "💰 Vergütung": [
        ("Vergütungsklassen", "Vergütungsklasse"),
    ],
}

TENURE_BINS = [0, 2, 5, 10, 20, 100]
TENURE_LABELS = ["0-2 J.", "2-5 J.", "5-10 J.", "10-20 J.", "20+ J."]

EMPLOYMENT_DEGREE_BINS = [0, 0.25, 0.50, 0.75, 0.95, 1.01]
EMPLOYMENT_DEGREE_LABELS = ["<25%", "25-50%", "50-75%", "75-95%", "Vollzeit"]

CHART_COLORS = [
    "#0088DE", "#00B9FC", "#10b981", "#f59e0b",
    "#E94D3A", "#8b5cf6", "#ec4899", "#06b6d4"
]

# Definierte Sortierreihenfolge für Alterskohorten
COHORT_ORDER = [
    "< 20 Jahre",
    "20-30 Jahre",
    "30-40 Jahre",
    "40-50 Jahre",
    "50-55 Jahre",
    "55-60 Jahre",
    "60-65 Jahre",
    "> 65 Jahre",
]


# =============================================================================
# KPI-KOMPONENTEN (Native Streamlit)
# =============================================================================

def render_kpi_cards(kpis: list):
    """
    Rendert KPI-Cards mit nativen Streamlit-Komponenten.

    Args:
        kpis: Liste von Dictionaries mit keys: title, value, subtitle, icon, delta (optional)
    """
    cols = st.columns(len(kpis))

    for col, kpi in zip(cols, kpis):
        with col:
            # Container für Card-Styling
            with st.container():
                # Icon und Titel
                st.markdown(f"**{kpi.get('icon', '📊')} {kpi['title']}**")

                # Hauptwert als große Metrik
                delta_value = kpi.get('delta')
                delta_str = None
                if delta_value is not None:
                    if isinstance(delta_value, str):
                        delta_str = delta_value
                    else:
                        delta_str = f"{delta_value:+.1f}%"

                st.metric(
                    label="",
                    value=kpi['value'],
                    delta=delta_str,
                    label_visibility="collapsed"
                )

                # Subtitle
                if kpi.get('subtitle'):
                    st.caption(kpi['subtitle'])


def render_kpi_cards_styled(kpis: list):
    """
    Alternative KPI-Darstellung mit mehr visueller Struktur.
    """
    cols = st.columns(len(kpis))

    for col, kpi in zip(cols, kpis):
        with col:
            # Farbiger oberer Rand basierend auf Status
            status = kpi.get('status', 'default')
            border_color = {
                'good': '#10b981',
                'warning': '#f59e0b',
                'critical': '#E94D3A',
                'default': '#0088DE'
            }.get(status, '#0088DE')

            st.markdown(
                f"""
                <div style="
                    background: linear-gradient(to bottom, #f8fafc, #ffffff);
                    border-radius: 12px;
                    padding: 1.25rem;
                    border: 1px solid #e2e8f0;
                    border-top: 4px solid {border_color};
                    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                ">
                    <div style="font-size: 0.8rem; color: #64748b; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px;">
                        {kpi.get('icon', '📊')} {kpi['title']}
                    </div>
                    <div style="font-size: 1.75rem; font-weight: 700; color: #1e293b; margin: 0.5rem 0;">
                        {kpi['value']}
                    </div>
                    <div style="font-size: 0.85rem; color: #94a3b8;">
                        {kpi.get('subtitle', '')}
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )


# =============================================================================
# DATENAUFBEREITUNG
# =============================================================================

@st.cache_data
def prepare_compact_data(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    """Bereitet Daten für die Kompakt-Ansicht vor."""
    df = snapshot_df.copy()

    # Jobfamily zuweisen
    try:
        definitions = load_jobfamily_definitions()
        if definitions and "Planstelle" in df.columns:
            df = assign_jobfamilies(df, definitions)
        else:
            df["Jobfamily"] = "(nicht zugeordnet)"
    except Exception:
        df["Jobfamily"] = "(nicht zugeordnet)"

    # Betriebszugehörigkeit-Bins
    if "Betriebszugehörigkeit_Jahre" in df.columns:
        df["Betriebszugehörigkeit_Bin"] = pd.cut(
            df["Betriebszugehörigkeit_Jahre"],
            bins=TENURE_BINS,
            labels=TENURE_LABELS,
            right=False
        )
    else:
        df["Betriebszugehörigkeit_Bin"] = "(unbekannt)"

    # Vergütungsklasse
    if "TrfGr" in df.columns and "St" in df.columns:
        df["Vergütungsklasse"] = df.apply(
            lambda row: f"{row['TrfGr']}/{int(row['St'])}"
            if pd.notna(row['TrfGr']) and pd.notna(row['St'])
            else (row.get("Bewertung Tarifgruppe", "(unbekannt)")
                  if pd.notna(row.get("Bewertung Tarifgruppe")) else "(unbekannt)"),
            axis=1
        )
    elif "Bewertung Tarifgruppe" in df.columns:
        df["Vergütungsklasse"] = df["Bewertung Tarifgruppe"].fillna("(unbekannt)")
    else:
        df["Vergütungsklasse"] = "(unbekannt)"

    # Beschäftigungsgrad-Kategorien
    if "FTE_person" in df.columns:
        df["Beschäftigungsgrad_Kat"] = pd.cut(
            df["FTE_person"].fillna(1.0),
            bins=EMPLOYMENT_DEGREE_BINS,
            labels=EMPLOYMENT_DEGREE_LABELS,
            right=False
        )
    elif "BsGrd" in df.columns:
        df["Beschäftigungsgrad_Kat"] = pd.cut(
            (df["BsGrd"] / 100).fillna(1.0),
            bins=EMPLOYMENT_DEGREE_BINS,
            labels=EMPLOYMENT_DEGREE_LABELS,
            right=False
        )
    else:
        df["Beschäftigungsgrad_Kat"] = "(unbekannt)"

    # Beschäftigungsstatus
    if "Vertragsart" in df.columns:
        df["Beschäftigungsstatus"] = df["Vertragsart"].fillna("(unbekannt)")
    elif "Status kundenindividuell" in df.columns:
        df["Beschäftigungsstatus"] = df["Status kundenindividuell"].fillna("(unbekannt)")
    else:
        df["Beschäftigungsstatus"] = "(unbekannt)"

    # SOLL-Kosten berechnen
    if "Soll_Cost_Year" not in df.columns:
        df["Soll_Cost_Year"] = df.apply(calculate_soll_cost, axis=1)

    return df


def calculate_soll_cost(row) -> float:
    """Berechnet SOLL-Kosten basierend auf Tarifgruppe/Step und SOLL-FTE."""
    try:
        soll_fte = row.get("Soll_FTE", 1.0)
        if pd.isna(soll_fte):
            soll_fte = 1.0

        tarif = row.get("TrfGr")
        if pd.isna(tarif):
            tarif = row.get("Bewertung Tarifgruppe")
        if pd.isna(tarif):
            tarif = "E9A"

        step = row.get("St")
        if pd.isna(step):
            step = 4
        step = int(step)

        base = BASE_SALARY.get(str(tarif), 52000)
        multiplier = STEP_MULTIPLIER.get(step, 1.0)

        return base * multiplier * EMPLOYER_COST_FACTOR * soll_fte
    except Exception:
        return 0.0


# =============================================================================
# BERECHNUNGSFUNKTIONEN
# =============================================================================

def get_ist_mak(df: pd.DataFrame) -> float:
    if "FTE_assigned" in df.columns:
        return df["FTE_assigned"].sum()
    elif "FTE_person" in df.columns:
        return df["FTE_person"].sum()
    return 0.0


def get_ist_koepfe(df: pd.DataFrame) -> int:
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df
    if "PersNr" in emp_df.columns:
        return emp_df["PersNr"].nunique()
    elif "Personalnummer" in emp_df.columns:
        return emp_df["Personalnummer"].nunique()
    return len(emp_df)


def get_ist_eur(df: pd.DataFrame) -> float:
    if "Total_Cost_Year" in df.columns:
        return df["Total_Cost_Year"].sum()
    return 0.0


def get_soll_mak(df: pd.DataFrame) -> float:
    if "Soll_FTE" in df.columns:
        return df["Soll_FTE"].sum()
    return 0.0


def get_soll_eur(df: pd.DataFrame) -> float:
    if "Soll_Cost_Year" in df.columns:
        return df["Soll_Cost_Year"].sum()
    return 0.0


def create_breakdown_table(df: pd.DataFrame, dimension_col: str, value_col: str,
                           include_soll: bool = False, soll_col: str = None) -> pd.DataFrame:
    """Erstellt eine Breakdown-Tabelle nach einer Dimension."""
    if dimension_col not in df.columns:
        return pd.DataFrame({"Hinweis": [f"Spalte '{dimension_col}' nicht verfügbar"]})

    if value_col == "Headcount":
        emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df
        id_col = "PersNr" if "PersNr" in emp_df.columns else "Personalnummer"
        if id_col in emp_df.columns:
            agg_df = emp_df.groupby(dimension_col)[id_col].nunique().reset_index()
            agg_df.columns = [dimension_col, "IST"]
        else:
            agg_df = emp_df.groupby(dimension_col).size().reset_index(name="IST")
    else:
        agg_df = df.groupby(dimension_col)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]

    if include_soll and soll_col and soll_col in df.columns:
        soll_agg = df.groupby(dimension_col)[soll_col].sum().reset_index()
        soll_agg.columns = [dimension_col, "SOLL"]
        agg_df = agg_df.merge(soll_agg, on=dimension_col, how="outer").fillna(0)
        agg_df["Delta"] = agg_df["IST"] - agg_df["SOLL"]
        agg_df["Erfüllungsgrad"] = agg_df.apply(
            lambda row: row["IST"] / row["SOLL"] if row["SOLL"] > 0 else 0, axis=1
        )

    total = agg_df["IST"].sum()
    agg_df["Anteil"] = agg_df["IST"] / total if total > 0 else 0

    # Spezielle Sortierung für Alterskohorten
    if dimension_col == "Alterskohorte":
        # Sortiere nach definierter Reihenfolge
        cohort_order_map = {cohort: i for i, cohort in enumerate(COHORT_ORDER)}
        agg_df["_sort_order"] = agg_df[dimension_col].map(
            lambda x: cohort_order_map.get(x, 999)
        )
        agg_df = agg_df.sort_values("_sort_order").drop(columns=["_sort_order"])
    else:
        # Standard: Nach Wert sortieren
        agg_df = agg_df.sort_values("IST", ascending=False)

    return agg_df


def export_to_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


# =============================================================================
# FILTER-FUNKTIONEN
# =============================================================================

def render_filter_for_jobfamilies(df: pd.DataFrame):
    """Rendert einen zusätzlichen Filter für Jobfamilies."""
    with st.sidebar:
        st.divider()
        st.subheader("💼 Jobfamilies")
        if "Jobfamily" in df.columns:
            jobfamilies = sorted([jf for jf in df["Jobfamily"].unique()
                                  if jf != "(nicht zugeordnet)"])
            if "selected_jobfamilies" not in st.session_state:
                st.session_state["selected_jobfamilies"] = []
            selected_jf = st.multiselect(
                "Jobfamilies auswählen",
                options=jobfamilies,
                default=st.session_state.get("selected_jobfamilies", []),
                key="jobfamily_select_compact",
                label_visibility="collapsed"
            )
            st.session_state["selected_jobfamilies"] = selected_jf


def apply_jobfamily_filter(df: pd.DataFrame) -> pd.DataFrame:
    if st.session_state.get("selected_jobfamilies"):
        return df[df["Jobfamily"].isin(st.session_state["selected_jobfamilies"])]
    return df


# =============================================================================
# CHART-FUNKTIONEN
# =============================================================================

def create_horizontal_bar_chart(df: pd.DataFrame, x_col: str, y_col: str,
                                 title: str = "",
                                 preserve_order: bool = False) -> go.Figure:
    """Erstellt ein horizontales Balkendiagramm mit allen Datenpunkten."""
    chart_df = df.copy()

    # Bei horizontalen Charts: immer umkehren für korrekte Darstellung
    # (Plotly zeigt erstes Element unten, wir wollen es oben)
    chart_df = chart_df.iloc[::-1]

    # Gradient-Farben
    n_bars = len(chart_df)
    colors = [f"rgba(0, 136, 222, {0.4 + (i * 0.5 / max(n_bars, 1))})" for i in range(n_bars)]

    fig = go.Figure(go.Bar(
        y=chart_df[x_col].astype(str),
        x=chart_df[y_col],
        orientation="h",
        marker=dict(
            color=colors,
            line=dict(color="#0088DE", width=1)
        ),
        text=chart_df[y_col].apply(
            lambda x: f"{x:,.1f}".replace(",", " ").replace(".", ",").replace(" ", ".")
            if isinstance(x, float) else f"{x:,}".replace(",", ".")
        ),
        textposition="outside",
        textfont=dict(size=11, color="#475569"),
        hovertemplate="<b>%{y}</b><br>Wert: %{x:,.2f}<extra></extra>"
    ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=80, t=50, b=30),
        height=max(300, n_bars * 35),
        showlegend=False,
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False),
        yaxis=dict(showgrid=False)
    )

    return fig


def create_donut_chart(df: pd.DataFrame, values_col: str, names_col: str,
                        title: str = "") -> go.Figure:
    """Erstellt ein Donut-Chart mit allen Datenpunkten."""
    chart_df = df.copy()

    # Erweiterte Farbpalette für mehr Kategorien
    extended_colors = CHART_COLORS * ((len(chart_df) // len(CHART_COLORS)) + 1)

    fig = go.Figure(go.Pie(
        values=chart_df[values_col],
        labels=chart_df[names_col],
        hole=0.55,
        marker=dict(
            colors=extended_colors[:len(chart_df)],
            line=dict(color="#ffffff", width=2)
        ),
        textinfo="percent",
        textposition="outside",
        textfont=dict(size=11),
        hovertemplate="<b>%{label}</b><br>%{value:,.1f}<br>%{percent}<extra></extra>",
        pull=[0.02] * len(chart_df)
    ))

    # Center Text
    total = chart_df[values_col].sum()
    fig.add_annotation(
        text=f"<b>{format_number(total, 1)}</b><br><span style='font-size:11px;color:#94a3b8'>Gesamt</span>",
        x=0.5, y=0.5,
        font=dict(size=20, color="#1e293b"),
        showarrow=False
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=50, b=20),
        height=350,
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.2,
            xanchor="center", x=0.5, font=dict(size=10)
        )
    )

    return fig


def create_comparison_chart(df: pd.DataFrame, dimension_col: str,
                             title: str = "") -> go.Figure:
    """Erstellt ein Vergleichs-Balkendiagramm für IST vs SOLL mit allen Datenpunkten."""
    chart_df = df.copy().iloc[::-1]
    n_bars = len(chart_df)

    fig = go.Figure()

    # IST Bars
    fig.add_trace(go.Bar(
        y=chart_df[dimension_col].astype(str),
        x=chart_df["IST"],
        name="IST",
        orientation="h",
        marker=dict(color="#0088DE"),
        text=chart_df["IST"].apply(lambda x: f"{x:,.1f}".replace(",", ".")),
        textposition="outside",
        textfont=dict(size=10)
    ))

    # SOLL Bars
    if "SOLL" in chart_df.columns:
        fig.add_trace(go.Bar(
            y=chart_df[dimension_col].astype(str),
            x=chart_df["SOLL"],
            name="SOLL",
            orientation="h",
            marker=dict(color="#E94D3A"),
            text=chart_df["SOLL"].apply(lambda x: f"{x:,.1f}".replace(",", ".")),
            textposition="outside",
            textfont=dict(size=10)
        ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#1e293b"), x=0),
        barmode="group",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=80, t=50, b=30),
        height=max(350, n_bars * 40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False),
        yaxis=dict(showgrid=False)
    )

    return fig


# =============================================================================
# TABELLEN-FORMATIERUNG
# =============================================================================

def format_dataframe_for_display(df: pd.DataFrame, value_type: str = "mak") -> pd.DataFrame:
    """Formatiert DataFrame für die Anzeige."""
    display_df = df.copy()

    # Dimension-Spalte umbenennen
    first_col = display_df.columns[0]
    display_df = display_df.rename(columns={first_col: "Kategorie"})

    # Formatierung der Werte
    for col in display_df.columns:
        if col in ["IST", "SOLL"]:
            if value_type == "eur":
                display_df[col] = display_df[col].apply(
                    lambda x: format_currency(x) if pd.notna(x) else "-"
                )
            elif value_type == "koepfe":
                display_df[col] = display_df[col].apply(
                    lambda x: f"{int(x):,}".replace(",", ".") if pd.notna(x) else "-"
                )
            else:  # mak
                display_df[col] = display_df[col].apply(
                    lambda x: f"{x:,.1f}".replace(",", " ").replace(".", ",").replace(" ", ".")
                    if pd.notna(x) else "-"
                )
        elif col == "Delta":
            if value_type == "eur":
                display_df[col] = display_df[col].apply(
                    lambda x: f"{'+'if x>0 else ''}{format_currency(x)}" if pd.notna(x) else "-"
                )
            else:
                display_df[col] = display_df[col].apply(
                    lambda x: f"{x:+,.1f}".replace(",", " ").replace(".", ",").replace(" ", ".")
                    if pd.notna(x) else "-"
                )
        elif col in ["Anteil", "Erfüllungsgrad"]:
            display_df[col] = display_df[col].apply(
                lambda x: f"{x*100:.1f}%".replace(".", ",") if pd.notna(x) else "-"
            )

    return display_df


# =============================================================================
# TAB-RENDERING FUNKTIONEN
# =============================================================================

def render_single_breakdown(df: pd.DataFrame, dimension_name: str, dimension_col: str,
                            value_col: str, value_type: str = "mak", key_prefix: str = ""):
    """
    Rendert einen einzelnen Breakdown-Block mit Chart und Tabelle.

    Args:
        df: DataFrame mit Daten
        dimension_name: Anzeigename der Dimension
        dimension_col: Spaltenname der Dimension
        value_col: Spaltenname für Werte (z.B. "FTE_assigned", "Headcount", "Total_Cost_Year")
        value_type: Typ für Formatierung ("mak", "koepfe", "eur")
        key_prefix: Prefix für Streamlit-Keys
    """
    if dimension_col not in df.columns:
        st.warning(f"Dimension '{dimension_name}' nicht verfügbar (Spalte '{dimension_col}' fehlt).")
        return

    # Breakdown erstellen
    breakdown_df = create_breakdown_table(df, dimension_col, value_col)

    if breakdown_df.empty or "Hinweis" in breakdown_df.columns:
        st.warning(f"Keine Daten für '{dimension_name}' verfügbar.")
        return

    st.subheader(f"📊 {dimension_name}")

    # Chart und Tabelle nebeneinander
    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        # Für Alterskohorten: Reihenfolge beibehalten
        preserve = (dimension_name == "Alterskohorten")

        fig = create_horizontal_bar_chart(
            breakdown_df, dimension_col, "IST",
            title="",
            preserve_order=preserve
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_table:
        st.markdown("**Datentabelle**")
        display_df = format_dataframe_for_display(breakdown_df, value_type)
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        # Export
        csv_data = export_to_csv(breakdown_df)
        st.download_button(
            label="📥 CSV Download",
            data=csv_data,
            file_name=f"{key_prefix}_{dimension_name.lower().replace(' ', '_')}.csv",
            mime="text/csv",
            key=f"download_{key_prefix}_{dimension_col}",
            use_container_width=True
        )


def render_single_comparison(df: pd.DataFrame, dimension_name: str, dimension_col: str,
                             ist_col: str, soll_col: str, value_type: str = "mak",
                             key_prefix: str = ""):
    """
    Rendert einen einzelnen IST vs SOLL Vergleichs-Block.
    """
    if dimension_col not in df.columns:
        st.warning(f"Dimension '{dimension_name}' nicht verfügbar.")
        return

    breakdown_df = create_breakdown_table(df, dimension_col, ist_col,
                                          include_soll=True, soll_col=soll_col)

    if breakdown_df.empty or "Hinweis" in breakdown_df.columns:
        st.warning(f"Keine Daten für '{dimension_name}' verfügbar.")
        return

    st.subheader(f"📊 {dimension_name}")

    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        fig = create_comparison_chart(breakdown_df, dimension_col, title="")
        st.plotly_chart(fig, use_container_width=True)

    with col_table:
        st.markdown("**Datentabelle**")
        display_df = format_dataframe_for_display(breakdown_df, value_type)
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        csv_data = export_to_csv(breakdown_df)
        st.download_button(
            label="📥 CSV Download",
            data=csv_data,
            file_name=f"{key_prefix}_{dimension_name.lower().replace(' ', '_')}.csv",
            mime="text/csv",
            key=f"download_{key_prefix}_{dimension_col}",
            use_container_width=True
        )


def render_ist_mak_tab(df: pd.DataFrame):
    """Rendert den IST-MAK Tab mit allen Themenfeldern untereinander."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    # KPIs berechnen
    total_mak = get_ist_mak(emp_df)
    total_koepfe = get_ist_koepfe(emp_df)
    teilzeit_count = (emp_df["Arbeitszeit"] == "Teilzeit").sum() if "Arbeitszeit" in emp_df.columns else 0
    teilzeit_rate = teilzeit_count / len(emp_df) if len(emp_df) > 0 else 0
    avg_fte = total_mak / total_koepfe if total_koepfe > 0 else 0

    # KPI-Row mit styled cards
    kpis = [
        {"title": "Gesamt MAK", "value": format_number(total_mak, 1),
         "subtitle": f"{total_koepfe} Mitarbeitende", "icon": "📊", "status": "good"},
        {"title": "Durchschnitt FTE", "value": format_number(avg_fte, 2),
         "subtitle": "pro Mitarbeitenden", "icon": "👤", "status": "default"},
        {"title": "Teilzeit-Quote", "value": format_percent(teilzeit_rate),
         "subtitle": f"{teilzeit_count} Teilzeit-MA", "icon": "⏰", "status": "default"},
    ]
    render_kpi_cards_styled(kpis)

    st.markdown("---")

    # Alle Themenfelder untereinander darstellen
    for themenfeld, dimensionen in THEMENFELDER_IST.items():
        st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col="FTE_assigned",
                value_type="mak",
                key_prefix="ist_mak"
            )
            st.markdown("---")


def render_ist_koepfe_tab(df: pd.DataFrame):
    """Rendert den IST-Köpfe Tab mit allen Themenfeldern untereinander."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    # KPIs
    total_koepfe = get_ist_koepfe(emp_df)
    female_count = (emp_df["Geschlecht"] == "w").sum() if "Geschlecht" in emp_df.columns else 0
    female_rate = female_count / total_koepfe if total_koepfe > 0 else 0
    atz_count = (emp_df["ATZ_Status"] != "Kein ATZ").sum() if "ATZ_Status" in emp_df.columns else 0
    atz_rate = atz_count / total_koepfe if total_koepfe > 0 else 0

    kpis = [
        {"title": "Gesamt Köpfe", "value": format_number(total_koepfe, 0),
         "subtitle": "Mitarbeitende", "icon": "👥", "status": "good"},
        {"title": "Frauen-Anteil", "value": format_percent(female_rate),
         "subtitle": f"{female_count} Frauen", "icon": "♀", "status": "default"},
        {"title": "ATZ-Quote", "value": format_percent(atz_rate),
         "subtitle": f"{atz_count} in ATZ", "icon": "🔄", "status": "default"},
    ]
    render_kpi_cards_styled(kpis)

    st.markdown("---")

    # Alle Themenfelder untereinander darstellen
    for themenfeld, dimensionen in THEMENFELDER_IST.items():
        st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col="Headcount",
                value_type="koepfe",
                key_prefix="ist_koepfe"
            )
            st.markdown("---")


def render_ist_eur_tab(df: pd.DataFrame):
    """Rendert den IST-EUR Tab mit allen Themenfeldern untereinander."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    if "Total_Cost_Year" not in emp_df.columns:
        st.warning("Kostenfeld 'Total_Cost_Year' nicht verfügbar.")
        return

    # KPIs
    total_cost = get_ist_eur(emp_df)
    total_koepfe = get_ist_koepfe(emp_df)
    avg_cost = total_cost / total_koepfe if total_koepfe > 0 else 0
    total_mak = get_ist_mak(emp_df)
    cost_per_mak = total_cost / total_mak if total_mak > 0 else 0

    kpis = [
        {"title": "Gesamt Kosten", "value": format_currency(total_cost),
         "subtitle": "Jahreskosten", "icon": "💰", "status": "good"},
        {"title": "Kosten/Kopf", "value": format_currency(avg_cost),
         "subtitle": "Durchschnitt", "icon": "👤", "status": "default"},
        {"title": "Kosten/MAK", "value": format_currency(cost_per_mak),
         "subtitle": "pro FTE", "icon": "📊", "status": "default"},
    ]
    render_kpi_cards_styled(kpis)

    st.markdown("---")

    # Alle Themenfelder untereinander darstellen
    for themenfeld, dimensionen in THEMENFELDER_IST.items():
        st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col="Total_Cost_Year",
                value_type="eur",
                key_prefix="ist_eur"
            )
            st.markdown("---")


def render_ist_vs_soll_mak_tab(df: pd.DataFrame):
    """Rendert den IST vs SOLL MAK Tab mit allen Themenfeldern untereinander."""
    if "Soll_FTE" not in df.columns:
        st.warning("SOLL-FTE nicht verfügbar.")
        return

    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_ist = get_ist_mak(emp_df)
    total_soll = get_soll_mak(df)
    delta = total_ist - total_soll
    erfuellungsgrad = total_ist / total_soll if total_soll > 0 else 0

    status = "good" if erfuellungsgrad >= 0.95 else ("warning" if erfuellungsgrad >= 0.85 else "critical")

    kpis = [
        {"title": "IST-MAK", "value": format_number(total_ist, 1),
         "subtitle": "Tatsächliche Kapazität", "icon": "📊", "status": "default"},
        {"title": "SOLL-MAK", "value": format_number(total_soll, 1),
         "subtitle": "Geplante Kapazität", "icon": "🎯", "status": "default"},
        {"title": "Delta", "value": f"{delta:+.1f}".replace(".", ","),
         "subtitle": "IST - SOLL", "icon": "📉" if delta < 0 else "📈", "status": status},
        {"title": "Erfüllungsgrad", "value": format_percent(erfuellungsgrad),
         "subtitle": "IST / SOLL", "icon": "✅", "status": status},
    ]
    render_kpi_cards_styled(kpis)

    st.markdown("---")

    # Alle Themenfelder für SOLL-Vergleich untereinander darstellen
    for themenfeld, dimensionen in THEMENFELDER_SOLL.items():
        st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            render_single_comparison(
                df, dimension_name, dimension_col,
                ist_col="FTE_assigned",
                soll_col="Soll_FTE",
                value_type="mak",
                key_prefix="ist_vs_soll_mak"
            )
            st.markdown("---")


def render_ist_vs_soll_eur_tab(df: pd.DataFrame):
    """Rendert den IST vs SOLL EUR Tab mit allen Themenfeldern untereinander."""
    if "Total_Cost_Year" not in df.columns:
        st.warning("IST-Kosten nicht verfügbar.")
        return

    if "Soll_Cost_Year" not in df.columns:
        st.info("SOLL-Kosten werden aus Tarifgruppe/Step geschätzt.")

    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_ist = get_ist_eur(emp_df)
    total_soll = get_soll_eur(df)
    delta = total_ist - total_soll
    erfuellungsgrad = total_ist / total_soll if total_soll > 0 else 0

    status = "good" if delta <= 0 else ("warning" if erfuellungsgrad <= 1.05 else "critical")

    kpis = [
        {"title": "IST-EUR", "value": format_currency(total_ist),
         "subtitle": "Tatsächliche Kosten", "icon": "💰", "status": "default"},
        {"title": "SOLL-EUR", "value": format_currency(total_soll),
         "subtitle": "Geplante Kosten", "icon": "🎯", "status": "default"},
        {"title": "Delta", "value": format_currency(abs(delta)),
         "subtitle": "Überbudget" if delta > 0 else "Unterbudget",
         "icon": "📉" if delta > 0 else "📈", "status": status},
        {"title": "Kostenquote", "value": format_percent(erfuellungsgrad),
         "subtitle": "IST / SOLL", "icon": "📊", "status": status},
    ]
    render_kpi_cards_styled(kpis)

    st.markdown("---")

    # Alle Themenfelder für SOLL-Vergleich untereinander darstellen
    for themenfeld, dimensionen in THEMENFELDER_SOLL.items():
        st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            render_single_comparison(
                df, dimension_name, dimension_col,
                ist_col="Total_Cost_Year",
                soll_col="Soll_Cost_Year",
                value_type="eur",
                key_prefix="ist_vs_soll_eur"
            )
            st.markdown("---")


# =============================================================================
# HAUPTFUNKTION
# =============================================================================

def main():
    """Hauptfunktion für die Kompakt-Seite."""

    st.title("⚡ Kompakt-Dashboard")
    st.caption("Alle wichtigen IST und IST vs SOLL Auswertungen auf einen Blick")

    try:
        # Daten laden
        snapshot_df, history_df, org_df, summary = load_and_prepare_data()
        prepared_df = prepare_compact_data(snapshot_df)

        # Filter rendern
        render_global_filters(prepared_df, history_df)
        render_filter_for_jobfamilies(prepared_df)

        # Filter anwenden
        filtered_df = apply_filters(prepared_df)
        filtered_df = apply_jobfamily_filter(filtered_df)

        # Filter-Summary
        filter_summary = get_filter_summary()
        jf_selected = st.session_state.get("selected_jobfamilies", [])
        if jf_selected:
            filter_summary += f" | {len(jf_selected)} Jobfamilies"

        st.info(f"🎯 {filter_summary}")

        # Prüfe Daten
        if len(filtered_df) == 0:
            st.warning("Keine Daten für die gewählten Filter.")
            return

        # Tabs
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "📊 IST-MAK",
            "👥 IST-Köpfe",
            "💰 IST-EUR",
            "🎯 IST vs SOLL MAK",
            "💶 IST vs SOLL EUR"
        ])

        with tab1:
            render_ist_mak_tab(filtered_df)

        with tab2:
            render_ist_koepfe_tab(filtered_df)

        with tab3:
            render_ist_eur_tab(filtered_df)

        with tab4:
            render_ist_vs_soll_mak_tab(filtered_df)

        with tab5:
            render_ist_vs_soll_eur_tab(filtered_df)

    except FileNotFoundError as e:
        st.error(f"Datenfehler: {str(e)}")
    except Exception as e:
        st.error(f"Fehler: {str(e)}")
        st.exception(e)


if __name__ == "__main__":
    main()
