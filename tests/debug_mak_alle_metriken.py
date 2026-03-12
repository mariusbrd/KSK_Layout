"""
debug_mak_alle_metriken.py
==========================
Validiert ALLE MAK-Metriken aus dem Dashboard gegen die Referenz-Exporte
im Ordner 'validation_mak_input_keine Sonderrollen'.

Repliziert exakt die Dashboard-Logik (combine_to_snapshot + get_unique_employees):
  - Planstellen als Basis (Mehrfachplanstellen → MAK summiert)
  - Stichtag-Filter, Vorstand/Ruhend/OE-Exklusionen
  - ATZ Phase aus ATZ.xlsx (ist_atz_fr Flag)
  - MAK = BsGrd/100 (0 fuer Vacant/Ruhend/ATZ-FR)
  - Qualifikation aus Ausbildung.xlsx

Ausfuehren (aus KSK_Layout/):
    py -X utf8 tests/debug_mak_alle_metriken.py
"""

import os
import sys
import pandas as pd
import numpy as np

# ── Pfade ─────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL_DIR  = os.path.join(BASE_DIR, "..", "Original-Daten")
DASHBOARD_DIR = os.path.join(BASE_DIR, "..")
VALIDATION_DIR = os.path.join(DASHBOARD_DIR, "validation_mak_input_keine Sonderrollen")

MITARBEITER_FILE = os.path.join(ORIGINAL_DIR, "Mitarbeiter.xlsx")
PLANSTELLEN_FILE = os.path.join(ORIGINAL_DIR, "Planstellen.XLSX")
ATZ_FILE         = os.path.join(ORIGINAL_DIR, "ATZ.xlsx")
AUSBILDUNG_FILE  = os.path.join(ORIGINAL_DIR, "Ausbildung.xlsx")

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

# Alterskohorten (aus config/settings.py)
DEFAULT_COHORTS = {
    "15-19": (15, 19), "20-24": (20, 24), "25-29": (25, 29),
    "30-34": (30, 34), "35-39": (35, 39), "40-44": (40, 44),
    "45-49": (45, 49), "50-54": (50, 54), "55-59": (55, 59),
    "60-64": (60, 64), "65-69": (65, 69),
}
COHORT_ORDER = list(DEFAULT_COHORTS.keys())

# Betriebszugehoerigkeit-Bins
TENURE_BINS   = [0, 2, 5, 10, 20, 200]
TENURE_LABELS = ["0-2 J.", "2-5 J.", "5-10 J.", "10-20 J.", "20+ J."]

SEP  = "=" * 75
SEP2 = "-" * 50
TOL  = 0.0005  # Toleranz (Rundungsdifferenzen)

# ─────────────────────────────────────────────────────────────────────────────


def hr(title=""):
    print("\n" + SEP)
    if title:
        print("  " + title)
        print(SEP)


def apply_org_exclusion_mask(ou_series: pd.Series, org_units: list) -> pd.Series:
    s = ou_series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    explicit = [u for u in org_units if u != "99XX"]
    mask = s.isin(explicit)
    if "99XX" in org_units:
        mask |= s.str.startswith("99") & ~s.isin(set(explicit))
    return mask


def assign_age_cohort(age: int) -> str:
    for name, (lo, hi) in DEFAULT_COHORTS.items():
        if lo <= age <= hi:
            return name
    return "Unbekannt"


def get_atz_status(row) -> str:
    """Exakt wie loader.py get_atz_status()."""
    if row.get("ist_atz_fr", False):
        return "Freistellungsphase"
    if pd.notna(row.get("Phase", None)):
        phase = str(row["Phase"]).strip()
        if phase == "AR":
            return "Arbeitsphase"
        elif phase == "FR":
            return "Freistellungsphase"
    vertragsart = row.get("Vertragsart")
    if pd.notna(vertragsart) and str(vertragsart).strip() == "Altersteilzeit":
        if row.get("Alter", 0) >= 60:
            return "Freistellungsphase"
        return "Arbeitsphase"
    return "Kein ATZ"


