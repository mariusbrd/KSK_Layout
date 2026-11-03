"""Compact annual departure values from an exact seeded event record stream."""
from __future__ import annotations

import numpy as np

from abgaenge.quit_batch_probe import PreparedQuitYearly, departure_annual_event_values
from abgaenge.quit_batch_probe import quit_annual_event_values
from utils.org_simulation_reconciliation import _person_scope_weights
from utils.monte_carlo_metric_packet import LEVELS
from utils.org_simulation_events import CAPACITY, DEPARTURES, INTERNAL, METRICS


def departure_annual_values(
    records: list[dict], yearly: PreparedQuitYearly,
) -> list[tuple[int, str, str, str, str, str, int | float]]:
    """Return packet-shaped annual values without display or employee frames.

    Ordering follows the four reference annual tables: level, year, group,
    category, event type, metric. The source event records must come from the
    exact seeded departure stream.
    """
    grouped = departure_annual_event_values(records, yearly)
    output = []
    for level in LEVELS:
        level_rows = [(*key, value) for key, value in grouped.items() if key[1] == level]
        for year, _, group, reason, (heads, mak) in sorted(
                level_rows, key=lambda item: (item[0], item[2], item[3])):
            if reason in DEPARTURES:
                category = "Personalabgang"
            elif reason in CAPACITY:
                category = "Kapazitätsänderung"
            elif reason in INTERNAL:
                category = "Interner Wechsel"
            else:
                category = "Sonstige Veränderungen"
            metrics = (heads, mak, max(-heads, 0) if reason in DEPARTURES else 0, 0)
            output.extend((year, level, group, category, reason, metric, value)
                          for metric, value in zip(METRICS, metrics))
    return output


def provisional_stock_values(
    openings: dict[tuple[str, str], tuple[int | float, float]],
    records: list[dict],
    yearly: PreparedQuitYearly,
    years: range,
    oe_scopes: dict[str, dict[str, list[tuple[str, float]]]],
) -> dict[tuple[int, str, str, str], float]:
    """Derive year-end heads/MAK without a target snapshot.

    The values are intentionally provisional: stock certification still needs
    the final reporting snapshot and its exclusions. Callers must not label
    these as reconciled or use them where technical basis effects are present.
    """
    changes = quit_annual_event_values(records, yearly)
    for level in ("Organisationseinheit", "OE-Cluster"):
        grouped: dict[tuple[int, str, str], list[tuple[int, float]]] = {}
        for event in records:
            person = str(event["persnr"])
            memberships = oe_scopes[level].get(person)
            if memberships is None:
                org = event.get("Organisationseinheit") or "Nicht zugeordnet"
                name = str(org).strip()
                if level == "OE-Cluster":
                    name = yearly.oe_map.get(name, "Nicht zugeordnet")
                memberships = [(name, 1.0)]
            for name, ratio in memberships:
                key = (event["event_date"].year, level, name)
                grouped.setdefault(key, []).append(
                    (event["headcount_change"], float(event["mak_change"]) * ratio))
        for key in [key for key in changes if key[1] == level]:
            del changes[key]
        for key, values in grouped.items():
            # The stock bridge sums each filtered year's Series via numpy.
            total = float(np.sum([mak for _, mak in values]))
            changes[key] = (sum(heads for heads, _ in values), total)
    groups = set(openings) | {(level, group) for _, level, group in changes}
    values = {}
    for level in LEVELS:
        for name in sorted(group for group_level, group in groups if group_level == level):
            opening_heads, opening_mak = openings.get((level, name), (0, 0.0))
            cumulative_heads, cumulative_mak = 0, 0.0
            for year in years:
                delta_heads, delta_mak = changes.get((year, level, name), (0, 0.0))
                cumulative_heads += delta_heads
                cumulative_mak += delta_mak
                values[(year, level, name, "Köpfe")] = float(opening_heads + cumulative_heads)
                values[(year, level, name, "MAK")] = float(opening_mak + cumulative_mak)
    return values


