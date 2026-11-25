"""Profiler diagnostics describe the stored paths without altering them."""
from __future__ import annotations

import math

import pandas as pd
from pathlib import Path
from streamlit.testing.v1 import AppTest

from utils.scenario_profiler_stats import (
    annual_distribution, change_distribution, endpoint_convergence,
    highlighted_yearly_ranks, run_changes,
)


def _paths():
    return pd.DataFrame([
        (date, index, value)
        for date, values in (("2026-12-31", [8., 8., 8.]),
                             ("2027-12-31", [6., 7., 5.]))
        for index, value in enumerate(values)
    ], columns=["Datum", "Laufindex", "Wert"])


def test_annual_quantiles_and_marked_ranks_use_all_real_runs():
    table = _paths()
    stats = annual_distribution(table)
    assert stats.iloc[1]["Mittelwert"] == 6
    assert stats.iloc[1]["Median"] == 6
    assert math.isclose(stats.iloc[1]["P10"], 5.2)
    ranks = highlighted_yearly_ranks(table, {"Mitte": 0, "Hoch": 1})
    assert ranks.loc[ranks["Stichtag"].eq("2026-12-31"), "Perzentilrang (%)"].tolist() == [50, 50]
    assert ranks.loc[ranks["Stichtag"].eq("2027-12-31"), "Perzentilrang (%)"].tolist() == [50, 83.33333333333333]
    assert ranks.loc[ranks["Stichtag"].eq("2027-12-31"), "Abstand zum Median"].tolist() == [0, 1]


def test_changes_and_convergence_keep_seed_order_and_absolute_values():
    table = _paths()
    changes = run_changes(table)
    assert changes["Veränderung"].tolist() == [-2, -1, -3]
    assert changes["Veränderung (%)"].tolist() == [-25, -12.5, -37.5]
    assert change_distribution(changes).iloc[0]["Mittelwert"] == -2
    convergence = endpoint_convergence(table)
    assert convergence["Läufe"].tolist() == [3]
    assert convergence.iloc[0]["Mittelwert"] == 6
    assert convergence.iloc[0]["P10"] == 5.2


def test_zero_start_percentage_is_undefined_and_stability_uses_prefixes():
    table = pd.DataFrame([
        (date, index, value)
        for date, values in (("2026-12-31", [0.] * 25),
                             ("2027-12-31", list(map(float, range(25)))))
        for index, value in enumerate(values)
    ], columns=["Datum", "Laufindex", "Wert"])
    changes = run_changes(table)
    assert changes["Veränderung"].iloc[-1] == 24
    assert changes["Veränderung (%)"].isna().all()
    assert change_distribution(changes)["Kennzahl"].tolist() == ["Veränderung"]
    convergence = endpoint_convergence(table)
    assert convergence["Läufe"].tolist() == [10, 20, 25]
    assert convergence["Mittelwert"].tolist() == [4.5, 9.5, 12]


def test_profiler_page_renders_all_statistics_from_existing_ensemble():
    page = next((Path(__file__).resolve().parents[1] / "pages").glob("13_*_Szenario_Profiler.py"))
    source = (
        "import importlib.util\nimport streamlit as st\n"
        "from test_monte_carlo_representative_runs import _ensemble\n"
        f"spec = importlib.util.spec_from_file_location('scenario_profiler_page_test', {str(page)!r})\n"
        "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
        "ensemble = _ensemble()\n"
        "ensemble['statistics']['departure_stock_samples'] = ensemble['statistics']['annual_stock_samples']\n"
        "module.current_view = lambda *args, **kwargs: object()\n"
        "module.current_ensemble = lambda *args, **kwargs: ensemble\n"
        "st.session_state['monte_carlo_ensemble_request'] = object()\n"
        "module.main()\n"
    )
    app = AppTest.from_string(source.encode("ascii", "backslashreplace").decode(),
                              default_timeout=30).run()
    assert not app.exception
    assert any("Verteilung je Jahresscheibe" in item.value for item in app.subheader)
    assert any("Position der markierten Läufe" in item.value for item in app.subheader)
    assert any("Stabilität bei wachsender Laufzahl" in item.value for item in app.subheader)
    assert len(app.dataframe) >= 4
