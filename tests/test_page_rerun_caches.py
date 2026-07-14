import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from plotly.utils import PlotlyJSONEncoder
import streamlit as st

from abgaenge.visuals import build_charts as build_abgaenge_charts
import kpi_reference
from config.settings import DEFAULT_AZUBI_SALARIES, DEFAULT_COHORTS
from dataloader import loader


ROOT = Path(__file__).resolve().parents[1]
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


@pytest.fixture(autouse=True)
def _freeze_context(monkeypatch):
    monkeypatch.setattr(loader, "get_setting", _frozen_get_setting)
    monkeypatch.setattr(kpi_reference, "get_setting", _frozen_get_setting)
    monkeypatch.setattr(loader, "get_current_stichtag", lambda: FROZEN_STICHTAG)
    monkeypatch.setattr(kpi_reference, "get_current_stichtag", lambda: FROZEN_STICHTAG)
    monkeypatch.setattr(
        loader.np.random,
        "normal",
        lambda loc=0.0, scale=1.0, size=None: float(loc) if size is None else np.full(size, float(loc)),
    )


def _reset_state():
    st.cache_data.clear()
    st.session_state.clear()
    st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
    st.session_state["azubi_salaries"] = DEFAULT_AZUBI_SALARIES.copy()
    st.session_state["vorstand_jahresgehalt"] = 200000.0
    st.session_state["employer_cost_factor"] = loader.EMPLOYER_COST_FACTOR
    st.session_state["selected_org_units"] = []
    st.session_state["selected_jobfamilies"] = []
    st.session_state["selected_cohorts"] = []
    st.session_state["selected_genders"] = ["m", "w"]
    st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit", "Inaktiv"]
    st.session_state["selected_education"] = []
    st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]
    st.session_state["selected_oe_clusters"] = []
    st.session_state["selected_jf_clusters"] = []


def _reference_prepare_hybrid_employee_snapshot(snapshot_df: pd.DataFrame, df_atz: pd.DataFrame) -> pd.DataFrame:
    df_ma = snapshot_df.dropna(subset=["PersNr"]).copy()

    atz_fr_persnr_set = set()
    if not df_atz.empty and {"PersNr", "Phase", "Beginn", "Ende"}.issubset(df_atz.columns):
        atz_fr = df_atz[
            (df_atz["Phase"] == "FR") &
            (df_atz["Beginn"] <= FROZEN_STICHTAG) &
            (df_atz["Ende"] >= FROZEN_STICHTAG)
        ]
        if not atz_fr.empty:
            atz_fr_persnr_set = set(atz_fr["PersNr"].dropna().astype(str).unique())

    df_ma = loader.calculate_mak_vectorized(df_ma, atz_fr_persnr_set)

    agg_dict = {
        "MAK_Calculated": "sum",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Sollarbeitszeit": "sum",
        "Organisationseinheit": "first",
    }
    # Mirrors pages/5_Prognose_Hybrid.py::_prepare_hybrid_employee_snapshot(): prefer the
    # person-capped MAK_Reporting sum over the raw (potentially double-counted) MAK_Calculated
    # sum for people with multiple active Planstellen.
    if "MAK_Reporting" in df_ma.columns:
        agg_dict["MAK_Reporting"] = "sum"
    for col in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St", "OE-Cluster", "JF-Cluster", "BsGrd"]:
        if col in df_ma.columns:
            agg_dict[col] = "first"

    df_employee_agg = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
    if "MAK_Reporting" in df_employee_agg.columns:
        df_employee_agg["MAK_Calculated"] = pd.to_numeric(
            df_employee_agg["MAK_Reporting"], errors="coerce"
        ).fillna(pd.to_numeric(df_employee_agg["MAK_Calculated"], errors="coerce").fillna(0.0))
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


def _reference_prepare_stacked_tariff_chart_source(df: pd.DataFrame, value_col: str) -> tuple[pd.DataFrame, list[str]]:
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


