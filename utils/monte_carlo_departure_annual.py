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
    return row_mak, active


def target_mak_in_position_order(source_positions, final_people) -> float:
    """Sum existing occupied positions without building a future snapshot."""
    row_mak, active = _existing_position_mak(source_positions, final_people)
    # The app sums only occupied rows. Including zero-valued vacated rows can
    # change NumPy's reduction tree and the final floating-point bit pattern.
    return float(np.sum(row_mak.loc[active].to_numpy()))


def target_head_mak_with_hires(source_positions, final_people) -> dict[str, int | float]:
    """Probe exact target totals for existing and forecast people.

    Vacancy matching follows the reference append helper. This remains a
    parity probe until all hire and exclusion variants have been checked.
    """
    import pandas as pd

    row_mak, active = _existing_position_mak(source_positions, final_people)
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
    new = new[~new.index.isin(existing_ids)].sort_index()
    for pid, person in new.iterrows():
        org = person.get("Organisationseinheit")
        planstelle = person.get("Planstelle")
        candidates = [i for i in range(len(positions)) if not occupied[i]
                      and (pd.isna(org) or str(positions.at[i, "Organisationseinheit"]) == str(org))]
        if pd.notna(planstelle):
            exact = [i for i in candidates if str(positions.at[i, "Planstelle"]) == str(planstelle)]
            if exact:
                candidates = exact
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
        if candidates:
            index = candidates[0]
            values[index] = mak
            occupied[index] = True
        else:
            values.append(mak)
            occupied.append(True)
    return {"heads": int(people.loc[people["active"] == True].index.nunique()),
            "mak": float(np.sum(np.asarray(values)[occupied]))}
