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
from typing import Dict

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from components.ui_compat import dataframe_compat, download_button_compat
from dataloader.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters, get_filter_summary
from config.settings import (
    COLORS, COLOR_SEQUENCE, CHART_HEIGHTS,
    format_number, format_currency, format_percent,
    BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR, DEFAULT_COHORTS,
    EDUCATION_GROUPS, EDUCATION_HIERARCHY,
)
from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions

# Scroll Navigation
try:
    from streamlit_scroll_navigation import scroll_navbar
    SCROLL_NAV_AVAILABLE = True
except ImportError:
    SCROLL_NAV_AVAILABLE = False
    st.warning("⚠️ streamlit-scroll-navigation nicht installiert. Führen Sie 'pip install streamlit-scroll-navigation' aus.")


# =============================================================================
# KONSTANTEN
# =============================================================================

# =============================================================================
# ZENTRALE CHART-REIHENFOLGE
# =============================================================================
# Alle Tabs verwenden dieselbe Reihenfolge. SOLL-Tabs zeigen eine Teilmenge,
# behalten aber die relative Position bei.
#
# Jeder Eintrag: (Themenfeld-Kategorie, Anzeigename, DataFrame-Spalte, in_soll)
# in_soll=True markiert Dimensionen, die auch in IST vs SOLL Tabs erscheinen.

CHART_ORDER = [
    # --- Demografie ---
    ("👥 Demografie",                     "Geschlecht",           "Geschlecht",               False),
    ("👥 Demografie",                     "Alterskohorten",       "Alterskohorte",             False),
    # --- Qualifikation & Beschäftigung ---
    ("🎓 Qualifikation & Beschäftigung",  "Qualifikation",        "Ausbildung",                True),
    ("🎓 Qualifikation & Beschäftigung",  "Beschäftigungsgrad",   "Beschäftigungsgrad_Kat",    False),
    ("🎓 Qualifikation & Beschäftigung",  "Beschäftigungsstatus", "Beschäftigungsstatus",      False),
    # --- Unternehmenszugehörigkeit ---
    ("🏢 Unternehmenszugehörigkeit",      "Dauer im Unternehmen", "Betriebszugehörigkeit_Bin", False),
    ("🏢 Unternehmenszugehörigkeit",      "ATZ-Status",           "ATZ_Status",                False),
    # --- Vergütung ---
    ("💰 Vergütung",                      "Vergütungsklassen",    "Vergütungsklasse",          True),
]


def _build_themenfelder(soll_only: bool = False):
    """Leitet Themenfeld-Gruppierung aus CHART_ORDER ab."""
    from collections import OrderedDict
    result = OrderedDict()
    for kategorie, name, col, in_soll in CHART_ORDER:
        if soll_only and not in_soll:
            continue
        # Für SOLL-Tabs: Kategorie-Label ohne Sub-Beschreibung
        label = kategorie
        if label not in result:
            result[label] = []
        result[label].append((name, col))
    return dict(result)


THEMENFELDER_IST = _build_themenfelder(soll_only=False)
THEMENFELDER_SOLL = _build_themenfelder(soll_only=True)

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

# Ordinale Sortierreihenfolgen für alle Dimensionen mit natürlicher Ordnung
# Schlüssel = Spaltenname im DataFrame, Wert = Liste der Kategorien in Reihenfolge
ORDINAL_ORDERS: Dict[str, list] = {
    "Alterskohorte": COHORT_ORDER,
    "Betriebszugehörigkeit_Bin": TENURE_LABELS,  # ["0-2 J.", "2-5 J.", "5-10 J.", "10-20 J.", "20+ J."]
    "Beschäftigungsgrad_Kat": EMPLOYMENT_DEGREE_LABELS,  # ["<25%", "25-50%", "50-75%", "75-95%", "Vollzeit"]
    "Ausbildung": EDUCATION_GROUPS,  # Reihenfolge aus settings.py (aufsteigend nach Qualifikation)
}


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
        def format_verguetungsklasse(row):
            """Formatiert Vergütungsklasse, behandelt '2+' und ähnliche Werte."""
            if pd.notna(row['TrfGr']) and pd.notna(row['St']):
                # Bereinige Stufe (entferne '+', '-', etc.)
                step_str = str(row['St']).strip().replace("+", "").replace("-", "")
                try:
                    step_int = int(step_str)
                    return f"{row['TrfGr']}/{step_int}"
                except (ValueError, TypeError):
                    # Fallback: zeige ursprünglichen Wert
                    return f"{row['TrfGr']}/{row['St']}"
            else:
                fallback = row.get("Bewertung Tarifgruppe", "(unbekannt)")
                return fallback if pd.notna(fallback) else "(unbekannt)"

        df["Vergütungsklasse"] = df.apply(format_verguetungsklasse, axis=1)
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
    """Berechnet SOLL-Kosten basierend auf Tarifgruppe/Step und SOLL-FTE.

    Nutzt TVÖD-Lookup aus session_state wenn verfügbar, sonst Fallback.
    """
    from dataloader.tvoed_loader import get_annual_salary, get_special_salary

    try:
        soll_fte = row.get("Soll_FTE", 1.0)
        if pd.isna(soll_fte):
            soll_fte = 1.0

        tarif = row.get("TrfGr")
        if pd.isna(tarif):
            tarif = row.get("Bewertung Tarifgruppe")
        if pd.isna(tarif):
            tarif = "E9A"

        tarif = str(tarif).strip().upper().replace(" ", "")

        step = row.get("St")
        if pd.isna(step):
            step = 4

        # Stufe bereinigen (falls "2+" → 2, "3+" → 3, etc.)
        step_str = str(step).strip().replace("+", "").replace("-", "")
        try:
            step = int(step_str)
        except (ValueError, TypeError):
            step = 4  # Fallback

        employer_factor = st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR)

        # Sonderfälle (Azubi, Vorstand)
        special = get_special_salary(tarif)
        if special is not None:
            return special * soll_fte * employer_factor

        # TVÖD-Lookup mit Fallback
        tvoed_lookup = st.session_state.get("tvoed_lookup", {})
        annual = get_annual_salary(
            tvoed_lookup, tarif, step,
            BASE_SALARY, STEP_MULTIPLIER
        )

        return annual * employer_factor * soll_fte
    except Exception:
        return 0.0


# =============================================================================
# BERECHNUNGSFUNKTIONEN
# =============================================================================

def get_ist_mak(df: pd.DataFrame) -> float:
    """IST-MAK = FTE effektiv (MAK) auf unique Mitarbeitende, Readme-konform."""
    from dataloader.kpi_engine import compute_fte_effektiv
    return compute_fte_effektiv(df)


def get_ist_koepfe(df: pd.DataFrame) -> int:
    """IST-Köpfe = unique PersNr (besetzte Planstellen), Readme-konform."""
    from dataloader.kpi_engine import compute_headcount
    return compute_headcount(df)


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
    elif value_col in ("MAK", "FTE_person") and "PersNr" in df.columns:
        # Person-level Metriken: Deduplizieren nach PersNr (erste Planstelle)
        from dataloader.kpi_engine import get_unique_employees
        emp_unique = get_unique_employees(df)
        if dimension_col in emp_unique.columns and value_col in emp_unique.columns:
            agg_df = emp_unique.groupby(dimension_col)[value_col].sum().reset_index()
        else:
            agg_df = df.groupby(dimension_col)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]
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

    # Ordinale Sortierung für Dimensionen mit natürlicher Reihenfolge
    if dimension_col in ORDINAL_ORDERS:
        order_list = ORDINAL_ORDERS[dimension_col]
        order_map = {val: i for i, val in enumerate(order_list)}
        agg_df["_sort_order"] = agg_df[dimension_col].map(
            lambda x: order_map.get(str(x), 999)
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

# Job Family Filter ist jetzt global in Sidebar (siehe components/sidebar.py)
# Lokale Filter-Funktionen wurden entfernt - alle Filterung erfolgt über apply_filters()


# =============================================================================
# CHART-FUNKTIONEN
# =============================================================================

def create_horizontal_bar_chart(df: pd.DataFrame, x_col: str, y_col: str,
                                 title: str = "",
                                 preserve_order: bool = False,
                                 print_mode: bool = False) -> go.Figure:
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

    height = max(300, n_bars * 35)
    if print_mode:
        height = min(height, 700)

    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=80, t=50, b=30),
        height=height,
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


STEP_COLORS = {
    1: "#B3E0FF",  # Hellblau
    2: "#66C2FF",  # Mittel-Hellblau
    3: "#33AAFF",  # Mittelblau
    4: "#0088DE",  # Blau
    5: "#0066A8",  # Dunkelblau
    6: "#004471",  # Sehr Dunkelblau
}


def create_stacked_tariff_chart(
    df: pd.DataFrame,
    value_col: str,
    title: str = "",
    print_mode: bool = False,
    value_type: str = "mak",
) -> go.Figure:
    """
    Erstellt ein gestapeltes horizontales Balkendiagramm nach Entgeltgruppe und Stufe.

    Y-Achse: Entgeltgruppen (E6, E7, E8, E9A, ...)
    Stapel: Erfahrungsstufen (1-6)
    Wert: Summe von value_col je (TrfGr, St)
    """
    if "TrfGr" not in df.columns or "St" not in df.columns:
        return create_horizontal_bar_chart(
            pd.DataFrame({"Vergütungsklasse": ["N/A"], "IST": [0]}),
            "Vergütungsklasse", "IST", title=title, print_mode=print_mode,
        )

    work_df = df.copy()
    work_df["TrfGr_clean"] = work_df["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    work_df["St_clean"] = work_df["St"].apply(
        lambda x: int(str(x).strip().replace("+", "").replace("-", "")) if pd.notna(x) else 4
    )

    # Aggregieren: Headcount vs. numerische Spalten
    if value_col == "Headcount":
        id_col = "PersNr" if "PersNr" in work_df.columns else "Personalnummer"
        pivot = (
            work_df[work_df["Is_Vacant"] == False]
            .groupby(["TrfGr_clean", "St_clean"])[id_col]
            .nunique()
            .reset_index(name="Wert")
        )
    else:
        pivot = (
            work_df.groupby(["TrfGr_clean", "St_clean"])[value_col]
            .sum()
            .reset_index(name="Wert")
        )

    # Sortierung der Tarifgruppen
    from config.settings import TARIFF_GROUPS
    group_order = {g: i for i, g in enumerate(TARIFF_GROUPS)}
    present_groups = sorted(
        pivot["TrfGr_clean"].unique(),
        key=lambda g: group_order.get(g, 999),
    )

    fig = go.Figure()

    # Hover/Tick-Format je nach Werttyp
    if value_type == "eur":
        x_tickformat = ",.0f"
    elif value_type == "koepfe":
        x_tickformat = ",.0f"
    else:  # mak
        x_tickformat = ",.1f"

    for step in sorted(pivot["St_clean"].unique()):
        step_data = pivot[pivot["St_clean"] == step]
        # Sicherstellen, dass alle Gruppen vorhanden sind (ggf. mit 0)
        step_vals = {row["TrfGr_clean"]: row["Wert"] for _, row in step_data.iterrows()}
        values = [step_vals.get(g, 0) for g in present_groups]

        if value_type == "eur":
            hover = f"<b>%{{y}}</b> Stufe {step}<br>Kosten: %{{x:,.0f}} €<extra></extra>"
        elif value_type == "koepfe":
            hover = f"<b>%{{y}}</b> Stufe {step}<br>Köpfe: %{{x:,.0f}}<extra></extra>"
        else:
            hover = f"<b>%{{y}}</b> Stufe {step}<br>MAK: %{{x:,.2f}}<extra></extra>"

        fig.add_trace(go.Bar(
            y=present_groups,
            x=values,
            name=f"Stufe {step}",
            orientation="h",
            marker=dict(
                color=STEP_COLORS.get(step, "#94a3b8"),
                line=dict(color="white", width=0.5),
            ),
            hovertemplate=hover,
        ))

    n_groups = len(present_groups)
    height = max(350, n_groups * 40)
    if print_mode:
        height = min(height, 700)

    fig.update_layout(
        barmode="stack",
        title=dict(text=title, font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=40, t=50, b=30),
        height=height,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1, font=dict(size=10),
            title_text="Erfahrungsstufe",
        ),
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False, tickformat=x_tickformat),
        yaxis=dict(showgrid=False, categoryorder="array", categoryarray=present_groups),
    )

    return fig


