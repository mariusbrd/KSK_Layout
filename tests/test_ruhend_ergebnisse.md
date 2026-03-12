# Testergebnisse: Ruhend-Funktion (MAK & Headcount)

**Datum:** 2026-03-09
**Datei:** `tests/test_ruhend_mak_headcount.py`
**Ergebnis:** 9/9 bestanden

---

## Testergebnisse

| TC | Beschreibung | Ergebnis | Befund |
|----|---|---|---|
| TC-01 | Initial-Ruhend zaehlt im Headcount | OK | HC = 2 (beide MA enthalten) |
| TC-02 | Initial-Ruhend hat MAK = 0 am Stichtag | OK | MAK = 0.75 (nur aktiver MA) |
| TC-03 | Initial-Ruhend kehrt zurueck: MAK-Bug | DOKUMENTIERT | mak_change bei Rueckkehr = 0.0 statt > 0 |
| TC-04 | Neu-Ruhend Start: HC unveraendert, MAK sinkt | OK | HC Delta=0, MAK Delta < 0 |
| TC-05 | Neu-Ruhend + Rueckkehr: MAK wiederhergestellt | OK | MAK 0.75 -> 0 -> 0.75 |
| TC-06 | Ruhend-MA vom ATZ ausgeschlossen | OK | Nur aktiver MA in ATZ-Pivot |
| TC-07 | Ruhend + Rente: Interaktion | DOKUMENTIERT | PersNr-Mismatch in aggregate_forecast_results |
| TC-08 | MAK-Verlauf via Events: Start -> Ruhend -> Rueckkehr | OK | HC Delta=0, MAK < 0 bei Start, > 0 bei Rueckkehr |
| TC-09 | Mehrfach-Ruhend: kein HC-Effekt | OK | 10 Events, alle headcount_change=0 |

---

## Dokumentierte Bugs

### Bug 1: Initial-Ruhend-Rueckkehr stellt MAK nicht wieder her (TC-03)

**Ort:** `abgaenge/forecast.py`, Zeile 610
**Ursache:** `mak_before_ruhend` wird aus `df_state["mak"]` gesetzt, *nachdem* MAK fuer Ruhend-MAs auf 0 gesetzt wurde.
**Effekt:** Zurueckkehrende Initial-Ruhend-MAs erhalten MAK = 0 statt ihres urspruenglichen MAK.
**Betrifft:** Nur initial-ruhende MA. Neu-ruhende MA (RUHEND_START waehrend Forecast) sind korrekt.

```python
# Zeile 599-610 (vereinfacht):
df_state["mak"] = 0.0          # <-- MAK wird hier auf 0 gesetzt fuer Ruhend-MAs
df_state["mak_before_ruhend"] = df_state["mak"]  # <-- speichert 0, nicht den echten MAK
```

**Fix:** `mak_before_ruhend` muss aus `raw_mak` (vor dem Nullsetzen) berechnet werden.

---

### Bug 2: PersNr-Mismatch in aggregate_forecast_results (TC-07/08)

**Ort:** `abgaenge/forecast.py`, Funktion `aggregate_forecast_results`
**Ursache:** `run_forecast_abgaenge` normalisiert PersNr auf 6-stellige Strings (`"1001"` -> `"001001"`). `aggregate_forecast_results` baut den df_state-Index aus dem rohen df_ma auf (nicht normalisiert). Events werden nicht auf Personen gemappt.
**Effekt:** KPI-Timeline (Headcount/MAK) spiegelt Events nicht wider, wenn PersNr nicht bereits 6-stellig ist.
**Betrifft:** Alle Event-Typen (Rente, Ruhend, Kuendigung) bei nicht-6-stelligen PersNr.
