"""A saved multi-run must re-open without executing the forecast again."""
from __future__ import annotations

import pandas as pd
import pytest
from concurrent.futures import Future

from test_monte_carlo_parameter_ui import _compact_app
from utils.monte_carlo_app_run import current_ensemble
from utils.monte_carlo_org_view import approved_org_ensemble
from utils.monte_carlo_representative_runs import select_representative_runs
from utils.simulation_run_archive import RUN_KEYS, build_run_archive, restore_run_archive
from utils.simulation_validation import approve_original, current_view


def test_ten_run_archive_restores_complete_session_without_recomputation():
    app = _compact_app(10, "2026-12-31")
    app.session_state["test_start_simulation"] = True
    app.run()
    assert not app.exception
    state = {key: app.session_state[key] for key in RUN_KEYS if key in app.session_state}
    approve_original(state, approved_at="2026-11-17T12:00:00")
    original = app.session_state["monte_carlo_ensemble_result"]
    original_stock = app.session_state["compact_sim_prepared_df"].copy(deep=True)
    original_events = app.session_state["compact_sim_event_summary"]["events"].copy(deep=True)
    raw = build_run_archive(state)

    # A different forecast replaces the visible run in the same session.
    second = _compact_app(1, "2027-12-31", without_quits=True)
    assert not second.exception
    newer = {key: second.session_state[key] for key in RUN_KEYS if key in second.session_state}
    assert newer["compact_sim_signature"] != state["compact_sim_signature"]
    stale_future = Future()
    newer["org_sim_representative_prefetch"] = {("old",): stale_future}
    newer["jf_yearly_view_cache"] = {("old",): object()}
    newer["jf_yearly_prepared_export"] = ("old", object())
    newer["simulation_approval_candidates_cache"] = {"key": "old"}
    restore_run_archive(newer, raw)
    assert stale_future.cancelled()
    assert not any(key in newer for key in (
        "org_sim_representative_prefetch", "jf_yearly_view_cache",
        "jf_yearly_prepared_export", "simulation_approval_candidates_cache"))
    assert newer["compact_sim_signature"] == state["compact_sim_signature"]
    pd.testing.assert_frame_equal(newer["compact_sim_prepared_df"], original_stock, check_exact=True)
    pd.testing.assert_frame_equal(newer["compact_sim_event_summary"]["events"], original_events,
                                  check_exact=True)
    assert current_ensemble(newer, newer["monte_carlo_ensemble_request"])["statistics"] == original["statistics"]

    fresh = {}
    restore_run_archive(fresh, raw)
    assert fresh["compact_sim_imported_archive"] is True
    assert pd.Timestamp(fresh["compact_sim_target_date_cached"]) == pd.Timestamp("2026-12-31")
    assert fresh["simulation_params"]["compact_plus"]["run_count"] == 10
    assert fresh["simulation_params"]["compact_plus"]["target_date"] == pd.Timestamp("2026-12-31")
    pd.testing.assert_frame_equal(fresh["compact_sim_prepared_df"], original_stock)
    pd.testing.assert_frame_equal(fresh["compact_sim_event_summary"]["events"], original_events)
    view = current_view(fresh, require_approval=True)
    assert view is not None
    ensemble = current_ensemble(fresh, fresh["monte_carlo_ensemble_request"])
    assert ensemble is not None
    assert approved_org_ensemble(fresh, view) is not None
    assert ensemble["statistics"]["target"] == original["statistics"]["target"]
    assert ensemble["seed_pairs"] == original["seed_pairs"]
    selected_indices = {item["index"] for item in select_representative_runs(ensemble).values()} - {0}
    cached_indices = {key[-1] for key in fresh["org_sim_representative_details"]}
    assert selected_indices <= cached_indices

    # A second page run must keep the exact objects and validation instance.
    for key, value in fresh.items():
        app.session_state[key] = value
    app.session_state["test_start_simulation"] = False
    instance = app.session_state["simulation_validation_instance"]
    app.run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == instance
    assert app.session_state["monte_carlo_ensemble_result"]["plan_signature"] == original["plan_signature"]
    assert current_view(dict(app.session_state.filtered_state), require_approval=True) is not None


def test_archive_rejects_tampering_before_session_mutation():
    state = {"sentinel": 1}
    with pytest.raises(ValueError):
        restore_run_archive(state, b"not a zip")
    assert state == {"sentinel": 1}
