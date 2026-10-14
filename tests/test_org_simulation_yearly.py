from copy import deepcopy

import pandas as pd
import pytest

from components import org_simulation_yearly as ui
from utils.org_simulation_events import build_simulation_event_summary
from test_org_simulation_events import _context


def _summary():
    context = _context()
    context["metadata"] = {"base_date": "2026-03-01", "target_date": "2028-09-30"}
    return build_simulation_event_summary(context, expected_run_signature="a")


def test_periods_include_empty_year_and_identify_partial_years():
    periods = ui.yearly_periods(_summary()["metadata"])
    assert periods.Jahr.tolist() == [2026, 2027, 2028]
    assert periods.Teiljahr.tolist() == [True, False, True]
    assert periods.iloc[-1].Bis == pd.Timestamp("2028-09-30")


def test_headline_metrics_use_full_cumulative_period_and_distinguish_atz_effects():
    annual = pd.DataFrame([
        {"Ebene": "Organisationseinheit", "Organisation": org, "Art": art, "Jahr": year,
         "Bestand-Köpfe": heads, "Bestand-MAK": mak}
        for org, values in {
            "A": [("Ausgangsstichtag", 2025, 10, 8.0), ("Jahresstichtag", 2026, 10, 7.0),
                  ("Jahresstichtag", 2027, 9, 7.0)],
            "B": [("Ausgangsstichtag", 2025, 5, 4.0), ("Jahresstichtag", 2026, 4, 3.5),
                  ("Jahresstichtag", 2027, 4, 3.5)],
        }.items() for art, year, heads, mak in values
    ])
    events = pd.DataFrame({
        "Organisationseinheit": ["A", "A", "B", "B"],
        "Jahr": [2026, 2027, 2026, 2028],
        "Ereignisart": ["ATZ_AR_TO_FR", "ATZ_END", "QUIT", "RETIREMENT"],
        "Köpfe-Veränderung": [0, -1, -1, -1],
        "MAK-Veränderung": [-1.0, 0.0, -0.5, -1.0],
    })
    summary = {"stock_reconciliation": {"status": "Abgestimmt", "annual": annual,
                                         "reporting_events": events}}
    selected = {"dimension": "Organisationseinheit", "organizations": ["A", "B"], "end_year": 2027}
    heads = ui.build_yearly_headline_metrics(summary, metric="Köpfe", **selected)
    mak = ui.build_yearly_headline_metrics(summary, metric="MAK", **selected)
    assert heads["initial"] == 15 and heads["final"] == 13
    assert heads["retirement_atz"] == 1 and heads["quit"] == 1
    assert heads["departure_share"] == pytest.approx(2 / 15)
    assert mak["initial"] == 12 and mak["final"] == 10.5
    assert mak["retirement_atz"] == 1 and mak["quit"] == 0.5
    assert mak["departure_share"] == pytest.approx(1.5 / 12)
    assert ui.build_yearly_headline_metrics(summary, metric="Köpfe", **{**selected, "organizations": ["A"]})["quit"] == 0
    summary["stock_reconciliation"]["status"] = "Abweichung"
    assert ui.build_yearly_headline_metrics(summary, metric="Köpfe", **selected) is None


@pytest.mark.parametrize("dimension,organizations", [("Organisationseinheit", ["OE1", "OE2", "OE3"]),
                                                       ("OE-Cluster", ["A", "Nicht zugeordnet"])])
def test_view_reconciles_to_saved_summary_and_fills_zero_year(dimension, organizations):
    saved = _summary()
    view = ui.build_yearly_view(saved, dimension=dimension, years=[2026, 2027, 2028],
                                organizations=organizations, event_types=saved["events"].Ereignisart.unique().tolist(), metric="Abgänge")
    assert view["table"].sum().sum() == saved["events"]["Abgänge"].sum()
    assert view["table"][2028].sum() == 0
    assert view["run_signature"] == "a"


def test_selection_filters_year_organization_and_event_type_without_mutation():
    saved = _summary()
    before = deepcopy(saved)
    view = ui.build_yearly_view(saved, dimension="OE-Cluster", years=[2026], organizations=["A"],
                                event_types=["QUIT"], metric="MAK-Veränderung")
    assert view["table"].loc["A", 2026] == -0.5
    assert view["details"].Ereignisart.tolist() == ["QUIT"]
    pd.testing.assert_frame_equal(saved["annual_cluster"], before["annual_cluster"], check_exact=True)