def create_stacked_tariff_breakdown_table(
    df: pd.DataFrame,
    value_col: str,
) -> pd.DataFrame:
    """
    Erstellt eine Breakdown-Tabelle nach Entgeltgruppe mit Stufen als Spalten.
    """
    if "TrfGr" not in df.columns or "St" not in df.columns:
        return pd.DataFrame({"Hinweis": ["TrfGr/St Spalten nicht verfügbar"]})

    work_df = df.copy()
    work_df["TrfGr_clean"] = work_df["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    work_df["St_clean"] = work_df["St"].apply(
        lambda x: int(str(x).strip().replace("+", "").replace("-", "")) if pd.notna(x) else 4
    )

    if value_col == "Headcount":
        id_col = "PersNr" if "PersNr" in work_df.columns else "Personalnummer"
        pivot = (
            work_df[work_df["Is_Vacant"] == False]
            .groupby(["TrfGr_clean", "St_clean"])[id_col]
            .nunique()
            .unstack(fill_value=0)
        )
    else:
        pivot = (
            work_df.groupby(["TrfGr_clean", "St_clean"])[value_col]
            .sum()
            .unstack(fill_value=0)
        )

    pivot.columns = [f"Stufe {int(c)}" for c in pivot.columns]
    pivot["Gesamt"] = pivot.sum(axis=1)

    from config.settings import TARIFF_GROUPS
    group_order = {g: i for i, g in enumerate(TARIFF_GROUPS)}
    pivot = pivot.reset_index().rename(columns={"TrfGr_clean": "Entgeltgruppe"})
    pivot["_sort"] = pivot["Entgeltgruppe"].map(lambda g: group_order.get(g, 999))
    pivot = pivot.sort_values("_sort").drop(columns=["_sort"])

    return pivot


def create_stacked_tariff_comparison_chart(
    df: pd.DataFrame,
    ist_col: str,
    soll_col: str,
    title: str = "",
    print_mode: bool = False,
    value_type: str = "mak",
) -> go.Figure:
    """
    Erstellt gestapeltes Vergütungs-Chart für IST vs SOLL Vergleich.

    Zeigt zwei gestapelte Balken pro Entgeltgruppe (IST und SOLL).
    """
    if "TrfGr" not in df.columns or "St" not in df.columns:
        return go.Figure()

    work_df = df.copy()
    work_df["TrfGr_clean"] = work_df["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    work_df["St_clean"] = work_df["St"].apply(
        lambda x: int(str(x).strip().replace("+", "").replace("-", "")) if pd.notna(x) else 4
    )

    from config.settings import TARIFF_GROUPS
    group_order = {g: i for i, g in enumerate(TARIFF_GROUPS)}

    # Tick-Format je nach Werttyp
    if value_type == "eur":
        soll_hover = "<b>%{y}</b> SOLL<br>Kosten: %{x:,.0f} €<extra></extra>"
        x_tickformat = ",.0f"
    elif value_type == "koepfe":
        soll_hover = "<b>%{y}</b> SOLL<br>Köpfe: %{x:,.0f}<extra></extra>"
        x_tickformat = ",.0f"
    else:  # mak
        soll_hover = "<b>%{y}</b> SOLL<br>MAK: %{x:,.2f}<extra></extra>"
        x_tickformat = ",.1f"

    # IST aggregieren
    ist_pivot = work_df.groupby(["TrfGr_clean", "St_clean"])[ist_col].sum().reset_index(name="Wert")
    ist_totals = ist_pivot.groupby("TrfGr_clean")["Wert"].sum()

    # SOLL aggregieren (nach Bewertung Tarifgruppe, da SOLL auch für Vakanzen gilt)
    soll_totals = work_df.groupby("TrfGr_clean")[soll_col].sum()

    present_groups = sorted(
        set(ist_totals.index) | set(soll_totals.index),
        key=lambda g: group_order.get(g, 999),
    )

    fig = go.Figure()

    # IST als gestapelte Balken
    steps = sorted(ist_pivot["St_clean"].unique())
    for step in steps:
        step_data = ist_pivot[ist_pivot["St_clean"] == step]
        step_vals = {row["TrfGr_clean"]: row["Wert"] for _, row in step_data.iterrows()}
        values = [step_vals.get(g, 0) for g in present_groups]

        if value_type == "eur":
            ist_hover = f"<b>%{{y}}</b> IST Stufe {step}<br>Kosten: %{{x:,.0f}} €<extra></extra>"
        elif value_type == "koepfe":
            ist_hover = f"<b>%{{y}}</b> IST Stufe {step}<br>Köpfe: %{{x:,.0f}}<extra></extra>"
        else:
            ist_hover = f"<b>%{{y}}</b> IST Stufe {step}<br>MAK: %{{x:,.2f}}<extra></extra>"

        fig.add_trace(go.Bar(
            y=present_groups,
            x=values,
            name=f"IST St.{step}",
            orientation="h",
            marker=dict(color=STEP_COLORS.get(step, "#94a3b8"), line=dict(color="white", width=0.5)),
            legendgroup="IST",
            legendgrouptitle_text="IST",
            hovertemplate=ist_hover,
        ))

    # SOLL als einzelner Balken (Outline)
    soll_values = [soll_totals.get(g, 0) for g in present_groups]
    fig.add_trace(go.Bar(
        y=present_groups,
        x=soll_values,
        name="SOLL",
        orientation="h",
        marker=dict(color="rgba(233, 77, 58, 0.15)", line=dict(color="#E94D3A", width=2)),
        legendgroup="SOLL",
        hovertemplate=soll_hover,
    ))

    n_groups = len(present_groups)
    height = max(400, n_groups * 50)
    if print_mode:
        height = min(height, 700)

    fig.update_layout(
        barmode="overlay",
        title=dict(text=title, font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=40, t=50, b=30),
        height=height,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1, font=dict(size=10),
        ),
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False, tickformat=x_tickformat),
        yaxis=dict(showgrid=False, categoryorder="array", categoryarray=present_groups),
    )

    return fig


def create_comparison_chart(df: pd.DataFrame, dimension_col: str,
                             title: str = "",
                             print_mode: bool = False) -> go.Figure:
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

    height = max(350, n_bars * 40)
    if print_mode:
        height = min(height, 700)

    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#1e293b"), x=0),
        barmode="group",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=80, t=50, b=30),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False),
        yaxis=dict(showgrid=False)
    )

    return fig


# =============================================================================
# QUALIFIKATIONSSPANNWEITE PRO PLANSTELLE
# =============================================================================

