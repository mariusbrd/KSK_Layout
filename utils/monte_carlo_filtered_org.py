"""Exact per-seed OE target aggregates for arbitrary sidebar selections.

The saved snapshots are app-only output. The frozen single-run packet and the
single-run Streamlit path are unaffected.
"""
from __future__ import annotations

import pickle
import zlib
from collections.abc import Mapping
from numbers import Real

import pandas as pd

from components.sidebar import _apply_filters_uncached
from dataloader.kpi_engine import (
    compute_atz_kpis, compute_fte_roh, compute_teilzeit_kpis,
    get_unique_employees,
)
from utils.monte_carlo_ensemble import _statistics


DIMENSIONS = ("Organisationseinheit", "Geschlecht", "Alterskohorte",
              "Beschäftigungsstatus", "TrfGr")
METRICS = ("heads", "mak", "eur")
SNAPSHOT_COLUMNS = (
    "PersNr", "Personalnummer", "Is_Vacant", "Organisationseinheit", "OE-Cluster",
    "Jobfamily", "JF-Cluster", "Alterskohorte", "Geschlecht", "Arbeitszeit",
    "Ausbildung", "ATZ_Status", "Beschäftigungsstatus", "TrfGr",
    "MAK_Reporting", "MAK_Calculated", "mak", "MAK", "FTE_assigned",
    "EUR_Reporting", "Total_Cost_Year",
    "BsGrd", "Personen_MAK", "Status kundenindividuell", "ist_atz_fr",
)


def decode_snapshot(blob: bytes) -> pd.DataFrame:
    if not isinstance(blob, bytes):
        raise ValueError("Ungültiger OE-Laufdatensatz")
    result = pickle.loads(zlib.decompress(blob))
    if not isinstance(result, pd.DataFrame):
        raise ValueError("Ungültiger OE-Laufdatensatz")
    return result


def _event_org_labels(series: pd.Series) -> pd.Series:
    """Match the OE page's numeric event-key normalization exactly."""
    return series.map(
        lambda value: str(int(value)) if isinstance(value, Real) and pd.notna(value)
        and float(value).is_integer() else value).fillna(
            "Nicht zugeordnet").astype(str).replace(
                {"": "Nicht zugeordnet", "UNMAPPED": "Nicht zugeordnet",
                 "Unmapped": "Nicht zugeordnet", "Unclustered": "Nicht zugeordnet"})


def _metric_columns(frame: pd.DataFrame) -> tuple[str, str]:
    mak = next((name for name in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK", "FTE_assigned")
                if name in frame), None)
    eur = next((name for name in ("EUR_Reporting", "Total_Cost_Year") if name in frame), None)
    if mak is None or eur is None:
        raise ValueError("OE-Laufdatensatz enthält nicht alle Kennzahlen")
    return mak, eur


def _per_seed_values(frame: pd.DataFrame, active_filters: Mapping | None) -> dict[tuple, float]:
    # Streamlit's cached wrapper would retain one full DataFrame per seed and
    # filter choice; the pure filter function has identical semantics here.
    filtered = _apply_filters_uncached(frame, dict(active_filters or {}))
    if "Is_Vacant" in filtered:
        filtered = filtered.loc[filtered["Is_Vacant"] != True]
    mak_col, eur_col = _metric_columns(filtered)
    person_col = next((name for name in ("PersNr", "Personalnummer") if name in filtered), None)
    result = {}
    dimensions = (None, *[name for name in DIMENSIONS if name in filtered])
    for dimension in dimensions:
        groups = [("__gesamt__", filtered)] if dimension is None else filtered.groupby(dimension, dropna=False, observed=True)
        for group, rows in groups:
            label = str(group)
            result[(dimension or "__gesamt__", label, "heads")] = float(
                rows[person_col].nunique() if person_col else len(rows))
            result[(dimension or "__gesamt__", label, "mak")] = float(
                pd.to_numeric(rows[mak_col], errors="coerce").fillna(0).sum())
            result[(dimension or "__gesamt__", label, "eur")] = float(
                pd.to_numeric(rows[eur_col], errors="coerce").fillna(0).sum())
    return result


