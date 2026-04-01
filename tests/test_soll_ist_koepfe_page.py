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
    return pivot, ["E9A"], ["E9A"], "Unbesetzt", "Nicht gefunden", work_df, 1, pd.Series({"E10": 1}), summary


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
    assert captured["downloads"] == 1
    assert captured["dataframes"] >= 1
    assert captured["plots"] >= 1
    joined_captions = "\n".join(captured["captions"])
    assert "Roh-nahe Kontrolllogik" in joined_captions
    assert "Kontrollsummen 0.01-Faelle" in joined_captions


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
