import pandas as pd
import streamlit as st

from dataloader import loader
from dataloader.cluster_resolver import ActiveClusterSource


def _make_active_source(signature: str, label: str, subtype: str = "ui_upload.persisted_local_copy") -> ActiveClusterSource:
    mode = "ui_upload" if subtype.startswith("ui_upload") else "input_folder"
    return ActiveClusterSource(
        mode=mode,
        subtype=subtype,
        status="active",
        is_active=True,
        is_valid=True,
        priority_rank=1,
        display_label=label,
        description=label,
        source_path=f"C:/tmp/{label}.xlsx",
        session_key=None,
        persisted_local_path=f"C:/tmp/{label}.xlsx" if subtype == "ui_upload.persisted_local_copy" else None,
        filename=f"{label}.xlsx",
        file_exists=True,
        content_hash=signature,
        source_signature=signature,
        activated_at=None,
        last_modified_at=None,
        oe_mapping_count=1,
        jf_mapping_count=1,
        resolution_reason="test",
        validation_errors=[],
        fallback_from=None,
        debug_meta={},
    )


def test_load_and_prepare_data_reacts_to_changed_active_cluster_source_signature(monkeypatch):
    st.cache_data.clear()
    st.session_state.clear()
    st.session_state["_cache_version_data_prep"] = 0

    base_snapshot = pd.DataFrame(
        {
            "PersNr": ["000001"],
            "Planstelle": ["P-1"],
            "Organisationseinheit": ["OE1"],
            "Kürzel OrgEinheit": ["001"],
            "Soll_FTE": [1.0],
            "FTE_assigned": [1.0],
            "Is_Vacant": [False],
            "MAK": [1.0],
            "Total_Cost_Year": [100.0],
        }
    )

    active_sources = [
        _make_active_source("cluster-sig-a", "Cluster A"),
        _make_active_source("cluster-sig-b", "Cluster B"),
    ]
    call_index = {"value": 0}
    apply_calls = {"count": 0}

    def _get_active_cluster_source(session_state=None):
        idx = min(call_index["value"], len(active_sources) - 1)
        source = active_sources[idx]
        call_index["value"] += 1
        return source

    monkeypatch.setattr(loader, "get_active_cluster_source", _get_active_cluster_source)
    monkeypatch.setattr(loader, "store_active_cluster_source_in_session", lambda session_state, active_cluster_source: {})
    monkeypatch.setattr(loader, "load_hr_data", lambda *args, **kwargs: {
        "snapshot_detail": base_snapshot.copy(),
        "history_cube": pd.DataFrame({"Date": pd.to_datetime(["2026-01-01"])}),
        "org_structure": pd.DataFrame({"Organisationseinheit": ["OE1"]}),
    })
    monkeypatch.setattr(loader, "enrich_snapshot_data", lambda df, stichtag=None, cohort_definitions=None: df.copy())
    monkeypatch.setattr(loader, "_apply_jobfamilies", lambda df: df.assign(Jobfamily="JF"))
    monkeypatch.setattr(
        loader,
        "load_cluster_mappings_from_source",
        lambda active_cluster_source: type("Bundle", (), {"oe_map": {}, "jf_map": {}, "source_signature": active_cluster_source.source_signature})(),
    )

    def _apply_clusters_to_snapshot_from_source(df, active_cluster_source, mapping_bundle=None):
        apply_calls["count"] += 1
        return df.assign(
            **{
                "OE-Cluster": active_cluster_source.display_label,
                "JF-Cluster": active_cluster_source.display_label,
            }
        )

    monkeypatch.setattr(loader, "apply_clusters_to_snapshot_from_source", _apply_clusters_to_snapshot_from_source)
    monkeypatch.setattr(loader, "_zero_out_azubi_mak", lambda df: df)
    monkeypatch.setattr(loader, "apply_exclusions", lambda df, exclusions: df)
    monkeypatch.setattr(loader, "get_data_summary", lambda df: {"rows": int(len(df))})
    monkeypatch.setattr(loader, "_load_tvoed_lookup_cached", lambda uploaded_tvoed_bytes, tvoed_file_signature: {})

    first = loader.load_and_prepare_data(use_original=False)
    second = loader.load_and_prepare_data(use_original=False)

    assert apply_calls["count"] == 2
    assert first[0].loc[0, "OE-Cluster"] == "Cluster A"
    assert second[0].loc[0, "OE-Cluster"] == "Cluster B"
    assert first[3]["active_cluster_source_signature"] == "cluster-sig-a"
    assert second[3]["active_cluster_source_signature"] == "cluster-sig-b"


def test_loader_uses_resolved_source_bytes_instead_of_global_upload_alias(monkeypatch):
    st.session_state.clear()
    source = _make_active_source("cluster-sig-session", "Cluster Session", subtype="ui_upload.session")
    source.debug_meta["source_bytes"] = b"session-bytes"
    st.session_state["global_uploads"] = {"Cluster": b"legacy-bytes"}

    monkeypatch.setattr(loader, "get_active_cluster_source", lambda session_state=None: source)
    stored = {}
    monkeypatch.setattr(
        loader,
        "store_active_cluster_source_in_session",
        lambda session_state, active_cluster_source: stored.update({"signature": active_cluster_source.source_signature}),
    )

    active_source, cluster_source_bytes = loader._resolve_loader_cluster_source()

    assert active_source.source_signature == "cluster-sig-session"
    assert cluster_source_bytes == b"session-bytes"
    assert stored["signature"] == "cluster-sig-session"
