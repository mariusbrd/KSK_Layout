# Monte-Carlo-Erweiterung der Personalsimulation – Projekt-Todoliste

Stand: 30.10.2026 · Branch: `feature/monte-carlo-simulation` · Ausgangscommit: `63a6ef5`

Diese Datei ist der dauerhafte Arbeitsstand des Projekts. Nach **jeder Phase** werden erledigte Punkte abgehakt, Messwerte und Abweichungen eingetragen, Entscheidungen mit Datum begründet und die nächsten Schritte aktualisiert. Offene Punkte bleiben sichtbar. Eine Phase gilt erst als abgeschlossen, wenn ihre Nachweise verlinkt sind.

## Ziel und verbindliche Vorgaben

- [ ] **Einzellauf-Parität:** Bei einem Durchgang müssen gleicher Eingabedatensatz, Basis- und Zielstichtag, Parameter, Einstellungen, Cluster- und Kostenquellen sowie gleicher Seed exakt die bisherigen Ergebnisse erzeugen. Dies umfasst Personen- und Planstellen-Snapshots, Ereignisse, Köpfe, MAK, Kosten, Audits, Freigabe, OE-/Jobgruppenansichten und Exporte. Der bisherige Ausführungspfad bleibt bis zum bestandenen Paritätsnachweis verfügbar.
- [ ] **Mehrlauf-Parität:** Für jeden Seed eines Monte-Carlo-Durchgangs müssen dessen fachliche Ergebnisse mit einem vollständigen Referenzlauf desselben Seeds übereinstimmen. Unterschiede durch eine schnellere Implementierung sind Fehler, keine zulässige Annäherung.
- [ ] **Jahresscheiben-Parität:** Für denselben Seed müssen OE, OE-Cluster, Jobfamily und JF-Cluster in jeder Jahresscheibe deckungsgleich bleiben: Ereignisart, Köpfe-/MAK-Veränderung, Jahres- und kumulierter Bestand, technische Effekte, Endwertabgleich sowie die dazugehörigen Excel-Tabellen. Bei Läufen mit Zugängen bleibt die heute geltende Nichtverfügbarkeit zertifizierter Jahresbestände erhalten, bis eine eigene fachliche Erweiterung ausdrücklich spezifiziert und geprüft ist.
- [ ] **Leistungsziel:** 10.000 Durchgänge mit den definierten Ergebnisdimensionen in höchstens 20 Sekunden auf festgelegter Zielhardware und einem festgelegten Referenzszenario. Laufzeit umfasst Vorbereitung, Berechnung und Aggregation; UI-/Exportzeit wird zusätzlich separat gemessen. Ziel ist bis zur Messung eine Hypothese, keine Zusage.
- [ ] **Statistik:** Bei mehr als einem Durchgang Mittelwerte und empirische Quantilbänder nur aus je Lauf korrekt berechneten Kennzahlen bilden. Bänder als Prognose-/Szenariobänder bezeichnen, nicht als Konfidenzintervalle des Mittelwerts. Köpfe im Einzellauf ganzzahlig lassen; Erwartungswerte dürfen Dezimalstellen haben.
- [ ] **Fachliche Kohärenz:** Kein gemittelter Personen-Snapshot. Personenlisten, Ereignisprüfungen und manuelle Abgangskorrekturen bleiben an einen konkreten Lauf gebunden. OE- und Jobgruppenkennzahlen samt Filtern, Mehrfachzuordnungen und Quoten werden zunächst je Lauf berechnet und erst danach aggregiert.
- [ ] **Nachvollziehbarkeit:** Daten-/Parameterfingerabdruck, Code-/Umgebungsversion, Seed-Ableitung, Zahl angeforderter/erfolgreicher/verworfener Läufe, Laufzeiten und Banddefinition speichern. Ein fehlgeschlagener Lauf darf nicht stillschweigend aus dem Ensemble verschwinden.
- [ ] **Keine Vermischung:** Einzellauf- und Ensemble-Ergebnisse, Freigaben, Cache-Einträge und Exporte eindeutig versionieren. Die heutige Abnahme und Justierung des Originallaufs darf nicht unbemerkt auf das Ensemble übertragen werden.

