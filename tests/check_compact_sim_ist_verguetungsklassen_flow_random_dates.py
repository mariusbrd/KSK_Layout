"""
End-to-End-Mehrfach-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Verguetungsklassen

Es werden dreissig reproduzierbar zufaellige Zukunftsdaten im Bereich
01.01.2026 bis 31.12.2028 geprueft.

Geprueft wird je Datum ueber den gesamten Flow:
1. Koepfe: Stufenverteilung aendert sich, Entgeltgruppen-Gesamtsummen bleiben gleich.
2. MAK: Stufenverteilung aendert sich, Entgeltgruppen-Gesamtsummen bleiben gleich.
3. EUR: Stufenverteilung aendert sich, Gesamt-EUR sinkt je Entgeltgruppe nicht.
4. Alle Matrizen stimmen mit den deduplizierten Gesamtwerten des simulierten Bestands ueberein.
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


def _collect_step_changes(off_tbl: pd.DataFrame, on_tbl: pd.DataFrame) -> int:
    merged = off_tbl.merge(on_tbl, on="Entgeltgruppe", how="outer", suffixes=("_off", "_on")).fillna(0.0)
    step_labels = sorted(
        {
            col[:-4]
            for col in merged.columns
            if col.endswith("_off") and col.startswith("Stufe ")
        }
    )

    changed = 0
    for _, row in merged.iterrows():
        for label in step_labels:
            before = float(row.get(f"{label}_off", 0.0))
            after = float(row.get(f"{label}_on", 0.0))
            if abs(before - after) > 1e-9:
                changed += 1
    return changed


def _validate_metric(
    *,
    off_prepared: pd.DataFrame,
    on_prepared: pd.DataFrame,
    compact,
    metric_name: str,
    value_col: str,
    target_date: pd.Timestamp,
) -> dict:
    off_chart_df = off_prepared[off_prepared["Is_Vacant"] == False].copy()
    on_chart_df = on_prepared[on_prepared["Is_Vacant"] == False].copy()

    off_tbl = compact.create_stacked_tariff_breakdown_table(off_chart_df, value_col)
    on_tbl = compact.create_stacked_tariff_breakdown_table(on_chart_df, value_col)

    _assert("Entgeltgruppe" in off_tbl.columns and "Entgeltgruppe" in on_tbl.columns, f"Breakdown-Tabelle fehlt fuer {metric_name} am {target_date:%d.%m.%Y}.")
    _assert("Gesamt" in off_tbl.columns and "Gesamt" in on_tbl.columns, f"Gesamt-Spalte fehlt fuer {metric_name} am {target_date:%d.%m.%Y}.")

    changed_steps = _collect_step_changes(off_tbl, on_tbl)
    _assert(changed_steps > 0, f"Stufenautomatik veraendert {metric_name} fuer {target_date:%d.%m.%Y} nicht.")

    off_totals = off_tbl[["Entgeltgruppe", "Gesamt"]].rename(columns={"Gesamt": "Gesamt_off"})
    on_totals = on_tbl[["Entgeltgruppe", "Gesamt"]].rename(columns={"Gesamt": "Gesamt_on"})
    totals = off_totals.merge(on_totals, on="Entgeltgruppe", how="outer").fillna(0.0)
    totals["delta"] = totals["Gesamt_on"] - totals["Gesamt_off"]

    overall_off = float(off_tbl["Gesamt"].sum())
    overall_on = float(on_tbl["Gesamt"].sum())

    if metric_name == "Koepfe":
        expected_off = float(get_unique_employees(off_chart_df)["PersNr"].notna().sum())
        expected_on = float(get_unique_employees(on_chart_df)["PersNr"].notna().sum())
        _assert((totals["delta"].abs() <= 1e-9).all(), f"Entgeltgruppen-Koepfe veraendern sich fuer {target_date:%d.%m.%Y}.")
    elif metric_name == "MAK":
        value_col = next((c for c in ("MAK_Calculated", "mak", "MAK") if c in off_chart_df.columns), value_col)
        expected_off = float(get_unique_employees(off_chart_df)[value_col].fillna(0.0).sum())
        expected_on = float(get_unique_employees(on_chart_df)[value_col].fillna(0.0).sum())
        _assert((totals["delta"].abs() <= 1e-6).all(), f"Entgeltgruppen-MAK veraendern sich fuer {target_date:%d.%m.%Y}.")
    else:
        expected_off = float(get_unique_employees(off_chart_df)["Total_Cost_Year"].fillna(0.0).sum())
        expected_on = float(get_unique_employees(on_chart_df)["Total_Cost_Year"].fillna(0.0).sum())
        _assert((totals["delta"] >= -1e-6).all(), f"Entgeltgruppen-EUR sinken fuer {target_date:%d.%m.%Y}.")
        _assert(overall_on > overall_off, f"Gesamt-EUR steigt fuer {target_date:%d.%m.%Y} trotz Stufenautomatik nicht an.")

    _assert(abs(overall_off - expected_off) < 1e-6, f"Matrix-{metric_name} off != Gesamt-{metric_name} off fuer {target_date:%d.%m.%Y}.")
    _assert(abs(overall_on - expected_on) < 1e-6, f"Matrix-{metric_name} on != Gesamt-{metric_name} on fuer {target_date:%d.%m.%Y}.")

    return {
        "changed_steps": changed_steps,
        "overall_off": overall_off,
        "overall_on": overall_on,
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
    print("Pruefpfad      : Simulation -> Salary Automation -> IST Verguetungsklassen (Koepfe/MAK/EUR)")
    print("")

    for idx, target_date in enumerate(target_dates, start=1):
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

        heads = _validate_metric(
            off_prepared=off_prepared,
            on_prepared=on_prepared,
            compact=compact,
            metric_name="Koepfe",
            value_col="Headcount",
            target_date=target_date,
        )
        mak = _validate_metric(
            off_prepared=off_prepared,
            on_prepared=on_prepared,
            compact=compact,
            metric_name="MAK",
            value_col=next((c for c in ("MAK_Calculated", "mak", "MAK") if c in on_prepared.columns), "FTE_assigned"),
            target_date=target_date,
        )
        eur = _validate_metric(
            off_prepared=off_prepared,
            on_prepared=on_prepared,
            compact=compact,
            metric_name="EUR",
            value_col="Total_Cost_Year",
            target_date=target_date,
        )

        print(f"[{idx}] {target_date:%d.%m.%Y}")
        print(f"    Koepfe: geaenderte Zellen {heads['changed_steps']}, off {heads['overall_off']:.0f}, on {heads['overall_on']:.0f}")
        print(f"    MAK   : geaenderte Zellen {mak['changed_steps']}, off {mak['overall_off']:.4f}, on {mak['overall_on']:.4f}")
        print(f"    EUR   : geaenderte Zellen {eur['changed_steps']}, off {eur['overall_off']:.2f}, on {eur['overall_on']:.2f}")
        print("")

    print(f"OK: Verguetungsklassen-Flow-Checks fuer {NUM_DATES} zufaellige Zukunftsdaten erfolgreich.")


if __name__ == "__main__":
    main()
