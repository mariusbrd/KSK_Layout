"""
Testfrage 1: ATZ Kandidatenpool – Wer wird gezogen?

Isolierter Test der Pool-Filterlogik aus forecast.py:_schedule_new_atz_cases()
Zeigt exakt, welche Statusgruppen im Pool landen und welche nicht.

Testet sowohl die ALTE Logik (ohne OE-Filter) als auch die NEUE Logik (mit EXCLUDED_OE_CODES).
"""

import numpy as np
import pandas as pd

COL_PERSNR = "PersNr"
COL_STATUS = "Status kundenindividuell"
COL_OE_CODE = "Kürzel OrgEinheit"
COL_ATZ_PHASE = "Phase"
COL_ATZ_BEGINN = "Beginn"
COL_ATZ_ENDE = "Ende"

# Muss identisch sein mit abgaenge/schemas.py EXCLUDED_OE_CODES
EXCLUDED_OE_CODES = frozenset({
    "9940", "9941", "9945", "9960", "9970",
    "9971", "9972", "9973", "9975", "9990",
})

# ── 1) Synthetischer Mitarbeiter-Datensatz (10 Personen, alle Statusfaelle) ──

start_date = pd.Timestamp("2025-12-31")
eligible_age_min = 55
eligible_age_max = 60

