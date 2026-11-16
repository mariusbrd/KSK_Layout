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
    from utils.org_simulation_events import RECONCILIATION_VERSION
    from utils.simulation_validation import approve_original, register_new_run

    st.session_state["compact_sim_signature"] = "test-page-run"
    st.session_state["compact_sim_event_context"] = {
        "run_signature": "test-page-run",
        "baseline_snapshot": status_quo_df,
        "final_snapshot": prepared_df,
    }
    st.session_state["compact_sim_event_summary"] = {
        "run_signature": "test-page-run",
        "reconciliation_version": RECONCILIATION_VERSION,
        "events": pd.DataFrame(),
        "stock_reconciliation": {"status": "Nicht verfügbar"},
    }
    register_new_run(st.session_state)
    approve_original(st.session_state, approved_at="2026-10-27T00:00:00+00:00")
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
    assert captured["kwargs"]["monte_carlo_ensemble"] is None
    assert captured["kwargs"]["comparison_df"] is st.session_state["compact_sim_status_quo_df"]
    assert not captured["kwargs"]["departure_events_df"].empty
    assert "31.01.2026" in captured["kwargs"]["subtitle"]
    pd.testing.assert_frame_equal(
        captured["df"].reset_index(drop=True),
        prepared_df.reset_index(drop=True),
        check_dtype=False,
    )
    assert metric_config == {"value_col": "Headcount", "value_type": "koepfe"}


def test_orgunit_simulation_page_passes_only_matching_approved_ensemble(monkeypatch):
    _run_reusable_orgunit_simulation()
    sim_page = _load_page_module("Organisationseinheiten_Simulation", "orgunit_simulation_mc")
    from utils.simulation_validation import current_view
    view = current_view(st.session_state, require_approval=True)
    saved = {
        "status": "complete", "count": 2, "band_level": 0.8,
        "reference_signature": view.signature, "reference_instance": view.instance,
        "statistics": {"status": "complete", "requested_count": 2,
                       "annual_event_samples": [], "annual_stock_samples": []},
    }
    st.session_state["monte_carlo_ensemble_result"] = saved
    captured = {}
    fake = type("FakeOrgModule", (), {"render_orgunit_analysis_page": staticmethod(
        lambda *_args, **kwargs: captured.update(kwargs))})
    monkeypatch.setattr(sim_page, "_load_orgunit_analysis_module", lambda: fake)
    sim_page.main()
    assert captured["monte_carlo_ensemble"] is saved
    saved["reference_instance"] += 1
    sim_page.main()
    assert captured["monte_carlo_ensemble"] is None


@pytest.mark.parametrize("page_name,loader_name", [
    ("Organisationseinheiten_Simulation", "_load_orgunit_analysis_module"),
    ("Jobfamily_Simulation", "_load_jobfamily_analysis_module"),
])
def test_simulation_analysis_pages_do_not_render_without_approval(monkeypatch, page_name, loader_name):
    _run_reusable_orgunit_simulation()
    st.session_state.pop("simulation_validation_approval", None)
    sim_page = _load_page_module(page_name, f"blocked_{page_name}")
    monkeypatch.setattr(sim_page, loader_name, lambda: pytest.fail("analysis must remain gated"))
    sim_page.main()


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


def test_orgunit_ranking_handles_missing_or_numeric_departure_org_keys():
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_analysis_mixed_departure_org")
    mapped = pd.DataFrame({"PersNr": ["000001"], "Organisationseinheit": ["A"],
                           "Is_Vacant": [False]})
    departures = pd.DataFrame({"Organisationseinheit": [None, 123],
                               "persnr": ["000002", "000003"],
                               "headcount_change": [-1, -1], "mak_change": [-1.0, -0.5]})
    summary = org_page._build_departure_org_summary(departures)
    assert set(summary.Organisationseinheit) == {"Nicht zugeordnet", "123"}
    ranking = org_page._build_orgunit_ranking_frame(
        mapped, mapped, departures, "Köpfe", {"value_col": "Headcount", "value_type": "koepfe"},
        comparison_active=True, value_label="Simulation", comparison_label="IST")
    assert ranking.set_index("Organisationseinheit").loc["123", "Abgänge"] == 1


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


