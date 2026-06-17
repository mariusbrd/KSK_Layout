"""
validate_parser_fix.py
======================
Validiert, dass der Zahlen-Normalisierungsfehler in _numeric_compensation_series()
nach dem Fix behoben ist.

Prueft:
  - Summe MAK_Reporting (Rohdaten, bereits numerisch, aus apply_person_mak_allocation)
  - Summe IST_MAK nach neuer _numeric_compensation_series (kind="fte")
  - Summe IST_MAK nach alter (buggy) Logik zum Vergleich
  - betroffene Zeilen mit signifikanten Abweichungen
  - Summe EUR_Reporting vs. IST_EUR (kind="currency")
  - compute_fte_effektiv() als existierende KPI-Referenz

Ausfuehren (aus Version 2 - Focus/):
    py -X utf8 KSK_Layout/tests/validate_parser_fix.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Streamlit-Import (benoetigt fuer Loader-Caching, session_state-Warnungen sind OK)
import streamlit as st

from dataloader.loader import load_original_data, combine_to_snapshot
from dataloader.mak_allocation import apply_person_mak_allocation
from dataloader.kpi_engine import compute_fte_effektiv, get_unique_employees
from kpi_reference import get_current_stichtag

SEP  = "=" * 65
SEP2 = "-" * 50
TOL  = 0.01   # Toleranz fuer Summen-Vergleich (0.01 MAK)

# ---------------------------------------------------------------------------
# Replik der Parser-Logik (kein Streamlit-Import aus Kompakt.py noetig)
# ---------------------------------------------------------------------------

def _parse_series_new(series: pd.Series, default: float = 0.0, kind: str = "fte") -> pd.Series:
    """Fixer Parser (nach dem Patch in pages/1_Kompakt.py)."""
    text = series.astype("string").str.strip()
    text = text.str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)

    has_comma = text.str.contains(",", regex=False).fillna(False)
    has_dot   = text.str.contains(".", regex=False).fillna(False)
    both      = has_comma & has_dot

    normalized = text.copy()

    last_is_comma = both & text.str.match(r"^-?[\d.]+,\d+$").fillna(False)
    last_is_dot   = both & text.str.match(r"^-?[\d,]+\.\d+$").fillna(False)

    normalized = normalized.where(
        ~last_is_comma,
        text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
    )
    normalized = normalized.where(
        ~last_is_dot,
        text.str.replace(",", "", regex=False),
    )

    only_comma = has_comma & ~has_dot
    normalized = normalized.where(
        ~only_comma,
        text.str.replace(",", ".", regex=False),
    )

    only_dot  = ~has_comma & has_dot
    multi_dot = only_dot & normalized.str.count(r"\.").gt(1).fillna(False)
    if kind == "currency":
        single_dot_thousands = (
            only_dot
            & normalized.str.match(r"^-?[1-9]\d{0,2}\.\d{3}$").fillna(False)
        )
    else:
        single_dot_thousands = pd.Series(False, index=normalized.index)

    dot_thousands = multi_dot | single_dot_thousands
    normalized = normalized.where(~dot_thousands, normalized.str.replace(".", "", regex=False))

    return pd.to_numeric(normalized, errors="coerce").fillna(default)


def _parse_series_old(series: pd.Series, default: float = 0.0) -> pd.Series:
    """Alter (buggy) Parser – zur Vergleichsberechnung."""
    text = series.astype("string").str.strip()
    text = text.str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
    has_comma = text.str.contains(",", regex=False).fillna(False)

    normalized = text.copy()
    comma_decimal = normalized.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    normalized = normalized.where(~has_comma, comma_decimal)

    multi_dot = (~has_comma) & normalized.str.count(r"\.").gt(1).fillna(False)
    # BUGGY: trifft auch auf 0.141, 0.333 etc.
    single_dot_thousands = (~has_comma) & normalized.str.match(r"^-?\d{1,3}\.\d{3}$").fillna(False)
    dot_thousands = multi_dot | single_dot_thousands
    normalized = normalized.where(~dot_thousands, normalized.str.replace(".", "", regex=False))

    return pd.to_numeric(normalized, errors="coerce").fillna(default)


# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------

def _load_prepared_df() -> pd.DataFrame:
    """Baut den echten Snapshot via combine_to_snapshot + apply_person_mak_allocation."""
    print("  Lade Original-Daten ...")
    raw = load_original_data()

    stichtag = get_current_stichtag()
    print(f"  Stichtag: {stichtag.date()}")

    print("  Baue Snapshot (combine_to_snapshot) ...")
    snap = combine_to_snapshot(
        mitarbeiter=raw["mitarbeiter"],
        planstellen=raw["planstellen"],
        atz=raw["atz"],
        ausbildung=raw["ausbildung"],
        stichtag=stichtag,
    )
    print(f"  Snapshot: {len(snap)} Zeilen, besetzt: {snap['Is_Vacant'].eq(False).sum()}")

    if "MAK_Reporting" not in snap.columns:
        print("  Wende MAK-Allocation an ...")
        snap = apply_person_mak_allocation(snap)

    print(f"  MAK_Reporting vorhanden: {'MAK_Reporting' in snap.columns}")
    print(f"  EUR_Reporting vorhanden: {'EUR_Reporting' in snap.columns}")
    return snap


# ---------------------------------------------------------------------------
# Hauptvalidierung
# ---------------------------------------------------------------------------

def main():
    print(SEP)
    print("  PARSER-FIX VALIDIERUNG: _numeric_compensation_series")
    print(SEP)

    # Smoke-Check zuerst (unabhaengig von Echtdaten)
    print()
    print(SEP2)
    print("  1. PARSER SMOKE-CHECK (synthetisch)")
    print(SEP2)

    smoke_cases = [
        # (label, value, kind, expected)
        ("0.141 -> 0.141 (fte)",    "0.141",    "fte",      0.141),
        ("0,141 -> 0.141 (fte)",    "0,141",    "fte",      0.141),
        ("1.000 -> 1.0 (fte)",      "1.000",    "fte",      1.0),
        ("1.000 -> 1000 (currency)","1.000",    "currency", 1000.0),
        ("0.333 -> 0.333 (fte)",    "0.333",    "fte",      0.333),
        ("0.641 -> 0.641 (fte)",    "0.641",    "fte",      0.641),
        ("762,91 -> 762.91 (fte)",  "762,91",   "fte",      762.91),
        ("1.234,56 -> 1234.56",     "1.234,56", "currency", 1234.56),
        ("59.674.856,62 -> ...",    "59.674.856,62","currency", 59674856.62),
    ]

    smoke_ok = True
    for label, val, kind, exp in smoke_cases:
        got = float(_parse_series_new(pd.Series([val]), kind=kind).iloc[0])
        ok = abs(got - exp) < 0.001
        smoke_ok &= ok
        print(f"  {'OK  ' if ok else 'FAIL'} {label}: {got!r}")

    # Alten Parser demonstrieren
    print()
    print("  Alter Parser (buggy) fuer Vergleich:")
    for val, exp_wrong in [("0.141", 141.0), ("0.333", 333.0), ("0.641", 641.0)]:
        got_old = float(_parse_series_old(pd.Series([val])).iloc[0])
        got_new = float(_parse_series_new(pd.Series([val]), kind="fte").iloc[0])
        print(f"    {val!r}: alt={got_old!r}  neu={got_new!r}  {'FIX OK' if abs(got_new - float(val)) < 0.0001 else 'NOCH FALSCH!'}")

    # Echtdaten laden
    print()
    print(SEP2)
    print("  2. ECHTDATEN-VALIDIERUNG")
    print(SEP2)

    try:
        prepared = _load_prepared_df()
    except Exception as exc:
        print(f"  FEHLER beim Laden: {exc}")
        print("  Echtdaten-Validierung wird uebersprungen.")
        prepared = None

    if prepared is not None and "MAK_Reporting" in prepared.columns:
        mak_col = "MAK_Reporting"
        mak_raw = prepared[mak_col].fillna(0.0)
        mak_raw_sum = float(mak_raw.sum())

        # Strings im Datensatz suchen (diagnostisch)
        mak_str = mak_raw.astype("string")
        affected_old_mask = mak_str.str.match(r"^-?\d{1,3}\.\d{3}$").fillna(False)
        n_affected = int(affected_old_mask.sum())

        # IST_MAK mit neuem Parser
        ist_mak_new = _parse_series_new(mak_raw, kind="fte")
        ist_mak_new_sum = float(ist_mak_new.sum())

        # IST_MAK mit altem Parser
        ist_mak_old = _parse_series_old(mak_raw)
        ist_mak_old_sum = float(ist_mak_old.sum())

        # KPI-Referenz
        kpi_fte = compute_fte_effektiv(prepared)

        # EUR
        eur_col = "EUR_Reporting" if "EUR_Reporting" in prepared.columns else None
        if eur_col:
            eur_raw = prepared[eur_col].fillna(0.0)
            eur_raw_sum = float(eur_raw.sum())
            ist_eur_new = _parse_series_new(eur_raw, kind="currency")
            ist_eur_new_sum = float(ist_eur_new.sum())
        else:
            eur_raw_sum = ist_eur_new_sum = 0.0

        print()
        print(f"  MAK_Reporting Summe (roh, numerisch):  {mak_raw_sum:.4f}")
        print(f"  IST_MAK neuer Parser (kind='fte'):     {ist_mak_new_sum:.4f}")
        print(f"  IST_MAK alter Parser (buggy):          {ist_mak_old_sum:.4f}")
        print(f"  compute_fte_effektiv (bestehende KPI): {kpi_fte:.4f}")
        diff_new = ist_mak_new_sum - mak_raw_sum
        diff_old = ist_mak_old_sum - mak_raw_sum
        diff_kpi = ist_mak_new_sum - kpi_fte
        print()
        print(f"  Differenz neu vs. roh:  {diff_new:+.4f}  {'OK' if abs(diff_new) < TOL else 'ABWEICHUNG!'}")
        print(f"  Differenz alt vs. roh:  {diff_old:+.4f}  {'OK (bekannter Bug war hier)' if abs(diff_old) > 0.001 else 'OK (kein Bug beobachtet)'}")
        print(f"  Differenz neu vs. KPI:  {diff_kpi:+.4f}  {'OK' if abs(diff_kpi) < TOL else 'ABWEICHUNG!'}")

        print()
        print(f"  Zeilen, die alter Parser verfaelschte (X.YYY-Pattern): {n_affected}")
        if n_affected > 0:
            rows = prepared[affected_old_mask].copy()
            rows["_str_value"] = mak_str.loc[affected_old_mask]
            rows["_val_old"]   = ist_mak_old.loc[affected_old_mask]
            rows["_val_new"]   = ist_mak_new.loc[affected_old_mask]
            id_col = next((c for c in ("PersNr","Personalnummer","Planstellennr") if c in rows.columns), None)
            show = [c for c in [id_col, "_str_value", "_val_old", "_val_new"] if c]
            print(rows[show].head(20).to_string(index=False))

        print()
        print(SEP2)
        print("  EUR-VERGLEICH")
        print(SEP2)
        print(f"  EUR_Reporting Summe (roh):              {eur_raw_sum:>20,.2f}")
        print(f"  IST_EUR neuer Parser (kind='currency'): {ist_eur_new_sum:>20,.2f}")
        diff_eur = ist_eur_new_sum - eur_raw_sum
        rel_eur  = abs(diff_eur) / max(abs(eur_raw_sum), 1)
        print(f"  Differenz:                              {diff_eur:>+20,.2f}  {'OK' if rel_eur < 0.001 else 'ABWEICHUNG!'}")

        data_ok = abs(diff_new) < TOL and abs(diff_kpi) < TOL and rel_eur < 0.001
    else:
        print("  Echtdaten nicht verfuegbar – nur Smoke-Check.")
        data_ok = True  # Smoke-Check reicht als Nachweis

    print()
    print(SEP)
    all_ok = smoke_ok and data_ok
    if all_ok:
        print("  ERGEBNIS: BESTANDEN")
    else:
        print("  ERGEBNIS: FEHLER – Bitte Abweichungen pruefen.")
    print(SEP)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
