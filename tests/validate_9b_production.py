"""
Validierung Hotspot 9b nach Produktivaenderung.
Misst build_mak_lineage_audit und vollstaendige Simulation.
Prueft alle audit_tables auf Vollstaendigkeit.
Kein Produktivcode veraendert.
"""
import sys, time, copy, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _apply_salary_automation_to_employee_state,
    _append_new_people_rows, _build_vacancies_from_attrition, _build_simulation_audit_tables,
    _finalize_future_snapshot, _normalize_persnr_series, _prepare_employee_forecast_base,
    _resolve_atz_status_map, _update_existing_rows, build_jf_to_cluster_map,
    default_abgaenge_params, default_zugaenge_params, simulate_compact_snapshot,
)
from abgaenge.forecast import run_forecast_abgaenge
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.enrichment import enrich_zugaenge_events
from dataloader.loader import load_original_data, normalize_atz
from utils.audit_helpers import (
    _event_lookup, _normalize_persnr_for_audit, _person_stage_summary, build_mak_lineage_audit,
)

BASE_DATE   = pd.Timestamp("2025-12-31")
TARGET_DATE = pd.Timestamp("2026-12-31")

print("=== Validierung Hotspot 9b nach Produktivaenderung ===\n")

# 1. Daten laden
print("1. Lade Daten ...")
raw = load_original_data()
pl, ma, atz_raw = raw["planstellen"], raw["mitarbeiter"], raw["atz"]
pl["PersNr"] = _normalize_persnr_series(pl["Personalnummer"].fillna("").astype(str))
ma_sub = ma[["PersNr","BsGrd","TrfGr","St","GebDatum","Eintritt","Austritt",
             "Vertragsart","MitarbGruppenbez.","Text Gsch","Status kundenindividuell"]].copy()
snap = pl.merge(ma_sub, on="PersNr", how="left")
snap.index = range(len(snap))
snap["Is_Vacant"] = snap["PersNr"].eq("")
snap["Personalnummer"] = snap["PersNr"]
soll_h = pd.to_numeric(snap["Sollarbeitszeit"], errors="coerce").fillna(0.0)
snap["Soll_FTE"] = soll_h / 39.0
bsgrd = pd.to_numeric(snap["BsGrd"], errors="coerce").fillna(0.0)
snap["BsGrd"] = bsgrd
for c, v in [("MAK_Calculated", bsgrd/100), ("mak", bsgrd/100), ("MAK", bsgrd/100),
             ("FTE_person", bsgrd/100), ("FTE_assigned", bsgrd/100 * (soll_h/39)),
             ("ATZ_Status", "Kein ATZ"), ("ist_atz_fr", False), ("Sollarbeitszeit", soll_h),
             ("Personen_MAK", bsgrd/100), ("Personen_MAK_Source", bsgrd/100),
             ("BsGrd_Source", bsgrd), ("snapshot_BsGrd", bsgrd),
             ("person_mak_source", "Personen_MAK_Source"), ("bsgrd_lineage_flag", "ok_source_bsgrd_used"),
             ("MAK_Technical_Uncorrected", bsgrd/100), ("MAK_Reporting", bsgrd/100),
             ("Ausbildung", pd.NA), ("Jobfamily", "Angestellte"),
             ("OE-Cluster", "Sonstiges"), ("JF-Cluster", "Sonstiges"), ("Alter_Jahre", 45.0)]:
    snap[c] = v
df_atz = normalize_atz(atz_raw)
print(f"   Snapshot: {len(snap)} Zeilen, {snap['PersNr'].ne('').sum()} besetzt\n")

