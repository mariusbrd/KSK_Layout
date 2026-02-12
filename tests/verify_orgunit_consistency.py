# -*- coding: utf-8 -*-
"""Verify OrgUnit consistency fix: Organisationseinheit as unique filter key."""
import sys, os
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import re

from dataloader.loader import load_original_data, combine_to_snapshot, calculate_mak_vectorized, normalize_atz
from abgaenge.forecast import run_forecast_abgaenge

# --- Load data ---
data = load_original_data()
snap = combine_to_snapshot(data["mitarbeiter"], data["planstellen"], data["atz"], data["ausbildung"])

# --- Simulate Page 3 aggregation ---
col_code = "Kuerzel OrgEinheit"
col_name_raw = "Organisationseinheit"
df_ma = snap.copy()
df_ma = df_ma.dropna(subset=["PersNr"])
df_ma = calculate_mak_vectorized(df_ma, set())
agg_dict = {"MAK_Calculated": "sum", "GebDatum": "first", "Eintritt": "first",
            "Austritt": "first", "Status kundenindividuell": "first", "Sollarbeitszeit": "sum",
            "Organisationseinheit": "first"}
for c in ["Geschlecht", "Planstelle", "Kürzel OrgEinheit", "ATZ_Status", "Jobfamily", "TrfGr", "St"]:
    if c in df_ma.columns:
        agg_dict[c] = "first"
df_ma = df_ma.groupby("PersNr", as_index=False).agg(agg_dict)
df_ma["Sollarbeitszeit"] = 39.0
df_ma["BsGrd"] = df_ma["MAK_Calculated"] * 100.0

# --- Run forecast ---
params = {
    "random_seed": 42,
    "components": {"atz": True, "retirement": True, "quit": True, "ruhend": True},
    "atz": {"new_atz_rate": 0.05, "atz_eligible_age_min": 55, "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 2.5, "atz_duration_fr_years": 2.5},
    "retirement": {"rent_rate_65": 0.95, "rent_rate_60_65": 0.02},
    "quit": {"quit_rate_base": 0.03},
    "ruhend": {"ruhend_new_cases_per_year": 2, "ruhend_return_rate": 0.5, "ruhend_avg_duration_months": 12},
}
result = run_forecast_abgaenge(df_ma=df_ma, df_atz=data["atz"],
    start_date=pd.Timestamp("2024-12-31"), end_date=pd.Timestamp("2034-12-31"),
    freq="Q", params=params)

events = result["events_person_level"]

# === TEST 1: Check that chart labels (Organisationseinheit) are unique per sub-unit ===
if "Organisationseinheit" in events.columns:
    herrenberg_events = events[events["Organisationseinheit"].str.contains("Herrenberg", na=False)]
    chart_labels = herrenberg_events.groupby("Organisationseinheit").size()
    print("TEST 1: Chart labels for Herrenberg events")
    print(chart_labels.to_string())
    print()

# === TEST 2: Filter by "Beratungs-Center Herrenberg" → exact match ===
selected = ["Beratungs-Center Herrenberg"]
if "Organisationseinheit" in events.columns:
    filtered = events[events["Organisationseinheit"].isin(selected)]
    print(f"TEST 2: Filter='{selected[0]}' -> {len(filtered)} events")
    if not filtered.empty:
        print(filtered[["persnr", "reason_code", "Kürzel OrgEinheit", "Organisationseinheit"]].to_string())
    print()

# === TEST 3: Filter by "Akquisepool Herrenberg" → separate result ===
selected2 = ["Akquisepool Herrenberg"]
if "Organisationseinheit" in events.columns:
    filtered2 = events[events["Organisationseinheit"].isin(selected2)]
    print(f"TEST 3: Filter='{selected2[0]}' -> {len(filtered2)} events")
    if not filtered2.empty:
        print(filtered2[["persnr", "reason_code", "Kürzel OrgEinheit", "Organisationseinheit"]].to_string())
    print()

# === TEST 4: Filter by "Beratungs-Center-Verbund Herrenberg" (5910) ===
selected3 = ["Beratungs-Center-Verbund Herrenberg"]
if "Organisationseinheit" in events.columns:
    filtered3 = events[events["Organisationseinheit"].isin(selected3)]
    print(f"TEST 4: Filter='{selected3[0]}' -> {len(filtered3)} events")
    if not filtered3.empty:
        print(filtered3[["persnr", "reason_code", "Kürzel OrgEinheit", "Organisationseinheit"]].to_string())
    print()

# === TEST 5: Sidebar display format ===
pairs = snap[["Kürzel OrgEinheit", "Organisationseinheit"]].drop_duplicates()
display_map = {
    row["Organisationseinheit"]: f"{row['Kürzel OrgEinheit']} - {row['Organisationseinheit']}"
    for _, row in pairs.iterrows()
}
herrenberg_displays = {k: v for k, v in display_map.items() if "Herrenberg" in k}
print("TEST 5: Sidebar display labels for Herrenberg units:")
for name, label in sorted(herrenberg_displays.items()):
    print(f"  Key='{name}' -> Display='{label}'")
print()

# === TEST 6: No code collision ===
code_591_names = pairs[pairs["Kürzel OrgEinheit"] == "591"]["Organisationseinheit"].tolist()
print(f"TEST 6: Code '591' maps to names: {code_591_names}")
# All should now be independently selectable in sidebar
print(f"  -> Each is a separate selectable item: {all(n in display_map for n in code_591_names)}")

print()
print("=== ALL TESTS COMPLETE ===")
