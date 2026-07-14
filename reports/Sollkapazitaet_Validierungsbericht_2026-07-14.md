# Sollkapazität – Validierungsbericht: Excel vs. Dashboard

**Datum:** 2026-07-14
**Ausgangswert (Dashboard, vor Fix):** 759,9 FTE (Gesamtbelegschaft, Seite "Kompakt")
**Live nachgerechneter Wert (vor Fix):** 759,0 FTE
**Abweichung (vor Fix):** 0,9 FTE (0,11 %) — plausibel durch Datenstand-Drift erklärbar, siehe Abschnitt 5

Methodik: Der komplette Berechnungspfad wurde nicht nur gelesen, sondern **live gegen den produktiven Code ausgeführt** (`dataloader/loader.py`, `components/sidebar.py`, `pages/1_⚡_Kompakt.py`) — mit den echten Original-Daten (`Original-Daten/Planstellen.XLSX`, `Mitarbeiter.xlsx`, `ATZ.xlsx`, `Ausbildung.xlsx`) und den aktuell persistierten Einstellungen (`config/user_settings.json`). Alle Zwischenwerte in diesem Bericht sind reproduzierbare Rechenergebnisse, keine Schätzungen.

> **Update 2026-07-14 (nach Fix):** Der in Abschnitt 4 beschriebene Geschlechts-Filter-Fehler wurde in `components/sidebar.py::_apply_filters_uncached()` behoben (unbesetzte Planstellen ohne Geschlecht werden nicht mehr herausgefiltert) und auf `origin`/`ksk_layout` gepusht (Commit `0024efb`). **Abschnitt 9 unten** wiederholt Ebene 1–3 komplett mit dem korrigierten Code und dokumentiert die neuen Werte. Die Abschnitte 1–8 bleiben als Diagnose-Dokumentation des ursprünglichen Zustands (vor Fix) erhalten.

---

## 1. Wie das Dashboard die Sollkapazität berechnet (Code-Analyse)

### 1.1 Datenherkunft
`app.py` selbst lädt keine Daten — es ist nur der Navigations-Einstiegspunkt. Die eigentliche Ladefunktion ist `dataloader/loader.py::load_and_prepare_data()`, aufgerufen von `pages/1_⚡_Kompakt.py::main()` (Zeile 7257). Für "Original-Daten" (kein Excel-Upload) liest sie vier Dateien:

| Datei | Rolle |
|---|---|
| `Original-Daten/Planstellen.XLSX` | **Quelle der Sollkapazität** — eine Zeile je Planstelle, Spalte `Sollarbeitszeit` (Wochenstunden) |
| `Original-Daten/Mitarbeiter.xlsx` | Personendaten (Geschlecht, Status, Ein-/Austritt …), wird per `Personalnummer` an Planstellen gejoint |
| `Original-Daten/ATZ.xlsx` | Altersteilzeit-Phasen |
| `Original-Daten/Ausbildung.xlsx` | Ausbildungs-/Bildungsdaten |

### 1.2 Transformationskette bis zur Kennzahl "Soll_FTE"
Reihenfolge, wie sie `load_and_prepare_data()` tatsächlich ausführt:

1. **`clean_planstellen()`** (`loader.py:1074`)
   - Entfernt die Summenzeile der Excel-Datei (letzte Zeile, `Kürzel OrgEinheit` = leer, `Sollarbeitszeit` = 32.731,08 = Kontrollsumme).
   - **Azubi-Korrektur:** Alle Zeilen mit `Kürzel OrgEinheit == "9910"` **und** `Sollarbeitszeit == 0.01` werden auf `39.0` gesetzt. Grund: Das Quellsystem kann für Ausbildungs-Planstellen keine reale Sollarbeitszeit hinterlegen und liefert stattdessen einen technischen Platzhalter (0,01 Std.) statt einer vollen Stelle (39 Std.).
2. **Merge** Planstellen → Mitarbeiter (`left`, auf `Personalnummer`/`PersNr`) → ATZ → Ausbildung (`combine_to_snapshot()`, `loader.py:1348`).
3. **`Soll_FTE = Sollarbeitszeit.fillna(0) / 39.0`** (`loader.py:1430`) — die zentrale Formel.
4. **0,01-Artefakt-Nullung:** `Soll_FTE > 0 und < 0,015` → `0.0` (`loader.py:1452`). Fängt alle 0,01-Platzhalter außerhalb OE 9910 ab (die durch Schritt 1 nicht korrigiert wurden), da `0,01 / 39 ≈ 0,0003 FTE`.
5. **`enrich_snapshot_data`, `_apply_jobfamilies`, `apply_clusters_to_snapshot_from_source`, `_zero_out_azubi_mak`** — betreffen IST-Kennzahlen (MAK) und Metadaten, **fassen `Soll_FTE`/`Sollarbeitszeit` nicht an**.
6. **`apply_exclusions(df, exclusions)`** (`loader.py:461`) — markiert exkludierte Zeilen als `Is_Excluded=True` und `Is_Vacant=True`, nullt IST-Felder (`MAK`, `BsGrd`, …). **Wichtig:** Laut explizitem Code-Kommentar (`loader.py:593`) bleiben `Soll_FTE`/`Sollarbeitszeit` dabei unverändert — der Rohwert der Spalte `Soll_FTE` ist exklusionsunabhängig.
7. **`apply_person_mak_allocation`** — erzeugt zusätzliche IST-Reporting-Spalten, ohne `Soll_FTE` zu verändern.

Bis hierhin (`snapshot_df`) ist **`Soll_FTE.sum()` eine reine Funktion von `Planstellen.XLSX`**, unabhängig von Exklusions-Einstellungen.

