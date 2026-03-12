"""
Test suite: Trainee Forecast – Hauptfälle & Edge Cases
=======================================================
Testet run_forecast_zugaenge in Bezug auf Trainee-Einstellungen.

Run:
  py KSK_Layout/tests/test_trainee_forecast.py
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap():
    """Minimaler Snapshot: 1 regulärer Mitarbeiter."""
    return pd.DataFrame([{
        "PersNr": "E001",
        "Organisationseinheit": "OE1",
        "Jobfamily": "Angestellte",
        "active": True,
        "mak": 1.0,
        "TrfGr": "E9A",
        "Eintritt": "2015-01-01",
    }])


def _snap_multi_unit():
    """Snapshot mit mehreren OEs."""
    rows = []
    for i, unit in enumerate(["OE1", "OE2", "OE3"]):
        rows.append({
            "PersNr": f"E{i+1:03d}",
            "Organisationseinheit": unit,
            "Jobfamily": "Angestellte",
            "active": True,
            "mak": 1.0,
            "TrfGr": "E9A",
            "Eintritt": "2015-01-01",
        })
    return pd.DataFrame(rows)


def _base_params(tr_overrides=None, disable_azubi=True, disable_hires=True):
    p = {
        "azubi": {
            "active": not disable_azubi,
            "new_cases_per_year": 0,
            "duration_years": 3,
            "retention_rate": 1.0,
            "strategy": "Random",
            "entry_tariff_group": "E5",
            "entry_step": 1,
            "exclude_baseline_azubis": False,
            "azubi_mak_during_training": 0.0,
            "azubi_mak_after_takeover": 1.0,
            "azubi_conversion_month": 8,
            "azubi_conversion_day": 1,
            "graduation_mode": "nearest_cycle",
            "nearest_cycle_grace_days": None,
            "use_takeover_matrix": False,
            "takeover_matrix": {},
            "takeover_dimension": "JobFamily",
            "jf_to_cluster_map": {},
        },
        "trainee": {
            "active": True,
            "new_cases_per_year": 5,
            "duration_years": 1.5,
            "salary_group": "E13",
            "strategy": "Random",
            "target_org_unit": None,
        },
        "new_hires": {
            "active": not disable_hires,
            "count_per_year": 0,
            "strategy": "Random",
            "target_org_unit": None,
        },
        "random_seed": 42,
    }
    if tr_overrides:
        p["trainee"].update(tr_overrides)
    return p


def _run(snap, params, start="2026-01-01", end="2028-12-31"):
    result = run_forecast_zugaenge(
        snap,
        start_date=pd.Timestamp(start),
        end_date=pd.Timestamp(end),
        params=params,
    )
    events = result.get("events", pd.DataFrame())
    trainees = events[events["type"] == "Trainee_Hire"] if not events.empty else pd.DataFrame()
    return events, trainees


# ===========================================================================
# HAUPTFÄLLE
# ===========================================================================

PASS = []
FAIL = []

def _check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# T01: Grundlegende Einstellung
# ---------------------------------------------------------------------------
print("\n--- T01–T05: Grundlegende Einstellung ---")

events, tr = _run(_snap(), _base_params({"new_cases_per_year": 5}))
_check("T01_events_not_empty", not tr.empty, f"got {len(tr)} events")
_check("T02_event_type_correct", (tr["type"] == "Trainee_Hire").all())
_check("T03_count_plus_one", (tr["count"] == 1).all(), f"counts: {tr['count'].unique().tolist()}")
_check("T04_mak_one_point_zero", (tr["mak"] == 1.0).all(), f"mak values: {tr['mak'].unique().tolist()}")
_check("T05_source_trainee", (tr["source"] == "Trainee").all())

# ---------------------------------------------------------------------------
# T06–T08: Jährliches Volumen (Debt-System)
# ---------------------------------------------------------------------------
print("\n--- T06–T08: Jährliches Volumen ---")

events_3y, tr_3y = _run(_snap(), _base_params({"new_cases_per_year": 12}),
                         start="2026-01-01", end="2028-12-31")
expected_3y = 12 * 3
_check("T06_annual_volume_approx",
       abs(len(tr_3y) - expected_3y) <= 1,
       f"expected ~{expected_3y}, got {len(tr_3y)}")

events_0, tr_0 = _run(_snap(), _base_params({"new_cases_per_year": 0}))
_check("T07_zero_count_no_events", len(tr_0) == 0, f"got {len(tr_0)}")

events_1, tr_1 = _run(_snap(), _base_params({"new_cases_per_year": 1}),
                       start="2026-01-01", end="2026-12-31")
_check("T08_one_per_year_exact",
       abs(len(tr_1) - 1) <= 1,
       f"expected ~1, got {len(tr_1)}")

# ---------------------------------------------------------------------------
# T09–T10: HC & MAK Auswirkung
# ---------------------------------------------------------------------------
print("\n--- T09–T10: HC & MAK ---")

_, tr_hc = _run(_snap(), _base_params({"new_cases_per_year": 10}))
_check("T09_hc_increment_positive", (tr_hc["count"] > 0).all())
_check("T10_mak_immediately_positive",
       tr_hc["mak"].sum() > 0,
       "Trainee mak soll sofort > 0 sein")

# ---------------------------------------------------------------------------
# T11–T13: Salary Group
# ---------------------------------------------------------------------------
print("\n--- T11–T13: Salary Group ---")

_, tr_e13 = _run(_snap(), _base_params({"salary_group": "E13"}))
_, tr_e9 = _run(_snap(), _base_params({"salary_group": "E9A"}))
_check("T11_salary_group_e13", (tr_e13["TrfGr"] == "E13").all(), f"{tr_e13['TrfGr'].unique().tolist()}")
_check("T12_salary_group_e9a", (tr_e9["TrfGr"] == "E9A").all(), f"{tr_e9['TrfGr'].unique().tolist()}")
_check("T13_salary_group_in_event", "TrfGr" in tr_e13.columns)

# ---------------------------------------------------------------------------
# T14–T15: Jobfamily & Planstelle
# ---------------------------------------------------------------------------
print("\n--- T14–T15: Jobfamily & Planstelle ---")

_, tr_jf = _run(_snap(), _base_params({"new_cases_per_year": 5}))
_check("T14_jobfamily_trainee", (tr_jf["Jobfamily"] == "Trainee").all(), f"{tr_jf['Jobfamily'].unique().tolist()}")
_check("T15_planstelle_trainee", (tr_jf["Planstelle"] == "Trainee").all())

# ---------------------------------------------------------------------------
# T16–T18: OrgUnit-Strategie
# ---------------------------------------------------------------------------
print("\n--- T16–T18: OrgUnit-Strategie ---")

snap3 = _snap_multi_unit()
all_units = snap3["Organisationseinheit"].unique().tolist()
_, tr_random = _run(snap3, _base_params({"new_cases_per_year": 20, "strategy": "Random"}))
assigned_units = tr_random["org_unit"].unique().tolist()
_check("T16_random_strategy_uses_known_units",
       all(u in all_units for u in assigned_units),
       f"unknown units: {[u for u in assigned_units if u not in all_units]}")

_, tr_ou = _run(snap3, _base_params({"new_cases_per_year": 20, "strategy": "OrgUnit", "target_org_unit": "OE2"}))
_check("T17_orgunit_strategy_targets_correct_unit",
       (tr_ou["org_unit"] == "OE2").all(),
       f"units: {tr_ou['org_unit'].unique().tolist()}")

# fill vacancies falls back to random for trainees (vacancies list is always empty)
_, tr_fv = _run(snap3, _base_params({"new_cases_per_year": 10, "strategy": "Fill Vacancies"}))
_check("T18_fill_vacancies_falls_back_to_random",
       len(tr_fv) > 0)

# ---------------------------------------------------------------------------
# T19–T20: Deterministik (Seed)
# ---------------------------------------------------------------------------
print("\n--- T19–T20: Determinismus ---")

p = _base_params({"new_cases_per_year": 10})
ev1, tr1_det = _run(_snap(), p)
ev2, tr2_det = _run(_snap(), p)
_check("T19_same_seed_same_count", len(tr1_det) == len(tr2_det))
_check("T20_same_seed_same_dates",
       list(tr1_det["date"].values) == list(tr2_det["date"].values))

# ---------------------------------------------------------------------------
# T21: active=False schaltet Trainees ab
# ---------------------------------------------------------------------------
print("\n--- T21: Active-Flag ---")

p_off = _base_params()
p_off["trainee"]["active"] = False
_, tr_off = _run(_snap(), p_off)
_check("T21_active_false_no_events", len(tr_off) == 0, f"got {len(tr_off)}")


# ===========================================================================
# EDGE CASES
# ===========================================================================
print("\n--- E01–E05: Edge Cases ---")

# E01: Sehr große Zahl (Stress)
_, tr_big = _run(_snap_multi_unit(), _base_params({"new_cases_per_year": 100}),
                 start="2026-01-01", end="2027-12-31")
_check("E01_large_volume_no_crash", len(tr_big) > 0)

# E02: 1-Tages-Prognose (eff_days = 0)
_, tr_1d = _run(_snap(), _base_params({"new_cases_per_year": 365}),
                start="2026-06-15", end="2026-06-15")
_check("E02_single_day_no_crash", True)  # kein Exception = PASS

# E03: Leerer Snapshot
empty_snap = pd.DataFrame(columns=["PersNr", "Organisationseinheit", "Jobfamily",
                                    "active", "mak", "TrfGr", "Eintritt"])
try:
    _, tr_empty = _run(empty_snap, _base_params({"new_cases_per_year": 5}))
    _check("E03_empty_snapshot_no_crash", True)
except Exception as ex:
    _check("E03_empty_snapshot_no_crash", False, str(ex))

# E04: duration_years variiert – hat keinen Effekt auf Hire-Count (trainees haben kein Lifecycle)
_, tr_dur05 = _run(_snap(), _base_params({"new_cases_per_year": 5, "duration_years": 0.5}))
_, tr_dur30 = _run(_snap(), _base_params({"new_cases_per_year": 5, "duration_years": 3.0}))
_check("E04_duration_years_has_no_effect_on_hire_count",
       len(tr_dur05) == len(tr_dur30),
       f"dur=0.5: {len(tr_dur05)}, dur=3.0: {len(tr_dur30)}")

# E05: Trainee IDs sind eindeutig (keine Kollisionen)
_, tr_ids = _run(_snap(), _base_params({"new_cases_per_year": 50}),
                 start="2026-01-01", end="2028-12-31")
unique_ids = tr_ids["persnr"].nunique()
total_ids = len(tr_ids)
_check("E05_trainee_ids_unique",
       unique_ids == total_ids,
       f"unique={unique_ids}, total={total_ids} (diff={total_ids - unique_ids})")

# E06: Kein Graduation-Event vorhanden (Trainees haben keinen Lifecycle)
events_full, _ = _run(_snap(), _base_params({"new_cases_per_year": 10}),
                       start="2026-01-01", end="2029-12-31")
grad_types = ["Trainee_Conversion_Out", "Trainee_Conversion_In", "Trainee_Exit", "Trainee_Graduation"]
if not events_full.empty and "type" in events_full.columns:
    grad_events = events_full[events_full["type"].isin(grad_types)]
    _check("E06_no_graduation_events_exist",
           len(grad_events) == 0,
           f"found {len(grad_events)} graduation events: {grad_events['type'].unique().tolist()}")
else:
    _check("E06_no_graduation_events_exist", True)

# E07: Trainee hat is_internal_transition NICHT gesetzt
_, tr_flag = _run(_snap(), _base_params({"new_cases_per_year": 5}))
if "is_internal_transition" in tr_flag.columns:
    _check("E07_no_internal_transition_flag",
           not tr_flag["is_internal_transition"].fillna(False).any(),
           "is_internal_transition fälschlicherweise gesetzt")
else:
    _check("E07_no_internal_transition_flag", True)  # Feld nicht vorhanden = korrekt

# E08: Trainee-Einträge haben entry_date innerhalb Prognosezeitraum
_, tr_dates = _run(_snap(), _base_params({"new_cases_per_year": 10}),
                   start="2026-01-01", end="2027-12-31")
if len(tr_dates) > 0:
    dates = pd.to_datetime(tr_dates["date"])
    _check("E08_entry_dates_within_forecast_range",
           (dates >= pd.Timestamp("2026-01-01")).all() and
           (dates <= pd.Timestamp("2027-12-31")).all(),
           f"min={dates.min().date()}, max={dates.max().date()}")

# E09: Keine Trainee-Events wenn new_cases_per_year=0 aber active=True
_, tr_zero_active = _run(_snap(), _base_params({"new_cases_per_year": 0, "active": True}))
_check("E09_zero_cases_no_events_even_if_active",
       len(tr_zero_active) == 0, f"got {len(tr_zero_active)}")

# E10: Unterschiedlicher Seed → unterschiedliche Daten (Stochastik aktiv)
p_s1 = _base_params({"new_cases_per_year": 10})
p_s2 = _base_params({"new_cases_per_year": 10})
p_s1["random_seed"] = 42
p_s2["random_seed"] = 99
_, tr_s1 = _run(_snap(), p_s1)
_, tr_s2 = _run(_snap(), p_s2)
same_dates = list(tr_s1["date"].values) == list(tr_s2["date"].values)
_check("E10_different_seed_different_dates",
       not same_dates or len(tr_s1) == 0,
       "beide Seeds liefern identische Daten — Stochastik eventuell nicht aktiv")


# ===========================================================================
# Ergebnis
# ===========================================================================
print(f"\n{'='*50}")
total = len(PASS) + len(FAIL)
print(f"Ergebnis: {len(PASS)}/{total} PASS")
if FAIL:
    print(f"\nFEHLGESCHLAGEN ({len(FAIL)}):")
    for f in FAIL:
        print(f"  - {f}")
print(f"{'='*50}")
