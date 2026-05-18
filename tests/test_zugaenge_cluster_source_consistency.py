import importlib.util
import io
from pathlib import Path

import pandas as pd
import streamlit as st

from dataloader.cluster_resolver import ActiveClusterSource, ClusterMappingBundle
from dataloader.jobfamily_service import JobFamilyService
from zugaenge.enrichment import build_known_jf_keys, enrich_zugaenge_events


ROOT = Path(__file__).resolve().parents[1]


def _load_page_module(glob_pattern: str, module_name: str):
    page_path = next((ROOT / "pages").glob(glob_pattern))
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_source(source_path: str, source_signature: str = "cluster-sig-test") -> ActiveClusterSource:
    return ActiveClusterSource(
        mode="ui_upload",
        subtype="ui_upload.persisted_local_copy",
        status="active",
        is_active=True,
        is_valid=True,
        priority_rank=1,
        display_label="Test Source",
        description="Test source",
        source_path=source_path,
        session_key=None,
        persisted_local_path=source_path,
        filename="cluster.xlsx",
        file_exists=True,
        content_hash=source_signature,
        source_signature=source_signature,
        activated_at=None,
        last_modified_at=None,
        oe_mapping_count=1,
        jf_mapping_count=1,
        resolution_reason="test",
        validation_errors=[],
        fallback_from=None,
        debug_meta={},
    )


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


def test_page5_dimension_values_use_active_cluster_file(tmp_path):
    module = _load_page_module("*_Prognose_Hybrid.py", "page5_dimension_values_test")
    cluster_file = tmp_path / "cluster.xlsx"

    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE B", "OE A"],
                "Cluster": ["OE Cluster B", "OE Cluster A"],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE A", "OE B"],
                "Planstelle": ["P1", "P2"],
                "Jobfamily Cluster": ["JF Cluster B", "JF Cluster A"],
            }
        ).to_excel(writer, sheet_name="JobFamilies", index=False)
    cluster_file.write_bytes(payload.getvalue())

    snapshot = pd.DataFrame(
        {
            "Organisationseinheit": ["Snapshot OE"],
            "Jobfamily": ["Snapshot JF"],
            "OE-Cluster": ["Snapshot OE Cluster"],
            "JF-Cluster": ["Snapshot JF Cluster"],
        }
    )
    source = _build_source(str(cluster_file))

    resolved = module._get_active_dimension_values(snapshot, source, None)

    assert resolved.org_units == ["OE A", "OE B"]
    assert resolved.job_family_clusters == ["JF Cluster A", "JF Cluster B"]


def test_page4_matrix_widget_keys_use_cluster_signature():
    module = _load_page_module("*_Prognose_Zugänge.py", "page4_cluster_widget_key_test")

    assert module._cluster_widget_key("az_takeover_matrix", "cluster-sig-new") == "az_takeover_matrix_cluster-sig-new"
    assert module._cluster_widget_key("hire_dist_matrix", None) == "hire_dist_matrix_no_cluster_source"


def test_page4_dimension_values_use_active_cluster_file(tmp_path):
    module = _load_page_module("*_Prognose_Zugänge.py", "page4_dimension_values_test")
    cluster_file = tmp_path / "cluster.xlsx"

    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE B", "OE A"],
                "Cluster": ["OE Cluster B", "OE Cluster A"],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE A", "OE B"],
                "Planstelle": ["P1", "P2"],
                "Jobfamily Cluster": ["JF Cluster B", "JF Cluster A"],
            }
        ).to_excel(writer, sheet_name="JobFamilies", index=False)
    cluster_file.write_bytes(payload.getvalue())

    snapshot = pd.DataFrame(
        {
            "Organisationseinheit": ["Snapshot OE"],
            "Jobfamily": ["Snapshot JF"],
            "OE-Cluster": ["Snapshot OE Cluster"],
            "JF-Cluster": ["Snapshot JF Cluster"],
        }
    )
    source = _build_source(str(cluster_file))

    resolved = module._get_active_dimension_values(snapshot, source, None)

    assert resolved.org_units == ["OE A", "OE B"]
    assert resolved.oe_clusters == ["OE Cluster A", "OE Cluster B"]
    assert resolved.job_family_clusters == ["JF Cluster A", "JF Cluster B"]


def test_page4_distribution_base_uses_active_cluster_dimensions(tmp_path):
    module = _load_page_module("*_Prognose_Zugänge.py", "page4_distribution_base_test")
    cluster_file = tmp_path / "cluster.xlsx"

    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE A", "OE B"],
                "Cluster": ["OE Cluster A", "OE Cluster B"],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE A", "OE B"],
                "Planstelle": ["P1", "P2"],
                "Jobfamily Cluster": ["JF Cluster A", "JF Cluster B"],
            }
        ).to_excel(writer, sheet_name="JobFamilies", index=False)
    cluster_file.write_bytes(payload.getvalue())

    snapshot = pd.DataFrame(
        {
            "JF-Cluster": ["JF Cluster A"],
            "OE-Cluster": ["OE Cluster A"],
        }
    )
    source = _build_source(str(cluster_file))
    resolved = module._get_active_dimension_values(snapshot, source, None)

    dist_base = module._build_hiring_distribution_base(snapshot, resolved)

    combos = set(tuple(row) for row in dist_base[["Jobfamily", "OE-Cluster"]].itertuples(index=False, name=None))
    assert ("JF Cluster A", "OE Cluster A") in combos
    assert ("JF Cluster A", "OE Cluster B") in combos
    assert ("JF Cluster B", "OE Cluster A") in combos
    assert ("JF Cluster B", "OE Cluster B") in combos
