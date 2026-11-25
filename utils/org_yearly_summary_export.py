"""OE export headline matrix from complete saved Monte Carlo paths."""
from __future__ import annotations

import math
import pandas as pd

from abgaenge.schemas import normalize_persnr
from components.org_simulation_yearly import build_yearly_headline_metrics
from utils.monte_carlo_ensemble import _statistics
from utils.monte_carlo_filtered_org import decode_snapshot
from utils.monte_carlo_representative_runs import select_representative_runs
from utils.org_simulation_events import DEPARTURES


KEYS = (
    ("initial", "Ausgangsbestand"),
    ("final", "Bestand am Zielstichtag"),
    ("departure_share", "Abgänge / Ausgangsbestand"),
    ("retirement_atz", "Rente inkl. ATZ bis Zielstichtag"),
    ("quit", "Kündigungen bis Zielstichtag"),
)


def _age(frame: pd.DataFrame, level: str, groups: set[str], date,
         cluster_map: dict | None = None) -> float | None:
    if not {"PersNr", "GebDatum"}.issubset(frame.columns):
        raise ValueError("Geburtsdaten für die OE-Zusammenfassung fehlen; Monte Carlo bitte neu berechnen")
    if level not in frame:
        if level != "OE-Cluster" or "Organisationseinheit" not in frame:
            raise ValueError("OE-Zuordnung für die Altersauswertung fehlt")
        frame = frame.copy()
        frame[level] = frame["Organisationseinheit"].map(cluster_map or {}).fillna("Nicht zugeordnet")
    people = frame.loc[frame[level].astype(str).isin(groups)].copy()
    if "Is_Vacant" in people:
        people = people.loc[people["Is_Vacant"].ne(True)]
    people["_id"] = normalize_persnr(people["PersNr"])
    people = people.dropna(subset=["_id"]).drop_duplicates("_id")
    births = pd.to_datetime(people["GebDatum"], errors="coerce").dropna()
    if births.empty:
        return None
    return float(((pd.Timestamp(date).normalize() - births).dt.days / 365.25).mean())


def _oe_multiple_counts(frame: pd.DataFrame, level: str, groups: set[str],
                        cluster_map: dict | None = None) -> int:
    """Extra group counts when a person occupies positions in several selected OEs."""
    if level not in frame:
        if level != "OE-Cluster" or "Organisationseinheit" not in frame:
            raise ValueError("OE-Zuordnung für die Mehrfachzählung fehlt")
        frame = frame.copy()
        frame[level] = frame["Organisationseinheit"].map(cluster_map or {}).fillna("Nicht zugeordnet")
    selected = frame.loc[frame[level].astype(str).isin(groups)].copy()
    if "Is_Vacant" in selected:
        selected = selected.loc[selected["Is_Vacant"].ne(True)]
    selected["_id"] = normalize_persnr(selected["PersNr"])
    selected = selected.dropna(subset=["_id"])
    group_heads = selected.drop_duplicates([level, "_id"]).shape[0]
    unique_heads = selected["_id"].nunique()
    return int(group_heads - unique_heads)


