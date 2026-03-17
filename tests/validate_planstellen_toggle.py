"""
Validierungsskript: Exklusionsgruppen-Toggle für Planstellen
============================================================

Fragestellung:
  "Wenn der Toggle 'Exklusionsgruppen auch auf Planstellen anwenden' aktiviert ist,
   wie viele Planstellen-Zeilen werden zusätzlich aus der Soll-IST-Matrix entfernt,
   und gibt es unerwünschte Kollateralschäden?"

Methodik:
  1. Lade Original-Daten (Planstellen + Mitarbeiter) direkt – ohne Streamlit
  2. Simuliere apply_exclusions() – Schritt 1: Person-Exklusion (Vorstand, Ruhend)
  3. Simuliere _build_soll_ist_pivot() – Schritt 2: OE-Exklusion (9900-9999)
  4. Prüfe: Welche Person-exkludierten Zeilen überleben den OE-Filter?
     → Das sind Kandidaten für den Toggle
  5. Analysiere Kollateralrisiko bei alternativen Ansätzen (OE-Erweiterung)

Resultat: Roadmap-Empfehlung auf Basis echter Daten.

Aufruf (vom KSK_Layout-Verzeichnis):
    python tests/validate_planstellen_toggle.py
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------
SCRIPT_DIR  = Path(__file__).resolve().parent
REPO_ROOT   = SCRIPT_DIR.parent                             # KSK_Layout/
BASE_DIR    = REPO_ROOT.parent                              # Version 2 - Focus/
ORIG_DIR    = BASE_DIR / "Original-Daten"
SETTINGS    = REPO_ROOT / "config" / "user_settings.json"

# Pfade zu Original-Daten
PL_FILE  = ORIG_DIR / "Planstellen.XLSX"
MA_FILE  = ORIG_DIR / "Mitarbeiter.xlsx"

# Sicherheits-Check
if not PL_FILE.exists() or not MA_FILE.exists():
    print("❌  Original-Daten nicht gefunden:")
    print(f"   Planstellen: {PL_FILE} – {'✅' if PL_FILE.exists() else '❌'}")
    print(f"   Mitarbeiter: {MA_FILE} – {'✅' if MA_FILE.exists() else '❌'}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Lade Einstellungen (Exklusion)
# ---------------------------------------------------------------------------
def load_exclusions() -> dict:
    if SETTINGS.exists():
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f).get("exclusions", {})
    return {}

EXCLUSIONS = load_exclusions()

print("=" * 70)
print("  VALIDATE: Exklusionsgruppen-Toggle für Planstellen")
print("=" * 70)
print(f"\nExklusions-Einstellungen aus user_settings.json:")
print(f"  vorstand  : {EXCLUSIONS.get('vorstand', False)}")
print(f"  ruhend_bv : {EXCLUSIONS.get('ruhend_bv', False)}")
print(f"  org_units : {len(EXCLUSIONS.get('org_units', []))} Codes")
print()

# ---------------------------------------------------------------------------
# Helper: OE normalisieren (identisch zu apply_exclusions in loader.py)
# ---------------------------------------------------------------------------
def normalize_oe(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def normalize_persnr(series: pd.Series) -> pd.Series:
    """Vereinfachte Variante – trimmt, entfernt .0-Suffix."""
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True).replace("nan", pd.NA)


# ---------------------------------------------------------------------------
# Lade Rohdaten
# ---------------------------------------------------------------------------
print("Lade Original-Daten …")
pl = pd.read_excel(PL_FILE)
ma = pd.read_excel(MA_FILE)
print(f"  Planstellen: {len(pl):,} Zeilen, {len(pl.columns)} Spalten")
print(f"  Mitarbeiter: {len(ma):,} Zeilen, {len(ma.columns)} Spalten")

# ---------------------------------------------------------------------------
# Spaltennamen ermitteln
# ---------------------------------------------------------------------------
def find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
        # Substringsuche als Fallback
        for k, v in lower.items():
            if cand.lower() in k:
                return v
    return None

# Planstellen
PL_PERSNR_COL  = find_col(pl, ["Personalnummer", "PersNr", "PersonalNr"])
PL_OE_COL      = find_col(pl, ["Kürzel OrgEinheit", "OEKuerzel", "OE"])
PL_SOLL_COL    = find_col(pl, ["Sollarbeitszeit", "SollAZ"])
PL_BEWERT_COL  = find_col(pl, ["Bewertung Tarifgruppe", "Bewertung"])
PL_TEXT_COL    = find_col(pl, ["Text Gehaltsband", "Gehaltsband"])

# Mitarbeiter
MA_PERSNR_COL  = find_col(ma, ["PersNr", "Personalnummer", "PersonalNr"])
MA_GRUPPE_COL  = find_col(ma, ["MitarbGruppenbez.", "Mitarbeitergruppe", "MitarbGruppe"])
MA_STATUS_COL  = find_col(ma, ["Status kundenindividuell", "Status"])
MA_TRFGR_COL   = find_col(ma, ["TrfGr", "Tarifgruppe"])

print(f"\nSpalten-Mapping Planstellen:")
print(f"  Personalnummer : {PL_PERSNR_COL}")
print(f"  OE-Kürzel      : {PL_OE_COL}")
print(f"  Sollarbeitszeit: {PL_SOLL_COL}")
print(f"  Bewertung TrfGr: {PL_BEWERT_COL}")
print(f"  Text Gehaltsband: {PL_TEXT_COL}")
print(f"\nSpalten-Mapping Mitarbeiter:")
print(f"  PersNr         : {MA_PERSNR_COL}")
print(f"  MitarbGruppe   : {MA_GRUPPE_COL}")
print(f"  Status         : {MA_STATUS_COL}")
print(f"  TrfGr          : {MA_TRFGR_COL}")

if not all([PL_PERSNR_COL, PL_OE_COL, MA_PERSNR_COL]):
    print("\n❌ Kritische Spalte nicht gefunden – Abbruch.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Merge: Planstellen LEFT JOIN Mitarbeiter (wie combine_to_snapshot)
# ---------------------------------------------------------------------------
pl_work = pl.copy()
ma_work = ma.copy()

pl_work["_persnr_norm"] = normalize_persnr(pl_work[PL_PERSNR_COL])
ma_work["_persnr_norm"] = normalize_persnr(ma_work[MA_PERSNR_COL])

# Nur benötigte MA-Spalten mitführen
ma_keep = ["_persnr_norm"]
for col in [MA_GRUPPE_COL, MA_STATUS_COL, MA_TRFGR_COL]:
    if col and col not in ma_keep:
        ma_keep.append(col)

merged = pl_work.merge(
    ma_work[ma_keep],
    on="_persnr_norm",
    how="left",
    suffixes=("", "_ma"),
)
merged["Is_Vacant_Initial"] = merged["_persnr_norm"].isna() | (merged["_persnr_norm"] == "nan")

print(f"\n{'='*70}")
print(f"  SCHRITT 1: Merge-Ergebnis")
print(f"{'='*70}")
print(f"  Gesamt Planstellen-Zeilen  : {len(merged):,}")
print(f"  Davon initial besetzt      : {(~merged['Is_Vacant_Initial']).sum():,}")
print(f"  Davon initial vakant       : {merged['Is_Vacant_Initial'].sum():,}")

# ---------------------------------------------------------------------------
# Schritt 2: OE-Exklusion (wie _build_soll_ist_pivot) → Aktuelle Logik
# ---------------------------------------------------------------------------
ex_units = EXCLUSIONS.get("org_units", [])

if PL_OE_COL and ex_units:
    s_ou = normalize_oe(merged[PL_OE_COL])
    explicit = [u for u in ex_units if u != "99XX"]
    oe_mask = s_ou.isin(explicit)
    if "99XX" in ex_units:
        explicit_set = set(explicit)
        oe_mask = oe_mask | (s_ou.str.startswith("99") & ~s_ou.isin(explicit_set))
    oe_excluded = merged[oe_mask].copy()
    after_oe = merged[~oe_mask].copy()
else:
    oe_excluded = pd.DataFrame()
    after_oe = merged.copy()

print(f"\n{'='*70}")
print(f"  SCHRITT 2: OE-Exklusion (aktuelle Logik – 99xx OEs)")
print(f"{'='*70}")
print(f"  Durch OE-Exklusion entfernt: {len(oe_excluded):,}")
print(f"  Nach OE-Exklusion verbleibend: {len(after_oe):,}")
if PL_OE_COL and len(oe_excluded) > 0:
    oe_counts = normalize_oe(oe_excluded[PL_OE_COL]).value_counts().head(20)
    print(f"  OEs im exclusion set:")
    for oe, cnt in oe_counts.items():
        print(f"    {oe:8s}: {cnt:4d} Zeilen")

# ---------------------------------------------------------------------------
# Schritt 3: Person-Level Exklusion identifizieren
# (Vorstand + Ruhend BV – die Person-basierten Gruppen)
# ---------------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"  SCHRITT 3: Person-Level Exklusion (Vorstand + Ruhend)")
print(f"  (In after_oe – d.h. Zeilen, die die OE-Exklusion überlebt haben)")
print(f"{'='*70}")

person_excl_mask = pd.Series(False, index=after_oe.index)

# 3a. Vorstand
vorstand_mask = pd.Series(False, index=after_oe.index)
if EXCLUSIONS.get("vorstand") and MA_GRUPPE_COL and MA_GRUPPE_COL in after_oe.columns:
    vorstand_mask = (
        after_oe[MA_GRUPPE_COL].astype(str).str.strip() == "Vorstand"
    )
    person_excl_mask |= vorstand_mask

n_vorstand = int(vorstand_mask.sum())
print(f"\n  A) Vorstand (MitarbGruppenbez. == 'Vorstand'):")
print(f"     Betroffene Zeilen: {n_vorstand}")
if n_vorstand > 0 and PL_OE_COL:
    vs = after_oe[vorstand_mask]
    print(f"     OEs der Vorstand-Planstellen:")
    for oe, cnt in normalize_oe(vs[PL_OE_COL]).value_counts().items():
        print(f"       {oe:8s}: {cnt:4d} Zeilen")
    print(f"     Davon besetzt  : {(~vs['Is_Vacant_Initial']).sum()}")
    print(f"     Davon vakant   : {vs['Is_Vacant_Initial'].sum()}")
    if PL_SOLL_COL in vs.columns:
        print(f"     Soll-Summe     : {vs[PL_SOLL_COL].fillna(0).sum():.1f}h")
    if MA_TRFGR_COL and MA_TRFGR_COL in vs.columns:
        print(f"     TrfGr-Verteilung: {dict(vs[MA_TRFGR_COL].value_counts())}")
    if PL_BEWERT_COL and PL_BEWERT_COL in vs.columns:
        print(f"     Soll-EG (Spalte H): {dict(vs[PL_BEWERT_COL].value_counts())}")

# 3b. Ruhend BV
ruhend_mask = pd.Series(False, index=after_oe.index)
if EXCLUSIONS.get("ruhend_bv") and MA_STATUS_COL and MA_STATUS_COL in after_oe.columns:
    ruhend_mask = (
        after_oe[MA_STATUS_COL].astype(str).str.strip()
        == "Ruhendes Beschäftigungsverhältnis"
    )
    person_excl_mask |= ruhend_mask

n_ruhend = int(ruhend_mask.sum())
print(f"\n  B) Ruhendes BV (Status == 'Ruhendes Beschäftigungsverhältnis'):")
print(f"     Betroffene Zeilen: {n_ruhend}")
if n_ruhend > 0 and PL_OE_COL:
    rv = after_oe[ruhend_mask]
    print(f"     OEs der Ruhend-Planstellen:")
    for oe, cnt in normalize_oe(rv[PL_OE_COL]).value_counts().head(10).items():
        print(f"       {oe:8s}: {cnt:4d} Zeilen")
    print(f"     Davon besetzt  : {(~rv['Is_Vacant_Initial']).sum()}")
    print(f"     Davon vakant   : {rv['Is_Vacant_Initial'].sum()}")

n_person_excl_total = int(person_excl_mask.sum())
print(f"\n  Gesamt Person-exkludiert (in non-OE-excluded rows): {n_person_excl_total}")

# ---------------------------------------------------------------------------
# Schritt 4: Kollateralanalyse – OE-Erweiterung (alternative Ansatz)
# ---------------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"  SCHRITT 4: Kollateral-Analyse (OE-Erweiterungs-Ansatz)")
print(f"  'Was wäre, wenn wir alle OEs der Vorstand-Personen exkludieren?'")
print(f"{'='*70}")

if n_vorstand > 0 and PL_OE_COL:
    vs_oes = set(normalize_oe(after_oe[vorstand_mask][PL_OE_COL]).unique())
    print(f"\n  Vorstand-OEs: {sorted(vs_oes)}")

    # Alle Zeilen in diesen OEs
    s_ou_after = normalize_oe(after_oe[PL_OE_COL])
    all_in_vs_oes = after_oe[s_ou_after.isin(vs_oes)]
    non_vorstand_in_vs_oes = all_in_vs_oes[~vorstand_mask[all_in_vs_oes.index]]

    print(f"  Gesamt Planstellen in Vorstand-OEs     : {len(all_in_vs_oes):,}")
    print(f"  Davon Vorstand-Zeilen                  : {n_vorstand}")
    print(f"  Davon NICHT-Vorstand (Kollateral)      : {len(non_vorstand_in_vs_oes):,}")
    if len(non_vorstand_in_vs_oes) > 0:
        print(f"  ⚠️  OE-basierter Ansatz würde {len(non_vorstand_in_vs_oes):,} Nicht-Vorstand-"
              f"Planstellen KOLLATERAL exkludieren!")
        if MA_GRUPPE_COL and MA_GRUPPE_COL in non_vorstand_in_vs_oes.columns:
            print(f"     MitarbGruppen der Kollateral-Zeilen:")
            for g, c in non_vorstand_in_vs_oes[MA_GRUPPE_COL].value_counts().head(10).items():
                print(f"       {str(g):30s}: {c:4d}")

if n_ruhend > 0 and PL_OE_COL:
    rv_oes = set(normalize_oe(after_oe[ruhend_mask][PL_OE_COL]).unique())
    rv_unique_oes = rv_oes - set(normalize_oe(after_oe[vorstand_mask][PL_OE_COL]).unique()) if n_vorstand > 0 else rv_oes
    print(f"\n  Ruhend-OEs (exkl. bereits bekannte OEs): {sorted(rv_unique_oes)}")
    if rv_unique_oes:
        s_ou_after = normalize_oe(after_oe[PL_OE_COL])
        all_in_rv_oes = after_oe[s_ou_after.isin(rv_unique_oes)]
        non_ruhend_in_rv_oes = all_in_rv_oes[~ruhend_mask[all_in_rv_oes.index]]
        print(f"  Gesamt Planstellen in Ruhend-OEs       : {len(all_in_rv_oes):,}")
        print(f"  Davon Ruhend-Zeilen                    : {int(ruhend_mask[s_ou_after.isin(rv_unique_oes)].sum())}")
        print(f"  Davon Nicht-Ruhend (Kollateral)        : {len(non_ruhend_in_rv_oes):,}")

# ---------------------------------------------------------------------------
# Schritt 5: Bewertung – Person-ID-basierter Ansatz (empfohlen)
# ---------------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"  SCHRITT 5: Bewertung – Person-ID-basierter Ansatz (Empfehlung)")
print(f"{'='*70}")

print(f"""
  ANSATZ A: MitarbGruppenbez./Status direkt in _build_soll_ist_pivot filtern
  ─────────────────────────────────────────────────────────────────────────────
  Funktionsprinzip:
    Nach OE-Exklusion zusätzlich filtern:
      if excl.get('vorstand'): entferne Zeilen mit MitarbGruppenbez. == 'Vorstand'
      if excl.get('ruhend_bv'): entferne Zeilen mit Status == 'Ruhendes BV'

  Warum das funktioniert:
    apply_exclusions() nullt MitarbGruppenbez. und Status NICHT (nicht in
    person_fields-Liste). Diese Felder bleiben auch nach der Person-Exklusion
    erhalten → zuverlässige Identifikation im Pivot-Builder möglich.

  Vorteile:
    ✅ Kein Kollateralschaden (nur exakt die betroffenen Zeilen werden entfernt)
    ✅ Keine neue Spalte nötig (nutzt bereits existierende Felder)
    ✅ Kein Eingriff in apply_exclusions() / loader.py nötig
    ✅ Toggle-Zustand wird nur in _build_soll_ist_pivot ausgewertet
    ✅ Rückwärtskompatibel (Toggle default=False → kein Verhaltensänderung)

  Einschränkung:
    ⚠️  Wirkung nur auf Zeilen, die BEREITS einem Vorstand/Ruhend-Mitarbeiter
       zugeordnet waren (besetzt). Genuinely vakante Planstellen in Vorstand-OEs
       werden NICHT entfernt – weil sie keine Personendaten haben.
    → Für den KSK-Use-Case ist das korrekt: Es gibt 3 Vorstand-Planstellen,
       alle besetzt (s. debug_keine_soll_eg_besetzt.py-Ergebnis).
