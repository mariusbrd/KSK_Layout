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
                       stock_status: Mapping[str, str],
                       annual_stock_tables: Mapping[str, pd.DataFrame] | None = None,
                       departure_rate_inputs: Mapping[str, dict] | None = None) -> dict:
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
    packet = {"version": PACKET_VERSION,
              "target": {key: target_totals[key].item() if hasattr(target_totals[key], "item")
                         else target_totals[key] for key in ("heads", "mak", "eur")},
              "annual_values": values,
              "stock_status": {level: stock_status[level] for level in LEVELS}}
    if annual_stock_tables is not None:
        if set(annual_stock_tables) != set(LEVELS):
            raise ValueError("All four annual stock levels are required")
        stock_values = []
        stock_seen = set()
        for level in LEVELS:
            if stock_status[level] not in {"Abgestimmt", "Verfügbar"}:
                raise ValueError(f"Annual stock is not certified for {level}")
            frame = annual_stock_tables[level]
            if not {"Stichtag", level, "Köpfe", "MAK"}.issubset(frame.columns):
                raise ValueError(f"Missing annual stock columns for {level}")
            if frame.empty:
                raise ValueError(f"Missing certified annual stock rows for {level}")
            for row in frame.itertuples(index=False):
                # itertuples renames non-identifier columns; positional access
                # through the original column order retains the exact labels.
                record = dict(zip(frame.columns, tuple(row)))
                date = pd.Timestamp(record["Stichtag"])
                if pd.isna(date):
                    raise ValueError("Invalid annual stock date")
                for metric, column in (("heads", "Köpfe"), ("mak", "MAK")):
                    key = (date.date().isoformat(), level, str(record[level]), metric)
                    value = record[column]
                    if key in stock_seen or pd.isna(value) or not math.isfinite(float(value)):
                        raise ValueError(f"Duplicate or invalid annual stock: {key}")
                    stock_seen.add(key)
                    stock_values.append((*key, value.item() if hasattr(value, "item") else value))
        packet["annual_stock_values"] = stock_values
    if departure_rate_inputs is not None:
        from utils.monte_carlo_departure_rate import departure_share
        if not set(departure_rate_inputs).issubset(LEVELS):
            raise ValueError("Unknown departure-rate level")
        for level, source in departure_rate_inputs.items():
            if source.get("level") != level:
                raise ValueError("Departure-rate level mismatch")
            if stock_status[level] not in {"Abgestimmt", "Verfügbar"}:
                raise ValueError(f"Departure rate is not certified for {level}")
            for group in source.get("opening", {}):
                for metric in ("heads", "mak"):
                    departure_share(source, groups=[group], end_year=9999,
                                    metric=metric)
        packet["departure_rate_inputs"] = dict(departure_rate_inputs)
    return packet
