"""Annual OE filters must calculate bands from per-seed values."""
from types import SimpleNamespace

import pandas as pd
import pytest

from utils.monte_carlo_ensemble import aggregate_packets
from utils.monte_carlo_metric_packet import LEVELS
from utils.monte_carlo_org_view import (
    approved_org_ensemble, event_band_table, stock_band_table, target_rank_band_stats,
)
from components.org_simulation_yearly import render_stacked_event_chart


def _ensemble():
    def packet(first, second, stock):
        return {
            "version": 1, "target": {"heads": 1, "mak": 1.0, "eur": 0.0},
            "stock_status": {level: "Abgestimmt" for level in LEVELS},
            "annual_values": [
                (2027, "Organisationseinheit", "A", "Abgänge", "QUIT", "Abgänge", first),
                (2027, "Organisationseinheit", "A", "Abgänge", "RETIREMENT", "Abgänge", second),
            ],
            "annual_stock_values": [
                ("2027-12-31", level, "A", metric, stock)
                for level in LEVELS for metric in ("heads", "mak")],
        }
    statistics = aggregate_packets(base_seed=42, count=2, runs=[
        {"seed": 42, "status": "ok", "packet": packet(0, 10, 4)},
        {"seed": 43, "status": "ok", "packet": packet(10, 0, 8)},
    ], band_level=0.8)
    return {"status": "complete", "count": 2, "band_level": 0.8,
            "reference_signature": "sig", "reference_instance": 3,
            "statistics": statistics}


def test_oe_annual_filters_sum_each_seed_before_quantiles():
    ensemble = _ensemble()
    view = {"dimension": "Organisationseinheit", "metric": "Abgänge",
            "event_types": ["QUIT", "RETIREMENT"],
            "chart": pd.DataFrame({"Jahr": [2027], "Organisationseinheit": ["A"], "Abgänge": [10]})}
    both = event_band_table(view, ensemble=ensemble).iloc[0]
    assert (both["mean"], both["lower"], both["upper"]) == (10, 10, 10)
    assert both["Referenzlauf"] == 10
    view["event_types"] = ["QUIT"]
    view["chart"]["Abgänge"] = 0
    quit_only = event_band_table(view, ensemble=ensemble).iloc[0]
    assert (quit_only["mean"], quit_only["lower"], quit_only["upper"]) == pytest.approx((5, 1, 9))


def test_oe_stock_band_matches_year_slice_and_approval():
    ensemble = _ensemble()
    chart = pd.DataFrame({"Stichtag": [pd.Timestamp("2027-12-31")],
                          "Organisation": ["A"], "Bestand-MAK": [4.0]})
    band = stock_band_table(chart, ensemble=ensemble, level="Organisationseinheit", metric="MAK").iloc[0]
    assert (band["Referenzlauf"], band["mean"], band["lower"], band["upper"]) == pytest.approx((4, 6, 4.4, 7.6))
    opening = pd.DataFrame({"Stichtag": [pd.Timestamp("2026-09-01")],
                            "Organisation": ["A"], "Bestand-MAK": [3.25]})
    opening_band = stock_band_table(
        opening, ensemble=ensemble, level="Organisationseinheit", metric="MAK",
        base_date="2026-09-01").iloc[0]
    assert (opening_band["mean"], opening_band["lower"], opening_band["upper"]) == (3.25, 3.25, 3.25)
    view = SimpleNamespace(signature="sig", instance=3, version="sig::3::original", approved=True)
    assert approved_org_ensemble({"monte_carlo_ensemble_result": ensemble}, view) is ensemble
    assert approved_org_ensemble({"monte_carlo_ensemble_result": ensemble},
                                 SimpleNamespace(signature="sig", instance=3,
                                                 version="sig::3::adjusted::x", approved=True)) is None


def test_stacked_event_chart_keeps_reference_bars_under_gray_band(monkeypatch):
    captured = []
    monkeypatch.setattr("components.org_simulation_yearly.st.altair_chart",
                        lambda chart, **_kwargs: captured.append(chart.to_dict()))
    events = pd.DataFrame({"Jahr": [2027, 2027], "Organisationseinheit": ["A", "A"],
                           "Ereignisart": ["QUIT", "RETIREMENT"], "Abgänge": [2, 3]})
    bands = pd.DataFrame({"Jahr": [2027], "Organisationseinheit": ["A"],
                          "Referenzlauf": [5], "mean": [6], "lower": [4], "upper": [8]})
    render_stacked_event_chart(events, dimension="Organisationseinheit", metric="Abgänge", bands=bands)
    assert len(captured) == 1
    assert captured[0]["layer"][0]["mark"]["type"] == "bar"
    assert captured[0]["layer"][1]["mark"]["type"] == "rule"
    assert captured[0]["layer"][1]["mark"]["color"] == "#6b7280"


def test_target_ranking_band_requires_exact_reference_parity():
    ensemble = _ensemble()
    chart = pd.DataFrame({"Organisationseinheit": ["A"], "Wert": [4.0]})
    bands = target_rank_band_stats(chart, ensemble=ensemble,
                                   target_date="2027-12-31", metric="MAK")
    assert bands["values"][0][3] == pytest.approx({"mean": 6, "lower": 4.4, "upper": 7.6})
    chart["Wert"] = 4.1
    assert target_rank_band_stats(chart, ensemble=ensemble,
                                  target_date="2027-12-31", metric="MAK") is None


def test_unmapped_cluster_stock_uses_bridge_display_label():
    ensemble = _ensemble()
    ensemble["statistics"]["annual_stock_samples"].append(
        ("2027-12-31", "OE-Cluster", "Unclustered", "mak", [4.0, 8.0]))
    chart = pd.DataFrame({"Stichtag": [pd.Timestamp("2027-12-31")],
                          "Organisation": ["Nicht zugeordnet"], "Bestand-MAK": [4.0]})
    bands = stock_band_table(chart, ensemble=ensemble, level="OE-Cluster", metric="MAK")
    assert bands.iloc[0]["mean"] == 6.0
