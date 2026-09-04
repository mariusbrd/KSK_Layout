from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KOMPAKT_PAGE = next((ROOT / "pages").glob("*_Kompakt.py"))


def _parse_kompakt_page() -> ast.Module:
    return ast.parse(KOMPAKT_PAGE.read_text(encoding="utf-8-sig"))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def test_kompakt_export_calls_declare_lineage_ids():
    tree = _parse_kompakt_page()
    missing: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) != "export_to_excel":
            continue
        if not any(keyword.arg == "lineage_ids" for keyword in node.keywords):
            missing.append(node.lineno)

    assert missing == []


def test_kompakt_direct_workbooks_write_lineage_sheet():
    tree = _parse_kompakt_page()
    missing: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue

        opens_excel_writer = any(
            isinstance(item.context_expr, ast.Call)
            and _call_name(item.context_expr) == "ExcelWriter"
            for item in node.items
        )
        if not opens_excel_writer:
            continue

        writes_lineage = any(
            isinstance(child, ast.Call) and _call_name(child) == "write_lineage_sheet"
            for child in ast.walk(node)
        )
        if not writes_lineage:
            missing.append(node.lineno)

    assert missing == []


def test_kompakt_generic_lineage_specs_cover_current_generic_elements():
    source = KOMPAKT_PAGE.read_text(encoding="utf-8-sig")

    expected_bindings = {
        'lineage_ids=("1-06",)': "Planebene-Verguetung",
        'lineage_ids=("1-07",)': "Qualifikationsspannweite",
        'lineage_ids=("1-08",)': "Standard-Breakdown",
        'lineage_ids=("1-09",)': "Standard-Vergleich",
        'lineage_ids=("1-10",)': "Tarif-Breakdown",
        'lineage_ids=("1-11",)': "Tarif-Vergleich",
    }

    for binding, label in expected_bindings.items():
        assert binding in source, label
