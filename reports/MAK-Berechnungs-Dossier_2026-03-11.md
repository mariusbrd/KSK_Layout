# MAK-Berechnungs-Dossier — KSK Böblingen HR-Dashboard
Erstellt: 2026-03-11
Analysierte Dateien: 
- KSK_Layout/dataloader/loader.py
- KSK_Layout/dataloader/kpi_engine.py
- KSK_Layout/kpi_reference.py
- KSK_Layout/utils/exclusion_groups.py
- KSK_Layout/abgaenge/forecast.py
- KSK_Layout/zugaenge/forecast.py
- KSK_Layout/pages/1_⚡_Kompakt.py
- KSK_Layout/pages/3_📉_Prognose_Abgänge.py
- KSK_Layout/pages/4_📈_Prognose_Zugänge.py
- KSK_Layout/pages/6_🔎_Deep_Dive_Exklusionsgruppen.py

---

## 1. Zusammenfassung der MAK-Berechnungslogik

### 1.1 Grundformel
**Beobachtung im Code**
- Zeilenweise Formel (`berechne_mak`): `MAK = BsGrd / 100.0`, außer bei Nullstellenbedingungen (`loader.py:208-254`).
- Vektorisierte Formel (`calculate_mak_vectorized`): baseline `MAK_Calculated = BsGrd.fillna(0)/100.0` (`loader.py:1037-1040`).

**Pseudocode (faktisch implementiert)**
```text
if Is_Vacant: 0
elif Status == "Ruhendes Beschäftigungsverhältnis": 0
elif ist_atz_fr == True or PersNr in atz_fr_set: 0
else: BsGrd / 100
```

### 1.2 Nullstellenbedingungen (MAK = 0 obwohl besetzt)
MAK wird auf 0 gesetzt bei:
- Vakanz (`Is_Vacant == True`) (`loader.py:230-232`, `1044-1045`)
- Ruhendes BV via Status (`loader.py:235-236`, `1048-1049`)
- ATZ-Freistellungsphase (`loader.py:240-247`, `1052-1055`)
- Azubi-Regel (hartes Zeroing) über `_zero_out_azubi_mak` (`loader.py:377-423`)
- Exklusion (direktes Nullen der IST-Metriken) (`loader.py:491-510`)

### 1.3 SOLL-MAK vs. IST-MAK
- **SOLL-MAK**: `Soll_FTE.sum()` (Planbedarf), z. B. Kompakt `get_soll_mak` (`pages/1_⚡_Kompakt.py:367-370`), Deep Dive (`pages/6_🔎_Deep_Dive_Exklusionsgruppen.py:162-167`).
- **IST-MAK**: effektive Kapazität aus MAK-Spalten, meist via `compute_fte_effektiv` (`kpi_engine.py:94-113`) bzw. `resolve_mak_series` (`exclusion_groups.py:71-79`).
- Semantik ist getrennt und im Code konsistent: Soll bleibt bei Exklusion erhalten, Ist wird 0 (`loader.py:512`).

---

## 2. Implementierungs-Inventar

### 2.1 Berechnungsfunktionen
| Funktion | Datei | Zeilen | Typ | Wird aufgerufen von |
|----------|-------|--------|-----|---------------------|
| `berechne_mak` | `dataloader/loader.py` | 208-254 | row-by-row | `enrich_snapshot_data` (`loader.py:348`) |
| `calculate_mak_vectorized` | `dataloader/loader.py` | 1031-1057 | vektorisiert | `combine_to_snapshot` (`loader.py:1160`), Seiten 3/4 (`pages/3...:112`, `pages/4...:78`) |
| `_zero_out_azubi_mak` | `dataloader/loader.py` | 377-423 | rule override | `load_and_prepare_data` Pfade (`loader.py:576, 639, 700`) |
| `compute_fte_effektiv` | `dataloader/kpi_engine.py` | 94-113 | KPI-Aggregation | Kompakt via `get_ist_mak` (`pages/1...:349-352`) |
| `resolve_mak_series` | `utils/exclusion_groups.py` | 71-79 | Spalten-Lookup | Deep Dive IST (`pages/6...:204`) |
| `compute_fte` (Referenz) | `kpi_reference.py` | 211-235 | Referenzlogik | Referenz-Validierung (`kpi_reference.py:556-615`) |

