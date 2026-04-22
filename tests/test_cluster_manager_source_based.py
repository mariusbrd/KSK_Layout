import io

import pandas as pd
import streamlit as st

from dataloader.cluster_manager import (
    apply_clusters_to_snapshot,
    apply_clusters_to_snapshot_from_source,
    delete_persisted_cluster_upload,
    get_active_cluster_file,
    load_cluster_mappings,
    load_cluster_mappings_from_source,
    persist_cluster_upload_bytes,
    validate_and_save_clusters,
    validate_cluster_upload,
)
from dataloader.cluster_resolver import (
    ActiveClusterSource,
    MODE_INPUT_FOLDER,
    MODE_SYNTHETIC,
    MODE_UI_UPLOAD,
    STATUS_ACTIVE,
    STATUS_FALLBACK,
    SUBTYPE_INPUT_EXTERNAL,
    SUBTYPE_SYNTHETIC_FALLBACK,
    SUBTYPE_UI_UPLOAD_PERSISTED,
)


def _build_cluster_workbook_bytes(
    *,
    oe_cluster: str = "OE-Cluster-A",
    jf_cluster: str = "JF-Cluster-A",
) -> bytes:
    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Cluster": [oe_cluster],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Planstelle": ["P1"],
                "Jobfamily Cluster": [jf_cluster],
            }
        ).to_excel(writer, sheet_name="JobFamilies", index=False)
    return payload.getvalue()


def _build_invalid_workbook_missing_sheet() -> bytes:
    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Cluster": ["Cluster-A"],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
    return payload.getvalue()


def _build_invalid_workbook_missing_columns() -> bytes:
    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Wrong": ["Cluster-A"],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame(
            {
                "Planstelle": ["P1"],
                "Wrong": ["JF-Cluster-A"],
            }
        ).to_excel(writer, sheet_name="JobFamilies", index=False)
    return payload.getvalue()


def _build_source(
    *,
    mode: str,
    subtype: str,
    source_path: str | None = None,
    source_bytes: bytes | None = None,
    source_signature: str = "sig",
) -> ActiveClusterSource:
    return ActiveClusterSource(
        mode=mode,
        subtype=subtype,
        status=STATUS_ACTIVE if subtype != SUBTYPE_SYNTHETIC_FALLBACK else STATUS_FALLBACK,
        is_active=True,
        is_valid=True,
        priority_rank=1,
        display_label="Test Source",
        description="Test source",
        source_path=source_path,
        session_key=None,
        persisted_local_path=source_path if subtype == SUBTYPE_UI_UPLOAD_PERSISTED else None,
        filename="cluster.xlsx" if source_path else None,
        file_exists=bool(source_path),
        content_hash=None,
        source_signature=source_signature,
        activated_at=None,
        last_modified_at=None,
        oe_mapping_count=0,
        jf_mapping_count=0,
        resolution_reason="test",
        validation_errors=[],
        fallback_from=None,
        debug_meta={"source_bytes": source_bytes} if source_bytes is not None else {},
    )


def test_validate_cluster_upload_success():
    result = validate_cluster_upload(_build_cluster_workbook_bytes())

    assert result.is_valid is True
    assert result.oe_mapping_count == 1
    assert result.jf_mapping_count == 1
    assert result.detected_jobfamily_format == "position_based"


def test_validate_cluster_upload_fails_for_missing_sheets_and_columns():
    missing_sheet = validate_cluster_upload(_build_invalid_workbook_missing_sheet())
    missing_columns = validate_cluster_upload(_build_invalid_workbook_missing_columns())

    assert missing_sheet.is_valid is False
    assert "Tabellenblaetter" in missing_sheet.message
    assert missing_columns.is_valid is False
    assert any("OrgUnits" in err for err in missing_columns.errors)


def test_persist_and_delete_cluster_upload_bytes(tmp_path):
    target = tmp_path / "cluster_mapping.xlsx"
    payload = _build_cluster_workbook_bytes()

    persisted = persist_cluster_upload_bytes(payload, target_path=str(target))
    deleted = delete_persisted_cluster_upload(target_path=str(target))
    deleted_again = delete_persisted_cluster_upload(target_path=str(target))

    assert persisted["success"] is True
    assert persisted["bytes_written"] == len(payload)
    assert deleted["success"] is True
    assert deleted["deleted"] is True
    assert target.exists() is False
    assert deleted_again["success"] is True
    assert deleted_again["deleted"] is False


