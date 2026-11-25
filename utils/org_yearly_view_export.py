"""Export the displayed yearly selection using the existing workbook/lineage writer."""
from copy import deepcopy
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
import re
import pandas as pd

from utils.org_simulation_export import (
    append_stock_export, append_yearly_export, build_org_simulation_export_bytes,
    export_fingerprint,
)


def build_stock_slide_table(summary, view, *, metric=None):
    """Wide, presentation-ready stock and departure series for the visible selection."""
    from components.org_simulation_yearly import LABELS

    bridge = summary["stock_reconciliation"]
    level = view["level"]
    metric = metric or view["metric"]
    organizations = set(view["organizations"])
    value = f"Bestand-{metric}"
    change = f"{metric}-Veränderung"
    annual = bridge["annual"]
    annual = annual.loc[annual["Ebene"].eq(level) & annual["Organisation"].isin(organizations)]
    available_years = set(annual.loc[annual["Art"].eq("Jahresstichtag"), "Jahr"])
    years = sorted({int(year) for year in view["years"]} & available_years)
    chart = annual.drop_duplicates(["Organisation", "Stichtag"], keep="last")
    chart = chart.loc[chart["Art"].eq("Ausgangsstichtag") | chart["Jahr"].isin(years)]
    chart = chart.copy()
    chart["Stichtag"] = pd.to_datetime(chart["Stichtag"])
    start_date = pd.Timestamp(summary["metadata"]["base_date"]).normalize()
    start = float(chart.loc[chart["Stichtag"].dt.normalize().eq(start_date), value].sum())
    stocks = {year: float(chart.loc[
        chart["Stichtag"].dt.year.eq(year) & chart["Stichtag"].dt.normalize().ne(start_date), value
    ].sum()) for year in years}

    events = bridge["reporting_events"].copy()
    if level != "Gesamt":
        events = events.loc[events[level].isin(organizations)]
    if events.empty:
        by_type = pd.Series(dtype=float)
    else:
        by_type = -events.groupby(["Jahr", "Ereignisart"])[change].sum()

    def losses(year, *types):
        return float(sum(by_type.get((year, kind), 0.0) for kind in types))

    rows = []
    def add(section, label, values, start_value=0.0):
        rows.append({"Bereich": section, "Kennzahl": label, "Start": start_value,
                     **{year: values[year] for year in years}})

    add("Bestand", "Verbleibender heutiger Bestand", stocks, start)
    yearly = annual.loc[annual["Art"].eq("Jahresstichtag")].groupby("Jahr")[value].sum()
    previous = {year: float(yearly.get(year - 1, start)) for year in years}
    add("Bestand", "Rückgang gegenüber Vorjahresstichtag", {year: previous[year] - stocks[year] for year in years})

    retirement_types = ("RETIREMENT", "ATZ_END", "ATZ_AR_TO_FR") if metric == "MAK" else ("RETIREMENT", "ATZ_END")
    categories = [
        ("Fluktuation", "Abgänge durch Fluktuation", ("QUIT",)),
        ("Renteneintritte (inkl. ATZ)", "Renteneintritte (inkl. ATZ)", retirement_types),
        ("Renteneintritte (inkl. ATZ)", "Rente (direkt)", ("RETIREMENT",)),
        ("Renteneintritte (inkl. ATZ)", "Rente nach ATZ-Vertragsende", ("ATZ_END",)),
        ("Weitere Abgänge", LABELS.get("Azubi_Exit", "Azubi: Nichtübernahme"), ("Azubi_Exit",)),
        ("Kapazitätsänderungen", "ATZ-Freistellung", ("ATZ_AR_TO_FR",)),
        ("Kapazitätsänderungen", "Ruhend: Beginn", ("RUHEND_START",)),
        ("Kapazitätsänderungen", "Ruhend: Rückkehr (negativer Verlust)", ("RUHEND_RETURN",)),
    ]
    for section, label, types in categories:
        add(section, label, {year: losses(year, *types) for year in years})
    return pd.DataFrame(rows)


SLIDE_SHEETS = ("Bestand_Folienvorlage_MAK", "Bestand_Folienvorlage_Koepfe")
REF_SLIDE_SHEETS = ("Ref_Bestand_Folienvorlage_MAK", "Ref_Bestand_Folienvorlage_Koepfe")


