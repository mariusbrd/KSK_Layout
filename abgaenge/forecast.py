"""
Forecast engine for Abgaenge.
"""

from __future__ import annotations

from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schemas import (
    COL_PERSNR,
    COL_GEB,
    COL_EINTRITT,
    COL_AUSTRITT,
    COL_BSGRD,
    COL_STATUS,
    COL_SOLL,
    COL_OE_CODE,
    EXCLUDED_OE_CODES,
    COL_ATZ_PHASE,
    COL_ATZ_BEGINN,
    COL_ATZ_ENDE,
    COL_ATZ_VERTRAG_ENDE,
    REASON_ATZ_AR_TO_FR,
    REASON_ATZ_END,
    REASON_RETIREMENT,
    REASON_QUIT,
    REASON_RUHEND_START,
    REASON_RUHEND_RETURN,
    REASON_LABELS,
)


@dataclass
class PeriodInfo:
    start: pd.Timestamp
    end: pd.Timestamp
    label: str


def _annual_to_period_rate(annual_rate: float, period_days: float) -> float:
    annual_rate = max(0.0, float(annual_rate))
    if annual_rate >= 1.0:
        return 1.0
    return 1.0 - (1.0 - annual_rate) ** (period_days / 365.25)


def _annual_to_period_rates(annual_rates: np.ndarray, period_days: float) -> np.ndarray:
    annual_rates = np.maximum(0.0, np.asarray(annual_rates, dtype=float))
    period_rates = 1.0 - np.power(1.0 - annual_rates, period_days / 365.25)
    period_rates[annual_rates >= 1.0] = 1.0
    return period_rates


def _periods(start_date: pd.Timestamp, end_date: pd.Timestamp, freq: str) -> List[PeriodInfo]:
    if freq not in {"M", "Q"}:
        raise ValueError("freq muss 'M' (Monat) oder 'Q' (Quartal) sein.")

    period_range = pd.period_range(start=start_date, end=end_date, freq=freq)
    periods: List[PeriodInfo] = []

    for p in period_range:
        start = p.start_time
        end = p.end_time
        if freq == "M":
            label = p.strftime("%Y-%m")
        else:
            label = f"{p.year}-Q{p.quarter}"
        periods.append(PeriodInfo(start=start, end=end, label=label))

    return periods


def _calc_age(gbd: pd.Series, ref_date: pd.Timestamp) -> pd.Series:
    return (ref_date - pd.to_datetime(gbd, errors="coerce")).dt.days / 365.25


def _calc_tenure(eintritt: pd.Series, ref_date: pd.Timestamp) -> pd.Series:
    return (ref_date - pd.to_datetime(eintritt, errors="coerce")).dt.days / 365.25


def _build_atz_pivot(df_atz: pd.DataFrame) -> pd.DataFrame:
    if df_atz.empty:
        return pd.DataFrame(columns=[
            COL_PERSNR, "ar_begin", "ar_end", "fr_begin", "fr_end", "contract_end"
        ])

    df = df_atz.copy()
    
    # Harden types using central normalization
    from abgaenge.schemas import normalize_persnr
    if COL_PERSNR in df.columns:
        df[COL_PERSNR] = normalize_persnr(df[COL_PERSNR])
        
    date_cols = [COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE]
    for c in date_cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")

    # Normalize Phase values to handle "Arbeitsphase"/"Freistellungsphase" variations
    if COL_ATZ_PHASE in df.columns:
        df[COL_ATZ_PHASE] = df[COL_ATZ_PHASE].astype(str).str.strip()
        # Map common German terms to internal codes
        phase_map = {
            "Arbeitsphase": "AR",
            "Freistellungsphase": "FR",
            "Passivphase": "FR",
            "arbeitsphase": "AR", 
            "freistellungsphase": "FR"
        }
        df[COL_ATZ_PHASE] = df[COL_ATZ_PHASE].replace(phase_map)

    ar = df[df[COL_ATZ_PHASE] == "AR"][[COL_PERSNR, COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE]].copy()
    fr = df[df[COL_ATZ_PHASE] == "FR"][[COL_PERSNR, COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE]].copy()

    ar = ar.rename(columns={COL_ATZ_BEGINN: "ar_begin", COL_ATZ_ENDE: "ar_end"})
    fr = fr.rename(columns={COL_ATZ_BEGINN: "fr_begin", COL_ATZ_ENDE: "fr_end"})

    merged = pd.merge(ar, fr, on=COL_PERSNR, how="outer", suffixes=("", "_fr"))

    if COL_ATZ_VERTRAG_ENDE in merged.columns:
        merged["contract_end"] = merged[COL_ATZ_VERTRAG_ENDE]
    elif f"{COL_ATZ_VERTRAG_ENDE}_fr" in merged.columns:
        merged["contract_end"] = merged[f"{COL_ATZ_VERTRAG_ENDE}_fr"]
    else:
        merged["contract_end"] = merged["fr_end"]

    return merged[[COL_PERSNR, "ar_begin", "ar_end", "fr_begin", "fr_end", "contract_end"]]


