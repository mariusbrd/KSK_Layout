"""
Synthetische Testdaten-Generierung für HR Pulse Dashboard.

Generiert realistische HR-Daten die 1:1 die Original-Datenstruktur abbilden:
- Mitarbeiter.xlsx (18 Spalten)
- Planstellen.XLSX (10 Spalten)
- ATZ.xlsx (6 Spalten)
- Ausbildung.xlsx (4 Spalten)

Die Daten bilden eine süddeutsche Sparkasse/Bank mit ~1200 Mitarbeitenden ab.
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
# VERTEILUNGEN (basierend auf Original-Daten)
# =============================================================================

# Organisationseinheiten (basierend auf Original: 159 Einheiten)
ORG_UNITS = [
    {"kuerzel": 800, "nr": 50002405, "name": "Steuerung"},
    {"kuerzel": 801, "nr": 50002406, "name": "Compliance"},
    {"kuerzel": 802, "nr": 50002407, "name": "Controlling und Rechnungswesen"},
    {"kuerzel": 803, "nr": 50002408, "name": "CR Controlling"},
    {"kuerzel": 804, "nr": 50002409, "name": "CR Rechnungswesen"},
    {"kuerzel": 810, "nr": 50002410, "name": "Treasury"},
    {"kuerzel": 815, "nr": 50002411, "name": "Facility Management"},
    {"kuerzel": 820, "nr": 50002412, "name": "Risikomanagement"},
    {"kuerzel": 826, "nr": 50002413, "name": "Marktfolge Kredit"},
    {"kuerzel": 830, "nr": 50002414, "name": "Recht"},
    {"kuerzel": 840, "nr": 50002415, "name": "Rechnungswesen"},
    {"kuerzel": 850, "nr": 50002416, "name": "Marketing"},
    {"kuerzel": 25, "nr": 50002417, "name": "Privatkunden Region Nord"},
    {"kuerzel": 26, "nr": 50002418, "name": "Privatkunden Region Süd"},
    {"kuerzel": 27, "nr": 50002419, "name": "Privatkunden Region West"},
    {"kuerzel": 28, "nr": 50002420, "name": "Firmenkunden"},
    {"kuerzel": 29, "nr": 50002421, "name": "Firmenkunden Spezial"},
    {"kuerzel": 30, "nr": 50002422, "name": "Baufinanzierung"},
    {"kuerzel": 31, "nr": 50002423, "name": "Immobiliencenter"},
    {"kuerzel": 100, "nr": 50002424, "name": "Vorstand"},
    {"kuerzel": 110, "nr": 50002425, "name": "Vorstandsstab"},
    {"kuerzel": 191, "nr": 50002426, "name": "Vertriebssteuerung"},
    {"kuerzel": 192, "nr": 50002427, "name": "Vertriebsmanagement"},
    {"kuerzel": 200, "nr": 50002428, "name": "Filiale Hauptstelle"},
    {"kuerzel": 201, "nr": 50002429, "name": "Filiale Nord"},
    {"kuerzel": 202, "nr": 50002430, "name": "Filiale Süd"},
    {"kuerzel": 203, "nr": 50002431, "name": "Filiale West"},
    {"kuerzel": 204, "nr": 50002432, "name": "Filiale Ost"},
    {"kuerzel": 205, "nr": 50002433, "name": "SB-Center 1"},
    {"kuerzel": 206, "nr": 50002434, "name": "SB-Center 2"},
    {"kuerzel": 300, "nr": 50002435, "name": "Personal und Organisation"},
    {"kuerzel": 330, "nr": 50002436, "name": "Personalmanagement"},
    {"kuerzel": 331, "nr": 50002437, "name": "Personalentwicklung"},
    {"kuerzel": 332, "nr": 50002438, "name": "Personalservice"},
    {"kuerzel": 400, "nr": 50002439, "name": "IT"},
    {"kuerzel": 411, "nr": 50002440, "name": "IT Anwendungen"},
    {"kuerzel": 412, "nr": 50002441, "name": "IT Infrastruktur"},
    {"kuerzel": 420, "nr": 50002442, "name": "Revision"},
    {"kuerzel": 500, "nr": 50002443, "name": "Kreditmanagement"},
    {"kuerzel": 510, "nr": 50002444, "name": "Kreditbearbeitung"},
    {"kuerzel": 520, "nr": 50002445, "name": "Kreditcontrolling"},
    {"kuerzel": 600, "nr": 50002446, "name": "Zahlungsverkehr"},
    {"kuerzel": 610, "nr": 50002447, "name": "Zahlungsverkehr Inland"},
    {"kuerzel": 620, "nr": 50002448, "name": "Zahlungsverkehr Ausland"},
    {"kuerzel": 700, "nr": 50002449, "name": "Wertpapiere"},
    {"kuerzel": 710, "nr": 50002450, "name": "Wertpapierservice"},
    {"kuerzel": 720, "nr": 50002451, "name": "Vermögensberatung"},
    {"kuerzel": 900, "nr": 50002452, "name": "Sonstige"},
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
GENDER_DISTRIBUTION = {"männlich": 0.37, "weiblich": 0.63}

# Beschäftigungsgrad-Verteilung (in Prozent, 0-100)
EMPLOYMENT_DISTRIBUTION = {
    100.0: 0.67,
    75.0: 0.12,
    50.0: 0.15,
    25.0: 0.06
}

# Tarifgruppen-Verteilung (basierend auf Original)
TARIFF_DISTRIBUTION = {
    "E5": 0.02, "E6": 0.05, "E7": 0.08, "E8": 0.12,
    "E9A": 0.15, "E9B": 0.10, "E9C": 0.18,
    "E10": 0.14, "E11": 0.10, "E12": 0.04,
    "E13": 0.02, "E14": 0.01, "E15": 0.01,
    "TVAöD": 0.02  # Auszubildende
}

# Vertragsarten (basierend auf Original)
CONTRACT_TYPES = {
    "Unbefristet": 0.85,
    "Zeitvertrag": 0.05,
    "Ausbildung": 0.07,
    "Werkstudent": 0.02,
    "Trainee": 0.01
}

# Ausbildungsgruppen (basierend auf Original)
EDUCATION_GROUPS = {
    930: ("kfm Berufsabschluss", 99560, 0.19),
    931: ("Sparkassen/Bankfachwirt", 99561, 0.17),
    932: ("Bankberufsabschluss", 99562, 0.17),
    933: ("SPK/Bankbetriebswirt", 99563, 0.21),
    934: ("Studium Lehrinstitut", 99564, 0.02),
    935: ("Bachelor FH", 99565, 0.08),
    936: ("Bachelor Universität", 99566, 0.01),
    937: ("Master FH", 99567, 0.02),
    938: ("Master Universität", 99568, 0.03),
    939: ("nicht kfm Berufsabschluss", 99569, 0.02),
    940: ("ohne Berufsabschluss", 99570, 0.01),
    941: ("derzeit Berufsausbildung", 99571, 0.07),
}

# ATZ-Modelle
ATZ_MODELS = ["OAT5", "OAT6", "BAT5", "BAT6"]
ATZ_RATE_55PLUS = 0.23  # 23% der 55+ in ATZ


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

def weighted_choice(choices: Dict, size: int = 1):
    """Zufallsauswahl basierend auf Gewichtung."""
    if isinstance(list(choices.values())[0], tuple):
        # Für EDUCATION_GROUPS mit (text, bv, weight)
        items = list(choices.keys())
        weights = [v[2] for v in choices.values()]
    else:
        items = list(choices.keys())
        weights = list(choices.values())

    # Normalisiere Gewichte
    total = sum(weights)
    weights = [w/total for w in weights]

    if items and isinstance(items[0], tuple):
        indices = np.random.choice(len(items), size=size, p=weights)
        return [items[i] for i in indices]

    return np.random.choice(items, size=size, p=weights)


def generate_planstellen_kuerzel(planstellen_nr: int, org_kuerzel: int) -> int:
    """Generiert ein Planstellenkürzel."""
    return int(f"{org_kuerzel:03d}{planstellen_nr:06d}")


# =============================================================================
# HAUPTFUNKTIONEN
# =============================================================================

def generate_mitarbeiter(
    n_employees: int = 1222,
    reference_date: str = "2026-01-01"
) -> pd.DataFrame:
    """
    Generiert Mitarbeiter.xlsx mit exakt der Original-Struktur.

    Spalten (18):
    - PersNr, Vorname, Nachname, GebDatum, Text Gsch
    - Eintritt, Austritt, BsGrd, Vertragsart
    - MitarbKreisbez., MitarbGruppenbez., Bankspezifisch, Bezeichnung
    - Status kundenindividuell, Tarifarttext, Tarifgebiettext, TrfGr, St
    """
    np.random.seed(42)
    ref_date = pd.to_datetime(reference_date)

    data = []

    for pers_nr in range(28, 28 + n_employees):  # Start bei 28 wie im Original
        # Geschlecht
        gender = weighted_choice(GENDER_DISTRIBUTION)[0]

        # Alter generieren
        age_range = weighted_choice(AGE_DISTRIBUTION)[0]
        age = np.random.randint(age_range[0], age_range[1] + 1)
        birth_date = ref_date - pd.DateOffset(years=age)

        # Eintrittsdatum
        max_tenure = min(age - 16, 45)  # Max. Betriebszugehörigkeit
        tenure_years = int(min(np.random.exponential(10), max_tenure))
        entry_date = ref_date - pd.DateOffset(years=tenure_years)

        # Austrittsdatum (9999-12-31 für aktive MA)
        exit_date = pd.Timestamp("9999-12-31")

        # Beschäftigungsgrad
        bs_grd = weighted_choice(EMPLOYMENT_DISTRIBUTION)[0]

        # Vertragsart
        if age < 20:
            vertragsart = "Ausbildung"
            tariff = "TVAöD"
            step = 1
        elif age >= 55 and np.random.random() < ATZ_RATE_55PLUS:
            vertragsart = "Altersteilzeit"
            tariff = weighted_choice({k: v for k, v in TARIFF_DISTRIBUTION.items() if k != "TVAöD"})[0]
            step = np.random.choice([4, 5, 6], p=[0.3, 0.4, 0.3])
        else:
            vertragsart = weighted_choice({k: v for k, v in CONTRACT_TYPES.items() if k != "Ausbildung"})[0]
            tariff = weighted_choice({k: v for k, v in TARIFF_DISTRIBUTION.items() if k != "TVAöD"})[0]
            step = np.random.choice([3, 4, 5, 6], p=[0.2, 0.4, 0.3, 0.1])

        # Status
        if vertragsart == "Altersteilzeit" and age >= 60:
            status = "Altersteilzeit Freistellung"
            bezeichnung = "freigestellt"
        else:
            status = "Aktives Beschäftigungsverhältnis"
            bezeichnung = "aktiv"

        # Tarifart
        if vertragsart == "Ausbildung":
            tarifart = "Auszubildende-VKA"
        else:
            tarifart = "TVöD"

        data.append({
            "PersNr": pers_nr,
            "Vorname": np.nan,  # Aus Datenschutzgründen leer
            "Nachname": np.nan,  # Aus Datenschutzgründen leer
            "GebDatum": birth_date,
            "Text Gsch": gender,
            "Eintritt": entry_date,
            "Austritt": exit_date,
            "BsGrd": bs_grd,
            "Vertragsart": vertragsart,
            "MitarbKreisbez.": "Beschäftigte TVöD" if tarifart == "TVöD" else "Auszubildende",
            "MitarbGruppenbez.": "Angestellte",
            "Bankspezifisch": "bankspezifisch",
            "Bezeichnung": bezeichnung,
            "Status kundenindividuell": status,
            "Tarifarttext": tarifart,
            "Tarifgebiettext": "Westdeutschland",
            "TrfGr": tariff,
            "St": step,
        })

    df = pd.DataFrame(data)
    return df


def generate_planstellen(
    mitarbeiter_df: pd.DataFrame,
    n_planstellen: int = 1729
) -> pd.DataFrame:
    """
    Generiert Planstellen.XLSX mit exakt der Original-Struktur.

    Spalten (10):
    - Kürzel OrgEinheit, OrgEinheitNr, Organisationseinheit
    - Planstellennr, Planstellenkürzel, Planstelle
    - Sollarbeitszeit, Bewertung Tarifgruppe, Text Gehaltsband
    - Personalnummer
    """
    np.random.seed(43)

    # Mitarbeiter-Liste für Zuweisung
    available_employees = list(mitarbeiter_df["PersNr"].values)
    np.random.shuffle(available_employees)
    employee_idx = 0

    data = []
    planstellen_counter = 50001525  # Start wie im Original

    # Verteile Planstellen auf Org-Einheiten
    n_per_org = n_planstellen // len(ORG_UNITS)
    remainder = n_planstellen % len(ORG_UNITS)

    for i, org in enumerate(ORG_UNITS):
        n_stellen = n_per_org + (1 if i < remainder else 0)

        for j in range(n_stellen):
            # Tarifgruppe für Planstelle
            tariff = weighted_choice({k: v for k, v in TARIFF_DISTRIBUTION.items() if k != "TVAöD"})[0]

            # Personalnummer zuweisen (ca. 72% besetzt)
            if employee_idx < len(available_employees) and np.random.random() < 0.72:
                pers_nr = available_employees[employee_idx]
                employee_idx += 1
            else:
                pers_nr = np.nan

            planstellen_kuerzel = generate_planstellen_kuerzel(planstellen_counter, org["kuerzel"])

            data.append({
                "Kürzel OrgEinheit": org["kuerzel"],
                "OrgEinheitNr": org["nr"],
                "Organisationseinheit": org["name"],
                "Planstellennr": planstellen_counter,
                "Planstellenkürzel": planstellen_kuerzel,
                "Planstelle": f"{org['name'][:10]}/Stelle {j+1}",
                "Sollarbeitszeit": 39.0,
                "Bewertung Tarifgruppe": tariff,
                "Text Gehaltsband": np.nan,
                "Personalnummer": pers_nr,
            })

            planstellen_counter += 1

    df = pd.DataFrame(data)

    # Personalnummer als int wo vorhanden
    df["Personalnummer"] = df["Personalnummer"].astype("Int64")

    return df


def generate_atz(
    mitarbeiter_df: pd.DataFrame,
    reference_date: str = "2026-01-01"
) -> pd.DataFrame:
    """
    Generiert ATZ.xlsx mit exakt der Original-Struktur.

    Spalten (6):
    - PersNr, Beginn, Ende, Ende ATZ Vertrag, Modell, Phase
    """
    np.random.seed(44)
    ref_date = pd.to_datetime(reference_date)

    # Filtere ATZ-Mitarbeiter
    atz_employees = mitarbeiter_df[mitarbeiter_df["Vertragsart"] == "Altersteilzeit"]

    data = []

    for _, row in atz_employees.iterrows():
        pers_nr = row["PersNr"]
        age = (ref_date - row["GebDatum"]).days / 365.25

        # ATZ-Modell
        modell = np.random.choice(ATZ_MODELS)

        # ATZ-Dauer (typisch 4-6 Jahre)
        atz_duration_years = int(modell[-1])  # OAT5 -> 5 Jahre

        # Beginn der ATZ (55-62 Jahre)
        atz_start_age = np.random.randint(55, 63)
        atz_start = row["GebDatum"] + pd.DateOffset(years=atz_start_age)

        # Ende Arbeitsphase (Hälfte der ATZ-Zeit)
        arbeitsphase_ende = atz_start + pd.DateOffset(years=atz_duration_years // 2)

        # Ende ATZ Vertrag
        atz_ende = atz_start + pd.DateOffset(years=atz_duration_years)

        # Aktuelle Phase bestimmen
        if ref_date < arbeitsphase_ende:
            phase = "AR"  # Arbeitsphase
            ende_aktuell = arbeitsphase_ende
        else:
            phase = "FR"  # Freistellungsphase
            ende_aktuell = atz_ende

        data.append({
            "PersNr": pers_nr,
            "Beginn": atz_start,
            "Ende": ende_aktuell,
            "Ende ATZ Vertrag": atz_ende,
            "Modell": modell,
            "Phase": phase,
        })

    df = pd.DataFrame(data)
    return df


def generate_ausbildung(
    mitarbeiter_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Generiert Ausbildung.xlsx mit exakt der Original-Struktur.

    Spalten (4):
    - Personalnummer, Ausbildungsgruppe, BV Ausbildungsgruppentext, Betriebsvergleich Ausbildung
    """
    np.random.seed(45)

    data = []

    for _, row in mitarbeiter_df.iterrows():
        pers_nr = row["PersNr"]
        age = (pd.Timestamp.today() - row["GebDatum"]).days / 365.25 if pd.notna(row["GebDatum"]) else 40
        tariff = row["TrfGr"]

        # Ausbildungsgruppe basierend auf Alter und Tarif
        if row["Vertragsart"] == "Ausbildung" or age < 20:
            ausb_gruppe = 941  # derzeit Berufsausbildung
        elif tariff in ["E5", "E6", "E7"]:
            ausb_gruppe = weighted_choice({930: 0.4, 932: 0.6})[0]
        elif tariff in ["E8", "E9A", "E9B"]:
            ausb_gruppe = weighted_choice({932: 0.3, 931: 0.4, 933: 0.3})[0]
        elif tariff in ["E9C", "E10", "E11"]:
            ausb_gruppe = weighted_choice({931: 0.2, 933: 0.4, 935: 0.2, 937: 0.2})[0]
        else:  # E12+
            ausb_gruppe = weighted_choice({933: 0.3, 935: 0.2, 937: 0.25, 938: 0.25})[0]

        ausb_text, bv_ausb, _ = EDUCATION_GROUPS[ausb_gruppe]

        data.append({
            "Personalnummer": pers_nr,
            "Ausbildungsgruppe": ausb_gruppe,
            "BV Ausbildungsgruppentext": ausb_text,
            "Betriebsvergleich Ausbildung": bv_ausb,
        })

    df = pd.DataFrame(data)
    return df


