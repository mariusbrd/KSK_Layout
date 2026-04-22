from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dataloader.cluster_resolver import ClusterMappingBundle
from utils import i18n


class DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyColumn(DummyContext):
    def date_input(self, label, value=None, **kwargs):
        return value

    def number_input(self, label, *args, value=None, min_value=None, **kwargs):
        if value is not None:
            return value
        if len(args) >= 3:
            return args[2]
        if len(args) >= 1:
            return args[0]
        return min_value

    def selectbox(self, label, options, index=0, **kwargs):
        return options[index]

    def slider(self, label, *args, value=None, **kwargs):
        if value is not None:
            return value
        if len(args) >= 3:
            return args[2]
        return args[-1] if args else None

    def checkbox(self, label, value=False, **kwargs):
        return value

    def radio(self, label, options, index=0, **kwargs):
        return options[index]

    def multiselect(self, label, options, default=None, **kwargs):
        return default or []


def _dummy_columns(spec):
    count = spec if isinstance(spec, int) else len(spec)
    return [DummyColumn() for _ in range(count)]


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


def test_attrition_page_helpers_are_localized_in_english(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Prognose_Abgänge.py", "attrition_page_phase4", unit_testing=True)
    captured = {"titles": [], "captions": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(module.st, "title", lambda text, *args, **kwargs: captured["titles"].append(text))
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))

    module._render_page_intro()

    assert captured["titles"] == ["📉 Forecast: Attrition"]
    assert captured["captions"] == [
        "Forecast of departures (partial retirement, retirement, resignations) with a clear split between FTE and headcount."
    ]
    assert module._get_result_tab_labels() == [
        "📊 Overview & trends",
        "🎯 Driver details",
        "📋 People lists / export",
    ]


