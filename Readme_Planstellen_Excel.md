# Datensteckbrief – Planstellen.XLSX

## Zweck und Inhalt

Die Datei **Planstellen.XLSX** enthält alle **Planstellen** des Unternehmens mit Organisationszuordnung, Sollarbeitszeit, Tarifbewertung und Besetzungsstatus.

**Aktueller Datenstand:** 1.728 Datensätze (+ 1 Summenzeile zum Entfernen)

### Wesentliche Anwendungsfälle
- Ermittlung offener vs. besetzter Stellen
- Soll-Kapazitätsplanung über Sollarbeitszeit
- Organisationsstruktur-Analysen
- Azubi-/Trainee-Stellenmanagement (Kürzel 9910)

---

## Wichtige Datenlogik und Besonderheiten

### Besetzungsstatus

| Status | Kriterium | Anzahl |
|--------|-----------|--------|
| Besetzt | `Personalnummer` ist gefüllt | 1.247 |
| Offen | `Personalnummer` ist leer | 481 |

### Mehrfachbesetzungen
- **25 Personen** haben 2 Planstellen (jeweils max. 2)
- Bei Kapazitätsberechnungen: Planstellen summieren, nicht Personen deduplizieren

### Azubi-/Trainee-Stellen (Sonderfall)

| Kriterium | Wert |
|-----------|------|
| Identifikation | `Kürzel OrgEinheit = '9910'` |
| Anzahl | 208 Planstellen |
| Sollarbeitszeit | **0.01** (Platzhalter!) |
| Korrektur erforderlich | **0.01 → 39** Wochenstunden |

### Summenzeile (letzte Zeile)
Die letzte Zeile enthält nur `NaN`-Werte und muss **entfernt** werden.

### Organisationsstruktur
- **Kürzel OrgEinheit**: Oberste Ebene (Cluster/Bereich)
- **OrgEinheitNr**: Numerische ID der Untereinheit
- **Organisationseinheit**: Klartext-Name

Vorhandene Kürzel (Auszug): `025`, `026`, `028`, `030`, `032`, `033`, `035`, `040`, `126`, `191`, `200`, `220`, `228`, `240`, `245`, `310`, `320`, `330`, `340`, `350`, `360`, `370`, `800`, `900`, `9910`, `9921`

---

## Spaltenbeschreibung

| Spalte | Name | Typ | Beschreibung | Beispiel |
|--------|------|-----|--------------|----------|
| A | Kürzel OrgEinheit | string | Cluster-/Bereichskürzel | `800`, `900`, `9910` |
| B | OrgEinheitNr | float | Numerische Org-Einheit-ID | 50002405.0 |
| C | Organisationseinheit | string | Name der Organisationseinheit | "Vorstandsstab" |
| D | Planstellennr | float | 8-stellige Planstellen-ID | 50000001.0 |
| E | Planstellenkürzel | string | Kurz-ID der Planstelle | "VS-001" |
| F | Planstelle | string | Bezeichnung/Titel | "Sachbearbeiter Kredit" |
| G | Sollarbeitszeit | float | Soll-Wochenstunden | 39.0, 20.0, 0.01 |
| H | Bewertung Tarifgruppe | string | Ziel-Tarifgruppe | `E6`, `E9A`, `E11` |
| I | Text Gehaltsband | string | Oberes Gehaltsband | (meist leer) |
| J | Personalnummer | float | PersNr bei Besetzung | 2915.0 oder NaN |

---

## Wertebereiche

### Sollarbeitszeit
| Wert | Bedeutung | Aktion |
|------|-----------|--------|
| 39.0 | Vollzeit | Standard |
| 0.01 | Platzhalter (Azubis) | → 39.0 korrigieren |
| 0.2 – 38.9 | Teilzeit | Korrekt |

### Bewertung Tarifgruppe
Vorhandene Werte: `E3`, `E4`, `E5`, `E6`, `E7`, `E8`, `E9A`, `E9B`, `E9C`, `E10`, `E11`, `E12`, `E13`, `E14`, `E15`

**Fehlende Werte:** 426 Planstellen ohne Tarifgruppen-Bewertung

---

## Empfohlene Datenbereinigungen

