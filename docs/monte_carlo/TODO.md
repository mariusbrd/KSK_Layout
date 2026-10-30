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

## Architekturentscheidung: Rechnung, Kennzahlen und Darstellung trennen

- **Durchgang 0 ist der Referenzlauf.** Bei `N=1` bleibt der vollständige heutige Pfad einschließlich Personenbestand, Audits, OE-/JF-Jahresscheiben, Freigabe und Export maßgeblich. Bei `N>1` bleibt dieser Lauf als konkret nachvollziehbares Ergebnis erhalten; die Zahl der Zusatzläufe darf seine Seeds oder Ergebnisse nicht ändern.
- **Zusatzläufe berechnen dieselben fachlichen Entscheidungen**, erzeugen aber nur die für das vereinbarte Ensemble benötigten Kennzahlen je Lauf, Jahr und Dimension. Jeder Kennzahlenwert muss gegen den vollständigen Lauf desselben Seeds beweisbar identisch sein. Einsparungen durch das Weglassen einer fachlichen Regel oder einer notwendigen Prüfung sind unzulässig.
- **Aggregation erfolgt nach der Rechnung je Lauf:** Mittelwerte und empirische Bänder werden aus den einzelnen, vollständig definierten Kennzahlen gebildet. Quoten, Mehrfachzuordnungen und Jahresbestände müssen zuvor je Lauf korrekt berechnet sein. Die App baut Diagramme, Tabellen und Exporte aus Referenzlauf und Ensemble-Ergebnis einmalig für die gewählte Ansicht; sie simuliert dafür nicht erneut.
- **Leistungsbudget separat ausweisen:** Vorbereitung, Referenzlauf, Zusatzläufe, Ensemble-Aggregation und Ergebnisaufbau einzeln messen; die vereinbarte 20-s-Grenze über die gesamte Berechnung samt Aggregation prüfen. Rendering und Export separat ausweisen. Falls schon der obligatorische Referenzlauf das Gesamtbudget überschreitet, ist das Ziel ohne weitere Beschleunigung des Referenzpfads rechnerisch nicht erreichbar.
- **Speichergrenze:** 10.000 vollständige Personen- und Jahres-DataFrames werden nicht gehalten. Nur Referenzlauf, ausdrücklich gewählte Prüfläufe und die für Quantile nötigen kompakten Kennzahlarrays beziehungsweise inkrementelle Aggregationen bleiben im Speicher.

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

Arbeitsentwurf: [Kennzahlenkonzept](KENNZAHLENKONZEPT.md). Er dokumentiert die Quellen im aktuellen Code, den ersten Implementierungsumfang, Verfügbarkeitsregeln und die noch offenen fachlichen Entscheidungen.

- [ ] Kennzahlenkatalog für CompactPlus, OE und Jobgruppen erstellen: additive Bestände/Flüsse, Quoten, Kosten, Ranglisten, Ereignisarten, Filterebenen und Zeitpunkte. Pro Kennzahl angeben, ob Mittelwert/Band sinnvoll ist.
- [ ] Pro Kennzahl festlegen, ob sie im Zusatzlauf entsteht, erst nach allen Läufen aggregiert oder ausschließlich aus dem konkreten Referenzlauf angezeigt wird. Benötigte Schlüssel `(Lauf, Jahr, Dimension, Gruppe, Kennzahl)` und die Behandlung fehlender Gruppen verbindlich definieren.
- [ ] Bandniveau und Berechnung festlegen (z. B. P10–P90, optional P5–P95); Stichprobenquantile, Rundung und Darstellung von null oder seltenen Ereignissen spezifizieren.
- [ ] Pro Durchgang zuerst fachlich vollständige Kennzahlen je gewählter Dimension berechnen. Quoten und Kosten pro Kopf/MAK je Durchgang bilden; Quantile niemals aus bereits aggregierten Gruppen oder addierten Quantilgrenzen ableiten.
- [ ] Stabile Gruppenmenge über alle Läufe definieren, einschließlich Gruppen ohne Ereignis/Bestand in einzelnen Läufen; deren Wert je nach Kennzahl explizit als 0 oder nicht definiert behandeln.
- [ ] Umgang mit Freigabe, einem konkreten Referenzlauf, manuellen Abgangskorrekturen und fehlgeschlagenen Läufen fachlich entscheiden. Ensemble-Mittelwert ist kein freigabefähiger Personenbestand.
- [ ] Jahresbestandsverläufe bei aktivierten Zugängen separat spezifizieren und je Lauf gegen den Zielbestand prüfen; bis dahin nicht als zertifizierte Bänder anbieten.
- **Gate:** prüfbare Beispiele für Mittelwerte, Quantile, Filter, Mehrfachzuordnungen und Quoten sind fachlich abgenommen.

## Phase 2 – Schlanker Zusatzlauf bei unverändertem Einzellauf