### 1.3 Wo die Exklusion doch noch wirkt: die "View"-Spalte
Die eigentliche KPI-Karte "Sollkapazität" auf der Kompakt-Seite verwendet **nicht** die rohe `Soll_FTE`-Summe, sondern `build_compact_compensation_planlevel_df()` (`pages/1_⚡_Kompakt.py:1770ff`), die daraus **`SOLL_MAK_View`** ableitet:

```python
out["SOLL_MAK"] = Soll_FTE                     # Zeile 1830
# Planstellennr-Duplikate: nur die erste Zeile je Planstellennr zählt für SOLL
out.loc[Is_Duplicate_Planstelle, "SOLL_MAK"] = 0.0     # Zeile 1886
# Exklusions-bereinigte "Reporting"-Sicht:
out["SOLL_MAK_View"] = out["SOLL_MAK"].where(~Is_Excluded, 0.0)   # Zeile 1912
```

D. h.: Der rohe `Soll_FTE`-Wert bleibt im Datensatz erhalten (Transparenz-Prinzip), aber die **angezeigte** KPI-Summe nullt Soll für exkludierte Zeilen doch — nur eben erst auf Anzeige-Ebene, nicht im Snapshot selbst.

### 1.4 Welche Exklusionen sind aktiv? (`config/user_settings.json`, persistiert)
```json
"exclusions": {
  "vorstand": true,
  "ruhend_bv": true,
  "planstellen_follow_person": true,
  "org_units": ["9900","9910","9920","9921","9940","9941","9945","9960",
                "9970","9971","9972","9973","9975","9980","9981","9990"],
  "special_groups": ["ausbildung_nachwuchs",
                      "jobfamily_validation_special_positions",
                      "sollarbeitszeit_001_positions"]
}
```
Das ist **nicht** der Code-Default (`vorstand: false` in `DEFAULT_EXCLUSIONS`) — jemand hat den Vorstands-Ausschluss in den Einstellungen aktiv gesetzt. Zusätzlich: `stichtag: 2026-05-20`, `include_future_hires: true`.

### 1.5 Filter: Sidebar-Defaults
`components/sidebar.py::render_global_filters()` setzt beim ersten Laden neutrale Defaults (leere Multiselects = "alle"):
`selected_org_units=[]`, `selected_jobfamilies=[]`, `selected_cohorts=[]`, `selected_education=[]`, `selected_oe_clusters=[]`, `selected_jf_clusters=[]`, `selected_employment=["Vollzeit","Teilzeit","Inaktiv"]` (alle 3), `selected_atz_status=["Kein ATZ","Arbeitsphase","Freistellungsphase"]` (alle 3).

**Eine Ausnahme ist nicht neutral:** `selected_genders` ist im UI ausgeblendet, wird aber hart auf `["m", "w"]` gesetzt (`sidebar.py:546`) und über `apply_filters()` **immer** angewendet:
```python
mask = df["Geschlecht"].isin(["m", "w"])   # sinngemäß, sidebar.py:815-820
```
**Befund:** Für unbesetzte Planstellen ist `Geschlecht` `NaN` (keine Person vorhanden → kein Geschlecht). `NaN.isin(["m","w"])` ist `False` → **jede aktuell unbesetzte Planstelle fällt aus dem gefilterten DataFrame heraus, bevor die Sollkapazität summiert wird** — unabhängig davon, ob sie durch die Einstellungen (Abschnitt 1.4) exkludiert ist oder eine ganz normale offene Stelle ist. Details und Größenordnung in Abschnitt 4.

---

## 2. Ebene 1 — Gesamtbelegschaft, Herleitung ausschließlich aus der Excel-Datei

**Datensatz:** `Original-Daten/Planstellen.XLSX`, 1.729 Zeilen (1.728 Planstellen + 1 Summenzeile).

| Schritt | Regel | Zeilen | Summe Sollarbeitszeit (Std.) | Soll_FTE (Std./39) |
|---|---|---|---|---|
| 0 | Rohdaten (inkl. Summenzeile) | 1.729 | — | — |
| 1 | Summenzeile entfernen (`Kürzel OrgEinheit` leer) | 1.728 | 32.731,08 (= Kontrollsumme der Excel-Summenzeile ✓) | 839,26 |
| 2 | Azubi-Korrektur OE 9910, `Sollarbeitszeit==0,01` → `39,0` (208 betroffene Zeilen) | 1.728 | 40.841,00 | 1.047,21 |
| 3 | 0,01-Artefakt-Nullung (`Soll_FTE<0,015`, 530 Zeilen außerhalb OE 9910) | 1.728 | — | **1.047,06** |

→ **Ebene-1-Ergebnis (reine Planstellen-Summe, keine Exklusionen):** **1.047,06 FTE**

Das ist die Kapazität, die **ohne jede Steuerungs-Exklusion** (Vorstand, Ruhendes BV, Sonderstatus-Bereiche) aus der Planstellen-Excel folgt — eine Zahl, die im Dashboard so an keiner Stelle direkt angezeigt wird (siehe Ebene 2).

**Wichtiger Hinweis zur Excel-Struktur:** Ohne Kenntnis der Azubi-Korrekturregel (Schritt 2) würde eine naive Nachrechnung "Sollarbeitszeit-Summe / 39" nur 839,26 FTE ergeben — 207,8 FTE zu wenig, weil 208 Ausbildungsplanstellen in der Excel-Datei technisch mit 0,01 Std. statt 39 Std. geführt werden.

---

## 3. Ebene 2 — Mit den im Dashboard standardmäßig gesetzten Filterkriterien

