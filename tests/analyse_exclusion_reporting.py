"""
analyse_exclusion_reporting.py
==============================
Prüft, ob Exklusionen sauber auf MAK_Reporting und EUR_Reporting wirken.

Drei Szenarien:
  S0 = Keine Exklusionen
  SA = Standard-Exklusionen ohne Jobfamily Validierung
  SB = Standard-Exklusionen mit Jobfamily Validierung (Dashboard-Standard)

Schlüsselfrage: Warum bleibt IST_EUR trotz fallender IST_Köpfe unverändert?

Run:
    py -X utf8 KSK_Layout/tests/analyse_exclusion_reporting.py
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

SEP  = "=" * 72
SEP2 = "-" * 52

# ── Exklusions-Konfigurationen ────────────────────────────────────────────────

_EXCL_NONE = {
    "vorstand": False, "ruhend_bv": False,
    "planstellen_follow_person": False,
    "org_units": [], "special_groups": [],
}
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

_SCENARIOS = [("S0", _EXCL_NONE), ("SA", _EXCL_SA), ("SB", _EXCL_SB)]


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

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


def _snap_stats(df: pd.DataFrame, label: str) -> dict:
    """Aggregiert alle relevanten Kennzahlen auf Snapshot-Ebene."""
    is_vac = df.get("Is_Vacant", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    has_pers = df.get("PersNr", pd.Series(dtype=object)).notna()
    return {
        "label":          label,
        "zeilen":         len(df),
        "is_vacant_true": int(is_vac.sum()),
        "is_vacant_false":int((~is_vac).sum()),
        "personen_uniq":  int(df["PersNr"].dropna().nunique()) if "PersNr" in df.columns else 0,
        "planst_uniq":    int(df["Planstellennr"].dropna().nunique()) if "Planstellennr" in df.columns else 0,
        # IST-Spalten
        "mak":            float(_n(df, "MAK").sum()),
        "mak_calc":       float(_n(df, "MAK_Calculated").sum()),
        "mak_rep":        float(_n(df, "MAK_Reporting").sum()),
        "fte_assigned":   float(_n(df, "FTE_assigned").sum()),
        "fte_person":     float(_n(df, "FTE_person").sum()),
        "eur_reporting":  float(_n(df, "EUR_Reporting").sum()),
        "total_cost":     float(_n(df, "Total_Cost_Year").sum()),
        # SOLL-Spalten
        "soll_fte":       float(_n(df, "Soll_FTE").sum()),
        "soll_cost":      float(_n(df, "Soll_Cost_Year").sum()),
    }


def _comp_stats(comp: pd.DataFrame, label: str) -> dict:
    """Aggregiert Kennzahlen auf comp_df-Ebene."""
    return {
        "label":        label,
        "zeilen":       len(comp),
        "ist_koepfe":   int(comp["IST_Kopf"].sum())       if "IST_Kopf"       in comp.columns else 0,
        "soll_planst":  int(comp["SOLL_Planstellen"].sum()) if "SOLL_Planstellen" in comp.columns else 0,
        "ist_mak":      float(comp["IST_MAK"].sum())      if "IST_MAK"        in comp.columns else 0.0,
        "soll_mak":     float(comp["SOLL_MAK"].sum())     if "SOLL_MAK"       in comp.columns else 0.0,
        "delta_mak":    float(comp["DELTA_MAK"].sum())    if "DELTA_MAK"      in comp.columns else 0.0,
        "ist_eur":      float(comp["IST_EUR"].sum())      if "IST_EUR"        in comp.columns else 0.0,
        "soll_eur":     float(comp["SOLL_EUR"].sum())     if "SOLL_EUR"       in comp.columns else 0.0,
        "delta_eur":    float(comp["DELTA_EUR"].sum())    if "DELTA_EUR"      in comp.columns else 0.0,
    }


def _build(base_snap: pd.DataFrame, excl: dict, mod) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Gibt (snap_nach_excl, comp_df) zurück."""
    snap = apply_exclusions(base_snap.copy(), excl)
    if "Soll_Cost_Year" not in snap.columns:
        snap["Soll_Cost_Year"] = snap.apply(_calc_soll, axis=1)
    comp = mod.build_compact_compensation_planlevel_df(snap)
    return snap, comp