- [ ] Unveränderliche Eingaben einmal pro Ensemble vorbereiten; Reihenfolge, Datentypen und Kopiersemantik des Referenzlaufs dokumentieren.
- [ ] Die Pipeline explizit in einmalige Vorbereitung, vollständigen Referenzlauf, kompakte Zusatzläufe, Ensemble-Statistik und einmaligen Ergebnisaufbau zerlegen. Zeit und Speicher je Abschnitt messen, damit Rechenkernkosten nicht mit OE-/JF-Aufbereitung oder Streamlit-Rendering vermischt werden.
- [ ] Zusatzläufe ohne vollständige Anzeige-Audits, Lineage-Tabellen und Excel-Artefakte prototypisieren. Nur für die Bandberechnung erforderliche, prüfbare End- und Jahreskennzahlen behalten.
- [ ] Für jeden Zusatzlauf fachliche Gleichheit mit einem vollständigen Referenzlauf desselben Seeds nachweisen; erforderliche Prüfungen dürfen nicht entfallen, nur anders/kompakter ermittelt werden.
- [ ] Seed-Plan definieren: Durchgang 0 verwendet unverändert die heutigen Abgangs- und Zugangsseeds; weitere Durchgänge erhalten stabile, dokumentierte Seeds ohne Kollision. Die Zahl der Durchgänge darf Durchgang 0 nicht beeinflussen.
- [ ] 1, 10, 100 und 1.000 Durchgänge messen: Zeit je Stufe, Speicher, Cache-Verhalten, Ergebnisgleichheit. Größte verbleibende Kosten quantifizieren.
- [ ] Früh einen unteren Zeitbedarf für 10.000 Läufe extrapolieren: Referenzlauf plus 9.999 fachlich identische Zusatzläufe plus Aggregation. Liegt dieser selbst ohne UI über 20 s, gezielt den verbleibenden Rechenkern optimieren und keine Zielerreichung allein durch Auslagerung der Darstellung behaupten.
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
| 30.10.2026 | Festgelegt | Architektur | Vollständiger Referenzlauf und schlanke, je Seed exakt geprüfte Zusatzläufe; Kennzahlen je Lauf vor Ensemble-Statistik, Darstellung nach der Berechnung. Der `N=1`-Pfad bleibt fachlich identisch. |

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

### 30.10.2026 – Phase 0: OE-Kosten auf Funktionsebene eingegrenzt

- Den bestehenden Benchmark um optionales `MC_PROFILE_SUMMARY=1` ergänzt. `cProfile` instrumentiert dann nur die OE-Ereigniszusammenfassung des Szenarios ohne Zugänge; die normale Messung bleibt unverändert. Im instrumentierten Lauf dauerte diese Funktion 31,79 s statt etwa 10,45 s ohne Profiler. Diese Profiler-Sekunden sind **keine** reale Laufzeit.
- Im OE-Bestandsabgleich wurden `get_unique_employees` **1.069-mal**, `compute_headcount` **534-mal** und `raw_stock_mak` **534-mal** aufgerufen. Ihre kumulierten Profilerzeiten betrugen 28,61 s, 19,63 s und 13,44 s; die Werte überlappen wegen verschachtelter Aufrufe und dürfen nicht addiert werden. Der Code ruft die Bestandskennzahlen je Gesamt-/OE-/Clustergruppe wiederholt auf und baut dafür jeweils personenbezogene Pandas-Tabellen auf.
- Primärer Kandidat für eine spätere, eng begrenzte Beschleunigung ist die **einmalige Voraggregation der unveränderten Ausgangs-, Projektions- und Endbestände je Ebene**. Ein naiver Ersatz durch globale `groupby`-Summen ist fachlich riskant: `get_unique_employees` behandelt Mehrfachplanstellen, `compute_headcount` zählt Personen je Gruppe, und die bestehende Gleitkomma-Additionsreihenfolge kann MAK-Endwerte beeinflussen. Erst mit produktivem Golden Master für `annual`, `checks`, Diagnose und beide JF-Jahresscheiben optimieren.
- Die OE-Zusammenfassung rechnet die Bestandsüberleitung nur bei deaktivierten Zugangsbausteinen. Bei aktiven Zugängen bleibt sie im aktuellen Ablauf ausdrücklich nicht verfügbar. Eine Monte-Carlo-Anzeige von Jahresbestandsbändern über Zugänge braucht deshalb einen neuen fachlichen Ergebnisvertrag; das darf den Einzellaufpfad nicht stillschweigend verändern.
- Nächster Schritt: produktiven Golden Master inklusive der heutigen sechs OE-Abweichungen sichern; dann einen isolierten Voraggregationsprototyp mit vollständigem Hashvergleich bauen und getrennt messen. Für Zusatzläufe zuerst den benötigten Kennzahlenscope definieren.

### 30.10.2026 – Phase 0: produktiver Jahres-Golden-Master gesichert

