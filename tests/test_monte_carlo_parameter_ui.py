"""The actual parameter and CompactPlus pages use one saved run setting."""
from __future__ import annotations

from pathlib import Path
from io import BytesIO

import pandas as pd
import pytest
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from utils.monte_carlo_export import build_monte_carlo_export_bytes
from utils.monte_carlo_chart_values import extract_ist_chart_values
from utils.monte_carlo_product_worker import _compact_module
from utils.simulation_params import default_simulation_params


ROOT = Path(__file__).resolve().parents[1]


def test_parameter_page_saves_count_without_duplicate_target_date():
    page = next((ROOT / "pages").glob("8_*_Simulationsparameter.py"))
    app = AppTest.from_file(str(page), default_timeout=90).run()
    assert not app.exception
    app.selectbox(key="sim_params_run_count").set_value(10).run()
    assert not app.exception
    next(button for button in app.button if button.label == "Parameter speichern").click().run()
    assert not app.exception
    saved = app.session_state["simulation_params"]
    assert saved["compact_plus"]["run_count"] == 10
    assert saved["abgaenge"]["random_seed"] == 42
    assert saved["zugaenge"]["random_seed"] == 42
    assert "compact_sim_signature" not in app.session_state
    assert not any(item.key == "sim_params_target_date" for item in app.date_input)
    app.run()
    assert app.selectbox(key="sim_params_run_count").value == 10


def _compact_app(count, target_date="2026-12-31", *, without_quits=False,
                 with_hires=False, with_training=False):
    page = next((ROOT / "pages").glob("7_*_Kompakt_plus_Simulation.py"))
    source = (
        "import importlib.util\nimport streamlit as st\n"
        "st.page_link = lambda *args, **kwargs: None\n"
        f"spec = importlib.util.spec_from_file_location('compact_plus_app_test', {str(page)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "real_button = st.button\n"
        "def test_button(label, *args, **kwargs):\n"
        "    if 'Simulation' in label and ('berechnen' in label or 'aktualisieren' in label):\n"
        "        return bool(st.session_state.get('test_start_simulation', False))\n"
        "    return real_button(label, *args, **kwargs)\n"
        "st.button = test_button\n"
        "module.render_global_filters = lambda *a, **k: None\n"
        "module.get_active_view_filters = lambda: {}\n"
        "module.filter_dataframe_by_view_filters = lambda df, filters: df\n"
        "module.get_filter_summary = lambda: ''\n"
        "module.render_active_filter_banner = lambda *a, **k: None\n"
        "module.set_metric_page_hint = lambda *a, **k: None\n"
        "module.get_global_metric_view = lambda: 'MAK'\n"
        "from types import SimpleNamespace\n"
        "from utils.monte_carlo_product_worker import _compact_prepare\n"
        "module.load_compact_page_module = lambda: SimpleNamespace(\n"
        "    prepare_compact_data=_compact_prepare(),\n"
        "    render_ist_mak_tab=lambda *a, **k: None,\n"
        "    render_ist_koepfe_tab=lambda *a, **k: None,\n"
        "    render_ist_vs_soll_mak_tab=lambda *a, **k: None,\n"
        "    render_ist_soll_koepfe_tab=lambda *a, **k: None)\n"
        "module.main()\n"
    )
    app = AppTest.from_string(source.encode("ascii", "backslashreplace").decode(),
                              default_timeout=180)
    settings = default_simulation_params()
    settings["compact_plus"].update(target_date=target_date, run_count=count)
    if without_quits:
        settings["abgaenge"]["components"]["quit"] = False
        settings["abgaenge"]["random_seed"] = 17
        settings["zugaenge"]["random_seed"] = 17
    if with_hires:
        settings["abgaenge"]["random_seed"] = 29
        settings["zugaenge"]["random_seed"] = 29
        settings["zugaenge"]["new_hires"].update(
            active=True, count_per_year=20, strategy="Fill Vacancies")
    if with_training:
        settings["abgaenge"]["random_seed"] = 31
        settings["zugaenge"]["random_seed"] = 31
        settings["zugaenge"]["azubi"].update(
            active=True, new_cases_per_year=4, retention_rate=1.0)
        settings["zugaenge"]["trainee"].update(active=True, new_cases_per_year=4)
    app.session_state["simulation_params"] = settings
    return app.run()


