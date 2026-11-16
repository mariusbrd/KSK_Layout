"""
Streamlit page: Organisationseinheiten-Analyse (IST).
"""

from __future__ import annotations

from pathlib import Path
from numbers import Real
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
    apply_robust_filter,
    get_active_view_filters,
    get_filter_summary,
    get_global_metric_view,
    normalize_global_metric_view,
    render_global_filters,
    set_metric_page_hint,
)
from components.ui_compat import (
    dataframe_compat,
    dataframe_export_fingerprint,
    lazy_excel_download_button_compat,
)
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
from utils.org_simulation_export import render_org_simulation_export
from utils.plot_helpers import (
    AGE_COHORT_ORDER,
    apply_legend_bottom,
    get_age_cohort_color_map,
    get_tariff_group_color_map,
)
from config.settings import TARIFF_GROUPS


ORG_COL = "Organisationseinheit"
ORG_NOT_ASSIGNED = "Nicht zugeordnet"
_ORG_UNASSIGNED_SENTINELS = {"Nicht zugeordnet", "UNMAPPED", "Unmapped", "Unclustered"}

_TOP_N_OPTIONS = ["8", "10", "15", "20", "Alle"]
_TOP_N_SESSION_KEY = "orgunit_analysis_top_n"
_TOP_N_DEFAULT = "8"
_SORT_OPTIONS_BASE = ["Aktuelle Kennzahl", "Köpfe", "MAK"]
_SORT_OPTIONS_COMPARISON = ["Delta", "Abgänge"]
_SORT_SESSION_KEY = "orgunit_analysis_sort_by"
_SORT_DEFAULT = "Aktuelle Kennzahl"
_MIN_SIZE_OPTIONS = ["Alle", "mind. 3 Köpfe", "mind. 5 Köpfe", "mind. 10 Köpfe", "mind. 1,0 MAK"]
_MIN_SIZE_SESSION_KEY = "orgunit_analysis_min_size"
_MIN_SIZE_DEFAULT = "Alle"
_SIM_FOCUS_OPTIONS = ["Alle", "Nur mit Veränderung", "Nur mit Abgängen"]
_SIM_FOCUS_SESSION_KEY = "orgunit_analysis_sim_focus"
_SIM_FOCUS_DEFAULT = "Alle"


def _org_lineage_ids(value_label: str, ist_id: str, sim_id: str) -> list[str]:
    ids = [sim_id] if value_label == "Simulation" else [ist_id]
    if value_label == "Simulation":
        ids.append("10-07")
    return ids

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
# Legacy display-list helper
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


def _active_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "Is_Vacant" in df.columns:
        return df[df["Is_Vacant"] != True].copy()
    return df.copy()


def _aggregate_metric(
    df: pd.DataFrame,
    group_cols: list[str],
    metric_view: str,
    metric_config: dict[str, str],
) -> pd.DataFrame:
    if df.empty or any(col not in df.columns for col in group_cols):
        return pd.DataFrame(columns=group_cols + ["Wert"])

    work_df = _active_rows(df)
    if work_df.empty:
        return pd.DataFrame(columns=group_cols + ["Wert"])

    if metric_view == "Köpfe":
        id_col = next((c for c in ("PersNr", "Personalnummer") if c in work_df.columns), None)
        if id_col:
            return (
                work_df.groupby(group_cols, observed=True, dropna=False)[id_col]
                .nunique()
                .reset_index(name="Wert")
            )
        return work_df.groupby(group_cols, observed=True, dropna=False).size().reset_index(name="Wert")

    value_col = metric_config["value_col"]
    if value_col not in work_df.columns:
        return pd.DataFrame(columns=group_cols + ["Wert"])

    work_df[value_col] = pd.to_numeric(work_df[value_col], errors="coerce").fillna(0.0)
    return (
        work_df.groupby(group_cols, observed=True, dropna=False)[value_col]
        .sum()
        .reset_index(name="Wert")
    )


def _build_departure_org_summary(events: pd.DataFrame) -> pd.DataFrame:
    columns = [ORG_COL, "Abgänge", "Personen", "MAK-Verlust"]
    if events is None or events.empty:
        return pd.DataFrame(columns=columns)

    work = events.copy()
    if ORG_COL in work:
        work[ORG_COL] = work[ORG_COL].map(
            lambda value: str(int(value)) if isinstance(value, Real) and pd.notna(value)
            and float(value).is_integer() else value)
    work = _normalize_org_column(work)
    if "persnr" not in work.columns:
        work["persnr"] = pd.NA

    if "Abgänge" not in work.columns:
        work["headcount_change"] = pd.to_numeric(work.get("headcount_change", 0), errors="coerce").fillna(0)
        work["Abgänge"] = work["headcount_change"].abs()
    if "MAK-Verlust" not in work.columns:
        work["MAK-Verlust"] = pd.to_numeric(work.get("mak_change", 0), errors="coerce").fillna(0).abs()

    grouped = (
        work.groupby(ORG_COL, dropna=False)
        .agg(
            Abgänge=("Abgänge", "sum"),
            Personen=("persnr", "nunique"),
            MAK_Verlust=("MAK-Verlust", "sum"),
        )
        .reset_index()
        .sort_values(["Abgänge", ORG_COL], ascending=[False, True])
    )
    grouped["Abgänge"] = grouped["Abgänge"].astype(int)
    grouped["Personen"] = grouped["Personen"].astype(int)
    grouped["MAK-Verlust"] = grouped["MAK_Verlust"].round(2)
    return grouped.drop(columns=["MAK_Verlust"])


def _build_departure_reason_summary(events: pd.DataFrame) -> pd.DataFrame:
    if events is None or events.empty:
        return pd.DataFrame()

    reason_col = "reason_label" if "reason_label" in events.columns else "reason_code"
    if reason_col not in events.columns:
        return pd.DataFrame()

    work = events.copy()
    if ORG_COL not in work.columns:
        work[ORG_COL] = ORG_NOT_ASSIGNED
    if "persnr" not in work.columns:
        work["persnr"] = pd.NA
    if "Abgänge" not in work.columns:
        work["headcount_change"] = pd.to_numeric(work.get("headcount_change", 0), errors="coerce").fillna(0)
        work["Abgänge"] = work["headcount_change"].abs()
    if "MAK-Verlust" not in work.columns:
        work["MAK-Verlust"] = pd.to_numeric(work.get("mak_change", 0), errors="coerce").fillna(0).abs()

    reason_df = (
        work.groupby([ORG_COL, reason_col], dropna=False)
        .agg(
            Abgänge=("Abgänge", "sum"),
            Personen=("persnr", "nunique"),
            MAK_Verlust=("MAK-Verlust", "sum"),
        )
        .reset_index()
        .sort_values(["Abgänge", ORG_COL], ascending=[False, True])
    )
    reason_df["Abgänge"] = reason_df["Abgänge"].astype(int)
    reason_df["Personen"] = reason_df["Personen"].astype(int)
    reason_df["MAK-Verlust"] = reason_df["MAK_Verlust"].round(2)
    return reason_df.drop(columns=["MAK_Verlust"])


def _get_widget_index(options: list[str], value: str | None, default: str) -> int:
    selected = value if value in options else default
    if selected not in options:
        selected = options[0]
    return options.index(selected)


def _resolve_sort_metric(sort_by: str, metric_view: str) -> str:
    if sort_by == "Aktuelle Kennzahl":
        current_metric = normalize_global_metric_view(metric_view) or "MAK"
        return "MAK" if current_metric == "EUR" else current_metric
    if sort_by in {"Köpfe", "MAK"}:
        return sort_by
    # Legacy/session compatibility: EUR/Mitarbeiterzahl are no longer visible options.
    if sort_by == "Mitarbeiterzahl":
        return "Köpfe"
    if sort_by == "EUR":
        return "MAK"
    return "Köpfe"


def _merge_metric_for_ranking(
    ranking_df: pd.DataFrame,
    source_df: pd.DataFrame,
    metric_view: str,
    target_col: str,
) -> pd.DataFrame:
    metric_config = _get_metric_config(source_df, metric_view)
    if metric_config is None:
        ranking_df[target_col] = 0.0
        return ranking_df

    metric_df = _aggregate_metric(source_df, [ORG_COL], metric_view, metric_config).rename(columns={"Wert": target_col})
    ranking_df = ranking_df.merge(metric_df, on=ORG_COL, how="left")
    ranking_df[target_col] = pd.to_numeric(ranking_df[target_col], errors="coerce").fillna(0.0)
    return ranking_df


def _build_orgunit_ranking_frame(
    mapped_df: pd.DataFrame,
    comparison_mapped_df: pd.DataFrame | None,
    departure_events: pd.DataFrame | None,
    metric_view: str,
    metric_config: dict[str, str],
    *,
    comparison_active: bool,
    value_label: str,
    comparison_label: str,
) -> pd.DataFrame:
    orgs: list[str] = []
    for org in _get_visible_org_units_for_display(mapped_df, "Alle"):
        if org not in orgs:
            orgs.append(org)

    if comparison_active and comparison_mapped_df is not None:
        for org in _get_visible_org_units_for_display(comparison_mapped_df, "Alle"):
            if org != ORG_NOT_ASSIGNED and org not in orgs:
                orgs.append(org)

    departure_summary = _build_departure_org_summary(departure_events if departure_events is not None else pd.DataFrame())
    if comparison_active and not departure_summary.empty:
        for org in departure_summary[ORG_COL].astype(str).tolist():
            if org != ORG_NOT_ASSIGNED and org not in orgs:
                orgs.append(org)

    ranking_df = pd.DataFrame({ORG_COL: orgs})
    if ranking_df.empty:
        return ranking_df

    for sort_metric in ("Köpfe", "MAK"):
        ranking_df = _merge_metric_for_ranking(ranking_df, mapped_df, sort_metric, sort_metric)
        if comparison_active and comparison_mapped_df is not None:
            ranking_df = _merge_metric_for_ranking(
                ranking_df,
                comparison_mapped_df,
                sort_metric,
                f"{comparison_label} {sort_metric}",
            )

    ranking_df["Mindestgröße Köpfe"] = pd.to_numeric(ranking_df["Köpfe"], errors="coerce").fillna(0.0)
    ranking_df["Mindestgröße MAK"] = pd.to_numeric(ranking_df["MAK"], errors="coerce").fillna(0.0)
    if comparison_active and comparison_mapped_df is not None:
        ranking_df["Mindestgröße Köpfe"] = ranking_df[["Mindestgröße Köpfe", f"{comparison_label} Köpfe"]].max(axis=1)
        ranking_df["Mindestgröße MAK"] = ranking_df[["Mindestgröße MAK", f"{comparison_label} MAK"]].max(axis=1)

        comparison_values = _build_org_metric_comparison(
            mapped_df,
            comparison_mapped_df,
            metric_view,
            metric_config,
            orgs,
            value_label=value_label,
            comparison_label=comparison_label,
        )
        ranking_df = ranking_df.merge(comparison_values[[ORG_COL, "Delta"]], on=ORG_COL, how="left")
    else:
        ranking_df["Delta"] = 0.0

    ranking_df["Delta"] = pd.to_numeric(ranking_df["Delta"], errors="coerce").fillna(0.0)
    if not departure_summary.empty:
        ranking_df = ranking_df.merge(
            departure_summary[[ORG_COL, "Abgänge", "MAK-Verlust"]],
            on=ORG_COL,
            how="left",
        )
    else:
        ranking_df["Abgänge"] = 0
        ranking_df["MAK-Verlust"] = 0.0
    ranking_df["Abgänge"] = pd.to_numeric(ranking_df["Abgänge"], errors="coerce").fillna(0).astype(int)
    ranking_df["MAK-Verlust"] = pd.to_numeric(ranking_df["MAK-Verlust"], errors="coerce").fillna(0.0)
    return ranking_df