def prepare_oe_stock_scopes(projection) -> dict[str, dict[str, list[tuple[str, float]]]]:
    """Invert the reference bridge's original person/group allocation once."""
    result = {}
    for level in ("Organisationseinheit", "OE-Cluster"):
        memberships: dict[str, list[tuple[str, float]]] = {}
        for group, persons in _person_scope_weights(projection, level).items():
            for person, ratio in persons.items():
                memberships.setdefault(person, []).append((group, ratio))
        result[level] = memberships
    return result


def provisional_target_head_mak(
    stock_values: dict[tuple[int, str, str, str], float],
    target_year: int,
    opening_unique_heads: int,
    records: list[dict],
    level: str = "Organisationseinheit",
) -> dict[str, float]:
    """Collapse provisional year-end group stock to target heads and MAK.

    This is deliberately not a certified target packet: reporting effects,
    hires and EUR require independent final-state derivation.
    """
    if level not in LEVELS:
        raise ValueError(f"Unknown stock level: {level}")
    mak = [value for (year, scope, _, metric), value in stock_values.items()
           if year == target_year and scope == level and metric == "MAK"]
    if not mak:
        raise ValueError(f"No stock values for {level} in {target_year}")
    heads = opening_unique_heads + sum(
        int(event["headcount_change"]) for event in records
        if event["event_date"].year <= target_year)
    return {"heads": heads, "mak": float(np.sum(mak))}


def _existing_position_mak(
    source_positions,
    final_people,
    atz_fr_people=None,
    use_source_reporting=False,
) -> tuple:
    """Return occupied existing-row MAK and their active mask.

    The original IST distribution is retained for multi-position employees.
    This helper only covers existing people and is not yet an app calculation.
    """
    import pandas as pd

    positions = source_positions.loc[source_positions["PersNr"].notna(),
                                     ["PersNr", "MAK_Reporting"]].copy()
    positions["PersNr"] = positions["PersNr"].astype(str).str.strip().str.replace(
        r"\.0$", "", regex=True)
    original = pd.to_numeric(positions["MAK_Reporting"], errors="coerce").fillna(0.0)
    counts = positions.groupby("PersNr")["PersNr"].transform("size")
    person_total = original.groupby(positions["PersNr"]).transform("sum")
    people = final_people.drop_duplicates("PersNr", keep="last").set_index("PersNr")
    active = positions["PersNr"].map(people["active"]).fillna(False).astype(bool)
    final_mak = positions["PersNr"].map(people["mak"]).fillna(0.0).astype(float)
    weights = original / person_total.where(person_total.gt(0), np.nan)
    weights = weights.fillna(1.0 / counts)
    row_mak = (final_mak * weights).where(active, 0.0)
    if use_source_reporting and "Personen_MAK_Source" in people:
        source_capacity = positions["PersNr"].map(people["Personen_MAK_Source"])
        source_capacity = pd.to_numeric(source_capacity, errors="coerce")
        final_status = positions["PersNr"].map(
            people.get("Status kundenindividuell", pd.Series(dtype=object)))
        final_contract = positions["PersNr"].map(
            people.get("Vertragsart", pd.Series(dtype=object)))
        final_group = positions["PersNr"].map(
            people.get("MitarbGruppenbez.", pd.Series(dtype=object)))
        source_azubi = source_positions.loc[positions.index].get(
            "Ist_Azubi", pd.Series(False, index=positions.index))
        zero = (positions["PersNr"].isin(atz_fr_people or set())
                | final_status.astype(str).str.strip().str.lower().eq(
                    "ruhendes besch\u00e4ftigungsverh\u00e4ltnis")
                | final_contract.astype(str).str.strip().str.lower().eq("auszubildende")
                | final_group.astype(str).str.strip().str.lower().eq("auszubildende")
                | source_azubi.fillna(False).astype(bool))
        report = source_capacity.mask(zero, 0.0)
        use_source = counts.eq(1) & active & source_capacity.notna()
        row_mak = row_mak.where(~use_source, report)
    return row_mak, active


