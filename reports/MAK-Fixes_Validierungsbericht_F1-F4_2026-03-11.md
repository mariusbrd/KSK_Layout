# MAK-Fixes Validierungsbericht - F1-F4
Datum: 2026-03-11

## Executive Summary
Die vier Fixes sind groesstenteils korrekt und wirksam umgesetzt. F1, F2 und F4 sind technisch sauber implementiert und verbessern die Vergleichbarkeit der IST-MAK-Werte deutlich.
F3 behebt den semantisch falschen `start_hc`-Fallback korrekt, hat aber weiterhin eine Prioritaetsabweichung bei der MAK-Spaltenwahl (`MAK_Calculated -> MAK -> mak` statt zentral `MAK_Calculated -> mak -> MAK`).
Es bestehen nach wie vor Restinkonsistenzen ausserhalb der vier Fixes, insbesondere in der Kompakt-Seite (`value_col="MAK" ... else "FTE_assigned"`) ohne `MAK_Calculated`/`mak`-Fallback.
In Summe: kein Blocker, aber ein mittlerer Harmonisierungspunkt bleibt offen.

## Einzelbewertung der Fixes

| Fix | Ergebnis | Anmerkung |
|-----|----------|-----------|
| F1 - resolve_mak_series() | ✅ | Oeffentliche Funktion vorhanden, Alias `_ist_mak` vorhanden und intern genutzt. |
| F2 - compute_fte_effektiv() | ✅ | Prioritaet + `fillna(0)` korrekt, Fallback erhalten, weiterhin dedup auf Mitarbeiterebene. |
| F3 - Forecast Fallback | ⚠️ | Fallback `0.0` korrekt; aber Prioritaet weicht von zentraler Regel ab (`MAK` vor `mak`). |
| F4 - Deep Dive Dedup | ✅ | Imports korrekt, dedup via `get_unique_employees()`, MAK-Lookup via `resolve_mak_series()`. |
| P-CROSS - Konsistenz | ⚠️ | Kernstellen harmonisiert, aber F3-Reihenfolge + Kompakt-Breakdown noch uneinheitlich. |

## Detailbefunde

### [FX-001] F1 korrekt umgesetzt (public + alias) - Schwere: Info
**Fix:** F1
**Datei:** `KSK_Layout/utils/exclusion_groups.py`, Zeile 71, 83, 179
**Beobachtung:** `resolve_mak_series(df)` ist oeffentlich definiert; `_ist_mak = resolve_mak_series` ist gesetzt; `aggregate_group()` nutzt `_ist_mak(sub).sum()` weiter.
**Erwartung:** Public API + Rueckwaertskompatibilitaet.
**Empfehlung:** Optional mittelfristig direkte Nutzung von `resolve_mak_series()` statt Alias fuer Klarheit.

### [FX-002] F1 empty-DataFrame Verhalten robust - Schwere: Info
**Fix:** F1
**Datei:** `KSK_Layout/utils/exclusion_groups.py`, Zeile 76-79
**Beobachtung:** Wenn keine MAK-Spalte vorhanden ist, Rueckgabe `pd.Series(0.0, index=df.index)`; bei leerem DF liefert das eine leere Series ohne Fehler.
**Erwartung:** Kein Crash bei leerem Input.
**Empfehlung:** Optional Unit-Test fuer leeren DataFrame ergaenzen.

### [FX-003] F1 ohne `__all__`, aber normal importierbar - Schwere: Niedrig
**Fix:** F1
**Datei:** `KSK_Layout/utils/exclusion_groups.py`
**Beobachtung:** Kein `__all__` definiert; `from utils.exclusion_groups import resolve_mak_series` funktioniert dennoch.
**Erwartung:** Erreichbarkeit per normalem Import.
**Empfehlung:** Optional `__all__` ergaenzen, falls explizite API-Grenzen gewuenscht sind.

### [FX-004] F2 Prioritaet + NaN-Handling korrekt - Schwere: Info
**Fix:** F2
**Datei:** `KSK_Layout/dataloader/kpi_engine.py`, Zeile 94-113
**Beobachtung:** `compute_fte_effektiv()` nutzt nun `for col in ("MAK_Calculated", "mak", "MAK")` und `fillna(0).sum()`.
**Erwartung:** Konsistenz mit zentraler Lookup-Regel.
**Empfehlung:** Keine Aenderung noetig.

