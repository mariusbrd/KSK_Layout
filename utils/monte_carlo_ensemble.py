"""Pure aggregation of certified per-run Monte Carlo metric packets.

The product's single-run output remains the reference. This module only
summarizes compact additional-run values; it never builds a synthetic person
or position snapshot.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from utils.monte_carlo_metric_packet import LEVELS, PACKET_VERSION
from utils.org_simulation_events import METRICS


def seed_sequence(base_seed: int, count: int) -> tuple[int, ...]:
    """Assign a stable prefix: increasing N does not change earlier seeds."""
    if isinstance(base_seed, bool) or not isinstance(base_seed, int) or base_seed < 0:
        raise ValueError("base_seed must be a nonnegative integer")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    return tuple(range(base_seed, base_seed + count))


def _quantile(values: Sequence[float], probability: float) -> float:
    """Empirical quantile with linear interpolation, matching numpy's default."""
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _statistics(values: Sequence[int | float], band_level: float) -> dict[str, float]:
    numeric = [float(value) for value in values]
    lower_probability = (1.0 - band_level) / 2.0
    return {"mean": math.fsum(numeric) / len(numeric),
            "lower": _quantile(numeric, lower_probability),
            "upper": _quantile(numeric, 1.0 - lower_probability)}


def _validated_packet(packet: Mapping) -> tuple[dict[str, float], dict[tuple, float], dict[tuple, float] | None]:
    if not isinstance(packet, Mapping):
        raise ValueError("Missing metric packet")
    if packet.get("version") != PACKET_VERSION:
        raise ValueError("Unsupported metric packet version")
    target = packet.get("target")
    if not isinstance(target, Mapping) or set(target) != {"heads", "mak", "eur"}:
        raise ValueError("Incomplete target metrics")
    if set(packet.get("stock_status", {})) != set(LEVELS):
        raise ValueError("Incomplete stock status")
    checked_target = {}
    for metric in ("heads", "mak"):
        value = float(target[metric])
        if not math.isfinite(value):
            raise ValueError(f"Nonfinite target {metric}")
        checked_target[metric] = value
    annual = {}
    for record in packet.get("annual_values", ()):
        if len(record) != 7:
            raise ValueError("Invalid annual metric record")
        year, level, group, category, event_type, metric, raw_value = record
        if level not in LEVELS or metric not in METRICS:
            raise ValueError("Unknown annual level or metric")
        key = (int(year), level, str(group), str(category), str(event_type), metric)
        value = float(raw_value)
        if key in annual or not math.isfinite(value):
            raise ValueError("Duplicate or nonfinite annual metric")
        annual[key] = value
    stock = None
    if "annual_stock_values" in packet:
        stock = {}
        for record in packet["annual_stock_values"]:
            if len(record) != 5:
                raise ValueError("Invalid annual stock record")
            date, level, group, metric, raw_value = record
            if level not in LEVELS or metric not in {"heads", "mak"}:
                raise ValueError("Unknown annual stock level or metric")
            key = (str(date), level, str(group), metric)
            value = float(raw_value)
            if key in stock or not math.isfinite(value):
                raise ValueError("Duplicate or nonfinite annual stock value")
            stock[key] = value
    return checked_target, annual, stock


