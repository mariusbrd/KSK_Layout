"""Compose the OE workbook from the actual rendered tables and saved run context."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from io import BytesIO
import hashlib
import json
import re

import pandas as pd

from abgaenge.schemas import normalize_persnr
from utils.compact_simulation_export import build_parameter_sheet
from utils.lineage import write_lineage_sheet
from utils.lineage.excel import _sanitize_excel_value


def export_fingerprint(tables, context):
    """Hash all rows (not a sample) and the entire immutable export context."""
    digest = hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode())
    for name, frame in tables.items():
        digest.update(name.encode())
        digest.update(str(list(frame.columns)).encode())
        try:
            digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
        except TypeError:
            digest.update(frame.to_json(orient="split", date_format="iso", double_precision=15, default_handler=str).encode())
        digest.update(str(list(frame.dtypes.astype(str))).encode())
    return digest.hexdigest()


def build_plan_comparison(current, baseline, orgs, compact):
    """Capacity SOLL uses the saved initial snapshot, never current input files.

    Headcount and position rows are deliberately distinct quantities. The existing
    Compact capacity/headcount functions remain the source of truth.
    """
    if baseline is None or baseline.empty:
        return pd.DataFrame()
    rows = []
    for org in orgs:
        initial = baseline[baseline["Organisationseinheit"] == org]
        future = current[current["Organisationseinheit"] == org]
        soll = compact.get_soll_mak(initial) if "Soll_FTE" in baseline.columns else None
        ist = compact.get_ist_mak(initial)
        simulation = compact.get_ist_mak(future)
        rows.append({"Organisationseinheit": org, "SOLL-MAK Ausgangsstellenplan": soll,
                     "IST-MAK": ist, "Simulation-MAK": simulation,
                     "IST minus SOLL (MAK)": ist - soll if soll is not None else None,
                     "Simulation minus SOLL (MAK)": simulation - soll if soll is not None else None,
                     "IST-Köpfe": compact.get_ist_koepfe(initial),
                     "Simulation-Köpfe": compact.get_ist_koepfe(future),
                     "Ausgangs-Planstellenzeilen": len(initial)})
    return pd.DataFrame(rows)


def build_monte_carlo_plan_comparison(current, baseline, orgs, compact,
                                      ensemble, active_filters):
    """Keep fixed plan/IST values and summarize each filtered future run."""
    from components.sidebar import _apply_filters_uncached
    from utils.monte_carlo_ensemble import _statistics
    from utils.monte_carlo_filtered_org import decode_snapshot

    reference = build_plan_comparison(current, baseline, orgs, compact)
    if reference.empty:
        return reference
    blobs = ensemble.get("org_filter_snapshots")
    if not isinstance(blobs, list) or len(blobs) != ensemble.get("count"):
        raise ValueError("OE-Laufdaten für den Stellenplanvergleich fehlen")
    runs = []
    for blob in blobs:
        future = _apply_filters_uncached(decode_snapshot(blob), dict(active_filters or {}))
        future = future.assign(Organisationseinheit=future.Organisationseinheit.fillna(
            "Nicht zugeordnet").astype(str).str.strip().replace("", "Nicht zugeordnet"))
        runs.append({org: (compact.get_ist_mak(future.loc[
            future.Organisationseinheit.eq(org)]),
            compact.get_ist_koepfe(future.loc[
                future.Organisationseinheit.eq(org)])) for org in orgs})
    for index, row in reference.iterrows():
        org = row["Organisationseinheit"]
        mak = [float(run[org][0]) for run in runs]
        heads = [float(run[org][1]) for run in runs]
        if abs(mak[0] - float(row["Simulation-MAK"])) > 1e-6 or abs(
                heads[0] - float(row["Simulation-Köpfe"])) > 1e-6:
            raise ValueError(f"Stellenplanvergleich weicht für {org} vom Referenzlauf ab")
        for label, samples in (("Simulation-MAK", mak), ("Simulation-Köpfe", heads)):
            stats = _statistics(samples, ensemble["band_level"])
            reference.loc[index, label] = stats["mean"]
            reference.loc[index, f"{label} P10"] = stats["lower"]
            reference.loc[index, f"{label} P90"] = stats["upper"]
        soll = row["SOLL-MAK Ausgangsstellenplan"]
        if pd.notna(soll):
            stats = _statistics([value - float(soll) for value in mak], ensemble["band_level"])
            reference.loc[index, "Simulation minus SOLL (MAK)"] = stats["mean"]
            reference.loc[index, "Simulation minus SOLL P10"] = stats["lower"]
            reference.loc[index, "Simulation minus SOLL P90"] = stats["upper"]
    return reference


def build_org_simulation_export_bytes(tables, context):
    context = deepcopy(context)
    notes = {
        "Exportzeitpunkt": datetime.now().isoformat(timespec="seconds"),
        "Exporttyp": "Organisationseinheiten-Analyse Simulation",
        "Betrachtungsräume": "KPI/Datenqualität: gesamter gefilterter Bestand. Rangliste/Details/SOLL: ausgewählte OEs.",
        "SOLL-Grundlage": "Soll_FTE aus dem gespeicherten, gefilterten Ausgangs-Snapshot; unverändert für IST und Simulation.",
        "Köpfe und Stellen": "Köpfe werden über die bestehenden Funktionen gezählt. Planstellenzeilen sind keine SOLL-Köpfe.",
        "Fehlendes SOLL": "Fehlt der Ausgangs-Snapshot oder Soll_FTE, sind SOLL-Werte nicht verfügbar; kein Ersatz durch aktuelle Dateien oder Null.",
        "Herkunft": "Lineage beschreibt Quellen, Formeln und aktuellen Exportcode. Nicht gespeicherte historische Eingabe-/Codeversionen sind nicht nachträglich beweisbar.",
        "Grafiken": "Die numerischen Daten der dargestellten Grafiken sind enthalten; keine Bildkopien.",
        "Abgänge": "Abgänge allein erklären nicht das gesamte Delta; auch Zugänge und Kapazitätsänderungen können wirken.",
    }
    notes.update({k: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list, tuple)) else v
                  for k, v in context.items() if k not in ("abgaenge_params", "zugaenge_params")})
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary_frame = tables.get("Zusammenfassung")
        if isinstance(summary_frame, pd.DataFrame):
            summary_frame.to_excel(writer, sheet_name="00_Zusammenfassung", index=False)
        else:
            pd.DataFrame(notes.items(), columns=["Feld", "Wert"]).to_excel(
                writer, sheet_name="00_Dokumentation", index=False)
        inventory = []
        for index, (name, frame) in enumerate(
                ((name, frame) for name, frame in tables.items() if name != "Zusammenfassung"), 1):
            sheet = f"{index:02d}_{re.sub(r'[\\/*?:\[\]]', '_', name)}"[:31]
            def export_value(value):
                if isinstance(value, (pd.Timestamp, datetime)) and pd.notna(value) and (value.microsecond or getattr(value, "nanosecond", 0)):
                    return value.isoformat()
                return _sanitize_excel_value(value)
            frame.map(export_value).to_excel(writer, sheet_name=sheet, index=False)
            inventory.append({"Bereich": name, "Tabellenblatt": sheet, "Zeilen": len(frame),
                              "Spalten": ", ".join(map(str, frame.columns))})
        if isinstance(summary_frame, pd.DataFrame):
            inventory.insert(0, {"Bereich": "Zusammenfassung", "Tabellenblatt": "00_Zusammenfassung",
                                 "Zeilen": len(summary_frame),
                                 "Spalten": ", ".join(map(str, summary_frame.columns))})
        if isinstance(summary_frame, pd.DataFrame):
            pd.DataFrame(notes.items(), columns=["Feld", "Wert"]).to_excel(
                writer, sheet_name="00_Dokumentation", index=False)
        pd.DataFrame(inventory).to_excel(writer, sheet_name="Exportinventar", index=False)
        build_parameter_sheet(abgaenge_params=context.get("abgaenge_params"),
                              zugaenge_params=context.get("zugaenge_params"), metadata=context.get("Lauf", {})).to_excel(
                                  writer, sheet_name="Parameter", index=False)
        write_lineage_sheet(writer, tuple(f"10-{i:02d}" for i in range(1, 11)), export_context={
            "Exporttyp": context.get("Exporttyp", "OE-Simulation Gesamtexport"), "Lauf": context.get("Lauf", {}),
            "Anzeige": context.get("Anzeige", {}), "SOLL": notes["SOLL-Grundlage"],
            "Jahresauswertung": context.get("Jahresauswertung", {}),
            "Bestandsüberleitung": context.get("Bestandsüberleitung", {}),
            "Historische Eingaben": "Nur soweit im gespeicherten Lauf dokumentiert; Input_Lineage zeigt aktuell zugängliche Quellen.",
        })
        if isinstance(summary_frame, pd.DataFrame):
            overview_ws = writer.sheets["00_Zusammenfassung"]
            for row_index, unit in enumerate(summary_frame["Einheit"], 2):
                number_format = "0.0%" if unit == "%" else (
                    "0.0" if unit in {"Jahre", "MAK"} else "0" if unit == "Köpfe" else None)
                if number_format:
                    for cells in overview_ws.iter_rows(min_row=row_index, max_row=row_index,
                                                       min_col=3, max_col=overview_ws.max_column):
                        for cell in cells:
                            if isinstance(cell.value, (int, float)):
                                cell.number_format = number_format
        for ws in writer.sheets.values():
            ws.freeze_panes = "A2"
            if ws.max_row > 1 and ws.max_column:
                ws.auto_filter.ref = ws.dimensions
            for cells in ws.columns:
                ws.column_dimensions[cells[0].column_letter].width = min(65, max(12, max(len(str(c.value or "")) for c in cells) + 2))
    return output.getvalue()


def _departure_planstelle_labels(events: pd.DataFrame, baseline: pd.DataFrame | None) -> pd.Series:
    """Describe all occupied starting positions without multiplying person events."""
    labels = pd.Series("", index=events.index, dtype=object)
    if (baseline is None or baseline.empty or events.empty
            or not {"PersNr", "Planstelle"}.issubset(baseline.columns)):
        return labels

    positions = baseline.copy()
    if "Is_Vacant" in positions:
        positions = positions.loc[~positions["Is_Vacant"].fillna(True).astype(bool)].copy()
    positions["_person"] = normalize_persnr(positions["PersNr"])
    positions["_label"] = positions["Planstelle"].fillna("").astype(str).str.strip()
    positions = positions.loc[positions["_person"].notna() & positions["_label"].ne("")]
    if positions.empty:
        return labels

    by_person = positions.groupby("_person", sort=False)["_label"].agg(
        lambda values: ", ".join(dict.fromkeys(values))
    )
    event_ids = events.get("persnr", events.get("PersNr"))
    if event_ids is None:
        return labels
    departures = events["Ereignisquelle"].eq("departure_events")
    labels.loc[departures] = normalize_persnr(event_ids.loc[departures]).map(by_person).fillna("")
    return labels


def append_yearly_export(tables, context, summary, yearly_view, *, monte_carlo=False):
    """Copy saved annual outputs; current selection and full run stay distinct."""
    if not summary or not context.get("Laufkennung") or summary.get("run_signature") != context["Laufkennung"]:
        context["Jahresauswertung"] = {"Status": "Keine zum Lauf passende Jahresauswertung verfügbar"}
        return
    from components.org_simulation_yearly import yearly_periods
    tables["Jahreszeiträume"] = yearly_periods(summary["metadata"])
    for key, name in (("annual_oe", "Jahre_OE_vollständig"), ("annual_cluster", "Jahre_Cluster_vollständig"),
                      ("total_oe", "Gesamt_OE_Ereignisse"), ("total_cluster", "Gesamt_Cluster_Ereignisse")):
        tables[name] = summary[key].copy(deep=True)
    events = summary["events"]
    columns = ["event_date", "Jahr", "Organisationseinheit", "OE-Cluster", "Ereignisquelle", "Ereignisart",
               "Kategorie", "Köpfe-Veränderung", "MAK-Veränderung", "Abgänge", "Zugänge"]
    columns = [c for c in ("persnr", "PersNr", "OE-Zuordnungsherkunft") if c in events] + columns
    event_basis = events[columns].copy(deep=True)
    baseline = summary.get("stock_basis", {}).get("baseline_snapshot")
    position_labels = _departure_planstelle_labels(events, baseline)
    id_columns = [c for c in ("persnr", "PersNr") if c in event_basis]
    insert_at = event_basis.columns.get_loc(id_columns[-1]) + 1 if id_columns else 0
    event_basis.insert(insert_at, "Planstellenbezeichnung", position_labels)
    tables["Jahres_Ereignisbasis"] = event_basis
    tables["OE_Cluster_Laufzuordnung"] = events[["Organisationseinheit", "OE-Cluster"]].drop_duplicates().reset_index(drop=True)
    if "cluster_mapping" in summary:
        tables["OE_Cluster_Quelldefinition"] = pd.DataFrame(
            list(summary["cluster_mapping"].get("oe_map", {}).items()), columns=["Organisationseinheit", "OE-Cluster"])
    info = {"Laufkennung": summary["run_signature"], "Zeitraum": summary["metadata"],
            "Vollständige Tabellen": "Gesamter gespeicherter Lauf; unabhängig von Bestandsfiltern und Jahresauswahl.",
            "Bedeutung": "Ereignissummen; keine Jahresendbestände oder Differenzen aufbereiteter MAK-Bestände.",
            "Clusterherkunft": "Zuordnung aus dem gespeicherten Lauf; nicht aus einer erneut geladenen Datei."}
    info["Clusterquellkennung"] = summary.get("cluster_mapping", {}).get("source_signature")
    info["Clusterdefinition"] = "Vollständiges gespeichertes OE-Mapping" if "cluster_mapping" in summary else "Nur Ereignis-OEs; für vollständiges Mapping Simulation neu berechnen."
    if yearly_view is not None and yearly_view.get("run_signature") == summary["run_signature"]:
        if "monte_carlo_bands" in yearly_view:
            tables["MC_Jahresereignisse_Auswahl"] = yearly_view["monte_carlo_bands"].rename(
                columns={"mean": "Mittelwert", "lower": "P10", "upper": "P90"}).copy(deep=True)
            if "monte_carlo_stacked_chart" in yearly_view:
                tables["MC_Jahresereignisse_Grafik"] = yearly_view["monte_carlo_stacked_chart"].copy(deep=True)
            context["Monte-Carlo Jahresereignisse"] = dict(yearly_view["monte_carlo_metadata"])
            tables["MC_Jahresereignisse_Info"] = pd.DataFrame(
                list(yearly_view["monte_carlo_metadata"].items()), columns=["Merkmal", "Wert"])
        tables["Jahresauswahl_Matrix"] = yearly_view["table"].reset_index()
        if "summary_table" in yearly_view:
            tables["Jahresauswahl_Summen"] = yearly_view["summary_table"].reset_index()
        tables["Jahresauswahl_Grafik"] = yearly_view.get(
            "monte_carlo_stacked_chart", yearly_view.get("stacked_chart", yearly_view["chart"])).copy(deep=True)
        tables["Jahresauswahl_Details"] = yearly_view["details"].copy(deep=True)
        if "reference_details" in yearly_view:
            tables["Ref_Jahresdetails"] = yearly_view["reference_details"].copy(deep=True)
            tables["Ref_Jahressummen"] = yearly_view["reference_summary_table"].reset_index()
        quit_evaluation = yearly_view.get("quit_evaluation")
        if quit_evaluation is not None and not quit_evaluation.empty:
            tables["Jahresauswahl_Kündigungseinordnung"] = quit_evaluation.copy(deep=True)
            tables["Kündigungseinordnung_Grafik"] = yearly_view["quit_chart"].copy(deep=True)
        retirement_evaluation = yearly_view.get("retirement_evaluation")
        if retirement_evaluation is not None:
            tables["Jahresauswahl_Renteneinordnung"] = retirement_evaluation["annual"].copy(deep=True)
            tables["Renteneinordnung_Grafik"] = yearly_view["retirement_chart"].copy(deep=True)
            tables["Rentenparameter_Vergleich"] = pd.DataFrame([
                {"Altersgruppe": cohort, **values}
                for cohort, values in retirement_evaluation["cohorts"].items()])
        atz_evaluation = yearly_view.get("atz_evaluation")
        if atz_evaluation is not None:
            tables["Jahresauswahl_ATZ_Einordnung"] = atz_evaluation["annual"].copy(deep=True)
            tables["ATZ_Gesamtsummen"] = atz_evaluation["totals"].copy(deep=True)
            tables["ATZ_Einordnung_Grafik"] = yearly_view["atz_chart"].copy(deep=True)
            tables["ATZ_Zeitplaene"] = atz_evaluation["details"].copy(deep=True)
            tables["ATZ_Ausgangszeitplan"] = atz_evaluation["schedule_audit"].copy(deep=True)
        documented_atz = summary.get("stock_reconciliation", {}).get("documented_effects")
        if isinstance(documented_atz, pd.DataFrame) and not documented_atz.empty:
            tables["ATZ_Undatierte_Bestandseffekte"] = documented_atz.copy(deep=True)
        if "reference_details" in yearly_view:
            audit_sheets = {
                "Jahresauswahl_Kündigungseinordnung": "Ref_Kuendigung",
                "Kündigungseinordnung_Grafik": "Ref_Kuendigung_Grafik",
                "Jahresauswahl_Renteneinordnung": "Ref_Rente",
                "Renteneinordnung_Grafik": "Ref_Rente_Grafik",
                "Rentenparameter_Vergleich": "Ref_Rentenparameter",
                "Jahresauswahl_ATZ_Einordnung": "Ref_ATZ",
                "ATZ_Gesamtsummen": "Ref_ATZ_Summen",
                "ATZ_Einordnung_Grafik": "Ref_ATZ_Grafik",
                "ATZ_Zeitplaene": "Ref_ATZ_Zeitplaene",
                "ATZ_Ausgangszeitplan": "Ref_ATZ_Ausgang",
                "ATZ_Undatierte_Bestandseffekte": "Ref_ATZ_Undatiert",
            }
            for name, audit_name in audit_sheets.items():
                if name in tables:
                    tables[audit_name] = tables.pop(name)
        from components.org_simulation_yearly import LABELS
        tables["Jahresauswahl_Details"]["Ereignisart"] = tables["Jahresauswahl_Details"]["Ereignisart"].map(
            lambda value: LABELS.get(value, value))
        info["Auswahl"] = {"Ebene": yearly_view["dimension"], "Kennzahl": yearly_view["metric"],
                          "Jahre": yearly_view["periods"]["Jahr"].tolist(),
                          "Organisationen": yearly_view["table"].index.tolist(),
                          "Ereignisarten": yearly_view.get("event_types", [])}
    else:
        info["Auswahl"] = "Keine darstellbare Jahresauswahl; vollständige Tabellen sind enthalten."
    if monte_carlo:
        reference_names = (
            "Jahre_OE_vollständig", "Jahre_Cluster_vollständig",
            "Gesamt_OE_Ereignisse", "Gesamt_Cluster_Ereignisse",
            "Jahres_Ereignisbasis", "OE_Cluster_Laufzuordnung",
        )
        for name in reference_names:
            if name in tables:
                tables[f"Ref_{name}"] = tables.pop(name)
        info["Vollständige Tabellen"] = (
            "Gesamter Referenzlauf (Seed 0); Mehrlaufwerte stehen in den MC-Auswahlblättern."
        )
    context["Jahresauswertung"] = info


def append_stock_export(tables, context, summary, stock_view, *, monte_carlo=None):
    bridge = summary.get("stock_reconciliation") if summary else None
    signature = context.get("Laufkennung")
    if not signature or not summary or summary.get("run_signature") != signature or not bridge or bridge.get("run_signature") != signature:
        context["Bestandsüberleitung"] = {"Status": "Keine zum Lauf passende Überleitung verfügbar"}
        return
    context["Bestandsüberleitung"] = {"Status": bridge["status"], "Grund": bridge["reason"],
        "Laufkennung": signature, "MAK-Toleranz": bridge["mak_tolerance"],
        "Bedeutung": "Ausgangsbestand plus technische Prognosebasis-Effekte, Berichtswirkung aller Abgangsereignisse und belegte undatierte ATZ-Aufbereitungseffekte. Köpfe exakt, MAK intern ungerundet geprüft. Unbelegte Differenzen bleiben Abweichungen.",
        "Betrachtungsraum": "Gesamter gespeicherter Lauf, unabhängig von Bestands- und Ereignisfiltern."}
    if not bridge["checks"].empty:
        tables["Bestand_Endwertabgleich"] = bridge["checks"].copy(deep=True)
    diagnostics = bridge.get("person_diagnostics")
    if diagnostics is not None and not diagnostics.empty:
        tables["Bestand_Personendiagnose"] = diagnostics.copy(deep=True)
    documented = bridge.get("documented_effects")
    if documented is not None and not documented.empty:
        tables["Bestand_Belegte_Aufbereitung"] = documented.copy(deep=True)
    for key, label in (("basis_effects", "Bestand_Prognosebasiseffekte"), ("reporting_events", "Bestand_Reportingereignisse")):
        if key in bridge:
            tables[label] = bridge[key].copy(deep=True)
    for key, label in (("baseline_snapshot", "Bestand_Ausgangsbasis_Lauf"), ("final_snapshot", "Bestand_Endbasis_Lauf")):
        if key in summary.get("stock_basis", {}):
            tables[label] = summary["stock_basis"][key].copy(deep=True)
    if monte_carlo is None:
        monte_carlo = bool(stock_view and "monte_carlo_bands" in stock_view)
    if bridge["status"] == "Abgestimmt":
        tables["Bestand_Überleitung_vollständig"] = bridge["annual"].copy(deep=True)
    if monte_carlo:
        for name in list(tables):
            if name.startswith("Bestand_"):
                tables[f"Ref_{name}"] = tables.pop(name)
    if bridge["status"] != "Abgestimmt":
        return
    if stock_view and stock_view.get("run_signature") == signature:
        if "monte_carlo_bands" in stock_view:
            bands = stock_view["monte_carlo_bands"].rename(
                columns={"mean": "Mittelwert", "lower": "P10", "upper": "P90"}).copy(deep=True)
            tables["MC_Jahresbestand_Auswahl"] = bands
            context["Monte-Carlo Jahresbestand"] = dict(stock_view["monte_carlo_metadata"])
            tables["MC_Jahresbestand_Info"] = pd.DataFrame(
                list(stock_view["monte_carlo_metadata"].items()), columns=["Merkmal", "Wert"])
            value = f"Bestand-{stock_view['metric']}"
            tables["Bestand_Auswahl_Grafik"] = bands[["Stichtag", "Organisation", "Mittelwert", "P10", "P90"]].rename(
                columns={"Mittelwert": value})
            tables["Bestand_Auswahl_Matrix"] = bands.pivot(
                index="Organisation", columns="Stichtag", values="Mittelwert").reset_index()
            tables["Ref_Bestand_Auswahl_Grafik"] = stock_view["chart"].copy(deep=True)
            tables["Ref_Bestand_Auswahl_Matrix"] = stock_view["table"].reset_index()
            if "summary_table" in stock_view:
                tables["Ref_Bestand_Auswahl_Summen"] = stock_view["summary_table"].reset_index()
        else:
            tables["Bestand_Auswahl_Grafik"] = stock_view["chart"].copy(deep=True)
            tables["Bestand_Auswahl_Matrix"] = stock_view["table"].reset_index()
            if "summary_table" in stock_view:
                tables["Bestand_Auswahl_Summen"] = stock_view["summary_table"].reset_index()
        context["Bestandsüberleitung"]["Auswahl"] = {k: stock_view[k] for k in ("level", "metric", "organizations", "years")}


def render_org_simulation_export(tables, context, *, current, baseline, display_orgs, compact, view,
                                 event_summary=None, yearly_view=None, stock_view=None,
                                 monte_carlo_ensemble=None):
    import streamlit as st
    from components.ui_compat import lazy_excel_download_button_compat

    if tables is None:
        return
    if monte_carlo_ensemble is None:
        plan = build_plan_comparison(current, baseline, display_orgs, compact)
    else:
        plan = build_monte_carlo_plan_comparison(
            current, baseline, display_orgs, compact, monte_carlo_ensemble,
            view.get("Filter"))
    source_columns = ["Organisationseinheit", "Kürzel OrgEinheit", "Planstelle", "PersNr", "Personalnummer",
                      "Is_Vacant", "Soll_FTE", "MAK_Reporting", "MAK_Calculated", "MAK", "Sollarbeitszeit"]
    # Saved snapshot rows allow capacity values to be traced without reloading originals.
    with st.expander("IST und Simulation gegenüber dem Ausgangsstellenplan", expanded=False):
        st.caption("SOLL-MAK stammen aus dem gespeicherten Ausgangsstellenplan im aktuellen Filterkontext. "
                   "Köpfe und Planstellenzeilen sind unterschiedliche Größen; es werden keine SOLL-Köpfe aus Zeilenzahlen abgeleitet.")
        if monte_carlo_ensemble is not None:
            st.caption("Simulation und Simulation minus SOLL: Mittelwert aller Läufe mit P10/P90. "
                       "SOLL und Ausgangs-IST sind feste Vergleichswerte.")
        if plan.empty:
            st.info("Kein auswertbarer gespeicherter Ausgangsstand für diesen Ausschnitt verfügbar.")
        else:
            st.dataframe(plan, hide_index=True, use_container_width=True)
            if baseline is not None and "Soll_FTE" not in baseline.columns:
                st.info("SOLL-MAK sind im gespeicherten Ausgangsstand nicht verfügbar.")
    frozen = deepcopy(context)
    frozen["Anzeige"] = deepcopy(view)
    frozen["Anzeige"]["Organisationseinheiten"] = list(display_orgs)
    frozen["Jahresquelle_Object_Fingerprint"] = id(event_summary) if event_summary is not None else None
    frozen["Jahresauswahl_Fingerprint"] = (
        None if yearly_view is None else {
            "Ebene": yearly_view.get("dimension"), "Kennzahl": yearly_view.get("metric"),
            "Jahre": yearly_view.get("periods", pd.DataFrame()).get("Jahr", pd.Series(dtype=int)).tolist(),
            "Organisationen": yearly_view.get("table", pd.DataFrame()).index.tolist(),
            "Ereignisarten": yearly_view.get("event_types", []),
        }
    )
    frozen["Bestandsauswahl_Fingerprint"] = None if stock_view is None else {
        key: stock_view.get(key) for key in ("level", "metric", "organizations", "years")
    }
    # Fingerprint rendered data without constructing the full workbook tables.
    basis_name = "Ref_Simulationsbasis_gefiltert" if monte_carlo_ensemble is not None else "Simulationsbasis_gefiltert"
    fingerprint = export_fingerprint({**tables, "IST_Simulation_SOLL": plan,
                                      basis_name: current,
                                      **({"Ausgangsbasis_gefiltert": baseline} if baseline is not None else {})}, frozen)

    def build_payload():
        export_tables = {name: frame.copy(deep=True) for name, frame in tables.items()}
        export_tables["IST_Simulation_SOLL"] = plan.copy(deep=True)
        export_tables[basis_name] = current[[c for c in source_columns if c in current]].copy()
        if baseline is not None:
            export_tables["Ausgangsbasis_gefiltert"] = baseline[[c for c in source_columns if c in baseline]].copy()
        export_context = deepcopy(frozen)
        export_context.pop("Jahresquelle_Object_Fingerprint", None)
        export_context.pop("Jahresauswahl_Fingerprint", None)
        export_context.pop("Bestandsauswahl_Fingerprint", None)
        append_yearly_export(export_tables, export_context, event_summary, yearly_view,
                             monte_carlo=monte_carlo_ensemble is not None)
        append_stock_export(export_tables, export_context, event_summary, stock_view,
                            monte_carlo=monte_carlo_ensemble is not None)
        return build_org_simulation_export_bytes(export_tables, export_context)

    lazy_excel_download_button_compat(
        label="Gesamte OE-Simulationsanalyse als Excel exportieren",
        data_builder=build_payload,
        file_name="OE_Simulation_Gesamtexport.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="org_simulation_full_export", fingerprint=fingerprint,
        use_container_width=True,
    )
