"""Verify additional result storage does not change the sole simulation result."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from test_compact_sim_cluster_source_consistency import _load_page7_module
from test_org_yearly_reference import _run, _payload, SCENARIOS, REFERENCE
from dataloader.cluster_resolver import ClusterMappingBundle
from dataloader.cluster_resolver import ActiveClusterSource
from dataloader.cluster_manager import load_cluster_mappings_from_source


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_store_real_result_without_changing_reference(scenario):
    page = _load_page7_module()
    result = _run(scenario)  # One simulation only; storage must not call it again.
    expected = json.loads(REFERENCE.read_text(encoding="utf-8"))["scenarios"][scenario]
    bundle = ClusterMappingBundle(oe_map={"OE1": "A"}, source_signature="source-a")
    context = page._build_simulation_event_context(result, bundle, "run-a")
    assert _payload(result) == expected
    assert context["run_signature"] == "run-a"
    assert context["metadata"] == result.metadata
    pd.testing.assert_frame_equal(context["departure_events"], result.abgaenge_result.get("events_person_level", pd.DataFrame()), check_exact=True)
    pd.testing.assert_frame_equal(context["accession_events"], result.zugaenge_result.get("events", pd.DataFrame()), check_exact=True)
    assert context["cluster_mapping"] == bundle.to_dict()


def test_context_is_independent_of_later_edits():
    page = _load_page7_module()
    result = SimpleNamespace(metadata={"target_date": "2027-12-31", "nested": {"a": 1}},
                             abgaenge_result={"events_person_level": pd.DataFrame({"headcount_change": [-1]})},
                             zugaenge_result={"events": pd.DataFrame({"count": [1]})})
    bundle = ClusterMappingBundle(oe_map={"OE1": "A"})
    saved = page._build_simulation_event_context(result, bundle, "run-a")
    result.metadata["nested"]["a"] = 2
    result.abgaenge_result["events_person_level"].loc[0, "headcount_change"] = -99
    result.zugaenge_result["events"].loc[0, "count"] = 99
    bundle.oe_map["OE1"] = "B"
    assert saved["metadata"]["nested"]["a"] == 1
    assert saved["departure_events"].loc[0, "headcount_change"] == -1
    assert saved["accession_events"].loc[0, "count"] == 1
    assert saved["cluster_mapping"]["oe_map"]["OE1"] == "A"


def test_empty_outputs_are_saved_without_fallback():
    page = _load_page7_module()
    result = SimpleNamespace(metadata={}, abgaenge_result={}, zugaenge_result={})
    context = page._build_simulation_event_context(result, ClusterMappingBundle(), "run-empty")
    assert context["departure_events"].empty
    assert context["accession_events"].empty
    assert context["cluster_mapping"]["oe_map"] == {}


def test_clear_removes_event_context_and_preserves_drafts(monkeypatch):
    page = _load_page7_module()
    state = {"compact_sim_event_context": {"run_signature": "old"},
             "compact_sim_event_summary": {"run_signature": "old"},
             "compact_sim_signature": "old", "draft_parameters": {"a": 1}}
    monkeypatch.setattr(page.st, "session_state", state)
    page._clear_simulation_cache()
    assert state == {"draft_parameters": {"a": 1}}


def test_storage_preserves_mapping_from_excel_file(tmp_path):
    path = tmp_path / "cluster.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"Organisationseinheit": ["OE1", "OE2", "OE3"],
                      "Cluster": ["Vertrieb", "Vertrieb", "Marktfolge"]}).to_excel(
                          writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame({"Planstelle": ["Beratung"], "Jobfamily Cluster": ["Regular"]}).to_excel(
            writer, sheet_name="JobFamilies", index=False)
    source = ActiveClusterSource(mode="ui_upload", subtype="ui_upload.persisted_local_copy",
                                  source_path=str(path), source_signature="excel-storage-test")
    bundle = load_cluster_mappings_from_source(source)
    page = _load_page7_module()
    result = SimpleNamespace(metadata={}, abgaenge_result={}, zugaenge_result={})
    saved = page._build_simulation_event_context(result, bundle, "run-excel")
    assert saved["cluster_mapping"]["oe_map"] == {
        "OE1": "Vertrieb", "OE2": "Vertrieb", "OE3": "Marktfolge"}
    assert saved["cluster_mapping"]["source_signature"] == source.source_signature
    # Replacing the uploaded file does not retroactively change this run.
    path.write_bytes(b"replaced after simulation")
    assert saved["cluster_mapping"]["oe_map"]["OE1"] == "Vertrieb"