def _apply_orgunit_top_filters(
    ranking_df: pd.DataFrame,
    top_n: str,
    sort_by: str,
    metric_view: str,
    min_size: str,
    sim_focus: str,
    *,
    comparison_active: bool,
) -> list[str]:
    if ranking_df.empty:
        return []

    filtered = ranking_df[ranking_df[ORG_COL] != ORG_NOT_ASSIGNED].copy()
    if min_size == "mind. 3 Köpfe":
        filtered = filtered[filtered["Mindestgröße Köpfe"] >= 3]
    elif min_size == "mind. 5 Köpfe":
        filtered = filtered[filtered["Mindestgröße Köpfe"] >= 5]
    elif min_size == "mind. 10 Köpfe":
        filtered = filtered[filtered["Mindestgröße Köpfe"] >= 10]
    elif min_size == "mind. 1,0 MAK":
        filtered = filtered[filtered["Mindestgröße MAK"] >= 1.0]

    if comparison_active and sim_focus == "Nur mit Veränderung":
        filtered = filtered[filtered["Delta"].abs() > 0.000001]
    elif comparison_active and sim_focus == "Nur mit Abgängen":
        filtered = filtered[filtered["Abgänge"] > 0]

    if sort_by == "Delta":
        filtered["_sort_value"] = filtered["Delta"].abs()
    elif sort_by == "Abgänge":
        filtered["_sort_value"] = pd.to_numeric(filtered.get("Abgänge", 0), errors="coerce").fillna(0)
    else:
        sort_col = _resolve_sort_metric(sort_by, metric_view)
        filtered["_sort_value"] = pd.to_numeric(filtered.get(sort_col, 0), errors="coerce").fillna(0)

    filtered = filtered.sort_values(["_sort_value", ORG_COL], ascending=[False, True])
    if top_n != "Alle":
        filtered = filtered.head(int(top_n))

    return filtered[ORG_COL].astype(str).tolist()


def _apply_monte_carlo_ranking_values(
    ranking_df: pd.DataFrame, rank_stats: dict,
    comparison_mapped_df: pd.DataFrame | None, metric_view: str,
    metric_config: dict[str, str], comparison_label: str,
    comparison_active: bool,
) -> pd.DataFrame:
    """Drive all existing top-list controls from per-run means."""
    ranking_df = ranking_df.copy()
    mc_orgs = sorted(set().union(*(set(rank_stats[metric])
                                  for metric in ("heads", "mak", "eur", "departures"))))
    missing_orgs = [org for org in mc_orgs if org not in set(ranking_df[ORG_COL])]
    if missing_orgs:
        ranking_df = pd.concat([ranking_df, pd.DataFrame({ORG_COL: missing_orgs})],
                               ignore_index=True)
    for label, code in (("Köpfe", "heads"), ("MAK", "mak")):
        ranking_df[label] = ranking_df[ORG_COL].map(
            lambda org: rank_stats[code].get(str(org), {}).get("mean", 0.0))
        if comparison_active:
            if f"{comparison_label} {label}" not in ranking_df:
                ranking_df[f"{comparison_label} {label}"] = 0.0
            baseline = pd.to_numeric(ranking_df[f"{comparison_label} {label}"],
                                     errors="coerce").fillna(0.0)
            ranking_df[f"Mindestgröße {label}"] = ranking_df[label].combine(baseline, max)
        else:
            ranking_df[f"Mindestgröße {label}"] = ranking_df[label]
    ranking_df["Abgänge"] = ranking_df[ORG_COL].map(
        lambda org: rank_stats["departures"].get(str(org), {}).get("mean", 0.0))
    ranking_df["MAK-Verlust"] = ranking_df[ORG_COL].map(
        lambda org: rank_stats["mak_loss"].get(str(org), {}).get("mean", 0.0))
    if comparison_active:
        baseline = _aggregate_metric(comparison_mapped_df, [ORG_COL], metric_view,
                                     metric_config).set_index(ORG_COL)["Wert"]
        code = {"Köpfe": "heads", "MAK": "mak", "EUR": "eur"}[metric_view]
        ranking_df["Delta"] = ranking_df[ORG_COL].map(
            lambda org: rank_stats[code].get(str(org), {}).get("mean", 0.0)
            - float(baseline.get(str(org), 0.0)))
    return ranking_df


def _filter_departure_events(events: pd.DataFrame) -> pd.DataFrame:
    if events is None or events.empty:
        return pd.DataFrame()

    filters = get_active_view_filters()
    out = events.copy()
    out = apply_robust_filter(out, ORG_COL, filters.get("selected_org_units", []))
    out = apply_robust_filter(out, "Jobfamily", filters.get("selected_jobfamilies", []))
    out = apply_robust_filter(out, "OE-Cluster", filters.get("selected_oe_clusters", []))
    out = apply_robust_filter(out, "JF-Cluster", filters.get("selected_jf_clusters", []))
    return out


