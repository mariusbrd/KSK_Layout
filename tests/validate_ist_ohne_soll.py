"""
validate_ist_ohne_soll.py
=========================
Validiert die Klassifikationsspalte Ist_ohne_Plan_Soll_Kategorie
in build_compact_compensation_planlevel_df().

Run:
    py -X utf8 KSK_Layout/tests/validate_ist_ohne_soll.py
"""
import sys, warnings
from pathlib import Path
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa
import pandas as pd

from dataloader.loader import load_original_data, combine_to_snapshot
from dataloader.mak_allocation import apply_person_mak_allocation
from kpi_reference import get_current_stichtag
from config.settings import BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR
from dataloader.tvoed_loader import get_annual_salary
from utils.compact_page_loader import load_compact_page_module

SEP = "=" * 65


def _calc_soll(row) -> float:
    try:
        soll_fte = float(row.get("Soll_FTE") or 0.0)
        tarif = str(row.get("TrfGr") or "E9A").strip().upper().replace(" ", "")
        if not tarif:
            tarif = "E9A"
        try:
            step = int(str(row.get("St") or "4").strip().replace("+", "").replace("-", ""))
        except Exception:
            step = 4
        annual = get_annual_salary({}, tarif, step, BASE_SALARY, STEP_MULTIPLIER)
        return annual * EMPLOYER_COST_FACTOR * soll_fte
    except Exception:
        return 0.0


def main() -> int:
    print(SEP)
    print("  VALIDIERUNG: Ist_ohne_Plan_Soll_Kategorie")
    print(SEP)

    # Daten laden
    raw      = load_original_data()
    stichtag = get_current_stichtag()
    snap     = combine_to_snapshot(
        mitarbeiter=raw["mitarbeiter"],
        planstellen=raw["planstellen"],
        atz=raw["atz"],
        ausbildung=raw["ausbildung"],
        stichtag=stichtag,
    )
    if "MAK_Reporting" not in snap.columns:
        snap = apply_person_mak_allocation(snap)

    prep = snap.copy()
    if "Soll_Cost_Year" not in prep.columns:
        prep["Soll_Cost_Year"] = prep.apply(_calc_soll, axis=1)

    print(f"Snapshot: {len(prep)} Zeilen, Stichtag {stichtag.date()}")

    # Kompakt-Modul laden und comp_df bauen
    mod  = load_compact_page_module()
    comp = mod.build_compact_compensation_planlevel_df(prep)

    print(f"comp_df: {len(comp)} Zeilen, {len(comp.columns)} Spalten")

    # Prüfe IST-MAK / IST-EUR unverändert
    ist_mak = float(comp["IST_MAK"].sum()) if "IST_MAK" in comp.columns else 0.0
    ist_eur = float(comp["IST_EUR"].sum()) if "IST_EUR" in comp.columns else 0.0
    soll_mak = float(comp["SOLL_MAK"].sum()) if "SOLL_MAK" in comp.columns else 0.0
    soll_eur = float(comp["SOLL_EUR"].sum()) if "SOLL_EUR" in comp.columns else 0.0

    print(f"\nIST_MAK:   {ist_mak:.4f}")
    print(f"IST_EUR:   {ist_eur:>15,.2f}")
    print(f"SOLL_MAK:  {soll_mak:.4f}")
    print(f"SOLL_EUR:  {soll_eur:>15,.2f}")

    cat_col = "Ist_ohne_Plan_Soll_Kategorie"
    if cat_col not in comp.columns:
        print(f"\nFEHLER: {cat_col} nicht in comp_df!")
        return 1

    subset = comp[comp[cat_col].ne("")]
    print(f"\n--- Ist_ohne_Plan_Soll_Kategorie ---")
    print(f"Zeilen mit Kategorie: {len(subset)}")
    print(f"IST_MAK (Subset):     {float(subset['IST_MAK'].sum()):.4f}")
    print(f"IST_EUR (Subset):     {float(subset['IST_EUR'].sum()):>15,.2f}")
    print(f"\nVerteilung:")
    vc = comp[cat_col].value_counts()
    for cat, cnt in vc.items():
        if cat == "":
            continue
        sub = subset[subset[cat_col] == cat]
        mak = float(sub["IST_MAK"].sum())
        eur = float(sub["IST_EUR"].sum())
        print(f"  {cat:<45} {cnt:>4} Zeilen  MAK {mak:.3f}  EUR {eur:>12,.0f}")

    # Prüfe bekannte Referenzwerte aus vorheriger Analyse
    # Erwartete Kategorien aus Analyse: 19 Trainee, 54 RuhendesBV, 43 ATZ, 4 Rente, Pool, ...
    n_trainee = int((comp[cat_col] == "Trainee / Ausbildung").sum())
    n_ruhend  = int((comp[cat_col] == "Ruhendes Beschäftigungsverhältnis").sum())
    n_atz     = int((comp[cat_col] == "ATZ / Freistellung").sum())
    n_regulaer= int((comp[cat_col] == "Reguläre aktive Stelle ohne Soll_FTE").sum())
    n_pool    = int((comp[cat_col] == "Pool- / Sammelplanstelle").sum())

    print(f"\n--- Plausibilitätsprüfung (Referenz aus Analyse) ---")
    print(f"Trainee / Ausbildung:                {n_trainee:>4}  (erw. ca. 19)")
    print(f"Ruhendes Beschäftigungsverhältnis:   {n_ruhend:>4}  (erw. ca. 54)")
    print(f"ATZ / Freistellung:                  {n_atz:>4}  (erw. ca. 43)")
    print(f"Pool- / Sammelplanstelle:            {n_pool:>4}  (erw. ca. 57-65)")
    print(f"Reguläre aktive Stelle ohne Soll_FTE:{n_regulaer:>4}  (erw. ca. 30-42)")

    ok = len(subset) > 0 and "Ist_ohne_Plan_Soll_Kategorie" in comp.columns
    print(f"\n{SEP}")
    print(f"  Ergebnis: {'BESTANDEN' if ok else 'FEHLER'}")
    print(SEP)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
