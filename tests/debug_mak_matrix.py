"""
debug_mak_matrix.py
====================
Validiert die MAK-Werte pro Entgeltgruppe x Erfahrungsstufe gegen
den Dashboard-Export 'ist_mak_verguetungsklassen_keine Sonderrollen.xlsx'.

Formel:  MAK = BsGrd / 100  (summiert je Zelle, nicht nunique)

Ausfuehren (aus KSK_Layout/):
    py -X utf8 tests/debug_mak_matrix.py
"""

import os
import sys
import pandas as pd

# ── Pfade ─────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DIR  = os.path.join(BASE_DIR, "..", "Original-Daten")
DASHBOARD_DIR = os.path.join(BASE_DIR, "..")

MITARBEITER_FILE = os.path.join(ORIGINAL_DIR, "Mitarbeiter.xlsx")
PLANSTELLEN_FILE = os.path.join(ORIGINAL_DIR, "Planstellen.XLSX")
REFERENCE_FILE   = os.path.join(DASHBOARD_DIR,
                                "ist_mak_verguetungsklassen_keine Sonderrollen.xlsx")

STICHTAG = pd.Timestamp("2025-12-31")

# Exklusionen (aus user_settings.json)
EXCLUSIONS = {
    "vorstand":  True,
    "ruhend_bv": True,
    "org_units": [
        "9900","9910","9920","9921","9940","9941","9945",
        "9960","9970","9971","9972","9973","9975",
        "9980","9981","9990","9999","99XX",
    ],
}

SEP  = "=" * 75
SEP2 = "-" * 75

def hr(title=""):
    print("\n" + SEP)
    if title:
        print("  " + title)
        print(SEP)


def normalize_trfgr(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)


def clean_step_series(s: pd.Series) -> pd.Series:
    s2 = s.astype(str).str.strip()
    s2 = s2.str.replace("+", "", regex=False).str.replace("-", "", regex=False)
    return pd.to_numeric(s2, errors="coerce").fillna(4).astype(int)


def apply_org_exclusion_mask(ou_series: pd.Series, org_units: list) -> pd.Series:
    """Gibt True fuer Zeilen zurueck, die exkludiert werden sollen."""
    s = ou_series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    explicit = [u for u in org_units if u != "99XX"]
    mask = s.isin(explicit)
    if "99XX" in org_units:
        mask |= s.str.startswith("99") & ~s.isin(set(explicit))
    return mask


