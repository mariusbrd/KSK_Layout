import pytest
import os
from dataloader.source_service import SourceService, DataSourceOrigin

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
    # Cluster files are often in Cluster-Daten
    cluster_dir = tmp_path / "Cluster-Daten"
    cluster_dir.mkdir()
    (cluster_dir / "OE_Cluster.xlsx").write_text("fake")

    status = SourceService.derive_group_status(
        "Clusterinformationen", 
        {}, 
        "nonexistent_dir",
        str(cluster_dir)
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
    # Setup: External file exists (Standard/Original)
    cluster_dir = tmp_path / "Cluster-Daten"
    cluster_dir.mkdir()
    (cluster_dir / "OE_Cluster.xlsx").write_text("external content")
    
    # Simulating a session upload
    uploads = {"Cluster": b"uploaded content"}
    
    status = SourceService.derive_group_status(
        "Clusterinformationen", 
        uploads, 
        "nonexistent_dir",
        str(cluster_dir)
    )
    
    # Erwartung: Uploaded hat Vorrang (Green) vor Original (Yellow)
    assert status.origin == DataSourceOrigin.UPLOADED
