"""
Jobfamily-Matching-Logik.

Pattern-basiertes und manuelles Mapping von Planstellen zu Jobfamilies.

Version 2.0 Features:
- Manual Overrides (höchste Priorität)
- Manual Assignments
- Pattern-basiert (bisherige Logik)
- Match-Type Tracking
"""

import pandas as pd
import json
import os
from fnmatch import fnmatch
from typing import Dict, List, Tuple, Optional
from datetime import datetime


def load_jobfamily_definitions(filepath: str = None) -> Dict:
    """
    Lädt Jobfamily-Definitionen aus JSON-Datei.
    Unterstützt v1 und v2 Format.

    Args:
        filepath: Pfad zur JSON-Datei (optional)

    Returns:
        Dict mit Jobfamily-Definitionen (v2 Format)
    """
    if filepath is None:
        # Default path
        config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        filepath = os.path.join(config_dir, "jobfamilies.json")

    if not os.path.exists(filepath):
        return _get_empty_v2_structure()

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check if v1 or v2 format
    if "jobfamilies" in data:
        # Already v2 format
        return data
    else:
        # v1 format - convert on the fly
        return _convert_v1_to_v2(data)


def save_jobfamily_definitions(definitions: Dict, filepath: str = None, create_backup: bool = True):
    """
    Speichert Jobfamily-Definitionen in JSON-Datei.
    Erstellt automatisch Backup vor dem Speichern.

    Args:
        definitions: Dict mit Jobfamily-Definitionen (v2 Format)
        filepath: Pfad zur JSON-Datei (optional)
        create_backup: Backup vor dem Speichern erstellen
    """
    if filepath is None:
        # Default path
        config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        filepath = os.path.join(config_dir, "jobfamilies.json")

    # Ensure v2 format
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    # Update metadata
    if "metadata" not in definitions:
        definitions["metadata"] = {}
    definitions["metadata"]["last_modified"] = datetime.now().isoformat()
    definitions["metadata"]["version"] = "2.0"

    # Create backup
    if create_backup and os.path.exists(filepath):
        backup_path = filepath + ".backup"
        with open(filepath, "r", encoding="utf-8") as f:
            backup_data = f.read()
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(backup_data)

    # Save
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(definitions, f, indent=2, ensure_ascii=False)


def match_jobfamily(planstelle: str, planstellennr: str, definitions: Dict) -> Tuple[str, str]:
    """
    Matcht eine Planstelle gegen Jobfamily-Definitionen mit Prioritäten.

    Priorität:
    1. manual_overrides (höchste Priorität)
    2. manual_assignments
    3. pattern-basiert (bisherige Logik)

    Args:
        planstelle: Planstellen-Bezeichnung
        planstellennr: Planstellen-Nummer (für manuelle Zuordnungen)
        definitions: Dict mit Jobfamily-Definitionen (v2 Format)

    Returns:
        Tuple[Jobfamily_Name, Match_Type]
        Match_Type: "manual_override" | "manual_assignment" | "pattern" | "unmapped"
    """
    if pd.isna(planstelle) or planstelle == "":
        return ("UNMAPPED", "unmapped")

    # Ensure v2 format
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    # Priority 1: Manual Overrides
    manual_overrides = definitions.get("manual_overrides", {})
    if planstellennr and planstellennr in manual_overrides:
        return (manual_overrides[planstellennr], "manual_override")

    # Priority 2: Manual Assignments
    jobfamilies = definitions.get("jobfamilies", {})
    for family_name, family_def in jobfamilies.items():
        manual_assignments = family_def.get("manual_assignments", [])
        if planstellennr and planstellennr in manual_assignments:
            return (family_name, "manual_assignment")

    # Priority 3: Pattern-basiert
    planstelle_lower = planstelle.lower()
    for family_name, family_def in jobfamilies.items():
        patterns = family_def.get("patterns", [])

        for pattern in patterns:
            pattern_lower = pattern.lower()

            # fnmatch für Wildcard-Matching
            if fnmatch(planstelle_lower, pattern_lower):
                return (family_name, "pattern")

    return ("UNMAPPED", "unmapped")


