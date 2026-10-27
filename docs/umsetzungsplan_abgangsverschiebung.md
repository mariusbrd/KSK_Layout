# Umsetzungsplan: Zeitpunkt eines simulierten Abgangs verschieben

Stand: 27.10.2026. Ausgangspunkt: `feature/simulation-validieren`, Sicherungsstand `6f8a8aa`.

## Ziel und unveränderliche Regeln

Eine berechtigte Person kann auf „Simulation abnehmen“ den Zeitpunkt **eines bereits simulierten, eindeutig zugeordneten Abgangs** in ein späteres Simulationsjahr verschieben. Die Person bleibt bis zum neuen Abgang im Jahresbestand; nach dem neuen Ereignis fällt ihre Wirkung wieder weg. Die Originalsimulation, ihre Parameter, Zufallszahlen, Rohereignisse und Ergebnisse bleiben unverändert. Ohne genehmigte Korrektur werden die bisherigen Ergebnisse objekt- und wertgleich verwendet.

Die abgeleitete Ergebnisversion muss in Kompakt, OE-Analyse, Jobgruppen-Analyse und den Exporten denselben Ereignisstand und Endbestand verwenden. Eine Verschiebung nach dem Zielstichtag ist nicht zulässig. Jahresendbestände zwischen altem und neuem Ereignisdatum ändern sich; am Zielstichtag bleiben Kopf und MAK nur dann gleich, wenn alle Prüfungen bestanden sind. Jahreskosten können sich durch die längere Beschäftigung ändern und dürfen nicht als unverändert ausgegeben werden.

## 1. Fachliche Zulässigkeit

Die erste Ausbaustufe unterstützt ausschließlich einfache `QUIT`-Ereignisse einer bereits im Ausgangsbestand vorhandenen Person. `RETIREMENT`, ATZ, ruhende Personen, Azubis, Zugangsereignisse und Personen mit weiteren zeitabhängigen Ereignissen bleiben gesperrt, bis ihre eigenen Regeln spezifiziert und getestet sind. Ein späterer Ausbau für direkten Renteneintritt braucht eine gesonderte Prüfung des zulässigen Austrittsdatums.

Ein Ereignis ist nur verschiebbar, wenn:

1. genau ein Unternehmensabgang für diese Personalnummer gespeichert ist;
2. Person, Planstellen und Abgang eindeutig derselben OE und Jobgruppe zugeordnet sind;
3. das Ereignis eine belegte Kopfänderung von `-1` und eine endliche, negative MAK-Wirkung besitzt;
4. die MAK-Wirkung dem tatsächlichen MAK-Anteil der Person in dieser OE entspricht; die Summe der Personanteile darf nicht größer als der belegte OE-Bestand sein;
5. das neue Datum nach dem ursprünglichen Datum und spätestens am Zielstichtag liegt;
6. keine andere Regel oder Person denselben Abgang beziehungsweise die Planstelle im Zwischenzeitraum bereits nutzt.

Für die erste Version werden nur gespeicherte Periodenenden als neue Termine angeboten. Eine frei gewählte Tagesgenauigkeit würde zusätzliche Regeln für Monatskosten, Periodenfelder und Zwischenbestände erfordern. Der Nutzer wählt das neue Jahr; die Anwendung zeigt das dazugehörige zulässige Periodenende vor der Prüfung an.

## 2. Abgeleitete Ergebnisversion

- Neue Korrekturart `shift_departure` mit Original-Ereigniskennung, Personalnummer, OE, Jobgruppe, altem und neuem Datum, ursprünglicher Kopf- und MAK-Wirkung sowie Begründung.
- Ereignis in einer Kopie des gespeicherten Abgangsprotokolls verschieben. `event_date` und die zugehörigen Periodenfelder gemeinsam aktualisieren; andere Ereignisse und Personen byte- beziehungsweise wertgleich lassen.
- Den gespeicherten finalen Person- und Planstellenbestand nur dann wiederverwenden, wenn der neue Abgang vor dem Zielstichtag liegt und kein abhängiger Folgezustand existiert. Andernfalls die Korrektur ablehnen statt einen Endbestand zu schätzen.
- OE-, Cluster- und Jobgruppen-Jahresscheiben sowie Ereignis- und Audit-Exporte aus der abgeleiteten Ereignisversion neu aufbauen. Originale Auswahl- und Zufallsprotokolle als Herkunft des Laufs kennzeichnen; sie werden nicht nachträglich umgeschrieben.
- Die Korrektur erhält eine stabile Ergebnisversionskennung aus Ursprungslauf, Person, altem und neuem Termin. Freigabe und Historie speichern genau diese Version. Ein neuer Simulationslauf entzieht wie bisher allen alten Entwürfen die Freigabefähigkeit.

