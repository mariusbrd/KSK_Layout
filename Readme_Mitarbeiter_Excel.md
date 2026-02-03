# Datensteckbrief – Mitarbeiter.xlsx

## Zweck und Inhalt

Die Datei **Mitarbeiter.xlsx** ist die **zentrale Stammdatendatei** für alle Mitarbeitenden. Sie enthält Identifikationsmerkmale, Beschäftigungszeiträume, Vertragsinformationen sowie tarifliche Einordnung und Statuskennzeichen.

**Aktueller Datenstand:** 1.222 Datensätze

### Wesentliche Anwendungsfälle
- Verknüpfung von Mitarbeitenden über **PersNr** mit anderen Dateien (ATZ, Planstellen, Ausbildung)
- Auswertung von Beschäftigungsstatus, Vertragsarten, Arbeitszeitumfang und Tarifmerkmalen
- Berechnung von MAK (Mitarbeiterkapazität) über BsGrd
- Grundlage für Alters- und Tenure-Analysen

---

## Wichtige Datenlogik und Besonderheiten

### Identifikation
- **Spalte A (PersNr)** ist die eindeutige numerische Mitarbeiter-ID (Primärschlüssel)
- Alle 1.222 PersNr sind eindeutig und verknüpfbar mit ATZ, Planstellen und Ausbildung

### Anonymisierung
- **Vorname** und **Nachname** sind vollständig leer (Anonymisierung)
- Auswertungen müssen auf **PersNr** basieren

### Eintritt und Austritt
- **Eintritt**: Vollständig gepflegt (Range: 01.08.1976 bis 01.12.2025)
- **Austritt**: Einheitlich **31.12.9999** = kein definierter Austritt / unbefristet
- Für Berechnungen: Austritt 9999-12-31 als `NULL` oder "offen" interpretieren

### Beschäftigungsgrad (BsGrd) → FTE/MAK
- **BsGrd** gibt den Ist-Arbeitszeitumfang in Prozent an (Wertebereich: 0–100)
- **FTE-Berechnung:** `FTE = BsGrd / 100`
- **Durchschnitt:** 85,1% | **Median:** 100%
- **Achtung:** BsGrd = 0 kommt vor (z.B. bei ruhenden Verhältnissen)

### Statuslogik (Kritisch für MAK-Berechnung)

| Spalte | Wert | Bedeutung | Anzahl |
|--------|------|-----------|--------|
| Bezeichnung | `aktiv` | Beschäftigungsverhältnis besteht | 1.222 (100%) |
| Status kundenindividuell | `Aktives Beschäftigungsverhältnis` | Arbeitet aktiv | 1.168 |
| Status kundenindividuell | `Ruhendes Beschäftigungsverhältnis` | Pausiert (Elternzeit, Sabbatical etc.) | 54 |

**⚠️ Wichtig:** "Ruhendes Beschäftigungsverhältnis" ist **NICHT** identisch mit ATZ-Freizeitphase!
- Die 54 Ruhenden sind nicht in ATZ.xlsx enthalten
- ATZ-Personen haben auch in der Freizeitphase "Aktives Beschäftigungsverhältnis"
- Ruhend = andere Gründe (Elternzeit, Sabbatical, Langzeitkrank etc.)

### MAK-Berechnung (empfohlen)
```python
def berechne_mak(row, atz_fr_persnr_set):
    if row['Status kundenindividuell'] == 'Ruhendes Beschäftigungsverhältnis':
        return 0
    if row['PersNr'] in atz_fr_persnr_set:  # ATZ-Freizeitphase aus ATZ.xlsx
        return 0
    return row['BsGrd'] / 100
```

---

## Spaltenbeschreibung

