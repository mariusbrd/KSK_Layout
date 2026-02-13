
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
# User: 2025-12-31 to 2027-12-31 = 2 years
# periods_years param in run_forecast_zugaenge defines end_date = start_date + offset
# If start is 2025-12-31, periods_years=2 -> end is 2027-12-31.

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

res = run_forecast_zugaenge(df_snapshot, start_date, periods_years=2, params=params)
events = res["events"]

azubi_hires = events[events["type"] == "Azubi_Hire"]
print(f"Total Azubi_Hire events: {len(azubi_hires)}")

# Scope check (as done in Hybrid Page)
end_date = start_date + pd.DateOffset(years=2)
in_scope = azubi_hires[(azubi_hires["date"] >= start_date) & (azubi_hires["date"] <= end_date)]
print(f"In-Scope Azubi_Hire events: {len(in_scope)}")
print(f"Filter loss: {len(azubi_hires) - len(in_scope)}")

# Breakdown by year
azubi_hires['year'] = azubi_hires['date'].dt.year
print("\nBreakdown by year:")
print(azubi_hires.groupby('year').size())