- `tests/test_monte_carlo_production_yearly_reference.py` und die zugehörige JSON-Referenz aus dem unveränderten lokalen CompactPlus-Pfad erzeugt. Zwei Szenarien mit identischen eingefrorenen Eingaben und Parametern wie der Vollpfad-Benchmark: ohne Zugänge und 20 Neueinstellungen/Jahr. Die Referenz speichert ausschließlich Eingabe-/Tabellen-Hashes, Schema, Zeilenzahl, Datentypen, Einstellungen sowie fachliche Status- und Begründungstexte; keine personenbezogenen Zeilen.
- Je Szenario eingefroren: finaler Snapshot, Roh- und normalisierte Ereignisse, OE-/OE-Cluster-Jahresereignisse, OE-Bestandsjahre und Endwertprüfungen samt Personendiagnose sowie JF-/JF-Cluster-Jahresereignisse, Jahresbestände und Prüfungen. Der Tabellenhash umfasst Spalten, Datentypen, Index, Zeilenreihenfolge und exakte Werte.
- Ohne Zugänge: 534 OE-Bestandsjahreszeilen; OE-Status **„Abweichung“** mit den bestehenden sechs fehlgeschlagenen Endwertprüfungen; 17 Zeilen je JF-Jahresereignistabelle. Mit Zugängen: OE-Bestandsjahre leer und Status **„Nicht verfügbar“**; 23 Zeilen je JF-Jahresereignistabelle. Die JF-Bestandsgründe sind ebenfalls eingefroren.
- Zweiter, unabhängiger Durchlauf: `py -m pytest -q tests/test_monte_carlo_production_yearly_reference.py` → **1 bestanden in 18,42 s**. Der Test scheitert auch bei geänderten lokalen Eingangsdaten, damit ein veralteter produktiver Golden Master nicht stillschweigend als Code-Regression ausgelegt wird. Er ist an die dokumentierte Python-/Pandas-Umgebung und lokalen Daten gebunden.
- Nächster Schritt: isolierten Voraggregationsprototyp nur hinter einem Test-/Benchmarkpfad erproben und gegen alle produktiven und synthetischen Jahresreferenzen prüfen; vor produktiver Änderung weitere Spezialfälle/mehrjährige Horizonte ergänzen.

### 30.10.2026 – Phase 0: isolierter Voraggregationsprototyp

- `tests/probe_oe_stock_preaggregation.py` vergleicht die bisherigen Kopf-/MAK-Bestandskennzahlen je OE und OE-Cluster mit einer einmaligen Personenaggregation; er verändert oder monkeypatcht keine Produktivfunktion. Der produktive Referenztest kann dafür einen lesenden Probe-Callback aufrufen, sein gespeicherter Golden Master bleibt unverändert.
- Der naheliegende Kandidat aus verschachtelter `groupby`-Summierung war **nicht bitgleich**: bei 20 OE-Gruppen und drei Clustern im Ausgangsbestand MAK-Differenzen bis etwa `7,1e-15`. Er wurde verworfen. Ursache ist die abweichende Additionsreihenfolge; auch der winzige Betrag verletzt die 1:1-Vorgabe.
- Der korrigierte Kandidat erhält die Reihenfolge des ersten Auftretens jeder Person je Gruppe, summiert zunächst je Person und danach die Personensummen in dieser Reihenfolge; Gruppen ohne besetzte Stelle bleiben mit `(0, 0.0)` enthalten. Im produktiven Datensatz ergaben sich für Ausgangs-, Projektions- und Endbestand über je 153 OEs und 24 Cluster **0 abweichende Gruppen**, einschließlich exakter MAK-Floatwerte.
- Ein einzelner Probelauf ergab für die OE-Gruppen je Bestand etwa **1,10–1,35 s** im bisherigen Kennzahlpfad gegenüber **0,013–0,019 s** für den Kandidaten; für Cluster etwa **0,17–0,25 s** gegenüber **0,007–0,012 s**. Diese Mikrozeiten sind keine Beschleunigung der ganzen Seite oder Simulation und noch keine statistisch abgesicherte Messung.
- **Grenze:** Der Prototyp prüft nur Gruppen-Kopf/MAK, noch nicht die gesamte OE-Jahresüberleitung, `checks`, Diagnosen oder JF-Jahresscheiben unter einer optimierten Implementierung. Vor Integration synthetische Mehrfachplanstellen, fehlende Personalnummern, unterschiedliche Eingabereihenfolgen, mehrjährige Horizonte und alle produktiven Hashreferenzen prüfen. Keine Produktivlogik geändert.
- Nächster Schritt: den Kandidaten als eng begrenzte alternative Bestandskennzahl in einer isolierten Kopie der OE-Überleitung einsetzen und vollständige `annual`-/`checks`-/JF-Hashes sowie Zeit vergleichen. Erst nach voller Parität wäre ein Produktivpatch vertretbar.

### 30.10.2026 – Phase 0: vollständige OE-Überleitung mit Testinjektion verglichen

