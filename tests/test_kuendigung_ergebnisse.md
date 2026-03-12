# Testergebnisse: Kuendigungs-Funktion (QUIT)

**Datum:** 2026-03-09
**Datei:** `tests/test_kuendigung_forecast.py`
**Ergebnis:** 13/13 bestanden

---

## Teil A: Pauschale Fluktuationsquote (`use_quit_matrix=False`)

| TC | Beschreibung | Ergebnis | Befund |
|----|---|---|---|
| A-01 | Rate=0 -> keine Kuendigungen | OK | 0 QUIT-Events |
| A-02 | Rate=1.0 -> alle MA kuendigen | OK | 10/10 MA gekuendigt |
| A-03 | QUIT-Event-Schema korrekt | OK | HC=-1, MAK<0, reason="QUIT" fuer alle Events |
| A-04 | ATZ-MA vom Quit ausgeschlossen | OK | ATZ-MA nicht in Quits, aktiver MA schon |
| A-05 | HC-Reduktion kumulativ und dauerhaft | OK | HC monoton fallend ueber alle Perioden |

---

## Teil B: Detaillierte Kuendigungsmatrix (`use_quit_matrix=True`)

| TC | Beschreibung | Ergebnis | Befund |
|----|---|---|---|
| B-01 | Matrix nach JobFamily: Rate differenziert | OK | JF 'Hoch' (1.0) gekuendigt, JF 'Null' (0.0) nicht |
| B-02 | Matrix nach OrgUnit: Rate differenziert | OK | OE 'OE-Hoch' (1.0) gekuendigt, OE 'OE-Null' (0.0) nicht |
| B-03 | Fallback: JF -> Default -> base_rate | OK | Alle drei Faelle korrekt (Default greift, base_rate greift) |
| B-04 | Altersgruppen werden beruecksichtigt | OK | Unter_30 (Rate=1.0) gekuendigt, 55+ (Rate=0.0) nicht |
| B-05 | Adjustment 'more': +50% Kuendigungen | OK | 32 (Adjust) vs. 26 (Normal) bei gleicher Basisrate |
| B-06 | Adjustment 'less': -50% Kuendigungen | OK | 25 (Reduce) vs. 37 (Normal) bei gleicher Basisrate |
| B-07 | Adjustments wirken immer ueber Jobfamily | OK | Kein Effekt wenn rate=0, auch mit 'more'-Adjustment |
| B-08 | Konsistenz: Pauschalrate == Matrix (gleiche Rate) | OK | Exakt 12 Quits in beiden Modi bei Seed=42 |

---

## Wichtige Verhaltenseigenschaften (bestaetigt)

- **ATZ-Ausschluss:** `in_atz=True` verhindert Kuendigungen zuverlaessig
- **Ruhend kein Schutz:** Ruhend-MAs sind NICHT explizit ausgeschlossen (koennen kuendigen)
- **Fallback-Reihenfolge:** exakter Treffer -> "Default" -> `quit_rate_base`
- **Adjustments JF-gebunden:** 'more'/'less' wirkt immer ueber `Jobfamily`, NIE ueber OrgUnit (auch wenn `quit_dimension=OrgUnit`)
- **Determinismus:** Gleicher Seed + gleiche Rate -> identische Ergebnisse in beiden Modi
