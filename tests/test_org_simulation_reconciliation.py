from copy import deepcopy
import json

import pandas as pd
import pytest

from utils.org_simulation_reconciliation import complete_departure_org_assignments
from utils.org_simulation_events import normalize_simulation_events
from utils.org_simulation_events import build_simulation_event_summary
from utils.org_simulation_reconciliation import build_departure_stock_bridge
from test_org_yearly_reference import _run, _payload, REFERENCE
from test_compact_simulation_engine import _base_snapshot
from test_compact_sim_cluster_source_consistency import _load_page7_module
from dataloader.cluster_resolver import ClusterMappingBundle


def test_person_budget_aggregation_matches_previous_per_person_calculation():
    from utils.org_simulation_reconciliation import _occupied, _person_mak_budgets, raw_stock_mak

    positions = pd.DataFrame({
        "PersNr": ["000001", "000001", "1", "000002", "000003", None],
        "Is_Vacant": [False, False, False, False, True, False],
        "MAK_Reporting": [0.25, 0.35, 0.2, 0.8, 0.9, 0.7],
    })
    occupied = _occupied(positions)
    expected = {pid: raw_stock_mak(group) for pid, group in occupied.groupby("_person")}
    assert _person_mak_budgets(occupied) == expected


def test_missing_atz_org_is_filled_from_same_run_without_changing_raw_result():
    result = _run("atz_ruhend")
    before = _payload(result)
    baseline = _base_snapshot().copy()
    baseline["PersNr"] = "000002"
    context = {"run_signature": "a", "baseline_snapshot": baseline,
               "cluster_mapping": {"oe_map": {"OE1": "A"}},
               "departure_events": result.abgaenge_result["events_person_level"]}
    normalized = normalize_simulation_events(context)
    atz = normalized.loc[normalized.Ereignisart == "ATZ_END"]
    assert atz.Organisationseinheit.tolist() == ["OE1"]
    assert atz["OE-Cluster"].tolist() == ["A"]
    assert atz["OE-Zuordnungsherkunft"].tolist() == ["Gespeicherter Ausgangsbestand"]
    assert _payload(result) == before


def test_multiple_rows_in_one_org_are_unambiguous_but_different_orgs_are_not():
    events = pd.DataFrame({"persnr": ["1", "2", "3"], "Organisationseinheit": [None, None, "Original"]})
    baseline = pd.DataFrame({"PersNr": ["000001", "000001", "000002", "000002", "000003"],
                             "Organisationseinheit": ["A", "A", "B", "C", "D"]})
    before = deepcopy(events)
    result = complete_departure_org_assignments(events, baseline)
    assert result.loc[0, "Organisationseinheit"] == "A"
    assert pd.isna(result.loc[1, "Organisationseinheit"])
    assert result.loc[1, "OE-Zuordnungsherkunft"] == "Mehrdeutige Ausgangszuordnung"
    assert result.loc[2, "Organisationseinheit"] == "Original"
    pd.testing.assert_frame_equal(events, before, check_exact=True)


def test_unknown_person_and_vacant_position_are_not_used():
    events = pd.DataFrame({"persnr": ["1", "2"], "Organisationseinheit": [None, None]})
    baseline = pd.DataFrame({"PersNr": ["000001"], "Organisationseinheit": ["A"], "Is_Vacant": [True]})
    result = complete_departure_org_assignments(events, baseline)
    assert result.Organisationseinheit.isna().all()
    assert result["OE-Zuordnungsherkunft"].eq("Nicht auflösbar").all()


def test_saved_stock_bases_and_parameters_are_copies_from_the_same_run():
    page = _load_page7_module()
    result = _run("no_events")
    baseline = _base_snapshot()
    final = result.future_snapshot_df
    params = {"new_hires": {"active": False}}
    context = page._build_simulation_event_context(result, ClusterMappingBundle(), "a",
        baseline_snapshot=baseline, final_snapshot=final, zugaenge_params=params)
    baseline.loc[0, "Organisationseinheit"] = "Later edit"
    params["new_hires"]["active"] = True
    assert context["baseline_snapshot"].loc[0, "Organisationseinheit"] == "OE1"
    assert context["zugaenge_params"]["new_hires"]["active"] is False
    pd.testing.assert_frame_equal(context["final_snapshot"], final, check_exact=True)


