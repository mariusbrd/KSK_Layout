"""
Streamlit page: Organisationseinheiten-Analyse (Simulation).
"""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
import sys

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

    if prepared_df.empty:
        render_page_header(
            "Organisationseinheiten-Analyse Simulation",
            f"Future-Sicht auf den simulierten Ziel-Stichtag {target_label}.",
        )
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
            "Die Seite zeigt ausschließlich den zuletzt berechneten Future-Snapshot aus "
            "'Kompakt plus Simulation'. Es werden keine IST-Daten geladen und keine eigene "
            "Simulation berechnet.\n\n"
            "Standardmäßig zeigen alle Auswertungen den Simulationsstand. Der Vergleichsschalter "
            "blendet je Grafik den Status-Quo-Snapshot aus dem Simulationslauf ein und ergänzt "
            "Delta-Spalten sowie simulierte Abgänge als Treiberinformation.\n\n"
            "Die angezeigten Organisationseinheiten werden nach Mitarbeiterzahl im simulierten Bestand "
            "sortiert. Filter und Exklusionen definieren den Betrachtungsraum innerhalb dieses "
            "Simulationsergebnisses."
        ),
        comparison_df=status_quo_df,
        comparison_label="IST",
        enable_comparison_toggle=True,
        departure_events_df=_get_departure_events(view.audit_tables),
        event_summary=view.event_summary if view.version != f"{view.signature}::{view.instance}::original" else _get_current_event_summary(),
        export_context={
            "Lauf": deepcopy(metadata),
            "Laufkennung": view.event_summary.get("run_signature"),
            "Ergebnisversion": view.version,
            "Laufeinstellungen": deepcopy(st.session_state.get("compact_sim_export_context", {})),
            "abgaenge_params": deepcopy(st.session_state.get("compact_sim_abgaenge_params")),
            "zugaenge_params": deepcopy(st.session_state.get("compact_sim_zugaenge_params")),
            "Clusterquelle": str(st.session_state.get("compact_sim_active_cluster_source", "nicht gespeichert")),
            "Parameterherkunft": "Gespeicherter Lauf; fehlende Werte werden nicht aus aktuellen Entwürfen ergänzt.",
        },
    )


if __name__ == "__main__":
    main()
