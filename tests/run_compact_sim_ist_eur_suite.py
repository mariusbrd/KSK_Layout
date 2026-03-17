"""
Fuehrt alle vorhandenen Echtdaten-Checks fuer
Kompakt plus Simulation -> IST-Analyse -> EUR
nacheinander aus und gibt eine kompakte Gesamtauswertung aus.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent

CHECK_SCRIPTS = [
    "check_compact_sim_ist_eur_geschlecht.py",
    "check_compact_sim_ist_eur_geschlecht_random_dates.py",
    "check_compact_sim_ist_eur_alterskohorten.py",
    "check_compact_sim_ist_eur_alterskohorten_random_dates.py",
    "check_compact_sim_ist_eur_qualifikation.py",
    "check_compact_sim_ist_eur_qualifikation_random_dates.py",
    "check_compact_sim_ist_eur_beschaeftigungsgrad.py",
    "check_compact_sim_ist_eur_beschaeftigungsgrad_random_dates.py",
    "check_compact_sim_ist_eur_beschaeftigungsstatus.py",
    "check_compact_sim_ist_eur_beschaeftigungsstatus_random_dates.py",
    "check_compact_sim_ist_eur_dauer_im_unternehmen.py",
    "check_compact_sim_ist_eur_dauer_im_unternehmen_random_dates.py",
    "check_compact_sim_ist_eur_atz_status.py",
    "check_compact_sim_ist_eur_atz_status_random_dates.py",
    "check_compact_sim_ist_eur_verguetungsklassen.py",
]


def main() -> None:
    print("IST-EUR Suite")
    print("=" * 72)

    results: list[tuple[str, int]] = []
    for script_name in CHECK_SCRIPTS:
        script_path = ROOT / script_name
        print(f"\n[RUN] {script_name}")
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT.parent),
            capture_output=True,
            text=True,
        )
        results.append((script_name, completed.returncode))

        print(f"Exit-Code: {completed.returncode}")
        stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
        stderr_lines = [line for line in completed.stderr.splitlines() if line.strip()]

        tail = stdout_lines[-12:] if stdout_lines else []
        if tail:
            print("Auszug:")
            for line in tail:
                print(line)

        if completed.returncode != 0:
            err_tail = stderr_lines[-12:] if stderr_lines else []
            if err_tail:
                print("Fehlerauszug:")
                for line in err_tail:
                    print(line)

    print("\n" + "=" * 72)
    passed = [name for name, code in results if code == 0]
    failed = [name for name, code in results if code != 0]
    print(f"Bestanden: {len(passed)}")
    print(f"Fehlgeschlagen: {len(failed)}")

    if failed:
        print("Fehlerhafte Checks:")
        for name in failed:
            print(f"- {name}")
        raise SystemExit(1)

    print("Alle IST-EUR-Checks erfolgreich.")


if __name__ == "__main__":
    main()
