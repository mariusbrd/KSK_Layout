"""Descriptive diagnostics for already saved Monte Carlo stock paths."""
from __future__ import annotations

import math

import pandas as pd

from utils.monte_carlo_ensemble import _quantile


def _matrix(table: pd.DataFrame) -> pd.DataFrame:
    if table.empty or table.duplicated(["Datum", "Laufindex"]).any():
        raise ValueError("Jahreswerte fehlen oder sind doppelt")
    matrix = table.pivot(index="Datum", columns="Laufindex", values="Wert").sort_index().sort_index(axis=1)
    if matrix.isna().any().any() or list(matrix.columns) != list(range(len(matrix.columns))):
        raise ValueError("Jahreswerte sind nicht für alle Läufe vollständig")
    return matrix.astype(float)


def _distribution(values) -> dict[str, float]:
    values = [float(value) for value in values]
    return {
        "Mittelwert": math.fsum(values) / len(values),
        "Minimum": min(values), "P10": _quantile(values, .1),
        "P25": _quantile(values, .25), "Median": _quantile(values, .5),
        "P75": _quantile(values, .75), "P90": _quantile(values, .9),
        "Maximum": max(values),
    }


def annual_distribution(table: pd.DataFrame) -> pd.DataFrame:
    matrix = _matrix(table)
    return pd.DataFrame([{"Stichtag": date, **_distribution(row)}
                         for date, row in matrix.iterrows()])


def highlighted_yearly_ranks(table: pd.DataFrame, highlights: dict[str, int]) -> pd.DataFrame:
    matrix = _matrix(table)
    records = []
    for date, row in matrix.iterrows():
        values = row.tolist()
        for label, index in highlights.items():
            value = float(row[index])
            # Midrank: identical outcomes receive the same central rank.
            rank = 100 * (sum(other < value for other in values)
                          + .5 * sum(other == value for other in values)) / len(values)
            records.append({"Stichtag": date, "Markierung": label, "Lauf": index + 1,
                            "Bestand": value, "Perzentilrang (%)": rank,
                            "Abstand zum Median": value - _quantile(values, .5)})
    return pd.DataFrame(records)


def run_changes(table: pd.DataFrame) -> pd.DataFrame:
    matrix = _matrix(table)
    first, last = matrix.iloc[0], matrix.iloc[-1]
    return pd.DataFrame([
        {"Lauf": index + 1, "Startbestand": float(first[index]),
         "Endbestand": float(last[index]), "Veränderung": float(last[index] - first[index]),
         "Veränderung (%)": 100 * float(last[index] - first[index]) / float(first[index])
         if first[index] != 0 else float("nan")}
        for index in matrix.columns
    ])


def change_distribution(changes: pd.DataFrame) -> pd.DataFrame:
    records = [{"Kennzahl": "Veränderung", **_distribution(changes["Veränderung"])}]
    percentages = changes["Veränderung (%)"].dropna()
    if not percentages.empty:
        records.append({"Kennzahl": "Veränderung (%)", **_distribution(percentages)})
    return pd.DataFrame(records)


def endpoint_convergence(table: pd.DataFrame, *, step: int = 10) -> pd.DataFrame:
    matrix = _matrix(table)
    values = matrix.iloc[-1].tolist()
    checkpoints = list(range(step, len(values) + 1, step))
    if not checkpoints or checkpoints[-1] != len(values):
        checkpoints.append(len(values))
    return pd.DataFrame([
        {"Läufe": count, "Mittelwert": math.fsum(values[:count]) / count,
         "P10": _quantile(values[:count], .1), "P90": _quantile(values[:count], .9)}
        for count in checkpoints
    ])
