# MAK-Validierungsbericht - KSK Boeblingen HR-Dashboard
Datum: 2026-03-11
Gepruefte Dateien: 10

## Executive Summary
Die Kernlogik fuer MAK im Snapshot ist in `loader.py` intern konsistent: `berechne_mak()` und `calculate_mak_vectorized()` liefern dieselbe Semantik (Vacancy/Ruhend/ATZ-FR => 0, sonst `BsGrd/100`).
Die groessten Risiken liegen nicht in der Formel selbst, sondern in spaltenbezogenen Fallbacks (`MAK_Calculated` vs `MAK` vs `mak`) und in seitenuebergreifenden Aggregationsebenen (Mitarbeiter- vs Planstellenebene).
Azubi-Design (Hire MAK=0, Takeover MAK>0) ist durchgaengig korrekt implementiert und auf der Zugaenge-Seite fachlich kommuniziert.
Exklusionen nullen IST-Metriken korrekt und erhalten SOLL-Felder wie vorgesehen.
Wichtigster technischer Befund: `kpi_reference.compute_fte()` ist nicht 1:1 deckungsgleich mit Snapshot-MAK, weil `Is_Vacant` dort nicht beruecksichtigt wird.

## Bewertungsmatrix

| Pruefpunkt | Ergebnis | Schwere | Kommentar |
|-----------|----------|---------|-----------|
| P1 - Formel-Konsistenz | ⚠️ | Mittel | Loader-Formeln konsistent; `kpi_reference.compute_fte()` ohne Vacancy-Check, daher nicht identisch fuer gleiche Inputs inkl. `Is_Vacant`. |
| P2 - Column-Namen | ⚠️ | Mittel | Uneinheitliche Prioritaeten zwischen Seiten/Utils (`MAK_Calculated`, `MAK`, `mak`). |
| P3 - Azubi-Logik | ✅ | Niedrig | Loader + Forecast + UI sind konsistent mit Design (Hire=0 MAK, Takeover wirksam). |
| P4 - Exklusions-Impact | ✅ | Niedrig | `apply_exclusions()` setzt `Is_Vacant=True` und nullt alle relevanten IST-Felder inkl. MAK-Spalten. |
| P5 - Ruhend-BV | ✅ | Niedrig | Statusbasierte Definition in Loader + Forecast konsistent; OE 9900 bleibt separate Gruppe. |
| P6 - ATZ-FR | ✅ | Niedrig | Row-/vectorized-Handling in Loader konsistent; Forecast behandelt FR als MAK=0 bei HC=1. |
| P7 - Aggregationsebene | ⚠️ | Mittel | Page 3/4 arbeiten mitarbeiterbasiert, Page 6 summiert zeilenbasiert; direkte Vergleichbarkeit begrenzt. |
| P8 - SOLL-MAK | ✅ | Niedrig | SOLL basiert konsistent auf `Soll_FTE` (Fallback `Sollarbeitszeit/39`) und wird in Deep Dive korrekt um Exklusions-Union bereinigt. |
| P9 - Numerik & Rundung | ✅ | Niedrig | Keine kritische NaN-Propagation im MAK-Kern; Rundungen primar in Anzeigeebene. |
| P10 - UI-Semantik | ⚠️ | Mittel | Labels sind meist korrekt, aber die angezeigte Naehe von Deep-Dive-IST zu Kompakt kann je nach Aggregationsebene abweichen. |

## Detailbefunde

### [F-001] Referenz-Implementierung nicht voll deckungsgleich mit Snapshot-MAK - Schwere: Mittel
**Datei:** `KSK_Layout/kpi_reference.py`, Zeile 211-235
**Beobachtung:** `compute_fte()` nutzt `BsGrd/100` und nullt nur fuer Ruhend/ATZ-FR; `Is_Vacant` wird nicht geprueft.
**Erwartetes Verhalten:** Bei Anspruch auf Identitaet zu Snapshot-MAK sollte Vacancy ebenfalls zu 0 fuehren.
**Tatsaechliches Verhalten:** Bei identischen Eingaben inkl. `Is_Vacant=True` kann `compute_fte()` > 0 liefern.
**Empfehlung:** Entweder (a) Referenz explizit als mitarbeiterbasierte Roh-FTE ohne Vacancy definieren, oder (b) Vacancy-Maske ergänzen.

### [F-002] Spaltenprioritaet fuer IST-MAK ist nicht systemweit einheitlich - Schwere: Mittel
**Datei:** `KSK_Layout/utils/exclusion_groups.py`, Zeile 71-76; `KSK_Layout/dataloader/kpi_engine.py`, Zeile 94-111; `KSK_Layout/pages/6_🔎_Deep_Dive_Exklusionsgruppen.py`, Zeile 201-206; `KSK_Layout/pages/1_⚡_Kompakt.py`, Zeile 1471
**Beobachtung:** `_ist_mak()` priorisiert `MAK_Calculated -> mak -> MAK`; `compute_fte_effektiv()` nutzt primär `MAK`; Deep Dive nutzt `MAK_Calculated -> MAK`; Kompakt-Breakdown nimmt `MAK` oder `FTE_assigned`.
**Erwartetes Verhalten:** Einheitliche Prioritaet auf allen Seiten.
**Tatsaechliches Verhalten:** Potenziell unterschiedliche Summen bei heterogenen DataFrames.
**Empfehlung:** Zentrale Helper-Funktion (z. B. `resolve_mak_series(df)`) erstellen und ueberall verwenden.