- Die bestehende `build_departure_stock_bridge` besitzt nun einen **optionalen, standardmäßig nicht gesetzten** `stock_metric_provider`. Der normale Dashboard-/Einzellauf ruft weiterhin exakt den bisherigen `compute_headcount`-/`raw_stock_mak`-Pfad auf. Nur der isolierte Probe-Pfad injiziert die voraggregierten Gruppenkennzahlen; keine Streamlit-Seite verwendet ihn.
- Im lokalen produktiven Szenario ohne Zugänge waren zwischen Standard- und Probe-Pfad die Hashes für `annual`, `checks`, `person_diagnostics`, `basis_effects`, `reporting_events` und `documented_effects` sowie OE-Status und Begründung **exakt identisch**. Zeit nur für die OE-Bestandsüberleitung im einzelnen Probelauf: **5,64 s Standard, 1,46 s Kandidat**. Der restliche Ergebnisaufbau und die Simulation sind darin nicht enthalten.
- `tests/test_oe_preaggregation_probe.py` prüft denselben optionalen Pfad in allen sieben synthetischen Jahresreferenzszenarien gegen jeden Schlüssel und jedes Tabellenhash-Ergebnis der Standard-Überleitung. Zusammen mit produktivem Golden Master und bestehenden Jahresreferenzen: **15 Tests bestanden in 27,60 s**.
- Das ist ein belastbarer lokaler Paritätsnachweis für diese Szenarien, aber noch keine Freigabe zur standardmäßigen Nutzung: Mehrjahresläufe, andere Datenreihenfolgen und Randfälle der Personen-/Planstellen-ID fehlen; JF-Jahresscheiben wurden im Probe-Pfad nur mittelbar über den unveränderten Standard-Golden-Master geprüft. Wiederholte, getrennte Laufzeitmessungen stehen aus.
- Nächster Schritt: zusätzliche Randfall- und Mehrjahres-Golden-Masters erzeugen, JF-Jahresansichten bei explizitem Probe-Bridge-Ergebnis direkt vergleichen und den 4-s-Effekt in wiederholten Vollpfadmessungen bestätigen. Danach entscheiden, ob die Voraggregation den Standardpfad ersetzen darf.

### 30.10.2026 – Phase 0: JF-Direktvergleich und Mehrjahresprüfung

- Das optionale OE-Bridge-Ergebnis wird nun ausdrücklich in die **Jobfamily- und JF-Cluster-Jahresansicht** eingespeist und Feld für Feld mit den Ansichten des Standard-Bridge-Ergebnisses verglichen. Im lokalen Produktivlauf: **0 unterschiedliche Felder** in beiden Ansichten; OE-Bridge-Tabellen, Status und Begründung ebenfalls weiterhin hashgleich.
- Die sieben vorhandenen synthetischen Referenzszenarien enthalten außer dem Nullhorizont bereits den Zeitraum **31.12.2025–31.12.2027**. Die direkte JF-Prüfung läuft für alle sieben. Zusammen mit den unveränderten Jahresreferenzen: **14 Tests bestanden**; zusätzlich drei gezielte Varianten mit Mehrfachplanstelle, fehlender Personalnummer, ausschließlich vakanten Gruppen und unterschiedlicher Eingabereihenfolge: **10 Probe-Tests bestanden**.
- Ein weiterer einzelner Produktivlauf ergab für die OE-Bridge **9,47 s Standard gegenüber 2,00 s Kandidat**. Die absolute Zeit schwankt deutlich gegenüber dem vorigen Lauf (5,64/1,46 s); das Verhältnis ist ein Hinweis, kein abgesicherter Speedup. Gesamtseitenzeit und wiederholte Kalt-/Warmläufe bleiben offen.
- Der normale Dashboardpfad verwendet die Voraggregation weiterhin nicht. Vor Umschaltung fehlen insbesondere weitere produktive Parameter-/Seedkombinationen und ein reproduzierbares Vollpfad-Benchmarkprotokoll; die aktuellen Golden Masters müssen unverändert bleiben.

### 30.10.2026 – Phase 0: wiederholte OE-Bridge-Messung

- `tests/benchmark_oe_bridge_repeated.py` lädt und simuliert den eingefrorenen lokalen Datensatz einmal, normalisiert die Ereignisse und misst dann drei Standard-/Voraggregationspaare mit wechselnder Reihenfolge. Jede Bridge-Ausgabe wird über alle Tabellenhashes und Statusfelder sowohl zwischen den Wiederholungen als auch zwischen beiden Pfaden exakt verglichen.
- Laufzeiten der **isolierten OE-Bestandsüberleitung** in Sekunden: Standard `[10,2109; 7,6487; 7,1526]`, Voraggregation `[3,1097; 2,6873; 2,1600]`; Mediane **7,6487 s** und **2,6873 s** (etwa Faktor 2,85). In jedem Paar volle Ergebnisparität. Beide Reihen zeigen einen Warm-/Systemeffekt; Messung ist lokal und enthält weder Simulation noch JF-Aufbau noch Streamlit-Rendering.
- Die bisherige Aussage „etwa vier Sekunden Einsparung“ ist für diese Messung plausibel, aber die Gesamtseitenwirkung ist weiter offen. Vor einer Standardpfad-Änderung vollständigen CompactPlus-Ergebnisaufbau mit und ohne Voraggregation messen und zusätzliche produktive Seeds/Parameter prüfen. Das 10.000-in-20-s-Ziel bleibt dadurch unberührt: selbst 2,7 s je Bridge wären für Zusatzläufe viel zu teuer; der Zusatzlauf braucht einen separaten schlanken Kennzahlpfad.

### 30.10.2026 – Phase 0: vollständiger OE-/JF-Ergebnisaufbau gemessen