```python
import pandas as pd
import numpy as np

df_plan = pd.read_excel("Planstellen.XLSX")

# 1. Summenzeile entfernen (letzte Zeile mit NaN)
df_plan = df_plan[df_plan['Kürzel OrgEinheit'].notna()].copy()

# 2. Azubi-Sollarbeitszeit korrigieren
df_plan.loc[df_plan['Kürzel OrgEinheit'] == '9910', 'Sollarbeitszeit'] = 39.0

# 3. Personalnummer standardisieren
df_plan['Personalnummer'] = df_plan['Personalnummer'].apply(
    lambda x: str(int(x)).zfill(6) if pd.notna(x) else None
)

# 4. Besetzungsstatus als Flag
df_plan['ist_besetzt'] = df_plan['Personalnummer'].notna()

# 5. Planstellennr als String (für Duplikat-Check)
df_plan['Planstellennr'] = df_plan['Planstellennr'].apply(
    lambda x: str(int(x)) if pd.notna(x) else None
)
```

---

## Kennzahlen und Auswertungen

### Kapazitätsübersicht
```python
# Soll-Kapazität gesamt (nach Azubi-Korrektur)
soll_kapazitaet = df_plan['Sollarbeitszeit'].sum()  # in Wochenstunden

# Soll-FTE
soll_fte = soll_kapazitaet / 39

# Besetzte Kapazität
besetzte_kapazitaet = df_plan[df_plan['ist_besetzt']]['Sollarbeitszeit'].sum()

# Offene Kapazität
offene_kapazitaet = df_plan[~df_plan['ist_besetzt']]['Sollarbeitszeit'].sum()
```

### Besetzungsquote
```python
besetzungsquote = len(df_plan[df_plan['ist_besetzt']]) / len(df_plan) * 100
# Aktuell: 1.247 / 1.728 = 72,2%
```

---

## Verknüpfung mit Mitarbeiter.xlsx

```python
df_ma = pd.read_excel("Mitarbeiter.xlsx")
df_ma['PersNr'] = df_ma['PersNr'].astype(str).str.zfill(6)

# Join: Planstelle → Mitarbeiter
df_plan_mit_ma = df_plan.merge(
    df_ma[['PersNr', 'BsGrd', 'Vertragsart', 'Status kundenindividuell']],
    left_on='Personalnummer',
    right_on='PersNr',
    how='left'
)

# Soll-Ist-Vergleich Arbeitszeit
df_plan_mit_ma['Soll_Ist_Diff'] = df_plan_mit_ma['Sollarbeitszeit'] - (df_plan_mit_ma['BsGrd'] / 100 * 39)
```

### Konsistenzcheck
- Alle 1.222 Mitarbeiter haben mindestens eine Planstelle ✓
- 25 Mitarbeiter haben genau 2 Planstellen
- Keine verwaisten Personalnummern in Planstellen

---

## Fehlende Werte

| Spalte | Anzahl fehlend | Grund/Aktion |
|--------|----------------|--------------|
| Personalnummer | 481 | Offene Stellen (korrekt) |
| Bewertung Tarifgruppe | 426 | Teilweise ungepflegt |
| Text Gehaltsband | 1.417 | Selten gepflegt (optional) |

---

## Verwendung im Forecast

### Offene Stellen als Einstellungspotenzial
```python
# Offene Stellen nach Org-Einheit
offene_nach_org = df_plan[~df_plan['ist_besetzt']].groupby('Kürzel OrgEinheit').size()

# Offene Stellen nach Tarifgruppe
offene_nach_tarif = df_plan[~df_plan['ist_besetzt']].groupby('Bewertung Tarifgruppe').size()
```

### Kapazitätsplanung
```python
# Planstellen als Kapazitätsobergrenze
max_kapazitaet_fte = df_plan['Sollarbeitszeit'].sum() / 39

# Aktuelle Auslastung (aus Mitarbeiter.xlsx)
ist_kapazitaet_fte = df_ma[df_ma['Status kundenindividuell'] == 'Aktives Beschäftigungsverhältnis']['BsGrd'].sum() / 100

# Auslastungsquote
auslastung = ist_kapazitaet_fte / max_kapazitaet_fte * 100
```

---

## Hinweis: Kein Job-Family-Feld

Die Planstellen-Datei enthält **kein explizites Job-Family-Feld**. Für Forecast-Matrizen nach Job Family gibt es folgende Optionen:

| Option | Feld | Eignung |
|--------|------|---------|
| A | `Kürzel OrgEinheit` | Org-Struktur, nicht Funktion |
| B | `Bewertung Tarifgruppe` | Hierarchie, nicht Funktion |
| C | `Planstelle` (Freitext) | Zu granular (viele Unique) |
| D | **Neues Mapping erstellen** | Empfohlen |

→ Abstimmung mit Fachbereich erforderlich
