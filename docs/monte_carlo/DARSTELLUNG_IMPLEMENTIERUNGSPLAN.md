# Implementierungsplan: Monte-Carlo-Darstellung im Streamlit-Dashboard

Stand: 10.11.2026 · Branch `feature/monte-carlo-presentation` · Ausgangspunkt `monte-carlo-calculation-v1` (`044bdae`)

## Ziel und verbindliche Regeln

- [ ] `N=1` verwendet den bisherigen Produktlauf und liefert bei identischem Datenstand, beiden bisherigen Seed-Werten und allen Parametern exakt dieselben Ergebnisse, Jahresscheiben, Freigabeobjekte und Exporte wie vor der Integration. Neue UI-Metadaten dürfen die fachlichen Werte nicht verändern.
- [ ] `N>1` berechnet vollständige, voneinander unabhängige Läufe desselben Produktpfads. Nur ein vollständiges Ensemble mit den vorgesehenen Seed-IDs und identischem Eingabefingerprint wird angezeigt. Fehler, Abbruch und veraltete Parameter dürfen keine scheinbar vollständigen Mittelwerte erzeugen.
- [ ] Der erste Seed ist ein konkreter Referenzlauf mit Personen- und Planstellendaten. Ensemble-Mittelwerte sind ausschließlich Kennzahlen; es gibt keinen gemittelten Personenbestand, keine gemittelten Planstellen und keine Freigabe einer fiktiven Personentabelle.
- [ ] Jahreswerte für Organisationseinheit, OE-Cluster, Jobgruppe und JF-Cluster entstehen aus den zertifizierten Werten **jedes** Laufs. Pro Jahr, Gruppe und Kennzahl werden erst die einzelnen Läufe berechnet und danach Mittelwert und Quantile gebildet. N=1-Jahresscheiben bleiben exakt unverändert.
- [ ] Bänder heißen in der Oberfläche „80-%-Ergebnisbereich (P10–P90)“ oder eine gleichwertig klare Bezeichnung. Es sind empirische Quantile der simulierten Ergebnisse, kein Konfidenzintervall des Mittelwerts. EUR-Ensemblewerte bleiben gemäß bestehender Entscheidung vertagt.
- [ ] Die feste Stellenplanlogik, Azubi-/Trainee-Regeln, IST-/SOLL-MAK, ATZ und RNG-Reihenfolge bleiben unberührt. UI, Orchestrierung und Projektion erhalten separate Verträge und Tests.
- [ ] Jede Seite wird erst nach ihrer technischen und visuellen Abnahme als abgeschlossen markiert. Nachfolgende Seite beginnt erst, wenn die vorherige freigegeben ist; Querschnittsfehler können eine frühere Abnahme wieder öffnen.

## Bestehende Architektur und Konsequenzen

- `pages/8_⚙️_Simulationsparameter.py` verwaltet derzeit Abgangs- und Zugangsparameter mit getrennten Datum-/Seed-Feldern. `utils/simulation_params.py` speichert außerdem `compact_plus`, aber die tatsächliche CompactPlus-Zielsteuerung erfolgt zurzeit auf `pages/7_⚡_Kompakt_plus_Simulation.py`. Die neuen Laufoptionen müssen in einer eindeutig wirksamen Gruppe stehen; doppelte Datums- oder Seed-Angaben dürfen nicht widersprüchlich sein.
- Seite 7 erzeugt `compact_sim_*`-Session-Artefakte und eine Signatur. `utils/simulation_validation.py` leitet daraus konkrete Original- und Freigabeansichten ab. `pages/10_🏢_Organisationseinheiten_Simulation.py` und `pages/11_💼_Jobfamily_Simulation.py` verlangen die Freigabe. Die Ensemble-Aggregation erhält ein zusätzliches, versioniertes Ergebnisobjekt neben diesen bestehenden Artefakten.
- `utils/monte_carlo_ensemble.py` aggregiert vollständige Kennzahlpakete. Der Zwei-/Drei-Prozess-Runner in `tests/benchmark_monte_carlo_two_process.py` ist ein **Benchmark**, noch kein App-Dienst: Prozessstart, Eingabe-Snapshot, Status-/Abbruchbehandlung, Windows-Schlafschutz und Rückgabe an Streamlit müssen produktfähig gekapselt werden.
- Bestehende globale kombinierte Personenfilter lassen sich aus den aktuell nach OE/JF aggregierten Paketen nicht exakt rekonstruieren. Bis ein zertifizierter gemeinsamer Filterwürfel existiert, zeigen gefilterte Personenansichten nur den konkreten Referenzlauf; Ensemblewerte werden nur für die jeweils nachweisbar unterstützte ungefilterte oder explizite OE-/JF-Gruppenauswahl angezeigt. Die Seite muss diese Grenze direkt am Wert erklären.

