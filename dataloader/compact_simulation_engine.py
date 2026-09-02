"""
Simulation helpers for "Kompakt plus Simulation".

This module reuses the existing attrition and accession forecast engines to
project a future personnel snapshot that can be consumed by the Kompakt page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import streamlit as st

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as default_abgaenge_params
from dataloader.cluster_manager import (
    apply_clusters_to_snapshot_from_source,
    load_cluster_mappings_from_source,
)
from dataloader.cluster_resolver import (
    ActiveClusterSource,
    ClusterMappingBundle,
    get_active_cluster_source,
)
from dataloader.loader import (
    _zero_out_azubi_mak,
    apply_exclusions,
    calculate_cost_vectorized,
    calculate_mak_vectorized,
    enrich_snapshot_data,
    normalize_education_series,
)
from dataloader.mak_allocation import apply_person_mak_allocation
from dataloader.mak_allocation import build_mak_allocation_audit
from kpi_reference import get_current_stichtag
from utils.settings_loader import get_setting
from utils.audit_helpers import (
    build_65plus_audit,
    build_65plus_decision_list,
    build_azubi_flow_audit,
    build_azubi_takeover_decision_list,
    build_azubi_takeover_target_audit,
    build_mak_decision_list,
    build_mak_abgaenge_check,
    build_mak_column_consistency_check,
    build_mak_correction_decision_15,
    build_mak_deep_dive_15_cases,
    build_mak_fuehrung_vertrieb_decision,
    build_mak_fuehrung_vertrieb_check,
    build_mak_lineage_audit,
    build_mak_origin_classification,
    build_mak_person_audit,
    build_mak_policy_impact,
    build_mak_reconciliation_bridge,
    build_mak_source_pattern_classification,
    build_mak_source_row_audit_15,
    build_mak_target_value_scenarios,
    build_mak_zugaenge_check,
    build_sonstiges_audit,
)
from zugaenge.enrichment import build_jf_to_cluster_map, enrich_zugaenge_events
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as default_zugaenge_params


SALARY_AUTOMATION_DEFAULTS = {
    "enabled": False,
    "scope": "new_hires_only",
    "fallback_step": 4,
    "e1_entry_step": 2,
    "e2_plus_default_entry_step": 1,
    "e1_progression_years": [4, 4, 4, 4],
    "e2_plus_progression_years": [1, 2, 3, 4, 5],
    "use_tenure_as_step_proxy_for_existing_staff": False,
}

SALARY_AUTOMATION_EVENT_TYPES = {"New_Hire", "Trainee_Hire", "Azubi_Conversion_In"}

PERSON_CLEAR_FIELDS = [
    "Personalnummer",
    "PersNr",
    "Personalnachname",
    "Personalvorname",
    "Name",
    "Vorname",
    "Nachname",
    "GebDatum",
    "Eintritt",
    "Austritt",
    "Alter",
    "Alter_Jahre",
    "ATZ_Status",
    "Phase",
    "ist_atz_fr",
]

NUMERIC_ZERO_FIELDS = [
    "MAK",
    "mak",
    "MAK_Calculated",
    "BsGrd",
    "FTE_person",
    "FTE_assigned",
    "Total_Cost_Year",
]

TRAINEE_EDUCATION_POOL = [
    "Bachelor FH",
    "Bachelor Universität",
    "Master FH",
    "Studium Lehrinstitut",
    "Master Universität",
    "Bankbetriebswirt",
]


@dataclass
class CompactSimulationResult:
    future_snapshot_df: pd.DataFrame
    future_employee_df: pd.DataFrame
    abgaenge_result: Dict[str, Any]
    zugaenge_result: Dict[str, Any]
    metadata: Dict[str, Any]
    audit_tables: Dict[str, pd.DataFrame] = field(default_factory=dict)


def _normalize_tariff_group(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _clamp_step(value: Any, fallback_step: int) -> int:
    try:
        step = int(float(value))
    except (TypeError, ValueError):
        step = int(fallback_step)
    return max(1, min(6, step))


def _get_salary_automation_settings() -> Dict[str, Any]:
    settings = get_setting("salary_automation", {}) or {}
    merged = {**SALARY_AUTOMATION_DEFAULTS, **settings}
    merged["e1_progression_years"] = list(merged.get("e1_progression_years", [4, 4, 4, 4]))
    merged["e2_plus_progression_years"] = list(merged.get("e2_plus_progression_years", [1, 2, 3, 4, 5]))
    return merged


def _build_salary_step_anchor_map(events_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    if events_df.empty or "type" not in events_df.columns or "persnr" not in events_df.columns:
        return {}

    anchors = events_df[events_df["type"].isin(SALARY_AUTOMATION_EVENT_TYPES)].copy()
    if anchors.empty:
        return {}

    anchors["persnr"] = _normalize_persnr_series(anchors["persnr"])
    anchors["date"] = pd.to_datetime(anchors["date"], errors="coerce")
    anchors = anchors.dropna(subset=["persnr", "date"])
    if anchors.empty:
        return {}

    anchors = anchors.sort_values(["persnr", "date"])
    anchor_map: Dict[str, Dict[str, Any]] = {}
    for _, row in anchors.iterrows():
        pid = str(row["persnr"])
        anchor_map[pid] = {
            "date": row["date"],
            "step": row.get("St"),
            "tariff": row.get("TrfGr"),
            "type": row.get("type"),
        }
    return anchor_map


def _progress_step_from_anchor(
    start_step: int,
    years_since_start: float,
    progression_years: list[int],
    base_step: int,
) -> int:
    current_step = int(start_step)
    elapsed_required = 0.0

    while current_step < 6:
        idx = current_step - base_step
        if idx < 0 or idx >= len(progression_years):
            break
        elapsed_required += float(progression_years[idx])
        if years_since_start + 1e-9 >= elapsed_required:
            current_step += 1
        else:
            break

    return max(base_step, min(6, current_step))


def _apply_salary_automation_to_employee_state(
    employee_df: pd.DataFrame,
    target_date: pd.Timestamp,
    events_df: pd.DataFrame,
) -> pd.DataFrame:
    if employee_df.empty or "PersNr" not in employee_df.columns:
        return employee_df

    settings = _get_salary_automation_settings()
    if not bool(settings.get("enabled", False)):
        return employee_df

    out = employee_df.copy()
    out["PersNr"] = _normalize_persnr_series(out["PersNr"])
    out["Eintritt"] = pd.to_datetime(out.get("Eintritt"), errors="coerce")

    fallback_step = int(settings.get("fallback_step", 4))
    anchor_map = _build_salary_step_anchor_map(events_df)
    scope = str(settings.get("scope", "new_hires_only"))
    use_existing_proxy = bool(settings.get("use_tenure_as_step_proxy_for_existing_staff", False))

    current_steps = pd.to_numeric(out.get("St"), errors="coerce").fillna(fallback_step).astype(int)
    tariffs = out.get("TrfGr", pd.Series("", index=out.index)).map(_normalize_tariff_group)

    new_steps = current_steps.copy()

    for idx in out.index:
        pid = out.at[idx, "PersNr"]
        tariff = tariffs.at[idx]
        if not tariff.startswith("E") or tariff.startswith("TVA"):
            continue

        is_e1 = tariff == "E1"
        base_step = 2 if is_e1 else 1
        entry_step_default = int(settings["e1_entry_step"] if is_e1 else settings["e2_plus_default_entry_step"])
        progression_years = settings["e1_progression_years"] if is_e1 else settings["e2_plus_progression_years"]

        current_step = _clamp_step(current_steps.at[idx], fallback_step)
        if current_step < base_step:
            current_step = entry_step_default

        anchor = anchor_map.get(pid)
        anchor_date = None
        start_step = None
        use_proxy_mode = False

        if anchor is not None:
            anchor_date = pd.to_datetime(anchor.get("date"), errors="coerce")
            start_step = _clamp_step(anchor.get("step"), entry_step_default)
            if start_step < base_step:
                start_step = entry_step_default
        elif scope == "all_staff" and use_existing_proxy:
            anchor_date = out.at[idx, "Eintritt"]
            start_step = entry_step_default
            use_proxy_mode = True
        else:
            continue

        if pd.isna(anchor_date):
            continue

        years_since_start = max(0.0, (target_date - anchor_date).days / 365.25)
        progressed_step = _progress_step_from_anchor(
            start_step=start_step,
            years_since_start=years_since_start,
            progression_years=progression_years,
            base_step=base_step,
        )

        if use_proxy_mode:
            new_steps.at[idx] = max(current_step, progressed_step)
        else:
            new_steps.at[idx] = max(current_step, progressed_step)

    out["St"] = new_steps.astype(int)
    return out


def _normalize_persnr_series(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def _sample_from_distribution(
    rng: np.random.Generator,
    choices: list[str],
    weights: list[float],
    fallback: str,
) -> str:
    if not choices or not weights:
        return fallback
    return str(rng.choice(choices, p=weights))


def _fill_missing_education_values(
    future_df: pd.DataFrame,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Fuellt verbleibende Ausbildungsluecken im Zukunftssnapshot deterministisch,
    damit die Qualifikationsdarstellung auf der Simulationsseite vollstaendig ist.
    """
    if "Ausbildung" not in future_df.columns or future_df.empty:
        return future_df

    out = future_df.copy()
    out["Ausbildung"] = normalize_education_series(out["Ausbildung"])

    occupied_mask = ~out.get("Is_Vacant", pd.Series(False, index=out.index)).fillna(False)
    education = out["Ausbildung"]
    missing_mask = occupied_mask & (
        education.isna() |
        education.astype(str).str.strip().isin(["", "nan"])
    )
    if not missing_mask.any():
        return out

    rng = np.random.default_rng(seed)

    known = out.loc[occupied_mask & ~missing_mask, "Ausbildung"].dropna().astype(str).str.strip()
    known_non_training = known[known.ne("derzeit Berufsausbildung")]
    if known_non_training.empty:
        general_choices = ["kfm Berufsabschluss"]
        general_weights = [1.0]
    else:
        general_counts = known_non_training.value_counts(normalize=True)
        general_choices = general_counts.index.tolist()
        general_weights = general_counts.values.astype(float).tolist()

    trainee_known = known_non_training[known_non_training.isin(TRAINEE_EDUCATION_POOL)]
    if trainee_known.empty:
        trainee_choices = ["Bachelor FH"]
        trainee_weights = [1.0]
    else:
        trainee_counts = trainee_known.value_counts(normalize=True)
        trainee_choices = trainee_counts.index.tolist()
        trainee_weights = trainee_counts.values.astype(float).tolist()

    jobfamily = out.get("Jobfamily", pd.Series("", index=out.index)).astype(str)
    mitarb = out.get("MitarbGruppenbez.", pd.Series("", index=out.index)).astype(str)
    trfgr = out.get("TrfGr", pd.Series("", index=out.index)).astype(str)

    azubi_mask = (
        mitarb.str.contains("Auszubild", case=False, na=False) |
        jobfamily.str.contains("Azubi|Ausbildung", case=False, na=False) |
        trfgr.str.contains("TVA", case=False, na=False)
    )
    trainee_mask = jobfamily.str.contains("Trainee", case=False, na=False)

    for idx in out.index[missing_mask]:
        if bool(azubi_mask.get(idx, False)):
            out.at[idx, "Ausbildung"] = "derzeit Berufsausbildung"
        elif bool(trainee_mask.get(idx, False)):
            out.at[idx, "Ausbildung"] = _sample_from_distribution(
                rng,
                trainee_choices,
                trainee_weights,
                "Bachelor FH",
            )
        else:
            out.at[idx, "Ausbildung"] = _sample_from_distribution(
                rng,
                general_choices,
                general_weights,
                "kfm Berufsabschluss",
            )

    out["Ausbildung"] = normalize_education_series(out["Ausbildung"])
    return out