def test_jobfamily_simulation_blocks_invalid_matching_multirun(monkeypatch):
    _run_reusable_orgunit_simulation()
    sim_page = _load_page_module("Jobfamily_Simulation", "jobfamily_invalid_multirun")
    from utils.simulation_validation import current_view
    view = current_view(st.session_state, require_approval=True)
    st.session_state["monte_carlo_ensemble_result"] = {
        "status": "complete", "count": 2,
        "reference_signature": view.signature,
        "reference_instance": view.instance,
        "statistics": {"status": "complete", "requested_count": 2},
    }
    errors = []
    monkeypatch.setattr(sim_page.st, "error", lambda message: errors.append(message))
    monkeypatch.setattr(sim_page, "_load_jobfamily_analysis_module",
                        lambda: pytest.fail("invalid multirun must not render Seed 0"))

    sim_page.main()

    assert len(errors) == 1
    assert "Monte-Carlo-Laufdaten" in errors[0]


def test_orgunit_simulation_blocks_invalid_matching_multirun(monkeypatch):
    _run_reusable_orgunit_simulation()
    sim_page = _load_page_module("Organisationseinheiten_Simulation", "orgunit_invalid_multirun")
    from utils.simulation_validation import current_view
    view = current_view(st.session_state, require_approval=True)
    st.session_state["monte_carlo_ensemble_result"] = {
        "status": "complete", "count": 2,
        "reference_signature": view.signature,
        "reference_instance": view.instance,
        "statistics": {"status": "complete", "requested_count": 2},
    }
    errors = []
    monkeypatch.setattr(sim_page.st, "error", lambda message: errors.append(message))
    monkeypatch.setattr(sim_page, "_load_orgunit_analysis_module",
                        lambda: pytest.fail("invalid multirun must not render Seed 0"))

    sim_page.main()

    assert len(errors) == 1
    assert "Monte-Carlo-Laufdaten" in errors[0]


def test_jobfamily_simulation_renders_valid_multirun_with_empty_reference(monkeypatch):
    _run_reusable_orgunit_simulation()
    sim_page = _load_page_module("Jobfamily_Simulation", "jobfamily_empty_reference_mc")
    from utils.simulation_validation import current_view
    view = current_view(st.session_state, require_approval=True)
    saved = {
        "status": "complete", "count": 2, "band_level": 0.8,
        "reference_signature": view.signature,
        "reference_instance": view.instance,
        "statistics": {"status": "complete", "requested_count": 2,
                       "annual_event_samples": [], "annual_stock_samples": []},
    }
    st.session_state["monte_carlo_ensemble_result"] = saved
    monkeypatch.setattr(sim_page, "current_view",
                        lambda *_args, **_kwargs: type("EmptyView", (), {
                            "prepared_df": view.prepared_df.iloc[:0].copy(),
                            "metadata": view.metadata,
                            "signature": view.signature,
                            "instance": view.instance,
                            "version": view.version,
                            "approved": view.approved,
                            "event_context": view.event_context,
                            "event_summary": view.event_summary,
                        })())
    captured = {}
    fake = type("FakeJobfamilyModule", (), {"render_jobfamily_analysis_page": staticmethod(
        lambda *_args, **kwargs: captured.update(kwargs))})
    monkeypatch.setattr(sim_page, "_load_jobfamily_analysis_module", lambda: fake)

    sim_page.main()

    assert captured["monte_carlo_ensemble"] is saved


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
    monkeypatch.setattr(org_page, "lazy_excel_download_button_compat", lambda *_args, **_kwargs: None)

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


