"""Compact inputs for the existing OE and JF annual departure-share tiles.

The caller must supply each view's reporting-adjusted events. Original event
tables cannot reproduce the displayed MAK numerator.
"""
from __future__ import annotations

import math

import pandas as pd

from utils.org_simulation_events import DEPARTURES

LEVELS = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster")
METRIC_COLUMNS = {"heads": "Köpfe", "mak": "MAK"}


def departure_rate_input(*, level: str, opening: pd.DataFrame,
                         reporting_events: pd.DataFrame) -> dict:
    """Store opening stock and year/type losses without person-level rows."""
    if level not in LEVELS:
        raise ValueError("Unknown organizational level")
    if not {level, "Köpfe", "MAK"}.issubset(opening.columns):
        raise ValueError("Incomplete opening stock")
    if not reporting_events.empty and not {
            level, "Jahr", "Ereignisart", "Köpfe-Veränderung", "MAK-Veränderung"
    }.issubset(reporting_events.columns):
        raise ValueError("Incomplete reporting events")
    stock = {}
    for row in opening[[level, "Köpfe", "MAK"]].itertuples(index=False, name=None):
        group = str(row[0])
        if group in stock or any(not math.isfinite(float(v)) for v in row[1:]):
            raise ValueError("Duplicate or nonfinite opening stock")
        stock[group] = {metric: float(row[index]) for index, metric in ((1, "heads"), (2, "mak"))}
    losses = []
    if not reporting_events.empty:
        for year, group, kind, heads, mak in reporting_events[[
                "Jahr", level, "Ereignisart", "Köpfe-Veränderung", "MAK-Veränderung"
        ]].itertuples(index=False, name=None):
            if not all(math.isfinite(float(value)) for value in (heads, mak)):
                raise ValueError("Nonfinite reporting loss")
            if kind in DEPARTURES or kind == "ATZ_AR_TO_FR":
                losses.append((int(year), str(group), str(kind),
                               max(0.0, -float(heads)), max(0.0, -float(mak))))
    return {"level": level, "opening": stock, "losses": losses}


def departure_share(source: dict, *, groups: list[str], end_year: int,
                    metric: str) -> dict:
    """Reproduce a selected tile's ratio from one seed's compact inputs."""
    if metric not in METRIC_COLUMNS:
        raise ValueError("Unknown stock metric")
    if not groups or len(groups) != len(set(groups)):
        raise ValueError("Select distinct groups")
    opening = source["opening"]
    if any(group not in opening for group in groups):
        raise ValueError("Missing opening stock group")
    denominator = float(sum(opening[group][metric] for group in groups))
    selected = set(groups)
    numerator = 0.0
    for year, group, kind, heads, mak in source["losses"]:
        if group not in selected or year > end_year:
            continue
        if source["level"] in ("Organisationseinheit", "OE-Cluster"):
            # OE counts ATZ release only as a MAK retirement effect.
            if kind == "ATZ_AR_TO_FR" and metric == "heads":
                continue
        numerator += heads if metric == "heads" else mak
    return {"numerator": numerator, "denominator": denominator,
            "share": numerator / denominator if denominator > 0 else None}


def certified_rate_inputs(context: dict, summary: dict) -> dict[str, dict]:
    """Read only views whose current product stock bridge is certified."""
    if summary.get("run_signature") != context.get("run_signature"):
        raise ValueError("Summary belongs to a different run")
    bridge = summary.get("stock_reconciliation", {})
    result = {}
    if bridge.get("status") == "Abgestimmt":
        annual, reported = bridge["annual"], bridge["reporting_events"]
        for level in LEVELS[:2]:
            opening = annual.loc[
                annual.Ebene.eq(level) & annual.Art.eq("Ausgangsstichtag"),
                ["Organisation", "Bestand-Köpfe", "Bestand-MAK"]
            ].rename(columns={"Organisation": level, "Bestand-Köpfe": "Köpfe",
                              "Bestand-MAK": "MAK"})
            if not opening.empty:
                result[level] = departure_rate_input(
                    level=level, opening=opening, reporting_events=reported)
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    for level in LEVELS[2:]:
        view = build_jobfamily_yearly_view(context, summary, dimension=level)
        stock = view["stock"]
        if stock.empty:
            continue
        opening = stock.loc[stock.Art.eq("Ausgangsstichtag")]
        heads = opening.loc[opening.Kennzahl.eq("Köpfe"), [level, "Bestand"]]
        mak = opening.loc[opening.Kennzahl.eq("MAK"), [level, "Bestand"]]
        both = heads.merge(mak, on=level, how="outer", suffixes=("-Köpfe", "-MAK"))
        both = both.rename(columns={"Bestand-Köpfe": "Köpfe", "Bestand-MAK": "MAK"})
        result[level] = departure_rate_input(
            level=level, opening=both, reporting_events=view["reporting_events"])
    return result
