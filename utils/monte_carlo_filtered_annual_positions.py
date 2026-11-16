"""Exact filtered annual position-stock samples, separate from the OE bridge."""
from __future__ import annotations

import math
from collections.abc import Mapping

from components.sidebar import _apply_filters_uncached
from utils.compact_regular_stock import _grouped_stock_metrics_indexed
from utils.monte_carlo_annual_positions import VERSION, decode_annual_positions
from utils.monte_carlo_ensemble import _statistics


LEVELS = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster")


def _position_values(years, active_filters=None):
    result = {}
    for frame in years:
        date = frame["Stichtag"].iloc[0].date().isoformat()
        selected = (_apply_filters_uncached(frame, dict(active_filters))
                    if active_filters else frame)
        for level in LEVELS:
            for group, (heads, mak) in _grouped_stock_metrics_indexed(selected, level).items():
                result[(date, level, str(group), "heads")] = float(heads)
                result[(date, level, str(group), "mak")] = float(mak)
    return result


def filtered_annual_position_samples(ensemble: Mapping,
                                     active_filters: Mapping | None) -> list[tuple]:
    """Filter positions per seed and year, then summarize the resulting sums.

    This contract represents the projected position stock. The separately
    certified departure bridge may contain technical effects and must not be
    replaced with these values without a person-level bridge allocation.
    """
    count = ensemble.get("count")
    blobs = ensemble.get("annual_positions")
    if (ensemble.get("annual_positions_version") != VERSION
            or not isinstance(blobs, list) or len(blobs) != count):
        raise ValueError("Jahresbestandszeilen je Lauf fehlen; Simulation neu berechnen")
    expected = {}
    for date, level, group, metric, values in ensemble["statistics"]["annual_stock_samples"]:
        if len(values) != count:
            raise ValueError("Unvollständige zertifizierte Jahresbestände")
        expected[(str(date), level, str(group), metric)] = values
    filtered = []
    for index, blob in enumerate(blobs):
        years = decode_annual_positions(blob)
        raw = _position_values(years)
        for key in raw.keys() | expected.keys():
            certified = float(expected.get(key, [0.0] * count)[index])
            if not math.isclose(raw.get(key, 0.0), certified, rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"Jahresbestand weicht vom zertifizierten Lauf ab: Seed {index}, {key}")
        filtered.append(_position_values(years, active_filters))
    keys = sorted(set().union(*(set(seed) for seed in filtered)))
    return [(*key, [seed.get(key, 0.0) for seed in filtered],
             _statistics([seed.get(key, 0.0) for seed in filtered], ensemble["band_level"]))
            for key in keys]
