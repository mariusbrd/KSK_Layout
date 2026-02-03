"""
KPI-Engine: Readme-konforme KPI-Berechnungen für das Streamlit-Dashboard.

Dieses Modul kapselt die korrekte Berechnungslogik aus kpi_reference.py
und stellt sie in einer Form bereit, die direkt aus Streamlit-Seiten
und dem Loader heraus genutzt werden kann.

WICHTIG: Alle KPIs beziehen sich auf die Mitarbeiter-Ebene (unique PersNr),
NICHT auf Planstellen-Zeilen, es sei denn, es handelt sich um
Planstellen-spezifische Kennzahlen (Vakanzen, Besetzungsquote).

Source of Truth: Readme_Kennzahlen-Definition.md
"""

import pandas as pd
import numpy as np
from typing import Dict, Set, Optional

# =============================================================================
# STICHTAG
# =============================================================================

STICHTAG = pd.Timestamp("2025-01-30")
VOLLZEIT_REFERENZ = 39.0


def get_stichtag() -> pd.Timestamp:
    """Gibt den verbindlichen Stichtag zurück."""
    return STICHTAG


# =============================================================================
# MITARBEITER-BASIERTE KPIs (aus Planstellen-Snapshot)
# =============================================================================

def get_unique_employees(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    """
    Extrahiert unique Mitarbeitende aus dem Planstellen-Snapshot.

    Ein Mitarbeiter kann auf mehreren Planstellen stehen.
    Für personenbezogene KPIs (Headcount, FTE, Teilzeit, Alter)
    brauchen wir eine deduplizierte Sicht.

    Returns:
        DataFrame mit einer Zeile pro Person (erste Planstelle behalten).
    """
    besetzt = snapshot_df[~snapshot_df["Is_Vacant"]].copy()
    if "PersNr" not in besetzt.columns:
        return besetzt

    # Dedupliziere: Behalte erste Planstelle pro Person
    unique = besetzt.drop_duplicates(subset="PersNr", keep="first")
    return unique


def compute_headcount(snapshot_df: pd.DataFrame) -> int:
    """Headcount = Anzahl unique PersNr (besetzte Planstellen)."""
    return len(get_unique_employees(snapshot_df))


def compute_fte_roh(snapshot_df: pd.DataFrame) -> float:
    """FTE roh = Sum(BsGrd/100) aller unique Mitarbeitenden."""
    emp = get_unique_employees(snapshot_df)
    return round((emp["BsGrd"].fillna(0) / 100.0).sum(), 2)


def compute_fte_effektiv(snapshot_df: pd.DataFrame) -> float:
    """
    FTE effektiv = Sum(MAK) aller unique Mitarbeitenden.
    MAK berücksichtigt bereits Ruhend und ATZ-FR = 0.
    """
    emp = get_unique_employees(snapshot_df)
    if "MAK" in emp.columns:
        return round(emp["MAK"].sum(), 2)
    # Fallback: manuell berechnen
    fte = emp["BsGrd"].fillna(0) / 100.0
    if "Status kundenindividuell" in emp.columns:
        fte = np.where(
            emp["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis",
            0.0, fte
        )
    if "ist_atz_fr" in emp.columns:
        fte = np.where(emp["ist_atz_fr"], 0.0, fte)
    return round(float(np.sum(fte)), 2)


def compute_ruhend(snapshot_df: pd.DataFrame) -> Dict:
    """Ruhend-Kennzahlen."""
    emp = get_unique_employees(snapshot_df)
    if "Status kundenindividuell" not in emp.columns:
        return {"koepfe": 0, "fte_verlust": 0.0}

    ruhend = emp["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis"
    fte_verlust = round((emp.loc[ruhend, "BsGrd"].fillna(0) / 100.0).sum(), 2)
    return {
        "koepfe": int(ruhend.sum()),
        "fte_verlust": fte_verlust,
    }


def compute_atz_fr_count(snapshot_df: pd.DataFrame) -> int:
    """Anzahl ATZ-Freizeitphase am Stichtag (unique Köpfe)."""
    emp = get_unique_employees(snapshot_df)
    if "ist_atz_fr" in emp.columns:
        return int(emp["ist_atz_fr"].sum())
    if "ATZ_Status" in emp.columns:
        return int((emp["ATZ_Status"] == "Freistellungsphase").sum())
    return 0


def compute_teilzeit_kpis(snapshot_df: pd.DataFrame) -> Dict:
    """
    Teilzeit gemäß Readme:
    - 0 < BsGrd < 100
    - Exkl. Ruhend und ATZ-FR
    - Quote bezogen auf Headcount gesamt
    """
    emp = get_unique_employees(snapshot_df)
    headcount = len(emp)

    ist_ruhend = pd.Series(False, index=emp.index)
    if "Status kundenindividuell" in emp.columns:
        ist_ruhend = emp["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis"

    ist_atz_fr = pd.Series(False, index=emp.index)
    if "ist_atz_fr" in emp.columns:
        ist_atz_fr = emp["ist_atz_fr"].fillna(False)

    teilzeit_mask = (
        (emp["BsGrd"] > 0) &
        (emp["BsGrd"] < 100) &
        (~ist_ruhend) &
        (~ist_atz_fr)
    )

    tz_count = int(teilzeit_mask.sum())
    tz_quote = round(tz_count / headcount * 100, 2) if headcount > 0 else 0
    avg_bsgrd = round(emp.loc[teilzeit_mask, "BsGrd"].mean(), 1) if tz_count > 0 else 0

    return {
        "count": tz_count,
        "quote_pct": tz_quote,
        "avg_bsgrd": avg_bsgrd,
    }


def compute_alter_kpis(snapshot_df: pd.DataFrame) -> Dict:
    """Alter-KPIs mit korrekter float-Präzision."""
    emp = get_unique_employees(snapshot_df)

    if "Alter_Jahre" in emp.columns:
        alter = emp["Alter_Jahre"]
    elif "Alter" in emp.columns:
        alter = emp["Alter"].astype(float)
    else:
        return {"avg": 0, "median": 0}

    return {
        "avg": round(alter.mean(), 1),
        "median": round(alter.median(), 1),
    }


def compute_verrentung(snapshot_df: pd.DataFrame, stichtag: pd.Timestamp = STICHTAG) -> Dict:
    """
    Verrentungswelle: Regelaltersgrenze 67.
    Zählt Personen deren Rentendatum (GebDatum + 67y) zwischen
    Stichtag und Jahresende des Horizonts liegt.
    """
    emp = get_unique_employees(snapshot_df)
    if "GebDatum" not in emp.columns:
        return {"bis_2030": 0, "bis_2035": 0, "bis_2040": 0}

    renten_datum = emp["GebDatum"].apply(
        lambda x: x + pd.DateOffset(years=67) if pd.notna(x) else pd.NaT
    )

    result = {}
    for key, end_date in [
        ("bis_2030", pd.Timestamp("2030-12-31")),
        ("bis_2035", pd.Timestamp("2035-12-31")),
        ("bis_2040", pd.Timestamp("2040-12-31")),
    ]:
        mask = (renten_datum > stichtag) & (renten_datum <= end_date)
        result[key] = int(mask.sum())

    return result


def compute_generationen(snapshot_df: pd.DataFrame) -> Dict[str, int]:
    """Generationen-Mix nach Geburtsjahr."""
    emp = get_unique_employees(snapshot_df)
    if "GebDatum" not in emp.columns:
        return {}

    geburtsjahr = emp["GebDatum"].dt.year
    grenzen = {
        "Boomer": (1946, 1964),
        "Gen X": (1965, 1980),
        "Gen Y": (1981, 1996),
        "Gen Z": (1997, 2100),
    }
    return {
        gen: int(((geburtsjahr >= s) & (geburtsjahr <= e)).sum())
        for gen, (s, e) in grenzen.items()
    }


# =============================================================================
# PLANSTELLEN-BASIERTE KPIs
# =============================================================================

def compute_planstellen_kpis(snapshot_df: pd.DataFrame) -> Dict:
    """Planstellen-KPIs aus dem Snapshot."""
    total = len(snapshot_df)
    besetzt = snapshot_df["Is_Vacant"] == False if "Is_Vacant" in snapshot_df.columns else pd.Series(True, index=snapshot_df.index)
    vakanz = ~besetzt

    return {
        "total": total,
        "besetzt": int(besetzt.sum()),
        "vakanzen": int(vakanz.sum()),
        "besetzungsquote": round(besetzt.sum() / total * 100, 1) if total > 0 else 0,
    }


def compute_vakanz_fte(snapshot_df: pd.DataFrame) -> Dict:
    """Vakanz-FTE (nur bekannte Sollarbeitszeit)."""
    if "Is_Vacant" not in snapshot_df.columns or "Sollarbeitszeit" not in snapshot_df.columns:
        return {"fte_bekannt": 0, "zeilen_bekannt": 0, "zeilen_unbekannt": 0}

    vakanz_df = snapshot_df[snapshot_df["Is_Vacant"]].copy()
    # "bekannt" = Sollarbeitszeit != 0.01
    bekannt = vakanz_df[vakanz_df["Sollarbeitszeit"] != 0.01]
    unbekannt = vakanz_df[vakanz_df["Sollarbeitszeit"] == 0.01]

    fte = round(bekannt["Sollarbeitszeit"].sum() / VOLLZEIT_REFERENZ, 2)

    return {
        "fte_bekannt": fte,
        "zeilen_bekannt": len(bekannt),
        "zeilen_unbekannt": len(unbekannt),
    }


# =============================================================================
# ATZ KPIs
# =============================================================================

def compute_atz_kpis(snapshot_df: pd.DataFrame) -> Dict:
    """ATZ-Kennzahlen aus dem Snapshot."""
    emp = get_unique_employees(snapshot_df)

    atz_ar = 0
    atz_fr = 0
    atz_gesamt = 0

    if "ATZ_Status" in emp.columns:
        atz_ar = int((emp["ATZ_Status"] == "Arbeitsphase").sum())
        atz_fr = int((emp["ATZ_Status"] == "Freistellungsphase").sum())
        atz_gesamt = atz_ar + atz_fr

    headcount = len(emp)

    return {
        "gesamt": atz_gesamt,
        "arbeitsphase": atz_ar,
        "freistellung": atz_fr,
        "quote_headcount_pct": round(atz_gesamt / headcount * 100, 2) if headcount > 0 else 0,
    }


# =============================================================================
# ZUSAMMENFASSUNG: Readme-konforme Summary
# =============================================================================

def compute_readme_summary(snapshot_df: pd.DataFrame) -> Dict:
    """
    Berechnet eine vollständige Summary gemäß Readme-Definitionen.

    Kann direkt als Ersatz für get_data_summary() verwendet werden,
    ergänzt um die bisherigen Felder für Abwärtskompatibilität.
    """
    headcount = compute_headcount(snapshot_df)
    fte_roh = compute_fte_roh(snapshot_df)
    fte_effektiv = compute_fte_effektiv(snapshot_df)
    ruhend = compute_ruhend(snapshot_df)
    atz_fr = compute_atz_fr_count(snapshot_df)
    teilzeit = compute_teilzeit_kpis(snapshot_df)
    alter = compute_alter_kpis(snapshot_df)
    plan = compute_planstellen_kpis(snapshot_df)
    vakanz = compute_vakanz_fte(snapshot_df)
    atz = compute_atz_kpis(snapshot_df)
    verrentung = compute_verrentung(snapshot_df)

    return {
        # Abwärtskompatibel mit bisherigem Summary-Format
        "total_planstellen": plan["total"],
        "total_employees": headcount,
        "total_fte": fte_roh,
        "total_mak": fte_effektiv,
        "total_soll_fte": snapshot_df["Soll_FTE"].sum() if "Soll_FTE" in snapshot_df.columns else 0,
        "total_cost": snapshot_df["Total_Cost_Year"].sum() if "Total_Cost_Year" in snapshot_df.columns else 0,

        # Vakanzen
        "vacancy_count": plan["vakanzen"],
        "vacancy_rate": plan["vakanzen"] / plan["total"] if plan["total"] > 0 else 0,
        "besetzungsgrad": plan["besetzt"] / plan["total"] if plan["total"] > 0 else 0,

        # Readme-KPIs
        "headcount": headcount,
        "fte_roh": fte_roh,
        "fte_effektiv": fte_effektiv,
        "fte_pro_kopf_pct": round(fte_effektiv / headcount * 100, 1) if headcount > 0 else 0,

        # Ruhend
        "ruhend_count": ruhend["koepfe"],
        "ruhend_fte_verlust": ruhend["fte_verlust"],
        "ruhend_rate": ruhend["koepfe"] / headcount if headcount > 0 else 0,

        # ATZ
        "atz_fr_count": atz_fr,
        "atz_count": atz["gesamt"],
        "atz_ar_count": atz["arbeitsphase"],
        "atz_rate": atz["gesamt"] / headcount if headcount > 0 else 0,

        # Teilzeit
        "teilzeit_count": teilzeit["count"],
        "teilzeit_rate": teilzeit["quote_pct"] / 100,  # als Dezimal für format_percent()
        "teilzeit_quote_pct": teilzeit["quote_pct"],
        "avg_bsgrd_teilzeit": teilzeit["avg_bsgrd"],

        # Demografie
        "avg_age": alter["avg"],
        "median_age": alter["median"],
        "avg_tenure": 0,  # wird unten gesetzt wenn verfügbar

        # Verrentung
        "rente_bis_2030": verrentung.get("bis_2030", 0),
        "rente_bis_2035": verrentung.get("bis_2035", 0),
        "rente_bis_2040": verrentung.get("bis_2040", 0),

        # Vakanz FTE
        "vakanz_fte_bekannt": vakanz["fte_bekannt"],

        # Geschlecht (abwärtskompatibel)
        "female_count": 0,
        "female_rate": 0,
    }


def enrich_summary_with_gender(summary: Dict, snapshot_df: pd.DataFrame) -> Dict:
    """Ergänzt Gender-Kennzahlen in bestehende Summary."""
    emp = get_unique_employees(snapshot_df)
    if "Geschlecht" in emp.columns:
        female = (emp["Geschlecht"] == "w").sum()
        summary["female_count"] = int(female)
        summary["female_rate"] = female / len(emp) if len(emp) > 0 else 0
    if "Betriebszugehörigkeit_Jahre" in emp.columns:
        summary["avg_tenure"] = round(emp["Betriebszugehörigkeit_Jahre"].mean(), 1)
    return summary
