"""
Granulares Timing der build_mak_lineage_audit-Internals.
Kein Produktivcode veraendert. Nur Analyse.
"""
import sys, time, copy
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from dataloader.compact_simulation_engine import (
    _prepare_employee_forecast_base, _apply_attrition_events_to_employee_state,
    _build_vacancies_from_attrition, _update_existing_rows, _append_new_people_rows,
    _finalize_future_snapshot, _normalize_persnr_series, _resolve_atz_status_map,
    _apply_salary_automation_to_employee_state,
    build_jf_to_cluster_map, default_abgaenge_params, default_zugaenge_params,
)
from abgaenge.forecast import run_forecast_abgaenge
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.enrichment import enrich_zugaenge_events
from dataloader.loader import load_original_data, normalize_atz
from utils.audit_helpers import (
    _person_stage_summary, _event_lookup, _normalize_persnr_for_audit,
    _classify_mak_origin_from_stage, build_mak_lineage_audit,
)

BASE_DATE   = pd.Timestamp("2025-12-31")
TARGET_DATE = pd.Timestamp("2026-12-31")


def _sep(w=60): print("-" * w)


def main():
    # 1. Daten laden
    raw = load_original_data()
    pl, ma, atz_raw = raw["planstellen"], raw["mitarbeiter"], raw["atz"]

    pl["PersNr"] = _normalize_persnr_series(pl["Personalnummer"].fillna("").astype(str))
    ma_sub = ma[["PersNr","BsGrd","TrfGr","St","GebDatum","Eintritt","Austritt",
                 "Vertragsart","MitarbGruppenbez.","Text Gsch","Status kundenindividuell"]].copy()
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
    snap["Personen_MAK"]  = bsgrd / 100.0
    snap["Personen_MAK_Source"] = bsgrd / 100.0
    snap["BsGrd_Source"]  = bsgrd
    snap["snapshot_BsGrd"] = bsgrd
    snap["person_mak_source"] = "Personen_MAK_Source"
    snap["bsgrd_lineage_flag"] = "ok_source_bsgrd_used"
    snap["MAK_Technical_Uncorrected"] = bsgrd / 100.0
    snap["MAK_Reporting"] = bsgrd / 100.0
    snap["Ausbildung"]    = pd.NA
    snap["Jobfamily"]     = "Angestellte"
    snap["OE-Cluster"]    = "Sonstiges"
    snap["JF-Cluster"]    = "Sonstiges"
    snap["Alter_Jahre"]   = 45.0
    df_atz = normalize_atz(atz_raw)

    # 2. Simulation: Lineage-Stages aufbauen
    print("Lade Lineage-Stages (Simulation) ...")
    cs = snap.copy()
    abg_params = default_abgaenge_params()
    zug_params  = default_zugaenge_params()
    emp_base = _prepare_employee_forecast_base(cs, df_atz, BASE_DATE)
    abg_res  = run_forecast_abgaenge(df_ma=emp_base, df_atz=df_atz,
                                      start_date=BASE_DATE, end_date=TARGET_DATE,
                                      freq="M", params=abg_params)
    emp_abg  = _apply_attrition_events_to_employee_state(
        emp_base, abg_res.get("events_person_level", pd.DataFrame()))
    vac      = _build_vacancies_from_attrition(emp_base, abg_res)
    rp_zug   = {**zug_params}
    rp_zug.setdefault("azubi", {})
    rp_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(cs)
    zug_res  = run_forecast_zugaenge(df_snapshot=emp_abg, start_date=BASE_DATE,
                                      end_date=TARGET_DATE, freq="M",
                                      params=rp_zug, vacancies=vac)
    zug_ev = zug_res.get("events", pd.DataFrame())
    if not zug_ev.empty:
        zug_res["events"] = enrich_zugaenge_events(zug_ev, cs, rp_zug)
    final_emp = zug_res.get("final_state", emp_abg).copy()
    if "PersNr" in final_emp.columns:
        final_emp["PersNr"] = _normalize_persnr_series(final_emp["PersNr"])
    final_emp = _apply_salary_automation_to_employee_state(
        final_emp, target_date=TARGET_DATE,
        events_df=zug_res.get("events", pd.DataFrame()))
    atz_piv    = abg_res.get("tables", {}).get("atz_pivot", pd.DataFrame())
    atz_status = _resolve_atz_status_map(atz_piv, TARGET_DATE)
    snap_upd   = _update_existing_rows(cs, final_emp, TARGET_DATE, atz_status)
    snap_app   = _append_new_people_rows(snap_upd.copy(), cs, final_emp, zug_res)
    snap_fin   = _finalize_future_snapshot(snap_app.copy(), TARGET_DATE)

    lineage_stages = {
        "01_rohdaten_ausgangsbestand":     cs,
        "02_forecast_base":                emp_base,
        "03_nach_abgaenge":                emp_abg,
        "04_nach_zugaenge":                final_emp,
        "05_nach_update_existing_rows":    snap_upd,
        "06_nach_append_new_people_rows":  snap_app,
        "07_final_future_snapshot":        snap_fin,
    }
    abg_events = abg_res.get("events_person_level", pd.DataFrame())
    zug_events = zug_res.get("events", pd.DataFrame())
    print("  ... fertig\n")

    # 3. Stage-Dimensionen
    _sep()
    print("STAGE-DIMENSIONEN")
    _sep()
    for k, v in lineage_stages.items():
        kb = v.memory_usage(deep=True).sum() // 1024
        print(f"  {k}: {v.shape[0]:5d} x {v.shape[1]:3d}  ({kb:5d} KB)")
    print(f"  abg_events: {abg_events.shape}, zug_events: {zug_events.shape}")

    # 4. _event_lookup Redundanz-Messung
    _sep()
    print("_event_lookup (2x vs 14x)")
    _sep()
    t0 = time.perf_counter()
    for _ in range(7):
        _event_lookup(abg_events, "persnr")
        _event_lookup(zug_events, "persnr")
    t14 = time.perf_counter() - t0
    t0 = time.perf_counter()
    abg_lk = _event_lookup(abg_events, "persnr")
    zug_lk = _event_lookup(zug_events, "persnr")
    t2 = time.perf_counter() - t0
    print(f"  14x _event_lookup:  {t14:.4f}s")
    print(f"   2x _event_lookup:  {t2:.4f}s")
    print(f"  Redundanz-Overhead: {t14-t2:.4f}s")

    # 5. _person_stage_summary pro Stage
    _sep()
    print("_person_stage_summary pro Stage (inkl. interner _event_lookup)")
    _sep()
    base_ids = set(_normalize_persnr_for_audit(cs["PersNr"].dropna()))
    for stage_name, df in lineage_stages.items():
        iv_mask = df["Is_Vacant"].fillna(False).astype(bool) if "Is_Vacant" in df.columns else pd.Series(False, index=df.index)
        active = df[df["PersNr"].notna() & ~iv_mask]
        n_persons = active["PersNr"].nunique() if "PersNr" in active.columns else 0
        t0 = time.perf_counter()
        _person_stage_summary(df, stage_name, stage_name, base_ids, abg_events, zug_events)
        elapsed = time.perf_counter() - t0
        print(f"  {stage_name}: {elapsed:.4f}s  "
              f"({df.shape[0]}r x {df.shape[1]}c,  ~{n_persons} Personen)")

    # 6. DataFrame-Copy-Kosten
    _sep()
    print("DataFrame-Copy-Kosten (df.copy().reset_index())")
    _sep()
    for stage_name, df in lineage_stages.items():
        t0 = time.perf_counter()
        w = df.copy().reset_index(drop=True)
        elapsed = time.perf_counter() - t0
        print(f"  {stage_name}: {elapsed:.4f}s")

    # 7. Inner groupby-loop Kosten isoliert (ohne _event_lookup)
    _sep()
    print("Inner groupby-loop (mit pre-computed lookups, nur Loop-Overhead)")
    _sep()
    for stage_name, df in lineage_stages.items():
        # Mimic _person_stage_summary setup
        work = df.copy().reset_index(drop=True)
        if "active" in work.columns:
            work = work[work["active"] == True].copy()
        if "Is_Vacant" in work.columns:
            work = work[work["Is_Vacant"] != True].copy()
        work = work[work["PersNr"].notna()].copy()
        if work.empty:
            print(f"  {stage_name}: (leer)")
            continue
        work["PersNr"] = _normalize_persnr_for_audit(work["PersNr"])
        from utils.audit_helpers import select_mak_column, _numeric, _series_text, _first_value
        mak_col = select_mak_column(work)
        if mak_col == "BsGrd":
            work["_mak_value"] = _numeric(work[mak_col]) / 100.0
        elif mak_col:
            work["_mak_value"] = _numeric(work[mak_col])
        else:
            work["_mak_value"] = 0.0
        work["_eur_value"] = _numeric(work["Total_Cost_Year"]) if "Total_Cost_Year" in work.columns else 0.0

        t0 = time.perf_counter()
        rows = []
        for pid, rows_df in work.groupby("PersNr", dropna=False):
            mak_values = rows_df["_mak_value"].tolist()
            rows.append({
                "processing_stage": stage_name,
                "PersNr": pid,
                "MAK_sum": float(sum(mak_values)),
                "MAK_max": float(max(mak_values)) if mak_values else 0.0,
                "Jobfamily": _first_value(rows_df, "Jobfamily", ""),
                "Planstelle": _series_text(rows_df.get("Planstelle", pd.Series(dtype=object))),
                "Rollenliste": _series_text(rows_df.get("Planstelle", pd.Series(dtype=object))),
                "Anzahl_Zeilen": int(len(rows_df)),
                "EUR_sum": float(rows_df["_eur_value"].sum()),
            })
        elapsed = time.perf_counter() - t0
        print(f"  {stage_name}: {elapsed:.4f}s  ({len(rows)} Personen)")

    # 8. .apply(axis=1) Kosten
    _sep()
    print("apply(axis=1) Kosten auf Lineage-Ergebnis")
    _sep()
    lineage = build_mak_lineage_audit(lineage_stages, cs, abg_events, zug_events)
    print(f"  Lineage shape: {lineage.shape}")
    t0 = time.perf_counter()
    _ = lineage.apply(_classify_mak_origin_from_stage, axis=1)
    print(f"  apply(_classify_mak_origin_from_stage): {time.perf_counter()-t0:.4f}s")
    t0 = time.perf_counter()
    _ = lineage.apply(
        lambda row: "MAK_sum_gt_1" if float(row.get("MAK_sum", 0.0) or 0.0) > 1.000001 else "unauffaellig",
        axis=1,
    )
    print(f"  apply(comment lambda):                  {time.perf_counter()-t0:.4f}s")

    _sep()
    print("Analyse abgeschlossen. Kein Produktivcode veraendert.")
    _sep()


if __name__ == "__main__":
    main()
