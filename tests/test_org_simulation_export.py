from copy import deepcopy
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import subprocess
import types

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_analysis_pages_contracts import _FakeCompact, _analysis_snapshot, _capture_render_helpers
from utils.compact_page_loader import load_compact_page_module
from utils.org_simulation_export import build_org_simulation_export_bytes, build_plan_comparison, export_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def load_page():
    path = next((ROOT / "pages").glob("9_*"))
    spec = importlib.util.spec_from_file_location("org_export_page", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def original_page():
    source = subprocess.check_output(["git", "show", "ee8425e:pages/9_🏢_Organisationseinheiten_Analyse.py"], cwd=ROOT).decode("utf-8-sig")
    module = types.ModuleType("original_org_page")
    module.__file__ = str(next((ROOT / "pages").glob("9_*")))
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("metric", ["MAK", "Köpfe"])
@pytest.mark.parametrize("comparison", [False, True])
def test_org_export_matches_rendered_tables(monkeypatch, metric, comparison):
    snapshot = _analysis_snapshot()
    previous = snapshot.copy()
    snapshot.loc[0, "MAK_Reporting"] = .6
    outputs = []
    collected = {}
    for module, exporting in ((original_page(), False), (load_page(), True)):
        compact = _FakeCompact()
        capture, displayed, downloads = _capture_render_helpers(monkeypatch, module)
        config = module._get_metric_config(snapshot, metric)
        args = (snapshot, "Geschlecht", "Geschlecht", metric, config, compact)
        kwargs = {"key_prefix": "gender", "display_orgs": ["OE B", "OE A"], "value_label": "Simulation",
                  "comparison_mapped_df": previous, "comparison_active": comparison}
        module._render_org_split_block(*args, **kwargs, **({"export_tables": collected} if exporting else {}))
        if comparison:
            module._render_org_rangliste_comparison(snapshot, previous, metric, config, compact, ["OE B", "OE A"],
                value_label="Simulation", comparison_label="IST", **({"export_tables": collected} if exporting else {}))
        else:
            module._render_org_rangliste(snapshot, metric, config, compact, ["OE B", "OE A"],
                value_label="Simulation", **({"export_tables": collected} if exporting else {}))
        outputs.append((capture.figures, displayed, compact.exports))
    for original, current in zip(outputs[0][0], outputs[1][0]):
        assert original.to_json() == current.to_json()
    for original, current in zip(outputs[0][1], outputs[1][1]):
        pd.testing.assert_frame_equal(original["data"], current["data"], check_exact=True)
    for original, current in zip(outputs[0][2], outputs[1][2]):
        pd.testing.assert_frame_equal(original["df"], current["df"], check_exact=True)
    assert len(collected) == 3
    if comparison:
        pd.testing.assert_frame_equal(collected["Rangliste_Vergleich"], outputs[1][2][1]["df"])
        assert {"IST", "Simulation", "Delta", "Delta %"}.issubset(collected["Geschlecht_Tabelle"])
    else:
        pd.testing.assert_frame_equal(collected["Geschlecht_Tabelle"], outputs[1][2][0]["df"].reset_index())


def test_plan_comparison_uses_saved_baseline():
    compact = load_compact_page_module()
    initial = _analysis_snapshot().assign(Soll_FTE=[1., .5, 1., 1.], MAK_Calculated=[1., .5, .75, 0.])
    future = initial[initial["Organisationseinheit"] == "OE A"].copy()
    future.loc[future.index[0], ["MAK_Calculated", "MAK_Reporting"]] = .25
    before = initial.copy(deep=True)
    result = build_plan_comparison(future, initial, ["OE B", "OE A"], compact).set_index("Organisationseinheit")
    assert result.loc["OE B", "SOLL-MAK Ausgangsstellenplan"] == 2.
    assert result.loc["OE B", "Simulation-Köpfe"] == 0
    assert result.loc["OE A", "SOLL-MAK Ausgangsstellenplan"] == 1.5
    assert result.loc["OE A", "Simulation-MAK"] == compact.get_ist_mak(future)
    assert result.loc["OE A", "Simulation minus SOLL (MAK)"] == compact.get_ist_mak(future) - 1.5
    pd.testing.assert_frame_equal(initial, before)
    assert build_plan_comparison(future, None, ["OE A"], compact).empty
    missing = build_plan_comparison(future, initial.drop(columns="Soll_FTE"), ["OE A"], compact)
    assert missing["SOLL-MAK Ausgangsstellenplan"].isna().all()


@pytest.mark.parametrize("metric", ["MAK", "Köpfe"])
@pytest.mark.parametrize("comparison", [False, True])
def test_role_summary_and_tariff_plot_remain_equivalent(monkeypatch, metric, comparison):
    snapshot = _analysis_snapshot()
    collected = {}
    results = []
    for module, exporting in ((original_page(), False), (load_page(), True)):
        compact = _FakeCompact()
        capture, displayed, downloads = _capture_render_helpers(monkeypatch, module)
        module._render_role_breakdown_block(snapshot, metric, compact, ["OE B", "OE A"],
            comparison_mapped_df=snapshot, comparison_active=comparison, value_label="Simulation",
            **({"export_tables": collected} if exporting else {}))
        results.append((capture.figures, displayed))
    for old, new in zip(results[0][0], results[1][0]):
        assert old.to_json() == new.to_json()
    for old, new in zip(results[0][1], results[1][1]):
        pd.testing.assert_frame_equal(old["data"], new["data"], check_exact=True)
    assert collected["Köpfe_Kapazität"]["MAK"].tolist() == [.75, 1.5]
    assert {"Tarifgruppe_Grafik", "Tarifgruppe_Tabelle"}.issubset(collected)


def test_multiple_positions_are_not_counted_as_multiple_people():
    compact = load_compact_page_module()
    initial = pd.concat([_analysis_snapshot().iloc[[0]]] * 2, ignore_index=True)
    initial[["Soll_FTE", "MAK_Reporting", "MAK_Calculated"]] = .5
    result = build_plan_comparison(initial, initial, ["OE A"], compact).iloc[0]
    assert result["IST-Köpfe"] == 1
    assert result["Ausgangs-Planstellenzeilen"] == 2
    assert result["SOLL-MAK Ausgangsstellenplan"] == 1.


def test_fingerprint_detects_middle_row_and_every_view_setting():
    tables = {"large": pd.DataFrame({"value": range(1000)})}
    context = {"Lauf": {"target_date": "2028-01-01"}, "Anzeige": {"Top-N": "8", "IST-Vergleich": False}}
    before = export_fingerprint(tables, context)
    changed = {"large": tables["large"].copy()}
    changed["large"].loc[501, "value"] = -1
    assert export_fingerprint(changed, context) != before
    precise = {"float": pd.DataFrame({"value": [1.0000000000000002]})}
    assert export_fingerprint(precise, context) != export_fingerprint({"float": pd.DataFrame({"value": [1.]})}, context)
    for name, value in (("Top-N", "Alle"), ("IST-Vergleich", True)):
        other = deepcopy(context)
        other["Anzeige"][name] = value
        assert export_fingerprint(tables, other) != before


def test_simulation_wrapper_freezes_saved_parameters_not_current_draft(monkeypatch):
    path = next((ROOT / "pages").glob("10_*"))
    spec = importlib.util.spec_from_file_location("org_sim_export_wrapper", path)
    page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(page)
    state = {"compact_sim_prepared_df": _analysis_snapshot(),
             "compact_sim_status_quo_df": _analysis_snapshot(),
             "compact_sim_metadata": {"target_date": "2028-01-01"}, "compact_sim_signature": "saved-run",
             "compact_sim_abgaenge_params": {"retirement": {"retirement_age": 67}},
             "simulation_params": {"abgaenge": {"retirement": {"retirement_age": 70}}}}
    captured = {}
    monkeypatch.setattr(st, "session_state", state)
    monkeypatch.setattr(page, "_load_orgunit_analysis_module", lambda: types.SimpleNamespace(
        render_orgunit_analysis_page=lambda *args, **kwargs: captured.update(kwargs)))
    page.main()
    frozen = captured["export_context"]
    assert frozen["abgaenge_params"]["retirement"]["retirement_age"] == 67
    assert frozen["Laufkennung"] == "saved-run"
    state["compact_sim_abgaenge_params"]["retirement"]["retirement_age"] = 68
    assert frozen["abgaenge_params"]["retirement"]["retirement_age"] == 67


def test_workbook_contains_all_tables_parameters_and_three_lineage_sheets():
    tables = {"Rangliste": pd.DataFrame({"Organisationseinheit": ["OE A"], "Simulation": [.25]}),
              "IST_Simulation_SOLL": pd.DataFrame({"SOLL-MAK": [1.5], "Simulation-MAK": [.25]})}
    context = {"Lauf": {"base_date": "2026-01-01", "target_date": "2028-01-01"},
               "abgaenge_params": {"retirement": {"retirement_age": 67}},
               "Anzeige": {"Kennzahl": "MAK", "IST-Vergleich": True}}
    payload = build_org_simulation_export_bytes(tables, context)
    book = pd.ExcelFile(BytesIO(payload))
    assert {"Lineage_Report", "Input_Lineage", "Transformations_Lineage", "Parameter", "Exportinventar"}.issubset(book.sheet_names)
    inventory = pd.read_excel(book, "Exportinventar")
    for item in inventory.to_dict("records"):
        actual = pd.read_excel(book, item["Tabellenblatt"])
        pd.testing.assert_frame_equal(actual, tables[item["Bereich"]], check_dtype=False)
    params = pd.read_excel(book, "Parameter").set_index("Parameter")
    assert str(params.loc["retirement.retirement_age", "Wert"]) == "67"
    lineage = pd.read_excel(book, "Lineage_Report")
    assert "10-08" in lineage.astype(str).to_numpy()


def make_app():
    source = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(ROOT / 'tests')!r})
