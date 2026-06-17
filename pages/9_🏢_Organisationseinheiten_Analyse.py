"""
Streamlit page: Organisationseinheiten-Analyse (IST).
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

BASE_PATH = Path(__file__).resolve().parents[1]
SRC_PATH = BASE_PATH / "src"
if SRC_PATH.exists():
    sys.path.append(str(SRC_PATH))
else:
    sys.path.append(str(BASE_PATH))

from components.sidebar import (
    apply_filters,
    get_filter_summary,
    get_global_metric_view,
    normalize_global_metric_view,
    render_global_filters,
    set_metric_page_hint,
)
from components.ui_compat import dataframe_compat, download_button_compat
from components.ui_shell import (
    render_active_filter_banner,
    render_context_box,
    render_page_header,
    render_section_intro,
)
from dataloader.kpi_engine import (
    compute_atz_kpis,
    compute_fte_roh,
    compute_teilzeit_kpis,
    get_unique_employees,
)
from dataloader.loader import load_and_prepare_data
from utils.compact_page_loader import load_compact_page_module
from utils.plot_helpers import AGE_COHORT_ORDER, apply_legend_bottom, get_age_cohort_color_map


ORG_COL = "Organisationseinheit"
ORG_NOT_ASSIGNED = "Nicht zugeordnet"
_ORG_UNASSIGNED_SENTINELS = {"Nicht zugeordnet", "UNMAPPED", "Unmapped", "Unclustered"}

_TOP_N_OPTIONS = ["8", "10", "15", "20", "Alle"]
_TOP_N_SESSION_KEY = "orgunit_analysis_top_n"
_TOP_N_DEFAULT = "8"

DETAIL_BLOCKS = [
    ("Geschlecht", "Geschlecht"),
    ("Alterskohorten", "Alterskohorte"),
    ("Beschäftigungsstatus", "Beschäftigungsstatus"),
]


# ---------------------------------------------------------------------------
# Data normalization
# ---------------------------------------------------------------------------

def _normalize_org_column(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if ORG_COL not in df.columns:
        df[ORG_COL] = ORG_NOT_ASSIGNED
        return df
    df[ORG_COL] = df[ORG_COL].fillna(ORG_NOT_ASSIGNED).astype(str)
    df.loc[df[ORG_COL].str.strip() == "", ORG_COL] = ORG_NOT_ASSIGNED
    df.loc[df[ORG_COL].isin(_ORG_UNASSIGNED_SENTINELS), ORG_COL] = ORG_NOT_ASSIGNED
    return df


# ---------------------------------------------------------------------------
# Central display list — headcount-based, metric-independent
# ---------------------------------------------------------------------------

def _get_visible_org_units_for_display(
    mapped_df: pd.DataFrame,
    top_n: str,
) -> list[str]:
    """Return ordered list of OEs to display, always ranked by headcount."""
    if mapped_df.empty or ORG_COL not in mapped_df.columns:
        return []

    work_df = mapped_df.copy()
    if "Is_Vacant" in work_df.columns:
        work_df = work_df[~work_df["Is_Vacant"]]

    # headcount: unique PersNr per OE, fallback Personalnummer, fallback row count
    id_col: str | None = None
    for candidate in ("PersNr", "Personalnummer"):
        if candidate in work_df.columns:
            id_col = candidate
            break

    if id_col:
        headcount = (
            work_df[work_df[ORG_COL] != ORG_NOT_ASSIGNED]
            .groupby(ORG_COL, observed=True)[id_col]
            .nunique()
        )
    else:
        headcount = (
            work_df[work_df[ORG_COL] != ORG_NOT_ASSIGNED]
            .groupby(ORG_COL, observed=True)
            .size()
        )

    # sort descending by headcount, then alphabetically for ties
    ranked = (
        headcount.reset_index(name="_n")
        .sort_values(["_n", ORG_COL], ascending=[False, True])
    )

    all_orgs: list[str] = ranked[ORG_COL].astype(str).tolist()

    if top_n == "Alle":
        return all_orgs
    return all_orgs[: int(top_n)]


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _get_metric_config(df: pd.DataFrame, metric_view: str) -> dict[str, str] | None:
    metric_view = normalize_global_metric_view(metric_view) or "MAK"

    if metric_view == "Köpfe":
        return {"value_col": "Headcount", "value_type": "koepfe"}
    if metric_view == "MAK":
        for candidate in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK", "FTE_assigned"):
            if candidate in df.columns:
                return {"value_col": candidate, "value_type": "mak"}
        return None
    if metric_view == "EUR":
        for candidate in ("EUR_Reporting", "Total_Cost_Year"):
            if candidate in df.columns:
                return {"value_col": candidate, "value_type": "eur"}
        return None

    return None


def _format_metric_value(value: float, metric_view: str, compact) -> str:
    metric_view = normalize_global_metric_view(metric_view) or "MAK"
    if metric_view == "Köpfe":
        return compact.format_number(value, 0)
    if metric_view == "MAK":
        return compact.format_number(value, 1)
    return compact.format_currency(value)


def _get_metric_total(df: pd.DataFrame, metric_view: str, compact) -> float:
    metric_view = normalize_global_metric_view(metric_view) or "MAK"
    if metric_view == "Köpfe":
        return float(compact.get_ist_koepfe(df))
    if metric_view == "MAK":
        return float(compact.get_ist_mak(df))
    return float(compact.get_ist_eur(df))


def _count_visible_org_units(df: pd.DataFrame) -> int:
    if ORG_COL not in df.columns or df.empty:
        return 0
    return int(df[ORG_COL].dropna().astype(str).nunique())


# ---------------------------------------------------------------------------
# KPI helpers
# ---------------------------------------------------------------------------

def _get_largest_org_label(
    mapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
) -> tuple[str, str]:
    if mapped_df.empty:
        return "Keine zugeordnete OE", "Aktuelle Filter enthalten keine zugeordneten Organisationseinheiten."

    ranking_df = compact.create_breakdown_table(mapped_df, ORG_COL, metric_config["value_col"])
    ranking_df = ranking_df[ranking_df[ORG_COL] != ORG_NOT_ASSIGNED]
    if ranking_df.empty:
        return "Keine zugeordnete OE", "Aktuelle Filter enthalten keine zugeordneten Organisationseinheiten."

    top_row = ranking_df.iloc[0]
    return str(top_row[ORG_COL]), _format_metric_value(float(top_row["IST"]), metric_view, compact)


def _build_kpis(
    mapped_df: pd.DataFrame,
    filtered_df: pd.DataFrame,
    unmapped_df: pd.DataFrame,
    metric_view: str,
    compact,
) -> list[dict]:
    metric_config = _get_metric_config(mapped_df if not mapped_df.empty else filtered_df, metric_view)
    if metric_config is None:
        return []

    emp_df = mapped_df[~mapped_df["Is_Vacant"]] if "Is_Vacant" in mapped_df.columns else mapped_df
    visible_orgs = _count_visible_org_units(mapped_df)
    total_metric = _get_metric_total(filtered_df, metric_view, compact)
    unmapped_metric = _get_metric_total(unmapped_df, metric_view, compact)

    if metric_view == "Köpfe":
        total_koepfe = compact.get_ist_koepfe(emp_df)
        unique_emp = get_unique_employees(emp_df)
        female_count = int((unique_emp["Geschlecht"] == "w").sum()) if "Geschlecht" in unique_emp.columns else 0
        female_rate = female_count / total_koepfe if total_koepfe > 0 else 0
        atz = compute_atz_kpis(emp_df)
        largest_name, largest_value = _get_largest_org_label(mapped_df, metric_view, metric_config, compact)
        return [
            {
                "title": "Zugeordnete Köpfe",
                "value": compact.format_number(total_koepfe, 0),
                "subtitle": "Mitarbeitende mit Organisationseinheit",
                "icon": "👥",
                "status": "good",
            },
            {
                "title": "Frauenanteil",
                "value": compact.format_percent(female_rate),
                "subtitle": f"{female_count} Frauen",
                "icon": "👤",
                "status": "default",
            },
            {
                "title": "ATZ-Quote",
                "value": f"{atz['quote_headcount_pct']:.1f}%".replace(".", ","),
                "subtitle": f"{atz['gesamt']} Mitarbeitende",
                "icon": "⏰",
                "status": "default",
            },
            {
                "title": "Größte Organisationseinheit",
                "value": largest_name,
                "subtitle": f"{largest_value} · {visible_orgs} sichtbar",
                "icon": "🏢",
                "status": "default" if unmapped_metric <= 0 else "warning",
            },
        ]

    if metric_view == "MAK":
        total_mak = compact.get_ist_mak(emp_df)
        total_fte_roh = compute_fte_roh(emp_df)
        total_koepfe = compact.get_ist_koepfe(emp_df)
        teilzeit = compute_teilzeit_kpis(emp_df)
        avg_fte = total_mak / total_koepfe if total_koepfe > 0 else 0
        largest_name, largest_value = _get_largest_org_label(mapped_df, metric_view, metric_config, compact)
        return [
            {
                "title": "Zugeordnete MAK",
                "value": compact.format_number(total_mak, 1),
                "subtitle": f"Roh-FTE {compact.format_number(total_fte_roh, 1)} bei {total_koepfe} Köpfen",
                "icon": "📈",
                "status": "good",
            },
            {
                "title": "Ø FTE",
                "value": compact.format_number(avg_fte, 2),
                "subtitle": "Durchschnittliche MAK pro Mitarbeitendem",
                "icon": "📊",
                "status": "default",
            },
            {
                "title": "Teilzeitquote",
                "value": f"{teilzeit['quote_pct']:.1f}%".replace(".", ","),
                "subtitle": f"{teilzeit['count']} Teilzeit-Mitarbeitende",
                "icon": "⏰",
                "status": "default",
            },
            {
                "title": "Größte Organisationseinheit",
                "value": largest_name,
                "subtitle": f"{largest_value} · {visible_orgs} sichtbar",
                "icon": "🏢",
                "status": "default" if unmapped_metric <= 0 else "warning",
            },
        ]

    total_cost = compact.get_ist_eur(emp_df)
    total_koepfe = compact.get_ist_koepfe(emp_df)
    total_mak = compact.get_ist_mak(emp_df)
    avg_cost = total_cost / total_koepfe if total_koepfe > 0 else 0
    cost_per_mak = total_cost / total_mak if total_mak > 0 else 0
    largest_name, largest_value = _get_largest_org_label(mapped_df, metric_view, metric_config, compact)
    mapped_share = (total_metric - unmapped_metric) / total_metric if total_metric > 0 else 0
    return [
        {
            "title": "Zugeordnete Kosten",
            "value": compact.format_currency(total_cost),
            "subtitle": "Sichtbare Jahreskosten in zugeordneten Organisationseinheiten",
            "icon": "💰",
            "status": "good",
        },
        {
            "title": "Kosten pro Kopf",
            "value": compact.format_currency(avg_cost),
            "subtitle": "Durchschnitt der zugeordneten Köpfe",
            "icon": "👤",
            "status": "default",
        },
        {
            "title": "Kosten pro MAK",
            "value": compact.format_currency(cost_per_mak),
            "subtitle": f"Zugeordneter Anteil: {compact.format_percent(mapped_share)}",
            "icon": "📊",
            "status": "default",
        },
        {
            "title": "Größte Organisationseinheit",
            "value": largest_name,
            "subtitle": f"{largest_value} · {visible_orgs} sichtbar",
            "icon": "🏢",
            "status": "default" if unmapped_metric <= 0 else "warning",
        },
    ]


# ---------------------------------------------------------------------------
# Rangliste (uses display_orgs — headcount order, metric values)
# ---------------------------------------------------------------------------

def _render_org_rangliste(
    mapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
    display_orgs: list[str],
) -> None:
    work_df = mapped_df[mapped_df[ORG_COL].isin(display_orgs)].copy()
    if "Is_Vacant" in work_df.columns:
        work_df = work_df[~work_df["Is_Vacant"]]

    if work_df.empty:
        st.info("Keine auswertbaren Daten im aktuellen Filterkontext.")
        return

    if metric_view == "Köpfe":
        id_col = next((c for c in ("PersNr", "Personalnummer") if c in work_df.columns), None)
        if id_col:
            agg = work_df.groupby(ORG_COL, observed=True)[id_col].nunique()
        else:
            agg = work_df.groupby(ORG_COL, observed=True).size()
    else:
        value_col = metric_config["value_col"]
        if value_col not in work_df.columns:
            st.warning(f"Spalte `{value_col}` nicht verfügbar.")
            return
        agg = work_df.groupby(ORG_COL, observed=True)[value_col].sum()

    # reindex to display_orgs — preserves headcount order, fills gaps with 0
    agg = agg.reindex(display_orgs, fill_value=0)
    total = agg.sum()

    chart_df = agg.reset_index()
    chart_df.columns = [ORG_COL, "Wert"]
    chart_df["Wert_Anzeige"] = chart_df["Wert"].apply(
        lambda v: _format_metric_value(float(v), metric_view, compact)
    )
    chart_df["Anteil"] = chart_df["Wert"].apply(
        lambda v: compact.format_percent(float(v) / total) if total > 0 else "0,0%"
    )

    # reversed so largest OE (first in display_orgs) appears at top of horizontal bars
    chart_order = list(reversed(display_orgs))
    chart_height = max(420, min(1200, 28 * len(display_orgs) + 160))

    fig = px.bar(
        chart_df,
        x="Wert",
        y=ORG_COL,
        orientation="h",
        custom_data=["Wert_Anzeige", "Anteil"],
        category_orders={ORG_COL: chart_order},
    )
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>%{customdata[0]} · %{customdata[1]}<extra></extra>"
    )
    fig.update_layout(
        height=chart_height,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="",
        yaxis_title="",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )

    display_table = pd.DataFrame({
        ORG_COL: display_orgs,
        "IST": [_format_metric_value(float(agg[o]), metric_view, compact) for o in display_orgs],
        "Anteil": [
            compact.format_percent(float(agg[o]) / total) if total > 0 else "0,0%"
            for o in display_orgs
        ],
    })

    col_chart, col_table = st.columns([3, 2])
    with col_chart:
        st.plotly_chart(fig, use_container_width=True)
    with col_table:
        dataframe_compat(display_table, width="stretch", hide_index=True)
        excel_df = pd.DataFrame({
            ORG_COL: display_orgs,
            "IST": [float(agg[o]) for o in display_orgs],
        })
        excel_data = compact.export_to_excel(
            excel_df,
            key_prefix="org_rangliste",
            dimension_name="Organisationseinheiten",
            value_type=metric_config["value_type"],
            table_title="Rangliste Organisationseinheiten",
        )
        download_button_compat(
            label="Excel Download",
            data=excel_data,
            file_name="org_rangliste.xlsx",
            mime=compact._EXCEL_MIME,
            key="download_org_rangliste",
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Detail-block aggregation (uses pre-computed display list)
# ---------------------------------------------------------------------------

def _aggregate_org_split(
    mapped_df: pd.DataFrame,
    split_col: str,
    metric_view: str,
    metric_config: dict[str, str],
    display_orgs: list[str],
) -> pd.DataFrame:
    if mapped_df.empty or split_col not in mapped_df.columns or ORG_COL not in mapped_df.columns:
        return pd.DataFrame()
    if not display_orgs:
        return pd.DataFrame()

    detail_df = mapped_df[mapped_df[ORG_COL].isin(display_orgs)].copy()
    detail_df[split_col] = detail_df[split_col].fillna("(unbekannt)").astype(str)
    if "Is_Vacant" in detail_df.columns:
        detail_df = detail_df[~detail_df["Is_Vacant"]]

    if detail_df.empty:
        return pd.DataFrame()

    if metric_view == "Köpfe":
        id_col = "PersNr" if "PersNr" in detail_df.columns else None
        if id_col:
            agg_df = (
                detail_df.groupby([ORG_COL, split_col], observed=True)[id_col]
                .nunique()
                .reset_index(name="Wert")
            )
        else:
            agg_df = detail_df.groupby([ORG_COL, split_col], observed=True).size().reset_index(name="Wert")
    else:
        value_col = metric_config["value_col"]
        if value_col not in detail_df.columns:
            return pd.DataFrame()
        agg_df = (
            detail_df.groupby([ORG_COL, split_col], observed=True)[value_col]
            .sum()
            .reset_index(name="Wert")
        )

    # category order = display_orgs order (headcount-descending), consistent across all charts
    agg_df[ORG_COL] = pd.Categorical(
        agg_df[ORG_COL],
        categories=display_orgs,
        ordered=True,
    )
    agg_df = agg_df.sort_values([ORG_COL, split_col])
    return agg_df


def _build_split_pivot(agg_df: pd.DataFrame, split_col: str) -> pd.DataFrame:
    if agg_df.empty:
        return pd.DataFrame()

    pivot_df = (
        agg_df.pivot_table(
            index=ORG_COL,
            columns=split_col,
            values="Wert",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    value_columns = [col for col in pivot_df.columns if col != ORG_COL]
    pivot_df["Gesamt"] = pivot_df[value_columns].sum(axis=1)
    # preserve display_orgs order via the Categorical index
    pivot_df[ORG_COL] = pd.Categorical(
        pivot_df[ORG_COL],
        categories=agg_df[ORG_COL].cat.categories.tolist(),
        ordered=True,
    )
    pivot_df = pivot_df.sort_values(ORG_COL)
    pivot_df[ORG_COL] = pivot_df[ORG_COL].astype(str)
    return pivot_df


def _format_split_display(pivot_df: pd.DataFrame, metric_view: str, compact) -> pd.DataFrame:
    display_df = pivot_df.copy()
    for col in display_df.columns:
        if col == ORG_COL:
            continue
        display_df[col] = display_df[col].apply(
            lambda value: _format_metric_value(float(value), metric_view, compact)
        )
    return display_df


def _render_org_split_block(
    mapped_df: pd.DataFrame,
    title: str,
    split_col: str,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
    key_prefix: str,
    display_orgs: list[str],
) -> None:
    agg_df = _aggregate_org_split(mapped_df, split_col, metric_view, metric_config, display_orgs)
    if agg_df.empty:
        st.info("Keine auswertbaren Daten im aktuellen Filterkontext.")
        return

    pivot_df = _build_split_pivot(agg_df, split_col)
    display_df = _format_split_display(pivot_df, metric_view, compact)

    chart_df = agg_df.copy()
    chart_df[ORG_COL] = chart_df[ORG_COL].astype(str)
    chart_df["Wert_Anzeige"] = chart_df["Wert"].apply(
        lambda value: _format_metric_value(float(value), metric_view, compact)
    )
    # chart order: display_orgs reversed so top OE appears at top of horizontal bars
    chart_order = list(reversed(display_orgs))

    chart_height = max(420, min(1200, 28 * len(display_orgs) + 160))

    _cdm = None
    _cat_ord: dict = {ORG_COL: chart_order}
    if split_col == "Alterskohorte":
        _cohort_vals = chart_df[split_col].unique().tolist()
        _cdm = get_age_cohort_color_map(_cohort_vals)
        _known = [c for c in AGE_COHORT_ORDER if c in _cohort_vals]
        _unknown = sorted(c for c in _cohort_vals if c not in AGE_COHORT_ORDER)
        _cat_ord[split_col] = _known + _unknown

    fig = px.bar(
        chart_df,
        x="Wert",
        y=ORG_COL,
        color=split_col,
        orientation="h",
        barmode="stack",
        custom_data=["Wert_Anzeige"],
        color_discrete_map=_cdm,
        category_orders=_cat_ord,
    )
    fig.update_traces(hovertemplate="<b>%{y}</b><br>%{fullData.name}: %{customdata[0]}<extra></extra>")
    fig.update_layout(
        height=chart_height,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="",
        yaxis_title="",
        legend_title_text=title,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    apply_legend_bottom(fig)

    col_chart, col_table = st.columns([3, 2])
    with col_chart:
        st.plotly_chart(fig, use_container_width=True)
    with col_table:
        dataframe_compat(display_df, width="stretch", hide_index=True)
        excel_data = compact.export_to_excel(
            pivot_df,
            key_prefix=key_prefix,
            dimension_name=f"Organisationseinheit x {title}",
            value_type=metric_config["value_type"],
            table_title=f"Organisationseinheit nach {title}",
        )
        download_button_compat(
            label="Excel Download",
            data=excel_data,
            file_name=f"{key_prefix}_{split_col.lower().replace(' ', '_')}.xlsx",
            mime=compact._EXCEL_MIME,
            key=f"download_{key_prefix}_{split_col}",
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Data quality block (based on full filtered_df — not limited by top_n)
# ---------------------------------------------------------------------------

def _render_data_quality_block(
    filtered_df: pd.DataFrame,
    mapped_df: pd.DataFrame,
    unmapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
) -> None:
    total_rows = len(filtered_df)
    unmapped_rows = len(unmapped_df)
    total_metric = _get_metric_total(filtered_df, metric_view, compact)
    unmapped_metric = _get_metric_total(unmapped_df, metric_view, compact)
    unmapped_share = unmapped_metric / total_metric if total_metric > 0 else 0
    unmapped_employees = len(get_unique_employees(unmapped_df)) if not unmapped_df.empty else 0
    mapped_share = _get_metric_total(mapped_df, metric_view, compact) / total_metric if total_metric > 0 else 0

    if unmapped_rows == 0:
        st.success("Alle sichtbaren Datensätze sind einer Organisationseinheit zugeordnet.")
        st.caption("Nicht zugeordnete Datensätze haben im aktuellen Filterkontext keine Wirkung.")
        return

    compact.render_kpi_cards_styled(
        [
            {
                "title": "Nicht zugeordnete Zeilen",
                "value": compact.format_number(unmapped_rows, 0),
                "subtitle": f"{compact.format_percent(unmapped_rows / total_rows) if total_rows > 0 else '0,0%'} der sichtbaren Datensätze",
                "icon": "🧩",
                "status": "warning",
            },
            {
                "title": "Nicht zugeordnete MA",
                "value": compact.format_number(unmapped_employees, 0),
                "subtitle": "Unique Mitarbeitende ohne Organisationseinheit",
                "icon": "👥",
                "status": "warning" if unmapped_employees > 0 else "good",
            },
            {
                "title": "Einfluss auf Kennzahl",
                "value": _format_metric_value(unmapped_metric, metric_view, compact),
                "subtitle": f"{compact.format_percent(unmapped_share)} der sichtbaren Kennzahl",
                "icon": "⚠️",
                "status": "warning" if unmapped_metric > 0 else "good",
            },
            {
                "title": "Zugeordneter Anteil",
                "value": compact.format_percent(mapped_share),
                "subtitle": "Anteil der Hauptanalyse am sichtbaren Gesamtvolumen",
                "icon": "✅",
                "status": "good" if mapped_share >= 0.95 else "default",
            },
        ]
    )

    render_context_box(
        "Wirkung auf die Hauptanalyse",
        "Die Rangliste und die Detailblöcke oberhalb schließen nicht zugeordnete Datensätze bewusst aus. So bleiben fachliche Auswertungen sauber, während der Rest hier transparent nachvollziehbar bleibt.",
        tone="warning",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    compact = load_compact_page_module()

    render_page_header(
        "Organisationseinheiten-Analyse",
        "IST-Sicht auf die aktuell sichtbare Personalsituation nach Organisationseinheiten.",
    )

    set_metric_page_hint(
        "Organisationseinheiten-Analyse nutzt den globalen Metrik-Switch direkt für KPIs, Rangliste und Detailblöcke."
    )

    snapshot_df, history_df, _, _ = load_and_prepare_data(show_status_messages=False)
    prepared_df = compact.prepare_compact_data(snapshot_df)
    prepared_df = _normalize_org_column(prepared_df)

    render_global_filters(prepared_df, history_df)
    filtered_df = apply_filters(prepared_df)
    filter_summary = get_filter_summary()
    render_active_filter_banner(filter_summary)

    metric_view = normalize_global_metric_view(get_global_metric_view()) or "MAK"
    metric_config = _get_metric_config(filtered_df, metric_view)
    if metric_config is None:
        st.error(f"Die Kennzahl `{metric_view}` ist in den aktuell geladenen Daten nicht verfügbar.")
        return

    filtered_df = _normalize_org_column(filtered_df)
    mapped_df = filtered_df[filtered_df[ORG_COL] != ORG_NOT_ASSIGNED].copy()
    unmapped_df = filtered_df[filtered_df[ORG_COL] == ORG_NOT_ASSIGNED].copy()

    # --- KPI block (always uses full mapped_df) ---
    render_section_intro(
        "KPI-Überblick",
        f"Kennzahl: {metric_view} · aktueller Filterkontext",
    )
    kpis = _build_kpis(mapped_df, filtered_df, unmapped_df, metric_view, compact)
    if kpis:
        compact.render_kpi_cards_styled(kpis)

    if mapped_df.empty:
        render_context_box(
            "Keine zugeordneten Organisationseinheiten im aktuellen Ausschnitt",
            "Im aktuellen Filterkontext sind keine zugeordneten Organisationseinheiten sichtbar. Die fachliche Hauptanalyse bleibt deshalb leer; der Datenqualitätsblock unten zeigt den verbleibenden Rest transparent an.",
            tone="warning",
        )
        _render_data_quality_block(filtered_df, mapped_df, unmapped_df, metric_view, metric_config, compact)
        return

    # --- Top-N selector ---
    st.divider()
    col_ctrl, _ = st.columns([2, 3])
    with col_ctrl:
        selected_top_n: str = st.radio(
            "Anzahl Organisationseinheiten",
            _TOP_N_OPTIONS,
            index=_TOP_N_OPTIONS.index(
                st.session_state.get(_TOP_N_SESSION_KEY, _TOP_N_DEFAULT)
            ),
            horizontal=True,
            key=_TOP_N_SESSION_KEY,
        )
    st.caption("Die Auswahl steuert alle Grafiken und Tabellen dieser Seite. Sortierung nach Mitarbeiterzahl.")

    # --- Central display list (headcount-based, metric-independent) ---
    display_orgs = _get_visible_org_units_for_display(mapped_df, selected_top_n)

    if not display_orgs:
        st.info("Im aktuellen Filterkontext sind keine Organisationseinheiten für die Analyse verfügbar.")
        return

    # --- Rangliste (display_orgs order — headcount; values follow global metric) ---
    render_section_intro(
        "Rangliste der Organisationseinheiten",
        "Sortiert nach Mitarbeiterzahl. Werte gemäß aktuell gewählter Kennzahl.",
    )
    _render_org_rangliste(mapped_df, metric_view, metric_config, compact, display_orgs)

    # --- Zusammensetzung (all three blocks use same display_orgs) ---
    if selected_top_n == "Alle":
        composition_caption = "Alle Organisationseinheiten im aktuellen Filterkontext."
    else:
        composition_caption = f"Top-{selected_top_n}-Organisationseinheiten im aktuellen Filterkontext."

    render_section_intro(
        "Zusammensetzung der Top-Organisationseinheiten",
        composition_caption,
    )
    for i, (title, split_col) in enumerate(DETAIL_BLOCKS):
        if i > 0:
            st.divider()
        st.subheader(title)
        _render_org_split_block(
            mapped_df,
            title,
            split_col,
            metric_view,
            metric_config,
            compact,
            key_prefix=f"org_{split_col.lower().replace(' ', '_')}",
            display_orgs=display_orgs,
        )

    # --- Datenqualität (always full filtered_df, never limited by top_n) ---
    with st.expander("Datenqualität", expanded=False):
        _render_data_quality_block(filtered_df, mapped_df, unmapped_df, metric_view, metric_config, compact)

    with st.expander("Hinweise zur Methodik", expanded=False):
        st.markdown(
            "Die Seite zeigt eine IST-Analyse der aktuell sichtbaren Personalsituation. "
            "Die globale Kennzahl aus der Sidebar steuert KPI-Header, Rangliste und Detailblöcke.\n\n"
            "Die Hauptanalyse zeigt zugeordnete Organisationseinheiten. Nicht zugeordnete Datensätze werden "
            "im Datenqualitätsblock separat ausgewiesen.\n\n"
            "Die angezeigten Organisationseinheiten werden nach Mitarbeiterzahl sortiert. "
            "Der Regler \"Anzahl Organisationseinheiten\" steuert, wie viele Organisationseinheiten in "
            "Grafiken und Tabellen angezeigt werden. Die Kennzahl aus der Sidebar steuert die dargestellten Werte.\n\n"
            "Filter und Exklusionen aus der Sidebar definieren den Betrachtungsraum."
        )


if __name__ == "__main__":
    main()
