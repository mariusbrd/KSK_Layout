from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from components.sidebar import filter_dataframe_by_view_filters, get_active_view_filters
from dataloader.cluster_manager import load_cluster_mappings_from_source
from dataloader.cluster_resolver import deserialize_active_cluster_source, get_active_cluster_source
from dataloader.compact_simulation_engine import (
    _normalize_persnr_series,
    simulate_compact_snapshot,
)
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module


PLACEHOLDER_JOBFAMILIES = {
    "UNMAPPED",
    "Kundenberatung Privat",
    "Kundenberatung Firmen",
    "Vermögensberatung",
    "Baufinanzierung",
    "Marktfolge Kredit",
    "Marktfolge Passiv",
    "IT & Digitalisierung",
    "Personal & Organisation",
    "Controlling & Finanzen",
    "Compliance & Recht",
    "Marketing & Vertriebssteuerung",
    "Führungskräfte",
}


@pytest.fixture(scope="module")
def compact_plus_future_context():
    snapshot_df, history_df, _, summary = load_and_prepare_data(show_status_messages=False)
    active_cluster_source = deserialize_active_cluster_source(summary.get("active_cluster_source"))
    if active_cluster_source is None:
        active_cluster_source = get_active_cluster_source()

    cluster_mapping_bundle = load_cluster_mappings_from_source(active_cluster_source)
    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    target_date = (base_date + pd.DateOffset(years=2)).normalize()
    df_atz = load_atz_data_cached(str(ROOT))

    sim_result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
        base_date=base_date,
        active_cluster_source=active_cluster_source,
        cluster_mapping_bundle=cluster_mapping_bundle,
        cluster_source_signature=summary.get("active_cluster_source_signature"),
    )

    future_df = sim_result.future_snapshot_df.copy()
    current_df = snapshot_df.copy()
    compact = load_compact_page_module()
    prepared_df = compact.prepare_compact_data(future_df)
    final_jobfamilies = {
        str(value).strip()
        for value in cluster_mapping_bundle.jf_map.values()
        if str(value).strip()
    }

    return {
        "current_df": current_df,
        "future_df": future_df,
        "prepared_df": prepared_df,
        "history_df": history_df,
        "sim_result": sim_result,
        "df_atz": df_atz,
        "active_cluster_source": active_cluster_source,
        "cluster_mapping_bundle": cluster_mapping_bundle,
        "cluster_source_signature": summary.get("active_cluster_source_signature"),
        "final_jobfamilies": final_jobfamilies,
        "base_date": base_date,
        "target_date": target_date,
    }