def target_mak_in_position_order(source_positions, final_people) -> float:
    """Sum existing occupied positions without building a future snapshot."""
    row_mak, active = _existing_position_mak(source_positions, final_people)
    # The app sums only occupied rows. Including zero-valued vacated rows can
    # change NumPy's reduction tree and the final floating-point bit pattern.
    return float(np.sum(row_mak.loc[active].to_numpy()))


def target_head_mak_with_hires(source_positions, final_people,
                               atz_fr_people=None, use_source_reporting=True,
                               excluded_new_people=None
                               ) -> dict[str, int | float]:
    """Probe exact target totals for existing and forecast people.

    Vacancy matching follows the reference append helper. The caller passes
    forecast IDs removed by final reporting exclusions. This remains a parity
    probe until all hire and exclusion variants have been checked.
    """
    import pandas as pd

    excluded_new_people = excluded_new_people or set()

    row_mak, active = _existing_position_mak(
        source_positions, final_people, atz_fr_people, use_source_reporting)
    positions = source_positions.reset_index(drop=True)
    values = [0.0] * len(positions)
    occupied = [False] * len(positions)
    for index, value in row_mak.items():
        if bool(active.loc[index]):
            values[index] = float(value)
            occupied[index] = True
    existing_ids = set(positions["PersNr"].dropna().astype(str).str.strip().str.replace(
        r"\.0$", "", regex=True))
    people = final_people.drop_duplicates("PersNr", keep="last").copy()
    people["PersNr"] = people["PersNr"].astype(str).str.strip().str.replace(
        r"\.0$", "", regex=True)
    people = people.set_index("PersNr")
    forecast = people.get("is_forecast", pd.Series(False, index=people.index))
    forecast = forecast.fillna(False).astype(bool)
    prefix = people.index.to_series().str.match(r"^(AZ|TR|NH)_", na=False)
    new = people[(people["active"] == True) & (forecast | prefix)]
    new = new[~new.index.isin(existing_ids | excluded_new_people)].sort_index()
    from heapq import heapify, heappop
    vacant_all = []
    vacant_org = {}
    vacant_exact = {}
    for index in range(len(positions)):
        if occupied[index]:
            continue
        vacant_all.append(index)
        org_key = str(positions.at[index, "Organisationseinheit"])
        plan_key = str(positions.at[index, "Planstelle"])
        vacant_org.setdefault(org_key, []).append(index)
        vacant_exact.setdefault((org_key, plan_key), []).append(index)
    heapify(vacant_all)
    for queue in (*vacant_org.values(), *vacant_exact.values()):
        heapify(queue)

    def first_free(queue):
        while queue and occupied[queue[0]]:
            heappop(queue)
        return queue[0] if queue else None

    for pid, person in new.iterrows():
        org = person.get("Organisationseinheit")
        planstelle = person.get("Planstelle")
        org_key = str(org)
        candidates = vacant_all if pd.isna(org) else vacant_org.get(org_key, [])
        index = first_free(candidates)
        if pd.notna(planstelle):
            if pd.isna(org):
                exact = min((first_free(queue) for (unit, position), queue
                             in vacant_exact.items() if position == str(planstelle)),
                            default=None, key=lambda value: float("inf") if value is None else value)
            else:
                exact = first_free(vacant_exact.get((org_key, str(planstelle)), []))
            if exact is not None:
                index = exact
        # The reference append helper carries existing person-source columns
        # into new rows. Missing BsGrd stays NaN; it does not fall back to the
        # event's technical `mak` in the reporting allocation.
        mak = 0.0
        for field, divisor in (("Personen_MAK_Source", 1.0),
                               ("BsGrd_Source", 100.0),
                               ("Personen_MAK", 1.0), ("BsGrd", 100.0)):
            value = pd.to_numeric(person.get(field), errors="coerce")
            if pd.notna(value):
                mak = max(0.0, float(value) / divisor)
                break
        if str(person.get("Vertragsart", "")).strip().lower() == "auszubildende":
            mak = 0.0
        if index is not None:
            values[index] = mak
            occupied[index] = True
        else:
            values.append(mak)
            occupied.append(True)
    return {"heads": int(people.loc[
        (people["active"] == True) & ~people.index.isin(excluded_new_people)
    ].index.nunique()),
            "mak": float(np.sum(np.asarray(values)[occupied]))}


