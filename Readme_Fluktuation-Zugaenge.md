# Fluktuation: Positive Fluktuation (Zugänge)

Dieses Dokument definiert die Logik für **Neuzugänge** im HR-Forecast-Dashboard, angepasst an die tatsächliche Datenstruktur.

---

## 1. Grundbegriffe und Statusmodell

### Definition Positive Fluktuation
Ereignisse, die dazu führen, dass Mitarbeitende:
- neu in die Belegschaft eintreten, oder
- aus einem Ausbildungs-/Trainee-Status in ein reguläres Beschäftigungsverhältnis übergehen (Übernahme)

### Statusmodell

| Status | Headcount | MAK | Kosten | Erkennbar in Daten |
|--------|-----------|-----|--------|-------------------|
| **Neu eingestellt** | Ja | > 0 | Ja | `Eintritt` in Periode |
| **Azubi** | Ja | Separat führen | Azubi-Tarif | `MitarbGruppenbez. = 'Auszubildende'` |
| **Trainee** | Ja | > 0 | Trainee-Gehalt | `Vertragsart = 'Trainee'` |
| **Übernommen** | Ja | > 0 | Regulär | Wechsel von Azubi → Angestellte |

---

## 2. Zugangskategorien und Datengrundlage

### 2.1 Auszubildende (Azubis)

**Aktuelle Daten:**
| Kennzahl | Wert | Quelle |
|----------|------|--------|
| Azubis gesamt | 131 | `MitarbGruppenbez. = 'Auszubildende'` |
| Azubi-Planstellen (Kürzel 9910) | 208 | Planstellen.XLSX |
| Besetzte Azubi-Planstellen | 130 | Planstellen mit Personalnummer |
| Offene Azubi-Planstellen | 78 | Planstellen ohne Personalnummer |

**Identifikation (empfohlen):**
```python
# Primäres Kriterium: MitarbGruppenbez.
azubis = df_ma[df_ma['MitarbGruppenbez.'] == 'Auszubildende']

# Alternativ/Zusätzlich:
# - Vertragsart = 'Ausbildung' (131 Personen, identisch)
# - Planstelle Kürzel = '9910' (130 Personen, 1 Differenz)
# - Ausbildung.xlsx = 'derzeit Berufsausbildung' (107 Personen, 24 Differenz)
```

**KPI-Logik:**
| Kennzahl | Berechnung |
|----------|------------|
| Anzahl Azubis | COUNT WHERE `MitarbGruppenbez. = 'Auszubildende'` |
| Neue Azubis pro Jahr | COUNT WHERE `Eintritt` in Periode UND Azubi |
| Azubis nach Lehrjahr | Ableitung aus `Eintritt` (Jahr 1-3) |

**Ausbildungsjahr berechnen:**
```python
stichtag = pd.Timestamp('today')
azubis['Ausbildungsjahr'] = ((stichtag - azubis['Eintritt']).dt.days / 365.25).apply(
    lambda x: min(3, int(x) + 1)  # Max 3 Jahre
)
```

**Übernahmequote:**
| Parameter | Empfehlung | Beschreibung |
|-----------|------------|--------------|
| `azubi_takeover_rate` | 0.85-0.95 | Anteil der Azubis, die übernommen werden |
| `azubi_program_duration_years` | 3 | Standard-Ausbildungsdauer |

**Implementierung Übernahme:**
```python
def get_azubi_uebernahmen(df_ma, period_start, period_end, takeover_rate=0.90):
    """
    Ermittelt erwartete Azubi-Übernahmen in einer Periode.
    """
    # Azubis, deren Ausbildung in der Periode endet
    azubis = df_ma[df_ma['MitarbGruppenbez.'] == 'Auszubildende'].copy()
    azubis['Ausbildungsende'] = azubis['Eintritt'] + pd.DateOffset(years=3)
    
    ending = azubis[
        (azubis['Ausbildungsende'] >= period_start) & 
        (azubis['Ausbildungsende'] <= period_end)
    ]
    
    expected_takeovers = int(len(ending) * takeover_rate)
    expected_exits = len(ending) - expected_takeovers
    
    return {
        'ausbildung_ende': len(ending),
        'uebernahmen': expected_takeovers,
        'nicht_uebernommen': expected_exits
    }
```

