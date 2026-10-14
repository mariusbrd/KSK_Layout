import pandas as pd

from components import org_evaluation_chart as chart_ui
from components.org_evaluation_chart import comparison_color_map, EXPECTED_COLOR


def test_expected_series_are_gray_and_observed_series_are_colored():
    colors = comparison_color_map(pd.Series([
        "OE A · beobachtet", "OE A · erwartet",
        "OE B · beobachtet", "OE B · erwartet",
    ]))
    assert colors["OE A · erwartet"] == EXPECTED_COLOR
    assert colors["OE B · erwartet"] == EXPECTED_COLOR
    assert colors["OE A · beobachtet"] != EXPECTED_COLOR
    assert colors["OE B · beobachtet"] != EXPECTED_COLOR
    assert colors["OE A · beobachtet"] != colors["OE B · beobachtet"]


def test_chart_keeps_series_labels_and_explicit_colors(monkeypatch):
    captured = []
    monkeypatch.setattr(chart_ui.st, "altair_chart", lambda chart, **kwargs: captured.append(chart.to_dict()))
    chart_ui.render_comparison_bar_chart(pd.DataFrame({
        "Jahr": [2030, 2030], "Serie": ["OE A · beobachtet", "OE A · erwartet"],
        "Anzahl": [2.0, 1.5]}))
    assert captured[0]["encoding"]["color"]["scale"]["range"] == ["#1266C3", EXPECTED_COLOR]
