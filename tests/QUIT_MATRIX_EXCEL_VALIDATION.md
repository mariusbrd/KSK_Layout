# Kündigungsmatrix: Excel-Erweiterung

Ausgangscommit: `b0046ce`, Branch `feature/kuendigungsquoten-optimierung`.

## Umfang und unveränderte Logik

Die beiden Seiten erhalten denselben Excel-Eingabeweg am bisherigen Matrixeditor.
Die Prognose-Engine, Priorität von JF-Cluster/Jobfamily, Altersgrenzen, jährliche
Wahrscheinlichkeiten, Ausschlüsse und getrennten Parameterquellen bleiben erhalten.
Die Original-Personaldaten und gespeicherten Dashboard-Einstellungen werden nicht
geändert. Nur vollständig validierte Dateien lassen sich ausdrücklich übernehmen.

Eine gezielte Korrektur betrifft die Prozentumrechnung: Die beiden Seiten markieren
ihre bekannten Prozentwerte ausdrücklich. Damit bedeutet auch eine ausschließlich
mit `0,5` gefüllte Matrix 0,5 % und nicht 50 %. Der Marker wird vor der Engine entfernt.
Andere Aufrufer ohne Marker behalten exakt die bisherige Skalenerkennung.

## Nachweise

- Originalfunktion `build_params_from_ui` direkt aus `b0046ce`: Vergleich über
  beide Dimensionen, aktivierte/deaktivierte Matrix, einheitliche und unterschiedliche
  Altersquoten sowie Grenzwerte. Bestehende, eindeutige Eingaben ergeben identische Parameter.
- Vollständige Prognose-DataFrames: manuelle Matrix gegen exportierte/importierte
  Matrix über beide Dimensionen, vier Quotenprofile und zwei Zufallsseeds.
- Excel-Rundlauf: einfache und detaillierte Vorlagen, beide Seitenformate
  (Index bzw. Gruppenspalte), führende Nullen, 0 %, 0,5 % und 100 %.
- Teilimport: übrige Gruppen unverändert; einheitliche Quote auf alle vier Altersgruppen.
- Validierung: unbekannte/doppelte/leere Gruppen, ungültige Quoten, leere Dateien,
  falsche Dimension, gemischte Formate, fehlendes Tabellenblatt und unlesbare Dateien.
- Deutsche Dezimalzahlen und Excel-Prozentformat werden eindeutig verarbeitet.
- Streamlit AppTest: Vorschau ohne Übernahme, ausdrückliches Übernehmen, wiederholter
  Upload, manuelle Nachbearbeitung, Speichern, Seitenwechsel, Dimensionswechsel,
  deaktivierte Matrix und Ablehnung ungültiger Dateien.
- Der echte Simulationsparameter-Matrixeditor wird ohne Upload gegen die ursprüngliche
  Seitenfunktion aus `b0046ce` verglichen.

AppTest bietet keine direkte Upload-/Editor-Bedienung. Uploadbytes werden im
Testadapter eingespeist; Editoränderungen werden als Browser-Widgetzustand übergeben.
Die Buttons, Berechnungsparameter und Streamlit-Neuberechnungen verwenden den echten Runtime-Pfad.

## Bestehende Abweichungen in der Standard-Testreihe

Die gesamte Standardroutine wurde mit `--continue-on-fail` ausgeführt. Folgende
Abweichungen betreffen unveränderte Daten-/Konfigurationspfade und wurden getrennt
mit der ursprünglichen Parameterumrechnung reproduziert bzw. bereits vor der
Featureänderung festgestellt:

1. `test_data_prep_regression.py::test_load_and_prepare_data_matches_reference_pipeline_and_golden_master`
2. `test_data_prep_regression.py::test_prepare_compact_data_matches_golden_master`
3. `test_soll_ist_koepfe_engine.py::test_soll_ist_koepfe_engine_matches_current_deep_dive_settings`
4. `test_abgaenge_golden_master.py::test_run_forecast_abgaenge_matches_golden_master`
5. `test_zugaenge_golden_master.py::test_page4_zugaenge_scenario_inputs_match_golden_fixture`

Die ersten beiden Vergleiche weichen von den gespeicherten Hashes ab. Der
Soll-Ist-Test setzt `exclusions.vorstand = true` voraus; gespeichert ist `false`.
Die Prognose-Golden-Master zeigen unter anderem abweichende Jobfamily-Werte
(`Sonstiges` gegenüber `Keine Job Family`). Referenzdaten und Einstellungen wurden
nicht angepasst, um diese Abweichungen zu verdecken.

Logs und maschinenlesbare Zusammenfassungen liegen lokal unter `tests/_results/`
(nicht versioniert). Nach Korrektur der Testadapter wurden die betroffenen
Oberflächen-/Seitenprüfungen erneut ausgeführt.

Die fokussierte Prüfung der Erweiterung und bestehenden Matrix-/Kündigungs-/Parameter-
Verträge bestand mit 127 Tests. Der abschließende Featurelauf enthält zusätzlich
Excel-Rundungs- und numerische Organisationsnummern-Szenarien. Die Seitenphase der
Standardroutine bestand bei der Wiederholung vollständig. Die fünf oben genannten
Ausgangsabweichungen bleiben offen.