## Vorbereitungsphase 0: gemeinsamer Ergebnisvertrag, ohne neue Ansicht

- [ ] Versioniertes `RunConfig`/`EnsembleResult` festlegen: Eingabefingerprint, Basis-/Zieldatum, vollständige Abgangs-/Zugangsparameter, konkrete Seed-Paare je Lauf, N, Bandniveau, Paketversion, Status, Fehlerliste, Laufzeit und Referenzlauf-ID.
- [ ] Seed-Vertrag: Bei `N=1` die **beiden bestehenden Seeds unverändert** an die beiden Forecasts geben. Ein gemeinsames UI-Seed-Feld darf einen abweichenden gespeicherten Seed-Paar-Fall nicht stillschweigend überschreiben. Bei `N>1` aus dem gespeicherten Seed-Paar deterministisch je Index `i` das Paar `(abgangs_seed+i, zugangs_seed+i)` bilden; beide Ausgangswerte beziehungsweise ein vereinheitlichter Wert werden im UI sichtbar. Eine explizite Seed-Änderung ist eine Parameteränderung und invalidiert alte Ergebnisse.
- [ ] Gemeinsame Signatur aus tatsächlichen Eingaben und Laufoptionen bauen; alte Einzellauf-Signatur für N=1 wertgleich lassen oder per Migration die Parität des Ergebnisobjekts belegen. Änderungen an N, Seed, Ziel-Datum, Fachparametern, Ausschlüssen, Clusterquelle und Bandniveau markieren den gespeicherten Lauf als veraltet. Eine reine Änderung des Bandniveaus kann aus vollständigen Einzelpaketen neu aggregiert werden, ohne neue Simulation, sofern die Signatur-/Freigaberegel dies ausdrücklich trennt.
- [ ] App-Orchestrator auf Windows mit begrenzter Prozesszahl und zeitlich begrenztem Schlafschutz; auf anderen Systemen ohne Windows-Aufruf. `N=1` umgeht ihn vollständig. Eingaben pro Worker fixieren, Seeds eindeutig zuweisen, Ergebnisse nach Seed sortieren, global aggregieren, Ressourcen im Fehlerfall freigeben. Kein teilweises Ensemble als fertiges Ergebnis speichern.
- [ ] Session-Vertrag: bisherige `compact_sim_*`-Objekte bleiben der konkrete Referenzlauf. Neue Ensembleobjekte werden unter eigenen Keys gespeichert und zusammen mit dem Referenzlauf invalidiert. Keine Voll-Snapshots aller Seeds in `st.session_state` halten.
- [ ] Abnahme 0: N=1-Differenztest gegen Tag `monte-carlo-calculation-v1`, N=2/N=10 exakte Paket- und Jahresparität, Fehler-/Abbruch-/Stale-Fälle, Prozess-Exit und Fingerprint. Leistung und Speicher für N=100 getrennt von UI-Rendering messen. **Ergebnis:** dokumentierter Daten- und Statusvertrag, noch keine sichtbare Monte-Carlo-Kennzahl.

## Seite 1: Simulationsparameter

Datei: `pages/8_⚙️_Simulationsparameter.py`; zugehörig `utils/simulation_params.py`.

