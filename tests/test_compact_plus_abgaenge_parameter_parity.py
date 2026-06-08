from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys

import pandas as pd
import pytest
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as default_abgaenge_params
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.loader import calculate_mak_vectorized
from utils.simulation_params import SESSION_KEY, get_compact_plus_params
from zugaenge.params import default_params as default_zugaenge_params


BASE_DATE = pd.Timestamp("2025-12-31")


@dataclass(frozen=True)
class AttritionParityScenario:
    name: str
    horizon_months: int
    components: dict[str, bool] | None = None
    retirement: dict | None = None
    quit: dict | None = None
    atz: dict | None = None
    ruhend: dict | None = None


SCENARIOS = [
    AttritionParityScenario("baseline_defaults_12m", 12),
    AttritionParityScenario(
        "retirement_only_12m",
        12,
        components={"atz": False, "retirement": True, "quit": False, "ruhend": False},
        retirement={"rent_rate_65": 1.0, "rent_rate_60_65": 0.0},
    ),
    AttritionParityScenario(
        "retirement_only_24m",
        24,
        components={"atz": False, "retirement": True, "quit": False, "ruhend": False},
        retirement={"rent_rate_65": 1.0, "rent_rate_60_65": 0.0},
    ),
    AttritionParityScenario(
        "retirement_only_36m",
        36,
        components={"atz": False, "retirement": True, "quit": False, "ruhend": False},
        retirement={"rent_rate_65": 1.0, "rent_rate_60_65": 0.0},
    ),
    AttritionParityScenario(
        "quit_only_base_rate_24m",
        24,
        components={"atz": False, "retirement": False, "quit": True, "ruhend": False},
        quit={"quit_rate_base": 1.0, "use_quit_matrix": False},
    ),
    AttritionParityScenario(
        "quit_only_matrix_24m",
        24,
        components={"atz": False, "retirement": False, "quit": True, "ruhend": False},
        quit={
            "quit_rate_base": 0.0,
            "use_quit_matrix": True,
            "quit_dimension": "JobFamily",
            "quit_matrix": {
                "alter_unter_30": {"Default": 1.0},
                "alter_30_45": {"Default": 1.0},
                "alter_45_55": {"Default": 1.0},
                "alter_55_plus": {"Default": 1.0},
            },
        },
    ),
    AttritionParityScenario(
        "atz_only_base_rate_36m",
        36,
        components={"atz": True, "retirement": False, "quit": False, "ruhend": False},
        atz={
            "new_atz_rate": 1.0,
            "use_atz_matrix": False,
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 0.5,
            "atz_duration_fr_years": 0.5,
        },
    ),
    AttritionParityScenario(
        "atz_only_matrix_36m",
        36,
        components={"atz": True, "retirement": False, "quit": False, "ruhend": False},
        atz={
            "new_atz_rate": 0.0,
            "use_atz_matrix": True,
            "atz_dimension": "JobFamily",
            "atz_matrix": {"Default": 1.0},
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 0.5,
            "atz_duration_fr_years": 0.5,
        },
    ),
    AttritionParityScenario(
        "all_components_disabled_36m",
        36,
        components={"atz": False, "retirement": False, "quit": False, "ruhend": False},
        retirement={"rent_rate_65": 0.0, "rent_rate_60_65": 0.0},
        quit={"quit_rate_base": 0.0, "use_quit_matrix": False},
        atz={"new_atz_rate": 0.0, "use_atz_matrix": False},
        ruhend={"ruhend_new_cases_per_year": 0, "ruhend_return_rate": 0.0},
    ),
]


def _employee(
    persnr: str,
    age: int,
    jobfamily: str,
    org_unit: str,
    cluster: str,
    *,
    mak: float = 1.0,
    status: str = "Aktives Beschäftigungsverhältnis",
) -> dict:
    return {
        "PersNr": persnr,
        "Personalnummer": persnr,
        "Is_Vacant": False,
        "GebDatum": BASE_DATE - pd.DateOffset(years=age),
        "Eintritt": BASE_DATE - pd.DateOffset(years=12),
        "Austritt": pd.NaT,
        "Status kundenindividuell": status,
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
        "Geschlecht": "w",
        "Text Gsch": "w",
        "Vertragsart": "Unbefristet",
        "MitarbGruppenbez.": "Beschäftigte",
        "Ausbildung": "Bachelor",
        "ATZ_Status": "Kein ATZ",
        "OE-Cluster": cluster,
        "JF-Cluster": jobfamily,
    }


