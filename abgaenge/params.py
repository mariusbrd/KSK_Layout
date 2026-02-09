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
            "new_atz_cases_per_year": 0,
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
            "quit_rate_matrix": {
                "alter_unter_30": {"tenure_unter_2": 0.12, "tenure_2_5": 0.08, "tenure_ueber_5": 0.05},
                "alter_30_45": {"tenure_unter_2": 0.08, "tenure_2_5": 0.05, "tenure_ueber_5": 0.03},
                "alter_ueber_45": {"tenure_unter_2": 0.05, "tenure_2_5": 0.03, "tenure_ueber_5": 0.02},
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
    if "quit_matrix_json" in ui_state:
        try:
            params["quit"]["quit_rate_matrix"] = json.loads(ui_state["quit_matrix_json"])
            params["quit"]["use_quit_matrix"] = True
        except Exception:
            # Keep defaults if parsing fails
            pass

    return params