## Gesicherte Ausgangslage

- Nutzerbeobachtung: derzeit ungefähr **7 Sekunden** für einen Dashboard-Durchlauf. Messumgebung und aktives Szenario müssen noch dokumentiert werden.
- Rechnerische Zielgröße: 10.000 × 7 s = 70.000 s (ca. 19,4 h). Für 20 s sind im Mittel 2 ms je Lauf erforderlich; nach einem 7-s-Referenzlauf bleiben etwa 1,3 ms je zusätzlichem Lauf.
- Read-only-Profiling am 30.10.2026 mit `py -u tests/profile_simulation_after_8d.py`: 1.198 Personen, 1.662 Snapshot-Zeilen, 77 ATZ-Einträge, ein Jahr, Standard-Zugangsparameter. Gemessene Engine-Schritte: erster Lauf 4,20 s, warmer Audit-Cache 1,72 s. Erster Lauf: Audit 2,31 s (davon MAK-Lineage 1,91 s), Finalisierung 0,76 s, Abgänge 0,55 s, Aktualisierung bestehender Zeilen 0,45 s. Profiling-Skript bildet weder alle Streamlit-Schritte noch die aktuelle 7-s-Produktivkonfiguration ab.
- Schon die unveränderte Abgangsprognose würde mit 0,55 s × 10.000 etwa 92 Minuten beanspruchen. Audit-Verzicht allein erfüllt das 20-s-Ziel nicht.
- Aktiver Pfad: `pages/7_⚡_Kompakt_plus_Simulation.py` → `dataloader/compact_simulation_engine.py` → `abgaenge/forecast.py` und `zugaenge/forecast.py` → finaler Snapshot/Audits → `utils/org_simulation_events.py` → Abnahme und Folgeanalysen.
- `utils/simulation.py::run_monte_carlo` gehört zum archivierten, anderen Simulationsmodell. Es ist kein fachlich äquivalenter Ersatz für CompactPlus.
- OE- und Jobgruppen-Jahresbestandsüberleitungen sind derzeit nur für Läufe mit ausdrücklich deaktivierten Zugängen zertifiziert (`utils/org_simulation_reconciliation.py`, `components/jobfamily_simulation_yearly.py`). Ein Band für Jahresendbestände mit Zugängen benötigt eine gesonderte fachliche Lösung.
- Der aktuelle Einzellauf speichert umfangreiche DataFrames in `st.session_state`; `_build_simulation_audit_tables` hat einen `st.cache_data`-Cache mit maximal acht Einträgen. 10.000 vollständige Laufobjekte dürfen nicht im Sitzungsspeicher gesammelt werden.

## Phase 0 – Mess- und Referenzvertrag