def _snapshot_for_attrition_parity() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _employee("000001", 66, "Beratung", "OE Rente", "Markt"),
            _employee("000002", 44, "Service", "OE Service", "Betrieb"),
            _employee("000003", 56, "ATZ Kandidat", "OE ATZ", "Marktfolge", mak=0.8),
            _employee("000004", 58, "ATZ Kandidat", "OE ATZ", "Marktfolge", mak=0.6),
            _employee("000005", 35, "Betrieb", "OE Betrieb", "Betrieb", mak=0.9),
        ]
    )


def _abgaenge_params_for(scenario: AttritionParityScenario) -> dict:
    params = default_abgaenge_params()
    params["random_seed"] = 123
    if scenario.components is not None:
        params["components"] = deepcopy(scenario.components)
    if scenario.retirement:
        params["retirement"].update(deepcopy(scenario.retirement))
    if scenario.quit:
        params["quit"].update(deepcopy(scenario.quit))
    if scenario.atz:
        params["atz"].update(deepcopy(scenario.atz))
    if scenario.ruhend:
        params["ruhend"].update(deepcopy(scenario.ruhend))
    return params


def _inactive_zugaenge_params() -> dict:
    params = default_zugaenge_params()
    params["azubi"]["active"] = False
    params["azubi"]["new_cases_per_year"] = 0
    params["azubi"]["retention_rate"] = 0.0
    params["trainee"]["active"] = False
    params["trainee"]["new_cases_per_year"] = 0
    params["new_hires"]["active"] = False
    params["new_hires"]["count_per_year"] = 0
    params["random_seed"] = 123
    return params


def _page3_like_employee_base(snapshot_df: pd.DataFrame, df_atz: pd.DataFrame, stichtag: pd.Timestamp) -> pd.DataFrame:
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
    }
    for col in [
        "Geschlecht",
        "Planstelle",
        "Kürzel OrgEinheit",
        "ATZ_Status",
        "Jobfamily",
        "TrfGr",
        "St",
        "OE-Cluster",
        "JF-Cluster",
    ]:
        if col in df_ma.columns:
            agg_dict[col] = "first"

    df_employee_agg = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
    df_employee_agg["mak"] = df_employee_agg["MAK_Calculated"]
    df_employee_agg["Sollarbeitszeit"] = 39.0
    mask_zero = df_employee_agg["mak"] <= 0
    if mask_zero.any() and "BsGrd" in df_employee_agg.columns:
        potential_mak = df_employee_agg.loc[mask_zero, "BsGrd"] / 100.0
        df_employee_agg.loc[mask_zero, "mak"] = potential_mak.fillna(0.0)
        df_employee_agg.loc[mask_zero, "MAK_Calculated"] = df_employee_agg.loc[mask_zero, "mak"]
    df_employee_agg["BsGrd"] = df_employee_agg["mak"] * 100.0
    return df_employee_agg


def _normalize_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    out = events.copy()
    for col in ["event_date", "period_start", "period_end"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.normalize()
    for col in ["persnr", "reason_code", "reason_label", "period_label", "Jobfamily", "Organisationseinheit"]:
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str)
    keep = [
        col
        for col in [
            "period_label",
            "period_start",
            "period_end",
            "event_date",
            "persnr",
            "reason_code",
            "reason_label",
            "headcount_change",
            "mak_change",
            "Jobfamily",
            "Organisationseinheit",
        ]
        if col in out.columns
    ]
    out = out[keep].sort_values(keep).reset_index(drop=True)
    return out


def _aggregate_by(events: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=by + ["headcount_change", "mak_change"])
    out = events.copy()
    if "event_date" in by:
        out["event_date"] = pd.to_datetime(out["event_date"], errors="coerce").dt.to_period("M").astype(str)
    return (
        out.groupby(by, dropna=False)[["headcount_change", "mak_change"]]
        .sum()
        .reset_index()
        .sort_values(by)
        .reset_index(drop=True)
    )


def _frame_debug(left: pd.DataFrame, right: pd.DataFrame) -> str:
    left_cmp = left.astype(str).assign(_source="direct")
    right_cmp = right.astype(str).assign(_source="compact")
    delta = pd.concat([left_cmp, right_cmp], ignore_index=True)
    return delta.to_string(index=False)