def test_monte_carlo_ranking_and_ist_comparison_use_mean_and_gray_band(monkeypatch):
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_mc_ranking")
    compact = load_compact_page_module()
    current = _ranking_snapshot()
    baseline = current.copy()
    config = org_page._get_metric_config(current, "MAK")
    stats = {"count": 2, "band_level": 0.8}
    for code in ("heads", "mak", "eur", "departures", "mak_loss"):
        stats[code] = {}
    stats["mak"]["Gross"] = {"mean": 10.0, "lower": 8.0, "upper": 12.0,
                               "samples": [8.0, 12.0], "seed_zero": 8.0}
    for org in ("Mittel", "Klein"):
        stats["mak"][org] = {"mean": 4.0, "lower": 3.0, "upper": 5.0,
                              "samples": [3.0, 5.0], "seed_zero": 3.0}
    capture_st = _ChartCaptureStreamlit()
    tables = []
    monkeypatch.setattr(org_page, "st", capture_st)
    monkeypatch.setattr(org_page, "dataframe_compat",
                        lambda frame, **_kwargs: tables.append(frame.copy()))
    monkeypatch.setattr(org_page, "lazy_excel_download_button_compat",
                        lambda *_args, **_kwargs: None)
    export = {}
    org_page._render_org_rangliste(
        current, "MAK", config, compact, ["Gross", "Mittel", "Klein"],
        value_label="Simulation", export_tables=export,
        monte_carlo_ensemble={"count": 2, "band_level": 0.8}, rank_statistics=stats)
    assert float(capture_st.figures[0].data[0].x[0]) == 10.0
    assert capture_st.figures[0].data[0].error_x.color == "#6b7280"
    assert "P10" in tables[0] and "P90" in export["Rangliste"]
    # The page omits the optional ensemble argument when a sidebar filter is
    # active. Rank statistics still contain the complete run metadata.
    org_page._render_org_rangliste(
        current, "MAK", config, compact, ["Gross", "Mittel", "Klein"],
        value_label="Simulation", rank_statistics=stats)
    assert float(capture_st.figures[1].data[0].x[0]) == 10.0

    org_page._render_org_rangliste_comparison(
        current, baseline, "MAK", config, compact, ["Gross", "Mittel", "Klein"],
        value_label="Simulation", comparison_label="IST", rank_statistics=stats,
        export_tables=export)
    comparison = export["Rangliste_Vergleich"].set_index("Organisationseinheit")
    assert comparison.loc["Gross", "Simulation"] == 10.0
    assert comparison.loc["Gross", "Delta"] == -2.0
    assert comparison.loc["Gross", "P10"] == 8.0
    assert any(trace.error_x.color == "#6b7280" for trace in capture_st.figures[2].data)


def test_monte_carlo_top_n_uses_means_instead_of_reference_order():
    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_mc_sort")
    reference_rank = pd.DataFrame({
        "Organisationseinheit": ["Gross", "Mittel"], "Köpfe": [12, 8],
        "MAK": [12.0, 8.0], "Mindestgröße Köpfe": [12, 8],
        "Mindestgröße MAK": [12.0, 8.0], "Delta": [0.0, 0.0],
        "Abgänge": [0, 0], "MAK-Verlust": [0.0, 0.0]})
    stats = {metric: {} for metric in ("heads", "mak", "eur", "departures", "mak_loss")}
    for metric in ("heads", "mak"):
        stats[metric] = {"Gross": {"mean": 5.0}, "Mittel": {"mean": 15.0}}
    updated = org_page._apply_monte_carlo_ranking_values(
        reference_rank, stats, None, "MAK", {}, "IST", False)
    selected = org_page._apply_orgunit_top_filters(
        updated, "8", "Aktuelle Kennzahl", "MAK", "Alle", "Alle",
        comparison_active=False)
    assert selected == ["Mittel", "Gross"]
    assert org_page._apply_orgunit_top_filters(
        updated, "Alle", "Aktuelle Kennzahl", "MAK", "mind. 10 Köpfe", "Alle",
        comparison_active=False) == ["Mittel"]


