"""
HR Pulse Dashboard - Globale Einstellungen und Konstanten
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple
from utils.settings_loader import load_user_settings

import os
USER_SETTINGS = load_user_settings()

# Basis-Verzeichnis des Projekts
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    "15-19": "#00B9FC",       # Blue Bolt (Hell) - Jüngste
    "20-24": "#0088DE",       # Blue Cola
    "25-29": "#3b82f6",       # Blue (Akzent)
    "30-34": "#10b981",       # Emerald
    "35-39": "#84cc16",       # Lime
    "40-44": "#757575",       # Sonic Silver
    "45-49": "#A9A9A9",       # X11 Dark Gray
    "50-54": "#f59e0b",       # Amber
    "55-59": "#E94D3A",       # Persian Red
    "60-64": "#8b5cf6",       # Violet
    "65-69": "#ec4899",       # Pink
}

# =============================================================================
# ALTERSKOHORTEN (Defaults) - 5-Jahres-Bins gemäß Ergebnisformat
# =============================================================================

# WICHTIG: OrderedDict-ähnliches Verhalten - Reihenfolge wird beibehalten (Python 3.7+)
DEFAULT_COHORTS: Dict[str, Tuple[int, int]] = {
    "15-19": (15, 19),
    "20-24": (20, 24),
    "25-29": (25, 29),
    "30-34": (30, 34),
    "35-39": (35, 39),
    "40-44": (40, 44),
    "45-49": (45, 49),
    "50-54": (50, 54),
    "55-59": (55, 59),
    "60-64": (60, 64),
    "65-69": (65, 69),
}

# =============================================================================
# TARIFSTRUKTUR
# =============================================================================

TARIFF_GROUPS = [
    "E1", "E2", "E2U", "E3", "E4", "E5",
    "E6", "E7", "E8", "E9A", "E9B", "E9C",
    "E10", "E11", "E12", "E13", "E14", "E15", "E15U",
]

TARIFF_STEPS = [1, 2, 3, 4, 5, 6]

# Fallback-Jahresgehälter nach Tarifgruppe (TVöD-Bund 2026, Stufe 4, Monat×12).
# Quelle: Original-Daten/TVÖD.xlsx – Entgelttabelle TVÖD Bund 2026.
# HINWEIS: Diese Werte werden nur verwendet, wenn TVÖD.xlsx nicht verfügbar ist.
# Bei vorhandener TVÖD.xlsx werden die echten Tabellenwerte genutzt.
BASE_SALARY: Dict[str, int] = {
    "E1":   31820,  # 2651.64 × 12
    "E2":   37213,  # 3101.04 × 12
    "E2U":  38809,  # 3234.12 × 12
    "E3":   39996,  # 3332.99 × 12
    "E4":   41492,  # 3457.66 × 12
    "E5":   43053,  # 3587.78 × 12
    "E6":   44631,  # 3719.22 × 12
    "E7":   45945,  # 3828.76 × 12
    "E8":   47909,  # 3992.40 × 12
    "E9A":  50356,  # 4196.35 × 12
    "E9B":  55298,  # 4608.13 × 12
    "E9C":  59503,  # 4958.59 × 12
    "E10":  62176,  # 5181.37 × 12
    "E11":  65449,  # 5454.10 × 12
    "E12":  71086,  # 5923.82 × 12
    "E13":  74128,  # 6177.31 × 12
    "E14":  79129,  # 6594.12 × 12
    "E15":  86573,  # 7214.39 × 12
    "E15U": 108002, # 9000.15 × 12
}

# Fallback-Stufenmultiplikatoren (relativ zu Stufe 4).
# Durchschnittswerte über alle TVöD-Bund-2026-Tarifgruppen.
# Werden nur verwendet, wenn TVÖD.xlsx nicht verfügbar ist.
STEP_MULTIPLIER: Dict[int, float] = {
    1: 0.85,
    2: 0.91,
    3: 0.96,
    4: 1.00,
    5: 1.07,
    6: 1.11,
}

# Arbeitgeber-Kostenfaktor (inkl. Sozialabgaben etc.)
EMPLOYER_COST_FACTOR = 1.25

# Sonderfall-Defaults (konfigurierbar über Einstellungen-Seite)
DEFAULT_AZUBI_JAHRESGEHALT = 14400.0
# Progressiv (Jahr 1, 2, 3, 4)
DEFAULT_AZUBI_SALARIES = {
    1: 8400.0,
    2: 9600.0,
    3: 11724.0,
    4: 12168.0
}
DEFAULT_VORSTAND_JAHRESGEHALT = 200000.0

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
    "Bankfachwirt",
    "Bankbetriebswirt",
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
    "Bankfachwirt": 5,
    "Bankbetriebswirt": 6,
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

# Laden aus User-Settings oder Defaults
sim_settings = USER_SETTINGS.get("simulation", {})
SIMULATION_DEFAULTS = SimulationDefaults(
    horizon_months=sim_settings.get("horizon_months", 60),
    retirement_age=sim_settings.get("retirement_age", 67),
    early_retirement_share=sim_settings.get("early_retirement_share", 0.10),
    azubi_intake_per_year=sim_settings.get("azubi_intake_per_year", 40),
    azubi_duration_months=sim_settings.get("azubi_duration_months", 36),
    azubi_takeover_rate=sim_settings.get("azubi_takeover_rate", 0.70),
    hiring_rate_pa=sim_settings.get("hiring_rate_pa", 0.04),
    time_to_fill_months=sim_settings.get("time_to_fill_months", 3),
)

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
# AUSSCHLUSS-GRUPPEN (Ausschluss von Ist-Zählung, Erhalt von Soll-Kapa)
# =============================================================================

EXCLUSION_ORG_UNITS = {
    "9920": "PE bereichsübergreifend / Trainee-Planstellen",
    "9999": "Personalrat",
    "9975": "PA Erziehungszeit",
    "9980": "PA Rückkehrer",
    "9990": "PA Freistellung (ATZ-FR, Beurlaubung, Turbo-TZ)",
    "9940": "PA Dauerkranke",
    "9900": "PA Ruhendes Beschäftigungsverhältnis",
    "9941": "PA Rente auf Zeit",
    "9945": "PA Beurlaubt Pflegezeit",
    "9960": "PA Bundeswehr/Zivildienst",
    "9970": "PA Mutterschutz",
    "9971": "PA Elternzeit",
    "9972": "PA Sonderurlaub § 28 TVöD",
    "9973": "PA Beschäftigungsverbot",
    "9981": "Aushilfen",
    "99XX": "Sonstige, z. B. Dummy/Versorgungsbezüge",
    "9910": "PA Auszubildende",
    "9921": "PA Praktikum",
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