def _reference_prepare_stacked_tariff_comparison_source(
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


def _reference_build_hybrid_netto_chart_sources(combined_events_in_scope: pd.DataFrame) -> dict:
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


def _reference_build_hybrid_zugaenge_chart_sources(filt_zug_events: pd.DataFrame) -> dict:
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


@pytest.mark.parametrize(
    ("dimension_col", "value_col", "include_soll", "soll_col"),
    [
        ("Geschlecht", "Headcount", False, None),
        ("Ausbildung", "MAK_Calculated", False, None),
        ("Ausbildung", "MAK_Calculated", True, "Soll_FTE"),
    ],
)
def test_compact_breakdown_cache_matches_clean_logic(dimension_col, value_col, include_soll, soll_col):
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "compact_page_rerun_cache_test")

    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    prepared_df = compact.prepare_compact_data(snapshot_df)

    expected = compact._create_breakdown_table_clean(
        prepared_df,
        dimension_col,
        value_col,
        include_soll=include_soll,
        soll_col=soll_col,
    )
    actual = compact.create_breakdown_table(
        prepared_df,
        dimension_col,
        value_col,
        include_soll=include_soll,
        soll_col=soll_col,
    )

    pd.testing.assert_frame_equal(actual, expected, check_dtype=True, check_like=False)


def test_hybrid_employee_snapshot_cache_matches_reference_logic():
    _reset_state()
    hybrid = _load_page_module("*_Prognose_Hybrid.py", "hybrid_page_rerun_cache_test")

    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    df_atz = loader.load_atz_data_cached(str(ROOT), None, None, None)

    expected = _reference_prepare_hybrid_employee_snapshot(snapshot_df, df_atz)
    actual = hybrid._prepare_hybrid_employee_snapshot(
        snapshot_df,
        df_atz,
        current_stichtag=FROZEN_STICHTAG,
    )

    pd.testing.assert_frame_equal(actual, expected, check_dtype=True, check_like=False)


def test_hybrid_distribution_base_cache_matches_reference():
    _reset_state()
    hybrid = _load_page_module("*_Prognose_Hybrid.py", "hybrid_page_distribution_cache_test")

    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    df_atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    df_ma = hybrid._prepare_hybrid_employee_snapshot(
        snapshot_df,
        df_atz,
        current_stichtag=FROZEN_STICHTAG,
    )

    expected = (
        df_ma.groupby(["Jobfamily", "OE-Cluster"]).size().reset_index(name="Count")
    )
    expected["Share %"] = (expected["Count"] / expected["Count"].sum()).round(4)
    expected = expected.sort_values(
        ["Count", "Jobfamily", "OE-Cluster"],
        ascending=[False, True, True],
    ).reset_index(drop=True)

    actual = hybrid._build_hybrid_distribution_base(df_ma)

    pd.testing.assert_frame_equal(actual, expected, check_dtype=True, check_like=False)


@pytest.mark.parametrize("value_col", ["MAK_Calculated", "Headcount"])
def test_compact_stacked_tariff_chart_source_cache_matches_reference(value_col):
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", f"compact_tariff_source_{value_col}")

    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    prepared_df = compact.prepare_compact_data(snapshot_df)

    expected_pivot, expected_groups = _reference_prepare_stacked_tariff_chart_source(prepared_df, value_col)
    actual_pivot, actual_groups = compact._prepare_stacked_tariff_chart_source(prepared_df, value_col)

    pd.testing.assert_frame_equal(actual_pivot, expected_pivot, check_dtype=True, check_like=False)
    assert actual_groups == expected_groups


