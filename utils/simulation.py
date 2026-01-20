"""
Workforce Simulation & Forecasting Engine.

Simuliert Personalentwicklung basierend auf:
- Renteneintritte (altersbasiert)
- Fluktuation (kohortenbasiert)
- Neueinstellungen (Hiring-Rate, Time-to-Fill)
- ATZ-Übergänge (Arbeitsphase → Freistellungsphase)
- Azubi-Übernahmen
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class SimulationParams:
    """Parameter für Workforce-Simulation."""

    # Zeithorizont
    horizon_months: int = 12
    start_date: datetime = None

    # Renteneintritte
    retirement_age: int = 67
    early_retirement_age: int = 63
    early_retirement_rate: float = 0.15  # 15% gehen mit 63

    # Fluktuation (pro Alterskohorte)
    attrition_rates: Dict[str, float] = None

    # Neueinstellungen
    hiring_enabled: bool = True
    hiring_rate: float = 0.30  # 30% der Vakanzen pro Monat
    time_to_fill_months: int = 3

    # ATZ-Übergänge
    atz_arbeitsphase_duration_months: int = 36  # 3 Jahre Arbeitsphase

    # Azubi-Übernahme
    azubi_takeover_rate: float = 0.85  # 85% Übernahmequote
    azubi_duration_months: int = 36  # 3 Jahre Ausbildung

    # Monte-Carlo
    enable_monte_carlo: bool = False
    monte_carlo_iterations: int = 100
    random_seed: int = 42

    def __post_init__(self):
        """Initialisiere Defaults."""
        if self.start_date is None:
            self.start_date = datetime.now()

        if self.attrition_rates is None:
            # Default Fluktuationsraten nach Alterskohorte
            self.attrition_rates = {
                "Azubis": 0.12,           # 12% p.a.
                "Young Professionals": 0.10,
                "Mid Career": 0.06,
                "Senior": 0.04,
                "Pre-Retirement": 0.02,
                "Retirement Ready": 0.01
            }


def assign_age_cohort(age: float, cohort_definitions: Dict[str, Tuple[int, int]]) -> str:
    """
    Weist einem Alter eine Kohorte zu.

    Args:
        age: Alter in Jahren
        cohort_definitions: Dict mit {name: (min_age, max_age)}

    Returns:
        Kohorten-Name oder "Unknown"
    """
    if pd.isna(age):
        return "Unknown"

    for cohort_name, (min_age, max_age) in cohort_definitions.items():
        if min_age <= age <= max_age:
            return cohort_name

    return "Unknown"


def calculate_age_at_date(birth_date: datetime, reference_date: datetime) -> float:
    """
    Berechnet Alter zum Stichtag.

    Args:
        birth_date: Geburtsdatum
        reference_date: Stichtag

    Returns:
        Alter in Jahren (float)
    """
    if pd.isna(birth_date):
        return np.nan

    delta = relativedelta(reference_date, birth_date)
    return delta.years + delta.months / 12.0


def calculate_retirements(
    df: pd.DataFrame,
    current_date: datetime,
    params: SimulationParams,
    rng: np.random.Generator
) -> pd.DataFrame:
    """
    Berechnet Renteneintritte für den aktuellen Monat.

    Args:
        df: Aktueller Personalbestand
        current_date: Aktuelles Datum
        params: Simulationsparameter
        rng: Random Number Generator

    Returns:
        DataFrame mit Abgängen
    """
    df_active = df[~df["Is_Vacant"]].copy()

    # Berechne aktuelles Alter
    df_active["Current_Age"] = df_active["GebDatum"].apply(
        lambda x: calculate_age_at_date(x, current_date)
    )

    retirements = []

    for idx, row in df_active.iterrows():
        age = row["Current_Age"]

        if pd.isna(age):
            continue

        # Reguläre Rente mit 67
        if age >= params.retirement_age:
            retirements.append(idx)

        # Frührente mit 63+ (15% Wahrscheinlichkeit)
        elif age >= params.early_retirement_age:
            if rng.random() < params.early_retirement_rate / 12:  # Monatliche Rate
                retirements.append(idx)

    return df.loc[retirements].copy() if retirements else pd.DataFrame()


def calculate_attrition(
    df: pd.DataFrame,
    current_date: datetime,
    params: SimulationParams,
    cohort_definitions: Dict[str, Tuple[int, int]],
    rng: np.random.Generator
) -> pd.DataFrame:
    """
    Berechnet Fluktuation für den aktuellen Monat.

    Args:
        df: Aktueller Personalbestand
        current_date: Aktuelles Datum
        params: Simulationsparameter
        cohort_definitions: Kohorten-Definitionen
        rng: Random Number Generator

    Returns:
        DataFrame mit Abgängen
    """
    df_active = df[~df["Is_Vacant"]].copy()

    # Berechne aktuelles Alter und Kohorte
    df_active["Current_Age"] = df_active["GebDatum"].apply(
        lambda x: calculate_age_at_date(x, current_date)
    )
    df_active["Age_Cohort"] = df_active["Current_Age"].apply(
        lambda x: assign_age_cohort(x, cohort_definitions)
    )

    attritions = []

    for idx, row in df_active.iterrows():
        cohort = row["Age_Cohort"]

        if cohort == "Unknown":
            continue

        # Jahresrate auf Monatsrate umrechnen
        annual_rate = params.attrition_rates.get(cohort, 0.05)
        monthly_rate = annual_rate / 12

        if rng.random() < monthly_rate:
            attritions.append(idx)

    return df.loc[attritions].copy() if attritions else pd.DataFrame()


def calculate_atz_transitions(
    df: pd.DataFrame,
    current_date: datetime,
    params: SimulationParams
) -> pd.DataFrame:
    """
    Berechnet ATZ-Übergänge von Arbeitsphase → Freistellungsphase.

    Args:
        df: Aktueller Personalbestand
        current_date: Aktuelles Datum
        params: Simulationsparameter

    Returns:
        DataFrame mit Übergängen (Planstellennr)
    """
    # Finde alle in ATZ-Arbeitsphase
    df_atz = df[
        (~df["Is_Vacant"]) &
        (df["Vertragsart"] == "Altersteilzeit") &
        (df.get("ATZ_Phase", "") == "Arbeitsphase")
    ].copy()

    transitions = []

    for idx, row in df_atz.iterrows():
        # Prüfe ob Arbeitsphase abgelaufen
        # (Vereinfachung: Wenn Eintritt + 36 Monate <= current_date)
        if pd.notna(row.get("Eintritt")):
            atz_start = row["Eintritt"]
            arbeitsphase_end = atz_start + relativedelta(months=params.atz_arbeitsphase_duration_months)

            if current_date >= arbeitsphase_end:
                transitions.append(row["Planstellennr"])

    return pd.DataFrame({"Planstellennr": transitions}) if transitions else pd.DataFrame()


def calculate_azubi_takeovers(
    df: pd.DataFrame,
    current_date: datetime,
    params: SimulationParams,
    rng: np.random.Generator
) -> List[Dict]:
    """
    Berechnet Azubi-Übernahmen nach Ausbildungsende.

    Args:
        df: Aktueller Personalbestand
        current_date: Aktuelles Datum
        params: Simulationsparameter
        rng: Random Number Generator

    Returns:
        Liste von Dicts mit neuen Mitarbeitenden
    """
    # Finde Azubis die Ausbildung beenden
    df_azubis = df[
        (~df["Is_Vacant"]) &
        (df.get("Tarifarttext", "").str.contains("Auszubildende", case=False, na=False))
    ].copy()

    takeovers = []

    for idx, row in df_azubis.iterrows():
        if pd.notna(row.get("Eintritt")):
            ausbildung_end = row["Eintritt"] + relativedelta(months=params.azubi_duration_months)

            # Prüfe ob Ausbildung endet in diesem Monat
            if ausbildung_end.year == current_date.year and ausbildung_end.month == current_date.month:
                # Übernahme-Wahrscheinlichkeit
                if rng.random() < params.azubi_takeover_rate:
                    takeovers.append({
                        "Personalnummer": row["Personalnummer"],
                        "Name": f"Azubi_Übernahme_{row['Personalnummer']}",
                        "Organisationseinheit": row["Organisationseinheit"],
                        "Takeover_Date": current_date
                    })

    return takeovers


def calculate_hires(
    df: pd.DataFrame,
    current_date: datetime,
    params: SimulationParams,
    hiring_pipeline: List[Dict],
    rng: np.random.Generator
) -> Tuple[List[Dict], List[Dict]]:
    """
    Berechnet Neueinstellungen basierend auf Vakanzen und Time-to-Fill.

    Args:
        df: Aktueller Personalbestand
        current_date: Aktuelles Datum
        params: Simulationsparameter
        hiring_pipeline: Laufende Einstellungsprozesse
        rng: Random Number Generator

    Returns:
        Tuple: (neue_mitarbeitende, aktualisierte_pipeline)
    """
    if not params.hiring_enabled:
        return [], hiring_pipeline

    new_hires = []
    updated_pipeline = []

    # Prüfe Pipeline auf fertige Einstellungen
    for process in hiring_pipeline:
        if current_date >= process["expected_start_date"]:
            # Einstellung abgeschlossen
            new_hires.append({
                "Planstellennr": process["planstellennr"],
                "Personalnummer": process["personalnummer"],
                "Start_Date": current_date,
                "Organisationseinheit": process["org_einheit"]
            })
        else:
            # Noch in Pipeline
            updated_pipeline.append(process)

    # Starte neue Einstellungsprozesse für Vakanzen
    vacancies = df[df["Is_Vacant"]].copy()

    for idx, row in vacancies.iterrows():
        # Prüfe ob bereits in Pipeline
        already_in_pipeline = any(
            p["planstellennr"] == row["Planstellennr"]
            for p in updated_pipeline
        )

        if already_in_pipeline:
            continue

        # Starte Einstellungsprozess mit Wahrscheinlichkeit = hiring_rate
        if rng.random() < params.hiring_rate:
            expected_start = current_date + relativedelta(months=params.time_to_fill_months)

            updated_pipeline.append({
                "planstellennr": row["Planstellennr"],
                "personalnummer": rng.integers(100000, 999999),
                "org_einheit": row["Organisationseinheit"],
                "start_date": current_date,
                "expected_start_date": expected_start
            })

    return new_hires, updated_pipeline


def simulate_month(
    df: pd.DataFrame,
    current_date: datetime,
    params: SimulationParams,
    cohort_definitions: Dict[str, Tuple[int, int]],
    hiring_pipeline: List[Dict],
    rng: np.random.Generator
) -> Tuple[pd.DataFrame, List[Dict], Dict]:
    """
    Simuliert einen einzelnen Monat.

    Args:
        df: Aktueller Personalbestand
        current_date: Aktuelles Datum
        params: Simulationsparameter
        cohort_definitions: Kohorten-Definitionen
        hiring_pipeline: Laufende Einstellungsprozesse
        rng: Random Number Generator

    Returns:
        Tuple: (neuer_bestand, neue_pipeline, events)
    """
    df_new = df.copy()
    events = {
        "retirements": 0,
        "attrition": 0,
        "atz_transitions": 0,
        "azubi_takeovers": 0,
        "hires": 0
    }

    # 1. Renteneintritte
    retirements = calculate_retirements(df_new, current_date, params, rng)
    if not retirements.empty:
        # Setze Austritt und markiere als vakant
        for idx in retirements.index:
            df_new.loc[idx, "Austritt"] = current_date
            df_new.loc[idx, "Is_Vacant"] = True
            df_new.loc[idx, "Personalnummer"] = np.nan
            df_new.loc[idx, "FTE_assigned"] = 0.0
        events["retirements"] = len(retirements)

    # 2. Fluktuation
    attritions = calculate_attrition(df_new, current_date, params, cohort_definitions, rng)
    if not attritions.empty:
        for idx in attritions.index:
            df_new.loc[idx, "Austritt"] = current_date
            df_new.loc[idx, "Is_Vacant"] = True
            df_new.loc[idx, "Personalnummer"] = np.nan
            df_new.loc[idx, "FTE_assigned"] = 0.0
        events["attrition"] = len(attritions)

    # 3. ATZ-Übergänge
    atz_transitions = calculate_atz_transitions(df_new, current_date, params)
    if not atz_transitions.empty:
        for planstellennr in atz_transitions["Planstellennr"]:
            mask = df_new["Planstellennr"] == planstellennr
            df_new.loc[mask, "ATZ_Phase"] = "Freistellungsphase"
            # In Freistellung: FTE = 0
            df_new.loc[mask, "FTE_assigned"] = 0.0
        events["atz_transitions"] = len(atz_transitions)

    # 4. Azubi-Übernahmen
    takeovers = calculate_azubi_takeovers(df_new, current_date, params, rng)
    events["azubi_takeovers"] = len(takeovers)
    # Note: Azubi-Übernahmen würden neue Planstellen benötigen - hier vereinfacht weggelassen

    # 5. Neueinstellungen
    new_hires, hiring_pipeline = calculate_hires(df_new, current_date, params, hiring_pipeline, rng)
    if new_hires:
        for hire in new_hires:
            mask = df_new["Planstellennr"] == hire["Planstellennr"]
            df_new.loc[mask, "Is_Vacant"] = False
            df_new.loc[mask, "Personalnummer"] = hire["Personalnummer"]
            # FTE = Soll_FTE (Vereinfachung: Vollzeit-Übernahme)
            df_new.loc[mask, "FTE_assigned"] = df_new.loc[mask, "Soll_FTE"]
            df_new.loc[mask, "Eintritt"] = hire["Start_Date"]
        events["hires"] = len(new_hires)

    return df_new, hiring_pipeline, events


def simulate_workforce(
    base_df: pd.DataFrame,
    params: SimulationParams,
    cohort_definitions: Dict[str, Tuple[int, int]]
) -> pd.DataFrame:
    """
    Simuliert Personalentwicklung über den definierten Horizont.

    Args:
        base_df: Ausgangsdatensatz (Snapshot)
        params: Simulationsparameter
        cohort_definitions: Kohorten-Definitionen

    Returns:
        DataFrame mit monatlichen Snapshots
    """
    rng = np.random.default_rng(params.random_seed)

    current_df = base_df.copy()
    current_date = params.start_date
    hiring_pipeline = []

    results = []

    for month in range(params.horizon_months + 1):
        # Berechne Kennzahlen
        snapshot = {
            "Date": current_date,
            "Month": month,
            "Headcount": current_df[~current_df["Is_Vacant"]]["Personalnummer"].nunique(),
            "FTE": current_df[~current_df["Is_Vacant"]]["FTE_assigned"].sum(),
            "Vacancies": current_df["Is_Vacant"].sum(),
            "Total_Cost": current_df[~current_df["Is_Vacant"]]["Total_Cost_Year"].sum(),
            "Pipeline_Size": len(hiring_pipeline)
        }

        results.append(snapshot)

        # Simuliere nächsten Monat
        if month < params.horizon_months:
            current_df, hiring_pipeline, events = simulate_month(
                current_df,
                current_date,
                params,
                cohort_definitions,
                hiring_pipeline,
                rng
            )

            # Füge Events hinzu
            results[-1].update(events)

            # Nächster Monat
            current_date = current_date + relativedelta(months=1)

    return pd.DataFrame(results)


def run_monte_carlo(
    base_df: pd.DataFrame,
    params: SimulationParams,
    cohort_definitions: Dict[str, Tuple[int, int]]
) -> Dict[str, pd.DataFrame]:
    """
    Führt Monte-Carlo-Simulation mit mehreren Iterationen durch.

    Args:
        base_df: Ausgangsdatensatz
        params: Simulationsparameter
        cohort_definitions: Kohorten-Definitionen

    Returns:
        Dict mit:
            - "mean": Durchschnittliche Entwicklung
            - "p10": 10. Perzentil
            - "p90": 90. Perzentil
            - "all_runs": Alle Einzelläufe
    """
    all_runs = []

    for iteration in range(params.monte_carlo_iterations):
        # Neuer Seed für jede Iteration
        iter_params = SimulationParams(
            horizon_months=params.horizon_months,
            start_date=params.start_date,
            retirement_age=params.retirement_age,
            early_retirement_age=params.early_retirement_age,
            early_retirement_rate=params.early_retirement_rate,
            attrition_rates=params.attrition_rates,
            hiring_enabled=params.hiring_enabled,
            hiring_rate=params.hiring_rate,
            time_to_fill_months=params.time_to_fill_months,
            random_seed=params.random_seed + iteration
        )

        result = simulate_workforce(base_df, iter_params, cohort_definitions)
        result["iteration"] = iteration
        all_runs.append(result)

    # Kombiniere alle Runs
    combined = pd.concat(all_runs, ignore_index=True)

    # Berechne Statistiken pro Monat
    grouped = combined.groupby("Month")

    mean_df = grouped.agg({
        "Date": "first",
        "Headcount": "mean",
        "FTE": "mean",
        "Vacancies": "mean",
        "Total_Cost": "mean"
    }).reset_index()

    p10_df = grouped.agg({
        "Date": "first",
        "Headcount": lambda x: np.percentile(x, 10),
        "FTE": lambda x: np.percentile(x, 10),
        "Vacancies": lambda x: np.percentile(x, 10),
        "Total_Cost": lambda x: np.percentile(x, 10)
    }).reset_index()

    p90_df = grouped.agg({
        "Date": "first",
        "Headcount": lambda x: np.percentile(x, 90),
        "FTE": lambda x: np.percentile(x, 90),
        "Vacancies": lambda x: np.percentile(x, 90),
        "Total_Cost": lambda x: np.percentile(x, 90)
    }).reset_index()

    return {
        "mean": mean_df,
        "p10": p10_df,
        "p90": p90_df,
        "all_runs": combined
    }