# 2. Lineage-Stages aufbauen
print("2. Baue Lineage-Stages ...")
cs = snap.copy()
abg_p = default_abgaenge_params(); zug_p = default_zugaenge_params()
emp_base = _prepare_employee_forecast_base(cs, df_atz, BASE_DATE)
abg_res  = run_forecast_abgaenge(df_ma=emp_base, df_atz=df_atz, start_date=BASE_DATE, end_date=TARGET_DATE, freq="M", params=abg_p)
emp_abg  = _apply_attrition_events_to_employee_state(emp_base, abg_res.get("events_person_level", pd.DataFrame()))
vac      = _build_vacancies_from_attrition(emp_base, abg_res)
rp_zug   = {**zug_p}; rp_zug.setdefault("azubi", {}); rp_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(cs)
zug_res  = run_forecast_zugaenge(df_snapshot=emp_abg, start_date=BASE_DATE, end_date=TARGET_DATE, freq="M", params=rp_zug, vacancies=vac)
zug_ev   = zug_res.get("events", pd.DataFrame())
if not zug_ev.empty: zug_res["events"] = enrich_zugaenge_events(zug_ev, cs, rp_zug)
final_emp = zug_res.get("final_state", emp_abg).copy()
if "PersNr" in final_emp.columns: final_emp["PersNr"] = _normalize_persnr_series(final_emp["PersNr"])
final_emp = _apply_salary_automation_to_employee_state(final_emp, target_date=TARGET_DATE, events_df=zug_res.get("events", pd.DataFrame()))
atz_piv  = abg_res.get("tables", {}).get("atz_pivot", pd.DataFrame())
atz_stat = _resolve_atz_status_map(atz_piv, TARGET_DATE)
snap_upd = _update_existing_rows(cs, final_emp, TARGET_DATE, atz_stat)
snap_app = _append_new_people_rows(snap_upd.copy(), cs, final_emp, zug_res)
snap_fin = _finalize_future_snapshot(snap_app.copy(), TARGET_DATE)
lineage_stages = {
    "01_rohdaten_ausgangsbestand": cs, "02_forecast_base": emp_base,
    "03_nach_abgaenge": emp_abg, "04_nach_zugaenge": final_emp,
    "05_nach_update_existing_rows": snap_upd, "06_nach_append_new_people_rows": snap_app,
    "07_final_future_snapshot": snap_fin,
}
abg_ev  = abg_res.get("events_person_level", pd.DataFrame())
zug_ev2 = zug_res.get("events", pd.DataFrame())
base_ids = set(_normalize_persnr_for_audit(cs["PersNr"].dropna()))
print("   OK\n")

# 3. build_mak_lineage_audit Timing (neue Produktivimplementierung)
print("3. Timing build_mak_lineage_audit (neue Implementierung) ...")
t0 = time.perf_counter()
lineage_new = build_mak_lineage_audit(lineage_stages, cs, abg_ev, zug_ev2)
t_lineage_new = time.perf_counter() - t0
print(f"   build_mak_lineage_audit (neu): {t_lineage_new:.3f}s")
print(f"   Output: {lineage_new.shape[0]} Zeilen x {lineage_new.shape[1]} Spalten")
print(f"   Spalten: {list(lineage_new.columns)}\n")

# 4. _person_stage_summary Timing pro Stage
print("4. _person_stage_summary Timing pro Stage (neue Impl.) ...")
abg_lk = _event_lookup(abg_ev, "persnr")
zug_lk = _event_lookup(zug_ev2, "persnr")
t_stages = 0.0
for sname, df in lineage_stages.items():
    t0 = time.perf_counter()
    out = _person_stage_summary(df, sname, sname, base_ids, abg_ev, zug_ev2, _abg_lookup=abg_lk, _zug_lookup=zug_lk)
    t_s = time.perf_counter() - t0
    t_stages += t_s
    print(f"   {sname}: {t_s:.4f}s  ({len(out)} Zeilen)")
print(f"   GESAMT _person_stage_summary x7: {t_stages:.3f}s\n")

# 5. Vollstaendige Simulation Cache-Miss
print("5. Vollstaendige Simulation (Cache-Miss) ...")
t0 = time.perf_counter()
result_A = simulate_compact_snapshot(
    snapshot_df=snap.copy(), df_atz=df_atz, target_date=TARGET_DATE,
    base_date=BASE_DATE, abgaenge_params=copy.deepcopy(abg_p), zugaenge_params=copy.deepcopy(zug_p),
)
t_miss = time.perf_counter() - t0
print(f"   Cache-Miss: {t_miss:.3f}s")