### 2.2 MAK-Spaltenvarianten
| Spaltenname | Erstellt in | Zeile | Priorität in Lookup | Hinweis |
|-------------|-------------|-------|---------------------|---------|
| `MAK` | `enrich_snapshot_data` | `loader.py:348` | niedrig (hinter `mak`) in zentralem Lookup | row-wise berechnet |
| `MAK_Calculated` | `calculate_mak_vectorized` | `loader.py:1039` | höchste Priorität | vektorisierte Hauptspalte |
| `mak` | Forecast-/Page-Aggregationspfade | z. B. `pages/3...:134`, `pages/4...:104`, `abgaenge/forecast.py:614` | mittlere Priorität | Engine-interne Arbeitsspalte |

### 2.3 Aggregationsebenen je Seite
| Seite | Ebene | Deduplizierung | MAK-Spalte |
|-------|-------|----------------|------------|
| Page 1 Kompakt | Mitarbeiter-Ebene für IST-KPIs via `get_unique_employees` | ja (`kpi_engine.py:36-80`) | `MAK_Calculated→mak→MAK` (`kpi_engine.py:101-103`) |
| Page 3 Abgänge | explizit auf Mitarbeiter aggregiert (`groupby PersNr`) | ja (`pages/3...:114-132`) | `MAK_Calculated` + `mak` |
| Page 4 Zugänge | explizit auf Mitarbeiter aggregiert (`groupby PersNr`) | ja (`pages/4...:80-105`) | `MAK_Calculated` + `mak` |
| Page 6 Deep Dive | aktive IST über dedup + Lookup | ja (`pages/6...:203-204`) | `resolve_mak_series` |

---

## 3. Sonderregeln und Spezialfälle

### 3.1 Azubi-MAK-Regel
- Identifikation Azubi: `TrfGr` enthält `TVA` oder `Jobfamily` enthält `Azubi|Ausbildung` (`loader.py:394-397`).
- Zeroing-Spalten: `MAK_Calculated`, `MAK`, `mak`, `BsGrd` (`loader.py:399-421`).
- Backup-Spalten: `*_raw` werden einmalig angelegt (`loader.py:401-402`, `407-408`, `413-414`, `419-420`).
- Zugänge-Engine: Azubi-Hire mit `mak_during` (Default 0.0) (`zugaenge/forecast.py:532`, `589`, `622`), Übernahme mit `mak_after` (`zugaenge/forecast.py:287`, `437`, `463`).

### 3.2 ATZ-Freistellungsphase
- Identifikation im Snapshot über `ist_atz_fr` bzw. Set (`loader.py:240-247`, `1052-1055`).
- Abgänge-Forecast setzt beim AR→FR Übergang `mak=0` bei HC unverändert (`abgaenge/forecast.py:674-690`).

### 3.3 Ruhendes Beschäftigungsverhältnis
- Definition statusbasiert: `Status kundenindividuell == "Ruhendes Beschäftigungsverhältnis"` (`loader.py:235-236`, `1048-1049`).
- Exklusion nutzt dieselbe Statusdefinition (`loader.py:454-459`).
- Forecast: Ruhend speichert `mak_before_ruhend` und stellt bei Return wieder her (`abgaenge/forecast.py:625`, `796-805`, `817-821`).

### 3.4 Soll_FTE = 0.01 Artefakt
- Normalisierung vorhanden: `Soll_FTE == 0.01 -> 0.0` (`loader.py:1127-1129`, `1325-1327`).
- Betroffen sind alle Soll-basierten KPIs (z. B. `get_soll_mak`, Deep Dive Soll-KPIs).
- Restrisiko: exakte Gleichheitsprüfung auf `0.01` kann bei numerischem Rauschen danebenliegen.