**Zielzuordnung nach Übernahme:**
Da **kein Job-Family-Feld** existiert, Empfehlung:
```python
# Option A: Gleichverteilung auf Org-Einheiten mit offenen Stellen
offene_stellen = df_plan[~df_plan['ist_besetzt']]
ziel_orgs = offene_stellen['Kürzel OrgEinheit'].value_counts()

# Option B: Basierend auf Tarifgruppen-Verteilung
azubi_target_tarifgruppen = ['E6', 'E7', 'E8']  # Typische Einstiegsgruppen
```

### 2.2 Trainees

**Aktuelle Daten:**
| Kennzahl | Wert |
|----------|------|
| Trainees gesamt | **2** |
| MitarbGruppenbez. | `Angestellte` (nicht `Auszubildende`!) |
| Tarifgruppe | E8 |
| BsGrd | 100% |

**Hinweis:** Mit nur 2 Trainees ist eine statistische Modellierung nicht sinnvoll. Trainees sollten als **direkte Einstellungen** behandelt werden.

**Parameter (falls Trainee-Programm ausgebaut wird):**
| Parameter | Beschreibung |
|-----------|--------------|
| `trainee_program_duration_months` | Programmdauer (z.B. 18 Monate) |
| `trainee_filled_positions` | Geplante Trainee-Stellen |
| `trainee_age_min/max` | Altersrange (z.B. 22-28) |

### 2.3 Direkte Einstellungen

**Datengrundlage:**
```python
# Neue Einstellungen pro Periode
def get_neueinstellungen(df_ma, period_start, period_end):
    return df_ma[
        (df_ma['Eintritt'] >= period_start) & 
        (df_ma['Eintritt'] <= period_end) &
        (df_ma['MitarbGruppenbez.'] != 'Auszubildende')  # Ohne Azubis
    ]

# Historische Einstellungsverteilung
eintritte_nach_jahr = df_ma.groupby(df_ma['Eintritt'].dt.year).size()
```

**Einstellungsparameter:**
| Parameter | Typ | Beschreibung |
|-----------|-----|--------------|
| `hires_per_year` | Int | Geplante Neueinstellungen pro Jahr |
| `hire_target_orgs` | List | Zielbereiche für Einstellungen |
| `hire_replacement_ratio` | Float | Anteil Ersatz vs. Wachstum |

**Implementierung:**
```python
def allokiere_einstellungen(df_plan, hires_count, method='vacancy_fill'):
    """
    Verteilt geplante Einstellungen auf Bereiche.
    
    Methods:
    - 'vacancy_fill': Priorisiert Bereiche mit offenen Stellen
    - 'proportional': Proportional zur Bereichsgröße
    """
    offene = df_plan[~df_plan['ist_besetzt']].copy()
    
    if method == 'vacancy_fill':
        # Nach Anzahl offener Stellen gewichten
        weights = offene.groupby('Kürzel OrgEinheit').size()
        weights = weights / weights.sum()
    else:
        # Proportional zur Gesamtgröße
        weights = df_plan.groupby('Kürzel OrgEinheit').size()
        weights = weights / weights.sum()
    
    allocation = (weights * hires_count).round().astype(int)
    return allocation
```

**Replacement-Logik:**
```python
def berechne_replacement_hires(abgaenge_letzte_periode, replacement_ratio=0.8):
    """
    Berechnet Ersatz-Einstellungen basierend auf Abgängen.
    """
    return int(abgaenge_letzte_periode * replacement_ratio)
```

---

## 3. Zugangsattribute

### Alter bei Einstellung
```python
# Historische Altersverteilung bei Einstellung
df_ma['Alter_bei_Eintritt'] = (df_ma['Eintritt'] - df_ma['GebDatum']).dt.days / 365.25

# Für Simulation: Altersverteilung nach Kategorie
alter_bei_eintritt = {
    'azubi': {'min': 16, 'max': 25, 'mean': 19},
    'trainee': {'min': 22, 'max': 28, 'mean': 25},
    'direkt': {'min': 25, 'max': 55, 'mean': 35}
}
```