REPORTING_POSITION_COLUMNS = (
    "PersNr", "Personalnummer", "Is_Vacant", "BsGrd", "MAK_Calculated",
    "MAK_Reporting", "MAK", "mak", "FTE_person", "FTE_assigned", "Soll_FTE",
    "Sollarbeitszeit", "Status kundenindividuell", "ist_atz_fr",
    "Phase", "Vertragsart", "TrfGr", "Jobfamily", "MitarbGruppenbez.",
    "K\u00fcrzel OrgEinheit", "Organisationseinheit", "Planstellennr",
    "Planstelle", "MA-Typ", "Ist_Azubi", "Personen_MAK_Source", "BsGrd_Source",
    "snapshot_BsGrd", "Personen_MAK", "_forecast_position_ist_mak",
    "GebDatum", "Eintritt", "Austritt", "Text Gsch", "ATZ_Status",
)


def prepare_target_position_model(source_positions):
    """Select reusable reporting inputs once before iterating over seeds."""
    return source_positions.loc[:, [column for column in REPORTING_POSITION_COLUMNS
                                    if column in source_positions]].copy()


def reporting_target_from_position_probe(
    updated_positions, target_date, exclusions,
) -> dict[str, int | float]:
    """Isolate the reference reporting stages after position updates.

    This diagnostic bridge intentionally reuses production transforms. It is
    not a many-seed implementation; it identifies which reporting rules must
    enter a prepared position model before the faster path can be certified.
    """
    import pandas as pd
    from dataloader.loader import (
        _zero_out_azubi_mak, apply_exclusions, calculate_mak_vectorized,
    )
    from dataloader.mak_allocation import apply_person_mak_allocation

    work = prepare_target_position_model(updated_positions)
    if "PersNr" in work:
        work["Is_Vacant"] = work["PersNr"].isna() | work["PersNr"].astype(str).str.strip().eq("")
    work = calculate_mak_vectorized(work)
    work = _zero_out_azubi_mak(work)
    work = apply_exclusions(work, exclusions, record_counts=False,
                            preserve_atz_fr_occupancy=True)
    work = apply_person_mak_allocation(work)
    if "_forecast_position_ist_mak" in work:
        desired = pd.to_numeric(work["_forecast_position_ist_mak"], errors="coerce")
        preserve = desired.notna() & work["PersNr"].notna() & work["Is_Vacant"].ne(True)
        work.loc[preserve, "MAK_Reporting"] = desired.loc[preserve].to_numpy()
    active = work.loc[work["Is_Vacant"].ne(True)]
    return {"heads": int(active["PersNr"].nunique()),
            "mak": float(pd.to_numeric(active["MAK_Reporting"], errors="coerce").fillna(0).sum())}


def target_head_mak_reporting_bridge(
    prepared_positions, final_people, target_date, atz_status,
    accession_events, exclusions,
) -> dict[str, int | float]:
    """Replay production reporting rules on a prepared position schema."""
    from dataloader.compact_simulation_engine import (
        _append_new_people_rows, _update_existing_rows,
    )
    positions = _update_existing_rows(
        prepared_positions, final_people, target_date, atz_status)
    positions = _append_new_people_rows(
        positions, prepared_positions, final_people,
        {"events": accession_events})
    return reporting_target_from_position_probe(positions, target_date, exclusions)


TARGET_PERSON_FIELDS = (
    "PersNr", "active", "mak", "Personen_MAK_Source", "BsGrd_Source",
    "Personen_MAK", "BsGrd", "Status kundenindividuell", "Vertragsart",
    "MitarbGruppenbez.", "is_forecast", "Organisationseinheit", "Planstelle",
    "TrfGr",
)


def prepare_target_people(employee_base):
    """Keep only person fields required by the target reporting calculation."""
    import pandas as pd

    fields = [field for field in TARGET_PERSON_FIELDS if field in employee_base]
    people = employee_base.loc[:, fields].drop_duplicates("PersNr", keep="last")
    return {str(row["PersNr"]): row for row in people.to_dict("records")}


