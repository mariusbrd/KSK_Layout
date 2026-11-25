"""Read-only trajectories from a completed Monte Carlo result."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

import pandas as pd

from utils.monte_carlo_representative_runs import select_representative_runs


def stock_trajectories(ensemble: dict, metric: str, level: str = "Gesamt",
                       group: str | None = None) -> pd.DataFrame:
    """Use the certified remaining-stock bridge shown on the OE yearly page."""
    if metric not in {"heads", "mak"} or level not in {"Gesamt", "Organisationseinheit", "OE-Cluster"}:
        raise ValueError("Ungültige Bestandsauswahl")
    count = ensemble["count"]
    samples = ensemble["statistics"].get("departure_stock_samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Jahreswerte des verbleibenden heutigen Bestands fehlen für diesen Lauf")
    sums = defaultdict(lambda: [0.0] * count)
    dates = set()
    has_group = level == "Gesamt"
    for date, row_level, row_group, row_metric, values in samples:
        source_level = "Organisationseinheit" if level == "Gesamt" else level
        if row_metric != metric or row_level != source_level:
            continue
        date = str(date)
        dates.add(date)
        if level != "Gesamt" and (row_level != level or str(row_group) != group):
            continue
        has_group = True
        if len(values) != count:
            raise ValueError("Unvollständige Laufwerte")
        for index, value in enumerate(values):
            sums[date][index] += float(value)
    if not dates or not has_group:
        return pd.DataFrame(columns=["Datum", "Laufindex", "Wert"])
    records = [(date, index, sums[date][index])
               for date in sorted(dates) for index in range(count)]
    return pd.DataFrame(records, columns=["Datum", "Laufindex", "Wert"])


def median_path_index(ensemble: dict) -> int:
    """Pick one real remaining-stock path nearest to global annual medians."""
    count = ensemble["count"]
    distances = [0.0] * count
    for metric in ("heads", "mak"):
        table = stock_trajectories(ensemble, metric)
        if table.empty:
            raise ValueError("Jahresbestände fehlen")
        for _, rows in table.groupby("Datum", sort=True):
            values = rows.sort_values("Laufindex")["Wert"].tolist()
            middle = median(values)
            span = max(values) - min(values)
            if span:
                for index, value in enumerate(values):
                    distances[index] += ((value - middle) / span) ** 2
    return min(range(count), key=lambda index: (distances[index], index))


def trajectory_frequencies(table: pd.DataFrame) -> pd.DataFrame:
    """Count exact, overlapping annual paths without changing their values."""
    if table.empty:
        return pd.DataFrame(columns=["Läufe", "Laufindizes"])
    paths = defaultdict(list)
    for index, rows in table.groupby("Laufindex", sort=True):
        path = tuple(rows.sort_values("Datum")["Wert"].tolist())
        paths[path].append(int(index))
    records = [{"Läufe": len(indices), "Laufindizes": indices}
               for indices in paths.values()]
    return pd.DataFrame(records).sort_values("Läufe", ascending=False, kind="stable").reset_index(drop=True)


def highlighted_indices(ensemble: dict) -> dict[str, int]:
    selected = select_representative_runs(ensemble)
    return {
        "Geringste Fluktuation": selected["best"]["index"],
        "Höchste Fluktuation": selected["worst"]["index"],
        "Median-naher Bestandsverlauf": median_path_index(ensemble),
        "Typischer Verlauf (bestehende OE-Auswahl)": selected["typical"]["index"],
    }
