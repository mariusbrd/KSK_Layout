"""
Re-Profiling nach Hotspot 8d.
Branch: perf/hotspot-8d-mak-fte-bsgrd-shadow @ 5dbefa8
Commit: 5dbefa8

Misst alle wesentlichen Bloecke von simulate_compact_snapshot
auf echten Produktionsdaten. Kein Produktivcode veraendert.

Aufruf:  py tests/profile_simulation_after_8d.py
(nicht als pytest, da Echtdaten und Timing benoetigt)

Szenarien:
  A - Erster Lauf              (Audit-Cache-Miss)
  B - Gleiche Parameter        (Audit-Cache-Hit)
  C - Parameterwechsel         (Audit-Cache-Miss)
  D - Rueckkehr zu Originalpar. (Audit-Cache-Hit erwartet)
"""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

# --- Produktionsimporte (keine Aenderung) ---
from dataloader.compact_simulation_engine import (
    _append_new_people_rows,
    _apply_attrition_events_to_employee_state,
    _apply_salary_automation_to_employee_state,
    _build_simulation_audit_tables,
    _build_vacancies_from_attrition,
    _finalize_future_snapshot,
    _normalize_persnr_series,
    _prepare_employee_forecast_base,
    _resolve_atz_status_map,
    _update_existing_rows,
    default_abgaenge_params,
    default_zugaenge_params,
    simulate_compact_snapshot,
)
from abgaenge.forecast import run_forecast_abgaenge
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.enrichment import enrich_zugaenge_events
from dataloader.loader import load_original_data, normalize_atz
from zugaenge.enrichment import build_jf_to_cluster_map
from utils.audit_helpers import (
    build_65plus_audit,
    build_65plus_decision_list,
    build_azubi_flow_audit,
    build_azubi_takeover_decision_list,
    build_azubi_takeover_target_audit,
    build_mak_abgaenge_check,
    build_mak_column_consistency_check,
    build_mak_correction_decision_15,
    build_mak_decision_list,
    build_mak_deep_dive_15_cases,
    build_mak_fuehrung_vertrieb_check,
    build_mak_fuehrung_vertrieb_decision,
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
from dataloader.mak_allocation import build_mak_allocation_audit

_ORIG = Path(__file__).resolve().parents[2] / "Original-Daten"

BASE_DATE   = pd.Timestamp("2025-12-31")
TARGET_DATE = pd.Timestamp("2026-12-31")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

class _T:
    """Minimaler Timing-Kontext."""
    def __init__(self, label: str, results: list):
        self._label = label
        self._results = results
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self
    def __exit__(self, *_):
        elapsed = time.perf_counter() - self._t0
        self._results.append((self._label, elapsed))


def _sep(char="-", width=68):
    print(char * width)


def _print_table(rows: list[tuple], header=("Block / Funktion", "Zeit (s)")):
    w0 = max(len(header[0]), max(len(r[0]) for r in rows))
    w1 = max(len(header[1]), 8)
    fmt = f"  {{:<{w0}}}  {{:>{w1}}}"
    _sep()
    print(fmt.format(*header))
    _sep()
    for label, t in rows:
        print(fmt.format(label, f"{t:.4f}"))
    _sep()


# ── Daten laden ───────────────────────────────────────────────────────────────

def _load_data():
    """Laedt Original-Daten und baut minimalen Snapshot fuer simulate_compact_snapshot."""
    if not (_ORIG / "Planstellen.XLSX").exists():
        print("FEHLER: Original-Daten nicht gefunden unter", _ORIG)
        sys.exit(1)

    print("Lade Original-Daten ...")
    t0 = time.perf_counter()
    raw = load_original_data()
    pl = raw["planstellen"]
    ma = raw["mitarbeiter"]
    atz_raw = raw["atz"]
    ausb = raw["ausbildung"]
    print(f"  Excel geladen:  {time.perf_counter()-t0:.2f}s")

    # Snapshot aus Planstellen + Mitarbeiter zusammenbauen
    pl["PersNr"] = _normalize_persnr_series(
        pl["Personalnummer"].fillna("").astype(str)
    )
    ma_sub = ma[
        ["PersNr", "BsGrd", "TrfGr", "St", "GebDatum", "Eintritt", "Austritt",
         "Vertragsart", "MitarbGruppenbez.", "Text Gsch", "Status kundenindividuell"]
    ].copy()
    snap = pl.merge(ma_sub, on="PersNr", how="left")
    snap.index = range(len(snap))

    snap["Is_Vacant"]     = snap["PersNr"].eq("")
    snap["Personalnummer"] = snap["PersNr"]
    soll_h = pd.to_numeric(snap["Sollarbeitszeit"], errors="coerce").fillna(0.0)
    snap["Soll_FTE"]      = soll_h / 39.0
    bsgrd = pd.to_numeric(snap["BsGrd"], errors="coerce").fillna(0.0)
    snap["BsGrd"]         = bsgrd
    snap["MAK_Calculated"] = bsgrd / 100.0
    snap["mak"]           = snap["MAK_Calculated"]
    snap["MAK"]           = snap["MAK_Calculated"]
    snap["FTE_person"]    = snap["MAK_Calculated"]
    snap["FTE_assigned"]  = snap["MAK_Calculated"] * snap["Soll_FTE"]
    snap["ATZ_Status"]    = "Kein ATZ"
    snap["ist_atz_fr"]    = False
    snap["Sollarbeitszeit"] = soll_h

    # Personen_MAK etc. (benoetigt von build_mak_person_audit)
    snap["Personen_MAK"]        = bsgrd / 100.0
    snap["Personen_MAK_Source"] = bsgrd / 100.0
    snap["BsGrd_Source"]        = bsgrd
    snap["snapshot_BsGrd"]      = bsgrd
    snap["person_mak_source"]   = "Personen_MAK_Source"
    snap["bsgrd_lineage_flag"]  = "ok_source_bsgrd_used"
    snap["MAK_Technical_Uncorrected"] = bsgrd / 100.0
    snap["MAK_Reporting"]       = bsgrd / 100.0
    snap["Ausbildung"]          = pd.NA
    snap["Jobfamily"]           = "Angestellte"
    snap["OE-Cluster"]          = "Sonstiges"
    snap["JF-Cluster"]          = "Sonstiges"
    snap["Planstelle"]          = snap.get("Planstelle", pd.NA)
    snap["Alter_Jahre"]         = 45.0

    # ATZ normalisieren
    df_atz = normalize_atz(atz_raw)

    print(f"  Snapshot: {len(snap)} Zeilen, {snap['PersNr'].ne('').sum()} besetzt, "
          f"{snap['PersNr'].ne('').sum() and snap[snap['PersNr'].ne('')]['PersNr'].nunique()} Personen")
    print(f"  ATZ:      {len(df_atz)} Eintraege")
    return snap, df_atz


# ── Schritt-fuer-Schritt-Profiling ────────────────────────────────────────────

def _profile_steps(snap: pd.DataFrame, df_atz: pd.DataFrame,
                   abg_params: dict, zug_params: dict,
                   scenario_label: str) -> tuple[list, dict]:
    """
    Fuehrt simulate_compact_snapshot Schritt fuer Schritt aus
    und misst jeden Block einzeln.
    Gibt (timing_rows, intermediate_data) zurueck.
    """
    rows = []
    data = {}

    current_snapshot = snap.copy()

    with _T("_prepare_employee_forecast_base", rows):
        employee_base = _prepare_employee_forecast_base(current_snapshot, df_atz, BASE_DATE)
    data["employee_base"] = employee_base

    with _T("run_forecast_abgaenge", rows):
        abgaenge_result = run_forecast_abgaenge(
            df_ma=employee_base,
            df_atz=df_atz,
            start_date=BASE_DATE,
            end_date=TARGET_DATE,
            freq="M",
            params=abg_params,
        )
    data["abgaenge_result"] = abgaenge_result

    with _T("_apply_attrition_events + _build_vacancies", rows):
        employee_after_abg = _apply_attrition_events_to_employee_state(
            employee_base,
            abgaenge_result.get("events_person_level", pd.DataFrame()),
        )
        vacancies = _build_vacancies_from_attrition(employee_base, abgaenge_result)
    data["employee_after_abg"] = employee_after_abg
    data["vacancies"] = vacancies

    run_params_zug = {**zug_params}
    run_params_zug.setdefault("azubi", {})
    run_params_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(current_snapshot)

    with _T("run_forecast_zugaenge", rows):
        zugaenge_result = run_forecast_zugaenge(
            df_snapshot=employee_after_abg,
            start_date=BASE_DATE,
            end_date=TARGET_DATE,
            freq="M",
            params=run_params_zug,
            vacancies=vacancies,
        )
    zug_events = zugaenge_result.get("events", pd.DataFrame())
    if not zug_events.empty:
        zugaenge_result["events"] = enrich_zugaenge_events(
            zug_events, current_snapshot, run_params_zug,
        )
    data["zugaenge_result"] = zugaenge_result

    final_employee_df = zugaenge_result.get("final_state", employee_after_abg).copy()
    if "PersNr" in final_employee_df.columns:
        final_employee_df["PersNr"] = _normalize_persnr_series(final_employee_df["PersNr"])
    final_employee_df = _apply_salary_automation_to_employee_state(
        final_employee_df,
        target_date=TARGET_DATE,
        events_df=zugaenge_result.get("events", pd.DataFrame()),
    )
    atz_pivot = abgaenge_result.get("tables", {}).get("atz_pivot", pd.DataFrame())
    atz_status_df = _resolve_atz_status_map(atz_pivot, TARGET_DATE)

    with _T("_update_existing_rows", rows):
        future_snapshot = _update_existing_rows(
            snapshot_df=current_snapshot,
            final_employee_df=final_employee_df,
            target_date=TARGET_DATE,
            atz_status_df=atz_status_df,
        )
    snap_after_update = future_snapshot.copy()

    with _T("_append_new_people_rows", rows):
        future_snapshot = _append_new_people_rows(
            future_df=future_snapshot,
            base_snapshot_df=current_snapshot,
            final_employee_df=final_employee_df,
            zugaenge_result=zugaenge_result,
        )
    snap_after_append = future_snapshot.copy()

    with _T("_finalize_future_snapshot", rows):
        future_snapshot = _finalize_future_snapshot(future_snapshot, TARGET_DATE)
    data["future_snapshot"] = future_snapshot
    data["final_employee_df"] = final_employee_df
    data["snap_after_update"] = snap_after_update
    data["snap_after_append"] = snap_after_append
    data["current_snapshot"] = current_snapshot

    lineage_stages = {
        "01_rohdaten_ausgangsbestand": current_snapshot,
        "02_forecast_base": employee_base,
        "03_nach_abgaenge": employee_after_abg,
        "04_nach_zugaenge": final_employee_df,
        "05_nach_update_existing_rows": snap_after_update,
        "06_nach_append_new_people_rows": snap_after_append,
        "07_final_future_snapshot": future_snapshot,
    }
    data["lineage_stages"] = lineage_stages

    with _T("_build_simulation_audit_tables (gesamt)", rows):
        audit_tables = _build_simulation_audit_tables(
            future_snapshot_df=future_snapshot,
            final_employee_df=final_employee_df,
            zugaenge_result=zugaenge_result,
            target_date=TARGET_DATE,
            abgaenge_params=abg_params,
            lineage_stages=lineage_stages,
            base_snapshot_df=current_snapshot,
            abgaenge_result=abgaenge_result,
        )
    data["audit_tables"] = audit_tables

    total = sum(t for _, t in rows)
    rows.append(("GESAMT (Schritte)", total))
    return rows, data


def _profile_audit_builders(data: dict) -> list:
    """Misst jeden Audit-Builder einzeln auf bereits berechneten Inputs."""
    rows = []
    fs  = data["future_snapshot"]
    fe  = data["final_employee_df"]
    cs  = data["current_snapshot"]
    ls  = data["lineage_stages"]
    zr  = data["zugaenge_result"]
    ar  = data["abgaenge_result"]
    events_df  = zr.get("events", pd.DataFrame())
    abg_events = ar.get("events_person_level", pd.DataFrame())

    with _T("build_mak_person_audit", rows):
        mak_audit = build_mak_person_audit(fs)
    with _T("build_azubi_flow_audit", rows):
        build_azubi_flow_audit(fe, events_df, TARGET_DATE, final_snapshot_df=fs)
    with _T("build_65plus_audit", rows):
        age65 = build_65plus_audit(fs, default_abgaenge_params())
    with _T("build_azubi_takeover_target_audit", rows):
        takeover = build_azubi_takeover_target_audit(fe, events_df, TARGET_DATE, final_snapshot_df=fs)
    with _T("build_mak_decision_list", rows):
        mak_decision = build_mak_decision_list(mak_audit)
    with _T("build_mak_lineage_audit", rows):
        lineage = build_mak_lineage_audit(ls, cs, abg_events, events_df)
    with _T("build_mak_origin_classification", rows):
        mak_origin = build_mak_origin_classification(lineage)
    with _T("build_mak_reconciliation_bridge", rows):
        build_mak_reconciliation_bridge(lineage, abg_events, events_df)
    with _T("build_mak_source_row_audit_15", rows):
        source_rows_15 = build_mak_source_row_audit_15(cs, mak_origin)
    with _T("build_mak_source_pattern_classification", rows):
        source_patterns = build_mak_source_pattern_classification(source_rows_15)
    with _T("build_mak_correction_decision_15", rows):
        correction_decision = build_mak_correction_decision_15(source_patterns)
    with _T("build_mak_allocation_audit", rows):
        build_mak_allocation_audit(fs)
    with _T("build_sonstiges_audit", rows):
        build_sonstiges_audit(fs)
    with _T("build_mak_policy_impact", rows):
        build_mak_policy_impact(fs, mak_decision)
    with _T("build_mak_deep_dive_15_cases", rows):
        build_mak_deep_dive_15_cases(lineage, mak_origin)
    with _T("build_mak_fuehrung_vertrieb_check", rows):
        build_mak_fuehrung_vertrieb_check(lineage, mak_decision)
    with _T("build_mak_column_consistency_check", rows):
        build_mak_column_consistency_check(fs)
    with _T("build_mak_abgaenge_check", rows):
        build_mak_abgaenge_check(lineage, abg_events)
    with _T("build_mak_zugaenge_check", rows):
        build_mak_zugaenge_check(lineage, events_df, cs)
    with _T("build_mak_target_value_scenarios", rows):
        build_mak_target_value_scenarios(fs, correction_decision)
    with _T("build_mak_fuehrung_vertrieb_decision", rows):
        build_mak_fuehrung_vertrieb_decision(source_rows_15, source_patterns, correction_decision)
    with _T("build_azubi_takeover_decision_list", rows):
        build_azubi_takeover_decision_list(takeover)
    with _T("build_65plus_decision_list", rows):
        build_65plus_decision_list(age65)

    total = sum(t for _, t in rows)
    rows.append(("GESAMT Audit-Builder", total))
    return rows


def _scenario_full(snap, df_atz, abg_params, zug_params, label) -> float:
    """Misst die vollstaendige simulate_compact_snapshot-Laufzeit."""
    t0 = time.perf_counter()
    simulate_compact_snapshot(
        snapshot_df=snap.copy(),
        df_atz=df_atz,
        target_date=TARGET_DATE,
        base_date=BASE_DATE,
        abgaenge_params=copy.deepcopy(abg_params),
        zugaenge_params=copy.deepcopy(zug_params),
    )
    elapsed = time.perf_counter() - t0
    print(f"  {label}: {elapsed:.3f}s")
    return elapsed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    _sep("=")
    print("RE-PROFILING NACH HOTSPOT 8d")
    print("Branch: perf/hotspot-8d-mak-fte-bsgrd-shadow @ 5dbefa8")
    print(f"Datum:  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    _sep("=")

    snap, df_atz = _load_data()

    abg_std = default_abgaenge_params()
    zug_std = default_zugaenge_params()

    # --- Parametervariante C: quit_rate etwas hoeher ---
    abg_alt = copy.deepcopy(abg_std)
    abg_alt["quit"]["quit_rate_base"] = 0.07

    # ── Szenario A: Erster Lauf (Schritt-fuer-Schritt + Audit-Builder) ────────
    _sep("=")
    print("SZENARIO A: Erster Lauf (Audit-Cache-Miss)")
    _sep()

    step_rows_A, data_A = _profile_steps(snap, df_atz, abg_std, zug_std, "A")
    _print_table(step_rows_A, ("Block", "Zeit (s)"))

    # ── Audit-Builder einzeln ─────────────────────────────────────────────────
    _sep()
    print("AUDIT-BUILDER (einzeln, auf Szenario-A-Daten):")
    _sep()
    audit_rows = _profile_audit_builders(data_A)
    _print_table(audit_rows, ("Audit-Builder", "Zeit (s)"))

    # ── Szenario B: Zweiter Lauf, gleiche Parameter (Cache-Hit erwartet) ──────
    _sep("=")
    print("SZENARIO B: Zweiter Lauf, gleiche Parameter (Audit-Cache-Hit erwartet)")
    _sep()
    step_rows_B, _ = _profile_steps(snap, df_atz, abg_std, zug_std, "B")
    _print_table(step_rows_B, ("Block", "Zeit (s)"))

    # ── Szenario C: Parameterwechsel (quit_rate 0.05 -> 0.07) ─────────────────
    _sep("=")
    print("SZENARIO C: Parameterwechsel quit_rate 0.05->0.07 (Audit-Cache-Miss)")
    _sep()
    step_rows_C, _ = _profile_steps(snap, df_atz, abg_alt, zug_std, "C")
    _print_table(step_rows_C, ("Block", "Zeit (s)"))

    # ── Szenario D: Rueckkehr zu Originalparametern (Cache-Hit erwartet) ──────
    _sep("=")
    print("SZENARIO D: Rueckkehr zu Standardparametern (Audit-Cache-Hit erwartet)")
    _sep()
    step_rows_D, _ = _profile_steps(snap, df_atz, abg_std, zug_std, "D")
    _print_table(step_rows_D, ("Block", "Zeit (s)"))

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    _sep("=")
    print("ZUSAMMENFASSUNG")
    _sep()

    def _total(rows):
        for label, t in rows:
            if label.startswith("GESAMT"):
                return t
        return 0.0

    print(f"  Szenario A (Cache-Miss):    {_total(step_rows_A):.3f}s")
    print(f"  Szenario B (Cache-Hit):     {_total(step_rows_B):.3f}s")
    print(f"  Szenario C (Cache-Miss):    {_total(step_rows_C):.3f}s")
    print(f"  Szenario D (Cache-Hit):     {_total(step_rows_D):.3f}s")
    print()

    # Finde groesste Bloecke in Szenario A (Cache-Miss)
    block_rows = [(l, t) for l, t in step_rows_A if not l.startswith("GESAMT")]
    block_rows.sort(key=lambda x: x[1], reverse=True)
    total_A = _total(step_rows_A)
    print("  Groesste Bloecke in Szenario A:")
    for label, t in block_rows[:6]:
        pct = 100.0 * t / total_A if total_A > 0 else 0
        print(f"    {label:<45} {t:6.3f}s  ({pct:.0f}%)")
    print()

    # Finde groesste Audit-Builder
    audit_block_rows = [(l, t) for l, t in audit_rows if not l.startswith("GESAMT")]
    audit_block_rows.sort(key=lambda x: x[1], reverse=True)
    print("  Groesste Audit-Builder:")
    for label, t in audit_block_rows[:6]:
        print(f"    {label:<45} {t:6.3f}s")

    _sep("=")
    print("Profiling abgeschlossen. Kein Produktivcode veraendert.")
    _sep("=")


if __name__ == "__main__":
    main()
