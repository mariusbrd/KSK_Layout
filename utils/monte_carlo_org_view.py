"""Certified annual OE bands for a saved, approved reference simulation."""
from __future__ import annotations

from collections.abc import Mapping
import math

import pandas as pd

from utils.monte_carlo_ensemble import _statistics


def approved_org_ensemble(state: Mapping, view) -> dict | None:
    """Never attach an original-run distribution to an adjusted result."""
    saved = state.get("monte_carlo_ensemble_result")
    if (not isinstance(saved, dict) or saved.get("status") != "complete"
            or saved.get("count", 0) < 2
            or saved.get("reference_signature") != view.signature
            or saved.get("reference_instance") != view.instance
            or view.version != f"{view.signature}::{view.instance}::original"
            or not view.approved):
        return None
    statistics = saved.get("statistics")
    if (not isinstance(statistics, dict) or statistics.get("status") != "complete"
            or statistics.get("requested_count") != saved["count"]
            or statistics.get("annual_event_samples") is None
            or statistics.get("annual_stock_samples") is None):
        return None
    return saved


def stock_band_table(chart: pd.DataFrame, *, ensemble: Mapping, level: str,
                     metric: str, base_date=None) -> pd.DataFrame:
    """Match each displayed reference bar to the same annual group and date."""
    stats = ensemble["statistics"]
    samples = {}
    for date, record_level, group, record_metric, values in stats["annual_stock_samples"]:
        # The stock worker labels unmapped cluster rows "Unclustered";
        # the approved OE bridge displays the same group as "Nicht zugeordnet".
        if record_level == "OE-Cluster" and group in {"Unclustered", "UNMAPPED", "Unmapped"}:
            group = "Nicht zugeordnet"
        key = (date, record_level, group, record_metric)
        if key in samples:
            samples[key] = [left + right for left, right in zip(samples[key], values)]
        else:
            samples[key] = values
    rows = []
    opening_date = pd.Timestamp(base_date).date().isoformat() if base_date is not None else None
    for _, row in chart.iterrows():
        date = pd.Timestamp(row["Stichtag"]).date().isoformat()
        group = str(row["Organisation"])
        key = (date, level, group, "heads" if metric == "Köpfe" else "mak")
        reference = float(row[f"Bestand-{metric}"])
        if key in samples:
            values = samples[key]
        elif date == opening_date:
            # The opening stock is common to every seed and need not be sent
            # as an annual worker record.
            values = [reference] * ensemble["count"]
        else:
            values = [0.0] * ensemble["count"]
        if len(values) != ensemble["count"]:
            raise ValueError("Unvollständige OE-Jahresbestandswerte")
        expected = round(float(values[0]), 2) if metric == "MAK" else float(values[0])
        if not math.isclose(reference, expected, rel_tol=0, abs_tol=1e-6):
            raise ValueError(
                f"OE-Jahresbestand weicht vom Referenzlauf ab: {date}, {level}, "
                f"{group}, {metric}: sichtbar={reference}, Seed 0={expected}")
        rows.append({"Stichtag": date, "Organisation": group,
                     "Referenzlauf": reference,
                     **_statistics(values, ensemble["band_level"])})
    return pd.DataFrame(rows)


def event_band_table(view: Mapping, *, ensemble: Mapping) -> pd.DataFrame:
    """Sum selected event types within each seed before calculating quantiles."""
    stats = ensemble["statistics"]
    dimension, metric = view["dimension"], view["metric"]
    selected = set(view["event_types"])
    lookup = {}
    for year, level, group, event_type, record_metric, values in stats["annual_event_samples"]:
        if level == dimension and record_metric == metric and event_type in selected:
            key = (int(year), str(group))
            previous = lookup.setdefault(key, [0.0] * ensemble["count"])
            if len(values) != ensemble["count"]:
                raise ValueError("Unvollständige OE-Jahresereignisse")
            for index, value in enumerate(values):
                previous[index] += value
    rows = []
    for year, group in view["chart"][["Jahr", dimension]].itertuples(index=False, name=None):
        values = lookup.get((int(year), str(group)), [0.0] * ensemble["count"])
        reference = view["chart"].loc[
            view["chart"]["Jahr"].eq(year) & view["chart"][dimension].eq(group), metric].iloc[0]
        if not math.isclose(float(reference), float(values[0]), rel_tol=0, abs_tol=1e-6):
            raise ValueError("OE-Jahresereignisse weichen vom Referenzlauf ab")
        rows.append({"Jahr": int(year), dimension: str(group),
                     "Referenzlauf": float(reference),
                     **_statistics(values, ensemble["band_level"])})
    return pd.DataFrame(rows)


def target_rank_band_stats(chart: pd.DataFrame, *, ensemble: Mapping,
                           target_date, metric: str) -> dict | None:
    """Use certified final OE stock only when it equals every reference bar."""
    from utils.monte_carlo_chart_values import CHART_VERSION

    code = {"Köpfe": "heads", "MAK": "mak"}.get(metric)
    if code is None or target_date is None:
        return None
    date = pd.Timestamp(target_date).date().isoformat()
    samples = {(record_date, level, group, record_metric): values
               for record_date, level, group, record_metric, values
               in ensemble["statistics"]["annual_stock_samples"]}
    rows = []
    for group, reference in chart[["Organisationseinheit", "Wert"]].itertuples(index=False, name=None):
        values = samples.get((date, "Organisationseinheit", str(group), code))
        if (values is None or len(values) != ensemble["count"]
                or not math.isclose(float(reference), float(values[0]), rel_tol=0, abs_tol=1e-6)):
            return None
        rows.append((code, "Organisationseinheit", str(group),
                     _statistics(values, ensemble["band_level"])))
    return {"version": CHART_VERSION, "values": rows}
