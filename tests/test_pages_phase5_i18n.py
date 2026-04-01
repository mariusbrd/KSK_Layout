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


def _load_page_module(pattern: str, module_name: str):
    page_path = next((ROOT / "pages").glob(pattern))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clean_session_state(monkeypatch):
    import streamlit as st

    monkeypatch.setattr(st, "session_state", {})


def test_compact_page_helpers_are_localized_in_english(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_helpers")
    captured = {"titles": [], "captions": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(module.st, "title", lambda text, *args, **kwargs: captured["titles"].append(text))
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))

    module._render_page_intro()

    assert captured["titles"] == [i18n.t("compact.title", language="en")]
    assert captured["captions"] == ["All important current-state and current-vs-target evaluations at a glance."]
    assert module._get_main_tab_labels() == [
        i18n.t("compact.tabs.ist", language="en"),
        i18n.t("compact.tabs.ist_soll", language="en"),
    ]


def test_compact_main_warns_in_english_for_empty_filters(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_warning")
    warnings = []

    df = pd.DataFrame({"PersNr": ["1"], "Organisationseinheit": ["A"]})
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(module, "SCROLL_NAV_AVAILABLE", False)
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (df, history_df, None, None))
    monkeypatch.setattr(module, "prepare_compact_data", lambda snapshot_df: snapshot_df)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "apply_filters", lambda input_df: pd.DataFrame())
    monkeypatch.setattr(module, "get_filter_summary", lambda: "2 active filters")
    monkeypatch.setattr(module, "render_active_filter_banner", lambda *args, **kwargs: None)

    monkeypatch.setattr(module.st, "sidebar", DummyContext())
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "toggle", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: warnings.append(text))

    module.main()

    assert warnings == ["No data for the selected filters."]


def test_compact_main_uses_localized_mode_labels_in_english(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_mode")
    captured = {"intro": [], "context": [], "tabs": []}

    df = pd.DataFrame({"PersNr": ["1"], "Organisationseinheit": ["A"]})
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(module, "SCROLL_NAV_AVAILABLE", False)
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (df, history_df, None, None))
    monkeypatch.setattr(module, "prepare_compact_data", lambda snapshot_df: snapshot_df)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "apply_filters", lambda input_df: input_df)
    monkeypatch.setattr(module, "get_filter_summary", lambda: "1 active filter")
    monkeypatch.setattr(module, "render_active_filter_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_section_intro", lambda title, text, **kwargs: captured["intro"].append((title, text)))
    monkeypatch.setattr(module, "render_context_box", lambda label, text, **kwargs: captured["context"].append((label, text)))
    monkeypatch.setattr(module, "get_global_metric_view", lambda: "FTE")
    monkeypatch.setattr(module, "render_ist_koepfe_tab", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_ist_mak_tab", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_ist_eur_tab", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_ist_soll_koepfe_tab", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_ist_vs_soll_mak_tab", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_ist_vs_soll_eur_tab", lambda *args, **kwargs: None)

    monkeypatch.setattr(module.st, "sidebar", DummyContext())
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "toggle", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "tabs", lambda labels, **kwargs: captured["tabs"].append(labels) or [DummyContext() for _ in labels])

    module.main()

    assert captured["intro"] == [
        (
            "Evaluation mode",
            "Switch between analysis area and metric view. The content below reacts to the active view filters.",
        )
    ]
    assert captured["context"] == [("Metric view", "Controlled via the sidebar: FTE")]
    assert captured["tabs"] == [["📈 Current-state analysis", "🎯 Current vs target"]]


