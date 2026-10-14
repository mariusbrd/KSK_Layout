from copy import deepcopy
from unittest.mock import patch

import pandas as pd
import pytest

from test_compact_simulation_engine import _base_snapshot
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.cluster_resolver import ActiveClusterSource, ClusterMappingBundle
from utils.org_simulation_events import build_simulation_event_summary
from utils.status_quo_baseline import build_status_quo_snapshot
from test_org_simulation_reconciliation import _bridge_context


def _actual_context(partial=False):
    rows = pd.concat([_base_snapshot(), _base_snapshot()], ignore_index=True)
    rows["Organisationseinheit"] = ["OE1", "OE2"]
    rows["Soll_FTE"] = [0.4, 0.6]
    rows["Personen_MAK_Source"] = 1.0
    exclusions = {}
    if partial:
        rows["BsGrd"] = 30.77
        rows["Personen_MAK_Source"] = 0.3077
        rows["MAK_Calculated"] = 0.3077
        rows["Soll_FTE"] = [0.0, 0.21]
        rows["Sollarbeitszeit"] = [0.01, 8.19]
        exclusions = {"special_groups": ["sollarbeitszeit_001_positions"]}
    initial = build_status_quo_snapshot(rows, pd.Timestamp("2025-12-31"))
    params = {key: {"active": False} for key in ("azubi", "trainee", "new_hires")}
    bundle = ClusterMappingBundle(oe_map={"OE1": "A", "OE2": "B"}, source_signature="report")
    with patch("utils.settings_loader.load_user_settings", return_value={"exclusions": exclusions}):
        result = simulate_compact_snapshot(initial, pd.DataFrame(), pd.Timestamp("2027-12-31"),
            base_date=pd.Timestamp("2025-12-31"), abgaenge_params={"components": dict.fromkeys(("atz", "quit", "retirement", "ruhend"), False)},
            zugaenge_params=params, active_cluster_source=ActiveClusterSource(mode="synthetic", subtype="synthetic.default_fallback", source_signature="report"),
            cluster_mapping_bundle=bundle)
    return {"run_signature": "a", "metadata": result.metadata, "baseline_snapshot": initial,
        "final_snapshot": result.future_snapshot_df, "departure_events": result.abgaenge_result["events_person_level"],
        "zugaenge_params": params, "cluster_mapping": bundle.to_dict(), "run_settings": {"exclusions": exclusions}}


@pytest.mark.parametrize("partial", [False, True])
def test_existing_projection_rules_reconcile_without_business_events(partial):
    context = _actual_context(partial)
    before = deepcopy(context)
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["checks"].Abgestimmt.all()
    assert not bridge["basis_effects"].empty
    assert bridge["reporting_events"].empty
    if partial:
        total = bridge["basis_effects"].loc[bridge["basis_effects"].Ebene.eq("Gesamt"), "MAK-Effekt"].sum()
        assert total == pytest.approx(-0.3077)
        first_year = bridge["annual"].loc[bridge["annual"].Ebene.eq("Gesamt") & bridge["annual"].Jahr.eq(2025)]
        assert first_year["Technischer Effekt-MAK"].sum() == 0
        next_year = bridge["annual"].loc[bridge["annual"].Ebene.eq("Gesamt") & bridge["annual"].Jahr.eq(2026)]
        assert next_year["Technischer Effekt-MAK"].sum() == pytest.approx(-0.3077)
    for key in ("baseline_snapshot", "final_snapshot", "departure_events"):
        pd.testing.assert_frame_equal(context[key], before[key], check_exact=True)


def test_already_freistellung_atz_does_not_subtract_fictitious_capacity():
    context = _bridge_context("no_events")
    initial = _base_snapshot()
    initial["Personen_MAK_Source"] = 1.0
    initial["ATZ_Status"] = "Freistellungsphase"
    initial["ist_atz_fr"] = True
    initial["MAK_Reporting"] = 0.0
    final = initial.copy()
    final["Is_Vacant"] = True
    final["PersNr"] = pd.NA
    context.update(baseline_snapshot=initial, final_snapshot=final, run_settings={"exclusions": {}},
        departure_events=pd.DataFrame([
            {"persnr": "000001", "Organisationseinheit": "OE1", "event_date": pd.Timestamp("2026-06-01"), "reason_code": "ATZ_AR_TO_FR", "headcount_change": 0, "mak_change": -1.0},
            {"persnr": "000001", "Organisationseinheit": "OE1", "event_date": pd.Timestamp("2027-06-01"), "reason_code": "ATZ_END", "headcount_change": -1, "mak_change": 0.0},
        ]))
    before = context["departure_events"].copy(deep=True)
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["reporting_events"]["MAK-Veränderung"].sum() == 0
    assert bridge["reporting_events"]["Original MAK-Veränderung"].sum() == -1
    pd.testing.assert_frame_equal(context["departure_events"], before, check_exact=True)


def test_export_preserves_nanosecond_period_end_without_moving_to_next_year():
    from io import BytesIO
    from utils.org_simulation_export import build_org_simulation_export_bytes
    timestamp = pd.Timestamp("2036-12-31 23:59:59.999999999")
    book = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(
        {"Test": pd.DataFrame({"event_date": [timestamp], "Jahr": [2036]})}, {})))
    inventory = pd.read_excel(book, "Exportinventar")
    values = pd.read_excel(book, inventory.iloc[0]["Tabellenblatt"])
    assert pd.Timestamp(values.iloc[0].event_date) == timestamp
    assert pd.Timestamp(values.iloc[0].event_date).year == 2036


def test_dormant_return_uses_saved_reporting_source_capacity():
    context = _bridge_context("no_events")
    initial = _base_snapshot()
    initial["Personen_MAK_Source"] = 1.0
    initial["Status kundenindividuell"] = "Ruhendes Beschäftigungsverhältnis"
    initial["MAK_Reporting"] = 0.0
    final = initial.copy()
    final["Status kundenindividuell"] = "Aktives Beschäftigungsverhältnis"
    final["MAK_Reporting"] = 1.0
    context.update(baseline_snapshot=initial, final_snapshot=final, run_settings={"exclusions": {}},
        departure_events=pd.DataFrame([{"persnr": "000001", "Organisationseinheit": "OE1",
            "event_date": pd.Timestamp("2026-06-01"), "reason_code": "RUHEND_RETURN",
            "headcount_change": 0, "mak_change": 0.0}]))
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["reporting_events"]["MAK-Veränderung"].sum() == 1
    assert bridge["reporting_events"]["Original MAK-Veränderung"].sum() == 0
