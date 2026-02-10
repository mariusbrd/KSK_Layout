"""
Synthetische Testdaten-Generierung für HR Pulse Dashboard.

Generiert realistische HR-Daten die 1:1 die Original-Datenstruktur abbilden:
- Mitarbeiter.xlsx (18 Spalten)
- Planstellen.XLSX (10 Spalten)
- ATZ.xlsx (6 Spalten)
- Ausbildung.xlsx (4 Spalten)

Die Daten bilden eine süddeutsche Sparkasse/Bank mit ~1200 Mitarbeitenden ab.

VERSION 2.0 - KORRIGIERT:
- Azubi-Sollarbeitszeit = 39 (nicht 0.01)
- ATZ-Merge wird korrekt vorbereitet (ist_atz_fr Flag)
- Soll_FTE korrekt berechnet
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR,
    DEFAULT_COHORTS, TARIFF_GROUPS
)

from dataloader.loader import (
    EDUCATION_MAPPING,
    EDUCATION_RANKING,
    derive_atz_fields,
    calculate_cost_row,
    create_combined_snapshot,
)

ID_PAD_LENGTH = 6


def normalize_persnr(series: pd.Series) -> pd.Series:
    """Normalisiert Personalnummern zu String mit führenden Nullen."""
    return series.apply(
        lambda x: str(int(x)).zfill(ID_PAD_LENGTH) if pd.notna(x) else pd.NA
    )


# =============================================================================
# VERTEILUNGEN (basierend auf Original-Daten)
# =============================================================================

# Organisationseinheiten
ORG_UNITS = [
    {"kuerzel": "800", "nr": 50002405, "name": "Steuerung"},
    {"kuerzel": "801", "nr": 50002406, "name": "Compliance"},
    {"kuerzel": "802", "nr": 50002407, "name": "Controlling und Rechnungswesen"},
    {"kuerzel": "803", "nr": 50002408, "name": "CR Controlling"},
    {"kuerzel": "804", "nr": 50002409, "name": "CR Rechnungswesen"},
    {"kuerzel": "810", "nr": 50002410, "name": "Treasury"},
    {"kuerzel": "815", "nr": 50002411, "name": "Facility Management"},
    {"kuerzel": "820", "nr": 50002412, "name": "Risikomanagement"},
    {"kuerzel": "826", "nr": 50002413, "name": "Marktfolge Kredit"},
    {"kuerzel": "830", "nr": 50002414, "name": "Recht"},
    {"kuerzel": "840", "nr": 50002415, "name": "Rechnungswesen"},
    {"kuerzel": "850", "nr": 50002416, "name": "Marketing"},
    {"kuerzel": "025", "nr": 50002417, "name": "Privatkunden Region Nord"},
    {"kuerzel": "026", "nr": 50002418, "name": "Privatkunden Region Süd"},
    {"kuerzel": "027", "nr": 50002419, "name": "Privatkunden Region West"},
    {"kuerzel": "028", "nr": 50002420, "name": "Firmenkunden"},
    {"kuerzel": "029", "nr": 50002421, "name": "Firmenkunden Spezial"},
    {"kuerzel": "030", "nr": 50002422, "name": "Baufinanzierung"},
    {"kuerzel": "031", "nr": 50002423, "name": "Immobiliencenter"},
    {"kuerzel": "100", "nr": 50002424, "name": "Vorstand"},
    {"kuerzel": "110", "nr": 50002425, "name": "Vorstandsstab"},
    {"kuerzel": "191", "nr": 50002426, "name": "Vertriebssteuerung"},
    {"kuerzel": "192", "nr": 50002427, "name": "Vertriebsmanagement"},
    {"kuerzel": "200", "nr": 50002428, "name": "Filiale Hauptstelle"},
    {"kuerzel": "201", "nr": 50002429, "name": "Filiale Nord"},
    {"kuerzel": "202", "nr": 50002430, "name": "Filiale Süd"},
    {"kuerzel": "203", "nr": 50002431, "name": "Filiale West"},
    {"kuerzel": "204", "nr": 50002432, "name": "Filiale Ost"},
    {"kuerzel": "205", "nr": 50002433, "name": "SB-Center 1"},
    {"kuerzel": "206", "nr": 50002434, "name": "SB-Center 2"},
    {"kuerzel": "300", "nr": 50002435, "name": "Personal und Organisation"},
    {"kuerzel": "330", "nr": 50002436, "name": "Personalmanagement"},
    {"kuerzel": "331", "nr": 50002437, "name": "Personalentwicklung"},
    {"kuerzel": "332", "nr": 50002438, "name": "Personalservice"},
    {"kuerzel": "400", "nr": 50002439, "name": "IT"},
    {"kuerzel": "411", "nr": 50002440, "name": "IT Anwendungen"},
    {"kuerzel": "412", "nr": 50002441, "name": "IT Infrastruktur"},
    {"kuerzel": "420", "nr": 50002442, "name": "Revision"},
    {"kuerzel": "500", "nr": 50002443, "name": "Kreditmanagement"},
    {"kuerzel": "510", "nr": 50002444, "name": "Kreditbearbeitung"},
    {"kuerzel": "520", "nr": 50002445, "name": "Kreditcontrolling"},
    {"kuerzel": "600", "nr": 50002446, "name": "Zahlungsverkehr"},
    {"kuerzel": "610", "nr": 50002447, "name": "Zahlungsverkehr Inland"},
    {"kuerzel": "620", "nr": 50002448, "name": "Zahlungsverkehr Ausland"},
    {"kuerzel": "700", "nr": 50002449, "name": "Wertpapiere"},
    {"kuerzel": "710", "nr": 50002450, "name": "Wertpapierservice"},
    {"kuerzel": "720", "nr": 50002451, "name": "Vermögensberatung"},
    {"kuerzel": "900", "nr": 50002452, "name": "Sonstige"},
    {"kuerzel": "9910", "nr": 50031255, "name": "Auszubildende"},  # Azubi-OrgEinheit
]

# Altersverteilung
AGE_DISTRIBUTION = {
    (16, 19): 0.07,   # Azubis
    (20, 29): 0.22,
    (30, 39): 0.16,
    (40, 49): 0.20,
    (50, 59): 0.25,
    (60, 67): 0.10
}

# Geschlechterverteilung
GENDER_DISTRIBUTION = {"männlich": 0.37, "weiblich": 0.63}

# Beschäftigungsgrad-Verteilung (in Prozent, 0-100)
EMPLOYMENT_DISTRIBUTION = {
    100.0: 0.56,
    75.0: 0.16,
    50.0: 0.23,
    25.0: 0.05
}

# Tarifgruppen-Verteilung (basierend auf Original)
TARIFF_DISTRIBUTION = {
    "E5": 0.01, "E6": 0.20, "E7": 0.03, "E8": 0.11,
    "E9A": 0.06, "E9B": 0.05, "E9C": 0.08,
    "E10": 0.13, "E11": 0.13, "E12": 0.02,
    "E13": 0.05, "E14": 0.01, "E15": 0.02,
    "TVAÖD": 0.10  # Auszubildende
}

# Vertragsarten (basierend auf Original)
CONTRACT_TYPES = {
    "Unbefristet": 0.84,
    "Zeitvertrag": 0.01,
    "Ausbildung": 0.11,
    "Werkstudentenvertrag": 0.002,
    "Trainee": 0.002,
    "Altersteilzeit": 0.04
}

# Ausbildungsgruppen (basierend auf Original)
EDUCATION_GROUPS = {
    930: ("kfm Berufsabschluss", 99560, 0.19),
    931: ("Sparkassen/Bankfachwirt", 99561, 0.17),
    932: ("Bankberufsabschluss", 99562, 0.17),
    933: ("SPK/Bankbetriebswirt", 99563, 0.21),
    934: ("Studium Lehrinstitut", 99564, 0.01),
    935: ("Bachelor FH", 99565, 0.08),
    936: ("Bachelor Universität", 99566, 0.01),
    937: ("Master FH", 99567, 0.02),
    938: ("Master Universität", 99568, 0.03),
    939: ("nicht kfm Berufsabschluss", 99569, 0.03),
    940: ("ohne Berufsabschluss", 99570, 0.01),
    941: ("derzeit Berufsausbildung", 99571, 0.07),
}

# ATZ-Modelle
ATZ_MODELS = ["OAT5"]  # Im Original nur OAT5
ATZ_RATE_55PLUS = 0.15  # 15% der 55+ in ATZ


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

    for pers_nr in range(28, 28 + n_employees):
        # Geschlecht
        gender = weighted_choice(GENDER_DISTRIBUTION)[0]

        # Alter generieren
        age_range = weighted_choice(AGE_DISTRIBUTION)[0]
        age = np.random.randint(age_range[0], age_range[1] + 1)
        birth_date = ref_date - pd.DateOffset(years=age)

        # Eintrittsdatum
        max_tenure = min(age - 16, 45)
        tenure_years = int(min(np.random.exponential(10), max_tenure))
        entry_date = ref_date - pd.DateOffset(years=tenure_years)

        # Austrittsdatum (9999-12-31 für aktive MA)
        exit_date = pd.Timestamp("9999-12-31")

        # Beschäftigungsgrad
        bs_grd = weighted_choice(EMPLOYMENT_DISTRIBUTION)[0]

        # Vertragsart und Tarifgruppe
        if age < 20:
            vertragsart = "Ausbildung"
            tariff = "TVAÖD"
            mitarb_gruppe = "Auszubildende"
            tarifart = "Auszubildende-VKA"
        elif age >= 55 and np.random.random() < ATZ_RATE_55PLUS:
            vertragsart = "Altersteilzeit"
            tariff = weighted_choice({k: v for k, v in TARIFF_DISTRIBUTION.items() if k != "TVAÖD"})[0]
            mitarb_gruppe = "Angestellte"
            tarifart = "TVÖD"
        else:
            vertragsart = weighted_choice(CONTRACT_TYPES)[0]
            if vertragsart == "Ausbildung":
                tariff = "TVAÖD"
                mitarb_gruppe = "Auszubildende"
                tarifart = "Auszubildende-VKA"
            else:
                tariff = weighted_choice({k: v for k, v in TARIFF_DISTRIBUTION.items() if k != "TVAÖD"})[0]
                mitarb_gruppe = "Angestellte"
                tarifart = "TVÖD"

        # Erfahrungsstufe (abhängig von Tenure)
        if tenure_years < 1:
            step = 1
        elif tenure_years < 3:
            step = 2
        elif tenure_years < 6:
            step = 3
        elif tenure_years < 10:
            step = 4
        elif tenure_years < 15:
            step = 5
        else:
            step = 6

        # Status
        # Ca. 4% ruhend (Elternzeit, Sabbatical etc.) - NICHT ATZ!
        if vertragsart != "Altersteilzeit" and np.random.random() < 0.04:
            status = "Ruhendes Beschäftigungsverhältnis"
        else:
            status = "Aktives Beschäftigungsverhältnis"

        data.append({
            "PersNr": pers_nr,
            "Vorname": np.nan,  # Anonymisiert
            "Nachname": np.nan,  # Anonymisiert
            "GebDatum": birth_date,
            "Text Gsch": gender,
            "Eintritt": entry_date,
            "Austritt": exit_date,
            "BsGrd": bs_grd,
            "Vertragsart": vertragsart,
            "MitarbKreisbez.": "Beschäftigte TVÖD",
            "MitarbGruppenbez.": mitarb_gruppe,
            "Bankspezifisch": "bankspezifisch" if np.random.random() > 0.1 else "nicht bankspezifisch",
            "Bezeichnung": "aktiv",
            "Status kundenindividuell": status,
            "Tarifarttext": tarifart,
            "Tarifgebiettext": "Westdeutschland",
            "TrfGr": tariff,
            "St": step
        })

    return pd.DataFrame(data)


def generate_planstellen(
    mitarbeiter_df: pd.DataFrame,
    n_planstellen: int = 1728,
    vacancy_rate: float = 0.28
) -> pd.DataFrame:
    """
    Generiert Planstellen.XLSX.
    
    KORRIGIERT: Azubi-Sollarbeitszeit = 39 (nicht 0.01)
    """
    np.random.seed(43)
    
    data = []
    planstellen_counter = 50000001
    
    # Zuordnung Mitarbeiter zu Org-Einheiten
    mitarbeiter_list = mitarbeiter_df.to_dict('records')
    azubis = [m for m in mitarbeiter_list if m['MitarbGruppenbez.'] == 'Auszubildende']
    regulaere = [m for m in mitarbeiter_list if m['MitarbGruppenbez.'] != 'Auszubildende']
    
    # Azubi-Planstellen
    azubi_org = [o for o in ORG_UNITS if o['kuerzel'] == '9910'][0]
    for azubi in azubis:
        data.append({
            "Kürzel OrgEinheit": azubi_org['kuerzel'],
            "OrgEinheitNr": float(azubi_org['nr']),
            "Organisationseinheit": azubi_org['name'],
            "Planstellennr": float(planstellen_counter),
            "Planstellenkürzel": f"AZU{planstellen_counter % 1000:03d}",
            "Planstelle": "Auszubildende/r",
            "Sollarbeitszeit": 39.0,  # KORRIGIERT: War 0.01 im Original
            "Bewertung Tarifgruppe": "TVAÖD",
            "Text Gehaltsband": np.nan,
            "Personalnummer": float(azubi['PersNr'])
        })
        planstellen_counter += 1
    
    # Reguläre Planstellen
    non_azubi_orgs = [o for o in ORG_UNITS if o['kuerzel'] != '9910']
    
    for ma in regulaere:
        org = non_azubi_orgs[np.random.randint(0, len(non_azubi_orgs))]
        soll_az = np.random.choice([39.0, 30.0, 20.0], p=[0.7, 0.2, 0.1])
        
        data.append({
            "Kürzel OrgEinheit": org['kuerzel'],
            "OrgEinheitNr": float(org['nr']),
            "Organisationseinheit": org['name'],
            "Planstellennr": float(planstellen_counter),
            "Planstellenkürzel": f"REG{planstellen_counter % 10000:04d}",
            "Planstelle": f"Sachbearbeiter/in {org['name']}",
            "Sollarbeitszeit": soll_az,
            "Bewertung Tarifgruppe": ma['TrfGr'],
            "Text Gehaltsband": np.nan,
            "Personalnummer": float(ma['PersNr'])
        })
        planstellen_counter += 1
    
    # Vakante Planstellen
    n_vacant = int(n_planstellen * vacancy_rate)
    for _ in range(n_vacant):
        org = non_azubi_orgs[np.random.randint(0, len(non_azubi_orgs))]
        tarif = weighted_choice({k: v for k, v in TARIFF_DISTRIBUTION.items() if k != "TVAÖD"})[0]
        
        data.append({
            "Kürzel OrgEinheit": org['kuerzel'],
            "OrgEinheitNr": float(org['nr']),
            "Organisationseinheit": org['name'],
            "Planstellennr": float(planstellen_counter),
            "Planstellenkürzel": f"VAK{planstellen_counter % 10000:04d}",
            "Planstelle": f"Vakanz {org['name']}",
            "Sollarbeitszeit": 39.0,
            "Bewertung Tarifgruppe": tarif,
            "Text Gehaltsband": np.nan,
            "Personalnummer": np.nan  # Vakant
        })
        planstellen_counter += 1
    
    return pd.DataFrame(data)


def generate_atz(mitarbeiter_df: pd.DataFrame, reference_date: str = "2026-01-01") -> pd.DataFrame:
    """
    Generiert ATZ.xlsx mit AR und FR Phasen.
    
    Jede ATZ-Person hat 2 Zeilen (AR + FR).
    """
    np.random.seed(44)
    ref_date = pd.to_datetime(reference_date)
    
    # Finde ATZ-Mitarbeiter
    atz_mitarbeiter = mitarbeiter_df[mitarbeiter_df['Vertragsart'] == 'Altersteilzeit']
    
    data = []
    
    for _, ma in atz_mitarbeiter.iterrows():
        # ATZ-Dauer: 4-6 Jahre total
        atz_duration_years = np.random.randint(4, 7)
        half_duration = atz_duration_years / 2
        
        # ATZ-Start: Zufällig in den letzten Jahren
        years_since_start = np.random.uniform(0, atz_duration_years)
        atz_start = ref_date - pd.DateOffset(days=int(years_since_start * 365.25))
        
        # Phasen berechnen
        ar_end = atz_start + pd.DateOffset(days=int(half_duration * 365.25))
        fr_start = ar_end + pd.DateOffset(days=1)
        atz_end = atz_start + pd.DateOffset(years=atz_duration_years)
        
        # AR-Phase
        data.append({
            "PersNr": ma['PersNr'],
            "Beginn": atz_start,
            "Ende": ar_end,
            "Ende ATZ Vertrag": atz_end,
            "Modell": "OAT5",
            "Phase": "AR"
        })
        
        # FR-Phase
        data.append({
            "PersNr": ma['PersNr'],
            "Beginn": fr_start,
            "Ende": atz_end,
            "Ende ATZ Vertrag": atz_end,
            "Modell": "OAT5",
            "Phase": "FR"
        })
    
    return pd.DataFrame(data)


def generate_ausbildung(mitarbeiter_df: pd.DataFrame) -> pd.DataFrame:
    """Generiert Ausbildung.xlsx."""
    np.random.seed(45)
    
    data = []
    
    for _, ma in mitarbeiter_df.iterrows():
        # Azubis bekommen "derzeit Berufsausbildung"
        if ma['MitarbGruppenbez.'] == 'Auszubildende':
            edu_key = 941
        else:
            edu_key = weighted_choice(
                {k: v for k, v in EDUCATION_GROUPS.items() if k != 941}
            )[0]
        
        edu_text, edu_bv, _ = EDUCATION_GROUPS[edu_key]
        
        data.append({
            "Personalnummer": ma['PersNr'],
            "Ausbildungsgruppe": edu_key,
            "BV Ausbildungsgruppentext": edu_text,
            "Betriebsvergleich Ausbildung": edu_bv
        })
    
    return pd.DataFrame(data)


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


def generate_all_files(
    n_employees: int = 1222,
    n_planstellen: int = 1728,
    output_dir: Optional[str] = None
) -> Dict[str, pd.DataFrame]:
    """Generiert alle 4 Original-Dateien."""
    
    print("Generiere Mitarbeiter...")
    mitarbeiter = generate_mitarbeiter(n_employees)
    
    print("Generiere Planstellen...")
    planstellen = generate_planstellen(mitarbeiter, n_planstellen)
    
    print("Generiere ATZ...")
    atz = generate_atz(mitarbeiter)
    
    print("Generiere Ausbildung...")
    ausbildung = generate_ausbildung(mitarbeiter)
    
    files = {
        "Mitarbeiter": mitarbeiter,
        "Planstellen": planstellen,
        "ATZ": atz,
        "Ausbildung": ausbildung
    }
    
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        for name, df in files.items():
            filepath = os.path.join(output_dir, f"{name}.xlsx")
            df.to_excel(filepath, index=False)
            print(f"  Gespeichert: {filepath}")
            
        # Create marker file for synthetic data
        marker_path = os.path.join(output_dir, ".is_synthetic")
        with open(marker_path, "w") as f:
            f.write("This directory contains synthetic data.")

    
    return files


def generate_synthetic_data(
    n_employees: int = 1222,
    n_planstellen: int = 1728,
    start_date: str = "2024-01-01",
    end_date: str = "2026-01-18"
) -> Dict[str, pd.DataFrame]:
    """Generiert alle Daten für das Dashboard."""
    
    files = generate_all_files(n_employees, n_planstellen)
    
    snapshot_df = create_combined_snapshot(
        files["Mitarbeiter"],
        files["Planstellen"],
        files["ATZ"],
        files["Ausbildung"]
    )
    
    history_df = generate_history_cube(snapshot_df, start_date, end_date)
    
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


def save_to_excel(data_dict: Dict[str, pd.DataFrame], filepath: str):
    """Speichert kombinierte Daten in eine Excel-Datei."""
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

    print("=" * 60)
    print("Generiere synthetische HR-Daten (KORRIGIERT v2.0)")
    print("=" * 60)

    if args.format == "original":
        generate_all_files(
            n_employees=1222,
            n_planstellen=1728,
            output_dir=args.output
        )
    else:
        data = generate_synthetic_data()
        output_path = os.path.join(args.output, "hr_data.xlsx")
        os.makedirs(args.output, exist_ok=True)
        save_to_excel(data, output_path)

    print("\n" + "=" * 60)
    print("FERTIG!")
    print("=" * 60)
