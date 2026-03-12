"""
debug_soll_ist_koepfe.py
========================
Validiert die Soll-Ist-Köpfe-Matrix aus dem Dashboard-Export gegen eine
vollständig unabhängig aus den Input-Daten rekonstruierte Matrix.

Zielstruktur (Export-Sheet 'Soll-Ist-Köpfe'):
  Zeilen  = Soll-EG  (Bewertung Tarifgruppe aus Planstellen, Spalte H)
  Spalten = Ist-EG   (TrfGr aus Mitarbeiter, Spalte Q) + "Unbesetzt" + "Gesamt"

Ausführen (aus KSK_Layout/):
    py -X utf8 tests/debug_soll_ist_koepfe.py
"""

import os
import sys
import json
import pandas as pd

# ── Pfade ─────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DIR  = os.path.join(BASE_DIR, "..", "Original-Daten")
VALIDATION_DIR = os.path.join(BASE_DIR, "..", "validation_entgeltgruppe")
SETTINGS_FILE = os.path.join(BASE_DIR, "config", "user_settings.json")

MITARBEITER_FILE = os.path.join(ORIGINAL_DIR, "Mitarbeiter.xlsx")
PLANSTELLEN_FILE = os.path.join(ORIGINAL_DIR, "Planstellen.XLSX")
REFERENCE_FILE   = os.path.join(VALIDATION_DIR,
                                "ist_soll_koepfe _aufstellung_keine Sonderrollen.xlsx")

# ── Kanonische EG-Reihenfolge (aus config/settings.py TARIFF_GROUPS) ──────────
TARIFF_GROUPS = [
    "E1","E2","E2U","E3","E4","E5","E6","E7","E8",
    "E9A","E9B","E9C","E10","E11","E12","E13","E14","E15","E15U",
]
EG_ORDER = {g: i for i, g in enumerate(TARIFF_GROUPS)}

SEP  = "=" * 75
SEP2 = "-" * 75


def hr(title=""):
    print("\n" + SEP)
    if title:
        print("  " + title)
        print(SEP)


def normalize_eg(s: pd.Series) -> pd.Series:
    """Normalisiert EG-Strings: strip + upper + Leerzeichen entfernen."""
    return (s.astype(str)
             .str.strip()
             .str.upper()
             .str.replace(" ", "", regex=False))


def normalize_persnr(s: pd.Series) -> pd.Series:
    """Normalisiert PersNr: float '2915.0' → '2915'."""
    return (s.astype(str)
             .str.strip()
             .str.replace(r"\.0$", "", regex=True)
             .str.upper())


