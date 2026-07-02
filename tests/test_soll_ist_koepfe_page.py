from pathlib import Path
import importlib.util
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def _load_compact_page_module():
    page_path = next((ROOT / "pages").glob("*_Kompakt.py"))
    spec = importlib.util.spec_from_file_location("compact_page_test_module", page_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sample_raw_logic_result():
    pivot = pd.DataFrame(
        {
            "E9A": [3],
            "E10": [0],
            "Unbesetzt": [2],
            "Gesamt": [5],
        },
        index=pd.Index(["E9A"], name="Soll-EG"),
    )
    work_df = pd.DataFrame(
        [
            {"_Ist_EG": "E9A", "_Soll_EG": "E9A", "_Soll_EG_H": "E9A", "_Soll_EG_I": "E9A"},
            {"_Ist_EG": "E9A", "_Soll_EG": "E9A", "_Soll_EG_H": "E9A", "_Soll_EG_I": "E9A"},
            {"_Ist_EG": "E9A", "_Soll_EG": "E9A", "_Soll_EG_H": "E9A", "_Soll_EG_I": "E9A"},
            # Echtes Gehaltsband (Spalte H != Spalte I) -> eigene Zeile "E10-E11"
            {"_Ist_EG": "E10", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11"},
        ]
    )
    summary = {
        "regular_total": 10,
        "regular_occupied": 8,
        "regular_vacant": 2,
        "matrix_not_found": 0,
        "matrix_occupied": 3,
        "technical_non9xxx_occupied": 4,
        "technical_total": 7,
        "technical_9xxx_total": 2,
        "technical_non9xxx_total": 5,
    }
    return pivot, ["E9A"], ["E9A", "E10"], "Unbesetzt", "Nicht gefunden", work_df, 1, pd.Series({"E10": 1}), summary


def test_build_soll_ist_pivot_raw_logic_delegates_to_engine(monkeypatch):
    module = _load_compact_page_module()

    captured = {}

    def fake_build_soll_ist_koepfe_result(*, use_max_eg):
        captured["use_max_eg"] = use_max_eg
        return {
            "pivot": pd.DataFrame(index=pd.Index([], name="Soll-EG")),
            "soll_order": [],
            "ist_eg_cols": [],
            "IST_UNBESETZT": "Unbesetzt",
            "IST_NOT_FOUND": "Nicht gefunden",
            "work_df": pd.DataFrame(),
            "n_no_soll_eg": 0,
            "no_soll_eg_row": pd.Series(dtype=int),
            "summary": {"regular_total": 0},
        }

    monkeypatch.setattr(module, "build_soll_ist_koepfe_result", fake_build_soll_ist_koepfe_result)

    result = module._build_soll_ist_pivot_raw_logic(use_max_eg=False)
    assert captured["use_max_eg"] is False
    assert len(result) == 9
    assert result[3] == "Unbesetzt"
    assert result[4] == "Nicht gefunden"


def test_render_ist_soll_koepfe_tab_uses_raw_logic_kpis_and_control_captions(monkeypatch):
    module = _load_compact_page_module()
    raw_result = _sample_raw_logic_result()

    captured = {"kpi_calls": [], "captions": [], "downloads": 0, "dataframes": 0, "plots": 0}

    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: captured["kpi_calls"].append(kpis))
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: captured.__setitem__("downloads", captured["downloads"] + 1))
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)

    monkeypatch.setattr(module.st, "toggle", lambda *args, **kwargs: True)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda text, *args, **kwargs: captured["captions"].append(text))
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: captured.__setitem__("dataframes", captured["dataframes"] + 1))
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: captured.__setitem__("plots", captured["plots"] + 1))
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E9A")

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    assert captured["kpi_calls"]
    titles = [item["title"] for item in captured["kpi_calls"][0]]
    assert "Soll-Stellen (regulaer)" in titles
    assert "Nicht definierte Sollstelle in Arbeit" in titles
    technical_kpi = next(item for item in captured["kpi_calls"][0] if item["title"] == "Nicht definierte Sollstelle in Arbeit")
    assert technical_kpi["value"] == "4"
    # Original-Matrix + Band-Matrix + Fit-Uebersicht haben je einen eigenen Download.
    assert captured["downloads"] == 3
    assert captured["dataframes"] >= 1
    assert captured["plots"] >= 1
    joined_captions = "\n".join(captured["captions"])
    assert "Roh-nahe Kontrolllogik" in joined_captions
    assert "Kontrollsummen 0.01-Faelle" in joined_captions


