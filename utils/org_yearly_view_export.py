"""Export the displayed yearly selection using the existing workbook/lineage writer."""
from copy import deepcopy
import pandas as pd

from utils.org_simulation_export import (
    append_stock_export, append_yearly_export, build_org_simulation_export_bytes,
    export_fingerprint,
)


def prepare_yearly_view_export(summary, view, *, stock=False):
    if not view or view.get("run_signature") != summary.get("run_signature"):
        raise ValueError("Die Exportauswahl gehört nicht zum gespeicherten Lauf.")
    context = {"Laufkennung": summary["run_signature"], "Lauf": deepcopy(summary["metadata"]),
               "Exporttyp": "Veränderungen pro Jahr – aktuelle Darstellung",
               "Betrachtungsräume": "Auswahl_Grafik und Auswahl_Matrix entsprechen exakt der sichtbaren Auswahl. Weitere Blätter dokumentieren den gesamten gespeicherten Lauf als Berechnungsnachweis.",
               "SOLL-Grundlage": "Dieser Ausschnitt enthält keine SOLL-Auswertung.",
               "Anzeige": {"Darstellung": "Verbleibender Bestand" if stock else "Veränderungen"}}
    tables = {}
    if stock:
        bridge = summary.get("stock_reconciliation", {})
        if bridge.get("status") != "Abgestimmt":
            raise ValueError("Der Bestandsverlauf ist nicht abgestimmt.")
        append_stock_export(tables, context, summary, view)
        append_yearly_export(tables, context, summary, None)
    else:
        append_yearly_export(tables, context, summary, view)
    from components.org_simulation_yearly import LABELS, build_yearly_view
    events = summary["events"]
    departures = events.loc[events["Ereignisquelle"].eq("departure_events")]
    types = view.get("event_types", sorted(departures["Ereignisart"].unique()))
    dimension = view["level"] if stock else view["dimension"]
    if dimension == "Gesamt":
        types = []
    organizations = view["organizations"] if stock else view["table"].index.tolist()
    years = view["years"] if stock else view["periods"]["Jahr"].tolist()
    metric = f"{view['metric']}-Veränderung" if stock else view["metric"]
    # Original-event matrices use exactly the existing yearly-view aggregation.
    # Keep signed effects, rather than silently turning returns into departures.
    matrices = []
    for event_type in types:
        if event_type not in set(departures["Ereignisart"]):
            continue
        selected = build_yearly_view(summary, dimension=dimension, years=years,
                                     organizations=organizations, event_types=[event_type], metric=metric)
        label = LABELS.get(event_type, event_type)
        name = f"Abgang_{label}"
        tables[name] = selected["table"].reset_index()
        matrices.append({"Ereignisart": event_type, "Bezeichnung": label, "Bereich": name, "Kennzahl": metric})
    if matrices:
        tables["Abgangsmatrizen_Verzeichnis"] = pd.DataFrame(matrices)
    context["Abgangsmatrizen"] = {
        "Kennzahl": metric, "Ebene": dimension, "Jahre": years, "Organisationen": organizations,
        "Ereignisarten": [row["Ereignisart"] for row in matrices],
        "Bedeutung": "Originale Prognoseereignisse pro Jahr, keine kumulierten Werte. Veränderungen sind vorzeichenbehaftet: Abnahmen negativ, Rückkehr positiv. Keine zusätzliche Rundung.",
        "Abgrenzung zum Bestand": "Die Matrixblätter enthalten Originalereignisse. Für die Bestandsüberleitung gelten zusätzlich technische Prognosebasis-Effekte und Reportingwirkungen; die Originalmatrixsummen allein erklären den Bestandsverlauf nicht zwingend.",
    }
    # Put the displayed data first; retain the existing, explicitly named audit sheets.
    selection = {name: frame for name, frame in tables.items()
                 if "Auswahl" in name or "auswahl" in name}
    tables = {**selection, **tables}
    return tables, context


def render_yearly_view_export(summary, view, *, stock=False):
    import streamlit as st
    from components.ui_compat import lazy_excel_download_button_compat

    tables, context = prepare_yearly_view_export(summary, view, stock=stock)
    if st.session_state.get("compact_sim_signature") == summary["run_signature"]:
        context["Laufeinstellungen"] = deepcopy(st.session_state.get("compact_sim_export_context", {}))
        context["abgaenge_params"] = deepcopy(st.session_state.get("compact_sim_abgaenge_params"))
        context["zugaenge_params"] = deepcopy(st.session_state.get("compact_sim_zugaenge_params"))
    lazy_excel_download_button_compat(
        label="Excel exportieren", data_builder=lambda: build_org_simulation_export_bytes(tables, context),
        file_name="OE_Jahresdarstellung.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="org_yearly_selection_export_stock" if stock else "org_yearly_selection_export_events",
        fingerprint=export_fingerprint(tables, context),
        help="Aktuelle Auswahl inklusive Datenherkunft, Formeln und Berechnungsnachweis.",
    )
