"""Measure seeded departure events plus four annual levels, without snapshots."""
from __future__ import annotations

from pathlib import Path
import sys
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state,
)
from abgaenge.params import default_params
from abgaenge.quit_batch_probe import (
    iter_quit_seed_records, prepare_quit_batch, prepare_quit_yearly,
)
from dataloader import loader
from dataloader.compact_simulation_engine import _prepare_employee_forecast_base
from utils.monte_carlo_departure_annual import (
    departure_annual_values, prepare_oe_stock_scopes, provisional_stock_values,
)
from utils.monte_carlo_metric_packet import LEVELS
from utils.org_simulation_reconciliation import grouped_stock_metrics, reporting_projection_base
from components.jobfamily_simulation_yearly import _dimension_values
from utils.status_quo_baseline import build_status_quo_snapshot


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    with_stock = "--stock" in sys.argv
    _freeze_context()
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "departure_annual_benchmark_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "departure_annual_benchmark_page")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    _, bundle, _ = page._get_simulation_cluster_context(source_summary)
    baseline = compact.prepare_compact_data(
        build_status_quo_snapshot(snapshot, FROZEN_STICHTAG))
    people = _prepare_employee_forecast_base(snapshot, atz, FROZEN_STICHTAG)
    target = pd.Timestamp("2028-12-31")
    dep = default_params()
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False,
                      atz_duration_ar_years=0.5, atz_duration_fr_years=0.5)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    start = perf_counter()
    prepared = prepare_quit_batch(people, atz, FROZEN_STICHTAG, target, dep)
    yearly = prepare_quit_yearly(baseline, bundle.oe_map)
    stock_openings = {}
    stock_scopes = None
    if with_stock:
        context = {"run_settings": {"exclusions": FROZEN_SETTINGS["exclusions"]},
                   "metadata": {"base_date": FROZEN_STICHTAG, "target_date": target},
                   "cluster_mapping": bundle.__dict__}
        projection = reporting_projection_base(context, baseline)
        projection["Organisationseinheit"] = projection["Organisationseinheit"].fillna("Nicht zugeordnet").astype(str).str.strip().replace("", "Nicht zugeordnet")
        projection["OE-Cluster"] = projection["Organisationseinheit"].map(bundle.oe_map).fillna("Nicht zugeordnet")
        stock_scopes = prepare_oe_stock_scopes(projection)
        for level in LEVELS:
            stock_frame = baseline.copy()
            if level in ("Jobfamily", "JF-Cluster"):
                stock_frame[level] = _dimension_values(stock_frame, level)
            else:
                stock_frame[level] = stock_frame[level].fillna("Nicht zugeordnet").astype(str).str.strip().replace("", "Nicht zugeordnet")
            stock_openings.update({(level, name): totals for name, totals in
                                   grouped_stock_metrics(stock_frame, level).items()})
        for level in ("Organisationseinheit", "OE-Cluster"):
            stock_openings.setdefault((level, "Nicht zugeordnet"), (0, 0.0))
    preparation = perf_counter() - start
    start = perf_counter()
    events = values = stock_values = 0
    for _, records, _, _ in iter_quit_seed_records(prepared, range(count)):
        events += len(records)
        values += len(departure_annual_values(records, yearly))
        if with_stock:
            stock_values += len(provisional_stock_values(
                stock_openings, records, yearly, range(2025, 2029), stock_scopes))
    print(f"seeds={count}; preparation_seconds={preparation:.4f}; "
          f"stream_and_annual_seconds={perf_counter() - start:.4f}; "
          f"events={events}; annual_values={values}; "
          f"provisional_stock_values={stock_values}")


if __name__ == "__main__":
    main()