def generate_all_files(
    n_employees: int = 1222,
    n_planstellen: int = 1729,
    reference_date: str = "2026-01-01",
    output_dir: str = None
) -> Dict[str, pd.DataFrame]:
    """
    Generiert alle 4 Dateien mit Original-Struktur.

    Returns:
        Dictionary mit DataFrames für jede Datei
    """
    print("=" * 60)
    print("Generiere synthetische Daten (Original-Struktur)")
    print("=" * 60)

    print("\n1. Generiere Mitarbeiter.xlsx...")
    mitarbeiter_df = generate_mitarbeiter(n_employees, reference_date)
    print(f"   -> {len(mitarbeiter_df)} Mitarbeiter generiert")

    print("\n2. Generiere Planstellen.XLSX...")
    planstellen_df = generate_planstellen(mitarbeiter_df, n_planstellen)
    vakanzen = planstellen_df["Personalnummer"].isna().sum()
    print(f"   -> {len(planstellen_df)} Planstellen generiert ({vakanzen} Vakanzen)")

    print("\n3. Generiere ATZ.xlsx...")
    atz_df = generate_atz(mitarbeiter_df, reference_date)
    print(f"   -> {len(atz_df)} ATZ-Verträge generiert")

    print("\n4. Generiere Ausbildung.xlsx...")
    ausbildung_df = generate_ausbildung(mitarbeiter_df)
    print(f"   -> {len(ausbildung_df)} Ausbildungs-Datensätze generiert")

    result = {
        "Mitarbeiter": mitarbeiter_df,
        "Planstellen": planstellen_df,
        "ATZ": atz_df,
        "Ausbildung": ausbildung_df,
    }

    # Speichern wenn output_dir angegeben
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

        print(f"\nSpeichere Dateien in: {output_dir}")

        mitarbeiter_df.to_excel(os.path.join(output_dir, "Mitarbeiter.xlsx"),
                                sheet_name="Sheet1", index=False)
        planstellen_df.to_excel(os.path.join(output_dir, "Planstellen.xlsx"),
                                sheet_name="Sheet1", index=False)
        atz_df.to_excel(os.path.join(output_dir, "ATZ.xlsx"),
                        sheet_name="Sheet1", index=False)
        ausbildung_df.to_excel(os.path.join(output_dir, "Ausbildung.xlsx"),
                               sheet_name="Sheet1", index=False)

        print("Fertig!")

    return result