### [FX-005] F2 Fallback und Rundung unveraendert sinnvoll - Schwere: Info
**Fix:** F2
**Datei:** `KSK_Layout/dataloader/kpi_engine.py`, Zeile 104-113
**Beobachtung:** BsGrd-Ruhend/ATZ-FR-Fallback blieb bestehen; Ausgabe weiterhin `round(float(...), 2)`.
**Erwartung:** Rueckwaertskompatibles Verhalten ausser bei beabsichtigter Prioritaetskorrektur.
**Empfehlung:** Keine Aenderung noetig.

### [FX-006] F3 behoben, aber Prioritaetsabweichung bleibt - Schwere: Mittel
**Fix:** F3
**Datei:** `KSK_Layout/zugaenge/forecast.py`, Zeile 1035-1036
**Beobachtung:** Neuer Code ist syntaktisch korrekt und robust (`_mak_col` + `start_mak` mit `0.0`-Fallback). Reihenfolge ist aber `("MAK_Calculated", "MAK", "mak")`.
**Erwartung:** Vollharmonisiert waere `("MAK_Calculated", "mak", "MAK")` wie in F1/F2/F4.
**Empfehlung:** Reihenfolge in F3 auf zentrale Regel angleichen.

### [FX-007] F3 Edge-Cases robust (leer/keine Spalte) - Schwere: Info
**Fix:** F3
**Datei:** `KSK_Layout/zugaenge/forecast.py`, Zeile 1035-1036
**Beobachtung:** Bei leerem `df_snapshot` oder ohne MAK-Spalten wird `start_mak = 0.0` gesetzt; kein Zugriff auf ungueltige Spalte.
**Erwartung:** Kein Crash, neutraler Startwert.
**Empfehlung:** Keine Aenderung noetig.

### [FX-008] F4 technisch korrekt und semantisch verbessert - Schwere: Info
**Fix:** F4
**Datei:** `KSK_Layout/pages/6_🔎_Deep_Dive_Exklusionsgruppen.py`, Zeile 22-31, 199-205
**Beobachtung:** Neue Imports vorhanden (`resolve_mak_series`, `get_unique_employees`); `active_ist_mak` nutzt deduplizierte Mitarbeiterebene und zentrale MAK-Aufloesung.
**Erwartung:** Naehere Deckung zu Kompakt (`compute_fte_effektiv`).
**Empfehlung:** Keine Aenderung noetig.

### [FX-009] P-CROSS: Nicht alle Stellen sind bereits voll harmonisiert - Schwere: Mittel
**Fix:** CROSS
**Datei:** `KSK_Layout/zugaenge/forecast.py`, Zeile 1035; `KSK_Layout/pages/1_⚡_Kompakt.py`, Zeile 1471
**Beobachtung:** F3-Prioritaet weicht ab; Kompakt-Breakdown nutzt `value_col="MAK" if ... else "FTE_assigned"` ohne `MAK_Calculated`/`mak`-Fallback.
**Erwartung:** Einheitliche Spaltenstrategie ueber alle relevanten Pfade.
**Empfehlung:** Zentralen Resolver auch im Kompakt-Breakdown nutzen oder vorab eine kanonische MAK-Spalte setzen.

## Offene Restrisiken nach den Fixes
- Prioritaetsabweichung in F3 (`MAK` vor `mak`) kann in gemischten DataFrames andere Startwerte erzeugen als F1/F2/F4.
- Kompakt-Breakdown (`pages/1`) bleibt teilweise auf `MAK` fokussiert und faellt sonst auf `FTE_assigned` zurueck; das ist nicht identisch zur neuen zentralen MAK-Resolution.
- `resolve_mak_series()` hat kein explizites `__all__`; funktional unkritisch, aber API-Explizitheit fehlt.

## Fazit
Die Fixes F1, F2 und F4 sind vollstaendig und korrekt umgesetzt. F3 ist funktional korrekt bezueglich des eigentlichen Bugs (kein Headcount-als-MAK-Fallback mehr), aber noch nicht voll harmonisiert in der Spaltenprioritaet. Es gibt keine Hinweise auf neue Crashes oder zirkulaere Imports durch die Aenderungen. Fuer vollstaendige MAK-Harmonisierung ist vor allem die Prioritaetsangleichung in F3 sowie ein Nachzug im Kompakt-Breakdown der naechste sinnvolle Schritt.
