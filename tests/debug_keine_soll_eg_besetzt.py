"""
debug_keine_soll_eg_besetzt.py
===============================
Untersucht Planstellen, die KEINE verwertbare Soll-EG haben (weder Spalte H
noch Spalte I), aber dennoch mit einer Person besetzt sind, die eine
Entgeltgruppe (TrfGr) hat.

Fragen:
  1. Wie viele solcher Planstellen gibt es?
  2. Was sind das fuer Stellen (Bezeichnung, OE, Sollarbeitszeit)?
  3. Was sind das fuer Personen (Funktion, EG, Beschaeftigungsart)?
  4. Gibt es Muster (bestimmte OEs, EG-Cluster, Stellentypen)?

Ausfuehren (aus KSK_Layout/):
    py -X utf8 tests/debug_keine_soll_eg_besetzt.py
"""

import os
import sys
import json
import pandas as pd

# ── Pfade ─────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DIR  = os.path.join(BASE_DIR, "..", "Original-Daten")
SETTINGS_FILE = os.path.join(BASE_DIR, "config", "user_settings.json")

MITARBEITER_FILE = os.path.join(ORIGINAL_DIR, "Mitarbeiter.xlsx")
PLANSTELLEN_FILE = os.path.join(ORIGINAL_DIR, "Planstellen.XLSX")

SEP  = "=" * 75
SEP2 = "-" * 75

TARIFF_GROUPS = [
    "E1","E2","E2U","E3","E4","E5","E6","E7","E8",
    "E9A","E9B","E9C","E10","E11","E12","E13","E14","E15","E15U",
]


def hr(title=""):
    print("\n" + SEP)
    if title:
        print("  " + title)
        print(SEP)


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def apply_oe_exclusion(ou_series: pd.Series, org_units: list) -> pd.Series:
    s = ou_series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    explicit = [u for u in org_units if u != "99XX"]
    mask = s.isin(explicit)
    if "99XX" in org_units:
        mask = mask | (s.str.startswith("99") & ~s.isin(set(explicit)))
    return mask


def normalize_persnr(s: pd.Series) -> pd.Series:
    return (s.astype(str).str.strip()
             .str.replace(r"\.0$", "", regex=True)
             .str.upper())


def clean_eg(s) -> str:
    """Normalisiert Soll-EG aus Spalte H oder I."""
    if pd.isna(s):
        return ""
    v = str(s).strip().upper().replace(" ", "")
    if v.startswith("BIS"):
        v = v[3:].strip()
    return v


