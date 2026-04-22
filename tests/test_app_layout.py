from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd
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


def test_inject_ui_theme_reinserts_css_even_when_session_flag_exists(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    from components.ui_shell import inject_ui_theme

    markdown_calls: list[str] = []
    monkeypatch.setattr(st, "session_state", {"_dashboard_ui_theme_injected": True})
    monkeypatch.setattr(
        st,
        "markdown",
        lambda body, **kwargs: markdown_calls.append(body),
    )

    inject_ui_theme()

    assert len(markdown_calls) == 1
    assert '<style id="dashboard-ui-theme">' in markdown_calls[0]


@pytest.mark.parametrize(
    ("helper_name", "args", "expected_fragment"),
    [
        ("render_app_shell_header", ("Title", "Subtitle"), "dashboard-app-shell"),
        ("render_page_header", ("Title", "Subtitle", "Note"), "dashboard-page-header"),
        ("render_context_box", ("Label", "Text"), "dashboard-context-box"),
        ("render_section_intro", ("Title", "Subtitle"), "dashboard-section-intro"),
    ],
)
def test_ui_shell_helpers_reinsert_css_on_render_even_with_session_flag(monkeypatch, helper_name, args, expected_fragment):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    from components import ui_shell

    markdown_calls: list[str] = []
    monkeypatch.setattr(st, "session_state", {"_dashboard_ui_theme_injected": True})
    monkeypatch.setattr(
        st,
        "markdown",
        lambda body, **kwargs: markdown_calls.append(body),
    )

    getattr(ui_shell, helper_name)(*args)

    assert len(markdown_calls) >= 2
    assert '<style id="dashboard-ui-theme">' in markdown_calls[0]
    assert expected_fragment in markdown_calls[1]


def test_render_active_filter_banner_reinserts_css_on_render_even_with_session_flag(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    from components.ui_shell import render_active_filter_banner

    markdown_calls: list[str] = []
    info_calls: list[str] = []
    monkeypatch.setattr(st, "session_state", {"_dashboard_ui_theme_injected": True})
    monkeypatch.setattr(
        st,
        "markdown",
        lambda body, **kwargs: markdown_calls.append(body),
    )
    monkeypatch.setattr(st, "info", lambda body, **kwargs: info_calls.append(body))

    render_active_filter_banner("2 active filters")

    assert len(markdown_calls) == 1
    assert '<style id="dashboard-ui-theme">' in markdown_calls[0]
    assert len(info_calls) == 1


def test_render_data_status_surfaces_runtime_status_message_in_sidebar(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    from components import sidebar as sidebar_module
    from dataloader.source_service import DataSourceOrigin

    markdown_calls: list[str] = []
    note_calls: list[str] = []
    monkeypatch.setattr(
        st,
        "session_state",
        {
            "global_uploads": {},
            "data_status_message": "Using synthetic fallback data.",
            "data_status_level": "info",
        },
    )
    monkeypatch.setattr(st, "markdown", lambda body, **kwargs: markdown_calls.append(body))
    monkeypatch.setattr(sidebar_module, "_render_sidebar_note", lambda body: note_calls.append(body))
    monkeypatch.setattr(
        sidebar_module.SourceService,
        "GROUPS",
        {"Mitarbeiterinformationen": object()},
    )
    monkeypatch.setattr(
        sidebar_module.SourceService,
        "derive_group_status",
        lambda *args, **kwargs: type(
            "Status",
            (),
            {"origin": DataSourceOrigin.SYNTHETIC, "completeness_label": "synthetic"},
        )(),
    )

    sidebar_module.render_data_status()

    assert markdown_calls
    assert note_calls == ["ℹ️ Using synthetic fallback data."]


def test_render_data_status_uses_resolver_based_cluster_status(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    from components import sidebar as sidebar_module
    from dataloader.cluster_resolver import ActiveClusterSource, store_active_cluster_source_in_session

    markdown_calls: list[str] = []
    monkeypatch.setattr(st, "session_state", {"global_uploads": {}})
    store_active_cluster_source_in_session(
        st.session_state,
        ActiveClusterSource(
            mode="ui_upload",
            subtype="ui_upload.persisted_local_copy",
            status="active",
            is_active=True,
            is_valid=True,
            priority_rank=2,
            display_label="UI-Upload (persistiert)",
            description="Persistierte Clusterquelle",
            source_path="C:/tmp/cluster_mapping.xlsx",
            session_key=None,
            persisted_local_path="C:/tmp/cluster_mapping.xlsx",
            filename="cluster_mapping.xlsx",
            file_exists=True,
            content_hash="hash",
            source_signature="sig",
            activated_at=None,
            last_modified_at=None,
            oe_mapping_count=1,
            jf_mapping_count=1,
            resolution_reason="test",
            validation_errors=[],
            fallback_from=None,
            debug_meta={},
        ),
    )
    monkeypatch.setattr(st, "markdown", lambda body, **kwargs: markdown_calls.append(body))

    sidebar_module.render_data_status()

    assert markdown_calls
    assert "Upload" in markdown_calls[0] or "upload" in markdown_calls[0]


def test_render_global_filters_uses_refined_sidebar_section_order(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))

    from components import sidebar as sidebar_module

    class DummyContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    section_calls: list[str] = []
    summary_calls: list[str] = []

    monkeypatch.setattr(
        st,
        "session_state",
        {
            "global_uploads": {},
            "selected_org_units": [],
            "selected_jobfamilies": [],
            "selected_cohorts": [],
            "selected_genders": ["m", "w"],
            "selected_employment": ["Vollzeit", "Teilzeit", "Inaktiv"],
            "selected_education": [],
            "selected_atz_status": ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
            "selected_oe_clusters": [],
            "selected_jf_clusters": [],
        },
    )
    monkeypatch.setattr(sidebar_module, "_render_sidebar_block_intro", lambda title, caption=None, icon=None: section_calls.append(title))
    monkeypatch.setattr(sidebar_module, "_render_sidebar_summary", lambda text: summary_calls.append(text))
    monkeypatch.setattr(sidebar_module, "_render_sidebar_caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar_module, "_render_sidebar_section", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar_module, "render_data_status", lambda *args, **kwargs: section_calls.append("DATA_STATUS_BODY"))
    monkeypatch.setattr(sidebar_module, "render_global_metric_selector", lambda: "MAK")
    monkeypatch.setattr(sidebar_module, "render_language_switcher", lambda: None)
    monkeypatch.setattr(sidebar_module, "render_cohort_editor", lambda: None)
    monkeypatch.setattr(sidebar_module, "get_filter_summary", lambda: "1 aktiver Filter")
    monkeypatch.setattr(sidebar_module, "inject_ui_theme", lambda: None)
    monkeypatch.setattr(sidebar_module, "initialize_language_state", lambda: None)
    monkeypatch.setattr(st, "sidebar", DummyContext())
    monkeypatch.setattr(st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "date_input", lambda *args, value=None, **kwargs: value)
    monkeypatch.setattr(st, "multiselect", lambda *args, default=None, **kwargs: default or [])
    monkeypatch.setattr(st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(st, "checkbox", lambda *args, value=False, **kwargs: value)
    monkeypatch.setattr(st, "columns", lambda spec: [DummyContext() for _ in range(spec if isinstance(spec, int) else len(spec))])
    monkeypatch.setattr(st, "popover", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(st, "rerun", lambda *args, **kwargs: None)

    snapshot_df = pd.DataFrame(
        {
            "Organisationseinheit": ["OE A"],
            "Kürzel OrgEinheit": ["100"],
            "Jobfamily": ["IT"],
            "Ausbildung": ["Bankfachwirt"],
            "OE-Cluster": ["Cluster A"],
            "JF-Cluster": ["Family A"],
        }
    )
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-01"])})

    sidebar_module.render_global_filters(snapshot_df, history_df)

    assert section_calls[:6] == [
        "Dashboard Steuerung",
        "Ansicht",
        "Primäre Filter",
        "Aktive Auswahl",
        "Beschäftigte",
        "Datenstatus",
    ]
    assert "Weitere Filter" not in section_calls
    assert "1 aktiver Filter" in summary_calls
