
import pandas as pd
import numpy as np
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from components.sidebar import apply_filters
import streamlit as st

# Mock Session State
if "selected_org_units" not in st.session_state:
    st.session_state["selected_org_units"] = []

# Mock DataFrame with "612.0" as float
df_test = pd.DataFrame({
    "PersNr": ["001"],
    "Kürzel OrgEinheit": [612.0], # Float in DataFrame
    "Dummy": ["Test"]
})

# Case 1: Filter with Float 612.0 (Simulating Multiselect value)
st.session_state["selected_org_units"] = [612.0]

print("--- Test 1: Filter by [612.0] ---")
result1 = apply_filters(df_test)
print(f"Result Count: {len(result1)}")

if not result1.empty:
    print("SUCCESS: 612.0 matched 612.0 (after normalization)")
else:
    print("FAILURE: 612.0 did not match.")

# Case 2: Filter with String "612"
st.session_state["selected_org_units"] = ["612"]
print("\n--- Test 2: Filter by ['612'] ---")
result2 = apply_filters(df_test)
print(f"Result Count: {len(result2)}")

if not result2.empty:
    print("SUCCESS: '612' matched 612.0 (after normalization)")
else:
    print("FAILURE: '612' did not match.")

# Case 3: Filter with String "612.0"
st.session_state["selected_org_units"] = ["612.0"]
print("\n--- Test 3: Filter by ['612.0'] ---")
result3 = apply_filters(df_test)
print(f"Result Count: {len(result3)}")

if not result3.empty:
    print("SUCCESS: '612.0' matched 612.0 (after normalization)")
else:
    print("FAILURE: '612.0' did not match.")
