# Datensteckbrief – ATZ.xlsx

## Zweck und Inhalt

Die Datei **ATZ.xlsx** enthält alle Mitarbeitenden in **Altersteilzeit (ATZ)** mit ihren jeweiligen Phasen und Zeiträumen. Sie ist essenziell für die korrekte Berechnung von MAK und Kapazitätsplanung.

**Aktueller Datenstand:** 102 Datensätze (= 51 Personen × 2 Phasen)

### Wesentliche Anwendungsfälle
- Identifikation von ATZ-Mitarbeitenden für MAK-Berechnung
- Unterscheidung zwischen Arbeitsphase (MAK > 0) und Freizeitphase (MAK = 0)
- Prognose von Kapazitätsabgängen durch ATZ-Ende
- Kostenplanung (Kosten laufen in beiden Phasen)

---

## Wichtige Datenlogik und Besonderheiten

### Doppelte Personalnummern (2 Zeilen pro Person)
Jede ATZ-Person hat **exakt zwei Datensätze**:
1. **AR (Arbeitsphase):** Person arbeitet noch, leistet Arbeit
2. **FR (Freizeitphase):** Person arbeitet nicht mehr, erhält aber weiter Gehalt

**⚠️ PersNr ist NICHT eindeutig!** Eindeutigkeit nur über `PersNr + Phase`.

### Phasenlogik und MAK-Auswirkung

| Phase | Bedeutung | MAK | Kosten | Headcount |
|-------|-----------|-----|--------|-----------|
| AR | Arbeitsphase | > 0 (gemäß BsGrd) | Ja | Ja |
| FR | Freizeitphase | **0** | Ja | Ja |

### Aktueller Status (Stichtag: 30.01.2025)

| Status | Anzahl Personen |
|--------|-----------------|
| ATZ gesamt | 51 |
| Aktuell in AR-Phase | 18 |
| Aktuell in FR-Phase | 24 |
| ATZ noch nicht begonnen | 5 |
| ATZ bereits beendet | 4 |

### Kritische Erkenntnis: ATZ ≠ "Ruhend"

**In Mitarbeiter.xlsx haben ALLE 50 ATZ-Personen:**
- `Status kundenindividuell` = "Aktives Beschäftigungsverhältnis"

**Die 54 "Ruhenden" in Mitarbeiter.xlsx sind NICHT in ATZ.xlsx!**

→ "Ruhend" bedeutet andere Abwesenheitsgründe (Elternzeit, Sabbatical etc.)
→ ATZ-FR-Phase muss separat aus dieser Datei ermittelt werden

### ATZ-Modell
Einziges vorhandenes Modell: **OAT5** (100% der Fälle)

---

## Spaltenbeschreibung

| Spalte | Name | Typ | Beschreibung | Beispiel |
|--------|------|-----|--------------|----------|
| A | PersNr | int | Personalnummer (Join-Key zu Mitarbeiter.xlsx) | 28, 50, 56 |
| B | Beginn | date | Startdatum der jeweiligen Phase | 01.05.2022 |
| C | Ende | date | Enddatum der jeweiligen Phase | 30.04.2024 |
| D | Ende ATZ Vertrag | date | Gesamtenddatum des ATZ-Vertrags | 30.04.2026 |
| E | Modell | string | ATZ-Modell | `OAT5` |
| F | Phase | string | Aktuelle Phase | `AR`, `FR` |

---

## Zeitliche Struktur (Beispiel PersNr 28)

```
|-------- AR-Phase --------|-------- FR-Phase --------|
01.05.2022            30.04.2024              30.04.2026
                                              (Ende ATZ Vertrag)
```

### Validierungsregeln
- `Beginn` < `Ende` (innerhalb jeder Phase)
- AR-Phase `Ende` = FR-Phase `Beginn` - 1 Tag (nahtloser Übergang)
- FR-Phase `Ende` = `Ende ATZ Vertrag`

---

## Empfohlene Datenbereinigungen und Ableitungen

