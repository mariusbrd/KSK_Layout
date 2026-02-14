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



REASON_LABEL_TO_CODE = {v: k for k, v in REASON_LABELS.items()}


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


def build_event_uid(df: pd.DataFrame) -> pd.Series:
    """
    Creates a canonical UID for events (Contract V6):
    {period_label}|{persnr_norm}|{reason_code}
    
    Logic:
    1. period_label: e.g. 'Apr 2027'
    2. persnr_norm: 6-digit zero-padded string (e.g. '000919')
    3. reason_code: normalized canonical code (QUIT, RETIREMENT, etc.)
    """
    if df.empty:
        return pd.Series(dtype=str)
    
    # 1. Period Label
    if 'period_label' in df.columns:
        p_label = df['period_label'].fillna("N/A").astype(str)
    else:
        # Fallback to period_start format
        ts = pd.to_datetime(df.get('period_start', pd.Timestamp.now()))
        p_label = ts.dt.strftime("%b %Y") # e.g. Apr 2027

    # 2. PersNr (6-digit zero-padded)
    pnr_col = "persnr" if "persnr" in df.columns else "PersNr"
    # User Patch: Canonical padding
    def _norm_pnr(val):
        try:
            if pd.isna(val): return "000000"
            # Remove .0 from float-like strings
            s = str(val).split('.')[0].strip()
            if s.isdigit():
                return s.zfill(6)
            return s
        except:
            return str(val)
            
    norm_pnr = df[pnr_col].apply(_norm_pnr)
    
    # 3. Reason Code Mapping (Canonical Codes)
    lbl_to_code = {
        "Kündigung": REASON_QUIT,
        "Rente (direkt)": REASON_RETIREMENT,
        "Rente (nach ATZ)": REASON_ATZ_END,
        "ATZ: AR → FR": REASON_ATZ_AR_TO_FR,
        "Ruhend (Start)": REASON_RUHEND_START,
        "Ruhend (Rückkehr)": REASON_RUHEND_RETURN
    }
    
    def _get_clean_code(row):
        # 1. Check reason_code directly
        c = row.get("reason_code")
        if pd.notna(c) and str(c).strip() not in ("", "nan"):
            c_str = str(c).strip()
            if c_str in REASON_LABELS: return c_str
            if c_str in lbl_to_code: return lbl_to_code[c_str]
        
        # 2. Map from label
        l = row.get("reason_label")
        if pd.notna(l) and str(l).strip() not in ("", "nan"):
            l_clean = str(l).strip()
            if l_clean in lbl_to_code: return lbl_to_code[l_clean]
            return REASON_LABEL_TO_CODE.get(l_clean, "OTHER")
        
        return "UNKNOWN"
    
    norm_reasons = df.apply(_get_clean_code, axis=1)
    
    return p_label + "|" + norm_pnr + "|" + norm_reasons
