"""
Streamlit page: Abgänge Prognose.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Dict
import sys
import os
import re
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

# Add project root or src to path
BASE_PATH = Path(__file__).resolve().parents[1]
SRC_PATH = BASE_PATH / "src"
if SRC_PATH.exists():
    sys.path.append(str(SRC_PATH))
else:
    sys.path.append(str(BASE_PATH))

from kpi_reference import get_current_stichtag  # Import dynamic Stichtag
from config.settings import COLORS, TARIFF_GROUPS

from abgaenge import (
    default_params as default_abgaenge_params,
    build_params_from_ui as build_abgaenge_params_from_ui,
    run_forecast_abgaenge,
    aggregate_forecast_results,
    validate_outputs,
    build_charts as build_abgaenge_charts,
    to_csv_bytes,
)
from zugaenge.params import default_params as default_zugaenge_params, get_strategies
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events, render_zugaenge_debug_sections

# Shared Components
from dataloader.loader import load_and_prepare_data, load_atz_data_cached, calculate_mak_vectorized, calculate_cost_vectorized
from dataloader.cluster_manager import load_cluster_mappings_from_source, resolve_forecast_ui_dimensions
from dataloader.cluster_resolver import (
    deserialize_active_cluster_source,
    get_active_cluster_source,
    invalidate_cluster_dependent_state,
)
from components.sidebar import (
    apply_event_filters,
    apply_event_filters_with_state,
    filter_dataframe_by_view_filters,
    get_active_view_filters,
    render_filter_status,
    render_global_filters,
    set_metric_page_hint,
)
from utils.i18n import t
from utils.plot_helpers import apply_legend_bottom
from utils.ui_helpers import render_distribution_matrix, render_orgunit_mode_hint
from utils.matrix_helpers import migrate_to_percent, percent_to_weights

_HYBRID_ZUG_REASON_LABELS = {
    "Azubi_Hire": "Azubi Neueinstellung (externer Zugang)",
    "Azubi_Conversion_Out": "Azubi-Abschluss (Statuswechsel: Ende Azubi)",
    "Azubi_Conversion_In": "Übernahme nach Ausbildungsabschluss (interne MAK-wirksame Stellenbesetzung)",
    "Azubi_Exit": "Azubi-Abschluss: Nichtübernahme (Abgang)",
}


def _get_hybrid_cluster_context(summary: Optional[dict[str, Any]]):
    summary = summary or {}
    active_cluster_source = deserialize_active_cluster_source(summary.get("active_cluster_source"))
    if active_cluster_source is None:
        active_cluster_source = get_active_cluster_source(session_state=st.session_state)

    cluster_source_signature = (
        summary.get("active_cluster_source_signature")
        or getattr(active_cluster_source, "source_signature", None)
    )
    cluster_mapping_bundle = load_cluster_mappings_from_source(active_cluster_source)
    cluster_is_active = bool(cluster_mapping_bundle.oe_map or cluster_mapping_bundle.jf_map)
    return active_cluster_source, cluster_mapping_bundle, cluster_source_signature, cluster_is_active


def _cluster_widget_key(base_key: str, cluster_source_signature: Optional[str]) -> str:
    suffix = str(cluster_source_signature or "no_cluster_source").strip() or "no_cluster_source"
    return f"{base_key}_{suffix}"


def _get_active_dimension_values(
    df_ma: pd.DataFrame,
    active_cluster_source,
    cluster_mapping_bundle,
):
    return resolve_forecast_ui_dimensions(
        df_ma,
        active_cluster_source,
        mapping_bundle=cluster_mapping_bundle,
    )

def _hybrid_results_match_cluster_signature(current_cluster_source_signature: Optional[str]) -> bool:
    if "hybrid_abg_res" not in st.session_state or "hybrid_zug_res" not in st.session_state:
        return False
    return st.session_state.get("hybrid_cluster_source_signature") == current_cluster_source_signature


def calculate_kpi_from_events(df_start_stats: pd.DataFrame, events_df: pd.DataFrame, start_date: pd.Timestamp, end_date: pd.Timestamp, freq: str) -> pd.DataFrame:
    """
    Calculates KPI time-series strictly from initial state + event deltas.
    Ensures 100% consistency with event-based charts.
    """
    # 1. Start Stats
    start_hc = df_start_stats["active"].sum() if "active" in df_start_stats.columns else len(df_start_stats)
    start_mak = df_start_stats["mak"].sum() if "mak" in df_start_stats.columns else start_hc

    # 2. Periods
    periods = pd.period_range(start=start_date, end=end_date, freq=freq)
    
    # 3. Aggregate Events by Period
    kpi_data = []
    
    current_hc = start_hc
    current_mak = start_mak
    
    # Pre-calculate deltas
    # Resample events to period end
    if not events_df.empty:
        # Copy to avoid mutation
        ev = events_df.copy()
        ev["period"] = ev["event_date"].dt.to_period(freq)
        
        # Group by period
        grp = ev.groupby("period")[["headcount_change", "mak_change"]].sum()
    else:
        grp = pd.DataFrame()

    for p in periods:
        delta_hc = 0
        delta_mak = 0.0
        
        if not grp.empty and p in grp.index:
            delta_hc = int(grp.loc[p, "headcount_change"])
            delta_mak = float(grp.loc[p, "mak_change"])
            
        current_hc += delta_hc
        current_mak += delta_mak
        
        kpi_data.append({
            "period_end": p.end_time,
            "headcount_end": current_hc,
            "mak_end": current_mak,
            "headcount_delta": delta_hc,
            "mak_delta": delta_mak,
            "mak_start": start_mak # Constant ref for debug
        })
        
    return pd.DataFrame(kpi_data)


def _check_sum(label: str, chart_sum: float, ref_sum: float, unit: str = "Pers", tolerance: float = 0.1):
    """Helper to render a debug metric comparing Chart vs Reference."""
    diff = abs(chart_sum - ref_sum)
    is_ok = diff <= tolerance
    icon = "✅" if is_ok else "❌"
    color = "green" if is_ok else "red"
    
    st.markdown(
        f"**{icon} {label}**<br>"
        f"Chart: `{chart_sum:.1f}` | Ref: `{ref_sum:.1f}` | Diff: `:{color}[{diff:.1f}]`",
        unsafe_allow_html=True
    )

def _get_unique_count(df: pd.DataFrame) -> int:
    """Robustly count unique persons, handling missing/renamed columns."""
    try:
        # Priority list of possible ID columns
        candidates = ["PersNr", "persnr", "ID", "id", "EmployeeID", "Personalnummer"]
        for col in candidates:
            if col in df.columns:
                return df[col].nunique()
        
        # Fallback if no ID found
        st.warning("⚠️ Debug: Kein Personen-Identifier (PersNr) gefunden.")
        return 0
    except Exception as e:
        # Never crash debug
        return 0


def _render_debug_aggregation(df: pd.DataFrame, group_cols: list[str], label: str, count_col: str = "count", mak_col: str = "mak_change", top_n: int = 20, key_prefix: str = ""):
    """
    Renders a standardized debug aggregation table with granular counts.
    """
    st.markdown(f"**{label}**")
    
    if df.empty:
        st.caption("Keine Daten verfügbar.")
        return

    try:
        # Check if columns exist
        missing = [c for c in group_cols if c not in df.columns]
        if missing:
            st.error(f"Fehlende Spalten für Aggregation: {missing}")
            return

        # 1. Fill NaNs to avoid dropping rows in groupby
        df_clean = df.copy()
        for c in group_cols:
            df_clean[c] = df_clean[c].fillna("Sonstiges").replace(r"^\s*$", "Sonstiges", regex=True).replace("Unclustered", "Sonstiges")

        # 2. Granular Aggregation
        # Rows: Count of records
        # HC Events: Count where headcount_change != 0 (abs > 0)
        # MAK Events: Count where mak_change != 0 (abs > 0)
        
        # Pre-calc masks
        df_clean["_hc_event"] = (df_clean[count_col].abs() > 0).astype(int) if count_col in df_clean.columns else 0
        df_clean["_mak_event"] = (df_clean[mak_col].abs() > 0).astype(int) if mak_col in df_clean.columns else 0
        
        agg_setup = {
            "Rows": ("persnr", "size"), # PersNr just as dummy column for size
            "HC Events": ("_hc_event", "sum"),
            "MAK Events": ("_mak_event", "sum"),
        }
        
        # Add Value Sums
        if count_col in df_clean.columns:
            agg_setup["Delta Köpfe"] = (count_col, "sum")
        if mak_col in df_clean.columns:
            agg_setup["Delta MAK"] = (mak_col, "sum")
            
        # Groupby
        grouped = df_clean.groupby(group_cols, as_index=False).agg(**agg_setup)

        # 3. Sorting (Impact)
        # Sort by absolute MAK change if available, else HC change, else Rows
        if "Delta MAK" in grouped.columns:
            grouped["_sort"] = grouped["Delta MAK"].abs()
        elif "Delta Köpfe" in grouped.columns:
            grouped["_sort"] = grouped["Delta Köpfe"].abs()
        else:
            grouped["_sort"] = grouped["Rows"]
            
        grouped = grouped.sort_values("_sort", ascending=False).drop(columns=["_sort"])
        
        # 4. Display Logic
        rows_total = len(grouped)
        if rows_total > top_n:
             show_all = st.checkbox(f"Alle {rows_total} Zeilen anzeigen ({label})", key=f"{key_prefix}tog_{label}_{group_cols[-1]}")
             if not show_all:
                 grouped = grouped.head(top_n)
                 st.caption(f"Zeige Top {top_n} von {rows_total}")
        
        st.dataframe(grouped, use_container_width=True, hide_index=True)
        
    except Exception as e:
        st.error(f"Fehler in Aggregation ({label}): {e}")


def _has_effective_hybrid_filters(active_filters: dict) -> bool:
    return any(
        [
            bool(active_filters.get("selected_org_units")),
            bool(active_filters.get("selected_jobfamilies")),
            bool(active_filters.get("selected_oe_clusters")),
            bool(active_filters.get("selected_jf_clusters")),
            bool(active_filters.get("selected_cohorts")),
            bool(active_filters.get("selected_education")),
            set(active_filters.get("selected_genders", ["m", "w"])) != {"m", "w"},
            set(active_filters.get("selected_employment", ["Vollzeit", "Teilzeit", "Inaktiv"])) != {"Vollzeit", "Teilzeit", "Inaktiv"},
            set(active_filters.get("selected_atz_status", ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"])) != {"Kein ATZ", "Arbeitsphase", "Freistellungsphase"},
        ]
    )


def _build_hybrid_zug_reason_labels(event_types: pd.Series, sources: pd.Series) -> pd.Series:
    type_labels = event_types.astype(str).map(_HYBRID_ZUG_REASON_LABELS)
    source_labels = sources.astype(str)
    return type_labels.where(type_labels.notna(), "Zugang (" + source_labels + ")")


def _build_hybrid_vacancies_from_events(
    abg_events: pd.DataFrame,
    df_ma: pd.DataFrame,
) -> list[dict[str, Any]]:
    exits = abg_events[abg_events["headcount_change"] < 0]
    if exits.empty:
        return []

    donor_lookup = (
        df_ma[["PersNr", "Jobfamily", "OE-Cluster"]]
        .copy()
        .assign(_lookup_key=lambda frame: frame["PersNr"])
        .drop_duplicates("_lookup_key", keep="first")
    )

    enriched_exits = exits.copy()
    enriched_exits["_lookup_key"] = enriched_exits["persnr"].astype(str).str.strip().str.replace(".0", "", regex=False)
    enriched_exits = enriched_exits.merge(
        donor_lookup[["_lookup_key", "Jobfamily", "OE-Cluster"]].rename(
            columns={
                "Jobfamily": "_donor_jobfamily",
                "OE-Cluster": "_donor_oe_cluster",
            }
        ),
        on="_lookup_key",
        how="left",
        sort=False,
        indicator=True,
    )
    matched_donor = enriched_exits["_merge"].eq("both")
    org_unit_series = (
        enriched_exits["Organisationseinheit"]
        if "Organisationseinheit" in enriched_exits.columns
        else pd.Series("Unbekannt", index=enriched_exits.index, dtype="object")
    )
    planstelle_series = (
        enriched_exits["Planstelle"]
        if "Planstelle" in enriched_exits.columns
        else pd.Series("Unbekannt", index=enriched_exits.index, dtype="object")
    )

    vacancy_df = pd.DataFrame(
        {
            "date": enriched_exits["event_date"],
            "org_unit": org_unit_series.where(org_unit_series.notna(), "Unbekannt"),
            "planstelle": planstelle_series.where(planstelle_series.notna(), "Unbekannt"),
            "persnr": enriched_exits["persnr"],
            "Jobfamily": enriched_exits["_donor_jobfamily"].where(matched_donor, "Angestellte"),
            "OE-Cluster": enriched_exits["_donor_oe_cluster"].where(matched_donor, "Sonstiges"),
        }
    )
    return vacancy_df.to_dict("records")


@st.cache_data
def _prepare_hybrid_employee_snapshot(
    snapshot_df: pd.DataFrame,
    df_atz: pd.DataFrame,
    *,
    current_stichtag: pd.Timestamp,
) -> pd.DataFrame:
    """Build the employee-level snapshot once per unique base dataset and ATZ state."""
    df_ma = snapshot_df.dropna(subset=["PersNr"]).copy()

    atz_fr_persnr_set = set()
    if not df_atz.empty and {"PersNr", "Phase", "Beginn", "Ende"}.issubset(df_atz.columns):
        atz_fr = df_atz[
            (df_atz["Phase"] == "FR") &
            (df_atz["Beginn"] <= current_stichtag) &
            (df_atz["Ende"] >= current_stichtag)
        ]
        if not atz_fr.empty:
            atz_fr_persnr_set = set(atz_fr["PersNr"].dropna().astype(str).unique())

    df_ma = calculate_mak_vectorized(df_ma, atz_fr_persnr_set)

    agg_dict = {
        "MAK_Calculated": "sum",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Sollarbeitszeit": "sum",
        "Organisationseinheit": "first",
    }
    for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St", "OE-Cluster", "JF-Cluster", "BsGrd"]:
        if col in df_ma.columns:
            agg_dict[col] = "first"

    df_employee_agg = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
    df_employee_agg["mak"] = df_employee_agg["MAK_Calculated"]
    df_employee_agg["Sollarbeitszeit"] = df_employee_agg["Sollarbeitszeit"].fillna(39.0)
    df_employee_agg["Sollarbeitszeit"] = 39.0

    mask_zero = df_employee_agg["mak"] <= 0
    if mask_zero.any() and "BsGrd" in df_employee_agg.columns:
        potential_mak = df_employee_agg.loc[mask_zero, "BsGrd"] / 100.0
        df_employee_agg.loc[mask_zero, "mak"] = potential_mak.fillna(0.0)
        df_employee_agg.loc[mask_zero, "MAK_Calculated"] = df_employee_agg.loc[mask_zero, "mak"]

    df_employee_agg["BsGrd"] = df_employee_agg["mak"] * 100.0
    return df_employee_agg


@st.cache_data
def _build_hybrid_distribution_base(df_ma: pd.DataFrame, resolved_dimensions: dict) -> pd.DataFrame:
    if resolved_dimensions.get("is_source_backed"):
        base = pd.DataFrame(columns=["Jobfamily", "OE-Cluster", "Count"])
        if {"JF-Cluster", "OE-Cluster"}.issubset(df_ma.columns):
            base = (
                df_ma.groupby(["JF-Cluster", "OE-Cluster"])
                .size()
                .reset_index(name="Count")
                .rename(columns={"JF-Cluster": "Jobfamily"})
            )

        job_family_clusters = list(resolved_dimensions.get("job_family_clusters", []) or [])
        oe_clusters = list(resolved_dimensions.get("oe_clusters", []) or [])
        if job_family_clusters and oe_clusters:
            combinations = pd.MultiIndex.from_product(
                [job_family_clusters, oe_clusters],
                names=["Jobfamily", "OE-Cluster"],
            ).to_frame(index=False)
            base = combinations.merge(base, on=["Jobfamily", "OE-Cluster"], how="left")
            base["Count"] = base["Count"].fillna(0.0)

        total_n = float(base["Count"].sum()) if "Count" in base.columns and not base.empty else 0.0
        base["Share %"] = (base["Count"] / total_n).round(4) if total_n > 0 else 0.0
        return base[["Jobfamily", "OE-Cluster", "Share %"]]

    dist_base = df_ma.groupby(["Jobfamily", "OE-Cluster"]).size().reset_index(name="Count")
    dist_base["Share %"] = (dist_base["Count"] / dist_base["Count"].sum()).round(4)
    return dist_base.sort_values(["Count", "Jobfamily", "OE-Cluster"], ascending=[False, True, True]).reset_index(drop=True)


@st.cache_data
def _prepare_hybrid_view_state(
    raw_abg_events: pd.DataFrame,
    raw_zug_events: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    df_ma: pd.DataFrame,
    active_filters: dict,
    *,
    ist_stichtag: pd.Timestamp,
    forecast_end_date: pd.Timestamp,
    freq_label: str,
    base_abg_kpis: Optional[pd.DataFrame] = None,
) -> dict:
    has_effective_filters = _has_effective_hybrid_filters(active_filters)

    if has_effective_filters:
        filt_abg_events, n_abg_before, n_abg_after = apply_event_filters_with_state(
            raw_abg_events,
            snapshot_df,
            active_filters=active_filters,
            mode="attrition",
        )
    else:
        filt_abg_events = raw_abg_events.copy()
        n_abg_before = len(raw_abg_events)
        n_abg_after = len(filt_abg_events)

    abg_cols = [
        "period_label", "period_start", "period_end", "event_date", "persnr",
        "reason_code", "reason_label", "headcount_change", "mak_change",
        "Organisationseinheit", "Jobfamily", "OE-Cluster",
    ]
    if any(c not in filt_abg_events.columns for c in ["persnr", "headcount_change", "Organisationseinheit"]):
        for c in abg_cols:
            if c not in filt_abg_events.columns:
                filt_abg_events[c] = pd.NaT if "date" in c or "_start" in c or "_end" in c else None

    if "event_date" in filt_abg_events.columns:
        filt_abg_events["event_date"] = pd.to_datetime(filt_abg_events["event_date"])
    filt_abg_events["source_view"] = "Abgang_Detail"

    if "OE-Cluster" not in filt_abg_events.columns or filt_abg_events["OE-Cluster"].isna().all():
        pm_lookup = df_ma.set_index("PersNr")
        pm_lookup.index = pm_lookup.index.astype(str).str.replace(r"\.0$", "", regex=True)
        filt_abg_events["_pid_clean"] = filt_abg_events["persnr"].astype(str).str.replace(r"\.0$", "", regex=True)
        cluster_map = pm_lookup["OE-Cluster"].to_dict() if "OE-Cluster" in pm_lookup.columns else {}
        jf_map = pm_lookup["Jobfamily"].to_dict() if "Jobfamily" in pm_lookup.columns else {}
        filt_abg_events["OE-Cluster"] = filt_abg_events["_pid_clean"].map(cluster_map).fillna("Sonstiges")
        if "Jobfamily" not in filt_abg_events.columns or filt_abg_events["Jobfamily"].isna().all():
            filt_abg_events["Jobfamily"] = filt_abg_events["_pid_clean"].map(jf_map).fillna("Unbekannt")
        filt_abg_events = filt_abg_events.drop(columns=["_pid_clean"], errors="ignore")

    if has_effective_filters:
        df_snapshot_filtered = filter_dataframe_by_view_filters(df_ma, active_filters)
    else:
        df_snapshot_filtered = df_ma.copy()

    if "org_unit" in raw_zug_events.columns and "Organisationseinheit" not in raw_zug_events.columns:
        raw_zug_events = raw_zug_events.rename(columns={"org_unit": "Organisationseinheit"})
    elif "org_unit" in raw_zug_events.columns and "Organisationseinheit" in raw_zug_events.columns:
        raw_zug_events = raw_zug_events.drop(columns=["org_unit"])
    if has_effective_filters:
        filt_zug_events, n_zug_before, n_zug_after = apply_event_filters_with_state(
            raw_zug_events,
            snapshot_df,
            active_filters=active_filters,
            mode="accession",
        )
    else:
        filt_zug_events = raw_zug_events.copy()
        n_zug_before = len(raw_zug_events)
        n_zug_after = len(filt_zug_events)

    if "date" in filt_zug_events.columns:
        filt_zug_events["date"] = pd.to_datetime(filt_zug_events["date"])
        filt_zug_events = filt_zug_events[
            (filt_zug_events["date"] >= pd.Timestamp(ist_stichtag)) &
            (filt_zug_events["date"] <= pd.Timestamp(forecast_end_date))
        ].copy()

    zug_cols = ["date", "type", "count", "persnr", "Organisationseinheit", "source", "mak", "Jobfamily", "OE-Cluster", "TrfGr", "St", "Planstelle"]
    if any(c not in filt_zug_events.columns for c in ["count", "source", "mak"]):
        for c in zug_cols:
            if c not in filt_zug_events.columns:
                filt_zug_events[c] = pd.NaT if c == "date" else None

    if "date" in filt_zug_events.columns:
        filt_zug_events["date"] = pd.to_datetime(filt_zug_events["date"])
    filt_zug_events = filt_zug_events[filt_zug_events["count"] != 0].copy()
    filt_zug_events = filt_zug_events.loc[:, ~filt_zug_events.columns.duplicated()]

    filt_zug_events_std = filt_zug_events.copy()
    filt_zug_events_std["event_date"] = pd.to_datetime(filt_zug_events_std["date"])
    filt_zug_events_std["mak_change"] = filt_zug_events_std["mak"]
    filt_zug_events_std["headcount_change"] = filt_zug_events_std["count"]
    if filt_zug_events_std.empty:
        filt_zug_events_std["reason_label"] = pd.Series(index=filt_zug_events_std.index, dtype="float64")
    else:
        filt_zug_events_std["reason_label"] = _build_hybrid_zug_reason_labels(
            filt_zug_events_std["type"],
            filt_zug_events_std["source"],
        )
    filt_zug_events_std["source_view"] = "Zugang_Detail"

    combined_events = pd.concat([filt_abg_events, filt_zug_events_std], ignore_index=True)
    start_ts = pd.Timestamp(ist_stichtag)
    end_ts = pd.Timestamp(forecast_end_date)
    combined_events_in_scope = combined_events[
        (combined_events["event_date"] >= start_ts) &
        (combined_events["event_date"] <= end_ts)
    ].copy()

    if not combined_events_in_scope.empty:
        if "event_uid" not in combined_events_in_scope.columns:
            combined_events_in_scope["event_uid"] = (
                combined_events_in_scope["event_date"].apply(lambda x: str(pd.Timestamp(x).date())) + "|" +
                combined_events_in_scope["persnr"].astype(str) + "|" +
                combined_events_in_scope["reason_label"].astype(str) + "|" +
                combined_events_in_scope["headcount_change"].astype(str) + "|" +
                combined_events_in_scope["source_view"].astype(str)
            )

        combined_events_in_scope["is_headcount_exit_any"] = combined_events_in_scope["headcount_change"] < 0
        combined_events_in_scope["is_headcount_exit_detail"] = (
            (combined_events_in_scope["headcount_change"] < 0) &
            (combined_events_in_scope["source_view"] == "Abgang_Detail")
        )

        reason_labels = combined_events_in_scope["reason_label"].astype(str)
        combined_events_in_scope["is_headcount_exit_bank"] = (
            (combined_events_in_scope["headcount_change"] < 0)
            & ~reason_labels.str.contains("Statuswechsel|Conversion_Out|Ruhend|Ruhephase", na=False, regex=True)
        )

        mask_hc0 = combined_events_in_scope["headcount_change"] == 0
        mask_atz_ar_fr = combined_events_in_scope["reason_label"].str.contains("AR → FR", na=False)
        bad_atz_mask = mask_hc0 & mask_atz_ar_fr & (combined_events_in_scope["mak_change"] == 0)
        if bad_atz_mask.any():
            combined_events_in_scope.loc[bad_atz_mask, "mak_change"] = -1.0

    for _df in [combined_events_in_scope, filt_zug_events]:
        if "OE-Cluster" in _df.columns:
            _mask = _df["OE-Cluster"].isna() | (_df["OE-Cluster"] == "Unclustered")
            _df.loc[_mask, "OE-Cluster"] = "Sonstiges"
        if "JF-Cluster" in _df.columns:
            _mask = _df["JF-Cluster"].isna() | (_df["JF-Cluster"] == "Unclustered")
            _df.loc[_mask, "JF-Cluster"] = "Sonstiges"

    if df_snapshot_filtered.empty:
        df_view_agg = pd.DataFrame(columns=["PersNr", "MAK_Calculated", "mak", "active"])
        net_kpis = pd.DataFrame()
        abg_view_kpis = pd.DataFrame()
        zug_view_kpis = pd.DataFrame()
    else:
        view_agg_dict = {
            "MAK_Calculated": "sum",
            "Organisationseinheit": "first",
            "Jobfamily": "first",
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Sollarbeitszeit": "sum",
        }
        for col in ["Geschlecht", "Planstelle", "OE-Cluster", "JF-Cluster", "TrfGr"]:
            if col in df_snapshot_filtered.columns:
                view_agg_dict[col] = "first"

        df_view_agg = df_snapshot_filtered.groupby("PersNr", as_index=False).agg(view_agg_dict)
        df_view_agg["mak"] = df_view_agg["MAK_Calculated"]
        df_view_agg["active"] = True

        agg_freq = "M" if freq_label == t("hybrid.settings.frequency.month") else "Q"
        net_kpis = calculate_kpi_from_events(
            df_start_stats=df_view_agg,
            events_df=combined_events_in_scope,
            start_date=pd.Timestamp(ist_stichtag),
            end_date=pd.Timestamp(forecast_end_date),
            freq=agg_freq,
        )
        if not has_effective_filters and base_abg_kpis is not None and not base_abg_kpis.empty:
            abg_view_kpis = base_abg_kpis.copy()
        else:
            abg_view_kpis = aggregate_forecast_results(
                df_initial=df_view_agg,
                events_df=filt_abg_events,
                start_date=pd.Timestamp(ist_stichtag),
                end_date=pd.Timestamp(forecast_end_date),
                freq=agg_freq,
                params=None,
            )
        zug_view_kpis = aggregate_forecast_results(
            df_initial=df_view_agg,
            events_df=filt_zug_events_std,
            start_date=pd.Timestamp(ist_stichtag),
            end_date=pd.Timestamp(forecast_end_date),
            freq=agg_freq,
            params=None,
        )

    return {
        "filt_abg_events": filt_abg_events,
        "filt_zug_events": filt_zug_events,
        "filt_zug_events_std": filt_zug_events_std,
        "combined_events_in_scope": combined_events_in_scope,
        "df_snapshot_filtered": df_snapshot_filtered,
        "df_view_agg": df_view_agg,
        "net_kpis": net_kpis,
        "abg_view_kpis": abg_view_kpis,
        "zug_view_kpis": zug_view_kpis,
        "n_abg_before": n_abg_before,
        "n_abg_after": n_abg_after,
        "n_zug_before": n_zug_before,
        "n_zug_after": n_zug_after,
    }


@st.cache_data
def _build_hybrid_netto_chart_sources(combined_events_in_scope: pd.DataFrame) -> dict:
    df_ts = pd.DataFrame({"date": pd.to_datetime(combined_events_in_scope["event_date"])})
    df_ts["month"] = df_ts["date"].dt.to_period("M").astype(str)
    df_ts["type"] = combined_events_in_scope["type"]
    df_ts["count"] = combined_events_in_scope["headcount_change"]

    mask_ext = df_ts["type"].isin(["Azubi_Hire", "Trainee_Hire", "New_Hire"])
    df_ext = df_ts[mask_ext].groupby(["month", "type"])["count"].sum().reset_index()
    type_map_ext = {
        "Azubi_Hire": "Neue Auszubildende (externer Zugang)",
        "Trainee_Hire": "Trainee (Extern)",
        "New_Hire": "Neueinstellung (Extern)",
    }
    df_ext["Kategorie"] = df_ext["type"].map(type_map_ext)

    mask_conv = df_ts["type"].isin(["Azubi_Conversion_In"])
    if mask_conv.any():
        df_conv = df_ts[mask_conv].groupby(["month"])["count"].sum().reset_index()
        df_conv["Kategorie"] = "Übernahme aus Ausbildung (interne Stellenbesetzung, MAK-wirksam)"
    else:
        df_conv = pd.DataFrame(columns=["month", "count", "Kategorie"])

    if combined_events_in_scope.empty:
        driver_agg = pd.DataFrame(columns=["JahrMonat", "reason_label", "mak_change"])
    else:
        events_for_drivers = combined_events_in_scope.copy()
        events_for_drivers["event_date"] = pd.to_datetime(events_for_drivers["event_date"])
        events_for_drivers["JahrMonat"] = events_for_drivers["event_date"].dt.to_period("M").astype(str)
        driver_agg = events_for_drivers.groupby(["JahrMonat", "reason_label"])["mak_change"].sum().reset_index()

    return {
        "df_ext": df_ext,
        "df_conv": df_conv,
        "driver_agg": driver_agg,
    }


@st.cache_data
def _build_hybrid_abgaenge_chart_bundle(
    abg_view_kpis: pd.DataFrame,
    filt_abg_events: pd.DataFrame,
) -> dict:
    from abgaenge.visuals import build_charts as build_abgaenge_charts

    return build_abgaenge_charts(abg_view_kpis, filt_abg_events)


@st.cache_data
def _build_hybrid_abgaenge_cluster_source(filt_abg_events: pd.DataFrame) -> pd.DataFrame:
    return (
        filt_abg_events[filt_abg_events["headcount_change"] < 0]
        .groupby("OE-Cluster")
        .size()
        .reset_index(name="Abgänge")
    )


@st.cache_data
def _build_hybrid_zugaenge_chart_sources(filt_zug_events: pd.DataFrame) -> dict:
    valid_types = ["Azubi_Hire", "Azubi_Conversion_In", "New_Hire", "Trainee_Hire"]
    if not filt_zug_events.empty:
        events_chart = filt_zug_events[filt_zug_events["type"].isin(valid_types)].copy()
    else:
        events_chart = pd.DataFrame()

    if not events_chart.empty:
        label_map = {
            "Azubi_Hire": "Neue Auszubildende",
            "Azubi_Conversion_In": "Übernahme aus Ausbildung",
            "New_Hire": "Neueinstellung",
            "Trainee_Hire": "Trainee",
        }
        events_chart["Quelle"] = events_chart["type"].map(label_map)

    if "OE-Cluster" in filt_zug_events.columns:
        z_stats = filt_zug_events.groupby("OE-Cluster").size().reset_index(name="Zugänge")
    else:
        z_stats = pd.DataFrame(columns=["OE-Cluster", "Zugänge"])

    return {
        "events_chart": events_chart,
        "z_stats": z_stats,
    }


def main():
    def get_filter_bundle():
        """Aggregates all relevant UI filters for stable hashing."""
        return {
            "org": st.session_state.get("selected_org_units", []),
            "jf": st.session_state.get("selected_jobfamilies", []),
            "gender": st.session_state.get("selected_genders", []),
            "empl": st.session_state.get("selected_employment", []),
            "atz": st.session_state.get("selected_atz_status", []),
        }

    def dict_hash(d):
        """Creates a stable hash of a dictionary."""
        return json.dumps(d, sort_keys=True, default=str)

    current_filter_hash = dict_hash(get_filter_bundle())

    st.title(t("hybrid.title"))
    st.caption(t("hybrid.subtitle"))
    set_metric_page_hint(
        t("hybrid.metric_hint")
    )

    try:
        # 1. Load Central Data (Consistent with Kompakt)
        # Loader automatically picks up 'global_uploads' from session_state (see loader.py)
        snapshot_df, history_df, _, summary = load_and_prepare_data()
        (
            active_cluster_source,
            cluster_mapping_bundle,
            cluster_source_signature,
            cluster_is_active,
        ) = _get_hybrid_cluster_context(summary)

        # 2. Render Sidebar Filters (Standard Dashboard Logic)
        render_global_filters(snapshot_df, history_df)
        
        # 3. Load ATZ Details (needed for engine phases)
        # Get uploads from session_state for specific loading
        global_uploads = st.session_state.get("global_uploads", {})
        up_ma_arg = global_uploads.get("Mitarbeiter")
        up_atz_arg = global_uploads.get("ATZ")
        up_pl_arg = global_uploads.get("Planstellen")
        
        if up_ma_arg: up_ma_arg.seek(0)
        if up_atz_arg: up_atz_arg.seek(0)
        if up_pl_arg: up_pl_arg.seek(0)
        
        df_atz = load_atz_data_cached(str(BASE_PATH), up_ma_arg, up_atz_arg, up_pl_arg)
        
        # 4. Preprocessing for Forecast Engine (GLOBAL)
        df_ma = _prepare_hybrid_employee_snapshot(
            snapshot_df,
            df_atz,
            current_stichtag=pd.Timestamp(get_current_stichtag()),
        )
        resolved_dimensions = _get_active_dimension_values(
            df_ma,
            active_cluster_source,
            cluster_mapping_bundle,
        )
        active_org_units = resolved_dimensions.org_units
        active_jobfamily_values = resolved_dimensions.job_family_clusters
        active_oe_clusters = resolved_dimensions.oe_clusters

    except FileNotFoundError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(f"Fehler beim Laden/Filtern der Daten: {e}")
        return
    
    # df_ma is now the GLOBAL Aggregated Dataset.

    default_start = get_current_stichtag().date()
    default_end = date(default_start.year + 2, default_start.month, default_start.day)

    # ── Parametrierung (kompakt) ──
    params_abg = default_abgaenge_params()
    params_zug = default_zugaenge_params()

    # ── Settings Accordion ──────────────────────────────────────────
    with st.expander(t("hybrid.settings.expander"), expanded=True):
        # ── Row 1: Base Settings (horizontal) ──
        st.markdown(t("hybrid.settings.period"))
        submit = False
        bc1, bc2, bc3, bc4 = st.columns(4)
        with bc1:
            ist_stichtag = st.date_input(t("hybrid.settings.actual_date"), value=default_start)
        with bc2:
            forecast_end_date = st.date_input(t("hybrid.settings.forecast_end"), value=default_end)
        with bc3:
            _freq_month = t("hybrid.settings.frequency.month")
            _freq_quarter = t("hybrid.settings.frequency.quarter")
            freq_label = st.selectbox(t("hybrid.settings.frequency"), options=[_freq_month, _freq_quarter], index=0)
        with bc4:
            random_seed = st.number_input(t("attrition.settings.random_seed"), value=int(params_abg["random_seed"]), step=1)

        st.markdown("---")

        # ── Row 2: Component Toggles (horizontal) ──
        st.markdown(t("hybrid.settings.components"))
        
        st.markdown(f"**{t('hybrid.settings.attrition_drivers')}**")
        cc1, cc2, cc3, cc4 = st.columns(4)
        with cc1:
            comp_atz = st.checkbox(t("hybrid.settings.component.atz"), value=params_abg["components"]["atz"])
        with cc2:
            comp_ret = st.checkbox(t("hybrid.settings.component.retirement"), value=params_abg["components"]["retirement"])
        with cc3:
            comp_quit = st.checkbox(t("hybrid.settings.component.quit"), value=params_abg["components"]["quit"])
        with cc4:
            comp_ruhend = st.checkbox(t("hybrid.settings.component.ruhend"), value=params_abg["components"]["ruhend"])
            
        st.markdown(f"**{t('hybrid.settings.hiring_drivers')}**")
        zc1, zc2, zc3 = st.columns(3)
        with zc1:
            comp_azubi = st.checkbox(t("hybrid.settings.component.azubis"), value=True)
        with zc2:
            comp_trainee = st.checkbox(t("hybrid.settings.component.trainees"), value=True)
        with zc3:
            comp_hires = st.checkbox(t("hybrid.settings.component.new_hires"), value=True)

        st.markdown("---")

        # ── Row 3: Detail Parameters (sub-expanders) ──
        st.markdown(t("hybrid.settings.details"))

        t_abg, t_zug = st.tabs([t("hybrid.tabs.attrition"), t("hybrid.tabs.hiring")])

        with t_abg:
            with st.expander("ATZ-Parameter"):
                ac1, ac2, ac3, ac4, ac5 = st.columns(5)
                with ac1:
                    new_atz_base = st.slider("Neue Fälle (Basis)", min_value=0.0, max_value=0.5, value=float(params_abg["atz"].get("new_atz_rate", 0.05)), step=0.005, format="%.3f", key="hy_atz_base")
                with ac2:
                    eligible_age = st.number_input("Mindestalter", value=int(params_abg["atz"]["atz_eligible_age_min"]), step=1, key="hy_atz_age_min")
                with ac3:
                    eligible_age_max = st.number_input("Höchstalter", value=int(params_abg["atz"]["atz_eligible_age_max"]), step=1, key="hy_atz_age_max")
                with ac4:
                    ar_years = st.number_input("AR-Dauer (Jahre)", value=float(params_abg["atz"]["atz_duration_ar_years"]), step=0.5, key="hy_atz_ar_years")
                with ac5:
                    fr_years = st.number_input("FR-Dauer (Jahre)", value=float(params_abg["atz"]["atz_duration_fr_years"]), step=0.5, key="hy_atz_fr_years")

                st.divider()

                bc1, bc2 = st.columns([1, 1])
                with bc1:
                    use_atz_matrix = st.checkbox("Detaillierte ATZ-Matrix verwenden", value=params_abg["atz"].get("use_atz_matrix", False), key="hy_atz_use_mat")
                with bc2:
                    atz_dim = st.radio("Dimension für ATZ", options=["JobFamily", "OrgUnit"], index=0 if params_abg["atz"].get("atz_dimension", "JobFamily") == "JobFamily" else 1, horizontal=True, key="hy_atz_dim")

                # Matrix Editor logic (percent scale)
                atz_unique_vals = active_org_units if atz_dim == "OrgUnit" else active_jobfamily_values
                atz_dim_items = ["Default"] + atz_unique_vals
                atz_pct = migrate_to_percent(params_abg["atz"].get("atz_matrix", {}))
                atz_editor_data = []
                for val in atz_dim_items:
                    rate = atz_pct.get(str(val), new_atz_base * 100)
                    atz_editor_data.append({atz_dim: val, "Wahrscheinlichkeit (%)": float(rate)})
                df_atz_matrix = pd.DataFrame(atz_editor_data).set_index(atz_dim)
                edited_atz_df = st.data_editor(
                    df_atz_matrix, use_container_width=True, height=300,
                    key=_cluster_widget_key("hy_atz_editor", cluster_source_signature), disabled=not use_atz_matrix,
                    column_config={"Wahrscheinlichkeit (%)": st.column_config.NumberColumn(
                        "Wahrscheinlichkeit (%)", min_value=0.0, max_value=100.0, step=0.5, format="%.1f"
                    )},
                )
                new_atz_matrix = {str(k): float(v["Wahrscheinlichkeit (%)"]) for k, v in edited_atz_df.iterrows()}

            with st.expander("Renten-Parameter"):
                rc1, rc2 = st.columns(2)
                with rc1:
                    rent65 = st.slider("Renteneintritt 65+", min_value=0.0, max_value=1.0, value=float(params_abg["retirement"]["rent_rate_65"]), step=0.05, key="hy_rent_65")
                with rc2:
                    rent60 = st.slider("Frühverrentung 60-64", min_value=0.0, max_value=1.0, value=float(params_abg["retirement"]["rent_rate_60_65"]), step=0.05, key="hy_rent_60")

            with st.expander("Kündigungs-Parameter"):
                c1, c2, c3 = st.columns([3, 3, 2])
                with c1:
                    quit_base = st.slider("Basisrate p.a.", min_value=0.0, max_value=0.5, value=float(params_abg["quit"]["quit_rate_base"]), step=0.01, key="hy_quit_base")
                with c2:
                    use_quit_matrix = st.checkbox("Detaillierte Kündigungsmatrix verwenden", value=params_abg["quit"].get("use_quit_matrix", True), key="hy_quit_use_mat")
                with c3:
                    quit_dim = st.radio("Dimension", options=["JobFamily", "OrgUnit"], index=0, horizontal=True, key="hy_quit_dim")
                
                # Quit Matrix Editor (percent scale)
                q_unique = active_org_units if quit_dim == "OrgUnit" else active_jobfamily_values
                q_cohorts = ["alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus"]
                q_labels = {"alter_unter_30": "u30 (%)", "alter_30_45": "30-45 (%)", "alter_45_55": "45-55 (%)", "alter_55_plus": "ü55 (%)"}
                q_items = ["Default"] + q_unique
                q_pct = migrate_to_percent(params_abg["quit"].get("quit_matrix", {}))
                q_editor_data = []
                for val in q_items:
                    row = {quit_dim: val}
                    for c in q_cohorts:
                        row[c] = float(q_pct.get(c, {}).get(str(val), quit_base * 100))
                    q_editor_data.append(row)
                df_q_matrix = pd.DataFrame(q_editor_data).set_index(quit_dim)
                q_col_conf = {}
                for c in q_cohorts:
                    q_col_conf[c] = st.column_config.NumberColumn(
                        q_labels.get(c, c), min_value=0.0, max_value=100.0, step=0.5, format="%.1f"
                    )
                edited_q_df = st.data_editor(
                    df_q_matrix, use_container_width=True, height=300,
                    key=_cluster_widget_key("hy_quit_editor", cluster_source_signature), disabled=not use_quit_matrix,
                    column_config=q_col_conf,
                )
                new_quit_matrix = {c: {str(k): float(v[c]) for k, v in edited_q_df.iterrows()} for c in q_cohorts}

            with st.expander("Ruhend-Parameter"):
                hc1, hc2, hc3 = st.columns(3)
                ruhend_new = hc1.number_input("Neue Fälle / Jahr", value=int(params_abg["ruhend"]["ruhend_new_cases_per_year"]), step=1, key="hy_ruh_new")
                ruhend_return = hc2.slider("Rückkehrquote p.a.", min_value=0.0, max_value=1.0, value=float(params_abg["ruhend"]["ruhend_return_rate"]), step=0.05, key="hy_ruh_ret")
                ruhend_duration = hc3.number_input("Ø Dauer (Monate)", value=int(params_abg["ruhend"]["ruhend_avg_duration_months"]), step=1, key="hy_ruh_dur")

        with t_zug:
            st.markdown("#### 🎓 Azubis & Trainees")
            az1, az2, az3, az4 = st.columns(4)
            azubi_count = az1.number_input("Neue Azubis pro Jahr", 0, 100, int(params_zug["azubi"].get("new_cases_per_year", 15)), key="hy_azu_count")
            retention = az2.slider("Azubi-Übernahme (%)", 0.0, 1.0, float(params_zug["azubi"]["retention_rate"]), 0.05, key="hy_azu_ret")
            duration = az3.number_input("Ausbildungsdauer (Jahre)", 1.0, 5.0, float(params_zug["azubi"]["duration_years"]), 0.5, key="hy_azu_dur")
            azu_isolated = st.checkbox("Nur Prognose-Azubis (Bestand ignorieren)", value=False, key="hy_azu_iso")
            az_strat = az4.selectbox("Azubi-Verteilung", ["Random", "OrgUnit"], index=0, key="hy_azu_strat")

            # --- Azubi Takeover Matrix ---
            st.markdown("##### Detaillierte Übernahme-Verteilung")
            az_mc1, az_mc2 = st.columns([1, 1])
            with az_mc1:
                use_az_matrix = st.checkbox(
                    "Detailmatrix statt pauschaler Verteilung verwenden",
                    value=params_zug["azubi"].get("use_takeover_matrix", False),
                    help="Wenn aktiviert, wird die nachfolgende Matrix für die Verteilung der Übernahmen genutzt.",
                    key="hy_chk_use_az_matrix"
                )
            with az_mc2:
                az_dim = st.radio(
                    "Dimension für Übernahme",
                    options=["JobFamily", "OrgUnit"],
                    index=0 if params_zug["azubi"].get("takeover_dimension", "JobFamily") == "JobFamily" else 1,
                    format_func=lambda x: "Verteilen nach Jobfamily" if x == "JobFamily" else "Verteilen nach Org Unit",
                    disabled=not use_az_matrix,
                    horizontal=True,
                    key="hy_rad_az_dim"
                )

            valid_units = active_org_units
            valid_jfs = active_jobfamily_values
            az_matrix_vals = valid_units if az_dim == "OrgUnit" else valid_jfs

            az_takeover_matrix = render_distribution_matrix(
                label=f"Matrix: {az_dim} (Gewichtung für Übernahme)",
                dimension=az_dim,
                current_matrix=migrate_to_percent(params_zug["azubi"].get("takeover_matrix", {})),
                valid_vals=az_matrix_vals,
                key_prefix=_cluster_widget_key("hy_az_takeover_matrix", cluster_source_signature),
                disabled=not use_az_matrix
            )

            az_tc1, az_tc2 = st.columns(2)
            az_tarif = az_tc1.selectbox("Übernahme-Tarif", TARIFF_GROUPS, index=TARIFF_GROUPS.index(params_zug["azubi"]["entry_tariff_group"]) if params_zug["azubi"]["entry_tariff_group"] in TARIFF_GROUPS else 5, key="hy_az_tarif")
            az_step = az_tc2.number_input("Übernahme-Stufe", 1, 6, params_zug["azubi"]["entry_step"], key="hy_az_step")

            render_orgunit_mode_hint(use_az_matrix, az_dim)

            st.divider()

            tr1, tr2, tr3 = st.columns(3)
            trainee_count = tr1.number_input("Neue Trainees pro Jahr", 0, 100, params_zug["trainee"]["new_cases_per_year"], key="hy_tra_count")
            trainee_dur = tr2.number_input("Trainee-Dauer (Jahre)", 0.5, 3.0, params_zug["trainee"]["duration_years"], 0.5, key="hy_tra_dur")
            tr_strat = tr3.selectbox("Trainee-Verteilung", ["Random", "OrgUnit"], index=0, key="hy_tra_strat")
            
            st.markdown("#### 💼 Neueinstellungen (unabhängig)")
            h1, h2, h3 = st.columns(3)
            hire_count = h1.number_input("Einstellungen pro Jahr", 0, 500, params_zug["new_hires"]["count_per_year"], key="hy_hir_count")
            hire_strat = h2.selectbox("Strategie", ["Random", "OrgUnit", "Fill Vacancies"], index=2, key="hy_hir_strat", help="'Fill Vacancies' nutzt die Abgangsprognose zum Nachbesetzen.")
            
            # Distribution Matrix for New Hires
            with st.expander("📊 Verteilung Neueinstellungen (Matrix)", expanded=False):
                dist_base = _build_hybrid_distribution_base(df_ma, resolved_dimensions.to_dict())[["Jobfamily", "OE-Cluster", "Share %"]]
                edited_dist = st.data_editor(dist_base, use_container_width=True, key=_cluster_widget_key("hy_hire_dist_mat", cluster_source_signature), column_config={"Share %": st.column_config.NumberColumn(format="%.2f")})
                hire_distribution = edited_dist.to_dict("records")
        
        st.markdown("---")


    # ── Action Button ──
    st.write("")
    if st.button(t("hybrid.action.compute"), use_container_width=True, key="btn_run_hybrid"):
        submit = True

    has_hybrid_res = _hybrid_results_match_cluster_signature(cluster_source_signature)

    if not submit and not has_hybrid_res and (
        "hybrid_abg_res" in st.session_state or "hybrid_zug_res" in st.session_state
    ):
        invalidate_cluster_dependent_state(
            st.session_state,
            reason="hybrid_cluster_source_changed",
        )
        st.info("Gespeicherte Hybrid-Ergebnisse wurden verworfen, weil sich die aktive Clusterquelle geändert hat.")

    if not submit and not has_hybrid_res:
        st.info(t("hybrid.info.prompt_compute"))
        return

    # 1. Build Final Params
    ui_state_abg = {
        "ist_stichtag": ist_stichtag.isoformat() if hasattr(ist_stichtag, "isoformat") else str(ist_stichtag),
        "forecast_end_date": forecast_end_date.isoformat() if hasattr(forecast_end_date, "isoformat") else str(forecast_end_date),
        "freq": "M" if freq_label == t("hybrid.settings.frequency.month") else "Q",
        "components": {"atz": comp_atz, "retirement": comp_ret, "quit": comp_quit, "ruhend": comp_ruhend},
        "atz": {"new_atz_rate": new_atz_base, "atz_eligible_age_min": eligible_age, "atz_eligible_age_max": eligible_age_max, "atz_duration_ar_years": ar_years, "atz_duration_fr_years": fr_years, "use_atz_matrix": use_atz_matrix, "atz_dimension": atz_dim, "atz_matrix": new_atz_matrix},
        "retirement": {"rent_rate_65": rent65, "rent_rate_60_65": rent60},
        "quit": {"quit_rate_base": quit_base, "use_quit_matrix": use_quit_matrix, "quit_dimension": quit_dim, "quit_matrix": new_quit_matrix},
        "ruhend": {"ruhend_new_cases_per_year": ruhend_new, "ruhend_return_rate": ruhend_return, "ruhend_avg_duration_months": ruhend_duration},
        "random_seed": random_seed,
    }
    final_params_abg = build_abgaenge_params_from_ui(ui_state_abg)

    jf_to_cluster_map = build_jf_to_cluster_map(df_ma)
    if resolved_dimensions.is_source_backed:
        jf_to_cluster_map.update({value: value for value in active_jobfamily_values})
    
    final_params_zug = {
        "azubi": {
            "active": comp_azubi,
            "retention_rate": retention,
            "duration_years": duration,
            "strategy": az_strat,
            "target_org_unit": None,
            "entry_tariff_group": az_tarif,
            "entry_step": az_step,
            "new_cases_per_year": azubi_count,
            "exclude_baseline_azubis": azu_isolated,
            "use_takeover_matrix": use_az_matrix,
            "takeover_dimension": az_dim,
            "takeover_matrix": percent_to_weights(az_takeover_matrix),
            "jf_to_cluster_map": jf_to_cluster_map,
        },
        "trainee": {"active": comp_trainee, "new_cases_per_year": trainee_count, "duration_years": trainee_dur, "salary_group": "E 12", "strategy": tr_strat, "target_org_unit": None},
        "new_hires": {"active": comp_hires, "count_per_year": hire_count, "strategy": hire_strat, "target_org_unit": None, "distribution": hire_distribution},
        "available_org_units": active_org_units,
        "org_unit_to_cluster_map": resolved_dimensions.org_unit_to_cluster_map,
        "random_seed": random_seed
    }

    # 2. Execution ──────────────────────────────────────────────────
    if submit:
        # A. Departures (Basis)
        with st.spinner("Berechne Abgangs-Szenario..."):
            # CONTRACT V3: Synchronize with Page 3 results if parameters match
            p3_bundle = st.session_state.get("abgaenge_results")
            use_cached_abg = False
            if p3_bundle and p3_bundle.get("schema_version") == 3:
                p3_params = p3_bundle.get("params", {})
                p3_cluster_signature = st.session_state.get("abgaenge_cluster_source_signature")
                if p3_params == ui_state_abg and p3_cluster_signature == cluster_source_signature:
                    p3_global = st.session_state.get("abgaenge_global_result")
                    if p3_global:
                        abg_res = p3_global
                        use_cached_abg = True
                        st.info("🔄 Verwende identische Abgangs-Ergebnisse von der Abgänge-Seite.")

            if not use_cached_abg:
                abg_res = run_forecast_abgaenge(
                    df_ma=df_ma, 
                    df_atz=df_atz,
                    start_date=pd.Timestamp(ist_stichtag),
                    end_date=pd.Timestamp(forecast_end_date),
                    freq="M" if freq_label == t("hybrid.settings.frequency.month") else "Q",
                    params=final_params_abg,
                )
            
            st.session_state["hybrid_abg_res"] = abg_res
            st.session_state["hybrid_abg_params"] = final_params_abg
        
        # B. Extract Vacancies (if Fill Vacancies is selected)
        vacancies = []
        if hire_strat == "Fill Vacancies":
            # Safeguard for missing columns in stale state
            if "headcount_change" not in abg_res["events_person_level"].columns:
                abg_res["events_person_level"] = pd.DataFrame(columns=[
                    "period_label", "period_start", "period_end", "event_date", 
                    "persnr", "reason_code", "reason_label", "headcount_change", "mak_change",
                    "age", "tenure", "Organisationseinheit", "Kürzel OrgEinheit", 
                    "Jobfamily", "Planstelle", "TrfGr", "St"
                ])
            vacancies = _build_hybrid_vacancies_from_events(abg_res["events_person_level"], df_ma)

        # C. Arrivals
        with st.spinner("Berechne Zugangs-Szenario..."):
            zug_res = run_forecast_zugaenge(
                df_snapshot=df_ma,
                start_date=pd.Timestamp(ist_stichtag),
                end_date=pd.Timestamp(forecast_end_date),
                freq="M" if freq_label == t("hybrid.settings.frequency.month") else "Q",
                params=final_params_zug,
                vacancies=vacancies
            )
            # Enrichment: shared pipeline (same as Page 4)
            zug_events = zug_res["events"]
            if not zug_events.empty:
                zug_events = enrich_zugaenge_events(
                    zug_events,
                    df_ma,
                    final_params_zug,
                    cluster_mapping_bundle=cluster_mapping_bundle,
                    active_cluster_source_signature=cluster_source_signature,
                )
                zug_res["events"] = zug_events
            zug_res["cluster_source_signature"] = cluster_source_signature
            st.session_state["hybrid_zug_res"] = zug_res
            st.session_state["hybrid_zug_params"] = final_params_zug
            st.session_state["hybrid_cluster_source_signature"] = cluster_source_signature
    else:
        abg_res = st.session_state["hybrid_abg_res"]
        zug_res = st.session_state["hybrid_zug_res"]

    # 3. Filtering & View Preparation (View-Only Zoom) ─────────────
    active_filters = get_active_view_filters()
    raw_abg_events = abg_res["events_person_level"].copy()
    raw_zug_events = zug_res["events"].copy()
    view_state = _prepare_hybrid_view_state(
        raw_abg_events,
        raw_zug_events,
        snapshot_df,
        df_ma,
        active_filters,
        ist_stichtag=pd.Timestamp(ist_stichtag),
        forecast_end_date=pd.Timestamp(forecast_end_date),
        freq_label=freq_label,
        base_abg_kpis=abg_res.get("forecast_kpis"),
    )

    filt_abg_events = view_state["filt_abg_events"]
    filt_zug_events = view_state["filt_zug_events"]
    filt_zug_events_std = view_state["filt_zug_events_std"]
    combined_events_in_scope = view_state["combined_events_in_scope"]
    df_snapshot_filtered = view_state["df_snapshot_filtered"]
    df_view_agg = view_state["df_view_agg"]
    net_kpis = view_state["net_kpis"]
    abg_view_kpis = view_state["abg_view_kpis"]
    zug_view_kpis = view_state["zug_view_kpis"]
    n_total_before = view_state["n_abg_before"] + view_state["n_zug_before"]
    n_total_after = view_state["n_abg_after"] + view_state["n_zug_after"]
    render_filter_status(n_total_before, n_total_after)

    if df_snapshot_filtered.empty:
        st.warning(t("hybrid.warning.no_filtered_data"))
        return

    # 4. Rendering ──────────────────────────────────────────────────
    st.divider()

    # Management Summary
    if not net_kpis.empty:
        # Task 1.2: Hybrid Cockpit KPIs use ONLY Bank Exits
        total_exits = int(abs(combined_events_in_scope[combined_events_in_scope["is_headcount_exit_bank"] == True]["headcount_change"].sum()))
        
        # External Hires (Consistency: Use type or schema)
        ext_hire_mask = combined_events_in_scope["type"].astype(str).str.contains("Hire", case=False)
        total_entries = int(combined_events_in_scope[ext_hire_mask & (combined_events_in_scope["headcount_change"] > 0)]["headcount_change"].sum())
        
        net_hc_delta = total_entries - total_exits
        
        last_kpi = net_kpis.iloc[-1]
        
        st.markdown(t("hybrid.summary.heading"))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(t("hybrid.summary.metric.exits_heads"), f"{total_exits}")
        m2.metric(t("hybrid.summary.metric.entries_heads"), f"{total_entries}")
        m3.metric(t("hybrid.summary.metric.net_delta_heads"), f"{net_hc_delta:+}")
        m4.metric(t("hybrid.summary.metric.fte_end"), f"{last_kpi['mak_end']:.1f}", delta=f"{last_kpi['mak_delta']:.1f}")

    t_all, t_abg_res, t_zug_res, t_list = st.tabs([
        t("hybrid.results.tab.net"),
        t("hybrid.results.tab.attrition"),
        t("hybrid.results.tab.hiring"),
        t("hybrid.results.tab.list"),
    ])
    
    with t_all:
        import plotly.graph_objects as go
        st.markdown(t("hybrid.net.heading"))
        fig_net = go.Figure()
        fig_net.add_trace(go.Scatter(x=net_kpis["period_end"], y=net_kpis["headcount_end"], name=t("hybrid.net.trace.headcount"), line=dict(color=COLORS["accent_blue"], width=3)))
        fig_net.add_trace(go.Scatter(x=net_kpis["period_end"], y=net_kpis["mak_end"], name=t("hybrid.net.trace.fte"), line=dict(color=COLORS["accent_green"], width=3, dash='dash')))
        fig_net.update_layout(xaxis_title=t("hybrid.net.axis.date"), yaxis_title=t("hybrid.net.axis.stock"))
        fig_net = apply_legend_bottom(fig_net)
        st.plotly_chart(fig_net, use_container_width=True, key="hybrid_net_line_chart")
        
        st.info(t("hybrid.net.info"))
        
        st.markdown(t("hybrid.net.drivers.heading"))
        # Stacked bar with exits (negative) and entries (positive)
        # Chart: Zugänge nach Typ (Monatlich verfeinert) - Fix: Strict Separation
        # We want: 
        # 1. External Intake: Azubi_Hire, Trainee_Hire, New_Hire
        # 2. Conversions: Azubi_Conversion_In (Netto 0 in headcount, but +1 for Regular Staff view)
        netto_chart_sources = _build_hybrid_netto_chart_sources(combined_events_in_scope)
        df_ext = netto_chart_sources["df_ext"]
        
        fig_z_month = px.bar(
            df_ext, 
            x="month", 
            y="count", 
            color="Kategorie", 
            title=t("hybrid.net.external_entries_chart.title"),
            labels={"count": t("hybrid.net.external_entries_chart.count"), "month": t("hybrid.net.external_entries_chart.month")},
            color_discrete_map={
                t("hybrid.net.external_entries_chart.apprentice"): "#1f77b4",
                t("hybrid.net.external_entries_chart.trainee"): "#ff7f0e",
                t("hybrid.net.external_entries_chart.new_hire"): "#2ca02c"
            }
        )
        fig_z_month = apply_legend_bottom(fig_z_month)
        st.plotly_chart(fig_z_month, use_container_width=True, key="hybrid_zug_month")
        
        # Series B: Internal Restructuring / Regular Growth (Optional View)
        # If we want to show "Growth of Regular Staff", we'd sum New_Hire + Azubi_Conversion_In
        # But the user asked for distinct separation. So let's show Conversions separately if significant.
        df_conv = netto_chart_sources["df_conv"]
        if not df_conv.empty:
            fig_conv = px.bar(
                df_conv,
                x="month",
                y="count",
                color="Kategorie",
                title=t("hybrid.net.takeovers_chart.title"),
                labels={"count": t("hybrid.net.takeovers_chart.count"), "month": t("hybrid.net.takeovers_chart.month")},
                color_discrete_sequence=["#9467bd"] # purple
            )
            fig_conv = apply_legend_bottom(fig_conv)
            st.plotly_chart(fig_conv, use_container_width=True, key="hybrid_conv_month")
            st.caption(t("hybrid.net.takeovers_chart.caption"))
        if not combined_events_in_scope.empty:
            driver_agg = netto_chart_sources["driver_agg"]
            
            fig_drivers = px.bar(
                driver_agg, x="JahrMonat", y="mak_change", color="reason_label",
                labels={"mak_change": "Kapazitätsänderung (MAK)", "JahrMonat": "Zeitraum", "reason_label": "Typ"},
                title=t("hybrid.net.effects_chart.title")
            )
            fig_drivers = apply_legend_bottom(fig_drivers)
            st.plotly_chart(fig_drivers, use_container_width=True, key="hyb_net_drivers_main")
            st.caption(t("hybrid.net.effects_chart.caption"))
        else:
            st.info(t("hybrid.net.effects_chart.no_data"))

        
        if st.session_state.get("debug_active", False):
            with st.expander("🐞 Debug / Plausibilitätschecks (Netto)", expanded=False):
                st.markdown("#### Validierung Netto-Cockpit")
                
                # 1. Scope & Filter Check
                # Step 4: Debug Block Refined (3 Views)
                st.markdown("##### 🔍 Exit-Definition Diagnostics (3 Sichten)")
                
                # A. Prepare Sets (Use Semantic Match Key for robustness)
                # A. Prepare Sets (Use Semantic Match Key for robustness)
                # Re-Construct Key strictly for combined_events too
                # Using 100% matching logic as Page 3 (Contract V4)
                from abgaenge.schemas import build_event_uid
                combined_events_in_scope["event_uid"] = build_event_uid(combined_events_in_scope)

                exits_any = combined_events_in_scope[combined_events_in_scope["is_headcount_exit_any"] == True]
                exits_bank = combined_events_in_scope[combined_events_in_scope["is_headcount_exit_bank"] == True]
                
                # Set 1: Technical
                key_tech = set(exits_any["event_uid"])
                c_tech = len(key_tech)

                # Set 2: Detail (Ref Page 3) AND Set 3: Bank
                key_bank = set(exits_bank["event_uid"])
                c_bank = len(key_bank)

                # Detail Logic: Prefer Page 3 Ref if available to show "Total Truth"
                key_detail = set()
                c_detail = 0
                detail_ref_df = None
                detail_source_label = "Hybrid Marker (Fallback)"
                
                # HARDFIX PROOF: VISUAL DEBUG
                st.markdown("##### 🕵️‍♂️ Hybrid Debug: Page 3 Reference")
                
                # Check for session state (Contract Hardening)
                # Priority: 1. abgaenge_results (Standard Contract)
                #           2. page3_abgaenge_events (Legacy/Hardfix)
                #           3. Local Fallback
                
                det_df_source = None
                p3_meta_text = "N/A"
                
                if "abgaenge_results" in st.session_state:
                    res = st.session_state["abgaenge_results"]
                    # V3 Check: schema_version
                    if isinstance(res, dict):
                         # PRIORITY: events_chart (100% match with P3 counts)
                         det_df_source = res.get("events_chart", res.get("events"))
                         
                         # Meta info
                         if "meta" in res:
                             p3_meta_text = str(res["meta"])
                         
                         # Drift Check
                         stored_filters = res.get("filters", {})
                         if stored_filters and dict_hash(stored_filters) != current_filter_hash:
                             st.warning("⚠️ Hinweis: Die Abgangs-Daten (Ref Page 3) wurden mit anderen Filtern berechnet als aktuell ausgewählt. Bitte Page 3 erneut ausführen für 100% Konsistenz.")
                    
                if det_df_source is None and "page3_abgaenge_events" in st.session_state:
                     det_df_source = st.session_state["page3_abgaenge_events"]
                
                if det_df_source is not None:
                     detail_ref_df = det_df_source.copy()
                     # Filter for matching scope (Exits only?)
                     # If we have V2 'events_chart', it's already filtered for exits.
                     # If legacy, we apply the filter.
                     if "headcount_change" in detail_ref_df.columns:
                         detail_ref_df = detail_ref_df[detail_ref_df["headcount_change"] < 0].copy()
                     
                     st.success(f"✅ Page 3 Session State gefunden! Count: {len(detail_ref_df)} | Meta: {p3_meta_text}", icon="✅")
                     detail_source_label = "abgaenge_results (V3 Source)"
                     p3_exists = True
                else:
                    # FALLBACK: Compute Locally using Shared Logic
                    st.info("ℹ️ Keine Page 3 Session State -> Berechne lokal (Hybrid Parameter)...", icon="ℹ️")
                    try:
                        from abgaenge.forecast import get_forecast_data
                        # Re-use Hybrid's 'params' (final_params_abg) which should be compatible or similar
                        # We need to construct a robust params dict if final_params_abg is not full enough?
                        # final_params_abg is defined earlier in Page 5.
                        
                        # Call forecast using Hybrid's date range and params
                        # Note: This implies "Ref Page3" = "Ref Abgänge Logic with Hybrid Params"
                        local_p3_res = get_forecast_data(
                            df_ma=df_ma,
                            params=final_params_abg, # defined lines 500+
                            start_date=pd.Timestamp(ist_stichtag),
                            end_date=pd.Timestamp(forecast_end_date),
                            freq="M"
                        )
                        detail_ref_df = local_p3_res["events_person_level"].copy()
                        if "headcount_change" in detail_ref_df.columns:
                             detail_ref_df = detail_ref_df[detail_ref_df["headcount_change"] < 0].copy()
                        
                        detail_source_label = "Computed Locally (fallback)"
                        st.write("Computed Count:", len(detail_ref_df))
                        p3_exists = True
                    except Exception as e:
                        st.error(f"Fehler bei lokaler Berechnung: {e}")
                        p3_exists = False

                if p3_exists and detail_ref_df is not None:
                     # 2. Build Key (Strict & Robust - MUST match Page 3 event_uid logic)
                     try:
                        # Ensure persistence of canonical UID across Detail set
                        if "event_uid" not in detail_ref_df.columns:
                            from abgaenge.schemas import build_event_uid
                            detail_ref_df["event_uid"] = build_event_uid(detail_ref_df)
                        
                        key_detail = set(detail_ref_df["event_uid"])
                        c_detail = len(key_detail)
                        
                        # Use the standardized UID for the sample tables too
                        detail_ref_df["_uid_match"] = detail_ref_df["event_uid"]
                     except Exception as e:
                        st.error(f"Fehler beim Erstellen des Keys (Ref): {e}")
                        c_detail = 0
                else:
                    # Fallback to local marker if calculation failed
                    exits_detail_hybrid = combined_events_in_scope[combined_events_in_scope["is_headcount_exit_detail"] == True]
                    key_detail = set(exits_detail_hybrid["event_uid"])
                    c_detail = len(key_detail)
                    detail_source_label = "Hybrid Marker (Fail Safe)"

                # RECONCILIATION SUMMARY (V6)
                from abgaenge.schemas import build_event_uid

                # Ensure canonical V6 UIDs across all sets for exact matching
                combined_events_in_scope["event_uid"] = build_event_uid(combined_events_in_scope)
                
                # Get Detail Page Exits (Gold Standard from session_state if available)
                detail_ref_df = None
                if "abgaenge_results" in st.session_state:
                    res = st.session_state["abgaenge_results"]
                    detail_ref_df = res.get("events_chart", res.get("events"))
                    if detail_ref_df is not None:
                        detail_ref_df["event_uid"] = build_event_uid(detail_ref_df)
                        # Filter to HC exits only for reconciliation
                        detail_ref_df = detail_ref_df[detail_ref_df["headcount_change"] < 0].copy()

                # Patch 4: Decouple Bank Exits from MAK
                # Definition: headcount_change < 0 AND not in explicit exclusion list
                def _is_bank_exit_v6(row):
                    if row["headcount_change"] >= 0: return False
                    rl = str(row.get("reason_label", ""))
                    # 1. Exclude Status Changes / Conversions (Standard Bank Definition)
                    if "Statuswechsel" in rl or "Conversion" in rl: return False
                    # 2. Exclude Ruhend
                    if "Ruhend" in rl: return False
                    return True

                combined_events_in_scope["is_headcount_exit_bank"] = combined_events_in_scope.apply(_is_bank_exit_v6, axis=1)
                exits_bank = combined_events_in_scope[combined_events_in_scope["is_headcount_exit_bank"] == True].copy()
                exits_bank["event_uid"] = build_event_uid(exits_bank)

                key_detail = set(detail_ref_df["event_uid"]) if detail_ref_df is not None else set()
                key_bank = set(exits_bank["event_uid"])
                
                st.markdown("### 📊 Side-by-Side Rekonziliation (Contract V6)")
                
                # 1. Reason-Level Table (Goal 1.1)
                def _get_reason_reco_v6(detail_df, bank_df):
                    reasons = ["QUIT", "RETIREMENT", "ATZ_END"]
                    rows = []
                    for r in reasons:
                        d_r = detail_df[detail_df["event_uid"].str.contains(f"\\|{r}$", regex=True)] if detail_df is not None else pd.DataFrame()
                        b_r = bank_df[bank_df["event_uid"].str.contains(f"\\|{r}$", regex=True)]
                        
                        k_d = set(d_r["event_uid"]) if not d_r.empty else set()
                        k_b = set(b_r["event_uid"])
                        k_i = k_d & k_b
                        
                        rows.append({
                            "Grund": r,
                            "Abgänge Seite": len(k_d),
                            "Hybrid Bank": len(k_b),
                            "Schnittmenge": len(k_i),
                            "Nur AbgängeSeite": len(k_d - k_b),
                            "Nur HybridBank": len(k_b - k_d)
                        })
                    return pd.DataFrame(rows)

                reco_df = _get_reason_reco_v6(detail_ref_df, exits_bank)
                st.dataframe(reco_df, use_container_width=True, hide_index=True)

                # 2. Monthly Counts Comparison (Goal 3)
                if detail_ref_df is not None:
                    st.markdown("#### 📅 Monatlicher Vergleich (QUIT & RETIREMENT)")
                    def _get_monthly_comp_v6(d_df, b_df):
                        # Use first part of UID (period_label) for grouping
                        d_df["month"] = d_df["event_uid"].str.split('|').str[0]
                        b_df["month"] = b_df["event_uid"].str.split('|').str[0]
                        all_months = sorted(set(d_df["month"]) | set(b_df["month"]), key=lambda x: pd.to_datetime(x, format="%b %Y", errors='coerce'))
                        
                        res = []
                        for m in all_months:
                            dq = len(d_df[(d_df["month"] == m) & (d_df["event_uid"].str.contains("|QUIT"))])
                            bq = len(b_df[(b_df["month"] == m) & (b_df["event_uid"].str.contains("|QUIT"))])
                            dr = len(d_df[(d_df["month"] == m) & (d_df["event_uid"].str.contains("|RETIREMENT"))])
                            br = len(b_df[(b_df["month"] == m) & (b_df["event_uid"].str.contains("|RETIREMENT"))])
                            res.append({
                                "Monat": m,
                                "QUIT (P3)": dq, "QUIT (Bank)": bq,
                                "RET (P3)": dr, "RET (Bank)": br,
                                "Identisch?": "✅" if (dq == bq and dr == br) else "❌"
                            })
                        return pd.DataFrame(res)
                    
                    st.dataframe(_get_monthly_comp_v6(detail_ref_df.copy(), exits_bank.copy()).head(36), use_container_width=True, hide_index=True)

                # 3. Bank Exclusion Analysis (Goal 2)
                if detail_ref_df is not None:
                    gap_keys = key_detail - key_bank
                    if gap_keys:
                        st.markdown(f"#### 🚫 Bank-Exklusions Analyse (Gap: {len(gap_keys)} Events)")
                        gap_df = detail_ref_df[detail_ref_df["event_uid"].isin(gap_keys)].copy()
                        
                        def _explain_v6(row):
                            rl = str(row.get("reason_label", ""))
                            hc = row.get("headcount_change", 0)
                            mak = row.get("mak_change", 0)
                            
                            # Check categorization rules
                            if "Statuswechsel" in rl or "Conversion" in rl: return "Regel: Azubi/Statuswechsel (Kein Bank-Exit)"
                            if "Ruhend" in rl: return "Regel: Ruhend (Nicht-permanenter Abgang)"
                            
                            # Detection of Patch 4 decoupling
                            if hc < 0 and mak == 0: return "Sollte in Bank sein (Check Patch 4 Integration)"
                            return "Filterabweichung (Stichtag/Pool)"
                        
                        gap_df["dropped_due_to_mak_filter"] = (gap_df["headcount_change"] < 0) & (gap_df["mak_change"] == 0)
                        gap_df["Begründung"] = gap_df.apply(_explain_v6, axis=1)
                        
                        display_cols = ["event_date", "persnr", "reason_label", "headcount_change", "mak_change", "dropped_due_to_mak_filter", "Begründung"]
                        st.dataframe(gap_df[display_cols].head(50), use_container_width=True, hide_index=True)

                # Eligibility Debug (Requirement C.1)
                with st.expander("🛠️ Ursachen-Analyse: Kündigungs-Abweichung (217 vs 169)", expanded=False):
                    st.markdown("**Eligibility Check (Eligible for QUIT):**")
                    # Simulation in Abgänge Page (217) uses GLOBAL population.
                    # Hybrid (169) uses GLOBAL population but might have different active components.
                    # Gap 217 vs 169: check if any filters or excluded statuses (Ruhend, Azubi) are applied differently.
                    
                    if not df_ma.empty:
                        c_ruhend = len(df_ma[df_ma["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis"])
                        c_azubi = len(df_ma[df_ma["Status kundenindividuell"].str.contains("Auszubildende", na=False)])
                        c_no_mak = len(df_ma[df_ma["mak"] <= 0])
                        
                        st.write({
                            "Gesamt-Personalstand (Aggregiert)": len(df_ma),
                            "Davon Ruhend": c_ruhend,
                            "Davon Azubi/Intern": c_azubi,
                            "Davon MAK <= 0 (Filterkriterium?)": c_no_mak
                        })
                        st.caption("Hinweis: In Page 3 werden alle 'Köpfe' simuliert, Hybrid könnte durch Vor-Filterung (z.B. nur MAK>0) Personen verlieren.")

                st.markdown("---")
                # RECO COUNTS (Fix for NameError crash)
                c_tech = len(combined_events_in_scope[combined_events_in_scope["headcount_change"] < 0])
                c_detail = len(key_detail)
                c_bank = len(key_bank)
                c_inter = len(key_detail & key_bank)

                st.write({
                    "1. Abgänge technisch (HC<0)": c_tech,
                    "2. Abgänge Detailseite (Ref Page 3)": c_detail,
                    "3. Abgänge Cockpit (Bank)": c_bank,
                    "4. Schnittmenge (Match)": c_inter
                })

                if c_inter == 0 and c_detail > 0 and c_bank > 0:
                    st.error("🚨 KRITISCH: Keine Schnittmenge gefunden! Prüfe Key-Definitionen (PersNr Padding?).")
                    with st.expander("🔬 Key-Debugging (Sample Rows)", expanded=True):
                         dbg_cols = [c for c in ["event_date", "period_start", "period_end", "persnr", "reason_code", "reason_label", "event_uid"] if c in combined_events_in_scope.columns]
                         
                         st.markdown("**Detail Seite (Top 10):**")
                         if detail_ref_df is not None:
                             st.dataframe(detail_ref_df[dbg_cols].head(10), use_container_width=True)
                         
                         st.markdown("**Bank Cockpit (Top 10):**")
                         st.dataframe(exits_bank[dbg_cols].head(10), use_container_width=True)

                # Audit Table (Robust & Explicit)
                with st.expander("🔎 Audit: Datenquellen & Definitionen", expanded=False):
                    # Manual DataFrame Contruction to avoid rendering issues
                    audit_cols = ["View", "Count", "Source"]
                    audit_rows = [
                        ["Technisch", c_tech, "Hybrid Filter (HC<0)"],
                        ["Detailseite", c_detail, detail_source_label],
                        ["Cockpit", c_bank, "Hybrid Bank Filter"],
                        ["Matches", c_inter, "Eindeutige Übereinstimmung (UID)"]
                    ]
                    audit_df = pd.DataFrame(audit_rows, columns=audit_cols)
                    st.dataframe(audit_df, use_container_width=True, hide_index=True)

                col_d1, col_d2 = st.columns(2)
                
                # Diff 1: Detail - Bank (Events in Detail but missing in Bank/Cockpit)
                diff_detail_bank = key_detail - key_bank
                with col_d1:
                    st.metric("Detailseite (+) vs Bank (-)", len(diff_detail_bank))
                    if diff_detail_bank:
                        st.caption("Events auf Detailseite (Ref P3), die im Bank-Cockpit fehlen:")
                        # Events might be in Hybrid OR only in P3
                        # Use the standardized UID for filtering
                        df_d_only = combined_events_in_scope[combined_events_in_scope["event_uid"].isin(diff_detail_bank)]
                        
                        if df_d_only.empty and p3_exists:
                            # Events are missing in Hybrid but present in P3 Key Set
                            # Use the pre-prepared detail_ref_df
                            missing = detail_ref_df[detail_ref_df["event_uid"].isin(diff_detail_bank)]
                            st.dataframe(missing[["event_date", "reason_label", "headcount_change"]].head(100), use_container_width=True, hide_index=True)
                        elif not df_d_only.empty:
                             cols_to_show = [c for c in ["event_date", "reason_label", "headcount_change", "source_view"] if c in df_d_only.columns]
                             st.dataframe(df_d_only.drop_duplicates("event_uid")[cols_to_show], use_container_width=True, hide_index=True)

                # Diff 2: Bank - Detail (Events in Bank but missing in Detail)
                diff_bank_detail = key_bank - key_detail
                with col_d2:
                    st.metric("Bank (+) vs Detailseite (-)", len(diff_bank_detail))
                    if diff_bank_detail:
                        st.caption("Events im Bank-Cockpit, die auf Detailseite fehlen:")
                        df_b_only = combined_events_in_scope[combined_events_in_scope["event_uid"].isin(diff_bank_detail)]
                        # Crash fix ensure cols exist
                        cols_b = [c for c in ["event_date", "reason_label", "headcount_change", "source_view"] if c in df_b_only.columns]
                        if not df_b_only.empty:
                            st.dataframe(df_b_only.drop_duplicates("event_uid")[cols_b], use_container_width=True, hide_index=True)
                        else:
                            st.info(f"Differenz von {len(diff_bank_detail)} Keys gefunden, aber keine passenden Events im Datensatz gefunden.")

                # 📌 Fix 3: Netto-Reconciliation Correct
                
                # 📌 Fix 3: Netto-Reconciliation Correct
                st.divider()
                st.markdown("##### ⚖️ Netto-Reconciliation")
                
                # 1. Treiber Summe (Technical Netto)
                treiber_sum_hc = combined_events_in_scope["headcount_change"].sum()
                
                # 2. Cockpit Netto Scope (Real Aggregation)
                # Valid Cockpit events are those where is_headcount_exit_bank is True (Exits)
                # AND Entries that should count (Need definition: usually all except internal transfers?)
                # Assuming "Bank Netto" = Sum of events that survive the bank filter.
                # Since we don't have a "is_entry_bank" flag yet, let's assume all entries count?
                # BETTER: Just use the events that match 'is_headcount_exit_bank' and their positive counterparts.
                # Actually, the user prompts says: "Cockpit-Netto als Summe headcount_change der Cockpit-Events"
                # So we must apply the specific Cockpit filters to ALL events (entries too).
                
                def _is_cockpit_relevant(row):
                    rl = str(row["reason_label"])
                    # Exclude Lifecycle Status Changes
                    if "Statuswechsel" in rl or "Conversion" in rl: return False
                    # Exclude Ruhend
                    if "Ruhend" in rl: return False
                    return True
                
                cockpit_events = combined_events_in_scope[combined_events_in_scope.apply(_is_cockpit_relevant, axis=1)]
                cockpit_netto = cockpit_events["headcount_change"].sum()
                
                st.write(f"**Treiber-Summe (Tech):** {treiber_sum_hc} | **Cockpit-Netto:** {cockpit_netto}")
                
                delta_net = treiber_sum_hc - cockpit_netto
                
                if delta_net != 0:
                    st.warning(f"⚠️ Differenz: {delta_net} HC-Punkte (Erklärung unten)")
                    
                    # Explain Diff using Set Logic on UIDs
                    tech_uids = set(combined_events_in_scope["event_uid"])
                    # We need event_uid on cockpit_events
                    cockpit_uids = set(cockpit_events["event_uid"])
                    
                    # Diff Set = Elements in Tech but NOT in Cockpit
                    diff_uids = tech_uids - cockpit_uids
                    
                    if diff_uids:
                        diff_df = combined_events_in_scope[combined_events_in_scope["event_uid"].isin(diff_uids)]
                        st.caption("Netto-Differenz erklärt durch folgende Events (Nicht im Cockpit):")
                        st.dataframe(
                            diff_df.groupby("reason_label")["headcount_change"].sum().reset_index().rename(columns={"headcount_change": "Delta Impact"}),
                            use_container_width=True, hide_index=True
                        )
                    else:
                        st.info("Keine Event-Set-Differenz gefunden, obwohl Summen abweichen. Prüfen Sie Rundung?")

                # 📌 Fix 4: Consistency Rules (Whitelist)
                st.divider()
                st.markdown("##### 🛡️ MAK/HC Consistency Rules (Audit)")
                
                def get_consistency_status(row):
                    hc = row["headcount_change"]
                    mak = row["mak_change"]
                    reason = str(row["reason_label"])
                    
                    # A. MAK Positive (Zugänge)
                    if hc > 0:
                        # Azubi Hire: HC+1, MAK~0 -> OK
                        if "Azubi" in reason and ("Eintritt" in reason or "Neueinstellung" in reason): 
                            return "OK" if abs(mak) < 0.1 else f"WARN (Azubi HC+1, MAK!~0: {mak})"
                        # Azubi Übernahme: HC+1, MAK+1 -> OK
                        if "Übernahme" in reason:
                            return "OK" if mak > 0.9 else f"WARN (Übernahme HC+1, MAK<0.9: {mak})"
                        # Standard Hire: HC+1, MAK>=0.1 -> OK
                        if mak > 0: return "OK"
                        return f"WARN (Standard Hire HC+1, MAK<=0: {mak})"
                    
                    # B. MAK Negative (Abgänge)
                    if hc < 0:
                        # Azubi End / Conversion Out: HC-1, MAK~0 -> OK
                        if "Azubi" in reason and ("Ende" in reason or "Statuswechsel" in reason):
                            return "OK" if abs(mak) < 0.1 else f"WARN (Azubi End HC-1, MAK!~0: {mak})"
                        # ATZ Rente (nach ATZ): HC-1, MAK<=0 -> OK
                        if "Rente" in reason and "ATZ" in reason:
                            return "OK" # Can be 0 if capacity gone in AR->FR
                        # Ruhend Start: HC-1, MAK<=0 -> OK
                        if "Ruhend" in reason:
                            return "OK"
                        # Standard Exit: HC-1, MAK<0 -> OK
                        if mak < 0: 
                            if mak < -1.1: return f"WARN (Exit MAK < -1.1: {mak:.2f})"
                            return "OK"
                        return f"ERR (Standard Exit HC-1, MAK=0)"

                    # C. Status Changes (HC=0)
                    if hc == 0:
                        # ATZ AR->FR: MAK < 0 -> OK
                        if "AR → FR" in reason:
                            return "OK" if mak < 0 else "ERR (ATZ AR->FR, MAK>=0)"
                        # Internal Change: Usually MAK=0?
                        # If meaningful change, allow specific ones.
                        if abs(mak) > 0.01: return "INFO (Internal MAK Change)"
                        return "OK"

                    return "OK"

                # Apply Rule
                combined_events_in_scope["consistency_result"] = combined_events_in_scope.apply(get_consistency_status, axis=1)
                
                # Filter for WARN/ERR/INFO
                audit_rows = combined_events_in_scope[combined_events_in_scope["consistency_result"].str.contains("WARN|ERR|INFO")]
                # Split Critical Errors vs Warnings
                err_rows = audit_rows[audit_rows["consistency_result"].str.contains("ERR")]
                warn_rows = audit_rows[audit_rows["consistency_result"].str.contains("WARN")]
                
                col_audit1, col_audit2 = st.columns(2)
                with col_audit1:
                    st.metric("Critical Errors", len(err_rows))
                    if not err_rows.empty:
                        st.error("Gefundene Inkonsistenzen (Logikfehler):")
                        st.dataframe(err_rows[["event_date", "reason_label", "headcount_change", "mak_change", "consistency_result"]], use_container_width=True)
                    else:
                        st.success("✅ Keine kritischen MAK-Logikfehler.")
                
                with col_audit2:
                    st.metric("Warnings / Info", len(warn_rows))
                    if not warn_rows.empty:
                        with st.expander("Warnungen anzeigen"):
                            st.dataframe(warn_rows[["event_date", "reason_label", "headcount_change", "mak_change", "consistency_result"]], use_container_width=True)

                st.divider()
                st.markdown("##### 1. Net End Validation")
                if not net_kpis.empty and not combined_events_in_scope.empty:
                    last_mak = net_kpis.iloc[-1]["mak_end"]
                    # Start + Delta
                    initial_mak = net_kpis.iloc[0]["mak_start"]
                    calc_delta = last_mak - initial_mak
                    # Ref Sum from SCOPED Events
                    events_delta = combined_events_in_scope["mak_change"].sum()
                    
                    # Check 1: Does KPI Delta match Event Delta? (Should be exact now)
                    _check_sum("MAK Delta (KPI vs Events)", calc_delta, events_delta, "MAK")
                    
                    # 2. Driver Chart Validation (Updated for Strict Separation)
                    # We only sum the EXTERNAL intake for the first chart validation if possible,
                    # or just skip chart validation here as we split the charts.
                    # Let's validate the TOTAL positive delta (External + Internal In) against event sum.
                    # This remains valid as 'headcount_change' > 0 are all additions.
                    
                    pos_delta = combined_events_in_scope[combined_events_in_scope["headcount_change"] > 0]["headcount_change"].sum()
                    # _check_sum("Total Inflow vs Event Sum", pos_delta, events_delta, "Heads") # This check is vague now
                    
                    st.divider()
                    st.markdown("### 🔭 Treiber-Analyse (Bottom-Up Summe)")
                    # Calculate summary per driver
                    driver_stats = []
                    
                    # 1. External Zugänge
                    azu_hire = combined_events_in_scope[combined_events_in_scope["type"] == "Azubi_Hire"]["headcount_change"].sum()
                    tra_hire = combined_events_in_scope[combined_events_in_scope["type"] == "Trainee_Hire"]["headcount_change"].sum()
                    new_hire = combined_events_in_scope[combined_events_in_scope["type"] == "New_Hire"]["headcount_change"].sum()
                    
                    driver_stats.append({"Bereich": "Zugänge (extern)", "Treiber": "Azubi Neueinstellung (externer Zugang)", "Delta HC": int(azu_hire)})
                    driver_stats.append({"Bereich": "Zugänge (extern)", "Treiber": "Trainee Neueinstellung", "Delta HC": int(tra_hire)})
                    driver_stats.append({"Bereich": "Zugänge (extern)", "Treiber": "Basis-Einstellungen / Nachbesetzung", "Delta HC": int(new_hire)})
                    
                    # 2. Umwandlungen (Internal Moves)
                    azu_conv_out = combined_events_in_scope[combined_events_in_scope["type"] == "Azubi_Conversion_Out"]["headcount_change"].sum()
                    azu_conv_in = combined_events_in_scope[combined_events_in_scope["type"] == "Azubi_Conversion_In"]["headcount_change"].sum()
                    azu_conv_count = int(combined_events_in_scope[combined_events_in_scope["type"] == "Azubi_Conversion_In"]["headcount_change"].sum())
                    
                    driver_stats.append({
                        "Bereich": "Umwandlungen (Netto 0)", 
                        "Treiber": "Azubi-Abschluss: Übernahme (Umwandlung)", 
                        "Delta HC": int(azu_conv_out + azu_conv_in),
                        "Fallzahl": azu_conv_count
                    })
                    
                    # 3. Abgänge
                    # Group reasons from abg_res
                    abg_by_reason = filt_abg_events.groupby("reason_label")["headcount_change"].sum().reset_index()
                    for _, row in abg_by_reason.iterrows():
                        driver_stats.append({"Bereich": "Abgänge", "Treiber": row["reason_label"], "Delta HC": int(row["headcount_change"])})
                    
                    # Add Azubi Exit to Abgänge
                    azu_exit = combined_events_in_scope[combined_events_in_scope["type"] == "Azubi_Exit"]["headcount_change"].sum()
                    if azu_exit != 0:
                        driver_stats.append({"Bereich": "Abgänge", "Treiber": "Azubi-Abschluss: Nichtübernahme (Abgang)", "Delta HC": int(azu_exit)})

                    df_drivers_audit = pd.DataFrame(driver_stats)
                    # Reorder to show Fallzahl for conversions
                    st.table(df_drivers_audit.fillna("-"))
                    
                    # Residual Check (Fix B)
                    sum_drivers = df_drivers_audit["Delta HC"].sum()
                    total_raw_delta = combined_events_in_scope["headcount_change"].sum()
                    residual = total_raw_delta - sum_drivers
                    
                    if abs(residual) > 0.1:
                        st.warning(f"⚠️ **Abweichung identifiziert**: Summe der Treiber ({sum_drivers}) != Netto-Delta ({total_raw_delta:.0f}). Differenz: {residual:.0f}")
                        unmapped = combined_events_in_scope[~combined_events_in_scope["type"].isin(["Azubi_Hire", "Trainee_Hire", "New_Hire", "Azubi_Conversion_Out", "Azubi_Conversion_In", "Azubi_Exit"])]
                        # Also exclude events already in filt_abg_events (which we mapped by reason)
                        unmapped = unmapped[~unmapped["persnr"].isin(filt_abg_events["persnr"])]
                        if not unmapped.empty:
                            st.write("Nicht kategorisierte Events:")
                            st.dataframe(unmapped[["event_date", "type", "reason_label", "source", "headcount_change"]], use_container_width=True)
                    else:
                        st.success("✅ Treiber-Abstimmung erfolgreich (Residual = 0).")

                    st.divider()
                    st.markdown("#### 🔢 Aggregierte Tabellen (Audit)")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        _render_debug_aggregation(
                            combined_events_in_scope, 
                            ["reason_label"], 
                            "Aggregation nach Treiber", 
                            count_col="headcount_change", 
                            mak_col="mak_change",
                            key_prefix="net_debug"
                        )
                    with c2:
                        _render_debug_aggregation(
                            combined_events_in_scope, 
                            ["Organisationseinheit"], 
                            "Top Netto-Effekte nach OE", 
                            count_col="headcount_change", 
                            mak_col="mak_change",
                            key_prefix="net_debug"
                        )
                        
                    st.divider()
                    st.markdown("#### 🔍 Detail-Checks")
                    
                    # Check A: Rente nach ATZ Logic
                    st.markdown("**Check: Rente (nach ATZ)**")
                    rente_atz = combined_events_in_scope[
                        (combined_events_in_scope["type"] == "Retirement") & 
                        (combined_events_in_scope["source"] == "ATZ")
                    ]
                    if not rente_atz.empty:
                        st.write(f"Gefundene Events: `{len(rente_atz)}`")
                        st.dataframe(rente_atz[["event_date", "persnr", "headcount_change", "mak_change", "Organisationseinheit"]].head(5), use_container_width=True)
                    else:
                        st.caption("Keine 'Retirement_ATZ' Events im Scope.")
                    
                    # Check C: MAK/Kopf-Konsistenz (Fix: MAK/Headcount Divergence)
                    st.markdown("**Check: MAK/Kopf-Konsistenz (Vorzeichen & Betrag)**")

                    # Check D: Azubi Conversion Consistency (Fix: Netto Mismatches)
                    st.markdown("**Check D: Azubi Conversion Consistency (Out vs In)**")
                    
                    # 1. Isolate relevant events
                    azu_out = combined_events_in_scope[combined_events_in_scope["type"] == "Azubi_Conversion_Out"]
                    azu_in = combined_events_in_scope[combined_events_in_scope["type"] == "Azubi_Conversion_In"]
                    
                    if not azu_out.empty and not azu_in.empty:
                        # 2. Join on PersNr
                        azu_merged = pd.merge(
                            azu_out[["persnr", "Organisationseinheit", "OE-Cluster", "Jobfamily"]],
                            azu_in[["persnr", "Organisationseinheit", "OE-Cluster", "Jobfamily"]],
                            on="persnr",
                            suffixes=("_out", "_in")
                        )
                        
                        # 3. Check for Dimension Mismatches
                        # We expect Out-Unit == In-Unit (for Baseline) mostly, but critical is if retention happens in same unit.
                        # Actually, forecast logic might CHANGE unit for logic reasons (e.g. strategy).
                        # BUT for Baseline, we forced it to stay.
                        # Strict Check: Do we have cases where Unit DIFFERS?
                        
                        mismatch_unit = azu_merged[azu_merged["Organisationseinheit_out"] != azu_merged["Organisationseinheit_in"]]
                        mismatch_cluster = azu_merged[azu_merged["OE-Cluster_out"] != azu_merged["OE-Cluster_in"]]
                        
                        if mismatch_unit.empty and mismatch_cluster.empty:
                            st.caption("✅ Keine Inkonsistenzen bei Azubi-Übernahmen gefunden.")
                        else:
                            if not mismatch_unit.empty:
                                st.warning(f"⚠️ **OE-Wechsel bei Übernahme**: {len(mismatch_unit)} Fälle. (Wird akzeptiert, solange Cluster gleich bleibt)")
                                st.dataframe(mismatch_unit, use_container_width=True)
                                
                            if not mismatch_cluster.empty:
                                st.error(f"❌ **Cluster-Wechsel bei Übernahme**: {len(mismatch_cluster)} Fälle. (Muss 0 sein!)")
                                st.dataframe(mismatch_cluster, use_container_width=True)
                    else:
                        st.caption("Keine Azubi-Conversions im Scope für Check D.")

                    st.markdown("**Check: MAK/Kopf-Konsistenz (Vorzeichen & Betrag)**")
                    
                    # Ensure numeric types for comparison (Fix: TypeError on round)
                    hc_numeric = pd.to_numeric(combined_events_in_scope["headcount_change"], errors="coerce").fillna(0)
                    mak_numeric = pd.to_numeric(combined_events_in_scope["mak_change"], errors="coerce").fillna(0)
                    
                    # Task 2.1: Refined MAK Validation Logic
                    # A. Whitelist: ATZ Phase-Transfer (HC=0 is correct)
                    from abgaenge.schemas import REASON_ATZ_AR_TO_FR
                    mask_check = ~combined_events_in_scope["reason_code"].isin([REASON_ATZ_AR_TO_FR])
                    
                    # B. Refined HC-Exit rule: 
                    # If HC == -1, then MAK must be in [-1.05, 0]
                    # We flag if (HC == -1 AND (MAK > 0 OR MAK < -1.05))
                    hc_exit_mask = (hc_numeric == -1)
                    hc_exit_invalid = hc_exit_mask & ((mak_numeric > 0) | (mak_numeric < -1.05))
                    
                    # C. Generic Mismatch (for non-exits)
                    # For non-exits (e.g., hires), we still expect HC and MAK to have same sign and roughly same val
                    non_exit_check = (~hc_exit_mask) & (mask_check) & (hc_numeric != 0)
                    non_exit_invalid = non_exit_check & (
                        (np.sign(hc_numeric) != np.sign(mak_numeric)) |
                        (abs(hc_numeric).round(1) != abs(mak_numeric).round(1))
                    )
                    
                    mismatch = combined_events_in_scope[hc_exit_invalid | non_exit_invalid]
                    
                    if not mismatch.empty:
                        st.warning(f"⚠️ {len(mismatch)} Events mit inkonsistenter MAK/Kopf-Änderung (signifikant).")
                        st.dataframe(mismatch[["event_date", "type", "persnr", "headcount_change", "mak_change", "source", "reason_label"]], use_container_width=True)
                        st.caption("ℹ️ Ein Event gilt als inkonsistent, wenn bei Köpfe -1 die MAK > 0 oder < -1.05 ist, oder wenn bei Zugängen Vorzeichen/Betrag nicht passen.")
                    else:
                        st.success("✅ Sämtliche Köpfe-Deltas sind konsistent mit MAK-Deltas (bereinigt).")
                    oe_stats = combined_events_in_scope.groupby("Organisationseinheit")[["headcount_change", "mak_change"]].sum()
                    suspicious = oe_stats[
                        (oe_stats["headcount_change"] < 0) & 
                        (oe_stats["mak_change"] > 0.001)
                    ]
                    if not suspicious.empty:
                        st.warning(f"⚠️ {len(suspicious)} OEs mit gegenläufigen Vorzeichen gefunden.")
                        st.dataframe(suspicious, use_container_width=True)
                        
                        # Show details for first suspicious OE
                        sus_oe = suspicious.index[0]
                        st.markdown(f"**Details für OE:** `{sus_oe}`")
                        sus_events = combined_events_in_scope[combined_events_in_scope["Organisationseinheit"] == sus_oe]
                        st.dataframe(sus_events[["event_date", "type", "source", "headcount_change", "mak_change", "Jobfamily"]], use_container_width=True)
                    else:
                        st.caption("✅ Keine OEs mit gegenläufigen Vorzeichen (Kopf < 0 / MAK > 0) gefunden.")
    
                else:
                    st.write("Keine Daten für Debugging.")

    with t_abg_res:
        st.markdown(t("hybrid.attrition.heading"))
        abg_charts = _build_hybrid_abgaenge_chart_bundle(abg_view_kpis, filt_abg_events)
        st.plotly_chart(abg_charts.get("line_headcount_mak"), use_container_width=True, key="hybrid_abg_line_chart")
        st.plotly_chart(abg_charts.get("bar_abgaenge_reasons"), use_container_width=True, key="hybrid_abg_reasons_chart")
        
        if cluster_is_active and "OE-Cluster" in filt_abg_events.columns:
             # Cluster chart logic
             c_stats = _build_hybrid_abgaenge_cluster_source(filt_abg_events)
             fig_c = px.bar(c_stats, x="Abgänge", y="OE-Cluster", orientation="h", title="Abgänge nach OE-Cluster")
             st.plotly_chart(fig_c, use_container_width=True, key="hyb_abg_oe_cluster_main")

    
        if st.session_state.get("debug_active", False):
             with st.expander("🐞 Debug / Plausibilitätschecks (Abgänge)", expanded=False):
                 st.markdown("#### Validierung Abgänge")
                 if not filt_abg_events.empty:
                      # Reasons Chart (Usually Counts Events or Heads)
                      # Fix B: Standardize on Unique Persons for "Köpfe" check
                      # The 'chart_reasons_sum' in previous code used 'headcount_change'.sum() which includes multiple phases.
                      # We want Unique Persons.
                      
                      unique_leavers = _get_unique_count(filt_abg_events[filt_abg_events["headcount_change"] < 0])
                      
                      # The chart (bar_abgaenge_reasons) usually shows *Cases* (Events). 
                      # If we want to check Consistency, we must know what the chart shows.
                      # 'bar_abgaenge_reasons' typically shows Sum of Headcount Loss (-1).
                      # If one person retires twice (ATZ Phase), they count twice? No, specific logic.
                      # We'll compare Event Count vs Event Count for robustness.
                      
                      chart_reasons_sum = abs(filt_abg_events[filt_abg_events["headcount_change"] < 0]["headcount_change"].sum())
                      # Compare against itself? No, against the filtered event set (our source of truth).
                      ref_reasons_sum = abs(filt_abg_events[filt_abg_events["headcount_change"] < 0]["headcount_change"].sum())
                      
                      _check_sum("Abgänge (Events): Chart vs Source", chart_reasons_sum, ref_reasons_sum, "Events")
                      
                      # Unique Persons Check
                      st.write(f"ℹ️ Unique Personen (Abgänge): `{unique_leavers}`")
                      
                      # Cluster Chart
                      if cluster_is_active and "OE-Cluster" in filt_abg_events.columns:
                          # Cluster chart counts rows (Events)
                          chart_cluster_sum = c_stats["Abgänge"].sum()
                          _check_sum("OE-Cluster Summe vs Event Count", chart_cluster_sum, chart_reasons_sum, "Events")
                          
                      st.divider()
                      st.markdown("#### 🔢 Aggregierte Tabellen (Audit)")
                      
                      ac1, ac2 = st.columns(2)
                      with ac1:
                          _render_debug_aggregation(filt_abg_events, ["reason_label"], "Nach Grund", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
                          _render_debug_aggregation(filt_abg_events, ["Jobfamily"], "Nach JobFamily", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
                      with ac2:
                          _render_debug_aggregation(filt_abg_events, ["OE-Cluster"], "Nach OE-Cluster", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
                          _render_debug_aggregation(filt_abg_events, ["Organisationseinheit"], "Nach Organisationseinheit (Top 20)", count_col="headcount_change", mak_col="mak_change", key_prefix="abg_debug")
    
                 else:
                      st.write("Keine Abgangsdaten.")

    with t_zug_res:
        st.markdown(t("hybrid.hiring.heading"))
        zug_chart_sources = _build_hybrid_zugaenge_chart_sources(filt_zug_events)
        events_chart = zug_chart_sources["events_chart"]

        if not events_chart.empty:
            fig_sources = px.histogram(
                events_chart, 
                x="date", 
                color="Quelle", 
                text_auto=True, 
                title="Zugänge nach Quelle",
                color_discrete_map={
                    "Neue Auszubildende": COLORS.get("accent_blue", "#1f77b4"),
                    "Übernahme aus Ausbildung": "#9467bd", 
                    "Neueinstellung": COLORS.get("accent_green", "#2ca02c"),
                    "Trainee": COLORS.get("accent_orange", "#ff7f0e")
                }
            )
            fig_sources.update_layout(xaxis_title="Datum", yaxis_title="Anzahl")
            st.plotly_chart(fig_sources, use_container_width=True, key="hybrid_zug_sources_chart")
            
            # 4. Erklärung
            st.caption(
                "Übernahmen stellen interne Stellenbesetzungen dar und erhöhen den MAK. "
                "Neue Auszubildende werden während der Ausbildung nicht zur MAK gezählt."
            )
        else:
            st.info(t("hybrid.hiring.no_events"))
            
            if cluster_is_active and "OE-Cluster" in filt_zug_events.columns:
                 z_stats = zug_chart_sources["z_stats"]
                 fig_z = px.bar(z_stats, x="Zugänge", y="OE-Cluster", orientation="h", title="Zugänge nach OE-Cluster", color_discrete_sequence=[COLORS["accent_green"]])
                 st.plotly_chart(fig_z, use_container_width=True, key="hybrid_zug_oe_cluster_chart")



    
        if st.session_state.get("debug_active", False):
            with st.expander("🐞 Debug / Plausibilitätschecks (Zugänge)", expanded=False):
                st.markdown("#### Validierung Zugänge")
                if not filt_zug_events.empty:
                    # Sources Chart
                    # Filter for actual entries (count > 0)
                    real_entries = filt_zug_events[filt_zug_events["count"] > 0]
                    
                    # Chart shows Count of Raws (Events)
                    chart_sources_sum = real_entries["count"].sum()
                    
                    # Ref: Unique Persons or Event Count? 
                    # If we assume 1 Entry Event per Person is standard -> Events == Heads.
                    ref_entries_sum = real_entries["count"].sum()
                    
                    _check_sum("Zugänge (Events): Sources vs Source", chart_sources_sum, ref_entries_sum, "Events")
                    
                    unique_entries = _get_unique_count(real_entries)
                    st.write(f"ℹ️ Unique Personen (Zugänge): `{unique_entries}`")
                    
                    # Cluster Chart
                    if cluster_is_active and "OE-Cluster" in filt_zug_events.columns:
                        # Check for Unclustered
                        unclustered_count = len(filt_zug_events[(filt_zug_events["OE-Cluster"] == "Unclustered") | (filt_zug_events["OE-Cluster"] == "Sonstiges")])
                        # Note: After fix, "Unclustered" should never appear.

                    st.divider()
                    st.markdown("#### 🚦 Treiber-Status Check")
                    # Check if Gating works
                    zug_params = st.session_state.get("hybrid_zug_params", {})
                    
                    # Count by broad type match
                    n_azubi = len(filt_zug_events[filt_zug_events["type"].astype(str).str.contains("Azubi", case=False)])
                    n_trainee = len(filt_zug_events[filt_zug_events["type"].astype(str).str.contains("Trainee", case=False)])
                    n_hire = len(filt_zug_events[filt_zug_events["type"] == "New_Hire"])
                    
                    status_data = [
                        {"Treiber": "Azubis", "Aktiv (UI)": zug_params.get("azubi", {}).get("active", "?"), "Events": n_azubi},
                        {"Treiber": "Trainees", "Aktiv (UI)": zug_params.get("trainee", {}).get("active", "?"), "Events": n_trainee},
                        {"Treiber": "Neueinstellungen", "Aktiv (UI)": zug_params.get("new_hires", {}).get("active", "?"), "Events": n_hire},
                    ]
                    st.dataframe(pd.DataFrame(status_data), use_container_width=True)
                        
                    if cluster_is_active and "OE-Cluster" in filt_zug_events.columns:
                        chart_z_cluster_sum = filt_zug_events.groupby("OE-Cluster").size().sum()
                        _check_sum("OE-Cluster Summe vs Total Events", chart_z_cluster_sum, len(filt_zug_events), "Events")
                    
                    st.divider()
                    st.markdown("#### 🎓 Azubi-Logik Drill-Down")
                    # Detailed analysis for Azubis
                    az_events = filt_zug_events[filt_zug_events["type"].astype(str).str.contains("Azubi", case=False)]
                    if not az_events.empty:
                        # Enhanced Audit Table (id: 16)
                        st.write("📋 **Detaillierte Azubi-Audit-Tabelle (Einzelschicksale)**")
                        audit_cols = ["persnr", "type", "entry_date", "graduation_date", "is_forecast", "source_pool", "cohort", "headcount_change", "mak_change"]
                        # Filter cols that exist
                        actual_cols = [c for c in audit_cols if c in az_events.columns]
                        
                        # Apply subtle deduplication for conversion view (Issue C)
                        # We keep both for raw HC tracking, but for audit view, we can collapse them
                        collapsed_audit = az_events[actual_cols].copy()
                        collapsed_audit = collapsed_audit.sort_values(["entry_date", "is_forecast", "persnr"])
                        
                        st.dataframe(collapsed_audit, use_container_width=True)

                        # Forecast Cohort Overview (Issue D)
                        st.write("📊 **Forecast-Azubi Kohorten Übersicht**")
                        f_hires_df = az_events[(az_events["type"] == "Azubi_Hire") & (az_events["is_forecast"] == True)].copy()
                        if not f_hires_df.empty:
                            # Check for grads
                            grad_events = az_events[az_events["type"].isin(["Azubi_Conversion_In", "Azubi_Exit"]) & (az_events["is_forecast"] == True)]
                            
                            cohort_data = []
                            for _, h_row in f_hires_df.iterrows():
                                pid = h_row["persnr"]
                                h_date = h_row["entry_date"]
                                g_date = h_row["graduation_date"]
                                
                                # Check if graduation event exists in scope
                                has_grad = grad_events[grad_events["persnr"] == pid]
                                
                                cohort_data.append({
                                    "Person ID": pid,
                                    "Entry Date": h_date,
                                    "Expected Graduation": g_date,
                                    "In Scope Hire": True,
                                    "In Scope Graduation": not has_grad.empty
                                })
                            st.dataframe(pd.DataFrame(cohort_data), use_container_width=True)
                        else:
                            st.info("Keine Forecast-Azubis in diesem Zeitraum.")

                        # Assertions & Metrics (Issue D)
                        st.write("⚖️ **Lifecycle-Validierung (Harte Checks & Proof)**")
                        
                        f_hires = len(az_events[(az_events["type"] == "Azubi_Hire") & (az_events["is_forecast"] == True)])
                        f_convs = len(az_events[(az_events["type"] == "Azubi_Conversion_In") & (az_events["is_forecast"] == True)])
                        f_exits = len(az_events[(az_events["type"] == "Azubi_Exit") & (az_events["is_forecast"] == True)])
                        f_grads = f_convs + f_exits
                        
                        b_hires = len(az_events[(az_events["type"] == "Azubi_Hire") & (az_events["is_forecast"] == False)])
                        b_convs = len(az_events[(az_events["type"] == "Azubi_Conversion_In") & (az_events["is_forecast"] == False)])
                        b_exits = len(az_events[(az_events["type"] == "Azubi_Exit") & (az_events["is_forecast"] == False)])
                        b_grads = b_convs + b_exits

                        c1, c2 = st.columns(2)
                        with c1:
                            st.metric("Completions Forecast (completions_forecast_count)", f_grads)
                            st.metric("Completions Baseline (completions_base_count)", b_grads)
                        
                        # Backend Regression Check (id: 16)
                        if "debug_info" in zug_res:
                            di = zug_res["debug_info"]
                            if "completions_baseline_count" in di:
                                c2.markdown("#### ⚙️ Engine Internals")
                                c2.write(f"Engine Baseline Completions: **{di['completions_baseline_count']}**")
                                c2.write(f"Engine Forecast Completions: **{di['completions_forecast_count']}**")
                                if "isolation_error" in di:
                                    c2.error(di["isolation_error"])
                                elif params_zug.get("azubi", {}).get("exclude_baseline_azubis", False):
                                    c2.success("✅ Isolation Verified by Engine")
                        
                        errors = []
                        # 1. Assertion: completions_forecast_in_scope == 0 for 2 yrs window / 3 yrs duration (Heuristic check)
                        duration = params_zug.get("azubi", {}).get("duration_years", 3.0)
                        start_ts = pd.Timestamp(ist_stichtag)
                        end_ts = pd.Timestamp(forecast_end_date)
                        window_years = (end_ts - start_ts).days / 365.25
                        
                        if window_years < duration and f_grads > 0:
                            # Find the problematic ones
                            prob_ids = az_events[az_events["type"].isin(["Azubi_Conversion_In", "Azubi_Exit"]) & (az_events["is_forecast"] == True)]["persnr"].unique()
                            errors.append(f"❌ **Assertion Failed**: Forecast-Completions > 0 ({f_grads}) im Zeitfenster {window_years:.1f}J bei Dauer {duration}J! Betroffene IDs: {prob_ids}")

                        # 2. Assertion: unique_persons(grads) <= unique_persons(hires)
                        unique_grads = az_events[az_events["type"].isin(["Azubi_Conversion_In", "Azubi_Exit"]) & (az_events["is_forecast"] == True)]["persnr"].nunique()
                        if unique_grads > f_hires:
                            errors.append(f"❌ **Assertion Failed**: Mehr Absolventen ({unique_grads}) als Neueinstellungen ({f_hires})!")

                        # 3. Assertion: No grad_date < hire_date + duration
                        if f_grads > 0:
                            # Re-verify duration logic per ID
                            for pid in az_events[az_events["is_forecast"] == True]["persnr"].unique():
                                p_events = az_events[az_events["persnr"] == pid]
                                h_e = p_events[p_events["type"] == "Azubi_Hire"]
                                g_e = p_events[p_events["type"].isin(["Azubi_Conversion_In", "Azubi_Exit"])]
                                if not h_e.empty and not g_e.empty:
                                    h_date = pd.to_datetime(h_e.iloc[0]["entry_date"])
                                    act_g_date = pd.to_datetime(g_e.iloc[0]["date"])
                                    expected_min = h_date + pd.DateOffset(years=int(duration))
                                    if act_g_date < expected_min:
                                        errors.append(f"❌ **Assertion Failed**: Abschluss vorzeitig für {pid} ({act_g_date.date()} < {expected_min.date()})")

                        # ID Uniqueness Check
                        unique_hires_ids = az_events[az_events["type"] == "Azubi_Hire"]["persnr"].nunique()
                        if unique_hires_ids != f_hires:
                            errors.append(f"❌ **Fehler**: Hires haben keine eindeutigen IDs ({unique_hires_ids} unique IDs vs {f_hires} Events)!")
                        
                        # Status Check per ID (Deduplication check)
                        ids_with_multi_grad = []
                        grad_events_only = az_events[az_events["type"].isin(["Azubi_Conversion_In", "Azubi_Exit"])]
                        grad_counts = grad_events_only.groupby("persnr").size()
                        multi_grad_ids = grad_counts[grad_counts > 1].index.tolist()
                        if multi_grad_ids:
                            errors.append(f"❌ **Fehler**: {len(multi_grad_ids)} Azubis haben mehr als einen Abschluss (z.B. {multi_grad_ids[:3]})!")
                        
                        if not errors:
                            st.success("✅ Sämtliche Lifecycle-Assetions erfolgreich erfüllt.")
                        else:
                            for err in errors:
                                st.error(err)
                        
                        st.divider()
                        st.write("📊 **Azubi Lebenszyklus-Analyse (Bestand vs. Prognose)**")
                        # Use a copy to avoid SettingWithCopyWarning
                        az_analysis = az_events.copy()
                        # Pivot by Year and IsForecast
                        az_analysis['Year'] = pd.to_datetime(az_analysis['date']).dt.year
                        az_analysis['Herkunft'] = az_analysis['is_forecast'].map({True: "Prognose", False: "Bestand"}).fillna("Bestand")
                        pivot_lifecycle = az_analysis.pivot_table(
                            index="Year",
                            columns=["Herkunft", "type"],
                            values="count",
                            aggfunc="sum",
                            fill_value=0
                        )
                        st.dataframe(pivot_lifecycle, use_container_width=True)

                        st.info("ℹ️ **Azubi_Hire**: Azubi Neueinstellung (externer Zugang).\n\n**Azubi_Conversion_In/Out**: Azubi-Abschluss: Übernahme (Umwandlung) (Netto 0 HC).\n\n**Azubi_Exit**: Azubi-Abschluss: Nichtübernahme (Abgang).")
                    else:
                        st.write("Keine Azubi-Events im Scope.")

                    # Out-of-Scope Azubis (Fix D)
                    raw_zug = st.session_state.get("hybrid_zug_res", {}).get("events", pd.DataFrame())
                    if not raw_zug.empty:
                        raw_az = raw_zug[raw_zug["type"].astype(str).str.contains("Azubi", case=False)]
                        if not raw_az.empty:
                            st_date = pd.Timestamp(ist_stichtag)
                            en_date = pd.Timestamp(forecast_end_date)
                            out_az = raw_az[(raw_az["date"] < st_date) | (raw_az["date"] > en_date)]
                            if not out_az.empty:
                                st.warning(f"⚠️ **{len(out_az)} Azubi-Events liegen außerhalb des Zeitfensters**")
                                out_az_annual = out_az.groupby([out_az['date'].dt.year, 'type']).size().reset_index(name='Out-of-Scope Count')
                                st.table(out_az_annual)

                    # Scope Analysis (Filterverlust)
                    st.divider()
                    st.markdown("#### 🔭 Scope-Analyse & Filterverlust")
                    raw_zug = st.session_state.get("hybrid_zug_res", {}).get("events", pd.DataFrame())
                    if not raw_zug.empty:
                        total_raw = len(raw_zug)
                        in_scope = len(filt_zug_events)
                        loss = total_raw - in_scope
                        
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Total Events", total_raw)
                        col2.metric("In Scope", in_scope)
                        col3.metric("Filterverlust", loss)
                        
                        if loss > 0:
                            # Identify the filtered events
                            # We assume filtering was by date and attributes
                            # Let's show events that are outside the date range specifically
                            st_date = pd.Timestamp(ist_stichtag)
                            en_date = pd.Timestamp(forecast_end_date)
                            
                            date_filter_loss = raw_zug[(raw_zug["date"] < st_date) | (raw_zug["date"] > en_date)]
                            if not date_filter_loss.empty:
                                st.write(f"⚠️ **{len(date_filter_loss)} Events liegen außerhalb des Zeitraums** ({st_date.strftime('%Y-%m-%d')} bis {en_date.strftime('%Y-%m-%d')}):")
                                # Summary of loss by type
                                loss_summary = date_filter_loss.groupby(['type', date_filter_loss['date'].dt.year]).size().reset_index(name='Count')
                                st.dataframe(loss_summary, use_container_width=True)
                                
                                if st.checkbox("Details zu Filterverlust anzeigen"):
                                    st.dataframe(date_filter_loss[["date", "type", "source", "persnr"]], use_container_width=True)
                        else:
                            st.success("Alle generierten Events liegen innerhalb des gewählten Zeitraums.")
                
                    st.divider()
                    st.markdown("#### 🔢 Aggregierte Tabellen (Audit)")
                    
                    zc1, zc2 = st.columns(2)
                    with zc1:
                        _render_debug_aggregation(filt_zug_events, ["source", "type"], "Nach Quelle & Typ", count_col="count", mak_col="mak", key_prefix="zug_debug")
                        _render_debug_aggregation(filt_zug_events, ["Jobfamily"], "Nach JobFamily", count_col="count", mak_col="mak", key_prefix="zug_debug")
                    with zc2:
                        _render_debug_aggregation(filt_zug_events, ["OE-Cluster"], "Nach OE-Cluster", count_col="count", mak_col="mak", key_prefix="zug_debug")
                        _render_debug_aggregation(filt_zug_events, ["Organisationseinheit"], "Nach Organisationseinheit (Top 20)", count_col="count", mak_col="mak", key_prefix="zug_debug")

                    # --- JF-Cluster Enrichment Debug (shared with Page 4) ---
                    st.divider()
                    st.markdown("#### JF-Cluster Enrichment Diagnose")
                    zug_params = st.session_state.get("hybrid_zug_params", final_params_zug)
                    render_zugaenge_debug_sections(filt_zug_events, zug_params, df_ma)

                else:
                    st.write("Keine Zugangsdaten.")

    with t_list:
        st.markdown(t("hybrid.list.heading"))
        st.dataframe(combined_events[["event_date", "reason_label", "headcount_change", "mak_change", "Organisationseinheit", "Jobfamily"]], use_container_width=True)
        st.download_button("📥 Gesamte Liste exportieren (CSV)", data=to_csv_bytes(combined_events), file_name="hybrid_prognose_details.csv")


main()
