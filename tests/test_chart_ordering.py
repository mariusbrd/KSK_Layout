from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def _load_page_module(filename_suffix: str, module_name: str):
    page_path = next((ROOT / "pages").glob(f"*_{filename_suffix}.py"))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ranking_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Kategorie": ["Gross", "Mittel", "Klein"],
            "IST": [12.0, 8.0, 3.0],
            "SOLL": [11.0, 7.0, 2.0],
        }
    )


def test_compact_horizontal_bar_chart_keeps_largest_value_visually_on_top():
    compact = _load_page_module("Kompakt", "compact_chart_order")
    fig = compact.create_horizontal_bar_chart(_ranking_df(), "Kategorie", "IST")

    assert list(fig.data[0].y) == ["Klein", "Mittel", "Gross"]
    assert list(fig.layout.yaxis.categoryarray) == ["Klein", "Mittel", "Gross"]


def test_compact_comparison_chart_keeps_largest_value_visually_on_top():
    compact = _load_page_module("Kompakt", "compact_comparison_chart_order")
    fig = compact.create_comparison_chart(_ranking_df(), "Kategorie")

    assert list(fig.data[0].y) == ["Klein", "Mittel", "Gross"]
    assert list(fig.layout.yaxis.categoryarray) == ["Klein", "Mittel", "Gross"]


def test_compact_compensation_planlevel_chart_keeps_top_n_visually_on_top(monkeypatch):
    compact = _load_page_module("Kompakt", "compact_compensation_chart_order")

    source = pd.DataFrame(
        {
            "_label": ["Gross", "Mittel", "Klein"],
            "_is_unassigned": [False, False, False],
            "IST_MAK": [12.0, 8.0, 3.0],
            "SOLL_MAK": [10.0, 8.0, 4.0],
            "DELTA_MAK": [2.0, 0.0, -1.0],
            "_delta_pct": [0.2, 0.0, -0.25],
        }
    )

    monkeypatch.setattr(
        compact,
        "_build_compensation_chart_source",
        lambda *_args, **_kwargs: (source.copy(), "IST_MAK", "SOLL_MAK", "DELTA_MAK", "MAK"),
    )

    fig = compact.create_compensation_planlevel_chart(
        pd.DataFrame({"x": [1]}),
        metric="MAK",
        view="IST",
        aggregation="Planebene",
        top_n="2",
    )

    assert list(fig.data[0].y) == ["Mittel", "Gross"]
    assert list(fig.layout.yaxis.categoryarray) == ["Mittel", "Gross"]
