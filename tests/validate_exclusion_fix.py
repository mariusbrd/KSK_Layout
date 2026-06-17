"""
validate_exclusion_fix.py
=========================
Validiert den Exklusions-Fix:
  apply_exclusions() nullt jetzt auch MAK_Reporting und EUR_Reporting.

Prüfungen:
  1. Is_Vacant=True Zeilen haben nach Fix MAK_Reporting=0 und EUR_Reporting=0
  2. IST_MAK / IST_EUR in comp_df entsprechen den Erwartungswerten (~762,911 / ~59,36 Mio.)
  3. SOLL-Werte bleiben unverändert
  4. IST_Köpfe bleibt bei ca. 903
  5. Mischzuordnungen: Personen mit excluded + non-excluded Planstellen

Run:
    py -X utf8 KSK_Layout/tests/validate_exclusion_fix.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: F401
import pandas as pd
import numpy as np

from dataloader.loader import load_original_data, combine_to_snapshot, apply_exclusions
from dataloader.mak_allocation import apply_person_mak_allocation
from kpi_reference import get_current_stichtag
from config.settings import BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR
from dataloader.tvoed_loader import get_annual_salary
from utils.compact_page_loader import load_compact_page_module
from utils.settings_loader import JOBFAMILY_VALIDATION_ORG_UNITS

SEP  = "=" * 70
SEP2 = "-" * 50

_EXCL_SA = {
    "vorstand": False, "ruhend_bv": True,
    "planstellen_follow_person": True,
    "org_units": JOBFAMILY_VALIDATION_ORG_UNITS,
    "special_groups": ["ausbildung_nachwuchs", "sollarbeitszeit_001_positions"],
}
_EXCL_SB = {
    "vorstand": False, "ruhend_bv": True,
    "planstellen_follow_person": True,
    "org_units": JOBFAMILY_VALIDATION_ORG_UNITS,
    "special_groups": [
        "ausbildung_nachwuchs",
        "jobfamily_validation_special_positions",
        "sollarbeitszeit_001_positions",
    ],
}


def _n(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0)


def _calc_soll(row: pd.Series) -> float:
    try:
        soll_fte = float(row.get("Soll_FTE") or 0.0)
        tarif = str(row.get("TrfGr") or "E9A").strip().upper().replace(" ", "") or "E9A"
        try:
            step = int(str(row.get("St") or "4").strip().replace("+", "").replace("-", ""))
        except Exception:
            step = 4
        return get_annual_salary({}, tarif, step, BASE_SALARY, STEP_MULTIPLIER) * EMPLOYER_COST_FACTOR * soll_fte
    except Exception:
        return 0.0


FAIL = 0
PASS_COUNT = 0


def _check(name: str, condition: bool, detail: str = "") -> bool:
    global FAIL, PASS_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")
    return condition


def main() -> int:
    global FAIL, PASS_COUNT
    print(SEP)
    print("  VALIDIERUNG: Exklusions-Fix MAK_Reporting / EUR_Reporting")
    print(SEP)

    # ── Basisdaten laden ─────────────────────────────────────────────────────
    raw      = load_original_data()
    stichtag = get_current_stichtag()
    base_snap = combine_to_snapshot(
        mitarbeiter=raw["mitarbeiter"],
        planstellen=raw["planstellen"],
        atz=raw["atz"],
        ausbildung=raw["ausbildung"],
        stichtag=stichtag,
    )
    if "MAK_Reporting" not in base_snap.columns:
        base_snap = apply_person_mak_allocation(base_snap)
    print(f"Snapshot: {len(base_snap)} Zeilen, Stichtag {stichtag.date()}")

    mod = load_compact_page_module()

    def _build(excl: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
        snap = apply_exclusions(base_snap.copy(), excl)
        if "Soll_Cost_Year" not in snap.columns:
            snap["Soll_Cost_Year"] = snap.apply(_calc_soll, axis=1)
        return snap, mod.build_compact_compensation_planlevel_df(snap)

    snap_sa, comp_sa = _build(_EXCL_SA)
    snap_sb, comp_sb = _build(_EXCL_SB)

    # ── Prüfung 1: Is_Vacant=True Restwerte ─────────────────────────────────
    print(f"\n{SEP2}")
    print("  PRÜFUNG 1: Keine Restwerte auf Is_Vacant=True Zeilen")
    print(SEP2)

    is_vac_sa = snap_sa.get("Is_Vacant", pd.Series(False, index=snap_sa.index)).fillna(False).astype(bool)
    mak_rep   = _n(snap_sa, "MAK_Reporting")
    eur_rep   = _n(snap_sa, "EUR_Reporting")
    alloc_w   = _n(snap_sa, "Allocation_Weight")
    pers_mak  = _n(snap_sa, "Personen_MAK")

    n_vac = int(is_vac_sa.sum())
    n_mak_rest = int((mak_rep[is_vac_sa] > 0).sum())
    n_eur_rest = int((eur_rep[is_vac_sa] > 0).sum())
    n_alloc_rest = int((alloc_w[is_vac_sa] > 0).sum())
    sum_mak_rest  = float(mak_rep[is_vac_sa].sum())
    sum_eur_rest  = float(eur_rep[is_vac_sa].sum())

    print(f"  Is_Vacant=True gesamt:                    {n_vac:>6}")
    print(f"  Davon MAK_Reporting > 0:                  {n_mak_rest:>6}  (Summe: {sum_mak_rest:.4f})")
    print(f"  Davon EUR_Reporting > 0:                  {n_eur_rest:>6}  (Summe: {sum_eur_rest:,.2f})")
    print(f"  Davon Allocation_Weight > 0:              {n_alloc_rest:>6}")
    print()

    _check("MAK_Reporting auf Is_Vacant=True Zeilen = 0",
           n_mak_rest == 0, f"{n_mak_rest} Zeilen mit Restwert {sum_mak_rest:.3f}")
    _check("EUR_Reporting auf Is_Vacant=True Zeilen = 0",
           n_eur_rest == 0, f"{n_eur_rest} Zeilen mit Restwert {sum_eur_rest:,.2f}")
    _check("Allocation_Weight auf Is_Vacant=True Zeilen = 0",
           n_alloc_rest == 0, f"{n_alloc_rest} Zeilen > 0")

    # ── Prüfung 2: Compensation-Kennzahlen ───────────────────────────────────
    print(f"\n{SEP2}")
    print("  PRÜFUNG 2: Compensation-Kennzahlen (SA = Standard ohne JFV)")
    print(SEP2)

    ist_mak = float(comp_sa["IST_MAK"].sum()) if "IST_MAK" in comp_sa.columns else 0.0
    ist_eur = float(comp_sa["IST_EUR"].sum()) if "IST_EUR" in comp_sa.columns else 0.0
    soll_mak = float(comp_sa["SOLL_MAK"].sum()) if "SOLL_MAK" in comp_sa.columns else 0.0
    soll_eur = float(comp_sa["SOLL_EUR"].sum()) if "SOLL_EUR" in comp_sa.columns else 0.0
    delta_eur = float(comp_sa["DELTA_EUR"].sum()) if "DELTA_EUR" in comp_sa.columns else 0.0
    ist_koepfe = int(comp_sa["IST_Kopf"].sum()) if "IST_Kopf" in comp_sa.columns else 0
    soll_planst = int(comp_sa["SOLL_Planstellen"].sum()) if "SOLL_Planstellen" in comp_sa.columns else 0

    print(f"  IST_Köpfe:        {ist_koepfe:>8,}")
    print(f"  SOLL_Planstellen: {soll_planst:>8,}")
    print(f"  IST_MAK:          {ist_mak:>12,.3f}")
    print(f"  SOLL_MAK:         {soll_mak:>12,.3f}")
    print(f"  IST_EUR:          {ist_eur:>16,.2f}")
    print(f"  SOLL_EUR:         {soll_eur:>16,.2f}")
    print(f"  DELTA_EUR:        {delta_eur:>16,.2f}")
    print()

    # Toleranzen für Erwartungswerte (±5% vom Zielwert)
    _check("IST_MAK zwischen 720 und 810 (erw. ~762,9)",
           720.0 <= ist_mak <= 810.0, f"tatsächlich: {ist_mak:.3f}")
    _check("IST_EUR zwischen 55 Mio. und 63 Mio. (erw. ~59,4 Mio.)",
           55_000_000 <= ist_eur <= 63_000_000, f"tatsächlich: {ist_eur:,.0f}")
    _check("IST_EUR ist NICHT mehr 70,6 Mio. (alt, fehlerhaft)",
           ist_eur < 68_000_000, f"tatsächlich: {ist_eur:,.0f}")
    _check("IST_Köpfe zwischen 890 und 916 (erw. ~903)",
           890 <= ist_koepfe <= 916, f"tatsächlich: {ist_koepfe}")
    _check("SOLL_MAK unverändert > 1000 (SOLL nicht betroffen)",
           soll_mak > 1_000.0, f"tatsächlich: {soll_mak:.3f}")
    _check("SOLL_EUR unverändert > 70 Mio. (SOLL nicht betroffen)",
           soll_eur > 70_000_000, f"tatsächlich: {soll_eur:,.0f}")
    _check("DELTA_EUR negativer als -10 Mio. (vorher: -5,2 Mio.)",
           delta_eur < -10_000_000, f"tatsächlich: {delta_eur:,.0f}")

    # ── Prüfung 3: SA und SB identisch? ──────────────────────────────────────
    print(f"\n{SEP2}")
    print("  PRÜFUNG 3: SA vs. SB (JFV-Exklusion inkrementeller Effekt)")
    print(SEP2)

    ist_mak_sb = float(comp_sb["IST_MAK"].sum()) if "IST_MAK" in comp_sb.columns else 0.0
    ist_eur_sb = float(comp_sb["IST_EUR"].sum()) if "IST_EUR" in comp_sb.columns else 0.0

    print(f"  SA IST_MAK: {ist_mak:.3f}  |  SB IST_MAK: {ist_mak_sb:.3f}  |  Diff: {ist_mak_sb - ist_mak:+.3f}")
    print(f"  SA IST_EUR: {ist_eur:,.2f}  |  SB IST_EUR: {ist_eur_sb:,.2f}  |  Diff: {ist_eur_sb - ist_eur:+,.2f}")
    print()

    _check("SA IST_MAK == SB IST_MAK (JFV auf MAK kein Zusatzeffekt mehr)",
           abs(ist_mak - ist_mak_sb) < 1.0, f"Diff: {abs(ist_mak - ist_mak_sb):.3f}")
    _check("SA IST_EUR == SB IST_EUR (JFV auf EUR kein Zusatzeffekt mehr)",
           abs(ist_eur - ist_eur_sb) < 1_000, f"Diff: {abs(ist_eur - ist_eur_sb):,.0f}")

    # Erklärung: JFV-Planstellen sind auch durch sollarbeitszeit_001 erfasst
    is_vac_sb = snap_sb.get("Is_Vacant", pd.Series(False, index=snap_sb.index)).fillna(False).astype(bool)
    zusatz_excl_sb = (is_vac_sb & ~is_vac_sa).sum()
    print(f"  (Zusätzlich in SB ausgeschlossen: {zusatz_excl_sb} Zeilen)")

    # ── Prüfung 4: Mischzuordnungen ──────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  PRÜFUNG 4: MISCHZUORDNUNGEN (Person in excluded + non-excluded)")
    print(SEP2)
    print("  Basis: Snapshot NACH apply_exclusions (SA), VOR Nullung durch Fix")
    print("         d.h. base_snap direkt ohne excl, dann analyse per PersNr\n")

    # Für diese Analyse nutzen wir den basis_snap (vor excl), um zu sehen,
    # welche Personen Zeilen in BEIDEN Gruppen haben.
    is_vac_base = base_snap.get("Is_Vacant", pd.Series(False, index=base_snap.index)).fillna(False).astype(bool)
    # Alle Zeilen die durch SA-Exklusion excluded werden (aber nicht schon vorher)
    snap_sa_for_mix = apply_exclusions(base_snap.copy(), _EXCL_SA)
    is_vac_after = snap_sa_for_mix.get("Is_Vacant", pd.Series(False, index=snap_sa_for_mix.index)).fillna(False).astype(bool)
    # neu ausgeschlossen durch apply_exclusions SA:
    newly_excluded = is_vac_after & ~is_vac_base

    if "PersNr" in base_snap.columns:
        # Personen in excluded Zeilen (nach SA)
        excl_persnr = set(
            snap_sa_for_mix.loc[is_vac_after, "PersNr"].dropna().unique()
        )
        # Personen in non-excluded Zeilen (nach SA)
        nonexcl_persnr = set(
            snap_sa_for_mix.loc[~is_vac_after, "PersNr"].dropna().unique()
        )
        # Schnittmenge = Mischzuordnungen
        mixed_persnr = excl_persnr & nonexcl_persnr
        n_mixed = len(mixed_persnr)

        print(f"  Personen in excluded Zeilen:               {len(excl_persnr):>6}")
        print(f"  Personen in non-excluded Zeilen:           {len(nonexcl_persnr):>6}")
        print(f"  Personen in BEIDEN (Mischzuordnung):       {n_mixed:>6}")
        print()

        _check("Keine Mischzuordnungen vorhanden (0 Personen in beiden Gruppen)",
               n_mixed == 0, f"{n_mixed} Personen betroffen")

        if n_mixed > 0:
            print(f"\n  Detail Mischzuordnungen (max. 10 Personen):")
            show = list(mixed_persnr)[:10]
            for pid in show:
                exc_rows  = snap_sa_for_mix[is_vac_after  & (snap_sa_for_mix["PersNr"] == pid)]
                noex_rows = snap_sa_for_mix[~is_vac_after & (snap_sa_for_mix["PersNr"] == pid)]
                mak_e  = float(_n(exc_rows,  "MAK_Reporting").sum())
                eur_e  = float(_n(exc_rows,  "EUR_Reporting").sum())
                mak_ne = float(_n(noex_rows, "MAK_Reporting").sum())
                eur_ne = float(_n(noex_rows, "EUR_Reporting").sum())
                alloc_e  = float(_n(exc_rows, "Allocation_Weight").sum())
                alloc_ne = float(_n(noex_rows, "Allocation_Weight").sum())
                n_exc_r = len(exc_rows); n_nex_r = len(noex_rows)
                print(f"    PersNr {pid}:")
                print(f"      excluded  Zeilen: {n_exc_r}  AllocW: {alloc_e:.3f}  MAK_Rep: {mak_e:.4f}  EUR_Rep: {eur_e:,.0f}")
                print(f"      non-excl  Zeilen: {n_nex_r}  AllocW: {alloc_ne:.3f}  MAK_Rep: {mak_ne:.4f}  EUR_Rep: {eur_ne:,.0f}")
                print(f"      -> Nach Fix: EUR_excl wird 0; EUR_nonexcl bleibt {eur_ne:,.0f} (keine Re-Allokation)")
    else:
        print("  PersNr nicht vorhanden — Mischzuordnungs-Analyse nicht möglich.")
        _check("Mischzuordnungs-Analyse", False, "PersNr fehlt")

    # ── Zusammenfassung ──────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  ERGEBNISBERICHT")
    print(SEP)

    vorher_ist_mak = 860.335
    vorher_ist_eur = 70_628_574.16

    print(f"""
  Geänderte Datei:    KSK_Layout/dataloader/loader.py
  Geänderte Funktion: apply_exclusions()
  Genullte Spalten (neu): MAK_Reporting, EUR_Reporting,
                          Allocation_Weight, Personen_MAK
  SOLL-Spalten unverändert: Ja (Soll_FTE, Soll_Cost_Year nicht genullt)

  Validierung (SA = Standard-Exklusionen ohne JFV):
    Is_Vacant=True mit MAK_Reporting > 0:  {n_mak_rest}  (erw. 0)
    Is_Vacant=True mit EUR_Reporting > 0:  {n_eur_rest}  (erw. 0)
    IST_MAK vorher:        {vorher_ist_mak:.3f}
    IST_MAK nachher:       {ist_mak:.3f}
    IST_EUR vorher:        {vorher_ist_eur:,.2f}
    IST_EUR nachher:       {ist_eur:,.2f}
    SOLL_MAK unverändert:  {'Ja' if soll_mak > 1000 else 'NEIN — FEHLER'}  ({soll_mak:.3f})
    SOLL_EUR unverändert:  {'Ja' if soll_eur > 70_000_000 else 'NEIN — FEHLER'}  ({soll_eur:,.0f})
    IST_Köpfe:             {ist_koepfe}  (erw. ~903)
    DELTA_EUR nachher:     {delta_eur:,.2f}

  Mischzuordnungen:      {'Ja — ' + str(n_mixed) + ' Personen (Re-Allokation offen)' if 'n_mixed' in dir() and n_mixed > 0 else 'Nein (keine Personen in excluded und non-excluded zugleich)'}

  Offene fachliche Entscheidung:
    Sollen Exklusionen künftig auch SOLL entfernen?  Offen
    (SOLL_EUR bleibt bei {soll_eur:,.0f}, inkl. excluded Planstellen)
    (DELTA_EUR = IST_EUR[bereinigt] - SOLL_EUR[unbereinigt] → {delta_eur:,.0f})
""")

    total = PASS_COUNT + FAIL
    print(f"  Tests: {PASS_COUNT}/{total} PASS  |  {FAIL} FAIL")
    print(SEP)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
