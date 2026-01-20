"""
Daten-Loader für HR Pulse Dashboard.

Lädt und cached HR-Daten aus Excel mit Streamlit.
"""

import pandas as pd
import streamlit as st
from typing import Dict, Tuple
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DATA_PATH, DEFAULT_COHORTS


@st.cache_data
def load_hr_data(filepath: str = None) -> Dict[str, pd.DataFrame]:
    """
    Lädt HR-Daten aus Excel-Datei.

    Args:
        filepath: Pfad zur Excel-Datei (default: aus settings.py)

    Returns:
        Dictionary mit DataFrames:
        - snapshot_detail
        - history_cube
        - org_structure

    Raises:
        FileNotFoundError: Wenn Datei nicht existiert
    """
    if filepath is None:
        filepath = DATA_PATH

    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"Datei nicht gefunden: {filepath}\n"
            f"Bitte zuerst Testdaten generieren mit: python data/synthetic.py"
        )

    # Lade alle Sheets
    data = {}

    try:
        # Snapshot Detail
        data["snapshot_detail"] = pd.read_excel(
            filepath,
            sheet_name="snapshot_detail",
            parse_dates=["GebDatum", "Eintritt", "Austritt"]
        )

        # History Cube
        data["history_cube"] = pd.read_excel(
            filepath,
            sheet_name="history_cube",
            parse_dates=["Date"]
        )

        # Org Structure
        data["org_structure"] = pd.read_excel(
            filepath,
            sheet_name="org_structure"
        )

    except Exception as e:
        raise Exception(f"Fehler beim Laden der Daten: {str(e)}")

    return data


@st.cache_data
def enrich_snapshot_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reichert Snapshot-Daten mit berechneten Feldern an.

    Args:
        df: Snapshot_Detail DataFrame

    Returns:
        Angereicherter DataFrame
    """
    df = df.copy()

    # Alter berechnen (zum aktuellen Datum)
    today = pd.Timestamp.today()
    df["Alter"] = (today - df["GebDatum"]).dt.days / 365.25
    df["Alter"] = df["Alter"].fillna(0).astype(int)

    # Betriebszugehörigkeit in Jahren
    df["Betriebszugehörigkeit_Jahre"] = (today - df["Eintritt"]).dt.days / 365.25
    df["Betriebszugehörigkeit_Jahre"] = df["Betriebszugehörigkeit_Jahre"].fillna(0)

    # Alterskohorten (aus session_state, falls verfügbar)
    if "cohort_definitions" in st.session_state:
        cohorts = st.session_state["cohort_definitions"]
    else:
        cohorts = DEFAULT_COHORTS

    df["Alterskohorte"] = df["Alter"].apply(
        lambda age: assign_age_cohort(age, cohorts)
    )

    # Geschlecht vereinfachen
    df["Geschlecht"] = df["Text Gsch"].map({
        "weiblich": "w",
        "männlich": "m"
    })

    # Vollzeit/Teilzeit
    df["Arbeitszeit"] = df["FTE_person"].apply(
        lambda x: "Vollzeit" if x >= 0.95 else "Teilzeit"
    )

    # ATZ-Status
    def get_atz_status(row):
        if pd.isna(row["Vertragsart"]) or row["Vertragsart"] != "Altersteilzeit":
            return "Kein ATZ"
        # Weitere Logik für Arbeits-/Freistellungsphase könnte hier ergänzt werden
        # Für jetzt: 50/50 Split basierend auf Alter
        if row["Alter"] < 60:
            return "Arbeitsphase"
        return "Freistellungsphase"

    df["ATZ_Status"] = df.apply(get_atz_status, axis=1)

    # Ist-Soll Abweichung
    df["Abweichung_FTE"] = df["Soll_FTE"] - df["FTE_assigned"]

    return df


def assign_age_cohort(age: int, cohorts: Dict[str, Tuple[int, int]]) -> str:
    """
    Ordnet ein Alter einer Kohorte zu.

    Args:
        age: Alter in Jahren
        cohorts: Dictionary mit Kohorten-Definitionen

    Returns:
        Kohorten-Name
    """
    for cohort_name, (min_age, max_age) in cohorts.items():
        if min_age <= age <= max_age:
            return cohort_name
    return "Unbekannt"


@st.cache_data
def get_data_summary(snapshot_df: pd.DataFrame) -> Dict:
    """
    Berechnet Zusammenfassungsstatistiken.

    Args:
        snapshot_df: Angereicherter Snapshot DataFrame

    Returns:
        Dictionary mit KPIs
    """
    # Nur besetzte Stellen
    besetzt = snapshot_df[~snapshot_df["Is_Vacant"]]

    summary = {
        "total_planstellen": len(snapshot_df),
        "total_employees": besetzt["PersNr"].nunique(),
        "total_fte": snapshot_df["FTE_assigned"].sum(),
        "total_soll_fte": snapshot_df["Soll_FTE"].sum(),
        "total_cost": snapshot_df["Total_Cost_Year"].sum(),
        "vacancy_count": snapshot_df["Is_Vacant"].sum(),
        "vacancy_rate": snapshot_df["Is_Vacant"].mean(),
        "besetzungsgrad": 1 - snapshot_df["Is_Vacant"].mean(),
        "avg_age": besetzt["Alter"].mean(),
        "avg_tenure": besetzt["Betriebszugehörigkeit_Jahre"].mean(),
        "teilzeit_count": (besetzt["Arbeitszeit"] == "Teilzeit").sum(),
        "teilzeit_rate": (besetzt["Arbeitszeit"] == "Teilzeit").mean(),
        "atz_count": (besetzt["ATZ_Status"] != "Kein ATZ").sum(),
        "atz_rate": (besetzt["ATZ_Status"] != "Kein ATZ").mean(),
        "female_count": (besetzt["Geschlecht"] == "w").sum(),
        "female_rate": (besetzt["Geschlecht"] == "w").mean(),
    }

    return summary


def load_and_prepare_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    Kompletter Daten-Lade- und Aufbereitungsprozess.

    Returns:
        Tuple aus (snapshot_df, history_df, org_df, summary)
    """
    # Lade Rohdaten
    data = load_hr_data()

    # Reichere Snapshot an
    snapshot_df = enrich_snapshot_data(data["snapshot_detail"])

    # Berechne Summary
    summary = get_data_summary(snapshot_df)

    return (
        snapshot_df,
        data["history_cube"],
        data["org_structure"],
        summary
    )


# =============================================================================
# TEST (für direkten Aufruf)
# =============================================================================

if __name__ == "__main__":
    print("Teste Daten-Loader...")

    try:
        snapshot, history, org, summary = load_and_prepare_data()

        print("\n✓ Daten erfolgreich geladen!")
        print(f"\nSnapshot: {len(snapshot)} Zeilen")
        print(f"History: {len(history)} Zeilen")
        print(f"Org Structure: {len(org)} Zeilen")

        print("\n" + "=" * 60)
        print("SUMMARY:")
        print("=" * 60)
        for key, value in summary.items():
            if isinstance(value, float):
                print(f"{key:.<40} {value:.2f}")
            else:
                print(f"{key:.<40} {value}")

    except FileNotFoundError as e:
        print(f"\n✗ FEHLER: {e}")
        print("\nBitte zuerst Testdaten generieren:")
        print("  python data/synthetic.py")