- [ ] Zielhardware, Python-/Pandas-/NumPy-/Streamlit-Versionen, reale Datengröße, Zielhorizont und aktive Parameter des beobachteten 7-s-Laufs dokumentieren.
- [ ] Dashboard-Lauf vollständig profilieren: Laden/Normalisieren, Personenbasis, Abgänge je Komponente, Ereignisübernahme/Vakanzen, Zugänge je Komponente, Snapshot-Aktualisierung, Finalisierung, Audit, Ereignisüberleitung, Aufbereitung, UI. Kalt/warm und mit/ohne Zugänge getrennt messen; RAM-Spitzen erfassen.
- [ ] Golden-Master-Szenarien aus unverändertem Code sichern: Nullhorizont, Standardlauf, ATZ, Renten- und Kündigungsmatrix, Ruhend, Azubi/Trainee/Neueinstellungen, Vakanznachbesetzung, Mehrfachplanstellen, Ausschlüsse, Clusterwechsel, Gehaltsautomatik, Teiljahr. Mehrere Seeds und Eingabereihenfolgen erfassen.
- [ ] Referenzdateien für **alle OE- und JF-Jahresscheiben** vor jeder Optimierung einfrieren: Rohereignisse, `annual_oe`, `annual_cluster`, OE-Bestandsüberleitung `annual` samt Status/Checks, JF- und JF-Cluster-`annual`/`stock`/`stock_checks`, ausgewählte Jahresansichten und Excel-Tabellen. Schlüssel und Zeilenreihenfolge der Referenz festhalten. **Teilstand:** Roh-/Jahrestabellen und Bestandsprüfungen für sieben synthetische Szenarien, ausgewählte OE-/JF-Excel-Quelltabellen für drei Szenarien und vier Zusatzseed-Läufe eingefroren. Vollständige UI-Ansichten, produktive Daten und weitere Spezialfälle bleiben offen.
- [ ] Vergleichsregel pro Artefakt festlegen: exakte Schema-/Zeilen-/Wertparität einschließlich Zufallsereignissen; bei Gleitkommawerten zunächst bit-/wertgleiche Forderung prüfen und jede technisch unvermeidliche Toleranz ausdrücklich begründen. Keine Toleranz stillschweigend einführen.
- [ ] Bestehende Tests und Export-/Freigabeverträge als Regressionsbasis inventarisieren; Referenzartefakte unveränderlich kennzeichnen.
- **Gate:** reproduzierbare Profilwerte und unveränderliche Referenzoutputs liegen vor. Ohne dieses Gate keine Beschleunigungsänderung.

## Phase 1 – Ergebnis- und Statistikvertrag

- [ ] Kennzahlenkatalog für CompactPlus, OE und Jobgruppen erstellen: additive Bestände/Flüsse, Quoten, Kosten, Ranglisten, Ereignisarten, Filterebenen und Zeitpunkte. Pro Kennzahl angeben, ob Mittelwert/Band sinnvoll ist.
- [ ] Bandniveau und Berechnung festlegen (z. B. P10–P90, optional P5–P95); Stichprobenquantile, Rundung und Darstellung von null oder seltenen Ereignissen spezifizieren.
- [ ] Pro Durchgang zuerst fachlich vollständige Kennzahlen je gewählter Dimension berechnen. Quoten und Kosten pro Kopf/MAK je Durchgang bilden; Quantile niemals aus bereits aggregierten Gruppen oder addierten Quantilgrenzen ableiten.
- [ ] Stabile Gruppenmenge über alle Läufe definieren, einschließlich Gruppen ohne Ereignis/Bestand in einzelnen Läufen; deren Wert je nach Kennzahl explizit als 0 oder nicht definiert behandeln.
- [ ] Umgang mit Freigabe, einem konkreten Referenzlauf, manuellen Abgangskorrekturen und fehlgeschlagenen Läufen fachlich entscheiden. Ensemble-Mittelwert ist kein freigabefähiger Personenbestand.
- [ ] Jahresbestandsverläufe bei aktivierten Zugängen separat spezifizieren und je Lauf gegen den Zielbestand prüfen; bis dahin nicht als zertifizierte Bänder anbieten.
- **Gate:** prüfbare Beispiele für Mittelwerte, Quantile, Filter, Mehrfachzuordnungen und Quoten sind fachlich abgenommen.

## Phase 2 – Schlanker Zusatzlauf bei unverändertem Einzellauf

