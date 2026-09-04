from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_app_navigation_sections_keep_expected_order():
    source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    section_names: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value in {
                    "Analyse",
                    "Prognosen",
                    "Simulation",
                    "Administration",
                }:
                    section_names.append(key.value)
            if section_names:
                break

    assert section_names == ["Analyse", "Prognosen", "Simulation", "Administration"]


def test_sidebar_navigation_section_headers_are_visually_styled():
    source = (ROOT / "components" / "ui_shell.py").read_text(encoding="utf-8-sig")

    assert 'data-testid="stNavSectionHeader"' in source
    assert 'data-testid="stSidebarNavSeparator"' in source
    assert "text-transform: uppercase" in source
