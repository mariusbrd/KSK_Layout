# OE-Exportinventur: „Veränderungen pro Jahr“ (17.11.2026)

## Geltungsbereich

Unter der Überschrift gibt es zwei separate Excel-Buttons: `OE_Bestandsverlauf.xlsx` im Block „Verbleibender Bestand“ und `OE_Veraenderungen.xlsx` im Block „Veränderungen“. Daneben existiert der separate Gesamtexport der OE-Seite; er ist nicht einer dieser beiden Buttons. Jeder Jahres-Export enthält zusätzlich `00_Dokumentation`, `Exportinventar`, `Parameter`, `Lineage_Report`, `Input_Lineage` und `Transformations_Lineage`. Die nummerierten Datenblätter beginnen bei `01_` nach der Dokumentation. Excel-Namen werden bei 31 Zeichen abgeschnitten; im `Exportinventar` stehen die vollständigen Bereichsnamen.

Die folgenden Listen stammen aus einem vollständig abgestimmten Referenzlauf mit aktiviertem Monte-Carlo-Bestand. Der Ereignisfall wurde mit Monte-Carlo-Jahreswerten und drei Ereignisarten aufgebaut. Je nach tatsächlicher Jahres-, OE- und Ereignisauswahl können weitere oder weniger Abgangsmatrizen, ATZ-, Kündigungs- und Rentenblätter entstehen.

## Historischer Stand vor der Vier-Block-Erweiterung: Bestand, 23 Datenblätter

| Nr. | Bereich |
|---:|---|
| 01 | Ref_Bestand_Folienvorlage_MAK |
| 02 | Ref_Bestand_Folienvorlage_Koepfe |
| 03 | MC_Jahresbestand_Auswahl |
| 04 | Bestand_Auswahl_Grafik |
| 05 | Bestand_Auswahl_Matrix |
| 06 | Ref_Bestand_Auswahl_Grafik |
| 07 | Ref_Bestand_Auswahl_Matrix |
| 08 | Ref_Bestand_Auswahl_Summen |
| 09 | Ref_Bestand_Endwertabgleich |
| 10 | Ref_Bestand_Prognosebasiseffekte |
| 11 | Ref_Bestand_Reportingereignisse |
| 12 | Ref_Bestand_Ausgangsbasis_Lauf |
| 13 | Ref_Bestand_Endbasis_Lauf |
| 14 | Ref_Bestand_Überleitung_vollständig |
| 15 | MC_Jahresbestand_Info |
| 16 | Jahreszeiträume |
| 17 | OE_Cluster_Quelldefinition |
| 18 | Ref_Jahre_OE_vollständig |
| 19 | Ref_Jahre_Cluster_vollständig |
| 20 | Ref_Gesamt_OE_Ereignisse |
| 21 | Ref_Gesamt_Cluster_Ereignisse |
| 22 | Ref_Jahres_Ereignisbasis |
| 23 | Ref_OE_Cluster_Laufzuordnung |

## Historischer Stand vor der Vier-Block-Erweiterung: Veränderungen, 27 Datenblätter

| Nr. | Bereich |
|---:|---|
| 01 | Ref_Bestand_Folienvorlage_MAK |
| 02 | Ref_Bestand_Folienvorlage_Koepfe |
| 03 | MC_Jahresereignisse_Auswahl |
| 04 | Jahresauswahl_Matrix |
| 05 | Jahresauswahl_Summen |
| 06 | Jahresauswahl_Grafik |
| 07 | Jahresauswahl_Details |
| 08 | MC_Jahresbestand_Auswahl |
| 09 | Jahreszeiträume |
| 10 | OE_Cluster_Quelldefinition |
| 11 | MC_Jahresereignisse_Info |
| 12 | Ref_Jahresdetails |
| 13 | Ref_Jahressummen |
| 14 | Ref_Jahre_OE_vollständig |
| 15 | Ref_Jahre_Cluster_vollständig |
| 16 | Ref_Gesamt_OE_Ereignisse |
| 17 | Ref_Gesamt_Cluster_Ereignisse |
| 18 | Ref_Jahres_Ereignisbasis |
| 19 | Ref_OE_Cluster_Laufzuordnung |
| 20 | MC_Jahresbestand_Info |
| 21 | Abgang_Rente (nach ATZ) |
| 22 | Referenzlauf_Abgang_Rente (nach ATZ) |
| 23 | Abgang_Ruhend (Rückkehr) |
| 24 | Referenzlauf_Abgang_Ruhend (Rückkehr) |
| 25 | Abgang_Ruhend (Start) |
| 26 | Referenzlauf_Abgang_Ruhend (Start) |
| 27 | Abgangsmatrizen_Verzeichnis |