```python
import pandas as pd

df_atz = pd.read_excel("ATZ.xlsx")

# 1. PersNr als String für konsistente Joins
df_atz['PersNr'] = df_atz['PersNr'].astype(str).str.zfill(6)

# 2. Datumsfelder validieren
df_atz['Beginn'] = pd.to_datetime(df_atz['Beginn'])
df_atz['Ende'] = pd.to_datetime(df_atz['Ende'])
df_atz['Ende ATZ Vertrag'] = pd.to_datetime(df_atz['Ende ATZ Vertrag'])

# 3. Stichtagsbezogene Phase ermitteln
stichtag = pd.Timestamp('today')
df_atz['ist_aktuell'] = (df_atz['Beginn'] <= stichtag) & (df_atz['Ende'] >= stichtag)

# 4. Sets für MAK-Berechnung erstellen
atz_persnr_alle = set(df_atz['PersNr'].unique())
atz_persnr_fr_aktuell = set(
    df_atz[(df_atz['Phase'] == 'FR') & df_atz['ist_aktuell']]['PersNr']
)

# 5. Ableitung für Forecast: ATZ-Daten pro Person pivotieren
df_atz_pivot = df_atz.pivot(index='PersNr', columns='Phase', values=['Beginn', 'Ende'])
df_atz_pivot.columns = ['AR_Beginn', 'FR_Beginn', 'AR_Ende', 'FR_Ende']
```

### Abgeleitete Felder für Forecast

| Feld | Ableitung | Verwendung |
|------|-----------|------------|
| `atz_start_date` | `Beginn` WHERE `Phase = 'AR'` | ATZ-Beginn |
| `atz_rest_start_date` | `Beginn` WHERE `Phase = 'FR'` | Beginn Freizeitphase |
| `atz_end_date` | `Ende ATZ Vertrag` | Kompletter Austritt |
| `atz_duration_ar_months` | Differenz AR Ende - AR Beginn | Dauer Arbeitsphase |
| `atz_duration_fr_months` | Differenz FR Ende - FR Beginn | Dauer Freizeitphase |

---

## Integration mit anderen Dateien

### Join mit Mitarbeiter.xlsx
```python
df_ma = pd.read_excel("Mitarbeiter.xlsx")
df_ma['PersNr'] = df_ma['PersNr'].astype(str).str.zfill(6)

# ATZ-Flag hinzufügen
df_ma['ist_atz'] = df_ma['PersNr'].isin(atz_persnr_alle)
df_ma['ist_atz_fr'] = df_ma['PersNr'].isin(atz_persnr_fr_aktuell)

# MAK berechnen
df_ma['MAK'] = df_ma.apply(lambda row: 
    0 if row['ist_atz_fr'] else 
    0 if row['Status kundenindividuell'] == 'Ruhendes Beschäftigungsverhältnis' else 
    row['BsGrd'] / 100, axis=1)
```

### Konsistenzcheck: Vertragsart
Von den 51 ATZ-Personen haben:
- 48 Personen `Vertragsart = 'Altersteilzeit'` ✓
- 2 Personen `Vertragsart = 'Unbefristet'` ⚠️ (Dateninkonsistenz prüfen)

---

## Verwendung im Forecast

### Abgänge durch ATZ-Ende
```python
# Personen, deren ATZ in Prognoseperiode endet
def get_atz_exits(df_atz, period_start, period_end):
    return df_atz[
        (df_atz['Phase'] == 'FR') & 
        (df_atz['Ende'] >= period_start) & 
        (df_atz['Ende'] <= period_end)
    ]['PersNr'].unique()
```

### Kapazitätsverlust durch AR→FR-Übergang
```python
# Personen, die von AR zu FR wechseln (MAK fällt auf 0)
def get_ar_to_fr_transitions(df_atz, period_start, period_end):
    return df_atz[
        (df_atz['Phase'] == 'FR') & 
        (df_atz['Beginn'] >= period_start) & 
        (df_atz['Beginn'] <= period_end)
    ]['PersNr'].unique()
```

---

## Kennzahlen (Ist-Stand)

| Kennzahl | Wert |
|----------|------|
| ATZ-Personen gesamt | 51 |
| ATZ-Quote (Basis: alle Aktiven) | 4,17% |
| ATZ-Quote (Basis: ohne Azubis) | 4,67% |
| Durchschn. AR-Dauer | ~2,5 Jahre |
| Durchschn. FR-Dauer | ~2,5 Jahre |
