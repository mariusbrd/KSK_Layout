from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

import pandas as pd
import pytest
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))

from abgaenge.params import default_params as default_abgaenge_params
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.loader import calculate_mak_vectorized
from utils.simulation_params import SESSION_KEY, get_compact_plus_params
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as default_zugaenge_params


BASE_DATE = pd.Timestamp("2025-12-31")
EVENT_COMPARE_COLUMNS = [
    "date",
    "type",
    "count",
    "persnr",
    "org_unit",
    "source",
    "mak",
    "Jobfamily",
    "Organisationseinheit",
    "OE-Cluster",
    "JF-Cluster",
    "TrfGr",
    "St",
    "Planstelle",
    "Geschlecht",
    "Text Gsch",
    "Ausbildung",
    "is_forecast",
    "entry_date",
    "graduation_date",
    "cohort",
    "Diagnose-Quelle",
    "_jf_fallback",
]


@dataclass(frozen=True)
class HiringParityScenario:
    name: str
    horizon_months: int
    azubi: dict[str, Any] | None = None
    trainee: dict[str, Any] | None = None
    new_hires: dict[str, Any] | None = None
    expect_zero_events: bool = False


SCENARIOS = [
    HiringParityScenario("baseline_defaults_12m", 12),
    HiringParityScenario(
        "all_zugaenge_disabled_36m",
        36,
        azubi={"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={"active": False, "count_per_year": 0},
        expect_zero_events=True,
    ),
    HiringParityScenario(
        "azubi_only_matrix_off_24m",
        24,
        azubi={
            "active": True,
            "new_cases_per_year": 12,
            "retention_rate": 1.0,
            "strategy": "Random",
            "use_takeover_matrix": False,
            "duration_years": 1.0,
            "graduation_mode": "nearest_cycle",
        },
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={"active": False, "count_per_year": 0},
    ),
    HiringParityScenario(
        "azubi_only_takeover_matrix_36m",
        36,
        azubi={
            "active": True,
            "new_cases_per_year": 12,
            "retention_rate": 1.0,
            "strategy": "Random",
            "duration_years": 1.0,
            "graduation_mode": "nearest_cycle",
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {"Beratung": 1.0, "Service": 0.0, "Betrieb": 0.0},
        },
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={"active": False, "count_per_year": 0},
    ),
    HiringParityScenario(
        "trainee_only_24m",
        24,
        azubi={"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        trainee={"active": True, "new_cases_per_year": 12, "strategy": "Random"},
        new_hires={"active": False, "count_per_year": 0},
    ),
    HiringParityScenario(
        "new_hires_only_random_12m",
        12,
        azubi={"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={"active": True, "count_per_year": 12, "strategy": "Random", "distribution": []},
    ),
    HiringParityScenario(
        "new_hires_only_distribution_36m",
        36,
        azubi={"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={
            "active": True,
            "count_per_year": 12,
            "strategy": "Random",
            "distribution": [
                {"Jobfamily": "Beratung", "OE-Cluster": "Markt", "Share %": 0.7},
                {"Jobfamily": "Betrieb", "OE-Cluster": "Betrieb", "Share %": 0.3},
            ],
        },
    ),
]


def _employee(
    persnr: str,
    age: int,
    jobfamily: str,
    org_unit: str,
    oe_cluster: str,
    jf_cluster: str,
    *,
    mak: float = 1.0,
    geschlecht: str = "w",
    ausbildung: str = "Bankberufsabschluss",
) -> dict[str, Any]:
    return {
        "PersNr": persnr,
        "Personalnummer": persnr,
        "Is_Vacant": False,
        "GebDatum": BASE_DATE - pd.DateOffset(years=age),
        "Eintritt": BASE_DATE - pd.DateOffset(years=8),
        "Austritt": pd.NaT,
        "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
        "Sollarbeitszeit": 39.0,
        "Soll_FTE": mak,
        "FTE_person": mak,
        "FTE_assigned": mak,
        "BsGrd": mak * 100.0,
        "MAK_Calculated": mak,
        "MAK": mak,
        "mak": mak,
        "Organisationseinheit": org_unit,
        "Kürzel OrgEinheit": str(int(persnr) * 10).zfill(4),
        "Planstelle": jobfamily,
        "Jobfamily": jobfamily,
        "TrfGr": "E9A",
        "St": 3,
        "Geschlecht": geschlecht,
        "Text Gsch": {"w": "weiblich", "m": "männlich", "d": "divers"}.get(geschlecht, geschlecht),
        "Vertragsart": "Unbefristet",
        "MitarbGruppenbez.": "Beschäftigte",
        "Ausbildung": ausbildung,
        "ATZ_Status": "Kein ATZ",
        "OE-Cluster": oe_cluster,
        "JF-Cluster": jf_cluster,
    }


def _snapshot_for_hiring_parity() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _employee("000001", 42, "Beratung", "OE Beratung", "Markt", "Beratung", geschlecht="w"),
            _employee("000002", 38, "Service", "OE Service", "Betrieb", "Service", mak=0.8, geschlecht="m"),
            _employee("000003", 51, "Betrieb", "OE Betrieb", "Betrieb", "Betrieb", mak=0.9, geschlecht="w"),
            _employee("000004", 47, "Beratung", "OE Beratung", "Markt", "Beratung", mak=0.7, geschlecht="m"),
            _employee("000005", 33, "Service", "OE Service", "Betrieb", "Service", geschlecht="w"),
        ]
    )


def _inactive_abgaenge_params() -> dict[str, Any]:
    params = default_abgaenge_params()
    params["components"] = {
        "atz": False,
        "retirement": False,
        "quit": False,
        "ruhend": False,
    }
    params["atz"]["new_atz_rate"] = 0.0
    params["retirement"]["rent_rate_65"] = 0.0
    params["retirement"]["rent_rate_60_65"] = 0.0
    params["quit"]["quit_rate_base"] = 0.0
    params["ruhend"]["ruhend_new_cases_per_year"] = 0
    params["random_seed"] = 123
    return params


def _zugaenge_params_for(scenario: HiringParityScenario, snapshot_df: pd.DataFrame) -> dict[str, Any]:
    params = default_zugaenge_params()
    params["random_seed"] = 123
    params["available_org_units"] = sorted(snapshot_df["Organisationseinheit"].dropna().astype(str).unique().tolist())
    params["org_unit_to_cluster_map"] = (
        snapshot_df.drop_duplicates("Organisationseinheit")
        .set_index("Organisationseinheit")["OE-Cluster"]
        .astype(str)
        .to_dict()
    )
    params["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(snapshot_df)
    if scenario.azubi:
        params["azubi"].update(deepcopy(scenario.azubi))
    if scenario.trainee:
        params["trainee"].update(deepcopy(scenario.trainee))
    if scenario.new_hires:
        params["new_hires"].update(deepcopy(scenario.new_hires))
    return params


def _page4_like_snapshot(snapshot_df: pd.DataFrame, df_atz: pd.DataFrame, stichtag: pd.Timestamp) -> pd.DataFrame:
    df_ma = snapshot_df.copy()
    df_ma = df_ma.dropna(subset=["PersNr"]).copy()

    atz_fr_persnr_set = set()
    if not df_atz.empty and {"PersNr", "Phase", "Beginn", "Ende"}.issubset(df_atz.columns):
        atz_fr = df_atz[
            (df_atz["Phase"] == "FR")
            & (pd.to_datetime(df_atz["Beginn"], errors="coerce") <= stichtag)
            & (pd.to_datetime(df_atz["Ende"], errors="coerce") >= stichtag)
        ]
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
        "Jobfamily": "first",
        "TrfGr": "first",
        "St": "first",
    }
    for col in ["Geschlecht", "Planstelle", "active", "OE-Cluster", "JF-Cluster", "Ausbildung", "Text Gsch", "Vertragsart"]:
        if col in df_ma.columns:
            agg_dict[col] = "first"

    out = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
    out["Sollarbeitszeit"] = 39.0
    out["BsGrd"] = out["MAK_Calculated"] * 100.0
    out["mak"] = out["MAK_Calculated"]
    out["active"] = True
    return out


def _normalize_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=[col for col in EVENT_COMPARE_COLUMNS if col in events.columns])
    out = events.copy()
    for col in ["date", "entry_date", "graduation_date"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.normalize()
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].fillna("").astype(str)
    keep = [col for col in EVENT_COMPARE_COLUMNS if col in out.columns]
    out = out[keep].sort_values(keep).reset_index(drop=True)
    return out


