"""
Echtdaten-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> EUR -> Geschlecht

Ziel-Stichtag ist fest auf 31.12.2026 gesetzt.
Das Script verwendet denselben Berechnungspfad wie die Seite.
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import warnings

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from abgaenge.params import default_params as default_abgaenge_params
from config.settings import DEFAULT_COHORTS
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.kpi_engine import get_unique_employees
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from zugaenge.params import default_params as default_zugaenge_params


TARGET_DATE = pd.Timestamp("2026-12-31")

warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime").setLevel(logging.ERROR)


def _init_state_from_app_defaults() -> None:
    if "cohort_definitions" not in st.session_state:
        st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "EUR"
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


def _load_atz_input() -> pd.DataFrame:
    uploads = st.session_state.get("global_uploads", {})
    up_ma = uploads.get("Mitarbeiter")
    up_atz = uploads.get("ATZ")
    up_pl = uploads.get("Planstellen")

    if up_ma:
        up_ma.seek(0)
    if up_atz:
        up_atz.seek(0)
    if up_pl:
        up_pl.seek(0)

    return load_atz_data_cached(str(ROOT), up_ma, up_atz, up_pl)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    _init_state_from_app_defaults()

    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    compact = load_compact_page_module()

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Ziel-Stichtag  : {TARGET_DATE:%d.%m.%Y}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST EUR -> Geschlecht")

    snapshot_df, _, _, _ = load_and_prepare_data()
    df_atz = _load_atz_input()

    result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=TARGET_DATE,
        base_date=base_date,
        abgaenge_params=st.session_state.get("abgaenge_params", default_abgaenge_params()),
        zugaenge_params=st.session_state.get("zugaenge_params", default_zugaenge_params()),
    )

    prepared_df = compact.prepare_compact_data(result.future_snapshot_df)
    emp_df = prepared_df[~prepared_df["Is_Vacant"]].copy() if "Is_Vacant" in prepared_df.columns else prepared_df.copy()
    unique_emp = get_unique_employees(emp_df)

    total_cost = float(compact.get_ist_eur(emp_df))
    total_cost_raw = float(unique_emp["Total_Cost_Year"].fillna(0.0).sum())
    total_heads = len(unique_emp)
    female_cost = float(unique_emp.loc[unique_emp["Geschlecht"] == "w", "Total_Cost_Year"].fillna(0.0).sum())
    male_cost = float(unique_emp.loc[unique_emp["Geschlecht"] == "m", "Total_Cost_Year"].fillna(0.0).sum())
    diverse_cost = float(unique_emp.loc[unique_emp["Geschlecht"] == "d", "Total_Cost_Year"].fillna(0.0).sum())
    missing_gender = int(unique_emp["Geschlecht"].isna().sum()) if "Geschlecht" in unique_emp.columns else total_heads

    breakdown_df = compact.create_breakdown_table(emp_df, "Geschlecht", "Total_Cost_Year")
    if "Hinweis" in breakdown_df.columns:
        raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

    breakdown_values = {
        str(row["Geschlecht"]): float(row["IST"])
        for _, row in breakdown_df.iterrows()
    }
    breakdown_total = float(breakdown_df["IST"].sum())
    summary = compact.analyze_ist_eur_data(prepared_df)

    print("")
    print("Ergebnisse")
    print(f"- Simulationsmodus aktiv : {result.metadata.get('used_simulation')}")
    print(f"- Gesamt EUR            : {total_cost:.2f}")
    print(f"- Gesamt EUR roh        : {total_cost_raw:.2f}")
    print(f"- Gesamt Koepfe         : {total_heads}")
    print(f"- EUR Frauen            : {female_cost:.2f}")
    print(f"- EUR Maenner           : {male_cost:.2f}")
    print(f"- EUR Divers            : {diverse_cost:.2f}")
    print(f"- Ohne Geschlecht       : {missing_gender}")
    print(f"- Breakdown             : {breakdown_values}")
    print("")

    _assert(result.metadata.get("used_simulation") is True, "Simulation wurde fuer 31.12.2026 nicht aktiv ausgefuehrt.")
    _assert("Geschlecht" in unique_emp.columns, "Spalte 'Geschlecht' fehlt im simulierten IST-Bestand.")
    _assert("Total_Cost_Year" in unique_emp.columns, "Spalte 'Total_Cost_Year' fehlt in der deduplizierten Mitarbeitersicht.")
    _assert(missing_gender == 0, "Es gibt Mitarbeitende ohne Geschlecht; die EUR-Geschlechterdarstellung waere unvollstaendig.")
    _assert(abs(total_cost - total_cost_raw) < 1e-6, "Gesamt-EUR und deduplizierte Gesamt-EUR stimmen nicht ueberein.")
    _assert(abs(breakdown_total - total_cost_raw) < 1e-6, "Geschlecht-Breakdown summiert sich nicht auf Gesamt-EUR roh.")
    _assert(abs(breakdown_values.get("w", 0.0) - female_cost) < 1e-6, "Frauen-EUR im Breakdown stimmt nicht mit dedupliziertem KPI-Pfad ueberein.")
    _assert(abs(breakdown_values.get("m", 0.0) - male_cost) < 1e-6, "Maenner-EUR im Breakdown stimmt nicht mit dedupliziertem KPI-Pfad ueberein.")
    _assert(abs(breakdown_values.get("d", 0.0) - diverse_cost) < 1e-6, "Divers-EUR im Breakdown stimmt nicht mit dedupliziertem KPI-Pfad ueberein.")

    summary_labels = {item["label"]: item["value"] for item in summary.get("kennzahlen", [])}
    _assert("Gesamt Kosten" in summary_labels, "Management Summary fuer IST-EUR enthaelt Gesamt Kosten nicht.")
    _assert("Kosten/Kopf" in summary_labels, "Management Summary fuer IST-EUR enthaelt Kosten/Kopf nicht.")
    _assert("Kosten/MAK" in summary_labels, "Management Summary fuer IST-EUR enthaelt Kosten/MAK nicht.")

    print("OK: IST-Analyse > EUR > Geschlecht ist fuer 31.12.2026 konsistent.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
