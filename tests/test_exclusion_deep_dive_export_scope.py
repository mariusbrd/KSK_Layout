from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = next((ROOT / "pages").glob("*_Deep_Dive_Exklusionsgruppen.py"))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def test_exclusion_deep_dive_has_no_file_exports_without_lineage():
    tree = ast.parse(PAGE.read_text(encoding="utf-8-sig"))
    export_calls = {
        _call_name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node)
        in {
            "download_button",
            "download_button_compat",
            "ExcelWriter",
            "to_excel",
            "to_csv",
            "export_to_excel",
            "write_lineage_sheet",
        }
    }

    assert export_calls == set()
