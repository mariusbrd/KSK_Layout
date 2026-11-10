"""Exact regular position stocks against independent product year-end runs."""
from __future__ import annotations

import pandas as pd
import pytest
from copy import deepcopy


@pytest.mark.parametrize("target_label,seed,strategy,annual_hires,quit_rate,atz_rate,ruh_cases,expect_atz_end,exclusion_mode", [
    ("2026-12-31", 123, "Fill Vacancies", 20, 0.0, 0.0, 0, False, "default"),
    ("2027-12-31", 123, "Random", 30, 0.25, 0.0, 0, False, "default"),
    ("2028-12-31", 999, "OrgUnit", 12, 0.12, 0.35, 0, False, "default"),
    ("2026-06-30", 77, "Fill Vacancies", 20, 0.12, 0.0, 0, False, "default"),
    ("2027-12-31", 77, "Fill Vacancies", 20, 0.12, 0.0, 4, False, "default"),
    ("2032-12-31", 7, "Fill Vacancies", 0, 0.12, 0.0, 0, True, "default"),
    ("2027-12-31", 42, "Fill Vacancies", 20, 0.12, 0.0, 0, False, "open"),
    ("2027-12-31", 77, "Fill Vacancies", 20, 0.12, 0.0, 4, False, "open"),
])
def test_position_year_matrix_matches_product(
    target_label: str, seed: int, strategy: str, annual_hires: int,
    quit_rate: float, atz_rate: float, ruh_cases: int, expect_atz_end: bool,
    exclusion_mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path
    from benchmark_page_rerun_paths import FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module, _reset_state
    from dataloader import loader
    from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source
    from dataloader.compact_simulation_engine import (
        _prepare_employee_forecast_base, _resolve_simulation_cluster_context,
        simulate_compact_snapshot,
    )
    import dataloader.compact_simulation_engine as simulation_engine
    from abgaenge.params import default_params as departure_defaults
    from zugaenge.params import default_params as hire_defaults
    from zugaenge.fixed_plan_seed_stream import iter_fixed_plan_regular_coupled_seeds
    from utils.compact_regular_stock import project_regular_position_stocks
    from utils.org_simulation_reconciliation import grouped_stock_metrics
    from utils.status_quo_baseline import build_status_quo_snapshot
    from components.jobfamily_simulation_yearly import _dimension_values

    if exclusion_mode == "open":
        monkeypatch.setattr(simulation_engine, "get_setting",
                            lambda key, default=None: deepcopy(FROZEN_SETTINGS.get(key, default)))

    if exclusion_mode == "open":
        exclusions = deepcopy(FROZEN_SETTINGS["exclusions"])
        exclusions.update(vorstand=False, ruhend_bv=False, org_units=[])
        monkeypatch.setitem(FROZEN_SETTINGS, "exclusions", exclusions)
    _freeze_context()
    _reset_state()
    root = Path(__file__).resolve().parents[1]
    positions, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(root), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp(target_label)
    people = _prepare_employee_forecast_base(positions, atz, start)
    compact = _load_page_module("*_Kompakt.py", "regular_stock_matrix_compact")
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(positions, start))
    source, bundle, signature = _resolve_simulation_cluster_context()
    mapped = apply_clusters_to_snapshot_from_source(baseline, source, mapping_bundle=bundle)
    dep, hire = departure_defaults(), hire_defaults()
    dep["quit"].update(quit_rate_base=quit_rate, use_quit_matrix=False)
    dep["atz"].update(new_atz_rate=atz_rate, use_atz_matrix=False)
    dep["ruhend"]["ruhend_new_cases_per_year"] = ruh_cases
    hire["azubi"]["active"] = False
    hire["trainee"]["active"] = False
    hire["new_hires"].update(
        active=True, count_per_year=annual_hires, strategy=strategy,
        target_org_unit=(str(people["Organisationseinheit"].dropna().iloc[0])
                         if strategy == "OrgUnit" else None),
    )
    _, departures, _, _, new_people = next(iter_fixed_plan_regular_coupled_seeds(
        positions, people, atz, start, end, dep, hire, [seed]))
    if expect_atz_end:
        assert departures["reason_code"].eq("ATZ_END").any()
    if ruh_cases:
        assert departures["reason_code"].astype(str).str.contains("RUH", case=False).any()
    projected = project_regular_position_stocks(
        baseline, mapped, departures, new_people, base_date=start, target_date=end,
        exclude_ruhend=bool(FROZEN_SETTINGS["exclusions"]["ruhend_bv"]),
        grouping_method="product")
    indexed = project_regular_position_stocks(
        baseline, mapped, departures, new_people, base_date=start, target_date=end,
        exclude_ruhend=bool(FROZEN_SETTINGS["exclusions"]["ruhend_bv"]),
        grouping_method="indexed")
    dimensions = ("Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster")
    for dimension in dimensions:
        pd.testing.assert_frame_equal(projected[dimension], indexed[dimension],
                                      check_exact=True)
    for date in sorted(projected["Organisationseinheit"]["Stichtag"].unique()):
        date = pd.Timestamp(date)
        reference = simulate_compact_snapshot(
            positions, atz, date, base_date=start,
            abgaenge_params={**dep, "random_seed": seed},
            zugaenge_params={**hire, "random_seed": seed},
            active_cluster_source=source, cluster_mapping_bundle=bundle,
            cluster_source_signature=signature, include_audits=False,
        )
        frame = compact.prepare_compact_data(reference.future_snapshot_df)
        for dimension in dimensions:
            work = (frame if dimension in dimensions[:2] else frame.assign(
                **{dimension: _dimension_values(frame, dimension)}))
            actual = {row[dimension]: (row["Köpfe"], row["MAK"])
                      for _, row in projected[dimension].loc[
                          projected[dimension]["Stichtag"].eq(date)].iterrows()}
            assert actual == grouped_stock_metrics(work, dimension), (target_label, seed, dimension, date)
