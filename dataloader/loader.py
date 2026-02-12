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
import numpy as np
from datetime import datetime
from typing import Dict, Tuple, Optional, Set, Any
import sys
import os

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    DATA_PATH, DEFAULT_COHORTS, BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR
)
from utils.settings_loader import get_setting
from dataloader.cluster_manager import apply_clusters_to_snapshot
from kpi_reference import get_current_stichtag

ID_PAD_LENGTH = 6


from abgaenge.schemas import normalize_persnr


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

    # P05: Explicit normalization for robustness
    if "snapshot_detail" in data and "PersNr" in data["snapshot_detail"].columns:
        data["snapshot_detail"]["PersNr"] = normalize_persnr(data["snapshot_detail"]["PersNr"])

    return data


@st.cache_data
def load_atz_data_cached(base_path_str: str, uploaded_ma: Any = None, uploaded_atz: Any = None, uploaded_pl: Any = None) -> pd.DataFrame:
    """
    Loads only ATZ data needed for forecast engine details.
    Cached wrapper around abgaenge.io.load_inputs.
    """
    # Deferred import to avoid circular dependency risks if any exist
    from abgaenge.io import load_inputs
    from pathlib import Path
    
    try:
        _, df_atz = load_inputs(Path(base_path_str), uploaded_ma, uploaded_atz, uploaded_pl)
        return df_atz
    except Exception as e:
        # Return empty DataFrame on failure to allow UI to proceed with warning
        return pd.DataFrame()


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
def enrich_snapshot_data(df: pd.DataFrame, stichtag: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """
    Reichert Snapshot-Daten mit berechneten Feldern an.

    KORRIGIERT (v2.0):
    - ATZ-Status verwendet neue get_atz_status() Funktion
    - MAK-Berechnung mit berechne_mak() Funktion
    
    Args:
        df: Snapshot_Detail DataFrame
        stichtag: Referenzdatum für Berechnungen (Default: get_current_stichtag())

    Returns:
        Angereicherter DataFrame
    """
    df = df.copy()

    if stichtag is None:
        stichtag = get_current_stichtag()

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


def load_and_prepare_data(
    use_original: bool = True,
    uploaded_files: Optional[Dict[str, Any]] = None
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    Kompletter Daten-Lade- und Aufbereitungsprozess.

    Args:
        use_original: Wenn True, versucht Original-Daten zu laden (default: True)
        uploaded_files: Optionales Dict mit Upload-Files (Mitarbeiter, Planstellen, etc.)

    Returns:
        Tuple aus (snapshot_df, history_df, org_df, summary)
    """



    # 0. Check global uploads (from session_state)
    if uploaded_files is None and "global_uploads" in st.session_state:
        uploaded_files = st.session_state["global_uploads"]

    # 1. Uploads haben Vorrang
    if uploaded_files:
        try:
            data = process_uploaded_data(uploaded_files)
            # data enthält bereits snapshot_detail, history_cube, org_structure
            snapshot_df = data["snapshot_detail"]
            history_df = data["history_cube"]
            org_df = data["org_structure"]
            
            
            # TVOED Loading for Upload Path
            from dataloader.tvoed_loader import load_tvoed_table
            tvoed_lookup = {}
            if "TVÖD" in uploaded_files:
                 # Load from BytesIO
                 tvoed_lookup = load_tvoed_table(uploaded_files["TVÖD"])
            elif os.path.exists(TVOED_FILE):
                 tvoed_lookup = load_tvoed_table(TVOED_FILE)
            
            st.session_state["tvoed_lookup"] = tvoed_lookup
            st.session_state["tvoed_available"] = len(tvoed_lookup) > 0

            # Anreicherung
            snapshot_df = enrich_snapshot_data(snapshot_df, stichtag=get_current_stichtag())
            
            # Jobfamily
            from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions
            try:
                definitions = load_jobfamily_definitions()
                snapshot_df = assign_jobfamilies(snapshot_df, definitions)
            except Exception:
                if "Jobfamily" not in snapshot_df.columns:
                    snapshot_df["Jobfamily"] = "UNMAPPED"
            
            # Custom Clusters
            snapshot_df = apply_clusters_to_snapshot(snapshot_df)

            summary = get_data_summary(snapshot_df)
            summary["data_source_type"] = "Eigene Daten (Upload)"
            return snapshot_df, history_df, org_df, summary
            
        except Exception as e:
            st.error(f"Fehler bei der Verarbeitung der hochgeladenen Dateien: {str(e)}")
            # Fallback auf Standard-Logik unten
            pass

    # Versuche Original-Daten zu laden (wenn use_original=True)
    if use_original:
        try:
            # Prüfe ob Original-Daten existieren (alle benötigten Dateien)
            missing_required = [name for name, path in ORIGINAL_FILES.items() if not os.path.exists(path)]
            
            if not missing_required:
                # 1. Lade Original-Daten
                original = load_original_data()

                # 2. Lade TVÖD-Entgelttabelle (optional)
                from dataloader.tvoed_loader import load_tvoed_table
                tvoed_lookup = {}
                if "TVÖD" in (uploaded_files or {}):
                     tvoed_lookup = load_tvoed_table(uploaded_files["TVÖD"])
                elif os.path.exists(TVOED_FILE):
                     tvoed_lookup = load_tvoed_table(TVOED_FILE)
                
                st.session_state["tvoed_lookup"] = tvoed_lookup
                st.session_state["tvoed_available"] = len(tvoed_lookup) > 0

                # 3. Kombiniere zu Snapshot (mit TVÖD-Lookup)
                current_stichtag = get_current_stichtag()
                snapshot_df = combine_to_snapshot(
                    original["mitarbeiter"],
                    original["planstellen"],
                    original["atz"],
                    original["ausbildung"],
                    stichtag=current_stichtag,
                    tvoed_lookup=tvoed_lookup,
                )

                # 4. Reichere Snapshot an (Standard-Prozedur)
                snapshot_df = enrich_snapshot_data(snapshot_df, stichtag=current_stichtag)

                # 5. Füge Jobfamily-Spalte hinzu
                from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions
                try:
                    definitions = load_jobfamily_definitions()
                    snapshot_df = assign_jobfamilies(snapshot_df, definitions)
                except Exception:
                    if "Jobfamily" not in snapshot_df.columns:
                        snapshot_df["Jobfamily"] = "UNMAPPED"

                # 5b. Custom Clusters
                snapshot_df = apply_clusters_to_snapshot(snapshot_df)

                # 6. Generiere History
                history_df = generate_history_from_snapshot(snapshot_df)

                # 7. Erstelle Org-Struktur
                org_df = create_org_structure(original["planstellen"])

                # 8. Berechne Summary
                summary = get_data_summary(snapshot_df)
                summary["data_source_type"] = "Original-Daten"

                return snapshot_df, history_df, org_df, summary

            else:
                st.info(
                    "ℹ️ Original-Daten unvollständig oder nicht gefunden. "
                    "Fehlend: " + ", ".join(missing_required) + ". "
                    "Verwende synthetische Testdaten."
                )

        except Exception as e:
            st.warning(f"⚠️ Fehler beim Laden der Original-Daten: {str(e)}\nVerwende synthetische Testdaten.")
            # Optional: Traceback bei Fehler
            # import traceback
            # st.code(traceback.format_exc())

    # Fallback: Lade synthetische Daten
    data = load_hr_data()

    # Reichere Snapshot an
    snapshot_df = enrich_snapshot_data(data["snapshot_detail"], stichtag=get_current_stichtag())

    # Füge Jobfamily-Spalte hinzu
    from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions
    try:
        definitions = load_jobfamily_definitions()
        snapshot_df = assign_jobfamilies(snapshot_df, definitions)
    except Exception as e:
        # Falls Fehler beim Laden, füge leere Spalte hinzu
        if "Jobfamily" not in snapshot_df.columns:
            snapshot_df["Jobfamily"] = "UNMAPPED"

    # Custom Clusters
    snapshot_df = apply_clusters_to_snapshot(snapshot_df)

    # Berechne Summary
    summary = get_data_summary(snapshot_df)
    summary["data_source_type"] = "Synthetische Testdaten"

    return (
        snapshot_df,
        data["history_cube"],
        data["org_structure"],
        summary
    )



# =============================================================================
# KONSTANTEN & SETUP (aus original_loader.py)
# =============================================================================

from config.settings import DEFAULT_COHORTS, BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DATA_DIR = os.path.join(BASE_DIR, "..", "Original-Daten")

ORIGINAL_FILES = {
    "mitarbeiter": os.path.join(ORIGINAL_DATA_DIR, "Mitarbeiter.xlsx"),
    "planstellen": os.path.join(ORIGINAL_DATA_DIR, "Planstellen.XLSX"),
    "atz": os.path.join(ORIGINAL_DATA_DIR, "ATZ.xlsx"),
    "ausbildung": os.path.join(ORIGINAL_DATA_DIR, "Ausbildung.xlsx"),
}

TVOED_FILE = os.path.join(ORIGINAL_DATA_DIR, "TVÖD.xlsx")

EDUCATION_MAPPING = {
    "Bachelor FH": "Bachelor",
    "Bachelor Universität": "Bachelor",
    "Master FH": "Master",
    "Master Universität": "Master",
    "Studium Lehrinstitut": "Sonstiges Studium",
    "Bankbetriebswirt": "Bankspezifische Weiterbildung",
    "Bankfachwirt": "Bankspezifische Weiterbildung",
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
    "Bankbetriebswirt": 4,
    "Bankfachwirt": 4,
    "Bankberufsabschluss": 3,
    "kfm Berufsabschluss": 3,
    "nicht kfm Berufsabschluss": 3,
    "derzeit Berufsausbildung": 2,
    "ohne Berufsabschluss": 1,
}

# =============================================================================
# HILFSFUNKTIONEN (aus original_loader.py)
# =============================================================================

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
        try:
            year = getattr(val, "year", None)
            if year is not None and (year == 9999 or year > 2262):
                return pd.NaT
        except Exception:
            pass
        s = str(val).strip()
        if s.startswith("9999-") or s.startswith("9999/"):
            return pd.NaT
        return val

    cleaned = series.map(_to_na_if_out_of_bounds)
    return pd.to_datetime(cleaned, errors="coerce")


def derive_atz_fields(atz_df: pd.DataFrame) -> pd.DataFrame:
    """Erzeugt ATZ-Ableitungen pro Person gemäß Readme."""
    if atz_df.empty:
        return pd.DataFrame(columns=[
            "PersNr", "atz_start_date", "atz_rest_start_date", "atz_end_date",
            "atz_duration_ar_months", "atz_duration_fr_months",
            "atz_has_two_phases", "atz_phase_gap_ok", "atz_phase_end_matches_contract"
        ])

    df = atz_df.copy()
    df["Phase"] = df["Phase"].astype(str).str.strip()

    phase_counts = df.groupby("PersNr")["Phase"].nunique().rename("atz_phase_count")
    phase_counts = phase_counts.to_frame()
    phase_counts["atz_has_two_phases"] = phase_counts["atz_phase_count"] == 2

    pivot = df.pivot_table(
        index="PersNr", columns="Phase", values=["Beginn", "Ende", "Ende ATZ Vertrag"], aggfunc="min"
    )
    pivot.columns = [f"{col[0]}_{col[1]}" for col in pivot.columns]
    result = pivot.join(phase_counts, how="left").reset_index()

    result["atz_start_date"] = result.get("Beginn_AR", pd.NaT)
    result["atz_rest_start_date"] = result.get("Beginn_FR", pd.NaT)
    result["atz_end_date"] = pd.Series(result.get("Ende ATZ Vertrag_FR", pd.NaT)).combine_first(pd.Series(result.get("Ende ATZ Vertrag_AR", pd.NaT)))

    ar_end = pd.Series(result.get("Ende_AR", pd.NaT))
    fr_begin = pd.Series(result.get("Beginn_FR", pd.NaT))
    fr_end = pd.Series(result.get("Ende_FR", pd.NaT))
    contract_end = pd.Series(result["atz_end_date"])

    result["atz_duration_ar_months"] = ((ar_end - pd.to_datetime(result["atz_start_date"], errors="coerce")).dt.days / 30.44)
    result["atz_duration_fr_months"] = ((pd.to_datetime(fr_end, errors="coerce") - pd.to_datetime(fr_begin, errors="coerce")).dt.days / 30.44)
    result["atz_phase_gap_ok"] = (ar_end.notna() & fr_begin.notna()) & ((pd.to_datetime(fr_begin, errors="coerce") - pd.to_datetime(ar_end, errors="coerce")).dt.days == 1)
    result["atz_phase_end_matches_contract"] = (fr_end.notna() & contract_end.notna() & (fr_end == contract_end))

    return result[[
        "PersNr", "atz_start_date", "atz_rest_start_date", "atz_end_date",
        "atz_duration_ar_months", "atz_duration_fr_months",
        "atz_has_two_phases", "atz_phase_gap_ok", "atz_phase_end_matches_contract"
    ]]


def clean_step(step) -> int:
    """Bereinigt Stufen-Werte wie '2+' zu 2."""
    if pd.isna(step):
        return 4
    step_str = str(step).strip().replace("+", "").replace("-", "")
    try:
        return int(step_str)
    except (ValueError, TypeError):
        return 4


def clean_planstellen(df: pd.DataFrame) -> pd.DataFrame:
    """Bereinigt Planstellen-Daten (entfernt Summenzeile, korrigiert Azubi)."""
    df = df.copy()
    df = df[df['Kürzel OrgEinheit'].notna()]
    azubi_mask = (df['Kürzel OrgEinheit'] == '9910') & (df['Sollarbeitszeit'] == 0.01)
    df.loc[azubi_mask, 'Sollarbeitszeit'] = 39.0
    if "Personalnummer" in df.columns:
        df["Personalnummer"] = normalize_persnr(df["Personalnummer"])
    return df


def get_current_atz_phase(atz_df: pd.DataFrame, stichtag: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Filtert ATZ-Daten auf die aktuelle Phase zum Stichtag."""
    if stichtag is None:
        stichtag = get_current_stichtag()
    atz_aktuell = atz_df[(atz_df['Beginn'] <= stichtag) & (atz_df['Ende'] >= stichtag)].copy()
    if atz_aktuell.empty:
        return atz_aktuell
    if atz_aktuell['PersNr'].duplicated().any():
        atz_aktuell = atz_aktuell.sort_values('Phase', ascending=False)
        atz_aktuell = atz_aktuell.drop_duplicates(subset='PersNr', keep='first')
    return atz_aktuell


def calculate_cost_row(row, tvoed_lookup=None) -> float:
    """Berechnet Jahreskosten für eine Zeile."""
    from dataloader.tvoed_loader import get_annual_salary, get_special_salary

    if row.get("Is_Vacant", True):
        return 0.0

    tariff = row.get("TrfGr", "E9A")
    step = row.get("St", 4)
    fte = row.get("FTE_person", 1.0) # Nutze FTE_person statt BsGrd

    if pd.isna(tariff): tariff = "E9A"
    if pd.isna(step): step = 4
    if pd.isna(fte): fte = 1.0

    tariff = str(tariff).strip().upper().replace(" ", "")
    step_int = clean_step(step)

    special = get_special_salary(tariff, step=step_int)
    employer_factor = st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR)

    if special is not None:
        return special * fte * employer_factor

    annual = get_annual_salary(tvoed_lookup or {}, tariff, step_int, BASE_SALARY, STEP_MULTIPLIER)
    return annual * fte * employer_factor

# =============================================================================
# CORE LOADING FUNKTIONEN (aus original_loader.py)
# =============================================================================

@st.cache_data
def load_original_data() -> Dict[str, pd.DataFrame]:
    """Lädt die 4 Original-Excel-Dateien."""
    data = {}
    missing_files = [fp for fp in ORIGINAL_FILES.values() if not os.path.exists(fp)]
    if missing_files:
        raise FileNotFoundError(f"Original-Daten nicht gefunden:\n" + "\n".join(missing_files))

    data["mitarbeiter"] = pd.read_excel(ORIGINAL_FILES["mitarbeiter"])
    if "GebDatum" in data["mitarbeiter"].columns:
        data["mitarbeiter"]["GebDatum"] = pd.to_datetime(data["mitarbeiter"]["GebDatum"], errors="coerce")
    if "Eintritt" in data["mitarbeiter"].columns:
        data["mitarbeiter"]["Eintritt"] = pd.to_datetime(data["mitarbeiter"]["Eintritt"], errors="coerce")
    if "Austritt" in data["mitarbeiter"].columns:
        data["mitarbeiter"]["Austritt"] = safe_parse_austritt(data["mitarbeiter"]["Austritt"])
    data["mitarbeiter"]["PersNr"] = normalize_persnr(data["mitarbeiter"]["PersNr"])

    data["planstellen"] = pd.read_excel(ORIGINAL_FILES["planstellen"])

    data["atz"] = pd.read_excel(ORIGINAL_FILES["atz"], parse_dates=["Beginn", "Ende", "Ende ATZ Vertrag"])
    data["atz"] = normalize_atz(data["atz"])

    data["ausbildung"] = pd.read_excel(ORIGINAL_FILES["ausbildung"])
    data["ausbildung"]["Personalnummer"] = normalize_persnr(data["ausbildung"]["Personalnummer"])
    data["ausbildung"]["BV Ausbildungsgruppentext"] = data["ausbildung"]["BV Ausbildungsgruppentext"].astype(str).str.strip()

    return data



def calculate_cost_vectorized(df: pd.DataFrame, tvoed_lookup: Dict) -> pd.DataFrame:
    """
    Berechnet Total_Cost_Year mittels vektorisierter Operationen (Merge statt Apply).
    100x schneller als iteratives apply().
    """
    import numpy as np
    
    df_out = df.copy()
    
    # 1. Prepare Lookup DataFrame
    if not tvoed_lookup:
        lookup_df = pd.DataFrame(columns=["TrfGr_L", "St_L", "Annual_L"])
    else:
        lookup_data = [{"TrfGr_L": k[0], "St_L": k[1], "Annual_L": v} for k, v in tvoed_lookup.items()]
        lookup_df = pd.DataFrame(lookup_data)
        
    # 2. Add Join Keys to Main DataFrame (Cleaned)
    if "TrfGr" not in df_out.columns: df_out["TrfGr"] = "E9A"
    if "St" not in df_out.columns: df_out["St"] = 4
    
    # Normalize TrfGr similar to tvoed_loader logic
    df_out["TrfGr_Join"] = df_out["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    # St is already cleaned
    df_out["St_Join"] = pd.to_numeric(df_out["St"], errors='coerce').fillna(4).astype(int)
    
    # 3. Merge Lookup
    df_out = df_out.merge(lookup_df, left_on=["TrfGr_Join", "St_Join"], right_on=["TrfGr_L", "St_L"], how="left")
    
    # 4. Fallback Logic (Vectorized Map)
    fallback_base = df_out["TrfGr_Join"].map(BASE_SALARY).fillna(50000)
    fallback_mult = df_out["St_Join"].map(STEP_MULTIPLIER).fillna(1.0)
    fallback_annual = fallback_base * fallback_mult
    
    # Fill NaN lookup values with fallback
    df_out["Annual_Final"] = df_out["Annual_L"].fillna(fallback_annual)
    
    # 5. Helper: Special Salaries
    # Azubi (Progressiv)
    from config.settings import DEFAULT_AZUBI_SALARIES
    azubi_salaries = st.session_state.get("azubi_salaries", DEFAULT_AZUBI_SALARIES)
    
    mask_azubi_base = df_out["TrfGr_Join"].isin(["TVAÖD", "TVÖAD", "TVAOD"])
    for year, salary in azubi_salaries.items():
        mask_year = mask_azubi_base & (df_out["St_Join"] == year)
        df_out.loc[mask_year, "Annual_Final"] = salary
    
    # Fallback for Azubis without valid year (St)
    mask_azubi_fallback = mask_azubi_base & (~df_out["St_Join"].isin(azubi_salaries.keys()))
    df_out.loc[mask_azubi_fallback, "Annual_Final"] = azubi_salaries.get(1, 14400.0)
    
    # Vorstand
    vorstand_salary = st.session_state.get("vorstand_jahresgehalt", 200000.0)
    mask_vorstand = df_out["TrfGr_Join"] == "1"
    df_out.loc[mask_vorstand, "Annual_Final"] = vorstand_salary
    
    # 6. Final Calculation
    employer_factor = st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR)
    fte = df_out["FTE_person"].fillna(1.0)
    
    total_cost = df_out["Annual_Final"] * fte * employer_factor
    
    # 7. Vacancy check
    if "Is_Vacant" in df_out.columns:
        mask_vacant = df_out["Is_Vacant"] == True
        total_cost = np.where(mask_vacant, 0.0, total_cost)
        
    df_out["Total_Cost_Year"] = total_cost
    
    # Cleanup
    drop_cols = ["TrfGr_Join", "St_Join", "TrfGr_L", "St_L", "Annual_L", "Annual_Final"]
    df_out.drop(columns=[c for c in drop_cols if c in df_out.columns], inplace=True)
    
    return df_out


def calculate_mak_vectorized(df: pd.DataFrame, atz_fr_persnr_set: set = None) -> pd.DataFrame:
    """
    Berechnet MAK_Calculated vektorisiert.
    """
    df_out = df.copy()
    
    # Baseline
    df_out["MAK_Calculated"] = df_out["BsGrd"].fillna(0) / 100.0
    
    # Vacancy Mask
    if "Is_Vacant" in df_out.columns:
        df_out.loc[df_out["Is_Vacant"] == True, "MAK_Calculated"] = 0.0
        
    # Ruhend Mask
    if "Status kundenindividuell" in df_out.columns:
        df_out.loc[df_out["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis", "MAK_Calculated"] = 0.0
        
    # ATZ FR Mask
    if "ist_atz_fr" in df_out.columns:
        df_out.loc[df_out["ist_atz_fr"] == True, "MAK_Calculated"] = 0.0
    elif atz_fr_persnr_set:
        df_out.loc[df_out["PersNr"].isin(atz_fr_persnr_set), "MAK_Calculated"] = 0.0
        
    return df_out


def combine_to_snapshot(mitarbeiter, planstellen, atz, ausbildung, stichtag=None, tvoed_lookup=None) -> pd.DataFrame:
    """Kombiniert die 4 Original-Dateien zu einem Snapshot DataFrame."""
    if stichtag is None: stichtag = get_current_stichtag()

    mitarbeiter = mitarbeiter.copy()
    mitarbeiter["PersNr"] = normalize_persnr(mitarbeiter["PersNr"])
    if "Austritt" in mitarbeiter.columns:
        mitarbeiter["Austritt"] = pd.to_datetime(mitarbeiter["Austritt"], errors="coerce")
        austritt_year = pd.DatetimeIndex(mitarbeiter["Austritt"]).year
        mitarbeiter.loc[austritt_year == 9999, "Austritt"] = pd.NaT

    # --- FILTERUNG NACH STICHTAG (Neu: Konfigurierbar) ---
    # Nur Mitarbeiter berücksichtigen, die am Stichtag bereits da sind
    # und noch nicht ausgetreten sind.
    
    # 1. Konfiguration laden
    include_future = get_setting("include_future_hires", False)
    
    # 2. Eintritts-Logik
    if "Eintritt" in mitarbeiter.columns:
        mitarbeiter["Eintritt"] = pd.to_datetime(mitarbeiter["Eintritt"], errors="coerce")
        
        # Statistik: Wie viele liegen in der Zukunft?
        future_hires_mask = mitarbeiter["Eintritt"] > stichtag
        future_hires_count = future_hires_mask.sum()
        # Store in session state for display in Settings
        if "stats_future_hires" not in st.session_state or st.session_state["stats_future_hires"] != future_hires_count:
             st.session_state["stats_future_hires"] = int(future_hires_count)
        
        # Filter anwenden (wenn nicht explizit gewünscht)
        if not include_future:
            mitarbeiter = mitarbeiter[mitarbeiter["Eintritt"] <= stichtag]
    
    # 3. Austritts-Logik (Bleibt strikt: Wer weg ist, ist weg)
    if "Austritt" in mitarbeiter.columns:
        mitarbeiter = mitarbeiter[
            (mitarbeiter["Austritt"].isna()) | 
            (mitarbeiter["Austritt"] >= stichtag)
        ]

    ausbildung = ausbildung.copy()
    ausbildung["Personalnummer"] = normalize_persnr(ausbildung["Personalnummer"])
    ausbildung["BV Ausbildungsgruppentext"] = ausbildung["BV Ausbildungsgruppentext"].astype(str).str.strip()

    atz = normalize_atz(atz)
    df = clean_planstellen(planstellen)

    df = df.merge(mitarbeiter, left_on="Personalnummer", right_on="PersNr", how="left", suffixes=("", "_ma"))
    df = df.merge(ausbildung[["Personalnummer", "BV Ausbildungsgruppentext"]], left_on="Personalnummer", right_on="Personalnummer", how="left", suffixes=("", "_ausb"))
    df = df.rename(columns={"BV Ausbildungsgruppentext": "Ausbildung"})

    if "Ausbildung" in df.columns: df["Ausbildung"] = df["Ausbildung"].astype("string")
    df["Bildungskategorie"] = df["Ausbildung"].map(EDUCATION_MAPPING)
    df["Bildungsrang"] = df["Ausbildung"].map(EDUCATION_RANKING)

    if "MitarbGruppenbez." in df.columns:
        df["Ist_Azubi"] = df["MitarbGruppenbez."] == "Auszubildende"

    atz_aktuell = get_current_atz_phase(atz, stichtag)
    df = df.merge(atz_aktuell[["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag", "Modell"]], left_on="PersNr", right_on="PersNr", how="left", suffixes=("", "_atz"))
    if "Phase" in df.columns:
        df["Phase"] = df["Phase"].astype(str).str.strip()

    df["Is_Vacant"] = df["Personalnummer"].isna()
    df["FTE_person"] = df["BsGrd"].fillna(0) / 100.0
    df["Soll_FTE"] = df["Sollarbeitszeit"].fillna(0) / 39.0
    df["FTE_assigned"] = df["FTE_person"] * df["Soll_FTE"]

    # Optimized Step Cleaning (Vectorized)
    if "St" in df.columns:
        # Vectorized clean_step logic
        # 1. Convert to string, strip
        s_step = df["St"].astype(str).str.strip()
        # 2. Remove + and -
        s_step = s_step.str.replace("+", "", regex=False).str.replace("-", "", regex=False)
        # 3. Convert to numeric, coerce errors -> NaN
        s_numeric = pd.to_numeric(s_step, errors="coerce")
        # 4. Fill NaN with default (4) and cast to int
        df["St"] = s_numeric.fillna(4).astype(int)

    atz_derived = derive_atz_fields(atz)
    df = df.merge(atz_derived, on="PersNr", how="left")

    atz_fr_persnr = set(atz_aktuell[atz_aktuell['Phase'] == 'FR']['PersNr'])
    df['ist_atz_fr'] = df['PersNr'].isin(atz_fr_persnr)

    # Vectorized Cost Calculation
    # We pass the tvoed_lookup dict. The vectorized function handles the merge efficiently.
    df = calculate_cost_vectorized(df, tvoed_lookup)
    
    # Vectorized MAK Calculation (Optional here if not already computed?)
    # snapshot_df usually relies on FTE_assigned (calculated above).
    # But let's verify if 'MAK' column is needed explicitly?
    # validate_snapshot checks for "MAK".
    # We traditionally calc FTE_assigned. 
    # If we want an explicit "MAK" column matching berechne_mak logic:
    df = calculate_mak_vectorized(df, atz_fr_persnr)

    return df



def calculate_cost_row(row) -> float:
    """Berechnet Jahreskosten für eine Zeile (aus synthetic.py übernommen)."""
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

    # Stufe bereinigen
    step_str = str(step).strip().replace("+", "").replace("-", "")
    try:
        step_int = int(step_str)
    except (ValueError, TypeError):
        step_int = 4

    # Globale Konstanten nutzen (oben importiert)
    base = BASE_SALARY.get(str(tariff), 50000)
    step_factor = STEP_MULTIPLIER.get(step_int, 1.0)

    return base * step_factor * fte * EMPLOYER_COST_FACTOR


def create_combined_snapshot(
    mitarbeiter: pd.DataFrame,
    planstellen: pd.DataFrame,
    atz: pd.DataFrame,
    ausbildung: pd.DataFrame,
    stichtag: Optional[pd.Timestamp] = None
) -> pd.DataFrame:
    """
    Kombiniert die 4 Dateien zu einem Snapshot (ETL-Logik).
    """
    if stichtag is None:
        stichtag = pd.Timestamp.today()
    
    df = planstellen.copy()
    
    # Rename & Normalize
    if "Personalnummer" in df.columns:
        df = df.rename(columns={"Personalnummer": "PersNr_Plan"})
    
    if "PersNr_Plan" in df.columns:
        df["PersNr_Plan"] = normalize_persnr(df["PersNr_Plan"])

    mitarbeiter = mitarbeiter.copy()
    if "PersNr" in mitarbeiter.columns:
        mitarbeiter["PersNr"] = normalize_persnr(mitarbeiter["PersNr"])

    ausbildung = ausbildung.copy()
    if "Personalnummer" in ausbildung.columns:
        ausbildung["Personalnummer"] = normalize_persnr(ausbildung["Personalnummer"])

    # Merge Mitarbeiter
    # Ensure join keys are strings
    if "PersNr_Plan" in df.columns:
        df["PersNr_Plan"] = df["PersNr_Plan"].astype(str)
    if "PersNr" in mitarbeiter.columns:
        mitarbeiter["PersNr"] = mitarbeiter["PersNr"].astype(str)
    
    # Left Merge Planstellen -> Mitarbeiter
    df = df.merge(
        mitarbeiter,
        left_on="PersNr_Plan",
        right_on="PersNr",
        how="left",
        suffixes=("", "_ma")
    )
    
    # Safe Parse Austritt (using safe parsing logic if available, or coerce)
    if "Austritt" in df.columns:
        # Inline safe logic simplified
        df["Austritt"] = pd.to_datetime(df["Austritt"], errors="coerce")

    # Merge Ausbildung
    if "Personalnummer" in ausbildung.columns:
        ausbildung["Personalnummer"] = ausbildung["Personalnummer"].astype(str)
        
    # Check if BV column exists
    ausb_cols = ["Personalnummer"]
    if "BV Ausbildungsgruppentext" in ausbildung.columns:
        ausb_cols.append("BV Ausbildungsgruppentext")
    elif "Ausbildungsgruppentext" in ausbildung.columns:
         ausb_cols.append("Ausbildungsgruppentext")
         
    if len(ausb_cols) > 1:
        df = df.merge(
            ausbildung[ausb_cols],
            left_on="PersNr",
            right_on="Personalnummer",
            how="left",
            suffixes=("", "_ausb")
        )
        # Rename result col to "Ausbildung"
        target_col = ausb_cols[1]
        df = df.rename(columns={target_col: "Ausbildung"})

    # Ausbildung-Mapping
    if "Ausbildung" in df.columns:
        df["Ausbildung"] = df["Ausbildung"].astype("string")
        # EDUCATION_MAPPING defined in this file
        df["Bildungskategorie"] = df["Ausbildung"].map(EDUCATION_MAPPING)
        df["Bildungsrang"] = df["Ausbildung"].map(EDUCATION_RANKING)

    # Azubi-Flag
    if "MitarbGruppenbez." in df.columns:
        df["Ist_Azubi"] = df["MitarbGruppenbez."] == "Auszubildende"

    # ATZ Logic
    # 1. Filter current ATZ
    atz_aktuell = atz.copy()
    if not atz_aktuell.empty:
        # Ensure dates
        for col in ["Beginn", "Ende"]:
            if col in atz_aktuell.columns:
                atz_aktuell[col] = pd.to_datetime(atz_aktuell[col], errors="coerce")
        
        atz_aktuell = atz_aktuell[
            (atz_aktuell['Beginn'] <= stichtag) & 
            (atz_aktuell['Ende'] >= stichtag)
        ]
    
    if "PersNr" in df.columns:
        df["PersNr"] = df["PersNr"].astype(str)
    if "PersNr" in atz_aktuell.columns:
        atz_aktuell["PersNr"] = normalize_persnr(atz_aktuell["PersNr"]).astype(str)
    
    # Merge ATZ Phase
    cols_atz = ["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag", "Modell"]
    cols_atz = [c for c in cols_atz if c in atz_aktuell.columns]
    
    if not atz_aktuell.empty:
        df = df.merge(
            atz_aktuell[cols_atz],
            on="PersNr",
            how="left",
            suffixes=("", "_atz")
        )

    # Derived Fields
    df["Is_Vacant"] = df["PersNr"].isna()
    if "PersNr_Plan" in df.columns:
        df["Personalnummer"] = df["PersNr_Plan"]

    # FTE
    # Ensure numeric
    for col in ["BsGrd", "Sollarbeitszeit"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["FTE_person"] = df["BsGrd"].fillna(0) / 100.0
    df["Soll_FTE"] = df["Sollarbeitszeit"].fillna(39.0) / 39.0
    df["FTE_assigned"] = df["FTE_person"] * df["Soll_FTE"]

    # Clean Step
    if "St" in df.columns:
        def clean_step(step):
            if pd.isna(step): return 4
            s = str(step).strip().replace("+", "").replace("-", "")
            try: return int(s)
            except: return 4
        df["St"] = df["St"].apply(clean_step)
        
    # Derive ATZ fields (function defined in this file)
    atz_derived = derive_atz_fields(atz)
    if "PersNr" in atz_derived.columns:
        atz_derived["PersNr"] = normalize_persnr(atz_derived["PersNr"]).astype(str)
        
    df = df.merge(atz_derived, on="PersNr", how="left")
    
    # ATZ Flags for MAK
    atz_fr_persnr = set()
    if "Phase" in atz_aktuell.columns:
        atz_fr_persnr = set(atz_aktuell[atz_aktuell['Phase'] == 'FR']['PersNr'])
    
    df['ist_atz_fr'] = df['PersNr'].isin(atz_fr_persnr)
    
    # Cost
    df["Total_Cost_Year"] = df.apply(calculate_cost_row, axis=1)
    
    # Cleanup (Optional: Keep all necessary columns)
    
    return df


def process_uploaded_data(uploaded_files: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """
    Verarbeitet hochgeladene Excel-Dateien und erstellt Snapshot/History.
    """
    # 1. Read Raw DFS
    dfs = {}
    required = ["Mitarbeiter", "Planstellen", "ATZ", "Ausbildung"]
    
    for name in required:
        if name not in uploaded_files:
            # Wenn optional, hier handhaben. Aber für Snapshot brauchen wir eigentlich alle.
            # Werfen wir Fehler, wenn essenzielle fehlen.
            pass
            
        if name in uploaded_files:
            # Read Excel from BytesIO
            dfs[name] = pd.read_excel(uploaded_files[name])
        else:
            # Create empty DF if missing? Or raise error?
            # For robustness: use empty DF
            dfs[name] = pd.DataFrame()

    # 2. Create Snapshot (ETL)
    # Check if main files are present
    if dfs["Planstellen"].empty and dfs["Mitarbeiter"].empty:
        raise ValueError("Mindestens Planstellen.xlsx oder Mitarbeiter.xlsx erforderlich.")

    snapshot_df = create_combined_snapshot(
        dfs["Mitarbeiter"],
        dfs["Planstellen"],
        dfs["ATZ"],
        dfs["Ausbildung"],
        stichtag=pd.Timestamp.today()
    )
    
    # 3. Create Org Structure
    org_df = create_org_structure(dfs["Planstellen"])
    
    # 4. Create Dummy History
    history_df = generate_history_from_snapshot(snapshot_df)
    
    return {
        "snapshot_detail": snapshot_df,
        "history_cube": history_df,
        "org_structure": org_df
    }


def generate_history_from_snapshot(snapshot_df: pd.DataFrame, start_date="2024-01-01", end_date="2026-01-18") -> pd.DataFrame:
    """Generiert History Cube aus Snapshot (monatliche Zeitreihen)."""
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
    """Erstellt Org-Struktur DataFrame aus Planstellen."""
    planstellen_clean = clean_planstellen(planstellen)
    org_df = planstellen_clean[["Kürzel OrgEinheit", "OrgEinheitNr", "Organisationseinheit"]].drop_duplicates()
    org_df = org_df.sort_values("Kürzel OrgEinheit")
    return org_df


# =============================================================================
# VALIDIERUNGSFUNKTIONEN
# =============================================================================

def validate_snapshot(df: pd.DataFrame) -> Dict[str, bool]:
    """Validiert einen Snapshot DataFrame auf bekannte Probleme."""
    results = {}
    if 'PersNr' in df.columns:
        pers_counts = df[df['PersNr'].notna()].groupby('PersNr').size()
        max_dups = pers_counts.max() if len(pers_counts) > 0 else 0
        results["max_planstellen_pro_person"] = max_dups
        results["keine_atz_duplikate"] = max_dups <= 3
    if 'Kürzel OrgEinheit' in df.columns and 'Sollarbeitszeit' in df.columns:
        azubi_soll = df[df['Kürzel OrgEinheit'] == '9910']['Sollarbeitszeit']
        results["azubi_sollarbeitszeit_ok"] = (azubi_soll >= 38.0).all() if len(azubi_soll) > 0 else True
    results["mak_vorhanden"] = "MAK" in df.columns
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
