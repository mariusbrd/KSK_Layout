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
            "anschließend auf 'Simulation validieren' freigeben.",
            tone="warning",
        )
        st.page_link("pages/12_Simulation_validieren.py", label="Zur Simulation validieren")
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

    if prepared_df.empty:
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
        methodology_text=(
            "Die Seite zeigt ausschließlich den zuletzt berechneten Future-Snapshot aus "
            "'Kompakt plus Simulation'. Es werden keine IST-Daten geladen und keine eigene "
            "Simulation berechnet.\n\n"
            "Alle KPIs, Ranglisten und Detailblöcke verwenden denselben Jobgruppen-Analysepfad "
            "wie die bestehende IST-Seite, aber mit dem simulierten Zielbild als Datenbasis.\n\n"
            "Filter und Exklusionen aus der Sidebar definieren den Betrachtungsraum innerhalb "
            "dieses Simulationsergebnisses."
        ),
        yearly_renderer=lambda: render_jobfamily_simulation_yearly(
            view.event_context,
            view.event_summary,
            run_signature=view.event_summary.get("run_signature"),
        ),
    )


if __name__ == "__main__":
    main()