def append_stock_slide_tables(tables, summary, stock_view):
    if "monte_carlo_bands" in stock_view:
        # The departure categories in this template come from Seed 0 only.
        # Keep them as a named audit until per-seed reporting effects exist.
        tables["Ref_Bestand_Folienvorlage_MAK"] = build_stock_slide_table(
            summary, stock_view, metric="MAK")
        tables["Ref_Bestand_Folienvorlage_Koepfe"] = build_stock_slide_table(
            summary, stock_view, metric="Köpfe")
    else:
        tables[SLIDE_SHEETS[0]] = build_stock_slide_table(summary, stock_view, metric="MAK")
        tables[SLIDE_SHEETS[1]] = build_stock_slide_table(summary, stock_view, metric="Köpfe")


def prepare_yearly_view_export(summary, view, *, stock=False, stock_view=None,
                               monte_carlo_ensemble=None, snapshot_frames=None,
                               overview_summary=None, overview_ensemble=None):
    if not view or view.get("run_signature") != summary.get("run_signature"):
        raise ValueError("Die Exportauswahl gehört nicht zum gespeicherten Lauf.")
    context = {"Laufkennung": summary["run_signature"], "Lauf": deepcopy(summary["metadata"]),
               "Exporttyp": "Veränderungen pro Jahr – aktuelle Darstellung",
               "Betrachtungsräume": "Auswahl_Grafik und Auswahl_Matrix entsprechen exakt der sichtbaren Auswahl. Weitere Blätter dokumentieren den gesamten gespeicherten Lauf als Berechnungsnachweis.",
               "SOLL-Grundlage": "Dieser Ausschnitt enthält keine SOLL-Auswertung.",
               "Anzeige": {"Darstellung": "Verbleibender Bestand" if stock else "Veränderungen"}}
    tables = {}
    selected_stock = view if stock else stock_view
    if selected_stock is not None and selected_stock.get("level") in {"Organisationseinheit", "OE-Cluster"}:
        from utils.org_yearly_summary_export import build_oe_summary
        tables["Zusammenfassung"] = build_oe_summary(
            overview_summary or summary, selected_stock,
            overview_ensemble if overview_ensemble is not None else monte_carlo_ensemble,
            snapshot_frames=snapshot_frames)
    if stock:
        bridge = summary.get("stock_reconciliation", {})
        if bridge.get("status") != "Abgestimmt":
            raise ValueError("Der Bestandsverlauf ist nicht abgestimmt.")
        append_stock_export(tables, context, summary, view)
        append_stock_slide_tables(tables, summary, view)
        context["Folienvorlage"] = {
            "Kennzahlen": ["MAK", "Köpfe"], "Ebene": view["level"],
            "Bedeutung": "Positive Abgangswerte sind Verluste aus der abgestimmten Berichtswirkung. Renteneintritte inkl. ATZ enthalten bei MAK auch die ATZ-Freistellung; bei Köpfen erst das Vertragsende. Die separate ATZ-Freistellungszeile ist eine Aufschlüsselung und darf nicht nochmals addiert werden. Rückkehr aus Ruhend ist negativ. Der Bestandsrückgang kann zusätzlich technische Prognosebasis- und undatierte Aufbereitungseffekte enthalten.",
        }
        if "monte_carlo_bands" in view:
            context["Folienvorlage"]["Mehrlaufstatus"] = (
                "Die Folienvorlage mit Abgangskategorien gehört zu Seed 0 und steht nur als Ref_*-Auditblatt bereit. "
                "Der primäre Jahresbestand steht als Mittelwert/P10/P90 in der MC-Auswahl und Bestandsauswahl.")
        append_yearly_export(tables, context, summary, None,
                             monte_carlo="monte_carlo_bands" in view)
    else:
        append_yearly_export(tables, context, summary, view,
                             monte_carlo="monte_carlo_bands" in view)
        if stock_view and stock_view.get("run_signature") == summary.get("run_signature"):
            append_stock_slide_tables(tables, summary, stock_view)
            if "monte_carlo_bands" in stock_view:
                tables["MC_Jahresbestand_Auswahl"] = stock_view["monte_carlo_bands"].rename(
                    columns={"mean": "Mittelwert", "lower": "P10", "upper": "P90"}).copy(deep=True)
                context["Monte-Carlo Jahresbestand"] = dict(stock_view["monte_carlo_metadata"])
                context["Folienvorlage"] = {
                    "Mehrlaufstatus": (
                        "Die beiden Ref_Bestand_Folienvorlage-Blätter zeigen Seed 0. "
                        "Mittelwert und P10/P90 des Jahresbestands stehen in MC_Jahresbestand_Auswahl."
                    )
                }
                tables["MC_Jahresbestand_Info"] = pd.DataFrame(
                    list(stock_view["monte_carlo_metadata"].items()), columns=["Merkmal", "Wert"])
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
        if (event_type not in set(departures["Ereignisart"])
                and (stock or "reference_details" not in view
                     or event_type not in set(view["details"]["Ereignisart"]))):
            continue
        selected = build_yearly_view(summary, dimension=dimension, years=years,
                                     organizations=organizations, event_types=[event_type], metric=metric)
        label = LABELS.get(event_type, event_type)
        name = f"Abgang_{label}"
        if not stock and "reference_details" in view:
            observed = view["details"].loc[view["details"]["Ereignisart"].eq(event_type)]
            matrix = observed.pivot(index=dimension, columns="Jahr", values=metric).reindex(
                index=organizations, columns=years, fill_value=0.0)
            tables[name] = matrix.reset_index()
            tables[f"Referenzlauf_{name}"] = selected["table"].reset_index()
        else:
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
    slide_ensemble = (overview_ensemble if overview_ensemble is not None
                      else monte_carlo_ensemble)
    slide_summary = overview_summary if overview_summary is not None else summary
    if slide_ensemble is not None and selected_stock is not None:
        from utils.org_yearly_scenario_export import build_scenario_stock_slide
        tables[SLIDE_SHEETS[0]] = build_scenario_stock_slide(
            slide_summary, selected_stock, slide_ensemble, metric="MAK")
        tables[SLIDE_SHEETS[1]] = build_scenario_stock_slide(
            slide_summary, selected_stock, slide_ensemble, metric="Köpfe")
        if REF_SLIDE_SHEETS[0] not in tables:
            tables[REF_SLIDE_SHEETS[0]] = build_stock_slide_table(
                slide_summary, selected_stock, metric="MAK")
            tables[REF_SLIDE_SHEETS[1]] = build_stock_slide_table(
                slide_summary, selected_stock, metric="Köpfe")
        context["Folienvorlage"] = {
            "Mehrlaufstatus": (
                "Blätter 01/02: Mittelwert und P10/P90 aller Läufe sowie drei vollständige "
                "Einzellaufpfade nach Kündigungszahl. Rente, ATZ und Azubi-Abgänge "
                "stammen ebenfalls aus den jeweiligen vollständigen Läufen. "
                "Ref_Bestand_Folienvorlage: Seed-0-Audit einschließlich Ruhend. "
                "Der Gesamtbestand enthält sämtliche Bestandseffekte; kumulierte Fluktuationsverluste "
                "sind kein separat simulierter Bestand ohne Kündigungen."
            ),
            "Szenarioauswahl": "Fluktuation max/min nach Kündigungszahl; typischer Lauf nach Gesamtjahrespfad",
        }
    # Put the displayed data first; retain the existing, explicitly named audit sheets.
    selection = {name: frame for name, frame in tables.items()
                 if "Auswahl" in name or "auswahl" in name}
    slide = {name: tables[name] for name in (*SLIDE_SHEETS, *REF_SLIDE_SHEETS)
             if name in tables}
    overview = {"Zusammenfassung": tables["Zusammenfassung"]} if "Zusammenfassung" in tables else {}
    tables = {**slide, **overview, **selection, **tables}
    return tables, context


