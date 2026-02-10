"""
TVÖD-Entgelttabellen-Loader.

Parst die TVÖD.xlsx und stellt eine Lookup-Funktion bereit,
die für eine gegebene (Tarifgruppe, Stufe) das Jahresgehalt liefert.

Fallback auf hardcodierte BASE_SALARY × STEP_MULTIPLIER wenn
die Datei fehlt oder ein bestimmter Wert nicht vorhanden ist.
"""

import os
import re
from typing import Dict, Tuple

import pandas as pd
import streamlit as st

from config.settings import BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR

def normalize_group_name(raw: str) -> str:
    """
    Normalisiert TVöD-Gruppennamen aus der Excel-Datei.

    Beispiele:
        "E9a"   → "E9A"
        "E9b"   → "E9B"
        "E 15U" → "E15U"
        "E2U"   → "E2U"
    """
    s = str(raw).strip()
    s = re.sub(r"\s+", "", s)  # Alle Leerzeichen entfernen
    s = s.upper()
    return s


def load_tvoed_table(file_or_path) -> Dict[Tuple[str, int], float]:
    """
    Parst TVÖD.xlsx in ein Lookup-Dict {(Gruppe, Stufe): Jahresgehalt}.

    Dateistruktur:
        Zeile 1: Titel ("Entgelttabelle TVÖD Bund 2026")
        Zeile 2: Spaltenköpfe: €, 1, 2, 3, 4, 5, 6
        Zeile 3+: Datenzeilen mit Monatsgehältern

    Args:
        file_or_path: Pfad zur TVÖD.xlsx (str/Path) oder file-like Object (BytesIO).

    Returns:
        Dict mit (Gruppe, Stufe) → Jahresgehalt (Monat × 12).
        Leeres Dict bei Fehler oder fehlender Datei.
    """
    # Check only if string/path
    if isinstance(file_or_path, (str, os.PathLike)):
        if not os.path.exists(file_or_path):
            return {}

    try:
        # Zeile 2 (index 1) enthält die Spaltenköpfe
        df = pd.read_excel(file_or_path, header=1)

        # Erste Spalte = Gruppenbezeichnung (Header ist "€" o.ä.)
        group_col = df.columns[0]

        lookup: Dict[Tuple[str, int], float] = {}

        for _, row in df.iterrows():
            raw_group = row[group_col]
            if pd.isna(raw_group):
                continue

            group = normalize_group_name(str(raw_group))

            # Nur Zeilen mit "E..." sind Tarifgruppen
            if not group.startswith("E"):
                continue

            for step in range(1, 7):
                # Spalten können als int oder str benannt sein
                val = None
                if step in df.columns:
                    val = row[step]
                elif str(step) in df.columns:
                    val = row[str(step)]

                if val is not None and pd.notna(val):
                    try:
                        monthly = float(val)
                        lookup[(group, step)] = round(monthly * 12, 2)
                    except (ValueError, TypeError):
                        pass

        return lookup
    except Exception:
        return {}


def get_annual_salary(
    tvoed_lookup: Dict[Tuple[str, int], float],
    tariff: str,
    step: int,
    fallback_base_salary: Dict[str, int],
    fallback_step_multiplier: Dict[int, float],
) -> float:
    """
    Gibt das Jahresgehalt für eine Tarifgruppe+Stufe zurück.

    Priorisierung:
        1. Exakter Lookup in tvoed_lookup
        2. Fallback: BASE_SALARY × STEP_MULTIPLIER

    Args:
        tvoed_lookup: Lookup-Dict aus load_tvoed_table()
        tariff: Tarifgruppe (z.B. "E9A")
        step: Stufe (1–6)
        fallback_base_salary: Hardcodierte Basisgehälter
        fallback_step_multiplier: Hardcodierte Stufenmultiplikatoren

    Returns:
        Jahres-Bruttogehalt (ohne Arbeitgeberfaktor)
    """
    key = (tariff, step)
    if tvoed_lookup and key in tvoed_lookup:
        return tvoed_lookup[key]

    # Fallback auf hardcodierte Werte
    base = fallback_base_salary.get(tariff, 50000)
    multiplier = fallback_step_multiplier.get(step, 1.0)
    return base * multiplier


def get_special_salary(tariff: str) -> float:
    """
    Gibt das konfigurierte Jahresgehalt für Sonderfälle zurück.

    Liest Werte aus st.session_state (gesetzt via Einstellungen-Seite).

    Args:
        tariff: Normalisierter Tarifgruppen-String

    Returns:
        Jahresgehalt oder None wenn kein Sonderfall
    """
    if tariff in ("TVAÖD", "TVÖAD", "TVAOD"):
        return st.session_state.get("azubi_jahresgehalt", 14400.0)
    if tariff == "1":
        return st.session_state.get("vorstand_jahresgehalt", 200000.0)
    return None
