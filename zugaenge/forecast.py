"""
Forecast engine for Zugaenge (New Hires).
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from config.settings import EDUCATION_GROUPS
from dataloader.loader import normalize_education_value

# Re-use Abgaenge constants/logic where useful
# But clean separation is better.
# We expect df_ma to have columns: PersNr, Eintritt, TrfGr, Organisationseinheit, Jobfamily.

@dataclass
class PeriodInfo:
    start: pd.Timestamp
    end: pd.Timestamp
    label: str


GENDER_TEXT_MAP = {
    "m": "männlich",
    "w": "weiblich",
    "d": "divers",
}

AZUBI_TRAINING_EDUCATION = "derzeit Berufsausbildung"
DEFAULT_NEW_HIRE_EDUCATION = "kfm Berufsabschluss"
DEFAULT_TRAINEE_EDUCATION = "Bachelor FH"
DEFAULT_AZUBI_CONVERSION_EDUCATION = "Bankberufsabschluss"
INTERNAL_EDUCATION_COL = "_education_normalized_internal"
INTERNAL_AZUBI_FLAG_COL = "_is_azubi_internal"


def _normalized_gender_series(df: pd.DataFrame) -> pd.Series:
    if "Geschlecht" not in df.columns or df.empty:
        return pd.Series(dtype="object")
    genders = df["Geschlecht"].astype(str).str.strip().str.lower()
    return genders[genders.isin(["m", "w", "d"])]


def _build_gender_distribution(
    df: pd.DataFrame,
    normalized_genders: Optional[pd.Series] = None,
) -> tuple[list[str], list[float]]:
    """
    Derive a stable sampling distribution for forecast hires from the current
    state. Falls back to a 50/50 split if no usable gender data exists.
    """
    if normalized_genders is not None:
        genders = normalized_genders
    else:
        genders = _normalized_gender_series(df)

    if genders.empty:
        return ["m", "w"], [0.5, 0.5]

    counts = genders.value_counts(normalize=True)
    return counts.index.tolist(), counts.values.astype(float).tolist()


def _sample_gender(
    rng: np.random.Generator,
    gender_choices: list[str],
    gender_weights: list[float],
) -> tuple[str, str]:
    if not gender_choices or not gender_weights:
        code = "m" if float(rng.random()) < 0.5 else "w"
    else:
        code = str(rng.choice(gender_choices, p=gender_weights))
    return code, GENDER_TEXT_MAP.get(code, code)


def _build_education_distribution(
    df: pd.DataFrame,
    *,
    allowed_values: Optional[List[str]] = None,
    excluded_values: Optional[List[str]] = None,
    fallback_choices: Optional[List[str]] = None,
) -> tuple[list[str], list[float]]:
    """
    Derive a stable education sampling distribution from the current state.
    Only canonical values from settings.py are retained.
    """
    fallback = [normalize_education_value(v) for v in (fallback_choices or [DEFAULT_NEW_HIRE_EDUCATION])]
    fallback = [str(v) for v in fallback if pd.notna(v)]
    if not fallback:
        fallback = [DEFAULT_NEW_HIRE_EDUCATION]

    if "Ausbildung" not in df.columns or df.empty:
        return fallback, [1.0 / len(fallback)] * len(fallback)

    if INTERNAL_EDUCATION_COL in df.columns:
        education = df[INTERNAL_EDUCATION_COL]
    else:
        education = df["Ausbildung"].map(normalize_education_value)
    education = education.dropna().astype(str).str.strip()
    education = education[education.isin(EDUCATION_GROUPS)]

    if education.empty:
        return fallback, [1.0 / len(fallback)] * len(fallback)

    if allowed_values:
        allowed = {
            str(normalized)
            for v in allowed_values
            for normalized in [normalize_education_value(v)]
            if pd.notna(normalized)
        }
        education = education[education.isin(allowed)]
    if excluded_values:
        excluded = {
            str(normalized)
            for v in excluded_values
            for normalized in [normalize_education_value(v)]
            if pd.notna(normalized)
        }
        education = education[~education.isin(excluded)]

    if education.empty:
        return fallback, [1.0 / len(fallback)] * len(fallback)

    counts = education.value_counts(normalize=True)
    return counts.index.tolist(), counts.values.astype(float).tolist()


def _sample_education(
    rng: np.random.Generator,
    education_choices: list[str],
    education_weights: list[float],
    fallback: str,
) -> str:
    canonical_fallback = normalize_education_value(fallback)
    if not education_choices or not education_weights:
        return str(canonical_fallback if pd.notna(canonical_fallback) else DEFAULT_NEW_HIRE_EDUCATION)

    choice = rng.choice(education_choices, p=education_weights)
    choice = normalize_education_value(choice)
    return str(choice if pd.notna(choice) else canonical_fallback)


def _build_internal_education_series(df: pd.DataFrame) -> pd.Series:
    if "Ausbildung" not in df.columns or df.empty:
        return pd.Series(index=df.index, dtype="object")
    return df["Ausbildung"].map(normalize_education_value)


def _build_internal_azubi_series(df: pd.DataFrame) -> pd.Series:
    if df.empty or "TrfGr" not in df.columns or "Jobfamily" not in df.columns:
        return pd.Series(index=df.index, dtype="bool")
    return (
        df["TrfGr"].astype(str).str.contains("TVA", na=False, case=False)
        | df["Jobfamily"].astype(str).str.contains("Azubi", na=False, case=False)
        | df["Jobfamily"].astype(str).str.contains("Ausbildung", na=False, case=False)
    )

def _next_august_conversion_date(entry_date: pd.Timestamp, conversion_month: int = 8, conversion_day: int = 1) -> pd.Timestamp:
    """
    Calculates the detailed graduation/conversion date based on the August rule.
    Rule: 
    - If entry is on or before conversion_day.conversion_month of the same year -> convert same year.
    - Else -> convert next year.
    
    NOTE: This effectively synchronizes all Azubis to the next available cycle.
    """
    if pd.isna(entry_date):
        return pd.NaT
        
    year = entry_date.year
    cutoff = pd.Timestamp(year, conversion_month, conversion_day)
    
    if entry_date <= cutoff:
        return cutoff
    else:
        return pd.Timestamp(year + 1, conversion_month, conversion_day)

def _estimate_baseline_graduation_date(
    entry_date: pd.Timestamp,
    duration_years: float,
    conversion_month: int = 8,
    conversion_day: int = 1,
    graduation_mode: str = "next_cycle",
    nearest_cycle_grace_days: Optional[int] = None,
) -> pd.Timestamp:
    """
    Calculates estimated graduation for Azubis.

    graduation_mode:
      "next_cycle"    (default, backward-compatible): snap to same-year cycle
                      if estimated_end <= cycle date, else NEXT year. Can add
                      up to ~11 months for late-year entrants. Strictly respects
                      minimum training duration (I10).
      "nearest_cycle": snap to the NEAREST cycle date (before or after
                      estimated_end). Reduces systematic bias for post-August
                      entrants (e.g. Sept entry + 3y -> Aug same year, not +1).
                      Trade-off: may graduate a few days before estimated_end
                      when the nearest August is just before the training end.

    nearest_cycle_grace_days:
      Only used with "nearest_cycle". Controls max days of early graduation:
        None (default): no constraint — full nearest-cycle bias reduction.
        0:  strict I10 — no early graduation; if august_same < estimated_end,
            force to august_next (equivalent to next_cycle for late entries).
        N>0: allow backward snap only when gap <= N days.
    """
    if pd.isna(entry_date):
        return pd.NaT

    years = int(duration_years)
    months = int((duration_years % 1) * 12)
    estimated_end = entry_date + pd.DateOffset(years=years, months=months)

    august_same = pd.Timestamp(estimated_end.year, conversion_month, conversion_day)
    august_next = pd.Timestamp(estimated_end.year + 1, conversion_month, conversion_day)

    if graduation_mode == "nearest_cycle":
        if estimated_end <= august_same:
            return august_same
        dist_past = (estimated_end - august_same).days
        # Apply grace constraint if set
        if nearest_cycle_grace_days is not None and dist_past > nearest_cycle_grace_days:
            return august_next
        dist_future = (august_next - estimated_end).days
        return august_same if dist_past <= dist_future else august_next
    else:  # "next_cycle"
        if estimated_end <= august_same:
            return august_same
        return august_next

def _annual_to_period_count(annual_count: float, period_days: float, rng: np.random.RandomState, debt: float = 0.0) -> (int, float):
    """
    Returns (num_cases, new_debt).
    Uses a deterministic-preferred rounding to hit target annual counts.
    """
    expected = annual_count * (period_days / 365.25) + debt
    count = int(expected)
    new_debt = expected - count
    return count, new_debt

def _get_random_org_unit(all_org_units: List[str], rng: np.random.RandomState) -> str:
    if not all_org_units:
        return "Unbekannt"
    return str(rng.choice(all_org_units))

def _resolve_org_unit(
    strategy: str,
    target_unit: Optional[str],
    all_org_units: List[str],
    vacancies: List[Dict[str, Any]],
    rng: np.random.RandomState,
    valid_units_with_cluster: Optional[List[str]] = None
) -> str:
    """
    Determines the target OrgUnit based on strategy.
    Strategies: "Random", "OrgUnit", "Fill Vacancies".
    
    If valid_units_with_cluster is provided, 'Random' strategy restricts to these units.
    """
    if strategy == "OrgUnit" and target_unit:
        return target_unit
    
    if strategy == "Fill Vacancies" and vacancies:
        # Take the first vacancy (FIFO)
        # We assume the caller handles removing it from the list if 'filled'
        # But here we just inspect.
        pass # Caller logic needed to consume vacancy.
        
    # Pool to choose from
    pool = valid_units_with_cluster if valid_units_with_cluster else all_org_units
    
    return _get_random_org_unit(pool, rng)

def resolve_distribution(
    rng: np.random.RandomState,
    matrix: Dict[str, float],
    dimension: str,
    valid_vals: List[str],
    default_val: str = "Unbekannt"
) -> str:
    """
    Resolves a target value based on a distribution matrix.
    
    Args:
        rng: Random state
        matrix: Dictionary mapping dimension values (or "Default") to weights/probabilities
        dimension: Name of the dimension (used for logging or fallback)
        valid_vals: List of allowed values
        default_val: Fallback if everything else fails
        
    Returns:
        Selected value
    """
    if not matrix:
        return default_val
        
    # 1. Filter matrix for valid values and non-zero weights
    choices = []
    weights = []
    
    # We include valid values that are in the matrix
    for val in valid_vals:
        weight = matrix.get(str(val))
        if weight is None:
            weight = matrix.get("Default", 0.0)
        
        if weight > 0:
            choices.append(str(val))
            weights.append(float(weight))
            
    # 2. If no valid choices found in matrix, use Default item from matrix if it has weight
    if not choices:
        default_weight = float(matrix.get("Default", 0.0))
        if default_weight > 0 and valid_vals:
            # Distribute Default weight across all valid values? 
            # Or just pick one? Pick a random one from valid pool if Default is specified.
            return str(rng.choice(valid_vals))
        return default_val
        
    # 3. Normalize weights
    total_w = sum(weights)
    if total_w <= 0:
        return default_val
        
    norm_weights = [w / total_w for w in weights]
    
    # 4. Sample
    return str(rng.choice(choices, p=norm_weights))

def distribute_deterministic(
    total_count: int,
    weights_dict: Dict[str, float],
    valid_vals: List[str] = None
) -> List[str]:
    """
    Distributes an integer total_count across categories defined in weights_dict
    using a stable rounding strategy (Largest Remainder Method).
    
    Args:
        total_count: Total number of items to distribute
        weights_dict: Mapping of category -> weight/share
        valid_vals: Optional list of allowed categories. If provided, weights_dict 
                   is filtered to these.
                   
    Returns:
        A list of category strings of length total_count.
    """
    if total_count <= 0:
        return []
        
    # 1. Prepare and normalize weights
    filtered_weights = {}
    if weights_dict:
        for k, v in weights_dict.items():
            if v > 0 and (valid_vals is None or k in valid_vals):
                filtered_weights[k] = float(v)
                
    if not filtered_weights:
        # Fallback: if no weights, return first valid value or "Unbekannt"
        fallback = valid_vals[0] if valid_vals else "Unbekannt"
        return [fallback] * total_count
        
    total_weight = sum(filtered_weights.values())
    shares = {k: v / total_weight for k, v in filtered_weights.items()}
    
    # 2. Initial allocation (floor)
    allocations = {}
    remainders = []
    
    current_sum = 0
    for k, share in shares.items():
        val = total_count * share
        count = int(np.floor(val))
        allocations[k] = count
        current_sum += count
        remainders.append((k, val - count))
        
    # 3. Distribute remainders
    # Sort by remainder descending, then by key ascending (for stable tie-breaking)
    remainders.sort(key=lambda x: (-x[1], x[0]))
    
    diff = total_count - current_sum
    for i in range(diff):
        k, _ = remainders[i % len(remainders)]
        allocations[k] += 1
        
    # 4. Flatten to list
    result = []
    # Sort keys for determinism
    for k in sorted(allocations.keys()):
        result.extend([k] * allocations[k])
        
    return result

def _get_unit_to_cluster_map(df: pd.DataFrame) -> Dict[str, str]:
    """Helper to create a mapping from Organisationseinheit to OE-Cluster."""
    if "Organisationseinheit" not in df.columns or "OE-Cluster" not in df.columns:
        return {}
    # Drop duplicates to speed up lookup, keep last (or first)
    # Ensure we drop NaNs in OE-Cluster so we don't map to NaN
    valid_map = df.dropna(subset=["OE-Cluster"])[["Organisationseinheit", "OE-Cluster"]]
    return valid_map.set_index("Organisationseinheit")["OE-Cluster"].to_dict()

def _simulate_azubis(
    df_state: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
    events: List[Dict[str, Any]],
    all_org_units: List[str],
    debts: Dict[str, float] = None
):
    """
    Handle takeover of existing Azubis.
    """
    azubi_params = params.get("azubi", {})
    retention_rate = float(azubi_params.get("retention_rate", 0.8))
    duration_years = float(azubi_params.get("duration_years", 3.0))
    strategy = azubi_params.get("strategy", "Random")
    target_unit = azubi_params.get("target_org_unit", None)
    entry_tariff = azubi_params.get("entry_tariff_group", "E5")
    entry_step = int(azubi_params.get("entry_step", 1))
    
    # MAK Logic Parameters
    mak_after = float(azubi_params.get("azubi_mak_after_takeover", 1.0))
    conv_month = int(azubi_params.get("azubi_conversion_month", 8))
    conv_day = int(azubi_params.get("azubi_conversion_day", 1))
    graduation_mode = azubi_params.get("graduation_mode", "next_cycle")
    _raw_grace = azubi_params.get("nearest_cycle_grace_days", None)
    grace_days = int(_raw_grace) if _raw_grace is not None else None

    # Identify Azubis
    if INTERNAL_AZUBI_FLAG_COL in df_state.columns:
        mask_azubi = df_state[INTERNAL_AZUBI_FLAG_COL].eq(True)
    else:
        mask_azubi = _build_internal_azubi_series(df_state)

    # id: 63 - Label training phase as "Sonstige" for charts
    training_label = "Sonstige"
    if "Jobfamily" not in df_state.columns:
        df_state["Jobfamily"] = "Unbekannt"

    # Fix 4: Save original Jobfamily once before overwriting (audit/export only)
    if "Jobfamily_pre_azubi" not in df_state.columns:
        df_state["Jobfamily_pre_azubi"] = pd.NA
    first_time_mask = mask_azubi & df_state["Jobfamily_pre_azubi"].isna()
    if first_time_mask.any():
        df_state.loc[first_time_mask, "Jobfamily_pre_azubi"] = df_state.loc[first_time_mask, "Jobfamily"]

    df_state.loc[mask_azubi, "Jobfamily"] = training_label

    current_azubis = df_state[
        (df_state["active"] == True) & 
        (df_state["Jobfamily"] == training_label) &
        mask_azubi # Ensure we only pick the ones identified as Azubis
    ]
    if current_azubis.empty:
        return

    # Support for Isolated Forecast (id: 16)
    exclude_baseline = params.get("azubi", {}).get("exclude_baseline_azubis", False)

    # --- Phase 1: Identify Graduates & Make Retention Decisions ---
    retained_info = [] # List of (persnr, row, graduation_date, is_forecast)
    exited_info = []   # List of (persnr, row, graduation_date, is_forecast)
    
    for persnr, row in current_azubis.iterrows():
        is_f = row.get("is_forecast", False)
        is_forecast = False if pd.isna(is_f) else bool(is_f)

        if exclude_baseline and not is_forecast:
            continue

        eintritt = pd.to_datetime(row.get("Eintritt", pd.NaT))
        graduation_date = row.get("GraduationDate", pd.NaT)

        if pd.isna(graduation_date) and pd.notna(eintritt):
             graduation_date = _estimate_baseline_graduation_date(eintritt, duration_years, conv_month, conv_day, graduation_mode, grace_days)

        if pd.isna(graduation_date):
            continue

        if period.start <= graduation_date <= period.end:
            fixed_destiny = row.get("GraduationModus", None)

            if pd.notna(fixed_destiny):
                should_retain = (fixed_destiny == "Takeover")
            else:
                success_chance = retention_rate
                if debts is not None:
                    expected_success = success_chance + debts.get("takeover_baseline", 0.0)
                    takeover_count = int(expected_success)
                    debts["takeover_baseline"] = expected_success - takeover_count
                    should_retain = takeover_count >= 1
                else:
                    should_retain = rng.random() < success_chance

            if should_retain:
                retained_info.append((persnr, row, graduation_date, is_forecast))
            else:
                exited_info.append((persnr, row, graduation_date, is_forecast))

    # --- Phase 2: Distribute Dimension Values for Retained Azubis ---
    if retained_info:
        unit_to_cluster = _get_unit_to_cluster_map(df_state)
        conversion_edu_choices, conversion_edu_weights = _build_education_distribution(
            df_state,
            allowed_values=[
                "kfm Berufsabschluss",
                "Bankberufsabschluss",
                "Bankfachwirt",
                "Bankbetriebswirt",
            ],
            excluded_values=[AZUBI_TRAINING_EDUCATION],
            fallback_choices=[DEFAULT_AZUBI_CONVERSION_EDUCATION],
        )
        # Stable sort by PersNr to ensure deterministic assignment
        retained_info.sort(key=lambda x: str(x[0]))
        total_retained = len(retained_info)

        # Prepare defaults (inherit from current row)
        assigned_units = [row.get("Organisationseinheit") for _, row, _, _ in retained_info]
        assigned_jfs = ["Angestellte"] * total_retained
        assigned_clusters = [row.get("OE-Cluster", "Sonstiges") for _, row, _, _ in retained_info]

        # Track whether OrgUnit dimension was used (affects JF interpretation)
        _is_orgunit_mode = False

        if azubi_params.get("use_takeover_matrix", False):
            matrix = azubi_params.get("takeover_matrix", {})
            dim = azubi_params.get("takeover_dimension", "JobFamily")

            if dim == "OrgUnit":
                _is_orgunit_mode = True
                new_units = distribute_deterministic(total_retained, matrix)
                for i, unit in enumerate(new_units):
                    assigned_units[i] = unit
                    assigned_clusters[i] = unit_to_cluster.get(unit, "Sonstiges")
                    # OrgUnit mode: no JF distribution → explicit "Sonstige"
                    assigned_jfs[i] = "Sonstige"
            else: # JobFamily
                new_jfs = distribute_deterministic(total_retained, matrix)
                for i, jf in enumerate(new_jfs):
                    assigned_jfs[i] = jf

        # Phase 3: Update State & Create Events for Retained
        for i, (persnr, row, graduation_date, is_forecast) in enumerate(retained_info):
            new_unit = assigned_units[i]
            new_jf = assigned_jfs[i]
            new_cluster = assigned_clusters[i]
            current_education = normalize_education_value(row.get("Ausbildung", pd.NA))
            if pd.notna(current_education) and str(current_education) != AZUBI_TRAINING_EDUCATION:
                new_education = str(current_education)
            else:
                new_education = _sample_education(
                    rng,
                    conversion_edu_choices,
                    conversion_edu_weights,
                    DEFAULT_AZUBI_CONVERSION_EDUCATION,
                )

            # id: 60 - Derive JF-Cluster for Takeovers
            if _is_orgunit_mode:
                # OrgUnit mode: JF-Cluster is deliberately "Sonstiges" (business rule)
                new_jf_cluster = "Sonstiges"
            else:
                jf_to_cluster = azubi_params.get("jf_to_cluster_map", {})
                new_jf_cluster = jf_to_cluster.get(str(new_jf).strip(), "Sonstiges")

            eintritt = pd.to_datetime(row.get("Eintritt", pd.NaT))
            audit_fields = {
                "entry_date": eintritt,
                "graduation_date": graduation_date,
                "is_forecast": is_forecast,
                "source_pool": "Forecast" if is_forecast else "Baseline",
                "cohort": eintritt.year if pd.notna(eintritt) else "Unknown"
            }
            current_mak = float(row.get("mak", 0.0))

            # Update State
            df_state.loc[persnr, "Jobfamily"] = new_jf 
            df_state.loc[persnr, "Organisationseinheit"] = new_unit
            if "OE-Cluster" in df_state.columns:
                df_state.loc[persnr, "OE-Cluster"] = new_cluster
            if "JF-Cluster" in df_state.columns:
                df_state.loc[persnr, "JF-Cluster"] = new_jf_cluster
            
            df_state.loc[persnr, "TrfGr"] = entry_tariff
            df_state.loc[persnr, "St"] = entry_step
            df_state.loc[persnr, "mak"] = mak_after
            df_state.loc[persnr, "Ausbildung"] = new_education
            if INTERNAL_EDUCATION_COL in df_state.columns:
                df_state.loc[persnr, INTERNAL_EDUCATION_COL] = new_education
            if INTERNAL_AZUBI_FLAG_COL in df_state.columns:
                df_state.loc[persnr, INTERNAL_AZUBI_FLAG_COL] = False
            
            # Events
            events.append({
                "date": graduation_date,
                "type": "Azubi_Conversion_Out",
                "reason_label": "Azubi-Abschluss: Übernahme (Umwandlung)",
                "count": -1,
                "persnr": persnr,
                "org_unit": row.get("Organisationseinheit"),
                "source": "Azubi",
                "mak": -current_mak,
                "OE-Cluster": row.get("OE-Cluster", "Sonstiges"),
                "JF-Cluster": row.get("JF-Cluster", "Sonstiges"),
                "comment": "Statuswechsel: Azubi Ende",
                "is_internal_transition": True,
                **audit_fields
            })
            _conv_in_event = {
                "date": graduation_date,
                "type": "Azubi_Conversion_In",
                "reason_label": "Azubi-Abschluss: Übernahme (Umwandlung)",
                "count": 1,
                "persnr": persnr,
                "org_unit": new_unit,
                "source": "Regular",
                "mak": mak_after,
                "TrfGr": entry_tariff,
                "St": entry_step,
                "Jobfamily": new_jf,
                "Ausbildung": new_education,
                # Carry original JF (saved by Fix 4) for audit/export; enables tracing
                # which original Jobfamily each converted Azubi came from.
                "Jobfamily_pre_azubi": row.get("Jobfamily_pre_azubi"),
                "Planstelle": str(row.get("Planstelle", "Nachbesetzung")),
                "OE-Cluster": new_cluster,
                "JF-Cluster": new_jf_cluster,
                "comment": "Statuswechsel: Reguläre Übernahme",
                "is_internal_transition": True,
                **audit_fields
            }
            if _is_orgunit_mode:
                _conv_in_event["_jf_orgunit_mode"] = True
            events.append(_conv_in_event)

    # --- Phase 4: Handle Exits ---
    for info in exited_info:
        persnr, row, graduation_date, is_forecast = info
        eintritt = pd.to_datetime(row.get("Eintritt", pd.NaT))
        audit_fields = {
            "entry_date": eintritt,
            "graduation_date": graduation_date,
            "is_forecast": is_forecast,
            "source_pool": "Forecast" if is_forecast else "Baseline",
            "cohort": eintritt.year if pd.notna(eintritt) else "Unknown"
        }
        current_mak = float(row.get("mak", 0.0))
        
        df_state.loc[persnr, "active"] = False
        events.append({
            "date": graduation_date,
            "type": "Azubi_Exit",
            "reason_label": "Azubi-Abschluss: Nichtübernahme (Abgang)",
            "count": -1,
            "persnr": persnr,
            "org_unit": row.get("Organisationseinheit"),
            "mak": -current_mak,
            "OE-Cluster": row.get("OE-Cluster", "Sonstiges"),
            "source": "Azubi",
            **audit_fields
        })

def _simulate_new_azubis(
    df_state: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
    events: List[Dict[str, Any]],
    all_org_units: List[str],
    num_cases: int = 0,
    forecast_start_date: Optional[pd.Timestamp] = None,
    debts: Dict[str, float] = None,
    gender_distribution: Optional[tuple[list[str], list[float]]] = None,
    unit_to_cluster_map: Optional[Dict[str, str]] = None,
):
    """
    Simulate hiring of NEW Azubis (not just retention).
    Azubi_Hire represents a brand new entry into the apprenticeship.
    """
    if num_cases <= 0:
        return

    azubi_params = params.get("azubi", {})
    # Duration doesn't affect ENTRY, but retention logic needs it later.
    duration_years = float(azubi_params.get("duration_years", 3.0))
    retention_rate = float(azubi_params.get("retention_rate", 0.8))
    strategy = azubi_params.get("strategy", "Random")
    target_unit = azubi_params.get("target_org_unit", None)
    entry_tariff = "TVAöD" # Default for Azubis during apprenticeship
    
    # MAK Logic Parameters
    mak_during = float(azubi_params.get("azubi_mak_during_training", 0.0))
    conv_month = int(azubi_params.get("azubi_conversion_month", 8))
    conv_day = int(azubi_params.get("azubi_conversion_day", 1))
    graduation_mode = azubi_params.get("graduation_mode", "next_cycle")
    _raw_grace = azubi_params.get("nearest_cycle_grace_days", None)
    grace_days = int(_raw_grace) if _raw_grace is not None else None

    # Pre-compute cluster map for lookups
    unit_to_cluster = unit_to_cluster_map if unit_to_cluster_map is not None else _get_unit_to_cluster_map(df_state)
    valid_units = list(unit_to_cluster.keys()) if unit_to_cluster else all_org_units
    if gender_distribution is None:
        gender_choices, gender_weights = _build_gender_distribution(df_state)
    else:
        gender_choices, gender_weights = gender_distribution
    azubi_edu = AZUBI_TRAINING_EDUCATION
    
    # Effective start for random entry date (to avoid filter loss in first month)
    eff_start = period.start
    if forecast_start_date and forecast_start_date > period.start:
        eff_start = forecast_start_date
    
    eff_days = (period.end - eff_start).days
    if eff_days < 0: eff_days = 0

    for i in range(num_cases):
        # Random start date in allowed period segment
        entry_date = eff_start + pd.Timedelta(days=rng.integers(0, eff_days + 1))

        # Deterministic unique ID derived from rng (seed-reproducible, I9)
        unique_suffix = format(rng.integers(0, 0xFFFF), "04x")
        new_id = f"AZ_{entry_date.year}_N{i+1:02d}_{unique_suffix}"
        
        org_unit = _resolve_org_unit(strategy, target_unit, all_org_units, [], rng, valid_units)
        new_cluster = unit_to_cluster.get(org_unit, "Sonstiges")
        gender_code, gender_text = _sample_gender(rng, gender_choices, gender_weights)

        # Graduation calc (id: 16) - Use August Rule respecting Duration
        grad_date = _estimate_baseline_graduation_date(entry_date, duration_years, conv_month, conv_day, graduation_mode, grace_days)
        
        # id: 63 - Azubi Training -> "Sonstige" Job Family
        jf_to_cluster = azubi_params.get("jf_to_cluster_map", {})
        new_jf = "Sonstige"
        new_jf_cluster = jf_to_cluster.get(new_jf, "Sonstiges")

        # State Object
        new_row = {
            "PersNr": new_id,
            "active": True,
            "Jobfamily": new_jf,
            "TrfGr": entry_tariff,
            "TrfGrStart": entry_tariff, # Store for audit
            "St": 1,
            "Organisationseinheit": org_unit,
            "Eintritt": entry_date,
            "GraduationDate": grad_date, # id: 16
            "is_forecast": True,        # id: 16
            "GebDatum": entry_date - pd.DateOffset(years=16), # Young
            "Geschlecht": gender_code,
            "Text Gsch": gender_text,
            "Ausbildung": azubi_edu,
            INTERNAL_EDUCATION_COL: azubi_edu,
            INTERNAL_AZUBI_FLAG_COL: True,
            "Vertragsart": "Auszubildende",
            "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "Planstelle": "Azubi",
            "age": 16.0,
            "tenure": 0.0,
            "mak": mak_during, # Azubi counts as 0 MAK initially (per new Rule)
            "OE-Cluster": new_cluster, # Assigned Cluster
            "JF-Cluster": new_jf_cluster
        }
        
        # --- Prepare Future Graduation Destiny (Fix id: 13) ---
        # Uses isolated forecast debt pool so baseline Azubi decisions do not
        # interfere with the user-configured retention_rate for new hires.
        success_chance = retention_rate
        if debts is not None:
            expected_success = success_chance + debts.get("takeover_forecast", 0.0)
            takeover_count = int(expected_success)
            debts["takeover_forecast"] = expected_success - takeover_count
            should_retain = takeover_count >= 1
        else:
            should_retain = rng.random() < success_chance

        # Mark destiny in new_row so _simulate_azubis picks it up later
        new_row["GraduationModus"] = "Takeover" if should_retain else "Exit"
        
        # Design decision: Azubi_Hire carries mak=0.0 intentionally.
        # Headcount (+1) rises immediately at hire; capacity (MAK) only increases
        # at takeover (Azubi_Conversion_In, mak=mak_after). This means HC and MAK
        # diverge during training — do NOT use abs(count)==abs(mak) as a test invariant
        # for Azubi events. See tests F01-F05 for the correct assertions.
        events.append({
            "date": entry_date,
            "type": "Azubi_Hire",
            "reason_label": "Azubi Neueinstellung (externer Zugang)",
            "count": 1,
            "persnr": new_id,
            "org_unit": org_unit,
            "source": "Azubi (New)",
            "mak": mak_during, # 0.0 by design — capacity counted at takeover, not at hire
            "TrfGr": entry_tariff,
            "St": 1,
            "Jobfamily": new_jf,
            "Planstelle": "Azubi",
            "Geschlecht": gender_code,
            "Text Gsch": gender_text,
            "Ausbildung": azubi_edu,
            "OE-Cluster": new_cluster,
            "JF-Cluster": new_jf_cluster,
            "is_forecast": True,
            "source_pool": "Forecast",
            "entry_date": entry_date,
            "graduation_date": grad_date,
            "cohort": entry_date.year,
            "_new_row": new_row
        })

def _simulate_trainees(
    df_state: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
    events: List[Dict[str, Any]],
    all_org_units: List[str],
    num_cases: int = 0,
    forecast_start_date: Optional[pd.Timestamp] = None,
    gender_distribution: Optional[tuple[list[str], list[float]]] = None,
    education_distribution: Optional[tuple[list[str], list[float]]] = None,
):
    trainee_params = params.get("trainee", {})
    if num_cases <= 0:
        return
    salary_group = trainee_params.get("salary_group", "E13")
    strategy = trainee_params.get("strategy", "Random")
    target_unit = trainee_params.get("target_org_unit", None)
    if gender_distribution is None:
        gender_choices, gender_weights = _build_gender_distribution(df_state)
    else:
        gender_choices, gender_weights = gender_distribution
    if education_distribution is None:
        trainee_edu_choices, trainee_edu_weights = _build_education_distribution(
            df_state,
        allowed_values=[
            "Bachelor FH",
            "Bachelor Universität",
            "Master FH",
            "Studium Lehrinstitut",
            "Master Universität",
            "Bankbetriebswirt",
        ],
            excluded_values=[AZUBI_TRAINING_EDUCATION],
            fallback_choices=[DEFAULT_TRAINEE_EDUCATION],
        )
    else:
        trainee_edu_choices, trainee_edu_weights = education_distribution
    
    # Effective start for random entry date (to avoid filter loss in first month)
    eff_start = period.start
    if forecast_start_date and forecast_start_date > period.start:
        eff_start = forecast_start_date
    
    eff_days = (period.end - eff_start).days
    if eff_days < 0: eff_days = 0

    for _ in range(num_cases):
        # Generate new Trainee
        new_id = f"TR_{rng.integers(10000, 99999)}"
        # Check collision? Unlikely with prefix.
        
        org_unit = _resolve_org_unit(strategy, target_unit, all_org_units, [], rng)
        entry_date = eff_start + pd.Timedelta(days=rng.integers(0, eff_days + 1))
        gender_code, gender_text = _sample_gender(rng, gender_choices, gender_weights)
        education = _sample_education(
            rng,
            trainee_edu_choices,
            trainee_edu_weights,
            DEFAULT_TRAINEE_EDUCATION,
        )
        
        # Add to State
        # We assume df_state structure matches
        new_row = {
            "PersNr": new_id,
            "active": True,
            "Jobfamily": "Trainee",
            "TrfGr": salary_group,
            "St": 1,
            "Organisationseinheit": org_unit,
            "Eintritt": entry_date,
            "GebDatum": entry_date - pd.DateOffset(years=25), # Approx simple age
            "Geschlecht": gender_code,
            "Text Gsch": gender_text,
            "Ausbildung": education,
            INTERNAL_EDUCATION_COL: education,
            "Vertragsart": "Zeitvertrag",
            "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "Planstelle": "Trainee",
            "age": 25.0,
            "tenure": 0.0,
            "mak": 1.0 # Full FTE
        }
        
        events.append({
            "date": entry_date,
            "type": "Trainee_Hire",
            "reason_label": "Trainee Neueinstellung",
            "count": 1,
            "persnr": new_id,
            "org_unit": org_unit,
            "source": "Trainee",
            "mak": 1.0,
            "TrfGr": salary_group,
            "St": 1,
            "Jobfamily": "Trainee",
            "Planstelle": "Trainee",
            "Geschlecht": gender_code,
            "Text Gsch": gender_text,
            "Ausbildung": education,
            "_new_row": new_row # Marker to add to DF later
        })

def _simulate_hires(
    df_state: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
    events: List[Dict[str, Any]],
    all_org_units: List[str],
    vacancies: List[Dict[str, Any]],
    num_cases: int = 0,
    forecast_start_date: Optional[pd.Timestamp] = None,
    gender_distribution: Optional[tuple[list[str], list[float]]] = None,
    education_distribution: Optional[tuple[list[str], list[float]]] = None,
):
    hire_params = params.get("new_hires", {})
    if num_cases <= 0:
        return

    strategy = hire_params.get("strategy", "Fill Vacancies")
    target_unit = hire_params.get("target_org_unit", None)
    
    # Matrix Distribution Logic
    dist_list = hire_params.get("distribution", [])

    dist_choices = []
    dist_weights = []
    
    if dist_list:
        for d in dist_list:
            dist_choices.append(d)
            dist_weights.append(float(d.get("Share %", 0.0)))
        
        # Normalize weights
        total_w = sum(dist_weights)
        if total_w > 0:
            dist_weights = [w / total_w for w in dist_weights]
        else:
            dist_list = [] # Invalid distribution
    if gender_distribution is None:
        gender_choices, gender_weights = _build_gender_distribution(df_state)
    else:
        gender_choices, gender_weights = gender_distribution
    if education_distribution is None:
        hire_edu_choices, hire_edu_weights = _build_education_distribution(
            df_state,
            excluded_values=[AZUBI_TRAINING_EDUCATION],
            fallback_choices=[DEFAULT_NEW_HIRE_EDUCATION],
        )
    else:
        hire_edu_choices, hire_edu_weights = education_distribution
    
    # Effective start for random entry date (to avoid filter loss in first month)
    eff_start = period.start
    if forecast_start_date and forecast_start_date > period.start:
        eff_start = forecast_start_date
    
    eff_days = (period.end - eff_start).days
    if eff_days < 0: eff_days = 0

    for _ in range(num_cases):
        new_id = f"NH_{rng.integers(10000, 99999)}"
        entry_date = eff_start + pd.Timedelta(days=rng.integers(0, eff_days + 1))
        gender_code, gender_text = _sample_gender(rng, gender_choices, gender_weights)
        education = _sample_education(
            rng,
            hire_edu_choices,
            hire_edu_weights,
            DEFAULT_NEW_HIRE_EDUCATION,
        )
        
        # Determine Attributes
        org_unit = "Unbekannt"
        plan_stelle = "Nachbesetzung" # Default
        jf = "Angestellte"
        oe_c = "Sonstiges"
        
        is_replacement = False
        
        if strategy == "Fill Vacancies" and vacancies:
            # Find a matching vacancy (e.g. earliest date)
            valid_vacancies = [v for v in vacancies if v["date"] <= entry_date]
            if valid_vacancies:
                vacancy = valid_vacancies.pop(0) # Consume
                vacancies.remove(vacancy) # Remove from main list
                
                org_unit = vacancy.get("org_unit", "Unbekannt")
                plan_stelle = vacancy.get("planstelle", "Nachbesetzung")
                
                # Inherit Attributes from Leaver
                jf = vacancy.get("Jobfamily", "Angestellte")
                oe_c = vacancy.get("OE-Cluster", "Sonstiges")
                
                is_replacement = True
            else:
                # No vacancy available yet? Fallback to random/matrix
                org_unit = _get_random_org_unit(all_org_units, rng)
                plan_stelle = org_unit 
        else:
            org_unit = _resolve_org_unit(strategy, target_unit, all_org_units, [], rng)
            plan_stelle = org_unit 

        # If NOT a replacement (or replacement lacked info? No, we stick to leaver info if available),
        # Apply Matrix Distribution for JF/Cluster
        if not is_replacement:
            if dist_list:
                # Sample from Matrix
                idx = rng.choice(len(dist_choices), p=dist_weights)
                choice = dist_choices[idx]
                
                jf = choice.get("Jobfamily", "Angestellte")
                oe_c = choice.get("OE-Cluster", "Sonstiges")
            else:
                # Fallback if no matrix: Keep defaults ("Angestellte", "Unclustered")
                pass

        new_row = {
            "PersNr": new_id,
            "active": True,
            "Jobfamily": jf,
            "OE-Cluster": oe_c, 
            "TrfGr": "E9A", # Default
            "St": 3, # Experienced hire?
            "Organisationseinheit": org_unit,
            "Eintritt": entry_date,
            "GebDatum": entry_date - pd.DateOffset(years=30),
            "Geschlecht": gender_code,
            "Text Gsch": gender_text,
            "Ausbildung": education,
            INTERNAL_EDUCATION_COL: education,
            "Vertragsart": "Unbefristet",
            "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "Planstelle": plan_stelle,
            "age": 30.0,
            "tenure": 0.0,
            "mak": 1.0
        }
        
        events.append({
            "date": entry_date,
            "type": "New_Hire",
            "reason_label": "Basis-Einstellungen / Nachbesetzung",
            "count": 1,
            "persnr": new_id,
            "org_unit": org_unit,
            "source": "NewHire",
            "mak": 1.0,
            "TrfGr": "E9A",
            "St": 3,
            "Jobfamily": jf,
            "OE-Cluster": oe_c, 
            "Planstelle": plan_stelle,
            "Geschlecht": gender_code,
            "Text Gsch": gender_text,
            "Ausbildung": education,
            "_new_row": new_row # Marker to add to DF later
        })

def run_forecast_zugaenge(
    df_snapshot: pd.DataFrame,
    start_date: pd.Timestamp,
    periods_years: int = 3,
    end_date: Optional[pd.Timestamp] = None,
    freq: str = "M",
    params: Dict[str, Any] = None,
    vacancies: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    
    if params is None:
        from .params import default_params
        params = default_params()
        
    # RNG Isolation (Contract V6)
    base_seed = int(params.get("random_seed", 42))
    ss = np.random.SeedSequence(base_seed)
    rng = np.random.default_rng(ss.spawn(1)[0])
    
    # Prepare State
    df_state = df_snapshot.copy()
    if "active" not in df_state.columns:
        df_state["active"] = True # Assume snapshot is all active
    df_state[INTERNAL_EDUCATION_COL] = _build_internal_education_series(df_state)
    df_state[INTERNAL_AZUBI_FLAG_COL] = _build_internal_azubi_series(df_state)
        
    if not df_state.empty and "PersNr" in df_state.columns:
        df_state.set_index("PersNr", drop=False, inplace=True)
        df_state.sort_index(inplace=True)
        
    # Get all OrgUnits for random assignment
    all_org_units = []
    if "Organisationseinheit" in df_state.columns:
        all_org_units = df_state["Organisationseinheit"].dropna().unique().tolist()

    # Generate Periods (Monthly)
    if end_date is None:
        end_date = start_date + pd.DateOffset(years=periods_years)
    period_range = pd.period_range(start=start_date, end=end_date, freq="M")
    
    all_events = []
    
    # Mutable vacancy list
    current_vacancies = sorted(vacancies, key=lambda x: x["date"]) if vacancies else []

    # Debt tracking for deterministic counts.
    # takeover_baseline: retention decisions for existing (snapshot) Azubis without pre-assigned destiny.
    # takeover_forecast: destiny pre-assignment for newly hired forecast Azubis.
    # Keeping them separate ensures user-controlled retention_rate only drives forecast Azubis,
    # not baseline Azubis whose graduation timing is already determined by HR data.
    debts = {"azubi": 0.0, "trainee": 0.0, "new_hires": 0.0, "takeover_baseline": 0.0, "takeover_forecast": 0.0}

    for p in period_range:
        # Clip to effective start/end (pro-rata support)
        eff_p_start = max(p.start_time, start_date)
        eff_p_end = min(p.end_time, end_date)
        
        if eff_p_start > eff_p_end:
            continue # Outside effective range
            
        period = PeriodInfo(eff_p_start, eff_p_end, p.strftime("%Y-%m"))
        period_days = (period.end - period.start).days + 1
        period_events = []
        
        # Calculate counts deterministic
        n_az = 0
        if params.get("azubi", {}).get("active", True):
            n_az, debts["azubi"] = _annual_to_period_count(params.get("azubi", {}).get("new_cases_per_year", 0), period_days, rng, debts["azubi"])
        
        n_tr = 0
        if params.get("trainee", {}).get("active", True):
            n_tr, debts["trainee"] = _annual_to_period_count(params.get("trainee", {}).get("new_cases_per_year", 0), period_days, rng, debts["trainee"])
            
        n_hi = 0
        if params.get("new_hires", {}).get("active", True):
            n_hi, debts["new_hires"] = _annual_to_period_count(params.get("new_hires", {}).get("count_per_year", 0), period_days, rng, debts["new_hires"])

        # 1. Azubis (Takeover of EXISTING & NEW from previous months)
        if params.get("azubi", {}).get("active", True):
            _simulate_azubis(df_state, params, period, rng, period_events, all_org_units, debts)

        period_gender_distribution = None
        if n_az > 0 or n_tr > 0 or n_hi > 0:
            normalized_genders = _normalized_gender_series(df_state)
            period_gender_distribution = _build_gender_distribution(df_state, normalized_genders)

        # 2. Trainees (NEW Hires)
        if params.get("trainee", {}).get("active", True):
            _simulate_trainees(
                df_state,
                params,
                period,
                rng,
                period_events,
                all_org_units,
                n_tr,
                start_date,
                period_gender_distribution,
            )
        
        # 3. New Azubis (NEW Hire entry)
        if params.get("azubi", {}).get("active", True):
            period_unit_to_cluster_map = _get_unit_to_cluster_map(df_state) if n_az > 0 else None
            _simulate_new_azubis(
                df_state,
                params,
                period,
                rng,
                period_events,
                all_org_units,
                n_az,
                start_date,
                debts,
                period_gender_distribution,
                period_unit_to_cluster_map,
            )
        
        # 4. New Hires (Standard)
        if params.get("new_hires", {}).get("active", True):
            _simulate_hires(
                df_state,
                params,
                period,
                rng,
                period_events,
                all_org_units,
                current_vacancies,
                n_hi,
                start_date,
                period_gender_distribution,
            )
        
        # Apply New Rows
        new_rows = [e["_new_row"] for e in period_events if "_new_row" in e]
        if new_rows:
            df_new = pd.DataFrame(new_rows)
            df_new.set_index("PersNr", drop=False, inplace=True) # Ensure Index matches
            # Expand df_state with any new columns from df_new (fill existing rows with NA)
            for col in df_new.columns:
                if col not in df_state.columns:
                    df_state[col] = pd.NA
            df_new = df_new.reindex(columns=df_state.columns)
            # Both frames now share identical columns; suppress FutureWarning about all-NA
            # column dtype inference (pandas >=2.1 interim behaviour, not a logic error).
            import warnings as _w
            with _w.catch_warnings():
                _w.filterwarnings("ignore", message=".*empty or all-NA.*", category=FutureWarning)
                df_state = pd.concat([df_state, df_new])
            
        # Cleanup _new_row from events before saving
        for e in period_events:
            e.pop("_new_row", None)
            all_events.append(e)

    # Result
    events_df = pd.DataFrame(all_events)
    if events_df.empty:
        # Enforce schema to avoid KeyErrors downstream
        events_df = pd.DataFrame(columns=["date", "type", "count", "persnr", "org_unit", "source", "mak", "Jobfamily", "OE-Cluster", "TrfGr", "St", "Planstelle", "is_forecast"])
    
    # Schema Normalization (id: 16 - Fix Schema)
    # Ensure consistent keys: persnr -> PersNr, org_unit -> Organisationseinheit
    # But keep 'persnr' for backward compatibility if needed, or better aliasing.
    # The hybrid page expects lower case 'persnr' in some places, but 'PersNr' is the standard in df.
    # We will enforce:
    # 1. persnr (lower) for events
    # 2. PersNr (Title) for State match
    # 3. org_unit (events) vs Organisationseinheit (State)
    # Actually, let's just make sure both exist to avoid KeyErrors.
    if not events_df.empty:
        if "PersNr" not in events_df.columns and "persnr" in events_df.columns:
            events_df["PersNr"] = events_df["persnr"]
        if "persnr" not in events_df.columns and "PersNr" in events_df.columns:
            events_df["persnr"] = events_df["PersNr"]
            
        if "Organisationseinheit" not in events_df.columns and "org_unit" in events_df.columns:
            events_df["Organisationseinheit"] = events_df["org_unit"]
        if "org_unit" not in events_df.columns and "Organisationseinheit" in events_df.columns:
            events_df["org_unit"] = events_df["Organisationseinheit"]

        # Ensure 'mak' exists
        if "mak" not in events_df.columns:
            events_df["mak"] = 1.0
            
    # Final Sanity: Drop duplicate columns (fix InvalidIndexError via concat)
    if not events_df.empty:
        events_df = events_df.loc[:, ~events_df.columns.duplicated()]
            
    # Debug / Regression Check (id: 16 - Fix Regression Check)
    debug_info = {}
    if params.get("debug", False) or params.get("azubi", {}).get("exclude_baseline_azubis", False):
        try:
            # Check internal pools
            # Note: This is post-simulation, so 'active' might have changed.
            # We better check the EVENTS generated.
            
            # Count Azubi-Outcome events by Source Pool
            outcome_events = events_df[events_df["type"].isin(["Azubi_Conversion_In", "Azubi_Exit", "Azubi_Conversion_Out"])].copy()
            if not outcome_events.empty:
                if "is_forecast" not in outcome_events.columns:
                    outcome_events["is_forecast"] = False # Assume baseline if missing
                
                # Event-based counts
                fc_ev = len(outcome_events[outcome_events["is_forecast"] == True])
                bl_ev = len(outcome_events[outcome_events["is_forecast"] == False])
                
                # Person-based counts (Unique PersNr)
                fc_pers = outcome_events[outcome_events["is_forecast"] == True]["persnr"].nunique()
                bl_pers = outcome_events[outcome_events["is_forecast"] == False]["persnr"].nunique()
                
                debug_info["completions_forecast_count"] = f"{fc_ev} Events ({fc_pers} Persons)"
                debug_info["completions_baseline_count"] = f"{bl_ev} Events ({bl_pers} Persons)"
            else:
                debug_info["completions_forecast_count"] = "0 Events (0 Persons)"
                debug_info["completions_baseline_count"] = "0 Events (0 Persons)"
                
            # Isolation Verification
            if params.get("azubi", {}).get("exclude_baseline_azubis", False):
                if bl_ev > 0: # Use event count for strict check
                     debug_info["isolation_error"] = f"CRITICAL: Found {bl_ev} baseline events despite isolation!"
        except Exception as e:
            debug_info["error"] = str(e)
    
    
    # Audit Baseline Conversions (New Feature)
    if params.get("azubi", {}).get("active", True):
        # 1. Identify Baseline Conversions in events
        baseline_convs = events_df[
            (events_df["type"] == "Azubi_Conversion_In") & 
            (events_df["is_forecast"] == False)
        ]
        
        if not baseline_convs.empty:
            # Group by year
            baseline_counts = baseline_convs["date"].dt.year.value_counts()
            
            # Compare against planned new cases
            planned_cases = float(params.get("azubi", {}).get("new_cases_per_year", 0))
            
            for year, count in baseline_counts.items():
                if count > planned_cases:
                    msg = f"Hinweis: Im Jahr {year} werden {count} Bestands-Azubis fertig (Planung: {int(planned_cases)})."
                    if "warnings" not in debug_info:
                        debug_info["warnings"] = []
                    debug_info["warnings"].append(msg)
                    # print(f"WARNING: {msg}") # Optional Console Output

    # Calculate Time Series KPIs (Headcount/MAK Evolution)
    # Start Stats
    start_hc = df_snapshot["active"].sum() if "active" in df_snapshot.columns else len(df_snapshot)
    _mak_col = next((c for c in ("MAK_Calculated", "mak", "MAK") if c in df_snapshot.columns), None)
    start_mak = float(df_snapshot[_mak_col].fillna(0).sum()) if _mak_col else 0.0

    # We need a monthly stats dataframe
    # Iterate periods and sum up events
    kpi_rows = []
    
    cum_hc_change = 0
    cum_mak_change = 0.0
    cum_cost_change = 0.0
    
    # Aggregate events by month
    if not events_df.empty:
        events_df["month"] = events_df["date"].dt.to_period("M")
        # Ensure count, FTE (default 1.0 for new hires if not present), Cost
        if "count" not in events_df.columns: events_df["count"] = 1
        if "mak" not in events_df.columns: events_df["mak"] = 1.0 # Default FTE for simulation
        
        # Calculate Cost for events if not done in page (better do it here if possible, but page does it via vectorization)
        # We'll skip complex cost logic here and rely on page, or do simple approximation
        # Page handles cost. We track HC/MAK here.
        
        events_by_month = events_df.groupby("month").agg({
            "count": "sum",
            "mak": "sum"
        }).to_dict("index")
    else:
        events_by_month = {}
        
    for p in period_range:
        m = p
        month_str = str(m)
        
        # Events in this month
        stats = events_by_month.get(m, {"count": 0, "mak": 0.0})
        delta_hc = stats["count"]
        delta_mak = stats["mak"]
        
        kpi_rows.append({
            "date": p.end_time,
            "headcount_start": start_hc + cum_hc_change,
            "headcount_end": start_hc + cum_hc_change + delta_hc,
            "mak_start": start_mak + cum_mak_change,
            "mak_end": start_mak + cum_mak_change + delta_mak,
            "entry_count": delta_hc,
            "entry_mak": delta_mak
        })
        
        cum_hc_change += delta_hc
        cum_mak_change += delta_mak

    forecast_kpis = pd.DataFrame(kpi_rows)

    if INTERNAL_EDUCATION_COL in df_state.columns:
        df_state = df_state.drop(columns=[INTERNAL_EDUCATION_COL])
    if INTERNAL_AZUBI_FLAG_COL in df_state.columns:
        df_state = df_state.drop(columns=[INTERNAL_AZUBI_FLAG_COL])

    return {
        "events": events_df,
        "final_state": df_state,
        "forecast_kpis": forecast_kpis,
        "debug_info": debug_info
    }
