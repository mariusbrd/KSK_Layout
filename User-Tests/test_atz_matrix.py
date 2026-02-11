"""
Testfrage 3: Greifen die Wahrscheinlichkeiten aus der ATZ Matrix wirklich?

Isolierter Test der ATZ-Matrix-Wahrscheinlichkeitslogik:
  1. Unit-Test: _select_atz_prob() einzeln pruefen
  2. End-to-End: _schedule_new_atz_cases() mit Extremwerten (0, 1, 0.5)
  3. Edge Cases: fehlender Eintrag, Fallback, Dimension-Wechsel
  4. Statistischer Test: 200 Iterationen fuer Gruppe C (0.5)
"""

import sys
import os
import numpy as np
import pandas as pd

# --- Setup: abgaenge-Modul importierbar machen ---
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from abgaenge.forecast import (
    _select_atz_prob,
    _schedule_new_atz_cases,
    PeriodInfo,
)
from abgaenge.schemas import COL_PERSNR, EXCLUDED_OE_CODES

# ============================================================
#  Synthetische Daten
# ============================================================

start_date = pd.Timestamp("2025-12-31")
period = PeriodInfo(
    start=pd.Timestamp("2026-01-01"),
    end=pd.Timestamp("2026-03-31"),
    label="2026-Q1",
)

def make_person(persnr, name, jobfamily, org_einheit="OE100", oe_code="100",
                age_years=57, bsgrd=100, soll=39.0):
    """Erzeugt eine Person im df_state-Format (als dict fuer DataFrame)."""
    geb = start_date - pd.DateOffset(years=age_years)
    return {
        "PersNr": persnr,
        "Name": name,
        "GebDatum": geb,
        "Eintritt": pd.Timestamp("2000-01-01"),
        "BsGrd": bsgrd,
        "Sollarbeitszeit": soll,
        "Status kundenindividuell": "Aktives Beschaeftigungsverhaeltnis",
        "Kuerzel OrgEinheit": oe_code,
        "Jobfamily": jobfamily,
        "Organisationseinheit": org_einheit,
    }

# 12 Personen: je 4 pro Gruppe (A/B/C), alle im Altersband 55-60
persons = [
    # Gruppe A: Jobfamily="GruppeA" -> Matrix-Wert = 0.0
    make_person("A01", "GruppeA_1", "GruppeA", age_years=55),
    make_person("A02", "GruppeA_2", "GruppeA", age_years=56),
    make_person("A03", "GruppeA_3", "GruppeA", age_years=57),
    make_person("A04", "GruppeA_4", "GruppeA", age_years=58),
    # Gruppe B: Jobfamily="GruppeB" -> Matrix-Wert = 1.0
    make_person("B01", "GruppeB_1", "GruppeB", age_years=55),
    make_person("B02", "GruppeB_2", "GruppeB", age_years=56),
    make_person("B03", "GruppeB_3", "GruppeB", age_years=57),
    make_person("B04", "GruppeB_4", "GruppeB", age_years=58),
    # Gruppe C: Jobfamily="GruppeC" -> Matrix-Wert = 0.5
    make_person("C01", "GruppeC_1", "GruppeC", age_years=55),
    make_person("C02", "GruppeC_2", "GruppeC", age_years=56),
    make_person("C03", "GruppeC_3", "GruppeC", age_years=57),
    make_person("C04", "GruppeC_4", "GruppeC", age_years=58),
    # Gruppe D: Jobfamily="GruppeD" -> KEIN Eintrag in Matrix (Fallback-Test)
    make_person("D01", "GruppeD_1", "GruppeD", age_years=57),
    make_person("D02", "GruppeD_2", "GruppeD", age_years=58),
]

df_state = pd.DataFrame(persons)
df_state[COL_PERSNR] = df_state["PersNr"].astype(str)
df_state = df_state.set_index(COL_PERSNR, drop=True)
df_state["age"] = (start_date - pd.to_datetime(df_state["GebDatum"])).dt.days / 365.25
df_state["tenure"] = 25.0
df_state["status_ruhend"] = False
df_state["in_atz"] = False
df_state["atz_fr_active"] = False
df_state["active"] = True
df_state["mak"] = (df_state["BsGrd"] / 100.0) * (df_state["Sollarbeitszeit"] / 39.0)

