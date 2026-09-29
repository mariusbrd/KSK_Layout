"""Run the dashboard validation tests in a coherent order.

The repository contains many focused pytest files plus manual analysis scripts.
This runner keeps the existing files in place, but executes the pytest files in
business-facing phases so that failures are easier to interpret.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
RESULTS = TESTS / "_results"


@dataclass(frozen=True)
class Phase:
    key: str
    title: str
    purpose: str
    files: tuple[str, ...]
    standard: bool = True
    smoke: bool = False


PHASES: tuple[Phase, ...] = (
    Phase(
        key="01_static_app_contracts",
        title="Static app contracts",
        purpose="App structure, navigation, text encoding, templates and basic source services.",
        smoke=True,
        files=(
            "test_app_layout.py",
            "test_sidebar_navigation.py",
            "test_text_normalization.py",
            "test_upload_templates.py",
            "test_source_service.py",
            "test_settings_loader.py",
            "test_compact_page_loader.py",
        ),
    ),
    Phase(
        key="02_input_data_and_clusters",
        title="Input data, loader and cluster contracts",
        purpose="Input schemas, data preparation, integrity checks and cluster source handling.",
        smoke=True,
        files=(
            "test_data_integrity.py",
            "test_loader_synthetic_fallback.py",
            "test_loader_cluster_source_signature.py",
            "test_data_prep_regression.py",
            "test_cluster_resolver.py",
            "test_cluster_manager_source_based.py",
            "test_cluster_settings_flow.py",
            "test_cluster_template_lineage.py",
            "test_synthetic_realism.py",
            "test_jf_cluster_fallbacks.py",
            "test_jf_cluster_no_unclustered.py",
            "test_azubi_cluster_mapping.py",
            "test_hybrid_cluster_alignment.py",
        ),
    ),
    Phase(
        key="03_core_calculation_engines",
        title="Core calculation engines",
        purpose="MAK, salary, Soll-Ist, exclusion, compensation and matrix calculation logic.",
        smoke=True,
        files=(
            "test_mak_allocation.py",
            "test_soll_ist_koepfe_engine.py",
            "test_soll_ist_koepfe_page.py",
            "test_compensation_band_fit.py",
            "test_numeric_compensation_series.py",
            "test_matrix_helpers.py",
            "test_jobfamily_logic.py",
            "test_exclusion_groups.py",
            "test_status_quo_baseline.py",
            "test_azubi_baseline_logic.py",
            "test_azubi_forecast_logic.py",
            "test_loader_azubi_zeroing.py",
            "test_occupied_placeholder_soll_correction.py",
        ),
    ),
    Phase(
        key="04_page_contracts",
        title="Page and display contracts",
        purpose="Page-specific tables, charts, filters, ordering and inventory coverage.",
        smoke=True,
        files=(
            "test_analysis_pages_contracts.py",
            "test_orgunit_simulation_page.py",
            "test_chart_ordering.py",
            "test_dashboard_display_coverage.py",
            "test_active_page_export_inventory.py",
            "test_forecast_page_export_scope.py",
            "test_exclusion_deep_dive_export_scope.py",
            "test_sidebar_filters.py",
            "test_dimension_switch_sync.py",
            "test_pages_phase3_i18n.py",
            "test_pages_phase4_i18n.py",
            "test_pages_phase5_i18n.py",
            "test_i18n_phase1.py",
        ),
    ),
    Phase(
        key="05_lineage_and_exports",
        title="Lineage and Excel export contracts",
        purpose="Lineage registry, transformations, input reports, glossary and Excel exports.",
        smoke=True,
        files=(
            "test_lineage_package_api.py",
            "test_lineage_registry.py",
            "test_transformation_lineage.py",
            "test_lineage_code.py",
            "test_lineage_export.py",
            "test_lineage_glossary.py",
            "test_import_export_lineage.py",
            "test_compact_ist_export.py",
            "test_compact_simulation_export.py",
            "test_lazy_excel_download.py",
            "test_kompakt_lineage_coverage.py",
            "test_compact_plus_simulation_lineage_coverage.py",
            "test_analysis_page_lineage_coverage.py",
            "test_glossary_analysis_pages.py",
            "test_settings_lineage_coverage.py",
        ),
    ),
    Phase(
        key="06_simulation_and_forecasts",
        title="Simulation and forecast logic",
        purpose="Simulation parameters, attrition, hiring, hybrid flows and scenario parity.",
        files=(
            "test_simulation_params.py",
            "test_simulation_params_architecture_guardrails.py",
            "test_compact_simulation_engine.py",
            "test_compact_sim_cluster_source_consistency.py",
            "test_compact_plus_actual_parity.py",
            "test_compact_plus_abgaenge_parameter_parity.py",
            "test_compact_plus_zugaenge_parameter_parity.py",
            "test_compact_plus_combined_parameter_parity.py",
            "test_compact_plus_sim_jobfamily_filters.py",
            "test_abgaenge_golden_master.py",
            "test_abgaenge_cluster_source_consistency.py",
            "test_zugaenge_golden_master.py",
            "test_zugaenge_cluster_source_consistency.py",
            "test_zugaenge_gender_assignment.py",
            "test_hire_dist.py",
            "test_forecast_separation.py",
            "test_hybrid_end_to_end_regression.py",
            "test_compact_salary_automation.py",
            "test_atz_hypothesen_forecast.py",
            "test_azubi_consistency.py",
            "test_azubi_charts.py",
            "test_azubi_deep.py",
            "test_azubi_deterministic_distribution.py",
            "test_azubi_distribution.py",
            "test_azubi_forecast.py",
            "test_azubi_forecast_deepdive.py",
            "test_azubi_lifecycle.py",
            "test_azubi_sprint_changes.py",
            "test_azubi_training_sonstige.py",
            "test_kuendigung_forecast.py",
            "test_renten_regler_forecast.py",
            "test_ruhend_mak_headcount.py",
            "test_trainee_forecast.py",
        ),
    ),
    Phase(
        key="07_performance_and_shadow_checks",
        title="Performance and shadow checks",
        purpose="Cache, performance guardrails and deeper MAK lineage shadow validations.",
        standard=False,
        files=(
            "test_page_rerun_caches.py",
            "test_shadow_mak_8d.py",
            "test_shadow_mak_8d_realdata.py",
            "test_shadow_mak_person_audit_10a.py",
            "test_shadow_person_stage_summary_9b.py",
            "test_workshop_deep_validation.py",
            "test_occupied_placeholder_soll_correction_deep.py",
        ),
    ),
)


def _existing_files(file_names: tuple[str, ...]) -> list[str]:
    return [str(TESTS / name) for name in file_names if (TESTS / name).exists()]


def _unknown_pytest_files() -> list[str]:
    known = {name for phase in PHASES for name in phase.files}
    return [
        str(path)
        for path in sorted(TESTS.glob("test_*.py"))
        if path.name not in known
    ]


def _selected_phases(profile: str, requested: list[str]) -> list[Phase]:
    if requested:
        by_key = {phase.key: phase for phase in PHASES}
        missing = [key for key in requested if key not in by_key]
        if missing:
            raise SystemExit(f"Unknown phase(s): {', '.join(missing)}")
        return [by_key[key] for key in requested]
    if profile == "smoke":
        return [phase for phase in PHASES if phase.smoke]
    if profile == "standard":
        return [phase for phase in PHASES if phase.standard]
    if profile == "full":
        return list(PHASES)
    raise SystemExit(f"Unknown profile: {profile}")


def _run_phase(phase: Phase, pytest_args: list[str]) -> dict[str, object]:
    files = _existing_files(phase.files)
    missing = [name for name in phase.files if not (TESTS / name).exists()]
    if not files:
        return {
            "phase": phase.key,
            "title": phase.title,
            "returncode": 0,
            "duration_seconds": 0.0,
            "missing_files": missing,
            "skipped": True,
        }

    cmd = [sys.executable, "-m", "pytest", *files, *pytest_args]
    print(f"\n[{phase.key}] {phase.title}")
    print(phase.purpose)
    if missing:
        print("Missing files:", ", ".join(missing))
    print("Command:", " ".join(cmd))

    start = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(cmd, cwd=str(ROOT), env=env)
    duration = time.perf_counter() - start

    return {
        "phase": phase.key,
        "title": phase.title,
        "returncode": completed.returncode,
        "duration_seconds": round(duration, 3),
        "missing_files": missing,
        "skipped": False,
    }


def _write_summary(results: list[dict[str, object]], profile: str) -> Path:
    RESULTS.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS / f"app_validation_{profile}_{ts}.json"
    payload = {
        "profile": profile,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "results": results,
        "passed": all(int(result["returncode"]) == 0 for result in results),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("smoke", "standard", "full"),
        default="standard",
        help="smoke = quick app contracts, standard = daily routine, full = includes heavy shadow checks",
    )
    parser.add_argument(
        "--phase",
        action="append",
        default=[],
        help="Run one explicit phase key. Can be passed multiple times.",
    )
    parser.add_argument(
        "--continue-on-fail",
        action="store_true",
        help="Continue with later phases after a failing phase.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ordered routine without executing pytest.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to pytest after '--', for example -- -q -x",
    )
    args = parser.parse_args()

    pytest_args = list(args.pytest_args)
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    if not pytest_args:
        pytest_args = ["-q", "-p", "no:cacheprovider"]

    phases = _selected_phases(args.profile, args.phase)
    unknown_files = _unknown_pytest_files()

    print("Dashboard app validation routine")
    print("=" * 72)
    print(f"Profile: {args.profile}")
    print(f"Pytest args: {' '.join(pytest_args)}")
    print(f"Selected phases: {len(phases)}")

    for phase in phases:
        existing = _existing_files(phase.files)
        print(f"- {phase.key}: {phase.title} ({len(existing)} files)")

    if unknown_files:
        print("\nUncategorized pytest files run only by 'py -3 -m pytest' directly:")
        for path in unknown_files:
            print(f"- {Path(path).name}")

    if args.dry_run:
        return 0

    results: list[dict[str, object]] = []
    for phase in phases:
        result = _run_phase(phase, pytest_args)
        results.append(result)
        if int(result["returncode"]) != 0 and not args.continue_on_fail:
            break

    summary_path = _write_summary(results, args.profile)

    print("\n" + "=" * 72)
    print("Validation summary")
    for result in results:
        status = "PASS" if int(result["returncode"]) == 0 else "FAIL"
        print(f"{status:4} {result['phase']} ({result['duration_seconds']}s)")
    print(f"Summary JSON: {summary_path}")

    return 0 if all(int(result["returncode"]) == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