def filtered_org_statistics(ensemble: Mapping, active_filters: Mapping | None) -> dict[tuple, dict]:
    """Filter each real run first, then calculate mean and empirical band."""
    blobs = ensemble.get("org_filter_snapshots")
    if (ensemble.get("org_filter_snapshot_version") != 1
            or not isinstance(blobs, list) or len(blobs) != ensemble.get("count")):
        raise ValueError("OE-Laufdaten für gefilterte Monte-Carlo-Sicht fehlen")
    per_seed = [_per_seed_values(decode_snapshot(blob), active_filters) for blob in blobs]
    keys = set().union(*(set(values) for values in per_seed))
    return {key: _statistics([values.get(key, 0.0) for values in per_seed],
                             ensemble["band_level"]) for key in keys}


def filtered_org_kpi_statistics(ensemble: Mapping, active_filters: Mapping | None,
                                compact) -> dict:
    """Recompute every OE KPI operand with the original functions in each run."""
    blobs = ensemble.get("org_filter_snapshots")
    if (ensemble.get("org_filter_snapshot_version") != 1
            or not isinstance(blobs, list) or len(blobs) != ensemble.get("count")):
        raise ValueError("OE-Laufdaten für KPI-Mittelwerte fehlen")
    per_seed = []
    org_totals = []
    for blob in blobs:
        filtered = _apply_filters_uncached(decode_snapshot(blob), dict(active_filters or {}))
        org = filtered.get("Organisationseinheit", pd.Series("Nicht zugeordnet", index=filtered.index))
        org = org.fillna("Nicht zugeordnet").astype(str).str.strip()
        org = org.replace({"": "Nicht zugeordnet", "UNMAPPED": "Nicht zugeordnet",
                           "Unmapped": "Nicht zugeordnet", "Unclustered": "Nicht zugeordnet"})
        filtered = filtered.assign(Organisationseinheit=org)
        mapped = filtered.loc[org.ne("Nicht zugeordnet")]
        unmapped = filtered.loc[org.eq("Nicht zugeordnet")]
        occupied = mapped.loc[mapped["Is_Vacant"] != True] if "Is_Vacant" in mapped else mapped
        unique = get_unique_employees(occupied)
        heads = compact.get_ist_koepfe(occupied)
        mak = compact.get_ist_mak(occupied)
        eur = compact.get_ist_eur(occupied)
        raw = compute_fte_roh(occupied)
        atz = compute_atz_kpis(occupied)
        part_time = compute_teilzeit_kpis(occupied)
        female = int(unique["Geschlecht"].eq("w").sum()) if "Geschlecht" in unique else 0
        total_eur = compact.get_ist_eur(filtered)
        unmapped_eur = compact.get_ist_eur(unmapped)
        per_seed.append({
            "heads": heads, "mak": mak, "eur": eur, "raw_mak": raw,
            "female": female, "female_share": female / heads if heads else 0.0,
            "atz": atz["gesamt"], "atz_share_pct": atz["quote_headcount_pct"],
            "part_time": part_time["count"], "part_time_share_pct": part_time["quote_pct"],
            "average_mak": mak / heads if heads else 0.0,
            "eur_per_head": eur / heads if heads else 0.0,
            "eur_per_mak": eur / mak if mak else 0.0,
            "mapped_eur_share": (total_eur - unmapped_eur) / total_eur if total_eur else 0.0,
            "unmapped_eur": unmapped_eur,
        })
        id_col = "PersNr" if "PersNr" in occupied else "Personalnummer"
        org_totals.append({
            "heads": occupied.groupby("Organisationseinheit", observed=True)[id_col].nunique().to_dict(),
            "mak": occupied.groupby("Organisationseinheit", observed=True)[
                "MAK_Reporting" if "MAK_Reporting" in occupied else "MAK_Calculated"].sum().to_dict(),
            "eur": occupied.groupby("Organisationseinheit", observed=True)[
                "EUR_Reporting" if "EUR_Reporting" in occupied else "Total_Cost_Year"].sum().to_dict(),
        })
    result = {key: _statistics([seed[key] for seed in per_seed], ensemble["band_level"])
              for key in per_seed[0]}
    orgs = set().union(*(set(seed["heads"]) for seed in org_totals))
    result["organizations"] = {
        metric: {org: _statistics([seed[metric].get(org, 0.0) for seed in org_totals],
                                  ensemble["band_level"])
                 for org in orgs}
        for metric in ("heads", "mak", "eur")}
    result["seed_zero"] = per_seed[0]
    return result