**Zentraler Befund:** Es gibt im Dashboard **keinen separaten "ungefilterten" Anzeigezustand**. Die KPI-Karte "Sollkapazität" durchläuft beim ersten Laden immer denselben Pfad: Snapshot → Exklusions-Flag → Sidebar-Filter (Default) → `SOLL_MAK_View`-Summe. **Ebene 1 (wie im Dashboard sichtbar) und Ebene 2 sind daher rechnerisch identisch** — der Wert 759,9 FTE, den Sie auf der Startseite sehen, ist bereits das Ergebnis von Ebene 2, nicht eine ungefilterte Rohzahl.

### 3.1 Schritt A — Exklusions-Einstellungen anwenden (auf alle 1.728 Zeilen, noch kein Sidebar-Filter)

| Gruppe | Kriterium | betroffene Zeilen | darin enthaltenes Soll_FTE |
|---|---|---:|---:|
| Vorstand | `MitarbGruppenbez. == "Vorstand"` | 3 | 3,00 |
| Ruhendes BV | `Status kundenindividuell == "Ruhendes Beschäftigungsverhältnis"` | 54 | 0,00 |
| 16 PA-Bereiche (9900…9990) | `Kürzel OrgEinheit` in Liste | 391 | 208,00 |
| Ausbildung/Nachwuchs | Sondermaske (Azubi-Status, TrfGr `TVA`, Ausbildungs-OE-Namen) | 209 | 208,00 |
| Jobfamily-Validierung (Sonderplanstellen) | 82 fest definierte `Planstellennr` | 82 | 0,00 |
| Sollarbeitszeit = 0,01 (technisch) | `Sollarbeitszeit == 0,01` | 529 | 0,00 |
| **Planstellennr-Duplikate** (Dedup, unabhängig von Exklusion) | nur 1. Zeile je `Planstellennr` zählt | 56 | 3,64 |

(Gruppen überschneiden sich — z. B. sind die 208 Azubi-Planstellen sowohl in "PA-Bereiche" als auch in "Ausbildung/Nachwuchs" enthalten; die Summen sind daher nicht additiv.)

Ergebnis nach Duplikat-Bereinigung und Exklusions-Nullung (`SOLL_MAK_View`, aber noch **ohne** Sidebar-Filter):

**1.047,06 → 1.043,42 (Dedup) → 832,42 FTE (nach Exklusion)**

### 3.2 Schritt B — Sidebar-Default-Filter anwenden
Alle Multiselect-Filter sind leer/neutral **außer** dem unsichtbaren Geschlechts-Filter `["m","w"]`. Dieser entfernt **481 Zeilen** (alle aktuell unbesetzten Planstellen, da `Geschlecht=NaN`) aus dem für die KPI verwendeten DataFrame — **bevor** `SOLL_MAK_View` summiert wird.

| | Zeilen | Soll_FTE (roh) |
|---|---:|---:|
| Verloren durch Geschlechts-Filter | 481 | 151,37 |
| … davon bereits durch Exklusion auf 0 gesetzt (kein zusätzlicher Effekt) | 393 | (in 832,42 bereits 0) |
| … davon **echte offene, nicht exkludierte Planstellen** (zusätzlicher Verlust) | **88** | **73,37** |

**832,42 − 73,37 = 759,05 FTE**

### 3.3 Ebene-2-Ergebnis
**759,05 FTE** (live nachgerechnet) vs. **759,9 FTE** (vom Nutzer beobachteter Dashboard-Wert) → Differenz **0,85 FTE**, siehe Abschnitt 5 für mögliche Ursachen.

---

## 4. Kritischer Befund: Offene Planstellen verschwinden unbeabsichtigt aus der Sollkapazität

Der ausgeblendete Geschlechts-Filter (`sidebar.py:546`, `st.session_state["selected_genders"] = ["m", "w"]`, immer aktiv) wurde ersichtlich für IST-Auswertungen konzipiert (Geschlechterverteilung der Belegschaft), wirkt aber **ungewollt auch auf die Sollkapazität**, weil `apply_filters()` global auf den gesamten DataFrame angewendet wird, bevor SOLL- und IST-Kennzahlen getrennt berechnet werden.

**Konkrete Auswirkung:** 88 aktuell unbesetzte, aber **nicht** exkludierte Planstellen mit zusammen **73,37 FTE** realer Personalbedarf fehlen in der angezeigten Sollkapazität — nicht weil sie fachlich ausgeschlossen wurden, sondern weil ihnen mangels Stelleninhaber kein Geschlecht zugeordnet werden kann. Beispiel `GB Immobilien` in Abschnitt 5.1 zeigt das exemplarisch: von 6,61 FTE Soll bleiben in der Dashboard-Ansicht nur 1,5 FTE sichtbar.

**Empfehlung:** `Soll_FTE`/`SOLL_MAK_View` sollte entweder vor dem Geschlechts-Filter berechnet werden (Soll ist personenunabhängig), oder der Geschlechts-Filter sollte `NaN`-Werte (= unbesetzt) grundsätzlich durchlassen, analog zur bereits vorhandenen Logik bei `Arbeitszeit`/`ATZ_Status`, wo unbesetzte Zeilen laut Code (keine Nullwerte in diesen Spalten beobachtet) offenbar korrekt einen Default erhalten.

---

## 5. Ebene 3 — Drei Organisationseinheiten im Detail

Ausgewählt, um drei unterschiedliche Mechanismen zu zeigen: (a) sauberer 1:1-Fall, (b) vollständige Exklusion inkl. Azubi-Korrektur, (c) der in Abschnitt 4 beschriebene Filter-Effekt.

### 5.1 GB Immobilien (Kürzel 220) — zeigt den Geschlechts-Filter-Effekt