### Beschäftigungsgrad bei Einstellung
```python
# Typische BsGrd-Verteilung für Neueinstellungen
bsgrd_verteilung = {
    'azubi': 100,  # Immer Vollzeit
    'trainee': 100,  # Immer Vollzeit
    'direkt': df_ma[df_ma['Tenure_Jahre'] < 2]['BsGrd'].describe()
}
```

### Tarifgruppe bei Einstellung
```python
# Einstiegs-Tarifgruppen nach Kategorie
einstiegs_tarif = {
    'azubi': 'TVAÖD',
    'trainee': 'E8',
    'direkt_ohne_studium': ['E5', 'E6', 'E7'],
    'direkt_mit_studium': ['E9A', 'E9B', 'E10', 'E11']
}
```

---

## 4. Verknüpfung mit Planstellen

### Besetzung offener Stellen
```python
def besetze_planstellen(df_plan, neue_mitarbeiter):
    """
    Ordnet neue Mitarbeiter offenen Planstellen zu.
    """
    offene = df_plan[~df_plan['ist_besetzt']].copy()
    
    for ma in neue_mitarbeiter:
        # Matching nach Tarifgruppe
        passende = offene[offene['Bewertung Tarifgruppe'] == ma['ziel_tarif']]
        
        if len(passende) > 0:
            # Erste passende Stelle zuweisen
            stelle_idx = passende.index[0]
            df_plan.loc[stelle_idx, 'Personalnummer'] = ma['PersNr']
            offene = offene.drop(stelle_idx)
    
    return df_plan
```

---

## 5. Prioritätsregeln

| Priorität | Zugangsart | Begründung |
|-----------|------------|------------|
| 1 | Azubi-Übernahmen | Intern, planbar |
| 2 | Trainee-Übernahmen | Intern, planbar |
| 3 | Neue Azubis | Jahrgangsbezogen |
| 4 | Direkte Einstellungen | Flexibel |

---

## 6. Parameter-Übersicht

### Pflichtparameter
| Parameter | Typ | Beispielwert |
|-----------|-----|--------------|
| `azubi_takeover_rate` | Float | 0.90 |
| `azubi_program_duration_years` | Int | 3 |
| `azubi_new_per_year` | Int | 40-50 |
| `hires_per_year` | Int | 20-30 |

### Optionale Parameter
| Parameter | Typ | Beschreibung |
|-----------|-----|--------------|
| `hire_replacement_ratio` | Float | Ersatz- vs. Wachstumsanteil |
| `hire_target_weights` | Dict | Gewichtung nach Bereich |
| `hire_seasonality` | Dict | Saisonale Verteilung |

---

## 7. Output-Kennzahlen

| Kennzahl | Beschreibung |
|----------|--------------|
| Zugänge gesamt | Summe aller Neuzugänge |
| Zugänge nach Kategorie | Azubi-Übernahmen, Neue Azubis, Direkte Einstellungen |
| MAK-Zuwachs | Kapazitätszuwachs durch Zugänge |
| Headcount-Zuwachs | Anzahl neue Köpfe |
| Zugangsquote | Zugänge / Durchschn. Bestand |
| Besetzungsquote Δ | Veränderung der Planstellen-Besetzung |

---

## 8. Aktuelle Strukturdaten für Forecast-Kalibrierung

| Kennzahl | Wert | Relevanz für Forecast |
|----------|------|----------------------|
| Azubis aktuell | 131 | Basis für Übernahme-Prognose |
| Azubi-Stellen offen | 78 | Potenzial für neue Azubis |
| Offene Stellen gesamt | 481 | Einstellungspotenzial |
| Durchschn. Alter bei Eintritt | ~30 Jahre | Für Alters-Simulation |
| Typische Einstiegs-TrfGr | E6, E8 | Für Kosten-Simulation |
