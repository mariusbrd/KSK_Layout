"""
HR Pulse Dashboard - Globale Einstellungen und Konstanten
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple

# =============================================================================
# FARBPALETTE
# =============================================================================

COLORS = {
    # Basis-Theme (Light)
    "background": "#FFFFFF",       # Weiß
    "card_bg": "#F5F5F5",          # Sehr helles Grau
    "card_border": "#BEBEBE",      # X11 Gray

    # Text
    "text_primary": "#757575",     # Sonic Silver
    "text_secondary": "#A9A9A9",   # X11 Dark Gray
    "text_muted": "#BEBEBE",       # X11 Gray

    # Moderne Akzente
    "accent_blue": "#0088DE",      # Blue Cola (Primär)
    "accent_blue_light": "#00B9FC", # Blue Bolt (Hell)
    "accent_red": "#E94D3A",       # Persian Red
    "accent_teal": "#0088DE",      # Blue Cola (für Kompatibilität)
    "accent_amber": "#f59e0b",     # Amber (für Warnungen)
    "accent_green": "#10b981",     # Emerald (für Success)

    # Status
    "status_good": "#10b981",      # Emerald (Grün)
    "status_warning": "#f59e0b",   # Amber (Orange/Gelb)
    "status_critical": "#E94D3A",  # Persian Red

    # Geschlecht
    "gender_female": "#E94D3A",    # Persian Red
    "gender_male": "#0088DE",      # Blue Cola
    "gender_diverse": "#A9A9A9",   # X11 Dark Gray
}

# Chart-Farbsequenz für kategorische Daten
COLOR_SEQUENCE = [
    "#0088DE",  # Blue Cola (Primär)
    "#00B9FC",  # Blue Bolt (Hell)
    "#E94D3A",  # Persian Red
    "#757575",  # Sonic Silver
    "#A9A9A9",  # X11 Dark Gray
    "#10b981",  # Emerald (Erfolg)
    "#f59e0b",  # Amber (Warnung)
    "#3b82f6",  # Blue (Akzent)
    "#8b5cf6",  # Violet (Akzent)
    "#84cc16",  # Lime (Akzent)
]

# Kohorten-Farben (konsistent über alle Charts)
COHORT_COLORS = {
    "Azubis": "#00B9FC",           # Blue Bolt (Hell)
    "Young Professionals": "#0088DE",  # Blue Cola
    "Mid Career": "#757575",       # Sonic Silver
    "Senior": "#A9A9A9",           # X11 Dark Gray
    "Pre-Retirement": "#f59e0b",   # Amber (Warnung)
    "Retirement Ready": "#E94D3A", # Persian Red
}

# =============================================================================
# ALTERSKOHORTEN (Defaults)
# =============================================================================

DEFAULT_COHORTS: Dict[str, Tuple[int, int]] = {
    "Azubis": (16, 19),
    "Young Professionals": (20, 29),
    "Mid Career": (30, 44),
    "Senior": (45, 54),
    "Pre-Retirement": (55, 62),
    "Retirement Ready": (63, 99),
}

# =============================================================================
# TARIFSTRUKTUR
# =============================================================================

TARIFF_GROUPS = ["E6", "E7", "E8", "E9A", "E9B", "E9C", "E10", "E11", "E12", "E13", "E14", "E15"]

TARIFF_STEPS = [1, 2, 3, 4, 5, 6]

# Basis-Jahresgehälter nach Tarifgruppe (TVöD-S, Stufe 4, ca.-Werte)
BASE_SALARY: Dict[str, int] = {
    "E6": 42000,
    "E7": 45000,
    "E8": 48000,
    "E9A": 52000,
    "E9B": 54000,
    "E9C": 56000,
    "E10": 60000,
    "E11": 65000,
    "E12": 72000,
    "E13": 78000,
    "E14": 85000,
    "E15": 95000,
}

# Stufenmultiplikatoren (relativ zu Stufe 4)
STEP_MULTIPLIER: Dict[int, float] = {
    1: 0.85,
    2: 0.92,
    3: 0.97,
    4: 1.00,
    5: 1.05,
    6: 1.12,
}

# Arbeitgeber-Kostenfaktor (inkl. Sozialabgaben etc.)
EMPLOYER_COST_FACTOR = 1.25

# =============================================================================
# BESCHÄFTIGUNG
# =============================================================================

FULLTIME_THRESHOLD = 0.95  # Ab diesem FTE-Wert gilt als Vollzeit

EMPLOYMENT_TYPES = ["Unbefristet", "Zeitvertrag", "Altersteilzeit", "Auszubildende"]

ATZ_PHASES = ["Arbeitsphase", "Freistellungsphase"]

STATUS_TYPES = ["Aktives Beschäftigungsverhältnis", "Ruhendes Beschäftigungsverhältnis"]

# =============================================================================
# QUALIFIKATIONEN
# =============================================================================

EDUCATION_GROUPS = [
    "derzeit Berufsausbildung",
    "ohne Berufsabschluss",
    "nicht kfm Berufsabschluss",
    "kfm Berufsabschluss",
    "Bankberufsabschluss",
    "Sparkassen/Bankfachwirt",
    "SPK/Bankbetriebswirt",
    "Bachelor FH",
    "Bachelor Universität",
    "Master FH",
    "Studium Lehrinstitut",
    "Master Universität",
]

# Hierarchie für Mindestqualifikations-Prüfung
EDUCATION_HIERARCHY = {
    "derzeit Berufsausbildung": 0,
    "ohne Berufsabschluss": 1,
    "nicht kfm Berufsabschluss": 2,
    "kfm Berufsabschluss": 3,
    "Bankberufsabschluss": 4,
    "Sparkassen/Bankfachwirt": 5,
    "SPK/Bankbetriebswirt": 6,
    "Bachelor FH": 6,
    "Bachelor Universität": 7,
    "Master FH": 8,
    "Studium Lehrinstitut": 8,
    "Master Universität": 9,
}

# =============================================================================
# SIMULATION DEFAULTS
# =============================================================================

@dataclass
class SimulationDefaults:
    """Default-Parameter für Simulationen"""
    horizon_months: int = 60  # 5 Jahre
    retirement_age: int = 67
    early_retirement_share: float = 0.10  # 10% Frühverrentung
    
    # Fluktuationsraten p.a. nach Alterskohorte
    attrition_by_cohort: Dict[str, float] = None
    
    # Azubi-Parameter
    azubi_intake_per_year: int = 40
    azubi_duration_months: int = 36
    azubi_takeover_rate: float = 0.70
    azubi_entry_tariff: str = "E6"
    
    # Hiring
    hiring_rate_pa: float = 0.04  # 4% der Vakanzen pro Jahr
    time_to_fill_months: int = 3
    
    def __post_init__(self):
        if self.attrition_by_cohort is None:
            self.attrition_by_cohort = {
                "Azubis": 0.05,
                "Young Professionals": 0.08,
                "Mid Career": 0.04,
                "Senior": 0.02,
                "Pre-Retirement": 0.01,
                "Retirement Ready": 0.01,
            }

SIMULATION_DEFAULTS = SimulationDefaults()

# =============================================================================
# KPI SCHWELLENWERTE
# =============================================================================

THRESHOLDS = {
    "besetzungsgrad": {
        "good": 0.85,      # >= 85% ist gut
        "warning": 0.75,   # >= 75% ist Warnung
        # < 75% ist kritisch
    },
    "atz_quote": {
        "good": 0.03,      # <= 3% ist gut
        "warning": 0.06,   # <= 6% ist Warnung
        # > 6% ist kritisch (hohe ATZ-Last)
    },
    "teilzeit_quote": {
        # Keine Bewertung, nur informativ
    },
    "alter_55plus_anteil": {
        "good": 0.25,      # <= 25% ist gut
        "warning": 0.35,   # <= 35% ist Warnung
        # > 35% ist kritisch (Demografie-Risiko)
    },
}

# =============================================================================
# UI KONFIGURATION
# =============================================================================

# Seitenbreite
PAGE_CONFIG = {
    "page_title": "HR Pulse",
    "page_icon": "📊",
    "layout": "wide",
    "initial_sidebar_state": "expanded",
}

# KPI-Card Größen
KPI_CARD_HEIGHT = 140  # px

# Chart-Höhen
CHART_HEIGHTS = {
    "small": 250,
    "medium": 350,
    "large": 450,
    "xlarge": 550,
}

# Anzahl Top-N in Rankings
TOP_N_DEFAULT = 5

# =============================================================================
# DATEIPFADE
# =============================================================================

DATA_PATH = "data/sample_data/hr_data.xlsx"
COHORTS_CONFIG_PATH = "config/cohorts.json"
SEGMENTS_CONFIG_PATH = "config/segments.json"
JOBFAMILIES_CONFIG_PATH = "config/jobfamilies.json"

# =============================================================================
# FORMATIERUNG
# =============================================================================

def format_number(value: float, decimals: int = 0) -> str:
    """Formatiert Zahlen mit Tausender-Punkt (deutsch)"""
    if decimals == 0:
        return f"{value:,.0f}".replace(",", ".")
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")

def format_currency(value: float, suffix: str = "€") -> str:
    """Formatiert Währungsbeträge"""
    if value >= 1_000_000:
        return f"{value/1_000_000:,.1f} Mio. {suffix}".replace(",", ".")
    elif value >= 1_000:
        return f"{value/1_000:,.0f}k {suffix}".replace(",", ".")
    return f"{value:,.0f} {suffix}".replace(",", ".")

def format_percent(value: float, decimals: int = 1) -> str:
    """Formatiert Prozentwerte"""
    return f"{value * 100:,.{decimals}f}%".replace(",", "X").replace(".", ",").replace("X", ".")

def get_status_color(value: float, metric: str) -> str:
    """Ermittelt Status-Farbe basierend auf Schwellenwerten"""
    if metric not in THRESHOLDS:
        return COLORS["text_primary"]
    
    thresholds = THRESHOLDS[metric]
    
    # Für Metriken wo höher = besser (z.B. Besetzungsgrad)
    if metric in ["besetzungsgrad"]:
        if value >= thresholds["good"]:
            return COLORS["status_good"]
        elif value >= thresholds["warning"]:
            return COLORS["status_warning"]
        return COLORS["status_critical"]
    
    # Für Metriken wo niedriger = besser (z.B. ATZ-Quote)
    if metric in ["atz_quote", "alter_55plus_anteil"]:
        if value <= thresholds["good"]:
            return COLORS["status_good"]
        elif value <= thresholds["warning"]:
            return COLORS["status_warning"]
        return COLORS["status_critical"]
    
    return COLORS["text_primary"]
