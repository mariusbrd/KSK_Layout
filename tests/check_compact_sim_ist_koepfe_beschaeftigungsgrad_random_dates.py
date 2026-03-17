"""
Mehrfach-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Koepfe -> Beschaeftigungsgrad

Prueft vier reproduzierbar zufaellige Zukunftsdaten gegen denselben
Berechnungspfad wie die Simulationsseite.
"""

from __future__ import annotations

import logging
from pathlib import Path
import random
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
from dataloader.kpi_engine import compute_headcount, get_unique_employees
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from zugaenge.params import default_params as default_zugaenge_params


DATE_START = pd.Timestamp("2026-01-01")
DATE_END = pd.Timestamp("2028-12-31")
DATE_COUNT = 4
RANDOM_SEED = 42
EXPECTED_ORDER = ["<25%", "25-50%", "50-75%", "75-95%", "Vollzeit"]

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


def _resolve_degree_column(df: pd.DataFrame) -> str:
    for col in df.columns:
        if col.startswith("Besch") and col.endswith("_Kat"):
            return col
    raise AssertionError("Spalte fuer Beschaeftigungsgrad_Kat wurde nicht gefunden.")


def _random_dates() -> list[pd.Timestamp]:
    rng = random.Random(RANDOM_SEED)
    horizon_days = (DATE_END - DATE_START).days
    offsets = sorted(rng.sample(range(horizon_days + 1), DATE_COUNT))
    return [DATE_START + pd.Timedelta(days=offset) for offset in offsets]


def main() -> None:
    _init_state_from_app_defaults()

    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    compact = load_compact_page_module()
    snapshot_df, _, _, _ = load_and_prepare_data()
    df_atz = _load_atz_input()

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Zufalls-Seed   : {RANDOM_SEED}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST Koepfe -> Beschaeftigungsgrad")
    print("")

    for idx, target_date in enumerate(_random_dates(), start=1):
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
        unique_emp = get_unique_employees(emp_df).copy()
        degree_col = _resolve_degree_column(prepared_df)

        breakdown_df = compact.create_breakdown_table(emp_df, degree_col, "Headcount")
        if "Hinweis" in breakdown_df.columns:
            raise AssertionError(f"{target_date:%d.%m.%Y}: Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

        total_heads = compute_headcount(emp_df)
        breakdown_counts = {
            str(row[degree_col]): int(row["IST"])
            for _, row in breakdown_df.iterrows()
        }
        breakdown_total = int(breakdown_df["IST"].sum())
        breakdown_order = breakdown_df[degree_col].astype(str).tolist()
        expected_order = [label for label in EXPECTED_ORDER if label in breakdown_order]

        if "FTE_person" in unique_emp.columns:
            unique_emp["expected_degree_cat"] = compact.categorize_employment_degree(unique_emp["FTE_person"])
        elif "BsGrd" in unique_emp.columns:
            unique_emp["expected_degree_cat"] = compact.categorize_employment_degree(unique_emp["BsGrd"] / 100.0)
        else:
            unique_emp["expected_degree_cat"] = "(unbekannt)"

        expected_missing = int(unique_emp["expected_degree_cat"].isna().sum())
        expected_df = (
            unique_emp.groupby("expected_degree_cat", observed=True)["PersNr"]
            .nunique()
            .reset_index(name="IST")
        )
        expected_counts = {
            str(row["expected_degree_cat"]): int(row["IST"])
            for _, row in expected_df.iterrows()
        }

        _assert(result.metadata.get("used_simulation") is True, f"{target_date:%d.%m.%Y}: Simulation wurde nicht aktiv ausgefuehrt.")
        _assert(expected_missing == 0, f"{target_date:%d.%m.%Y}: Es gibt Mitarbeitende ohne Beschaeftigungsgrad-Kategorie.")
        _assert(breakdown_total == total_heads, f"{target_date:%d.%m.%Y}: Breakdown summiert sich nicht auf Gesamt-Koepfe.")
        _assert(breakdown_order == expected_order, f"{target_date:%d.%m.%Y}: Chart-Reihenfolge ist ungueltig.")
        _assert(breakdown_counts == expected_counts, f"{target_date:%d.%m.%Y}: Chart-Breakdown stimmt nicht mit deduplizierter Mitarbeitersicht ueberein.")

        print(f"[{idx}] {target_date:%d.%m.%Y}")
        print(f"    Koepfe    : {total_heads}")
        print(f"    Breakdown : {breakdown_counts}")

    print("")
    print("OK: Beschaeftigungsgrad-Checks fuer 4 zufaellige Zukunftsdaten erfolgreich.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
