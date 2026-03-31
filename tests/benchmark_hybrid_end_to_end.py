import copy
import importlib.util
import json
import statistics
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
from abgaenge import default_params as default_abgaenge_params
from abgaenge.forecast import run_forecast_abgaenge
from config.settings import DEFAULT_AZUBI_SALARIES, DEFAULT_COHORTS
from dataloader import loader
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as default_zugaenge_params


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
FROZEN_END_DATE = pd.Timestamp("2027-12-31")
DEFAULT_FILTERS = {
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


def _frozen_get_setting(key: str, default=None):
    return copy.deepcopy(FROZEN_SETTINGS.get(key, default))


def _load_hybrid_page_module():
    page_path = next((ROOT / "pages").glob("*_Prognose_Hybrid.py"))
    spec = importlib.util.spec_from_file_location("benchmark_hybrid_end_to_end_page", page_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _build_static_inputs(hybrid):
    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    df_atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    df_ma = hybrid._prepare_hybrid_employee_snapshot(snapshot_df, df_atz, current_stichtag=FROZEN_STICHTAG)

    params_abg = default_abgaenge_params()
    params_abg["random_seed"] = 42

    params_zug = default_zugaenge_params()
    params_zug["random_seed"] = 42
    params_zug["new_hires"]["strategy"] = "Fill Vacancies"
    params_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(df_ma)

    return snapshot_df, df_atz, df_ma, params_abg, params_zug


def _measure(fn):
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def benchmark_hybrid_end_to_end(runs: int = 5) -> dict:
    _freeze_context()
    _reset_state()
    hybrid = _load_hybrid_page_module()
    snapshot_df, df_atz, df_ma, params_abg, params_zug = _build_static_inputs(hybrid)

    stage_samples = {
        "forecast_abgaenge": [],
        "vacancy_extraction": [],
        "forecast_zugaenge": [],
        "enrich_zugaenge_events": [],
        "view_state_prepare": [],
        "chart_prep_netto": [],
        "chart_prep_abgaenge_bundle": [],
        "chart_prep_abgaenge_cluster": [],
        "chart_prep_zugaenge": [],
        "total_submit_to_chart_sources": [],
    }

    for _ in range(runs):
        total_start = time.perf_counter()

        t_abg, abg_res = _measure(
            lambda: run_forecast_abgaenge(
                df_ma=df_ma,
                df_atz=df_atz,
                start_date=FROZEN_STICHTAG,
                end_date=FROZEN_END_DATE,
                freq="M",
                params=copy.deepcopy(params_abg),
            )
        )
        stage_samples["forecast_abgaenge"].append(t_abg)

        t_vac, vacancies = _measure(
            lambda: hybrid._build_hybrid_vacancies_from_events(abg_res["events_person_level"], df_ma)
        )
        stage_samples["vacancy_extraction"].append(t_vac)

        t_zug, zug_res = _measure(
            lambda: run_forecast_zugaenge(
                df_snapshot=df_ma,
                start_date=FROZEN_STICHTAG,
                end_date=FROZEN_END_DATE,
                freq="M",
                params=copy.deepcopy(params_zug),
                vacancies=vacancies,
            )
        )
        stage_samples["forecast_zugaenge"].append(t_zug)

        zug_events = zug_res["events"]
        t_enrich, enriched = _measure(
            lambda: enrich_zugaenge_events(zug_events, df_ma, params_zug) if not zug_events.empty else zug_events
        )
        zug_res["events"] = enriched
        stage_samples["enrich_zugaenge_events"].append(t_enrich)

        t_view, view_state = _measure(
            lambda: hybrid._prepare_hybrid_view_state(
                abg_res["events_person_level"].copy(),
                zug_res["events"].copy(),
                snapshot_df,
                df_ma,
                DEFAULT_FILTERS,
                ist_stichtag=FROZEN_STICHTAG,
                forecast_end_date=FROZEN_END_DATE,
                freq_label="Monat",
                base_abg_kpis=abg_res.get("forecast_kpis"),
            )
        )
        stage_samples["view_state_prepare"].append(t_view)

        t_netto, _ = _measure(lambda: hybrid._build_hybrid_netto_chart_sources(view_state["combined_events_in_scope"]))
        stage_samples["chart_prep_netto"].append(t_netto)

        t_abg_bundle, _ = _measure(lambda: hybrid._build_hybrid_abgaenge_chart_bundle(view_state["abg_view_kpis"], view_state["filt_abg_events"]))
        stage_samples["chart_prep_abgaenge_bundle"].append(t_abg_bundle)

        t_abg_cluster, _ = _measure(lambda: hybrid._build_hybrid_abgaenge_cluster_source(view_state["filt_abg_events"]))
        stage_samples["chart_prep_abgaenge_cluster"].append(t_abg_cluster)

        t_zug_chart, _ = _measure(lambda: hybrid._build_hybrid_zugaenge_chart_sources(view_state["filt_zug_events"]))
        stage_samples["chart_prep_zugaenge"].append(t_zug_chart)

        stage_samples["total_submit_to_chart_sources"].append(time.perf_counter() - total_start)

    return {
        "runs": runs,
        "samples": {key: [round(value, 4) for value in values] for key, values in stage_samples.items()},
        "median": {key: round(statistics.median(values), 4) for key, values in stage_samples.items()},
        "min": {key: round(min(values), 4) for key, values in stage_samples.items()},
    }


def main():
    print(json.dumps(benchmark_hybrid_end_to_end(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
