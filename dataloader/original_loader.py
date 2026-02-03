"""
Original-Daten Loader für HR Pulse Dashboard.

Lädt die 4 Original-Excel-Dateien und konvertiert sie ins Dashboard-Format.

VERSION 3.0 - TVÖD-INTEGRATION:
- ATZ-Merge ohne Duplikate (stichtagsbezogen)
- Azubi-Sollarbeitszeit wird korrigiert (0.01 → 39)
- Ruhend vs. ATZ-FR korrekt unterschieden
- Summenzeile in Planstellen wird entfernt
- TVÖD.xlsx wird geladen und für exakte Kostenberechnung genutzt
- Sonderfälle (Azubi, Vorstand) über Einstellungen-Seite konfigurierbar
"""

import pandas as pd
import numpy as np
import streamlit as st
from typing import Dict, Tuple, Optional
import sys
import os
from datetime import datetime

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DEFAULT_COHORTS, BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR

# =============================================================================
# STICHTAG (verbindlich, kein Systemdatum!)
# =============================================================================

STICHTAG = pd.Timestamp("2025-01-30")

# =============================================================================
# PFADE ZU ORIGINAL-DATEN
# =============================================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DATA_DIR = os.path.join(BASE_DIR, "..", "Original-Daten")

ORIGINAL_FILES = {
    "mitarbeiter": os.path.join(ORIGINAL_DATA_DIR, "Mitarbeiter.xlsx"),
    "planstellen": os.path.join(ORIGINAL_DATA_DIR, "Planstellen.XLSX"),
    "atz": os.path.join(ORIGINAL_DATA_DIR, "ATZ.xlsx"),
    "ausbildung": os.path.join(ORIGINAL_DATA_DIR, "Ausbildung.xlsx"),
}

TVOED_FILE = os.path.join(ORIGINAL_DATA_DIR, "TVÖD.xlsx")


# =============================================================================
# HILFSFUNKTIONEN
# =============================================================================

ID_PAD_LENGTH = 6

EDUCATION_MAPPING = {
    "Bachelor FH": "Bachelor",
    "Bachelor Universität": "Bachelor",
    "Master FH": "Master",
    "Master Universität": "Master",
    "Studium Lehrinstitut": "Sonstiges Studium",
    "SPK/Bankbetriebswirt": "Bankspezifische Weiterbildung",
    "Sparkassen/Bankfachwirt": "Bankspezifische Weiterbildung",
    "Bankberufsabschluss": "Berufsausbildung",
    "kfm Berufsabschluss": "Berufsausbildung",
    "nicht kfm Berufsabschluss": "Berufsausbildung",
    "derzeit Berufsausbildung": "In Ausbildung",
    "ohne Berufsabschluss": "Ohne Abschluss",
}

EDUCATION_RANKING = {
    "Master Universität": 6,
    "Master FH": 6,
    "Bachelor Universität": 5,
    "Bachelor FH": 5,
    "Studium Lehrinstitut": 5,
    "SPK/Bankbetriebswirt": 4,
    "Sparkassen/Bankfachwirt": 4,
    "Bankberufsabschluss": 3,
    "kfm Berufsabschluss": 3,
    "nicht kfm Berufsabschluss": 3,
    "derzeit Berufsausbildung": 2,
    "ohne Berufsabschluss": 1,
}


def normalize_persnr(series: pd.Series) -> pd.Series:
    """Normalisiert Personalnummern zu String mit führenden Nullen."""
    return series.apply(
        lambda x: str(int(x)).zfill(ID_PAD_LENGTH) if pd.notna(x) else pd.NA
    )


def normalize_atz(atz_df: pd.DataFrame) -> pd.DataFrame:
    """Bereinigt ATZ-Daten und standardisiert PersNr und Datumsfelder."""
    df = atz_df.copy()
    df["PersNr"] = normalize_persnr(df["PersNr"])
    df["Beginn"] = pd.to_datetime(df["Beginn"], errors="coerce")
    df["Ende"] = pd.to_datetime(df["Ende"], errors="coerce")
    df["Ende ATZ Vertrag"] = pd.to_datetime(df["Ende ATZ Vertrag"], errors="coerce")
    return df


