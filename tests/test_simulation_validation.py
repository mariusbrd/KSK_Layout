"""The validation gate must preserve the unmodified saved simulation."""
import io

import pandas as pd

from utils.simulation_validation import (
    approve_adjustment, approve_original, clear_adjustment, current_view,
    invalidate_approval, register_new_run, stage_adjustment, value_fingerprint,
)


def _state():
    frame = pd.DataFrame({"PersNr": ["1"], "MAK_Reporting": [1.0]})
    return {
        "compact_sim_signature": "seed-42-run",
        "compact_sim_prepared_df": frame,
        "compact_sim_status_quo_df": frame.copy(),
        "compact_sim_metadata": {"target_date": "2031-12-31"},
        "compact_sim_event_context": {"run_signature": "seed-42-run", "baseline_snapshot": frame,
                                      "final_snapshot": frame},
        "compact_sim_event_summary": {"run_signature": "seed-42-run", "events": pd.DataFrame(),
                                      "stock_reconciliation": {"status": "Nicht verfügbar"}},
    }


def test_unadjusted_approval_returns_original_objects_without_recalculation():
    state = _state()
    register_new_run(state)
    assert current_view(state, require_approval=True) is None
    original = current_view(state)
    approved = approve_original(state, approved_at="2026-10-27T00:00:00+00:00")
    assert approved.approved
    assert approved.prepared_df is original.prepared_df
    assert approved.event_context is original.event_context
    assert approved.event_summary is original.event_summary


def test_same_signature_new_run_requires_new_approval():
    state = _state()
    register_new_run(state)
    approve_original(state, approved_at="2026-10-27T00:00:00+00:00")
    register_new_run(state)
    assert current_view(state, require_approval=True) is None
    assert current_view(state).instance == 2
    invalidate_approval(state)
    assert current_view(state, require_approval=True) is None


def test_mismatched_artifacts_cannot_be_approved():
    state = _state()
    state["compact_sim_event_summary"]["run_signature"] = "other"
    assert current_view(state) is None


def test_simple_retirement_adjustment_keeps_other_employee_and_reconciles():
    from test_orgunit_simulation_page import (
        BASE_DATE, TARGET_DATE, _base_snapshot, _abgaenge_retire_65plus_params,
        _inactive_zugaenge_params,
    )
    from dataloader.compact_simulation_engine import simulate_compact_snapshot
    from dataloader.cluster_resolver import get_active_cluster_source
    from dataloader.cluster_manager import load_cluster_mappings_from_source
    from utils.compact_page_loader import load_compact_page_module
    from utils.org_simulation_events import build_simulation_event_summary
    from utils.simulation_adjustments import derive_retained_departures, eligible_departures
    from utils.settings_loader import get_setting

    source = _base_snapshot()
    result = simulate_compact_snapshot(
        source, pd.DataFrame(), TARGET_DATE, base_date=BASE_DATE,
        abgaenge_params=_abgaenge_retire_65plus_params(),
        zugaenge_params=_inactive_zugaenge_params(),
    )
    compact = load_compact_page_module()
    prepared = compact.prepare_compact_data(result.future_snapshot_df)
    cluster_source = get_active_cluster_source()
    bundle = load_cluster_mappings_from_source(cluster_source)
    context = {
        "run_signature": "simple-retirement",
        "metadata": result.metadata,
        "source_snapshot": source,
        "raw_final_snapshot": result.future_snapshot_df,
        "future_employee_state": result.future_employee_df,
        "baseline_snapshot": compact.prepare_compact_data(source),
        "final_snapshot": prepared,
        "departure_events": result.abgaenge_result["events_person_level"],
        "accession_events": result.zugaenge_result["events"],
        "df_atz": pd.DataFrame(),
        "zugaenge_params": _inactive_zugaenge_params(),
        "abgaenge_params": _abgaenge_retire_65plus_params(),
        "cluster_mapping": bundle.to_dict(),
        "run_settings": {"exclusions": get_setting("exclusions", {})},
        "cost_lookup_fingerprint": value_fingerprint({}),
    }
    summary = build_simulation_event_summary(context, expected_run_signature="simple-retirement")
    state = {
        "compact_sim_signature": "simple-retirement",
        "compact_sim_prepared_df": prepared,
        "compact_sim_status_quo_df": context["baseline_snapshot"],
        "compact_sim_metadata": result.metadata,
        "compact_sim_event_context": context,
        "compact_sim_event_summary": summary,
    }
    register_new_run(state)
    original = current_view(state)
    candidates = eligible_departures(original)
    assert candidates["PersNr"].tolist() == ["000001"]
    adjusted = derive_retained_departures(original, ["000001"])
    assert adjusted.event_summary["stock_reconciliation"]["status"] == "Abgestimmt"
    assert set(adjusted.prepared_df.loc[~adjusted.prepared_df["Is_Vacant"], "PersNr"]) == {"000001", "000002"}
    assert adjusted.event_context["departure_events"].empty
    assert adjusted.audit_tables["Abgaenge_Events_Raw"].empty
    assert adjusted.prepared_df.loc[1, "MAK_Reporting"] == prepared.loc[1, "MAK_Reporting"]
    from utils.compact_simulation_export import build_compact_simulation_export_bytes
    payload = build_compact_simulation_export_bytes(
        prepared_df=adjusted.prepared_df,
        abgaenge_params=context["abgaenge_params"],
        zugaenge_params=context["zugaenge_params"],
        metadata=adjusted.metadata,
        audit_tables=adjusted.audit_tables,
    )
    exported = pd.read_excel(io.BytesIO(payload), sheet_name="Jobfamily_Summary")
    assert exported["Köpfe"].sum() == 2
    stage_adjustment(state, adjusted)
    assert current_view(state, require_approval=True) is None
    approved = approve_adjustment(state, approved_at="2026-10-27T00:00:00+00:00")
    assert approved.version == adjusted.version
    assert approved.prepared_df is adjusted.prepared_df
    assert current_view(state, require_approval=True).event_summary is adjusted.event_summary
    original_again = approve_original(state, approved_at="2026-10-27T00:01:00+00:00")
    assert original_again.prepared_df is prepared
    assert "simulation_validation_draft" not in state
    assert [entry["kind"] for entry in state["simulation_validation_history"]] == ["adjusted", "original"]
    stage_adjustment(state, adjusted)
    clear_adjustment(state)
    assert current_view(state, require_approval=True) is None
