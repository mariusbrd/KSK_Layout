# Testergebnisse: Azubi-Forecast (Prognose Zugaenge)

**Datum:** 2026-03-10 (aktualisiert nach Sprint 1-3 + Deep-Validation)
**Testfiles:** `tests/test_azubi_forecast.py` + `tests/test_azubi_deep.py` + `tests/test_azubi_sprint_changes.py`
**Gesamtergebnis:** 77/77 bestanden | 0 fehlgeschlagen

---

## Uebersicht Testabdeckung

| Suite | Tests | Ergebnis | Inhalt |
|-------|-------|----------|--------|
| `test_azubi_forecast.py` | 34 | 34/34 PASS | Basis-Lifecycle, Modi, Matrizen, MAK, Fixes |
| `test_azubi_deep.py` | 24 | 24/24 PASS | Destiny-Konsistenz, Timing, Schulden, Erkennung, Grenzfaelle, Makro-Bilanz |
| `test_azubi_sprint_changes.py` | 19 | 19/19 PASS | Sprint 1-3 Aenderungen: nearest_cycle, getrennte Debt-Pools, Jobfamily_pre_azubi, HC-Neutralitaet |

---

## A – Basis-Lebenszyklus

| Test | Ergebnis | Befund |
|------|----------|--------|
| A01 – Hire-Events erzeugt | PASS | ~15/Jahr korrekt generiert |
| A02 – Event-Schema korrekt | PASS | count=+1, mak=0.0, ID-Format `AZ_YYYY_NNN_XXXX` |
| A03 – Conversion-Paar balanciert | PASS | Conversion_Out und Conversion_In immer als Paar (gleiche PersNr-Menge) |
| A04 – Exit setzt count=-1 | PASS | Azubi_Exit traegt korrekte -1 Headcount-Aenderung |
| A05 – Abschluss-Jahr korrekt | PASS | Jan 2026 + 3y -> Aug 2029 (next_cycle) |
| A06 – Eindeutige IDs | PASS | Keine doppelten PersNr ueber 5 Jahres-Simulation |

---

## B – Abschluss-Modus (graduation_mode)

| Test | Ergebnis | Befund |
|------|----------|--------|
| B01 – next_cycle: Post-August +1 Jahr | PASS | Sep 2026 + 3y -> Aug 2030 (nicht 2029) |
| B02 – nearest_cycle: Kein kuenstlicher Delay | PASS | Sep 2026 + 3y -> Aug 2029 (1 Jahr frueher) |
| B03 – nearest_cycle = next_cycle fuer Pre-August | PASS | Maerz-Eintritt: beide Modi identisch (Aug 2029) |
| B04 – Modus beeinflusst Simulation | PASS | nearest_cycle erzeugt >= Absolventen im gleichen Zeitfenster |

**Fazit:** `graduation_mode` funktioniert korrekt. `nearest_cycle` eliminiert den systematischen +1-Jahres-Verzug fuer Post-August-Eintritte (neuer Parameter, backward-kompatibel).

---

## C – Uebernahmequote & Schicksal

| Test | Ergebnis | Befund |
|------|----------|--------|
| C01 – rate=1.0: nur Takeovers | PASS | Null Exits bei 100% Uebernahmequote |
| C02 – rate=0.0: nur Exits | PASS | Null Takeovers bei 0% Uebernahmequote |
| C03 – Deterministische Aufteilung | PASS | 15 Azubis × 80% = exakt 12 Takeovers + 3 Exits pro Kohorte |
| C04 – GraduationModus vorab vergeben | PASS | Jeder neue Azubi erhaelt bei Einstellung deterministische Destiny |

**Fazit:** Das Debt-System fuer deterministische Rundung ist exakt — kein probabilistisches Rauschen.

---

## D – Uebernahme-Matrix (JobFamily-Dimension)

| Test | Ergebnis | Befund |
|------|----------|--------|
| D01 – Matrix mit einem Ziel | PASS | Alle Conv_In-Events erhalten korrekte Jobfamily |
| D02 – Matrix mit zwei Zielen | PASS | Beide Jobfamilies erscheinen in Conv_In-Events |
| D03 – Deterministische Aufteilung (15, 2:1) | PASS | Exakt 10x A, 5x B (Largest Remainder) |
| D04 – Largest Remainder Korrektheit | PASS | 3+3+4=10, kein Off-by-One |
| D05 – Gewicht=0 wird ausgeschlossen | PASS | Kategorie mit Gewicht 0 erscheint nie im Ergebnis |
| D06 – Leere Matrix | PASS | **Bekanntes Problem dokumentiert** (siehe Bugs) |
| D07 – Wahrscheinlichkeitsverteilung | PASS | 3:1-Gewichtung ergibt ~75% A in 1000 Trials |