### 3.5 Exklusionen
- `apply_exclusions` setzt `Is_Vacant=True`, leert Personendaten und nullt IST-Felder inkl. MAK (`loader.py:497-510`).
- `Soll_FTE`/`Sollarbeitszeit` bleiben bewusst erhalten (`loader.py:512`).
- Reihenfolge ist stabil: nach Azubi-Zeroing und nach Enrichment in allen Datenpfaden (`loader.py:576/580`, `639/643`, `700/704`).

---

## 4. Konsistenz-Audit

### 4.1 Formel-Konsistenz (berechne_mak vs. calculate_mak_vectorized vs. kpi_reference)
- `berechne_mak` und `calculate_mak_vectorized` sind für Standardfälle semantisch gleich (Vacant/Ruhend/ATZ-FR -> 0, sonst `BsGrd/100`).
- Abweichung bei fehlender `BsGrd`-Spalte: vectorized setzt default `1.0` (`loader.py:1041`), row-wise würde mangels Spalte auf `0` fallen (`loader.py:250-254`).
- `kpi_reference.compute_fte` setzt FTE_effektiv aus `BsGrd/100` mit Ruhend/ATZ-FR, ohne `Is_Vacant`-Prüfung (`kpi_reference.py:222-230`).

### 4.2 Spalten-Lookup-Konsistenz
- Zentrale Priorität: `MAK_Calculated -> mak -> MAK` in `resolve_mak_series` (`exclusion_groups.py:76-79`) und `compute_fte_effektiv` (`kpi_engine.py:101-103`).
- Kompakt IST nutzt `compute_fte_effektiv` (`pages/1...:349-352`).
- Deep Dive IST nutzt `resolve_mak_series` (`pages/6...:203-204`).
- Zugänge-Forecast Start-MAK nutzt identische Priorität (`zugaenge/forecast.py:1035-1036`).

### 4.3 Aggregationsebenen-Konsistenz
- Seiten 3/4 aggregieren MAK explizit auf Mitarbeiter (`pages/3...:114-135`, `pages/4...:80-105`).
- Kompakt/Deep Dive deduplizieren über `get_unique_employees`, das aber nur `Sollarbeitszeit`/`Soll_FTE` summiert; MAK-Spalten werden **nicht** über Mehrfachplanstellen summiert (`kpi_engine.py:55-75`).

### 4.4 Forecast-Konsistenz
- Abgänge startet mit `MAK_Calculated` falls vorhanden (`abgaenge/forecast.py:613-615`), sonst Rechenfallback (`616-621`).
- Zugänge startet mit Snapshot-Summe der priorisierten MAK-Spalte (`zugaenge/forecast.py:1035-1036`), fallback `0.0`.
- Abgänge begrenzt `mak` non-negative (`abgaenge/forecast.py:470`).
- Azubi-Timing in Zugänge ist konsistent (MAK bei Hire 0, bei Conversion In positiv).

---

## 5. Identifizierte Risiken und Befunde

### 5.1 Kritische Befunde (können zu falschen Zahlen führen)

#### [K1] Unterzählung IST-MAK bei Mehrfachplanstellen in `compute_fte_effektiv`
**Datei:** `KSK_Layout/dataloader/kpi_engine.py`, Zeilen 36-80 und 94-103
**Beobachtung:** `get_unique_employees` summiert nur `Sollarbeitszeit`/`Soll_FTE`, nicht `MAK*`. Danach wird in `compute_fte_effektiv` die MAK-Spalte der deduplizierten „ersten“ Zeile summiert.
**Beispieltest:** 1 PersNr mit 2x `MAK_Calculated=0.5` ergibt `0.5` statt `1.0`.
**Erwartetes Verhalten:** Für Mitarbeiter-Kapazität sollten MAK-Beiträge über alle Planstellen je PersNr summiert werden.
**Empfehlung:** In `get_unique_employees` optional `MAK_Calculated`, `mak`, `MAK`, `BsGrd` aggregieren (sum) oder vor `compute_fte_effektiv` dedizierte MAK-Aggregation je PersNr bauen.