def _assert_frame_equal_with_debug(
    direct: pd.DataFrame,
    compact: pd.DataFrame,
    *,
    scenario: AttritionParityScenario,
    context: str,
) -> None:
    try:
        pd.testing.assert_frame_equal(compact, direct, check_dtype=False, check_exact=True)
    except AssertionError as exc:
        raise AssertionError(
            f"{context} mismatch for scenario {scenario.name}\n"
            f"direct rows={len(direct)}, compact rows={len(compact)}\n"
            f"{_frame_debug(direct, compact)}"
        ) from exc


def _run_direct_and_compact(scenario: AttritionParityScenario) -> tuple[pd.DataFrame, pd.DataFrame, object]:
    for key in [SESSION_KEY, "abgaenge_params", "zugaenge_params"]:
        st.session_state.pop(key, None)

    target_date = BASE_DATE + pd.DateOffset(months=scenario.horizon_months)
    snapshot_df = _snapshot_for_attrition_parity()
    df_atz = pd.DataFrame()
    abgaenge_params = _abgaenge_params_for(scenario)
    zugaenge_params = _inactive_zugaenge_params()
    st.session_state[SESSION_KEY] = {
        "abgaenge": {**deepcopy(abgaenge_params), "_ui": {"freq": "M", "ignored": True}},
        "zugaenge": {**deepcopy(zugaenge_params), "_ui": {"start_date": str(BASE_DATE.date())}},
    }

    compact_abgaenge_params, compact_zugaenge_params = get_compact_plus_params()

    assert compact_abgaenge_params == abgaenge_params
    assert compact_zugaenge_params == zugaenge_params
    assert "_ui" not in compact_abgaenge_params
    assert "_ui" not in compact_zugaenge_params

    page3_like_employee_base = _page3_like_employee_base(snapshot_df, df_atz, BASE_DATE)
    direct_result = run_forecast_abgaenge(
        df_ma=page3_like_employee_base,
        df_atz=df_atz,
        start_date=BASE_DATE,
        end_date=target_date,
        freq="M",
        params=compact_abgaenge_params,
    )
    sim_result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
        base_date=BASE_DATE,
        abgaenge_params=compact_abgaenge_params,
        zugaenge_params=compact_zugaenge_params,
    )

    direct_events = _normalize_events(direct_result["events_person_level"])
    compact_events = _normalize_events(sim_result.abgaenge_result["events_person_level"])
    return direct_events, compact_events, sim_result


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_compact_plus_abgaenge_events_match_direct_forecast_from_simulation_params(
    scenario: AttritionParityScenario,
) -> None:
    direct_events, compact_events, sim_result = _run_direct_and_compact(scenario)
    target_date = BASE_DATE + pd.DateOffset(months=scenario.horizon_months)

    assert sim_result.metadata["used_simulation"] is True
    assert pd.Timestamp(sim_result.metadata["base_date"]).normalize() == BASE_DATE
    assert pd.Timestamp(sim_result.metadata["target_date"]).normalize() == target_date
    assert sim_result.metadata["abgaenge_events"] == len(direct_events)
    assert sim_result.metadata["zugaenge_events"] == 0
    if "horizon_days" in sim_result.metadata:
        assert sim_result.metadata["horizon_days"] == (target_date - BASE_DATE).days

    zugaenge_events = sim_result.zugaenge_result.get("events", pd.DataFrame())
    assert len(zugaenge_events) == 0

    _assert_frame_equal_with_debug(direct_events, compact_events, scenario=scenario, context="event detail")
    for columns in [
        ["reason_code"],
        ["event_date"],
        ["reason_code", "event_date"],
        ["Jobfamily"],
        ["Organisationseinheit"],
    ]:
        _assert_frame_equal_with_debug(
            _aggregate_by(direct_events, columns),
            _aggregate_by(compact_events, columns),
            scenario=scenario,
            context=f"aggregate {columns}",
        )

    if scenario.name == "all_components_disabled_36m":
        assert len(direct_events) == 0
        assert len(compact_events) == 0


def test_compact_plus_abgaenge_parity_falls_back_to_defaults_when_simulation_params_missing() -> None:
    for key in [SESSION_KEY, "abgaenge_params", "zugaenge_params"]:
        st.session_state.pop(key, None)

    compact_abgaenge_params, compact_zugaenge_params = get_compact_plus_params()

    assert compact_abgaenge_params == default_abgaenge_params()
    assert compact_zugaenge_params == default_zugaenge_params()
    assert "_ui" not in compact_abgaenge_params
    assert "_ui" not in compact_zugaenge_params
