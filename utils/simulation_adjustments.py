"""Deterministic post-processing of a completed forecast; never calls a forecast.

The first supported rule reverses a single uncomplicated resignation or
retirement of an original employee. Ambiguous cases fail closed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

import pandas as pd
import streamlit as st

from abgaenge.schemas import normalize_persnr
from abgaenge.params import get_retirement_ages
from dataloader.cluster_resolver import ActiveClusterSource, ClusterMappingBundle
from dataloader.compact_simulation_engine import (
    _apply_salary_automation_to_employee_state,
    _build_simulation_audit_tables,
    _finalize_future_snapshot,
    _prepare_employee_forecast_base,
    _update_existing_rows,
)
from utils.compact_page_loader import load_compact_page_module
from utils.org_simulation_events import build_simulation_event_summary
from utils.org_simulation_reconciliation import raw_stock_mak
from utils.simulation_validation import SimulationView, value_fingerprint
from utils.settings_loader import get_setting


def _person_ids(frame: pd.DataFrame, column: str) -> pd.Series:
    return normalize_persnr(frame[column]).astype("string")


def eligible_departures(view: SimulationView) -> pd.DataFrame:
    """Return simple departure candidates with an unambiguous original scope."""
    context = view.event_context
    if get_setting("exclusions", {}) != context.get("run_settings", {}).get("exclusions", {}):
        return pd.DataFrame()
    saved_salary = view.export_context.get("salary_automation")
    if saved_salary is not None and get_setting("salary_automation", {}) != saved_salary:
        return pd.DataFrame()
    saved_lookup = context.get("cost_lookup_fingerprint")
    if saved_lookup is None or value_fingerprint(st.session_state.get("tvoed_lookup", {})) != saved_lookup:
        return pd.DataFrame()
    source = context.get("source_snapshot")
    events = context.get("departure_events")
    final = context.get("final_snapshot")
    if not isinstance(source, pd.DataFrame) or not isinstance(events, pd.DataFrame) or not isinstance(final, pd.DataFrame):
        return pd.DataFrame()
    if (events.empty or "persnr" not in events or "PersNr" not in source
            or "Is_Vacant" not in final or "Organisationseinheit" not in source
            or "Jobfamily" not in source):
        return pd.DataFrame()
    params = context.get("zugaenge_params", {})
    if any(params.get(key, {}).get("active") is not False for key in ("azubi", "trainee", "new_hires")):
        return pd.DataFrame()
    if isinstance(context.get("accession_events"), pd.DataFrame) and not context["accession_events"].empty:
        return pd.DataFrame()
    if len(source) != len(final):
        return pd.DataFrame()
    raw_final = context.get("raw_final_snapshot")
    if (not isinstance(raw_final, pd.DataFrame)
            or not isinstance(context.get("future_employee_state"), pd.DataFrame)
            or not source.index.is_unique or not source.index.equals(raw_final.index)):
        return pd.DataFrame()
    work = events.copy()
    work["_person"] = _person_ids(work, "persnr")
    work["_heads"] = pd.to_numeric(work.get("headcount_change", 0), errors="coerce").fillna(0)
    source = source.copy()
    source["_person"] = _person_ids(source, "PersNr")
    final_ids = set(_person_ids(final.loc[~final.get("Is_Vacant", False).fillna(False).astype(bool)], "PersNr").dropna())
    atz = context.get("df_atz")
    atz_ids = set(_person_ids(atz, "PersNr").dropna()) if isinstance(atz, pd.DataFrame) and "PersNr" in atz else set()
    rows = []
    for pid, person_events in work.groupby("_person", dropna=True):
        if len(person_events) != 1:
            continue
        event = person_events.iloc[0]
        if event.get("reason_code") not in ("QUIT", "RETIREMENT") or event["_heads"] != -1:
            continue
        if pid in final_ids:
            continue
        positions = source.loc[source["_person"].eq(pid)]
        if positions.empty or "Is_Vacant" in positions and positions["Is_Vacant"].fillna(False).any():
            continue
        orgs = positions["Organisationseinheit"].dropna().astype(str).str.strip().unique()
        groups = positions["Jobfamily"].dropna().astype(str).str.strip().unique() if "Jobfamily" in positions else []
        if len(orgs) != 1 or len(groups) != 1 or not orgs[0] or not groups[0]:
            continue
        if "Status kundenindividuell" in positions and positions["Status kundenindividuell"].astype(str).str.contains("Ruhend", case=False).any():
            continue
        if "ATZ_Status" in positions and positions["ATZ_Status"].fillna("Kein ATZ").ne("Kein ATZ").any():
            continue
        if "Ist_Azubi" in positions and positions["Ist_Azubi"].fillna(False).astype(bool).any():
            continue
        if pid in atz_ids:
            continue
        rows.append({
            "PersNr": str(pid), "Organisationseinheit": orgs[0], "Jobfamily": groups[0],
            "Ereignisart": event["reason_code"], "Ereignisdatum": event["event_date"],
            "Köpfe": float(event["_heads"]),
            "MAK": float(pd.to_numeric(pd.Series([event.get("mak_change", 0)]), errors="coerce").fillna(0).iloc[0]),
        })
    return pd.DataFrame(rows)


def derive_retained_departures(view: SimulationView, person_ids: list[str]) -> SimulationView:
    """Restore selected original people and remove only their saved departures."""
    selected = tuple(sorted(set(str(pid) for pid in person_ids)))
    if not selected:
        raise ValueError("Bitte mindestens einen Abgang auswählen.")
    candidates = eligible_departures(view)
    allowed = set(candidates.get("PersNr", pd.Series(dtype=str)))
    if not set(selected).issubset(allowed):
        raise ValueError("Mindestens ein gewählter Abgang ist für diese Justierung nicht eindeutig geeignet.")
    context = view.event_context
    source = context["source_snapshot"].copy(deep=True)
    original_raw = context["raw_final_snapshot"]
    if len(source) != len(original_raw) or not source.index.is_unique or not source.index.equals(original_raw.index):
        raise ValueError("Der Zielbestand enthält zusätzliche Zeilen; eine eindeutige Wiederherstellung ist nicht möglich.")
    base_date = pd.Timestamp(view.metadata["base_date"])
    target_date = pd.Timestamp(view.metadata["target_date"])
    df_atz = context.get("df_atz")
    if not isinstance(df_atz, pd.DataFrame):
        df_atz = pd.DataFrame()
    original_people = _person_ids(source, "PersNr")
    final_raw = original_raw.copy(deep=True)
    employee_state = context["future_employee_state"].copy(deep=True)
    employee_ids = _person_ids(employee_state, "PersNr")
    bundle = ClusterMappingBundle(**context["cluster_mapping"])
    cluster_source = ActiveClusterSource(
        mode="synthetic", subtype="synthetic.default_fallback",
        source_signature=bundle.source_signature,
    )
    for pid in selected:
        mask = original_people.eq(pid).fillna(False).to_numpy(dtype=bool)
        rows = source.iloc[mask].copy(deep=True)
        if rows.empty or not final_raw.iloc[mask]["Is_Vacant"].fillna(False).all():
            raise ValueError(f"Planstellen von {pid} sind im Zielbestand nicht eindeutig vakant.")
        employee = _prepare_employee_forecast_base(rows, df_atz, base_date)
        if len(employee) != 1:
            raise ValueError(f"Ausgangsbestand von {pid} ist nicht eindeutig.")
        employee = _apply_salary_automation_to_employee_state(employee, target_date, pd.DataFrame())
        restored = _update_existing_rows(rows, employee, target_date, pd.DataFrame())
        restored = _finalize_future_snapshot(
            restored, target_date,
            active_cluster_source=cluster_source, cluster_mapping_bundle=bundle,
        )
        for column in final_raw.columns.intersection(restored.columns):
            final_raw.loc[final_raw.index[mask], column] = restored[column].to_numpy()
        state_mask = employee_ids.eq(pid).fillna(False).to_numpy(dtype=bool)
        if state_mask.sum() != 1:
            raise ValueError(f"Personalzustand von {pid} ist nicht eindeutig.")
        for column in employee_state.columns.intersection(employee.columns):
            employee_state.loc[employee_state.index[state_mask], column] = employee[column].to_numpy()

    prepared = load_compact_page_module().prepare_compact_data(final_raw)
    original_prepared = view.prepared_df
    outside = ~original_people.isin(selected).fillna(False).to_numpy(dtype=bool)
    common = original_prepared.columns.intersection(prepared.columns)
    try:
        pd.testing.assert_frame_equal(
            original_prepared.loc[outside, common].reset_index(drop=True),
            prepared.loc[outside, common].reset_index(drop=True),
            check_dtype=False,
        )
    except AssertionError as exc:
        raise ValueError("Die Justierung würde unbeteiligte Personen verändern.") from exc

    # The saved source, selection audits and lookup data are read only. Share
    # those large objects; replace only the fields changed by this derivation.
    event_context = context.copy()
    departures = context["departure_events"]
    retained = ~_person_ids(departures, "persnr").isin(selected).fillna(False)
    event_context["departure_events"] = departures.loc[retained].copy(deep=True)
    event_context["final_snapshot"] = prepared
    event_context["raw_final_snapshot"] = final_raw
    event_context["future_employee_state"] = employee_state
    rule = {"kind": "retain_departure", "persons": selected}
    digest = hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()[:12]
    version = f"{view.signature}::{view.instance}::adjusted::{digest}"
    event_context["run_signature"] = version
    metadata = deepcopy(view.metadata)
    metadata["adjustment"] = rule
    metadata["base_run_signature"] = view.signature
    metadata["selection_audit_provenance"] = "Originaler Zufalls- und Auswahlverlauf; dargestellte Abgänge folgen der Justierung"
    metadata["abgaenge_events"] = len(event_context["departure_events"])
    metadata["vacancy_count"] = int(final_raw["Is_Vacant"].fillna(False).sum())
    event_context["metadata"] = metadata
    summary = build_simulation_event_summary(event_context, expected_run_signature=version)
    summary["adjustment"] = rule
    bridge = summary.get("stock_reconciliation", {})
    if bridge.get("status") != "Abgestimmt":
        raise ValueError("Der korrigierte Bestand ist nicht abgestimmt: " + str(bridge.get("reason") or bridge.get("status")))
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    for dimension in ("Jobfamily", "JF-Cluster"):
        yearly = build_jobfamily_yearly_view(event_context, summary, dimension=dimension)
        checks = yearly.get("stock_checks")
        if yearly.get("stock_reason") or not isinstance(checks, pd.DataFrame) or checks.empty or not checks["Abgestimmt"].all():
            raise ValueError(f"Die Jobgruppen-Bestandsüberleitung ({dimension}) ist nicht abgestimmt: {yearly.get('stock_reason', '')}")

    audits = _build_simulation_audit_tables(
        future_snapshot_df=final_raw,
        final_employee_df=employee_state,
        zugaenge_result={"events": pd.DataFrame(), "final_state": employee_state},
        target_date=target_date,
        abgaenge_params=context.get("abgaenge_params", {}),
        lineage_stages={"01_rohdaten_ausgangsbestand": source, "07_final_future_snapshot": final_raw},
        base_snapshot_df=source,
        abgaenge_result={"events_person_level": event_context["departure_events"], "tables": {}},
    )
    return SimulationView(
        signature=view.signature, instance=view.instance, prepared_df=prepared,
        status_quo_df=view.status_quo_df, metadata=metadata,
        audit_tables=audits, event_context=event_context,
        event_summary=summary, export_context=deepcopy(view.export_context),
        version=version, approved=False,
    )


def _shifted_period(original_date: pd.Timestamp, year: int) -> pd.Period:
    """Keep the original event's month in the new year (monthly forecast)."""
    return pd.Period(year=year, month=original_date.month, freq="M")


