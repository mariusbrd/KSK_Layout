from pathlib import Path
import importlib.util
import io
import sys

import pandas as pd
import pytest

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
    kpi_items = captured["kpi_calls"][0]
    titles = [item["title"] for item in kpi_items]
    # KPI-Karten sind jetzt direkt aus fit_summary_df (Band-Fit-Uebersicht) abgeleitet -
    # dieselbe Quelle wie Band-Tabelle/Fit-Chart/Fit-Tabelle weiter unten (Konsistenzgarantie).
    assert "Planstellen (Soll-EG-Band vorhanden)" in titles
    assert "Passend" in titles
    assert "Abweichend" in titles
    assert "Passquote gesamt" in titles
    assert "Nicht definierte Sollstelle in Arbeit" in titles

    by_title = {item["title"]: item for item in kpi_items}
    # work_df (aus _sample_raw_logic_result): 3x Band "E9A" (exakt passend) + 1x Band
    # "E10-E11" (Ist=E10, passend im Band) -> alle 4 Planstellen "Passend", 0 "Abweichend".
    assert by_title["Planstellen (Soll-EG-Band vorhanden)"]["value"] == "4"
    assert by_title["Passend"]["value"] == "4"
    assert by_title["Abweichend"]["value"] == "0"
    assert by_title["Passquote gesamt"]["value"] == "100,0%"
    technical_kpi = by_title["Nicht definierte Sollstelle in Arbeit"]
    assert technical_kpi["value"] == "4"
    # Band-Matrix + Fit-Uebersicht haben je einen eigenen Download (Original-Matrix entfaellt).
    assert captured["downloads"] == 2
    assert captured["dataframes"] >= 1
    assert captured["plots"] >= 1
    joined_captions = "\n".join(captured["captions"])
    assert "Roh-nahe Kontrolllogik" in joined_captions
    assert "Kontrollsummen 0,01-Fälle" in joined_captions


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


def test_aggregate_detail_breakdown_counts_and_sorts_descending():
    module = _load_compact_page_module()

    subset = pd.DataFrame({
        "Organisationseinheit": ["OE A", "OE A", "OE B", "OE A", "OE C"],
    })
    result = module._aggregate_detail_breakdown(subset, "Organisationseinheit")

    assert list(result.columns) == ["Organisationseinheit", "Anzahl"]
    assert result.iloc[0].tolist() == ["OE A", 3]
    # Ties (OE B / OE C both count 1) - order between them is not asserted, just presence.
    assert set(result["Organisationseinheit"][1:]) == {"OE B", "OE C"}
    assert result["Anzahl"].sum() == 5


def test_aggregate_detail_breakdown_collapses_long_tail_into_sonstige():
    module = _load_compact_page_module()

    # 12 distinct single-occurrence values -> with top_n=10, the two smallest get collapsed.
    subset = pd.DataFrame({"Planstelle": [f"Rolle {i}" for i in range(12)]})
    result = module._aggregate_detail_breakdown(subset, "Planstelle", top_n=10)

    assert len(result) == 11  # 10 individual + 1 "Sonstige"
    assert "Sonstige" in result["Planstelle"].tolist()
    sonstige_row = result[result["Planstelle"] == "Sonstige"].iloc[0]
    assert sonstige_row["Anzahl"] == 2
    assert result["Anzahl"].sum() == 12


def test_aggregate_detail_breakdown_handles_empty_and_missing_column():
    module = _load_compact_page_module()

    empty_result = module._aggregate_detail_breakdown(pd.DataFrame({"Organisationseinheit": []}), "Organisationseinheit")
    assert empty_result.empty
    assert list(empty_result.columns) == ["Organisationseinheit", "Anzahl"]

    missing_col_result = module._aggregate_detail_breakdown(pd.DataFrame({"Other": [1, 2]}), "Organisationseinheit")
    assert missing_col_result.empty


def test_aggregate_detail_breakdown_maps_missing_values_to_ohne_angabe():
    module = _load_compact_page_module()

    subset = pd.DataFrame({"Organisationseinheit": ["OE A", None, "", "  ", "OE A"]})
    result = module._aggregate_detail_breakdown(subset, "Organisationseinheit")

    ohne_angabe_row = result[result["Organisationseinheit"] == "(ohne Angabe)"].iloc[0]
    assert ohne_angabe_row["Anzahl"] == 3  # None, "", "  " all collapse into this bucket
    oe_a_row = result[result["Organisationseinheit"] == "OE A"].iloc[0]
    assert oe_a_row["Anzahl"] == 2