- [ ] Oberhalb der fachlichen Tabs einen Abschnitt **„Simulationslauf“** einfügen: wirksamer Ist-Stichtag als Datenstand, Ziel-Datum, Anzahl Läufe und Seed-Anzeige. Keine zweite unabhängig wirkende Ziel-Datum-Eingabe in den Prognose-Tabs suggerieren. Die vorhandenen Abgangs-/Zugangs-Datumfelder klar als Einstellungen ihrer getrennten Prognosen kennzeichnen oder für CompactPlus aus der sichtbaren Laufsteuerung entfernen.
- [ ] Anzahl Läufe mit leicht zugänglichen Werten **1, 2, 10, 20, 30 …**; Standard 1. Kein Start beim Ändern eines Felds. Technisches Maximum und erwartete Laufzeit für höhere N transparent machen, ohne eine ungemessene Laufzeitgarantie.
- [ ] Seed als verständliche Referenzlauf-Einstellung präsentieren. Wenn die vorhandenen Abgangs- und Zugangs-Seeds verschieden sind, beide Werte in einem erweiterten Bereich sichtbar machen; bei N=1 bleibt das gespeicherte Paar unverändert. Bei N>1 die verwendete Folge kurz anzeigen, z. B. „Läufe 42–61“ nur wenn beide Seeds gleich sind, sonst beide Folgen.
- [ ] Dynamischen Text für „1 konkreter Lauf“ beziehungsweise „N Läufe mit Mittelwert und 80-%-Ergebnisbereich“ ergänzen. Speichern speichert nur Parameter; es rechnet nichts. Validierung für N, Ziel-Datum und Seed-Grenzen.
- [ ] Abnahme 1: Werte über Seitenwechsel/Rerun stabil; gespeicherte Werte steuern tatsächlich Seite 7; N=1 mit unveränderten Seeds erzeugt dieselbe Parameterübergabe; keine Berechnung durch Widgetänderung; verständliche deutsche Beschriftung im Browser geprüft. **Sichtbares Ergebnis:** einfache Laufkonfiguration mit eindeutigem Modus.

## Seite 2: CompactPlus-Simulation

Datei: `pages/7_⚡_Kompakt_plus_Simulation.py`; Export `utils/compact_simulation_export.py`.

- [ ] Ziel-Datum und Laufzahl aus der zentralen Konfiguration zeigen; nur eine autoritative Eingabestelle für beide. Button **„Simulation berechnen“** startet N=1 beziehungsweise N>1. „Ist-Stand auswerten“ bleibt ein einzelner Ausgangsbestand ohne Monte-Carlo-Band.
- [ ] N=1 ruft den bisherigen `simulate_compact_snapshot`-Pfad einschließlich Audits und Exportkontext unverändert auf. N>1 berechnet einen vollständigen Referenzlauf plus kompakte Pakete für die weiteren Seeds und zeigt Fortschritt `fertig/N`, Laufzeit und klare Fehlerbehandlung. Keine erneute Berechnung beim bloßen Seitenwechsel.
- [ ] Abschlusskarte: N, konkrete Seedfolge, Ziel-Datum, Daten-/Clusterfingerprint, Status und Banddefinition. Ziel-Köpfe/MAK und fachlich verfügbare Ereigniszahlen als Mittelwert plus P10/P90. Die konkrete Referenzzahl bleibt als „Referenzlauf“ abrufbar; Personen-/Planstellenansichten bleiben Referenzlauf. EUR-Ensemblekarte ausblenden oder als „noch nicht verfügbar“ kennzeichnen.
- [ ] Globale kombinierte Filter: Kennzahlen aus Personenfilter nur für Referenzlauf zeigen, solange keine exakte Mehrlauf-Filterschicht vorhanden ist. Ungefilterte Ensemblekennzahlen nicht neben gefilterte Referenzwerte ohne sichtbaren Kontext stellen. Keine Summe von Quantilen als gefiltertes Band bilden.
- [ ] Export zunächst klar trennen: bisheriger Excel-Export des konkreten Referenzlaufs und ein Ensemble-Kennzahlenexport mit Seeds, N, Banddefinition, Mittelwerten und Grenzen. Dateinamen und Metadaten müssen den Typ nennen; keine synthetischen Personendaten exportieren.
- [ ] Abnahme 2: kompletter N=1-Screenshot-/Wert-/Exportvergleich; N=2/10 UI-Kennzahlen exakt gegen Aggregator; N=100 Status, Fortschritt, Abbruch und Wiederaufruf; keine alten Ensemblewerte nach Parameteränderung; kontrollierter Speicherverbrauch. **Sichtbares Ergebnis:** eine vertraute CompactPlus-Seite mit zusätzlicher Verteilung bei N>1.

## Seite 3: Simulation abnehmen

Datei: `pages/12_✅_Simulation_abnehmen.py`; Vertrag `utils/simulation_validation.py`.

