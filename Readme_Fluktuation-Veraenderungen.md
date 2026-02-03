# Fluktuation: Veränderungen (Bestandsentwicklung)

Dieses Dokument definiert **interne Veränderungen** im Personalbestand, die weder Zu- noch Abgänge sind, aber MAK, Kosten und Strukturkennzahlen beeinflussen.

---

## 1. Grundprinzip

Veränderungen betreffen Mitarbeitende, die im Unternehmen **bleiben**, deren Attribute sich jedoch ändern:

| Auswirkung | Betroffene Kennzahl |
|------------|---------------------|
| Headcount | Bleibt konstant |
| MAK | Kann sich ändern (Teilzeit, Statuswechsel) |
| Kosten | Können sich ändern (Gehaltsstufe, Tarifgruppe) |
| Struktur | Kann sich ändern (Alter, Tenure, Org-Zuordnung) |

---

## 2. Veränderungskategorien

### 2.1 Natürliche Alterung

**Definition:** Mit jedem Zeitraum steigt das Alter aller Mitarbeitenden.

**Berechnung:**
```python
def aktualisiere_alter(df_ma, stichtag_neu):
    """
    Aktualisiert das Alter aller Mitarbeitenden.
    """
    df_ma['Alter'] = (stichtag_neu - df_ma['GebDatum']).dt.days / 365.25
    return df_ma
```

**Einfluss auf andere Modelle:**
| Abhängige Größe | Auswirkung |
|-----------------|------------|
| Kündigungswahrscheinlichkeit | Altersabhängig |
| Rentenwahrscheinlichkeit | Ab 60 Jahren relevant |
| ATZ-Berechtigung | Ab Mindestalter |

**Aktuelle Altersstruktur:**
| Altersgruppe | Anzahl | Anteil |
|--------------|--------|--------|
| < 30 | ~180 | 14,7% |
| 30-39 | ~220 | 18,0% |
| 40-49 | ~320 | 26,2% |
| 50-59 | ~400 | 32,7% |
| 60+ | ~102 | 8,4% |

### 2.2 Tenure-Entwicklung (Betriebszugehörigkeit)

**Definition:** Zeit, die eine Person im Unternehmen ist.

**Berechnung:**
```python
def aktualisiere_tenure(df_ma, stichtag_neu):
    """
    Aktualisiert die Betriebszugehörigkeit.
    """
    df_ma['Tenure_Jahre'] = (stichtag_neu - df_ma['Eintritt']).dt.days / 365.25
    return df_ma
```

**Wichtig:** Tenure ≠ Alter!
- Alter = Lebensjahre
- Tenure = Unternehmenszugehörigkeit

**Einfluss:**
| Abhängige Größe | Auswirkung |
|-----------------|------------|
| Erfahrungsstufe | Automatischer Aufstieg |
| Kündigungswahrscheinlichkeit | Tenure-abhängig (Anfang höher) |
| Gehaltskosten | Stufensprünge |

**Datengrundlage:**
- `Eintritt` ist vollständig gepflegt (Range: 1976 bis 2025)
- Tenure berechenbar für alle 1.222 Mitarbeitenden

### 2.3 Erfahrungsstufenwechsel (Gehaltssprünge)

**Definition:** Automatischer Aufstieg in der Erfahrungsstufe (St) nach Tenure-Schwellen.

**Aktuelle Daten - Erfahrungsstufen:**
| Stufe | Anzahl | Anteil |
|-------|--------|--------|
| 1 | 21 | 1,7% |
| 2 | 144 | 11,8% |
| 2+ | 27 | 2,2% |
| 3 | 161 | 13,2% |
| 4 | 170 | 13,9% |
| 5 | 122 | 10,0% |
| 6 | 574 | 47,0% |
| NaN | 3 | 0,2% (Vorstand) |

**TVöD-Stufenmodell (Standard):**
| Stufe | Tenure-Schwelle | Verweildauer |
|-------|-----------------|--------------|
| 1 | Einstieg | 1 Jahr |
| 2 | Nach 1 Jahr | 2 Jahre |
| 3 | Nach 3 Jahren | 3 Jahre |
| 4 | Nach 6 Jahren | 4 Jahre |
| 5 | Nach 10 Jahren | 5 Jahre |
| 6 | Nach 15 Jahren | Endstufe |

