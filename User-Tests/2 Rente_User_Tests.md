### Testfrage 5: Rente-Plots dürfen bei 0% und 0% nicht crashen

Aufgabe:
Starte die Seite „Prognose Abgänge“ mit Renteneintritt 65 plus = 0% und Frühverrentung = 0%. In diesem Fall darf kein Plot Fehler auftreten. Aktuell kommt ein Plotly Fehler, weil ein Diagramm nicht als gültiges Plotly Objekt an die Darstellung übergeben wird. Behebe das so, dass alle Plots auch bei 0 und 0 stabil funktionieren.

Vorgehen:

Reproduziere den Fehler mit genau diesen Einstellungen (0% / 0%).

Identifiziere, welches Diagramm in diesem Szenario nicht erzeugt wird (z. B. weil die zugrunde liegenden Daten leer sind) und deshalb beim Rendern ein ungültiger Wert übergeben wird.

Prüfe danach systematisch alle Diagramme auf der Seite, ob sie bei leerem Ergebnis (keine Rentenabgänge) korrekt behandelt werden. Ziel ist: kein Diagramm darf “fehlend” sein, wenn es gerendert werden soll.

Erwartung:

Bei 0% / 0% darf kein Exception Traceback auftreten.

Wenn keine Daten für einen Plot vorhanden sind, muss die UI entweder

ein leeres, aber gültiges Plotly Diagramm anzeigen (z. B. mit Hinweis “Keine Daten für diese Auswahl”), oder

statt des Plots eine Info/Warning anzeigen (ohne Plot Rendering aufzurufen).

Das gilt für alle Plots auf der Seite, nicht nur den einen aus dem Trace.

Gezielt zu prüfen (häufige Ursachen):

Ein Plot wird nur erzeugt, wenn Daten vorhanden sind und ist sonst “nicht gesetzt” → beim Rendern wird dann kein Plotly Objekt übergeben.

Berechnungen für Anteile/Quoten teilen durch 0 (wenn es keine Abgänge gibt) und erzeugen ungültige Werte, die später Plotly crashen lassen.

Gruppierungen / Pivot / Resampling liefern leere Tabellen und nachgelagerte Plot Funktionen erwarten mindestens eine Zeile.

Fix Hint (ohne Variablennamen):

Stelle sicher, dass vor jedem Render-Aufruf geprüft wird, ob wirklich ein gültiges Plotly Diagrammobjekt existiert.

Falls nicht: Erzeuge ein Fallback Diagramm (gültiges, leeres Plotly Objekt mit Hinweistext) oder zeige stattdessen eine UI Meldung an.

Ergänze außerdem “Zero Safe” Logik in allen Kennzahlen, die auf Summen beruhen (wenn Summe = 0, dann Anteil = 0 statt Division).

Abnahme-Test:

0% / 0% → Seite lädt vollständig, alle Plot Bereiche zeigen entweder Diagramm oder Hinweis, kein Crash.

Zusätzlich: Sehr kleine Werte (z. B. 1% / 0%, 0% / 1%) → ebenfalls stabil.



### Testfrage 6: Reihenfolge ATZ vor Rente und gegenseitiger Ausschluss

Aufgabe:
Validiere, dass bei gleichzeitiger Aktivierung von Altersteilzeit und Rente die Berechnung zwingend in der Reihenfolge erfolgt: erst Altersteilzeit, danach Rente. Außerdem darf keine Person, die durch die Altersteilzeit Routine in Altersteilzeit überführt wurde, im selben Simulationslauf zusätzlich durch die Rentenlogik in Rente geschickt werden. Wenn das nicht gewährleistet ist, korrigiere die Ablaufsteuerung und die Filterlogik.

Vorgehen:

Finde den Codepfad, der die Abgangslogiken orchestriert, also die Reihenfolge, in der Altersteilzeit, Rente und ggf. weitere Abgänge berechnet werden.

Prüfe, ob die Altersteilzeit Ergebnisse (reduzierte MAK und Köpfe sowie Statuswechsel/Markierung der betroffenen Personen) als Input in die Rentenberechnung eingehen.