def _build_education_tick_labels():
    """
    Erstellt Tick-Labels für die ordinale Ausbildungsskala.

    Bei geteiltem Rang (z.B. Rang 6 = Bankbetriebswirt und Bachelor FH)
    werden die Labels mit ' / ' kombiniert.

    Returns:
        Tuple aus (tick_vals, tick_labels, ord_to_first_label)
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for label, rank in EDUCATION_HIERARCHY.items():
        groups[rank].append(label)

    tick_vals = sorted(groups.keys())
    tick_labels = []
    ord_to_first_label = {}
    for rank in tick_vals:
        labels = groups[rank]
        ord_to_first_label[rank] = labels[0]
        # Kürzen für Tick-Labels: max 2 Labels kombinieren
        short = [l.replace("Berufsabschluss", "Abschl.")
                  .replace("Berufsausbildung", "Ausb.")
                  .replace("Universität", "Uni")
                  .replace("Berufsausbildung", "Ausb.")
                  .replace("Bankbetriebswirt", "BankBWirt")
                  .replace("Studium Lehrinstitut", "Studium LI")
                 for l in labels]
        tick_labels.append(" / ".join(short))

    return tick_vals, tick_labels, ord_to_first_label


def create_education_range_data(df: pd.DataFrame, min_persons: int = 2) -> pd.DataFrame:
    """
    Aggregiert Ausbildungsniveaus pro Planstelle auf ordinaler Skala.

    Nutzt EDUCATION_HIERARCHY aus settings.py. Unbekannte Ausbildungswerte
    werden bei der Aggregation ignoriert (nicht stillschweigend: sie tauchen
    in der Spalte 'unbekannt_n' auf, falls vorhanden).

    Args:
        df: Snapshot DataFrame mit Spalten 'Planstelle' und 'Ausbildung'
        min_persons: Mindestanzahl Personen pro Planstelle (Default: 2,
                     damit die Spannweite min-max sinnvoll ist)

    Returns:
        DataFrame mit Spalten: Planstelle, min_ord, max_ord, mean_ord,
        count, min_label, max_label, mean_label
    """
    if "Planstelle" not in df.columns or "Ausbildung" not in df.columns:
        return pd.DataFrame()

    # Nur besetzte Stellen mit Ausbildungsdaten
    mask = pd.Series(True, index=df.index)
    if "Is_Vacant" in df.columns:
        mask = mask & ~df["Is_Vacant"]
    mask = mask & df["Ausbildung"].notna() & df["Planstelle"].notna()

    work_df = df.loc[mask, ["Planstelle", "Ausbildung"]].copy()
    if work_df.empty:
        return pd.DataFrame()

    # Normalisierung: strip + lowercase Lookup für Robustheit
    norm_map = {k.strip().lower(): v for k, v in EDUCATION_HIERARCHY.items()}
    work_df["_edu_norm"] = work_df["Ausbildung"].astype(str).str.strip()
    work_df["_edu_ord"] = work_df["_edu_norm"].str.lower().map(norm_map)

    # Unbekannte Werte protokollieren (nicht stillschweigend entfernen)
    # Sie werden bei der Aggregation ausgeschlossen, aber gezählt
    unknown_mask = work_df["_edu_ord"].isna()
    n_unknown = int(unknown_mask.sum())

    work_df = work_df[~unknown_mask]
    if work_df.empty:
        return pd.DataFrame()

    # Aggregation pro Planstelle
    agg = work_df.groupby("Planstelle")["_edu_ord"].agg(
        min_ord="min", max_ord="max", mean_ord="mean", count="count"
    ).reset_index()

    # Filter auf Mindestanzahl
    agg = agg[agg["count"] >= min_persons].copy()
    if agg.empty:
        return pd.DataFrame()

    # Anzahl Personen auf Min- und Max-Niveau pro Planstelle
    detail = work_df[["Planstelle", "_edu_ord"]].merge(
        agg[["Planstelle", "min_ord", "max_ord"]], on="Planstelle"
    )
    n_min = (detail[detail["_edu_ord"] == detail["min_ord"]]
             .groupby("Planstelle").size().reset_index(name="n_min"))
    n_max = (detail[detail["_edu_ord"] == detail["max_ord"]]
             .groupby("Planstelle").size().reset_index(name="n_max"))
    agg = agg.merge(n_min, on="Planstelle", how="left")
    agg = agg.merge(n_max, on="Planstelle", how="left")
    agg["n_min"] = agg["n_min"].fillna(0).astype(int)
    agg["n_max"] = agg["n_max"].fillna(0).astype(int)

    # Ordinal -> Label (inverse Mapping, erster Eintrag bei geteiltem Rang)
    _, _, ord_to_label = _build_education_tick_labels()

    agg["min_label"] = agg["min_ord"].astype(int).map(ord_to_label)
    agg["max_label"] = agg["max_ord"].astype(int).map(ord_to_label)
    agg["mean_label"] = agg["mean_ord"].round().astype(int).map(ord_to_label)

    # Sortierung: nach mean_ord aufsteigend (niedrigste Qualifikation oben)
    agg = agg.sort_values("mean_ord", ascending=True).reset_index(drop=True)

    # Metadaten für Hinweis
    agg.attrs["n_unknown"] = n_unknown

    return agg


def create_education_range_chart(range_df: pd.DataFrame,
                                  print_mode: bool = False) -> go.Figure:
    """
    Erstellt ein Spannweiten-Diagramm: pro Planstelle eine horizontale Linie
    von min bis max Ausbildungsniveau, mit Marker für den Mittelwert.

    Args:
        range_df: Ergebnis von create_education_range_data()
        print_mode: Kompaktere Darstellung für Druck

    Returns:
        Plotly Figure
    """
    if range_df.empty:
        return go.Figure()

    tick_vals, tick_labels, _ = _build_education_tick_labels()

    fig = go.Figure()

    # Horizontale Linien von min bis max (eine Trace pro Zeile,
    # damit jede Linie unabhängig ist)
    for _, row in range_df.iterrows():
        fig.add_trace(go.Scatter(
            x=[row["min_ord"], row["max_ord"]],
            y=[row["Planstelle"], row["Planstelle"]],
            mode="lines",
            line=dict(color="#BEBEBE", width=3),
            showlegend=False,
            hoverinfo="skip",
        ))

    # Min-Marker (alle als eine Trace für gemeinsame Legende)
    fig.add_trace(go.Scatter(
        x=range_df["min_ord"],
        y=range_df["Planstelle"],
        mode="markers",
        marker=dict(color="#00B9FC", size=8, symbol="diamond"),
        name="Min",
        customdata=list(zip(range_df["min_label"], range_df["n_min"], range_df["count"])),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Min: %{customdata[0]} (n=%{customdata[1]})<br>"
            "Gesamt: %{customdata[2]} Personen"
            "<extra></extra>"
        ),
    ))

    # Max-Marker
    fig.add_trace(go.Scatter(
        x=range_df["max_ord"],
        y=range_df["Planstelle"],
        mode="markers",
        marker=dict(color="#E94D3A", size=8, symbol="diamond"),
        name="Max",
        customdata=list(zip(range_df["max_label"], range_df["n_max"], range_df["count"])),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Max: %{customdata[0]} (n=%{customdata[1]})<br>"
            "Gesamt: %{customdata[2]} Personen"
            "<extra></extra>"
        ),
    ))

    # Mean-Marker
    fig.add_trace(go.Scatter(
        x=range_df["mean_ord"],
        y=range_df["Planstelle"],
        mode="markers",
        marker=dict(color="#0088DE", size=10, symbol="circle"),
        name="Mittelwert",
        customdata=list(zip(range_df["mean_label"], range_df["count"])),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Mittel: %{customdata[0]}<br>"
            "Gesamt: %{customdata[1]} Personen"
            "<extra></extra>"
        ),
    ))

    n_rows = len(range_df)
    height = max(400, n_rows * 28)
    if print_mode:
        height = min(height, 800)

    all_ords = sorted(set(EDUCATION_HIERARCHY.values()))
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=30, t=50, b=80),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(226, 232, 240, 0.8)",
            zeroline=False,
            tickvals=tick_vals,
            ticktext=tick_labels,
            tickangle=-35,
            range=[min(all_ords) - 0.5, max(all_ords) + 0.5],
            title="Ausbildungsniveau (ordinal)",
        ),
        yaxis=dict(showgrid=False),
    )

    return fig


def render_education_range_section(df: pd.DataFrame,
                                    key_prefix: str = "",
                                    print_mode: bool = False):
    """
    Rendert die Qualifikationsspannweite pro Planstelle als Ersatz
    für die einfache IST-vs-SOLL-Balkenvergleichs-Grafik.

    Zeigt pro Planstelle min, mean und max Ausbildungsniveau
    der dort eingesetzten Personen.
    """
    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    st.subheader("📊 Qualifikation pro Planstelle")

    # Mindestanzahl Personen (ab 2 ist die Spannweite sinnvoll)
    MIN_PERSONS = 2

    range_df = create_education_range_data(df, min_persons=MIN_PERSONS)

    if range_df.empty:
        st.warning(
            "Keine Planstellen mit ausreichend Daten gefunden. "
            f"(Mindestens {MIN_PERSONS} Personen mit bekannter Ausbildung pro Stelle nötig.)"
        )
        if print_mode:
            st.markdown('</div>', unsafe_allow_html=True)
        return

    n_unknown = range_df.attrs.get("n_unknown", 0)

    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        fig = create_education_range_chart(range_df, print_mode=print_mode)
        st.plotly_chart(fig, use_container_width=True)
        if n_unknown > 0:
            st.caption(
                f"Hinweis: {n_unknown} Personen mit unbekanntem Ausbildungsabschluss "
                f"wurden bei der Berechnung ausgeschlossen."
            )

    with col_table:
        st.markdown("**Datentabelle**")
        display_df = range_df[[
            "Planstelle", "min_label", "n_min", "mean_label", "max_label", "n_max", "count"
        ]].copy()
        display_df.columns = ["Planstelle", "Min", "n(Min)", "Mittel", "Max", "n(Max)", "Gesamt"]
        dataframe_compat(display_df, width="stretch", hide_index=True)

        csv_data = export_to_csv(
            range_df[["Planstelle", "min_label", "n_min", "max_label", "n_max",
                       "mean_label", "min_ord", "max_ord", "mean_ord", "count"]]
        )
        download_button_compat(
            label="CSV Download",
            data=csv_data,
            file_name=f"{key_prefix}_qualifikation_spannweite.csv",
            mime="text/csv",
            key=f"download_{key_prefix}_edu_range",
            width="stretch",
        )

    if print_mode:
        st.markdown('</div>', unsafe_allow_html=True)


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
                            value_col: str, value_type: str = "mak", key_prefix: str = "",
                            print_mode: bool = False):
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
    # Sonderfall: Vergütungsklassen → gestapeltes Tarif-Chart
    is_verguetung = dimension_col == "Vergütungsklasse"

    if not is_verguetung and dimension_col not in df.columns:
        st.warning(f"Dimension '{dimension_name}' nicht verfügbar (Spalte '{dimension_col}' fehlt).")
        return

    if is_verguetung and ("TrfGr" not in df.columns or "St" not in df.columns):
        st.warning(f"Vergütungsdaten nicht verfügbar (TrfGr/St fehlen).")
        return

    # Print-Block Wrapper für saubere Seitenumbrüche
    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    st.subheader(f"📊 {dimension_name}")

    if is_verguetung:
        # Gestapeltes Tarif-Chart
        col_chart, col_table = st.columns([3, 2])

        with col_chart:
            fig = create_stacked_tariff_chart(df, value_col, title="", print_mode=print_mode, value_type=value_type)
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown("**Datentabelle**")
            breakdown_df = create_stacked_tariff_breakdown_table(df, value_col)

            # Formatierung der numerischen Spalten
            display_df = breakdown_df.copy()
            num_cols = [c for c in display_df.columns if c != "Entgeltgruppe"]
            for col in num_cols:
                if value_type == "eur":
                    display_df[col] = display_df[col].apply(
                        lambda x: format_currency(x) if pd.notna(x) and x != 0 else "-"
                    )
                elif value_type == "koepfe":
                    display_df[col] = display_df[col].apply(
                        lambda x: f"{int(x):,}".replace(",", ".") if pd.notna(x) and x != 0 else "-"
                    )
                else:  # mak
                    display_df[col] = display_df[col].apply(
                        lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
                        if pd.notna(x) and x != 0 else "-"
                    )

            dataframe_compat(display_df, width="stretch", hide_index=True)

            csv_data = export_to_csv(breakdown_df)
            download_button_compat(
                label="CSV Download",
                data=csv_data,
                file_name=f"{key_prefix}_verguetungsklassen.csv",
                mime="text/csv",
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )
    else:
        # Standard: Horizontales Balkendiagramm
        breakdown_df = create_breakdown_table(df, dimension_col, value_col)

        if breakdown_df.empty or "Hinweis" in breakdown_df.columns:
            st.warning(f"Keine Daten für '{dimension_name}' verfügbar.")
            if print_mode:
                st.markdown('</div>', unsafe_allow_html=True)
            return

        col_chart, col_table = st.columns([3, 2])

        with col_chart:
            preserve = (dimension_col in ORDINAL_ORDERS)
            fig = create_horizontal_bar_chart(
                breakdown_df, dimension_col, "IST",
                title="",
                preserve_order=preserve,
                print_mode=print_mode,
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown("**Datentabelle**")
            display_df = format_dataframe_for_display(breakdown_df, value_type)
            dataframe_compat(display_df, width="stretch", hide_index=True)

            csv_data = export_to_csv(breakdown_df)
            download_button_compat(
                label="CSV Download",
                data=csv_data,
                file_name=f"{key_prefix}_{dimension_name.lower().replace(' ', '_')}.csv",
                mime="text/csv",
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )

    # Print-Block Wrapper schließen
    if print_mode:
        st.markdown('</div>', unsafe_allow_html=True)


def render_single_comparison(df: pd.DataFrame, dimension_name: str, dimension_col: str,
                             ist_col: str, soll_col: str, value_type: str = "mak",
                             key_prefix: str = "",
                             print_mode: bool = False):
    """
    Rendert einen einzelnen IST vs SOLL Vergleichs-Block.
    """
    is_verguetung = dimension_col == "Vergütungsklasse"

    if not is_verguetung and dimension_col not in df.columns:
        st.warning(f"Dimension '{dimension_name}' nicht verfügbar.")
        return

    if is_verguetung and ("TrfGr" not in df.columns or "St" not in df.columns):
        st.warning(f"Vergütungsdaten nicht verfügbar (TrfGr/St fehlen).")
        return

    # Print-Block Wrapper für saubere Seitenumbrüche
    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    st.subheader(f"📊 {dimension_name}")

    if is_verguetung:
        col_chart, col_table = st.columns([3, 2])

        with col_chart:
            fig = create_stacked_tariff_comparison_chart(
                df, ist_col, soll_col, title="", print_mode=print_mode, value_type=value_type,
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown("**Datentabelle**")
            breakdown_df = create_stacked_tariff_breakdown_table(df, ist_col)

            # Formatierung der numerischen Spalten
            display_df = breakdown_df.copy()
            num_cols = [c for c in display_df.columns if c != "Entgeltgruppe"]
            for col in num_cols:
                if value_type == "eur":
                    display_df[col] = display_df[col].apply(
                        lambda x: format_currency(x) if pd.notna(x) and x != 0 else "-"
                    )
                elif value_type == "koepfe":
                    display_df[col] = display_df[col].apply(
                        lambda x: f"{int(x):,}".replace(",", ".") if pd.notna(x) and x != 0 else "-"
                    )
                else:  # mak
                    display_df[col] = display_df[col].apply(
                        lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
                        if pd.notna(x) and x != 0 else "-"
                    )

            dataframe_compat(display_df, width="stretch", hide_index=True)

            csv_data = export_to_csv(breakdown_df)
            download_button_compat(
                label="CSV Download",
                data=csv_data,
                file_name=f"{key_prefix}_verguetungsklassen.csv",
                mime="text/csv",
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )
    else:
        breakdown_df = create_breakdown_table(df, dimension_col, ist_col,
                                              include_soll=True, soll_col=soll_col)

        if breakdown_df.empty or "Hinweis" in breakdown_df.columns:
            st.warning(f"Keine Daten für '{dimension_name}' verfügbar.")
            if print_mode:
                st.markdown('</div>', unsafe_allow_html=True)
            return

        col_chart, col_table = st.columns([3, 2])

        with col_chart:
            fig = create_comparison_chart(breakdown_df, dimension_col, title="", print_mode=print_mode)
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown("**Datentabelle**")
            display_df = format_dataframe_for_display(breakdown_df, value_type)
            dataframe_compat(display_df, width="stretch", hide_index=True)

            csv_data = export_to_csv(breakdown_df)
            download_button_compat(
                label="CSV Download",
                data=csv_data,
                file_name=f"{key_prefix}_{dimension_name.lower().replace(' ', '_')}.csv",
                mime="text/csv",
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )

    # Print-Block Wrapper schließen
    if print_mode:
        st.markdown('</div>', unsafe_allow_html=True)


def render_ist_mak_tab(df: pd.DataFrame, print_mode: bool = False):
    """Rendert den IST-MAK Tab mit allen Themenfeldern untereinander."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df


    # KPIs berechnen
    from dataloader.kpi_engine import compute_teilzeit_kpis, compute_fte_roh
    total_mak = get_ist_mak(emp_df)
    total_fte_roh = compute_fte_roh(emp_df)
    total_koepfe = get_ist_koepfe(emp_df)
    teilzeit = compute_teilzeit_kpis(emp_df)
    avg_fte = total_mak / total_koepfe if total_koepfe > 0 else 0

    # KPI-Row mit styled cards
    kpis = [
        {"title": "Gesamt MAK (Effektiv)", "value": format_number(total_mak, 1),
         "subtitle": f"Roh: {format_number(total_fte_roh, 1)} | {total_koepfe} Köpfe", "icon": "📊", "status": "good"},
        {"title": "Durchschnitt FTE", "value": format_number(avg_fte, 2),
         "subtitle": "pro Mitarbeitenden", "icon": "👤", "status": "default"},
        {"title": "Teilzeit-Quote", "value": f"{teilzeit['quote_pct']:.1f}%".replace(".", ","),
         "subtitle": f"{teilzeit['count']} Teilzeit-MA", "icon": "⏰", "status": "default"},
    ]
    render_kpi_cards_styled(kpis)

    if print_mode:
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
    else:
        st.markdown("---")
        # Intra-Tab-Navigation (nur in Standard-Ansicht)
        render_intra_tab_navigation(THEMENFELDER_IST, "ist-mak")

    # Alle Themenfelder untereinander darstellen
    for idx, (themenfeld, dimensionen) in enumerate(THEMENFELDER_IST.items()):
        if not print_mode:
            # Themenfeld mit Anker für Scroll-Navigation (verwende Streamlit anchor)
            anchor_id = f"ist-mak-{idx}"
            st.subheader(themenfeld, anchor=anchor_id, divider=True)

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST-MAK > {themenfeld}")

            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col="MAK" if "MAK" in emp_df.columns else "FTE_assigned",
                value_type="mak",
                key_prefix="ist_mak",
                print_mode=print_mode
            )
            if print_mode:
                st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
            else:
                st.markdown("---")

    # Management Summary am Ende
    summary_data = analyze_ist_mak_data(df)
    render_management_summary("IST-MAK", summary_data, print_mode)


