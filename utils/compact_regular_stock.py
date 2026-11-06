"""Isolated regular-only stock projection and stock/event diagnostics.

This is a read-only diagnostic. It does not modify event or stock semantics.
Person identifiers are retained in the returned detail for local inspection;
callers publishing a report should aggregate or anonymize them first.
"""
from __future__ import annotations

import pandas as pd

from abgaenge.schemas import normalize_persnr
from utils.org_simulation_reconciliation import grouped_stock_metrics


def allocate_departures_to_position_groups(
    baseline: pd.DataFrame,
    events: pd.DataFrame,
    dimension: str,
) -> pd.DataFrame:
    """Split existing-person departures over their occupied original groups.

    The product's event view keeps its original group label. This copy is only
    for a stock projection and uses the person's actual reported position MAK,
    not Soll-MAK, as allocation weight.
    """
    work = events.copy()
    if work.empty:
        return work
    occupied = baseline.loc[~baseline["Is_Vacant"].fillna(True).astype(bool)
                            & baseline["PersNr"].notna()].copy()
    occupied["persnr"] = normalize_persnr(occupied["PersNr"])
    occupied["MAK_Reporting"] = pd.to_numeric(
        occupied["MAK_Reporting"], errors="coerce").fillna(0.0)
    groups = occupied.groupby(["persnr", dimension], sort=True)[
        "MAK_Reporting"].sum()
    members: dict[str, list[tuple[str, float]]] = {}
    for person, values in groups.groupby(level=0, sort=True):
        by_group = values.droplevel(0)
        total = float(by_group.sum())
        members[person] = [(group, float(mak / total) if total > 0 else 1.0 / len(by_group))
                           for group, mak in by_group.items()]
    work["persnr"] = normalize_persnr(work["persnr"])
    result = []
    for _, event in work.iterrows():
        if event.get("Ereignisquelle") != "departure_events":
            result.append(event.copy())
            continue
        destinations = members.get(event["persnr"])
        if not destinations:
            result.append(event.copy())
            continue
        for group, ratio in destinations:
            row = event.copy()
            row[dimension] = group
            row["MAK-Veränderung"] = float(event["MAK-Veränderung"]) * ratio
            result.append(row)
    return pd.DataFrame(result, columns=work.columns).reset_index(drop=True)


