from __future__ import annotations

from pathlib import Path
import re

import pandas as pd

from utils.lineage_registry import (
    CodeReference,
    LineageSpec,
    get_lineage_spec,
    get_lineage_specs,
    iter_lineage_specs,
    lineage_report_dataframe,
)


ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = ROOT / "tests"

EXPECTED_ANALYSIS_IDS = {
    "7-01",
    "1-01",
    "1-02",
    "1-03",
    "1-04",
    "1-05",
    "1-06",
    "1-07",
    "1-08",
    "1-09",
    "1-10",
    "1-11",
    "2-01",
    "2-02",
    "2-03",
    "2-04",
    "2-05",
    "2-06",
    "8-13",
    "8-14",
    "8-15",
    "8-16",
    "8-17",
    "9-13",
    "9-14",
    "9-15",
    "9-16",
    "9-17",
    "10-01",
    "10-02",
    "10-03",
    "10-04",
    "10-05",
    "10-06",
    "10-07",
    "11-01",
    "11-02",
    "11-03",
    "11-04",
    "11-05",
}


def _available_test_functions() -> set[str]:
    functions: set[str] = set()
    for test_file in TESTS_ROOT.glob("test_*.py"):
        if test_file.name in {"test_dashboard_display_coverage.py", "test_glossary_analysis_pages.py"}:
            continue
        text = test_file.read_text(encoding="utf-8")
        functions.update(re.findall(r"^def (test_[A-Za-z0-9_]+)\(", text, flags=re.MULTILINE))
    return functions


def _find_code_file(ref: CodeReference) -> Path:
    matches = sorted(ROOT.glob(ref.file_glob))
    assert matches, f"No file matches {ref.file_glob}"
    assert len(matches) == 1, f"Expected one file for {ref.file_glob}, got {matches}"
    return matches[0]


def _source_has_function(path: Path, function_name: str) -> bool:
    text = path.read_text(encoding="utf-8")
    return bool(re.search(rf"^def {re.escape(function_name)}\(", text, flags=re.MULTILINE))


def test_lineage_registry_has_expected_analysis_ids():
    specs = iter_lineage_specs()
    ids = [spec.lineage_id for spec in specs]

    assert set(ids) == EXPECTED_ANALYSIS_IDS
    assert len(ids) == len(set(ids))


def test_lineage_registry_specs_have_required_content():
    for spec in iter_lineage_specs():
        assert isinstance(spec, LineageSpec)
        assert spec.lineage_id
        assert spec.label
        assert spec.page
        assert spec.section
        assert spec.display_type
        assert spec.unit
        assert spec.data_basis
        assert spec.sources
        assert spec.calculations
        assert spec.formula
        assert spec.filters
        assert spec.data_lineage
        assert spec.tests
        assert spec.validation_status


def test_lineage_registry_references_existing_code_functions():
    for spec in iter_lineage_specs():
        for ref in spec.calculations:
            path = _find_code_file(ref)
            assert _source_has_function(path, ref.function_name), (
                f"{spec.lineage_id} references missing function "
                f"{ref.function_name} in {path}"
            )


def test_lineage_registry_references_existing_tests():
    available_functions = _available_test_functions()
    available_functions.add("test_lineage_registry_contains_data_quality_specs")
    referenced_functions = {
        test_name
        for spec in iter_lineage_specs()
        for test_name in spec.tests
    }

    missing = sorted(referenced_functions - available_functions)
    assert missing == []


def test_lineage_registry_contains_data_quality_specs():
    specs = {spec.lineage_id: spec for spec in iter_lineage_specs()}

    assert specs["8-17"].section == "Datenqualitaet"
    assert specs["9-17"].section == "Datenqualitaet"
    assert specs["8-17"].validation_status == "technisch teilweise bestaetigt"
    assert specs["9-17"].validation_status == "technisch teilweise bestaetigt"


def test_lineage_registry_lookup_and_report_dataframe():
    specs = get_lineage_specs(["9-14", "10-03"])
    report = lineage_report_dataframe(["9-14", "10-03"])

    assert get_lineage_spec("9-14").label == "Organisationseinheiten Rangliste"
    assert [spec.lineage_id for spec in specs] == ["9-14", "10-03"]
    assert isinstance(report, pd.DataFrame)
    assert report["Lineage-ID"].tolist() == ["9-14", "10-03"]
    assert {
        "Lineage-ID",
        "Label",
        "Seite",
        "Bereich",
        "Formel",
        "Filterwirkung",
        "Data Lineage",
        "Testnachweis",
    }.issubset(report.columns)


def test_lineage_registry_unknown_id_raises_helpful_error():
    try:
        get_lineage_spec("missing")
    except KeyError as exc:
        assert "Unknown lineage id: missing" in str(exc)
    else:
        raise AssertionError("Expected KeyError for unknown lineage id")