# Leeres ATZ-Pivot (keine bestehenden ATZ-Faelle)
atz_pivot_empty = pd.DataFrame(columns=[
    COL_PERSNR, "ar_begin", "ar_end", "fr_begin", "fr_end", "contract_end"
])

# ============================================================
#  ATZ Matrix mit Extremwerten
# ============================================================

params_matrix = {
    "components": {"atz": True, "retirement": False, "quit": False, "ruhend": False},
    "atz": {
        "new_atz_rate": 0.05,       # Basis-Rate (Fallback)
        "use_atz_matrix": True,
        "atz_dimension": "JobFamily",
        "atz_matrix": {
            "GruppeA": 0.0,         # NIEMALS ATZ
            "GruppeB": 1.0,         # IMMER ATZ
            "GruppeC": 0.5,         # 50% Chance
            # GruppeD: KEIN EINTRAG -> muss auf Default / base zurueckfallen
            "Default": 0.10,        # Fallback fuer unbekannte Jobfamilies
        },
        "atz_eligible_age_min": 55,
        "atz_eligible_age_max": 60,
        "atz_duration_ar_years": 2.5,
        "atz_duration_fr_years": 2.5,
    },
    "random_seed": 42,
}


# ============================================================
#  TEST 1: Unit-Test _select_atz_prob()
# ============================================================

print("=" * 70)
print("  TEST 1: Unit-Test _select_atz_prob()")
print("=" * 70)

errors = []

for idx, row in df_state.iterrows():
    prob = _select_atz_prob(row, params_matrix)
    jf = row["Jobfamily"]
    expected_prob = {
        "GruppeA": 0.0,
        "GruppeB": 1.0,
        "GruppeC": 0.5,
        "GruppeD": 0.10,  # Fallback auf "Default"
    }[jf]
    status = "OK" if abs(prob - expected_prob) < 1e-9 else "FAIL"
    if status == "FAIL":
        errors.append(f"  {idx}: Jobfamily={jf}, erwartet={expected_prob}, erhalten={prob}")
    print(f"  {idx} | Jobfamily={jf:<10} | prob={prob:.4f} | erwartet={expected_prob:.4f} | {status}")

print()
if errors:
    print(f"  ERGEBNIS: {len(errors)} FEHLER")
    for e in errors:
        print(e)
else:
    print("  ERGEBNIS: Alle 14 Probability-Lookups korrekt.")


# ============================================================
#  TEST 2: Unit-Test _select_atz_prob() OHNE Matrix
# ============================================================

print(f"\n{'=' * 70}")
print("  TEST 2: _select_atz_prob() mit use_atz_matrix=False")
print("=" * 70)

params_no_matrix = {
    "atz": {
        "new_atz_rate": 0.07,
        "use_atz_matrix": False,
    }
}

errors2 = []
for idx, row in df_state.iterrows():
    prob = _select_atz_prob(row, params_no_matrix)
    if abs(prob - 0.07) > 1e-9:
        errors2.append(f"  {idx}: erwartet=0.07, erhalten={prob}")

if errors2:
    print(f"  ERGEBNIS: {len(errors2)} FEHLER")
    for e in errors2:
        print(e)
else:
    print("  ERGEBNIS: Alle Personen erhalten base_rate=0.07 (Matrix ignoriert). OK.")


# ============================================================
#  TEST 3: End-to-End Extremwert-Test (200 Iterationen)
# ============================================================

print(f"\n{'=' * 70}")
print("  TEST 3: End-to-End _schedule_new_atz_cases() (200 Iterationen)")
print("=" * 70)

N_ITER = 200
group_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
group_total = {"A": 4 * N_ITER, "B": 4 * N_ITER, "C": 4 * N_ITER, "D": 2 * N_ITER}
# Track per person
person_selected = {idx: 0 for idx in df_state.index}

