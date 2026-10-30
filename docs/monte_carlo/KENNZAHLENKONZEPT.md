# Kennzahlenkonzept für den schlanken Monte-Carlo-Zusatzlauf

Stand: 30.10.2026 · Konzept, noch keine Produktimplementierung

## 1. Architektur und Ergebnisobjekte

Ein Ensemble besteht aus einem **vollständigen Referenzlauf** (Durchgang 0) und `N−1` zusätzlichen Läufen. `N=1` führt weiterhin ausschließlich durch den heutigen Pfad. Ein Zusatzlauf trifft dieselben fachlichen Entscheidungen mit seinem Seed, liefert aber ein kompaktes, je Lauf prüfbares Kennzahlenpaket. Es enthält keine Personenliste für die Anzeige, kein Freigabeobjekt und keine Excel-Datei.

Pipeline: unveränderliche Eingaben vorbereiten → Referenzlauf vollständig berechnen → Zusatzläufe berechnen und je Lauf Kennzahlen ableiten → Werte pro Kennzahlschlüssel über Läufe ausrichten → Mittelwerte und empirische Quantile bilden → Ansicht und Export einmalig aus Referenzlauf und Ensemble-Statistik erzeugen. Die letzte Stufe darf keine erneute Simulation auslösen.

Ein kompakter Kennzahlschlüssel lautet `(Jahr/Stichtag, Ebene, Gruppe, Ereigniskategorie, Ereignisart, Kennzahl)`. Nicht benötigte Teile werden ausdrücklich als „Gesamt“ beziehungsweise „Alle“ codiert. Gespeichert werden pro Durchgang Seed-Paar, Ergebnisversion, Laufstatus, ein Wertarray pro definierter Schlüsselmenge und Prüfstatus. Die konkrete Binär-/Tabellenrepräsentation wird nach dem Prototyp gewählt; 10.000 vollständige Snapshots bleiben ausgeschlossen.

## 2. Vorläufiger Kennzahlenvertrag

| Bereich | Wert pro Durchgang | Quelle im vollständigen Lauf | Ensemble-Anzeige |
| --- | --- | --- | --- |
| CompactPlus Zielbestand | eindeutige Köpfe, MAK, EUR und Differenz zum Status quo nach derselben Filterregel | finaler aufbereiteter Snapshot; `_metric_totals` auf Seite 7 | Mittelwert und Band je Kennzahl; Referenzwert separat |
| CompactPlus Ereigniszahl | Anzahl Abgangs-/Zugangsereignisse und fachliche Typen | Ergebnis-Metadaten und gespeicherte Ereignisse | Mittelwert/Band der **Zählwerte**; keine „gemittelten Personen“ |
| OE und OE-Cluster Jahresereignisse | `Köpfe-Veränderung`, `MAK-Veränderung`, `Abgänge`, `Zugänge` pro Jahr, Gruppe, Kategorie und Ereignisart | `annual_oe` / `annual_cluster` in `utils/org_simulation_events.py` | Mittelwert/Band je Schlüssel; Auswahl von Jahren/Typen aus Laufwerten ableiten |
| Jobfamily und JF-Cluster Jahresereignisse | dieselben vier Größen pro Jahr, Gruppe, Kategorie und Ereignisart | `annual` aus `build_jobfamily_yearly_view` | Mittelwert/Band je Schlüssel; Original- und Zielzuordnung sowie Mehrfachplanstellen je Lauf erhalten |
| OE Jahresbestände | Köpfe/MAK pro Ausgangs- und Jahresstichtag samt technischem Effekt und Endwertprüfung | `stock_reconciliation.annual/checks` | Band **nur**, wenn der Bestand für jeden verwendeten Lauf fachlich zertifiziert ist; sonst Verfügbarkeitsstatus statt Band |
| JF Jahresbestände | Köpfe/MAK pro Ausgangs- und Jahresstichtag samt `stock_checks` | `stock` / `stock_checks` beider JF-Ansichten | dieselbe Zertifizierungsregel; Gruppenkopfzahlen können bei Mehrfachzuordnung nicht additiv sein |
| OE-/JF-Analyse-KPIs | Frauen-, ATZ-, Teilzeitquote, durchschnittliche MAK, Kosten pro Kopf/MAK, größte Gruppe | `_build_kpis` der Analyse-Seiten auf gefiltertem Endbestand | erst nach eigenem Definitions-/Filtertest als Ensemble-KPI; Quoten **pro Lauf** berechnen, Ranglisten als Gruppenwahrscheinlichkeit statt Mittelwert eines Namens prüfen |
| Personendetails, Audits, Korrekturen, Freigabe | konkreter Personen- und Ereignisstand | Referenzlauf | ausschließlich Referenzlauf; kein Ensemble-Personenbestand und keine Freigabe eines Mittelwerts |

**Erster Implementierungsumfang:** CompactPlus Zielbestand/Ereigniszahlen und die vier Ebenen der Jahresereignisse. Jahresbestandsbänder sind ein eigener nachgelagerter Umfang, sobald die Zertifizierung je Lauf geklärt ist. Die heutigen OE-/JF-Analyse-KPI-Karten bleiben im ersten Schritt Referenzlaufwerte und erhalten erst nach eigener Filter-/Quotenparität Ensemble-Werte. Das verhindert eine scheinbar vollständige, fachlich ungesicherte Seite.