def test_bucket_top_n_returns_same_length_series_with_tail_collapsed():
    module = _load_compact_page_module()

    series = pd.Series(["A", "A", "A", "B", "B", "C", "D"])
    bucketed = module._bucket_top_n(series, top_n=2)

    assert len(bucketed) == len(series)
    assert list(bucketed) == ["A", "A", "A", "B", "B", "Sonstige", "Sonstige"]


def test_bucket_top_n_no_bucketing_needed_when_within_top_n():
    module = _load_compact_page_module()

    series = pd.Series(["A", "B", "C"])
    bucketed = module._bucket_top_n(series, top_n=10)

    assert list(bucketed) == ["A", "B", "C"]
    assert "Sonstige" not in bucketed.values


def test_aggregate_detail_breakdown_stacked_crosstab_matches_totals():
    module = _load_compact_page_module()

    subset = pd.DataFrame({
        "Planstelle": ["Berater/in", "Berater/in", "Leiter/in", "Leiter/in", "Leiter/in"],
        "Organisationseinheit": ["OE A", "OE B", "OE A", "OE A", "OE B"],
    })
    result = module._aggregate_detail_breakdown_stacked(subset, "Planstelle", "Organisationseinheit")

    assert result.loc["Berater/in", "OE A"] == 1
    assert result.loc["Berater/in", "OE B"] == 1
    assert result.loc["Leiter/in", "OE A"] == 2
    assert result.loc["Leiter/in", "OE B"] == 1
    # Rows sorted by total count descending: Leiter/in (3) before Berater/in (2).
    assert list(result.index) == ["Leiter/in", "Berater/in"]
    assert int(result.sum().sum()) == 5


def test_aggregate_detail_breakdown_stacked_collapses_long_tail_group_into_sonstige():
    module = _load_compact_page_module()

    # 3 distinct OEs, group_top_n=2 -> the smallest OE must collapse into "Sonstige".
    subset = pd.DataFrame({
        "Planstelle": ["Berater/in"] * 6,
        "Organisationseinheit": ["OE A", "OE A", "OE A", "OE B", "OE B", "OE C"],
    })
    result = module._aggregate_detail_breakdown_stacked(subset, "Planstelle", "Organisationseinheit", group_top_n=2)

    assert set(result.columns) == {"OE A", "OE B", "Sonstige"}
    assert result.loc["Berater/in", "Sonstige"] == 1  # the single "OE C" row


def test_aggregate_detail_breakdown_stacked_handles_empty_and_missing_columns():
    module = _load_compact_page_module()

    assert module._aggregate_detail_breakdown_stacked(pd.DataFrame(), "Planstelle", "Organisationseinheit").empty
    assert module._aggregate_detail_breakdown_stacked(
        pd.DataFrame({"Other": [1]}), "Planstelle", "Organisationseinheit"
    ).empty


def test_aggregate_direction_split_counts_klasse_per_value_group_cell():
    module = _load_compact_page_module()

    subset = pd.DataFrame({
        "Planstelle": ["Berater/in", "Berater/in", "Berater/in", "Leiter/in"],
        "Organisationseinheit": ["OE A", "OE A", "OE B", "OE A"],
        "_Klasse": ["Übergruppiert", "Untergruppiert", "Übergruppiert", "Übergruppiert"],
    })
    result = module._aggregate_direction_split(subset, "Planstelle", "Organisationseinheit")

    assert result[("Berater/in", "OE A")] == {"Übergruppiert": 1, "Untergruppiert": 1}
    assert result[("Berater/in", "OE B")] == {"Übergruppiert": 1}
    assert result[("Leiter/in", "OE A")] == {"Übergruppiert": 1}
    assert ("Leiter/in", "OE B") not in result  # no rows for this combination


def test_aggregate_direction_split_handles_empty_and_missing_columns():
    module = _load_compact_page_module()

    assert module._aggregate_direction_split(pd.DataFrame(), "Planstelle", "Organisationseinheit") == {}
    assert module._aggregate_direction_split(
        pd.DataFrame({"Planstelle": ["A"], "Organisationseinheit": ["OE A"]}),  # missing _Klasse
        "Planstelle", "Organisationseinheit",
    ) == {}


