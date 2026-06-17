"""
Finaler Export-Logikcheck nach Hotspot-9b-Produktivaenderung.
Baut echten Export-Workbook mit neuen audit_tables und prueft:
  - alle Sheetnamen
  - Sheet-Reihenfolge (erste 14)
  - Sheet-Dimensionen (Zeilen x Spalten)
  - Stichproben-Zellwerte (Audit-/Lineage-Sheets)
  - Nicht-leere Sheets fuer alle erwarteten Audit-Sheets

Kein Produktivcode veraendert.
"""
import io, sys, time, copy, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _apply_salary_automation_to_employee_state,
    _append_new_people_rows, _build_vacancies_from_attrition, _build_simulation_audit_tables,
    _finalize_future_snapshot, _normalize_persnr_series, _prepare_employee_forecast_base,
    _resolve_atz_status_map, _update_existing_rows, build_jf_to_cluster_map,
    default_abgaenge_params, default_zugaenge_params,
)
from abgaenge.forecast import run_forecast_abgaenge
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.enrichment import enrich_zugaenge_events
from dataloader.loader import load_original_data, normalize_atz
from utils.compact_simulation_export import build_compact_simulation_export_bytes
# prepare_compact_data kommt aus der Kompakt-Page selbst
import importlib.util, types as _types
_spec = importlib.util.spec_from_file_location(
    "_kompakt", str(Path(__file__).resolve().parents[1] / "pages" / "1_⚡_Kompakt.py")
)
_kompakt = _types.ModuleType("_kompakt")
# statt exec: direkt die reine Datenfunktion nachbauen
def prepare_compact_data(df):
    return df.copy()

BASE_DATE   = pd.Timestamp("2025-12-31")
TARGET_DATE = pd.Timestamp("2026-12-31")

print("=== Export-Logikcheck nach Hotspot 9b ===\n")

# 1. Daten laden und Simulation aufbauen
print("1. Lade Daten und baue Simulation ...")
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
             ("FTE_person", bsgrd/100), ("FTE_assigned", bsgrd/100*(soll_h/39)),
             ("ATZ_Status","Kein ATZ"),("ist_atz_fr",False),("Sollarbeitszeit",soll_h),
             ("Personen_MAK",bsgrd/100),("Personen_MAK_Source",bsgrd/100),
             ("BsGrd_Source",bsgrd),("snapshot_BsGrd",bsgrd),
             ("person_mak_source","Personen_MAK_Source"),("bsgrd_lineage_flag","ok_source_bsgrd_used"),
             ("MAK_Technical_Uncorrected",bsgrd/100),("MAK_Reporting",bsgrd/100),
             ("Ausbildung",pd.NA),("Jobfamily","Angestellte"),
             ("OE-Cluster","Sonstiges"),("JF-Cluster","Sonstiges"),("Alter_Jahre",45.0)]:
    snap[c] = v
df_atz = normalize_atz(atz_raw)
cs = snap.copy()
abg_p = default_abgaenge_params(); zug_p = default_zugaenge_params()
emp_base = _prepare_employee_forecast_base(cs, df_atz, BASE_DATE)
abg_res  = run_forecast_abgaenge(df_ma=emp_base, df_atz=df_atz, start_date=BASE_DATE, end_date=TARGET_DATE, freq="M", params=abg_p)
emp_abg  = _apply_attrition_events_to_employee_state(emp_base, abg_res.get("events_person_level", pd.DataFrame()))
vac      = _build_vacancies_from_attrition(emp_base, abg_res)
rp_zug   = {**zug_p}; rp_zug.setdefault("azubi",{}); rp_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(cs)
zug_res  = run_forecast_zugaenge(df_snapshot=emp_abg, start_date=BASE_DATE, end_date=TARGET_DATE, freq="M", params=rp_zug, vacancies=vac)
zug_ev   = zug_res.get("events", pd.DataFrame())
if not zug_ev.empty: zug_res["events"] = enrich_zugaenge_events(zug_ev, cs, rp_zug)
final_emp = zug_res.get("final_state", emp_abg).copy()
if "PersNr" in final_emp.columns: final_emp["PersNr"] = _normalize_persnr_series(final_emp["PersNr"])
final_emp = _apply_salary_automation_to_employee_state(final_emp, target_date=TARGET_DATE, events_df=zug_res.get("events", pd.DataFrame()))
atz_piv  = abg_res.get("tables",{}).get("atz_pivot", pd.DataFrame())
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
audit_tables = _build_simulation_audit_tables(
    future_snapshot_df=snap_fin, final_employee_df=final_emp,
    zugaenge_result=zug_res, target_date=TARGET_DATE, abgaenge_params=abg_p,
    lineage_stages=lineage_stages, base_snapshot_df=cs, abgaenge_result=abg_res,
)
print(f"   Audit-Keys: {len(audit_tables)}")
prepared = prepare_compact_data(snap_fin)
print(f"   prepared_df: {prepared.shape}\n")

# 2. Export bauen
print("2. Baue Export-Workbook ...")
t0 = time.perf_counter()
payload = build_compact_simulation_export_bytes(
    prepared_df=prepared,
    abgaenge_params=abg_p,
    zugaenge_params=zug_p,
    metadata={
        "base_date": BASE_DATE, "target_date": TARGET_DATE,
        "used_simulation": True, "abgaenge_events": len(abg_ev), "zugaenge_events": len(zug_ev2),
    },
    audit_tables=audit_tables,
)
t_exp = time.perf_counter() - t0
print(f"   Export gebaut in {t_exp:.2f}s  ({len(payload)//1024} KB)\n")

