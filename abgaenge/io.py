"""
I/O helpers for Abgaenge forecast.
"""

from pathlib import Path
from typing import Tuple, Optional, Any

import pandas as pd

from .schemas import (
    COL_PERSNR,
    COL_GEB,
    COL_EINTRITT,
    COL_AUSTRITT,
    COL_ATZ_BEGINN,
    COL_ATZ_ENDE,
    COL_ATZ_VERTRAG_ENDE,
    COL_SOLL,
    normalize_persnr,
)

import logging
logger = logging.getLogger(__name__)


def _safe_parse_date(series: pd.Series) -> pd.Series:
    """Parse dates robustly: 9999-12-31 and other out-of-bounds values → NaT."""
    def _clean(val):
        if pd.isna(val):
            return pd.NaT
        try:
            year = getattr(val, "year", None)
            if year is not None and (year >= 9999 or year > 2262):
                return pd.NaT
        except Exception:
            pass
        s = str(val).strip()
        if s.startswith("9999"):
            return pd.NaT
        return val

    return pd.to_datetime(series.map(_clean), errors="coerce")


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


def load_inputs(
    base_path: Path,
    uploaded_ma: Any = None,
    uploaded_atz: Any = None,
    uploaded_pl: Any = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load Mitarbeiter.xlsx and ATZ.xlsx (optionally from uploads).
    Also merges Sollarbeitszeit from Planstellen.xlsx if available.

    Args:
        base_path: Project root (e.g., Data_Layout).
        uploaded_*: Optional file-like objects (BytesIO) from Streamlit uploader.

    Returns:
        Tuple of (df_ma, df_atz)
    """
    # 1. Determine Sources
    if uploaded_ma and uploaded_atz:
        # Use Uploads
        ma_source = uploaded_ma
        atz_source = uploaded_atz
        pl_source = uploaded_pl  # Can be None
    else:
        # Resolve Paths
        ma_path, atz_path, pl_path = _resolve_input_paths(base_path)
        ma_source = ma_path
        atz_source = atz_path
        pl_source = pl_path

    # 2. Read Data
    df_ma = pd.read_excel(ma_source)  # No parse_dates – 9999-12-31 causes OutOfBounds
    
    # P05: Centralized PersNr normalization
    if COL_PERSNR in df_ma.columns:
         df_ma[COL_PERSNR] = normalize_persnr(df_ma[COL_PERSNR])

    # Safe date parsing: handle 9999-12-31 → NaT before pd.to_datetime
    for col in [COL_GEB, COL_EINTRITT, COL_AUSTRITT]:
        if col in df_ma.columns:
            df_ma[col] = _safe_parse_date(df_ma[col])

    df_atz = pd.read_excel(atz_source)  # No parse_dates – same 9999 risk
    
    if COL_PERSNR in df_atz.columns:
         df_atz[COL_PERSNR] = normalize_persnr(df_atz[COL_PERSNR])

    # Safe date parsing for ATZ columns
    for col in [COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE]:
        if col in df_atz.columns:
            df_atz[col] = _safe_parse_date(df_atz[col])
    
    # Merge Sollarbeitszeit from Planstellen if available
    # Merge Sollarbeitszeit from Planstellen if available
    if pl_source:
        try:
            df_pl = pd.read_excel(pl_source)
            # Minimal cleaning for Planstellen
            if "Personalnummer" in df_pl.columns and "Sollarbeitszeit" in df_pl.columns:
                 # P05: Centralized PersNr normalization
                 df_pl["Personalnummer"] = normalize_persnr(df_pl["Personalnummer"])
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
        except Exception as e:
            # P12: Log instead of silently ignoring
            logger.warning(f"Planstellen-Merge fehlgeschlagen: {e}")

    return df_ma, df_atz