@pytest.mark.parametrize("without_quits", [False, True])
def test_compact_plus_single_run_uses_saved_target_without_monte_carlo(without_quits):
    app = _compact_app(1, without_quits=without_quits)
    assert not app.exception
    assert str(app.session_state["compact_sim_target_date_cached"].date()) == "2026-12-31"
    assert "monte_carlo_ensemble_result" not in app.session_state
    if without_quits:
        assert app.session_state["simulation_params"]["abgaenge"]["components"]["quit"] is False
        assert app.session_state["simulation_params"]["abgaenge"]["random_seed"] == 17
    assert not any("Monte-Carlo-Kennzahlen" in item.value for item in app.caption)
    assert any(button.label == "Simulationsergebnisse als Excel exportieren" for button in app.button)
    assert not any(button.label == "Monte-Carlo-Kennzahlen als Excel exportieren" for button in app.button)


@pytest.mark.parametrize("target_date, expected_years, without_quits, with_hires, with_training", [
    pytest.param("2026-12-31", {2026}, False, False, False, id="standard-2026"),
    pytest.param("2027-12-31", {2026, 2027}, False, False, False, id="standard-2027"),
    pytest.param("2027-12-31", {2026, 2027}, True, False, False, id="without-quits"),
    pytest.param("2027-12-31", {2026, 2027}, False, True, False, id="regular-hires"),
    pytest.param("2027-12-31", {2026, 2027}, False, False, True, id="training"),
    pytest.param("2030-12-31", set(range(2026, 2031)), False, False, True,
                 id="training-conversion-2030"),
])
def test_real_two_run_jobfamily_year_values_match_saved_reference(
        target_date, expected_years, without_quits, with_hires, with_training):
    from components.jobfamily_simulation_yearly import (
        _monte_carlo_jobfamily_event_frame, _monte_carlo_position_stock_frame,
        build_jobfamily_yearly_view,
    )

    app = _compact_app(2, target_date, without_quits=without_quits,
                       with_hires=with_hires, with_training=with_training)
    assert not app.exception
    app.session_state["test_start_simulation"] = True
    app.run()
    assert not app.exception
    ensemble = app.session_state["monte_carlo_ensemble_result"]
    from utils.monte_carlo_representative_data import load_representative_seed_data
    for run_index in range(ensemble["count"]):
        selected_run = load_representative_seed_data(ensemble, run_index)
        assert selected_run.seed_pair == tuple(ensemble["seed_pairs"][run_index])
    if target_date == "2026-12-31" and not any((without_quits, with_hires, with_training)):
        from utils.monte_carlo_representative_data import materialize_representative_seed_detail
        detail = materialize_representative_seed_detail(
            app.session_state["monte_carlo_ensemble_request"], ensemble, 1)
        assert detail.saved.index == 1
        assert detail.summary["stock_reconciliation"]["status"] == "Abgestimmt"
        assert not detail.summary["annual_oe"].empty
    if without_quits or with_hires or with_training:
        expected_seed = 17 if without_quits else 29 if with_hires else 31
        assert app.session_state["compact_sim_abgaenge_params"]["random_seed"] == expected_seed
        if without_quits:
            assert app.session_state["compact_sim_abgaenge_params"]["components"]["quit"] is False
        if with_hires:
            assert app.session_state["compact_sim_zugaenge_params"]["new_hires"]["active"] is True
            assert app.session_state["compact_sim_zugaenge_params"]["new_hires"]["count_per_year"] == 20
            accession_events = app.session_state["compact_sim_event_context"]["accession_events"]
            assert accession_events["type"].eq("New_Hire").any()
        if with_training:
            assert app.session_state["compact_sim_zugaenge_params"]["azubi"]["active"] is True
            assert app.session_state["compact_sim_zugaenge_params"]["trainee"]["active"] is True
            accession_events = app.session_state["compact_sim_event_context"]["accession_events"]
            assert accession_events["type"].eq("Azubi_Hire").any()
            assert not accession_events["type"].eq("Trainee_Hire").any()
            if target_date == "2030-12-31":
                assert accession_events["type"].eq("Azubi_Conversion_In").any()
        single = _compact_app(1, target_date, without_quits=without_quits,
                              with_hires=with_hires, with_training=with_training)
        assert not single.exception
        single.session_state["test_start_simulation"] = True
        single.run()
        assert not single.exception
        assert "monte_carlo_ensemble_result" not in single.session_state
        pd.testing.assert_frame_equal(
            single.session_state["compact_sim_prepared_df"],
            app.session_state["compact_sim_prepared_df"], check_exact=True)
        for name in ("annual_oe", "annual_cluster"):
            pd.testing.assert_frame_equal(
                single.session_state["compact_sim_event_summary"][name],
                app.session_state["compact_sim_event_summary"][name],
                check_exact=True)
        for dimension in ("Jobfamily", "JF-Cluster"):
            single_years = build_jobfamily_yearly_view(
                single.session_state["compact_sim_event_context"],
                single.session_state["compact_sim_event_summary"],
                dimension=dimension, include_stock=False)
            ensemble_seed_zero_years = build_jobfamily_yearly_view(
                app.session_state["compact_sim_event_context"],
                app.session_state["compact_sim_event_summary"],
                dimension=dimension, include_stock=False)
            pd.testing.assert_frame_equal(
                single_years["annual"], ensemble_seed_zero_years["annual"],
                check_exact=True)
    context = app.session_state["compact_sim_event_context"]
    summary = app.session_state["compact_sim_event_summary"]
    stock = _monte_carlo_position_stock_frame(ensemble, {})
    assert not stock.empty
    assert set(pd.to_datetime(stock["Stichtag"]).dt.year) == expected_years
    assert all(len(samples) == 2 for samples in stock["samples"])
    prepared = app.session_state["compact_sim_prepared_df"]
    gender = prepared["Geschlecht"].dropna().iloc[0]
    person_filters = {"selected_genders": [gender]}
    filtered_stock = _monte_carlo_position_stock_frame(ensemble, person_filters)
    assert not filtered_stock.empty
    assert all(len(samples) == 2 for samples in filtered_stock["samples"])
    assert set(pd.to_datetime(filtered_stock["Stichtag"]).dt.year) == expected_years
    for year in expected_years:
        def seed_zero_mak(frame):
            rows = frame.loc[frame["Stichtag"].dt.year.eq(year)
                             & frame["Ebene"].eq("Jobfamily")
                             & frame["Kennzahl"].eq("MAK")]
            return sum(float(samples[0]) for samples in rows["samples"])
        assert 0 < seed_zero_mak(filtered_stock) < seed_zero_mak(stock)
    for dimension in ("Jobfamily", "JF-Cluster"):
        reference = build_jobfamily_yearly_view(
            context, summary, dimension=dimension, include_stock=False)
        events = _monte_carlo_jobfamily_event_frame(
            ensemble, context, reference, dimension, {})
        assert len(events.attrs["event_runs"]) == 2
        assert len(events.attrs["reported_runs"]) == 2
        filtered_events = _monte_carlo_jobfamily_event_frame(
            ensemble, context, reference, dimension, person_filters)
        assert len(filtered_events.attrs["event_runs"]) == 2
        assert len(filtered_events.attrs["reported_runs"]) == 2
    yearly_app = AppTest.from_string(
        "import streamlit as st\n"
        "from components.jobfamily_simulation_yearly import render_jobfamily_simulation_yearly\n"
        "render_jobfamily_simulation_yearly("
        "st.session_state['context'], st.session_state['summary'], "
        "run_signature=st.session_state['summary']['run_signature'], "
        "monte_carlo_ensemble=st.session_state['ensemble'])\n",
        default_timeout=90)
    yearly_app.session_state["context"] = context
    yearly_app.session_state["summary"] = summary
    yearly_app.session_state["ensemble"] = ensemble
    yearly_app.run()
    assert not yearly_app.exception
    assert any("Summe der gewählten Jahre" in item.value for item in yearly_app.markdown)
    yearly_app.radio(key="jf_yearly_dimension").set_value("JF-Cluster").run()
    assert not yearly_app.exception
    yearly_app.session_state["global_metric_view"] = "MAK"
    yearly_app.run()
    assert not any(radio.label in {"Kennzahl des Bestandsverlaufs", "Kennzahl der Veränderung"}
                   for radio in yearly_app.radio)
    assert not yearly_app.exception
    assert any("Abgehende MAK" in list(frame.value.columns)
               for frame in yearly_app.dataframe)
    yearly_app.button(key="jf_yearly_prepare_export").click().run()
    assert not yearly_app.exception
    prepared = yearly_app.session_state["jf_yearly_prepared_export"]
    with pd.ExcelFile(BytesIO(prepared[1])) as book:
        totals = pd.read_excel(book, "Ereignisse_Summe")
        assert "Abgehende Köpfe" in totals
        assert "Abgehende MAK P90" in totals