## Redundanzprüfung und Entscheidung

- Im geprüften Bestands-Workbook sind keine zwei Datenblätter als vollständige DataFrames identisch. Die Sortierung wurde geändert, nicht die Daten oder Anzahl der Blätter.
- `MC_Jahresbestand_Auswahl` und `Bestand_Auswahl_Grafik` enthalten überlappende Werte; das erste Blatt hat zusätzlich den Referenzwert, das zweite ist die sichtbare Grafikauswahl. `Bestand_Auswahl_Matrix` ist dieselbe Auswahl als Jahresmatrix. Diese unterschiedlichen Ansichten sind für Nachvollziehbarkeit und direkte Folienarbeit nützlich.
- `Jahre_*`, `Gesamt_*` und `Jahres_Ereignisbasis` haben verschiedene Granularität. Die `Ref_`-Bestandsblätter dokumentieren den vollständigen Einzelpfad und die technische Überleitung. Sie sind nicht durch die MC-Mittelwertauswahl ersetzbar.
- `MC_*_Info` wiederholt Metadaten teilweise aus der Dokumentation und ist ein möglicher Kandidat für einen späteren schlanken Exportmodus. Ohne ausdrücklichen fachlichen Verzicht auf eigenständige Metadatenblätter wird es nicht gelöscht.
- Die beiden `Ref_Bestand_Folienvorlage`-Blätter enthalten den vollständigen Seed-0-Pfad. Sie sind auch nach der Verschiebung keine Mittelwert-Folienvorlage. Im Ereignis-Export steht diese Einschränkung jetzt zusätzlich in `00_Dokumentation`.

## Aktueller Stand: vier Bestands- und Fluktuationsblöcke

Bei einem Mehrlauf mit abgestimmtem Jahresbestand und gespeicherten berichtswirksamen Kündigungsverlusten stehen nun auf den nummerierten Blättern 01/02 jeweils vier durch Leerzeilen getrennte Blöcke: (1) Mittelwert aller Läufe mit P10/P90 je Jahreswert, (2) vollständiger Lauf mit maximaler Kündigungszahl, (3) typischer vollständiger Lauf mit geringstem Abstand zum Ensemble-Jahrespfad, (4) vollständiger Lauf mit minimaler Kündigungszahl. MAK steht vor Köpfen. Jede Laufzeile trägt Laufindex und beide Seeds. Je Block werden tatsächlicher verbleibender Bestand, Bestandsrückgang zum Vorjahresstichtag, jährlicher und kumulierter berichtswirksamer Kündigungsverlust gezeigt. Die Perzentile werden erst nach Aggregation innerhalb jedes Laufs berechnet. Der tatsächliche Bestand enthält alle Abgangs- und technischen Effekte; die kumulierten Kündigungsverluste sind keine kontrafaktische Neuberechnung ohne Kündigungen.

Die bisherigen vollständigen Seed-0-Folienvorlagen mit Rente, ATZ und Ruhend stehen als `Ref_`-Audit auf 03/04. Ein Einzellauf erzeugt weiterhin nur seine bisherige MAK- und Köpfe-Folienvorlage auf 01/02. Wenn die zertifizierten Mehrlauf-Bestands- oder Kündigungswerte fehlen, wird keine zusammengesetzte Vier-Block-Tabelle aus ungeeigneten Ersatzwerten erzeugt.