def _aggregate_by(events: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=by + ["count", "mak"])
    out = events.copy()
    if "date" in by:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.to_period("M").astype(str)
    value_cols = [col for col in ["count", "mak", "headcount_change", "mak_change", "Cost_Impact"] if col in out.columns]
    return (
        out.groupby(by, dropna=False)[value_cols]
        .sum()
        .reset_index()
        .sort_values(by)
        .reset_index(drop=True)
    )


def _assert_frame_equal_with_debug(
    direct: pd.DataFrame,
    compact: pd.DataFrame,
    *,
    scenario: HiringParityScenario,
    context: str,
) -> None:
    try:
        pd.testing.assert_frame_equal(compact, direct, check_dtype=False, check_exact=False, atol=1e-9, rtol=1e-9)
    except AssertionError as exc:
        direct_cmp = direct.astype(str).assign(_source="direct")
        compact_cmp = compact.astype(str).assign(_source="compact")
        raise AssertionError(
            f"{context} mismatch for scenario {scenario.name}\n"
            f"direct rows={len(direct)}, compact rows={len(compact)}\n"
            f"direct:\n{direct.to_string(index=False)}\n\n"
            f"compact:\n{compact.to_string(index=False)}\n\n"
            f"combined:\n{pd.concat([direct_cmp, compact_cmp], ignore_index=True).to_string(index=False)}"
        ) from exc