def test_real_oe_scenario_page_export_uses_selected_seed():
    from utils.monte_carlo_representative_runs import select_representative_runs
    from utils.simulation_validation import approve_original

    compact_app = _compact_app(2)
    compact_app.session_state["test_start_simulation"] = True
    compact_app.run()
    assert not compact_app.exception
    ensemble = compact_app.session_state["monte_carlo_ensemble_result"]
    selected = select_representative_runs(ensemble)
    label, choice = next((label, item) for label, item in (
        ("Geringste Fluktuation", selected["best"]),
        ("Höchste Fluktuation", selected["worst"]),
        ("Typischer Verlauf", selected["typical"]),
    ) if item["index"] == 1)
    saved_state = {key: compact_app.session_state[key] for key in (
        "compact_sim_signature", "compact_sim_prepared_df", "compact_sim_status_quo_df",
        "compact_sim_metadata", "compact_sim_audit_tables", "compact_sim_event_context",
        "compact_sim_event_summary", "compact_sim_target_date_cached",
        "compact_sim_export_context", "compact_sim_abgaenge_params",
        "compact_sim_zugaenge_params", "simulation_validation_instance",
        "monte_carlo_ensemble_result", "monte_carlo_ensemble_request",
    )}
    approve_original(saved_state, approved_at="2026-11-16T00:00:00+00:00")

    page = next((ROOT / "pages").glob("10_*_Organisationseinheiten_Simulation.py"))
    source = (
        "import importlib.util\nimport streamlit as st\n"
        "st.page_link = lambda *args, **kwargs: None\n"
        f"spec = importlib.util.spec_from_file_location('real_oe_scenario_page', {str(page)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "original_radio = st.radio\n"
        "def choose_scenario(*args, **kwargs):\n"
        "    if kwargs.get('key') == 'org_sim_scenario_mode': return st.session_state['test_oe_scenario_label']\n"
        "    if kwargs.get('key') == 'org_yearly_level' and st.session_state.get('test_oe_cluster_mode'): return 'OE-Cluster'\n"
        "    return original_radio(*args, **kwargs)\n"
        "st.radio = choose_scenario\n"
        "original_multiselect = st.multiselect\n"
        "def choose_org(*args, **kwargs):\n"
        "    if kwargs.get('key') == 'org_units_select' and st.session_state.get('test_oe_org_filter'):\n"
        "        return [st.session_state['test_oe_org_filter']]\n"
        "    if kwargs.get('key') == 'jobfamily_select' and st.session_state.get('test_oe_jobfamily_filter'):\n"
        "        return [st.session_state['test_oe_jobfamily_filter']]\n"
        "    if kwargs.get('key') == 'employment_select' and st.session_state.get('test_oe_employment_filter'):\n"
        "        return [st.session_state['test_oe_employment_filter']]\n"
        "    return original_multiselect(*args, **kwargs)\n"
        "st.multiselect = choose_org\n"
        "import components.sidebar as sidebar\n"
        "import components.ui_compat as compat\n"
        "from unittest.mock import patch\n"
        "def app_test_metric_selector(label, options, value, key):\n"
        "    return st.selectbox(label, options, index=options.index(value), key=key)\n"
        "def capture(**kwargs):\n"
        "    if kwargs.get('key') == 'org_simulation_full_export':\n"
        "        st.session_state['test_oe_export_builder'] = kwargs['data_builder']\n"
        "with patch.object(compat, 'lazy_excel_download_button_compat', capture), "
        "patch.object(sidebar, '_segmented_single', app_test_metric_selector):\n"
        "    module.main()\n"
    )
    baseline_app = AppTest.from_string(source.encode("ascii", "backslashreplace").decode(),
                                       default_timeout=180)
    for key, value in saved_state.items():
        baseline_app.session_state[key] = value
    baseline_app.session_state["test_oe_scenario_label"] = "Konsolidiert"
    baseline_app.run()
    assert not baseline_app.exception
    key = (ensemble["plan_signature"], saved_state["compact_sim_signature"],
           saved_state["simulation_validation_instance"], 1)
    pending = baseline_app.session_state["org_sim_representative_prefetch"]
    assert key in pending
    detail = pending[key].result(timeout=120)
    saved_state["org_sim_representative_details"] = {key: detail}
    oe_app = AppTest.from_string(source.encode("ascii", "backslashreplace").decode(),
                                 default_timeout=180)
    for key, value in saved_state.items():
        oe_app.session_state[key] = value
    oe_app.session_state["test_oe_scenario_label"] = label
    oe_app.run()
    assert not oe_app.exception
    assert "test_oe_export_builder" in oe_app.session_state
    book = pd.ExcelFile(BytesIO(oe_app.session_state["test_oe_export_builder"]()))
    inventory = pd.read_excel(book, "Exportinventar").set_index("Bereich")
    exported = pd.read_excel(book, inventory.loc["Simulationsbasis_gefiltert", "Tabellenblatt"])
    assert len(exported) == len(detail.prepared)
    assert exported["Organisationseinheit"].fillna("").astype(str).tolist() == (
        detail.prepared["Organisationseinheit"].fillna("").astype(str).tolist())
    yearly = pd.read_excel(book, inventory.loc["Jahre_OE_vollständig", "Tabellenblatt"])
    assert len(yearly) == len(detail.summary["annual_oe"])
    for metric in ("Abgänge", "Zugänge", "MAK-Verlust"):
        if metric in yearly:
            assert yearly[metric].sum() == pytest.approx(detail.summary["annual_oe"][metric].sum())
    bridge = pd.read_excel(book, inventory.loc["Bestand_Überleitung_vollständig", "Tabellenblatt"])
    assert len(bridge) == len(detail.summary["stock_reconciliation"]["annual"])
    assert not any(name.startswith("MC_") or name.startswith("Ref_") for name in inventory.index)
    documentation = pd.read_excel(book, "00_Dokumentation").set_index("Feld")
    assert documentation.loc["Szenarioansicht", "Wert"] == label
    assert int(documentation.loc["Szenario-Laufindex", "Wert"]) == choice["index"]
    assert oe_app.session_state["org_sim_representative_details"]

    from components.sidebar import _apply_filters_uncached
    candidates = detail.prepared.loc[
        detail.prepared["Jobfamily"].notna()
        & detail.prepared["Jobfamily"].ne("UNMAPPED")
        & detail.prepared["Arbeitszeit"].isin(["Vollzeit", "Teilzeit", "Inaktiv"])
        & detail.prepared["Organisationseinheit"].notna()]
    assert not candidates.empty
    selected_person = candidates.iloc[0]
    org = str(selected_person["Organisationseinheit"])
    jobfamily = str(selected_person["Jobfamily"])
    employment = str(selected_person["Arbeitszeit"])
    filtered_app = AppTest.from_string(source.encode("ascii", "backslashreplace").decode(),
                                       default_timeout=180)
    for key, value in saved_state.items():
        filtered_app.session_state[key] = value
    filtered_app.session_state["org_sim_representative_details"] = (
        oe_app.session_state["org_sim_representative_details"])
    filtered_app.session_state["test_oe_cluster_mode"] = True
    filtered_app.session_state["test_oe_scenario_label"] = label
    filtered_app.session_state["test_oe_org_filter"] = org
    filtered_app.session_state["test_oe_jobfamily_filter"] = jobfamily
    filtered_app.session_state["test_oe_employment_filter"] = employment
    filtered_app.run()
    assert not filtered_app.exception
    filtered_book = pd.ExcelFile(BytesIO(filtered_app.session_state["test_oe_export_builder"]()))
    filtered_inventory = pd.read_excel(filtered_book, "Exportinventar").set_index("Bereich")
    filtered_target = pd.read_excel(
        filtered_book, filtered_inventory.loc["Simulationsbasis_gefiltert", "Tabellenblatt"])
    expected_target = _apply_filters_uncached(detail.prepared, {
        "selected_org_units": [org], "selected_jobfamilies": [jobfamily],
        "selected_genders": ["m", "w"], "selected_employment": [employment],
        "selected_atz_status": ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
    })
    assert len(filtered_target) == len(expected_target)
    assert len(filtered_target) > 0
    assert set(filtered_target["Organisationseinheit"]) == {org}
    assert set(expected_target["Jobfamily"]) == {jobfamily}
    assert set(expected_target["Arbeitszeit"]) == {employment}
    for metric in ("MAK_Reporting", "MAK_Calculated", "Soll_FTE"):
        assert pd.to_numeric(filtered_target[metric], errors="coerce").sum() == pytest.approx(
            pd.to_numeric(expected_target[metric], errors="coerce").sum())
    cluster_yearly = pd.read_excel(
        filtered_book, filtered_inventory.loc["Jahre_Cluster_vollständig", "Tabellenblatt"])
    pd.testing.assert_frame_equal(
        cluster_yearly.reset_index(drop=True),
        detail.summary["annual_cluster"].reset_index(drop=True),
        check_dtype=False, check_exact=False, atol=1e-9)
    assert not any(name.startswith("MC_") or name.startswith("Ref_")
                   for name in filtered_inventory.index)

    for scenario_label, scenario_choice in (
        ("Geringste Fluktuation", selected["best"]),
        ("Höchste Fluktuation", selected["worst"]),
        ("Typischer Verlauf", selected["typical"]),
    ):
        scenario_app = AppTest.from_string(source.encode("ascii", "backslashreplace").decode(),
                                           default_timeout=180)
        for key, value in saved_state.items():
            scenario_app.session_state[key] = value
        scenario_app.session_state["org_sim_representative_details"] = (
            oe_app.session_state["org_sim_representative_details"])
        scenario_app.session_state["test_oe_scenario_label"] = scenario_label
        scenario_app.run()
        assert not scenario_app.exception
        scenario_book = pd.ExcelFile(BytesIO(scenario_app.session_state["test_oe_export_builder"]()))
        scenario_doc = pd.read_excel(scenario_book, "00_Dokumentation").set_index("Feld")
        assert scenario_doc.loc["Szenarioansicht", "Wert"] == scenario_label
        assert int(scenario_doc.loc["Szenario-Laufindex", "Wert"]) == scenario_choice["index"]
        scenario_inventory = pd.read_excel(scenario_book, "Exportinventar").set_index("Bereich")
        scenario_target = pd.read_excel(
            scenario_book, scenario_inventory.loc["Simulationsbasis_gefiltert", "Tabellenblatt"])
        expected = (saved_state["compact_sim_prepared_df"] if scenario_choice["index"] == 0
                    else detail.prepared)
        assert len(scenario_target) == len(expected)
        assert pd.to_numeric(scenario_target["MAK_Reporting"], errors="coerce").sum() == pytest.approx(
            pd.to_numeric(expected["MAK_Reporting"], errors="coerce").sum())

    # Exercise the actual rerun path in one Streamlit session, where stale
    # widget state or a previous export builder could otherwise survive.
    oe_app.session_state["test_oe_scenario_label"] = "Konsolidiert"
    oe_app.run()
    assert not oe_app.exception
    assert any("Läufe" in item.value and "Mittelwerte" in item.value
               for item in oe_app.info)

    oe_app.session_state["test_oe_scenario_label"] = label
    oe_app.run()
    assert not oe_app.exception
    again_book = pd.ExcelFile(BytesIO(oe_app.session_state["test_oe_export_builder"]()))
    again_inventory = pd.read_excel(again_book, "Exportinventar").set_index("Bereich")
    assert not any(name.startswith("MC_") or name.startswith("Ref_")
                   for name in again_inventory.index)
    again_doc = pd.read_excel(again_book, "00_Dokumentation").set_index("Feld")
    assert again_doc.loc["Szenarioansicht", "Wert"] == label
    assert int(again_doc.loc["Szenario-Laufindex", "Wert"]) == choice["index"]
    for section in ("KPI_Anzeige", "Rangliste", "Jahre_OE_vollständig"):
        pd.testing.assert_frame_equal(
            pd.read_excel(again_book, again_inventory.loc[section, "Tabellenblatt"]),
            pd.read_excel(book, inventory.loc[section, "Tabellenblatt"]),
            check_dtype=False)