def test_monte_carlo_composition_chart_table_and_comparison_match(monkeypatch):
    import pickle
    import zlib
    from utils.monte_carlo_filtered_org import filtered_org_composition_statistics

    org_page = _load_page_module("Organisationseinheiten_Analyse", "orgunit_mc_composition")
    compact = load_compact_page_module()
    reference = _ranking_snapshot().loc[lambda frame: frame["Organisationseinheit"].eq("Gross")].copy()
    alternative = reference.copy()
    alternative.loc[alternative.index[:4], "Geschlecht"] = "m"
    def blob(frame):
        return zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8,
                "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(reference), blob(alternative)]}
    composition = filtered_org_composition_statistics(
        ensemble, {}, ["Gross"], "Köpfe", ("Geschlecht",))["Geschlecht"]
    capture_st = _ChartCaptureStreamlit()
    tables = []
    downloads = []
    monkeypatch.setattr(org_page, "st", capture_st)
    monkeypatch.setattr(org_page, "dataframe_compat",
                        lambda frame, **_kwargs: tables.append(frame.copy()))
    monkeypatch.setattr(org_page, "lazy_excel_download_button_compat",
                        lambda *_args, **kwargs: downloads.append(kwargs))
    config = org_page._get_metric_config(reference, "Köpfe")
    export = {}
    org_page._render_org_split_block(
        reference, "Geschlecht", "Geschlecht", "Köpfe", config, compact,
        "mc_gender", ["Gross"], export_tables=export,
        composition_view=composition, monte_carlo_band_level=0.8)
    assert not capture_st.figures[0].data[0].type == "scatter"
    assert any(trace.name == "P10–P90 Gesamt" for trace in capture_st.figures[0].data)
    assert tables[0].loc[0, "w"] == compact.format_number(10.0, 0)
    assert "w P10" in export["Geschlecht_Tabelle"]

    org_page._render_org_split_block(
        reference, "Geschlecht", "Geschlecht", "Köpfe", config, compact,
        "mc_gender_compare", ["Gross"], comparison_mapped_df=reference,
        comparison_active=True, value_label="Simulation", comparison_label="IST",
        export_tables=export, composition_view=composition,
        monte_carlo_band_level=0.8)
    compared = export["Geschlecht_Tabelle"].set_index("Geschlecht")
    assert compared.loc["w", "Simulation"] == 10.0
    assert compared.loc["w", "Delta"] == -2.0
    assert compared.loc["w", "P10"] == pytest.approx(8.4)
    monkeypatch.setattr(compact, "export_to_excel", lambda frame, **_kwargs: frame.copy())
    direct_export = downloads[-1]["data_builder"]()
    pd.testing.assert_frame_equal(direct_export.reset_index(drop=True),
                                  export["Geschlecht_Tabelle"].reset_index(drop=True))


