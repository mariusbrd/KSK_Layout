"""
Verify that direct attribute filtering on events works correctly.
Tests:
1. Filter by OrgUnit → only events for that OrgUnit remain
2. Filter by Jobfamily → only events for that Jobfamily remain  
3. No filter → all events remain
4. Filter by non-existent OrgUnit → 0 events
"""
import pandas as pd
import numpy as np
import re
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock events (simulating global forecast output)
events_global = pd.DataFrame({
    "persnr": ["001", "002", "003", "004", "005"],
    "reason_code": ["QUIT", "QUIT", "ATZ_END", "RETIREMENT", "ATZ_AR_FR"],
    "Kürzel OrgEinheit": ["591", "591", "5910", "612", "612.0"],
    "Organisationseinheit": [
        "Beratungs-Center Herrenberg",
        "Beratungs-Center Herrenberg", 
        "Beratungs-Center-Verbund Herrenberg",
        "Filiale Merklingen",
        "Filiale Merklingen"
    ],
    "Jobfamily": ["Kundenberatung Privat", "Vermögensberatung", "Kundenberatung Privat", "IT", "IT"],
    "headcount_change": [-1, -1, -1, -1, 0],
    "mak_change": [-1.0, -0.5, -1.0, -1.0, -0.5],
})

def filter_events(events, selected_orgs=None, selected_jf=None, selected_genders=None, valid_ids=None):
    """Replicate the new P04 filtering logic."""
    events_view = events.copy()
    
    # 1. OrgUnit direct
    if selected_orgs and "Kürzel OrgEinheit" in events_view.columns:
        s_ev = events_view["Kürzel OrgEinheit"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        f_ev = {re.sub(r"\.0$", "", str(x).strip()) for x in selected_orgs}
        events_view = events_view[s_ev.isin(f_ev)]
    
    # 2. Jobfamily direct
    if selected_jf and "Jobfamily" in events_view.columns:
        events_view = events_view[events_view["Jobfamily"].isin(selected_jf)]
    
    # 3. PersNr fallback for other filters
    if selected_genders and valid_ids is not None:
        events_view = events_view[events_view["persnr"].isin(valid_ids)]
    
    return events_view

# Test 1: Filter by OrgUnit 591
result = filter_events(events_global, selected_orgs=["591"])
assert len(result) == 2, f"Expected 2, got {len(result)}"
assert all(result["Kürzel OrgEinheit"] == "591"), "Wrong OrgUnit"
print("✅ Test 1 PASSED: Filter OrgUnit 591 → 2 events")

# Test 2: Filter by OrgUnit 5910
result = filter_events(events_global, selected_orgs=["5910"])
assert len(result) == 1, f"Expected 1, got {len(result)}"
print("✅ Test 2 PASSED: Filter OrgUnit 5910 → 1 event")

# Test 3: Filter by OrgUnit 612 (with .0 normalization)
result = filter_events(events_global, selected_orgs=["612"])
assert len(result) == 2, f"Expected 2, got {len(result)}"
print("✅ Test 3 PASSED: Filter OrgUnit 612 (normalizes 612.0) → 2 events")

# Test 4: No filter → all events
result = filter_events(events_global)
assert len(result) == 5, f"Expected 5, got {len(result)}"
print("✅ Test 4 PASSED: No filter → 5 events")

# Test 5: Non-existent OrgUnit
result = filter_events(events_global, selected_orgs=["9999"])
assert len(result) == 0, f"Expected 0, got {len(result)}"
print("✅ Test 5 PASSED: Non-existent OrgUnit → 0 events")

# Test 6: Jobfamily filter
result = filter_events(events_global, selected_jf=["IT"])
assert len(result) == 2, f"Expected 2, got {len(result)}"
print("✅ Test 6 PASSED: Filter Jobfamily IT → 2 events")

# Test 7: Combined OrgUnit + Jobfamily
result = filter_events(events_global, selected_orgs=["591"], selected_jf=["Kundenberatung Privat"])
assert len(result) == 1, f"Expected 1, got {len(result)}"
print("✅ Test 7 PASSED: OrgUnit 591 + Jobfamily KP → 1 event")

# Test 8: Filter back to all after filtering
result1 = filter_events(events_global, selected_orgs=["591"])
result2 = filter_events(events_global)
assert len(result2) == 5, "Global values changed after filter!"
print("✅ Test 8 PASSED: Unfiltert after filter → global values unchanged")

print("\n🎉 ALL TESTS PASSED")
