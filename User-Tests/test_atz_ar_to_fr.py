"""
Testfrage 2: ATZ Wechsel in die Freistellung passiert zu frueh?

Trace-Test: Simuliert das erste Jahr mit verschiedenen ATZ-Szenarien
und protokolliert exakt, wann AR->FR Uebergaenge stattfinden.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from abgaenge.forecast import (
    _build_atz_pivot,
    _enforce_min_ar_duration,
    _schedule_new_atz_cases,
    _get_atz_events_from_schedule,
    _periods,
    _calc_age,
    PeriodInfo,
)
from abgaenge.schemas import COL_PERSNR, COL_ATZ_PHASE, COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE
from abgaenge.params import default_params

# ── Konfiguration ──

START_DATE = pd.Timestamp("2025-12-31")
END_DATE   = pd.Timestamp("2026-12-31")   # 1 Jahr simulieren
FREQ       = "M"                            # Monatlich

params = default_params()
# Nur ATZ-Komponente aktiv
params["components"] = {"atz": True, "retirement": False, "quit": False, "ruhend": False}
# Hohe ATZ-Rate, damit neue Faelle erzwungen werden
params["atz"]["new_atz_rate"] = 1.0
# Standard AR/FR Dauer
ar_years = params["atz"]["atz_duration_ar_years"]  # 2.5
fr_years = params["atz"]["atz_duration_fr_years"]  # 2.5
print(f"AR-Dauer (Jahre): {ar_years}")
print(f"FR-Dauer (Jahre): {fr_years}")
print(f"AR-Dauer (Monate, berechnet): {int(round(ar_years * 12))}")

# ── 1) Testpersonen ──

df_ma = pd.DataFrame([
    # A: Aktiv, 57 Jahre, kein ATZ -> wird neu in ATZ gescheduled
    {"PersNr": "000001", "Name": "Neu_ATZ_57",
     "GebDatum": pd.Timestamp("1968-06-15"), "Eintritt": pd.Timestamp("2000-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "100", "Jobfamily": "Vertrieb", "Organisationseinheit": "OE100"},
    # B: Aktiv, 58 Jahre, kein ATZ -> wird neu in ATZ gescheduled
    {"PersNr": "000002", "Name": "Neu_ATZ_58",
     "GebDatum": pd.Timestamp("1967-03-20"), "Eintritt": pd.Timestamp("1995-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "200", "Jobfamily": "IT", "Organisationseinheit": "OE200"},
    # C: Bereits in ATZ-AR seit 01.01.2025 (AR laeuft 1 Jahr) -> FR ab 01.01.2026 gemaess Daten
    {"PersNr": "000003", "Name": "Bestehend_AR_nahe_FR",
     "GebDatum": pd.Timestamp("1966-08-10"), "Eintritt": pd.Timestamp("1990-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "100", "Jobfamily": "Beratung", "Organisationseinheit": "OE100"},
    # D: Bereits in ATZ-AR seit 01.07.2025 (AR 2.5 Jahre) -> FR ab 01.01.2028
    {"PersNr": "000004", "Name": "Bestehend_AR_weit",
     "GebDatum": pd.Timestamp("1967-04-01"), "Eintritt": pd.Timestamp("1993-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "100", "Jobfamily": "Vertrieb", "Organisationseinheit": "OE100"},
    # E: Kontrollgruppe, 40 Jahre -> nicht ATZ-berechtigt
    {"PersNr": "000005", "Name": "Kontrollgruppe_40",
     "GebDatum": pd.Timestamp("1985-05-01"), "Eintritt": pd.Timestamp("2010-01-01"),
     "BsGrd": 100, "Sollarbeitszeit": 39.0,
     "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
     "Kürzel OrgEinheit": "300", "Jobfamily": "Verwaltung", "Organisationseinheit": "OE300"},
])

# ── 2) Bestehende ATZ-Daten ──

df_atz = pd.DataFrame([
    # Person C: AR-Phase, Beginn 01.01.2025, Ende 31.12.2025 (1 Jahr AR)
    {"PersNr": "000003", "Phase": "AR",
     "Beginn": pd.Timestamp("2025-01-01"), "Ende": pd.Timestamp("2025-12-31"),
     "Ende ATZ Vertrag": pd.Timestamp("2027-12-31")},
    # Person C: FR-Phase, Beginn 01.01.2026, Ende 31.12.2026
    {"PersNr": "000003", "Phase": "FR",
     "Beginn": pd.Timestamp("2026-01-01"), "Ende": pd.Timestamp("2026-12-31"),
     "Ende ATZ Vertrag": pd.Timestamp("2027-12-31")},
    # Person D: AR-Phase, Beginn 01.07.2025, Ende 31.12.2027 (2.5 Jahre)
    {"PersNr": "000004", "Phase": "AR",
     "Beginn": pd.Timestamp("2025-07-01"), "Ende": pd.Timestamp("2027-12-31"),
     "Ende ATZ Vertrag": pd.Timestamp("2030-06-30")},
    # Person D: FR-Phase, Beginn 01.01.2028, Ende 30.06.2030
    {"PersNr": "000004", "Phase": "FR",
     "Beginn": pd.Timestamp("2028-01-01"), "Ende": pd.Timestamp("2030-06-30"),
     "Ende ATZ Vertrag": pd.Timestamp("2030-06-30")},
])

# ── 3) ATZ-Pivot bauen (wie forecast.py) ──

atz_pivot_raw = _build_atz_pivot(df_atz)
print("\n=== ATZ Pivot VOR Fix (bestehende Faelle, Rohdaten) ===")
print(atz_pivot_raw.to_string(index=False))

# Fix anwenden: Mindest-AR erzwingen
min_ar_months = int(round(ar_years * 12))
min_fr_months = int(round(fr_years * 12))
atz_pivot = _enforce_min_ar_duration(atz_pivot_raw, min_ar_months, min_fr_months)
print(f"\n=== ATZ Pivot NACH Fix (Mindest-AR = {min_ar_months} Monate erzwungen) ===")
print(atz_pivot.to_string(index=False))

# Zeige Korrekturen
for _, row in atz_pivot.iterrows():
    raw_row = atz_pivot_raw[atz_pivot_raw[COL_PERSNR] == row[COL_PERSNR]]
    if not raw_row.empty:
        old_fr = raw_row.iloc[0]["fr_begin"]
        new_fr = row["fr_begin"]
        if pd.notna(old_fr) and pd.notna(new_fr) and old_fr != new_fr:
            print(f"  KORREKTUR {row[COL_PERSNR]}: fr_begin {old_fr.date()} -> {new_fr.date()}")

# ── 4) Simulation: 1 Jahr, Monat fuer Monat ──

print(f"\n{'=' * 80}")
print(f"SIMULATION: {START_DATE.date()} bis {END_DATE.date()} (monatlich)")
print(f"{'=' * 80}")

# Build df_state (analog forecast.py)
df_state = df_ma.copy()
df_state[COL_PERSNR] = df_state[COL_PERSNR].astype(str)
df_state = df_state.set_index(COL_PERSNR, drop=True)
df_state.index = df_state.index.astype(str)
df_state["age"] = _calc_age(df_state["GebDatum"], START_DATE)
df_state["status_ruhend"] = df_state["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis"

# ATZ FR detection
atz_fr_active = set()
if not df_atz.empty and "Phase" in df_atz.columns:
    fr_rows = df_atz[df_atz["Phase"] == "FR"]
    fr_active = fr_rows[
        (fr_rows["Beginn"] <= START_DATE) & (fr_rows["Ende"] >= START_DATE)
    ]
    atz_fr_active = set(fr_active["PersNr"].dropna().astype(str))

df_state["in_atz"] = df_state.index.isin(
    df_atz["PersNr"].dropna().astype(str).unique()
) if not df_atz.empty else False
df_state["atz_fr_active"] = df_state.index.isin(atz_fr_active)
df_state["active"] = True

bsgrd = pd.to_numeric(df_state["BsGrd"], errors="coerce").fillna(0.0)
df_state["mak"] = bsgrd / 100.0

print(f"\nInitialer Status:")
print(f"{'PersNr':<10} {'Name':<25} {'Alter':>6} {'in_atz':>7} {'atz_fr':>7} {'MAK':>5}")
print("-" * 65)
for idx, row in df_state.iterrows():
    print(f"{idx:<10} {row['Name']:<25} {row['age']:>6.1f} {str(row['in_atz']):>7} "
          f"{str(row['atz_fr_active']):>7} {row['mak']:>5.2f}")

rng = np.random.RandomState(42)
periods = _periods(START_DATE, END_DATE, FREQ)

all_events = []

print(f"\n{'=' * 80}")
print("PERIODEN-TRACE")
print(f"{'=' * 80}")

for period in periods:
    # Refresh ages
    df_state["age"] = _calc_age(df_state["GebDatum"], period.start)

    # Schedule new ATZ
    atz_pivot = _schedule_new_atz_cases(df_state, atz_pivot, params, period, rng)
    df_state["in_atz"] = df_state.index.isin(
        atz_pivot[COL_PERSNR].dropna().astype(str).unique()
    )

    # Get events
    ar_to_fr, atz_end = _get_atz_events_from_schedule(atz_pivot, period.start, period.end)

    if ar_to_fr or atz_end:
        print(f"\n  Periode {period.label} ({period.start.date()} - {period.end.date()}):")

    for persnr in ar_to_fr:
        if persnr in df_state.index and df_state.loc[persnr, "active"]:
            old_mak = float(df_state.loc[persnr, "mak"])
            # Lookup the person's ATZ details
            person_atz = atz_pivot[atz_pivot[COL_PERSNR] == persnr]
            fr_begin = person_atz["fr_begin"].iloc[0] if not person_atz.empty else "?"
            ar_begin = person_atz["ar_begin"].iloc[0] if not person_atz.empty else "?"
            ar_end = person_atz["ar_end"].iloc[0] if not person_atz.empty else "?"

            # Calculate time since AR start
            if pd.notna(ar_begin):
                ar_duration_days = (pd.Timestamp(fr_begin) - pd.Timestamp(ar_begin)).days
                ar_duration_years = ar_duration_days / 365.25
            else:
                ar_duration_years = float("nan")

            name = df_state.loc[persnr, "Name"]
            print(f"    >>> AR->FR: {persnr} ({name})")
            print(f"        AR-Beginn:    {ar_begin}")
            print(f"        AR-Ende:      {ar_end}")
            print(f"        FR-Beginn:    {fr_begin}")
            print(f"        AR-Dauer:     {ar_duration_years:.2f} Jahre ({ar_duration_days} Tage)")
            print(f"        Mindest-AR:   {ar_years} Jahre ({int(round(ar_years * 12))} Monate)")
            if ar_duration_years < ar_years:
                print(f"        !!! FEHLER: AR-Dauer ({ar_duration_years:.2f}J) < Mindest-AR ({ar_years}J) !!!")
            print(f"        MAK vorher:   {old_mak:.2f}")

            df_state.loc[persnr, "mak"] = 0.0
            df_state.loc[persnr, "atz_fr_active"] = True
            all_events.append({
                "period": period.label, "persnr": persnr, "name": name,
                "event": "AR->FR", "fr_begin": str(fr_begin),
                "ar_duration_years": ar_duration_years,
                "is_existing_case": persnr in ["000003", "000004"],
            })

    for persnr in atz_end:
        if persnr in df_state.index and df_state.loc[persnr, "active"]:
            name = df_state.loc[persnr, "Name"]
            print(f"    >>> ATZ-Ende: {persnr} ({name}) -> Rente")
            df_state.loc[persnr, "active"] = False
            all_events.append({
                "period": period.label, "persnr": persnr, "name": name,
                "event": "ATZ_END",
            })

# ── 5) Zusammenfassung ──

print(f"\n{'=' * 80}")
print("ZUSAMMENFASSUNG: Alle ATZ-Events im ersten Jahr")
print(f"{'=' * 80}")

if not all_events:
    print("  Keine ATZ-Events im ersten Jahr.")
else:
    for e in all_events:
        if e["event"] == "AR->FR":
            flag = " <- BESTEHEND" if e.get("is_existing_case") else " <- NEU GESCHEDULED"
            warn = ""
            if e["ar_duration_years"] < ar_years:
                warn = f" !!! ZU FRUEH (nur {e['ar_duration_years']:.2f}J statt {ar_years}J)"
            print(f"  {e['period']}: {e['persnr']} ({e['name']}) AR->FR | FR-Beginn: {e['fr_begin']} | AR-Dauer: {e['ar_duration_years']:.2f}J{flag}{warn}")
        else:
            print(f"  {e['period']}: {e['persnr']} ({e['name']}) {e['event']}")

# ── 6) Pivot final: Zeige alle ATZ-Faelle mit Datumsprognose ──

print(f"\n{'=' * 80}")
print("FINALER ATZ-PIVOT (alle Faelle nach 1 Jahr Simulation)")
print(f"{'=' * 80}")
print(atz_pivot.to_string(index=False))

# ── 7) Pruefe: Gibt es neue Faelle mit AR-Dauer < Mindest-AR? ──

print(f"\n{'=' * 80}")
print("PRUEFUNG: Neue ATZ-Faelle mit zu kurzer AR-Dauer")
print(f"{'=' * 80}")

new_cases = atz_pivot[~atz_pivot[COL_PERSNR].isin(["000003", "000004"])]
if new_cases.empty:
    print("  Keine neuen ATZ-Faelle erzeugt (ATZ-Rate zu niedrig oder Pool leer).")
else:
    min_ar_months = int(round(ar_years * 12))
    has_error = False
    for _, row in new_cases.iterrows():
        ar_b = pd.Timestamp(row["ar_begin"])
        fr_b = pd.Timestamp(row["fr_begin"])
        if pd.notna(ar_b) and pd.notna(fr_b):
            delta_months = (fr_b.year - ar_b.year) * 12 + (fr_b.month - ar_b.month)
            delta_years = (fr_b - ar_b).days / 365.25
            ok = "OK" if delta_months >= min_ar_months else "FEHLER"
            if ok == "FEHLER":
                has_error = True
            print(f"  {row[COL_PERSNR]}: AR {ar_b.date()} -> FR {fr_b.date()} = {delta_months} Monate ({delta_years:.2f}J) [{ok}]")
    if not has_error:
        print(f"\n  ERGEBNIS: Alle neuen Faelle haben AR-Dauer >= {min_ar_months} Monate. KEIN BUG bei neuen Faellen.")
    else:
        print(f"\n  ERGEBNIS: FEHLER GEFUNDEN! Neue Faelle mit AR < {min_ar_months} Monate!")