def test_atz_reference_now_reconciles_per_org_without_balancing_adjustments():
    from dataloader.kpi_engine import compute_headcount, compute_fte_effektiv
    from utils.status_quo_baseline import build_status_quo_snapshot
    stored = json.loads(REFERENCE.read_text(encoding="utf-8"))["scenarios"]["identity"]["future_snapshot"]["data"]
    baseline = build_status_quo_snapshot(pd.DataFrame(stored["data"], columns=stored["columns"]), "2025-12-31")
    result = _run("atz_ruhend")
    events = normalize_simulation_events({"run_signature": "a", "baseline_snapshot": baseline,
        "cluster_mapping": {"oe_map": {"OE1": "A", "OE2": "B"}},
        "departure_events": result.abgaenge_result["events_person_level"]})
    for org in ("OE1", "OE2"):
        initial = baseline[baseline.Organisationseinheit == org]
        final = result.future_snapshot_df[result.future_snapshot_df.Organisationseinheit == org]
        changes = events[events.Organisationseinheit == org]
        assert compute_headcount(initial) + changes["Köpfe-Veränderung"].sum() == compute_headcount(final)
        assert abs(compute_fte_effektiv(initial) + changes["MAK-Veränderung"].sum() - compute_fte_effektiv(final)) < 1e-9


def _bridge_context(scenario="atz_ruhend", *, seed=42):
    stored = json.loads(REFERENCE.read_text(encoding="utf-8"))["scenarios"]["identity"]["future_snapshot"]["data"]
    from utils.status_quo_baseline import build_status_quo_snapshot
    baseline = build_status_quo_snapshot(pd.DataFrame(stored["data"], columns=stored["columns"]), "2025-12-31")
    if scenario == "identity":
        from dataloader.compact_simulation_engine import simulate_compact_snapshot
        from dataloader.cluster_resolver import ActiveClusterSource
        result = simulate_compact_snapshot(
            baseline, pd.DataFrame(), pd.Timestamp("2025-12-31"), base_date=pd.Timestamp("2025-12-31"),
            active_cluster_source=ActiveClusterSource(mode="synthetic", subtype="synthetic.default_fallback", source_signature="identity"),
            cluster_mapping_bundle=ClusterMappingBundle(oe_map={"OE1": "A", "OE2": "B"}, source_signature="identity"))
    else:
        result = _run(scenario, seed=seed)
    return {"run_signature": "a", "metadata": result.metadata,
            "baseline_snapshot": baseline, "final_snapshot": result.future_snapshot_df,
            "zugaenge_params": {k: {"active": False} for k in ("azubi", "trainee", "new_hires")},
            "cluster_mapping": {"oe_map": {"OE1": "A", "OE2": "B"}},
            "departure_events": result.abgaenge_result.get("events_person_level", pd.DataFrame()),
            "accession_events": result.zugaenge_result.get("events", pd.DataFrame())}


@pytest.mark.parametrize("scenario", ["identity", "no_events", "departures", "atz_ruhend"])
def test_bridge_reconciles_company_oe_cluster_and_preserves_stock(scenario):
    context = _bridge_context(scenario)
    before = deepcopy(context)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    bridge = summary["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["checks"].Abgestimmt.all()
    assert set(bridge["checks"].Ebene) == {"Gesamt", "Organisationseinheit", "OE-Cluster"}
    for _, rows in bridge["annual"].groupby(["Ebene", "Organisation"]):
        assert rows.iloc[0]["Bestand-Köpfe"] == rows.iloc[0]["Ausgangs-Köpfe"]
        assert rows.iloc[0]["Bestand-MAK"] == rows.iloc[0]["Ausgangs-MAK"]
        assert rows.iloc[-1].Stichtag == pd.Timestamp(context["metadata"]["target_date"])
        assert rows["Jahresänderung-Köpfe"].sum() == rows.iloc[-1]["Kumulierte Änderung-Köpfe"]
    for key in ("baseline_snapshot", "final_snapshot", "departure_events", "accession_events"):
        pd.testing.assert_frame_equal(context[key], before[key], check_exact=True)


@pytest.mark.parametrize("block", ["azubi", "trainee", "new_hires"])
def test_active_accession_block_is_rejected_even_without_events(block):
    context = _bridge_context("no_events")
    context["zugaenge_params"][block]["active"] = True
    assert build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]["status"] == "Nicht verfügbar"