def test_jobfamily_ranking_chart_axis_places_largest_value_at_top(monkeypatch):
    jobfamily_page = _load_page_module("Jobfamily_Analyse", "jobfamily_analysis_chart_order")
    compact = load_compact_page_module()
    df = _ranking_snapshot()
    metric_config = jobfamily_page._get_metric_config(df, "MAK")
    capture_st = _ChartCaptureStreamlit()

    monkeypatch.setattr(jobfamily_page, "st", capture_st)
    monkeypatch.setattr(jobfamily_page, "dataframe_compat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobfamily_page, "lazy_excel_download_button_compat", lambda *_args, **_kwargs: None)

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


def test_jobfamily_monte_carlo_ranking_top_and_right_table_use_run_means(monkeypatch):
    import pickle
    import zlib

    page = _load_page_module("Jobfamily_Analyse", "jobfamily_mc_ranking")
    compact = load_compact_page_module()
    reference = _ranking_snapshot()
    alternative = reference.copy()
    alternative.loc[alternative.index[:4], "Jobfamily"] = "Neu"
    blob = lambda frame: zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(reference), blob(alternative)]}
    stats = page._monte_carlo_jobfamily_rank_statistics(
        ensemble, {}, reference, "MAK", compact)
    assert stats["values"]["MAK"]["Gross"]["mean"] == 10.0
    assert stats["values"]["MAK"]["Neu"]["mean"] == 2.0
    ranking = page._monte_carlo_jobfamily_ranking_frame(stats)
    assert page._apply_jobfamily_top_filters(
        ranking, "2", "Aktuelle Kennzahl", "MAK", "Alle") == ["Gross", "Mittel"]

    capture = _ChartCaptureStreamlit()
    tables, downloads = [], []
    monkeypatch.setattr(page, "st", capture)
    monkeypatch.setattr(page, "dataframe_compat", lambda frame, **_kwargs: tables.append(frame.copy()))
    monkeypatch.setattr(page, "lazy_excel_download_button_compat",
                        lambda *_args, **kwargs: downloads.append(kwargs))
    page._render_jobfamily_rangliste(
        reference, "MAK", page._get_metric_config(reference, "MAK"), compact,
        value_label="Simulation", key_prefix="jf_mc", top_n="Alle",
        display_jobfamilies=["Gross", "Mittel", "Neu"], is_simulation=True,
        rank_statistics=stats)
    assert list(capture.figures[0].data[0].x) == [10.0, 8.0, 2.0]
    assert capture.figures[0].data[0].error_x.color == "#6b7280"
    assert tables[0].loc[0, "P10"] == compact.format_number(8.4, 1)
    monkeypatch.setattr(compact, "export_to_excel", lambda frame, **_kwargs: frame.copy())
    exported = downloads[0]["data_builder"]()
    assert exported.loc[0, "Simulation"] == 10.0
    assert exported.loc[0, "P10"] == pytest.approx(8.4)


def test_jobfamily_monte_carlo_ranking_rejects_seed_zero_drift():
    import pickle
    import zlib

    page = _load_page_module("Jobfamily_Analyse", "jobfamily_mc_seed_drift")
    compact = load_compact_page_module()
    reference = _ranking_snapshot()
    changed = reference.copy()
    changed.loc[changed.index[0], "MAK_Reporting"] = 0.5
    blob = lambda frame: zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(changed), blob(reference)]}
    with pytest.raises(ValueError, match="Seed 0"):
        page._monte_carlo_jobfamily_rank_statistics(ensemble, {}, reference, "MAK", compact)


def test_jobfamily_monte_carlo_kpis_average_each_runs_ratio_and_check_seed_zero():
    import pickle
    import zlib

    page = _load_page_module("Jobfamily_Analyse", "jobfamily_mc_kpis")
    compact = load_compact_page_module()
    reference = _ranking_snapshot()
    reference["BsGrd"] = 100.0
    later = reference.iloc[:2].copy()
    blob = lambda frame: zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(reference), blob(later)]}
    stats = page._monte_carlo_jobfamily_kpi_statistics(
        ensemble, {}, reference, "MAK", compact)
    first = page._jobfamily_kpi_operands(reference, "MAK", compact)
    second = page._jobfamily_kpi_operands(later, "MAK", compact)
    assert stats["heads"]["mean"] == pytest.approx((first["heads"] + second["heads"]) / 2)
    assert stats["average_mak"]["mean"] == pytest.approx(
        (first["average_mak"] + second["average_mak"]) / 2)
    rank = page._monte_carlo_jobfamily_rank_statistics(ensemble, {}, reference, "MAK", compact)
    cards = page._build_monte_carlo_jobfamily_kpis(stats, rank, "MAK", compact)
    assert cards[0]["value"] == compact.format_number(stats["mak"]["mean"], 1)
    assert "P10–P90" in cards[0]["subtitle"]

    changed = reference.copy()
    changed.loc[changed.index[0], "MAK_Reporting"] = 0.5
    ensemble["org_filter_snapshots"][0] = blob(changed)
    with pytest.raises(ValueError, match="Seed 0"):
        page._monte_carlo_jobfamily_kpi_statistics(ensemble, {}, reference, "MAK", compact)


