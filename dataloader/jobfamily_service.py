import pandas as pd
from typing import Dict, List, Optional

try:
    from .cluster_resolver import ClusterMappingBundle
except ImportError:  # pragma: no cover - fallback for unit tests outside package
    ClusterMappingBundle = object  # type: ignore[assignment]


JOBFAMILY_UNMAPPED = "UNMAPPED"
_LEGACY_UNMAPPED_VALUES = {
    "",
    "nan",
    "none",
    "(nicht zugeordnet)",
    "unmapped",
}


def normalize_jobfamily_value(value: object, default: str = JOBFAMILY_UNMAPPED) -> str:
    """Normalize legacy or empty Jobfamily values to one shared sentinel."""
    if pd.isna(value):
        return default

    text = str(value).strip()
    if not text:
        return default

    if text.casefold() in _LEGACY_UNMAPPED_VALUES:
        return default

    return text


def normalize_jobfamily_series(
    series: pd.Series,
    default: str = JOBFAMILY_UNMAPPED,
) -> pd.Series:
    """Return a normalized Jobfamily series with one canonical fallback value."""
    return series.apply(lambda value: normalize_jobfamily_value(value, default=default))


def normalize_jobfamily_column(
    df: pd.DataFrame,
    column: str = "Jobfamily",
    default: str = JOBFAMILY_UNMAPPED,
) -> pd.DataFrame:
    """Ensure a dataframe contains a normalized Jobfamily column."""
    out = df.copy()
    if column not in out.columns:
        out[column] = default
    else:
        out[column] = normalize_jobfamily_series(out[column], default=default)
    return out

class JobFamilyService:
    """
    Single Source of Truth for Job Families.
    Provides logic for extraction, selection sanitization and cross-page state.
    """
    
    @staticmethod
    def get_active_jobfamilies(
        df_ma: Optional[pd.DataFrame] = None,
        cluster_mapping_bundle: Optional[ClusterMappingBundle] = None,
    ) -> List[str]:
        """
        Returns the currently valid Job Families.
        Priority:
        1. Job Families present in the current Mitarbeiter data (if provided).
        2. Explicit Jobfamily keys from an already-loaded mapping bundle.
        3. Default / Fallback.
        """
        # 1. Prefer actual Jobfamily values from the current snapshot.
        if df_ma is not None and "Jobfamily" in df_ma.columns:
            normalized = normalize_jobfamily_series(df_ma["Jobfamily"])
            jfs = {
                jf for jf in normalized.dropna().astype(str).str.strip().unique()
                if jf and jf != JOBFAMILY_UNMAPPED
            }
            return sorted(jfs)

        # 2. Derive Jobfamily keys from explicit mappings where possible.
        jf_map = getattr(cluster_mapping_bundle, "jf_map", {}) or {}
        jf_keys = set()
        for key in jf_map.keys():
            if isinstance(key, tuple):
                continue
            key_str = str(key).strip()
            if key_str and key_str.lower() not in {"nan", "none"}:
                jf_keys.add(key_str)
        if jf_keys:
            return sorted(jf_keys)

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
