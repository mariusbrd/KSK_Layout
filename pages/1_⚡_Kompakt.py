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
import io
from datetime import datetime
from typing import Dict

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from abgaenge.schemas import normalize_persnr
from components.ui_compat import dataframe_compat, download_button_compat
from dataloader.loader import load_and_prepare_data
from dataloader.jobfamily_service import JOBFAMILY_UNMAPPED, normalize_jobfamily_column, normalize_jobfamily_series
from dataloader.soll_ist_koepfe_engine import build_soll_ist_koepfe_result
from components.sidebar import render_global_filters, apply_filters, get_filter_summary, get_global_metric_view, normalize_global_metric_view, set_metric_page_hint
from utils.text_normalization import normalize_dashboard_text, normalize_display_text
from config.settings import (
    COLORS, COLOR_SEQUENCE, CHART_HEIGHTS,
    format_number, format_currency, format_percent,
    BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR, DEFAULT_COHORTS,
    EDUCATION_GROUPS, EDUCATION_HIERARCHY,
)
from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions
from utils.compact_ist_export import build_compact_ist_demographics_export_bytes
from utils.i18n import get_language, t
from utils.plot_helpers import apply_legend_bottom
from utils.settings_loader import get_setting

# Scroll Navigation
try:
    from streamlit_scroll_navigation import scroll_navbar
    SCROLL_NAV_AVAILABLE = True
except ImportError:
    SCROLL_NAV_AVAILABLE = False
    st.warning("⚠️ streamlit-scroll-navigation nicht installiert. Führen Sie 'pip install streamlit-scroll-navigation' aus.")


# =============================================================================
# HELPERS
# =============================================================================

def _normalize_personalnummer_keys(series: pd.Series) -> pd.Series:
    normalized = normalize_persnr(series)
    return normalized.astype("string").fillna("").replace({"<NA>": ""}).str.strip()


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
    ("Demografie", "Geschlecht", "Geschlecht", False),
    ("Demografie", "Alterskohorten", "Alterskohorte", False),
    # --- Qualifikation & Beschäftigung ---
    ("Qualifikation & Beschäftigung", "Qualifikation", "Ausbildung", True),
    ("Qualifikation & Beschäftigung", "Beschäftigungsgrad", "Beschäftigungsgrad_Kat", False),
    ("Qualifikation & Beschäftigung", "Beschäftigungsstatus", "Beschäftigungsstatus", False),
    # --- Unternehmenszugehörigkeit ---
    ("Unternehmenszugehörigkeit", "Dauer im Unternehmen", "Betriebszugehörigkeit_Bin", False),
    ("Unternehmenszugehörigkeit", "ATZ-Status", "ATZ_Status", False),
    # --- Vergütung ---
    ("Vergütung", "Vergütungsklassen", "Vergütungsklasse", True),
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
    "Betriebszugehörigkeit_Bin": TENURE_LABELS,
    "Beschäftigungsgrad_Kat": EMPLOYMENT_DEGREE_LABELS,
    "Ausbildung": EDUCATION_GROUPS,
}


def _render_page_intro():
    st.title(t("compact.title"))
    st.caption(t("compact.subtitle"))


def _get_main_tab_labels() -> list[str]:
    return [t("compact.tabs.ist"), t("compact.tabs.ist_soll")]


_COMPACT_LABEL_KEYS = {
    "Demografie": "compact.section.demographics",
    "👥 Demografie": "compact.section.demographics",
    "Qualifikation & Beschäftigung": "compact.section.qualification_employment",
    "🎓 Qualifikation & Beschäftigung": "compact.section.qualification_employment",
    "Qualifikation & Beschäftigung": "compact.section.qualification_employment",
    "🎓 Qualifikation & Beschäftigung": "compact.section.qualification_employment",
    "Unternehmenszugehörigkeit": "compact.section.tenure",
    "🏢 Unternehmenszugehörigkeit": "compact.section.tenure",
    "Unternehmenszugehörigkeit": "compact.section.tenure",
    "🏢 Unternehmenszugehörigkeit": "compact.section.tenure",
    "Vergütung": "compact.section.compensation",
    "💰 Vergütung": "compact.section.compensation",
    "Vergütung": "compact.section.compensation",
    "💰 Vergütung": "compact.section.compensation",
    "Geschlecht": "compact.dimension.gender",
    "Alterskohorten": "compact.dimension.age_cohorts",
    "Qualifikation": "compact.dimension.qualification",
    "Beschäftigungsgrad": "compact.dimension.employment_degree",
    "Beschäftigungsgrad": "compact.dimension.employment_degree",
    "Beschäftigungsstatus": "compact.dimension.employment_status",
    "Beschäftigungsstatus": "compact.dimension.employment_status",
    "Dauer im Unternehmen": "compact.dimension.tenure_company",
    "ATZ-Status": "compact.dimension.atz_status",
    "Vergütungsklassen": "compact.dimension.compensation_bands",
    "Vergütungsklassen": "compact.dimension.compensation_bands",
    "Gesamt MAK (Effektiv)": "compact.kpi.total_mak_effective",
    "Durchschnitt FTE": "compact.kpi.avg_fte",
    "Teilzeit-Quote": "compact.kpi.part_time_rate",
    "Gesamt Köpfe": "compact.kpi.total_heads",
    "Mitarbeitende": "compact.kpi.employees",
    "Frauen-Anteil": "compact.kpi.female_share",
    "ATZ-Quote": "compact.kpi.atz_quote",
    "Gesamt Kosten": "compact.kpi.total_cost",
    "Jahreskosten": "compact.kpi.annual_costs",
    "Kosten/Kopf": "compact.kpi.cost_per_head",
    "Durchschnitt": "compact.kpi.average",
    "Kosten/MAK": "compact.kpi.cost_per_fte",
    "Datentabelle": "compact.common.data_table",
    "IST-MAK": "compact.summary.current_mak",
    "IST-EUR": "compact.summary.current_eur",
    "Gesamt-MAK": "compact.summary.metric.total_mak",
    "Ø FTE": "compact.summary.metric.avg_fte",
    "Teilzeitquote": "compact.summary.metric.part_time_rate",
    "Aktuelle Kostenstruktur monitoren und Budget einhalten": "compact.rec.monitor_costs",
    "Retention-Maßnahmen für Schlüsselkräfte prüfen": "compact.rec.retention_key_roles",
    "Regulär auswertbare Planstellen": "compact.ist_soll_heads.kpi.regular_positions",
    "inkludierte OEs mit verwertbarer Soll-Entgeltgruppe": "compact.ist_soll_heads.kpi.regular_positions.subtitle",
    "Besetzt": "compact.ist_soll_heads.kpi.filled",
    "Unbesetzt": "compact.ist_soll_heads.kpi.vacant",
    "Ohne Ist-EG": "compact.ist_soll_heads.kpi.missing_actual",
    "Besetzt, aber im Mitarbeiterdatensatz ohne Tarifgruppe": "compact.ist_soll_heads.kpi.missing_actual.subtitle",
    "Soll-Stellen (regulaer)": "compact.ist_soll_heads.kpi.regular_target_positions",
    "Regulaere Planstellen ohne Sollkapa 0,01": "compact.ist_soll_heads.kpi.regular_target_positions.subtitle",
    "Regulaer besetzt": "compact.ist_soll_heads.kpi.regular_filled",
    "Regulaer unbesetzt": "compact.ist_soll_heads.kpi.regular_vacant",
    "Nicht definierte Sollstelle in Arbeit": "compact.ist_soll_heads.kpi.undefined_target_in_work",
    "Sollkapa 0,01, nicht 9XXX/99XX, mit Personalnummer": "compact.ist_soll_heads.kpi.undefined_target_in_work.subtitle",
    "Passend oder im Band": "compact.ist_soll_heads.kpi.matching_or_band",
    "Low-AZ-Planstellen gesamt": "compact.ist_soll_heads.overhang.kpi.low_az_total",
    "Alle Planstellen mit Soll-Arbeitszeit 0 oder 0,1": "compact.ist_soll_heads.overhang.kpi.low_az_total.subtitle",
    "Besetzte Zusatzstellen": "compact.ist_soll_heads.overhang.kpi.occupied_additional",
    "Low-AZ-Planstellen mit tatsächlich besetzter Person": "compact.ist_soll_heads.overhang.kpi.occupied_additional.subtitle",
    "Ohne reguläre Stelle": "compact.ist_soll_heads.overhang.kpi.without_regular",
    "Person erscheint nur über diese technische Zusatzstelle": "compact.ist_soll_heads.overhang.kpi.without_regular.subtitle",
    "Zusätzlich zur regulären Stelle": "compact.ist_soll_heads.overhang.kpi.alongside_regular",
    "Technische Zusatzstelle neben einer regulären aktiven Stelle": "compact.ist_soll_heads.overhang.kpi.alongside_regular.subtitle",
    "Sonderfälle gesamt": "compact.ist_soll_heads.special_cases.kpi.total",
    "Besetzt, aber ohne hinterlegte Soll-EG": "compact.ist_soll_heads.special_cases.kpi.total.subtitle",
    "Passend (exakt)": "compact.ist_soll_heads.detail.class.exact",
    "Passend im Band": "compact.ist_soll_heads.detail.class.in_band",
    "Übergruppiert": "compact.ist_soll_heads.detail.class.overgraded",
    "Untergruppiert": "compact.ist_soll_heads.detail.class.undergraded",
    "Nicht gefunden": "compact.ist_soll_heads.detail.class.not_found",
}

_COMPACT_LABEL_KEYS = {
    normalize_dashboard_text(key): value
    for key, value in _COMPACT_LABEL_KEYS.items()
}

_COMPACT_FALLBACK_NORMALIZATION = {
    "Beschäftigung": "Beschäftigung",
    "Beschäftigungs": "Beschäftigungs",
    "Unternehmenszugehörigkeit": "Unternehmenszugehörigkeit",
    "Betriebszugehörigkeit": "Betriebszugehörigkeit",
    "Vergütung": "Vergütung",
    "Köpfe": "Köpfe",
    "Gründe": "Gründe",
}


def _compact_text(text: str | None) -> str:
    if not text:
        return "" if text is None else text
    normalized = normalize_dashboard_text(text)
    for broken, clean in _COMPACT_FALLBACK_NORMALIZATION.items():
        normalized = normalized.replace(broken, clean)
    if "Retention-Ma" in normalized:
        return t("compact.rec.retention_key_roles")
    if "Aktuelle Kostenstruktur monitoren" in normalized:
        return t("compact.rec.monitor_costs")
    key = _COMPACT_LABEL_KEYS.get(normalized) or _COMPACT_LABEL_KEYS.get(text)
    return t(key) if key else normalized


def _compact_metric_view_label(metric_view: str | None) -> str:
    return normalize_dashboard_text(metric_view) if metric_view else t("compact.metric_view.none")


def _build_compact_sidebar_hint() -> str:
    metric_view = _compact_metric_view_label(normalize_global_metric_view(get_global_metric_view()))
    return (
        f"{t('compact.mode.subtitle')} "
        f"{t('compact.metric_view.text', metric_view=metric_view)}"
    )


def _is_mojibake(text: str | None) -> bool:
    return isinstance(text, str) and any(marker in text for marker in (chr(0x00C3), chr(0x00E2), chr(0x0192), chr(0x00C6)))


def _sanitize_kpi_icon(icon: str | None, title: str | None) -> str:
    if isinstance(icon, str) and icon and not _is_mojibake(icon):
        return icon

    fallback_icons = {
        t("compact.kpi.total_mak_effective"): "📈",
        t("compact.kpi.avg_fte"): "📊",
        t("compact.kpi.part_time_rate"): "⏰",
        t("compact.kpi.total_heads"): "👥",
        t("compact.kpi.female_share"): "👤",
        t("compact.kpi.atz_quote"): "⏰",
        t("compact.kpi.total_cost"): "💰",
        t("compact.kpi.cost_per_head"): "👤",
        t("compact.kpi.cost_per_fte"): "📊",
    }
    return fallback_icons.get(title or "", "📊")


def _translate_kpi_item(kpi: dict) -> dict:
    localized = dict(kpi)
    if "title" in localized:
        localized["title"] = _compact_text(localized["title"])
    if "subtitle" in localized:
        localized["subtitle"] = _compact_text(localized["subtitle"])
    localized["icon"] = _sanitize_kpi_icon(localized.get("icon"), localized.get("title"))
    return localized


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

    for col, raw_kpi in zip(cols, kpis):
        kpi = _translate_kpi_item(raw_kpi)
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

    for col, raw_kpi in zip(cols, kpis):
        kpi = _translate_kpi_item(raw_kpi)
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
                    background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
                    border-radius: 14px;
                    padding: 1rem 1.05rem;
                    border: 1px solid #dce8f5;
                    border-top: 4px solid {border_color};
                    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05);
                ">
                    <div style="font-size: 0.76rem; color: #64748b; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;">
                        {kpi.get('icon', '📊')} {kpi['title']}
                    </div>
                    <div style="font-size: 1.72rem; font-weight: 700; color: #0f172a; margin: 0.45rem 0 0.35rem 0; line-height: 1.15;">
                        {kpi['value']}
                    </div>
                    <div style="font-size: 0.84rem; color: #64748b; line-height: 1.35;">
                        {kpi.get('subtitle', '')}
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )


# =============================================================================
# DATENAUFBEREITUNG
# =============================================================================

def categorize_employment_degree(series: pd.Series) -> pd.Series:
    """
    Kategorisiert Beschäftigungsgrade robust.

    Wichtig:
    - 0 wird als <25% behandelt, damit ruhende/temporär kapazitätslose Köpfe
      in der Beschäftigungsgrad-Sicht nicht herausfallen.
    - Werte >= 95% werden als Vollzeit behandelt, auch wenn sie durch
      Mehrfachplanstellen rechnerisch über 100% liegen.
    """
    values = pd.to_numeric(series, errors="coerce").fillna(0.0)
    bins = [-0.000001, 0.25, 0.50, 0.75, 0.95, float("inf")]
    return pd.cut(
        values,
        bins=bins,
        labels=EMPLOYMENT_DEGREE_LABELS,
        right=False
    )

@st.cache_data
def prepare_compact_data(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    """Bereitet Daten für die Kompakt-Ansicht vor."""
    return _prepare_compact_data_clean(snapshot_df)


def _has_compatible_jobfamily(df: pd.DataFrame) -> bool:
    if "Jobfamily" not in df.columns:
        return False
    if df.empty:
        return True
    jobfamily = normalize_jobfamily_series(df["Jobfamily"])
    return jobfamily.astype(str).str.strip().ne("").all()

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
        df["Beschäftigungsgrad_Kat"] = categorize_employment_degree(df["FTE_person"])
    elif "BsGrd" in df.columns:
        df["Beschäftigungsgrad_Kat"] = categorize_employment_degree(df["BsGrd"] / 100)
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

    df = df.rename(
        columns=lambda col: normalize_dashboard_text(col) if isinstance(col, str) else col
    )

    return df


def _prepare_compact_data_clean(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    """Bereitet Daten für die Kompakt-Ansicht mit sauberen Spaltennamen auf."""
    df = snapshot_df.copy()

    if not _has_compatible_jobfamily(df):
        try:
            definitions = load_jobfamily_definitions()
            if definitions and "Planstelle" in df.columns:
                df = assign_jobfamilies(df, definitions)
            else:
                df["Jobfamily"] = JOBFAMILY_UNMAPPED
        except Exception:
            df["Jobfamily"] = JOBFAMILY_UNMAPPED

    df = normalize_jobfamily_column(df)

    if "Betriebszugehörigkeit_Jahre" in df.columns:
        tenure_years = pd.to_numeric(df["Betriebszugehörigkeit_Jahre"], errors="coerce")
        df["Betriebszugehörigkeit_Bin"] = pd.cut(
            tenure_years,
            bins=TENURE_BINS,
            labels=TENURE_LABELS,
            right=False,
        )
        df["Betriebszugehörigkeit_Bin"] = df["Betriebszugehörigkeit_Bin"].astype(object).where(
            df["Betriebszugehörigkeit_Bin"].notna(),
            "(unbekannt)",
        )
    else:
        df["Betriebszugehörigkeit_Bin"] = "(unbekannt)"

    if "TrfGr" in df.columns and "St" in df.columns:
        def format_verguetungsklasse(row):
            if pd.notna(row["TrfGr"]) and pd.notna(row["St"]):
                step_str = str(row["St"]).strip().replace("+", "").replace("-", "")
                try:
                    return f"{row['TrfGr']}/{int(step_str)}"
                except (ValueError, TypeError):
                    return f"{row['TrfGr']}/{row['St']}"

            fallback = row.get("Bewertung Tarifgruppe", "(unbekannt)")
            return fallback if pd.notna(fallback) else "(unbekannt)"

        df["Vergütungsklasse"] = df.apply(format_verguetungsklasse, axis=1)
    elif "Bewertung Tarifgruppe" in df.columns:
        df["Vergütungsklasse"] = df["Bewertung Tarifgruppe"].fillna("(unbekannt)")
    else:
        df["Vergütungsklasse"] = "(unbekannt)"

    if "FTE_person" in df.columns:
        df["Beschäftigungsgrad_Kat"] = categorize_employment_degree(df["FTE_person"])
    elif "BsGrd" in df.columns:
        df["Beschäftigungsgrad_Kat"] = categorize_employment_degree(df["BsGrd"] / 100)
    else:
        df["Beschäftigungsgrad_Kat"] = "(unbekannt)"

    if "Vertragsart" in df.columns:
        df["Beschäftigungsstatus"] = df["Vertragsart"].fillna("(unbekannt)")
    elif "Status kundenindividuell" in df.columns:
        df["Beschäftigungsstatus"] = df["Status kundenindividuell"].fillna("(unbekannt)")
    else:
        df["Beschäftigungsstatus"] = "(unbekannt)"

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
        special = get_special_salary(tarif, step=step)
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
    cost_col = "EUR_Reporting" if "EUR_Reporting" in df.columns else "Total_Cost_Year"
    if cost_col in df.columns:
        if "PersNr" in df.columns:
            from dataloader.kpi_engine import get_unique_employees
            emp = get_unique_employees(df)
            return emp[cost_col].sum()
        return df[cost_col].sum()
    return 0.0


def get_soll_mak(df: pd.DataFrame) -> float:
    if "Soll_FTE" in df.columns:
        return df["Soll_FTE"].sum()
    return 0.0


def get_soll_eur(df: pd.DataFrame) -> float:
    if "Soll_Cost_Year" in df.columns:
        return df["Soll_Cost_Year"].sum()
    return 0.0


@st.cache_data
def _create_breakdown_table_cached(
    df: pd.DataFrame,
    dimension_col: str,
    value_col: str,
    include_soll: bool = False,
    soll_col: str = None,
) -> pd.DataFrame:
    return _create_breakdown_table_clean(
        df,
        dimension_col,
        value_col,
        include_soll=include_soll,
        soll_col=soll_col,
    )


def create_breakdown_table(df: pd.DataFrame, dimension_col: str, value_col: str,
                           include_soll: bool = False, soll_col: str = None) -> pd.DataFrame:
    """Erstellt eine Breakdown-Tabelle nach einer Dimension."""
    return _create_breakdown_table_cached(
        df,
        dimension_col,
        value_col,
        include_soll=include_soll,
        soll_col=soll_col,
    )
    if dimension_col not in df.columns:
        return pd.DataFrame({"Hinweis": [f"Spalte '{dimension_col}' nicht verfügbar"]})

    if value_col == "Headcount":
        emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df
        id_col = "PersNr" if "PersNr" in emp_df.columns else "Personalnummer"
        if dimension_col == "Beschäftigungsgrad_Kat" and "PersNr" in emp_df.columns:
            from dataloader.kpi_engine import get_unique_employees
            emp_unique = get_unique_employees(emp_df).copy()
            if "FTE_person" in emp_unique.columns:
                emp_unique[dimension_col] = categorize_employment_degree(emp_unique["FTE_person"])
            elif "BsGrd" in emp_unique.columns:
                emp_unique[dimension_col] = categorize_employment_degree(emp_unique["BsGrd"] / 100.0)
            else:
                emp_unique[dimension_col] = "(unbekannt)"
            agg_df = emp_unique.groupby(dimension_col, observed=True)["PersNr"].nunique().reset_index()
            agg_df.columns = [dimension_col, "IST"]
        elif dimension_col == "Betriebszugehörigkeit_Bin" and "PersNr" in emp_df.columns:
            from dataloader.kpi_engine import get_unique_employees
            emp_unique = get_unique_employees(emp_df).copy()
            if "Betriebszugehörigkeit_Jahre" in emp_unique.columns:
                emp_unique[dimension_col] = pd.cut(
                    pd.to_numeric(emp_unique["Betriebszugehörigkeit_Jahre"], errors="coerce"),
                    bins=TENURE_BINS,
                    labels=TENURE_LABELS,
                    right=False,
                )
                emp_unique[dimension_col] = emp_unique[dimension_col].astype(object).where(
                    emp_unique[dimension_col].notna(), "(unbekannt)"
                )
            elif dimension_col in emp_unique.columns:
                emp_unique[dimension_col] = emp_unique[dimension_col].fillna("(unbekannt)")
            else:
                emp_unique[dimension_col] = "(unbekannt)"
            agg_df = emp_unique.groupby(dimension_col, observed=True)["PersNr"].nunique().reset_index()
            agg_df.columns = [dimension_col, "IST"]
        elif id_col in emp_df.columns:
            agg_df = emp_df.groupby(dimension_col, observed=True)[id_col].nunique().reset_index()
            agg_df.columns = [dimension_col, "IST"]
        else:
            agg_df = emp_df.groupby(dimension_col, observed=True).size().reset_index(name="IST")
    elif value_col in ("MAK_Reporting", "EUR_Reporting") and "PersNr" in df.columns:
        agg_df = df.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]
    elif value_col in ("MAK_Calculated", "mak", "MAK", "FTE_person", "Total_Cost_Year") and "PersNr" in df.columns:
        # Person-level Metriken: Deduplizieren nach PersNr mit summierten
        # Kapazitaetsspalten aus get_unique_employees().
        from dataloader.kpi_engine import get_unique_employees
        emp_unique = get_unique_employees(df)
        if dimension_col in emp_unique.columns and value_col in emp_unique.columns:
            agg_df = emp_unique.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        else:
            agg_df = df.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]
    else:
        agg_df = df.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]

    if include_soll and soll_col and soll_col in df.columns:
        soll_agg = df.groupby(dimension_col, observed=True)[soll_col].sum().reset_index()
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