def get_unique_employees(df: pd.DataFrame) -> pd.DataFrame:
    """
    Exakt wie kpi_engine.py get_unique_employees():
    - Filtert Is_Vacant==False
    - Summiert MAK-Spalten pro Person (Mehrfachplanstellen)
    - Behaelt erste Zeile fuer alle anderen Felder
    """
    besetzt = df[~df["Is_Vacant"]].copy() if "Is_Vacant" in df.columns else df.copy()
    if "PersNr" not in besetzt.columns or besetzt.empty:
        return besetzt

    sum_cols = [c for c in ("Sollarbeitszeit", "Soll_FTE", "MAK_Calculated", "MAK", "mak", "BsGrd")
                if c in besetzt.columns]

    if sum_cols:
        sums = besetzt.groupby("PersNr")[sum_cols].sum().reset_index()
        sums.columns = ["PersNr"] + [f"{c}_sum" for c in sum_cols]
        unique = besetzt.drop_duplicates(subset="PersNr", keep="first").copy()
        unique = unique.merge(sums, on="PersNr", how="left")
        for c in sum_cols:
            unique[c] = unique[f"{c}_sum"]
            unique = unique.drop(columns=[f"{c}_sum"])
        return unique
    else:
        return besetzt.drop_duplicates(subset="PersNr", keep="first")


def compare_series(calc: pd.Series, ref: pd.Series, label: str) -> list:
    """Vergleicht zwei Series (nach Index) und gibt Abweichungen zurueck."""
    diffs = []
    all_idx = sorted(set(calc.index) | set(ref.index))
    for idx in all_idx:
        v_calc = float(calc.get(idx, 0.0))
        v_ref  = float(ref.get(idx, 0.0))
        if abs(v_calc - v_ref) > TOL:
            diffs.append({
                "Metrik":    label,
                "Gruppe":    str(idx),
                "Berechnet": round(v_calc, 4),
                "Referenz":  round(v_ref, 4),
                "Delta":     round(v_calc - v_ref, 4),
            })
    return diffs


# ─────────────────────────────────────────────────────────────────────────────