import pandas as pd
import streamlit as st
from test_org_simulation_export import load_page
from test_analysis_pages_contracts import _analysis_snapshot
from test_org_simulation_yearly import _summary
page = load_page()
yearly = _summary() if st.session_state.get("test_yearly") else None
initial = _analysis_snapshot().assign(Soll_FTE=[1., .5, 1., 1.], MAK_Calculated=[1., .5, .75, 0.],
    BsGrd=[100., 50., 75., 0.], Sollarbeitszeit=39., ATZ_Status="Kein ATZ", Alter_Jahre=40.)
future = initial.iloc[:2].copy()
future.loc[0, ["MAK_Reporting", "MAK_Calculated"]] = .25
if st.session_state.get("empty"):
    future = future.iloc[:0]
page.render_global_filters = lambda *args: None
page.apply_filters = lambda df: df.copy()
page.get_global_metric_view = lambda: st.session_state.get("test_metric", "MAK")
page.get_filter_summary = lambda: "Testfilter"
page.get_active_view_filters = lambda: {{"test_metric": st.session_state.get("test_metric", "MAK")}}
import components.ui_compat as compat
def download(**kwargs):
    if kwargs["key"] == "org_simulation_full_export":
        st.session_state["test_builder"] = kwargs["data_builder"]
        st.session_state["test_fingerprint"] = kwargs["fingerprint"]
