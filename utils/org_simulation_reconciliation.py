"""Read-only support for reconciling events with the same run's stock snapshots."""
from __future__ import annotations

import pandas as pd

from abgaenge.schemas import normalize_persnr
from dataloader.kpi_engine import compute_headcount, compute_fte_effektiv, get_unique_employees

MAK_TOLERANCE = 1e-6  # Raw capacities; presentation alone rounds to two places.


def documented_atz_capacity_effects(context, projection, final, events):
    """Recognize only an undated ATZ capacity recovery supported by saved run data."""
    params = context.get("abgaenge_params", {})
    if not isinstance(params, dict) or params.get("components", {}).get("atz") is not False:
        return pd.DataFrame()
    before, after = _occupied(projection), _occupied(final)
    required = {"ist_atz_fr", "Personen_MAK_Source", "MAK_Reporting", "Organisationseinheit"}
    if not required.issubset(before.columns) or not required.issubset(after.columns):
        return pd.DataFrame()
    event_ids = set(normalize_persnr(events.get("persnr", pd.Series(dtype=object))).dropna())
    rows = []
    for pid in sorted(set(before._person) & set(after._person) - event_ids):
        old, new = before.loc[before._person.eq(pid)], after.loc[after._person.eq(pid)]
        if not old["ist_atz_fr"].fillna(False).astype(bool).all() or new["ist_atz_fr"].fillna(False).astype(bool).any():
            continue
        old_orgs, new_orgs = set(old.Organisationseinheit), set(new.Organisationseinheit)
        if len(old_orgs) != 1 or old_orgs != new_orgs:
            continue
        old_mak = pd.to_numeric(old.MAK_Reporting, errors="coerce").fillna(0).sum()
        new_mak = pd.to_numeric(new.MAK_Reporting, errors="coerce").fillna(0).sum()
        source_mak = pd.to_numeric(old.Personen_MAK_Source, errors="coerce").dropna()
        delta = float(new_mak - old_mak)
        if abs(old_mak) > MAK_TOLERANCE or delta <= MAK_TOLERANCE or source_mak.empty:
            continue
        if abs(float(source_mak.iloc[0]) - delta) > MAK_TOLERANCE:
            continue
        rows.append({"PersNr": pid, "Organisationseinheit": next(iter(old_orgs)),
                     "Köpfe-Effekt": 0, "MAK-Effekt": delta,
                     "Ursache": "ATZ-Freistellungsstatus endet ohne ATZ-Ereignis",
                     "Zeitbezug": "Bis Zielstichtag; Jahr nicht belegt"})
    return pd.DataFrame(rows)


def raw_stock_mak(snapshot: pd.DataFrame) -> float:
    for column in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK"):
        if column in snapshot:
            columns = [name for name in ("PersNr", "Is_Vacant", column) if name in snapshot]
            employees = get_unique_employees(snapshot[columns])
            return float(pd.to_numeric(employees[column], errors="coerce").fillna(0).sum())
    # Legacy fallback has no raw reporting capacity available.
    return compute_fte_effektiv(snapshot)


def _occupied(snapshot):
    rows = snapshot.copy(deep=True)
    if "Is_Vacant" in rows:
        rows = rows.loc[~rows.Is_Vacant.fillna(True).astype(bool)].copy()
    rows["_person"] = normalize_persnr(rows.PersNr)
    return rows.loc[rows._person.notna()].copy()


def _person_mak_budgets(rows):
    """Sum occupied reporting capacity once per person for event replay.

    ``rows`` already passed through ``_occupied``. The old per-person call to
    ``raw_stock_mak`` rebuilt a full employee table for every person, although
    its result is simply the sum of that person's position capacities.
    """
    column = next((c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in rows), None)
    if column is None:
        return {pid: raw_stock_mak(group) for pid, group in rows.groupby("_person")}
    capacity = pd.to_numeric(rows[column], errors="coerce").fillna(0)
    # Preserve the old two-stage order: get_unique_employees first summed by
    # raw PersNr, then raw_stock_mak summed those person rows. This also keeps
    # mixed raw identifiers with one normalized ID numerically consistent.
    by_raw_id = capacity.groupby([rows["_person"], rows["PersNr"]], sort=True).sum()
    return by_raw_id.groupby(level=0, sort=True).sum().to_dict()