| Planstellennr | Planstelle | Sollarbeitszeit | Soll_FTE | Besetzt? | Exkludiert? | Geschlecht |
|---|---|---:|---:|---|---|---|
| 50030071 | Geschäftsbereichsleiter/in Immobilien | 39,00 | 1,00 | ja | nein | m |
| 50048168 | Assistent/in | 19,50 | 0,50 | ja | nein | w |
| 50064566 | Direktionsassistent/in | 19,50 | 0,50 | **nein** | nein | — |
| 50064567 | Direktionsassistent/in | 0,20 | 0,01 | **nein** | nein | — |
| 50064568 | Direktionsassistent/in | 39,00 | 1,00 | **nein** | nein | — |
| 50064569 | Direktionsassistent/in | 31,59 | 0,81 | **nein** | nein | — |
| 50064570 | Direktionsassistent/in | 31,20 | 0,80 | **nein** | nein | — |
| 50064571 | Direktionsassistent/in | 39,00 | 1,00 | **nein** | nein | — |
| 50064572 | Direktionsassistent/in | 39,00 | 1,00 | **nein** | nein | — |

- **Ebene 1 (Excel, nur Sollarbeitszeit/39):** 257,99 Std. / 39 = **6,61 FTE**
- **Ebene 2, Schritt A (nach Exklusion, vor Sidebar-Filter):** keine dieser 9 Zeilen ist exkludiert → weiterhin **6,61 FTE**
- **Ebene 2, Schritt B (nach Default-Sidebar-Filter):** 7 der 9 Zeilen sind unbesetzt → `Geschlecht=NaN` → fallen raus → nur die 2 besetzten Zeilen bleiben → **1,50 FTE**
- **Dashboard-Anzeige für diese OE:** 1,50 FTE
- **Abweichung zur "echten" Soll-Nachfrage laut Excel:** 5,11 FTE (77 %) — vollständig durch den in Abschnitt 4 beschriebenen Filter-Effekt erklärt, keine Rundung, keine Exklusion.

### 5.2 PA Auszubildende (Kürzel 9910) — zeigt Azubi-Korrektur + vollständige Exklusion

36 Planstellen, alle mit `Sollarbeitszeit = 0,01` in der Rohdatei, alle unbesetzt, alle vom Typ "PA Azubi …" / "PA Einarbeitung …".

- **Ebene 1, naiv (Sollarbeitszeit/39 ohne Azubi-Korrektur):** 0,36 Std. / 39 = **0,0092 FTE** — fachlich falsch, da die Korrekturregel (Abschnitt 1.2, Schritt 2) genau für OE 9910 + 0,01 greift.
- **Ebene 1, korrekt (mit Azubi-Korrektur):** 36 × 39,0 Std. / 39 = **36,00 FTE**
- **Ebene 2, Schritt A:** OE `9910` ist Teil der 16 exkludierten PA-Bereiche **und** erfüllt die Sondermaske "Ausbildung/Nachwuchs" → alle 36 Zeilen `Is_Excluded=True` → **0,00 FTE** in `SOLL_MAK_View`
- **Ebene 2, Schritt B:** bleibt 0,00 FTE (Filter kann nur weiter reduzieren)
- **Dashboard-Anzeige für diese OE:** 0,00 FTE
- **Bewertung:** Die Exklusion wirkt hier genau wie in den Einstellungen konfiguriert (Azubis sind bewusst aus der Sollkapazität-Steuerungssicht ausgeschlossen). Kein unerwarteter Effekt — im Gegensatz zu 5.1 ist dies eine **beabsichtigte** Null.

### 5.3 Beratungs-Center Maichingen (Kürzel 711) — sauberer Referenzfall ohne Abweichung

| Planstellennr | Planstelle | Sollarbeitszeit | Soll_FTE | Besetzt? |
|---|---|---:|---:|---|
| 50001494 | Leiter/in Beratungs-Center | 19,11 | 0,49 | ja |
| 50001496 | Individualkundenberater/in | 39,00 | 1,00 | ja |
| 50001495 | Individualkundenberater/in | 39,00 | 1,00 | ja |
| 50048967 | Individualkundenberater/in | 39,00 | 1,00 | ja |
| 50010360 | Serviceberater/in | 27,30 | 0,70 | ja |
| 50001499 | Serviceberater/in | 11,70 | 0,30 | ja |
| 50012295 | Serviceberater/in | 39,00 | 1,00 | ja |

- **Ebene 1 (Excel):** 214,11 Std. / 39 = **5,49 FTE**
- **Ebene 2, Schritt A (Exklusion):** keine Zeile betroffen → 5,49 FTE
- **Ebene 2, Schritt B (Sidebar-Filter):** alle 7 Zeilen besetzt, alle mit `Geschlecht ∈ {m,w}` → keine Zeile fällt raus → **5,49 FTE**
- **Dashboard-Anzeige für diese OE:** 5,49 FTE
- **Abweichung:** keine (0,00 FTE) — Excel und Dashboard stimmen exakt überein, weil hier weder Exklusion noch der Geschlechts-Filter greifen (voll besetzte, nicht-exkludierte Einheit).

---

## 6. Zusammenfassung: Excel vs. Dashboard je Ebene

| Ebene | Beschreibung | Excel-Herleitung | Dashboard (live nachgerechnet) | Nutzer-Beobachtung | Differenz | Ursache |
|---|---|---:|---:|---:|---:|---|
| 1/2 | Gesamtbelegschaft = Default-Filter (im Dashboard identisch, siehe Abschnitt 3) | 1.047,06 (ohne Exklusion) | **759,05** | 759,9 | 0,85 | Exklusionen (Abschnitt 3.1) + Geschlechts-Filter-Bug (Abschnitt 4); Rest-Differenz = Datenstand-Drift (Abschnitt 7) |
| 3.1 | GB Immobilien | 6,61 | 1,50 | — | −5,11 | Geschlechts-Filter entfernt 7 unbesetzte, nicht exkludierte Zeilen |
| 3.2 | PA Auszubildende | 36,00 (mit Azubi-Korrektur) | 0,00 | — | −36,00 | Vollständig exkludiert (gewollt) |
| 3.3 | Beratungs-Center Maichingen | 5,49 | 5,49 | — | 0,00 | Kein Filter-/Exklusionseffekt — exakte Übereinstimmung |