def filtered_org_rank_statistics(ensemble: Mapping, active_filters: Mapping | None) -> dict:
    """OE ranking and departure drivers, filtered separately in every seed."""
    targets = ensemble.get("org_filter_snapshots")
    events = ensemble.get("org_departure_events")
    count = ensemble.get("count")
    if (ensemble.get("org_filter_snapshot_version") != 1
            or ensemble.get("org_departure_events_version") != 1
            or not isinstance(targets, list) or not isinstance(events, list)
            or len(targets) != count or len(events) != count):
        raise ValueError("OE-Laufdaten für Rangliste und Abgänge fehlen")
    per_seed = []
    for target_blob, event_blob in zip(targets, events):
        target = _apply_filters_uncached(decode_snapshot(target_blob), dict(active_filters or {}))
        if "Is_Vacant" in target:
            target = target.loc[target["Is_Vacant"] != True]
        target = target.copy()
        if "Organisationseinheit" in target:
            target["Organisationseinheit"] = target["Organisationseinheit"].fillna(
                "Nicht zugeordnet").astype(str).replace(
                    {"": "Nicht zugeordnet", "UNMAPPED": "Nicht zugeordnet",
                     "Unmapped": "Nicht zugeordnet", "Unclustered": "Nicht zugeordnet"})
        else:
            target["Organisationseinheit"] = "Nicht zugeordnet"
        person_col = "PersNr" if "PersNr" in target else "Personalnummer"
        mak_col, eur_col = _metric_columns(target)
        grouped = target.groupby("Organisationseinheit", observed=True)
        values = {
            "heads": grouped[person_col].nunique().to_dict(),
            "mak": grouped[mak_col].sum().to_dict(),
            "eur": grouped[eur_col].sum().to_dict(),
        }
        event = _apply_filters_uncached(decode_snapshot(event_blob), dict(active_filters or {}))
        event = event.copy()
        if not event.empty and "headcount_change" in event:
            event = event.loc[pd.to_numeric(event["headcount_change"], errors="coerce") < 0]
        if not event.empty:
            event["Organisationseinheit"] = _event_org_labels(event["Organisationseinheit"])
            event["_departures"] = pd.to_numeric(
                event["headcount_change"], errors="coerce").fillna(0).abs()
            event["_mak_loss"] = pd.to_numeric(
                event["mak_change"], errors="coerce").fillna(0).abs()
            event_groups = event.groupby("Organisationseinheit", observed=True)
            values["departures"] = event_groups["_departures"].sum().to_dict()
            values["mak_loss"] = event_groups["_mak_loss"].sum().round(2).to_dict()
        else:
            values["departures"] = {}
            values["mak_loss"] = {}
        per_seed.append(values)
    result = {"count": count, "band_level": ensemble["band_level"]}
    for metric in ("heads", "mak", "eur", "departures", "mak_loss"):
        groups = set().union(*(set(seed[metric]) for seed in per_seed))
        result[metric] = {
            group: {**_statistics(samples := [float(seed[metric].get(group, 0.0)) for seed in per_seed],
                                  ensemble["band_level"]),
                    "samples": samples,
                    "seed_zero": float(per_seed[0][metric].get(group, 0.0))}
            for group in groups}
    return result


def filtered_org_departure_reasons(ensemble: Mapping, active_filters: Mapping | None) -> pd.DataFrame:
    """Mean and bands for the departure-reason table next to the IST comparison."""
    events = ensemble.get("org_departure_events")
    count = ensemble.get("count")
    if (ensemble.get("org_departure_events_version") != 1
            or not isinstance(events, list) or len(events) != count):
        raise ValueError("OE-Laufdaten für Abgangsgründe fehlen")
    runs = []
    reason_name = None
    for blob in events:
        event = _apply_filters_uncached(decode_snapshot(blob), dict(active_filters or {}))
        if event.empty:
            runs.append({})
            continue
        event = event.loc[pd.to_numeric(event["headcount_change"], errors="coerce") < 0].copy()
        reason_name = "reason_label" if "reason_label" in event else "reason_code"
        if event.empty or reason_name not in event:
            runs.append({})
            continue
        event["Organisationseinheit"] = _event_org_labels(event["Organisationseinheit"])
        event["_departures"] = pd.to_numeric(event["headcount_change"], errors="coerce").abs()
        event["_mak_loss"] = pd.to_numeric(event["mak_change"], errors="coerce").abs()
        grouped = event.groupby(["Organisationseinheit", reason_name], dropna=False).agg(
            departures=("_departures", "sum"), people=("persnr", "nunique"),
            mak_loss=("_mak_loss", "sum"))
        runs.append({key: (float(row.departures), float(row.people), round(float(row.mak_loss), 2))
                     for key, row in grouped.iterrows()})
    if reason_name is None:
        return pd.DataFrame()
    keys = sorted(set().union(*(set(run) for run in runs)), key=lambda key: (str(key[0]), str(key[1])))
    rows = []
    for org, reason in keys:
        row = {"Organisationseinheit": org, reason_name: reason}
        for metric, position in (("Abgänge", 0), ("Personen", 1), ("MAK-Verlust", 2)):
            stats = _statistics([run.get((org, reason), (0.0, 0.0, 0.0))[position]
                                 for run in runs], ensemble["band_level"])
            row[metric] = stats["mean"]
            row[f"{metric} P10"] = stats["lower"]
            row[f"{metric} P90"] = stats["upper"]
        rows.append(row)
    return pd.DataFrame(rows)