def excluded_forecast_people(person_state, existing_ids, exclusions):
    """Apply the reference exclusion rules to the new forecast row shape.

    The reference append step replaces vacancy columns with a candidate made
    from person fields. Position codes and identifiers are therefore missing
    on the resulting new row, even when it occupies an existing vacancy.
    """
    import pandas as pd
    from dataloader.loader import apply_exclusions

    records = []
    for person_id, person in person_state.items():
        if (person_id in existing_ids or person.get("active") != True or
                not (person.get("is_forecast") is True or
                     person_id.startswith(("AZ_", "TR_", "NH_")))):
            continue
        records.append({
            "_forecast_id": person_id,
            "PersNr": person_id,
            "Personalnummer": person_id,
            "Is_Vacant": False,
            "Organisationseinheit": person.get("Organisationseinheit"),
            "Planstelle": person.get("Planstelle"),
            "Status kundenindividuell": person.get("Status kundenindividuell"),
            "MitarbGruppenbez.": person.get("MitarbGruppenbez."),
            "Vertragsart": person.get("Vertragsart"),
            "TrfGr": person.get("TrfGr"),
            "BsGrd": person.get("BsGrd"),
            "Sollarbeitszeit": 39.0,
            "Planstellennr": pd.NA,
            "Kürzel OrgEinheit": pd.NA,
            "MA-Typ": pd.NA,
        })
    if not records:
        return set()
    candidates = pd.DataFrame.from_records(records)
    filtered = apply_exclusions(candidates, exclusions, record_counts=False)
    return set(filtered.loc[filtered["Is_Vacant"].eq(True), "_forecast_id"].astype(str))


def replay_target_people_compact(prepared_people, departure_records, new_people,
                                 *, as_dict=False):
    """Apply exact departure state transitions without a full employee frame."""
    import pandas as pd

    state = {person: row.copy() for person, row in prepared_people.items()}
    for event in sorted(departure_records, key=lambda row: row["event_date"]):
        person = str(event["persnr"])
        if person not in state:
            continue
        row = state[person]
        heads = int(event.get("headcount_change", 0) or 0)
        mak_change = float(event.get("mak_change", 0.0) or 0.0)
        if heads < 0:
            row["active"] = False
            row["mak"] = 0.0
        else:
            if heads > 0:
                row["active"] = True
            row["mak"] = max(0.0, float(row["mak"]) + mak_change)
        reason = str(event.get("reason_code", "") or "")
        if reason == "RUHEND_START":
            row["Status kundenindividuell"] = "Ruhendes Besch\u00e4ftigungsverh\u00e4ltnis"
        elif reason == "RUHEND_RETURN":
            row["Status kundenindividuell"] = "Aktives Besch\u00e4ftigungsverh\u00e4ltnis"
    for person in new_people:
        row = {field: person.get(field, pd.NA) for field in TARGET_PERSON_FIELDS}
        state[str(row["PersNr"])] = row
    return state if as_dict else pd.DataFrame.from_records(list(state.values()))


def prepare_target_array_model(source_positions):
    """Prepare position ratios and vacancy keys once for compact seed totals."""
    import pandas as pd

    positions = source_positions.reset_index(drop=True)
    identifiers = positions["PersNr"].astype(str).str.strip().str.replace(
        r"\.0$", "", regex=True)
    identifiers = identifiers.where(positions["PersNr"].notna(), None)
    occupied = positions["PersNr"].notna()
    mak = pd.to_numeric(positions["MAK_Reporting"], errors="coerce").fillna(0.0)
    counts = identifiers.loc[occupied].value_counts()
    totals = mak.loc[occupied].groupby(identifiers.loc[occupied]).transform("sum")
    ratios = (mak.loc[occupied] / totals.where(totals.gt(0), np.nan)).fillna(
        1.0 / identifiers.loc[occupied].map(counts))
    weights = np.zeros(len(positions), dtype=float)
    weights[occupied.to_numpy()] = ratios.to_numpy()
    azubi = positions.get("Ist_Azubi", pd.Series(False, index=positions.index))
    return {
        "ids": identifiers.tolist(),
        "weights": weights,
        "counts": counts.to_dict(),
        "azubi": azubi.fillna(False).astype(bool).tolist(),
        "org": positions["Organisationseinheit"].astype(str).tolist(),
        "plan": positions["Planstelle"].astype(str).tolist(),
        "existing_ids": set(identifiers.loc[occupied]),
    }