- [ ] Unveränderliche Eingaben einmal pro Ensemble vorbereiten; Reihenfolge, Datentypen und Kopiersemantik des Referenzlaufs dokumentieren.
- [ ] Zusatzläufe ohne vollständige Anzeige-Audits, Lineage-Tabellen und Excel-Artefakte prototypisieren. Nur für die Bandberechnung erforderliche, prüfbare End- und Jahreskennzahlen behalten.
- [ ] Für jeden Zusatzlauf fachliche Gleichheit mit einem vollständigen Referenzlauf desselben Seeds nachweisen; erforderliche Prüfungen dürfen nicht entfallen, nur anders/kompakter ermittelt werden.
- [ ] Seed-Plan definieren: Durchgang 0 verwendet unverändert die heutigen Abgangs- und Zugangsseeds; weitere Durchgänge erhalten stabile, dokumentierte Seeds ohne Kollision. Die Zahl der Durchgänge darf Durchgang 0 nicht beeinflussen.
- [ ] 1, 10, 100 und 1.000 Durchgänge messen: Zeit je Stufe, Speicher, Cache-Verhalten, Ergebnisgleichheit. Größte verbleibende Kosten quantifizieren.
- **Gate:** Einzellauf vollständig identisch; Zusatzläufe ergebnisgleich; gemessener Speedup und verbleibender Abstand zum 20-s-Ziel dokumentiert.

## Phase 3 – Hochleistungs-Rechenkern nur bei belegtem Bedarf

- [ ] Auf Basis von Phase 2 entscheiden, welche Teile der monatlichen Abgangs-/Zugangslogik in kompakte Arrays oder kompilierte Funktionen überführt werden müssen. Vorher keine pauschale Neuschreibung.
- [ ] Ereignisreihenfolge, berechtigte Personen je Monat, Sortierung, Zufallsziehung, Rundung und Vakanzverbrauch als ausführbaren Paritätsvertrag festhalten.
- [ ] Batch-Verarbeitung vieler Seeds prototypisieren; dynamische Abhängigkeiten zwischen Abgängen, Vakanzen und Zugängen ausdrücklich abbilden. Parität für jede aktive Komponente und ihre Kombination nachweisen.
- [ ] Aggregation inkrementell vornehmen; Arbeitsspeicher unabhängig von 10.000 vollständigen Snapshots halten. Referenzlauf und gegebenenfalls einzelne Prüfläufe vollständig rekonstruierbar lassen.
- [ ] 10.000 Läufe auf Zielhardware und Referenzszenarien messen; Median und wiederholte Messungen, Kalt-/Warmstart, RAM-Spitze und CPU-Auslastung berichten.
- **Gate:** Ergebnisparität für alle Golden Masters und Leistungsziel nach reproduzierbarer Messung erreicht. Falls nicht: Engpass und realistische Grenze offen dokumentieren, keine fachlich abweichende „schnelle“ Simulation als identisch ausgeben.

## Phase 4 – Integration, Abnahme und Exporte

- [ ] Durchgangszahl, Bandniveau, Fortschritt, Abbruchverhalten und Ergebnisstatus in CompactPlus integrieren; `1` führt nachweislich zum unveränderten bisherigen Anzeige- und Exportpfad.
- [ ] OE- und Jobgruppenanalyse um Ensemble-Kennzahlen und Bänder ergänzen, ohne den personenbezogenen Referenzlauf mit Erwartungswerten zu vermischen. Filter und Gruppenzuordnungen je Lauf korrekt anwenden.
- [ ] Jahresereignisse und zulässige Jahresbestände je Lauf aggregieren; bei nicht zertifizierbarem Bestand klaren Status statt eines scheinbar exakten Bandes anzeigen.
- [ ] Ensemble-Metadaten, Prüfstatus, Daten-/Code-Fingerabdruck und Ergebnisversion im Export dokumentieren. Einzellauf- und Ensemble-Exporte klar trennen.
- [ ] Kompatibilität der bestehenden Abnahme, Justierung, Cache-Invalidierung und Navigation prüfen. Neuberechnung invalidiert alte Freigaben entsprechend dem gewählten Ergebnisobjekt.
- [ ] Vollständige Standard-Test-Routine, Golden-Master-Parität, fachliche Ensemble-Tests und Performance-Gate ausführen.
- **Gate:** bei `N=1` keine fachliche/visuelle/Export-Regression; bei `N>1` konsistente Bänder über CompactPlus, OE und Jobgruppen; Leistungsziel nachvollziehbar geprüft.

## Entscheidungen und offene Fragen