def test_missing_bases_and_missing_flags_are_not_silently_replaced():
    context = _bridge_context("no_events")
    del context["baseline_snapshot"]
    assert build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]["annual"].empty
    context = _bridge_context("no_events")
    del context["zugaenge_params"]["azubi"]["active"]
    assert build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]["annual"].empty


def test_end_value_discrepancy_is_reported_without_overwriting_projection():
    context = _bridge_context("no_events")
    context["final_snapshot"].loc[0, "MAK_Reporting"] = 0.5
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abweichung"
    company = bridge["checks"].loc[bridge["checks"].Ebene == "Gesamt"].iloc[0]
    assert company["Differenz MAK"] == 0.5
    assert company["Berechnete MAK"] == 5.0
    assert company["Finale MAK"] == 4.5


def test_documented_atz_recovery_reconciles_end_value_without_annual_event():
    context = _bridge_context("no_events")
    pid = context["baseline_snapshot"].iloc[0]["PersNr"]
    for key in ("baseline_snapshot", "final_snapshot"):
        frame = context[key]
        mask = frame.PersNr.eq(pid)
        frame.loc[mask, "Personen_MAK_Source"] = 1.0
        frame.loc[mask, "MAK_Reporting"] = 0.0 if key == "baseline_snapshot" else 1.0
        frame.loc[mask, "ist_atz_fr"] = key == "baseline_snapshot"
    context["abgaenge_params"] = {"components": {"atz": False, "quit": True}}
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["documented_effects"]["MAK-Effekt"].sum() == pytest.approx(1.0)
    company = bridge["annual"].loc[bridge["annual"].Ebene.eq("Gesamt")]
    assert company["Jahresänderung-MAK"].sum() == 0.0
    assert company.iloc[-1]["Bestand-MAK"] == pytest.approx(bridge["checks"].iloc[0]["Finale MAK"])


def test_atz_recovery_without_saved_disabled_component_remains_unreconciled():
    context = _bridge_context("no_events")
    pid = context["baseline_snapshot"].iloc[0]["PersNr"]
    for key in ("baseline_snapshot", "final_snapshot"):
        frame = context[key]
        mask = frame.PersNr.eq(pid)
        frame.loc[mask, "Personen_MAK_Source"] = 1.0
        frame.loc[mask, "MAK_Reporting"] = 0.0 if key == "baseline_snapshot" else 1.0
        frame.loc[mask, "ist_atz_fr"] = key == "baseline_snapshot"
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abweichung"


def test_ambiguous_multiple_oe_assignment_is_not_certified():
    context = _bridge_context("atz_ruhend")
    second = context["baseline_snapshot"].loc[context["baseline_snapshot"].PersNr == "000002"].copy()
    second["Organisationseinheit"] = "OE2"
    context["baseline_snapshot"] = pd.concat([context["baseline_snapshot"], second], ignore_index=True)
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abweichung"


def test_run_mismatch_is_rejected():
    with pytest.raises(ValueError):
        build_departure_stock_bridge({"run_signature": "a"}, pd.DataFrame(), expected_run_signature="b")


def test_events_outside_run_and_unknown_types_are_not_certified():
    context = _bridge_context("departures")
    context["departure_events"].loc[0, "event_date"] = pd.Timestamp("2040-01-01")
    assert build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]["status"] == "Nicht verfügbar"
    context = _bridge_context("departures")
    context["departure_events"].loc[0, "reason_code"] = "UNKNOWN"
    assert build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]["status"] == "Nicht verfügbar"


