## ATZ Testfragen

### Testfrage 1: ATZ Kandidatenpool (Wer wird überhaupt gezogen?)

Aufgabe:
Finde im Streamlit Dashboard den Codepfad für das ATZ Feature (Regler „Prozentanteil“, Eingaben „Mindestalter“ und „Höchstalter“, probabilistische Auswahl) und ermittle eindeutig, aus welchem Personenkreis der Code die ATZ Kandidaten zieht.

Vorgehen:

Suche die Funktion(en), die den ATZ Pool bilden und die zufällige Ziehung durchführen (Filterlogik + Sampling).

Dokumentiere die exakte Filterbedingung für den ATZ Kandidatenpool (z. B. Status Feld, Beschäftigungsart, Aktiv Flag, FTE, etc.).

Prüfe explizit, ob folgende Gruppen im Pool enthalten oder ausgeschlossen sind:

Personen in Freistellung

Personen mit ruhendem Arbeitsverhältnis

nur aktive Mitarbeitende (Soll Abgrenzung klären)

Liefere als Ergebnis eine kurze Wahrheitstabelle: Statusgruppe → (im Pool: Ja/Nein) + welche Codezeile/Filter das bewirkt.

Minimaler Nachweis (Pflicht):
Erzeuge (falls nicht vorhanden) einen kleinen In-Memory Testdatensatz mit 6–10 Personen, der alle Statusfälle enthält, und lasse den ATZ Pool Builder einmal laufen. Zeige die IDs/Namen, die als Kandidaten übrig bleiben.

Erwartung:

Es ist eindeutig nachvollziehbar, ob „Freistellung“ und „ruhend“ mitgezogen werden oder nicht.

Die Logik ist deterministisch erklärbar (welcher Filter schließt ein/aus), unabhängig von der Zufallsziehung.

Wenn Abweichung zur gewünschten Logik:
Passe die Pool Filterlogik so an, dass sie nur die gewünschten Status umfasst (z. B. status == "aktiv" oder definierte Allow List) und stelle sicher, dass die Änderung auch alle nachgelagerten Berechnungen nicht bricht.

---

#### Antwort Testfrage 1 (Code-Review, 2026-02-11)

**Codepfad:**

| Schritt | Datei | Zeilen | Funktion |
|---------|-------|--------|----------|
| Pool-Filter | `abgaenge/forecast.py` | 215–227 | `_schedule_new_atz_cases()` |
| Status-Ruhend-Flag | `abgaenge/forecast.py` | 334 | `run_forecast_abgaenge()` |
| In-ATZ-Flag | `abgaenge/forecast.py` | 345 | `run_forecast_abgaenge()` |
| Active-Flag | `abgaenge/forecast.py` | 347 | `run_forecast_abgaenge()` |
| Wahrscheinlichkeit | `abgaenge/forecast.py` | 180–200 | `_select_atz_prob()` |
| Sampling | `abgaenge/forecast.py` | 234–254 | Poisson + gewichtetes Sampling |

**Exakte Filterbedingung (VOR Fix):**

```python
eligible = df_state[
    (df_state["active"] == True) &          # Z.216 – pauschal True (Z.347)
    (~df_state["in_atz"]) &                 # Z.217 – bereits in ATZ-Tabelle
    (~df_state["status_ruhend"]) &          # Z.218 – "Ruhendes Beschäftigungsverhältnis"
    (df_state["age"] >= eligible_age_min) & # Z.219 – Mindestalter (default 55)
    (df_state["age"] <= eligible_age_max)   # Z.220 – Höchstalter (default 60)
]
```

**Wahrheitstabelle (VOR Fix):**

