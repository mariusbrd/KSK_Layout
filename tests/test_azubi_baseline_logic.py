import pytest
import pandas as pd
import numpy as np
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from zugaenge.forecast import _next_august_conversion_date

# --- REPLICATE LOGIC TO BE IMPLEMENTED FOR TEST ---
def _estimate_baseline_graduation_date(entry_date: pd.Timestamp, duration_years: float, conversion_month: int = 8, conversion_day: int = 1) -> pd.Timestamp:
    """
    Proposed Logic: 
    1. Calculate estimated end = entry + duration.
    2. Snap to next valid August cycle.
    """
    if pd.isna(entry_date):
        return pd.NaT
        
    # 1. Estimated Finish
    years = int(duration_years)
    months = int((duration_years % 1) * 12)
    estimated_end = entry_date + pd.DateOffset(years=years, months=months)
    
    # 2. August Candidate in same year
    august_candidate = pd.Timestamp(estimated_end.year, conversion_month, conversion_day)
    
    # 3. Decision
    if estimated_end <= august_candidate:
        return august_candidate
    else:
        return pd.Timestamp(estimated_end.year + 1, conversion_month, conversion_day)

def test_baseline_logic():
    print("\n--- Testing Baseline Graduation Estimation ---")
    
    duration = 3.0
    
    # Case 1: Entry early in year (Feb), ends Feb+3y -> Fits in August same year
    entry1 = pd.Timestamp("2024-02-01")
    est_end1 = entry1 + pd.DateOffset(years=3) # 2027-02-01
    # August 2027 is > Feb 2027. So conversion should be 2027-08-01.
    res1 = _estimate_baseline_graduation_date(entry1, duration)
    print(f"Entry: {entry1.date()}, Est End: {est_end1.date()} -> Conversion: {res1.date()}")
    assert res1 == pd.Timestamp("2027-08-01")
    
    # Case 2: Entry late in year (Sep), ends Sep+3y -> Missed August -> Next Year
    entry2 = pd.Timestamp("2024-09-01")
    est_end2 = entry2 + pd.DateOffset(years=3) # 2027-09-01
    # August 2027 is < Sep 2027. So conversion should be 2028-08-01.
    res2 = _estimate_baseline_graduation_date(entry2, duration)
    print(f"Entry: {entry2.date()}, Est End: {est_end2.date()} -> Conversion: {res2.date()}")
    assert res2 == pd.Timestamp("2028-08-01")
    
    # Case 3: Exact hit? Entry 2024-08-01 -> End 2027-08-01 -> Fits exactly?
    entry3 = pd.Timestamp("2024-08-01")
    est_end3 = entry3 + pd.DateOffset(years=3) # 2027-08-01
    # Rule: <= August candidate. 
    # 2027-08-01 <= 2027-08-01 is True.
    res3 = _estimate_baseline_graduation_date(entry3, duration)
    print(f"Entry: {entry3.date()}, Est End: {est_end3.date()} -> Conversion: {res3.date()}")
    assert res3 == pd.Timestamp("2027-08-01")

    # Case 4: Day After? Entry 2024-08-02 -> End 2027-08-02 -> Missed Aug 1st.
    entry4 = pd.Timestamp("2024-08-02")
    est_end4 = entry4 + pd.DateOffset(years=3) # 2027-08-02
    # 2027-08-02 > 2027-08-01. Next year.
    res4 = _estimate_baseline_graduation_date(entry4, duration)
    print(f"Entry: {entry4.date()}, Est End: {est_end4.date()} -> Conversion: {res4.date()}")
    assert res4 == pd.Timestamp("2028-08-01")

    print("--- Success ---")

if __name__ == "__main__":
    test_baseline_logic()