def test_load_cluster_mappings_from_source_supports_persisted_external_and_synthetic(tmp_path):
    persisted = tmp_path / "cluster_mapping.xlsx"
    external = tmp_path / "OE_Cluster.xlsx"
    persisted.write_bytes(_build_cluster_workbook_bytes(oe_cluster="Persisted", jf_cluster="Persisted-JF"))
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    persisted_bundle = load_cluster_mappings_from_source(
        _build_source(
            mode=MODE_UI_UPLOAD,
            subtype=SUBTYPE_UI_UPLOAD_PERSISTED,
            source_path=str(persisted),
            source_signature="persisted-sig",
        )
    )
    external_bundle = load_cluster_mappings_from_source(
        _build_source(
            mode=MODE_INPUT_FOLDER,
            subtype=SUBTYPE_INPUT_EXTERNAL,
            source_path=str(external),
            source_signature="external-sig",
        )
    )
    synthetic_bundle = load_cluster_mappings_from_source(
        _build_source(
            mode=MODE_SYNTHETIC,
            subtype=SUBTYPE_SYNTHETIC_FALLBACK,
            source_signature="synthetic-sig",
        )
    )

    assert persisted_bundle.oe_map["OE1"] == "Persisted"
    assert persisted_bundle.jf_map[("OE1", "P1")] == "Persisted-JF"
    assert external_bundle.oe_map["OE1"] == "External"
    assert external_bundle.jf_map[("OE1", "P1")] == "External-JF"
    assert synthetic_bundle.oe_map == {}
    assert synthetic_bundle.jf_map == {}


def test_apply_clusters_to_snapshot_from_source_enriches_snapshot(tmp_path):
    persisted = tmp_path / "cluster_mapping.xlsx"
    persisted.write_bytes(_build_cluster_workbook_bytes(oe_cluster="Persisted", jf_cluster="Persisted-JF"))
    source = _build_source(
        mode=MODE_UI_UPLOAD,
        subtype=SUBTYPE_UI_UPLOAD_PERSISTED,
        source_path=str(persisted),
        source_signature="persisted-sig",
    )
    df = pd.DataFrame(
        {
            "Organisationseinheit": ["OE1", "OE2"],
            "Planstelle": ["P1", "P2"],
            "Jobfamily": ["Beratung", "Unknown"],
        }
    )

    result = apply_clusters_to_snapshot_from_source(df, source)

    assert result.loc[0, "OE-Cluster"] == "Persisted"
    assert result.loc[0, "JF-Cluster"] == "Persisted-JF"
    assert result.loc[1, "OE-Cluster"] == "Unclustered"
    assert result.loc[1, "JF-Cluster"] == "Sonstiges"


def test_legacy_wrappers_continue_to_return_usable_results(tmp_path, monkeypatch):
    st.cache_data.clear()
    st.session_state.clear()

    persisted = tmp_path / "cluster_mapping.xlsx"
    external = tmp_path / "OE_Cluster.xlsx"
    persisted.write_bytes(_build_cluster_workbook_bytes(oe_cluster="Persisted", jf_cluster="Persisted-JF"))
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    monkeypatch.setattr("dataloader.cluster_manager.CLUSTER_FILE", str(persisted))
    monkeypatch.setattr("dataloader.cluster_manager.EXTERNAL_CLUSTER_FILE", str(external))

    oe_map, jf_map = load_cluster_mappings()
    wrapped = apply_clusters_to_snapshot(
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Planstelle": ["P1"],
                "Jobfamily": ["Beratung"],
            }
        )
    )

    assert oe_map["OE1"] == "Persisted"
    assert jf_map[("OE1", "P1")] == "Persisted-JF"
    assert wrapped.loc[0, "OE-Cluster"] == "Persisted"
    assert wrapped.loc[0, "JF-Cluster"] == "Persisted-JF"


def test_validate_and_save_clusters_legacy_wrapper_persists_file(tmp_path, monkeypatch):
    target = tmp_path / "cluster_mapping.xlsx"
    monkeypatch.setattr("dataloader.cluster_manager.CLUSTER_FILE", str(target))

    success, message = validate_and_save_clusters(io.BytesIO(_build_cluster_workbook_bytes()))

    assert success is True
    assert "erfolgreich" in message
    assert target.exists() is True


def test_get_active_cluster_file_prefers_persisted_copy_without_mtime(tmp_path, monkeypatch):
    persisted = tmp_path / "cluster_mapping.xlsx"
    external = tmp_path / "OE_Cluster.xlsx"
    persisted.write_bytes(_build_cluster_workbook_bytes(oe_cluster="Persisted"))
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External"))

    monkeypatch.setattr("dataloader.cluster_manager.CLUSTER_FILE", str(persisted))
    monkeypatch.setattr("dataloader.cluster_manager.EXTERNAL_CLUSTER_FILE", str(external))

    assert get_active_cluster_file() == str(persisted)