for i in range(N_ITER):
    rng = np.random.RandomState(i * 7 + 13)
    # Fresh df_state for each iteration (no in_atz contamination)
    df_iter = df_state.copy()
    df_iter["in_atz"] = False
    df_iter["active"] = True

    result_pivot = _schedule_new_atz_cases(
        df_iter, atz_pivot_empty.copy(), params_matrix, period, rng
    )

    # Who was selected?
    if not result_pivot.empty:
        selected = set(result_pivot[COL_PERSNR].astype(str).unique())
    else:
        selected = set()

    for persnr in selected:
        if persnr in person_selected:
            person_selected[persnr] += 1
        group = persnr[0]  # A, B, C, or D
        if group in group_counts:
            group_counts[group] += 1

# Results
print(f"\n  Iterationen: {N_ITER}")
print(f"  Periode: {period.label} ({(period.end - period.start).days} Tage)")
print()

print("  Per-Person Ergebnisse:")
for idx in sorted(df_state.index):
    count = person_selected[idx]
    jf = df_state.loc[idx, "Jobfamily"]
    rate = count / N_ITER
    print(f"    {idx} | {jf:<10} | gezogen: {count:>3}/{N_ITER} ({rate:.1%})")

print()
print("  Gruppen-Zusammenfassung:")
for grp in ["A", "B", "C", "D"]:
    n_persons = 4 if grp != "D" else 2
    total_draws = group_counts[grp]
    total_possible = group_total[grp]
    rate = total_draws / total_possible if total_possible > 0 else 0
    print(f"    Gruppe {grp}: {total_draws:>4} Ziehungen / {total_possible} moegliche ({rate:.1%})")

# ============================================================
#  TEST 3 Assertions
# ============================================================

print()
errors3 = []

# Gruppe A (prob=0): NIEMAND darf jemals gezogen werden
if group_counts["A"] > 0:
    errors3.append(f"FAIL: Gruppe A (prob=0) wurde {group_counts['A']}x gezogen (erwartet: 0)")

# Gruppe B (prob=1): Bei Poisson(lambda=1.0*0.247) ~ 25% chance pro Person/Iteration
# Aber MINDESTENS einige muessen gezogen worden sein
if group_counts["B"] == 0:
    errors3.append(f"FAIL: Gruppe B (prob=1) wurde nie gezogen (erwartet: regelmaessig)")

# Gruppe B sollte deutlich haeufiger gezogen werden als Gruppe C
b_rate = group_counts["B"] / group_total["B"]
c_rate = group_counts["C"] / group_total["C"]
if c_rate > 0 and b_rate <= c_rate:
    errors3.append(f"FAIL: Gruppe B Rate ({b_rate:.3f}) <= Gruppe C Rate ({c_rate:.3f})")

# Gruppe D (Fallback=0.10): sollte seltener gezogen werden als Gruppe C (0.5)
d_rate = group_counts["D"] / group_total["D"]
if group_counts["C"] > 0 and group_counts["D"] > 0 and d_rate >= c_rate:
    errors3.append(f"WARN: Gruppe D Rate ({d_rate:.3f}) >= Gruppe C Rate ({c_rate:.3f}) (Fallback-Verhalten)")

if errors3:
    for e in errors3:
        print(f"  {e}")
    print(f"\n  ERGEBNIS: {len(errors3)} FEHLER/WARNUNGEN")
else:
    print("  Alle Gruppen-Assertions bestanden:")
    print("  - Gruppe A (prob=0): nie gezogen")
    print(f"  - Gruppe B (prob=1): {group_counts['B']}x gezogen (dominant)")
    print(f"  - Gruppe C (prob=0.5): {group_counts['C']}x gezogen (mittel)")
    print(f"  - Gruppe D (Fallback=0.10): {group_counts['D']}x gezogen (selten)")
    print(f"\n  ERGEBNIS: MATRIX GREIFT KORREKT")