- `tests/benchmark_full_result_preaggregation.py` misst auf demselben gespeicherten produktiven Simulationslauf die komplette OE-Ereigniszusammenfassung und danach die Jobfamily- und JF-Cluster-Jahresansichten. Je drei Standard-/Probe-Paare mit wechselnder Reihenfolge. Nach jedem Durchlauf werden **alle DataFrames über exakte Schema-/Wert-/Reihenfolge-Hashes** und übrige Ergebnisfelder verglichen: **sechs volle Ergebnisaufbauten exakt identisch**. Der Probe-Pfad verwendet eine temporäre Funktionsinjektion nur im Benchmark; der App-Pfad bleibt unverändert.
- Gemessene OE-Zusammenfassung in Sekunden: Standard `[22,3328; 17,1758; 16,7057]`, Probe `[6,0724; 5,5378; 3,9298]`; Mediane **17,1758 s** und **5,5378 s**. Vollständiger OE-/JF-Ergebnisaufbau: Standard `[31,2692; 24,2209; 24,3178]`, Probe `[14,3348; 13,5969; 13,5912]`; Mediane **24,3178 s** und **13,5969 s**.
- Die absoluten Zeiten lagen deutlich über den früheren Einzelmessungen und drifteten innerhalb der Reihe. Das weist auf erhebliche System-/Cache-/Messumgebungseinflüsse hin; die Zahlen beschreiben diesen lokalen Messlauf, keine verlässliche Produktions-SLA. Das Ergebnis belegt jedoch für denselben Input einen großen Kostenteil in der OE-Überleitung. Laden, Simulation, Streamlit-Rendering und Export sind **nicht** enthalten.
- Auch nach Voraggregation kostet der Ergebnisaufbau hier mehr als die gewünschten 20 s für 10.000 Durchgänge. Für Monte Carlo muss der Referenzlauf exakt bleiben und Zusatzläufe dürfen nur ihre fachlich benötigten, pro Lauf geprüften Kennzahlen aufbauen. Nächster Schritt: weiteren OE-/JF-Aufwand und den Ergebnisumfang pro Zusatzlauf festlegen; die Voraggregation erst nach zusätzlichen produktiven Seeds und stabilerer Messumgebung als Standardpfad erwägen.

### 30.10.2026 – Architekturentscheidung für die nächste Projektphase

- Den Ansatz „vollständiger Referenzlauf + fachlich gleiche schlanke Zusatzläufe + einmalige Ensemble-Auswertung/Darstellung“ als verbindlichen Architekturvertrag aufgenommen. Damit wird die teure OE-/JF-Aufbereitung nicht 10.000-mal ausgeführt; Jahreskennzahlen und Mehrfachzuordnungen bleiben trotzdem pro Lauf prüfpflichtig.
- Effekt auf die Planung: Phase 1 muss einen konkreten Kennzahlen- und Schlüsselvertrag liefern, bevor Phase 2 einen Zusatzlauf implementiert. Phase 2 misst künftig getrennt Vorbereitung, Referenz, Zusatzläufe, Statistik und Ergebnisaufbau. Die 20-s-Grenze gilt weiter für die vereinbarte Gesamtberechnung, nicht bloß für einen isolierten Rechenkern.
- Nächster Schritt: Kennzahleninventar aus den tatsächlichen CompactPlus-, OE- und JF-Seiten erstellen und pro Kennzahl festlegen, welche Laufdaten sie braucht. Danach einen minimalen Zusatzlauf für ein enges, exakt vergleichbares Szenario prototypisieren.

### 30.10.2026 – Phase 1: Kennzahlenkonzept als Arbeitsentwurf

- `KENNZAHLENKONZEPT.md` aus den tatsächlichen CompactPlus-, OE-/OE-Cluster- und Jobfamily-/JF-Cluster-Pfaden abgeleitet. Erster Umfang: Zielbestand/Ereigniszahlen und vier Ebenen der Jahresereignisse. Jahresbestandsbänder und Analyse-KPI-Karten bleiben an eigene Zertifizierungs-/Filtergates gebunden.
- Kritischer Filterbefund: Die bestehenden globalen Personenfilter können kombiniert werden. Einzelne OE- oder JF-Randsummen reichen nicht, um deren Schnittmenge je Lauf exakt nachzubilden. Ensemble-Bänder für solche Kombinationen benötigen eine feinere gemeinsame Laufbasis oder exakte Neuberechnung; der erste Prototyp begrenzt die Ensemble-Auswahl deshalb auf ungefilterte Gesamtwerte und explizite Jahresgruppen.
- Nächster Schritt: anhand des Konzepts ein kleines maschinenprüfbares Zusatzlauf-Kennzahlenpaket definieren, zunächst für einen Seed und die vier Jahresereignisebenen. Vor breiter UI-Integration die offenen Punkte zu Banddefinition, Seed-Plan, Filterumfang und nicht zertifizierten Läufen entscheiden.

### 30.10.2026 – Phase 1: kompaktes Kennzahlenpaket v1 eingefroren

