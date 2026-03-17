"""
Fuehrt die Gesamtfunktionalitaet fuer Kompakt plus Simulation aus:

1. IST-Koepfe Suite
2. IST-MAK Suite
3. IST-EUR Suite
4. Unit-Tests zur Stufenautomatik
5. End-to-End-Flow fuer Verguetungsklassen ueber Koepfe -> MAK -> EUR
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent

RUN_STEPS: list[tuple[str, list[str]]] = [
    ("IST-Koepfe Suite", [sys.executable, str(ROOT / "run_compact_sim_ist_koepfe_suite.py")]),
    ("IST-MAK Suite", [sys.executable, str(ROOT / "run_compact_sim_ist_mak_suite.py")]),
    ("IST-EUR Suite", [sys.executable, str(ROOT / "run_compact_sim_ist_eur_suite.py")]),
    ("Salary Automation Tests", [sys.executable, "-m", "pytest", str(ROOT / "test_compact_salary_automation.py"), "-q"]),
    ("Verguetungsklassen Flow 30", [sys.executable, str(ROOT / "check_compact_sim_ist_verguetungsklassen_flow_random_dates.py")]),
]


def main() -> None:
    print("Kompakt plus Simulation - Gesamtfunktionalitaet")
    print("=" * 72)

    results: list[tuple[str, int]] = []
    for label, cmd in RUN_STEPS:
        print(f"\n[RUN] {label}")
        completed = subprocess.run(
            cmd,
            cwd=str(ROOT.parent),
            capture_output=True,
            text=True,
        )
        results.append((label, completed.returncode))

        print(f"Exit-Code: {completed.returncode}")
        stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
        stderr_lines = [line for line in completed.stderr.splitlines() if line.strip()]

        tail = stdout_lines[-18:] if stdout_lines else []
        if tail:
            print("Auszug:")
            for line in tail:
                print(line)

        if completed.returncode != 0:
            err_tail = stderr_lines[-18:] if stderr_lines else []
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
        print("Fehlerhafte Schritte:")
        for name in failed:
            print(f"- {name}")
        raise SystemExit(1)

    print("Gesamtfunktionalitaet erfolgreich geprueft.")


if __name__ == "__main__":
    main()
