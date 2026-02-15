import pytest
import pandas as pd
from dataloader.cluster_manager import enrich_jf_clusters

def test_enrich_jf_clusters_sonstige_fallback():
    """Test 1: Azubi Ausbildung 'Sonstige' -> Cluster 'Sonstiges'"""
    df = pd.DataFrame({"Jobfamily": ["Sonstige", "sonstige", " Sonstige "]})
    # Empty normal maps
    res = enrich_jf_clusters(df, {}, {})
    
    assert all(res == "Sonstiges")

def test_enrich_jf_clusters_trainee_fallback():
    """Test 2: Trainee 'Trainee' -> Cluster 'Sonstiges'"""
    df = pd.DataFrame({"Jobfamily": ["Trainee", " trainee "]})
    res = enrich_jf_clusters(df, {}, {})
    
    assert all(res == "Sonstiges")

def test_enrich_jf_clusters_regression_preserves_valid():
    """Test 3: Normal mapping must NOT be overwritten by fallback."""
    df = pd.DataFrame({"Jobfamily": ["IT & Digitalisierung"]})
    jf_map = {"it & digitalisierung": "IT-Cluster"}
    
    res = enrich_jf_clusters(df, jf_map, {})
    
    assert res.iloc[0] == "IT-Cluster"

def test_enrich_jf_clusters_robustness():
    """Test 4: Robustness against case and whitespace."""
    df = pd.DataFrame({"Jobfamily": ["  SONSTIGE  ", "tRaInEe"]})
    res = enrich_jf_clusters(df, {}, {})
    
    assert all(res == "Sonstiges")

def test_enrich_jf_clusters_snapshot_inheritance():
    """Verify that snapshot-based mapping (inheritance) works."""
    df = pd.DataFrame({"Jobfamily": ["Specialist"]})
    snapshot_map = {"Specialist": "Special-Cluster"}
    
    res = enrich_jf_clusters(df, {}, snapshot_map)
    
    assert res.iloc[0] == "Special-Cluster"
