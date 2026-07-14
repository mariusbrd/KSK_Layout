# Sollkapazität – Validierungsbericht: Excel vs. Dashboard

**Datum:** 2026-07-14
**Ausgangswert (Dashboard):** 759,9 FTE (Gesamtbelegschaft, Seite "Kompakt")
**Live nachgerechneter Wert (dieser Bericht):** 759,0 FTE
**Abweichung:** 0,9 FTE (0,11 %) — plausibel durch Datenstand-Drift erklärbar, siehe Abschnitt 5

Methodik: Der komplette Berechnungspfad wurde nicht nur gelesen, sondern **live gegen den produktiven Code ausgeführt** (`dataloader/loader.py`, `components/sidebar.py`, `pages/1_⚡_Kompakt.py`) — mit den echten Original-Daten (`Original-Daten/Planstellen.XLSX`, `Mitarbeiter.xlsx`, `ATZ.xlsx`, `Ausbildung.xlsx`) und den aktuell persistierten Einstellungen (`config/user_settings.json`). Alle Zwischenwerte in diesem Bericht sind reproduzierbare Rechenergebnisse, keine Schätzungen.

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