def assign_jobfamilies(df: pd.DataFrame, definitions: Dict = None, include_match_type: bool = False) -> pd.DataFrame:
    """
    Weist allen Planstellen im DataFrame Jobfamilies zu.

    Args:
        df: DataFrame mit Planstellen
        definitions: Optional Dict mit Jobfamily-Definitionen
        include_match_type: Wenn True, wird auch Match_Type Spalte erstellt

    Returns:
        DataFrame mit neuer Spalte "Jobfamily" (und optional "Match_Type")
    """
    if definitions is None:
        definitions = load_jobfamily_definitions()

    df = df.copy()

    # Ensure Planstellennr column exists
    if "Planstellennr" not in df.columns:
        df["Planstellennr"] = df.index.astype(str)

    # Mapping durchführen
    result = df.apply(
        lambda row: match_jobfamily(
            row.get("Planstelle", ""),
            str(row.get("Planstellennr", "")),
            definitions
        ),
        axis=1
    )

    # Unpack tuple results
    df["Jobfamily"] = result.apply(lambda x: x[0])

    if include_match_type:
        df["Match_Type"] = result.apply(lambda x: x[1])

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

    for family_name, family_def in definitions.get("jobfamilies", {}).items():
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
                "Ist_Level": actual_level,
                "Soll_Qualifikation": min_qual_required,
                "Soll_Level": required_level,
                "Has_Gap": has_gap,
                "Gap_Size": max(0, required_level - actual_level)
            })

    return pd.DataFrame(gaps)


# =====================================================================
# V2 FORMAT HELPER FUNCTIONS
# =====================================================================