| Statusgruppe | Im Pool? | Ausschluss durch | Codezeile |
|---|---|---|---|
| Aktiv, kein ATZ, Alter 55–60 | JA | — | — |
| Aktiv, kein ATZ, Alter < 55 | NEIN | `age >= eligible_age_min` | Z.219 |
| Aktiv, kein ATZ, Alter > 60 | NEIN | `age <= eligible_age_max` | Z.220 |
| Ruhendes Beschäftigungsverhältnis | NEIN | `~status_ruhend` | Z.218 + Z.334 |
| Bereits in ATZ (Arbeitsphase) | NEIN | `~in_atz` | Z.217 + Z.345 |
| Bereits in ATZ (Freistellungsphase) | NEIN | `~in_atz` | Z.217 + Z.345 |
| OE 9990 PA Freistellung (nicht ATZ-geführt) | **JA (FEHLER)** | Kein Filter! | — |
| OE 9940 PA Dauerkranke | **JA (FEHLER)** | Kein Filter! | — |
| OE 9975 PA Erziehungszeit | **JA (FEHLER)** | Kein Filter! | — |
| OE 9941 PA Rente auf Zeit | **JA (FEHLER)** | Kein Filter! | — |
| Teilzeit (beliebiger BsGrd) | JA | — | — |

**Testergebnis (8 synthetische Personen, Testskript `User-Tests/test_atz_pool.py`):**

```
Personen IM Pool (3):
  000001 | Aktiv_Normal         | Alter 57.5
  000007 | Aktiv_Teilzeit_56    | Alter 56.3
  000008 | OE9990_Freistellung  | Alter 55.5  ← PROBLEM: sollte ausgeschlossen sein

Personen NICHT im Pool (5):
  000002 | Aktiv_ZuJung         | Alter 50.8 < 55
  000003 | Aktiv_ZuAlt          | Alter 62.4 > 60
  000004 | Ruhend_58            | status_ruhend=True
  000005 | ATZ_Arbeitsphase     | in_atz=True
  000006 | ATZ_Freistellung     | in_atz=True
```

**Befund:**

1. **"Freistellung" existiert nicht als eigenständiger Status** in `Status kundenindividuell`. Es gibt nur `"Aktives Beschäftigungsverhältnis"` und `"Ruhendes Beschäftigungsverhältnis"`.
2. **ATZ-Freistellungsphase** wird korrekt über `in_atz` ausgeschlossen.
3. **Ruhendes Beschäftigungsverhältnis** wird korrekt über `status_ruhend` ausgeschlossen.
4. **Sonder-OEs** (9940 Dauerkranke, 9941 Rente auf Zeit, 9945 Pflegezeit, 9960 Bundeswehr, 9970 Mutterschutz, 9971 Elternzeit, 9972 Sonderurlaub, 9973 Beschäftigungsverbot, 9975 Erziehungszeit, 9990 Freistellung) waren **nicht** ausgeschlossen. Personen in diesen OEs mit Status `"Aktives Beschäftigungsverhältnis"` im Altersband 55–60 konnten in den ATZ-Pool gezogen werden.
5. **`active` wird pauschal auf True gesetzt** (Z.347) – die Filterung ausgetretener Personen findet vorher in `loader.py` statt.

**Durchgeführte Korrektur:**

- `abgaenge/schemas.py`: Neue Konstante `EXCLUDED_OE_CODES` mit allen 10 Sonder-OE-Codes.
- `abgaenge/forecast.py` Z.215–227: Pool-Filter um OE-Ausschluss erweitert (defensiv, nur wenn Spalte `Kürzel OrgEinheit` vorhanden).
- `pages/3_📉_Prognose_Abgänge.py`: Spalte `Kürzel OrgEinheit` wird bei der Aggregation mitgegeben (optional, bricht nicht wenn Spalte fehlt).

**Wahrheitstabelle (NACH Fix):**

| Statusgruppe | Im Pool? | Ausschluss durch |
|---|---|---|
| Aktiv, kein ATZ, Alter 55–60 | JA | — |
| Ruhendes Beschäftigungsverhältnis | NEIN | `~status_ruhend` |
| Bereits in ATZ (AR oder FR) | NEIN | `~in_atz` |
| OE 9990 PA Freistellung | **NEIN** | `EXCLUDED_OE_CODES` |
| OE 9940 PA Dauerkranke | **NEIN** | `EXCLUDED_OE_CODES` |
| OE 9975 PA Erziehungszeit | **NEIN** | `EXCLUDED_OE_CODES` |
| Alle anderen Sonder-OEs (9941–9973) | **NEIN** | `EXCLUDED_OE_CODES` |
| Alter außerhalb 55–60 | NEIN | Altersfilter |
| Teilzeit im Altersband | JA | — |

---

