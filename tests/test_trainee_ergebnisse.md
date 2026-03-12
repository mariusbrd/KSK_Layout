# Trainee Forecast – Testergebnis

**Datum:** 2026-03-10
**Tests:** 31 / 31 PASS
**Datei:** `KSK_Layout/tests/test_trainee_forecast.py`

---

## Testergebnis nach Kategorie

| Kategorie | Tests | Ergebnis |
|-----------|------:|---------|
| Grundlegende Einstellung (T01–T05) | 5 | ✅ alle grün |
| Jährliches Volumen / Debt-System (T06–T08) | 3 | ✅ alle grün |
| HC & MAK-Auswirkung (T09–T10) | 2 | ✅ alle grün |
| Salary Group (T11–T13) | 3 | ✅ alle grün |
| Jobfamily & Planstelle (T14–T15) | 2 | ✅ alle grün |
| OrgUnit-Strategie (T16–T18) | 3 | ✅ alle grün |
| Deterministik / Seed (T19–T20) | 2 | ✅ alle grün |
| Active-Flag (T21) | 1 | ✅ alle grün |
| Edge Cases (E01–E10) | 10 | ✅ alle grün |
| **Gesamt** | **31** | **✅ 31/31** |

---

## Bestätigte Invarianten

| # | Invariante | Status |
|---|-----------|--------|
| MAK sofort | `Trainee_Hire` hat `mak=1.0` — Trainees sind ab Tag 1 MAK-wirksam | ✅ bestätigt |
| HC +1 | `count=1` auf jedem Hire-Event | ✅ bestätigt |
| Debt-System | Kumulierter Jahresfehler ≤ 1 über 3 Jahre | ✅ bestätigt |
| Deterministik | Gleicher Seed → identische Counts & Daten | ✅ bestätigt |
| OrgUnit-Strategie | Random, OrgUnit-Target und Fill Vacancies alle korrekt | ✅ bestätigt |
| Active-Flag | `active=False` → keine Events | ✅ bestätigt |
| Datum-Bereich | Alle Entry-Dates liegen im Prognosezeitraum | ✅ bestätigt |
| ID-Eindeutigkeit | Keine ID-Kollisionen in 150-Trainee-Lauf (50/Jahr × 3 Jahre) | ✅ bestätigt |

---

## Architektur-Beobachtungen (keine Bugs, aber dokumentierungswürdig)

### 1. Kein Lebenszyklus (by design)
Trainees haben keine Graduation-, Conversion- oder Exit-Logik. Sie werden eingestellt und verbleiben dauerhaft als `active=True` im State. Der Parameter `duration_years` wird in der UI angezeigt (Formularfeld) und in `run_params` übergeben, **wird von `_simulate_trainees` jedoch nie verwendet.** Das ist eine bewusste Vereinfachung — E06 bestätigt, dass keine Abschluss-Events erzeugt werden.

**Implikation:** Langfristige Simulationen akkumulieren Trainees unbegrenzt im State. Für HC-/MAK-Bilanzen im Dashboard ist das unkritisch, weil Trainees nur Hire-Events erzeugen und kein Folge-Lifecycle haben.

### 2. ID-Kollisionsrisiko (theoretisch)
IDs werden als `f"TR_{rng.integers(10000, 99999)}"` generiert — ein 5-stelliger Zufallsbereich mit 90.000 Möglichkeiten. E05 (150 Trainees) bestätigt Eindeutigkeit für realistische Volumina. Bei sehr hohen Volumina (>1.000 Trainees) steigt das Kollisionsrisiko. **Empfehlung**: analoges Muster wie Azubis (`AZ_YEAR_N01_xxxx`) würde IDs strukturierter und Jahr-bezogen machen.

### 3. `count_annual` ist Dead Code
In `_simulate_trainees` Zeile 648 wird `count_annual` aus den Params gelesen, aber niemals verwendet — die Schleife läuft über `num_cases` (vom Caller übergeben). Kein funktionaler Fehler, aber unnötiger Code.

### 4. Fehlende State-Felder
Im `new_row`-Dict fehlen `OE-Cluster`, `JF-Cluster`, `is_forecast`. Das ist harmlos weil:
- Cluster-Felder: Trainees haben keine Cluster-Logik
- `is_forecast`: Trainees werden nie als Baseline verarbeitet, kein `exclude_baseline`-Mechanismus existiert für Trainees

---

## Empfehlung

| Priorität | Maßnahme |
|-----------|---------|
| Low | `count_annual`-Zeile in `_simulate_trainees` entfernen (Dead Code) |
| Low | ID-Schema auf `TR_{year}_N{i:02d}_{hex4}` umstellen (analog Azubis) |
| Info | `duration_years`-Parameter in UI-Kommentar als "zukünftig" kennzeichnen, solange kein Lifecycle implementiert ist |