def test_assign_breakdown_colors_gives_unique_colors_and_fixed_sonstige_gray():
    module = _load_compact_page_module()

    colors = module._assign_breakdown_colors(["OE A", "OE B", "OE C", "Sonstige"])

    assert colors["Sonstige"] == module._BREAKDOWN_SONSTIGE_COLOR
    real_colors = [colors["OE A"], colors["OE B"], colors["OE C"]]
    assert len(set(real_colors)) == 3  # all distinct
    assert module._BREAKDOWN_SONSTIGE_COLOR not in real_colors


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

    # Regression: marker.color must stay a single value per trace, not a per-bar array -
    # a per-bar color array (previously used to grey out the "(Keine Soll-EG)" row) makes
    # Plotly's legend swatch fall back to a generic grey instead of the trace's real color.
    # The dimming must happen via marker.opacity (an array) instead.
    for trace in fig.data:
        assert isinstance(trace.marker.color, str), (
            f"trace {trace.name!r} has a non-scalar marker.color ({trace.marker.color!r}) - "
            "this breaks the legend swatch color"
        )
    unbesetzt_trace = next(t for t in fig.data if t.name == "Unbesetzt")
    nosoll_pos = list(unbesetzt_trace.y).index("(Keine Soll-EG)")
    regular_pos = list(unbesetzt_trace.y).index("E9A")
    assert unbesetzt_trace.marker.opacity[nosoll_pos] == pytest.approx(0.35)
    assert unbesetzt_trace.marker.opacity[regular_pos] == pytest.approx(1.0)


def test_build_soll_ist_distribution_figure_keeps_nosoll_ist_eg_not_in_regular_matrix():
    """Regression: no_soll_eg_row (occupied positions without a usable Soll-EG) can contain
    Ist-EG values that never appear in the regular matrix (e.g. a data-quality artifact like
    "1" instead of a real tariff group). Such values must still get their own trace/column
    instead of being silently dropped from the "(Keine Soll-EG)" row."""
    module = _load_compact_page_module()

    band_pivot = pd.DataFrame(
        {"E9A": [2]},
        index=pd.Index(["E9A"], name="Soll-EG-Spanne"),
    )
    # "1" is not part of ist_eg_cols and never appears in band_pivot's columns.
    no_soll_eg_row = pd.Series({"E10": 1, "1": 3})

    fig = module._build_soll_ist_distribution_figure(
        band_pivot,
        list(band_pivot.index),
        ist_eg_cols=["E9A", "E10"],
        IST_UNBESETZT="Unbesetzt",
        IST_NOT_FOUND="Nicht gefunden",
        no_soll_eg_row=no_soll_eg_row,
        print_mode=False,
    )

    trace_names = {trace.name for trace in fig.data}
    assert "1" in trace_names, "Ist-EG value outside the regular matrix must still get a trace"

    # Sum every trace's value at the "(Keine Soll-EG)" row - must equal no_soll_eg_row's total,
    # not just the subset that happens to overlap with ist_eg_cols.
    nosoll_total = sum(
        (x or 0)
        for trace in fig.data
        for y, x in zip(trace.y, trace.x)
        if y == "(Keine Soll-EG)"
    )
    assert nosoll_total == int(no_soll_eg_row.sum()) == 4


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

    # Regression: for manually base-stacked bars, Plotly's hover %{x} resolves to the
    # cumulative stack position (base+x), not the segment's own value - e.g. for the
    # "Unbesetzt" segment of E10-E11 (base=2, x=1), a naive %{x} hovertemplate would show
    # "3" instead of "1". The hovertemplate must reference %{customdata}, fed with the raw
    # per-row segment values, so the displayed number is always the segment's own value.
    unbesetzt_trace = next(t for t in ist_traces if "Unbesetzt" in t.name)
    assert "%{customdata" in unbesetzt_trace.hovertemplate
    assert "%{x" not in unbesetzt_trace.hovertemplate
    assert list(unbesetzt_trace.customdata) == list(unbesetzt_trace.x)
    # For E10-E11, base=2 and x=1 -> a cumulative reading would wrongly show 3.
    e10_e11_idx = list(unbesetzt_trace.y).index("E10-E11")
    assert unbesetzt_trace.base[e10_e11_idx] == 2
    assert unbesetzt_trace.customdata[e10_e11_idx] == 1


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