#### [K2] Uneinheitliches Verhalten bei fehlender `BsGrd`-Spalte
**Datei:** `KSK_Layout/dataloader/loader.py`, Zeilen 1037-1041 vs. 250-254
**Beobachtung:** Vectorized setzt `MAK_Calculated=1.0`, row-wise ergibt faktisch 0.
**Risiko:** Unterschiedliche MAK-Basiswerte je Aufrufpfad bei unvollständigen Uploads.
**Empfehlung:** Einheitliche Policy definieren (konservativ 0.0 empfohlen) und in beiden Funktionen angleichen.

### 5.2 Mittlere Befunde (können zu Inkonsistenzen führen)

#### [M1] `kpi_reference.compute_fte` ohne `Is_Vacant`-Kriterium
**Datei:** `KSK_Layout/kpi_reference.py`, Zeilen 222-230
**Beobachtung:** Keine explizite Vakanz-Nullung im Referenzpfad.
**Bewertung:** Nur unkritisch, wenn Input bereits auf besetzte Mitarbeitende reduziert ist; sonst Vergleichbarkeit eingeschränkt.

#### [M2] 0.01-Normalisierung basiert auf exakter Gleichheit
**Datei:** `KSK_Layout/dataloader/loader.py`, Zeilen 1127-1129 und 1325-1327
**Beobachtung:** `== 0.01` kann bei Float-Rundung fragil sein.
**Empfehlung:** Toleranzbasierte Normalisierung (`abs(x-0.01) < 1e-9`) oder integer-basierte Vorverarbeitung.

#### [M3] Fallback `MAK_Calculated=1.0` bei fehlender `BsGrd`
**Datei:** `KSK_Layout/dataloader/loader.py`, Zeile 1041
**Beobachtung:** implizite Vollzeitannahme.
**Risiko:** Kann IST-MAK künstlich erhöhen.

### 5.3 Niedrige Befunde / Technische Schulden

#### [N1] Doppelter BsGrd-Assign in Seite 3
**Datei:** `KSK_Layout/pages/3_📉_Prognose_Abgänge.py`, Zeilen 160 und 162
**Beobachtung:** `df_employee_agg["BsGrd"] = ...` doppelt gesetzt.
**Auswirkung:** Keine funktionale, aber unnötig.

#### [N2] Kommentare teils historisch/inkonsistent
**Datei:** mehrere (z. B. `loader.py` Docstrings vs. aktuelle Status-Definition)
**Auswirkung:** Verständnisrisiko, kein direkter Rechenfehler.

### 5.4 Implizite Annahmen (nicht dokumentiert, aber wirksam)
- `Is_Vacant` fungiert als harte Trennlinie für IST-seitige KPIs (`kpi_engine.py:51`, `loader.py:497-510`).
- Azubi-Kapazität zählt erst bei Übernahme (`loader.py:377-423`, `zugaenge/forecast.py:589, 622, 437`).
- Forecast-Seiten 3/4 normalisieren bewusst die Engine über `Sollarbeitszeit=39` + `BsGrd=MAK*100` (Backcalculation) (`pages/3...:141-147`, `pages/4...:101-104`).

---

## 6. Datenqualitäts-Beobachtungen

- Harte Artefakt-Korrektur für `Soll_FTE==0.01` existiert (gut), aber nur punktgenau.
- Fehlende `BsGrd` führt je nach Pfad zu 1.0 oder 0.0 MAK (Inkonsistenz).
- Mehrfachplanstellen sind explizit möglich; Summierung ist für Soll implementiert, für MAK in `get_unique_employees` aber nicht.
- Exklusionslogik nullt auch `_raw`-Felder (`loader.py:488`), was spätere forensische Nachverfolgung reduziert.

