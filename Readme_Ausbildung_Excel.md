# Datensteckbrief – Ausbildung.xlsx

## Zweck und Inhalt

Die Datei **Ausbildung.xlsx** enthält den **höchsten Bildungsabschluss** aller Mitarbeitenden. Sie dient der Qualifikationsanalyse und kann über die Personalnummer mit anderen Dateien verknüpft werden.

**Aktueller Datenstand:** 1.205 Datensätze

### Wesentliche Anwendungsfälle
- Qualifikationsstruktur-Analysen
- Identifikation von Mitarbeitenden in Ausbildung ("derzeit Berufsausbildung")
- Bildungsniveau als Input für Gehalts- oder Potenzialanalysen

---

## Wichtige Datenlogik und Besonderheiten

### Scope der Datei
- Enthält 1.205 von 1.222 Mitarbeitenden (98,6%)
- **17 Mitarbeitende** haben keinen Eintrag in dieser Datei

### Bildungsabschlüsse – Verteilung

| Abschluss | Anzahl | Anteil |
|-----------|--------|--------|
| SPK/Bankbetriebswirt | 250 | 20,7% |
| kfm Berufsabschluss | 227 | 18,8% |
| Bankberufsabschluss | 207 | 17,2% |
| Sparkassen/Bankfachwirt | 206 | 17,1% |
| derzeit Berufsausbildung | 107 | 8,9% |
| Bachelor FH | 91 | 7,6% |
| Master Universität | 36 | 3,0% |
| nicht kfm Berufsabschluss | 33 | 2,7% |
| Master FH | 19 | 1,6% |
| Studium Lehrinstitut | 12 | 1,0% |
| Bachelor Universität | 11 | 0,9% |
| ohne Berufsabschluss | 6 | 0,5% |

### Azubi-Identifikation: Diskrepanz beachten!

| Quelle | Kriterium | Anzahl |
|--------|-----------|--------|
| Mitarbeiter.xlsx | `MitarbGruppenbez. = 'Auszubildende'` | 131 |
| Ausbildung.xlsx | `BV Ausbildungsgruppentext = 'derzeit Berufsausbildung'` | 107 |
| **Differenz** | | **24** |

**Erklärung:** 24 Azubis haben in Ausbildung.xlsx bereits einen anderen Abschluss hinterlegt (z.B. vorheriger Berufsabschluss).

**Empfehlung:** Für Azubi-Auswertungen immer `MitarbGruppenbez.` aus Mitarbeiter.xlsx als führendes Kriterium verwenden.

---

## Spaltenbeschreibung

| Spalte | Name | Typ | Beschreibung | Beispiel |
|--------|------|-----|--------------|----------|
| A | Personalnummer | int | Mitarbeiter-ID (Join-Key) | 28, 50, 56 |
| B | Ausbildungsgruppe | int | Technischer Gruppenschlüssel | 1, 2, 3, ... |
| C | BV Ausbildungsgruppentext | string | Klartext des Bildungsabschlusses | "Bachelor FH" |
| D | Betriebsvergleich Ausbildung | int | Technischer Referenzschlüssel | 99560, 99200, ... |

---

## Empfohlene Datenbereinigungen

```python
import pandas as pd

df_ausb = pd.read_excel("Ausbildung.xlsx")

# 1. Personalnummer standardisieren
df_ausb['Personalnummer'] = df_ausb['Personalnummer'].astype(str).str.zfill(6)

# 2. Ausbildungsgruppentext trimmen
df_ausb['BV Ausbildungsgruppentext'] = df_ausb['BV Ausbildungsgruppentext'].str.strip()

# 3. Bildungskategorien gruppieren (Optional)
bildungs_mapping = {
    'Bachelor FH': 'Bachelor',
    'Bachelor Universität': 'Bachelor',
    'Master FH': 'Master',
    'Master Universität': 'Master',
    'Studium Lehrinstitut': 'Sonstiges Studium',
    'SPK/Bankbetriebswirt': 'Bankspezifische Weiterbildung',
    'Sparkassen/Bankfachwirt': 'Bankspezifische Weiterbildung',
    'Bankberufsabschluss': 'Berufsausbildung',
    'kfm Berufsabschluss': 'Berufsausbildung',
    'nicht kfm Berufsabschluss': 'Berufsausbildung',
    'derzeit Berufsausbildung': 'In Ausbildung',
    'ohne Berufsabschluss': 'Ohne Abschluss'
}
df_ausb['Bildungskategorie'] = df_ausb['BV Ausbildungsgruppentext'].map(bildungs_mapping)
```