---

## 7. Mögliche Ursachen für die Rest-Abweichung 759,05 vs. 759,9 FTE

Die hier hergeleitete Zahl (759,05 FTE) wurde **gerade eben** live gegen die aktuell im Verzeichnis liegenden Original-Daten und die aktuell gespeicherten Einstellungen gerechnet. Der vom Nutzer beobachtete Wert (759,9 FTE) stammt vermutlich aus einer früheren Dashboard-Sitzung. Mögliche, jeweils sehr kleine (<1 FTE) Ursachen für die Differenz von 0,85 FTE:

1. **Datenstand-Drift:** Der fest hinterlegte Stichtag ist `2026-05-20`; das heutige Datum ist `2026-07-14`, also fast zwei Monate später. `Mitarbeiter.xlsx`/`Planstellen.XLSX` sind externe Dateien, die in dieser Zeit aktualisiert worden sein können (z. B. eine neue "Ruhendes BV"-Statusänderung, ein Vorstands-Wechsel, eine neue/geänderte Planstelle) — jede einzelne Änderung verschiebt die Summe um genau den Betrag der betroffenen Planstelle(n).
2. **Cache-Stand:** Streamlit cached `load_and_prepare_data()` über `@st.cache_data` anhand von Datei-Signaturen (Zeitstempel/Größe). Wenn der Nutzer den 759,9-Wert vor einer Datenänderung gesehen und die App seither nicht neu geladen hat, zeigt die UI ggf. noch den alten Cache-Stand.
3. **Rundungsanzeige:** Die KPI-Karte rundet für die Anzeige (z. B. auf eine Nachkommastelle); das beeinflusst aber nur die letzte Nachkommastelle, nicht eine Differenz von 0,85.
4. **Nicht in Frage kommt:** ein Logikfehler in der hier nachvollzogenen Kette — der komplette Code-Pfad wurde nicht nur gelesen, sondern mit den echten Originaldaten ausgeführt (nicht simuliert), und die Zwischensummen (1.047,06 → 1.043,42 → 832,42 → 759,05) sind intern konsistent nachgerechnet (z. B. 832,42 − 73,37 = 759,05 exakt).

**Empfehlung zur endgültigen Klärung:** Dashboard im Browser neu laden (Cache invalidieren) und den aktuell angezeigten Wert mit den Original-Daten-Dateiständen (Änderungsdatum) vergleichen, die zum Zeitpunkt der 759,9-Beobachtung vorlagen.

---

## 8. Referenzierte Code-Stellen

- `KSK_Layout/dataloader/loader.py`: `clean_planstellen` (1074), `combine_to_snapshot` (1348, insb. 1427–1453), `apply_exclusions` (461–595), `load_and_prepare_data` (785)
- `KSK_Layout/pages/1_⚡_Kompakt.py`: `build_compact_compensation_planlevel_df` (1770ff, insb. 1828–1917 für SOLL_MAK/SOLL_MAK_View), `get_soll_mak` (663), `main()` (7192)
- `KSK_Layout/components/sidebar.py`: `render_global_filters` (441, insb. 545f. Geschlechts-Zwangswert), `_apply_filters_uncached`/`apply_filters` (796–875)
- `KSK_Layout/utils/exclusion_groups.py`: `build_group_masks` (123–197), Gruppendefinitionen `PA_GROUPS`/`SPECIAL_GROUPS`
- `KSK_Layout/utils/settings_loader.py`: `DEFAULT_EXCLUSIONS` (21), `get_setting`/`load_user_settings`
- `KSK_Layout/config/user_settings.json`: aktuell persistierte Exklusions-/Stichtag-Einstellungen
- `KSK_Layout/dataloader/mak_allocation.py`: `apply_person_mak_allocation` (103), `_person_mak` (40) — Personen-Korrektur bei Mehrfachplanstellen (siehe Abschnitt 10.3)
- `KSK_Layout/reports/MAK-Berechnungs-Dossier_2026-03-11.md`: früherer Befund [K1] zur Mehrfachplanstellen-Unterzählung, referenziert in Abschnitt 10.3/11.3

---

## 9. Wiederholung der Analyse nach dem Fix (Commit `0024efb`)

Der in Abschnitt 4 beschriebene Fehler ist behoben: `_apply_filters_uncached()` (`components/sidebar.py:815–826`) lässt Zeilen mit `Geschlecht = NaN` (unbesetzte Planstellen) jetzt unabhängig von der Geschlechts-Auswahl immer durch. Aktive Personen-Filter (z. B. gezielt nur "männlich") filtern weiterhin korrekt echte Mitarbeitende — geprüft per Regressionstest (948 verbleibende Zeilen bei `selected_genders=["m"]`, davon 467 besetzt mit `m`, 0 mit `w`, 481 unbesetzt und unverändert enthalten).

Die komplette Kette wurde erneut **live gegen den jetzt korrigierten Code** ausgeführt (identisches Vorgehen wie Abschnitt 2–5, keine Schätzung).

### 9.1 Ebene 1/2 — Gesamtbelegschaft mit Default-Filtern

| Schritt | Soll_FTE |
|---|---:|
| Ebene 1: Planstellen.XLSX, bereinigt (Summenzeile weg, Azubi-Korrektur, 0,01-Artefakt genullt) | 1.047,06 |
| Planstellennr-Duplikate bereinigt (Dedup) | 1.043,42 |
| Exklusionen angewendet (Vorstand, Ruhend BV, 16 PA-Bereiche, Sonderplanstellen) | 832,42 |
| Default-Sidebar-Filter (Geschlecht/Arbeitszeit/ATZ/…) | **832,42** (unverändert — der Fix entfernt genau den Verlust, der hier vorher entstand) |

