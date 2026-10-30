"""A delayed quit changes annual timing while retaining the saved final stock."""
import io
import pandas as pd
import pytest
import streamlit as st

from test_orgunit_simulation_page import _base_snapshot, _inactive_zugaenge_params, BASE_DATE
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.cluster_resolver import get_active_cluster_source
from dataloader.cluster_manager import load_cluster_mappings_from_source
from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
from utils.compact_page_loader import load_compact_page_module
from utils.compact_simulation_export import build_compact_simulation_export_bytes
from utils.org_simulation_events import build_simulation_event_summary
from utils.settings_loader import get_setting
from utils.status_quo_baseline import build_status_quo_snapshot
from utils.simulation_adjustments import (
    derive_combined_adjustment, derive_shifted_departure, derive_shifted_departures, eligible_timing_shifts,
)
from utils.simulation_validation import (
    approve_adjustment, current_view, register_new_run, stage_adjustment, value_fingerprint,
)


@pytest.fixture
def saved_quit_run(request):
    st.session_state["tvoed_lookup"] = {}
    source = _base_snapshot()
    if getattr(request, "param", False):
        additional = source.loc[source["PersNr"].eq("000002")].copy()
        additional["PersNr"] = "000003"
        additional["Personalnummer"] = "000003"
        source = pd.concat([source, additional], ignore_index=True)
    source["Jobfamily"] = "Sonstiges"
    source["JF-Cluster"] = "Sonstiges"
    target = pd.Timestamp("2027-12-31")
    params = {"components": {"atz": False, "retirement": False, "quit": True, "ruhend": False},
              "quit": {"quit_rate_base": 1.0, "use_quit_matrix": False}, "random_seed": 42}
    result = simulate_compact_snapshot(
        source, pd.DataFrame(), target, base_date=BASE_DATE,
        abgaenge_params=params, zugaenge_params=_inactive_zugaenge_params(),
    )
    compact = load_compact_page_module()
    prepared = compact.prepare_compact_data(result.future_snapshot_df)
    bundle = load_cluster_mappings_from_source(get_active_cluster_source())
    context = {
        "run_signature": "quit-timing", "metadata": result.metadata,
        "source_snapshot": source, "raw_final_snapshot": result.future_snapshot_df,
        "future_employee_state": result.future_employee_df,
        "baseline_snapshot": compact.prepare_compact_data(build_status_quo_snapshot(source, BASE_DATE)),
        "final_snapshot": prepared,
        "departure_events": result.abgaenge_result["events_person_level"],
        "accession_events": result.zugaenge_result["events"],
        "df_atz": pd.DataFrame(), "zugaenge_params": _inactive_zugaenge_params(),
        "abgaenge_params": params, "cluster_mapping": bundle.to_dict(),
        "run_settings": {"exclusions": get_setting("exclusions", {})},
        "cost_lookup_fingerprint": value_fingerprint({}),
    }
    summary = build_simulation_event_summary(context, expected_run_signature="quit-timing")
    state = {"compact_sim_signature": "quit-timing", "compact_sim_prepared_df": prepared,
             "compact_sim_status_quo_df": context["baseline_snapshot"],
             "compact_sim_metadata": result.metadata, "compact_sim_event_context": context,
             "compact_sim_event_summary": summary}
    register_new_run(state)
    yield current_view(state)
    st.session_state.clear()


