"""
debug_verguetungsmatrix.py
==========================
Vergleicht die Vergütungsklassen-Matrix (Entgeltgruppe × Erfahrungsstufe)
zwischen dem Dashboard-Snapshot und der rohen Mitarbeiter.xlsx.

Ausführen (aus KSK_Layout/):
    python tests/debug_verguetungsmatrix.py

Was das Skript prüft
--------------------
A) Dashboard-Sicht:
   Liest den vorberechneten Snapshot (251218_*.xlsx, Sheet "snapshot_detail")
   oder direkt Mitarbeiter.xlsx + Planstellen.XLSX aus Original-Daten.
   Wendet exakt dieselbe Logik an wie Kompakt > IST-Köpfe > Vergütungsklassen:
     - Is_Vacant == False
     - TrfGr normalisiert (strip, upper, kein Leerzeichen)
     - Stufe bereinigt (float→int, +/- entfernt, NaN → 4)
     - Grupierung: (TrfGr, Stufe) → PersNr.nunique()

B) Excel-Sicht (Mitarbeiter.xlsx direkt):
   Reiner Rohdaten-Filter: TrfGr-Spalte, ohne Merge mit Planstellen,
   ohne Is_Vacant-Filterung → zählt Zeilen (Positionen), nicht Personen.

C) Vergleich:
   Beide Matrizen werden gegenübergestellt. Abweichungen werden rot markiert.
   Für jede abweichende Zelle werden die betroffenen PersNr ausgegeben.
"""

import os
import sys
import pandas as pd
import numpy as np

# ── Konfiguration ─────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DIR   = os.path.join(BASE_DIR, "..", "Original-Daten")

# Snapshot-Excel (vorberechnete Daten, Priorität 1)
SNAPSHOT_FILE  = os.path.join(BASE_DIR, "..", "Original-Daten",
                               "251218_Daten Baseline Forecast KSKBB_v1.xlsx")
SNAPSHOT_SHEET = "snapshot_detail"

# Rohdaten (Priorität 2, wenn Snapshot nicht vorhanden)
MITARBEITER_FILE  = os.path.join(ORIGINAL_DIR, "Mitarbeiter.xlsx")
PLANSTELLEN_FILE  = os.path.join(ORIGINAL_DIR, "Planstellen.XLSX")

STICHTAG = pd.Timestamp("2025-12-31")

# Sidebar-Defaults des Dashboards (aus render_global_filters):
#   Geschlecht = ["m", "w"]  →  Text Geschlecht in ["männlich", "weiblich"]
#   Arbeitszeit = ["Vollzeit", "Teilzeit", "Inaktiv"]
# Setze auf None, um diesen Filter zu deaktivieren:
FILTER_GESCHLECHT  = ["männlich", "weiblich"]   # None = kein Filter
FILTER_ARBEITSZEIT = None                         # None = kein Filter

# Exklusionen (aus user_settings.json lesen oder hier hart setzen)
EXCLUSIONS = {
    "vorstand":  False,
    "ruhend_bv": False,
    "org_units": [],
}

# ─────────────────────────────────────────────────────────────────────────────

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
    """Exakt dieselbe Logik wie combine_to_snapshot (vektorisiert)."""
    s2 = s.astype(str).str.strip()
    s2 = s2.str.replace("+", "", regex=False).str.replace("-", "", regex=False)
    numeric = pd.to_numeric(s2, errors="coerce")
    return numeric.fillna(4).astype(int)


