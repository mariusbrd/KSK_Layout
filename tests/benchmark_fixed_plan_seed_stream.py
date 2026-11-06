"""Time the exact fixed-plan seed stream; outputs contain no employee data."""
from __future__ import annotations

import argparse
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
from abgaenge.params import default_params as departure_defaults
from zugaenge.enrichment import build_jf_to_cluster_map
from zugaenge.fixed_plan_seed_stream import (
    iter_fixed_plan_coupled_seeds, iter_fixed_plan_regular_coupled_seeds,
)
from zugaenge.params import default_params as hire_defaults


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--target", default="2026-12-31")
    parser.add_argument("--compare-regular", action="store_true")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")
    _freeze_context()
    _reset_state()
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    start, target = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(args.target)
    people = _prepare_employee_forecast_base(snapshot, atz, start)
    dep, hire = departure_defaults(), hire_defaults()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["new_hires"].update(active=True, count_per_year=20,
                              strategy="Fill Vacancies")
    hire["azubi"].update(active=True, new_cases_per_year=4, retention_rate=1.0,
                         jf_to_cluster_map=build_jf_to_cluster_map(snapshot))
    hire["trainee"].update(active=True, new_cases_per_year=4)
    if args.compare_regular:
        hire["azubi"]["active"] = False
        hire["trainee"]["active"] = False
        started = perf_counter()
        reference_hires = sum(
            len(accessions["events"])
            for _, _, _, accessions in iter_fixed_plan_coupled_seeds(
                snapshot, people, atz, start, target, dep, hire,
                range(args.seeds)))
        reference_seconds = perf_counter() - started
        started = perf_counter()
        compact_profile: dict[str, float] = {}
        compact_hires = sum(
            len(hires)
            for _, _, _, hires, _ in iter_fixed_plan_regular_coupled_seeds(
                snapshot, people, atz, start, target, dep, hire,
                range(args.seeds), compact_profile))
        compact_seconds = perf_counter() - started
        assert compact_hires == reference_hires
        print(f"regular_coupled_seeds={args.seeds}; reference_s={reference_seconds:.3f}; "
              f"compact_s={compact_seconds:.3f}; hires={compact_hires}; "
              "scope=departures_and_raw_regular_hires")
        for phase, seconds in compact_profile.items():
            print(f"compact_{phase}={seconds:.3f}s ({seconds / compact_seconds:.1%})")
        return
    profile: dict[str, float] = {}
    started = perf_counter()
    count = departure_count = accession_count = 0
    for _, departures, _, accessions in iter_fixed_plan_coupled_seeds(
            snapshot, people, atz, start, target, dep, hire,
            range(args.seeds), profile):
        count += 1
        departure_count += len(departures)
        accession_count += len(accessions["events"])
    elapsed = perf_counter() - started
    print(f"seeds={count}; target={target.date()}; total_s={elapsed:.3f}; "
          f"per_seed_s={elapsed / count:.3f}; departures={departure_count}; "
          f"accessions={accession_count}")
    for phase, seconds in profile.items():
        print(f"{phase}={seconds:.3f}s ({seconds / elapsed:.1%})")


if __name__ == "__main__":
    main()
