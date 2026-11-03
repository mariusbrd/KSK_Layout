"""Vacant positions available for hires under a fixed staffing plan."""
from __future__ import annotations

import pandas as pd


def _person_id(value) -> str | None:
    if pd.isna(value):
        return None
    result = str(value).strip()
    if result.endswith(".0"):
        result = result[:-2]
    return result or None


def available_position_vacancies(snapshot: pd.DataFrame,
                                 departure_events: pd.DataFrame,
                                 base_date: pd.Timestamp) -> list[dict]:
    """Return each hireable plan row once, with its first available date.

    A position with zero Soll or a reporting exclusion offers no hiring
    capacity. All positions of a departing person become available together.
    """
    exits: dict[str, pd.Timestamp] = {}
    if departure_events is not None and not departure_events.empty:
        for event in departure_events.to_dict("records"):
            if int(event.get("headcount_change", 0) or 0) >= 0:
                continue
            person_id = _person_id(event.get("persnr"))
            date = pd.to_datetime(event.get("event_date"), errors="coerce")
            if person_id is not None and pd.notna(date):
                exits[person_id] = min(exits.get(person_id, date), date)

    vacancies = []
    for index, row in snapshot.reset_index(drop=True).iterrows():
        soll = pd.to_numeric(row.get("Soll_FTE"), errors="coerce")
        if pd.isna(soll) or not 0.0 < float(soll) <= 1.0:
            continue
        excluded = row.get("Is_Excluded")
        if pd.notna(excluded) and bool(excluded):
            continue
        person_id = _person_id(row.get("PersNr"))
        vacant = row.get("Is_Vacant")
        if person_id is None or (pd.notna(vacant) and bool(vacant)):
            date = pd.Timestamp(base_date)
        elif person_id in exits:
            date = exits[person_id]
        else:
            continue
        vacancies.append({
            "position_index": int(index),
            "date": date,
            "org_unit": row.get("Organisationseinheit"),
            "planstelle": row.get("Planstelle"),
            "planstellennr": row.get("Planstellennr"),
            "soll_mak": float(soll),
        })
    return sorted(vacancies, key=lambda vacancy: (
        vacancy["date"], vacancy["position_index"]))