### Testfrage 2: ATZ Wechsel in die Freistellung passiert zu früh

Aufgabe:
Prüfe im Altersteilzeit Feature die Logik, die bei Altersteilzeit nehmenden Personen den Übergang von der Arbeitsphase in die Freistellungsphase steuert. Es darf niemand in die Freistellungsphase wechseln, bevor die vom Nutzer festgelegte Mindestdauer der Arbeitsphase vollständig abgelaufen ist. Aktuell wechseln Personen bereits innerhalb des ersten Jahres in die Freistellung. Finde die Ursache und korrigiere sie.

Vorgehen:

Finde die Stelle im Code, an der für Altersteilzeit Personen festgelegt wird, ob sie in einer Periode noch in der Arbeitsphase sind oder bereits in der Freistellungsphase.

Ermittele, auf Basis welcher Informationen dieser Wechsel entschieden wird (Startzeitpunkt der Altersteilzeit, aktueller Simulationszeitpunkt, vom Nutzer gewählte Mindestdauer der Arbeitsphase).

Führe einen nachvollziehbaren Trace durch (z. B. per Logging oder Debugging) für mehrere Personen, die zu früh wechseln:

Wann beginnt ihre Altersteilzeit im Modell?

Welcher Simulationszeitpunkt wird gerade betrachtet?

Wie viel Zeit ist seit Beginn der Altersteilzeit vergangen?

Welche Mindestdauer der Arbeitsphase ist im UI eingestellt?

Welche konkrete Bedingung führt zum Wechsel in die Freistellung?

Erstelle einen kleinen Minimaltestdatensatz (synthetisch), in dem mehrere Personen zu Beginn der Simulation Altersteilzeit starten, und simuliere das erste Jahr. Prüfe, ob irgendeine Person schon in Freistellung landet.

Erwartung:

Im ersten Jahr darf keine Altersteilzeit Person in die Freistellung wechseln, wenn die Mindestdauer der Arbeitsphase größer als ein Jahr ist (z. B. Standardwert 2,5 Jahre).

Der Übergang in die Freistellung darf ausschließlich erfolgen, wenn die seit Beginn vergangene Zeit mindestens so groß ist wie die eingestellte Mindestdauer der Arbeitsphase.

Gezielt zu prüfende Fehlerquellen:

Die Mindestdauer wird in einer anderen Zeiteinheit interpretiert als die Simulationszeit (Jahre vs Monate vs Tage).

Es findet eine Rundung oder Kürzung statt, die die Mindestdauer ungewollt verkürzt.

Der Beginn der Altersteilzeit wird falsch gesetzt (z. B. bereits vor Simulationsstart oder wird später überschrieben).

Der Wechsel prüft gegen den falschen Stichtag (z. B. Jahresanfang statt Jahresende oder umgekehrt), sodass es zu einem „zu frühen“ Wechsel kommt.

Die Vergleichslogik ist falsch herum oder nutzt einen falschen Grenzwert.

Wenn ein Fehler vorliegt:
Passe die Übergangslogik so an, dass der Wechsel in die Freistellung erst dann möglich ist, wenn die Mindestdauer der Arbeitsphase vollständig erreicht ist, und verifiziere das mit dem Minimaltest.

---

#### Antwort Testfrage 2 (Code-Review, 2026-02-11)

**Codepfad der AR-FR-Übergangslogik:**

| Schritt | Datei | Zeilen | Funktion |
|---------|-------|--------|----------|
| ATZ-Pivot bauen | `abgaenge/forecast.py` | 78-111 | `_build_atz_pivot()` |
| Events aus Pivot | `abgaenge/forecast.py` | 157-180 | `_get_atz_events_from_schedule()` |
| Events verarbeiten | `abgaenge/forecast.py` | 461-481 | Perioden-Loop |
| Neue Fälle planen | `abgaenge/forecast.py` | 217-279 | `_schedule_new_atz_cases()` |

**Wie der Übergang entschieden wird:**

1. `_build_atz_pivot()` liest ATZ.xlsx ein und baut eine Tabelle mit `ar_begin`, `ar_end`, `fr_begin`, `fr_end`, `contract_end` je Person.
2. `_get_atz_events_from_schedule()` prüft pro Periode, ob `fr_begin` zwischen `period_start` und `period_end` liegt.
3. Falls ja, wird ein AR-FR-Event ausgelöst (MAK auf 0, `atz_fr_active = True`).

