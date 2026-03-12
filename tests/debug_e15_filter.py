"""
debug_e15_filter.py
===================
Diagnostikskript: Warum zählt das Dashboard X E15-Personen,
während die Mitarbeiter-Excel Y Zeilen zeigt?

Ausführen (aus KSK_Layout/):
    python tests/debug_e15_filter.py

Konfiguration: MITARBEITER_PATH und STICHTAG unten anpassen.
"""

import sys
import os

# ── Pfad konfigurieren ────────────────────────────────────────────────────────
# Passe diese Pfade an deine lokale Umgebung an:
MITARBEITER_PATH = r"C:\Pfad\zur\Mitarbeiter.xlsx"   # ← hier anpassen
STICHTAG         = "2025-12-31"
TRFGR_FILTER     = "E15"   # gesuchte Entgeltgruppe (normalisiert ohne Leerzeichen)

# Aktive Exklusionen (aus user_settings.json / Dashboard-Einstellungen):
EXCLUSIONS = {
    "vorstand":  True,   # ← auf False setzen wenn inaktiv
    "ruhend_bv": True,   # ← auf False setzen wenn inaktiv
    "org_units": [       # ← liste der aktiven PA-Bereiche
        "9900","9910","9920","9921","9940","9941","9945",
        "9960","9970","9971","9972","9973","99XX",
    ],
}

# Sidebar-Filter (Dashboard-Defaults):
FILTER_GESCHLECHT  = ["männlich", "weiblich"]   # "divers" fehlt im Default
FILTER_ARBEITSZEIT = ["Vollzeit", "Teilzeit", "Inaktiv"]
# ─────────────────────────────────────────────────────────────────────────────

import pandas as pd

SEP = "─" * 70

def hline(title=""):
    print(f"\n{SEP}")
    if title:
        print(f"  {title}")
        print(SEP)

def normalize_trfgr(s: pd.Series) -> pd.Series:
    """Normalisiert TrfGr: 'E 15' → 'E15', 'e15' → 'E15'"""
    return s.astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)

def clean_step_val(v) -> int:
    if pd.isna(v):
        return 4
    s = str(v).strip().replace("+","").replace("-","")
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 4


