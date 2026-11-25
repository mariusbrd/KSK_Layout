"""Choose complete, reproducible seed paths for the OE scenario views."""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping


LEVEL = "Organisationseinheit"


def _values(raw, count: int) -> list[float]:
    if not isinstance(raw, (tuple, list)) or len(raw) != count:
        raise ValueError("Unvollständige Laufwerte für die Szenarioauswahl")
    values = [float(value) for value in raw]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Ungültige Laufwerte für die Szenarioauswahl")
    return values


def select_representative_runs(ensemble: Mapping) -> dict[str, dict]:
    """Select whole paths using companywide QUIT and annual stock trajectories.

    Best and worst use total voluntary departures. The typical run minimizes
    its normalized squared distance to the ensemble mean for each year's
    voluntary departures, remaining heads, and remaining MAK. Each dimension
    and year has equal weight; a constant dimension contributes zero.
    """
    if ensemble.get("status") != "complete":
        raise ValueError("Szenarioauswahl erfordert ein vollständiges Ensemble")
    count = ensemble.get("count")
    pairs = ensemble.get("seed_pairs")
    statistics = ensemble.get("statistics")
    if (not isinstance(count, int) or isinstance(count, bool) or count < 2
            or not isinstance(pairs, (tuple, list)) or len(pairs) != count
            or not isinstance(statistics, Mapping) or statistics.get("status") != "complete"
            or statistics.get("requested_count") != count):
        raise ValueError("Ungültiger Laufvertrag für die Szenarioauswahl")
    event_rows = statistics.get("annual_event_samples")
    stock_rows = statistics.get("annual_stock_samples")
    if not isinstance(event_rows, list) or not isinstance(stock_rows, list):
        raise ValueError("Jahreswerte für die Szenarioauswahl fehlen")

    quit_by_year = defaultdict(lambda: [0.0] * count)
    stock_by_year = defaultdict(lambda: [0.0] * count)
    stock_keys = set()
    for year, level, _group, kind, metric, raw in event_rows:
        if level == LEVEL and kind == "QUIT" and metric == "Abgänge":
            values = _values(raw, count)
            annual = quit_by_year[int(year)]
            for index, value in enumerate(values):
                annual[index] += value
    for date, level, group, metric, raw in stock_rows:
        if level != LEVEL or metric not in {"heads", "mak"}:
            continue
        key = (str(date), str(group), metric)
        if key in stock_keys:
            raise ValueError("Doppelte Jahresbestandswerte für die Szenarioauswahl")
        stock_keys.add(key)
        annual = stock_by_year[(str(date), metric)]
        for index, value in enumerate(_values(raw, count)):
            annual[index] += value
    dates = sorted({date for date, _metric in stock_by_year})
    if not dates or any((date, metric) not in stock_by_year
                        for date in dates for metric in ("heads", "mak")):
        raise ValueError("Unvollständige Jahresbestände für die Szenarioauswahl")
    years = {int(date[:4]) for date in dates}
    if not set(quit_by_year).issubset(years):
        raise ValueError("Abgänge außerhalb des gespeicherten Jahresverlaufs")

    turnover = [math.fsum(quit_by_year[year][index] for year in sorted(years))
                for index in range(count)]
    paths = [quit_by_year[year] for year in sorted(years)]
    paths.extend(stock_by_year[(date, metric)] for date in dates for metric in ("heads", "mak"))
    distance = [0.0] * count
    for values in paths:
        mean = math.fsum(values) / count
        span = max(values) - min(values)
        if span:
            for index, value in enumerate(values):
                distance[index] += ((value - mean) / span) ** 2
    distance = [math.sqrt(value / len(paths)) for value in distance]

    def selected(index: int) -> dict:
        pair = pairs[index]
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError("Ungültiges Seed-Paar für die Szenarioauswahl")
        return {"index": index, "seed_pair": (int(pair[0]), int(pair[1])),
                "quit_departures": turnover[index], "mean_path_distance": distance[index]}

    return {
        "best": selected(min(range(count), key=lambda index: (turnover[index], index))),
        "worst": selected(min(range(count), key=lambda index: (-turnover[index], index))),
        "typical": selected(min(range(count), key=lambda index: (distance[index], index))),
    }