def build_base_dataset() -> pd.DataFrame:
    """Baut den Basis-Datensatz auf — repliziert combine_to_snapshot-Logik."""

    sys.path.insert(0, BASE_DIR)
    from abgaenge.schemas import normalize_persnr

    print("\n  Lade Quelldateien...")
    ma  = pd.read_excel(MITARBEITER_FILE)
    pl  = pd.read_excel(PLANSTELLEN_FILE)
    atz = pd.read_excel(ATZ_FILE)
    aus = pd.read_excel(AUSBILDUNG_FILE)
    print(f"  MA: {len(ma)}  PL: {len(pl)}  ATZ: {len(atz)}  Ausbildung: {len(aus)}")

    # ── PersNr normalisieren ─────────────────────────────────────────────────
    ma["PersNr"]          = normalize_persnr(ma["PersNr"])
    pl["Personalnummer"]  = normalize_persnr(pl["Personalnummer"])
    atz["PersNr"]         = normalize_persnr(atz["PersNr"])
    aus["Personalnummer"] = normalize_persnr(aus["Personalnummer"])

    # ── ATZ aufbereiten ──────────────────────────────────────────────────────
    atz["Beginn"] = pd.to_datetime(atz["Beginn"], errors="coerce")
    atz["Ende"]   = pd.to_datetime(atz["Ende"],   errors="coerce")

    # Aktuelle Phase zum Stichtag
    atz_aktuell = atz[(atz["Beginn"] <= STICHTAG) & (atz["Ende"] >= STICHTAG)].copy()
    if atz_aktuell["PersNr"].duplicated().any():
        atz_aktuell = atz_aktuell.sort_values("Phase", ascending=False)
        atz_aktuell = atz_aktuell.drop_duplicates(subset="PersNr", keep="first")

    atz_fr_set = set(atz_aktuell[atz_aktuell["Phase"] == "FR"]["PersNr"])
    print(f"  ATZ aktiv zum Stichtag: {len(atz_aktuell)}  (davon FR: {len(atz_fr_set)})")

    # ── Mitarbeiter: Stichtag-Filter ─────────────────────────────────────────
    ma["Eintritt"] = pd.to_datetime(ma["Eintritt"], errors="coerce")
    ma["Austritt"] = pd.to_datetime(ma["Austritt"], errors="coerce")
    ma["Austritt"] = ma["Austritt"].where(ma["Austritt"].dt.year != 9999, other=pd.NaT)
    before = len(ma)
    ma = ma[ma["Eintritt"] <= STICHTAG]
    ma = ma[(ma["Austritt"].isna()) | (ma["Austritt"] >= STICHTAG)]
    print(f"  MA nach Stichtag-Filter: {before} -> {len(ma)}")

    # ── Mitarbeiter: Personenexklusion (Vorstand, Ruhend) ───────────────────
    if EXCLUSIONS.get("vorstand") and "MitarbGruppenbez." in ma.columns:
        ma = ma[ma["MitarbGruppenbez."] != "Vorstand"]
    if EXCLUSIONS.get("ruhend_bv") and "Status kundenindividuell" in ma.columns:
        mask_ruhend = ma["Status kundenindividuell"].astype(str).str.strip().isin([
            "Ruhendes Beschäftigungsverhältnis",
            "Ruhendes Beschaeftigungsverhaeltnis",
        ])
        ma = ma[~mask_ruhend]
    print(f"  MA nach Personenexklusion: {len(ma)}")

    # ── Planstellen: OE-Exklusion ────────────────────────────────────────────
    ex_units = EXCLUSIONS.get("org_units", [])
    mask_pl_excl = apply_org_exclusion_mask(pl["Kürzel OrgEinheit"], ex_units)
    pl_active = pl[~mask_pl_excl].copy()
    pl_active = pl_active[pl_active["Personalnummer"].isin(ma["PersNr"])].copy()
    print(f"  Planstellen nach OE-Exkl + Personenfilter: {len(pl_active)}")
    print(f"  Unique Personen: {pl_active['Personalnummer'].nunique()}")

    # ── Merge: Planstellen (Basis) + Mitarbeiter ─────────────────────────────
    ma_cols = [c for c in [
        "PersNr", "Vorname", "Nachname", "GebDatum", "Text Gsch",
        "Eintritt", "BsGrd", "Vertragsart", "MitarbGruppenbez.",
        "Status kundenindividuell", "TrfGr", "St",
    ] if c in ma.columns]
    # Planstellen nutzen "Personalnummer" — nach Merge wird es zur PersNr-Referenzspalte
    df = pl_active.rename(columns={"Personalnummer": "PersNr"}).copy()
    df = df.merge(ma[ma_cols], on="PersNr", how="left")

    # ── Merge: Ausbildung ─────────────────────────────────────────────────────
    aus_clean = aus[["Personalnummer", "BV Ausbildungsgruppentext"]].copy()
    aus_clean["Personalnummer"] = aus_clean["Personalnummer"].astype(str)
    aus_clean["BV Ausbildungsgruppentext"] = aus_clean["BV Ausbildungsgruppentext"].astype(str).str.strip()
    aus_clean = aus_clean.rename(columns={"BV Ausbildungsgruppentext": "Ausbildung",
                                           "Personalnummer": "PersNr_aus"})
    df = df.merge(aus_clean, left_on="PersNr", right_on="PersNr_aus", how="left")
    df.drop(columns=["PersNr_aus"], errors="ignore", inplace=True)

    # ── Merge: ATZ-Phase ─────────────────────────────────────────────────────
    atz_phase = atz_aktuell[["PersNr", "Phase"]].copy()
    atz_phase["Phase"] = atz_phase["Phase"].astype(str).str.strip()
    df = df.merge(atz_phase, on="PersNr", how="left")

    # ── Is_Vacant (Planstelle ohne Mitarbeiter) ──────────────────────────────
    # In unserem gefilterten Datensatz sind alle Planstellen besetzt
    # (pl_active wurde auf Personen in ma eingeschraenkt).
    df["Is_Vacant"] = False

    # ── ist_atz_fr Flag ──────────────────────────────────────────────────────
    df["ist_atz_fr"] = df["PersNr"].isin(atz_fr_set)

    # ── MAK berechnen ─────────────────────────────────────────────────────────
    bsgrd = pd.to_numeric(df["BsGrd"], errors="coerce").fillna(0)
    df["MAK_Calculated"] = bsgrd / 100.0
    # Ruhend = 0 (bereits aus Mitarbeiter-Basis entfernt → keine Auswirkung)
    # ATZ-FR = 0
    df.loc[df["ist_atz_fr"] == True, "MAK_Calculated"] = 0.0
    # Kopiere als MAK (fuer get_unique_employees)
    df["MAK"] = df["MAK_Calculated"]

    # ── Alter berechnen (zum Stichtag) ───────────────────────────────────────
    if "GebDatum" in df.columns:
        df["Alter"] = ((STICHTAG - pd.to_datetime(df["GebDatum"], errors="coerce")).dt.days / 365.25).fillna(0).astype(int)
    else:
        df["Alter"] = 0

    # ── Alterskohorte ─────────────────────────────────────────────────────────
    df["Alterskohorte"] = df["Alter"].apply(assign_age_cohort)

    # ── Betriebszugehoerigkeit ────────────────────────────────────────────────
    df["Betriebszugehörigkeit_Jahre"] = (
        (STICHTAG - pd.to_datetime(df["Eintritt"], errors="coerce")).dt.days / 365.25
    ).fillna(0)
    df["Betriebszugehörigkeit_Bin"] = pd.cut(
        df["Betriebszugehörigkeit_Jahre"],
        bins=TENURE_BINS,
        labels=TENURE_LABELS,
        right=False,
    ).astype(str)
    # pd.cut kann 'nan' ausgeben fuer Werte ausserhalb der Bins
    df.loc[df["Betriebszugehörigkeit_Bin"] == "nan", "Betriebszugehörigkeit_Bin"] = "0-2 J."

    # ── Geschlecht ────────────────────────────────────────────────────────────
    if "Text Gsch" in df.columns:
        df["Geschlecht"] = df["Text Gsch"].map({"weiblich": "w", "männlich": "m"})

    # ── ATZ-Status ────────────────────────────────────────────────────────────
    df["ATZ_Status"] = df.apply(get_atz_status, axis=1)

    # ── Beschaeftigungsstatus (= Vertragsart) ─────────────────────────────────
    if "Vertragsart" in df.columns:
        df["Beschäftigungsstatus"] = df["Vertragsart"].fillna("(unbekannt)").astype(str).str.strip()
    else:
        df["Beschäftigungsstatus"] = "(unbekannt)"

    print(f"\n  Basis-Datensatz bereit: {len(df)} Zeilen, {df['PersNr'].nunique()} Personen")
    return df