def test_compact_stacked_tariff_comparison_source_cache_matches_reference():
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "compact_tariff_comparison_source")

    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    prepared_df = compact.prepare_compact_data(snapshot_df)

    expected_ist, expected_soll, expected_groups = _reference_prepare_stacked_tariff_comparison_source(
        prepared_df,
        "MAK_Calculated",
        "Soll_FTE",
    )
    actual_ist, actual_soll, actual_groups = compact._prepare_stacked_tariff_comparison_chart_source(
        prepared_df,
        "MAK_Calculated",
        "Soll_FTE",
    )

    pd.testing.assert_frame_equal(actual_ist, expected_ist, check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(actual_soll, expected_soll, check_dtype=True, check_like=False)
    assert actual_groups == expected_groups


def test_hybrid_netto_chart_sources_cache_matches_reference():
    _reset_state()
    hybrid = _load_page_module("*_Prognose_Hybrid.py", "hybrid_netto_chart_source_test")

    combined_events_in_scope = pd.DataFrame(
        [
            {
                "event_date": pd.Timestamp("2026-01-31"),
                "type": "Azubi_Hire",
                "headcount_change": 2,
                "reason_label": "Azubi Neueinstellung (externer Zugang)",
                "mak_change": 0.0,
            },
            {
                "event_date": pd.Timestamp("2026-02-28"),
                "type": "Azubi_Conversion_In",
                "headcount_change": 1,
                "reason_label": "Übernahme nach Ausbildungsabschluss (interne MAK-wirksame Stellenbesetzung)",
                "mak_change": 1.0,
            },
            {
                "event_date": pd.Timestamp("2026-02-28"),
                "type": "New_Hire",
                "headcount_change": 3,
                "reason_label": "Zugang (extern)",
                "mak_change": 3.0,
            },
        ]
    )

    expected = _reference_build_hybrid_netto_chart_sources(combined_events_in_scope)
    actual = hybrid._build_hybrid_netto_chart_sources(combined_events_in_scope)

    for key in ["df_ext", "df_conv", "driver_agg"]:
        pd.testing.assert_frame_equal(actual[key], expected[key], check_dtype=True, check_like=False)


def test_hybrid_zugaenge_chart_sources_cache_matches_reference():
    _reset_state()
    hybrid = _load_page_module("*_Prognose_Hybrid.py", "hybrid_zugaenge_chart_source_test")

    filt_zug_events = pd.DataFrame(
        [
            {"date": pd.Timestamp("2026-01-31"), "type": "Azubi_Hire", "OE-Cluster": "A"},
            {"date": pd.Timestamp("2026-02-28"), "type": "Azubi_Conversion_In", "OE-Cluster": "A"},
            {"date": pd.Timestamp("2026-02-28"), "type": "New_Hire", "OE-Cluster": "B"},
            {"date": pd.Timestamp("2026-03-31"), "type": "Ignore_Me", "OE-Cluster": "B"},
        ]
    )

    expected = _reference_build_hybrid_zugaenge_chart_sources(filt_zug_events)
    actual = hybrid._build_hybrid_zugaenge_chart_sources(filt_zug_events)

    pd.testing.assert_frame_equal(actual["events_chart"], expected["events_chart"], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(actual["z_stats"], expected["z_stats"], check_dtype=True, check_like=False)


def test_hybrid_abgaenge_chart_bundle_cache_matches_reference():
    _reset_state()
    hybrid = _load_page_module("*_Prognose_Hybrid.py", "hybrid_abgaenge_chart_bundle_test")

    abg_view_kpis = pd.DataFrame(
        {
            "period_label": ["Jan 2026", "Feb 2026"],
            "headcount_end": [100, 98],
            "mak_end": [95.0, 93.5],
        }
    )
    filt_abg_events = pd.DataFrame(
        {
            "period_label": ["Jan 2026", "Jan 2026", "Feb 2026"],
            "reason_code": ["QUIT", "RETIREMENT", "QUIT"],
            "headcount_change": [-1, -1, -1],
            "mak_change": [-1.0, -1.0, -1.0],
        }
    )

    expected = build_abgaenge_charts(abg_view_kpis, filt_abg_events)
    actual = hybrid._build_hybrid_abgaenge_chart_bundle(abg_view_kpis, filt_abg_events)

    assert set(actual.keys()) == set(expected.keys())
    for key in expected:
        assert json.dumps(actual[key].to_plotly_json(), sort_keys=True, cls=PlotlyJSONEncoder) == json.dumps(
            expected[key].to_plotly_json(),
            sort_keys=True,
            cls=PlotlyJSONEncoder,
        )
