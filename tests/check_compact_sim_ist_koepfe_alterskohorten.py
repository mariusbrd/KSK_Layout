"""
Echtdaten-Check fuer:
Kompakt plus Simulation -> IST-Analyse -> Koepfe -> Alterskohorten

Ziel-Stichtag ist fest auf 31.12.2026 gesetzt.
Der Check verwendet denselben Pfad wie die Seite:

1. Basis-Snapshot laden
2. Zukunftsbestand simulieren
3. Kompakt-Daten vorbereiten
4. Alterskohorten-Breakdown ueber create_breakdown_table("Alterskohorte", "Headcount")
5. Fortschreibung fuer identische Personen gegen den Basisbestand pruefen
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
from dataloader.kpi_engine import compute_headcount, get_unique_employees
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


def main() -> None:
    _init_state_from_app_defaults()

    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    compact = load_compact_page_module()
    cohort_definitions = list(st.session_state["cohort_definitions"].keys())

    print(f"Basis-Stichtag : {base_date:%d.%m.%Y}")
    print(f"Ziel-Stichtag  : {TARGET_DATE:%d.%m.%Y}")
    print("Pruefpfad      : Simulation -> prepare_compact_data -> IST Koepfe -> Alterskohorten")

    snapshot_df, _, _, _ = load_and_prepare_data()
    df_atz = _load_atz_input()

    base_prepared = compact.prepare_compact_data(snapshot_df)

    result = simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=df_atz,
        target_date=TARGET_DATE,
        base_date=base_date,
        abgaenge_params=st.session_state.get("abgaenge_params", default_abgaenge_params()),
        zugaenge_params=st.session_state.get("zugaenge_params", default_zugaenge_params()),
    )
    future_prepared = compact.prepare_compact_data(result.future_snapshot_df)

    base_emp = base_prepared[~base_prepared["Is_Vacant"]].copy()
    future_emp = future_prepared[~future_prepared["Is_Vacant"]].copy()
    base_unique = get_unique_employees(base_emp)
    future_unique = get_unique_employees(future_emp)

    total_heads = compute_headcount(future_emp)
    breakdown_df = compact.create_breakdown_table(future_emp, "Alterskohorte", "Headcount")
    if "Hinweis" in breakdown_df.columns:
        raise AssertionError(f"Breakdown konnte nicht erzeugt werden: {breakdown_df.iloc[0]['Hinweis']}")

    breakdown_counts = {
        str(row["Alterskohorte"]): int(row["IST"])
        for _, row in breakdown_df.iterrows()
    }
    breakdown_total = int(breakdown_df["IST"].sum())
    breakdown_order = breakdown_df["Alterskohorte"].astype(str).tolist()
    expected_order = [label for label in cohort_definitions if label in breakdown_order]

    missing_cohort = int(future_unique["Alterskohorte"].isna().sum()) if "Alterskohorte" in future_unique.columns else total_heads
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

    print("")
    print("Ergebnisse")
    print(f"- Simulationsmodus aktiv : {result.metadata.get('used_simulation')}")
    print(f"- Gesamt Koepfe         : {total_heads}")
    print(f"- Kohorten gesamt       : {breakdown_counts}")
    print(f"- Reihenfolge Chart     : {breakdown_order}")
    print(f"- Fehlende Kohorten     : {missing_cohort}")
    print(f"- Ueberlebende Personen : {len(shared)}")
    print(f"- Ø Altersdelta         : {avg_age_delta:.4f}")
    print(f"- Kohortenwechsel       : {moved_cohort_count}")
    if invalid_labels:
        print(f"- Unerwartete Labels    : {invalid_labels}")
    print("")

    _assert(result.metadata.get("used_simulation") is True, "Simulation wurde fuer 31.12.2026 nicht aktiv ausgefuehrt.")
    _assert("Alterskohorte" in future_unique.columns, "Spalte 'Alterskohorte' fehlt im simulierten IST-Bestand.")
    _assert(missing_cohort == 0, "Es gibt Mitarbeitende ohne Alterskohorte.")
    _assert(not invalid_labels, f"Breakdown enthaelt ungueltige Kohortenlabels: {invalid_labels}")
    _assert(breakdown_total == total_heads, "Alterskohorten-Breakdown summiert sich nicht auf Gesamt-Koepfe.")
    _assert(breakdown_order == expected_order, "Die Reihenfolge der Alterskohorten entspricht nicht den aktiven Kohortendefinitionen.")
    _assert(not shared.empty, "Es konnten keine identischen Personen zwischen Basis- und Zukunftsbestand gematcht werden.")
    _assert((shared["age_delta"] > 0.95).all(), "Mindestens eine ueberlebende Person wurde nicht auf den Zukunfts-Stichtag gealtert.")
    _assert((shared["age_delta"] < 1.05).all(), "Mindestens eine ueberlebende Person wurde unplausibel stark gealtert.")
    _assert(moved_cohort_count > 0, "Keine Person ist in eine neue Alterskohorte gerueckt; die Kohortenfortschreibung sollte geprueft werden.")

    print("OK: IST-Analyse > Koepfe > Alterskohorten ist fuer 31.12.2026 konsistent.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FEHLER: {exc}")
        raise
