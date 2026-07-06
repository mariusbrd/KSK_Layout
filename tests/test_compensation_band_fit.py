from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def _load_compact_page_module():
    page_path = next((ROOT / "pages").glob("*_Kompakt.py"))
    spec = importlib.util.spec_from_file_location("compact_page_compensation_fit_test", page_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sample_comp_df() -> pd.DataFrame:
    return pd.DataFrame([
        # Band E9A (kein echtes Band): zwei besetzte Planstellen, eine davon Teilzeit
        # -> Passend deckt nicht das volle SOLL ab (Kapazitaetsluecke, keine Fehleingruppierung).
        {"Soll_Entgeltgruppe_H": "E9A", "Soll_Entgeltgruppe_I": "E9A", "Ist_Entgeltgruppe": "E9A",
         "Is_Vacant": False, "IST_MAK": 1.0, "SOLL_MAK_View": 1.0},
        {"Soll_Entgeltgruppe_H": "E9A", "Soll_Entgeltgruppe_I": "E9A", "Ist_Entgeltgruppe": "E9A",
         "Is_Vacant": False, "IST_MAK": 0.5, "SOLL_MAK_View": 1.0},
        # Band E10-E11 (echtes Gehaltsband): eine passende, eine abweichende,
        # eine vakante und eine Nicht-gefunden-Planstelle.
        {"Soll_Entgeltgruppe_H": "E10", "Soll_Entgeltgruppe_I": "E11", "Ist_Entgeltgruppe": "E10",
         "Is_Vacant": False, "IST_MAK": 1.0, "SOLL_MAK_View": 1.0},
        {"Soll_Entgeltgruppe_H": "E10", "Soll_Entgeltgruppe_I": "E11", "Ist_Entgeltgruppe": "E9A",
         "Is_Vacant": False, "IST_MAK": 0.8, "SOLL_MAK_View": 1.0},
        {"Soll_Entgeltgruppe_H": "E10", "Soll_Entgeltgruppe_I": "E11", "Ist_Entgeltgruppe": "Nicht zugeordnet",
         "Is_Vacant": True, "IST_MAK": 0.0, "SOLL_MAK_View": 1.0},
        {"Soll_Entgeltgruppe_H": "E10", "Soll_Entgeltgruppe_I": "E11", "Ist_Entgeltgruppe": "Nicht zugeordnet",
         "Is_Vacant": False, "IST_MAK": 0.9, "SOLL_MAK_View": 1.0},
    ])


def test_build_compact_compensation_planlevel_df_retains_soll_entgeltgruppe_h_and_i():
    """
    Regression test: build_compact_compensation_planlevel_df() must not silently
    drop Soll_Entgeltgruppe_H/_I via its trailing preferred_order column allowlist
    (this happened once - the columns were computed but filtered out at return).
    """
    module = _load_compact_page_module()

    prepared_df = pd.DataFrame([
        {
            "PersNr": "000001", "TrfGr": "E10", "St": 4,
            "Bewertung Tarifgruppe": "E10", "Text Gehaltsband": "bis E11",
            "Is_Vacant": False, "Sollarbeitszeit": 39.0,
            "FTE_assigned": 1.0, "MAK_Reporting": 1.0,
            "Total_Cost_Year": 60000.0, "EUR_Reporting": 60000.0,
            "Soll_FTE": 1.0,
        },
    ])

    comp_df = module.build_compact_compensation_planlevel_df(prepared_df)

    assert "Soll_Entgeltgruppe_H" in comp_df.columns
    assert "Soll_Entgeltgruppe_I" in comp_df.columns
    assert comp_df.loc[0, "Soll_Entgeltgruppe_H"] == "E10"
    assert comp_df.loc[0, "Soll_Entgeltgruppe_I"] == "E11"


def test_build_compensation_band_fit_summary_matches_hand_calculation():
    module = _load_compact_page_module()
    comp_df = _sample_comp_df()

    summary = module._build_compensation_band_fit_summary(comp_df, ist_col="IST_MAK", soll_col="SOLL_MAK_View")

    assert list(summary["Soll-EG-Spanne"]) == ["E9A", "E10-E11"]

    row_e9a = summary[summary["Soll-EG-Spanne"] == "E9A"].iloc[0]
    assert row_e9a["SOLL"] == pytest.approx(2.0)
    assert row_e9a["Passend"] == pytest.approx(1.5)
    assert row_e9a["Abweichend"] == pytest.approx(0.0)
    assert row_e9a["Kapazitätslücke"] == pytest.approx(0.5)
    assert row_e9a["Passquote"] == pytest.approx(0.75)

    row_band = summary[summary["Soll-EG-Spanne"] == "E10-E11"].iloc[0]
    assert row_band["SOLL"] == pytest.approx(4.0)
    assert row_band["Passend"] == pytest.approx(1.0)
    assert row_band["Abweichend"] == pytest.approx(0.8)
    assert row_band["Vakanz"] == pytest.approx(1.0)
    assert row_band["Nicht gefunden"] == pytest.approx(0.9)
    assert row_band["Kapazitätslücke"] == pytest.approx(0.3)
    assert row_band["Passquote"] == pytest.approx(0.25)

    # Kapazitätslücke schliesst die Rekonziliationslücke: SOLL geht jetzt fuer
    # jedes Band exakt auf (anders als bei Koepfe war das vorher nicht der Fall).
    reconciled = summary["Passend"] + summary["Abweichend"] + summary["Vakanz"] + summary["Nicht gefunden"] + summary["Kapazitätslücke"]
    assert (summary["SOLL"] - reconciled).abs().max() == pytest.approx(0.0)

    # SOLL over the whole table must equal 2 planstellen (band E9A) + 4 (band E10-E11).
    assert summary["SOLL"].sum() == pytest.approx(6.0)


def test_build_compensation_band_fit_summary_drops_rows_without_usable_soll_eg():
    module = _load_compact_page_module()
    comp_df = pd.DataFrame([
        {"Soll_Entgeltgruppe_H": "Nicht zugeordnet", "Soll_Entgeltgruppe_I": "Nicht zugeordnet",
         "Ist_Entgeltgruppe": "E9A", "Is_Vacant": False, "IST_MAK": 1.0, "SOLL_MAK_View": 0.0},
    ])

    summary = module._build_compensation_band_fit_summary(comp_df, ist_col="IST_MAK", soll_col="SOLL_MAK_View")
    assert summary.empty


def test_build_compensation_fit_figure_pairs_soll_bar_with_two_ist_segments():
    module = _load_compact_page_module()

    fit_summary_df = pd.DataFrame([
        {"Soll-EG-Spanne": "E9A", "SOLL": 2.0, "Passend": 1.5, "Abweichend": 0.0, "Vakanz": 0.0, "Nicht gefunden": 0.0, "Kapazitätslücke": 0.5, "Passquote": 0.75},
        {"Soll-EG-Spanne": "E10-E11", "SOLL": 4.0, "Passend": 1.0, "Abweichend": 0.8, "Vakanz": 1.0, "Nicht gefunden": 0.9, "Kapazitätslücke": 0.3, "Passquote": 0.25},
    ])

    fig = module._build_compensation_fit_figure(fit_summary_df, value_label="MAK", print_mode=False)

    # Ascending order -> highest band ("E10-E11") plotted last -> appears on top.
    assert list(fig.layout.yaxis.categoryarray) == ["E9A", "E10-E11"]

    traces_by_name = {trace.name: trace for trace in fig.data}
    assert "SOLL (MAK)" in traces_by_name
    soll_trace = traces_by_name["SOLL (MAK)"]
    assert soll_trace.offsetgroup == "soll"
    assert list(soll_trace.x) == [2.0, 4.0]

    ist_traces = [t for t in fig.data if t.offsetgroup == "ist"]
    # Passend/Abweichend/Vakanz/Nicht gefunden/Kapazitätslücke are all stacked,
    # so the IST stack's total length matches the SOLL reference bar exactly.
    assert len(ist_traces) == 5

    passend_trace = next(t for t in ist_traces if "Passend" in t.name)
    abweichend_trace = next(t for t in ist_traces if "Abweichend" in t.name)
    kapazitaetsluecke_trace = next(t for t in ist_traces if "Kapazitätslücke" in t.name)
    assert list(passend_trace.base) == [0.0, 0.0]
    assert list(abweichend_trace.base) == list(passend_trace.x)

    # Kapazitätslücke reconciles SOLL and the IST stack exactly for both rows
    # (unlike before, where the capacity gap was only an implicit bar-length diff).
    stack_end = [float(b) + float(x) for b, x in zip(kapazitaetsluecke_trace.base, kapazitaetsluecke_trace.x)]
    assert stack_end == pytest.approx(list(soll_trace.x))


def test_format_compensation_fit_summary_for_display_appends_total_row():
    module = _load_compact_page_module()

    fit_summary_df = pd.DataFrame([
        {"Soll-EG-Spanne": "E9A", "SOLL": 2.0, "Passend": 1.5, "Abweichend": 0.0, "Vakanz": 0.0, "Nicht gefunden": 0.0, "Kapazitätslücke": 0.5, "Passquote": 0.75},
        {"Soll-EG-Spanne": "E10-E11", "SOLL": 4.0, "Passend": 1.0, "Abweichend": 0.8, "Vakanz": 1.0, "Nicht gefunden": 0.9, "Kapazitätslücke": 0.3, "Passquote": 0.25},
    ])

    display = module._format_compensation_fit_summary_for_display(fit_summary_df, "MAK")

    assert display.iloc[-1]["Soll-EG-Spanne"] == "Gesamt"
    # Formatted strings use German decimal comma via _format_compensation_value.
    assert "," in display.iloc[0]["SOLL"]
    # Kapazitätslücke is signed (+/-) since it can also indicate overcoverage.
    assert display.iloc[0]["Kapazitätslücke"] == "+0,5"
    assert display.iloc[-1]["Kapazitätslücke"] == "+0,8"


def test_render_single_comparison_clean_uses_band_fit_section_for_verguetung(monkeypatch):
    module = _load_compact_page_module()

    calls = {"band_fit": None, "old_section": 0}

    def fake_band_fit(df, *, value_type, key_prefix, print_mode):
        calls["band_fit"] = {"value_type": value_type, "key_prefix": key_prefix, "print_mode": print_mode}

    def fake_old_section(*args, **kwargs):
        calls["old_section"] += 1

    monkeypatch.setattr(module, "render_compensation_band_fit_section", fake_band_fit)
    monkeypatch.setattr(module, "render_compensation_planlevel_section", fake_old_section)
    monkeypatch.setattr(module.st, "subheader", lambda *a, **k: None)
    monkeypatch.setattr(module.st, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(module.st, "expander", lambda *a, **k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(module.st, "columns", lambda *a, **k: [__import__("contextlib").nullcontext(), __import__("contextlib").nullcontext()])
    monkeypatch.setattr(module, "create_stacked_tariff_comparison_chart", lambda *a, **k: module.go.Figure())
    monkeypatch.setattr(module.st, "plotly_chart", lambda *a, **k: None)
    monkeypatch.setattr(module, "create_stacked_tariff_breakdown_table", lambda *a, **k: pd.DataFrame({"Entgeltgruppe": []}))
    monkeypatch.setattr(module, "dataframe_compat", lambda *a, **k: None)
    monkeypatch.setattr(module, "export_to_excel", lambda *a, **k: b"")
    monkeypatch.setattr(module, "download_button_compat", lambda *a, **k: None)

    df = pd.DataFrame({"TrfGr": ["E9A"], "St": [4]})
    module._render_single_comparison_clean(
        df, "Vergütungsklasse", "Vergütungsklasse",
        ist_col="FTE_assigned", soll_col="Soll_FTE",
        value_type="mak", key_prefix="ist_vs_soll_mak", print_mode=False,
    )

    assert calls["band_fit"] == {"value_type": "mak", "key_prefix": "ist_vs_soll_mak", "print_mode": False}
    assert calls["old_section"] == 0
