import pytest
import pandas as pd

from utils import i18n


class DummySidebar:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyContext(DummySidebar):
    pass


def _dummy_columns(spec):
    count = spec if isinstance(spec, int) else len(spec)
    return [DummyContext() for _ in range(count)]


@pytest.fixture(autouse=True)
def clean_session_state(monkeypatch):
    import streamlit as st

    monkeypatch.setattr(st, "session_state", {})


def test_initialize_language_state_defaults_to_german():
    import streamlit as st

    language = i18n.initialize_language_state()

    assert language == "de"
    assert st.session_state[i18n.LANGUAGE_SESSION_KEY] == "de"


def test_t_uses_active_language_and_formats_placeholders(monkeypatch):
    import streamlit as st

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    result = i18n.t("sidebar.language.toggle", language_name="German")

    assert result == "Switch to German"


def test_t_falls_back_to_default_language_for_missing_key(monkeypatch):
    import streamlit as st

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    assert i18n.t("does.not.exist") == "does.not.exist"


def test_toggle_language_switches_between_german_and_english():
    import streamlit as st

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"
    assert i18n.toggle_language() == "en"
    assert st.session_state[i18n.LANGUAGE_SESSION_KEY] == "en"

    assert i18n.toggle_language() == "de"
    assert st.session_state[i18n.LANGUAGE_SESSION_KEY] == "de"


def test_get_language_name_localizes_target_language():
    import streamlit as st

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"
    assert i18n.get_language_name("en") == "Englisch"

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"
    assert i18n.get_language_name("de") == "German"


def test_render_language_switcher_toggles_language_and_reruns(monkeypatch):
    import streamlit as st
    from components import sidebar

    reruns = []
    button_labels = []
    captions = []

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    monkeypatch.setattr(sidebar.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar.st, "caption", lambda text, *args, **kwargs: captions.append(text))
    monkeypatch.setattr(
        sidebar.st,
        "button",
        lambda label, **kwargs: button_labels.append(label) or True,
    )
    monkeypatch.setattr(sidebar.st, "rerun", lambda: reruns.append(True))

    sidebar.render_language_switcher()

    assert st.session_state[i18n.LANGUAGE_SESSION_KEY] == "en"
    assert reruns == [True]
    assert captions == ["Aktive Sprache: Deutsch"]
    assert button_labels == ["Zu Englisch wechseln"]


def test_render_metric_selector_only_includes_language_switcher(monkeypatch):
    from components import sidebar

    calls = {"switcher": 0, "selector": 0, "notes": 0}

    monkeypatch.setattr(sidebar.st, "sidebar", DummySidebar())
    monkeypatch.setattr(sidebar.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "render_global_metric_selector", lambda: calls.__setitem__("selector", calls["selector"] + 1))
    monkeypatch.setattr(sidebar, "_render_sidebar_note", lambda *args, **kwargs: calls.__setitem__("notes", calls["notes"] + 1))
    monkeypatch.setattr(sidebar, "render_language_switcher", lambda: calls.__setitem__("switcher", calls["switcher"] + 1))

    sidebar.render_metric_selector_only("Test note")

    assert calls["selector"] == 1
    assert calls["notes"] == 1
    assert calls["switcher"] == 1


def test_multiselect_placeholder_is_localized_in_german(monkeypatch):
    import streamlit as st
    from components import sidebar

    captured = {}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    def fake_multiselect(label, options, default=None, **kwargs):
        captured["label"] = label
        captured["placeholder"] = kwargs.get("placeholder")
        return default or []

    monkeypatch.setattr(sidebar.st, "multiselect", fake_multiselect)

    sidebar._multiselect_with_placeholder("Test", ["A", "B"], default=[])

    assert captured == {
        "label": "Test",
        "placeholder": "Optionen auswählen",
    }


def test_working_time_and_atz_options_are_localized_in_english(monkeypatch):
    import streamlit as st
    from components import sidebar

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    assert sidebar._localized_working_time_option("Vollzeit") == "Full-time"
    assert sidebar._localized_working_time_option("Teilzeit") == "Part-time"
    assert sidebar._localized_working_time_option("Inaktiv") == "Inactive"
    assert sidebar._localized_atz_option("Kein ATZ") == "No partial retirement"
    assert sidebar._localized_atz_option("Arbeitsphase") == "Work phase"
    assert sidebar._localized_atz_option("Freistellungsphase") == "Release phase"