# ============================================================
#  TEST 4: Dimension-Wechsel (OrgUnit statt JobFamily)
# ============================================================

print(f"\n{'=' * 70}")
print("  TEST 4: Dimension-Wechsel auf OrgUnit")
print("=" * 70)

params_orgunit = {
    "components": {"atz": True, "retirement": False, "quit": False, "ruhend": False},
    "atz": {
        "new_atz_rate": 0.05,
        "use_atz_matrix": True,
        "atz_dimension": "OrgUnit",
        "atz_matrix": {
            "OE100": 0.0,   # Alle Personen sind in OE100 -> prob=0
        },
        "atz_eligible_age_min": 55,
        "atz_eligible_age_max": 60,
        "atz_duration_ar_years": 2.5,
        "atz_duration_fr_years": 2.5,
    },
    "random_seed": 42,
}

errors4 = []

# Verify _select_atz_prob uses OrgUnit
for idx, row in df_state.iterrows():
    prob = _select_atz_prob(row, params_orgunit)
    if abs(prob - 0.0) > 1e-9:
        errors4.append(f"  {idx}: OrgUnit=OE100 -> erwartet=0.0, erhalten={prob}")
        break

# End-to-end: nobody should be selected
rng = np.random.RandomState(42)
df_iter = df_state.copy()
result = _schedule_new_atz_cases(df_iter, atz_pivot_empty.copy(), params_orgunit, period, rng)

if not result.empty:
    errors4.append(f"FAIL: {len(result)} Personen gezogen trotz OrgUnit-prob=0")

if errors4:
    for e in errors4:
        print(f"  {e}")
else:
    print("  OrgUnit-Dimension greift: alle Personen in OE100 -> prob=0 -> 0 Ziehungen.")
    print("  ERGEBNIS: DIMENSION-WECHSEL FUNKTIONIERT")


# ============================================================
#  TEST 5: Edge Case - Komplett fehlender Matrix-Eintrag (kein Default)
# ============================================================

print(f"\n{'=' * 70}")
print("  TEST 5: Edge Case - Matrix ohne Default-Eintrag")
print("=" * 70)

params_no_default = {
    "atz": {
        "new_atz_rate": 0.03,
        "use_atz_matrix": True,
        "atz_dimension": "JobFamily",
        "atz_matrix": {
            "GruppeA": 0.0,
            "GruppeB": 1.0,
            # Kein "GruppeC", kein "GruppeD", kein "Default"
        },
    },
}

errors5 = []
for idx, row in df_state.iterrows():
    prob = _select_atz_prob(row, params_no_default)
    jf = row["Jobfamily"]
    if jf == "GruppeA":
        exp = 0.0
    elif jf == "GruppeB":
        exp = 1.0
    else:
        # GruppeC und GruppeD: kein Match, kein Default -> Fallback auf base=0.03
        exp = 0.03
    if abs(prob - exp) > 1e-9:
        errors5.append(f"  {idx}: Jobfamily={jf}, erwartet={exp}, erhalten={prob}")

if errors5:
    for e in errors5:
        print(e)
    print(f"  ERGEBNIS: {len(errors5)} FEHLER")
else:
    print("  Ohne Default-Eintrag: GruppeC/D fallen auf base_rate=0.03 zurueck.")
    print("  Fallback-Hierarchie: exakter Match -> 'Default' -> base_rate. KORREKT.")


# ============================================================
#  TEST 6: Sensitivitaets-Test - Matrix aendern und Ergebnis vergleichen
# ============================================================

print(f"\n{'=' * 70}")
print("  TEST 6: Sensitivitaet - Ergebnis aendert sich bei Matrix-Aenderung")
print("=" * 70)

# Lauf 1: GruppeA=0.0, GruppeB=1.0
selected_run1 = set()
for i in range(50):
    rng = np.random.RandomState(i * 7 + 13)
    df_iter = df_state.copy()
    df_iter["in_atz"] = False
    df_iter["active"] = True
    result = _schedule_new_atz_cases(df_iter, atz_pivot_empty.copy(), params_matrix, period, rng)
    if not result.empty:
        selected_run1.update(result[COL_PERSNR].astype(str).unique())