→ **Neue Ebene-1/2-Sollkapazität: 832,4 FTE** (vorher 759,0/759,9 FTE). Die Differenz von **+73,4 FTE** entspricht exakt den zuvor identifizierten 88 offenen, nicht exkludierten Planstellen (Abschnitt 4) — kein weiterer, unerklärter Rest.

### 9.2 Ebene 3 — dieselben drei Organisationseinheiten, erneut hergeleitet

| OE | Excel-Herleitung (Ebene 1) | Dashboard **vor** Fix | Dashboard **nach** Fix | Kommentar |
|---|---:|---:|---:|---|
| GB Immobilien | 6,61 | 1,50 | **6,61** | Alle 9 Planstellen (2 besetzt + 7 offen) jetzt vollständig enthalten — exakte Übereinstimmung mit Excel |
| PA Auszubildende | 36,00 (mit Azubi-Korrektur) | 0,00 | **0,00** | Unverändert — weiterhin korrekt und beabsichtigt vollständig exkludiert (Vorstand-/PA-Bereichs-Exklusion, nicht der Filter-Bug) |
| Beratungs-Center Maichingen | 5,49 | 5,49 | **5,49** | Unverändert — war nie betroffen (voll besetzt) |

**GB Immobilien** ist der Beleg für die Wirksamkeit des Fixes: Vorher zeigte das Dashboard nur 1,50 von 6,61 FTE echtem Soll-Bedarf dieser OE (77 % fehlten), jetzt stimmen Excel-Herleitung und Dashboard-Anzeige exakt überein.

### 9.3 Fazit
Mit dem Fix bildet das Dashboard die Sollkapazität **korrekt gemäß der konfigurierten Exklusionslogik** ab: Nur Positionen, die über `config/user_settings.json` bewusst exkludiert wurden (Vorstand, Ruhendes BV, Sonderstatus-/PA-Bereiche, technische 0,01-Artefakte), fehlen in der Summe — reguläre offene Planstellen ohne Stelleninhaber zählen wieder vollständig zur Sollkapazität, wie es fachlich auch für eine "Soll"-Kennzahl (personenunabhängiger Planbedarf) erwartet wird.

**Neuer Referenzwert für die Gesamtbelegschaft: 832,4 FTE** (statt zuvor 759,9 FTE). Dieser Wert sollte anstelle von 759,9 FTE für Berichte/Meetings verwendet werden, da er den tatsächlich konfigurierten Exklusionsstand korrekt und vollständig abbildet.

> **Beobachtung am Rande:** Der ursprünglich beobachtete Wert 759,9 FTE liegt zufällig sehr nah an der **IST-MAK** der Gesamtbelegschaft auf Snapshot-Ebene (759,91 FTE, siehe Abschnitt 11). Das ist nach Prüfung beider Berechnungspfade eine **Koinzidenz für diesen Datenstand**, keine Verwechslung: Die SOLL-KPI-Karte auf der Kompakt-Seite liest nachweislich `SOLL_MAK_View` (Code-Zeile 1912), nicht `MAK_Reporting`/`IST_MAK`. Dennoch: falls beim nächsten Datenstand SOLL und IST wieder zufällig nahe beieinanderliegen, lohnt sich ein Blick auf das Karten-Label, um Verwechslungen auszuschließen.

---

## 10. Wie das Dashboard die IST-Arbeitszeit (IST-MAK) berechnet (Code-Analyse)

Analog zu Abschnitt 1 (SOLL), diesmal für die tatsächlich geleistete/vertraglich vereinbarte Arbeitszeit der Belegschaft ("IST"). Die IST-Seite ist deutlich komplexer als die SOLL-Seite, weil sie — anders als SOLL — **personenbezogen** ist: Eine Person hat eine reale Kapazität (ihren Beschäftigungsgrad), die aber auf **mehrere** Planstellen-Zeilen verteilt sein kann, wenn die Person mehrere Stellen gleichzeitig innehat (z. B. Leitung zweier kleiner Beratungs-Center). Naives zeilenweises Aufsummieren würde solche Personen doppelt zählen.

### 10.1 Datenherkunft
Die IST-Arbeitszeit stammt aus `Original-Daten/Mitarbeiter.xlsx`, Spalte **`BsGrd`** (Beschäftigungsgrad in Prozent, z. B. 100 = Vollzeit, 50 = halbe Stelle). Diese Spalte wird beim Zusammenbau des Snapshots (`combine_to_snapshot`) auf jede Planstellen-Zeile der Person gemappt.

### 10.2 Stufe 1 — technische (zeilenbasierte) IST-MAK
`calculate_mak_vectorized()` (`loader.py:1246`) berechnet für **jede Zeile** einzeln:

```python
MAK_Calculated = BsGrd.fillna(0) / 100.0
# 0 setzen bei:
#   Is_Vacant == True                                  (unbesetzt)
#   Status kundenindividuell == "Ruhendes Beschäftigungsverhältnis"
#   ist_atz_fr == True                                 (Altersteilzeit-Freistellungsphase)
```
Zusätzlich nullt `_zero_out_azubi_mak()` (`loader.py:412`) diese Spalte für alle als Azubi erkannten Zeilen (TrfGr enthält "TVA", oder Jobfamily enthält "Azubi"/"Ausbildung"), und `apply_exclusions()` (`loader.py:461`) nullt sie zusätzlich für alle über die Einstellungen exkludierten Zeilen (Vorstand, Ruhend BV, PA-Bereiche, Sondergruppen — dieselbe Logik wie bei SOLL, Abschnitt 1.4). Anders als bei SOLL wird hier **direkt die Spalte genullt**, nicht erst eine separate "View"-Spalte gebildet — IST-Werte werden also schon auf Snapshot-Ebene bereinigt.