def test_attrition_page_main_warns_in_english_for_empty_filters(monkeypatch):
    import streamlit as st
    import dataloader.loader as loader

    module = _load_page_module("*_Prognose_Abgänge.py", "attrition_page_phase4_main", unit_testing=True)
    warnings = []

    snapshot_df = pd.DataFrame(
        {
            "PersNr": ["1"],
            "GebDatum": pd.to_datetime(["1980-01-01"]),
            "Eintritt": pd.to_datetime(["2010-01-01"]),
            "Austritt": [pd.NaT],
            "Status kundenindividuell": ["Aktiv"],
            "Sollarbeitszeit": [39.0],
            "Organisationseinheit": ["A"],
        }
    )
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})

    st.session_state.clear()
    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "global_uploads": {},
            "abgaenge_global_result": {
                "events_person_level": pd.DataFrame(),
                "cluster_source_signature": "cluster-sig-test",
            },
            "abgaenge_cluster_source_signature": "cluster-sig-test",
        }
    )

    monkeypatch.setattr(module, "get_current_stichtag", lambda: pd.Timestamp("2026-03-27"))
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (snapshot_df, history_df, None, None))
    monkeypatch.setattr(
        module,
        "_get_attrition_cluster_context",
        lambda summary: (
            SimpleNamespace(source_signature="cluster-sig-test"),
            ClusterMappingBundle(),
            "cluster-sig-test",
            False,
        ),
    )
    monkeypatch.setattr(module, "load_atz_data_cached", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(loader, "calculate_mak_vectorized", lambda df, *_args, **_kwargs: df.assign(MAK_Calculated=1.0))
    monkeypatch.setattr(module.JobFamilyService, "get_active_jobfamilies", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "apply_event_filters", lambda *args, **kwargs: (pd.DataFrame(), 0, 0))
    monkeypatch.setattr(module, "apply_filters", lambda df: pd.DataFrame())
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "get_effective_metric_view", lambda *args, **kwargs: ("MAK", False))

    monkeypatch.setattr(module.st, "sidebar", DummyContext())
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "columns", _dummy_columns)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: warnings.append(text))
    monkeypatch.setattr(module.st, "success", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "selectbox", lambda label, options, index=0, **kwargs: options[index])
    monkeypatch.setattr(module.st, "number_input", lambda label, value=None, min_value=None, **kwargs: value if value is not None else min_value)
    monkeypatch.setattr(module.st, "checkbox", lambda label, value=False, **kwargs: value)
    monkeypatch.setattr(module.st, "slider", lambda label, min_value=None, max_value=None, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "data_editor", lambda df, **kwargs: df)
    monkeypatch.setattr(module.st, "multiselect", lambda label, options, default=None, **kwargs: default or [])
    monkeypatch.setattr(module.st, "form_submit_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)

    module.main()

    assert warnings == ["⚠️ No data available after filtering."]


def test_attrition_settings_form_is_localized_in_english(monkeypatch):
    import streamlit as st
    import dataloader.loader as loader

    module = _load_page_module("*_Prognose_Abgänge.py", "attrition_page_phase4_form", unit_testing=True)
    captured = {
        "date_inputs": [],
        "number_inputs": [],
        "selectboxes": [],
        "checkboxes": [],
        "sliders": [],
        "radios": [],
        "multiselects": [],
        "captions": [],
        "infos": [],
        "markdowns": [],
    }

    snapshot_df = pd.DataFrame(
        {
            "PersNr": ["1"],
            "GebDatum": pd.to_datetime(["1980-01-01"]),
            "Eintritt": pd.to_datetime(["2010-01-01"]),
            "Austritt": [pd.NaT],
            "Status kundenindividuell": ["Aktiv"],
            "Sollarbeitszeit": [39.0],
            "Organisationseinheit": ["A"],
            "Jobfamily": ["JF1"],
        }
    )
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})

    st.session_state.clear()
    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "global_uploads": {},
            "abgaenge_global_result": {
                "events_person_level": pd.DataFrame(),
                "cluster_source_signature": "cluster-sig-test",
            },
            "abgaenge_cluster_source_signature": "cluster-sig-test",
        }
    )

    monkeypatch.setattr(module, "get_current_stichtag", lambda: pd.Timestamp("2026-03-27"))
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (snapshot_df, history_df, None, None))
    monkeypatch.setattr(
        module,
        "_get_attrition_cluster_context",
        lambda summary: (
            SimpleNamespace(source_signature="cluster-sig-test"),
            ClusterMappingBundle(),
            "cluster-sig-test",
            False,
        ),
    )
    monkeypatch.setattr(module, "load_atz_data_cached", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(loader, "calculate_mak_vectorized", lambda df, *_args, **_kwargs: df.assign(MAK_Calculated=1.0))
    monkeypatch.setattr(module.JobFamilyService, "get_active_jobfamilies", lambda *args, **kwargs: ["JF1"])
    monkeypatch.setattr(module.JobFamilyService, "get_available_years", lambda start, count: [2026, 2027])
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "apply_event_filters", lambda *args, **kwargs: (pd.DataFrame(), 0, 0))
    monkeypatch.setattr(module, "apply_filters", lambda df: pd.DataFrame())
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "get_effective_metric_view", lambda *args, **kwargs: ("MAK", False))

    monkeypatch.setattr(module.st, "sidebar", DummyContext())
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "columns", _dummy_columns)
    monkeypatch.setattr(module.st, "markdown", lambda text, *args, **kwargs: captured["markdowns"].append(text))
    monkeypatch.setattr(module.st, "info", lambda text, *args, **kwargs: captured["infos"].append(text))
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "success", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))
    monkeypatch.setattr(module.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module.st,
        "date_input",
        lambda label, value=None, **kwargs: captured["date_inputs"].append(label) or value,
    )
    monkeypatch.setattr(
        module.st,
        "selectbox",
        lambda label, options, index=0, **kwargs: captured["selectboxes"].append((label, list(options))) or options[index],
    )
    monkeypatch.setattr(
        module.st,
        "number_input",
        lambda label, value=None, min_value=None, **kwargs: captured["number_inputs"].append(label) or (value if value is not None else min_value),
    )
    monkeypatch.setattr(
        module.st,
        "checkbox",
        lambda label, value=False, **kwargs: captured["checkboxes"].append(label) or value,
    )
    monkeypatch.setattr(
        module.st,
        "slider",
        lambda label, min_value=None, max_value=None, value=None, **kwargs: captured["sliders"].append(label) or value,
    )
    monkeypatch.setattr(module.st, "data_editor", lambda df, **kwargs: df)
    monkeypatch.setattr(
        module.st,
        "multiselect",
        lambda label, options, default=None, **kwargs: (
            captured["multiselects"].append(label)
            or (["JF1"] if label == "Select job families" else (default or []))
        ),
    )
    monkeypatch.setattr(
        module.st,
        "radio",
        lambda label, options, index=0, **kwargs: captured["radios"].append(label) or options[index],
    )
    monkeypatch.setattr(module.st, "form_submit_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)

    module.main()

    assert "Actual reference date" in captured["date_inputs"]
    assert "Forecast end" in captured["date_inputs"]
    assert "Random seed" in captured["number_inputs"]
    assert "Frequency" in [label for label, _options in captured["selectboxes"]]
    assert any(label == "Frequency" and options == ["Month", "Quarter"] for label, options in captured["selectboxes"])
    assert "Partial retirement (incl. retirement after partial retirement)" in captured["checkboxes"]
    assert "Retirement" in captured["checkboxes"]
    assert "Resignation" in captured["checkboxes"]
    assert "New cases (base): 0.05 (Range 0.00–0.50)" in captured["sliders"]
    assert "Minimum age" in captured["number_inputs"]
    assert "Maximum age" in captured["number_inputs"]
    assert "Use detailed partial-retirement matrix" in captured["checkboxes"]
    assert "Dimension for partial retirement" in captured["radios"]
    assert "Retirement entry 65+: 0.90 (Range 0.00–1.00)" in captured["sliders"]
    assert "Early retirement 60-64: 0.10 (Range 0.00–1.00)" in captured["sliders"]
    assert "Base rate p.a.: 0.05 (Range 0.00–0.50)" in captured["sliders"]
    assert "Use detailed resignation matrix" in captured["checkboxes"]
    assert "Dimension" in captured["radios"]
    assert "##### 📅 Year-specific adjustments (job families)" in captured["markdowns"]
    assert "📈 More resignations (+50%)" in captured["captions"]
    assert "📉 Fewer resignations (-50%)" in captured["captions"]
    assert "Select job families" in captured["multiselects"]
    assert "Years for JF1" in captured["multiselects"]
    assert "Matrix: JobFamily (entry probability for eligible employees)" in captured["captions"]
    assert "Matrix: JobFamily × age" in captured["captions"]
    assert "Here you can define an increase (+50%) or reduction (-50%) of the resignation rate for specific years and job families." in captured["infos"]
    assert "Ist-Stichtag" not in captured["date_inputs"]
    assert "Prognose-Ende" not in captured["date_inputs"]
    assert all("Monat" not in options for _label, options in captured["selectboxes"])
    assert "Kündigung" not in captured["checkboxes"]


def test_hiring_page_helpers_are_localized_in_english(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Prognose_Zugänge.py", "hiring_page_phase4")
    captured = {"titles": [], "captions": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(module.st, "title", lambda text, *args, **kwargs: captured["titles"].append(text))
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))

    module._render_page_intro()

    assert captured["titles"] == ["📈 Forecast: Hiring"]
    assert captured["captions"] == [
        "Forecast of new hires (apprentices, trainees, external hires) and their effect on headcount and FTE."
    ]
    assert module._get_settings_tab_labels() == [
        "🎓 Tab 1: apprentice takeovers",
        "🚀 Tab 2: trainee program",
        "💼 Tab 3: new hires",
    ]
    assert module._get_result_tab_labels() == [
        "📊 Overview & trends",
        "📋 People list / details",
        "💰 Cost analysis",
    ]


def test_hiring_settings_form_has_clean_german_labels(monkeypatch):
    import streamlit as st
    import dataloader.loader as loader
    import components.sidebar as sidebar

    module = _load_page_module("*_Prognose_Zugänge.py", "hiring_page_phase4_form")
    captured = {
        "date_inputs": [],
        "number_inputs": [],
        "selectboxes": [],
        "sliders": [],
        "checkboxes": [],
        "radios": [],
        "markdowns": [],
        "infos": [],
        "warnings": [],
        "matrix_labels": [],
        "expanders": [],
        "captions": [],
    }

    class CapturingColumn(DummyContext):
        def date_input(self, label, value=None, **kwargs):
            captured["date_inputs"].append(label)
            return value

        def number_input(self, label, *args, value=None, min_value=None, **kwargs):
            captured["number_inputs"].append(label)
            if value is not None:
                return value
            if len(args) >= 3:
                return args[2]
            if len(args) >= 1:
                return args[0]
            return min_value

        def selectbox(self, label, options, index=0, format_func=None, **kwargs):
            rendered = [format_func(opt) if format_func else opt for opt in options]
            captured["selectboxes"].append((label, rendered))
            return options[index]

        def slider(self, label, *args, value=None, **kwargs):
            captured["sliders"].append(label)
            if value is not None:
                return value
            if len(args) >= 3:
                return args[2]
            return args[-1] if args else None

    def capturing_columns(spec):
        count = spec if isinstance(spec, int) else len(spec)
        return [CapturingColumn() for _ in range(count)]

    snapshot_df = pd.DataFrame(
        {
            "PersNr": ["1"],
            "GebDatum": pd.to_datetime(["1980-01-01"]),
            "Eintritt": pd.to_datetime(["2010-01-01"]),
            "Austritt": [pd.NaT],
            "Status kundenindividuell": ["Aktiv"],
            "Sollarbeitszeit": [39.0],
            "Organisationseinheit": ["A"],
            "Jobfamily": ["JF1"],
            "TrfGr": ["E9"],
            "St": [1],
        }
    )
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})

    st.session_state.clear()
    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "de",
            "global_uploads": {},
        }
    )

    monkeypatch.setattr(module, "get_current_stichtag", lambda: pd.Timestamp("2025-12-31"))
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (snapshot_df, history_df, None, None))
    monkeypatch.setattr(
        module,
        "_get_page_cluster_context",
        lambda summary: (
            SimpleNamespace(source_signature="cluster-sig-test"),
            ClusterMappingBundle(),
            "cluster-sig-test",
            False,
        ),
    )
    monkeypatch.setattr(module, "load_atz_data_cached", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(loader, "calculate_mak_vectorized", lambda df, *_args, **_kwargs: df.assign(MAK_Calculated=1.0))
    monkeypatch.setattr(module, "render_distribution_matrix", lambda label, **kwargs: captured["matrix_labels"].append(label) or {})
    monkeypatch.setattr(module, "render_orgunit_mode_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)

    monkeypatch.setattr(sidebar, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "apply_event_filters", lambda *args, **kwargs: (pd.DataFrame(), 0, 0))
    monkeypatch.setattr(sidebar, "apply_filters", lambda df: pd.DataFrame())
    monkeypatch.setattr(sidebar, "render_filter_status", lambda *args, **kwargs: None)

    monkeypatch.setattr(module.st, "sidebar", SimpleNamespace(button=lambda *args, **kwargs: False))
    monkeypatch.setattr(module.st, "expander", lambda label, *args, **kwargs: captured["expanders"].append(label) or DummyContext())
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "tabs", lambda labels, **kwargs: [DummyContext() for _ in labels])
    monkeypatch.setattr(module.st, "columns", capturing_columns)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda text, *args, **kwargs: captured["markdowns"].append(text))
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "checkbox", lambda label, value=False, **kwargs: captured["checkboxes"].append(label) or value)
    monkeypatch.setattr(
        module.st,
        "radio",
        lambda label, options, index=0, format_func=None, **kwargs: (
            captured["radios"].append((label, [format_func(opt) if format_func else opt for opt in options]))
            or options[index]
        ),
    )
    monkeypatch.setattr(module.st, "info", lambda text, *args, **kwargs: captured["infos"].append(text))
    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: captured["warnings"].append(text))
    monkeypatch.setattr(module.st, "form_submit_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "data_editor", lambda df, **kwargs: df)
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))
    monkeypatch.setattr(module.st, "title", lambda *args, **kwargs: None)

    module.main()

    assert "Startdatum" in captured["date_inputs"]
    assert "Enddatum" in captured["date_inputs"]
    assert "Azubis" in captured["checkboxes"]
    assert "Neueinstellungen" in captured["checkboxes"]
    assert "Neue Azubis pro Jahr" in captured["number_inputs"]
    assert "Einstellungen pro Jahr" in captured["number_inputs"]
    assert "Übernahmequote (%)" in captured["sliders"]
    assert "Ausbildungsdauer (Jahre)" in captured["number_inputs"]
    assert ("Verteilung", ["Random", "OrgUnit"]) in captured["selectboxes"]
    assert ("Strategie", ["Zufällig", "Organisationseinheit", "Vakanzen auffüllen"]) in captured["selectboxes"]
    assert "Übernahme-Tarif" in [label for label, _options in captured["selectboxes"]]
    assert "Übernahme-Stufe" in captured["number_inputs"]
    assert ("Abschluss-Modus", ["Nächster Zyklus", "Nächster Folgezyklus"]) in captured["radios"]
    assert "##### 🔄 Detaillierte Übernahme-Verteilung" in captured["markdowns"]
    assert "Detailmatrix statt pauschaler Verteilung verwenden" in captured["checkboxes"]
    assert ("Dimension für Übernahme", ["Verteilen nach Jobfamily", "Verteilen nach Org Unit"]) in captured["radios"]
    assert "Anteil der Übernahmen nach JobFamily - Summe sollte 100 % ergeben" in captured["matrix_labels"]
    assert any(text.startswith("**Nächster Zyklus (empfohlen):**") for text in captured["infos"])
    assert "⬆️ Parameter einstellen und Prognose berechnen." in captured["infos"]

    assert "🔊 Verteilung Neueinstellungen (Matrix)" in captured["expanders"]
    assert "Steuern Sie, in welchen Bereichen neue Stellen (ohne Nachbesetzung) entstehen." in captured["captions"]

    joined = "\n".join(
        captured["date_inputs"]
        + captured["number_inputs"]
        + captured["sliders"]
        + captured["checkboxes"]
        + captured["markdowns"]
        + captured["infos"]
        + captured["warnings"]
        + captured["matrix_labels"]
        + captured["expanders"]
        + captured["captions"]
        + [label for label, _options in captured["selectboxes"]]
        + [option for _label, options in captured["selectboxes"] for option in options]
        + [label for label, _options in captured["radios"]]
    )
    assert "Ã" not in joined


