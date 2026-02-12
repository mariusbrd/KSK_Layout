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
COL_OE_CODE = "Kürzel OrgEinheit"

# Sonder-OEs: Personen sind faktisch nicht im aktiven Dienst und dürfen
# nicht in den ATZ-Kandidatenpool gezogen werden.
EXCLUDED_OE_CODES = frozenset({
    "9940",   # PA Dauerkranke
    "9941",   # PA Rente auf Zeit
    "9945",   # PA Beurlaubt Par. 3 Pflegezeit
    "9960",   # PA Bundeswehr/Zivildienst
    "9970",   # PA Mutterschutz
    "9971",   # PA Elternzeit
    "9972",   # PA SU Par. 28 TVöD
    "9973",   # PA Beschäftigungsverbot
    "9975",   # PA Erziehungszeit
    "9990",   # PA Freistellung (ATZ-FR, Beurlaubung, Turbo-TZ)
})

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
    REASON_ATZ_END: "Rente (nach ATZ)", 
    REASON_RETIREMENT: "Rente (direkt)", 
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
    def _safe_convert(x):
        if pd.isna(x):
            return pd.NA
        try:
            s = str(x).strip().lower()
            if s in ["nan", "none", "", "nat"]:
                return pd.NA
            
            # Handle float strings like "3223.0"
            f = float(x)
            i = int(f)
            return str(i).zfill(ID_PAD_LENGTH)
        except (ValueError, TypeError):
            # Fallback for non-numeric strings
            return str(x).strip().zfill(ID_PAD_LENGTH)

    return series.apply(_safe_convert)