def render_ist_koepfe_tab(df: pd.DataFrame, print_mode: bool = False):
    """Rendert den IST-Köpfe Tab mit allen Themenfeldern untereinander."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    # KPIs
    from dataloader.kpi_engine import compute_atz_kpis, get_unique_employees
    total_koepfe = get_ist_koepfe(emp_df)
    unique_emp = get_unique_employees(emp_df)
    female_count = int((unique_emp["Geschlecht"] == "w").sum()) if "Geschlecht" in unique_emp.columns else 0
    female_rate = female_count / total_koepfe if total_koepfe > 0 else 0
    atz = compute_atz_kpis(emp_df)

    kpis = [
        {"title": "Gesamt Köpfe", "value": format_number(total_koepfe, 0),
         "subtitle": "Mitarbeitende", "icon": "👥", "status": "good"},
        {"title": "Frauen-Anteil", "value": format_percent(female_rate),
         "subtitle": f"{female_count} Frauen", "icon": "♀", "status": "default"},
        {"title": "ATZ-Quote", "value": f"{atz['quote_headcount_pct']:.1f}%".replace(".", ","),
         "subtitle": f"{atz['gesamt']} in ATZ", "icon": "🔄", "status": "default"},
    ]
    render_kpi_cards_styled(kpis)

    if print_mode:
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
    else:
        st.markdown("---")
        # Intra-Tab-Navigation (nur in Standard-Ansicht)
        render_intra_tab_navigation(THEMENFELDER_IST, "ist-koepfe")

    # Alle Themenfelder untereinander darstellen
    for idx, (themenfeld, dimensionen) in enumerate(THEMENFELDER_IST.items()):
        if not print_mode:
            # Themenfeld mit Anker für Scroll-Navigation (verwende Streamlit anchor)
            anchor_id = f"ist-koepfe-{idx}"
            st.subheader(themenfeld, anchor=anchor_id, divider=True)

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST-Köpfe > {themenfeld}")

            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col="Headcount",
                value_type="koepfe",
                key_prefix="ist_koepfe",
                print_mode=print_mode
            )
            if print_mode:
                st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
            else:
                st.markdown("---")

    # Management Summary am Ende
    summary_data = analyze_ist_koepfe_data(df)
    render_management_summary("IST-Köpfe", summary_data, print_mode)


def render_ist_eur_tab(df: pd.DataFrame, print_mode: bool = False):
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

    if print_mode:
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
    else:
        st.markdown("---")
        # Intra-Tab-Navigation (nur in Standard-Ansicht)
        render_intra_tab_navigation(THEMENFELDER_IST, "ist-eur")

    # Alle Themenfelder untereinander darstellen
    for idx, (themenfeld, dimensionen) in enumerate(THEMENFELDER_IST.items()):
        if not print_mode:
            # Themenfeld mit Anker für Scroll-Navigation (verwende Streamlit anchor)
            anchor_id = f"ist-eur-{idx}"
            st.subheader(themenfeld, anchor=anchor_id, divider=True)

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST-EUR > {themenfeld}")

            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col="Total_Cost_Year",
                value_type="eur",
                key_prefix="ist_eur",
                print_mode=print_mode
            )
            if print_mode:
                st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
            else:
                st.markdown("---")

    # Management Summary am Ende
    summary_data = analyze_ist_eur_data(df)
    render_management_summary("IST-EUR", summary_data, print_mode)


def render_ist_vs_soll_mak_tab(df: pd.DataFrame, print_mode: bool = False):
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

    if print_mode:
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
    else:
        st.markdown("---")

    # Alle Themenfelder für SOLL-Vergleich untereinander darstellen
    for themenfeld, dimensionen in THEMENFELDER_SOLL.items():
        if not print_mode:
            st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST vs SOLL MAK > {themenfeld}")

            # Qualifikation: Spannweiten-Chart statt einfachem Balkenvergleich
            if dimension_col == "Ausbildung":
                render_education_range_section(
                    df,
                    key_prefix="ist_vs_soll_mak",
                    print_mode=print_mode,
                )
            else:
                render_single_comparison(
                    df, dimension_name, dimension_col,
                    ist_col="FTE_assigned",
                    soll_col="Soll_FTE",
                    value_type="mak",
                    key_prefix="ist_vs_soll_mak",
                    print_mode=print_mode
                )
            if print_mode:
                st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
            else:
                st.markdown("---")

    # Management Summary am Ende
    summary_data = analyze_ist_vs_soll_mak_data(df)
    render_management_summary("IST vs SOLL MAK", summary_data, print_mode)


def render_ist_vs_soll_eur_tab(df: pd.DataFrame, print_mode: bool = False):
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

    if print_mode:
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
    else:
        st.markdown("---")

    # Alle Themenfelder für SOLL-Vergleich untereinander darstellen
    for themenfeld, dimensionen in THEMENFELDER_SOLL.items():
        if not print_mode:
            st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST vs SOLL EUR > {themenfeld}")

            render_single_comparison(
                df, dimension_name, dimension_col,
                ist_col="Total_Cost_Year",
                soll_col="Soll_Cost_Year",
                value_type="eur",
                key_prefix="ist_vs_soll_eur",
                print_mode=print_mode
            )
            if print_mode:
                st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
            else:
                st.markdown("---")

    # Management Summary am Ende
    summary_data = analyze_ist_vs_soll_eur_data(df)
    render_management_summary("IST vs SOLL EUR", summary_data, print_mode)


# =============================================================================
# PRINT-STYLING
# =============================================================================

def inject_print_styles():
    """Injiziert CSS für professionelle Druckansicht mit Kopf-/Fußzeilen."""
    from datetime import datetime

    today = datetime.now().strftime("%d.%m.%Y")

    st.markdown(f"""
    <style>
        @media print {{
            /* Seitenformat */
            @page {{
                size: A4 portrait;
                margin: 2.5cm 2cm 2.5cm 2cm;

                /* Kopfzeile */
                @top-left {{
                    content: "HR Pulse Dashboard";
                    font-size: 10pt;
                    font-weight: bold;
                    color: #0088DE;
                }}

                @top-center {{
                    content: "Kompakt-Auswertung";
                    font-size: 9pt;
                    color: #64748b;
                }}

                @top-right {{
                    content: "{today}";
                    font-size: 9pt;
                    color: #64748b;
                }}

                /* Fußzeile */
                @bottom-left {{
                    content: "HR Dashboard";
                    font-size: 8pt;
                    color: #94a3b8;
                }}

                @bottom-center {{
                    content: "Vertraulich";
                    font-size: 8pt;
                    color: #E94D3A;
                    font-weight: bold;
                }}

                @bottom-right {{
                    content: "Seite " counter(page) " von " counter(pages);
                    font-size: 8pt;
                    color: #94a3b8;
                }}
            }}

            /* Fallback für Browser ohne @page Kopf-/Fußzeilen-Support */
            .print-header {{
                display: block !important;
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                height: 60px;
                background: white;
                border-bottom: 2px solid #0088DE;
                padding: 10px 20px;
                z-index: 9999;
            }}

            .print-header-left {{
                float: left;
                font-size: 14pt;
                font-weight: bold;
                color: #0088DE;
            }}

            .print-header-center {{
                text-align: center;
                font-size: 11pt;
                color: #64748b;
                padding-top: 5px;
            }}

            .print-header-right {{
                float: right;
                font-size: 10pt;
                color: #64748b;
            }}

            .print-footer {{
                display: block !important;
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                height: 40px;
                background: white;
                border-top: 1px solid #e2e8f0;
                padding: 10px 20px;
                font-size: 9pt;
                color: #94a3b8;
            }}

            .print-footer-left {{
                float: left;
            }}

            .print-footer-center {{
                text-align: center;
                color: #E94D3A;
                font-weight: bold;
            }}

            .print-footer-right {{
                float: right;
            }}

            /* Content Bereich */
            .print-content {{
                margin-top: 70px !important;
                margin-bottom: 50px !important;
            }}

            /* Verstecke Streamlit UI-Elemente */
            header, .stApp > header, [data-testid="stHeader"] {{
                display: none !important;
            }}

            .stApp > div:first-child {{
                padding-top: 0 !important;
            }}

            /* Verstecke Sidebar in Druckansicht */
            [data-testid="stSidebar"], .css-1d391kg, section[data-testid="stSidebar"] {{
                display: none !important;
            }}

            /* Verstecke Scroll-Navigation in Druckansicht */
            iframe[title*="streamlit_scroll_navigation"],
            iframe[title*="scroll_navbar"],
            div[data-testid*="scroll_nav"] {{
                display: none !important;
            }}

            /* Verstecke alle UI-Komponenten */
            [data-testid="stToolbar"],
            [data-testid="stDecoration"],
            [data-testid="stStatusWidget"],
            .stDeployButton,
            button[kind="header"] {{
                display: none !important;
            }}

            /* Volle Breite für Content */
            .main .block-container {{
                max-width: 100% !important;
                padding-left: 1cm !important;
                padding-right: 1cm !important;
            }}

            /* Seitenumbrüche */
            .page-break {{
                page-break-after: always;
                break-after: page;
                height: 0;
                margin: 0;
                padding: 0;
            }}

            .page-break-before {{
                page-break-before: always;
                break-before: page;
            }}

            /* Verhindere Umbrüche innerhalb von Blöcken */
            .print-block {{
                page-break-inside: avoid;
                break-inside: avoid;
            }}

            /* Section Titles */
            .print-section-title {{
                page-break-after: avoid;
                break-after: avoid;
                margin-top: 20px;
                margin-bottom: 10px;
                font-size: 16pt;
                font-weight: bold;
                color: #1e293b;
                border-bottom: 2px solid #0088DE;
                padding-bottom: 5px;
            }}

            /* Charts und Tabellen */
            .stPlotlyChart, .stDataFrame {{
                page-break-inside: avoid;
                break-inside: avoid;
            }}

            /* KPI Cards */
            .stMetric, [data-testid="stMetricValue"] {{
                page-break-inside: avoid;
                break-inside: avoid;
            }}

            /* Management Summary Box */
            .management-summary {{
                page-break-inside: avoid;
                break-inside: avoid;
                background: #f8fafc;
                border: 2px solid #0088DE;
                border-radius: 8px;
                padding: 15px;
                margin: 20px 0;
            }}

            .management-summary h3 {{
                color: #0088DE;
                border-bottom: 1px solid #e2e8f0;
                padding-bottom: 5px;
                margin-bottom: 10px;
            }}

            /* Deckblatt und Inhaltsverzeichnis */
            .cover-page {{
                page-break-inside: avoid;
                break-inside: avoid;
                min-height: 100vh;
            }}

            .toc-page {{
                page-break-inside: avoid;
                break-inside: avoid;
            }}

            table {{
                page-break-inside: avoid;
                break-inside: avoid;
            }}

            /* Bessere Schriftarten für Druck */
            * {{
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
                color-adjust: exact !important;
            }}

            body {{
                font-family: "Segoe UI", Arial, sans-serif;
            }}

            /* Entferne unnötige Abstände */
            .element-container {{
                margin-bottom: 0.5rem !important;
            }}

            /* Tabs unsichtbar machen (werden ja nicht gebraucht) */
            [data-testid="stTabs"] {{
                display: none !important;
            }}
        }}

        /* Screen-only: Header/Footer versteckt */
        @media screen {{
            .print-header, .print-footer {{
                display: none;
            }}
        }}
    </style>
    """, unsafe_allow_html=True)


def render_print_header_footer(filter_summary: str):
    """Rendert Header und Footer für Druckansicht (Fallback für Browser)."""
    from datetime import datetime

    today = datetime.now().strftime("%d.%m.%Y")

    # Header
    st.markdown(f"""
    <div class="print-header">
        <div class="print-header-left">HR Pulse Dashboard</div>
        <div class="print-header-right">{today}</div>
        <div class="print-header-center">Kompakt-Auswertung | {filter_summary}</div>
    </div>
    """, unsafe_allow_html=True)

    # Footer (wird automatisch auf jeder Seite wiederholt)
    st.markdown("""
    <div class="print-footer">
        <div class="print-footer-left">HR Dashboard</div>
        <div class="print-footer-center">VERTRAULICH</div>
        <div class="print-footer-right">HR Pulse Dashboard</div>
    </div>
    """, unsafe_allow_html=True)


def page_break():
    """Fügt einen Seitenumbruch ein."""
    st.markdown('<div class="page-break"></div>', unsafe_allow_html=True)


def section_title(title: str, icon: str = "", anchor: str = None):
    """
    Rendert einen Abschnittstitel mit intelligentem Seitenumbruch.

    Args:
        title: Titel des Abschnitts
        icon: Emoji-Icon (optional)
        anchor: Anker-ID für Scroll-Navigation (optional)
    """
    if anchor:
        # Verwende Streamlit's nativen anchor für die Scroll-Navigation
        st.header(f"{icon} {title}", anchor=anchor, divider=False)
    else:
        st.markdown(
            f'<div class="print-section-title">{icon} {title}</div>',
            unsafe_allow_html=True
        )


def render_intra_tab_navigation(themenfelder: dict, tab_prefix: str):
    """
    Rendert eine vertikale Scroll-Navigation in einer Sidebar am rechten Rand.

    Args:
        themenfelder: Dict mit Themenfeldern (z.B. THEMENFELDER_IST)
        tab_prefix: Prefix für Anker-IDs (z.B. "ist-mak")
    """
    if not SCROLL_NAV_AVAILABLE:
        return

    # Erstelle Anker-IDs und Labels aus Themenfeldern
    anchor_ids = [f"{tab_prefix}-{i}" for i in range(len(themenfelder))]
    anchor_labels = [name for name in themenfelder.keys()]

    # Helles, professionelles Styling für vertikale Sidebar am rechten Rand
    # override_styles erfordert spezifische Keys
    custom_styles = {
        "navigationBarVertical": {
            "position": "fixed",
            "right": "20px",
            "top": "120px",
            "zIndex": "999",
            "background": "linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)",
            "border": "1px solid #e2e8f0",
            "borderRadius": "12px",
            "padding": "16px 12px",
            "boxShadow": "0 4px 20px rgba(0, 136, 222, 0.12)",
            "width": "220px",
        },
        "navbarButtonBase": {
            "background": "linear-gradient(135deg, #ffffff 0%, #f0f9ff 100%)",
            "color": "#0088DE",
            "border": "1px solid #e0f2fe",
            "borderRadius": "8px",
            "padding": "10px 16px",
            "fontWeight": "500",
            "marginBottom": "8px",
            "textAlign": "left",
            "width": "100%",
        },
        "navbarButtonHover": {
            "background": "linear-gradient(135deg, #0088DE 0%, #00B9FC 100%)",
            "color": "white",
            "borderColor": "#0088DE",
            "boxShadow": "0 4px 12px rgba(0, 136, 222, 0.25)",
            "transform": "translateX(-4px)",
        },
        "navbarButtonActive": {
            "background": "linear-gradient(135deg, #0088DE 0%, #0066aa 100%)",
            "color": "white",
            "borderColor": "#0066aa",
            "boxShadow": "0 2px 8px rgba(0, 102, 170, 0.3)",
        }
    }

    scroll_navbar(
        anchor_ids=anchor_ids,
        anchor_labels=anchor_labels,
        orientation="vertical",
        key=f"scroll_nav_{tab_prefix}",
        override_styles=custom_styles
    )


def render_cover_page_and_toc(filter_summary: str, df: pd.DataFrame):
    """Rendert Deckblatt mit Executive Summary - Vereinfachte Version"""
    from datetime import datetime
    today = datetime.now().strftime("%d.%m.%Y")

    # Deckblatt
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    st.markdown('<h1 style="text-align:center; color:#0088DE;">HR Pulse Dashboard</h1>', unsafe_allow_html=True)
    st.markdown('<h2 style="text-align:center; color:#64748b;">Kompakt-Auswertung</h2>', unsafe_allow_html=True)
    st.markdown(f'<p style="text-align:center; color:#94a3b8;"><strong>{today}</strong></p>', unsafe_allow_html=True)
    st.markdown(f'<p style="text-align:center; color:#94a3b8;">{filter_summary}</p>', unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)

    # Executive Summary
    st.subheader("🎯 Executive Summary")
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df
    from dataloader.kpi_engine import compute_planstellen_kpis
    plan_kpis = compute_planstellen_kpis(df)
    total_planstellen = plan_kpis["total"]
    total_koepfe = get_ist_koepfe(df)
    total_mak = get_ist_mak(df)
    total_cost = get_ist_eur(emp_df)
    besetzungsgrad = plan_kpis["besetzungsquote"] / 100 if total_planstellen > 0 else 0
    vakanzen = plan_kpis["vakanzen"]
    total_soll_mak = get_soll_mak(df) if "Soll_FTE" in df.columns else 0
    erfuellungsgrad_mak = total_mak / total_soll_mak if total_soll_mak > 0 else 0
    total_soll_cost = get_soll_eur(df) if "Soll_Cost_Year" in df.columns else 0
    budget_quote = total_cost / total_soll_cost if total_soll_cost > 0 else 0
    mak_status = "🔴" if erfuellungsgrad_mak < 0.85 else ("⚠️" if erfuellungsgrad_mak < 0.95 else "✅")
    budget_status = "🔴" if budget_quote > 1.05 else ("✅" if budget_quote <= 1.02 else "⚠️")

    # Tabelle mit Kennzahlen
    summary_data = {
        "Kennzahl": [
            "Planstellen gesamt",
            "Mitarbeitende (Köpfe)",
            "Besetzungsgrad",
            f"{mak_status} MAK-Erfüllungsgrad",
            f"{budget_status} Budget-Quote"
        ],
        "Wert": [
            f"{total_planstellen:,}",
            f"{total_koepfe:,}",
            f"{besetzungsgrad*100:.1f}% ({vakanzen} Vakanzen)",
            f"{erfuellungsgrad_mak*100:.1f}% ({format_number(total_mak, 1)} / {format_number(total_soll_mak, 1)})",
            f"{budget_quote*100:.1f}% ({format_currency(total_cost/1e6, 1)} Mio. € / {format_currency(total_soll_cost/1e6, 1)} Mio. €)"
        ]
    }
    st.table(pd.DataFrame(summary_data))

    # Handlungsfelder
    st.markdown("**🎯 Top 3 Handlungsfelder:**")
    handlungsfelder = []
    if erfuellungsgrad_mak < 0.85:
        handlungsfelder.append(f"**SOFORT:** Recruiting-Offensive starten - MAK-Gap von {format_number(total_soll_mak - total_mak, 1)} MAK schließen")
    elif erfuellungsgrad_mak < 0.95:
        handlungsfelder.append(f"Recruiting beschleunigen - MAK-Gap von {format_number(total_soll_mak - total_mak, 1)} MAK schließen")
    else:
        handlungsfelder.append(f"✅ MAK-Kapazität gut - Erfüllungsgrad bei {erfuellungsgrad_mak*100:.1f}%")
    if budget_quote > 1.05:
        handlungsfelder.append(f"**DRINGEND:** Budget-Überschreitung von {format_currency((total_cost - total_soll_cost)/1e6, 1)} Mio. € analysieren!")
    elif budget_quote <= 1.02:
        handlungsfelder.append("✅ Budget im Plan - Disziplin beibehalten")
    else:
        handlungsfelder.append(f"Budget beobachten - {budget_quote*100:.1f}% Quote")
    from dataloader.kpi_engine import compute_atz_kpis as _atz_cover
    atz_count = _atz_cover(df)["gesamt"]
    if atz_count > 0:
        handlungsfelder.append(f"Nachfolgeplanung für {atz_count} ATZ-Mitarbeitende vorbereiten")
    for i, feld in enumerate(handlungsfelder[:3], 1):
        st.markdown(f"{i}. {feld}")

    page_break()

    # Inhaltsverzeichnis
    st.subheader("📋 Inhaltsverzeichnis")
    toc_data = {
        "Section": [
            "1. Executive Summary",
            "2. Inhaltsverzeichnis",
            "",
            "📊 IST-Analysen",
            "3. IST-MAK Analyse (Kapazität)",
            "4. IST-Köpfe Analyse (Headcount)",
            "5. IST-EUR Analyse (Kosten)",
            "",
            "🎯 IST vs SOLL Vergleiche",
            "6. IST vs SOLL MAK (Kapazitäts-Gap)",
            "7. IST vs SOLL EUR (Budget-Analyse)"
        ],
        "Seite": [
            "1",
            "2",
            "",
            "",
            "3",
            "~8",
            "~13",
            "",
            "",
            "~18",
            "~21"
        ]
    }
    st.table(pd.DataFrame(toc_data))

    page_break()

def generate_executive_summary_html(df: pd.DataFrame) -> str:
    """Generiert HTML für Executive Summary auf Deckblatt."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    # Top Kennzahlen
    total_planstellen = len(df)
    total_koepfe = get_ist_koepfe(emp_df)
    total_mak = get_ist_mak(emp_df)
    total_cost = get_ist_eur(emp_df)

    besetzungsgrad = total_koepfe / total_planstellen if total_planstellen > 0 else 0
    vakanzen = df["Is_Vacant"].sum() if "Is_Vacant" in df.columns else 0

    # SOLL-Vergleich
    total_soll_mak = get_soll_mak(df) if "Soll_FTE" in df.columns else 0
    erfuellungsgrad_mak = total_mak / total_soll_mak if total_soll_mak > 0 else 0

    total_soll_cost = get_soll_eur(df) if "Soll_Cost_Year" in df.columns else 0
    budget_quote = total_cost / total_soll_cost if total_soll_cost > 0 else 0

    # Status ermitteln
    mak_status = "🔴" if erfuellungsgrad_mak < 0.85 else ("⚠️" if erfuellungsgrad_mak < 0.95 else "✅")
    budget_status = "🔴" if budget_quote > 1.05 else ("✅" if budget_quote <= 1.02 else "⚠️")

    html = f"""
        <div style="text-align: left;">
            <table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
                <tr style="background: white;">
                    <td style="padding: 0.8rem; border-bottom: 1px solid #e2e8f0;">
                        <strong>Planstellen gesamt</strong>
                    </td>
                    <td style="padding: 0.8rem; text-align: right; border-bottom: 1px solid #e2e8f0;">
                        <strong>{total_planstellen:,}</strong>
                    </td>
                </tr>
                <tr style="background: white;">
                    <td style="padding: 0.8rem; border-bottom: 1px solid #e2e8f0;">
                        <strong>Mitarbeitende (Köpfe)</strong>
                    </td>
                    <td style="padding: 0.8rem; text-align: right; border-bottom: 1px solid #e2e8f0;">
                        <strong>{total_koepfe:,}</strong>
                    </td>
                </tr>
                <tr style="background: white;">
                    <td style="padding: 0.8rem; border-bottom: 1px solid #e2e8f0;">
                        <strong>Besetzungsgrad</strong>
                    </td>
                    <td style="padding: 0.8rem; text-align: right; border-bottom: 1px solid #e2e8f0;">
                        <strong>{besetzungsgrad*100:.1f}%</strong> ({vakanzen} Vakanzen)
                    </td>
                </tr>
                <tr style="background: white;">
                    <td style="padding: 0.8rem; border-bottom: 1px solid #e2e8f0;">
                        <strong>{mak_status} MAK-Erfüllungsgrad</strong>
                    </td>
                    <td style="padding: 0.8rem; text-align: right; border-bottom: 1px solid #e2e8f0;">
                        <strong>{erfuellungsgrad_mak*100:.1f}%</strong> ({total_mak:.1f} / {total_soll_mak:.1f})
                    </td>
                </tr>
                <tr style="background: white;">
                    <td style="padding: 0.8rem; border-bottom: 1px solid #e2e8f0;">
                        <strong>{budget_status} Budget-Quote</strong>
                    </td>
                    <td style="padding: 0.8rem; text-align: right; border-bottom: 1px solid #e2e8f0;">
                        <strong>{budget_quote*100:.1f}%</strong> ({format_currency(total_cost)} / {format_currency(total_soll_cost)})
                    </td>
                </tr>
            </table>

            <div style="margin-top: 1.5rem; padding: 1rem; background: white; border-radius: 8px; border-left: 4px solid #0088DE;">
                <div style="font-weight: bold; color: #1e293b; margin-bottom: 0.5rem;">
                    🎯 Top 3 Handlungsfelder:
                </div>
                <div style="font-size: 0.95rem; line-height: 1.8;">
    """

    # Dynamische Handlungsfelder basierend auf Daten
    handlungsfelder = []

    if erfuellungsgrad_mak < 0.85:
        handlungsfelder.append("1. <strong>KRITISCH:</strong> Recruiting-Offensive starten - Nur {:.1f}% MAK-Erfüllung!".format(erfuellungsgrad_mak*100))
    elif erfuellungsgrad_mak < 0.95:
        handlungsfelder.append("1. Recruiting beschleunigen - MAK-Gap von {:.1f} MAK schließen".format(total_soll_mak - total_mak))
    else:
        handlungsfelder.append("1. ✅ Besetzung optimal - Fluktuation minimieren")

    if budget_quote > 1.05:
        handlungsfelder.append("2. <strong>DRINGEND:</strong> Budget-Überschreitung von {} analysieren!".format(format_currency(total_cost - total_soll_cost)))
    elif budget_quote > 1.02:
        handlungsfelder.append("2. Budget-Überwachung verschärfen - Quote bei {:.1f}%".format(budget_quote*100))
    else:
        handlungsfelder.append("2. ✅ Budget im Plan - Disziplin beibehalten")

    # ATZ-Check (unique Köpfe via kpi_engine)
    from dataloader.kpi_engine import compute_atz_kpis as _atz_check
    _atz_kpis = _atz_check(emp_df)
    atz_count = _atz_kpis["gesamt"]
    atz_rate = _atz_kpis["quote_headcount_pct"] / 100

    if atz_rate > 0.05:
        handlungsfelder.append("3. Nachfolgeplanung für {} ATZ-Mitarbeitende vorbereiten".format(atz_count))
    elif vakanzen > 20:
        handlungsfelder.append("3. Priorisierung der {} offenen Stellen - Quick Wins identifizieren".format(vakanzen))
    else:
        handlungsfelder.append("3. Talentmanagement & Retention-Maßnahmen intensivieren")

    for hf in handlungsfelder:
        html += f"                    {hf}<br>\n"

    html += """
                </div>
            </div>
        </div>
    """

    return html


