from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"


def _load_app_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clean_streamlit_state(monkeypatch):
    monkeypatch.setattr(st, "session_state", {})
    monkeypatch.setattr(st, "set_page_config", lambda **kwargs: None)
    monkeypatch.setattr(st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "logo", lambda *args, **kwargs: None)
    monkeypatch.setattr(st.cache_data, "clear", lambda: None)


def test_app_main_runs_without_global_shell_header(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    logo_calls = []
    monkeypatch.setattr(st, "logo", lambda path, **kwargs: logo_calls.append((path, kwargs)))
    module = _load_app_module("app_layout_test")
    calls = {"run": 0}

    class DummyNavigator:
        def run(self):
            calls["run"] += 1

    monkeypatch.setattr(module, "needs_setup", lambda: False)
    monkeypatch.setattr(module, "render_setup_wizard", lambda: True)
    monkeypatch.setattr(module.st, "navigation", lambda pages: DummyNavigator())

    module.main()

    assert calls["run"] == 1
    assert "app.header.title" not in APP_PATH.read_text(encoding="utf-8")
    assert len(logo_calls) == 1
    assert logo_calls[0][0].endswith("assets\\sidebar_brand.svg")
    assert logo_calls[0][1] == {"size": "large"}


def test_inject_ui_theme_contains_full_width_sidebar_logo_styles(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    from components.ui_shell import inject_ui_theme

    markdown_calls: list[str] = []
    monkeypatch.setattr(st, "session_state", {})
    monkeypatch.setattr(
        st,
        "markdown",
        lambda body, **kwargs: markdown_calls.append(body),
    )

    inject_ui_theme()

    assert len(markdown_calls) == 1
    css = markdown_calls[0]
    assert 'section[data-testid="stSidebar"] .e3rr4jk4' in css
    assert 'section[data-testid="stSidebar"] .e3rr4jk4 .stLogo' in css
    assert 'div:has(> [data-testid="stLogoLink"])' in css
    assert 'section[data-testid="stSidebar"] [data-testid="stLogoLink"]' in css
    assert 'width: 100% !important;' in css
    assert 'section[data-testid="stSidebar"] [data-testid="stLogo"]' in css
    assert 'height: auto !important;' in css
    assert 'max-height: none !important;' in css
    assert 'min-height: 4.5rem !important;' in css