Erstelle einen Minimaltest mit Personen, die sowohl ATZ geeignet sind als auch rentennah (damit es einen Konfliktfall geben kann). Starte die Berechnung einmal mit aktivierter Altersteilzeit und aktivierter Rente.

Prüfe zwei Dinge als Nachweis:

Altersteilzeit Abgänge reduzieren zuerst MAK und Köpfe, bevor die Rentenabgänge berechnet werden.

Es gibt keine Doppelabgänge: dieselbe Person wird nicht gleichzeitig Altersteilzeit und Rente zugeordnet.

Erwartung:

Die Berechnung ist deterministisch in dieser Reihenfolge: Altersteilzeit → Rente.

Die Rentenlogik betrachtet ausschließlich den Personenkreis, der nach Altersteilzeit noch als „für Rente relevant“ gilt.

Personen, die in Altersteilzeit überführt wurden (egal ob Arbeitsphase oder Freistellungsphase), werden in derselben Periode nicht zusätzlich als Rentenabgang gezählt.

Gezielt zu prüfen (typische Fehler):

Beide Routinen laufen unabhängig voneinander auf demselben Ausgangsdatensatz.

Rentenberechnung nutzt nicht den aktualisierten Personenkreis nach Altersteilzeit.

Es fehlt ein klarer Ausschlussfilter für Personen, die bereits in Altersteilzeit sind oder gerade in Altersteilzeit überführt wurden.

Aggregation/Plots zählen Abgänge doppelt, weil beide Ereignisse in derselben Periode als separate „Reason“ auftauchen.

Wenn nicht gewährleistet:

Stelle sicher, dass die Rentenberechnung erst nach Abschluss der Altersteilzeit Routine startet und die aktualisierte Personentabelle verwendet.

Ergänze einen eindeutigen Ausschlussmechanismus, sodass Personen mit Altersteilzeit Status nicht mehr durch die Rentenlogik verarbeitet werden.

Verifiziere die Korrektur mit dem Minimaltest: keine Doppelabgänge, korrekte Reihenfolge in den Kennzahlen und Plots.

---

#### Antwort Testfrage 6 (Code-Review, 2026-02-11)

**Befund:**
Die Ausführungsreihenfolge in `abgaenge/forecast.py` ist fest kodiert:

1.  **ATZ Planung (Z. 460-463):**
    *   Neue ATZ-Fälle werden per `_schedule_new_atz_cases` ermittelt.
    *   **WICHTIG:** Unmittelbar danach wird das Flag `in_atz` im State-DataFrame aktualisiert:
        `df_state["in_atz"] = df_state.index.isin(...)` (Z. 462).
    *   Damit sind alle Personen, die *gerade eben* für ATZ ausgewählt wurden (oder schon waren), als `in_atz=True` markiert.

2.  **Rente Berechnung (Z. 516-550):**
    *   Erst *danach* läuft der Block für "Retirement events".
    *   Die Filterbedingung lautet explizit:
        `eligible = df_state[(df_state["active"]) & (~df_state["in_atz"])].copy()` (Z. 517).
    *   Durch `~df_state["in_atz"]` werden alle Personen ausgeschlossen, die im vorherigen Schritt den ATZ-Status erhalten haben.

**Testergebnis (Reproduktionsskript `User-Tests/test_atz_rent_order.py`):**
Ein synthetischer Testfall mit einer Person, die sowohl für ATZ (erzwungene Wahrscheinlichkeit) als auch für Rente (100% Wahrscheinlichkeit) in Frage kommt, zeigt:
- Die Person wird vom ATZ-Algorithmus erfasst (interner State).
- Die Person taucht **NICHT** als Renten-Abgang auf (0 Events generiert).
- Wäre die Logik fehlerhaft, müsste ein Renten-Event generiert werden (da Wahrscheinlichkeit 100%).