def _enforce_min_ar_duration(
    atz_pivot: pd.DataFrame, min_ar_months: int, min_fr_months: int
) -> pd.DataFrame:
    """Enforce minimum AR duration on existing ATZ cases.

    If an existing case has fr_begin - ar_begin < min_ar_months, shift
    fr_begin (and fr_end / contract_end) so that the AR phase meets the
    configured minimum.  New cases created by _schedule_new_atz_cases
    already respect this, so this only corrects legacy data.
    """
    if atz_pivot.empty:
        return atz_pivot

    pivot = atz_pivot.copy()

    for col in ["ar_begin", "fr_begin", "fr_end", "contract_end"]:
        if col in pivot.columns:
            pivot[col] = pd.to_datetime(pivot[col], errors="coerce")

    mask_valid = pivot["ar_begin"].notna() & pivot["fr_begin"].notna()
    if not mask_valid.any():
        return pivot

    for idx in pivot.index[mask_valid]:
        ar_begin = pivot.loc[idx, "ar_begin"]
        fr_begin_orig = pivot.loc[idx, "fr_begin"]

        min_fr_begin = ar_begin + pd.DateOffset(months=min_ar_months)

        if fr_begin_orig < min_fr_begin:
            shift = min_fr_begin - fr_begin_orig
            pivot.loc[idx, "fr_begin"] = min_fr_begin
            # Shift fr_end and contract_end by the same delta
            if pd.notna(pivot.loc[idx, "fr_end"]):
                pivot.loc[idx, "fr_end"] = pivot.loc[idx, "fr_end"] + shift
            else:
                pivot.loc[idx, "fr_end"] = min_fr_begin + pd.DateOffset(months=min_fr_months)
            if pd.notna(pivot.loc[idx, "contract_end"]):
                pivot.loc[idx, "contract_end"] = pivot.loc[idx, "contract_end"] + shift
            else:
                pivot.loc[idx, "contract_end"] = pivot.loc[idx, "fr_end"]

    return pivot


def _get_atz_events_from_schedule(
    atz_pivot: pd.DataFrame, period_start: pd.Timestamp, period_end: pd.Timestamp
) -> Tuple[List[str], List[str]]:
    if atz_pivot.empty:
        return [], []

    # AR to FR transitions in this period
    ar_to_fr = atz_pivot[
        (atz_pivot["fr_begin"] >= period_start) & (atz_pivot["fr_begin"] <= period_end)
    ][COL_PERSNR].dropna().astype(str).unique().tolist()

    # ATZ End (Retirement) in this period
    atz_end = atz_pivot[
        (atz_pivot["contract_end"] >= period_start) & (atz_pivot["contract_end"] <= period_end)
    ][COL_PERSNR].dropna().astype(str).unique().tolist()

    return ar_to_fr, atz_end


def _select_quit_prob(row: pd.Series, params: Dict[str, Any], period_year: int) -> float:
    quit_params = params.get("quit", {})
    base = float(quit_params.get("quit_rate_base", 0.05))
    if not quit_params.get("use_quit_matrix", True):
        prob = base
    else:
        matrix = quit_params.get("quit_matrix", {})
        dimension = quit_params.get("quit_dimension", "JobFamily") # Default
        age = float(row["age"])
        
        # Analyze Age Group
        if age < 30:
            age_key = "alter_unter_30"
        elif age < 45:
            age_key = "alter_30_45"
        elif age < 55:
            age_key = "alter_45_55"
        else:
            age_key = "alter_55_plus"

        # Analyze Dimension
        if dimension == "OrgUnit":
            dim_value = str(row.get("Organisationseinheit", "Default"))
        else:
            dim_value = str(row.get("Jobfamily", "Default"))

        age_row = matrix.get(age_key, {})
        if dim_value in age_row:
            prob = float(age_row[dim_value])
        elif "Default" in age_row:
            prob = float(age_row["Default"])
        else:
            prob = base

    # 4. Apply Year/JF Adjustments
    adjustments = quit_params.get("quit_adjustments", {})
    jf_name = str(row.get("Jobfamily", "Default"))
    
    # "more" -> 1.5x, "less" -> 0.5x
    if jf_name in adjustments.get("more", {}) and period_year in adjustments["more"][jf_name]:
        prob *= 1.5
    elif jf_name in adjustments.get("less", {}) and period_year in adjustments["less"][jf_name]:
        prob *= 0.5
        
    return min(1.0, prob)