def _create_breakdown_table_clean(
    df: pd.DataFrame,
    dimension_col: str,
    value_col: str,
    *,
    include_soll: bool = False,
    soll_col: str | None = None,
) -> pd.DataFrame:
    """Erstellt eine Breakdown-Tabelle mit sauberen Spaltenreferenzen."""
    if dimension_col not in df.columns:
        return pd.DataFrame({"Hinweis": [f"Spalte '{dimension_col}' nicht verfügbar"]})

    if value_col == "Headcount":
        emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df
        id_col = "PersNr" if "PersNr" in emp_df.columns else "Personalnummer"

        if dimension_col == "Beschäftigungsgrad_Kat" and "PersNr" in emp_df.columns:
            from dataloader.kpi_engine import get_unique_employees

            emp_unique = get_unique_employees(emp_df).copy()
            if "FTE_person" in emp_unique.columns:
                emp_unique[dimension_col] = categorize_employment_degree(emp_unique["FTE_person"])
            elif "BsGrd" in emp_unique.columns:
                emp_unique[dimension_col] = categorize_employment_degree(emp_unique["BsGrd"] / 100.0)
            else:
                emp_unique[dimension_col] = "(unbekannt)"

            agg_df = emp_unique.groupby(dimension_col, observed=True)["PersNr"].nunique().reset_index()
            agg_df.columns = [dimension_col, "IST"]
        elif dimension_col == "Betriebszugehörigkeit_Bin" and "PersNr" in emp_df.columns:
            from dataloader.kpi_engine import get_unique_employees

            emp_unique = get_unique_employees(emp_df).copy()
            if "Betriebszugehörigkeit_Jahre" in emp_unique.columns:
                emp_unique[dimension_col] = pd.cut(
                    pd.to_numeric(emp_unique["Betriebszugehörigkeit_Jahre"], errors="coerce"),
                    bins=TENURE_BINS,
                    labels=TENURE_LABELS,
                    right=False,
                )
                emp_unique[dimension_col] = emp_unique[dimension_col].astype(object).where(
                    emp_unique[dimension_col].notna(),
                    "(unbekannt)",
                )
            elif dimension_col in emp_unique.columns:
                emp_unique[dimension_col] = emp_unique[dimension_col].fillna("(unbekannt)")
            else:
                emp_unique[dimension_col] = "(unbekannt)"

            agg_df = emp_unique.groupby(dimension_col, observed=True)["PersNr"].nunique().reset_index()
            agg_df.columns = [dimension_col, "IST"]
        elif id_col in emp_df.columns:
            agg_df = emp_df.groupby(dimension_col, observed=True)[id_col].nunique().reset_index()
            agg_df.columns = [dimension_col, "IST"]
        else:
            agg_df = emp_df.groupby(dimension_col, observed=True).size().reset_index(name="IST")
    elif value_col in ("MAK_Reporting", "EUR_Reporting") and "PersNr" in df.columns:
        agg_df = df.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]
    elif value_col in ("MAK_Calculated", "mak", "MAK", "FTE_person", "Total_Cost_Year") and "PersNr" in df.columns:
        from dataloader.kpi_engine import get_unique_employees

        emp_unique = get_unique_employees(df)
        if dimension_col in emp_unique.columns and value_col in emp_unique.columns:
            agg_df = emp_unique.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        else:
            agg_df = df.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]
    else:
        agg_df = df.groupby(dimension_col, observed=True)[value_col].sum().reset_index()
        agg_df.columns = [dimension_col, "IST"]

    if include_soll and soll_col and soll_col in df.columns:
        soll_agg = df.groupby(dimension_col, observed=True)[soll_col].sum().reset_index()
        soll_agg.columns = [dimension_col, "SOLL"]
        agg_df = agg_df.merge(soll_agg, on=dimension_col, how="outer").fillna(0)
        agg_df["Delta"] = agg_df["IST"] - agg_df["SOLL"]
        agg_df["Erfüllungsgrad"] = agg_df.apply(
            lambda row: row["IST"] / row["SOLL"] if row["SOLL"] > 0 else 0,
            axis=1,
        )

    total = agg_df["IST"].sum()
    agg_df["Anteil"] = agg_df["IST"] / total if total > 0 else 0

    if dimension_col in ORDINAL_ORDERS:
        order_list = ORDINAL_ORDERS[dimension_col]
        order_map = {val: i for i, val in enumerate(order_list)}
        agg_df["_sort_order"] = agg_df[dimension_col].map(lambda x: order_map.get(str(x), 999))
        agg_df = agg_df.sort_values("_sort_order").drop(columns=["_sort_order"])
    else:
        agg_df = agg_df.sort_values("IST", ascending=False)

    return agg_df


def _build_filter_meta_rows() -> list:
    """Liest aktive Filter aus session_state und gibt strukturierte Zeilen zurück."""
    from utils.settings_loader import get_setting
    rows = []

    def _add(label, val):
        rows.append([label, str(val) if val else "—"])

    org = st.session_state.get("selected_org_units", [])
    _add("Organisationseinheiten", ", ".join(org) if org else "alle")

    jf = st.session_state.get("selected_jobfamilies", [])
    _add("Jobgruppen", ", ".join(jf) if jf else "alle")

    cohorts = st.session_state.get("selected_cohorts", [])
    _add("Altersgruppen (Kohorten)", ", ".join(str(c) for c in cohorts) if cohorts else "alle")

    genders = st.session_state.get("selected_genders", [])
    _add("Geschlecht", ", ".join(genders) if genders else "alle")

    emp = st.session_state.get("selected_employment", [])
    _add("Arbeitszeit", ", ".join(emp) if emp else "alle")

    edu = st.session_state.get("selected_education", [])
    _add("Qualifikation", ", ".join(edu) if edu else "alle")

    atz = st.session_state.get("selected_atz_status", [])
    _add("ATZ-Status", ", ".join(atz) if atz else "alle")

    oe_cl = st.session_state.get("selected_oe_clusters", [])
    _add("OE-Cluster", ", ".join(oe_cl) if oe_cl else "alle")

    jf_cl = st.session_state.get("selected_jf_clusters", [])
    _add("Jobgruppen-Cluster", ", ".join(jf_cl) if jf_cl else "alle")

    ex = get_setting("exclusions", {})
    _add("Exkludiert: Vorstand",    "ja" if ex.get("vorstand") else "nein")
    _add("Exkludiert: Ruhendes BV", "ja" if ex.get("ruhend_bv") else "nein")
    units = ex.get("org_units", [])
    if units:
        _add("Exkludiert: PA-Bereiche (Anzahl)", str(len(units)))
        _add("Exkludiert: PA-Bereiche (vollständig)", ", ".join(sorted(units)))
    else:
        _add("Exkludiert: PA-Bereiche", "keine")

    return rows

_COL_DESCRIPTIONS = {
    # KPI-Spalten
    "IST":            "IST-Wert (tatsächlicher Bestand zum Stichtag)",
    "SOLL":           "SOLL-Wert (Planbedarf laut Stellenplanung)",
    "Delta":          "Differenz IST minus SOLL (negativ = Unterbesetzung)",
    "Erfüllungsgrad": "IST / SOLL in Prozent",
    "Anteil":         "Prozentualer Anteil am Gesamtwert der Auswertung",
    "MAK":            "Mitarbeiterkapazität (FTE-Äquivalent, effektiv)",
    "MAK_Calculated": "Berechneter MAK-Wert (vektorisiert)",
    "Köpfe":          "Headcount = Anzahl unique Mitarbeitende",
    "EUR":            "Jahreskosten in Euro (inkl. Arbeitgeberanteil)",
    "Soll_FTE":       "Planstellen-Sollkapazität (Sollarbeitszeit / 39)",
    "BsGrd":          "Beschäftigungsgrad in Prozent (100 = Vollzeit)",
    # Entgelt / Stellenplan
    "Entgeltgruppe":  "TVöD-Entgeltgruppe / Vergütungsklasse",
    "Planstelle":     "Bezeichnung der Planstelle / Qualifikationsstufe",
    "min_label":      "Minimum-Entgeltgruppe im Bereich",
    "max_label":      "Maximum-Entgeltgruppe im Bereich",
    "n_min":          "Anzahl Mitarbeitende auf Minimum-Stufe",
    "n_max":          "Anzahl Mitarbeitende auf Maximum-Stufe",
    "mean_label":     "Durchschnittliche Entgeltgruppe (gewichtet)",
    "count":          "Gesamtanzahl Mitarbeitende in diesem Bereich",
    # Dimensions-/Gruppierungsspalten
    "Geschlecht":          "Geschlecht des Mitarbeitenden (M / W / D)",
    "Jobfamily":           "Jobgruppe (fachliche Eingruppierung der Stelle)",
    "JF_Cluster":          "Jobgruppen-Cluster (aggregierte Jobgruppen-Gruppe)",
    "OE_Cluster":          "Organisationseinheiten-Cluster (aggregierte OE-Gruppe)",
    "Kürzel OrgEinheit":   "Kürzel der Organisationseinheit",
    "OrgEinheit":          "Name der Organisationseinheit",
    "Altersgruppe":        "Altersgruppe des Mitarbeitenden (z. B. <30, 30-39, 40-49)",
    "Qualifikation":       "Qualifikationsstufe / Ausbildungsprofil",
    "Standort":            "Standort / Filiale des Mitarbeitenden",
    "Status":              "Beschäftigungsstatus (z. B. Aktiv, Ruhend, ATZ-FR)",
    "Beschäftigungsart":   "Art des Beschäftigungsverhältnisses",
    "Teilzeit_Vollzeit":   "Teilzeit- oder Vollzeitstatus",
}

_VALUE_TYPE_LABEL = {
    "mak":    "Mitarbeiterkapazität (MAK / FTE)",
    "koepfe": "Köpfe (Headcount)",
    "eur":    "Kosten (EUR/Jahr)",
}
_KEY_PREFIX_LABEL = {
    "ist_mak":   "IST-MAK",
    "ist_koepfe":"IST-Köpfe",
    "ist_eur":   "IST-EUR",
    "soll_mak":  "IST vs. SOLL MAK",
    "soll_eur":  "IST vs. SOLL EUR",
}

_COL_DESCRIPTIONS = {
    normalize_display_text(key): normalize_display_text(value)
    for key, value in _COL_DESCRIPTIONS.items()
}
_VALUE_TYPE_LABEL = {
    normalize_display_text(key): normalize_display_text(value)
    for key, value in _VALUE_TYPE_LABEL.items()
}
_KEY_PREFIX_LABEL = {
    normalize_display_text(key): normalize_display_text(value)
    for key, value in _KEY_PREFIX_LABEL.items()
}







def export_to_excel(
    df: pd.DataFrame,
    key_prefix: str,
    dimension_name: str = "",
    value_type: str = "",
    table_title: str = "",
) -> bytes:
    """
    Exportiert eine Tabelle als Excel-Datei (XLSX) mit zwei Sheets:
    - 'Daten':          Die eigentliche Datentabelle (identisch zum bisherigen CSV)
    - 'Dokumentation':  Tabellenkontext, aktive Filter und Spalten-Erklärungen
    """
    from utils.settings_loader import get_setting

    stichtag = get_setting("stichtag", "unbekannt")
    tab_label = _KEY_PREFIX_LABEL.get(key_prefix, key_prefix)
    val_label = _VALUE_TYPE_LABEL.get(value_type, value_type)
    title = table_title or (
        f"{tab_label} - {dimension_name}" if dimension_name else tab_label
    )

    # Dokumentations-Sheet aufbauen
    meta_rows = [
        ["Exportzeitpunkt",   datetime.now().strftime("%d.%m.%Y %H:%M:%S")],
        ["Tabelle",           title],
        ["Tab / Thema",       tab_label],
        ["Kennzahl",          val_label],
        ["Stichtag",          stichtag],
        ["", ""],
        ["AKTIVE FILTER", ""],
    ]
    meta_rows += _build_filter_meta_rows()
    meta_rows += [
        ["", ""],
        ["SPALTEN-ERKLÄRUNG", ""],
    ]
    for col in df.columns:
        desc = _COL_DESCRIPTIONS.get(
            normalize_display_text(col),
            f"Gruppierungsmerkmal / Dimensionsspalte: {normalize_display_text(col)}",
        )
        meta_rows.append([col, desc])

    meta_df = pd.DataFrame(meta_rows, columns=["Feld", "Wert"])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Daten", index=False)
        meta_df.to_excel(writer, sheet_name="Dokumentation", index=False)

        # Spaltenbreiten anpassen
        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            for col_cells in ws.columns:
                max_len = max((len(str(c.value or "")) for c in col_cells), default=10)
                ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 4, 60)

    return output.getvalue()


_EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# =============================================================================
# FILTER-FUNKTIONEN
# =============================================================================

# Job Family Filter ist jetzt global in Sidebar (siehe components/sidebar.py)
# Lokale Filter-Funktionen wurden entfernt - alle Filterung erfolgt über apply_filters()


# =============================================================================
# CHART-FUNKTIONEN
# =============================================================================

@st.cache_data
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


@st.cache_data
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
        # legend=dict(...) # Entfernt zugunsten von apply_legend_bottom
    )
    
    fig = apply_legend_bottom(fig)
    return fig


STEP_COLORS = {
    1: "#B3E0FF",  # Hellblau
    2: "#66C2FF",  # Mittel-Hellblau
    3: "#33AAFF",  # Mittelblau
    4: "#0088DE",  # Blau
    5: "#0066A8",  # Dunkelblau
    6: "#004471",  # Sehr Dunkelblau
}


@st.cache_data
def _prepare_stacked_tariff_chart_source(
    df: pd.DataFrame,
    value_col: str,
) -> tuple[pd.DataFrame, list[str]]:
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
            .reset_index(name="Wert")
        )
    else:
        pivot = (
            work_df.groupby(["TrfGr_clean", "St_clean"])[value_col]
            .sum()
            .reset_index(name="Wert")
        )

    from config.settings import TARIFF_GROUPS

    group_order = {g: i for i, g in enumerate(TARIFF_GROUPS)}
    present_groups = sorted(
        pivot["TrfGr_clean"].unique(),
        key=lambda g: group_order.get(g, 999),
    )
    return pivot, present_groups


@st.cache_data
def _prepare_stacked_tariff_comparison_chart_source(
    df: pd.DataFrame,
    ist_col: str,
    soll_col: str,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    work_df = df.copy()
    work_df["TrfGr_clean"] = work_df["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    work_df["St_clean"] = work_df["St"].apply(
        lambda x: int(str(x).strip().replace("+", "").replace("-", "")) if pd.notna(x) else 4
    )

    ist_pivot = work_df.groupby(["TrfGr_clean", "St_clean"])[ist_col].sum().reset_index(name="Wert")
    ist_totals = ist_pivot.groupby("TrfGr_clean")["Wert"].sum()
    soll_totals = work_df.groupby("TrfGr_clean")[soll_col].sum().reset_index(name="Wert")

    from config.settings import TARIFF_GROUPS

    group_order = {g: i for i, g in enumerate(TARIFF_GROUPS)}
    present_groups = sorted(
        set(ist_totals.index) | set(soll_totals["TrfGr_clean"]),
        key=lambda g: group_order.get(g, 999),
    )
    return ist_pivot, soll_totals, present_groups


@st.cache_data
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

    pivot, present_groups = _prepare_stacked_tariff_chart_source(df, value_col)

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
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False, tickformat=x_tickformat),
        yaxis=dict(showgrid=False, categoryorder="array", categoryarray=present_groups),
    )
    
    fig = apply_legend_bottom(fig)


    return fig


@st.cache_data
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


@st.cache_data
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

    ist_pivot, soll_totals, present_groups = _prepare_stacked_tariff_comparison_chart_source(df, ist_col, soll_col)
    soll_value_map = dict(zip(soll_totals["TrfGr_clean"], soll_totals["Wert"]))
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
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False, tickformat=x_tickformat),
        yaxis=dict(showgrid=False, categoryorder="array", categoryarray=present_groups),
    )
    
    fig = apply_legend_bottom(fig)


    return fig


PLANLEVEL_CANDIDATE_COLUMNS = [
    "Planebene",
    "Ebene",
    "Führungsebene",
    "Führungsebene",
    "Hierarchieebene",
    "Stellenebene",
    "Funktionslevel",
    "Funktions-Level",
    "Level",
    "Planstellenebene",
    "Managementebene",
    "Rollenebene",
]


