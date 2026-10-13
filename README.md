# README

Core app folder.

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
