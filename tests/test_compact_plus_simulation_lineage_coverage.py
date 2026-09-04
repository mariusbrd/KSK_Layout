from __future__ import annotations

import ast
from pathlib import Path

from utils.compact_simulation_export import DEFAULT_COMPACT_SIMULATION_LINEAGE_IDS


ROOT = Path(__file__).resolve().parents[1]
PAGE = next((ROOT / "pages").glob("*_Kompakt_plus_Simulation.py"))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def test_compact_plus_simulation_page_uses_lazy_export_with_default_lineage():
    tree = ast.parse(PAGE.read_text(encoding="utf-8-sig"))

    export_builder_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "build_compact_simulation_export_bytes"
    ]
    lazy_export_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "lazy_excel_download_button_compat"
    ]

    assert len(export_builder_calls) == 1
    assert not any(keyword.arg == "lineage_ids" for keyword in export_builder_calls[0].keywords)
    assert len(lazy_export_calls) == 1


def test_compact_plus_simulation_default_lineage_covers_workbook_and_downstream_views():
    assert DEFAULT_COMPACT_SIMULATION_LINEAGE_IDS[0] == "7-01"
    assert {"10-01", "10-07", "11-01", "11-05"}.issubset(DEFAULT_COMPACT_SIMULATION_LINEAGE_IDS)
