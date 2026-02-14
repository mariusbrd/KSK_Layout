import pytest
import pandas as pd
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dataloader.loader import _zero_out_azubi_mak

def test_azubi_mak_zeroing():
    """
    Verifies that _zero_out_azubi_mak correctly:
    1. Identifies Azubis via TrfGr ("TVA") or Jobfamily ("Azubi").
    2. Sets MAK, MAK_Calculated, mak, BsGrd to 0.0.
    3. Preserves original values in *_raw columns.
    """
    print("\n--- Testing Azubi MAK Zeroing ---")
    
    # Setup Test Data
    df = pd.DataFrame({
        "PersNr": ["101", "102", "103", "104"],
        "TrfGr": ["E13", "TVAöD", "E9", "TVA-B BBiG"],
        "Jobfamily": ["Angestellte", "Azubi", "Angestellte", "Unbekannt"], # 104 found via TrfGr
        "MAK": [1.0, 1.0, 0.5, 1.0],
        "MAK_Calculated": [1.0, 1.0, 0.5, 1.0],
        "BsGrd": [100, 100, 50, 100],
        "mak": [1.0, 1.0, 0.5, 1.0]
    })
    
    print("Original Data:")
    print(df[["PersNr", "TrfGr", "Jobfamily", "MAK"]])
    
    # Apply Logic
    df_processed = _zero_out_azubi_mak(df)
    
    print("\nProcessed Data:")
    print(df_processed[["PersNr", "MAK", "MAK_raw", "BsGrd", "BsGrd_raw"]])
    
    # Assertions
    
    # 1. Non-Azubis (101, 103) should be untouched
    non_azubis = df_processed[df_processed["PersNr"].isin(["101", "103"])]
    assert (non_azubis["MAK"] > 0).all(), "Non-Azubis should have MAK > 0"
    assert (non_azubis["BsGrd"] > 0).all()
    
    # 2. Azubis (102, 104) should be zeroed
    azubis = df_processed[df_processed["PersNr"].isin(["102", "104"])]
    
    # Check Zeroing
    assert (azubis["MAK"] == 0.0).all(), "Azubi MAK must be 0.0"
    assert (azubis["MAK_Calculated"] == 0.0).all()
    assert (azubis["mak"] == 0.0).all()
    assert (azubis["BsGrd"] == 0).all()
    
    # Check RAW preservation
    assert (azubis["MAK_raw"] == 1.0).all(), "Original MAK must be preserved in MAK_raw"
    assert (azubis["BsGrd_raw"] == 100).all()
    
    # Check consistency: Non-Azubis should NOT have _raw columns created if logic is row-wise? 
    # Actually vectorization creates column for all. 
    # Check values for non-azubis in raw columns -> should be NaN or Original?
    # Logic: df["MAK_raw"] = df["MAK"] assigns for ALL rows.
    assert (non_azubis["MAK_raw"] == non_azubis["MAK"]).all()
    
    print("SUCCESS: Centralized Azubi Zeroing works as expected.")

if __name__ == "__main__":
    test_azubi_mak_zeroing()
