"""
analyse_soll_fte_null.py
========================
Fachliche Analyse der besetzten Zeilen mit Soll_FTE = 0 / Soll_Cost_Year = 0.

Fragestellung: Handelt es sich um
  A. Fachlich korrekte IST-ohne-SOLL-Fälle
  B. Exklusionskandidaten (Azubi, ATZ, Sonderurlaub, ...)
  C. Datenqualitätsprobleme

Run:
    py -X utf8 KSK_Layout/tests/analyse_soll_fte_null.py
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
from kpi_reference import get_current_stichtag
from config.settings import BASE_SALARY, STEP_MULTIPLIER, EMPLOYER_COST_FACTOR
from dataloader.tvoed_loader import get_annual_salary

SEP  = "=" * 72
SEP2 = "-" * 52

COLS_WANTED = [
    "Planstellennr", "Planstelle", "Planstellenkürzel",
    "Organisationseinheit", "Kürzel OrgEinheit", "OE-Cluster", "Jobfamily",
    "MitarbKreisbez.", "MitarbGruppenbez.",
    "Vertragsart", "Status kundenindividuell",
    "ATZ_Status", "Phase",
    "Ausbildung", "Ist_Azubi",
    "Beschäftigungsstatus",
    "TrfGr", "St",
    "Soll_FTE", "Soll_Cost_Year",
    "MAK_Reporting", "EUR_Reporting",
    "Is_Vacant", "PersNr", "Personalnummer",
]


def _soll_fte_numeric(snap: pd.DataFrame) -> pd.Series:
    if "Soll_FTE" not in snap.columns:
        return pd.Series(np.nan, index=snap.index)
    return pd.to_numeric(snap["Soll_FTE"], errors="coerce")


def _calculate_soll_cost_bare(row: pd.Series) -> float:
    try:
        soll_fte = float(row.get("Soll_FTE") or 0.0)
        tarif = str(row.get("TrfGr") or "").strip().upper().replace(" ", "") or "E9A"
        step_raw = row.get("St")
        try:
            step = int(str(step_raw).strip().replace("+","").replace("-",""))
        except Exception:
            step = 4
        if tarif in ("TVAÖD", "TVÖAD", "TVAOD"):
            return {1:14400.0,2:15212.0,3:16099.0,4:17023.0}.get(step,14400.0) * soll_fte * EMPLOYER_COST_FACTOR
        if tarif == "1":
            return 200000.0 * soll_fte * EMPLOYER_COST_FACTOR
        annual = get_annual_salary({}, tarif, step, BASE_SALARY, STEP_MULTIPLIER)
        return annual * EMPLOYER_COST_FACTOR * soll_fte
    except Exception:
        return 0.0


def _pct(n: int, total: int) -> str:
    return f"{n} ({n/total*100:.0f}%)" if total else str(n)


def _mak_sum(df: pd.DataFrame) -> float:
    col = next((c for c in ["MAK_Reporting","MAK_Calculated","MAK","mak"] if c in df.columns), None)
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum()) if col else 0.0


def _eur_sum(df: pd.DataFrame) -> float:
    col = next((c for c in ["EUR_Reporting","Total_Cost_Year"] if c in df.columns), None)
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum()) if col else 0.0


def _n_persons(df: pd.DataFrame) -> int:
    for c in ["PersNr","Personalnummer"]:
        if c in df.columns:
            return int(df[c].dropna().astype(str).str.strip().ne("").sum())
    return len(df)


def _vc(series: pd.Series, top: int = 10, label: str = "Wert") -> str:
    if series.empty:
        return "  (leer)"
    vc = series.fillna("(NaN)").astype(str).str.strip().value_counts().head(top)
    lines = [f"  {v!r:35s} {c}" for v, c in vc.items()]
    return "\n".join(lines)


def _flag_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].fillna("").astype(str).str.strip().ne("")


# ── Keywords für Sonderkategorien ────────────────────────────────────────────
KEYWORD_CATS = {
    "ATZ / Altersteilzeit": {
        "cols": ["ATZ_Status","Status kundenindividuell","MitarbGruppenbez.","Vertragsart","Phase","Planstelle","Planstellenkürzel"],
        "keywords": ["atz","altersteilzeit","freistellung"],
    },
    "Elternzeit": {
        "cols": ["Status kundenindividuell","MitarbGruppenbez.","Vertragsart"],
        "keywords": ["elternzeit","erziehungsurlaub"],
    },
    "Sonderurlaub / Beurlaubung": {
        "cols": ["Status kundenindividuell","MitarbGruppenbez.","Vertragsart"],
        "keywords": ["sonderurlaub","beurlaubung","unbezahlter"],
    },
    "Rente auf Zeit / Vorruhestand": {
        "cols": ["Status kundenindividuell","MitarbGruppenbez.","Vertragsart","Planstelle"],
        "keywords": ["rente","vorruhestand","altersrente","freigestellt"],
    },
    "Azubi / Ausbildung": {
        "cols": ["TrfGr","Ist_Azubi","Jobfamily","MitarbGruppenbez.","Planstelle"],
        "keywords": ["tva","azubi","ausbildung"],
    },
    "Trainee": {
        "cols": ["Jobfamily","MitarbGruppenbez.","Planstelle","Vertragsart"],
        "keywords": ["trainee"],
    },
    "Vorstand": {
        "cols": ["MitarbGruppenbez.","Planstelle","TrfGr"],
        "keywords": ["vorstand"],
    },
    "Ruhendes BV": {
        "cols": ["Status kundenindividuell"],
        "keywords": ["ruhendes beschäftigungsverhältnis","ruhendes bv","ruhendes besch"],
    },
    "Pool / Sammelplanstelle": {
        "cols": ["Planstelle","Planstellenkürzel"],
        "keywords": ["pool","sammel"],
    },
    "Technische / Mini-Planstelle (Soll_FTE)": {
        "cols": [],  # separate numeric check
        "keywords": [],
        "_numeric_check": True,
    },
}

RECOMMEND = {
    "ATZ / Altersteilzeit":              "Exklusionskandidat (B) — ATZ reduziert IST-MAK, SOLL-FTE=0 ist erwartet im Freistellungsblock",
    "Elternzeit":                        "Exklusionskandidat (B) — Elternzeit = kein SOLL-Bedarf aktiv",
    "Sonderurlaub / Beurlaubung":        "fachlich prüfen — je nach Dauer IST-ohne-SOLL oder Exklusion",
    "Rente auf Zeit / Vorruhestand":     "fachlich prüfen — könnten bereits in Exklusionsgruppen sein",
    "Azubi / Ausbildung":                "fachlich korrekt (A) — Azubi hat MAK=0 by design, SOLL_FTE=0 erwartet",
    "Trainee":                           "fachlich korrekt (A) — wenn Trainee-Logik analog zu Azubi",
    "Vorstand":                          "Exklusionskandidat (B) — Vorstand wird üblicherweise excludiert",
    "Ruhendes BV":                       "Exklusionskandidat (B) — apply_exclusions enthält ruhend_bv-Logik",
    "Pool / Sammelplanstelle":           "fachlich prüfen — Soll_FTE=0 bei Pool könnte Datenqualitätsproblem (C) sein",
    "Technische / Mini-Planstelle (Soll_FTE)": "fachlich prüfen — Mini-Planstellen (0<FTE≤0.015) als Is_Technical_Position bekannt",
}


def _keyword_mask(df: pd.DataFrame, cols: list[str], keywords: list[str]) -> pd.Series:
    mask = pd.Series(False, index=df.index)
    pattern = "|".join(keywords) if keywords else None
    if not pattern:
        return mask
    for col in cols:
        if col in df.columns:
            mask |= df[col].fillna("").astype(str).str.lower().str.contains(pattern, regex=True, na=False)
    return mask


def main() -> int:
    print(SEP)
    print("  ANALYSE: Besetzte Zeilen mit Soll_FTE = 0 / Soll_Cost_Year = 0")
    print(SEP)

    # ── Daten laden ──────────────────────────────────────────────────────────
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

    print(f"Snapshot: {len(snap)} Zeilen, Stichtag {stichtag.date()}")
    avail = [c for c in COLS_WANTED if c in snap.columns]
    miss  = [c for c in COLS_WANTED if c not in snap.columns]
    if miss:
        print(f"Fehlende Spalten (werden übersprungen): {', '.join(miss)}")

    # ── Soll_FTE numerisch ───────────────────────────────────────────────────
    soll_fte_num = _soll_fte_numeric(snap)
    snap["_soll_fte_num"] = soll_fte_num

    # ── Soll_Cost_Year berechnen (bare mode) ─────────────────────────────────
    snap["_soll_cost"] = snap.apply(_calculate_soll_cost_bare, axis=1)

    # ── Filter: besetzte Zeilen mit Soll_FTE = 0 ────────────────────────────
    vacant_mask   = snap["Is_Vacant"].fillna(False).astype(bool) if "Is_Vacant" in snap.columns else pd.Series(False, index=snap.index)
    persnr_mask   = pd.Series(False, index=snap.index)
    for col in ["PersNr","Personalnummer"]:
        if col in snap.columns:
            persnr_mask |= snap[col].fillna("").astype(str).str.strip().ne("")
    trfgr_mask    = snap["TrfGr"].fillna("").astype(str).str.strip().ne("") if "TrfGr" in snap.columns else pd.Series(False, index=snap.index)
    soll_zero     = soll_fte_num.fillna(0).eq(0)

    focus = snap[~vacant_mask & persnr_mask & trfgr_mask & soll_zero].copy()
    N = len(focus)
    P = _n_persons(focus)
    mak = _mak_sum(focus)
    eur = _eur_sum(focus)

    print(f"\nGesamter Snapshot:           {len(snap)} Zeilen")
    print(f"Davon besetzt (Is_Vacant=F): {(~vacant_mask).sum()}")
    print(f"Filter-Basis (besetzt, PersNr, TrfGr, Soll_FTE=0): {N} Zeilen / {P} Personen")
    print(f"IST_MAK:   {mak:.4f}")
    print(f"IST_EUR:   {eur:>15,.2f}")

    # ── 2. Aggregation ───────────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  2. TOP AGGREGATIONEN")
    print(SEP2)

    agg_dims = {
        "Planstellennr":          "Top Planstellennr",
        "Planstelle":             "Top Planstellenbezeichnungen",
        "Organisationseinheit":   "Top OE",
        "Kürzel OrgEinheit":      "Top OE-Kürzel",
        "OE-Cluster":             "Top OE-Cluster",
        "Jobfamily":              "Top Jobfamily",
        "MitarbGruppenbez.":      "Top Mitarbeitergruppen",
        "MitarbKreisbez.":        "Top Mitarbeiterkreis",
        "Vertragsart":            "Top Vertragsart",
        "Status kundenindividuell": "Top Status kundenindividuell",
        "ATZ_Status":             "ATZ_Status-Verteilung",
        "Phase":                  "Phase-Verteilung",
        "Ist_Azubi":              "Ist_Azubi",
        "TrfGr":                  "TrfGr-Verteilung",
        "St":                     "Stufe-Verteilung",
    }

    for col, label in agg_dims.items():
        if col in focus.columns:
            print(f"\n  {label}:")
            print(_vc(focus[col], top=12))
        else:
            print(f"\n  {label}: [Spalte nicht im Snapshot]")

    # ── 3. Sonderkategorien ───────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  3. SONDERKATEGORIEN")
    print(SEP2)
    print(f"  {'Kategorie':<42} {'Zeilen':>6} {'Pers':>5} {'IST_MAK':>10} {'IST_EUR':>15} Empfehlung")
    print(f"  {'-'*42} {'-'*6} {'-'*5} {'-'*10} {'-'*15}")

    cat_masks: dict[str, pd.Series] = {}
    for cat, cfg in KEYWORD_CATS.items():
        if cfg.get("_numeric_check"):
            # Technische Planstelle: 0 < Soll_FTE <= 0.015 — per Definition gibt es diese hier nicht
            # (da wir bereits Soll_FTE=0 gefiltert haben). Diese Kategorie entfällt.
            m = pd.Series(False, index=focus.index)
        else:
            m = _keyword_mask(focus, cfg["cols"], cfg["keywords"])
        cat_masks[cat] = m
        sub = focus[m]
        n   = len(sub)
        p   = _n_persons(sub)
        mk  = _mak_sum(sub)
        eu  = _eur_sum(sub)
        rec = RECOMMEND.get(cat, "fachlich prüfen")
        print(f"  {cat:<42} {n:>6} {p:>5} {mk:>10.3f} {eu:>15,.0f}  {rec}")

    # ── Überlappungen auflösen für Klassifikation ────────────────────────────
    # Fachlich korrekt (A): Azubi, Trainee
    mask_A = cat_masks["Azubi / Ausbildung"] | cat_masks["Trainee"]
    # Exklusionskandidaten (B): ATZ, Elternzeit, Vorstand, Ruhendes BV
    mask_B = (
        cat_masks["ATZ / Altersteilzeit"] |
        cat_masks["Elternzeit"] |
        cat_masks["Vorstand"] |
        cat_masks["Ruhendes BV"]
    ) & ~mask_A
    # Fachlich prüfen (B2): Sonderurlaub, Pool, Rente
    mask_B2 = (
        cat_masks["Sonderurlaub / Beurlaubung"] |
        cat_masks["Rente auf Zeit / Vorruhestand"] |
        cat_masks["Pool / Sammelplanstelle"]
    ) & ~mask_A & ~mask_B
    # Unkategorisiert (C): alle verbleibenden
    any_cat = mask_A | cat_masks["ATZ / Altersteilzeit"] | cat_masks["Elternzeit"] | \
              cat_masks["Sonderurlaub / Beurlaubung"] | cat_masks["Rente auf Zeit / Vorruhestand"] | \
              cat_masks["Vorstand"] | cat_masks["Ruhendes BV"] | cat_masks["Pool / Sammelplanstelle"] | \
              cat_masks["Trainee"]
    mask_C  = ~any_cat

    # ── 4. Exklusionsprüfung ─────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  4. EXKLUSIONSPRÜFUNG")
    print(SEP2)

    # Vorstand (apply_exclusions: MitarbGruppenbez. == "Vorstand")
    ex_vorstand = cat_masks["Vorstand"]
    # Ruhendes BV (apply_exclusions: Status kundenindividuell)
    ex_ruhend   = cat_masks["Ruhendes BV"]
    # Azubi MAK-Nullung (loader._zero_out_azubi_mak: TrfGr contains TVA OR Jobfamily contains Azubi/Ausbildung)
    ex_azubi_tva = pd.Series(False, index=focus.index)
    if "TrfGr" in focus.columns:
        ex_azubi_tva |= focus["TrfGr"].fillna("").astype(str).str.contains("TVA", case=False, na=False)
    if "Jobfamily" in focus.columns:
        ex_azubi_tva |= focus["Jobfamily"].fillna("").astype(str).str.contains("Azubi|Ausbildung", case=False, na=False)

    print(f"  Vorstand-Exklusion (apply_exclusions):         {ex_vorstand.sum()} Zeilen")
    print(f"  Ruhendes BV (apply_exclusions):                {ex_ruhend.sum()} Zeilen")
    print(f"  Azubi MAK=0 (_zero_out_azubi_mak):             {ex_azubi_tva.sum()} Zeilen")
    print(f"  Gesamte bekannte Exklusions-Abdeckung:         {(ex_vorstand|ex_ruhend|ex_azubi_tva).sum()} Zeilen")
    print(f"  Verbleibend ohne bekannte Exklusion:           {(~(ex_vorstand|ex_ruhend|ex_azubi_tva)).sum()} Zeilen")
    print()
    print("  Werden diese Zeilen aktuell durch Exklusionen behandelt?")
    print("  -> Vorstand: JA, wenn 'vorstand'-Exklusion in Session State aktiviert")
    print("  -> Ruhendes BV: JA, wenn 'ruhend_bv'-Exklusion aktiviert")
    print("  -> Azubi (TVA): MAK wird genullt (MAK=0), aber Zeile bleibt in Snapshot")
    print("  -> ATZ/Elternzeit/Sonderurlaub: NEIN — keine eigene apply_exclusions-Logik")
    print("  -> Rest: NEIN — verbleiben unbehandelt in IST-Basis")

    # ── 5. Klassifikation A / B / C ──────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  5. KLASSIFIKATION")
    print(SEP2)

    groups = [
        ("A – Fachlich korrekt: IST ohne SOLL", mask_A,
         "Azubi/Trainee: Soll_FTE=0 by design. MAK bereits 0. Im IST belassen."),
        ("B – Klarer Exklusionskandidat",       mask_B,
         "ATZ/Elternzeit/Vorstand/RuhendesBV: kein aktiver SOLL-Bedarf. Prüfen, ob per Einstellung ausgeschlossen."),
        ("B2 – Fachlich zu prüfen",             mask_B2,
         "Sonderurlaub/Pool/Rente: kontextabhängig. Ggf. separat ausweisen."),
        ("C – Datenqualität: kein Kategorie-Match", mask_C,
         "Keine Kategorie erkannt. Soll_FTE=0 bei regulärer Vollstelle ist ein Datenproblem."),
    ]

    for grp_label, mask, rec in groups:
        sub  = focus[mask]
        n, p = len(sub), _n_persons(sub)
        mk   = _mak_sum(sub)
        eu   = _eur_sum(sub)
        print(f"\n  {grp_label}")
        print(f"  Zeilen: {n}, Personen: {p}, IST_MAK: {mk:.3f}, IST_EUR: {eu:,.0f}")
        print(f"  Empfehlung: {rec}")

        if n > 0 and n <= 30:
            show_cols = [c for c in ["Planstellennr","Planstelle","TrfGr","Organisationseinheit",
                                     "Status kundenindividuell","MitarbGruppenbez.","ATZ_Status","Phase",
                                     "PersNr","_soll_fte_num"] if c in sub.columns]
            print(f"  Beispiele:")
            print(sub[show_cols].head(15).to_string(index=False))
        elif n > 30:
            print(f"  Top Planstellenbezeichnungen (n={n}):")
            if "Planstelle" in sub.columns:
                print(_vc(sub["Planstelle"], top=8))
            print(f"  Top Organisationseinheit:")
            if "Organisationseinheit" in sub.columns:
                print(_vc(sub["Organisationseinheit"], top=8))
            if "Status kundenindividuell" in sub.columns:
                print(f"  Top Status:")
                print(_vc(sub["Status kundenindividuell"], top=8))

    # ── 6. Pool-Planstellen Tiefenanalyse ────────────────────────────────────
    print(f"\n{SEP2}")
    print("  6. POOL-PLANSTELLEN (Soll_FTE=0, viele Zeilen je Planstellennr)")
    print(SEP2)
    if "Planstellennr" in focus.columns:
        pl_counts = focus.groupby("_soll_fte_num").apply(lambda g: g["Planstellennr"].value_counts().head(5))
        # Einfacher: Planstellennr nach Häufigkeit im Focus
        pl_vc = focus["Planstellennr"].fillna("(NaN)").astype(str).value_counts().head(10)
        print(f"  Top Planstellennr im Soll_FTE=0-Subset (besetzte Zeilen):")
        print(pl_vc.to_string())

    # ── 7. Zusammenfassung ────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  ERGEBNISFORMAT")
    print(SEP)
    print(f"  Besetzte Zeilen mit Soll_FTE = 0:")
    print(f"  - Zeilen:    {N}")
    print(f"  - Personen:  {P}")
    print(f"  - IST_MAK:   {mak:.4f}")
    print(f"  - IST_EUR:   {eur:>15,.2f}")

    print(f"\n  Klassifikation:")
    for grp_label, mask, _ in groups:
        sub = focus[mask]
        print(f"  - {grp_label}: {len(sub)} Zeilen")

    mask_A_sum = focus[mask_A]; mask_B_sum = focus[mask_B]; mask_B2_sum = focus[mask_B2]; mask_C_sum = focus[mask_C]
    print(f"\n  Top Ursachen (geschätzt):")
    for i, (cat, m) in enumerate(cat_masks.items(), 1):
        n = m.sum()
        if n > 0:
            print(f"  {i}. {cat}: {n} Zeilen")

    print(f"\n  Empfehlung:")
    print(f"  - kurzfristig:  Gruppe C identifizieren und Datenproblem in Planstellen-Excel beheben.")
    print(f"                  Gruppe A (Azubi) ist korrekt — keine Änderung nötig.")
    print(f"  - mittelfristig: Gruppe B für ATZ/Elternzeit in apply_exclusions aufnehmen oder")
    print(f"                   als eigene Kategorie 'IST ohne Plan-SOLL' ausweisen.")
    print(f"  - UI-Hinweis:   Relevant wenn Gruppe B oder C > 5% der IST_EUR-Basis.")
    print(f"  - Codeänderung nötig: Nein (kurzfristig) — fachliche Klärung zuerst.")
    print(SEP)

    return 0


if __name__ == "__main__":
    sys.exit(main())