---

## E – Uebernahme-Matrix (OrgUnit-Dimension)

| Test | Ergebnis | Befund |
|------|----------|--------|
| E01 – OrgUnit-Matrix weist korrekte Einheit zu | PASS | Conv_In-Events enthalten Ziel-OrgUnit aus Matrix |
| E02 – OrgUnit-Modus setzt JF="Sonstige" | PASS | Alle Conv_In haben Jobfamily="Sonstige" im OrgUnit-Modus |
| E03 – OrgUnit + JF-Konsistenz | PASS | Kombination org_unit=Ziel UND JF="Sonstige" korrekt |

---

## F – MAK-Logik

| Test | Ergebnis | Befund |
|------|----------|--------|
| F01 – MAK=0 waehrend Ausbildung | PASS | Azubi_Hire: mak=0.0 |
| F02 – MAK=1.0 nach Uebernahme | PASS | Conv_In: mak=1.0 (Standard) |
| F03 – Conv_Out hat negative MAK | PASS | Entfernt MAK aus Azubi-Pool korrekt |
| F04 – Netto-MAK einer Uebernahme = +1.0 | PASS | Conv_Out(-0) + Conv_In(+1.0) = +1.0 pro Person |
| F05 – Netto-HC einer Uebernahme = 0 | PASS | Conv_Out(-1) + Conv_In(+1) = 0 HC-Aenderung |
| F06 – Benutzerdefinierter MAK-Wert | PASS | azubi_mak_after_takeover=0.75 korrekt uebertragen |

---

## G – Fix-Regressionen

| Test | Ergebnis | Befund |
|------|----------|--------|
| G01 – Jobfamily_pre_azubi erhalten | PASS | Fix 4: Original-JF vor Ueberschreibung gespeichert |
| G02 – is_internal_transition auf Conv-Events | PASS | Fix 5: Beide Conversion-Typen tragen Flag=True |
| G03 – Isolations-Modus korrekt | PASS | exclude_baseline_azubis=True: keine Baseline-Abschluesse |
| G04 – Default graduation_mode=next_cycle | PASS | Fix 1: Rueckwaerts-kompatibel als Default in params.py |

---

## H – Destiny-Konsistenz (Deep Tests)

| Test | Ergebnis | Befund |
|------|----------|--------|
| H01 – Takeover-Destiny -> Conversion_In | PASS | Alle vorgemerkten Takeover-Azubis enden als Conv_In |
| H02 – Exit-Destiny -> Azubi_Exit | PASS | Kein Azubi erscheint in beiden Exit- UND Conv_In-Events |
| H03 – Keine Doppelgraduierungen | PASS | Jede PersNr hat maximal ein Graduation-Event |
| H04 – Kein Abschluss vor Einstellung | PASS | Graduation-Datum immer nach Hire-Datum |

---

## I – Timing & Graduation-Datum (Deep Tests)

| Test | Ergebnis | Befund |
|------|----------|--------|
| I01 – Graduation immer am 01. August | PASS | Alle Grad.-Events haben Monat=8, Tag=1 |
| I02 – Mindestausbildungszeit eingehalten | PASS | nearest_cycle: immer >= 2 Jahre nach Hire |
| I03 – Randwerte Graduation-Funktion | PASS | Genau am Stichtag, Tag davor/danach korrekt |
| I04 – Bruchdauer korrekt | PASS | 1.5y ab Jan 2026 -> Aug 2027 (korrekte Monatsberechnung) |

---

## J – Schulden-System (Deep Tests)

| Test | Ergebnis | Befund |
|------|----------|--------|
| J01 – Makro-Quote trifft Ziel | PASS | 80% ueber 6 Jahre: Takeover-Rate = 80% ±5% |
| J02 – Nur ganzzahlige Event-Counts | PASS | Keine Bruchwerte bei beliebigen Raten |
| J03 – Jahres-Hire-Count stimmt | PASS | ~15/Jahr: immer innerhalb ±1 (Debt-Rundung) |

---

## K – Bestands-Azubi-Erkennung (Deep Tests)

