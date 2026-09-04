from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORECAST_PAGES = {
    "abgaenge": next((ROOT / "pages").glob("*_Prognose_Abgänge.py")),
    "zugaenge": next((ROOT / "pages").glob("*_Prognose_Zugänge.py")),
    "hybrid": next((ROOT / "pages").glob("*_Prognose_Hybrid.py")),
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


def _keyword_value(node: ast.Call, keyword_name: str):
    for keyword in node.keywords:
        if keyword.arg == keyword_name:
            try:
                return ast.literal_eval(keyword.value)
            except Exception:
                return ast.unparse(keyword.value)
    return None


def _download_calls(path: Path) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Call) and _call_name(node) == "download_button"
    ]


def test_forecast_pages_do_not_define_excel_exports_without_lineage():
    forbidden_calls = {"ExcelWriter", "to_excel", "export_to_excel", "write_lineage_sheet"}
    offending: dict[str, list[str]] = {}

    for name, path in FORECAST_PAGES.items():
        calls = [
            _call_name(node)
            for node in ast.walk(_parse(path))
            if isinstance(node, ast.Call) and _call_name(node) in forbidden_calls
        ]
        if calls:
            offending[name] = calls

    assert offending == {}


def test_attrition_page_exports_reason_detail_lists_as_csv():
    calls = _download_calls(FORECAST_PAGES["abgaenge"])

    csv_calls = [
        call
        for call in calls
        if _keyword_value(call, "mime") == "text/csv"
        and str(_keyword_value(call, "file_name")).startswith("f'abgaenge_")
    ]

    assert len(csv_calls) == 1
    assert _call_name(csv_calls[0].keywords[1].value) == "to_csv_bytes"


def test_hiring_page_has_no_download_exports():
    assert _download_calls(FORECAST_PAGES["zugaenge"]) == []


def test_hybrid_page_exports_combined_event_list_as_csv():
    calls = _download_calls(FORECAST_PAGES["hybrid"])

    csv_calls = [
        call
        for call in calls
        if _keyword_value(call, "mime") == "text/csv"
        and _keyword_value(call, "file_name") == "hybrid_prognose_details.csv"
    ]

    assert len(csv_calls) == 1
    data_keyword = next(keyword for keyword in csv_calls[0].keywords if keyword.arg == "data")
    assert isinstance(data_keyword.value, ast.Call)
    assert _call_name(data_keyword.value) == "to_csv_bytes"
