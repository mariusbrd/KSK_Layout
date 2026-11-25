"""Decode one selected seed without mixing it with the reference run."""
from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Mapping

import pandas as pd

from utils.monte_carlo_annual_event_rows import (
    VERSION as EVENT_VERSION, decode_annual_event_rows,
)
from utils.monte_carlo_annual_positions import (
    VERSION as POSITION_VERSION, decode_annual_positions,
)
from utils.monte_carlo_filtered_annual_events import _samples as event_values
from utils.monte_carlo_filtered_annual_positions import _position_values as position_values
from utils.monte_carlo_filtered_org import decode_snapshot


@dataclass(frozen=True)
class RepresentativeSeedData:
    index: int
    seed_pair: tuple[int, int]
    target: pd.DataFrame
    departure_events: pd.DataFrame
    annual_events: pd.DataFrame
    annual_positions: tuple[pd.DataFrame, ...]


@dataclass(frozen=True)
class RepresentativeSeedDetail:
    saved: RepresentativeSeedData
    prepared: pd.DataFrame
    context: dict
    summary: dict


def _blob(ensemble: Mapping, key: str, version_key: str, version: int,
          index: int, count: int) -> bytes:
    blobs = ensemble.get(key)
    if (ensemble.get(version_key) != version or not isinstance(blobs, list)
            or len(blobs) != count or not isinstance(blobs[index], bytes)):
        raise ValueError(f"Laufdaten fehlen oder sind veraltet: {key}")
    return blobs[index]


def _certified(rows: list, index: int, count: int) -> dict[tuple, float]:
    result = {}
    for *key, values in rows:
        if not isinstance(values, (tuple, list)) or len(values) != count:
            raise ValueError("Unvollständige zertifizierte Jahreswerte")
        signature = tuple(key)
        if signature in result:
            raise ValueError("Doppelte zertifizierte Jahreswerte")
        value = float(values[index])
        if not math.isfinite(value):
            raise ValueError("Ungültige zertifizierte Jahreswerte")
        result[signature] = value
    return result


def _same_values(actual: Mapping, expected: Mapping, name: str) -> None:
    for key in actual.keys() | expected.keys():
        if not math.isclose(float(actual.get(key, 0.0)), float(expected.get(key, 0.0)),
                            rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"{name} weichen vom zertifizierten Lauf ab: {key}")


def load_representative_seed_data(ensemble: Mapping, index: int) -> RepresentativeSeedData:
    """Return one seed's saved target, event and annual position data after parity checks.

    The detailed stock bridge and plan export are intentionally outside this
    contract until they have a separately certified per-seed source.
    """
    count = ensemble.get("count")
    pairs = ensemble.get("seed_pairs")
    statistics = ensemble.get("statistics")
    if (ensemble.get("status") != "complete" or not isinstance(count, int)
            or isinstance(count, bool) or count < 2
            or not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count
            or not isinstance(pairs, (tuple, list)) or len(pairs) != count
            or not isinstance(statistics, Mapping) or statistics.get("status") != "complete"
            or statistics.get("requested_count") != count):
        raise ValueError("Ungültiger Laufvertrag für die repräsentative Ansicht")
    pair = pairs[index]
    if not isinstance(pair, (tuple, list)) or len(pair) != 2:
        raise ValueError("Ungültiges Seed-Paar")

    target = decode_snapshot(_blob(
        ensemble, "org_filter_snapshots", "org_filter_snapshot_version", 1, index, count))
    departures = decode_snapshot(_blob(
        ensemble, "org_departure_events", "org_departure_events_version", 1, index, count))
    events = decode_annual_event_rows(_blob(
        ensemble, "annual_event_rows", "annual_event_rows_version", EVENT_VERSION, index, count))
    positions = decode_annual_positions(_blob(
        ensemble, "annual_positions", "annual_positions_version", POSITION_VERSION, index, count))
    expected_events = {key: value for key, value in _certified(
        statistics.get("annual_event_samples") or [], index, count).items()
        if key[1] in {"Organisationseinheit", "OE-Cluster"}}
    expected_positions = _certified(statistics.get("annual_stock_samples") or [], index, count)
    if not expected_positions:
        raise ValueError("Zertifizierte Jahresbestände fehlen")
    _same_values(event_values(events), expected_events, "Jahresereignisse")
    _same_values(position_values(positions), expected_positions, "Jahresbestände")
    return RepresentativeSeedData(
        index, (int(pair[0]), int(pair[1])), target, departures, events, tuple(positions))


