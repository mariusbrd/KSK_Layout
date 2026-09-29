# Dashboard Test Routine

Dieses Dokument ordnet die vorhandenen Tests nach Pruefziel. Die Dateien werden
nicht umbenannt; die Reihenfolge wird ueber `tests/run_app_validation.py`
orchestriert.

## Profile

```powershell
py -3 -B tests/run_app_validation.py --profile smoke
py -3 -B tests/run_app_validation.py --profile standard
py -3 -B tests/run_app_validation.py --profile full
```

`smoke` prueft schnelle App-Vertraege, Seitenvertraege, Exporte und Lineage.
`standard` ist die empfohlene Routine vor Push/Merge.
`full` ergaenzt schwere Shadow-, Performance- und Deep-Validation-Tests.
Der Runner deaktiviert den Pytest-Cache standardmaessig, damit der Projekt-Root
sauber bleibt.

Zusaetzliche pytest-Argumente koennen nach `--` uebergeben werden:

```powershell
py -3 -B tests/run_app_validation.py --profile standard -- -q -x
```

## Reihenfolge

1. `01_static_app_contracts`
   App-Struktur, Navigation, Text-Encoding, Upload-Templates und Source-Service.

2. `02_input_data_and_clusters`
   Input-Schemas, Loader, Datenintegritaet, Clusterquellen und Fallbacks.

3. `03_core_calculation_engines`
   MAK, Soll-Ist-Koepfe, Verguetung, Exklusionen, Matrizen und Kern-KPIs.

4. `04_page_contracts`
   Seitenlogik, Filter, Sortierung, Chart-/Tabelleninventar und i18n-Vertraege.

5. `05_lineage_and_exports`
   Lineage-Registry, Transformationen, Input-Lineage, Glossar und Excel-Exporte.

6. `06_simulation_and_forecasts`
   Simulationsparameter, Abgaenge, Zugaenge, Hybridlogik und Szenario-Paritaet.

7. `07_performance_and_shadow_checks`
   Cache-/Performance-Pruefungen und tiefe MAK-Shadow-Validierungen.

## Interpretation

Wenn eine fruehe Phase fehlschlaegt, sind spaetere Fehler oft Folgefehler. Die
Routine stoppt deshalb standardmaessig nach der ersten fehlerhaften Phase. Mit
`--continue-on-fail` kann ein vollstaendiger Fehlerueberblick erzeugt werden.

Nach jedem Lauf wird eine JSON-Zusammenfassung unter `tests/_results/` geschrieben.
Dieser Ordner ist bewusst nicht versioniert.

## Manuelle Skripte

Dateien mit Prefix `analyse_`, `check_`, `debug_`, `repro_`, `trace_`,
`validate_`, `verify_`, `benchmark_` und `run_compact_` bleiben als fachliche
Spezialnachweise erhalten. Sie sind nicht Teil der Standardroutine, ausser ihre
Logik wurde bereits in `test_*.py` ueberfuehrt.