def aggregate_packets(*, base_seed: int, count: int, runs: Sequence[Mapping],
                      band_level: float = 0.8) -> dict:
    """Aggregate only a complete, ordered seed set; failures remain explicit.

    Each run has ``seed``, ``status`` (``ok`` or ``failed``), and either
    ``packet`` or ``error``. Missing annual event keys mean zero events in that
    run. Annual stock is intentionally absent from this version of the packet.
    """
    seeds = seed_sequence(base_seed, count)
    if not isinstance(band_level, (int, float)) or isinstance(band_level, bool) or (
            not math.isfinite(float(band_level)) or not 0.0 < band_level < 1.0):
        raise ValueError("band_level must be between zero and one")
    if len(runs) != count or tuple(run.get("seed") for run in runs) != seeds:
        raise ValueError("Runs must contain every expected seed exactly once, in order")
    failures = []
    checked = []
    for run in runs:
        if run.get("status") == "failed":
            failures.append({"seed": run["seed"],
                             "error": str(run.get("error", "Unknown run failure"))})
        elif run.get("status") == "ok":
            try:
                checked.append((run["seed"], *_validated_packet(run["packet"])))
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                failures.append({"seed": run["seed"], "error": str(exc)})
        else:
            raise ValueError("Unknown run status")
    result = {"version": 1, "status": "incomplete" if failures else "complete",
              "seeds": seeds, "requested_count": count,
              "successful_count": len(checked), "failures": failures,
              "band_level": float(band_level), "band_kind": "empirical_quantile",
              "target": None, "annual_values": None, "annual_stock_values": None,
              "annual_event_samples": None, "annual_stock_samples": None,
              "stock_status_by_seed": {
                  run["seed"]: dict(run["packet"]["stock_status"])
                  for run in runs if run.get("status") == "ok"
                  and any(seed == run["seed"] for seed, *_ in checked)}}
    if failures:
        return result
    stock_present = [stock is not None for _, _, _, stock in checked]
    if any(stock_present) and not all(stock_present):
        result["status"] = "incomplete"
        result["failures"] = [
            {"seed": seed, "error": "Missing certified annual stock values"}
            for seed, _, _, stock in checked if stock is None]
        return result
    result["target"] = {
        metric: _statistics([target[metric] for _, target, _, _ in checked], band_level)
        for metric in ("heads", "mak")}
    keys = sorted({key for _, _, annual, _ in checked for key in annual})
    result["annual_values"] = [
        (*key, _statistics([annual.get(key, 0.0) for _, _, annual, _ in checked], band_level))
        for key in keys]
    # Compact per-seed sums allow exact quantiles after the user's event-type
    # selection. Summing already calculated P10/P90 values would be invalid.
    event_per_run = []
    for _, _, annual, _ in checked:
        grouped = {}
        for (year, level, group, _category, event_type, metric), value in annual.items():
            key = (year, level, group, event_type, metric)
            grouped[key] = grouped.get(key, 0.0) + value
        event_per_run.append(grouped)
    event_keys = sorted(set().union(*(set(values) for values in event_per_run)))
    result["annual_event_samples"] = [
        (*key, [values.get(key, 0.0) for values in event_per_run]) for key in event_keys]
    if all(stock_present):
        dates_by_run = [
            {(date, level) for date, level, _, _ in stock}
            for _, _, _, stock in checked]
        if any(dates != dates_by_run[0] for dates in dates_by_run[1:]):
            result["status"] = "incomplete"
            result["target"] = None
            result["annual_values"] = None
            result["failures"] = [
                {"seed": seed, "error": "Annual stock dates differ between runs"}
                for (seed, _, _, _), dates in zip(checked, dates_by_run)
                if dates != dates_by_run[0]]
            return result
        stock_keys = sorted({key for _, _, _, stock in checked for key in stock})
        result["annual_stock_values"] = [
            (*key, _statistics([stock.get(key, 0.0) for _, _, _, stock in checked], band_level))
            for key in stock_keys]
        result["annual_stock_samples"] = [
            (*key, [stock.get(key, 0.0) for _, _, _, stock in checked]) for key in stock_keys]
    return result


def aggregate_departure_share(*, ensemble: Mapping, runs: Sequence[Mapping],
                              level: str, groups: Sequence[str], end_year: int,
                              metric: str) -> dict:
    """Summarize a selected annual tile from per-seed ratios, never pooled sums."""
    from utils.monte_carlo_departure_rate import departure_share

    if ensemble.get("status") != "complete":
        return {"status": "incomplete", "reason": "Ensemble is incomplete"}
    seeds = tuple(ensemble["seeds"])
    if len(runs) != len(seeds) or tuple(run.get("seed") for run in runs) != seeds:
        raise ValueError("Runs do not match the ensemble seed sequence")
    if not groups or len(groups) != len(set(groups)):
        raise ValueError("Select distinct groups")
    per_seed = []
    for run in runs:
        if run.get("status") != "ok":
            return {"status": "unavailable", "reason": "Run is unavailable",
                    "seed": run["seed"]}
        source = run["packet"].get("departure_rate_inputs", {}).get(level)
        if source is None:
            return {"status": "unavailable", "reason": "No certified rate source",
                    "seed": run["seed"], "level": level}
        try:
            value = departure_share(source, groups=list(groups), end_year=end_year,
                                    metric=metric)
        except (KeyError, TypeError, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc),
                    "seed": run["seed"], "level": level}
        if value["share"] is None:
            return {"status": "unavailable", "reason": "Opening stock is zero",
                    "seed": run["seed"], "level": level}
        per_seed.append({"seed": run["seed"], **value})
    return {"status": "complete", "count": len(per_seed), "level": level,
            "groups": tuple(groups), "end_year": end_year, "metric": metric,
            "per_seed": per_seed,
            "share": _statistics([value["share"] for value in per_seed],
                                 float(ensemble["band_level"]))}
