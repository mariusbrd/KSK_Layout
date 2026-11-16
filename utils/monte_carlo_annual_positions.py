"""App-only yearly position rows for future filtered Monte Carlo stock views."""
from __future__ import annotations

import pickle
import zlib

import pandas as pd

from abgaenge.schemas import normalize_persnr
from dataloader.loader import DEFAULT_COHORTS, assign_age_cohort
from utils.compact_regular_stock import _grouped_stock_metrics_indexed


VERSION = 1
FILTER_COLUMNS = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster",
                  "Alterskohorte", "Geschlecht", "Arbeitszeit", "Ausbildung", "ATZ_Status")
IDENTITY_COLUMNS = ("Stichtag", "PersNr", "Is_Vacant", "MAK_Reporting")
PERSON_COLUMNS = ("GebDatum", "BsGrd", "Geschlecht", "Ausbildung", "ATZ_Status")


def annual_position_rows(date: pd.Timestamp, frame: pd.DataFrame,
                         baseline: pd.DataFrame, year_people: pd.DataFrame,
                         cohorts: dict | None) -> pd.DataFrame:
    """Attach year-specific filter attributes without changing stock projection."""
    if len(frame) != len(baseline) or "PersNr" not in year_people:
        raise ValueError("Unvollständiger Jahresbestand")
    result = frame.loc[:, ["PersNr", "Is_Vacant", "MAK_Reporting",
                           "Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"]].copy()
    result["Stichtag"] = pd.Timestamp(date).normalize()
    old_ids = normalize_persnr(baseline["PersNr"]).reset_index(drop=True)
    new_ids = normalize_persnr(frame["PersNr"]).reset_index(drop=True)
    same_person = new_ids.notna() & old_ids.notna() & (
        new_ids.fillna("").astype(str) == old_ids.fillna("").astype(str))
    people = year_people.copy()
    people["_annual_person"] = normalize_persnr(people["PersNr"])
    people = people.loc[people["_annual_person"].notna()].drop_duplicates(
        "_annual_person", keep="last").set_index("_annual_person")
    for column in PERSON_COLUMNS:
        old = (baseline[column].reset_index(drop=True) if column in baseline
               else pd.Series(pd.NA, index=result.index))
        current = (new_ids.map(people[column]) if column in people
                   else pd.Series(pd.NA, index=result.index))
        result[column] = old.where(same_person, current).combine_first(current)
    birth = pd.to_datetime(result["GebDatum"], errors="coerce")
    age = ((pd.Timestamp(date) - birth).dt.days / 365.25).fillna(0).astype(int)
    definitions = cohorts if cohorts is not None else DEFAULT_COHORTS
    result["Alterskohorte"] = age.map(lambda value: assign_age_cohort(value, definitions))
    degree = pd.to_numeric(result["BsGrd"], errors="coerce")
    if "mak" in people:
        degree = degree.fillna(new_ids.map(pd.to_numeric(people["mak"], errors="coerce")) * 100)
    result["Arbeitszeit"] = pd.Series(pd.NA, index=result.index, dtype="object")
    result.loc[degree.eq(0), "Arbeitszeit"] = "Inaktiv"
    result.loc[degree.gt(0) & degree.lt(100), "Arbeitszeit"] = "Teilzeit"
    result.loc[degree.ge(100), "Arbeitszeit"] = "Vollzeit"
    result.loc[result["ATZ_Status"].isna() & new_ids.notna(), "ATZ_Status"] = "Kein ATZ"
    for column in ("Geschlecht", "Arbeitszeit", "Ausbildung", "ATZ_Status"):
        result.loc[new_ids.notna(), column] = result.loc[
            new_ids.notna(), column].fillna("UNMAPPED")
    return result.loc[:, [*IDENTITY_COLUMNS, *FILTER_COLUMNS]].reset_index(drop=True)


def encode_annual_positions(rows: list[pd.DataFrame], stock_tables: dict) -> bytes:
    """Certify every unfiltered OE/cluster stock against existing annual output."""
    if not rows:
        raise ValueError("Keine Jahresbestandszeilen vorhanden")
    for level in ("Organisationseinheit", "OE-Cluster"):
        table = stock_tables[level]
        expected = {}
        for values in table.itertuples(index=False, name=None):
            record = dict(zip(table.columns, values))
            expected[(pd.Timestamp(record["Stichtag"]), str(record[level]))] = (
                float(record["Köpfe"]), float(record["MAK"]))
        observed = {}
        for year in rows:
            date = pd.Timestamp(year["Stichtag"].iloc[0])
            for group, values in _grouped_stock_metrics_indexed(year, level).items():
                observed[(date, str(group))] = tuple(map(float, values))
        if expected != observed:
            raise ValueError(f"Jahresbestand weicht vom zertifizierten Lauf ab: {level}")
    return zlib.compress(pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL), level=1)


def decode_annual_positions(blob: bytes) -> list[pd.DataFrame]:
    if not isinstance(blob, bytes):
        raise ValueError("Ungültiger Jahresbestands-Laufdatensatz")
    result = pickle.loads(zlib.decompress(blob))
    if (not isinstance(result, list) or not result
            or any(not isinstance(row, pd.DataFrame)
                   or not set((*IDENTITY_COLUMNS, *FILTER_COLUMNS)).issubset(row)
                   for row in result)):
        raise ValueError("Unvollständiger Jahresbestands-Laufdatensatz")
    return result
