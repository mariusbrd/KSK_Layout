from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dataloader.compact_simulation_engine import simulate_compact_snapshot
from utils.compact_page_loader import load_compact_page_module


BASE_DATE = pd.Timestamp("2025-12-31")
TARGET_DATE = pd.Timestamp("2026-01-31")


@pytest.fixture(autouse=True)
def _clear_streamlit_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


def _load_page_module(filename_suffix: str, module_name: str):
    page_path = next((ROOT / "pages").glob(f"*_{filename_suffix}.py"))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot_row(
    persnr: str,
    *,
    org: str,
    age_years: int,
    mak: float,
    tariff: str,
) -> dict:
    return {
        "PersNr": persnr,
        "Personalnummer": persnr,
        "Is_Vacant": False,
        "GebDatum": BASE_DATE - pd.DateOffset(years=age_years),
        "Eintritt": pd.Timestamp("2010-01-01"),
        "Austritt": pd.NaT,
        "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
        "Sollarbeitszeit": 39.0,
        "Soll_FTE": mak,
        "FTE_person": mak,
        "FTE_assigned": mak,
        "BsGrd": mak * 100.0,
        "MAK_Calculated": mak,
        "MAK_Reporting": mak,
        "MAK": mak,
        "mak": mak,
        "EUR_Reporting": 50_000.0 * mak,
        "Total_Cost_Year": 50_000.0 * mak,
        "Soll_Cost_Year": 55_000.0 * mak,
        "Organisationseinheit": org,
        "Kürzel OrgEinheit": org,
        "OE-Cluster": f"{org}-Cluster",
        "JF-Cluster": "Kundenberatung-Cluster",
        "Planstelle": "Kundenberater",
        "Jobfamily": "Kundenberatung",
        "TrfGr": tariff,
        "St": 3,
        "Geschlecht": "w",
        "Text Gsch": "weiblich",
        "Vertragsart": "Unbefristet",
        "MitarbGruppenbez.": "Beschäftigte",
        "Ausbildung": "Bankberufsabschluss",
        "ATZ_Status": "Kein ATZ",
    }


def _base_snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _snapshot_row(
                "000001",
                org="Beratung",
                age_years=66,
                mak=1.0,
                tariff="E11",
            ),
            _snapshot_row(
                "000002",
                org="Marktfolge",
                age_years=45,
                mak=0.8,
                tariff="E9A",
            ),
        ]
    )


def _ranking_snapshot() -> pd.DataFrame:
    rows = []
    for name, count in [("Gross", 12), ("Mittel", 8), ("Klein", 3)]:
        for idx in range(count):
            rows.append(
                {
                    "PersNr": f"{name}-{idx}",
                    "Personalnummer": f"{name}-{idx}",
                    "Is_Vacant": False,
                    "Organisationseinheit": name,
                    "Jobfamily": name,
                    "Geschlecht": "w",
                    "MAK_Reporting": 1.0,
                    "EUR_Reporting": 100.0,
                    "Headcount": 1,
                }
            )
    return pd.DataFrame(rows)


def _ranking_snapshot_with_diverging_headcount_and_mak() -> pd.DataFrame:
    rows = []
    specs = [
        ("Viele_Teilzeit", 20, 0.5),
        ("Weniger_Vollzeit", 12, 1.0),
        ("Klein", 8, 1.0),
    ]
    for name, count, mak in specs:
        for idx in range(count):
            rows.append(
                {
                    "PersNr": f"{name}-{idx}",
                    "Personalnummer": f"{name}-{idx}",
                    "Is_Vacant": False,
                    "Organisationseinheit": name,
                    "Jobfamily": name,
                    "Geschlecht": "w",
                    "MAK_Reporting": mak,
                    "EUR_Reporting": 100.0 * mak,
                    "Headcount": 1,
                }
            )
    return pd.DataFrame(rows)


class _NoopContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ChartCaptureStreamlit:
    def __init__(self):
        self.figures = []

    def columns(self, spec):
        return [_NoopContext() for _ in spec]

    def plotly_chart(self, fig, **_kwargs):
        self.figures.append(fig)

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


