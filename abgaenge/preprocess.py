"""
Preprocessing for Abgaenge forecast inputs.
"""

from typing import Tuple
import pandas as pd

from .schemas import (
    COL_PERSNR,
    COL_GEB,
    COL_EINTRITT,
    COL_AUSTRITT,
    COL_ATZ_BEGINN,
    COL_ATZ_ENDE,
    COL_ATZ_VERTRAG_ENDE,
    COL_ATZ_PHASE,
)

ID_PAD_LENGTH = 6


def _normalize_persnr(series: pd.Series) -> pd.Series:
    return series.apply(
        lambda x: str(int(x)).zfill(ID_PAD_LENGTH) if pd.notna(x) else pd.NA
    )


def preprocess_inputs(
    df_ma: pd.DataFrame, df_atz: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Normalize IDs, parse dates, de-duplicate ATZ rows.
    Interpret 9999-12-31 as open (NaT).
    """
    df_ma = df_ma.copy()
    df_atz = df_atz.copy()

    if COL_PERSNR in df_ma.columns:
        df_ma[COL_PERSNR] = _normalize_persnr(df_ma[COL_PERSNR])

    if COL_PERSNR in df_atz.columns:
        df_atz[COL_PERSNR] = _normalize_persnr(df_atz[COL_PERSNR])

    for col in [COL_GEB, COL_EINTRITT, COL_AUSTRITT]:
        if col in df_ma.columns:
            df_ma[col] = pd.to_datetime(df_ma[col], errors="coerce")

    if COL_AUSTRITT in df_ma.columns:
        austritt_year = pd.DatetimeIndex(df_ma[COL_AUSTRITT]).year
        df_ma.loc[austritt_year == 9999, COL_AUSTRITT] = pd.NaT

    for col in [COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE]:
        if col in df_atz.columns:
            df_atz[col] = pd.to_datetime(df_atz[col], errors="coerce")

    # De-duplicate ATZ rows by key columns
    atz_dedup_cols = [c for c in [COL_PERSNR, COL_ATZ_PHASE, COL_ATZ_BEGINN, COL_ATZ_ENDE] if c in df_atz.columns]
    if atz_dedup_cols:
        df_atz = df_atz.drop_duplicates(subset=atz_dedup_cols)

    return df_ma, df_atz
