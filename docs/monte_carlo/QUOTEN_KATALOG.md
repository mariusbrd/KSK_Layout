# Quotenvertrag für die Monte-Carlo-Darstellung

Stand: 10.11.2026. Dieser Vertrag beschreibt die derzeit angezeigten, von einer Simulation abhängigen Quoten. Parameterquoten und reine Ist-Analysen gehören nicht zum Mehrlaufpaket.

## OE-Jahresanalyse

Die Kachel **„Abgänge / Ausgangsbestand“** in `components/org_simulation_yearly.py` verwendet je ausgewählter Ebene (`Organisationseinheit`, `OE-Cluster`), Gruppenauswahl, Kennzahl (`Köpfe`, `MAK`) und Endjahr folgende Größen:

- Nenner: Summe des zertifizierten Bestands am Ausgangsstichtag über die ausgewählten Gruppen. Er ist für identische Eingabedaten in allen Seeds gleich, muss aber im Paket je Seed eindeutig verfügbar sein.
- Zähler: positive Abgangsverluste aus den **reportingbereinigten** Ereignissen der ausgewählten Gruppen bis einschließlich Endjahr. Für Köpfe zählt `RETIREMENT`/`ATZ_END` als Rente; für MAK zusätzlich `ATZ_AR_TO_FR`. Kündigungen und weitere Abgangsarten sind enthalten. Technische Bestandseffekte sind ausgeschlossen. Die Auswahl einzelner Zwischenjahre und Ereignisarten verändert die Kachel nicht.
- Quote: `Zähler / Nenner`, falls `Nenner > 0`; andernfalls nicht verfügbar (`None`, Anzeige „–“). Kein künstlicher Nullwert.
- Für N > 1: Zähler und Quote für **jeden Seed** aus dessen eigenen reportingbereinigten Ereignissen bilden; danach Mittelwert und empirische Quantile der Quoten. Die Quote der gemittelten Zähler und Nenner ist kein Ersatz.

## JF-Jahresanalyse

Die gleichnamige Kachel in `components/jobfamily_simulation_yearly.py` folgt demselben Grundsatz für `Jobfamily` und `JF-Cluster`. Nenner ist der sichtbare Ausgangsbestand der ausgewählten Gruppen; Zähler sind die reportingbereinigten negativen `Köpfe-Veränderung`- beziehungsweise `MAK-Veränderung`-Werte bis `max(years)` für `DEPARTURES | {ATZ_AR_TO_FR}`. Ohne ausgewählte Jahre oder ohne zertifizierten Bestand wird die Kachel nicht angezeigt. Die ausgewählten Ereignisarten filtern diese Kachel nicht. Gruppenköpfe können bei Mehrfachzuordnung dieselbe Person mehrfach zählen; das gilt auch für Zähler und Nenner der Quote.

## Kompakt plus Simulation und andere Ansichten

Die produktive Simulationsseite zeigt weitere Verhältniswerte. Vor Aufnahme in das Mehrlaufpaket ist je Kachel zu prüfen, ob sie aus dem simulierten Zielbestand, dem festen Stellenplan oder einem unveränderten Ist-Bestand stammt. Nur simulierte Verhältniswerte werden je Seed berechnet. EUR-basierte Quoten bleiben gemäß Projektvorgabe vertagt. Parameterquoten (z. B. eingestellte Kündigungswahrscheinlichkeit) werden nicht als Ergebnisverteilungen interpretiert.

## Technische Sperre für die Jahresquote

`annual_values` im aktuellen Paket sind **Originalereignisse**. Die Jahreskacheln lesen dagegen `reporting_events`. Der bisherige `build_complete_metric_packet` ruft bei JF `build_jobfamily_yearly_view(..., include_stock=False)` auf; dieser liefert ausdrücklich keinen reportingbereinigten Ereignisblock. Aus den vorhandenen Paketwerten lässt sich der Zähler deshalb nicht 1:1 rekonstruieren. Die Jahresquote darf bis zur Aufnahme und direkten Paritätsprüfung der reportingbereinigten Verluste **nicht** aus `annual_values` oder Bestandsdifferenzen abgeleitet werden.

## Prüf- und Freigaberegel

Für mindestens N=1 und N=10 werden Zähler, Nenner und Quote je Seed mit den bestehenden OE-/JF-Kachelfunktionen beziehungsweise deren unmittelbaren Rohwerten bei identischer Auswahl verglichen. Bei einem nicht verfügbaren Nenner wird keine Quote in ein Band aufgenommen und die Zahl gültiger Seeds explizit ausgewiesen; ein stillschweigend verkleinertes Band ist unzulässig. Die Einzelansicht verwendet weiter die bestehende Funktion und deren Rundung.