def _single_oe_view(view, oe, *, stock, dimension="Organisationseinheit"):
    """Scope the saved display data without changing any simulation result."""
    scoped = dict(view)
    if stock:
        scoped["organizations"] = [oe]
    for key, value in view.items():
        if not isinstance(value, pd.DataFrame):
            continue
        column = next((name for name in (dimension, "Organisation")
                       if name in value.columns), None)
        if column is not None:
            scoped[key] = value.loc[value[column].eq(oe)].copy()
        elif value.index.name in {dimension, "Organisation"}:
            scoped[key] = value.loc[value.index.isin([oe])].copy()
        elif not stock and key in {"summary_table", "table"}:
            scoped[key] = value.loc[value.index.isin([oe])].copy()
    if not stock:
        # Evaluations without an OE column can contain totals across the whole
        # visible selection. Do not put those totals into an individual workbook.
        for key in ("quit_evaluation", "quit_chart", "retirement_evaluation",
                    "retirement_chart", "atz_evaluation", "atz_chart"):
            scoped.pop(key, None)
    return scoped


def _multiple_oe_view(view, organizations, *, dimension):
    """Keep all tables of a workbook within the selected ZIP population."""
    scoped = dict(view)
    if "organizations" in scoped:
        scoped["organizations"] = list(organizations)
    for key, value in view.items():
        if not isinstance(value, pd.DataFrame):
            continue
        column = next((name for name in (dimension, "Organisation")
                       if name in value.columns), None)
        if column is not None:
            scoped[key] = value.loc[value[column].isin(organizations)].copy()
        elif value.index.name in {dimension, "Organisation"}:
            scoped[key] = value.loc[value.index.isin(organizations)].copy()
    return scoped


