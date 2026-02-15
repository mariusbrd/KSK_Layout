import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch
from dataloader.jobfamily_service import JobFamilyService
from abgaenge.forecast import _select_quit_prob

def test_jf_extraction_default():
    # Mock load_cluster_mappings to return empty to trigger fallback
    with patch("dataloader.jobfamily_service.load_cluster_mappings", return_value=({}, {})):
        jfs = JobFamilyService.get_active_jobfamilies(None)
        assert "Alternativlos" in jfs
        assert len(jfs) > 0

def test_jf_extraction_from_df():
    # Mock load_cluster_mappings to return empty to ensure df_ma is used
    with patch("dataloader.jobfamily_service.load_cluster_mappings", return_value=({}, {})):
        df = pd.DataFrame({"Jobfamily": ["JF1", "JF2", "JF1", np.nan, "  JF3  "]})
        jfs = JobFamilyService.get_active_jobfamilies(df)
        assert jfs == ["JF1", "JF2", "JF3"]

def test_sanitize_selection():
    valid = ["JF1", "JF2", "JF3"]
    selected = ["JF1", "JF_OLD", "JF3"]
    sanitized = JobFamilyService.sanitize_selection(selected, valid)
    assert sanitized == ["JF1", "JF3"]

def test_quit_prob_adjustments():
    # Test with use_quit_matrix=False (should apply to base)
    params_no_matrix = {
        "quit": {
            "quit_rate_base": 0.1,
            "use_quit_matrix": False,
            "quit_adjustments": {
                "more": {"JF1": [2025]},
                "less": {"JF1": [2026]}
            }
        }
    }
    
    row_jf1 = pd.Series({"Jobfamily": "JF1", "age": 40})
    
    # Year 2025 - JF1 should have 1.5x
    prob_2025 = _select_quit_prob(row_jf1, params_no_matrix, 2025)
    assert pytest.approx(prob_2025) == 0.15
    
    # Year 2026 - JF1 should have 0.5x
    prob_2026 = _select_quit_prob(row_jf1, params_no_matrix, 2026)
    assert pytest.approx(prob_2026) == 0.05
    
    # Test with Matrix (should apply to matrix value)
    params_with_matrix = {
        "quit": {
            "quit_rate_base": 0.1,
            "use_quit_matrix": True,
            "quit_matrix": {
                "alter_30_45": {"JF1": 0.2, "Default": 0.1}
            },
            "quit_dimension": "JobFamily",
            "quit_adjustments": {
                "more": {"JF1": [2025]},
            }
        }
    }
    # Age 40 falls in alter_30_45
    prob_mat = _select_quit_prob(row_jf1, params_with_matrix, 2025)
    # Matrix says 0.2, plus 1.5x adjustment = 0.3
    assert pytest.approx(prob_mat) == 0.3
