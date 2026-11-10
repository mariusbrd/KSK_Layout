"""Complete product metric packet for one frozen CompactPlus seed pair.

This is the same product simulation and certified annual stock bridge used by
the many-seed benchmark. The single-run Streamlit path is not modified.
"""
from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import dataloader.compact_simulation_engine as engine
from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
from utils.compact_regular_stock import (
    atz_affected_position_mask, project_special_annual_position_stocks,
    rebase_existing_atz_status, sync_original_position_oe,
    update_compact_for_atz_changes, update_stock_only_for_atz_changes,
)
from utils.compact_special_position_events import special_position_transitions
from utils.monte_carlo_complete_packet import build_complete_metric_packet
from utils.monte_carlo_input_fingerprint import fingerprint_simulation_inputs
from utils.status_quo_baseline import build_status_quo_snapshot
from utils.settings_loader import get_setting


_ROOT = Path(__file__).resolve().parents[1]
_COMPACT_MODULE = None
_SESSION_KEYS = (
    "cohort_definitions", "azubi_salaries", "vorstand_jahresgehalt",
    "employer_cost_factor", "tvoed_lookup",
)


def freeze_product_worker_input(*, snapshot: pd.DataFrame, atz: pd.DataFrame,
                                base_date, target_date, active_cluster_source,
                                cluster_mapping_bundle, cluster_source_signature,
                                salary_automation=None, exclusions=None,
                                session_values=None) -> dict[str, Any]:
    """Capture product dependencies before spawning; never read app state in a worker."""
    if session_values is None:
        session_values = {key: deepcopy(st.session_state[key]) for key in _SESSION_KEYS
                          if key in st.session_state}
    elif not set(session_values).issubset(_SESSION_KEYS):
        raise ValueError("Unexpected worker session value")
    return {
        "snapshot": snapshot.copy(deep=True), "atz": atz.copy(deep=True),
        "base_date": pd.Timestamp(base_date), "target_date": pd.Timestamp(target_date),
        "settings": {
            "salary_automation": deepcopy(get_setting("salary_automation", {})
                                          if salary_automation is None else salary_automation),
            "exclusions": deepcopy(get_setting("exclusions", {})
                                   if exclusions is None else exclusions),
        },
        "session_values": deepcopy(session_values),
        "active_cluster_source": deepcopy(active_cluster_source),
        "cluster_mapping_bundle": deepcopy(cluster_mapping_bundle),
        "cluster_source_signature": cluster_source_signature,
    }


def product_input_fingerprint(payload: dict[str, Any]) -> str:
    """Include all captured per-worker data and non-plan calculation settings."""
    return fingerprint_simulation_inputs(
        payload["snapshot"], payload["atz"],
        salary_automation=payload["settings"]["salary_automation"],
        cost_lookup=payload["session_values"],
        cluster_mapping=payload["cluster_mapping_bundle"].to_dict(),
    )


def _compact_module():
    global _COMPACT_MODULE
    if _COMPACT_MODULE is None:
        page = next((_ROOT / "pages").glob("1_*_Kompakt.py"))
        spec = importlib.util.spec_from_file_location("monte_carlo_compact_prepare", page)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COMPACT_MODULE = module
    return _COMPACT_MODULE


def _compact_prepare():
    return _compact_module().prepare_compact_data


def _metric_totals(frame: pd.DataFrame) -> dict[str, float]:
    """Match the CompactPlus page's target metric definition exactly."""
    active = frame.copy()
    if "Is_Vacant" in active.columns:
        active = active[active["Is_Vacant"] != True].copy()
    heads = int(active["PersNr"].nunique()) if "PersNr" in active.columns else int(len(active))
    mak_col = "MAK_Reporting" if "MAK_Reporting" in active.columns else "MAK_Calculated"
    eur_col = "EUR_Reporting" if "EUR_Reporting" in active.columns else "Total_Cost_Year"
    return {
        "heads": heads,
        "mak": float(pd.to_numeric(active.get(mak_col, 0), errors="coerce").fillna(0).sum()),
        "eur": float(pd.to_numeric(active.get(eur_col, 0), errors="coerce").fillna(0).sum()),
    }