def run():
    # ── 0. Datei laden ────────────────────────────────────────────────────────
    if not os.path.exists(MITARBEITER_PATH):
        print(f"[FEHLER] Datei nicht gefunden: {MITARBEITER_PATH}")
        print("Bitte MITARBEITER_PATH im Skript anpassen.")
        sys.exit(1)

    print(f"Lade: {MITARBEITER_PATH}")
    df = pd.read_excel(MITARBEITER_PATH)
    print(f"Geladene Zeilen: {len(df)}")
    print(f"Spalten: {list(df.columns)}")

    stichtag = pd.Timestamp(STICHTAG)

    # ── 1. Rohdaten: alle TrfGr=E15 ──────────────────────────────────────────
    hline("SCHRITT 1 — Rohdaten: TrfGr = E15 (keine Filter)")
    if "TrfGr" not in df.columns:
        print("[WARNUNG] Spalte 'TrfGr' nicht gefunden!")
        return

    df["TrfGr_norm"] = normalize_trfgr(df["TrfGr"])
    e15_raw = df[df["TrfGr_norm"] == TRFGR_FILTER].copy()
    print(f"  Zeilen gesamt (Positionen):  {len(e15_raw)}")

    id_col = next((c for c in ("PersNr","Personalnummer") if c in e15_raw.columns), None)
    if id_col:
        print(f"  Unique PersNr (Personen):    {e15_raw[id_col].nunique()}")
        dupes = e15_raw[e15_raw.duplicated(subset=id_col, keep=False)]
        if not dupes.empty:
            print(f"\n  ⚠  Mehrfachplanstellen ({len(dupes)} Zeilen für {dupes[id_col].nunique()} Personen):")
            for pid, grp in dupes.groupby(id_col):
                print(f"     PersNr {pid}: {len(grp)} Zeilen")
    else:
        print("  [WARNUNG] Kein PersNr/Personalnummer-Spalte gefunden")

    # ── 2. Stichtag-Filter ────────────────────────────────────────────────────
    hline("SCHRITT 2 — Stichtag-Filter (Eintritt ≤ Stichtag AND Austritt ≥ Stichtag)")
    work = e15_raw.copy()
    before = len(work)

    if "Eintritt" in work.columns:
        work["Eintritt_dt"] = pd.to_datetime(work["Eintritt"], errors="coerce")
        mask_ein = work["Eintritt_dt"] <= stichtag
    else:
        print("  [WARNUNG] Spalte 'Eintritt' fehlt — Stichtag-Filter übersprungen")
        mask_ein = pd.Series(True, index=work.index)

    if "Austritt" in work.columns:
        work["Austritt_dt"] = pd.to_datetime(work["Austritt"], errors="coerce")
        # 31.12.9999 = kein Austritt; NaT ebenfalls behalten
        mask_aus = (work["Austritt_dt"] >= stichtag) | work["Austritt_dt"].isna()
    else:
        print("  [WARNUNG] Spalte 'Austritt' fehlt — Stichtag-Filter übersprungen")
        mask_aus = pd.Series(True, index=work.index)

    dropped = work[~(mask_ein & mask_aus)]
    work = work[mask_ein & mask_aus]
    print(f"  Vorher: {before}  →  Nachher: {len(work)}  (–{before - len(work)} Zeilen)")
    if not dropped.empty and id_col:
        print(f"  Herausgefilterter PersNr: {list(dropped[id_col].dropna().astype(str))}")

    # ── 3. Is_Vacant ─────────────────────────────────────────────────────────
    hline("SCHRITT 3 — Vakante Positionen (Is_Vacant / BsGrd=0)")
    before = len(work)
    if "Is_Vacant" in work.columns:
        dropped = work[work["Is_Vacant"] == True]
        work = work[work["Is_Vacant"] != True]
        print(f"  Via Is_Vacant:  Vorher {before} → Nachher {len(work)}  (–{len(dropped)})")
    elif "BsGrd" in work.columns:
        dropped = work[pd.to_numeric(work["BsGrd"], errors="coerce").fillna(0) == 0]
        work = work[pd.to_numeric(work["BsGrd"], errors="coerce").fillna(0) > 0]
        print(f"  Via BsGrd==0:   Vorher {before} → Nachher {len(work)}  (–{len(dropped)})")
        if not dropped.empty and id_col:
            print(f"  Herausgefilterter PersNr: {list(dropped[id_col].dropna().astype(str))}")
    else:
        print("  Spalten 'Is_Vacant' und 'BsGrd' fehlen — übersprungen")

    # ── 4. Exklusion: Vorstand ────────────────────────────────────────────────
    hline("SCHRITT 4 — Exklusion: Vorstand")
    before = len(work)
    if EXCLUSIONS.get("vorstand") and "MitarbGruppenbez." in work.columns:
        dropped = work[work["MitarbGruppenbez."] == "Vorstand"]
        work = work[work["MitarbGruppenbez."] != "Vorstand"]
        print(f"  Vorher {before} → Nachher {len(work)}  (–{len(dropped)})")
        if not dropped.empty and id_col:
            print(f"  Herausgefiltert: {list(dropped[id_col].dropna().astype(str))}")
    else:
        print(f"  Übersprungen (aktiv={EXCLUSIONS.get('vorstand')}, Spalte vorhanden={'MitarbGruppenbez.' in work.columns})")

    # ── 5. Exklusion: Ruhendes BV ─────────────────────────────────────────────
    hline("SCHRITT 5 — Exklusion: Ruhendes BV")
    before = len(work)
    if EXCLUSIONS.get("ruhend_bv") and "Status kundenindividuell" in work.columns:
        mask_ruhend = work["Status kundenindividuell"].astype(str).str.strip() == "Ruhendes Beschäftigungsverhältnis"
        dropped = work[mask_ruhend]
        work = work[~mask_ruhend]
        print(f"  Vorher {before} → Nachher {len(work)}  (–{len(dropped)})")
        if not dropped.empty and id_col:
            print(f"  Herausgefiltert: {list(dropped[id_col].dropna().astype(str))}")
    else:
        print(f"  Übersprungen (aktiv={EXCLUSIONS.get('ruhend_bv')}, Spalte vorhanden={'Status kundenindividuell' in work.columns})")

    # ── 6. Exklusion: PA-Bereiche ─────────────────────────────────────────────
    hline("SCHRITT 6 — Exklusion: PA-Bereiche (org_units)")
    before = len(work)
    ex_units = EXCLUSIONS.get("org_units", [])
    if ex_units and "Kürzel OrgEinheit" in work.columns:
        s_ou = work["Kürzel OrgEinheit"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        mask_ou = s_ou.isin([u for u in ex_units if u != "99XX"])
        if "99XX" in ex_units:
            explicit = {u for u in ex_units if u != "99XX"}
            mask_ou |= s_ou.str.startswith("99") & ~s_ou.isin(explicit)
        dropped = work[mask_ou]
        work = work[~mask_ou]
        print(f"  Vorher {before} → Nachher {len(work)}  (–{len(dropped)})")
        if not dropped.empty and id_col:
            print(f"  Herausgefiltert: PersNr {list(dropped[id_col].dropna().astype(str))}, OEs {list(dropped['Kürzel OrgEinheit'].astype(str))}")
    else:
        print(f"  Übersprungen (org_units leer oder Spalte fehlt)")

    # ── 7. Sidebar-Filter: Geschlecht ─────────────────────────────────────────
    hline("SCHRITT 7 — Sidebar-Filter: Geschlecht")
    before = len(work)
    geschlecht_col = next((c for c in ("Text Geschlecht","Geschlecht","Gender") if c in work.columns), None)
    if geschlecht_col:
        unique_vals = work[geschlecht_col].dropna().unique()
        print(f"  Vorhandene Geschlecht-Werte: {list(unique_vals)}")
        dropped = work[~work[geschlecht_col].isin(FILTER_GESCHLECHT)]
        work = work[work[geschlecht_col].isin(FILTER_GESCHLECHT)]
        print(f"  Vorher {before} → Nachher {len(work)}  (–{len(dropped)})")
        if not dropped.empty and id_col:
            print(f"  Herausgefiltert: {list(dropped[id_col].dropna().astype(str))}")
    else:
        print("  Spalte 'Text Geschlecht' nicht gefunden — übersprungen")

    # ── 8. Sidebar-Filter: Arbeitszeit ────────────────────────────────────────
    hline("SCHRITT 8 — Sidebar-Filter: Arbeitszeit")
    before = len(work)
    az_col = next((c for c in ("Arbeitszeit","Teilzeit_Vollzeit","Beschäftigungsart") if c in work.columns), None)
    if az_col:
        unique_vals = work[az_col].dropna().unique()
        print(f"  Vorhandene Arbeitszeit-Werte: {list(unique_vals)}")
        dropped = work[~work[az_col].isin(FILTER_ARBEITSZEIT)]
        work = work[~work[az_col].isin(FILTER_ARBEITSZEIT) == False]
        print(f"  Vorher {before} → Nachher {len(work)}  (–{before - len(work)})")
    else:
        print(f"  Spalte 'Arbeitszeit' nicht gefunden — übersprungen")

    # ── 9. Finales Ergebnis ───────────────────────────────────────────────────
    hline("ERGEBNIS")
    print(f"  Zeilen nach allen Filtern (Positionen):  {len(work)}")
    if id_col:
        unique_count = work[id_col].nunique()
        print(f"  Unique PersNr (= Dashboard-Headcount):  {unique_count}")
        print()
        if unique_count != len(work):
            print(f"  ⚠  Differenz Zeilen vs. Personen: {len(work) - unique_count}")
            print("     → Einige Personen halten mehrere E15-Positionen (Mehrfachplanstellen)")
            dupes = work[work.duplicated(subset=id_col, keep=False)]
            for pid, grp in dupes.groupby(id_col):
                print(f"     PersNr {pid}: {len(grp)} Positionen")

    # ── 10. Detailliste ───────────────────────────────────────────────────────
    hline("DETAILLISTE — verbleibende Personen")
    show_cols = [c for c in [id_col, "TrfGr", "St", "BsGrd",
                              "Status kundenindividuell", "Text Geschlecht",
                              "Kürzel OrgEinheit", "Eintritt", "Austritt"] if c in work.columns]
    pd.set_option("display.max_rows", 50)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 200)
    print(work[show_cols].to_string(index=False))
    print(SEP)


if __name__ == "__main__":
    run()