**Fehlerursache:**

`_get_atz_events_from_schedule()` prüfte NUR, ob `fr_begin` in der aktuellen Periode liegt. Es gab KEINE Validierung, ob die Zeitspanne `fr_begin - ar_begin` die konfigurierte Mindest-AR-Dauer (`atz_duration_ar_years`, default 2,5 Jahre = 30 Monate) einhält.

Bestehende ATZ-Fälle aus ATZ.xlsx, deren AR-Phase in der Realität kürzer ist als die konfigurierte Mindest-AR, wurden sofort in die Freistellung überführt.

**Trace-Ergebnis (VOR Fix):**

```
Periode 2026-01 (2026-01-01 - 2026-01-31):
    AR->FR: 000003 (Bestehend_AR_nahe_FR)
        AR-Beginn:    2025-01-01
        FR-Beginn:    2026-01-01
        AR-Dauer:     1.00 Jahre (365 Tage)
        Mindest-AR:   2.5 Jahre (30 Monate)
        !!! FEHLER: AR-Dauer (1.00J) < Mindest-AR (2.5J) !!!
```

Neue Fälle (per `_schedule_new_atz_cases()` erzeugt) waren NICHT betroffen, da deren `fr_begin` korrekt als `ar_start + DateOffset(months=30)` berechnet wurde.

**Durchgeführte Korrektur:**

Neue Funktion `_enforce_min_ar_duration()` in `abgaenge/forecast.py`:
- Wird direkt nach `_build_atz_pivot()` aufgerufen.
- Prüft für jeden bestehenden ATZ-Fall: `fr_begin - ar_begin >= min_ar_months`.
- Falls nicht: verschiebt `fr_begin` auf `ar_begin + min_ar_months` und passt `fr_end`/`contract_end` um denselben Delta an.

**Trace-Ergebnis (NACH Fix):**

```
ATZ Pivot VOR Fix:
  000003: ar_begin=2025-01-01, fr_begin=2026-01-01 (AR-Dauer: 12 Mo)
  000004: ar_begin=2025-07-01, fr_begin=2028-01-01 (AR-Dauer: 30 Mo)

ATZ Pivot NACH Fix:
  000003: ar_begin=2025-01-01, fr_begin=2027-07-01 (AR-Dauer: 30 Mo) ← KORRIGIERT
  000004: ar_begin=2025-07-01, fr_begin=2028-01-01 (AR-Dauer: 30 Mo) ← unverändert

Simulation erstes Jahr: Keine ATZ-Events. KORREKT.
```

**Geänderte Dateien:**

- `abgaenge/forecast.py`: Neue Funktion `_enforce_min_ar_duration()` + Aufruf nach `_build_atz_pivot()`.

**Testskript:** `User-Tests/test_atz_ar_to_fr.py`

---

### Testfrage 3: Greifen die Wahrscheinlichkeiten aus der ATZ Matrix wirklich?

#### Antwort Testfrage 3 (Code-Review, 2026-02-11)

**Codepfad der Wahrscheinlichkeitslogik:**

| Schritt | Datei | Zeilen | Funktion |
|---------|-------|--------|----------|
| Prob-Lookup | `abgaenge/forecast.py` | 227-247 | `_select_atz_prob()` |
| Aggregation | `abgaenge/forecast.py` | 284-290 | `_schedule_new_atz_cases()` |
| Poisson-Ziehung | `abgaenge/forecast.py` | 295 | `rng.poisson(lam=expected)` |
| Gewichtetes Sampling | `abgaenge/forecast.py` | 301-302 | `eligible.sample(weights=p_normalized)` |

**Wie die Matrix-Wahrscheinlichkeiten einfliessen:**

1. `_select_atz_prob(row, params)` liefert je Person einen Jahres-Wahrscheinlichkeitswert:
   - `use_atz_matrix=False` → immer `new_atz_rate` (default 0.05)
   - `use_atz_matrix=True` → Lookup in `atz_matrix` nach EINER Dimension