def test_soll_eg_band_label_handles_missing_values():
    module = _load_compact_page_module()

    assert module._soll_eg_band_label("E9A", "E9A") == "E9A"
    assert module._soll_eg_band_label("E10", "E11") == "E10-E11"
    assert module._soll_eg_band_label("", "E11") == "E11"
    assert module._soll_eg_band_label("E10", "") == "E10"
    assert module._soll_eg_band_label("", "") == "(ohne Soll-EG)"


def test_build_soll_ist_band_pivot_creates_explicit_range_rows():
    module = _load_compact_page_module()

    work_df = pd.DataFrame(
        [
            {"_Soll_EG_H": "E9A", "_Soll_EG_I": "E9A", "_Ist_EG": "E9A"},
            {"_Soll_EG_H": "E9A", "_Soll_EG_I": "E9A", "_Ist_EG": "E9A"},
            {"_Soll_EG_H": "E10", "_Soll_EG_I": "E11", "_Ist_EG": "E10"},
            {"_Soll_EG_H": "E10", "_Soll_EG_I": "E11", "_Ist_EG": "Unbesetzt"},
        ]
    )

    pivot = module._build_soll_ist_band_pivot(
        work_df, ist_eg_cols=["E9A", "E10"], IST_UNBESETZT="Unbesetzt", IST_NOT_FOUND="Nicht gefunden",
    )

    assert list(pivot.index) == ["E9A", "E10-E11"]
    assert pivot.loc["E9A", "E9A"] == 2
    assert pivot.loc["E10-E11", "E10"] == 1
    assert pivot.loc["E10-E11", "Unbesetzt"] == 1
    assert pivot["Gesamt"].tolist() == [2, 2]


def test_soll_eg_band_range_members_expands_multi_group_bands():
    module = _load_compact_page_module()

    from config.settings import TARIFF_GROUPS
    eg_order = {g: i for i, g in enumerate(TARIFF_GROUPS)}

    assert module._soll_eg_band_range_members("E9A", eg_order) == {"E9A"}
    assert module._soll_eg_band_range_members("E10-E11", eg_order) == {"E10", "E11"}
    # A band spanning E9A..E9C also covers the group(s) ranked strictly between them.
    assert module._soll_eg_band_range_members("E9A-E9C", eg_order) == {"E9A", "E9B", "E9C"}


def test_build_soll_ist_distribution_figure_uses_explicit_band_rows():
    module = _load_compact_page_module()

    band_pivot = pd.DataFrame(
        {
            "E9A": [2, 0],
            "E10": [0, 1],
            "E11": [0, 1],
            "Unbesetzt": [0, 1],
        },
        index=pd.Index(["E9A", "E10-E11"], name="Soll-EG-Spanne"),
    )
    no_soll_eg_row = pd.Series({"E10": 1})

    fig = module._build_soll_ist_distribution_figure(
        band_pivot,
        list(band_pivot.index),
        ist_eg_cols=["E9A", "E10", "E11"],
        IST_UNBESETZT="Unbesetzt",
        IST_NOT_FOUND="Nicht gefunden",
        no_soll_eg_row=no_soll_eg_row,
        print_mode=False,
    )

    # Y-axis carries the explicit band label, not a toggle-collapsed single value.
    y_categories = set(fig.layout.yaxis.categoryarray)
    assert "E10-E11" in y_categories
    assert "(Keine Soll-EG)" in y_categories  # special row for positions without a usable Soll-EG
    # One trace per Ist-EG/special column that is actually present in the pivot.
    trace_names = {trace.name for trace in fig.data}
    assert trace_names == {"E9A", "E10", "E11", "Unbesetzt"}
    # Plotly renders the first categoryarray entry at the bottom and the last at
    # the top: special row at the very bottom, then ascending -> highest band on top.
    assert list(fig.layout.yaxis.categoryarray) == ["(Keine Soll-EG)", "E9A", "E10-E11"]