| Datum | Status | Thema | Entscheidung / erforderlicher Nachweis |
| --- | --- | --- | --- |
| 30.10.2026 | Festgelegt | Einzellauf | Bestehende Ergebnisse müssen 1:1 reproduzierbar bleiben. |
| 30.10.2026 | Ziel | Leistung | 10.000 Durchgänge in maximal 20 s; Zielhardware und Ergebnisscope noch festzulegen. |
| 30.10.2026 | Offen | Bänder | Standardniveau, zusätzliche Bandniveaus und Quantildefinition fachlich festlegen. |
| 30.10.2026 | Offen | Freigabe | Ensemble-Freigabe und Behandlung manueller Korrekturen spezifizieren. |
| 30.10.2026 | Offen | Zugänge | Zertifizierte Jahresbestände mit Zugängen sind derzeit nicht verfügbar. |
| 30.10.2026 | Offen | Betrieb | Wo 10.000 Läufe gerechnet werden sollen und welche gleichzeitige Nutzerzahl zu unterstützen ist. |

## Laufendes Arbeitsprotokoll

### 30.10.2026 – Planungsstart und Bestandsprofil

- Ausgangsbranch und Commit geprüft; Arbeitsbaum vor Anlage dieser Datei sauber.
- Read-only-Profiling des vorhandenen Skripts ausgeführt. Zahlen und Grenzen stehen unter „Gesicherte Ausgangslage“.
- Kernbefund: Audit/Lineage ist der größte Kaltlauf-Block; danach bleiben Abgangsprognose, Snapshot-Aktualisierung und Finalisierung. Audit-Verzicht allein kann das 20-s-Ziel nicht erfüllen.
- Nächster Schritt: Phase 0 mit repräsentativem Dashboard-Lauf und Golden-Master-Vertrag.

### 30.10.2026 – Phase 0 begonnen: Jahresscheiben und Testbasis

- Nutzerforderung ergänzt: OE- und JF-Jahresscheiben müssen bei gleichem Seed **vollständig deckungsgleich** bleiben. Dazu gehören ausdrücklich Zwischenjahre und Exporte, nicht nur Zielwerte.
- Lokale Umgebung: Windows 11, Python 3.13.5, pandas 2.3.3, NumPy 2.4.1, Streamlit 1.53.1. Zielserver und Konfiguration des beobachteten 7-s-Laufs sind noch unbekannt.
- Vorhandene Referenzbasis inventarisiert: `tests/test_org_yearly_reference.py` friert sieben synthetische vollständige Simulationsoutputs ein, derzeit aber keine vollständigen OE-/JF-Jahresscheiben. `tests/test_jobfamily_simulation_yearly.py` und `tests/test_org_simulation_yearly.py` prüfen ausgewählte Jahres- und Exportregeln; sie ersetzen keinen vollständigen Golden Master.
- Gezielter Ausgangstest (`py -m pytest -q tests/test_org_yearly_reference.py tests/test_org_simulation_yearly.py tests/test_jobfamily_simulation_yearly.py`): 21 bestanden, 6 fehlgeschlagen. Die sieben eingefrorenen Simulationsszenarien und die JF-Jahrestests bestanden. Die sechs Fehler betreffen OE-Renderingtests unter `@st.fragment`; drei direkte Aufrufe liefern ohne Streamlit-ScriptRunContext keine gerenderten Nachrichten, zwei AppTests laufen in ihr 3-s-Timeout, ein weiterer Renderingtest erhält `None`. Drei direkte Fehler wurden isoliert reproduziert. Diese Ausgangslage darf nicht als spätere Monte-Carlo-Regression fehlklassifiziert werden; Ursache und geeignete Testausführung vor dem Gate klären.
- Nächster Schritt: OE-/JF-Jahresscheibenreferenz aus dem unveränderten Code sichern und den realen 7-s-Pfad samt Zeitanteilen messen.

### 30.10.2026 – Phase 0: erste OE-/JF-Jahresreferenz gesichert

