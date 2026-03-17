"""
Reproduzierbarer Mehrfach-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> MAK -> Verguetungsklassen

Es werden dreissig zufaellige, aber seed-feste Zukunftsdaten zwischen
01.01.2026 und 31.12.2028 geprueft.

Geprueft wird je Datum:
1. Die Stufenautomatik veraendert die MAK-Verteilung ueber Entgeltgruppe x Stufe.
2. Die Gesamt-MAK je Entgeltgruppe bleibt dabei erhalten.
3. Die Gesamt-MAK der Matrix entspricht der Gesamt-MAK des simulierten Bestands.
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from unittest.mock import patch
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
RANDOM_SEED = 42
NUM_DATES = 30
RANGE_START = pd.Timestamp("2026-01-01")
RANGE_END = pd.Timestamp("2028-12-31")

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


def _pick_random_dates() -> list[pd.Timestamp]:
    rng = np.random.default_rng(RANDOM_SEED)
    total_days = int((RANGE_END - RANGE_START).days)
    offsets = sorted(rng.choice(total_days + 1, size=NUM_DATES, replace=False).tolist())
    return [RANGE_START + pd.Timedelta(days=int(offset)) for offset in offsets]


def _simulate_with_settings(
    *,
    snapshot_df: pd.DataFrame,
    df_atz: pd.DataFrame,
    target_date: pd.Timestamp,
    settings: dict,
) -> pd.DataFrame:
    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    with patch(
        "dataloader.compact_simulation_engine._get_salary_automation_settings",
        return_value=settings,
    ):
        result = simulate_compact_snapshot(
            snapshot_df=snapshot_df,
            df_atz=df_atz,
            target_date=target_date,
            base_date=base_date,
            abgaenge_params=default_abgaenge_params(),
            zugaenge_params=default_zugaenge_params(),
        )
    return result.future_snapshot_df


def _collect_step_changes(off_tbl: pd.DataFrame, on_tbl: pd.DataFrame) -> list[tuple[str, str, float, float]]:
    merged = off_tbl.merge(on_tbl, on="Entgeltgruppe", how="outer", suffixes=("_off", "_on")).fillna(0)
    step_labels = sorted(
        {
            col[:-4]
            for col in merged.columns
            if col.endswith("_off") and col.startswith("Stufe ")
        }
    )

    changes: list[tuple[str, str, float, float]] = []
    for _, row in merged.iterrows():
        entgeltgruppe = str(row["Entgeltgruppe"])
        for label in step_labels:
            before = float(row.get(f"{label}_off", 0.0))
            after = float(row.get(f"{label}_on", 0.0))
            if abs(before - after) > 1e-9:
                changes.append((entgeltgruppe, label, before, after))
    return changes


def _run_single_check(*, compact, snapshot_df: pd.DataFrame, df_atz: pd.DataFrame, target_date: pd.Timestamp) -> dict:
    off_snapshot = _simulate_with_settings(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
        settings={**SALARY_AUTOMATION_SETTINGS, "enabled": False},
    )
    on_snapshot = _simulate_with_settings(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=target_date,
        settings=SALARY_AUTOMATION_SETTINGS,
    )

    off_prepared = compact.prepare_compact_data(off_snapshot)
    on_prepared = compact.prepare_compact_data(on_snapshot)

    value_col_off = next((c for c in ("MAK_Calculated", "mak", "MAK") if c in off_prepared.columns), "FTE_assigned")
    value_col_on = next((c for c in ("MAK_Calculated", "mak", "MAK") if c in on_prepared.columns), "FTE_assigned")

    off_chart_df = off_prepared[off_prepared["Is_Vacant"] == False].copy()
    on_chart_df = on_prepared[on_prepared["Is_Vacant"] == False].copy()

    off_tbl = compact.create_stacked_tariff_breakdown_table(off_chart_df, value_col_off)
    on_tbl = compact.create_stacked_tariff_breakdown_table(on_chart_df, value_col_on)

    _assert("Entgeltgruppe" in off_tbl.columns and "Entgeltgruppe" in on_tbl.columns, "Breakdown-Tabelle fehlt.")
    _assert("Gesamt" in off_tbl.columns and "Gesamt" in on_tbl.columns, "Gesamt-Spalte fehlt.")

    off_totals = off_tbl[["Entgeltgruppe", "Gesamt"]].rename(columns={"Gesamt": "Gesamt_off"})
    on_totals = on_tbl[["Entgeltgruppe", "Gesamt"]].rename(columns={"Gesamt": "Gesamt_on"})
    totals = off_totals.merge(on_totals, on="Entgeltgruppe", how="outer").fillna(0.0)
    totals["delta"] = totals["Gesamt_on"] - totals["Gesamt_off"]

    changed_steps = _collect_step_changes(off_tbl, on_tbl)
    total_delta_groups = totals[totals["delta"].abs() > 1e-6].copy()

    overall_off = float(off_tbl["Gesamt"].sum())
    overall_on = float(on_tbl["Gesamt"].sum())
    expected_off = float(get_unique_employees(off_prepared)["MAK_Calculated"].fillna(0.0).sum())
    expected_on = float(get_unique_employees(on_prepared)["MAK_Calculated"].fillna(0.0).sum())

    _assert(len(changed_steps) > 0, f"Stufenautomatik veraendert die MAK-Verguetungsklassen fuer {target_date:%d.%m.%Y} nicht.")
    _assert(total_delta_groups.empty, f"Entgeltgruppen-MAK veraendert sich fuer {target_date:%d.%m.%Y} unzulaessig: {total_delta_groups.to_dict(orient='records')}")
    _assert(abs(overall_off - expected_off) < 1e-6, f"Matrix-MAK off != Gesamt-MAK off fuer {target_date:%d.%m.%Y}")
    _assert(abs(overall_on - expected_on) < 1e-6, f"Matrix-MAK on != Gesamt-MAK on fuer {target_date:%d.%m.%Y}")

    return {
        "target_date": target_date,
        "overall_off": overall_off,
        "overall_on": overall_on,
        "changed_steps": len(changed_steps),
    }


def main() -> None:
    _init_state_from_app_defaults()

    compact = load_compact_page_module()
    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    snapshot_df, _, _, _ = load_and_prepare_data()
    df_atz = load_atz_data_cached(str(ROOT), None, None, None)
    target_dates = _pick_random_dates()

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Seed           : {RANDOM_SEED}")
    print(f"Pruefbereich   : {RANGE_START:%d.%m.%Y} bis {RANGE_END:%d.%m.%Y}")
    print("Pruefpfad      : Simulation -> Salary Automation -> IST MAK -> Verguetungsklassen")
    print("")

    for idx, target_date in enumerate(target_dates, start=1):
        result = _run_single_check(
            compact=compact,
            snapshot_df=snapshot_df,
            df_atz=df_atz,
            target_date=target_date,
        )
        print(f"[{idx}] {result['target_date']:%d.%m.%Y}")
        print(f"    Geaenderte Zellen : {result['changed_steps']}")
        print(f"    Gesamt-MAK off    : {result['overall_off']:.4f}")
        print(f"    Gesamt-MAK on     : {result['overall_on']:.4f}")
        print("")

    print(f"OK: MAK-Verguetungsklassen-Checks fuer {NUM_DATES} zufaellige Zukunftsdaten erfolgreich.")


if __name__ == "__main__":
    main()
