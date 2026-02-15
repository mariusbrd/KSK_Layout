"""
Parameter definition for Zugaenge (New Hires) forecast.
"""

from typing import Dict, Any, List

def default_params() -> Dict[str, Any]:
    return {
        "azubi": {
            "retention_rate": 1.0,  # 100% takeover
            "new_cases_per_year": 15, # Default new Azubis per year
            "duration_years": 3.0,
            "strategy": "Random",  # "Random" or "OrgUnit"
            "target_org_unit": None, # If strategy is OrgUnit
            "entry_tariff_group": "E5",
            "entry_step": 1,
            "exclude_baseline_azubis": False, # id: 16 - Isolation Mode
            "azubi_mak_during_training": 0.0,
            "azubi_mak_after_takeover": 1.0,
            "azubi_conversion_month": 8,
            "azubi_conversion_day": 1,
            "use_takeover_matrix": False,
            "takeover_dimension": "JobFamily", # "JobFamily" or "OrgUnit"
            "takeover_matrix": {}, # {val: weight}
            "jf_to_cluster_map": {}, # For consistent cluster assignment in engine
        },
        "trainee": {
            "new_cases_per_year": 5,
            "duration_years": 1.5,
            "salary_group": "E13", # TVöD group
            "strategy": "Random",
            "target_org_unit": None,
        },
        "new_hires": {
            "count_per_year": 10,
            "strategy": "Fill Vacancies", # "Random", "OrgUnit", "Fill Vacancies"
            "target_org_unit": None,
        },
        "random_seed": 42,
    }

def get_strategies() -> List[str]:
    return ["Random", "OrgUnit", "Fill Vacancies"]