def test_render_ist_soll_koepfe_tab_shows_explicit_band_matrix(monkeypatch):
    """The tab renders exactly one matrix table, using explicit pay-grade ranges (no more
    toggle-collapsed single-EG matrix alongside it)."""
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

    assert len(dataframe_calls) >= 1
    band_styler = dataframe_calls[0]["data"]
    assert isinstance(band_styler, pd.io.formats.style.Styler)
    band_df = band_styler.data
    # The band table exposes the real "E10-E11" range instead of a toggle-collapsed single value.
    assert "E10-E11" in list(band_df.index)
    assert dataframe_calls[0]["kwargs"].get("key") == "ist_vs_soll_koepfe_matrix_band"


def test_render_ist_soll_koepfe_tab_detail_kpi_percentages_match_donut_denominator(monkeypatch):
    """Regression: the detail KPI tiles (Passend/Übergruppierungsquote/Unbesetztquote) must use
    the same denominator (n_total, i.e. all positions in the band) as the donut chart below them.
    Previously Passend/Übergruppiert divided by n_besetzt (excluding Unbesetzt/Nicht gefunden)
    while Unbesetzt already divided by n_total and the donut's auto-computed Plotly percentages
    are always over the full pie (n_total) - the mismatch made the KPI tiles and the donut show
    different percentages for the same underlying counts."""
    module = _load_compact_page_module()

    # 10 rows: 6 Passend, 2 Übergruppiert, 1 Untergruppiert, 1 Unbesetzt -> n_besetzt=9, n_total=10.
    # A wrong (n_besetzt) denominator would show 66.7%/22.2% instead of the correct 60.0%/20.0%.
    pivot = pd.DataFrame({"E9A": [10]}, index=pd.Index(["E9A"], name="Soll-EG"))
    rows = (
        [{"_Ist_EG": "E9A"}] * 6
        + [{"_Ist_EG": "E12"}] * 2
        + [{"_Ist_EG": "E5"}] * 1
        + [{"_Ist_EG": "Unbesetzt"}] * 1
    )
    for row in rows:
        row.update({"_Soll_EG": "E9A", "_Soll_EG_H": "E9A", "_Soll_EG_I": "E9A"})
    work_df = pd.DataFrame(rows)
    summary = {
        "regular_total": 10, "regular_occupied": 9, "regular_vacant": 1,
        "matrix_not_found": 0, "matrix_occupied": 9, "technical_non9xxx_occupied": 0,
        "technical_total": 0, "technical_9xxx_total": 0, "technical_non9xxx_total": 0,
    }
    raw_result = (pivot, ["E9A"], ["E5", "E9A", "E12"], "Unbesetzt", "Nicht gefunden", work_df, 0, pd.Series(dtype=int), summary)

    kpi_calls = []
    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: kpi_calls.append(kpis))
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E9A")
    monkeypatch.setattr(module.st, "radio", lambda label, options=None, index=0, **k: options[index])

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    detail_kpis = {item["title"]: item for item in kpi_calls[1]}
    assert detail_kpis["Passend"]["value"] == "60,0%"
    assert detail_kpis["Übergruppierungsquote"]["value"] == "20,0%"
    assert detail_kpis["Unbesetztquote"]["value"] == "10,0%"
    # "Unbesetzt" is a neutral category, not a value judgement - grey accent, not green.
    assert detail_kpis["Unbesetztquote"]["status"] == "neutral"