Aktuelle Reihenfolge des **Bestands-Buttons** im geprüften Mehrlauffall: 01 `Bestand_Folienvorlage_MAK`, 02 `Bestand_Folienvorlage_Koepfe`, 03 `Ref_Bestand_Folienvorlage_MAK`, 04 `Ref_Bestand_Folienvorlage_Koepfe`, 05 `MC_Jahresbestand_Auswahl`, 06 `Bestand_Auswahl_Grafik`, 07 `Bestand_Auswahl_Matrix`, 08 `Ref_Bestand_Auswahl_Grafik`, 09 `Ref_Bestand_Auswahl_Matrix`, 10 `Ref_Bestand_Auswahl_Summen`, 11 `Ref_Bestand_Endwertabgleich`, 12 `Ref_Bestand_Prognosebasiseffekte`, 13 `Ref_Bestand_Reportingereignisse`, 14 `Ref_Bestand_Ausgangsbasis_Lauf`, 15 `Ref_Bestand_Endbasis_Lauf`, 16 `Ref_Bestand_Überleitung_vollständig`, 17 `MC_Jahresbestand_Info`, 18 `Jahreszeiträume`, 19 `OE_Cluster_Quelldefinition`, 20 `Ref_Jahre_OE_vollständig`, 21 `Ref_Jahre_Cluster_vollständig`, 22 `Ref_Gesamt_OE_Ereignisse`, 23 `Ref_Gesamt_Cluster_Ereignisse`, 24 `Ref_Jahres_Ereignisbasis`, 25 `Ref_OE_Cluster_Laufzuordnung`.

Aktuelle Reihenfolge des **Ereignis-Buttons** im geprüften Mehrlauffall: 01/02 neue Vier-Block-Folienvorlagen, 03/04 Seed-0-Folienvorlagen, 05 `MC_Jahresereignisse_Auswahl`, 06 `Jahresauswahl_Matrix`, 07 `Jahresauswahl_Summen`, 08 `Jahresauswahl_Grafik`, 09 `Jahresauswahl_Details`, 10 `MC_Jahresbestand_Auswahl`, 11 `Jahreszeiträume`, 12 `OE_Cluster_Quelldefinition`, 13 `MC_Jahresereignisse_Info`, 14 `Ref_Jahresdetails`, 15 `Ref_Jahressummen`, 16 `Ref_Jahre_OE_vollständig`, 17 `Ref_Jahre_Cluster_vollständig`, 18 `Ref_Gesamt_OE_Ereignisse`, 19 `Ref_Gesamt_Cluster_Ereignisse`, 20 `Ref_Jahres_Ereignisbasis`, 21 `Ref_OE_Cluster_Laufzuordnung`, 22 `MC_Jahresbestand_Info`, 23–28 ereignisartabhängige Abgangsmatrizen, 29 `Abgangsmatrizen_Verzeichnis`. Die Zahl der Ereignisartblätter variiert.

Prüfung: 53 angrenzende Export-/Jahrestests und 2 gezielte Vier-Block-Vertragstests bestanden. Ein echter CompactPlus-Zwei-Lauf bestätigt die Seed-0-Werte für MAK und Köpfe (1/1 Produkttest). Vollständige vier unterschiedliche Szenarien sind bei nur zwei Läufen definitionsgemäß nicht möglich; der Drei-Lauf-Vertragstest prüft unterschiedliche Best-/Typisch-/Worst-Indizes.

Stichtagsregel: Wenn der Ausgangsstichtag bereits der 31.12. eines ausgewählten Jahres ist, zeigt die neue Vier-Block-Tabelle für dieses Jahr den festen Ausgangsbestand und null zusätzliche Jahresverluste. Es wird kein fiktiver weiterer Prognoseschritt erzeugt. Die ältere Seed-0-Auditvorlage bleibt tabelleninhaltlich unverändert und kann in diesem Grenzfall den Ausgangsstichtag als separate Spalte anders aufbereiten.
