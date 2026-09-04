from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES_ROOT = ROOT / "pages"

EXPORT_SCOPE_BY_PAGE = {
    "1_\u26a1_Kompakt.py": "excel_lineage",
    "2_\u2699\ufe0f_Einstellungen.py": "excel_lineage",
    "3_\U0001f4c9_Prognose_Abg\u00e4nge.py": "csv_only",
    "4_\U0001f4c8_Prognose_Zug\u00e4nge.py": "no_downloads",
    "5_\U0001f3e2_Prognose_Hybrid.py": "csv_only",
    "6_\U0001f50e_Deep_Dive_Exklusionsgruppen.py": "no_downloads",
    "7_\u26a1_Kompakt_plus_Simulation.py": "excel_lineage",
    "8_\u2699\ufe0f_Simulationsparameter.py": "no_downloads",
    "8_\U0001f4bc_Jobfamily_Analyse.py": "excel_lineage",
    "9_\U0001f3e2_Organisationseinheiten_Analyse.py": "excel_lineage",
    "10_\U0001f3e2_Organisationseinheiten_Simulation.py": "delegates_to_analysis",
    "11_\U0001f4bc_Jobfamily_Simulation.py": "delegates_to_analysis",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _calls(path: Path, names: set[str]) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Call) and _call_name(node) in names
    ]


def _keyword_value(node: ast.Call, keyword_name: str):
    for keyword in node.keywords:
        if keyword.arg == keyword_name:
            try:
                return ast.literal_eval(keyword.value)
            except Exception:
                return ast.unparse(keyword.value)
    return None


def test_every_active_page_has_an_export_scope_classification():
    page_names = {path.name for path in PAGES_ROOT.glob("*.py")}

    assert page_names == set(EXPORT_SCOPE_BY_PAGE)


def test_active_page_export_scope_classification_matches_code():
    excel_calls = {"ExcelWriter", "to_excel", "export_to_excel"}
    download_calls = {"download_button", "download_button_compat", "lazy_excel_download_button_compat"}

    for page_name, scope in EXPORT_SCOPE_BY_PAGE.items():
        path = PAGES_ROOT / page_name
        downloads = _calls(path, download_calls)
        excel = _calls(path, excel_calls)

        if scope == "no_downloads":
            assert downloads == [], page_name
            assert excel == [], page_name
        elif scope == "delegates_to_analysis":
            assert downloads == [], page_name
            assert excel == [], page_name
        elif scope == "csv_only":
            assert downloads, page_name
            assert excel == [], page_name
            file_names = [_keyword_value(call, "file_name") for call in downloads]
            assert all(".csv" in str(file_name) for file_name in file_names), page_name
        elif scope == "excel_lineage":
            assert downloads or excel, page_name
        else:
            raise AssertionError(f"Unknown export scope {scope!r} for {page_name}")