from unittest.mock import patch
with patch.object(compat, "lazy_excel_download_button_compat", download):
    page.render_orgunit_analysis_page(future, None, value_label="Simulation", comparison_df=initial,
        enable_comparison_toggle=True, event_summary=yearly, export_context={{"Laufkennung": "a", "Lauf": {{"target_date": "2028-01-01"}},
        "abgaenge_params": {{"retirement": {{"retirement_age": 67}}}}}})
'''
    return AppTest.from_string(source.encode("ascii", "backslashreplace").decode(), default_timeout=40).run()


@pytest.mark.parametrize("metric", ["MAK", "Köpfe"])
def test_real_page_full_export_uses_current_view_and_saved_run(metric):
    at = make_app()
    at.session_state["test_metric"] = metric
    at.run()
    assert not at.exception
    before = at.session_state["test_fingerprint"]
    at.toggle(key="orgunit_analysis_show_comparison").set_value(True).run()
    assert not at.exception
    assert at.session_state["test_fingerprint"] != before
    payload = at.session_state["test_builder"]()
    book = pd.ExcelFile(BytesIO(payload))
    inventory = pd.read_excel(book, "Exportinventar").set_index("Bereich")
    assert {"KPI_Anzeige", "Rangliste_Vergleich", "Geschlecht_Grafik", "Alterskohorten_Tabelle",
            "Beschäftigungsstatus_Tabelle", "Tarifgruppe_Tabelle", "Köpfe_Kapazität", "Datenqualität",
            "IST_Simulation_SOLL", "Ausgangsbasis_gefiltert", "Simulationsbasis_gefiltert"}.issubset(inventory.index)
    plan = pd.read_excel(book, inventory.loc["IST_Simulation_SOLL", "Tabellenblatt"])
    assert plan["SOLL-MAK Ausgangsstellenplan"].sum() == 3.5
    assert "OE B" in plan["Organisationseinheit"].tolist()
    at.radio(key="orgunit_analysis_top_n").set_value("Alle").run()
    assert not at.exception
    at.selectbox(key="orgunit_analysis_min_size").set_value("mind. 10 Köpfe").run()
    assert not at.exception
    restricted = pd.ExcelFile(BytesIO(at.session_state["test_builder"]()))
    restricted_inventory = pd.read_excel(restricted, "Exportinventar").set_index("Bereich")
    assert int(restricted_inventory.loc["IST_Simulation_SOLL", "Zeilen"]) == 0
    at.selectbox(key="orgunit_analysis_min_size").set_value("Alle").run()
    at.session_state["empty"] = True
    at.run()
    assert not at.exception
    empty_book = pd.ExcelFile(BytesIO(at.session_state["test_builder"]()))
    assert "Exportinventar" in empty_book.sheet_names