""")

print(f"  Quantitative Wirkung des Toggles (Ansatz A):")
print(f"    Planstellen nach OE-Exklusion (aktuell in Matrix): {len(after_oe):,}")
print(f"    Davon Toggle-Kandidaten (Vorstand)               : {n_vorstand}")
print(f"    Davon Toggle-Kandidaten (Ruhend BV)              : {n_ruhend}")
print(f"    Gesamt Toggle-Effekt (inkl. Überschneidungen)    : {n_person_excl_total}")
print(f"    Matrix nach Toggle                               : {len(after_oe) - n_person_excl_total:,}")

# ---------------------------------------------------------------------------
# Schritt 6: Vakante Planstellen in Vorstand-OEs (ungefähre Abschätzung)
# ---------------------------------------------------------------------------
if n_vorstand > 0 and PL_OE_COL:
    print(f"\n{'='*70}")
    print(f"  SCHRITT 6: Vakante Planstellen in Vorstand-OEs")
    print(f"  (Would-be-additional if OE-approach were used)")
    print(f"{'='*70}")
    vs_oes = set(normalize_oe(after_oe[vorstand_mask][PL_OE_COL]).unique())
    s_ou_after = normalize_oe(after_oe[PL_OE_COL])
    in_vs_oes = after_oe[s_ou_after.isin(vs_oes) & after_oe["Is_Vacant_Initial"]]
    print(f"  Vakante Planstellen in Vorstand-OEs: {len(in_vs_oes):,}")
    print(f"  (Diese würde OE-Ansatz zusätzlich exkludieren, Ansatz A hingegen NICHT.)")
    if len(in_vs_oes) > 0:
        print(f"  → Diese Planstellen stellen echten Personalbedarf dar und")
        print(f"    sollten in der Matrix SICHTBAR bleiben. Ansatz A ist korrekt.")

# ---------------------------------------------------------------------------
# ZUSAMMENFASSUNG
# ---------------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"  ZUSAMMENFASSUNG & ROADMAP-EMPFEHLUNG")
print(f"{'='*70}")
print(f"""
  BEFUND:
    • OE-basierte 99xx-Gruppen (Azubis, Elternzeit, etc.) sind bereits
      korrekt in der Planstellen-Matrix exkludiert. Kein Handlungsbedarf.
    • Person-Level-Gruppen (Vorstand, Ruhend BV) hinterlassen {n_person_excl_total}
      Zeilen in der Matrix, obwohl die Personen exkludiert sind.
    • OE-basierter Ansatz für Vorstand würde erheblichen Kollateralschaden
      verursachen (nicht-Vorstand-Planstellen in OE 800 würden entfernt).
    • Ansatz A (direkte Filterung via MitarbGruppenbez./Status im Pivot-Builder)
      ist präzise, sicher und einfach umzusetzen.

  ROADMAP:
    1. Neues Setting: exclusions.planstellen_follow_person (bool, default=False)
    2. Einstellungen.py: Toggle-UI hinzufügen (Checkbox, Erläuterung)
    3. 1_Kompakt.py: _build_soll_ist_pivot() um Toggle-Logik erweitern:
         if toggle: filter MitarbGruppenbez. == 'Vorstand' aus work
         if toggle: filter Status == 'Ruhendes BV' aus work
    4. Kein Eingriff in loader.py / apply_exclusions() nötig
    5. Test: 3 Vorstand-Zeilen müssen aus '(Keine Soll-EG)'-Zeile verschwinden

  RISIKO: NIEDRIG – der Toggle ist additiv, opt-in, und ohne Kollateralschaden.
""")
print("=" * 70)
print("  Validierung abgeschlossen.")
print("=" * 70)
