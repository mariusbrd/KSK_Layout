"""Read-only productive OE stock/event residual audit; prints aggregates only."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import (  # noqa: E402
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context, _load_page_module,
    _reset_state,
)
from dataloader import loader  # noqa: E402
from dataloader.cluster_manager import apply_clusters_to_snapshot_from_source  # noqa: E402
from dataloader.compact_simulation_engine import (  # noqa: E402
    _resolve_simulation_cluster_context, simulate_compact_snapshot,
)
from abgaenge.params import default_params as departure_defaults  # noqa: E402
from zugaenge.params import default_params as hire_defaults  # noqa: E402
from utils.org_simulation_events import build_simulation_event_summary  # noqa: E402
from utils.compact_regular_stock import (  # noqa: E402
    project_regular_group_stock_from_events, reconcile_regular_year_stock,
)
from utils.org_simulation_reconciliation import grouped_stock_metrics  # noqa: E402
from utils.status_quo_baseline import build_status_quo_snapshot  # noqa: E402


def main() -> None:
    _freeze_context()
    _reset_state()
    positions, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    compact = _load_page_module("*_Kompakt.py", "stock_diagnostic_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "stock_diagnostic_page")
    start = pd.Timestamp(FROZEN_STICHTAG)
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(positions, start))
    source, bundle, signature = _resolve_simulation_cluster_context()
    mapped_baseline = apply_clusters_to_snapshot_from_source(
        baseline, source, mapping_bundle=bundle)
    dep, hire = departure_defaults(), hire_defaults()
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    hire["azubi"]["active"] = False
    hire["trainee"]["active"] = False
    hire["new_hires"].update(active=True, count_per_year=20,
                              strategy="Fill Vacancies")
    for seed in (7, 42):
        results = {}
        for year in (2026, 2027):
            end = pd.Timestamp(year, 12, 31)
            results[year] = simulate_compact_snapshot(
                positions, atz, end, base_date=start,
                abgaenge_params={**deepcopy(dep), "random_seed": seed},
                zugaenge_params={**deepcopy(hire), "random_seed": seed},
                active_cluster_source=source,
                cluster_mapping_bundle=bundle,
                cluster_source_signature=signature,
                include_audits=False)
        signature_run = f"stock-diagnostic::{seed}"
        context = page._build_simulation_event_context(
            results[2027], bundle, signature_run,
            baseline_snapshot=baseline,
            final_snapshot=compact.prepare_compact_data(
                results[2027].future_snapshot_df),
            zugaenge_params=hire, abgaenge_params=dep, df_atz=atz,
            run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
            source_snapshot=positions)
        summary = build_simulation_event_summary(
            context, expected_run_signature=signature_run)
        projected = project_regular_group_stock_from_events(
            baseline, summary["events"], base_date=start,
            target_date=pd.Timestamp("2027-12-31"))
        jf_detail = reconcile_regular_year_stock(
            mapped_baseline, results[2026].future_snapshot_df,
            summary["events"], year=2026, dimension="JF-Cluster",
            start_date=start)
        jf_affected = jf_detail.loc[
            jf_detail["residual_mak"].abs().gt(1e-6)
            | jf_detail["residual_heads"].ne(0)]
        print(f"seed={seed}; jf_affected={len(jf_affected)}; "
              f"jf_causes={jf_affected['cause_hint'].value_counts().to_dict()}; "
              f"jf_total_residual={jf_affected['residual_mak'].sum():.6f}; "
              f"jf_head_residual={jf_affected['residual_heads'].sum():.0f}")
        for year, before in ((2026, baseline),
                             (2027, results[2026].future_snapshot_df)):
            expected = grouped_stock_metrics(
                results[year].future_snapshot_df, "Organisationseinheit")
            observed = projected.loc[projected["Stichtag"].eq(
                pd.Timestamp(year, 12, 31))].set_index("Organisationseinheit")
            stock_errors = [abs(float(observed.at[group, "MAK"]) - mak)
                            for group, (_, mak) in expected.items()]
            head_errors = [abs(float(observed.at[group, "Köpfe"]) - heads)
                           for group, (heads, _) in expected.items()]
            print(f"seed={seed}; year={year}; projected_max_mak_error={max(stock_errors):.12f}; "
                  f"projected_head_errors={sum(error != 0 for error in head_errors)}")
            detail = reconcile_regular_year_stock(
                before, results[year].future_snapshot_df,
                summary["events"], year=year,
                start_date=start if year == 2026 else None)
            affected = detail.loc[detail["residual_mak"].abs().gt(1e-6)]
            causes = affected.groupby("cause_hint").agg(
                person_groups=("persnr", "size"),
                residual_mak=("residual_mak", "sum"),
                absolute_mak=("residual_mak", lambda x: x.abs().sum()),
            )
            grouped = detail.groupby("Organisationseinheit")["residual_mak"].sum()
            print(f"seed={seed}; year={year}; affected_person_groups={len(affected)}; "
                  f"affected_oes={int(grouped.abs().gt(1e-6).sum())}; "
                  f"max_abs_oe_mak={grouped.abs().max():.6f}; "
                  f"total_residual_mak={grouped.sum():.6f}")
            print(causes.to_string())


if __name__ == "__main__":
    main()