def test_multiple_departure_reasons_stack_to_same_yearly_total():
    saved = _summary()
    types = sorted(set(saved["events"].Ereignisart) & {"QUIT", "RETIREMENT", "ATZ_END"})
    view = ui.build_yearly_view(saved, dimension="Organisationseinheit", years=[2026, 2027],
                                organizations=["OE1", "OE2", "OE3"], event_types=types, metric="Abgänge")
    stacked = ui.build_stacked_event_chart(view)
    assert stacked["Abgänge"].sum() == view["chart"]["Abgänge"].sum()
    summary_table = ui.build_yearly_summary_table(view)
    assert summary_table["Summe ausgewählte Kennzahl"].sum() == view["chart"]["Abgänge"].sum()
    assert summary_table["Abgehende Köpfe"].sum() == view["details"]["Abgänge"].sum()
    from utils.org_simulation_export import append_yearly_export
    view["summary_table"] = summary_table
    view["stacked_chart"] = stacked
    tables, context = {}, {"Laufkennung": "a"}
    append_yearly_export(tables, context, saved, view)
    pd.testing.assert_frame_equal(tables["Jahresauswahl_Summen"], summary_table.reset_index())
    pd.testing.assert_frame_equal(tables["Jahresauswahl_Grafik"], stacked)


@pytest.mark.parametrize("summary,signature", [(None, "a"), (_summary(), "wrong"),
                                               ({"run_signature": "a", "metadata": {}}, "a")])
def test_missing_or_mismatched_summary_shows_message_without_controls(monkeypatch, summary, signature):
    messages = []
    for method in ("subheader", "info", "warning"):
        monkeypatch.setattr(ui.st, method, lambda message: messages.append(message))
    monkeypatch.setattr(ui.st, "radio", lambda *args, **kwargs: pytest.fail("No controls for invalid results"))
    assert ui.render_org_simulation_yearly(summary, run_signature=signature) is None
    assert len(messages) == 2


def test_renderer_uses_saved_cluster_tables(monkeypatch):
    saved = _summary()
    rendered = []
    class Context:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(ui.st, "subheader", lambda *args: None)
    monkeypatch.setattr(ui.st, "caption", lambda *args: None)
    monkeypatch.setattr(ui.st, "radio", lambda label, options, **kwargs: "OE-Cluster" if label == "Organisationsebene" else "Abgänge")
    monkeypatch.setattr(ui.st, "multiselect", lambda label, options, **kwargs: kwargs["default"])
    monkeypatch.setattr(ui.st, "bar_chart", lambda df, **kwargs: rendered.append(df.copy()))
    monkeypatch.setattr(ui.st, "altair_chart", lambda chart, **kwargs: rendered.append(chart.to_dict()))
    monkeypatch.setattr(ui.st, "dataframe", lambda df, **kwargs: rendered.append(df.copy()))
    monkeypatch.setattr(ui.st, "expander", lambda *args: Context())
    view = ui.render_org_simulation_yearly(saved, run_signature="a")
    assert view["dimension"] == "OE-Cluster"
    assert len(rendered) == 3
    assert view["table"].sum().sum() == 4
    assert view["summary_table"]["Summe ausgewählte Kennzahl"].sum() == 4


def test_streamlit_controls_render_and_switch_levels_without_simulation():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from test_org_simulation_yearly import _summary
from components.org_simulation_yearly import render_org_simulation_yearly
render_org_simulation_yearly(_summary(), run_signature="a")
''').run()
    assert not app.exception
    assert len(app.dataframe) == 2
    app.radio[0].set_value("OE-Cluster").run()
    assert not app.exception
    assert app.dataframe[0].value["Summe ausgewählte Kennzahl"].sum() == 4
    app.radio(key="org_yearly_metric").set_value("MAK-Veränderung").run()
    assert not app.exception
    assert app.dataframe[0].value["Summe ausgewählte Kennzahl"].sum() == pytest.approx(-0.75)


def test_incompatible_cluster_mapping_warns_without_guessing_a_source():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from test_org_simulation_yearly import _summary
from components.org_simulation_yearly import render_org_simulation_yearly
saved = _summary()
saved["events"]["OE-Cluster"] = "Nicht zugeordnet"
render_org_simulation_yearly(saved, run_signature="a")
''').run()
    assert not app.exception
    assert len(app.warning) == 1
    assert "passt zu keiner" in app.warning[0].value