def test_render_ist_soll_koepfe_tab_detail_section_uses_band_based_selection(monkeypatch):
    """The Detailbereich drilldown selects a pay-grade BAND (e.g. "E10-E11"), not a
    toggle-collapsed single value, and its KPI tiles use the simplified "Passend" category
    (the old "Passend (exakt)"/"Passend im Band" split was tied to the removed toggle)."""
    module = _load_compact_page_module()
    raw_result = _sample_raw_logic_result()

    selectbox_options = {}
    kpi_calls = []

    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: kpi_calls.append(kpis))
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)

    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: None)

    def fake_selectbox(label, options=None, **kwargs):
        selectbox_options["options"] = list(options) if options is not None else None
        return "E10-E11"

    monkeypatch.setattr(module.st, "selectbox", fake_selectbox)

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    # Selectbox-Optionen sind Band-Labels (aus band_pivot.index), nicht toggle-kollabierte
    # Einzelwerte ("E9A", "E11" o.ae.).
    assert selectbox_options["options"] == ["E9A", "E10-E11"]

    # Zweiter render_kpi_cards_styled()-Aufruf ist der Detailbereich (erster ist die
    # Top-KPI-Sektion). Fuer Band "E10-E11" (Ist=E10, innerhalb der Spanne) muss die
    # vereinfachte "Passend"-Kachel erscheinen, keine "Passend (exakt)"/"Passend im Band"-Kacheln.
    assert len(kpi_calls) >= 2
    detail_titles = [item["title"] for item in kpi_calls[1]]
    assert any(title.startswith("Passend") for title in detail_titles)
    assert not any("exakt" in title for title in detail_titles)
    assert "Passend im Band" not in detail_titles
    assert "Übergruppierungsquote" in detail_titles
    assert "Unbesetztquote" in detail_titles


def test_render_ist_soll_koepfe_tab_detail_breakdown_shows_oe_and_type_for_selected_subset(monkeypatch):
    """The new OE/Planstellentyp breakdown below the donut+bar charts must reflect exactly the
    rows classified into the selected subset (default: 'Außerhalb der Range' = Übergruppiert +
    Untergruppiert) for the currently selected band - never raw per-row/person data, only
    aggregated counts."""
    module = _load_compact_page_module()

    pivot = pd.DataFrame(
        {"E9A": [0], "E10": [1], "E11": [0], "E12": [1], "Unbesetzt": [0], "Gesamt": [2]},
        index=pd.Index(["E11"], name="Soll-EG"),
    )
    work_df = pd.DataFrame([
        # Band E10-E11: Passend (Ist=E10), Übergruppiert (Ist=E12), Untergruppiert (Ist=E9A).
        {"_Ist_EG": "E10", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Alpha", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Beta", "Planstelle": "Leiter/in"},
        {"_Ist_EG": "E9A", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Beta", "Planstelle": "Berater/in"},
    ])
    summary = {
        "regular_total": 3, "regular_occupied": 3, "regular_vacant": 0,
        "matrix_not_found": 0, "matrix_occupied": 3, "technical_non9xxx_occupied": 0,
        "technical_total": 0, "technical_9xxx_total": 0, "technical_non9xxx_total": 0,
    }
    raw_result = (pivot, ["E11"], ["E9A", "E10", "E11", "E12"], "Unbesetzt", "Nicht gefunden", work_df, 0, pd.Series(dtype=int), summary)

    plotly_calls = []
    radio_calls = {}

    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: None)
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)

    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E10-E11")
    monkeypatch.setattr(module.st, "plotly_chart", lambda fig, *a, **k: plotly_calls.append(fig))

    def fake_radio(label, options=None, index=0, **kwargs):
        radio_calls["options"] = list(options) if options is not None else None
        return options[index]

    monkeypatch.setattr(module.st, "radio", fake_radio)

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    # Default subset (index=0) must be "Außerhalb der Range".
    assert radio_calls["options"][0] == module.t("compact.ist_soll_heads.detail.breakdown.subset.outside")

    # The last two rendered charts are the OE- and Planstellentyp-breakdown (donut+bar come first).
    oe_fig, type_fig = plotly_calls[-2], plotly_calls[-1]

    oe_counts = dict(zip(oe_fig.data[0].y, oe_fig.data[0].x))
    assert oe_counts == {"OE Beta": 2}  # Both deviating rows (E12, E9A) belong to OE Beta.

    type_counts = dict(zip(type_fig.data[0].y, type_fig.data[0].x))
    assert type_counts == {"Leiter/in": 1, "Berater/in": 1}

    # No person-level column (PersNr, Personalnummer, names) anywhere in the rendered figures.
    for fig in (oe_fig, type_fig):
        for trace in fig.data:
            assert "PersNr" not in str(trace.hovertemplate)
            assert "Personalnummer" not in str(trace.hovertemplate)


