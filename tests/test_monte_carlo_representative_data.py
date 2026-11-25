"""A scenario receives only one certified seed's saved data."""
from __future__ import annotations

import pickle
import zlib
from types import SimpleNamespace

import pandas as pd
import pytest

from utils.monte_carlo_annual_event_rows import VERSION as EVENT_VERSION, REQUIRED_COLUMNS
from utils.monte_carlo_annual_positions import VERSION as POSITION_VERSION, FILTER_COLUMNS, IDENTITY_COLUMNS
from utils.monte_carlo_filtered_annual_positions import _position_values
from utils.monte_carlo_representative_data import (
    load_representative_seed_data, materialize_representative_seed_detail,
)


def _pack(value):
    return zlib.compress(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), level=1)


def _ensemble():
    targets, departures, events, positions = [], [], [], []
    per_seed_stock = []
    for index in range(2):
        target = pd.DataFrame({"PersNr": [str(index + 1)],
                               "Organisationseinheit": ["A"], "Is_Vacant": [False],
                               "MAK_Reporting": [0.6 + index]})
        year = pd.DataFrame({
            "Stichtag": [pd.Timestamp("2027-12-31")],
            "PersNr": [str(index + 1)], "Is_Vacant": [False],
            "MAK_Reporting": [0.6 + index],
            "Organisationseinheit": ["A"], "OE-Cluster": ["C"],
            "Jobfamily": ["J"], "JF-Cluster": ["JC"],
            "Alterskohorte": ["30-39"], "Geschlecht": ["w"],
            "Arbeitszeit": ["Teilzeit"], "Ausbildung": ["x"],
            "ATZ_Status": ["Kein ATZ"],
        }).loc[:, [*IDENTITY_COLUMNS, *FILTER_COLUMNS]]
        empty_event = pd.DataFrame(columns=[*REQUIRED_COLUMNS, "age"])
        targets.append(_pack(target))
        departures.append(_pack(pd.DataFrame()))
        events.append(_pack(empty_event))
        positions.append(_pack([year]))
        per_seed_stock.append(_position_values([year]))
    keys = sorted(set().union(*(set(row) for row in per_seed_stock)))
    return {
        "status": "complete", "count": 2, "seed_pairs": ((42, 42), (43, 43)),
        "org_filter_snapshot_version": 1, "org_filter_snapshots": targets,
        "org_departure_events_version": 1, "org_departure_events": departures,
        "annual_event_rows_version": EVENT_VERSION, "annual_event_rows": events,
        "annual_positions_version": POSITION_VERSION, "annual_positions": positions,
        "statistics": {"status": "complete", "requested_count": 2,
                       "annual_event_samples": [],
                       "annual_stock_samples": [(*key, [seed.get(key, 0.0) for seed in per_seed_stock])
                                                for key in keys]},
    }


def test_selected_seed_keeps_its_own_target_and_year_positions():
    ensemble = _ensemble()
    first = load_representative_seed_data(ensemble, 0)
    second = load_representative_seed_data(ensemble, 1)
    assert first.seed_pair == (42, 42)
    assert second.seed_pair == (43, 43)
    assert first.target["MAK_Reporting"].iloc[0] == 0.6
    assert second.target["MAK_Reporting"].iloc[0] == 1.6
    assert second.annual_positions[0]["MAK_Reporting"].iloc[0] == 1.6


def test_changed_certified_year_and_stale_event_contract_fail_closed():
    ensemble = _ensemble()
    first = list(ensemble["statistics"]["annual_stock_samples"][0])
    first[-1] = [99.0, first[-1][1]]
    ensemble["statistics"]["annual_stock_samples"][0] = tuple(first)
    with pytest.raises(ValueError, match="Jahresbestände weichen"):
        load_representative_seed_data(ensemble, 0)
    ensemble = _ensemble()
    ensemble["annual_event_rows_version"] = EVENT_VERSION - 1
    with pytest.raises(ValueError, match="veraltet"):
        load_representative_seed_data(ensemble, 1)


def test_materialized_detail_must_match_saved_seed_and_frozen_input(monkeypatch):
    from utils import monte_carlo_product_worker as worker

    ensemble = _ensemble()
    ensemble.update(plan_signature="plan", input_fingerprint="input")
    plan = SimpleNamespace(
        signature="plan", input_fingerprint="input", count=2,
        seed_pairs=((42, 42), (43, 43)),
        params_for_index=lambda index: ({"random_seed": 42 + index},
                                        {"random_seed": 42 + index}),
    )
    request = SimpleNamespace(plan=plan, frozen_input={"frozen": True})
    monkeypatch.setattr(worker, "product_input_fingerprint", lambda payload: "input")

    def fake_run(index, attrition, accession, payload, *, include_ist_charts, detail_sink):
        assert index == 1 and include_ist_charts
        assert attrition["random_seed"] == accession["random_seed"] == 43
        target = load_representative_seed_data(ensemble, index).target
        detail_sink(target.copy(), {"run_signature": "seed-43"},
                    {"stock_reconciliation": {"status": "Abgestimmt"}})
        return {"annual_event_rows": ensemble["annual_event_rows"][index],
                "annual_positions": ensemble["annual_positions"][index]}

    monkeypatch.setattr(worker, "run_product_seed", fake_run)
    detail = materialize_representative_seed_detail(request, ensemble, 1)
    assert detail.saved.seed_pair == (43, 43)
    assert detail.prepared["MAK_Reporting"].iloc[0] == 1.6
    ensemble["input_fingerprint"] = "other"
    with pytest.raises(ValueError, match="Laufplan"):
        materialize_representative_seed_detail(request, ensemble, 1)