def _get_empty_v2_structure() -> Dict:
    """
    Gibt leere v2 Struktur zurück.

    Returns:
        Dict mit v2 Struktur
    """
    return {
        "jobfamilies": {},
        "manual_overrides": {},
        "metadata": {
            "version": "2.0",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
    }


def _convert_v1_to_v2(v1_data: Dict) -> Dict:
    """
    Konvertiert v1 Format zu v2 Format.

    Args:
        v1_data: Jobfamily-Definitionen im v1 Format

    Returns:
        Dict im v2 Format
    """
    v2_data = _get_empty_v2_structure()

    # Konvertiere alle Jobfamilies
    for family_name, family_def in v1_data.items():
        v2_family_def = family_def.copy()

        # Füge leere manual_assignments hinzu
        if "manual_assignments" not in v2_family_def:
            v2_family_def["manual_assignments"] = []

        # Update version
        v2_family_def["version"] = "v2"

        v2_data["jobfamilies"][family_name] = v2_family_def

    v2_data["metadata"]["migration_date"] = datetime.now().isoformat()
    v2_data["metadata"]["migrated_from"] = "v1"

    return v2_data


def get_manual_assignments(definitions: Dict) -> Dict[str, List[str]]:
    """
    Extrahiert alle manuellen Zuordnungen.

    Args:
        definitions: Dict mit Jobfamily-Definitionen (v2 Format)

    Returns:
        Dict[JobFamily -> List[Planstellennr]]
    """
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    result = {}
    for family_name, family_def in definitions.get("jobfamilies", {}).items():
        manual_assignments = family_def.get("manual_assignments", [])
        if manual_assignments:
            result[family_name] = manual_assignments

    return result


def get_manual_overrides(definitions: Dict) -> Dict[str, str]:
    """
    Extrahiert alle manuellen Overrides.

    Args:
        definitions: Dict mit Jobfamily-Definitionen (v2 Format)

    Returns:
        Dict[Planstellennr -> JobFamily]
    """
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    return definitions.get("manual_overrides", {})


# =====================================================================
# CRUD FUNCTIONS FOR MANUAL ASSIGNMENTS
# =====================================================================

def add_manual_assignment(
    planstellennr: str,
    jobfamily: str,
    definitions: Dict,
    as_override: bool = False
) -> Dict:
    """
    Fügt eine manuelle Zuordnung hinzu.

    Args:
        planstellennr: Planstellen-Nummer
        jobfamily: Jobfamily-Name
        definitions: Dict mit Jobfamily-Definitionen
        as_override: Wenn True, wird als Override (höchste Priorität) gespeichert

    Returns:
        Aktualisierte Definitionen
    """
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    if as_override:
        # Add as manual override
        if "manual_overrides" not in definitions:
            definitions["manual_overrides"] = {}
        definitions["manual_overrides"][planstellennr] = jobfamily
    else:
        # Add to jobfamily's manual_assignments
        if jobfamily in definitions.get("jobfamilies", {}):
            if "manual_assignments" not in definitions["jobfamilies"][jobfamily]:
                definitions["jobfamilies"][jobfamily]["manual_assignments"] = []

            if planstellennr not in definitions["jobfamilies"][jobfamily]["manual_assignments"]:
                definitions["jobfamilies"][jobfamily]["manual_assignments"].append(planstellennr)

    return definitions


def remove_manual_assignment(planstellennr: str, definitions: Dict) -> Dict:
    """
    Entfernt eine manuelle Zuordnung (aus allen Families und Overrides).

    Args:
        planstellennr: Planstellen-Nummer
        definitions: Dict mit Jobfamily-Definitionen

    Returns:
        Aktualisierte Definitionen
    """
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    # Remove from manual_overrides
    if planstellennr in definitions.get("manual_overrides", {}):
        del definitions["manual_overrides"][planstellennr]

    # Remove from all jobfamily manual_assignments
    for family_name, family_def in definitions.get("jobfamilies", {}).items():
        manual_assignments = family_def.get("manual_assignments", [])
        if planstellennr in manual_assignments:
            manual_assignments.remove(planstellennr)
            family_def["manual_assignments"] = manual_assignments

    return definitions


def bulk_add_manual_assignments(
    assignments: Dict[str, str],
    definitions: Dict,
    as_override: bool = False
) -> Dict:
    """
    Fügt mehrere manuelle Zuordnungen auf einmal hinzu.

    Args:
        assignments: Dict[Planstellennr -> JobFamily]
        definitions: Dict mit Jobfamily-Definitionen
        as_override: Wenn True, werden alle als Overrides gespeichert

    Returns:
        Aktualisierte Definitionen
    """
    for planstellennr, jobfamily in assignments.items():
        definitions = add_manual_assignment(
            planstellennr,
            jobfamily,
            definitions,
            as_override=as_override
        )

    return definitions


def clear_all_manual_assignments(definitions: Dict, family_name: Optional[str] = None) -> Dict:
    """
    Löscht alle manuellen Zuordnungen.

    Args:
        definitions: Dict mit Jobfamily-Definitionen
        family_name: Wenn angegeben, nur für diese Family löschen

    Returns:
        Aktualisierte Definitionen
    """
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    if family_name:
        # Clear only for specific family
        if family_name in definitions.get("jobfamilies", {}):
            definitions["jobfamilies"][family_name]["manual_assignments"] = []
    else:
        # Clear all
        definitions["manual_overrides"] = {}
        for family_def in definitions.get("jobfamilies", {}).values():
            family_def["manual_assignments"] = []

    return definitions


def get_assignment_info(planstellennr: str, definitions: Dict) -> Dict:
    """
    Gibt Informationen über die Zuordnung einer Planstelle zurück.

    Args:
        planstellennr: Planstellen-Nummer
        definitions: Dict mit Jobfamily-Definitionen

    Returns:
        Dict mit Zuordnungsinformationen
    """
    if "jobfamilies" not in definitions:
        definitions = _convert_v1_to_v2(definitions)

    info = {
        "planstellennr": planstellennr,
        "has_manual_override": False,
        "has_manual_assignment": False,
        "override_family": None,
        "assignment_family": None,
        "all_families": []
    }

    # Check manual override
    manual_overrides = definitions.get("manual_overrides", {})
    if planstellennr in manual_overrides:
        info["has_manual_override"] = True
        info["override_family"] = manual_overrides[planstellennr]

    # Check manual assignments
    for family_name, family_def in definitions.get("jobfamilies", {}).items():
        manual_assignments = family_def.get("manual_assignments", [])
        if planstellennr in manual_assignments:
            info["has_manual_assignment"] = True
            info["assignment_family"] = family_name
            info["all_families"].append(family_name)

    return info