def test_render_ist_soll_koepfe_tab_detail_breakdown_colors_link_oe_and_type_charts(monkeypatch):
    """Regression for the OE-color-linking feature: each Organisationseinheit gets a fixed,
    unique color in the OE chart. A Planstellentyp spanning MULTIPLE OEs must render as a
    genuinely stacked bar (barmode='stack', one trace per OE), and each OE-segment's color must
    exactly match that OE's bar color in the OE chart."""
    module = _load_compact_page_module()

    pivot = pd.DataFrame(
        {"E5": [1], "E9A": [0], "E10": [0], "E11": [0], "E12": [2], "Unbesetzt": [0], "Gesamt": [4]},
        index=pd.Index(["E11"], name="Soll-EG"),
    )
    # "Berater/in" spans two OEs (Alpha, Beta); "Leiter/in" only OE Gamma - exercises both the
    # multi-segment stack and the single-segment ("looks like a plain bar") case. All four rows
    # are OUTSIDE the E10-E11 range (E12 -> Übergruppiert, E5 -> Untergruppiert) so all land in
    # the default "Außerhalb der Range" subset.
    work_df = pd.DataFrame([
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Alpha", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Beta", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Gamma", "Planstelle": "Leiter/in"},
        {"_Ist_EG": "E5", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Alpha", "Planstelle": "Berater/in"},
    ])
    summary = {
        "regular_total": 4, "regular_occupied": 4, "regular_vacant": 0,
        "matrix_not_found": 0, "matrix_occupied": 4, "technical_non9xxx_occupied": 0,
        "technical_total": 0, "technical_9xxx_total": 0, "technical_non9xxx_total": 0,
    }
    raw_result = (pivot, ["E11"], ["E5", "E9A", "E10", "E11", "E12"], "Unbesetzt", "Nicht gefunden", work_df, 0, pd.Series(dtype=int), summary)

    plotly_calls = []
    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: None)
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E10-E11")
    monkeypatch.setattr(module.st, "plotly_chart", lambda fig, *a, **k: plotly_calls.append(fig))
    monkeypatch.setattr(module.st, "radio", lambda label, options=None, index=0, **k: options[index])

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    oe_fig, type_fig = plotly_calls[-2], plotly_calls[-1]

    # OE chart: one trace, per-bar color array, no legend (Y-axis already names each OE).
    assert len(oe_fig.data) == 1
    assert oe_fig.data[0].showlegend is False
    oe_bar_colors = dict(zip(oe_fig.data[0].y, oe_fig.data[0].marker.color))
    assert len(set(oe_bar_colors.values())) == 3  # Alpha/Beta/Gamma each get a distinct color

    # Type chart: genuinely stacked (barmode='stack'), one trace per OE.
    assert type_fig.layout.barmode == "stack"
    type_traces_by_oe = {trace.name: trace for trace in type_fig.data}
    assert set(type_traces_by_oe) == {"OE Alpha", "OE Beta", "OE Gamma"}

    # "Berater/in" (2x Alpha, 1x Beta) must appear as two non-zero segments; "Leiter/in" (1x
    # Gamma) as a single segment.
    berater_alpha = dict(zip(type_traces_by_oe["OE Alpha"].y, type_traces_by_oe["OE Alpha"].x))["Berater/in"]
    berater_beta = dict(zip(type_traces_by_oe["OE Beta"].y, type_traces_by_oe["OE Beta"].x))["Berater/in"]
    leiter_gamma = dict(zip(type_traces_by_oe["OE Gamma"].y, type_traces_by_oe["OE Gamma"].x))["Leiter/in"]
    assert (berater_alpha, berater_beta, leiter_gamma) == (2, 1, 1)

    # The core requirement: an OE's color must be IDENTICAL between the OE chart's bar and the
    # matching OE-trace in the stacked type chart.
    for oe_name, bar_color in oe_bar_colors.items():
        assert type_traces_by_oe[oe_name].marker.color == bar_color