| Test | Ergebnis | Befund |
|------|----------|--------|
| K01 – Erkennung via TVAoeD-Tarif | PASS | Persistente Erkennung ueber alle Perioden |
| K02 – Erkennung via Jobfamily="Ausbildung" | PASS | **Bug dokumentiert** (siehe Bugs) |
| K03 – Nicht-Azubis unveraendert | PASS | TrfGr=E9A + JF=Angestellte: keine ungewollten Aenderungen |

---

## L – Default-Key in Takeover-Matrix (Deep Tests)

| Test | Ergebnis | Befund |
|------|----------|--------|
| L01 – Default als wörtliche Jobfamily | PASS | **Bug dokumentiert**: Default-Key wird als JF-Name "Default" behandelt |
| L02 – Nur Default-Key in Matrix | PASS | Alle Azubis erhalten Jobfamily="Default" (Dokumentation des Verhaltens) |

---

## M – Grenzfaelle (Deep Tests)

| Test | Ergebnis | Befund |
|------|----------|--------|
| M01 – 0 Azubis/Jahr: keine Events | PASS | Korrekt leer |
| M02 – Kurze Ausbildungsdauer | PASS | 1y ab Aug 2025 -> Aug 2026; 1y ab Mrz 2026 -> Aug 2027 |
| M03 – Leerer Snapshot | PASS | Laeuft fehlerfrei, erzeugt Hire-Events |
| M04 – 100 Azubis/Jahr: keine ID-Kollision | PASS | Alle PersNr eindeutig (UUID-Suffix funktioniert) |
| M05 – Exakt 50% Aufteilung | PASS | Bei ungerader Kohorte max. ±1 Abweichung (korrekt) |

---

## N – MAK-Bilanz Makro (Deep Tests)

| Test | Ergebnis | Befund |
|------|----------|--------|
| N01 – MAK-Bilanz Hire vs. Uebernahme | PASS | Hire-MAK gesamt=0; Conv_In-MAK = Anzahl Takeovers |
| N02 – HC-Bilanz Netto | PASS | Mit retention=1.0: kein negativer Netto-HC |
| N03 – MAK=0 waehrend gesamter Ausbildung | PASS | Kein Azubi in Training hat mak>0 |

---

## Gesamtbewertung

| Bereich | Status | Hinweis |
|---------|--------|---------|
| Basis-Lebenszyklus | Korrekt | |
| graduation_mode (next/nearest) | Korrekt | Default: nearest_cycle (Sprint 1) |
| Uebernahmequote / Schicksal | Korrekt | Deterministisch und exakt |
| JF-Uebernahme-Matrix | Korrekt | 1 Bug bei leerer Matrix (Fehlkonfiguration) |
| OrgUnit-Uebernahme-Matrix | Korrekt | |
| MAK-Logik (Hire + Uebernahme) | Korrekt | HC- und MAK-Netto-Effekte exakt validiert |
| Fix-Regressionen (1, 4, 5) | Korrekt | Alle Fixes aktiv und getestet |
| Destiny-Konsistenz | Korrekt | Keine Doppelgraduierungen, kein Zeitparadox |
| Timing / August-Regel | Korrekt | Randwerte und Bruchdauern korrekt |
| Schulden-System (getrennte Pools) | Korrekt | Sprint 2: Baseline/Forecast-Debt entkoppelt |
| Grenzfaelle | Korrekt | Leerscenario, hohe Mengen, kurze Dauer |
| Bestands-Azubi-Erkennung | **Teilweise** | Bug K02: JF-only-Erkennung bricht ab Periode 2 |
| Default-Key in Matrix | **Teilweise** | Bug L01: Default-Key wird als JF-Name interpretiert |
| Jobfamily_pre_azubi in Events | Korrekt | Sprint 3: Feld jetzt in Conv_In Events enthalten |
| Conversion-Pairs im Detail-View | Korrekt | Sprint 3: Filter + Hinweis in Details-Tab |

---

## Umgesetzte Verbesserungen (Sprint 1–3)

### Sprint 1: graduation_mode Default auf nearest_cycle

**Aenderung:** `params.py` Default von `"next_cycle"` auf `"nearest_cycle"` umgestellt.

**Begruendung:** `next_cycle` fuehrt bei Eintritten nach August systematisch zu
bis zu 11 Monaten Verzug gegenueber der tatsaechlichen 3-Jahres-Ausbildungsdauer.
Beispiel: Eintritt Sep 2026 + 3 Jahre -> Aug **2029** (nearest) statt Aug **2030** (next).

**UI-Aenderung (Seite 4):** Radio-Selector "Abschluss-Modus" mit erklaerenden Info/Warning-Texten.
`graduation_mode` wird jetzt explizit in `run_params` uebergeben (war vorher nicht gesetzt).