def _first_existing_column(df: pd.DataFrame, candidates: tuple[str, ...] | list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _numeric_compensation_series(
    series: pd.Series, default: float = 0.0, kind: str = "fte"
) -> pd.Series:
    """Robuste numerische Umwandlung fuer Kennzahlspalten.

    kind="fte"      MAK/FTE/Sollarbeitszeit: X.YYY wird nie als Tausenderformat
                    behandelt (0.141->0.141, 1.000->1.0).
    kind="currency" EUR/Kosten: X.YYY mit fuehrender Ziffer 1-9 gilt als Tausender
                    (1.000->1000), 0.XXX bleibt Dezimalzahl.
    kind="auto"     wie "fte" (sicherer Fallback).

    Formate:
      Letzter Trenner = Komma -> deutsches Format  (1.234,56  -> 1234.56)
      Letzter Trenner = Punkt -> englisches Format  (1,234.56  -> 1234.56)
      Nur Komma, kein Punkt   -> deutsches Dezimal  (0,141     -> 0.141)
        Sonderfall: 1,000 -> 1.0 (im deutschen Kontext Komma = Dezimal)
      Nur Punkt, kein Komma   -> abhaengig von kind (s.o.)
    """
    text = series.astype("string").str.strip()
    text = text.str.replace("\u00a0", "", regex=False).str.replace(" ", "", regex=False)

    has_comma = text.str.contains(",", regex=False).fillna(False)
    has_dot   = text.str.contains(".", regex=False).fillna(False)
    both      = has_comma & has_dot

    normalized = text.copy()

    # Beide Trennzeichen: Format anhand des letzten Trenners bestimmen
    # Deutsch (letzter = Komma): 1.234,56 -> 1234.56 / 59.674.856,62 -> 59674856.62
    last_is_comma = both & text.str.match(r"^-?[\d.]+,\d+$").fillna(False)
    # Englisch (letzter = Punkt): 1,234.56 -> 1234.56 / 59,674,856.62 -> 59674856.62
    last_is_dot   = both & text.str.match(r"^-?[\d,]+\.\d+$").fillna(False)

    normalized = normalized.where(
        ~last_is_comma,
        text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
    )
    normalized = normalized.where(
        ~last_is_dot,
        text.str.replace(",", "", regex=False),
    )

    # Nur Komma, kein Punkt: deutsches Dezimalformat (0,141->0.141, 762,91->762.91)
    # Hinweis: 1,000 ergibt 1.0 \u2013 im deutschen Kontext korrekt (Komma = Dezimal).
    only_comma = has_comma & ~has_dot
    normalized = normalized.where(
        ~only_comma,
        text.str.replace(",", ".", regex=False),
    )

    # Nur Punkt, kein Komma: Tausender-Erkennung je nach kind
    only_dot  = ~has_comma & has_dot
    multi_dot = only_dot & normalized.str.count(r"\.").gt(1).fillna(False)
    if kind == "currency":
        # Euro: 1.000->1000, aber 0.141 bleibt 0.141 (fuehrende Null != Tausender)
        single_dot_thousands = (
            only_dot
            & normalized.str.match(r"^-?[1-9]\d{0,2}\.\d{3}$").fillna(False)
        )
    else:
        # FTE/MAK/auto: einzelnen Punkt nie als Tausendertrennzeichen entfernen
        single_dot_thousands = pd.Series(False, index=normalized.index)

    dot_thousands = multi_dot | single_dot_thousands
    normalized = normalized.where(~dot_thousands, normalized.str.replace(".", "", regex=False))

    return pd.to_numeric(normalized, errors="coerce").fillna(default)


def _person_identifier_column(df: pd.DataFrame) -> str | None:
    """Bevorzugt die ID-Spalte, die tatsaechlich Werte enthaelt."""
    for col in ("PersNr", "Personalnummer"):
        if col in df.columns and df[col].notna().any():
            return col
    return _first_existing_column(df, ["PersNr", "Personalnummer"])


def _clean_compensation_group(value) -> str:
    if pd.isna(value):
        return "Nicht zugeordnet"
    text = str(value).strip().upper().replace(" ", "")
    if not text or text in ("NAN", "NONE", "<NA>"):
        return "Nicht zugeordnet"
    if text.startswith("BIS"):
        text = text[3:].strip()
    return text or "Nicht zugeordnet"


def _clean_compensation_step(value):
    if pd.isna(value):
        return "Nicht zugeordnet"
    text = str(value).strip().replace("+", "").replace("-", "")
    if not text or text.upper() in ("NAN", "NONE", "<NA>"):
        return "Nicht zugeordnet"
    try:
        return int(float(text))
    except (ValueError, TypeError):
        return str(value).strip() or "Nicht zugeordnet"


_IST_OHNE_SOLL_CAT_ORDER = [
    "Trainee / Ausbildung",
    "Ruhendes Beschäftigungsverhältnis",
    "ATZ / Freistellung",
    "Rente auf Zeit",
    "Pool- / Sammelplanstelle",
    "Reguläre aktive Stelle ohne Soll_FTE",
    "Sonstiger Fall ohne Plan-SOLL",
]


def _classify_ist_ohne_plan_soll(df: pd.DataFrame, out: pd.DataFrame) -> pd.Series:
    """
    Klassifiziert besetzte Zeilen mit Soll_FTE = 0 und vorhandenen IST-Werten.

    Gibt "" fuer normale Zeilen (Soll_FTE > 0 oder keine IST-Werte).
    Prioritaet: Trainee > Ruhendes BV > ATZ > Rente > Pool > Regulaer > Sonstiger.
    """
    result = pd.Series("", index=out.index, dtype=str)

    # Basis-Filter
    _soll_fte = (
        pd.to_numeric(df["Soll_FTE"], errors="coerce").fillna(0.0)
        if "Soll_FTE" in df.columns
        else pd.Series(0.0, index=df.index)
    )
    _ist_kopf = out["IST_Kopf"].eq(1) if "IST_Kopf" in out.columns else pd.Series(False, index=out.index)
    _hat_ist  = (
        (out["IST_MAK"].gt(0) if "IST_MAK" in out.columns else pd.Series(False, index=out.index))
        | (out["IST_EUR"].gt(0) if "IST_EUR" in out.columns else pd.Series(False, index=out.index))
    )
    _hat_eg = (
        out["Ist_Entgeltgruppe"].ne("Nicht zugeordnet")
        if "Ist_Entgeltgruppe" in out.columns
        else pd.Series(True, index=out.index)
    )
    basis = _ist_kopf & _soll_fte.eq(0.0) & _hat_ist & _hat_eg

    if not basis.any():
        return result

    def _cn(col: str, pattern: str) -> pd.Series:
        if col not in df.columns:
            return pd.Series(False, index=df.index)
        return df[col].fillna("").astype(str).str.lower().str.contains(pattern, regex=True, na=False)

    def _eq(col: str, val: str) -> pd.Series:
        if col not in df.columns:
            return pd.Series(False, index=df.index)
        return df[col].fillna("").astype(str).str.strip() == val

    def _in(col: str, vals: list) -> pd.Series:
        if col not in df.columns:
            return pd.Series(False, index=df.index)
        return df[col].fillna("").astype(str).str.strip().isin(vals)

    # Pool: Planstellennr erscheint mehrfach im Snapshot
    if "Planstellennr" in out.columns:
        _plnr_s = out["Planstellennr"].fillna("").astype(str).str.strip()
        _vc_map = _plnr_s[_plnr_s.ne("")].value_counts().to_dict()
        is_pool_plnr = _plnr_s.map(lambda x: _vc_map.get(x, 1)).gt(1) & _plnr_s.ne("")
    else:
        is_pool_plnr = pd.Series(False, index=out.index)

    _azubi_flag = (
        df["Ist_Azubi"].fillna(False).astype(bool)
        if "Ist_Azubi" in df.columns
        else pd.Series(False, index=df.index)
    )

    mask_trainee  = basis & (
        _cn("Vertragsart", r"trainee|ausbildung|werkstudent")
        | _cn("Planstelle", r"trainee|azubi|ausbildung|werkstudent")
        | _azubi_flag
        | _cn("TrfGr", r"tva")
    )
    mask_ruhend   = basis & _eq("Status kundenindividuell", "Ruhendes Beschäftigungsverhältnis")
    mask_atz      = basis & (
        _eq("Vertragsart", "Altersteilzeit")
        | _in("Phase", ["FR", "AR"])
        | _cn("Planstelle", r"altersteilzeit")
        | _cn("Planstellenkürzel", r"atz")
    )
    mask_rente    = basis & (
        _cn("Planstelle",                 r"rente|vorruhestand")
        | _cn("Vertragsart",              r"rente|vorruhestand")
        | _cn("Status kundenindividuell", r"rente|vorruhestand")
    )
    mask_pool     = basis & (is_pool_plnr | _cn("Planstelle", r"pool|sammel"))
    mask_regulaer = (
        basis
        & _eq("Status kundenindividuell", "Aktives Beschäftigungsverhältnis")
        & ~mask_trainee & ~mask_ruhend & ~mask_atz & ~mask_rente & ~mask_pool
    )

    # Zuweisung: niedrigste Prio zuerst, hoehere ueberschreibt.
    # Reihenfolge: Ruhendes BV < Pool < Regulaer < Rente < ATZ < Trainee
    # ATZ ueberschreibt Ruhendes BV, da ATZ fachlich spezifischer ist.
    result[basis]         = "Sonstiger Fall ohne Plan-SOLL"
    result[mask_ruhend]   = "Ruhendes Beschäftigungsverhältnis"
    result[mask_pool]     = "Pool- / Sammelplanstelle"
    result[mask_regulaer] = "Reguläre aktive Stelle ohne Soll_FTE"
    result[mask_rente]    = "Rente auf Zeit"
    result[mask_atz]      = "ATZ / Freistellung"
    result[mask_trainee]  = "Trainee / Ausbildung"

    return result


@st.cache_data
def build_compact_compensation_planlevel_df(prepared_df: pd.DataFrame) -> pd.DataFrame:
    """
    Baut eine zentrale, planstellennahe Verguetungsbasis fuer Kompakt.

    Die Funktion erfindet keine neue TVOED-, MAK- oder EUR-Logik, sondern nutzt
    die bereits im Snapshot vorhandenen Reporting- und Soll-Spalten.
    """
    if prepared_df is None or prepared_df.empty:
        return pd.DataFrame()

    df = prepared_df.copy()
    out = pd.DataFrame(index=df.index)

    planlevel_col = _first_existing_column(df, PLANLEVEL_CANDIDATE_COLUMNS)
    if planlevel_col:
        out["Planebene"] = df[planlevel_col].fillna("Nicht zugeordnet").astype(str).str.strip()
        out.loc[out["Planebene"].isin(["", "nan", "None", "<NA>"]), "Planebene"] = "Nicht zugeordnet"
        out["Planebene_Source"] = planlevel_col
    else:
        # Fachlich unsicher: Ohne echte Quellspalte wird keine Planebene geraten.
        out["Planebene"] = "Nicht zugeordnet"
        out["Planebene_Source"] = "missing"

    passthrough_cols = [
        "Organisationseinheit",
        "Kürzel OrgEinheit",
        "Kürzel OrgEinheit",
        "OE-Cluster",
        "JF-Cluster",
        "Jobfamily",
        "Planstellennr",
        "Planstelle",
        "Planstellenkürzel",
        "Planstellenkürzel",
        "Is_Vacant",
        "Is_Excluded",       # Exklusionsflag aus apply_exclusions()
        "Exclusion_Group",   # Exklusionsgruppe für Transparenz und Debug
        "PersNr",
        "Personalnummer",
        "Vergütungsklasse",
        "Vergütungsklasse",
        # Zusatz-Kontext fuer IST-ohne-Plan-SOLL-Download und Klassifikation
        "Status kundenindividuell",
        "Vertragsart",
        "Phase",
        "ATZ_Status",
        "Ausbildung",
        "Ist_Azubi",
        "Personalnachname",
        "Personalvorname",
    ]
    for col in passthrough_cols:
        if col in df.columns and col not in out.columns:
            out[col] = df[col]

    rename_aliases = {
        "Kürzel OrgEinheit": "Kürzel OrgEinheit",
        "Planstellenkürzel": "Planstellenkürzel",
        "Vergütungsklasse": "Vergütungsklasse",
    }
    out = out.rename(columns={old: new for old, new in rename_aliases.items() if old in out.columns and new not in out.columns})

    for col in [
        "Organisationseinheit",
        "Kürzel OrgEinheit",
        "OE-Cluster",
        "JF-Cluster",
        "Jobfamily",
        "Planstellennr",
        "Planstelle",
        "Planstellenkürzel",
        "PersNr",
        "Personalnummer",
        "Vergütungsklasse",
    ]:
        if col not in out.columns:
            out[col] = pd.NA

    out["Ist_Entgeltgruppe"] = df["TrfGr"].map(_clean_compensation_group) if "TrfGr" in df.columns else "Nicht zugeordnet"
    out["Ist_Stufe"] = df["St"].map(_clean_compensation_step) if "St" in df.columns else "Nicht zugeordnet"

    if "Bewertung Tarifgruppe" in df.columns:
        soll_group = df["Bewertung Tarifgruppe"].map(_clean_compensation_group)
    else:
        soll_group = pd.Series("Nicht zugeordnet", index=df.index)
    if "Text Gehaltsband" in df.columns:
        fallback_group = df["Text Gehaltsband"].map(_clean_compensation_group)
        soll_group = soll_group.where(soll_group.ne("Nicht zugeordnet"), fallback_group)
    out["Soll_Entgeltgruppe"] = soll_group.fillna("Nicht zugeordnet")

    soll_step_col = _first_existing_column(
        df,
        ["Soll_Stufe", "Soll Stufe", "Bewertung Stufe", "Stufe Soll", "Sollstufe"],
    )
    if soll_step_col:
        out["Soll_Stufe"] = df[soll_step_col].map(_clean_compensation_step)
    elif "St" in df.columns:
        # Fachlich unsicher: Es gibt keine eigene Soll-Stufe; aktuelle Ist-Stufe wird nur transparent gespiegelt.
        out["Soll_Stufe"] = df["St"].map(_clean_compensation_step)
    else:
        out["Soll_Stufe"] = "Nicht zugeordnet"

    ist_mak_col = _first_existing_column(df, ["MAK_Reporting", "MAK_Calculated", "MAK", "FTE_assigned"])
    out["IST_MAK"] = _numeric_compensation_series(df[ist_mak_col], kind="fte") if ist_mak_col else 0.0
    out["SOLL_MAK"] = _numeric_compensation_series(df["Soll_FTE"], kind="fte") if "Soll_FTE" in df.columns else 0.0
    out["DELTA_MAK"] = out["IST_MAK"] - out["SOLL_MAK"]

    ist_eur_col = _first_existing_column(df, ["EUR_Reporting", "Total_Cost_Year"])
    out["IST_EUR"] = _numeric_compensation_series(df[ist_eur_col], kind="currency") if ist_eur_col else 0.0
    if "Soll_Cost_Year" in df.columns:
        out["SOLL_EUR"] = _numeric_compensation_series(df["Soll_Cost_Year"], kind="currency")
    else:
        out["SOLL_EUR"] = df.apply(calculate_soll_cost, axis=1)
    out["DELTA_EUR"] = out["IST_EUR"] - out["SOLL_EUR"]

    is_vacant = df["Is_Vacant"].fillna(False).astype(bool) if "Is_Vacant" in df.columns else pd.Series(False, index=df.index)
    person_col = _person_identifier_column(df)
    has_person = df[person_col].notna() if person_col else pd.Series(False, index=df.index)
    out["IST_Kopf"] = ((~is_vacant) & has_person).astype(int)

    if "Sollarbeitszeit" in df.columns:
        soll_hours = _numeric_compensation_series(df["Sollarbeitszeit"], default=float("nan"), kind="fte")
        # SOLL_Kopf ist konservativ als regulaere Planstellenzeile definiert; 0,01 gilt als technisches Artefakt.
        out["SOLL_Kopf"] = ((soll_hours.fillna(0.0) > 0.015) | soll_hours.isna()).astype(int)
    elif {"Planstellennr", "Planstelle"}.intersection(df.columns):
        out["SOLL_Kopf"] = 1
    else:
        out["SOLL_Kopf"] = 0
    out["DELTA_Kopf"] = out["IST_Kopf"] - out["SOLL_Kopf"]

    # ── plan_df: SOLL auf eindeutige gueltige Planstellen korrigieren ─────────
    # Fachlich: SOLL entsteht aus Planstellen, nicht aus Mitarbeiter-Zeilen.
    # Doppelte Planstellennr im Snapshot fuehren sonst zu SOLL-Doppelzaehlung.

    # 1. Technische Mini-Planstellen (Sollarbeitszeit <= 0.015 = Systemartefakt)
    if "Sollarbeitszeit" in df.columns:
        _soll_az = _numeric_compensation_series(df["Sollarbeitszeit"], default=0.0, kind="fte")
        out["Is_Technical_Position"] = (_soll_az > 0.0) & (_soll_az <= 0.015)
    else:
        out["Is_Technical_Position"] = False

    # 2. Duplikate bei Planstellennr markieren — nur erste Zeile je Planstelle
    #    zaehlt fuer SOLL (verhindert Doppelzaehlung bei mehrfach gemeldeten Nrn.)
    if "Planstellennr" in out.columns:
        _pl_str  = out["Planstellennr"].fillna("").astype(str).str.strip()
        _has_pl  = _pl_str.ne("")
        _is_first_pl = ~(_pl_str.where(_has_pl).duplicated(keep="first"))
        out["Is_Duplicate_Planstelle"] = _has_pl & ~_is_first_pl
    else:
        out["Is_Duplicate_Planstelle"] = False

    # 3. SOLL auf Duplikaten auf 0 setzen; Delta danach neu berechnen
    _dup = out["Is_Duplicate_Planstelle"]
    if _dup.any():
        out.loc[_dup, "SOLL_MAK"]  = 0.0
        out.loc[_dup, "SOLL_EUR"]  = 0.0
        out.loc[_dup, "SOLL_Kopf"] = 0
        out["DELTA_MAK"]  = out["IST_MAK"]  - out["SOLL_MAK"]
        out["DELTA_EUR"]  = out["IST_EUR"]   - out["SOLL_EUR"]
        out["DELTA_Kopf"] = out["IST_Kopf"]  - out["SOLL_Kopf"]

    # 4. SOLL_Planstellen: semantisch klarerer Name fuer SOLL_Kopf
    #    Hinweis: SOLL_EUR basiert auf Soll_Cost_Year falls vorhanden,
    #    sonst auf calculate_soll_cost (TVOEED-Fallback). Quell-Spalten
    #    Soll_EG_Source / Soll_Cost_Source sind aktuell nicht im Snapshot.
    out["SOLL_Planstellen"] = out["SOLL_Kopf"]

    # 5. Reporting-Sicht: View-Spalten (exklusionsbereinigtes SOLL fuer UI)
    #    Is_Excluded kommt aus apply_exclusions() via df. Echte Vakanzen
    #    (Is_Vacant=True ohne Is_Excluded=True) bleiben im SOLL erhalten.
    #    Die Dedup-Bereinigung (Is_Duplicate_Planstelle) ist in SOLL_MAK /
    #    SOLL_Planstellen bereits enthalten — View erbt diese Nullung.
    _is_excl = (
        df["Is_Excluded"].fillna(False).astype(bool)
        if "Is_Excluded" in df.columns
        else pd.Series(False, index=df.index)
    )
    out["SOLL_MAK_View"]         = out["SOLL_MAK"].where(~_is_excl, 0.0)
    out["SOLL_EUR_View"]         = out["SOLL_EUR"].where(~_is_excl, 0.0)
    out["SOLL_Planstellen_View"] = out["SOLL_Planstellen"].where(~_is_excl, 0)
    out["DELTA_MAK_View"]        = out["IST_MAK"]  - out["SOLL_MAK_View"]
    out["DELTA_EUR_View"]        = out["IST_EUR"]  - out["SOLL_EUR_View"]
    out["DELTA_Koepfe_View"]     = out["IST_Kopf"] - out["SOLL_Planstellen_View"]

    # 6. Transparenz: besetzte Zeilen mit Soll_FTE=0 klassifizieren
    out["Ist_ohne_Plan_Soll_Kategorie"] = _classify_ist_ohne_plan_soll(df, out)

    preferred_order = [
        "Planebene", "Planebene_Source", "Organisationseinheit", "Kürzel OrgEinheit",
        "OE-Cluster", "JF-Cluster", "Jobfamily", "Planstellennr", "Planstelle",
        "Planstellenkürzel", "Ist_Entgeltgruppe", "Ist_Stufe", "Soll_Entgeltgruppe",
        "Soll_Stufe", "IST_MAK", "SOLL_MAK", "DELTA_MAK", "IST_Kopf", "SOLL_Kopf",
        "SOLL_Planstellen", "DELTA_Kopf", "IST_EUR", "SOLL_EUR", "DELTA_EUR",
        # Reporting-Sicht View-Spalten (exklusionsbereinigt)
        "SOLL_MAK_View", "DELTA_MAK_View",
        "SOLL_EUR_View", "DELTA_EUR_View",
        "SOLL_Planstellen_View", "DELTA_Koepfe_View",
        "Is_Vacant", "Is_Excluded", "Exclusion_Group",
        "Is_Technical_Position", "Is_Duplicate_Planstelle",
        "Ist_ohne_Plan_Soll_Kategorie",
        "PersNr", "Personalnummer", "Personalnachname", "Personalvorname",
        "Vergütungsklasse",
        "Status kundenindividuell", "Vertragsart", "Phase", "ATZ_Status",
        "Ausbildung", "Ist_Azubi",
    ]
    return out[[col for col in preferred_order if col in out.columns]].reset_index(drop=True)



def _compensation_metric_columns(metric: str, view: str) -> tuple[str | None, str | None, str | None, str]:
    # Reporting-Sicht: SOLL und DELTA verwenden View-Spalten (exklusionsbereinigt).
    # IST bleibt unveraendert (IST_MAK, IST_EUR, IST_Kopf).
    metric_map = {
        "MAK":    ("IST_MAK",  "SOLL_MAK_View",          "DELTA_MAK_View",    "MAK"),
        "Köpfe":  ("IST_Kopf", "SOLL_Planstellen_View",   "DELTA_Koepfe_View", "Köpfe"),
        "Koepfe": ("IST_Kopf", "SOLL_Planstellen_View",   "DELTA_Koepfe_View", "Köpfe"),
        "Euro":   ("IST_EUR",  "SOLL_EUR_View",           "DELTA_EUR_View",    "EUR"),
        "EUR":    ("IST_EUR",  "SOLL_EUR_View",           "DELTA_EUR_View",    "EUR"),
    }
    ist_col, soll_col, delta_col, label = metric_map.get(metric, metric_map["MAK"])
    if view == "IST":
        return ist_col, None, None, label
    if view == "SOLL":
        return soll_col, None, None, label
    if view == "Delta":
        return delta_col, None, None, label
    return ist_col, soll_col, None, label


def _compensation_metric_base_columns(metric: str) -> tuple[str, str, str, str]:
    ist_col, soll_col, _, value_label = _compensation_metric_columns(metric, "IST vs. SOLL")
    _, _, delta_col, _ = _compensation_metric_columns(metric, "Delta")
    return ist_col or "IST_MAK", soll_col or "SOLL_MAK", delta_col or "DELTA_MAK", value_label


def _aggregate_compensation_for_chart(comp_df: pd.DataFrame, group_cols: list[str], value_col: str) -> pd.DataFrame:
    work = comp_df.copy()
    if value_col == "DELTA_Kopf":
        ist = _aggregate_compensation_for_chart(work, group_cols, "IST_Kopf")
        soll = work.groupby(group_cols, dropna=False)["SOLL_Kopf"].sum().reset_index()
        merged = ist.merge(soll, on=group_cols, how="outer").fillna(0)
        merged[value_col] = merged["IST_Kopf"] - merged["SOLL_Kopf"]
        return merged[group_cols + [value_col]]
    if value_col == "DELTA_Koepfe_View":
        # Reporting-Sicht: IST per PersNr dedupliziert, SOLL aus View-Spalte (exklusionsbereinigt)
        ist = _aggregate_compensation_for_chart(work, group_cols, "IST_Kopf")
        soll = work.groupby(group_cols, dropna=False)["SOLL_Planstellen_View"].sum().reset_index()
        merged = ist.merge(soll, on=group_cols, how="outer").fillna(0)
        merged[value_col] = merged["IST_Kopf"] - merged["SOLL_Planstellen_View"]
        return merged[group_cols + [value_col]]
    if value_col == "IST_Kopf":
        id_col = _person_identifier_column(work)
        if id_col in work.columns:
            active = work[work["IST_Kopf"].fillna(0).gt(0)].copy()
            # Innerhalb einer Gruppierung wird per PersNr dedupliziert; ueber mehrere Gruppen kann dieselbe Person fachlich mehrfach erscheinen.
            return active.groupby(group_cols, dropna=False)[id_col].nunique().reset_index(name=value_col)
    return work.groupby(group_cols, dropna=False)[value_col].sum().reset_index()


def _compensation_is_planlevel_available(comp_df: pd.DataFrame) -> bool:
    return "Planebene_Source" in comp_df.columns and not comp_df["Planebene_Source"].eq("missing").all()


def _compensation_grouped_source(
    comp_df: pd.DataFrame,
    *,
    aggregation: str,
    value_col: str,
) -> pd.DataFrame:
    """Aggregiert die vorhandene Compensation-Basis semantisch fuer IST- und SOLL-Spalten."""
    work = comp_df.copy()
    uses_soll_dimension = value_col.startswith("SOLL_")

    if aggregation == "Planebene":
        group_cols = ["_grp_planebene"]
        work["_grp_planebene"] = work["Planebene"].fillna("Nicht zugeordnet")
    elif aggregation == "Planebene + Entgeltgruppe":
        eg_col = "Soll_Entgeltgruppe" if uses_soll_dimension else "Ist_Entgeltgruppe"
        group_cols = ["_grp_planebene", "_grp_eg"]
        work["_grp_planebene"] = work["Planebene"].fillna("Nicht zugeordnet")
        work["_grp_eg"] = work[eg_col].fillna("Nicht zugeordnet")
    elif aggregation == "Entgeltgruppe + Stufe":
        eg_col = "Soll_Entgeltgruppe" if uses_soll_dimension else "Ist_Entgeltgruppe"
        stufe_col = "Soll_Stufe" if uses_soll_dimension else "Ist_Stufe"
        group_cols = ["_grp_eg", "_grp_stufe"]
        work["_grp_eg"] = work[eg_col].fillna("Nicht zugeordnet")
        work["_grp_stufe"] = work[stufe_col].fillna("Nicht zugeordnet")
    else:
        eg_col = "Soll_Entgeltgruppe" if uses_soll_dimension else "Ist_Entgeltgruppe"
        group_cols = ["_grp_eg"]
        work["_grp_eg"] = work[eg_col].fillna("Nicht zugeordnet")

    agg = _aggregate_compensation_for_chart(work, group_cols, value_col)
    if aggregation == "Entgeltgruppe + Stufe":
        agg["_label"] = agg.apply(lambda row: f"{row['_grp_eg']} / Stufe {row['_grp_stufe']}", axis=1)
    elif aggregation == "Planebene + Entgeltgruppe":
        agg["_label"] = agg.apply(lambda row: f"{row['_grp_planebene']} / {row['_grp_eg']}", axis=1)
    else:
        agg["_label"] = agg[group_cols[0]].astype(str)
    agg["_is_unassigned"] = agg["_label"].astype(str).str.contains("Nicht zugeordnet", case=False, na=False)
    return agg[["_label", "_is_unassigned", value_col]]


def _build_compensation_chart_source(
    comp_df: pd.DataFrame,
    *,
    metric: str,
    aggregation: str,
) -> tuple[pd.DataFrame, str, str, str, str]:
    ist_col, soll_col, delta_col, value_label = _compensation_metric_base_columns(metric)
    ist = _compensation_grouped_source(comp_df, aggregation=aggregation, value_col=ist_col)
    soll = _compensation_grouped_source(comp_df, aggregation=aggregation, value_col=soll_col)
    source = ist.merge(soll, on="_label", how="outer", suffixes=("_ist", "_soll")).fillna(0)
    source["_is_unassigned"] = source.get("_is_unassigned_ist", False) | source.get("_is_unassigned_soll", False)
    source[delta_col] = source[ist_col] - source[soll_col]
    source["_delta_pct"] = source.apply(
        lambda row: row[delta_col] / row[soll_col] if row[soll_col] else None,
        axis=1,
    )
    return source[["_label", "_is_unassigned", ist_col, soll_col, delta_col, "_delta_pct"]], ist_col, soll_col, delta_col, value_label


def _format_compensation_value(value: float, value_label: str, *, signed: bool = False) -> str:
    if pd.isna(value):
        return "-"
    sign = "+" if signed and value > 0 else ""
    if value_label == "EUR":
        abs_value = abs(float(value))
        prefix = "-" if value < 0 else sign
        if abs_value >= 1_000_000:
            return f"{prefix}{abs_value / 1_000_000:.1f} Mio. EUR".replace(".", ",")
        return f"{prefix}{abs_value / 1_000:.0f} TEUR".replace(",", ".")
    if value_label == "Köpfe":
        return f"{sign}{int(round(value)):,}".replace(",", ".")
    return f"{sign}{value:,.1f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _topn_to_limit(top_n: str) -> int | None:
    return None if top_n == "Alle" else int(top_n)


def _compensation_metric_from_sidebar(value_type: str) -> str:
    metric_view = _compact_metric_view_label(normalize_global_metric_view(get_global_metric_view()))
    if metric_view == "Köpfe":
        return "Köpfe"
    if metric_view == "EUR":
        return "Euro"
    if metric_view == "MAK":
        return "MAK"
    return "Euro" if value_type == "eur" else ("Köpfe" if value_type == "koepfe" else "MAK")


def create_compensation_planlevel_chart(
    comp_df: pd.DataFrame,
    *,
    metric: str,
    view: str,
    aggregation: str,
    top_n: str = "15",
    print_mode: bool = False,
) -> go.Figure:
    if comp_df.empty:
        return go.Figure()

    source, ist_col, soll_col, delta_col, value_label = _build_compensation_chart_source(
        comp_df,
        metric=metric,
        aggregation=aggregation,
    )
    main = source[~source["_is_unassigned"]].copy()
    if main.empty:
        main = source.copy()

    fig = go.Figure()
    if view == "IST vs. SOLL":
        main["_sort"] = main[delta_col].abs()
        main = main.sort_values("_sort", ascending=False)
        limit = _topn_to_limit(top_n)
        if limit:
            main = main.head(limit)
        main = main.sort_values("_sort", ascending=True)
        custom = main[[ist_col, soll_col, delta_col, "_delta_pct"]]
        hover = (
            "<b>%{y}</b><br>IST: %{customdata[0]:,.1f}<br>SOLL: %{customdata[1]:,.1f}"
            "<br>Delta: %{customdata[2]:+,.1f}<br>Delta %: %{customdata[3]:.1%}<extra></extra>"
        )
        fig.add_trace(go.Bar(y=main["_label"], x=main[ist_col], name="IST", orientation="h", marker_color="#0088DE", customdata=custom, hovertemplate=hover))
        fig.add_trace(go.Bar(y=main["_label"], x=main[soll_col], name="SOLL", orientation="h", marker_color="#E94D3A", customdata=custom, hovertemplate=hover))
        fig.update_layout(barmode="group")
    else:
        value_col = delta_col if view == "Delta" else (soll_col if view == "SOLL" else ist_col)
        main["_sort"] = main[delta_col].abs() if view == "Delta" else main[value_col].abs()
        main = main.sort_values("_sort", ascending=False)
        limit = _topn_to_limit(top_n)
        if limit:
            main = main.head(limit)
        main = main.sort_values("_sort", ascending=True)
        colors = main[value_col].apply(lambda x: "#10b981" if x >= 0 else "#E94D3A")
        hover = (
            "<b>%{y}</b><br>IST: %{customdata[0]:,.1f}<br>SOLL: %{customdata[1]:,.1f}"
            "<br>Delta: %{customdata[2]:+,.1f}<br>Delta %: %{customdata[3]:.1%}<extra></extra>"
        )
        fig.add_trace(go.Bar(
            y=main["_label"],
            x=main[value_col],
            name=view,
            orientation="h",
            marker_color=colors,
            customdata=main[[ist_col, soll_col, delta_col, "_delta_pct"]],
            hovertemplate=hover,
        ))

    n_rows = len(main)
    view_title = "Top-Abweichungen" if view == "Delta" else view
    x_title = f"Delta {value_label}" if view == "Delta" else f"{view if view != 'IST vs. SOLL' else 'IST/SOLL'} {value_label}"
    tickformat = ",.0f" if value_label in ("EUR", "Köpfe") else ",.1f"
    fig.update_layout(
        title=dict(text=f"{view_title} {value_label} nach {aggregation}", font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=40, t=50, b=35),
        height=max(320, min(760 if print_mode else 680, n_rows * 34 + 140)),
        xaxis=dict(title=x_title, showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=True, zerolinecolor="#475569", tickformat=tickformat),
        yaxis=dict(title="", showgrid=False),
    )
    return apply_legend_bottom(fig)


def create_compensation_heatmap(
    comp_df: pd.DataFrame,
    *,
    metric: str,
    view: str,
) -> go.Figure:
    heatmap_view = "Delta" if view == "IST vs. SOLL" else view
    value_col, _, _, value_label = _compensation_metric_columns(metric, heatmap_view)
    if value_col is None:
        value_col = _compensation_metric_base_columns(metric)[2]

    source = _compensation_grouped_source(comp_df, aggregation="Entgeltgruppe + Stufe", value_col=value_col)
    source = source[~source["_is_unassigned"]].copy()
    if source.empty:
        return go.Figure()
    split = source["_label"].str.split(" / Stufe ", expand=True)
    source["Entgeltgruppe"] = split[0]
    source["Stufe"] = split[1] if split.shape[1] > 1 else "Nicht zugeordnet"
    pivot = source.pivot_table(index="Entgeltgruppe", columns="Stufe", values=value_col, aggfunc="sum", fill_value=0)
    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=[str(c) for c in pivot.columns],
        y=[str(i) for i in pivot.index],
        colorscale="RdBu" if heatmap_view == "Delta" else "Blues",
        reversescale=(heatmap_view == "Delta"),
        colorbar=dict(title=value_label),
        hovertemplate="<b>%{y}</b><br>Stufe %{x}<br>Wert: %{z:,.1f}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=f"Heatmap Entgeltgruppe x Stufe ({heatmap_view} {value_label})", font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=20, t=50, b=35),
        height=max(320, min(620, len(pivot.index) * 28 + 160)),
        xaxis=dict(title="Stufe"),
        yaxis=dict(title="Entgeltgruppe"),
    )
    return fig


