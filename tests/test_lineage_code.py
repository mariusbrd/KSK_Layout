from __future__ import annotations

import json

from utils.lineage_code import (
    DEFAULT_BASELINE_PATH,
    build_function_lineage_index,
    build_function_lineage_index_for_refs,
    build_hash_baseline,
    compare_hash_baseline,
    compare_hash_baseline_for_refs,
    function_lineage_dataframe,
)
from utils.lineage_registry import get_lineage_spec


EXPECTED_FUNCTION_REFERENCE_COUNT = 72


def test_function_lineage_resolves_registry_references():
    index = build_function_lineage_index()

    assert len(index) == EXPECTED_FUNCTION_REFERENCE_COUNT
    for key, lineage in index.items():
        assert key == f"{lineage.file_path}:{lineage.function_name}"
        assert lineage.file_path.endswith(".py")
        assert lineage.start_line > 0
        assert lineage.end_line >= lineage.start_line
        assert len(lineage.source_hash) == 16


def test_function_lineage_dataframe_contract():
    df = function_lineage_dataframe()

    assert list(df.columns) == [
        "Code-Key",
        "Datei",
        "Funktion",
        "Startzeile",
        "Endzeile",
        "Source-Hash",
    ]
    assert len(df) == EXPECTED_FUNCTION_REFERENCE_COUNT
    assert df["Code-Key"].is_unique
    assert df["Source-Hash"].str.len().eq(16).all()


def test_hash_baseline_matches_current_source():
    comparison = compare_hash_baseline()

    assert DEFAULT_BASELINE_PATH.exists()
    assert len(comparison) == EXPECTED_FUNCTION_REFERENCE_COUNT
    assert set(comparison["Status"]) == {"ok"}


def test_hash_baseline_detects_changed_function(tmp_path):
    baseline = build_hash_baseline()
    changed_key = sorted(baseline)[0]
    modified = dict(baseline)
    modified[changed_key] = "0000000000000000"

    baseline_path = tmp_path / "lineage_function_hashes.json"
    baseline_path.write_text(json.dumps(modified, indent=2, sort_keys=True), encoding="utf-8")

    comparison = compare_hash_baseline(baseline_path=baseline_path)
    changed = comparison.loc[comparison["Code-Key"] == changed_key].iloc[0]

    assert changed["Status"] == "changed"
    assert changed["Expected-Hash"] == "0000000000000000"
    assert changed["Actual-Hash"] == baseline[changed_key]


def test_selected_function_lineage_resolves_only_requested_refs():
    refs = get_lineage_spec("9-14").calculations

    selected = build_function_lineage_index_for_refs(refs)
    comparison = compare_hash_baseline_for_refs(refs)

    assert len(selected) == len(refs)
    assert set(comparison["Code-Key"]) == set(selected)
    assert set(comparison["Status"]) == {"ok"}