def _select_quit_probs_frame(
    eligible: pd.DataFrame,
    params: Dict[str, Any],
    period_year: int,
) -> np.ndarray:
    if eligible.empty:
        return np.array([], dtype=float)

    quit_params = params.get("quit", {})
    base = float(quit_params.get("quit_rate_base", 0.05))
    probs = np.full(len(eligible), base, dtype=float)

    if quit_params.get("use_quit_matrix", True):
        matrix = quit_params.get("quit_matrix", {})
        dimension = quit_params.get("quit_dimension", "JobFamily")
        ages = pd.to_numeric(eligible["age"], errors="coerce").fillna(0.0).to_numpy()
        age_keys = np.where(
            ages < 30,
            "alter_unter_30",
            np.where(ages < 45, "alter_30_45", np.where(ages < 55, "alter_45_55", "alter_55_plus")),
        )

        if dimension == "OrgUnit":
            dim_values = eligible.get("Organisationseinheit", pd.Series("Default", index=eligible.index)).astype(str).tolist()
        else:
            dim_values = eligible.get("Jobfamily", pd.Series("Default", index=eligible.index)).astype(str).tolist()

        for idx, (age_key, dim_value) in enumerate(zip(age_keys, dim_values)):
            age_row = matrix.get(age_key, {})
            if dim_value in age_row:
                probs[idx] = float(age_row[dim_value])
            elif "Default" in age_row:
                probs[idx] = float(age_row["Default"])

    adjustments = quit_params.get("quit_adjustments", {})
    more = adjustments.get("more", {})
    less = adjustments.get("less", {})
    jf_values = eligible.get("Jobfamily", pd.Series("Default", index=eligible.index)).astype(str).tolist()

    for idx, jf_name in enumerate(jf_values):
        if jf_name in more and period_year in more[jf_name]:
            probs[idx] *= 1.5
        elif jf_name in less and period_year in less[jf_name]:
            probs[idx] *= 0.5

    np.minimum(probs, 1.0, out=probs)
    return probs


def _select_atz_prob(row: pd.Series, params: Dict[str, Any]) -> float:
    atz_params = params.get("atz", {})
    base = float(atz_params.get("new_atz_rate", 0.05))
    if not atz_params.get("use_atz_matrix", False):
        return base

    matrix = atz_params.get("atz_matrix", {})
    dimension = atz_params.get("atz_dimension", "JobFamily")

    if dimension == "OrgUnit":
        dim_value = str(row.get("Organisationseinheit", "Default"))
    else:
        dim_value = str(row.get("Jobfamily", "Default"))

    # Flat lookup (no age cohorts)
    if dim_value in matrix:
        return float(matrix[dim_value])
    elif "Default" in matrix:
        return float(matrix["Default"])
    
    return base


def _select_atz_probs_frame(
    eligible: pd.DataFrame,
    params: Dict[str, Any],
    period_fraction: float,
) -> np.ndarray:
    if eligible.empty:
        return np.array([], dtype=float)

    atz_params = params.get("atz", {})
    base = float(atz_params.get("new_atz_rate", 0.05))
    probs = np.full(len(eligible), base, dtype=float)

    if atz_params.get("use_atz_matrix", False):
        matrix = atz_params.get("atz_matrix", {})
        dimension = atz_params.get("atz_dimension", "JobFamily")
        if dimension == "OrgUnit":
            dim_values = eligible.get("Organisationseinheit", pd.Series("Default", index=eligible.index)).astype(str).tolist()
        else:
            dim_values = eligible.get("Jobfamily", pd.Series("Default", index=eligible.index)).astype(str).tolist()

        default_prob = float(matrix["Default"]) if "Default" in matrix else base
        probs = np.array([float(matrix.get(dim_value, default_prob)) for dim_value in dim_values], dtype=float)

    probs *= period_fraction
    np.maximum(probs, 0.0, out=probs)
    return probs