def _seed_headlines(summary, ensemble, level, groups, end_year, metric):
    count = ensemble["count"]
    sources = ensemble["statistics"].get("departure_rate_inputs_by_seed")
    stocks = ensemble["statistics"].get("departure_stock_samples")
    if not isinstance(sources, list) or len(sources) != count or not isinstance(stocks, list):
        raise ValueError("Laufwerte für die OE-Zusammenfassung fehlen")
    code = "heads" if metric == "Köpfe" else "mak"
    date = min(pd.Timestamp(end_year, 12, 31), pd.Timestamp(summary["metadata"]["target_date"]))
    end_date = date.date().isoformat()
    stock = [[0.0] * count for _ in groups]
    for record_date, record_level, group, record_metric, values in stocks:
        if (record_date, record_level, record_metric) != (end_date, level, code):
            continue
        if level == "OE-Cluster" and group in {"Unclustered", "UNMAPPED", "Unmapped"}:
            group = "Nicht zugeordnet"
        if group in groups:
            if len(values) != count:
                raise ValueError("Unvollständige OE-Jahresbestände")
            row = stock[groups.index(group)]
            for index, value in enumerate(values):
                row[index] += float(value)
    result = {key: [] for key, _ in KEYS}
    for index, by_level in enumerate(sources):
        source = by_level.get(level)
        if not isinstance(source, dict):
            raise ValueError("OE-Laufwerte fehlen")
        opening = math.fsum(source["opening"].get(group, {}).get(code, 0.0) for group in groups)
        closing = math.fsum(row[index] for row in stock)
        retirement = quit_loss = other = 0.0
        retirement_types = {"RETIREMENT", "ATZ_END"}
        if metric == "MAK":
            retirement_types.add("ATZ_AR_TO_FR")
        for year, group, kind, heads, mak in source["losses"]:
            if group not in groups or year > end_year:
                continue
            value = float(heads if code == "heads" else mak)
            if kind in retirement_types:
                retirement += value
            elif kind == "QUIT":
                quit_loss += value
            elif kind in DEPARTURES:
                other += value
        result["initial"].append(opening)
        result["final"].append(closing)
        result["retirement_atz"].append(retirement)
        result["quit"].append(quit_loss)
        result["departure_share"].append((retirement + quit_loss + other) / opening
                                         if opening > 0 else None)
    reference = build_yearly_headline_metrics(summary, dimension=level,
                                              organizations=groups, end_year=end_year,
                                              metric=metric)
    if reference is None:
        raise ValueError("Referenzkennzahlen für die OE-Zusammenfassung fehlen")
    for key, values in result.items():
        if reference[key] is None and values[0] is None:
            continue
        if reference[key] is None or values[0] is None or not math.isclose(
                float(reference[key]), float(values[0]), rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"OE-Zusammenfassung weicht vom Referenzlauf ab: {key}")
    return result