### [F-003] Forecast-Zugaenge: fragiler Start-MAK-Fallback - Schwere: Mittel
**Datei:** `KSK_Layout/zugaenge/forecast.py`, Zeile 1034-1036
**Beobachtung:** `start_mak` wird aus `df_snapshot['mak']` gelesen, sonst auf `start_hc` gefallbackt.
**Erwartetes Verhalten:** Fallback sollte `MAK_Calculated`/`MAK` pruefen, nicht Headcount als MAK-Ersatz.
**Tatsaechliches Verhalten:** Bei fehlendem `mak` kann Anfangs-MAK falsch sein.
**Empfehlung:** Fallback-Reihenfolge analog P2 auf `MAK_Calculated -> MAK -> mak -> 0` umstellen.

### [F-004] Deep-Dive-Aktiv-IST-MAK nicht strikt deckungsgleich zu Kompakt-IST-MAK - Schwere: Mittel
**Datei:** `KSK_Layout/pages/6_🔎_Deep_Dive_Exklusionsgruppen.py`, Zeile 197-206; `KSK_Layout/dataloader/kpi_engine.py`, Zeile 36-111
**Beobachtung:** Deep Dive summiert aktive, besetzte Zeilen direkt; Kompakt-Top-KPI nutzt deduplizierte Mitarbeiterlogik.
**Erwartetes Verhalten:** Wenn als "≈ Kompakt IST-MAK" gelabelt, sollte Aggregationslogik identisch sein.
**Tatsaechliches Verhalten:** Bei Mehrfach-Planstellen pro PersNr sind Abweichungen moeglich.
**Empfehlung:** In Deep Dive fuer `active_ist_mak` optional `get_unique_employees()` + gemeinsame MAK-Resolution verwenden.

### [F-005] P3/P4 fachlich korrekt implementiert (Bestaetigung) - Schwere: Niedrig
**Datei:** `KSK_Layout/dataloader/loader.py`, Zeile 377-423, 426-512, 575-580/639-643/700-703; `KSK_Layout/zugaenge/forecast.py`, Zeile 286-318, 404-463, 531-625
**Beobachtung:** Azubi-MAK wird in Loader zentral genullt (inkl. `MAK_Calculated`, `MAK`, `mak`, `BsGrd` + `_raw`), Exklusionen nullen IST-Metriken und setzen `Is_Vacant=True`.
**Erwartetes Verhalten:** Genau dieses Design.
**Tatsaechliches Verhalten:** Konform.
**Empfehlung:** Keine funktionale Aenderung erforderlich.

## Konsistenz-Checkliste: Seitenuebergreifend

| Aspekt | Page 1 Kompakt | Page 3 Abgaenge | Page 4 Zugaenge | Page 6 Deep Dive | Konsistent? |
|--------|----------------|-----------------|-----------------|------------------|-------------|
| MAK-Spaltenname | KPI via `compute_fte_effektiv` (primär `MAK`), Breakdown via `MAK` | Vorverarbeitung nutzt `MAK_Calculated` -> `mak` | Vorverarbeitung nutzt `MAK_Calculated` -> `mak` | `MAK_Calculated` dann `MAK` | ❌ |
| Aggregationsebene | KPI mitarbeiterbasiert (dedup), teils gemischt in Breakdowns | explizit mitarbeiterbasiert (`groupby PersNr`) | explizit mitarbeiterbasiert (`groupby PersNr`) | ueberwiegend zeilenbasiert | ❌ |
| Azubi-Behandlung | indirekt ueber Loader/MAK-Spalten | nicht zentrales Thema | Hire MAK=0, Takeover MAK>0 | spiegelt Loader-Zustand | ✅ |
| Ruhend-Behandlung | ueber Loader/KPI-Engine (Status) | Status-basiert im Forecast | ueber vorgeladene MAK-Werte | eigene Masken Status-basiert | ✅ |
| ATZ-FR-Behandlung | ueber Loader/KPI-Engine | FR reduziert MAK, HC bleibt | ueber vorgeladene MAK-Werte | je nach MAK-Spalte | ✅ |
| Exklusions-Impact | wirksam ueber Loader | wirksam im geladenen Snapshot | wirksam im geladenen Snapshot | zusaetzliche Union-Maske fuer Transparenz | ✅ |

## Empfehlungen nach Prioritaet

### Prioritaet 1 - Sofort beheben (Kritisch)
1. Keine kritischen Rechenfehler gefunden.

### Prioritaet 2 - Kurzfristig beheben (Mittel)
1. Einheitliche MAK-Spaltenauflösung zentralisieren und in Kompakt/DeepDive/KPI-Engine/Forecast verwenden.
2. `zugaenge/forecast.py` Start-MAK-Fallback korrigieren (`start_hc` nicht als MAK-Ersatz verwenden).
3. Deep-Dive `active_ist_mak` auf dieselbe Dedup-Logik wie Kompakt heben oder Label klar als zeilenbasiert kennzeichnen.

### Prioritaet 3 - Mittelfristig / Nice-to-have (Niedrig)
1. `kpi_reference.compute_fte()` semantisch explizit dokumentieren (mit/ohne Vacancy) oder optionalen Vacancy-Parameter einfuehren.
2. In UI-Tooltips je Seite Aggregationsniveau (Mitarbeiter vs Planstelle) explizit machen.

## Fazit
Die MAK-Berechnung im Kern ist robust und fuer die operativen Snapshot-Pfade konsistent implementiert. Die wichtigsten verbleibenden Abweichungen entstehen durch unterschiedliche Spaltenprioritaeten und unterschiedliche Aggregationsebenen zwischen Seiten, nicht durch eine fehlerhafte Grundformel. Mit 2-3 gezielten Harmonisierungsschritten (Spaltenauflösung + Aggregationsvereinheitlichung + Forecast-Fallback) ist die MAK-Darstellung dashboardweit deutlich belastbarer und besser vergleichbar.