def _active_non_vacant(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    result = df.copy()
    if "PersNr" in result.columns:
        result["PersNr_norm"] = _normalize_persnr_series(result["PersNr"].fillna(""))
        result = result[result["PersNr_norm"].ne("")]
    if "Is_Vacant" in result.columns:
        result = result[result["Is_Vacant"].ne(True)]
    return result


def _mak_sum(df: pd.DataFrame) -> float:
    for col in ("MAK_Calculated", "mak", "MAK"):
        if col in df.columns:
            return float(pd.to_numeric(df[col], errors="coerce").fillna(0.0).sum())
    return 0.0


def _eur_sum(df: pd.DataFrame) -> float:
    if "Total_Cost_Year" not in df.columns:
        return 0.0
    return float(pd.to_numeric(df["Total_Cost_Year"], errors="coerce").fillna(0.0).sum())


def test_future_snapshot_exposes_only_final_jobfamilies(compact_plus_future_context):
    future_active = _active_non_vacant(compact_plus_future_context["future_df"])
    final_jobfamilies = compact_plus_future_context["final_jobfamilies"]

    visible_jobfamilies = {
        str(value).strip()
        for value in future_active["Jobfamily"].dropna().astype(str)
        if str(value).strip()
    }

    assert visible_jobfamilies
    assert visible_jobfamilies <= final_jobfamilies
    assert visible_jobfamilies.isdisjoint(PLACEHOLDER_JOBFAMILIES)
    assert set(future_active["JF-Cluster"].dropna().astype(str).str.strip()) <= final_jobfamilies
    assert (
        future_active["Jobfamily"].astype(str).str.strip()
        == future_active["JF-Cluster"].astype(str).str.strip()
    ).all()


def test_existing_people_keep_jobfamily_when_still_active(compact_plus_future_context):
    current_active = _active_non_vacant(compact_plus_future_context["current_df"])
    future_active = _active_non_vacant(compact_plus_future_context["future_df"])

    key_cols = ["PersNr_norm", "Organisationseinheit", "Planstelle"]
    current_keyed = current_active[key_cols + ["Jobfamily"]].rename(
        columns={"Jobfamily": "Jobfamily_current"}
    )
    future_keyed = future_active[key_cols + ["Jobfamily"]].rename(
        columns={"Jobfamily": "Jobfamily_future"}
    )

    comparable = future_keyed.merge(current_keyed, on=key_cols, how="inner")

    assert not comparable.empty
    changed = comparable[
        comparable["Jobfamily_future"].astype(str).str.strip()
        != comparable["Jobfamily_current"].astype(str).str.strip()
    ]
    assert changed.empty, changed.head(25).to_string(index=False)


def test_sidebar_jobfamily_filters_match_direct_future_aggregation(compact_plus_future_context):
    future_active = _active_non_vacant(compact_plus_future_context["future_df"])
    jobfamilies = sorted(future_active["Jobfamily"].dropna().astype(str).str.strip().unique())

    assert jobfamilies

    for jobfamily in jobfamilies:
        active_filters = {
            "selected_jobfamilies": [jobfamily],
            "selected_org_units": [],
            "selected_oe_clusters": [],
            "selected_jf_clusters": [],
            "selected_genders": [],
            "selected_employment": [],
            "selected_atz_status": [],
            "selected_cohorts": [],
            "selected_education": [],
        }
        filtered = filter_dataframe_by_view_filters(future_active, active_filters)
        direct = future_active[
            future_active["Jobfamily"].astype(str).str.strip().eq(jobfamily)
        ]

        assert not filtered.empty, f"Jobfamily filter returned no rows for {jobfamily!r}"
        assert set(filtered["Jobfamily"].astype(str).str.strip()) == {jobfamily}
        assert len(filtered) == len(direct)
        assert pytest.approx(_mak_sum(filtered), rel=1e-9, abs=1e-9) == _mak_sum(direct)
        assert pytest.approx(_eur_sum(filtered), rel=1e-9, abs=1e-6) == _eur_sum(direct)


def test_session_state_jobfamily_navigation_filters_prepared_simulation_df(compact_plus_future_context):
    prepared_df = _active_non_vacant(compact_plus_future_context["prepared_df"])
    jobfamilies = sorted(prepared_df["Jobfamily"].dropna().astype(str).str.strip().unique())

    assert jobfamilies

    for jobfamily in jobfamilies:
        st.session_state["selected_jobfamilies"] = [jobfamily]
        st.session_state["selected_org_units"] = []
        st.session_state["selected_oe_clusters"] = []
        st.session_state["selected_jf_clusters"] = []
        st.session_state["selected_genders"] = []
        st.session_state["selected_employment"] = []
        st.session_state["selected_atz_status"] = []
        st.session_state["selected_cohorts"] = []
        st.session_state["selected_education"] = []

        active_filters = get_active_view_filters()
        filtered = filter_dataframe_by_view_filters(prepared_df, active_filters)
        direct = prepared_df[
            prepared_df["Jobfamily"].astype(str).str.strip().eq(jobfamily)
        ]

        assert not filtered.empty, f"Prepared navigation filter returned no rows for {jobfamily!r}"
        assert set(filtered["Jobfamily"].astype(str).str.strip()) == {jobfamily}
        assert len(filtered) == len(direct)
        assert pytest.approx(_mak_sum(filtered), rel=1e-9, abs=1e-9) == _mak_sum(direct)
        assert pytest.approx(_eur_sum(filtered), rel=1e-9, abs=1e-6) == _eur_sum(direct)


@pytest.mark.parametrize(
    ("label", "offset"),
    [
        ("short_plus_1_month", pd.DateOffset(months=1)),
        ("medium_plus_2_years", pd.DateOffset(years=2)),
        ("long_plus_5_years", pd.DateOffset(years=5)),
    ],
)
def test_future_jobfamilies_are_consistent_across_target_dates(
    compact_plus_future_context,
    label,
    offset,
):
    base_date = compact_plus_future_context["base_date"]
    target_date = (base_date + offset).normalize()
    sim_result = simulate_compact_snapshot(
        snapshot_df=compact_plus_future_context["current_df"],
        df_atz=compact_plus_future_context["df_atz"],
        target_date=target_date,
        base_date=base_date,
        active_cluster_source=compact_plus_future_context["active_cluster_source"],
        cluster_mapping_bundle=compact_plus_future_context["cluster_mapping_bundle"],
        cluster_source_signature=compact_plus_future_context["cluster_source_signature"],
    )

    future_active = _active_non_vacant(sim_result.future_snapshot_df)
    final_jobfamilies = compact_plus_future_context["final_jobfamilies"]

    assert not future_active.empty, f"{label}: no active future rows"
    assert set(future_active["Jobfamily"].dropna().astype(str).str.strip()) <= final_jobfamilies
    assert (
        future_active["Jobfamily"].astype(str).str.strip()
        == future_active["JF-Cluster"].astype(str).str.strip()
    ).all(), f"{label}: Jobfamily and JF-Cluster diverged"
    assert set(future_active["Jobfamily"].dropna().astype(str).str.strip()).isdisjoint(
        PLACEHOLDER_JOBFAMILIES
    )


def test_simulated_new_people_have_final_jobfamilies_in_future_snapshot(compact_plus_future_context):
    current_active = _active_non_vacant(compact_plus_future_context["current_df"])
    future_active = _active_non_vacant(compact_plus_future_context["future_df"])
    final_jobfamilies = compact_plus_future_context["final_jobfamilies"]

    current_ids = set(current_active["PersNr_norm"])
    new_people = future_active[~future_active["PersNr_norm"].isin(current_ids)].copy()

    assert compact_plus_future_context["sim_result"].metadata.get("zugaenge_events", 0) > 0
    assert not new_people.empty
    assert set(new_people["Jobfamily"].dropna().astype(str).str.strip()) <= final_jobfamilies
    assert (
        new_people["Jobfamily"].astype(str).str.strip()
        == new_people["JF-Cluster"].astype(str).str.strip()
    ).all()
    assert set(new_people["Jobfamily"].dropna().astype(str).str.strip()).isdisjoint(
        PLACEHOLDER_JOBFAMILIES
    )

    prepared_active = _active_non_vacant(compact_plus_future_context["prepared_df"])
    for jobfamily in sorted(new_people["Jobfamily"].dropna().astype(str).str.strip().unique()):
        filtered = filter_dataframe_by_view_filters(
            prepared_active,
            {
                "selected_jobfamilies": [jobfamily],
                "selected_org_units": [],
                "selected_oe_clusters": [],
                "selected_jf_clusters": [],
                "selected_genders": [],
                "selected_employment": [],
                "selected_atz_status": [],
                "selected_cohorts": [],
                "selected_education": [],
            },
        )
        filtered_ids = set(_normalize_persnr_series(filtered["PersNr"].fillna("")))
        expected_ids = set(new_people.loc[
            new_people["Jobfamily"].astype(str).str.strip().eq(jobfamily),
            "PersNr_norm",
        ])
        assert expected_ids <= filtered_ids
