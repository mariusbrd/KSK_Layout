# README

Core app folder.

## Kündigungsquoten mit Excel bearbeiten

Auf **Prognose Abgänge** und **Simulationsparameter** steht unter der detaillierten
Kündigungsmatrix der Bereich **Quoten mit Excel bearbeiten** bereit:

1. Dimension auswählen (Organisationseinheit oder Jobfamily) und Vorlage herunterladen.
2. Quoten im Tabellenblatt `Quoten` ändern: `3` bedeutet 3 % pro Jahr, `0,5` bedeutet 0,5 %.
3. Datei hochladen, die Vorschau prüfen und **Quoten übernehmen** klicken.
4. Auf der Abgangsseite berechnen bzw. auf der Parameterseite **Parameter speichern** klicken.

Die einfache Vorlage enthält eine Quote je Gruppe, die für alle vier Altersgruppen
gilt. Sind bereits unterschiedliche Altersquoten eingestellt, wird die detaillierte
Vorlage vorbelegt. Wer dann die einfache Vorlage auswählt, erhält für diese Gruppen
leere Quoten und trägt die gewünschte einheitliche Quote selbst ein.

Teilimporte ändern ausschließlich enthaltene Gruppen. Nicht benötigte Zeilen komplett
entfernen. Leere Quoten, unbekannte Gruppen, Duplikate und Werte außerhalb von 0–100 %
verhindern die gesamte Übernahme. Gruppennamen und führende Nullen unverändert lassen.
`Default` ist weiterhin der Rückfallwert. Die Parameterstände der beiden Seiten bleiben
getrennt; der Upload verändert keine Original-Personaldaten.

## Dynamische Rentenaltersgrenzen

In den Renten-Parametern von **Prognose Abgänge** und **Simulationsparameter** sind
**Renteneintritt ab Alter** und **Frühverrentung ab Alter** einstellbar. Die Obergrenze
der Frühverrentung ergibt sich automatisch: Bei 67/62 gilt sie für 62–66 Jahre,
die reguläre Renteneintrittsquote gilt ab 67. Eine Zusammenfassung zeigt die Bereiche
und jährlichen Wahrscheinlichkeiten; `0,90` im bisherigen Quotenregler bedeutet 90 %.

Die Altersfelder verändern die Wahrscheinlichkeitsbereiche, keinen zwingenden
Austrittstermin. ATZ-Vertragsenden bleiben separat. Frühverrentung muss vor dem
regulären Renteneintritt beginnen; ungültige Bereiche lassen sich nicht berechnen
oder speichern. Standard und Rückfallwerte für ältere Parameterstände bleiben 65/60.

Die neuen Alterswerte werden über die bisherigen Berechnungs-/Speicheraktionen
übernommen. Gespeicherte Simulationsparameter gehen an CompactPlus und werden in
dessen Parameterexport dokumentiert. Prüfberichte mit technischen Namen wie
`65plus_Audit` verwenden die eingestellte Rentengrenze; das Excel-Readme erläutert sie.

Alle Wahrscheinlichkeitsregler auf den beiden Parameterseiten verwenden Schritte
von **0,25 Prozentpunkten** (`0,0025` auf der Skala 0–1). Prozentmatrizen verwenden
`0,25` und zwei Nachkommastellen. Die Regler zeigen vier Nachkommastellen; ein Wert
von `0,0275` entspricht 2,75 %. Bestehende Werte und Berechnungsregeln werden nicht gerundet.

Read:

1. `app.py`
2. `pages/`
3. `dataloader/`, `abgaenge/`, `zugaenge/`, `utils/`
4. `tests/TESTS_OVERVIEW.md`

Use this folder for:

- product code
- page logic
- loaders, forecasts, shared helpers

Skip first:

- `pages/archive/`
- `User-Tests/` unless ATZ or user bug work

Long archived version: `../_archive/navigation_docs/KSK_Layout_README_full_2026-04-21.md`

## Optionale Exklusion nach MA-Typ

Auf der Exklusionsgruppen-Seite kann zwischen bisherigen Gruppenregeln,
Mitarbeitertypen und der Kombination beider Grundlagen gewechselt werden.
Die Seite gliedert sich in „Ausschlüsse festlegen“ (Auswahl und Vorschau),
„Auswirkungen“ (angewendete Kennzahlen und Gegenüberstellung) und
„Gruppen untersuchen“ (Profile und Einzelzeilen). Die beiden Auswertungstabs
zeigen stets die zuletzt angewendete Auswahl, nicht den noch offenen Entwurf.
Angekreuzte Gruppen bzw. ausgewählte MA-Typen werden ausgeschlossen. Änderungen
wirken erst nach „Änderungen anwenden“; inaktive Auswahlen bleiben gespeichert.

Die optionale Spalte `MA-Typ` stammt aus der Planstellen-Datei. Fehlt sie, bleibt
die gespeicherte Auswahl sichtbar, trifft aber keine Zeilen. Leere Werte werden
nicht als eigener Typ behandelt. Im kombinierten Modus genügt ein Treffer aus
einer der beiden Grundlagen. Die Vorschau zählt Planstellenzeilen, keine Personen.

Die Einstellungen `selection_basis` (`legacy`, `ma_typ`, `combined`) und
`ma_typ_values` ergänzen den bestehenden `exclusions`-Block. Ohne diese Felder
bleibt das alte Verhalten aktiv. Beide Grundlagen verwenden `apply_exclusions`;
die bisherige Ausschlusswirkung und die bestehenden Unterschiede zwischen
Soll-Auswertungen werden nicht umgestellt. MA-Typ-Ausschlüsse gelten als
personenbezogene Ausschlüsse und überstimmen damit die Ist-Erhaltung rein
technischer Sonderplanstellen.

Kompatibilitäts- und Oberflächentests: `tests/test_ma_typ_exclusions.py`.