def test_shifted_quit_preserves_final_stock_and_moves_one_year(saved_quit_run):
    original = saved_quit_run
    candidates = eligible_timing_shifts(original)
    assert "000002" in set(candidates["PersNr"])
    adjusted = derive_shifted_departure(original, "000002", 2027, "Kündigung um ein Jahr verschoben")
    assert adjusted.prepared_df is original.prepared_df
    assert adjusted.event_context["final_snapshot"] is original.event_context["final_snapshot"]
    assert original.event_context["departure_events"].loc[
        original.event_context["departure_events"]["persnr"].eq("000002"), "event_date"
    ].iloc[0].year == 2025
    assert adjusted.event_context["departure_events"].loc[
        adjusted.event_context["departure_events"]["persnr"].eq("000002"), "event_date"
    ].iloc[0].year == 2027
    assert adjusted.event_summary["stock_reconciliation"]["status"] == "Abgestimmt"
    before = original.event_summary["stock_reconciliation"]["annual"]
    after = adjusted.event_summary["stock_reconciliation"]["annual"]
    def mak_in(frame, year):
        return frame.loc[(frame["Ebene"] == "Organisationseinheit")
                         & (frame["Organisation"] == "Marktfolge")
                         & (frame["Art"] == "Jahresstichtag")
                         & (frame["Jahr"] == year), "Bestand-MAK"].iloc[0]
    assert mak_in(after, 2026) - mak_in(before, 2026) == pytest.approx(0.8)
    assert mak_in(after, 2027) == pytest.approx(mak_in(before, 2027))
    original_jf = build_jobfamily_yearly_view(original.event_context, original.event_summary, dimension="Jobfamily")
    adjusted_jf = build_jobfamily_yearly_view(adjusted.event_context, adjusted.event_summary, dimension="Jobfamily")
    def jf_mak_in(view, year):
        stock = view["stock"]
        return stock.loc[(stock["Stichtag"].dt.year == year) & stock["Art"].eq("Jahresstichtag")
                         & stock["Kennzahl"].eq("MAK") & stock["Jobfamily"].eq("Sonstiges"), "Bestand"].iloc[0]
    assert jf_mak_in(adjusted_jf, 2026) - jf_mak_in(original_jf, 2026) == pytest.approx(0.8)
    assert jf_mak_in(adjusted_jf, 2027) == pytest.approx(jf_mak_in(original_jf, 2027))
    assert adjusted.audit_tables["Abgaenge_Events_Raw"].loc[
        adjusted.audit_tables["Abgaenge_Events_Raw"]["persnr"].eq("000002"), "event_date"
    ].iloc[0].year == 2027
    payload = build_compact_simulation_export_bytes(
        prepared_df=adjusted.prepared_df,
        abgaenge_params=original.event_context["abgaenge_params"],
        zugaenge_params=original.event_context["zugaenge_params"],
        metadata=adjusted.metadata, audit_tables=adjusted.audit_tables,
    )
    exported = pd.read_excel(io.BytesIO(payload), sheet_name="15_Abgaenge_Personenliste")
    assert exported.loc[exported["PersNr"].astype(str).str.zfill(6).eq("000002"), "Austrittsjahr"].iloc[0] == 2027
    state = {"compact_sim_signature": original.signature,
             "compact_sim_prepared_df": original.prepared_df,
             "compact_sim_status_quo_df": original.status_quo_df,
             "compact_sim_metadata": original.metadata,
             "compact_sim_audit_tables": original.audit_tables,
             "compact_sim_event_context": original.event_context,
             "compact_sim_event_summary": original.event_summary,
             "simulation_validation_instance": original.instance}
    stage_adjustment(state, adjusted)
    approved = approve_adjustment(state, approved_at="2026-10-27T00:00:00+00:00")
    assert current_view(state, require_approval=True).event_summary is adjusted.event_summary
    assert approved.prepared_df is original.prepared_df


def test_shift_rejects_mak_larger_than_oe_stock_and_invalid_year(saved_quit_run):
    original = saved_quit_run
    with pytest.raises(ValueError):
        derive_shifted_departure(original, "000002", 2028)
    original.event_context["departure_events"].loc[
        original.event_context["departure_events"]["persnr"].eq("000002"), "mak_change"
    ] = -1.0
    assert "000002" not in set(eligible_timing_shifts(original)["PersNr"])