---

## 7. Externe Perspektive: Konzeptionelle Einschätzung

### 7.1 Stärken der aktuellen Implementierung
- Klare Trennung IST/SOLL bei Exklusionen (fachlich sauber).
- Statusbasierte Ruhend-Definition konsistent über Loader und Exclusion-Deep-Dive.
- Forecast-Events modellieren MAK-Deltas explizit und robust (inkl. Non-negative Clamp in Abgänge).
- Azubi-Logik fachlich nachvollziehbar mit explizitem Kapazitäts-Timing.

### 7.2 Schwächen und Risiken
- Größtes Risiko: mögliche IST-MAK-Unterzählung bei Mehrfachplanstellen durch Deduplikationslogik in KPI-Engine.
- Uneinheitliche Fallbacks bei fehlenden Kernspalten (`BsGrd`).
- Referenzpfad (`kpi_reference`) und Runtime-Engine sind nicht vollständig deckungsgleich in allen Guards.

### 7.3 Empfehlungen für Weiterentwicklung
1. **Priorität 1:** `get_unique_employees` um MAK-Aggregation je PersNr erweitern (oder `compute_fte_effektiv` separat korrekt aggregieren).
2. **Priorität 1:** Fallback-Verhalten bei fehlender `BsGrd` vereinheitlichen.
3. **Priorität 2:** Toleranzbasierte Normalisierung des `0.01`-Artefakts.
4. **Priorität 2:** Explizite Tests für Mehrfachplanstellen (2x50%=1.0) in KPI-Engine ergänzen.
5. **Priorität 3:** Kommentarkonsolidierung und zentrale MAK-Spezifikation (eine Source-of-Truth-Doku).

---

## 8. Glossar

| Begriff | Definition im Kontext dieses Systems |
|---------|---------------------------------------|
| MAK | Effektive Mitarbeiterkapazität (FTE-äquivalent), IST-seitig |
| IST-MAK | Summe effektiver Kapazität aktiver/besetzter Mitarbeitender |
| SOLL-MAK | Planbedarf aus `Soll_FTE` |
| FTE | Vollzeitäquivalent |
| BsGrd | Beschäftigungsgrad in Prozent |
| Planstelle | Eine Zeile im Snapshot (positionsbasiert) |
| Is_Vacant | Kennzeichnet nicht besetzte/exkludierte Stelle |
| ATZ-FR | Altersteilzeit-Freistellungsphase (MAK=0, HC kann >0 sein) |
| Ruhendes BV | Status „Ruhendes Beschäftigungsverhältnis“, MAK=0 |

---

## Schrittweise Rekonstruktion (1–10) — Kurzantworten

1. **Formel:** `BsGrd/100`, mit Nullung über Vacant/Ruhend/ATZ-FR.
2. **Zwei Implementierungen:** weitgehend gleich, aber Abweichung bei fehlender `BsGrd`.
3. **Azubi:** robust identifiziert und dreifach genullt; Übernahme macht MAK wirksam.
4. **Soll_FTE 0.01:** Normalisierung vorhanden; exakte Gleichheit bleibt fragil.
5. **Exklusion:** direkte Nullung IST + `Is_Vacant=True`; Soll bleibt bewusst erhalten.
6. **Aggregation:** Mischbetrieb; 3/4 explizit korrekt aggregiert, KPI-Engine dedup kann MAK unterzählen.
7. **Forecast:** Start-MAK aus Snapshot; Events ändern MAK explizit; negative MAK wird verhindert.
8. **Spaltenwildwuchs:** zentrale Priorität inzwischen weitgehend harmonisiert.
9. **kpi_reference:** Referenzzielwerte vorhanden (`fte_effektiv=993.23`), aber Guard-Details unterscheiden sich.
10. **Kritische Sicht:** größtes fachliches Risiko ist Aggregation von MAK bei Mehrfachplanstellen.
