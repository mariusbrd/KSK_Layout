"""
Echtdaten-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Koepfe -> Geschlecht

Ziel-Stichtag ist fest auf 31.12.2026 gesetzt.
Das Script verwendet bewusst denselben Berechnungspfad wie die Seite:

1. Basis-Snapshot laden
2. Zukunftsbestand simulieren
3. Kompakt-Daten vorbereiten
4. IST-Koepfe auf Mitarbeiterebene dedupliziert auswerten
5. Geschlecht-Breakdown ueber create_breakdown_table("Geschlecht", "Headcount") pruefen

Bei Inkonsistenzen beendet sich das Script mit Exit-Code 1.
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

from config.settings import DEFAULT_COHORTS
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.kpi_engine import compute_headcount, get_unique_employees
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from zugaenge.params import default_params as default_zugaenge_params
from abgaenge.params import default_params as default_abgaenge_params


TARGET_DATE = pd.Timestamp("2026-12-31")

warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime").setLevel(logging.ERROR)


def _init_state_from_app_defaults() -> None:
    # Spiegelung der fuer diese Auswertung relevanten Defaults aus app.py.
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


def _classify_missing_gender(unique_emp: pd.DataFrame) -> dict[str, int]:
    if "Geschlecht" not in unique_emp.columns:
        return {}

    missing = unique_emp[unique_emp["Geschlecht"].isna()].copy()
    if missing.empty or "PersNr" not in missing.columns:
        return {}

    persnr = missing["PersNr"].astype(str)
    return {
        "new_hires": int(persnr.str.startswith("NH_").sum()),
        "azubis": int(persnr.str.startswith("AZ_").sum()),
        "trainees": int(persnr.str.startswith("TR_").sum()),
        "other": int((~persnr.str.startswith(("NH_", "AZ_", "TR_"))).sum()),
    }


def main() -> None:
    _init_state_from_app_defaults()

    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    compact = load_compact_page_module()

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Ziel-Stichtag  : {TARGET_DATE:%d.%m.%Y}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST Koepfe -> Geschlecht")

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

    total_heads = compute_headcount(emp_df)
    female_count = int((unique_emp["Geschlecht"] == "w").sum()) if "Geschlecht" in unique_emp.columns else 0
    male_count = int((unique_emp["Geschlecht"] == "m").sum()) if "Geschlecht" in unique_emp.columns else 0
    diverse_count = int((unique_emp["Geschlecht"] == "d").sum()) if "Geschlecht" in unique_emp.columns else 0
    missing_gender = int(unique_emp["Geschlecht"].isna().sum()) if "Geschlecht" in unique_emp.columns else total_heads
    female_rate = female_count / total_heads if total_heads else 0.0
    missing_sources = _classify_missing_gender(unique_emp)

    breakdown_df = compact.create_breakdown_table(emp_df, "Geschlecht", "Headcount")
    if "Hinweis" in breakdown_df.columns:
        raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

    breakdown_counts = {
        str(row["Geschlecht"]): int(row["IST"])
        for _, row in breakdown_df.iterrows()
    }
    breakdown_total = int(breakdown_df["IST"].sum())
    summary = compact.analyze_ist_koepfe_data(prepared_df)

    print("")
    print("Ergebnisse")
    print(f"- Simulationsmodus aktiv : {result.metadata.get('used_simulation')}")
    print(f"- Gesamt Koepfe         : {total_heads}")
    print(f"- Frauen                : {female_count}")
    print(f"- Maenner               : {male_count}")
    print(f"- Divers                : {diverse_count}")
    print(f"- Ohne Geschlecht       : {missing_gender}")
    print(f"- Frauenanteil          : {female_rate:.4%}")
    print(f"- Breakdown             : {breakdown_counts}")
    if missing_sources:
        print(f"- Fehlende Geschlechter : {missing_sources}")
    print("")

    _assert(result.metadata.get("used_simulation") is True, "Simulation wurde fuer 31.12.2026 nicht aktiv ausgefuehrt.")
    _assert(total_heads == len(unique_emp), "Headcount und deduplizierte Mitarbeitendenzahl weichen ab.")
    _assert("Geschlecht" in unique_emp.columns, "Spalte 'Geschlecht' fehlt im simulierten IST-Bestand.")
    _assert(missing_gender == 0, "Es gibt Mitarbeitende ohne Geschlecht; die Geschlechterdarstellung waere unvollstaendig.")
    _assert(breakdown_total == total_heads, "Geschlecht-Breakdown summiert sich nicht auf Gesamt-Koepfe.")
    _assert(breakdown_counts.get("w", 0) == female_count, "Frauenanzahl im Breakdown stimmt nicht mit KPI-Pfad ueberein.")
    _assert(breakdown_counts.get("m", 0) == male_count, "Maenneranzahl im Breakdown stimmt nicht mit KPI-Pfad ueberein.")
    _assert(breakdown_counts.get("d", 0) == diverse_count, "Divers-Anzahl im Breakdown stimmt nicht mit KPI-Pfad ueberein.")
    _assert(len(breakdown_counts) >= 2, "Geschlecht-Breakdown ist unplausibel schmal und sollte geprueft werden.")

    summary_labels = {item["label"]: item["value"] for item in summary.get("kennzahlen", [])}
    _assert("Gesamt Köpfe" in summary_labels, "Management Summary fuer IST-Koepfe enthaelt Gesamt-Koepfe nicht.")
    _assert("Frauenanteil" in summary_labels, "Management Summary fuer IST-Koepfe enthaelt Frauenanteil nicht.")

    print("OK: IST-Analyse > Koepfe > Geschlecht ist fuer 31.12.2026 konsistent.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
