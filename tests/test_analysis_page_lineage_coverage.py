from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_PAGES = [
    next((ROOT / "pages").glob("*_Organisationseinheiten_Analyse.py")),
    next((ROOT / "pages").glob("*_Jobfamily_Analyse.py")),
]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _export_calls_without_lineage(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    missing: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) != "export_to_excel":
            continue
        if not any(keyword.arg == "lineage_ids" for keyword in node.keywords):
            missing.append(node.lineno)
    return missing


def test_analysis_page_export_calls_declare_lineage_ids():
    missing_by_page = {
        path.name: _export_calls_without_lineage(path)
        for path in ANALYSIS_PAGES
    }

    assert missing_by_page == {
        "9_\U0001f3e2_Organisationseinheiten_Analyse.py": [],
        "8_\U0001f4bc_Jobfamily_Analyse.py": [],
    }


def test_analysis_page_lineage_ids_match_expected_display_blocks():
    org_source = ANALYSIS_PAGES[0].read_text(encoding="utf-8-sig")
    job_source = ANALYSIS_PAGES[1].read_text(encoding="utf-8-sig")

    assert '_org_lineage_ids(value_label, "9-14", "10-02")' in org_source
    assert 'lineage_ids = ["10-03", "10-04", "10-07"]' in org_source
    assert '"9-16" if split_col == "TrfGr" else "9-15"' in org_source
    assert '"10-05"' in org_source

    assert '_jobfamily_lineage_ids(is_simulation, "8-14", "11-02")' in job_source
    assert '_jobfamily_lineage_ids(is_simulation, "8-17", "11-05")' in job_source
    assert '"8-16" if split_col == "TrfGr" else "8-15"' in job_source
    assert '"11-03"' in job_source
