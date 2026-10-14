from copy import deepcopy
from io import BytesIO

import pandas as pd
import pytest

from test_org_simulation_yearly import _summary
from components.org_simulation_yearly import build_yearly_view
from utils.org_simulation_export import append_yearly_export, build_org_simulation_export_bytes, export_fingerprint


def _export_inputs():
    summary = _summary()
    view = build_yearly_view(summary, dimension="OE-Cluster", years=[2026], organizations=["A"],
                             event_types=["QUIT"], metric="MAK-Veränderung")
    context = {"Laufkennung": "a", "Lauf": summary["metadata"]}
    tables = {}
    append_yearly_export(tables, context, summary, view)
    return summary, view, tables, context


def test_yearly_workbook_matches_display_and_full_run():
    summary, view, tables, context = _export_inputs()
    pd.testing.assert_frame_equal(tables["Jahresauswahl_Grafik"], view["chart"], check_exact=True)
    pd.testing.assert_frame_equal(tables["Jahresauswahl_Matrix"], view["table"].reset_index(), check_exact=True)
    assert tables["Jahre_Cluster_vollständig"]["Abgänge"].sum() == 4
    assert tables["Jahresauswahl_Details"]["Abgänge"].sum() == 1
    assert context["Jahresauswertung"]["Auswahl"]["Ereignisarten"] == ["QUIT"]
    assert tables["Jahresauswahl_Details"]["Ereignisart"].tolist() == ["Kündigung"]
    workbook = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(tables, context)))
    inventory = pd.read_excel(workbook, "Exportinventar")
    for row in inventory.to_dict("records"):
        actual = pd.read_excel(workbook, row["Tabellenblatt"])
        pd.testing.assert_frame_equal(actual, tables[row["Bereich"]].reset_index(drop=True),
                                      check_dtype=False, check_index_type=False, check_names=False)
    assert "10-09" in pd.read_excel(workbook, "Lineage_Report").astype(str).to_numpy()
    assert "Jahresauswertung" in pd.read_excel(workbook, "00_Dokumentation")["Feld"].tolist()


def test_full_run_tables_do_not_depend_on_display_selection():
    summary, view, tables, context = _export_inputs()
    other = {}
    append_yearly_export(other, {"Laufkennung": "a"}, summary, None)
    for name in ("Jahre_OE_vollständig", "Jahre_Cluster_vollständig", "Gesamt_OE_Ereignisse", "Gesamt_Cluster_Ereignisse"):
        pd.testing.assert_frame_equal(tables[name], other[name], check_exact=True)
    assert "Jahresauswahl_Matrix" not in other


def test_export_copies_sources_and_fingerprint_covers_year_selection_and_mapping():
    summary, view, tables, context = _export_inputs()
    before = deepcopy(summary)
    fingerprint = export_fingerprint(tables, context)
    tables["Jahre_OE_vollständig"].iloc[0, -1] = 99
    assert export_fingerprint(tables, context) != fingerprint
    pd.testing.assert_frame_equal(summary["annual_oe"], before["annual_oe"], check_exact=True)
    changed = deepcopy(context)
    changed["Jahresauswertung"]["Auswahl"]["Jahre"] = [2027]
    assert export_fingerprint(tables, changed) != export_fingerprint(tables, context)


@pytest.mark.parametrize("signature", [None, "different"])
def test_mismatched_run_is_never_exported(signature):
    tables, context = {}, {"Laufkennung": signature}
    append_yearly_export(tables, context, _summary(), None)
    assert tables == {}
    assert "Keine" in context["Jahresauswertung"]["Status"]


def test_real_page_export_updates_year_selection_and_keeps_full_run_tables():
    from test_org_simulation_export import make_app
    app = make_app()
    app.session_state["test_yearly"] = True
    app.run()
    assert not app.exception
    before = app.session_state["test_fingerprint"]
    app.radio(key="org_yearly_level").set_value("OE-Cluster").run()
    next(widget for widget in app.multiselect if widget.label == "Jahre").set_value([2026]).run()
    next(widget for widget in app.multiselect if widget.label == "OE-Cluster").set_value(["A"]).run()
    next(widget for widget in app.multiselect if widget.label == "Ereignisarten").set_value(["QUIT"]).run()
    assert not app.exception
    assert app.session_state["test_fingerprint"] != before
    book = pd.ExcelFile(BytesIO(app.session_state["test_builder"]()))
    inventory = pd.read_excel(book, "Exportinventar").set_index("Bereich")
    selected = pd.read_excel(book, inventory.loc["Jahresauswahl_Grafik", "Tabellenblatt"])
    full = pd.read_excel(book, inventory.loc["Jahre_Cluster_vollständig", "Tabellenblatt"])
    assert selected["Abgänge"].sum() == 1
    assert full["Abgänge"].sum() == 4


def test_real_event_export_supports_period_columns_and_empty_run():
    from test_org_yearly_reference import _run
    from utils.org_simulation_events import build_simulation_event_summary
    for scenario in ("combined", "no_events"):
        result = _run(scenario)
        summary = build_simulation_event_summary({
            "run_signature": "a", "metadata": result.metadata,
            "cluster_mapping": {"oe_map": {"OE1": "A", "OE2": "B"}},
            "departure_events": result.abgaenge_result.get("events_person_level", pd.DataFrame()),
            "accession_events": result.zugaenge_result.get("events", pd.DataFrame())}, expected_run_signature="a")
        view = build_yearly_view(summary, dimension="Organisationseinheit", years=[2026, 2027],
                                 organizations=["OE1", "OE2"],
                                 event_types=summary["events"].Ereignisart.unique().tolist(), metric="Abgänge")
        tables, context = {}, {"Laufkennung": "a", "Lauf": result.metadata}
        append_yearly_export(tables, context, summary, view)
        book = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(tables, context)))
        inventory = pd.read_excel(book, "Exportinventar").set_index("Bereich")
        full = pd.read_excel(book, inventory.loc["Jahre_OE_vollständig", "Tabellenblatt"])
        assert full["Abgänge"].sum() == pytest.approx(summary["events"]["Abgänge"].sum())