def run():
    print("\n" + SEP)
    print("  MAK-Matrix Validierung (EG x Stufe)")
    print("  Stichtag: " + str(STICHTAG.date()))
    print(SEP)

    # ── 1. Dateien laden ─────────────────────────────────────────────────────
    for path, name in [(MITARBEITER_FILE, "Mitarbeiter.xlsx"),
                       (PLANSTELLEN_FILE, "Planstellen.XLSX"),
                       (REFERENCE_FILE,   "Referenz-Excel")]:
        if not os.path.exists(path):
            print(f"[FEHLER] Datei nicht gefunden: {path}")
            sys.exit(1)

    sys.path.insert(0, BASE_DIR)
    from abgaenge.schemas import normalize_persnr

    print("\n  Lade Daten...")
    ma = pd.read_excel(MITARBEITER_FILE)
    pl = pd.read_excel(PLANSTELLEN_FILE)
    ref = pd.read_excel(REFERENCE_FILE, sheet_name="Daten")

    print(f"  Mitarbeiter.xlsx: {len(ma)} Zeilen")
    print(f"  Planstellen.XLSX: {len(pl)} Zeilen")

    # ── 2. PersNr normalisieren ───────────────────────────────────────────────
    ma["PersNr_norm"] = normalize_persnr(ma["PersNr"])
    pl["PersNr_norm"] = normalize_persnr(pl["Personalnummer"])

    # ── 3. Stichtag-Filter ───────────────────────────────────────────────────
    hr("SCHRITT 1 — Stichtag-Filter")
    ma["Eintritt"] = pd.to_datetime(ma["Eintritt"], errors="coerce")
    ma["Austritt"] = pd.to_datetime(ma["Austritt"], errors="coerce")
    ma["Austritt"] = ma["Austritt"].where(ma["Austritt"].dt.year != 9999, other=pd.NaT)

    before = len(ma)
    ma = ma[ma["Eintritt"] <= STICHTAG]
    ma = ma[(ma["Austritt"].isna()) | (ma["Austritt"] >= STICHTAG)]
    print(f"  {before} -> {len(ma)} Zeilen (nach Stichtag-Filter)")

    # ── 4. Exklusionen auf Mitarbeiter anwenden (fuer Personen-Filter) ────────
    hr("SCHRITT 2 — Personenfilter (Vorstand, Ruhendes BV)")

    # Vorstand
    before = len(ma)
    if EXCLUSIONS.get("vorstand") and "MitarbGruppenbez." in ma.columns:
        ma = ma[ma["MitarbGruppenbez."] != "Vorstand"]
        print(f"  Vorstand:     {before} -> {len(ma)} (-{before - len(ma)})")
    else:
        print(f"  Vorstand:     uebersprungen")

    # Ruhendes BV
    before = len(ma)
    if EXCLUSIONS.get("ruhend_bv") and "Status kundenindividuell" in ma.columns:
        mask_ruhend = (
            ma["Status kundenindividuell"].astype(str).str.strip()
            .isin(["Ruhendes Beschäftigungsverhältnis",
                   "Ruhendes Beschaeftigungsverhaeltnis"])
        )
        ma = ma[~mask_ruhend]
        print(f"  Ruhendes BV:  {before} -> {len(ma)} (-{before - len(ma)})")
    else:
        print(f"  Ruhendes BV:  uebersprungen")

    print(f"  Verbleibende Personen nach Personenfilter: {len(ma)}")

    # ── 5. OE-Exklusion auf Planstellen + Merge ──────────────────────────────
    hr("SCHRITT 3 — OE-Exklusion auf Planstellen + Merge (Planstellen als Basis)")
    #
    # WICHTIG: Das Dashboard startet von ALLEN Planstellen-Zeilen (nicht dedupliziert).
    # Personen mit Mehrfachplanstellen erhalten mehrere Zeilen → MAK wird ueber alle
    # Planstellen summiert. Deshalb NICHT deduplizieren, sondern alle Zeilen behalten.
    #
    ex_units = EXCLUSIONS.get("org_units", [])
    if ex_units and "Kürzel OrgEinheit" in pl.columns:
        mask_pl_excl = apply_org_exclusion_mask(pl["Kürzel OrgEinheit"], ex_units)
        pl_active = pl[~mask_pl_excl].copy()
        print(f"  Planstellen total: {len(pl)}  | nach OE-Exklusion: {len(pl_active)}")
    else:
        pl_active = pl.copy()
        print(f"  OE-Exklusion: uebersprungen ({len(pl_active)} Planstellen)")

    # Nur Planstellen fuer Personen, die nach Personenfilter verbleiben
    pl_active = pl_active[pl_active["PersNr_norm"].isin(ma["PersNr_norm"])].copy()
    print(f"  Planstellen nach Personenfilter: {len(pl_active)}")
    print(f"  Unique Personen: {pl_active['PersNr_norm'].nunique()}")

    # Mehrfachplanstellen-Info
    pl_counts = pl_active.groupby("PersNr_norm").size()
    mehrfach = pl_counts[pl_counts > 1]
    if not mehrfach.empty:
        print(f"  Personen mit Mehrfachplanstellen: {len(mehrfach)} "
              f"(+{pl_counts[mehrfach.index].sum() - len(mehrfach)} extra Planstellen)")

    # Mitarbeiter-Daten auf Planstellen mergen (wie combine_to_snapshot)
    ma_cols = ["PersNr_norm", "TrfGr", "St", "BsGrd", "Status kundenindividuell",
               "MitarbGruppenbez."]
    ma_cols = [c for c in ma_cols if c in ma.columns]
    merged = pl_active.merge(ma[ma_cols], on="PersNr_norm", how="left")
    print(f"  Merged Zeilen: {len(merged)}")
    print(f"  BsGrd vorhanden: {merged['BsGrd'].notna().sum()} / {len(merged)}")

    # ── 6. (Vakante Planstellen-Zeilen haben keine Person → BsGrd=NaN=0) ─────
    # Planstellen ohne zugeordnete Person (Is_Vacant) wurden bereits entfernt,
    # da pl_active nur Planstellen fuer Personen in ma enthaelt.

    # ── 7. TrfGr + Stufe normalisieren ───────────────────────────────────────
    merged["TrfGr_c"] = normalize_trfgr(merged["TrfGr"])
    merged["St_c"]    = clean_step_series(merged["St"])

    # ── 8. MAK berechnen (BsGrd / 100) ───────────────────────────────────────
    hr("SCHRITT 5 — MAK-Berechnung (BsGrd / 100)")
    merged["BsGrd_num"] = pd.to_numeric(merged["BsGrd"], errors="coerce").fillna(0)
    merged["MAK"]       = merged["BsGrd_num"] / 100.0

    print(f"  Gesamt-MAK (alle EG): {merged['MAK'].sum():.4f}")
    print(f"  Personen mit BsGrd=0: {(merged['BsGrd_num'] == 0).sum()}")
    print(f"  Personen mit BsGrd>0: {(merged['BsGrd_num'] > 0).sum()}")

    # ── 9. Matrix aufbauen: MAK summiert je (TrfGr, Stufe) ──────────────────
    hr("SCHRITT 6 — Berechnete MAK-Matrix")
    mak_matrix = (
        merged.groupby(["TrfGr_c", "St_c"])["MAK"]
        .sum()
        .unstack(fill_value=0.0)
        .rename_axis(None, axis=1)
    )
    mak_matrix.columns = [f"Stufe {int(c)}" for c in mak_matrix.columns]
    mak_matrix["Gesamt"] = mak_matrix.sum(axis=1)
    mak_matrix.index.name = "Entgeltgruppe"

    pd.set_option("display.float_format", "{:.4f}".format)
    print(mak_matrix.to_string())
    print(f"\n  Gesamt-MAK (berechnete Matrix): {mak_matrix['Gesamt'].sum():.4f}")

    # ── 10. Referenz-Matrix aufbereiten ──────────────────────────────────────
    hr("SCHRITT 7 — Referenz-Matrix (Dashboard-Export)")
    ref = ref.set_index("Entgeltgruppe")
    print(ref.to_string())
    print(f"\n  Gesamt-MAK (Referenz): {ref['Gesamt'].sum():.4f}")

    # ── 11. Zellenweiser Vergleich ────────────────────────────────────────────
    hr("SCHRITT 8 — Vergleich (berechnete Matrix vs. Referenz)")

    all_eg  = sorted(set(mak_matrix.index) | set(ref.index))
    all_col = sorted(
        set(mak_matrix.columns) | set(ref.columns),
        key=lambda c: (c == "Gesamt", c)
    )

    tol = 0.0005  # Toleranz fuer Rundungsdifferenzen (< 0.1 EUR bei Std-Stundensatz)

    diff_rows = []
    ok_count = 0

    for eg in all_eg:
        for col in all_col:
            val_calc = mak_matrix.loc[eg, col] if (eg in mak_matrix.index and col in mak_matrix.columns) else 0.0
            val_ref  = ref.loc[eg, col]         if (eg in ref.index      and col in ref.columns)         else 0.0
            delta    = val_calc - val_ref

            if abs(delta) > tol:
                diff_rows.append({
                    "Entgeltgruppe": eg,
                    "Spalte":        col,
                    "Berechnet":     round(val_calc, 4),
                    "Referenz":      round(val_ref, 4),
                    "Delta":         round(delta, 4),
                })
            else:
                ok_count += 1

    total = ok_count + len(diff_rows)
    print(f"\n  Zellen uebereinstimmend: {ok_count}/{total}")
    print(f"  Zellen abweichend:       {len(diff_rows)}/{total}")

    if not diff_rows:
        print("\n  Keine Abweichungen — MAK-Werte korrekt.")
    else:
        diff_df = pd.DataFrame(diff_rows)
        print("\n  Abweichende Zellen:")
        print(diff_df.to_string(index=False))

        # Detail: Welche Personen tragen zu abweichenden Zellen bei?
        hr("DETAIL — Personen in abweichenden Zellen")
        for row in diff_rows:
            eg  = row["Entgeltgruppe"]
            col = row["Spalte"]
            if col == "Gesamt":
                continue
            try:
                stufe = int(col.replace("Stufe ", ""))
            except ValueError:
                continue

            cell = merged[(merged["TrfGr_c"] == eg) & (merged["St_c"] == stufe)]
            if cell.empty:
                continue

            print(f"\n  {eg} / {col}:  berechnet={row['Berechnet']:.4f}  "
                  f"referenz={row['Referenz']:.4f}  delta={row['Delta']:.4f}")
            show = [c for c in ["PersNr_norm", "TrfGr", "St", "BsGrd_num", "MAK",
                                 "Status kundenindividuell", "Kürzel OrgEinheit"] if c in cell.columns]
            print(cell[show].to_string(index=False))

    # ── 12. Excel-Export ──────────────────────────────────────────────────────
    out_path = os.path.join(DASHBOARD_DIR, "mak_validierung.xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        mak_matrix.round(4).to_excel(writer, sheet_name="Berechnet")
        ref.round(4).to_excel(writer, sheet_name="Referenz")
        if diff_rows:
            pd.DataFrame(diff_rows).to_excel(writer, sheet_name="Abweichungen", index=False)
        # Detail-Daten pro Person
        detail_cols = [c for c in ["PersNr_norm", "TrfGr_c", "St_c", "BsGrd_num", "MAK",
                                    "Status kundenindividuell", "MitarbGruppenbez.",
                                    "Kürzel OrgEinheit"] if c in merged.columns]
        merged[detail_cols].sort_values(["TrfGr_c", "St_c"]).to_excel(
            writer, sheet_name="Detail_Personen", index=False
        )

    print("\n\n  Excel-Export: " + os.path.basename(out_path))
    print("\n" + SEP + "\n")


if __name__ == "__main__":
    run()