def test_mak_rounding_tolerance_does_not_hide_centimal_differences():
    context = _bridge_context("no_events")
    context["departure_events"] = pd.DataFrame([{"event_date": pd.Timestamp("2026-01-31"),
        "persnr": "000001", "Organisationseinheit": "OE1", "reason_code": "ATZ_AR_TO_FR",
        "headcount_change": 0, "mak_change": -0.003}])
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abweichung"
    context["departure_events"].loc[0, "mak_change"] = -0.006
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abweichung"
    projected = bridge["annual"].loc[bridge["annual"].Ebene == "Gesamt"].iloc[-1]["Bestand-MAK"]
    actual = bridge["checks"].loc[bridge["checks"].Ebene == "Gesamt"].iloc[0]["Finale MAK"]
    assert projected != actual


def test_unrounded_basis_removes_accumulated_rounding_discrepancy():
    context = _bridge_context("no_events")
    context["baseline_snapshot"].loc[0, "MAK_Reporting"] = 0.333333
    context["final_snapshot"].loc[0, "MAK_Reporting"] = 0.23
    context["departure_events"] = pd.DataFrame([{"event_date": pd.Timestamp("2026-01-31"),
        "persnr": "000001", "Organisationseinheit": "OE1", "reason_code": "ATZ_AR_TO_FR",
        "headcount_change": 0, "mak_change": -0.103333}])
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["checks"]["Differenz MAK"].abs().max() < 1e-9


