"""Read-only productive parity and timing probe for the quit-only batch.

Run: py tests/benchmark_quit_batch_probe.py
No person records are printed.
"""
from __future__ import annotations

from pathlib import Path
import sys
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _reset_state
from dataloader import loader
from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params
from abgaenge.quit_batch_probe import (iter_quit_seed_records, prepare_quit_batch,
                                       quit_annual_event_values, run_quit_batch)
from test_monte_carlo_yearly_reference import _frame_signature


def _year_slices(events: pd.DataFrame) -> dict[str, dict]:
    if events.empty:
        return {key: {} for key in ("Organisationseinheit", "Jobfamily")}
    result = {}
    for key in ("Organisationseinheit", "Jobfamily"):
        frame = events.assign(year=events["event_date"].dt.year)
        annual = frame.groupby(["year", key], sort=True, dropna=False)[
            ["headcount_change", "mak_change"]].sum()
        result[key] = _frame_signature(annual)
    return result


def main() -> None:
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    base = _prepare_employee_forecast_base(snapshot, atz, FROZEN_STICHTAG)
    params = default_params()
    params["components"] = {"atz": False, "retirement": False, "quit": True, "ruhend": False}
    target = pd.Timestamp("2026-12-31")
    seeds = (42, 7, 123, 999)
    start = perf_counter()
    prepared = prepare_quit_batch(base, atz, FROZEN_STICHTAG, target, params)
    prepare_seconds = perf_counter() - start
    start = perf_counter()
    batch = run_quit_batch(prepared, seeds)
    batch_seconds = perf_counter() - start
    start = perf_counter()
    for seed in seeds:
        reference = run_forecast_abgaenge(
            base, atz, FROZEN_STICHTAG, target, "M",
            {**params, "random_seed": seed}, include_diagnostics=False,
        )["events_person_level"]
        assert _frame_signature(batch[seed].events) == _frame_signature(reference), seed
        assert _year_slices(batch[seed].events) == _year_slices(reference), seed
        stream = next(records for current_seed, records, _, _ in
                      iter_quit_seed_records(prepared, [seed]) if current_seed == seed)
        assert _frame_signature(pd.DataFrame(stream)) == _frame_signature(reference), seed
        annual = quit_annual_event_values(stream)
        for dimension in ("Organisationseinheit", "Jobfamily"):
            grouped = reference.assign(year=reference["event_date"].dt.year).groupby(
                ["year", dimension], sort=True)[["headcount_change", "mak_change"]].sum()
            for (year, group), row in grouped.iterrows():
                assert annual[(year, dimension, group)] == (
                    int(row["headcount_change"]), float(row["mak_change"])), seed
        departed = set(reference["persnr"]) if not reference.empty else set()
        expected_active = [person not in departed for person in prepared.ids]
        assert batch[seed].active.tolist() == expected_active, seed
        assert batch[seed].mak.tolist() == [float(m) if a else 0.0
                                            for m, a in zip(prepared.mak, expected_active)], seed
    reference_seconds = perf_counter() - start
    print(f"seeds={len(seeds)}; preparation={prepare_seconds:.4f}s; "
          f"batch={batch_seconds:.4f}s; reference={reference_seconds:.4f}s; "
          "exact event, state, OE/JF annual-event parity=True")
    if "--stress-1000" in sys.argv:
        start = perf_counter()
        stress = run_quit_batch(prepared, range(1000))
        print(f"stress_seeds={len(stress)}; batch_seconds={perf_counter() - start:.4f}s; "
              "scope=quit events and final active/MAK only")
        start = perf_counter()
        stream_count = sum(len(quit_annual_event_values(records)) for _, records, _, _ in
                           iter_quit_seed_records(prepared, range(1000)))
        print(f"stream_seeds=1000; annual_keys={stream_count}; "
              f"stream_seconds={perf_counter() - start:.4f}s; no per-seed DataFrames retained")


if __name__ == "__main__":
    main()