def apply_oe_exclusion(ou_series: pd.Series, org_units: list) -> pd.Series:
    """Gibt True für Zeilen zurück, die exkludiert werden sollen."""
    s = ou_series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    explicit = [u for u in org_units if u != "99XX"]
    mask = s.isin(explicit)
    if "99XX" in org_units:
        mask = mask | (s.str.startswith("99") & ~s.isin(set(explicit)))
    return mask


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def run():
    print("\n" + SEP)
    print("  Soll-Ist-Köpfe Validierung")
    print("  Prüfung: Dashboard-Export vs. unabhängige Rekonstruktion aus Input-Daten")
    print(SEP)

    # ── 0. Dateien prüfen ─────────────────────────────────────────────────────
    for path, name in [
        (MITARBEITER_FILE, "Mitarbeiter.xlsx"),
        (PLANSTELLEN_FILE, "Planstellen.XLSX"),
        (REFERENCE_FILE,   "Referenz-Export"),
    ]:
        if not os.path.exists(path):
            print(f"[FEHLER] Datei nicht gefunden: {path}")
            sys.exit(1)

    # ── 1. Settings laden ─────────────────────────────────────────────────────
    settings    = load_settings()
    ex          = settings.get("exclusions", {})
    ex_units    = ex.get("org_units", [])
    excl_vorstand  = ex.get("vorstand",  True)
    excl_ruhend    = ex.get("ruhend_bv", True)
    stichtag_raw   = settings.get("stichtag", "2025-12-31")
    STICHTAG       = pd.Timestamp(stichtag_raw)

    hr("SCHRITT 0 — Einstellungen")
    print(f"  Stichtag:          {STICHTAG.date()}")
    print(f"  Excl. Vorstand:    {excl_vorstand}")
    print(f"  Excl. Ruhendes BV: {excl_ruhend}")
    print(f"  OE-Exklusion:      {ex_units}")

    # ── 2. Input-Daten laden ──────────────────────────────────────────────────
    hr("SCHRITT 1 — Input-Daten laden")
    ma = pd.read_excel(MITARBEITER_FILE)
    pl = pd.read_excel(PLANSTELLEN_FILE)
    print(f"  Mitarbeiter.xlsx:  {len(ma)} Zeilen")
    print(f"  Planstellen.XLSX:  {len(pl)} Zeilen")

    # ── 3. Referenz-Export laden ──────────────────────────────────────────────
    hr("SCHRITT 2 — Referenz-Export laden")
    ref_raw = pd.read_excel(REFERENCE_FILE, sheet_name="Soll-Ist-Köpfe", header=None)
    # Erste Zeile = Spaltenheader, erste Spalte = Zeilenindex
    ref = ref_raw.iloc[1:].copy()
    ref.columns = ref_raw.iloc[0].tolist()
    ref = ref.rename(columns={ref.columns[0]: "Soll-EG"})
    ref = ref.set_index("Soll-EG")
    # Numerische Werte erzwingen
    ref = ref.apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
    print(f"  Referenz geladen: {ref.shape[0]} Zeilen × {ref.shape[1]} Spalten")
    print(f"  Soll-EG-Zeilen:   {list(ref.index)}")
    print(f"  Ist-EG-Spalten:   {list(ref.columns)}")
    print(f"  Gesamt-Summe:     {ref.loc['Gesamt', 'Gesamt'] if 'Gesamt' in ref.index else ref.values.sum()}")

    # ── 4. Mitarbeiter: Stichtag-Filter ───────────────────────────────────────
    hr("SCHRITT 3 — Mitarbeiter: Stichtag-Filter")
    ma["Eintritt"] = pd.to_datetime(ma["Eintritt"], errors="coerce")
    ma["Austritt"] = pd.to_datetime(ma["Austritt"], errors="coerce")
    # Jahr 9999 = kein Austritt → auf NaT setzen
    ma["Austritt"] = ma["Austritt"].where(ma["Austritt"].dt.year != 9999, other=pd.NaT)

    before = len(ma)
    ma = ma[ma["Eintritt"] <= STICHTAG]
    ma = ma[(ma["Austritt"].isna()) | (ma["Austritt"] >= STICHTAG)]
    print(f"  {before} → {len(ma)} Zeilen nach Stichtag-Filter")

    # ── 5. Mitarbeiter: Personenfilter ────────────────────────────────────────
    hr("SCHRITT 4 — Mitarbeiter: Personenfilter (Vorstand, Ruhendes BV)")

    # Vorstand
    before = len(ma)
    if excl_vorstand and "MitarbGruppenbez." in ma.columns:
        ma = ma[ma["MitarbGruppenbez."] != "Vorstand"]
        print(f"  Vorstand:     {before} → {len(ma)} (-{before - len(ma)})")
    else:
        print("  Vorstand:     übersprungen")

    # Ruhendes BV
    before = len(ma)
    if excl_ruhend and "Status kundenindividuell" in ma.columns:
        ruhend_mask = (
            ma["Status kundenindividuell"].astype(str).str.strip()
            .isin(["Ruhendes Beschäftigungsverhältnis",
                   "Ruhendes Beschaeftigungsverhaeltnis"])
        )
        ma = ma[~ruhend_mask]
        print(f"  Ruhendes BV:  {before} → {len(ma)} (-{before - len(ma)})")
    else:
        print("  Ruhendes BV:  übersprungen")

    print(f"  Verbleibende Personen: {len(ma)}")

    # ── 6. Planstellen: OE-Exklusion ─────────────────────────────────────────
    hr("SCHRITT 5 — Planstellen: OE-Exklusion")
    if ex_units and "Kürzel OrgEinheit" in pl.columns:
        mask_excl = apply_oe_exclusion(pl["Kürzel OrgEinheit"], ex_units)
        pl_incl = pl[~mask_excl].copy()
        print(f"  Planstellen gesamt:        {len(pl)}")
        print(f"  Planstellen exkludierte OE: {mask_excl.sum()}")
        print(f"  Planstellen inkludierte OE: {len(pl_incl)}")
    else:
        pl_incl = pl.copy()
        print("  OE-Exklusion: übersprungen")

    # ── 7. Soll-EG bestimmen: Spalte I (Text Gehaltsband) hat Vorrang ─────────
    #
    # Neue fachliche Logik:
    #   Vorrang: "Text Gehaltsband" (Spalte I) — oberes Ende des Gehaltsbandes
    #            z. B. "bis E11" → nach Bereinigung "E11"
    #   Fallback: "Bewertung Tarifgruppe" (Spalte H) — Basisbewertung
    #
    # Planstellen ohne verwertbare Soll-EG in beiden Spalten (Werkstudenten,
    # Reserve, Sonderrollen ohne EG) werden ausgeschlossen.
    #
    hr("SCHRITT 6 — Soll-EG bestimmen (Spalte I Vorrang, Fallback Spalte H)")

    _invalid_eg = {"", "NAN", "NONE"}

    def _clean_eg(s) -> str:
        if pd.isna(s):
            return ""
        v = str(s).strip().upper().replace(" ", "")
        if v.startswith("BIS"):
            v = v[3:].strip()
        return v

    col_h = pl_incl["Bewertung Tarifgruppe"].map(_clean_eg)
    if "Text Gehaltsband" in pl_incl.columns:
        col_i = pl_incl["Text Gehaltsband"].map(_clean_eg)
    else:
        col_i = pd.Series("", index=pl_incl.index)

    pl_incl = pl_incl.copy()
    pl_incl["_Soll_EG"] = col_i.where(~col_i.isin(_invalid_eg), other=col_h)

    before = len(pl_incl)
    pl_incl = pl_incl[~pl_incl["_Soll_EG"].isin(_invalid_eg)].copy()
    print(f"  {before} → {len(pl_incl)} Planstellen (nach Filter auf verwertbare Soll-EG)")
    print(f"  Entfernt: {before - len(pl_incl)} Planstellen ohne Soll-EG "
          f"(Werkstudent/in, Reserve, Sonderrollen ohne EG)")

    # Statistik: wie viele nutzen Spalte I vs. Fallback H
    uses_i = (~col_i.reindex(pl_incl.index).isin(_invalid_eg)).sum()
    uses_h = len(pl_incl) - uses_i
    print(f"  Soll-EG aus Spalte I (Text Gehaltsband):    {uses_i}")
    print(f"  Soll-EG aus Spalte H (Bewertung TrfGr, FB): {uses_h}")

    # ── 8. Schlüssel normalisieren ────────────────────────────────────────────
    hr("SCHRITT 7 — Schlüssel normalisieren")

    # Planstellen PersNr
    pl_incl["_PersNr"] = normalize_persnr(pl_incl["Personalnummer"].astype(str))
    pl_incl["_PersNr"] = pl_incl["_PersNr"].where(
        pl_incl["Personalnummer"].notna(), other=None
    )

    # Mitarbeiter PersNr
    ma["_PersNr"] = normalize_persnr(ma["PersNr"].astype(str))

    # Ist-EG Lookup-Tabelle: PersNr → normalisiertes TrfGr
    ma["_TrfGr"] = normalize_eg(ma["TrfGr"].fillna(""))
    persnr_to_trfgr = ma.set_index("_PersNr")["_TrfGr"].to_dict()

    print(f"  Lookup-Tabelle: {len(persnr_to_trfgr)} Personen")

    # Mehrfachplanstellen-Dokumentation (zählen pro Planstellen-Zeile, nicht per Person)
    pl_counts = pl_incl["_PersNr"].dropna().value_counts()
    mehrfach = pl_counts[pl_counts > 1]
    if not mehrfach.empty:
        print(f"\n  HINWEIS Mehrfachplanstellen:")
        print(f"  {len(mehrfach)} Personen haben >1 Planstelle in inkludierten OEs.")
        print(f"  Jede Planstellen-Zeile wird separat gezählt (Köpfe = Planstellen-Zeilen).")
        print(f"  Dies ist konsistent mit dem Dashboard (prepared_df = Planstellen als Basis).")
        total_extra = int(pl_counts[mehrfach.index].sum() - len(mehrfach))
        print(f"  Zusätzliche Zählungen durch Mehrfachplanstellen: +{total_extra}")

    # ── 9. Ist-EG je Planstellen-Zeile bestimmen ─────────────────────────────
    #
    # Logik (entspricht _build_soll_ist_pivot / render_ist_soll_koepfe_tab):
    #   Is_Vacant = True  → Personalnummer ist NaN (keine Person zugeordnet)
    #   Is_Vacant = False → Person gefunden, TrfGr aus Mitarbeiter.xlsx
    #   Falls Person nicht in Mitarbeiter (nach Filter): "Nicht gefunden"
    #   Falls TrfGr leer/NaN nach Lookup:               "Nicht gefunden"
    #
    hr("SCHRITT 8 — Ist-EG je Planstellen-Zeile bestimmen")

    IST_UNBESETZT  = "Unbesetzt"
    IST_NOT_FOUND  = "Nicht gefunden"

    def get_ist_eg(row):
        pnr = row["_PersNr"]
        if pnr is None or pd.isna(pnr) or str(pnr).strip().lower() in ("", "nan", "none"):
            return IST_UNBESETZT
        trfgr = persnr_to_trfgr.get(str(pnr), "")
        if not trfgr or trfgr.lower() in ("", "nan"):
            return IST_NOT_FOUND
        return trfgr

    pl_incl["_Ist_EG"] = pl_incl.apply(get_ist_eg, axis=1)

    print(f"  Unbesetzt:       {(pl_incl['_Ist_EG'] == IST_UNBESETZT).sum()}")
    print(f"  Nicht gefunden:  {(pl_incl['_Ist_EG'] == IST_NOT_FOUND).sum()}")
    print(f"  Besetzt (EG):    {(~pl_incl['_Ist_EG'].isin([IST_UNBESETZT, IST_NOT_FOUND])).sum()}")
    print(f"  Total:           {len(pl_incl)}")

    # ── 10. Pivot aufbauen ────────────────────────────────────────────────────
    hr("SCHRITT 9 — Pivot-Matrix aufbauen")

    pivot_raw = (
        pl_incl.groupby(["_Soll_EG", "_Ist_EG"])
        .size()
        .unstack(fill_value=0)
    )

    # Spaltenreihenfolge: EG-Spalten nach TARIFF_GROUPS, dann Sonderspalten
    ist_eg_cols = sorted(
        [c for c in pivot_raw.columns if c not in (IST_UNBESETZT, IST_NOT_FOUND)],
        key=lambda g: EG_ORDER.get(g, 999),
    )
    special_cols = [c for c in (IST_UNBESETZT, IST_NOT_FOUND) if c in pivot_raw.columns]
    ordered_cols = ist_eg_cols + special_cols

    pivot = pivot_raw.reindex(columns=ordered_cols, fill_value=0)
    pivot["Gesamt"] = pivot.sum(axis=1)

    # Zeilenreihenfolge nach TARIFF_GROUPS
    soll_order = sorted(pivot.index.tolist(), key=lambda g: EG_ORDER.get(g, 999))
    pivot = pivot.loc[soll_order]
    pivot.index.name = "Soll-EG"

    # Summenzeile
    gesamt_row = pivot.sum(axis=0).rename("Gesamt")
    pivot_full = pd.concat([pivot, gesamt_row.to_frame().T])

    print("  Berechnete Matrix (ohne Summenzeile):")
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.width", 200)
    print(pivot.to_string())
    print(f"\n  Gesamt-Summe (Köpfe): {int(pivot['Gesamt'].sum())}")

    # ── 11. Referenz-Matrix aufbereiten ──────────────────────────────────────
    hr("SCHRITT 10 — Referenz-Matrix aufbereiten")

    # Gesamt-Zeile und Gesamt-Spalte entfernen für Zell-Vergleich
    ref_data = ref.drop(index="Gesamt", errors="ignore").drop(columns="Gesamt", errors="ignore")
    ref_gesamt = ref.loc["Gesamt", "Gesamt"] if "Gesamt" in ref.index else None

    print(f"  Referenz-Gesamt: {int(ref_gesamt)}")
    print(f"  Berechneter Gesamt: {int(pivot['Gesamt'].sum())}")

    # ── 12. Zellweiser Vergleich ──────────────────────────────────────────────
    hr("SCHRITT 11 — Zellweiser Vergleich (berechnete Matrix vs. Referenz)")

    # Alle Soll-EG und Ist-EG aus beiden Quellen
    all_soll = sorted(
        set(pivot.index) | set(ref_data.index),
        key=lambda g: (EG_ORDER.get(g, 999), g),
    )
    all_ist = sorted(
        set(pivot.columns) | set(ref_data.columns),
        key=lambda c: (c == "Gesamt", EG_ORDER.get(c, 998), c),
    )

    diff_rows = []
    ok_count  = 0

    for soll in all_soll:
        for ist in all_ist:
            if ist == "Gesamt":
                continue
            calc = int(pivot.loc[soll, ist]) if (soll in pivot.index and ist in pivot.columns) else 0
            refv = int(ref_data.loc[soll, ist]) if (soll in ref_data.index and ist in ref_data.columns) else 0
            delta = calc - refv
            if delta != 0:
                diff_rows.append({
                    "Soll-EG":    soll,
                    "Ist-EG":     ist,
                    "Berechnet":  calc,
                    "Referenz":   refv,
                    "Delta":      delta,
                })
            else:
                ok_count += 1

    total_cells = ok_count + len(diff_rows)
    print(f"\n  Zellen übereinstimmend: {ok_count}/{total_cells}")
    print(f"  Zellen abweichend:      {len(diff_rows)}/{total_cells}")

    if not diff_rows:
        print("\n  ✅  Keine Abweichungen — Soll-Ist-Köpfe-Matrix ist korrekt.")
    else:
        diff_df = pd.DataFrame(diff_rows)
        print("\n  ❌  Abweichende Zellen:")
        print(diff_df.to_string(index=False))

        # Detail: Welche Planstellen tragen zu Abweichungen bei?
        hr("DETAIL — Planstellen-Zeilen in abweichenden Zellen")
        for row in diff_rows:
            soll = row["Soll-EG"]
            ist  = row["Ist-EG"]
            cell = pl_incl[
                (pl_incl["_Soll_EG"] == soll) & (pl_incl["_Ist_EG"] == ist)
            ]
            if cell.empty:
                print(f"\n  {soll} / {ist}: keine Planstellen-Zeilen gefunden")
                continue
            print(f"\n  {soll} / {ist}:  berechnet={row['Berechnet']}  "
                  f"referenz={row['Referenz']}  delta={row['Delta']}")
            show_cols = [c for c in [
                "Planstellennr", "Planstelle", "_PersNr",
                "Kürzel OrgEinheit", "_Soll_EG", "_Ist_EG",
            ] if c in cell.columns]
            print(cell[show_cols].to_string(index=False))

    # ── 13. Summenzeilen-Vergleich ────────────────────────────────────────────
    hr("SCHRITT 12 — Summenzeilen und Summenspalten prüfen")

    # Berechnete Summenzeile
    calc_row_sums = pivot["Gesamt"]
    ref_row_sums  = ref["Gesamt"] if "Gesamt" in ref.columns else None

    sum_diffs = []
    for soll in pivot.index:
        calc_s = int(calc_row_sums[soll]) if soll in calc_row_sums.index else 0
        ref_s  = int(ref.loc[soll, "Gesamt"]) if (soll in ref.index and "Gesamt" in ref.columns) else None
        if ref_s is not None and calc_s != ref_s:
            sum_diffs.append({"Soll-EG": soll, "Berechnet Gesamt": calc_s, "Referenz Gesamt": ref_s, "Delta": calc_s - ref_s})

    if sum_diffs:
        print("  Abweichungen in Summen-Spalte 'Gesamt':")
        print(pd.DataFrame(sum_diffs).to_string(index=False))
    else:
        print(f"  Summen-Spalte 'Gesamt': keine Abweichungen")
        print(f"  Gesamt-Total: {int(pivot['Gesamt'].sum())} (berechnet) vs. {int(ref_gesamt)} (Referenz)")

    # ── 14. Diagnose: Personen ohne TrfGr / Nicht gefunden ───────────────────
    not_found_pl = pl_incl[pl_incl["_Ist_EG"] == IST_NOT_FOUND]
    if not not_found_pl.empty:
        hr(f"DIAGNOSE — {len(not_found_pl)} Planstellen 'Nicht gefunden'")
        print("  Diese Planstellen haben eine Personalnummer, aber die Person wurde nach")
        print("  Stichtag-/Personenfilter nicht in Mitarbeiter.xlsx gefunden oder hat keine TrfGr.")
        show = [c for c in ["Planstellennr", "Planstelle", "_PersNr",
                             "Kürzel OrgEinheit", "_Soll_EG"] if c in not_found_pl.columns]
        print(not_found_pl[show].to_string(index=False))

    # ── 15. Excel-Export ──────────────────────────────────────────────────────
    out_path = os.path.join(BASE_DIR, "..", "soll_ist_koepfe_validierung.xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        pivot_full.to_excel(writer, sheet_name="Berechnet")
        ref.to_excel(writer, sheet_name="Referenz")
        if diff_rows:
            pd.DataFrame(diff_rows).to_excel(writer, sheet_name="Abweichungen", index=False)
        # Detail-Daten je Planstelle
        detail_cols = [c for c in [
            "Kürzel OrgEinheit", "Organisationseinheit", "Planstellennr",
            "Planstelle", "_PersNr", "_Soll_EG", "_Ist_EG",
        ] if c in pl_incl.columns]
        pl_incl[detail_cols].sort_values(["_Soll_EG", "_Ist_EG"]).to_excel(
            writer, sheet_name="Detail_Planstellen", index=False
        )

    print(f"\n  Excel-Export: {os.path.basename(out_path)}")
    print("\n" + SEP + "\n")


if __name__ == "__main__":
    run()