def target_head_mak_from_array_model(model, person_state, atz_fr_people=None,
                                     excluded_new_people=None):
    """Compute target heads/MAK from prepared positions and compact state.

    ``excluded_new_people`` contains forecast IDs removed by final reporting
    exclusions, as determined from the run settings and person/position data.
    """
    from heapq import heapify, heappop
    import pandas as pd

    atz_fr_people = atz_fr_people or set()
    excluded_new_people = excluded_new_people or set()
    ids = model["ids"]
    values = [0.0] * len(ids)
    occupied = [False] * len(ids)
    vacant_all, vacant_org, vacant_exact = [], {}, {}
    for index, person_id in enumerate(ids):
        person = person_state.get(person_id) if person_id is not None else None
        if person is not None and person.get("active") == True:
            value = float(person.get("mak", 0.0)) * model["weights"][index]
            source_mak = pd.to_numeric(person.get("Personen_MAK_Source"), errors="coerce")
            if model["counts"][person_id] == 1 and pd.notna(source_mak):
                status = str(person.get("Status kundenindividuell", "")).strip().lower()
                contract = str(person.get("Vertragsart", "")).strip().lower()
                group = str(person.get("MitarbGruppenbez.", "")).strip().lower()
                zero = (person_id in atz_fr_people or model["azubi"][index]
                        or status == "ruhendes besch\u00e4ftigungsverh\u00e4ltnis"
                        or contract == "auszubildende" or group == "auszubildende")
                value = 0.0 if zero else max(0.0, float(source_mak))
            values[index] = value
            occupied[index] = True
        else:
            vacant_all.append(index)
            org_key, plan_key = model["org"][index], model["plan"][index]
            vacant_org.setdefault(org_key, []).append(index)
            vacant_exact.setdefault((org_key, plan_key), []).append(index)
    heapify(vacant_all)
    for queue in (*vacant_org.values(), *vacant_exact.values()):
        heapify(queue)

    def first_free(queue):
        while queue and occupied[queue[0]]:
            heappop(queue)
        return queue[0] if queue else None

    new_ids = sorted(person_id for person_id, person in person_state.items()
                     if person_id not in model["existing_ids"] and
                     person_id not in excluded_new_people and
                     person.get("active") == True and
                     (person.get("is_forecast") is True or
                      person_id.startswith(("AZ_", "TR_", "NH_"))))
    for person_id in new_ids:
        person = person_state[person_id]
        org, plan = person.get("Organisationseinheit"), person.get("Planstelle")
        org_key = str(org)
        index = first_free(vacant_all if pd.isna(org) else vacant_org.get(org_key, []))
        if pd.notna(plan):
            if pd.isna(org):
                exact = min((first_free(queue) for (_, position), queue
                             in vacant_exact.items() if position == str(plan)),
                            default=None, key=lambda value: float("inf") if value is None else value)
            else:
                exact = first_free(vacant_exact.get((org_key, str(plan)), []))
            if exact is not None:
                index = exact
        mak = 0.0
        for field, divisor in (("Personen_MAK_Source", 1.0),
                               ("BsGrd_Source", 100.0),
                               ("Personen_MAK", 1.0), ("BsGrd", 100.0)):
            value = pd.to_numeric(person.get(field), errors="coerce")
            if pd.notna(value):
                mak = max(0.0, float(value) / divisor)
                break
        if str(person.get("Vertragsart", "")).strip().lower() == "auszubildende":
            mak = 0.0
        if index is None:
            values.append(mak)
            occupied.append(True)
        else:
            values[index] = mak
            occupied[index] = True
    return {"heads": sum(person.get("active") == True
                         for person_id, person in person_state.items()
                         if person_id not in excluded_new_people),
            "mak": float(np.sum(np.asarray(values)[occupied]))}
