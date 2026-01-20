"""
Jobfamily-Matching-Logik.

Pattern-basiertes Mapping von Planstellen-Bezeichnungen zu Jobfamilies.
"""

import pandas as pd
import json
import os
from fnmatch import fnmatch
from typing import Dict, List, Tuple


def load_jobfamily_definitions(filepath: str = None) -> Dict:
    """
    Lädt Jobfamily-Definitionen aus JSON-Datei.

    Args:
        filepath: Pfad zur JSON-Datei (optional)

    Returns:
        Dict mit Jobfamily-Definitionen
    """
    if filepath is None:
        # Default path
        config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        filepath = os.path.join(config_dir, "jobfamilies.json")

    if not os.path.exists(filepath):
        return {}

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobfamily_definitions(definitions: Dict, filepath: str = None):
    """
    Speichert Jobfamily-Definitionen in JSON-Datei.

    Args:
        definitions: Dict mit Jobfamily-Definitionen
        filepath: Pfad zur JSON-Datei (optional)
    """
    if filepath is None:
        # Default path
        config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        filepath = os.path.join(config_dir, "jobfamilies.json")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(definitions, f, indent=2, ensure_ascii=False)


def match_jobfamily(planstelle: str, definitions: Dict) -> str:
    """
    Matcht eine Planstellen-Bezeichnung gegen Jobfamily-Patterns.

    Args:
        planstelle: Planstellen-Bezeichnung
        definitions: Dict mit Jobfamily-Definitionen

    Returns:
        Jobfamily-Name oder "UNMAPPED"
    """
    if pd.isna(planstelle) or planstelle == "":
        return "UNMAPPED"

    planstelle_lower = planstelle.lower()

    # Prüfe alle Jobfamilies
    for family_name, family_def in definitions.items():
        patterns = family_def.get("patterns", [])

        for pattern in patterns:
            pattern_lower = pattern.lower()

            # fnmatch für Wildcard-Matching
            if fnmatch(planstelle_lower, pattern_lower):
                return family_name

    return "UNMAPPED"


def assign_jobfamilies(df: pd.DataFrame, definitions: Dict = None) -> pd.DataFrame:
    """
    Weist allen Planstellen im DataFrame Jobfamilies zu.

    Args:
        df: DataFrame mit Planstellen
        definitions: Optional Dict mit Jobfamily-Definitionen

    Returns:
        DataFrame mit neuer Spalte "Jobfamily"
    """
    if definitions is None:
        definitions = load_jobfamily_definitions()

    df = df.copy()

    # Mapping durchführen
    df["Jobfamily"] = df["Planstelle"].apply(
        lambda x: match_jobfamily(x, definitions)
    )

    return df


def get_jobfamily_stats(df: pd.DataFrame) -> Dict:
    """
    Berechnet Statistiken zu Jobfamilies.

    Args:
        df: DataFrame mit Jobfamily-Spalte

    Returns:
        Dict mit Statistiken
    """
    if "Jobfamily" not in df.columns:
        return {}

    total_count = len(df)
    unmapped_count = (df["Jobfamily"] == "UNMAPPED").sum()
    mapped_count = total_count - unmapped_count

    family_counts = df["Jobfamily"].value_counts().to_dict()

    return {
        "total_planstellen": total_count,
        "mapped_planstellen": mapped_count,
        "unmapped_planstellen": unmapped_count,
        "mapping_rate": mapped_count / total_count if total_count > 0 else 0,
        "family_counts": family_counts,
        "unique_families": len([f for f in family_counts.keys() if f != "UNMAPPED"])
    }


def get_qualification_gaps(df: pd.DataFrame, definitions: Dict = None) -> pd.DataFrame:
    """
    Analysiert Qualifikationslücken zwischen Ist und Soll.

    Args:
        df: DataFrame mit Jobfamily und Ausbildung
        definitions: Optional Dict mit Jobfamily-Definitionen

    Returns:
        DataFrame mit Gap-Analyse
    """
    if definitions is None:
        definitions = load_jobfamily_definitions()

    # Nur besetzte Stellen
    df_active = df[~df["Is_Vacant"]].copy()

    # Qualifikations-Hierarchie (aus settings)
    from config.settings import EDUCATION_HIERARCHY

    gaps = []

    for family_name, family_def in definitions.items():
        family_data = df_active[df_active["Jobfamily"] == family_name]

        if len(family_data) == 0:
            continue

        min_qual_required = family_def.get("min_qualification", "")
        required_level = EDUCATION_HIERARCHY.get(min_qual_required, 0)

        # Prüfe für jeden Mitarbeitenden
        for _, row in family_data.iterrows():
            actual_qual = row.get("Ausbildung", "")
            actual_level = EDUCATION_HIERARCHY.get(actual_qual, 0)

            has_gap = actual_level < required_level

            gaps.append({
                "Jobfamily": family_name,
                "Planstelle": row.get("Planstelle", ""),
                "Personalnummer": row.get("Personalnummer", ""),
                "Ist_Qualifikation": actual_qual,
                "Soll_Qualifikation": min_qual_required,
                "Ist_Level": actual_level,
                "Soll_Level": required_level,
                "Gap": has_gap,
                "Gap_Points": required_level - actual_level
            })

    return pd.DataFrame(gaps)


def get_unmapped_planstellen(df: pd.DataFrame) -> pd.DataFrame:
    """
    Gibt alle unmapped Planstellen zurück.

    Args:
        df: DataFrame mit Jobfamily-Spalte

    Returns:
        DataFrame mit unmapped Planstellen
    """
    if "Jobfamily" not in df.columns:
        return pd.DataFrame()

    unmapped = df[df["Jobfamily"] == "UNMAPPED"].copy()

    # Gruppiere nach Planstellen-Bezeichnung für Übersicht
    unmapped_summary = unmapped.groupby("Planstelle").agg({
        "Planstellennr": "count",
        "Organisationseinheit": lambda x: ", ".join(x.unique()[:3])
    }).reset_index()

    unmapped_summary.columns = ["Planstelle", "Anzahl", "Org-Einheiten (Auswahl)"]
    unmapped_summary = unmapped_summary.sort_values("Anzahl", ascending=False)

    return unmapped_summary
