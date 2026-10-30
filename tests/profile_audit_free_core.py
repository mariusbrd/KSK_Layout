"""Function-level cProfile of one audit-free productive CompactPlus run.

Run: py tests/profile_audit_free_core.py [--no-hires]
Profiler times are instrumented and must not be used as real wall times.
"""
from __future__ import annotations

import cProfile
from copy import deepcopy
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state
from abgaenge.params import default_params as abg_defaults
from zugaenge.params import default_params as zug_defaults
from dataloader import loader
from dataloader.compact_simulation_engine import simulate_compact_snapshot


def main() -> None:
    _freeze_context()
    _reset_state()
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "audit_free_profile_page")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, source_signature = page._get_simulation_cluster_context(source_summary)
    abg, zug = abg_defaults(), zug_defaults()
    hires = 0 if "--no-hires" in sys.argv else 20
    if hires:
        zug["new_hires"].update(active=True, count_per_year=hires, strategy="Fill Vacancies")
    profiler = cProfile.Profile()
    result = profiler.runcall(
        simulate_compact_snapshot, snapshot_df=snapshot, df_atz=atz,
        target_date=pd.Timestamp("2026-12-31"), base_date=FROZEN_STICHTAG,
        abgaenge_params=deepcopy(abg), zugaenge_params=deepcopy(zug),
        active_cluster_source=source, cluster_mapping_bundle=bundle,
        cluster_source_signature=source_signature, include_audits=False)
    rows = []
    import pstats
    for (filename, line, name), (primitive, calls, own, cumulative, _) in pstats.Stats(profiler).stats.items():
        path = Path(filename)
        if path.suffix != ".py" or ROOT not in path.parents:
            continue
        rows.append((cumulative, own, calls, str(path.relative_to(ROOT)), line, name))
    for cumulative, own, calls, filename, line, name in sorted(rows, reverse=True)[:45]:
        print(f"{cumulative:9.4f} cumulative  {own:8.4f} own  {calls:6} calls  {filename}:{line} {name}")
    print(f"new_hires_per_year={hires}; events: departures={result.metadata['abgaenge_events']}, accessions={result.metadata['zugaenge_events']}")


if __name__ == "__main__":
    main()