def build_matrix_from_snapshot(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    Baut die Vergütungsklassen-Matrix exakt wie Kompakt > IST-Köpfe:
    - Is_Vacant == False
    - TrfGr normalisiert, Stufe bereinigt
    - PersNr.nunique() je (TrfGr, Stufe)
    """
    hr(f"MATRIX A — {label}")

    if "TrfGr" not in df.columns:
        print("  [FEHLER] Spalte 'TrfGr' fehlt!")
        return pd.DataFrame()
    if "St" not in df.columns:
        print("  [FEHLER] Spalte 'St' fehlt!")
        return pd.DataFrame()

    work = df.copy()
    work["TrfGr_c"] = normalize_trfgr(work["TrfGr"])
    work["St_c"]    = clean_step_series(work["St"])

    id_col = "PersNr" if "PersNr" in work.columns else "Personalnummer"

    # Exakt wie Dashboard: Is_Vacant == False
    if "Is_Vacant" in work.columns:
        active = work[work["Is_Vacant"] == False]
        print(f"  Zeilen total: {len(work)}  |  Is_Vacant==False: {len(active)}"
              f"  |  Is_Vacant==True: {(work['Is_Vacant'] == True).sum()}")
    else:
        print("  [WARNUNG] Is_Vacant fehlt — kein Vakanz-Filter angewendet")
        active = work

    # Sidebar-Filter Geschlecht
    if FILTER_GESCHLECHT and "Text Geschlecht" in active.columns:
        before = len(active)
        active = active[active["Text Geschlecht"].isin(FILTER_GESCHLECHT)]
        print(f"  Geschlecht-Filter ({FILTER_GESCHLECHT}): {before} → {len(active)}")

    # Sidebar-Filter Arbeitszeit
    if FILTER_ARBEITSZEIT and "Arbeitszeit" in active.columns:
        before = len(active)
        active = active[active["Arbeitszeit"].isin(FILTER_ARBEITSZEIT)]
        print(f"  Arbeitszeit-Filter: {before} → {len(active)}")

    # Matrix aufbauen
    matrix = (
        active.groupby(["TrfGr_c", "St_c"])[id_col]
        .nunique()
        .unstack(fill_value=0)
        .rename_axis(None, axis=1)
    )
    matrix.columns = [f"Stufe {int(c)}" for c in matrix.columns]
    matrix["Gesamt"] = matrix.sum(axis=1)
    matrix.index.name = "Entgeltgruppe"

    print(f"\n  Eindeutige Entgeltgruppen: {len(matrix)}")
    print(f"  Gesamt Personen (dedupliziert): {active[id_col].nunique()}")
    print(f"  Gesamt Positionen (Zeilen):     {len(active)}")
    print()
    print(matrix.to_string())
    return matrix


def build_matrix_from_raw_excel(path: str) -> pd.DataFrame:
    """
    Naive Excel-Sicht: Filtert Mitarbeiter.xlsx direkt nach TrfGr,
    zählt Zeilen pro (TrfGr, Stufe).
    Spiegelt das, was ein Nutzer beim Excel-Filtern sieht.
    """
    hr("MATRIX B — Mitarbeiter.xlsx (naive Excel-Sicht, keine Is_Vacant-Filterung)")

    if not os.path.exists(path):
        print(f"  [FEHLER] Datei nicht gefunden: {path}")
        return pd.DataFrame()

    raw = pd.read_excel(path)
    print(f"  Geladene Zeilen: {len(raw)}")
    print(f"  Spalten: {[c for c in raw.columns if not c.startswith('Unnamed')]}")

    if "TrfGr" not in raw.columns or "St" not in raw.columns:
        print("  [FEHLER] TrfGr oder St-Spalte fehlt!")
        return pd.DataFrame()

    raw["TrfGr_c"] = normalize_trfgr(raw["TrfGr"])
    raw["St_c"]    = clean_step_series(raw["St"])

    id_col = next((c for c in ("PersNr","Personalnummer") if c in raw.columns), None)

    # Stichtag-Filter (wie Loader)
    if "Eintritt" in raw.columns:
        raw["Eintritt_dt"] = pd.to_datetime(raw["Eintritt"], errors="coerce")
        before = len(raw)
        raw = raw[raw["Eintritt_dt"] <= STICHTAG]
        print(f"  Eintritt ≤ Stichtag: {before} → {len(raw)}")

    if "Austritt" in raw.columns:
        raw["Austritt_dt"] = pd.to_datetime(raw["Austritt"], errors="coerce")
        raw["Austritt_dt"] = raw["Austritt_dt"].where(
            raw["Austritt_dt"].dt.year != 9999, other=pd.NaT
        )
        before = len(raw)
        raw = raw[(raw["Austritt_dt"].isna()) | (raw["Austritt_dt"] >= STICHTAG)]
        print(f"  Austritt ≥ Stichtag (oder leer): {before} → {len(raw)}")

    print(f"\n  Zeilen nach Stichtag-Filter:  {len(raw)}")
    if id_col:
        print(f"  Unique PersNr:               {raw[id_col].nunique()}")

    # Matrix: Zeilen zählen (wie Excel-Filter), NICHT PersNr.nunique()
    matrix_rows = (
        raw.groupby(["TrfGr_c", "St_c"]).size()
        .unstack(fill_value=0)
        .rename_axis(None, axis=1)
    )
    matrix_rows.columns = [f"Stufe {int(c)}" for c in matrix_rows.columns]
    matrix_rows["Gesamt"] = matrix_rows.sum(axis=1)
    matrix_rows.index.name = "Entgeltgruppe"

    # Matrix: PersNr.nunique() (wie Dashboard)
    if id_col:
        matrix_uniq = (
            raw.groupby(["TrfGr_c", "St_c"])[id_col].nunique()
            .unstack(fill_value=0)
            .rename_axis(None, axis=1)
        )
        matrix_uniq.columns = [f"Stufe {int(c)}" for c in matrix_uniq.columns]
        matrix_uniq["Gesamt"] = matrix_uniq.sum(axis=1)
        matrix_uniq.index.name = "Entgeltgruppe"
    else:
        matrix_uniq = matrix_rows.copy()

    print("\n  B1) Zeilen-Zählung (= was Excel-Filter anzeigt):")
    print(matrix_rows.to_string())
    print("\n  B2) Unique PersNr (= was Dashboard zählt, ohne Is_Vacant-Merge):")
    print(matrix_uniq.to_string())

    return matrix_uniq


def compare_matrices(matrix_a: pd.DataFrame, matrix_b: pd.DataFrame,
                     label_a: str, label_b: str):
    """Vergleicht zwei Matrizen zellenweise und zeigt Abweichungen."""
    hr(f"VERGLEICH: {label_a}  vs.  {label_b}")

    # Gemeinsame Entgeltgruppen und Stufen
    all_eg  = sorted(set(matrix_a.index) | set(matrix_b.index))
    all_col = sorted(set(matrix_a.columns) | set(matrix_b.columns),
                     key=lambda c: (c == "Gesamt", c))

    diff_rows = []
    ok_count  = 0
    ko_count  = 0

    for eg in all_eg:
        for col in all_col:
            val_a = int(matrix_a.loc[eg, col]) if (eg in matrix_a.index and col in matrix_a.columns) else 0
            val_b = int(matrix_b.loc[eg, col]) if (eg in matrix_b.index and col in matrix_b.columns) else 0
            if val_a != val_b:
                diff_rows.append({
                    "Entgeltgruppe": eg,
                    "Spalte":        col,
                    label_a:         val_a,
                    label_b:         val_b,
                    "Differenz":     val_b - val_a,
                })
                ko_count += 1
            else:
                ok_count += 1

    total = ok_count + ko_count
    print(f"\n  Zellen übereinstimmend: {ok_count}/{total}")
    print(f"  Zellen abweichend:      {ko_count}/{total}")

    if not diff_rows:
        print("\n  ✅  Keine Abweichungen — Dashboard und Excel sind konsistent.")
        return

    diff_df = pd.DataFrame(diff_rows)
    print("\n  ❌  Abweichende Zellen:")
    print(diff_df.to_string(index=False))

    return diff_df


def analyze_discrepancies_for_eg(snapshot_df: pd.DataFrame,
                                  raw_df: pd.DataFrame,
                                  entgeltgruppe: str):
    """
    Detailanalyse für eine abweichende Entgeltgruppe:
    Zeigt, welche PersNr im Snapshot aber nicht in raw vorhanden sind und umgekehrt.
    """
    hr(f"DETAIL-ANALYSE für Entgeltgruppe: {entgeltgruppe}")

    id_col = "PersNr" if "PersNr" in snapshot_df.columns else "Personalnummer"
    raw_id_col = next((c for c in ("PersNr","Personalnummer") if c in raw_df.columns), None)

    # Snapshot: alle nicht-vakanten Personen mit dieser EG
    snap_eg = snapshot_df[
        (normalize_trfgr(snapshot_df["TrfGr"]) == entgeltgruppe) &
        (snapshot_df.get("Is_Vacant", pd.Series(False, index=snapshot_df.index)) == False)
    ].copy()

    # Raw: alle Personen nach Stichtag-Filter mit dieser EG
    raw_eg = raw_df[normalize_trfgr(raw_df["TrfGr"]) == entgeltgruppe].copy() if not raw_df.empty else pd.DataFrame()

    snap_ids = set(snap_eg[id_col].dropna().astype(str)) if id_col in snap_eg.columns else set()
    raw_ids  = set(raw_eg[raw_id_col].dropna().astype(str)) if (raw_id_col and not raw_eg.empty) else set()

    only_snap = snap_ids - raw_ids
    only_raw  = raw_ids  - snap_ids
    in_both   = snap_ids & raw_ids

    print(f"  Im Snapshot (nicht vakant):    {len(snap_ids)} Personen")
    print(f"  In Mitarbeiter.xlsx (Stichtag): {len(raw_ids)} Personen")
    print(f"  In beiden:                      {len(in_both)}")

    if only_snap:
        print(f"\n  ⚠  Nur im Snapshot ({len(only_snap)}): {sorted(only_snap)}")
        detail = snap_eg[snap_eg[id_col].astype(str).isin(only_snap)]
        show = [c for c in [id_col, "TrfGr", "St", "BsGrd",
                             "Status kundenindividuell", "Kürzel OrgEinheit",
                             "Is_Vacant"] if c in detail.columns]
        print(detail[show].to_string(index=False))

    if only_raw:
        print(f"\n  ⚠  Nur in Mitarbeiter.xlsx ({len(only_raw)}): {sorted(only_raw)}")
        if not raw_eg.empty and raw_id_col:
            detail = raw_eg[raw_eg[raw_id_col].astype(str).isin(only_raw)]
            show = [c for c in [raw_id_col, "TrfGr", "St", "BsGrd",
                                 "Status kundenindividuell",
                                 "Eintritt", "Austritt"] if c in detail.columns]
            print(detail[show].to_string(index=False))

    # Mehrfachplanstellen prüfen
    if not snap_eg.empty and id_col in snap_eg.columns:
        dupes = snap_eg[snap_eg.duplicated(subset=id_col, keep=False)]
        if not dupes.empty:
            print(f"\n  ⚠  Mehrfachplanstellen im Snapshot ({dupes[id_col].nunique()} Personen):")
            for pid, grp in dupes.groupby(id_col):
                print(f"     PersNr {pid}: {len(grp)} Positionen (Stufen: "
                      f"{list(clean_step_series(grp['St']))})")


def run():
    print("\n" + SEP)
    print("  Verguetungsklassen-Matrix Validierung")
    print("  Stichtag: " + str(STICHTAG.date()))
    print(SEP)

    # ── Schritt 1: Snapshot laden ─────────────────────────────────────────────
    snapshot_df = pd.DataFrame()

    if os.path.exists(SNAPSHOT_FILE):
        print(f"\n  Lade Snapshot: {os.path.basename(SNAPSHOT_FILE)}")
        try:
            snapshot_df = pd.read_excel(
                SNAPSHOT_FILE, sheet_name=SNAPSHOT_SHEET,
                parse_dates=["GebDatum", "Eintritt", "Austritt"]
            )
            # PersNr normalisieren (float → int string)
            for col in ("PersNr", "Personalnummer"):
                if col in snapshot_df.columns:
                    snapshot_df[col] = (
                        snapshot_df[col]
                        .apply(lambda x: str(int(float(x))) if pd.notna(x) and str(x).replace(".", "").isdigit() else str(x))
                    )
            print(f"  Snapshot-Zeilen: {len(snapshot_df)}")
        except Exception as e:
            print(f"  [WARNUNG] Snapshot-Ladefeuer: {e}")
            snapshot_df = pd.DataFrame()
    else:
        print(f"  Snapshot nicht gefunden: {SNAPSHOT_FILE}")

    # Fallback: Mitarbeiter.xlsx allein (ohne Planstellen-Merge)
    if snapshot_df.empty and os.path.exists(MITARBEITER_FILE):
        print(f"\n  Fallback: Lade Mitarbeiter.xlsx als Snapshot-Basis")
        snapshot_df = pd.read_excel(MITARBEITER_FILE)
        # Stichtag-Filter anwenden
        for col_dt, op in [("Eintritt", "le"), ("Austritt", "ge")]:
            if col_dt in snapshot_df.columns:
                snapshot_df[col_dt] = pd.to_datetime(snapshot_df[col_dt], errors="coerce")
                if op == "le":
                    snapshot_df = snapshot_df[snapshot_df[col_dt] <= STICHTAG]
                else:
                    snapshot_df = snapshot_df[
                        snapshot_df[col_dt].isna() | (snapshot_df[col_dt] >= STICHTAG)
                    ]
        if "Is_Vacant" not in snapshot_df.columns:
            snapshot_df["Is_Vacant"] = False

    if snapshot_df.empty:
        print("\n  [FEHLER] Kein Snapshot ladbar. Bitte Pfade prüfen.")
        sys.exit(1)

    # ── Schritt 2: Matrix A aus Snapshot ─────────────────────────────────────
    matrix_a = build_matrix_from_snapshot(
        snapshot_df,
        label=f"Dashboard-Snapshot ({SNAPSHOT_SHEET})"
    )

    # ── Schritt 3: Matrix B aus roher Mitarbeiter.xlsx ────────────────────────
    raw_df = pd.DataFrame()
    if os.path.exists(MITARBEITER_FILE):
        raw_df_full = pd.read_excel(MITARBEITER_FILE)
        # Stichtag-Filter für Rohdaten
        raw_wk = raw_df_full.copy()
        if "Eintritt" in raw_wk.columns:
            raw_wk["Eintritt"] = pd.to_datetime(raw_wk["Eintritt"], errors="coerce")
            raw_wk = raw_wk[raw_wk["Eintritt"] <= STICHTAG]
        if "Austritt" in raw_wk.columns:
            raw_wk["Austritt"] = pd.to_datetime(raw_wk["Austritt"], errors="coerce")
            raw_wk["Austritt"] = raw_wk["Austritt"].where(
                raw_wk["Austritt"].dt.year != 9999, other=pd.NaT
            )
            raw_wk = raw_wk[raw_wk["Austritt"].isna() | (raw_wk["Austritt"] >= STICHTAG)]
        raw_df = raw_wk
        matrix_b = build_matrix_from_raw_excel(MITARBEITER_FILE)
    else:
        print(f"\n  [WARNUNG] Mitarbeiter.xlsx nicht gefunden: {MITARBEITER_FILE}")
        matrix_b = pd.DataFrame()

    # ── Schritt 4: Vergleich ──────────────────────────────────────────────────
    if not matrix_a.empty and not matrix_b.empty:
        diff_df = compare_matrices(
            matrix_a, matrix_b,
            label_a="Dashboard",
            label_b="Excel (unique PersNr)"
        )

        # ── Schritt 5: Detail-Analyse für abweichende EGs ─────────────────────
        if diff_df is not None and not diff_df.empty:
            abweichende_egs = diff_df["Entgeltgruppe"].unique()
            for eg in abweichende_egs:
                analyze_discrepancies_for_eg(snapshot_df, raw_df, eg)

    # ── Schritt 6: Gesamtsummary ──────────────────────────────────────────────
    hr("ZUSAMMENFASSUNG")
    if not matrix_a.empty:
        print(f"  Dashboard-Gesamt (Köpfe): {matrix_a['Gesamt'].sum()}")
    if not matrix_b.empty:
        print(f"  Excel-Gesamt (Köpfe):     {matrix_b['Gesamt'].sum()}")
    print()
    print("  Legende:")
    print("  - Dashboard: Is_Vacant==False + PersNr.nunique()")
    print("  - Excel:     Stichtag-Filter + PersNr.nunique() (kein Planstellen-Merge)")
    print("  - Diff:      Zellen wo Dashboard ≠ Excel")
    print("\n" + SEP + "\n")


def run_full_matrix_test():
    """
    Vollstaendiger Test aller EG x Stufe Kombinationen.

    Fuer jede Zelle der Vergutungsmatrix wird geprueft:
      - Wie viele Personen zaehlt das Dashboard (PersNr.nunique, Is_Vacant=False)?
      - Wie viele sieht man in Excel mit Standardfiltern (Unbefristet + Aktives BV)?
      - Welche Personen erklaeren die Differenz, und warum (Vertragsart, Status, OE)?

    Ergebnis wird als Tabelle + Excel-Datei ausgegeben.
    """
    import sys
    sys.path.insert(0, BASE_DIR)
    from abgaenge.schemas import normalize_persnr

    print("\n" + SEP)
    print("  VOLLTEST: Alle EG x Stufe Kombinationen")
    print("  Stichtag: " + str(STICHTAG.date()))
    print(SEP)

    # --- Daten laden ---
    if not os.path.exists(MITARBEITER_FILE):
        print("[FEHLER] Mitarbeiter.xlsx nicht gefunden: " + MITARBEITER_FILE)
        return
    if not os.path.exists(PLANSTELLEN_FILE):
        print("[FEHLER] Planstellen.XLSX nicht gefunden: " + PLANSTELLEN_FILE)
        return

    ma = pd.read_excel(MITARBEITER_FILE)
    pl = pd.read_excel(PLANSTELLEN_FILE)

    # PersNr normalisieren
    ma["PersNr_norm"] = normalize_persnr(ma["PersNr"])
    pl["PersNr_norm"] = normalize_persnr(pl["Personalnummer"])

    # Stichtag-Filter
    ma["Eintritt"] = pd.to_datetime(ma["Eintritt"], errors="coerce")
    ma["Austritt"] = pd.to_datetime(ma["Austritt"], errors="coerce")
    ma["Austritt"] = ma["Austritt"].where(ma["Austritt"].dt.year != 9999, other=pd.NaT)
    ma = ma[ma["Eintritt"] <= STICHTAG]
    ma = ma[(ma["Austritt"].isna()) | (ma["Austritt"] >= STICHTAG)]

    # TrfGr + Stufe normalisieren
    ma["TrfGr_c"] = ma["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    s = ma["St"].astype(str).str.strip().str.replace("+","",regex=False).str.replace("-","",regex=False)
    ma["St_c"] = pd.to_numeric(s, errors="coerce").fillna(4).astype(int)

    print(f"  Mitarbeiter geladen (nach Stichtag): {len(ma)}")
    print(f"  Planstellen geladen:                 {len(pl)}")

    # --- Filter-Masken ---
    mask_unbefristet = ma["Vertragsart"].astype(str).str.strip() == "Unbefristet"
    mask_aktiv       = ma["Status kundenindividuell"].astype(str).str.strip() == "Aktives Beschäftigungsverhältnis"
    mask_excel       = mask_unbefristet & mask_aktiv

    # Vertragsarten und Status-Werte global
    print("\n  Vertragsart-Werte gesamt:")
    for val, cnt in ma["Vertragsart"].value_counts().items():
        print(f"    {val}: {cnt}")
    print("\n  Status-Werte gesamt:")
    for val, cnt in ma["Status kundenindividuell"].value_counts().items():
        print(f"    {val}: {cnt}")

    # --- Ergebnis-Tabelle aufbauen ---
    results = []
    detail_rows = []

    all_egs    = sorted(ma["TrfGr_c"].unique())
    all_stufen = sorted(ma["St_c"].unique())

    for eg in all_egs:
        for stufe in all_stufen:
            cell = ma[(ma["TrfGr_c"] == eg) & (ma["St_c"] == stufe)]
            if cell.empty:
                continue

            ids_dashboard = set(cell["PersNr_norm"].dropna())
            ids_excel     = set(cell[mask_excel.reindex(cell.index, fill_value=False)]["PersNr_norm"].dropna())

            n_dashboard = len(ids_dashboard)
            n_excel     = len(ids_excel)
            diff        = n_dashboard - n_excel
            only_dash   = ids_dashboard - ids_excel  # in Dashboard, nicht in Excel

            results.append({
                "Entgeltgruppe": eg,
                "Stufe":         stufe,
                "Dashboard":     n_dashboard,
                "Excel_Filter":  n_excel,
                "Differenz":     diff,
                "Nur_Dashboard": len(only_dash),
            })

            # Detail fuer abweichende Zellen
            for pid in sorted(only_dash):
                person = cell[cell["PersNr_norm"] == pid].iloc[0]
                # Planstelle holen
                pl_row  = pl[pl["PersNr_norm"] == pid]
                oe      = pl_row["Kürzel OrgEinheit"].values[0] if not pl_row.empty else "?"
                soll_az = pl_row["Sollarbeitszeit"].values[0]   if not pl_row.empty else "?"

                vertragsart = str(person.get("Vertragsart", "?")).strip()
                status      = str(person.get("Status kundenindividuell", "?")).strip()

                # Grund bestimmen
                gruende = []
                if vertragsart != "Unbefristet":
                    gruende.append("Vertragsart=" + vertragsart)
                if status != "Aktives Beschäftigungsverhältnis":
                    gruende.append("Status=" + status)
                grund = "; ".join(gruende) if gruende else "unbekannt"

                detail_rows.append({
                    "Entgeltgruppe": eg,
                    "Stufe":         stufe,
                    "PersNr":        pid,
                    "Vertragsart":   vertragsart,
                    "Status":        status,
                    "OE":            oe,
                    "Sollarbeitszeit": soll_az,
                    "Grund":         grund,
                })

    results_df = pd.DataFrame(results)
    detail_df  = pd.DataFrame(detail_rows)

    # --- Ausgabe: Zusammenfassung ---
    print("\n" + SEP)
    print("  MATRIX: Dashboard vs. Excel-Filter (Unbefristet + Aktives BV)")
    print(SEP)

    # Pivot Dashboard
    dash_pivot = results_df.pivot(index="Entgeltgruppe", columns="Stufe", values="Dashboard").fillna(0).astype(int)
    dash_pivot.columns = ["Stufe " + str(c) for c in dash_pivot.columns]
    dash_pivot["Gesamt"] = dash_pivot.sum(axis=1)

    # Pivot Excel
    xl_pivot = results_df.pivot(index="Entgeltgruppe", columns="Stufe", values="Excel_Filter").fillna(0).astype(int)
    xl_pivot.columns = ["Stufe " + str(c) for c in xl_pivot.columns]
    xl_pivot["Gesamt"] = xl_pivot.sum(axis=1)

    # Pivot Differenz
    diff_pivot = results_df.pivot(index="Entgeltgruppe", columns="Stufe", values="Differenz").fillna(0).astype(int)
    diff_pivot.columns = ["Stufe " + str(c) for c in diff_pivot.columns]
    diff_pivot["Gesamt"] = diff_pivot.sum(axis=1)

    print("\n  A) Dashboard-Werte:")
    print(dash_pivot.to_string())
    print(f"\n  Gesamt Dashboard: {dash_pivot['Gesamt'].sum()}")

    print("\n  B) Excel-Filter-Werte (Unbefristet + Aktives BV):")
    print(xl_pivot.to_string())
    print(f"\n  Gesamt Excel-Filter: {xl_pivot['Gesamt'].sum()}")

    # Nur abweichende Zellen zeigen
    diff_nonzero = results_df[results_df["Differenz"] > 0].copy()
    if diff_nonzero.empty:
        print("\n  Keine Abweichungen gefunden.")
    else:
        print("\n  C) Abweichende Zellen (Dashboard > Excel-Filter):")
        print(diff_nonzero.to_string(index=False))

    # --- Detail-Ausgabe: alle abweichenden Personen ---
    if not detail_df.empty:
        print("\n" + SEP)
        print("  DETAIL: Alle Personen mit Abweichung (im Dashboard, nicht im Excel-Filter)")
        print(SEP)
        print(detail_df.to_string(index=False))

        # Grund-Statistik
        print("\n  Grund-Statistik:")
        for grund, grp in detail_df.groupby("Grund"):
            print(f"    {grund}: {len(grp)} Personen")

    # --- Excel-Export ---
    out_path = os.path.join(BASE_DIR, "..", "matrix_validierung_volltest.xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        dash_pivot.to_excel(writer, sheet_name="Dashboard")
        xl_pivot.to_excel(writer, sheet_name="Excel_Filter")
        diff_pivot.to_excel(writer, sheet_name="Differenz")
        if not detail_df.empty:
            detail_df.to_excel(writer, sheet_name="Detail_Abweichungen", index=False)
        results_df.to_excel(writer, sheet_name="Rohdaten", index=False)

    print("\n  Excel-Export: " + os.path.basename(out_path))
    print("\n" + SEP + "\n")


if __name__ == "__main__":
    run()
    run_full_matrix_test()
