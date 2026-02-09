"""
KPI-Referenzmodul: Implementiert exakt die Readme-Kennzahlen-Definitionen.

Dieses Modul ist die EINZIGE Quelle der Wahrheit für KPI-Berechnungen.
Alle Streamlit-Seiten sollen diese Funktionen nutzen.

Source of Truth: Readme_Kennzahlen-Definition.md
Stichtag: Wird aus user_settings.json gelesen (konfigurierbar über Einstellungsseite).
"""

import pandas as pd
import numpy as np
from typing import Dict, Set, Tuple, Optional
from pathlib import Path

# =============================================================================
# KONSTANTEN
# =============================================================================

from utils.settings_loader import get_setting

# Einziger hardcoded Stichtag-Default im gesamten Projekt.
# Alle anderen Module sollen get_current_stichtag() verwenden.
STICHTAG_DEFAULT = "2025-01-30"


def get_current_stichtag() -> pd.Timestamp:
    """Holt den aktuellen Stichtag aus den User-Settings.

    Fallback auf STICHTAG_DEFAULT wenn kein Wert konfiguriert ist.
    Dies ist die einzige Funktion, die alle Module nutzen sollen.
    """
    val = get_setting("stichtag")
    if val:
        return pd.Timestamp(val)
    return pd.Timestamp(STICHTAG_DEFAULT)


# Abwärtskompatibilität: STICHTAG-Modul-Variable und get_stichtag()
STICHTAG = get_current_stichtag()


def get_stichtag() -> pd.Timestamp:
    """Alias für get_current_stichtag() (Abwärtskompatibilität)."""
    return get_current_stichtag()


VOLLZEIT_REFERENZ = 39.0  # Wochenstunden
ID_PAD_LENGTH = 6

# Pfade zu Original-Daten
BASE_DIR = Path(__file__).parent
ORIGINAL_DATA_DIR = BASE_DIR.parent / "Original-Daten"

ORIGINAL_FILES = {
    "mitarbeiter": ORIGINAL_DATA_DIR / "Mitarbeiter.xlsx",
    "planstellen": ORIGINAL_DATA_DIR / "Planstellen.XLSX",
    "atz": ORIGINAL_DATA_DIR / "ATZ.xlsx",
    "ausbildung": ORIGINAL_DATA_DIR / "Ausbildung.xlsx",
    "tvoed": ORIGINAL_DATA_DIR / "TVÖD.xlsx",
}

# Generationen-Grenzen (Geburtsjahr)
GENERATIONEN = {
    "Boomer": (1946, 1964),
    "Gen X": (1965, 1980),
    "Gen Y": (1981, 1996),
    "Gen Z": (1997, 2100),
}


# =============================================================================
# STANDARDISIERUNG
# =============================================================================

def standardize_persnr(series: pd.Series) -> pd.Series:
    """PersNr zu String, 6-stellig mit führenden Nullen."""
    return series.apply(
        lambda x: str(int(x)).zfill(ID_PAD_LENGTH) if pd.notna(x) else pd.NA
    )


def get_stichtag() -> pd.Timestamp:
    """Gibt den fixen Stichtag zurück. KEIN Systemdatum."""
    return STICHTAG


# =============================================================================
# DATEN LADEN (roh, ohne Streamlit-Abhängigkeit)
# =============================================================================

def load_mitarbeiter(filepath: Optional[Path] = None) -> pd.DataFrame:
    """Lädt Mitarbeiter.xlsx mit korrekter Typbehandlung."""
    fp = filepath or ORIGINAL_FILES["mitarbeiter"]
    df = pd.read_excel(fp)
    df["PersNr"] = standardize_persnr(df["PersNr"])
    df["GebDatum"] = pd.to_datetime(df["GebDatum"], errors="coerce")
    df["Eintritt"] = pd.to_datetime(df["Eintritt"], errors="coerce")
    # Austritt: 9999-12-31 als NaT
    if "Austritt" in df.columns:
        def safe_austritt(val):
            if pd.isna(val):
                return pd.NaT
            try:
                if hasattr(val, "year") and val.year >= 9999:
                    return pd.NaT
            except Exception:
                pass
            s = str(val).strip()
            if s.startswith("9999"):
                return pd.NaT
            return val
        df["Austritt"] = df["Austritt"].map(safe_austritt)
        df["Austritt"] = pd.to_datetime(df["Austritt"], errors="coerce")
    return df