- [ ] Freigabekarte zeigt klar, **was** freigegeben wird: konkreter Referenzlauf mit Seed-Paar; bei N>1 zusätzlich Status und Vollständigkeit des zugehörigen Ensembles. Eine Freigabe eines mathematischen „Durchschnittspersonalbestands“ ist ausgeschlossen.
- [ ] Empfohlene erste Regel: manuelle personenbezogene Justierung nur für N=1. Bei N>1 wäre eine Justierung nur des Referenzlaufs inkonsistent mit unveränderten Ensemblebändern; deshalb im UI sperren und knapp erklären. Eine spätere Justierung aller Seed-Läufe wäre ein eigenes Fach- und Rechenprojekt.
- [ ] Unvollständige, fehlgeschlagene oder stale Ensembles nicht als freigegebene Mehrlauf-Auswertung ausgeben. Vorhandene N=1-Freigabefunktion unverändert lassen; neue Mehrlauf-Freigabe prüft Signatur, N, Vollständigkeit und Referenzstatus.
- [ ] Abnahme 3: N=1-Original/Justierung/Freigabe unverändert; N>1 Freigabe nur bei vollständigem Ensemble; Recompute und Parameteränderung invalidieren die Freigabe; Folgeseiten erhalten genau die freigegebene Version. **Sichtbares Ergebnis:** nachvollziehbare Freigabe mit sauberer Trennung von konkretem Lauf und Statistik.

## Seite 4: Organisationseinheiten-Simulation

Datei: `pages/10_🏢_Organisationseinheiten_Simulation.py`; Komponenten `components/org_simulation_yearly.py`, `components/org_simulation_stock.py`; Export `utils/org_simulation_export.py`.

- [ ] Bestehende Analyse mit `require_approval=True` beibehalten. Bei N=1 alle aktuellen Diagramme, Tabellen, Jahre und Exporte exakt erhalten.
- [ ] Für N>1 nur zertifizierte OE- und OE-Cluster-Jahresereignisse und Jahresbestände als Mittelwert plus P10/P90 ergänzen. Ausgangsstichtag ist gemeinsamer deterministischer Bestand; alle späteren Jahresscheiben müssen aus gleichen Seeds und Stichtagen stammen. Fehlende additive Ereignisgruppen je Seed als 0, undefinierte Quoten nicht als 0 behandeln.
- [ ] Auswahl Jahr, OE/OE-Cluster und Köpfe/MAK wirkt auf Referenz- und Ensembleansicht konsistent. Bänder für additive Ereignisse/Bilanz erst aus je Seed gebildeten Summen, nicht aus summierten Quantilen. Technische Restgrößen und Quoten nur nach eigener je-Seed-Fachzertifizierung anzeigen; bis dahin Referenzwert oder eindeutiger Verfügbarkeitsstatus.
- [ ] Tabellen- und Excel-Export mit sichtbaren Werten, Einheit, N, Seeds, Bandniveau und Stichtag abstimmen. Personen- und Planstellenexport bleibt konkret referenziert.
- [ ] Abnahme 4: **jede** OE-/OE-Cluster-Jahresscheibe N=1 byte-/wertgleich; N=2/10 je Zeile gegen Rohpakete; N=100 Stichproben samt Randgruppen/Nullgruppen; UI-Filter und sichtbarer Excel-Export deckungsgleich; kein Band bei uncertifiziertem Schlüssel. **Sichtbares Ergebnis:** OE-Jahresentwicklung mit nachvollziehbarem Band.

## Seite 5: Jobgruppen-Simulation

Datei: `pages/11_💼_Jobfamily_Simulation.py`; Komponente `components/jobfamily_simulation_yearly.py`.

- [ ] Dieselben Anzeige- und Statusregeln wie auf der OE-Seite für Jobfamily und JF-Cluster. N=1-Jahresscheiben und Export bleiben unverändert.
- [ ] Mehrfachplanstellen und Jobgruppenwechsel pro Seed respektieren. Köpfe in mehreren Jobgruppen sind nicht automatisch additive eindeutige Gesamtköpfe. Jahresereignisse und Bestände auf den vorhandenen zertifizierten Gruppenschlüsseln aggregieren; Gesamtsummen nur aus fachlich korrekten Einzel-Laufgrößen ableiten.
- [ ] Auswahl Jahr, Jobgruppe/JF-Cluster und Köpfe/MAK sowie Export gegen die identische Ergebnisversion prüfen. Nicht unterstützte kombinierte Personenfilter und Quoten sichtbar als Referenzlaufwerte kennzeichnen.
- [ ] Abnahme 5: **jede** JF-/JF-Cluster-Jahresscheibe N=1 exakt; N=2/10 Einzelpakete gegen UI; N=100 Mehrfachplanstellen-, Nullgruppen- und Wechselstichproben; Excel gegen sichtbare Tabellen. **Sichtbares Ergebnis:** Jobgruppen-Jahresentwicklung mit fachlich korrekten Bändern.