- `utils/monte_carlo_metric_packet.py` definiert einen reinen Wertadapter mit Ziel-Köpfen/MAK/EUR, den vier OE-/JF-Jahresereignisebenen und Bestandsstatus je Ebene. Jede Ereigniszeile wird als `(Jahr, Ebene, Gruppe, Kategorie, Ereignisart, Kennzahl, Wert)` gespeichert; doppelte oder nicht endliche Schlüssel/Werte werden abgelehnt. Das Paket enthält keine Personenzeilen oder Anzeige-DataFrames.
- Vier vollständige synthetische Referenzläufe (`departures` und `combined`, Seeds 7 und 123) als Hash-Fixture gesichert. Umfang: 32 bis 200 einzelne Jahreskennzahlenwerte je Lauf. Unabhängiger Prüflauf zusammen mit den bisherigen Seed-Golden-Masters: **8 Tests bestanden**.
- Wichtiger Statusbefund: Schon der synthetische Fall `departures:7` liefert OE-Status „Abweichung“ und keine JF-Bestandskurve; `departures:123` ist abgestimmt und hat JF-Bestand. Die Bestandsband-Verfügbarkeit ist somit **seedabhängig** und darf nicht aus dem Referenzlauf allein abgeleitet werden.
- Der Adapter liest noch vollständige Lauf- und Ansichtsoutputs. Er beweist die Struktur und exakten Zielwerte, **keine** Beschleunigung. Nächster Schritt: dieselben Paketwerte für Zusatzseeds mit einem schrittweise schlankeren Pfad erzeugen und pro Seed gegen den eingefrorenen vollständigen Referenzlauf vergleichen. Seed-/Metadatenhülle und Filterumfang bleiben offene Vertragsbestandteile.

### 30.10.2026 – Phase 2 begonnen: Ereignispaket ohne Bestandsüberleitungen

- `utils/monte_carlo_event_packet.py` baut Zielwerte und OE-/OE-Cluster-/JF-/JF-Cluster-Jahresereignisse direkt aus dem gespeicherten Simulationskontext. `build_jobfamily_yearly_view(..., include_stock=False)` überspringt ausschließlich die JF-Bestandsprojektion; der Standardwert bleibt `True` und der bestehende Dashboardpfad unverändert. OE-Bridge und JF-Projektion sind im Test durch absichtliche Fehlermocks abgesichert: **kein Aufruf** im Zusatzpfad.
- Für vier Zusatzseed-Fälle (`departures`/`combined`, jeweils 7/123) stimmen `target` und **alle Jahresereigniswerte samt Reihenfolge** exakt mit dem vollständigen Referenzpaket überein. Bestandsstatus im schlanken Paket ist ausdrücklich „Nicht berechnet“ und darf nicht als Zertifizierung ausgegeben werden. Zusammen mit den vorhandenen Paket- und Jahresreferenzen: **15 Tests bestanden**.
- Isolierte Auswertung nach bereits erfolgter Simulation (sechs Personen, ein einzelner Messlauf): volle Paketableitung `1,0250 / 1,0803 / 0,2168 / 0,3126 s`; Ereignis-only `0,1218 / 0,1183 / 0,1631 / 0,1560 s` in derselben Fallreihenfolge. Die Zugangs-Szenarien sparen weniger, weil der volle Bestandsweg dort ohnehin nicht verfügbar ist. Weder Simulation noch finale Snapshot-Aufbereitung sind in diesen Zeiten enthalten.
- **Leistungsbefund:** Selbst der schlanke Ereignispfad benötigt bei diesem kleinen synthetischen Datensatz noch rund 120–160 ms je Lauf. Bei 10.000 Läufen wären das isoliert ungefähr 20–27 Minuten, weit über dem 20-s-Gesamtziel. Das heutige Pandas-/Personen-Zuordnungsverfahren kann deshalb nicht einfach 10.000-mal wiederholt werden; nachgewiesen werden muss ein gebündelter oder kompakter Rechenkern bei exakt gleichen Jahreswerten.
- Nächster Schritt: Kosten des Ereignispfads in Normalisierung, OE-Gruppierung, JF-Zuordnung und Paketserialisierung aufteilen; einen vorbereiteten, mehrfach verwendbaren Zuordnungskontext entwerfen. Zusätzlich die eigentliche Abgangs-/Zugangs-Simulation pro Seed weiter profilieren. Der Referenzlauf bleibt unangetastet.

### 30.10.2026 – Phase 2: Ereignispfad zerlegt und JF-Basis vorbereitet

