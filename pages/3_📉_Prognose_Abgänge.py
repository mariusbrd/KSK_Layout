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

from abgaenge import (
    default_params,
    build_params_from_ui,
    run_forecast_abgaenge,
    aggregate_forecast_results,
    validate_outputs,
    build_charts,
    to_csv_bytes,
)

# Shared Components
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from dataloader.cluster_manager import load_cluster_mappings_from_source, resolve_forecast_ui_dimensions
from dataloader.cluster_resolver import (
    deserialize_active_cluster_source,
    get_active_cluster_source,
    invalidate_cluster_dependent_state,
)
from dataloader.jobfamily_service import JobFamilyService
from components.sidebar import render_global_filters, apply_filters, apply_event_filters, render_filter_status, apply_robust_filter, get_effective_metric_view, set_metric_page_hint
from components.ui_shell import render_context_box, render_page_header, render_section_intro
from utils.i18n import t
from utils.matrix_helpers import migrate_to_percent
from utils.plot_helpers import apply_legend_bottom


def _cluster_widget_key(base_key: str, cluster_source_signature: Optional[str]) -> str:
    suffix = str(cluster_source_signature or "no_cluster_source").strip() or "no_cluster_source"
    return f"{base_key}_{suffix}"


def _sync_params_matrix_dimensions(
    params: Dict[str, Any],
    valid_jfs: list[str],
    active_org_units: list[str],
) -> Dict[str, Any]:
    """Reconcile ATZ- und Kündigung-Matrix-Zeilen mit den aktuellen Cluster-Gruppen.

    - Bestehende Werte für weiterhin vorhandene Gruppen bleiben erhalten.
    - Neu hinzugekommene Gruppen erhalten den aktuellen Default-Wert der Matrix.
    - Nicht mehr vorhandene Gruppen werden entfernt.
    - Der „Default"-Schlüssel wird immer beibehalten.
    Alle Werte bleiben im Gewichtungsmaßstab (0–1), da params aus dem Session-State
    via build_params_from_ui stammt.
    """
    # ATZ-Matrix
    atz_dim = params["atz"].get("atz_dimension", "JobFamily")
    current_groups = valid_jfs if atz_dim == "JobFamily" else active_org_units
    old_atz = params["atz"].get("atz_matrix", {"Default": params["atz"].get("new_atz_rate", 0.05)})
    default_atz = float(old_atz.get("Default", params["atz"].get("new_atz_rate", 0.05)))
    new_atz: Dict[str, Any] = {"Default": old_atz.get("Default", default_atz)}
    for grp in current_groups:
        new_atz[str(grp)] = old_atz.get(str(grp), default_atz)
    params["atz"]["atz_matrix"] = new_atz

    # Kündigung-Matrix
    quit_dim = params["quit"].get("quit_dimension", "JobFamily")
    current_groups_q = valid_jfs if quit_dim == "JobFamily" else active_org_units
    old_quit = params["quit"].get("quit_matrix", {})
    _age_defaults: Dict[str, float] = {
        "alter_unter_30": 0.12,
        "alter_30_45": 0.08,
        "alter_45_55": 0.05,
        "alter_55_plus": 0.02,
    }
    new_quit: Dict[str, Any] = {}
    for cohort, fallback_rate in _age_defaults.items():
        old_cohort = old_quit.get(cohort, {})
        cohort_default = float(old_cohort.get("Default", fallback_rate))
        new_cohort: Dict[str, Any] = {"Default": cohort_default}
        for grp in current_groups_q:
            new_cohort[str(grp)] = old_cohort.get(str(grp), cohort_default)
        new_quit[cohort] = new_cohort
    params["quit"]["quit_matrix"] = new_quit

    return params


def _get_param_dimension_values(
    df_ma: pd.DataFrame,
    active_cluster_source,
    cluster_mapping_bundle,
) -> tuple[list[str], list[str]]:
    resolved = resolve_forecast_ui_dimensions(
        df_ma,
        active_cluster_source,
        mapping_bundle=cluster_mapping_bundle,
    )
    return resolved.org_units, resolved.job_family_clusters


def _render_page_intro():
    st.title(t("attrition.title"))
    st.caption(t("attrition.subtitle"))


def _get_result_tab_labels() -> list[str]:
    return [
        t("attrition.tabs.overview"),
        t("attrition.tabs.drivers"),
        t("attrition.tabs.export"),
    ]


def _get_attrition_cluster_context(summary: Optional[dict[str, Any]]):
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


def _clear_stale_abgaenge_results():
    for key in [
        "abgaenge_results",
        "abgaenge_global_result",
        "abgaenge_params",
        "abgaenge_ui_state",
        "abgaenge_timestamp",
        "abgaenge_cluster_source_signature",
    ]:
        st.session_state.pop(key, None)


def _abgaenge_results_match_cluster_signature(current_cluster_source_signature: Optional[str]) -> bool:
    if "abgaenge_global_result" not in st.session_state:
        return False
    return st.session_state.get("abgaenge_cluster_source_signature") == current_cluster_source_signature