## Abschlussphase 6: seitenübergreifende Freigabe

- [ ] Durchgehender Nutzerweg: Parameter speichern → berechnen → abnehmen → OE/JF prüfen → exportieren; jeweils N=1, N=2, N=10 und N=100. Navigation und Reruns dürfen keine neue Simulation auslösen.
- [ ] Gemeinsame Status-/Bandtexte, Rundung, Farben, Legenden, Tooltips, Einheiten und Exportmetadaten auf allen Seiten konsistent. Bei N=1 erscheinen keine irreführenden Monte-Carlo-Hinweise.
- [ ] Referenz-Fingerprint der Produktdaten, Clusterversion, Seed-Paare und Paketversion in Reproduktionsmetadaten dokumentieren; Fehler-/Abbruch-/Stale-Fälle im Browser prüfen. Keine personenbezogenen Zusatzlaufdaten in UI-Session oder Kennzahlenexport.
- [ ] Zehn-Jahres-N=100-Lauf im integrierten App-Pfad mit zentraler Aggregation und Windows-Schlafschutz messen; kaltes Laden, Rechnung, Rendering und Excel getrennt ausweisen. Die bisherige Benchmarkzeit von 365,145 s ist ein Richtwert, keine garantierte App-End-to-End-Zeit.
- [ ] Finales Abnahmeprotokoll pro Seite mit Datum, Commit, getesteten Seeds/Parametern, N=1-Parität, N>1-Werten, bekannten Grenzen und Nutzerentscheidung. Erst danach Darstellungs-Branch nach `main` übernehmen.

## Fortschritt und Abnahmeprotokoll

| Phase | Status | Technische Prüfung | Sichtprüfung/Entscheidung |
| --- | --- | --- | --- |
| 0 Gemeinsamer Vertrag | in Arbeit: Laufvertrag, Datenfingerprint, Prozessrahmen und erster Produkt-Worker implementiert | 21 kombinierte Tests; N=2-Produktpakete inklusive aller vier Jahresdimensionen seriell/Spawn identisch und gegen vorhandenen Benchmark SHA-256-identisch (10.11.2026) | N=10/Mehrjahresparität, Zieltermine außerhalb Jahresende, Session und App-Anbindung offen |
| 1 Simulationsparameter | offen | – | – |
| 2 CompactPlus | offen | – | – |
| 3 Simulation abnehmen | offen | – | – |
| 4 Organisationseinheiten | offen | – | – |
| 5 Jobgruppen | offen | – | – |
| 6 Gesamtabnahme | offen | – | – |

Dieses Dokument wird nach jeder Phase mit Befunden, Änderungen, Testergebnissen und offenen Punkten fortgeschrieben. Eine Seite gilt erst mit ausgefüllter Tabellenzeile und expliziter fachlicher Abnahme als fertig.

### Phase 0, Schritt 3 – Prozess-Orchestrierungsrahmen (10.11.2026)