def _format_compensation_detail_table(comp_df: pd.DataFrame) -> pd.DataFrame:
    display_df = comp_df.copy()
    for col in ["IST_MAK", "SOLL_MAK", "DELTA_MAK", "SOLL_MAK_View", "DELTA_MAK_View"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".") if pd.notna(x) else "-")
    for col in ["IST_Kopf", "SOLL_Kopf", "DELTA_Kopf", "SOLL_Planstellen_View", "DELTA_Koepfe_View"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda x: f"{int(x):,}".replace(",", ".") if pd.notna(x) else "-")
    for col in ["IST_EUR", "SOLL_EUR", "DELTA_EUR", "SOLL_EUR_View", "DELTA_EUR_View"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda x: format_currency(x) if pd.notna(x) else "-")
    return display_df



def _compensation_totals(comp_df: pd.DataFrame, metric: str) -> tuple[float, float, float, float | None, str]:
    ist_col, soll_col, delta_col, value_label = _compensation_metric_base_columns(metric)
    all_df = comp_df.assign(_all="Gesamt")
    ist = _aggregate_compensation_for_chart(all_df, ["_all"], ist_col)
    soll = _aggregate_compensation_for_chart(all_df, ["_all"], soll_col)
    ist_value = float(ist[ist_col].sum()) if ist_col in ist.columns else 0.0
    soll_value = float(soll[soll_col].sum()) if soll_col in soll.columns else 0.0
    delta_value = ist_value - soll_value
    fulfillment = ist_value / soll_value if soll_value else None
    return ist_value, soll_value, delta_value, fulfillment, value_label


def _render_compensation_kpis(comp_df: pd.DataFrame, metric: str) -> None:
    ist_value, soll_value, delta_value, fulfillment, value_label = _compensation_totals(comp_df, metric)
    status = "good" if delta_value >= 0 else "warning"
    soll_title    = "SOLL Planstellen" if metric == "K\u00f6pfe" else f"SOLL {value_label}"
    soll_subtitle = "Eindeutige Planstellen" if metric == "K\u00f6pfe" else "Zielbasis"
    kpis = [
        {"title": f"IST {value_label}", "value": _format_compensation_value(ist_value, value_label), "subtitle": "Aktuelle Basis", "icon": "\U0001f4ca", "status": "default"},
        {"title": soll_title, "value": _format_compensation_value(soll_value, value_label), "subtitle": soll_subtitle, "icon": "\U0001f4ca", "status": "default"},
        {"title": f"Delta {value_label}", "value": _format_compensation_value(delta_value, value_label, signed=True), "subtitle": "IST - SOLL", "icon": "\u2194", "status": status},
    ]
    if metric == "MAK":
        kpis.append({
            "title": "Erfüllungsgrad MAK",
            "value": f"{fulfillment * 100:.1f}%".replace(".", ",") if fulfillment is not None else "-",
            "subtitle": "IST / SOLL",
            "icon": "\u2713",
            "status": "good" if fulfillment is not None and fulfillment >= 0.95 else "warning",
        })
    render_kpi_cards_styled(kpis)


def _render_compensation_unassigned_box(comp_df: pd.DataFrame, metric: str, aggregation: str) -> None:
    source, ist_col, soll_col, delta_col, value_label = _build_compensation_chart_source(
        comp_df,
        metric=metric,
        aggregation=aggregation,
    )
    unassigned = source[source["_is_unassigned"]]
    if unassigned.empty:
        return
    ist_value = float(unassigned[ist_col].sum())
    soll_value = float(unassigned[soll_col].sum())
    delta_value = float(unassigned[delta_col].sum())
    st.info(
        "Nicht zugeordnet: "
        f"IST {_format_compensation_value(ist_value, value_label)} / "
        f"SOLL {_format_compensation_value(soll_value, value_label)} / "
        f"Delta {_format_compensation_value(delta_value, value_label, signed=True)}. "
        "Diese Werte werden aus der Hauptgrafik ausgeblendet und separat als Datenqualitätshinweis gezeigt."
    )


def _build_ist_ohne_plan_soll_summary(comp_df: pd.DataFrame) -> pd.DataFrame | None:
    """Gibt Aggregation nach Ist_ohne_Plan_Soll_Kategorie zurueck, oder None."""
    cat_col = "Ist_ohne_Plan_Soll_Kategorie"
    if cat_col not in comp_df.columns:
        return None
    subset = comp_df[comp_df[cat_col].ne("")]
    if subset.empty:
        return None

    pers_col = "PersNr" if "PersNr" in comp_df.columns else None
    pl_col   = "Planstellennr" if "Planstellennr" in comp_df.columns else None

    rows = []
    for cat in _IST_OHNE_SOLL_CAT_ORDER:
        g = subset[subset[cat_col] == cat]
        if g.empty:
            continue
        rows.append({
            "Kategorie":  cat,
            "Zeilen":     len(g),
            "Personen":   int(g[pers_col].dropna().astype(str).str.strip().ne("").sum()) if pers_col else len(g),
            "IST_MAK":    float(g["IST_MAK"].sum()) if "IST_MAK" in g.columns else 0.0,
            "IST_EUR":    float(g["IST_EUR"].sum()) if "IST_EUR" in g.columns else 0.0,
            "Planstellen": int(g[pl_col].dropna().astype(str).str.strip().ne("").nunique()) if pl_col else 0,
        })

    rows.append({
        "Kategorie":  "GESAMT",
        "Zeilen":     len(subset),
        "Personen":   int(subset[pers_col].dropna().astype(str).str.strip().ne("").sum()) if pers_col else len(subset),
        "IST_MAK":    float(subset["IST_MAK"].sum()) if "IST_MAK" in subset.columns else 0.0,
        "IST_EUR":    float(subset["IST_EUR"].sum()) if "IST_EUR" in subset.columns else 0.0,
        "Planstellen": int(subset[pl_col].dropna().astype(str).str.strip().ne("").nunique()) if pl_col else 0,
    })
    return pd.DataFrame(rows)


def _render_ist_ohne_plan_soll_warning(comp_df: pd.DataFrame, key_prefix: str) -> None:
    """Warnbox und Detail-Download fuer IST-ohne-Plan-SOLL-Faelle."""
    cat_col = "Ist_ohne_Plan_Soll_Kategorie"
    if cat_col not in comp_df.columns:
        return
    subset = comp_df[comp_df[cat_col].ne("")]
    if subset.empty:
        return

    total_eur     = float(subset["IST_EUR"].sum()) if "IST_EUR" in subset.columns else 0.0
    total_ist_eur = float(comp_df["IST_EUR"].sum()) if "IST_EUR" in comp_df.columns else 1.0
    total_mak     = float(subset["IST_MAK"].sum()) if "IST_MAK" in subset.columns else 0.0
    total_zeilen  = len(subset)
    pers_col      = "PersNr" if "PersNr" in subset.columns else None
    total_personen = int(subset[pers_col].dropna().astype(str).str.strip().ne("").sum()) if pers_col else total_zeilen
    anteil        = total_eur / total_ist_eur if total_ist_eur > 0 else 0.0

    st.warning(
        f"**IST ohne Plan-SOLL:** {total_zeilen} Planstellenzeilen ({total_personen} Personen) "
        f"haben IST-Werte, aber Soll_FTE = 0. "
        f"Sie erscheinen im Delta vollständig als IST ohne SOLL und sollten fachlich geprüft werden."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Zeilen",        str(total_zeilen))
    c2.metric("IST_MAK",       f"{total_mak:.2f}".replace(".", ","))
    c3.metric("IST_EUR",       f"{total_eur/1e6:.2f} Mio. €".replace(".", ","))
    c4.metric("Anteil IST_EUR", f"{anteil*100:.1f} %".replace(".", ","))

    regulaer = subset[subset[cat_col] == "Reguläre aktive Stelle ohne Soll_FTE"]
    if len(regulaer) > 0:
        reg_mak = float(regulaer["IST_MAK"].sum()) if "IST_MAK" in regulaer.columns else 0.0
        reg_eur = float(regulaer["IST_EUR"].sum()) if "IST_EUR" in regulaer.columns else 0.0
        st.error(
            f"**Datenqualitätsproblem:** {len(regulaer)} reguläre aktive Beschäftigungsverhältnisse "
            f"haben keine Soll_FTE (IST_MAK {reg_mak:.2f}, IST_EUR {reg_eur/1e6:.2f} Mio. €). "
            "Bitte Planstellen-Datei prüfen."
        )

    # Detail-Download
    detail_cols_wanted = [
        "PersNr", "Personalnummer", "Personalnachname", "Personalvorname",
        "Planstellennr", "Planstelle", "Planstellenkürzel",
        "Organisationseinheit", "Kürzel OrgEinheit",
        "Status kundenindividuell", "Vertragsart", "Phase", "ATZ_Status",
        "Ausbildung", "Ist_Azubi",
        "Ist_Entgeltgruppe", "Ist_Stufe",
        "IST_MAK", "IST_EUR", "SOLL_MAK", "SOLL_EUR",
        "Ist_ohne_Plan_Soll_Kategorie",
    ]
    detail_df = subset[[c for c in detail_cols_wanted if c in subset.columns]].copy()
    detail_df = detail_df.rename(columns={"SOLL_MAK": "Soll_FTE", "SOLL_EUR": "Soll_Cost_Year",
                                          "Ist_Entgeltgruppe": "TrfGr", "Ist_Stufe": "Stufe"})

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        detail_df.to_excel(writer, sheet_name="IST ohne Plan-SOLL", index=False)
        ws = writer.sheets["IST ohne Plan-SOLL"]
        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 4, 50)
    buf.seek(0)

    download_button_compat(
        label="Download: IST ohne Plan-SOLL",
        data=buf.getvalue(),
        file_name=f"{key_prefix}_ist_ohne_plan_soll.xlsx",
        mime=_EXCEL_MIME,
        key=f"download_{key_prefix}_ist_ohne_plan_soll",
        width="stretch",
    )


def _tariff_group_sort_key(label: object) -> float:
    """Fachlicher Sortierschlüssel für TVöD-Entgeltgruppen (höherer Wert = höherwertige Gruppe).

    AT / Außertariflich → 100
    E15..E1             → 15.0 .. 1.0  (E9C=9.3, E9B=9.2, E9A=9.1, E9=9.0)
    TVAöD / Ausbildung  → 0
    Nicht zugeordnet    → -1
    Unbekannt           → -2
    """
    import re
    s = str(label).strip()
    su = re.sub(r"\s+", "", s.upper())

    # AT / Außertariflich
    if su in ("AT", "AUSSERTARIFLICH", "AUSSERTARIFLICH"):
        return 100.0

    # E-Gruppen: E1..E15, optional Suffix A/B/C (auch EG-Schreibweise)
    m = re.match(r"^E(?:G)?(\d+)([ABC]?)$", su)
    if m:
        num  = int(m.group(1))
        frac = {"A": 0.1, "B": 0.2, "C": 0.3}.get(m.group(2), 0.0)
        return float(num) + frac

    # TVAöD / Ausbildung
    if any(x in su for x in ("TVAÖD", "TVAOD", "AUSBILDUNG")):
        return 0.0

    # Leer / Nicht zugeordnet / Sonderzeichen
    if not s or su in ("-", "NAN", "NONE") or "NICHTZUGEORDNET" in su or su.startswith("NICHT"):
        return -1.0

    return -2.0


def _sort_by_entgeltgruppe(df: pd.DataFrame, *, ascending: bool = True) -> pd.DataFrame:
    """Fachlich korrekte Sortierung nach TVöD-Entgeltgruppe via _tariff_group_sort_key.

    ascending=True  → E1 zuerst, E15 zuletzt  (für Plotly categoryarray: E15 erscheint oben)
    ascending=False → E15 zuerst, E1 zuletzt  (für Tabelle: E15 in erster Zeile = oben)
    """
    df = df.copy()
    df["_eg_order"] = df["_label"].apply(_tariff_group_sort_key)
    return df.sort_values("_eg_order", ascending=ascending).drop(columns=["_eg_order"])


def _create_eg_ist_vs_soll_chart(
    main: pd.DataFrame,
    *,
    ist_col: str,
    soll_col: str,
    delta_col: str,
    value_label: str,
    print_mode: bool = False,
) -> go.Figure:
    """Horizontales gruppiertes Balkendiagramm IST vs. SOLL nach Entgeltgruppe (fachlich sortiert)."""
    if main.empty:
        return go.Figure()
    custom = main[[ist_col, soll_col, delta_col, "_delta_pct"]].values
    hover = (
        "<b>%{y}</b><br>IST: %{customdata[0]:,.1f}<br>SOLL: %{customdata[1]:,.1f}"
        "<br>Delta: %{customdata[2]:+,.1f}<br>Delta%%: %{customdata[3]:.1%%}<extra></extra>"
    )
    tickformat = ",.0f" if value_label in ("EUR", "Köpfe") else ",.1f"
    n_rows = len(main)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=main["_label"], x=main[ist_col], name="IST",
        orientation="h", marker_color="#0088DE", customdata=custom, hovertemplate=hover,
    ))
    fig.add_trace(go.Bar(
        y=main["_label"], x=main[soll_col], name="SOLL",
        orientation="h", marker_color="#E94D3A", customdata=custom, hovertemplate=hover,
    ))
    # categoryarray in ascending order → Plotly maps first item to bottom, last to top
    # → E15 (last) appears at the top of the chart, E1 / TVAöD at the bottom
    ordered_labels = main["_label"].tolist()
    fig.update_layout(
        barmode="group",
        title=dict(text=f"IST vs. SOLL {value_label} nach Entgeltgruppe", font=dict(size=15, color="#1e293b"), x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=40, t=50, b=35),
        height=max(320, min(760 if print_mode else 680, n_rows * 34 + 140)),
        xaxis=dict(
            title=f"IST/SOLL {value_label}", showgrid=True,
            gridcolor="rgba(226, 232, 240, 0.8)", zeroline=True,
            zerolinecolor="#475569", tickformat=tickformat,
        ),
        yaxis=dict(
            title="", showgrid=False,
            categoryorder="array",
            categoryarray=ordered_labels,
        ),
    )
    return apply_legend_bottom(fig)


def _build_eg_summary_table(
    main: pd.DataFrame,
    *,
    ist_col: str,
    soll_col: str,
    delta_col: str,
    value_label: str,
) -> pd.DataFrame:
    """Übersichtstabelle Entgeltgruppe | IST | SOLL | Delta | Delta % mit Gesamtzeile."""
    rows = []
    for _, row in main.iterrows():
        ist_v   = float(row[ist_col])
        soll_v  = float(row[soll_col])
        delta_v = float(row[delta_col])
        pct_raw = row.get("_delta_pct")
        pct_str = (
            f"{pct_raw:+.1%}".replace(".", ",")
            if pct_raw is not None and not pd.isna(pct_raw)
            else "-"
        )
        rows.append({
            "Entgeltgruppe": row["_label"],
            "IST":     _format_compensation_value(ist_v, value_label),
            "SOLL":    _format_compensation_value(soll_v, value_label),
            "Delta":   _format_compensation_value(delta_v, value_label, signed=True),
            "Delta %": pct_str,
        })
    tot_ist   = float(main[ist_col].sum())
    tot_soll  = float(main[soll_col].sum())
    tot_delta = tot_ist - tot_soll
    tot_pct   = tot_delta / tot_soll if tot_soll else None
    rows.append({
        "Entgeltgruppe": "Gesamt",
        "IST":     _format_compensation_value(tot_ist, value_label),
        "SOLL":    _format_compensation_value(tot_soll, value_label),
        "Delta":   _format_compensation_value(tot_delta, value_label, signed=True),
        "Delta %": f"{tot_pct:+.1%}".replace(".", ",") if tot_pct is not None else "-",
    })
    return pd.DataFrame(rows)


def render_compensation_planlevel_section(
    df: pd.DataFrame,
    *,
    value_type: str,
    key_prefix: str,
    view_mode: str | None = None,
    print_mode: bool = False,
) -> None:
    comp_df = build_compact_compensation_planlevel_df(df)
    if comp_df.empty:
        st.warning("Keine Vergütungsdaten verfügbar.")
        return

    has_planlevel = _compensation_is_planlevel_available(comp_df)

    # Header — an IST-Seite angeglichen (divider=True)
    st.subheader("IST vs. SOLL nach Entgeltgruppen", divider=True)
    st.caption("Reporting-Sicht · exklusionsbereinigt · aktive Filter berücksichtigt")
    if not st.session_state.get("tvoed_available", bool(st.session_state.get("tvoed_lookup", {}))):
        st.caption("TVÖD-Tabelle nicht verfügbar – Euro-Werte basieren auf konfigurierten Fallback-Werten.")

    # Fest verdrahtete Werte — keine UI-Regler
    metric      = _compensation_metric_from_sidebar(value_type)
    aggregation = "Entgeltgruppe"

    _render_ist_ohne_plan_soll_warning(comp_df, key_prefix)

    # Info zu offenen nicht-exkludierten Planstellen (optional, bei relevanter SOLL_EUR-Summe)
    if "Is_Excluded" in comp_df.columns and "IST_Kopf" in comp_df.columns:
        _ov_mask = (
            ~comp_df["Is_Excluded"].fillna(False).astype(bool)
            & (comp_df["IST_Kopf"].fillna(0) == 0)
        )
        if "SOLL_MAK" in comp_df.columns:
            _ov_mask = _ov_mask & (comp_df["SOLL_MAK"].fillna(0).gt(0) | comp_df["SOLL_EUR"].fillna(0).gt(0))
        _n_ov_info  = int(_ov_mask.sum())
        _eur_ov_info = float(comp_df.loc[_ov_mask, "SOLL_EUR_View"].sum()) if "SOLL_EUR_View" in comp_df.columns else 0.0
        if _n_ov_info > 0 and _eur_ov_info > 1_000:
            st.caption(
                f"{_n_ov_info} offene Planstellen sind im SOLL enthalten "
                f"und erklären ca. {_format_compensation_value(_eur_ov_info, 'EUR')} der Abweichung."
            )

    # Datenbasis: IST nach Ist_Entgeltgruppe, SOLL nach Soll_Entgeltgruppe, outer join
    source, ist_col, soll_col, delta_col, value_label = _build_compensation_chart_source(
        comp_df, metric=metric, aggregation=aggregation,
    )
    main = source[~source["_is_unassigned"]].copy()
    if main.empty:
        main = source.copy()

    chart_col, table_col = st.columns([1.55, 1])

    with chart_col:
        # ascending=True: E1 zuerst, E15 zuletzt → categoryarray legt E15 oben in Plotly ab
        chart_data = _sort_by_entgeltgruppe(main, ascending=True)
        fig = _create_eg_ist_vs_soll_chart(
            chart_data,
            ist_col=ist_col, soll_col=soll_col, delta_col=delta_col,
            value_label=value_label, print_mode=print_mode,
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            key=f"{key_prefix}_comp_eg_chart",
            config={"displayModeBar": False},
        )

    with table_col:
        # ascending=False: E15 zuerst → E15 in erster Tabellenzeile = oben
        table_data = _sort_by_entgeltgruppe(main, ascending=False)
        tbl = _build_eg_summary_table(
            table_data,
            ist_col=ist_col, soll_col=soll_col, delta_col=delta_col, value_label=value_label,
        )
        dataframe_compat(tbl, width="stretch", hide_index=True)

        excel_data = export_to_excel(
            comp_df,
            dimension_name="Verguetung auf Planebene" if has_planlevel else "Verguetung nach Entgeltgruppen",
            value_type=value_type,
            key_prefix=key_prefix,
        )
        download_button_compat(
            label="Excel Download",
            data=excel_data,
            file_name=f"{key_prefix}_verguetung_planebene.xlsx",
            mime=_EXCEL_MIME,
            key=f"download_{key_prefix}_verguetung_planebene",
            width="stretch",
        )

    _render_compensation_unassigned_box(comp_df, metric, aggregation)

    # Methodik — standardmäßig geschlossen, unterhalb von Grafik/Tabelle
    with st.expander("Hinweise zur Berechnung", expanded=False):
        st.markdown(
            "**Reporting-Sicht:** Aktive Exklusionsgruppen und Filter definieren den Betrachtungsraum. "
            "IST und SOLL werden innerhalb dieses Scopes verglichen.\n\n"
            "**Köpfe / Planstellen:** IST zeigt eindeutige Personen je Entgeltgruppe. "
            "SOLL zeigt gültige Planstellen. "
            "Personen mit mehreren Planstellen können über mehrere Entgeltgruppen hinweg mehrfach erscheinen."
        )

    # Debug-Expander nur bei URL-Parameter ?debug=1
    if bool(st.query_params.get("debug")):
        with st.expander("Berechnungsprüfung", expanded=True):
            _n_dup   = int(comp_df["Is_Duplicate_Planstelle"].sum()) if "Is_Duplicate_Planstelle" in comp_df.columns else 0
            _n_tech  = int(comp_df["Is_Technical_Position"].sum())   if "Is_Technical_Position"   in comp_df.columns else 0
            _n_excl  = int(comp_df["Is_Excluded"].sum())             if "Is_Excluded"             in comp_df.columns else 0
            _ist_mak = float(comp_df["IST_MAK"].sum())
            _ist_eur = float(comp_df["IST_EUR"].sum())
            _soll_mak_full  = float(comp_df["SOLL_MAK"].sum())
            _soll_eur_full  = float(comp_df["SOLL_EUR"].sum())
            _soll_pl_full   = int(comp_df["SOLL_Planstellen"].sum()) if "SOLL_Planstellen" in comp_df.columns else 0
            _soll_mak_view  = float(comp_df["SOLL_MAK_View"].sum())       if "SOLL_MAK_View"       in comp_df.columns else _soll_mak_full
            _soll_eur_view  = float(comp_df["SOLL_EUR_View"].sum())       if "SOLL_EUR_View"       in comp_df.columns else _soll_eur_full
            _soll_pl_view   = int(comp_df["SOLL_Planstellen_View"].sum()) if "SOLL_Planstellen_View" in comp_df.columns else _soll_pl_full
            _delta_eur_view = _ist_eur - _soll_eur_view
            _budget_ausch   = _ist_eur / _soll_eur_view * 100 if _soll_eur_view > 0 else 0.0

            _lines = [
                "**Planstellen-Deduplizierung**",
                f"- Doppelte Planstellennr (SOLL=0 gesetzt): {_n_dup}",
                f"- Technische Mini-Planstellen (Soll_FTE ≤ 0,015): {_n_tech}",
                f"- Exkludierte Zeilen (Is_Excluded=True): {_n_excl}",
                "",
                "**MAK — Reporting-Sicht**",
                f"- IST_MAK:              {_ist_mak:.4f}",
                f"- SOLL_MAK (Reporting): {_soll_mak_view:.4f}",
                f"- DELTA_MAK:            {_ist_mak - _soll_mak_view:+.4f}",
                "",
                "**EUR — Vollständiger Stellenplan vs. Reporting-Sicht**",
                f"  *(Basis Soll_Cost_Year — bei TVÖD-Verfügbarkeit exakt, sonst Fallback)*",
                f"- Vollständiges SOLL_EUR (inkl. Exkl.): {_soll_eur_full:>18,.2f}",
                f"- Reporting-SOLL_EUR  (ohne Exkl.):     {_soll_eur_view:>18,.2f}",
                f"- Herausgerechnetes SOLL_EUR:            {_soll_eur_full - _soll_eur_view:>18,.2f}",
                f"- IST_EUR:                               {_ist_eur:>18,.2f}",
                f"- DELTA_EUR (Reporting):                 {_delta_eur_view:>+18,.2f}",
                f"- Budget-Ausschöpfung (Reporting):       {_budget_ausch:.1f} %",
                "",
                "**Planstellen — Vollständig vs. Reporting-Sicht**",
                f"- Vollständig:  {_soll_pl_full}",
                f"- Reporting:    {_soll_pl_view}",
                f"- Differenz:    {_soll_pl_full - _soll_pl_view}",
                "",
                "**MAK — Vollständig vs. Reporting-Sicht**",
                f"- Vollständig:  {_soll_mak_full:.4f}",
                f"- Reporting:    {_soll_mak_view:.4f}",
                f"- Differenz:    {_soll_mak_full - _soll_mak_view:.4f}",
            ]
            if _soll_eur_view == 0.0:
                _lines += ["", ":warning: SOLL_EUR_View = 0 — prüfe ob TVÖD-Tabelle geladen und TrfGr/Soll_FTE im Snapshot vorhanden sind."]
            if _n_dup > 5:
                _lines += ["", f":warning: Mehr als 5 doppelte Planstellennr ({_n_dup}). Bitte Snapshot-Qualität prüfen."]
            st.markdown("\n".join(_lines))

            if "Exclusion_Group" in comp_df.columns and _n_excl > 0:
                excl_df = comp_df[comp_df["Is_Excluded"].fillna(False).astype(bool)].copy()
                grp_agg = excl_df.groupby("Exclusion_Group", dropna=False).agg(
                    Zeilen          = ("Exclusion_Group", "count"),
                    SOLL_MAK        = ("SOLL_MAK",        "sum"),
                    SOLL_EUR        = ("SOLL_EUR",        "sum"),
                    SOLL_Planstellen= ("SOLL_Planstellen","sum"),
                ).reset_index()
                grp_agg["SOLL_MAK"]         = grp_agg["SOLL_MAK"].map(lambda x: f"{x:.3f}")
                grp_agg["SOLL_EUR"]         = grp_agg["SOLL_EUR"].map(lambda x: f"{x:,.0f} €")
                grp_agg["SOLL_Planstellen"] = grp_agg["SOLL_Planstellen"].map(lambda x: f"{int(x)}")
                st.markdown("**Exklusionsgruppen — SOLL in Exklusionen (vollständiger Stellenplan)**")
                dataframe_compat(grp_agg, width="stretch", hide_index=True)

            if "Is_Excluded" in comp_df.columns and "IST_Kopf" in comp_df.columns:
                _ov_m = ~comp_df["Is_Excluded"].fillna(False).astype(bool) & (comp_df["IST_Kopf"].fillna(0) == 0)
                if "SOLL_MAK" in comp_df.columns:
                    _ov_m = _ov_m & (comp_df["SOLL_MAK"].fillna(0).gt(0) | comp_df["SOLL_EUR"].fillna(0).gt(0))
                _ov_df  = comp_df[_ov_m].copy()
                _n_ov   = len(_ov_df)
                _ov_mak = float(_ov_df["SOLL_MAK_View"].sum()) if "SOLL_MAK_View" in _ov_df.columns else 0.0
                _ov_eur = float(_ov_df["SOLL_EUR_View"].sum()) if "SOLL_EUR_View" in _ov_df.columns else 0.0
                st.markdown(
                    f"**Offene nicht-exkludierte Planstellen** (Is_Excluded=False, IST_Kopf=0, SOLL>0)\n\n"
                    f"- Anzahl Planstellen: **{_n_ov}**\n"
                    f"- SOLL_MAK: {_ov_mak:.3f}\n"
                    f"- SOLL_EUR: {_ov_eur:,.0f} €"
                )
                if _n_ov > 0:
                    _oe_col = next((c for c in ["Kürzel OrgEinheit", "Organisationseinheit"] if c in _ov_df.columns), None)
                    _eur_col_ov = "SOLL_EUR_View" if "SOLL_EUR_View" in _ov_df.columns else "SOLL_EUR"
                    if _oe_col:
                        _top_oe = _ov_df.groupby(_oe_col, dropna=False)[_eur_col_ov].sum().nlargest(5).reset_index()
                        _top_oe.columns = [_oe_col, "SOLL_EUR"]
                        _top_oe["SOLL_EUR"] = _top_oe["SOLL_EUR"].map(lambda x: f"{x:,.0f} €")
                        st.markdown("*Top Organisationseinheiten nach SOLL_EUR:*")
                        dataframe_compat(_top_oe, width="stretch", hide_index=True)
                    _ps_cols = [c for c in ["Planstellennr", "Planstelle"] if c in _ov_df.columns]
                    if _ps_cols:
                        _top_ps = _ov_df.groupby(_ps_cols, dropna=False)[_eur_col_ov].sum().nlargest(5).reset_index()
                        _top_ps[_eur_col_ov] = _top_ps[_eur_col_ov].map(lambda x: f"{x:,.0f} €")
                        st.markdown("*Top Planstellen nach SOLL_EUR:*")
                        dataframe_compat(_top_ps, width="stretch", hide_index=True)

            _summary = _build_ist_ohne_plan_soll_summary(comp_df)
            if _summary is not None and not _summary.empty:
                st.markdown("**IST ohne Plan-SOLL — Kategorisierung**")
                _sfmt = _summary.copy()
                _sfmt["IST_MAK"] = _sfmt["IST_MAK"].map(lambda x: f"{x:.3f}")
                _sfmt["IST_EUR"] = _sfmt["IST_EUR"].map(lambda x: f"{x:,.0f} €")
                dataframe_compat(_sfmt, width="stretch", hide_index=True)


