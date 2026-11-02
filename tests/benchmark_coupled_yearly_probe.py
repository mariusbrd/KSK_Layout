"""Multi-year OE/JF parity of isolated seed records against the saved-run path."""
from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context,
    _load_page_module, _reset_state,
)
from dataloader import loader
from dataloader.compact_simulation_engine import (
    _append_new_people_rows, _apply_salary_automation_to_employee_state,
    _finalize_future_snapshot, _normalize_persnr_series,
    _prepare_employee_forecast_base, _resolve_atz_status_map,
    _update_existing_rows, simulate_compact_snapshot,
)
from abgaenge.params import default_params as departure_defaults
from abgaenge.quit_batch_probe import (
    iter_quit_seed_records_with_atz, prepare_atz_pivot_for_probe,
    prepare_quit_batch,
)
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.org_simulation_events import build_simulation_event_summary
from utils.status_quo_baseline import build_status_quo_snapshot
from zugaenge.coupled_batch_probe import (
    iter_coupled_seed_records_with_atz, prepare_coupled_batch,
    replay_final_employee_state,
)
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events
from zugaenge.params import default_params as hire_defaults


def _equal_frames(reference: dict, candidate: dict, names: tuple[str, ...]) -> None:
    for name in names:
        pd.testing.assert_frame_equal(candidate[name], reference[name], check_exact=True,
                                      obj=name)