- `utils/monte_carlo_orchestrator.py` führt ausschließlich `N>1` über Windows-kompatible Spawn-Prozesse aus. Ein `RunPlan` weist jedem Laufindex das feste Abgangs-/Zugangs-Seed-Paar zu. Eingaben werden vor dem Start im Elternprozess und nach dem Start in jedem Worker gegen denselben Fingerprint geprüft. Der bestehende `N=1`-Produktpfad ist hiervon ausgeschlossen.
- Jeder Worker liefert ein signiertes Ergebnis oder einen Fehler. Die zentrale Finalisierung sortiert anhand des Laufindex und veröffentlicht Mittelwerte und Quantile nur bei vollständigem, gültigem Paketbestand. Fortschritt und Laufzeiten werden erfasst. Während der aktiven Rechnung verhindert ein Windows-Schlafschutz den Idle-Standby und wird anschließend freigegeben.
- Synthetische Spawn-Tests prüfen `N=2` und `N=10` gegen einzeln berechnete Seed-Werte sowie abweichende Worker-Eingaben, fehlgeschlagene Seeds, nachträglich geänderte Eltern-Eingaben, ungültige Kennzahlpakete und die Sperre für `N=1`. Diese Tests belegen den Orchestrierungsvertrag, noch keine Produktparität.
- **Offen:** Einen produktiven Worker-Adapter mit vollständig eingefrorenen Zusatzinputs bauen. Der Simulationskern liest derzeit Gehaltsautomatik und Ausschlüsse über `get_setting` sowie die Kostenlookup aus `st.session_state`; diese Quellen dürfen im Spawn-Worker nicht implizit fehlen oder abweichen. Danach N=2/N=10 mit tatsächlichen Produktpaketen und allen OE-/JF-Jahresscheiben gegen serielle Einzelpfade prüfen und erst dann die App-Session anbinden. Phase 0 und sämtliche Seiten bleiben bis dahin offen.

### Phase 0, Schritt 4 – produktiver Seed-Adapter und erster Paritätsnachweis (10.11.2026)

- `utils/monte_carlo_product_worker.py` kapselt den unveränderten Produktlauf und den bereits im Benchmark zertifizierten Jahresbestandsaufbau in einem aufrufbaren Worker. `freeze_product_worker_input` erfasst Snapshot, ATZ, Datumswerte, Clusterquelle/-bundle, Gehaltsautomatik, Ausschlüsse und die für die Finalisierung relevanten Session-Werte vor dem Spawn. Der Fingerprint umfasst Personal-/ATZ-Frame, Gehaltsautomatik, Session-Werte und Clusterbundle; Datum, Fachparameter, Ausschlüsse und Clusterquellensignatur gehören zusätzlich in die `RunPlan`-Signatur.
- Für den ersten Test wurden zwei Seeds mit aktiven Abgängen, Neueinstellungen, Azubis und Trainees bis 31.12.2026 berechnet. Die seriellen Produktpakete und das Spawn-Ensemble stimmen einschließlich aller Jahresereignisse und OE-, OE-Cluster-, Jobfamily- und JF-Cluster-Jahresbestände exakt überein. Die SHA-256-Prüfsummen beider vollständigen Pakete stimmen außerdem mit einem unabhängig erzeugten Lauf von `tests/benchmark_special_annual_many_seeds.py` überein: Seed 0 `c7dad96d…`, Seed 1 `6e603bab…`. Der kombinierte Vertrags-/Adapter-/Fingerprintlauf bestand mit **21/21 Tests**. Eine zunächst beobachtete Test-Reihenfolgeabhängigkeit durch globale Benchmark-Einstellungen wurde im Test-Fixture behoben.
- **Grenzen vor App-Anbindung:** Der Adapter verlangt derzeit einen Zieltermin am 31.12.; andere Zieltermine benötigen einen getrennt geprüften Jahresvertrag. N=10 und mehrere Jahre sind für diesen neuen Adapter noch gegen den unabhängigen Benchmark zu prüfen. Die tatsächliche Streamlit-Session, gespeicherte Einstellungen und die alte N=1-Ansicht wurden noch nicht angeschlossen oder visuell abgenommen. Die Plan-/Payload-Konsistenz für Ausschlüsse wird beim späteren App-Einstieg zentral gebaut und geprüft. Keine Seite gilt als fertig.

### Phase 0, Schritt 1 – Laufvertrag (10.11.2026)

