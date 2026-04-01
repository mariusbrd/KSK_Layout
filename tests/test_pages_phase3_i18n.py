from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from utils import i18n


class DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _dummy_columns(spec):
    count = spec if isinstance(spec, int) else len(spec)
    return [DummyContext() for _ in range(count)]


def _load_page_module(pattern: str, module_name: str, *, unit_testing: bool = False):
    page_path = next((ROOT / "pages").glob(pattern))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    if unit_testing:
        module._UNIT_TESTING = True
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clean_session_state(monkeypatch):
    import streamlit as st

    monkeypatch.setattr(st, "session_state", {})


def test_settings_page_renders_core_texts_in_english(monkeypatch):
    import streamlit as st
    from utils import settings_loader

    module = _load_page_module("*_Einstellungen.py", "settings_page_test_module", unit_testing=True)

    captured = {
        "titles": [],
        "subheaders": [],
        "captions": [],
        "expanders": [],
        "form_submit_labels": [],
        "buttons": [],
    }

    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "show_reload_success": False,
            "global_uploads": {},
            "tvoed_available": False,
            "tvoed_lookup": {},
            "employer_cost_factor": 1.25,
        }
    )

    monkeypatch.setattr(module, "render_metric_selector_only", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "load_cluster_mappings", lambda *args, **kwargs: ({}, {}))
    monkeypatch.setattr(settings_loader, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(settings_loader, "set_setting", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "save_user_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "load_user_settings", lambda *args, **kwargs: {})

    monkeypatch.setattr(module.st, "title", lambda text, *args, **kwargs: captured["titles"].append(text))
    monkeypatch.setattr(module.st, "subheader", lambda text, *args, **kwargs: captured["subheaders"].append(text))
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "success", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "download_button", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "container", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "expander", lambda label, *args, **kwargs: captured["expanders"].append(label) or DummyContext())
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "columns", _dummy_columns)
    monkeypatch.setattr(module.st, "spinner", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "file_uploader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "button", lambda label, **kwargs: captured["buttons"].append(label) or False)
    monkeypatch.setattr(module.st, "form_submit_button", lambda label, **kwargs: captured["form_submit_labels"].append(label) or False)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "checkbox", lambda label, value=False, **kwargs: value)
    monkeypatch.setattr(module.st, "number_input", lambda label, value=None, min_value=None, **kwargs: value if value is not None else min_value)
    monkeypatch.setattr(module.st, "selectbox", lambda label, options, index=0, **kwargs: options[index])
    monkeypatch.setattr(module.st, "text_input", lambda label, value="", **kwargs: value)

    module.render_settings_page()

    assert captured["titles"] == ["Settings"]
    assert "Data management" in captured["subheaders"]
    assert "Simulation parameters" in captured["subheaders"]
    assert any("Upload files" in label for label in captured["expanders"])
    assert any("Custom clusters (Excel mapping)" in label for label in captured["expanders"])
    assert "Save simulation parameters" in captured["form_submit_labels"]
    assert "Reload data" in captured["buttons"]


def test_compact_plus_simulation_hero_is_localized(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt_plus_Simulation.py", "compact_plus_page_test_module")

    captured = {"titles": [], "captions": [], "context": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(module.st, "title", lambda text, *args, **kwargs: captured["titles"].append(text))
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))
    monkeypatch.setattr(module, "render_context_box", lambda label, text, **kwargs: captured["context"].append((label, text, kwargs)))

    module._render_hero()

    assert captured["titles"] == ["⚡ Compact plus Simulation"]
    assert captured["captions"] == ["Compact evaluation based on a workforce projected forward to a target date."]
    assert captured["context"][0][0] == "Simulation logic"
    assert captured["context"][0][1] == (
        "Forward projection of the workforce to a selected future date based on the existing attrition and hiring forecast logic."
    )
    assert captured["context"][0][2] == {"tone": "info"}


