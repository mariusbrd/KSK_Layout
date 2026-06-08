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

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as default_abgaenge_params
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state,
    _build_vacancies_from_attrition,
    _prepare_employee_forecast_base,
    simulate_compact_snapshot,
)
from utils.simulation_params import SESSION_KEY, get_compact_plus_params
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as default_zugaenge_params


BASE_DATE = pd.Timestamp("2025-12-31")
ABGAENGE_COMPARE_COLUMNS = [
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
ZUGAENGE_COMPARE_COLUMNS = [
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
class CombinedParityScenario:
    name: str
    horizon_months: int
    abgaenge_components: dict[str, bool] | None = None
    retirement: dict[str, Any] | None = None
    quit: dict[str, Any] | None = None
    atz: dict[str, Any] | None = None
    ruhend: dict[str, Any] | None = None
    azubi: dict[str, Any] | None = None
    trainee: dict[str, Any] | None = None
    new_hires: dict[str, Any] | None = None
    expect_zero_events: bool = False
    expect_vacancies: bool = False


SCENARIOS = [
    CombinedParityScenario("baseline_combined_defaults_12m", 12),
    CombinedParityScenario(
        "retirement_fill_vacancies_12m",
        12,
        abgaenge_components={"atz": False, "retirement": True, "quit": False, "ruhend": False},
        retirement={"rent_rate_65": 1.0, "rent_rate_60_65": 0.0},
        azubi={"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={"active": True, "count_per_year": 12, "strategy": "Fill Vacancies"},
        expect_vacancies=True,
    ),
    CombinedParityScenario(
        "quit_new_hires_24m",
        24,
        abgaenge_components={"atz": False, "retirement": False, "quit": True, "ruhend": False},
        quit={"quit_rate_base": 1.0, "use_quit_matrix": False},
        azubi={"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={"active": True, "count_per_year": 12, "strategy": "Random"},
        expect_vacancies=True,
    ),
    CombinedParityScenario(
        "atz_azubi_trainee_36m",
        36,
        abgaenge_components={"atz": True, "retirement": False, "quit": False, "ruhend": False},
        atz={
            "new_atz_rate": 1.0,
            "use_atz_matrix": False,
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 0.5,
            "atz_duration_fr_years": 0.5,
        },
        azubi={
            "active": True,
            "new_cases_per_year": 12,
            "retention_rate": 1.0,
            "duration_years": 1.0,
            "graduation_mode": "nearest_cycle",
            "use_takeover_matrix": False,
        },
        trainee={"active": True, "new_cases_per_year": 6, "strategy": "Random"},
        new_hires={"active": False, "count_per_year": 0},
        expect_vacancies=True,
    ),
    CombinedParityScenario(
        "quit_matrix_azubi_matrix_24m",
        24,
        abgaenge_components={"atz": False, "retirement": False, "quit": True, "ruhend": False},
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
        azubi={
            "active": True,
            "new_cases_per_year": 12,
            "retention_rate": 1.0,
            "duration_years": 1.0,
            "graduation_mode": "nearest_cycle",
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {"Beratung": 1.0, "Service": 0.0, "Betrieb": 0.0, "ATZ Kandidat": 0.0},
        },
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
        expect_vacancies=True,
    ),
    CombinedParityScenario(
        "all_components_disabled_36m",
        36,
        abgaenge_components={"atz": False, "retirement": False, "quit": False, "ruhend": False},
        retirement={"rent_rate_65": 0.0, "rent_rate_60_65": 0.0},
        quit={"quit_rate_base": 0.0, "use_quit_matrix": False},
        atz={"new_atz_rate": 0.0, "use_atz_matrix": False},
        ruhend={"ruhend_new_cases_per_year": 0, "ruhend_return_rate": 0.0},
        azubi={"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        trainee={"active": False, "new_cases_per_year": 0},
        new_hires={"active": False, "count_per_year": 0},
        expect_zero_events=True,
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
        "Eintritt": BASE_DATE - pd.DateOffset(years=10),
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


def _snapshot_for_combined_parity() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _employee("000001", 66, "Beratung", "OE Beratung", "Markt", "Beratung", geschlecht="w"),
            _employee("000002", 44, "Service", "OE Service", "Betrieb", "Service", mak=0.8, geschlecht="m"),
            _employee("000003", 56, "ATZ Kandidat", "OE ATZ", "Marktfolge", "ATZ Kandidat", mak=0.8),
            _employee("000004", 58, "ATZ Kandidat", "OE ATZ", "Marktfolge", "ATZ Kandidat", mak=0.6),
            _employee("000005", 35, "Betrieb", "OE Betrieb", "Betrieb", "Betrieb", mak=0.9),
        ]
    )


def _abgaenge_params_for(scenario: CombinedParityScenario) -> dict[str, Any]:
    params = default_abgaenge_params()
    params["random_seed"] = 123
    if scenario.abgaenge_components is not None:
        params["components"] = deepcopy(scenario.abgaenge_components)
    if scenario.retirement:
        params["retirement"].update(deepcopy(scenario.retirement))
    if scenario.quit:
        params["quit"].update(deepcopy(scenario.quit))
    if scenario.atz:
        params["atz"].update(deepcopy(scenario.atz))
    if scenario.ruhend:
        params["ruhend"].update(deepcopy(scenario.ruhend))
    return params


def _zugaenge_params_for(scenario: CombinedParityScenario, snapshot_df: pd.DataFrame) -> dict[str, Any]:
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


def _strip_and_normalize_dates(events: pd.DataFrame, date_cols: list[str]) -> pd.DataFrame:
    out = events.copy()
    for col in date_cols:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.normalize()
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].fillna("").astype(str)
    return out


def _normalize_abgaenge_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=[col for col in ABGAENGE_COMPARE_COLUMNS if col in events.columns])
    out = _strip_and_normalize_dates(events, ["event_date", "period_start", "period_end"])
    keep = [col for col in ABGAENGE_COMPARE_COLUMNS if col in out.columns]
    return out[keep].sort_values(keep).reset_index(drop=True)


def _normalize_zugaenge_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=[col for col in ZUGAENGE_COMPARE_COLUMNS if col in events.columns])
    out = _strip_and_normalize_dates(events, ["date", "entry_date", "graduation_date"])
    keep = [col for col in ZUGAENGE_COMPARE_COLUMNS if col in out.columns]
    return out[keep].sort_values(keep).reset_index(drop=True)


def _normalize_vacancies(vacancies: list[dict[str, Any]]) -> pd.DataFrame:
    if not vacancies:
        return pd.DataFrame(columns=["date", "org_unit", "planstelle", "persnr", "Jobfamily", "OE-Cluster"])
    out = pd.DataFrame(vacancies)
    out = _strip_and_normalize_dates(out, ["date"])
    keep = [col for col in ["date", "org_unit", "planstelle", "persnr", "Jobfamily", "OE-Cluster"] if col in out.columns]
    return out[keep].sort_values(keep).reset_index(drop=True)


def _aggregate_by(events: pd.DataFrame, by: list[str], date_col: str) -> pd.DataFrame:
    if events.empty:
        value_cols = [col for col in ["count", "mak", "headcount_change", "mak_change"] if col in events.columns]
        return pd.DataFrame(columns=by + value_cols)
    out = events.copy()
    if date_col in by:
        out[date_col] = pd.to_datetime(out[date_col], errors="coerce").dt.to_period("M").astype(str)
    value_cols = [col for col in ["count", "mak", "headcount_change", "mak_change"] if col in out.columns]
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
    scenario: CombinedParityScenario,
    context: str,
) -> None:
    try:
        pd.testing.assert_frame_equal(compact, direct, check_dtype=False, check_exact=False, atol=1e-9, rtol=1e-9)
    except AssertionError as exc:
        raise AssertionError(
            f"{context} mismatch for scenario {scenario.name} ({scenario.horizon_months} months)\n"
            f"direct rows={len(direct)}, compact rows={len(compact)}\n"
            f"direct:\n{direct.to_string(index=False)}\n\n"
            f"compact:\n{compact.to_string(index=False)}"
        ) from exc


def _assert_engine_ready_matrices(value: Any, path: str = "params") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key.endswith("matrix") and isinstance(item, dict):
                _assert_matrix_values_in_unit_interval(item, child_path)
            _assert_engine_ready_matrices(item, child_path)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _assert_engine_ready_matrices(item, f"{path}[{idx}]")


def _assert_matrix_values_in_unit_interval(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_matrix_values_in_unit_interval(item, f"{path}.{key}")
    else:
        numeric = float(value)
        assert 0.0 <= numeric <= 1.0, f"{path} contains non-engine-ready matrix value {numeric}"


def _run_direct_reference(
    snapshot_df: pd.DataFrame,
    df_atz: pd.DataFrame,
    target_date: pd.Timestamp,
    abgaenge_params: dict[str, Any],
    zugaenge_params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    employee_base = _prepare_employee_forecast_base(snapshot_df, df_atz, BASE_DATE)
    abgaenge_result = run_forecast_abgaenge(
        df_ma=employee_base,
        df_atz=df_atz,
        start_date=BASE_DATE,
        end_date=target_date,
        freq="M",
        params=deepcopy(abgaenge_params),
    )
    employee_after_abgaenge = _apply_attrition_events_to_employee_state(
        employee_base,
        abgaenge_result.get("events_person_level", pd.DataFrame()),
    )
    vacancies = _build_vacancies_from_attrition(employee_base, abgaenge_result)

    run_params_zug = deepcopy(zugaenge_params)
    run_params_zug.setdefault("azubi", {})
    run_params_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(snapshot_df)
    zugaenge_result = run_forecast_zugaenge(
        df_snapshot=employee_after_abgaenge,
        start_date=BASE_DATE,
        end_date=target_date,
        freq="M",
        params=run_params_zug,
        vacancies=deepcopy(vacancies),
    )
    if not zugaenge_result.get("events", pd.DataFrame()).empty:
        zugaenge_result["events"] = enrich_zugaenge_events(
            zugaenge_result["events"],
            snapshot_df,
            run_params_zug,
        )

    return abgaenge_result, zugaenge_result, vacancies


def _run_compact_and_direct(scenario: CombinedParityScenario):
    for key in [SESSION_KEY, "abgaenge_params", "zugaenge_params"]:
        st.session_state.pop(key, None)

    target_date = BASE_DATE + pd.DateOffset(months=scenario.horizon_months)
    snapshot_df = _snapshot_for_combined_parity()
    df_atz = pd.DataFrame()
    abgaenge_params = _abgaenge_params_for(scenario)
    zugaenge_params = _zugaenge_params_for(scenario, snapshot_df)
    st.session_state[SESSION_KEY] = {
        "abgaenge": {**deepcopy(abgaenge_params), "_ui": {"freq": "M", "ignored": True}},
        "zugaenge": {**deepcopy(zugaenge_params), "_ui": {"start_date": str(BASE_DATE.date()), "ignored": True}},
    }

    compact_abgaenge_params, compact_zugaenge_params = get_compact_plus_params()
    assert compact_abgaenge_params == abgaenge_params
    assert compact_zugaenge_params == zugaenge_params
    assert "_ui" not in compact_abgaenge_params
    assert "_ui" not in compact_zugaenge_params
    _assert_engine_ready_matrices(compact_abgaenge_params, "abgaenge")
    _assert_engine_ready_matrices(compact_zugaenge_params, "zugaenge")

    direct_abgaenge, direct_zugaenge, direct_vacancies = _run_direct_reference(
        snapshot_df,
        df_atz,
        target_date,
        compact_abgaenge_params,
        compact_zugaenge_params,
    )
    compact_result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
        base_date=BASE_DATE,
        abgaenge_params=deepcopy(compact_abgaenge_params),
        zugaenge_params=deepcopy(compact_zugaenge_params),
    )
    compact_employee_base = _prepare_employee_forecast_base(snapshot_df, df_atz, BASE_DATE)
    compact_vacancies = _build_vacancies_from_attrition(compact_employee_base, compact_result.abgaenge_result)

    return direct_abgaenge, direct_zugaenge, direct_vacancies, compact_result, compact_vacancies


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_compact_plus_combined_abgaenge_zugaenge_match_direct_reference_from_simulation_params(
    scenario: CombinedParityScenario,
) -> None:
    direct_abgaenge, direct_zugaenge, direct_vacancies, compact_result, compact_vacancies = _run_compact_and_direct(scenario)
    target_date = BASE_DATE + pd.DateOffset(months=scenario.horizon_months)

    direct_abg_events = _normalize_abgaenge_events(direct_abgaenge["events_person_level"])
    compact_abg_events = _normalize_abgaenge_events(compact_result.abgaenge_result["events_person_level"])
    direct_zug_events = _normalize_zugaenge_events(direct_zugaenge["events"])
    compact_zug_events = _normalize_zugaenge_events(compact_result.zugaenge_result["events"])

    assert compact_result.metadata["used_simulation"] is True
    assert pd.Timestamp(compact_result.metadata["base_date"]).normalize() == BASE_DATE
    assert pd.Timestamp(compact_result.metadata["target_date"]).normalize() == target_date
    assert compact_result.metadata["abgaenge_events"] == len(direct_abg_events)
    assert compact_result.metadata["zugaenge_events"] == len(direct_zug_events)
    if "horizon_days" in compact_result.metadata:
        assert compact_result.metadata["horizon_days"] == (target_date - BASE_DATE).days

    _assert_frame_equal_with_debug(direct_abg_events, compact_abg_events, scenario=scenario, context="abgaenge event detail")
    _assert_frame_equal_with_debug(direct_zug_events, compact_zug_events, scenario=scenario, context="zugaenge event detail")
    _assert_frame_equal_with_debug(
        _normalize_vacancies(direct_vacancies),
        _normalize_vacancies(compact_vacancies),
        scenario=scenario,
        context="vacancies",
    )

    for columns in [["reason_code"], ["event_date"], ["reason_code", "event_date"], ["Jobfamily"], ["Organisationseinheit"]]:
        if direct_abg_events.empty or all(col in direct_abg_events.columns for col in columns):
            _assert_frame_equal_with_debug(
                _aggregate_by(direct_abg_events, columns, "event_date"),
                _aggregate_by(compact_abg_events, columns, "event_date"),
                scenario=scenario,
                context=f"abgaenge aggregate {columns}",
            )

    for columns in [["type"], ["date"], ["type", "date"], ["Jobfamily"], ["Organisationseinheit"], ["TrfGr"], ["source"]]:
        if direct_zug_events.empty or all(col in direct_zug_events.columns for col in columns):
            _assert_frame_equal_with_debug(
                _aggregate_by(direct_zug_events, columns, "date"),
                _aggregate_by(compact_zug_events, columns, "date"),
                scenario=scenario,
                context=f"zugaenge aggregate {columns}",
            )

    if scenario.expect_zero_events:
        assert len(direct_abg_events) == 0
        assert len(compact_abg_events) == 0
        assert len(direct_zug_events) == 0
        assert len(compact_zug_events) == 0
        assert len(direct_vacancies) == 0
        assert len(compact_vacancies) == 0
    if scenario.expect_vacancies:
        assert len(direct_vacancies) > 0
        assert len(compact_vacancies) > 0