- Stufenmessung an vier synthetischen Zusatzseed-Fällen: Ereignisnormalisierung ca. **8–36 ms**, beide OE-Gruppierungen zusammen ca. **4–12 ms**, JF- und JF-Cluster-Jahresansicht zusammen ca. **45–105 ms**, Paketaufbau ca. **1–3 ms**. Die beiden JF-Zuordnungen sind der größte Auswertungsanteil; absolute Zeiten schwanken zwischen Läufen.
- `prepare_jobfamily_baseline` berechnet eindeutige ursprüngliche Jobgruppen-Zuordnungen und Mehrfachplanstellen-Gewichte **einmal pro Ausgangsbestand**. Der optionale `baseline_cache` wird nur im Zusatzpfad übergeben; die Standardaufrufe der JF-Ansicht bleiben unverändert. Finale Personen-/JF-Zuordnungen werden weiterhin je Seed berechnet.
- Für alle vier Seed-Fälle sind vorbereitetes und unvorbereitetes Ereignispaket exakt gleich und beide entsprechen den vollständigen Jahresereignissen. Eine Vorbereitung aus Seed 7 wurde bei Seed 123 wiederverwendet und lieferte dieselben Werte. Einschließlich bestehender JF- und OE-Jahresreferenzen: **24 Tests bestanden**.
- Einzelmessung nach bereits erfolgter Simulation, Vorbereitungszeit nicht enthalten: unvorbereitet **0,097 / 0,117 / 0,119 / 0,114 s**, vorbereitet **0,067 / 0,087 / 0,097 / 0,091 s** für `departures:7`, `departures:123`, `combined:7`, `combined:123`. Die Ersparnis von etwa 20–30 ms pro Lauf ist relevant, aber selbst der vorbereitete Pfad überschreitet das 2-ms-Gesamtbudget um ein Vielfaches. Weitere Optimierung der JF-Zuordnung und der eigentlichen Simulation ist zwingend.
- Nächster Schritt: vorbereiteten Kontext mit Eingabe-/Clusterfingerabdruck absichern; JF-Jahresereignisse in einem kompakteren, möglichst gebündelten Mehrseed-Pfad prototypisieren. Vorher die Kosten der Abgangs-/Zugangslogik für wechselnde Seeds getrennt messen, damit sich das Restbudget realistisch verteilt.

### 30.10.2026 – Phase 2: Simulationsengine mit wechselnden Seeds vermessen

- `tests/benchmark_seeded_engine_stages.py` lädt den eingefrorenen lokalen Datensatz einmal und misst vollständige CompactPlus-Läufe für Seeds **42, 7, 123**, jeweils ohne Zugänge und mit 20 Neueinstellungen/Jahr. Die importierten Engine-Funktionen werden ausschließlich während der Messung mit Zeit-Wrappern versehen; Personendaten werden nicht ausgegeben. Für Seed 42 stimmt der aufbereitete finale Snapshot in beiden Szenarien exakt mit dem produktiven Golden Master überein.
- Vollständige Engine-Laufzeiten ohne Zugänge **4,19 / 4,47 / 5,20 s**, mit 20 Neueinstellungen **4,35 / 4,20 / 6,33 s** (ein einzelner, sequentieller Messlauf; Reihenfolge und Caches beeinflussen die Werte). Audit-Tabellen je Lauf **2,64 / 2,69 / 3,28 s** bzw. **2,02 / 2,89 / 3,45 s** und damit größter einzelner Block.
- Abgangsprognose ohne Zugänge **0,78 / 0,95 / 0,89 s**, mit Neueinstellungen **1,21 / 0,47 / 1,04 s**. Zugangsprognose ohne aktive Bausteine etwa **0,01–0,02 s**, mit Neueinstellungen **0,12 / 0,12 / 0,33 s**. Aktualisierung bestehender Zeilen **0,23–0,61 s**, Finalisierung **0,23–0,41 s**; neue Personenzeilen bei aktiven Zugängen **0,11–0,24 s**. Wrapperzeiten können verschachtelt sein und sind nicht als streng additive Zerlegung zu lesen.
- Die Differenz aus Gesamtzeit und Auditzeit beträgt grob **1,55–2,88 s je Lauf**. Das ist keine gemessene „auditfreie Engine“, aber ein belastbarer Hinweis: Selbst vollständiger Audit-Verzicht ließe den aktuellen Rechenkern um Größenordnungen über dem Budget von höchstens 2 ms pro Lauf. Für 10.000 Durchgänge braucht es daher neben dem schlanken Ereignispaket eine wesentlich kompaktere oder gebündelte, paritätsgeprüfte Abgangs-/Zugangsrechnung und Finalisierung.
- Nächster Schritt: einen **optionalen** auditfreien Zusatzlaufpfad prototypisieren, finale Snapshots/Ereignisse für mehrere Seeds gegen den vollständigen Lauf vergleichen und dessen echte Laufzeit messen. Erst danach die verbleibenden Rechenkernfunktionen detailliert profilieren; keine Audits im Referenzlauf entfernen.

### 30.10.2026 – Phase 2: optionaler auditfreier Engine-Lauf