| Spalte | Name | Typ | Beschreibung | Werte/Beispiele |
|--------|------|-----|--------------|-----------------|
| A | PersNr | int | Eindeutige Mitarbeiter-ID | 28, 50, 112, ... |
| B | Vorname | - | Leer (Anonymisierung) | NaN |
| C | Nachname | - | Leer (Anonymisierung) | NaN |
| D | GebDatum | date | Geburtsdatum | 01.11.1961 |
| E | Text Gsch | string | Geschlecht | `männlich`, `weiblich` |
| F | Eintritt | date | Eintrittsdatum | 06.08.1984 |
| G | Austritt | date | Austrittsdatum | `9999-12-31` = unbefristet |
| H | BsGrd | float | Beschäftigungsgrad in % | 0.0 – 100.0 |
| I | Vertragsart | string | Vertragstyp | `Unbefristet`, `Altersteilzeit`, `Ausbildung`, `Zeitvertrag`, `Trainee`, `Werkstudentenvertrag` |
| J | MitarbKreisbez. | string | Mitarbeiterkreis | `Beschäftigte TVÖD`, `Vorstandsmitglied` |
| K | MitarbGruppenbez. | string | Mitarbeitergruppe | `Angestellte`, `Auszubildende`, `Vorstand` |
| L | Bankspezifisch | string | Bankspezifische Tätigkeit | `bankspezifisch`, `nicht bankspezifisch` |
| M | Bezeichnung | string | Vertragsstatus | `aktiv` (einziger Wert) |
| N | Status kundenindividuell | string | Aktivitätsstatus | `Aktives Beschäftigungsverhältnis`, `Ruhendes Beschäftigungsverhältnis` |
| O | Tarifarttext | string | Tarifart | `TVÖD`, `Auszubildende-VKA`, `Vorstandsvergütung` |
| P | Tarifgebiettext | string | Tarifgebiet | `Westdeutschland` (einziger Wert) |
| Q | TrfGr | string | Tarifgruppe | `E5`–`E15`, `E9A`–`E9C`, `TVAÖD`, `1` |
| R | St | string | Erfahrungsstufe | `1`–`6`, `2+` |

---

## Verteilung der Kategorien (Ist-Stand)

### Vertragsart
| Wert | Anzahl | Anteil |
|------|--------|--------|
| Unbefristet | 1.022 | 83,6% |
| Ausbildung | 131 | 10,7% |
| Altersteilzeit | 50 | 4,1% |
| Zeitvertrag | 15 | 1,2% |
| Werkstudentenvertrag | 2 | 0,2% |
| Trainee | 2 | 0,2% |

### Mitarbeitergruppe
| Wert | Anzahl | Anteil |
|------|--------|--------|
| Angestellte | 1.088 | 89,0% |
| Auszubildende | 131 | 10,7% |
| Vorstand | 3 | 0,2% |

### Geschlecht
| Wert | Anzahl | Anteil |
|------|--------|--------|
| weiblich | 771 | 63,1% |
| männlich | 451 | 36,9% |

---

## Empfohlene Datenbereinigungen

```python
import pandas as pd

df = pd.read_excel("Mitarbeiter.xlsx")

# 1. PersNr als String mit führenden Nullen (für Joins)
df['PersNr'] = df['PersNr'].astype(str).str.zfill(6)

# 2. Austritt 9999-12-31 auf None setzen
df['Austritt'] = pd.to_datetime(df['Austritt'])
df.loc[df['Austritt'].dt.year == 9999, 'Austritt'] = pd.NaT

# 3. Datumsfelder standardisieren
df['GebDatum'] = pd.to_datetime(df['GebDatum'])
df['Eintritt'] = pd.to_datetime(df['Eintritt'])

# 4. BsGrd numerisch validieren
df['BsGrd'] = pd.to_numeric(df['BsGrd'], errors='coerce')
df['FTE'] = df['BsGrd'] / 100

# 5. Alter und Tenure berechnen
stichtag = pd.Timestamp('today')
df['Alter'] = (stichtag - df['GebDatum']).dt.days / 365.25
df['Tenure_Jahre'] = (stichtag - df['Eintritt']).dt.days / 365.25

# 6. Aktiv-Flag konsolidieren
df['ist_aktiv'] = df['Status kundenindividuell'] == 'Aktives Beschäftigungsverhältnis'
```

---

## Verknüpfungen zu anderen Dateien

| Zieldatei | Join-Key | Hinweis |
|-----------|----------|---------|
| ATZ.xlsx | PersNr | 50 Personen verknüpfbar |
| Planstellen.XLSX | PersNr = Personalnummer | Alle 1.222 verknüpfbar |
| Ausbildung.xlsx | PersNr = Personalnummer | 1.205 Personen verknüpfbar |

---

## Fehlende Werte

| Spalte | Anzahl fehlend | Grund |
|--------|----------------|-------|
| Vorname | 1.222 | Anonymisierung (korrekt) |
| Nachname | 1.222 | Anonymisierung (korrekt) |
| St | 3 | Vorstand ohne Tarifstufe |
