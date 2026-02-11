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



### Testfrage 3: Greifen die Wahrscheinlichkeiten aus der ADZ Matrix wirklich?

Aufgabe:
Validiere, ob die in der ADZ Matrix hinterlegten Wahrscheinlichkeiten für Jobfamilien und Organisationseinheiten tatsächlich in die ADZ Wahrscheinlichkeitsberechnung einfließen und damit das Ergebnis (wer ADZ bekommt bzw. in ADZ übergeht) messbar beeinflussen.

Vorgehen:

Finde den Codepfad, der die ADZ Wahrscheinlichkeit je Person bestimmt und anschließend die Auswahl bzw. den Übergang auslöst.

Prüfe, ob dabei gezielt Werte aus der ADZ Matrix anhand der Personenzuordnung (Jobfamilie, Organisationseinheit) nachgeschlagen werden.

Kläre, wie beide Dimensionen zusammenwirken:

Wird nur eine Dimension genutzt (Jobfamilie oder Organisationseinheit)?

Werden beide kombiniert (z. B. Multiplikation, Gewichtung, Priorität, Overrides)?

Erzeuge einen Minimaltest mit synthetischen Personen, die sich nur in Jobfamilie und Organisationseinheit unterscheiden, und setze in der ADZ Matrix Extremwerte:

Gruppe A: Wahrscheinlichkeit 0

Gruppe B: Wahrscheinlichkeit 1

optional Gruppe C: mittlerer Wert (z. B. 0,5)

Führe die ADZ Logik aus (einmal oder mehrfach, je nach Implementierung) und prüfe das Ergebnis gruppenweise.

Erwartung (klarer Nachweis):

Gruppen mit Wahrscheinlichkeit 0 dürfen nie in ADZ landen.

Gruppen mit Wahrscheinlichkeit 1 müssen immer in ADZ landen (sofern keine zusätzlichen Gatekeeper Regeln existieren).

Gruppen mit mittlerer Wahrscheinlichkeit müssen eine entsprechende Trefferquote zeigen (bei ausreichend vielen Personen bzw. Wiederholungen).

Das Ergebnis muss sich ändern, wenn du die Matrixwerte änderst (sonst greift die Matrix nicht).

Edge Cases (gezielt prüfen):

Was passiert, wenn für eine Jobfamilie oder Organisationseinheit kein Eintrag in der Matrix existiert? (Default, 0, Fehler, Fallback)

Was passiert bei widersprüchlichen Einträgen, wenn beide Dimensionen gepflegt sind? (Priorität/Regel)

Werden Personen korrekt zu Jobfamilie und Organisationseinheit gemappt (keine Tippfehler, gleiche Schreibweise, Normalisierung)?

Wenn die Matrix nicht greift:
Korrigiere die Logik so, dass die ADZ Entscheidung die Matrixwerte tatsächlich heranzieht, und wiederhole den Extremwert Test als Beleg, dass die Änderung wirkt.



### Testfrage 4: Umgang mit bereits laufenden ADZ Fällen beim Start „ab heute“

Aufgabe:
Prüfe, wie der ADZ Algorithmus beim Start mit dem Stichtag „heute“ mit Personen umgeht, die in den Eingangsdaten bereits als ADZ Fälle hinterlegt sind und sich schon in der Arbeitsphase befinden oder bereits in einer späteren Phase sind. Kläre, ob die Simulation diese Personen korrekt fortschreibt oder ob sie fälschlich neu startet, doppelt zählt oder falsch terminiert.

Vorgehen:

Finde die Logik, die beim Start der Simulation ADZ Fälle initialisiert und die Phasen (Arbeitsphase, nachgelagerte Phase, Renteneintritt) zeitlich einordnet.

Prüfe explizit, ob es eine Unterscheidung gibt zwischen

Personen, die neu in ADZ aufgenommen werden, und

Personen, die bereits als ADZ Fälle in den Daten existieren.

Erzeuge einen Minimaltestdatensatz mit mindestens diesen Fällen:

Person A: bereits in ADZ Arbeitsphase, Beginn liegt in der Vergangenheit, Stichtag ist „heute“

Person B: bereits in ADZ Arbeitsphase, Beginn liegt kurz vor dem Stichtag

Person C: bereits in der nachgelagerten Phase (z. B. Freistellung oder ARF R Phase), Beginn liegt in der Vergangenheit

Person D: ADZ Fall mit bereits hinterlegtem geplanten Renteneintritt

Person E: kein ADZ Fall (Kontrollgruppe)

Starte die ADZ Berechnung mit Stichtag „heute“ und protokolliere je Person:

Wird die Person als „laufender ADZ Fall“ erkannt oder neu aufgenommen?

In welcher Phase startet die Person in der Simulation unmittelbar nach dem Stichtag?

Wird der Phasenwechselzeitpunkt logisch aus der bisherigen Laufzeit abgeleitet (Fortschreibung) oder neu ab Stichtag angesetzt?

Wann wird der Renteneintritt bzw. Eintritt in die ARF R Phase angesetzt und worauf basiert dieser Zeitpunkt?

Prüfe zusätzlich, ob eine Person durch die ADZ Auswahl Logik erneut „gezogen“ werden kann, obwohl sie bereits als ADZ Fall markiert ist.

Erwartung:

Personen, die bereits als ADZ Fälle hinterlegt sind, werden nicht neu gestartet und nicht erneut gezogen, sondern ab Stichtag korrekt fortgeschrieben.

Bei bereits laufender Arbeitsphase wird der Übergang in die nächste Phase anhand der bereits verstrichenen Zeit bestimmt (Restlaufzeit Logik), nicht indem die Mindestdauer erneut ab Stichtag beginnt.

Renteneintritt bzw. Eintritt in die ARF R Phase wird entweder

aus bereits hinterlegten Daten übernommen (falls vorhanden) oder

konsistent aus den Regeln abgeleitet, ohne Sprünge oder Verkürzungen.

Gezielt zu prüfende Fehlerquellen:

Laufende ADZ Fälle werden wie neue Fälle behandelt (Reset ab Stichtag).

Phasenwechsel wird fälschlich an „aktuelles Jahr = Jahr 1“ geknüpft statt an „verstrichene Zeit seit ADZ Beginn“.

Fehlende oder unklare Defaults bei bereits hinterlegten ADZ Informationen führen zu ungewollten Übergängen.

Doppelte Logik: bestehende Fälle werden fortgeschrieben und zusätzlich über die Auswahl Logik erneut verarbeitet.

Wenn Inkonsistenz gefunden wird:
Passe die Initialisierung so an, dass bereits laufende ADZ Fälle eindeutig als solche erkannt werden und ab Stichtag mit korrekter Restlaufzeit in der jeweiligen Phase weiterlaufen. Markiere sie außerdem so, dass sie nicht erneut in den Auswahlprozess geraten.