def test_jobfamily_monte_carlo_composition_uses_run_segments_and_bands(monkeypatch):
    import pickle
    import zlib

    page = _load_page_module("Jobfamily_Analyse", "jobfamily_mc_composition")
    compact = load_compact_page_module()
    reference = _ranking_snapshot()
    reference["Geschlecht"] = "w"
    later = reference.copy()
    later.loc[later.index[:4], "Geschlecht"] = "m"
    blob = lambda frame: zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(reference), blob(later)]}
    stats = page._monte_carlo_jobfamily_split_statistics(
        ensemble, {}, reference, ["Gross"], "Geschlecht", "Köpfe", compact)
    women = stats.loc[stats["Geschlecht"].eq("w")].iloc[0]
    assert women["Wert"] == pytest.approx(10.0)
    assert women["P10"] == pytest.approx(8.4)
    assert women["P90"] == pytest.approx(11.6)

    capture = _ChartCaptureStreamlit()
    tables, downloads = [], []
    monkeypatch.setattr(page, "st", capture)
    monkeypatch.setattr(page, "dataframe_compat", lambda frame, **_kwargs: tables.append(frame.copy()))
    monkeypatch.setattr(page, "lazy_excel_download_button_compat",
                        lambda *_args, **kwargs: downloads.append(kwargs))
    page._render_jobfamily_split_block(
        reference, "Geschlecht", "Geschlecht", "Köpfe",
        page._get_metric_config(reference, "Köpfe"), compact, "jf_mc_split",
        display_jobfamilies=["Gross"], monte_carlo_statistics=stats)
    assert "P10" in tables[0].columns
    assert capture.figures[0].data[-1].error_x.color == "#6b7280"
    monkeypatch.setattr(compact, "export_to_excel", lambda frame, **_kwargs: frame.copy())
    exported = downloads[0]["data_builder"]()
    assert exported.loc[exported["Geschlecht"].eq("w"), "P10"].iloc[0] == pytest.approx(8.4)
    overall = exported.loc[exported["Geschlecht"].eq("Gesamt")].iloc[0]
    assert overall["Wert"] == pytest.approx(12.0)
    assert overall["P10"] == pytest.approx(12.0)
    assert overall["P90"] == pytest.approx(12.0)
    assert "Seed 0" not in exported.columns
    assert tables[0].loc[tables[0]["Geschlecht"].eq("Gesamt")].shape[0] == 1

    changed = reference.copy()
    changed.loc[changed.index[0], "Geschlecht"] = "m"
    ensemble["org_filter_snapshots"][0] = blob(changed)
    with pytest.raises(ValueError, match="Seed 0"):
        page._monte_carlo_jobfamily_split_statistics(
            ensemble, {}, reference, ["Gross"], "Geschlecht", "Köpfe", compact)


def test_jobfamily_monte_carlo_tariff_and_capacity_include_empty_later_group():
    import pickle
    import zlib

    page = _load_page_module("Jobfamily_Analyse", "jobfamily_mc_tariff_capacity")
    compact = load_compact_page_module()
    reference = _ranking_snapshot()
    reference["TrfGr"] = "E9"
    later = reference.loc[reference["Jobfamily"].ne("Gross")].copy()
    blob = lambda frame: zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(reference), blob(later)]}
    tariff = page._monte_carlo_jobfamily_split_statistics(
        ensemble, {}, reference, ["Gross"], "TrfGr", "MAK", compact)
    assert tariff.iloc[0]["Wert"] == pytest.approx(6.0)
    assert tariff.iloc[0]["P10"] == pytest.approx(1.2)

    capacity = page._monte_carlo_jobfamily_role_summary_statistics(
        ensemble, {}, reference, ["Gross"], compact)
    assert capacity.loc[0, "Köpfe"] == pytest.approx(6.0)
    assert capacity.loc[0, "MAK"] == pytest.approx(6.0)
    assert capacity.loc[0, "Ø MAK"] == pytest.approx(0.5)
    assert capacity.loc[0, "Ø MAK P90"] == pytest.approx(0.9)

    changed = reference.copy()
    changed.loc[changed.index[0], "MAK_Reporting"] = 0.5
    ensemble["org_filter_snapshots"][0] = blob(changed)
    with pytest.raises(ValueError, match="Seed 0"):
        page._monte_carlo_jobfamily_role_summary_statistics(
            ensemble, {}, reference, ["Gross"], compact)


