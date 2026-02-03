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
    COL_ATZ_VERTRAG_ENDE,
)


def _resolve_input_paths(base_path: Path) -> Tuple[Path, Path]:
    """
    Resolve Mitarbeiter.xlsx and ATZ.xlsx paths.

    Priority:
    1) Original-Daten (sibling of base_path)
    2) data/sample_data (inside base_path)
    """
    base_path = base_path.resolve()
    original_dir = base_path.parent / "Original-Daten"
    sample_dir = base_path / "data" / "sample_data"

    candidates = [
        (original_dir / "Mitarbeiter.xlsx", original_dir / "ATZ.xlsx"),
        (sample_dir / "Mitarbeiter.xlsx", sample_dir / "ATZ.xlsx"),
    ]

    for ma_path, atz_path in candidates:
        if ma_path.exists() and atz_path.exists():
            return ma_path, atz_path

    raise FileNotFoundError(
        "Mitarbeiter.xlsx und/oder ATZ.xlsx nicht gefunden. "
        "Erwarte Dateien in Original-Daten oder data/sample_data."
    )


def load_inputs(base_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load Mitarbeiter.xlsx and ATZ.xlsx.

    Args:
        base_path: Project root (e.g., KSK_Layout).

    Returns:
        Tuple of (df_ma, df_atz)
    """
    ma_path, atz_path = _resolve_input_paths(base_path)

    df_ma = pd.read_excel(
        ma_path,
        parse_dates=[COL_GEB, COL_EINTRITT, COL_AUSTRITT],
    )

    df_atz = pd.read_excel(
        atz_path,
        parse_dates=[COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE],
    )

    return df_ma, df_atz
