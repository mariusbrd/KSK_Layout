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
import json

# Import settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (
    DATA_PATH, DEFAULT_COHORTS, BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR, BASE_DIR,
    EDUCATION_GROUPS, DEFAULT_AZUBI_SALARIES, EXCLUSION_ORG_UNITS,
)
from utils.settings_loader import get_setting
from utils.cache_utils import (
    coerce_file_bytes,
    ensure_file_like,
    deserialize_uploaded_files,
    get_cache_version,
    get_file_signature,
    serialize_uploaded_files,
    stable_json_dumps,
)
from dataloader.cluster_manager import (
    apply_clusters_to_snapshot,
    apply_clusters_to_snapshot_from_source,
    load_cluster_mappings_from_source,
)
from dataloader.cluster_resolver import (
    deserialize_active_cluster_source,
    get_active_cluster_source,
    serialize_active_cluster_source,
    store_active_cluster_source_in_session,
)
from dataloader.mak_allocation import apply_person_mak_allocation
from kpi_reference import get_current_stichtag

ID_PAD_LENGTH = 6


from abgaenge.schemas import normalize_persnr


@st.cache_data
def load_hr_data(
    filepath: Optional[str] = None,
    auto_generate: bool = True,
    source_signature: Optional[Tuple[str, int, int]] = None,
) -> Dict[str, pd.DataFrame]:
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

    # Columns that MUST be present for correct operation.
    # If any are missing the file is stale and must be regenerated.
    _REQUIRED_COLS = {"BsGrd", "ist_atz_fr", "MitarbGruppenbez.", "Phase", "Ist_Azubi"}

    needs_generate = not os.path.exists(filepath)

    # Staleness check: detect old Excel files missing critical columns
    if not needs_generate and auto_generate:
        try:
            probe = pd.read_excel(filepath, sheet_name="snapshot_detail", nrows=0)
            missing = _REQUIRED_COLS - set(probe.columns)
            if missing:
                needs_generate = True
                message = (
                    f"ℹ️ Synthetische Daten veraltet "
                    f"(fehlende Spalten: {', '.join(sorted(missing))}). "
                    f"Regeneriere…"
                )
                st.session_state["data_status_message"] = message
                st.session_state["data_status_level"] = "info"
                if not st.session_state.get("suppress_data_status_messages", False):
                    st.info(message)
        except Exception:
            needs_generate = True

    if needs_generate:
        if auto_generate:
            try:
                from dataloader.synthetic import generate_synthetic_data

                data_dict = generate_synthetic_data()
                if not st.session_state.get("suppress_data_status_messages", False):
                    st.success("Testdaten erfolgreich generiert.")
                return data_dict

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
def enrich_snapshot_data(
    df: pd.DataFrame,
    stichtag: Optional[pd.Timestamp] = None,
    cohort_definitions: Optional[Dict[str, Tuple[int, int]]] = None,
) -> pd.DataFrame:
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
    if "GebDatum" in df.columns:
        df["Alter_Jahre"] = (stichtag - pd.to_datetime(df["GebDatum"], errors="coerce")).dt.days / 365.25
        df["Alter_Jahre"] = df["Alter_Jahre"].fillna(0)
        df["Alter"] = df["Alter_Jahre"].astype(int)
    else:
        df["Alter_Jahre"] = 0.0
        df["Alter"] = 0

    # Betriebszugehörigkeit in Jahren (zum STICHTAG)
    if "Eintritt" in df.columns:
        df["Betriebszugehörigkeit_Jahre"] = (
            stichtag - pd.to_datetime(df["Eintritt"], errors="coerce")
        ).dt.days / 365.25
        df["Betriebszugehörigkeit_Jahre"] = df["Betriebszugehörigkeit_Jahre"].fillna(0)
    else:
        df["Betriebszugehörigkeit_Jahre"] = 0.0

    # Alterskohorten (aus session_state, falls verfügbar)
    if cohort_definitions is not None:
        cohorts = cohort_definitions
    elif "cohort_definitions" in st.session_state:
        cohorts = st.session_state["cohort_definitions"]
    else:
        cohorts = DEFAULT_COHORTS

    df["Alterskohorte"] = df["Alter"].apply(
        lambda age: assign_age_cohort(age, cohorts)
    )

    # Geschlecht vereinfachen
    if "Text Gsch" in df.columns:
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
    if "Soll_FTE" in df.columns and "FTE_assigned" in df.columns:
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


def _zero_out_azubi_mak(df: pd.DataFrame) -> pd.DataFrame:
    """
    Sets MAK to 0.0 for Azubis (Single Source of Truth).
    Preserves original values in *_raw columns for auditing.
    
    Identifies Azubis by:
    - TrfGr containing "TVA" (TVÖD Azubi)
    - Jobfamily containing "Azubi" or "Ausbildung"
    """
    if df.empty: return df
    
    df_out = df.copy()
    
    # Identify Azubis
    if "TrfGr" not in df_out.columns: df_out["TrfGr"] = ""
    if "Jobfamily" not in df_out.columns: df_out["Jobfamily"] = ""
    
    mask_azubi = (
        (df_out["TrfGr"].astype(str).str.contains("TVA", case=False, na=False)) |
        (df_out["Jobfamily"].astype(str).str.contains("Azubi|Ausbildung", case=False, na=False))
    )
    
    # MAK_Calculated
    if "MAK_Calculated" in df_out.columns:
        if "MAK_Calculated_raw" not in df_out.columns:
            df_out["MAK_Calculated_raw"] = df_out["MAK_Calculated"]
        df_out.loc[mask_azubi, "MAK_Calculated"] = 0.0
        
    # MAK (Capitalized - often from enrich_snapshot)
    if "MAK" in df_out.columns:
        if "MAK_raw" not in df_out.columns:
            df_out["MAK_raw"] = df_out["MAK"]
        df_out.loc[mask_azubi, "MAK"] = 0.0

    # mak (lowercase - used in forecast engine)
    if "mak" in df_out.columns:
        if "mak_raw" not in df_out.columns:
            df_out["mak_raw"] = df_out["mak"]
        df_out.loc[mask_azubi, "mak"] = 0.0
        
    # BsGrd (Occupancy Rate) - Root cause for MAK usually
    if "BsGrd" in df_out.columns:
         if "BsGrd_raw" not in df_out.columns:
             df_out["BsGrd_raw"] = df_out["BsGrd"]
         df_out.loc[mask_azubi, "BsGrd"] = 0
         
    return df_out