def main():
    _render_page_intro()
    metric_choice, metric_hint = get_effective_metric_view(["Köpfe", "MAK"], fallback="MAK")
    if metric_hint:
        set_metric_page_hint(t("attrition.metric_hint"))
    else:
        set_metric_page_hint(None)

    try:
        # 1. Load Central Data (Consistent with Kompakt)
        # Loader automatically picks up 'global_uploads' from session_state (see loader.py)
        snapshot_df, history_df, _, summary = load_and_prepare_data()
        (
            active_cluster_source,
            cluster_mapping_bundle,
            cluster_source_signature,
            cluster_is_active,
        ) = _get_attrition_cluster_context(summary)

        # 2. Render Sidebar Filters
        render_global_filters(snapshot_df, history_df)
        
        # --- Sidebar Reset Button ---
        with st.sidebar:
            st.divider()
            if st.button(t("attrition.reset_results"), use_container_width=True, help=t("attrition.reset_results.help")):
                _clear_stale_abgaenge_results()
                st.rerun()
        
        # 3. GLOBAL DATA PREPARATION (Filters applied LATER for View)
        # We skip apply_filters(snapshot_df) here to ensure Global Forecast.
        df_ma = snapshot_df.copy()

        # 4. Load ATZ Details (needed for engine phases)
        # Get uploads from session_state for specific loading
        global_uploads = st.session_state.get("global_uploads", {})
        up_ma_arg = global_uploads.get("Mitarbeiter")
        up_atz_arg = global_uploads.get("ATZ")
        up_pl_arg = global_uploads.get("Planstellen")
        
        if up_ma_arg: up_ma_arg.seek(0)
        if up_atz_arg: up_atz_arg.seek(0)
        if up_pl_arg: up_pl_arg.seek(0)
        
        df_atz = load_atz_data_cached(str(BASE_PATH), up_ma_arg, up_atz_arg, up_pl_arg)
        
        # 5. Preprocessing for Forecast Engine (GLOBAL)
        # The snapshot_df is position-level data. Employees with multiple positions
        # contribute MAK from each position (e.g., 2x 50% = 1.0 FTE total).
        # The forecast engine expects employee-level data (1 row per person).
        
        # Remove Vacancies
        df_ma = df_ma.dropna(subset=["PersNr"])
        
        # Calculate MAK for each position (same logic as Kompakt)
        from dataloader.loader import berechne_mak, calculate_mak_vectorized
        
        # Get ATZ FR employees if available (for MAK calculation)
        atz_fr_persnr_set = set()
        if not df_atz.empty:
            if "PersNr" in df_atz.columns and "Phase" in df_atz.columns:
                # People currently in Freistellungsphase have MAK = 0
                stichtag_ts = pd.Timestamp(get_current_stichtag())
                atz_fr = df_atz[
                    (df_atz["Phase"] == "FR") &
                    (df_atz["Beginn"] <= stichtag_ts) &
                    (df_atz["Ende"] >= stichtag_ts)
                ]
                if not atz_fr.empty:
                    atz_fr_persnr_set = set(atz_fr["PersNr"].dropna().astype(str).unique())
        
        # Calculate MAK for each row (position) - Vectorized!
        df_ma = calculate_mak_vectorized(df_ma, atz_fr_persnr_set)
        
        # Aggregate by employee: sum MAK, keep first occurrence of other attributes
        agg_dict = {
            "MAK_Calculated": "sum",  # Sum MAK across all positions (from calculate_mak_vectorized)
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Sollarbeitszeit": "sum",  # Sum work hours across positions
            "Organisationseinheit": "first", # Preserve OrgUnit for Analytics
        }

        # Optional: include other columns if they exist
        for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St", "OE-Cluster", "JF-Cluster"]:
            if col in df_ma.columns:
                agg_dict[col] = "first"
        
        # Performance Optimization: Aggregate using groupby
        df_employee_agg = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
        
        # Standardize column name for subsequent logic
        df_employee_agg["mak"] = df_employee_agg["MAK_Calculated"]
        # IMPORTANT: Keep MAK_Calculated for forecast engine (it checks for this specific column)
        # df_employee_agg = df_employee_agg.rename(columns={"MAK_Calculated": "mak"})
        
        # 1. Ensure Sollarbeitszeit is present (fallback 39.0)
        df_employee_agg["Sollarbeitszeit"] = df_employee_agg["Sollarbeitszeit"].fillna(39.0)
        
        # Backcalculate BsGrd from aggregated MAK for engine compatibility
        # Forecast Engine Logic: MAK = (BsGrd/100) * (Soll/39)
        # We want: MAK = MAK_Calculated
        # So we set Soll = 39.0 (Factor=1) and BsGrd = MAK_Calculated * 100
        
        # 1. Neutralize Soll-Factor in Engine
        df_employee_agg["Sollarbeitszeit"] = 39.0
        
        # 2. Set BsGrd to match desired MAK exactly
        # FIX: If 'mak' is 0.0 (e.g. Is_Vacant), try to derive from BsGrd/Soll if available
        # This handles cases where user wants to see "Potential" loss of a slot even if currently vacant/ruhend
        mask_zero = (df_employee_agg["mak"] <= 0)
        if mask_zero.any():
            if "BsGrd" in df_employee_agg.columns:
                 potential_mak = df_employee_agg.loc[mask_zero, "BsGrd"] / 100.0
                 df_employee_agg.loc[mask_zero, "mak"] = potential_mak.fillna(0.0)
                 # CRITICAL: Also update MAK_Calculated because forecast.py checks this specifically!
                 df_employee_agg.loc[mask_zero, "MAK_Calculated"] = df_employee_agg.loc[mask_zero, "mak"]

        df_employee_agg["BsGrd"] = df_employee_agg["mak"] * 100.0
        
        df_employee_agg["BsGrd"] = df_employee_agg["mak"] * 100.0
        
        # Use aggregated data for forecast
        df_ma = df_employee_agg

        # P07: Resolve active UI dimensions from the active cluster source.
        active_org_units, valid_jfs = _get_param_dimension_values(
            df_ma,
            active_cluster_source,
            cluster_mapping_bundle,
        )
        sidebar_jobfamilies = JobFamilyService.get_active_jobfamilies(
            df_ma,
            cluster_mapping_bundle=cluster_mapping_bundle,
        )
        
        # Cleanup Session State selections if they are invalid
        if "selected_jobfamilies" in st.session_state:
            st.session_state["selected_jobfamilies"] = JobFamilyService.sanitize_selection(
                st.session_state["selected_jobfamilies"], sidebar_jobfamilies
            )
    except FileNotFoundError as e:
        st.error(str(e))
        return
    except Exception as e:
        st.error(t("attrition.error.load_filter", error=e))
        return
    
    # df_ma is now the GLOBAL Aggregated Dataset.
    if not _abgaenge_results_match_cluster_signature(cluster_source_signature) and (
        "abgaenge_global_result" in st.session_state or "abgaenge_results" in st.session_state
    ):
        invalidate_cluster_dependent_state(
            st.session_state,
            reason="abgaenge_cluster_source_changed",
        )
        render_context_box(
            "Hinweis",
            "Gespeicherte Abgangs-Ergebnisse wurden verworfen, weil sich die aktive Clusterquelle geändert hat.",
            tone="info",
            compact=True,
        )

    default_start = get_current_stichtag().date()
    default_end = date(default_start.year + 2, default_start.month, default_start.day)

    # ── Parametrierung (Form) ──
    # Gespeicherte Parameter laden; Fallback auf Defaults beim ersten Aufruf.
    params = st.session_state.get("abgaenge_params", default_params())
    # Wenn sich die aktive Cluster-Quelle seit dem letzten Sync geändert hat,
    # Matrix-Dimensionen an die aktuellen Gruppen anpassen.
    _last_params_sig = st.session_state.get("abgaenge_params_cluster_signature")
    if _last_params_sig != cluster_source_signature:
        params = _sync_params_matrix_dimensions(params, valid_jfs, active_org_units)
        st.session_state["abgaenge_params"] = params
        st.session_state["abgaenge_params_cluster_signature"] = cluster_source_signature
    # Ruhend bleibt auf dieser Seite vorerst fachlich deaktiviert.
    params["components"]["ruhend"] = False
    ruhend_new = int(params["ruhend"]["ruhend_new_cases_per_year"])
    ruhend_return = float(params["ruhend"]["ruhend_return_rate"])
    ruhend_duration = int(params["ruhend"]["ruhend_avg_duration_months"])
    comp_ruhend = False

    render_section_intro(
        "Prognose-Einstellungen",
        "Zeitraum, Komponenten und Feinparameter für die Abgangsprognose.",
    )

    with st.form("abgaenge_forecast_form", clear_on_submit=False):
        # ── Row 1: Base Settings (horizontal) ──
        st.markdown("Zeitraum & Basis")
        submit = False
        freq_options = {
            t("attrition.settings.frequency.month"): "M",
            t("attrition.settings.frequency.quarter"): "Q",
        }
        bc1, bc2, bc3, bc4 = st.columns(4)
        with bc1:
            ist_stichtag = st.date_input(t("attrition.settings.actual_date"), value=default_start)
        with bc2:
            forecast_end_date = st.date_input(t("attrition.settings.forecast_end"), value=default_end)
        with bc3:
            freq_label = st.selectbox(t("attrition.settings.frequency"), options=list(freq_options.keys()), index=0)
        with bc4:
            random_seed = st.number_input(t("attrition.settings.random_seed"), value=int(params["random_seed"]), step=1)

        st.divider()

        # ── Row 2: Component Toggles (horizontal) ──
        st.markdown("Komponenten")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            comp_atz = st.checkbox(t("attrition.settings.component.atz"), value=params["components"]["atz"])
        with cc2:
            comp_ret = st.checkbox(t("attrition.settings.component.retirement"), value=params["components"]["retirement"])
        with cc3:
            comp_quit = st.checkbox(t("attrition.settings.component.quit"), value=params["components"]["quit"])

        st.divider()
        st.info(t("attrition.settings.atz_note"))

        # ── Row 3: Detail Parameters (sub-expanders) - OUTSIDE FORM for interactivity ──
        st.markdown("Detail-Parameter")

        with st.expander(t("attrition.expander.atz"), expanded=False):
            # ── ATZ Row 1: General Constraints ──
            ac1, ac2, ac3, ac4, ac5 = st.columns(5)
            with ac1:
                new_atz_base = st.slider(t("attrition.atz.base_rate"), min_value=0.0, max_value=0.5, value=float(params["atz"].get("new_atz_rate", 0.05)), step=0.005, format="%.3f", help=t("attrition.atz.base_rate.help"), key="slide_atz_base")
                params["atz"]["new_atz_rate"] = new_atz_base
            with ac2:
                eligible_age = st.number_input(t("attrition.atz.min_age"), value=int(params["atz"]["atz_eligible_age_min"]), step=1, key="num_atz_age_min")
                params["atz"]["atz_eligible_age_min"] = eligible_age
            with ac3:
                eligible_age_max = st.number_input(t("attrition.atz.max_age"), value=int(params["atz"]["atz_eligible_age_max"]), step=1, key="num_atz_age_max")
                params["atz"]["atz_eligible_age_max"] = eligible_age_max
            with ac4:
                ar_years = st.number_input(t("attrition.atz.ar_years"), value=float(params["atz"]["atz_duration_ar_years"]), step=0.5, key="num_atz_ar_years")
                params["atz"]["atz_duration_ar_years"] = ar_years
            with ac5:
                fr_years = st.number_input(t("attrition.atz.fr_years"), value=float(params["atz"]["atz_duration_fr_years"]), step=0.5, key="num_atz_fr_years")
                params["atz"]["atz_duration_fr_years"] = fr_years

            use_atz_matrix = False
            atz_dim = params["atz"].get("atz_dimension", "JobFamily")
            new_atz_matrix = params["atz"].get("atz_matrix", {})
            params["atz"]["use_atz_matrix"] = use_atz_matrix
            params["atz"]["atz_dimension"] = atz_dim
            params["atz"]["atz_matrix"] = new_atz_matrix

        with st.expander(t("attrition.expander.retirement"), expanded=False):
            rc1, rc2 = st.columns(2)
            with rc1:
                rent65 = st.slider(t("attrition.retirement.rate_65"), min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_65"]), step=0.05, key="slide_rent_65")
                params["retirement"]["rent_rate_65"] = rent65
            with rc2:
                rent60 = st.slider(t("attrition.retirement.rate_60_64"), min_value=0.0, max_value=1.0, value=float(params["retirement"]["rent_rate_60_65"]), step=0.05, key="slide_rent_60")
                params["retirement"]["rent_rate_60_65"] = rent60

        with st.expander(t("attrition.expander.quit"), expanded=False):
            # ── Controls Row ──
            c1, c2, c3 = st.columns([3, 3, 2])
            
            with c1:
                quit_base = st.slider(
                    t("attrition.quit.base_rate"),
                    min_value=0.0, max_value=0.5, 
                    value=float(params["quit"]["quit_rate_base"]), 
                    step=0.01, 
                    help=t("attrition.quit.base_rate.help"),
                    key="slide_quit_base_live"
                )
                params["quit"]["quit_rate_base"] = quit_base
            with c2:
                st.write("") # Alignment
                use_quit_matrix = st.checkbox(
                    t("attrition.quit.use_matrix"),
                    value=params["quit"].get("use_quit_matrix", True),
                    help=t("attrition.quit.use_matrix.help"),
                    key="chk_use_quit_matrix_live"
                )
                params["quit"]["use_quit_matrix"] = use_quit_matrix
            with c3:
                quit_dim = st.radio(
                    t("attrition.quit.dimension"),
                    options=["JobFamily", "OrgUnit"],
                    index=0 if params["quit"].get("quit_dimension", "JobFamily") == "JobFamily" else 1,
                    help=t("attrition.quit.dimension.help"),
                    disabled=not use_quit_matrix,
                    horizontal=True,
                    key="rad_quit_dim_live"
                )
                params["quit"]["quit_dimension"] = quit_dim

            st.divider()

            # ── Matrix Row ──
            st.caption(t("attrition.quit.matrix_caption", dimension=quit_dim))
            
            # 1. Determine dimension values
            unique_vals = []
            if quit_dim == "OrgUnit":
                unique_vals = active_org_units
            else:
                unique_vals = valid_jfs
            
            age_cohorts = ["alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus"]
            age_labels = {"alter_unter_30": "u30", "alter_30_45": "30-45", "alter_45_55": "45-55", "alter_55_plus": "ü55"}

            # 2. Build DataFrame (percent scale)
            current_quit_matrix = migrate_to_percent(params["quit"].get("quit_matrix", {}))
            dim_items = ["Default"] + unique_vals
            editor_data = []

            for val in dim_items:
                row_data = {quit_dim: val}
                for cohort in age_cohorts:
                    cohort_rates = current_quit_matrix.get(cohort, {})
                    rate = cohort_rates.get(str(val), cohort_rates.get("Default", quit_base * 100))
                    row_data[cohort] = float(rate)
                editor_data.append(row_data)

            df_matrix = pd.DataFrame(editor_data).set_index(quit_dim)

            # 3. Render Editor
            col_conf = {
                quit_dim: st.column_config.TextColumn(f"{quit_dim}", disabled=True)
            }
            for c in age_cohorts:
                col_conf[c] = st.column_config.NumberColumn(
                    age_labels.get(c, c) + " (%)",
                    min_value=0.0, max_value=100.0, step=0.5, format="%.1f"
                )

            edited_df = st.data_editor(
                df_matrix,
                use_container_width=True,
                height=min(400, 50 + len(dim_items) * 35),
                disabled=not use_quit_matrix,
                key=_cluster_widget_key("quit_matrix_editor_live_fixed", f"{cluster_source_signature}_{quit_dim}"),
                column_config=col_conf
            )

            # 4. Save back (percent scale — conversion to weights in build_params_from_ui)
            new_quit_matrix = {c: {} for c in age_cohorts}
            for dim_val, row in edited_df.iterrows():
                for c in age_cohorts:
                    new_quit_matrix[c][str(dim_val)] = float(row[c])
            params["quit"]["quit_matrix"] = new_quit_matrix

            st.divider()
            st.markdown("Jahresgenaue Anpassungen (Jobgruppen)")
            render_context_box("Hinweis", t("attrition.adjustments.info"), tone="neutral", compact=True)
            
            # More/Less selections
            adj_col1, adj_col2 = st.columns(2)
            
            # Load current adjustments from session_state or params
            current_adj = params["quit"].get("quit_adjustments", {"more": {}, "less": {}})
            
            with adj_col1:
                st.caption(t("attrition.adjustments.more"))
                selected_more_jf = st.multiselect(
                    t("attrition.adjustments.job_families"),
                    options=valid_jfs, 
                    default=list(current_adj.get("more", {}).keys()),
                    key="ms_quit_more_jf"
                )
                
                more_years_map = {}
                available_years = JobFamilyService.get_available_years(ist_stichtag.year, (forecast_end_date.year - ist_stichtag.year) + 1)
                
                for jf in selected_more_jf:
                    default_years = current_adj.get("more", {}).get(jf, [])
                    # Sanitize default years to be within available range
                    default_years = [y for y in default_years if y in available_years]
                    
                    years = st.multiselect(
                        t("attrition.adjustments.years_for", job_family=jf),
                        options=available_years,
                        default=default_years,
                        key=f"ms_more_years_{jf}"
                    )
                    if years:
                        more_years_map[jf] = years
            
            with adj_col2:
                st.caption(t("attrition.adjustments.less"))
                selected_less_jf = st.multiselect(
                    t("attrition.adjustments.job_families"),
                    options=valid_jfs, 
                    default=list(current_adj.get("less", {}).keys()),
                    key="ms_quit_less_jf"
                )
                
                less_years_map = {}
                for jf in selected_less_jf:
                    default_years = current_adj.get("less", {}).get(jf, [])
                    default_years = [y for y in default_years if y in available_years]

                    years = st.multiselect(
                        t("attrition.adjustments.years_for", job_family=jf),
                        options=available_years,
                        default=default_years,
                        key=f"ms_less_years_{jf}"
                    )
                    if years:
                        less_years_map[jf] = years
            
            quit_adjustments = {
                "more": more_years_map,
                "less": less_years_map
            }
            params["quit"]["quit_adjustments"] = quit_adjustments

    # ── Action Button ──
        submit_col, _ = st.columns([1, 3])
        with submit_col:
            submit = st.form_submit_button(t("attrition.action.compute"), use_container_width=True, type="primary")

    # ── Rendering or Calculation Logic ──
    freq = freq_options[freq_label]

    if submit:
        # Pre-calculation checks
        if forecast_end_date <= ist_stichtag:
            st.error(t("attrition.error.invalid_date"))
            st.stop()

        ui_state = {
            "ist_stichtag": ist_stichtag.isoformat() if hasattr(ist_stichtag, "isoformat") else str(ist_stichtag),
            "forecast_end_date": forecast_end_date.isoformat() if hasattr(forecast_end_date, "isoformat") else str(forecast_end_date),
            "freq": freq,
            "components": {
                "atz": comp_atz,
                "retirement": comp_ret,
                "quit": comp_quit,
                "ruhend": comp_ruhend,
            },
            "atz": {
                "new_atz_rate": new_atz_base,
                "atz_eligible_age_min": eligible_age,
                "atz_eligible_age_max": eligible_age_max,
                "atz_duration_ar_years": ar_years,
                "atz_duration_fr_years": fr_years,
                "use_atz_matrix": use_atz_matrix,
                "atz_dimension": atz_dim,
                "atz_matrix": new_atz_matrix,
            },
            "retirement": {
                "rent_rate_65": rent65,
                "rent_rate_60_65": rent60,
            },
            "quit": {
                "quit_rate_base": quit_base,
                "use_quit_matrix": use_quit_matrix,
                "quit_dimension": quit_dim,
                "quit_matrix": new_quit_matrix,
                "quit_adjustments": quit_adjustments,
            },
            "ruhend": {
                "ruhend_new_cases_per_year": ruhend_new,
                "ruhend_return_rate": ruhend_return,
                "ruhend_avg_duration_months": ruhend_duration,
            },
            "random_seed": random_seed,
        }

        params = build_params_from_ui(ui_state)
    
        # ── Calculation Block ──
        try:
            with st.spinner(t("attrition.spinner")):
                # P01: Run Global Forecast
                global_result = run_forecast_abgaenge(
                    df_ma=df_ma, 
                    df_atz=df_atz,
                    start_date=pd.Timestamp(ist_stichtag),
                    # P01: Using exact date for consistency across pages
                    end_date=pd.Timestamp(forecast_end_date),
                    freq=freq,
                    params=params,
                )
        
            # --- Enrichment: add OrgUnit/Clusters/UID to global events ---
            events_global = global_result["events_person_level"]
            if not events_global.empty:
                if "Organisationseinheit" not in events_global.columns and "Organisationseinheit" in df_ma.columns:
                    lookup_oe = df_ma[["PersNr", "Organisationseinheit"]].drop_duplicates("PersNr").copy()
                    lookup_oe["PersNr"] = lookup_oe["PersNr"].astype(str)
                    events_global = events_global.merge(lookup_oe, left_on="persnr", right_on="PersNr", how="left").drop(columns=["PersNr"])

                for cluster_col in ["OE-Cluster", "JF-Cluster"]:
                    if cluster_col not in events_global.columns and cluster_col in df_ma.columns:
                        lookup_c = df_ma[["PersNr", cluster_col]].drop_duplicates("PersNr").copy()
                        lookup_c["PersNr"] = lookup_c["PersNr"].astype(str)
                        events_global = events_global.merge(lookup_c, left_on="persnr", right_on="PersNr", how="left").drop(columns=["PersNr"])
                        events_global[cluster_col] = events_global[cluster_col].fillna("Sonstiges")

                from abgaenge.schemas import build_event_uid
                events_global["event_uid"] = build_event_uid(events_global)
                if "persnr" in events_global.columns:
                    events_global["persnr"] = events_global["persnr"].astype(str)

                global_result["events_person_level"] = events_global

            # --- Save (unfiltered) ---
            import datetime
            global_result["cluster_source_signature"] = cluster_source_signature
            st.session_state["abgaenge_global_result"] = global_result
            st.session_state["abgaenge_params"] = params
            st.session_state["abgaenge_params_cluster_signature"] = cluster_source_signature
            st.session_state["abgaenge_ui_state"] = ui_state
            st.session_state["abgaenge_timestamp"] = datetime.datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
            st.session_state["abgaenge_cluster_source_signature"] = cluster_source_signature
            st.success(t("attrition.success.computed"))
            st.rerun()
            
        except Exception as e:
            st.error(t("attrition.error.calculation", error=e))
            st.stop()

    # ── Rendering Logic (View-Only Zoom from Global Result) ──
    global_result = st.session_state.get("abgaenge_global_result")
    if not global_result:
        render_context_box(
            "Keine Prognose",
            t("attrition.info.no_forecast"),
            tone="info",
        )
        return

    last_run_params = st.session_state.get("abgaenge_ui_state", {})
    timestamp = st.session_state.get("abgaenge_timestamp", "–")

    # ── View-Only Zoom: apply current sidebar filters to global events ──
    # NOTE: Use df_ma (employee-level, with MAK_Calculated) — NOT snapshot_df
    # (raw position-level without MAK_Calculated).
    events_global = global_result["events_person_level"]

    events_view, n_before, n_after = apply_event_filters(
        events_global, df_ma, mode="attrition"
    )

    # Filtered snapshot for KPI re-aggregation
    df_filtered_rows = apply_filters(df_ma)
    if df_filtered_rows.empty:
        st.warning(t("attrition.warning.no_filtered_data"))
        return

    # df_ma is already employee-level (1 row per PersNr), so groupby is
    # effectively a no-op but keeps the code path consistent.
    view_agg_dict = {
        "MAK_Calculated": "sum",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Sollarbeitszeit": "sum",
        "Organisationseinheit": "first",
    }
    for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St"]:
        if col in df_filtered_rows.columns:
            view_agg_dict[col] = "first"

    df_view_agg = df_filtered_rows.groupby("PersNr", as_index=False).agg(view_agg_dict)
    df_view_agg["mak"] = pd.to_numeric(df_view_agg["MAK_Calculated"], errors="coerce").fillna(0.0)

    # P05: Adjust Events (Clamp MAK Loss to View Reality)
    if not events_view.empty:
        df_view_agg_indexed = df_view_agg.set_index("PersNr")
        if "mak" in df_view_agg_indexed.columns:
            pmak_map = df_view_agg_indexed["mak"].to_dict()

            def adjust_mak_loss(row):
                pid = str(row["persnr"])
                if row["mak_change"] < 0:
                    current_view_mak = float(pmak_map.get(pid, 0.0))
                    return -abs(current_view_mak)
                return row["mak_change"]

            events_view = events_view.copy()
            events_view["mak_change"] = events_view.apply(adjust_mak_loss, axis=1)

    # P06: Re-Aggregate KPIs for current view
    forecast_kpis = aggregate_forecast_results(
        df_initial=df_view_agg,
        events_df=events_view,
        start_date=pd.Timestamp(ist_stichtag),
        end_date=pd.Timestamp(forecast_end_date) + pd.Timedelta(hours=23, minutes=59, seconds=59),
        freq=freq,
        params=build_params_from_ui(last_run_params) if last_run_params else None,
    )

    events = events_view

    # Publish filtered bundle for Page 5 cross-reference (Contract V3 compat)
    import datetime as _dt
    events_chart_df = events[events["headcount_change"] < 0].copy() if not events.empty else pd.DataFrame()
    st.session_state["abgaenge_results"] = {
        "schema_version": 3,
        "forecast_kpis": forecast_kpis,
        "events_chart": events_chart_df,
        "events_raw": events,
        "events": events,
        "params": last_run_params,
        "cluster_source_signature": cluster_source_signature,
        "filters": {
            "org": st.session_state.get("selected_org_units", []),
            "jf": st.session_state.get("selected_jobfamilies", []),
            "gender": st.session_state.get("selected_genders", []),
            "empl": st.session_state.get("selected_employment", []),
            "atz": st.session_state.get("selected_atz_status", []),
        },
        "meta": {
            "rows_chart": len(events_chart_df),
            "rows_raw": len(events),
            "unique_persons_chart": int(events_chart_df["persnr"].nunique()) if not events_chart_df.empty else 0,
            "created_at": _dt.datetime.now().isoformat(),
        },
        "timestamp": timestamp,
    }

    # Drift Detection (forecast parameters only – sidebar filters are view-only)
    curr_ui_state = {
        "ist_stichtag": ist_stichtag.isoformat() if hasattr(ist_stichtag, "isoformat") else str(ist_stichtag),
        "forecast_end_date": forecast_end_date.isoformat() if hasattr(forecast_end_date, "isoformat") else str(forecast_end_date),
        "freq": freq,
        "components": {
            "atz": comp_atz, "retirement": comp_ret, "quit": comp_quit, "ruhend": comp_ruhend
        },
        "atz": {
             "new_atz_rate": new_atz_base,
             "atz_eligible_age_min": eligible_age,
             "atz_eligible_age_max": eligible_age_max,
             "atz_duration_ar_years": ar_years,
             "atz_duration_fr_years": fr_years,
             "use_atz_matrix": use_atz_matrix,
             "atz_dimension": atz_dim,
             "atz_matrix": new_atz_matrix,
        },
        "retirement": { "rent_rate_65": rent65, "rent_rate_60_65": rent60 },
        "quit": {
            "quit_rate_base": quit_base,
            "use_quit_matrix": use_quit_matrix,
            "quit_dimension": quit_dim,
            "quit_matrix": new_quit_matrix,
        },
        "ruhend": {
            "ruhend_new_cases_per_year": ruhend_new,
            "ruhend_return_rate": ruhend_return,
            "ruhend_avg_duration_months": ruhend_duration,
        },
        "random_seed": random_seed,
    }

    def dict_hash(d):
        return json.dumps(d, sort_keys=True, default=str)

    drift = dict_hash(curr_ui_state) != dict_hash(last_run_params)

    # Header Info
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown(t("attrition.status.timestamp", timestamp=timestamp))
    with c2:
        if drift:
            st.warning(t("attrition.status.params_changed"))
        else:
            st.success(t("attrition.status.current"))

    render_filter_status(n_before, n_after)

    # Ensure columns exist for downstream charts
    for cluster_col in ["OE-Cluster", "JF-Cluster", "Organisationseinheit", "Jobfamily"]:
        if cluster_col not in events.columns:
            events[cluster_col] = "Unbekannt"
        else:
            events[cluster_col] = events[cluster_col].fillna("Unbekannt")
    if "persnr" in events.columns:
        events["persnr"] = events["persnr"].astype(str)

    # ── Ergebnisse ──────────────────────────────────────────────────
    st.divider()

    # KPI Metrics Header
    if not forecast_kpis.empty:
        first = forecast_kpis.iloc[0]
        last = forecast_kpis.iloc[-1]

        exits_total = int(forecast_kpis["exit_count"].sum())
        mak_loss_total = float(forecast_kpis["mak_loss_gross"].sum())
        avg_headcount = float(forecast_kpis[["headcount_start", "headcount_end"]].mean(axis=1).mean())
        abgangsquote = (exits_total / avg_headcount) if avg_headcount > 0 else 0.0
        
        # Calculate annualized rate
        forecast_days = (pd.Timestamp(forecast_end_date) - pd.Timestamp(ist_stichtag)).days
        years = max(0.1, forecast_days / 365.25)
        abgangsquote_annual = (1 - (1 - abgangsquote)**(1/years)) if abgangsquote < 1 else (abgangsquote / years)

        st.markdown(t("attrition.summary.section"))
        m1, m2, m3, m4 = st.columns([1, 1, 1, 1])
        with m1:
            st.metric(t("attrition.summary.metric.total_exits_heads"), f"{exits_total}")
        with m2:
            st.metric(t("attrition.summary.metric.capacity_loss_fte"), f"{mak_loss_total:.1f}")
        with m3:
            st.metric(
                t("attrition.summary.metric.attrition_rate_period"),
                f"{abgangsquote*100:.1f}%",
                help=t("attrition.summary.metric.attrition_rate_period.help"),
            )
        with m4:
            st.metric(
                t("attrition.summary.metric.avg_fluctuation_annual"),
                f"{abgangsquote_annual*100:.1f}%",
                help=t("attrition.summary.metric.avg_fluctuation_annual.help"),
            )
        
        st.info(t("attrition.summary.interpretation"))

        # P03: Secure Start/End KPI (Robust extraction from Timeline info)
        with st.expander(t("attrition.summary.details.expander"), expanded=False):
            d1, d2, d3, d4 = st.columns(4)
            
            # Use total period delta (End - Start) for consistency
            hc_start = float(first['headcount_start']) if not pd.isna(first['headcount_start']) else 0.0
            hc_end = float(last['headcount_end']) if not pd.isna(last['headcount_end']) else 0.0
            mak_start = float(first['mak_start']) if not pd.isna(first['mak_start']) else 0.0
            mak_end = float(last['mak_end']) if not pd.isna(last['mak_end']) else 0.0
            
            total_hc_delta = int(hc_end - hc_start)
            total_mak_delta = float(mak_end - mak_start)
            
            d1.metric(t("attrition.summary.details.headcount_start"), f"{int(hc_start)}")
            d2.metric(
                t("attrition.summary.details.headcount_end"),
                f"{int(hc_end)}",
                delta=f"{total_hc_delta}",
                delta_color="normal",
                help=t("attrition.summary.details.delta_help"),
            )
            
            d3.metric(t("attrition.summary.details.fte_start"), f"{mak_start:.1f}")
            d4.metric(
                t("attrition.summary.details.fte_end"),
                f"{mak_end:.1f}",
                delta=f"{total_mak_delta:.1f}",
                delta_color="normal",
                help=t("attrition.summary.details.delta_help"),
            )
            
            # P01: Clearer caption without hardcoded artifacts
            end_date_str = last['period_end'].strftime("%d.%m.%Y") if hasattr(last['period_end'], 'strftime') else last['period_label']
            st.caption(t("attrition.summary.details.delta_caption", end_date=end_date_str))

        # DEBUG OUTPUT
        if st.session_state.get("debug_active", False):
            with st.expander(t("attrition.debug.expander"), expanded=False):
                # 1. Summary Metrics
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write(f"{t('attrition.debug.raw_events')}: {len(events)}")
                    st.write(f"{t('attrition.debug.exits_heads_sum')}: {events['headcount_change'].sum() if not events.empty else 0}")
                with c2:
                    unique_total = events['persnr'].nunique() if not events.empty else 0
                    unique_hc = events[events['headcount_change'] < 0]['persnr'].nunique() if not events.empty else 0
                    unique_cap = events[(events['headcount_change'] == 0) & (events['mak_change'] < 0)]['persnr'].nunique() if not events.empty else 0
                    
                    st.write(f"{t('attrition.debug.unique_people_all')}: {unique_total}")
                    st.write(f"- {t('attrition.debug.unique_people_exit')}: {unique_hc}")
                    st.write(f"- {t('attrition.debug.unique_people_capacity_only')}: {unique_cap}")
                
                with c3:
                    st.write(f"{t('attrition.debug.fte_loss_sum')}: {events['mak_change'].sum() if not events.empty else 0:.2f}")
                    st.caption(t("attrition.debug.fte_loss_caption"))
                    
                    # Logic Overlap Check (ATZ_END vs RETIREMENT)
                    if not events.empty:
                        from abgaenge.schemas import REASON_ATZ_END, REASON_RETIREMENT
                        pnr_atz_end = set(events[events["reason_code"] == REASON_ATZ_END]["persnr"])
                        pnr_retirement = set(events[events["reason_code"] == REASON_RETIREMENT]["persnr"])
                        overlap = len(pnr_atz_end & pnr_retirement)
                        color = "red" if overlap > 0 else "green"
                        st.markdown(f"{t('attrition.debug.overlap_atz_retirement')}: :{color}[{overlap}]")

                st.divider()
                
                # 2. Impact Table
                st.markdown(t("attrition.debug.events_by_effect"))
                if not events.empty:
                    debug_impact = events.groupby("reason_code").agg(
                        Event_Count=("reason_code", "count"),
                        Unique_Pers=("persnr", "nunique"),
                        Sum_Headcount=("headcount_change", "sum"),
                        Sum_MAK=("mak_change", "sum")
                    ).reset_index()
                    
                    # Labels
                    from abgaenge.schemas import REASON_LABELS
                    debug_impact["Ursache"] = debug_impact["reason_code"].map(REASON_LABELS)
                    st.caption(t("attrition.debug.events_caption"))
                    st.table(debug_impact[["Ursache", "Event_Count", "Unique_Pers", "Sum_Headcount", "Sum_MAK"]].round(2))
                
                st.divider()
                st.markdown(t("attrition.debug.sample_last_events"))
                st.dataframe(events.tail(20))

    # --- Choice of Metric for Charts ---
    st.caption(t("attrition.caption.metric_view", metric=metric_choice))

    charts = build_charts(forecast_kpis, events, metric_type=metric_choice)
    
    # Apply standard legend to all charts from builder
    for key, fig in charts.items():
        if fig:
            charts[key] = apply_legend_bottom(fig)

    tab1, tab2, tab3 = st.tabs(_get_result_tab_labels())

    # Helper for Debug Metrics
    def _render_debug_metric(label, chart_val, global_val, unit=""):
        if not st.session_state.get("debug_active", False):
            return
        
        diff = chart_val - global_val
        is_ok = abs(diff) < 0.1
        color = "green" if is_ok else "red"
        icon = "✅" if is_ok else "❌"
        
        # UI uses 1 decimal, Debug uses 2
        st.markdown(
            f"<small>{icon} Debug ({label}): UI (gerundet, 1 Dez.): {global_val:,.1f}{unit} | Debug (2 Dez.): {global_val:,.2f}{unit} | Diff (zu Chart): :{color}[{diff:+.3f}]</small>", 
            unsafe_allow_html=True
        )

    with tab1:
        # ── Section 1: zeitliche Entwicklung & Gesamt-Struktur ──
        st.markdown(t("attrition.overview.trend.heading"))
        st.plotly_chart(charts.get("line_headcount_mak"), use_container_width=True)
        # Debug Timeline: Last Point vs KPI
        if not forecast_kpis.empty:
            _render_debug_metric("Timeline End MAK", last['mak_end'], last['mak_end'], " MAK")
            
        st.caption(t("attrition.overview.trend.caption"))

        st.plotly_chart(charts.get("bar_reasons_total"), use_container_width=True)
        # Debug Reason Total
        if not events.empty:
            layout_val = events["headcount_change" if metric_choice=="Köpfe" else "mak_change"].sum()
            compare_val = exits_total if metric_choice=="Köpfe" else mak_loss_total
            _render_debug_metric(f"Reason Chart Sum ({metric_choice})", abs(layout_val), compare_val, " MAK" if metric_choice=="MAK" else "")
            
        st.caption(t("attrition.overview.total_loss.caption", metric=metric_choice))

        st.divider()

        # ── Section 2: Struktur der Abgänge ──
        st.markdown(t("attrition.overview.reason_timeline.heading", metric=metric_choice))
        st.plotly_chart(charts.get("bar_abgaenge_reasons"), use_container_width=True)
        # Debug Detailed Reasons
        if not events.empty:
            _render_debug_metric("Detailed Reason Sum", abs(events["headcount_change" if metric_choice=="Köpfe" else "mak_change"].sum()), exits_total if metric_choice=="Köpfe" else mak_loss_total, " MAK" if metric_choice=="MAK" else "")

        st.caption(t("attrition.overview.reason_timeline.caption", metric=metric_choice))

        st.divider()

        if "Organisationseinheit" in events.columns:
            st.markdown(t("attrition.overview.top_org_units.heading"))
            
            # Aggregate
            exclude_units = ["Unbekannt", None]
            # FIX: Only count actual Headcount Exits (ignore ATZ internal steps for this chart)
            org_events = events[~events["Organisationseinheit"].isin(exclude_units)]
            org_events = org_events[org_events["headcount_change"] < 0]
            
            if not org_events.empty:
                org_stats = org_events.groupby("Organisationseinheit").size().reset_index(name="Abgänge")
                org_stats = org_stats.sort_values("Abgänge", ascending=True).tail(15) 
                
                fig_org = px.bar(
                    org_stats, 
                    x="Abgänge", 
                    y="Organisationseinheit", 
                    orientation="h",
                    title=t("attrition.overview.top_org_units.chart_title"),
                    text="Abgänge",
                    color="Abgänge",
                    color_continuous_scale="Reds"
                )
                fig_org.update_layout(yaxis_title=None, showlegend=False, height=600)
                st.plotly_chart(fig_org, use_container_width=True)
                # Debug OrgUnit Sum (Check if we missed anyone)
                if not org_events.empty:
                    # We check sum of ALL units (not just top 15) against global
                    # Note: org_events excludes 'Unbekannt'
                    _render_debug_metric("OrgUnit Sum (Headcount)", len(org_events), exits_total, "")

        st.divider()

        # ── Section 4: Cluster-Analyse (OE) ──
        st.markdown(t("attrition.overview.cluster_oe.heading"))
        if cluster_is_active:
            if "OE-Cluster" in events.columns:
                # Get full set of clusters for consistent Y-axis
                all_clusters = sorted(df_ma["OE-Cluster"].unique().tolist())

                # Filter for Headcount departures (Upper Chart)
                cluster_events_h = events[events["headcount_change"] < 0].copy()

                # Filter for MAK losses (Lower Chart) - captures ATZ-FR etc.
                cluster_events_m = events[events["mak_change"] < 0].copy()

                # Layout: Vertical (untereinander)

                # Chart 1: Kopfabgänge
                st.markdown(t("attrition.overview.cluster_oe.people"))
                c_stats_h = cluster_events_h.groupby("OE-Cluster").size().reindex(all_clusters, fill_value=0).reset_index(name="Abgänge")
                c_stats_h = c_stats_h.sort_values("Abgänge", ascending=True)
                
                fig_h = px.bar(
                    c_stats_h,
                    x="Abgänge",
                    y="OE-Cluster",
                    orientation="h",
                    title=t("attrition.overview.cluster_oe.people_chart_title"),
                    text="Abgänge",
                    color="Abgänge",
                    color_continuous_scale="Reds"
                )
                fig_h.update_layout(yaxis_title=None, showlegend=False, height=600)
                st.plotly_chart(fig_h, use_container_width=True)
                # Debug Cluster Headcount
                if not cluster_events_h.empty:
                    _render_debug_metric("Cluster Sum (Headcount)", len(cluster_events_h), exits_total, "")

                st.divider()

                # Chart 2: MAK-Abgänge (Capacity Loss)
                st.markdown(t("attrition.overview.cluster_oe.fte"))
                
                if "mak_change" in cluster_events_m.columns:
                    cluster_events_m["mak_loss"] = cluster_events_m["mak_change"].abs()
                    c_stats_m = cluster_events_m.groupby("OE-Cluster")["mak_loss"].sum().reindex(all_clusters, fill_value=0.0).reset_index(name="MAK-Verlust")
                    c_stats_m = c_stats_m.sort_values("MAK-Verlust", ascending=True)
                    

                    fig_m = px.bar(
                        c_stats_m,
                        x="MAK-Verlust",
                        y="OE-Cluster",
                        orientation="h",
                        title=t("attrition.overview.cluster_oe.fte_chart_title"),
                        text_auto=".1f",
                        color="MAK-Verlust",
                        color_continuous_scale="Reds"
                    )
                    fig_m.update_layout(yaxis_title=None, showlegend=False, height=600)
                    st.plotly_chart(fig_m, use_container_width=True)
                    # Debug Cluster MAK
                    if not cluster_events_m.empty:
                        # mak_loss is positive
                        _render_debug_metric("Cluster Sum (MAK)", cluster_events_m["mak_loss"].sum(), mak_loss_total, " MAK")
                else:
                    st.info(t("attrition.overview.cluster_oe.no_fte"))
            else:
                st.warning(t("attrition.overview.cluster_oe.missing_column"))

            st.divider()

            # ── Section 5: Cluster-Analyse (JF) ──
            st.markdown(t("attrition.overview.cluster_jf.heading"))
            if "JF-Cluster" in events.columns:
                # Get full set of clusters for consistent Y-axis
                all_jf_clusters = sorted(df_ma["JF-Cluster"].unique().tolist())

                # Filter for Headcount departures
                cluster_events_h_jf = events[events["headcount_change"] < 0].copy()

                # Filter for MAK losses
                cluster_events_m_jf = events[events["mak_change"] < 0].copy()

                # Chart 1: Kopfabgänge JF
                st.markdown(t("attrition.overview.cluster_jf.people"))
                c_stats_h_jf = cluster_events_h_jf.groupby("JF-Cluster").size().reindex(all_jf_clusters, fill_value=0).reset_index(name="Abgänge")
                c_stats_h_jf = c_stats_h_jf.sort_values("Abgänge", ascending=True)
                
                fig_h_jf = px.bar(
                    c_stats_h_jf,
                    x="Abgänge",
                    y="JF-Cluster",
                    orientation="h",
                    title=t("attrition.overview.cluster_jf.people_chart_title"),
                    text="Abgänge",
                    color="Abgänge",
                    color_continuous_scale="Reds"
                )
                fig_h_jf.update_layout(yaxis_title=None, showlegend=False, height=600)
                st.plotly_chart(fig_h_jf, use_container_width=True)
                # Debug JF Headcount
                if not cluster_events_h_jf.empty:
                    _render_debug_metric("JF Cluster Sum (Headcount)", len(cluster_events_h_jf), exits_total, "")

                st.divider()

                # Chart 2: MAK-Abgänge JF
                st.markdown(t("attrition.overview.cluster_jf.fte"))
                if "mak_change" in cluster_events_m_jf.columns:
                    cluster_events_m_jf["mak_loss"] = cluster_events_m_jf["mak_change"].abs()
                    c_stats_m_jf = cluster_events_m_jf.groupby("JF-Cluster")["mak_loss"].sum().reindex(all_jf_clusters, fill_value=0.0).reset_index(name="MAK-Verlust")
                    c_stats_m_jf = c_stats_m_jf.sort_values("MAK-Verlust", ascending=True)
                    
                    fig_m_jf = px.bar(
                        c_stats_m_jf,
                        x="MAK-Verlust",
                        y="JF-Cluster",
                        orientation="h",
                        title=t("attrition.overview.cluster_jf.fte_chart_title"),
                        text_auto=".1f",
                        color="MAK-Verlust",
                        color_continuous_scale="Reds"
                    )
                    fig_m_jf.update_layout(yaxis_title=None, showlegend=False, height=600)
                    st.plotly_chart(fig_m_jf, use_container_width=True)
                    # Debug JF MAK
                    if not cluster_events_m_jf.empty:
                         # mak_loss is positive
                         _render_debug_metric("JF Cluster Sum (MAK)", cluster_events_m_jf["mak_loss"].sum(), mak_loss_total, " MAK")
            else:
                st.warning(t("attrition.overview.cluster_jf.missing_column"))

        else:
            st.info(t("attrition.info.no_custom_clusters"))

        st.divider()

        # ── Section 3: Datengrundlage ──
        with st.expander(t("attrition.overview.raw_table.expander"), expanded=False):
            # Round for raw display (2 digits as requested for non-summary)
            df_display = forecast_kpis.copy()
            for col in ["mak_start", "mak_end", "mak_delta", "mak_loss_gross", "abgangsquote"]:
                if col in df_display.columns:
                    df_display[col] = df_display[col].round(2)
            st.dataframe(df_display, use_container_width=True, height=500)

    with tab2:
        if events.empty:
            render_context_box("Keine Ereignisse", t("attrition.info.no_driver_events"), tone="info", compact=True)
        else:
            # ── Section 1: Management Summary ──
            st.markdown(t("attrition.drivers.summary.heading"))
            st.info(t("attrition.drivers.summary.info"))

            # Data Prep
            summary_df = events.copy()
            summary_df["event_date"] = pd.to_datetime(summary_df["event_date"])
            summary_df["Jahr"] = summary_df["event_date"].dt.to_period("Y").astype(str)

            # --- Table A: Headcount ---
            st.markdown(t("attrition.drivers.table.headcount"))
            hc_exits = summary_df[summary_df["headcount_change"] < 0]
            if not hc_exits.empty:
                hc_pivot = hc_exits.pivot_table(index="Jahr", columns="reason_label", values="persnr", aggfunc="count", fill_value=0)
                hc_pivot["Gesamt"] = hc_pivot.sum(axis=1)
                st.dataframe(hc_pivot, use_container_width=True)
            else:
                st.info(t("attrition.drivers.info.no_headcount"))

            # --- Table B: MAK ---
            st.markdown(t("attrition.drivers.table.fte"))
            mak_exits = summary_df[summary_df["mak_change"] < -0.001].copy()
            if not mak_exits.empty:
                mak_exits["MAK_Verlust"] = mak_exits["mak_change"].abs()
                mak_pivot = mak_exits.pivot_table(index="Jahr", columns="reason_label", values="MAK_Verlust", aggfunc="sum", fill_value=0.0)
                mak_pivot["Gesamt"] = mak_pivot.sum(axis=1)
                # Management View: Round to 1 decimal place as requested
                st.dataframe(mak_pivot.round(1), use_container_width=True)
            else:
                st.info(t("attrition.drivers.info.no_fte"))

            st.divider()

            # ── Section 2: Visual Analysis ──
            st.markdown(t("attrition.drivers.visual.heading"))
            for key, fig in charts.items():
                if key.startswith("driver_"):
                    st.plotly_chart(fig, use_container_width=True)

            st.divider()

            # ── Section 3: Detailed Data Tables ──
            st.markdown(t("attrition.drivers.detail.heading"))
            res_global = st.session_state.get("abgaenge_global_result", {})
            tables = res_global.get("tables", {})
            if not tables:
                render_context_box("Keine Daten", t("attrition.drivers.info.no_tables"), tone="info", compact=True)
            else:
                for name, df in tables.items():
                    if df is None or df.empty:
                        continue
                    with st.expander(t("attrition.drivers.detail.expander", name=name.capitalize()), expanded=False):
                        st.dataframe(df, use_container_width=True)

    with tab3:
        if events.empty:
            render_context_box("Keine Personenlisten", t("attrition.info.no_person_lists"), tone="info", compact=True)
        else:
            for reason in sorted(events["reason_label"].unique().tolist()):
                reason_df = events[events["reason_label"] == reason]
                safe_reason = "".join([c if c.isalnum() else "_" for c in reason]).strip("_").lower()
                with st.expander(f"{reason} ({len(reason_df)})", expanded=False):
                    st.dataframe(reason_df, use_container_width=True)
                    st.download_button(
                        label=t("attrition.export.csv_label", reason=reason),
                        data=to_csv_bytes(reason_df),
                        file_name=f"abgaenge_{safe_reason}.csv",
                        mime="text/csv",
                    )

    with st.expander(t("attrition.expander.plausibility")):
        res_global = st.session_state.get("abgaenge_global_result")
        if res_global:
            checks = validate_outputs(res_global)
            st.write(t("attrition.plausibility.checks"))
            st.json(checks)
        st.write(t("attrition.plausibility.params"))
        st.json(last_run_params)


if not globals().get("_UNIT_TESTING"):
    main()