def test_compact_plus_two_runs_create_complete_bound_ensemble():
    app = _compact_app(2)
    assert not app.exception
    app.session_state["test_start_simulation"] = True
    app.run()
    assert not app.exception
    assert "monte_carlo_ensemble_result" in app.session_state, (
        [item.value for item in app.error], [item.value for item in app.info],
        [item.value for item in app.success])
    saved = app.session_state["monte_carlo_ensemble_result"]
    assert saved["status"] == "complete"
    assert saved["count"] == 2
    assert saved["statistics"]["annual_stock_values"]
    assert saved["statistics"]["departure_rate_inputs_by_seed"] is not None
    from utils.monte_carlo_filtered_org import (
        SNAPSHOT_COLUMNS, decode_snapshot, filtered_org_statistics)
    filtered_values = filtered_org_statistics(saved, {})
    assert filtered_values[("__gesamt__", "__gesamt__", "heads")]["mean"] >= 0
    assert filtered_values[("__gesamt__", "__gesamt__", "heads")]["lower"] <= filtered_values[("__gesamt__", "__gesamt__", "heads")]["upper"]
    reference_columns = [name for name in SNAPSHOT_COLUMNS
                         if name in app.session_state["compact_sim_prepared_df"]]
    pd.testing.assert_frame_equal(
        decode_snapshot(saved["org_filter_snapshots"][0]),
        app.session_state["compact_sim_prepared_df"].loc[:, reference_columns],
        check_exact=True)
    from utils.monte_carlo_filtered_org import filtered_org_kpi_statistics
    kpi_values = filtered_org_kpi_statistics(saved, {}, _compact_module())
    reference = app.session_state["compact_sim_prepared_df"]
    assigned = reference.loc[reference["Organisationseinheit"].notna() &
                             reference["Organisationseinheit"].ne("Nicht zugeordnet")]
    assert kpi_values["seed_zero"]["heads"] == _compact_module().get_ist_koepfe(assigned)
    assert kpi_values["seed_zero"]["mak"] == _compact_module().get_ist_mak(assigned)
    from utils.monte_carlo_filtered_org import filtered_org_rank_statistics
    rank_values = filtered_org_rank_statistics(saved, {})
    audit_events = app.session_state["compact_sim_audit_tables"].get("Abgaenge_Events_Raw")
    expected_departures = 0.0 if audit_events is None or audit_events.empty else float(
        pd.to_numeric(audit_events.loc[
            pd.to_numeric(audit_events["headcount_change"], errors="coerce") < 0,
            "headcount_change"], errors="coerce").abs().sum())
    assert sum(value["seed_zero"] for value in rank_values["departures"].values()) == expected_departures
    from utils.monte_carlo_filtered_org import filtered_org_composition_statistics
    import importlib.util
    org_page_path = next((ROOT / "pages").glob("9_*_Organisationseinheiten_Analyse.py"))
    org_spec = importlib.util.spec_from_file_location("org_mc_seed_zero_parity", org_page_path)
    org_page = importlib.util.module_from_spec(org_spec)
    org_spec.loader.exec_module(org_page)
    normalized = org_page._normalize_org_column(reference)
    mapped = normalized.loc[normalized["Organisationseinheit"].ne("Nicht zugeordnet")]
    orgs = sorted(mapped["Organisationseinheit"].unique().tolist())
    dimensions = ("Geschlecht", "Alterskohorte", "Beschäftigungsstatus")
    for metric in ("Köpfe", "MAK"):
        compositions = filtered_org_composition_statistics(
            saved, {}, orgs, metric, dimensions)
        config = org_page._get_metric_config(mapped, metric)
        for dimension in dimensions:
            legacy = org_page._aggregate_org_split(mapped, dimension, metric, config, orgs)
            observed = compositions[dimension]["segments"]
            legacy_values = {(str(org), str(group)): float(value)
                             for org, group, value in legacy[["Organisationseinheit", dimension, "Wert"]]
                             .itertuples(index=False, name=None)}
            for org, group, value in observed[
                    ["Organisationseinheit", dimension, "Seed 0"]].itertuples(
                        index=False, name=None):
                assert float(value) == legacy_values.get((str(org), str(group)), 0.0)
    from components.org_simulation_yearly import build_yearly_view, yearly_periods
    from utils.monte_carlo_org_view import (
        event_band_table, event_detail_band_table, event_summary_band_table, stock_band_table)
    summary = app.session_state["compact_sim_event_summary"]
    from utils.monte_carlo_org_view import annual_headline_statistics
    from components.org_simulation_yearly import build_yearly_headline_metrics
    for level, source_key in (("Organisationseinheit", "annual_oe"),
                              ("OE-Cluster", "annual_cluster")):
        source = summary[source_key]
        from components.org_simulation_yearly import build_yearly_summary_table
        for event_metric in ("Abgänge", "Zugänge", "Köpfe-Veränderung", "MAK-Veränderung"):
            view = build_yearly_view(
                summary, dimension=level,
                years=yearly_periods(summary["metadata"])["Jahr"].tolist(),
                organizations=sorted(source[level].unique().tolist()),
                event_types=sorted(source["Ereignisart"].unique().tolist()), metric=event_metric)
            assert not event_band_table(view, ensemble=saved).empty
            view["summary_table"] = build_yearly_summary_table(view)
            event_details = event_detail_band_table(view, ensemble=saved)
            event_summary = event_summary_band_table(view, event_details, ensemble=saved)
            assert not event_details.empty
            assert event_summary.index.tolist() == view["table"].index.tolist()
        annual = summary["stock_reconciliation"]["annual"]
        stock = annual.loc[annual["Ebene"].eq(level)].drop_duplicates(
            ["Organisation", "Stichtag"], keep="last")
        selected_org = [stock["Organisation"].iloc[0]]
        for stock_metric in ("Köpfe", "MAK"):
            reference_headline = build_yearly_headline_metrics(
                summary, dimension=level, organizations=selected_org,
                end_year=max(yearly_periods(summary["metadata"])["Jahr"]),
                metric=stock_metric)
            stats = annual_headline_statistics(
                summary, ensemble=saved, dimension=level,
                organizations=selected_org,
                end_year=max(yearly_periods(summary["metadata"])["Jahr"]),
                metric=stock_metric)
            assert stats is not None
            assert stats["initial"]["mean"] == reference_headline["initial"]
        for stock_metric in ("Köpfe", "MAK"):
            stock_chart = stock[["Stichtag", "Organisation", f"Bestand-{stock_metric}"]].copy()
            if stock_metric == "MAK":
                stock_chart[f"Bestand-{stock_metric}"] = stock_chart[f"Bestand-{stock_metric}"].round(2)
            bands = stock_band_table(
                stock_chart, ensemble=saved, level=level, metric=stock_metric,
                base_date=summary["metadata"]["base_date"])
            assert len(bands) == len(stock_chart)
            assert bands["Referenzlauf"].tolist() == stock_chart[f"Bestand-{stock_metric}"].tolist()
            assert bands["lower"].le(bands["mean"]).all()
            assert bands["mean"].le(bands["upper"]).all()
    chart_values = {(metric, dimension, group): values for metric, dimension, group, values
                    in saved["chart_statistics"]["values"]}
    reference_chart_values = extract_ist_chart_values(
        app.session_state["compact_sim_prepared_df"], _compact_module())
    assert reference_chart_values
    for metric, dimension, group, value in reference_chart_values:
        assert chart_values[metric, dimension, group]["samples"][0] == value
    workbook = load_workbook(BytesIO(build_monte_carlo_export_bytes(
        ensemble=saved, base_date="2026-09-01", target_date="2026-12-31")),
        read_only=True, data_only=True)
    assert list(workbook["Zielwerte"].values)[1][2:] == tuple(
        saved["statistics"]["target"]["heads"][key] for key in ("mean", "lower", "upper"))
    assert workbook["Jahresereignisse"].max_row == len(saved["statistics"]["annual_values"]) + 1
    assert workbook["Jahresbestaende"].max_row == len(saved["statistics"]["annual_stock_values"]) + 1
    assert any("Ungefilterter Zielbestand aller Läufe" in item.value for item in app.markdown)
    assert any("Vergütung bleibt ohne Fehlerbänder" in item.value for item in app.caption)
    assert any("Zu- und Abgangszahlen stammen aus dem konkreten Referenzlauf" in item.value for item in app.caption)
    assert any(button.label == "Referenzlauf als Excel exportieren" for button in app.button)
    assert any(button.label == "Monte-Carlo-Kennzahlen als Excel exportieren" for button in app.button)
    app.session_state["test_start_simulation"] = False
    before_instance = app.session_state["simulation_validation_instance"]
    next(button for button in app.button if button.label == "Monte-Carlo-Kennzahlen als Excel exportieren").click().run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert app.session_state["compact_plus_monte_carlo_export__lazy_payload"][:2] == b"PK"
    app.run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert app.session_state["monte_carlo_ensemble_result"]["plan_signature"] == saved["plan_signature"]
    app.date_input(key="compact_sim_target_date").set_value("2027-06-15").run()
    assert not app.exception
    assert str(app.session_state["compact_sim_target_date_cached"].date()) == "2027-06-15"
    assert app.session_state["monte_carlo_ensemble_result"]["status"] == "complete"
    assert app.session_state["monte_carlo_ensemble_result"]["plan_signature"] != saved["plan_signature"]
    assert app.session_state["simulation_validation_instance"] != before_instance
    before_instance = app.session_state["simulation_validation_instance"]
    settings = dict(app.session_state["simulation_params"])
    settings["compact_plus"] = {**settings["compact_plus"], "run_count": 10}
    app.session_state["simulation_params"] = settings
    app.run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert any("noch nicht berechnet" in item.value for item in app.info)
    assert not any(button.label == "Monte-Carlo-Kennzahlen als Excel exportieren" for button in app.button)
