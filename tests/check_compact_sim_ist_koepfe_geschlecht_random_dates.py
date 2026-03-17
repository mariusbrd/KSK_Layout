"""
Reproduzierbarer Mehrfach-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Koepfe -> Geschlecht

Es werden vier zufaellige, aber seed-feste Zukunftsdaten zwischen
01.01.2026 und 31.12.2028 geprueft.
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from abgaenge.params import default_params as default_abgaenge_params
from config.settings import DEFAULT_COHORTS
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.kpi_engine import compute_headcount, get_unique_employees
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from zugaenge.params import default_params as default_zugaenge_params


warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime").setLevel(logging.ERROR)

RANDOM_SEED = 42
NUM_DATES = 4
RANGE_START = pd.Timestamp("2026-01-01")
RANGE_END = pd.Timestamp("2028-12-31")


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


def _pick_random_dates() -> list[pd.Timestamp]:
    rng = np.random.default_rng(RANDOM_SEED)
    total_days = int((RANGE_END - RANGE_START).days)
    offsets = sorted(rng.choice(total_days + 1, size=NUM_DATES, replace=False).tolist())
    return [RANGE_START + pd.Timedelta(days=int(offset)) for offset in offsets]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _run_single_check(
    *,
    target_date: pd.Timestamp,
    base_date: pd.Timestamp,
    snapshot_df: pd.DataFrame,
    df_atz: pd.DataFrame,
    compact,
) -> dict:
    result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
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

    breakdown_df = compact.create_breakdown_table(emp_df, "Geschlecht", "Headcount")
    if "Hinweis" in breakdown_df.columns:
        raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

    breakdown_counts = {
        str(row["Geschlecht"]): int(row["IST"])
        for _, row in breakdown_df.iterrows()
    }
    breakdown_total = int(breakdown_df["IST"].sum())
    summary = compact.analyze_ist_koepfe_data(prepared_df)
    summary_labels = {item["label"]: item["value"] for item in summary.get("kennzahlen", [])}

    _assert(result.metadata.get("used_simulation") is True, f"Simulation wurde fuer {target_date:%d.%m.%Y} nicht aktiv ausgefuehrt.")
    _assert(total_heads == len(unique_emp), f"Headcount und deduplizierte Mitarbeitendenzahl weichen fuer {target_date:%d.%m.%Y} ab.")
    _assert("Geschlecht" in unique_emp.columns, f"Spalte 'Geschlecht' fehlt fuer {target_date:%d.%m.%Y}.")
    _assert(missing_gender == 0, f"Es gibt Mitarbeitende ohne Geschlecht fuer {target_date:%d.%m.%Y}.")
    _assert(breakdown_total == total_heads, f"Geschlecht-Breakdown summiert sich fuer {target_date:%d.%m.%Y} nicht auf Gesamt-Koepfe.")
    _assert(breakdown_counts.get("w", 0) == female_count, f"Frauenanzahl im Breakdown stimmt fuer {target_date:%d.%m.%Y} nicht.")
    _assert(breakdown_counts.get("m", 0) == male_count, f"Maenneranzahl im Breakdown stimmt fuer {target_date:%d.%m.%Y} nicht.")
    _assert(breakdown_counts.get("d", 0) == diverse_count, f"Divers-Anzahl im Breakdown stimmt fuer {target_date:%d.%m.%Y} nicht.")
    _assert(len(breakdown_counts) >= 2, f"Geschlecht-Breakdown ist fuer {target_date:%d.%m.%Y} unplausibel schmal.")
    _assert("Frauenanteil" in summary_labels, f"Management Summary enthaelt fuer {target_date:%d.%m.%Y} keinen Frauenanteil.")

    return {
        "target_date": target_date,
        "total_heads": total_heads,
        "female_count": female_count,
        "male_count": male_count,
        "diverse_count": diverse_count,
        "female_rate": female_rate,
        "breakdown_counts": breakdown_counts,
    }


def main() -> None:
    _init_state_from_app_defaults()

    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    compact = load_compact_page_module()
    snapshot_df, _, _, _ = load_and_prepare_data()
    df_atz = _load_atz_input()
    target_dates = _pick_random_dates()

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Seed           : {RANDOM_SEED}")
    print(f"Pruefbereich   : {RANGE_START:%d.%m.%Y} bis {RANGE_END:%d.%m.%Y}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST Koepfe -> Geschlecht")
    print("")

    for idx, target_date in enumerate(target_dates, start=1):
        result = _run_single_check(
            target_date=target_date,
            base_date=base_date,
            snapshot_df=snapshot_df,
            df_atz=df_atz,
            compact=compact,
        )
        print(f"[{idx}] {result['target_date']:%d.%m.%Y}")
        print(f"    Gesamt Koepfe : {result['total_heads']}")
        print(f"    Frauen        : {result['female_count']}")
        print(f"    Maenner       : {result['male_count']}")
        print(f"    Divers        : {result['diverse_count']}")
        print(f"    Frauenanteil  : {result['female_rate']:.4%}")
        print(f"    Breakdown     : {result['breakdown_counts']}")
        print("")

    print("OK: Geschlecht-Checks fuer 4 zufaellige Zukunftsdaten erfolgreich.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
