
import pandas as pd
import numpy as np
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from zugaenge.forecast import run_forecast_zugaenge

df_snapshot = pd.DataFrame([
    {"PersNr": "1", "Organisationseinheit": "OE1", "Jobfamily": "Angestellte", "active": True, "mak": 1.0, "TrfGr": "E9A", "Eintritt": "2020-01-01"}
])

start_date = pd.Timestamp("2025-12-31")
# Test over 10 years to see lifecycle
periods_years = 10

params = {
    "azubi": {
        "active": True,
        "new_cases_per_year": 15,
        "duration_years": 3,
        "retention_rate": 0.8,
        "strategy": "Random"
    },
    "trainee": {"active": False},
    "new_hires": {"active": False},
    "random_seed": 42
}

print(f"Running forecast for {periods_years} years...")
res = run_forecast_zugaenge(df_snapshot, start_date, periods_years=periods_years, params=params)
events = pd.DataFrame(res["events"])

# 1. Check Hires
hires = events[events["type"] == "Azubi_Hire"]
print(f"\nTotal Azubi_Hire: {len(hires)}")
hires['year'] = pd.to_datetime(hires['date']).dt.year
print("Hires per year:")
print(hires.groupby('year').size())

# 2. Check Graduations (Exit or Conversion)
grads = events[events["type"].isin(["Azubi_Exit", "Azubi_Conversion_Out"])]
print(f"\nTotal Graduations (Exits + Out): {len(grads)}")
grads['year'] = pd.to_datetime(grads['date']).dt.year
print("Graduations per year:")
print(grads.groupby('year').size())

# 3. Check Takeovers (Conversions In)
takeovers = events[events["type"] == "Azubi_Conversion_In"]
print(f"\nTotal Takeovers (In): {len(takeovers)}")
takeovers['year'] = pd.to_datetime(takeovers['date']).dt.year
print("Takeovers per year:")
print(takeovers.groupby('year').size())

# 4. Check Cohort 2026 (Starts 2026, Ends 2029)
# 15 hires in 2026 should lead to 12 takeovers and 3 exits in 2029 (80% of 15)
print("\n--- Cohort 2026 Analysis ---")
h_26 = len(hires[hires['year'] == 2026])
t_29 = len(takeovers[takeovers['year'] == 2029])
e_29 = len(events[(events["type"] == "Azubi_Exit") & (pd.to_datetime(events['date']).dt.year == 2029)])

print(f"Hires 2026: {h_26}")
print(f"Takeovers 2029 (Conv_In): {t_29}")
print(f"Exits 2029: {e_29}")

if h_26 == 15 and t_29 == 12 and e_29 == 3:
    print("SUCCESS: Cohort counts are exactly as expected (15 -> 12/3).")
else:
    print("WARNING: Cohort counts mismatch.")

# 5. Check Net Headcount effect of Takeover
# Azubi_Conversion_Out (-1) + Azubi_Conversion_In (+1) = 0
for yr in range(2029, 2035):
    g_yr = len(events[(events["type"] == "Azubi_Conversion_Out") & (pd.to_datetime(events['date']).dt.year == yr)])
    t_yr = len(events[(events["type"] == "Azubi_Conversion_In") & (pd.to_datetime(events['date']).dt.year == yr)])
    if g_yr != t_yr:
        print(f"ERROR: Unbalanced takeover in {yr} (Out={g_yr}, In={t_yr})")
    else:
        print(f"Year {yr}: Takeover net effect is balanced (0 HC).")

# 6. Check MAK vs Headcount Consistency (CRITICAL FIX)
print("\n--- MAK vs Headcount Consistency Check ---")
azu_types = ["Azubi_Hire", "Azubi_Conversion_Out", "Azubi_Conversion_In", "Azubi_Exit"]
azu_events = events[events["type"].isin(azu_types)]

mismatched = []
for idx, row in azu_events.iterrows():
    hc = float(row.get("headcount_change", row.get("count", 0)))
    mak = float(row.get("mak_change", row.get("mak", 0)))
    if abs(hc) != abs(mak):
        mismatched.append(f"{row['type']} at {row['date']}: HC={hc}, MAK={mak}")

if not mismatched:
    print("SUCCESS: All Azubi events have matching MAK and Headcount absolute values.")
else:
    print("ERROR: Inconsistent MAK/HC found:")
    for m in mismatched[:10]: print(f"  - {m}")

# 7. Check Unique IDs and Audit Fields (id: 16)
print("\n--- Unique IDs & Audit Fields Check ---")
all_ids = hires["persnr"].tolist()
unique_ids = hires["persnr"].nunique()
if unique_ids == len(all_ids):
    print(f"SUCCESS: All {unique_ids} hires have unique IDs.")
else:
    print(f"ERROR: Found duplicate IDs in hires! ({unique_ids} unique vs {len(all_ids)} total)")

# Check format (AZ_YEAR_N..)
bad_format = [pid for pid in all_ids if not pid.startswith("AZ_")]
if bad_format:
    print(f"ERROR: Found bad ID formats: {bad_format[:5]}")
else:
    print("SUCCESS: ID format is correct.")

# Check audit fields
if "is_forecast" in events.columns and "entry_date" in events.columns:
    print("SUCCESS: Audit fields (is_forecast, entry_date) are present.")
else:
    print("ERROR: Missing audit fields in events.")

# 8. Test Isolation Mode (CRITICAL FIX id: 16)
print("\n--- Isolation Mode Check ---")
params_iso = params.copy()
params_iso["azubi"]["exclude_baseline_azubis"] = True

# We have 1 person in snapshot (not Azubi, but we'll add one to be sure)
df_snap_with_azu = pd.concat([
    df_snapshot,
    pd.DataFrame([{"PersNr": "BESTAND_AZ", "Organisationseinheit": "OE1", "Jobfamily": "Azubi", "active": True, "mak": 1.0, "Eintritt": "2024-01-01"}])
])

res_iso = run_forecast_zugaenge(df_snap_with_azu, start_date, periods_years=2, params=params_iso)
events_iso = pd.DataFrame(res_iso["events"])

# With duration_years=3, 2 years of forecast should only have Hires, NO graduations from these new hires.
# And with exclude_baseline_azubis=True, the person "BESTAND_AZ" (graduating 2027) should be ignored.
hires_iso = events_iso[events_iso["type"] == "Azubi_Hire"]
grads_iso = events_iso[events_iso["type"].isin(["Azubi_Exit", "Azubi_Conversion_Out"])]

print(f"Isolated Hires (2 yrs): {len(hires_iso)}")
print(f"Isolated Graduations (2 yrs): {len(grads_iso)}")

if len(grads_iso) == 0:
    print("SUCCESS: Isolation works. No graduations in first 2 years for 3-year duration.")
else:
    print(f"ERROR: Found {len(grads_iso)} graduations in isolated 2-year window (expected 0).")
    print(grads_iso[["date", "type", "persnr", "is_forecast"]])