def build_yearly_oe_bulk_zip(summary, view, *, stock=False, stock_view=None,
                             monte_carlo_ensemble=None, overview_summary=None,
                             overview_ensemble=None):
    """Build one workbook per OE and one consolidated selected-scope workbook."""
    dimension = view.get("level" if stock else "dimension")
    if dimension not in {"Organisationseinheit", "OE-Cluster"}:
        raise ValueError("Der ZIP-Export ist nur für Organisationseinheiten und OE-Cluster verfügbar.")
    organizations = view["organizations"] if stock else view["table"].index.tolist()
    organizations = list(dict.fromkeys(organizations))
    if not organizations:
        raise ValueError("Keine Organisationen ausgewählt.")
    output = BytesIO()
    snapshot_frames = None
    age_ensemble = overview_ensemble if overview_ensemble is not None else monte_carlo_ensemble
    if age_ensemble is not None:
        from utils.monte_carlo_filtered_org import decode_snapshot
        snapshot_frames = [decode_snapshot(blob) for blob in
                           age_ensemble.get("org_filter_snapshots", [])]
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        aggregate_view = _multiple_oe_view(view, organizations, dimension=dimension)
        aggregate_stock = (aggregate_view if stock else
                           _multiple_oe_view(stock_view, organizations, dimension=dimension)
                           if stock_view is not None else None)
        total_tables, total_context = prepare_yearly_view_export(
            summary, aggregate_view, stock=stock, stock_view=aggregate_stock,
            monte_carlo_ensemble=monte_carlo_ensemble,
            snapshot_frames=snapshot_frames,
            overview_summary=overview_summary,
            overview_ensemble=overview_ensemble)
        total_context["Betrachtungsräume"] = (
            f"Gesamtauswertung der {len(organizations)} im ZIP enthaltenen {dimension}-Einheiten.")
        archive.writestr(
            "00_Gesamt_ausgewaehlte_Einheiten.xlsx",
            build_org_simulation_export_bytes(total_tables, total_context))
        for number, oe in enumerate(organizations, 1):
            selected = _single_oe_view(view, oe, stock=stock, dimension=dimension)
            selected_stock = (selected if stock else
                              _single_oe_view(stock_view, oe, stock=True, dimension=dimension) if stock_view else None)
            tables, context = prepare_yearly_view_export(
                summary, selected, stock=stock, stock_view=selected_stock,
                monte_carlo_ensemble=monte_carlo_ensemble,
                snapshot_frames=snapshot_frames,
                overview_summary=overview_summary,
                overview_ensemble=overview_ensemble)
            # The ordinary workbook also contains complete-run audit sheets.
            # These would disclose other OEs and mislabel aggregate figures as
            # belonging to this OE, so the bulk workbook retains scoped views.
            tables = {name: frame for name, frame in tables.items()
                      if ("Auswahl" in name or "auswahl" in name or
                          "Folienvorlage" in name or name.startswith("Abgang_") or
                          name.startswith("Referenzlauf_Abgang_") or
                          name in {"Zusammenfassung", "Abgangsmatrizen_Verzeichnis", "Jahreszeiträume", "JahreszeitrÃ¤ume",
                                   "MC_Jahresbestand_Info", "MC_Jahresereignisse_Info"})}
            context[dimension] = oe
            context["Betrachtungsräume"] = (
                f"Einzelauswertung für genau diese {dimension}; "
                "laufweite Auditblätter sind im separaten Gesamtexport enthalten.")
            context["BetrachtungsrÃ¤ume"] = context["Betrachtungsräume"]
            for field in ("Jahresauswertung", "Bestandsüberleitung", "BestandsÃ¼berleitung"):
                if field in context and isinstance(context[field], dict):
                    context[field]["Betrachtungsraum"] = f"{dimension}: {oe}"
                    for key in list(context[field]):
                        if key.startswith("Vollst"):
                            context[field][key] = f"Im ZIP nur die ausgewählte {dimension}."
            filename_oe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(oe)).strip(" ._")[:90] or "OE"
            prefix = "OE" if dimension == "Organisationseinheit" else "OE_Cluster"
            name = f"{prefix}_{number:03d}_{filename_oe}.xlsx"
            archive.writestr(name, build_org_simulation_export_bytes(tables, context))
    return output.getvalue()