def test_get_filter_summary_is_localized_in_english(monkeypatch):
    import streamlit as st
    from components import sidebar
    from utils import settings_loader

    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "selected_org_units": ["Filiale A", "Filiale B"],
            "selected_jobfamilies": ["IT"],
            "selected_cohorts": [],
            "selected_genders": ["m"],
            "selected_employment": ["Vollzeit"],
            "selected_education": ["Bankfachwirt"],
            "selected_atz_status": ["Kein ATZ"],
            "selected_oe_clusters": ["Sales"],
            "selected_jf_clusters": [],
        }
    )
    monkeypatch.setattr(settings_loader, "get_setting", lambda *args, **kwargs: {"vorstand": True, "org_units": ["9900"]})

    summary = sidebar.get_filter_summary()

    assert "active filters" in summary
    assert "org units" in summary
    assert "Qualification" in summary
    assert "Excl.: Board, 1 areas" in summary


def test_render_filter_status_uses_localized_caption(monkeypatch):
    import streamlit as st
    from components import sidebar
    from utils import settings_loader

    captions = []
    st.session_state.update(
        {
            i18n.LANGUAGE_SESSION_KEY: "en",
            "selected_org_units": ["Filiale A"],
            "selected_jobfamilies": [],
            "selected_cohorts": [],
            "selected_genders": ["m", "w"],
            "selected_employment": ["Vollzeit", "Teilzeit", "Inaktiv"],
            "selected_education": [],
            "selected_atz_status": ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
            "selected_oe_clusters": [],
            "selected_jf_clusters": [],
        }
    )
    monkeypatch.setattr(settings_loader, "get_setting", lambda *args, **kwargs: {})
    monkeypatch.setattr(sidebar.st, "caption", lambda text, *args, **kwargs: captions.append(text))

    sidebar.render_filter_status(10, 4)

    assert captions == ["🔍 Filters active: 10 → 4 events | 1 active filters: 1 org units"]


def test_render_active_filter_banner_is_localized(monkeypatch):
    import streamlit as st
    from components import ui_shell

    infos = []
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(ui_shell.st, "info", lambda text, *args, **kwargs: infos.append(text))

    ui_shell.render_active_filter_banner("2 active filters")

    assert infos == ["🎯 Active filters: 2 active filters"]


def test_jobfamily_matrix_warning_is_localized(monkeypatch):
    import streamlit as st
    from components import jobfamily_matrix

    warnings = []
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"
    monkeypatch.setattr(jobfamily_matrix.st, "warning", lambda text, *args, **kwargs: warnings.append(text))

    result = jobfamily_matrix.render_assignment_matrix(
        pd.DataFrame({"Planstellennr": [1], "Planstelle": ["IT"]}),
        {"jobfamilies": {}},
    )

    assert result == {}
    assert warnings == [
        "No job families defined yet. Please create job families in the 'Definitions' tab first."
    ]


def test_jobfamily_matrix_no_results_message_is_localized(monkeypatch):
    import streamlit as st
    from components import jobfamily_matrix

    infos = []
    st.session_state.clear()
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"
    st.session_state["jf_matrix_search"] = ""
    st.session_state["jf_matrix_page"] = 0
    st.session_state["jf_matrix_filter_unmapped"] = False
    st.session_state["jf_pending_changes"] = {}

    monkeypatch.setattr(jobfamily_matrix.st, "metric", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobfamily_matrix.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobfamily_matrix.st, "columns", _dummy_columns)
    monkeypatch.setattr(jobfamily_matrix.st, "text_input", lambda *args, **kwargs: "zzz")
    monkeypatch.setattr(jobfamily_matrix.st, "checkbox", lambda *args, **kwargs: False)
    monkeypatch.setattr(jobfamily_matrix.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(jobfamily_matrix.st, "info", lambda text, *args, **kwargs: infos.append(text))
    monkeypatch.setattr(jobfamily_matrix.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobfamily_matrix.st, "markdown", lambda *args, **kwargs: None)

    result = jobfamily_matrix.render_assignment_matrix(
        pd.DataFrame({"Planstellennr": [1], "Planstelle": ["IT Specialist"]}),
        {"jobfamilies": {"IT": {"patterns": [], "manual_assignments": []}}},
    )

    assert result == {}
    assert infos == ["No jobs found. Please adjust the filters."]


def test_setup_wizard_steps_are_localized(monkeypatch):
    import streamlit as st
    from components import setup_wizard

    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    assert setup_wizard.get_wizard_steps() == [
        "Welcome",
        "Choose data source",
        "Define job families",
        "Review assignment",
        "Finish",
    ]


def test_render_welcome_step_uses_localized_button_labels(monkeypatch):
    import streamlit as st
    from components import setup_wizard

    button_labels = []
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(setup_wizard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_wizard.st, "columns", _dummy_columns)
    monkeypatch.setattr(setup_wizard.st, "container", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(
        setup_wizard.st,
        "button",
        lambda label, **kwargs: button_labels.append(label) or False,
    )

    setup_wizard.render_welcome_step()

    assert "Next" in button_labels
    assert "Skip wizard" in button_labels