def test_jobfamily_monte_carlo_quality_includes_unmapped_only_in_later_run(monkeypatch):
    import pickle
    import zlib

    page = _load_page_module("Jobfamily_Analyse", "jobfamily_mc_quality")
    compact = load_compact_page_module()
    reference = _ranking_snapshot()
    reference["Planstelle"] = "Stelle A"
    later = reference.copy()
    later.loc[later.index[:4], "Jobfamily"] = "UNMAPPED"
    blob = lambda frame: zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(reference), blob(later)]}
    stats = page._monte_carlo_jobfamily_quality_statistics(
        ensemble, {}, reference, "MAK", compact)
    assert stats["values"]["unmapped_rows"]["mean"] == pytest.approx(2.0)
    assert stats["values"]["unmapped_rows"]["upper"] == pytest.approx(3.6)
    assert stats["planstellen"]["Stelle A"]["mean"] == pytest.approx(2.0)

    import types
    cards, tables, downloads = [], [], []
    monkeypatch.setattr(compact, "render_kpi_cards_styled", lambda value: cards.extend(value))
    monkeypatch.setattr(page, "render_context_box", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(page, "st", types.SimpleNamespace(markdown=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(page, "dataframe_compat", lambda frame, **_kwargs: tables.append(frame.copy()))
    monkeypatch.setattr(page, "lazy_excel_download_button_compat",
                        lambda *_args, **kwargs: downloads.append(kwargs))
    page._render_data_quality_block(
        reference, reference, reference.iloc[:0], "MAK",
        page._get_metric_config(reference, "MAK"), compact,
        monte_carlo_statistics=stats)
    assert cards[0]["value"] == compact.format_number(2.0, 1)
    assert "P10–P90" in cards[0]["subtitle"]
    assert "P10" in tables[0]
    monkeypatch.setattr(compact, "export_to_excel", lambda frame, **_kwargs: frame.copy())
    exported = downloads[0]["data_builder"]()
    assert exported.loc[0, "P90"] == pytest.approx(3.6)

    changed = reference.copy()
    changed.loc[changed.index[0], "Jobfamily"] = "UNMAPPED"
    ensemble["org_filter_snapshots"][0] = blob(changed)
    with pytest.raises(ValueError, match="Seed 0"):
        page._monte_carlo_jobfamily_quality_statistics(
            ensemble, {}, reference, "MAK", compact)


def test_jobfamily_monte_carlo_ranking_keeps_group_only_in_later_run_when_filtered():
    import pickle
    import zlib

    page = _load_page_module("Jobfamily_Analyse", "jobfamily_mc_later_group")
    compact = load_compact_page_module()
    reference = _ranking_snapshot()
    later = reference.copy()
    later.loc[later.index[:4], "Jobfamily"] = "Neu"
    blob = lambda frame: zlib.compress(pickle.dumps(frame, protocol=pickle.HIGHEST_PROTOCOL))
    ensemble = {"count": 2, "band_level": 0.8, "org_filter_snapshot_version": 1,
                "org_filter_snapshots": [blob(reference), blob(later)]}
    stats = page._monte_carlo_jobfamily_rank_statistics(
        ensemble, {"selected_jobfamilies": ["Neu"]}, reference.iloc[:0], "MAK", compact)
    assert stats["groups"] == ["Neu"]
    assert stats["values"]["MAK"]["Neu"]["mean"] == 2.0
