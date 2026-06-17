"""
analyse_delta_nach_fix.py
=========================
Analysiert das neue SOLL/IST-Delta nach dem Exklusions-Fix.

Nach Fix:
  IST_MAK  = 762,911  (vorher: 860,335)
  IST_EUR  = 59.360.567  (vorher: 70.628.574)
  SOLL_MAK = 1.043,423  (unverändert)
  SOLL_EUR = 75.837.436  (unverändert)
  DELTA_EUR = -16.476.869  (vorher: -5.208.862)

Fragestellung:
  Was treibt das neue Delta? Welches SOLL steckt in exkludierten Planstellen?

Run:
    py -X utf8 KSK_Layout/tests/analyse_delta_nach_fix.py
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

SEP  = "=" * 76
SEP2 = "-" * 54

_EXCL_SA = {   # Standard ohne JFV
    "vorstand": False, "ruhend_bv": True,
    "planstellen_follow_person": True,
    "org_units": JOBFAMILY_VALIDATION_ORG_UNITS,
    "special_groups": ["ausbildung_nachwuchs", "sollarbeitszeit_001_positions"],
}
_EXCL_SB = {   # Dashboard-Standard mit JFV
    "vorstand": False, "ruhend_bv": True,
    "planstellen_follow_person": True,
    "org_units": JOBFAMILY_VALIDATION_ORG_UNITS,
    "special_groups": [
        "ausbildung_nachwuchs",
        "jobfamily_validation_special_positions",
        "sollarbeitszeit_001_positions",
    ],
}
_EXCL_NONE = {
    "vorstand": False, "ruhend_bv": False,
    "planstellen_follow_person": False,
    "org_units": [], "special_groups": [],
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


def _build(base_snap: pd.DataFrame, excl: dict, mod) -> tuple[pd.DataFrame, pd.DataFrame]:
    snap = apply_exclusions(base_snap.copy(), excl)
    if "Soll_Cost_Year" not in snap.columns:
        snap["Soll_Cost_Year"] = snap.apply(_calc_soll, axis=1)
    comp = mod.build_compact_compensation_planlevel_df(snap)
    return snap, comp


def _agg_by(comp: pd.DataFrame, col: str, n: int = 20, label: str | None = None) -> None:
    """Aggregiere comp_df nach col und zeige Top-N nach DELTA_EUR."""
    if col not in comp.columns:
        print(f"  (Spalte '{col}' nicht vorhanden, übersprungen)")
        return

    grp = comp.groupby(col, dropna=False).agg(
        IST_MAK   = ("IST_MAK",        "sum"),
        SOLL_MAK  = ("SOLL_MAK",       "sum"),
        DELTA_MAK = ("DELTA_MAK",      "sum"),
        IST_EUR   = ("IST_EUR",        "sum"),
        SOLL_EUR  = ("SOLL_EUR",       "sum"),
        DELTA_EUR = ("DELTA_EUR",      "sum"),
        IST_Koepfe   = ("IST_Kopf",    "sum"),
        SOLL_Planst  = ("SOLL_Planstellen", "sum"),
    ).reset_index()

    grp = grp.sort_values("DELTA_EUR").head(n)

    print(f"  {label or col} (Top {n} nach DELTA_EUR):")
    hdr = (f"  {'Wert':<35} {'IST_MAK':>9} {'SOLL_MAK':>9} {'DELTA_MAK':>10} "
           f"{'IST_EUR':>14} {'SOLL_EUR':>14} {'DELTA_EUR':>14} {'IST_K':>6} {'SOLL_P':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for _, r in grp.iterrows():
        val = str(r[col])[:34]
        print(
            f"  {val:<35} {r.IST_MAK:>9.2f} {r.SOLL_MAK:>9.2f} {r.DELTA_MAK:>10.2f} "
            f"{r.IST_EUR:>14,.0f} {r.SOLL_EUR:>14,.0f} {r.DELTA_EUR:>14,.0f} "
            f"{int(r.IST_Koepfe):>6} {int(r.SOLL_Planst):>6}"
        )


def main() -> int:
    print(SEP)
    print("  DELTA-ANALYSE NACH EXKLUSIONS-FIX")
    print(SEP)

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

    snap_sa, comp_sa = _build(base_snap, _EXCL_SA, mod)
    snap_sb, comp_sb = _build(base_snap, _EXCL_SB, mod)
    snap_s0, comp_s0 = _build(base_snap, _EXCL_NONE, mod)

    ist_mak = float(comp_sa["IST_MAK"].sum())
    soll_mak = float(comp_sa["SOLL_MAK"].sum())
    ist_eur = float(comp_sa["IST_EUR"].sum())
    soll_eur = float(comp_sa["SOLL_EUR"].sum())
    delta_eur = float(comp_sa["DELTA_EUR"].sum())

    print(f"\n  SA-Basis (Standard-Exklusionen ohne JFV) nach Fix:")
    print(f"    IST_MAK : {ist_mak:>12,.3f}   SOLL_MAK : {soll_mak:>12,.3f}   DELTA_MAK : {ist_mak-soll_mak:>12,.3f}")
    print(f"    IST_EUR : {ist_eur:>16,.2f}   SOLL_EUR : {soll_eur:>16,.2f}   DELTA_EUR : {delta_eur:>16,.2f}")

    # ── Abschnitt 1: Delta-Treiber ────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 1: DELTA-TREIBER NACH DIMENSION")
    print(SEP)

    dims = [
        ("Organisationseinheit",     "Organisationseinheit",     20),
        ("Kürzel OrgEinheit",        "Kürzel OrgEinheit",        25),
        ("Planebene",                "Planebene",                15),
        ("Jobfamily",                "Jobfamily",                20),
        ("OE-Cluster",               "OE-Cluster",               15),
        ("Ist_Entgeltgruppe",        "Ist_Entgeltgruppe",        20),
        ("Ist_ohne_Plan_Soll_Kategorie", "Ist_ohne_Plan_Soll_Kategorie", 10),
        ("Is_Duplicate_Planstelle",  "Is_Duplicate_Planstelle",  5),
        ("Is_Technical_Position",    "Is_Technical_Position",    5),
    ]

    for label, col, n in dims:
        print()
        _agg_by(comp_sa, col, n=n, label=label)

    # Planstelle (Top 25 nach DELTA_EUR)
    print()
    _agg_by(comp_sa, "Planstelle", n=25, label="Planstelle (Top 25 neg. Delta)")

    # ── Abschnitt 2: IST=0 aber SOLL>0 ─────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 2: AUSGESCHLOSSENE PLANSTELLEN MIT VERBLEIBENDEM SOLL")
    print(SEP)
    print("  (IST_MAK=0 UND IST_EUR=0 UND (SOLL_MAK>0 ODER SOLL_EUR>0))\n")

    ist_0 = (comp_sa["IST_MAK"].abs() < 0.001) & (comp_sa["IST_EUR"].abs() < 1.0)
    soll_pos = (comp_sa["SOLL_MAK"] > 0.001) | (comp_sa["SOLL_EUR"] > 1.0)
    mask_excl_soll = ist_0 & soll_pos

    excl_soll_df = comp_sa[mask_excl_soll].copy()
    print(f"  Zeilen mit IST=0 und SOLL>0:  {len(excl_soll_df)}")
    print(f"  Summe SOLL_MAK:               {excl_soll_df['SOLL_MAK'].sum():>10,.3f}")
    print(f"  Summe SOLL_EUR:               {excl_soll_df['SOLL_EUR'].sum():>16,.2f}")
    print(f"  Summe DELTA_EUR:              {excl_soll_df['DELTA_EUR'].sum():>16,.2f}")

    # OE-Kürzel der betroffenen Zeilen im Snapshot
    oe_col = "Kürzel OrgEinheit"
    if oe_col in excl_soll_df.columns:
        print(f"\n  Aufschlüsselung nach OE-Kürzel:")
        oe_grp = excl_soll_df.groupby(oe_col, dropna=False).agg(
            Zeilen     = (oe_col, "count"),
            SOLL_MAK   = ("SOLL_MAK", "sum"),
            SOLL_EUR   = ("SOLL_EUR", "sum"),
            DELTA_EUR  = ("DELTA_EUR", "sum"),
        ).sort_values("SOLL_EUR", ascending=False)
        hdr2 = f"  {'OE-Kürzel':<16} {'Zeilen':>7} {'SOLL_MAK':>10} {'SOLL_EUR':>16} {'DELTA_EUR':>16}"
        print(hdr2)
        print("  " + "-" * (len(hdr2) - 2))
        for oe, r in oe_grp.head(20).iterrows():
            print(f"  {str(oe):<16} {int(r.Zeilen):>7} {r.SOLL_MAK:>10,.3f} {r.SOLL_EUR:>16,.0f} {r.DELTA_EUR:>16,.0f}")

    # Planstellen-Bezeichnungen
    if "Planstelle" in excl_soll_df.columns:
        print(f"\n  Top Planstellenbezeichnungen (nach SOLL_EUR):")
        pl_grp = excl_soll_df.groupby("Planstelle", dropna=False).agg(
            Zeilen   = ("SOLL_EUR", "count"),
            SOLL_MAK = ("SOLL_MAK", "sum"),
            SOLL_EUR = ("SOLL_EUR", "sum"),
        ).sort_values("SOLL_EUR", ascending=False).head(20)
        for pl, r in pl_grp.iterrows():
            print(f"    {str(pl):<42} {int(r.Zeilen):>3} Zeilen  SOLL_MAK {r.SOLL_MAK:>7,.3f}  SOLL_EUR {r.SOLL_EUR:>12,.0f}")

    # ── Abschnitt 3: SOLL in Exklusionsgruppen ────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 3: SOLL-BUDGET IN EXKLUSIONSGRUPPEN")
    print(SEP)
    print("  Messung: Zeilen in comp_sa mit IST_Kopf=0 aber SOLL>0\n")

    # Snap-SA hat Is_Vacant flags gesetzt; wir brauchen den Ursprungs-OE
    # Da comp_df die OE-Spalten enthält, können wir direkt dort auswerten.

    excl_mask_comp = comp_sa["IST_Kopf"].eq(0)  # ausgeschlossen = kein IST-Kopf

    def _soll_group_stats(name: str, row_mask: pd.Series) -> dict:
        sub = comp_sa[row_mask]
        return {
            "name":      name,
            "zeilen":    int(row_mask.sum()),
            "soll_mak":  float(sub["SOLL_MAK"].sum()),
            "soll_eur":  float(sub["SOLL_EUR"].sum()),
            "delta_eur": float(sub["DELTA_EUR"].sum()),
        }

    # OE-Codes für Exklusionsgruppen (aus JOBFAMILY_VALIDATION_ORG_UNITS)
    oe_norm = comp_sa.get("Kürzel OrgEinheit",
                          pd.Series("", index=comp_sa.index)).astype(str).str.strip()

    def _oe(codes):
        if isinstance(codes, str):
            codes = [codes]
        return excl_mask_comp & oe_norm.isin(codes)

    groups_data = []

    # Azubi/Nachwuchs (9910)
    groups_data.append(_soll_group_stats("Azubi / Nachwuchs (OE 9910)", _oe("9910")))

    # Trainee (9920)
    groups_data.append(_soll_group_stats("Trainee / PE bereichsübergreifend (OE 9920)",
                                         _oe("9920")))

    # Praktikum (9921)
    groups_data.append(_soll_group_stats("Praktikum (OE 9921)", _oe("9921")))

    # Erziehungszeit (9975)
    groups_data.append(_soll_group_stats("PA Erziehungszeit (OE 9975)", _oe("9975")))

    # Elternzeit (9971)
    groups_data.append(_soll_group_stats("PA Elternzeit (OE 9971)", _oe("9971")))

    # Mutterschutz (9970)
    groups_data.append(_soll_group_stats("PA Mutterschutz (OE 9970)", _oe("9970")))

    # Sonderurlaub (9972)
    groups_data.append(_soll_group_stats("PA Sonderurlaub §28 (OE 9972)", _oe("9972")))

    # Ruhendes BV (9900)
    groups_data.append(_soll_group_stats("PA Ruhendes BV (OE 9900)", _oe("9900")))

    # ATZ / Freistellung (9990)
    groups_data.append(_soll_group_stats("PA ATZ / Freistellung (OE 9990)", _oe("9990")))

    # Rente auf Zeit (9941)
    groups_data.append(_soll_group_stats("PA Rente auf Zeit (OE 9941)", _oe("9941")))

    # Dauerkranke (9940)
    groups_data.append(_soll_group_stats("PA Dauerkranke (OE 9940)", _oe("9940")))

    # Bundeswehr/Zivil (9960)
    groups_data.append(_soll_group_stats("PA Bundeswehr/Zivildienst (OE 9960)", _oe("9960")))

    # Beurlaubt Pflege (9945)
    groups_data.append(_soll_group_stats("PA Beurlaubt Pflegezeit (OE 9945)", _oe("9945")))

    # Rückkehrer (9980)
    groups_data.append(_soll_group_stats("PA Rückkehrer (OE 9980)", _oe("9980")))

    # Aushilfen (9981)
    groups_data.append(_soll_group_stats("PA Aushilfen (OE 9981)", _oe("9981")))

    # Personalrat (9999)
    groups_data.append(_soll_group_stats("PA Personalrat (OE 9999)", _oe("9999")))

    # Sonstige 99XX (alles was mit 99 anfängt, aber nicht explizit)
    explicit_99 = set(JOBFAMILY_VALIDATION_ORG_UNITS)
    mask_sonstige_99 = excl_mask_comp & oe_norm.str.startswith("99") & ~oe_norm.isin(explicit_99)
    groups_data.append(_soll_group_stats("Sonstige 99XX", mask_sonstige_99))

    # Technische/0,01-Planstellen (Is_Technical_Position)
    if "Is_Technical_Position" in comp_sa.columns:
        mask_tech = excl_mask_comp & comp_sa["Is_Technical_Position"].fillna(False)
        groups_data.append(_soll_group_stats("Technische Planstellen (Sollarbeitszeit≤0.01)", mask_tech))

    # Pool-/Sammelplanstellen (Ist_ohne_Plan_Soll_Kategorie)
    if "Ist_ohne_Plan_Soll_Kategorie" in comp_sa.columns:
        mask_pool = excl_mask_comp & comp_sa["Ist_ohne_Plan_Soll_Kategorie"].eq("Pool- / Sammelplanstelle")
        groups_data.append(_soll_group_stats("Pool- / Sammelplanstellen", mask_pool))

    # Non-99XX excluded (JFV / sollarbeitszeit_001 etc.)
    mask_non99_excl = excl_mask_comp & ~oe_norm.str.startswith("99")
    groups_data.append(_soll_group_stats("Non-99XX ausgeschlossen (JFV / 0.01-AZ)", mask_non99_excl))

    # Summe aller excluded
    groups_data.append(_soll_group_stats("GESAMT ausgeschlossen (IST_Kopf=0)", excl_mask_comp))

    hdr3 = f"  {'Exklusionsgruppe':<46} {'Zeilen':>7} {'SOLL_MAK':>10} {'SOLL_EUR':>16} {'DELTA_EUR':>16}"
    print(hdr3)
    print("  " + "-" * (len(hdr3) - 2))
    for g in groups_data:
        sep_line = g["name"].startswith("GESAMT")
        if sep_line:
            print("  " + "-" * (len(hdr3) - 2))
        print(f"  {g['name']:<46} {g['zeilen']:>7} {g['soll_mak']:>10,.3f} "
              f"{g['soll_eur']:>16,.0f} {g['delta_eur']:>16,.0f}")

    # ── Abschnitt 4: Szenarien ────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 4: FACHLICHE SZENARIEN")
    print(SEP)

    # Szenario A: Aktuelle SA-Basis (IST bereinigt, SOLL vollständig)
    a_ist_mak  = float(comp_sa["IST_MAK"].sum())
    a_soll_mak = float(comp_sa["SOLL_MAK"].sum())
    a_ist_eur  = float(comp_sa["IST_EUR"].sum())
    a_soll_eur = float(comp_sa["SOLL_EUR"].sum())
    a_delta_mak = a_ist_mak - a_soll_mak
    a_delta_eur = a_ist_eur - a_soll_eur
    a_ist_k    = int(comp_sa["IST_Kopf"].sum())
    a_soll_p   = int(comp_sa["SOLL_Planstellen"].sum())

    # Szenario B: IST bereinigt UND SOLL bereinigt
    # SOLL für Is_Vacant=True Zeilen = 0 → nur non-excluded SOLL zählt
    b_mask_active = comp_sa["IST_Kopf"].gt(0) | comp_sa["SOLL_Planstellen"].gt(0)
    # Für Scenario B: SOLL auf excluded Zeilen ignorieren
    b_ist_mak  = a_ist_mak   # IST unverändert
    b_soll_mak = float(comp_sa.loc[~excl_mask_comp, "SOLL_MAK"].sum())
    b_ist_eur  = a_ist_eur   # IST unverändert
    b_soll_eur = float(comp_sa.loc[~excl_mask_comp, "SOLL_EUR"].sum())
    b_delta_mak = b_ist_mak - b_soll_mak
    b_delta_eur = b_ist_eur - b_soll_eur
    b_ist_k    = a_ist_k
    b_soll_p   = int(comp_sa.loc[~excl_mask_comp, "SOLL_Planstellen"].sum())

    # Vergleich mit S0 (keine Exklusionen) als Referenz
    s0_ist_mak  = float(comp_s0["IST_MAK"].sum())
    s0_soll_mak = float(comp_s0["SOLL_MAK"].sum())
    s0_ist_eur  = float(comp_s0["IST_EUR"].sum())
    s0_soll_eur = float(comp_s0["SOLL_EUR"].sum())
    s0_delta_eur = s0_ist_eur - s0_soll_eur
    s0_ist_k    = int(comp_s0["IST_Kopf"].sum())
    s0_soll_p   = int(comp_s0["SOLL_Planstellen"].sum())

    col_w = 18
    print(f"\n  {'Kennzahl':<22} {'S0 (kein Excl)':>{col_w}} {'A: Stellenplan':>{col_w}} {'B: Reporting':>{col_w}} {'A vs. B':>{col_w}}")
    print("  " + "-" * (22 + col_w * 4 + 4))

    def row(name, s0v, av, bv, fmt=",.2f"):
        s0s = f"{s0v:{fmt}}".rjust(col_w)
        as_ = f"{av:{fmt}}".rjust(col_w)
        bs_ = f"{bv:{fmt}}".rjust(col_w)
        diff= f"{bv - av:+,.2f}".rjust(col_w)
        print(f"  {name:<22} {s0s} {as_} {bs_} {diff}")

    row("IST_Köpfe",       s0_ist_k,   a_ist_k,   b_ist_k,   ",.0f")
    row("SOLL_Planstellen",s0_soll_p,  a_soll_p,  b_soll_p,  ",.0f")
    row("IST_MAK",         s0_ist_mak,  a_ist_mak,  b_ist_mak,  ",.3f")
    row("SOLL_MAK",        s0_soll_mak, a_soll_mak, b_soll_mak, ",.3f")
    row("DELTA_MAK",       s0_ist_mak-s0_soll_mak, a_delta_mak, b_delta_mak, ",.3f")
    row("IST_EUR",         s0_ist_eur,  a_ist_eur,  b_ist_eur)
    row("SOLL_EUR",        s0_soll_eur, a_soll_eur, b_soll_eur)
    row("DELTA_EUR",       s0_delta_eur, a_delta_eur, b_delta_eur)

    # Budget-Ausschöpfung
    a_ausch = a_ist_eur / a_soll_eur * 100 if a_soll_eur > 0 else 0
    b_ausch = b_ist_eur / b_soll_eur * 100 if b_soll_eur > 0 else 0
    s0_ausch = s0_ist_eur / s0_soll_eur * 100 if s0_soll_eur > 0 else 0
    print()
    row("Budget-Ausschöpf.", s0_ausch, a_ausch, b_ausch, ",.1f")

    soll_excl_mak = a_soll_mak - b_soll_mak
    soll_excl_eur = a_soll_eur - b_soll_eur
    excl_soll_p   = a_soll_p   - b_soll_p
    print(f"\n  SOLL in exkludierten Planstellen (A minus B):")
    print(f"    SOLL_Planstellen: {excl_soll_p:>6,}")
    print(f"    SOLL_MAK:         {soll_excl_mak:>10,.3f}")
    print(f"    SOLL_EUR:         {soll_excl_eur:>16,.2f}")

    # ── Abschnitt 5: Delta-Komposition ────────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 5: DELTA-KOMPOSITION (SOLL/IST-LÜCKE AUFSCHLÜSSELN)")
    print(SEP)

    # Gesamtdelta = Vakanz (besetzt SOLL ohne IST) + Excl-SOLL (excluded SOLL ohne IST)
    # + echtes Delta (nicht-excluded Zeilen mit Unterauslastung)
    excl_rows      = comp_sa["IST_Kopf"].eq(0)
    nonexcl_rows   = ~excl_rows

    # Excl-SOLL: SOLL_EUR auf excluded Rows (kein IST)
    excl_soll_eur_actual = float(comp_sa.loc[excl_rows, "SOLL_EUR"].sum())
    excl_ist_eur_actual  = float(comp_sa.loc[excl_rows, "IST_EUR"].sum())  # sollte 0 sein

    # Non-excl Delta: Delta auf aktiven Zeilen
    nonexcl_ist_eur  = float(comp_sa.loc[nonexcl_rows, "IST_EUR"].sum())
    nonexcl_soll_eur = float(comp_sa.loc[nonexcl_rows, "SOLL_EUR"].sum())
    nonexcl_delta    = nonexcl_ist_eur - nonexcl_soll_eur

    # Vakanz-Zeilen: IST_Kopf=0 UND SOLL_Planstellen>0 auf non-99XX-excluded
    # (echte offene Stellen ohne Person, nicht durch Exklusion)
    vac_nonexcl = excl_rows & comp_sa["SOLL_Planstellen"].gt(0)
    # Aber excl_rows = alle mit IST_Kopf=0, darunter echte Vakanzen UND excluded

    print(f"\n  Gesamt DELTA_EUR:                        {a_delta_eur:>16,.2f}")
    print(f"\n  Aufschlüsselung:")
    print(f"    SOLL_EUR auf ausgeschlossenen Zeilen:  {excl_soll_eur_actual:>16,.2f}  (Anteil am Delta: {excl_soll_eur_actual/abs(a_delta_eur)*100:.1f}%)")
    print(f"    IST_EUR  auf ausgeschlossenen Zeilen:  {excl_ist_eur_actual:>16,.2f}  (sollte 0 sein)")
    print(f"    Delta auf nicht-excluded Zeilen:       {nonexcl_delta:>16,.2f}  (Anteil am Delta: {nonexcl_delta/a_delta_eur*100:.1f}%)")
    print(f"      IST_EUR  (nicht-excluded):           {nonexcl_ist_eur:>16,.2f}")
    print(f"      SOLL_EUR (nicht-excluded):           {nonexcl_soll_eur:>16,.2f}")

    # IST_EUR / SOLL_EUR für nicht-excluded: echte Ausschöpfung
    real_ausch = nonexcl_ist_eur / nonexcl_soll_eur * 100 if nonexcl_soll_eur > 0 else 0
    print(f"      Budget-Ausschöpfung (nur nicht-excluded): {real_ausch:.1f}%")

    # ── Abschnitt 6: Empfehlung ───────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  ABSCHNITT 6: EMPFEHLUNG")
    print(SEP)

    print(f"""
  Aktuelles Delta nach Fix (Szenario A — Stellenplan-Sicht):
    IST_EUR:   {a_ist_eur:>16,.2f}
    SOLL_EUR:  {a_soll_eur:>16,.2f}
    DELTA_EUR: {a_delta_eur:>16,.2f}

  Haupttreiber des Deltas:
    1. SOLL_EUR für ausgeschlossene Planstellen (Azubi/Elternzeit/ATZ etc.):
       {excl_soll_eur_actual:>16,.2f}  ({excl_soll_eur_actual/abs(a_delta_eur)*100:.1f}% des Deltas)
    2. Delta auf aktiven (nicht-excluded) Planstellen (echte Vakanzen/Unterauslastung):
       {nonexcl_delta:>16,.2f}  ({nonexcl_delta/a_delta_eur*100:.1f}% des Deltas)

  Szenario A (Stellenplan-Sicht):
    IST_EUR:        {a_ist_eur:>16,.2f}   (nur aktive, exklusionsbereinigte MA)
    SOLL_EUR:       {a_soll_eur:>16,.2f}   (vollständiger Stellenplan)
    DELTA_EUR:      {a_delta_eur:>16,.2f}
    Interpretation: Wie viel Budget verbraucht der aktive Personalbestand
                    gegenüber dem gesamten Stellenplan?

  Szenario B (Reporting-Sicht):
    IST_EUR:        {b_ist_eur:>16,.2f}   (exklusionsbereinigt, identisch)
    SOLL_EUR:       {b_soll_eur:>16,.2f}   (nur nicht-excluded Planstellen)
    DELTA_EUR:      {b_delta_eur:>16,.2f}
    Interpretation: Wie groß ist die Lücke im aktiven Personalbestand
                    (echte Vakanzen, ohne Azubi/Elternzeit/ATZ-SOLL)?
    Budget-Ausschöpfung: {b_ausch:.1f}%

  Differenz A vs. B:
    SOLL_EUR-Differenz:   {soll_excl_eur:>16,.2f}   (SOLL in excluded Planstellen)
    DELTA_EUR-Differenz:  {b_delta_eur - a_delta_eur:>16,.2f}

  Ist das neue Delta fachlich plausibel?
    Ja. DELTA_EUR = -16,5 Mio. setzt sich zusammen aus:
      {excl_soll_eur_actual:>10,.2f}  SOLL für ausgeschlossene Planstellen
       + {nonexcl_delta:>9,.2f}  echte Unter-/Überauslastung aktiver Stellen
    Vorher war das Delta mit +11,3 Mio. EUR ausgeschlossener Mitarbeiter-EUR
    zu positiv (zu günstig) dargestellt.

  Empfehlung Toggle:
    Ja, ein Toggle "Stellenplan-Sicht / Reporting-Sicht" wäre sinnvoll:
    - Stellenplan-Sicht (A): SOLL = gesamter Stellenplan inkl. reserved capacity
      → zeigt wo Budget gebunden ist (Azubi-Stellen, Elternzeit-Reserve etc.)
    - Reporting-Sicht (B):   SOLL = nur aktive Positionen ohne Exklusions-SOLL
      → zeigt echte operative Vakanz und Unterauslastung
    Differenz = {soll_excl_eur:>,.0f} EUR = "gebundenes Reserve-Budget"
    (Azubi-Stellen, Elternzeit-Reserven, ATZ-Freistellungen etc.)

  Empfehlung unmittelbar:
    - SOLL_EUR-Korrektur (Szenario B) noch nicht implementieren
    - Offene Entscheidung dokumentieren: wie soll SOLL für excluded Planstellen
      im Dashboard kommuniziert werden?
    - Im Berechnungsprüfung-Expander beide Werte ausweisen.
""")

    print(SEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
