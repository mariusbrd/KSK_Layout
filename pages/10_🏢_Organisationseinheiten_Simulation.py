"""
Streamlit page: Organisationseinheiten-Analyse (Simulation).
"""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import streamlit as st

BASE_PATH = Path(__file__).resolve().parents[1]
SRC_PATH = BASE_PATH / "src"
if SRC_PATH.exists():
    sys.path.append(str(SRC_PATH))
else:
    sys.path.append(str(BASE_PATH))

from components.ui_shell import render_context_box, render_page_header
from utils.org_simulation_events import build_simulation_event_summary, RECONCILIATION_VERSION
from utils.simulation_validation import current_view
from utils.monte_carlo_org_view import approved_org_ensemble
from utils.monte_carlo_app_run import approved_representative_request
from utils.monte_carlo_representative_runs import select_representative_runs
from utils.monte_carlo_representative_data import materialize_representative_seed_detail
from utils.monte_carlo_representative_prefetch import PREFETCH_KEY, submit_detail


REPRESENTATIVE_CACHE_KEY = "org_sim_representative_details"
SCENARIOS = {
    "Konsolidiert": None,
    "Geringste Fluktuation": "best",
    "Höchste Fluktuation": "worst",
    "Typischer Verlauf": "typical",
}
SCENARIO_LABELS = {
    "Konsolidiert": "Mittelwert + P10–P90",
    "Geringste Fluktuation": "Minimum (wenigste Kündigungen)",
    "Höchste Fluktuation": "Maximum (meiste Kündigungen)",
    "Typischer Verlauf": "Mittlerer Einzellauf",
}


ORG_ANALYSIS_PAGE = BASE_PATH / "pages" / "9_🏢_Organisationseinheiten_Analyse.py"


