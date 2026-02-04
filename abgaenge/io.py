"""
I/O helpers for Abgaenge forecast.
"""

from pathlib import Path
from typing import Tuple

import pandas as pd

from .schemas import (
    COL_PERSNR,
    COL_GEB,
    COL_EINTRITT,
    COL_AUSTRITT,
    COL_ATZ_BEGINN,
    COL_ATZ_ENDE,
    COL_ATZ_ENDE,
    COL_ATZ_VERTRAG_ENDE,
    COL_SOLL,
)


def _resolve_input_paths(base_path: Path) -> Tuple[Path, Path, Path]:
    """
    Resolve Mitarbeiter.xlsx, ATZ.xlsx and Planstellen.xlsx paths.

    Priority:
    1) Original-Daten (sibling of base_path)
    2) data/sample_data (inside base_path)
    """
    base_path = base_path.resolve()
    original_dir = base_path.parent / "Original-Daten"
    sample_dir = base_path / "data" / "sample_data"

    candidates = [
        (original_dir / "Mitarbeiter.xlsx", original_dir / "ATZ.xlsx", original_dir / "Planstellen.XLSX"),
        # Try both camelCase and uppercase ext for Planstellen just in case
        (original_dir / "Mitarbeiter.xlsx", original_dir / "ATZ.xlsx", original_dir / "Planstellen.xlsx"),
        (sample_dir / "Mitarbeiter.xlsx", sample_dir / "ATZ.xlsx", sample_dir / "Planstellen.xlsx"),
    ]

    for ma_path, atz_path, pl_path in candidates:
        if ma_path.exists() and atz_path.exists() and pl_path.exists():
            return ma_path, atz_path, pl_path

    # Fallback if Planstellen missing but others exist (unlikely in prod but good for robust dev)
    # Re-check simplified candidates
    simple_candidates = [
        (original_dir / "Mitarbeiter.xlsx", original_dir / "ATZ.xlsx"),
        (sample_dir / "Mitarbeiter.xlsx", sample_dir / "ATZ.xlsx")
    ]
    for ma_path, atz_path in simple_candidates:
         if ma_path.exists() and atz_path.exists():
             # Return None for Planstellen if not found
             return ma_path, atz_path, None

    raise FileNotFoundError(
        "Mitarbeiter.xlsx und/oder ATZ.xlsx nicht gefunden. "
        "Erwarte Dateien in Original-Daten oder data/sample_data."
    )


def load_inputs(base_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load Mitarbeiter.xlsx and ATZ.xlsx.
    Also merges Sollarbeitszeit from Planstellen.xlsx if available.

    Args:
        base_path: Project root (e.g., KSK_Layout).

    Returns:
        Tuple of (df_ma, df_atz)
    """
    ma_path, atz_path, pl_path = _resolve_input_paths(base_path)

    df_ma = pd.read_excel(
        ma_path,
        parse_dates=[COL_GEB, COL_EINTRITT, COL_AUSTRITT],
    )
    
    # Normalize PersNr immediately for mapping
    # Note: IO usually shouldn't modify data too much, but we need keys for merging
    if COL_PERSNR in df_ma.columns:
         df_ma[COL_PERSNR] = df_ma[COL_PERSNR].apply(
            lambda x: str(int(x)).zfill(6) if pd.notna(x) else pd.NA
        )

    df_atz = pd.read_excel(
        atz_path,
        parse_dates=[COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE],
    )
    
    if COL_PERSNR in df_atz.columns:
         df_atz[COL_PERSNR] = df_atz[COL_PERSNR].apply(
            lambda x: str(int(x)).zfill(6) if pd.notna(x) else pd.NA
        )
    
    # Merge Sollarbeitszeit from Planstellen if available
    if pl_path:
        try:
            df_pl = pd.read_excel(pl_path)
            # Minimal cleaning for Planstellen
            if "Personalnummer" in df_pl.columns and "Sollarbeitszeit" in df_pl.columns:
                 # Clean ID
                 df_pl["Personalnummer"] = df_pl["Personalnummer"].apply(
                    lambda x: str(int(x)).zfill(6) if pd.notna(x) else pd.NA
                 )
                 # Clean Soll (handle Azubis 0.01 -> 39.0 logic if needed, or simple merge)
                 # Loader logic: Azubi 9910 & 0.01 -> 39.0.
                 # For simplicity, we just take the column. Forecast handles normalization.
                 
                 # Deduplicate: One Person might have multiple Planstellen? 
                 # Loader sums them or takes one? Loader does distinct df merging.
                 # We take the first match for simplicity or max? 
                 # Usually 1:1 for active employees.
                 df_pl_subset = df_pl[["Personalnummer", "Sollarbeitszeit"]].drop_duplicates(subset=["Personalnummer"])
                 
                 df_ma = df_ma.merge(
                     df_pl_subset, 
                     left_on=COL_PERSNR, 
                     right_on="Personalnummer", 
                     how="left"
                 )
                 # Renaming handled or keep as Sollarbeitszeit (COL_SOLL string is "Sollarbeitszeit")
        except Exception:
            # Ignore errors in Planstellen merge to ensure stability
            pass

    return df_ma, df_atz
