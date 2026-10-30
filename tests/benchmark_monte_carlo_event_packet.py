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
from utils.monte_carlo_event_packet import build_event_only_packet


def main() -> None:
    page = _load_page7_module()
    rows = []
    for scenario, seed in CASES:
        context = _bridge_context(scenario, seed=seed)
        totals = page._metric_totals(context["final_snapshot"])
        samples = {}
        for label, call in (("full", lambda: _packet(scenario, seed, context=context)),
                            ("events_only", lambda: build_event_only_packet(context, target_totals=totals))):
            start = time.perf_counter()
            packet = call()
            samples[label] = {"seconds": round(time.perf_counter() - start, 4), "packet": packet}
        assert samples["full"]["packet"]["target"] == samples["events_only"]["packet"]["target"]
        assert samples["full"]["packet"]["annual_values"] == samples["events_only"]["packet"]["annual_values"]
        rows.append({"scenario": scenario, "seed": seed,
                     "full_seconds": samples["full"]["seconds"],
                     "events_only_seconds": samples["events_only"]["seconds"],
                     "annual_values": len(samples["full"]["packet"]["annual_values"])})
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