def _abgaenge_retire_65plus_params() -> dict:
    return {
        "components": {
            "atz": False,
            "retirement": True,
            "quit": False,
            "ruhend": False,
        },
        "retirement": {
            "rent_rate_65": 1.0,
            "rent_rate_60_65": 0.0,
        },
        "random_seed": 42,
    }


def _inactive_zugaenge_params() -> dict:
    return {
        "azubi": {"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        "trainee": {"active": False, "new_cases_per_year": 0},
        "new_hires": {"active": False, "count_per_year": 0},
        "random_seed": 42,
    }


def _run_reusable_orgunit_simulation() -> tuple[pd.DataFrame, object]:
    st.session_state["tvoed_lookup"] = {}

    result = simulate_compact_snapshot(
        snapshot_df=_base_snapshot(),
        df_atz=pd.DataFrame(),
        target_date=TARGET_DATE,
        base_date=BASE_DATE,
        abgaenge_params=_abgaenge_retire_65plus_params(),
        zugaenge_params=_inactive_zugaenge_params(),
    )

    compact = load_compact_page_module()
    prepared_df = compact.prepare_compact_data(result.future_snapshot_df)
    status_quo_df = compact.prepare_compact_data(_base_snapshot())
    st.session_state["compact_sim_prepared_df"] = prepared_df
    st.session_state["compact_sim_status_quo_df"] = status_quo_df
    st.session_state["compact_sim_metadata"] = result.metadata
    st.session_state["compact_sim_audit_tables"] = result.audit_tables
    st.session_state["compact_sim_target_date_cached"] = TARGET_DATE
    return prepared_df, result


def test_orgunit_simulation_page_uses_computed_future_snapshot_and_preserves_values(monkeypatch):
    prepared_df, result = _run_reusable_orgunit_simulation()
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis")
    sim_page = _load_page_module("Organisationseinheiten_Simulation", "orgunit_simulation")

    assert result.metadata["used_simulation"] is True
    assert result.metadata["abgaenge_events"] == 1
    assert result.metadata["zugaenge_events"] == 0

    active_future = prepared_df[prepared_df["Is_Vacant"] != True].copy()
    assert set(active_future["PersNr"].astype(str)) == {"000002"}
    assert active_future["Organisationseinheit"].tolist() == ["Marktfolge"]

    metric_config = org_page._get_metric_config(prepared_df, "Köpfe")
    mapped_df = org_page._normalize_org_column(prepared_df)
    display_orgs = org_page._get_visible_org_units_for_display(mapped_df, "Alle")
    assert display_orgs == ["Marktfolge"]
    assert org_page._get_metric_total(mapped_df, "Köpfe", load_compact_page_module()) == 1
    assert org_page._get_metric_total(mapped_df, "MAK", load_compact_page_module()) == pytest.approx(0.8)

    captured = {}

    def fake_render_orgunit_analysis_page(df, history_df, **kwargs):
        captured["df"] = df.copy()
        captured["history_df"] = history_df
        captured["kwargs"] = kwargs

    fake_org_module = type(
        "FakeOrgModule",
        (),
        {"render_orgunit_analysis_page": staticmethod(fake_render_orgunit_analysis_page)},
    )
    monkeypatch.setattr(sim_page, "_load_orgunit_analysis_module", lambda: fake_org_module)

    sim_page.main()

    assert captured["history_df"] is None
    assert captured["kwargs"]["value_label"] == "Simulation"
    assert captured["kwargs"]["comparison_label"] == "IST"
    assert captured["kwargs"]["enable_comparison_toggle"] is True
    assert captured["kwargs"]["comparison_df"] is st.session_state["compact_sim_status_quo_df"]
    assert not captured["kwargs"]["departure_events_df"].empty
    assert "31.01.2026" in captured["kwargs"]["subtitle"]
    pd.testing.assert_frame_equal(
        captured["df"].reset_index(drop=True),
        prepared_df.reset_index(drop=True),
        check_dtype=False,
    )
    assert metric_config == {"value_col": "Headcount", "value_type": "koepfe"}


def test_orgunit_simulation_departure_toggle_aggregates_company_leavers_by_org_unit():
    _prepared_df, result = _run_reusable_orgunit_simulation()
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_departures")
    sim_page = _load_page_module("Organisationseinheiten_Simulation", "orgunit_simulation_departures")

    departures = sim_page._get_departure_events()
    assert len(departures) == 1
    assert departures["persnr"].tolist() == ["000001"]
    assert departures["Organisationseinheit"].tolist() == ["Beratung"]
    assert departures["Abgänge"].sum() == 1

    summary = org_page._build_departure_org_summary(departures)
    assert summary.to_dict("records") == [
        {
            "Organisationseinheit": "Beratung",
            "Abgänge": 1,
            "Personen": 1,
            "MAK-Verlust": 1.0,
        }
    ]

    reason_summary = org_page._build_departure_reason_summary(departures)
    assert reason_summary["Organisationseinheit"].tolist() == ["Beratung"]
    assert reason_summary["Abgänge"].tolist() == [1]
    assert reason_summary["Personen"].tolist() == [1]
    assert reason_summary["MAK-Verlust"].tolist() == [1.0]
    assert result.metadata["abgaenge_events"] == 1


def test_orgunit_comparison_summary_combines_ist_simulation_delta_and_departures():
    prepared_df, _result = _run_reusable_orgunit_simulation()
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_comparison")
    sim_page = _load_page_module("Organisationseinheiten_Simulation", "orgunit_simulation_comparison")

    metric_config = org_page._get_metric_config(prepared_df, "Köpfe")
    current = org_page._normalize_org_column(prepared_df)
    comparison = org_page._normalize_org_column(st.session_state["compact_sim_status_quo_df"])
    departures = sim_page._get_departure_events()

    table = org_page._build_org_metric_comparison(
        current[current["Organisationseinheit"] != "Nicht zugeordnet"],
        comparison[comparison["Organisationseinheit"] != "Nicht zugeordnet"],
        "Köpfe",
        metric_config,
        ["Marktfolge", "Beratung"],
        value_label="Simulation",
        comparison_label="IST",
        departure_events=departures,
    )

    by_org = table.set_index("Organisationseinheit")
    assert by_org.loc["Marktfolge", "IST"] == 1
    assert by_org.loc["Marktfolge", "Simulation"] == 1
    assert by_org.loc["Marktfolge", "Delta"] == 0
    assert by_org.loc["Beratung", "IST"] == 1
    assert by_org.loc["Beratung", "Simulation"] == 0
    assert by_org.loc["Beratung", "Delta"] == -1
    assert by_org.loc["Beratung", "Abgänge"] == 1
    assert by_org.loc["Beratung", "MAK-Verlust"] == 1.0


def test_orgunit_top_filters_reuse_ranking_for_min_size_sort_and_simulation_focus():
    prepared_df, _result = _run_reusable_orgunit_simulation()
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_top_filters")
    sim_page = _load_page_module("Organisationseinheiten_Simulation", "orgunit_simulation_top_filters")

    current = org_page._normalize_org_column(prepared_df)
    comparison = org_page._normalize_org_column(st.session_state["compact_sim_status_quo_df"])
    departures = sim_page._get_departure_events()
    metric_config = org_page._get_metric_config(current, "Köpfe")

    ranking = org_page._build_orgunit_ranking_frame(
        current[current["Organisationseinheit"] != "Nicht zugeordnet"],
        comparison[comparison["Organisationseinheit"] != "Nicht zugeordnet"],
        departures,
        "Köpfe",
        metric_config,
        comparison_active=True,
        value_label="Simulation",
        comparison_label="IST",
    )

    assert org_page._apply_orgunit_top_filters(
        ranking,
        "Alle",
        "Delta",
        "Köpfe",
        "Alle",
        "Nur mit Veränderung",
        comparison_active=True,
    ) == ["Beratung"]
    assert org_page._apply_orgunit_top_filters(
        ranking,
        "Alle",
        "Abgänge",
        "Köpfe",
        "Alle",
        "Nur mit Abgängen",
        comparison_active=True,
    ) == ["Beratung"]
    assert org_page._apply_orgunit_top_filters(
        ranking,
        "Alle",
        "Mitarbeiterzahl",
        "Köpfe",
        "mind. 1,0 MAK",
        "Alle",
        comparison_active=True,
    ) == ["Beratung"]


def test_jobfamily_simulation_page_uses_computed_future_snapshot(monkeypatch):
    prepared_df, _result = _run_reusable_orgunit_simulation()
    sim_page = _load_page_module("Jobfamily_Simulation", "jobfamily_simulation")

    captured = {}

    def fake_render_jobfamily_analysis_page(df, history_df, **kwargs):
        captured["df"] = df.copy()
        captured["history_df"] = history_df
        captured["kwargs"] = kwargs

    fake_jobfamily_module = type(
        "FakeJobfamilyModule",
        (),
        {"render_jobfamily_analysis_page": staticmethod(fake_render_jobfamily_analysis_page)},
    )
    monkeypatch.setattr(sim_page, "_load_jobfamily_analysis_module", lambda: fake_jobfamily_module)

    sim_page.main()

    assert captured["history_df"] is None
    assert captured["kwargs"]["value_label"] == "Simulation"
    assert captured["kwargs"]["key_prefix"] == "jobfamily_simulation"
    assert "31.01.2026" in captured["kwargs"]["subtitle"]
    pd.testing.assert_frame_equal(
        captured["df"].reset_index(drop=True),
        prepared_df.reset_index(drop=True),
        check_dtype=False,
    )


def test_orgunit_top_n_selects_largest_values_not_smallest():
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_top_n_order")
    df = _ranking_snapshot()
    metric_config = org_page._get_metric_config(df, "MAK")

    ranking = org_page._build_orgunit_ranking_frame(
        df,
        comparison_mapped_df=None,
        departure_events=None,
        metric_view="MAK",
        metric_config=metric_config,
        comparison_active=False,
        value_label="IST",
        comparison_label="IST",
    )

    assert org_page._apply_orgunit_top_filters(
        ranking,
        "2",
        "Mitarbeiterzahl",
        "MAK",
        "Alle",
        "Alle",
        comparison_active=False,
    ) == ["Gross", "Mittel"]


def test_orgunit_current_metric_sort_uses_visible_mak_not_headcount():
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_current_metric_sort")
    df = _ranking_snapshot_with_diverging_headcount_and_mak()
    metric_config = org_page._get_metric_config(df, "MAK")

    ranking = org_page._build_orgunit_ranking_frame(
        df,
        comparison_mapped_df=None,
        departure_events=None,
        metric_view="MAK",
        metric_config=metric_config,
        comparison_active=False,
        value_label="Simulation",
        comparison_label="IST",
    )

    assert org_page._apply_orgunit_top_filters(
        ranking,
        "2",
        "Aktuelle Kennzahl",
        "MAK",
        "Alle",
        "Alle",
        comparison_active=False,
    ) == ["Weniger_Vollzeit", "Viele_Teilzeit"]


def test_orgunit_sort_options_do_not_offer_eur_and_eur_falls_back_to_mak():
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_no_eur_sort")
    df = _ranking_snapshot_with_diverging_headcount_and_mak()
    metric_config = org_page._get_metric_config(df, "EUR")

    assert "EUR" not in org_page._SORT_OPTIONS_BASE
    assert org_page._resolve_sort_metric("Aktuelle Kennzahl", "EUR") == "MAK"
    assert org_page._resolve_sort_metric("EUR", "EUR") == "MAK"

    ranking = org_page._build_orgunit_ranking_frame(
        df,
        comparison_mapped_df=None,
        departure_events=None,
        metric_view="EUR",
        metric_config=metric_config,
        comparison_active=False,
        value_label="Simulation",
        comparison_label="IST",
    )

    assert org_page._apply_orgunit_top_filters(
        ranking,
        "2",
        "Aktuelle Kennzahl",
        "EUR",
        "Alle",
        "Alle",
        comparison_active=False,
    ) == ["Weniger_Vollzeit", "Viele_Teilzeit"]


def test_jobfamily_top_n_selects_largest_values_not_smallest():
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_analysis_top_n_order")
    compact = load_compact_page_module()
    df = _ranking_snapshot()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")

    assert jobfamily_page._get_top_jobfamilies(df, metric_config, compact, top_n="2") == ["Gross", "Mittel"]


def test_jobfamily_current_metric_sort_uses_visible_mak_not_headcount():
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_analysis_current_metric_sort")
    compact = load_compact_page_module()
    df = _ranking_snapshot_with_diverging_headcount_and_mak()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")

    ranking = jobfamily_page._build_jobfamily_ranking_frame(df, "MAK", metric_config, compact)

    assert jobfamily_page._apply_jobfamily_top_filters(
        ranking,
        "2",
        "Aktuelle Kennzahl",
        "MAK",
        "Alle",
    ) == ["Weniger_Vollzeit", "Viele_Teilzeit"]

    assert jobfamily_page._apply_jobfamily_top_filters(
        ranking,
        "2",
        "Köpfe",
        "MAK",
        "Alle",
    ) == ["Viele_Teilzeit", "Weniger_Vollzeit"]


def test_jobfamily_min_size_filter_applies_before_top_n():
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_analysis_min_size")
    compact = load_compact_page_module()
    df = _ranking_snapshot_with_diverging_headcount_and_mak()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")

    ranking = jobfamily_page._build_jobfamily_ranking_frame(df, "MAK", metric_config, compact)

    assert jobfamily_page._apply_jobfamily_top_filters(
        ranking,
        "Alle",
        "Aktuelle Kennzahl",
        "MAK",
        "mind. 10 Köpfe",
    ) == ["Weniger_Vollzeit", "Viele_Teilzeit"]


def test_jobfamily_sort_options_do_not_offer_eur_and_eur_falls_back_to_mak():
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_analysis_no_eur_sort")
    compact = load_compact_page_module()
    df = _ranking_snapshot_with_diverging_headcount_and_mak()
    metric_config = jobfamily_page._get_metric_config(df, "EUR")

    assert "EUR" not in jobfamily_page._SORT_OPTIONS_BASE
    assert jobfamily_page._resolve_sort_metric("Aktuelle Kennzahl", "EUR") == "MAK"
    assert jobfamily_page._resolve_sort_metric("EUR", "EUR") == "MAK"

    ranking = jobfamily_page._build_jobfamily_ranking_frame(df, "EUR", metric_config, compact)

    assert jobfamily_page._apply_jobfamily_top_filters(
        ranking,
        "2",
        "Aktuelle Kennzahl",
        "EUR",
        "Alle",
    ) == ["Weniger_Vollzeit", "Viele_Teilzeit"]


def test_orgunit_ranking_chart_axis_places_largest_value_at_top(monkeypatch):
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_chart_order")
    compact = load_compact_page_module()
    df = _ranking_snapshot()
    metric_config = org_page._get_metric_config(df, "MAK")
    capture_st = _ChartCaptureStreamlit()

    monkeypatch.setattr(org_page, "st", capture_st)
    monkeypatch.setattr(org_page, "dataframe_compat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(org_page, "download_button_compat", lambda *_args, **_kwargs: None)

    org_page._render_org_rangliste(
        df,
        "MAK",
        metric_config,
        compact,
        ["Gross", "Mittel", "Klein"],
    )

    fig = capture_st.figures[0]
    assert list(fig.data[0].y) == ["Gross", "Mittel", "Klein"]
    assert list(fig.layout.yaxis.categoryarray) == ["Klein", "Mittel", "Gross"]


def test_jobfamily_ranking_chart_axis_places_largest_value_at_top(monkeypatch):
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_analysis_chart_order")
    compact = load_compact_page_module()
    df = _ranking_snapshot()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")
    capture_st = _ChartCaptureStreamlit()

    monkeypatch.setattr(jobfamily_page, "st", capture_st)
    monkeypatch.setattr(jobfamily_page, "dataframe_compat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobfamily_page, "download_button_compat", lambda *_args, **_kwargs: None)

    jobfamily_page._render_jobfamily_rangliste(
        df,
        "MAK",
        metric_config,
        compact,
        value_label="IST",
        key_prefix="test_jobfamily",
        top_n="2",
    )

    fig = capture_st.figures[0]
    assert list(fig.data[0].y) == ["Gross", "Mittel"]
    assert list(fig.layout.yaxis.categoryarray) == ["Mittel", "Gross"]