@pytest.mark.parametrize("same_cluster", [True, False])
def test_multi_oe_departure_removes_one_head_per_scope_and_allocates_capacity(same_cluster):
    context = _bridge_context("no_events")
    baseline = pd.concat([_base_snapshot(), _base_snapshot()], ignore_index=True)
    baseline["Organisationseinheit"] = ["OE1", "OE2"]
    baseline["MAK_Reporting"] = [0.4, 0.6]
    final = baseline.copy()
    final["PersNr"] = pd.NA
    final["Is_Vacant"] = True
    final["MAK_Reporting"] = 0.0
    context.update(baseline_snapshot=baseline, final_snapshot=final,
                   cluster_mapping={"oe_map": {"OE1": "A", "OE2": "A" if same_cluster else "B"}},
                   departure_events=pd.DataFrame([{"event_date": pd.Timestamp("2026-01-31"),
                       "persnr": "000001", "Organisationseinheit": "OE1", "reason_code": "QUIT",
                       "headcount_change": -1, "mak_change": -1.0}]))
    before = deepcopy(context["departure_events"])
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    bridge = summary["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["checks"].Abgestimmt.all()
    assert bridge["annual"].loc[bridge["annual"].Ebene == "Gesamt", "Jahresänderung-Köpfe"].sum() == -1
    assert bridge["annual"].loc[bridge["annual"].Ebene == "Organisationseinheit", "Jahresänderung-Köpfe"].sum() == -2
    assert bridge["annual"].loc[bridge["annual"].Ebene == "OE-Cluster", "Jahresänderung-Köpfe"].sum() == (-1 if same_cluster else -2)
    pd.testing.assert_frame_equal(context["departure_events"], before, check_exact=True)


def test_person_diagnostic_identifies_non_event_capacity_and_missing_head():
    context = _bridge_context("no_events")
    context["final_snapshot"].loc[0, "MAK_Reporting"] = 0.5
    context["final_snapshot"].loc[1, "Is_Vacant"] = True
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abweichung"
    rows = bridge["person_diagnostics"].set_index("PersNr")
    assert rows.loc["000001", "Unerklärte MAK-Änderung"] == -0.5
    assert rows.loc["000002", "Unerklärte Kopfänderung"] == -1
    assert not bridge["checks"].Abgestimmt.all()


def test_saved_summary_refreshes_without_a_second_simulation(monkeypatch):
    from test_orgunit_simulation_page import _load_page_module
    page = _load_page_module("Organisationseinheiten_Simulation", "refresh_stock_summary")
    context = _bridge_context("no_events")
    state = {"compact_sim_signature": "a", "compact_sim_event_context": context,
             "compact_sim_event_summary": {"run_signature": "a"}}
    monkeypatch.setattr(page.st, "session_state", state)
    summary = page._get_current_event_summary()
    from utils.org_simulation_events import RECONCILIATION_VERSION
    assert summary["reconciliation_version"] == RECONCILIATION_VERSION
    assert summary["stock_reconciliation"]["status"] == "Abgestimmt"
    assert page._get_current_event_summary() is summary
    state["compact_sim_signature"] = "other"
    assert page._get_current_event_summary() is summary


def test_oe_reassignment_is_diagnosed_without_guessing_a_year():
    context = _bridge_context("no_events")
    context["final_snapshot"].loc[0, "Organisationseinheit"] = "OE2"
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abweichung"
    diagnostic = bridge["person_diagnostics"].set_index("PersNr").loc["000001"]
    assert "Zeitpunkt nicht dokumentiert" in diagnostic.Diagnose
    assert diagnostic["Unerklärte MAK-Änderung"] == 0


def test_identical_fractional_raw_stocks_are_agreed_without_rounding():
    context = _bridge_context("no_events")
    context["baseline_snapshot"].loc[0, "MAK_Reporting"] = 0.7977
    context["final_snapshot"].loc[0, "MAK_Reporting"] = 0.7977
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["checks"]["Differenz MAK"].abs().max() < 1e-9


def test_half_centimal_binary_noise_does_not_create_false_discrepancy():
    context = _bridge_context("no_events")
    baseline = _base_snapshot()
    baseline["MAK_Reporting"] = 35.605
    final = baseline.copy()
    final["MAK_Reporting"] = 35.60500000000001
    context.update(baseline_snapshot=baseline, final_snapshot=final)
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    assert bridge["checks"]["Differenz MAK"].abs().max() < 1e-12


def test_actual_atz_freistellung_preserves_heads_but_reduces_capacity():
    from dataloader.compact_simulation_engine import simulate_compact_snapshot
    from dataloader.cluster_resolver import ActiveClusterSource
    from utils.status_quo_baseline import build_status_quo_snapshot
    baseline = build_status_quo_snapshot(_base_snapshot(age_years=58), "2025-12-31")
    atz = pd.DataFrame([
        {"PersNr": "000001", "Phase": "AR", "Beginn": pd.Timestamp("2024-01-01"),
         "Ende": pd.Timestamp("2026-07-01"), "Ende ATZ Vertrag": pd.Timestamp("2027-07-01")},
        {"PersNr": "000001", "Phase": "FR", "Beginn": pd.Timestamp("2026-07-01"),
         "Ende": pd.Timestamp("2027-07-01"), "Ende ATZ Vertrag": pd.Timestamp("2027-07-01")},
    ])
    params = {k: {"active": False} for k in ("azubi", "trainee", "new_hires")}
    bundle = ClusterMappingBundle(oe_map={"OE1": "A"}, source_signature="atz-fr")
    result = simulate_compact_snapshot(baseline, atz, pd.Timestamp("2026-12-31"),
        base_date=pd.Timestamp("2025-12-31"),
        abgaenge_params={"components": {"atz": True, "quit": False, "retirement": False, "ruhend": False},
                        "atz": {"new_atz_rate": 0.0}, "random_seed": 42}, zugaenge_params=params,
        active_cluster_source=ActiveClusterSource(mode="synthetic", subtype="synthetic.default_fallback", source_signature="atz-fr"),
        cluster_mapping_bundle=bundle)
    context = {"run_signature": "a", "metadata": result.metadata, "baseline_snapshot": baseline,
               "final_snapshot": result.future_snapshot_df, "zugaenge_params": params,
               "cluster_mapping": bundle.to_dict(), "departure_events": result.abgaenge_result["events_person_level"]}
    bridge = build_simulation_event_summary(context, expected_run_signature="a")["stock_reconciliation"]
    assert bridge["status"] == "Abgestimmt"
    row = bridge["checks"].loc[bridge["checks"].Ebene == "Gesamt"].iloc[0]
    assert row["Berechnete Köpfe"] == row["Finale Köpfe"] == 1
    assert row["Berechnete MAK"] == row["Finale MAK"] == 0
