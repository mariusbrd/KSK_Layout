import copy
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as default_abgaenge_params
from dataloader import loader
from dataloader.loader import calculate_mak_vectorized, load_atz_data_cached
from zugaenge.enrichment import build_jf_to_cluster_map
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as default_zugaenge_params


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "zugaenge_golden_master.pkl"
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
FROZEN_END_DATE = pd.Timestamp("2028-12-31")


def _frozen_get_setting(key: str, default=None):
    return copy.deepcopy(FROZEN_SETTINGS.get(key, default))


@pytest.fixture(autouse=True)
def _freeze_context(monkeypatch):
    monkeypatch.setattr(loader, "get_setting", _frozen_get_setting)
    monkeypatch.setattr(loader, "get_current_stichtag", lambda: FROZEN_STICHTAG)


def _reset_state():
    st.cache_data.clear()
    st.session_state.clear()


def _build_page4_snapshot(df_atz: pd.DataFrame) -> pd.DataFrame:
    snapshot_df_raw, _, _, _ = loader.load_and_prepare_data()
    df_ma_global = snapshot_df_raw.copy().dropna(subset=["PersNr"])

    atz_fr_persnr_set = set()
    if not df_atz.empty and {"PersNr", "Phase", "Beginn", "Ende"}.issubset(df_atz.columns):
        atz_fr = df_atz[
            (df_atz["Phase"] == "FR")
            & (df_atz["Beginn"] <= FROZEN_STICHTAG)
            & (df_atz["Ende"] >= FROZEN_STICHTAG)
        ]
        if not atz_fr.empty:
            atz_fr_persnr_set = set(atz_fr["PersNr"].dropna().astype(str).unique())

    df_ma_global = calculate_mak_vectorized(df_ma_global, atz_fr_persnr_set)

    agg_dict = {
        "MAK_Calculated": "sum",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Sollarbeitszeit": "sum",
        "Organisationseinheit": "first",
        "Jobfamily": "first",
        "TrfGr": "first",
        "St": "first",
    }
    for col in ["Geschlecht", "Planstelle", "active", "OE-Cluster", "JF-Cluster", "Ausbildung"]:
        if col in df_ma_global.columns:
            agg_dict[col] = "first"

    snapshot_df = df_ma_global.groupby("PersNr", as_index=False).agg(agg_dict)
    snapshot_df["Sollarbeitszeit"] = 39.0
    snapshot_df["BsGrd"] = snapshot_df["MAK_Calculated"] * 100.0
    snapshot_df["mak"] = snapshot_df["MAK_Calculated"]
    snapshot_df["active"] = True
    return snapshot_df


def _build_page4_vacancies(snapshot_df: pd.DataFrame, df_atz: pd.DataFrame, end_date: pd.Timestamp) -> pd.DataFrame:
    abg_res = run_forecast_abgaenge(
        df_ma=snapshot_df,
        df_atz=df_atz,
        start_date=FROZEN_STICHTAG,
        end_date=end_date,
        freq="M",
        params=default_abgaenge_params(),
    )
    exits = abg_res["events_person_level"][abg_res["events_person_level"]["headcount_change"] < 0]

    snap_lookup = snapshot_df.copy()
    snap_lookup["pid_str"] = snap_lookup["PersNr"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    snap_lookup = snap_lookup.set_index("pid_str")

    vacancies = []
    for _, row in exits.iterrows():
        pid = str(row["persnr"]).strip().replace(".0", "")
        leaver_jf = "Angestellte"
        leaver_oe_c = "Unclustered"

        if pid in snap_lookup.index:
            leaver_data = snap_lookup.loc[pid]
            if isinstance(leaver_data, pd.DataFrame):
                leaver_data = leaver_data.iloc[0]
            leaver_jf = leaver_data.get("Jobfamily", "Angestellte")
            leaver_oe_c = leaver_data.get("OE-Cluster", "Unclustered")

        vacancies.append(
            {
                "date": row["event_date"],
                "org_unit": row.get("Organisationseinheit", "Unbekannt"),
                "planstelle": row.get("Planstelle", "Unbekannt"),
                "persnr": row["persnr"],
                "Jobfamily": leaver_jf,
                "OE-Cluster": leaver_oe_c,
            }
        )

    return pd.DataFrame(vacancies)


def _build_scenario():
    _reset_state()
    df_atz = load_atz_data_cached(str(ROOT), None, None, None)
    snapshot_df = _build_page4_snapshot(df_atz)
    vacancies_df = _build_page4_vacancies(snapshot_df, df_atz, FROZEN_END_DATE)

    params = default_zugaenge_params()
    params["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(snapshot_df)
    params["random_seed"] = 42

    return {
        "snapshot_df": snapshot_df,
        "vacancies_df": vacancies_df,
        "params": params,
        "start_date": FROZEN_STICHTAG,
        "end_date": FROZEN_END_DATE,
    }


def test_page4_zugaenge_scenario_inputs_match_golden_fixture():
    scenario = _build_scenario()
    golden = pd.read_pickle(FIXTURE_PATH)

    assert scenario["params"] == golden["params"]
    assert scenario["start_date"] == golden["start_date"]
    assert scenario["end_date"] == golden["end_date"]
    pd.testing.assert_frame_equal(scenario["snapshot_df"], golden["snapshot_df"], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(scenario["vacancies_df"], golden["vacancies_df"], check_dtype=True, check_like=False)


def test_run_forecast_zugaenge_matches_golden_master():
    golden = pd.read_pickle(FIXTURE_PATH)

    result = run_forecast_zugaenge(
        df_snapshot=golden["snapshot_df"].copy(),
        start_date=golden["start_date"],
        end_date=golden["end_date"],
        freq="M",
        params=copy.deepcopy(golden["params"]),
        vacancies=golden["vacancies_df"].to_dict("records"),
    )

    assert result["debug_info"] == golden["result"]["debug_info"]
    pd.testing.assert_frame_equal(result["events"], golden["result"]["events"], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(result["final_state"], golden["result"]["final_state"], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(result["forecast_kpis"], golden["result"]["forecast_kpis"], check_dtype=True, check_like=False)


def test_run_forecast_zugaenge_is_seed_stable_on_repeated_runs():
    golden = pd.read_pickle(FIXTURE_PATH)

    result_a = run_forecast_zugaenge(
        df_snapshot=golden["snapshot_df"].copy(),
        start_date=golden["start_date"],
        end_date=golden["end_date"],
        freq="M",
        params=copy.deepcopy(golden["params"]),
        vacancies=golden["vacancies_df"].to_dict("records"),
    )
    result_b = run_forecast_zugaenge(
        df_snapshot=golden["snapshot_df"].copy(),
        start_date=golden["start_date"],
        end_date=golden["end_date"],
        freq="M",
        params=copy.deepcopy(golden["params"]),
        vacancies=golden["vacancies_df"].to_dict("records"),
    )

    assert result_a["debug_info"] == result_b["debug_info"]
    pd.testing.assert_frame_equal(result_a["events"], result_b["events"], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(result_a["final_state"], result_b["final_state"], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(result_a["forecast_kpis"], result_b["forecast_kpis"], check_dtype=True, check_like=False)
