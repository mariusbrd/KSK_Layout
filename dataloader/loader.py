"""
Daten-Loader für HR Pulse Dashboard.

Lädt und cached HR-Daten aus Excel mit Streamlit.

VERSION 2.0 - KORRIGIERT:
- ATZ-Status korrekt aus ist_atz_fr Flag
- MAK-Berechnung unterscheidet Ruhend vs. ATZ-FR
- Erweiterte Summary-Statistiken
"""

import pandas as pd
import streamlit as st
from typing import Dict, Tuple, Optional, Set
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DATA_PATH, DEFAULT_COHORTS

ID_PAD_LENGTH = 6

# Stichtag (verbindlich, kein Systemdatum!)
STICHTAG = pd.Timestamp("2025-01-30")


def normalize_persnr(series: pd.Series) -> pd.Series:
    """Normalisiert Personalnummern zu String mit führenden Nullen."""
    return series.apply(
        lambda x: str(int(x)).zfill(ID_PAD_LENGTH) if pd.notna(x) else pd.NA
    )


@st.cache_data
def load_hr_data(filepath: Optional[str] = None, auto_generate: bool = True) -> Dict[str, pd.DataFrame]:
    """
    Lädt HR-Daten aus Excel-Datei.

    Wenn die Datei nicht existiert und auto_generate=True, werden automatisch
    synthetische Testdaten generiert.

    Args:
        filepath: Pfad zur Excel-Datei (default: aus settings.py)
        auto_generate: Automatisch Testdaten generieren falls Datei fehlt

    Returns:
        Dictionary mit DataFrames:
        - snapshot_detail
        - history_cube
        - org_structure

    Raises:
        FileNotFoundError: Wenn Datei nicht existiert und auto_generate=False
    """
    if filepath is None:
        filepath = DATA_PATH

    if not os.path.exists(filepath):
        if auto_generate:
            st.warning("⚠️ Testdaten nicht gefunden. Generiere automatisch synthetische Daten...")
            try:
                # Importiere Generator-Funktionen
                from dataloader.synthetic import generate_synthetic_data, save_to_excel

                # Erstelle Verzeichnis falls nicht vorhanden
                os.makedirs(os.path.dirname(filepath), exist_ok=True)

                # Generiere Daten
                data_dict = generate_synthetic_data()

                # Speichere in Excel
                save_to_excel(data_dict, filepath)

                st.success("✅ Testdaten erfolgreich generiert!")
            except Exception as e:
                st.error(f"❌ Fehler beim Generieren der Testdaten: {str(e)}")
                raise
        else:
            raise FileNotFoundError(
                f"Datei nicht gefunden: {filepath}\n"
                f"Bitte zuerst Testdaten generieren mit: python dataloader/synthetic.py"
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


def get_atz_status(row) -> str:
    """
    Ermittelt ATZ-Status für eine Zeile.
    
    KORRIGIERT (v2.0):
    - Verwendet ist_atz_fr Flag (wenn vorhanden)
    - Fallback auf Phase-Spalte
    - Letzter Fallback auf Vertragsart + Alter
    
    Args:
        row: DataFrame Row
    
    Returns:
        ATZ-Status: "Kein ATZ", "Arbeitsphase", oder "Freistellungsphase"
    """
    # Methode 1: Über ist_atz_fr Flag (von original_loader.py v2.0)
    if row.get("ist_atz_fr", False):
        return "Freistellungsphase"
    
    # Methode 2: Über Phase-Spalte (von ATZ-Merge)
    if "Phase" in row.index and pd.notna(row.get("Phase")):
        phase = row["Phase"]
        if phase == "AR":
            return "Arbeitsphase"
        elif phase == "FR":
            return "Freistellungsphase"
    
    # Methode 3: Fallback über Vertragsart (nur wenn keine besseren Daten)
    vertragsart = row.get("Vertragsart")
    if pd.notna(vertragsart) and vertragsart == "Altersteilzeit":
        # Schätzung basierend auf Alter (NICHT zuverlässig!)
        alter = row.get("Alter", 0)
        if alter >= 60:
            return "Freistellungsphase"
        return "Arbeitsphase"
    
    return "Kein ATZ"


def berechne_mak(row, atz_fr_persnr_set: Optional[Set[str]] = None) -> float:
    """
    Berechnet MAK (Mitarbeiterkapazität) für eine Zeile.
    
    KORRIGIERT (v2.0):
    - Unterscheidet Ruhend (Elternzeit etc.) von ATZ-FR
    - Verwendet ist_atz_fr Flag wenn verfügbar
    
    MAK = 0 für:
    - Vakante Stellen
    - Ruhende Beschäftigungsverhältnisse (Elternzeit, Sabbatical etc.)
    - ATZ-Freizeitphase
    
    MAK = BsGrd/100 für alle anderen
    
    Args:
        row: DataFrame Row
        atz_fr_persnr_set: Optional Set der PersNr in ATZ-Freizeitphase
    
    Returns:
        MAK-Wert (0.0 bis 1.0)
    """
    # Vakante Stelle = 0
    if row.get("Is_Vacant", False):
        return 0.0
    
    # 1. Ruhend = 0 (Elternzeit, Sabbatical etc. - NICHT ATZ!)
    if row.get("Status kundenindividuell") == "Ruhendes Beschäftigungsverhältnis":
        return 0.0
    
    # 2. ATZ-Freizeitphase = 0
    # Methode A: Über Flag
    if row.get("ist_atz_fr", False):
        return 0.0
    
    # Methode B: Über PersNr-Set (falls übergeben)
    if atz_fr_persnr_set is not None:
        persnr = row.get("PersNr")
        if pd.notna(persnr) and persnr in atz_fr_persnr_set:
            return 0.0
    
    # 3. Aktiv → FTE aus BsGrd
    bsgrd = row.get("BsGrd", 0)
    if pd.isna(bsgrd):
        bsgrd = 0
    
    return bsgrd / 100.0


@st.cache_data
def enrich_snapshot_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reichert Snapshot-Daten mit berechneten Feldern an.

    KORRIGIERT (v2.0):
    - ATZ-Status verwendet neue get_atz_status() Funktion
    - MAK-Berechnung mit berechne_mak() Funktion
    
    Args:
        df: Snapshot_Detail DataFrame

    Returns:
        Angereicherter DataFrame
    """
    df = df.copy()

    # IDs standardisieren
    if "PersNr" in df.columns:
        df["PersNr"] = normalize_persnr(df["PersNr"])

    if "Personalnummer" in df.columns:
        df["Personalnummer"] = normalize_persnr(df["Personalnummer"])

    # Austritt 9999-12-31 auf NaT setzen
    if "Austritt" in df.columns:
        df["Austritt"] = pd.to_datetime(df["Austritt"], errors="coerce")
        austritt_year = pd.DatetimeIndex(df["Austritt"]).year
        df.loc[austritt_year == 9999, "Austritt"] = pd.NaT

    # Alter berechnen (zum STICHTAG, nicht Systemdatum!)
    stichtag = STICHTAG
    df["Alter_Jahre"] = (stichtag - pd.to_datetime(df["GebDatum"], errors="coerce")).dt.days / 365.25
    df["Alter_Jahre"] = df["Alter_Jahre"].fillna(0)
    df["Alter"] = df["Alter_Jahre"].astype(int)

    # Betriebszugehörigkeit in Jahren (zum STICHTAG)
    df["Betriebszugehörigkeit_Jahre"] = (
        stichtag - pd.to_datetime(df["Eintritt"], errors="coerce")
    ).dt.days / 365.25
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

    # Vollzeit/Teilzeit (Readme: 0 < BsGrd < 100 = Teilzeit)
    # Exkludiert Ruhend und ATZ-FR (BsGrd kann 0 sein)
    def _arbeitszeit(row):
        bsgrd = row.get("BsGrd", 0)
        if pd.isna(bsgrd):
            bsgrd = 0
        if bsgrd == 0:
            return "Inaktiv"
        if bsgrd < 100:
            return "Teilzeit"
        return "Vollzeit"
    df["Arbeitszeit"] = df.apply(_arbeitszeit, axis=1)

    # ATZ-Status (KORRIGIERT)
    df["ATZ_Status"] = df.apply(get_atz_status, axis=1)

    # MAK berechnen (KORRIGIERT)
    # Erstelle ATZ-FR Set wenn ist_atz_fr Spalte existiert
    atz_fr_persnr_set: Optional[Set[str]] = None
    if "ist_atz_fr" in df.columns:
        atz_fr_persnr_set = set(df[df["ist_atz_fr"] == True]["PersNr"].dropna())
    
    df["MAK"] = df.apply(lambda row: berechne_mak(row, atz_fr_persnr_set), axis=1)

    # Ist-Soll Abweichung
    df["Abweichung_FTE"] = df["Soll_FTE"] - df["FTE_assigned"]

    return df


@st.cache_data
def get_data_summary(snapshot_df: pd.DataFrame) -> Dict:
    """
    Berechnet Zusammenfassungsstatistiken (Readme-konform).

    Verwendet kpi_engine für konsistente, deduplizierte Berechnungen
    auf Mitarbeiter-Ebene (unique PersNr).

    Args:
        snapshot_df: Angereicherter Snapshot DataFrame

    Returns:
        Dictionary mit KPIs
    """
    from dataloader.kpi_engine import compute_readme_summary, enrich_summary_with_gender
    summary = compute_readme_summary(snapshot_df)
    summary = enrich_summary_with_gender(summary, snapshot_df)
    return summary


def load_and_prepare_data(use_original: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    Kompletter Daten-Lade- und Aufbereitungsprozess.

    Args:
        use_original: Wenn True, versucht Original-Daten zu laden (default: True)

    Returns:
        Tuple aus (snapshot_df, history_df, org_df, summary)
    """
    # Versuche Original-Daten zu laden (wenn use_original=True)
    if use_original:
        try:
            from dataloader.original_loader import load_original_and_prepare_data

            # Prüfe ob Original-Daten existieren (alle benötigten Dateien)
            original_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "Original-Daten")

            required_files = {
                "Mitarbeiter.xlsx": os.path.join(original_dir, "Mitarbeiter.xlsx"),
                "Planstellen.XLSX": os.path.join(original_dir, "Planstellen.XLSX"),
                "ATZ.xlsx": os.path.join(original_dir, "ATZ.xlsx"),
                "Ausbildung.xlsx": os.path.join(original_dir, "Ausbildung.xlsx"),
            }
            optional_files = {
                "TVÖD.xlsx": os.path.join(original_dir, "TVÖD.xlsx"),
            }

            missing_required = [name for name, path in required_files.items() if not os.path.exists(path)]
            missing_optional = [name for name, path in optional_files.items() if not os.path.exists(path)]

            if not missing_required:
                if missing_optional:
                    st.warning(
                        "Optional fehlend: " + ", ".join(missing_optional) + ". "
                        "Kosten werden aus approximierten Fallback-Werten berechnet "
                        "(nicht exakte TVöD-Tabelle)."
                    )
                    st.session_state["tvoed_available"] = False
                return load_original_and_prepare_data()
            else:
                st.info(
                    "ℹ️ Original-Daten unvollständig oder nicht gefunden. "
                    "Fehlend: " + ", ".join(missing_required) + ". "
                    "Verwende synthetische Testdaten."
                )
        except Exception as e:
            st.warning(f"⚠️ Fehler beim Laden der Original-Daten: {str(e)}\nVerwende synthetische Testdaten.")

    # Fallback: Lade synthetische Daten
    data = load_hr_data()

    # Reichere Snapshot an
    snapshot_df = enrich_snapshot_data(data["snapshot_detail"])

    # Füge Jobfamily-Spalte hinzu
    from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions
    try:
        definitions = load_jobfamily_definitions()
        snapshot_df = assign_jobfamilies(snapshot_df, definitions)
    except Exception as e:
        # Falls Fehler beim Laden, füge leere Spalte hinzu
        if "Jobfamily" not in snapshot_df.columns:
            snapshot_df["Jobfamily"] = "UNMAPPED"

    # Berechne Summary
    summary = get_data_summary(snapshot_df)

    return (
        snapshot_df,
        data["history_cube"],
        data["org_structure"],
        summary
    )