**Diese Stufe entspricht exakt der unabhängig aus Excel nachvollziehbaren Formel:** `BsGrd/100`, mit Nullung für unbesetzt/ruhend/ATZ-FR/Azubi/exkludiert.

### 10.3 Stufe 2 — Personen-Korrektur bei Mehrfachplanstellen (`MAK_Reporting`)
`apply_person_mak_allocation()` (`dataloader/mak_allocation.py:103`) läuft danach und behebt genau das Doppelzähl-Risiko aus Stufe 1:

1. Für jede Person wird ihre **tatsächliche Gesamtkapazität** (`Personen_MAK`) ermittelt — bevorzugt aus einer je-Person eindeutigen `BsGrd`-Quelle (`_build_person_capacity_source`, dedupliziert auf `PersNr` direkt aus `Mitarbeiter.xlsx`, vor dem Join mit Planstellen), mit Fallback auf die Snapshot-`BsGrd`. Auch hier wird für Vakanz/Ruhend/ATZ-FR/Azubi auf 0 gesetzt.
2. Hat eine Person **mehrere** aktive (besetzte, nicht-exkludierte) Planstellen-Zeilen, wird ihre `Personen_MAK` **proportional zum Soll-Gewicht** (`Planstellen_Soll_MAK`, also `Soll_FTE`) jeder Zeile verteilt (`Allocation_Weight = Zeilen-Soll / Summe-Soll-der-Person`). Gibt es kein Soll-Gewicht, wird gleichmäßig aufgeteilt.
3. `MAK_Reporting = Personen_MAK × Allocation_Weight` — Summe über alle Zeilen einer Person ergibt exakt wieder deren `Personen_MAK` (keine Über-, keine Unterzählung).
4. Ein Diagnose-Flag (`MAK_Allocation_Flag`) markiert Sonderfälle, u. a. `exception_required_mak_gt_1`, wenn die Summe der technischen Zeilen-MAK einer Person **größer** ist als ihre tatsächliche Personenkapazität (Hinweis auf prüfbedürftige Stammdaten, z. B. zwei Stellen je 100 % BsGrd bei nur einer realen Vollzeitstelle).

**Wichtig:** Diese Korrektur ist genau der Fix für einen Befund aus einem früheren internen Code-Review (`reports/MAK-Berechnungs-Dossier_2026-03-11.md`, Befund [K1]: "Unterzählung IST-MAK bei Mehrfachplanstellen"). Der damalige Code-Stand hatte dieses Problem noch nicht gelöst; im aktuellen Code ist es durch `apply_person_mak_allocation()` behoben.

### 10.4 Welche Spalte zeigt das Dashboard tatsächlich an?
`pages/1_⚡_Kompakt.py::build_compact_compensation_planlevel_df()` (Zeile 1828):
```python
ist_mak_col = _first_existing_column(df, ["MAK_Reporting", "MAK_Calculated", "MAK", "FTE_assigned"])
out["IST_MAK"] = _numeric_compensation_series(df[ist_mak_col], kind="fte")
```
`MAK_Reporting` hat oberste Priorität und existiert immer (wird in jedem Lade-Durchlauf erzeugt) → **die angezeigte IST-MAK ist immer die personen-korrigierte Stufe-2-Größe**, nicht die naive Zeilensumme.

Zusätzlich gilt — wie bei SOLL — die **Planstellennr-Duplikat-Bereinigung** (Abschnitt 1.3): Zeilen mit doppelt gemeldeter `Planstellennr` werden für IST **und** SOLL auf 0 gesetzt, damit eine fehlerhaft doppelt erfasste Planstelle nicht doppelt in die Summe eingeht.

Im Gegensatz zu SOLL gibt es für IST **keine** zusätzliche "View"-Nullung nach `Is_Excluded` — das ist auch nicht nötig, weil `apply_exclusions()` die IST-Spalten (inkl. `MAK_Reporting`, da nach `apply_exclusions` erzeugt — Reihenfolge: Azubi-Zeroing → Exklusion → Personen-Allokation) bereits direkt auf 0 setzt.

---

## 11. Vollständiger Datenabgleich: Dashboard-SOLL vs. Dashboard-IST vs. unabhängige Berechnung

**Methodik:** Für jedes Szenario wurden drei Zahlen unabhängig ermittelt: (a) der **Dashboard-Wert**, live aus dem produktiven Code gelesen (`SOLL_MAK_View` / `IST_MAK` aus `build_compact_compensation_planlevel_df`, inkl. Default-Sidebar-Filter und Planstellennr-Dedup), (b) eine **unabhängige Berechnung**, komplett neu und ohne Aufruf von Dashboard-Code direkt aus `Planstellen.XLSX`, `Mitarbeiter.xlsx` und `ATZ.xlsx` nachgebaut (Regeln aus Abschnitt 1 und 10 von Hand in einem separaten Skript reimplementiert), jeweils **ohne** die Planstellennr-Dedup-Sonderbereinigung (die nur 56 von 1.728 Zeilen betrifft und in keiner der drei Beispiel-OEs vorkommt).

### 11.1 Gesamtbelegschaft (Default-Filter, nach Fix)