def _person_scope_weights(initial, dimension):
    original = _occupied(initial)
    cap = next((c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in original), None)
    weight = next((c for c in ("Allocation_Weight", "Soll_FTE") if c in original), None)
    original["_capacity"] = pd.to_numeric(original[cap], errors="coerce").fillna(0) if cap else 0.0
    original["_weight"] = pd.to_numeric(original[weight], errors="coerce").fillna(0) if weight else 1.0
    totals = original.groupby("_person").agg(capacity=("_capacity", "sum"), weight=("_weight", "sum"), rows=("_person", "size"))
    members = original.groupby(["_person", dimension]).agg(capacity=("_capacity", "sum"), weight=("_weight", "sum"), rows=("_person", "size"))
    scopes = {}
    for (pid, group), member in members.iterrows():
        total = totals.loc[pid]
        ratio = member.capacity / total.capacity if total.capacity > 1e-12 else (
            member.weight / total.weight if total.weight > 0 else member.rows / total.rows)
        scopes.setdefault(group, {})[pid] = ratio
    return scopes


def _scope_departure_changes(initial, events, dimension, name, allocations=None):
    """One head per original person/group; capacity uses saved row distribution.

    This changes reporting allocation only, never raw forecast events. Counts
    across groups can be non-additive when a person belongs to several groups.
    """
    if events.empty:
        return events.copy()
    allocations = allocations if allocations is not None else _person_scope_weights(initial, dimension)
    members = allocations.get(name, {})
    ids = events["_bridge_person"]
    selected = ids.isin(members) | (~events["_bridge_known"] & events[dimension].eq(name))
    output = []
    for index, event in events.loc[selected].iterrows():
        ratio = members.get(ids.loc[index], 1.0)
        row = event.copy()
        row[dimension] = name
        row["MAK-Veränderung"] = float(event["MAK-Veränderung"]) * ratio
        output.append(row)
    return pd.DataFrame(output, columns=events.columns) if output else events.iloc[:0].copy()


