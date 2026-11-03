"""Multi-year OE/JF parity of isolated seed records against the saved-run path."""
from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
import sys
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context,
    _load_page_module, _reset_state,
)
from dataloader import loader
import dataloader.compact_simulation_engine as simulation_engine
from dataloader.compact_simulation_engine import (
    _append_new_people_rows, _apply_salary_automation_to_employee_state,
    _finalize_future_snapshot, _normalize_persnr_series,
    _prepare_employee_forecast_base, _resolve_atz_status_map,
    _update_existing_rows, simulate_compact_snapshot,
)
from abgaenge.params import default_params as departure_defaults
from abgaenge.quit_batch_probe import (
    iter_quit_seed_records_with_atz, prepare_atz_pivot_for_probe,
    prepare_quit_batch, prepare_quit_yearly,
)
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view, _dimension_values
from utils.org_simulation_events import build_simulation_event_summary
from utils.monte_carlo_departure_annual import (
    departure_annual_values, prepare_oe_stock_scopes, provisional_stock_values,
    provisional_target_head_mak,
    target_mak_in_position_order,
    target_head_mak_with_hires,
    reporting_target_from_position_probe,
    prepare_target_position_model, target_head_mak_reporting_bridge,
    prepare_target_people, replay_target_people_compact,
    prepare_target_array_model, target_head_mak_from_array_model,
    excluded_forecast_people,
)
from utils.monte_carlo_metric_packet import LEVELS, make_metric_packet
from utils.org_simulation_reconciliation import grouped_stock_metrics, reporting_projection_base
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
    matrix_mode = sys.argv[1:] == ["--matrix"]
    exclusions_mode = sys.argv[1:] == ["--target-exclusions"]
    sample_mode = sys.argv[1:] == ["--target-sample"]
    atz_end_mode = sys.argv[1:] == ["--target-atz-end"]
    training_clear_mode = sys.argv[1:] == ["--target-training-clear"]
    training_mode = sys.argv[1:] == ["--target-training"] or training_clear_mode
    target_only = (sys.argv[1:] == ["--target-only"] or exclusions_mode
                   or sample_mode or atz_end_mode or training_mode)
    if sys.argv[1:] not in ([], ["--matrix"], ["--target-only"],
                            ["--target-exclusions"], ["--target-sample"],
                            ["--target-atz-end"], ["--target-training"],
                            ["--target-training-clear"]):
        raise SystemExit("Usage: py tests/benchmark_coupled_yearly_probe.py [--matrix|--target-only|--target-exclusions|--target-sample|--target-atz-end|--target-training|--target-training-clear]")
    if exclusions_mode:
        FROZEN_SETTINGS["exclusions"] = {
            **FROZEN_SETTINGS["exclusions"], "vorstand": False,
            "ruhend_bv": False, "org_units": [],
        }
        simulation_engine.get_setting = lambda key, default=None: deepcopy(
            FROZEN_SETTINGS.get(key, default))
    if training_mode:
        FROZEN_SETTINGS["exclusions"] = {
            **FROZEN_SETTINGS["exclusions"],
            "special_groups": ([] if training_clear_mode else
                               ["ausbildung_nachwuchs",
                                "jobfamily_validation_special_positions",
                                "sollarbeitszeit_001_positions"]),
            "org_units": [] if training_clear_mode else FROZEN_SETTINGS["exclusions"]["org_units"],
            "vorstand": False if training_clear_mode else FROZEN_SETTINGS["exclusions"]["vorstand"],
            "ruhend_bv": False if training_clear_mode else FROZEN_SETTINGS["exclusions"]["ruhend_bv"],
        }
        simulation_engine.get_setting = lambda key, default=None: deepcopy(
            FROZEN_SETTINGS.get(key, default))
    logging.getLogger("streamlit").setLevel(logging.ERROR)
    _freeze_context()
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "yearly_probe_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "yearly_probe_page")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, signature = page._get_simulation_cluster_context(source_summary)
    base, target = FROZEN_STICHTAG, pd.Timestamp(
        "2032-12-31" if atz_end_mode else
        "2026-12-31" if training_mode else "2028-12-31")
    people = _prepare_employee_forecast_base(snapshot, atz, base)
    prepared_positions = prepare_target_position_model(snapshot)
    prepared_target_people = prepare_target_people(people)
    target_array_model = prepare_target_array_model(snapshot)
    occupied_counts = snapshot.loc[snapshot["PersNr"].notna(), "PersNr"].value_counts()
    assert occupied_counts.gt(1).any(), "No multi-position employee in source"
    baseline = compact.prepare_compact_data(build_status_quo_snapshot(snapshot, base))
    departure_yearly = prepare_quit_yearly(baseline, bundle.oe_map)
    stock_openings = {}
    stock_scopes = None
    for level in LEVELS:
        stock_frame = baseline.copy()
        if level in ("Jobfamily", "JF-Cluster"):
            stock_frame[level] = _dimension_values(stock_frame, level)
        else:
            stock_frame[level] = stock_frame[level].fillna("Nicht zugeordnet").astype(str).str.strip().replace("", "Nicht zugeordnet")
        stock_openings.update({(level, name): totals
                               for name, totals in grouped_stock_metrics(stock_frame, level).items()})
    for level in ("Organisationseinheit", "OE-Cluster"):
        stock_openings.setdefault((level, "Nicht zugeordnet"), (0, 0.0))

    scenarios = (
        (("existing_atz_end", 0, "existing_atz_only"),) if atz_end_mode else
        (("training_hires", 8, "training_hires"),) if training_mode else
        (("departures_only", 0, "standard"), ("with_hires", 20, "standard"))
        if exclusions_mode else
        (("departures_only", 0, "standard"), ("with_hires", 20, "standard"),
         ("random_hires", 30, "random_hires"),
         ("existing_atz_only", 0, "existing_atz_only"),
         ("high_quit", 0, "high_quit"))
        if target_only else
        (("departures_only", 0, "standard"), ("with_hires", 20, "standard"))
        if not matrix_mode else (
            ("existing_atz_only", 0, "existing_atz_only"),
            ("high_quit", 0, "high_quit"),
            ("random_hires", 30, "random_hires"),
        )
    )
    for label, annual_hires, variant in scenarios:
        dep_params, hire_params = departure_defaults(), hire_defaults()
        if variant == "existing_atz_only":
            dep_params["components"] = {
                key: key == "atz" for key in dep_params["components"]}
            dep_params["atz"].update(new_atz_rate=0.0, use_atz_matrix=False)
        elif variant == "high_quit":
            dep_params["atz"].update(new_atz_rate=0.0, use_atz_matrix=False)
            dep_params["quit"].update(quit_rate_base=0.25, use_quit_matrix=False)
        else:
            dep_params["atz"].update(new_atz_rate=0.35, use_atz_matrix=False,
                                     atz_duration_ar_years=0.5,
                                     atz_duration_fr_years=0.5)
            dep_params["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
        if annual_hires:
            if variant == "training_hires":
                hire_params["azubi"].update(active=True, new_cases_per_year=4,
                                             retention_rate=0.5)
                hire_params["trainee"].update(active=True, new_cases_per_year=4)
            else:
                hire_params["new_hires"].update(active=True, count_per_year=annual_hires,
                                                strategy=("Random" if variant == "random_hires"
                                                          else "Fill Vacancies"))
            if training_mode:
                # The isolated hire probe deliberately supports new hires only.
                seed_stream = ((seed, None, None, None, None) for seed in (7, 42))
            else:
                prepared = prepare_coupled_batch(people, atz, base, target,
                                                 dep_params, hire_params)
                selected_seeds = range(1000, 1010) if sample_mode else (7, 42)
                seed_stream = ((seed, departures, pivot, hires, new_people)
                               for seed, departures, pivot, _, hires, new_people
                               in iter_coupled_seed_records_with_atz(prepared, selected_seeds))
        else:
            prepared_dep = prepare_quit_batch(people, atz, base, target, dep_params)
            initial_pivot = prepare_atz_pivot_for_probe(prepared_dep, atz, dep_params)
            selected_seeds = ((7, 42) if atz_end_mode else
                              range(1000, 1020) if sample_mode else (7, 42, 123, 999))
            seed_stream = ((seed, departures, pivot, None, [])
                           for seed, departures, pivot in iter_quit_seed_records_with_atz(
                               prepared_dep, selected_seeds, initial_pivot))

        for seed, departures, pivot, hire_records, new_people in seed_stream:
            if target_only and variant == "existing_atz_only":
                atz_reasons = {event["reason_code"] for event in departures
                               if str(event["reason_code"]).startswith("ATZ_")}
                assert ("ATZ_END" in atz_reasons if atz_end_mode else atz_reasons), (
                    label, seed, "missing_existing_atz_events", atz_reasons)
                print(f"existing_atz_events={label}; seed={seed}; reasons={sorted(atz_reasons)}")
            run_dep = {**dep_params, "random_seed": seed}
            run_hire = deepcopy(hire_params)
            run_hire["random_seed"] = seed
            reference = simulate_compact_snapshot(
                snapshot, atz, target, base_date=base,
                abgaenge_params=run_dep, zugaenge_params=run_hire,
                active_cluster_source=source, cluster_mapping_bundle=bundle,
                cluster_source_signature=signature, include_audits=False)
            if training_mode:
                final_reference = compact.prepare_compact_data(reference.future_snapshot_df)
                target_totals = page._metric_totals(final_reference)
                training_rows = final_reference[final_reference["PersNr"].astype(str).str.match(
                    r"^(AZ|TR)_", na=False)]
                event_types = set(reference.zugaenge_result["events"]["type"])
                assert {"Azubi_Hire", "Trainee_Hire"}.issubset(event_types), (
                    label, seed, "training_types")
                status = _resolve_atz_status_map(
                    reference.abgaenge_result["tables"]["atz_pivot"], target)
                atz_fr_people = set(status.loc[
                    status["ist_atz_fr"] == True, "PersNr"].astype(str))
                final_people = reference.future_employee_df
                exclusions = simulation_engine.get_setting("exclusions", {})
                excluded_new = excluded_forecast_people(
                    prepare_target_people(final_people),
                    target_array_model["existing_ids"], exclusions)
                new_ids = set(final_people.loc[
                    final_people["PersNr"].astype(str).str.match(r"^(AZ|TR|NH)_", na=False)
                    & final_people["active"].eq(True), "PersNr"].astype(str))
                final_ids = set(reference.future_snapshot_df["PersNr"].dropna().astype(str))
                assert excluded_new == new_ids - final_ids, (
                    label, seed, "training_exclusions", excluded_new,
                    new_ids - final_ids)
                expected = {"heads": target_totals["heads"], "mak": target_totals["mak"]}
                dataframe_target = target_head_mak_with_hires(
                    snapshot, final_people, atz_fr_people,
                    excluded_new_people=excluded_new)
                assert dataframe_target == expected, (
                    label, seed, "dataframe_target", dataframe_target, expected)
                assert target_head_mak_from_array_model(
                    target_array_model, prepare_target_people(final_people),
                    atz_fr_people, excluded_new_people=excluded_new
                ) == expected, (label, seed, "array_target")
                print(f"target_training_parity={label}; seed={seed}; excluded={len(excluded_new)}; training_rows={len(training_rows)}; heads={expected['heads']}; mak={expected['mak']}")
                continue
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

            replay_start = perf_counter()
            final_people = replay_final_employee_state(people, departures, new_people)
            final_people["PersNr"] = _normalize_persnr_series(final_people["PersNr"])
            final_people = _apply_salary_automation_to_employee_state(
                final_people, target_date=target, events_df=hires)
            replay_seconds = perf_counter() - replay_start
            pd.testing.assert_frame_equal(final_people, reference.future_employee_df,
                                          check_exact=True)
            atz_target = _resolve_atz_status_map(pivot, target)
            future = _update_existing_rows(
                snapshot, final_people, target, atz_target)
            future = _append_new_people_rows(
                future, snapshot, final_people, {"events": hires})
            updated_positions = future
            future = _finalize_future_snapshot(
                future, target, active_cluster_source=source,
                cluster_mapping_bundle=bundle)
            pd.testing.assert_frame_equal(future, reference.future_snapshot_df,
                                          check_exact=True)

            final_reference = compact.prepare_compact_data(reference.future_snapshot_df)
            final_candidate = compact.prepare_compact_data(future)
            pd.testing.assert_frame_equal(final_candidate, final_reference,
                                          check_exact=True)
            if target_only:
                target_totals = page._metric_totals(final_reference)
                final_target_people = prepare_target_people(final_people)
                run_exclusions = simulation_engine.get_setting("exclusions", {})
                excluded_new = excluded_forecast_people(
                    final_target_people, target_array_model["existing_ids"],
                    run_exclusions)
                active_new = {person_id for person_id, person in final_target_people.items()
                              if person_id not in target_array_model["existing_ids"]
                              and person.get("active") == True
                              and (person.get("is_forecast") is True or
                                   person_id.startswith(("AZ_", "TR_", "NH_")))}
                final_ids = set(reference.future_snapshot_df["PersNr"].dropna().astype(str))
                assert excluded_new == active_new - final_ids, (
                    label, seed, "forecast_exclusions", excluded_new,
                    active_new - final_ids)
                if annual_hires and variant != "training_hires":
                    forecast_rows = final_reference[
                        final_reference["PersNr"].astype(str).str.match(
                            r"^(AZ|TR|NH)_", na=False)]
                    assert len(forecast_rows) == annual_hires * 3, (
                        label, seed, "forecast_heads", len(forecast_rows))
                    assert forecast_rows["MAK_Reporting"].eq(0.0).all(), (
                        label, seed, "forecast_reporting_mak")
                target_start = perf_counter()
                atz_fr_people = set(atz_target.loc[
                    atz_target["ist_atz_fr"] == True, "PersNr"].astype(str))
                candidate_totals = target_head_mak_with_hires(
                    snapshot, final_people, atz_fr_people,
                    excluded_new_people=excluded_new)
                target_seconds = perf_counter() - target_start
                compact_start = perf_counter()
                compact_people = replay_target_people_compact(
                    prepared_target_people, departures, new_people)
                compact_totals = target_head_mak_with_hires(
                    snapshot, compact_people, atz_fr_people,
                    excluded_new_people=excluded_new)
                compact_seconds = perf_counter() - compact_start
                assert compact_totals == {"heads": target_totals["heads"],
                                          "mak": target_totals["mak"]}, (
                    label, seed, "compact_replay", compact_totals,
                    target_totals)
                array_start = perf_counter()
                array_people = replay_target_people_compact(
                    prepared_target_people, departures, new_people, as_dict=True)
                assert excluded_forecast_people(
                    array_people, target_array_model["existing_ids"],
                    run_exclusions) == excluded_new, (
                    label, seed, "compact_forecast_exclusions")
                array_totals = target_head_mak_from_array_model(
                    target_array_model, array_people, atz_fr_people,
                    excluded_new_people=excluded_new)
                array_seconds = perf_counter() - array_start
                assert array_totals == {"heads": target_totals["heads"],
                                        "mak": target_totals["mak"]}, (
                    label, seed, "array_target", array_totals, target_totals)
                bridge_start = perf_counter()
                prepared_bridge = target_head_mak_reporting_bridge(
                    prepared_positions, final_people, target, atz_target,
                    hires, FROZEN_SETTINGS["exclusions"])
                bridge_seconds = perf_counter() - bridge_start
                assert prepared_bridge == {"heads": target_totals["heads"],
                                           "mak": target_totals["mak"]}, (
                    label, seed, "prepared_reporting_bridge", prepared_bridge,
                    target_totals)
                assert candidate_totals["heads"] == target_totals["heads"], (
                    label, seed, "heads", candidate_totals, target_totals)
                if exclusions_mode:
                    bridge = reporting_target_from_position_probe(
                        updated_positions, target, FROZEN_SETTINGS["exclusions"])
                    assert bridge == {"heads": target_totals["heads"],
                                      "mak": target_totals["mak"]}, (
                        label, seed, "reporting_bridge", bridge, target_totals)
                    print(f"target_exclusion_parity={label}; seed={seed}; replay_s={replay_seconds:.4f}; target_s={target_seconds:.4f}; compact_s={compact_seconds:.4f}; array_s={array_seconds:.4f}; bridge_s={bridge_seconds:.4f}")
                assert candidate_totals["mak"] == target_totals["mak"], (
                    label, seed, "mak", candidate_totals, target_totals)
                if not exclusions_mode:
                    print(f"target_parity={label}; seed={seed}; heads={candidate_totals['heads']}; mak={candidate_totals['mak']}; replay_s={replay_seconds:.4f}; target_s={target_seconds:.4f}; compact_s={compact_seconds:.4f}; array_s={array_seconds:.4f}; bridge_s={bridge_seconds:.4f}")
                continue
            context = page._build_simulation_event_context(
                reference, bundle, f"yearly::{label}::{seed}",
                baseline_snapshot=baseline, final_snapshot=final_reference,
                zugaenge_params=hire_params, abgaenge_params=dep_params,
                df_atz=atz, run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
                source_snapshot=snapshot)
            if stock_scopes is None:
                projection = reporting_projection_base(context, baseline)
                projection["Organisationseinheit"] = projection["Organisationseinheit"].fillna("Nicht zugeordnet").astype(str).str.strip().replace("", "Nicht zugeordnet")
                projection["OE-Cluster"] = projection["Organisationseinheit"].map(bundle.oe_map).fillna("Nicht zugeordnet")
                stock_scopes = prepare_oe_stock_scopes(projection)
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
            if not matrix_mode:
                assert event_years == {2025, 2026, 2027, 2028}, (label, seed, event_years)
            departure_types = set(candidate_summary["events"]["Ereignisart"])
            if not matrix_mode:
                assert {"ATZ_AR_TO_FR", "ATZ_END"}.issubset(departure_types), (
                    label, seed, departure_types)
            ref_bridge = reference_summary["stock_reconciliation"]
            probe_bridge = candidate_summary["stock_reconciliation"]
            assert (probe_bridge["status"], probe_bridge["reason"]) == (
                ref_bridge["status"], ref_bridge["reason"])
            _equal_frames(ref_bridge, probe_bridge,
                          ("annual", "checks", "person_diagnostics"))
            annual_tables = {"Organisationseinheit": reference_summary["annual_oe"],
                             "OE-Cluster": reference_summary["annual_cluster"]}
            jf_views = {}
            for dimension in ("Jobfamily", "JF-Cluster"):
                ref_jf = build_jobfamily_yearly_view(
                    context, reference_summary, dimension=dimension)
                probe_jf = build_jobfamily_yearly_view(
                    candidate, candidate_summary, dimension=dimension)
                assert probe_jf["stock_reason"] == ref_jf["stock_reason"]
                _equal_frames(ref_jf, probe_jf,
                              ("annual", "stock", "stock_checks"))
                annual_tables[dimension] = ref_jf["annual"]
                jf_views[dimension] = ref_jf
            if not annual_hires:
                assert ref_bridge["basis_effects"].empty, (label, seed, "basis_effects")
                assert ref_bridge["documented_effects"].empty, (label, seed, "documented_effects")
                reported = ref_bridge["reporting_events"]
                assert reported["MAK-Veränderung"].equals(
                    reported["Original MAK-Veränderung"]), (label, seed, "reporting_mak")
                reference_packet = make_metric_packet(
                    target_totals={"heads": 0, "mak": 0.0, "eur": 0.0},
                    annual_tables=annual_tables,
                    stock_status={level: "Nicht berechnet" for level in LEVELS})
                assert departure_annual_values(departures, departure_yearly) == (
                    reference_packet["annual_values"]), (label, seed)
                provisional = provisional_stock_values(
                    stock_openings, departures, departure_yearly, range(2025, 2029),
                    stock_scopes)
                target_totals = page._metric_totals(final_reference)
                compact_target = provisional_target_head_mak(
                    provisional, target.year, page._metric_totals(baseline)["heads"],
                    departures)
                assert compact_target["heads"] == target_totals["heads"], (
                    label, seed, "target_heads", compact_target, target_totals)
                assert abs(compact_target["mak"] - target_totals["mak"]) <= 1e-12, (
                    label, seed, "target_mak", compact_target, target_totals)
                row_order_mak = target_mak_in_position_order(snapshot, final_people)
                assert row_order_mak == target_totals["mak"], (
                    label, seed, "target_mak_position_order", row_order_mak,
                    target_totals["mak"])
                for _, row in ref_bridge["annual"].iterrows():
                    if row["Art"] != "Jahresstichtag" or row["Jahr"] < 2026:
                        continue
                    level = "Organisationseinheit" if row["Ebene"] == "Organisationseinheit" else "OE-Cluster" if row["Ebene"] == "OE-Cluster" else None
                    if level is None:
                        continue
                    assert provisional[(int(row["Jahr"]), level, row["Organisation"], "Köpfe")] == float(row["Bestand-Köpfe"]), (label, seed, level, row["Organisation"], row["Jahr"], provisional[(int(row["Jahr"]), level, row["Organisation"], "Köpfe")], float(row["Bestand-Köpfe"]))
                    assert provisional[(int(row["Jahr"]), level, row["Organisation"], "MAK")] == float(row["Bestand-MAK"]), (label, seed, level, row["Organisation"], row["Jahr"], provisional[(int(row["Jahr"]), level, row["Organisation"], "MAK")], float(row["Bestand-MAK"]))
                for level, view in jf_views.items():
                    for _, row in view["stock"].iterrows():
                        if row["Art"] == "Jahresstichtag" and row["Stichtag"].year >= 2026:
                            assert provisional[(row["Stichtag"].year, level, row[level], row["Kennzahl"])] == float(row["Bestand"]), (label, seed, level, row["Stichtag"])
            if annual_hires:
                assert probe_bridge["status"] == "Nicht verfügbar"
                assert probe_bridge["annual"].empty
            else:
                assert not probe_bridge["annual"].empty
            print(f"yearly_parity={label}; seed={seed}; years=2025-2028; "
                  f"oe_status={probe_bridge['status']}; atz_rows={len(pivot)}")


if __name__ == "__main__":
    main()
