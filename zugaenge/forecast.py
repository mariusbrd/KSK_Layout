"""
Forecast engine for Zugaenge (New Hires).
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

# Re-use Abgaenge constants/logic where useful
# But clean separation is better.
# We expect df_ma to have columns: PersNr, Eintritt, TrfGr, Organisationseinheit, Jobfamily.

@dataclass
class PeriodInfo:
    start: pd.Timestamp
    end: pd.Timestamp
    label: str

def _annual_to_period_count(annual_count: float, period_days: float, rng: np.random.RandomState) -> int:
    expected = annual_count * (period_days / 365.25)
    return int(rng.poisson(lam=expected)) if expected > 0 else 0

def _get_random_org_unit(all_org_units: List[str], rng: np.random.RandomState) -> str:
    if not all_org_units:
        return "Unbekannt"
    return str(rng.choice(all_org_units))

def _resolve_org_unit(
    strategy: str,
    target_unit: Optional[str],
    all_org_units: List[str],
    vacancies: List[Dict[str, Any]],
    rng: np.random.RandomState
) -> str:
    """
    Determines the target OrgUnit based on strategy.
    Strategies: "Random", "OrgUnit", "Fill Vacancies".
    """
    if strategy == "OrgUnit" and target_unit:
        return target_unit
    
    if strategy == "Fill Vacancies" and vacancies:
        # Take the first vacancy (FIFO)
        # We assume the caller handles removing it from the list if 'filled'
        # But here we just inspect.
        pass # Caller logic needed to consume vacancy.
        
    return _get_random_org_unit(all_org_units, rng)

def _simulate_azubis(
    df_state: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
    events: List[Dict[str, Any]],
    all_org_units: List[str]
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

    # Identify Azubis: TrfGr starts with "TVA" (TVAöD) or Jobfamily="Azubi"
    # We use a robust check
    mask_azubi = (
        df_state["TrfGr"].astype(str).str.contains("TVA", na=False, case=False) |
        (df_state["Jobfamily"].astype(str).str.contains("Azubi", na=False, case=False)) |
        (df_state["Jobfamily"].astype(str).str.contains("Ausbildung", na=False, case=False))
    )
    
    # Only consider those currently active and modeled as Azubis
    # We need a marker for "Is Azubi". 
    # If we don't track it, we might re-process them.
    # We'll use 'Jobfamily' as the definitive marker in the simulation.
    if "Jobfamily" not in df_state.columns:
        df_state["Jobfamily"] = "Unbekannt"
        
    # Standardize Azubis to Jobfamily="Azubi" if they match criteria and aren't already processed
    df_state.loc[mask_azubi & (df_state["Jobfamily"] != "Azubi"), "Jobfamily"] = "Azubi"

    current_azubis = df_state[
        (df_state["active"] == True) & 
        (df_state["Jobfamily"] == "Azubi")
    ]

    for persnr, row in current_azubis.iterrows():
        # Calculate graduation date
        eintritt = pd.to_datetime(row.get("Eintritt", pd.NaT))
        if pd.isna(eintritt):
            continue # Can't determine graduation
            
        graduation_date = eintritt + pd.DateOffset(years=int(duration_years)) + pd.DateOffset(months=int((duration_years % 1) * 12))
        
        # Check if graduation is in this period
        if period.start <= graduation_date <= period.end:
            # Decisions: Retain?
            if rng.random() < retention_rate:
                # Retained
                new_unit = _resolve_org_unit(strategy, target_unit, all_org_units, [], rng)
                
                # Update State
                df_state.loc[persnr, "Jobfamily"] = "Angestellte" # Graduate
                df_state.loc[persnr, "Organisationseinheit"] = new_unit
                # Reset Salary to Configured Entry (Assumption for ex-Azubi)
                df_state.loc[persnr, "TrfGr"] = entry_tariff
                df_state.loc[persnr, "St"] = entry_step
                
                events.append({
                    "date": graduation_date,
                    "type": "Azubi_Takeover",
                    "count": 1,
                    "persnr": persnr,
                    "org_unit": new_unit,
                    "source": "Azubi",
                    "mak": 1.0,
                    "TrfGr": entry_tariff,
                    "St": entry_step,
                    "Jobfamily": "Angestellte"
                })
            else:
                # Not retained - Exit
                df_state.loc[persnr, "active"] = False
                events.append({
                    "date": graduation_date,
                    "type": "Azubi_Exit",
                    "count": -1,
                    "persnr": persnr,
                    "org_unit": row.get("Organisationseinheit"),
                    "mak": -float(row.get("mak", 1.0)),
                    "source": "Azubi"
                })

def _simulate_trainees(
    df_state: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
    events: List[Dict[str, Any]],
    all_org_units: List[str]
):
    trainee_params = params.get("trainee", {})
    count_annual = float(trainee_params.get("new_cases_per_year", 0))
    salary_group = trainee_params.get("salary_group", "E13")
    strategy = trainee_params.get("strategy", "Random")
    target_unit = trainee_params.get("target_org_unit", None)
    
    period_days = (period.end - period.start).days
    num_cases = _annual_to_period_count(count_annual, period_days, rng)
    
    for _ in range(num_cases):
        # Generate new Trainee
        new_id = f"TR_{rng.randint(10000, 99999)}"
        # Check collision? Unlikely with prefix.
        
        org_unit = _resolve_org_unit(strategy, target_unit, all_org_units, [], rng)
        entry_date = period.start + pd.Timedelta(days=rng.randint(0, period_days))
        
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
            "age": 25.0,
            "tenure": 0.0,
            "mak": 1.0 # Full FTE
        }
        
        # Append logic (slow for dataframe, but okay for simulation step)
        # Better: Collect new rows and concat once per period. 
        # But we modify df_state in place for Azubis.
        # We'll rely on calling function to concat new hires.
        events.append({
            "date": entry_date,
            "type": "Trainee_Hire",
            "count": 1,
            "persnr": new_id,
            "org_unit": org_unit,
            "source": "Trainee",
            "mak": 1.0,
            "TrfGr": salary_group,
            "St": 1,
            "Jobfamily": "Trainee",
            "_new_row": new_row # Marker to add to DF later
        })

def _simulate_hires(
    df_state: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
    events: List[Dict[str, Any]],
    all_org_units: List[str],
    vacancies: List[Dict[str, Any]]
):
    hire_params = params.get("new_hires", {})
    count_annual = float(hire_params.get("count_per_year", 0))
    strategy = hire_params.get("strategy", "Fill Vacancies")
    target_unit = hire_params.get("target_org_unit", None)
    
    period_days = (period.end - period.start).days
    num_cases = _annual_to_period_count(count_annual, period_days, rng)
    
    for _ in range(num_cases):
        new_id = f"NH_{rng.randint(10000, 99999)}"
        entry_date = period.start + pd.Timedelta(days=rng.randint(0, period_days))
        
        # Determine OrgUnit
        org_unit = "Unbekannt"
        
        if strategy == "Fill Vacancies" and vacancies:
            # Find a matching vacancy (e.g. earliest date)
            # Filter vacancies that happened *before* entry_date to be realistic?
            # Or just take from the pool.
            # Vacancies list should be sorted.
            valid_vacancies = [v for v in vacancies if v["date"] <= entry_date]
            if valid_vacancies:
                vacancy = valid_vacancies.pop(0) # Consume
                vacancies.remove(vacancy) # Remove from main list too
                org_unit = vacancy["org_unit"]
            else:
                # No vacancy available yet? Fallback to random
                org_unit = _get_random_org_unit(all_org_units, rng)
        else:
            org_unit = _resolve_org_unit(strategy, target_unit, all_org_units, [], rng)

        new_row = {
            "PersNr": new_id,
            "active": True,
            "Jobfamily": "Angestellte",
            "TrfGr": "E9A", # Default
            "St": 3, # Experienced hire?
            "Organisationseinheit": org_unit,
            "Eintritt": entry_date,
            "GebDatum": entry_date - pd.DateOffset(years=30),
            "age": 30.0,
            "tenure": 0.0,
            "mak": 1.0
        }
        
        events.append({
            "date": entry_date,
            "type": "New_Hire",
            "count": 1,
            "persnr": new_id,
            "org_unit": org_unit,
            "source": "NewHire",
            "mak": 1.0,
            "TrfGr": "E9A",
            "St": 3,
            "Jobfamily": "Angestellte",
            "_new_row": new_row
        })

def run_forecast_zugaenge(
    df_snapshot: pd.DataFrame,
    start_date: pd.Timestamp,
    periods_years: int = 3,
    params: Dict[str, Any] = None,
    vacancies: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    
    if params is None:
        from .params import default_params
        params = default_params()
        
    rng = np.random.RandomState(params.get("random_seed", 42))
    
    # Prepare State
    df_state = df_snapshot.copy()
    if "active" not in df_state.columns:
        df_state["active"] = True # Assume snapshot is all active
        
    # Get all OrgUnits for random assignment
    all_org_units = []
    if "Organisationseinheit" in df_state.columns:
        all_org_units = df_state["Organisationseinheit"].dropna().unique().tolist()

    # Generate Periods (Monthly)
    end_date = start_date + pd.DateOffset(years=periods_years)
    period_range = pd.period_range(start=start_date, end=end_date, freq="M")
    
    all_events = []
    
    # Mutable vacancy list
    current_vacancies = sorted(vacancies, key=lambda x: x["date"]) if vacancies else []

    for p in period_range:
        period = PeriodInfo(p.start_time, p.end_time, p.strftime("%Y-%m"))
        period_events = []
        
        # 1. Azubis
        _simulate_azubis(df_state, params, period, rng, period_events, all_org_units)

        # 2. Trainees
        _simulate_trainees(df_state, params, period, rng, period_events, all_org_units)
        
        # 3. New Hires
        _simulate_hires(df_state, params, period, rng, period_events, all_org_units, current_vacancies)
        
        # Apply New Rows
        new_rows = [e["_new_row"] for e in period_events if "_new_row" in e]
        if new_rows:
            df_new = pd.DataFrame(new_rows)
            df_new.set_index("PersNr", drop=False, inplace=True) # Ensure Index matches
            # Align columns
            for col in df_state.columns:
                if col not in df_new.columns:
                    df_new[col] = pd.NA
            
            df_state = pd.concat([df_state, df_new])
            
        # Cleanup _new_row from events before saving
        for e in period_events:
            e.pop("_new_row", None)
            all_events.append(e)

    # Result
    events_df = pd.DataFrame(all_events)
    
    # Calculate Time Series KPIs (Headcount/MAK Evolution)
    # Start Stats
    start_hc = df_snapshot["active"].sum() if "active" in df_snapshot.columns else len(df_snapshot)
    start_mak = df_snapshot["mak"].sum() if "mak" in df_snapshot.columns else start_hc # Fallback

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

    return {
        "events": events_df,
        "final_state": df_state,
        "forecast_kpis": forecast_kpis
    }
