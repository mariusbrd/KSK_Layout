
import pandas as pd
import numpy as np
import sys
import os

# Adds project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from abgaenge.forecast import run_forecast_abgaenge

# 1. Create Mock Data containing '5910'
# We need minimal required columns: PersNr, GebDatum, Eintritt, BsGrd, Status kundenindividuell
df_ma_mock = pd.DataFrame({
    "PersNr": ["001", "002"],
    "GebDatum": ["1980-01-01", "1990-01-01"],
    "Eintritt": ["2010-01-01", "2020-01-01"],
    "Status kundenindividuell": ["Aktiv", "Aktiv"],
    "BsGrd": [100, 50],
    "Sollarbeitszeit": [39.0, 39.0],
    # Crucial Column:
    "Kürzel OrgEinheit": ["5910", 5910],  # One string, one int
    "Organisationseinheit": ["TestStr", "TestInt"]
})

print("--- Source Data ---")
print(df_ma_mock[["PersNr", "Kürzel OrgEinheit"]])

# 2. Run Forecast
# We mock params to trigger some events (e.g. Quit)
params = {
    "random_seed": 42,
    "components": {"quit": True, "retirement": False, "atz": False, "ruhend": False},
    "quit": {"quit_rate_base": 0.99}  # Force Quits
}

# Mock ATZ as empty
df_atz_mock = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende"])

try:
    result = run_forecast_abgaenge(
        df_ma=df_ma_mock,
        df_atz=df_atz_mock,
        start_date="2024-01-01",
        end_date="2025-01-01",
        freq="M",
        params=params
    )
    
    events = result["events_person_level"]
    print("\n--- Result Events ---")
    if not events.empty:
        # Check if Kürzel OrgEinheit exists and what values it has
        if "Kürzel OrgEinheit" in events.columns:
            print(events[["persnr", "reason_code", "Kürzel OrgEinheit"]])
            
            # Check for 5910
            has_5910 = events["Kürzel OrgEinheit"].astype(str).str.contains("5910").any()
            if has_5910:
                print("SUCCESS: Found '5910' in events!")
            else:
                print("FAILURE: '5910' NOT found in events. Values are:", events["Kürzel OrgEinheit"].unique())
        else:
            print("FAILURE: Column 'Kürzel OrgEinheit' missing in events!")
            print("Columns:", events.columns)
    else:
        print("WARNING: No events generated. Increase quit rate?")

except Exception as e:
    print(f"CRITICAL ERROR: {e}")
    import traceback
    traceback.print_exc()