**Implementierung:**
```python
def berechne_erfahrungsstufe(tenure_jahre):
    """
    Ermittelt TVöD-Erfahrungsstufe basierend auf Tenure.
    """
    if tenure_jahre < 1:
        return '1'
    elif tenure_jahre < 3:
        return '2'
    elif tenure_jahre < 6:
        return '3'
    elif tenure_jahre < 10:
        return '4'
    elif tenure_jahre < 15:
        return '5'
    else:
        return '6'

def aktualisiere_erfahrungsstufen(df_ma):
    """
    Aktualisiert Erfahrungsstufen basierend auf Tenure.
    """
    df_ma['St_berechnet'] = df_ma['Tenure_Jahre'].apply(berechne_erfahrungsstufe)
    
    # Abweichungen identifizieren (Ist vs. Soll)
    df_ma['St_abweichung'] = df_ma['St'] != df_ma['St_berechnet']
    
    return df_ma
```

**Kostenauswirkung:**
Jeder Stufensprung erhöht das Gehalt um ca. 3-5% (TVöD-Tabelle).

### 2.4 Teilzeitänderungen

**Definition:** Änderung des Beschäftigungsumfangs (BsGrd/FTE).

**Aktuelle BsGrd-Verteilung:**
| BsGrd-Bereich | Anzahl | Anteil |
|---------------|--------|--------|
| 100% (Vollzeit) | ~680 | 55,6% |
| 75-99% | ~200 | 16,4% |
| 50-74% | ~280 | 22,9% |
| < 50% | ~62 | 5,1% |

**Statistik:**
- Durchschnitt: 85,1%
- Median: 100%
- Min: 0% (Ruhend)
- Max: 100%

**Modellierung:**
```python
# Teilzeit-Änderungswahrscheinlichkeit (Beispielmatrix)
parttime_change_rates = {
    'vollzeit_zu_teilzeit': {
        'alter_unter_35': 0.03,
        'alter_35_50': 0.05,
        'alter_ueber_50': 0.02
    },
    'teilzeit_zu_vollzeit': {
        'alter_unter_35': 0.08,
        'alter_35_50': 0.04,
        'alter_ueber_50': 0.02
    }
}

def simuliere_teilzeitaenderungen(df_ma, rates):
    """
    Simuliert Teilzeitänderungen für eine Periode.
    """
    import numpy as np
    
    df = df_ma.copy()
    df['Altersgruppe'] = pd.cut(df['Alter'], bins=[0, 35, 50, 100],
                                 labels=['alter_unter_35', 'alter_35_50', 'alter_ueber_50'])
    
    for idx, row in df.iterrows():
        if row['BsGrd'] == 100:  # Vollzeit
            rate = rates['vollzeit_zu_teilzeit'].get(row['Altersgruppe'], 0.03)
            if np.random.random() < rate:
                df.loc[idx, 'BsGrd'] = np.random.choice([50, 60, 75, 80])
        else:  # Teilzeit
            rate = rates['teilzeit_zu_vollzeit'].get(row['Altersgruppe'], 0.05)
            if np.random.random() < rate:
                df.loc[idx, 'BsGrd'] = 100
    
    return df
```

**MAK-Auswirkung:**
```python
# MAK-Veränderung durch Teilzeitänderungen
mak_vorher = df_ma['BsGrd'].sum() / 100
mak_nachher = df_ma_neu['BsGrd'].sum() / 100
mak_delta = mak_nachher - mak_vorher
```

### 2.5 Organisationswechsel (intern)

**Definition:** Wechsel zwischen Organisationseinheiten ohne Austritt.

**Datengrundlage:**
- Aktuelle Zuordnung über Planstellen.XLSX (`Kürzel OrgEinheit`)
- **Historische Wechsel nicht trackbar** (nur Ist-Zustand)

**Für Forecast:**
```python
# Interne Wechselmatrix (vereinfacht)
# Annahme: Geringe Wechselwahrscheinlichkeit zwischen Bereichen
internal_transfer_rate = 0.02  # 2% pro Jahr

def simuliere_org_wechsel(df_plan, transfer_rate):
    """
    Simuliert interne Organisationswechsel.
    """
    besetzt = df_plan[df_plan['ist_besetzt']].copy()
    wechsel_count = int(len(besetzt) * transfer_rate)
    
    # Zufällige Auswahl für Wechsel
    wechsler = besetzt.sample(n=wechsel_count)
    
    # Neue Org-Zuordnung (vereinfacht: zufällig)
    alle_orgs = df_plan['Kürzel OrgEinheit'].dropna().unique()
    for idx in wechsler.index:
        neue_org = np.random.choice(alle_orgs)
        # In Realität: Matching mit offenen Stellen
    
    return wechsel_count
```

