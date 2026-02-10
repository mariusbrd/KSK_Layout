"""
Parameter helpers for Abgaenge forecast.
"""

from typing import Dict, Any
import json


def default_params() -> Dict[str, Any]:
    return {
        "components": {
            "atz": True,
            "retirement": True,
            "quit": True,
            "ruhend": True,
        },
        "atz": {
            "new_atz_rate": 0.05, # was new_atz_cases_per_year
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,  # F02: Upper bound for ATZ eligibility
            "atz_duration_ar_years": 2.5,
            "atz_duration_fr_years": 2.5,
        },
        "retirement": {
            "rent_rate_65": 0.90,
            "rent_rate_60_65": 0.10,
        },
        "quit": {
            "quit_rate_base": 0.05,
            "use_quit_matrix": True,
            # New Matrix params
            "quit_dimension": "JobFamily",  # or "OrgUnit"
            "quit_matrix": {
                # Default Matrix: Age Group x JobFamily (will be dynamic in UI)
                # But we provide a minimal structure here to avoid errors
                "alter_unter_30": {"Default": 0.12},
                "alter_30_45": {"Default": 0.08},
                "alter_45_55": {"Default": 0.05},
                "alter_55_plus": {"Default": 0.02},
            },
        },
        "ruhend": {
            "ruhend_new_cases_per_year": 0,
            "ruhend_return_rate": 0.95,
            "ruhend_avg_duration_months": 12,
        },
        "random_seed": 42,
    }


def build_params_from_ui(ui_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge UI state into default params.
    """
    params = default_params()

    for key, value in ui_state.items():
        if key in params:
            if isinstance(params[key], dict) and isinstance(value, dict):
                params[key].update(value)
            else:
                params[key] = value

    # Optional quit matrix JSON from UI
    # Quit Matrix update
    if "quit_matrix" in ui_state:
        params["quit"]["quit_matrix"] = ui_state["quit_matrix"]
        params["quit"]["use_quit_matrix"] = True
    
    if "quit_dimension" in ui_state:
        params["quit"]["quit_dimension"] = ui_state["quit_dimension"]

    return params
