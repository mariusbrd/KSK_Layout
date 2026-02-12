# -*- coding: utf-8 -*-
"""Run forecast on real data and check what Kuerzel OrgEinheit the events carry."""
import sys, os
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np

# Load data same way Page 3 does
PLAN_FILE = r"f:\Dropbox\Projekte\KSKBöB_HR-Dashboard_Production\Original-Daten\Planstellen.XLSX"
MA_FILE = r"f:\Dropbox\Projekte\KSKBöB_HR-Dashboard_Production\Original-Daten\Mitarbeiter.xlsx"
ATZ_FILE = r"f:\Dropbox\Projekte\KSKBöB_HR-Dashboard_Production\Original-Daten\ATZ.xlsx"

from dataloader.loader import normalize_persnr, clean_planstellen, combine_to_snapshot, calculate_mak_vectorized, normalize_atz

# Load raw
plan = pd.read_excel(PLAN_FILE)
ma = pd.read_excel(MA_FILE)
ma["GebDatum"] = pd.to_datetime(ma["GebDatum"], errors="coerce")
ma["Eintritt"] = pd.to_datetime(ma["Eintritt"], errors="coerce") 
ma["PersNr"] = normalize_persnr(ma["PersNr"])
atz = pd.read_excel(ATZ_FILE, parse_dates=["Beginn", "Ende", "Ende ATZ Vertrag"])
atz = normalize_atz(atz)

# combine_to_snapshot
snap = combine_to_snapshot(ma, plan, atz, pd.DataFrame(columns=["Personalnummer", "BV Ausbildungsgruppentext"]))

# Simulate Page 3 aggregation
col = "Kürzel OrgEinheit"
df_ma = snap.copy()
df_ma = df_ma.dropna(subset=["PersNr"])
df_ma = calculate_mak_vectorized(df_ma, set())

agg_dict = {
    "MAK_Calculated": "sum",
    "GebDatum": "first",
    "Eintritt": "first",
    "Austritt": "first",
    "Status kundenindividuell": "first",
    "Sollarbeitszeit": "sum",
    "Organisationseinheit": "first",
}
for c in ["Geschlecht", "Planstelle", col, "ATZ_Status", "Jobfamily", "TrfGr", "St"]:
    if c in df_ma.columns:
        agg_dict[c] = "first"

df_ma = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
df_ma["Sollarbeitszeit"] = 39.0
df_ma["BsGrd"] = df_ma["MAK_Calculated"] * 100.0

print(f"df_ma has {len(df_ma)} persons")
print(f"  5910: {(df_ma[col].astype(str) == '5910').sum()} persons")
print(f"  591: {(df_ma[col].astype(str) == '591').sum()} persons")

# Run forecast
from abgaenge.forecast import run_forecast_abgaenge

params = {
    "random_seed": 42,
    "components": {"atz": True, "retirement": True, "quit": True, "ruhend": True},
    "atz": {"new_atz_rate": 0.05, "atz_eligible_age_min": 55, "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 2.5, "atz_duration_fr_years": 2.5},
    "retirement": {"rent_rate_65": 0.95, "rent_rate_60_65": 0.02},
    "quit": {"quit_rate_base": 0.03},
    "ruhend": {"ruhend_new_cases_per_year": 2, "ruhend_return_rate": 0.5, "ruhend_avg_duration_months": 12},
}

result = run_forecast_abgaenge(
    df_ma=df_ma,
    df_atz=atz,
    start_date=pd.Timestamp("2024-12-31"),
    end_date=pd.Timestamp("2034-12-31"),
    freq="Q",
    params=params,
)

events = result["events_person_level"]
print(f"\nTotal events: {len(events)}")

# Check events for 591/5910
if col in events.columns:
    ev_5910 = events[events[col].astype(str) == "5910"]
    ev_591 = events[events[col].astype(str) == "591"]
    print(f"Events with Kuerzel={col}='5910': {len(ev_5910)}")
    print(f"Events with Kuerzel={col}='591': {len(ev_591)}")
    
    if not ev_5910.empty:
        print(f"\n5910 events:")
        print(ev_5910[["persnr", "reason_code", col, "Organisationseinheit"]].to_string())
    
    if not ev_591.empty:
        print(f"\n591 events:")
        print(ev_591[["persnr", "reason_code", col, "Organisationseinheit"]].to_string())
    
    # Check what the user sees: OrgEinheit name
    if "Organisationseinheit" in events.columns:
        verbund = events[events["Organisationseinheit"].astype(str).str.contains("Herrenberg")]
        if not verbund.empty:
            print(f"\nAll Herrenberg events (by Name):")
            print(verbund[["persnr", "reason_code", col, "Organisationseinheit"]].to_string())
else:
    print(f"Column '{col}' NOT in events. Columns: {events.columns.tolist()}")
