import copy
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import kpi_reference
from components.sidebar import filter_dataframe_by_view_filters
from config.settings import DEFAULT_AZUBI_SALARIES, DEFAULT_COHORTS
from dataloader import loader
from dataloader.compact_simulation_engine import simulate_compact_snapshot
FROZEN_SETTINGS = {
    "stichtag": "2025-12-31",
    "include_future_hires": True,
    "exclusions": {
        "vorstand": True,
        "ruhend_bv": True,
        "planstellen_follow_person": True,
        "org_units": [
            "9900", "9910", "9920", "9921", "9940", "9941", "9945",
            "9960", "9970", "9971", "9972", "9973", "9975", "9980",
            "9981", "9990", "9999", "99XX",
        ],
    },
}
FROZEN_STICHTAG = pd.Timestamp(FROZEN_SETTINGS["stichtag"])


def _load_page_module(pattern: str, module_name: str):
    page_path = next((ROOT / "pages").glob(pattern))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frozen_get_setting(key: str, default=None):
    return copy.deepcopy(FROZEN_SETTINGS.get(key, default))


def _freeze_context():
    loader.get_setting = _frozen_get_setting
    kpi_reference.get_setting = _frozen_get_setting
    loader.get_current_stichtag = lambda: FROZEN_STICHTAG
    kpi_reference.get_current_stichtag = lambda: FROZEN_STICHTAG
    loader.np.random.normal = lambda loc=0.0, scale=1.0, size=None: float(loc) if size is None else np.full(size, float(loc))


def _reset_state():
    st.cache_data.clear()
    st.session_state.clear()
    st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
    st.session_state["azubi_salaries"] = DEFAULT_AZUBI_SALARIES.copy()
    st.session_state["vorstand_jahresgehalt"] = 200000.0
    st.session_state["employer_cost_factor"] = loader.EMPLOYER_COST_FACTOR


def _sample_filters(df: pd.DataFrame) -> tuple[dict, dict]:
    empty_filters = {
        "selected_org_units": [],
        "selected_jobfamilies": [],
        "selected_oe_clusters": [],
        "selected_jf_clusters": [],
        "selected_genders": ["m", "w"],
        "selected_employment": ["Vollzeit", "Teilzeit", "Inaktiv"],
        "selected_atz_status": ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
        "selected_cohorts": [],
        "selected_education": [],
    }

    changed_filters = copy.deepcopy(empty_filters)
    for key, column, limit in [
        ("selected_org_units", "Organisationseinheit", 2),
        ("selected_jobfamilies", "Jobfamily", 2),
        ("selected_oe_clusters", "OE-Cluster", 1),
        ("selected_jf_clusters", "JF-Cluster", 1),
        ("selected_cohorts", "Alterskohorte", 2),
        ("selected_education", "Ausbildung", 2),
    ]:
        if column in df.columns:
            values = [str(v) for v in df[column].dropna().unique() if str(v).strip()]
            if values:
                changed_filters[key] = sorted(values)[:limit]
    changed_filters["selected_genders"] = ["w"] if "Geschlecht" in df.columns else empty_filters["selected_genders"]
    changed_filters["selected_employment"] = ["Vollzeit", "Teilzeit"]
    return empty_filters, changed_filters


def _measure(func):
    start = time.perf_counter()
    func()
    return time.perf_counter() - start


def _reference_prepare_stacked_tariff_chart_source(df: pd.DataFrame, value_col: str):
    work_df = df.copy()
    work_df["TrfGr_clean"] = work_df["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    work_df["St_clean"] = work_df["St"].apply(
        lambda x: int(str(x).strip().replace("+", "").replace("-", "")) if pd.notna(x) else 4
    )
    if value_col == "Headcount":
        id_col = "PersNr" if "PersNr" in work_df.columns else "Personalnummer"
        return (
            work_df[work_df["Is_Vacant"] == False]
            .groupby(["TrfGr_clean", "St_clean"])[id_col]
            .nunique()
            .reset_index(name="Wert")
        )
    return (
        work_df.groupby(["TrfGr_clean", "St_clean"])[value_col]
        .sum()
        .reset_index(name="Wert")
    )


def _reference_prepare_stacked_tariff_comparison_source(df: pd.DataFrame, ist_col: str, soll_col: str):
    work_df = df.copy()
    work_df["TrfGr_clean"] = work_df["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    work_df["St_clean"] = work_df["St"].apply(
        lambda x: int(str(x).strip().replace("+", "").replace("-", "")) if pd.notna(x) else 4
    )
    ist_pivot = work_df.groupby(["TrfGr_clean", "St_clean"])[ist_col].sum().reset_index(name="Wert")
    soll_totals = work_df.groupby("TrfGr_clean")[soll_col].sum().reset_index(name="Wert")
    return ist_pivot, soll_totals


