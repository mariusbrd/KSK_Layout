"""
Migration Script: Jobfamily Definitions v1 -> v2

Migriert bestehende Jobfamily-Definitionen von v1 zu v2 Format.

Änderungen in v2:
- Nested structure: jobfamilies, manual_overrides, metadata
- Manual assignments pro Jobfamily
- Global manual overrides
- Metadata tracking (version, timestamps)
"""

import json
import os
from datetime import datetime
from typing import Dict
import shutil
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')


def migrate_v1_to_v2(v1_data: Dict) -> Dict:
    """
    Konvertiert v1 Format zu v2 Format.

    V1 Format:
    {
        "Jobfamily Name": {
            "patterns": [...],
            "min_qualification": "...",
            "description": "...",
            "version": "v1",
            "valid_from": "..."
        }
    }

    V2 Format:
    {
        "jobfamilies": {
            "Jobfamily Name": {
                "patterns": [...],
                "min_qualification": "...",
                "description": "...",
                "version": "v2",
                "valid_from": "...",
                "manual_assignments": []
            }
        },
        "manual_overrides": {},
        "metadata": {
            "version": "2.0",
            "created": "...",
            "last_modified": "...",
            "migration_date": "...",
            "migrated_from": "v1"
        }
    }

    Args:
        v1_data: Dict mit v1 Definitionen

    Returns:
        Dict mit v2 Definitionen
    """
    print("🔄 Starte Migration v1 -> v2...")

    v2_data = {
        "jobfamilies": {},
        "manual_overrides": {},
        "metadata": {
            "version": "2.0",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "migration_date": datetime.now().isoformat(),
            "migrated_from": "v1"
        }
    }

    # Konvertiere jede Jobfamily
    for family_name, family_def in v1_data.items():
        print(f"  ✓ Migriere '{family_name}'...")

        v2_family_def = family_def.copy()

        # Füge neue v2 Felder hinzu
        if "manual_assignments" not in v2_family_def:
            v2_family_def["manual_assignments"] = []

        # Update version
        v2_family_def["version"] = "v2"

        v2_data["jobfamilies"][family_name] = v2_family_def

    print(f"✅ Migration abgeschlossen: {len(v2_data['jobfamilies'])} Jobfamilies konvertiert")

    return v2_data


def backup_file(filepath: str) -> str:
    """
    Erstellt Backup der Original-Datei.

    Args:
        filepath: Pfad zur Datei

    Returns:
        Pfad zum Backup
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.backup_{timestamp}"

    shutil.copy2(filepath, backup_path)
    print(f"📦 Backup erstellt: {backup_path}")

    return backup_path


def validate_v2_structure(data: Dict) -> bool:
    """
    Validiert v2 Struktur.

    Args:
        data: Dict mit v2 Definitionen

    Returns:
        True wenn valide
    """
    print("🔍 Validiere v2 Struktur...")

    required_keys = ["jobfamilies", "manual_overrides", "metadata"]
    for key in required_keys:
        if key not in data:
            print(f"  ❌ Fehlendes Feld: {key}")
            return False

    # Validate metadata
    metadata_keys = ["version", "last_modified"]
    for key in metadata_keys:
        if key not in data.get("metadata", {}):
            print(f"  ⚠️ Warnung: Fehlendes Metadata-Feld: {key}")

    # Validate jobfamilies
    for family_name, family_def in data.get("jobfamilies", {}).items():
        if "patterns" not in family_def:
            print(f"  ❌ Jobfamily '{family_name}' hat keine Patterns")
            return False

        if "manual_assignments" not in family_def:
            print(f"  ⚠️ Warnung: Jobfamily '{family_name}' hat keine manual_assignments")

    print("  ✅ Struktur ist valide")
    return True


def migrate_file(filepath: str, dry_run: bool = False) -> bool:
    """
    Migriert eine Jobfamily-JSON-Datei von v1 zu v2.

    Args:
        filepath: Pfad zur JSON-Datei
        dry_run: Wenn True, keine Änderungen speichern

    Returns:
        True wenn erfolgreich
    """
    print(f"\n{'='*60}")
    print(f"Migration: {filepath}")
    print(f"{'='*60}\n")

    # Check if file exists
    if not os.path.exists(filepath):
        print(f"❌ Datei nicht gefunden: {filepath}")
        return False

    # Load existing data
    print("📖 Lade bestehende Definitionen...")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check if already v2
    if "jobfamilies" in data:
        print("ℹ️ Datei ist bereits im v2 Format")
        return True

    # Create backup
    if not dry_run:
        backup_file(filepath)

    # Migrate
    v2_data = migrate_v1_to_v2(data)

    # Validate
    if not validate_v2_structure(v2_data):
        print("❌ Validierung fehlgeschlagen!")
        return False

    # Save
    if dry_run:
        print("\n🔍 DRY RUN - Keine Änderungen gespeichert")
        print(f"\n📄 Preview der migrierten Daten:")
        print(json.dumps(v2_data, indent=2, ensure_ascii=False)[:500] + "...")
    else:
        print(f"\n💾 Speichere v2 Definitionen...")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(v2_data, f, indent=2, ensure_ascii=False)
        print(f"✅ Migration erfolgreich abgeschlossen!")

    return True


def rollback_migration(filepath: str, backup_path: str) -> bool:
    """
    Stellt Original-Datei aus Backup wieder her.

    Args:
        filepath: Pfad zur Datei
        backup_path: Pfad zum Backup

    Returns:
        True wenn erfolgreich
    """
    print(f"\n🔄 Rollback: {filepath}")

    if not os.path.exists(backup_path):
        print(f"❌ Backup nicht gefunden: {backup_path}")
        return False

    shutil.copy2(backup_path, filepath)
    print(f"✅ Rollback erfolgreich: {filepath} wiederhergestellt")

    return True


def main():
    """
    Haupt-Migrations-Funktion.
    """
    import sys

    # Default path
    config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
    default_filepath = os.path.join(config_dir, "jobfamilies.json")

    # Parse arguments
    dry_run = "--dry-run" in sys.argv
    filepath = default_filepath

    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        filepath = sys.argv[1]

    print(f"\n{'='*60}")
    print(f"Jobfamily Migration v1 -> v2")
    print(f"{'='*60}\n")

    if dry_run:
        print("⚠️ DRY RUN MODE - Keine Änderungen werden gespeichert\n")

    # Run migration
    success = migrate_file(filepath, dry_run=dry_run)

    if success:
        print("\n" + "="*60)
        print("✅ Migration erfolgreich!")
        print("="*60)

        if not dry_run:
            print("\n💡 Tipps:")
            print("  - Backup wurde erstellt (*.backup_TIMESTAMP)")
            print("  - Testen Sie die Anwendung gründlich")
            print("  - Bei Problemen: Rollback mit backup_TIMESTAMP Datei")
    else:
        print("\n" + "="*60)
        print("❌ Migration fehlgeschlagen!")
        print("="*60)
        sys.exit(1)


if __name__ == "__main__":
    main()