- `utils/monte_carlo_run_contract.py` definiert einen versionierten `RunPlan` mit Eingabefingerprint, Ziel-/Basisdatum, vollständigen wirksamen Parametern, beiden Seedfolgen, N, Bandniveau, Clusterquellensignatur und daraus abgeleiteter Signatur. `params_for_index(0)` gibt Kopien der bisherigen beiden Parameterobjekte ohne Seed-Änderung zurück; weitere Indizes erhalten je Forecast `Ausgangsseed + Index`.
- `finalize_run_packets` akzeptiert nur vollständig vorhandene, eindeutige Indizes mit passender Signatur, passendem Datenfingerprint und richtigem Seed-Paar. Erst danach ruft der Vertrag die vorhandene Ensembleaggregation auf. Bei fehlenden, doppelten, fehlgeschlagenen oder unpassenden Rückgaben bleibt `statistics=None` und der Status `incomplete`. Die Funktion erzeugt keinen Personenbestand.
- `tests/test_monte_carlo_run_contract.py` prüft N=1-Parameterparität ohne Mutation, stabile unterschiedliche Seedfolgen, Signaturänderungen und vollständige/ungültige Rückgaben. Zusammen mit den bestehenden Ensemble- und Parameterprüfungen: **24 bestanden**. Keine Änderung an Streamlit-Seiten oder Produkt-Engine.
- Noch offen für Phase 0: einen belastbaren Datenfingerprint aus den tatsächlichen Eingaben bilden; produktfähige Prozess-Orchestrierung samt Windows-Schlafschutz und Fehler-/Abbruchstatus; Session-/Freigabe-Anbindung; voller N=1- und N=2/10-Paritätsdurchlauf über den appnahen Pfad. Schritt 1 ist technisch abgeschlossen, Phase 0 insgesamt ausdrücklich noch nicht abgenommen.

### Phase 0, Schritt 2 – Eingabefingerprint und Referenzbindung (10.11.2026)

- `utils/monte_carlo_input_fingerprint.py` bildet einen prozessstabilen SHA-256-Fingerprint aus vollständigem Snapshot und ATZ-Frame einschließlich Schema, Index, Zeilenfolge und Zellwerten. Optionale zusätzliche Recheninputs sind Gehaltsautomatik, Kostenlookup und Clusterzuordnung; ihre Werte gehen ebenfalls ein, sobald die App sie übergibt. Parameter, Datum, Ausschlüsse und Clusterquellensignatur werden separat durch `RunPlan.signature` gebunden. Unbekannte Objekttypen lösen einen Fehler aus statt eines instabilen `repr`-Hashes.
- Synthetische Änderungen an IST-MAK, ATZ-Datum, Zeilenfolge, Schema, Lookup und Clusterzuordnung verändern den Fingerprint. Unterschiedliche Mapping-Einfügefolge erzeugt denselben Fingerprint. Sowohl synthetische als auch die tatsächlich geladenen produktiven Eingaben ergeben in einem frisch gestarteten Python-Prozess denselben Fingerprint.
- Bei der erneuten historischen Referenzprüfung schlugen zunächst fünf ältere Frozen-Fixtures fehl. Diagnose: ATZ-Signatur unverändert (`5b60a67b40c23061`), Snapshot-Signatur verändert (`df4d8144a01d899a` → `78cbaa1c7a5c7be2`), weil die Clusterdatei bereits im Berechnungs-Commit `044bdae` von SHA-256 `39a4…` auf `52d1…` geändert wurde. Produktrechenpfade sind gegenüber dem Rücksprung-Tag `monte-carlo-calculation-v1` unverändert. Die alten Fixtures wurden **nicht überschrieben**.
- Für den aktuellen Clusterstand wurden zusätzliche Hash-only-Fixtures `monte_carlo_production_multi_year_reference_v5.json` und `monte_carlo_seed_reference_v2.json` mit `tests/capture_current_monte_carlo_references.py` erfasst. Der Capture-Skript verweigert abweichende Cluster- oder Produktcode-Stände und überschreibt keine vorhandene Referenz. Die zwei Referenztests wählen ausschließlich anhand des explizit bekannten vollständigen Clusterdatei-Hashes die alte oder neue Fixture; unbekannte Zuordnungen scheitern. Anschließend bestanden **18/18** Fingerprint-/Referenztests; der ergänzende echte Zwei-Prozess-Produktiveingabetest bestand separat (**1/1**).
- Inhaltliche Grenze: Diese Tests belegen die Identität aktueller Eingangsdaten über Prozesse und die vorhandenen eingefrorenen Seed-/Jahreswerte für den aktuellen Clusterstand. Sie belegen noch **nicht** die vollständige Streamlit-Mehrlauf-Orchestrierung oder alle 100 Seeds gegen serielle Einzelläufe. Als nächstes werden tatsächliche App-Zusatzeingaben an den Fingerprint angeschlossen und pro Worker vor dem Rechnen geprüft; danach folgt der appnahe Paritätslauf.
