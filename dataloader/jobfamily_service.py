import pandas as pd
import os
from typing import Dict, List, Set, Optional

# Re-use existing cluster manager logic
try:
    from .cluster_manager import load_cluster_mappings
except ImportError:
    # Fallback for unit tests outside package
    def load_cluster_mappings(): return {}, {}

class JobFamilyService:
    """
    Single Source of Truth for Job Families.
    Provides logic for extraction, selection sanitization and cross-page state.
    """
    
    @staticmethod
    def get_active_jobfamilies(df_ma: Optional[pd.DataFrame] = None) -> List[str]:
        """
        Returns the currently valid Job Families.
        Priority:
        1. Explicitly mapped Job Families from Cluster Upload.
        2. Job Families present in the current Mitarbeiter data (if provided).
        3. Default / Fallback.
        """
        # 1. Try Cluster Mapping
        _, jf_map = load_cluster_mappings()
        
        if jf_map:
            # jf_map can be {Pos: Cluster} or {(Org, Pos): Cluster}
            clusters = set()
            for val in jf_map.values():
                if pd.notna(val) and str(val).strip() != "":
                    clusters.add(str(val).strip())
            
            if clusters:
                return sorted(list(clusters))
                
        # 2. Try df_ma if clusters missing
        if df_ma is not None and "Jobfamily" in df_ma.columns:
            jfs = set(df_ma["Jobfamily"].dropna().unique())
            return sorted([str(jf).strip() for jf in jfs if str(jf).strip() != ""])
            
        # 3. Fallback
        return ["Alternativlos", "Vertrieb", "Produktion", "Verwaltung"]

    @staticmethod
    def sanitize_selection(current_selection: List[str], valid_families: List[str]) -> List[str]:
        """
        Removes items from selection that are no longer in the valid list.
        """
        if not current_selection:
            return []
        
        valid_set = set(valid_families)
        return [item for item in current_selection if item in valid_set]

    @staticmethod
    def sanitize_dict_config(config: Dict[str, List[int]], valid_families: List[str]) -> Dict[str, List[int]]:
        """
        Cleans up structures like {"JF_A": [2024, 2025]}
        """
        valid_set = set(valid_families)
        return {jf: years for jf, years in config.items() if jf in valid_set}

    @staticmethod
    def get_available_years(start_year: int, horizon_years: int = 10) -> List[int]:
        """
        Returns list of selectable years for forecast adjustments.
        """
        return list(range(start_year, start_year + horizon_years + 1))
