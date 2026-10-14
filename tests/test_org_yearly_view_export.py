from io import BytesIO

import pandas as pd
import pytest

from test_org_simulation_stock import _app
from test_org_simulation_reconciliation import _bridge_context
from components.org_simulation_yearly import build_yearly_view
from utils.org_simulation_events import build_simulation_event_summary
from utils.org_simulation_export import build_org_simulation_export_bytes, export_fingerprint
from utils.org_yearly_view_export import prepare_yearly_view_export


@pytest.mark.parametrize("level,metric", [("Organisationseinheit", "Köpfe"), ("OE-Cluster", "MAK")])
def test_selected_stock_workbook_equals_visible_selection(level, metric):
    summary = build_simulation_event_summary(_bridge_context("atz_ruhend"), expected_run_signature="a")
    app = _app(summary)
    app.radio(key="org_stock_level").set_value(level).run()
    app.radio(key="org_stock_metric").set_value(metric).run()
    assert not app.exception
    assert any(button.label == "Excel exportieren" for button in app.button)
    view = app.session_state["view"]
    tables, context = prepare_yearly_view_export(summary, view, stock=True)
    before = export_fingerprint(tables, context)
    book = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(tables, context)))
    inventory = pd.read_excel(book, "Exportinventar").set_index("Bereich")
    actual = pd.read_excel(book, inventory.loc["Bestand_Auswahl_Grafik", "Tabellenblatt"])
    pd.testing.assert_frame_equal(actual, view["chart"].reset_index(drop=True), check_dtype=False)
    assert {"Lineage_Report", "Transformations_Lineage", "Input_Lineage"}.issubset(book.sheet_names)
    assert export_fingerprint(tables, context) == before
    changed = dict(view, metric="MAK" if metric == "Köpfe" else "Köpfe")
    assert export_fingerprint(*prepare_yearly_view_export(summary, changed, stock=True)) != before


def test_event_export_preserves_year_org_and_type_selection():
    summary = build_simulation_event_summary(_bridge_context("atz_ruhend"), expected_run_signature="a")
    source = summary["annual_oe"]
    view = build_yearly_view(summary, dimension="Organisationseinheit", years=[int(source.Jahr.iloc[0])],
                             organizations=[source.Organisationseinheit.iloc[0]],
                             event_types=[source.Ereignisart.iloc[0]], metric="MAK-Veränderung")
    tables, context = prepare_yearly_view_export(summary, view)
    pd.testing.assert_frame_equal(tables["Jahresauswahl_Grafik"], view["chart"], check_exact=True)
    assert context["Jahresauswertung"]["Auswahl"]["Ereignisarten"] == view["event_types"]
    with pytest.raises(ValueError):
        prepare_yearly_view_export(summary, dict(view, run_signature="wrong"))


@pytest.mark.parametrize("dimension,metric", [("Organisationseinheit", "Köpfe"), ("OE-Cluster", "MAK")])
def test_individual_departure_matrices_match_existing_yearly_aggregation(dimension, metric):
    summary = build_simulation_event_summary(_bridge_context("atz_ruhend"), expected_run_signature="a")
    original = summary["events"].copy(deep=True)
    events = original.loc[original.Ereignisquelle.eq("departure_events")]
    event_types = sorted(events.Ereignisart.unique())[:2]
    source = summary["annual_oe" if dimension == "Organisationseinheit" else "annual_cluster"]
    organizations = sorted(source[dimension].unique())
    years = sorted(source.Jahr.unique())
    view = {"run_signature": "a", "level": dimension, "metric": metric,
            "organizations": organizations, "years": years, "event_types": event_types,
            "chart": pd.DataFrame(), "table": pd.DataFrame()}
    tables, context = prepare_yearly_view_export(summary, view, stock=True)
    inventory = tables["Abgangsmatrizen_Verzeichnis"]
    assert inventory.Ereignisart.tolist() == event_types
    book = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(tables, context)))
    sheets = pd.read_excel(book, "Exportinventar").set_index("Bereich")
    for row in inventory.itertuples(index=False):
        expected = build_yearly_view(summary, dimension=dimension, years=years,
                                    organizations=organizations, event_types=[row.Ereignisart],
                                    metric=f"{metric}-Veränderung")["table"].reset_index()
        pd.testing.assert_frame_equal(tables[row.Bereich], expected, check_exact=True)
        actual = pd.read_excel(book, sheets.loc[row.Bereich, "Tabellenblatt"])
        pd.testing.assert_frame_equal(actual, expected.rename_axis(None, axis=1), check_dtype=False)
    pd.testing.assert_frame_equal(summary["events"], original, check_exact=True)
    tables, _ = prepare_yearly_view_export(summary, dict(view, event_types=[]), stock=True)
    assert not any(name.startswith("Abgang_") for name in tables)