def apply_exclusions(df: pd.DataFrame, exclusions: Dict[str, Any]) -> pd.DataFrame:
    """
    Wendet Gruppen-Ausschlüsse auf den Snapshot an.
    Exkludierte Personen werden als Vakanz markiert (Ist entfernt, Soll bleibt).

    ACHTUNG (v2.1):
    - Ist-Werte (PersNr, MAK, BsGrd) werden genullt/leert.
    - Soll-Werte (Soll_FTE, Sollarbeitszeit) bleiben erhalten (Bedarf).

    v2.2: Is_Excluded-Flag und Exclusion_Group werden ergaenzt (immer, auch ohne
    aktive Exklusion). Echte Vakanzen (Is_Vacant=True aus Snapshot, nicht durch
    diese Funktion gesetzt) erhalten Is_Excluded=False.

    Criteria (UI-konform):
    1. Vorstand: MitarbGruppenbez. == "Vorstand"
    2. Ruhendes BV: Status kundenindividuell == "Ruhendes Beschäftigungsverhältnis"
    3. PA-Bereiche: Kürzel OrgEinheit in org_units OR starts with "99" (if 99XX selected)
    4. Spezielle Gruppen: via build_group_masks()
    """
    if df.empty: return df

    df_out = df.copy()
    exclusion_mask = pd.Series(False, index=df_out.index)
    _sub_masks: list[tuple[str, pd.Series]] = []

    _EXCL_GROUP_LABELS = {
        "ausbildung_nachwuchs":                  "Azubi / Nachwuchs",
        "jobfamily_validation_special_positions": "Jobfamily Validierung",
        "sollarbeitszeit_001_positions":          "Sollarbeitszeit ≤ 0,01",
    }

    # 1. Vorstand
    if exclusions.get("vorstand"):
        if "MitarbGruppenbez." in df_out.columns:
            _m = df_out["MitarbGruppenbez."] == "Vorstand"
            exclusion_mask |= _m
            _sub_masks.append(("Vorstand", _m))

    # 2. Ruhendes BV (via Status-Feld — identisch mit build_group_masks() in exclusion_groups.py)
    # Fruehhere Version nutzte OE=="9900"; das wich von build_group_masks() ab und schloss
    # Personen mit Status "Ruhendes BV" in anderen OEs nicht aus.
    # OE 9900 wird weiterhin separat ueber org_units erfasst (PA Ruhendes BV).
    if exclusions.get("ruhend_bv"):
        if "Status kundenindividuell" in df_out.columns:
            _m = (
                df_out["Status kundenindividuell"].astype(str).str.strip()
                == "Ruhendes Beschäftigungsverhältnis"
            )
            exclusion_mask |= _m
            _sub_masks.append(("Ruhendes BV", _m))

    # 3. Spezifische PA-Bereiche (und 99XX-Logik)
    ex_org_units = exclusions.get("org_units", [])
    if ex_org_units and "Kürzel OrgEinheit" in df_out.columns:
        s_ou = df_out["Kürzel OrgEinheit"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        _m_ou = s_ou.isin(ex_org_units)
        # 99XX Spezialregel: Alles was mit 99 beginnt, aber NICHT bereits explizit gelistete Codes
        # (konsistent mit build_group_masks() in exclusion_groups.py)
        if "99XX" in ex_org_units:
            explicit_pa_codes = {c for c in ex_org_units if c != "99XX"}
            _m_ou = _m_ou | (s_ou.str.startswith("99") & ~s_ou.isin(explicit_pa_codes))
        exclusion_mask |= _m_ou
        _sub_masks.append(("PA-Bereich", _m_ou))

    special_groups = exclusions.get("special_groups") or []
    if special_groups:
        from utils.exclusion_groups import build_group_masks

        group_masks = build_group_masks(df_out)
        for group_key in special_groups:
            if group_key in group_masks:
                _m = group_masks[group_key]
                exclusion_mask |= _m
                _sub_masks.append((_EXCL_GROUP_LABELS.get(group_key, group_key), _m))

    # Exklusionsflag — immer initialisieren (auch wenn keine Exklusion aktiv)
    df_out["Is_Excluded"] = False
    df_out["Exclusion_Group"] = ""

    # --- Anwendung der Exklusion (Vacating) ---
    if exclusion_mask.any():
        try:
            import streamlit as st
            st.session_state["last_exclusion_count"] = int(exclusion_mask.sum())
        except Exception:
            pass

        # Felder, die zur PERSON gehören (Ist-Identifier)
        person_fields = [
            "Personalnummer", "PersNr", "Personalnachname", "Personalvorname",
            "Name", "Vorname", "Nachname", "GebDatum", "Eintritt", "Austritt",
            "Alter", "Alter_Jahre", "Ist_Azubi",
            "MAK_raw", "mak_raw", "MAK_Calculated_raw", "BsGrd_raw"
        ]

        # Kennzahlen, die das IST-Volumen beschreiben.
        # MAK_Reporting und EUR_Reporting entstehen in apply_person_mak_allocation(),
        # das VOR apply_exclusions() laeuft. Sie muessen daher hier explizit genullt
        # werden, damit ausgeschlossene Zeilen nicht in IST_MAK / IST_EUR eingehen.
        ist_metrics = [
            "MAK", "mak", "MAK_Calculated", "BsGrd",
            "FTE_person", "FTE_assigned", "Total_Cost_Year",
            "MAK_Reporting", "EUR_Reporting",
            "Allocation_Weight", "Personen_MAK",
        ]

        # Markiere als Vakanz
        if "Is_Vacant" not in df_out.columns:
            df_out["Is_Vacant"] = False

        df_out["Is_Vacant"] = df_out["Is_Vacant"].astype("boolean")
        df_out.loc[exclusion_mask, "Is_Vacant"] = True

        # Is_Excluded und Exclusion_Group setzen (erste Gruppe gewinnt bei Mehrfachzuordnung)
        df_out.loc[exclusion_mask, "Is_Excluded"] = True
        for _grp_label, _grp_mask in _sub_masks:
            _new = _grp_mask & df_out["Exclusion_Group"].eq("")
            df_out.loc[_new, "Exclusion_Group"] = _grp_label

        # Leere Personendaten
        existing_person_fields = [f for f in person_fields if f in df_out.columns]
        # Bool-Spalten (z.B. Ist_Azubi) muessen vor der pd.NA-Zuweisung auf
        # nullable "boolean" gecastet werden — sonst FutureWarning "incompatible dtype".
        for f in existing_person_fields:
            if df_out[f].dtype == bool:
                df_out[f] = df_out[f].astype("boolean")
        df_out.loc[exclusion_mask, existing_person_fields] = pd.NA

        # Nulle Ist-Volumen
        existing_ist_metrics = [f for f in ist_metrics if f in df_out.columns]
        df_out.loc[exclusion_mask, existing_ist_metrics] = 0.0

        # HINWEIS: Soll_FTE und Sollarbeitszeit werden NICHT angefasst.

    return df_out


def _get_data_prep_context() -> Dict[str, Any]:
    return {
        "stichtag": str(get_current_stichtag()),
        "include_future_hires": bool(get_setting("include_future_hires", False)),
        "occupied_placeholder_soll_correction": bool(get_setting("occupied_placeholder_soll_correction", False)),
        "exclusions": get_setting("exclusions", {}),
        "cohort_definitions": st.session_state.get("cohort_definitions", DEFAULT_COHORTS),
        "employer_cost_factor": float(st.session_state.get("employer_cost_factor", EMPLOYER_COST_FACTOR)),
        "azubi_salaries": st.session_state.get("azubi_salaries", DEFAULT_AZUBI_SALARIES),
        "vorstand_jahresgehalt": float(st.session_state.get("vorstand_jahresgehalt", 200000.0)),
        "cache_version": get_cache_version("data_prep"),
    }


def _resolve_loader_cluster_source(
    uploaded_files: Optional[Dict[str, Any]] = None,
):
    active_cluster_source = get_active_cluster_source(session_state=st.session_state)
    cluster_source_bytes = None
    if active_cluster_source.subtype == "ui_upload.session":
        debug_meta = getattr(active_cluster_source, "debug_meta", {}) or {}
        cluster_source_bytes = coerce_file_bytes(debug_meta.get("source_bytes"))
    store_active_cluster_source_in_session(st.session_state, active_cluster_source)
    return active_cluster_source, cluster_source_bytes


def _cluster_source_payload_json(active_cluster_source, cluster_source_bytes: Optional[bytes]) -> str:
    payload = serialize_active_cluster_source(active_cluster_source)
    if cluster_source_bytes is not None:
        payload.setdefault("debug_meta", {})
        payload["debug_meta"]["source_bytes_present"] = True
    return stable_json_dumps(payload)


def _build_cluster_summary_fields(active_cluster_source) -> Dict[str, Any]:
    return {
        "active_cluster_source": serialize_active_cluster_source(active_cluster_source),
        "active_cluster_source_signature": active_cluster_source.source_signature,
        "active_cluster_mode": active_cluster_source.mode,
        "active_cluster_subtype": active_cluster_source.subtype,
        "active_cluster_status": active_cluster_source.status,
        "active_cluster_display_label": active_cluster_source.display_label,
    }


@st.cache_data
def _load_tvoed_lookup_cached(
    uploaded_tvoed_bytes: Optional[bytes],
    tvoed_file_signature: Optional[Tuple[str, int, int]],
) -> Dict[Tuple[str, int], float]:
    from dataloader.tvoed_loader import load_tvoed_table

    if uploaded_tvoed_bytes:
        return load_tvoed_table(ensure_file_like(uploaded_tvoed_bytes))
    if tvoed_file_signature and os.path.exists(TVOED_FILE):
        return load_tvoed_table(TVOED_FILE)
    return {}


def _apply_jobfamilies(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    from dataloader.jobfamily_matcher import assign_jobfamilies, load_jobfamily_definitions
    from dataloader.jobfamily_service import normalize_jobfamily_column

    try:
        definitions = load_jobfamily_definitions()
        return normalize_jobfamily_column(assign_jobfamilies(snapshot_df, definitions))
    except Exception:
        return normalize_jobfamily_column(snapshot_df)


@st.cache_data
def _load_and_prepare_data_cached(
    use_original: bool,
    uploaded_payload: Tuple[Tuple[str, bytes], ...],
    context_json: str,
    cluster_source_signature: Optional[str],
    cluster_source_payload_json: str,
    cluster_source_bytes: Optional[bytes],
    original_file_signatures: Tuple[Tuple[str, Optional[Tuple[str, int, int]]], ...],
    synthetic_file_signature: Optional[Tuple[str, int, int]],
    tvoed_file_signature: Optional[Tuple[str, int, int]],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict, Dict, Optional[int]]:
    context = json.loads(context_json)
    current_stichtag = pd.to_datetime(context["stichtag"])
    uploaded_files = deserialize_uploaded_files(uploaded_payload)
    active_cluster_source = deserialize_active_cluster_source(
        json.loads(cluster_source_payload_json),
        source_bytes=cluster_source_bytes,
    )
    cluster_mapping_bundle = load_cluster_mappings_from_source(active_cluster_source)
    tvoed_lookup = _load_tvoed_lookup_cached(
        uploaded_payload and dict(uploaded_payload).get("TVÖD"),
        tvoed_file_signature,
    )
    future_hires_count = None

    if uploaded_files:
        data = process_uploaded_data(uploaded_files)
        snapshot_df = data["snapshot_detail"]
        history_df = data["history_cube"]
        org_df = data["org_structure"]

        snapshot_df = enrich_snapshot_data(
            snapshot_df,
            stichtag=current_stichtag,
            cohort_definitions=context["cohort_definitions"],
        )
        snapshot_df = _apply_jobfamilies(snapshot_df)
        snapshot_df = apply_clusters_to_snapshot_from_source(
            snapshot_df,
            active_cluster_source,
            mapping_bundle=cluster_mapping_bundle,
        )
        snapshot_df = _zero_out_azubi_mak(snapshot_df)
        snapshot_df = apply_exclusions(snapshot_df, context["exclusions"])
        snapshot_df = apply_person_mak_allocation(snapshot_df)

        summary = get_data_summary(snapshot_df)
        summary["data_source_type"] = "Eigene Daten (Upload)"
        summary.update(_build_cluster_summary_fields(active_cluster_source))
        return snapshot_df, history_df, org_df, summary, tvoed_lookup, future_hires_count

    if use_original:
        missing_required = [name for name, path in ORIGINAL_FILES.items() if not os.path.exists(path)]
        if not missing_required:
            original = load_original_data(file_signatures=original_file_signatures)
            if "Eintritt" in original["mitarbeiter"].columns:
                eintritt = pd.to_datetime(original["mitarbeiter"]["Eintritt"], errors="coerce")
                future_hires_count = int((eintritt > current_stichtag).sum())

            snapshot_df = combine_to_snapshot(
                original["mitarbeiter"],
                original["planstellen"],
                original["atz"],
                original["ausbildung"],
                stichtag=current_stichtag,
                tvoed_lookup=tvoed_lookup,
                include_future_hires=context["include_future_hires"],
                employer_factor=context["employer_cost_factor"],
                azubi_salaries=context["azubi_salaries"],
                vorstand_salary=context["vorstand_jahresgehalt"],
                occupied_placeholder_soll_correction=context["occupied_placeholder_soll_correction"],
            )
            snapshot_df = enrich_snapshot_data(
                snapshot_df,
                stichtag=current_stichtag,
                cohort_definitions=context["cohort_definitions"],
            )
            snapshot_df = _apply_jobfamilies(snapshot_df)
            snapshot_df = apply_clusters_to_snapshot_from_source(
                snapshot_df,
                active_cluster_source,
                mapping_bundle=cluster_mapping_bundle,
            )
            snapshot_df = _zero_out_azubi_mak(snapshot_df)
            snapshot_df = apply_exclusions(snapshot_df, context["exclusions"])
            snapshot_df = apply_person_mak_allocation(snapshot_df)

            history_df = generate_history_from_snapshot(snapshot_df)
            org_df = create_org_structure(original["planstellen"])
            summary = get_data_summary(snapshot_df)
            summary["data_source_type"] = "Original-Daten"
            summary.update(_build_cluster_summary_fields(active_cluster_source))
            return snapshot_df, history_df, org_df, summary, tvoed_lookup, future_hires_count

    data = load_hr_data(source_signature=synthetic_file_signature)
    snapshot_df = enrich_snapshot_data(
        data["snapshot_detail"],
        stichtag=current_stichtag,
        cohort_definitions=context["cohort_definitions"],
    )
    snapshot_df = _apply_jobfamilies(snapshot_df)
    snapshot_df = apply_clusters_to_snapshot_from_source(
        snapshot_df,
        active_cluster_source,
        mapping_bundle=cluster_mapping_bundle,
    )
    snapshot_df = _zero_out_azubi_mak(snapshot_df)
    snapshot_df = apply_exclusions(snapshot_df, context["exclusions"])
    snapshot_df = apply_person_mak_allocation(snapshot_df)

    summary = get_data_summary(snapshot_df)
    summary["data_source_type"] = "Synthetische Testdaten"
    summary.update(_build_cluster_summary_fields(active_cluster_source))
    return snapshot_df, data["history_cube"], data["org_structure"], summary, tvoed_lookup, future_hires_count


def load_and_prepare_data(
    use_original: bool = True,
    uploaded_files: Optional[Dict[str, Any]] = None,
    show_status_messages: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    Kompletter Daten-Lade- und Aufbereitungsprozess.

    Args:
        use_original: Wenn True, versucht Original-Daten zu laden (default: True)
        uploaded_files: Optionales Dict mit Upload-Files (Mitarbeiter, Planstellen, etc.)
        show_status_messages: Rendert Statusmeldungen im Main-Bereich, falls True

    Returns:
        Tuple aus (snapshot_df, history_df, org_df, summary)
    """
    if uploaded_files is None and "global_uploads" in st.session_state:
        uploaded_files = st.session_state["global_uploads"]

    uploaded_payload = serialize_uploaded_files(uploaded_files)
    active_cluster_source, cluster_source_bytes = _resolve_loader_cluster_source(uploaded_files)
    context_json = stable_json_dumps(_get_data_prep_context())
    cluster_source_signature = active_cluster_source.source_signature
    cluster_source_payload_json = _cluster_source_payload_json(active_cluster_source, cluster_source_bytes)
    original_file_signatures = tuple((name, get_file_signature(path)) for name, path in sorted(ORIGINAL_FILES.items()))
    synthetic_file_signature = get_file_signature(DATA_PATH)
    tvoed_file_signature = get_file_signature(TVOED_FILE)
    missing_required = [name for name, path in ORIGINAL_FILES.items() if not os.path.exists(path)]
    st.session_state["suppress_data_status_messages"] = not show_status_messages

    def _set_data_status(message: str | None, level: str = "info") -> None:
        if message:
            st.session_state["data_status_message"] = message
            st.session_state["data_status_level"] = level
        else:
            st.session_state.pop("data_status_message", None)
            st.session_state.pop("data_status_level", None)

    try:
        snapshot_df, history_df, org_df, summary, tvoed_lookup, future_hires_count = _load_and_prepare_data_cached(
            use_original=use_original,
            uploaded_payload=uploaded_payload,
            context_json=context_json,
            cluster_source_signature=cluster_source_signature,
            cluster_source_payload_json=cluster_source_payload_json,
            cluster_source_bytes=cluster_source_bytes,
            original_file_signatures=original_file_signatures,
            synthetic_file_signature=synthetic_file_signature,
            tvoed_file_signature=tvoed_file_signature,
        )
        st.session_state["tvoed_lookup"] = tvoed_lookup
        st.session_state["tvoed_available"] = len(tvoed_lookup) > 0
        if future_hires_count is not None:
            st.session_state["stats_future_hires"] = int(future_hires_count)
            _set_data_status(None)
        elif use_original and not uploaded_payload and missing_required:
            message = (
                "ℹ️ Original-Daten unvollständig oder nicht gefunden. "
                "Fehlend: " + ", ".join(missing_required) + ". "
                "Verwende synthetische Testdaten."
            )
            _set_data_status(message, "info")
            if show_status_messages:
                st.info(message)
        else:
            _set_data_status(None)
        return snapshot_df, history_df, org_df, summary
    except FileNotFoundError as e:
        message = (
            "ℹ️ Original-Daten unvollständig oder nicht gefunden. "
            "Fehlend: " + ", ".join(str(e).splitlines()[1:]) + ". "
            "Verwende synthetische Testdaten."
        )
        _set_data_status(message, "info")
        if show_status_messages:
            st.info(message)
    except Exception as e:
        if uploaded_payload:
            _set_data_status(None)
            st.error(f"Fehler bei der Verarbeitung der hochgeladenen Dateien: {str(e)}")
        elif use_original:
            message = f"⚠️ Fehler beim Laden der Original-Daten: {str(e)}\nVerwende synthetische Testdaten."
            _set_data_status(message, "warning")
            if show_status_messages:
                st.warning(message)

    snapshot_df, history_df, org_df, summary, tvoed_lookup, _ = _load_and_prepare_data_cached(
        use_original=False,
        uploaded_payload=tuple(),
        context_json=context_json,
        cluster_source_signature=cluster_source_signature,
        cluster_source_payload_json=cluster_source_payload_json,
        cluster_source_bytes=cluster_source_bytes,
        original_file_signatures=original_file_signatures,
        synthetic_file_signature=synthetic_file_signature,
        tvoed_file_signature=tvoed_file_signature,
    )
    st.session_state["tvoed_lookup"] = tvoed_lookup
    st.session_state["tvoed_available"] = len(tvoed_lookup) > 0
    return snapshot_df, history_df, org_df, summary



# =============================================================================
# KONSTANTEN & SETUP (aus original_loader.py)
# =============================================================================

from config.settings import DEFAULT_COHORTS, BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR

# BASE_DIR imported from config.settings
ORIGINAL_DATA_DIR = os.path.join(BASE_DIR, "..", "Original-Daten")

ORIGINAL_FILES = {
    "mitarbeiter": os.path.join(ORIGINAL_DATA_DIR, "Mitarbeiter.xlsx"),
    "planstellen": os.path.join(ORIGINAL_DATA_DIR, "Planstellen.XLSX"),
    "atz": os.path.join(ORIGINAL_DATA_DIR, "ATZ.xlsx"),
    "ausbildung": os.path.join(ORIGINAL_DATA_DIR, "Ausbildung.xlsx"),
}

# Robust TVOED File Path detection (Handles Umlaut variations)
def get_tvoed_path(data_dir):
    possible_names = ["TVÖD.xlsx", "TVOED.xlsx", "TVÖD.XLSX", "TVOED.XLSX", "TVOED.XLS", "TVOE.xlsx"]
    for name in possible_names:
        path = os.path.join(data_dir, name)
        if os.path.exists(path):
            return path
    
    # Fallback search
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            if f.upper().startswith("TVO") and f.lower().endswith(".xlsx"):
                return os.path.join(data_dir, f)
    
    return os.path.join(data_dir, "TVÖD.xlsx") # Default

TVOED_FILE = get_tvoed_path(ORIGINAL_DATA_DIR)

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

EDUCATION_NORMALIZATION_MAP = {
    "bankfachwirt/-in": "Bankfachwirt",
    "sparkassen/bankfachwirt": "Bankfachwirt",
    "spk/bankfachwirt": "Bankfachwirt",
    "bankbetriebswirt/-in": "Bankbetriebswirt",
    "sparkassen/bankbetriebswirt": "Bankbetriebswirt",
    "spk/bankbetriebswirt": "Bankbetriebswirt",
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


def normalize_education_value(value: Any) -> Any:
    """Normalisiert Ausbildungslabels auf den zentralen Kanon aus settings.py."""
    if pd.isna(value):
        return pd.NA

    text = str(value).strip()
    if not text or text.lower() == "nan":
        return pd.NA

    if text in EDUCATION_GROUPS:
        return text

    normalized = EDUCATION_NORMALIZATION_MAP.get(text.lower())
    if normalized:
        return normalized

    return text


def normalize_education_series(series: pd.Series) -> pd.Series:
    """Wendet die Ausbildungsnormalisierung vektorisiert auf eine Series an."""
    normalized = series.map(normalize_education_value)
    return normalized.astype("string")


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
    """Bereinigt Stufen-Werte wie '2+', '6.0' zu 2, 6."""
    if pd.isna(step):
        return 4
    step_str = str(step).strip().replace("+", "").replace("-", "")
    try:
        return int(float(step_str))
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
def load_original_data(
    file_signatures: Optional[Tuple[Tuple[str, Optional[Tuple[str, int, int]]], ...]] = None,
) -> Dict[str, pd.DataFrame]:
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



def calculate_cost_vectorized(
    df: pd.DataFrame,
    tvoed_lookup: Dict,
    employer_factor: Optional[float] = None,
    azubi_salaries: Optional[Dict[Any, float]] = None,
    vorstand_salary: Optional[float] = None,
) -> pd.DataFrame:
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
    df_out["Annual_Final"] = df_out["Annual_L"].fillna(fallback_annual).infer_objects(copy=False)
    
    # 5. Helper: Special Salaries
    # Azubi (Progressiv)
    from config.settings import DEFAULT_AZUBI_SALARIES
    if azubi_salaries is None:
        azubi_salaries = st.session_state.get("azubi_salaries", DEFAULT_AZUBI_SALARIES)
    azubi_salaries = {int(k): float(v) for k, v in dict(azubi_salaries).items()}
    
    mask_azubi_base = df_out["TrfGr_Join"].isin(["TVAÖD", "TVÖAD", "TVAOD"])
    for year, salary in azubi_salaries.items():
        mask_year = mask_azubi_base & (df_out["St_Join"] == year)
        df_out.loc[mask_year, "Annual_Final"] = salary
    
    # Fallback for Azubis without valid year (St)
    mask_azubi_fallback = mask_azubi_base & (~df_out["St_Join"].isin(azubi_salaries.keys()))
    df_out.loc[mask_azubi_fallback, "Annual_Final"] = azubi_salaries.get(1, 14400.0)
    
    # Vorstand
    if vorstand_salary is None:
        vorstand_salary = st.session_state.get("vorstand_jahresgehalt", 200000.0)
    mask_vorstand = df_out["TrfGr_Join"] == "1"
    df_out.loc[mask_vorstand, "Annual_Final"] = vorstand_salary
    
    # 6. Final Calculation
    if employer_factor is None:
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
    
    # Baseline – guard against missing BsGrd (e.g. incomplete uploads)
    # Konservativ: ohne BsGrd wird MAK = 0.0 gesetzt (nicht 1.0),
    # um künstliche IST-MAK-Inflation zu verhindern (Bug K2 aus MAK-Dossier).
    if "BsGrd" in df_out.columns:
        df_out["MAK_Calculated"] = df_out["BsGrd"].fillna(0) / 100.0
    else:
        df_out["MAK_Calculated"] = 0.0
    
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


def _build_person_capacity_source(mitarbeiter: pd.DataFrame) -> pd.DataFrame:
    """Build one stable person MAK source row before the Planstellen join."""
    cols = ["PersNr", "BsGrd_Source", "Personen_MAK_Source", "bsgrd_lineage_flag"]
    if mitarbeiter is None or mitarbeiter.empty or "PersNr" not in mitarbeiter.columns:
        return pd.DataFrame(columns=cols)

    work = mitarbeiter.copy()
    work["PersNr"] = normalize_persnr(work["PersNr"])
    if "BsGrd" not in work.columns:
        out = work[["PersNr"]].drop_duplicates().copy()
        out["BsGrd_Source"] = pd.NA
        out["Personen_MAK_Source"] = pd.NA
        out["bsgrd_lineage_flag"] = "source_bsgrd_missing_fallback_used"
        return out[cols]

    work["BsGrd_Source"] = pd.to_numeric(work["BsGrd"], errors="coerce")
    grouped = work.groupby("PersNr", dropna=False)["BsGrd_Source"]
    out = grouped.first().reset_index()
    nunique = grouped.nunique(dropna=True).reset_index(name="_bsgrd_nunique")
    out = out.merge(nunique, on="PersNr", how="left")
    out["Personen_MAK_Source"] = out["BsGrd_Source"] / 100.0
    out["bsgrd_lineage_flag"] = "ok_source_bsgrd_used"
    out.loc[out["BsGrd_Source"].isna(), "bsgrd_lineage_flag"] = "source_bsgrd_missing_fallback_used"
    out.loc[out["_bsgrd_nunique"].fillna(0).gt(1), "bsgrd_lineage_flag"] = "person_capacity_conflict"
    return out.drop(columns=["_bsgrd_nunique"])[cols]


def _mark_bsgrd_lineage(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "BsGrd_Source" not in out.columns:
        out["BsGrd_Source"] = pd.NA
    if "Personen_MAK_Source" not in out.columns:
        out["Personen_MAK_Source"] = pd.NA
    if "bsgrd_lineage_flag" not in out.columns:
        out["bsgrd_lineage_flag"] = "source_bsgrd_missing_fallback_used"
    out["snapshot_BsGrd"] = pd.to_numeric(out["BsGrd"], errors="coerce") if "BsGrd" in out.columns else pd.NA
    source_bsgrd = pd.to_numeric(out["BsGrd_Source"], errors="coerce")
    snapshot_bsgrd = pd.to_numeric(out["snapshot_BsGrd"], errors="coerce")
    differs = source_bsgrd.notna() & snapshot_bsgrd.notna() & source_bsgrd.sub(snapshot_bsgrd).abs().gt(1e-6)
    out.loc[differs & out["bsgrd_lineage_flag"].eq("ok_source_bsgrd_used"), "bsgrd_lineage_flag"] = "snapshot_bsgrd_differs_from_source"
    gt100 = source_bsgrd.le(100) & snapshot_bsgrd.gt(100)
    out.loc[gt100 & ~out["bsgrd_lineage_flag"].eq("person_capacity_conflict"), "bsgrd_lineage_flag"] = "snapshot_bsgrd_gt_100_but_source_le_100"
    return out


def _mask_regular_occupied_placeholder_positions(df: pd.DataFrame) -> pd.Series:
    """
    True = besetzte Platzhalter-Planstelle OHNE bekannten Sonderstatus (Zielgruppe fuer die
    optionale Soll=Ist-Korrektur, siehe occupied_placeholder_soll_correction).

    Nutzt dieselben Signale, die apply_exclusions() und _zero_out_azubi_mak() bereits fuer die
    Vorstand-/Ruhend-BV-/Azubi-Erkennung verwenden, statt eine neue Klassifikation zu erfinden.
    """
    known_excl = set(EXCLUSION_ORG_UNITS.keys())
    oe = df.get("Kürzel OrgEinheit", pd.Series("", index=df.index)).astype(str).str.strip()
    is_excl_oe = oe.isin(known_excl) | oe.str.startswith("99")

    status = df.get("Status kundenindividuell", pd.Series("", index=df.index)).astype(str)
    is_ruhend = status.eq("Ruhendes Beschäftigungsverhältnis")

    gruppe = df.get("MitarbGruppenbez.", pd.Series("", index=df.index)).astype(str)
    is_vorstand = gruppe.eq("Vorstand")

    is_azubi = df.get("Ist_Azubi", pd.Series(False, index=df.index)).astype(bool)

    phase = df.get("Phase", pd.Series("", index=df.index)).astype(str).str.strip()
    is_atz_fr = phase.eq("FR")

    return ~(is_excl_oe | is_ruhend | is_vorstand | is_azubi | is_atz_fr)


def combine_to_snapshot(
    mitarbeiter,
    planstellen,
    atz,
    ausbildung,
    stichtag=None,
    tvoed_lookup=None,
    include_future_hires: Optional[bool] = None,
    employer_factor: Optional[float] = None,
    azubi_salaries: Optional[Dict[Any, float]] = None,
    vorstand_salary: Optional[float] = None,
    occupied_placeholder_soll_correction: Optional[bool] = None,
) -> pd.DataFrame:
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
    include_future = get_setting("include_future_hires", False) if include_future_hires is None else include_future_hires
    
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
    person_capacity_source = _build_person_capacity_source(mitarbeiter)

    ausbildung = ausbildung.copy()
    ausbildung["Personalnummer"] = normalize_persnr(ausbildung["Personalnummer"])
    ausbildung["BV Ausbildungsgruppentext"] = ausbildung["BV Ausbildungsgruppentext"].astype(str).str.strip()

    atz = normalize_atz(atz)
    df = clean_planstellen(planstellen)

    df = df.merge(mitarbeiter, left_on="Personalnummer", right_on="PersNr", how="left", suffixes=("", "_ma"))
    if not person_capacity_source.empty:
        df = df.merge(person_capacity_source, on="PersNr", how="left")
    df = df.merge(ausbildung[["Personalnummer", "BV Ausbildungsgruppentext"]], left_on="Personalnummer", right_on="Personalnummer", how="left", suffixes=("", "_ausb"))
    df = df.rename(columns={"BV Ausbildungsgruppentext": "Ausbildung"})

    if "Ausbildung" in df.columns:
        df["Ausbildung"] = normalize_education_series(df["Ausbildung"])
        df["Bildungskategorie"] = df["Ausbildung"].map(EDUCATION_MAPPING)
        df["Bildungsrang"] = df["Ausbildung"].map(EDUCATION_RANKING)

    if "MitarbGruppenbez." in df.columns:
        df["Ist_Azubi"] = df["MitarbGruppenbez."] == "Auszubildende"

    atz_aktuell = get_current_atz_phase(atz, stichtag)
    df = df.merge(atz_aktuell[["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag", "Modell"]], left_on="PersNr", right_on="PersNr", how="left", suffixes=("", "_atz"))
    if "Phase" in df.columns:
        df["Phase"] = df["Phase"].astype(str).str.strip()

    df["Is_Vacant"] = df["Personalnummer"].isna()
    df = _mark_bsgrd_lineage(df)
    df["FTE_person"] = df["BsGrd"].fillna(0) / 100.0
    df["Soll_FTE"] = df["Sollarbeitszeit"].fillna(0) / 39.0

    # Optionale Korrektur: besetzte Platzhalter-Planstellen (Sollarbeitszeit ≈ 0,01) ausserhalb
    # bekannter Sonderstatus-Gruppen erhalten Soll = Ist der besetzenden Person, statt auf 0
    # genullt zu werden. Standardmaessig aus — siehe Einstellungen > Sonderfaelle.
    if occupied_placeholder_soll_correction is None:
        occupied_placeholder_soll_correction = get_setting("occupied_placeholder_soll_correction", False)
    if occupied_placeholder_soll_correction:
        correction_mask = (
            ~df["Is_Vacant"]
            & (df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015)
            & _mask_regular_occupied_placeholder_positions(df)
        )
        df.loc[correction_mask, "Soll_FTE"] = df.loc[correction_mask, "FTE_person"]
        df.loc[correction_mask, "Sollarbeitszeit"] = df.loc[correction_mask, "FTE_person"] * 39.0

    # Systemartefakt: Soll_FTE ≈ 0.01 ist ein Platzhalterwert für 0
    # (Quellsystem kann Soll-MAK = 0 nicht verbuchen → wird als 0,01 geliefert)
    # Toleranzbasierter Vergleich (< 0.015) statt exakter Gleichheit, da Excel-Floats
    # leicht von 0.01 abweichen können (z.B. 0.38999.../39 = 0.009999...).
    # Untergrenze: 0.015 entspricht ~0,585h Sollarbeitszeit — kein realer Wert.
    # (Greift nur noch fuer vakante/Sonderstatus-Zeilen oder wenn obige Korrektur aus ist.)
    df.loc[(df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015), "Soll_FTE"] = 0.0
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
    df = calculate_cost_vectorized(
        df,
        tvoed_lookup,
        employer_factor=employer_factor,
        azubi_salaries=azubi_salaries,
        vorstand_salary=vorstand_salary,
    )
    
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
    stichtag: Optional[pd.Timestamp] = None,
    occupied_placeholder_soll_correction: Optional[bool] = None,
) -> pd.DataFrame:
    """
    Kombiniert die 4 Dateien zu einem Snapshot (ETL-Logik).
    """
    if stichtag is None:
        stichtag = get_current_stichtag()
    
    df = planstellen.copy()
    
    # Rename & Normalize
    if "Personalnummer" in df.columns:
        df = df.rename(columns={"Personalnummer": "PersNr_Plan"})
    
    if "PersNr_Plan" in df.columns:
        df["PersNr_Plan"] = normalize_persnr(df["PersNr_Plan"])

    mitarbeiter = mitarbeiter.copy()
    if "PersNr" in mitarbeiter.columns:
        mitarbeiter["PersNr"] = normalize_persnr(mitarbeiter["PersNr"])
    person_capacity_source = _build_person_capacity_source(mitarbeiter)

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
    if not person_capacity_source.empty:
        df = df.merge(person_capacity_source, on="PersNr", how="left")
    
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
        df["Ausbildung"] = normalize_education_series(df["Ausbildung"])
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

    df = _mark_bsgrd_lineage(df)
    df["FTE_person"] = df["BsGrd"].fillna(0) / 100.0
    df["Soll_FTE"] = df["Sollarbeitszeit"].fillna(39.0) / 39.0

    # Optionale Korrektur: besetzte Platzhalter-Planstellen (Sollarbeitszeit ≈ 0,01) ausserhalb
    # bekannter Sonderstatus-Gruppen erhalten Soll = Ist der besetzenden Person — identisch zu
    # Stelle 1 oben (combine_to_snapshot). Standardmaessig aus.
    if occupied_placeholder_soll_correction is None:
        occupied_placeholder_soll_correction = get_setting("occupied_placeholder_soll_correction", False)
    if occupied_placeholder_soll_correction:
        correction_mask = (
            ~df["Is_Vacant"]
            & (df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015)
            & _mask_regular_occupied_placeholder_positions(df)
        )
        df.loc[correction_mask, "Soll_FTE"] = df.loc[correction_mask, "FTE_person"]
        df.loc[correction_mask, "Sollarbeitszeit"] = df.loc[correction_mask, "FTE_person"] * 39.0

    # Systemartefakt: Soll_FTE ≈ 0.01 ist ein Platzhalterwert für 0
    # Toleranzbasierter Vergleich (< 0.015) — identisch zu Stelle 1 oben.
    # (Greift nur noch fuer vakante/Sonderstatus-Zeilen oder wenn obige Korrektur aus ist.)
    df.loc[(df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015), "Soll_FTE"] = 0.0
    df["FTE_assigned"] = df["FTE_person"] * df["Soll_FTE"]

    # Clean Step
    if "St" in df.columns:
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
            dfs[name] = pd.read_excel(ensure_file_like(uploaded_files[name]))
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
        stichtag=get_current_stichtag()
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