def load_atz(filepath: Optional[Path] = None) -> pd.DataFrame:
    """Lädt ATZ.xlsx mit standardisierten PersNr und Datumsfeldern."""
    fp = filepath or ORIGINAL_FILES["atz"]
    df = pd.read_excel(fp, parse_dates=["Beginn", "Ende", "Ende ATZ Vertrag"])
    df["PersNr"] = standardize_persnr(df["PersNr"])
    df["Phase"] = df["Phase"].astype(str).str.strip()
    df["Beginn"] = pd.to_datetime(df["Beginn"], errors="coerce")
    df["Ende"] = pd.to_datetime(df["Ende"], errors="coerce")
    return df


def load_planstellen(filepath: Optional[Path] = None) -> pd.DataFrame:
    """Lädt Planstellen.XLSX roh."""
    fp = filepath or ORIGINAL_FILES["planstellen"]
    df = pd.read_excel(fp)
    return df


def load_tvoed(filepath: Optional[Path] = None) -> Dict[Tuple[str, int], float]:
    """
    Lädt TVÖD.xlsx und gibt Lookup-Dict {(Gruppe, Stufe): Monatsgehalt} zurück.

    WICHTIG: Gibt MONATSGEHALT zurück (nicht Jahresgehalt).
    """
    import re
    fp = filepath or ORIGINAL_FILES["tvoed"]
    if not fp.exists():
        return {}

    df = pd.read_excel(fp, header=1)
    group_col = df.columns[0]
    lookup = {}

    for _, row in df.iterrows():
        raw_group = row[group_col]
        if pd.isna(raw_group):
            continue
        # Normalisierung: Leerzeichen entfernen, Großschreibung
        group = re.sub(r"\s+", "", str(raw_group).strip()).upper()
        if not group.startswith("E"):
            continue

        for step in range(1, 7):
            val = None
            if step in df.columns:
                val = row[step]
            elif str(step) in df.columns:
                val = row[str(step)]

            if val is not None and pd.notna(val):
                try:
                    lookup[(group, step)] = float(val)
                except (ValueError, TypeError):
                    pass

    return lookup


# =============================================================================
# ATZ-FUNKTIONEN
# =============================================================================

def compute_atz_sets(df_atz: pd.DataFrame, stichtag: pd.Timestamp = STICHTAG) -> Dict[str, Set[str]]:
    """
    Berechnet ATZ-Sets am Stichtag.

    Returns:
        Dict mit:
        - "atz_fr": Set PersNr in Freizeitphase am Stichtag
        - "atz_ar": Set PersNr in Arbeitsphase am Stichtag
        - "atz_all": Alle unique PersNr in ATZ.xlsx
    """
    atz_fr = set(df_atz[
        (df_atz["Phase"] == "FR") &
        (df_atz["Beginn"] <= stichtag) &
        (df_atz["Ende"] >= stichtag)
    ]["PersNr"].unique())

    atz_ar = set(df_atz[
        (df_atz["Phase"] == "AR") &
        (df_atz["Beginn"] <= stichtag) &
        (df_atz["Ende"] >= stichtag)
    ]["PersNr"].unique())

    atz_all = set(df_atz["PersNr"].unique())

    return {
        "atz_fr": atz_fr,
        "atz_ar": atz_ar,
        "atz_all": atz_all,
    }


# =============================================================================
# FTE-BERECHNUNG
# =============================================================================

