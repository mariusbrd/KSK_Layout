"""Isolated regular-only stock projection and stock/event diagnostics.

This is a read-only diagnostic. It does not modify event or stock semantics.
Person identifiers are retained in the returned detail for local inspection;
callers publishing a report should aggregate or anonymize them first.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from time import perf_counter
from typing import Callable, Sequence

from abgaenge.schemas import normalize_persnr
from utils.compact_special_position_events import PositionTransition
from utils.org_simulation_reconciliation import grouped_stock_metrics


def rebase_existing_atz_status(
    first_updated: pd.DataFrame,
    original_positions: pd.DataFrame,
    preapplied_people: pd.DataFrame,
    atz_status: pd.DataFrame,
) -> pd.DataFrame:
    """Reuse the year-invariant part of the product's existing-row update.

    `_update_existing_rows` uses its date only through the supplied ATZ map.
    This bridge starts from one complete product update and replays exactly
    its ATZ assignments for a later year. It must be parity-checked before
    use with a different product implementation.
    """
    out = first_updated.copy()
    original_ids = (original_positions["PersNr"].fillna("").astype(str).str.strip()
                    .str.replace(r"\.0$", "", regex=True))
    state = preapplied_people.copy()
    state["PersNr"] = (state["PersNr"].astype(str).str.strip()
                       .str.replace(r"\.0$", "", regex=True))
    active_ids = set(state.loc[state["active"] == True, "PersNr"])
    active_existing_ids = set(original_ids.loc[original_ids.ne("")]) & active_ids
    mask = original_ids.isin(active_existing_ids)
    if "ATZ_Status" in original_positions and mask.any():
        out.loc[mask, "ATZ_Status"] = "Kein ATZ"
    if "ist_atz_fr" in original_positions and mask.any():
        out.loc[mask, "ist_atz_fr"] = False
    atz_map = (atz_status.drop_duplicates(subset=["PersNr"], keep="last")
               .set_index("PersNr") if not atz_status.empty else pd.DataFrame())
    for person in sorted(active_existing_ids):
        rows = original_ids.eq(person)
        if person in atz_map.index:
            out.loc[rows, "ATZ_Status"] = atz_map.loc[person, "ATZ_Status"]
            out.loc[rows, "ist_atz_fr"] = bool(atz_map.loc[person, "ist_atz_fr"])
    return out


def atz_affected_position_mask(
    previous_input: pd.DataFrame,
    current_input: pd.DataFrame,
) -> pd.Series:
    """Return all original positions of people with a changed ATZ status."""
    changed = pd.Series(False, index=current_input.index)
    for column in ("ATZ_Status", "ist_atz_fr"):
        if column in current_input and column in previous_input:
            left = current_input[column].astype("string").fillna("<NA>")
            right = previous_input[column].astype("string").fillna("<NA>")
            equal = left.eq(right)
            changed |= ~equal
    if not changed.any():
        return changed
    people = current_input.loc[changed, "PersNr"].dropna().unique()
    return current_input["PersNr"].isin(people)


def update_stock_only_for_atz_changes(
    previous_input: pd.DataFrame,
    previous_stock: pd.DataFrame,
    current_input: pd.DataFrame,
    finalize_subset: Callable[[pd.DataFrame], pd.DataFrame],
) -> pd.DataFrame:
    """Recompute complete person groups whose ATZ status changed.

    This is an isolated stock-only probe. The caller must verify that all
    other pre-finalization input fields are unchanged between years and that
    the resulting stock frame matches a full product finalization exactly.
    """
    affected = atz_affected_position_mask(previous_input, current_input)
    if not affected.any():
        return previous_stock.copy()
    refreshed = finalize_subset(current_input.loc[affected].copy())
    out = previous_stock.copy()
    for column in refreshed.columns:
        if column != "Ausbildung":
            out.loc[affected, column] = refreshed[column]
    return out


def update_compact_for_atz_changes(
    previous_compact: pd.DataFrame,
    current_stock: pd.DataFrame,
    affected: pd.Series,
    prepare_subset: Callable[[pd.DataFrame], pd.DataFrame],
) -> pd.DataFrame:
    """Reuse row-local compact preparation for unchanged stock positions."""
    out = previous_compact.copy()
    if affected.any():
        refreshed = prepare_subset(current_stock.loc[affected].copy())
        out.loc[affected, refreshed.columns] = refreshed
    return out


def sync_original_position_oe(
    baseline: pd.DataFrame,
    original_positions: pd.DataFrame,
    year_end_people: pd.DataFrame,
) -> pd.DataFrame:
    """Apply the product's single-position person OE sync to original rows.

    The accessions engine can update an original holder's OE even when a
    different person is later reserved on that position. The fixed position
    snapshot inherits this OE from the original holder at each year end.
    """
    out = baseline.copy()
    original_ids = normalize_persnr(original_positions["PersNr"]).reset_index(drop=True)
    counts = original_ids.value_counts()
    state = year_end_people.copy()
    state["_person"] = normalize_persnr(state["PersNr"])
    state = state.drop_duplicates("_person", keep="last").set_index("_person")
    for index, person in original_ids.items():
        if pd.isna(person) or counts.get(person, 0) != 1 or person not in state.index:
            continue
        if not bool(state.at[person, "active"]):
            continue
        for column in ("Organisationseinheit", "OE-Cluster"):
            if column in out.columns and column in state.columns:
                out.at[out.index[index], column] = state.at[person, column]
    return out


def _grouped_stock_metrics_indexed(frame: pd.DataFrame, dimension: str) -> dict:
    """Probe the product's two-stage sum with indexed final groups.

    Person/group sums and first-person order remain pandas operations. Only
    the final iteration over group DataFrames is replaced by positional
    indexes into the already ordered capacity vector.
    """
    occupied = frame.loc[~frame.Is_Vacant].dropna(subset=["PersNr"])
    first = occupied.drop_duplicates(subset=[dimension, "PersNr"], keep="first")[[
        dimension, "PersNr"]]
    person_capacity = (
        occupied.groupby([dimension, "PersNr"], sort=True)["MAK_Reporting"]
        .sum().rename("_sum").reset_index()
    )
    ordered = first.merge(person_capacity, on=[dimension, "PersNr"],
                          how="left", sort=False)
    values = ordered["_sum"].fillna(0).to_numpy()
    populated = {name: (len(indices), float(np.sum(values[indices])))
                 for name, indices in ordered.groupby(dimension, sort=True).indices.items()}
    return {name: populated.get(name, (0, 0.0))
            for name in frame[dimension].dropna().unique()}


def _position_initial(baseline: pd.DataFrame, mapped_baseline: pd.DataFrame) -> pd.DataFrame:
    """Keep the product's position order and all stock replay input fields."""
    initial = baseline[["PersNr", "Is_Vacant", "MAK_Reporting",
                        "Organisationseinheit", "OE-Cluster"]].copy().reset_index(drop=True)
    initial["_report_excluded"] = baseline.get(
        "Is_Excluded", pd.Series(False, index=baseline.index)
    ).fillna(False).astype(bool).reset_index(drop=True)
    initial["Jobfamily"] = mapped_baseline["Jobfamily"].reset_index(drop=True)
    initial["JF-Cluster"] = mapped_baseline["JF-Cluster"].reset_index(drop=True)
    initial["_source_mak"] = pd.to_numeric(
        baseline.get("Personen_MAK_Source", pd.Series(index=baseline.index, dtype=float)),
        errors="coerce").reset_index(drop=True)
    initial["_initial_ruhend"] = baseline.get(
        "Status kundenindividuell", pd.Series("", index=baseline.index)
    ).astype(str).str.strip().eq("Ruhendes BeschÃ¤ftigungsverhÃ¤ltnis").reset_index(drop=True)
    initial["_initial_azubi"] = baseline.get(
        "Ist_Azubi", pd.Series(False, index=baseline.index)
    ).fillna(False).astype(bool).reset_index(drop=True)
    initial["_person"] = normalize_persnr(initial["PersNr"])
    return initial


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
    profile: dict[str, float] | None = None,
    exclude_ruhend: bool = True,
    grouping_method: str = "indexed",
    special_transitions: Sequence[PositionTransition] | None = None,
    position_observer: Callable[[pd.Timestamp, pd.DataFrame], None] | None = None,
    target_only: bool = False,
) -> dict[str, pd.DataFrame]:
    """Replay positions once for all four OE/JF stock dimensions.

    The default input remains regular-only. The isolated special-transitions
    input replays full fixed-plan accession reservations and training releases.
    """
    if special_transitions is not None and new_people:
        raise ValueError("Special transitions and regular new_people are mutually exclusive")
    if any(not str(person.get("PersNr", "")).startswith("NH_")
           for person in new_people):
        raise ValueError("Regular position stock projection accepts only NH_ hires")
    if grouping_method not in {"product", "indexed"}:
        raise ValueError("Unknown grouping method")
    group_metrics = (grouped_stock_metrics if grouping_method == "product"
                     else _grouped_stock_metrics_indexed)
    phase_start = perf_counter()
    dimensions = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster")
    initial = baseline[["PersNr", "Is_Vacant", "MAK_Reporting",
                        "Organisationseinheit", "OE-Cluster"]].copy().reset_index(drop=True)
    initial["_report_excluded"] = baseline.get(
        "Is_Excluded", pd.Series(False, index=baseline.index)
    ).fillna(False).astype(bool).reset_index(drop=True)
    initial["Jobfamily"] = mapped_baseline["Jobfamily"].reset_index(drop=True)
    initial["JF-Cluster"] = mapped_baseline["JF-Cluster"].reset_index(drop=True)
    initial["_source_mak"] = pd.to_numeric(
        baseline.get("Personen_MAK_Source", pd.Series(index=baseline.index, dtype=float)),
        errors="coerce").reset_index(drop=True)
    initial["_initial_ruhend"] = baseline.get(
        "Status kundenindividuell", pd.Series("", index=baseline.index)
    ).astype(str).str.strip().eq("Ruhendes Beschäftigungsverhältnis").reset_index(drop=True)
    initial["_initial_azubi"] = baseline.get(
        "Ist_Azubi", pd.Series(False, index=baseline.index)
    ).fillna(False).astype(bool).reset_index(drop=True)
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
    if profile is not None:
        profile["position_prepare"] = profile.get("position_prepare", 0.0) + perf_counter() - phase_start
    start, end = pd.Timestamp(base_date), pd.Timestamp(target_date)
    output: dict[str, list[dict]] = {dimension: [] for dimension in dimensions}
    frame = initial.copy()
    ruhend_people: set[str] = set()
    event_cursor = 0
    special_cursor = 0
    azubi_report_positions: set[int] = set()
    ordered_special = tuple(special_transitions or ())
    if any(ordered_special[i].date > ordered_special[i + 1].date
           for i in range(len(ordered_special) - 1)):
        raise ValueError("Special position transitions must be date ordered")
    for year in range(start.year, end.year + 1):
        date = min(pd.Timestamp(year, 12, 31), end)
        if date <= start:
            continue
        phase_start = perf_counter()
        while event_cursor < len(event_rows) and event_rows[event_cursor]["_day"] <= date:
            event = event_rows[event_cursor]
            event_cursor += 1
            indices = person_rows.get(event["_person"])
            if indices is None or len(indices) == 0:
                continue
            indices = list(indices)
            single_index = indices[0] if len(indices) == 1 else None
            reason = str(event.get("reason_code", "") or "")
            if reason == "RUHEND_START":
                ruhend_people.add(event["_person"])
            elif reason == "RUHEND_RETURN":
                ruhend_people.discard(event["_person"])
            if int(event.get("headcount_change", 0) or 0) < 0:
                if single_index is not None:
                    frame.at[single_index, "PersNr"] = pd.NA
                    frame.at[single_index, "Is_Vacant"] = True
                    frame.at[single_index, "MAK_Reporting"] = 0.0
                else:
                    frame.loc[indices, "PersNr"] = pd.NA
                    frame.loc[indices, "Is_Vacant"] = True
                    frame.loc[indices, "MAK_Reporting"] = 0.0
                continue
            mak_delta = float(event.get("mak_change", 0.0) or 0.0)
            if (reason == "RUHEND_RETURN" and mak_delta == 0.0 and len(indices) > 1
                    and initial.loc[indices, "_initial_ruhend"].any()
                    and initial.loc[indices, "_source_mak"].fillna(0.0).gt(0).any()):
                raise ValueError(
                    "Dormant return across multiple original positions requires product parity")
            if mak_delta:
                if single_index is not None:
                    old_value = pd.to_numeric(frame.at[single_index, "MAK_Reporting"],
                                              errors="coerce")
                    old_value = 0.0 if pd.isna(old_value) else float(old_value)
                    frame.at[single_index, "MAK_Reporting"] = max(0.0, old_value + mak_delta)
                else:
                    old = pd.to_numeric(frame.loc[indices, "MAK_Reporting"],
                                        errors="coerce").fillna(0.0)
                    total = float(old.sum())
                    weights = old / total if total > 0 else pd.Series(
                        1.0 / len(indices), index=old.index)
                    frame.loc[indices, "MAK_Reporting"] = (
                        old + mak_delta * weights).clip(lower=0.0).to_numpy()
            elif (reason == "RUHEND_RETURN" and len(indices) == 1
                  and bool(initial.at[indices[0], "_initial_ruhend"])
                  and not bool(initial.at[indices[0], "_initial_azubi"])
                  and float(frame.at[indices[0], "MAK_Reporting"]) == 0.0
                  and pd.notna(initial.at[indices[0], "_source_mak"])):
                # Existing dormant people can have zero reported MAK while
                # retaining a positive source capacity. Their return event
                # carries zero delta; product reporting restores the source.
                frame.at[indices[0], "MAK_Reporting"] = float(initial.at[indices[0], "_source_mak"])
            if reason == "RUHEND_START":
                if single_index is not None:
                    frame.at[single_index, "MAK_Reporting"] = 0.0
                else:
                    frame.loc[indices, "MAK_Reporting"] = 0.0
        if profile is not None:
            profile["position_departures"] = profile.get("position_departures", 0.0) + perf_counter() - phase_start
        phase_start = perf_counter()
        while (special_cursor < len(ordered_special)
               and ordered_special[special_cursor].date.normalize() <= date):
            transition = ordered_special[special_cursor]
            special_cursor += 1
            index = transition.position_index
            if transition.action == "release":
                azubi_report_positions.discard(index)
                frame.at[index, "PersNr"] = pd.NA
                frame.at[index, "Is_Vacant"] = True
                frame.at[index, "MAK_Reporting"] = 0.0
            elif transition.action == "reserve":
                if (transition.event_type == "Azubi_Hire"
                        and bool(initial.at[index, "_report_excluded"])):
                    azubi_report_positions.add(index)
                else:
                    azubi_report_positions.discard(index)
                frame.at[index, "PersNr"] = transition.person
                frame.at[index, "Is_Vacant"] = False
                frame.at[index, "MAK_Reporting"] = transition.mak
            elif transition.action == "relabel":
                if transition.mak is not None:
                    frame.at[index, "MAK_Reporting"] = transition.mak
                if transition.org_unit is not None and pd.notna(transition.org_unit):
                    frame.at[index, "Organisationseinheit"] = transition.org_unit
                if transition.oe_cluster is not None and pd.notna(transition.oe_cluster):
                    frame.at[index, "OE-Cluster"] = transition.oe_cluster
            else:
                raise ValueError(f"Unknown position transition: {transition.action}")
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
        if target_only and date != end:
            if profile is not None:
                profile["position_hires"] = (
                    profile.get("position_hires", 0.0) + perf_counter() - phase_start)
            continue
        # The product excludes a person while their employment is dormant.
        # Mask only the grouped view; preserve the position state for later years.
        grouped_frame = (frame.copy() if azubi_report_positions or
                         (exclude_ruhend and ruhend_people) else frame)

        if azubi_report_positions:
            grouped_frame.loc[list(azubi_report_positions), "PersNr"] = pd.NA
        if exclude_ruhend:
            for person in ruhend_people:
                indices = person_rows.get(person)
                if indices is not None:
                    grouped_frame.loc[list(indices), "PersNr"] = pd.NA
        if position_observer is not None:
            position_observer(date, grouped_frame.copy(deep=True))
        if profile is not None:
            profile["position_hires"] = profile.get("position_hires", 0.0) + perf_counter() - phase_start
        for dimension in dimensions:
            phase_start = perf_counter()
            output[dimension].extend(
                {"Stichtag": date, dimension: group,
                 "Köpfe": heads, "MAK": mak}
                for group, (heads, mak) in group_metrics(grouped_frame, dimension).items())
            if profile is not None:
                key = f"position_group_{dimension}"
                profile[key] = profile.get(key, 0.0) + perf_counter() - phase_start
    return {dimension: pd.DataFrame(
        output[dimension], columns=["Stichtag", dimension, "Köpfe", "MAK"])
        for dimension in dimensions}