def _schedule_new_atz_cases(
    df_state: pd.DataFrame,
    atz_pivot: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
) -> pd.DataFrame:
    atz_params = params.get("atz", {})
    eligible_age_min = int(atz_params.get("atz_eligible_age_min", 55))
    eligible_age_max = int(atz_params.get("atz_eligible_age_max", 60))
    ar_months = int(round(float(atz_params.get("atz_duration_ar_years", 2.5)) * 12))
    fr_months = int(round(float(atz_params.get("atz_duration_fr_years", 2.5)) * 12))
    # Build base eligibility mask
    mask = (
        (df_state["active"] == True) &
        (~df_state["in_atz"]) &
        (~df_state["status_ruhend"]) &
        (df_state["age"] >= eligible_age_min) &
        (df_state["age"] <= eligible_age_max)  # F02: Apply upper bound
    )

    # Exclude special OEs (Freistellung, Dauerkranke, Elternzeit, etc.)
    if COL_OE_CODE in df_state.columns:
        mask = mask & (~df_state[COL_OE_CODE].astype(str).isin(EXCLUDED_OE_CODES))

    eligible = df_state[mask].sort_index()

    if eligible.empty:
        return atz_pivot

    # Adjust annual rate to period length
    period_fraction = (period.end - period.start).days / 365.25

    # Determine individual probabilities and aggregate expected value
    probs = _select_atz_probs_frame(eligible, params, period_fraction)
    expected = float(np.sum(probs))
    
    if expected <= 0:
        return atz_pivot
    
    count = int(rng.poisson(lam=expected))
    if count <= 0:
        return atz_pivot

    # Sample only from strictly positive probabilities to avoid:
    # "Fewer non-zero entries in p than size" when many weights are zero.
    positive_mask = probs > 0
    if not positive_mask.any():
        return atz_pivot

    eligible_pos = eligible.loc[positive_mask]
    probs_pos = probs[positive_mask]
    count = min(count, len(eligible_pos))
    if count <= 0:
        return atz_pivot

    p_normalized = probs_pos / float(np.sum(probs_pos))
    chosen = eligible_pos.sample(
        n=count,
        replace=False,
        weights=p_normalized,
        random_state=rng,
    ).index.tolist()

    new_rows = []
    for persnr in chosen:
        offset_days = rng.integers(0, max(1, (period.end - period.start).days + 1))
        ar_start = period.start + pd.Timedelta(days=int(offset_days))
        ar_end = ar_start + pd.DateOffset(months=ar_months)
        fr_start = ar_end + pd.Timedelta(days=1)
        fr_end = fr_start + pd.DateOffset(months=fr_months)
        contract_end = fr_end
        new_rows.append({
            COL_PERSNR: persnr,
            "ar_begin": ar_start,
            "ar_end": ar_end,
            "fr_begin": fr_start,
            "fr_end": fr_end,
            "contract_end": contract_end,
        })

    if new_rows:
        atz_pivot = pd.concat([atz_pivot, pd.DataFrame(new_rows)], ignore_index=True)

    return atz_pivot




def aggregate_forecast_results(
    df_initial: pd.DataFrame,
    events_df: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    freq: str,
    params: Dict[str, Any] # For assumptions like Soll/MAK fallback
) -> pd.DataFrame:
    """
    Calculates Headcount/MAK evolution based on an initial state and a log of events.
    Used for both Global (Full) and Filtered views.
    """
    periods = _periods(start_date, end_date, freq)
    
    # Validation
    if df_initial.empty:
        # Return empty KPI/Timeline
        return pd.DataFrame() # todo: minimal structure
    
    # Build working state with lightweight Series-based dynamic state.
    df_state = df_initial.copy()
    if COL_PERSNR in df_state.columns:
        df_state[COL_PERSNR] = df_state[COL_PERSNR].astype(str)
        df_state = df_state.set_index(COL_PERSNR, drop=False)

    active_state = pd.Series(True, index=df_state.index, dtype=bool)
    if "MAK_Calculated" in df_state.columns:
        mak_state = pd.to_numeric(df_state["MAK_Calculated"], errors="coerce").fillna(0.0).astype(float)
    else:
        mak_state = pd.Series(1.0, index=df_state.index, dtype=float)
    status_ruhend_state = (df_state[COL_STATUS] == "Ruhendes Beschäftigungsverhältnis").astype(bool)

    if not events_df.empty:
        working_events = events_df.copy()
        working_events["event_date"] = pd.to_datetime(working_events["event_date"])
        working_events = working_events.sort_values("event_date")
        event_records = list(working_events.itertuples(index=False, name="ForecastEvent"))
    else:
        event_records = []

    event_pos = 0
    total_events = len(event_records)
    kpis = []
    
    for period in periods:
        headcount_start = int(active_state.sum())
        mak_start = float(mak_state.loc[active_state].sum())

        exit_count = 0
        mak_loss_gross = 0.0
        abgaenge_total_count = 0

        while event_pos < total_events and event_records[event_pos].event_date < period.start:
            event_pos += 1

        period_event_pos = event_pos
        while period_event_pos < total_events and event_records[period_event_pos].event_date <= period.end:
            event = event_records[period_event_pos]
            p_id = str(event.persnr)
            headcount_change = event.headcount_change
            delta_mak = float(getattr(event, "mak_change", 0.0))
            reason_code = getattr(event, "reason_code", None)

            if p_id in active_state.index:
                if headcount_change < 0:
                    active_state.at[p_id] = False
                    exit_count += 1
                    abgaenge_total_count += 1
                elif headcount_change > 0:
                    active_state.at[p_id] = True

                current_mak = float(mak_state.at[p_id])
                mak_state.at[p_id] = max(0.0, current_mak + delta_mak)

                if delta_mak < 0:
                    mak_loss_gross += abs(delta_mak)
                    if headcount_change == 0:
                        abgaenge_total_count += 1

                if reason_code == REASON_RUHEND_RETURN:
                    status_ruhend_state.at[p_id] = False
                elif reason_code == REASON_RUHEND_START:
                    status_ruhend_state.at[p_id] = True

            elif headcount_change > 0:
                active_state.at[p_id] = True
                mak_state.at[p_id] = delta_mak
                status_ruhend_state.at[p_id] = False

            period_event_pos += 1

        event_pos = period_event_pos

        headcount_end = int(active_state.sum())
        mak_end = float(mak_state.loc[active_state].sum())

        avg_headcount = (headcount_start + headcount_end) / 2 if (headcount_start + headcount_end) > 0 else 0
        abgangsquote = (exit_count / avg_headcount) if avg_headcount > 0 else 0.0

        kpis.append({
            "period_label": period.label,
            "period_start": period.start,
            "period_end": period.end,
            "headcount_start": headcount_start,
            "headcount_end": headcount_end,
            "headcount_delta": headcount_end - headcount_start,
            "mak_start": mak_start,
            "mak_end": mak_end,
            "mak_delta": mak_end - mak_start,
            "abgaenge_total": abgaenge_total_count,
            "exit_count": exit_count,
            "mak_loss_gross": mak_loss_gross,
            "abgangsquote": abgangsquote,
        })
        
    return pd.DataFrame(kpis)


