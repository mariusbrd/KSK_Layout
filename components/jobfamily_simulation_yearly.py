"""Read-only yearly job-family views of one saved CompactPlus run.

This module never invokes a forecast and never mutates the OE event summary.
"""
from __future__ import annotations

import hashlib
from io import BytesIO

import pandas as pd
import streamlit as st

from components.ui_compat import lazy_excel_download_button_compat
from components.org_simulation_yearly import LABELS, yearly_periods, render_stacked_event_chart
from dataloader.jobfamily_service import normalize_jobfamily_series
from utils.org_simulation_events import DEPARTURES, METRICS, normalize_simulation_events
from utils.org_simulation_reconciliation import (
    grouped_stock_metrics, reporting_departure_events, reporting_projection_base,
)
from abgaenge.schemas import normalize_persnr
from dataloader.kpi_engine import compute_headcount
from components.sidebar import get_active_view_filters
from utils.monte_carlo_filtered_annual_positions import filtered_annual_position_samples
from utils.monte_carlo_annual_event_rows import VERSION as ANNUAL_EVENTS_VERSION, decode_annual_event_rows
from utils.monte_carlo_annual_positions import VERSION as ANNUAL_POSITIONS_VERSION, decode_annual_positions
from utils.monte_carlo_filtered_annual_events import _samples as oe_event_samples
from utils.monte_carlo_ensemble import _statistics
from components.sidebar import _apply_filters_uncached
from utils.jobfamily_yearly_stock_export import build_jobfamily_stock_export_bytes


DIMENSIONS = ("Jobfamily", "JF-Cluster")
UNASSIGNED = "UNMAPPED"
YEARLY_VIEW_CACHE_VERSION = 2


def _dimension_values(frame: pd.DataFrame, dimension: str) -> pd.Series:
    if dimension == "Jobfamily":
        return normalize_jobfamily_series(frame.get("Jobfamily", pd.Series(index=frame.index, dtype=object)))
    values = frame.get("JF-Cluster", pd.Series(index=frame.index, dtype=object))
    return values.fillna(UNASSIGNED).astype(str).str.strip().replace("", UNASSIGNED)


def _baseline_assignments(baseline: pd.DataFrame, dimension: str) -> dict[str, str]:
    occupied = baseline.loc[~baseline.get("Is_Vacant", pd.Series(False, index=baseline.index)).fillna(False).astype(bool)].copy()
    occupied["_person"] = normalize_persnr(occupied["PersNr"])
    occupied["_group"] = _dimension_values(occupied, dimension)
    groups = occupied.dropna(subset=["_person"]).groupby("_person")["_group"].agg(lambda x: set(x))
    return {person: next(iter(values)) for person, values in groups.items() if len(values) == 1}


def _allocate_original_events_to_groups(
    events: pd.DataFrame, baseline: pd.DataFrame, dimension: str, *, members=None,
) -> pd.DataFrame:
    """Split a person's capacity event across their original position groups.

    Group headcounts are intentionally not additive when one person occupies
    positions in more than one group. The company event remains unchanged.
    """
    if events.empty or "persnr" not in events:
        return events.copy()
    members = _original_group_members(baseline, dimension) if members is None else members
    if not members:
        return events.copy()
    output = []
    persons = normalize_persnr(events["persnr"])
    for index, event in events.iterrows():
        person = persons.loc[index]
        origin = event.get("Ereignisquelle") == "departure_events" or event.get("Ereignisart") == "Azubi_Conversion_Out"
        groups = members.get(person) if origin else None
        if not groups:
            output.append(event.copy())
            continue
        for group, ratio in groups:
            row = event.copy()
            row[dimension] = group
            row["MAK-Veränderung"] = float(event["MAK-Veränderung"]) * ratio
            # One employee disappears from each group they occupied. These
            # group headcounts must not be summed as company headcounts.
            output.append(row)
    return pd.DataFrame(output, columns=events.columns).reset_index(drop=True)


def _original_group_members(baseline: pd.DataFrame, dimension: str) -> dict:
    occupied = baseline.loc[
        ~baseline.get("Is_Vacant", pd.Series(False, index=baseline.index)).fillna(False).astype(bool)
    ].copy()
    occupied["_person"] = normalize_persnr(occupied["PersNr"])
    occupied["_group"] = _dimension_values(occupied, dimension)
    capacity_col = next((c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in occupied), None)
    occupied["_capacity"] = pd.to_numeric(occupied[capacity_col], errors="coerce").fillna(0.0) if capacity_col else 0.0
    weight_col = next((c for c in ("Allocation_Weight", "Soll_FTE") if c in occupied), None)
    occupied["_weight"] = pd.to_numeric(occupied[weight_col], errors="coerce").fillna(0.0) if weight_col else 1.0
    grouped = occupied.groupby(["_person", "_group"], dropna=False).agg(
        capacity=("_capacity", "sum"), weight=("_weight", "sum"), rows=("_person", "size")
    )
    members: dict[str, list[tuple[str, float]]] = {}
    for person, part in grouped.groupby(level=0):
        if len(part) <= 1:
            continue
        weights = part["capacity"]
        if float(weights.sum()) <= 1e-12:
            weights = part["weight"]
        if float(weights.sum()) <= 1e-12:
            weights = part["rows"].astype(float)
        members[person] = [(str(group), float(value / weights.sum()))
                           for (_, group), value in weights.items()]
    return members


def prepare_jobfamily_baseline(baseline: pd.DataFrame) -> dict:
    """Prepare only assignments that cannot change between ensemble seeds."""
    return {dimension: {"assignments": _baseline_assignments(baseline, dimension),
                        "members": _original_group_members(baseline, dimension)}
            for dimension in DIMENSIONS}


def build_jobfamily_yearly_view(context: dict, summary: dict, *, dimension: str,
                                include_stock: bool = True, baseline_cache: dict | None = None,
                                projection_cache: dict | None = None) -> dict:
    """Aggregate saved events; certify departure-only stock slices separately."""
    if dimension not in DIMENSIONS:
        raise ValueError("Ungültige Jobgruppenebene.")
    signature = context.get("run_signature")
    if not signature or summary.get("run_signature") != signature:
        raise ValueError("Ereignisse und Simulation gehören zu unterschiedlichen Läufen.")
    baseline, final = context.get("baseline_snapshot"), context.get("final_snapshot")
    if baseline is None or final is None:
        raise ValueError("Gespeicherter Ausgangs- oder Zielbestand fehlt.")
    cached = baseline_cache.get(dimension) if baseline_cache is not None else None
    assignments = cached["assignments"] if cached is not None else _baseline_assignments(baseline, dimension)
    members = cached["members"] if cached is not None else None
    final_assignments = _baseline_assignments(final, dimension)
    events = summary["events"].copy(deep=True)
    if not events.empty:
        source = events.get(dimension, pd.Series(index=events.index, dtype=object))
        source = source.astype("string").str.strip()
        missing = source.isna() | source.eq("") | source.isin(("nan", "None", "UNMAPPED"))
        persons = normalize_persnr(events.get("persnr", pd.Series(index=events.index, dtype=object)))
        fallback = persons.map(assignments).astype("string")
        # Departures and conversion-out use the original assignment. New
        # accessions and conversion-in use the saved final person assignment.
        departure = events["Ereignisquelle"].eq("departure_events")
        conversion_out = events["Ereignisart"].eq("Azubi_Conversion_Out")
        origin = departure | conversion_out
        # The forecast employee row may carry a pre-cluster Jobfamily while
        # both saved stock snapshots carry the run's mapped Jobfamily. The
        # saved person assignment is authoritative for original employees.
        source = source.where(~(origin & fallback.notna()), fallback)
        source = source.where(~(missing & ~origin), persons.map(final_assignments).astype("string"))
        if dimension == "JF-Cluster" and "Jobfamily" in events:
            mapping_rows = pd.concat([baseline[["Jobfamily", "JF-Cluster"]], final[["Jobfamily", "JF-Cluster"]]], ignore_index=True)
            mapping_rows = mapping_rows.dropna().drop_duplicates()
            unique_names = mapping_rows.groupby("Jobfamily")["JF-Cluster"].agg(lambda x: set(x))
            name_map = {name: next(iter(values)) for name, values in unique_names.items() if len(values) == 1}
            missing = source.isna() | source.eq("") | source.eq(UNASSIGNED)
            source = source.where(~missing, events["Jobfamily"].map(name_map).astype("string"))
        events[dimension] = source.fillna(UNASSIGNED).replace("", UNASSIGNED)
        person_events = events.copy(deep=True)
        events = _allocate_original_events_to_groups(person_events, baseline, dimension, members=members)
        events["Zuordnung_geklärt"] = events[dimension].ne(UNASSIGNED)
        annual = events.groupby(["Jahr", dimension, "Kategorie", "Ereignisart"], dropna=False)[list(METRICS)].sum().reset_index()
    else:
        events[dimension] = pd.Series(dtype=str)
        annual = pd.DataFrame(columns=["Jahr", dimension, "Kategorie", "Ereignisart", *METRICS])

    stock = pd.DataFrame()
    stock_checks = pd.DataFrame()
    reported = pd.DataFrame()
    stock_reason = "Bestandsverlauf nur bei ausdrücklich deaktivierten Zugängen verfügbar."
    oe_bridge = summary.get("stock_reconciliation", {})
    if not include_stock:
        stock_reason = "Bestandsverlauf im Zusatzlauf nicht berechnet."
    params = context.get("zugaenge_params")
    no_entries = isinstance(params, dict) and all(params.get(k, {}).get("active") is False for k in ("azubi", "trainee", "new_hires"))
    if include_stock and no_entries and oe_bridge.get("status") == "Nicht verfügbar":
        stock_reason = "Bestandsverlauf für diesen Lauf nicht verfügbar: " + str(oe_bridge.get("reason") or "OE-Bestandsüberleitung nicht verfügbar")
    elif include_stock and no_entries and (events.empty or events["Ereignisquelle"].eq("departure_events").all()):
        if not events.empty and events[dimension].eq(UNASSIGNED).any():
            stock_reason = "Ereignisse ohne eindeutige Jobgruppenzuordnung verhindern den Bestandsabgleich."
        else:
            projection_key = (signature, id(context), id(baseline))
            if (projection_cache is not None
                    and projection_cache.get("key") == projection_key
                    and "projection" in projection_cache):
                projection = projection_cache["projection"]
            else:
                projection = reporting_projection_base(context, baseline) if pd.Timestamp(summary["metadata"]["target_date"]) > pd.Timestamp(summary["metadata"]["base_date"]) else baseline.copy(deep=True)
                if projection_cache is not None:
                    projection_cache.update(key=projection_key, projection=projection)
            reported = reporting_departure_events(projection, person_events if not events.empty else events, context)
            reported = _allocate_original_events_to_groups(reported, baseline, dimension)
            stock, stock_reason, stock_checks = _build_certified_stock(baseline, projection, final, reported, dimension, summary["metadata"])
    return {"run_signature": signature, "dimension": dimension, "events": events,
            "annual": annual, "stock": stock, "stock_reason": stock_reason, "stock_checks": stock_checks,
            "reporting_events": reported,
            "abgaenge_params": summary.get("abgaenge_params", {}),
            "oe_bridge_status": oe_bridge.get("status"), "oe_bridge_reason": oe_bridge.get("reason", ""),
            "periods": yearly_periods(summary["metadata"])}