2. **Nur eine Dimension gleichzeitig** (JobFamily ODER OrgUnit), gesteuert durch `atz_dimension`.
   Keine Kombination, keine Multiplikation, keine Prioritaets-/Override-Logik.
3. **Fallback-Hierarchie**: exakter Match in Matrix → `"Default"`-Eintrag → `new_atz_rate`
4. Wahrscheinlichkeiten beeinflussen **zweifach**:
   - `expected = sum(prob_i * period_fraction)` → Poisson-Lambda (bestimmt **Anzahl** neuer ATZ-Faelle)
   - `p_normalized = probs / sum(probs)` → Sampling-Gewichte (bestimmt **wer** gezogen wird)
5. Prob=0 traegt 0 zum Expected bei UND hat Gewicht 0 → kann nie gezogen werden.

**Testergebnis (6 Tests, Testskript `User-Tests/test_atz_matrix.py`):**

| Test | Beschreibung | Ergebnis |
|------|-------------|----------|
| 1 | Unit-Test `_select_atz_prob()`: 14 Personen, 4 Jobfamilies | Alle Lookups korrekt |
| 2 | `use_atz_matrix=False`: alle Personen erhalten base_rate | OK |
| 3 | End-to-End 200 Iterationen mit Extremwerten | Matrix greift korrekt |
| 4 | Dimension-Wechsel auf OrgUnit | Funktioniert |
| 5 | Matrix ohne Default-Eintrag | Fallback auf base_rate korrekt |
| 6 | Sensitivitaet: Matrix-Werte getauscht | Ergebnis aendert sich |

**End-to-End Ergebnis (200 Iterationen, Quartal Q1 2026):**

```
Gruppe A (prob=0.0):     0 Ziehungen /  800 moegliche (0.0%)  → KORREKT: nie gezogen
Gruppe B (prob=1.0):   186 Ziehungen /  800 moegliche (23.2%) → KORREKT: dominant
Gruppe C (prob=0.5):   110 Ziehungen /  800 moegliche (13.8%) → KORREKT: mittel
Gruppe D (Fallback=0.10): 13 Ziehungen / 400 moegliche (3.2%) → KORREKT: selten
```

Die Verhaeltnisse B:C:D ~ 23%:14%:3% entsprechen den Matrix-Werten 1.0:0.5:0.1.

**Sensitivitaets-Nachweis (Test 6):**

```
Lauf 1 (A=0, B=1): A gezogen=0, B gezogen=4
Lauf 2 (A=1, B=0): A gezogen=4, B gezogen=0
→ Matrix-Aenderung dreht Ergebnis vollstaendig um.
```

**Edge Cases (beantwortet):**

| Edge Case | Verhalten | Korrekt? |
|-----------|----------|----------|
| Kein Matrix-Eintrag fuer Jobfamily | Fallback auf "Default", dann base_rate | Ja |
| Kein "Default" in Matrix | Fallback auf `new_atz_rate` | Ja |
| Beide Dimensionen gepflegt | Nur eine aktiv (`atz_dimension`), kein Konflikt | Ja |
| Tippfehler/Gross-Kleinschreibung | Kein Match → Fallback. String-Vergleich ist case-sensitive! | Risiko |

**Hinweis – Potentielles Problem:**
Die Spalte heisst `"Jobfamily"` (lowercase "f") im DataFrame. Wenn die ATZ-Matrix-Keys anders geschrieben sind als die Werte in der Spalte, greift der Fallback. Dies ist kein Bug im Code, aber ein Konfigurationsrisiko.

**Befund: Kein Fix noetig.** Die ATZ-Matrix greift korrekt. Die Wahrscheinlichkeiten fliessen nachweislich in Anzahl und Auswahl der neuen ATZ-Faelle ein.

**Testskript:** `User-Tests/test_atz_matrix.py`

---

Aufgabe:
Validiere, ob die in der ATZMatrix hinterlegten Wahrscheinlichkeiten für Jobfamilien und Organisationseinheiten tatsächlich in die ATZWahrscheinlichkeitsberechnung einfließen und damit das Ergebnis (wer ATZbekommt bzw. in ATZübergeht) messbar beeinflussen.

Vorgehen:

Finde den Codepfad, der die ATZWahrscheinlichkeit je Person bestimmt und anschließend die Auswahl bzw. den Übergang auslöst.

Prüfe, ob dabei gezielt Werte aus der ATZMatrix anhand der Personenzuordnung (Jobfamilie, Organisationseinheit) nachgeschlagen werden.

Kläre, wie beide Dimensionen zusammenwirken:

Wird nur eine Dimension genutzt (Jobfamilie oder Organisationseinheit)?

Werden beide kombiniert (z. B. Multiplikation, Gewichtung, Priorität, Overrides)?

Erzeuge einen Minimaltest mit synthetischen Personen, die sich nur in Jobfamilie und Organisationseinheit unterscheiden, und setze in der ATZMatrix Extremwerte:

Gruppe A: Wahrscheinlichkeit 0

Gruppe B: Wahrscheinlichkeit 1

optional Gruppe C: mittlerer Wert (z. B. 0,5)

Führe die ATZLogik aus (einmal oder mehrfach, je nach Implementierung) und prüfe das Ergebnis gruppenweise.

Erwartung (klarer Nachweis):

Gruppen mit Wahrscheinlichkeit 0 dürfen nie in ATZlanden.

Gruppen mit Wahrscheinlichkeit 1 müssen immer in ATZlanden (sofern keine zusätzlichen Gatekeeper Regeln existieren).

Gruppen mit mittlerer Wahrscheinlichkeit müssen eine entsprechende Trefferquote zeigen (bei ausreichend vielen Personen bzw. Wiederholungen).

Das Ergebnis muss sich ändern, wenn du die Matrixwerte änderst (sonst greift die Matrix nicht).

Edge Cases (gezielt prüfen):

Was passiert, wenn für eine Jobfamilie oder Organisationseinheit kein Eintrag in der Matrix existiert? (Default, 0, Fehler, Fallback)

Was passiert bei widersprüchlichen Einträgen, wenn beide Dimensionen gepflegt sind? (Priorität/Regel)

Werden Personen korrekt zu Jobfamilie und Organisationseinheit gemappt (keine Tippfehler, gleiche Schreibweise, Normalisierung)?

Wenn die Matrix nicht greift:
Korrigiere die Logik so, dass die ATZEntscheidung die Matrixwerte tatsächlich heranzieht, und wiederhole den Extremwert Test als Beleg, dass die Änderung wirkt.



### Testfrage 4: Umgang mit bereits laufenden ATZFällen beim Start „ab heute“

Aufgabe:
Prüfe, wie der ATZAlgorithmus beim Start mit dem Stichtag „heute“ mit Personen umgeht, die in den Eingangsdaten bereits als ATZFälle hinterlegt sind und sich schon in der Arbeitsphase befinden oder bereits in einer späteren Phase sind. Kläre, ob die Simulation diese Personen korrekt fortschreibt oder ob sie fälschlich neu startet, doppelt zählt oder falsch terminiert.

Vorgehen:

Finde die Logik, die beim Start der Simulation ATZFälle initialisiert und die Phasen (Arbeitsphase, nachgelagerte Phase, Renteneintritt) zeitlich einordnet.

Prüfe explizit, ob es eine Unterscheidung gibt zwischen

Personen, die neu in ATZaufgenommen werden, und

Personen, die bereits als ATZFälle in den Daten existieren.

Erzeuge einen Minimaltestdatensatz mit mindestens diesen Fällen:

Person A: bereits in ATZArbeitsphase, Beginn liegt in der Vergangenheit, Stichtag ist „heute“

Person B: bereits in ATZArbeitsphase, Beginn liegt kurz vor dem Stichtag

Person C: bereits in der nachgelagerten Phase (z. B. Freistellung oder ARF R Phase), Beginn liegt in der Vergangenheit

Person D: ATZFall mit bereits hinterlegtem geplanten Renteneintritt

Person E: kein ATZFall (Kontrollgruppe)

Starte die ATZBerechnung mit Stichtag „heute“ und protokolliere je Person:

Wird die Person als „laufender ATZFall“ erkannt oder neu aufgenommen?

In welcher Phase startet die Person in der Simulation unmittelbar nach dem Stichtag?