# =============================================================================
# LEGACY-FUNKTIONEN (für Kompatibilität mit bestehendem Code)
# =============================================================================

def generate_synthetic_data(
    n_employees: int = 1200,
    n_planstellen: int = 1700,
    start_date: str = "2024-01-01",
    end_date: str = "2026-01-18"
) -> Dict[str, pd.DataFrame]:
    """
    Legacy-Funktion: Generiert kombinierte Daten im alten Format.

    Diese Funktion wird vom bestehenden loader.py verwendet.
    Sie konvertiert die neuen separaten Dateien in das alte Format.
    """
    # Generiere neue Daten
    files = generate_all_files(n_employees, n_planstellen)

    mitarbeiter = files["Mitarbeiter"]
    planstellen = files["Planstellen"]
    atz = files["ATZ"]
    ausbildung = files["Ausbildung"]

    # Kombiniere zu snapshot_detail (wie bisher erwartet)
    snapshot_df = create_combined_snapshot(mitarbeiter, planstellen, atz, ausbildung)

    # History Cube generieren
    history_df = generate_history_cube(snapshot_df, start_date, end_date)

    # Org Structure
    org_df = pd.DataFrame([
        {"Kürzel OrgEinheit": org["kuerzel"],
         "OrgEinheitNr": org["nr"],
         "Organisationseinheit": org["name"]}
        for org in ORG_UNITS
    ])

    return {
        "snapshot_detail": snapshot_df,
        "history_cube": history_df,
        "org_structure": org_df
    }