def project_special_annual_position_stocks(
    annual_baselines: Sequence[tuple[pd.Timestamp, pd.DataFrame, pd.DataFrame]],
    special_transitions: Sequence[PositionTransition],
    *,
    grouping_method: str = "indexed",
) -> dict[str, pd.DataFrame]:
    """Project all supplied year ends with one chronological transition pass.

    Each annual baseline already includes the product's preapplied departures,
    ATZ state, exclusions, and original-holder OE sync for that year. Only
    accession position transitions are carried forward here.
    """
    if grouping_method not in {"product", "indexed"}:
        raise ValueError("Unknown grouping method")
    dimensions = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster")
    group_metrics = (grouped_stock_metrics if grouping_method == "product"
                     else _grouped_stock_metrics_indexed)
    ordered = tuple(special_transitions)
    if any(ordered[i].date > ordered[i + 1].date for i in range(len(ordered) - 1)):
        raise ValueError("Special position transitions must be date ordered")
    output: dict[str, list[dict]] = {dimension: [] for dimension in dimensions}
    latest_occupancy: dict[int, PositionTransition] = {}
    latest_labels: dict[int, PositionTransition] = {}
    cursor = 0
    last_date = None
    expected_size = None
    for date, baseline, mapped in annual_baselines:
        date = pd.Timestamp(date)
        if last_date is not None and date <= last_date:
            raise ValueError("Annual baselines must have increasing dates")
        last_date = date
        frame = _position_initial(baseline, mapped)
        if expected_size is None:
            expected_size = len(frame)
        elif len(frame) != expected_size:
            raise ValueError("Fixed staffing plan row count changed")
        while cursor < len(ordered) and ordered[cursor].date.normalize() <= date:
            transition = ordered[cursor]
            cursor += 1
            if transition.action in {"release", "reserve"}:
                latest_occupancy[transition.position_index] = transition
            elif transition.action == "relabel":
                latest_labels[transition.position_index] = transition
            else:
                raise ValueError(f"Unknown position transition: {transition.action}")
        azubi_report_positions = []
        for index, transition in latest_occupancy.items():
            if transition.action == "release":
                frame.at[index, "PersNr"] = pd.NA
                frame.at[index, "Is_Vacant"] = True
                frame.at[index, "MAK_Reporting"] = 0.0
            else:
                frame.at[index, "PersNr"] = transition.person
                frame.at[index, "Is_Vacant"] = False
                frame.at[index, "MAK_Reporting"] = transition.mak
                if (transition.event_type == "Azubi_Hire"
                        and bool(frame.at[index, "_report_excluded"])):
                    azubi_report_positions.append(index)
        for index, transition in latest_labels.items():
            if transition.mak is not None:
                frame.at[index, "MAK_Reporting"] = transition.mak
            if transition.org_unit is not None and pd.notna(transition.org_unit):
                frame.at[index, "Organisationseinheit"] = transition.org_unit
            if transition.oe_cluster is not None and pd.notna(transition.oe_cluster):
                frame.at[index, "OE-Cluster"] = transition.oe_cluster
        if azubi_report_positions:
            frame.loc[azubi_report_positions, "PersNr"] = pd.NA
        for dimension in dimensions:
            output[dimension].extend(
                {"Stichtag": date, dimension: group,
                 "K\u00f6pfe": heads, "MAK": mak}
                for group, (heads, mak) in group_metrics(frame, dimension).items())
    return {dimension: pd.DataFrame(
        output[dimension], columns=["Stichtag", dimension, "K\u00f6pfe", "MAK"])
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
