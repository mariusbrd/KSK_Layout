"""Chronological position transitions extracted from fixed-plan accession events.

This is an isolated bridge for the future compact stock replay. It does not
change the product forecast or infer vacancies from reporting labels.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.fixed_plan_vacancies import _person_id


@dataclass(frozen=True)
class PositionTransition:
    date: pd.Timestamp
    action: str
    position_index: int
    person: str
    event_type: str
    mak: float | None = None
    org_unit: str | None = None
    oe_cluster: str | None = None


def special_position_transitions(
    position_snapshot: pd.DataFrame,
    accession_events: pd.DataFrame,
) -> tuple[PositionTransition, ...]:
    """Return exact release/reservation order for special accession events.

    The event frame must be the unmodified full fixed-plan accession result.
    Existing people may hold multiple positions. A conversion releases only
    rows represented in the product's fixed-plan original-position context;
    an exit vacates every original position of the inactive person.
    """
    occupied: dict[str, set[int]] = {}
    relocatable: set[int] = set()
    for index, person in enumerate(position_snapshot["PersNr"]):
        key = _person_id(person)
        if key is not None:
            occupied.setdefault(key, set()).add(index)
        if "Soll_FTE" not in position_snapshot.columns:
            relocatable.add(index)
            continue
        position = position_snapshot.iloc[index]
        soll = pd.to_numeric(position.get("Soll_FTE"), errors="coerce")
        code = position.get("KÃ¼rzel OrgEinheit")
        is_training = pd.notna(code) and str(code).strip().removesuffix(".0") == "9910"
        if pd.notna(soll) and 0 <= float(soll) <= 1 and (float(soll) > 0 or is_training):
            relocatable.add(index)
    transitions: list[PositionTransition] = []
    initial_position_counts = {person: len(indices) for person, indices in occupied.items()}
    for event in accession_events.to_dict("records"):
        kind = event.get("type")
        if kind not in {"Azubi_Conversion_Out", "Azubi_Exit", "Azubi_Hire",
                        "Trainee_Hire", "New_Hire", "Azubi_Conversion_In"}:
            continue
        person = _person_id(event.get("persnr"))
        if person is None:
            raise ValueError(f"Missing person for {kind}")
        date = pd.Timestamp(event["date"])
        if kind in {"Azubi_Conversion_Out", "Azubi_Exit"}:
            current = occupied.pop(person, set())
            released = current & relocatable if kind == "Azubi_Conversion_Out" else current
            occupied[person] = current - released
            for index in sorted(released):
                transitions.append(PositionTransition(date, "release", index, person, kind))
            continue
        raw_index = event.get("position_index")
        if pd.isna(raw_index):
            raise ValueError(f"Missing position index for {kind}")
        index = int(raw_index)
        if kind == "Azubi_Conversion_In" and initial_position_counts.get(person) == 1:
            for retained_index in sorted(occupied.get(person, set())):
                original = position_snapshot.iloc[retained_index]
                original_soll = pd.to_numeric(original.get("Soll_FTE"), errors="coerce")
                original_azubi = original.get("Ist_Azubi", False)
                new_mak = float(event["mak"])
                # Product allocation keeps each technical row MAK when the
                # two-position total is <= 1 and exceeds the person source.
                # Otherwise its Soll weights assign zero to this old position.
                retained_mak = (
                    new_mak if pd.notna(original_soll) and float(original_soll) == 0.0
                    and pd.notna(original_azubi) and bool(original_azubi)
                    and 0.0 < new_mak <= 0.5 else None)
                transitions.append(PositionTransition(
                    date, "relabel", retained_index, person, kind,
                    mak=retained_mak,
                    org_unit=event.get("org_unit"),
                    oe_cluster=event.get("OE-Cluster")))
        transitions.append(PositionTransition(
            date, "reserve", index, person, kind, float(event["mak"])))
        occupied.setdefault(person, set()).add(index)
    return tuple(transitions)