- `simulate_compact_snapshot(..., include_audits=False)` überspringt ausschließlich die Anzeige-Audits und die dafür zusätzlich gehaltenen Zwischen-Snapshot-Kopien. Der Standardwert ist `True`; alle bestehenden Dashboard-/Einzellaufaufrufe und deren Audits bleiben unverändert. Auch im Nullhorizont wird bei `False` kein Audit gebaut.
- Synthetische Parität für Nullhorizont, Abgänge (Seeds 7/123) und Kombination (Seeds 7/123): finaler Snapshot, finaler Personenstand, Abgangs-/Zugangsereignisse, Prognose-KPIs, Metadaten und das schlanke **OE-/JF-Jahresereignispaket** stimmen exakt mit einem vollständigen Lauf desselben Seeds überein. Ein Fehlermock verbietet den Audit-Aufruf im Zusatzpfad. Gezielte Referenztests: **12 bestanden**.
- Produktive Paarmessung auf eingefrorenem Datensatz, Seeds 42/7/123, jeweils ohne und mit 20 Neueinstellungen/Jahr: finale Snapshots, Personenstände, Abgangs-/Zugangsereignisse und Metadaten in **allen sechs Paaren exakt gleich**. Vollständige Engine **4,94 / 4,78 / 5,52 s** bzw. **4,36 / 4,81 / 5,74 s**; auditfrei **2,21 / 2,44 / 1,61 s** bzw. **2,07 / 2,35 / 1,52 s**. Die Laufreihenfolge wurde teilweise gewechselt; ein einzelner sequentieller Messlauf mit Cache-/Systemeinflüssen, keine SLA.
- Der auditfreie Pfad ist weiterhin etwa **750- bis 1.200-mal langsamer als ein 2-ms-Laufbudget** und enthält noch nicht das Ereignispaket. Das 20-s-Ziel für 10.000 Läufe ist mit dieser Engine-Struktur nicht erreichbar. Der Audit-Verzicht ist dennoch ein sauberer, exakt geprüfter Baustein für weniger Zusatzlaufkosten.
- Nächster Schritt: die verbleibenden 1,5–2,4 s in Abgangsprognose, Zugangslogik, Snapshot-Aktualisierung und Finalisierung auf Funktionsebene profilieren. Danach einen gebündelten Kern mit festgehaltener Ereignis-/Seed-Reihenfolge prototypisieren; Jahres- und Endwerte je Seed weiter gegen vollständige Läufe prüfen.

### Vorlage für spätere Einträge

### 30.10.2026 – Phase 2: auditfreien Rechenkern auf Funktionsebene profiliert

- `tests/profile_audit_free_core.py` profiliert einen produktiven Lauf mit eingefrorenen lokalen Eingaben, Seed 42 und `include_audits=False`, wahlweise mit 20 Neueinstellungen/Jahr oder ohne. Es gibt nur Funktionszeiten und Ereignisanzahlen aus, keine Personenzeilen. Die folgenden **cProfile-Kumulativzeiten sind instrumentiert, überlappen sich und sind keine reale Laufzeitmessung**; maßgeblich für Laufzeiten bleiben die vorigen uninstrumentierten Paarmessungen.
- Mit Neueinstellungen: gesamter Engine-Aufruf **4,57 s**, `run_forecast_abgaenge` **1,90 s**, `_update_existing_rows` **0,95 s**, `_finalize_future_snapshot` **0,60 s**, `_append_new_people_rows` **0,47 s**, `run_forecast_zugaenge` **0,31 s**. Ohne Neueinstellungen: gesamt **3,93 s**, Abgänge **1,83 s**, Bestandsaktualisierung **0,98 s**, Finalisierung **0,70 s**, Zugänge nur **0,03 s**. Es fielen jeweils 70 Abgänge an; mit Neueinstellungen zusätzlich 20 Zugänge.
- Innerhalb der Abgangsprognose fallen `_schedule_new_atz_cases` mit etwa **0,60 s** (13 Perioden) und der Generator für Kündigungsdiagnose-Zeilen mit **0,57–0,60 s** (1.612 Aufrufe) auf. Die Diagnose wird auch für den auditfreien Engine-Pfad weiterhin aufgebaut. Innerhalb der Bestandsaktualisierung kosten 888 Aufrufe der personenweisen `groupby.transform`-Funktion bereits etwa **0,26–0,28 s** instrumentiert. Finalisierung umfasst erneute Anreicherung, MAK-/Kostenrechnung und Ausschluss-/Zuordnungslogik.
- **Architekturfolge:** Der optionale Audit-Verzicht spart den Anzeigeaufbau, lässt aber die periodische Pandas-Zustandsführung, Diagnosen und personenweise Snapshot-Finalisierung bestehen. Allein das Weglassen der Kündigungsdiagnose kann das 2-ms-Gesamtbudget pro Zusatzlauf nicht erreichen. Der nächste Prototyp muss den eigentlichen mehrperiodischen Rechenkern und die per-Seed-Kennzahlableitung kompakt oder gebündelt ausführen.
- **Paritätsvertrag für den nächsten Schritt:** Perioden- und Ereignisreihenfolge, Seed-/Teilstream-Verbrauch, `DataFrame.sample`-Auswahl, ATZ-/Kündigungsbedingungen, Vakanzen und Zugangszuteilung sowie Float-Additionsreihenfolge explizit als Trace erfassen. Jede Änderung zuerst gegen vollständige Einzelruns pro Seed auf finale Personen-/Ereigniswerte und OE-/JF-Jahresscheiben prüfen. Als eng begrenzter Zwischenschritt kann die nicht ergebnisrelevante Diagnosesammlung optional entfallen, sofern RNG und alle Ergebnis-Hashes exakt gleich bleiben. Der Standardlauf bleibt dabei unverändert.

`Datum – Phase – Änderung/Erkenntnis – Messumgebung und Messwerte – Paritätsnachweis – Entscheidung – offene Risiken – nächster Schritt`
