"""
Reproduzierbarer Mehrfach-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> EUR -> Beschaeftigungsstatus

Es werden zehn zufaellige, aber seed-feste Zukunftsdaten zwischen
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
from dataloader.kpi_engine import get_unique_employees
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from zugaenge.params import default_params as default_zugaenge_params


warnings.filterwarnings("ignore", category=FutureWarning)
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime").setLevel(logging.ERROR)

RANDOM_SEED = 42
NUM_DATES = 10
RANGE_START = pd.Timestamp("2026-01-01")
RANGE_END = pd.Timestamp("2028-12-31")


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


def _pick_random_dates() -> list[pd.Timestamp]:
    rng = np.random.default_rng(RANDOM_SEED)
    total_days = int((RANGE_END - RANGE_START).days)
    offsets = sorted(rng.choice(total_days + 1, size=NUM_DATES, replace=False).tolist())
    return [RANGE_START + pd.Timedelta(days=int(offset)) for offset in offsets]


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
    future_prepared = compact.prepare_compact_data(result.future_snapshot_df)
    future_emp = future_prepared[~future_prepared["Is_Vacant"]].copy()
    future_unique = get_unique_employees(future_emp).copy()
    status_col = "Beschäftigungsstatus"

    breakdown_df = compact.create_breakdown_table(future_emp, status_col, "Total_Cost_Year")
    if "Hinweis" in breakdown_df.columns:
        raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

    breakdown_values = {
        str(row[status_col]): float(row["IST"])
        for _, row in breakdown_df.iterrows()
    }
    breakdown_total = float(breakdown_df["IST"].sum())
    total_cost = float(compact.get_ist_eur(future_emp))
    total_cost_raw = float(future_unique["Total_Cost_Year"].fillna(0.0).sum())

    expected_df = (
        future_unique.groupby(status_col, observed=True)["Total_Cost_Year"]
        .sum()
        .reset_index(name="IST")
    )
    expected_values = {
        str(row[status_col]): float(row["IST"])
        for _, row in expected_df.iterrows()
    }

    missing_unknown_mask = (
        future_unique[status_col].isna() |
        future_unique[status_col].astype(str).str.strip().isin(["", "nan", "(unbekannt)"])
    )
    missing_forecast = int(
        future_unique.loc[missing_unknown_mask, "PersNr"].astype(str).str.startswith(("NH_", "AZ_", "TR_")).sum()
    )

    _assert(result.metadata.get("used_simulation") is True, f"Simulation wurde fuer {target_date:%d.%m.%Y} nicht aktiv ausgefuehrt.")
    _assert(abs(total_cost - total_cost_raw) < 1e-6, f"Gesamt-EUR und deduplizierte Gesamt-EUR stimmen fuer {target_date:%d.%m.%Y} nicht ueberein.")
    _assert(abs(breakdown_total - total_cost_raw) < 1e-6, f"Beschaeftigungsstatus-Breakdown summiert sich nicht auf Gesamt-EUR roh fuer {target_date:%d.%m.%Y}.")
    _assert(set(breakdown_values.keys()) == set(expected_values.keys()), f"Chart-Kategorien und deduplizierte Kategorien unterscheiden sich fuer {target_date:%d.%m.%Y}.")
    for label, expected in expected_values.items():
        actual = breakdown_values.get(label, 0.0)
        _assert(abs(actual - expected) < 1e-6, f"EUR fuer Status '{label}' stimmt fuer {target_date:%d.%m.%Y} nicht mit deduplizierter Mitarbeitersicht ueberein.")
    _assert(missing_forecast == 0, f"Simulierte Zugaenge haben fuer {target_date:%d.%m.%Y} keinen gueltigen Beschaeftigungsstatus in der EUR-Sicht.")

    return {
        "target_date": target_date,
        "total_cost_raw": total_cost_raw,
        "breakdown_values": breakdown_values,
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
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST EUR -> Beschaeftigungsstatus")
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
        print(f"    Gesamt EUR  : {result['total_cost_raw']:.2f}")
        print(f"    Breakdown   : {result['breakdown_values']}")
        print("")

    print("OK: EUR-Beschaeftigungsstatus-Checks fuer 10 zufaellige Zukunftsdaten erfolgreich.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