def _resolve_simulation_cluster_context(
    active_cluster_source: Optional[ActiveClusterSource] = None,
    cluster_mapping_bundle: Optional[ClusterMappingBundle] = None,
    cluster_source_signature: Optional[str] = None,
) -> tuple[ActiveClusterSource, ClusterMappingBundle, Optional[str]]:
    """
    Resolve the simulation cluster context once.

    The preferred Patch-6 path passes an explicit active source and optional
    bundle/signature from the page. A narrow legacy fallback remains for older
    call sites until Patch 7 removes implicit resolution from this module.
    """
    resolved_source = active_cluster_source
    if resolved_source is None:
        resolved_source = get_active_cluster_source(session_state=getattr(st, "session_state", None))

    resolved_bundle = cluster_mapping_bundle or load_cluster_mappings_from_source(resolved_source)
    resolved_signature = cluster_source_signature or getattr(resolved_source, "source_signature", None)
    return resolved_source, resolved_bundle, resolved_signature


def _current_atz_fr_set(df_atz: pd.DataFrame, ref_date: pd.Timestamp) -> set[str]:
    if df_atz.empty or "PersNr" not in df_atz.columns or "Phase" not in df_atz.columns:
        return set()

    phase = df_atz["Phase"].astype(str).str.strip()
    start = pd.to_datetime(df_atz.get("Beginn"), errors="coerce")
    end = pd.to_datetime(df_atz.get("Ende"), errors="coerce")

    mask = (phase == "FR") & (start <= ref_date) & (end >= ref_date)
    return set(_normalize_persnr_series(df_atz.loc[mask, "PersNr"]))