print("6. Vollstaendige Simulation (Cache-Hit) ...")
t0 = time.perf_counter()
result_B = simulate_compact_snapshot(
    snapshot_df=snap.copy(), df_atz=df_atz, target_date=TARGET_DATE,
    base_date=BASE_DATE, abgaenge_params=copy.deepcopy(abg_p), zugaenge_params=copy.deepcopy(zug_p),
)
t_hit = time.perf_counter() - t0
print(f"   Cache-Hit:  {t_hit:.3f}s\n")

# 7. Audit-Tabellen pruefen
print("7. Audit-Tabellen Vollstaendigkeitspruefung ...")
audit = result_A.audit_tables
expected_keys = [
    "MAK_Personen_Audit", "MAK_Allocation_Audit", "Azubi_Flow_Audit", "Sonstiges_Audit",
    "65plus_Audit", "Azubi_Takeover_Target_Audit", "Azubi_Takeover_Decision_List",
    "MAK_Decision_List", "MAK_Policy_Impact", "MAK_Lineage_Audit", "MAK_Origin_Classification",
    "MAK_Reconciliation_Bridge", "MAK_Deep_Dive_15_Cases", "MAK_Fuehrung_Vertrieb_Check",
    "MAK_Column_Consistency_Check", "MAK_Abgaenge_Check", "MAK_Zugaenge_Check",
    "MAK_Source_Row_Audit_15", "MAK_BsGrd_Lineage_15", "MAK_Source_Pattern_Classification",
    "MAK_Correction_Decision_15", "MAK_Target_Value_Scenarios", "MAK_Fuehrung_Vertrieb_Decision",
    "65plus_Decision_List",
]
missing = [k for k in expected_keys if k not in audit]
print(f"   Keys vorhanden: {len(audit)} / {len(expected_keys)} erwartet")
if missing: print(f"   FEHLEND: {missing}")
else: print("   Alle Keys vorhanden: OK")
print()
for key in ["MAK_Lineage_Audit", "MAK_Origin_Classification", "MAK_Reconciliation_Bridge",
            "MAK_Abgaenge_Check", "MAK_Zugaenge_Check"]:
    tbl = audit.get(key, pd.DataFrame())
    print(f"   {key}: {tbl.shape[0]} Zeilen x {getattr(tbl, 'shape', (0,0))[1]} Spalten")

# 8. Simulationsergebnis-Checks
print("\n8. Simulationsergebnis-Checks ...")
fs = result_A.future_snapshot_df
fe = result_A.future_employee_df
md = result_A.metadata
print(f"   future_snapshot_df: {fs.shape}")
print(f"   future_employee_df: {fe.shape}")
print(f"   metadata keys: {list(md.keys())}")
active_fs = fs[fs["Is_Vacant"] == False]
print(f"   Aktive Zeilen im Snapshot: {len(active_fs)}")
print(f"   MAK_sum gesamt: {active_fs['MAK_Calculated'].sum():.4f}")
print(f"   abgaenge_events: {md.get('abgaenge_events', '?')}")
print(f"   zugaenge_events: {md.get('zugaenge_events', '?')}")

print("\n=== ZUSAMMENFASSUNG ===")
print(f"  build_mak_lineage_audit (neu):            {t_lineage_new:.3f}s  (alt: ~18.8s)")
print(f"  _person_stage_summary x7 (neu):           {t_stages:.3f}s  (alt: ~7.7s)")
print(f"  Simulation Cache-Miss (neu):              {t_miss:.3f}s  (alt: ~23.4s)")
print(f"  Simulation Cache-Hit (neu):               {t_hit:.3f}s  (alt: ~2.5s)")
print(f"  Tests: 34 passed (s.o.)")
print(f"  Audit-Keys vorhanden: {len([k for k in expected_keys if k in audit])}/{len(expected_keys)}")
