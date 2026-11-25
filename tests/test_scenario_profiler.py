"""The profiler reads every stored path and highlights real run indices."""
from __future__ import annotations

from test_monte_carlo_representative_runs import _ensemble
from utils.scenario_profiler import highlighted_indices, stock_trajectories, trajectory_frequencies
from utils.scenario_profiler_chart import CHART_TYPES, build_profiler_figure


def test_profiler_uses_all_run_values_and_never_stitches_highlighted_paths():
    ensemble = _ensemble()
    ensemble["statistics"]["departure_stock_samples"] = ensemble["statistics"]["annual_stock_samples"] + [
        ("2026-12-31", "OE-Cluster", "C", "mak", [80, 60, 70]),
        ("2027-12-31", "OE-Cluster", "C", "mak", [60, 80, 70]),
    ]
    global_mak = stock_trajectories(ensemble, "mak")
    assert len(global_mak) == 2 * 3
    assert global_mak.loc[global_mak["Laufindex"].eq(1), "Wert"].tolist() == [60, 80]
    cluster = stock_trajectories(ensemble, "mak", "OE-Cluster", "C")
    assert cluster["Wert"].tolist() == global_mak["Wert"].tolist()
    indices = highlighted_indices(ensemble)
    assert indices["Geringste Fluktuation"] == 2
    assert indices["Höchste Fluktuation"] == 0
    assert indices["Median-naher Bestandsverlauf"] == 2
    assert indices["Typischer Verlauf (bestehende OE-Auswahl)"] == 2


def test_profiler_supports_one_hundred_complete_lines():
    ensemble = _ensemble()
    ensemble["count"] = 100
    ensemble["seed_pairs"] = tuple((42 + i, 42 + i) for i in range(100))
    ensemble["statistics"]["requested_count"] = 100
    ensemble["statistics"]["annual_event_samples"] = [
        (2026, "Organisationseinheit", "A", "QUIT", "Abgänge", list(range(100)))
    ]
    ensemble["statistics"]["annual_stock_samples"] = [
        (date, "Organisationseinheit", "A", metric, [float(100 - i) for i in range(100)])
        for date in ("2026-12-31", "2027-12-31") for metric in ("heads", "mak")
    ]
    ensemble["statistics"]["departure_stock_samples"] = ensemble["statistics"]["annual_stock_samples"]
    table = stock_trajectories(ensemble, "mak")
    assert table["Laufindex"].nunique() == 100
    assert len(table) == 200
    indices = highlighted_indices(ensemble)
    assert indices["Geringste Fluktuation"] == 0
    assert indices["Höchste Fluktuation"] == 99
    assert indices["Median-naher Bestandsverlauf"] == 49


def test_identical_runs_remain_present_even_when_only_one_line_is_visible():
    ensemble = _ensemble()
    ensemble["statistics"]["annual_stock_samples"] = [
        (date, "Organisationseinheit", "A", "mak", [5.0, 5.0, 5.0])
        for date in ("2026-12-31", "2027-12-31")
    ]
    ensemble["statistics"]["departure_stock_samples"] = ensemble["statistics"]["annual_stock_samples"]
    table = stock_trajectories(ensemble, "mak", "Organisationseinheit", "A")
    assert table["Laufindex"].nunique() == 3
    frequencies = trajectory_frequencies(table)
    assert len(frequencies) == 1
    assert frequencies.iloc[0]["Läufe"] == 3
    assert frequencies.iloc[0]["Laufindizes"] == [0, 1, 2]


def test_profiler_uses_oe_yearly_bridge_instead_of_static_position_stock():
    ensemble = _ensemble()
    ensemble["statistics"]["departure_stock_samples"] = [
        ("2026-12-31", "Organisationseinheit", "A", "mak", [8.0, 8.0, 8.0]),
        ("2027-12-31", "Organisationseinheit", "A", "mak", [6.0, 7.0, 5.0]),
    ]
    table = stock_trajectories(ensemble, "mak", "Organisationseinheit", "A")
    assert table.loc[table["Laufindex"].eq(0), "Wert"].tolist() == [8.0, 6.0]


def test_disappeared_oe_is_zero_in_later_year_slice():
    ensemble = _ensemble()
    ensemble["statistics"]["departure_stock_samples"] = [
        ("2026-12-31", "Organisationseinheit", "A", "mak", [2.0, 1.0, 3.0]),
        ("2027-12-31", "Organisationseinheit", "B", "mak", [4.0, 4.0, 4.0]),
    ]
    table = stock_trajectories(ensemble, "mak", "Organisationseinheit", "A")
    assert table.loc[table["Laufindex"].eq(0), "Wert"].tolist() == [2.0, 0.0]


def test_all_chart_types_preserve_each_annual_value():
    ensemble = _ensemble()
    ensemble["statistics"]["departure_stock_samples"] = ensemble["statistics"]["annual_stock_samples"]
    table = stock_trajectories(ensemble, "mak", "Organisationseinheit", "A")
    highlights = highlighted_indices(ensemble)
    expected = sorted(table["Wert"].tolist())
    for chart_type in CHART_TYPES:
        figure = build_profiler_figure(table, highlights, chart_type, "MAK")
        plotted = sorted(float(value) for trace in figure.data for value in trace.y)
        if chart_type == "Boxplots je Jahr":
            # Highlight markers repeat selected observations over the box points.
            plotted = sorted(float(value) for trace in figure.data if trace.type == "box" for value in trace.y)
        assert plotted == expected
        assert all("Lauf" in trace.name for trace in figure.data if trace.type == "scatter" and trace.showlegend)