def person_stock_diagnostics(initial, final, events):
    """Explain remaining discrepancies, without assigning an invented year."""
    before, after = _occupied(initial), _occupied(final)
    event_ids = normalize_persnr(events.get("persnr", events.get("PersNr", pd.Series(pd.NA, index=events.index))))
    def totals(frame):
        column = next((c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in frame), None)
        frame = frame.copy()
        frame["_mak"] = pd.to_numeric(frame[column], errors="coerce").fillna(0) if column else 0.0
        return frame.groupby("_person").agg(mak=("_mak", "sum"), orgs=("Organisationseinheit", lambda values: sorted(set(values))))
    old_totals, new_totals = totals(before), totals(after)
    event_totals = events.assign(_person=event_ids).groupby("_person")[["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
    ids = sorted(set(before._person) | set(after._person) | set(event_ids.dropna()))
    rows = []
    for pid in ids:
        hc_before, hc_after = int(pid in old_totals.index), int(pid in new_totals.index)
        mak_before = old_totals.loc[pid, "mak"] if hc_before else 0.0
        mak_after = new_totals.loc[pid, "mak"] if hc_after else 0.0
        hc_change = event_totals.loc[pid, "Köpfe-Veränderung"] if pid in event_totals.index else 0.0
        mak_change = event_totals.loc[pid, "MAK-Veränderung"] if pid in event_totals.index else 0.0
        rest_hc, rest_mak = hc_after - hc_before - hc_change, mak_after - mak_before - mak_change
        old_orgs = old_totals.loc[pid, "orgs"] if hc_before else []
        new_orgs = new_totals.loc[pid, "orgs"] if hc_after else []
        causes = []
        if new_orgs and old_orgs != new_orgs:
            causes.append("OE-Zuordnung im Endbestand verändert; Zeitpunkt nicht dokumentiert")
        if rest_hc:
            causes.append("Kopfzahländerung nicht durch Personalereignisse erklärt")
        if abs(rest_mak) > MAK_TOLERANCE:
            causes.append("MAK-Aufbereitung weicht von Ereigniswirkung ab")
        if causes:
            rows.append({"PersNr": pid, "Ausgangs-OEs": " | ".join(old_orgs), "Finale OEs": " | ".join(new_orgs),
                         "Ausgangs-Köpfe": hc_before, "Finale Köpfe": hc_after, "Ereignis-Köpfe": hc_change,
                         "Unerklärte Kopfänderung": rest_hc, "Ausgangs-MAK": mak_before, "Finale MAK": mak_after,
                         "Ereignis-MAK": mak_change, "Unerklärte MAK-Änderung": rest_mak,
                         "Diagnose": "; ".join(causes)})
    return pd.DataFrame(rows)


def complete_departure_org_assignments(events: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    """Fill only missing OEs with an unambiguous original person assignment.

    Multiple position rows in one OE are fine. Different OEs are explicitly
    ambiguous: never allocate the whole event to the first position.
    """
    out = events.copy(deep=True)
    org = out.get("Organisationseinheit", pd.Series(pd.NA, index=out.index))
    missing = org.isna() | org.astype(str).str.strip().isin(["", "nan", "None", "Nicht zugeordnet"])
    out["Organisationseinheit"] = org
    out["OE-Zuordnungsherkunft"] = "Ereignis"
    out.loc[missing, "OE-Zuordnungsherkunft"] = "Nicht auflösbar"
    if not missing.any() or baseline is None or not {"PersNr", "Organisationseinheit"}.issubset(baseline.columns):
        return out
    original = baseline.copy(deep=True)
    if "Is_Vacant" in original:
        original = original.loc[~original["Is_Vacant"].fillna(True).astype(bool)].copy()
    original["_person"] = normalize_persnr(original["PersNr"])
    original["_org"] = original["Organisationseinheit"].fillna("").astype(str).str.strip()
    original = original.loc[original["_person"].notna() & original["_org"].ne("")]
    assignments = original.groupby("_person")["_org"].agg(lambda values: sorted(set(values)))
    ids = normalize_persnr(out.get("persnr", out.get("PersNr", pd.Series(pd.NA, index=out.index))))
    for index in out.index[missing]:
        choices = assignments.get(ids.loc[index], [])
        if len(choices) == 1:
            out.loc[index, "Organisationseinheit"] = choices[0]
            out.loc[index, "OE-Zuordnungsherkunft"] = "Gespeicherter Ausgangsbestand"
        elif len(choices) > 1:
            out.loc[index, "OE-Zuordnungsherkunft"] = "Mehrdeutige Ausgangszuordnung"
    return out


def reporting_projection_base(context, initial):
    """Re-use deterministic reporting rules when entering the forecast view.

    This is not an inferred business transfer: the existing routine copies its
    first person OE to all position rows for every nonzero projection horizon.
    No forecast, random draw, current settings or input-file load is performed.
    """
    if "run_settings" not in context:
        return initial.copy(deep=True)
    from dataloader.compact_simulation_engine import _update_existing_rows
    from dataloader.loader import calculate_mak_vectorized, apply_exclusions, _zero_out_azubi_mak
    from dataloader.mak_allocation import apply_person_mak_allocation
    from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
    from dataloader.cluster_resolver import ActiveClusterSource, ClusterMappingBundle
    employees = get_unique_employees(initial).copy()
    column = next(c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in employees)
    employees["mak"] = pd.to_numeric(employees[column], errors="coerce").fillna(0)
    employees["active"] = True
    atz_columns = [c for c in ("PersNr", "ATZ_Status", "ist_atz_fr") if c in initial]
    atz = initial[atz_columns].drop_duplicates("PersNr") if len(atz_columns) == 3 else pd.DataFrame()
    base = _update_existing_rows(initial, employees, pd.Timestamp(context["metadata"]["base_date"]), atz)
    base = calculate_mak_vectorized(base)
    bundle = ClusterMappingBundle(**context.get("cluster_mapping", {}))
    source = ActiveClusterSource(mode="synthetic", subtype="synthetic.default_fallback", source_signature=bundle.source_signature)
    base = apply_clusters_to_snapshot_from_source(base, source, mapping_bundle=bundle)
    base = _zero_out_azubi_mak(base)
    base = apply_exclusions(base, context["run_settings"].get("exclusions", {}), record_counts=False)
    return apply_person_mak_allocation(base)


def reporting_departure_events(projection, events, context):
    """Evaluate capacity effects under existing reporting masks, keeping raw events."""
    if "run_settings" not in context or events.empty:
        return events.copy(deep=True)
    from dataloader.loader import apply_exclusions, calculate_mak_vectorized
    from dataloader.mak_allocation import apply_person_mak_allocation
    potential = projection.copy(deep=True)
    if "Status kundenindividuell" in potential:
        potential.loc[potential["Status kundenindividuell"].eq("Ruhendes Beschäftigungsverhältnis"), "Status kundenindividuell"] = "Aktives Beschäftigungsverhältnis"
    potential = calculate_mak_vectorized(potential)
    potential = apply_exclusions(potential, context["run_settings"].get("exclusions", {}), record_counts=False)
    potential = apply_person_mak_allocation(potential)
    original = _occupied(projection)
    budget = _person_mak_budgets(original)
    recovery = _person_mak_budgets(_occupied(potential))
    fr = set(original.loc[original.get("ist_atz_fr", pd.Series(False, index=original.index)).fillna(False).astype(bool), "_person"])
    out = events.copy(deep=True)
    out["Original MAK-Veränderung"] = out["MAK-Veränderung"]
    out["Berichtseffekt-Regel"] = "Unverändertes Ereignis"
    ids = normalize_persnr(out.get("persnr", out.get("PersNr", pd.Series(pd.NA, index=out.index))))
    for index in out.sort_values("event_date", kind="stable").index:
        pid, kind = ids.loc[index], out.loc[index, "Ereignisart"]
        if pid not in budget:
            continue
        previous = budget[pid]
        if kind in ("QUIT", "RETIREMENT", "ATZ_END", "ATZ_AR_TO_FR", "RUHEND_START"):
            budget[pid] = 0.0
            if kind == "ATZ_AR_TO_FR":
                fr.add(pid)
        elif kind == "RUHEND_RETURN":
            budget[pid] = 0.0 if pid in fr else recovery.get(pid, previous)
        else:
            continue
        out.loc[index, "MAK-Veränderung"] = budget[pid] - previous
        out.loc[index, "Berichtseffekt-Regel"] = "Kapazität nach gespeicherten Reporting-/Exklusionsregeln"
    return out


def build_departure_stock_bridge(context: dict, events: pd.DataFrame, *, expected_run_signature: str) -> dict:
    """Arithmetic bridge only; never simulate, replay states or balance differences."""
    if context.get("run_signature") != expected_run_signature:
        raise ValueError("Bestandsüberleitung und Simulation gehören nicht zum selben Lauf.")
    result = {"run_signature": expected_run_signature, "status": "Nicht verfügbar", "reason": "",
              "annual": pd.DataFrame(), "checks": pd.DataFrame(), "person_diagnostics": pd.DataFrame(),
              "mak_tolerance": MAK_TOLERANCE}
    params = context.get("zugaenge_params")
    if not isinstance(params, dict) or any(params.get(k, {}).get("active", True) is not False
                                           for k in ("azubi", "trainee", "new_hires")):
        result["reason"] = "Nur für Läufe mit ausdrücklich deaktivierten Zugangsbausteinen verfügbar."
        return result
    if not events.empty and events["Ereignisquelle"].ne("departure_events").any():
        result["reason"] = "Der gespeicherte Lauf enthält Zugangsereignisse."
        return result
    if not events.empty and events["Kategorie"].eq("Sonstige Veränderungen").any():
        result["reason"] = "Unbekannte Ereignisarten können nicht als Bestandsüberleitung bestätigt werden."
        return result
    initial, final = context.get("baseline_snapshot"), context.get("final_snapshot")
    if initial is None or final is None or any(not {"PersNr", "Organisationseinheit"}.issubset(df.columns)
                                               for df in (initial, final)):
        result["reason"] = "Gespeicherte Ausgangs- oder Endbasis fehlt; Simulation explizit neu berechnen."
        return result
    if not events.empty and not {"persnr", "PersNr"}.intersection(events.columns):
        result["reason"] = "Personalnummern in den Ereignissen fehlen; personenbezogene Zuordnung ist nicht prüfbar."
        return result
    if any(not {"MAK_Reporting", "MAK_Calculated", "mak", "MAK"}.intersection(df.columns) for df in (initial, final)):
        result["reason"] = "Ungerundete Kapazitätsspalten in der gespeicherten Bestandsbasis fehlen."
        return result
    try:
        start = pd.Timestamp(context["metadata"]["base_date"]).normalize()
        end = pd.Timestamp(context["metadata"]["target_date"]).normalize()
        if pd.isna(start) or pd.isna(end) or end < start:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        result["reason"] = "Ungültiger oder fehlender Laufzeitraum."
        return result
    if not events.empty and ((events.event_date.dt.normalize() < start) | (events.event_date.dt.normalize() > end)).any():
        result["reason"] = "Ereignisse liegen außerhalb des gespeicherten Laufzeitraums."
        return result
    mapping = context.get("cluster_mapping", {}).get("oe_map", {})
    def prepare(df):
        work = df.copy(deep=True)
        work["Organisationseinheit"] = work.Organisationseinheit.fillna("Nicht zugeordnet").astype(str).str.strip().replace("", "Nicht zugeordnet")
        work["OE-Cluster"] = work.Organisationseinheit.map(mapping).fillna("Nicht zugeordnet")
        return work
    initial, final = prepare(initial), prepare(final)
    projection = prepare(reporting_projection_base(context, initial)) if end > start else initial.copy(deep=True)
    raw_events = events
    events = reporting_departure_events(projection, events, context)
    atz_effects = documented_atz_capacity_effects(context, projection, final, raw_events)
    if not atz_effects.empty:
        atz_effects = atz_effects.copy()
        atz_effects["OE-Cluster"] = atz_effects.Organisationseinheit.map(mapping).fillna("Nicht zugeordnet")
    result["documented_effects"] = atz_effects
    events = events.copy(deep=True)
    events["_bridge_person"] = normalize_persnr(events.get("persnr", events.get("PersNr", pd.Series(pd.NA, index=events.index))))
    events["_bridge_known"] = events["_bridge_person"].isin(set(_occupied(initial)._person))
    groups = [("Gesamt", "Gesamt", initial, final, events)]
    for dimension in ("Organisationseinheit", "OE-Cluster"):
        allocations = _person_scope_weights(projection, dimension)
        names = set(initial[dimension]) | set(final[dimension]) | set(events[dimension])
        groups.extend((dimension, name, initial[initial[dimension] == name], final[final[dimension] == name],
                       _scope_departure_changes(projection, events, dimension, name, allocations)) for name in sorted(names))
    annual, checks, basis_effects = [], [], []
    for level, name, before, after, changes in groups:
        hc, mak = compute_headcount(before), raw_stock_mak(before)
        projected_before = projection if level == "Gesamt" else projection.loc[projection[level].eq(name)]
        basis_hc, basis_mak = compute_headcount(projected_before) - hc, raw_stock_mak(projected_before) - mak
        if basis_hc != 0 or abs(basis_mak) > MAK_TOLERANCE:
            basis_effects.append({"Ebene": level, "Organisation": name, "Köpfe-Effekt": basis_hc,
                                  "MAK-Effekt": basis_mak, "Regel": "Technischer Übergang IST zur Prognosebasis; kein Personalereignis"})
        cumulative_hc, cumulative_mak = 0.0, 0.0
        basis_applied = False
        annual.append({"Ebene": level, "Organisation": name, "Stichtag": start, "Jahr": start.year,
                       "Art": "Ausgangsstichtag", "Ausgangs-Köpfe": hc, "Ausgangs-MAK": mak,
                       "Jahresänderung-Köpfe": 0.0, "Jahresänderung-MAK": 0.0,
                       "Technischer Effekt-Köpfe": 0.0, "Technischer Effekt-MAK": 0.0,
                       "Kumulierte Änderung-Köpfe": 0.0, "Kumulierte Änderung-MAK": 0.0,
                       "Bestand-Köpfe": hc, "Bestand-MAK": mak})
        if end > start:
            for year in range(start.year, end.year + 1):
                date = min(pd.Timestamp(year, 12, 31), end)
                yearly = changes[changes.Jahr == year]
                delta_hc, delta_mak = yearly["Köpfe-Veränderung"].sum(), yearly["MAK-Veränderung"].sum()
                technical_hc, technical_mak = 0.0, 0.0
                if not basis_applied and (date > start or not yearly.empty):
                    delta_hc += basis_hc
                    delta_mak += basis_mak
                    technical_hc, technical_mak = basis_hc, basis_mak
                    basis_applied = True
                cumulative_hc += delta_hc
                cumulative_mak += delta_mak
                annual.append({"Ebene": level, "Organisation": name, "Stichtag": date, "Jahr": year,
                               "Art": "Jahresstichtag", "Ausgangs-Köpfe": hc, "Ausgangs-MAK": mak,
                               "Jahresänderung-Köpfe": delta_hc, "Jahresänderung-MAK": delta_mak,
                               "Technischer Effekt-Köpfe": technical_hc, "Technischer Effekt-MAK": technical_mak,
                               "Kumulierte Änderung-Köpfe": cumulative_hc, "Kumulierte Änderung-MAK": cumulative_mak,
                               "Bestand-Köpfe": hc + cumulative_hc, "Bestand-MAK": mak + cumulative_mak})
        projected_hc, projected_mak = hc + cumulative_hc, mak + cumulative_mak
        final_hc, final_mak = compute_headcount(after), raw_stock_mak(after)
        effect_mak = 0.0 if atz_effects.empty else float(
            atz_effects["MAK-Effekt"].sum() if level == "Gesamt" else
            atz_effects.loc[atz_effects[level].eq(name), "MAK-Effekt"].sum())
        diff_hc, diff_mak = projected_hc - final_hc, projected_mak + effect_mak - final_mak
        # Comparing rounded values again is unsafe at half-centimal boundaries:
        # binary summation noise can put equivalent raw sums on opposite sides.
        passed = diff_hc == 0 and abs(diff_mak) <= MAK_TOLERANCE + 1e-9
        checks.append({"Ebene": level, "Organisation": name, "Berechnete Köpfe": projected_hc,
                       "Finale Köpfe": final_hc, "Differenz Köpfe": diff_hc, "Berechnete MAK": projected_mak + effect_mak,
                       "Finale MAK": final_mak, "Differenz MAK": diff_mak, "Abgestimmt": passed})
        if abs(effect_mak) > MAK_TOLERANCE:
            annual.append({"Ebene": level, "Organisation": name, "Stichtag": end, "Jahr": end.year,
                           "Art": "Undatierter Aufbereitungseffekt bis Zielstichtag",
                           "Ausgangs-Köpfe": hc, "Ausgangs-MAK": mak,
                           "Jahresänderung-Köpfe": 0.0, "Jahresänderung-MAK": 0.0,
                           "Technischer Effekt-Köpfe": 0.0, "Technischer Effekt-MAK": effect_mak,
                           "Kumulierte Änderung-Köpfe": cumulative_hc,
                           "Kumulierte Änderung-MAK": cumulative_mak + effect_mak,
                           "Bestand-Köpfe": projected_hc, "Bestand-MAK": projected_mak + effect_mak})
    result["annual"], result["checks"] = pd.DataFrame(annual), pd.DataFrame(checks)
    result["person_diagnostics"] = person_stock_diagnostics(projection, final, events)
    if not atz_effects.empty and not result["person_diagnostics"].empty:
        result["person_diagnostics"] = result["person_diagnostics"].loc[
            ~result["person_diagnostics"].PersNr.isin(atz_effects.PersNr)].copy()
    result["basis_effects"] = pd.DataFrame(basis_effects)
    result["reporting_events"] = events.drop(columns=["_bridge_person", "_bridge_known"], errors="ignore").copy()
    event_ids = normalize_persnr(events.get("persnr", events.get("PersNr", pd.Series(pd.NA, index=events.index))))
    known = set(_occupied(initial)._person)
    unresolved = not events.empty and (events.Organisationseinheit.eq("Nicht zugeordnet") & ~event_ids.isin(known)).any()
    negative = (result["annual"]["Bestand-Köpfe"] < 0).any() or (result["annual"]["Bestand-MAK"] < -MAK_TOLERANCE).any()
    result["status"] = "Abgestimmt" if result["checks"].Abgestimmt.all() and not unresolved and not negative else "Abweichung"
    failed = int((~result["checks"].Abgestimmt).sum())
    result["reason"] = "" if result["status"] == "Abgestimmt" else f"{failed} Endwertprüfungen abweichend; {len(result['person_diagnostics'])} Personen mit zusätzlichen Zuordnungs-/Aufbereitungseffekten. Keine zeitliche Verteilung ungeklärter Differenzen."
    return result
