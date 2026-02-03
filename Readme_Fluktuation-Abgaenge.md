# Fluktuation: Negative Fluktuation (Abgänge)

Dieses Dokument definiert die Logik für **Abgänge** im HR-Forecast-Dashboard, angepasst an die tatsächliche Datenstruktur.

---

## 1. Grundbegriffe und Statusmodell

### Definition Negative Fluktuation
Ereignisse, die dazu führen, dass Mitarbeitende:
- vollständig aus der Belegschaft ausscheiden, oder
- nicht mehr als aktive Arbeitskraft zählen (MAK → 0), obwohl weiterhin Kosten anfallen

### Statusmodell

| Status | Headcount | MAK | Kosten | Erkennbar in Daten |
|--------|-----------|-----|--------|-------------------|
| **Aktiv** | Ja | > 0 | Ja | `Status = 'Aktives Beschäftigungsverhältnis'` UND nicht ATZ-FR |
| **Inaktiv mit Kosten** | Ja | 0 | Ja | `Status = 'Ruhendes Beschäftigungsverhältnis'` ODER ATZ-FR |
| **Ausgeschieden** | Nein | 0 | Nein | `Austritt` < Stichtag (aktuell nicht im Datenbestand) |

### Kritische Erkenntnis: Ruhend ≠ ATZ-FR

| Kategorie | Anzahl | Status kundenindividuell | In ATZ.xlsx |
|-----------|--------|--------------------------|-------------|
| Ruhend (Elternzeit, Sabbatical etc.) | 54 | Ruhendes Beschäftigungsverhältnis | Nein |
| ATZ-Freizeitphase | 24 | **Aktives** Beschäftigungsverhältnis | Ja (Phase=FR) |

**→ Beide müssen separat geprüft werden!**

---

## 2. Abgangskategorien und Datengrundlage

### 2.1 Rente

**Definition:** Ausscheiden durch altersbedingten Renteneintritt

**Datengrundlage:**
```python
# Altersberechnung
stichtag = pd.Timestamp('today')
df_ma['Alter'] = (stichtag - df_ma['GebDatum']).dt.days / 365.25

# Rentenkohorten identifizieren
alter_65 = df_ma[df_ma['Alter'] >= 65]  # 1 Person aktuell
alter_60_64 = df_ma[(df_ma['Alter'] >= 60) & (df_ma['Alter'] < 65)]  # 101 Personen
```

**Forecast-Parameter:**
| Parameter | Empfehlung | Beschreibung |
|-----------|------------|--------------|
| `rent_rate_65` | 80-100% | Anteil der 65-Jährigen, die ausscheiden |
| `rent_rate_60_65` | 5-15% | Anteil der 60-64-Jährigen (Frühverrentung) |

**Priorisierung:** Wenn Person in ATZ → ATZ-Logik geht vor (kein doppelter Abgang)

### 2.2 ATZ (Altersteilzeit)

**Definition:** Gesteuerter Übergang in den Ruhestand über ATZ-Modell

**Datengrundlage:** ATZ.xlsx mit AR/FR-Phasen

**Ereignisse:**

| Ereignis | MAK-Auswirkung | Headcount | Kosten |
|----------|----------------|-----------|--------|
| Start ATZ (AR-Beginn) | Bleibt (ggf. reduziert) | Ja | Ja |
| AR → FR (Freizeitphase) | → 0 | Ja | Ja |
| Ende FR (Austritt) | 0 | → Nein | → Nein |

**Implementierung:**
```python
def get_atz_events(df_atz, period_start, period_end):
    """
    Ermittelt ATZ-Ereignisse in einer Periode.
    """
    events = {
        'ar_to_fr': [],  # Kapazitätsverlust
        'exits': []       # Headcount-Verlust
    }
    
    # AR → FR Übergänge (MAK fällt auf 0)
    fr_starts = df_atz[
        (df_atz['Phase'] == 'FR') & 
        (df_atz['Beginn'] >= period_start) & 
        (df_atz['Beginn'] <= period_end)
    ]
    events['ar_to_fr'] = list(fr_starts['PersNr'].unique())
    
    # Komplette Austritte (Ende ATZ-Vertrag)
    atz_exits = df_atz[
        (df_atz['Phase'] == 'FR') & 
        (df_atz['Ende'] >= period_start) & 
        (df_atz['Ende'] <= period_end)
    ]
    events['exits'] = list(atz_exits['PersNr'].unique())
    
    return events
```

**Forecast-Parameter für neue ATZ-Verträge:**
| Parameter | Beschreibung |
|-----------|--------------|
| `new_atz_cases_per_period` | Erwartete neue ATZ-Verträge pro Jahr |
| `atz_eligible_age_min` | Mindestalter für ATZ-Berechtigung (z.B. 55) |

**ATZ-Berechtigte (Eligibility Check):**
```python
# Personen im ATZ-berechtigten Alter, die noch nicht in ATZ sind
atz_persnr = set(df_atz['PersNr'])
eligible = df_ma[
    (df_ma['Alter'] >= 55) & 
    (df_ma['Alter'] <= 60) & 
    (~df_ma['PersNr'].isin(atz_persnr)) &
    (df_ma['MitarbGruppenbez.'] != 'Auszubildende')
]
print(f"ATZ-berechtigt, noch nicht in ATZ: {len(eligible)}")
```

### 2.3 Kündigungen

**Definition:** Freiwilliges oder unfreiwilliges Ausscheiden