def create_combined_snapshot(
    mitarbeiter: pd.DataFrame,
    planstellen: pd.DataFrame,
    atz: pd.DataFrame,
    ausbildung: pd.DataFrame
) -> pd.DataFrame:
    """Kombiniert die 4 Dateien zu einem Snapshot wie bisher erwartet."""

    # Starte mit Planstellen
    df = planstellen.copy()

    # Rename für Konsistenz
    df = df.rename(columns={"Personalnummer": "PersNr_Plan"})

    # Merge Mitarbeiter-Daten
    df = df.merge(
        mitarbeiter,
        left_on="PersNr_Plan",
        right_on="PersNr",
        how="left",
        suffixes=("", "_ma")
    )

    # Merge Ausbildung
    df = df.merge(
        ausbildung[["Personalnummer", "BV Ausbildungsgruppentext"]],
        left_on="PersNr",
        right_on="Personalnummer",
        how="left"
    )
    df = df.rename(columns={"BV Ausbildungsgruppentext": "Ausbildung"})

    # Merge ATZ
    df = df.merge(
        atz[["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag", "Modell"]],
        on="PersNr",
        how="left",
        suffixes=("", "_atz")
    )

    # Berechne zusätzliche Felder
    df["Is_Vacant"] = df["PersNr"].isna()
    df["Personalnummer"] = df["PersNr_Plan"]

    # FTE-Felder
    df["FTE_person"] = df["BsGrd"].fillna(0) / 100.0
    df["Soll_FTE"] = 1.0
    df["FTE_assigned"] = df["FTE_person"]

    # Kosten berechnen
    df["Total_Cost_Year"] = df.apply(calculate_cost_row, axis=1)

    # Cleanup
    cols_to_keep = [
        "Kürzel OrgEinheit", "OrgEinheitNr", "Organisationseinheit",
        "Planstellennr", "Planstelle", "Sollarbeitszeit", "Bewertung Tarifgruppe",
        "Personalnummer", "Soll_FTE", "PersNr", "GebDatum", "Text Gsch",
        "Eintritt", "Austritt", "BsGrd", "Vertragsart", "Status kundenindividuell",
        "Tarifarttext", "TrfGr", "St", "FTE_person", "Total_Cost_Year",
        "Is_Vacant", "FTE_assigned", "Ausbildung",
        "Phase", "Beginn", "Ende", "Ende ATZ Vertrag", "Modell"
    ]

    # Nur existierende Spalten behalten
    cols_to_keep = [c for c in cols_to_keep if c in df.columns]
    df = df[cols_to_keep]

    return df