def test_build_soll_ist_band_fit_summary_splits_passend_and_abweichend():
    module = _load_compact_page_module()

    band_pivot = pd.DataFrame(
        {
            "E9A": [2, 0],
            "E10": [0, 1],
            "E11": [1, 1],
            "Unbesetzt": [0, 1],
            "Nicht gefunden": [1, 0],
        },
        index=pd.Index(["E9A", "E10-E11"], name="Soll-EG-Spanne"),
    )

    summary = module._build_soll_ist_band_fit_summary(
        band_pivot, ist_eg_cols=["E9A", "E10", "E11"], IST_UNBESETZT="Unbesetzt", IST_NOT_FOUND="Nicht gefunden",
    )

    row_e9a = summary[summary["Soll-EG-Spanne"] == "E9A"].iloc[0]
    # Row "E9A": E9A (2) is in-range -> Passend. E11 (1) is out-of-range -> Abweichend.
    assert row_e9a["Passend"] == 2
    assert row_e9a["Abweichend"] == 1
    assert row_e9a["Nicht gefunden"] == 1
    assert row_e9a["Planstellen"] == 4
    assert row_e9a["Passquote"] == 0.5

    row_band = summary[summary["Soll-EG-Spanne"] == "E10-E11"].iloc[0]
    # Row "E10-E11": both E10 (1) and E11 (1) are in-range -> Passend.
    assert row_band["Passend"] == 2
    assert row_band["Abweichend"] == 0
    assert row_band["Unbesetzt"] == 1
    assert row_band["Planstellen"] == 3


def test_build_soll_ist_fit_figure_pairs_soll_bar_with_stacked_ist_segments():
    module = _load_compact_page_module()

    fit_summary_df = pd.DataFrame([
        {"Soll-EG-Spanne": "E9A", "Passend": 2, "Abweichend": 1, "Unbesetzt": 0, "Nicht gefunden": 1, "Planstellen": 4, "Passquote": 0.5},
        {"Soll-EG-Spanne": "E10-E11", "Passend": 2, "Abweichend": 0, "Unbesetzt": 1, "Nicht gefunden": 0, "Planstellen": 3, "Passquote": 2 / 3},
    ])

    fig = module._build_soll_ist_fit_figure(fit_summary_df, print_mode=False)

    # Plotly renders the first categoryarray entry at the bottom and the last at
    # the top, so the highest pay-grade band ("E10-E11") must come last here.
    assert list(fig.layout.yaxis.categoryarray) == ["E9A", "E10-E11"]

    traces_by_name = {trace.name: trace for trace in fig.data}
    assert "SOLL (Planstellen)" in traces_by_name
    soll_trace = traces_by_name["SOLL (Planstellen)"]
    assert soll_trace.offsetgroup == "soll"
    assert list(soll_trace.x) == [4, 3]  # y_order ascending: E9A first, then E10-E11

    ist_traces = [t for t in fig.data if t.offsetgroup == "ist"]
    assert len(ist_traces) == 4  # Passend, Abweichend, Unbesetzt, Nicht gefunden
    # IST segments must be manually stacked via cumulative `base`, not overlapping at 0.
    passend_trace = next(t for t in ist_traces if "Passend" in t.name)
    abweichend_trace = next(t for t in ist_traces if "Abweichend" in t.name)
    assert list(passend_trace.base) == [0, 0]
    assert list(abweichend_trace.base) == list(passend_trace.x)

    assert fig.layout.barmode == "group"


