import copy
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st

from abgaenge.forecast import aggregate_forecast_results, run_forecast_abgaenge
from abgaenge.params import default_params
from abgaenge.schemas import REASON_ATZ_AR_TO_FR, REASON_ATZ_END, REASON_QUIT, REASON_RETIREMENT
from dataloader import loader


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "abgaenge_golden_master.pkl"
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


def _frozen_get_setting(key: str, default=None):
    return copy.deepcopy(FROZEN_SETTINGS.get(key, default))


@pytest.fixture(autouse=True)
def _freeze_context(monkeypatch):
    monkeypatch.setattr(loader, "get_setting", _frozen_get_setting)
    monkeypatch.setattr(loader, "get_current_stichtag", lambda: FROZEN_STICHTAG)


def _reset_state():
    st.cache_data.clear()
    st.session_state.clear()


def _prepare_abgaenge_page_input(snapshot_df: pd.DataFrame, df_atz: pd.DataFrame) -> pd.DataFrame:
    df_ma = snapshot_df.copy().dropna(subset=["PersNr"])

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


def _normalize_engine_input(df_ma: pd.DataFrame, df_atz: pd.DataFrame) -> pd.DataFrame:
    df_state = df_ma.copy()
    df_state["PersNr"] = df_state["PersNr"].astype(str).str.split(".").str[0].str.zfill(6)
    df_state = df_state.set_index("PersNr", drop=True)
    if df_state.index.duplicated().any():
        df_state = df_state[~df_state.index.duplicated(keep="first")]

    df_state["status_ruhend"] = df_state["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis"
    if "MAK_Calculated" in df_state.columns:
        df_state["mak"] = pd.to_numeric(df_state["MAK_Calculated"], errors="coerce").fillna(0.0)
    else:
        df_state["mak"] = 0.0

    atz_fr_active = set()
    if not df_atz.empty and {"PersNr", "Phase", "Beginn", "Ende"}.issubset(df_atz.columns):
        fr_rows = df_atz[df_atz["Phase"] == "FR"]
        fr_active = fr_rows[(fr_rows["Beginn"] <= FROZEN_STICHTAG) & (fr_rows["Ende"] >= FROZEN_STICHTAG)]
        atz_fr_active = set(fr_active["PersNr"].dropna().astype(str).str.split(".").str[0].str.zfill(6))

    all_atz_persnrs = set(df_atz["PersNr"].dropna().astype(str).str.split(".").str[0].str.zfill(6)) if not df_atz.empty else set()
    df_state["in_atz"] = df_state.index.isin(all_atz_persnrs)
    df_state["atz_fr_active"] = df_state.index.isin(atz_fr_active)
    df_state["active"] = True
    return df_state


def _reconstruct_final_state(df_ma: pd.DataFrame, df_atz: pd.DataFrame, events_df: pd.DataFrame) -> pd.DataFrame:
    df_state = _normalize_engine_input(df_ma, df_atz)
    if events_df.empty:
        return df_state.reset_index().rename(columns={"index": "PersNr"})

    working_events = events_df.copy()
    working_events["event_date"] = pd.to_datetime(working_events["event_date"])

    for event in working_events.itertuples(index=False):
        persnr = str(event.persnr)
        if persnr not in df_state.index:
            continue

        if event.reason_code == REASON_ATZ_AR_TO_FR:
            df_state.loc[persnr, "atz_fr_active"] = True
            df_state.loc[persnr, "mak"] = 0.0
        elif event.reason_code in {REASON_ATZ_END, REASON_RETIREMENT, REASON_QUIT}:
            df_state.loc[persnr, "mak"] = 0.0
            df_state.loc[persnr, "active"] = False

    return df_state.reset_index().rename(columns={"index": "PersNr"})


def _build_scenario():
    _reset_state()
    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    df_atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    df_ma = _prepare_abgaenge_page_input(snapshot_df, df_atz)
    params = default_params()
    params["components"]["ruhend"] = False
    params["random_seed"] = 42
    start_date = FROZEN_STICHTAG
    end_date = start_date + pd.DateOffset(months=24)
    return df_ma, df_atz, start_date, end_date, params


def test_run_forecast_abgaenge_matches_golden_master():
    df_ma, df_atz, start_date, end_date, params = _build_scenario()
    golden = pd.read_pickle(FIXTURE_PATH)

    result = run_forecast_abgaenge(df_ma, df_atz, start_date, end_date, "M", copy.deepcopy(params))
    final_state = _reconstruct_final_state(df_ma, df_atz, result["events_person_level"])

    assert result["assumptions"] == golden["assumptions"]
    pd.testing.assert_frame_equal(result["forecast_kpis"], golden["forecast_kpis"], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(result["events_person_level"], golden["events_person_level"], check_dtype=True, check_like=False)
    for table_name in sorted(golden["tables"]):
        pd.testing.assert_frame_equal(
            result["tables"][table_name],
            golden["tables"][table_name],
            check_dtype=True,
            check_like=False,
        )
    pd.testing.assert_frame_equal(final_state, golden["final_state"], check_dtype=True, check_like=False)


def test_aggregate_forecast_results_matches_golden_master_replay():
    df_ma, _, start_date, end_date, params = _build_scenario()
    golden = pd.read_pickle(FIXTURE_PATH)

    replay_kpis = aggregate_forecast_results(
        df_initial=df_ma,
        events_df=golden["events_person_level"].copy(),
        start_date=start_date,
        end_date=end_date,
        freq="M",
        params=copy.deepcopy(params),
    )

    pd.testing.assert_frame_equal(replay_kpis, golden["forecast_kpis"], check_dtype=True, check_like=False)
