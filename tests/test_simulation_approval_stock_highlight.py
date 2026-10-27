"""Zero-MAK warning follows the actual final OE stock."""
import importlib.util
from pathlib import Path

import pandas as pd


PAGE = Path(__file__).resolve().parents[1] / "pages" / "12_✅_Simulation_abnehmen.py"


def _page():
    spec = importlib.util.spec_from_file_location("simulation_approval_page", PAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zero_mak_marks_only_final_zero_oes():
    page = _page()
    baseline = pd.DataFrame({
        "Organisationseinheit": ["Treasury", "Markt", "Alt"],
        "PersNr": ["1", "2", "3"],
        "Is_Vacant": [False, False, False],
        "MAK_Reporting": [1.0, 0.5, 1.0],
    })
    final = pd.DataFrame({
        "Organisationseinheit": ["Treasury", "Markt"],
        "PersNr": ["1", "2"],
        "Is_Vacant": [True, False],
        "MAK_Reporting": [0.0, 0.5],
    })
    table = page._oe_final_stock(baseline, final)
    assert page._zero_mak_orgs(table, 1e-6) == {"Treasury", "Alt"}
    assert page._new_zero_mak_orgs(table, 1e-6) == {"Treasury", "Alt"}
    assert table.set_index("Organisation").loc["Markt", "Finale MAK"] == 0.5
    styled = page._mark_zero_mak_rows(table, {"Treasury", "Alt"})._compute().ctx
    red_rows = {table.iloc[row]["Organisation"] for (row, _), styles in styled.items()
                if any(key == "background-color" and value == "#f8d7da" for key, value in styles)}
    assert red_rows == {"Treasury", "Alt"}


def test_zero_mak_warning_excludes_orgs_already_empty_at_baseline():
    page = _page()
    stock = pd.DataFrame({"Ebene": ["Organisationseinheit"] * 3,
                          "Organisation": ["Treasury", "Leer", "Markt"],
                          "Ausgangs-MAK": [1.0, 0.0, 1.0],
                          "Finale MAK": [0.0, 0.0, 0.5]})
    assert page._new_zero_mak_orgs(stock, 1e-6) == {"Treasury"}


def test_yearly_chart_shows_original_and_corrected_heads_and_mak(monkeypatch):
    page = _page()
    captured = {}
    monkeypatch.setattr(page.st, "plotly_chart", lambda fig, **kwargs: captured.update(fig=fig))
    comparison = pd.DataFrame({
        "Ebene": ["Organisationseinheit"] * 2,
        "Organisation": ["Treasury"] * 2,
        "Jahr": [2030, 2031],
        "Köpfe Original": [2, 0], "Köpfe Justierung": [2, 1],
        "MAK Original": [1.5, 0.0], "MAK Justierung": [1.5, 0.8],
    })
    page._render_annual_change_chart(comparison, "Treasury")
    traces = captured["fig"].data
    assert len(traces) == 4
    assert list(traces[1].y) == [2, 1]
    assert list(traces[3].y) == [1.5, 0.8]