def run():
    hr("debug_keine_soll_eg_besetzt.py")
    print("  Untersucht Planstellen ohne Soll-EG, die aber mit einer")
    print("  eingruppierter Person besetzt sind.")

    # ── 0. Dateien pruefen ────────────────────────────────────────────────────
    for path, name in [
        (MITARBEITER_FILE, "Mitarbeiter.xlsx"),
        (PLANSTELLEN_FILE, "Planstellen.XLSX"),
    ]:
        if not os.path.exists(path):
            print(f"[FEHLER] Datei nicht gefunden: {path}")
            sys.exit(1)

    # ── 1. Settings ───────────────────────────────────────────────────────────
    settings       = load_settings()
    ex             = settings.get("exclusions", {})
    ex_units       = ex.get("org_units", [])
    stichtag_raw   = settings.get("stichtag", "2025-12-31")
    STICHTAG       = pd.Timestamp(stichtag_raw)

    hr("SCHRITT 1 — Einstellungen")
    print(f"  Stichtag:       {STICHTAG.date()}")
    print(f"  OE-Exklusion:   {ex_units}")

    # ── 2. Daten laden ────────────────────────────────────────────────────────
    hr("SCHRITT 2 — Daten laden")
    ma = pd.read_excel(MITARBEITER_FILE)
    pl = pd.read_excel(PLANSTELLEN_FILE)
    print(f"  Mitarbeiter.xlsx: {len(ma):,} Zeilen")
    print(f"  Planstellen.XLSX: {len(pl):,} Zeilen")
    print(f"\n  Planstellen-Spalten: {list(pl.columns)}")
    print(f"  Mitarbeiter-Spalten: {list(ma.columns)}")

    # ── 3. OE-Exklusion auf Planstellen ──────────────────────────────────────
    hr("SCHRITT 3 — OE-Exklusion")
    oe_col_pl = next((c for c in pl.columns if "kuerzel" in c.lower() or "kürzel" in c.lower()), None)
    if oe_col_pl is None:
        oe_col_pl = next((c for c in pl.columns if "org" in c.lower()), pl.columns[0])
    print(f"  OE-Spalte Planstellen: '{oe_col_pl}'")

    excl_mask = apply_oe_exclusion(pl[oe_col_pl], ex_units)
    pl_incl = pl[~excl_mask].copy()
    print(f"  Planstellen gesamt:    {len(pl):,}")
    print(f"  Davon exkludiert:      {excl_mask.sum():,}")
    print(f"  Inkludiert:            {len(pl_incl):,}")

    # ── 4. Soll-EG-Normalisierung ─────────────────────────────────────────────
    hr("SCHRITT 4 — Soll-EG-Normalisierung (Spalte H + I)")
    _invalid = {"", "NAN", "NONE"}

    col_h_name = "Bewertung Tarifgruppe"
    col_i_name = "Text Gehaltsband"

    if col_h_name not in pl_incl.columns:
        print(f"[FEHLER] Spalte '{col_h_name}' nicht gefunden.")
        sys.exit(1)

    col_h = pl_incl[col_h_name].map(clean_eg)
    col_i = (pl_incl[col_i_name].map(clean_eg)
             if col_i_name in pl_incl.columns
             else pd.Series("", index=pl_incl.index))

    soll_eg_i = col_i.where(~col_i.isin(_invalid), other=col_h)  # Maximalwert
    soll_eg_h = col_h

    pl_incl = pl_incl.copy()
    pl_incl["_Soll_EG_H"] = soll_eg_h
    pl_incl["_Soll_EG_I"] = soll_eg_i
    pl_incl["_Soll_EG"]   = soll_eg_i  # Maximalwert hat Vorrang

    n_mit_soll_eg = int((~soll_eg_i.isin(_invalid)).sum())
    n_ohne_soll_eg = int(soll_eg_i.isin(_invalid).sum())
    print(f"  Mit gueltiger Soll-EG:    {n_mit_soll_eg:,}")
    print(f"  Ohne Soll-EG (weder H/I): {n_ohne_soll_eg:,}")

    # ── 5. Planstellen ohne Soll-EG isolieren ────────────────────────────────
    hr("SCHRITT 5 — Planstellen ohne Soll-EG")
    pl_no_eg = pl_incl[pl_incl["_Soll_EG"].isin(_invalid)].copy()
    print(f"  Anzahl: {len(pl_no_eg):,}")

    # Personalnummer-Spalte finden
    pnr_col_pl = next((c for c in pl_no_eg.columns
                       if c.lower() in ("personalnummer", "persnr", "personalnr")
                       or "personal" in c.lower()), None)
    print(f"  PNr-Spalte Planstellen: '{pnr_col_pl}'")

    if pnr_col_pl:
        pl_no_eg["_PNr"] = normalize_persnr(pl_no_eg[pnr_col_pl])
        n_besetzt   = int((~pl_no_eg["_PNr"].isin(["", "NAN", "NONE"])).sum())
        n_unbesetzt = int(pl_no_eg["_PNr"].isin(["", "NAN", "NONE"]).sum())
        print(f"  Davon besetzt   (PNr vorhanden):  {n_besetzt:,}")
        print(f"  Davon unbesetzt (PNr leer):       {n_unbesetzt:,}")
    else:
        print("  [WARNUNG] Keine PNr-Spalte gefunden.")
        sys.exit(1)

    # ── 6. Nur besetzte Faelle weiterverfolgen ────────────────────────────────
    pl_no_eg_besetzt = pl_no_eg[~pl_no_eg["_PNr"].isin(["", "NAN", "NONE"])].copy()

    if len(pl_no_eg_besetzt) == 0:
        print("\n  => Keine besetzten Planstellen ohne Soll-EG gefunden.")
        return

    hr("SCHRITT 6 — Stellenmerkmale der betroffenen Planstellen")

    # Verfuegbare Spalten anzeigen
    candidate_cols = {
        "Bezeichnung":     next((c for c in pl_no_eg_besetzt.columns if "bezeichnung" in c.lower()), None),
        "OE-Kuerzel":      oe_col_pl,
        "OE-Name":         next((c for c in pl_no_eg_besetzt.columns if "organisationseinheit" in c.lower()), None),
        "Sollarbeitszeit": next((c for c in pl_no_eg_besetzt.columns if "sollarbeitszeit" in c.lower() or "soll_az" in c.lower()), None),
        "Stellen-ID":      next((c for c in pl_no_eg_besetzt.columns if "stellen" in c.lower() and "id" in c.lower()), None),
        "Stellenart":      next((c for c in pl_no_eg_besetzt.columns if "art" in c.lower() and "stell" in c.lower()), None),
        "Spalte H (roh)":  col_h_name,
        "Spalte I (roh)":  col_i_name if col_i_name in pl_no_eg_besetzt.columns else None,
    }

    show_cols_pl = [v for v in candidate_cols.values() if v is not None and v in pl_no_eg_besetzt.columns]
    show_cols_pl = list(dict.fromkeys(show_cols_pl))  # deduplizieren

    print(f"  {len(pl_no_eg_besetzt)} Planstellen ohne Soll-EG, aber besetzt:\n")
    print(pl_no_eg_besetzt[show_cols_pl + ["_PNr"]].to_string(index=False))

    # Verteilung nach OE
    print(f"\n{SEP2}")
    print("  Verteilung nach OE:")
    print(pl_no_eg_besetzt[oe_col_pl].value_counts().to_string())

    # Rohwerte Spalte H und I
    print(f"\n{SEP2}")
    print(f"  Rohwerte '{col_h_name}' (Spalte H):")
    print(pl_no_eg_besetzt[col_h_name].value_counts(dropna=False).to_string())
    if col_i_name in pl_no_eg_besetzt.columns:
        print(f"\n  Rohwerte '{col_i_name}' (Spalte I):")
        print(pl_no_eg_besetzt[col_i_name].value_counts(dropna=False).to_string())

    # ── 7. Mitarbeiter-Seite: TrfGr der betroffenen Personen ─────────────────
    hr("SCHRITT 7 — Mitarbeiter-Abgleich: Wer sind die Personen?")

    pnr_col_ma = next((c for c in ma.columns if "persnr" in c.lower()
                       or ("personal" in c.lower() and "nr" in c.lower())), None)
    trfgr_col  = next((c for c in ma.columns if "trfgr" in c.lower()
                       or "tarifgruppe" in c.lower()), None)
    print(f"  PNr-Spalte Mitarbeiter: '{pnr_col_ma}'")
    print(f"  TrfGr-Spalte:          '{trfgr_col}'")

    if pnr_col_ma is None or trfgr_col is None:
        print("  [WARNUNG] PNr- oder TrfGr-Spalte nicht gefunden.")
        return

    ma["_PNr"] = normalize_persnr(ma[pnr_col_ma])

    # Stichtag-Filter auf Mitarbeiter
    ma["Eintritt"] = pd.to_datetime(ma.get("Eintritt"), errors="coerce")
    ma["Austritt"] = pd.to_datetime(ma.get("Austritt"), errors="coerce")
    ma.loc[ma["Austritt"].dt.year >= 9000, "Austritt"] = pd.NaT
    ma_aktiv = ma[
        (ma["Eintritt"].isna() | (ma["Eintritt"] <= STICHTAG)) &
        (ma["Austritt"].isna() | (ma["Austritt"] > STICHTAG))
    ].copy()

    affected_pnr = set(pl_no_eg_besetzt["_PNr"].unique())
    ma_affected = ma_aktiv[ma_aktiv["_PNr"].isin(affected_pnr)].copy()

    print(f"\n  Betroffene PNrs (Planstellen-Seite): {len(affected_pnr)}")
    print(f"  Davon in Mitarbeiter-Daten gefunden: {len(ma_affected)}")

    # Entgeltgruppen der betroffenen Personen
    print(f"\n{SEP2}")
    print("  Ist-EG (TrfGr) der betroffenen Personen:")
    eg_dist = (ma_affected[trfgr_col]
               .astype(str).str.strip().str.upper()
               .value_counts(dropna=False))
    print(eg_dist.to_string())

    # Detailtabelle Person × Planstelle
    print(f"\n{SEP2}")
    print("  Detailansicht: Planstelle JOIN Person\n")

    ma_candidate_cols = {
        "Name":           next((c for c in ma_affected.columns if "nachname" in c.lower() or c.lower() == "name"), None),
        "Vorname":        next((c for c in ma_affected.columns if "vorname" in c.lower()), None),
        "Funktion":       next((c for c in ma_affected.columns if "funktion" in c.lower() or "stelle" in c.lower()), None),
        "TrfGr":          trfgr_col,
        "Beschaeftigung": next((c for c in ma_affected.columns if "beschaeftigung" in c.lower() or "art" in c.lower()), None),
        "OE":             next((c for c in ma_affected.columns if "org" in c.lower() or "kuerzel" in c.lower()), None),
    }
    show_cols_ma = [v for v in ma_candidate_cols.values() if v is not None and v in ma_affected.columns]
    show_cols_ma = list(dict.fromkeys(["_PNr"] + show_cols_ma))

    # Join: Planstelle (OE, Bezeichnung, Spalte H/I) + Person (TrfGr, Name)
    join_left = pl_no_eg_besetzt[show_cols_pl + ["_PNr"]].copy()
    join_right = ma_affected[show_cols_ma].copy()
    detail = join_left.merge(join_right, on="_PNr", how="left")
    print(detail.to_string(index=False))

    # ── 8. Zusammenfassung ────────────────────────────────────────────────────
    hr("ZUSAMMENFASSUNG")
    print(f"  Planstellen in inkludierten OEs gesamt:           {len(pl_incl):,}")
    print(f"  Davon ohne Soll-EG (weder Spalte H noch I):       {n_ohne_soll_eg:,}")
    print(f"    - Davon unbesetzt:                              {n_unbesetzt:,}")
    print(f"    - Davon besetzt mit eingruppierter Person:      {len(pl_no_eg_besetzt):,}")
    print(f"  Eindeutige Personen betroffen:                    {len(affected_pnr):,}")
    print(f"  Davon in aktiven Mitarbeiterdaten gefunden:       {len(ma_affected):,}")
    if len(ma_affected) > 0:
        print(f"\n  Ist-EG-Verteilung dieser Personen:")
        for eg in TARIFF_GROUPS:
            n = int((ma_affected[trfgr_col].astype(str).str.strip().str.upper() == eg).sum())
            if n > 0:
                print(f"    {eg:6s}: {n}")
        n_other = int((~ma_affected[trfgr_col].astype(str).str.strip().str.upper().isin(TARIFF_GROUPS)).sum())
        if n_other > 0:
            print(f"    Sonstige: {n_other}")

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    run()
