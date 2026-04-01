"""
Synthetische Testdaten-Generierung für HR Pulse Dashboard.

Generiert realistische HR-Daten die 1:1 die Original-Datenstruktur abbilden:
- Mitarbeiter.xlsx (18 Spalten)
- Planstellen.XLSX (10 Spalten)
- ATZ.xlsx (6 Spalten)
- Ausbildung.xlsx (4 Spalten)

Die Daten bilden eine süddeutsche Bank mit ~1200 Mitarbeitenden ab.

VERSION 2.0 - KORRIGIERT:
- Azubi-Sollarbeitszeit = 39 (nicht 0.01)
- ATZ-Merge wird korrekt vorbereitet (ist_atz_fr Flag)
- Soll_FTE korrekt berechnet
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple, Optional
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
from utils.text_normalization import normalize_dashboard_text

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
# Organisationseinheiten (Anonymisiert)
ORG_UNITS = [
    # Stäbe & Steuerung
    {"kuerzel": "800", "nr": 50002405, "name": "Unternehmenssteuerung"},
    {"kuerzel": "801", "nr": 50002406, "name": "Compliance & Recht"},
    {"kuerzel": "802", "nr": 50002407, "name": "Finanzen & Controlling"},
    {"kuerzel": "810", "nr": 50002410, "name": "Treasury"},
    {"kuerzel": "820", "nr": 50002412, "name": "Risikomanagement"},
    {"kuerzel": "300", "nr": 50002435, "name": "Human Resources"},
    {"kuerzel": "400", "nr": 50002439, "name": "IT & Organisation"},
    {"kuerzel": "420", "nr": 50002442, "name": "Interne Revision"},
    
    # Markt
    {"kuerzel": "FK", "nr": 50002418, "name": "Firmenkunden"},
    {"kuerzel": "Immo", "nr": 50002422, "name": "Immobilienfinanzierung"},
    {"kuerzel": "RegN", "nr": 50002417, "name": "Privatkunden Region Nord"},
    {"kuerzel": "RegS", "nr": 50002418, "name": "Privatkunden Region Süd"},
    {"kuerzel": "RegW", "nr": 50002419, "name": "Privatkunden Region West"},
    {"kuerzel": "RegO", "nr": 50002420, "name": "Privatkunden Region Ost"},
    
    # Marktfolge / Betrieb
    {"kuerzel": "MFK", "nr": 50002413, "name": "Marktfolge Kredit"},
    {"kuerzel": "MFP", "nr": 50002444, "name": "Marktfolge Passiv"},
    {"kuerzel": "ZV", "nr": 50002446, "name": "Zahlungsverkehr"},
    {"kuerzel": "Serv", "nr": 50002438, "name": "Kundenservice"},
    
    # Führung
    {"kuerzel": "100", "nr": 50002424, "name": "Vorstand"},
    {"kuerzel": "110", "nr": 50002425, "name": "Vorstandsstab"},
    {"kuerzel": "900", "nr": 50002452, "name": "Sonstige"},
    {"kuerzel": "9910", "nr": 50031255, "name": "Auszubildende"},
]

ORG_BY_KEY = {org["kuerzel"]: org for org in ORG_UNITS}

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
    931: ("Bankfachwirt", 99561, 0.17),
    932: ("Bankberufsabschluss", 99562, 0.17),
    933: ("Bankbetriebswirt", 99563, 0.21),
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

def weighted_choice(choices: Dict, size: int = 1, rng: Optional[np.random.Generator] = None):
    """Zufallsauswahl basierend auf Gewichtung."""
    rng = rng or np.random.default_rng()
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
        indices = rng.choice(len(items), size=size, p=weights)
        return [items[i] for i in indices]

    return rng.choice(items, size=size, p=weights)


# =============================================================================
# BUSINESS-ANNAHMEN FÃœR REALISTISCHE TESTDATEN
# =============================================================================

SYNTHETIC_SEEDS = {
    "employees": 42,
    "positions": 43,
    "atz": 44,
    "history": 45,
}

NON_AZUBI_TARIFF_DISTRIBUTION = {
    k: v for k, v in TARIFF_DISTRIBUTION.items() if k != "TVAÃ–D"
}

SYNTHETIC_ASSUMPTIONS = {
    "technical_only_employee_share": 0.05,
    "extra_technical_position_share": 0.12,
    "missing_actual_grade_share": 0.015,
    "regular_position_hours": {
        39.0: 0.72,
        35.0: 0.05,
        30.0: 0.11,
        25.0: 0.03,
        20.0: 0.06,
        15.0: 0.03,
    },
    "technical_position_hours": {
        0.01: 0.78,
        0.1: 0.14,
        0.0: 0.08,
    },
    "vacancy_type_mix": {
        "regular": 0.82,
        "technical": 0.18,
    },
    "soll_case_weights": {
        "exact": 0.50,
        "in_band_up": 0.17,
        "over_small": 0.15,
        "under_small": 0.09,
        "over_large": 0.03,
        "under_large": 0.02,
        "no_soll": 0.04,
    },
}

EDUCATION_BY_AGE = {
    "young": {
        930: 0.21,
        932: 0.18,
        931: 0.10,
        939: 0.16,
        940: 0.10,
        935: 0.13,
        936: 0.04,
        933: 0.05,
        937: 0.02,
        938: 0.01,
    },
    "mid": {
        930: 0.17,
        932: 0.17,
        931: 0.17,
        933: 0.16,
        935: 0.11,
        936: 0.04,
        937: 0.03,
        938: 0.02,
        939: 0.10,
        940: 0.03,
    },
    "senior": {
        930: 0.16,
        932: 0.16,
        931: 0.18,
        933: 0.19,
        935: 0.08,
        936: 0.03,
        937: 0.03,
        938: 0.02,
        939: 0.12,
        940: 0.03,
    },
}

EDUCATION_GRADE_BANDS = {
    930: ("E5", "E8"),
    931: ("E8", "E11"),
    932: ("E6", "E9C"),
    933: ("E10", "E13"),
    934: ("E11", "E13"),
    935: ("E9C", "E12"),
    936: ("E10", "E12"),
    937: ("E11", "E13"),
    938: ("E12", "E14"),
    939: ("E4", "E8"),
    940: ("E3", "E6"),
    941: ("TVAÃ–D", "TVAÃ–D"),
}

REGULAR_ORG_GROUPS = {
    "front": ["RegN", "RegS", "RegW", "RegO", "Serv", "ZV"],
    "specialist": ["FK", "Immo", "MFK", "MFP", "300", "400"],
    "hq": ["800", "801", "802", "810", "820", "420", "110", "900"],
}

ROLE_TITLES = {
    "front_low": ["Serviceberater/in", "Kundenberater/in", "Sachbearbeiter/in Service"],
    "front_mid": ["Berater/in Privatkunden", "Spezialist/in Kundenservice", "Firmenkundenbetreuer/in"],
    "front_high": ["Senior Berater/in", "Lead Firmenkunden", "Fachverantwortung Markt"],
    "specialist_low": ["Sachbearbeiter/in", "Assistenz", "Operations Specialist"],
    "specialist_mid": ["Spezialist/in", "Referent/in", "Analyst/in"],
    "specialist_high": ["Senior Spezialist/in", "Projektleitung", "Fachexperte/in"],
    "hq_low": ["Sachbearbeitung Stab", "Koordinator/in", "Referent/in Junior"],
    "hq_mid": ["Referent/in", "Controller/in", "Business Partner"],
    "hq_high": ["Senior Referent/in", "Leitung / Expert/in", "Strategische/r Expert/in"],
    "technical": ["Technische Zusatzstelle", "Systemplatzhalter", "Restplanstelle"],
    "vacancy": ["Vakanz", "Offene Stelle", "Nachbesetzung"],
}

GRADE_ORDER = list(TARIFF_GROUPS)
GRADE_TO_INDEX = {grade: idx for idx, grade in enumerate(GRADE_ORDER)}


def _grade_shift(grade: str, delta: int) -> str:
    if grade not in GRADE_TO_INDEX:
        return grade
    idx = GRADE_TO_INDEX[grade]
    return GRADE_ORDER[max(0, min(len(GRADE_ORDER) - 1, idx + delta))]


def _pick_from_weighted_dict(distribution: Dict, rng: np.random.Generator):
    return weighted_choice(distribution, rng=rng)[0]


def _normalize_generated_frame(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result = result.rename(
        columns=lambda col: normalize_dashboard_text(col) if isinstance(col, str) else col
    )
    object_columns = result.select_dtypes(include=["object", "string"]).columns
    for col in object_columns:
        result[col] = result[col].map(
            lambda value: normalize_dashboard_text(value) if isinstance(value, str) else value
        )
    return result


def _choose_education_key(age: int, rng: np.random.Generator) -> int:
    if age < 20:
        return 941
    if age < 30:
        return int(_pick_from_weighted_dict(EDUCATION_BY_AGE["young"], rng))
    if age < 50:
        return int(_pick_from_weighted_dict(EDUCATION_BY_AGE["mid"], rng))
    return int(_pick_from_weighted_dict(EDUCATION_BY_AGE["senior"], rng))


def _choose_actual_grade(education_key: int, age: int, rng: np.random.Generator) -> str:
    if education_key == 941:
        return "TVAÃ–D"

    low_grade, high_grade = EDUCATION_GRADE_BANDS[education_key]
    low_idx = GRADE_TO_INDEX[low_grade]
    high_idx = GRADE_TO_INDEX[high_grade]
    career_factor = np.clip((age - 22) / 28, 0.0, 1.0)
    center = low_idx + (high_idx - low_idx) * career_factor
    sampled = int(round(rng.normal(center, 0.9)))
    return GRADE_ORDER[max(low_idx, min(high_idx, sampled))]


def _choose_regular_org(actual_grade: str, rng: np.random.Generator) -> dict:
    rank = GRADE_TO_INDEX.get(actual_grade, 0)
    if rank <= GRADE_TO_INDEX["E8"]:
        group = _pick_from_weighted_dict({"front": 0.68, "specialist": 0.24, "hq": 0.08}, rng)
    elif rank <= GRADE_TO_INDEX["E11"]:
        group = _pick_from_weighted_dict({"front": 0.34, "specialist": 0.48, "hq": 0.18}, rng)
    else:
        group = _pick_from_weighted_dict({"front": 0.10, "specialist": 0.42, "hq": 0.48}, rng)
    org_key = _pick_from_weighted_dict({key: 1.0 for key in REGULAR_ORG_GROUPS[group]}, rng)
    return next(org for org in ORG_UNITS if org["kuerzel"] == org_key)


def _choose_role_title(group_key: str, actual_grade: str, rng: np.random.Generator) -> str:
    rank = GRADE_TO_INDEX.get(actual_grade, 0)
    if rank <= GRADE_TO_INDEX["E8"]:
        tier = "low"
    elif rank <= GRADE_TO_INDEX["E11"]:
        tier = "mid"
    else:
        tier = "high"
    return str(_pick_from_weighted_dict({title: 1.0 for title in ROLE_TITLES[f"{group_key}_{tier}"]}, rng))


def _build_soll_profile(actual_grade: Optional[str], rng: np.random.Generator) -> Tuple[str, Any, str]:
    case = str(_pick_from_weighted_dict(SYNTHETIC_ASSUMPTIONS["soll_case_weights"], rng))
    fallback_grade = str(_pick_from_weighted_dict(NON_AZUBI_TARIFF_DISTRIBUTION, rng))
    actual = actual_grade if actual_grade in GRADE_TO_INDEX else fallback_grade

    if case == "exact":
        return actual, np.nan, case
    if case == "in_band_up":
        lower = _grade_shift(actual, -1)
        upper = _grade_shift(actual, 1)
        if lower == actual:
            lower = _grade_shift(actual, -2)
        if upper == actual:
            upper = _grade_shift(actual, 2)
        return lower, f"bis {upper}", case
    if case == "over_small":
        lower = _grade_shift(actual, -1)
        return lower, np.nan, case
    if case == "under_small":
        higher = _grade_shift(actual, 1)
        return higher, np.nan, case
    if case == "over_large":
        lower = _grade_shift(actual, -2)
        upper = _grade_shift(actual, -1)
        return lower, f"bis {upper}", case
    if case == "under_large":
        higher = _grade_shift(actual, 2)
        return higher, np.nan, case
    if case == "no_soll":
        return "", np.nan, case
    return actual, np.nan, "exact"


def _build_employee_blueprints(
    n_employees: int,
    reference_date: str = "2025-12-31",
    seed: int = SYNTHETIC_SEEDS["employees"],
) -> List[Dict[str, Any]]:
    rng = np.random.default_rng(seed)
    ref_date = pd.to_datetime(reference_date)
    blueprints: List[Dict[str, Any]] = []

    for pers_nr in range(28, 28 + n_employees):
        gender = str(_pick_from_weighted_dict(GENDER_DISTRIBUTION, rng))
        age_range = _pick_from_weighted_dict(AGE_DISTRIBUTION, rng)
        age = int(rng.integers(age_range[0], age_range[1] + 1))
        birth_date = ref_date - pd.DateOffset(years=age)

        max_tenure = max(0, min(age - 16, 45))
        tenure_years = int(min(rng.exponential(10), max_tenure))
        entry_date = ref_date - pd.DateOffset(years=tenure_years)
        exit_date = pd.Timestamp("9999-12-31")
        bs_grd = float(_pick_from_weighted_dict(EMPLOYMENT_DISTRIBUTION, rng))

        education_key = _choose_education_key(age, rng)
        if education_key == 941:
            vertragsart = "Ausbildung"
            tariff = "TVAÃ–D"
            mitarb_gruppe = "Auszubildende"
            tarifart = "Auszubildende-VKA"
        elif age >= 58 and rng.random() < 0.12:
            vertragsart = "Altersteilzeit"
            tariff = _choose_actual_grade(education_key, age, rng)
            mitarb_gruppe = "Angestellte"
            tarifart = "TVÃ–D"
        elif age <= 26 and rng.random() < 0.015:
            vertragsart = "Trainee"
            tariff = _choose_actual_grade(education_key, age, rng)
            mitarb_gruppe = "Angestellte"
            tarifart = "TVÃ–D"
        elif age <= 32 and rng.random() < 0.03:
            vertragsart = "Zeitvertrag"
            tariff = _choose_actual_grade(education_key, age, rng)
            mitarb_gruppe = "Angestellte"
            tarifart = "TVÃ–D"
        else:
            vertragsart = "Unbefristet"
            tariff = _choose_actual_grade(education_key, age, rng)
            mitarb_gruppe = "Angestellte"
            tarifart = "TVÃ–D"

        if tariff != "TVAÃ–D" and rng.random() < SYNTHETIC_ASSUMPTIONS["missing_actual_grade_share"]:
            tariff = np.nan

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

        status = (
            "Ruhendes BeschÃ¤ftigungsverhÃ¤ltnis"
            if vertragsart != "Altersteilzeit" and rng.random() < 0.035
            else "Aktives BeschÃ¤ftigungsverhÃ¤ltnis"
        )

        blueprints.append(
            {
                "PersNr": pers_nr,
                "Vorname": np.nan,
                "Nachname": np.nan,
                "GebDatum": birth_date,
                "Text Gsch": gender,
                "Eintritt": entry_date,
                "Austritt": exit_date,
                "BsGrd": bs_grd,
                "Vertragsart": vertragsart,
                "MitarbKreisbez.": "BeschÃ¤ftigte TVÃ–D",
                "MitarbGruppenbez.": mitarb_gruppe,
                "Bankspezifisch": "bankspezifisch" if rng.random() > 0.12 else "nicht bankspezifisch",
                "Bezeichnung": "aktiv",
                "Status kundenindividuell": status,
                "Tarifarttext": tarifart,
                "Tarifgebiettext": "Westdeutschland",
                "TrfGr": tariff,
                "St": step,
                "__education_key": education_key,
            }
        )

    return blueprints


# =============================================================================
# HAUPTFUNKTIONEN
# =============================================================================

def generate_mitarbeiter(
    n_employees: int = 1222,
    reference_date: str = "2025-12-31"
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


def generate_atz(mitarbeiter_df: pd.DataFrame, reference_date: str = "2025-12-31") -> pd.DataFrame:
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


def generate_mitarbeiter(
    n_employees: int = 1222,
    reference_date: str = "2025-12-31",
    blueprints: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """Generiert Mitarbeiter.xlsx mit fachlich konsistenten Mitarbeiter-Profilen."""
    employee_blueprints = blueprints or _build_employee_blueprints(
        n_employees=n_employees,
        reference_date=reference_date,
        seed=SYNTHETIC_SEEDS["employees"],
    )
    mitarbeiter = pd.DataFrame(employee_blueprints).copy()
    return mitarbeiter.drop(columns=["__education_key"], errors="ignore")


def _build_regular_position_record(
    blueprint: Dict[str, Any],
    org: Dict[str, Any],
    planstellennr: int,
    rng: np.random.Generator,
) -> Dict[str, Any]:
    actual_grade = blueprint.get("TrfGr")
    soll_h, soll_i, _ = _build_soll_profile(actual_grade if pd.notna(actual_grade) else None, rng)
    role_family = (
        "front"
        if org["kuerzel"] in REGULAR_ORG_GROUPS["front"]
        else "specialist"
        if org["kuerzel"] in REGULAR_ORG_GROUPS["specialist"]
        else "hq"
    )
    return {
        "KÃ¼rzel OrgEinheit": org["kuerzel"],
        "OrgEinheitNr": float(org["nr"]),
        "Organisationseinheit": org["name"],
        "Planstellennr": float(planstellennr),
        "PlanstellenkÃ¼rzel": f"REG{planstellennr % 10000:04d}",
        "Planstelle": _choose_role_title(role_family, actual_grade if pd.notna(actual_grade) else "E8", rng),
        "Sollarbeitszeit": float(_pick_from_weighted_dict(SYNTHETIC_ASSUMPTIONS["regular_position_hours"], rng)),
        "Bewertung Tarifgruppe": soll_h,
        "Text Gehaltsband": soll_i,
        "Personalnummer": float(blueprint["PersNr"]),
    }


def _build_technical_position_record(
    blueprint: Dict[str, Any],
    org: Dict[str, Any],
    planstellennr: int,
    rng: np.random.Generator,
    occupied: bool = True,
) -> Dict[str, Any]:
    return {
        "KÃ¼rzel OrgEinheit": org["kuerzel"],
        "OrgEinheitNr": float(org["nr"]),
        "Organisationseinheit": org["name"],
        "Planstellennr": float(planstellennr),
        "PlanstellenkÃ¼rzel": f"TEC{planstellennr % 10000:04d}",
        "Planstelle": str(_pick_from_weighted_dict({title: 1.0 for title in ROLE_TITLES["technical"]}, rng)),
        "Sollarbeitszeit": float(_pick_from_weighted_dict(SYNTHETIC_ASSUMPTIONS["technical_position_hours"], rng)),
        "Bewertung Tarifgruppe": "",
        "Text Gehaltsband": np.nan,
        "Personalnummer": float(blueprint["PersNr"]) if occupied else np.nan,
    }


def _build_vacancy_record(planstellennr: int, rng: np.random.Generator) -> Dict[str, Any]:
    vacancy_type = str(_pick_from_weighted_dict(SYNTHETIC_ASSUMPTIONS["vacancy_type_mix"], rng))
    if vacancy_type == "technical":
        tech_org_key = str(_pick_from_weighted_dict({"900": 0.4, "110": 0.2, "300": 0.2, "9910": 0.2}, rng))
        org = next(org for org in ORG_UNITS if org["kuerzel"] == tech_org_key)
        return {
            "KÃ¼rzel OrgEinheit": org["kuerzel"],
            "OrgEinheitNr": float(org["nr"]),
            "Organisationseinheit": org["name"],
            "Planstellennr": float(planstellennr),
            "PlanstellenkÃ¼rzel": f"TVK{planstellennr % 10000:04d}",
            "Planstelle": str(_pick_from_weighted_dict({title: 1.0 for title in ROLE_TITLES["technical"]}, rng)),
            "Sollarbeitszeit": float(_pick_from_weighted_dict(SYNTHETIC_ASSUMPTIONS["technical_position_hours"], rng)),
            "Bewertung Tarifgruppe": "",
            "Text Gehaltsband": np.nan,
            "Personalnummer": np.nan,
        }

    grade = str(_pick_from_weighted_dict(NON_AZUBI_TARIFF_DISTRIBUTION, rng))
    org = _choose_regular_org(grade, rng)
    role_family = (
        "front"
        if org["kuerzel"] in REGULAR_ORG_GROUPS["front"]
        else "specialist"
        if org["kuerzel"] in REGULAR_ORG_GROUPS["specialist"]
        else "hq"
    )
    use_band = rng.random() < 0.18
    return {
        "KÃ¼rzel OrgEinheit": org["kuerzel"],
        "OrgEinheitNr": float(org["nr"]),
        "Organisationseinheit": org["name"],
        "Planstellennr": float(planstellennr),
        "PlanstellenkÃ¼rzel": f"VAK{planstellennr % 10000:04d}",
        "Planstelle": str(_pick_from_weighted_dict({title: 1.0 for title in ROLE_TITLES["vacancy"]}, rng)),
        "Sollarbeitszeit": float(_pick_from_weighted_dict(SYNTHETIC_ASSUMPTIONS["regular_position_hours"], rng)),
        "Bewertung Tarifgruppe": grade,
        "Text Gehaltsband": f"bis {_grade_shift(grade, 1)}" if use_band and grade != GRADE_ORDER[-1] else np.nan,
        "Personalnummer": np.nan,
    }


def generate_planstellen(
    mitarbeiter_df: pd.DataFrame,
    n_planstellen: int = 1728,
    vacancy_rate: float = 0.28,
    blueprints: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """
    Generiert Planstellen.XLSX mit realistischen Soll-Ist-Abweichungen,
    Zusatzstellen und technischen Low-AZ-FÃ¤llen.
    """
    rng = np.random.default_rng(SYNTHETIC_SEEDS["positions"])
    records: List[Dict[str, Any]] = []
    planstellen_counter = 50000001

    employee_blueprints = blueprints or pd.DataFrame(mitarbeiter_df).to_dict("records")
    azubis = [bp for bp in employee_blueprints if bp["MitarbGruppenbez."] == "Auszubildende"]
    regulars = [bp for bp in employee_blueprints if bp["MitarbGruppenbez."] != "Auszubildende"]

    regular_ids = [bp["PersNr"] for bp in regulars]
    technical_only_count = max(12, int(len(regular_ids) * SYNTHETIC_ASSUMPTIONS["technical_only_employee_share"]))
    technical_only_ids = set(rng.choice(regular_ids, size=technical_only_count, replace=False).tolist())

    remaining_regular_ids = [pid for pid in regular_ids if pid not in technical_only_ids]
    extra_technical_count = max(25, int(len(remaining_regular_ids) * SYNTHETIC_ASSUMPTIONS["extra_technical_position_share"]))
    extra_technical_ids = set(rng.choice(remaining_regular_ids, size=extra_technical_count, replace=False).tolist())

    azubi_org = ORG_BY_KEY["9910"]
    for azubi in azubis:
        records.append(
            {
                "KÃ¼rzel OrgEinheit": azubi_org["kuerzel"],
                "OrgEinheitNr": float(azubi_org["nr"]),
                "Organisationseinheit": azubi_org["name"],
                "Planstellennr": float(planstellen_counter),
                "PlanstellenkÃ¼rzel": f"AZU{planstellen_counter % 1000:03d}",
                "Planstelle": "Auszubildende/r",
                "Sollarbeitszeit": 0.01,
                "Bewertung Tarifgruppe": "TVAÃ–D",
                "Text Gehaltsband": np.nan,
                "Personalnummer": float(azubi["PersNr"]),
            }
        )
        planstellen_counter += 1

    for blueprint in regulars:
        actual_grade = blueprint["TrfGr"] if pd.notna(blueprint["TrfGr"]) else "E8"
        regular_org = _choose_regular_org(actual_grade, rng)
        has_regular_position = blueprint["PersNr"] not in technical_only_ids

        if has_regular_position:
            records.append(_build_regular_position_record(blueprint, regular_org, planstellennr=planstellen_counter, rng=rng))
            planstellen_counter += 1

        if blueprint["PersNr"] in technical_only_ids or blueprint["PersNr"] in extra_technical_ids:
            technical_org = regular_org
            if not has_regular_position:
                technical_key = str(
                    _pick_from_weighted_dict(
                        {"900": 0.35, "110": 0.15, "300": 0.20, regular_org["kuerzel"]: 0.30},
                        rng,
                    )
                )
                technical_org = ORG_BY_KEY.get(technical_key, regular_org)
            records.append(
                _build_technical_position_record(
                    blueprint,
                    technical_org,
                    planstellennr=planstellen_counter,
                    rng=rng,
                    occupied=True,
                )
            )
            planstellen_counter += 1

    while len(records) < n_planstellen:
        records.append(_build_vacancy_record(planstellen_counter, rng))
        planstellen_counter += 1

    return pd.DataFrame(records[:n_planstellen])


def generate_ausbildung(
    mitarbeiter_df: pd.DataFrame,
    blueprints: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """Generiert Ausbildung.xlsx konsistent zur Mitarbeiter-Synthese."""
    employee_blueprints = blueprints or _build_employee_blueprints(
        n_employees=len(mitarbeiter_df),
        seed=SYNTHETIC_SEEDS["employees"],
    )
    data = []
    for blueprint in employee_blueprints:
        edu_key = int(blueprint["__education_key"])
        edu_text, edu_bv, _ = EDUCATION_GROUPS[edu_key]
        data.append(
            {
                "Personalnummer": blueprint["PersNr"],
                "Ausbildungsgruppe": edu_key,
                "BV Ausbildungsgruppentext": edu_text,
                "Betriebsvergleich Ausbildung": edu_bv,
            }
        )
    return pd.DataFrame(data)


def generate_history_cube(
    snapshot_df: pd.DataFrame,
    start_date: str = "2024-01-01",
    end_date: str = "2025-12-31"
) -> pd.DataFrame:
    """Generiert History_Cube (monatliche Zeitreihen)."""
    rng = np.random.default_rng(SYNTHETIC_SEEDS["history"])
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
            noise = float(rng.normal(1.0, 0.02))

            data.append({
                "Kürzel OrgEinheit": org_unit,
                "Date": date,
                "Headcount": max(0, int(base_headcount * trend_factor * noise)),
                "FTE": base_fte * trend_factor * noise,
                "Total_Cost": base_cost * trend_factor * noise,
                "Vacancy_Count": max(0, int(base_vacancy * noise)),
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
    end_date: str = "2025-12-31"
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


def generate_all_files(
    n_employees: int = 1222,
    n_planstellen: int = 1728,
    output_dir: Optional[str] = None
) -> Dict[str, pd.DataFrame]:
    """Generiert alle 4 Original-Dateien mit business-nahen Soll-Ist-Fällen."""
    print("Generiere Mitarbeiter...")
    blueprints = _build_employee_blueprints(n_employees)
    mitarbeiter = generate_mitarbeiter(n_employees, blueprints=blueprints)

    print("Generiere Planstellen...")
    planstellen = generate_planstellen(mitarbeiter, n_planstellen, blueprints=blueprints)

    print("Generiere ATZ...")
    atz = generate_atz(mitarbeiter)

    print("Generiere Ausbildung...")
    ausbildung = generate_ausbildung(mitarbeiter, blueprints=blueprints)

    files = {
        "Mitarbeiter": _normalize_generated_frame(mitarbeiter),
        "Planstellen": _normalize_generated_frame(planstellen),
        "ATZ": _normalize_generated_frame(atz),
        "Ausbildung": _normalize_generated_frame(ausbildung),
    }

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        for name, df in files.items():
            filepath = os.path.join(output_dir, f"{name}.xlsx")
            df.to_excel(filepath, index=False)
            print(f"  Gespeichert: {filepath}")

        marker_path = os.path.join(output_dir, ".is_synthetic")
        with open(marker_path, "w") as f:
            f.write("This directory contains synthetic data.")

    return files


def compute_synthetic_soll_ist_diagnostics(files: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """Erzeugt fachliche Diagnostik für die Soll-Ist-Köpfe-Synthese."""
    mitarbeiter = files["Mitarbeiter"].copy()
    planstellen = files["Planstellen"].copy()

    mitarbeiter["PersNr"] = normalize_persnr(mitarbeiter["PersNr"])
    planstellen["Personalnummer"] = normalize_persnr(planstellen["Personalnummer"])

    merged = planstellen.merge(
        mitarbeiter[["PersNr", "TrfGr", "MitarbGruppenbez.", "Status kundenindividuell"]],
        left_on="Personalnummer",
        right_on="PersNr",
        how="left",
    )

    merged["__has_pnr__"] = merged["Personalnummer"].notna()
    merged["__is_001__"] = pd.to_numeric(merged["Sollarbeitszeit"], errors="coerce").eq(0.01)
    merged["__is_regular__"] = ~merged["__is_001__"]
    merged["__is_9xxx__"] = merged["Kürzel OrgEinheit"].astype(str).str.startswith("99")

    def _norm_eg(value: Any) -> str:
        if pd.isna(value):
            return ""
        text = str(value).strip().upper().replace(" ", "")
        if text.startswith("BIS"):
            text = text[3:].strip()
        return text

    merged["_Soll_EG_H"] = merged["Bewertung Tarifgruppe"].map(_norm_eg)
    merged["_Soll_EG_I"] = merged["Text Gehaltsband"].map(_norm_eg)
    merged["_Soll_EG_I"] = merged["_Soll_EG_I"].where(merged["_Soll_EG_I"].ne(""), merged["_Soll_EG_H"])
    merged["_Ist_EG"] = merged["TrfGr"].map(_norm_eg)
    merged.loc[~merged["__has_pnr__"], "_Ist_EG"] = "Unbesetzt"
    merged.loc[merged["__has_pnr__"] & merged["_Ist_EG"].eq(""), "_Ist_EG"] = "Nicht gefunden"

    regular_df = merged.loc[merged["__is_regular__"]].copy()
    technical_df = merged.loc[merged["__is_001__"]].copy()
    no_soll_df = regular_df.loc[regular_df["_Soll_EG_H"].eq("") & regular_df["_Soll_EG_I"].eq("")].copy()
    matrix_df = regular_df.loc[~(regular_df["_Soll_EG_H"].eq("") & regular_df["_Soll_EG_I"].eq(""))].copy()
    occupied_matrix = matrix_df.loc[~matrix_df["_Ist_EG"].isin(["Unbesetzt", "Nicht gefunden"])].copy()

    grade_rank = {grade: idx for idx, grade in enumerate(GRADE_ORDER)}
    rank_h = occupied_matrix["_Soll_EG_H"].map(lambda grade: grade_rank.get(grade, 999))
    rank_i = occupied_matrix["_Soll_EG_I"].map(lambda grade: grade_rank.get(grade, 999))
    rank_ist = occupied_matrix["_Ist_EG"].map(lambda grade: grade_rank.get(grade, 999))

    exact_mask = rank_ist.eq(rank_i)
    in_band_mask = ~exact_mask & rank_ist.between(rank_h, rank_i, inclusive="both")
    over_mask = rank_ist.gt(rank_i)
    under_mask = rank_ist.lt(rank_h)

    regular_people = set(regular_df.loc[regular_df["__has_pnr__"], "Personalnummer"].dropna())
    technical_people = set(technical_df.loc[technical_df["__has_pnr__"], "Personalnummer"].dropna())

    diagnostics = {
        "total_positions": int(len(merged)),
        "regular_positions": int(len(regular_df)),
        "regular_occupied": int(regular_df["__has_pnr__"].sum()),
        "regular_vacant": int((~regular_df["__has_pnr__"]).sum()),
        "regular_no_soll": int(len(no_soll_df)),
        "matrix_positions": int(len(matrix_df)),
        "matrix_occupied": int(len(occupied_matrix)),
        "matrix_vacant": int((matrix_df["_Ist_EG"] == "Unbesetzt").sum()),
        "not_found_actual_grade": int((matrix_df["_Ist_EG"] == "Nicht gefunden").sum()),
        "technical_positions_001": int(len(technical_df)),
        "technical_positions_9xxx": int((technical_df["__is_9xxx__"]).sum()),
        "technical_positions_non9xxx": int((~technical_df["__is_9xxx__"]).sum()),
        "technical_occupied": int(technical_df["__has_pnr__"].sum()),
        "technical_only_people": int(len(technical_people - regular_people)),
        "technical_plus_regular_people": int(len(technical_people & regular_people)),
        "exact_match": int(exact_mask.sum()),
        "passend_im_band": int(in_band_mask.sum()),
        "overgraded": int(over_mask.sum()),
        "undergraded": int(under_mask.sum()),
        "soll_grade_distribution": matrix_df["_Soll_EG_I"].value_counts().to_dict(),
        "ist_grade_distribution": occupied_matrix["_Ist_EG"].value_counts().to_dict(),
    }
    diagnostics["exact_match_share"] = diagnostics["exact_match"] / diagnostics["matrix_occupied"] if diagnostics["matrix_occupied"] else 0.0
    diagnostics["in_band_share"] = diagnostics["passend_im_band"] / diagnostics["matrix_occupied"] if diagnostics["matrix_occupied"] else 0.0
    diagnostics["overgraded_share"] = diagnostics["overgraded"] / diagnostics["matrix_occupied"] if diagnostics["matrix_occupied"] else 0.0
    diagnostics["undergraded_share"] = diagnostics["undergraded"] / diagnostics["matrix_occupied"] if diagnostics["matrix_occupied"] else 0.0
    diagnostics["regular_vacancy_share"] = diagnostics["regular_vacant"] / diagnostics["regular_positions"] if diagnostics["regular_positions"] else 0.0
    return diagnostics


def validate_synthetic_files(files: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """Validiert Schema, Relationen und fachliche Nicht-Trivialität."""
    issues: List[str] = []
    required = {
        "Mitarbeiter": {"PersNr", "Vertragsart", "TrfGr", "Status kundenindividuell", "BsGrd"},
        "Planstellen": {"Kürzel OrgEinheit", "Sollarbeitszeit", "Bewertung Tarifgruppe", "Personalnummer"},
        "ATZ": {"PersNr", "Beginn", "Ende", "Ende ATZ Vertrag", "Phase"},
        "Ausbildung": {"Personalnummer", "Ausbildungsgruppe", "BV Ausbildungsgruppentext"},
    }
    for file_name, required_columns in required.items():
        missing = required_columns - set(files[file_name].columns)
        if missing:
            issues.append(f"{file_name}: fehlende Spalten {sorted(missing)}")

    employee_ids = set(normalize_persnr(files["Mitarbeiter"]["PersNr"]).dropna())
    occupied_plan_ids = set(normalize_persnr(files["Planstellen"]["Personalnummer"]).dropna())
    missing_relations = occupied_plan_ids - employee_ids
    if missing_relations:
        issues.append(f"Planstellen ohne Mitarbeiterreferenz: {len(missing_relations)}")

    azubi_ids = set(
        normalize_persnr(
            files["Mitarbeiter"].loc[files["Mitarbeiter"]["MitarbGruppenbez."] == "Auszubildende", "PersNr"]
        ).dropna()
    )
    ausbildung_ids = set(normalize_persnr(files["Ausbildung"]["Personalnummer"]).dropna())
    if azubi_ids - ausbildung_ids:
        issues.append("Azubi-Ausbildungsdaten unvollständig")

    diagnostics = compute_synthetic_soll_ist_diagnostics(files)
    if diagnostics["matrix_occupied"] <= 0:
        issues.append("Soll-Ist-Matrix ist leer")
    if diagnostics["overgraded"] <= 0:
        issues.append("Keine Übergruppierung erzeugt")
    if diagnostics["undergraded"] <= 0:
        issues.append("Keine Untergruppierung erzeugt")
    if diagnostics["technical_only_people"] <= 0:
        issues.append("Keine Personen nur auf technischen Zusatzstellen")
    if diagnostics["technical_plus_regular_people"] <= 0:
        issues.append("Keine Personen mit technischer Zusatzstelle neben regulärer Stelle")

    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "diagnostics": diagnostics,
    }


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
