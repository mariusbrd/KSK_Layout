"""Time full versus event-only packet extraction on the same seeded results.

Run: py tests/benchmark_monte_carlo_event_packet.py
Simulation, page import and target-total calculation are outside each timer.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test_compact_sim_cluster_source_consistency import _load_page7_module
from test_monte_carlo_metric_packet import CASES, _packet
from test_org_simulation_reconciliation import _bridge_context
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view, prepare_jobfamily_baseline
from utils.monte_carlo_event_packet import build_event_only_packet
from utils.monte_carlo_metric_packet import LEVELS, make_metric_packet
from utils.org_simulation_events import aggregate_simulation_events, normalize_simulation_events


def _timed_stages(context, totals):
    durations = {}
    def measure(label, call):
        start = time.perf_counter()
        value = call()
        durations[label] = round(time.perf_counter() - start, 4)
        return value
    signature = context["run_signature"]
    events = measure("normalize_events", lambda: normalize_simulation_events(
        context, expected_run_signature=signature))
    annual = {level: measure(f"aggregate_{level}", lambda level=level:
                     aggregate_simulation_events(events, dimension=level))
              for level in ("Organisationseinheit", "OE-Cluster")}
    summary = {"run_signature": signature, "events": events, "metadata": context["metadata"]}
    for level in ("Jobfamily", "JF-Cluster"):
        annual[level] = measure(f"build_{level}", lambda level=level:
                                build_jobfamily_yearly_view(context, summary,
                                                            dimension=level, include_stock=False)["annual"])
    packet = measure("make_packet", lambda: make_metric_packet(
        target_totals=totals, annual_tables=annual,
        stock_status={level: "Nicht berechnet" for level in LEVELS}))
    return packet, durations


def main() -> None:
    page = _load_page7_module()
    rows = []
    for scenario, seed in CASES:
        context = _bridge_context(scenario, seed=seed)
        totals = page._metric_totals(context["final_snapshot"])
        prepared = prepare_jobfamily_baseline(context["baseline_snapshot"])
        samples = {}
        for label, call in (("full", lambda: _packet(scenario, seed, context=context)),
                            ("events_only", lambda: build_event_only_packet(context, target_totals=totals)),
                            ("events_prepared", lambda: build_event_only_packet(
                                context, target_totals=totals, baseline_cache=prepared))):
            start = time.perf_counter()
            packet = call()
            samples[label] = {"seconds": round(time.perf_counter() - start, 4), "packet": packet}
        assert samples["full"]["packet"]["target"] == samples["events_only"]["packet"]["target"]
        assert samples["full"]["packet"]["annual_values"] == samples["events_only"]["packet"]["annual_values"]
        assert samples["events_prepared"]["packet"] == samples["events_only"]["packet"]
        staged, durations = _timed_stages(context, totals)
        assert staged == samples["events_only"]["packet"]
        rows.append({"scenario": scenario, "seed": seed,
                     "full_seconds": samples["full"]["seconds"],
                     "events_only_seconds": samples["events_only"]["seconds"],
                     "events_prepared_seconds": samples["events_prepared"]["seconds"],
                     "annual_values": len(samples["full"]["packet"]["annual_values"]),
                     "stages_seconds": durations})
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
