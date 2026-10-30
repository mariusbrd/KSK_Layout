"""Zero-MAK warning follows the actual final OE stock."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

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


def test_oe_stock_aggregation_matches_existing_kpis_for_split_positions():
    page = _page()
    from dataloader.kpi_engine import compute_headcount
    from utils.org_simulation_reconciliation import raw_stock_mak

    baseline = pd.DataFrame({
        "Organisationseinheit": ["Treasury", "Treasury", "Treasury", "Markt", "Markt", "Leer"],
        "PersNr": ["1", "1", "2", "3", None, "4"],
        "Is_Vacant": [False, False, True, False, False, False],
        "MAK_Reporting": [0.4, 0.6, 0.0, 0.8, 1.0, 0.0],
    })
    final = baseline.loc[baseline["Organisationseinheit"].ne("Leer")].copy()
    table = page._oe_final_stock(baseline, final).set_index("Organisation")
    for name in ("Treasury", "Markt", "Leer"):
        before = baseline.loc[baseline["Organisationseinheit"].eq(name)]
        after = final.loc[final["Organisationseinheit"].eq(name)]
        assert table.at[name, "Ausgangs-Köpfe"] == compute_headcount(before)
        assert table.at[name, "Ausgangs-MAK"] == raw_stock_mak(before)
        assert table.at[name, "Finale Köpfe"] == compute_headcount(after)
        assert table.at[name, "Finale MAK"] == raw_stock_mak(after)


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


def test_selection_basket_keeps_other_change_type_and_checks_combined_draft():
    page = _page()
    candidates = pd.DataFrame({"PersNr": ["1", "2", "3"],
                               "Organisationseinheit": ["Treasury", "Treasury", "Markt"]})
    basket = {"run": ("run", 1), "retained": (), "shifts": {}, "reason": ""}
    basket = page._with_scope_selection(basket, candidates, "Treasury", False, ["1"], {}, "")
    basket = page._with_scope_selection(basket, candidates, "Treasury", True, ["2"], {"2": 2027}, "Späterer Austritt")
    basket = page._with_scope_selection(basket, candidates, "Markt", False, ["3"], {}, "")
    basket = page._with_scope_selection(basket, candidates, "Markt", False, [], {}, "")
    assert basket["retained"] == ("1",)
    assert basket["shifts"] == {"2": 2027}
    draft = SimpleNamespace(metadata={"adjustment": {
        "kind": "combined_departures", "retained": ("1",),
        "shifts": [{"person": "2", "new_date": "2027-12-31T00:00:00"}],
        "reason": "Späterer Austritt",
    }})
    assert page._basket_matches_draft(basket, draft)
    assert not page._basket_matches_draft({**basket, "shifts": {"2": 2028}}, draft)