def prepare_representative_seed_detail(request, ensemble: Mapping,
                                       index: int) -> RepresentativeSeedData:
    """Certify one saved seed without rerunning the simulation."""
    from utils.monte_carlo_product_worker import product_input_fingerprint
    plan = request.plan
    if (ensemble.get("plan_signature") != plan.signature
            or ensemble.get("input_fingerprint") != plan.input_fingerprint
            or ensemble.get("count") != plan.count
            or tuple(map(tuple, ensemble.get("seed_pairs", ()))) != plan.seed_pairs
            or product_input_fingerprint(request.frozen_input) != plan.input_fingerprint):
        raise ValueError("Eingaben oder Laufplan der repräsentativen Ansicht stimmen nicht überein")
    saved = load_representative_seed_data(ensemble, index)
    return saved


def materialize_prepared_representative_seed_detail(request, saved: RepresentativeSeedData
                                                    ) -> RepresentativeSeedDetail:
    """Recompute a certified saved seed from frozen inputs in an isolated process."""
    from utils.monte_carlo_product_worker import (
        product_input_fingerprint, run_product_seed,
    )

    plan = request.plan
    index = saved.index
    if (not 0 <= index < plan.count
            or saved.seed_pair != plan.seed_pairs[index]
            or product_input_fingerprint(request.frozen_input) != plan.input_fingerprint):
        raise ValueError("Eingaben oder Seed-Paar der repräsentativen Ansicht stimmen nicht überein")
    captured = []
    attrition, accession = plan.params_for_index(index)
    packet = run_product_seed(
        index, attrition, accession, request.frozen_input,
        include_ist_charts=True,
        detail_sink=lambda prepared, context, summary: captured.append(
            (prepared, context, summary)),
    )
    if len(captured) != 1:
        raise ValueError("Detaillierter Seed-Lauf wurde nicht vollständig erzeugt")
    prepared, context, summary = captured[0]
    try:
        pd.testing.assert_frame_equal(
            prepared.loc[:, saved.target.columns], saved.target, check_exact=True)
        pd.testing.assert_frame_equal(
            decode_annual_event_rows(packet["annual_event_rows"]),
            saved.annual_events, check_exact=True)
        regenerated_positions = decode_annual_positions(packet["annual_positions"])
        if len(regenerated_positions) != len(saved.annual_positions):
            raise AssertionError("Anzahl der Jahresscheiben weicht ab")
        for actual, original in zip(regenerated_positions, saved.annual_positions):
            pd.testing.assert_frame_equal(actual, original, check_exact=True)
    except (AssertionError, KeyError) as exc:
        raise ValueError("Detaillierter Seed-Lauf weicht von gespeicherten Laufdaten ab") from exc
    if summary["stock_reconciliation"]["status"] == "Abweichung":
        raise ValueError("Detaillierte Bestandsüberleitung ist nicht abgestimmt")
    return RepresentativeSeedDetail(saved, prepared, context, summary)


def materialize_representative_seed_detail(request, ensemble: Mapping,
                                           index: int) -> RepresentativeSeedDetail:
    """Recompute one selected seed and certify it against the saved ensemble."""
    saved = prepare_representative_seed_detail(request, ensemble, index)
    return materialize_prepared_representative_seed_detail(request, saved)
