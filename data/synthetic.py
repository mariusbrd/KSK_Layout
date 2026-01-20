"""
Synthetische Testdaten-Generierung für HR Pulse Dashboard.

Generiert realistische HR-Daten für eine süddeutsche Sparkasse/Bank mit:
- ~1200 Mitarbeitende
- ~1700 Planstellen
- Historische Daten (2024-01 bis heute)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR,
    DEFAULT_COHORTS, TARIFF_GROUPS
)


# =============================================================================
# VERTEILUNGEN
# =============================================================================

# Organisationseinheiten
ORG_UNITS = [
    {"kuerzel": "025", "name": "Privatkunden Region Nord", "target_size": 45},
    {"kuerzel": "026", "name": "Privatkunden Region Süd", "target_size": 42},
    {"kuerzel": "028", "name": "Firmenkunden", "target_size": 55},
    {"kuerzel": "030", "name": "Baufinanzierung", "target_size": 35},
    {"kuerzel": "191", "name": "Vertriebssteuerung", "target_size": 60},
    {"kuerzel": "826", "name": "Marktfolge Kredit", "target_size": 85},
    {"kuerzel": "840", "name": "Controlling & Rechnungswesen", "target_size": 40},
    {"kuerzel": "815", "name": "Facility Management", "target_size": 25},
    {"kuerzel": "850", "name": "Marketing", "target_size": 30},
    {"kuerzel": "900", "name": "Compliance", "target_size": 15},
    {"kuerzel": "411", "name": "IT & Organisation", "target_size": 65},
    {"kuerzel": "330", "name": "Personal", "target_size": 20},
    {"kuerzel": "420", "name": "Revision", "target_size": 12},
    {"kuerzel": "810", "name": "Treasury", "target_size": 18},
    {"kuerzel": "820", "name": "Risikomanagement", "target_size": 22},
]

# Altersverteilung
AGE_DISTRIBUTION = {
    (16, 19): 0.07,   # Azubis
    (20, 29): 0.22,
    (30, 39): 0.16,
    (40, 49): 0.20,
    (50, 59): 0.25,
    (60, 69): 0.10
}

# Geschlechterverteilung
GENDER_DISTRIBUTION = {"w": 0.63, "m": 0.37}

# Beschäftigungsgrad-Verteilung
EMPLOYMENT_DISTRIBUTION = {
    1.0: 0.67,      # Vollzeit
    0.75: 0.12,
    0.50: 0.15,
    0.25: 0.06
}

# Tarifgruppen-Verteilung
TARIFF_DISTRIBUTION = {
    "E6": 0.05, "E7": 0.08, "E8": 0.12,
    "E9A": 0.15, "E9B": 0.10, "E9C": 0.18,
    "E10": 0.14, "E11": 0.10, "E12": 0.04,
    "E13": 0.02, "E14": 0.01, "E15": 0.01
}

# Qualifikationsgruppen
EDUCATION_DISTRIBUTION = {
    "derzeit Berufsausbildung": 0.09,
    "kfm Berufsabschluss": 0.19,
    "Bankberufsabschluss": 0.17,
    "Sparkassen/Bankfachwirt": 0.17,
    "SPK/Bankbetriebswirt": 0.21,
    "Bachelor FH": 0.08,
    "Bachelor Universität": 0.01,
    "Master FH": 0.02,
    "Master Universität": 0.03,
    "ohne Berufsabschluss": 0.01,
    "nicht kfm Berufsabschluss": 0.02
}

# ATZ-Parameter
ATZ_RATE_55PLUS = 0.23  # 23% der 55+ in ATZ
ATZ_PHASE_SPLIT = {"Arbeitsphase": 0.5, "Freistellungsphase": 0.5}

# Vakanzrate-Bereich
VACANCY_RATE_RANGE = (0.15, 0.35)


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def assign_age_cohort(age: int, cohorts: Dict[str, Tuple[int, int]]) -> str:
    """Ordnet ein Alter einer Kohorte zu."""
    for cohort_name, (min_age, max_age) in cohorts.items():
        if min_age <= age <= max_age:
            return cohort_name
    return "Unknown"


def calculate_cost(tariff: str, step: int, fte: float) -> float:
    """
    Berechnet Jahreskosten für eine Person/Stelle.

    Args:
        tariff: Tarifgruppe (z.B. "E9B")
        step: Tarifstufe (1-6)
        fte: Beschäftigungsgrad (0-1)

    Returns:
        Jahreskosten in Euro
    """
    base = BASE_SALARY.get(tariff, 50000)
    step_factor = STEP_MULTIPLIER.get(step, 1.0)
    return base * step_factor * fte * EMPLOYER_COST_FACTOR


def weighted_choice(choices: Dict, size: int = 1):
    """Zufallsauswahl basierend auf Gewichtung."""
    items = list(choices.keys())
    weights = list(choices.values())

    # Handle special case where items are tuples (e.g., age ranges)
    if items and isinstance(items[0], tuple):
        indices = np.random.choice(len(items), size=size, p=weights)
        return [items[i] for i in indices]

    return np.random.choice(items, size=size, p=weights)


# =============================================================================
# HAUPTFUNKTIONEN
# =============================================================================

def generate_snapshot_detail(
    n_employees: int = 1200,
    n_planstellen: int = 1700,
    reference_date: str = "2026-01-01"
) -> pd.DataFrame:
    """
    Generiert die Snapshot_Detail Tabelle.

    Args:
        n_employees: Anzahl Mitarbeitende
        n_planstellen: Anzahl Planstellen (inkl. Vakanzen)
        reference_date: Stichtag

    Returns:
        DataFrame mit Snapshot-Daten
    """
    np.random.seed(42)  # Reproduzierbarkeit
    ref_date = pd.to_datetime(reference_date)

    data = []
    person_id_counter = 10000
    planstellen_id_counter = 1

    # Normalisiere Org-Einheiten Größen
    total_target = sum(org["target_size"] for org in ORG_UNITS)
    scaling = n_planstellen / total_target

    for org in ORG_UNITS:
        n_stellen_org = int(org["target_size"] * scaling)
        vacancy_rate = np.random.uniform(*VACANCY_RATE_RANGE)
        n_besetzt = int(n_stellen_org * (1 - vacancy_rate))
        n_vakant = n_stellen_org - n_besetzt

        # Besetzte Stellen
        for i in range(n_besetzt):
            # Person generieren
            gender = weighted_choice(GENDER_DISTRIBUTION)[0]
            age_range = weighted_choice(AGE_DISTRIBUTION)[0]
            age = np.random.randint(age_range[0], age_range[1] + 1)
            birth_date = ref_date - pd.DateOffset(years=age)

            # Beschäftigungsgrad
            bs_grd = weighted_choice(EMPLOYMENT_DISTRIBUTION)[0]

            # Tarifgruppe & Stufe
            tariff = weighted_choice(TARIFF_DISTRIBUTION)[0]
            step = np.random.choice([3, 4, 5, 6], p=[0.2, 0.4, 0.3, 0.1])

            # Qualifikation (korreliert mit Alter und Tarif)
            if age < 20:
                education = "derzeit Berufsausbildung"
            elif tariff in ["E6", "E7", "E8"]:
                education = weighted_choice({
                    "kfm Berufsabschluss": 0.4,
                    "Bankberufsabschluss": 0.6
                })[0]
            elif tariff in ["E9A", "E9B", "E9C"]:
                education = weighted_choice({
                    "Bankberufsabschluss": 0.3,
                    "Sparkassen/Bankfachwirt": 0.5,
                    "SPK/Bankbetriebswirt": 0.2
                })[0]
            else:
                education = weighted_choice({
                    "SPK/Bankbetriebswirt": 0.4,
                    "Bachelor FH": 0.3,
                    "Master FH": 0.2,
                    "Master Universität": 0.1
                })[0]

            # Eintrittsdatum (zwischen 1 und 40 Jahren Betriebszugehörigkeit)
            tenure_years = int(min(np.random.exponential(10), age - 16))
            entry_date = ref_date - pd.DateOffset(years=tenure_years)

            # ATZ-Status (nur für 55+)
            vertragsart = "Unbefristet"
            if age >= 55:
                if np.random.random() < ATZ_RATE_55PLUS:
                    vertragsart = "Altersteilzeit"
            elif age < 20:
                vertragsart = "Auszubildende"

            # FTE-Werte
            fte_person = bs_grd
            soll_fte = 1.0  # Planstelle ist immer 1.0 FTE
            fte_assigned = fte_person  # Was die Person tatsächlich bringt

            # Kosten
            total_cost = calculate_cost(tariff, step, fte_person)

            data.append({
                "Kürzel OrgEinheit": org["kuerzel"],
                "OrgEinheitNr": float(org["kuerzel"]),
                "Organisationseinheit": org["name"],
                "Planstellennr": float(planstellen_id_counter),
                "Planstelle": f"Planstelle {planstellen_id_counter}",
                "Sollarbeitszeit": 39.0,
                "Bewertung Tarifgruppe": tariff,
                "Personalnummer": float(person_id_counter),
                "Soll_FTE": soll_fte,
                "PersNr": float(person_id_counter),
                "GebDatum": birth_date,
                "Text Gsch": "weiblich" if gender == "w" else "männlich",
                "Eintritt": entry_date,
                "Austritt": pd.NaT,
                "BsGrd": bs_grd * 100,  # in Prozent
                "Vertragsart": vertragsart,
                "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
                "Tarifarttext": "TVöD" if vertragsart != "Auszubildende" else "Auszubildende-VKA",
                "TrfGr": tariff,
                "St": str(step),
                "FTE_person": fte_person,
                "Total_Cost_Year": total_cost,
                "Is_Vacant": False,
                "FTE_assigned": fte_assigned,
                "Ausbildung": education,
            })

            person_id_counter += 1
            planstellen_id_counter += 1

        # Vakante Stellen
        for i in range(n_vakant):
            tariff = weighted_choice(TARIFF_DISTRIBUTION)[0]

            data.append({
                "Kürzel OrgEinheit": org["kuerzel"],
                "OrgEinheitNr": float(org["kuerzel"]),
                "Organisationseinheit": org["name"],
                "Planstellennr": float(planstellen_id_counter),
                "Planstelle": f"Planstelle {planstellen_id_counter}",
                "Sollarbeitszeit": 39.0,
                "Bewertung Tarifgruppe": tariff,
                "Personalnummer": np.nan,
                "Soll_FTE": 1.0,
                "PersNr": np.nan,
                "GebDatum": pd.NaT,
                "Text Gsch": np.nan,
                "Eintritt": pd.NaT,
                "Austritt": pd.NaT,
                "BsGrd": np.nan,
                "Vertragsart": np.nan,
                "Status kundenindividuell": np.nan,
                "Tarifarttext": np.nan,
                "TrfGr": np.nan,
                "St": np.nan,
                "FTE_person": 0.0,
                "Total_Cost_Year": 0.0,
                "Is_Vacant": True,
                "FTE_assigned": 0.0,
                "Ausbildung": np.nan,
            })

            planstellen_id_counter += 1

    df = pd.DataFrame(data)
    return df


def generate_history_cube(
    snapshot_df: pd.DataFrame,
    start_date: str = "2024-01-01",
    end_date: str = "2026-01-18"
) -> pd.DataFrame:
    """
    Generiert History_Cube (monatliche Zeitreihen).

    Args:
        snapshot_df: Snapshot_Detail DataFrame
        start_date: Startdatum
        end_date: Enddatum

    Returns:
        DataFrame mit monatlichen Aggregaten pro OrgEinheit
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    # Monatliche Stichtage
    dates = pd.date_range(start=start, end=end, freq='MS')

    data = []

    for org_unit in snapshot_df["Kürzel OrgEinheit"].unique():
        org_data = snapshot_df[snapshot_df["Kürzel OrgEinheit"] == org_unit]

        # Basis-Werte (aktueller Stand)
        base_headcount = org_data[~org_data["Is_Vacant"]]["PersNr"].nunique()
        base_fte = org_data["FTE_assigned"].sum()
        base_cost = org_data["Total_Cost_Year"].sum()
        base_vacancy = org_data["Is_Vacant"].sum()

        for date in dates:
            # Leichte Variation über Zeit (Trend + Noise)
            months_from_start = (date.year - start.year) * 12 + (date.month - start.month)
            trend_factor = 1.0 + (months_from_start / 100)  # Langsames Wachstum
            noise = np.random.normal(1.0, 0.02)  # 2% Schwankung

            headcount = int(base_headcount * trend_factor * noise)
            fte = base_fte * trend_factor * noise
            cost = base_cost * trend_factor * noise
            vacancy = int(base_vacancy * noise)

            data.append({
                "Kürzel OrgEinheit": org_unit,
                "Date": date,
                "Headcount": headcount,
                "FTE": fte,
                "Total_Cost": cost,
                "Vacancy_Count": vacancy,
            })

    df = pd.DataFrame(data)
    return df


