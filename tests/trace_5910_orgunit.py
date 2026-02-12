# -*- coding: utf-8 -*-
"""Trace 5910 through the full pipeline."""
import sys, os
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

PLAN_FILE = r"f:\Dropbox\Projekte\KSKBöB_HR-Dashboard_Production\Original-Daten\Planstellen.XLSX"
MA_FILE = r"f:\Dropbox\Projekte\KSKBöB_HR-Dashboard_Production\Original-Daten\Mitarbeiter.xlsx"

# Step 1: Raw Planstellen
plan = pd.read_excel(PLAN_FILE)
col = [c for c in plan.columns if "rzel" in c][0]
print(f"[1-RAW PLANSTELLEN] dtype={plan[col].dtype}")
print(f"  591 exact: {(plan[col].astype(str) == '591').sum()} rows")
print(f"  5910 exact: {(plan[col].astype(str) == '5910').sum()} rows")

# Step 2: Raw Mitarbeiter  
ma = pd.read_excel(MA_FILE)
if col in ma.columns:
    print(f"\n[2-RAW MITARBEITER] dtype={ma[col].dtype}")
    print(f"  591 exact: {(ma[col].astype(str) == '591').sum()} rows")
    print(f"  5910 exact: {(ma[col].astype(str) == '5910').sum()} rows")
else:
    print(f"\n[2-RAW MITARBEITER] Column '{col}' NOT present")
    # Check if it comes from Planstellen merge
    org_cols = [c for c in ma.columns if "org" in c.lower() or "einheit" in c.lower()]
    print(f"  Org-related columns: {org_cols}")

# Step 3: After combine_to_snapshot (simplified - just merge plan + ma)
from dataloader.loader import normalize_persnr, clean_planstellen

plan_clean = clean_planstellen(plan.copy())
print(f"\n[3-CLEAN PLANSTELLEN] dtype={plan_clean[col].dtype}")
print(f"  591 exact: {(plan_clean[col].astype(str) == '591').sum()} rows")
print(f"  5910 exact: {(plan_clean[col].astype(str) == '5910').sum()} rows")

# Check persons in 5910
if "Personalnummer" in plan_clean.columns:
    persnrs_5910 = plan_clean[plan_clean[col].astype(str) == "5910"]["Personalnummer"].unique()
    persnrs_591 = plan_clean[plan_clean[col].astype(str) == "591"]["Personalnummer"].unique()
    print(f"  5910 PersNrs: {persnrs_5910}")
    print(f"  591 PersNrs: {persnrs_591}")
    
    # Check overlap
    overlap = set(str(x) for x in persnrs_5910) & set(str(x) for x in persnrs_591)
    if overlap:
        print(f"  OVERLAP! Persons in both 591 AND 5910: {overlap}")
    else:
        print(f"  No overlap between 591 and 5910 persons")

# Step 4: Simulate the groupby aggregation from Page 3
# Merge plan with ma (simplified)
if "Personalnummer" in plan_clean.columns:
    plan_clean["PersNr_Plan"] = normalize_persnr(plan_clean["Personalnummer"])
    
    # Get PersNr from ma
    ma["PersNr"] = normalize_persnr(ma["PersNr"])
    
    # Merge like combine_to_snapshot
    merged = plan_clean.merge(ma, left_on="PersNr_Plan", right_on="PersNr", how="left", suffixes=("", "_ma"))
    
    # Check Kuerzel OrgEinheit after merge  
    col_merged = [c for c in merged.columns if "rzel" in c and "_ma" not in c][0] if any("rzel" in c and "_ma" not in c for c in merged.columns) else None
    if col_merged:
        print(f"\n[4-MERGED] column={col_merged} dtype={merged[col_merged].dtype}")
        print(f"  591 exact: {(merged[col_merged].astype(str) == '591').sum()} rows")
        print(f"  5910 exact: {(merged[col_merged].astype(str) == '5910').sum()} rows")
    
    # Step 5: Aggregate
    agg_dict = {col_merged: "first"} if col_merged else {}
    if col_merged and "PersNr" in merged.columns:
        agg = merged.groupby("PersNr", as_index=False).agg(agg_dict)
        print(f"\n[5-AGGREGATED] dtype={agg[col_merged].dtype}")
        print(f"  591 exact: {(agg[col_merged].astype(str) == '591').sum()} rows")
        print(f"  5910 exact: {(agg[col_merged].astype(str) == '5910').sum()} rows")
        
        # Check the overlapping persons
        if overlap:
            for p in overlap:
                val = agg[agg["PersNr"] == p][col_merged].values
                print(f"  Overlap person {p}: aggregated to '{val}'")
