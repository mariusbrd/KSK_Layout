"""
Schemas and constants for Abgaenge.
"""

import pandas as pd

# Column names
COL_PERSNR = "PersNr"
COL_GEB = "GebDatum"
COL_EINTRITT = "Eintritt"
COL_AUSTRITT = "Austritt"
COL_BSGRD = "BsGrd"
COL_STATUS = "Status kundenindividuell"
COL_SOLL = "Sollarbeitszeit"

COL_ATZ_PHASE = "Phase"
COL_ATZ_BEGINN = "Beginn"
COL_ATZ_ENDE = "Ende"
COL_ATZ_VERTRAG_ENDE = "Ende ATZ Vertrag"

# Reason codes
REASON_ATZ_AR_TO_FR = "ATZ_AR_TO_FR"
REASON_ATZ_END = "ATZ_END"
REASON_RETIREMENT = "RETIREMENT"
REASON_QUIT = "QUIT"
REASON_RUHEND_START = "RUHEND_START"
REASON_RUHEND_RETURN = "RUHEND_RETURN"

REASON_LABELS = {
    REASON_ATZ_AR_TO_FR: "ATZ: AR → FR",
    REASON_ATZ_END: "ATZ: Ende",
    REASON_RETIREMENT: "Rente",
    REASON_QUIT: "Kündigung",
    REASON_RUHEND_START: "Ruhend (Start)",
    REASON_RUHEND_RETURN: "Ruhend (Rückkehr)",
}

# Event mak_change convention:
#   mak_change < 0  → Kapazitätsverlust (Abgang, AR→FR, Ruhend-Start)
#   mak_change > 0  → Kapazitätsgewinn (Ruhend-Rückkehr)
#   mak_change == 0  → Kein Kapazitätseffekt

ID_PAD_LENGTH = 6


def normalize_persnr(series: pd.Series) -> pd.Series:
    """P05: Central PersNr normalization – zero-padded 6-digit string."""
    return series.apply(
        lambda x: str(int(x)).zfill(ID_PAD_LENGTH) if pd.notna(x) else pd.NA
    )
