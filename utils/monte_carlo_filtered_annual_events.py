"""Rebuild annual event samples for every seed under the active sidebar filters."""
from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

from components.sidebar import _apply_filters_uncached
from utils.monte_carlo_annual_event_rows import VERSION, decode_annual_event_rows
from utils.org_simulation_events import METRICS, aggregate_simulation_events


LEVELS = ("Organisationseinheit", "OE-Cluster")


def _samples(events: pd.DataFrame) -> dict[tuple, float]:
    result = {}
    for level in LEVELS:
        if events.empty:
            continue
        grouped = events.groupby(["Jahr", level, "Ereignisart"], dropna=False,
                                 sort=True)[list(METRICS)].sum()
        for (year, group, kind), row in grouped.iterrows():
            for metric in METRICS:
                result[(int(year), level, str(group), str(kind), metric)] = float(row[metric])
    return result


def filtered_annual_event_view(summary: Mapping, ensemble: Mapping,
                               active_filters: Mapping | None) -> tuple[dict, dict]:
    """Return filtered seed-zero reference and empirical per-seed event samples.

    The stored unfiltered event rows must reproduce the certified annual
    samples for both OE levels before any filter is applied.
    """
    blobs = ensemble.get("annual_event_rows")
    count = ensemble.get("count")
    if (ensemble.get("annual_event_rows_version") != VERSION
            or not isinstance(blobs, list) or len(blobs) != count):
        raise ValueError("Jahresereignisse je Lauf fehlen; Simulation neu berechnen")
    original = ensemble["statistics"]["annual_event_samples"]
    certified = {}
    for year, level, group, kind, metric, values in original:
        if level in LEVELS:
            if len(values) != count:
                raise ValueError("Unvollständige Jahresereignisse je Lauf")
            certified[(int(year), level, str(group), str(kind), metric)] = values
    filtered = []
    reference_events = None
    for index, blob in enumerate(blobs):
        events = decode_annual_event_rows(blob)
        raw = _samples(events)
        for key in raw.keys() | certified.keys():
            expected = float(certified.get(key, [0.0] * count)[index])
            if not math.isclose(raw.get(key, 0.0), expected, rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"Jahresereignisse weichen vom zertifizierten Lauf ab: Seed {index}, {key}")
        selected = _apply_filters_uncached(events, dict(active_filters or {}))
        if index == 0:
            reference_events = selected
        filtered.append(_samples(selected))
    keys = sorted(set().union(*(set(seed) for seed in filtered)))
    samples = [(*key, [seed.get(key, 0.0) for seed in filtered]) for key in keys]
    reference = dict(summary)
    reference["events"] = reference_events
    reference["annual_oe"] = aggregate_simulation_events(
        reference_events, dimension="Organisationseinheit")
    reference["annual_cluster"] = aggregate_simulation_events(
        reference_events, dimension="OE-Cluster")
    adjusted = dict(ensemble)
    adjusted["statistics"] = dict(ensemble["statistics"])
    adjusted["statistics"]["annual_event_samples"] = samples
    return reference, adjusted