# =============================================================================
# MANAGEMENT SUMMARY KOMPONENTEN
# =============================================================================

def render_management_summary(title: str, summary_data: dict, print_mode: bool = False):
    """
    Rendert eine Management Summary Box mit Key Insights und Handlungsempfehlungen.

    Args:
        title: Titel der Summary
        summary_data: Dict mit 'kennzahlen', 'insights', 'handlungsempfehlungen'
        print_mode: Ob im Print-Modus
    """
    st.markdown("---")

    # Wrapper für Print-Block und Management Summary Box
    if print_mode:
        st.markdown('<div class="print-block management-summary">', unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="
            background: linear-gradient(to right, #f8fafc, #ffffff);
            border: 2px solid #0088DE;
            border-radius: 12px;
            padding: 1.5rem;
            margin: 1.5rem 0;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        ">
        """, unsafe_allow_html=True)

    st.markdown(f"### 📋 Management Summary: {title}")

    # Kennzahlen
    if summary_data.get('kennzahlen'):
        st.markdown("**📊 Kennzahlen auf einen Blick:**")
        for kz in summary_data['kennzahlen']:
            icon = "✓" if kz.get('status') == 'good' else ("⚠️" if kz.get('status') == 'warning' else "🔴")
            st.markdown(f"- {icon} **{kz['label']}:** {kz['value']}")
        st.markdown("")

    # Key Insights
    if summary_data.get('insights'):
        st.markdown("**💡 Key Insights:**")
        for insight in summary_data['insights']:
            icon = "💡" if insight.get('type') == 'info' else ("⚠️" if insight.get('type') == 'warning' else "✓")
            st.markdown(f"{icon} {insight['text']}")
        st.markdown("")

    # Handlungsempfehlungen
    if summary_data.get('handlungsempfehlungen'):
        st.markdown("**🎯 Handlungsempfehlungen:**")
        for i, emp in enumerate(summary_data['handlungsempfehlungen'], 1):
            st.markdown(f"{i}. {emp}")

    st.markdown('</div>', unsafe_allow_html=True)


def analyze_ist_mak_data(df: pd.DataFrame) -> dict:
    """Analysiert IST-MAK Daten und erstellt Management Summary."""
    from dataloader.kpi_engine import compute_teilzeit_kpis as _tz_mak
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_mak = get_ist_mak(df)
    total_koepfe = get_ist_koepfe(df)
    avg_fte = total_mak / total_koepfe if total_koepfe > 0 else 0

    _tz = _tz_mak(df)
    teilzeit_count = _tz["count"]
    teilzeit_rate = _tz["quote_pct"] / 100

    # Kennzahlen
    kennzahlen = [
        {"label": "Gesamt-MAK", "value": format_number(total_mak, 1), "status": "good"},
        {"label": "Mitarbeitende", "value": format_number(total_koepfe, 0), "status": "good"},
        {"label": "Ø FTE", "value": format_number(avg_fte, 2), "status": "good" if avg_fte >= 0.85 else "warning"},
        {"label": "Teilzeitquote", "value": format_percent(teilzeit_rate), "status": "good" if teilzeit_rate < 0.4 else "warning"}
    ]

    # Insights generieren
    insights = []

    # Teilzeitquote
    if teilzeit_rate > 0.4:
        insights.append({
            "type": "warning",
            "text": f"Hohe Teilzeitquote von {format_percent(teilzeit_rate)} kann Kapazitätsengpässe verursachen."
        })
    elif teilzeit_rate > 0.25:
        insights.append({
            "type": "info",
            "text": f"Teilzeitquote von {format_percent(teilzeit_rate)} im moderaten Bereich."
        })
    else:
        insights.append({
            "type": "good",
            "text": f"Niedrige Teilzeitquote von {format_percent(teilzeit_rate)} ermöglicht hohe Planbarkeit."
        })

    # FTE-Durchschnitt
    if avg_fte < 0.8:
        insights.append({
            "type": "warning",
            "text": f"Niedriger Ø FTE von {format_number(avg_fte, 2)} deutet auf viele Teilzeitkräfte hin."
        })

    # Altersstruktur (wenn vorhanden)
    if "Alterskohorte" in emp_df.columns:
        age_dist = emp_df["Alterskohorte"].value_counts()
        over_55 = age_dist.get("55-60 Jahre", 0) + age_dist.get("60-65 Jahre", 0) + age_dist.get("> 65 Jahre", 0)
        over_55_rate = over_55 / len(emp_df) if len(emp_df) > 0 else 0

        if over_55_rate > 0.3:
            insights.append({
                "type": "warning",
                "text": f"{format_percent(over_55_rate)} der MA sind über 55 Jahre - Nachfolgeplanung wichtig!"
            })

    # Handlungsempfehlungen
    handlungsempfehlungen = []

    if teilzeit_rate > 0.4:
        handlungsempfehlungen.append("Prüfung von Vollzeit-Anreizen oder Aufstockungsmöglichkeiten")

    if avg_fte < 0.8:
        handlungsempfehlungen.append("Analyse der Teilzeit-Gründe und ggf. Flexibilisierung der Arbeitsmodelle")

    if len(handlungsempfehlungen) == 0:
        handlungsempfehlungen.append("Aktuelle Kapazitätsstruktur weiter monitoren")
        handlungsempfehlungen.append("Fokus auf Retention der Vollzeit-Mitarbeitenden")

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen
    }


def analyze_ist_koepfe_data(df: pd.DataFrame) -> dict:
    """Analysiert IST-Köpfe Daten und erstellt Management Summary."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_koepfe = get_ist_koepfe(emp_df)
    from dataloader.kpi_engine import get_unique_employees as _get_emp, compute_atz_kpis as _atz_koepfe
    _unique_emp = _get_emp(emp_df)
    female_count = int((_unique_emp["Geschlecht"] == "w").sum()) if "Geschlecht" in _unique_emp.columns else 0
    female_rate = female_count / total_koepfe if total_koepfe > 0 else 0

    _atz_k = _atz_koepfe(emp_df)
    atz_count = _atz_k["gesamt"]
    atz_rate = _atz_k["quote_headcount_pct"] / 100

    kennzahlen = [
        {"label": "Gesamt Köpfe", "value": format_number(total_koepfe, 0), "status": "good"},
        {"label": "Frauenanteil", "value": format_percent(female_rate), "status": "good" if female_rate >= 0.4 else "warning"},
        {"label": "ATZ-Quote", "value": format_percent(atz_rate), "status": "good" if atz_rate < 0.05 else "warning"},
    ]

    insights = []

    # Geschlechterverteilung
    if female_rate < 0.35:
        insights.append({
            "type": "warning",
            "text": f"Frauenanteil von {format_percent(female_rate)} unter Zielquote - Diversity-Initiative erforderlich."
        })
    elif female_rate >= 0.45:
        insights.append({
            "type": "good",
            "text": f"Ausgewogene Geschlechterverteilung mit {format_percent(female_rate)} Frauenanteil."
        })

    # ATZ
    if atz_rate > 0.05:
        insights.append({
            "type": "warning",
            "text": f"Erhöhte ATZ-Quote von {format_percent(atz_rate)} ({atz_count} Personen) - Nachfolgeplanung prüfen!"
        })

    # Altersstruktur
    if "Alterskohorte" in emp_df.columns:
        age_dist = emp_df["Alterskohorte"].value_counts()
        young = age_dist.get("< 20 Jahre", 0) + age_dist.get("20-30 Jahre", 0)
        young_rate = young / len(emp_df) if len(emp_df) > 0 else 0

        if young_rate < 0.15:
            insights.append({
                "type": "warning",
                "text": f"Nur {format_percent(young_rate)} Nachwuchskräfte (<30 Jahre) - Talentgewinnung intensivieren!"
            })

    handlungsempfehlungen = []

    if female_rate < 0.35:
        handlungsempfehlungen.append("Diversity-Recruiting verstärken, Fokus auf weibliche Talente")

    if atz_rate > 0.05:
        handlungsempfehlungen.append(f"Nachfolgeplanung für {atz_count} ATZ-Mitarbeitende initiieren")

    if len(handlungsempfehlungen) == 0:
        handlungsempfehlungen.append("Aktuelle Personalstruktur beibehalten")
        handlungsempfehlungen.append("Nachfolgeplanung für kritische Positionen vorbereiten")

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen
    }


def analyze_ist_eur_data(df: pd.DataFrame) -> dict:
    """Analysiert IST-EUR Daten und erstellt Management Summary."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_cost = get_ist_eur(emp_df)
    total_koepfe = get_ist_koepfe(emp_df)
    total_mak = get_ist_mak(emp_df)

    avg_cost_kopf = total_cost / total_koepfe if total_koepfe > 0 else 0
    cost_per_mak = total_cost / total_mak if total_mak > 0 else 0

    kennzahlen = [
        {"label": "Gesamt Kosten", "value": format_currency(total_cost), "status": "good"},
        {"label": "Kosten/Kopf", "value": format_currency(avg_cost_kopf), "status": "good"},
        {"label": "Kosten/MAK", "value": format_currency(cost_per_mak), "status": "good"},
    ]

    insights = []

    # Kosten pro Kopf Analyse
    if avg_cost_kopf > 70000:
        insights.append({
            "type": "info",
            "text": f"Überdurchschnittliche Kosten/Kopf von {format_currency(avg_cost_kopf)} deuten auf erfahrene Belegschaft hin."
        })
    elif avg_cost_kopf < 50000:
        insights.append({
            "type": "info",
            "text": f"Kosten/Kopf von {format_currency(avg_cost_kopf)} im unteren Bereich - viele Nachwuchskräfte oder Teilzeit."
        })

    # Verteilung prüfen
    if "Total_Cost_Year" in emp_df.columns:
        high_cost = (emp_df["Total_Cost_Year"] > 80000).sum()
        high_cost_rate = high_cost / len(emp_df) if len(emp_df) > 0 else 0

        if high_cost_rate > 0.3:
            insights.append({
                "type": "info",
                "text": f"{format_percent(high_cost_rate)} der MA mit Kosten >80k € - hoher Anteil an Führungskräften/Spezialisten."
            })

    handlungsempfehlungen = []

    if avg_cost_kopf > 70000:
        handlungsempfehlungen.append("Prüfung der Gehaltsstruktur und Benchmarking mit Markt")
        handlungsempfehlungen.append("Nachwuchskräfte-Programme zur langfristigen Kostenoptimierung")
    else:
        handlungsempfehlungen.append("Aktuelle Kostenstruktur monitoren und Budget einhalten")
        handlungsempfehlungen.append("Retention-Maßnahmen für Schlüsselkräfte prüfen")

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen
    }


def analyze_ist_vs_soll_mak_data(df: pd.DataFrame) -> dict:
    """Analysiert IST vs SOLL MAK Daten und erstellt Management Summary."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_ist = get_ist_mak(emp_df)
    total_soll = get_soll_mak(df)
    delta = total_ist - total_soll
    erfuellungsgrad = total_ist / total_soll if total_soll > 0 else 0

    kennzahlen = [
        {"label": "IST-MAK", "value": format_number(total_ist, 1), "status": "good"},
        {"label": "SOLL-MAK", "value": format_number(total_soll, 1), "status": "good"},
        {"label": "Delta", "value": f"{delta:+.1f}".replace(".", ","),
         "status": "good" if abs(delta) < 10 else "warning"},
        {"label": "Erfüllungsgrad", "value": format_percent(erfuellungsgrad),
         "status": "good" if erfuellungsgrad >= 0.95 else ("warning" if erfuellungsgrad >= 0.85 else "critical")}
    ]

    insights = []

    if erfuellungsgrad < 0.85:
        insights.append({
            "type": "warning",
            "text": f"Kritische Unterbesetzung: Nur {format_percent(erfuellungsgrad)} der Soll-Kapazität besetzt!"
        })
        vakanzen = df["Is_Vacant"].sum() if "Is_Vacant" in df.columns else 0
        if vakanzen > 0:
            insights.append({
                "type": "warning",
                "text": f"{vakanzen} offene Stellen identifiziert - Recruiting beschleunigen!"
            })
    elif erfuellungsgrad < 0.95:
        insights.append({
            "type": "warning",
            "text": f"Moderate Unterbesetzung bei {format_percent(erfuellungsgrad)} - {abs(delta):.1f} MAK fehlen."
        })
    elif erfuellungsgrad > 1.05:
        insights.append({
            "type": "info",
            "text": f"Überbesetzung bei {format_percent(erfuellungsgrad)} - {delta:+.1f} MAK über Soll."
        })
    else:
        insights.append({
            "type": "good",
            "text": f"Optimale Besetzung bei {format_percent(erfuellungsgrad)} Erfüllungsgrad."
        })

    handlungsempfehlungen = []

    if erfuellungsgrad < 0.85:
        handlungsempfehlungen.append("SOFORT: Recruiting-Offensive starten, Zeitarbeit prüfen")
        handlungsempfehlungen.append("Prioritäten setzen: Welche Positionen sind kritisch?")
        handlungsempfehlungen.append("Überstunden/Mehrarbeit in kritischen Bereichen genehmigen")
    elif erfuellungsgrad < 0.95:
        handlungsempfehlungen.append("Recruiting beschleunigen, Time-to-Hire reduzieren")
        handlungsempfehlungen.append("Interne Umbesetzungen prüfen")
    elif erfuellungsgrad > 1.05:
        handlungsempfehlungen.append("Überkapazitäten analysieren - sind alle Stellen notwendig?")
        handlungsempfehlungen.append("Budget-Einsparungspotenziale prüfen")
    else:
        handlungsempfehlungen.append("Aktuelle Besetzung halten, Fluktuation minimieren")

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen
    }