# Lauf 2: Werte getauscht -> GruppeA=1.0, GruppeB=0.0
params_swapped = {
    "components": {"atz": True},
    "atz": {
        "new_atz_rate": 0.05,
        "use_atz_matrix": True,
        "atz_dimension": "JobFamily",
        "atz_matrix": {
            "GruppeA": 1.0,   # JETZT hoch
            "GruppeB": 0.0,   # JETZT null
            "GruppeC": 0.5,
            "Default": 0.10,
        },
        "atz_eligible_age_min": 55,
        "atz_eligible_age_max": 60,
        "atz_duration_ar_years": 2.5,
        "atz_duration_fr_years": 2.5,
    },
    "random_seed": 42,
}

selected_run2 = set()
for i in range(50):
    rng = np.random.RandomState(i * 7 + 13)
    df_iter = df_state.copy()
    df_iter["in_atz"] = False
    df_iter["active"] = True
    result = _schedule_new_atz_cases(df_iter, atz_pivot_empty.copy(), params_swapped, period, rng)
    if not result.empty:
        selected_run2.update(result[COL_PERSNR].astype(str).unique())

a_in_run1 = {p for p in selected_run1 if p.startswith("A")}
b_in_run1 = {p for p in selected_run1 if p.startswith("B")}
a_in_run2 = {p for p in selected_run2 if p.startswith("A")}
b_in_run2 = {p for p in selected_run2 if p.startswith("B")}

print(f"  Lauf 1 (A=0, B=1): A gezogen={len(a_in_run1)}, B gezogen={len(b_in_run1)}")
print(f"  Lauf 2 (A=1, B=0): A gezogen={len(a_in_run2)}, B gezogen={len(b_in_run2)}")

errors6 = []
if len(a_in_run1) > 0:
    errors6.append("FAIL: Lauf 1 hat Gruppe A gezogen (prob=0)")
if len(b_in_run1) == 0:
    errors6.append("FAIL: Lauf 1 hat Gruppe B nie gezogen (prob=1)")
if len(a_in_run2) == 0:
    errors6.append("FAIL: Lauf 2 hat Gruppe A nie gezogen (prob=1)")
if len(b_in_run2) > 0:
    errors6.append("FAIL: Lauf 2 hat Gruppe B gezogen (prob=0)")

if errors6:
    for e in errors6:
        print(f"  {e}")
else:
    print("  Matrix-Aenderung veraendert Ergebnis korrekt:")
    print("  - Lauf 1: A=0 -> nicht gezogen, B=1 -> gezogen")
    print("  - Lauf 2: A=1 -> gezogen, B=0 -> nicht gezogen")
    print("  ERGEBNIS: SENSITIVITAET NACHGEWIESEN")


# ============================================================
#  GESAMT-ERGEBNIS
# ============================================================

all_errors = errors + errors2 + errors3 + errors4 + errors5 + errors6
print(f"\n{'=' * 70}")
print("  GESAMT-ERGEBNIS")
print("=" * 70)
if all_errors:
    print(f"  {len(all_errors)} FEHLER/WARNUNGEN insgesamt:")
    for e in all_errors:
        print(f"    {e}")
else:
    print("  ALLE 6 TESTS BESTANDEN")
    print()
    print("  Zusammenfassung:")
    print("  1. _select_atz_prob() liefert korrekte Werte je Jobfamily")
    print("  2. use_atz_matrix=False -> immer base_rate (Matrix ignoriert)")
    print("  3. Extremwerte (0/1/0.5) beeinflussen Sampling messbar")
    print("  4. Dimension-Wechsel (OrgUnit) funktioniert")
    print("  5. Fallback-Hierarchie: exakt -> Default -> base_rate")
    print("  6. Matrix-Aenderung veraendert Ergebnis (Sensitivitaet)")
    print()
    print("  FAZIT: Die ATZ-Matrix greift korrekt.")
