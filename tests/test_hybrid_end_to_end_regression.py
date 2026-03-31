import copy
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st

import kpi_reference
from abgaenge.forecast import aggregate_forecast_results, run_forecast_abgaenge
from abgaenge.params import default_params as default_abgaenge_params
from components.sidebar import apply_event_filters_with_state, filter_dataframe_by_view_filters
from config.settings import DEFAULT_AZUBI_SALARIES, DEFAULT_COHORTS
from dataloader import loader
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as default_zugaenge_params


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


def _load_hybrid_page_module():
    page_path = next((ROOT / "pages").glob("*_Prognose_Hybrid.py"))
    spec = importlib.util.spec_from_file_location("hybrid_page_regression_test", page_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_changed_filters(df: pd.DataFrame) -> dict:
    changed = copy.deepcopy(DEFAULT_FILTERS)
    for key, column, limit in [
        ("selected_org_units", "Organisationseinheit", 2),
        ("selected_jobfamilies", "Jobfamily", 2),
        ("selected_oe_clusters", "OE-Cluster", 1),
        ("selected_jf_clusters", "JF-Cluster", 1),
    ]:
        if column in df.columns:
            values = [str(v) for v in df[column].dropna().unique() if str(v).strip()]
            if values:
                changed[key] = sorted(values)[:limit]
    return changed


def _reference_build_hybrid_vacancies_from_events(abg_events: pd.DataFrame, df_ma: pd.DataFrame) -> list[dict]:
    vacancies = []
    exits = abg_events[abg_events["headcount_change"] < 0]
    snap_lookup = df_ma.set_index("PersNr")

    for _, row in exits.iterrows():
        pid = str(row["persnr"]).strip().replace(".0", "")
        donor_jobfamily = "Angestellte"
        donor_oe_cluster = "Sonstiges"
        if pid in snap_lookup.index:
            donor_row = snap_lookup.loc[pid]
            if isinstance(donor_row, pd.DataFrame):
                donor_row = donor_row.iloc[0]
            donor_jobfamily = donor_row.get("Jobfamily", donor_jobfamily)
            donor_oe_cluster = donor_row.get("OE-Cluster", donor_oe_cluster)
        vacancies.append(
            {
                "date": row["event_date"],
                "org_unit": row.get("Organisationseinheit", "Unbekannt"),
                "planstelle": row.get("Planstelle", "Unbekannt"),
                "persnr": row["persnr"],
                "Jobfamily": donor_jobfamily,
                "OE-Cluster": donor_oe_cluster,
            }
        )

    return vacancies


def _reference_prepare_hybrid_view_state(
    hybrid,
    raw_abg_events: pd.DataFrame,
    raw_zug_events: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    df_ma: pd.DataFrame,
    active_filters: dict,
    *,
    ist_stichtag: pd.Timestamp,
    forecast_end_date: pd.Timestamp,
    freq_label: str,
) -> dict:
    filt_abg_events, n_abg_before, n_abg_after = apply_event_filters_with_state(
        raw_abg_events,
        snapshot_df,
        active_filters=active_filters,
        mode="attrition",
    )

    abg_cols = [
        "period_label", "period_start", "period_end", "event_date", "persnr",
        "reason_code", "reason_label", "headcount_change", "mak_change",
        "Organisationseinheit", "Jobfamily", "OE-Cluster",
    ]
    if any(c not in filt_abg_events.columns for c in ["persnr", "headcount_change", "Organisationseinheit"]):
        for col in abg_cols:
            if col not in filt_abg_events.columns:
                filt_abg_events[col] = pd.NaT if "date" in col or "_start" in col or "_end" in col else None

    if "event_date" in filt_abg_events.columns:
        filt_abg_events["event_date"] = pd.to_datetime(filt_abg_events["event_date"])
    filt_abg_events["source_view"] = "Abgang_Detail"

    if "OE-Cluster" not in filt_abg_events.columns or filt_abg_events["OE-Cluster"].isna().all():
        pm_lookup = df_ma.set_index("PersNr")
        pm_lookup.index = pm_lookup.index.astype(str).str.replace(r"\.0$", "", regex=True)
        filt_abg_events["_pid_clean"] = filt_abg_events["persnr"].astype(str).str.replace(r"\.0$", "", regex=True)
        cluster_map = pm_lookup["OE-Cluster"].to_dict() if "OE-Cluster" in pm_lookup.columns else {}
        jf_map = pm_lookup["Jobfamily"].to_dict() if "Jobfamily" in pm_lookup.columns else {}
        filt_abg_events["OE-Cluster"] = filt_abg_events["_pid_clean"].map(cluster_map).fillna("Sonstiges")
        if "Jobfamily" not in filt_abg_events.columns or filt_abg_events["Jobfamily"].isna().all():
            filt_abg_events["Jobfamily"] = filt_abg_events["_pid_clean"].map(jf_map).fillna("Unbekannt")
        filt_abg_events = filt_abg_events.drop(columns=["_pid_clean"], errors="ignore")

    df_snapshot_filtered = filter_dataframe_by_view_filters(df_ma, active_filters)

    if "org_unit" in raw_zug_events.columns and "Organisationseinheit" not in raw_zug_events.columns:
        raw_zug_events = raw_zug_events.rename(columns={"org_unit": "Organisationseinheit"})
    elif "org_unit" in raw_zug_events.columns and "Organisationseinheit" in raw_zug_events.columns:
        raw_zug_events = raw_zug_events.drop(columns=["org_unit"])
    filt_zug_events, n_zug_before, n_zug_after = apply_event_filters_with_state(
        raw_zug_events,
        snapshot_df,
        active_filters=active_filters,
        mode="accession",
    )

    if "date" in filt_zug_events.columns:
        filt_zug_events["date"] = pd.to_datetime(filt_zug_events["date"])
        filt_zug_events = filt_zug_events[
            (filt_zug_events["date"] >= pd.Timestamp(ist_stichtag))
            & (filt_zug_events["date"] <= pd.Timestamp(forecast_end_date))
        ].copy()

    zug_cols = ["date", "type", "count", "persnr", "Organisationseinheit", "source", "mak", "Jobfamily", "OE-Cluster", "TrfGr", "St", "Planstelle"]
    if any(c not in filt_zug_events.columns for c in ["count", "source", "mak"]):
        for col in zug_cols:
            if col not in filt_zug_events.columns:
                filt_zug_events[col] = pd.NaT if col == "date" else None

    if "date" in filt_zug_events.columns:
        filt_zug_events["date"] = pd.to_datetime(filt_zug_events["date"])
    filt_zug_events = filt_zug_events[filt_zug_events["count"] != 0].copy()
    filt_zug_events = filt_zug_events.loc[:, ~filt_zug_events.columns.duplicated()]

    filt_zug_events_std = filt_zug_events.copy()
    filt_zug_events_std["event_date"] = pd.to_datetime(filt_zug_events_std["date"])
    filt_zug_events_std["mak_change"] = filt_zug_events_std["mak"]
    filt_zug_events_std["headcount_change"] = filt_zug_events_std["count"]

    def _map_zug_reason(row):
        event_type = str(row.get("type", ""))
        if event_type == "Azubi_Hire":
            return "Azubi Neueinstellung (externer Zugang)"
        if event_type == "Azubi_Conversion_Out":
            return "Azubi-Abschluss (Statuswechsel: Ende Azubi)"
        if event_type == "Azubi_Conversion_In":
            return "Übernahme nach Ausbildungsabschluss (interne MAK-wirksame Stellenbesetzung)"
        if event_type == "Azubi_Exit":
            return "Azubi-Abschluss: Nichtübernahme (Abgang)"
        return "Zugang (" + str(row.get("source", "unbekannt")) + ")"

    filt_zug_events_std["reason_label"] = filt_zug_events_std.apply(_map_zug_reason, axis=1)
    filt_zug_events_std["source_view"] = "Zugang_Detail"

    combined_events = pd.concat([filt_abg_events, filt_zug_events_std], ignore_index=True)
    start_ts = pd.Timestamp(ist_stichtag)
    end_ts = pd.Timestamp(forecast_end_date)
    combined_events_in_scope = combined_events[
        (combined_events["event_date"] >= start_ts)
        & (combined_events["event_date"] <= end_ts)
    ].copy()

    if not combined_events_in_scope.empty:
        if "event_uid" not in combined_events_in_scope.columns:
            combined_events_in_scope["event_uid"] = (
                combined_events_in_scope["event_date"].apply(lambda value: str(pd.Timestamp(value).date()))
                + "|"
                + combined_events_in_scope["persnr"].astype(str)
                + "|"
                + combined_events_in_scope["reason_label"].astype(str)
                + "|"
                + combined_events_in_scope["headcount_change"].astype(str)
                + "|"
                + combined_events_in_scope["source_view"].astype(str)
            )

        combined_events_in_scope["is_headcount_exit_any"] = combined_events_in_scope["headcount_change"] < 0
        combined_events_in_scope["is_headcount_exit_detail"] = (
            (combined_events_in_scope["headcount_change"] < 0)
            & (combined_events_in_scope["source_view"] == "Abgang_Detail")
        )

        def _is_bank_exit(row):
            if row["headcount_change"] >= 0:
                return False
            reason_label = str(row["reason_label"])
            if "Statuswechsel" in reason_label or "Conversion_Out" in reason_label:
                return False
            if "Ruhend" in reason_label or "Ruhephase" in reason_label:
                return False
            return True

        combined_events_in_scope["is_headcount_exit_bank"] = combined_events_in_scope.apply(_is_bank_exit, axis=1)

        mask_hc0 = combined_events_in_scope["headcount_change"] == 0
        mask_atz_ar_fr = combined_events_in_scope["reason_label"].str.contains("AR → FR", na=False)
        bad_atz_mask = mask_hc0 & mask_atz_ar_fr & (combined_events_in_scope["mak_change"] == 0)
        if bad_atz_mask.any():
            combined_events_in_scope.loc[bad_atz_mask, "mak_change"] = -1.0

    for frame in [combined_events_in_scope, filt_zug_events]:
        if "OE-Cluster" in frame.columns:
            mask = frame["OE-Cluster"].isna() | (frame["OE-Cluster"] == "Unclustered")
            frame.loc[mask, "OE-Cluster"] = "Sonstiges"
        if "JF-Cluster" in frame.columns:
            mask = frame["JF-Cluster"].isna() | (frame["JF-Cluster"] == "Unclustered")
            frame.loc[mask, "JF-Cluster"] = "Sonstiges"

    if df_snapshot_filtered.empty:
        df_view_agg = pd.DataFrame(columns=["PersNr", "MAK_Calculated", "mak", "active"])
        net_kpis = pd.DataFrame()
        abg_view_kpis = pd.DataFrame()
        zug_view_kpis = pd.DataFrame()
    else:
        view_agg_dict = {
            "MAK_Calculated": "sum",
            "Organisationseinheit": "first",
            "Jobfamily": "first",
            "GebDatum": "first",
            "Eintritt": "first",
            "Austritt": "first",
            "Status kundenindividuell": "first",
            "Sollarbeitszeit": "sum",
        }
        for col in ["Geschlecht", "Planstelle", "OE-Cluster", "JF-Cluster", "TrfGr"]:
            if col in df_snapshot_filtered.columns:
                view_agg_dict[col] = "first"

        df_view_agg = df_snapshot_filtered.groupby("PersNr", as_index=False).agg(view_agg_dict)
        df_view_agg["mak"] = df_view_agg["MAK_Calculated"]
        df_view_agg["active"] = True

        agg_freq = "M" if freq_label == "Monat" else "Q"
        net_kpis = hybrid.calculate_kpi_from_events(
            df_start_stats=df_view_agg,
            events_df=combined_events_in_scope,
            start_date=pd.Timestamp(ist_stichtag),
            end_date=pd.Timestamp(forecast_end_date),
            freq=agg_freq,
        )
        abg_view_kpis = aggregate_forecast_results(
            df_initial=df_view_agg,
            events_df=filt_abg_events,
            start_date=pd.Timestamp(ist_stichtag),
            end_date=pd.Timestamp(forecast_end_date),
            freq=agg_freq,
            params=None,
        )
        zug_view_kpis = aggregate_forecast_results(
            df_initial=df_view_agg,
            events_df=filt_zug_events_std,
            start_date=pd.Timestamp(ist_stichtag),
            end_date=pd.Timestamp(forecast_end_date),
            freq=agg_freq,
            params=None,
        )

    return {
        "filt_abg_events": filt_abg_events,
        "filt_zug_events": filt_zug_events,
        "filt_zug_events_std": filt_zug_events_std,
        "combined_events_in_scope": combined_events_in_scope,
        "df_snapshot_filtered": df_snapshot_filtered,
        "df_view_agg": df_view_agg,
        "net_kpis": net_kpis,
        "abg_view_kpis": abg_view_kpis,
        "zug_view_kpis": zug_view_kpis,
        "n_abg_before": n_abg_before,
        "n_abg_after": n_abg_after,
        "n_zug_before": n_zug_before,
        "n_zug_after": n_zug_after,
    }


def _build_hybrid_scenario():
    _reset_state()
    hybrid = _load_hybrid_page_module()
    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    df_atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    df_ma = hybrid._prepare_hybrid_employee_snapshot(snapshot_df, df_atz, current_stichtag=FROZEN_STICHTAG)

    params_abg = default_abgaenge_params()
    params_abg["random_seed"] = 42
    abg_res = run_forecast_abgaenge(
        df_ma=df_ma,
        df_atz=df_atz,
        start_date=FROZEN_STICHTAG,
        end_date=FROZEN_END_DATE,
        freq="M",
        params=params_abg,
    )

    params_zug = default_zugaenge_params()
    params_zug["random_seed"] = 42
    params_zug["new_hires"]["strategy"] = "Fill Vacancies"
    params_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(df_ma)

    vacancies = hybrid._build_hybrid_vacancies_from_events(abg_res["events_person_level"], df_ma)
    zug_res = run_forecast_zugaenge(
        df_snapshot=df_ma,
        start_date=FROZEN_STICHTAG,
        end_date=FROZEN_END_DATE,
        freq="M",
        params=params_zug,
        vacancies=vacancies,
    )
    if not zug_res["events"].empty:
        zug_res["events"] = enrich_zugaenge_events(zug_res["events"], df_ma, params_zug)

    return {
        "hybrid": hybrid,
        "snapshot_df": snapshot_df,
        "df_atz": df_atz,
        "df_ma": df_ma,
        "abg_res": abg_res,
        "zug_res": zug_res,
    }


def test_hybrid_vacancy_builder_matches_reference():
    scenario = _build_hybrid_scenario()
    expected = _reference_build_hybrid_vacancies_from_events(
        scenario["abg_res"]["events_person_level"],
        scenario["df_ma"],
    )
    actual = scenario["hybrid"]._build_hybrid_vacancies_from_events(
        scenario["abg_res"]["events_person_level"],
        scenario["df_ma"],
    )

    pd.testing.assert_frame_equal(
        pd.DataFrame(actual),
        pd.DataFrame(expected),
        check_dtype=True,
        check_like=False,
    )


@pytest.mark.parametrize("active_filters", [DEFAULT_FILTERS])
def test_hybrid_view_state_matches_reference_without_filters(active_filters):
    scenario = _build_hybrid_scenario()
    expected = _reference_prepare_hybrid_view_state(
        scenario["hybrid"],
        scenario["abg_res"]["events_person_level"].copy(),
        scenario["zug_res"]["events"].copy(),
        scenario["snapshot_df"],
        scenario["df_ma"],
        active_filters,
        ist_stichtag=FROZEN_STICHTAG,
        forecast_end_date=FROZEN_END_DATE,
        freq_label="Monat",
    )
    actual = scenario["hybrid"]._prepare_hybrid_view_state(
        scenario["abg_res"]["events_person_level"].copy(),
        scenario["zug_res"]["events"].copy(),
        scenario["snapshot_df"],
        scenario["df_ma"],
        active_filters,
        ist_stichtag=FROZEN_STICHTAG,
        forecast_end_date=FROZEN_END_DATE,
        freq_label="Monat",
        base_abg_kpis=scenario["abg_res"]["forecast_kpis"],
    )

    for key in ["n_abg_before", "n_abg_after", "n_zug_before", "n_zug_after"]:
        assert actual[key] == expected[key]
    for key in [
        "filt_abg_events",
        "filt_zug_events",
        "filt_zug_events_std",
        "combined_events_in_scope",
        "df_snapshot_filtered",
        "df_view_agg",
        "net_kpis",
        "abg_view_kpis",
        "zug_view_kpis",
    ]:
        pd.testing.assert_frame_equal(actual[key], expected[key], check_dtype=True, check_like=False)


def test_hybrid_view_state_matches_reference_with_filters():
    scenario = _build_hybrid_scenario()
    active_filters = _sample_changed_filters(scenario["df_ma"])

    expected = _reference_prepare_hybrid_view_state(
        scenario["hybrid"],
        scenario["abg_res"]["events_person_level"].copy(),
        scenario["zug_res"]["events"].copy(),
        scenario["snapshot_df"],
        scenario["df_ma"],
        active_filters,
        ist_stichtag=FROZEN_STICHTAG,
        forecast_end_date=FROZEN_END_DATE,
        freq_label="Monat",
    )
    actual = scenario["hybrid"]._prepare_hybrid_view_state(
        scenario["abg_res"]["events_person_level"].copy(),
        scenario["zug_res"]["events"].copy(),
        scenario["snapshot_df"],
        scenario["df_ma"],
        active_filters,
        ist_stichtag=FROZEN_STICHTAG,
        forecast_end_date=FROZEN_END_DATE,
        freq_label="Monat",
        base_abg_kpis=scenario["abg_res"]["forecast_kpis"],
    )

    for key in ["n_abg_before", "n_abg_after", "n_zug_before", "n_zug_after"]:
        assert actual[key] == expected[key]
    for key in [
        "filt_abg_events",
        "filt_zug_events",
        "filt_zug_events_std",
        "combined_events_in_scope",
        "df_snapshot_filtered",
        "df_view_agg",
        "net_kpis",
        "abg_view_kpis",
        "zug_view_kpis",
    ]:
        pd.testing.assert_frame_equal(actual[key], expected[key], check_dtype=True, check_like=False)
