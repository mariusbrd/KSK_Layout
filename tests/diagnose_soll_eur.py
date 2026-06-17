"""
diagnose_soll_eur.py
====================
Prüft SOLL_EUR / Soll_Cost_Year an drei Stellen der Pipeline:

  Stage 0 – raw_snap   : combine_to_snapshot() + apply_person_mak_allocation()
  Stage 1 – prepared   : raw_snap nach _prepare_compact_data_clean()-Logik
                         (= was build_compact_compensation_planlevel_df bekommt)
  Stage 2 – comp_df    : out["SOLL_EUR"] in build_compact_compensation_planlevel_df
                         (= was im Dashboard angezeigt wird)

Fragestellung:
  - Existiert Soll_Cost_Year, wie viele Werte > 0, Summen?
  - Ist SOLL_EUR wirklich 0 oder nur im frühen Snapshot?

Run:
    py -X utf8 KSK_Layout/tests/diagnose_soll_eur.py
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

import streamlit as st  # noqa: F401

from dataloader.loader import load_original_data, combine_to_snapshot
from dataloader.mak_allocation import apply_person_mak_allocation
from dataloader.tvoed_loader import get_annual_salary, get_special_salary
from kpi_reference import get_current_stichtag
from config.settings import BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR

SEP  = "=" * 70
SEP2 = "-" * 50


# ── Replik von calculate_soll_cost (ohne st.session_state) ───────────────────
# Nutzt nur Fallback-Werte (tvoed_lookup leer), entspricht bare-mode-Verhalten.
# Im echten Dashboard wird stattdessen die geladene TVÖD-Tabelle genutzt.

def _calculate_soll_cost_bare(row: pd.Series) -> float:
    """calculate_soll_cost ohne session_state (Fallback-Modus)."""
    try:
        soll_fte = row.get("Soll_FTE", 1.0)
        if pd.isna(soll_fte):
            soll_fte = 1.0
        soll_fte = float(soll_fte)

        tarif = row.get("TrfGr")
        if pd.isna(tarif) if not isinstance(tarif, str) else not tarif.strip():
            tarif = row.get("Bewertung Tarifgruppe")
        if pd.isna(tarif) if not isinstance(tarif, str) else not tarif.strip():
            tarif = "E9A"
        tarif = str(tarif).strip().upper().replace(" ", "")

        step = row.get("St")
        if pd.isna(step) if not isinstance(step, (int, float)) else False:
            step = 4
        step_str = str(step).strip().replace("+", "").replace("-", "")
        try:
            step = int(step_str)
        except (ValueError, TypeError):
            step = 4

        employer_factor = EMPLOYER_COST_FACTOR  # session_state-Fallback

        # Sonderfälle (ohne session_state – hardcoded defaults)
        if tarif in ("TVAÖD", "TVÖAD", "TVAOD"):
            default_azubi = {1: 14400.0, 2: 15212.0, 3: 16099.0, 4: 17023.0}
            return default_azubi.get(step, 14400.0) * soll_fte * employer_factor
        if tarif == "1":
            return 200000.0 * soll_fte * employer_factor

        annual = get_annual_salary({}, tarif, step, BASE_SALARY, STEP_MULTIPLIER)
        return annual * employer_factor * soll_fte
    except Exception as e:
        return 0.0


def _numeric_series_currency(series: pd.Series) -> pd.Series:
    """Minimal-Parser fuer EUR-Werte (ohne Streamlit-Import)."""
    text = series.astype("string").str.strip()
    text = text.str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
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
    normalized = normalized.where(~only_comma, text.str.replace(",", ".", regex=False))
    only_dot   = ~has_comma & has_dot
    multi_dot  = only_dot & normalized.str.count(r"\.").gt(1).fillna(False)
    single_dot_k = only_dot & normalized.str.match(r"^-?[1-9]\d{0,2}\.\d{3}$").fillna(False)
    dot_thousands = multi_dot | single_dot_k
    normalized = normalized.where(~dot_thousands, normalized.str.replace(".", "", regex=False))
    return pd.to_numeric(normalized, errors="coerce").fillna(0.0)


def _report_col(label: str, series: pd.Series) -> None:
    vals = pd.to_numeric(series, errors="coerce").fillna(0.0)
    n_pos = int((vals > 0).sum())
    n_neg = int((vals < 0).sum())
    n_zero= int((vals == 0).sum())
    n_nan = int(series.isna().sum())
    total = float(vals.sum())
    print(f"\n  {label}")
    print(f"    Zeilen gesamt:   {len(vals)}")
    print(f"    > 0:             {n_pos}")
    print(f"    == 0:            {n_zero}")
    print(f"    < 0:             {n_neg}")
    print(f"    NaN/fehlend:     {n_nan}")
    print(f"    Summe:           {total:>20,.2f}")
    if n_pos > 0:
        print(f"    Ø (alle Zeilen): {total/len(vals):>20,.2f}")
        print(f"    Ø (nur > 0):     {total/n_pos:>20,.2f}")


def main() -> int:
    print(SEP)
    print("  DIAGNOSE SOLL_EUR / Soll_Cost_Year – Pipeline-Trace")
    print(SEP)

    # ── Stage 0: roher Snapshot ──────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  STAGE 0: raw_snap (combine_to_snapshot + MAK-Allocation)")
    print(SEP2)

    raw      = load_original_data()
    stichtag = get_current_stichtag()
    raw_snap = combine_to_snapshot(
        mitarbeiter=raw["mitarbeiter"],
        planstellen=raw["planstellen"],
        atz=raw["atz"],
        ausbildung=raw["ausbildung"],
        stichtag=stichtag,
    )
    if "MAK_Reporting" not in raw_snap.columns:
        raw_snap = apply_person_mak_allocation(raw_snap)

    print(f"  Zeilen: {len(raw_snap)}")
    print(f"  Soll_Cost_Year vorhanden: {'Soll_Cost_Year' in raw_snap.columns}")

    ist_eur_col = next(
        (c for c in ["EUR_Reporting", "Total_Cost_Year"] if c in raw_snap.columns), None
    )
    print(f"  IST-EUR-Spalte:           {ist_eur_col!r}")

    if "Soll_Cost_Year" in raw_snap.columns:
        _report_col("Soll_Cost_Year (Stage 0)", raw_snap["Soll_Cost_Year"])
    if ist_eur_col:
        _report_col(f"{ist_eur_col} (Stage 0 IST)", raw_snap[ist_eur_col])

    # ── Stage 1: prepared_df (_prepare_compact_data_clean Logik) ────────────
    print(f"\n{SEP2}")
    print("  STAGE 1: prepared_df (nach _prepare_compact_data_clean-Logik)")
    print("  [Soll_Cost_Year wird via calculate_soll_cost(bare-mode) ergaenzt]")
    print(SEP2)

    prepared = raw_snap.copy()
    if "Soll_Cost_Year" not in prepared.columns:
        print("  -> Soll_Cost_Year nicht vorhanden, berechne jetzt ...")
        prepared["Soll_Cost_Year"] = prepared.apply(_calculate_soll_cost_bare, axis=1)
        print("  -> berechnet.")
    else:
        print("  -> Soll_Cost_Year bereits im Snapshot vorhanden.")

    _report_col("Soll_Cost_Year (Stage 1 / prepared_df)", prepared["Soll_Cost_Year"])

    # Detailanalyse: welche TrfGr liefern 0?
    zero_mask = pd.to_numeric(prepared["Soll_Cost_Year"], errors="coerce").fillna(0) == 0
    if zero_mask.any():
        print(f"\n  Zeilen mit Soll_Cost_Year == 0 ({zero_mask.sum()} Stück):")
        diag_cols = [c for c in ["TrfGr", "Bewertung Tarifgruppe", "St", "Soll_FTE",
                                  "Is_Vacant", "Planstellennr"] if c in prepared.columns]
        zero_df = prepared.loc[zero_mask, diag_cols].copy()
        val_counts_trfgr = (
            zero_df["TrfGr"].fillna("(NaN)").value_counts()
            if "TrfGr" in zero_df.columns else pd.Series(dtype=int)
        )
        print("  TrfGr-Verteilung bei Soll_Cost_Year==0:")
        print(val_counts_trfgr.to_string())
        vacant_zero = prepared.loc[zero_mask, "Is_Vacant"].sum() if "Is_Vacant" in prepared.columns else "?"
        print(f"  Davon Is_Vacant==True: {vacant_zero}")
    else:
        print("  Alle Zeilen haben Soll_Cost_Year > 0.")

    # ── Stage 2: comp_df SOLL_EUR / IST_EUR Simulation ───────────────────────
    print(f"\n{SEP2}")
    print("  STAGE 2: comp_df – Simulation build_compact_compensation_planlevel_df")
    print("  [Entspricht dem, was im Dashboard angezeigt wird]")
    print(SEP2)

    # SOLL_EUR: Soll_Cost_Year aus Stage 1 parsen
    soll_eur_raw = _numeric_series_currency(prepared["Soll_Cost_Year"])

    # IST_EUR: EUR_Reporting / Total_Cost_Year parsen
    if ist_eur_col:
        ist_eur_raw = _numeric_series_currency(prepared[ist_eur_col])
    else:
        ist_eur_raw = pd.Series(0.0, index=prepared.index)

    # Duplikat-Deduplizierung (wie in build_compact_compensation_planlevel_df)
    if "Planstellennr" in prepared.columns:
        pl_str    = prepared["Planstellennr"].fillna("").astype(str).str.strip()
        has_pl    = pl_str.ne("")
        is_first  = ~(pl_str.where(has_pl).duplicated(keep="first"))
        is_dup    = has_pl & ~is_first
        n_dup     = int(is_dup.sum())
        soll_eur_dedup = soll_eur_raw.copy()
        soll_eur_dedup.loc[is_dup] = 0.0
    else:
        n_dup = 0
        soll_eur_dedup = soll_eur_raw.copy()

    _report_col("SOLL_EUR (Stage 2, nach Dedup)", soll_eur_dedup)
    _report_col("IST_EUR  (Stage 2)", ist_eur_raw)

    delta_eur = ist_eur_raw - soll_eur_dedup
    total_delta = float(delta_eur.sum())
    total_ist   = float(ist_eur_raw.sum())
    total_soll  = float(soll_eur_dedup.sum())

    print(f"\n  ── ZUSAMMENFASSUNG EUR ──")
    print(f"  IST_EUR  Summe:     {total_ist:>25,.2f}")
    print(f"  SOLL_EUR Summe:     {total_soll:>25,.2f}")
    print(f"  DELTA_EUR Summe:    {total_delta:>25,.2f}  (IST - SOLL)")
    if total_soll != 0:
        print(f"  Budget-Ausschöpfung: {total_ist/total_soll*100:.1f}%")
    else:
        print(f"  SOLL_EUR = 0 → Budgetquote nicht berechenbar")

    print(f"\n  Deduplizierte Planstellen (SOLL auf 0 gesetzt): {n_dup}")
    if n_dup > 0:
        soll_roh   = float(soll_eur_raw.sum())
        soll_saved = soll_roh - total_soll
        print(f"  SOLL_EUR roh (vor Dedup):  {soll_roh:>20,.2f}")
        print(f"  Rausgenommener Betrag:     {soll_saved:>20,.2f}")

    # ── Kontext: Bare-mode vs. echtes Dashboard ───────────────────────────────
    print(f"\n{SEP2}")
    print("  HINWEIS: Bare-mode vs. echtes Dashboard")
    print(SEP2)
    print("  Dieses Skript nutzt calculate_soll_cost im Fallback-Modus:")
    print("    - tvoed_lookup = {}  (kein geladenes TVÖD)")
    print("    - BASE_SALARY × STEP_MULTIPLIER × EMPLOYER_COST_FACTOR")
    print("  Im echten Dashboard wird die TVÖD-Tabelle aus session_state genutzt.")
    print("  Die Summe kann daher abweichen, aber Nullen-Muster bleibt gleich.")

    # Wichtige Erkenntnis kommunizieren
    print(f"\n{SEP}")
    if total_soll > 0:
        print("  ERGEBNIS: SOLL_EUR ist NICHT 0 im Dashboard.")
        print("  Soll_Cost_Year wird von _prepare_compact_data_clean immer ergaenzt.")
        print("  Der Wert im Debug-Expander 'SOLL_EUR = 0' war falsch → wird korrigiert.")
    else:
        print("  ERGEBNIS: SOLL_EUR = 0 (alle Werte null – prüfe TrfGr/St-Spalten).")
    print(SEP)

    return 0


if __name__ == "__main__":
    sys.exit(main())