**Datengrundlage:** 
- Historische Kündigungen: **Nicht im aktuellen Datenbestand** (alle Austritt = 9999-12-31)
- Für Forecast: Annahmebasierte Modellierung erforderlich

**Empfohlene Modellierung:**
```python
# Kündigungswahrscheinlichkeit nach Alter und Tenure
quit_rate_matrix = {
    'alter_unter_30': {'tenure_unter_2': 0.12, 'tenure_2_5': 0.08, 'tenure_ueber_5': 0.05},
    'alter_30_45': {'tenure_unter_2': 0.08, 'tenure_2_5': 0.05, 'tenure_ueber_5': 0.03},
    'alter_ueber_45': {'tenure_unter_2': 0.05, 'tenure_2_5': 0.03, 'tenure_ueber_5': 0.02}
}

def berechne_erwartete_kuendigungen(df_ma, quit_rate_matrix):
    """
    Berechnet erwartete Kündigungen basierend auf Risikomatrix.
    """
    df = df_ma.copy()
    df['Altersgruppe'] = pd.cut(df['Alter'], bins=[0, 30, 45, 100], 
                                 labels=['alter_unter_30', 'alter_30_45', 'alter_ueber_45'])
    df['Tenuregruppe'] = pd.cut(df['Tenure_Jahre'], bins=[0, 2, 5, 100],
                                 labels=['tenure_unter_2', 'tenure_2_5', 'tenure_ueber_5'])
    
    def get_quit_prob(row):
        return quit_rate_matrix.get(row['Altersgruppe'], {}).get(row['Tenuregruppe'], 0.03)
    
    df['quit_prob'] = df.apply(get_quit_prob, axis=1)
    expected_quits = df['quit_prob'].sum()
    return expected_quits
```

**Hinweis: Job Family fehlt!**
Die ursprüngliche Dokumentation fordert `quit_rate_matrix[age_group][job_family]`. 
Da **kein Job-Family-Feld** existiert, muss entweder:
1. Ein Mapping erstellt werden (Planstelle → Job Family), oder
2. Die Matrix auf Alter × Tenure reduziert werden

### 2.4 Ruhende Beschäftigungsverhältnisse (Elternzeit, Sabbatical etc.)

**Definition:** Temporäre Inaktivität ohne Kündigung

**Datengrundlage:**
```python
ruhend = df_ma[df_ma['Status kundenindividuell'] == 'Ruhendes Beschäftigungsverhältnis']
# Aktuell: 54 Personen
```

**Logik:**
- Headcount: Ja (Person ist noch beschäftigt)
- MAK: 0 (keine aktive Arbeitsleistung)
- Kosten: Teilweise (abhängig von Grund)

**Für Forecast:**
- Rückkehrquote modellieren (z.B. 95% kehren zurück)
- Durchschnittliche Abwesenheitsdauer (z.B. 12 Monate)

### 2.5 Weitere Kategorien (optional)

| Kategorie | Datengrundlage | Empfehlung |
|-----------|----------------|------------|
| Sterbefälle | Keine | Über Sterberaten nach Alter modellieren |
| Dauerkrank | Nicht direkt erkennbar | In "Ruhend" enthalten, ggf. separieren |

---

## 3. Prioritätsregeln bei Mehrfachtreffern

Wenn mehrere Abgangsgründe auf eine Person zutreffen:

| Priorität | Kategorie | Begründung |
|-----------|-----------|------------|
| 1 | ATZ (inkl. Übergänge) | Vertraglich fixiert |
| 2 | Rente | Altersbezogen, nach ATZ-Prüfung |
| 3 | Kündigung | Freiwillig/unfreiwillig |
| 4 | Sonstige | Nachrangig |

**Implementierung:**
```python
def assign_exit_reason(row, atz_persnr, atz_exits_period):
    if row['PersNr'] in atz_exits_period:
        return 'ATZ_Ende'
    if row['PersNr'] in atz_persnr:
        return 'ATZ_aktiv'  # Kein Abgang, nur Phasenwechsel
    if row['Alter'] >= 65:
        return 'Rente'
    # Kündigungen werden stochastisch zugewiesen
    return 'Kein_Abgang'
```

---

## 4. Parameter-Übersicht

### Pflichtparameter
| Parameter | Typ | Beispielwert |
|-----------|-----|--------------|
| `rent_rate_65` | Float | 0.90 |
| `rent_rate_60_65` | Float | 0.10 |
| `new_atz_cases_per_year` | Int | 5 |
| `quit_rate_base` | Float | 0.05 |

### Optionale Parameter
| Parameter | Typ | Beschreibung |
|-----------|-----|--------------|
| `quit_rate_matrix` | Dict | Differenziert nach Alter/Tenure |
| `death_rate_by_age` | Dict | Sterblichkeit nach Altersgruppe |
| `ruhend_return_rate` | Float | Rückkehrquote aus Ruhend |

---

## 5. Output-Kennzahlen

| Kennzahl | Beschreibung |
|----------|--------------|
| Abgänge gesamt | Summe aller Abgänge im Zeitraum |
| Abgänge nach Grund | Differenziert nach Rente, ATZ, Kündigung etc. |
| MAK-Verlust | Kapazitätsverlust durch Abgänge und AR→FR |
| Headcount-Verlust | Nur echte Austritte (nicht Ruhend/ATZ-FR) |
| Abgangsquote | Abgänge / Durchschn. Bestand |