## 3. Jahresscheiben und Verfügbarkeit

Die bestehenden OE- und JF-Jahresereignisse sind für denselben Seed **zeilen- und wertgleich** zur vollständigen Simulation abzuleiten. Für Zwischenjahre, Teiljahre, `UNMAPPED`/„Nicht zugeordnet“, ATZ-/Ruhend-Effekte und Mehrfachplanstellen gelten die aktuellen Zuordnungs- und Vorzeichenregeln. Kopfzahlen über mehrere JF-Gruppen dürfen nicht automatisch zum eindeutigen Gesamtbestand summiert werden.

Zertifizierte Jahresbestände sind heute bei aktivierten Zugangsbausteinen nicht verfügbar. Außerdem hat der produktive Referenzfall ohne Zugänge bereits den OE-Status „Abweichung“. Ein Zusatzlauf darf daraus keine scheinbar zertifizierte Jahreskurve erzeugen. Ein Bestandsband wird nur aus einer einheitlich definierten Menge **vollständig zertifizierter** Läufe berechnet; die Behandlung nicht zertifizierter Läufe ist vor Freigabe fachlich festzulegen. Bis dahin bleibt das Band für diese Konstellation gesperrt, und der Referenzstatus sichtbar.

## 4. Filter, Statistik und Darstellung

Die globalen Seitenfilter umfassen OE, Jobfamily, OE-/JF-Cluster, Geschlecht, Arbeitszeit und ATZ-Status. Ein vorab nur nach OE oder JF aggregiertes Ergebnis kann kombinierte Personenfilter im Allgemeinen nicht exakt nachbilden. Für den ersten Prototyp sind Ensemble-Bänder daher auf **ungefilterte Gesamtwerte und die expliziten Jahresgruppenfilter** begrenzt. Bevor globale Kombinationsfilter für Bänder freigegeben werden, muss entweder je Lauf eine kompakte, ausreichend feine gemeinsame Gruppierung vorliegen oder bei Filteränderung aus einem reproduzierbaren Laufzustand exakt neu aggregiert werden. Die mögliche Kardinalität und Laufzeit sind zu messen; ein bloßes Zusammenrechnen marginaler Gruppen ist unzulässig.

Fehlende Gruppen werden über die Vereinigung der Gruppen aller Läufe ausgerichtet. Bei additiven Ereigniszählwerten bedeutet „im Lauf kein Ereignis“ den Wert 0; bei nicht definierten Quoten, fehlender Zertifizierung oder nicht bestehender Gruppe wird kein stillschweigender Nullwert eingesetzt. Mittelwerte und empirische Quantilbänder entstehen **je Kennzahl nach der Berechnung pro Lauf**. Quantile addieren sich nicht über Gruppen oder Ereignisarten; gefilterte Summen müssen je Lauf zuerst gebildet werden. Die Bezeichnung lautet Prognose-/Szenarioband, nicht Konfidenzintervall des Mittelwerts. Bandniveau und konkrete Quantilinterpolation sind noch fachlich festzulegen.

## 5. Paritäts- und Leistungsgates

1. Für `N=1`: vollständiger Vergleich mit dem heutigen Ergebnis, einschließlich Personen-/Planstellenbestand, OE-/JF-Jahresscheiben, UI-Ansichten und Exporten.
2. Für jeden geprüften Zusatzseed: kompakte Kennzahlen gegen dieselben Werte aus einem vollständigen Lauf **exakt** vergleichen; Rohereignisse, Jahresereignisse, Endwerte und Verfügbarkeitsstatus prüfen. Zunächst synthetische Spezialfälle, danach produktive Eingaben und verschiedene Seeds.
3. Zeit separat erfassen: Eingabevorbereitung, Referenzlauf, Zusatzlauf-Rechenkern, Kennzahlenableitung, Ensemble-Statistik, Ergebnisaufbau, Rendering und Export. Die 20-s-Grenze bezieht sich auf Vorbereitung bis Ensemble-Statistik einschließlich Referenzlauf; die UI wird zusätzlich ausgewiesen. Ist der Referenzlauf allein länger als das Budget, ist das Gesamtziel ohne dessen Beschleunigung unerreichbar.
4. Stufen 1, 10, 100, 1.000 und 10.000 Läufe auf definierter Hardware messen; Kalt-/Warmstart, RAM-Spitze und Ergebnisparität dokumentieren. Derzeitige rund 0,55 s der Abgangsprognose pro Lauf zeigen, dass eine reine Trennung von Darstellung und Rechnung das 2-ms-Budget je Lauf nicht erfüllt.

## 6. Offene fachliche Entscheidungen vor UI-Implementierung

- Standardband (z. B. P10–P90), Quantilinterpolation und Rundung.
- Seed-Ableitung für zusätzliche Läufe bei unverändertem Seed des Referenzlaufs.
- Verhalten bei fehlgeschlagenen/nicht zertifizierten Läufen und Mindestzahl gültiger Läufe je Kennzahl.
- Freigabe und Export eines Ensembles gegenüber dem konkreten Referenzlauf.
- Ob und wie kombinierte globale Filter für Ensemble-Bänder unterstützt werden sollen.
- Zielhardware, Mehrnutzerlast und genaue 20-s-Messgrenze einschließlich kalter Eingabevorbereitung.
