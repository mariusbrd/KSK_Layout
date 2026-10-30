"""Exact per-seed parity gate for the isolated quit-only batch probe."""
from __future__ import annotations

import pandas as pd
import pytest

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.quit_batch_probe import (iter_quit_seed_records, prepare_quit_batch,
                                       quit_annual_event_values, run_quit_batch)
from test_kuendigung_forecast import _atz_df, _make_employee, _params_only_quit, EMPTY_ATZ


@pytest.mark.parametrize("matrix", [False, True])
@pytest.mark.parametrize("freq", ["M", "Q"])
def test_quit_batch_matches_reference_event_order_and_final_state(matrix: bool, freq: str) -> None:
    people = pd.DataFrame([
        _make_employee(str(100 + i), age_years=24 + i * 4, mak=0.5 + i / 10,
                       org=f"OE-{i % 3}", jobfamily=f"JF-{i % 2}")
        for i in range(12)
    ])
    people = pd.concat([people, people.iloc[[2]]], ignore_index=True)
    people.loc[12, "MAK_Calculated"] = 8.0  # duplicate ID must keep first
    atz = _atz_df("101", pd.Timestamp("2026-01-01"))
    params = _params_only_quit(
        rate_base=0.35, use_matrix=matrix,
        matrix={"alter_unter_30": {"JF-0": 0.8, "Default": 0.2},
                "alter_30_45": {"JF-1": 0.6, "Default": 0.3},
                "alter_45_55": {"Default": 0.4},
                "alter_55_plus": {"Default": 0.5}},
        adjustments={"more": {"JF-0": [2027]}, "less": {"JF-1": [2026]}},
    )
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2027-12-31")
    prepared = prepare_quit_batch(people, atz, start, end, params, freq=freq)
    batch = run_quit_batch(prepared, (7, 42, 123, 999))
    streamed = list(iter_quit_seed_records(prepared, (7, 42, 123, 999)))
    assert [seed for seed, _, _, _ in streamed] == [7, 42, 123, 999]
    for seed, result in batch.items():
        reference = run_forecast_abgaenge(
            people, atz, start, end, freq,
            {**params, "random_seed": seed}, include_diagnostics=False,
        )["events_person_level"]
        pd.testing.assert_frame_equal(result.events, reference, check_exact=True)
        records = next(records for current_seed, records, _, _ in streamed if current_seed == seed)
        pd.testing.assert_frame_equal(pd.DataFrame(records), reference, check_exact=True)
        expected_annual = {}
        for dimension in ("Organisationseinheit", "Jobfamily"):
            yearly = reference.assign(year=reference["event_date"].dt.year)
            for (year, group), row in yearly.groupby(["year", dimension], sort=True)[
                    ["headcount_change", "mak_change"]].sum().iterrows():
                expected_annual[(year, dimension, group)] = (
                    int(row["headcount_change"]), float(row["mak_change"]))
        assert quit_annual_event_values(records) == expected_annual
        departed = set(reference["persnr"])
        expected_active = [person not in departed for person in prepared.ids]
        assert result.active.tolist() == expected_active
        assert result.mak.tolist() == [float(m) if a else 0.0
                                       for m, a in zip(prepared.mak, expected_active)]


def test_quit_batch_rejects_other_components() -> None:
    params = _params_only_quit()
    params["components"]["atz"] = True
    with pytest.raises(ValueError, match="quit-only"):
        prepare_quit_batch(pd.DataFrame([_make_employee("101")]), EMPTY_ATZ,
                           pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31"), params)


def test_quit_batch_matches_empty_event_schema() -> None:
    people = pd.DataFrame([_make_employee("101")])
    params = _params_only_quit(rate_base=0.0)
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31")
    prepared = prepare_quit_batch(people, EMPTY_ATZ, start, end, params)
    actual = run_quit_batch(prepared, [7])[7].events
    expected = run_forecast_abgaenge(people, EMPTY_ATZ, start, end, "M",
                                     {**params, "random_seed": 7})["events_person_level"]
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