def eligible_timing_shifts(view: SimulationView) -> pd.DataFrame:
    """Only saved, single-OE quits whose MAK matches the occupied OE stock."""
    if view.event_summary.get("stock_reconciliation", {}).get("status") != "Abgestimmt":
        return pd.DataFrame()
    candidates = eligible_departures(view)
    if candidates.empty:
        return candidates
    source = view.event_context.get("baseline_snapshot")
    events = view.event_context.get("departure_events")
    if not isinstance(source, pd.DataFrame) or not isinstance(events, pd.DataFrame):
        return pd.DataFrame()
    target = pd.Timestamp(view.metadata["target_date"]).normalize()
    base = pd.Timestamp(view.metadata["base_date"]).normalize()
    source_ids = _person_ids(source, "PersNr")
    rows = []
    for _, candidate in candidates.loc[candidates["Ereignisart"].eq("QUIT")].iterrows():
        pid = candidate["PersNr"]
        old = pd.Timestamp(candidate["Ereignisdatum"])
        if pd.isna(old) or old.normalize() < base or old.normalize() > target:
            continue
        if old != old.to_period("M").end_time:
            continue
        years = [year for year in range(old.year + 1, target.year + 1)
                 if _shifted_period(old, year).end_time.normalize() <= target]
        if not years:
            continue
        person = source.loc[source_ids.eq(pid).fillna(False)]
        if "Austritt" in person:
            contract_ends = pd.to_datetime(person["Austritt"], errors="coerce").dropna()
            if not contract_ends.empty:
                years = [year for year in years if _shifted_period(old, year).end_time.normalize()
                         <= contract_ends.min().normalize()]
        params = view.event_context.get("abgaenge_params", {})
        if params.get("components", {}).get("retirement", True):
            retirement = params.get("retirement", {})
            try:
                regular_age, early_age = get_retirement_ages(retirement)
                early_rate = float(retirement.get("rent_rate_60_65", 0.1))
                regular_rate = float(retirement.get("rent_rate_65", 0.9))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(early_rate) or not math.isfinite(regular_rate):
                continue
            if "GebDatum" not in person:
                continue
            births = pd.to_datetime(person["GebDatum"], errors="coerce").dropna().unique()
            if len(births) != 1:
                continue
            threshold = early_age if early_rate > 0 else regular_age
            if regular_rate <= 0 and early_rate <= 0:
                threshold = 121
            eligible_until = pd.Timestamp(births[0]) + pd.DateOffset(years=threshold)
            years = [year for year in years if _shifted_period(old, year).end_time < eligible_until]
        if not years:
            continue
        org = candidate["Organisationseinheit"]
        oe = source.loc[source["Organisationseinheit"].astype(str).str.strip().eq(org)]
        person_mak, oe_mak = raw_stock_mak(person), raw_stock_mak(oe)
        event_mak = -float(candidate["MAK"])
        if (not all(math.isfinite(value) for value in (person_mak, oe_mak, event_mak))
                or person_mak <= 1e-6 or event_mak <= 1e-6
                or abs(event_mak - person_mak) > 1e-6
                or event_mak > oe_mak + 1e-6):
            continue
        rows.append({**candidate.to_dict(), "Personen-MAK in OE": person_mak,
                     "OE-Ausgangs-MAK": oe_mak, "Zulässige Jahre": tuple(years)})
    return pd.DataFrame(rows)