- `tests/test_monte_carlo_yearly_reference.py` und `tests/fixtures/monte_carlo_yearly_reference.json` aus dem unveränderten Simulationscode erzeugt. Sieben Szenarien: Identität, keine Ereignisse, Abgänge, Zugänge, ATZ/Ruhend, Azubi-Transfer und Kombination.
- Referenz prüft pro Szenario Rohereignisse, OE- und OE-Cluster-Jahresereignisse, OE-Jahresbestand/Checks sowie Jobfamily- und JF-Cluster-Jahresereignisse/Bestand/Checks. Für jede Tabelle sind Spalten, Datentypen, Zeilenanzahl und ein Hash über exakte Werte einschließlich Reihenfolge und Index eingefroren. Bei aktiven Zugängen ist die heutige Nichtverfügbarkeit zertifizierter Jahresbestände Teil der Referenz.
- `py -m pytest -q tests/test_monte_carlo_yearly_reference.py tests/test_org_yearly_reference.py`: **14 bestanden**. Das ist ein erster, reproduzierbarer Paritätsanker; er deckt noch nicht zusätzliche Seeds, produktive Datensätze, UI-Auswahlen oder Excel-Blätter ab.
- Nächster Schritt: reale 7-s-Messung mit vollständiger Seitenzeit und weitere Jahresreferenzen, besonders Exporte und Mehrfachzuordnungen.

### 30.10.2026 – Phase 0: Jahres-Exporte, Zusatzseeds und Seitenbenchmark

- `tests/test_monte_carlo_yearly_export_reference.py` friert für `departures`, `atz_ruhend` und `combined` die OE-Jahresexporttabellen einer konkreten Auswahl und – sofern heute verfügbar – die Jobfamily-/JF-Cluster-Bestands-Exporttabellen ein. `py -m pytest -q tests/test_monte_carlo_yearly_export_reference.py tests/test_monte_carlo_yearly_reference.py`: **10 bestanden**.
- `tests/test_monte_carlo_seed_reference.py` friert für `departures` und `combined` jeweils die Seeds **7 und 123** ein: finaler Snapshot, Abgangs-/Zugangsereignisse, OE-/OE-Cluster-/JF-/JF-Cluster-Jahrestabellen und Bestandsprüfungen. Die bestehenden Testhelfer akzeptieren dafür optional einen Seed; Standardseed 42 und der bisherige Golden Master bleiben unverändert. `py -m pytest -q tests/test_monte_carlo_seed_reference.py tests/test_org_yearly_reference.py`: **11 bestanden**.
- Vorhandenen Benchmark `benchmark_compact_plus_simulation()` unter eingefrorenen Einstellungen ausgeführt. Ergebnis: **7,4654 s** für Simulationsstart einschließlich `simulate_compact_snapshot`, `prepare_compact_data` und erstem Filter; warmer Darstellungsdurchlauf 0,9534 s, Tabwechsel 0,2628 s, Filterwechsel 0,2285 s. Windows 11 / Python 3.13.5 / pandas 2.3.3 / NumPy 2.4.1 / Streamlit 1.53.1. Das trifft die Nutzerbeobachtung von ca. 7 s, ist aber **keine vollständige Seiten-/Freigabezeit** und verwendet das eingefrorene Benchmark-Szenario.
- Ausstehend: Vollpfad mit Ereigniszusammenfassung, Freigabe und Streamlit-Rendering messen; RAM-Spitzen und aktive Zugänge profilieren; zusätzliche Seed-/Spezialfallreferenzen. Keine Produktivlogik geändert.
- Ergänzendes `cProfile` des Benchmarks: In einem instrumentierten Gesamtlauf dominierten Excel-Laden (`load_and_prepare_data`, 21 `read_excel`-Aufrufe), MAK-Lineage (`build_mak_lineage_audit` mit sieben `_person_stage_summary`-Aufrufen) und die zweimal aufgerufene Simulationsengine. `cProfile` erhöht die Laufzeit deutlich; seine kumulierten Sekunden sind **nicht** als uninstrumentierte Laufzeit zu interpretieren. Die Funktionsrangfolge bestätigt, dass Laden, Audit/Lineage und Abgangsprognose getrennt zu optimieren bzw. aus Zusatzläufen herauszuhalten sind.

