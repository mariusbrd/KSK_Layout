import pandas as pd
import sys
import os

def inspect_atz():
    print("=== INSPECT: ATZ.xlsx Structure ===")
    
    # Path to original data
    path = r"f:\Dropbox\Projekte\KSKBöB_HR-Dashboard_Production\Original-Daten\ATZ.xlsx"
    
    if not os.path.exists(path):
        print(f"❌ File not found: {path}")
        return

    try:
        df = pd.read_excel(path)
        print(f"Loaded DataFrame with shape: {df.shape}")
        print("\nColumns:")
        print(df.columns.tolist())
        
        # Check for Phase column variations
        phase_cols = [c for c in df.columns if "phase" in c.lower() or "art" in c.lower() or "status" in c.lower()]
        print(f"\nPotential Phase Columns found: {phase_cols}")
        
        for col in phase_cols:
            print(f"\nUnique values in '{col}':")
            print(df[col].unique())
            
        # Check date columns
        date_cols = [c for c in df.columns if "beginn" in c.lower() or "ende" in c.lower()]
        print(f"\nDate Columns found: {date_cols}")
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], errors='coerce')
            
        # Check durations
        if 'PersNr' in df.columns and 'Phase' in df.columns and 'Beginn' in df.columns:
            ar = df[df['Phase'] == 'AR'][['PersNr', 'Beginn']].rename(columns={'Beginn': 'AR_Start'})
            fr = df[df['Phase'] == 'FR'][['PersNr', 'Beginn']].rename(columns={'Beginn': 'FR_Start'})
            
            # Check for FR without AR (Orphans)
            merged_all = pd.merge(ar, fr, on='PersNr', how='outer')
            
            orphans = merged_all[merged_all['AR_Start'].isna() & merged_all['FR_Start'].notna()]
            if not orphans.empty:
                print(f"\n[WARNING] Found {len(orphans)} FR cases without AR start (Orphans):")
                print(orphans[['PersNr', 'FR_Start']])
            else:
                print("\n[INFO] No FR orphans found (all have AR start).")

            merged = pd.merge(ar, fr, on='PersNr', how='inner')
            merged['Duration_Days'] = (merged['FR_Start'] - merged['AR_Start']).dt.days
            merged['Duration_Months'] = merged['Duration_Days'] / 30.44
             
            print("\nDuration Analysis (AR Start -> FR Start):")
            print(merged[['PersNr', 'AR_Start', 'FR_Start', 'Duration_Months']].sort_values('Duration_Months').head(10))

            
    except Exception as e:
        print(f"❌ Error loading Excel: {e}")

if __name__ == "__main__":
    inspect_atz()
