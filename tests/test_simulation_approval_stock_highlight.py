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
    assert table.set_index("Organisation").loc["Markt", "Finale MAK"] == 0.5
    styled = page._mark_zero_mak_rows(table, {"Treasury", "Alt"})._compute().ctx
    red_rows = {table.iloc[row]["Organisation"] for (row, _), styles in styled.items()
                if any(key == "background-color" and value == "#f8d7da" for key, value in styles)}
    assert red_rows == {"Treasury", "Alt"}
