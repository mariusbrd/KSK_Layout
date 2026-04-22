import importlib.util
from pathlib import Path

import pandas as pd
import streamlit as st

from dataloader.cluster_resolver import ClusterMappingBundle
from dataloader.jobfamily_service import JobFamilyService
from zugaenge.enrichment import build_known_jf_keys, enrich_zugaenge_events


ROOT = Path(__file__).resolve().parents[1]


def _load_page_module(glob_pattern: str, module_name: str):
    page_path = next((ROOT / "pages").glob(glob_pattern))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_known_jf_keys_uses_explicit_bundle_without_implicit_resolution(monkeypatch):
    def _fail_legacy_resolution():
        raise AssertionError("legacy cluster resolution should not run when a bundle is provided")

    monkeypatch.setattr("dataloader.cluster_manager.load_cluster_mappings", _fail_legacy_resolution)

    bundle = ClusterMappingBundle(jf_map={"MappedJF": "MappedCluster"})
    result = build_known_jf_keys(
        {"azubi": {"jf_to_cluster_map": {"ParamJF": "ParamCluster"}}},
        pd.DataFrame(),
        cluster_mapping_bundle=bundle,
        active_cluster_source_signature="sig-a",
    )

    assert "mappedjf" in result
    assert "paramjf" in result


def test_enrich_zugaenge_events_uses_explicit_cluster_mapping_bundle():
    events = pd.DataFrame(
        [
            {
                "persnr": "NH_001",
                "type": "New_Hire",
                "date": pd.Timestamp("2026-01-15"),
                "Organisationseinheit": "OE A",
                "Planstelle": "P100",
                "Jobfamily": "UnknownJF",
                "mak": 1.0,
                "count": 1,
                "source": "NewHire",
            }
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "PersNr": "EMP_01",
                "Organisationseinheit": "OE A",
                "Jobfamily": "IT",
                "JF-Cluster": "IT-Cluster",
                "OE-Cluster": "OE-Cluster-A",
            }
        ]
    )
    bundle = ClusterMappingBundle(
        jf_map={("OE A", "P100"): "Bundle-JF-Cluster"},
        source_signature="cluster-sig-a",
    )

    enriched = enrich_zugaenge_events(
        events,
        snapshot,
        {"azubi": {"jf_to_cluster_map": {}}},
        cluster_mapping_bundle=bundle,
        active_cluster_source_signature="cluster-sig-a",
    )

    assert enriched.iloc[0]["JF-Cluster"] == "Bundle-JF-Cluster"
    assert enriched.iloc[0]["OE-Cluster"] != "Unclustered"


def test_get_active_jobfamilies_returns_real_jobfamilies_not_cluster_values():
    df_ma = pd.DataFrame({"Jobfamily": ["JF1", "JF2", "JF1"]})
    bundle = ClusterMappingBundle(jf_map={"LegacyJF": "Cluster Label", "OtherJF": "Other Cluster"})

    result = JobFamilyService.get_active_jobfamilies(df_ma, cluster_mapping_bundle=bundle)

    assert result == ["JF1", "JF2"]
    assert "Cluster Label" not in result


def test_page4_signature_helper_invalidates_mismatched_results():
    module = _load_page_module("*_Prognose_Zugänge.py", "page4_cluster_signature_test")

    st.session_state.clear()
    st.session_state["zugaenge_global_result"] = {"events": pd.DataFrame()}
    st.session_state["zugaenge_vacancies"] = []
    st.session_state["zugaenge_cluster_source_signature"] = "cluster-sig-old"

    assert module._is_zugaenge_result_current("cluster-sig-old") is True
    assert module._is_zugaenge_result_current("cluster-sig-new") is False

    module._clear_stale_zugaenge_results()
    assert "zugaenge_global_result" not in st.session_state
    assert "zugaenge_vacancies" not in st.session_state
    assert "zugaenge_cluster_source_signature" not in st.session_state


def test_page5_signature_helper_invalidates_mismatched_results():
    module = _load_page_module("*_Prognose_Hybrid.py", "page5_cluster_signature_test")

    st.session_state.clear()
    st.session_state["hybrid_abg_res"] = {"events_person_level": pd.DataFrame()}
    st.session_state["hybrid_zug_res"] = {"events": pd.DataFrame()}
    st.session_state["hybrid_cluster_source_signature"] = "cluster-sig-old"

    assert module._hybrid_results_match_cluster_signature("cluster-sig-old") is True
    assert module._hybrid_results_match_cluster_signature("cluster-sig-new") is False

    module.invalidate_cluster_dependent_state(
        st.session_state,
        reason="hybrid_cluster_source_changed",
    )
    assert "hybrid_abg_res" not in st.session_state
    assert "hybrid_zug_res" not in st.session_state
    assert "hybrid_cluster_source_signature" not in st.session_state