def test_style_soll_ist_band_matrix_colors_in_range_green_and_out_of_range_red():
    module = _load_compact_page_module()

    pivot_display = pd.DataFrame(
        {
            "E9A": [2, 0, 0],
            "E10": [0, 1, 0],
            "E11": [1, 0, 0],
            "Unbesetzt": [0, 1, 0],
            "Gesamt": [3, 2, 0],
        },
        index=pd.Index(["E9A", "E10-E11", "Gesamt"]),
    )

    styler = module._style_soll_ist_band_matrix(pivot_display, data_cols=["E9A", "E10", "E11"])
    styles = styler._compute().ctx  # {(row_pos, col_pos): [("background-color", value)]}

    col_pos = {col: idx for idx, col in enumerate(pivot_display.columns)}
    row_pos = {row: idx for idx, row in enumerate(pivot_display.index)}

    def _style_at(row: str, col: str) -> str:
        return "; ".join(f"{prop}: {val}" for prop, val in styles.get((row_pos[row], col_pos[col]), []))

    # Row "E9A": E9A is in-range and populated -> green. E11 is out-of-range and populated -> red.
    assert module._BAND_IN_RANGE_STYLE in _style_at("E9A", "E9A")
    assert module._BAND_OUT_OF_RANGE_STYLE in _style_at("E9A", "E11")
    # Row "E10-E11": both E10 and E11 are in-range -> green.
    assert module._BAND_IN_RANGE_STYLE in _style_at("E10-E11", "E10")
    # Empty data cells stay uncolored.
    assert _style_at("E9A", "E10") == ""
    # Special/Gesamt columns and the Gesamt row are never colored.
    assert _style_at("E10-E11", "Unbesetzt") == ""
    assert _style_at("E9A", "Gesamt") == ""
    assert _style_at("Gesamt", "E9A") == ""


def test_render_ist_soll_koepfe_tab_shows_explicit_band_matrix_below_original(monkeypatch):
    """The Soll-Ist matrix is followed by a second table with explicit pay-grade ranges."""
    module = _load_compact_page_module()
    raw_result = _sample_raw_logic_result()

    dataframe_calls = []
    subheaders = []

    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: None)
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)
    monkeypatch.setattr(
        module,
        "render_compensation_planlevel_section",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(module.st, "toggle", lambda *args, **kwargs: True)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda text, *args, **kwargs: subheaders.append(text))
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module.st,
        "dataframe",
        lambda data, *args, **kwargs: dataframe_calls.append({"data": data, "kwargs": kwargs}),
    )
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E9A")

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    assert module.t("compact.ist_soll_heads.matrix_band.heading") in subheaders

    # Both tables render as styled pandas Stylers (conditional green/red cell coloring):
    # the first is the original toggle-collapsed matrix, the second the explicit band matrix.
    assert len(dataframe_calls) >= 2
    original_styler = dataframe_calls[0]["data"]
    band_styler = dataframe_calls[1]["data"]
    assert isinstance(original_styler, pd.io.formats.style.Styler)
    assert isinstance(band_styler, pd.io.formats.style.Styler)
    original_df, band_df = original_styler.data, band_styler.data
    assert band_df.shape != original_df.shape or list(band_df.index) != list(original_df.index)
    # The band table exposes the real "E10-E11" range instead of a toggle-collapsed single value.
    assert "E10-E11" in list(band_df.index)
    assert dataframe_calls[1]["kwargs"].get("key") == "ist_vs_soll_koepfe_matrix_band"


def test_render_ist_soll_koepfe_tab_print_mode_skips_toggle(monkeypatch):
    module = _load_compact_page_module()
    captured = {"use_max_eg": None}

    def fake_raw_logic(use_max_eg=True):
        captured["use_max_eg"] = use_max_eg
        return _sample_raw_logic_result()

    def fail_toggle(*args, **kwargs):
        raise AssertionError("toggle should not be called in print mode")

    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", fake_raw_logic)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: None)
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)

    monkeypatch.setattr(module.st, "toggle", fail_toggle)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: None)

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=True)
    assert captured["use_max_eg"] is True


def test_render_ist_soll_koepfe_tab_warns_on_missing_tariff_group(monkeypatch):
    module = _load_compact_page_module()
    warnings = []

    monkeypatch.setattr(
        module,
        "_build_soll_ist_pivot_raw_logic",
        lambda use_max_eg=True: (None, [], [], "Unbesetzt", "Nicht gefunden", pd.DataFrame(), 0, pd.Series(dtype=int), {}),
    )
    monkeypatch.setattr(module.st, "toggle", lambda *args, **kwargs: True)
    monkeypatch.setattr(module.st, "warning", lambda text, *args, **kwargs: warnings.append(text))

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)
    assert warnings
    assert "Bewertung Tarifgruppe" in warnings[0]


def test_normalize_personalnummer_keys_handles_empty_and_numeric_values():
    module = _load_compact_page_module()

    series = pd.Series(["", None, 1, 1.0, "000123", "  45  ", "nan"])
    normalized = module._normalize_personalnummer_keys(series)

    assert normalized.tolist() == ["", "", "000001", "000001", "000123", "000045", ""]
