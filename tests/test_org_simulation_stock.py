from io import BytesIO

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from test_org_simulation_reconciliation import _bridge_context
from utils.org_simulation_events import build_simulation_event_summary
from utils.org_simulation_export import append_stock_export, build_org_simulation_export_bytes


def _app(summary):
    app = AppTest.from_string('''
import streamlit as st
from components.org_simulation_stock import render_departure_stock
st.session_state["view"] = render_departure_stock(st.session_state["summary"], run_signature="a")
''')
    app.session_state["summary"] = summary
    return app.run()


def test_stock_controls_use_saved_result_and_show_matching_endpoint():
    summary = build_simulation_event_summary(_bridge_context("no_events"), expected_run_signature="a")
    app = _app(summary)
    assert not app.exception
    assert len(app.success) == 1
    app.radio(key="org_stock_metric").set_value("MAK").run()
    assert app.dataframe[0].value.iloc[0, -1] == 5
    app.radio(key="org_stock_level").set_value("OE-Cluster").run()
    assert not app.exception
    view = app.session_state["view"]
    assert view["table"].iloc[:, -1].sum() == 5
    assert not view["chart"].duplicated(["Stichtag", "Organisation"]).any()


@pytest.mark.parametrize("mode", ["mismatch", "active", "discrepancy"])
def test_invalid_stock_views_never_show_certified_chart(mode):
    context = _bridge_context("no_events")
    if mode == "active":
        context["zugaenge_params"]["new_hires"]["active"] = True
    if mode == "discrepancy":
        context["final_snapshot"].loc[0, "MAK_Reporting"] = 0.5
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    if mode == "mismatch":
        summary["run_signature"] = "other"
    app = _app(summary)
    assert not app.exception
    assert not app.radio
    assert not app.success
    assert app.session_state["view"] is None
    if mode == "discrepancy":
        assert len(app.warning) == 1
        assert len(app.dataframe) == 2


def test_stock_export_matches_ui_and_documents_full_checks():
    summary = build_simulation_event_summary(_bridge_context("atz_ruhend"), expected_run_signature="a")
    app = _app(summary)
    assert not app.exception
    view = app.session_state["view"]
    tables, context = {}, {"Laufkennung": "a", "Lauf": summary["metadata"]}
    append_stock_export(tables, context, summary, view)
    pd.testing.assert_frame_equal(tables["Bestand_Auswahl_Grafik"], view["chart"], check_exact=True)
    pd.testing.assert_frame_equal(tables["Bestand_Auswahl_Matrix"], view["table"].reset_index(), check_exact=True)
    book = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(tables, context)))
    inventory = pd.read_excel(book, "Exportinventar").set_index("Bereich")
    checks = pd.read_excel(book, inventory.loc["Bestand_Endwertabgleich", "Tabellenblatt"])
    assert checks.Abgestimmt.all()
    chart = pd.read_excel(book, inventory.loc["Bestand_Auswahl_Grafik", "Tabellenblatt"])
    pd.testing.assert_frame_equal(chart, view["chart"].reset_index(drop=True), check_dtype=False)


def test_export_with_discrepancy_contains_checks_but_no_stock_curve():
    context = _bridge_context("no_events")
    context["final_snapshot"].loc[0, "MAK_Reporting"] = 0.5
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    tables, documentation = {}, {"Laufkennung": "a"}
    append_stock_export(tables, documentation, summary, None)
    assert {"Bestand_Endwertabgleich", "Bestand_Personendiagnose", "Bestand_Ausgangsbasis_Lauf", "Bestand_Endbasis_Lauf",
            "Bestand_Prognosebasiseffekte", "Bestand_Reportingereignisse"} == set(tables)
    assert documentation["Bestandsüberleitung"]["Status"] == "Abweichung"


def test_stock_export_rejects_mismatched_run():
    summary = build_simulation_event_summary(_bridge_context("no_events"), expected_run_signature="a")
    tables, context = {}, {"Laufkennung": "other"}
    append_stock_export(tables, context, summary, None)
    assert tables == {}


def test_integrated_yearly_view_shows_every_year_for_oe_and_cluster():
    summary = build_simulation_event_summary(_bridge_context("no_events"), expected_run_signature="a")
    app = AppTest.from_string('''
import streamlit as st
from components.org_simulation_yearly import render_org_simulation_yearly
st.session_state["combined_view"] = render_org_simulation_yearly(st.session_state["summary"], run_signature="a")
''')
    app.session_state["summary"] = summary
    app.run()
    app.radio(key="org_yearly_mode").set_value("Verbleibender Bestand").run()
    assert not app.exception
    view = app.session_state["combined_view"]["stock_view"]
    assert view["level"] == "Organisationseinheit"
    assert view["chart"].Stichtag.dt.year.unique().tolist() == [2025, 2026, 2027]
    assert view["table"].iloc[:, -1].sum() == 6
    app.radio(key="org_yearly_level").set_value("OE-Cluster").run()
    app.radio(key="org_stock_metric").set_value("MAK").run()
    assert not app.exception
    view = app.session_state["combined_view"]["stock_view"]
    assert view["level"] == "OE-Cluster"
    assert len(view["table"].columns) == 3
    assert view["table"].iloc[:, -1].sum() == 5
    next(w for w in app.multiselect if w.label == "Jahre").set_value([2026]).run()
    assert not app.exception
    view = app.session_state["combined_view"]["stock_view"]
    assert view["years"] == [2026]
    assert view["chart"].Stichtag.dt.year.unique().tolist() == [2025, 2026]
    tables, context = {}, {"Laufkennung": "a"}
    append_stock_export(tables, context, summary, view)
    assert context["Bestandsüberleitung"]["Auswahl"]["years"] == [2026]
    pd.testing.assert_frame_equal(tables["Bestand_Auswahl_Grafik"], view["chart"], check_exact=True)
