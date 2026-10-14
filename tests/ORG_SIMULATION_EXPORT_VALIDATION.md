# OE-Simulation Gesamtexport: Validierung

Branch `Exportfunktion-OrgSimulation`; Ausgangscode `ee8425e`.

## Wiederverwendung und Eingriff

Die vorhandenen OE-Renderfunktionen nehmen optional einen Tabellen-Sammler entgegen.
Der Sammler erhält Kopien der bereits erzeugten Aggregationen. Berechnungen, Filter,
Rangfolge, Diagramme und Einzeldownloads werden nicht ersetzt. Die normale IST-Seite
aktiviert den Sammler nicht und erhält keinen neuen Gesamtexport.

Eine neue Exportfunktion übernimmt die OE-spezifische Zusammenstellung. Parameter-
Tabellen, Lineage-Berichte, Excel-Formatierungshilfen und der verzögerte Download
werden wiederverwendet. Das SOLL verwendet `get_soll_mak`, IST-Kapazität und Köpfe
die vorhandenen Kompakt-Kennzahlenfunktionen. Simulations-Engine und Originaldaten
werden nicht verändert oder erneut geladen.

## Daten und Betrachtungsräume

- KPI-Anzeigen/Datenqualität beziehen sich auf den gesamten gefilterten Bestand.
- Rangliste, Zusammensetzungen, Tarifgruppen, Kapazitätsübersicht und SOLL-Vergleich
  verwenden die tatsächliche gemeinsame OE-Anzeigeliste, einschließlich ihrer Reihenfolge.
- Diagrammquellen werden numerisch exportiert; keine Bildkopien. KPI-Werte bleiben
  zusätzlich in ihrer angezeigten Form dokumentiert.
- Vergleichstabellen exportieren die bestehenden IST-/Simulations-/Delta-Werte.
- SOLL-MAK werden unverändert aus dem gefilterten gespeicherten Ausgangs-Snapshot
  übernommen. Fehlende Ausgangsbasis oder SOLL-Spalten bleiben als nicht verfügbar erkennbar.
- Kopfzahl, MAK und Ausgangs-Planstellenzeilen sind unterschiedliche Größen.
- Zusätzliche gespeicherte Basiszeilen erlauben die Rückverfolgung von Kapazitäten.
- Laufparameter, Metadaten und Laufeinstellungen stammen aus den gespeicherten
  CompactPlus-Zuständen, niemals aus später geänderten Parameterentwürfen.

## Tests

`test_org_simulation_export.py` prüft:

1. Original-Renderfunktionen direkt aus `ee8425e` gegenüber der Erweiterung mit
   aktiviertem Sammler: gleiche Plotly-Daten, Tabellen und Einzelexport-DataFrames.
   Szenarien: Köpfe/MAK, Vergleich an/aus, Rangliste, Geschlecht und Tarifgruppen.
2. Unveränderte formatierte Kapazitätsübersicht bei zusätzlichen numerischen Exportwerten.
3. SOLL aus gespeichertem Ausgangsbestand; verschwundene OEs; fehlende Ausgangsbasis/SOLL.
4. Mehrfachplanstellen: zwei Planstellenzeilen ergeben einen Kopf und additive SOLL-MAK.
5. Vollständiger Hash: Änderungen mitten in 1.000 Zeilen, kleine Float-Unterschiede
   sowie Änderungen von Auswahl/Vergleich invalidieren den Export.
6. Wrapper friert die gespeicherten Parameter ein; aktuelle Entwürfe und spätere
   Änderungen am Sitzungssatz beeinflussen den eingefrorenen Exportkontext nicht.
7. Excel-Inventar, numerische Tabellen, Laufparameter und alle drei Herkunftsblätter.
8. Tatsächliche Streamlit-Seite: beide Kennzahlen, Vergleichsschalter, nur im IST
   vorhandene OEs, Top-N/Alle, Mindestgröße ohne Treffer und leerer Simulationsausschnitt.

Bestehende OE-, Jobgruppen-, Einzelexport-, Download-, Transformations- und Lineage-
Verträge werden zusätzlich ausgeführt. Die Standard-Testreihe prüft die gesamte App.
Code-Prüfsummen wurden ausschließlich für geänderte OE-Funktionen und neue
registrierte Export-/SOLL-Bezüge aktualisiert; die Ergebnisreferenzen bleiben unverändert.

## Grenze des historischen Herkunftsnachweises

Die vorhandenen Sitzungsstände speichern Ausgangs-/Future-Snapshots und Laufparameter,
aber keine vollständige historische Kopie aller Eingabedateien und des damaligen Codes.
Der Export dokumentiert diesen Unterschied ausdrücklich. Input-/Code-Lineage kann
aktuell zugängliche Quellen beschreiben; sie beweist keine nicht gespeicherte historische
Dateiversion. Es werden keine aktuellen Dateien als damalige Simulationsgrundlage ausgegeben.

Bekannte Ausgangsabweichungen der Standardroutine sind weiterhin in
`QUIT_MATRIX_EXCEL_VALIDATION.md` und `RETIREMENT_AGE_VALIDATION.md` beschrieben.
Lokale Logs und JSON-Zusammenfassungen liegen unter `tests/_results/` (nicht versioniert).

## Abschließende Ergebnisse

- 15 neue Feature-Szenarien bestanden.
- 61 fokussierte OE-/Export-/Download-/Transformations-/Code-Lineage-Tests bestanden.
- 66 Tests der vollständigen Lineage-/Exportphase bestanden nach Erweiterung ihrer
  statischen Registererwartungen um den neuen Eintrag und sieben zusätzliche Codebezüge.
- Die gesamte Standardroutine wurde ausgeführt; die Seitenphase bestand. Es bleiben
  die fünf bekannten Daten-/Konfigurationsabweichungen. Golden-Master-Dateien und
  gespeicherte Dashboard-Einstellungen wurden nicht verändert.
- Ein zusätzlicher Phasenrunner scheiterte erst beim Schreiben seiner JSON-Zusammenfassung
  mit `PermissionError`; alle 66 Tests waren bestanden. Die Phase wurde anschließend
  direkt mit pytest wiederholt und erfolgreich mit Exitcode 0 abgeschlossen.
- `git diff --check` ist sauber. Änderungen sind lokal; kein Commit/Push durch diese Umsetzung.
