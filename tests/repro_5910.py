
import pandas as pd
import numpy as np
import sys
import os
import re

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from components.sidebar import apply_filters
import streamlit as st

# Mock Session State
if "selected_org_units" not in st.session_state:
    st.session_state["selected_org_units"] = []

# Mock DataFrame: 5910 as Integer, String, Float
df_test = pd.DataFrame({
    "PersNr": ["001", "002", "003", "004"],
    "Kürzel OrgEinheit": [5910, "5910", 5910.0, " 5910"], 
    "Dummy": ["Int", "Str", "Float", "Space"]
})

print("--- DataFrame Before Filter ---")
print(df_test)

# Case 1: Filter with Integer 5910 (most likely from Sidebar Multiselect on Int data)
st.session_state["selected_org_units"] = [5910]
print("\n--- Test 1: Filter by [5910] (Int) ---")
result1 = apply_filters(df_test)
print(f"Result Count: {len(result1)} (Expected 4)")
if len(result1) == 4:
    print("SUCCESS: Int 5910 matched all variants.")
else:
    print("FAILURE: Int 5910 did not match all.")
    print("DEBUG: Remaining Rows:\n", result1)

# Case 2: Filter with String "5910"
st.session_state["selected_org_units"] = ["5910"]
print("\n--- Test 2: Filter by ['5910'] (Str) ---")
result2 = apply_filters(df_test)
print(f"Result Count: {len(result2)} (Expected 4)")
if len(result2) == 4:
    print("SUCCESS: Str '5910' matched all variants.")
else:
    print("FAILURE: Str '5910' did not match all.")
    print("DEBUG: Remaining Rows:\n", result2)

# Case 3: Filter with Float 5910.0
st.session_state["selected_org_units"] = [5910.0]
print("\n--- Test 3: Filter by [5910.0] (Float) ---")
result3 = apply_filters(df_test)
print(f"Result Count: {len(result3)} (Expected 4)")
if len(result3) == 4:
    print("SUCCESS: Float 5910.0 matched all variants.")
else:
    print("FAILURE: Float 5910.0 did not match all.")
    print("DEBUG: Remaining Rows:\n", result3)

# Case 4: Filter with String "5910.0"
st.session_state["selected_org_units"] = ["5910.0"]
print("\n--- Test 4: Filter by ['5910.0'] (Str Float) ---")
result4 = apply_filters(df_test)
print(f"Result Count: {len(result4)} (Expected 4)")
if len(result4) == 4:
     print("SUCCESS: Str '5910.0' matched all variants.")
else:
     print("FAILURE: Str '5910.0' did not match all.")
     print("DEBUG: Remaining Rows:\n", result4)