def build_oe_summary(summary: dict, view: dict, ensemble: dict | None = None,
                     *, snapshot_frames: list[pd.DataFrame] | None = None) -> pd.DataFrame:
    """Rows are metadata/KPIs; columns are consolidated and whole-run views."""
    level = view["level"]
    groups = list(dict.fromkeys(map(str, view["organizations"])))
    if not groups:
        raise ValueError("Keine Organisationen für die OE-Zusammenfassung gewählt")
    base = pd.Timestamp(summary["metadata"]["base_date"]).normalize()
    target = pd.Timestamp(summary["metadata"]["target_date"]).normalize()
    year = target.year
    columns = (["Mittelwert", "P10", "P90", "Geringste Fluktuation",
                "Typischer Einzellauf", "Höchste Fluktuation"] if ensemble else ["Einzellauf"])
    rows = []

    def add(label, unit="", values=None):
        row = {"Kennzahl / Information": label, "Einheit": unit}
        row.update({column: None if values is None else values.get(column) for column in columns})
        rows.append(row)

    add("Organisation(en)", values={column: ", ".join(groups) for column in columns})
    add("Organisationsebene", values={column: level for column in columns})
    add("Ausgangsstichtag", values={column: base.date().isoformat() for column in columns})
    add("Zielstichtag", values={column: target.date().isoformat() for column in columns})
    count = ensemble["count"] if ensemble else 1
    add("Anzahl Läufe", values={column: count for column in columns})
    selected = select_representative_runs(ensemble) if ensemble else {}
    indices = {"Geringste Fluktuation": selected["best"]["index"],
               "Typischer Einzellauf": selected["typical"]["index"],
               "Höchste Fluktuation": selected["worst"]["index"]} if ensemble else {}
    add("Laufindex", values={column: indices[column] + 1 for column in indices})
    if ensemble:
        for label, offset in (("Seed Abgänge", 0), ("Seed Zugänge", 1)):
            add(label, values={column: ensemble["seed_pairs"][index][offset]
                               for column, index in indices.items()})

    baseline = summary.get("stock_basis", {}).get("baseline_snapshot")
    final = summary.get("stock_basis", {}).get("final_snapshot")
    if not isinstance(baseline, pd.DataFrame) or not isinstance(final, pd.DataFrame):
        raise ValueError("Personenbestände für die OE-Zusammenfassung fehlen")
    cluster_map = summary.get("cluster_mapping", {}).get("oe_map", {})
    today_age = _age(baseline, level, set(groups), base, cluster_map)
    initial_multiple = _oe_multiple_counts(baseline, level, set(groups), cluster_map)
    if ensemble:
        blobs = ensemble.get("org_filter_snapshots")
        if (ensemble.get("org_filter_snapshot_version") != 1 or not isinstance(blobs, list)
                or len(blobs) != count):
            raise ValueError("OE-Zielsnapshots für die Altersauswertung fehlen")
        frames = snapshot_frames if snapshot_frames is not None else [decode_snapshot(blob) for blob in blobs]
        if len(frames) != count:
            raise ValueError("Unvollständige OE-Zielsnapshots für die Altersauswertung")
        future_age = [_age(frame, level, set(groups), target, cluster_map) for frame in frames]
        future_multiple = [_oe_multiple_counts(frame, level, set(groups), cluster_map)
                           for frame in frames]
        reference_age = _age(final, level, set(groups), target, cluster_map)
        if ((future_age[0] is None) != (reference_age is None)
                or (future_age[0] is not None and not math.isclose(
                    future_age[0], reference_age, rel_tol=0, abs_tol=1e-9))):
            raise ValueError("Durchschnittsalter weicht vom Referenzlauf ab")
        valid_ages = [age for age in future_age if age is not None]
        age_values = {"Mittelwert": None, "P10": None, "P90": None}
        if valid_ages:
            stats = _statistics(valid_ages, ensemble["band_level"])
            age_values.update({"Mittelwert": stats["mean"], "P10": stats["lower"],
                               "P90": stats["upper"]})
        age_values.update({column: future_age[index] for column, index in indices.items()})
    else:
        future_age = _age(final, level, set(groups), target, cluster_map)
        future_multiple = [_oe_multiple_counts(final, level, set(groups), cluster_map)]
        age_values = {"Einzellauf": future_age}

    for metric in ("MAK", "Köpfe"):
        add(f"{metric}: Kennzahlen", metric)
        if ensemble:
            samples = _seed_headlines(summary, ensemble, level, groups, year, metric)
            for key, label in KEYS:
                values = samples[key]
                if any(value is None for value in values):
                    cells = {column: None for column in columns}
                else:
                    stats = _statistics(values, ensemble["band_level"])
                    cells = {"Mittelwert": stats["mean"], "P10": stats["lower"],
                             "P90": stats["upper"]}
                    cells.update({column: values[index] for column, index in indices.items()})
                add(label, "%" if key == "departure_share" else metric, cells)
        else:
            reference = build_yearly_headline_metrics(summary, dimension=level,
                                                      organizations=groups, end_year=year,
                                                      metric=metric)
            if reference is None:
                raise ValueError("Referenzkennzahlen für die OE-Zusammenfassung fehlen")
            for key, label in KEYS:
                add(label, "%" if key == "departure_share" else metric,
                    {"Einzellauf": reference[key]})
        if metric == "Köpfe":
            add("Zusätzliche OE-Zählungen im Ausgangsbestand", metric,
                {column: initial_multiple for column in columns})
            if ensemble:
                for label, values in (
                    ("Zusätzliche OE-Zählungen am Zielstichtag", future_multiple),
                    ("Rückgang zusätzlicher OE-Zählungen",
                     [initial_multiple - value for value in future_multiple]),
                ):
                    stats = _statistics(values, ensemble["band_level"])
                    cells = {"Mittelwert": stats["mean"], "P10": stats["lower"],
                             "P90": stats["upper"]}
                    cells.update({column: values[index] for column, index in indices.items()})
                    add(label, metric, cells)
            else:
                add("Zusätzliche OE-Zählungen am Zielstichtag", metric,
                    {"Einzellauf": future_multiple[0]})
                add("Rückgang zusätzlicher OE-Zählungen", metric,
                    {"Einzellauf": initial_multiple - future_multiple[0]})
        add("Durchschnittsalter heute", "Jahre",
            {column: today_age for column in columns})
        add("Durchschnittsalter am Zielstichtag", "Jahre", age_values)
        if ensemble:
            add("Läufe mit berechenbarem Zielalter", "Läufe",
                {column: len(valid_ages) for column in columns})
    return pd.DataFrame(rows)