### 2.6 Tarifgruppen-Änderungen

**Definition:** Wechsel der Tarifgruppe (Beförderung/Höhergruppierung).

**Aktuelle Tarifgruppen-Verteilung:**
| TrfGr | Anzahl | Anteil |
|-------|--------|--------|
| E6 | 240 | 19,6% |
| E11 | 161 | 13,2% |
| E10 | 153 | 12,5% |
| TVAÖD | 131 | 10,7% (Azubis) |
| E8 | 130 | 10,6% |
| E9C | 103 | 8,4% |
| E9A | 71 | 5,8% |
| E13 | 62 | 5,1% |
| Sonstige | ~171 | 14,0% |

**Modellierung:**
```python
# Typische Beförderungspfade
befoerderungspfade = {
    'E5': ['E6'],
    'E6': ['E7', 'E8'],
    'E7': ['E8'],
    'E8': ['E9A', 'E9B', 'E9C'],
    'E9A': ['E10'],
    'E9B': ['E10'],
    'E9C': ['E10'],
    'E10': ['E11'],
    'E11': ['E12', 'E13'],
    'E12': ['E13'],
    'E13': ['E14', 'E15']
}

befoerderungsrate = 0.05  # 5% pro Jahr werden befördert
```

---

## 3. Verarbeitungsreihenfolge

Für jeden Forecast-Zeitraum:

| Schritt | Aktion | Auswirkung |
|---------|--------|------------|
| 1 | Alter aktualisieren | Alter +1 Jahr |
| 2 | Tenure aktualisieren | Tenure +1 Jahr |
| 3 | Erfahrungsstufen prüfen | Ggf. Stufensprung → Kosten |
| 4 | Teilzeitänderungen | MAK-Veränderung |
| 5 | Org-/Tarifwechsel | Strukturveränderung |
| 6 | Risiken neu berechnen | Input für Abgangslogik |

**Implementierung:**
```python
def aktualisiere_bestand(df_ma, df_atz, df_plan, stichtag_neu, params):
    """
    Führt alle Bestandsveränderungen für eine Periode durch.
    """
    # 1. Alter
    df_ma = aktualisiere_alter(df_ma, stichtag_neu)
    
    # 2. Tenure
    df_ma = aktualisiere_tenure(df_ma, stichtag_neu)
    
    # 3. Erfahrungsstufen
    df_ma = aktualisiere_erfahrungsstufen(df_ma)
    
    # 4. Teilzeit
    if params.get('simulate_parttime', True):
        df_ma = simuliere_teilzeitaenderungen(df_ma, params['parttime_rates'])
    
    # 5. MAK neu berechnen
    df_ma = berechne_mak(df_ma, df_atz, stichtag_neu)
    
    return df_ma
```

---

## 4. Parameter-Übersicht

### Pflichtparameter
| Parameter | Typ | Beschreibung |
|-----------|-----|--------------|
| Periodizität | String | 'monatlich' oder 'jährlich' |
| Stichtag | Date | Referenzdatum |

### Optionale Parameter
| Parameter | Typ | Default |
|-----------|-----|---------|
| `parttime_change_rates` | Dict | Siehe 2.4 |
| `befoerderungsrate` | Float | 0.05 |
| `internal_transfer_rate` | Float | 0.02 |
| `tenure_thresholds` | Dict | TVöD-Standard |

---

## 5. Output-Kennzahlen

| Kennzahl | Beschreibung |
|----------|--------------|
| MAK-Veränderung | Delta durch Teilzeit |
| Kosten-Veränderung | Delta durch Stufensprünge |
| Durchschnittsalter | Entwicklung über Zeit |
| Durchschn. Tenure | Entwicklung über Zeit |
| Stufenverteilung | Anteil je Erfahrungsstufe |
| Teilzeitquote | Anteil < 100% BsGrd |

---

## 6. Aktuelle Strukturdaten

| Kennzahl | Wert |
|----------|------|
| Durchschnittsalter | ~45 Jahre |
| Durchschn. Tenure | ~15 Jahre |
| Teilzeitquote | 44,4% |
| Anteil Stufe 6 | 47,0% |
| Durchschn. BsGrd | 85,1% |