def main() -> None:
    logging.getLogger("streamlit").setLevel(logging.ERROR)
    _freeze_context()
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "yearly_probe_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "yearly_probe_page")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, signature = page._get_simulation_cluster_context(source_summary)
    base, target = FROZEN_STICHTAG, pd.Timestamp("2028-12-31")
    people = _prepare_employee_forecast_base(snapshot, atz, base)
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(snapshot, base))

    for label, annual_hires in (("departures_only", 0), ("with_hires", 20)):
        dep_params, hire_params = departure_defaults(), hire_defaults()
        dep_params["atz"].update(new_atz_rate=0.35, use_atz_matrix=False,
                                 atz_duration_ar_years=0.5,
                                 atz_duration_fr_years=0.5)
        dep_params["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
        if annual_hires:
            hire_params["new_hires"].update(active=True, count_per_year=annual_hires,
                                            strategy="Fill Vacancies")
            prepared = prepare_coupled_batch(people, atz, base, target,
                                             dep_params, hire_params)
            seed_stream = ((seed, departures, pivot, hires, new_people)
                           for seed, departures, pivot, _, hires, new_people
                           in iter_coupled_seed_records_with_atz(prepared, (7, 42)))
        else:
            prepared_dep = prepare_quit_batch(people, atz, base, target, dep_params)
            initial_pivot = prepare_atz_pivot_for_probe(prepared_dep, atz, dep_params)
            seed_stream = ((seed, departures, pivot, None, [])
                           for seed, departures, pivot in iter_quit_seed_records_with_atz(
                               prepared_dep, (7, 42), initial_pivot))

        for seed, departures, pivot, hire_records, new_people in seed_stream:
            run_dep = {**dep_params, "random_seed": seed}
            run_hire = deepcopy(hire_params)
            run_hire["random_seed"] = seed
            reference = simulate_compact_snapshot(
                snapshot, atz, target, base_date=base,
                abgaenge_params=run_dep, zugaenge_params=run_hire,
                active_cluster_source=source, cluster_mapping_bundle=bundle,
                cluster_source_signature=signature, include_audits=False)
            pd.testing.assert_frame_equal(
                pd.DataFrame(departures),
                reference.abgaenge_result["events_person_level"], check_exact=True)
            pd.testing.assert_frame_equal(
                pivot, reference.abgaenge_result["tables"]["atz_pivot"],
                check_exact=True)
            if hire_records is None:
                hires = reference.zugaenge_result["events"].iloc[0:0].copy()
            else:
                run_hire["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(snapshot)
                hires = enrich_zugaenge_events(
                    pd.DataFrame(hire_records), snapshot, run_hire,
                    cluster_mapping_bundle=bundle,
                    active_cluster_source_signature=signature)
            pd.testing.assert_frame_equal(
                hires, reference.zugaenge_result["events"], check_exact=True)

            final_people = replay_final_employee_state(people, departures, new_people)
            final_people["PersNr"] = _normalize_persnr_series(final_people["PersNr"])
            final_people = _apply_salary_automation_to_employee_state(
                final_people, target_date=target, events_df=hires)
            pd.testing.assert_frame_equal(final_people, reference.future_employee_df,
                                          check_exact=True)
            future = _update_existing_rows(
                snapshot, final_people, target, _resolve_atz_status_map(pivot, target))
            future = _append_new_people_rows(
                future, snapshot, final_people, {"events": hires})
            future = _finalize_future_snapshot(
                future, target, active_cluster_source=source,
                cluster_mapping_bundle=bundle)
            pd.testing.assert_frame_equal(future, reference.future_snapshot_df,
                                          check_exact=True)

            final_reference = compact.prepare_compact_data(reference.future_snapshot_df)
            final_candidate = compact.prepare_compact_data(future)
            pd.testing.assert_frame_equal(final_candidate, final_reference,
                                          check_exact=True)
            context = page._build_simulation_event_context(
                reference, bundle, f"yearly::{label}::{seed}",
                baseline_snapshot=baseline, final_snapshot=final_reference,
                zugaenge_params=hire_params, abgaenge_params=dep_params,
                df_atz=atz, run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
                source_snapshot=snapshot)
            candidate = dict(context)
            candidate["departure_events"] = pd.DataFrame(departures)
            candidate["accession_events"] = hires
            candidate["raw_final_snapshot"] = future
            candidate["future_employee_state"] = final_people
            candidate["final_snapshot"] = final_candidate
            # The detailed ATZ schedule audit is not yet emitted by the batch probe.
            # The six-column pivot itself was independently checked above.
            reference_summary = build_simulation_event_summary(
                context, expected_run_signature=context["run_signature"])
            candidate_summary = build_simulation_event_summary(
                candidate, expected_run_signature=context["run_signature"])
            _equal_frames(reference_summary, candidate_summary,
                          ("events", "annual_oe", "annual_cluster"))
            event_years = set(candidate_summary["events"]["Jahr"].dropna().astype(int))
            assert event_years == {2025, 2026, 2027, 2028}, (label, seed, event_years)
            departure_types = set(candidate_summary["events"]["Ereignisart"])
            assert {"ATZ_AR_TO_FR", "ATZ_END"}.issubset(departure_types), (
                label, seed, departure_types)
            ref_bridge = reference_summary["stock_reconciliation"]
            probe_bridge = candidate_summary["stock_reconciliation"]
            assert (probe_bridge["status"], probe_bridge["reason"]) == (
                ref_bridge["status"], ref_bridge["reason"])
            _equal_frames(ref_bridge, probe_bridge,
                          ("annual", "checks", "person_diagnostics"))
            for dimension in ("Jobfamily", "JF-Cluster"):
                ref_jf = build_jobfamily_yearly_view(
                    context, reference_summary, dimension=dimension)
                probe_jf = build_jobfamily_yearly_view(
                    candidate, candidate_summary, dimension=dimension)
                assert probe_jf["stock_reason"] == ref_jf["stock_reason"]
                _equal_frames(ref_jf, probe_jf,
                              ("annual", "stock", "stock_checks"))
            if annual_hires:
                assert probe_bridge["status"] == "Nicht verfügbar"
                assert probe_bridge["annual"].empty
            else:
                assert not probe_bridge["annual"].empty
            print(f"yearly_parity={label}; seed={seed}; years=2025-2028; "
                  f"oe_status={probe_bridge['status']}; atz_rows={len(pivot)}")


if __name__ == "__main__":
    main()