def _build_org_metric_comparison(
    mapped_df: pd.DataFrame,
    comparison_mapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    display_orgs: list[str],
    *,
    value_label: str,
    comparison_label: str,
    departure_events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    current = _aggregate_metric(mapped_df, [ORG_COL], metric_view, metric_config).rename(columns={"Wert": value_label})
    previous = _aggregate_metric(comparison_mapped_df, [ORG_COL], metric_view, metric_config).rename(columns={"Wert": comparison_label})
    out = pd.DataFrame({ORG_COL: display_orgs})
    out = out.merge(previous, on=ORG_COL, how="left").merge(current, on=ORG_COL, how="left")
    out[[comparison_label, value_label]] = out[[comparison_label, value_label]].fillna(0.0)
    out["Delta"] = out[value_label] - out[comparison_label]
    out["Delta %"] = out.apply(
        lambda row: row["Delta"] / row[comparison_label] if row[comparison_label] else 0.0,
        axis=1,
    )

    departures = _build_departure_org_summary(departure_events if departure_events is not None else pd.DataFrame())
    if not departures.empty:
        out = out.merge(departures[[ORG_COL, "Abgänge", "MAK-Verlust"]], on=ORG_COL, how="left")
        out[["Abgänge", "MAK-Verlust"]] = out[["Abgänge", "MAK-Verlust"]].fillna(0)
        out["Abgänge"] = out["Abgänge"].astype(int)
    return out


def _format_comparison_table(
    comparison_df: pd.DataFrame,
    metric_view: str,
    compact,
    *,
    value_columns: list[str],
) -> pd.DataFrame:
    display_df = comparison_df.copy()
    for col in value_columns:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda value: _format_metric_value(float(value), metric_view, compact))
    if "Delta" in display_df.columns:
        display_df["Delta"] = display_df["Delta"].apply(lambda value: _format_metric_value(float(value), metric_view, compact))
    if "Delta %" in display_df.columns:
        display_df["Delta %"] = display_df["Delta %"].apply(lambda value: compact.format_percent(float(value)))
    return display_df


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
                "subtitle": f"Roh-MAK {compact.format_number(total_fte_roh, 1)} bei {total_koepfe} Köpfen",
                "icon": "📈",
                "status": "good",
            },
            {
                "title": "Ø MAK",
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


def _build_monte_carlo_kpis(statistics: dict, metric_view: str, compact) -> list[dict]:
    """Keep the four familiar cards, with every numeric value from all runs."""
    def band(key: str, formatter) -> str:
        values = statistics[key]
        return f"P10–P90: {formatter(values['lower'])} bis {formatter(values['upper'])}"

    def heads(value: float) -> str:
        return compact.format_number(value, 1)

    def mak(value: float) -> str:
        return compact.format_number(value, 1)

    def percent(value: float) -> str:
        return compact.format_percent(value)

    def pct_points(value: float) -> str:
        return f"{value:.1f}%".replace(".", ",")

    rank_metric = {"Köpfe": "heads", "MAK": "mak", "EUR": "eur"}[metric_view]
    orgs = statistics["organizations"][rank_metric]
    largest = max(orgs, key=lambda org: (orgs[org]["mean"], org)) if orgs else None
    largest_card = {
        "title": "Größte Organisationseinheit", "value": largest or "Keine zugeordnete OE",
        "subtitle": (f"Mittelwert: {_format_metric_value(orgs[largest]['mean'], metric_view, compact)} · "
                     f"P10–P90: {_format_metric_value(orgs[largest]['lower'], metric_view, compact)} bis "
                     f"{_format_metric_value(orgs[largest]['upper'], metric_view, compact)} · "
                     f"{len(orgs)} sichtbar") if largest else "Keine zugeordnete Organisationseinheit",
        "icon": "🏢", "status": "default",
    }
    if metric_view == "Köpfe":
        return [
            {"title": "Zugeordnete Köpfe", "value": heads(statistics["heads"]["mean"]),
             "subtitle": band("heads", heads), "icon": "👥", "status": "good"},
            {"title": "Frauenanteil", "value": percent(statistics["female_share"]["mean"]),
             "subtitle": f"{band('female_share', percent)} · Ø {heads(statistics['female']['mean'])} Frauen",
             "icon": "👤", "status": "default"},
            {"title": "ATZ-Quote", "value": pct_points(statistics["atz_share_pct"]["mean"]),
             "subtitle": f"{band('atz_share_pct', pct_points)} · Ø {heads(statistics['atz']['mean'])} Mitarbeitende",
             "icon": "⏰", "status": "default"},
            largest_card,
        ]
    if metric_view == "MAK":
        return [
            {"title": "Zugeordnete MAK", "value": mak(statistics["mak"]["mean"]),
             "subtitle": f"{band('mak', mak)} · Roh-MAK Ø {mak(statistics['raw_mak']['mean'])} "
                         f"bei Ø {heads(statistics['heads']['mean'])} Köpfen",
             "icon": "📈", "status": "good"},
            {"title": "Ø MAK", "value": compact.format_number(statistics["average_mak"]["mean"], 2),
             "subtitle": band("average_mak", lambda value: compact.format_number(value, 2)),
             "icon": "📊", "status": "default"},
            {"title": "Teilzeitquote", "value": pct_points(statistics["part_time_share_pct"]["mean"]),
             "subtitle": f"{band('part_time_share_pct', pct_points)} · "
                         f"Ø {heads(statistics['part_time']['mean'])} Teilzeit-Mitarbeitende",
             "icon": "⏰", "status": "default"},
            largest_card,
        ]
    return [
        {"title": "Zugeordnete Kosten", "value": compact.format_currency(statistics["eur"]["mean"]),
         "subtitle": band("eur", compact.format_currency), "icon": "💰", "status": "good"},
        {"title": "Kosten pro Kopf", "value": compact.format_currency(statistics["eur_per_head"]["mean"]),
         "subtitle": band("eur_per_head", compact.format_currency), "icon": "👤", "status": "default"},
        {"title": "Kosten pro MAK", "value": compact.format_currency(statistics["eur_per_mak"]["mean"]),
         "subtitle": f"{band('eur_per_mak', compact.format_currency)} · "
                     f"Zugeordnet Ø {percent(statistics['mapped_eur_share']['mean'])}",
         "icon": "📊", "status": "default"},
        largest_card,
    ]


# ---------------------------------------------------------------------------
# Rangliste (uses display_orgs order from the top filters; values follow global metric)
# ---------------------------------------------------------------------------

def _render_org_rangliste(
    mapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
    display_orgs: list[str],
    value_label: str = "IST",
    export_tables: dict | None = None,
    monte_carlo_ensemble: dict | None = None,
    target_date=None,
    rank_statistics: dict | None = None,
) -> None:
    work_df = mapped_df[mapped_df[ORG_COL].isin(display_orgs)].copy()
    if "Is_Vacant" in work_df.columns:
        work_df = work_df[~work_df["Is_Vacant"]]

    if work_df.empty and rank_statistics is None:
        st.info("Keine auswertbaren Daten im aktuellen Filterkontext.")
        return

    if rank_statistics is not None:
        code = {"Köpfe": "heads", "MAK": "mak", "EUR": "eur"}[metric_view]
        agg = pd.Series({org: rank_statistics[code].get(org, {}).get("mean", 0.0)
                         for org in display_orgs}, dtype=float)
    elif metric_view == "Köpfe":
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

    # reindex to display_orgs — preserves selected filter order, fills gaps with 0
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
    if rank_statistics is not None:
        from utils.monte_carlo_ensemble import _statistics
        code = {"Köpfe": "heads", "MAK": "mak", "EUR": "eur"}[metric_view]
        zero = [0.0] * rank_statistics["count"]
        samples = {org: rank_statistics[code].get(org, {}).get("samples", zero)
                   for org in display_orgs}
        totals = [sum(samples[org][index] for org in display_orgs)
                  for index in range(rank_statistics["count"])]
        shares = {org: _statistics(
            [samples[org][index] / totals[index] if totals[index] else 0.0
             for index in range(rank_statistics["count"])],
            rank_statistics["band_level"]) for org in display_orgs}
        chart_df["P10"] = chart_df[ORG_COL].map(
            lambda org: rank_statistics[code].get(org, {}).get("lower", 0.0))
        chart_df["P90"] = chart_df[ORG_COL].map(
            lambda org: rank_statistics[code].get(org, {}).get("upper", 0.0))
        chart_df["Wert_Anzeige"] = chart_df.apply(
            lambda row: f"{row['Wert_Anzeige']} · P10–P90: "
                        f"{_format_metric_value(row['P10'], metric_view, compact)} bis "
                        f"{_format_metric_value(row['P90'], metric_view, compact)}", axis=1)
        chart_df["Anteil"] = chart_df[ORG_COL].map(
            lambda org: compact.format_percent(shares[org]["mean"]))
        chart_df["_upper_error"] = chart_df["P90"] - chart_df["Wert"]
        chart_df["_lower_error"] = chart_df["Wert"] - chart_df["P10"]

    if export_tables is not None:
        export_tables["Rangliste"] = (
            chart_df[[ORG_COL, "Wert", "P10", "P90", "Anteil"]].copy()
            if rank_statistics is not None else chart_df.copy())

    # Plotly Express maps y-axis category_orders to the visible top-to-bottom order
    # for horizontal bars. display_orgs is already sorted descending.
    chart_order = list(display_orgs)
    chart_height = max(420, min(1200, 28 * len(display_orgs) + 160))

    fig = px.bar(
        chart_df,
        x="Wert",
        y=ORG_COL,
        orientation="h",
        custom_data=["Wert_Anzeige", "Anteil"],
        category_orders={ORG_COL: chart_order},
        **({"error_x": "_upper_error", "error_x_minus": "_lower_error"}
           if rank_statistics is not None else {}),
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
    if rank_statistics is not None:
        fig.for_each_trace(lambda trace: trace.error_x.update(
            color="#6b7280", thickness=3, width=6))
    elif monte_carlo_ensemble is not None:
        from utils.monte_carlo_org_view import target_rank_band_stats
        from utils.monte_carlo_chart_values import overlay_ist_band
        band_stats = target_rank_band_stats(
            chart_df, ensemble=monte_carlo_ensemble, target_date=target_date,
            metric=metric_view)
        if band_stats is not None:
            fig = overlay_ist_band(
                fig, band_stats, metric="heads" if metric_view == "Köpfe" else "mak",
                dimension=ORG_COL)

    display_table = pd.DataFrame({
        ORG_COL: display_orgs,
        value_label: [_format_metric_value(float(agg[o]), metric_view, compact) for o in display_orgs],
        "Anteil": [
            compact.format_percent(float(agg[o]) / total) if total > 0 else "0,0%"
            for o in display_orgs
        ],
    })
    if rank_statistics is not None:
        display_table[value_label] = [
            _format_metric_value(float(agg[org]), metric_view, compact) for org in display_orgs]
        display_table["P10"] = [
            _format_metric_value(rank_statistics[code].get(org, {}).get("lower", 0.0),
                                 metric_view, compact) for org in display_orgs]
        display_table["P90"] = [
            _format_metric_value(rank_statistics[code].get(org, {}).get("upper", 0.0),
                                 metric_view, compact) for org in display_orgs]
        display_table["Anteil"] = [compact.format_percent(shares[org]["mean"])
                                   for org in display_orgs]

    col_chart, col_table = st.columns([3, 2])
    with col_chart:
        st.plotly_chart(fig, use_container_width=True)
    with col_table:
        dataframe_compat(display_table, width="stretch", hide_index=True)
        excel_df = pd.DataFrame({
            ORG_COL: display_orgs,
            value_label: [float(agg[o]) for o in display_orgs],
        })
        if rank_statistics is not None:
            excel_df["P10"] = [rank_statistics[code].get(org, {}).get("lower", 0.0)
                                  for org in display_orgs]
            excel_df["P90"] = [rank_statistics[code].get(org, {}).get("upper", 0.0)
                                  for org in display_orgs]
            excel_df["Anteil Mittelwert"] = [shares[org]["mean"] for org in display_orgs]
        lineage_ids = _org_lineage_ids(value_label, "9-14", "10-02")
        lazy_excel_download_button_compat(
            label="Excel Download",
            data_builder=lambda: compact.export_to_excel(
                excel_df,
                key_prefix="org_rangliste",
                dimension_name="Organisationseinheiten",
                value_type=metric_config["value_type"],
                table_title="Rangliste Organisationseinheiten",
                lineage_ids=lineage_ids,
            ),
            file_name="org_rangliste.xlsx",
            mime=compact._EXCEL_MIME,
            key="download_org_rangliste",
            fingerprint=dataframe_export_fingerprint(
                excel_df,
                "org_rangliste",
                metric_config["value_type"],
                tuple(lineage_ids),
            ),
            width="stretch",
        )


def _render_org_rangliste_comparison(
    mapped_df: pd.DataFrame,
    comparison_mapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
    display_orgs: list[str],
    *,
    value_label: str,
    comparison_label: str,
    departure_events: pd.DataFrame | None = None,
    export_tables: dict | None = None,
    rank_statistics: dict | None = None,
) -> None:
    comparison_df = _build_org_metric_comparison(
        mapped_df,
        comparison_mapped_df,
        metric_view,
        metric_config,
        display_orgs,
        value_label=value_label,
        comparison_label=comparison_label,
        departure_events=departure_events,
    )
    if comparison_df.empty:
        st.info("Keine auswertbaren Vergleichsdaten im aktuellen Filterkontext.")
        return

    if rank_statistics is not None:
        from utils.monte_carlo_ensemble import _statistics
        code = {"Köpfe": "heads", "MAK": "mak", "EUR": "eur"}[metric_view]
        band_level = rank_statistics["band_level"]
        count = rank_statistics["count"]
        zero = [0.0] * count
        for index, row in comparison_df.iterrows():
            org = str(row[ORG_COL])
            values = rank_statistics[code].get(org, {"mean": 0.0, "lower": 0.0,
                                                      "upper": 0.0, "samples": zero})
            baseline = float(row[comparison_label])
            comparison_df.loc[index, value_label] = values["mean"]
            comparison_df.loc[index, "Delta"] = values["mean"] - baseline
            comparison_df.loc[index, "Delta %"] = (
                (values["mean"] - baseline) / baseline if baseline else 0.0)
            comparison_df.loc[index, "P10"] = values["lower"]
            comparison_df.loc[index, "P90"] = values["upper"]
            comparison_df.loc[index, "Delta P10"] = values["lower"] - baseline
            comparison_df.loc[index, "Delta P90"] = values["upper"] - baseline
            ratio = _statistics([(value - baseline) / baseline if baseline else 0.0
                                 for value in values["samples"]], band_level)
            comparison_df.loc[index, "Delta % P10"] = ratio["lower"]
            comparison_df.loc[index, "Delta % P90"] = ratio["upper"]
            for source, label in (("departures", "Abgänge"), ("mak_loss", "MAK-Verlust")):
                driver = rank_statistics[source].get(org, {"mean": 0.0, "lower": 0.0,
                                                          "upper": 0.0})
                comparison_df.loc[index, label] = driver["mean"]
                comparison_df.loc[index, f"{label} P10"] = driver["lower"]
                comparison_df.loc[index, f"{label} P90"] = driver["upper"]

    if export_tables is not None:
        export_tables["Rangliste_Vergleich"] = comparison_df.copy()

    chart_df = comparison_df[[ORG_COL, comparison_label, value_label]].melt(
        id_vars=ORG_COL,
        var_name="Stand",
        value_name="Wert",
    )
    chart_df["Wert_Anzeige"] = chart_df["Wert"].apply(
        lambda value: _format_metric_value(float(value), metric_view, compact)
    )
    if rank_statistics is not None:
        limits = comparison_df.set_index(ORG_COL)[["P10", "P90"]]
        chart_df["Wert_Anzeige"] = chart_df.apply(
            lambda row: (row["Wert_Anzeige"] if row["Stand"] != value_label else
                         f"{row['Wert_Anzeige']} · P10–P90: "
                         f"{_format_metric_value(float(limits.loc[row[ORG_COL], 'P10']), metric_view, compact)} bis "
                         f"{_format_metric_value(float(limits.loc[row[ORG_COL], 'P90']), metric_view, compact)}"),
            axis=1)
        chart_df["_upper_error"] = chart_df.apply(
            lambda row: max(0.0, float(limits.loc[row[ORG_COL], "P90"]) - row["Wert"])
            if row["Stand"] == value_label else 0.0, axis=1)
        chart_df["_lower_error"] = chart_df.apply(
            lambda row: max(0.0, row["Wert"] - float(limits.loc[row[ORG_COL], "P10"]))
            if row["Stand"] == value_label else 0.0, axis=1)
    chart_order = list(display_orgs)
    chart_height = max(420, min(1200, 34 * len(display_orgs) + 180))

    fig = px.bar(
        chart_df,
        x="Wert",
        y=ORG_COL,
        color="Stand",
        orientation="h",
        barmode="group",
        custom_data=["Wert_Anzeige"],
        category_orders={ORG_COL: chart_order, "Stand": [comparison_label, value_label]},
        **({"error_x": "_upper_error", "error_x_minus": "_lower_error"}
           if rank_statistics is not None else {}),
    )
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>%{fullData.name}: %{customdata[0]}<extra></extra>"
    )
    fig.update_layout(
        height=chart_height,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title="",
        yaxis_title="",
        legend_title_text="Stand",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    apply_legend_bottom(fig)
    if rank_statistics is not None:
        fig.for_each_trace(lambda trace: trace.error_x.update(
            color="#6b7280", thickness=3, width=6))

    display_cols = [ORG_COL, comparison_label, value_label, "Delta", "Delta %"]
    if "Abgänge" in comparison_df.columns:
        display_cols.extend(["Abgänge", "MAK-Verlust"])
    if rank_statistics is not None:
        display_cols.extend(["P10", "P90", "Delta P10", "Delta P90",
                             "Delta % P10", "Delta % P90", "Abgänge P10", "Abgänge P90",
                             "MAK-Verlust P10", "MAK-Verlust P90"])
    display_table = _format_comparison_table(
        comparison_df[display_cols],
        metric_view,
        compact,
        value_columns=[comparison_label, value_label],
    )
    if rank_statistics is not None:
        for column in ("P10", "P90", "Delta P10", "Delta P90", "MAK-Verlust P10", "MAK-Verlust P90"):
            display_table[column] = display_table[column].map(
                lambda value: _format_metric_value(float(value),
                    "MAK" if column.startswith("MAK-Verlust") else metric_view, compact))
        for column in ("Delta % P10", "Delta % P90"):
            display_table[column] = display_table[column].map(
                lambda value: compact.format_percent(float(value)))

    col_chart, col_table = st.columns([3, 2])
    with col_chart:
        st.plotly_chart(fig, use_container_width=True)
    with col_table:
        dataframe_compat(display_table, width="stretch", hide_index=True)
        lineage_ids = ["10-03", "10-04", "10-07"]
        excel_df = comparison_df[display_cols]
        lazy_excel_download_button_compat(
            label="Excel Download",
            data_builder=lambda: compact.export_to_excel(
                excel_df,
                key_prefix="org_rangliste_comparison",
                dimension_name="Organisationseinheiten",
                value_type=metric_config["value_type"],
                table_title="Rangliste Organisationseinheiten Vergleich",
                lineage_ids=lineage_ids,
            ),
            file_name="org_rangliste_vergleich.xlsx",
            mime=compact._EXCEL_MIME,
            key="download_org_rangliste_comparison",
            fingerprint=dataframe_export_fingerprint(
                excel_df,
                "org_rangliste_comparison",
                metric_config["value_type"],
                tuple(lineage_ids),
            ),
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

    # category order = display_orgs order, consistent across all charts
    agg_df[ORG_COL] = pd.Categorical(
        agg_df[ORG_COL],
        categories=display_orgs,
        ordered=True,
    )
    agg_df = agg_df.sort_values([ORG_COL, split_col])
    return agg_df


def _build_split_comparison(
    mapped_df: pd.DataFrame,
    comparison_mapped_df: pd.DataFrame,
    split_col: str,
    metric_view: str,
    metric_config: dict[str, str],
    display_orgs: list[str],
    *,
    value_label: str,
    comparison_label: str,
) -> pd.DataFrame:
    group_cols = [ORG_COL, split_col]
    current = _aggregate_metric(mapped_df, group_cols, metric_view, metric_config).rename(columns={"Wert": value_label})
    previous = _aggregate_metric(comparison_mapped_df, group_cols, metric_view, metric_config).rename(columns={"Wert": comparison_label})
    out = previous.merge(current, on=group_cols, how="outer")
    if out.empty:
        return pd.DataFrame(columns=group_cols + [comparison_label, value_label, "Delta", "Delta %"])

    out[ORG_COL] = out[ORG_COL].fillna(ORG_NOT_ASSIGNED).astype(str)
    out[split_col] = out[split_col].fillna("(unbekannt)").astype(str)
    out = out[out[ORG_COL].isin(display_orgs)].copy()
    out[[comparison_label, value_label]] = out[[comparison_label, value_label]].fillna(0.0)
    out["Delta"] = out[value_label] - out[comparison_label]
    out["Delta %"] = out.apply(
        lambda row: row["Delta"] / row[comparison_label] if row[comparison_label] else 0.0,
        axis=1,
    )
    out[ORG_COL] = pd.Categorical(out[ORG_COL], categories=display_orgs, ordered=True)
    out = out.sort_values([ORG_COL, split_col])
    out[ORG_COL] = out[ORG_COL].astype(str)
    return out


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
    *,
    comparison_mapped_df: pd.DataFrame | None = None,
    comparison_active: bool = False,
    value_label: str = "Simulation",
    comparison_label: str = "IST",
    export_tables: dict | None = None,
    composition_view: dict | None = None,
    monte_carlo_band_level: float = 0.8,
) -> None:
    reference_agg = _aggregate_org_split(
        mapped_df, split_col, metric_view, metric_config, display_orgs)
    if composition_view is None:
        agg_df = reference_agg
    else:
        segments = composition_view["segments"]
        ref_values = {(str(org), str(group)): float(value)
                      for org, group, value in reference_agg[[ORG_COL, split_col, "Wert"]]
                      .itertuples(index=False, name=None)} if not reference_agg.empty else {}
        seed_values = {(str(org), str(group)): float(value)
                       for org, group, value in segments[[ORG_COL, split_col, "Seed 0"]]
                       .itertuples(index=False, name=None)} if not segments.empty else {}
        if (any(abs(value - seed_values.get(key, 0.0)) > 1e-6
                for key, value in ref_values.items())
                or any(abs(value) > 1e-6 for key, value in seed_values.items()
                       if key not in ref_values)):
            st.error(f"{title}: Die OE-Zusammensetzung weicht in Seed 0 vom Referenzlauf ab.")
            return
        agg_df = segments[[ORG_COL, split_col, "Wert", "P10", "P90", "samples"]].copy()
        agg_df[ORG_COL] = pd.Categorical(
            agg_df[ORG_COL], categories=display_orgs, ordered=True)
        agg_df = agg_df.sort_values([ORG_COL, split_col])
    if agg_df.empty:
        st.info("Keine auswertbaren Daten im aktuellen Filterkontext.")
        return

    pivot_df = _build_split_pivot(agg_df, split_col)
    if composition_view is not None:
        for bound in ("P10", "P90"):
            bounds = agg_df.pivot_table(
                index=ORG_COL, columns=split_col, values=bound,
                aggfunc="sum", fill_value=0, observed=False)
            for group in bounds.columns:
                pivot_df[f"{group} {bound}"] = pivot_df[ORG_COL].map(
                    bounds[group].astype(float).to_dict()).fillna(0.0)
            total_lookup = composition_view["totals"].set_index(ORG_COL)[bound]
            pivot_df[f"Gesamt {bound}"] = pivot_df[ORG_COL].map(total_lookup).fillna(0.0)

    if comparison_active and comparison_mapped_df is not None:
        comparison_agg = _aggregate_org_split(
            comparison_mapped_df,
            split_col,
            metric_view,
            metric_config,
            display_orgs,
        )
        current_chart = agg_df.copy()
        current_chart["Stand"] = value_label
        previous_chart = comparison_agg.copy()
        previous_chart["Stand"] = comparison_label
        chart_df = pd.concat([previous_chart, current_chart], ignore_index=True)
        chart_df[ORG_COL] = chart_df[ORG_COL].astype(str)
        chart_df["_Anzeige_OE"] = chart_df[ORG_COL] + " · " + chart_df["Stand"]

        if composition_view is None:
            comparison_df = _build_split_comparison(
                mapped_df, comparison_mapped_df, split_col, metric_view,
                metric_config, display_orgs, value_label=value_label,
                comparison_label=comparison_label)
        else:
            from utils.monte_carlo_ensemble import _statistics
            previous = (comparison_agg[[ORG_COL, split_col, "Wert"]].rename(
                columns={"Wert": comparison_label}) if not comparison_agg.empty
                else pd.DataFrame(columns=[ORG_COL, split_col, comparison_label]))
            current = agg_df[[ORG_COL, split_col, "Wert", "P10", "P90", "samples"]].rename(
                columns={"Wert": value_label})
            comparison_df = previous.merge(current, on=[ORG_COL, split_col], how="outer")
            comparison_df[ORG_COL] = comparison_df[ORG_COL].astype(str)
            comparison_df[split_col] = comparison_df[split_col].fillna("(unbekannt)").astype(str)
            for column in (comparison_label, value_label, "P10", "P90"):
                comparison_df[column] = pd.to_numeric(
                    comparison_df[column], errors="coerce").fillna(0.0)
            comparison_df["Delta"] = comparison_df[value_label] - comparison_df[comparison_label]
            comparison_df["Delta %"] = comparison_df.apply(
                lambda row: row["Delta"] / row[comparison_label]
                if row[comparison_label] else 0.0, axis=1)
            comparison_df["Delta P10"] = comparison_df["P10"] - comparison_df[comparison_label]
            comparison_df["Delta P90"] = comparison_df["P90"] - comparison_df[comparison_label]
            comparison_df["samples"] = comparison_df["samples"].map(
                lambda value: value if isinstance(value, list)
                else [0.0] * len(next(iter(current["samples"]), [])))
            ratio_bounds = comparison_df.apply(
                lambda row: _statistics(
                    [(value - row[comparison_label]) / row[comparison_label]
                     if row[comparison_label] else 0.0 for value in row["samples"]],
                    monte_carlo_band_level), axis=1)
            comparison_df["Delta % P10"] = ratio_bounds.map(lambda value: value["lower"])
            comparison_df["Delta % P90"] = ratio_bounds.map(lambda value: value["upper"])
            comparison_df = comparison_df.drop(columns="samples")
            comparison_df[ORG_COL] = pd.Categorical(
                comparison_df[ORG_COL], categories=display_orgs, ordered=True)
            comparison_df = comparison_df.sort_values([ORG_COL, split_col])
            comparison_df[ORG_COL] = comparison_df[ORG_COL].astype(str)
        display_df = _format_comparison_table(
            comparison_df,
            metric_view,
            compact,
            value_columns=[comparison_label, value_label],
        )
        if composition_view is not None:
            for column in ("P10", "P90", "Delta P10", "Delta P90"):
                display_df[column] = display_df[column].map(
                    lambda value: _format_metric_value(float(value), metric_view, compact))
            for column in ("Delta % P10", "Delta % P90"):
                display_df[column] = display_df[column].map(
                    lambda value: compact.format_percent(float(value)))
    else:
        chart_df = agg_df.copy()
        chart_df[ORG_COL] = chart_df[ORG_COL].astype(str)
        chart_df["_Anzeige_OE"] = chart_df[ORG_COL]
        display_df = _format_split_display(pivot_df, metric_view, compact)

    if export_tables is not None:
        export_tables[f"{title}_Grafik"] = chart_df.drop(
            columns=["samples"], errors="ignore").copy()
        export_tables[f"{title}_Tabelle"] = comparison_df.copy() if comparison_active and comparison_mapped_df is not None else pivot_df.reset_index()

    chart_df["Wert_Anzeige"] = chart_df["Wert"].apply(
        lambda value: _format_metric_value(float(value), metric_view, compact)
    )
    if composition_view is not None:
        chart_df["Wert_Anzeige"] = chart_df.apply(
            lambda row: row["Wert_Anzeige"]
            if comparison_active and row["Stand"] == comparison_label else
            f"{row['Wert_Anzeige']} · P10–P90: "
            f"{_format_metric_value(float(row['P10']), metric_view, compact)} bis "
            f"{_format_metric_value(float(row['P90']), metric_view, compact)}",
            axis=1)
    # Plotly Express maps y-axis category_orders to the visible top-to-bottom order
    # for horizontal bars. display_orgs is already sorted descending.
    if comparison_active and comparison_mapped_df is not None:
        chart_order = [
            f"{org} · {stand}"
            for org in display_orgs
            for stand in [comparison_label, value_label]
        ]
    else:
        chart_order = list(display_orgs)

    row_count = len(display_orgs) * (2 if comparison_active and comparison_mapped_df is not None else 1)
    chart_height = max(420, min(1400, 28 * row_count + 160))

    _cdm = None
    _cat_ord: dict = {ORG_COL: chart_order}
    if split_col == "Alterskohorte":
        _cohort_vals = chart_df[split_col].unique().tolist()
        _cdm = get_age_cohort_color_map(_cohort_vals)
        _known = [c for c in AGE_COHORT_ORDER if c in _cohort_vals]
        _unknown = sorted(c for c in _cohort_vals if c not in AGE_COHORT_ORDER)
        _cat_ord[split_col] = _known + _unknown
    elif split_col == "TrfGr":
        # Heller-zu-dunkler-Blau-Verlauf nach Entgeltgruppen-Hoehe (niedrige EG
        # hell, hohe EG dunkel), analog zur Alterskohorten-Farbskala oben.
        _trf_vals = chart_df[split_col].unique().tolist()
        _cdm = get_tariff_group_color_map(_trf_vals)
        _known = [g for g in TARIFF_GROUPS if g in _trf_vals]
        _unknown = sorted(v for v in _trf_vals if v not in TARIFF_GROUPS)
        _cat_ord[split_col] = _known + _unknown

    fig = px.bar(
        chart_df,
        x="Wert",
        y="_Anzeige_OE",
        color=split_col,
        orientation="h",
        barmode="stack",
        custom_data=["Wert_Anzeige"],
        color_discrete_map=_cdm,
        category_orders={**_cat_ord, "_Anzeige_OE": chart_order},
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
    if composition_view is not None:
        import plotly.graph_objects as go
        totals = composition_view["totals"].set_index(ORG_COL)
        band_positions = [
            f"{org} · {value_label}" if comparison_active else org
            for org in display_orgs]
        low = [float(totals.loc[org, "P10"]) for org in display_orgs]
        high = [float(totals.loc[org, "P90"]) for org in display_orgs]
        fig.add_trace(go.Scatter(
            x=[value for a, b in zip(low, high) for value in (a, b, None)],
            y=[value for org in band_positions for value in (org, org, None)],
            mode="lines", line=dict(color="rgba(107, 114, 128, 0.75)", width=7),
            name="P10–P90 Gesamt", hoverinfo="skip", legendgroup="mc_total"))
        for name, values, symbol in (("P10", low, "line-ns-open"),
                                     ("P90", high, "line-ns-open")):
            fig.add_trace(go.Scatter(
                x=values, y=band_positions, mode="markers", name=name,
                marker=dict(color="#6b7280", size=13, symbol=symbol),
                hovertemplate=f"<b>%{{y}}</b><br>{name}: %{{x:,.2f}}<extra></extra>",
                legendgroup="mc_total"))

    col_chart, col_table = st.columns([3, 2])
    with col_chart:
        st.plotly_chart(fig, use_container_width=True)
    with col_table:
        dataframe_compat(display_df, width="stretch", hide_index=True)
        right_export_df = (
            comparison_df
            if composition_view is not None and comparison_active and comparison_mapped_df is not None
            else pivot_df
        )
        lineage_ids = _org_lineage_ids(
            value_label,
            "9-16" if split_col == "TrfGr" else "9-15",
            "10-05",
        )
        lazy_excel_download_button_compat(
            label="Excel Download",
            data_builder=lambda: compact.export_to_excel(
                right_export_df,
                key_prefix=key_prefix,
                dimension_name=f"Organisationseinheit x {title}",
                value_type=metric_config["value_type"],
                table_title=f"Organisationseinheit nach {title}",
                lineage_ids=lineage_ids,
            ),
            file_name=f"{key_prefix}_{split_col.lower().replace(' ', '_')}.xlsx",
            mime=compact._EXCEL_MIME,
            key=f"download_{key_prefix}_{split_col}",
            fingerprint=dataframe_export_fingerprint(
                right_export_df,
                key_prefix,
                metric_config["value_type"],
                tuple(lineage_ids),
            ),
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Role breakdown block (Köpfe/MAK, folgt dem globalen Metrik-Switch — keine
# EUR-Werte. Steht global=EUR, weicht der Block auf MAK aus, siehe
# _resolve_role_metric().)
# ---------------------------------------------------------------------------

def _resolve_role_metric(metric_view: str, mapped_df: pd.DataFrame) -> tuple[str, dict[str, str] | None, bool]:
    """Koepfe/MAK direkt uebernehmen, EUR auf MAK abbilden (kein Geld in dieser Sektion).

    Gibt (effektive_metric_view, metric_config, ist_fallback) zurück.
    """
    normalized = normalize_global_metric_view(metric_view) or "MAK"
    is_fallback = normalized == "EUR"
    effective = "MAK" if is_fallback else normalized
    return effective, _get_metric_config(mapped_df, effective), is_fallback


def _build_role_summary_table(
    mapped_df: pd.DataFrame,
    display_orgs: list[str],
    compact,
    export_rows: list | None = None,
) -> pd.DataFrame:
    work = mapped_df[mapped_df[ORG_COL].isin(display_orgs)].copy()
    if "Is_Vacant" in work.columns:
        work = work[~work["Is_Vacant"]]

    mak_col = next(
        (c for c in ("MAK_Reporting", "MAK_Calculated", "MAK") if c in work.columns), None
    )
    id_col = next((c for c in ("PersNr", "Personalnummer") if c in work.columns), None)

    if work.empty:
        return pd.DataFrame()

    rows = []
    for oe in display_orgs:
        sub = work[work[ORG_COL] == oe]
        koepfe = int(sub[id_col].nunique()) if id_col else len(sub)
        mak = float(sub[mak_col].sum()) if mak_col else 0.0
        if export_rows is not None:
            export_rows.append({ORG_COL: oe, "Köpfe": koepfe, "MAK": mak, "Ø MAK": mak / koepfe if koepfe else None})
        rows.append({
            ORG_COL: oe,
            "Köpfe": koepfe,
            "MAK": compact.format_number(mak, 1),
            "Ø MAK": compact.format_number(mak / koepfe, 2) if koepfe > 0 else "–",
        })
    return pd.DataFrame(rows)


def _build_monte_carlo_role_summary_table(
    display_orgs: list[str], rank_statistics: dict, compact,
    export_rows: list | None = None,
) -> pd.DataFrame:
    from utils.monte_carlo_filtered_org import _statistics

    rows = []
    for oe in display_orgs:
        head = rank_statistics["heads"].get(oe)
        mak = rank_statistics["mak"].get(oe)
        count = rank_statistics["count"]
        heads = head["samples"] if head else [0.0] * count
        maks = mak["samples"] if mak else [0.0] * count
        ratios = [capacity / persons if persons > 0 else None
                  for persons, capacity in zip(heads, maks)]
        ratio_stats = (_statistics(ratios, rank_statistics["band_level"])
                       if all(value is not None for value in ratios) else None)
        head_stats = _statistics(heads, rank_statistics["band_level"])
        mak_stats = _statistics(maks, rank_statistics["band_level"])
        raw = {
            ORG_COL: oe,
            "Köpfe": head_stats["mean"], "Köpfe P10": head_stats["lower"],
            "Köpfe P90": head_stats["upper"],
            "MAK": mak_stats["mean"], "MAK P10": mak_stats["lower"],
            "MAK P90": mak_stats["upper"],
            "Ø MAK": ratio_stats["mean"] if ratio_stats else None,
            "Ø MAK P10": ratio_stats["lower"] if ratio_stats else None,
            "Ø MAK P90": ratio_stats["upper"] if ratio_stats else None,
        }
        if export_rows is not None:
            export_rows.append(raw)
        rows.append({ORG_COL: oe, **{
            key: compact.format_number(value, 2 if key.startswith("Ø") else 1)
            if value is not None else "–"
            for key, value in raw.items() if key != ORG_COL}})
    return pd.DataFrame(rows)


def _render_role_breakdown_block(
    mapped_df: pd.DataFrame,
    metric_view: str,
    compact,
    display_orgs: list[str],
    *,
    comparison_mapped_df: pd.DataFrame | None = None,
    comparison_active: bool = False,
    value_label: str = "Simulation",
    comparison_label: str = "IST",
    export_tables: dict | None = None,
    composition_view: dict | None = None,
    rank_statistics: dict | None = None,
    monte_carlo_band_level: float = 0.8,
) -> None:
    has_tarifgruppe = "TrfGr" in mapped_df.columns

    if not has_tarifgruppe:
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

    _render_org_split_block(
        mapped_df, "Tarifgruppe", "TrfGr",
        role_metric_view, role_metric_config, compact,
        key_prefix="org_role_trf", display_orgs=display_orgs,
        comparison_mapped_df=comparison_mapped_df,
        comparison_active=comparison_active,
        value_label=value_label,
        comparison_label=comparison_label,
        composition_view=composition_view,
        monte_carlo_band_level=monte_carlo_band_level,
            **({"export_tables": export_tables} if export_tables is not None else {}),
    )

    # Summary table: Köpfe, MAK, Ø MAK per OE — keine Euro-Werte
    st.divider()
    st.subheader("Kopfzahl und Kapazität pro Organisationseinheit")
    raw_rows = [] if export_tables is not None else None
    if rank_statistics is None:
        summary_df = _build_role_summary_table(mapped_df, display_orgs, compact, **({"export_rows": raw_rows} if raw_rows is not None else {}))
    else:
        summary_df = _build_monte_carlo_role_summary_table(
            display_orgs, rank_statistics, compact, export_rows=raw_rows)
        if any(any(value <= 0 for value in rank_statistics["heads"].get(oe, {}).get(
                "samples", [0.0] * rank_statistics["count"])) for oe in display_orgs):
            st.caption("Ø MAK ist nur definiert, wenn in jedem Lauf mindestens eine Person in der OE vorhanden ist.")
    if export_tables is not None:
        export_tables["Köpfe_Kapazität"] = pd.DataFrame(raw_rows)
    if not summary_df.empty:
        dataframe_compat(summary_df, width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Data quality block (based on full filtered_df — not limited by top_n)
# ---------------------------------------------------------------------------

def _monte_carlo_data_quality(ensemble, active_filters, metric_view, compact,
                              reference):
    """Calculate data-quality counts and ratios inside every saved run."""
    from components.sidebar import _apply_filters_uncached
    from utils.monte_carlo_ensemble import _statistics
    from utils.monte_carlo_filtered_org import decode_snapshot

    blobs = ensemble.get("org_filter_snapshots")
    if not isinstance(blobs, list) or len(blobs) != ensemble.get("count"):
        raise ValueError("OE-Laufdaten für die Datenqualität fehlen")
    runs = []
    for blob in blobs:
        frame = _normalize_org_column(
            _apply_filters_uncached(decode_snapshot(blob), dict(active_filters or {})))
        unmapped = frame.loc[frame[ORG_COL].eq(ORG_NOT_ASSIGNED)]
        mapped = frame.loc[frame[ORG_COL].ne(ORG_NOT_ASSIGNED)]
        total_metric = _get_metric_total(frame, metric_view, compact)
        unmapped_metric = _get_metric_total(unmapped, metric_view, compact)
        mapped_metric = _get_metric_total(mapped, metric_view, compact)
        runs.append({
            "Sichtbare Zeilen": len(frame),
            "Nicht zugeordnete Zeilen": len(unmapped),
            "Nicht zugeordnete MA": len(get_unique_employees(unmapped)) if not unmapped.empty else 0,
            "Gesamtkennzahl": total_metric,
            "Nicht zugeordnete Kennzahl": unmapped_metric,
            "Nicht zugeordneter Anteil": unmapped_metric / total_metric if total_metric > 0 else 0.0,
            "Zugeordneter Anteil": mapped_metric / total_metric if total_metric > 0 else 0.0,
            "Nicht zugeordnete Zeilenquote": len(unmapped) / len(frame) if len(frame) else 0.0,
        })
    for key, expected in reference.items():
        if abs(float(runs[0][key]) - float(expected)) > 1e-6:
            raise ValueError(f"Datenqualität ({key}) weicht vom Referenzlauf ab")
    return {key: _statistics([float(run[key]) for run in runs], ensemble["band_level"])
            for key in runs[0]}


def _render_data_quality_block(
    filtered_df: pd.DataFrame,
    mapped_df: pd.DataFrame,
    unmapped_df: pd.DataFrame,
    metric_view: str,
    metric_config: dict[str, str],
    compact,
    export_tables: dict | None = None,
    monte_carlo_ensemble: dict | None = None,
    active_filters: dict | None = None,
) -> None:
    total_rows = len(filtered_df)
    unmapped_rows = len(unmapped_df)
    total_metric = _get_metric_total(filtered_df, metric_view, compact)
    unmapped_metric = _get_metric_total(unmapped_df, metric_view, compact)
    unmapped_share = unmapped_metric / total_metric if total_metric > 0 else 0
    unmapped_employees = len(get_unique_employees(unmapped_df)) if not unmapped_df.empty else 0
    mapped_share = _get_metric_total(mapped_df, metric_view, compact) / total_metric if total_metric > 0 else 0

    mc = None
    if monte_carlo_ensemble is not None:
        try:
            mc = _monte_carlo_data_quality(
                monte_carlo_ensemble, active_filters, metric_view, compact, {
                    "Sichtbare Zeilen": total_rows,
                    "Nicht zugeordnete Zeilen": unmapped_rows,
                    "Nicht zugeordnete MA": unmapped_employees,
                    "Gesamtkennzahl": total_metric,
                    "Nicht zugeordnete Kennzahl": unmapped_metric,
                    "Nicht zugeordneter Anteil": unmapped_share,
                    "Zugeordneter Anteil": mapped_share,
                    "Nicht zugeordnete Zeilenquote": unmapped_rows / total_rows if total_rows else 0.0,
                })
        except ValueError as exc:
            st.error(f"Monte-Carlo-Datenqualität nicht verfügbar: {exc}")
            return
        total_rows = mc["Sichtbare Zeilen"]["mean"]
        unmapped_rows = mc["Nicht zugeordnete Zeilen"]["mean"]
        unmapped_employees = mc["Nicht zugeordnete MA"]["mean"]
        total_metric = mc["Gesamtkennzahl"]["mean"]
        unmapped_metric = mc["Nicht zugeordnete Kennzahl"]["mean"]
        unmapped_share = mc["Nicht zugeordneter Anteil"]["mean"]
        mapped_share = mc["Zugeordneter Anteil"]["mean"]

    if export_tables is not None:
        row = {
            "Sichtbare Zeilen": total_rows, "Nicht zugeordnete Zeilen": unmapped_rows,
            "Nicht zugeordnete MA": unmapped_employees, "Gesamtkennzahl": total_metric,
            "Nicht zugeordnete Kennzahl": unmapped_metric, "Nicht zugeordneter Anteil": unmapped_share,
            "Zugeordneter Anteil": mapped_share}
        if mc is not None:
            for key, values in mc.items():
                row[f"{key} P10"] = values["lower"]
                row[f"{key} P90"] = values["upper"]
        export_tables["Datenqualität"] = pd.DataFrame([row])

    if unmapped_rows == 0:
        st.success("Alle sichtbaren Datensätze sind einer Organisationseinheit zugeordnet.")
        st.caption("Nicht zugeordnete Datensätze haben im aktuellen Filterkontext keine Wirkung.")
        return

    if mc is not None:
        st.caption("Mittelwerte aller Läufe; P10–P90 je Kennzahl und Anteil im Excel-Export.")

    compact.render_kpi_cards_styled(
        [
            {
                "title": "Nicht zugeordnete Zeilen",
                "value": compact.format_number(unmapped_rows, 1 if mc is not None else 0),
                "subtitle": (f"{compact.format_percent(mc['Nicht zugeordnete Zeilenquote']['mean'])} der sichtbaren Datensätze"
                             f" · P10–P90: {compact.format_number(mc['Nicht zugeordnete Zeilen']['lower'], 1)} bis "
                             f"{compact.format_number(mc['Nicht zugeordnete Zeilen']['upper'], 1)} Zeilen"
                             if mc is not None else
                             f"{compact.format_percent(unmapped_rows / total_rows) if total_rows > 0 else '0,0%'} der sichtbaren Datensätze"),
                "icon": "🧩",
                "status": "warning",
            },
            {
                "title": "Nicht zugeordnete MA",
                "value": compact.format_number(unmapped_employees, 1 if mc is not None else 0),
                "subtitle": (f"P10–P90: {compact.format_number(mc['Nicht zugeordnete MA']['lower'], 1)} bis "
                             f"{compact.format_number(mc['Nicht zugeordnete MA']['upper'], 1)} Mitarbeitende"
                             if mc is not None else "Unique Mitarbeitende ohne Organisationseinheit"),
                "icon": "👥",
                "status": "warning" if unmapped_employees > 0 else "good",
            },
            {
                "title": "Einfluss auf Kennzahl",
                "value": _format_metric_value(unmapped_metric, metric_view, compact),
                "subtitle": (f"{compact.format_percent(unmapped_share)} der sichtbaren Kennzahl · "
                             f"P10–P90: {_format_metric_value(mc['Nicht zugeordnete Kennzahl']['lower'], metric_view, compact)} "
                             f"bis {_format_metric_value(mc['Nicht zugeordnete Kennzahl']['upper'], metric_view, compact)}"
                             if mc is not None else f"{compact.format_percent(unmapped_share)} der sichtbaren Kennzahl"),
                "icon": "⚠️",
                "status": "warning" if unmapped_metric > 0 else "good",
            },
            {
                "title": "Zugeordneter Anteil",
                "value": compact.format_percent(mapped_share),
                "subtitle": (f"P10–P90: {compact.format_percent(mc['Zugeordneter Anteil']['lower'])} bis "
                             f"{compact.format_percent(mc['Zugeordneter Anteil']['upper'])}"
                             if mc is not None else "Anteil der Hauptanalyse am sichtbaren Gesamtvolumen"),
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
# Page rendering
# ---------------------------------------------------------------------------

def render_orgunit_analysis_page(
    prepared_df: pd.DataFrame,
    history_df: pd.DataFrame | None,
    *,
    title: str = "Organisationseinheiten-Analyse",
    subtitle: str = "IST-Sicht auf die aktuell sichtbare Personalsituation nach Organisationseinheiten.",
    value_label: str = "IST",
    methodology_text: str | None = None,
    comparison_df: pd.DataFrame | None = None,
    comparison_label: str = "IST",
    enable_comparison_toggle: bool = False,
    departure_events_df: pd.DataFrame | None = None,
    export_context: dict | None = None,
    event_summary: dict | None = None,
    monte_carlo_ensemble: dict | None = None,
) -> None:
    compact = load_compact_page_module()
    export_tables = {} if export_context is not None else None

    render_page_header(
        title,
        subtitle,
    )
    if monte_carlo_ensemble is not None:
        st.info(
            f"{monte_carlo_ensemble['count']} Läufe: Zielbestand, Rangliste, "
            "Zusammensetzung und Jahresereignisse zeigen Mittelwerte und P10–P90. "
            "Die globalen Personenfilter wirken auf diese Mehrlaufwerte. "
            "Der Jahresbestand aus der Bestandsüberleitung bleibt ungefiltert; "
            "Referenzlauf-Auswertungen sind gesondert gekennzeichnet."
        )

    set_metric_page_hint(
        f"{title} nutzt den globalen Metrik-Switch direkt für KPIs, Rangliste und Detailblöcke."
    )

    yearly_view = None

    def render_yearly_view():
        if export_tables is None:
            return None
        from components.org_simulation_yearly import render_org_simulation_yearly
        return render_org_simulation_yearly(
            event_summary, run_signature=export_context.get("Laufkennung"),
            monte_carlo_ensemble=monte_carlo_ensemble)

    prepared_df = _normalize_org_column(prepared_df)
    comparison_prepared_df = None
    if comparison_df is not None and not comparison_df.empty:
        comparison_prepared_df = _normalize_org_column(comparison_df)
    history_for_filters = history_df if history_df is not None else pd.DataFrame()

    if monte_carlo_ensemble is None:
        render_global_filters(prepared_df, history_for_filters)
    else:
        from utils.monte_carlo_filter_options import filter_options_from_ensemble
        render_global_filters(
            prepared_df, history_for_filters,
            option_values=filter_options_from_ensemble(monte_carlo_ensemble))
    filtered_df = apply_filters(prepared_df)
    comparison_filtered_df = (
        apply_filters(comparison_prepared_df)
        if comparison_prepared_df is not None
        else None
    )
    filtered_departure_events = _filter_departure_events(departure_events_df) if departure_events_df is not None else pd.DataFrame()
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

    def finish_export(display_orgs):
        if export_tables is None:
            return
        stock_view = yearly_view.get("stock_view") if yearly_view is not None else None
        render_org_simulation_export(
            export_tables, export_context, current=filtered_df, baseline=comparison_filtered_df,
            event_summary=event_summary, yearly_view=yearly_view,
            stock_view=stock_view, monte_carlo_ensemble=monte_carlo_ensemble,
            display_orgs=display_orgs, compact=compact, view={
                "Kennzahl": metric_view, "Filter": get_active_view_filters(), "Filterübersicht": filter_summary,
                "Top-N": st.session_state.get(_TOP_N_SESSION_KEY, _TOP_N_DEFAULT),
                "Sortierung": st.session_state.get(_SORT_SESSION_KEY, _SORT_DEFAULT),
                "Mindestgröße": st.session_state.get(_MIN_SIZE_SESSION_KEY, _MIN_SIZE_DEFAULT),
                "Simulationsfokus": st.session_state.get(_SIM_FOCUS_SESSION_KEY, _SIM_FOCUS_DEFAULT),
                "IST-Vergleich": comparison_active, "IST verfügbar": comparison_filtered_df is not None,
            },
        )

    # --- KPI block (always uses full mapped_df) ---
    render_section_intro(
        "KPI-Überblick",
        f"Kennzahl: {metric_view} · aktueller Filterkontext",
    )
    if monte_carlo_ensemble is None:
        kpis = _build_kpis(mapped_df, filtered_df, unmapped_df, metric_view, compact)
    else:
        from utils.monte_carlo_filtered_org import filtered_org_kpi_statistics
        try:
            mc_kpis = filtered_org_kpi_statistics(
                monte_carlo_ensemble, get_active_view_filters(), compact)
        except ValueError as exc:
            st.error(f"Monte-Carlo-Kennzahlen fehlen: {exc}. Bitte Simulation neu berechnen.")
            return
        kpis = _build_monte_carlo_kpis(mc_kpis, metric_view, compact)
    if export_tables is not None:
        export_tables["KPI_Anzeige"] = pd.DataFrame(kpis).reindex(columns=["title", "value", "subtitle", "status"])
    if kpis:
        compact.render_kpi_cards_styled(kpis)
    comparison_active = False
    comparison_mapped_df = None
    if comparison_filtered_df is not None:
        comparison_filtered_df = _normalize_org_column(comparison_filtered_df)
        comparison_mapped_df = comparison_filtered_df[comparison_filtered_df[ORG_COL] != ORG_NOT_ASSIGNED].copy()
    comparison_available = enable_comparison_toggle and comparison_mapped_df is not None

    mc_rank_stats = None
    if monte_carlo_ensemble is not None:
        from utils.monte_carlo_filtered_org import filtered_org_rank_statistics
        try:
            mc_rank_stats = filtered_org_rank_statistics(
                monte_carlo_ensemble, get_active_view_filters())
        except ValueError as exc:
            st.error(f"Monte-Carlo-Rangliste fehlt: {exc}. Bitte Simulation neu berechnen.")
            return
        # Fail visibly if the app-specific Seed-0 data have drifted from the
        # concrete reference that the user approved.
        for view_name, code in (("Köpfe", "heads"), ("MAK", "mak"), ("EUR", "eur")):
            config = _get_metric_config(mapped_df, view_name)
            if config is None:
                continue
            reference = _aggregate_metric(mapped_df, [ORG_COL], view_name, config)
            for org, value in reference[[ORG_COL, "Wert"]].itertuples(index=False, name=None):
                seed_zero = mc_rank_stats[code].get(str(org), {}).get("seed_zero", 0.0)
                if abs(float(value) - seed_zero) > 1e-6:
                    st.error(f"OE-Rangliste für {org} ({view_name}) weicht vom Referenzlauf ab.")
                    return

    if mapped_df.empty and (mc_rank_stats is None or not any(
            value["mean"] > 0 for org, value in mc_rank_stats["heads"].items()
            if org != ORG_NOT_ASSIGNED)):
        render_context_box(
            "Keine zugeordneten Organisationseinheiten im aktuellen Ausschnitt",
            "Im aktuellen Filterkontext sind keine zugeordneten Organisationseinheiten sichtbar. Die fachliche Hauptanalyse bleibt deshalb leer; der Datenqualitätsblock unten zeigt den verbleibenden Rest transparent an.",
            tone="warning",
        )
        _render_data_quality_block(filtered_df, mapped_df, unmapped_df, metric_view, metric_config, compact,
                                   export_tables=export_tables, monte_carlo_ensemble=monte_carlo_ensemble,
                                   active_filters=get_active_view_filters())
        yearly_view = render_yearly_view()
        finish_export([])
        return

    # --- Top filters ---
    st.divider()
    if enable_comparison_toggle and comparison_mapped_df is None:
        st.caption("IST-Vergleich nicht verfügbar, weil kein Vergleichs-Snapshot vorliegt.")

    ctrl_cols = st.columns([3, 2, 2, 2, 2] if comparison_available else [3, 2, 2])
    with ctrl_cols[0]:
        selected_top_n: str = st.radio(
            "Anzahl Organisationseinheiten",
            _TOP_N_OPTIONS,
            index=_get_widget_index(_TOP_N_OPTIONS, st.session_state.get(_TOP_N_SESSION_KEY), _TOP_N_DEFAULT),
            horizontal=True,
            key=_TOP_N_SESSION_KEY,
        )
    if comparison_available:
        with ctrl_cols[4]:
            comparison_active = st.toggle(
                f"{comparison_label}-Vergleich anzeigen",
                value=False,
                key="orgunit_analysis_show_comparison",
            )

    sort_options = _SORT_OPTIONS_BASE.copy()
    if comparison_active:
        sort_options.extend(_SORT_OPTIONS_COMPARISON)

    current_sort = st.session_state.get(_SORT_SESSION_KEY, _SORT_DEFAULT)
    if current_sort not in sort_options:
        st.session_state[_SORT_SESSION_KEY] = _SORT_DEFAULT
    current_focus = st.session_state.get(_SIM_FOCUS_SESSION_KEY, _SIM_FOCUS_DEFAULT)
    if current_focus not in _SIM_FOCUS_OPTIONS:
        st.session_state[_SIM_FOCUS_SESSION_KEY] = _SIM_FOCUS_DEFAULT

    sort_col_index = 1
    min_size_col_index = 2
    sim_focus_col_index = 3

    with ctrl_cols[sort_col_index]:
        selected_sort: str = st.selectbox(
            "Sortierung",
            sort_options,
            index=_get_widget_index(sort_options, st.session_state.get(_SORT_SESSION_KEY), _SORT_DEFAULT),
            key=_SORT_SESSION_KEY,
        )
    with ctrl_cols[min_size_col_index]:
        selected_min_size: str = st.selectbox(
            "Mindestgröße",
            _MIN_SIZE_OPTIONS,
            index=_get_widget_index(_MIN_SIZE_OPTIONS, st.session_state.get(_MIN_SIZE_SESSION_KEY), _MIN_SIZE_DEFAULT),
            key=_MIN_SIZE_SESSION_KEY,
        )
    if comparison_active:
        with ctrl_cols[sim_focus_col_index]:
            selected_sim_focus: str = st.selectbox(
                "Simulationsfokus",
                _SIM_FOCUS_OPTIONS,
                index=_get_widget_index(
                    _SIM_FOCUS_OPTIONS,
                    st.session_state.get(_SIM_FOCUS_SESSION_KEY),
                    _SIM_FOCUS_DEFAULT,
                ),
                key=_SIM_FOCUS_SESSION_KEY,
            )
    else:
        selected_sim_focus = _SIM_FOCUS_DEFAULT

    caption_parts = ["Die Auswahl steuert Rangliste und Zusammensetzung. Die Jahresauswertung hat eigene Organisations- und Jahresfilter."]
    if comparison_active:
        caption_parts.append("Mindestgröße bezieht sich auf den größeren Wert aus IST und Simulation.")
        caption_parts.append("Delta = absolute Veränderung der aktuell gewählten Kennzahl.")
    st.caption(" ".join(caption_parts))

    # --- Central display list (shared by all charts and tables on this page) ---
    ranking_df = _build_orgunit_ranking_frame(
        mapped_df,
        comparison_mapped_df,
        filtered_departure_events,
        metric_view,
        metric_config,
        comparison_active=comparison_active,
        value_label=value_label,
        comparison_label=comparison_label,
    )
    if mc_rank_stats is not None:
        ranking_df = _apply_monte_carlo_ranking_values(
            ranking_df, mc_rank_stats, comparison_mapped_df, metric_view,
            metric_config, comparison_label, comparison_active)
    display_orgs = _apply_orgunit_top_filters(
        ranking_df,
        selected_top_n,
        selected_sort,
        metric_view,
        selected_min_size,
        selected_sim_focus,
        comparison_active=comparison_active,
    )

    if not display_orgs:
        st.info("Im aktuellen Filterkontext sind keine Organisationseinheiten für die Analyse verfügbar.")
        _render_data_quality_block(filtered_df, mapped_df, unmapped_df, metric_view, metric_config, compact,
                                   export_tables=export_tables, monte_carlo_ensemble=monte_carlo_ensemble,
                                   active_filters=get_active_view_filters())
        yearly_view = render_yearly_view()
        finish_export([])
        return

    # --- Rangliste (display_orgs order follows selected sort; values follow global metric) ---
    sort_label = _resolve_sort_metric(selected_sort, metric_view)
    render_section_intro(
        "Rangliste der Organisationseinheiten",
        f"Sortiert nach {sort_label}. Werte gemäß aktuell gewählter Kennzahl.",
    )
    if comparison_active and comparison_mapped_df is not None:
        _render_org_rangliste_comparison(
            mapped_df,
            comparison_mapped_df,
            metric_view,
            metric_config,
            compact,
            display_orgs,
            value_label=value_label,
            comparison_label=comparison_label,
            departure_events=filtered_departure_events,
            rank_statistics=mc_rank_stats,
            **({"export_tables": export_tables} if export_tables is not None else {}),
        )
    else:
        _render_org_rangliste(
            mapped_df, metric_view, metric_config, compact, display_orgs,
            value_label=value_label,
            monte_carlo_ensemble=monte_carlo_ensemble if filtered_df.equals(prepared_df) else None,
            target_date=event_summary.get("metadata", {}).get("target_date") if event_summary else None,
            rank_statistics=mc_rank_stats,
            **({"export_tables": export_tables} if export_tables is not None else {}))

    # --- Zusammensetzung (all three blocks use same display_orgs) ---
    if selected_top_n == "Alle":
        composition_caption = "Alle Organisationseinheiten im aktuellen Filterkontext."
    else:
        composition_caption = f"Top-{selected_top_n}-Organisationseinheiten im aktuellen Filterkontext."

    render_section_intro(
        "Zusammensetzung der Top-Organisationseinheiten",
        composition_caption,
    )
    mc_composition = None
    mc_tariff_composition = None
    if monte_carlo_ensemble is not None:
        from utils.monte_carlo_filtered_org import filtered_org_composition_statistics
        try:
            dimensions = tuple(split_col for _, split_col in DETAIL_BLOCKS)
            if "TrfGr" in mapped_df.columns and metric_view != "EUR":
                dimensions += ("TrfGr",)
            mc_composition = filtered_org_composition_statistics(
                monte_carlo_ensemble, get_active_view_filters(), display_orgs,
                metric_view, dimensions)
            if "TrfGr" in mapped_df.columns:
                mc_tariff_composition = (mc_composition["TrfGr"] if metric_view != "EUR"
                    else filtered_org_composition_statistics(
                        monte_carlo_ensemble, get_active_view_filters(), display_orgs,
                        "MAK", ("TrfGr",))["TrfGr"])
        except ValueError as exc:
            st.error(f"Monte-Carlo-Zusammensetzung fehlt: {exc}. Bitte Simulation neu berechnen.")
            return
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
            comparison_mapped_df=comparison_mapped_df,
            comparison_active=comparison_active,
            value_label=value_label,
            comparison_label=comparison_label,
            composition_view=mc_composition[split_col] if mc_composition is not None else None,
            monte_carlo_band_level=monte_carlo_ensemble["band_level"] if monte_carlo_ensemble else 0.8,
            **({"export_tables": export_tables} if export_tables is not None else {}),
        )

    # --- Personalstruktur nach Tarifgruppe (folgt dem globalen Metrik-Switch, keine EUR-Werte) ---
    st.divider()
    render_section_intro(
        "Personalstruktur nach Tarifgruppe",
        "Köpfe bzw. MAK pro Organisationseinheit. Folgt der globalen Darstellungsart (Köpfe/MAK).",
    )
    _render_role_breakdown_block(
        mapped_df,
        metric_view,
        compact,
        display_orgs,
        comparison_mapped_df=comparison_mapped_df,
        comparison_active=comparison_active,
        value_label=value_label,
        comparison_label=comparison_label,
        composition_view=mc_tariff_composition,
        rank_statistics=mc_rank_stats,
        monte_carlo_band_level=monte_carlo_ensemble["band_level"] if monte_carlo_ensemble else 0.8,
    **({"export_tables": export_tables} if export_tables is not None else {}),
    )

    if export_tables is not None:
        st.divider()
        yearly_view = render_yearly_view()

    if comparison_active:
        if monte_carlo_ensemble is None:
            reason_df = _build_departure_reason_summary(filtered_departure_events)
        else:
            from utils.monte_carlo_filtered_org import filtered_org_departure_reasons
            reason_df = filtered_org_departure_reasons(
                monte_carlo_ensemble, get_active_view_filters())
            if not reason_df.empty:
                reason_df = reason_df.loc[reason_df[ORG_COL].isin(display_orgs)].copy()
        if not reason_df.empty:
            if export_tables is not None:
                export_tables["Abgänge_nach_Grund"] = reason_df.copy()
            with st.expander("Simulierte Abgänge nach Grund", expanded=False):
                dataframe_compat(reason_df, width="stretch", hide_index=True)

    # --- Datenqualität (always full filtered_df, never limited by top_n) ---
    with st.expander("Datenqualität", expanded=False):
        _render_data_quality_block(filtered_df, mapped_df, unmapped_df, metric_view, metric_config, compact,
                                   export_tables=export_tables, monte_carlo_ensemble=monte_carlo_ensemble,
                                   active_filters=get_active_view_filters())

    finish_export(display_orgs)

    with st.expander("Hinweise zur Methodik", expanded=False):
        if methodology_text:
            st.markdown(methodology_text)
        else:
            st.markdown(
                "Die Seite zeigt eine IST-Analyse der aktuell sichtbaren Personalsituation. "
                "Die globale Kennzahl aus der Sidebar steuert KPI-Header, Rangliste und Detailblöcke.\n\n"
                "Die Hauptanalyse zeigt zugeordnete Organisationseinheiten. Nicht zugeordnete Datensätze werden "
                "im Datenqualitätsblock separat ausgewiesen.\n\n"
                "Die angezeigten Organisationseinheiten werden standardmäßig nach der aktuell gewählten Kennzahl "
                "sortiert. Optional kann explizit nach Köpfen, MAK, Delta oder Abgängen sortiert werden. "
                "Der Regler \"Anzahl Organisationseinheiten\" steuert, wie viele Organisationseinheiten in "
                "Grafiken und Tabellen angezeigt werden. Die Kennzahl aus der Sidebar steuert die dargestellten Werte.\n\n"
                "Filter und Exklusionen aus der Sidebar definieren den Betrachtungsraum."
            )


def main() -> None:
    compact = load_compact_page_module()
    snapshot_df, history_df, _, _ = load_and_prepare_data(show_status_messages=False)
    prepared_df = compact.prepare_compact_data(snapshot_df)

    render_orgunit_analysis_page(
        prepared_df,
        history_df,
        title="Organisationseinheiten-Analyse",
        subtitle="IST-Sicht auf die aktuell sichtbare Personalsituation nach Organisationseinheiten.",
        value_label="IST",
    )


if __name__ == "__main__":
    main()
