"""The UI run contract preserves single-run inputs and rejects mixed ensembles."""
from __future__ import annotations

from copy import deepcopy

import pytest

from utils.monte_carlo_metric_packet import LEVELS
from utils.monte_carlo_run_contract import build_run_plan, finalize_run_packets


def plan(*, count=2, attrition_seed=17, accession_seed=29, fingerprint="input-A",
         band_level=0.8):
    return build_run_plan(
        base_date="2025-12-31", target_date="2035-12-31",
        attrition_params={"random_seed": attrition_seed, "quit": {"rate": 0.12}},
        accession_params={"random_seed": accession_seed, "azubi": {"active": True}},
        exclusions={"org_units": ["A"]}, include_future_hires=False,
        input_fingerprint=fingerprint, cluster_source_signature="cluster-A",
        count=count, band_level=band_level)


def packet(value):
    return {"version": 1, "target": {"heads": value, "mak": float(value),
                                      "eur": 0.0}, "annual_values": [],
            "stock_status": {level: "Nicht verfügbar" for level in LEVELS}}


def outcome(request, index, value):
    return {"index": index, "signature": request.signature,
            "input_fingerprint": request.input_fingerprint,
            "seed_pair": request.seed_pairs[index], "status": "ok",
            "packet": packet(value)}


def test_one_run_passes_both_original_seed_values_without_mutation():
    request = plan(count=1)
    attrition_before = deepcopy(request._attrition_params)
    accession_before = deepcopy(request._accession_params)
    attrition, accession = request.params_for_index(0)
    assert attrition == attrition_before
    assert accession == accession_before
    assert request.seed_pairs == ((17, 29),)
    attrition["quit"]["rate"] = 0.99
    accession["azubi"]["active"] = False
    assert request.params_for_index(0) == (attrition_before, accession_before)


def test_seed_pairs_are_stable_prefixes_and_preserve_distinct_seed_offset():
    short, long = plan(count=2), plan(count=10)
    assert short.seed_pairs == long.seed_pairs[:2] == ((17, 29), (18, 30))
    assert long.params_for_index(1)[0]["random_seed"] == 18
    assert long.params_for_index(1)[1]["random_seed"] == 30


def test_signature_covers_data_params_count_and_band():
    original = plan()
    assert original.signature == plan().signature
    assert original.signature != plan(fingerprint="input-B").signature
    assert original.signature != plan(count=3).signature
    assert original.signature != plan(band_level=0.9).signature
    assert original.signature != plan(accession_seed=30).signature


def test_only_complete_matching_run_set_yields_statistics():
    request = plan()
    first, second = outcome(request, 0, 10), outcome(request, 1, 20)
    complete = finalize_run_packets(request, [second, first])
    assert complete["status"] == "complete"
    assert complete["statistics"]["target"]["heads"]["mean"] == 15.0
    assert complete["seed_pairs"] == ((17, 29), (18, 30))
    assert finalize_run_packets(request, [first])["statistics"] is None
    assert finalize_run_packets(request, [first, first, second])["status"] == "incomplete"
    changed = {**second, "input_fingerprint": "input-B"}
    assert finalize_run_packets(request, [first, changed])["statistics"] is None
    changed = {**second, "seed_pair": None}
    assert finalize_run_packets(request, [first, changed])["statistics"] is None
    changed = {**second, "status": "failed", "error": "forecast failed"}
    assert finalize_run_packets(request, [first, changed])["failures"] == [
        {"index": 1, "error": "forecast failed"}]


@pytest.mark.parametrize("change", [
    {"count": 0}, {"count": True}, {"fingerprint": ""},
    {"attrition_seed": -1}, {"band_level": 1.0},
])
def test_invalid_run_config_is_rejected(change):
    with pytest.raises(ValueError):
        plan(**change)
