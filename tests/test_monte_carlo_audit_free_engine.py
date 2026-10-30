"""Audit-free runs preserve all state and event outputs of full runs."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from test_monte_carlo_yearly_reference import _frame_signature
from test_org_simulation_reconciliation import _bridge_context
from test_compact_sim_cluster_source_consistency import _load_page7_module
from test_org_yearly_reference import _run
from utils.monte_carlo_event_packet import build_event_only_packet


@pytest.mark.parametrize("scenario,seed", (("identity", 42), ("departures", 7),
                                           ("departures", 123), ("combined", 7),
                                           ("combined", 123)))
def test_audit_free_run_matches_full_state_and_events(scenario: str, seed: int) -> None:
    full = _run(scenario, seed=seed)
    with patch("dataloader.compact_simulation_engine._build_simulation_audit_tables",
               side_effect=AssertionError("Display audit called")):
        compact = _run(scenario, seed=seed, include_audits=False)
    assert full.audit_tables
    assert compact.audit_tables == {}
    assert compact.metadata == full.metadata
    for field in ("future_snapshot_df", "future_employee_df"):
        assert _frame_signature(getattr(compact, field)) == _frame_signature(getattr(full, field)), field
    for field, keys in (("abgaenge_result", ("events_person_level", "forecast_kpis")),
                        ("zugaenge_result", ("events", "final_state", "forecast_kpis"))):
        left, right = getattr(compact, field), getattr(full, field)
        for key in keys:
            a, b = left.get(key, pd.DataFrame()), right.get(key, pd.DataFrame())
            assert _frame_signature(a) == _frame_signature(b), (field, key)
    context = _bridge_context(scenario, seed=seed)
    page = _load_page7_module()
    packets = []
    for result in (full, compact):
        current = {**context, "metadata": result.metadata,
                   "final_snapshot": result.future_snapshot_df,
                   "departure_events": result.abgaenge_result.get("events_person_level", pd.DataFrame()),
                   "accession_events": result.zugaenge_result.get("events", pd.DataFrame())}
        # The synthetic identity fixture has no EUR column; stock equality was
        # checked above, so this comparison isolates the yearly event packet.
        totals = (page._metric_totals(result.future_snapshot_df) if scenario != "identity"
                  else {"heads": 0, "mak": 0.0, "eur": 0.0})
        packets.append(build_event_only_packet(current, target_totals=totals))
    assert packets[0] == packets[1]
