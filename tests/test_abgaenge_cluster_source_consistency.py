import importlib.util
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]


def _load_page_module():
    page_path = next((ROOT / "pages").glob("*_Prognose_Abgänge.py"))
    spec = importlib.util.spec_from_file_location("attrition_cluster_signature_test", page_path)
    module = importlib.util.module_from_spec(spec)
    module._UNIT_TESTING = True
    spec.loader.exec_module(module)
    return module


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
