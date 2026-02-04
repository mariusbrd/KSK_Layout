"""
Schemas and constants for Abgaenge.
"""

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