def test_render_ist_soll_koepfe_tab_hover_shows_direction_split_for_outside_range(monkeypatch):
    """Regression: for the 'Außerhalb der Range' subset (Übergruppiert + Untergruppiert
    combined), both the OE chart and the Planstellentyp chart must carry a per-point
    'davon übergruppiert/untergruppiert' breakdown via customdata, so hovering reveals whether
    the deviations in that OE/that Planstellentyp-OE-segment skew over- or undergraded."""
    module = _load_compact_page_module()

    pivot = pd.DataFrame(
        {"E5": [1], "E9A": [0], "E10": [0], "E11": [0], "E12": [2], "Unbesetzt": [0], "Gesamt": [4]},
        index=pd.Index(["E11"], name="Soll-EG"),
    )
    # OE Alpha / "Berater/in" has BOTH one Übergruppiert (E12) and one Untergruppiert (E5) row -
    # the core scenario the user reported (mixed direction hidden behind a single bar length).
    work_df = pd.DataFrame([
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Alpha", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E5", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Alpha", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Beta", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Gamma", "Planstelle": "Leiter/in"},
    ])
    summary = {
        "regular_total": 4, "regular_occupied": 4, "regular_vacant": 0,
        "matrix_not_found": 0, "matrix_occupied": 4, "technical_non9xxx_occupied": 0,
        "technical_total": 0, "technical_9xxx_total": 0, "technical_non9xxx_total": 0,
    }
    raw_result = (pivot, ["E11"], ["E5", "E9A", "E10", "E11", "E12"], "Unbesetzt", "Nicht gefunden", work_df, 0, pd.Series(dtype=int), summary)

    plotly_calls = []
    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: None)
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E10-E11")
    monkeypatch.setattr(module.st, "plotly_chart", lambda fig, *a, **k: plotly_calls.append(fig))
    monkeypatch.setattr(module.st, "radio", lambda label, options=None, index=0, **k: options[index])

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    oe_fig, type_fig = plotly_calls[-2], plotly_calls[-1]

    # OE chart: hovertemplate must contain the direction-split placeholders, and OE Alpha's
    # customdata must show 1 übergruppiert + 1 untergruppiert (not just "2 total").
    assert "customdata[0]" in oe_fig.data[0].hovertemplate
    assert "customdata[1]" in oe_fig.data[0].hovertemplate
    oe_direction = dict(zip(oe_fig.data[0].y, oe_fig.data[0].customdata))
    assert list(oe_direction["OE Alpha"]) == [1, 1]
    assert list(oe_direction["OE Beta"]) == [1, 0]
    assert list(oe_direction["OE Gamma"]) == [1, 0]

    # Type chart: the "OE Alpha" trace's "Berater/in" point must carry the same [1, 1] split.
    alpha_trace = next(t for t in type_fig.data if t.name == "OE Alpha")
    assert "customdata[0]" in alpha_trace.hovertemplate
    alpha_direction = dict(zip(alpha_trace.y, alpha_trace.customdata))
    assert list(alpha_direction["Berater/in"]) == [1, 1]


