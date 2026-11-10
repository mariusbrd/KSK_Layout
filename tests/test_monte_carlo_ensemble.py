"""Multi-run statistics keep exact per-seed values and reject incomplete sets."""
from __future__ import annotations

import pytest
import pandas as pd

from utils.monte_carlo_ensemble import aggregate_packets, seed_sequence
from utils.monte_carlo_metric_packet import LEVELS, make_metric_packet
from utils.org_simulation_events import METRICS


KEY = (2027, "Organisationseinheit", "Beratung", "Abgänge", "Kündigung",
       "MAK-Veränderung")


def packet(heads: int, mak: float, annual: float | None) -> dict:
    return {"version": 1, "target": {"heads": heads, "mak": mak, "eur": 0.0},
            "annual_values": [] if annual is None else [(*KEY, annual)],
            "stock_status": {level: "Nicht verfügbar" for level in LEVELS}}


def test_stable_seed_prefix_and_single_run_value() -> None:
    assert seed_sequence(42, 1) == seed_sequence(42, 100)[:1]
    one = aggregate_packets(base_seed=42, count=1, runs=[
        {"seed": 42, "status": "ok", "packet": packet(7, 5.7023, -0.5)}])
    assert one["status"] == "complete"
    assert one["target"]["mak"] == {"mean": 5.7023, "lower": 5.7023,
                                     "upper": 5.7023}
    assert one["annual_values"] == [(*KEY, {"mean": -0.5, "lower": -0.5,
                                            "upper": -0.5})]


def test_missing_event_is_zero_and_quantile_band_is_empirical() -> None:
    runs = [{"seed": seed, "status": "ok", "packet": packet(10 + seed, float(seed), annual)}
            for seed, annual in enumerate((0.0, None, 2.0, 4.0))]
    result = aggregate_packets(base_seed=0, count=4, runs=runs, band_level=0.5)
    assert result["status"] == "complete"
    assert result["band_kind"] == "empirical_quantile"
    assert result["annual_values"] == [(*KEY, {"mean": 1.5, "lower": 0.0,
                                               "upper": 2.5})]
    assert result["target"]["heads"] == {"mean": 11.5, "lower": 10.75,
                                            "upper": 12.25}
    assert result["stock_status_by_seed"][0]["Organisationseinheit"] == "Nicht verfügbar"
    assert "eur" not in result["target"]


def test_failed_or_invalid_run_blocks_all_statistics() -> None:
    good = {"seed": 8, "status": "ok", "packet": packet(10, 8.0, 2.0)}
    failed = {"seed": 9, "status": "failed", "error": "forecast failed"}
    result = aggregate_packets(base_seed=8, count=2, runs=[good, failed])
    assert result["status"] == "incomplete"
    assert result["successful_count"] == 1
    assert result["target"] is None and result["annual_values"] is None
    assert result["failures"] == [{"seed": 9, "error": "forecast failed"}]
    invalid = {"seed": 9, "status": "ok", "packet": packet(11, 9.0, float("nan"))}
    assert aggregate_packets(base_seed=8, count=2, runs=[good, invalid])["status"] == "incomplete"


def test_seed_and_band_validation() -> None:
    with pytest.raises(ValueError, match="expected seed"):
        aggregate_packets(base_seed=8, count=2, runs=[
            {"seed": 9, "status": "ok", "packet": packet(10, 8.0, 2.0)},
            {"seed": 8, "status": "ok", "packet": packet(11, 9.0, 3.0)}])
    with pytest.raises(ValueError, match="band_level"):
        aggregate_packets(base_seed=8, count=1, runs=[
            {"seed": 8, "status": "ok", "packet": packet(10, 8.0, 2.0)}],
            band_level=1.0)