def _reference_build_hybrid_netto_chart_sources(combined_events_in_scope: pd.DataFrame):
    df_ts = pd.DataFrame({"date": pd.to_datetime(combined_events_in_scope["event_date"])})
    df_ts["month"] = df_ts["date"].dt.to_period("M").astype(str)
    df_ts["type"] = combined_events_in_scope["type"]
    df_ts["count"] = combined_events_in_scope["headcount_change"]
    mask_ext = df_ts["type"].isin(["Azubi_Hire", "Trainee_Hire", "New_Hire"])
    df_ext = df_ts[mask_ext].groupby(["month", "type"])["count"].sum().reset_index()
    mask_conv = df_ts["type"].isin(["Azubi_Conversion_In"])
    if mask_conv.any():
        df_conv = df_ts[mask_conv].groupby(["month"])["count"].sum().reset_index()
    else:
        df_conv = pd.DataFrame(columns=["month", "count"])
    if combined_events_in_scope.empty:
        driver_agg = pd.DataFrame(columns=["JahrMonat", "reason_label", "mak_change"])
    else:
        events_for_drivers = combined_events_in_scope.copy()
        events_for_drivers["event_date"] = pd.to_datetime(events_for_drivers["event_date"])
        events_for_drivers["JahrMonat"] = events_for_drivers["event_date"].dt.to_period("M").astype(str)
        driver_agg = events_for_drivers.groupby(["JahrMonat", "reason_label"])["mak_change"].sum().reset_index()
    return df_ext, df_conv, driver_agg


def _reference_build_hybrid_zugaenge_chart_sources(filt_zug_events: pd.DataFrame):
    valid_types = ["Azubi_Hire", "Azubi_Conversion_In", "New_Hire", "Trainee_Hire"]
    if not filt_zug_events.empty:
        events_chart = filt_zug_events[filt_zug_events["type"].isin(valid_types)].copy()
    else:
        events_chart = pd.DataFrame()
    if "OE-Cluster" in filt_zug_events.columns:
        z_stats = filt_zug_events.groupby("OE-Cluster").size().reset_index(name="Zugänge")
    else:
        z_stats = pd.DataFrame(columns=["OE-Cluster", "Zugänge"])
    return events_chart, z_stats


def _run_compact_bundle(compact, prepared_df: pd.DataFrame, filters: dict):
    filtered_df = filter_dataframe_by_view_filters(prepared_df, filters)
    compact.create_breakdown_table(filtered_df, "Geschlecht", "Headcount")
    compact.create_breakdown_table(filtered_df, "Ausbildung", "MAK_Calculated")
    compact.create_breakdown_table(filtered_df, "Ausbildung", "MAK_Calculated", include_soll=True, soll_col="Soll_FTE")
    compact.create_stacked_tariff_breakdown_table(filtered_df, "MAK_Calculated")
    compact.analyze_ist_mak_data(filtered_df)
    compact.analyze_ist_koepfe_data(filtered_df)
    compact.analyze_ist_vs_soll_mak_data(filtered_df)


def benchmark_kompakt():
    compact = _load_page_module("*_Kompakt.py", "benchmark_compact_page")
    empty_filters, changed_filters = {}, {}

    def _initial():
        snapshot_df, _, _, _ = loader.load_and_prepare_data()
        prepared_df = compact.prepare_compact_data(snapshot_df)
        nonlocal empty_filters, changed_filters
        empty_filters, changed_filters = _sample_filters(prepared_df)
        _run_compact_bundle(compact, prepared_df, empty_filters)

    initial_load = _measure(_initial)
    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    prepared_df = compact.prepare_compact_data(snapshot_df)
    warm_rerun = _measure(lambda: _run_compact_bundle(compact, prepared_df, empty_filters))
    tab_switch = _measure(lambda: _run_compact_bundle(compact, prepared_df, empty_filters))
    filter_change = _measure(lambda: _run_compact_bundle(compact, prepared_df, changed_filters))
    compact.create_stacked_tariff_chart(prepared_df, "MAK_Calculated", value_type="mak")
    compact.create_stacked_tariff_comparison_chart(prepared_df, "MAK_Calculated", "Soll_FTE", value_type="mak")
    chart_paths = {
        "stacked_tariff_source_reference": round(_measure(lambda: _reference_prepare_stacked_tariff_chart_source(prepared_df, "MAK_Calculated")), 4),
        "stacked_tariff_chart_cached_warm": round(_measure(lambda: compact.create_stacked_tariff_chart(prepared_df, "MAK_Calculated", value_type="mak")), 4),
        "stacked_tariff_comparison_source_reference": round(_measure(lambda: _reference_prepare_stacked_tariff_comparison_source(prepared_df, "MAK_Calculated", "Soll_FTE")), 4),
        "stacked_tariff_comparison_chart_cached_warm": round(_measure(lambda: compact.create_stacked_tariff_comparison_chart(prepared_df, "MAK_Calculated", "Soll_FTE", value_type="mak")), 4),
    }

    return {
        "initial_load": round(initial_load, 4),
        "warm_rerun": round(warm_rerun, 4),
        "tab_switch": round(tab_switch, 4),
        "filter_change": round(filter_change, 4),
        "chart_paths": chart_paths,
    }


