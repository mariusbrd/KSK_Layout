"""Profile the corrected productive JF/JF-cluster yearly views for one saved run."""
from __future__ import annotations

import cProfile
import io
from pathlib import Path
import pstats
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from test_monte_carlo_production_multi_year_reference import _payload
from utils.org_simulation_events import build_simulation_event_summary


def profile(context):
    if not context["run_signature"].endswith("departures_only::7"):
        return
    summary = build_simulation_event_summary(
        context, expected_run_signature=context["run_signature"])
    profiler = cProfile.Profile()
    profiler.enable()
    for dimension in ("Jobfamily", "JF-Cluster"):
        build_jobfamily_yearly_view(context, summary, dimension=dimension)
    profiler.disable()
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats("cumtime").print_stats(20)
    print(output.getvalue(), flush=True)


if __name__ == "__main__":
    _payload(probe=profile)
