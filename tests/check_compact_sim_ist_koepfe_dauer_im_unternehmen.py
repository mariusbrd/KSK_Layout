"""
Echtdaten-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Koepfe -> Dauer im Unternehmen

Ziel-Stichtag ist fest auf 31.12.2026 gesetzt.
Der Check verwendet denselben Pfad wie die Seite:

1. Basis-Snapshot laden
2. Zukunftsbestand simulieren
3. Kompakt-Daten vorbereiten
4. Dauer-im-Unternehmen-Breakdown ueber create_breakdown_table(..., "Headcount")
5. Ergebnis gegen deduplizierte Mitarbeitersicht pruefen
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


TARGET_DATE = pd.Timestamp("2026-12-31")
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


def _tenure_counts(unique_emp: pd.DataFrame, tenure_col: str) -> dict[str, int]:
    counts = (
        unique_emp.groupby(tenure_col, observed=True)["PersNr"]
        .nunique()
        .reset_index(name="IST")
    )
    return {str(row[tenure_col]): int(row["IST"]) for _, row in counts.iterrows()}


def main() -> None:
    random.seed(42)
    _init_state_from_app_defaults()

    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    compact = load_compact_page_module()
    tenure_col = "Betriebszugehörigkeit_Bin"

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Ziel-Stichtag  : {TARGET_DATE:%d.%m.%Y}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST Koepfe -> Dauer im Unternehmen")

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
    unique_emp = get_unique_employees(emp_df).copy()

    breakdown_df = compact.create_breakdown_table(emp_df, tenure_col, "Headcount")
    if "Hinweis" in breakdown_df.columns:
        raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

    total_heads = compute_headcount(emp_df)
    breakdown_counts = {str(row[tenure_col]): int(row["IST"]) for _, row in breakdown_df.iterrows()}
    breakdown_total = int(breakdown_df["IST"].sum())
    expected_counts = _tenure_counts(unique_emp, tenure_col)

    missing_unknown_mask = (
        unique_emp[tenure_col].isna() |
        unique_emp[tenure_col].astype(str).str.strip().isin(["", "nan", "(unbekannt)"])
    )
    missing_unknown = int(missing_unknown_mask.sum())
    missing_forecast = int(
        unique_emp.loc[missing_unknown_mask, "PersNr"].astype(str).str.startswith(FORECAST_PREFIXES).sum()
    )

    forecast_unique = unique_emp[unique_emp["PersNr"].astype(str).str.startswith(FORECAST_PREFIXES)].copy()
    forecast_tenure_counts = forecast_unique[tenure_col].astype(str).value_counts(dropna=False).to_dict()
    negative_tenure = int((pd.to_numeric(unique_emp["Betriebszugehörigkeit_Jahre"], errors="coerce") < 0).fillna(False).sum())

    print("")
    print("Ergebnisse")
    print(f"- Simulationsmodus aktiv : {result.metadata.get('used_simulation')}")
    print(f"- Gesamt Koepfe         : {total_heads}")
    print(f"- Breakdown Chart       : {breakdown_counts}")
    print(f"- Erwartet dedup        : {expected_counts}")
    print(f"- Unbekannt/fehlend     : {missing_unknown}")
    print(f"- Davon Forecast        : {missing_forecast}")
    print(f"- Forecast-Tenure       : {forecast_tenure_counts}")
    print(f"- Negative Tenure       : {negative_tenure}")
    print("")

    _assert(result.metadata.get("used_simulation") is True, "Simulation wurde fuer 31.12.2026 nicht aktiv ausgefuehrt.")
    _assert(breakdown_total == total_heads, "Dauer-im-Unternehmen-Breakdown summiert sich nicht auf Gesamt-Koepfe.")
    _assert(breakdown_counts == expected_counts, "Chart-Breakdown stimmt nicht mit deduplizierter Mitarbeitersicht ueberein.")
    _assert(missing_forecast == 0, "Simulierte Zugaenge haben keine gueltige Unternehmenszugehoerigkeit.")
    _assert(negative_tenure == 0, "Es gibt negative Betriebszugehoerigkeit im simulierten Bestand.")

    print("OK: IST-Analyse > Koepfe > Dauer im Unternehmen ist fuer 31.12.2026 konsistent.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