def _load_orgunit_analysis_module():
    spec = importlib.util.spec_from_file_location(
        "orgunit_analysis_page",
        ORG_ANALYSIS_PAGE,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Organisationseinheiten-Analyse konnte nicht geladen werden: {ORG_ANALYSIS_PAGE}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _format_target_date(value) -> str:
    if value is None:
        return "unbekannt"
    try:
        return pd.Timestamp(value).strftime("%d.%m.%Y")
    except Exception:
        return "unbekannt"


def _get_departure_events(audit_tables: dict | None = None) -> pd.DataFrame:
    audit_tables = audit_tables if audit_tables is not None else st.session_state.get("compact_sim_audit_tables", {}) or {}
    events = audit_tables.get("Abgaenge_Events_Raw", pd.DataFrame())
    if events is None or events.empty:
        return pd.DataFrame()

    out = events.copy()
    out["headcount_change"] = pd.to_numeric(out.get("headcount_change", 0), errors="coerce").fillna(0)
    out = out[out["headcount_change"] < 0].copy()
    if out.empty:
        return out

    out["Abgänge"] = out["headcount_change"].abs()
    out["MAK-Verlust"] = pd.to_numeric(out.get("mak_change", 0), errors="coerce").fillna(0).abs()
    if "event_date" in out.columns:
        out["event_date"] = pd.to_datetime(out["event_date"], errors="coerce")
        out["Jahr"] = out["event_date"].dt.year
    return out


def _get_current_event_summary():
    summary = st.session_state.get("compact_sim_event_summary")
    context = st.session_state.get("compact_sim_event_context")
    signature = st.session_state.get("compact_sim_signature")
    if context and signature and context.get("run_signature") == signature and (
        not summary or summary.get("run_signature") != signature or summary.get("reconciliation_version") != RECONCILIATION_VERSION
    ):
        context = deepcopy(context)
        saved_settings = st.session_state.get("compact_sim_export_context")
        if "run_settings" not in context and saved_settings is not None:
            context["run_settings"] = deepcopy(saved_settings)
        summary = build_simulation_event_summary(context, expected_run_signature=signature)
        st.session_state["compact_sim_event_summary"] = summary
    return summary


def _representative_cache_key(ensemble, view, index):
    return (ensemble["plan_signature"], view.signature, view.instance, index)


def _start_representative_prefetch(view, ensemble):
    """Start only the distinct non-reference paths after the current page rendered."""
    request = approved_representative_request(st.session_state, view)
    if request is None:
        return
    selections = select_representative_runs(ensemble)
    cache = st.session_state.setdefault(REPRESENTATIVE_CACHE_KEY, {})
    pending = st.session_state.setdefault(PREFETCH_KEY, {})
    for index in sorted({item["index"] for item in selections.values()} - {0}):
        key = _representative_cache_key(ensemble, view, index)
        if key not in cache and key not in pending:
            pending[key] = submit_detail(request, ensemble, index)


def _selected_scenario(view, ensemble):
    """Resolve a complete selected run; leave the consolidated view untouched."""
    if ensemble is None:
        return None
    st.markdown("### Ergebnisansicht")
    st.caption(
        "Standard: Mittelwert aller Läufe mit P10–P90-Bändern. "
        "Minimum und Maximum beziehen sich auf die Zahl der Kündigungen. "
        "Der mittlere Einzellauf liegt dem durchschnittlichen Jahresverlauf am nächsten. "
        "Jede Einzellaufansicht zeigt einen vollständigen, tatsächlich berechneten Verlauf."
    )
    label = st.radio(
        "Monte-Carlo-Ansicht auswählen", list(SCENARIOS), horizontal=True,
        format_func=lambda value: SCENARIO_LABELS[value],
        key="org_sim_scenario_mode",
    )
    scenario = SCENARIOS[label]
    if scenario is None:
        return None
    selection = select_representative_runs(ensemble)[scenario]
    request = approved_representative_request(st.session_state, view)
    if request is None:
        raise ValueError("Die gespeicherten Eingaben für die Szenarioansicht fehlen oder sind veraltet. Bitte die Simulation erneut berechnen und freigeben.")
    index = selection["index"]
    if index == 0:
        detail = SimpleNamespace(
            prepared=view.prepared_df,
            context=view.event_context,
            summary=_get_current_event_summary(),
        )
    else:
        cache = st.session_state.setdefault(REPRESENTATIVE_CACHE_KEY, {})
        cache_key = _representative_cache_key(ensemble, view, index)
        if cache_key not in cache:
            pending = st.session_state.get(PREFETCH_KEY, {})
            future = pending.pop(cache_key, None)
            with st.spinner(f"Lauf {index + 1} wird für die OE-Ansicht aufbereitet …"):
                try:
                    cache[cache_key] = (future.result() if future is not None
                                        else materialize_representative_seed_detail(request, ensemble, index))
                except Exception as exc:
                    raise ValueError(f"Der ausgewählte Lauf konnte nicht geprüft werden: {exc}") from exc
        detail = cache[cache_key]
    pair = selection["seed_pair"]
    st.caption(
        f"{label}: vollständiger Lauf {index + 1} von {ensemble['count']} "
        f"(Seeds Abgänge/Zugänge {pair[0]}/{pair[1]}). "
        f"Kündigungen im Betrachtungszeitraum: {selection['quit_departures']:g}. "
        "Alle Jahreswerte und Organisationseinheiten stammen aus demselben Lauf."
    )
    return label, selection, detail


def main() -> None:
    view = current_view(st.session_state, require_approval=True)
    if view is None:
        render_page_header(
            "Organisationseinheiten-Analyse Simulation",
            "Auswertung des freigegebenen Simulationsergebnisses.",
        )
        render_context_box(
            "Keine freigegebene Simulation verfügbar",
            "Bitte zuerst eine Simulation auf 'Kompakt plus Simulation' berechnen und "
            "anschließend auf der Seite 'Simulation abnehmen' freigeben.",
            tone="warning",
        )
        st.page_link("pages/12_✅_Simulation_abnehmen.py", label="Zur Simulationsabnahme")
        return
    prepared_df = view.prepared_df
    status_quo_df = view.status_quo_df
    metadata = view.metadata
    if metadata.get("adjustment"):
        st.info("Freigegebener justierter Ergebnisstand.")
    target_date = metadata.get("target_date") or st.session_state.get("compact_sim_target_date_cached")
    target_label = _format_target_date(target_date)

    if prepared_df is None:
        render_page_header(
            "Organisationseinheiten-Analyse Simulation",
            "Future-Sicht auf die simulierte Personalsituation nach Organisationseinheiten.",
        )
        render_context_box(
            "Keine Simulation verfügbar",
            "Bitte zuerst auf der Seite 'Kompakt plus Simulation' eine Simulation berechnen. "
            "Diese Seite zeigt ausschließlich Simulationsergebnisse und fällt bewusst nicht auf IST-Daten zurück.",
            tone="warning",
        )
        return

    ensemble = approved_org_ensemble(st.session_state, view)
    saved_ensemble = st.session_state.get("monte_carlo_ensemble_result")
    if (ensemble is None and view.version == f"{view.signature}::{view.instance}::original"
            and isinstance(saved_ensemble, dict) and saved_ensemble.get("count", 0) > 1
            and saved_ensemble.get("reference_signature") == view.signature
            and saved_ensemble.get("reference_instance") == view.instance):
        st.error(
            "Die gespeicherten Monte-Carlo-Laufdaten sind für diese freigegebene "
            "OE-Ansicht unvollständig oder veraltet. Bitte Simulation neu berechnen "
            "und freigeben. Es werden keine Referenzwerte als Mehrlaufergebnis angezeigt."
        )
        return
    render_page_header(
        "Organisationseinheiten-Analyse Simulation",
        f"Future-Sicht auf den simulierten Ziel-Stichtag {target_label}.",
    )
    try:
        selected = _selected_scenario(view, ensemble)
    except (ValueError, KeyError, TypeError) as exc:
        st.error(f"Szenarioansicht nicht verfügbar: {exc}")
        return
    if selected is not None:
        scenario_label, selection, detail = selected
        prepared_df = detail.prepared
        departure_events = _get_departure_events({"Abgaenge_Events_Raw": detail.context["departure_events"]})
        event_summary = detail.summary
        ensemble = None
    else:
        scenario_label = "Konsolidiert" if ensemble is not None else "Einzellauf"
        selection = None
        departure_events = _get_departure_events(view.audit_tables)
        event_summary = (view.event_summary if view.version != f"{view.signature}::{view.instance}::original"
                         else _get_current_event_summary())
    if prepared_df.empty:
        st.info("Das vorliegende Simulationsergebnis enthält keine auswertbaren Daten.")
        return
    org_analysis = _load_orgunit_analysis_module()
    org_analysis.render_orgunit_analysis_page(
        prepared_df.copy(),
        history_df=None,
        title="Organisationseinheiten-Analyse Simulation",
        subtitle=f"Future-Sicht auf den simulierten Ziel-Stichtag {target_label}.",
        value_label="Simulation",
        methodology_text=(
            "Die Seite zeigt den freigegebenen Future-Stand aus 'Kompakt plus Simulation'. "
            "Bei mehreren Läufen ist die konsolidierte Mittelwertansicht mit P10–P90 der Standard. "
            "Die drei anderen Ansichten zeigen jeweils einen vollständigen Einzellauf.\n\n"
            "Der Vergleichsschalter "
            "blendet je Grafik den Status-Quo-Snapshot aus dem Simulationslauf ein und ergänzt "
            "Delta-Spalten sowie simulierte Abgänge als Treiberinformation.\n\n"
            "Die angezeigten Organisationseinheiten werden nach Mitarbeiterzahl im simulierten Bestand "
            "sortiert. Filter und Exklusionen definieren den Betrachtungsraum innerhalb dieses "
            "Simulationsergebnisses."
        ),
        comparison_df=status_quo_df,
        comparison_label="IST",
        enable_comparison_toggle=True,
        departure_events_df=departure_events,
        event_summary=event_summary,
        monte_carlo_ensemble=ensemble,
        show_header=False,
        export_context={
            "Lauf": deepcopy(metadata),
            "Laufkennung": event_summary.get("run_signature") if event_summary else None,
            "Ergebnisversion": view.version,
            "Szenarioansicht": scenario_label,
            "Szenario-Laufindex": selection["index"] if selection else None,
            "Szenario-Seed-Paar": selection["seed_pair"] if selection else None,
            "Szenario-Kündigungen": selection["quit_departures"] if selection else None,
            "Laufeinstellungen": deepcopy(st.session_state.get("compact_sim_export_context", {})),
            "abgaenge_params": deepcopy(st.session_state.get("compact_sim_abgaenge_params")),
            "zugaenge_params": deepcopy(st.session_state.get("compact_sim_zugaenge_params")),
            "Clusterquelle": str(st.session_state.get("compact_sim_active_cluster_source", "nicht gespeichert")),
            "Parameterherkunft": "Gespeicherter Lauf; fehlende Werte werden nicht aus aktuellen Entwürfen ergänzt.",
        },
    )
    if ensemble is not None:
        try:
            _start_representative_prefetch(view, ensemble)
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            st.warning(f"Die Einzellaufansichten konnten nicht vorab vorbereitet werden: {exc}")


if __name__ == "__main__":
    main()