def generate_org_structure() -> pd.DataFrame:
    """Generiert Organisationsstruktur-Tabelle."""
    data = [
        {
            "Kürzel OrgEinheit": org["kuerzel"],
            "OrgEinheitNr": float(org["kuerzel"]),
            "Organisationseinheit": org["name"],
        }
        for org in ORG_UNITS
    ]
    return pd.DataFrame(data)


def generate_synthetic_data(
    n_employees: int = 1200,
    n_planstellen: int = 1700,
    start_date: str = "2024-01-01",
    end_date: str = "2026-01-18"
) -> Dict[str, pd.DataFrame]:
    """
    Generiert alle benötigten DataFrames.

    Args:
        n_employees: Anzahl Mitarbeitende
        n_planstellen: Anzahl Planstellen
        start_date: Startdatum für Historie
        end_date: Enddatum für Historie

    Returns:
        Dictionary mit DataFrames:
        - snapshot_detail
        - history_cube
        - org_structure
    """
    print("Generiere Snapshot_Detail...")
    snapshot_df = generate_snapshot_detail(n_employees, n_planstellen)

    print("Generiere History_Cube...")
    history_df = generate_history_cube(snapshot_df, start_date, end_date)

    print("Generiere Org_Structure...")
    org_df = generate_org_structure()

    print("Fertig!")

    return {
        "snapshot_detail": snapshot_df,
        "history_cube": history_df,
        "org_structure": org_df
    }


