"""
Streamlit page: Jobfamily-Analyse (IST).
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
from dataloader.jobfamily_service import JOBFAMILY_UNMAPPED, normalize_jobfamily_column
from dataloader.kpi_engine import (
    compute_atz_kpis,
    compute_fte_roh,
    compute_teilzeit_kpis,
    get_unique_employees,
)
from dataloader.loader import load_and_prepare_data
from utils.compact_page_loader import load_compact_page_module
from utils.plot_helpers import (
    AGE_COHORT_ORDER,
    apply_legend_bottom,
    get_age_cohort_color_map,
    get_tariff_group_color_map,
)
from config.settings import TARIFF_GROUPS


DETAIL_BLOCKS = [
    ("Geschlecht", "Geschlecht"),
    ("Alterskohorten", "Alterskohorte"),
    ("Beschäftigungsstatus", "Beschäftigungsstatus"),
]

TOP_JOBFAMILY_COUNT = 8


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


def _count_visible_jobfamilies(df: pd.DataFrame) -> int:
    if "Jobfamily" not in df.columns or df.empty:
        return 0
    return int(df["Jobfamily"].dropna().astype(str).nunique())


def _get_largest_jobfamily_label(
    mapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
) -> tuple[str, str]:
    if mapped_df.empty:
        return "Keine zugeordnete Jobgruppe", "Aktuelle Filter enthalten nur UNMAPPED oder keine Daten."

    ranking_df = compact.create_breakdown_table(mapped_df, "Jobfamily", metric_config["value_col"])
    ranking_df = ranking_df[ranking_df["Jobfamily"] != JOBFAMILY_UNMAPPED]
    if ranking_df.empty:
        return "Keine zugeordnete Jobgruppe", "Aktuelle Filter enthalten nur UNMAPPED oder keine Daten."

    top_row = ranking_df.iloc[0]
    return str(top_row["Jobfamily"]), _format_metric_value(float(top_row["IST"]), metric_view, compact)


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
    visible_jobfamilies = _count_visible_jobfamilies(mapped_df)
    total_metric = _get_metric_total(filtered_df, metric_view, compact)
    unmapped_metric = _get_metric_total(unmapped_df, metric_view, compact)

    if metric_view == "Köpfe":
        total_koepfe = compact.get_ist_koepfe(emp_df)
        unique_emp = get_unique_employees(emp_df)
        female_count = int((unique_emp["Geschlecht"] == "w").sum()) if "Geschlecht" in unique_emp.columns else 0
        female_rate = female_count / total_koepfe if total_koepfe > 0 else 0
        atz = compute_atz_kpis(emp_df)
        largest_name, largest_value = _get_largest_jobfamily_label(mapped_df, metric_view, metric_config, compact)
        return [
            {
                "title": "Zugeordnete Köpfe",
                "value": compact.format_number(total_koepfe, 0),
                "subtitle": "Mitarbeitende mit Zuordnung",
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
                "title": "Größte Jobgruppe",
                "value": largest_name,
                "subtitle": f"{largest_value} · {visible_jobfamilies} sichtbar",
                "icon": "💼",
                "status": "default" if unmapped_metric <= 0 else "warning",
            },
        ]

    if metric_view == "MAK":
        total_mak = compact.get_ist_mak(emp_df)
        total_fte_roh = compute_fte_roh(emp_df)
        total_koepfe = compact.get_ist_koepfe(emp_df)
        teilzeit = compute_teilzeit_kpis(emp_df)
        avg_fte = total_mak / total_koepfe if total_koepfe > 0 else 0
        largest_name, largest_value = _get_largest_jobfamily_label(mapped_df, metric_view, metric_config, compact)
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
                "title": "Größte Jobgruppe",
                "value": largest_name,
                "subtitle": f"{largest_value} · {visible_jobfamilies} sichtbar",
                "icon": "💼",
                "status": "default" if unmapped_metric <= 0 else "warning",
            },
        ]

    total_cost = compact.get_ist_eur(emp_df)
    total_koepfe = compact.get_ist_koepfe(emp_df)
    total_mak = compact.get_ist_mak(emp_df)
    avg_cost = total_cost / total_koepfe if total_koepfe > 0 else 0
    cost_per_mak = total_cost / total_mak if total_mak > 0 else 0
    largest_name, largest_value = _get_largest_jobfamily_label(mapped_df, metric_view, metric_config, compact)
    mapped_share = (total_metric - unmapped_metric) / total_metric if total_metric > 0 else 0
    return [
        {
            "title": "Zugeordnete Kosten",
            "value": compact.format_currency(total_cost),
            "subtitle": "Sichtbare Jahreskosten in zugeordneten Jobgruppen",
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
            "title": "Größte Jobgruppe",
            "value": largest_name,
            "subtitle": f"{largest_value} · {visible_jobfamilies} sichtbar",
            "icon": "💼",
            "status": "default" if unmapped_metric <= 0 else "warning",
        },
    ]


def _get_top_jobfamilies(
    mapped_df: pd.DataFrame,
    metric_config: dict[str, str],
    compact,
    top_n: int = TOP_JOBFAMILY_COUNT,
) -> list[str]:
    ranking_df = compact.create_breakdown_table(mapped_df, "Jobfamily", metric_config["value_col"])
    ranking_df = ranking_df[ranking_df["Jobfamily"] != JOBFAMILY_UNMAPPED]
    return ranking_df["Jobfamily"].head(top_n).astype(str).tolist()


def _aggregate_jobfamily_split(
    mapped_df: pd.DataFrame,
    split_col: str,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
    top_n: int = TOP_JOBFAMILY_COUNT,
) -> pd.DataFrame:
    if mapped_df.empty or split_col not in mapped_df.columns or "Jobfamily" not in mapped_df.columns:
        return pd.DataFrame()

    top_jobfamilies = _get_top_jobfamilies(mapped_df, metric_config, compact, top_n=top_n)
    if not top_jobfamilies:
        return pd.DataFrame()

    detail_df = mapped_df[mapped_df["Jobfamily"].isin(top_jobfamilies)].copy()
    detail_df[split_col] = detail_df[split_col].fillna("(unbekannt)").astype(str)
    if "Is_Vacant" in detail_df.columns:
        detail_df = detail_df[~detail_df["Is_Vacant"]]

    if detail_df.empty:
        return pd.DataFrame()

    if metric_view == "Köpfe":
        id_col = "PersNr" if "PersNr" in detail_df.columns else None
        if id_col:
            agg_df = (
                detail_df.groupby(["Jobfamily", split_col], observed=True)[id_col]
                .nunique()
                .reset_index(name="Wert")
            )
        else:
            agg_df = detail_df.groupby(["Jobfamily", split_col], observed=True).size().reset_index(name="Wert")
    else:
        value_col = metric_config["value_col"]
        if value_col not in detail_df.columns:
            return pd.DataFrame()
        agg_df = (
            detail_df.groupby(["Jobfamily", split_col], observed=True)[value_col]
            .sum()
            .reset_index(name="Wert")
        )

    totals = agg_df.groupby("Jobfamily", observed=True)["Wert"].sum().sort_values(ascending=False)
    agg_df["Jobfamily"] = pd.Categorical(
        agg_df["Jobfamily"],
        categories=list(totals.index),
        ordered=True,
    )
    agg_df = agg_df.sort_values(["Jobfamily", split_col])
    return agg_df


def _build_split_pivot(agg_df: pd.DataFrame, split_col: str) -> pd.DataFrame:
    if agg_df.empty:
        return pd.DataFrame()

    pivot_df = (
        agg_df.pivot_table(
            index="Jobfamily",
            columns=split_col,
            values="Wert",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    value_columns = [col for col in pivot_df.columns if col != "Jobfamily"]
    pivot_df["Gesamt"] = pivot_df[value_columns].sum(axis=1)
    pivot_df = pivot_df.sort_values("Gesamt", ascending=False)
    return pivot_df


def _format_split_display(pivot_df: pd.DataFrame, metric_view: str, compact) -> pd.DataFrame:
    display_df = pivot_df.copy()
    for col in display_df.columns:
        if col == "Jobfamily":
            continue
        display_df[col] = display_df[col].apply(lambda value: _format_metric_value(float(value), metric_view, compact))
    return display_df


def _render_jobfamily_split_block(
    mapped_df: pd.DataFrame,
    title: str,
    split_col: str,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
    key_prefix: str,
) -> None:
    agg_df = _aggregate_jobfamily_split(mapped_df, split_col, metric_view, metric_config, compact)
    if agg_df.empty:
        st.info(f"Keine auswertbaren Daten im aktuellen Filterkontext.")
        return

    pivot_df = _build_split_pivot(agg_df, split_col)
    display_df = _format_split_display(pivot_df, metric_view, compact)
    chart_df = agg_df.copy()
    chart_df["Jobfamily"] = chart_df["Jobfamily"].astype(str)
    chart_df["Wert_Anzeige"] = chart_df["Wert"].apply(lambda value: _format_metric_value(float(value), metric_view, compact))

    _cdm = None
    _cat_ord: dict = {}
    if split_col == "Alterskohorte":
        _cohort_vals = chart_df[split_col].unique().tolist()
        _cdm = get_age_cohort_color_map(_cohort_vals)
        _known = [c for c in AGE_COHORT_ORDER if c in _cohort_vals]
        _unknown = sorted(c for c in _cohort_vals if c not in AGE_COHORT_ORDER)
        _cat_ord = {split_col: _known + _unknown}
    elif split_col == "TrfGr":
        # Heller-zu-dunkler-Blau-Verlauf nach Entgeltgruppen-Hoehe (niedrige EG
        # hell, hohe EG dunkel), analog zur Alterskohorten-Farbskala oben.
        _trf_vals = chart_df[split_col].unique().tolist()
        _cdm = get_tariff_group_color_map(_trf_vals)
        _known = [g for g in TARIFF_GROUPS if g in _trf_vals]
        _unknown = sorted(v for v in _trf_vals if v not in TARIFF_GROUPS)
        _cat_ord = {split_col: _known + _unknown}

    fig = px.bar(
        chart_df,
        x="Wert",
        y="Jobfamily",
        color=split_col,
        orientation="h",
        barmode="stack",
        custom_data=["Wert_Anzeige"],
        color_discrete_map=_cdm,
        category_orders=_cat_ord if _cat_ord else None,
    )
    fig.update_traces(hovertemplate="<b>%{y}</b><br>%{fullData.name}: %{customdata[0]}<extra></extra>")
    fig.update_layout(
        height=max(360, 42 * chart_df["Jobfamily"].nunique()),
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
            dimension_name=f"Jobgruppe x {title}",
            value_type=metric_config["value_type"],
            table_title=f"Jobgruppe nach {title}",
        )
        download_button_compat(
            label="Excel Download",
            data=excel_data,
            file_name=f"{key_prefix}_{split_col.lower().replace(' ', '_')}.xlsx",
            mime=compact._EXCEL_MIME,
            key=f"download_{key_prefix}_{split_col}",
            width="stretch",
        )


def _resolve_role_metric(metric_view: str, mapped_df: pd.DataFrame) -> tuple[str, dict[str, str] | None, bool]:
    """Koepfe/MAK direkt uebernehmen, EUR auf MAK abbilden (kein Geld in dieser Sektion).

    Gibt (effektive_metric_view, metric_config, ist_fallback) zurueck.
    """
    normalized = normalize_global_metric_view(metric_view) or "MAK"
    is_fallback = normalized == "EUR"
    effective = "MAK" if is_fallback else normalized
    return effective, _get_metric_config(mapped_df, effective), is_fallback


def _build_role_summary_table(
    mapped_df: pd.DataFrame,
    jobfamilies: list[str],
    compact,
) -> pd.DataFrame:
    work = mapped_df[mapped_df["Jobfamily"].isin(jobfamilies)].copy()
    if "Is_Vacant" in work.columns:
        work = work[~work["Is_Vacant"]]

    mak_col = next(
        (c for c in ("MAK_Reporting", "MAK_Calculated", "MAK") if c in work.columns), None
    )
    id_col = next((c for c in ("PersNr", "Personalnummer") if c in work.columns), None)

    if work.empty:
        return pd.DataFrame()

    rows = []
    for jf in jobfamilies:
        sub = work[work["Jobfamily"] == jf]
        koepfe = int(sub[id_col].nunique()) if id_col else len(sub)
        mak = float(sub[mak_col].sum()) if mak_col else 0.0
        rows.append({
            "Jobfamily": jf,
            "Köpfe": koepfe,
            "MAK": compact.format_number(mak, 1),
            "Ø FTE": compact.format_number(mak / koepfe, 2) if koepfe > 0 else "–",
        })
    return pd.DataFrame(rows)


def _render_role_breakdown_block(
    mapped_df: pd.DataFrame,
    metric_view: str,
    compact,
    top_n: int = TOP_JOBFAMILY_COUNT,
) -> None:
    if "TrfGr" not in mapped_df.columns:
        st.info("Keine Tarifgruppen-Spalte verfügbar.")
        return

    role_metric_view, role_metric_config, is_fallback = _resolve_role_metric(metric_view, mapped_df)
    if role_metric_config is None:
        st.info(f"Die Kennzahl `{role_metric_view}` ist in den aktuell geladenen Daten nicht verfügbar.")
        return
    if is_fallback:
        st.caption(
            "Diese Sektion zeigt keine Euro-Werte. Bei globaler Darstellungsart 'EUR' wird hier "
            "stattdessen MAK angezeigt."
        )
    else:
        st.caption(f"Aktuelle Darstellungsart: {role_metric_view}.")

    _render_jobfamily_split_block(
        mapped_df, "Tarifgruppe", "TrfGr",
        role_metric_view, role_metric_config, compact,
        key_prefix="jobfamily_role_trf",
    )

    # Summary table: Köpfe, MAK, Ø FTE per Jobgruppe — keine Euro-Werte
    jobfamilies = _get_top_jobfamilies(mapped_df, role_metric_config, compact, top_n=top_n)
    st.divider()
    st.subheader("Kopfzahl und Kapazität pro Jobgruppe")
    summary_df = _build_role_summary_table(mapped_df, jobfamilies, compact)
    if not summary_df.empty:
        dataframe_compat(summary_df, width="stretch", hide_index=True)


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
        st.success("Alle sichtbaren Datensätze sind einer Jobgruppe zugeordnet.")
        st.caption("UNMAPPED hat im aktuellen Filterkontext keine Wirkung.")
        return

    compact.render_kpi_cards_styled(
        [
            {
                "title": "UNMAPPED Datensätze",
                "value": compact.format_number(unmapped_rows, 0),
                "subtitle": f"{compact.format_percent(unmapped_rows / total_rows) if total_rows > 0 else '0,0%'} der sichtbaren Datensätze",
                "icon": "🧩",
                "status": "warning" if unmapped_rows > 0 else "good",
            },
            {
                "title": "UNMAPPED Mitarbeitende",
                "value": compact.format_number(unmapped_employees, 0),
                "subtitle": "Unique Mitarbeitende mit nicht zugeordneter Zeile",
                "icon": "👥",
                "status": "warning" if unmapped_employees > 0 else "good",
            },
            {
                "title": "UNMAPPED Einfluss",
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
        "Die Rangliste und die Detailblöcke oberhalb schließen UNMAPPED bewusst aus. So bleiben fachliche Auswertungen sauber, während der Rest hier transparent nachvollziehbar bleibt.",
        tone="warning",
    )

    if "Planstelle" not in unmapped_df.columns:
        return

    unmapped_planstellen = compact.create_breakdown_table(
        unmapped_df,
        "Planstelle",
        metric_config["value_col"],
    ).head(10)

    if unmapped_planstellen.empty or "Hinweis" in unmapped_planstellen.columns:
        return

    st.markdown("**Top-Planstellen innerhalb UNMAPPED**")
    display_df = compact.format_dataframe_for_display(unmapped_planstellen, metric_config["value_type"])
    dataframe_compat(display_df, width="stretch", hide_index=True)

    excel_data = compact.export_to_excel(
        unmapped_planstellen,
        key_prefix="jobfamily_quality",
        dimension_name="UNMAPPED Planstellen",
        value_type=metric_config["value_type"],
        table_title="UNMAPPED - Top Planstellen",
    )
    download_button_compat(
        label="Excel Download",
        data=excel_data,
        file_name="jobfamily_unmapped_planstellen.xlsx",
        mime=compact._EXCEL_MIME,
        key="download_jobfamily_quality_planstellen",
        width="stretch",
    )


def main() -> None:
    compact = load_compact_page_module()

    render_page_header(
        "Jobgruppen-Analyse",
        "IST-Sicht auf die aktuell sichtbare Personalsituation.",
    )

    set_metric_page_hint(
        "Jobgruppen-Analyse nutzt den globalen Metrik-Switch direkt für KPIs, Rangliste und Detailblöcke."
    )

    snapshot_df, history_df, _, _ = load_and_prepare_data(show_status_messages=False)
    prepared_df = compact.prepare_compact_data(snapshot_df)
    prepared_df = normalize_jobfamily_column(prepared_df)

    render_global_filters(prepared_df, history_df)
    filtered_df = apply_filters(prepared_df)
    filter_summary = get_filter_summary()
    render_active_filter_banner(filter_summary)

    selected_jobfamilies = st.session_state.get("selected_jobfamilies", [])
    selected_jf_clusters = st.session_state.get("selected_jf_clusters", [])
    if selected_jobfamilies or selected_jf_clusters:
        drilldown_parts = []
        if selected_jobfamilies:
            drilldown_parts.append(f"{len(selected_jobfamilies)} Jobgruppen-Filter")
        if selected_jf_clusters:
            drilldown_parts.append(f"{len(selected_jf_clusters)} Jobgruppen-Cluster-Filter")
        render_context_box(
            "Drilldown aktiv",
            "Die Seite zeigt aktuell einen verengten Ausschnitt aufgrund aktiver "
            + " und ".join(drilldown_parts)
            + ". Das Filterverhalten entspricht bewusst der globalen Sidebar.",
            tone="warning",
        )

    metric_view = normalize_global_metric_view(get_global_metric_view()) or "MAK"
    metric_config = _get_metric_config(filtered_df, metric_view)
    if metric_config is None:
        st.error(f"Die Kennzahl `{metric_view}` ist in den aktuell geladenen Daten nicht verfügbar.")
        return

    filtered_df = normalize_jobfamily_column(filtered_df)
    mapped_df = filtered_df[filtered_df["Jobfamily"] != JOBFAMILY_UNMAPPED].copy()
    unmapped_df = filtered_df[filtered_df["Jobfamily"] == JOBFAMILY_UNMAPPED].copy()

    render_section_intro(
        "KPI-Überblick",
        f"Kennzahl: {metric_view} · aktueller Filterkontext",
    )
    kpis = _build_kpis(mapped_df, filtered_df, unmapped_df, metric_view, compact)
    if kpis:
        compact.render_kpi_cards_styled(kpis)

    if mapped_df.empty:
        render_context_box(
            "Keine zugeordneten Jobgruppen im aktuellen Ausschnitt",
            "Im aktuellen Filterkontext sind nur UNMAPPED-Zeilen oder gar keine Daten sichtbar. Die fachliche Hauptanalyse bleibt deshalb leer; der Datenqualitätsblock unten zeigt den verbleibenden Rest transparent an.",
            tone="warning",
        )
        _render_data_quality_block(filtered_df, mapped_df, unmapped_df, metric_view, metric_config, compact)
        return

    render_section_intro(
        "Rangliste der Jobgruppen",
        "Sortiert nach der aktuell gewählten Kennzahl.",
    )
    compact.render_single_breakdown(
        mapped_df,
        "Jobgruppen",
        "Jobfamily",
        value_col=metric_config["value_col"],
        value_type=metric_config["value_type"],
        key_prefix="jobfamily_ist",
        print_mode=False,
    )

    render_section_intro(
        "Zusammensetzung der Top-Jobgruppen",
        f"Top-{TOP_JOBFAMILY_COUNT}-Jobgruppen im aktuellen Filterkontext.",
    )
    for i, (title, split_col) in enumerate(DETAIL_BLOCKS):
        if i > 0:
            st.divider()
        st.subheader(title)
        _render_jobfamily_split_block(
            mapped_df,
            title,
            split_col,
            metric_view,
            metric_config,
            compact,
            key_prefix=f"jobfamily_{split_col.lower().replace(' ', '_')}",
        )

    # --- Personalstruktur nach Tarifgruppe (folgt dem globalen Metrik-Switch, keine EUR-Werte) ---
    st.divider()
    render_section_intro(
        "Personalstruktur nach Tarifgruppe",
        f"Köpfe bzw. MAK der Top-{TOP_JOBFAMILY_COUNT}-Jobgruppen. Folgt der globalen Darstellungsart (Köpfe/MAK).",
    )
    _render_role_breakdown_block(mapped_df, metric_view, compact, top_n=TOP_JOBFAMILY_COUNT)

    with st.expander("Datenqualität", expanded=False):
        _render_data_quality_block(filtered_df, mapped_df, unmapped_df, metric_view, metric_config, compact)

    with st.expander("Hinweise zur Methodik", expanded=False):
        st.markdown(
            "Die Seite zeigt eine IST-Analyse der aktuell sichtbaren Personalsituation. "
            "Die globale Kennzahl aus der Sidebar steuert KPI-Header, Rangliste und Detailblöcke.\n\n"
            "Die Hauptanalyse zeigt zugeordnete Jobgruppen. Nicht zugeordnete Datensätze werden "
            "im Datenqualitätsblock separat ausgewiesen.\n\n"
            "Filter und Exklusionen aus der Sidebar definieren den Betrachtungsraum."
        )


if __name__ == "__main__":
    main()