def test_compact_plus_simulation_main_warns_in_english_for_empty_filters(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt_plus_Simulation.py", "compact_plus_page_test_module_main")

    warnings = []
    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "compact_sim_signature": "sig",
            "compact_sim_prepared_df": pd.DataFrame({"x": [1]}),
            "compact_sim_metadata": {},
            "compact_sim_target_date_cached": pd.Timestamp("2026-03-27"),
        }
    )

    monkeypatch.setattr(module, "_inject_page_styles", lambda: None)
    monkeypatch.setattr(module, "_render_hero", lambda: None)
    monkeypatch.setattr(module, "load_compact_page_module", lambda: type("CompactDummy", (), {"prepare_compact_data": staticmethod(lambda df: df)})())
    monkeypatch.setattr(module, "get_current_stichtag", lambda: "2026-03-27")
    monkeypatch.setattr(module, "_build_simulation_signature", lambda **kwargs: "sig")
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (pd.DataFrame({"x": [1]}), pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])}), None, None))
    monkeypatch.setattr(module, "render_section_intro", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "filter_dataframe_by_view_filters", lambda df, *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(module, "get_filter_summary", lambda: "2 active filters")
    monkeypatch.setattr(module, "render_active_filter_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_context_box", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)

    monkeypatch.setattr(module.st, "columns", _dummy_columns)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: warnings.append(text))

    module.main()

    assert warnings == ["No data for the selected filters."]


def test_compact_plus_simulation_main_uses_localized_control_intro(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt_plus_Simulation.py", "compact_plus_page_test_module_controls")

    captured = {"intro": [], "context": []}
    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "compact_sim_signature": "sig",
            "compact_sim_prepared_df": pd.DataFrame({"PersNr": ["1"], "Organisationseinheit": ["A"]}),
            "compact_sim_metadata": {},
            "compact_sim_target_date_cached": pd.Timestamp("2026-03-27"),
        }
    )

    class CompactDummy:
        @staticmethod
        def render_ist_koepfe_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_mak_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_eur_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_soll_koepfe_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_vs_soll_mak_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_vs_soll_eur_tab(*args, **kwargs):
            return None

    monkeypatch.setattr(module, "_inject_page_styles", lambda: None)
    monkeypatch.setattr(module, "_render_hero", lambda: None)
    monkeypatch.setattr(module, "load_compact_page_module", lambda: CompactDummy())
    monkeypatch.setattr(module, "get_current_stichtag", lambda: "2026-03-27")
    monkeypatch.setattr(module, "_build_simulation_signature", lambda **kwargs: "sig")
    monkeypatch.setattr(
        module,
        "load_and_prepare_data",
        lambda: (
            pd.DataFrame({"PersNr": ["1"], "Organisationseinheit": ["A"]}),
            pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])}),
            None,
            None,
        ),
    )
    monkeypatch.setattr(module, "render_section_intro", lambda title, text, **kwargs: captured["intro"].append((title, text, kwargs)))
    monkeypatch.setattr(module, "render_context_box", lambda label, text, **kwargs: captured["context"].append((label, text, kwargs)))
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "filter_dataframe_by_view_filters", lambda df, *_args, **_kwargs: df)
    monkeypatch.setattr(module, "get_filter_summary", lambda: "2 active filters")
    monkeypatch.setattr(module, "render_active_filter_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "get_active_view_filters", lambda: {})
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "get_global_metric_view", lambda: "FTE")
    monkeypatch.setattr(module, "_render_status_box", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_render_summary_cards", lambda *args, **kwargs: None)

    monkeypatch.setattr(module.st, "columns", _dummy_columns)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "tabs", lambda labels, **kwargs: [DummyContext() for _ in labels])

    module.main()

    assert captured["intro"][0] == (
        "Control simulation",
        "Choose the target date, calculate the workforce and then analyze it like on the compact page.",
        {},
    )