def safe_parse_austritt(series: pd.Series) -> pd.Series:
    """Parst Austritt robust (9999-12-31 ist außerhalb pandas datetime64[ns])."""
    def _to_na_if_out_of_bounds(val):
        if pd.isna(val):
            return pd.NaT

        # Excel kann hier python datetime/date liefern
        try:
            year = getattr(val, "year", None)
            if year is not None and (year == 9999 or year > 2262):
                return pd.NaT
        except Exception:
            pass

        # String-Fallback
        s = str(val).strip()
        if s.startswith("9999-") or s.startswith("9999/"):
            return pd.NaT

        return val

    cleaned = series.map(_to_na_if_out_of_bounds)
    return pd.to_datetime(cleaned, errors="coerce")


def derive_atz_fields(atz_df: pd.DataFrame) -> pd.DataFrame:
    """
    Erzeugt ATZ-Ableitungen pro Person gemäß Readme.

    Returns:
        DataFrame mit PersNr und abgeleiteten ATZ-Feldern.
    """
    if atz_df.empty:
        return pd.DataFrame(
            columns=[
                "PersNr",
                "atz_start_date",
                "atz_rest_start_date",
                "atz_end_date",
                "atz_duration_ar_months",
                "atz_duration_fr_months",
                "atz_has_two_phases",
                "atz_phase_gap_ok",
                "atz_phase_end_matches_contract",
            ]
        )

    df = atz_df.copy()
    df["Phase"] = df["Phase"].astype(str).str.strip()

    phase_counts = df.groupby("PersNr")["Phase"].nunique().rename("atz_phase_count")
    phase_counts = phase_counts.to_frame()
    phase_counts["atz_has_two_phases"] = phase_counts["atz_phase_count"] == 2

    pivot = df.pivot_table(
        index="PersNr",
        columns="Phase",
        values=["Beginn", "Ende", "Ende ATZ Vertrag"],
        aggfunc="min",
    )
    pivot.columns = [f"{col[0]}_{col[1]}" for col in pivot.columns]

    result = pivot.join(phase_counts, how="left").reset_index()

    result["atz_start_date"] = result.get("Beginn_AR", pd.NaT)
    result["atz_rest_start_date"] = result.get("Beginn_FR", pd.NaT)
    result["atz_end_date"] = pd.Series(
        result.get("Ende ATZ Vertrag_FR", pd.NaT)
    ).combine_first(pd.Series(result.get("Ende ATZ Vertrag_AR", pd.NaT)))

    ar_end = pd.Series(result.get("Ende_AR", pd.NaT))
    fr_begin = pd.Series(result.get("Beginn_FR", pd.NaT))
    fr_end = pd.Series(result.get("Ende_FR", pd.NaT))
    contract_end = pd.Series(result["atz_end_date"])

    result["atz_duration_ar_months"] = (
        (ar_end - pd.to_datetime(result["atz_start_date"], errors="coerce")).dt.days / 30.44
    )
    result["atz_duration_fr_months"] = (
        (pd.to_datetime(fr_end, errors="coerce") - pd.to_datetime(fr_begin, errors="coerce")).dt.days / 30.44
    )

    result["atz_phase_gap_ok"] = (ar_end.notna() & fr_begin.notna()) & (
        (pd.to_datetime(fr_begin, errors="coerce") - pd.to_datetime(ar_end, errors="coerce")).dt.days == 1
    )
    result["atz_phase_end_matches_contract"] = (
        fr_end.notna() & contract_end.notna() & (fr_end == contract_end)
    )

    return result[
        [
            "PersNr",
            "atz_start_date",
            "atz_rest_start_date",
            "atz_end_date",
            "atz_duration_ar_months",
            "atz_duration_fr_months",
            "atz_has_two_phases",
            "atz_phase_gap_ok",
            "atz_phase_end_matches_contract",
        ]
    ]