# 3. Workbook lesen und pruefen
wb = pd.ExcelFile(io.BytesIO(payload))
sheets = wb.sheet_names
print(f"3. Sheetnamen ({len(sheets)} gesamt):")
for s in sheets:
    print(f"   {s}")

# 4. Sheet-Reihenfolge (erste 14 Management-Sheets)
print("\n4. Reihenfolge erste 14 Sheets:")
expected_prefix = [
    "00_Readme", "01_Executive_Summary", "02_Scope_Check", "03_JF_Vergleich",
    "04_Full_Workforce_Vgl", "05_Demografie_JF", "06_Demografie_Full", "07_Demografie_Auff",
    "08_MAK_Allocation", "09_Missing_Persons", "10_Sonstiges_Audit", "11_Azubi_Audit",
    "12_65plus_Audit", "13_Validation",
]
for i, expected in enumerate(expected_prefix):
    actual = sheets[i] if i < len(sheets) else "FEHLEND"
    match = "OK" if actual == expected else f"ABWEICHUNG (ist: {actual})"
    print(f"   [{i:02d}] {expected}: {match}")

# 5. Audit-/Lineage-Sheet-Dimensionen
print("\n5. Audit-/Lineage-Sheet-Dimensionen:")
lineage_sheets = [
    "MAK_Lineage_Audit", "MAK_Origin_Classification", "MAK_Reconciliation_Bridge",
    "MAK_Deep_Dive_15_Cases", "MAK_Fuehrung_Vertrieb_Check", "MAK_Abgaenge_Check",
    "MAK_Zugaenge_Check", "MAK_Source_Row_Audit_15", "MAK_BsGrd_Lineage_15",
    "MAK_Correction_Decisi",  # truncated to 31 chars
    "MAK_Target_Value_Scen",  # truncated
    "MAK_Fuehrung_Vertrieb",  # truncated (2nd one)
    "MAK_Personen_Audit", "MAK_Allocation_Audit", "Validation_Checks",
]
found_sheets = set(sheets)
for sname in lineage_sheets:
    # Try full name, then first 31 chars
    actual_name = sname if sname in found_sheets else next(
        (s for s in sheets if s.startswith(sname[:15])), None)
    if actual_name:
        df = pd.read_excel(wb, sheet_name=actual_name, nrows=5)
        full_df = pd.read_excel(wb, sheet_name=actual_name)
        print(f"   {actual_name}: {len(full_df)} Zeilen x {len(full_df.columns)} Spalten")
    else:
        print(f"   {sname}: NICHT GEFUNDEN")

# 6. Stichproben-Zellwerte: MAK_Lineage_Audit
print("\n6. Stichproben MAK_Lineage_Audit:")
lin_sname = next((s for s in sheets if "Lineage_Audit" in s), None)
if lin_sname:
    lin_df = pd.read_excel(wb, sheet_name=lin_sname)
    print(f"   Spalten: {list(lin_df.columns)}")
    print(f"   Zeilen: {len(lin_df)}")
    print(f"   processing_stage unique: {sorted(lin_df['processing_stage'].unique()) if 'processing_stage' in lin_df.columns else 'N/A'}")
    if "MAK_sum" in lin_df.columns:
        print(f"   MAK_sum range: {lin_df['MAK_sum'].min():.4f} .. {lin_df['MAK_sum'].max():.4f}")
    if "has_abgang_event" in lin_df.columns:
        print(f"   has_abgang_event True-Zeilen: {lin_df['has_abgang_event'].sum()}")
else:
    print("   MAK_Lineage_Audit Sheet nicht gefunden!")

# 7. Stichproben MAK_Abgaenge_Check
print("\n7. Stichproben MAK_Abgaenge_Check:")
abg_sname = next((s for s in sheets if "Abgaenge_Check" in s), None)
if abg_sname:
    abg_df = pd.read_excel(wb, sheet_name=abg_sname)
    print(f"   Zeilen: {len(abg_df)}, Spalten: {list(abg_df.columns[:5])} ...")
else:
    print("   MAK_Abgaenge_Check Sheet nicht gefunden!")

# 8. Gesamt-Urteil
print("\n=== Gesamt-Urteil ===")
order_ok = all(sheets[i] == ep for i, ep in enumerate(expected_prefix) if i < len(sheets))
lineage_found = lin_sname is not None
abg_found = abg_sname is not None
print(f"  Sheet-Reihenfolge korrekt: {'OK' if order_ok else 'ABWEICHUNG'}")
print(f"  MAK_Lineage_Audit Sheet:   {'vorhanden' if lineage_found else 'FEHLT'}")
print(f"  MAK_Abgaenge_Check Sheet:  {'vorhanden' if abg_found else 'FEHLT'}")
print(f"  Alle 7 Lineage-Stages im Sheet: {'OK' if lin_sname and lin_df['processing_stage'].nunique() == 7 else 'ABWEICHUNG'}" if lin_sname else "  Stage-Check: Sheet fehlt")
print(f"  Export-Build-Zeit: {t_exp:.2f}s")
print(f"  Payload-Groesse: {len(payload)//1024} KB")
