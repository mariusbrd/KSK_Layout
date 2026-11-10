"""IST chart bands retain the reference bars and use per-run categories."""
import plotly.graph_objects as go
import pytest

from utils.monte_carlo_chart_values import (
    aggregate_ist_chart_values, overlay_ist_band,
)


def _packet(values):
    return {"compact_ist_version": 2, "compact_ist_values": values}


def test_chart_quantiles_are_per_category_and_missing_additive_groups_are_zero():
    result = aggregate_ist_chart_values([
        _packet([("heads", "Geschlecht", "w", 4), ("heads", "Geschlecht", "m", 6)]),
        _packet([("heads", "Geschlecht", "w", 8)]),
    ], band_level=0.8)
    values = {(metric, dimension, group): stats for metric, dimension, group, stats in result["values"]}
    assert values["heads", "Geschlecht", "w"] == {
        "mean": 6, "lower": 4.4, "upper": 7.6, "samples": [4, 8]}
    assert values["heads", "Geschlecht", "m"]["samples"] == [6, 0]


def test_overlay_adds_bounds_without_changing_reference_bar():
    original = go.Figure(go.Bar(y=["w", "m"], x=[4, 6], orientation="h"))
    chart_stats = aggregate_ist_chart_values([
        _packet([("heads", "Geschlecht", "w", 4), ("heads", "Geschlecht", "m", 6)]),
        _packet([("heads", "Geschlecht", "w", 8), ("heads", "Geschlecht", "m", 2)]),
    ], band_level=0.8)
    overlaid = overlay_ist_band(original, chart_stats, metric="heads", dimension="Geschlecht")
    assert len(original.data) == 1
    assert len(overlaid.data) == 4
    assert list(overlaid.data[0].x) == [4, 6]
    assert overlaid.data[1].line.color == "rgba(107, 114, 128, 0.65)"
    assert overlaid.data[2].marker.color == "#6b7280"
    assert overlaid.data[3].marker.color == "#6b7280"
    assert list(overlaid.data[2].x) == pytest.approx([4.4, 2.4])
    assert list(overlaid.data[3].x) == pytest.approx([7.6, 5.6])
    assert overlay_ist_band(original, chart_stats, metric="mak", dimension="Geschlecht") is original


def test_tariff_values_are_outside_the_chart_band_contract():
    with pytest.raises(ValueError, match="Unknown CompactPlus IST chart key"):
        aggregate_ist_chart_values([
            _packet([("mak", "Vergütungsklasse", "E5", 4)]),
            _packet([("mak", "Vergütungsklasse", "E5", 8)]),
        ], band_level=0.8)


def test_missing_or_duplicate_chart_packet_is_rejected():
    with pytest.raises(ValueError, match="Missing certified"):
        aggregate_ist_chart_values([_packet([]), {}], band_level=0.8)
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_ist_chart_values([
            _packet([("mak", "Geschlecht", "w", 1), ("mak", "Geschlecht", "w", 2)]),
            _packet([]),
        ], band_level=0.8)