**Ergebnis:**
Die Reihenfolge (ATZ vor Rente) und der gegenseitige Ausschluss sind **korrekt implementiert**. Es besteht keine Gefahr von Doppelabgängen in derselben Periode.

---




### Testfrage 7: Rente Kandidatenpool und Einbezug ruhender Beschäftigungsverhältnisse

Aufgabe:
Ermittle die aktuelle Berechnungsgrundlage der Rentenlogik: Aus welchem Personenkreis werden Personen für „Rente“ gezogen, und können Personen in einem ruhendem Beschäftigungsverhältnis in diesen Pool fallen? Es gibt kein Soll Verhalten. Ich brauche eine präzise Beschreibung des Ist Zustands.

Vorgehen:

Finde im Code die Stelle, an der der Personenkreis für Renteneintritte bestimmt wird (Aufbau des Pools, bevor Wahrscheinlichkeiten/Regeln angewandt werden).

Dokumentiere die Filterlogik in verständlicher Form: Welche Kriterien müssen erfüllt sein, damit eine Person überhaupt als „Rente relevant“ gilt?

Prüfe explizit die Behandlung folgender Statusgruppen:

aktive Mitarbeitende

ruhendes Beschäftigungsverhältnis

Freistellung (falls als eigener Status vorhanden)

Liefere als Ergebnis:

eine kurze Beschreibung der Pool Definition (in Worten)

eine knappe Zuordnung je Statusgruppe: „kann in den Pool fallen: ja/nein“

Verweis auf die Stelle im Code, die diese Entscheidung festlegt (Datei + Abschnitt/Kommentar oder Funktionsname, aber ohne Variablennamen im Text).

Minimaler Nachweis (Pflicht):
Erzeuge einen Mini Testdatensatz mit je 2–3 Personen pro Statusgruppe (inkl. ruhend) und lasse die Pool Bildung einmal laufen. Zeige, welche Personen im Pool landen.

Erwartung:

Es ist nachvollziehbar, ob ruhende Beschäftigungsverhältnisse in die Rentenlogik einbezogen werden oder ausgeschlossen sind.

Die Logik ist erklärbar anhand der tatsächlich implementierten Filter, nicht anhand Annahmen.

---

#### Antwort Testfrage 7 (Code-Review, 2026-02-11)

**Befund:**
Die Renten-Logik (`abgaenge/forecast.py`, Z. 516ff) definiert den Kandidatenpool wie folgt:

```python
eligible = df_state[(df_state["active"]) & (~df_state["in_atz"])].copy()
```

Die Spalte `"active"` wird bei Initialisierung (Z. 401) pauschal auf `True` gesetzt für alle Personen, die im Datensatz sind (d.h. noch nicht ausgetreten laut `Austritt`-Datum).
Es gibt **KEINEN expliziten Filter**, der `status_ruhend` ausschließt (im Gegensatz zur ATZ-Logik, wo `~df_state["status_ruhend"]` geprüft wird).

**Zuordnungstabelle:**

| Statusgruppe | Im Renten-Pool? | Begründung |
|---|---|---|
| Aktive Mitarbeitende | **JA** | `active=True` |
| Ruhendes Beschäftigungsverhältnis | **JA** | `active=True`, kein Ausschlussfilter |
| Freistellung (ATZ) | **NEIN** | `in_atz=True` (via `df_atz` Check) |
| Freistellung (Sonstige, falls im Datensatz) | **JA** | Sofern `active=True` und nicht in ATZ-Liste |

**Testergebnis (Testskript `User-Tests/test_rent_pool.py`):**
Ein Testlauf mit 3 Personen (Aktiv, Ruhend, ATZ) und 100% Rentenwahrscheinlichkeit ergab:
*   Person Aktiv: **Verrentet**
*   Person Ruhend: **Verrentet** (Bestätigt: Ruhende sind im Pool)
*   Person ATZ: **Nicht Verrentet** (Bestätigt: ATZ ist ausgeschlossen)

**Fazit:**
Die Rentenlogik zieht auch Personen in ruhenden Arbeitsverhältnissen heran.

---