def project_regular_group_stock_from_events(
    baseline: pd.DataFrame,
    events: pd.DataFrame,
    *,
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    dimension: str = "Organisationseinheit",
) -> pd.DataFrame:
    """Probe year-end group stocks without rebuilding any target snapshot."""
    opening = grouped_stock_metrics(baseline, dimension)
    allocated = allocate_departures_to_position_groups(baseline, events, dimension)
    if not allocated.empty:
        allocated["_day"] = pd.to_datetime(allocated["event_date"]).dt.normalize()
    start, end = pd.Timestamp(base_date), pd.Timestamp(target_date)
    rows = []
    for year in range(start.year, end.year + 1):
        date = min(pd.Timestamp(year, 12, 31), end)
        if date <= start:
            continue
        current = (allocated.loc[allocated["_day"].ge(start.normalize())
                                 & allocated["_day"].le(date)]
                   if not allocated.empty else allocated)
        deltas = (current.groupby(dimension, sort=True)[
            ["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
                  if not current.empty else pd.DataFrame())
        for group in sorted(set(opening) | set(deltas.index)):
            heads, mak = opening.get(group, (0, 0.0))
            rows.append({"Stichtag": date, dimension: group,
                         "Köpfe": heads + (float(deltas.at[group, "Köpfe-Veränderung"])
                                           if group in deltas.index else 0.0),
                         "MAK": mak + (float(deltas.at[group, "MAK-Veränderung"])
                                        if group in deltas.index else 0.0)})
    return pd.DataFrame(rows, columns=["Stichtag", dimension, "Köpfe", "MAK"])


def project_regular_position_stocks(
    baseline: pd.DataFrame,
    mapped_baseline: pd.DataFrame,
    departure_events: pd.DataFrame,
    new_people: list[dict],
    *,
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """Replay regular positions once for all four OE/JF stock dimensions.

    Apprentices, trainees and conversions require a separate chronological
    position-state extension and are deliberately rejected here.
    """
    if any(not str(person.get("PersNr", "")).startswith("NH_")
           for person in new_people):
        raise ValueError("Regular position stock projection accepts only NH_ hires")
    dimensions = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster")
    initial = baseline[["PersNr", "Is_Vacant", "MAK_Reporting",
                        "Organisationseinheit", "OE-Cluster"]].copy().reset_index(drop=True)
    initial["Jobfamily"] = mapped_baseline["Jobfamily"].reset_index(drop=True)
    initial["JF-Cluster"] = mapped_baseline["JF-Cluster"].reset_index(drop=True)
    initial["_person"] = normalize_persnr(initial["PersNr"])
    occupied = initial.loc[initial["_person"].notna()
                           & ~initial["Is_Vacant"].fillna(True).astype(bool)]
    person_rows = occupied.groupby("_person", sort=False).groups
    if departure_events.empty:
        event_rows = []
    else:
        ordered_events = departure_events.sort_values("event_date", kind="stable").copy()
        ordered_events["_person"] = normalize_persnr(ordered_events["persnr"])
        ordered_events["_day"] = pd.to_datetime(
            ordered_events["event_date"]).dt.normalize()
        event_rows = ordered_events.to_dict("records")
    start, end = pd.Timestamp(base_date), pd.Timestamp(target_date)
    output: dict[str, list[dict]] = {dimension: [] for dimension in dimensions}
    for year in range(start.year, end.year + 1):
        date = min(pd.Timestamp(year, 12, 31), end)
        if date <= start:
            continue
        frame = initial.copy()
        for event in event_rows:
            if event["_day"] > date:
                continue
            indices = person_rows.get(event["_person"])
            if indices is None or len(indices) == 0:
                continue
            indices = list(indices)
            if int(event.get("headcount_change", 0) or 0) < 0:
                frame.loc[indices, "PersNr"] = pd.NA
                frame.loc[indices, "Is_Vacant"] = True
                frame.loc[indices, "MAK_Reporting"] = 0.0
                continue
            mak_delta = float(event.get("mak_change", 0.0) or 0.0)
            if mak_delta:
                old = pd.to_numeric(frame.loc[indices, "MAK_Reporting"],
                                    errors="coerce").fillna(0.0)
                total = float(old.sum())
                weights = old / total if total > 0 else pd.Series(
                    1.0 / len(indices), index=old.index)
                frame.loc[indices, "MAK_Reporting"] = (
                    old + mak_delta * weights).clip(lower=0.0).to_numpy()
        for person in new_people:
            if pd.Timestamp(person["Eintritt"]).normalize() > date:
                continue
            index = person.get("_plan_position_index")
            if pd.isna(index):
                continue
            index = int(index)
            frame.at[index, "PersNr"] = person["PersNr"]
            frame.at[index, "Is_Vacant"] = False
            frame.at[index, "MAK_Reporting"] = float(person["mak"])
        for dimension in dimensions:
            output[dimension].extend(
                {"Stichtag": date, dimension: group,
                 "Köpfe": heads, "MAK": mak}
                for group, (heads, mak) in grouped_stock_metrics(frame, dimension).items())
    return {dimension: pd.DataFrame(
        output[dimension], columns=["Stichtag", dimension, "Köpfe", "MAK"])
        for dimension in dimensions}


def _occupied_person_groups(snapshot: pd.DataFrame, dimension: str) -> pd.DataFrame:
    occupied = snapshot.loc[
        ~snapshot["Is_Vacant"].fillna(True).astype(bool)
        & snapshot["PersNr"].notna()].copy()
    occupied["persnr"] = normalize_persnr(occupied["PersNr"])
    occupied = occupied.loc[occupied["persnr"].notna()]
    occupied["MAK_Reporting"] = pd.to_numeric(
        occupied["MAK_Reporting"], errors="coerce").fillna(0.0)
    occupied["_atz_fr"] = occupied.get(
        "ist_atz_fr", pd.Series(False, index=occupied.index)).fillna(False).astype(bool)
    occupied["_ruh"] = occupied.get(
        "Status kundenindividuell", pd.Series("", index=occupied.index)
    ).astype(str).str.strip().eq("Ruhendes Beschäftigungsverhältnis")
    occupied["_positions"] = [(int(i),) for i in occupied.index]
    return occupied.groupby([dimension, "persnr"], dropna=False, sort=True).agg(
        mak=("MAK_Reporting", "sum"),
        heads=("persnr", lambda _: 1),
        atz_fr=("_atz_fr", "any"),
        ruhend=("_ruh", "any"),
        positions=("_positions", "sum"),
    )


def reconcile_regular_year_stock(
    before: pd.DataFrame,
    after: pd.DataFrame,
    events: pd.DataFrame,
    *,
    year: int,
    dimension: str = "Organisationseinheit",
    start_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return exact person/group residuals of stock change minus event MAK.

    With ``start_date``, include all events from that calendar day through the
    given year end. This matters for the first forecast period: the product
    can process events on the opening snapshot date itself.
    """
    old = _occupied_person_groups(before, dimension).add_prefix("before_")
    new = _occupied_person_groups(after, dimension).add_prefix("after_")
    if events.empty:
        event_totals = pd.DataFrame(columns=["event_mak", "event_heads"])
    else:
        event_days = pd.to_datetime(events["event_date"]).dt.normalize()
        first_day = (pd.Timestamp(start_date).normalize() if start_date is not None
                     else pd.Timestamp(year, 1, 1))
        dated = events.loc[event_days.ge(first_day)
                           & event_days.le(pd.Timestamp(year, 12, 31))].copy()
        dated["persnr"] = normalize_persnr(dated["persnr"])
        dated["event_mak"] = pd.to_numeric(
            dated["MAK-Veränderung"], errors="coerce").fillna(0.0)
        dated["event_heads"] = pd.to_numeric(
            dated["Köpfe-Veränderung"], errors="coerce").fillna(0.0)
        event_totals = dated.groupby([dimension, "persnr"], dropna=False, sort=True)[
            ["event_mak", "event_heads"]].sum()
    detail = pd.concat([old, new, event_totals], axis=1, sort=True).reset_index()
    for column in ("before_mak", "after_mak", "event_mak",
                   "before_heads", "after_heads", "event_heads"):
        detail[column] = pd.to_numeric(detail[column], errors="coerce").fillna(0.0)
    detail["stock_change"] = detail["after_mak"] - detail["before_mak"]
    detail["residual_mak"] = detail["stock_change"] - detail["event_mak"]
    detail["residual_heads"] = (detail["after_heads"] - detail["before_heads"]
                                - detail["event_heads"])
    before_present = detail["before_positions"].notna()
    after_present = detail["after_positions"].notna()
    atz_changed = detail["before_atz_fr"].astype("boolean").fillna(False).ne(
        detail["after_atz_fr"].astype("boolean").fillna(False))
    ruh_changed = detail["before_ruhend"].astype("boolean").fillna(False).ne(
        detail["after_ruhend"].astype("boolean").fillna(False))
    positions_changed = detail["before_positions"].fillna("").ne(
        detail["after_positions"].fillna(""))
    detail["cause_hint"] = "capacity_or_reporting_change"
    detail.loc[atz_changed, "cause_hint"] = "atz_phase_change"
    detail.loc[ruh_changed, "cause_hint"] = "ruh_state_change"
    detail.loc[positions_changed & before_present & after_present
               & ~atz_changed & ~ruh_changed, "cause_hint"] = "position_change"
    detail.loc[~before_present & after_present & ~atz_changed & ~ruh_changed,
               "cause_hint"] = "newly_reported_person"
    detail.loc[before_present & ~after_present & ~atz_changed & ~ruh_changed,
               "cause_hint"] = "person_no_longer_reported"
    for person, rows in detail.groupby("persnr", sort=False):
        affected = rows["residual_mak"].abs().gt(1e-6)
        if (affected.sum() > 1
                and abs(float(rows["residual_mak"].sum())) <= 1e-6
                and rows["after_positions"].isna().all()
                and rows["before_positions"].notna().any()
                and float(rows["event_mak"].sum()) < 0):
            detail.loc[rows.index[affected], "cause_hint"] = "cross_group_departure_allocation"
    return detail