df_ma = pd.DataFrame([
    # Person 1: Aktiv, 57 Jahre, kein ATZ → SOLL im Pool sein
    {"PersNr": "000001", "Name": "Aktiv_Normal",
     "GebDatum": pd.Timestamp("1968-06-15"), "Eintritt": pd.Timestamp("2000-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "100", "Jobfamily": "Vertrieb", "Organisationseinheit": "OE100"},

    # Person 2: Aktiv, aber zu jung (50) → NEIN (Alter)
    {"PersNr": "000002", "Name": "Aktiv_ZuJung",
     "GebDatum": pd.Timestamp("1975-03-20"), "Eintritt": pd.Timestamp("2005-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "200", "Jobfamily": "IT", "Organisationseinheit": "OE200"},

    # Person 3: Aktiv, aber zu alt (62) → NEIN (Alter)
    {"PersNr": "000003", "Name": "Aktiv_ZuAlt",
     "GebDatum": pd.Timestamp("1963-08-10"), "Eintritt": pd.Timestamp("1990-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "300", "Jobfamily": "Verwaltung", "Organisationseinheit": "OE300"},

    # Person 4: Ruhendes Beschaeftigungsverhaeltnis, 58 Jahre → NEIN (status_ruhend)
    {"PersNr": "000004", "Name": "Ruhend_58",
     "GebDatum": pd.Timestamp("1967-04-01"), "Eintritt": pd.Timestamp("1995-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Ruhendes Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "9900", "Jobfamily": "Vertrieb", "Organisationseinheit": "PA Ruhendes BV"},

    # Person 5: Bereits in ATZ-Arbeitsphase, 57 Jahre → NEIN (in_atz)
    {"PersNr": "000005", "Name": "ATZ_Arbeitsphase",
     "GebDatum": pd.Timestamp("1968-11-20"), "Eintritt": pd.Timestamp("1992-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "100", "Jobfamily": "Beratung", "Organisationseinheit": "OE100"},

    # Person 6: Bereits in ATZ-Freistellungsphase, 59 Jahre → NEIN (in_atz)
    {"PersNr": "000006", "Name": "ATZ_Freistellung",
     "GebDatum": pd.Timestamp("1966-02-14"), "Eintritt": pd.Timestamp("1988-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "9990", "Jobfamily": "Vertrieb", "Organisationseinheit": "PA Freistellung"},

    # Person 7: Aktiv, 56 Jahre, Teilzeit → JA (Teilzeit ist kein Ausschluss)
    {"PersNr": "000007", "Name": "Aktiv_Teilzeit_56",
     "GebDatum": pd.Timestamp("1969-09-30"), "Eintritt": pd.Timestamp("2002-01-01"),
     "BsGrd": 50, "Sollarbeitszeit": 19.5,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "200", "Jobfamily": "IT", "Organisationseinheit": "OE200"},

    # Person 8: OE 9990 (Beurlaubung, nicht ATZ), 55 Jahre → NEIN nach Fix (Sonder-OE)
    {"PersNr": "000008", "Name": "OE9990_Beurlaubt_55",
     "GebDatum": pd.Timestamp("1970-07-12"), "Eintritt": pd.Timestamp("1998-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "9990", "Jobfamily": "Verwaltung", "Organisationseinheit": "PA Freistellung"},

    # Person 9: OE 9940 (Dauerkranke), 58 Jahre → NEIN nach Fix (Sonder-OE)
    {"PersNr": "000009", "Name": "OE9940_Dauerkrank_58",
     "GebDatum": pd.Timestamp("1967-10-05"), "Eintritt": pd.Timestamp("1993-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "9940", "Jobfamily": "Vertrieb", "Organisationseinheit": "PA Dauerkranke"},

    # Person 10: OE 9975 (Erziehungszeit), 56 Jahre → NEIN nach Fix (Sonder-OE)
    {"PersNr": "000010", "Name": "OE9975_Erziehung_56",
     "GebDatum": pd.Timestamp("1969-05-18"), "Eintritt": pd.Timestamp("2001-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "9975", "Jobfamily": "Verwaltung", "Organisationseinheit": "PA Erziehungszeit"},
])

# ── 2) ATZ-Daten (Personen 5 und 6 sind bereits in ATZ) ──

df_atz = pd.DataFrame([
    {"PersNr": "000005", "Phase": "AR",
     "Beginn": pd.Timestamp("2025-01-01"), "Ende": pd.Timestamp("2027-06-30"),
     "Ende ATZ Vertrag": pd.Timestamp("2030-01-01")},
    {"PersNr": "000006", "Phase": "FR",
     "Beginn": pd.Timestamp("2024-01-01"), "Ende": pd.Timestamp("2026-06-30"),
     "Ende ATZ Vertrag": pd.Timestamp("2026-06-30")},
])

# ── 3) df_state aufbauen (analog forecast.py) ──

df_state = df_ma.copy()
df_state[COL_PERSNR] = df_state[COL_PERSNR].astype(str)
df_state = df_state.set_index(COL_PERSNR, drop=True)
df_state.index = df_state.index.astype(str)
df_state["age"] = (start_date - pd.to_datetime(df_state["GebDatum"])).dt.days / 365.25
df_state["status_ruhend"] = df_state[COL_STATUS] == "Ruhendes Beschäftigungsverhältnis"

atz_fr_active = set()
if not df_atz.empty and COL_ATZ_PHASE in df_atz.columns:
    fr_rows = df_atz[df_atz[COL_ATZ_PHASE] == "FR"]
    fr_active = fr_rows[
        (fr_rows[COL_ATZ_BEGINN] <= start_date) & (fr_rows[COL_ATZ_ENDE] >= start_date)
    ]
    atz_fr_active = set(fr_active[COL_PERSNR].dropna().astype(str))

df_state["in_atz"] = df_state.index.isin(
    df_atz[COL_PERSNR].dropna().astype(str).unique()
) if not df_atz.empty else False
df_state["atz_fr_active"] = df_state.index.isin(atz_fr_active)
df_state["active"] = True


def apply_filter(df, use_oe_filter: bool):
    """Wendet die Pool-Filterlogik an, optional mit OE-Ausschluss."""
    mask = (
        (df["active"] == True) &
        (~df["in_atz"]) &
        (~df["status_ruhend"]) &
        (df["age"] >= eligible_age_min) &
        (df["age"] <= eligible_age_max)
    )
    if use_oe_filter and COL_OE_CODE in df.columns:
        mask = mask & (~df[COL_OE_CODE].astype(str).isin(EXCLUDED_OE_CODES))
    return df[mask]


def print_results(eligible, df_all, label):
    """Gibt Pool-Ergebnis formatiert aus."""
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print(f"Kandidaten im Pool: {len(eligible)}\n")

    if not eligible.empty:
        print("  IM Pool:")
        for idx, row in eligible.iterrows():
            oe = row.get(COL_OE_CODE, "?")
            print(f"    + {idx} | {row['Name']:<25} | Alter {row['age']:.1f} | OE {oe}")

    excluded = df_all[~df_all.index.isin(eligible.index)]
    print("\n  NICHT im Pool:")
    for idx, row in excluded.iterrows():
        reasons = []
        if row["active"] != True:
            reasons.append("active=False")
        if row["in_atz"]:
            reasons.append("in_atz=True")
        if row["status_ruhend"]:
            reasons.append("status_ruhend=True")
        if row["age"] < eligible_age_min:
            reasons.append(f"Alter {row['age']:.1f} < {eligible_age_min}")
        if row["age"] > eligible_age_max:
            reasons.append(f"Alter {row['age']:.1f} > {eligible_age_max}")
        oe = str(row.get(COL_OE_CODE, ""))
        if oe in EXCLUDED_OE_CODES and label.startswith("NACH"):
            reasons.append(f"Sonder-OE {oe}")
        print(f"    x {idx} | {row['Name']:<25} | {', '.join(reasons)}")


# ── 4) Test: VOR Fix (ohne OE-Filter) ──

eligible_old = apply_filter(df_state, use_oe_filter=False)
print_results(eligible_old, df_state, "VOR FIX (alte Logik, ohne OE-Ausschluss)")

# ── 5) Test: NACH Fix (mit OE-Filter) ──

eligible_new = apply_filter(df_state, use_oe_filter=True)
print_results(eligible_new, df_state, "NACH FIX (neue Logik, mit EXCLUDED_OE_CODES)")

# ── 6) Vergleich ──

removed = set(eligible_old.index) - set(eligible_new.index)
print(f"\n{'=' * 70}")
print("VERGLEICH: Durch Fix entfernte Personen")
print("=" * 70)
if removed:
    for persnr in sorted(removed):
        row = df_state.loc[persnr]
        print(f"  - {persnr} | {row['Name']} | OE {row[COL_OE_CODE]} ({row['Organisationseinheit']})")
else:
    print("  (keine Aenderung)")

# ── 7) Assertions ──

print(f"\n{'=' * 70}")
print("ASSERTIONS")
print("=" * 70)

errors = []

# Personen 1 und 7 MUESSEN im Pool sein (beide Varianten)
for p in ["000001", "000007"]:
    if p not in eligible_old.index:
        errors.append(f"FAIL: {p} sollte im alten Pool sein")
    if p not in eligible_new.index:
        errors.append(f"FAIL: {p} sollte im neuen Pool sein")

# Personen 2-6 duerfen in KEINER Variante im Pool sein
for p in ["000002", "000003", "000004", "000005", "000006"]:
    if p in eligible_old.index:
        errors.append(f"FAIL: {p} sollte NICHT im alten Pool sein")
    if p in eligible_new.index:
        errors.append(f"FAIL: {p} sollte NICHT im neuen Pool sein")

# Personen 8-10: IM alten Pool (Bug), NICHT im neuen Pool (Fix)
for p in ["000008", "000009", "000010"]:
    if p not in eligible_old.index:
        errors.append(f"FAIL: {p} sollte im alten Pool sein (Bug reproduziert)")
    if p in eligible_new.index:
        errors.append(f"FAIL: {p} sollte NICHT im neuen Pool sein (Fix wirkt nicht)")

if errors:
    for e in errors:
        print(f"  {e}")
    print(f"\n  ERGEBNIS: {len(errors)} FEHLER")
else:
    print("  Alle 10 Assertions bestanden.")
    print("  - Personen 1, 7: korrekt im Pool (aktiv, Altersband, regulaere OE)")
    print("  - Personen 2, 3: korrekt ausgeschlossen (Alter)")
    print("  - Person 4: korrekt ausgeschlossen (status_ruhend)")
    print("  - Personen 5, 6: korrekt ausgeschlossen (in_atz)")
    print("  - Personen 8, 9, 10: korrekt ausgeschlossen nach Fix (Sonder-OE)")
    print(f"\n  ERGEBNIS: ALLE TESTS BESTANDEN")