**Test G04** angepasst: Erwartet jetzt `"nearest_cycle"` als Default.

---

### Sprint 2: Getrennte Takeover-Debt-Pools

**Aenderung:** `debts["takeover"]` aufgeteilt in:
- `debts["takeover_baseline"]`: Steuerung von Bestands-Azubis ohne vorbestimmtes GraduationModus
- `debts["takeover_forecast"]`: Destiny-Zuweisung fuer neu eingestellte Forecast-Azubis

**Begruendung:** Der gemeinsame Pool fuehrte zu erklaerungsbeduerftigem Verhalten: Schwankungen in
`new_cases_per_year` konnten Baseline-Outcomes kippen (reproduziert in Deep-Dive-Check C3).

**Auswirkung:** Nutzer-konfigurierte `retention_rate` beeinflusst jetzt ausschliesslich
Forecast-Azubis. Bestands-Azubis haben einen eigenen, unabhaengigen Abrundungs-Pool.

---

### Sprint 3: Jobfamily_pre_azubi und Conversion-Pairs

**Aenderung 1 (C4):** `Jobfamily_pre_azubi` wird jetzt auch in `Azubi_Conversion_In` Events
mitgefuehrt. Ermoeglicht Spurverfolgung: Welche Original-JF hatte eine uebergenommene Person?

**Aenderung 2 (C5):** Details-Tab (Seite 4) separiert jetzt interne Statuswechsel-Paare:
- Hinweistext: Anzahl interner Conv_Out/Conv_In Events wird angezeigt
- Checkbox: Nutzer kann interne Events optional ein-/ausblenden
- KPI "Gesamt Zugaenge": Tooltip zeigt Aufspaltung externe Zugaenge vs. interne Uebernahmen

---

## Bekannte Bugs (dokumentiert als Regressionstests)

### Bug K02 – Erkennungsverlust bei JF-only-Azubis (Mittel)

**Beschreibung:** Bestands-Azubis mit `Jobfamily="Azubi"` oder `"Ausbildung"`, aber `TrfGr != TVAoeD`
(z.B. `TrfGr="E9A"`) verlieren ihre Erkennung ab Periode 2.

**Ursache:** `_simulate_azubis` ueberschreibt `Jobfamily -> "Sonstige"` in Periode 1.
In Periode 2 wird `mask_azubi` neu ausgewertet — kein "TVA" in TrfGr, kein "Azubi"/"Ausbildung"
in Jobfamily -> Azubi ist unsichtbar und graduiert nie.

**Auswirkung in Produktion:** Gering, sofern alle echten Azubis im HR-System mit `TrfGr="TVAoeD"`
gepflegt sind. Kritisch, wenn alternative Tarifgruppen verwendet werden.

**Empfehlung:** Sicherstellen, dass alle Azubis im Datenbestand `TrfGr="TVAoeD"` tragen.
Alternativ: `GraduationDate` als persistenten Erkennungsmarker zusaetzlich pruefen.

---

### Bug D06/L01 – Default-Key in Takeover-Matrix (Niedrig)

**Beschreibung:** Wenn `"Default"` als Key mit positivem Gewicht in `takeover_matrix` uebergeben
wird, erstellt `distribute_deterministic` Azubis mit `Jobfamily="Default"` (wörtlich).

**Ursache:** `distribute_deterministic` hat keine Sonderbehandlung fuer den `"Default"`-Key.

**Auswirkung in Produktion:** Tritt nur auf, wenn der Nutzer den "Default"-Eintrag in der UI
manuell auf einen positiven Wert setzt. Nach dem UI-Fix (default_value=0.0) ist dies unwahrscheinlich.
Zusaetzlich: leere Matrix (`takeover_matrix={}`) fuehrt zu `Jobfamily="Unbekannt"` statt "Angestellte".

**Empfehlung:** Guard in `_simulate_azubis`: `if not matrix: skip distribution, keep defaults`.

---

### UI-Fix umgesetzt: Uebernahme-Verteilungs-Tabelle

**Problem:** Tabelle zeigte ueberall 100% (default_value=100.0), Nutzer dachte jede JF bekommt alle Azubis.

**Fix (Seite 4):**
- Leere Matrix wird mit gleichmaessiger Anfangsverteilung initialisiert (100/n % pro JF)
- `default_value=0.0`: neue/unbekannte JFs starten bei 0%
- Label geaendert: "Anteil der Uebernahmen – Summe sollte 100% ergeben"
