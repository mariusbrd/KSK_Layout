from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PAGE = next((ROOT / "pages").glob("*_Einstellungen.py"))
SETUP_WIZARD = ROOT / "components" / "setup_wizard.py"


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _call_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return [_call_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_settings_page_excel_downloads_use_lineage_enabled_builders():
    source = SETTINGS_PAGE.read_text(encoding="utf-8-sig")
    call_names = _call_names(SETTINGS_PAGE)

    assert call_names.count("build_integrity_report_excel") == 1
    assert call_names.count("generate_upload_template_bytes") == 4
    assert call_names.count("generate_tvoed_template_bytes") == 1
    assert call_names.count("generate_template_bytes") == 1
    assert call_names.count("build_complete_glossary_workbook_bytes") == 1
    assert source.count('mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"') == 3


def test_setup_wizard_excel_downloads_use_lineage_enabled_exports():
    source = SETUP_WIZARD.read_text(encoding="utf-8-sig")
    call_names = _call_names(SETUP_WIZARD)

    assert call_names.count("export_to_excel") == 1
    assert call_names.count("export_mapping_report") == 1
    assert 'file_name="jobfamilies_export.xlsx"' in source
    assert 'file_name="mapping_report.xlsx"' in source