def test_hiring_page_main_warns_in_english_for_empty_filters(monkeypatch):
    import streamlit as st
    import dataloader.loader as loader
    import components.sidebar as sidebar

    module = _load_page_module("*_Prognose_Zugänge.py", "hiring_page_phase4_main")
    warnings = []

    snapshot_df = pd.DataFrame(
        {
            "PersNr": ["1"],
            "GebDatum": pd.to_datetime(["1980-01-01"]),
            "Eintritt": pd.to_datetime(["2010-01-01"]),
            "Austritt": [pd.NaT],
            "Status kundenindividuell": ["Aktiv"],
            "Sollarbeitszeit": [39.0],
            "Organisationseinheit": ["A"],
            "Jobfamily": ["JF1"],
            "TrfGr": ["E9"],
            "St": [1],
        }
    )
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})

    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "global_uploads": {},
            "zugaenge_global_result": {"events": pd.DataFrame()},
            "zugaenge_vacancies": [],
            "zugaenge_cluster_source_signature": "cluster-sig-test",
            "zugaenge_start_date": pd.Timestamp("2026-03-27"),
            "zugaenge_end_date": pd.Timestamp("2027-03-27"),
            "zugaenge_use_azubis": True,
            "zugaenge_use_trainees": True,
            "zugaenge_use_newhires": True,
        }
    )

    monkeypatch.setattr(module, "get_current_stichtag", lambda: pd.Timestamp("2026-03-27"))
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (snapshot_df, history_df, None, None))
    monkeypatch.setattr(
        module,
        "_get_page_cluster_context",
        lambda summary: (
            SimpleNamespace(source_signature="cluster-sig-test"),
            ClusterMappingBundle(),
            "cluster-sig-test",
            False,
        ),
    )
    monkeypatch.setattr(module, "load_atz_data_cached", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(loader, "calculate_mak_vectorized", lambda df, *_args, **_kwargs: df.assign(MAK_Calculated=1.0))
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)

    monkeypatch.setattr(sidebar, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "apply_event_filters", lambda *args, **kwargs: (pd.DataFrame(), 0, 0))
    monkeypatch.setattr(sidebar, "apply_filters", lambda df: pd.DataFrame())
    monkeypatch.setattr(sidebar, "render_filter_status", lambda *args, **kwargs: None)

    monkeypatch.setattr(module.st, "sidebar", SimpleNamespace(button=lambda *args, **kwargs: False))
    monkeypatch.setattr(module.st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "tabs", lambda labels, **kwargs: [DummyContext() for _ in labels])
    monkeypatch.setattr(module.st, "columns", _dummy_columns)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: warnings.append(text))
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "success", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "error", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "checkbox", lambda label, value=False, **kwargs: value)
    monkeypatch.setattr(module.st, "number_input", lambda label, min_value=None, max_value=None, value=None, **kwargs: value if value is not None else min_value)
    monkeypatch.setattr(module.st, "selectbox", lambda label, options, index=0, **kwargs: options[index])
    monkeypatch.setattr(module.st, "slider", lambda label, min_value=None, max_value=None, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "radio", lambda label, options, index=0, **kwargs: options[index])
    monkeypatch.setattr(module.st, "data_editor", lambda df, **kwargs: df)
    monkeypatch.setattr(module.st, "form_submit_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)

    module.main()

    assert warnings == ["⚠️ No data available after filtering."]


def test_hybrid_page_intro_uses_clean_german_runtime_text(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Prognose_Hybrid.py", "hybrid_page_phase4_intro")
    captured = {"titles": [], "captions": [], "metric_hints": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    monkeypatch.setattr(module.st, "title", lambda text, *args, **kwargs: captured["titles"].append(text))
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))
    monkeypatch.setattr(module, "set_metric_page_hint", lambda text: captured["metric_hints"].append(text))
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (_ for _ in ()).throw(FileNotFoundError("stop after intro")))
    monkeypatch.setattr(module.st, "error", lambda *args, **kwargs: None)

    module.main()

    assert captured["titles"] == ["🏢 Prognose: Hybrid"]
    assert captured["captions"] == [
        "Prognose von Hybrid-Szenarien (Abgänge und Zugänge) mit klarer Trennung von MAK und Headcount."
    ]
    assert captured["metric_hints"] == [
        "Diese Seite zeigt derzeit ein kombiniertes Netto-Cockpit. Die globale Pille schaltet hier noch nicht die gesamte Seite zwischen Köpfe / MAK / EUR um."
    ]
    joined = "\n".join(captured["titles"] + captured["captions"] + captured["metric_hints"])
    assert "Abgänge" in joined
    assert "Zugänge" in joined
    assert "Köpfe" in joined
    assert not any(marker in joined for marker in ("\u00c3", "\u00e2", "\u0192", "\u00c6"))


