"""Compact value contract for a future Monte Carlo additional run.

This pure adapter currently reads complete reference outputs. It is not a fast
simulation path and is not used by the Streamlit app.
"""
from __future__ import annotations

import math
from typing import Mapping

import pandas as pd

from utils.org_simulation_events import METRICS

LEVELS = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster")
PACKET_VERSION = 1


def make_metric_packet(*, target_totals: Mapping[str, int | float], annual_tables: Mapping[str, pd.DataFrame],
                       stock_status: Mapping[str, str]) -> dict:
    """Keep exact per-run annual values without employee rows or display frames."""
    if set(annual_tables) != set(LEVELS) or set(stock_status) != set(LEVELS):
        raise ValueError("All four annual levels and stock statuses are required")
    if set(target_totals) != {"heads", "mak", "eur"}:
        raise ValueError("Target totals must contain heads, mak and eur")
    values = []
    seen = set()
    for level in LEVELS:
        frame = annual_tables[level]
        required = {"Jahr", level, "Kategorie", "Ereignisart", *METRICS}
        if not required.issubset(frame.columns):
            raise ValueError(f"Missing annual columns for {level}")
        for row in frame.itertuples(index=False, name=None):
            record = dict(zip(frame.columns, row))
            for metric in METRICS:
                key = (int(record["Jahr"]), level, str(record[level]),
                       str(record["Kategorie"]), str(record["Ereignisart"]), metric)
                value = record[metric]
                if key in seen or pd.isna(value) or not math.isfinite(float(value)):
                    raise ValueError(f"Duplicate or invalid annual metric: {key}")
                seen.add(key)
                values.append((*key, value.item() if hasattr(value, "item") else value))
    return {"version": PACKET_VERSION,
            "target": {key: target_totals[key].item() if hasattr(target_totals[key], "item")
                       else target_totals[key] for key in ("heads", "mak", "eur")},
            "annual_values": values,
            "stock_status": {level: stock_status[level] for level in LEVELS}}