def render_yearly_view_export(summary, view, *, stock=False, stock_view=None,
                              monte_carlo_ensemble=None):
    import streamlit as st
    from components.ui_compat import lazy_excel_download_button_compat

    if not view or view.get("run_signature") != summary.get("run_signature"):
        return
    overview_summary = overview_ensemble = None
    if monte_carlo_ensemble is None:
        from utils.simulation_validation import current_view
        from utils.monte_carlo_org_view import approved_org_ensemble
        original = current_view(st.session_state, require_approval=True, use_approved=False)
        if original is not None and original.event_summary is not summary:
            overview_ensemble = approved_org_ensemble(st.session_state, original)
            if overview_ensemble is not None:
                overview_summary = original.event_summary
    # Building every audit sheet on each widget rerun delays the charts even
    # when nobody exports. Fingerprint the visible selection and build on click.
    selection = {key: view.get(key) for key in ("dimension", "level", "metric", "years", "organizations", "event_types")}
    if "periods" in view:
        selection["years"] = view["periods"].Jahr.tolist()
    if "table" in view and "organizations" not in view:
        selection["organizations"] = view["table"].index.tolist()
    fingerprint = export_fingerprint(
        {key: view[key] for key in ("chart", "table", "monte_carlo_bands") if isinstance(view.get(key), pd.DataFrame)},
        {"run": summary["run_signature"], "summary_object": id(summary), "stock": stock,
         "ensemble": None if monte_carlo_ensemble is None else monte_carlo_ensemble.get("plan_signature"),
         "overview_ensemble": None if overview_ensemble is None else overview_ensemble.get("plan_signature"),
         "selection": selection, "stock_view": None if stock_view is None else
         {key: stock_view.get(key) for key in ("level", "metric", "organizations", "years")}},
    )

    def build_payload():
        tables, context = prepare_yearly_view_export(
            summary, view, stock=stock, stock_view=stock_view,
            monte_carlo_ensemble=monte_carlo_ensemble,
            overview_summary=overview_summary, overview_ensemble=overview_ensemble)
        from utils.simulation_validation import current_view
        active = current_view(st.session_state, require_approval=True)
        if active is not None and active.event_summary is summary:
            context["Laufeinstellungen"] = deepcopy(active.export_context)
            context["abgaenge_params"] = deepcopy(st.session_state.get("compact_sim_abgaenge_params"))
            context["zugaenge_params"] = deepcopy(st.session_state.get("compact_sim_zugaenge_params"))
            context["Ergebnisversion"] = active.version
        elif st.session_state.get("compact_sim_signature") == summary["run_signature"]:
            context["Laufeinstellungen"] = deepcopy(st.session_state.get("compact_sim_export_context", {}))
            context["abgaenge_params"] = deepcopy(st.session_state.get("compact_sim_abgaenge_params"))
            context["zugaenge_params"] = deepcopy(st.session_state.get("compact_sim_zugaenge_params"))
        return build_org_simulation_export_bytes(tables, context)

    dimension = view.get("level" if stock else "dimension")
    zip_available = dimension in {"Organisationseinheit", "OE-Cluster"}
    excel_column, zip_column = st.columns(2) if zip_available else (st.container(), None)
    with excel_column:
        lazy_excel_download_button_compat(
        label="Excel exportieren", data_builder=build_payload,
        file_name="OE_Bestandsverlauf.xlsx" if stock else "OE_Veraenderungen.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="org_yearly_selection_export_stock" if stock else "org_yearly_selection_export_events",
        fingerprint=fingerprint,
        help=("Aktuelle Auswahl inklusive Datenherkunft, Formeln und Berechnungsnachweis. "
              + ("Die Folienvorlagen für MAK und Köpfe stehen in den ersten beiden Datenblättern."
                 if stock or stock_view is not None else "")),
        )
    if zip_available:
        with zip_column:
            lazy_excel_download_button_compat(
                label="Alle OEs als ZIP" if dimension == "Organisationseinheit" else "Alle OE-Cluster als ZIP",
                data_builder=lambda: build_yearly_oe_bulk_zip(
                    summary, view, stock=stock, stock_view=stock_view,
                    monte_carlo_ensemble=monte_carlo_ensemble,
                    overview_summary=overview_summary, overview_ensemble=overview_ensemble),
                file_name=("OE_Cluster" if dimension == "OE-Cluster" else "OE")
                + ("_Bestandsverlauf_Einzeln.zip" if stock else "_Veraenderungen_Einzeln.zip"),
                mime="application/zip",
                key="org_yearly_bulk_stock" if stock else "org_yearly_bulk_events",
                fingerprint=fingerprint,
                spinner_label="Einzeldateien und ZIP werden erstellt...",
                help=(f"Eine eigene Excel-Datei je ausgewählter {dimension} "
                      "und eine konsolidierte Gesamtdatei für genau diese Auswahl."),
            )