def _print_diff_table(
    rows: list[tuple[str, str, str]],   # (label, key, fmt)
    dicts: dict[str, dict],
    keys: list[str] = ("S0", "SA", "SB"),
    col_w: int = 16,
) -> None:
    k0, k1, k2 = keys
    h = (f"  {'Kennzahl':<32} {k0:>{col_w}} {k1:>{col_w}} {k2:>{col_w}}"
         f" {'S0→SA Diff':>{col_w}} {'SA→SB Diff':>{col_w}}")
    print(h)
    print("  " + "-" * (len(h) - 2))
    for label, key, fmt in rows:
        v0 = float(dicts[k0].get(key, 0))
        v1 = float(dicts[k1].get(key, 0))
        v2 = float(dicts[k2].get(key, 0))
        s0 = f"{v0:{fmt}}".rjust(col_w)
        s1 = f"{v1:{fmt}}".rjust(col_w)
        s2 = f"{v2:{fmt}}".rjust(col_w)
        d01 = f"{v1 - v0:+,.0f}".rjust(col_w) if "." not in fmt else f"{v1-v0:+,.2f}".rjust(col_w)
        d12 = f"{v2 - v1:+,.0f}".rjust(col_w) if "." not in fmt else f"{v2-v1:+,.2f}".rjust(col_w)
        print(f"  {label:<32} {s0} {s1} {s2} {d01} {d12}")


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main() -> int:
    print(SEP)
    print("  ANALYSE: Exklusionswirkung auf MAK_Reporting / EUR_Reporting")
    print(SEP)

    # ── Basisdaten laden ─────────────────────────────────────────────────────
    print(f"\n{SEP2}  DATENLADEN  {SEP2}")
    raw      = load_original_data()
    stichtag = get_current_stichtag()
    # Wichtig: apply_person_mak_allocation NACH combine_to_snapshot,
    # BEVOR apply_exclusions — um Reihenfolge zu testen.
    raw_snap = combine_to_snapshot(
        mitarbeiter=raw["mitarbeiter"],
        planstellen=raw["planstellen"],
        atz=raw["atz"],
        ausbildung=raw["ausbildung"],
        stichtag=stichtag,
    )
    print(f"combine_to_snapshot(): {len(raw_snap)} Zeilen")
    print(f"  MAK_Reporting vorhanden: {'MAK_Reporting' in raw_snap.columns}")
    print(f"  EUR_Reporting vorhanden: {'EUR_Reporting' in raw_snap.columns}")

    # apply_person_mak_allocation VOR apply_exclusions (Produktions-Reihenfolge)
    if "MAK_Reporting" not in raw_snap.columns:
        raw_snap = apply_person_mak_allocation(raw_snap)
    print(f"apply_person_mak_allocation(): MAK_Reporting jetzt vorhanden: {'MAK_Reporting' in raw_snap.columns}")
    print(f"  Reihenfolge: apply_exclusions kommt DANACH")

    mod = load_compact_page_module()

    # ── Szenarien aufbauen ───────────────────────────────────────────────────
    print(f"\n{SEP2}  SZENARIEN BERECHNEN  {SEP2}")
    snaps: dict[str, pd.DataFrame] = {}
    comps: dict[str, pd.DataFrame] = {}
    snap_stats: dict[str, dict] = {}
    comp_stats_d: dict[str, dict] = {}

    for label, excl in _SCENARIOS:
        print(f"  Berechne {label} ...")
        snap, comp = _build(raw_snap, excl, mod)
        snaps[label] = snap
        comps[label] = comp
        snap_stats[label] = _snap_stats(snap, label)
        comp_stats_d[label] = _comp_stats(comp, label)

    # ── Abschnitt 1: Pipeline-Reihenfolge ────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 1: PIPELINE-REIHENFOLGE UND SPALTEN-NULLUNG")
    print(SEP)

    print("""
  Reihenfolge im Dashboard (load_and_prepare_data):
    1. combine_to_snapshot()            → Planstellen + Mitarbeiter joinen
    2. apply_person_mak_allocation()    → MAK_Reporting, EUR_Reporting erzeugen
    3. apply_exclusions(snap, excl)     → Exklusion anwenden
    4. prepare_compact_data()           → Aufbereitung für Compact-Page
    5. apply_filters()                  → Sidebar-Filter
    6. build_compact_compensation_planlevel_df()

  apply_exclusions() nullt folgende Spalten (ist_metrics):""")

    ist_metrics_zeroed = [
        "MAK", "mak", "MAK_Calculated", "BsGrd",
        "FTE_person", "FTE_assigned", "Total_Cost_Year"
    ]
    ist_metrics_preserved = [
        "MAK_Reporting", "EUR_Reporting",
        "Soll_FTE", "Sollarbeitszeit", "Soll_Cost_Year",
        "Planstellennr", "Planstelle", "Organisationseinheit",
    ]
    person_fields_nulled = [
        "Personalnummer", "PersNr", "Personalnachname", "Personalvorname",
        "Name", "GebDatum", "Eintritt", "Austritt", "Ist_Azubi"
    ]

    print(f"    Genullt (0.0):    {', '.join(ist_metrics_zeroed)}")
    print(f"    NA gesetzt:       {', '.join(person_fields_nulled)}")
    print(f"    NICHT berührt:    {', '.join(ist_metrics_preserved)}")
    print()
    print("  KRITISCH: MAK_Reporting und EUR_Reporting werden NICHT genullt!")
    print("  Sie entstehen in apply_person_mak_allocation (Schritt 2),")
    print("  BEVOR apply_exclusions (Schritt 3) läuft → bleiben unberührt.")

    # ── Abschnitt 2: Snapshot-Vergleich ──────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 2: SNAPSHOT-KENNZAHLEN NACH APPLY_EXCLUSIONS")
    print(SEP)

    rows_snap = [
        ("Zeilen gesamt",        "zeilen",         ",.0f"),
        ("Is_Vacant True",       "is_vacant_true",  ",.0f"),
        ("Is_Vacant False",      "is_vacant_false", ",.0f"),
        ("Personen eindeutig",   "personen_uniq",   ",.0f"),
        ("Planstellen eindeutig","planst_uniq",     ",.0f"),
        ("──── IST-Spalten ────", "",               ""),
        ("MAK (genullt)",        "mak",            ",.3f"),
        ("MAK_Calculated (genullt)", "mak_calc",   ",.3f"),
        ("MAK_Reporting (NICHT genullt)", "mak_rep",",.3f"),
        ("FTE_assigned (genullt)","fte_assigned",  ",.3f"),
        ("EUR_Reporting (NICHT genullt)","eur_reporting",",.2f"),
        ("Total_Cost_Year (genullt)", "total_cost",",.2f"),
        ("──── SOLL-Spalten ────","",              ""),
        ("Soll_FTE",             "soll_fte",       ",.3f"),
        ("Soll_Cost_Year",       "soll_cost",      ",.2f"),
    ]

    col_w = 18
    h = (f"  {'Kennzahl':<36} {'S0':>{col_w}} {'SA':>{col_w}} {'SB':>{col_w}}"
         f" {'S0→SA Diff':>{col_w}} {'SA→SB Diff':>{col_w}}")
    print(h)
    print("  " + "-" * (len(h) - 2))

    for label, key, fmt in rows_snap:
        if key == "":
            print(f"\n  {label}")
            continue
        v0 = float(snap_stats["S0"].get(key, 0))
        v1 = float(snap_stats["SA"].get(key, 0))
        v2 = float(snap_stats["SB"].get(key, 0))
        s0 = f"{v0:{fmt}}".rjust(col_w)
        s1 = f"{v1:{fmt}}".rjust(col_w)
        s2 = f"{v2:{fmt}}".rjust(col_w)
        d01 = f"{v1-v0:+,.2f}".rjust(col_w)
        d12 = f"{v2-v1:+,.2f}".rjust(col_w)
        print(f"  {label:<36} {s0} {s1} {s2} {d01} {d12}")

    # ── Abschnitt 3: Comp-DF-Vergleich ───────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 3: COMPENSATION-KENNZAHLEN NACH build_compact...()")
    print(SEP)

    rows_comp = [
        ("IST_Köpfe",            "ist_koepfe",  ",.0f"),
        ("SOLL_Planstellen",     "soll_planst", ",.0f"),
        ("IST_MAK",              "ist_mak",     ",.3f"),
        ("SOLL_MAK",             "soll_mak",    ",.3f"),
        ("DELTA_MAK",            "delta_mak",   ",.3f"),
        ("IST_EUR",              "ist_eur",     ",.2f"),
        ("SOLL_EUR",             "soll_eur",    ",.2f"),
        ("DELTA_EUR",            "delta_eur",   ",.2f"),
    ]
    _print_diff_table(rows_comp, comp_stats_d, col_w=18)

    # ── Abschnitt 4: Ausgeschlossene Zeilen mit Restwerten ───────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 4: AUSGESCHLOSSENE ZEILEN MIT REST-MAK/EUR-REPORTING")
    print(SEP)
    print("  Basis: SA-Snapshot (Standard-Exklusionen ohne JFV)\n")

    snap_sa = snaps["SA"]
    is_vac_sa = snap_sa.get("Is_Vacant", pd.Series(False, index=snap_sa.index)).fillna(False).astype(bool)
    mak_rep   = _n(snap_sa, "MAK_Reporting")
    eur_rep   = _n(snap_sa, "EUR_Reporting")
    mak_calc  = _n(snap_sa, "MAK_Calculated")
    fte_asgn  = _n(snap_sa, "FTE_assigned")

    # Zeilen: Is_Vacant == True UND (MAK_Reporting > 0 OR EUR_Reporting > 0)
    excl_with_rest = is_vac_sa & ((mak_rep > 0) | (eur_rep > 0))
    excl_no_rest   = is_vac_sa & (mak_rep == 0) & (eur_rep == 0)

    n_excl_total   = int(is_vac_sa.sum())
    n_excl_rest    = int(excl_with_rest.sum())
    n_excl_no_rest = int(excl_no_rest.sum())

    print(f"  Ausgeschlossene Zeilen (Is_Vacant=True) gesamt:     {n_excl_total:>6}")
    print(f"  Davon mit MAK_Reporting>0 ODER EUR_Reporting>0:     {n_excl_rest:>6}")
    print(f"  Davon ohne Restwerte (MAK_Rep=0 UND EUR_Rep=0):     {n_excl_no_rest:>6}")

    if n_excl_rest > 0:
        rest_df = snap_sa.loc[excl_with_rest].copy()
        n_pers  = int(rest_df["PersNr"].dropna().nunique()) if "PersNr" in rest_df.columns else "?"

        sum_mak_rep = float(_n(rest_df, "MAK_Reporting").sum())
        sum_eur_rep = float(_n(rest_df, "EUR_Reporting").sum())
        sum_mak_cal = float(_n(rest_df, "MAK_Calculated").sum())
        sum_fte_asg = float(_n(rest_df, "FTE_assigned").sum())

        print(f"\n  Restwerte auf ausgeschlossenen Zeilen:")
        print(f"    Eindeutige Personen (PersNr):  {n_pers:>6}")
        print(f"    Summe MAK_Reporting:           {sum_mak_rep:>12,.3f}")
        print(f"    Summe EUR_Reporting:           {sum_eur_rep:>14,.2f}")
        print(f"    Summe MAK_Calculated:          {sum_mak_cal:>12,.3f}")
        print(f"    Summe FTE_assigned:            {sum_fte_asg:>12,.3f}")

        # Anteil am Gesamt
        tot_eur = float(_n(snap_sa, "EUR_Reporting").sum())
        tot_mak = float(_n(snap_sa, "MAK_Reporting").sum())
        pct_eur = sum_eur_rep / tot_eur * 100 if tot_eur > 0 else 0
        pct_mak = sum_mak_rep / tot_mak * 100 if tot_mak > 0 else 0
        print(f"    Anteil EUR_Reporting gesamt:  {pct_eur:>7.1f}%")
        print(f"    Anteil MAK_Reporting gesamt:  {pct_mak:>7.1f}%")

        # Welche OE/Exklusionsgruppe?
        print(f"\n  Exklusionsgrund (OE-Kürzel Verteilung):")
        if "Kürzel OrgEinheit" in rest_df.columns:
            oe_vc = rest_df["Kürzel OrgEinheit"].fillna("(leer)").astype(str).str.strip()
            oe_vc = oe_vc.str.replace(r"\.0$", "", regex=True)
            for oe, cnt in oe_vc.value_counts().head(12).items():
                print(f"    OE {oe:<12}: {cnt:>4} Zeilen")

        print(f"\n  Top Planstellenbezeichnungen:")
        if "Planstelle" in rest_df.columns:
            for pl, cnt in rest_df["Planstelle"].fillna("(leer)").value_counts().head(8).items():
                sub = rest_df[rest_df["Planstelle"] == pl]
                eur_s = float(_n(sub, "EUR_Reporting").sum())
                print(f"    {pl:<42} {cnt:>4} Zeilen  EUR {eur_s:>12,.0f}")

        # Warum ist EUR_Reporting hier nicht 0?
        # Prüfe: welche Spalte nutzt build_compact_compensation_planlevel_df für IST_EUR?
        print(f"\n  Status-Verteilung (ausgeschlossene Zeilen mit Restwerten):")
        if "Status kundenindividuell" in rest_df.columns:
            for st_val, cnt in rest_df["Status kundenindividuell"].fillna("(leer)").value_counts().head(6).items():
                print(f"    {st_val:<42} {cnt:>4}")

        # Beispielzeilen
        print(f"\n  Beispielzeilen (erste 5):")
        show_cols = [c for c in [
            "Planstellennr", "Planstelle", "PersNr", "Is_Vacant",
            "Kürzel OrgEinheit", "Status kundenindividuell",
            "MAK_Reporting", "EUR_Reporting", "MAK_Calculated", "MAK", "Total_Cost_Year"
        ] if c in rest_df.columns]
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 200)
        pd.set_option("display.max_colwidth", 30)
        print(rest_df[show_cols].head(5).to_string(index=False))

    # ── Abschnitt 5: Spiegelung in comp_df ────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 5: COMP_DF — IST_EUR AUS WELCHER SPALTE?")
    print(SEP)

    # Prüfe in SA-Snapshot: welche Quelle nutzt IST_EUR?
    # IST_EUR = _numeric_compensation_series(df["EUR_Reporting"], kind="currency")
    snap_sa_prep = snaps["SA"]
    eur_col = next(
        (c for c in ["EUR_Reporting", "Total_Cost_Year"] if c in snap_sa_prep.columns), None
    )
    mak_col = next(
        (c for c in ["MAK_Reporting", "MAK_Calculated", "MAK", "mak"] if c in snap_sa_prep.columns), None
    )
    print(f"  IST_EUR Quellespalte: {eur_col!r}")
    print(f"  IST_MAK Quellespalte: {mak_col!r}")
    print()

    # Vergleich: EUR_Reporting auf Is_Vacant=True vs Is_Vacant=False
    is_vac = snap_sa_prep.get("Is_Vacant", pd.Series(False, index=snap_sa_prep.index)).fillna(False).astype(bool)
    if eur_col:
        eur_vac_true  = float(_n(snap_sa_prep, eur_col)[is_vac].sum())
        eur_vac_false = float(_n(snap_sa_prep, eur_col)[~is_vac].sum())
        eur_total     = float(_n(snap_sa_prep, eur_col).sum())
        print(f"  {eur_col} Summe gesamt:          {eur_total:>18,.2f}")
        print(f"  {eur_col} bei Is_Vacant=False:   {eur_vac_false:>18,.2f}  ({eur_vac_false/eur_total*100:.1f}%)")
        print(f"  {eur_col} bei Is_Vacant=True:    {eur_vac_true:>18,.2f}  ({eur_vac_true/eur_total*100:.1f}%)")
        print()
        print(f"  IST_EUR in comp_df (SA):           {comp_stats_d['SA']['ist_eur']:>18,.2f}")
        print(f"  -> IST_EUR = gesamt EUR_Reporting (Is_Vacant ignoriert!)")
    if mak_col:
        mak_vac_true  = float(_n(snap_sa_prep, mak_col)[is_vac].sum())
        mak_vac_false = float(_n(snap_sa_prep, mak_col)[~is_vac].sum())
        mak_total     = float(_n(snap_sa_prep, mak_col).sum())
        print(f"\n  {mak_col} Summe gesamt:        {mak_total:>14,.3f}")
        print(f"  {mak_col} bei Is_Vacant=False: {mak_vac_false:>14,.3f}  ({mak_vac_false/mak_total*100:.1f}%)")
        print(f"  {mak_col} bei Is_Vacant=True:  {mak_vac_true:>14,.3f}  ({mak_vac_true/mak_total*100:.1f}%)")

    # ── Abschnitt 6: Was würde passieren wenn EUR_Reporting genullt würde? ────
    print(f"\n{SEP}")
    print("  ABSCHNITT 6: HYPOTHETISCHER VERGLEICH — WENN EXKLUSIONEN GREIFEN")
    print(SEP)

    snap_sa_fixed = snaps["SA"].copy()
    is_vac_fix = snap_sa_fixed.get("Is_Vacant", pd.Series(False, index=snap_sa_fixed.index)).fillna(False).astype(bool)
    if "EUR_Reporting" in snap_sa_fixed.columns:
        snap_sa_fixed.loc[is_vac_fix, "EUR_Reporting"] = 0.0
    if "MAK_Reporting" in snap_sa_fixed.columns:
        snap_sa_fixed.loc[is_vac_fix, "MAK_Reporting"] = 0.0

    comp_sa_fixed = mod.build_compact_compensation_planlevel_df(snap_sa_fixed)
    cs_fixed = _comp_stats(comp_sa_fixed, "SA_FIXED")

    print(f"\n  Vergleich SA (jetzt) vs. SA_FIXED (EUR_Reporting/MAK_Reporting für")
    print(f"  Is_Vacant=True auf 0 gesetzt, ohne sonstige Codeänderung):\n")

    cols_h = f"  {'Kennzahl':<28} {'SA (aktuell)':>18} {'SA_FIXED':>18} {'Differenz':>18}"
    print(cols_h)
    print("  " + "-" * (len(cols_h) - 2))
    for lbl, key, fmt in [
        ("IST_Köpfe",   "ist_koepfe", ",.0f"),
        ("IST_MAK",     "ist_mak",    ",.3f"),
        ("SOLL_MAK",    "soll_mak",   ",.3f"),
        ("DELTA_MAK",   "delta_mak",  ",.3f"),
        ("IST_EUR",     "ist_eur",    ",.2f"),
        ("SOLL_EUR",    "soll_eur",   ",.2f"),
        ("DELTA_EUR",   "delta_eur",  ",.2f"),
    ]:
        va = float(comp_stats_d["SA"].get(key, 0))
        vf = float(cs_fixed.get(key, 0))
        sa_ = f"{va:{fmt}}".rjust(18)
        sf_ = f"{vf:{fmt}}".rjust(18)
        d_  = f"{vf-va:+,.2f}".rjust(18)
        print(f"  {lbl:<28} {sa_} {sf_} {d_}")

    # ── Abschnitt 7: Entscheidungsvorlage ────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 7: ENTSCHEIDUNGSVORLAGE")
    print(SEP)

    snap_sa   = snaps["SA"]
    is_vac_sa = snap_sa.get("Is_Vacant", pd.Series(False, index=snap_sa.index)).fillna(False).astype(bool)
    mak_rep_s = _n(snap_sa, "MAK_Reporting")
    eur_rep_s = _n(snap_sa, "EUR_Reporting")

    excl_mak_rest = float(mak_rep_s[is_vac_sa].sum())
    excl_eur_rest = float(eur_rep_s[is_vac_sa].sum())
    excl_mak_n    = int((mak_rep_s[is_vac_sa] > 0).sum())
    excl_eur_n    = int((eur_rep_s[is_vac_sa] > 0).sum())

    # Top Planstellen mit EUR-Restwert
    rest_pls = ""
    if "Planstelle" in snap_sa.columns and excl_eur_n > 0:
        top5 = (snap_sa.loc[is_vac_sa & (eur_rep_s > 0), "Planstelle"]
                .fillna("(leer)").value_counts().head(5))
        rest_pls = ", ".join(f"{k} ({v})" for k, v in top5.items())

    eur_total_s = float(_n(snap_sa, "EUR_Reporting").sum())
    mak_total_s = float(_n(snap_sa, "MAK_Reporting").sum())
    pct_eur_rest = excl_eur_rest / eur_total_s * 100 if eur_total_s > 0 else 0.0
    pct_mak_rest = excl_mak_rest / mak_total_s * 100 if mak_total_s > 0 else 0.0

    print(f"""
  Befund:
    Exklusion wirkt auf Köpfe (Is_Vacant=True):       Ja, vollständig
    Exklusion wirkt auf MAK (MAK, mak, MAK_Calculated): Ja, wird genullt
    Exklusion wirkt auf MAK_Reporting:                 Nein
    Exklusion wirkt auf EUR_Reporting:                 Nein
    Exklusion wirkt auf FTE_assigned:                  Ja, wird genullt
    Exklusion wirkt auf Total_Cost_Year:               Ja, wird genullt

  Impact (Snapshot SA = Standard-Exklusionen ohne JFV):
    Is_Vacant=True gesamt:                             {snap_stats["SA"]["is_vacant_true"]:>6}
    Davon mit MAK_Reporting > 0:                       {excl_mak_n:>6} Zeilen
    Davon mit EUR_Reporting > 0:                       {excl_eur_n:>6} Zeilen
    MAK_Reporting trotz Exklusion:                     {excl_mak_rest:>10,.3f}  ({pct_mak_rest:.1f}% des Gesamt)
    EUR_Reporting trotz Exklusion:                     {excl_eur_rest:>14,.2f}  ({pct_eur_rest:.1f}% des Gesamt)
    Betroffene Planstellen (Top 5):                    {rest_pls}

  Fachliche Bewertung:
    Ist es korrekt, dass ausgeschlossene Personen in IST_EUR bleiben?
    -> NEIN. Wenn eine Person/Planstelle aus dem Reporting ausgeschlossen ist,
       sollte ihre Vergütung in IST_EUR und IST_MAK nicht erscheinen.
       Aktuell: DELTA_EUR = IST_EUR (mit Exkl.) - SOLL_EUR enthält Kosten
       ausgeschlossener Mitarbeiter → Delta ist verzerrt.

  Fix-Empfehlung:
    Bevorzugte Variante: B (apply_person_mak_allocation respektiert Is_Vacant)
    Warum B:
      - MAK_Reporting und EUR_Reporting entstehen IN apply_person_mak_allocation.
      - Nach apply_exclusions sind Is_Vacant=True-Flags gesetzt.
      - Korrekteste Lösung: apply_exclusions VOR apply_person_mak_allocation
        stellen, ODER: apply_person_mak_allocation setzt MAK_Reporting=0
        für Is_Vacant=True Zeilen.
      - Variante A (nachträglich nullen in apply_exclusions) erfordert,
        dass MAK_Reporting/EUR_Reporting zu dem Zeitpunkt schon existieren.
        Falls die Reihenfolge alloc→excl ist, muss excl nochmal ran.
      - Variante C (Schutz in build_compact...) ist ein lokaler Workaround.
        Andere Dashboard-Seiten würden weiterhin falsche Werte zeigen.

    Minimaler Implementierungsplan für Variante B:
      1. In apply_person_mak_allocation() nach Berechnung:
         mask_vac = df["Is_Vacant"] == True (wenn vorhanden)
         df.loc[mask_vac, "MAK_Reporting"] = 0.0
         df.loc[mask_vac, "EUR_Reporting"] = 0.0  (falls hier gesetzt)
      ODER:
      1. Reihenfolge im Loader umkehren:
         apply_exclusions() → DANN apply_person_mak_allocation()
         Dann berechnet alloc gar nicht erst für Is_Vacant=True Zeilen.

    Risiko:
      - Prüfen ob Is_Vacant in combine_to_snapshot() bereits korrekt befüllt ist.
      - Prüfen ob Abgänge/Zugänge durch Reihenfolgeänderung beeinflusst werden.
      - Regressions-Tests für bestehende MAK-Werte nötig.

    Fix nötig: Ja
""")

    print(SEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