def test_hybrid_zugaenge_chart_sources_use_clean_runtime_labels():
    module = _load_page_module("*_Prognose_Hybrid.py", "hybrid_page_phase4_chart_labels")

    filt_zug_events = pd.DataFrame(
        {
            "type": ["Azubi_Hire", "Azubi_Conversion_In", "New_Hire", "Trainee_Hire"],
            "OE-Cluster": ["A", "A", "B", "B"],
        }
    )

    chart_sources = module._build_hybrid_zugaenge_chart_sources(filt_zug_events)

    assert list(chart_sources["events_chart"]["Quelle"]) == [
        "Neue Auszubildende",
        "Übernahme aus Ausbildung",
        "Neueinstellung",
        "Trainee",
    ]
    assert list(chart_sources["z_stats"].columns) == ["OE-Cluster", "Zugänge"]
    joined = "\n".join(chart_sources["events_chart"]["Quelle"].tolist() + chart_sources["z_stats"].columns.tolist())
    assert "Übernahme aus Ausbildung" in joined
    assert "Zugänge" in joined
    assert not any(marker in joined for marker in ("\u00c3", "\u00e2", "\u0192", "\u00c6"))


def test_exclusion_groups_page_is_localized_in_english(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Deep_Dive_Exklusionsgruppen.py", "exclusion_page_phase4", unit_testing=True)
    captured = {
        "titles": [],
        "captions": [],
        "markdowns": [],
        "infos": [],
        "warnings": [],
        "errors": [],
        "buttons": [],
        "checkboxes": [],
        "selectboxes": [],
        "metric_hints": [],
        "tabs": [],
    }

    class ExclusionColumn(DummyContext):
        def markdown(self, text, *args, **kwargs):
            captured["markdowns"].append(text)

        def button(self, label, *args, **kwargs):
            captured["buttons"].append(label)
            return False

    def exclusion_columns(spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [ExclusionColumn() for _ in range(count)]

    snapshot_df = pd.DataFrame(
        {
            "PersNr": ["1", "2", "3"],
            "Soll_FTE": [1.0, 0.0, 0.0],
            "MAK_Calculated": [1.0, 0.0, 0.0],
            "Is_Vacant": [False, False, True],
            "Kürzel OrgEinheit": ["1000", "1001", "9900"],
            "MitarbGruppenbez.": ["Vorstand", "Mitarbeiter", "Mitarbeiter"],
            "Status kundenindividuell": ["Aktiv", "Ruhendes Beschäftigungsverhältnis", "Aktiv"],
            "Organisationseinheit": ["Steuerung", "Service", "PA 9900"],
            "Jobfamily": ["UNMAPPED", "UNMAPPED", "UNMAPPED"],
        }
    )
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})

    st.session_state.update({i18n.LANGUAGE_SESSION_KEY: "en"})

    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (snapshot_df, history_df, None, None))
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda text: captured["metric_hints"].append(text))
    monkeypatch.setattr(
        module,
        "_load_current_exclusions",
        lambda: {"vorstand": False, "ruhend_bv": False, "org_units": [], "planstellen_follow_person": True},
    )

    monkeypatch.setattr(module.st, "title", lambda text, *args, **kwargs: captured["titles"].append(text))
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))
    monkeypatch.setattr(module.st, "markdown", lambda text, *args, **kwargs: captured["markdowns"].append(text))
    monkeypatch.setattr(module.st, "info", lambda text, *args, **kwargs: captured["infos"].append(text))
    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: captured["warnings"].append(text))
    monkeypatch.setattr(module.st, "error", lambda text, *args, **kwargs: captured["errors"].append(text))
    monkeypatch.setattr(module.st, "columns", exclusion_columns)
    monkeypatch.setattr(module.st, "button", lambda label, *args, **kwargs: captured["buttons"].append(label) or False)
    monkeypatch.setattr(module.st, "checkbox", lambda label, *args, **kwargs: captured["checkboxes"].append(label) or False)
    monkeypatch.setattr(
        module.st,
        "tabs",
        lambda labels, **kwargs: captured["tabs"].append(labels) or [DummyContext() for _ in labels],
    )
    monkeypatch.setattr(
        module.st,
        "selectbox",
        lambda label, options, **kwargs: captured["selectboxes"].append((label, list(options))) or options[0],
    )
    monkeypatch.setattr(module.st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)

    module.main()

    assert captured["titles"] == ["🔎 Exclusion groups"]
    assert captured["captions"][0] == (
        "Transparency and controls for all excludable staff groups. Groups can be included or excluded directly here, "
        "and the change takes effect immediately on all other pages."
    )
    assert captured["metric_hints"] == [
        "This page is a control and transparency page. The global pill currently has no business effect here."
    ]
    assert "### Overview" in captured["markdowns"]
    assert "### Group exclusions" in captured["markdowns"]
    assert "### Visualization" in captured["markdowns"]
    assert "### Drilldown: select group" in captured["markdowns"]
    assert any("Total positions" in text for text in captured["markdowns"])
    assert any("Active scope:" in text and "Full dashboard (incl. positions)" in text for text in captured["markdowns"])
    assert "Exclude all" in captured["buttons"]
    assert "Include all" in captured["buttons"]
    assert "👥 Apply to employees & forecast" in captured["buttons"]
    assert "🏢 Apply to full dashboard" in captured["buttons"]
    assert captured["tabs"] == [["📊 Positions by group", "📐 Target FTE by group"]]
    assert captured["selectboxes"] == [("Group", ["Board", "Dormant employment relationship", "PA dormant employment relationship", "PA apprentices", "PE cross-functional / trainee positions", "PA internship", "PA long-term sick", "PA temporary retirement", "PA care leave", "PA military / civil service", "PA maternity protection", "PA parental leave", "PA special leave § 28 TVöD", "PA employment ban", "PA child-rearing leave", "PA returnees", "Temporary helpers", "PA release (ATZ-FR, leave, turbo part-time)", "Staff council", "Other 99XX (dummy / pension benefits)"])]
    assert "Board" in captured["checkboxes"]
    assert "Dormant employment relationship" in captured["checkboxes"]
    assert "Gray = currently excluded. Blue = active in the model." in captured["captions"]
    assert "Gray = currently excluded. Amber = active in the model." in captured["captions"]