def test_render_ist_soll_koepfe_tab_detail_excel_export_matches_charts_and_hover(monkeypatch):
    """Regression: the Detailbereich Excel export must contain everything the app itself shows
    for the current view - the whole-band KPI/donut summary, the whole-band Ist-EG distribution,
    the OE breakdown WITH the Übergruppiert/Untergruppiert split (from the hover), the
    Planstellentyp breakdown, the Planstellentyp x OE crosstab, and a flat Planstellentyp x OE
    direction-split detail table equivalent to the type chart's hover content."""
    module = _load_compact_page_module()

    pivot = pd.DataFrame(
        {"E5": [1], "E9A": [0], "E10": [0], "E11": [0], "E12": [2], "Unbesetzt": [1], "Gesamt": [5]},
        index=pd.Index(["E11"], name="Soll-EG"),
    )
    work_df = pd.DataFrame([
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Alpha", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E5", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Alpha", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E12", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Beta", "Planstelle": "Berater/in"},
        {"_Ist_EG": "E10", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Gamma", "Planstelle": "Leiter/in"},  # Passend, not in "outside"
        {"_Ist_EG": "Unbesetzt", "_Soll_EG": "E11", "_Soll_EG_H": "E10", "_Soll_EG_I": "E11",
         "Organisationseinheit": "OE Gamma", "Planstelle": "Leiter/in"},
    ])
    summary = {
        "regular_total": 5, "regular_occupied": 4, "regular_vacant": 1,
        "matrix_not_found": 0, "matrix_occupied": 4, "technical_non9xxx_occupied": 0,
        "technical_total": 0, "technical_9xxx_total": 0, "technical_non9xxx_total": 0,
    }
    raw_result = (pivot, ["E11"], ["E5", "E9A", "E10", "E11", "E12"], "Unbesetzt", "Nicht gefunden", work_df, 0, pd.Series(dtype=int), summary)

    downloads = []
    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: None)
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: downloads.append(kwargs))
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E10-E11")
    monkeypatch.setattr(module.st, "plotly_chart", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "radio", lambda label, options=None, index=0, **k: options[index])

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    detail_download = next(d for d in downloads if "detail_aufschluesselung" in d["file_name"])
    workbook = pd.ExcelFile(io.BytesIO(detail_download["data"]))

    assert set(workbook.sheet_names) == {
        "Übersicht", "Ist-Eingruppierung", "Organisationseinheiten",
        "Planstellentypen", "Planstellentypen je OE", "Planstellentyp Richtung je OE",
    }

    uebersicht = workbook.parse("Übersicht")
    row = dict(zip(uebersicht["Kennzahl"], uebersicht["Anzahl"]))
    assert row["Planstellen gesamt"] == 5
    assert row["Passend"] == 1
    assert row["Übergruppiert"] == 2
    assert row["Untergruppiert"] == 1
    assert row["Unbesetzt"] == 1

    ist_eg = workbook.parse("Ist-Eingruppierung")
    assert dict(zip(ist_eg["Ist-Entgeltgruppe"], ist_eg["Anzahl"]))["E12"] == 2

    oe_sheet = workbook.parse("Organisationseinheiten")
    assert set(oe_sheet.columns) >= {"Organisationseinheit", "Anzahl", "Übergruppiert", "Untergruppiert"}
    alpha_row = oe_sheet[oe_sheet["Organisationseinheit"] == "OE Alpha"].iloc[0]
    assert (alpha_row["Anzahl"], alpha_row["Übergruppiert"], alpha_row["Untergruppiert"]) == (2, 1, 1)

    richtung_sheet = workbook.parse("Planstellentyp Richtung je OE")
    alpha_typ_row = richtung_sheet[
        (richtung_sheet["Planstelle"] == "Berater/in") & (richtung_sheet["Organisationseinheit"] == "OE Alpha")
    ].iloc[0]
    assert (alpha_typ_row["Übergruppiert"], alpha_typ_row["Untergruppiert"]) == (1, 1)


def test_render_ist_soll_koepfe_tab_hover_omits_direction_split_for_single_category_subsets(monkeypatch):
    """For 'Innerhalb der Range' or 'Unbesetzt' (single _Klasse value each), the direction split
    is meaningless - the hovertemplate must stay plain (no customdata placeholders) instead of
    always showing a trivially-zero or misleading breakdown."""
    module = _load_compact_page_module()
    raw_result = _sample_raw_logic_result()

    plotly_calls = []
    monkeypatch.setattr(module, "_build_soll_ist_pivot_raw_logic", lambda use_max_eg=True: raw_result)
    monkeypatch.setattr(module, "render_kpi_cards_styled", lambda kpis: None)
    monkeypatch.setattr(module, "download_button_compat", lambda **kwargs: None)
    monkeypatch.setattr(module, "apply_legend_bottom", lambda fig: fig)
    monkeypatch.setattr(module.st, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E9A")
    monkeypatch.setattr(module.st, "plotly_chart", lambda fig, *a, **k: plotly_calls.append(fig))

    def fake_radio(label, options=None, index=0, **kwargs):
        # Select "Innerhalb der Range" (index 1) instead of the default "Außerhalb der Range".
        return options[1]

    monkeypatch.setattr(module.st, "radio", fake_radio)

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)

    oe_fig = plotly_calls[-2]
    assert "customdata" not in oe_fig.data[0].hovertemplate
    assert oe_fig.data[0].customdata is None


def test_render_ist_soll_koepfe_tab_never_shows_toggle(monkeypatch):
    """The Min/Max-toggle was removed entirely - use_max_eg is always hardcoded True and
    st.toggle must never be called, in both normal and print mode."""
    module = _load_compact_page_module()
    captured = {"use_max_eg": None}

    def fake_raw_logic(use_max_eg=True):
        captured["use_max_eg"] = use_max_eg
        return _sample_raw_logic_result()

    def fail_toggle(*args, **kwargs):
        raise AssertionError("st.toggle should never be called - the toggle was removed")

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
    monkeypatch.setattr(module.st, "selectbox", lambda *args, **kwargs: "E9A")

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=True)
    assert captured["use_max_eg"] is True

    module.render_ist_soll_koepfe_tab(pd.DataFrame(), print_mode=False)
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