---

## Hierarchie der Bildungsabschlüsse (Vorschlag)

Für Analysen kann eine Rangfolge hilfreich sein:

| Rang | Kategorie | Abschlüsse |
|------|-----------|------------|
| 6 | Master | Master Universität, Master FH |
| 5 | Bachelor | Bachelor Universität, Bachelor FH, Studium Lehrinstitut |
| 4 | Bankspez. Weiterbildung | SPK/Bankbetriebswirt, Sparkassen/Bankfachwirt |
| 3 | Berufsausbildung | Bankberufsabschluss, kfm/nicht kfm Berufsabschluss |
| 2 | In Ausbildung | derzeit Berufsausbildung |
| 1 | Ohne Abschluss | ohne Berufsabschluss |

```python
rang_mapping = {
    'Master Universität': 6, 'Master FH': 6,
    'Bachelor Universität': 5, 'Bachelor FH': 5, 'Studium Lehrinstitut': 5,
    'SPK/Bankbetriebswirt': 4, 'Sparkassen/Bankfachwirt': 4,
    'Bankberufsabschluss': 3, 'kfm Berufsabschluss': 3, 'nicht kfm Berufsabschluss': 3,
    'derzeit Berufsausbildung': 2,
    'ohne Berufsabschluss': 1
}
df_ausb['Bildungsrang'] = df_ausb['BV Ausbildungsgruppentext'].map(rang_mapping)
```

---

## Verknüpfung mit Mitarbeiter.xlsx

```python
df_ma = pd.read_excel("Mitarbeiter.xlsx")
df_ma['PersNr'] = df_ma['PersNr'].astype(str).str.zfill(6)

# Join
df_ma_mit_bildung = df_ma.merge(
    df_ausb[['Personalnummer', 'BV Ausbildungsgruppentext', 'Bildungskategorie']],
    left_on='PersNr',
    right_on='Personalnummer',
    how='left'
)

# Mitarbeitende ohne Bildungseintrag identifizieren
ohne_bildung = df_ma_mit_bildung[df_ma_mit_bildung['BV Ausbildungsgruppentext'].isna()]
print(f"Mitarbeitende ohne Bildungseintrag: {len(ohne_bildung)}")  # 17
```

---

## Verwendung im Forecast

### Qualifikationsstruktur über Zeit
```python
# Bildungsstruktur nach Altersgruppe
df_ma_mit_bildung['Altersgruppe'] = pd.cut(
    df_ma_mit_bildung['Alter'], 
    bins=[0, 30, 40, 50, 60, 100],
    labels=['<30', '30-39', '40-49', '50-59', '60+']
)

bildung_nach_alter = pd.crosstab(
    df_ma_mit_bildung['Altersgruppe'], 
    df_ma_mit_bildung['Bildungskategorie'],
    normalize='index'
)
```

### Ausbildungsabschluss-Prognose
Für die Prognose von Azubi-Übernahmen:
```python
# Azubis (nach Mitarbeiter.xlsx) mit aktuellem Bildungsstand
azubis = df_ma_mit_bildung[df_ma_mit_bildung['MitarbGruppenbez.'] == 'Auszubildende']
print(azubis['BV Ausbildungsgruppentext'].value_counts())
```

---

## Hinweis: Keine Zeitangaben

Die Datei enthält **keine Datumsinformationen** zum Bildungsabschluss:
- Kein Abschlussdatum
- Kein Ausbildungsbeginn/-ende

Für Azubi-Dauer-Berechnungen muss daher auf `Eintritt` aus Mitarbeiter.xlsx zurückgegriffen werden:
```python
# Annahme: Ausbildungsdauer = 3 Jahre ab Eintritt
azubis['Ausbildungsende_erwartet'] = azubis['Eintritt'] + pd.DateOffset(years=3)
```

---

## Kennzahlen (Ist-Stand)

| Kennzahl | Wert |
|----------|------|
| Mitarbeitende mit Bildungseintrag | 1.205 (98,6%) |
| Akademikerquote (Bachelor+Master) | 13,1% |
| Bankspez. Weiterbildung | 37,8% |
| Berufsausbildung | 38,8% |
| In Ausbildung | 8,9% |