## 3. Prüfungen vor der Freigabe

- Vorjahres-, Zwischenjahres- und Zieljahresbestand für Köpfe und MAK je OE, OE-Cluster, Jobgruppe, JF-Cluster und Gesamt prüfen.
- Für den gewählten Abgang: bis zum alten Datum keine Änderung, zwischen altem und neuem Datum genau der belegte Personenanteil zusätzlich, ab neuem Datum wieder der ursprüngliche Bestand. Am Zielstichtag muss der Endbestand des Originallaufs identisch sein.
- Nie negative Köpfe oder MAK durch die Verschiebung; keine Abgangswirkung größer als die zugeordnete Personen- oder OE-Kapazität. Bei Mehrfachplanstellen die MAK-Anteile summieren und gegen das Ereignis abgleichen.
- OE- und Jobgruppen-Bestandsüberleitungen müssen `Abgestimmt` sein. Der kompakte Endbestand und alle Excel-Exporte müssen mit diesen Überleitungen übereinstimmen.
- Falls Kosten- oder Vergütungsjahresscheiben angezeigt werden, ihre Zwischenjahreswirkung neu berechnen oder die Korrektur für diese Ausgabe sperren. Keine implizite Übernahme unveränderter Jahreskosten.

## 4. Bedienablauf auf „Simulation abnehmen“

Nach der bestehenden Bestandsprüfung bietet die Personentabelle für zulässige Abgänge die Aktion **„Abgang verschieben“**. Nach der Auswahl erscheinen altes Datum, belegte OE-MAK, zulässige neue Jahre und das daraus berechnete Periodenende. Die Eingabe liegt wie die bestehende Checkbox-Auswahl in einem Formular, sodass einzelne Änderungen keinen vollständigen Seitenlauf auslösen.

„Verschiebung prüfen“ erzeugt einen Entwurf. Die Vorschau zeigt in einem Graphen je Jahr Original und Korrektur für Köpfe und MAK der OE; eine Tabelle nennt den Unterschied für alle betroffenen Jahresscheiben. Eine Zusammenfassung bestätigt, ob der Zielbestand unverändert ist. Ändert sich Person oder Termin, wird die Vorschau ungültig und der blaue Freigabeknopf gesperrt. „Lauf unverändert freigeben“ bleibt als separater heller Knopf erhalten.

## 5. Umsetzungsschritte

1. **Eignung und Terminauswahl:** reine Funktionen für Abgangskandidaten, Periodenenden und MAK-/OE-Zuordnung; konkrete Sperrgründe anzeigen.
2. **Ableitung:** neue Funktion neben `derive_retained_departures`, ohne Änderung an `abgaenge/forecast.py` oder `compact_simulation_engine.py`; unveränderliche Originalartefakte und Versionskennung sicherstellen.
3. **Konsistenz:** Ereigniszusammenfassung, OE-/JF-Jahresscheiben und Audit-/Exportdaten aus der abgeleiteten Version erzeugen; alle Prüfungen vor `stage_adjustment` ausführen.
4. **Oberfläche:** Auswahl, Jahr-/Periodenwahl, Entwurf, Jahresgrafik, Differenztabelle und zweigeteilte Freigabe ergänzen.
5. **Tests:** Golden-Case (2016 nach 2017), MAK-Anteil 0,8, Mehrfachplanstelle, zu kleiner OE-Bestand, Zeitgrenze, weiterer Abgang, unveränderter Zielbestand, konsistente OE/JF/Kompakt-Exporte, Freigabewechsel und unveränderter Originallauf mit gleichem Seed.

## Abnahmekriterien

Ein einfacher Abgang kann von einem zulässigen Jahr in ein späteres Jahr innerhalb des Betrachtungszeitraums verschoben werden. Die Jahresgrafik zeigt den zusätzlichen Bestand nur im Zwischenzeitraum. Der Endbestand bleibt identisch; alle OE-, JF- und Kompaktwerte stimmen je Jahresscheibe und im Export überein. Ein Ereignis mit 1,0 MAK kann aus einer OE mit nur 0,8 MAK belegter Kapazität nicht verschoben werden. Ohne Korrektur bleiben sämtliche gespeicherten Originalwerte unverändert.