def derive_shifted_departure(view: SimulationView, person_id: str, new_year: int,
                             reason: str = "") -> SimulationView:
    """Shift a saved quit event; preserve the original target snapshot exactly."""
    candidates = eligible_timing_shifts(view)
    selected = candidates.loc[candidates.get("PersNr", pd.Series(dtype=str)).eq(str(person_id))]
    if len(selected) != 1:
        raise ValueError("Dieser Kündigungsabgang ist für eine Terminverschiebung nicht eindeutig geeignet.")
    candidate = selected.iloc[0]
    if new_year not in candidate["Zulässige Jahre"]:
        raise ValueError("Das neue Jahr liegt außerhalb der zulässigen späteren Perioden.")
    old = pd.Timestamp(candidate["Ereignisdatum"])
    period = _shifted_period(old, new_year)
    context = view.event_context
    event_context = context.copy()
    departures = context["departure_events"].copy(deep=True)
    mask = _person_ids(departures, "persnr").eq(str(person_id)).fillna(False)
    if int(mask.sum()) != 1:
        raise ValueError("Der gespeicherte Abgang ist nicht eindeutig.")
    index = departures.index[mask][0]
    departures.at[index, "event_date"] = period.end_time
    for column, value in (("period_start", period.start_time), ("period_end", period.end_time),
                          ("period_label", period.strftime("%Y-%m"))):
        if column in departures:
            departures.at[index, column] = value
    event_context["departure_events"] = departures
    rule = {"kind": "shift_departure", "persons": (str(person_id),),
            "organization": candidate["Organisationseinheit"], "jobfamily": candidate["Jobfamily"],
            "old_date": old.isoformat(), "new_date": period.end_time.isoformat(),
            "heads": float(candidate["Köpfe"]), "mak": float(candidate["MAK"]),
            "reason": reason.strip()}
    digest = hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()[:12]
    version = f"{view.signature}::{view.instance}::adjusted::{digest}"
    event_context["run_signature"] = version
    metadata = deepcopy(view.metadata)
    metadata["adjustment"] = rule
    metadata["base_run_signature"] = view.signature
    metadata["selection_audit_provenance"] = "Originaler Zufalls- und Auswahlverlauf; ein Kündigungsdatum wurde nachträglich verschoben"
    metadata["annual_costs_not_recomputed"] = True
    event_context["metadata"] = metadata
    summary = build_simulation_event_summary(event_context, expected_run_signature=version)
    summary["adjustment"] = rule
    bridge = summary.get("stock_reconciliation", {})
    if bridge.get("status") != "Abgestimmt":
        raise ValueError("Die OE-Bestandsüberleitung ist nach der Verschiebung nicht abgestimmt: "
                         + str(bridge.get("reason") or bridge.get("status")))
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    for dimension in ("Jobfamily", "JF-Cluster"):
        yearly = build_jobfamily_yearly_view(event_context, summary, dimension=dimension)
        checks = yearly.get("stock_checks")
        if yearly.get("stock_reason") or not isinstance(checks, pd.DataFrame) or checks.empty or not checks["Abgestimmt"].all():
            raise ValueError(f"Die Jobgruppen-Bestandsüberleitung ({dimension}) ist nicht abgestimmt: {yearly.get('stock_reason', '')}")
    original_annual = view.event_summary.get("stock_reconciliation", {}).get("annual")
    shifted_annual = bridge.get("annual")
    if not isinstance(original_annual, pd.DataFrame) or not isinstance(shifted_annual, pd.DataFrame):
        raise ValueError("Die Jahresscheiben des Originallaufs fehlen.")
    def stock_by_year(frame):
        scoped = frame.loc[frame["Ebene"].eq("Organisationseinheit")
                           & frame["Organisation"].eq(candidate["Organisationseinheit"])
                           & frame["Art"].eq("Jahresstichtag")]
        return scoped.set_index("Jahr")[["Bestand-Köpfe", "Bestand-MAK"]]
    before, after = stock_by_year(original_annual), stock_by_year(shifted_annual)
    if not before.index.equals(after.index):
        raise ValueError("Die Verschiebung hat die Jahresscheiben verändert.")
    expected_heads, expected_mak = -float(candidate["Köpfe"]), -float(candidate["MAK"])
    for year in before.index:
        affected = old.year <= year < new_year
        head_diff = float(after.loc[year, "Bestand-Köpfe"] - before.loc[year, "Bestand-Köpfe"])
        mak_diff = float(after.loc[year, "Bestand-MAK"] - before.loc[year, "Bestand-MAK"])
        if abs(head_diff - (expected_heads if affected else 0.0)) > 1e-6 or abs(mak_diff - (expected_mak if affected else 0.0)) > 1e-6:
            raise ValueError("Die Jahreswirkung des verschobenen Abgangs ist nicht eindeutig.")
    raw_final = context.get("raw_final_snapshot")
    employee_state = context.get("future_employee_state")
    source = context.get("source_snapshot")
    if not all(isinstance(frame, pd.DataFrame) for frame in (raw_final, employee_state, source)):
        raise ValueError("Gespeicherter Ziel- oder Ausgangsbestand für den Export fehlt.")
    audits = _build_simulation_audit_tables(
        future_snapshot_df=raw_final, final_employee_df=employee_state,
        zugaenge_result={"events": pd.DataFrame(), "final_state": employee_state},
        target_date=pd.Timestamp(view.metadata["target_date"]),
        abgaenge_params=context.get("abgaenge_params", {}),
        lineage_stages={"01_rohdaten_ausgangsbestand": source, "07_final_future_snapshot": raw_final},
        base_snapshot_df=source,
        abgaenge_result={"events_person_level": departures, "tables": {}},
    )
    return SimulationView(
        signature=view.signature, instance=view.instance, prepared_df=view.prepared_df,
        status_quo_df=view.status_quo_df, metadata=metadata, audit_tables=audits,
        event_context=event_context, event_summary=summary,
        export_context=deepcopy(view.export_context), version=version, approved=False,
    )
