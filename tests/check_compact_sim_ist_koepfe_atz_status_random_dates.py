"""
Mehrfach-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Koepfe -> ATZ-Status

Es werden vier reproduzierbar zufaellige Zukunftsdaten im Bereich
01.01.2026 bis 31.12.2028 geprueft.
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


SEED = 42
N_DATES = 4
DATE_START = pd.Timestamp("2026-01-01")
DATE_END = pd.Timestamp("2028-12-31")
ATZ_COL = "ATZ_Status"
VALID_ATZ_STATUS = {"Kein ATZ", "Arbeitsphase", "Freistellungsphase"}
FORECAST_PREFIXES = ("NH_", "AZ_", "TR_")

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


def _pick_random_dates() -> list[pd.Timestamp]:
    rng = random.Random(SEED)
    horizon = (DATE_END - DATE_START).days
    offsets = sorted(rng.sample(range(horizon + 1), N_DATES))
    return [DATE_START + pd.Timedelta(days=offset) for offset in offsets]


def main() -> None:
    _init_state_from_app_defaults()

    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    compact = load_compact_page_module()
    random_dates = _pick_random_dates()

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Seed           : {SEED}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST Koepfe -> ATZ-Status")
    print("")

    snapshot_df, _, _, _ = load_and_prepare_data()
    df_atz = _load_atz_input()

    for idx, target_date in enumerate(random_dates, start=1):
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

        breakdown_df = compact.create_breakdown_table(emp_df, ATZ_COL, "Headcount")
        if "Hinweis" in breakdown_df.columns:
            raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

        total_heads = compute_headcount(emp_df)
        breakdown_counts = {str(row[ATZ_COL]): int(row["IST"]) for _, row in breakdown_df.iterrows()}
        expected_counts = (
            unique_emp.groupby(ATZ_COL, observed=True)["PersNr"]
            .nunique()
            .reset_index(name="IST")
        )
        expected_counts = {str(row[ATZ_COL]): int(row["IST"]) for _, row in expected_counts.iterrows()}

        invalid_count = int((~unique_emp[ATZ_COL].isin(VALID_ATZ_STATUS)).sum())
        forecast_unique = unique_emp[unique_emp["PersNr"].astype(str).str.startswith(FORECAST_PREFIXES)].copy()
        forecast_atz_counts = forecast_unique[ATZ_COL].astype(str).value_counts(dropna=False).to_dict()

        print(f"[{idx}] {target_date:%d.%m.%Y}")
        print(f"  Koepfe    : {total_heads}")
        print(f"  Breakdown : {breakdown_counts}")

        _assert(result.metadata.get("used_simulation") is True, f"Simulation war fuer {target_date:%d.%m.%Y} nicht aktiv.")
        _assert(int(breakdown_df["IST"].sum()) == total_heads, f"Breakdown summiert sich fuer {target_date:%d.%m.%Y} nicht auf Gesamt-Koepfe.")
        _assert(breakdown_counts == expected_counts, f"Breakdown stimmt fuer {target_date:%d.%m.%Y} nicht mit deduplizierter Mitarbeitersicht ueberein.")
        _assert(invalid_count == 0, f"Ungueltige ATZ-Statuswerte fuer {target_date:%d.%m.%Y} gefunden.")
        _assert(forecast_atz_counts.get("Kein ATZ", 0) == len(forecast_unique), f"Forecast-Zugaenge haben fuer {target_date:%d.%m.%Y} unerwartete ATZ-Statuswerte.")

    print("")
    print(f"OK: ATZ-Status-Checks fuer {N_DATES} zufaellige Zukunftsdaten erfolgreich.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
