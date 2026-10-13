"""
Parameter helpers for Abgaenge forecast.
"""

from typing import Dict, Any
import json
import math


def get_retirement_ages(retirement: Dict[str, Any]) -> tuple[int, int]:
    """Resolve backwards-compatible age limits and reject ambiguous intervals."""
    ages = []
    for key, fallback in (("retirement_age", 65), ("early_retirement_age", 60)):
        raw = retirement.get(key, fallback)
        try:
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or not value.is_integer() or not 1 <= value <= 120:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Rentenalter müssen ganze Jahre zwischen 1 und 120 sein.") from None
        ages.append(int(value))
    regular, early = ages
    if early >= regular:
        raise ValueError("Frühverrentung muss vor dem regulären Renteneintritt beginnen.")
    return regular, early


def default_params() -> Dict[str, Any]:
    return {
        "components": {
            "atz": True,
            "retirement": True,
            "quit": True,
            "ruhend": True,
        },
        "atz": {
            "new_atz_rate": 0.05,
            "use_atz_matrix": False,
            "atz_dimension": "JobFamily",
            "atz_matrix": {
                "Default": 0.05,
            },
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 2.5,
            "atz_duration_fr_years": 2.5,
        },
        "retirement": {
            "rent_rate_65": 0.90,
            "rent_rate_60_65": 0.10,
            "retirement_age": 65,
            "early_retirement_age": 60,
        },
        "quit": {
            "quit_rate_base": 0.05,
            "use_quit_matrix": True,
            "quit_dimension": "JobFamily",
            "quit_matrix": {
                "alter_unter_30": {"Default": 0.12},
                "alter_30_45": {"Default": 0.08},
                "alter_45_55": {"Default": 0.05},
                "alter_55_plus": {"Default": 0.02},
            },
            "quit_adjustments": {
                "more": {}, # Format: {"JF_Name": [2024, 2025]}
                "less": {}, # Format: {"JF_Name": [2026]}
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
    """Merge UI state into default params.

    Matrices are stored in **percent scale** (0–100) in the UI / session
    state.  This function converts them to the **weight scale** (0–1)
    expected by the forecast engine.
    """
    from utils.matrix_helpers import percent_to_weights, migrate_to_percent

    params = default_params()

    for key, value in ui_state.items():
        if key in params:
            if isinstance(params[key], dict) and isinstance(value, dict):
                params[key].update(value)
            else:
                params[key] = value

    # ── Convert percent → weights for forecast engine ──
    atz_mat = params.get("atz", {}).get("atz_matrix")
    if atz_mat:
        atz_mat = migrate_to_percent(atz_mat)          # auto-upgrade old 0–1
        params["atz"]["atz_matrix"] = percent_to_weights(atz_mat)

    quit_mat = params.get("quit", {}).get("quit_matrix")
    # Explicit UI percentages also disambiguate matrices containing only 0–1 %.
    # Remove the UI marker before passing parameters to the unchanged engine.
    quit_scale = params.get("quit", {}).pop("_matrix_scale", None)
    if quit_mat:
        if quit_scale != "percent":
            quit_mat = migrate_to_percent(quit_mat)    # unchanged legacy auto-upgrade
        params["quit"]["quit_matrix"] = percent_to_weights(quit_mat)

    get_retirement_ages(params["retirement"])
    return params
