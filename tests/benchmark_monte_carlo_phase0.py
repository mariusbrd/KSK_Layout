"""Repeatable, read-only timing of the current CompactPlus result pipeline.

Run: py tests/benchmark_monte_carlo_phase0.py
Uses the same frozen local input settings as benchmark_page_rerun_paths.py.
This script reports timings and process memory, never employee records.
"""
from __future__ import annotations

from copy import deepcopy
import ctypes
import json
import logging
from pathlib import Path
import sys
import threading
import time
import warnings

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state,
)
from abgaenge.params import default_params as abg_defaults
from zugaenge.params import default_params as zug_defaults
from dataloader import loader
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from utils.org_simulation_events import build_simulation_event_summary
from utils.status_quo_baseline import build_status_quo_snapshot
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def _rss_mib() -> float:
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = ctypes.c_void_p
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MemoryCounters), ctypes.c_ulong]
    get_memory.restype = ctypes.c_int
    ok = get_memory(get_process(), ctypes.byref(counters), counters.cb)
    if not ok:
        raise OSError("GetProcessMemoryInfo failed")
    return counters.WorkingSetSize / (1024 * 1024)


def _measure(call):
    samples = [_rss_mib()]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.01):
            samples.append(_rss_mib())

    worker = threading.Thread(target=sample, daemon=True)
    worker.start()
    start = time.perf_counter()
    try:
        value = call()
    finally:
        elapsed = time.perf_counter() - start
        stop.set()
        worker.join()
        samples.append(_rss_mib())
    return value, {"seconds": round(elapsed, 4), "rss_start_mib": round(samples[0], 1),
                   "rss_peak_mib": round(max(samples), 1), "rss_end_mib": round(samples[-1], 1)}


def main() -> None:
    logging.disable(logging.WARNING)
    warnings.filterwarnings("ignore", category=FutureWarning)
    _freeze_context()
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "phase0_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "phase0_compact_plus")
    (snapshot, _, _, source_summary), loading = _measure(loader.load_and_prepare_data)
    atz, atz_loading = _measure(lambda: loader.load_atz_data_cached(str(ROOT), None, None, None))
    source, bundle, source_signature = page._get_simulation_cluster_context(source_summary)
    target = pd.Timestamp("2026-12-31")
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(snapshot, FROZEN_STICHTAG))
    output = {"environment": {"python": sys.version.split()[0], "pandas": pd.__version__,
                               "snapshot_rows": len(snapshot), "people": snapshot.PersNr.dropna().nunique(),
                               "base_date": str(FROZEN_STICHTAG.date()), "target_date": str(target.date())},
              "load_snapshot": loading, "load_atz": atz_loading, "scenarios": {}}

    for label, annual_hires in (("no_entries", 0), ("new_hires_20_per_year", 20)):
        abg = abg_defaults()
        zug = zug_defaults()
        if annual_hires:
            zug["new_hires"].update(active=True, count_per_year=annual_hires,
                                    strategy="Fill Vacancies")
        result, simulation = _measure(lambda: simulate_compact_snapshot(
            snapshot_df=snapshot, df_atz=atz, target_date=target,
            base_date=FROZEN_STICHTAG, abgaenge_params=deepcopy(abg),
            zugaenge_params=deepcopy(zug), active_cluster_source=source,
            cluster_mapping_bundle=bundle, cluster_source_signature=source_signature,
        ))
        final, compact_prep = _measure(lambda: compact.prepare_compact_data(result.future_snapshot_df))
        context, context_prep = _measure(lambda: page._build_simulation_event_context(
            result, bundle, f"phase0::{label}", baseline_snapshot=baseline,
            final_snapshot=final, zugaenge_params=zug, abgaenge_params=abg,
            df_atz=atz, run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
            source_snapshot=snapshot,
        ))
        summary, summary_prep = _measure(lambda: build_simulation_event_summary(
            context, expected_run_signature=f"phase0::{label}"))
        yearly = {}
        for dimension in ("Jobfamily", "JF-Cluster"):
            _, yearly[dimension] = _measure(lambda dimension=dimension: build_jobfamily_yearly_view(
                context, summary, dimension=dimension))
        output["scenarios"][label] = {
            "simulation": simulation, "compact_prepare": compact_prep,
            "event_context": context_prep, "event_summary": summary_prep,
            "jobfamily_yearly": yearly,
            "departure_events": result.metadata["abgaenge_events"],
            "accession_events": result.metadata["zugaenge_events"],
            "oe_stock_status": summary["stock_reconciliation"]["status"],
            "oe_stock_reason": summary["stock_reconciliation"].get("reason"),
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