# ─────────────────────────────────────────────────────────────────────────────


def run():
    print("\n" + SEP)
    print("  MAK-Vollvalidierung — alle 7 Metriken")
    print("  Stichtag: " + str(STICHTAG.date()))
    print(SEP)

    # ── Datensatz aufbauen ───────────────────────────────────────────────────
    hr("DATENSATZ")
    df = build_base_dataset()

    # ── Einzigartiger Mitarbeiter-Datensatz (get_unique_employees) ───────────
    hr("DEDUPLIZIERUNG (get_unique_employees)")
    emp = get_unique_employees(df)
    print(f"  Planstellen-Zeilen (Basis):  {len(df)}")
    print(f"  Unique Personen (emp):        {len(emp)}")
    total_mak = emp["MAK"].sum()
    print(f"  Gesamt-MAK:                   {total_mak:.4f}")
    print(f"  Referenz-Gesamt:              846.3591")

    all_diffs = []

    # ═══════════════════════════════════════════════════════════════════════
    # 1. Vergütungsklassen (EG × Stufe) — vorberechnet, aber Konsistenzcheck
    # ═══════════════════════════════════════════════════════════════════════
    hr("1/7 — Vergütungsklassen (EG x Stufe)")

    ref_vg = pd.read_excel(
        os.path.join(VALIDATION_DIR, "ist_mak_verguetungsklassen.xlsx"),
        sheet_name="Daten",
        index_col=0,
    )
    # Berechnete Matrix (Summe direkt aus df, ohne get_unique_employees —
    # denn hier zaehlt pro Planstelle, nicht pro Person)
    df["TrfGr_c"] = df["TrfGr"].astype(str).str.strip().str.upper().str.replace(" ", "", regex=False)
    s = df["St"].astype(str).str.strip().str.replace("+","",regex=False).str.replace("-","",regex=False)
    df["St_c"] = pd.to_numeric(s, errors="coerce").fillna(4).astype(int)
    eg_mask = df["TrfGr_c"].str.match(r"^E\d{1,2}[ABC]?$")

    mak_vg = (
        df[eg_mask].groupby(["TrfGr_c", "St_c"])["MAK"]
        .sum()
        .unstack(fill_value=0.0)
        .rename_axis(None, axis=1)
    )
    mak_vg.columns = [f"Stufe {int(c)}" for c in mak_vg.columns]
    mak_vg["Gesamt"] = mak_vg.sum(axis=1)
    mak_vg.index.name = "Entgeltgruppe"

    ok = ko = 0
    for eg in sorted(set(mak_vg.index) | set(ref_vg.index)):
        for col in mak_vg.columns:
            v_c = float(mak_vg.loc[eg, col]) if (eg in mak_vg.index and col in mak_vg.columns) else 0.0
            v_r = float(ref_vg.loc[eg, col]) if (eg in ref_vg.index and col in ref_vg.columns) else 0.0
            if abs(v_c - v_r) > TOL:
                ko += 1
                all_diffs.append({"Metrik": "Vergütungsklassen", "Gruppe": f"{eg}/{col}",
                                   "Berechnet": round(v_c, 4), "Referenz": round(v_r, 4),
                                   "Delta": round(v_c - v_r, 4)})
            else:
                ok += 1
    status = "OK" if ko == 0 else f"FEHLER ({ko} Zellen)"
    print(f"  {ok}/{ok+ko} Zellen korrekt — {status}")

    # ═══════════════════════════════════════════════════════════════════════
    # 2. Geschlecht
    # ═══════════════════════════════════════════════════════════════════════
    hr("2/7 — Geschlecht")

    ref_g = pd.read_excel(
        os.path.join(VALIDATION_DIR, "ist_mak_geschlecht (1).xlsx"),
        sheet_name="Daten",
    ).set_index("Geschlecht")

    calc_g = emp.groupby("Geschlecht")["MAK"].sum()
    print(f"  Berechnet:\n{calc_g.to_string()}")
    print(f"  Referenz:\n{ref_g['IST'].to_string()}")
    diffs_g = compare_series(calc_g, ref_g["IST"], "Geschlecht")
    all_diffs.extend(diffs_g)
    print(f"  Status: {'OK' if not diffs_g else f'FEHLER ({len(diffs_g)} Abweichungen)'}")

    # ═══════════════════════════════════════════════════════════════════════
    # 3. Alterskohorten
    # ═══════════════════════════════════════════════════════════════════════
    hr("3/7 — Alterskohorten")

    ref_a = pd.read_excel(
        os.path.join(VALIDATION_DIR, "ist_mak_alterskohorten (1).xlsx"),
        sheet_name="Daten",
    ).set_index("Alterskohorte")

    calc_a = emp.groupby("Alterskohorte")["MAK"].sum().reindex(COHORT_ORDER, fill_value=0.0)
    ref_a_ist = ref_a["IST"].reindex(COHORT_ORDER, fill_value=0.0)

    print(f"  {'Kohorte':<10} {'Berechnet':>12} {'Referenz':>12} {'Delta':>10}")
    print("  " + "-" * 46)
    for k in COHORT_ORDER:
        v_c = calc_a.get(k, 0.0)
        v_r = ref_a_ist.get(k, 0.0)
        flag = " <<" if abs(v_c - v_r) > TOL else ""
        print(f"  {k:<10} {v_c:>12.4f} {v_r:>12.4f} {v_c-v_r:>10.4f}{flag}")

    diffs_a = compare_series(calc_a, ref_a_ist, "Alterskohorten")
    all_diffs.extend(diffs_a)
    print(f"  Status: {'OK' if not diffs_a else f'FEHLER ({len(diffs_a)} Abweichungen)'}")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. Dauer im Unternehmen
    # ═══════════════════════════════════════════════════════════════════════
    hr("4/7 — Dauer im Unternehmen")

    ref_t = pd.read_excel(
        os.path.join(VALIDATION_DIR, "ist_mak_dauer_im_unternehmen.xlsx"),
        sheet_name="Daten",
    ).set_index("Betriebszugehörigkeit_Bin")

    calc_t = emp.groupby("Betriebszugehörigkeit_Bin")["MAK"].sum().reindex(TENURE_LABELS, fill_value=0.0)
    ref_t_ist = ref_t["IST"].reindex(TENURE_LABELS, fill_value=0.0)

    print(f"  {'Bin':<12} {'Berechnet':>12} {'Referenz':>12} {'Delta':>10}")
    print("  " + "-" * 48)
    for k in TENURE_LABELS:
        v_c = calc_t.get(k, 0.0)
        v_r = ref_t_ist.get(k, 0.0)
        flag = " <<" if abs(v_c - v_r) > TOL else ""
        print(f"  {k:<12} {v_c:>12.4f} {v_r:>12.4f} {v_c-v_r:>10.4f}{flag}")

    diffs_t = compare_series(calc_t, ref_t_ist, "Dauer_im_Unternehmen")
    all_diffs.extend(diffs_t)
    print(f"  Status: {'OK' if not diffs_t else f'FEHLER ({len(diffs_t)} Abweichungen)'}")

    # ═══════════════════════════════════════════════════════════════════════
    # 5. ATZ-Status
    # ═══════════════════════════════════════════════════════════════════════
    hr("5/7 — ATZ-Status")

    ref_atz = pd.read_excel(
        os.path.join(VALIDATION_DIR, "ist_mak_atz-status.xlsx"),
        sheet_name="Daten",
    ).set_index("ATZ_Status")

    atz_order = ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"]
    calc_atz = emp.groupby("ATZ_Status")["MAK"].sum().reindex(atz_order, fill_value=0.0)
    ref_atz_ist = ref_atz["IST"].reindex(atz_order, fill_value=0.0)

    print(f"  {'Status':<22} {'Berechnet':>12} {'Referenz':>12} {'Delta':>10}")
    print("  " + "-" * 58)
    for k in atz_order:
        v_c = calc_atz.get(k, 0.0)
        v_r = ref_atz_ist.get(k, 0.0)
        flag = " <<" if abs(v_c - v_r) > TOL else ""
        print(f"  {k:<22} {v_c:>12.4f} {v_r:>12.4f} {v_c-v_r:>10.4f}{flag}")

    diffs_atz = compare_series(calc_atz, ref_atz_ist, "ATZ_Status")
    all_diffs.extend(diffs_atz)
    print(f"  Status: {'OK' if not diffs_atz else f'FEHLER ({len(diffs_atz)} Abweichungen)'}")

    # Detail: ATZ-FR-Personen mit MAK>0 (moeglicher Bug)
    fr_persons = emp[emp["ATZ_Status"] == "Freistellungsphase"]
    if not fr_persons.empty:
        fr_with_mak = fr_persons[fr_persons["MAK"] > 0]
        if not fr_with_mak.empty:
            print(f"\n  HINWEIS: {len(fr_with_mak)} Person(en) mit ATZ_Status=Freistellungsphase und MAK>0:")
            show = [c for c in ["PersNr", "MAK", "ist_atz_fr", "Phase", "Vertragsart", "Alter",
                                 "Status kundenindividuell"] if c in fr_with_mak.columns]
            print(fr_with_mak[show].to_string(index=False))
            print("  (ist_atz_fr=False + Phase=FR => Vertragsart-Fallback, kein MAK-Zeroing)")

    # ═══════════════════════════════════════════════════════════════════════
    # 6. Beschäftigungsstatus
    # ═══════════════════════════════════════════════════════════════════════
    hr("6/7 — Beschäftigungsstatus")

    ref_bs = pd.read_excel(
        os.path.join(VALIDATION_DIR, "ist_mak_beschäftigungsstatus.xlsx"),
        sheet_name="Daten",
    ).set_index("Beschäftigungsstatus")

    calc_bs = emp.groupby("Beschäftigungsstatus")["MAK"].sum().sort_values(ascending=False)
    ref_bs_ist = ref_bs["IST"]

    print(f"  {'Status':<30} {'Berechnet':>12} {'Referenz':>12} {'Delta':>10}")
    print("  " + "-" * 66)
    all_bs_keys = sorted(set(calc_bs.index) | set(ref_bs_ist.index))
    for k in all_bs_keys:
        v_c = float(calc_bs.get(k, 0.0))
        v_r = float(ref_bs_ist.get(k, 0.0))
        flag = " <<" if abs(v_c - v_r) > TOL else ""
        print(f"  {k:<30} {v_c:>12.4f} {v_r:>12.4f} {v_c-v_r:>10.4f}{flag}")

    diffs_bs = compare_series(calc_bs, ref_bs_ist, "Beschäftigungsstatus")
    all_diffs.extend(diffs_bs)
    print(f"  Status: {'OK' if not diffs_bs else f'FEHLER ({len(diffs_bs)} Abweichungen)'}")

    if diffs_bs:
        print("\n  Vorhandene Vertragsart-Werte in emp:")
        for v, c in emp["Beschäftigungsstatus"].value_counts().items():
            mak_sum = emp[emp["Beschäftigungsstatus"] == v]["MAK"].sum()
            print(f"    {v}: {c} Personen, MAK={mak_sum:.4f}")

    # ═══════════════════════════════════════════════════════════════════════
    # 7. Qualifikation (Ausbildung)
    # ═══════════════════════════════════════════════════════════════════════
    hr("7/7 — Qualifikation (Ausbildung)")

    ref_q = pd.read_excel(
        os.path.join(VALIDATION_DIR, "ist_mak_qualifikation (1).xlsx"),
        sheet_name="Daten",
    ).set_index("Ausbildung")

    # Nur Personen mit bekannter Ausbildung (nicht NaN, nicht "nan")
    emp_q = emp[emp["Ausbildung"].notna() & (emp["Ausbildung"].astype(str) != "nan")].copy()
    calc_q = emp_q.groupby("Ausbildung")["MAK"].sum().sort_values(ascending=False)
    ref_q_ist = ref_q["IST"]

    print(f"  Personen mit Ausbildungsdaten: {len(emp_q)} / {len(emp)}")
    print(f"  Gesamt-MAK mit Ausbildung:     {calc_q.sum():.4f}")
    print(f"  Gesamt-MAK Referenz:           {ref_q_ist.sum():.4f}")
    print()
    print(f"  {'Ausbildung':<35} {'Berechnet':>12} {'Referenz':>12} {'Delta':>10}")
    print("  " + "-" * 71)
    all_q_keys = sorted(set(calc_q.index) | set(ref_q_ist.index))
    for k in all_q_keys:
        v_c = float(calc_q.get(k, 0.0))
        v_r = float(ref_q_ist.get(k, 0.0))
        flag = " <<" if abs(v_c - v_r) > TOL else ""
        print(f"  {str(k):<35} {v_c:>12.4f} {v_r:>12.4f} {v_c-v_r:>10.4f}{flag}")

    diffs_q = compare_series(calc_q, ref_q_ist, "Qualifikation")
    all_diffs.extend(diffs_q)
    print(f"  Status: {'OK' if not diffs_q else f'FEHLER ({len(diffs_q)} Abweichungen)'}")

    # ═══════════════════════════════════════════════════════════════════════
    # GESAMTÜBERSICHT
    # ═══════════════════════════════════════════════════════════════════════
    hr("GESAMTÜBERSICHT")

    metrik_counts = {}
    for d in all_diffs:
        metrik_counts[d["Metrik"]] = metrik_counts.get(d["Metrik"], 0) + 1

    metrics = [
        "Vergütungsklassen", "Geschlecht", "Alterskohorten",
        "Dauer_im_Unternehmen", "ATZ_Status", "Beschäftigungsstatus", "Qualifikation"
    ]
    print(f"  {'Metrik':<30} {'Status'}")
    print("  " + "-" * 50)
    for m in metrics:
        n_diff = metrik_counts.get(m, 0)
        status = "OK" if n_diff == 0 else f"FEHLER ({n_diff} Abweichungen)"
        print(f"  {m:<30} {status}")

    total_diffs = len(all_diffs)
    print(f"\n  Gesamt: {total_diffs} Abweichungen {'(alle Metriken korrekt!)' if total_diffs == 0 else ''}")

    if all_diffs:
        print("\n  Alle Abweichungen:")
        diff_df = pd.DataFrame(all_diffs)
        print(diff_df.to_string(index=False))

    # ── Excel-Export ─────────────────────────────────────────────────────────
    out_path = os.path.join(DASHBOARD_DIR, "mak_vollvalidierung.xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        emp.to_excel(writer, sheet_name="Deduplizierte_Personen", index=False)
        if all_diffs:
            pd.DataFrame(all_diffs).to_excel(writer, sheet_name="Abweichungen", index=False)
        # Zusammenfassung
        summary_rows = []
        for m in metrics:
            n_diff = metrik_counts.get(m, 0)
            summary_rows.append({"Metrik": m, "Abweichungen": n_diff,
                                  "Status": "OK" if n_diff == 0 else "FEHLER"})
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Zusammenfassung", index=False)

    print(f"\n  Excel-Export: {os.path.basename(out_path)}")
    print("\n" + SEP + "\n")


if __name__ == "__main__":
    run()