| Kennzahl | Unabhängig (Rohdaten, ohne Dedup) | Dashboard (live, inkl. Dedup) | Differenz | Ursache der Differenz |
|---|---:|---:|---:|---|
| **SOLL** | 836,06 | **832,42** | −3,64 | Planstellennr-Dedup (56 Zeilen im Datensatz doppelt gemeldet) |
| **IST, technisch (Stufe 1, ohne Personen-Korrektur)** | 778,91 | — (wird nicht direkt angezeigt) | — | zur Einordnung: zeigt Ausmaß des Mehrfachplanstellen-Effekts |
| **IST, personen-korrigiert (Stufe 2 = MAK_Reporting)** | 759,91 | **756,22** | −3,69 | Planstellennr-Dedup (deckungsgleich mit SOLL-Differenz) |
| **Differenz SOLL − IST (Unterdeckung)** | 76,15 | **76,20** | — | konsistent |

Beide Differenzen (SOLL: −3,64, IST: −3,69) sind **exakt** durch die 56 dedup-bereinigten Zeilen erklärt — kein unerklärter Rest. Der Effekt der Personen-Korrektur (Stufe 1 → Stufe 2) beträgt unabhängig nachgerechnet **19,00 FTE** (778,91 − 759,91): 19 Mitarbeitende halten mehr als eine Planstelle gleichzeitig; ohne die Korrektur in `apply_person_mak_allocation()` würde deren Kapazität mehrfach gezählt.

### 11.2 Die drei Organisationseinheiten

| OE | SOLL unabhängig | SOLL Dashboard | IST unabhängig (technisch) | IST unabhängig (personen-korr.) | IST Dashboard | Delta (SOLL−IST, Dashboard) |
|---|---:|---:|---:|---:|---:|---:|
| GB Immobilien | 6,61 | **6,61** ✓ | 2,00 | 2,00 | **2,00** ✓ | −4,61 (7 von 9 Planstellen unbesetzt) |
| PA Auszubildende | 0,00 | **0,00** ✓ | 0,00 | 0,00 | **0,00** ✓ | 0,00 (vollständig exkludiert) |
| Beratungs-Center Maichingen | 5,49 | **5,49** ✓ | 6,10 | 5,59 | **5,5925** ✓ | +0,10 (leichte Überdeckung) |

Für alle drei OEs stimmt die unabhängige Berechnung **exakt** mit dem Dashboard überein (✓) — keine der 56 Dedup-Zeilen liegt in diesen OEs, daher entfällt hier auch die kleine Differenz aus 11.1.

**Beratungs-Center Maichingen im Detail (Beleg für die Personen-Korrektur, Abschnitt 10.3):**

| Planstelle | Personalnummer | BsGrd | Soll_FTE (Zeile) | MAK technisch (Stufe 1) | MAK personen-korr. (Stufe 2) |
|---|---|---:|---:|---:|---:|
| Leiter/in Beratungs-Center | 003002 | 100 % | 0,49 | 1,00 | **0,49** |
| Individualkundenberater/in | 005631 | 100 % | 1,00 | 1,00 | 1,00 |
| Individualkundenberater/in | 001821 | 100 % | 1,00 | 1,00 | 1,00 |
| Individualkundenberater/in | 004965 | 100 % | 1,00 | 1,00 | 1,00 |
| Serviceberater/in | 002761 | 64,1 % | 0,70 | 0,641 | 0,641 |
| Serviceberater/in | 001882 | 46,15 % | 0,30 | 0,4615 | 0,4615 |
| Serviceberater/in | 005695 | 100 % | 1,00 | 1,00 | 1,00 |

Personalnummer 003002 ("Leiter/in Beratungs-Center") ist laut Rohdaten **gleichzeitig Leiter/in an zwei Standorten** (hier Maichingen, sowie am Beratungs-Center-Verbund Maichingen mit `Sollarbeitszeit` 19,89 Std.). Die Person hat `BsGrd = 100 %` (reale Kapazität = 1,0 FTE), erscheint aber technisch mit `1,0` auf **beiden** Zeilen (macht in Summe 2,0 — eine Überzählung um 1,0 FTE, wenn man naiv zeilenweise summiert). Die Personen-Korrektur verteilt die reale 1,0 FTE proportional zum Soll-Gewicht der beiden Stellen (19,11 / (19,11+19,89) = 0,49 auf Maichingen, 0,51 auf die andere OE) — Summe bleibt exakt 1,0 FTE für die Person. Das System markiert diesen Fall zusätzlich mit dem Flag `exception_required_mak_gt_1`, weil die technische Summe (2,0) die reale Personenkapazität (1,0) übersteigt — ein bewusster Prüfhinweis für Stammdaten-Qualität, keine Rechenungenauigkeit.

### 11.3 Fazit
- Die **Berechnungslogik für IST-MAK ist vollständig nachvollzogen und unabhängig reproduzierbar**: Alle vier Prüfszenarien (Gesamt + 3 OEs) stimmen zwischen unabhängiger Neuberechnung und Live-Dashboard exakt überein (die einzige Abweichung — 3,6–3,7 FTE auf Gesamtebene — ist die bekannte, korrekt funktionierende Planstellennr-Dedup-Bereinigung, keine Unbekannte).
- Die **Personen-Korrektur bei Mehrfachplanstellen funktioniert korrekt** und verhindert eine Überzählung von aktuell 19,0 FTE unternehmensweit — ein früher dokumentierter Befund ([K1] im MAK-Berechnungs-Dossier vom 2026-03-11) ist im aktuellen Code behoben.
- SOLL und IST sind **methodisch nicht symmetrisch**: SOLL bleibt auf Zeilenebene (Planstellen-Sicht) und wird erst für die Anzeige exklusionsbereinigt (`SOLL_MAK_View`); IST wird bereits auf Snapshot-Ebene direkt genullt und zusätzlich personenbezogen umverteilt. Für den Anwender ist das nicht sichtbar, aber wichtig für die Interpretation von Zwischenwerten in Debug-Exports.