def _group_totals(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    work = frame.copy(deep=True)
    work[dimension] = _dimension_values(work, dimension)
    totals = grouped_stock_metrics(work, dimension)
    rows = [{dimension: name, "Köpfe": float(totals[name][0]), "MAK": totals[name][1]}
            for name in sorted(totals)]
    return pd.DataFrame(rows).set_index(dimension) if rows else pd.DataFrame(columns=["Köpfe", "MAK"])


def _build_certified_stock(baseline, projection, final, events, dimension, metadata):
    initial = _group_totals(baseline, dimension)
    projected = _group_totals(projection, dimension)
    target = _group_totals(final, dimension)
    names = sorted(set(initial.index) | set(projected.index) | set(target.index) | set(events.get(dimension, pd.Series(dtype=str))))
    start, end = pd.Timestamp(metadata["base_date"]).normalize(), pd.Timestamp(metadata["target_date"]).normalize()
    rows = []
    checks = []
    for name in names:
        for metric in ("Köpfe", "MAK"):
            tolerance = 0.0 if metric == "Köpfe" else 1e-6
            opening = float(initial[metric].get(name, 0.0))
            technical = float(projected[metric].get(name, 0.0)) - opening
            scoped = events.loc[events[dimension].eq(name)] if not events.empty else events
            delta = scoped.groupby("Jahr")[f"{metric}-Veränderung"].sum() if not scoped.empty else pd.Series(dtype=float)
            cumulative = technical if end > start else 0.0
            rows.append({dimension: name, "Stichtag": start, "Art": "Ausgangsstichtag", "Kennzahl": metric, "Bestand": opening})
            if end > start:
                for year in range(start.year, end.year + 1):
                    cumulative += float(delta.get(year, 0.0))
                    year_end = min(pd.Timestamp(year, 12, 31), end)
                    if year_end > start:
                        rows.append({dimension: name, "Stichtag": year_end,
                                     "Art": "Jahresstichtag", "Kennzahl": metric, "Bestand": opening + cumulative})
                    if opening + cumulative < -tolerance:
                        checks.append({dimension: name, "Kennzahl": metric, "Prüfung": "Zwischenbestand",
                                       "Berechnet": opening + cumulative, "Zielbestand": pd.NA,
                                       "Differenz": pd.NA, "Abgestimmt": False})
            actual = float(target[metric].get(name, 0.0))
            calculated = opening + cumulative
            checks.append({dimension: name, "Kennzahl": metric, "Prüfung": "Endwert",
                           "Berechnet": calculated, "Zielbestand": actual, "Differenz": calculated - actual,
                           "Abgestimmt": abs(calculated - actual) <= tolerance})
    check_frame = pd.DataFrame(checks)
    failed = check_frame.loc[~check_frame["Abgestimmt"]] if not check_frame.empty else check_frame
    if not failed.empty:
        return pd.DataFrame(), f"Jobgruppen-Bestandsüberleitung nicht abgestimmt ({len(failed)} Prüfungen abweichend).", check_frame
    return pd.DataFrame(rows), "", check_frame


def _cached_yearly_view(context: dict, summary: dict, dimension: str, *,
                        include_stock: bool = True) -> dict:
    """Keep expensive reporting projection stable across Streamlit filter reruns."""
    key = (YEARLY_VIEW_CACHE_VERSION, context["run_signature"], id(context),
           dimension, include_stock)
    cache = st.session_state.get("jf_yearly_view_cache")
    if not isinstance(cache, dict) or key not in cache:
        projection_key = key[:3] + ("projection",)
        shared = cache[projection_key] if isinstance(cache, dict) and projection_key in cache else {}
        view = build_jobfamily_yearly_view(
            context, summary, dimension=dimension, include_stock=include_stock,
            projection_cache=shared if include_stock else None)
        cache = {item: value for item, value in cache.items() if item[:3] == key[:3]} if isinstance(cache, dict) else {}
        cache[key] = view
        if include_stock and "projection" in shared:
            cache[projection_key] = shared
        st.session_state["jf_yearly_view_cache"] = cache
        return view
    return cache[key]


def _render_departure_evaluations(view: dict, *, dimension: str, years: list[int],
                                  groups: list[str], event_types: list[str]) -> None:
    """Use the same section sequence as the OE view for saved JF events."""
    events = view["events"]
    scoped = events.loc[events.Jahr.isin(years) & events[dimension].isin(groups)]
    if "QUIT" in event_types:
        st.subheader("Einordnung der Kündigungen")
        quits = scoped.loc[scoped.Ereignisart.eq("QUIT")]
        st.metric("Kündigungen in der Auswahl", int(quits["Abgänge"].sum()))
        st.caption("Beobachtete Kündigungen des gespeicherten Laufs je Jobgruppe und Jahr. Die gespeicherte Wahrscheinlichkeitsprüfung liegt nur nach OE vor; ein JF-Erwartungswert wäre daraus nicht belastbar ableitbar.")
        quit_table = quits.groupby([dimension, "Jahr"], as_index=False)[["Abgänge", "MAK-Veränderung"]].sum()
        if not quit_table.empty:
            st.bar_chart(quit_table, x="Jahr", y="Abgänge", color=dimension, stack=False)
        st.dataframe(quit_table, hide_index=True, use_container_width=True)
    if "RETIREMENT" in event_types:
        st.subheader("Einordnung der Renteneintritte")
        retirement = scoped.loc[scoped.Ereignisart.eq("RETIREMENT")].copy()
        st.metric("Renteneintritte in der Auswahl", int(retirement["Abgänge"].sum()))
        if not retirement.empty:
            age = pd.to_numeric(retirement.get("age", pd.Series(index=retirement.index, dtype=float)), errors="coerce")
            regular_age = float(((view.get("abgaenge_params") or {}).get("retirement") or {})
                                .get("retirement_age", 65))
            retirement["Altersgruppe"] = "Altersangabe fehlt"
            retirement.loc[age.lt(regular_age), "Altersgruppe"] = "Frührente"
            retirement.loc[age.ge(regular_age), "Altersgruppe"] = "Reguläre Rente"
            retirement_table = retirement.groupby([dimension, "Jahr", "Altersgruppe"], as_index=False)[
                ["Abgänge", "MAK-Veränderung"]].sum()
            st.bar_chart(retirement_table, x="Jahr", y="Abgänge", color="Altersgruppe", stack=False)
            st.dataframe(retirement_table, hide_index=True, use_container_width=True)
        else:
            st.info("Für diese Auswahl liegen keine Renteneintritte vor.")
    if set(event_types) & {"ATZ_AR_TO_FR", "ATZ_END"}:
        st.subheader("Einordnung der Altersteilzeit")
        atz = scoped.loc[scoped.Ereignisart.isin({"ATZ_AR_TO_FR", "ATZ_END"})].copy()
        cols = st.columns(2)
        cols[0].metric("Freistellungsbeginne", int(atz.Ereignisart.eq("ATZ_AR_TO_FR").sum()))
        cols[1].metric("ATZ-Vertragsenden", int(atz.Ereignisart.eq("ATZ_END").sum()))
        st.caption("Der Freistellungsbeginn verändert die MAK ohne Kopf-Abgang. Der Kopf-Abgang entsteht erst am Vertragsende.")
        if not atz.empty:
            atz["Ereignisart"] = atz.Ereignisart.map(lambda value: LABELS.get(value, value))
            atz_table = atz.groupby([dimension, "Jahr", "Ereignisart"], as_index=False)[
                ["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
            st.bar_chart(atz_table, x="Jahr", y="MAK-Veränderung", color="Ereignisart", stack=False)
            st.dataframe(atz_table, hide_index=True, use_container_width=True)
        else:
            st.info("Für diese Auswahl liegen keine ATZ-Ereignisse vor.")


def _export_yearly_view(selected: pd.DataFrame, stock: pd.DataFrame, *, dimension: str,
                        groups: list[str], years: list[int], run_signature: str,
                        stock_reason: str, reference_only: bool = False) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        prefix = "Ref_" if reference_only else ""
        selected.to_excel(writer, sheet_name=f"{prefix}Ereignisse_Jahre", index=False)
        if not stock.empty:
            stock.loc[stock[dimension].isin(groups) & stock.Stichtag.dt.year.isin(years)].to_excel(
                writer, sheet_name=f"{prefix}Bestand_Jahre", index=False)
        context_row = {"Laufkennung": run_signature, "Ebene": dimension,
                       "Bestandsabgleich": "Abgestimmt" if not stock.empty else stock_reason}
        if reference_only:
            context_row["Ansicht"] = "Referenzlauf (Seed 0)"
        pd.DataFrame([context_row]).to_excel(
                           writer, sheet_name="Kontext", index=False)
    return output.getvalue()


def _monte_carlo_position_stock_frame(ensemble: dict, active_filters: dict) -> pd.DataFrame:
    rows = []
    for date, level, group, metric, _samples, stats in filtered_annual_position_samples(
            ensemble, active_filters):
        if level not in DIMENSIONS:
            continue
        rows.append({"Stichtag": pd.Timestamp(date), "Ebene": level,
                     "Jobgruppe": group, "Kennzahl": "Köpfe" if metric == "heads" else "MAK",
                     "Mittelwert": stats["mean"], "P10": stats["lower"],
                     "P90": stats["upper"], "samples": list(_samples)})
    return pd.DataFrame(rows, columns=["Stichtag", "Ebene", "Jobgruppe", "Kennzahl",
                                       "Mittelwert", "P10", "P90", "samples"])


def _monte_carlo_position_headlines(stock: pd.DataFrame, baseline: pd.DataFrame, *,
                                    dimension: str, groups: list[str], years: list[int],
                                    metric: str, count: int, band_level: float,
                                    active_filters: dict,
                                    reported_runs: list[pd.DataFrame] | None = None) -> dict | None:
    """Aggregate selected groups within each seed before computing stock bands."""
    if not years or not groups:
        return None
    selected_baseline = _apply_filters_uncached(baseline, dict(active_filters))
    opening_by_group = _group_totals(selected_baseline, dimension)
    opening = float(sum(opening_by_group[metric].get(group, 0.0) for group in groups))
    closing_date = stock.loc[stock["Stichtag"].dt.year.isin(years), "Stichtag"].max()
    if pd.isna(closing_date):
        return None
    closing_rows = stock.loc[stock["Ebene"].eq(dimension)
                             & stock["Stichtag"].eq(closing_date)
                             & stock["Jobgruppe"].isin(groups)
                             & stock["Kennzahl"].eq(metric)]
    if any(len(samples) != count for samples in closing_rows["samples"]):
        raise ValueError("Unvollständige JF-Jahresbestände je Lauf")
    closing = [sum(float(samples[index]) for samples in closing_rows["samples"])
               for index in range(count)]
    result = {"opening": _statistics([opening] * count, band_level),
              "closing": _statistics(closing, band_level),
              "closing_date": closing_date}
    if reported_runs is None:
        return result
    if len(reported_runs) != count:
        raise ValueError("Unvollständige berichtete JF-Ereignisse je Lauf")
    selected_groups = set(groups)
    end_year = max(years)
    loss_column = f"{metric}-Veränderung"
    retirement_types = ({"RETIREMENT", "ATZ_END"} if metric == "Köpfe"
                        else {"RETIREMENT", "ATZ_AR_TO_FR", "ATZ_END"})
    losses = {"departure_loss": [], "retirement_atz": [], "quit_loss": []}
    for events in reported_runs:
        scoped = events.loc[events[dimension].isin(selected_groups)
                            & events["Jahr"].le(end_year)]
        values = -pd.to_numeric(scoped[loss_column], errors="coerce").clip(upper=0)
        losses["departure_loss"].append(float(values.loc[
            scoped["Ereignisart"].isin(DEPARTURES | {"ATZ_AR_TO_FR"})].sum()))
        losses["retirement_atz"].append(float(values.loc[
            scoped["Ereignisart"].isin(retirement_types)].sum()))
        losses["quit_loss"].append(float(values.loc[
            scoped["Ereignisart"].eq("QUIT")].sum()))
    result.update({name: _statistics(values, band_level)
                   for name, values in losses.items()})
    result["departure_share"] = (
        _statistics([value / opening for value in losses["departure_loss"]], band_level)
        if opening > 0 else None)
    return result


def _group_run_jobfamily_events(events: pd.DataFrame, baseline: pd.DataFrame,
                                final: pd.DataFrame, dimension: str) -> pd.DataFrame:
    if events.empty:
        return events.assign(**{dimension: pd.Series(dtype=str)})
    assignments = _baseline_assignments(baseline, dimension)
    final_assignments = _baseline_assignments(final, dimension)
    source = events.get(dimension, pd.Series(index=events.index, dtype=object)).astype("string").str.strip()
    missing = source.isna() | source.eq("") | source.isin(("nan", "None", UNASSIGNED))
    persons = normalize_persnr(events.get("persnr", pd.Series(index=events.index, dtype=object)))
    origin = events["Ereignisquelle"].eq("departure_events") | events["Ereignisart"].eq("Azubi_Conversion_Out")
    source = source.where(~(origin & persons.map(assignments).notna()),
                          persons.map(assignments).astype("string"))
    source = source.where(~(missing & ~origin), persons.map(final_assignments).astype("string"))
    if dimension == "JF-Cluster" and "Jobfamily" in events:
        mapping = pd.concat([baseline[["Jobfamily", "JF-Cluster"]],
                             final[["Jobfamily", "JF-Cluster"]]], ignore_index=True).dropna().drop_duplicates()
        unique = mapping.groupby("Jobfamily")["JF-Cluster"].agg(lambda values: set(values))
        name_map = {name: next(iter(values)) for name, values in unique.items() if len(values) == 1}
        missing = source.isna() | source.eq("") | source.eq(UNASSIGNED)
        source = source.where(~missing, events["Jobfamily"].map(name_map).astype("string"))
    work = events.copy()
    work[dimension] = source.fillna(UNASSIGNED).replace("", UNASSIGNED)
    return _allocate_original_events_to_groups(work, baseline, dimension,
                                               members=_original_group_members(baseline, dimension))


def _retirement_age_values(events: pd.DataFrame, dimension: str,
                           regular_age: float) -> pd.DataFrame:
    work = events.loc[events["Ereignisart"].eq("RETIREMENT")].copy()
    if work.empty:
        return pd.DataFrame(columns=["Abgänge", "MAK-Veränderung"])
    ages = pd.to_numeric(work.get("age", pd.Series(index=work.index, dtype=float)),
                         errors="coerce")
    work["Altersgruppe"] = "Altersangabe fehlt"
    work.loc[ages.lt(regular_age), "Altersgruppe"] = "Frührente"
    work.loc[ages.ge(regular_age), "Altersgruppe"] = "Reguläre Rente"
    return work.groupby(["Jahr", dimension, "Altersgruppe"], dropna=False)[
        ["Abgänge", "MAK-Veränderung"]].sum()


def _monte_carlo_jobfamily_event_frame(
    ensemble: dict, context: dict, view: dict, dimension: str,
    active_filters: dict,
) -> pd.DataFrame:
    import math

    blobs = ensemble.get("annual_event_rows")
    positions = ensemble.get("annual_positions")
    count = ensemble.get("count")
    if (ensemble.get("annual_event_rows_version") != ANNUAL_EVENTS_VERSION
            or ensemble.get("annual_positions_version") != ANNUAL_POSITIONS_VERSION
            or not isinstance(blobs, list) or not isinstance(positions, list)
            or not blobs or len(blobs) != count or len(positions) != count):
        raise ValueError("Jahresereignis- oder Positionszeilen je Lauf fehlen")
    certified = {(int(year), level, str(group), str(kind), metric): samples
                 for year, level, group, kind, metric, samples
                 in ensemble["statistics"]["annual_event_samples"]
                 if level in ("Organisationseinheit", "OE-Cluster")}
    for samples in certified.values():
        if len(samples) != count:
            raise ValueError("Unvollständige zertifizierte Jahresereignisse")
    baseline = context["baseline_snapshot"]
    projection = reporting_projection_base(context, baseline)
    runs = []
    event_runs = []
    reported_runs = []
    for index, (blob, position_blob) in enumerate(zip(blobs, positions)):
        events = decode_annual_event_rows(blob)
        raw = oe_event_samples(events)
        for key in raw.keys() | certified.keys():
            expected = float(certified.get(key, [0.0] * count)[index])
            if not math.isclose(raw.get(key, 0.0), expected, rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"Jahresereignis weicht vom zertifizierten Lauf ab: Seed {index}, {key}")
        final = decode_annual_positions(position_blob)[-1]
        grouped = _group_run_jobfamily_events(events, baseline, final, dimension)
        reported = reporting_departure_events(projection, events, context)
        reported_grouped = _group_run_jobfamily_events(reported, baseline, final, dimension)
        if index == 0:
            expected = view["annual"].groupby(
                ["Jahr", dimension, "Kategorie", "Ereignisart"], dropna=False)[list(METRICS)].sum()
            actual = grouped.groupby(
                ["Jahr", dimension, "Kategorie", "Ereignisart"], dropna=False)[list(METRICS)].sum()
            keys = expected.index.union(actual.index)
            if not expected.reindex(keys, fill_value=0).sub(
                    actual.reindex(keys, fill_value=0)).abs().le(1e-6).all().all():
                raise ValueError("JF-Jahresereignisse weichen in Seed 0 von der Einzelansicht ab")
            regular_age = float(((view.get("abgaenge_params") or {}).get("retirement") or {})
                                .get("retirement_age", 65))
            expected_age = _retirement_age_values(view["events"], dimension, regular_age)
            actual_age = _retirement_age_values(grouped, dimension, regular_age)
            age_keys = expected_age.index.union(actual_age.index)
            if not expected_age.reindex(age_keys, fill_value=0).sub(
                    actual_age.reindex(age_keys, fill_value=0)).abs().le(1e-6).all().all():
                raise ValueError("JF-Rentenalter weicht in Seed 0 von der Einzelansicht ab")
            reference_reported = view.get("reporting_events", pd.DataFrame())
            if (reference_reported.empty and
                    (context.get("departure_events") is not None
                     or context.get("accession_events") is not None)):
                reference_events = normalize_simulation_events(
                    context, expected_run_signature=context["run_signature"])
                reference_reported = _group_run_jobfamily_events(
                    reporting_departure_events(projection, reference_events, context),
                    baseline, context["final_snapshot"], dimension)
            if not reference_reported.empty:
                keys = ["Jahr", dimension, "Ereignisart"]
                expected_reported = reference_reported.groupby(keys)[
                    ["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
                actual_reported = reported_grouped.groupby(keys)[
                    ["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
                rows = expected_reported.index.union(actual_reported.index)
                if not expected_reported.reindex(rows, fill_value=0).sub(
                        actual_reported.reindex(rows, fill_value=0)).abs().le(1e-6).all().all():
                    raise ValueError("Berichtete JF-Abgänge weichen in Seed 0 von der Einzelansicht ab")
        selected = _apply_filters_uncached(grouped, dict(active_filters))
        event_runs.append(selected)
        reported_runs.append(_apply_filters_uncached(reported_grouped, dict(active_filters)))
        values = selected.groupby(["Jahr", dimension, "Kategorie", "Ereignisart"],
                                  dropna=False)[list(METRICS)].sum()
        runs.append(values)
    keys = runs[0].index
    for run in runs[1:]:
        keys = keys.union(run.index)
    rows = []
    for key in keys:
        for metric in METRICS:
            samples = [float(run[metric].get(key, 0.0)) for run in runs]
            stats = _statistics(samples, ensemble["band_level"])
            rows.append({"Jahr": key[0], dimension: key[1], "Kategorie": key[2],
                         "Ereignisart": key[3], "Kennzahl": metric,
                         "Mittelwert": stats["mean"], "P10": stats["lower"],
                         "P90": stats["upper"], "Seed 0": samples[0],
                         "samples": samples})
    result = pd.DataFrame(rows, columns=["Jahr", dimension, "Kategorie", "Ereignisart",
                                         "Kennzahl", "Mittelwert", "P10", "P90",
                                         "Seed 0", "samples"])
    result.attrs["event_runs"] = event_runs
    result.attrs["reported_runs"] = reported_runs
    return result


def _render_monte_carlo_position_stock(stock: pd.DataFrame, *, dimension: str,
                                       groups: list[str], years: list[int],
                                        metric: str,
                                       ensemble: dict, baseline: pd.DataFrame,
                                       active_filters: dict,
                                       reported_runs: list[pd.DataFrame] | None = None) -> None:
    import plotly.graph_objects as go

    st.caption("Zertifizierter Positionsbestand je Lauf und Jahresstichtag. Die bisherige "
               "JF-Bestandsüberleitung enthält zusätzliche technische Brückenwerte und ist "
               "fachlich nicht mit diesem Verlauf gleichzusetzen.")
    visible = stock.loc[stock["Ebene"].eq(dimension) & stock["Jobgruppe"].isin(groups)
                        & stock["Stichtag"].dt.year.isin(years)
                        & stock["Kennzahl"].eq(metric)].copy()
    if visible.empty:
        st.info("Für die Auswahl liegt kein Jahrespositionsbestand vor.")
        return
    headlines = _monte_carlo_position_headlines(
        stock, baseline, dimension=dimension, groups=groups, years=years,
        metric=metric, count=ensemble["count"], band_level=ensemble["band_level"],
        active_filters=active_filters, reported_runs=reported_runs)
    if headlines is not None:
        first = st.columns(3)
        first[0].metric("Ausgangsbestand (Positionen)",
                     f"{headlines['opening']['mean']:.1f}" if metric == "MAK"
                     else f"{headlines['opening']['mean']:.0f}",
                     help="Gleicher gefilterter Ausgangsbestand in jedem Lauf.")
        closing = headlines["closing"]
        first[1].metric(f"Positionsbestand Ende {headlines['closing_date'].year}",
                      f"{closing['mean']:.1f}" if metric == "MAK"
                      else f"{closing['mean']:.1f}",
                      help=f"P10–P90: {closing['lower']:.2f} bis {closing['upper']:.2f}")
        if reported_runs is not None:
            def metric_help(values: dict) -> str:
                return f"P10–P90: {values['lower']:.2f} bis {values['upper']:.2f}"
            share = headlines["departure_share"]
            first[2].metric("Abgänge / Ausgangsbestand", "–" if share is None
                            else f"{share['mean']:.1%}".replace(".", ","),
                            help=None if share is None else metric_help(share))
            second = st.columns(2)
            for col, title, key in (
                (second[0], f"Rente inkl. ATZ bis {max(years)}", "retirement_atz"),
                (second[1], f"Kündigungen bis {max(years)}", "quit_loss"),
            ):
                values = headlines[key]
                col.metric(title, f"{values['mean']:.1f}".replace(".", ","),
                           help=metric_help(values))
    fig = go.Figure()
    for group in groups:
        part = visible.loc[visible["Jobgruppe"].eq(group)].sort_values("Stichtag")
        if part.empty:
            continue
        fig.add_trace(go.Scatter(
            x=part["Stichtag"], y=part["Mittelwert"], mode="lines+markers", name=group,
            error_y=dict(type="data", symmetric=False, color="#6b7280", thickness=1.5,
                         array=(part["P90"] - part["Mittelwert"]).tolist(),
                         arrayminus=(part["Mittelwert"] - part["P10"]).tolist()),
            customdata=part[["P10", "P90"]].to_numpy(),
            hovertemplate="<b>%{fullData.name}</b><br>%{x|%d.%m.%Y}<br>Ø %{y:.2f}"
                          "<br>P10–P90: %{customdata[0]:.2f} bis %{customdata[1]:.2f}<extra></extra>"))
    fig.update_layout(xaxis_title="", yaxis_title=metric, legend_title_text=dimension,
                      margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(visible.drop(columns="samples"), hide_index=True, use_container_width=True)
    lazy_excel_download_button_compat(
        label="Excel exportieren",
        data_builder=lambda: _export_monte_carlo_position_stock(visible, ensemble, headlines),
        file_name="Jobgruppen_Jahrespositionsbestand.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="jf_yearly_selection_export_stock",
        fingerprint=_yearly_export_key(
            str(ensemble.get("reference_instance")), dimension, years, groups, [],
            metric=metric, active_filters=active_filters, ensemble=ensemble),
    )


def _export_monte_carlo_position_stock(visible: pd.DataFrame, ensemble: dict,
                                       headlines: dict | None = None) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        visible.drop(columns="samples", errors="ignore").to_excel(
            writer, sheet_name="Positionsbestand_Jahre", index=False)
        if headlines is not None:
            labels = (("Ausgangsbestand (Positionen)", "opening"),
                      (f"Positionsbestand Ende {headlines['closing_date'].year}", "closing"),
                      ("Abgänge / Ausgangsbestand", "departure_share"),
                      ("Rente inkl. ATZ", "retirement_atz"),
                      ("Kündigungen", "quit_loss"))
            pd.DataFrame([
                {"Kennzahl": label, "Mittelwert": values["mean"],
                 "P10": values["lower"], "P90": values["upper"]}
                for label, key in labels if (values := headlines.get(key)) is not None
            ]).to_excel(writer, sheet_name="Kennzahlen", index=False)
        pd.DataFrame([{"Läufe": ensemble["count"], "Band": "P10–P90",
                       "Bestandsart": "Zertifizierter Positionsbestand"}]).to_excel(
                           writer, sheet_name="Kontext", index=False)
    return output.getvalue()


def _monte_carlo_event_totals(selected: pd.DataFrame, *, dimension: str,
                              metric: str, band_level: float) -> pd.DataFrame:
    rows = []
    for (year, group), part in selected.groupby(["Jahr", dimension], sort=True):
        samples = [sum(values[index] for values in part["samples"])
                   for index in range(len(part.iloc[0]["samples"]))]
        stats = _statistics(samples, band_level)
        rows.append({"Jahr": int(year), dimension: group, "Kennzahl": metric,
                     "Mittelwert": stats["mean"], "P10": stats["lower"],
                     "P90": stats["upper"]})
    return pd.DataFrame(rows, columns=["Jahr", dimension, "Kennzahl", "Mittelwert",
                                       "P10", "P90"])


def _monte_carlo_event_year_sum(selected: pd.DataFrame, *, dimension: str,
                                band_level: float) -> pd.DataFrame:
    """Sum selected years within each seed, then calculate group quantiles."""
    rows = []
    for group, part in selected.groupby(dimension, sort=True):
        count = len(part.iloc[0]["samples"])
        samples = [sum(float(values[index]) for values in part["samples"])
                   for index in range(count)]
        stats = _statistics(samples, band_level)
        rows.append({dimension: group, "Summe ausgewählte Kennzahl": stats["mean"],
                     "P10": stats["lower"], "P90": stats["upper"]})
    return pd.DataFrame(rows, columns=[dimension, "Summe ausgewählte Kennzahl", "P10", "P90"])


def _monte_carlo_departure_year_sum(all_selected: pd.DataFrame, *, dimension: str,
                                    groups: list[str], count: int,
                                    band_level: float) -> pd.DataFrame:
    """Sum departures within each run before calculating group quantiles."""
    departures = all_selected.loc[all_selected["Ereignisart"].isin(DEPARTURES)]
    result = pd.DataFrame({dimension: groups})
    for source_metric, label, transform in (
        ("Abgänge", "Abgehende Köpfe", lambda value: value),
        ("MAK-Veränderung", "Abgehende MAK", lambda value: max(-value, 0.0)),
    ):
        selected = departures.loc[departures["Kennzahl"].eq(source_metric)]
        values = {}
        for group, part in selected.groupby(dimension):
            samples = [sum(transform(float(row[index])) for row in part["samples"])
                       for index in range(count)]
            values[group] = _statistics(samples, band_level)
        for suffix, key in (("", "mean"), (" P10", "lower"), (" P90", "upper")):
            result[f"{label}{suffix}"] = [values.get(group, {key: 0.0})[key]
                                           for group in groups]
    return result


def _monte_carlo_unassigned_events(event_runs: list[pd.DataFrame], *,
                                   dimension: str, years: list[int],
                                   event_types: list[str], band_level: float) -> dict:
    counts = [float(len(events.loc[
        events["Jahr"].isin(years)
        & events["Ereignisart"].isin(event_types)
        & events[dimension].eq(UNASSIGNED)])) for events in event_runs]
    return _statistics(counts, band_level)


def _monte_carlo_yearly_group_options(baseline: pd.DataFrame, stock: pd.DataFrame,
                                      events: pd.DataFrame, *, dimension: str,
                                      active_filters: dict) -> list[str]:
    filtered_baseline = _apply_filters_uncached(baseline, dict(active_filters))
    return sorted(set(_dimension_values(filtered_baseline, dimension)) |
                  set(stock.loc[stock["Ebene"].eq(dimension), "Jobgruppe"]) |
                  set(events[dimension]))


def _monte_carlo_evaluation_frame(event_runs: list[pd.DataFrame], *, dimension: str,
                                  years: list[int], groups: list[str],
                                  kinds: set[str], retirement_age: float,
                                  band_level: float) -> pd.DataFrame:
    metrics = ("Abgänge", "Köpfe-Veränderung", "MAK-Veränderung", "Ereignisse")
    runs = []
    for events in event_runs:
        scoped = events.loc[events["Jahr"].isin(years) & events[dimension].isin(groups)
                            & events["Ereignisart"].isin(kinds)].copy()
        if scoped.empty:
            runs.append({})
            continue
        ages = pd.to_numeric(scoped.get("age", pd.Series(index=scoped.index, dtype=float)),
                             errors="coerce")
        scoped["Altersgruppe"] = ""
        if "RETIREMENT" in kinds:
            scoped["Altersgruppe"] = "Altersangabe fehlt"
            scoped.loc[ages.lt(retirement_age), "Altersgruppe"] = "Frührente"
            scoped.loc[ages.ge(retirement_age), "Altersgruppe"] = "Reguläre Rente"
        grouped = scoped.groupby(["Jahr", dimension, "Ereignisart", "Altersgruppe"],
                                 dropna=False, sort=True)
        values = {}
        for key, part in grouped:
            values[key] = {"Abgänge": float(part["Abgänge"].sum()),
                           "Köpfe-Veränderung": float(part["Köpfe-Veränderung"].sum()),
                           "MAK-Veränderung": float(part["MAK-Veränderung"].sum()),
                           "Ereignisse": float(len(part))}
        runs.append(values)
    keys = sorted(set().union(*(set(run) for run in runs)))
    rows = []
    for key in keys:
        for metric in metrics:
            samples = [run.get(key, {}).get(metric, 0.0) for run in runs]
            stats = _statistics(samples, band_level)
            rows.append({"Jahr": key[0], dimension: key[1], "Ereignisart": key[2],
                         "Altersgruppe": key[3], "Kennzahl": metric,
                         "Mittelwert": stats["mean"], "P10": stats["lower"],
                         "P90": stats["upper"], "samples": samples})
    return pd.DataFrame(rows, columns=["Jahr", dimension, "Ereignisart", "Altersgruppe",
                                       "Kennzahl", "Mittelwert", "P10", "P90", "samples"])


def _render_monte_carlo_evaluations(mc_events: pd.DataFrame, view: dict, *,
                                    dimension: str, years: list[int], groups: list[str],
                                    event_types: list[str], ensemble: dict) -> dict[str, pd.DataFrame]:
    import plotly.graph_objects as go

    regular_age = float(((view.get("abgaenge_params") or {}).get("retirement") or {})
                        .get("retirement_age", 65))
    sections = [
        ("QUIT", {"QUIT"}, "Einordnung der Kündigungen", "Abgänge", "Kündigungen in der Auswahl"),
        ("RETIREMENT", {"RETIREMENT"}, "Einordnung der Renteneintritte", "Abgänge", "Renteneintritte in der Auswahl"),
        ("ATZ", {"ATZ_AR_TO_FR", "ATZ_END"}, "Einordnung der Altersteilzeit", "MAK-Veränderung", None),
    ]
    export_frames = {}
    for _code, kinds, title, chart_metric, card_title in sections:
        if not set(event_types) & kinds:
            continue
        st.subheader(title)
        frame = _monte_carlo_evaluation_frame(
            mc_events.attrs["event_runs"], dimension=dimension, years=years,
            groups=groups, kinds=kinds, retirement_age=regular_age,
            band_level=ensemble["band_level"])
        export_frames[_code] = frame
        if frame.empty:
            st.info("Für diese Auswahl liegen in keinem Lauf Ereignisse vor.")
            continue
        if card_title:
            values = frame.loc[frame["Kennzahl"].eq("Abgänge")]
            samples = [sum(row[index] for row in values["samples"])
                       for index in range(ensemble["count"])]
            stats = _statistics(samples, ensemble["band_level"])
            st.metric(card_title, f"{stats['mean']:.1f}".replace(".", ","),
                      help=f"P10–P90: {stats['lower']:.1f} bis {stats['upper']:.1f}")
        else:
            cols = st.columns(2)
            for col, kind, label in ((cols[0], "ATZ_AR_TO_FR", "Freistellungsbeginne"),
                                     (cols[1], "ATZ_END", "ATZ-Vertragsenden")):
                values = frame.loc[frame["Ereignisart"].eq(kind) & frame["Kennzahl"].eq("Ereignisse")]
                samples = [sum(row[index] for row in values["samples"])
                           for index in range(ensemble["count"])]
                stats = _statistics(samples, ensemble["band_level"])
                col.metric(label, f"{stats['mean']:.1f}".replace(".", ","),
                           help=f"P10–P90: {stats['lower']:.1f} bis {stats['upper']:.1f}")
        plotted = frame.loc[frame["Kennzahl"].eq(chart_metric)]
        fig = go.Figure()
        group_cols = [dimension, "Altersgruppe"] if "RETIREMENT" in kinds else [dimension, "Ereignisart"]
        for key, part in plotted.groupby(group_cols, sort=True):
            label = " · ".join(map(str, key if isinstance(key, tuple) else (key,)))
            fig.add_trace(go.Bar(
                x=part["Jahr"].astype(str), y=part["Mittelwert"], name=label,
                error_y=dict(type="data", symmetric=False, color="#6b7280", thickness=1.5,
                             array=(part["P90"] - part["Mittelwert"]).tolist(),
                             arrayminus=(part["Mittelwert"] - part["P10"]).tolist())))
        fig.update_layout(barmode="group", xaxis_title="Jahr", yaxis_title=chart_metric)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(frame.drop(columns="samples"), hide_index=True, use_container_width=True)
        if "QUIT" in kinds:
            st.caption("Beobachtete Kündigungen je Lauf. Die gespeicherte Wahrscheinlichkeitsprüfung liegt nur nach OE vor; ein JF-Erwartungswert ist daraus nicht belastbar ableitbar.")
        elif "ATZ_AR_TO_FR" in kinds:
            st.caption("Der Freistellungsbeginn verändert die MAK ohne Kopf-Abgang; der Kopf-Abgang entsteht am Vertragsende.")
    return export_frames


def _render_monte_carlo_events(selected: pd.DataFrame, *, dimension: str,
                               metric: str, ensemble: dict,
                               all_selected: pd.DataFrame | None = None
                               ) -> tuple[pd.DataFrame, pd.DataFrame]:
    import plotly.graph_objects as go

    totals = _monte_carlo_event_totals(selected, dimension=dimension, metric=metric,
                                       band_level=ensemble["band_level"])
    year_sum = _monte_carlo_event_year_sum(
        selected, dimension=dimension, band_level=ensemble["band_level"])
    if all_selected is not None and not year_sum.empty:
        departure_sum = _monte_carlo_departure_year_sum(
            all_selected, dimension=dimension, groups=year_sum[dimension].tolist(),
            count=ensemble["count"], band_level=ensemble["band_level"])
        year_sum = year_sum.merge(departure_sum, on=dimension, validate="one_to_one")
    if all_selected is not None and not totals.empty:
        departures = all_selected.loc[all_selected["Ereignisart"].isin(DEPARTURES)]
        for source_metric, label, transform in (
            ("Abgänge", "Abgehende Köpfe", lambda value: value),
            ("MAK-Veränderung", "Abgehende MAK", lambda value: max(-value, 0.0)),
        ):
            metric_rows = departures.loc[departures["Kennzahl"].eq(source_metric)]
            values = {}
            for (year, group), part in metric_rows.groupby(["Jahr", dimension]):
                samples = [sum(transform(row[index]) for row in part["samples"])
                           for index in range(ensemble["count"])]
                values[(year, group)] = _statistics(samples, ensemble["band_level"])
            totals[label] = [values.get((row["Jahr"], row[dimension]), {"mean": 0.0})["mean"]
                             for _, row in totals.iterrows()]
            totals[f"{label} P10"] = [values.get((row["Jahr"], row[dimension]), {"lower": 0.0})["lower"]
                                      for _, row in totals.iterrows()]
            totals[f"{label} P90"] = [values.get((row["Jahr"], row[dimension]), {"upper": 0.0})["upper"]
                                      for _, row in totals.iterrows()]
    if totals.empty:
        st.info("Für diese Auswahl liegen in keinem Lauf Ereignisse vor.")
        return totals, year_sum
    fig = go.Figure()
    for group, part in totals.groupby(dimension, sort=True):
        fig.add_trace(go.Bar(
            x=part["Jahr"].astype(str), y=part["Mittelwert"], name=str(group),
            error_y=dict(type="data", symmetric=False, color="#6b7280", thickness=1.5,
                         array=(part["P90"] - part["Mittelwert"]).tolist(),
                         arrayminus=(part["Mittelwert"] - part["P10"]).tolist()),
            customdata=part[["P10", "P90"]].to_numpy(),
            hovertemplate="<b>%{fullData.name}</b><br>%{x}<br>Ø %{y:.2f}"
                          "<br>P10–P90: %{customdata[0]:.2f} bis %{customdata[1]:.2f}<extra></extra>"))
    fig.update_layout(barmode="group", xaxis_title="Jahr", yaxis_title=metric)
    st.plotly_chart(fig, use_container_width=True)
    from utils.metric_view import selected_unit_columns
    unit = "MAK" if metric.startswith("MAK") else "Köpfe"
    st.dataframe(selected_unit_columns(totals, unit), hide_index=True, use_container_width=True)
    st.markdown("**Summe der gewählten Jahre je Jobgruppe**")
    st.dataframe(selected_unit_columns(year_sum, unit), hide_index=True, use_container_width=True)
    with st.expander("Aufschlüsselung nach Ereignisart"):
        detail = selected.drop(columns=["samples", "Seed 0"]).copy()
        detail["Ereignisart"] = detail["Ereignisart"].map(lambda value: LABELS.get(value, value))
        st.dataframe(selected_unit_columns(detail, unit), hide_index=True, use_container_width=True)
    return totals, year_sum


def _export_monte_carlo_events(selected: pd.DataFrame, totals: pd.DataFrame,
                               ensemble: dict,
                               evaluations: dict[str, pd.DataFrame] | None = None,
                               year_sum: pd.DataFrame | None = None,
                               unassigned: dict | None = None) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        totals.to_excel(writer, sheet_name="Ereignisse_Jahre", index=False)
        if year_sum is not None:
            year_sum.to_excel(writer, sheet_name="Ereignisse_Summe", index=False)
        selected.drop(columns=["samples", "Seed 0"]).to_excel(
            writer, sheet_name="Ereignisarten_Jahre", index=False)
        for key, sheet in (("QUIT", "Einordnung_Kuendigung"),
                           ("RETIREMENT", "Einordnung_Rente"),
                           ("ATZ", "Einordnung_ATZ")):
            frame = (evaluations or {}).get(key)
            if frame is not None and not frame.empty:
                frame.drop(columns="samples").to_excel(writer, sheet_name=sheet, index=False)
        if unassigned is not None:
            pd.DataFrame([{"Kennzahl": "Nicht zugeordnete Ereigniszeilen",
                           "Mittelwert": unassigned["mean"],
                           "P10": unassigned["lower"],
                           "P90": unassigned["upper"]}]).to_excel(
                               writer, sheet_name="Datenqualitaet", index=False)
        pd.DataFrame([{"Läufe": ensemble["count"], "Band": "P10–P90",
                       "Ansicht": "Monte Carlo"}]).to_excel(writer, sheet_name="Kontext", index=False)
    return output.getvalue()


def _yearly_export_key(run_signature: str, dimension: str, years: list[int],
                       groups: list[str], event_types: list[str], *,
                       metric: str | None = None, active_filters: dict | None = None,
                       ensemble: dict | None = None) -> tuple:
    """Bind a prepared workbook to one concrete run and an immutable selection."""
    selection = (run_signature, dimension, tuple(years), tuple(groups), tuple(event_types))
    if ensemble is None:
        return (*selection, metric, None)
    filters = tuple(sorted((name, tuple(values) if isinstance(values, (list, tuple)) else values)
                           for name, values in (active_filters or {}).items()))
    identity = (id(ensemble), ensemble.get("plan_signature"),
                ensemble.get("input_fingerprint"), ensemble.get("reference_instance"),
                ensemble.get("count"), ensemble.get("annual_event_rows_version"),
                ensemble.get("annual_positions_version"))
    return (*selection, metric, filters, identity)


@st.fragment
def render_jobfamily_simulation_yearly(context: dict | None, summary: dict | None, *,
                                       run_signature: str | None,
                                       monte_carlo_ensemble: dict | None = None) -> None:
    from components.sidebar import get_global_metric_view
    selected_unit = get_global_metric_view()
    st.subheader("Veränderungen pro Jahr")
    if monte_carlo_ensemble is not None:
        st.caption("Jahrespositionsbestand, Jahresereignisse und Einordnungen: Mittelwert und P10–P90.")
    if not context or not summary or not run_signature or context.get("run_signature") != run_signature:
        st.info("Bitte die Simulation auf 'Kompakt plus Simulation' neu berechnen.")
        return
    dimension = st.radio("Jobgruppenebene", DIMENSIONS, horizontal=True, key="jf_yearly_dimension")
    try:
        view = _cached_yearly_view(
            context, summary, dimension,
            include_stock=monte_carlo_ensemble is None)
    except (KeyError, ValueError, TypeError) as exc:
        st.warning(f"Jahresauswertung nicht verfügbar: {exc}")
        return
    mc_stock = None
    mc_events = None
    if monte_carlo_ensemble is not None:
        try:
            mc_stock = _monte_carlo_position_stock_frame(
                monte_carlo_ensemble, get_active_view_filters())
            mc_events = _monte_carlo_jobfamily_event_frame(
                monte_carlo_ensemble, context, view, dimension,
                get_active_view_filters())
        except (KeyError, ValueError, TypeError) as exc:
            st.error(f"Monte-Carlo-Jahreswerte nicht verfügbar: {exc}. Bitte Simulation neu berechnen.")
            return
    periods = view["periods"]
    years = st.multiselect("Jahre", periods.Jahr.tolist(), default=periods.Jahr.tolist(), key="jf_yearly_years")
    if mc_stock is None:
        options = sorted(set(view["annual"].get(dimension, pd.Series(dtype=str)).dropna()) |
                         set(_dimension_values(context["baseline_snapshot"], dimension)) |
                         set(_dimension_values(context["final_snapshot"], dimension)))
    else:
        options = _monte_carlo_yearly_group_options(
            context["baseline_snapshot"], mc_stock, mc_events,
            dimension=dimension, active_filters=get_active_view_filters())
    if options == ["Sonstiges"]:
        st.warning("Alle Daten dieses Laufs sind 'Sonstiges' zugeordnet. Bitte die aktive Jobgruppen-Clusterdatei prüfen; ein Vergleich einzelner Jobgruppen ist so nicht aussagekräftig.")
    groups = st.multiselect("Jobgruppen", options, default=options, key=f"jf_yearly_groups_{dimension}")
    if mc_stock is None:
        st.caption("Die Ereignisauswahl filtert nur Veränderungen. Der verbleibende Bestand umfasst stets den vollständigen Simulationslauf und ist unabhängig von den Filtern der Bestandsansicht.")
    else:
        st.caption("Jahrespositionsbestand und Ereignisse folgen den aktiven Personenfiltern der Sidebar. Die Ereignisauswahl steuert nur den Bereich Veränderungen.")
    st.caption("Personen mit Planstellen in mehreren Jobgruppen zaehlen in jeder betroffenen Jobgruppe als Kopf. Gruppenkopfzahlen sind daher nicht zur Unternehmenssumme addierbar; MAK werden nach Planstellenanteilen verteilt.")
    st.markdown("#### Verbleibender Bestand")
    stock = view["stock"]
    if mc_stock is not None:
        _render_monte_carlo_position_stock(
            mc_stock, dimension=dimension, groups=groups, years=years,
            metric=selected_unit,
            ensemble=monte_carlo_ensemble, baseline=context["baseline_snapshot"],
            active_filters=get_active_view_filters(),
            reported_runs=mc_events.attrs["reported_runs"])
    else:
        if stock.empty:
            st.info(view["stock_reason"])
            if not view["stock_checks"].empty:
                with st.expander("Abweichungen im Jobgruppen-Endwertabgleich", expanded=True):
                    st.dataframe(view["stock_checks"].loc[lambda frame: ~frame["Abgestimmt"]],
                                 hide_index=True, use_container_width=True)
        else:
            stock_metric = selected_unit
            visible = stock.loc[stock[dimension].isin(groups) & stock.Kennzahl.eq(stock_metric) &
                                (stock.Stichtag.dt.year.isin(years) | stock.Art.eq("Ausgangsstichtag"))].copy()
            if years and not visible.empty:
                departure_types = sorted(view["annual"].loc[
                    view["annual"].Ereignisart.isin(DEPARTURES), "Ereignisart"].unique())
                suffix = hashlib.sha256(run_signature.encode()).hexdigest()[:12]
                with st.expander("Abgangsarten im Excel-Export"):
                    export_types = st.multiselect(
                        "Abgangsarten für zusätzliche Matrixblätter", departure_types,
                        default=departure_types, format_func=lambda value: LABELS.get(value, value),
                        key=f"jf_stock_export_types_{dimension}_{suffix}")
                    st.caption("Originale Jahresereignisse mit Vorzeichen; technische Bestandsaufbereitung ist separat dokumentiert.")
                lazy_excel_download_button_compat(
                        label="Excel exportieren",
                        data_builder=lambda: build_jobfamily_stock_export_bytes(
                            view, dimension=dimension, groups=groups, years=years,
                            metric=stock_metric, event_types=export_types),
                        file_name="Jobgruppen_Bestandsverlauf.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="jf_yearly_selection_export_stock",
                        fingerprint=(run_signature, id(view), dimension, tuple(groups),
                                     tuple(years), stock_metric, tuple(export_types)),
                        help="Aktuelle Bestandsauswahl mit Folienvorlagen für MAK und Köpfe, Jahreswerten und Endwertabgleich.",
                )
                opening = visible.loc[visible.Art.eq("Ausgangsstichtag")].Bestand.sum()
                closing_rows = visible.loc[visible.Art.eq("Jahresstichtag")]
                closing = closing_rows.loc[closing_rows.Stichtag.eq(closing_rows.Stichtag.max()), "Bestand"].sum() if not closing_rows.empty else opening
                reported = view["reporting_events"]
                reported = reported.loc[reported[dimension].isin(groups) & reported.Jahr.le(max(years))] if years and not reported.empty else reported.iloc[:0]
                loss_column = f"{stock_metric}-Veränderung"
                retirement_types = {"RETIREMENT", "ATZ_END"} if stock_metric == "Köpfe" else {"RETIREMENT", "ATZ_AR_TO_FR", "ATZ_END"}
                loss = -pd.to_numeric(reported[loss_column], errors="coerce").clip(upper=0) if not reported.empty else pd.Series(dtype=float)
                retirement_loss = float(loss.loc[reported.Ereignisart.isin(retirement_types)].sum()) if not reported.empty else 0.0
                quit_loss = float(loss.loc[reported.Ereignisart.eq("QUIT")].sum()) if not reported.empty else 0.0
                departure_loss = float(loss.loc[reported.Ereignisart.isin(DEPARTURES | {"ATZ_AR_TO_FR"})].sum()) if not reported.empty else 0.0
                first = st.columns(3)
                first[0].metric("Ausgangsbestand", f"{opening:.1f}" if stock_metric == "MAK" else f"{opening:.0f}")
                first[1].metric(f"Bestand Ende {max(years)}", f"{closing:.1f}" if stock_metric == "MAK" else f"{closing:.0f}")
                first[2].metric("Abgänge / Ausgangsbestand", "–" if opening <= 0 else f"{departure_loss / opening:.1%}")
                second = st.columns(2)
                second[0].metric(f"Rente inkl. ATZ bis {max(years)}", f"{retirement_loss:.1f}" if stock_metric == "MAK" else f"{retirement_loss:.0f}")
                second[1].metric(f"Kündigungen bis {max(years)}", f"{quit_loss:.1f}" if stock_metric == "MAK" else f"{quit_loss:.0f}")
                if stock_metric == "Köpfe" and set(groups) == set(options):
                    unique_opening = compute_headcount(context["baseline_snapshot"])
                    if opening != unique_opening:
                        st.caption(f"Die Summe der Jobgruppen-Ausgangsbestände ({opening:.0f}) zählt Personen mit mehreren Jobgruppen mehrfach. Im Gesamtbestand sind es {unique_opening:.0f} eindeutige Personen.")
                st.success("Jahresverlauf mit dem finalen Jobgruppenbestand abgestimmt.")
                plotted = visible.drop_duplicates([dimension, "Stichtag"], keep="last").copy()
                plotted["Stichtag"] = plotted["Stichtag"].dt.strftime("%Y-%m-%d")
                st.bar_chart(plotted, x="Stichtag", y="Bestand", color=dimension, stack=False)
                st.dataframe(visible, hide_index=True, use_container_width=True)
    st.markdown("#### Veränderungen")
    if mc_stock is not None:
        st.caption("Jahresereignisse und Einordnungen: Mittelwert und P10–P90 je Lauf.")
    metric = f"{selected_unit}-Veränderung"
    types = (sorted(set(mc_events["Ereignisart"].dropna())) if mc_events is not None
             else sorted(set(view["annual"].Ereignisart.dropna())))
    selected_types = st.multiselect("Ereignisarten", types, default=types,
                                    format_func=lambda x: LABELS.get(x, x), key="jf_yearly_types")
    selected = (view["annual"].loc[
        view["annual"].Jahr.isin(years)
        & view["annual"][dimension].isin(groups)
        & view["annual"].Ereignisart.isin(selected_types)].copy()
        if mc_events is None else None)
    if mc_events is not None:
        mc_selected = mc_events.loc[mc_events["Jahr"].isin(years)
                                    & mc_events[dimension].isin(groups)
                                    & mc_events["Ereignisart"].isin(selected_types)
                                    & mc_events["Kennzahl"].eq(metric)].copy()
        mc_all_selected = mc_events.loc[mc_events["Jahr"].isin(years)
                                        & mc_events[dimension].isin(groups)
                                        & mc_events["Ereignisart"].isin(selected_types)].copy()
        mc_totals, mc_year_sum = _render_monte_carlo_events(
            mc_selected, dimension=dimension, metric=metric,
            ensemble=monte_carlo_ensemble, all_selected=mc_all_selected)
    else:
        if not selected.empty:
            chart = selected.groupby(["Jahr", dimension], as_index=False)[metric].sum()
            if len(set(selected_types) & DEPARTURES) > 1:
                stacked = selected.groupby(["Jahr", dimension, "Ereignisart"], as_index=False)[metric].sum()
                render_stacked_event_chart(stacked, dimension=dimension, metric=metric)
            else:
                st.bar_chart(chart, x="Jahr", y=metric, color=dimension, stack=False)
            table = chart.pivot(index=dimension, columns="Jahr", values=metric).fillna(0)
            table["Summe ausgewählte Kennzahl"] = table.sum(axis=1)
            departures = selected.loc[selected.Ereignisart.isin(DEPARTURES)]
            table["Abgehende Köpfe"] = departures.groupby(dimension)["Abgänge"].sum().reindex(table.index, fill_value=0)
            mak_loss = -pd.to_numeric(departures["MAK-Veränderung"], errors="coerce").clip(upper=0)
            table["Abgehende MAK"] = mak_loss.groupby(departures[dimension]).sum().reindex(table.index, fill_value=0)
            from utils.metric_view import selected_unit_columns
            st.dataframe(selected_unit_columns(table, selected_unit), use_container_width=True)
        else:
            st.info("Für diese Auswahl liegen keine Ereignisse vor.")
    for period in periods.loc[periods.Jahr.isin(years)].itertuples(index=False):
        if period.Teiljahr:
            st.caption(f"{period.Jahr}: Teiljahr {period.Von:%d.%m.%Y} bis {period.Bis:%d.%m.%Y}.")
    if mc_events is None:
        _render_departure_evaluations(view, dimension=dimension, years=years,
                                      groups=groups, event_types=selected_types)
    else:
        mc_eval_frames = _render_monte_carlo_evaluations(
            mc_events, view, dimension=dimension, years=years, groups=groups,
            event_types=selected_types, ensemble=monte_carlo_ensemble)
    if mc_events is None:
        with st.expander("Aufschlüsselung nach Ereignisart"):
            detail = selected.copy()
            detail["Ereignisart"] = detail["Ereignisart"].map(lambda value: LABELS.get(value, value))
            from utils.metric_view import selected_unit_columns
            st.dataframe(selected_unit_columns(detail, selected_unit), hide_index=True, use_container_width=True)
        unresolved = view["events"].loc[view["events"].get(dimension, pd.Series(dtype=str)).eq(UNASSIGNED)]
        if not unresolved.empty:
            st.warning(f"{len(unresolved)} Ereignisse haben keine eindeutige {dimension}-Zuordnung; sie stehen unter UNMAPPED.")
        st.caption("Ereignisse des gespeicherten Laufs. Die Auswahl hier ist unabhängig von den Sidebar-Filtern der Bestandsansicht. Interne Jobgruppenwechsel sind keine externen Zugänge.")
    else:
        mc_unassigned = _monte_carlo_unassigned_events(
            mc_events.attrs["event_runs"], dimension=dimension, years=years,
            event_types=selected_types, band_level=monte_carlo_ensemble["band_level"])
        if mc_unassigned["upper"] > 0 or mc_unassigned["mean"] > 0:
            st.warning(
                f"Nicht zugeordnete {dimension}-Ereigniszeilen in der gewählten Jahres- und "
                f"Ereignisauswahl: Ø {mc_unassigned['mean']:.1f}, "
                f"P10–P90 {mc_unassigned['lower']:.1f} bis {mc_unassigned['upper']:.1f}. "
                "Sie stehen unter UNMAPPED; die aktiven Personenfilter gelten je Lauf."
            )
        st.caption("Jahresereignisse und Detailtabelle folgen den aktiven Sidebar-Filtern. Interne Jobgruppenwechsel sind keine externen Zugänge.")
    if years and groups:
        # Streamlit evaluates widgets on every rerun; delay workbook construction
        # until the user explicitly requests an export.
        export_key = _yearly_export_key(
            run_signature, dimension, years, groups, selected_types,
            metric=metric,
            active_filters=get_active_view_filters() if mc_events is not None else None,
            ensemble=monte_carlo_ensemble if mc_events is not None else None)
        if st.button("Excel-Export vorbereiten", key="jf_yearly_prepare_export"):
            data = (_export_monte_carlo_events(mc_selected, mc_totals, monte_carlo_ensemble,
                                               mc_eval_frames, mc_year_sum, mc_unassigned)
                    if mc_events is not None else _export_yearly_view(
                        selected, stock, dimension=dimension, groups=groups, years=years,
                        run_signature=run_signature, stock_reason=view["stock_reason"]))
            st.session_state["jf_yearly_prepared_export"] = (export_key, data)
        prepared_export = st.session_state.get("jf_yearly_prepared_export")
        if prepared_export and prepared_export[0] == export_key:
            st.download_button("Jahresscheiben als Excel exportieren", prepared_export[1],
                           file_name="Jobgruppen_Jahresscheiben.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="jf_yearly_export")
