
import json
import os
from typing import Dict, Any
from pathlib import Path

# Speicherort für User-Settings
SETTINGS_FILE = Path(__file__).parent.parent / "config" / "user_settings.json"


def _migrate_exclusions_schema(exclusions: Any) -> tuple[Dict[str, Any], bool]:
    """Hebt ältere Exclusions-Schemata minimalinvasiv auf den aktuellen Stand."""
    if not isinstance(exclusions, dict):
        return {}, False

    migrated = dict(exclusions)
    changed = False

    if "planstellen_follow_person" not in migrated:
        # Rueckwaertskompatibel: Das neue Verhalten bleibt opt-in.
        migrated["planstellen_follow_person"] = False
        changed = True

    if "org_units" not in migrated or migrated.get("org_units") is None:
        migrated["org_units"] = []
        changed = True

    return migrated, changed


def _migrate_settings_schema(settings: Any) -> tuple[Dict[str, Any], bool]:
    """Normalisiert geladene Settings auf ein kompatibles Basisschema."""
    if not isinstance(settings, dict):
        return {}, False

    migrated = dict(settings)
    changed = False

    exclusions, exclusions_changed = _migrate_exclusions_schema(
        migrated.get("exclusions", {})
    )
    if exclusions_changed or "exclusions" not in migrated:
        migrated["exclusions"] = exclusions
        changed = True

    return migrated, changed

def load_user_settings() -> Dict[str, Any]:
    """Lädt Benutzereinstellungen aus JSON-Datei."""
    if not SETTINGS_FILE.exists():
        return {}
    
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
        migrated, changed = _migrate_settings_schema(settings)
        if changed:
            save_user_settings(migrated)
        return migrated
    except Exception as e:
        print(f"Fehler beim Laden der Settings: {e}")
        return {}

def save_user_settings(settings: Dict[str, Any]) -> bool:
    """Speichert Benutzereinstellungen in JSON-Datei."""
    try:
        # Sicherstellen, dass Verzeichnis existiert
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Fehler beim Speichern der Settings: {e}")
        return False

def get_setting(key: str, default: Any = None) -> Any:
    """Holt einen einzelnen Wert aus den gespeicherten Settings."""
    settings = load_user_settings()
    return settings.get(key, default)

def set_setting(key: str, value: Any) -> bool:
    """Setzt einen einzelnen Wert und speichert sofort."""
    settings = load_user_settings()
    settings[key] = value
    return save_user_settings(settings)