@st.cache_data
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
        xaxis=dict(showgrid=True, gridcolor="rgba(226, 232, 240, 0.8)", zeroline=False),
        yaxis=dict(showgrid=False)
    )
    
    fig = apply_legend_bottom(fig)


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
        yaxis=dict(showgrid=False),
    )
    
    fig = apply_legend_bottom(fig)


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
        st.markdown(f"**{t('compact.common.data_table')}**")
        display_df = range_df[[
            "Planstelle", "min_label", "n_min", "mean_label", "max_label", "n_max", "count"
        ]].copy()
        display_df.columns = ["Planstelle", "Min", "n(Min)", "Mittel", "Max", "n(Max)", "Gesamt"]
        dataframe_compat(display_df, width="stretch", hide_index=True)

        excel_data = export_to_excel(
            range_df[["Planstelle", "min_label", "n_min", "max_label", "n_max",
                       "mean_label", "min_ord", "max_ord", "mean_ord", "count"]],
            dimension_name="Qualifikationsspannweite pro Planstelle",
            key_prefix=key_prefix,
        )
        download_button_compat(
            label="Excel Download",
            data=excel_data,
            file_name=f"{key_prefix}_qualifikation_spannweite.xlsx",
            mime=_EXCEL_MIME,
            key=f"download_{key_prefix}_edu_range",
            width="stretch",
        )

    if print_mode:
        st.markdown('</div>', unsafe_allow_html=True)


# =============================================================================
# TABELLEN-FORMATIERUNG
# =============================================================================

