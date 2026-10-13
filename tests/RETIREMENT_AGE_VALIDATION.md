# Dynamische Rentenaltersgrenzen: Umsetzung und Ergebnisvergleich

Branch: `feature/kuendigungsquoten-optimierung`.
Referenz der unveränderten Prognose-Engine: Commit `b0046ce`.
Die zuvor implementierte Excel-Kündigungsmatrix bleibt erhalten.

## Minimaler Eingriff

- Zwei neue Felder im bestehenden `retirement`-Block: `retirement_age` (65) und
  `early_retirement_age` (60). Der obere Frühverrentungsrand wird automatisch abgeleitet.
- Ein gemeinsamer Resolver validiert ganze Jahre und die Reihenfolge der Grenzen.
- In der Engine wird eine bestehende Altersauswahl auf die Parameter umgestellt.
  Zufallsströme, Altersermittlung, Periodenumrechnung, Reihenfolge der Ereignisse,
  Rentenquoten und ATZ-Vertragsenden bleiben unverändert.
- Beide Seiten verwenden dieselben Altersfelder; bestehende Quotenregler behalten
  ihre Skala, Schlüssel und stabile Beschriftungen. Die dynamische Zusammenfassung
  erläutert Bereiche und Prozentwerte. Das verhindert Widget-Resets durch wechselnde Labels.
- Neue Felder laufen durch bestehende Parameteraufbereitung, Sitzungsstände,
  Berechnungs-/Speicherbuttons und CompactPlus-Weitergabe. Cache-Signaturen und
  Parameterexport erfassen bereits den ganzen Block und benötigen keinen Sonderweg.
- Der bisherige `65plus_Audit` verwendet die gewählte Rentengrenze. Technische
  Tabellen-/Prüfschlüssel bleiben erhalten; Export-Readme und sichtbare Fachhinweise
  erläutern die tatsächliche Grenze.

## Vorher und nachher

Vor Änderung der Produktivlogik bestand die Ausgangsprüfung mit **113 Tests**.
Die Referenzfunktion wird für Vergleiche direkt aus dem Ausgangscommit geladen;
sie wird nicht aus der neuen Implementierung nachgebaut.

**112 Äquivalenzszenarien** kombinieren:

- fehlende neue Altersfelder oder ausdrücklich gesetzte 65/60;
- Monats-/Quartalsberechnung;
- Zufallsseeds 7 und 42;
- 12 und 36 Monate;
- sieben Quoten-/Komponentenszenarien: übliche Quoten, null, hundert Prozent,
  nur reguläre Rente, nur Frühverrentung, Kombination mit Kündigung und ATZ,
  deaktivierte Rentenkomponente.

Verglichen werden alle Ergebnis-DataFrames exakt, einschließlich Typen und Reihenfolge:
Personenereignisse, Ereigniszeitpunkte, Abgangsgründe, Köpfe, MAK, KPI-Zeitreihen,
ATZ-Pivot und sämtliche Teiltabellen. Die Annahmen sind bei identischen Eingaben
ebenfalls identisch. Zwei zusätzliche Standardfelder in neu aufbereiteten
Parameterblöcken sind eine beabsichtigte Schemaerweiterung, keine Ergebnisänderung.

## Neue Altersgrenzen und vollständiger Parameterweg

- Grenzen 67/62, 63/58 und 70/65: Personen knapp unter, genau auf und knapp über
  den Grenzen; beide Frequenzen; Quoten 0/100 %, reguläre/Frührente getrennt.
- Wechsel in den regulären Rentenbereich während der Prognose.
- ATZ-Vertragsende separat; keine doppelte Verarbeitung als reguläre Rente.
- Ungültige Grenzen werden in Oberfläche, Parameteraufbereitung und Engine abgewiesen.
- Echte Streamlit-Seiten mit synthetischer Datenbasis: ändern, berechnen/speichern,
  Parametersätze und Annahmen prüfen, Seite verlassen und zurückkehren.
- Rentenquoten bleiben beim Ändern der Altersgrenzen erhalten.
- Zentrale Speicherung und Weitergabe an CompactPlus; Cache-Signaturen unterscheiden
  sich bei Änderung jeder der beiden Altersgrenzen.
- CompactPlus-Paritätsprüfung für 67/62 über 12 und 36 Monate.
- Parametertabelle und tatsächlicher Excel-Export enthalten beide Alterswerte;
  Rentengrenzen-Audit und Readme beziehen sich auf die gewählte Grenze.
- Audit mit fehlenden bzw. expliziten 65/60 bleibt exakt identisch zur Originalfunktion.
- Regression der bereits ergänzten Excel-Kündigungsmatrix.

## Bestehende Vergleichsdaten

Die fünf bekannten Ausgangsabweichungen der Standardroutine sind in
`QUIT_MATRIX_EXCEL_VALIDATION.md` dokumentiert. Golden-Master-Dateien und gespeicherte
Dashboard-Einstellungen werden nicht angepasst. Der bestehende Abgänge-Golden-Master-
Test prüft die beiden neuen Standardfelder ausdrücklich und vergleicht danach weiterhin
alle bisherigen Annahmen und Ergebnis-DataFrames gegen die unveränderte Fixture.

Die lokalen Testlogs und Zusammenfassungen liegen unter `tests/_results/`
(nicht versioniert).

## Abschließendes Ergebnis

- **295 fokussierte Tests bestanden**, einschließlich der 112 exakten
  Originalcode-Vergleiche, neuen Altersgrenzen, tatsächlichen Seiten, CompactPlus,
  Exporten, Workshop-Validierung und Excel-Kündigungsmatrix.
- Die gesamte Standardroutine wurde ausgeführt. Der Lokalisierungstest wurde auf
  die beabsichtigten neuen Beschriftungen und Altersfelder erweitert. Die Code-
  Prüfsummen ausschließlich der zwei geänderten Exportfunktionen wurden nach
  erfolgreicher fachlicher Validierung aktualisiert; sie sind keine Ergebnisreferenzen.
- Nach diesen Anpassungen bestanden die vollständigen Seiten- sowie Lineage-/Export-
  Phasen bei der Wiederholung. Es verbleiben ausschließlich die fünf bereits
  dokumentierten Daten-/Konfigurationsabweichungen.
- `git diff --check` ist sauber. Originaldaten, Golden-Master-Dateien und
  `config/user_settings.json` wurden nicht geändert.