### 30.10.2026 – Phase 0: Vollpfad-Benchmark mit Speicherprofil

- Read-only-Benchmark `tests/benchmark_monte_carlo_phase0.py` für denselben eingefrorenen lokalen Datensatz und dieselben Ausschlussregeln wie der Seitenbenchmark ergänzt. Er misst Datenladen, vollständige Simulation, Compact-Aufbereitung, OE-Ereigniszusammenfassung und beide JF-Jahresansichten; Prozess-Arbeitsspeicher wird etwa alle 10 ms abgetastet. Keine Produktivlogik geändert.
- Umgebung: Windows, Python 3.13.5, pandas 2.3.3; 1.662 Snapshot-Zeilen, 957 eindeutige Personalnummern; Horizont 31.12.2025 bis 31.12.2026. Ein einzelner Messlauf, deshalb keine belastbare Verteilung oder Leistungszusage. Laden des Snapshots 2,58 s, ATZ 0,48 s.
- Ohne Zugänge: Simulation 4,00 s, Compact-Aufbereitung 0,11 s, Ereigniskontext 0,02 s, **OE-Ereigniszusammenfassung 10,45 s**, JF-Jahresansicht 2,60 s, JF-Cluster-Jahresansicht 3,43 s. Prozess-Arbeitsspeicher von 160 MiB beim Start bis rund 212 MiB als beobachtete Spitze. Die Einzelzeiten sind kein Streamlit-Render- oder Freigabebenchmark.
- Mit 20 Neueinstellungen pro Jahr: Simulation 6,63 s, Compact-Aufbereitung 0,09 s, Ereigniszusammenfassung 0,04 s, JF und JF-Cluster jeweils etwa 0,11 s; beobachtete Speicherspitze rund 215 MiB. Die Szenarien liefen nacheinander im selben Prozess und teilen Caches; direkte Zeitvergleiche sind daher nur Hinweise. Bei Zugängen markiert der aktuelle Code zertifizierte OE-/JF-Jahresbestände als „Nicht verfügbar“.
- **Ausgangsbefund zur Parität:** Ohne Zugänge liefert die aktuelle OE-Bestandsüberleitung den Status „Abweichung“: sechs Endwertprüfungen abweichend, zwei Personen mit zusätzlichen Zuordnungs-/Aufbereitungseffekten. Der Status blieb nach Korrektur des Benchmarks auf die tatsächlichen App-Ausschlüsse bestehen. Spätere Änderungen müssen diesen aktuellen Output 1:1 erhalten, bis eine gesondert freigegebene fachliche Korrektur erfolgt; den Status nicht als Monte-Carlo-Regression bewerten.
- Folgerung: Der bisherige 7,47-s-Triggerwert unterschätzt den Aufwand für den gesamten Ergebnisaufbau. Zusatzläufe dürfen die teure OE-/JF-Aufbereitung nur vermeiden, wenn ihre je Lauf benötigten Jahreskennzahlen nachweislich exakt gleich entstehen. Für 10.000 Läufe in 20 s wären im Mittel höchstens 2 ms je Lauf einschließlich Aggregation verfügbar; mit dem heutigen vollständigen Lauf ist das Ziel nicht erreichbar.
- Nächster Schritt: OE-Zusammenfassung auf Funktionsebene profilieren, Jahresscheiben und Status im produktiven Datensatz als Golden Master sichern, Cold-/Warm-Cache-Messungen wiederholen und erst danach einen schlanken Zusatzlauf entwerfen.

### Vorlage für spätere Einträge

`Datum – Phase – Änderung/Erkenntnis – Messumgebung und Messwerte – Paritätsnachweis – Entscheidung – offene Risiken – nächster Schritt`
