"""App-only person-level annual event contract for future filtered year views."""
from __future__ import annotations

import pickle
import zlib

import pandas as pd

from abgaenge.schemas import normalize_persnr
from dataloader.loader import DEFAULT_COHORTS, assign_age_cohort
from utils.org_simulation_events import METRICS, aggregate_simulation_events, normalize_simulation_events


VERSION = 3
FILTER_COLUMNS = (
    "persnr", "age", "Jobfamily", "JF-Cluster", "Alterskohorte", "Geschlecht",
    "Arbeitszeit", "Ausbildung", "ATZ_Status",
)
REQUIRED_COLUMNS = (
    "event_date", "Jahr", "Organisationseinheit", "OE-Cluster",
    "Ereignisart", "Kategorie", "Ereignisquelle", *METRICS,
)


def _with_person_dimensions(events: pd.DataFrame, positions: list[pd.DataFrame],
                            source: pd.DataFrame, cohorts: dict | None) -> pd.DataFrame:
    result = events.copy()
    if result.empty or "persnr" not in result:
        return result
    result["_person"] = normalize_persnr(result["persnr"])
    original = source.copy()
    original["_person"] = normalize_persnr(original["PersNr"])
    original = original.loc[original["_person"].notna()].drop_duplicates(
        "_person", keep="first").set_index("_person")
    years = {int(frame["Stichtag"].dt.year.iloc[0]): frame for frame in positions}
    dimensions = ("Jobfamily", "JF-Cluster", "Alterskohorte", "Geschlecht",
                  "Arbeitszeit", "Ausbildung", "ATZ_Status")
    for year, indices in result.groupby("Jahr").groups.items():
        frame = years.get(int(year))
        people = (frame.loc[frame["PersNr"].notna()].copy() if frame is not None
                  else pd.DataFrame(columns=["PersNr", *dimensions]))
        people["_person"] = normalize_persnr(people["PersNr"])
        people = people.drop_duplicates("_person", keep="first").set_index("_person")
        ids = result.loc[indices, "_person"]
        for column in dimensions:
            if column not in result:
                result[column] = pd.NA
            current = ids.map(people[column]) if column in people else pd.Series(pd.NA, index=indices)
            previous = ids.map(original[column]) if column in original else pd.Series(pd.NA, index=indices)
            values = current.combine_first(previous)
            missing = result.loc[indices, column].isna()
            if column in ("Jobfamily", "JF-Cluster"):
                # Conversion-out belongs to the vacated training position.
                # Its missing source group must not inherit the new person's
                # year-end regular position (the single-run view keeps it
                # unassigned when no source assignment exists).
                missing &= ~result.loc[indices, "Ereignisart"].eq("Azubi_Conversion_Out")
            result.loc[indices[missing], column] = values.loc[indices[missing]]
        # A leaver is absent from the year-end position rows. Cohort then
        # advances from the original birth date to the event's actual date.
        if "GebDatum" in original:
            departed = ~ids.isin(people.index)
            births = pd.to_datetime(ids.map(original["GebDatum"]), errors="coerce")
            dates = pd.to_datetime(result.loc[indices, "event_date"])
            age = ((dates - births).dt.days / 365.25).fillna(0).astype(int)
            definitions = cohorts if cohorts is not None else DEFAULT_COHORTS
            computed = age.map(lambda value: assign_age_cohort(value, definitions))
            result.loc[indices[departed], "Alterskohorte"] = computed.loc[indices[departed]]
    for column in dimensions:
        result[column] = result[column].fillna("UNMAPPED")
    return result.drop(columns="_person")


def encode_annual_event_rows(context: dict, packet: dict, *,
                             positions: list[pd.DataFrame] | None = None,
                             source: pd.DataFrame | None = None,
                             cohorts: dict | None = None) -> bytes:
    """Store event rows only after exact unfiltered OE/cluster parity checks."""
    events = normalize_simulation_events(context)
    for level in ("Organisationseinheit", "OE-Cluster"):
        actual = aggregate_simulation_events(events, dimension=level)
        expected = {
            (int(year), str(group), str(category), str(kind), metric): float(value)
            for year, record_level, group, category, kind, metric, value
            in packet["annual_values"] if record_level == level
        }
        # Build by column name because itertuples renames punctuation and spaces.
        observed = {}
        for values in actual.itertuples(index=False, name=None):
            row = dict(zip(actual.columns, values))
            for metric in METRICS:
                key = (int(row["Jahr"]), str(row[level]), str(row["Kategorie"]),
                       str(row["Ereignisart"]), metric)
                observed[key] = float(row[metric])
        if expected != observed:
            raise ValueError(f"Jahresereignisse weichen vom zertifizierten Lauf ab: {level}")
    if positions is not None and source is not None:
        events = _with_person_dimensions(events, positions, source, cohorts)
    if "age" not in events:
        events["age"] = pd.NA
    columns = [name for name in (*REQUIRED_COLUMNS, *FILTER_COLUMNS) if name in events]
    return zlib.compress(pickle.dumps(events.loc[:, columns].copy(),
                                      protocol=pickle.HIGHEST_PROTOCOL), level=1)


def decode_annual_event_rows(blob: bytes) -> pd.DataFrame:
    if not isinstance(blob, bytes):
        raise ValueError("Ungültiger Jahresereignis-Laufdatensatz")
    frame = pickle.loads(zlib.decompress(blob))
    if not isinstance(frame, pd.DataFrame) or not set((*REQUIRED_COLUMNS, "age")).issubset(frame):
        raise ValueError("Unvollständiger Jahresereignis-Laufdatensatz")
    return frame