def save_to_excel(data_dict: Dict[str, pd.DataFrame], filepath: str):
    """
    Speichert alle DataFrames in eine Excel-Datei.

    Args:
        data_dict: Dictionary mit DataFrames
        filepath: Ziel-Dateipfad
    """
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        for sheet_name, df in data_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"Daten gespeichert in: {filepath}")


# =============================================================================
# MAIN (für direkten Aufruf)
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("HR Pulse - Synthetische Testdaten-Generierung")
    print("=" * 60)

    # Generiere Daten
    data = generate_synthetic_data(
        n_employees=1200,
        n_planstellen=1700,
        start_date="2024-01-01",
        end_date="2026-01-18"
    )

    # Speichere in Excel
    output_path = "data/sample_data/hr_data.xlsx"
    save_to_excel(data, output_path)

    # Statistiken ausgeben
    print("\n" + "=" * 60)
    print("STATISTIKEN:")
    print("=" * 60)
    print(f"Snapshot_Detail: {len(data['snapshot_detail']):,} Zeilen")
    print(f"  - Mitarbeitende: {data['snapshot_detail'][~data['snapshot_detail']['Is_Vacant']]['PersNr'].nunique():,}")
    print(f"  - Planstellen: {data['snapshot_detail']['Planstellennr'].nunique():,}")
    print(f"  - Vakanzen: {data['snapshot_detail']['Is_Vacant'].sum():,}")
    print(f"\nHistory_Cube: {len(data['history_cube']):,} Zeilen")
    print(f"Org_Structure: {len(data['org_structure']):,} Zeilen")
