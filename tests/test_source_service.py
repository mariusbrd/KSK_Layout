import pytest
import os
from dataloader.source_service import SourceService, DataSourceOrigin
from dataloader.cluster_resolver import ActiveClusterSource

def test_derive_group_status_uploaded():
    # Setup: At least one file is uploaded
    uploads = {"Mitarbeiter": b"fake", "Planstellen": b"fake"}
    status = SourceService.derive_group_status(
        "Mitarbeiterinformationen", 
        uploads, 
        "nonexistent_dir",
        "nonexistent_cluster_dir"
    )
    assert status.origin == DataSourceOrigin.UPLOADED
    assert status.is_complete is False # Needs 4 files
    assert status.completeness_label == "teilweise vorhanden"

def test_derive_group_status_original(tmp_path):
    # Setup: No uploads, but files in original_dir
    original_dir = tmp_path / "Original-Daten"
    original_dir.mkdir()
    (original_dir / "TVÖD.xlsx").write_text("fake")
    
    status = SourceService.derive_group_status(
        "Entgeltinformationen", 
        {}, 
        str(original_dir),
        "nonexistent_cluster_dir"
    )
    assert status.origin == DataSourceOrigin.ORIGINAL
    assert status.is_complete is True
    assert status.completeness_label == "vollständig"

def test_derive_group_status_synthetic():
    # Setup: No uploads, no original files
    status = SourceService.derive_group_status(
        "Entgeltinformationen", 
        {}, 
        "nonexistent_dir",
        "nonexistent_cluster_dir"
    )
    assert status.origin == DataSourceOrigin.SYNTHETIC
    assert status.completeness_label == "aktiv"

def test_ma_info_partial_original(tmp_path):
    # Setup: 2 of 4 files exist in original_dir
    original_dir = tmp_path / "Original-Daten"
    original_dir.mkdir()
    (original_dir / "Mitarbeiter.xlsx").write_text("fake")
    (original_dir / "Planstellen.XLSX").write_text("fake")

    status = SourceService.derive_group_status(
        "Mitarbeiterinformationen", 
        {}, 
        str(original_dir),
        "nonexistent_cluster_dir"
    )
    assert status.origin == DataSourceOrigin.ORIGINAL
    assert status.is_complete is False
    assert status.completeness_label == "teilweise vorhanden"

def test_cluster_logic(tmp_path):
    status = SourceService.derive_group_status(
        "Clusterinformationen", 
        {}, 
        "nonexistent_dir",
        "nonexistent_cluster_dir",
        active_cluster_source=_build_active_source(
            "input_folder",
            "input_folder.external_file",
            "Input-Ordner",
        ),
    )
    assert status.origin == DataSourceOrigin.ORIGINAL
    assert status.is_complete is True

def test_priority_upload_over_original(tmp_path):
    # Setup: Both upload and original file exist
    original_dir = tmp_path / "Original-Daten"
    original_dir.mkdir()
    (original_dir / "TVÖD.xlsx").write_text("original content")
    
    uploads = {"TVÖD": b"uploaded content"}
    
    status = SourceService.derive_group_status(
        "Entgeltinformationen", 
        uploads, 
        str(original_dir),
        "nonexistent_cluster_dir"
    )
    
# Uploaded must have priority
    assert status.origin == DataSourceOrigin.UPLOADED
    assert status.is_complete is True

def test_cluster_upload_priority(tmp_path):
    status = SourceService.derive_group_status(
        "Clusterinformationen", 
        {}, 
        "nonexistent_dir",
        "nonexistent_cluster_dir",
        active_cluster_source=_build_active_source(
            "ui_upload",
            "ui_upload.persisted_local_copy",
            "UI-Upload (persistiert)",
        ),
    )
    
    assert status.origin == DataSourceOrigin.UPLOADED


def _build_active_source(mode: str, subtype: str, display_label: str, status: str = "active") -> ActiveClusterSource:
    return ActiveClusterSource(
        mode=mode,
        subtype=subtype,
        status=status,
        is_active=True,
        is_valid=True,
        priority_rank=1,
        display_label=display_label,
        description=display_label,
        source_path=None,
        session_key=None,
        persisted_local_path=None,
        filename=None,
        file_exists=False,
        content_hash="hash",
        source_signature="sig",
        activated_at=None,
        last_modified_at=None,
        oe_mapping_count=1,
        jf_mapping_count=1,
        resolution_reason="test",
        validation_errors=[],
        fallback_from=None,
        debug_meta={},
    )


def test_cluster_group_status_uses_resolver_result_for_persisted_upload():
    status = SourceService.derive_group_status(
        "Clusterinformationen",
        {},
        "nonexistent_dir",
        "nonexistent_cluster_dir",
        active_cluster_source=_build_active_source(
            "ui_upload",
            "ui_upload.persisted_local_copy",
            "UI-Upload (persistiert)",
        ),
    )

    assert status.origin == DataSourceOrigin.UPLOADED
    assert status.is_complete is True
    assert status.completeness_label == "aktiv"


def test_cluster_group_status_uses_resolver_result_for_input_folder():
    status = SourceService.derive_group_status(
        "Clusterinformationen",
        {},
        "nonexistent_dir",
        "nonexistent_cluster_dir",
        active_cluster_source=_build_active_source(
            "input_folder",
            "input_folder.external_file",
            "Input-Ordner",
        ),
    )

    assert status.origin == DataSourceOrigin.ORIGINAL
    assert status.is_complete is True
    assert status.completeness_label == "aktiv"


def test_cluster_group_status_uses_resolver_result_for_synthetic_fallback():
    status = SourceService.derive_group_status(
        "Clusterinformationen",
        {},
        "nonexistent_dir",
        "nonexistent_cluster_dir",
        active_cluster_source=_build_active_source(
            "synthetic",
            "synthetic.default_fallback",
            "Synthetisch / Fallback",
            status="fallback",
        ),
    )

    assert status.origin == DataSourceOrigin.SYNTHETIC
    assert status.is_complete is True
    assert status.completeness_label == "aktiv"