def test_current_full_run_packets_keep_single_values_and_two_run_mean() -> None:
    from test_monte_carlo_metric_packet import _packet

    first, second = (_packet("combined", seed) for seed in (7, 8))
    one = aggregate_packets(base_seed=7, count=1, runs=[
        {"seed": 7, "status": "ok", "packet": first}])
    assert one["target"]["heads"]["mean"] == first["target"]["heads"]
    assert one["target"]["mak"]["mean"] == first["target"]["mak"]
    assert [(record[:6], record[6]["mean"]) for record in one["annual_values"]] == sorted(
        [(record[:6], float(record[6])) for record in first["annual_values"]])

    two = aggregate_packets(base_seed=7, count=2, runs=[
        {"seed": 7, "status": "ok", "packet": first},
        {"seed": 8, "status": "ok", "packet": second}])
    assert two["target"]["heads"]["mean"] == (
        first["target"]["heads"] + second["target"]["heads"]) / 2
    first_values = {record[:6]: float(record[6]) for record in first["annual_values"]}
    second_values = {record[:6]: float(record[6]) for record in second["annual_values"]}
    for *key, statistics in two["annual_values"]:
        group = tuple(key)
        assert statistics["mean"] == (
            first_values.get(group, 0.0) + second_values.get(group, 0.0)) / 2


@pytest.mark.parametrize("count", [1, 10, 100])
def test_requested_run_counts_have_complete_stable_packets(count: int) -> None:
    runs = [{"seed": seed, "status": "ok", "packet": packet(seed, float(seed),
                                                                 float(seed % 5))}
            for seed in seed_sequence(1000, count)]
    result = aggregate_packets(base_seed=1000, count=count, runs=runs)
    assert result["status"] == "complete"
    assert result["successful_count"] == count
    assert result["seeds"] == seed_sequence(1000, 100)[:count]
    assert result["target"]["heads"]["mean"] == 1000 + (count - 1) / 2


def test_certified_annual_stock_is_optional_and_never_silently_missing() -> None:
    annual = {level: pd.DataFrame(columns=["Jahr", level, "Kategorie", "Ereignisart", *METRICS])
              for level in LEVELS}
    status = {level: "Abgestimmt" for level in LEVELS}

    def with_stock(mak: float, *, extra_group: bool = False) -> dict:
        stocks = {}
        for level in LEVELS:
            rows = [{"Stichtag": pd.Timestamp("2027-12-31"), level: "A",
                     "Köpfe": 2, "MAK": mak}]
            if extra_group:
                rows.append({"Stichtag": pd.Timestamp("2027-12-31"), level: "B",
                             "Köpfe": 1, "MAK": 0.5})
            stocks[level] = pd.DataFrame(rows)
        return make_metric_packet(
            target_totals={"heads": 2, "mak": mak, "eur": 0.0},
            annual_tables=annual, stock_status=status, annual_stock_tables=stocks)

    first, second = with_stock(1.5, extra_group=True), with_stock(2.0)
    assert first["annual_stock_values"][0] == (
        "2027-12-31", "Organisationseinheit", "A", "heads", 2)
    result = aggregate_packets(base_seed=0, count=2, runs=[
        {"seed": 0, "status": "ok", "packet": first},
        {"seed": 1, "status": "ok", "packet": second}])
    assert result["status"] == "complete"
    stock = {row[:4]: row[4] for row in result["annual_stock_values"]}
    assert stock[("2027-12-31", "Organisationseinheit", "A", "mak")]["mean"] == 1.75
    assert stock[("2027-12-31", "Organisationseinheit", "B", "mak")]["mean"] == 0.25

    mixed = aggregate_packets(base_seed=0, count=2, runs=[
        {"seed": 0, "status": "ok", "packet": first},
        {"seed": 1, "status": "ok", "packet": packet(2, 2.0, None)}])
    assert mixed["status"] == "incomplete"
    assert mixed["annual_stock_values"] is None
    assert mixed["failures"] == [
        {"seed": 1, "error": "Missing certified annual stock values"}]