def _build_synthetic_hybrid_events(df_ma: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = df_ma.head(3).copy()
    if base.empty:
        return pd.DataFrame(), pd.DataFrame()
    abg_rows = []
    for _, row in base.iterrows():
        abg_rows.append({
            "period_label": "Jan 2026",
            "period_start": pd.Timestamp("2026-01-01"),
            "period_end": pd.Timestamp("2026-01-31"),
            "event_date": pd.Timestamp("2026-01-31"),
            "persnr": row["PersNr"],
            "reason_code": "QUIT",
            "reason_label": "Kündigung",
            "headcount_change": -1,
            "mak_change": -float(row.get("mak", 1.0) or 1.0),
            "Organisationseinheit": row.get("Organisationseinheit"),
            "Jobfamily": row.get("Jobfamily"),
            "OE-Cluster": row.get("OE-Cluster"),
        })
    zug_rows = [{
        "date": pd.Timestamp("2026-02-28"),
        "type": "New_Hire",
        "count": 1,
        "persnr": "NH-1",
        "Organisationseinheit": base.iloc[0].get("Organisationseinheit"),
        "source": "benchmark",
        "mak": 1.0,
        "Jobfamily": base.iloc[0].get("Jobfamily"),
        "OE-Cluster": base.iloc[0].get("OE-Cluster"),
        "TrfGr": base.iloc[0].get("TrfGr"),
        "St": base.iloc[0].get("St"),
        "Planstelle": base.iloc[0].get("Planstelle"),
    }]
    return pd.DataFrame(abg_rows), pd.DataFrame(zug_rows)


def benchmark_hybrid():
    hybrid = _load_page_module("*_Prognose_Hybrid.py", "benchmark_hybrid_page")
    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    df_atz = loader.load_atz_data_cached(str(ROOT), None, None, None)

    initial_load = _measure(
        lambda: hybrid._prepare_hybrid_employee_snapshot(snapshot_df, df_atz, current_stichtag=FROZEN_STICHTAG)
    )
    df_ma = hybrid._prepare_hybrid_employee_snapshot(snapshot_df, df_atz, current_stichtag=FROZEN_STICHTAG)
    empty_filters, changed_filters = _sample_filters(df_ma)
    raw_abg_events, raw_zug_events = _build_synthetic_hybrid_events(df_ma)
    base_view_state = hybrid._prepare_hybrid_view_state(
        raw_abg_events,
        raw_zug_events,
        snapshot_df,
        df_ma,
        empty_filters,
        ist_stichtag=pd.Timestamp("2025-12-31"),
        forecast_end_date=pd.Timestamp("2026-12-31"),
        freq_label="Monat",
    )

    warm_rerun = _measure(
        lambda: hybrid._prepare_hybrid_view_state(
            raw_abg_events,
            raw_zug_events,
            snapshot_df,
            df_ma,
            empty_filters,
            ist_stichtag=pd.Timestamp("2025-12-31"),
            forecast_end_date=pd.Timestamp("2026-12-31"),
            freq_label="Monat",
        )
    )
    tab_switch = _measure(
        lambda: hybrid._prepare_hybrid_view_state(
            raw_abg_events,
            raw_zug_events,
            snapshot_df,
            df_ma,
            empty_filters,
            ist_stichtag=pd.Timestamp("2025-12-31"),
            forecast_end_date=pd.Timestamp("2026-12-31"),
            freq_label="Monat",
        )
    )
    filter_change = _measure(
        lambda: hybrid._prepare_hybrid_view_state(
            raw_abg_events,
            raw_zug_events,
            snapshot_df,
            df_ma,
            changed_filters,
            ist_stichtag=pd.Timestamp("2025-12-31"),
            forecast_end_date=pd.Timestamp("2026-12-31"),
            freq_label="Monat",
        )
    )
    hybrid._build_hybrid_netto_chart_sources(base_view_state["combined_events_in_scope"])
    hybrid._build_hybrid_abgaenge_chart_bundle(base_view_state["abg_view_kpis"], base_view_state["filt_abg_events"])
    hybrid._build_hybrid_zugaenge_chart_sources(base_view_state["filt_zug_events"])
    chart_paths = {
        "netto_sources_reference": round(_measure(lambda: _reference_build_hybrid_netto_chart_sources(base_view_state["combined_events_in_scope"])), 4),
        "netto_sources_cached_warm": round(_measure(lambda: hybrid._build_hybrid_netto_chart_sources(base_view_state["combined_events_in_scope"])), 4),
        "abgaenge_chart_bundle_reference": round(_measure(lambda: __import__("abgaenge.visuals", fromlist=["build_charts"]).build_charts(base_view_state["abg_view_kpis"], base_view_state["filt_abg_events"])), 4),
        "abgaenge_chart_bundle_cached_warm": round(_measure(lambda: hybrid._build_hybrid_abgaenge_chart_bundle(base_view_state["abg_view_kpis"], base_view_state["filt_abg_events"])), 4),
        "zugaenge_sources_reference": round(_measure(lambda: _reference_build_hybrid_zugaenge_chart_sources(base_view_state["filt_zug_events"])), 4),
        "zugaenge_sources_cached_warm": round(_measure(lambda: hybrid._build_hybrid_zugaenge_chart_sources(base_view_state["filt_zug_events"])), 4),
    }

    return {
        "initial_load": round(initial_load, 4),
        "warm_rerun": round(warm_rerun, 4),
        "tab_switch": round(tab_switch, 4),
        "filter_change": round(filter_change, 4),
        "chart_paths": chart_paths,
    }


def benchmark_compact_plus_simulation():
    compact = _load_page_module("*_Kompakt.py", "benchmark_compact_page_for_sim")
    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    empty_filters, changed_filters = _sample_filters(snapshot_df)
    df_atz = loader.load_atz_data_cached(str(ROOT), None, None, None)

    def _trigger_simulation():
        sim_result = simulate_compact_snapshot(
            snapshot_df=snapshot_df,
            df_atz=df_atz,
            target_date=pd.Timestamp("2026-12-31"),
            base_date=FROZEN_STICHTAG,
            abgaenge_params={},
            zugaenge_params={},
        )
        prepared_df = compact.prepare_compact_data(sim_result.future_snapshot_df)
        filter_dataframe_by_view_filters(prepared_df, empty_filters)

    simulation_trigger = _measure(_trigger_simulation)
    sim_result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=pd.Timestamp("2026-12-31"),
        base_date=FROZEN_STICHTAG,
        abgaenge_params={},
        zugaenge_params={},
    )
    prepared_df = compact.prepare_compact_data(sim_result.future_snapshot_df)

    warm_rerun = _measure(lambda: _run_compact_bundle(compact, prepared_df, empty_filters))
    tab_switch = _measure(lambda: _run_compact_bundle(compact, prepared_df, empty_filters))
    filter_change = _measure(lambda: _run_compact_bundle(compact, prepared_df, changed_filters))
    compact.create_stacked_tariff_chart(prepared_df, "MAK_Calculated", value_type="mak")
    chart_paths = {
        "stacked_tariff_source_reference": round(_measure(lambda: _reference_prepare_stacked_tariff_chart_source(prepared_df, "MAK_Calculated")), 4),
        "stacked_tariff_chart_cached_warm": round(_measure(lambda: compact.create_stacked_tariff_chart(prepared_df, "MAK_Calculated", value_type="mak")), 4),
    }

    return {
        "simulation_trigger": round(simulation_trigger, 4),
        "warm_rerun": round(warm_rerun, 4),
        "tab_switch": round(tab_switch, 4),
        "filter_change": round(filter_change, 4),
        "chart_paths": chart_paths,
    }


def main():
    _freeze_context()
    _reset_state()
    results = {
        "kompakt": benchmark_kompakt(),
        "hybrid": benchmark_hybrid(),
        "kompakt_plus_simulation": benchmark_compact_plus_simulation(),
    }
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