def compute_fte(df_ma: pd.DataFrame, atz_sets: Dict[str, Set[str]]) -> pd.DataFrame:
    """
    Berechnet FTE_roh und FTE_effektiv gemäß Readme.

    FTE_roh = BsGrd / 100
    FTE_effektiv:
      - 0 bei "Ruhendes Beschäftigungsverhältnis"
      - 0 bei ATZ Freizeitphase am Stichtag
      - sonst BsGrd/100
    """
    df = df_ma.copy()
    df["FTE_roh"] = df["BsGrd"] / 100.0

    ist_ruhend = df["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis"
    ist_atz_fr = df["PersNr"].isin(atz_sets["atz_fr"])

    df["FTE_effektiv"] = np.where(
        ist_ruhend, 0.0,
        np.where(ist_atz_fr, 0.0, df["FTE_roh"])
    )

    df["ist_ruhend"] = ist_ruhend
    df["ist_atz_fr"] = ist_atz_fr

    return df


# =============================================================================
# TEILZEIT
# =============================================================================

def compute_teilzeit(df_ma: pd.DataFrame, atz_sets: Dict[str, Set[str]]) -> Dict:
    """
    Teilzeit = 0 < BsGrd < 100, exkl. Ruhend und ATZ-FR.
    Teilzeitquote = Teilzeitkräfte / Headcount gesamt.
    """
    ist_ruhend = df_ma["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis"
    ist_atz_fr = df_ma["PersNr"].isin(atz_sets["atz_fr"])

    teilzeit_mask = (
        (df_ma["BsGrd"] > 0) &
        (df_ma["BsGrd"] < 100) &
        (~ist_ruhend) &
        (~ist_atz_fr)
    )

    teilzeitkraefte = df_ma[teilzeit_mask]
    headcount_gesamt = len(df_ma)

    return {
        "teilzeitkraefte": len(teilzeitkraefte),
        "teilzeitquote": len(teilzeitkraefte) / headcount_gesamt * 100 if headcount_gesamt > 0 else 0,
        "avg_bsgrd_teilzeit": teilzeitkraefte["BsGrd"].mean() if len(teilzeitkraefte) > 0 else 0,
    }


# =============================================================================
# PLANSTELLEN
# =============================================================================

def clean_planstellen(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bereinigt Planstellen gemäß Readme:
    1. Summenzeile entfernen (Kürzel OrgEinheit ist NaN)
    2. Sollarbeitszeit 0.01 in Org 9910 auf 39 korrigieren
    3. 0.01 außerhalb 9910 bleibt UNBEKANNT (nicht korrigieren)
    """
    df = df.copy()

    # 1. Summenzeile entfernen
    df = df[df["Kürzel OrgEinheit"].notna()].copy()

    # 2. Nur Org 9910 UND Sollarbeitszeit == 0.01 korrigieren
    mask_9910_001 = (
        (df["Kürzel OrgEinheit"] == "9910") &
        (df["Sollarbeitszeit"] == 0.01)
    )
    df.loc[mask_9910_001, "Sollarbeitszeit"] = VOLLZEIT_REFERENZ

    # Personalnummer standardisieren
    if "Personalnummer" in df.columns:
        df["Personalnummer"] = standardize_persnr(df["Personalnummer"])

    return df


def compute_planstellen_kpis(df_plan: pd.DataFrame) -> Dict:
    """Berechnet Planstellen-KPIs gemäß Readme."""
    df = clean_planstellen(df_plan)

    total_zeilen = len(df)
    besetzt = df["Personalnummer"].notna()
    besetzte_zeilen = besetzt.sum()
    vakanz_zeilen = (~besetzt).sum()

    # Sollarbeitszeit 0.01 Analyse
    soll_001 = df["Sollarbeitszeit"] == 0.01
    soll_001_9910 = soll_001 & (df["Kürzel OrgEinheit"] == "9910")
    soll_001_nicht_9910 = soll_001 & (df["Kürzel OrgEinheit"] != "9910")

    # Aber: 9910 wurde bereits korrigiert, also 0.01 in 9910 gibt es nicht mehr.
    # Wir müssen die Zählung VOR Korrektur machen.
    # Also laden wir nochmal roh:
    df_roh = df_plan.copy()
    df_roh = df_roh[df_roh["Kürzel OrgEinheit"].notna()]
    soll_001_roh = df_roh["Sollarbeitszeit"] == 0.01
    soll_001_9910_roh = soll_001_roh & (df_roh["Kürzel OrgEinheit"] == "9910")
    soll_001_nicht_9910_roh = soll_001_roh & (df_roh["Kürzel OrgEinheit"] != "9910")

    # Vakanz-FTE (nur bekannte Sollarbeitszeit)
    vakanz_df = df[~besetzt].copy()
    # "bekannt" = Sollarbeitszeit != 0.01 (nach Korrektur; 9910 wurde auf 39 korrigiert)
    # Aber: 0.01 außerhalb 9910 ist unbekannt
    vakanz_bekannt = vakanz_df[vakanz_df["Sollarbeitszeit"] != 0.01]
    vakanz_fte_bekannt = vakanz_bekannt["Sollarbeitszeit"].sum() / VOLLZEIT_REFERENZ

    return {
        "planstellen_zeilen": total_zeilen,
        "besetzte_zeilen": int(besetzte_zeilen),
        "vakanz_zeilen": int(vakanz_zeilen),
        "besetzungsquote": besetzte_zeilen / total_zeilen * 100 if total_zeilen > 0 else 0,
        "soll_001_gesamt": int(soll_001_roh.sum()),
        "soll_001_9910": int(soll_001_9910_roh.sum()),
        "soll_001_nicht_9910": int(soll_001_nicht_9910_roh.sum()),
        "vakanz_fte_bekannt": round(vakanz_fte_bekannt, 2),
        "vakanz_bekannt_zeilen": len(vakanz_bekannt),
        "vakanz_unbekannt_zeilen": len(vakanz_df) - len(vakanz_bekannt),
    }


# =============================================================================
# DEMOGRAFIE
# =============================================================================

def compute_alter(df_ma: pd.DataFrame, stichtag: pd.Timestamp = STICHTAG) -> pd.Series:
    """Alter in Jahren als float (Readme: days / 365.25)."""
    return (stichtag - df_ma["GebDatum"]).dt.days / 365.25


def compute_verrentung(df_ma: pd.DataFrame, stichtag: pd.Timestamp = STICHTAG) -> Dict:
    """
    Verrentungswelle: Regelaltersgrenze 67.
    Rentendatum = GebDatum + 67 Jahre.
    Zähle Rentendatum zwischen Stichtag und Jahresende des Horizonts.
    """
    renten_datum = df_ma["GebDatum"] + pd.DateOffset(years=67)
    # DateOffset funktioniert nicht vektorisiert, also einzeln:
    renten_datum = df_ma["GebDatum"].apply(lambda x: x + pd.DateOffset(years=67) if pd.notna(x) else pd.NaT)

    horizonte = {
        "bis_2030": pd.Timestamp("2030-12-31"),
        "bis_2035": pd.Timestamp("2035-12-31"),
        "bis_2040": pd.Timestamp("2040-12-31"),
    }

    result = {}
    for key, end_date in horizonte.items():
        mask = (renten_datum > stichtag) & (renten_datum <= end_date)
        result[key] = int(mask.sum())

    return result


def compute_generationen(df_ma: pd.DataFrame) -> Dict[str, int]:
    """Generationen-Mix nach Geburtsjahr."""
    geburtsjahr = df_ma["GebDatum"].dt.year
    result = {}
    for gen, (start, end) in GENERATIONEN.items():
        result[gen] = int(((geburtsjahr >= start) & (geburtsjahr <= end)).sum())
    return result


# =============================================================================
# TVÖD KOSTEN
# =============================================================================

def compute_tvoed_kosten(
    df_ma: pd.DataFrame,
    tvoed_lookup: Dict[Tuple[str, int], float],
) -> Dict:
    """
    TVÖD-Kostenberechnung gemäß Readme:
    - Nur Tarifarttext == "TVÖD"
    - Exkl. Auszubildende-VKA und Vorstandsvergütung
    - Grundentgelt × (BsGrd/100)
    - Monats-Gehaltssumme und Jahres-Gehaltssumme (×12)
    """
    import re

    # Filter: Nur TVÖD-Mitarbeitende
    if "Tarifarttext" not in df_ma.columns:
        return {"error": "Spalte Tarifarttext fehlt"}

    tvoed_ma = df_ma[df_ma["Tarifarttext"] == "TVÖD"].copy()

    # Exkludiere Azubis und Vorstand
    if "MitarbGruppenbez." in tvoed_ma.columns:
        tvoed_ma = tvoed_ma[tvoed_ma["MitarbGruppenbez."] != "Auszubildende"]

    def normalize_tariff(val):
        if pd.isna(val):
            return None
        return re.sub(r"\s+", "", str(val).strip()).upper()

    def clean_step(val):
        if pd.isna(val):
            return None
        s = str(val).strip().replace("+", "").replace("-", "")
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    tvoed_ma["TrfGr_norm"] = tvoed_ma["TrfGr"].apply(normalize_tariff)
    tvoed_ma["St_clean"] = tvoed_ma["St"].apply(clean_step)

    # Lookup Monatsgehalt
    def get_monthly(row):
        key = (row["TrfGr_norm"], row["St_clean"])
        if key[0] is None or key[1] is None:
            return np.nan
        return tvoed_lookup.get(key, np.nan)

    tvoed_ma["Monatsgehalt"] = tvoed_ma.apply(get_monthly, axis=1)

    # FTE-gewichtet
    tvoed_ma["Gehalt_FTE"] = tvoed_ma["Monatsgehalt"] * (tvoed_ma["BsGrd"] / 100.0)

    hat_gehalt = tvoed_ma["Monatsgehalt"].notna()

    monats_summe = tvoed_ma.loc[hat_gehalt, "Gehalt_FTE"].sum()
    jahres_summe = monats_summe * 12

    return {
        "ma_mit_gehalt": int(hat_gehalt.sum()),
        "ma_ohne_gehalt": int((~hat_gehalt).sum()),
        "avg_monatsgehalt_vollzeit": tvoed_ma.loc[hat_gehalt, "Monatsgehalt"].mean(),
        "median_monatsgehalt": tvoed_ma.loc[hat_gehalt, "Monatsgehalt"].median(),
        "monats_gehaltssumme_fte": round(monats_summe, 2),
        "jahres_gehaltssumme_fte": round(jahres_summe, 2),
    }


# =============================================================================
# HAUPTFUNKTION: Alle KPIs berechnen
# =============================================================================

def compute_all_kpis(stichtag: pd.Timestamp = STICHTAG) -> Dict:
    """
    Berechnet alle KPIs aus den Original-Daten und gibt ein Dict zurück.
    Kein Streamlit erforderlich.
    """
    # Daten laden
    df_ma = load_mitarbeiter()
    df_atz = load_atz()
    df_plan = load_planstellen()
    tvoed_lookup = load_tvoed()

    # ATZ-Sets
    atz_sets = compute_atz_sets(df_atz, stichtag)

    # FTE
    df_ma = compute_fte(df_ma, atz_sets)

    headcount = len(df_ma)
    fte_roh = round(df_ma["FTE_roh"].sum(), 2)
    fte_effektiv = round(df_ma["FTE_effektiv"].sum(), 2)
    fte_pro_kopf = round(fte_effektiv / headcount * 100, 1) if headcount > 0 else 0

    ruhend_koepfe = int(df_ma["ist_ruhend"].sum())
    ruhend_fte_verlust = round(df_ma.loc[df_ma["ist_ruhend"], "FTE_roh"].sum(), 2)

    atz_fr_koepfe = int(df_ma["ist_atz_fr"].sum())

    # Teilzeit
    teilzeit = compute_teilzeit(df_ma, atz_sets)

    # ATZ Struktur
    bsgrd_0_gesamt = int((df_ma["BsGrd"] == 0).sum())
    bsgrd_0_atz_fr = int(((df_ma["BsGrd"] == 0) & df_ma["ist_atz_fr"]).sum())
    bsgrd_0_atz_ar = int(((df_ma["BsGrd"] == 0) & df_ma["PersNr"].isin(atz_sets["atz_ar"])).sum())

    # Planstellen
    plan_kpis = compute_planstellen_kpis(df_plan)

    # Demografie
    alter = compute_alter(df_ma, stichtag)
    avg_alter = round(alter.mean(), 1)
    median_alter = round(alter.median(), 1)

    verrentung = compute_verrentung(df_ma, stichtag)
    generationen = compute_generationen(df_ma)

    # TVÖD
    tvoed_kpis = compute_tvoed_kosten(df_ma, tvoed_lookup)

    return {
        # Mitarbeiter / FTE
        "headcount": headcount,
        "fte_roh": fte_roh,
        "fte_effektiv": fte_effektiv,
        "fte_pro_kopf_pct": fte_pro_kopf,
        "ruhend_koepfe": ruhend_koepfe,
        "ruhend_fte_verlust": ruhend_fte_verlust,
        "atz_fr_koepfe": atz_fr_koepfe,

        # Teilzeit
        "teilzeitkraefte": teilzeit["teilzeitkraefte"],
        "teilzeitquote_pct": round(teilzeit["teilzeitquote"], 2),
        "avg_bsgrd_teilzeit": round(teilzeit["avg_bsgrd_teilzeit"], 1),

        # ATZ Struktur
        "atz_personen_unique": len(atz_sets["atz_all"]),
        "atz_ar_stichtag": len(atz_sets["atz_ar"]),
        "atz_fr_stichtag": len(atz_sets["atz_fr"]),
        "bsgrd_0_gesamt": bsgrd_0_gesamt,
        "bsgrd_0_atz_fr": bsgrd_0_atz_fr,
        "bsgrd_0_atz_ar": bsgrd_0_atz_ar,

        # Planstellen
        **{f"plan_{k}": v for k, v in plan_kpis.items()},

        # Demografie
        "avg_alter": avg_alter,
        "median_alter": median_alter,
        "rente_bis_2030": verrentung["bis_2030"],
        "rente_bis_2035": verrentung["bis_2035"],
        "rente_bis_2040": verrentung["bis_2040"],
        "generationen": generationen,

        # TVÖD
        **{f"tvoed_{k}": v for k, v in tvoed_kpis.items()},
    }


# =============================================================================
# VALIDIERUNG gegen Zielwerte
# =============================================================================

def validate_all(kpis: Dict) -> list:
    """
    Prüft alle KPIs gegen die validierten Zielwerte aus der Readme.
    Gibt Liste von (KPI, erwartet, tatsächlich, ok) Tupeln zurück.
    """
    checks = [
        # Mitarbeiter / FTE
        ("Headcount gesamt", 1222, kpis["headcount"]),
        ("FTE roh gesamt", 1039.77, kpis["fte_roh"]),
        ("FTE effektiv gesamt", 993.23, kpis["fte_effektiv"]),
        ("FTE effektiv pro Kopf %", 81.3, kpis["fte_pro_kopf_pct"]),
        ("Ruhend Köpfe", 54, kpis["ruhend_koepfe"]),
        ("FTE Verlust Ruhend", 46.54, kpis["ruhend_fte_verlust"]),
        ("ATZ FR am Stichtag", 24, kpis["atz_fr_koepfe"]),

        # Teilzeit
        ("Teilzeitkräfte", 349, kpis["teilzeitkraefte"]),
        ("Teilzeitquote %", 28.56, kpis["teilzeitquote_pct"]),
        ("Ø BsGrd Teilzeit", 59.1, kpis["avg_bsgrd_teilzeit"]),

        # ATZ Struktur
        ("ATZ Personen unique", 50, kpis["atz_personen_unique"]),
        ("ATZ AR Stichtag", 18, kpis["atz_ar_stichtag"]),
        ("ATZ FR Stichtag", 24, kpis["atz_fr_stichtag"]),
        ("BsGrd 0 gesamt", 32, kpis["bsgrd_0_gesamt"]),
        ("BsGrd 0 ATZ FR", 24, kpis["bsgrd_0_atz_fr"]),
        ("BsGrd 0 ATZ AR", 8, kpis["bsgrd_0_atz_ar"]),

        # Planstellen
        ("Planstellenzeilen", 1728, kpis["plan_planstellen_zeilen"]),
        ("Vakanzen", 481, kpis["plan_vakanz_zeilen"]),
        ("Soll 0.01 gesamt", 737, kpis["plan_soll_001_gesamt"]),
        ("Soll 0.01 Org 9910", 208, kpis["plan_soll_001_9910"]),
        ("Soll 0.01 nicht 9910", 529, kpis["plan_soll_001_nicht_9910"]),
        ("Vakanz FTE bekannt", 151.38, kpis["plan_vakanz_fte_bekannt"]),

        # Demografie
        ("Durchschnittsalter", 40.4, kpis["avg_alter"]),
        ("Median Alter", 41.8, kpis["median_alter"]),
        ("Rente bis 2030", 65, kpis["rente_bis_2030"]),
        ("Rente bis 2035", 229, kpis["rente_bis_2035"]),
        ("Rente bis 2040", 379, kpis["rente_bis_2040"]),

        # TVÖD
        ("TVÖD MA mit Gehalt", 1088, kpis.get("tvoed_ma_mit_gehalt", 0)),
        ("Monats-Gehaltssumme FTE", 4558390.88, kpis.get("tvoed_monats_gehaltssumme_fte", 0)),
        ("Jahres-Gehaltssumme FTE", 54700690.52, kpis.get("tvoed_jahres_gehaltssumme_fte", 0)),
    ]

    results = []
    for name, expected, actual in checks:
        # Toleranz für Fließkomma
        if isinstance(expected, float):
            ok = abs(expected - actual) < 0.1
        else:
            ok = expected == actual
        results.append((name, expected, actual, ok))

    return results


# =============================================================================
# STANDALONE AUSFÜHRUNG
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("KPI-Referenzberechnung (Readme-konform)")
    print(f"Stichtag: {STICHTAG.strftime('%d.%m.%Y')}")
    print("=" * 70)

    kpis = compute_all_kpis()

    print("\n--- Validierung gegen Zielwerte ---\n")
    results = validate_all(kpis)

    pass_count = 0
    fail_count = 0

    for name, expected, actual, ok in results:
        status = "PASS" if ok else "FAIL"
        if ok:
            pass_count += 1
        else:
            fail_count += 1
        print(f"  [{status}] {name:.<40} erwartet={expected}, ist={actual}")

    print(f"\n--- Ergebnis: {pass_count} PASS, {fail_count} FAIL ---")

    if "generationen" in kpis:
        print("\n--- Generationen-Mix ---")
        for gen, count in kpis["generationen"].items():
            print(f"  {gen}: {count}")

    print()
