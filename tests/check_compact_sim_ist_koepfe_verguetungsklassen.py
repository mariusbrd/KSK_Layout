"""
Echtdaten-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Koepfe -> Verguetungsklassen

Ziel-Stichtag ist fest auf 31.12.2026 gesetzt.
Das Script prueft explizit die Stufenautomatik:

1. Basis-Snapshot laden
2. Zukunftsbestand einmal ohne und einmal mit aktivierter Stufenautomatik simulieren
3. Kompakt-Daten vorbereiten
4. Verguetungsklassen-Breakdown exakt ueber den Seitenpfad erzeugen
5. Sicherstellen, dass sich die Verteilung ueber die Stufen veraendert,
   die Gesamtkoepfe pro Entgeltgruppe aber gleich bleiben

Bei Inkonsistenzen beendet sich das Script mit Exit-Code 1.
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from unittest.mock import patch
import warnings

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from abgaenge.params import default_params as default_abgaenge_params
from config.settings import DEFAULT_COHORTS
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from zugaenge.params import default_params as default_zugaenge_params


TARGET_DATE = pd.Timestamp("2026-12-31")
SALARY_AUTOMATION_SETTINGS = {
    "enabled": True,
    "scope": "all_staff",
    "fallback_step": 4,
    "e1_entry_step": 2,
    "e2_plus_default_entry_step": 1,
    "e1_progression_years": [4, 4, 4, 4],
    "e2_plus_progression_years": [1, 2, 3, 4, 5],
    "use_tenure_as_step_proxy_for_existing_staff": True,
}

warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime").setLevel(logging.ERROR)


def _init_state_from_app_defaults() -> None:
    if "cohort_definitions" not in st.session_state:
        st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "MAK"
    if "selected_genders" not in st.session_state:
        st.session_state["selected_genders"] = ["m", "w"]
    if "selected_employment" not in st.session_state:
        st.session_state["selected_employment"] = ["Vollzeit", "Teilzeit", "Inaktiv"]
    if "selected_atz_status" not in st.session_state:
        st.session_state["selected_atz_status"] = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]
    if "selected_org_units" not in st.session_state:
        st.session_state["selected_org_units"] = []
    if "selected_cohorts" not in st.session_state:
        st.session_state["selected_cohorts"] = []
    if "selected_education" not in st.session_state:
        st.session_state["selected_education"] = []
    if "date_range" not in st.session_state:
        st.session_state["date_range"] = None
    if "global_uploads" not in st.session_state:
        st.session_state["global_uploads"] = {}


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _simulate_with_settings(snapshot_df: pd.DataFrame, df_atz: pd.DataFrame, settings: dict) -> pd.DataFrame:
    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    with patch(
        "dataloader.compact_simulation_engine._get_salary_automation_settings",
        return_value=settings,
    ):
        result = simulate_compact_snapshot(
            snapshot_df=snapshot_df,
            df_atz=df_atz,
            target_date=TARGET_DATE,
            base_date=base_date,
            abgaenge_params=default_abgaenge_params(),
            zugaenge_params=default_zugaenge_params(),
        )
    return result.future_snapshot_df


def _collect_step_changes(off_tbl: pd.DataFrame, on_tbl: pd.DataFrame) -> list[tuple[str, str, int, int]]:
    merged = off_tbl.merge(on_tbl, on="Entgeltgruppe", how="outer", suffixes=("_off", "_on")).fillna(0)
    step_labels = sorted(
        {
            col[:-4]
            for col in merged.columns
            if col.endswith("_off") and col.startswith("Stufe ")
        }
    )

    changes: list[tuple[str, str, int, int]] = []
    for _, row in merged.iterrows():
        entgeltgruppe = str(row["Entgeltgruppe"])
        for label in step_labels:
            before = int(row.get(f"{label}_off", 0))
            after = int(row.get(f"{label}_on", 0))
            if before != after:
                changes.append((entgeltgruppe, label, before, after))
    return changes


def main() -> None:
    _init_state_from_app_defaults()

    compact = load_compact_page_module()
    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    snapshot_df, _, _, _ = load_and_prepare_data()
    df_atz = load_atz_data_cached(str(ROOT), None, None, None)

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Ziel-Stichtag  : {TARGET_DATE:%d.%m.%Y}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST Koepfe -> Verguetungsklassen")

    off_snapshot = _simulate_with_settings(snapshot_df, df_atz, {**SALARY_AUTOMATION_SETTINGS, "enabled": False})
    on_snapshot = _simulate_with_settings(snapshot_df, df_atz, SALARY_AUTOMATION_SETTINGS)

    off_prepared = compact.prepare_compact_data(off_snapshot)
    on_prepared = compact.prepare_compact_data(on_snapshot)

    off_chart_df = off_prepared[off_prepared["Is_Vacant"] == False].copy()
    on_chart_df = on_prepared[on_prepared["Is_Vacant"] == False].copy()

    off_tbl = compact.create_stacked_tariff_breakdown_table(off_chart_df, "Headcount")
    on_tbl = compact.create_stacked_tariff_breakdown_table(on_chart_df, "Headcount")

    _assert("Entgeltgruppe" in off_tbl.columns and "Entgeltgruppe" in on_tbl.columns, "Breakdown-Tabelle fehlt.")
    _assert("Gesamt" in off_tbl.columns and "Gesamt" in on_tbl.columns, "Gesamt-Spalte fehlt.")

    off_totals = off_tbl[["Entgeltgruppe", "Gesamt"]].rename(columns={"Gesamt": "Gesamt_off"})
    on_totals = on_tbl[["Entgeltgruppe", "Gesamt"]].rename(columns={"Gesamt": "Gesamt_on"})
    totals = off_totals.merge(on_totals, on="Entgeltgruppe", how="outer").fillna(0)
    totals["delta"] = totals["Gesamt_on"] - totals["Gesamt_off"]

    changed_steps = _collect_step_changes(off_tbl, on_tbl)
    total_delta_groups = totals[totals["delta"] != 0].copy()

    _assert(
        len(changed_steps) > 0,
        "Stufenautomatik veraendert die Verguetungsklassen-Verteilung fuer 31.12.2026 nicht.",
    )
    _assert(
        total_delta_groups.empty,
        f"Entgeltgruppen-Gesamtsummen veraendern sich unzulaessig: {total_delta_groups.to_dict(orient='records')}",
    )

    print(f"Geaenderte Zellen (Stufe) : {len(changed_steps)}")
    for entgeltgruppe, stufe, before, after in changed_steps[:20]:
        delta = after - before
        print(f"- {entgeltgruppe} {stufe}: {before} -> {after} ({delta:+d})")

    print("OK: IST-Analyse > Koepfe > Verguetungsklassen reagiert fuer 31.12.2026 konsistent auf die Stufenautomatik.")


if __name__ == "__main__":
    main()