def _run_direct_and_compact(scenario: HiringParityScenario) -> tuple[pd.DataFrame, pd.DataFrame, object]:
    for key in [SESSION_KEY, "abgaenge_params", "zugaenge_params"]:
        st.session_state.pop(key, None)

    target_date = BASE_DATE + pd.DateOffset(months=scenario.horizon_months)
    snapshot_df = _snapshot_for_hiring_parity()
    df_atz = pd.DataFrame()
    abgaenge_params = _inactive_abgaenge_params()
    zugaenge_params = _zugaenge_params_for(scenario, snapshot_df)
    st.session_state[SESSION_KEY] = {
        "abgaenge": {**deepcopy(abgaenge_params), "_ui": {"ignored": True}},
        "zugaenge": {**deepcopy(zugaenge_params), "_ui": {"start_date": str(BASE_DATE.date())}},
    }

    compact_abgaenge_params, compact_zugaenge_params = get_compact_plus_params()

    assert compact_abgaenge_params == abgaenge_params
    assert compact_zugaenge_params == zugaenge_params
    assert "_ui" not in compact_abgaenge_params
    assert "_ui" not in compact_zugaenge_params

    page4_snapshot = _page4_like_snapshot(snapshot_df, df_atz, BASE_DATE)
    direct_params = deepcopy(compact_zugaenge_params)
    direct_result = run_forecast_zugaenge(
        df_snapshot=page4_snapshot,
        start_date=BASE_DATE,
        end_date=target_date,
        freq="M",
        params=direct_params,
        vacancies=[],
    )
    direct_events = direct_result["events"]
    if not direct_events.empty:
        direct_events = enrich_zugaenge_events(direct_events, page4_snapshot, direct_params)

    sim_result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
        base_date=BASE_DATE,
        abgaenge_params=deepcopy(compact_abgaenge_params),
        zugaenge_params=deepcopy(compact_zugaenge_params),
    )

    return _normalize_events(direct_events), _normalize_events(sim_result.zugaenge_result["events"]), sim_result


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_compact_plus_zugaenge_events_match_direct_forecast_from_simulation_params(
    scenario: HiringParityScenario,
) -> None:
    direct_events, compact_events, sim_result = _run_direct_and_compact(scenario)
    target_date = BASE_DATE + pd.DateOffset(months=scenario.horizon_months)

    assert sim_result.metadata["used_simulation"] is True
    assert pd.Timestamp(sim_result.metadata["base_date"]).normalize() == BASE_DATE
    assert pd.Timestamp(sim_result.metadata["target_date"]).normalize() == target_date
    assert sim_result.metadata["abgaenge_events"] == 0
    assert sim_result.metadata["zugaenge_events"] == len(direct_events)
    if "horizon_days" in sim_result.metadata:
        assert sim_result.metadata["horizon_days"] == (target_date - BASE_DATE).days

    abgaenge_events = sim_result.abgaenge_result.get("events_person_level", pd.DataFrame())
    assert len(abgaenge_events) == 0

    _assert_frame_equal_with_debug(direct_events, compact_events, scenario=scenario, context="event detail")
    for columns in [
        ["type"],
        ["date"],
        ["type", "date"],
        ["Jobfamily"],
        ["Organisationseinheit"],
        ["TrfGr"],
        ["source"],
    ]:
        if direct_events.empty or all(col in direct_events.columns for col in columns):
            _assert_frame_equal_with_debug(
                _aggregate_by(direct_events, columns),
                _aggregate_by(compact_events, columns),
                scenario=scenario,
                context=f"aggregate {columns}",
            )

    if scenario.expect_zero_events:
        assert len(direct_events) == 0
        assert len(compact_events) == 0


def test_compact_plus_zugaenge_parity_falls_back_to_defaults_when_simulation_params_missing() -> None:
    for key in [SESSION_KEY, "abgaenge_params", "zugaenge_params"]:
        st.session_state.pop(key, None)

    _, compact_zugaenge_params = get_compact_plus_params()

    assert compact_zugaenge_params == default_zugaenge_params()
    assert "_ui" not in compact_zugaenge_params