def _prepare_employee_forecast_base(
    snapshot_df: pd.DataFrame,
    df_atz: pd.DataFrame,
    ref_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    Converts the position-based snapshot into the employee-level structure
    expected by the forecast engines.
    """
    df_ma = snapshot_df.copy()
    df_ma = df_ma.dropna(subset=["PersNr"]).copy()
    if df_ma.empty:
        return df_ma

    atz_fr_persnr_set = _current_atz_fr_set(df_atz, ref_date)
    df_ma = calculate_mak_vectorized(df_ma, atz_fr_persnr_set)

    agg_dict: Dict[str, Any] = {
        "MAK_Calculated": "sum",
        "Sollarbeitszeit": "sum",
        "Soll_FTE": "sum",
        "GebDatum": "first",
        "Eintritt": "first",
        "Austritt": "first",
        "Status kundenindividuell": "first",
        "Organisationseinheit": "first",
    }

    optional_first_cols = [
        "Geschlecht",
        "Planstelle",
        "Kürzel OrgEinheit",
        "ATZ_Status",
        "Jobfamily",
        "TrfGr",
        "St",
        "OE-Cluster",
        "JF-Cluster",
        "MitarbGruppenbez.",
        "Ausbildung",
        "Text Gsch",
        "Vertragsart",
        "Text Gehaltsband",
        "Bewertung Tarifgruppe",
        "BsGrd_Source",
        "snapshot_BsGrd",
        "Personen_MAK_Source",
        "Personen_MAK",
        "person_mak_source",
        "bsgrd_lineage_flag",
        "Planstellen_Soll_MAK",
        "MAK_Technical_Uncorrected",
        "MAK_Adjustment_Delta",
        "MAK_Allocation_Flag",
        "MAK_Allocation_Comment",
    ]
    for col in optional_first_cols:
        if col in df_ma.columns:
            agg_dict[col] = "first"

    # MAK_Reporting und Allocation_Weight sind bereits ueber apply_person_mak_allocation()
    # personenscharf gewichtet (Allocation_Weight summiert sich je Person auf 1,0) -- im
    # Gegensatz zu MAK_Calculated, das der rohe Zeilenwert je Planstelle ist. Bei Personen mit
    # mehreren aktiven Planstellen (BsGrd ist ein Personen-Attribut und steht unveraendert auf
    # jeder ihrer Zeilen) wuerde "MAK_Calculated": "sum" ihre Kapazitaet mehrfach zaehlen (z. B.
    # 2x100% BsGrd -> faelschlich 2,0 MAK statt real 1,0). Daher hier "sum" statt "first".
    if "MAK_Reporting" in df_ma.columns:
        agg_dict["MAK_Reporting"] = "sum"
    if "Allocation_Weight" in df_ma.columns:
        agg_dict["Allocation_Weight"] = "sum"

    emp_df = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
    emp_df["PersNr"] = _normalize_persnr_series(emp_df["PersNr"])
    if "Ausbildung" in emp_df.columns:
        emp_df["Ausbildung"] = normalize_education_series(emp_df["Ausbildung"])
    # Startkapazitaet bevorzugt aus der personenscharf gewichteten MAK_Reporting-Summe (siehe
    # oben); Fallback auf die rohe MAK_Calculated-Summe nur, falls MAK_Reporting fehlt (z. B.
    # unvollstaendige Upload-/Synthetik-Datenpfade ohne apply_person_mak_allocation()).
    if "MAK_Reporting" in emp_df.columns:
        emp_df["mak"] = pd.to_numeric(emp_df["MAK_Reporting"], errors="coerce").fillna(
            pd.to_numeric(emp_df["MAK_Calculated"], errors="coerce").fillna(0.0)
        )
    else:
        emp_df["mak"] = pd.to_numeric(emp_df["MAK_Calculated"], errors="coerce").fillna(0.0)
    emp_df["MAK_Calculated"] = emp_df["mak"]
    emp_df["Sollarbeitszeit"] = 39.0
    emp_df["BsGrd"] = emp_df["mak"] * 100.0
    emp_df["active"] = True
    emp_df["Is_Vacant"] = False
    return emp_df


def _apply_attrition_events_to_employee_state(
    employee_df: pd.DataFrame,
    events_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Replays attrition events onto the employee-level state to produce the
    employee base for the accession engine.
    """
    state = employee_df.copy()
    if state.empty:
        return state

    state["PersNr"] = _normalize_persnr_series(state["PersNr"])
    state = state.set_index("PersNr", drop=False)

    if "active" not in state.columns:
        state["active"] = True
    if "mak" not in state.columns:
        state["mak"] = pd.to_numeric(state.get("MAK_Calculated", 0.0), errors="coerce").fillna(0.0)

    state["status_ruhend"] = (
        state.get("Status kundenindividuell", pd.Series("", index=state.index))
        .astype(str)
        .str.strip()
        .eq("Ruhendes Beschäftigungsverhältnis")
    )
    if "atz_fr_active" not in state.columns:
        state["atz_fr_active"] = False

    if events_df.empty:
        return state.reset_index(drop=True)

    events = events_df.copy()
    events["persnr"] = _normalize_persnr_series(events["persnr"])
    events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce")
    events = events.sort_values("event_date")

    for _, event in events.iterrows():
        pid = event["persnr"]
        if pid not in state.index:
            continue

        headcount_change = int(event.get("headcount_change", 0) or 0)
        mak_change = float(event.get("mak_change", 0.0) or 0.0)
        reason_code = str(event.get("reason_code", "") or "")

        if headcount_change < 0:
            state.loc[pid, "active"] = False
            state.loc[pid, "mak"] = 0.0
        elif headcount_change > 0:
            state.loc[pid, "active"] = True
            state.loc[pid, "mak"] = max(0.0, float(state.loc[pid, "mak"]) + mak_change)
        else:
            state.loc[pid, "mak"] = max(0.0, float(state.loc[pid, "mak"]) + mak_change)

        if reason_code == "RUHEND_START":
            state.loc[pid, "status_ruhend"] = True
            if "Status kundenindividuell" in state.columns:
                state.loc[pid, "Status kundenindividuell"] = "Ruhendes Beschäftigungsverhältnis"
        elif reason_code == "RUHEND_RETURN":
            state.loc[pid, "status_ruhend"] = False
            if "Status kundenindividuell" in state.columns:
                state.loc[pid, "Status kundenindividuell"] = "Aktives Beschäftigungsverhältnis"
        elif reason_code == "ATZ_AR_TO_FR":
            state.loc[pid, "atz_fr_active"] = True

    return state.reset_index(drop=True)


def _build_vacancies_from_attrition(
    employee_df: pd.DataFrame,
    abgaenge_result: Dict[str, Any],
) -> list[Dict[str, Any]]:
    vacancies: list[Dict[str, Any]] = []
    events = abgaenge_result.get("events_person_level", pd.DataFrame())
    if events.empty:
        return vacancies

    exits = events[events.get("headcount_change", 0) < 0].copy()
    if exits.empty:
        return vacancies

    lookup = employee_df.copy()
    lookup["PersNr"] = _normalize_persnr_series(lookup["PersNr"])
    lookup = lookup.set_index("PersNr")

    for _, row in exits.iterrows():
        pid = _normalize_persnr_series(pd.Series([row.get("persnr", "")])).iloc[0]
        jobfamily = "Angestellte"
        oe_cluster = "Sonstiges"
        if pid in lookup.index:
            donor = lookup.loc[pid]
            if isinstance(donor, pd.DataFrame):
                donor = donor.iloc[0]
            jobfamily = donor.get("Jobfamily", jobfamily)
            oe_cluster = donor.get("OE-Cluster", oe_cluster)

        vacancies.append({
            "date": pd.to_datetime(row.get("event_date"), errors="coerce"),
            "org_unit": row.get("Organisationseinheit", "Unbekannt"),
            "planstelle": row.get("Planstelle", "Nachbesetzung"),
            "persnr": pid,
            "Jobfamily": jobfamily,
            "OE-Cluster": oe_cluster,
        })

    return vacancies


def _resolve_atz_status_map(atz_pivot: pd.DataFrame, target_date: pd.Timestamp) -> pd.DataFrame:
    if atz_pivot.empty:
        return pd.DataFrame(columns=["PersNr", "ATZ_Status", "ist_atz_fr"])

    atz = atz_pivot.copy()
    atz["PersNr"] = _normalize_persnr_series(atz["PersNr"])
    for col in ["ar_begin", "fr_begin", "contract_end"]:
        atz[col] = pd.to_datetime(atz.get(col), errors="coerce")

    atz["ATZ_Status"] = "Kein ATZ"
    ar_mask = (
        atz["ar_begin"].notna() &
        (atz["ar_begin"] <= target_date) &
        (
            atz["fr_begin"].isna() |
            (target_date < atz["fr_begin"])
        )
    )
    fr_mask = (
        atz["fr_begin"].notna() &
        (atz["fr_begin"] <= target_date) &
        (
            atz["contract_end"].isna() |
            (target_date <= atz["contract_end"])
        )
    )
    atz.loc[ar_mask, "ATZ_Status"] = "Arbeitsphase"
    atz.loc[fr_mask, "ATZ_Status"] = "Freistellungsphase"
    atz["ist_atz_fr"] = atz["ATZ_Status"].eq("Freistellungsphase")
    atz["status_rank"] = atz["ATZ_Status"].map({
        "Kein ATZ": 0,
        "Arbeitsphase": 1,
        "Freistellungsphase": 2,
    }).fillna(0)
    atz = (
        atz.sort_values(["PersNr", "status_rank"], ascending=[True, False])
        .drop_duplicates(subset=["PersNr"], keep="first")
    )
    return atz[["PersNr", "ATZ_Status", "ist_atz_fr"]]


def _vacate_rows(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    if not mask.any():
        return df

    out = df.copy()
    if "Is_Vacant" not in out.columns:
        out["Is_Vacant"] = False
    out.loc[mask, "Is_Vacant"] = True

    clear_cols = [col for col in PERSON_CLEAR_FIELDS if col in out.columns]
    for col in clear_cols:
        series = out[col]

        if pd.api.types.is_datetime64_any_dtype(series.dtype):
            out.loc[mask, col] = pd.NaT
            continue

        if str(series.dtype) == "boolean":
            out.loc[mask, col] = pd.NA
            continue

        if pd.api.types.is_bool_dtype(series.dtype):
            out.loc[mask, col] = False
            continue

        if pd.api.types.is_integer_dtype(series.dtype):
            out[col] = series.astype("Int64")
            out.loc[mask, col] = pd.NA
            continue

        out.loc[mask, col] = pd.NA

    zero_cols = [col for col in NUMERIC_ZERO_FIELDS if col in out.columns]
    out.loc[mask, zero_cols] = 0.0
    return out


def _distribute_value(total_value: float, weights: pd.Series) -> pd.Series:
    if len(weights) == 0:
        return pd.Series(dtype=float)

    weights = pd.to_numeric(weights, errors="coerce").fillna(0.0)
    if float(weights.sum()) <= 0:
        weights = pd.Series(1.0, index=weights.index)

    return (weights / float(weights.sum())) * float(total_value)


def _update_existing_rows(
    snapshot_df: pd.DataFrame,
    final_employee_df: pd.DataFrame,
    target_date: pd.Timestamp,
    atz_status_df: pd.DataFrame,
) -> pd.DataFrame:
    future_df = snapshot_df.copy()
    if future_df.empty:
        return future_df

    future_df["PersNr_norm"] = _normalize_persnr_series(future_df["PersNr"].fillna(""))
    final_df = final_employee_df.copy()
    final_df["PersNr"] = _normalize_persnr_series(final_df["PersNr"])
    final_df = final_df.drop_duplicates(subset=["PersNr"], keep="last")
    final_df = final_df.set_index("PersNr")

    if not atz_status_df.empty:
        atz_map = atz_status_df.drop_duplicates(subset=["PersNr"], keep="last").set_index("PersNr")
    else:
        atz_map = pd.DataFrame()

    occupied_mask = future_df["PersNr_norm"].ne("")
    existing_ids = set(future_df.loc[occupied_mask, "PersNr_norm"])
    active_ids = set(final_df[final_df.get("active", False) == True].index)
    inactive_existing_ids = existing_ids - active_ids

    if inactive_existing_ids:
        vacate_mask = future_df["PersNr_norm"].isin(inactive_existing_ids)
        future_df = _vacate_rows(future_df, vacate_mask)

    active_existing_ids = existing_ids & active_ids
    active_existing_mask = future_df["PersNr_norm"].isin(active_existing_ids)
    if active_existing_mask.any():
        active_person_ids = future_df.loc[active_existing_mask, "PersNr_norm"]
        future_df.loc[active_existing_mask, "Is_Vacant"] = False
        future_df.loc[active_existing_mask, "PersNr"] = active_person_ids.values
        if "Personalnummer" in future_df.columns:
            future_df.loc[active_existing_mask, "Personalnummer"] = active_person_ids.values

    atz_status_prefilled = "ATZ_Status" in future_df.columns
    ist_atz_fr_prefilled = "ist_atz_fr" in future_df.columns
    if active_existing_mask.any():
        if atz_status_prefilled:
            future_df.loc[active_existing_mask, "ATZ_Status"] = "Kein ATZ"
        if ist_atz_fr_prefilled:
            future_df.loc[active_existing_mask, "ist_atz_fr"] = False

    sync_cols = [
        "GebDatum",
        "Eintritt",
        "Austritt",
        "Organisationseinheit",
        "Jobfamily",
        "TrfGr",
        "St",
        "KÃ¼rzel OrgEinheit",
        "Geschlecht",
        "Text Gsch",
        "MitarbGruppenbez.",
        "Ausbildung",
        "Vertragsart",
        "Status kundenindividuell",
        "OE-Cluster",
        "JF-Cluster",
        "Personen_MAK",
        "Personen_MAK_Source",
        "BsGrd_Source",
        "snapshot_BsGrd",
        "person_mak_source",
        "bsgrd_lineage_flag",
    ]
    if active_existing_mask.any():
        active_target_ids = future_df.loc[active_existing_mask, "PersNr_norm"]
        for col in sync_cols:
            if col in future_df.columns and col in final_df.columns:
                sync_values = active_target_ids.map(final_df[col])
                if pd.api.types.is_datetime64_any_dtype(future_df[col]):
                    sync_values = pd.to_datetime(sync_values, errors="coerce")
                future_df.loc[active_existing_mask, col] = sync_values

    for pid in sorted(active_existing_ids):
        row_mask = future_df["PersNr_norm"] == pid
        if not row_mask.any():
            continue

        row_indices = future_df.index[row_mask]

        if pid in atz_map.index:
            future_df.loc[row_indices, "ATZ_Status"] = atz_map.loc[pid, "ATZ_Status"]
            future_df.loc[row_indices, "ist_atz_fr"] = bool(atz_map.loc[pid, "ist_atz_fr"])
        else:
            if not atz_status_prefilled and "ATZ_Status" in future_df.columns:
                future_df.loc[row_indices, "ATZ_Status"] = "Kein ATZ"
            if not ist_atz_fr_prefilled and "ist_atz_fr" in future_df.columns:
                future_df.loc[row_indices, "ist_atz_fr"] = False

    if active_existing_mask.any():
        ae = future_df.loc[active_existing_mask]
        mak_lookup = final_df["mak"].fillna(0.0) if "mak" in final_df.columns else pd.Series(0.0, index=final_df.index)
        total_mak = ae["PersNr_norm"].map(mak_lookup).fillna(0.0)
        if "Soll_FTE" in future_df.columns:
            weights = pd.to_numeric(ae["Soll_FTE"], errors="coerce").fillna(0.0)
            fte_sum = ae.groupby("PersNr_norm")["Soll_FTE"].transform(
                lambda x: float(pd.to_numeric(x, errors="coerce").fillna(0.0).sum())
            )
            count = ae.groupby("PersNr_norm")["PersNr_norm"].transform("count").astype(float)
            ratio = weights / fte_sum.where(fte_sum > 0, np.nan)
            ratio = ratio.fillna(1.0 / count)
            mak_per_row = total_mak * ratio
            fte_for_assigned = ae["Soll_FTE"].fillna(0.0)
        else:
            count = ae.groupby("PersNr_norm")["PersNr_norm"].transform("count").astype(float)
            mak_per_row = total_mak / count
            fte_for_assigned = None
        future_df.loc[active_existing_mask, "MAK_Calculated"] = mak_per_row.values
        future_df.loc[active_existing_mask, "mak"] = mak_per_row.values
        future_df.loc[active_existing_mask, "MAK"] = mak_per_row.values
        future_df.loc[active_existing_mask, "BsGrd"] = (mak_per_row * 100.0).round(6).values
        if "FTE_person" in future_df.columns:
            future_df.loc[active_existing_mask, "FTE_person"] = mak_per_row.values
        if fte_for_assigned is not None and "FTE_assigned" in future_df.columns:
            future_df.loc[active_existing_mask, "FTE_assigned"] = (mak_per_row * fte_for_assigned).values

    return future_df.drop(columns=["PersNr_norm"], errors="ignore")


def _append_new_people_rows(
    future_df: pd.DataFrame,
    base_snapshot_df: pd.DataFrame,
    final_employee_df: pd.DataFrame,
    zugaenge_result: Dict[str, Any],
) -> pd.DataFrame:
    existing_ids = set(_normalize_persnr_series(base_snapshot_df["PersNr"].dropna()))
    final_state = final_employee_df.copy()
    if final_state.empty or "PersNr" not in final_state.columns:
        return future_df

    final_state["PersNr"] = _normalize_persnr_series(final_state["PersNr"])
    final_state = final_state.drop_duplicates(subset=["PersNr"], keep="last").set_index("PersNr")
    active_mask = final_state.get("active", False) == True
    forecast_flag = (
        final_state.get("is_forecast", pd.Series(False, index=final_state.index))
        .replace({pd.NA: False, None: False})
        .infer_objects(copy=False)
        .astype(bool)
    )
    forecast_prefix = final_state.index.to_series().astype(str).str.match(r"^(AZ|TR|NH)_", na=False)
    final_new_people = final_state[active_mask & (forecast_flag | forecast_prefix)].copy()
    final_new_people = final_new_people[~final_new_people.index.isin(existing_ids)]
    if final_new_people.empty:
        return future_df

    events = zugaenge_result.get("events", pd.DataFrame())
    if not events.empty and "persnr" in events.columns:
        event_lookup = events.copy()
        event_lookup["persnr"] = _normalize_persnr_series(event_lookup["persnr"])
        event_lookup = event_lookup.drop_duplicates(subset=["persnr"], keep="last").set_index("persnr")
    else:
        event_lookup = pd.DataFrame()

    rows_to_append = []
    for pid, emp in final_new_people.sort_index().iterrows():
        if isinstance(emp, pd.DataFrame):
            emp = emp.iloc[0]
        if not pid or pid in existing_ids:
            continue

        if not event_lookup.empty and pid in event_lookup.index:
            event = event_lookup.loc[pid]
            if isinstance(event, pd.DataFrame):
                event = event.iloc[-1]
        else:
            event = pd.Series(dtype=object)

        candidate = {col: pd.NA for col in future_df.columns}
        candidate["PersNr"] = pid
        if "Personalnummer" in future_df.columns:
            candidate["Personalnummer"] = pid
        candidate["Is_Vacant"] = False
        candidate["Organisationseinheit"] = emp.get("Organisationseinheit", event.get("Organisationseinheit"))
        candidate["Planstelle"] = emp.get("Planstelle", event.get("Planstelle", "Simulation"))
        candidate["Jobfamily"] = emp.get("Jobfamily", event.get("Jobfamily", "Unbekannt"))
        candidate["OE-Cluster"] = emp.get("OE-Cluster", event.get("OE-Cluster", "Sonstiges"))
        candidate["JF-Cluster"] = emp.get("JF-Cluster", event.get("JF-Cluster", "Sonstiges"))
        candidate["TrfGr"] = emp.get("TrfGr", event.get("TrfGr", "E9A"))
        candidate["St"] = emp.get("St", event.get("St", 1))
        candidate["Eintritt"] = emp.get("Eintritt", event.get("date"))
        candidate["GebDatum"] = emp.get("GebDatum", pd.NaT)
        candidate["Status kundenindividuell"] = emp.get("Status kundenindividuell", "Aktives Beschäftigungsverhältnis")
        candidate["MitarbGruppenbez."] = emp.get("MitarbGruppenbez.", pd.NA)
        candidate["Geschlecht"] = emp.get("Geschlecht", pd.NA)
        candidate["Text Gsch"] = emp.get("Text Gsch", pd.NA)
        candidate["Ausbildung"] = emp.get("Ausbildung", pd.NA)
        candidate["Vertragsart"] = emp.get("Vertragsart", "Unbefristet")
        candidate["ATZ_Status"] = "Kein ATZ"
        candidate["ist_atz_fr"] = False
        candidate["Sollarbeitszeit"] = 39.0
        candidate["Soll_FTE"] = 1.0
        candidate["MAK_Calculated"] = float(emp.get("mak", emp.get("MAK_Calculated", event.get("mak", 0.0))) or 0.0)
        candidate["MAK"] = candidate["MAK_Calculated"]
        candidate["mak"] = candidate["MAK_Calculated"]
        candidate["BsGrd"] = float(emp.get("BsGrd", candidate["MAK_Calculated"] * 100.0) or 0.0)
        candidate["FTE_person"] = candidate["BsGrd"] / 100.0
        candidate["Personen_MAK"] = float(emp.get("Personen_MAK", candidate["FTE_person"]) or 0.0)
        candidate["Personen_MAK_Source"] = float(emp.get("Personen_MAK_Source", candidate["Personen_MAK"]) or 0.0)
        candidate["BsGrd_Source"] = float(emp.get("BsGrd_Source", candidate["Personen_MAK_Source"] * 100.0) or 0.0)
        candidate["snapshot_BsGrd"] = candidate["BsGrd"]
        candidate["person_mak_source"] = "Personen_MAK_Source"
        candidate["bsgrd_lineage_flag"] = "ok_source_bsgrd_used"
        candidate["FTE_assigned"] = candidate["FTE_person"] * candidate["Soll_FTE"]

        vacancy_mask = future_df.get("Is_Vacant", pd.Series(False, index=future_df.index)) == True
        if vacancy_mask.any():
            org_unit = candidate.get("Organisationseinheit")
            planstelle = candidate.get("Planstelle")
            row_mask = vacancy_mask.copy()
            if "Organisationseinheit" in future_df.columns and pd.notna(org_unit):
                row_mask = row_mask & future_df["Organisationseinheit"].astype(str).eq(str(org_unit))
            if "Planstelle" in future_df.columns and pd.notna(planstelle):
                exact_mask = row_mask & future_df["Planstelle"].astype(str).eq(str(planstelle))
                if exact_mask.any():
                    row_mask = exact_mask

            if row_mask.any():
                idx = future_df.index[row_mask][0]
                for col, value in candidate.items():
                    if col in future_df.columns:
                        future_df.at[idx, col] = value
                existing_ids.add(pid)
                continue

        rows_to_append.append(candidate)

    if not rows_to_append:
        return future_df

    new_rows_df = pd.DataFrame(rows_to_append)
    new_rows_df = new_rows_df.reindex(columns=future_df.columns, fill_value=pd.NA)
    all_na_cols = [
        col
        for col in new_rows_df.columns
        if col in future_df.columns and new_rows_df[col].isna().all()
    ]
    if all_na_cols:
        new_rows_df = new_rows_df.drop(columns=all_na_cols)
    return pd.concat([future_df, new_rows_df], ignore_index=True)


def _finalize_future_snapshot(
    future_df: pd.DataFrame,
    target_date: pd.Timestamp,
    *,
    active_cluster_source: Optional[ActiveClusterSource] = None,
    cluster_mapping_bundle: Optional[ClusterMappingBundle] = None,
) -> pd.DataFrame:
    future_df = future_df.copy()

    if "PersNr" in future_df.columns:
        persnr = future_df["PersNr"]
        future_df["Is_Vacant"] = persnr.isna() | _normalize_persnr_series(persnr.fillna("")).eq("")

    if "Sollarbeitszeit" in future_df.columns and "Soll_FTE" not in future_df.columns:
        future_df["Soll_FTE"] = pd.to_numeric(future_df["Sollarbeitszeit"], errors="coerce").fillna(0.0) / 39.0

    if "BsGrd" in future_df.columns:
        future_df["FTE_person"] = pd.to_numeric(future_df["BsGrd"], errors="coerce").fillna(0.0) / 100.0

    if "Soll_FTE" in future_df.columns and "FTE_person" in future_df.columns:
        future_df["FTE_assigned"] = future_df["FTE_person"].fillna(0.0) * future_df["Soll_FTE"].fillna(0.0)

    future_df = calculate_mak_vectorized(future_df)
    future_df["MAK"] = future_df["MAK_Calculated"]
    future_df["mak"] = future_df["MAK_Calculated"]

    resolved_source, resolved_bundle, _ = _resolve_simulation_cluster_context(
        active_cluster_source=active_cluster_source,
        cluster_mapping_bundle=cluster_mapping_bundle,
    )
    future_df = apply_clusters_to_snapshot_from_source(
        future_df,
        resolved_source,
        mapping_bundle=resolved_bundle,
    )
    if "Ausbildung" in future_df.columns:
        future_df["Ausbildung"] = normalize_education_series(future_df["Ausbildung"])
        future_df = _fill_missing_education_values(future_df)
    future_df = _zero_out_azubi_mak(future_df)
    future_df = calculate_cost_vectorized(future_df, st.session_state.get("tvoed_lookup", {}))
    future_df = enrich_snapshot_data(future_df, stichtag=target_date)
    future_df = apply_exclusions(future_df, get_setting("exclusions", {}))
    future_df = apply_person_mak_allocation(future_df)
    return future_df


@st.cache_data(show_spinner=False, max_entries=8)
def _build_simulation_audit_tables(
    *,
    future_snapshot_df: pd.DataFrame,
    final_employee_df: pd.DataFrame,
    zugaenge_result: Dict[str, Any],
    target_date: pd.Timestamp,
    abgaenge_params: Dict[str, Any],
    lineage_stages: Optional[Dict[str, pd.DataFrame]] = None,
    base_snapshot_df: Optional[pd.DataFrame] = None,
    abgaenge_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, pd.DataFrame]:
    events_df = zugaenge_result.get("events", pd.DataFrame()) if zugaenge_result else pd.DataFrame()
    abg_events_df = abgaenge_result.get("events_person_level", pd.DataFrame()) if abgaenge_result else pd.DataFrame()
    mak_audit = build_mak_person_audit(future_snapshot_df)
    azubi_flow = build_azubi_flow_audit(
        final_employee_df,
        events_df,
        target_date,
        final_snapshot_df=future_snapshot_df,
    )
    age65_audit = build_65plus_audit(future_snapshot_df, abgaenge_params)
    takeover_audit = build_azubi_takeover_target_audit(
        final_employee_df,
        events_df,
        target_date,
        final_snapshot_df=future_snapshot_df,
    )
    mak_decision = build_mak_decision_list(mak_audit)
    lineage_stages = lineage_stages or {"07_final_future_snapshot": future_snapshot_df}
    lineage = build_mak_lineage_audit(
        lineage_stages,
        base_snapshot_df if base_snapshot_df is not None else pd.DataFrame(),
        abg_events_df,
        events_df,
    )
    mak_origin = build_mak_origin_classification(lineage)
    source_rows_15 = build_mak_source_row_audit_15(
        base_snapshot_df if base_snapshot_df is not None else pd.DataFrame(),
        mak_origin,
    )
    source_patterns = build_mak_source_pattern_classification(source_rows_15)
    correction_decision = build_mak_correction_decision_15(source_patterns)
    known_mak_ids = {
        "002588", "003002", "003640", "004183", "004435",
        "004670", "004705", "004797", "003761", "004378",
        "005639", "002791", "004671", "004730", "005621",
    }
    bsgrd_lineage_15 = pd.DataFrame()
    if "PersNr" in future_snapshot_df.columns:
        work = future_snapshot_df.copy()
        work["PersNr"] = _normalize_persnr_series(work["PersNr"])
        work = work[work["PersNr"].isin(known_mak_ids)].copy()
        if not work.empty:
            bsgrd_lineage_15 = work.groupby("PersNr", dropna=False).agg(
                BsGrd_in_Mitarbeiter_xlsx=("BsGrd_Source", "max"),
                Anzahl_Mitarbeiterzeilen=("PersNr", lambda _x: 1),
                BsGrd_nach_Mitarbeiter_Load=("BsGrd_Source", "max"),
                BsGrd_nach_Planstellen_Join_je_Zeile=("snapshot_BsGrd", lambda x: " | ".join(map(str, pd.Series(x).dropna().unique()))),
                BsGrd_nach_prepare_employee_forecast_base=("BsGrd_Source", "max"),
                BsGrd_im_final_snapshot=("snapshot_BsGrd", "max"),
                Personen_MAK_alt=("MAK_Technical_Uncorrected", "sum"),
                Personen_MAK_neu=("Personen_MAK", "max"),
                MAK_Reporting=("MAK_Reporting", "sum"),
                Ursache_BsGrd_Abweichung=("bsgrd_lineage_flag", "first"),
            ).reset_index()
            bsgrd_lineage_15["empfohlene_Korrektur"] = "Personen_MAK_Source als fuehrende Reporting-Kapazitaet verwenden"
    return {
        "MAK_Personen_Audit": mak_audit,
        "MAK_Allocation_Audit": build_mak_allocation_audit(future_snapshot_df),
        "Azubi_Flow_Audit": azubi_flow,
        "Sonstiges_Audit": build_sonstiges_audit(future_snapshot_df),
        "65plus_Audit": age65_audit,
        "Azubi_Takeover_Target_Audit": takeover_audit,
        "Azubi_Takeover_Decision_List": build_azubi_takeover_decision_list(takeover_audit),
        "MAK_Decision_List": mak_decision,
        "MAK_Policy_Impact": build_mak_policy_impact(future_snapshot_df, mak_decision),
        "MAK_Lineage_Audit": lineage,
        "MAK_Origin_Classification": mak_origin,
        "MAK_Reconciliation_Bridge": build_mak_reconciliation_bridge(lineage, abg_events_df, events_df),
        "MAK_Deep_Dive_15_Cases": build_mak_deep_dive_15_cases(lineage, mak_origin),
        "MAK_Fuehrung_Vertrieb_Check": build_mak_fuehrung_vertrieb_check(lineage, mak_decision),
        "MAK_Column_Consistency_Check": build_mak_column_consistency_check(future_snapshot_df),
        "Abgaenge_Events_Raw": abg_events_df,
        "MAK_Abgaenge_Check": build_mak_abgaenge_check(lineage, abg_events_df),
        "MAK_Zugaenge_Check": build_mak_zugaenge_check(
            lineage,
            events_df,
            base_snapshot_df if base_snapshot_df is not None else pd.DataFrame(),
        ),
        "MAK_Source_Row_Audit_15": source_rows_15,
        "MAK_BsGrd_Lineage_15": bsgrd_lineage_15,
        "MAK_Source_Pattern_Classification": source_patterns,
        "MAK_Correction_Decision_15": correction_decision,
        "MAK_Target_Value_Scenarios": build_mak_target_value_scenarios(future_snapshot_df, correction_decision),
        "MAK_Fuehrung_Vertrieb_Decision": build_mak_fuehrung_vertrieb_decision(
            source_rows_15,
            source_patterns,
            correction_decision,
        ),
        "65plus_Decision_List": build_65plus_decision_list(age65_audit),
    }


def simulate_compact_snapshot(
    snapshot_df: pd.DataFrame,
    df_atz: pd.DataFrame,
    target_date: pd.Timestamp,
    *,
    base_date: Optional[pd.Timestamp] = None,
    abgaenge_params: Optional[Dict[str, Any]] = None,
    zugaenge_params: Optional[Dict[str, Any]] = None,
    active_cluster_source: Optional[ActiveClusterSource] = None,
    cluster_mapping_bundle: Optional[ClusterMappingBundle] = None,
    cluster_source_signature: Optional[str] = None,
    progress_callback: Any = None,
) -> CompactSimulationResult:
    """
    Projects the current snapshot to a future target date for use on the
    "Kompakt plus Simulation" page.
    """
    if base_date is None:
        base_date = get_current_stichtag()
    base_date = pd.Timestamp(base_date)
    target_date = pd.Timestamp(target_date)
    active_cluster_source, cluster_mapping_bundle, cluster_source_signature = _resolve_simulation_cluster_context(
        active_cluster_source=active_cluster_source,
        cluster_mapping_bundle=cluster_mapping_bundle,
        cluster_source_signature=cluster_source_signature,
    )

    def _report(step: str, label: str, value: float) -> None:
        if progress_callback is not None:
            progress_callback(step, label, value)

    current_snapshot = snapshot_df.copy()
    abgaenge_params = abgaenge_params or default_abgaenge_params()
    zugaenge_params = zugaenge_params or default_zugaenge_params()

    if target_date <= base_date:
        # Identity case (zero-horizon): a simulation up to the base date must not change
        # the already-finalised snapshot produced by load_and_prepare_data().
        # Calling _finalize_future_snapshot() on an already-finalised frame re-runs the
        # full MAK / cost / exclusion pipeline a second time, causing numeric drift
        # (e.g. FTE_assigned uses .fillna(0.0) the second time but not the first).
        # We only re-apply cluster mapping (idempotent, no numeric side-effects) so that
        # an active_cluster_source that differs from the one used in load_and_prepare_data
        # is still honoured.  All other finalization is intentionally skipped.
        future_snapshot = current_snapshot.copy()
        future_snapshot = apply_clusters_to_snapshot_from_source(
            future_snapshot,
            active_cluster_source,
            mapping_bundle=cluster_mapping_bundle,
        )
        empty_abg = {"events_person_level": pd.DataFrame(), "tables": {"atz_pivot": pd.DataFrame()}}
        empty_zug = {"events": pd.DataFrame(), "final_state": pd.DataFrame()}
        metadata = {
            "base_date": base_date,
            "target_date": target_date,
            "used_simulation": False,
            "horizon_days": 0,
            "vacancy_count": int(current_snapshot.get("Is_Vacant", pd.Series(dtype=bool)).sum()),
            "abgaenge_events": 0,
            "zugaenge_events": 0,
            "cluster_source_signature": cluster_source_signature,
        }
        future_employees = _prepare_employee_forecast_base(future_snapshot, df_atz, target_date)
        audit_tables = _build_simulation_audit_tables(
            future_snapshot_df=future_snapshot,
            final_employee_df=future_employees,
            zugaenge_result=empty_zug,
            target_date=target_date,
            abgaenge_params=abgaenge_params,
            lineage_stages={
                "01_rohdaten_ausgangsbestand": current_snapshot,
                "02_forecast_base": future_employees,
                "07_final_future_snapshot": future_snapshot,
            },
            base_snapshot_df=current_snapshot,
            abgaenge_result=empty_abg,
        )
        return CompactSimulationResult(future_snapshot, future_employees, empty_abg, empty_zug, metadata, audit_tables)

    _report("start", "Schritt 1/9: Simulation wird vorbereitet ...", 0.05)
    employee_base = _prepare_employee_forecast_base(current_snapshot, df_atz, base_date)

    _report("abgaenge", "Schritt 3/9: Abgänge werden berechnet ...", 0.20)
    abgaenge_result = run_forecast_abgaenge(
        df_ma=employee_base,
        df_atz=df_atz,
        start_date=base_date,
        end_date=target_date,
        freq="M",
        params=abgaenge_params,
    )
    employee_after_abg = _apply_attrition_events_to_employee_state(
        employee_base,
        abgaenge_result.get("events_person_level", pd.DataFrame()),
    )

    vacancies = _build_vacancies_from_attrition(employee_base, abgaenge_result)

    _report("events_vacancies", "Schritt 4/9: Ereignisse und Vakanzen werden verarbeitet ...", 0.30)
    run_params_zug = {**zugaenge_params}
    run_params_zug.setdefault("azubi", {})
    run_params_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(current_snapshot)

    _report("zugaenge", "Schritt 5/9: Zugänge werden berechnet ...", 0.42)
    zugaenge_result = run_forecast_zugaenge(
        df_snapshot=employee_after_abg,
        start_date=base_date,
        end_date=target_date,
        freq="M",
        params=run_params_zug,
        vacancies=vacancies,
    )
    zug_events = zugaenge_result.get("events", pd.DataFrame())
    if not zug_events.empty:
        zugaenge_result["events"] = enrich_zugaenge_events(
            zug_events,
            current_snapshot,
            run_params_zug,
            cluster_mapping_bundle=cluster_mapping_bundle,
            active_cluster_source_signature=cluster_source_signature,
        )

    final_employee_df = zugaenge_result.get("final_state", employee_after_abg).copy()
    if "PersNr" in final_employee_df.columns:
        final_employee_df["PersNr"] = _normalize_persnr_series(final_employee_df["PersNr"])
    final_employee_df = _apply_salary_automation_to_employee_state(
        final_employee_df,
        target_date=target_date,
        events_df=zugaenge_result.get("events", pd.DataFrame()),
    )

    atz_pivot = abgaenge_result.get("tables", {}).get("atz_pivot", pd.DataFrame())
    atz_status_df = _resolve_atz_status_map(atz_pivot, target_date)

    _report("update_existing", "Schritt 6/9: Bestehender Bestand wird aktualisiert ...", 0.55)
    future_snapshot = _update_existing_rows(
        snapshot_df=current_snapshot,
        final_employee_df=final_employee_df,
        target_date=target_date,
        atz_status_df=atz_status_df,
    )
    snapshot_after_update_existing = future_snapshot.copy()
    future_snapshot = _append_new_people_rows(
        future_df=future_snapshot,
        base_snapshot_df=current_snapshot,
        final_employee_df=final_employee_df,
        zugaenge_result=zugaenge_result,
    )
    snapshot_after_append = future_snapshot.copy()
    _report("finalize_snapshot", "Schritt 7/9: Future Snapshot wird finalisiert ...", 0.65)
    future_snapshot = _finalize_future_snapshot(
        future_snapshot,
        target_date,
        active_cluster_source=active_cluster_source,
        cluster_mapping_bundle=cluster_mapping_bundle,
    )

    metadata = {
        "base_date": base_date,
        "target_date": target_date,
        "used_simulation": True,
        "vacancy_count": len(vacancies),
        "abgaenge_events": len(abgaenge_result.get("events_person_level", pd.DataFrame())),
        "zugaenge_events": len(zugaenge_result.get("events", pd.DataFrame())),
        "cluster_source_signature": cluster_source_signature,
    }
    _report("audits", "Schritt 8/9: Audit-Tabellen werden erstellt ...", 0.90)
    audit_tables = _build_simulation_audit_tables(
        future_snapshot_df=future_snapshot,
        final_employee_df=final_employee_df,
        zugaenge_result=zugaenge_result,
        target_date=target_date,
        abgaenge_params=abgaenge_params,
        lineage_stages={
            "01_rohdaten_ausgangsbestand": current_snapshot,
            "02_forecast_base": employee_base,
            "03_nach_abgaenge": employee_after_abg,
            "04_nach_zugaenge": final_employee_df,
            "05_nach_update_existing_rows": snapshot_after_update_existing,
            "06_nach_append_new_people_rows": snapshot_after_append,
            "07_final_future_snapshot": future_snapshot,
        },
        base_snapshot_df=current_snapshot,
        abgaenge_result=abgaenge_result,
    )

    return CompactSimulationResult(
        future_snapshot_df=future_snapshot,
        future_employee_df=final_employee_df,
        abgaenge_result=abgaenge_result,
        zugaenge_result=zugaenge_result,
        metadata=metadata,
        audit_tables=audit_tables,
    )