@st.cache_data
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
    return _render_single_breakdown_clean(
        df,
        dimension_name,
        dimension_col,
        value_col,
        value_type=value_type,
        key_prefix=key_prefix,
        print_mode=print_mode,
    )

    # Sonderfall: Vergütungsklassen → gestapeltes Tarif-Chart
    is_verguetung = dimension_col == "Vergütungsklasse"

    if not is_verguetung and dimension_col not in df.columns:
        st.warning(f"Dimension '{localized_dimension_name}' nicht verfügbar (Spalte '{dimension_col}' fehlt).")
        return

    if is_verguetung and ("TrfGr" not in df.columns or "St" not in df.columns):
        st.warning(f"Vergütungsdaten nicht verfügbar (TrfGr/St fehlen).")
        return

    # Print-Block Wrapper für saubere Seitenumbrüche
    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    st.subheader(localized_dimension_name)
    if is_verguetung:
        # Gestapeltes Tarif-Chart
        col_chart, col_table = st.columns([3, 2])

        with col_chart:
            fig = create_stacked_tariff_chart(df, value_col, title="", print_mode=print_mode, value_type=value_type)
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown(f"**{t('compact.common.data_table')}**")
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

            excel_data = export_to_excel(
                breakdown_df,
                dimension_name=localized_dimension_name,
                value_type=value_type,
                key_prefix=key_prefix,
            )
            download_button_compat(
                label="Excel Download",
                data=excel_data,
                file_name=f"{key_prefix}_verguetungsklassen.xlsx",
                mime=_EXCEL_MIME,
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )
    else:
        # Standard: Horizontales Balkendiagramm
        breakdown_df = create_breakdown_table(df, dimension_col, value_col)

        if breakdown_df.empty or "Hinweis" in breakdown_df.columns:
            st.warning(f"Keine Daten für '{localized_dimension_name}' verfügbar.")
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
            st.markdown(f"**{t('compact.common.data_table')}**")
            display_df = format_dataframe_for_display(breakdown_df, value_type)
            dataframe_compat(display_df, width="stretch", hide_index=True)

            excel_data = export_to_excel(
                breakdown_df,
                dimension_name=localized_dimension_name,
                value_type=value_type,
                key_prefix=key_prefix,
            )
            download_button_compat(
                label="Excel Download",
                data=excel_data,
                file_name=f"{key_prefix}_{localized_dimension_name.lower().replace(' ', '_')}.xlsx",
                mime=_EXCEL_MIME,
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
    return _render_single_comparison_clean(
        df,
        dimension_name,
        dimension_col,
        ist_col,
        soll_col,
        value_type=value_type,
        key_prefix=key_prefix,
        print_mode=print_mode,
    )
    is_verguetung = dimension_col == "Vergütungsklasse"

    if not is_verguetung and dimension_col not in df.columns:
        st.warning(f"Dimension '{localized_dimension_name}' nicht verfügbar.")
        return

    if is_verguetung and ("TrfGr" not in df.columns or "St" not in df.columns):
        st.warning(f"Vergütungsdaten nicht verfügbar (TrfGr/St fehlen).")
        return

    # Print-Block Wrapper für saubere Seitenumbrüche
    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    st.subheader(localized_dimension_name)

    if is_verguetung:
        col_chart, col_table = st.columns([3, 2])
        with col_chart:
            fig = create_stacked_tariff_comparison_chart(
                df, ist_col, soll_col, title="", print_mode=print_mode, value_type=value_type,
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown(f"**{t('compact.common.data_table')}**")
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

            excel_data = export_to_excel(
                breakdown_df,
                dimension_name=localized_dimension_name,
                value_type=value_type,
                key_prefix=key_prefix,
            )
            download_button_compat(
                label="Excel Download",
                data=excel_data,
                file_name=f"{key_prefix}_verguetungsklassen.xlsx",
                mime=_EXCEL_MIME,
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )
    else:
        breakdown_df = create_breakdown_table(df, dimension_col, ist_col,
                                              include_soll=True, soll_col=soll_col)

        if breakdown_df.empty or "Hinweis" in breakdown_df.columns:
            st.warning(f"Keine Daten für '{localized_dimension_name}' verfügbar.")
            if print_mode:
                st.markdown('</div>', unsafe_allow_html=True)
            return

        col_chart, col_table = st.columns([3, 2])

        with col_chart:
            fig = create_comparison_chart(breakdown_df, dimension_col, title="", print_mode=print_mode)
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown(f"**{t('compact.common.data_table')}**")
            display_df = format_dataframe_for_display(breakdown_df, value_type)
            dataframe_compat(display_df, width="stretch", hide_index=True)

            excel_data = export_to_excel(
                breakdown_df,
                dimension_name=localized_dimension_name,
                value_type=value_type,
                key_prefix=key_prefix,
            )
            download_button_compat(
                label="Excel Download",
                data=excel_data,
                file_name=f"{key_prefix}_{localized_dimension_name.lower().replace(' ', '_')}.xlsx",
                mime=_EXCEL_MIME,
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )

    # Print-Block Wrapper schließen
    if print_mode:
        st.markdown('</div>', unsafe_allow_html=True)


def _render_single_breakdown_clean(
    df: pd.DataFrame,
    dimension_name: str,
    dimension_col: str,
    value_col: str,
    *,
    value_type: str = "mak",
    key_prefix: str = "",
    print_mode: bool = False,
):
    """Rendert einen Breakdown-Block mit bereinigten Warn- und Spaltenpfaden."""
    localized_dimension_name = _compact_text(dimension_name)
    is_verguetung = dimension_col == "Vergütungsklasse"

    if not is_verguetung and dimension_col not in df.columns:
        st.warning(f"Dimension '{localized_dimension_name}' nicht verfügbar (Spalte '{dimension_col}' fehlt).")
        return

    if is_verguetung and ("TrfGr" not in df.columns or "St" not in df.columns):
        st.warning("Vergütungsdaten nicht verfügbar (TrfGr/St fehlen).")
        return

    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    st.subheader(localized_dimension_name)

    if is_verguetung:
        col_chart, col_table = st.columns([3, 2])
        with col_chart:
            fig = create_stacked_tariff_chart(df, value_col, title="", print_mode=print_mode, value_type=value_type)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_tariff_breakdown_chart")

        with col_table:
            st.markdown(f"**{t('compact.common.data_table')}**")
            breakdown_df = create_stacked_tariff_breakdown_table(df, value_col)
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
                else:
                    display_df[col] = display_df[col].apply(
                        lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
                        if pd.notna(x) and x != 0 else "-"
                    )

            dataframe_compat(display_df, width="stretch", hide_index=True)
            excel_data = export_to_excel(
                breakdown_df,
                dimension_name=localized_dimension_name,
                value_type=value_type,
                key_prefix=key_prefix,
            )
            download_button_compat(
                label="Excel Download",
                data=excel_data,
                file_name=f"{key_prefix}_verguetungsklassen.xlsx",
                mime=_EXCEL_MIME,
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )
    else:
        breakdown_df = create_breakdown_table(df, dimension_col, value_col)
        if breakdown_df.empty:
            st.warning(f"Keine Daten für '{localized_dimension_name}' verfügbar.")
            if print_mode:
                st.markdown('</div>', unsafe_allow_html=True)
            return
        if "Hinweis" in breakdown_df.columns:
            st.warning(str(breakdown_df["Hinweis"].iloc[0]))
            if print_mode:
                st.markdown('</div>', unsafe_allow_html=True)
            return

        col_chart, col_table = st.columns([3, 2])
        with col_chart:
            fig = create_horizontal_bar_chart(
                breakdown_df,
                dimension_col,
                "IST",
                title="",
                preserve_order=(dimension_col in ORDINAL_ORDERS),
                print_mode=print_mode,
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown(f"**{t('compact.common.data_table')}**")
            display_df = format_dataframe_for_display(breakdown_df, value_type)
            dataframe_compat(display_df, width="stretch", hide_index=True)
            excel_data = export_to_excel(
                breakdown_df,
                dimension_name=localized_dimension_name,
                value_type=value_type,
                key_prefix=key_prefix,
            )
            download_button_compat(
                label="Excel Download",
                data=excel_data,
                file_name=f"{key_prefix}_{localized_dimension_name.lower().replace(' ', '_')}.xlsx",
                mime=_EXCEL_MIME,
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )

    if print_mode:
        st.markdown('</div>', unsafe_allow_html=True)


def _render_single_comparison_clean(
    df: pd.DataFrame,
    dimension_name: str,
    dimension_col: str,
    ist_col: str,
    soll_col: str,
    *,
    value_type: str = "mak",
    key_prefix: str = "",
    print_mode: bool = False,
):
    """Rendert einen IST-vs-SOLL-Block mit bereinigten Warn- und Spaltenpfaden."""
    localized_dimension_name = _compact_text(dimension_name)
    is_verguetung = dimension_col == "Vergütungsklasse"

    if not is_verguetung and dimension_col not in df.columns:
        st.warning(f"Dimension '{localized_dimension_name}' nicht verfügbar (Spalte '{dimension_col}' fehlt).")
        return

    if is_verguetung and ("TrfGr" not in df.columns or "St" not in df.columns):
        st.warning("Vergütungsdaten nicht verfügbar (TrfGr/St fehlen).")
        return

    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    if not is_verguetung:
        st.subheader(localized_dimension_name)

    if is_verguetung:
        render_compensation_planlevel_section(
            df,
            value_type=value_type,
            key_prefix=key_prefix,
            view_mode="IST vs. SOLL",
            print_mode=print_mode,
        )
        with st.expander("Vergütung nach Entgeltgruppe und Stufe anzeigen", expanded=print_mode):
            col_chart, col_table = st.columns([3, 2])
            with col_chart:
                fig = create_stacked_tariff_comparison_chart(
                    df,
                    ist_col,
                    soll_col,
                    title="",
                    print_mode=print_mode,
                    value_type=value_type,
                )
                st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_tariff_comparison_chart")

            with col_table:
                st.markdown(f"**{t('compact.common.data_table')}**")
                breakdown_df = create_stacked_tariff_breakdown_table(df, ist_col)
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
                    else:
                        display_df[col] = display_df[col].apply(
                            lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
                            if pd.notna(x) and x != 0 else "-"
                        )

                dataframe_compat(display_df, width="stretch", hide_index=True)
                excel_data = export_to_excel(
                    breakdown_df,
                    dimension_name=localized_dimension_name,
                    value_type=value_type,
                    key_prefix=key_prefix,
                )
                download_button_compat(
                    label="Excel Download",
                    data=excel_data,
                    file_name=f"{key_prefix}_verguetungsklassen.xlsx",
                    mime=_EXCEL_MIME,
                    key=f"download_{key_prefix}_{dimension_col}",
                    width="stretch",
                )
    else:
        breakdown_df = create_breakdown_table(
            df,
            dimension_col,
            ist_col,
            include_soll=True,
            soll_col=soll_col,
        )
        if breakdown_df.empty:
            st.warning(f"Keine Daten für '{localized_dimension_name}' verfügbar.")
            if print_mode:
                st.markdown('</div>', unsafe_allow_html=True)
            return
        if "Hinweis" in breakdown_df.columns:
            st.warning(str(breakdown_df["Hinweis"].iloc[0]))
            if print_mode:
                st.markdown('</div>', unsafe_allow_html=True)
            return

        col_chart, col_table = st.columns([3, 2])
        with col_chart:
            fig = create_comparison_chart(breakdown_df, dimension_col, title="", print_mode=print_mode)
            st.plotly_chart(fig, use_container_width=True)

        with col_table:
            st.markdown(f"**{t('compact.common.data_table')}**")
            display_df = format_dataframe_for_display(breakdown_df, value_type)
            dataframe_compat(display_df, width="stretch", hide_index=True)
            excel_data = export_to_excel(
                breakdown_df,
                dimension_name=localized_dimension_name,
                value_type=value_type,
                key_prefix=key_prefix,
            )
            download_button_compat(
                label="Excel Download",
                data=excel_data,
                file_name=f"{key_prefix}_{localized_dimension_name.lower().replace(' ', '_')}.xlsx",
                mime=_EXCEL_MIME,
                key=f"download_{key_prefix}_{dimension_col}",
                width="stretch",
            )

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
        {"title": t("compact.kpi.total_mak_effective"), "value": format_number(total_mak, 1),
         "subtitle": t("compact.kpi.raw_and_heads", raw_value=format_number(total_fte_roh, 1), headcount=total_koepfe), "icon": "📈", "status": "good"},
        {"title": t("compact.kpi.avg_fte"), "value": format_number(avg_fte, 2),
         "subtitle": t("compact.kpi.per_employee"), "icon": "📊", "status": "default"},
        {"title": t("compact.kpi.part_time_rate"), "value": f"{teilzeit['quote_pct']:.1f}%".replace(".", ","),
         "subtitle": t("compact.kpi.part_time_employees", count=teilzeit['count']), "icon": "⏰", "status": "default"},
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
        localized_themenfeld = _compact_text(themenfeld)
        if not print_mode:
            # Themenfeld mit Anker für Scroll-Navigation (verwende Streamlit anchor)
            anchor_id = f"ist-mak-{idx}"
            st.subheader(localized_themenfeld, anchor=anchor_id, divider=True)

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST-MAK > {localized_themenfeld}")

            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col=next((c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in emp_df.columns), "FTE_assigned"),
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
        {"title": t("compact.kpi.total_heads"), "value": format_number(total_koepfe, 0),
         "subtitle": t("compact.kpi.employees"), "icon": "👥", "status": "good"},
        {"title": t("compact.kpi.female_share"), "value": format_percent(female_rate),
         "subtitle": t("compact.kpi.women_count", count=female_count), "icon": "👤", "status": "default"},
        {"title": t("compact.kpi.atz_quote"), "value": f"{atz['quote_headcount_pct']:.1f}%".replace(".", ","),
         "subtitle": t("compact.kpi.in_partial_retirement", count=atz['gesamt']), "icon": "⏰", "status": "default"},
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
        localized_themenfeld = _compact_text(themenfeld)
        if not print_mode:
            # Themenfeld mit Anker für Scroll-Navigation (verwende Streamlit anchor)
            anchor_id = f"ist-koepfe-{idx}"
            st.subheader(localized_themenfeld, anchor=anchor_id, divider=True)

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST-Köpfe > {localized_themenfeld}")

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

    cost_col = "EUR_Reporting" if "EUR_Reporting" in emp_df.columns else "Total_Cost_Year"
    if cost_col not in emp_df.columns:
        st.warning("Kostenfeld 'Total_Cost_Year' nicht verfügbar.")
        return

    # KPIs
    total_cost = get_ist_eur(emp_df)
    total_koepfe = get_ist_koepfe(emp_df)
    avg_cost = total_cost / total_koepfe if total_koepfe > 0 else 0
    total_mak = get_ist_mak(emp_df)
    cost_per_mak = total_cost / total_mak if total_mak > 0 else 0

    kpis = [
        {"title": t("compact.kpi.total_cost"), "value": format_currency(total_cost),
         "subtitle": t("compact.kpi.annual_costs"), "icon": "💰", "status": "good"},
        {"title": t("compact.kpi.cost_per_head"), "value": format_currency(avg_cost),
         "subtitle": t("compact.kpi.average"), "icon": "👤", "status": "default"},
        {"title": t("compact.kpi.cost_per_fte"), "value": format_currency(cost_per_mak),
         "subtitle": t("compact.kpi.per_fte"), "icon": "📊", "status": "default"},
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
        localized_themenfeld = _compact_text(themenfeld)
        if not print_mode:
            # Themenfeld mit Anker für Scroll-Navigation (verwende Streamlit anchor)
            anchor_id = f"ist-eur-{idx}"
            st.subheader(localized_themenfeld, anchor=anchor_id, divider=True)

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST-EUR > {localized_themenfeld}")

            render_single_breakdown(
                emp_df, dimension_name, dimension_col,
                value_col=cost_col,
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
    cost_col = "EUR_Reporting" if "EUR_Reporting" in df.columns else "Total_Cost_Year"
    if cost_col not in df.columns:
        st.warning("IST-Kosten nicht verfügbar.")
        return

    if "Soll_Cost_Year" not in df.columns:
        st.info("SOLL-Kosten werden aus Tarifgruppe/Step geschätzt.")

    _eur_comp = build_compact_compensation_planlevel_df(df)
    total_ist  = float(_eur_comp["IST_EUR"].sum())       if "IST_EUR"       in _eur_comp.columns else get_ist_eur(df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df)
    total_soll = float(_eur_comp["SOLL_EUR_View"].sum()) if "SOLL_EUR_View" in _eur_comp.columns else get_soll_eur(df)
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
                ist_col=cost_col,
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
    st.subheader("Executive Summary")
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
    mak_status = "🔴" if erfuellungsgrad_mak < 0.85 else ("🟡" if erfuellungsgrad_mak < 0.95 else "🟢")
    budget_status = "🔴" if budget_quote > 1.05 else ("🟢" if budget_quote <= 1.02 else "🟡")

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
    st.markdown("**Top 3 Handlungsfelder:**")
    handlungsfelder = []
    if erfuellungsgrad_mak < 0.85:
        handlungsfelder.append(f"**SOFORT:** Recruiting-Offensive starten - MAK-Gap von {format_number(total_soll_mak - total_mak, 1)} MAK schließen")
    elif erfuellungsgrad_mak < 0.95:
        handlungsfelder.append(f"Recruiting beschleunigen - MAK-Gap von {format_number(total_soll_mak - total_mak, 1)} MAK schließen")
    else:
        handlungsfelder.append(f"🟢 MAK-Kapazität gut - Erfüllungsgrad bei {erfuellungsgrad_mak*100:.1f}%")
    if budget_quote > 1.05:
        handlungsfelder.append(f"**DRINGEND:** Budget-Überschreitung von {format_currency((total_cost - total_soll_cost)/1e6, 1)} Mio. € analysieren!")
    elif budget_quote <= 1.02:
        handlungsfelder.append("🟢 Budget im Plan - Disziplin beibehalten")
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


def _render_cover_page_and_toc_clean(filter_summary: str, df: pd.DataFrame) -> None:
    """Rendert Deckblatt und Inhaltsverzeichnis mit kanonischen UTF-8-Texten."""
    today = datetime.now().strftime("%d.%m.%Y")

    st.markdown("<br><br><br>", unsafe_allow_html=True)
    st.markdown('<h1 style="text-align:center; color:#0088DE;">HR Pulse Dashboard</h1>', unsafe_allow_html=True)
    st.markdown('<h2 style="text-align:center; color:#64748b;">Kompakt-Auswertung</h2>', unsafe_allow_html=True)
    st.markdown(f'<p style="text-align:center; color:#94a3b8;"><strong>{today}</strong></p>', unsafe_allow_html=True)
    st.markdown(f'<p style="text-align:center; color:#94a3b8;">{filter_summary}</p>', unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)

    st.subheader("Executive Summary")
    from dataloader.kpi_engine import compute_planstellen_kpis

    plan_kpis = compute_planstellen_kpis(df)
    total_planstellen = plan_kpis["total"]
    total_koepfe = get_ist_koepfe(df)
    vakanzen = plan_kpis["vakanzen"]
    besetzungsgrad = plan_kpis["besetzungsquote"] / 100 if total_planstellen > 0 else 0

    _cov_comp = build_compact_compensation_planlevel_df(df)
    total_mak      = float(_cov_comp["IST_MAK"].sum())       if "IST_MAK"       in _cov_comp.columns else get_ist_mak(df)
    total_cost     = float(_cov_comp["IST_EUR"].sum())        if "IST_EUR"       in _cov_comp.columns else get_ist_eur(df)
    total_soll_mak = float(_cov_comp["SOLL_MAK_View"].sum()) if "SOLL_MAK_View" in _cov_comp.columns else get_soll_mak(df)
    total_soll_cost= float(_cov_comp["SOLL_EUR_View"].sum()) if "SOLL_EUR_View" in _cov_comp.columns else get_soll_eur(df)
    erfuellungsgrad_mak = total_mak / total_soll_mak if total_soll_mak > 0 else 0
    budget_quote = total_cost / total_soll_cost if total_soll_cost > 0 else 0
    mak_status = "🔴" if erfuellungsgrad_mak < 0.85 else ("🟡" if erfuellungsgrad_mak < 0.95 else "🟢")
    budget_status = "🔴" if budget_quote > 1.05 else ("🟢" if budget_quote <= 1.02 else "🟡")

    summary_data = {
        "Kennzahl": [
            "Planstellen gesamt",
            "Mitarbeitende (Köpfe)",
            "Besetzungsgrad",
            f"{mak_status} MAK-Erfüllungsgrad",
            f"{budget_status} Budget-Quote",
        ],
        "Wert": [
            f"{total_planstellen:,}",
            f"{total_koepfe:,}",
            f"{besetzungsgrad * 100:.1f}% ({vakanzen} Vakanzen)",
            f"{erfuellungsgrad_mak * 100:.1f}% ({format_number(total_mak, 1)} / {format_number(total_soll_mak, 1)})",
            f"{budget_quote * 100:.1f}% ({format_currency(total_cost / 1e6, 1)} Mio. € / {format_currency(total_soll_cost / 1e6, 1)} Mio. €)",
        ],
    }
    st.table(pd.DataFrame(summary_data))

    st.markdown("**Top 3 Handlungsfelder:**")
    handlungsfelder = []
    if erfuellungsgrad_mak < 0.85:
        handlungsfelder.append(
            f"**SOFORT:** Recruiting-Offensive starten - MAK-Gap von {format_number(total_soll_mak - total_mak, 1)} MAK schließen"
        )
    elif erfuellungsgrad_mak < 0.95:
        handlungsfelder.append(
            f"Recruiting beschleunigen - MAK-Gap von {format_number(total_soll_mak - total_mak, 1)} MAK schließen"
        )
    else:
        handlungsfelder.append(f"🟢 MAK-Kapazität gut - Erfüllungsgrad bei {erfuellungsgrad_mak * 100:.1f}%")
    if budget_quote > 1.05:
        handlungsfelder.append(
            f"**DRINGEND:** Budget-Überschreitung von {format_currency((total_cost - total_soll_cost) / 1e6, 1)} Mio. € analysieren!"
        )
    elif budget_quote <= 1.02:
        handlungsfelder.append("🟢 Budget im Plan - Disziplin beibehalten")
    else:
        handlungsfelder.append(f"Budget beobachten - {budget_quote * 100:.1f}% Quote")

    from dataloader.kpi_engine import compute_atz_kpis as _atz_cover

    atz_count = _atz_cover(df)["gesamt"]
    if atz_count > 0:
        handlungsfelder.append(f"Nachfolgeplanung für {atz_count} ATZ-Mitarbeitende vorbereiten")
    for i, feld in enumerate(handlungsfelder[:3], 1):
        st.markdown(f"{i}. {feld}")

    page_break()

    st.subheader("Inhaltsverzeichnis")
    toc_data = {
        "Section": [
            "1. Executive Summary",
            "2. Inhaltsverzeichnis",
            "",
            "IST-Analysen",
            "3. IST-MAK Analyse (Kapazität)",
            "4. IST-Köpfe Analyse (Headcount)",
            "5. IST-EUR Analyse (Kosten)",
            "",
            "IST vs SOLL Vergleiche",
            "6. IST vs SOLL MAK (Kapazitäts-Gap)",
            "7. IST vs SOLL EUR (Budget-Analyse)",
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
            "~21",
        ],
    }
    st.table(pd.DataFrame(toc_data))
    page_break()


render_cover_page_and_toc = _render_cover_page_and_toc_clean


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

    st.markdown(f"### {t('compact.summary.title')}: {_compact_text(title)}")

    if summary_data.get('kennzahlen'):
        st.markdown(f"**{t('compact.summary.metrics_glance')}**")
        for kz in summary_data['kennzahlen']:
            icon = "✓" if kz.get('status') == 'good' else ("!" if kz.get('status') == 'warning' else "•")
            st.markdown(f"- {icon} **{_compact_text(kz['label'])}:** {kz['value']}")
        st.markdown("")

    if summary_data.get('insights'):
        st.markdown(f"**{t('compact.summary.key_insights')}**")
        for insight in summary_data['insights']:
            icon = "•" if insight.get('type') == 'info' else ("!" if insight.get('type') == 'warning' else "✓")
            st.markdown(f"{icon} {_compact_text(insight['text'])}")
        st.markdown("")

    if summary_data.get('handlungsempfehlungen'):
        st.markdown(f"**{t('compact.summary.recommendations')}**")
        for i, emp in enumerate(summary_data['handlungsempfehlungen'], 1):
            st.markdown(f"{i}. {_compact_text(emp)}")

    st.markdown('</div>', unsafe_allow_html=True)




@st.cache_data
def analyze_ist_mak_data(df: pd.DataFrame) -> dict:
    """Analysiert IST-MAK Daten und erstellt Management Summary."""
    from dataloader.kpi_engine import compute_teilzeit_kpis as _tz_mak
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df

    total_mak = get_ist_mak(df)
    total_koepfe = get_ist_koepfe(df)
    avg_fte = total_mak / total_koepfe if total_koepfe > 0 else 0

    _tz = _tz_mak(df)
    teilzeit_rate = _tz["quote_pct"] / 100

    kennzahlen = [
        {"label": t("compact.summary.metric.total_mak"), "value": format_number(total_mak, 1), "status": "good"},
        {"label": t("compact.summary.metric.employees"), "value": format_number(total_koepfe, 0), "status": "good"},
        {"label": t("compact.summary.metric.avg_fte"), "value": format_number(avg_fte, 2), "status": "good" if avg_fte >= 0.85 else "warning"},
        {"label": t("compact.summary.metric.part_time_rate"), "value": format_percent(teilzeit_rate), "status": "good" if teilzeit_rate < 0.4 else "warning"},
    ]

    insights = []
    if teilzeit_rate > 0.4:
        insights.append({
            "type": "warning",
            "text": t("compact.insight.part_time_high", rate=format_percent(teilzeit_rate)),
        })
    elif teilzeit_rate > 0.25:
        insights.append({
            "type": "info",
            "text": t("compact.insight.part_time_moderate", rate=format_percent(teilzeit_rate)),
        })
    else:
        insights.append({
            "type": "good",
            "text": t("compact.insight.part_time_low", rate=format_percent(teilzeit_rate)),
        })

    if avg_fte < 0.8:
        insights.append({
            "type": "warning",
            "text": t("compact.insight.avg_fte_low", value=format_number(avg_fte, 2)),
        })

    handlungsempfehlungen = []
    if teilzeit_rate > 0.4:
        handlungsempfehlungen.append(t("compact.rec.full_time_incentives"))

    if avg_fte < 0.8:
        handlungsempfehlungen.append(t("compact.rec.part_time_causes"))

    if len(handlungsempfehlungen) == 0:
        handlungsempfehlungen.append(t("compact.rec.monitor_capacity"))
        handlungsempfehlungen.append(t("compact.rec.retention_full_time"))

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen,
    }



@st.cache_data
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


@st.cache_data
def analyze_ist_eur_data(df: pd.DataFrame) -> dict:
    """Analysiert IST-EUR Daten und erstellt Management Summary."""
    emp_df = df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df
    from dataloader.kpi_engine import get_unique_employees
    unique_emp = get_unique_employees(emp_df) if "PersNr" in emp_df.columns else emp_df

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
    if "Total_Cost_Year" in unique_emp.columns:
        high_cost = (unique_emp["Total_Cost_Year"] > 80000).sum()
        high_cost_rate = high_cost / len(unique_emp) if len(unique_emp) > 0 else 0

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
        handlungsempfehlungen.append(t("compact.rec.monitor_costs"))
        handlungsempfehlungen.append("Retention-Maßnahmen für Schlüsselkräfte prüfen")

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen
    }


@st.cache_data
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


def _render_education_range_section_clean(df: pd.DataFrame,
                                          key_prefix: str = "",
                                          print_mode: bool = False):
    """Saubere, lokalisierte Variante der Qualifikations-Spannweite."""
    language = get_language()

    title = "📊 Qualifikation pro Planstelle" if language == "de" else "📊 Qualification by position"
    empty_text = (
        "Keine Planstellen mit ausreichend Daten gefunden. "
        "(Mindestens {min_persons} Personen mit bekannter Ausbildung pro Stelle nötig.)"
        if language == "de"
        else "No positions with sufficient data were found. "
        "(At least {min_persons} people with known qualification per position are required.)"
    )
    unknown_note = (
        "Hinweis: {count} Personen mit unbekanntem Ausbildungsabschluss wurden bei der Berechnung ausgeschlossen."
        if language == "de"
        else "Note: {count} employees with an unknown qualification were excluded from the calculation."
    )
    table_columns = (
        ["Planstelle", "Min", "n(Min)", "Mittel", "Max", "n(Max)", "Gesamt"]
        if language == "de"
        else ["Position", "Min", "n(Min)", "Mean", "Max", "n(Max)", "Total"]
    )
    export_dimension_name = (
        "Qualifikationsspannweite pro Planstelle"
        if language == "de"
        else "Qualification range by position"
    )

    if print_mode:
        st.markdown('<div class="print-block">', unsafe_allow_html=True)

    st.subheader(title)

    min_persons = 2
    range_df = create_education_range_data(df, min_persons=min_persons)

    if range_df.empty:
        st.warning(empty_text.format(min_persons=min_persons))
        if print_mode:
            st.markdown('</div>', unsafe_allow_html=True)
        return

    n_unknown = range_df.attrs.get("n_unknown", 0)
    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        fig = create_education_range_chart(range_df, print_mode=print_mode)
        st.plotly_chart(fig, use_container_width=True)
        if n_unknown > 0:
            st.caption(unknown_note.format(count=n_unknown))

    with col_table:
        st.markdown(f"**{t('compact.common.data_table')}**")
        display_df = range_df[[
            "Planstelle", "min_label", "n_min", "mean_label", "max_label", "n_max", "count"
        ]].copy()
        display_df.columns = table_columns
        dataframe_compat(display_df, width="stretch", hide_index=True)

        excel_data = export_to_excel(
            range_df[["Planstelle", "min_label", "n_min", "max_label", "n_max",
                      "mean_label", "min_ord", "max_ord", "mean_ord", "count"]],
            dimension_name=export_dimension_name,
            key_prefix=key_prefix,
        )
        download_button_compat(
            label="Excel Download",
            data=excel_data,
            file_name=f"{key_prefix}_qualifikation_spannweite.xlsx",
            mime=_EXCEL_MIME,
            key=f"download_{key_prefix}_edu_range",
            width="stretch",
        )

    if print_mode:
        st.markdown('</div>', unsafe_allow_html=True)


def _analyze_ist_vs_soll_mak_data_clean(df: pd.DataFrame) -> dict:
    """Saubere, lokalisierte Variante der Summary für IST vs SOLL MAK."""
    language = get_language()

    _comp = build_compact_compensation_planlevel_df(df)
    total_ist  = float(_comp["IST_MAK"].sum())       if "IST_MAK"       in _comp.columns else get_ist_mak(df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df)
    total_soll = float(_comp["SOLL_MAK_View"].sum()) if "SOLL_MAK_View" in _comp.columns else get_soll_mak(df)
    delta = total_ist - total_soll
    erfuellungsgrad = total_ist / total_soll if total_soll > 0 else 0

    if language == "de":
        kennzahlen = [
            {"label": "IST-MAK", "value": format_number(total_ist, 1), "status": "good"},
            {"label": "SOLL-MAK", "value": format_number(total_soll, 1), "status": "good"},
            {"label": "Delta", "value": f"{delta:+.1f}".replace(".", ","), "status": "good" if abs(delta) < 10 else "warning"},
            {"label": "Erfüllungsgrad", "value": format_percent(erfuellungsgrad), "status": "good" if erfuellungsgrad >= 0.95 else ("warning" if erfuellungsgrad >= 0.85 else "critical")},
        ]
    else:
        kennzahlen = [
            {"label": "Current MAK", "value": format_number(total_ist, 1), "status": "good"},
            {"label": "Target MAK", "value": format_number(total_soll, 1), "status": "good"},
            {"label": "Delta", "value": f"{delta:+.1f}".replace(".", ","), "status": "good" if abs(delta) < 10 else "warning"},
            {"label": "Fulfillment rate", "value": format_percent(erfuellungsgrad), "status": "good" if erfuellungsgrad >= 0.95 else ("warning" if erfuellungsgrad >= 0.85 else "critical")},
        ]

    insights = []
    vakanzen = int(df["Is_Vacant"].sum()) if "Is_Vacant" in df.columns else 0
    if language == "de":
        if erfuellungsgrad < 0.85:
            insights.append({"type": "warning", "text": f"Kritische Unterbesetzung: Nur {format_percent(erfuellungsgrad)} der Soll-Kapazität besetzt!"})
            if vakanzen > 0:
                insights.append({"type": "warning", "text": f"{vakanzen} offene Stellen identifiziert - Recruiting beschleunigen!"})
        elif erfuellungsgrad < 0.95:
            insights.append({"type": "warning", "text": f"Moderate Unterbesetzung bei {format_percent(erfuellungsgrad)} - {abs(delta):.1f} MAK fehlen."})
        elif erfuellungsgrad > 1.05:
            insights.append({"type": "info", "text": f"Überbesetzung bei {format_percent(erfuellungsgrad)} - {delta:+.1f} MAK über Soll."})
        else:
            insights.append({"type": "good", "text": f"Optimale Besetzung bei {format_percent(erfuellungsgrad)} Erfüllungsgrad."})
    else:
        if erfuellungsgrad < 0.85:
            insights.append({"type": "warning", "text": f"Critical understaffing: only {format_percent(erfuellungsgrad)} of target capacity is filled."})
            if vakanzen > 0:
                insights.append({"type": "warning", "text": f"{vakanzen} open positions identified - accelerate recruiting."})
        elif erfuellungsgrad < 0.95:
            insights.append({"type": "warning", "text": f"Moderate understaffing at {format_percent(erfuellungsgrad)} - {abs(delta):.1f} MAK are missing."})
        elif erfuellungsgrad > 1.05:
            insights.append({"type": "info", "text": f"Overstaffing at {format_percent(erfuellungsgrad)} - {delta:+.1f} MAK above target."})
        else:
            insights.append({"type": "good", "text": f"Optimal staffing at a fulfillment rate of {format_percent(erfuellungsgrad)}."})

    if language == "de":
        if erfuellungsgrad < 0.85:
            handlungsempfehlungen = [
                "SOFORT: Recruiting-Offensive starten, Zeitarbeit prüfen",
                "Prioritäten setzen: Welche Positionen sind kritisch?",
                "Überstunden/Mehrarbeit in kritischen Bereichen genehmigen",
            ]
        elif erfuellungsgrad < 0.95:
            handlungsempfehlungen = [
                "Recruiting beschleunigen, Time-to-Hire reduzieren",
                "Interne Umbesetzungen prüfen",
            ]
        elif erfuellungsgrad > 1.05:
            handlungsempfehlungen = [
                "Überkapazitäten analysieren - sind alle Stellen notwendig?",
                "Budget-Einsparungspotenziale prüfen",
            ]
        else:
            handlungsempfehlungen = ["Aktuelle Besetzung halten, Fluktuation minimieren"]
    else:
        if erfuellungsgrad < 0.85:
            handlungsempfehlungen = [
                "IMMEDIATE: launch a recruiting push and review temporary staffing",
                "Set priorities: which roles are business-critical?",
                "Approve overtime/additional work in critical areas",
            ]
        elif erfuellungsgrad < 0.95:
            handlungsempfehlungen = [
                "Accelerate recruiting and reduce time-to-hire",
                "Review internal redeployment options",
            ]
        elif erfuellungsgrad > 1.05:
            handlungsempfehlungen = [
                "Analyze excess capacity - are all roles still required?",
                "Review budget savings potential",
            ]
        else:
            handlungsempfehlungen = ["Maintain the current staffing level and minimize attrition"]

    return {
        "kennzahlen": kennzahlen,
        "insights": insights,
        "handlungsempfehlungen": handlungsempfehlungen,
    }


def _render_ist_vs_soll_mak_tab_clean(df: pd.DataFrame, print_mode: bool = False):
    """Saubere, lokalisierte Rendering-Variante für IST vs SOLL MAK."""
    language = get_language()
    if "Soll_FTE" not in df.columns:
        st.warning("SOLL-FTE nicht verfügbar." if language == "de" else "Target FTE is not available.")
        return

    _kpi_comp = build_compact_compensation_planlevel_df(df)
    total_ist  = float(_kpi_comp["IST_MAK"].sum())       if "IST_MAK"       in _kpi_comp.columns else get_ist_mak(df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df)
    total_soll = float(_kpi_comp["SOLL_MAK_View"].sum()) if "SOLL_MAK_View" in _kpi_comp.columns else get_soll_mak(df)
    delta = total_ist - total_soll
    erfuellungsgrad = total_ist / total_soll if total_soll > 0 else 0
    status = "good" if erfuellungsgrad >= 0.95 else ("warning" if erfuellungsgrad >= 0.85 else "critical")

    if language == "de":
        kpis = [
            {"title": "IST-MAK", "value": format_number(total_ist, 1), "subtitle": "Tatsächliche Kapazität", "icon": "📊", "status": "default"},
            {"title": "SOLL-MAK", "value": format_number(total_soll, 1), "subtitle": "Geplante Kapazität", "icon": "📊", "status": "default"},
            {"title": "Delta", "value": f"{delta:+.1f}".replace(".", ","), "subtitle": "IST - SOLL", "icon": "📉" if delta < 0 else "📈", "status": status},
            {"title": "Erfüllungsgrad", "value": format_percent(erfuellungsgrad), "subtitle": "IST / SOLL", "icon": "📊", "status": status},
        ]
    else:
        kpis = [
            {"title": "Current MAK", "value": format_number(total_ist, 1), "subtitle": "Actual capacity", "icon": "📊", "status": "default"},
            {"title": "Target MAK", "value": format_number(total_soll, 1), "subtitle": "Planned capacity", "icon": "📊", "status": "default"},
            {"title": "Delta", "value": f"{delta:+.1f}".replace(".", ","), "subtitle": "Current - target", "icon": "📉" if delta < 0 else "📈", "status": status},
            {"title": "Fulfillment rate", "value": format_percent(erfuellungsgrad), "subtitle": "Current / target", "icon": "📊", "status": status},
        ]
    render_kpi_cards_styled(kpis)

    if print_mode:
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
    else:
        st.markdown("---")

    for themenfeld, dimensionen in THEMENFELDER_SOLL.items():
        if not print_mode:
            st.markdown(f"### {themenfeld}")

        for dimension_name, dimension_col in dimensionen:
            if print_mode:
                st.caption(f"IST vs SOLL MAK > {themenfeld}")

            if dimension_col == "Ausbildung":
                _render_education_range_section_clean(
                    df,
                    key_prefix="ist_vs_soll_mak",
                    print_mode=print_mode,
                )
            else:
                render_single_comparison(
                    df,
                    dimension_name,
                    dimension_col,
                    ist_col="FTE_assigned",
                    soll_col="Soll_FTE",
                    value_type="mak",
                    key_prefix="ist_vs_soll_mak",
                    print_mode=print_mode,
                )
            if print_mode:
                st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)
            else:
                st.markdown("---")

    summary_data = _analyze_ist_vs_soll_mak_data_clean(df)
    render_management_summary("IST vs SOLL MAK", summary_data, print_mode)


render_education_range_section = _render_education_range_section_clean
analyze_ist_vs_soll_mak_data = _analyze_ist_vs_soll_mak_data_clean
render_ist_vs_soll_mak_tab = _render_ist_vs_soll_mak_tab_clean


@st.cache_data
def analyze_ist_vs_soll_eur_data(df: pd.DataFrame) -> dict:
    """Analysiert IST vs SOLL EUR Daten und erstellt Management Summary."""
    _eur_sum = build_compact_compensation_planlevel_df(df)
    total_ist  = float(_eur_sum["IST_EUR"].sum())       if "IST_EUR"       in _eur_sum.columns else get_ist_eur(df[~df["Is_Vacant"]] if "Is_Vacant" in df.columns else df)
    total_soll = float(_eur_sum["SOLL_EUR_View"].sum()) if "SOLL_EUR_View" in _eur_sum.columns else get_soll_eur(df)
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
# IST vs SOLL KÖPFE
# =============================================================================

def _build_soll_ist_pivot(df: pd.DataFrame, use_max_eg: bool = True):
    """
    Baut die Soll-Ist-Matrix fuer den Koepfe-Vergleich.

    use_max_eg=True  → Soll-EG aus Spalte I "Text Gehaltsband" (Maximalwert), Fallback Spalte H
    use_max_eg=False → Soll-EG ausschließlich aus Spalte H "Bewertung Tarifgruppe" (Basiswert)

    Returns (pivot, soll_order, ist_eg_cols, IST_UNBESETZT, IST_NOT_FOUND)
    """
    from config.settings import TARIFF_GROUPS
    from utils.settings_loader import get_setting

    IST_UNBESETZT  = "Unbesetzt"
    IST_NOT_FOUND  = "Nicht gefunden"

    # Nur inkludierte OEs
    ex = get_setting("exclusions", {})
    ex_units = ex.get("org_units", [])
    work = df.copy()
    if ex_units and "Kürzel OrgEinheit" in work.columns:
        s_ou = work["Kürzel OrgEinheit"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        explicit = [u for u in ex_units if u != "99XX"]
        mask_excl = s_ou.isin(explicit)
        if "99XX" in ex_units:
            mask_excl = mask_excl | (s_ou.str.startswith("99") & ~s_ou.isin(set(explicit)))
        work = work[~mask_excl]

    # Personenbasierte Exklusion auf Planstellen anwenden (opt-in Toggle)
    if ex.get("planstellen_follow_person", False):
        person_pl_mask = pd.Series(False, index=work.index)
        if ex.get("vorstand") and "MitarbGruppenbez." in work.columns:
            person_pl_mask |= work["MitarbGruppenbez."].astype(str).str.strip() == "Vorstand"
        if ex.get("ruhend_bv") and "Status kundenindividuell" in work.columns:
            person_pl_mask |= (
                work["Status kundenindividuell"].astype(str).str.strip()
                == "Ruhendes Beschäftigungsverhältnis"
            )
        if person_pl_mask.any():
            work = work[~person_pl_mask]

    # Soll-EG aus Planstellen
    # Vorrang: Spalte I "Text Gehaltsband" (oberes Ende des Gehaltsbandes, z. B. "bis E11")
    # Fallback: Spalte H "Bewertung Tarifgruppe" (Basisbewertung)
    if "Bewertung Tarifgruppe" not in work.columns:
        return None, [], [], IST_UNBESETZT, IST_NOT_FOUND

    def _normalize_eg_raw(s) -> str:
        """Bereinigt einen EG-Rohwert: strip, upper, Leerzeichen weg, 'BIS'-Präfix entfernen."""
        if pd.isna(s):
            return ""
        v = str(s).strip().upper().replace(" ", "")
        if v.startswith("BIS"):
            v = v[3:].strip()   # "BISE11" → "E11"
        return v

    work = work.copy()
    _invalid = {"", "NAN", "NONE"}

    col_h = work["Bewertung Tarifgruppe"].map(_normalize_eg_raw)
    col_i = work["Text Gehaltsband"].map(_normalize_eg_raw) if "Text Gehaltsband" in work.columns else pd.Series("", index=work.index)

    # Basis- und Maximalwert immer mitführen (für Band-Vergleich im Detailbereich)
    work["_Soll_EG_H"] = col_h                                           # Basiswert (Spalte H)
    work["_Soll_EG_I"] = col_i.where(~col_i.isin(_invalid), other=col_h)  # Maximalwert (I), Fallback H

    if use_max_eg:
        # Spalte I (Maximalwert) hat Vorrang; Fallback auf Spalte H
        work["_Soll_EG"] = work["_Soll_EG_I"]
    else:
        # Ausschließlich Spalte H (Basiswert)
        work["_Soll_EG"] = col_h

    # Ist-Kategorie (vor dem Filter, damit auch Planstellen ohne Soll-EG erfasst werden)
    def _ist_kat(row):
        if row.get("Is_Vacant", True):
            return IST_UNBESETZT
        trfgr = row.get("TrfGr")
        if pd.isna(trfgr) or str(trfgr).strip().lower() in ("", "nan"):
            return IST_NOT_FOUND
        return str(trfgr).strip().upper().replace(" ", "")

    work["_Ist_EG"] = work.apply(_ist_kat, axis=1)

    # Planstellen ohne Soll-EG, die dennoch besetzt sind → Sonderzeile (Option C)
    _no_soll_mask = work["_Soll_EG"].isin(_invalid)
    _no_soll_occupied = work[
        _no_soll_mask & ~work["_Ist_EG"].isin([IST_UNBESETZT, IST_NOT_FOUND])
    ]
    no_soll_eg_row = _no_soll_occupied["_Ist_EG"].value_counts()  # Series {ist_eg: count}

    # Anzahl Planstellen ohne verwertbare Soll-EG festhalten und ausschließen
    n_no_soll_eg = int(_no_soll_mask.sum())
    work = work[~_no_soll_mask]

    # Pivot
    pivot = work.groupby(["_Soll_EG", "_Ist_EG"]).size().unstack(fill_value=0)

    eg_order = {g: i for i, g in enumerate(TARIFF_GROUPS)}
    ist_eg_cols = sorted(
        [c for c in pivot.columns if c not in (IST_UNBESETZT, IST_NOT_FOUND)],
        key=lambda g: eg_order.get(g, 999),
    )
    special_cols = [c for c in (IST_UNBESETZT, IST_NOT_FOUND) if c in pivot.columns]
    pivot = pivot[ist_eg_cols + special_cols]
    pivot["Gesamt"] = pivot.sum(axis=1)

    soll_order = sorted(pivot.index.tolist(), key=lambda g: eg_order.get(g, 999))
    pivot = pivot.loc[soll_order]
    pivot.index.name = "Soll-EG"

    return pivot, soll_order, ist_eg_cols, IST_UNBESETZT, IST_NOT_FOUND, work, n_no_soll_eg, no_soll_eg_row


def _build_soll_ist_pivot_raw_logic(use_max_eg: bool = True):
    """
    Roh-nahe Soll-Ist-Logik fuer den Koepfe-Vergleich.

    Sollkapa 0,01 bleibt fuer diesen Tab unveraendert. Deep-Dive-Exklusionen
    werden in Variante B angewendet: ausgeschlossene Personen/OEs entfallen
    zusammen mit ihrer Planstelle vollstaendig aus der Analyse.
    """
    result = build_soll_ist_koepfe_result(use_max_eg=use_max_eg)
    return (
        result["pivot"],
        result["soll_order"],
        result["ist_eg_cols"],
        result["IST_UNBESETZT"],
        result["IST_NOT_FOUND"],
        result["work_df"],
        result["n_no_soll_eg"],
        result["no_soll_eg_row"],
        result["summary"],
    )


def render_ist_soll_koepfe_tab(df: pd.DataFrame, print_mode: bool = False):
    """
    Rendert den 'IST vs SOLL Köpfe'-Tab.

    Zeigt je Soll-Entgeltgruppe (Planstellen-Bewertung), in welchen tatsaechlichen
    Tarifgruppen die besetzenden Mitarbeitenden eingruppiert sind.
    """
    IST_UNBESETZT = "Unbesetzt"
    IST_NOT_FOUND = "Nicht gefunden"

    # ── Toggle: Maximalwert (Spalte I) vs. Basiswert (Spalte H) ──────────────
    if not print_mode:
        use_max_eg = st.toggle(
            t("compact.ist_soll_heads.toggle.max_grade.label"),
            value=True,
            key="soll_ist_koepfe_use_max_eg",
            help=t("compact.ist_soll_heads.toggle.max_grade.help"),
        )
    else:
        use_max_eg = True  # Im Druckbericht immer Maximalwert

    pivot, soll_order, ist_eg_cols, IST_UNBESETZT, IST_NOT_FOUND, work_df, n_no_soll_eg, no_soll_eg_row, summary = _build_soll_ist_pivot_raw_logic(use_max_eg=use_max_eg)

    if pivot is None:
        st.warning(t("compact.ist_soll_heads.warning.missing_target_column"))
        return

    # ── KPI-Karten ────────────────────────────────────────────────────────────
    st.info(t("compact.ist_soll_heads.reading_hint"))

    total_pl  = int(pivot["Gesamt"].sum())
    unbesetzt = int(pivot[IST_UNBESETZT].sum()) if IST_UNBESETZT in pivot.columns else 0
    not_found = int(pivot[IST_NOT_FOUND].sum()) if IST_NOT_FOUND in pivot.columns else 0
    besetzt   = total_pl - unbesetzt - not_found

    # Roh-nahe Fachlogik fuer diesen Tab:
    # reguläre Sollstellen (ohne 0,01) als KPI-Basis,
    # 0,01-Sonderfälle separat ausweisen.
    total_pl = int(summary["regular_total"])
    besetzt = int(summary["regular_occupied"])
    unbesetzt = int(summary["regular_vacant"])
    not_found = int(summary["matrix_not_found"])
    technical_in_work = int(summary["technical_non9xxx_occupied"])
    matrix_besetzt = int(summary["matrix_occupied"])

    # Band-aware Passungsquote über alle Planstellen
    from config.settings import TARIFF_GROUPS as _TG_KPI
    _eg_rank_kpi = {g: i for i, g in enumerate(_TG_KPI)}
    _rank_ist  = work_df["_Ist_EG"].map(lambda e: _eg_rank_kpi.get(e, 999))
    _rank_h_kpi = work_df["_Soll_EG_H"].map(lambda e: _eg_rank_kpi.get(e, 999)) if "_Soll_EG_H" in work_df.columns else work_df["_Soll_EG"].map(lambda e: _eg_rank_kpi.get(e, 999))
    _rank_i_kpi = work_df["_Soll_EG_I"].map(lambda e: _eg_rank_kpi.get(e, 999)) if "_Soll_EG_I" in work_df.columns else work_df["_Soll_EG"].map(lambda e: _eg_rank_kpi.get(e, 999))
    _rank_soll_kpi = work_df["_Soll_EG"].map(lambda e: _eg_rank_kpi.get(e, 999))
    _occupied  = ~work_df["_Ist_EG"].isin([IST_UNBESETZT, IST_NOT_FOUND])
    _exakt     = _occupied & (_rank_ist == _rank_soll_kpi)
    _im_band   = _occupied & ~_exakt & (_rank_h_kpi <= _rank_ist) & (_rank_ist <= _rank_i_kpi)
    n_passend_exakt   = int(_exakt.sum())
    n_passend_band    = int(_im_band.sum())
    n_passend_gesamt  = n_passend_exakt + n_passend_band
    passend_pct       = n_passend_gesamt / matrix_besetzt * 100 if matrix_besetzt > 0 else 0.0

    kpis = [
        {
            "title": "Regulär auswertbare Planstellen",
            "value": f"{total_pl:,}".replace(",", "."),
            "subtitle": "inkludierte OEs mit verwertbarer Soll-Entgeltgruppe",
            "icon": "📋",
            "status": "default",
        },
        {
            "title": "Besetzt",
            "value": f"{besetzt:,}".replace(",", "."),
            "subtitle": f"{besetzt / total_pl * 100:.1f}% der Planstellen".replace(".", ",") if total_pl else "—",
            "icon": "👤",
            "status": "good",
        },
        {
            "title": "Unbesetzt",
            "value": f"{unbesetzt:,}".replace(",", "."),
            "subtitle": f"{unbesetzt / total_pl * 100:.1f}% der Planstellen".replace(".", ",") if total_pl else "—",
            "icon": "🔲",
            "status": "warning" if unbesetzt > 0 else "good",
        },
        {
            "title": "Passend oder im Band",
            "value": f"{passend_pct:.1f}%".replace(".", ","),
            "subtitle": f"{n_passend_gesamt} Stellen — exakt ({n_passend_exakt}) + im Band ({n_passend_band})",
            "icon": "✅",
            "status": "good" if passend_pct >= 75 else "warning",
        },
        {
            "title": "Ohne Ist-EG",
            "value": f"{not_found:,}".replace(",", "."),
            "subtitle": "Besetzt, aber im Mitarbeiterdatensatz ohne Tarifgruppe",
            "icon": "❓",
            "status": "warning" if not_found > 0 else "good",
        },
    ]
    kpis = [
        {
            "title": "Soll-Stellen (regulaer)",
            "value": f"{total_pl:,}".replace(",", "."),
            "subtitle": "Regulaere Planstellen ohne Sollkapa 0,01",
            "icon": "📋",
            "status": "default",
        },
        {
            "title": "Regulaer besetzt",
            "value": f"{besetzt:,}".replace(",", "."),
            "subtitle": f"{besetzt / total_pl * 100:.1f}% der Planstellen".replace(".", ",") if total_pl else "—",
            "icon": "👤",
            "status": "good",
        },
        {
            "title": "Regulaer unbesetzt",
            "value": f"{unbesetzt:,}".replace(",", "."),
            "subtitle": f"{unbesetzt / total_pl * 100:.1f}% der Planstellen".replace(".", ",") if total_pl else "—",
            "icon": "🔲",
            "status": "warning" if unbesetzt > 0 else "good",
        },
        {
            "title": "Nicht definierte Sollstelle in Arbeit",
            "value": f"{technical_in_work:,}".replace(",", "."),
            "subtitle": "Sollkapa 0,01, nicht 9XXX/99XX, mit Personalnummer",
            "icon": "🧩",
            "status": "warning" if technical_in_work > 0 else "good",
        },
        {
            "title": "Passend oder im Band",
            "value": f"{passend_pct:.1f}%".replace(".", ","),
            "subtitle": f"{n_passend_gesamt} matrixfaehige Stellen — exakt ({n_passend_exakt}) + im Band ({n_passend_band})",
            "icon": "✅",
            "status": "good" if passend_pct >= 75 else "warning",
        },
    ]
    st.markdown(t("compact.ist_soll_heads.summary.heading"))
    render_kpi_cards_styled(kpis)
    st.caption(
        t(
            "compact.ist_soll_heads.summary.caption_primary",
            total_positions=f"{total_pl:,}".replace(",", "."),
            occupied=f"{besetzt:,}".replace(",", "."),
            vacant=f"{unbesetzt:,}".replace(",", "."),
            matching=f"{n_passend_gesamt:,}".replace(",", "."),
        )
    )

    st.caption(
        t(
            "compact.ist_soll_heads.summary.caption_secondary",
            total_positions=f"{total_pl:,}".replace(",", "."),
            occupied=f"{besetzt:,}".replace(",", "."),
            vacant=f"{unbesetzt:,}".replace(",", "."),
            technical_in_work=f"{technical_in_work:,}".replace(",", "."),
        )
    )

    if n_no_soll_eg > 0 and not print_mode:
        st.info(t("compact.ist_soll_heads.summary.no_soll_info", count=n_no_soll_eg))

    if not print_mode:
        st.caption(
            t(
                "compact.ist_soll_heads.summary.technical_caption",
                total=f"{summary['technical_total']:,}".replace(",", "."),
                on_9xxx=f"{summary['technical_9xxx_total']:,}".replace(",", "."),
                outside_9xxx=f"{summary['technical_non9xxx_total']:,}".replace(",", "."),
                occupied=f"{summary['technical_non9xxx_occupied']:,}".replace(",", "."),
            )
        )
        st.markdown("---")

    st.markdown(t("compact.ist_soll_heads.analysis.heading"))
    st.caption(t("compact.ist_soll_heads.analysis.caption"))

    # ── Tabelle ───────────────────────────────────────────────────────────────
    st.subheader(t("compact.ist_soll_heads.matrix.heading"))
    st.caption(t("compact.ist_soll_heads.matrix.caption"))

    # Summenzeile anhängen
    total_row = pivot.sum(axis=0).rename("Gesamt")
    pivot_display = pd.concat([pivot, total_row.to_frame().T])

    # Integer-Formatierung
    def _fmt_int(v):
        try:
            i = int(v)
            return f"{i:,}".replace(",", ".") if i > 0 else "–"
        except (ValueError, TypeError):
            return "–"

    display_df = pivot_display.map(_fmt_int)

    # Spaltenbreite angepasst
    column_config = {col: st.column_config.TextColumn(col, width="small")
                     for col in display_df.columns}
    column_config["Gesamt"] = st.column_config.TextColumn("Gesamt", width="small")

    st.dataframe(
        display_df,
        use_container_width=True,
        column_config=column_config,
    )

    render_compensation_planlevel_section(
        df,
        value_type="koepfe",
        key_prefix="ist_vs_soll_koepfe",
        view_mode="IST vs. SOLL",
        print_mode=print_mode,
    )

    # Excel-Download
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pivot_display.to_excel(writer, sheet_name="Soll-Ist-Köpfe")
        buf.seek(0)
        download_button_compat(
            label=t("compact.ist_soll_heads.matrix.download"),
            data=buf.getvalue(),
            file_name="ist_soll_koepfe.xlsx",
            mime=_EXCEL_MIME,
            key="download_ist_soll_koepfe",
        )
    except Exception:
        pass

    if not print_mode:
        st.markdown("---")
    else:
        st.markdown("<div style='page-break-after: always;'></div>", unsafe_allow_html=True)

    # ── Gestapeltes Balkendiagramm ────────────────────────────────────────────
    st.subheader(t("compact.ist_soll_heads.distribution.heading"))
    st.caption(t("compact.ist_soll_heads.distribution.caption"))

    chart_cols = ist_eg_cols + (
        [IST_UNBESETZT] if IST_UNBESETZT in pivot.columns else []
    ) + (
        [IST_NOT_FOUND] if IST_NOT_FOUND in pivot.columns else []
    )
    chart_pivot = pivot[chart_cols] if chart_cols else pivot.drop(columns=["Gesamt"], errors="ignore")

    # Farbpalette: je Ist-EG eine Farbe (blau-Spektrum), Sonder-Spalten separat
    _BLUE_PALETTE = [
        "#B3E0FF", "#99D6FF", "#66C2FF", "#33AAFF",
        "#0088DE", "#0070BE", "#005A9E", "#004A80",
        "#003D6B", "#003058", "#10b981", "#06b6d4",
        "#8b5cf6", "#f59e0b", "#ec4899", "#0d9488",
    ]
    color_map = {}
    for i, eg in enumerate(ist_eg_cols):
        color_map[eg] = _BLUE_PALETTE[i % len(_BLUE_PALETTE)]
    color_map[IST_UNBESETZT] = "#cbd5e1"   # neutrales Grau
    color_map[IST_NOT_FOUND] = "#fcd9bd"   # gedämpftes Orange — Datenpflegesignal, kein Alarm

    _NOSOLL_LABEL = "(Keine Soll-EG)"
    _has_nosoll_chart = len(no_soll_eg_row) > 0

    fig = go.Figure()
    # Zeilen: SOLL-EG von oben (höchste EG oben → umkehren)
    # Sonderzeile am unteren Ende (wird zuerst in y_order eingefügt → erscheint ganz unten)
    y_order = list(reversed(soll_order))
    if _has_nosoll_chart:
        y_order = [_NOSOLL_LABEL] + y_order  # ganz unten im Chart

    for col in chart_cols:
        values = []
        for eg in y_order:
            if eg == _NOSOLL_LABEL:
                values.append(int(no_soll_eg_row.get(col, 0)))
            else:
                values.append(
                    int(chart_pivot.loc[eg, col]) if eg in chart_pivot.index and col in chart_pivot.columns else 0
                )
        # Sonderzeile: gedämpfte Farbe (Opacity 0.45) für alle Segmente
        _color = color_map.get(col, "#94a3b8")
        _marker = dict(
            color=[f"rgba(160,160,160,0.35)" if eg == _NOSOLL_LABEL else _color for eg in y_order],
            line=dict(color="white", width=0.5),
        )
        fig.add_trace(go.Bar(
            y=y_order,
            x=values,
            name=col,
            orientation="h",
            marker=_marker,
            hovertemplate=t("compact.ist_soll_heads.distribution.hover", ist_group=col),
        ))

    n_rows = len(soll_order) + (1 if _has_nosoll_chart else 0)
    height  = max(350, n_rows * 42)
    if print_mode:
        height = min(height, 700)

    # Trennlinie im Chart zwischen regulären Zeilen und Sonderzeile
    shapes = []
    if _has_nosoll_chart:
        shapes.append(dict(
            type="line",
            x0=0, x1=1, xref="paper",
            y0=0.5, y1=0.5, yref="y",   # zwischen Index 0 (Sonderzeile) und 1 (erste reguläre EG)
            line=dict(color="#94a3b8", width=1.5, dash="dot"),
        ))

    fig.update_layout(
        barmode="stack",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#64748b", size=11),
        margin=dict(l=10, r=20, t=30, b=30),
        height=height,
        shapes=shapes,
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(226,232,240,0.8)",
            zeroline=False,
            tickformat=",d",
        ),
        yaxis=dict(
            showgrid=False,
            categoryorder="array",
            categoryarray=y_order,
        ),
    )
    from utils.plot_helpers import apply_legend_bottom
    fig = apply_legend_bottom(fig)

    st.plotly_chart(fig, use_container_width=True, key="ist_vs_soll_koepfe_distribution_chart")
    if not_found > 0:
        st.caption(t("compact.ist_soll_heads.distribution.missing_ist_caption", count=not_found))

    # ── Detailbereich: eine Soll-EG tiefer analysieren ────────────────────────
    if not print_mode:
        st.markdown("---")
        st.subheader(t("compact.ist_soll_heads.detail.heading"))
        st.caption(t("compact.ist_soll_heads.detail.caption"))

        selected_soll = st.selectbox(
            t("compact.ist_soll_heads.detail.select_label"),
            options=soll_order,
            key="soll_ist_detail_eg",
            label_visibility="collapsed",
        )

        # Zeilen für die gewählte Soll-EG
        detail = work_df[work_df["_Soll_EG"] == selected_soll].copy()
        n_total = len(detail)

        if n_total == 0:
            st.info(t("compact.ist_soll_heads.detail.no_positions"))
        else:
            # ── Klassifizierung jeder Planstelle ──────────────────────────────
            # Band-Logik: Soll-EG_H (Spalte H) = untere Grenze,
            #             Soll-EG_I (Spalte I, Fallback H) = obere Grenze.
            # Ist-EG liegt im Band → "Passend im Band"
            # Ist-EG == Soll-EG (Toggle-Wert) → "Passend (exakt)"
            # Ist-EG < Soll-EG_H → Untergruppiert
            # Ist-EG > Soll-EG_I → Übergruppiert
            from config.settings import TARIFF_GROUPS as _TG
            _EG_RANK = {g: i for i, g in enumerate(_TG)}

            # Für die gewählte Soll-EG: Band aus erster Zeile auslesen
            _soll_h = detail["_Soll_EG_H"].iloc[0] if "_Soll_EG_H" in detail.columns else selected_soll
            _soll_i = detail["_Soll_EG_I"].iloc[0] if "_Soll_EG_I" in detail.columns else selected_soll
            _rank_h = _EG_RANK.get(_soll_h, 999)
            _rank_i = _EG_RANK.get(_soll_i, 999)
            _soll_rank = _EG_RANK.get(selected_soll, 999)  # aktiver Toggle-Wert
            _has_band = _rank_h != _rank_i                  # Band hat Breite (H ≠ I)

            def _classify(ist_eg: str) -> str:
                if ist_eg == IST_UNBESETZT:
                    return "Unbesetzt"
                if ist_eg == IST_NOT_FOUND:
                    return "Nicht gefunden"
                ist_rank = _EG_RANK.get(ist_eg, 999)
                if ist_rank == _soll_rank:
                    return "Passend (exakt)"
                if _has_band and _rank_h <= ist_rank <= _rank_i:
                    return "Passend im Band"
                if ist_rank > _rank_i:
                    return "Übergruppiert"
                return "Untergruppiert"

            detail["_Klasse"] = detail["_Ist_EG"].map(_classify)

            n_unbesetzt       = int((detail["_Klasse"] == "Unbesetzt").sum())
            n_passend_exakt   = int((detail["_Klasse"] == "Passend (exakt)").sum())
            n_passend_band    = int((detail["_Klasse"] == "Passend im Band").sum())
            n_ueber           = int((detail["_Klasse"] == "Übergruppiert").sum())
            n_unter           = int((detail["_Klasse"] == "Untergruppiert").sum())
            n_not_found       = int((detail["_Klasse"] == "Nicht gefunden").sum())
            n_besetzt         = n_total - n_unbesetzt - n_not_found
            n_passend_gesamt  = n_passend_exakt + n_passend_band  # exakt + im Band

            passungs_pct      = n_passend_exakt   / n_besetzt * 100 if n_besetzt > 0 else 0.0
            band_pct          = n_passend_gesamt  / n_besetzt * 100 if n_besetzt > 0 else 0.0
            ueber_pct         = n_ueber           / n_besetzt * 100 if n_besetzt > 0 else 0.0
            unbesetzt_pct     = n_unbesetzt        / n_total  * 100 if n_total   > 0 else 0.0

            # ── KPI-Kacheln ────────────────────────────────────────────────────
            # Kachel "Passend im Band" nur anzeigen, wenn das Band Breite hat
            detail_kpis = [
                {
                    "title":    "Passend (exakt)",
                    "value":    f"{passungs_pct:.1f}%".replace(".", ","),
                    "subtitle": f"{n_passend_exakt} Planstellen — Ist-EG = Soll-EG",
                    "icon":     "✅",
                    "status":   "good" if passungs_pct >= 70 else "warning",
                },
                {
                    "title":    f"Passend im Band ({_soll_h}–{_soll_i})" if _has_band else "Passend im Band",
                    "value":    f"{band_pct:.1f}%".replace(".", ","),
                    "subtitle": (
                        f"{n_passend_gesamt} Planstellen — exakt + im Band"
                        if _has_band else
                        f"{n_passend_gesamt} (kein Band in Spalte I)"
                    ),
                    "icon":     "🎯",
                    "status":   "good" if band_pct >= 70 else "warning",
                },
                {
                    "title":    "Übergruppierungsquote",
                    "value":    f"{ueber_pct:.1f}%".replace(".", ","),
                    "subtitle": f"{n_ueber} Planstellen — Ist-EG > {_soll_i}",
                    "icon":     "⬆️",
                    "status":   "warning" if ueber_pct > 20 else "default",
                },
                {
                    "title":    "Unbesetztquote",
                    "value":    f"{unbesetzt_pct:.1f}%".replace(".", ","),
                    "subtitle": f"{n_unbesetzt} von {n_total} Planstellen",
                    "icon":     "🔲",
                    "status":   "warning" if unbesetzt_pct > 10 else "good",
                },
            ]
            render_kpi_cards_styled(detail_kpis)

            st.markdown("")

            col_donut, col_bar = st.columns([1, 1], gap="large")

            # ── Chart 1: Klassifizierungs-Donut ───────────────────────────────
            with col_donut:
                _band_label = f"Passend im Band ({_soll_h}–{_soll_i})" if _has_band else "Passend im Band"
                st.caption(
                    f"**Verteilung** — Soll-EG {selected_soll}"
                    + (f"  |  Band: {_soll_h} – {_soll_i}" if _has_band else "")
                )
                _KL_COLOR = {
                    "Passend (exakt)":  "#10b981",   # grün
                    _band_label:        "#34d399",   # hellgrün
                    "Passend im Band":  "#34d399",
                    "Übergruppiert":    "#0088DE",   # blau
                    "Untergruppiert":   "#f59e0b",   # amber
                    "Unbesetzt":        "#cbd5e1",   # grau
                    "Nicht gefunden":   "#E94D3A",   # rot
                }
                klasse_counts = detail["_Klasse"].value_counts()
                kl_order = ["Passend (exakt)", "Passend im Band",
                            "Übergruppiert", "Untergruppiert",
                            "Unbesetzt", "Nicht gefunden"]
                kl_labels = [k for k in kl_order if k in klasse_counts.index]
                kl_values = [int(klasse_counts[k]) for k in kl_labels]
                kl_colors = [_KL_COLOR.get(k, "#94a3b8") for k in kl_labels]

                # Legende-Labels mit Band-Info anreichern
                kl_display = []
                for k in kl_labels:
                    if k == "Passend im Band" and _has_band:
                        kl_display.append(f"Im Band ({_soll_h}–{_soll_i})")
                    else:
                        kl_display.append(k)

                fig_donut = go.Figure(go.Pie(
                    labels=kl_display,
                    values=kl_values,
                    marker=dict(colors=kl_colors, line=dict(color="white", width=2)),
                    hole=0.52,
                    textinfo="label+percent",
                    textfont=dict(size=11),
                    hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
                ))
                fig_donut.update_layout(
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=10, r=10, t=10, b=10),
                    height=280,
                    showlegend=False,
                )
                st.plotly_chart(fig_donut, use_container_width=True)

            # ── Chart 2: Ist-EG-Balken für gewählte Soll-EG ───────────────────
            with col_bar:
                st.caption(t("compact.ist_soll_heads.detail.actual_grading_caption", selected_soll=selected_soll))
                ist_counts = (
                    detail.groupby("_Ist_EG").size()
                    .reset_index(name="n")
                )
                # Reihenfolge: EG-Spalten nach Rang, dann Sonderkategorien
                def _ist_sort_key(eg):
                    if eg in (IST_UNBESETZT, IST_NOT_FOUND):
                        return (1, eg)
                    return (0, _EG_RANK.get(eg, 999))
                ist_counts = ist_counts.sort_values(
                    "_Ist_EG", key=lambda s: s.map(_ist_sort_key)
                )

                # Balken-Farbe nach Band-Klassifizierung + Rahmen für Band-Bereich
                bar_colors = [
                    _KL_COLOR.get(_classify(eg), "#94a3b8")
                    for eg in ist_counts["_Ist_EG"]
                ]
                # Rahmen: Stellen innerhalb des Bandes [H..I] hervorheben
                bar_line_colors = []
                bar_line_widths = []
                for eg in ist_counts["_Ist_EG"]:
                    r = _EG_RANK.get(eg, 999)
                    if _has_band and _rank_h <= r <= _rank_i:
                        bar_line_colors.append("#1a1a2e")
                        bar_line_widths.append(2)
                    else:
                        bar_line_colors.append("white")
                        bar_line_widths.append(0.5)
                fig_bar = go.Figure(go.Bar(
                    x=ist_counts["_Ist_EG"],
                    y=ist_counts["n"],
                    marker=dict(
                        color=bar_colors,
                        line=dict(color=bar_line_colors, width=bar_line_widths),
                    ),
                    hovertemplate="Ist-EG: %{x}<br>Planstellen: %{y}<extra></extra>",
                ))
                fig_bar.update_layout(
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#64748b", size=11),
                    margin=dict(l=10, r=10, t=10, b=30),
                    height=280,
                    xaxis=dict(showgrid=False, title=None),
                    yaxis=dict(
                        showgrid=True,
                        gridcolor="rgba(226,232,240,0.8)",
                        zeroline=False,
                        title=None,
                        dtick=1,
                    ),
                )
                st.plotly_chart(fig_bar, use_container_width=True)

    st.markdown("---")
    st.markdown(t("compact.ist_soll_heads.special_cases.heading"))
    st.caption(t("compact.ist_soll_heads.special_cases.caption"))

    if len(no_soll_eg_row) > 0:
        st.subheader(t("compact.ist_soll_heads.special_cases.no_target.heading"))
        st.caption(t("compact.ist_soll_heads.special_cases.no_target.caption"))
        _nosoll_total = int(no_soll_eg_row.sum())
        _nosoll_kpis = [{
            "title": "Sonderfälle gesamt",
            "value": f"{_nosoll_total:,}".replace(",", "."),
            "subtitle": "Besetzt, aber ohne hinterlegte Soll-EG",
            "icon": "📎",
            "status": "warning" if _nosoll_total > 0 else "good",
        }]
        render_kpi_cards_styled(_nosoll_kpis)
        _nosoll_display = pd.Series(0, index=ist_eg_cols)
        for eg, cnt in no_soll_eg_row.items():
            if eg in _nosoll_display.index:
                _nosoll_display[eg] = cnt
        _nosoll_display["Gesamt"] = _nosoll_total
        _nosoll_df = _nosoll_display.rename("(Keine Soll-EG)").to_frame().T
        _nosoll_df.index.name = "Soll-EG"
        _nosoll_fmt = _nosoll_df.map(_fmt_int)
        st.dataframe(
            _nosoll_fmt,
            use_container_width=True,
            column_config={col: st.column_config.TextColumn(col, width="small")
                           for col in _nosoll_fmt.columns},
        )

    # ── Analyse Überhänge ─────────────────────────────────────────────────────
    if not print_mode:
        st.markdown("---")
        st.subheader(t("compact.ist_soll_heads.overhang.heading"))
        st.caption(t("compact.ist_soll_heads.overhang.caption"))

        # OE-Exklusion auf Rohdaten anwenden (gleiche Logik wie _build_soll_ist_pivot)
        from utils.settings_loader import get_setting as _get_setting
        _ex       = _get_setting("exclusions", {})
        _ex_units = _ex.get("org_units", [])
        ueb_base  = df.copy()
        if _ex_units and "Kürzel OrgEinheit" in ueb_base.columns:
            _s_ou     = ueb_base["Kürzel OrgEinheit"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            _explicit = [u for u in _ex_units if u != "99XX"]
            _mask_ex  = _s_ou.isin(_explicit)
            if "99XX" in _ex_units:
                _mask_ex = _mask_ex | (_s_ou.str.startswith("99") & ~_s_ou.isin(set(_explicit)))
            ueb_base = ueb_base[~_mask_ex]

        # Optional dieselbe personenbezogene Planstellen-Exklusion wie in der Matrix anwenden.
        if _ex.get("planstellen_follow_person", False):
            _person_pl_mask = pd.Series(False, index=ueb_base.index)
            if _ex.get("vorstand") and "MitarbGruppenbez." in ueb_base.columns:
                _person_pl_mask |= (
                    ueb_base["MitarbGruppenbez."].astype(str).str.strip() == "Vorstand"
                )
            if _ex.get("ruhend_bv") and "Status kundenindividuell" in ueb_base.columns:
                _person_pl_mask |= (
                    ueb_base["Status kundenindividuell"].astype(str).str.strip()
                    == "Ruhendes Beschäftigungsverhältnis"
                )
            if _person_pl_mask.any():
                ueb_base = ueb_base[~_person_pl_mask]

        # Spalte G: Sollarbeitszeit robust als Zahl lesen
        # Schwellenwert <= 0.1 erfasst: 0, 0.01 (Systemplatzhalter), 0.1
        # Leere Werte werden NICHT als 0 interpretiert (errors="coerce" → NaN)
        if "Sollarbeitszeit" in ueb_base.columns:
            _soll_az = pd.to_numeric(
                ueb_base["Sollarbeitszeit"].astype(str)
                    .str.strip()
                    .str.replace(",", ".", regex=False),
                errors="coerce",
            )
            _mask_low_az  = _soll_az.notna() & (_soll_az <= 0.1)

            # "Besetzt" robust erkennen: primär über Is_Vacant, fallback auf Personalnummer.
            if "Is_Vacant" in ueb_base.columns:
                _mask_has_pnr = ~(ueb_base["Is_Vacant"].fillna(True).astype(bool))
            elif "Personalnummer" in ueb_base.columns:
                _pnr_raw = _normalize_personalnummer_keys(ueb_base["Personalnummer"])
                _mask_has_pnr = _pnr_raw.ne("")
            else:
                _mask_has_pnr = pd.Series(False, index=ueb_base.index)

            n_low_az    = int(_mask_low_az.sum())
            n_ueberhang = int((_mask_low_az & _mask_has_pnr).sum())

            # Überhang-Datensatz für Detailanalysen A / B / C
            ueb_df = ueb_base[_mask_low_az & _mask_has_pnr].copy()

            # ── Option B: Mehrfachplanstellen-Analyse ─────────────────────────
            # "Echte Stelle" = Planstelle derselben Person in inkludierten OEs mit Soll > 0.1
            _az_all = pd.to_numeric(
                ueb_base["Sollarbeitszeit"].astype(str).str.strip().str.replace(",", ".", regex=False),
                errors="coerce",
            )
            _ueb_persnr = set(_normalize_personalnummer_keys(ueb_df["Personalnummer"]))
            _ueb_persnr.discard("")
            _all_for_ueb = ueb_base[
                _normalize_personalnummer_keys(ueb_base["Personalnummer"]).isin(_ueb_persnr)
            ]
            _all_for_ueb_pnr = _normalize_personalnummer_keys(_all_for_ueb["Personalnummer"])
            _has_real_set = set(
                _all_for_ueb_pnr.loc[_az_all.reindex(_all_for_ueb.index).gt(0.1)]
            )
            _has_real_set.discard("")
            _ueb_df_pnr = _normalize_personalnummer_keys(ueb_df["Personalnummer"])
            n_nur_ueberhang = int((~_ueb_df_pnr.isin(_has_real_set)).sum())
            n_mit_echter = n_ueberhang - n_nur_ueberhang

            st.info(
                t(
                    "compact.ist_soll_heads.overhang.quick_interpretation",
                    total=n_ueberhang,
                    without_regular=n_nur_ueberhang,
                    alongside_regular=n_mit_echter,
                )
            )

            # ── KPI-Kacheln (mit Option B) ────────────────────────────────────
            ueb_kpis = [
                {
                    "title":    "Low-AZ-Planstellen gesamt",
                    "value":    f"{n_low_az:,}".replace(",", "."),
                    "subtitle": "Alle Planstellen mit Soll-Arbeitszeit 0 oder 0,1",
                    "icon":     "⏱️",
                    "status":   "warning" if n_low_az > 0 else "good",
                },
                {
                    "title":    "Besetzte Zusatzstellen",
                    "value":    f"{n_ueberhang:,}".replace(",", "."),
                    "subtitle": "Low-AZ-Planstellen mit tatsächlich besetzter Person",
                    "icon":     "🔴",
                    "status":   "warning" if n_ueberhang > 0 else "good",
                },
                {
                    "title":    "Ohne reguläre Stelle",
                    "value":    f"{n_nur_ueberhang:,}".replace(",", "."),
                    "subtitle": "Person erscheint nur über diese technische Zusatzstelle",
                    "icon":     "⚠️",
                    "status":   "warning" if n_nur_ueberhang > 0 else "good",
                },
                {
                    "title":    "Zusätzlich zur regulären Stelle",
                    "value":    f"{n_mit_echter:,}".replace(",", "."),
                    "subtitle": "Technische Zusatzstelle neben einer regulären aktiven Stelle",
                    "icon":     "🔗",
                    "status":   "default",
                },
            ]
            render_kpi_cards_styled(ueb_kpis)

            if n_ueberhang > 0:
                st.markdown("")
                col_oe, col_eg = st.columns([1, 1], gap="large")

                # ── Option A: OE-Verteilung ───────────────────────────────────
                with col_oe:
                    st.caption(t("compact.ist_soll_heads.overhang.where_caption"))
                    _oe_col = "Organisationseinheit" if "Organisationseinheit" in ueb_df.columns else "Kürzel OrgEinheit"
                    oe_counts = (
                        ueb_df.groupby([_oe_col, "Kürzel OrgEinheit"]).size()
                        .reset_index(name="n")
                        .sort_values("n", ascending=False)
                        .head(10)
                        .sort_values("n", ascending=True)   # Plotly zeigt unten→oben
                    )
                    # Label: "Kürzel — Name"
                    oe_counts["_label"] = (
                        oe_counts["Kürzel OrgEinheit"].astype(str)
                        + " — "
                        + oe_counts[_oe_col].astype(str)
                    )
                    fig_oe = go.Figure(go.Bar(
                        y=oe_counts["_label"],
                        x=oe_counts["n"],
                        orientation="h",
                        marker=dict(color="#0088DE", line=dict(color="white", width=0.5)),
                        hovertemplate=t("compact.ist_soll_heads.overhang.where_hover"),
                        text=oe_counts["n"],
                        textposition="outside",
                    ))
                    fig_oe.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#64748b", size=11),
                        margin=dict(l=10, r=40, t=10, b=10),
                        height=max(250, len(oe_counts) * 34),
                        xaxis=dict(showgrid=True, gridcolor="rgba(226,232,240,0.8)", zeroline=False),
                        yaxis=dict(showgrid=False),
                    )
                    st.plotly_chart(fig_oe, use_container_width=True)

                # ── Option C: Ist-EG Verteilung ───────────────────────────────
                with col_eg:
                    st.caption(t("compact.ist_soll_heads.overhang.grading_caption"))
                    if "TrfGr" in ueb_df.columns:
                        from config.settings import TARIFF_GROUPS as _TG_UEB
                        _EG_RANK_UEB = {g: i for i, g in enumerate(_TG_UEB)}
                        eg_counts = (
                            ueb_df["TrfGr"].fillna("Unbekannt")
                            .value_counts()
                            .reset_index(name="n")
                            .rename(columns={"TrfGr": "eg"})
                        )
                        eg_counts = eg_counts.sort_values(
                            "eg",
                            key=lambda s: s.map(lambda v: (_EG_RANK_UEB.get(v, 998), v)),
                        )
                        fig_eg = go.Figure(go.Bar(
                            x=eg_counts["eg"],
                            y=eg_counts["n"],
                            marker=dict(color="#0088DE", line=dict(color="white", width=0.5)),
                            hovertemplate=t("compact.ist_soll_heads.overhang.grading_hover"),
                            text=eg_counts["n"],
                            textposition="outside",
                        ))
                        fig_eg.update_layout(
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(color="#64748b", size=11),
                            margin=dict(l=10, r=10, t=10, b=30),
                            height=300,
                            xaxis=dict(showgrid=False, title=None),
                            yaxis=dict(
                                showgrid=True,
                                gridcolor="rgba(226,232,240,0.8)",
                                zeroline=False,
                                title=None,
                                dtick=5,
                            ),
                        )
                        st.plotly_chart(fig_eg, use_container_width=True)
                    else:
                        st.info(t("compact.ist_soll_heads.overhang.info.missing_trfgr"))

            # ── Detailtabelle Überhänge ───────────────────────────────────
            st.markdown("---")
            st.subheader(t("compact.ist_soll_heads.overhang.detail.heading"))

            col_exp_a, col_exp_b = st.columns(2, gap="medium")
            with col_exp_a:
                st.markdown(t("compact.ist_soll_heads.overhang.detail.without_regular"))
            with col_exp_b:
                st.markdown(t("compact.ist_soll_heads.overhang.detail.alongside_regular"))
            st.markdown("")

            # Typ-Spalte: Nur Überhang vs. Überhang + echte Stelle
            _ueb_detail = ueb_df.copy()
            _ueb_detail_pnr = _normalize_personalnummer_keys(_ueb_detail["Personalnummer"])
            _ueb_detail["_Typ"] = _ueb_detail_pnr.map(
                lambda p: (
                    t("compact.ist_soll_heads.overhang.filter.alongside_regular")
                    if p in _has_real_set
                    else t("compact.ist_soll_heads.overhang.filter.without_regular")
                )
            )

            _filter_opts = [
                t("compact.ist_soll_heads.overhang.filter.all"),
                t("compact.ist_soll_heads.overhang.filter.without_regular"),
                t("compact.ist_soll_heads.overhang.filter.alongside_regular"),
            ]
            _filter_sel = st.radio(
                t("compact.ist_soll_heads.overhang.filter.label"),
                options=_filter_opts,
                horizontal=True,
                key="ueb_detail_filter",
            )
            if _filter_sel != t("compact.ist_soll_heads.overhang.filter.all"):
                _ueb_detail = _ueb_detail[_ueb_detail["_Typ"] == _filter_sel]

            # Anzuzeigende Spalten (nur vorhandene)
            _display_cols_pref = [
                "Personalnummer",
                "Kürzel OrgEinheit",
                "Organisationseinheit",
                "TrfGr",
                "Bewertung Tarifgruppe",
                "Text Gehaltsband",
                "Sollarbeitszeit",
                "_Typ",
            ]
            _display_cols = [c for c in _display_cols_pref if c in _ueb_detail.columns]
            _rename_map = {
                "TrfGr": t("compact.ist_soll_heads.overhang.table.actual_grade"),
                "Bewertung Tarifgruppe": t("compact.ist_soll_heads.overhang.table.target_grade_base"),
                "Text Gehaltsband": t("compact.ist_soll_heads.overhang.table.target_grade_max"),
                "Sollarbeitszeit": t("compact.ist_soll_heads.overhang.table.target_capacity"),
                "_Typ": t("compact.ist_soll_heads.overhang.table.case_type"),
            }
            _show_df = (
                _ueb_detail[_display_cols]
                .rename(columns=_rename_map)
                .reset_index(drop=True)
            )
            st.caption(t("compact.ist_soll_heads.overhang.table.entries", count=len(_show_df)))
            st.dataframe(_show_df, use_container_width=True, hide_index=True)
        else:
            st.info(t("compact.ist_soll_heads.overhang.info.missing_target_capacity"))


# =============================================================================
# HAUPTFUNKTION
# =============================================================================

def main():
    """Hauptfunktion für die Kompakt-Seite."""

    _render_page_intro()

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
        snapshot_df, history_df, org_df, summary = load_and_prepare_data(show_status_messages=False)
        prepared_df = prepare_compact_data(snapshot_df)
        set_metric_page_hint(_build_compact_sidebar_hint())

        # Filter rendern
        # Hinweis: Job Family Filter ist jetzt global in Sidebar (render_global_filters)
        render_global_filters(prepared_df, history_df)
        
        with st.sidebar:
            st.divider()
            compact_ist_export = build_compact_ist_demographics_export_bytes(
                prepared_df=prepared_df,
                stichtag=get_setting("stichtag", summary.get("stichtag", "unbekannt") if isinstance(summary, dict) else "unbekannt"),
            )
            download_button_compat(
                label="IST-Demografie als Excel exportieren",
                data=compact_ist_export,
                file_name=f"Kompakt_IST_Demografie_{datetime.now():%Y%m%d}.xlsx",
                mime=_EXCEL_MIME,
                key="compact_ist_demographics_export",
                width="stretch",
            )

        # Druck-Modus Toggle
        with st.sidebar:
            st.divider()
            print_mode = st.toggle(
                t("compact.print_mode.label"),
                value=False, 
                help=t("compact.print_mode.help")
            )

        # Filter anwenden (inkl. Job Families über globale Sidebar)
        filtered_df = apply_filters(prepared_df)

        # Filter-Summary (inkl. Job Families)
        filter_summary = get_filter_summary()

        # Prüfe Daten
        if len(filtered_df) == 0:
            st.warning(t("compact.warning.no_filtered_data"))
            return

        if print_mode:
            # Print-Styles injizieren
            inject_print_styles()

            # Header und Footer rendern
            render_print_header_footer(filter_summary)

            # Scroll-Navigation einfügen (falls verfügbar)
            if SCROLL_NAV_AVAILABLE:
                anchor_ids = ["deckblatt", "ist-koepfe", "ist-mak", "ist-eur", "ist-vs-soll-koepfe", "ist-vs-soll-mak", "ist-vs-soll-eur"]
                anchor_labels = ["🔰 Executive Summary", "👥 IST-Köpfe", "📊 IST-MAK", "💶 IST-EUR", "📋 IST vs SOLL Köpfe", "📉 IST vs SOLL MAK", "💶 IST vs SOLL EUR"]

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

            section_title("Deckblatt & Executive Summary", "🔰", anchor="deckblatt")
            render_cover_page_and_toc(filter_summary, filtered_df)

            # Druckansicht: Alle Tabs untereinander mit professionellen Seitenumbrüchen
            section_title("IST-Köpfe Analyse", "👥", anchor="ist-koepfe")
            render_ist_koepfe_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST-MAK Analyse", "📊", anchor="ist-mak")
            render_ist_mak_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST-EUR Analyse", "💶", anchor="ist-eur")
            render_ist_eur_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST vs SOLL Köpfe", "📋", anchor="ist-vs-soll-koepfe")
            render_ist_soll_koepfe_tab(prepared_df, print_mode=True)
            page_break()

            section_title("IST vs SOLL MAK Vergleich", "📉", anchor="ist-vs-soll-mak")
            render_ist_vs_soll_mak_tab(filtered_df, print_mode=True)
            page_break()

            section_title("IST vs SOLL EUR Vergleich", "💶", anchor="ist-vs-soll-eur")
            render_ist_vs_soll_eur_tab(filtered_df, print_mode=True)

            st.markdown('</div>', unsafe_allow_html=True)

            # Hinweis für Benutzer
            st.info(t("compact.print_hint"))

            
        else:
            metric_view = _compact_metric_view_label(normalize_global_metric_view(get_global_metric_view()))
            ist_tab, ist_soll_tab = st.tabs(_get_main_tab_labels())

            with ist_tab:
                if metric_view == "Köpfe":
                    render_ist_koepfe_tab(filtered_df)
                elif metric_view == "MAK":
                    render_ist_mak_tab(filtered_df)
                else:
                    render_ist_eur_tab(filtered_df)

            with ist_soll_tab:
                if metric_view == "Köpfe":
                    # prepared_df (not filtered_df) wird verwendet, damit vakante
                    # Planstellen enthalten sind (Geschlecht-/Arbeitszeit-Filter
                    # wuerden leere Person-Zeilen herausfiltern).
                    render_ist_soll_koepfe_tab(prepared_df)
                elif metric_view == "MAK":
                    render_ist_vs_soll_mak_tab(filtered_df)
                else:
                    render_ist_vs_soll_eur_tab(filtered_df)

            return

            main_tab_ist, main_tab_soll = st.tabs([
                "📈 IST-Analyse",
                "🎯 IST vs SOLL",
            ])

            # ── Hauptbereich: IST-Analyse ─────────────────────────────────────
            with main_tab_ist:
                sub1, sub2, sub3 = st.tabs([
                    "👥 Köpfe",
                    "📊 MAK",
                    "💰 EUR",
                ])
                with sub1:
                    render_ist_koepfe_tab(filtered_df)
                with sub2:
                    render_ist_mak_tab(filtered_df)
                with sub3:
                    render_ist_eur_tab(filtered_df)

            # ── Hauptbereich: IST vs SOLL ─────────────────────────────────────
            with main_tab_soll:
                sub4, sub5, sub6 = st.tabs([
                    "🔢 Köpfe",
                    "🎯 MAK",
                    "💶 EUR",
                ])
                with sub4:
                    # prepared_df (not filtered_df) wird verwendet, damit vakante
                    # Planstellen enthalten sind (Geschlecht-/Arbeitszeit-Filter
                    # wuerden leere Person-Zeilen herausfiltern).
                    render_ist_soll_koepfe_tab(prepared_df)
                with sub5:
                    render_ist_vs_soll_mak_tab(filtered_df)
                with sub6:
                    render_ist_vs_soll_eur_tab(filtered_df)

    except FileNotFoundError as e:
        st.error(f"Datenfehler: {str(e)}")
    except Exception as e:
        st.error(f"Fehler: {str(e)}")
        st.exception(e)


if __name__ == "__main__":
    main()