def analyze_ist_vs_soll_eur_data(df: pd.DataFrame) -> dict:
    """Analysiert IST vs SOLL EUR Daten und erstellt Management Summary."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_ist = get_ist_eur(emp_df)
    total_soll = get_soll_eur(df)
    delta = total_ist - total_soll
    kostenquote = total_ist / total_soll if total_soll > 0 else 0

    kennzahlen = [
        {"label": "IST-Kosten", "value": format_currency(total_ist), "status": "good"},
        {"label": "SOLL-Kosten", "value": format_currency(total_soll), "status": "good"},
        {"label": "Delta", "value": format_currency(abs(delta)),
         "status": "good" if abs(delta) < total_soll * 0.05 else "warning"},
        {"label": "Kostenquote", "value": format_percent(kostenquote),
         "status": "good" if kostenquote <= 1.02 else ("warning" if kostenquote <= 1.05 else "critical")}
    ]

    insights = []

    if delta > 0:
        ueberschreitung_pct = (delta / total_soll) * 100 if total_soll > 0 else 0
        if ueberschreitung_pct > 5:
            insights.append({
                "type": "warning",
                "text": f"Budget-Überschreitung von {format_currency(delta)} ({ueberschreitung_pct:.1f}%) - Maßnahmen erforderlich!"
            })
        else:
            insights.append({
                "type": "info",
                "text": f"Leichte Budget-Überschreitung von {format_currency(delta)} ({ueberschreitung_pct:.1f}%)."
            })
    elif delta < 0:
        einsparung_pct = (abs(delta) / total_soll) * 100 if total_soll > 0 else 0
        insights.append({
            "type": "good",
            "text": f"Budget-Unterschreitung: {format_currency(abs(delta))} Einsparung ({einsparung_pct:.1f}%)."
        })
    else:
        insights.append({
            "type": "good",
            "text": "Kosten exakt im Budget - perfekte Planung!"
        })

    handlungsempfehlungen = []

    if delta > total_soll * 0.05:
        handlungsempfehlungen.append("DRINGEND: Budget-Überschreitung analysieren und Gegenmaßnahmen einleiten")
        handlungsempfehlungen.append("Neueinstellungen temporär stoppen, Freigabeprozess verschärfen")
        handlungsempfehlungen.append("Überstunden reduzieren, Zeitarbeit überprüfen")
    elif delta > 0:
        handlungsempfehlungen.append("Kosten monitoren, keine weiteren Erhöhungen genehmigen")
        handlungsempfehlungen.append("Gehaltsrunden kritisch prüfen")
    elif delta < 0:
        handlungsempfehlungen.append("Einsparungen dokumentieren für Budget-Planung Folgejahr")
        handlungsempfehlungen.append("Prüfen: Sind Unterbesetzungen Grund für Einsparungen?")
    else:
        handlungsempfehlungen.append("Budget-Disziplin beibehalten")

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen
    }


# =============================================================================
# HAUPTFUNKTION
# =============================================================================

def main():
    """Hauptfunktion für die Kompakt-Seite."""

    st.title("⚡ Kompakt-Dashboard")
    st.caption("Alle wichtigen IST und IST vs SOLL Auswertungen auf einen Blick")

    # CSS für helles Scroll-Navigation Template
    if SCROLL_NAV_AVAILABLE:
        st.markdown("""
        <style>
        /* Helles Template für Scroll-Navigation Buttons */
        iframe[title*="streamlit_scroll_navigation"] {
            border: none !important;
        }

        /* Button-Styling über die Komponente hinweg */
        button[kind="secondary"],
        button[data-testid*="baseButton"] {
            background: linear-gradient(135deg, #ffffff 0%, #f0f9ff 100%) !important;
            color: #0088DE !important;
            border: 1px solid #e0f2fe !important;
            border-radius: 8px !important;
            padding: 10px 16px !important;
            font-weight: 500 !important;
            transition: all 0.3s ease !important;
            box-shadow: 0 2px 4px rgba(0, 136, 222, 0.08) !important;
            text-align: left !important;
            margin: 4px 0 !important;
        }

        button[kind="secondary"]:hover,
        button[data-testid*="baseButton"]:hover {
            background: linear-gradient(135deg, #0088DE 0%, #00B9FC 100%) !important;
            color: white !important;
            border-color: #0088DE !important;
            box-shadow: 0 4px 12px rgba(0, 136, 222, 0.25) !important;
            transform: translateX(-4px) !important;
        }

        button[kind="secondary"]:active,
        button[data-testid*="baseButton"]:active {
            background: #0088DE !important;
            color: white !important;
            border-color: #0066aa !important;
        }

        /* Aktiver Button-Highlight */
        button[kind="secondary"][aria-pressed="true"],
        button[data-testid*="baseButton"][aria-pressed="true"] {
            background: linear-gradient(135deg, #0088DE 0%, #0066aa 100%) !important;
            color: white !important;
            border-color: #0066aa !important;
            box-shadow: 0 2px 8px rgba(0, 102, 170, 0.3) !important;
        }

        /* Verstecke Scroll-Navigation auf kleineren Bildschirmen */
        @media (max-width: 1200px) {
            iframe[title*="streamlit_scroll_navigation"] {
                display: none !important;
            }
        }
        </style>
        """, unsafe_allow_html=True)

    try:
        # Daten laden
        snapshot_df, history_df, org_df, summary = load_and_prepare_data()
        prepared_df = prepare_compact_data(snapshot_df)

        # Filter rendern
        # Hinweis: Job Family Filter ist jetzt global in Sidebar (render_global_filters)
        render_global_filters(prepared_df, history_df)
        
        # Druck-Modus Toggle
        with st.sidebar:
            st.divider()
            print_mode = st.toggle(
                "🖨️ Druckansicht (Alle Tabs)", 
                value=False, 
                help="Zeigt alle Tabs untereinander an, um sie als PDF zu drucken (Strg+P)."
            )

        # Filter anwenden (inkl. Job Families über globale Sidebar)
        filtered_df = apply_filters(prepared_df)

        # Filter-Summary (inkl. Job Families)
        filter_summary = get_filter_summary()

        st.info(f"🎯 {filter_summary}")

        # Prüfe Daten
        if len(filtered_df) == 0:
            st.warning("Keine Daten für die gewählten Filter.")
            return

        if print_mode:
            # Print-Styles injizieren
            inject_print_styles()

            # Header und Footer rendern
            render_print_header_footer(filter_summary)

            # Scroll-Navigation einfügen (falls verfügbar)
            if SCROLL_NAV_AVAILABLE:
                anchor_ids = ["deckblatt", "ist-mak", "ist-koepfe", "ist-eur", "ist-vs-soll-mak", "ist-vs-soll-eur"]
                anchor_labels = ["🎯 Executive Summary", "📊 IST-MAK", "👥 IST-Köpfe", "💰 IST-EUR", "🎯 IST vs SOLL MAK", "💶 IST vs SOLL EUR"]

                # Helles, professionelles Styling für vertikale Sidebar am rechten Rand
                # override_styles erfordert spezifische Keys
                custom_styles_print = {
                    "navigationBarVertical": {
                        "position": "fixed",
                        "right": "20px",
                        "top": "120px",
                        "zIndex": "999",
                        "background": "linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)",
                        "border": "1px solid #e2e8f0",
                        "borderRadius": "12px",
                        "padding": "16px 12px",
                        "boxShadow": "0 4px 20px rgba(0, 136, 222, 0.12)",
                        "width": "240px",
                    },
                    "navbarButtonBase": {
                        "background": "linear-gradient(135deg, #ffffff 0%, #f0f9ff 100%)",
                        "color": "#0088DE",
                        "border": "1px solid #e0f2fe",
                        "borderRadius": "8px",
                        "padding": "12px 16px",
                        "fontWeight": "500",
                        "marginBottom": "8px",
                        "textAlign": "left",
                        "width": "100%",
                    },
                    "navbarButtonHover": {
                        "background": "linear-gradient(135deg, #0088DE 0%, #00B9FC 100%)",
                        "color": "white",
                        "borderColor": "#0088DE",
                        "boxShadow": "0 4px 12px rgba(0, 136, 222, 0.25)",
                        "transform": "translateX(-4px)",
                    },
                    "navbarButtonActive": {
                        "background": "linear-gradient(135deg, #0088DE 0%, #0066aa 100%)",
                        "color": "white",
                        "borderColor": "#0066aa",
                        "boxShadow": "0 2px 8px rgba(0, 102, 170, 0.3)",
                    }
                }

                # Vertikale Navigation-Sidebar am rechten Rand
                scroll_navbar(
                    anchor_ids=anchor_ids,
                    anchor_labels=anchor_labels,
                    orientation="vertical",
                    key="scroll_nav_print_mode",
                    override_styles=custom_styles_print
                )

            # Content-Wrapper
            st.markdown('<div class="print-content">', unsafe_allow_html=True)

            # Deckblatt mit Executive Summary und Inhaltsverzeichnis
            section_title("Deckblatt & Executive Summary", "🎯", anchor="deckblatt")
            render_cover_page_and_toc(filter_summary, filtered_df)

            # Druckansicht: Alle Tabs untereinander mit professionellen Seitenumbrüchen
            section_title("IST-MAK Analyse", "📊", anchor="ist-mak")
            render_ist_mak_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST-Köpfe Analyse", "👥", anchor="ist-koepfe")
            render_ist_koepfe_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST-EUR Analyse", "💰", anchor="ist-eur")
            render_ist_eur_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST vs SOLL MAK Vergleich", "🎯", anchor="ist-vs-soll-mak")
            render_ist_vs_soll_mak_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST vs SOLL EUR Vergleich", "💶", anchor="ist-vs-soll-eur")
            render_ist_vs_soll_eur_tab(filtered_df, print_mode=True)

            st.markdown('</div>', unsafe_allow_html=True)

            # Hinweis für Benutzer
            st.info("💡 **Drucken:** Drücken Sie Strg+P (Windows) oder Cmd+P (Mac) und wählen Sie 'Als PDF speichern'")

            
        else:
            # Standardansicht: Tabs
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