@pytest.mark.parametrize(
    ("metric_view", "expected_calls"),
    [
        ("Köpfe", {"ist_koepfe": 1, "ist_mak": 0, "ist_eur": 0, "ist_soll_koepfe": 1, "ist_vs_soll_mak": 0, "ist_vs_soll_eur": 0}),
        ("MAK", {"ist_koepfe": 0, "ist_mak": 1, "ist_eur": 0, "ist_soll_koepfe": 0, "ist_vs_soll_mak": 1, "ist_vs_soll_eur": 0}),
    ],
)
def test_compact_main_routes_selected_metric_view(monkeypatch, metric_view, expected_calls):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", f"compact_page_phase5_metric_{metric_view}")
    calls = {key: 0 for key in expected_calls}

    df = pd.DataFrame({"PersNr": ["1"], "Organisationseinheit": ["A"]})
    history_df = pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])})
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    monkeypatch.setattr(module, "SCROLL_NAV_AVAILABLE", False)
    monkeypatch.setattr(module, "load_and_prepare_data", lambda: (df, history_df, None, None))
    monkeypatch.setattr(module, "prepare_compact_data", lambda snapshot_df: snapshot_df)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "apply_filters", lambda input_df: input_df)
    monkeypatch.setattr(module, "get_filter_summary", lambda: "1 aktiver Filter")
    monkeypatch.setattr(module, "render_active_filter_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_section_intro", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_context_box", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "get_global_metric_view", lambda: metric_view)
    monkeypatch.setattr(module, "render_ist_koepfe_tab", lambda *args, **kwargs: calls.__setitem__("ist_koepfe", calls["ist_koepfe"] + 1))
    monkeypatch.setattr(module, "render_ist_mak_tab", lambda *args, **kwargs: calls.__setitem__("ist_mak", calls["ist_mak"] + 1))
    monkeypatch.setattr(module, "render_ist_eur_tab", lambda *args, **kwargs: calls.__setitem__("ist_eur", calls["ist_eur"] + 1))
    monkeypatch.setattr(module, "render_ist_soll_koepfe_tab", lambda *args, **kwargs: calls.__setitem__("ist_soll_koepfe", calls["ist_soll_koepfe"] + 1))
    monkeypatch.setattr(module, "render_ist_vs_soll_mak_tab", lambda *args, **kwargs: calls.__setitem__("ist_vs_soll_mak", calls["ist_vs_soll_mak"] + 1))
    monkeypatch.setattr(module, "render_ist_vs_soll_eur_tab", lambda *args, **kwargs: calls.__setitem__("ist_vs_soll_eur", calls["ist_vs_soll_eur"] + 1))

    monkeypatch.setattr(module.st, "sidebar", DummyContext())
    monkeypatch.setattr(module.st, "divider", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "toggle", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "tabs", lambda labels, **kwargs: [DummyContext() for _ in labels])

    module.main()

    assert calls == expected_calls


def test_compact_render_management_summary_localizes_eur_labels_in_english(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_summary")
    captured = []
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(module.st, "markdown", lambda text, *args, **kwargs: captured.append(text))

    module.render_management_summary(
        "IST-EUR",
        {
            "kennzahlen": [
                {"label": "Gesamt Kosten", "value": "73.1 Mio. EUR", "status": "good"},
                {"label": "Kosten/Kopf", "value": "60k EUR", "status": "good"},
                {"label": "Kosten/MAK", "value": "82k EUR", "status": "good"},
            ],
            "insights": [],
            "handlungsempfehlungen": [
                "Aktuelle Kostenstruktur monitoren und Budget einhalten",
                "Retention-Maßnahmen für Schlüsselkräfte prüfen",
            ],
        },
        print_mode=False,
    )

    combined = "\n".join(captured)
    assert "Management Summary: Current-state EUR" in combined
    assert "Key metrics at a glance:" in combined
    assert "Total cost" in combined
    assert "Cost per head" in combined
    assert "Cost per FTE" in combined
    assert "Recommended actions:" in combined
    assert "Monitor the current cost structure and keep spending within budget" in combined
    assert "Review retention measures for key roles" in combined


def test_compact_breakdown_localizes_dimension_and_data_table_in_english(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_breakdown")
    captured = {"subheaders": [], "markdown": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    monkeypatch.setattr(
        module,
        "create_breakdown_table",
        lambda *args, **kwargs: pd.DataFrame({"Geschlecht": ["m"], "IST": [10]}),
    )
    monkeypatch.setattr(module, "create_horizontal_bar_chart", lambda *args, **kwargs: object())
    monkeypatch.setattr(module, "format_dataframe_for_display", lambda df, value_type="mak": df)
    monkeypatch.setattr(module, "dataframe_compat", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "export_to_excel", lambda *args, **kwargs: b"x")
    monkeypatch.setattr(module, "download_button_compat", lambda *args, **kwargs: None)

    monkeypatch.setattr(module.st, "subheader", lambda text, *args, **kwargs: captured["subheaders"].append(text))
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda text, *args, **kwargs: captured["markdown"].append(text))
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "columns", lambda spec: [DummyContext(), DummyContext()])

    module.render_single_breakdown(
        pd.DataFrame({"Geschlecht": ["m"], "Headcount": [10]}),
        "Geschlecht",
        "Geschlecht",
        value_col="Headcount",
        value_type="koepfe",
        key_prefix="test",
    )

    assert captured["subheaders"] == ["Gender"]
    assert any("**Data table**" == text for text in captured["markdown"])


def test_compact_ist_eur_kpis_use_english_subtitles(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_kpis_en_eur")
    captured: dict[str, list] = {"kpis": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "en"

    df = pd.DataFrame(
        {
            "PersNr": ["1", "2"],
            "Is_Vacant": [False, False],
            "Total_Cost_Year": [60000.0, 80000.0],
            "BsGrd": [100, 50],
            "FTE_assigned": [1.0, 0.5],
        }
    )

    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: captured["kpis"].extend(kpis))
    monkeypatch.setattr(module, "render_intra_tab_navigation", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_single_breakdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_management_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)

    module.render_ist_eur_tab(df, print_mode=False)

    assert [kpi["subtitle"] for kpi in captured["kpis"]] == [
        "Annual costs",
        "Average",
        "per FTE",
    ]


def test_compact_ist_mak_kpis_are_clean_in_german(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_kpis_de")
    captured: dict[str, list] = {"kpis": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    df = pd.DataFrame(
        {
            "PersNr": ["1", "2"],
            "Is_Vacant": [False, False],
            "BsGrd": [100, 50],
            "FTE_assigned": [1.0, 0.5],
            "Geschlecht": ["w", "m"],
            "ATZ_Status": ["Kein ATZ", "Kein ATZ"],
        }
    )

    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: captured["kpis"].extend(kpis))
    monkeypatch.setattr(module, "render_intra_tab_navigation", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_single_breakdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_management_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)

    module.render_ist_mak_tab(df, print_mode=False)

    assert [kpi["title"] for kpi in captured["kpis"]] == [
        i18n.t("compact.kpi.total_mak_effective", language="de"),
        i18n.t("compact.kpi.avg_fte", language="de"),
        i18n.t("compact.kpi.part_time_rate", language="de"),
    ]
    flat_texts = [value for kpi in captured["kpis"] for value in kpi.values() if isinstance(value, str)]
    assert not any(marker in text for text in flat_texts for marker in ("Ã", "â", "ƒ", "Æ"))


def test_compact_management_summary_mak_is_clean_in_german(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_summary_de")
    captured: list[str] = []
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    monkeypatch.setattr(module.st, "markdown", lambda text, *args, **kwargs: captured.append(text))

    module.render_management_summary(
        "IST-MAK",
        {
            "kennzahlen": [
                {"label": i18n.t("compact.summary.metric.total_mak", language="de"), "value": "887,7", "status": "good"},
                {"label": i18n.t("compact.summary.metric.avg_fte", language="de"), "value": "0,73", "status": "warning"},
            ],
            "insights": [
                {
                    "type": "warning",
                    "text": i18n.t("compact.insight.avg_fte_low", language="de", value="0,73"),
                }
            ],
            "handlungsempfehlungen": [
                i18n.t("compact.rec.part_time_causes", language="de")
            ],
        },
        print_mode=False,
    )

    combined = "\n".join(captured)
    assert "Management Summary: IST-MAK" in combined
    assert "Ø FTE" in combined
    assert "Teilzeit-Gründe" in combined
    assert not any(marker in combined for marker in ("Ã", "â", "ƒ", "Æ"))


def test_compact_ist_vs_soll_mak_path_is_clean_in_german(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_ist_vs_soll_mak_clean")
    captured: dict[str, list] = {"kpis": [], "summary": []}
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    df = pd.DataFrame(
        {
            "PersNr": ["1", "2", "3"],
            "Is_Vacant": [False, False, True],
            "BsGrd": [100, 50, 0],
            "FTE_assigned": [1.0, 0.5, 0.0],
            "Soll_FTE": [1.0, 1.0, 1.0],
            "Ausbildung": ["A", "B", None],
            "Vergütungsklasse": ["9/2", "9/3", "9/2"],
        }
    )

    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: captured["kpis"].extend(kpis))
    monkeypatch.setattr(module, "render_single_comparison", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_render_education_range_section_clean", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_management_summary", lambda title, summary_data, print_mode=False: captured["summary"].append((title, summary_data)))
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)

    module.render_ist_vs_soll_mak_tab(df, print_mode=False)

    flat_kpi_texts = [value for kpi in captured["kpis"] for value in kpi.values() if isinstance(value, str)]
    assert "Tatsächliche Kapazität" in flat_kpi_texts
    assert "Geplante Kapazität" in flat_kpi_texts
    assert "Erfüllungsgrad" in flat_kpi_texts
    assert not any(marker in text for text in flat_kpi_texts for marker in ("\u00c3", "\u00e2", "\u0192", "\u00c6"))

    summary_title, summary_data = captured["summary"][0]
    assert summary_title == "IST vs SOLL MAK"
    summary_texts = []
    for item in summary_data["kennzahlen"]:
        summary_texts.extend(str(value) for value in item.values() if isinstance(value, str))
    for item in summary_data["insights"]:
        summary_texts.extend(str(value) for value in item.values() if isinstance(value, str))
    summary_texts.extend(summary_data["handlungsempfehlungen"])
    assert "Erfüllungsgrad" in summary_texts
    assert "Kritische Unterbesetzung: Nur 50,0% der Soll-Kapazität besetzt!" in summary_texts
    assert "SOFORT: Recruiting-Offensive starten, Zeitarbeit prüfen" in summary_texts
    assert not any(marker in text for text in summary_texts for marker in ("\u00c3", "\u00e2", "\u0192", "\u00c6"))


def test_prepare_compact_data_creates_clean_dimension_columns(monkeypatch):
    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_prepare_clean")

    monkeypatch.setattr(module, "load_jobfamily_definitions", lambda: None)
    monkeypatch.setattr(module, "calculate_soll_cost", lambda row: 0.0)

    prepared = module.prepare_compact_data(
        pd.DataFrame(
            {
                "Planstelle": ["P-1"],
                "Betriebszugehörigkeit_Jahre": [4],
                "TrfGr": ["9"],
                "St": ["2+"],
                "FTE_person": [0.6],
                "Vertragsart": ["Unbefristet"],
            }
        )
    )

    assert "Betriebszugehörigkeit_Bin" in prepared.columns
    assert "Beschäftigungsgrad_Kat" in prepared.columns
    assert "Beschäftigungsstatus" in prepared.columns
    assert "Vergütungsklasse" in prepared.columns
    broken_tenure_bin = "Betriebszugeh" + "?" + "rigkeit_Bin"
    broken_employment_bin = "Besch" + "?" + "ftigungsgrad_Kat"
    assert broken_tenure_bin not in prepared.columns
    assert broken_employment_bin not in prepared.columns
    assert prepared.loc[0, "Vergütungsklasse"] == "9/2"
    assert prepared.loc[0, "Beschäftigungsstatus"] == "Unbefristet"
    assert prepared.loc[0, "Betriebszugehörigkeit_Bin"] == "2-5 J."


def test_compact_breakdown_missing_dimension_warning_is_clean_in_german(monkeypatch):
    import streamlit as st

    module = _load_page_module("*_Kompakt.py", "compact_page_phase5_breakdown_warning_de")
    warnings = []
    st.session_state[i18n.LANGUAGE_SESSION_KEY] = "de"

    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: warnings.append(text))

    module.render_single_breakdown(
        pd.DataFrame({"Geschlecht": ["m"], "Headcount": [1]}),
        "Beschäftigungsgrad",
        "Beschäftigungsgrad_Kat",
        value_col="Headcount",
        value_type="koepfe",
        key_prefix="warn",
    )

    assert warnings == [
        "Dimension 'Beschäftigungsgrad' nicht verfügbar (Spalte 'Beschäftigungsgrad_Kat' fehlt)."
    ]
    assert not any(marker in warnings[0] for marker in ("Ã", "â", "ƒ", "Æ"))
