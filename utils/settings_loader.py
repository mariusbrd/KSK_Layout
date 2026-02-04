
import json
import os
from typing import Dict, Any
from pathlib import Path

# Speicherort für User-Settings
SETTINGS_FILE = Path(__file__).parent.parent / "config" / "user_settings.json"

def load_user_settings() -> Dict[str, Any]:
    """Lädt Benutzereinstellungen aus JSON-Datei."""
    if not SETTINGS_FILE.exists():
        return {}
    
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
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
