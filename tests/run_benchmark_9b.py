"""Benchmark-Script fuer Hotspot 9b. Kein Produktivcode veraendert."""
import sys, time, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _apply_salary_automation_to_employee_state,
    _append_new_people_rows, _build_vacancies_from_attrition, _finalize_future_snapshot,
    _normalize_persnr_series, _prepare_employee_forecast_base, _resolve_atz_status_map,
    _update_existing_rows, build_jf_to_cluster_map,
    default_abgaenge_params, default_zugaenge_params,
)
from abgaenge.forecast import run_forecast_abgaenge
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.enrichment import enrich_zugaenge_events
from dataloader.loader import load_original_data, normalize_atz
from utils.audit_helpers import (
    _event_lookup, _normalize_persnr_for_audit, _person_stage_summary,
)
# Import shadow from test file
from test_shadow_person_stage_summary_9b import _person_stage_summary_shadow

BASE_DATE   = pd.Timestamp("2025-12-31")
TARGET_DATE = pd.Timestamp("2026-12-31")

print("Lade Daten ...")
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
snap["MAK_Calculated"] = bsgrd/100.0
snap["mak"] = snap["MAK_Calculated"]
snap["MAK"] = snap["MAK_Calculated"]
snap["FTE_person"] = snap["MAK_Calculated"]
snap["FTE_assigned"] = snap["MAK_Calculated"] * snap["Soll_FTE"]
snap["ATZ_Status"] = "Kein ATZ"; snap["ist_atz_fr"] = False
snap["Sollarbeitszeit"] = soll_h
snap["Personen_MAK"] = bsgrd/100.0; snap["Personen_MAK_Source"] = bsgrd/100.0
snap["BsGrd_Source"] = bsgrd; snap["snapshot_BsGrd"] = bsgrd
snap["person_mak_source"] = "Personen_MAK_Source"; snap["bsgrd_lineage_flag"] = "ok_source_bsgrd_used"
snap["MAK_Technical_Uncorrected"] = bsgrd/100.0; snap["MAK_Reporting"] = bsgrd/100.0
snap["Ausbildung"] = pd.NA; snap["Jobfamily"] = "Angestellte"
snap["OE-Cluster"] = "Sonstiges"; snap["JF-Cluster"] = "Sonstiges"; snap["Alter_Jahre"] = 45.0
df_atz = normalize_atz(atz_raw)

cs = snap.copy()
abg_params = default_abgaenge_params(); zug_params = default_zugaenge_params()
emp_base = _prepare_employee_forecast_base(cs, df_atz, BASE_DATE)
abg_res = run_forecast_abgaenge(df_ma=emp_base, df_atz=df_atz, start_date=BASE_DATE, end_date=TARGET_DATE, freq="M", params=abg_params)
emp_abg = _apply_attrition_events_to_employee_state(emp_base, abg_res.get("events_person_level", pd.DataFrame()))
vac = _build_vacancies_from_attrition(emp_base, abg_res)
rp_zug = {**zug_params}; rp_zug.setdefault("azubi", {}); rp_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(cs)
zug_res = run_forecast_zugaenge(df_snapshot=emp_abg, start_date=BASE_DATE, end_date=TARGET_DATE, freq="M", params=rp_zug, vacancies=vac)
zug_ev = zug_res.get("events", pd.DataFrame())
if not zug_ev.empty: zug_res["events"] = enrich_zugaenge_events(zug_ev, cs, rp_zug)
final_emp = zug_res.get("final_state", emp_abg).copy()
if "PersNr" in final_emp.columns: final_emp["PersNr"] = _normalize_persnr_series(final_emp["PersNr"])
final_emp = _apply_salary_automation_to_employee_state(final_emp, target_date=TARGET_DATE, events_df=zug_res.get("events", pd.DataFrame()))
atz_piv = abg_res.get("tables", {}).get("atz_pivot", pd.DataFrame())
atz_status = _resolve_atz_status_map(atz_piv, TARGET_DATE)
snap_upd = _update_existing_rows(cs, final_emp, TARGET_DATE, atz_status)
snap_app = _append_new_people_rows(snap_upd.copy(), cs, final_emp, zug_res)
snap_fin = _finalize_future_snapshot(snap_app.copy(), TARGET_DATE)

stages = {
    "01_rohdaten_ausgangsbestand": cs, "02_forecast_base": emp_base,
    "03_nach_abgaenge": emp_abg, "04_nach_zugaenge": final_emp,
    "05_nach_update_existing_rows": snap_upd, "06_nach_append_new_people_rows": snap_app,
    "07_final_future_snapshot": snap_fin,
}
abg_events = abg_res.get("events_person_level", pd.DataFrame())
zug_events = zug_res.get("events", pd.DataFrame())
base_ids = set(_normalize_persnr_for_audit(cs["PersNr"].dropna()))
abg_lk = _event_lookup(abg_events, "persnr")
zug_lk = _event_lookup(zug_events, "persnr")

print("Benchmark ...")
print(f"\n  {'Stage':<40} {'alt (s)':>8}  {'shadow (s)':>10}  {'Speedup':>8}  {'Zeilen Out':>10}")
print("-" * 85)
t_old_total = t_new_total = 0.0
for sname, df in stages.items():
    n_rows = df.shape[0]; n_cols = df.shape[1]
    iv = df["Is_Vacant"].fillna(False).astype(bool) if "Is_Vacant" in df.columns else pd.Series(False, index=df.index)
    n_persons = df.loc[df["PersNr"].notna() & ~iv, "PersNr"].nunique() if "PersNr" in df.columns else "?"

    t0 = time.perf_counter()
    old = _person_stage_summary(df, sname, sname, base_ids, abg_events, zug_events)
    t_old = time.perf_counter() - t0

    t1 = time.perf_counter()
    new = _person_stage_summary_shadow(df, sname, sname, base_ids, abg_lk, zug_lk)
    t_new = time.perf_counter() - t1

    t_old_total += t_old; t_new_total += t_new
    sp = t_old/t_new if t_new > 0 else 0
    print(f"  {sname:<40} {t_old:>8.4f}  {t_new:>10.4f}  {sp:>7.1f}x  {len(old):>10}")

print("-" * 85)
print(f"  {'GESAMT':.<40} {t_old_total:>8.4f}  {t_new_total:>10.4f}  {t_old_total/t_new_total:>7.1f}x")

# event_lookup Redundanz
t0 = time.perf_counter()
for _ in range(7): _event_lookup(abg_events,"persnr"); _event_lookup(zug_events,"persnr")
t14 = time.perf_counter()-t0
t0 = time.perf_counter(); _event_lookup(abg_events,"persnr"); _event_lookup(zug_events,"persnr"); t2=time.perf_counter()-t0
print(f"\n  _event_lookup 14x={t14:.4f}s  2x={t2:.4f}s  Einsparung={t14-t2:.4f}s")
print(f"  Gesamteinsparung alt->shadow: {t_old_total-t_new_total:.2f}s")
print(f"  Neues geschaetztes build_mak_lineage_audit: ~{t_new_total + t14:.1f}s  (alt: ~{t_old_total:.1f}s)")
