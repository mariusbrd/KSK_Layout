"""
Reproduzierbarer Mehrfach-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> EUR -> Alterskohorten

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
    cohort_definitions = list(st.session_state["cohort_definitions"].keys())

    base_prepared = compact.prepare_compact_data(snapshot_df)
    result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
        base_date=base_date,
        abgaenge_params=st.session_state.get("abgaenge_params", default_abgaenge_params()),
        zugaenge_params=st.session_state.get("zugaenge_params", default_zugaenge_params()),
    )
    future_prepared = compact.prepare_compact_data(result.future_snapshot_df)

    base_emp = base_prepared[~base_prepared["Is_Vacant"]].copy()
    future_emp = future_prepared[~future_prepared["Is_Vacant"]].copy()
    base_unique = get_unique_employees(base_emp)
    future_unique = get_unique_employees(future_emp)

    total_cost = float(compact.get_ist_eur(future_emp))
    total_cost_raw = float(future_unique["Total_Cost_Year"].fillna(0.0).sum())
    breakdown_df = compact.create_breakdown_table(future_emp, "Alterskohorte", "Total_Cost_Year")
    if "Hinweis" in breakdown_df.columns:
        raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

    breakdown_total = float(breakdown_df["IST"].sum())
    breakdown_order = breakdown_df["Alterskohorte"].astype(str).tolist()
    expected_order = [label for label in cohort_definitions if label in breakdown_order]
    missing_cohort = int(future_unique["Alterskohorte"].isna().sum()) if "Alterskohorte" in future_unique.columns else len(future_unique)
    invalid_labels = sorted(set(breakdown_order) - set(cohort_definitions))

    shared = base_unique.merge(
        future_unique[["PersNr", "Alter_Jahre", "Alterskohorte"]],
        on="PersNr",
        suffixes=("_base", "_future"),
        how="inner",
    )
    shared = shared[~shared["PersNr"].astype(str).str.startswith(("NH_", "AZ_", "TR_"))].copy()
    shared["age_delta"] = shared["Alter_Jahre_future"] - shared["Alter_Jahre_base"]
    moved_cohort_count = int((shared["Alterskohorte_base"] != shared["Alterskohorte_future"]).sum())
    avg_age_delta = float(shared["age_delta"].mean()) if not shared.empty else 0.0

    _assert(result.metadata.get("used_simulation") is True, f"Simulation wurde fuer {target_date:%d.%m.%Y} nicht aktiv ausgefuehrt.")
    _assert("Alterskohorte" in future_unique.columns, f"Spalte 'Alterskohorte' fehlt fuer {target_date:%d.%m.%Y}.")
    _assert("Total_Cost_Year" in future_unique.columns, f"Spalte 'Total_Cost_Year' fehlt fuer {target_date:%d.%m.%Y} in der deduplizierten Mitarbeitersicht.")
    _assert(missing_cohort == 0, f"Es gibt Mitarbeitende ohne Alterskohorte fuer {target_date:%d.%m.%Y}.")
    _assert(not invalid_labels, f"Breakdown enthaelt ungueltige Kohortenlabels fuer {target_date:%d.%m.%Y}: {invalid_labels}")
    _assert(abs(total_cost - total_cost_raw) < 1e-6, f"Gesamt-EUR und deduplizierte Gesamt-EUR stimmen fuer {target_date:%d.%m.%Y} nicht ueberein.")
    _assert(abs(breakdown_total - total_cost_raw) < 1e-6, f"Alterskohorten-Breakdown summiert sich nicht auf Gesamt-EUR roh fuer {target_date:%d.%m.%Y}.")
    _assert(breakdown_order == expected_order, f"Die Reihenfolge der Alterskohorten stimmt fuer {target_date:%d.%m.%Y} nicht.")
    _assert(not shared.empty, f"Keine identischen Personen fuer den Vergleich Basis/Zukunft am {target_date:%d.%m.%Y}.")
    _assert((shared["age_delta"] > 0).all(), f"Mindestens eine ueberlebende Person wurde bis {target_date:%d.%m.%Y} nicht weiter gealtert.")
    _assert(moved_cohort_count > 0, f"Keine Person ist bis {target_date:%d.%m.%Y} in eine neue Alterskohorte gerueckt.")

    return {
        "target_date": target_date,
        "total_cost_raw": total_cost_raw,
        "avg_age_delta": avg_age_delta,
        "moved_cohort_count": moved_cohort_count,
        "breakdown_order": breakdown_order,
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
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST EUR -> Alterskohorten")
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
        print(f"    Gesamt EUR    : {result['total_cost_raw']:.2f}")
        print(f"    Ø Altersdelta : {result['avg_age_delta']:.4f}")
        print(f"    Kohortenwechsel : {result['moved_cohort_count']}")
        print(f"    Reihenfolge   : {', '.join(result['breakdown_order'])}")
        print("")

    print("OK: EUR-Alterskohorten-Checks fuer 10 zufaellige Zukunftsdaten erfolgreich.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
