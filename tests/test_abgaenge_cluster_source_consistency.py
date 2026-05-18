import importlib.util
from pathlib import Path

import io
import pandas as pd
import streamlit as st

from abgaenge.forecast import _select_atz_prob, _select_quit_prob
from dataloader.cluster_resolver import ActiveClusterSource


ROOT = Path(__file__).resolve().parents[1]


def _load_page_module():
    page_path = next((ROOT / "pages").glob("*_Prognose_Abgänge.py"))
    spec = importlib.util.spec_from_file_location("attrition_cluster_signature_test", page_path)
    module = importlib.util.module_from_spec(spec)
    module._UNIT_TESTING = True
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


def test_abgaenge_signature_helper_invalidates_mismatched_results():
    module = _load_page_module()

    st.session_state.clear()
    st.session_state["abgaenge_global_result"] = {"events_person_level": pd.DataFrame()}
    st.session_state["abgaenge_results"] = {"events": pd.DataFrame()}
    st.session_state["abgaenge_cluster_source_signature"] = "cluster-sig-old"

    assert module._abgaenge_results_match_cluster_signature("cluster-sig-old") is True
    assert module._abgaenge_results_match_cluster_signature("cluster-sig-new") is False

    module._clear_stale_abgaenge_results()
    assert "abgaenge_global_result" not in st.session_state
    assert "abgaenge_results" not in st.session_state
    assert "abgaenge_cluster_source_signature" not in st.session_state


def test_abgaenge_matrix_widget_key_uses_cluster_signature():
    module = _load_page_module()

    assert module._cluster_widget_key("atz_matrix_editor_live", "cluster-sig-new") == "atz_matrix_editor_live_cluster-sig-new"
    assert module._cluster_widget_key("quit_matrix_editor_live_fixed", None) == "quit_matrix_editor_live_fixed_no_cluster_source"


def test_abgaenge_dimension_values_use_active_cluster_file(tmp_path):
    module = _load_page_module()
    cluster_file = tmp_path / "cluster.xlsx"

    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE B", "OE A"],
                "Cluster": ["Cluster B", "Cluster A"],
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

    df_ma = pd.DataFrame(
        {
            "Organisationseinheit": ["Snapshot OE"],
            "Jobfamily": ["Snapshot JF"],
            "JF-Cluster": ["Snapshot Cluster"],
        }
    )
    source = _build_source(str(cluster_file))

    org_units, job_families = module._get_param_dimension_values(df_ma, source, None)

    assert org_units == ["OE A", "OE B"]
    assert job_families == ["JF Cluster A", "JF Cluster B"]


def test_abgaenge_forecast_uses_jf_cluster_for_jobfamily_dimension():
    row = pd.Series(
        {
            "Organisationseinheit": "OE Snapshot",
            "Jobfamily": "Raw Snapshot JF",
            "JF-Cluster": "JF Cluster A",
            "age": 40,
        }
    )
    params = {
        "atz": {
            "use_atz_matrix": True,
            "atz_dimension": "JobFamily",
            "atz_matrix": {"JF Cluster A": 0.27, "Default": 0.05},
        },
        "quit": {
            "use_quit_matrix": True,
            "quit_dimension": "JobFamily",
            "quit_rate_base": 0.02,
            "quit_matrix": {
                "alter_30_45": {"JF Cluster A": 0.13, "Default": 0.01},
            },
            "quit_adjustments": {"more": {}, "less": {}},
        },
    }

    assert _select_atz_prob(row, params) == 0.27
    assert _select_quit_prob(row, params, 2027) == 0.13