def calculate_cost_row(row) -> float:
    """Berechnet Jahreskosten für eine Zeile."""
    if row.get("Is_Vacant", True):
        return 0.0

    tariff = row.get("TrfGr", "E9A")
    step = row.get("St", 4)
    fte = row.get("FTE_person", 1.0)

    if pd.isna(tariff):
        tariff = "E9A"
    if pd.isna(step):
        step = 4
    if pd.isna(fte):
        fte = 1.0

    base = BASE_SALARY.get(str(tariff), 50000)
    step_factor = STEP_MULTIPLIER.get(int(step), 1.0)

    return base * step_factor * fte * EMPLOYER_COST_FACTOR


def generate_history_cube(
    snapshot_df: pd.DataFrame,
    start_date: str = "2024-01-01",
    end_date: str = "2026-01-18"
) -> pd.DataFrame:
    """Generiert History_Cube (monatliche Zeitreihen)."""
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    dates = pd.date_range(start=start, end=end, freq='MS')

    data = []

    for org_unit in snapshot_df["Kürzel OrgEinheit"].unique():
        org_data = snapshot_df[snapshot_df["Kürzel OrgEinheit"] == org_unit]

        base_headcount = org_data[~org_data["Is_Vacant"]]["PersNr"].nunique()
        base_fte = org_data["FTE_assigned"].sum()
        base_cost = org_data["Total_Cost_Year"].sum()
        base_vacancy = org_data["Is_Vacant"].sum()

        for date in dates:
            months_from_start = (date.year - start.year) * 12 + (date.month - start.month)
            trend_factor = 1.0 + (months_from_start / 100)
            noise = np.random.normal(1.0, 0.02)

            data.append({
                "Kürzel OrgEinheit": org_unit,
                "Date": date,
                "Headcount": int(base_headcount * trend_factor * noise),
                "FTE": base_fte * trend_factor * noise,
                "Total_Cost": base_cost * trend_factor * noise,
                "Vacancy_Count": int(base_vacancy * noise),
            })

    return pd.DataFrame(data)


def save_to_excel(data_dict: Dict[str, pd.DataFrame], filepath: str):
    """Legacy: Speichert kombinierte Daten in eine Excel-Datei."""
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        for sheet_name, df in data_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    print(f"Daten gespeichert in: {filepath}")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generiere synthetische HR-Daten")
    parser.add_argument("--format", choices=["original", "combined"], default="original",
                        help="Ausgabeformat: 'original' (4 Dateien) oder 'combined' (1 Datei)")
    parser.add_argument("--output", type=str, default="data/sample_data",
                        help="Ausgabeverzeichnis")
    args = parser.parse_args()

    if args.format == "original":
        # Generiere 4 separate Dateien wie im Original
        generate_all_files(
            n_employees=1222,
            n_planstellen=1729,
            output_dir=args.output
        )
    else:
        # Generiere kombinierte Datei (Legacy)
        data = generate_synthetic_data()
        output_path = os.path.join(args.output, "hr_data.xlsx")
        os.makedirs(args.output, exist_ok=True)
        save_to_excel(data, output_path)

    print("\n" + "=" * 60)
    print("FERTIG!")
    print("=" * 60)