def _annual_stocks(snapshot, atz, start, end, product, observed, source, bundle, prepare):
    people = engine._prepare_employee_forecast_base(snapshot, atz, start)
    departures = product.abgaenge_result["events_person_level"]
    accessions = product.zugaenge_result["events"]
    atz_pivot = product.abgaenge_result["tables"]["atz_pivot"]
    preapplied = engine._apply_attrition_events_to_employee_state(people, departures)
    transitions = special_position_transitions(snapshot, accessions)
    annual_baselines = []
    first_updated = previous_input = previous_stock = previous_compact = None
    first_year = start.year if start < pd.Timestamp(start.year, 12, 31) else start.year + 1
    for year in range(first_year, end.year + 1):
        date = min(pd.Timestamp(year, 12, 31), end)
        status = engine._resolve_atz_status_map(atz_pivot, date)
        if first_updated is None:
            base = engine._update_existing_rows(snapshot, preapplied, date, status)
            first_updated = base
        else:
            base = rebase_existing_atz_status(first_updated, snapshot, preapplied, status)
        if previous_input is None:
            affected = None
            stock = engine._finalize_future_snapshot(
                base, date, active_cluster_source=source,
                cluster_mapping_bundle=bundle, stock_only=True)
        else:
            affected = atz_affected_position_mask(previous_input, base)
            stock = update_stock_only_for_atz_changes(
                previous_input, previous_stock, base,
                lambda rows: engine._finalize_future_snapshot(
                    rows, date, active_cluster_source=source,
                    cluster_mapping_bundle=bundle, stock_only=True))
        previous_input, previous_stock = base, stock
        if previous_compact is None:
            raw_compact = prepare(stock)
        else:
            raw_compact = update_compact_for_atz_changes(
                previous_compact, stock, affected, prepare)
        previous_compact = raw_compact
        baseline = sync_original_position_oe(raw_compact, snapshot, observed[date])
        mapped = apply_clusters_to_snapshot_from_source(
            baseline, source, mapping_bundle=bundle)
        baseline["OE-Cluster"] = mapped["OE-Cluster"].to_numpy()
        annual_baselines.append((date, baseline, mapped))
    return project_special_annual_position_stocks(annual_baselines, transitions)


def run_product_seed(index: int, attrition: dict, accession: dict,
                     payload: dict[str, Any], *, include_ist_charts: bool = False) -> dict:
    """Return one complete packet; all external values must come from payload."""
    settings = payload["settings"]
    required = {"salary_automation", "exclusions"}
    if set(settings) != required:
        raise ValueError("Worker settings must contain salary_automation and exclusions")
    session_values = payload["session_values"]
    end_date = pd.Timestamp(payload["target_date"]).normalize()
    original_get_setting = engine.get_setting
    original_session_state = st.session_state
    try:
        # A spawned worker is isolated; patch its imported module references only
        # for this calculation. Restore them for serial parity tests as well.
        engine.get_setting = lambda key, default=None: deepcopy(settings.get(key, default))
        st.session_state = deepcopy(session_values)
        snapshot, atz = payload["snapshot"], payload["atz"]
        start, end = pd.Timestamp(payload["base_date"]), pd.Timestamp(payload["target_date"])
        source, bundle = payload["active_cluster_source"], payload["cluster_mapping_bundle"]
        observed = {}
        product = engine.simulate_compact_snapshot(
            snapshot, atz, end, base_date=start,
            abgaenge_params=attrition, zugaenge_params=accession,
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=payload["cluster_source_signature"],
            include_audits=False,
            year_end_observer=lambda date, state, events: observed.setdefault(date, state),
            month_end_observer=lambda date, state, events: observed.setdefault(date, state)
            if date == end_date else None,
        )
        stocks = _annual_stocks(snapshot, atz, start, end, product, observed,
                                source, bundle, _compact_prepare())
        context = {
            "run_signature": f"seed-pair:{attrition['random_seed']}:{accession['random_seed']}",
            "metadata": product.metadata,
            "baseline_snapshot": build_status_quo_snapshot(snapshot, start),
            "final_snapshot": product.future_snapshot_df,
            "zugaenge_params": accession,
            "cluster_mapping": bundle.to_dict(),
            "departure_events": product.abgaenge_result["events_person_level"],
            "accession_events": product.zugaenge_result["events"],
        }
        packet = build_complete_metric_packet(
            context, target_totals=_metric_totals(product.future_snapshot_df),
            annual_stock_tables=stocks)
        if include_ist_charts:
            from utils.monte_carlo_chart_values import CHART_VERSION, extract_ist_chart_values
            compact = _compact_module()
            prepared = compact.prepare_compact_data(product.future_snapshot_df)
            packet["compact_ist_version"] = CHART_VERSION
            packet["compact_ist_values"] = extract_ist_chart_values(prepared, compact)
        return packet
    finally:
        engine.get_setting = original_get_setting
        st.session_state = original_session_state


def run_product_seed_with_ist_charts(index: int, attrition: dict, accession: dict,
                                     payload: dict[str, Any]) -> dict:
    """App-only extension; the frozen reference packet path remains byte-identical."""
    return run_product_seed(index, attrition, accession, payload,
                            include_ist_charts=True)
