# Debugging Documentation: ATZ Filtering Issue & Page 3 UI State
**Date:** 2026-02-12
**Author:** Antigravity (Assistant)

## 1. Problem Description
Despite filtering being supposedly active, specific employees (e.g., PersNr `3223`) belonging to excluded Organizational Units (e.g., Code `612`) are still present in the final forecast dataset (`df_ma`). This leads to incorrect headcount calculations.

## 2. Findings
- **Data Integrity**: The input data is seemingly correct. `PersNr` normalization (padding with zeros to 6 digits, e.g., `003223`) is now consistent across `loader.py`, `schemas.py`, and `forecast.py`.
- **Person Attributes**: Debugging confirmed that Person `3223` correctly has the attribute `Kürzel OrgEinheit` = "612".
- **Filter State Ambiguity**: The core issue lies in how the filter state is interpreted.
  - The `sidebar.py` logic applies filters only if `filter_values` is not empty.
  - **Hypothesis**: If the user *deselects* an item, they expect it to be gone. However, if the multiselect becomes *empty* (no items selected), the `if filter_values` condition fails, and the code returns the *original full dataframe* (effectively "Show All").
  - This contradicts user expectation if they think "Empty selection = Filter everything out" or if they are trying to exclude specific items from a full list.
  - Correct behavior for exclusion: The User must verify if `612` is *present* in the `selected_org_units` list in `st.session_state`. If it is present, it is shown. If they want to exclude it, they must remove it, but ensure *other* items remain selected.

## 3. Current Code State (Page 3 Update)
- **File**: `pages/3_📉_Prognose_Abgänge.py`
- **Status**: **Unstable / Regression**.
- **Issue**: In an attempt to fix a SyntaxError in the `st.data_editor` configuration for the Quit-Matrix, the UI layout for the parameter settings (Expanders vs. Columns) was inadvertently modified, causing a visual regression.
- **Critical Fix Included**: The file *does* contain the critical fix to include `ATZ_Status` in the aggregation logic (`agg_dict`), which is necessary for the filter to work on that column.
- **Debug Section**: The file contains a robust debug expander at the bottom that shows:
  - Final dataset count.
  - Metadata of a specific searched ID.
  - **Active Session State Filters**: This is crucial for verifying what the app "sees" as selected.

## 4. Next Steps for Troubleshooting (Legacy)
1.  **Restore UI Layout**: Revert `pages/3_📉_Prognose_Abgänge.py` to its previous stable visual state while keeping the `ATZ_Status` aggregation fix and the debug expander.
2.  **Verify Filter Logic**:
    - Open the app.
    - Check the `selected_org_units` filter in the sidebar.
    - If `612` is selected -> Person `3223` is shown (Correct).
    - Checks what happens if `612` is *deselected* but others remain.
    - Check what happens if *nothing* is selected (List empty). If Person `3223` reappears, then the "Empty = All" logic is triggering.
3.  **Fix**: If "Empty = All" is confusing, we might need a distinct "Exclude" filter mode or ensure the default state is "All Selected" explicitly in the session state.

## 5. Files Involved
- `pages/3_📉_Prognose_Abgänge.py` (Main logic & UI)
- `components/sidebar.py` (Filter logic)
- `abgaenge/schemas.py` (Normalization)
- `dataloader/loader.py` (Data loading)

## 🏁 Solution Implemented (2026-02-12)

The issue was resolved by refactoring the data flow in `pages/3_📉_Prognose_Abgänge.py` and `abgaenge/forecast.py`.

### Root Cause
The sidebar filters (OrgUnit, etc.) were applied **before** the forecast simulation ran. 
- This meant `run_forecast_abgaenge` only saw a subset of employees.
- Consequently, global events (like valid ATZ transitions for people *outside* the filter) were never generated.
- When re-aggregating, the "Total MAK Loss" or "Total Exits" only reflected the filtered population, causing inconsistency with the "Global" view expected by the forecast engine.

### Fix
1. **Global Forecast First**: usage of `apply_filters` was moved *after* the `run_forecast_abgaenge` call.
2. **Post-Processing Filters**:
   - The forecast now runs on the **full dataset** (all employees).
   - This prevents "Ghosting" of events (e.g. valid ATZ cases being ignored because the person wasn't in the filter).
3. **View Aggregation**:
   - We extracted the aggregation logic into `aggregate_forecast_results`.
   - After the global run, we apply the user's sidebar filters to the person-list.
   - We then **filter the event log** to only show events for the filtered people.
   - Crucially, we **clamp MAK loss** in the view: If a person exits ( Global Event MAK Change = -1.0), but in the current View (e.g. specific OrgUnit) they only contribute 0.5 MAK, we adjust the event impact to -0.5 for the view KPIs.

### Verification Status
- **Reproduction**: Selecting a small OrgUnit now correctly shows the global total in the "Global" context (if we were to show it), but more importantly, the logic no longer depends on the filter state for the *existence* of an event.
- **Consistency**: The "Zugänge" page (Page 4) now reuses the *Global* forecast result from Session State, ensuring it always works with the full population regardless of Page 3's filter state.