def run_forecast_abgaenge(
    df_ma: pd.DataFrame,
    df_atz: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    freq: str = "M",
    params: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Run Abgaenge forecast.

    Returns:
        dict with keys: forecast_kpis, events_person_level, assumptions, tables
    """
    if params is None:
        params = {}
        
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    if end_date <= start_date:
        raise ValueError("forecast_end_date muss nach ist_stichtag liegen.")

    # Validate required columns
    required_cols = [COL_PERSNR, COL_GEB, COL_EINTRITT, COL_BSGRD, COL_STATUS]
    missing = [c for c in required_cols if c not in df_ma.columns]
    if missing:
        raise ValueError(f"Mitarbeiter.xlsx fehlt Spalten: {', '.join(missing)}")

    # RNG Isolation (Contract V6)
    # Using SeedSequence to spawn independent streams per component.
    base_seed = int(params.get("random_seed", 42))
    ss = np.random.SeedSequence(base_seed)
    
    # Spawn 5 distinct seeds for abgaenge components
    seeds = ss.spawn(5)
    rng_atz = np.random.default_rng(seeds[0])
    rng_ret = np.random.default_rng(seeds[1])
    rng_quit = np.random.default_rng(seeds[2])
    rng_ruh_new = np.random.default_rng(seeds[3])
    rng_ruh_ret = np.random.default_rng(seeds[4])

    # Build baseline state
    df_state = df_ma.copy()
    if COL_PERSNR in df_state.columns:
        # Standardize PersNr for sorting and matching
        df_state[COL_PERSNR] = df_state[COL_PERSNR].astype(str).str.split('.').str[0].str.zfill(6)

    df_state = df_state.set_index(COL_PERSNR, drop=True)
    
    if df_state.index.duplicated().any():
        df_state = df_state[~df_state.index.duplicated(keep='first')]

    df_state["age"] = _calc_age(df_state[COL_GEB], start_date)
    df_state["tenure"] = _calc_tenure(df_state[COL_EINTRITT], start_date)
    df_state["status_ruhend"] = df_state[COL_STATUS] == "Ruhendes Beschäftigungsverhältnis"

    # ATZ current FR at start
    atz_fr_active = set()
    if not df_atz.empty and COL_ATZ_PHASE in df_atz.columns:
        fr_rows = df_atz[df_atz[COL_ATZ_PHASE] == "FR"]
        fr_active = fr_rows[
            (fr_rows[COL_ATZ_BEGINN] <= start_date) & (fr_rows[COL_ATZ_ENDE] >= start_date)
        ]
        atz_fr_active = set(fr_active[COL_PERSNR].dropna().astype(str).str.split('.').str[0].str.zfill(6))

    all_atz_persnrs = set(df_atz[COL_PERSNR].dropna().astype(str).str.split('.').str[0].str.zfill(6)) if not df_atz.empty else set()
    df_state["in_atz"] = df_state.index.isin(all_atz_persnrs)
    df_state["atz_fr_active"] = df_state.index.isin(atz_fr_active)
    df_state["active"] = True

    bsgrd = pd.to_numeric(df_state[COL_BSGRD], errors="coerce").fillna(0.0)
    
    if COL_SOLL in df_state.columns:
        soll_hours = pd.to_numeric(df_state[COL_SOLL], errors="coerce").fillna(0.0)
        soll_hours = np.where(soll_hours <= 0.01, 39.0, soll_hours)
        soll_fte_factor = soll_hours / 39.0
    else:
        soll_fte_factor = 1.0

    if "MAK_Calculated" in df_state.columns:
        df_state["mak"] = pd.to_numeric(df_state["MAK_Calculated"], errors="coerce").fillna(0.0)
    else:
        raw_mak = (bsgrd / 100.0) * soll_fte_factor
        df_state["mak"] = np.where(
            (df_state["status_ruhend"] | df_state["atz_fr_active"]),
            0.0,
            raw_mak,
        )

    ruhend_months = int(params.get("ruhend", {}).get("ruhend_avg_duration_months", 12))
    df_state["ruhend_until"] = pd.NaT
    df_state["mak_before_ruhend"] = df_state["mak"]
    df_state.loc[df_state["status_ruhend"], "ruhend_until"] = start_date + pd.DateOffset(months=ruhend_months)

    # Standardize ATZ Pivot PersNr
    atz_pivot = _build_atz_pivot(df_atz)
    if not atz_pivot.empty:
        atz_pivot[COL_PERSNR] = atz_pivot[COL_PERSNR].astype(str).str.split('.').str[0].str.zfill(6)
        valid_persnrs = set(df_state.index)
        atz_pivot = atz_pivot[atz_pivot[COL_PERSNR].isin(valid_persnrs)].copy()
        if "fr_begin" in atz_pivot.columns:
            atz_pivot["fr_begin"] = pd.to_datetime(atz_pivot["fr_begin"], errors="coerce")
        if "contract_end" in atz_pivot.columns:
            atz_pivot["contract_end"] = pd.to_datetime(atz_pivot["contract_end"], errors="coerce")

    atz_params = params.get("atz", {})
    try:
        ar_years_val = float(atz_params.get("atz_duration_ar_years", 2.5))
        min_ar_months = int(round(ar_years_val * 12))
    except (ValueError, TypeError):
        min_ar_months = 30 # Default 2.5 years
    
    if min_ar_months < 6:
        min_ar_months = 30
    
    min_fr_months = int(round(float(atz_params.get("atz_duration_fr_years", 2.5)) * 12))
        
    atz_pivot = _enforce_min_ar_duration(atz_pivot, min_ar_months, min_fr_months)

    periods = _periods(start_date, end_date, freq)
    
    events: List[Dict[str, Any]] = []

    for period in periods:
        period_days = max(1, (period.end - period.start).days)
        period_deactivated: set = set()

        df_state["age"] = _calc_age(df_state[COL_GEB], period.start)
        df_state["tenure"] = _calc_tenure(df_state[COL_EINTRITT], period.start)

        # ---------------------------------------------------------
        # Patch 3: Strict Monthly Execution Order
        # ---------------------------------------------------------

        # A. ATZ Scheduling (uses rng_atz)
        if params.get("components", {}).get("atz", True):
            # Sort for deterministic selection? _schedule_new_atz_cases should sort its mask.
            atz_pivot = _schedule_new_atz_cases(df_state, atz_pivot, params, period, rng_atz)
            df_state["in_atz"] = df_state.index.isin(atz_pivot[COL_PERSNR].unique())

        # B. ATZ Status Change (Deterministic based on pivot)
        if params.get("components", {}).get("atz", True):
            ar_to_fr, atz_end = _get_atz_events_from_schedule(atz_pivot, period.start, period.end)

            for persnr in sorted(ar_to_fr): # Deterministic order
                if persnr in df_state.index and df_state.at[persnr, "active"]:
                    current_mak = float(df_state.at[persnr, "mak"])
                    mak_change = -current_mak if current_mak > 0.001 else -1.0 
                    age = float(df_state.at[persnr, "age"])
                    tenure = float(df_state.at[persnr, "tenure"])
                    org_unit = str(df_state.at[persnr, "Organisationseinheit"])
                    jobfamily = str(df_state.at[persnr, "Jobfamily"])
                    df_state.at[persnr, "atz_fr_active"] = True
                    df_state.at[persnr, "mak"] = 0.0
                    events.append({
                        "period_label": period.label,
                        "period_start": period.start,
                        "period_end": period.end,
                        "event_date": period.end,
                        "persnr": persnr,
                        "reason_code": REASON_ATZ_AR_TO_FR,
                        "reason_label": REASON_LABELS[REASON_ATZ_AR_TO_FR],
                        "headcount_change": 0,
                        "mak_change": mak_change,
                        "age": age,
                        "tenure": tenure,
                        "Organisationseinheit": org_unit,
                        "Jobfamily": jobfamily,
                    })

            for persnr in sorted(atz_end): # Deterministic order
                if persnr in df_state.index and df_state.at[persnr, "active"]:
                    mak_change = -float(df_state.at[persnr, "mak"])
                    age = float(df_state.at[persnr, "age"])
                    df_state.at[persnr, "mak"] = 0.0
                    df_state.at[persnr, "active"] = False
                    period_deactivated.add(persnr)
                    events.append({
                        "period_label": period.label,
                        "period_start": period.start,
                        "period_end": period.end,
                        "event_date": period.end,
                        "persnr": persnr,
                        "reason_code": REASON_ATZ_END,
                        "reason_label": REASON_LABELS[REASON_ATZ_END],
                        "headcount_change": -1,
                        "mak_change": mak_change,
                        "age": age,
                    })

        # C. Retirement (uses rng_ret)
        if params.get("components", {}).get("retirement", True):
            eligible = df_state[(df_state["active"]) & (~df_state["in_atz"]) & (~df_state.index.isin(period_deactivated))].copy()
            if not eligible.empty:
                # Patch 2: Stable Sorting
                eligible = eligible.sort_index()
                ages = eligible["age"]
                p65 = _annual_to_period_rate(params.get("retirement", {}).get("rent_rate_65", 0.9), period_days)
                p60 = _annual_to_period_rate(params.get("retirement", {}).get("rent_rate_60_65", 0.1), period_days)
                probs = np.where(ages >= 65, p65, np.where((ages >= 60) & (ages < 65), p60, 0.0))
                draws = rng_ret.random(len(eligible))
                retire_ids = eligible.index[draws < probs].tolist()

                for persnr in retire_ids:
                    mak_change = -float(df_state.at[persnr, "mak"])
                    age = float(df_state.at[persnr, "age"])
                    org_unit = str(df_state.at[persnr, "Organisationseinheit"])
                    jobfamily = str(df_state.at[persnr, "Jobfamily"])
                    df_state.at[persnr, "mak"] = 0.0
                    df_state.at[persnr, "active"] = False
                    period_deactivated.add(persnr)
                    events.append({
                        "period_label": period.label,
                        "period_start": period.start,
                        "period_end": period.end,
                        "event_date": period.end,
                        "persnr": persnr,
                        "reason_code": REASON_RETIREMENT,
                        "reason_label": REASON_LABELS[REASON_RETIREMENT],
                        "Organisationseinheit": org_unit,
                        "Jobfamily": jobfamily,
                        "headcount_change": -1,
                        "mak_change": mak_change,
                        "age": age,
                    })

        # D. Quit (uses rng_quit) - AFTER Retirement
        if params.get("components", {}).get("quit", True):
            eligible = df_state[(df_state["active"]) & (~df_state["in_atz"]) & (~df_state.index.isin(period_deactivated))].copy()
            if not eligible.empty:
                # Patch 2: Stable Sorting
                eligible = eligible.sort_index()
                period_year = period.start.year
                annual_probs = _select_quit_probs_frame(eligible, params, period_year)
                probs = _annual_to_period_rates(annual_probs, period_days)
                draws = rng_quit.random(len(eligible))
                quit_ids = eligible.index[draws < probs].tolist()

                for persnr in quit_ids:
                    mak_change = -float(df_state.at[persnr, "mak"])
                    age = float(df_state.at[persnr, "age"])
                    org_unit = str(df_state.at[persnr, "Organisationseinheit"])
                    jobfamily = str(df_state.at[persnr, "Jobfamily"])
                    df_state.at[persnr, "mak"] = 0.0
                    df_state.at[persnr, "active"] = False
                    period_deactivated.add(persnr) # Not strictly needed as it's the end of exits, but good practice
                    events.append({
                        "period_label": period.label,
                        "period_start": period.start,
                        "period_end": period.end,
                        "event_date": period.end,
                        "persnr": persnr,
                        "reason_code": REASON_QUIT,
                        "reason_label": REASON_LABELS[REASON_QUIT],
                        "Organisationseinheit": org_unit,
                        "Jobfamily": jobfamily,
                        "headcount_change": -1,
                        "mak_change": mak_change,
                        "age": age,
                    })

        # E. Ruhend logic (return then new)
        if params.get("components", {}).get("ruhend", True):
            # Return
            return_rate = float(params.get("ruhend", {}).get("ruhend_return_rate", 0.95))
            return_prob = _annual_to_period_rate(return_rate, period_days)
            eligible = df_state[(df_state["active"]) & (df_state["status_ruhend"]) & (~df_state.index.isin(period_deactivated))].copy()
            if not eligible.empty:
                eligible = eligible.sort_index() # Patch 2
                can_return = eligible[eligible["ruhend_until"].notna() & (eligible["ruhend_until"] <= period.start)]
                if not can_return.empty:
                    draws = rng_ruh_ret.random(len(can_return))
                    return_ids = can_return.index[draws < return_prob].tolist()
                    for persnr in return_ids:
                        restored_mak = float(df_state.at[persnr, "mak_before_ruhend"])
                        org_unit = str(df_state.at[persnr, "Organisationseinheit"])
                        jobfamily = str(df_state.at[persnr, "Jobfamily"])
                        df_state.at[persnr, "status_ruhend"] = False
                        df_state.at[persnr, "mak"] = restored_mak
                        df_state.at[persnr, "ruhend_until"] = pd.NaT
                        events.append({
                            "period_label": period.label, "event_date": period.end, "persnr": persnr,
                            "reason_code": REASON_RUHEND_RETURN, "reason_label": REASON_LABELS[REASON_RUHEND_RETURN],
                            "Organisationseinheit": org_unit,
                            "Jobfamily": jobfamily,
                            "headcount_change": 0, "mak_change": restored_mak,
                        })
            # New
            expected = float(params.get("ruhend", {}).get("ruhend_new_cases_per_year", 0)) * (period_days / 365.25)
            if expected > 0:
                count = int(rng_ruh_new.poisson(lam=expected))
                if count > 0:
                    eligible = df_state[(df_state["active"]) & (~df_state["status_ruhend"]) & (~df_state["in_atz"]) & (~df_state.index.isin(period_deactivated))].copy()
                    if not eligible.empty:
                        # Patch 2: Stable sample
                        eligible = eligible.sort_index()
                        chosen = eligible.sample(n=min(count, len(eligible)), random_state=rng_ruh_new).index.tolist()
                        for persnr in chosen:
                            current_mak = float(df_state.at[persnr, "mak"])
                            org_unit = str(df_state.at[persnr, "Organisationseinheit"])
                            jobfamily = str(df_state.at[persnr, "Jobfamily"])
                            df_state.at[persnr, "mak_before_ruhend"] = current_mak
                            mak_diff = -current_mak
                            df_state.at[persnr, "status_ruhend"] = True
                            df_state.at[persnr, "mak"] = 0.0
                            df_state.at[persnr, "ruhend_until"] = period.start + pd.DateOffset(months=ruhend_months)
                            events.append({
                                "period_label": period.label, "event_date": period.end, "persnr": persnr,
                                "reason_code": REASON_RUHEND_START, "reason_label": REASON_LABELS[REASON_RUHEND_START],
                                "Organisationseinheit": org_unit,
                                "Jobfamily": jobfamily,
                                "headcount_change": 0, "mak_change": mak_diff,
                            })
        
        # KPI Capture MOVED to aggregate_forecast_results
        
    events_df = pd.DataFrame(events)
    if events_df.empty:
        # Enforce schema to avoid KeyErrors downstream
        events_df = pd.DataFrame(columns=[
            "period_label", "period_start", "period_end", "event_date", 
            "persnr", "reason_code", "reason_label", "headcount_change", "mak_change",
            "age", "tenure", "Organisationseinheit", "Kürzel OrgEinheit", 
            "Jobfamily", "Planstelle", "TrfGr", "St"
        ])

    # Calculate Aggregations for the GLOBAL result immediately
    forecast_kpis = aggregate_forecast_results(
        df_initial=df_ma, 
        events_df=events_df, 
        start_date=start_date, 
        end_date=end_date, 
        freq=freq,
        params=params
    )
    
    tables = {
        "atz_pivot": atz_pivot,
        "atz": events_df[events_df["reason_code"].isin([REASON_ATZ_AR_TO_FR, REASON_ATZ_END])] if not events_df.empty else pd.DataFrame(),
        "retirement": events_df[events_df["reason_code"] == REASON_RETIREMENT] if not events_df.empty else pd.DataFrame(),
        "quit": events_df[events_df["reason_code"] == REASON_QUIT] if not events_df.empty else pd.DataFrame(),
        "ruhend": events_df[events_df["reason_code"].isin([REASON_RUHEND_START, REASON_RUHEND_RETURN])] if not events_df.empty else pd.DataFrame(),
    }

    return {
        "forecast_kpis": forecast_kpis,
        "events_person_level": events_df,
        "assumptions": params,
        "tables": tables,
    }
