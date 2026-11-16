"""
Streamlit page: Jobgruppen-Analyse (Simulation).
"""

from __future__ import annotations

import importlib.util
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
from components.jobfamily_simulation_yearly import render_jobfamily_simulation_yearly
from utils.monte_carlo_org_view import approved_org_ensemble
from utils.simulation_validation import current_view


JOBFAMILY_ANALYSIS_PAGE = next(
    (BASE_PATH / "pages").glob("*_Jobfamily_Analyse.py"),
    BASE_PATH / "pages" / "8_💼_Jobfamily_Analyse.py",
)


def _load_jobfamily_analysis_module():
    spec = importlib.util.spec_from_file_location(
        "jobfamily_analysis_page",
        JOBFAMILY_ANALYSIS_PAGE,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Jobgruppen-Analyse konnte nicht geladen werden: {JOBFAMILY_ANALYSIS_PAGE}")

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


def main() -> None:
    view = current_view(st.session_state, require_approval=True)
    if view is None:
        render_page_header(
            "Jobgruppen-Analyse Simulation",
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
    metadata = view.metadata
    if metadata.get("adjustment"):
        st.info("Freigegebener justierter Ergebnisstand.")
    target_date = metadata.get("target_date") or st.session_state.get("compact_sim_target_date_cached")
    target_label = _format_target_date(target_date)

    if prepared_df is None:
        render_page_header(
            "Jobgruppen-Analyse Simulation",
            "Future-Sicht auf die simulierte Personalsituation nach Jobgruppen.",
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
    matching_multirun = (
        view.version == f"{view.signature}::{view.instance}::original"
        and isinstance(saved_ensemble, dict)
        and saved_ensemble.get("count", 0) > 1
        and saved_ensemble.get("reference_signature") == view.signature
        and saved_ensemble.get("reference_instance") == view.instance
    )
    if matching_multirun and ensemble is None:
        render_page_header(
            "Jobgruppen-Analyse Simulation",
            f"Future-Sicht auf den simulierten Ziel-Stichtag {target_label}.",
        )
        st.error(
            "Die gespeicherten Monte-Carlo-Laufdaten sind für diese freigegebene "
            "JF-Ansicht unvollständig oder veraltet. Bitte Simulation neu berechnen "
            "und freigeben. Es werden keine Referenzwerte als Mehrlaufergebnis angezeigt."
        )
        return

    if prepared_df.empty and ensemble is None:
        render_page_header(
            "Jobgruppen-Analyse Simulation",
            f"Future-Sicht auf den simulierten Ziel-Stichtag {target_label}.",
        )
        st.info("Das vorliegende Simulationsergebnis enthält keine auswertbaren Daten.")
        return

    jobfamily_analysis = _load_jobfamily_analysis_module()
    jobfamily_analysis.render_jobfamily_analysis_page(
        prepared_df.copy(),
        history_df=None,
        title="Jobgruppen-Analyse Simulation",
        subtitle=f"Future-Sicht auf den simulierten Ziel-Stichtag {target_label}.",
        value_label="Simulation",
        key_prefix="jobfamily_simulation",
        monte_carlo_ensemble=ensemble,
        methodology_text=((
            "Bei mehreren Läufen zeigt die Seite für aggregierte Jobgruppen- und "
            "Jahreskennzahlen Mittelwerte und empirische P10–P90-Ergebnisbänder. "
            "Die aktiven Personenfilter werden vor der Aggregation auf jeden Lauf "
            "angewendet. Der zertifizierte Jahrespositionsbestand ist fachlich von "
            "der technischen Bestandsüberleitung des Einzellaufs zu unterscheiden. "
            "Es wird kein gemittelter Personenbestand erzeugt."
        ) if ensemble is not None else (
            "Die Seite zeigt ausschließlich den zuletzt berechneten Future-Snapshot aus "
            "'Kompakt plus Simulation'. Es werden keine IST-Daten geladen und keine eigene "
            "Simulation berechnet.\n\n"
            "Alle KPIs, Ranglisten und Detailblöcke verwenden denselben Jobgruppen-Analysepfad "
            "wie die bestehende IST-Seite, aber mit dem simulierten Zielbild als Datenbasis.\n\n"
            "Filter und Exklusionen aus der Sidebar definieren den Betrachtungsraum innerhalb "
            "dieses Simulationsergebnisses."
        )),
        yearly_renderer=lambda: render_jobfamily_simulation_yearly(
            view.event_context,
            view.event_summary,
            run_signature=view.event_summary.get("run_signature"),
            monte_carlo_ensemble=ensemble,
        ),
    )


if __name__ == "__main__":
    main()