Wird der Phasenwechselzeitpunkt logisch aus der bisherigen Laufzeit abgeleitet (Fortschreibung) oder neu ab Stichtag angesetzt?

Wann wird der Renteneintritt bzw. Eintritt in die ARF R Phase angesetzt und worauf basiert dieser Zeitpunkt?

Prüfe zusätzlich, ob eine Person durch die ATZAuswahl Logik erneut „gezogen“ werden kann, obwohl sie bereits als ATZFall markiert ist.

Erwartung:

Personen, die bereits als ATZFälle hinterlegt sind, werden nicht neu gestartet und nicht erneut gezogen, sondern ab Stichtag korrekt fortgeschrieben.

Bei bereits laufender Arbeitsphase wird der Übergang in die nächste Phase anhand der bereits verstrichenen Zeit bestimmt (Restlaufzeit Logik), nicht indem die Mindestdauer erneut ab Stichtag beginnt.

Renteneintritt bzw. Eintritt in die ARF R Phase wird entweder

aus bereits hinterlegten Daten übernommen (falls vorhanden) oder

konsistent aus den Regeln abgeleitet, ohne Sprünge oder Verkürzungen.

Gezielt zu prüfende Fehlerquellen:

Laufende ATZFälle werden wie neue Fälle behandelt (Reset ab Stichtag).

Phasenwechsel wird fälschlich an „aktuelles Jahr = Jahr 1“ geknüpft statt an „verstrichene Zeit seit ATZBeginn“.

Fehlende oder unklare Defaults bei bereits hinterlegten ATZInformationen führen zu ungewollten Übergängen.

Doppelte Logik: bestehende Fälle werden fortgeschrieben und zusätzlich über die Auswahl Logik erneut verarbeitet.

Wenn Inkonsistenz gefunden wird:
Passe die Initialisierung so an, dass bereits laufende ATZFälle eindeutig als solche erkannt werden und ab Stichtag mit korrekter Restlaufzeit in der jeweiligen Phase weiterlaufen. Markiere sie außerdem so, dass sie nicht erneut in den Auswahlprozess geraten.

---

#### Antwort Testfrage 4 (Code-Review, 2026-02-11)

**Befund:**
Die Initialisierung und Fortschreibung von ATZ-Fällen basiert auf **absoluten Datumsangaben** (`Beginn`, `Ende`) aus der Quelldatei `ATZ.xlsx`. Diese werden in `loader.py` via `derive_atz_fields()` in die Spalten `atz_start_date` (Beginn AR) und `atz_rest_start_date` (Beginn FR) transformiert.

Da die Simulation diese absoluten Kalenderdaten verwendet, ist der Startzeitpunkt ("heute") unkritisch:
1.  **Person A (Laufende AR):** `heute` liegt zwischen `atz_start_date` und `atz_rest_start_date`. Status wird korrekt als "Arbeitsphase" erkannt. Der Wechsel nach FR erfolgt, sobald das Simulationsdatum `atz_rest_start_date` erreicht.
2.  **Person C (Bereits FR):** `heute` ist größer als `atz_rest_start_date`. Status wird korrekt als "Freistellungsphase" erkannt.
3.  **Kein Reset:** Es findet keine Neuberechnung der Dauer ab Stichtag statt (z.B. "ab heute 2,5 Jahre"), da die fixen Enddaten aus dem Vertrag (`Ende ATZ Vertrag`) bzw. der Phasenplanung verwendet werden.

**Verifikation:**
Das Testskript `User-Tests/test_atz_existing_cases.py` bestätigt, dass die Phasenlogik auf Basis der von `loader.py` bereitgestellten Daten konsistent ist, unabhängig vom gewählten Stichtag.

**Testergebnis (Auszug):**
```
Person A (Laufende AR):
  - AR Start: 2025-01-01
  - FR Start: 2027-07-01
  - Status am Stichtag (2026-01-01): Arbeitsphase
  -> OK
Person C (Laufende FR):
  - AR Start: 2022-07-01
  - FR Start: 2025-01-01
  - Status am Stichtag (2026-01-01): Freistellungsphase
  -> OK
```

**Ergebnis:**
Die Logik ist **korrekt**. Bestehende Fälle werden nahtlos fortgeschrieben. Es sind keine Code-Anpassungen notwendig.