def filtered_org_composition_statistics(
    ensemble: Mapping, active_filters: Mapping | None, display_orgs: list[str],
    metric_view: str, dimensions: tuple[str, ...],
) -> dict[str, dict[str, pd.DataFrame]]:
    """Group each real target run like the OE composition charts, then band it."""
    blobs = ensemble.get("org_filter_snapshots")
    count = ensemble.get("count")
    if (ensemble.get("org_filter_snapshot_version") != 1
            or not isinstance(blobs, list) or len(blobs) != count):
        raise ValueError("OE-Laufdaten für Zusammensetzung fehlen")
    if metric_view not in {"Köpfe", "MAK", "EUR"}:
        raise ValueError("Unbekannte OE-Kennzahl")
    per_dimension = {dimension: [] for dimension in dimensions}
    for blob in blobs:
        frame = _apply_filters_uncached(decode_snapshot(blob), dict(active_filters or {}))
        if "Is_Vacant" in frame:
            frame = frame.loc[frame["Is_Vacant"] != True]
        frame = frame.copy()
        frame["Organisationseinheit"] = frame["Organisationseinheit"].fillna(
            "Nicht zugeordnet").astype(str).replace(
                {"": "Nicht zugeordnet", "UNMAPPED": "Nicht zugeordnet",
                 "Unmapped": "Nicht zugeordnet", "Unclustered": "Nicht zugeordnet"})
        frame = frame.loc[frame["Organisationseinheit"].isin(display_orgs)]
        if metric_view == "Köpfe":
            value_col = "PersNr" if "PersNr" in frame else None
        elif metric_view == "MAK":
            value_col = next((name for name in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK", "FTE_assigned")
                              if name in frame), None)
        else:
            value_col = next((name for name in ("EUR_Reporting", "Total_Cost_Year")
                              if name in frame), None)
        if value_col is None and metric_view != "Köpfe":
            raise ValueError("Kennzahl im OE-Laufdatensatz fehlt")
        for dimension in dimensions:
            if dimension not in frame:
                raise ValueError(f"OE-Merkmal {dimension} fehlt")
            work = frame.assign(**{dimension: frame[dimension].fillna("(unbekannt)").astype(str)})
            grouped = work.groupby(["Organisationseinheit", dimension], observed=True)
            if metric_view == "Köpfe":
                values = (grouped[value_col].nunique() if value_col else grouped.size())
            else:
                values = grouped[value_col].sum()
            per_dimension[dimension].append(values.to_dict())
    result = {}
    for dimension, runs in per_dimension.items():
        keys = sorted(set().union(*(set(run) for run in runs)),
                      key=lambda key: (display_orgs.index(key[0]), str(key[1])))
        segments = []
        for org, group in keys:
            samples = [float(run.get((org, group), 0.0)) for run in runs]
            stats = _statistics(samples, ensemble["band_level"])
            segments.append({"Organisationseinheit": org, dimension: group,
                             "Wert": stats["mean"], "P10": stats["lower"],
                             "P90": stats["upper"], "Seed 0": samples[0],
                             "samples": samples})
        segment_df = pd.DataFrame(segments, columns=[
            "Organisationseinheit", dimension, "Wert", "P10", "P90", "Seed 0", "samples"])
        totals = []
        for org in display_orgs:
            total_samples = [sum(row["samples"][index] for row in segments
                                 if row["Organisationseinheit"] == org)
                             for index in range(count)]
            stats = _statistics(total_samples, ensemble["band_level"])
            totals.append({"Organisationseinheit": org, "Wert": stats["mean"],
                           "P10": stats["lower"], "P90": stats["upper"],
                           "Seed 0": total_samples[0]})
        result[dimension] = {"segments": segment_df, "totals": pd.DataFrame(totals)}
    return result
