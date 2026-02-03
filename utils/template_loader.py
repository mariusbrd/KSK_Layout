"""
Template-Loader fuer Job Family Templates.

Verwaltet das Laden, Auflisten und Anwenden von Branchen-Templates.
"""

import os
import json
import fnmatch
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import pandas as pd


# =============================================================================
# KONSTANTEN
# =============================================================================

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "templates"
)


# =============================================================================
# TEMPLATE LOADING
# =============================================================================

def get_templates_directory() -> str:
    """Gibt den Pfad zum Templates-Verzeichnis zurueck."""
    return TEMPLATES_DIR


def get_available_templates() -> List[Dict]:
    """
    Gibt eine Liste aller verfuegbaren Templates zurueck.

    Returns:
        List[{
            "id": "sparkassen",
            "name": "Sparkassen & Banken",
            "description": "...",
            "icon": "🏦",
            "family_count": 12,
            "industry": "banking"
        }]
    """
    templates = []

    if not os.path.exists(TEMPLATES_DIR):
        return templates

    for filename in os.listdir(TEMPLATES_DIR):
        if filename.endswith("_template.json"):
            filepath = os.path.join(TEMPLATES_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                template_info = data.get("template_info", {})
                jobfamilies = data.get("jobfamilies", {})

                templates.append({
                    "id": template_info.get("id", filename.replace("_template.json", "")),
                    "name": template_info.get("name", filename),
                    "description": template_info.get("description", ""),
                    "icon": template_info.get("icon", "📋"),
                    "family_count": len(jobfamilies),
                    "industry": template_info.get("industry", "general"),
                    "version": template_info.get("version", "1.0"),
                    "filepath": filepath
                })
            except (json.JSONDecodeError, IOError) as e:
                print(f"Fehler beim Laden von {filename}: {e}")
                continue

    # Sortiere nach Name
    templates.sort(key=lambda x: x["name"])

    return templates


def load_template(template_id: str) -> Optional[Dict]:
    """
    Laedt ein spezifisches Template.

    Args:
        template_id: ID des Templates (z.B. "sparkassen", "universal")

    Returns:
        Template-Dictionary oder None wenn nicht gefunden
    """
    # Suche nach Template-Datei
    filename = f"{template_id}_template.json"
    filepath = os.path.join(TEMPLATES_DIR, filename)

    if not os.path.exists(filepath):
        # Versuche alle Templates zu durchsuchen
        for tpl in get_available_templates():
            if tpl["id"] == template_id:
                filepath = tpl["filepath"]
                break
        else:
            return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Fehler beim Laden von Template {template_id}: {e}")
        return None


def get_template_preview(template_id: str) -> Dict:
    """
    Gibt eine Vorschau des Templates zurueck.

    Args:
        template_id: ID des Templates

    Returns:
        {
            "families": [{"name": "...", "pattern_count": 5, "description": "..."}],
            "total_patterns": 45,
            "industries_covered": ["banking"]
        }
    """
    template = load_template(template_id)

    if not template:
        return {"families": [], "total_patterns": 0, "industries_covered": []}

    families = []
    total_patterns = 0

    for family_name, family_def in template.get("jobfamilies", {}).items():
        patterns = family_def.get("patterns", [])
        families.append({
            "name": family_name,
            "description": family_def.get("description", ""),
            "pattern_count": len(patterns),
            "patterns": patterns[:3],  # Erste 3 Patterns als Vorschau
            "color": family_def.get("color", "#808080")
        })
        total_patterns += len(patterns)

    return {
        "families": families,
        "total_patterns": total_patterns,
        "industry": template.get("template_info", {}).get("industry", "general")
    }


# =============================================================================
# TEMPLATE APPLICATION
# =============================================================================

def apply_template(
    template_id: str,
    df: Optional[pd.DataFrame] = None,
    planstelle_column: str = "Planstelle"
) -> Tuple[Dict, Dict]:
    """
    Wendet ein Template auf vorhandene Daten an und berechnet Matching-Statistiken.

    Args:
        template_id: ID des Templates
        df: Optional DataFrame mit Job-Daten
        planstelle_column: Name der Spalte mit Job-Titeln

    Returns:
        Tuple[definitions, matching_report]

        definitions: Fertige Job Family Definitionen (v2 Format)
        matching_report: {
            "total_jobs": 500,
            "matched": 450,
            "unmatched": 50,
            "matching_rate": 0.9,
            "by_family": {"IT": 45, "Vertrieb": 120, ...},
            "unmatched_examples": ["Job1", "Job2", ...]
        }
    """
    template = load_template(template_id)

    if not template:
        return {}, {"error": f"Template '{template_id}' nicht gefunden"}

    # Extrahiere Job Families
    jobfamilies = template.get("jobfamilies", {})

    # Erstelle v2-kompatible Definitionen
    definitions = {
        "jobfamilies": jobfamilies,
        "manual_overrides": {},
        "metadata": {
            "version": "2.0",
            "template": template_id,
            "template_name": template.get("template_info", {}).get("name", template_id),
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
    }

    # Berechne Matching-Report wenn DataFrame vorhanden
    matching_report = {
        "total_jobs": 0,
        "matched": 0,
        "unmatched": 0,
        "matching_rate": 0.0,
        "by_family": {},
        "unmatched_examples": []
    }

    if df is not None and planstelle_column in df.columns:
        # Unique Job-Titel
        job_titles = df[planstelle_column].dropna().unique()
        matching_report["total_jobs"] = len(job_titles)

        matched_titles = set()
        family_counts = {family: 0 for family in jobfamilies.keys()}

        # Teste jedes Pattern
        for job_title in job_titles:
            job_matched = False
            for family_name, family_def in jobfamilies.items():
                for pattern in family_def.get("patterns", []):
                    if fnmatch.fnmatch(job_title.lower(), pattern.lower()):
                        matched_titles.add(job_title)
                        family_counts[family_name] += 1
                        job_matched = True
                        break
                if job_matched:
                    break

        # Statistiken berechnen
        matching_report["matched"] = len(matched_titles)
        matching_report["unmatched"] = len(job_titles) - len(matched_titles)
        matching_report["matching_rate"] = (
            len(matched_titles) / len(job_titles) if len(job_titles) > 0 else 0.0
        )
        matching_report["by_family"] = {k: v for k, v in family_counts.items() if v > 0}

        # Beispiele fuer nicht gematchte Jobs
        unmatched_titles = [t for t in job_titles if t not in matched_titles]
        matching_report["unmatched_examples"] = unmatched_titles[:10]

    return definitions, matching_report


def get_matching_preview(
    template_id: str,
    df: pd.DataFrame,
    planstelle_column: str = "Planstelle",
    limit: int = 20
) -> List[Dict]:
    """
    Gibt eine Vorschau der Zuordnungen zurueck.

    Args:
        template_id: ID des Templates
        df: DataFrame mit Job-Daten
        planstelle_column: Name der Spalte mit Job-Titeln
        limit: Maximale Anzahl der Vorschau-Eintraege

    Returns:
        List[{
            "job_title": "IT-Administrator",
            "matched_family": "IT & Digitalisierung",
            "matched_pattern": "*IT-*",
            "is_matched": True
        }]
    """
    template = load_template(template_id)

    if not template or df is None or planstelle_column not in df.columns:
        return []

    jobfamilies = template.get("jobfamilies", {})
    job_titles = df[planstelle_column].dropna().unique()[:limit]

    preview = []

    for job_title in job_titles:
        entry = {
            "job_title": job_title,
            "matched_family": None,
            "matched_pattern": None,
            "is_matched": False
        }

        for family_name, family_def in jobfamilies.items():
            for pattern in family_def.get("patterns", []):
                if fnmatch.fnmatch(job_title.lower(), pattern.lower()):
                    entry["matched_family"] = family_name
                    entry["matched_pattern"] = pattern
                    entry["is_matched"] = True
                    break
            if entry["is_matched"]:
                break

        preview.append(entry)

    return preview


# =============================================================================
# TEMPLATE VALIDATION
# =============================================================================

def validate_template(template: Dict) -> Tuple[bool, List[str]]:
    """
    Validiert ein Template auf Vollstaendigkeit und Korrektheit.

    Args:
        template: Template-Dictionary

    Returns:
        Tuple[is_valid, error_messages]
    """
    errors = []

    # Prüfe template_info
    if "template_info" not in template:
        errors.append("'template_info' fehlt")
    else:
        info = template["template_info"]
        for field in ["id", "name", "description"]:
            if field not in info:
                errors.append(f"template_info.{field} fehlt")

    # Prüfe jobfamilies
    if "jobfamilies" not in template:
        errors.append("'jobfamilies' fehlt")
    elif not isinstance(template["jobfamilies"], dict):
        errors.append("'jobfamilies' muss ein Dictionary sein")
    elif len(template["jobfamilies"]) == 0:
        errors.append("'jobfamilies' ist leer")
    else:
        for family_name, family_def in template["jobfamilies"].items():
            if not isinstance(family_def, dict):
                errors.append(f"'{family_name}' muss ein Dictionary sein")
                continue

            if "patterns" not in family_def:
                errors.append(f"'{family_name}': 'patterns' fehlt")
            elif not isinstance(family_def["patterns"], list):
                errors.append(f"'{family_name}': 'patterns' muss eine Liste sein")

            if "manual_assignments" not in family_def:
                # Kein Fehler, aber Warnung (wird automatisch hinzugefügt)
                pass

    return len(errors) == 0, errors


def ensure_v2_compatibility(template: Dict) -> Dict:
    """
    Stellt sicher, dass ein Template v2-kompatibel ist.

    Args:
        template: Template-Dictionary

    Returns:
        v2-kompatibles Template
    """
    # Kopie erstellen
    result = template.copy()

    # Ensure metadata
    if "metadata" not in result:
        result["metadata"] = {}

    result["metadata"]["version"] = "2.0"

    # Ensure manual_assignments in each family
    for family_name, family_def in result.get("jobfamilies", {}).items():
        if "manual_assignments" not in family_def:
            family_def["manual_assignments"] = []

    # Ensure manual_overrides
    if "manual_overrides" not in result:
        result["manual_overrides"] = {}

    return result


# =============================================================================
# STANDARD SETS / QUICK LOAD
# =============================================================================

def get_standard_sets() -> List[Dict]:
    """
    Gibt eine Liste der verfuegbaren Standard-Sets zurueck.

    Diese Funktion bietet einen schnellen Zugriff auf vorkonfigurierte
    Job Family Sets, die direkt geladen werden koennen.

    Returns:
        List[{
            "id": "sparkassen",
            "name": "Sparkassen & Banken",
            "description": "10 Job Families fuer Sparkassen",
            "icon": "🏦",
            "family_count": 10,
            "recommended_for": "Sparkassen, Volksbanken, Genossenschaftsbanken"
        }]
    """
    templates = get_available_templates()

    # Erweitere mit zusätzlichen Infos für die Quick-Load Ansicht
    standard_sets = []
    for template in templates:
        set_info = {
            "id": template["id"],
            "name": template["name"],
            "description": template["description"],
            "icon": template["icon"],
            "family_count": template["family_count"],
            "industry": template["industry"]
        }

        # Empfehlungen je nach Branche
        if template["industry"] == "banking":
            set_info["recommended_for"] = "Sparkassen, Volksbanken, Genossenschaftsbanken"
        elif template["industry"] == "insurance":
            set_info["recommended_for"] = "Versicherungen, Finanzdienstleister"
        else:
            set_info["recommended_for"] = "Alle Branchen, flexible Anpassung"

        standard_sets.append(set_info)

    return standard_sets


def load_standard_set(
    set_id: str,
    merge_mode: str = "replace"
) -> Tuple[Dict, str]:
    """
    Laedt ein Standard-Set und gibt fertige Definitionen zurueck.

    Args:
        set_id: ID des Standard-Sets (z.B. "sparkassen", "universal")
        merge_mode: "replace" = komplett ersetzen, "merge" = zusammenfuehren

    Returns:
        Tuple[definitions, message]

        definitions: Fertige Job Family Definitionen im v2 Format
        message: Erfolgsmeldung oder Fehler
    """
    template = load_template(set_id)

    if not template:
        return {}, f"Standard-Set '{set_id}' nicht gefunden"

    # Erstelle v2-kompatible Definitionen
    jobfamilies = template.get("jobfamilies", {})

    definitions = {
        "jobfamilies": {},
        "manual_overrides": {},
        "metadata": {
            "version": "2.0",
            "source": "standard_set",
            "template_id": set_id,
            "template_name": template.get("template_info", {}).get("name", set_id),
            "loaded_at": datetime.now().isoformat()
        }
    }

    # Konvertiere Families in v2 Format
    for family_name, family_def in jobfamilies.items():
        definitions["jobfamilies"][family_name] = {
            "description": family_def.get("description", ""),
            "patterns": family_def.get("patterns", []),
            "min_qualification": family_def.get("min_qualification", ""),
            "color": family_def.get("color", "#808080"),
            "manual_assignments": family_def.get("manual_assignments", [])
        }

    template_name = template.get("template_info", {}).get("name", set_id)
    message = f"✅ Standard-Set '{template_name}' geladen ({len(jobfamilies)} Job Families)"

    return definitions, message


def merge_definitions(
    existing: Dict,
    new_definitions: Dict,
    conflict_mode: str = "keep_existing"
) -> Tuple[Dict, Dict]:
    """
    Fuegt neue Definitionen mit bestehenden zusammen.

    Args:
        existing: Bestehende Definitionen
        new_definitions: Neue Definitionen zum Hinzufuegen
        conflict_mode: "keep_existing" = bestehende behalten,
                      "overwrite" = neue ueberschreiben,
                      "rename" = neue umbenennen

    Returns:
        Tuple[merged_definitions, merge_report]

        merge_report: {
            "added": ["Family1", "Family2"],
            "skipped": ["Family3"],
            "renamed": {"OldName": "NewName"},
            "total_families": 15
        }
    """
    merged = {
        "jobfamilies": dict(existing.get("jobfamilies", {})),
        "manual_overrides": dict(existing.get("manual_overrides", {})),
        "metadata": {
            "version": "2.0",
            "last_modified": datetime.now().isoformat(),
            "merged_from": new_definitions.get("metadata", {}).get("template_id", "unknown")
        }
    }

    report = {
        "added": [],
        "skipped": [],
        "renamed": {},
        "overwritten": []
    }

    for family_name, family_def in new_definitions.get("jobfamilies", {}).items():
        if family_name in merged["jobfamilies"]:
            if conflict_mode == "keep_existing":
                report["skipped"].append(family_name)
            elif conflict_mode == "overwrite":
                merged["jobfamilies"][family_name] = family_def
                report["overwritten"].append(family_name)
            elif conflict_mode == "rename":
                # Finde eindeutigen Namen
                counter = 2
                new_name = f"{family_name} ({counter})"
                while new_name in merged["jobfamilies"]:
                    counter += 1
                    new_name = f"{family_name} ({counter})"
                merged["jobfamilies"][new_name] = family_def
                report["renamed"][family_name] = new_name
        else:
            merged["jobfamilies"][family_name] = family_def
            report["added"].append(family_name)

    report["total_families"] = len(merged["jobfamilies"])

    return merged, report