# =============================================================================
# VALIDIERUNGSFUNKTIONEN
# =============================================================================

def validate_snapshot(df: pd.DataFrame) -> Dict[str, bool]:
    """
    Validiert einen Snapshot DataFrame auf bekannte Probleme.
    
    Returns:
        Dict mit Validierungsergebnissen
    """
    results = {}
    
    # 1. Keine übermäßigen Duplikate?
    if 'PersNr' in df.columns:
        pers_counts = df[df['PersNr'].notna()].groupby('PersNr').size()
        max_dups = pers_counts.max() if len(pers_counts) > 0 else 0
        results["max_planstellen_pro_person"] = max_dups
        results["keine_atz_duplikate"] = max_dups <= 3  # 2-3 ist ok (Mehrfachplanstellen)
    
    # 2. Azubi-Sollarbeitszeit korrigiert?
    if 'Kürzel OrgEinheit' in df.columns and 'Sollarbeitszeit' in df.columns:
        azubi_soll = df[df['Kürzel OrgEinheit'] == '9910']['Sollarbeitszeit']
        if len(azubi_soll) > 0:
            results["azubi_sollarbeitszeit_ok"] = (azubi_soll >= 38.0).all()
        else:
            results["azubi_sollarbeitszeit_ok"] = True
    
    # 3. MAK-Spalte vorhanden?
    results["mak_vorhanden"] = "MAK" in df.columns
    
    # 4. ATZ_Status gültig?
    if "ATZ_Status" in df.columns:
        valid_status = {"Kein ATZ", "Arbeitsphase", "Freistellungsphase"}
        results["atz_status_valid"] = df["ATZ_Status"].isin(valid_status).all()
    
    return results


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

        # Validierung
        print("\n" + "=" * 60)
        print("VALIDIERUNG:")
        print("=" * 60)
        validation = validate_snapshot(snapshot)
        for key, value in validation.items():
            status = "✓" if value == True or (isinstance(value, (int, float)) and value <= 3) else "⚠"
            print(f"  {status} {key}: {value}")

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
        print("  python dataloader/synthetic.py")