def clean_step(step) -> int:
    """
    Bereinigt Stufen-Werte wie '2+' zu 2.
    
    Args:
        step: Stufen-Wert (kann int, str oder NaN sein)
    
    Returns:
        Bereinigte Stufe als int
    """
    if pd.isna(step):
        return 4  # Default
    step_str = str(step).strip().replace("+", "").replace("-", "")
    try:
        return int(step_str)
    except (ValueError, TypeError):
        return 4  # Fallback


def clean_planstellen(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bereinigt Planstellen-Daten.
    
    KORREKTUREN:
    - Entfernt Summenzeile (letzte Zeile mit NaN)
    - Korrigiert Azubi-Sollarbeitszeit (0.01 → 39)
    
    Args:
        df: Planstellen DataFrame
    
    Returns:
        Bereinigter DataFrame
    """
    df = df.copy()
    
    # Summenzeile entfernen (Zeilen ohne Kürzel OrgEinheit)
    df = df[df['Kürzel OrgEinheit'].notna()]
    
    # KORREKTUR: Azubi-Sollarbeitszeit korrigieren
    # NUR wo Kürzel OrgEinheit == '9910' UND Sollarbeitszeit == 0.01
    # (Nicht pauschal alle 9910-Zeilen überschreiben!)
    azubi_mask = (df['Kürzel OrgEinheit'] == '9910') & (df['Sollarbeitszeit'] == 0.01)
    df.loc[azubi_mask, 'Sollarbeitszeit'] = 39.0

    # Personalnummer standardisieren
    if "Personalnummer" in df.columns:
        df["Personalnummer"] = normalize_persnr(df["Personalnummer"])
    
    return df


def get_current_atz_phase(atz_df: pd.DataFrame, stichtag: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """
    Filtert ATZ-Daten auf die aktuelle Phase zum Stichtag.
    
    KORREKTUR: Vermeidet Duplikate durch Merge, indem nur die
    aktuelle Phase (AR oder FR) pro Person zurückgegeben wird.
    
    ATZ.xlsx hat 2 Zeilen pro Person (AR + FR Phase).
    Diese Funktion gibt nur die zum Stichtag gültige Phase zurück.
    
    Args:
        atz_df: ATZ DataFrame mit allen Phasen
        stichtag: Referenzdatum (default: heute)
    
    Returns:
        DataFrame mit einer Zeile pro Person (aktuelle Phase)
    """
    if stichtag is None:
        stichtag = STICHTAG

    # Filtere auf aktuelle Phase (Beginn <= Stichtag <= Ende)
    atz_aktuell = atz_df[
        (atz_df['Beginn'] <= stichtag) & 
        (atz_df['Ende'] >= stichtag)
    ].copy()

    if atz_aktuell.empty:
        return atz_aktuell
    
    # Sicherheitscheck: Sollte nur eine Zeile pro Person geben
    # Falls doch Duplikate: Nehme FR vor AR (FR hat höhere Priorität)
    if atz_aktuell['PersNr'].duplicated().any():
        atz_aktuell = atz_aktuell.sort_values('Phase', ascending=False)
        atz_aktuell = atz_aktuell.drop_duplicates(subset='PersNr', keep='first')
    
    return atz_aktuell


# =============================================================================
# LOADER-FUNKTIONEN
# =============================================================================

@st.cache_data
def load_original_data() -> Dict[str, pd.DataFrame]:
    """
    Lädt die 4 Original-Excel-Dateien.

    Returns:
        Dictionary mit DataFrames:
        - mitarbeiter
        - planstellen
        - atz
        - ausbildung

    Raises:
        FileNotFoundError: Wenn Dateien nicht gefunden werden
    """
    data = {}

    # Prüfe ob alle Dateien existieren
    missing_files = []
    for key, filepath in ORIGINAL_FILES.items():
        if not os.path.exists(filepath):
            missing_files.append(filepath)

    if missing_files:
        raise FileNotFoundError(
            f"Original-Daten nicht gefunden:\n" + "\n".join(missing_files)
        )

    # Lade Mitarbeiter
    # NOTE: Austritt=9999-12-31 ist außerhalb pandas datetime64[ns] und darf NICHT via parse_dates geparst werden.
    data["mitarbeiter"] = pd.read_excel(ORIGINAL_FILES["mitarbeiter"])

    # Datumsfelder robust parsen
    if "GebDatum" in data["mitarbeiter"].columns:
        data["mitarbeiter"]["GebDatum"] = pd.to_datetime(
            data["mitarbeiter"]["GebDatum"], errors="coerce"
        )
    if "Eintritt" in data["mitarbeiter"].columns:
        data["mitarbeiter"]["Eintritt"] = pd.to_datetime(
            data["mitarbeiter"]["Eintritt"], errors="coerce"
        )
    if "Austritt" in data["mitarbeiter"].columns:
        data["mitarbeiter"]["Austritt"] = safe_parse_austritt(
            data["mitarbeiter"]["Austritt"]
        )

    data["mitarbeiter"]["PersNr"] = normalize_persnr(data["mitarbeiter"]["PersNr"])

    # Lade Planstellen
    data["planstellen"] = pd.read_excel(
        ORIGINAL_FILES["planstellen"]
    )

    # Lade ATZ
    data["atz"] = pd.read_excel(
        ORIGINAL_FILES["atz"],
        parse_dates=["Beginn", "Ende", "Ende ATZ Vertrag"]
    )
    data["atz"] = normalize_atz(data["atz"])

    # Lade Ausbildung
    data["ausbildung"] = pd.read_excel(
        ORIGINAL_FILES["ausbildung"]
    )
    data["ausbildung"]["Personalnummer"] = normalize_persnr(
        data["ausbildung"]["Personalnummer"]
    )
    data["ausbildung"]["BV Ausbildungsgruppentext"] = (
        data["ausbildung"]["BV Ausbildungsgruppentext"].astype(str).str.strip()
    )

    return data


def calculate_cost_row(row, tvoed_lookup=None) -> float:
    """
    Berechnet Jahreskosten für eine Zeile.

    Verwendet TVÖD-Tabelle wenn verfügbar, sonst Fallback auf BASE_SALARY.
    Sonderfälle (Azubi, Vorstand) werden über Einstellungen-Seite konfiguriert.

    Args:
        row: DataFrame Row
        tvoed_lookup: Optional TVÖD-Lookup-Dict {(Gruppe, Stufe): Jahresgehalt}

    Returns:
        Jahreskosten in Euro
    """
    from dataloader.tvoed_loader import get_annual_salary, get_special_salary

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

    # Tarifgruppe normalisieren (Leerzeichen entfernen, Großschreibung)
    tariff = str(tariff).strip().upper().replace(" ", "")

    # Stufe bereinigen
    step_int = clean_step(step)

    # Sonderfälle: Azubis (TVAÖD) und Vorstand ("1")
    special = get_special_salary(tariff)
    if special is not None:
        employer_factor = st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR)
        return special * fte * employer_factor

    # Reguläre Tarifgruppen: TVÖD-Lookup mit Fallback
    employer_factor = st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR)
    annual = get_annual_salary(
        tvoed_lookup or {}, tariff, step_int,
        BASE_SALARY, STEP_MULTIPLIER
    )

    return annual * fte * employer_factor


def combine_to_snapshot(
    mitarbeiter: pd.DataFrame,
    planstellen: pd.DataFrame,
    atz: pd.DataFrame,
    ausbildung: pd.DataFrame,
    stichtag: Optional[pd.Timestamp] = None,
    tvoed_lookup: Optional[Dict] = None,
) -> pd.DataFrame:
    """
    Kombiniert die 4 Original-Dateien zu einem Snapshot DataFrame.

    KORREKTUREN (v2.0):
    1. Azubi-Sollarbeitszeit wird korrigiert (0.01 → 39)
    2. ATZ-Merge ohne Duplikate (nur aktuelle Phase zum Stichtag)
    3. Summenzeile in Planstellen wird entfernt
    4. ist_atz_fr Flag für MAK-Berechnung
    
    Args:
        mitarbeiter: Mitarbeiter DataFrame
        planstellen: Planstellen DataFrame
        atz: ATZ DataFrame
        ausbildung: Ausbildung DataFrame
        stichtag: Referenzdatum für ATZ-Phase (default: heute)

    Returns:
        Kombinierter Snapshot DataFrame
    """
    if stichtag is None:
        stichtag = STICHTAG

    # Standardisierung: Personalnummern, Datumsfelder, Austritt 9999-12-31
    mitarbeiter = mitarbeiter.copy()
    mitarbeiter["PersNr"] = normalize_persnr(mitarbeiter["PersNr"])
    if "Austritt" in mitarbeiter.columns:
        mitarbeiter["Austritt"] = pd.to_datetime(mitarbeiter["Austritt"], errors="coerce")
        austritt_year = pd.DatetimeIndex(mitarbeiter["Austritt"]).year
        mitarbeiter.loc[austritt_year == 9999, "Austritt"] = pd.NaT

    ausbildung = ausbildung.copy()
    ausbildung["Personalnummer"] = normalize_persnr(ausbildung["Personalnummer"])
    ausbildung["BV Ausbildungsgruppentext"] = (
        ausbildung["BV Ausbildungsgruppentext"].astype(str).str.strip()
    )

    atz = normalize_atz(atz)

    # KORREKTUR 1: Planstellen bereinigen (Summenzeile + Azubi-Sollarbeitszeit)
    df = clean_planstellen(planstellen)

    # Merge Mitarbeiter-Daten
    df = df.merge(
        mitarbeiter,
        left_on="Personalnummer",
        right_on="PersNr",
        how="left",
        suffixes=("", "_ma")
    )

    # Merge Ausbildung
    df = df.merge(
        ausbildung[["Personalnummer", "BV Ausbildungsgruppentext"]],
        left_on="Personalnummer",
        right_on="Personalnummer",
        how="left",
        suffixes=("", "_ausb")
    )
    df = df.rename(columns={"BV Ausbildungsgruppentext": "Ausbildung"})

    # Ausbildung-Mapping + Rang
    if "Ausbildung" in df.columns:
        df["Ausbildung"] = df["Ausbildung"].astype("string")
    df["Bildungskategorie"] = df["Ausbildung"].map(EDUCATION_MAPPING)
    df["Bildungsrang"] = df["Ausbildung"].map(EDUCATION_RANKING)

    # Azubi-Flag über Mitarbeitergruppe (führend)
    if "MitarbGruppenbez." in df.columns:
        df["Ist_Azubi"] = df["MitarbGruppenbez."] == "Auszubildende"

    # KORREKTUR 2: ATZ-Merge ohne Duplikate (nur aktuelle Phase)
    atz_aktuell = get_current_atz_phase(atz, stichtag)
    
    df = df.merge(
        atz_aktuell[["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag", "Modell"]],
        left_on="PersNr",
        right_on="PersNr",
        how="left",
        suffixes=("", "_atz")
    )
    if "Phase" in df.columns:
        df["Phase"] = df["Phase"].astype(str).str.strip()

    # Berechne abgeleitete Felder

    # Vakanz-Status
    df["Is_Vacant"] = df["Personalnummer"].isna()

    # FTE-Felder (Azubi-Sollarbeitszeit bereits in clean_planstellen korrigiert)
    df["FTE_person"] = df["BsGrd"].fillna(0) / 100.0
    # Soll_FTE: 0.01 außerhalb 9910 bleibt unbekannt (NICHT auf 39 defaulten)
    df["Soll_FTE"] = df["Sollarbeitszeit"].fillna(0) / 39.0
    df["FTE_assigned"] = df["FTE_person"] * df["Soll_FTE"]

    # Stufe (St) bereinigen: "2+" → 2, "3+" → 3, etc.
    if "St" in df.columns:
        df["St"] = df["St"].apply(clean_step)

    # ATZ-Ableitungen (Readme)
    atz_derived = derive_atz_fields(atz)
    df = df.merge(atz_derived, on="PersNr", how="left")

    # KORREKTUR 3: ATZ-FR Flag für MAK-Berechnung
    # Dieses Flag wird später in loader.py für die MAK-Berechnung verwendet
    atz_fr_persnr = set(atz_aktuell[atz_aktuell['Phase'] == 'FR']['PersNr'])
    df['ist_atz_fr'] = df['PersNr'].isin(atz_fr_persnr)
    
    # Kosten berechnen (mit TVÖD-Lookup wenn verfügbar)
    df["Total_Cost_Year"] = df.apply(
        lambda row: calculate_cost_row(row, tvoed_lookup=tvoed_lookup), axis=1
    )

    return df


def generate_history_from_snapshot(
    snapshot_df: pd.DataFrame,
    start_date: str = "2024-01-01",
    end_date: str = "2026-01-18"
) -> pd.DataFrame:
    """
    Generiert History Cube aus Snapshot (monatliche Zeitreihen).

    Da wir nur einen Snapshot haben, projizieren wir zurück mit kleinen Variationen.

    Args:
        snapshot_df: Snapshot DataFrame
        start_date: Startdatum für History
        end_date: Enddatum für History

    Returns:
        History DataFrame mit monatlichen Datenpunkten
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    dates = pd.date_range(start=start, end=end, freq='MS')

    data = []

    for org_unit in snapshot_df["Kürzel OrgEinheit"].unique():
        org_data = snapshot_df[snapshot_df["Kürzel OrgEinheit"] == org_unit]

        current_headcount = org_data[~org_data["Is_Vacant"]]["PersNr"].nunique()
        current_fte = org_data["FTE_assigned"].sum()
        current_cost = org_data["Total_Cost_Year"].sum()
        current_vacancy = org_data["Is_Vacant"].sum()

        for date in dates:
            months_from_end = (end.year - date.year) * 12 + (end.month - date.month)

            # Je weiter in der Vergangenheit, desto mehr Abweichung (max 5%)
            trend_factor = 1.0 - (months_from_end / len(dates)) * 0.05
            noise = np.random.normal(1.0, 0.01)

            data.append({
                "Kürzel OrgEinheit": org_unit,
                "Date": date,
                "Headcount": max(0, int(current_headcount * trend_factor * noise)),
                "FTE": max(0, current_fte * trend_factor * noise),
                "Total_Cost": max(0, current_cost * trend_factor * noise),
                "Vacancy_Count": max(0, int(current_vacancy * noise)),
            })

    return pd.DataFrame(data)


def create_org_structure(planstellen: pd.DataFrame) -> pd.DataFrame:
    """
    Erstellt Org-Struktur DataFrame aus Planstellen.

    Args:
        planstellen: Planstellen DataFrame

    Returns:
        Org-Struktur DataFrame mit eindeutigen Einheiten
    """
    # Bereinige Planstellen zuerst (entfernt Summenzeile)
    planstellen_clean = clean_planstellen(planstellen)
    
    org_df = planstellen_clean[
        ["Kürzel OrgEinheit", "OrgEinheitNr", "Organisationseinheit"]
    ].drop_duplicates()
    org_df = org_df.sort_values("Kürzel OrgEinheit")

    return org_df


# =============================================================================
# HAUPTFUNKTION
# =============================================================================

def load_original_and_prepare_data(stichtag: Optional[pd.Timestamp] = None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    Lädt Original-Daten und bereitet sie für das Dashboard auf.

    Kompatibel mit der bestehenden load_and_prepare_data() Schnittstelle.

    Args:
        stichtag: Referenzdatum für ATZ-Phase (default: heute)

    Returns:
        Tuple aus (snapshot_df, history_df, org_df, summary)
    """
    if stichtag is None:
        stichtag = STICHTAG

    # Lade Original-Dateien
    original = load_original_data()

    # Lade TVÖD-Entgelttabelle (optional)
    from dataloader.tvoed_loader import load_tvoed_table
    tvoed_lookup = load_tvoed_table(TVOED_FILE)
    st.session_state["tvoed_lookup"] = tvoed_lookup
    st.session_state["tvoed_available"] = len(tvoed_lookup) > 0

    # Kombiniere zu Snapshot (mit TVÖD-Lookup)
    snapshot_df = combine_to_snapshot(
        original["mitarbeiter"],
        original["planstellen"],
        original["atz"],
        original["ausbildung"],
        stichtag=stichtag,
        tvoed_lookup=tvoed_lookup,
    )

    # Importiere enrich-Funktion vom bestehenden Loader
    from dataloader.loader import enrich_snapshot_data, get_data_summary

    # Reichere Snapshot an
    snapshot_df = enrich_snapshot_data(snapshot_df)

    # Füge Jobfamily-Spalte hinzu
    from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions
    try:
        definitions = load_jobfamily_definitions()
        snapshot_df = assign_jobfamilies(snapshot_df, definitions)
    except Exception as e:
        if "Jobfamily" not in snapshot_df.columns:
            snapshot_df["Jobfamily"] = "UNMAPPED"

    # Generiere History
    history_df = generate_history_from_snapshot(snapshot_df)

    # Erstelle Org-Struktur
    org_df = create_org_structure(original["planstellen"])

    # Berechne Summary (Readme-konform über kpi_engine)
    from dataloader.kpi_engine import compute_readme_summary, enrich_summary_with_gender
    summary = compute_readme_summary(snapshot_df)
    summary = enrich_summary_with_gender(summary, snapshot_df)

    return (
        snapshot_df,
        history_df,
        org_df,
        summary
    )


# =============================================================================
# TEST (für direkten Aufruf)
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Teste Original-Daten Loader (KORRIGIERT v2.0)...")
    print("=" * 60)

    try:
        # Prüfe ob Dateien existieren
        print("\n1. Prüfe Dateien...")
        for key, filepath in ORIGINAL_FILES.items():
            exists = "✓" if os.path.exists(filepath) else "✗"
            print(f"   {exists} {key}: {os.path.basename(filepath)}")

        # Lade Daten
        print("\n2. Lade Original-Daten...")
        original = load_original_data()

        print(f"   ✓ Mitarbeiter: {len(original['mitarbeiter'])} Zeilen")
        print(f"   ✓ Planstellen: {len(original['planstellen'])} Zeilen")
        print(f"   ✓ ATZ: {len(original['atz'])} Zeilen ({original['atz']['PersNr'].nunique()} Personen)")
        print(f"   ✓ Ausbildung: {len(original['ausbildung'])} Zeilen")

        # Kombiniere zu Snapshot
        print("\n3. Kombiniere zu Snapshot (KORRIGIERT)...")
        snapshot_df, history_df, org_df, summary = load_original_and_prepare_data()

        print(f"   ✓ Snapshot: {len(snapshot_df)} Zeilen")
        print(f"   ✓ History: {len(history_df)} Zeilen")
        print(f"   ✓ Org-Struktur: {len(org_df)} Zeilen")
        
        # Validierung: Keine Duplikate durch ATZ-Merge?
        planstellen_clean = clean_planstellen(original['planstellen'])
        erwartete_zeilen = len(planstellen_clean)
        if len(snapshot_df) == erwartete_zeilen:
            print(f"   ✓ KEINE Duplikate durch ATZ-Merge!")
        else:
            print(f"   ⚠ Zeilen-Differenz: {len(snapshot_df) - erwartete_zeilen}")

        # Zeige Summary
        print("\n4. Summary:")
        print(f"   Planstellen: {summary['total_planstellen']}")
        print(f"   Mitarbeitende: {summary['total_employees']}")
        print(f"   FTE: {summary['total_fte']:.1f}")
        print(f"   Soll-FTE: {summary['total_soll_fte']:.1f}")
        print(f"   Vakanzen: {summary['vacancy_count']} ({summary['vacancy_rate']*100:.1f}%)")
        print(f"   Besetzungsgrad: {summary['besetzungsgrad']*100:.1f}%")
        print(f"   ATZ gesamt: {summary['atz_count']}")
        if 'atz_ar_count' in summary:
            print(f"     - Arbeitsphase: {summary['atz_ar_count']}")
            print(f"     - Freistellungsphase: {summary['atz_fr_count']}")
        if 'ruhend_count' in summary:
            print(f"   Ruhend (nicht ATZ): {summary['ruhend_count']}")

        print("\n" + "=" * 60)
        print("✓ ERFOLGREICH!")
        print("=" * 60)

    except FileNotFoundError as e:
        print(f"\n✗ FEHLER: {e}")
    except Exception as e:
        print(f"\n✗ FEHLER: {e}")
        import traceback
        traceback.print_exc()