@pytest.mark.parametrize("saved_quit_run", [True], indirect=True)
def test_two_shifts_are_approved_together_and_annual_effects_add(saved_quit_run):
    original = saved_quit_run
    candidates = eligible_timing_shifts(original).set_index("PersNr")
    assert {"000002", "000003"}.issubset(candidates.index)
    adjusted = derive_shifted_departures(original, {"000002": 2026, "000003": 2027}, "Beide Kündigungen später")
    assert adjusted.prepared_df is original.prepared_df
    assert adjusted.event_context["final_snapshot"] is original.event_context["final_snapshot"]
    assert adjusted.event_summary["stock_reconciliation"]["status"] == "Abgestimmt"
    assert len(adjusted.metadata["adjustment"]["shifts"]) == 2
    annual_before = original.event_summary["stock_reconciliation"]["annual"]
    annual_after = adjusted.event_summary["stock_reconciliation"]["annual"]
    def oe_mak(frame, year):
        return frame.loc[(frame["Ebene"] == "Organisationseinheit")
                         & frame["Organisation"].eq("Marktfolge")
                         & frame["Art"].eq("Jahresstichtag")
                         & frame["Jahr"].eq(year), "Bestand-MAK"].iloc[0]
    assert oe_mak(annual_after, 2025) - oe_mak(annual_before, 2025) == pytest.approx(1.6)
    assert oe_mak(annual_after, 2026) - oe_mak(annual_before, 2026) == pytest.approx(0.8)
    assert oe_mak(annual_after, 2027) == pytest.approx(oe_mak(annual_before, 2027))
    original_jf = build_jobfamily_yearly_view(original.event_context, original.event_summary, dimension="Jobfamily")
    adjusted_jf = build_jobfamily_yearly_view(adjusted.event_context, adjusted.event_summary, dimension="Jobfamily")
    def jf_mak(view, year):
        stock = view["stock"]
        return stock.loc[(stock["Stichtag"].dt.year == year) & stock["Art"].eq("Jahresstichtag")
                         & stock["Kennzahl"].eq("MAK") & stock["Jobfamily"].eq("Sonstiges"), "Bestand"].iloc[0]
    assert jf_mak(adjusted_jf, 2026) - jf_mak(original_jf, 2026) == pytest.approx(0.8)
    for person, year in (("000002", 2026), ("000003", 2027)):
        event = adjusted.audit_tables["Abgaenge_Events_Raw"]
        assert event.loc[event["persnr"].eq(person), "event_date"].iloc[0].year == year
    with pytest.raises(ValueError):
        derive_shifted_departures(original, {"000002": 2026, "000003": 2028})


@pytest.mark.parametrize("saved_quit_run", [True], indirect=True)
def test_retained_and_shifted_departure_share_one_approved_version(saved_quit_run):
    original = saved_quit_run
    adjusted = derive_combined_adjustment(
        original, ["000002"], {"000003": 2027}, "Kündigung später")
    rule = adjusted.metadata["adjustment"]
    assert rule["kind"] == "combined_departures"
    assert rule["retained"] == ("000002",)
    assert rule["shifts"][0]["person"] == "000003"
    assert adjusted.event_summary["run_signature"] == adjusted.version
    assert adjusted.event_context["run_signature"] == adjusted.version
    assert adjusted.event_summary["stock_reconciliation"]["status"] == "Abgestimmt"
    final = adjusted.event_context["final_snapshot"]
    assert not final.loc[final["PersNr"].eq("000002"), "Is_Vacant"].any()
    assert adjusted.event_context["departure_events"].loc[
        adjusted.event_context["departure_events"]["persnr"].eq("000003"), "event_date"].iloc[0].year == 2027
    state = {"compact_sim_signature": original.signature,
             "compact_sim_prepared_df": original.prepared_df,
             "compact_sim_status_quo_df": original.status_quo_df,
             "compact_sim_metadata": original.metadata,
             "compact_sim_audit_tables": original.audit_tables,
             "compact_sim_event_context": original.event_context,
             "compact_sim_event_summary": original.event_summary,
             "simulation_validation_instance": original.instance}
    stage_adjustment(state, adjusted)
    approve_adjustment(state, approved_at="2026-10-27T00:00:00+00:00")
    assert current_view(state, require_approval=True).version == adjusted.version
    with pytest.raises(ValueError, match="zugleich"):
        derive_combined_adjustment(original, ["000002"], {"000002": 2027}, "Konflikt")


def test_shift_rejects_a_year_after_a_known_contract_end(saved_quit_run):
    original = saved_quit_run
    source = original.event_context["baseline_snapshot"]
    source.loc[source["PersNr"].eq("000002"), "Austritt"] = pd.Timestamp("2026-12-31")
    row = eligible_timing_shifts(original).set_index("PersNr").loc["000002"]
    assert 2027 not in row["Zulässige Jahre"]
    assert 2026 in row["Zulässige Jahre"]
