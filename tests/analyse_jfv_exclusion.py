"""
analyse_jfv_exclusion.py
========================
Vergleicht IST-ohne-Plan-SOLL-Klassifikation in drei Szenarien:

  S0 : Keine Exklusionen (Rohdata-Baseline)
  SA : Alle Standard-Exklusionen OHNE jobfamily_validation_special_positions
  SB : Alle Standard-Exklusionen MIT jobfamily_validation_special_positions
       (= aktueller Dashboard-Standard per user_settings.json)

Ziel: Prüfen, ob die 93 "Reguläre aktive Stellen ohne Soll_FTE" und
      20 "Pool-/Sammelplanstellen" nach Aktivierung der JFV-Exclusion
      noch relevant ins Gewicht fallen.

Keine Code-Änderungen — nur Analyse.

Run:
    py -X utf8 KSK_Layout/tests/analyse_jfv_exclusion.py
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

from dataloader.loader import load_original_data, combine_to_snapshot, apply_exclusions
from dataloader.mak_allocation import apply_person_mak_allocation
from kpi_reference import get_current_stichtag
from config.settings import BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR
from dataloader.tvoed_loader import get_annual_salary
from utils.compact_page_loader import load_compact_page_module
from utils.settings_loader import JOBFAMILY_VALIDATION_ORG_UNITS
from utils.exclusion_groups import JOBFAMILY_VALIDATION_SPECIAL_PLAN_IDS

SEP  = "=" * 70
SEP2 = "-" * 50

CAT_ORDER = [
    "Trainee / Ausbildung",
    "Ruhendes Beschäftigungsverhältnis",
    "ATZ / Freistellung",
    "Rente auf Zeit",
    "Pool- / Sammelplanstelle",
    "Reguläre aktive Stelle ohne Soll_FTE",
    "Sonstiger Fall ohne Plan-SOLL",
]

# --- Exklusions-Konfigurationen -----------------------------------------------

_EXCL_NONE: dict = {   # S0: Keine Exklusionen
    "vorstand": False,
    "ruhend_bv": False,
    "planstellen_follow_person": False,
    "org_units": [],
    "special_groups": [],
}

_EXCL_SA: dict = {     # SA: Alles außer Jobfamily Validierung
    "vorstand": False,
    "ruhend_bv": True,
    "planstellen_follow_person": True,
    "org_units": JOBFAMILY_VALIDATION_ORG_UNITS,
    "special_groups": ["ausbildung_nachwuchs", "sollarbeitszeit_001_positions"],
}

_EXCL_SB: dict = {     # SB: Dashboard-Standard (inkl. JFV)
    "vorstand": False,
    "ruhend_bv": True,
    "planstellen_follow_person": True,
    "org_units": JOBFAMILY_VALIDATION_ORG_UNITS,
    "special_groups": [
        "ausbildung_nachwuchs",
        "jobfamily_validation_special_positions",
        "sollarbeitszeit_001_positions",
    ],
}


# --- Hilfsfunktionen ----------------------------------------------------------

def _calc_soll(row: pd.Series) -> float:
    """Berechne Soll_Cost_Year im Fallback-Modus (ohne TVÖD-Tabelle)."""
    try:
        soll_fte = float(row.get("Soll_FTE") or 0.0)
        tarif = str(row.get("TrfGr") or "E9A").strip().upper().replace(" ", "") or "E9A"
        try:
            step = int(str(row.get("St") or "4").strip().replace("+", "").replace("-", ""))
        except Exception:
            step = 4
        annual = get_annual_salary({}, tarif, step, BASE_SALARY, STEP_MULTIPLIER)
        return annual * EMPLOYER_COST_FACTOR * soll_fte
    except Exception:
        return 0.0


def _cat_detail(comp: pd.DataFrame) -> dict[str, dict]:
    cat_col = "Ist_ohne_Plan_Soll_Kategorie"
    if cat_col not in comp.columns:
        return {c: {"n": 0, "eur": 0.0, "mak": 0.0} for c in CAT_ORDER}
    subset = comp[comp[cat_col].ne("")]
    result = {}
    for cat in CAT_ORDER:
        rows = subset[subset[cat_col] == cat]
        result[cat] = {
            "n":   len(rows),
            "eur": float(rows["IST_EUR"].sum()) if "IST_EUR" in rows.columns else 0.0,
            "mak": float(rows["IST_MAK"].sum()) if "IST_MAK" in rows.columns else 0.0,
        }
    return result


def _summary(comp: pd.DataFrame, label: str) -> dict:
    cat_col = "Ist_ohne_Plan_Soll_Kategorie"
    subset  = comp[comp[cat_col].ne("")] if cat_col in comp.columns else pd.DataFrame()
    return {
        "label":        label,
        "zeilen":       len(comp),
        "ist_koepfe":   int(comp["IST_Kopf"].sum())       if "IST_Kopf"       in comp.columns else 0,
        "soll_planst":  int(comp["SOLL_Planstellen"].sum()) if "SOLL_Planstellen" in comp.columns else 0,
        "ist_mak":      float(comp["IST_MAK"].sum())      if "IST_MAK"        in comp.columns else 0.0,
        "soll_mak":     float(comp["SOLL_MAK"].sum())     if "SOLL_MAK"       in comp.columns else 0.0,
        "delta_mak":    float((comp["DELTA_MAK"].sum()))  if "DELTA_MAK"      in comp.columns else 0.0,
        "ist_eur":      float(comp["IST_EUR"].sum())      if "IST_EUR"        in comp.columns else 0.0,
        "soll_eur":     float(comp["SOLL_EUR"].sum())     if "SOLL_EUR"       in comp.columns else 0.0,
        "delta_eur":    float(comp["DELTA_EUR"].sum())    if "DELTA_EUR"      in comp.columns else 0.0,
        "n_ohne_soll":  len(subset),
        "eur_ohne_soll":float(subset["IST_EUR"].sum())   if ("IST_EUR" in subset.columns and len(subset) > 0) else 0.0,
        "mak_ohne_soll":float(subset["IST_MAK"].sum())   if ("IST_MAK" in subset.columns and len(subset) > 0) else 0.0,
        "cat_detail":   _cat_detail(comp),
    }


def _build_scenario(base_snap: pd.DataFrame, excl_config: dict, mod) -> pd.DataFrame:
    """Wende Exklusionen an und baue comp_df."""
    snap = apply_exclusions(base_snap.copy(), excl_config)
    if "Soll_Cost_Year" not in snap.columns:
        snap["Soll_Cost_Year"] = snap.apply(_calc_soll, axis=1)
    return mod.build_compact_compensation_planlevel_df(snap)


def _fmt_diff(a: float, b: float, pct: bool = True) -> tuple[str, str]:
    d = b - a
    d_str = f"{d:>+,.0f}"
    p_str = f"{d / a * 100:>+.1f}%" if (pct and a != 0) else "   n/a"
    return d_str, p_str


# --- Hauptprogramm ------------------------------------------------------------

def main() -> int:
    print(SEP)
    print("  ANALYSE: Wirkung 'Jobfamily Validierung' auf IST-ohne-Plan-SOLL")
    print(SEP)
    print("  S0 = Keine Exklusionen (Rohdata-Baseline)")
    print("  SA = Alle Standard-Exklusionen OHNE Jobfamily Validierung")
    print("  SB = Alle Standard-Exklusionen MIT Jobfamily Validierung (Dashboard-Standard)")

    # --- 1. Basisdaten laden --------------------------------------------------
    print(f"\n{SEP2}\n  DATENLADEN\n{SEP2}")
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

    # --- 2. Drei Szenarien berechnen ------------------------------------------
    print(f"\n{SEP2}\n  SZENARIEN BERECHNEN\n{SEP2}")
    results: dict[str, dict] = {}
    for label, excl_config in [("S0", _EXCL_NONE), ("SA", _EXCL_SA), ("SB", _EXCL_SB)]:
        print(f"  Berechne {label} ...")
        comp = _build_scenario(base_snap, excl_config, mod)
        results[label] = _summary(comp, label)
        n_excl = int(excl_config.get("ruhend_bv", False))  # just for display
        print(f"    -> comp_df: {results[label]['zeilen']} Zeilen  |  "
              f"IST_Köpfe: {results[label]['ist_koepfe']}  |  "
              f"IST-ohne-SOLL: {results[label]['n_ohne_soll']} Zeilen")

    s0, sa, sb = results["S0"], results["SA"], results["SB"]

    # --- 3. Gesamtvergleich ---------------------------------------------------
    print(f"\n{SEP}")
    print("  ABSCHNITT 1: GESAMTVERGLEICH IST/SOLL/DELTA")
    print(SEP)

    col_w = 16
    h = (f"{'Kennzahl':<33} {'S0 (kein Excl)':>{col_w}} {'SA (ohne JFV)':>{col_w}} "
         f"{'SB (mit JFV)':>{col_w}} {'SA→SB Diff':>{col_w}} {'SA→SB %':>9}")
    print(h)
    print("-" * len(h))

    def row(name: str, key: str, fmt: str = ",.0f") -> None:
        v0 = s0[key]; va = sa[key]; vb = sb[key]
        d, p = _fmt_diff(float(va), float(vb))
        s_v0 = f"{float(v0):{fmt}}".rjust(col_w)
        s_va = f"{float(va):{fmt}}".rjust(col_w)
        s_vb = f"{float(vb):{fmt}}".rjust(col_w)
        print(f"  {name:<31} {s_v0} {s_va} {s_vb} {d:>{col_w}} {p:>9}")

    row("Zeilen (comp_df)",        "zeilen",      ",.0f")
    row("IST_Köpfe",               "ist_koepfe",  ",.0f")
    row("SOLL_Planstellen",        "soll_planst", ",.0f")
    row("IST_MAK",                 "ist_mak",     ",.3f")
    row("SOLL_MAK",                "soll_mak",    ",.3f")
    row("DELTA_MAK",               "delta_mak",   ",.3f")
    row("IST_EUR",                 "ist_eur",     ",.2f")
    row("SOLL_EUR",                "soll_eur",    ",.2f")
    row("DELTA_EUR",               "delta_eur",   ",.2f")
    row("IST-ohne-SOLL Zeilen",    "n_ohne_soll", ",.0f")
    row("IST-ohne-SOLL EUR",       "eur_ohne_soll",",.2f")

    # --- 4. Kategorie-Vergleich -----------------------------------------------
    print(f"\n{SEP}")
    print("  ABSCHNITT 2: IST-OHNE-PLAN-SOLL — KATEGORIEVERGLEICH SA vs. SB")
    print(SEP)

    h2 = (f"{'Kategorie':<47} {'SA Zeilen':>10} {'SB Zeilen':>10} "
          f"{'SA IST_EUR':>14} {'SB IST_EUR':>14} {'Reduk.':>8}")
    print(h2)
    print("-" * len(h2))
    for cat in CAT_ORDER:
        ca = sa["cat_detail"].get(cat, {"n": 0, "eur": 0.0})
        cb = sb["cat_detail"].get(cat, {"n": 0, "eur": 0.0})
        n_a, e_a = ca["n"], ca["eur"]
        n_b, e_b = cb["n"], cb["eur"]
        red = (e_a - e_b) / e_a * 100 if e_a > 0 else 0.0
        delta_n = n_b - n_a
        print(f"  {cat:<45} {n_a:>10,d} {n_b:>10,d} "
              f"{e_a:>14,.0f} {e_b:>14,.0f} {red:>7.1f}%")

    # --- 5. Welche JFV-Planstellen schneiden IST-ohne-SOLL? ------------------
    print(f"\n{SEP}")
    print("  ABSCHNITT 3: JFV-PLANSTELLEN MIT SOLL_FTE=0 UND IST-KOSTEN")
    print(SEP)
    print(f"  JFV enthält {len(JOBFAMILY_VALIDATION_SPECIAL_PLAN_IDS)} spezifische Planstellennr.")

    if "Planstellennr" in base_snap.columns:
        pl_norm = (base_snap["Planstellennr"]
                   .fillna("").astype(str).str.strip()
                   .str.replace(r"\.0$", "", regex=True))
        is_jfv  = pl_norm.isin(JOBFAMILY_VALIDATION_SPECIAL_PLAN_IDS)
        soll_0  = pd.to_numeric(
            base_snap.get("Soll_FTE", pd.Series(dtype=float)), errors="coerce"
        ).fillna(0).eq(0)
        besetzt = base_snap.get("Is_Vacant", pd.Series(False, index=base_snap.index)) == False
        eur_col = next(
            (c for c in ["EUR_Reporting", "Total_Cost_Year"] if c in base_snap.columns), None
        )
        eur_pos = (
            pd.to_numeric(base_snap[eur_col], errors="coerce").fillna(0).gt(0)
            if eur_col else pd.Series(False, index=base_snap.index)
        )

        n_jfv_snap    = int(is_jfv.sum())
        n_jfv_soll0   = int((is_jfv & soll_0 & besetzt).sum())
        n_jfv_mit_eur = int((is_jfv & soll_0 & besetzt & eur_pos).sum())

        print(f"\n  JFV-Planstellen im Snapshot gesamt:       {n_jfv_snap:>6}")
        print(f"  Davon besetzt + Soll_FTE=0:               {n_jfv_soll0:>6}")
        print(f"  Davon besetzt + Soll_FTE=0 + {eur_col or '?'}>0: {n_jfv_mit_eur:>6}")

        if n_jfv_mit_eur > 0:
            jfv_df = base_snap.loc[is_jfv & soll_0 & besetzt & eur_pos].copy()
            eur_sum = float(
                pd.to_numeric(jfv_df[eur_col], errors="coerce").fillna(0).sum()
            ) if eur_col else 0.0
            print(f"  IST-EUR dieser Zeilen (aus {eur_col}): {eur_sum:>14,.2f}")

            # PersNr distinct
            if "PersNr" in jfv_df.columns:
                n_pers = jfv_df["PersNr"].dropna().nunique()
                print(f"  Eindeutige Personen:                     {n_pers:>6}")

            if "Planstelle" in jfv_df.columns:
                print(f"\n  Top Planstellenbezeichnungen:")
                for name, cnt in jfv_df["Planstelle"].fillna("(leer)").value_counts().head(8).items():
                    print(f"    {name:<40} {cnt:>3}")

            if "Organisationseinheit" in jfv_df.columns:
                print(f"\n  Top Organisationseinheiten:")
                for name, cnt in jfv_df["Organisationseinheit"].fillna("(leer)").value_counts().head(5).items():
                    print(f"    {name:<40} {cnt:>3}")

            # Planstellennr im Schnitt
            overlap_ids = set(pl_norm[is_jfv & soll_0 & besetzt & eur_pos].unique())
            print(f"\n  Betroffene Planstellennr: {overlap_ids}")
        else:
            print("\n  Keine JFV-Planstellen mit Soll_FTE=0 und IST_EUR>0 gefunden.")
            print("  -> Jobfamily Validierung hat KEINEN direkten Einfluss auf IST-ohne-SOLL.")
    else:
        print("  'Planstellennr' Spalte nicht im Snapshot vorhanden.")

    # --- 6. Kernfrage & Entscheidungsvorlage ---------------------------------
    print(f"\n{SEP}")
    print("  ABSCHNITT 4: ENTSCHEIDUNGSVORLAGE")
    print(SEP)

    n_sa_ohne = sa["n_ohne_soll"]
    n_sb_ohne = sb["n_ohne_soll"]
    e_sa_ohne = sa["eur_ohne_soll"]
    e_sb_ohne = sb["eur_ohne_soll"]
    e_red_pct = (e_sa_ohne - e_sb_ohne) / e_sa_ohne * 100 if e_sa_ohne > 0 else 0.0

    n_reg_sa  = sa["cat_detail"].get("Reguläre aktive Stelle ohne Soll_FTE", {}).get("n", 0)
    n_reg_sb  = sb["cat_detail"].get("Reguläre aktive Stelle ohne Soll_FTE", {}).get("n", 0)
    e_reg_sa  = sa["cat_detail"].get("Reguläre aktive Stelle ohne Soll_FTE", {}).get("eur", 0.0)
    e_reg_sb  = sb["cat_detail"].get("Reguläre aktive Stelle ohne Soll_FTE", {}).get("eur", 0.0)
    n_pool_sa = sa["cat_detail"].get("Pool- / Sammelplanstelle", {}).get("n", 0)
    n_pool_sb = sb["cat_detail"].get("Pool- / Sammelplanstelle", {}).get("n", 0)
    e_pool_sa = sa["cat_detail"].get("Pool- / Sammelplanstelle", {}).get("eur", 0.0)
    e_pool_sb = sb["cat_detail"].get("Pool- / Sammelplanstelle", {}).get("eur", 0.0)

    print(f"""
  Exklusionsgruppe geprüft:
    Jobfamily Validierung (82 Planstellennr)

  Filterwirkung (SA ohne JFV → SB mit JFV):
    Zeilen IST-ohne-SOLL vorher (SA):  {n_sa_ohne:>6}
    Zeilen IST-ohne-SOLL nachher (SB): {n_sb_ohne:>6}
    IST_EUR ohne SOLL vorher (SA):     {e_sa_ohne:>14,.2f}
    IST_EUR ohne SOLL nachher (SB):    {e_sb_ohne:>14,.2f}
    Reduktion durch JFV:               {e_sa_ohne - e_sb_ohne:>14,.2f}  ({e_red_pct:.1f}%)

  Reguläre aktive Stellen ohne Soll_FTE:
    vorher (SA):  {n_reg_sa} Zeilen | {e_reg_sa:,.2f} EUR
    nachher (SB): {n_reg_sb} Zeilen | {e_reg_sb:,.2f} EUR
    Bewertung: {"Weiterhin kritisch" if n_reg_sb > 20 else "Weitgehend beseitigt" if n_reg_sb == 0 else "Teilweise reduziert"}

  Pool-/Sammelplanstellen ohne Plan-SOLL:
    vorher (SA):  {n_pool_sa} Zeilen | {e_pool_sa:,.2f} EUR
    nachher (SB): {n_pool_sb} Zeilen | {e_pool_sb:,.2f} EUR
    Bewertung: {"Weiterhin vorhanden" if n_pool_sb > 0 else "Vollständig ausgeschlossen"}
""")

    # Gesamtbewertung
    if e_red_pct < 5.0:
        bewertung = "Ja, weiterhin kritisch (Reduktion durch JFV < 5%)"
        empfehlung = "Warnbox immer anzeigen, auch bei JFV-Exklusion aktiv"
    elif e_red_pct < 30.0:
        bewertung = "Teilweise relevant (Reduktion durch JFV < 30%)"
        empfehlung = "Warnbox anzeigen; Hinweis 'Exklusionsgruppe reduziert Fälle' ergänzen"
    else:
        bewertung = "Weitgehend weggefiltert (Reduktion ≥ 30%)"
        empfehlung = "Warnbox nur bei relevanten Restfällen (Threshold-basiert)"

    print(f"  Fazit — Fehler fällt noch ins Gewicht: {bewertung}")
    print(f"\n  Empfehlung UI-Warnung:")
    print(f"    {empfehlung}")

    print(f"\n{SEP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
