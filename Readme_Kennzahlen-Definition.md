# Kennzahlendefinition (validiert)

Dieses Dokument definiert die Kennzahlen für das HR Dashboard mit **verbindlichen Annahmen, Berechnungslogik, Validierungswerten** und Implementierungshinweisen.

## 0. Verbindliche Rahmenbedingungen

**Stichtag (Snapshot): 30.01.2025** (Quelle: Readme_ATZ_Excel.md)

**Vollzeitreferenz:** 39 Wochenstunden

**Wichtige Datenbesonderheiten (müssen im Code adressiert werden):**
- `Austritt = 31.12.9999` bedeutet **kein Austritt gesetzt** → als *offen* interpretieren (nicht als echtes Datum).
- In `Mitarbeiter.xlsx` existieren **86 Personen mit Eintritt nach dem Stichtag** → bei Snapshot Kennzahlen optional ausschließen (siehe Tenure).
- `ATZ.xlsx` enthält **50 eindeutige Personen**. Das Readme nennt **51** → **Quelleninkonsistenz** (offener Punkt an Data Owner).
- `Planstellen.XLSX`: letzte Summenzeile ist enthalten → entfernen (Kürzel OrgEinheit ist leer/NaN).
- `Sollarbeitszeit = 0,01` ist ein Platzhalter:
  - **208 Zeilen** in Org **9910** (Azubi/Trainee) → auf **39** korrigieren.
  - **529 Zeilen** außerhalb 9910 → Bedeutung **unklar**, **nicht** automatisch korrigieren.

---

## 1. Mitarbeiterzahl nach MAK / FTE effektiv

### Definition
**MAK / FTE effektiv** misst die **effektive Arbeitskapazität** in Vollzeitäquivalenten.

### Berechnungslogik

**Grundformel:**
```
FTE_roh = BsGrd / 100
FTE_effektiv = FTE_roh × Aktivitätsfaktor
```

**Aktivitätsfaktor:**
| Situation | Faktor |
|---|---:|
| Aktives Beschäftigungsverhältnis, nicht in ATZ Freizeitphase | 1 |
| Ruhendes Beschäftigungsverhältnis | 0 |
| ATZ Freizeitphase (aus ATZ.xlsx; Stichtag im Zeitraum) | 0 |

### Implementierung (verbindlich)
```python
import pandas as pd
import numpy as np

STICHTAG = pd.Timestamp("2025-01-30") ### Wobei Stichtag from nutzer wählbar sein soll

def berechne_fte_effektiv(df_ma, df_atz, stichtag=STICHTAG):
    # PersNr standardisieren
    df_ma = df_ma.copy()
    df_atz = df_atz.copy()
    df_ma["PersNr"] = df_ma["PersNr"].astype(int).astype(str).str.zfill(6)
    df_atz["PersNr"] = df_atz["PersNr"].astype(int).astype(str).str.zfill(6)

    # ATZ Freizeitphase am Stichtag
    atz_fr = set(df_atz[
        (df_atz["Phase"] == "FR") &
        (df_atz["Beginn"] <= stichtag) &
        (df_atz["Ende"] >= stichtag)
    ]["PersNr"].unique())

    df_ma["FTE_roh"] = df_ma["BsGrd"] / 100.0
    df_ma["FTE_effektiv"] = np.where(
        df_ma["Status kundenindividuell"] == "Ruhendes Beschäftigungsverhältnis",
        0.0,
        np.where(df_ma["PersNr"].isin(atz_fr), 0.0, df_ma["FTE_roh"])
    )
    return df_ma
```

### Validierte Werte (Stichtag 30.01.2025)
| Kennzahl | Wert |
|---|---:|
| Headcount gesamt | 1222 |
| FTE roh gesamt | 1,039.77 |
| FTE effektiv gesamt | 993.23 |
| Verhältnis FTE effektiv / Kopf | 81.3 % |
| Ruhend (Köpfe) | 54 |
| Kapazitätsverlust durch Ruhend (FTE) | 46.54 |
| ATZ Freizeitphase am Stichtag (Köpfe) | 24 |
| Durchschnitt BsGrd (Status = aktiv) | 85.0 % |

---

## 2. Teilzeitquote (arbeitsaktive Teilzeit)

### Definition
Teilzeit misst den Anteil Mitarbeitender mit reduziertem Beschäftigungsgrad **unter Ausschluss** von Ruhenden und ATZ Freizeitphase (keine echte Kapazität am Stichtag).

### Logik
```
Teilzeit = 0 < BsGrd < 100
Filter: Status != Ruhendes Beschäftigungsverhältnis
Filter: PersNr nicht in ATZ-FR am Stichtag
```

### Validierte Werte (Stichtag 30.01.2025)
| Kennzahl | Wert |
|---|---:|
| Teilzeitkräfte (exkl. Ruhende/ATZ-FR) | 349 |
| Teilzeitquote (bezogen auf Headcount gesamt) | 28.56 % |
| Ø BsGrd der Teilzeitkräfte | 59.1 % |

**Hinweis zur Quote:** Alternativ kann die Teilzeitquote auf „arbeitsaktive Köpfe“ bezogen werden (Headcount minus Ruhende minus ATZ-FR). Dann ist der Nenner kleiner und die Quote höher. Diese Denominator-Entscheidung muss im Dashboard klar dokumentiert werden.

---

## 3. ATZ Kennzahlen (Altersteilzeit)

### Definition
ATZ Kennzahlen basieren auf `ATZ.xlsx` (PersNr nicht eindeutig, Eindeutigkeit nur über PersNr und Phase). Pro Person werden **zwei Phasen** erwartet: `AR` und `FR`.

### Validierte Grundmengen
| Kennzahl | Wert |
|---|---:|
| ATZ Personen gesamt in ATZ.xlsx (unique PersNr) | 50 |
| ATZ aktiv am Stichtag (AR oder FR, Stichtag im Zeitraum) | 42 |
| ATZ Arbeitsphase am Stichtag (AR) | 18 |
| ATZ Freizeitphase am Stichtag (FR) | 24 |

### ATZ Quote (Varianten)

**Formel:**
```
ATZ-Quote = Anzahl ATZ-Personen / Grundgesamtheit × 100
```

| Variante | Grundgesamtheit | Quote (validiert) |
|---|---|---:|
| A | alle Mitarbeitenden (Headcount) | 4.09 % |
| **B (empfohlen)** | ohne Auszubildende (MitarbGruppenbez. != Auszubildende) | 4.58 % |
| C | Status = Aktives Beschäftigungsverhältnis | 4.28 % |

**Subkennzahlen (Variante B):**
| Kennzahl | Wert |
|---|---:|
| ATZ AR Quote | 1.65 % |
| ATZ FR Quote | 2.20 % |

### Implementierung (Quote)
```python
def berechne_atz_quote(df_ma, df_atz, variante="B"):
    df_ma = df_ma.copy()
    df_atz = df_atz.copy()

    df_ma["PersNr"] = df_ma["PersNr"].astype(int).astype(str).str.zfill(6)
    df_atz["PersNr"] = df_atz["PersNr"].astype(int).astype(str).str.zfill(6)

    atz_persnr = set(df_atz["PersNr"].unique())
    atz_count = len(atz_persnr)

    if variante == "A":
        denom = len(df_ma)
    elif variante == "B":
        denom = len(df_ma[df_ma["MitarbGruppenbez."] != "Auszubildende"])
    elif variante == "C":
        denom = len(df_ma[df_ma["Status kundenindividuell"] == "Aktives Beschäftigungsverhältnis"])
    else:
        raise ValueError("variante muss A, B oder C sein")

    return atz_count / denom * 100
```

---

## 4. Planstellen: Besetzung, Vakanzen, Plan Ist (Datenqualitätskritisch)

### 4.1 Besetzungsquote (Zeilenbasiert)

**Definition:**
- Eine Planstellenzeile gilt als **besetzt**, wenn `Personalnummer` gefüllt ist.

**Validierte Werte**
| Kennzahl | Wert |
|---|---:|
| Planstellenzeilen gesamt (Summenzeile entfernt) | 1728 |
| Besetzte Planstellenzeilen | 1247 |
| Unbesetzte Planstellenzeilen (Vakanzen) | 481 |
| Besetzungsquote (zeilenbasiert) | 72.2 % |

### 4.2 Sollarbeitszeit Korrekturen (verbindlich)
- Entferne Summenzeile: `Kürzel OrgEinheit` ist NaN.
- Korrigiere nur Azubi/Trainee Pool:
  - Wenn `Kürzel OrgEinheit == "9910"` und `Sollarbeitszeit == 0.01` → setze auf 39.
- Alle anderen `Sollarbeitszeit == 0.01` außerhalb 9910 → **UNBEKANNT** (nicht schätzen).

**Validierte Mengen**
| Kennzahl | Wert |
|---|---:|
| Sollarbeitszeit = 0,01 gesamt | 737 |
| davon Org 9910 (korrigierbar) | 208 |
| davon außerhalb 9910 (unklar) | 529 |

### 4.3 Vakanz Volumen (nur bekannte Sollarbeitszeit)
**Definition:**
```
Vakanz_FTE_bekannt = Sum(Sollarbeitszeit_corr) / 39 für unbesetzte Zeilen
```

**Validierte Werte**
| Kennzahl | Wert |
|---|---:|
| Vakanz Zeilen gesamt | 481 |
| davon mit bekannter Sollarbeitszeit | 166 |
| davon mit Platzhalter 0,01 außerhalb 9910 (unklar) | 315 |
| Vakanz FTE (bekannt) | 151.38 |

### 4.4 Plan Ist Abgleich / Kapazitätsauslastung (nur eingeschränkt möglich)
**Ist Kapazität:** FTE effektiv gesamt (993.23)

**Soll Kapazität (bekannt):** Summe korrigierte Sollarbeitszeit / 39 = 1,047.07

**WICHTIG:** Da **529 Planstellenzeilen** außerhalb 9910 einen Platzhalter haben, ist die Soll Kapazität **unvollständig**.  
Eine „Auslastung“ kann deshalb nur als **„bezogen auf bekannte Soll Stunden“** ausgewiesen werden, nicht als Gesamtwert.

---

## 5. Fluktuation (Abgänge und Zugänge)

### Definition
Fluktuation erfordert historische Abgänge.

### Datengrundlage
- **Abgänge:** `Austritt` Datum
- **Zugänge:** `Eintritt` Datum

### Datenstand (validiert)
Im aktuellen Export haben **alle** Mitarbeitenden `Austritt = 31.12.9999` → **keine historischen Abgänge im Datenbestand**.

**Implikation:** Abgangsquote und Netto Fluktuation können aktuell nicht berechnet werden, nur Zugänge (Eintritt) als Struktur.

---

## 6. Altersstruktur und Verrentung

### Altersberechnung (verbindlich)
```python
STICHTAG = pd.Timestamp("2025-01-30")
df_ma["Alter_Jahre"] = (STICHTAG - df_ma["GebDatum"]).dt.days / 365.25
```

### Validierte Statistik (Stichtag 30.01.2025)
- Durchschnittsalter: **40.4 Jahre**
- Median Alter: **41.8 Jahre**

### Alterspyramide (5 Jahres Cluster, Headcount)
| Altersgruppe | Headcount |
|---|---:|
| 15-19 | 111 |
| 20-24 | 161 |
| 25-29 | 103 |
| 30-34 | 103 |
| 35-39 | 95 |
| 40-44 | 123 |
| 45-49 | 120 |
| 50-54 | 149 |
| 55-59 | 155 |
| 60-64 | 101 |
| 65-69 | 1 |

### Verrentungswelle (Regelaltersgrenze 67, Stichtag 30.01.2025)
**Logik:**
- Renten Datum = GebDatum plus 67 Jahre
- Zähle Renten Datum zwischen Stichtag und Jahresende des Horizonts (inkl.)

| Horizont | Köpfe | FTE effektiv | FTE roh |
|---|---:|---:|---:|
| bis 2030 (Ende Jahr) | 65 | 32.9 | 33.9 |
| bis 2035 (Ende Jahr) | 229 | 165.7 | 167.7 |
| bis 2040 (Ende Jahr) | 379 | 287.5 | 292.5 |

### Generationen Mix (Headcount, validiert)
Grenzen:
- Boomer: 1946-1964
- Gen X: 1965-1980
- Gen Y: 1981-1996
- Gen Z: ab 1997

| Generation | Headcount |
|---|---:|
| Boomer | 99 |
| Gen X | 452 |
| Gen Y | 339 |
| Gen Z | 332 |

---

## 7. Kostenstruktur Simulation (TVÖD 2026, Grundentgelt)

### Scope (verbindlich)
- Nur Mitarbeitende mit `Tarifarttext == "TVÖD"` werden gemappt.
- Auszubildende (`Auszubildende-VKA`) und Vorstand (`Vorstandsvergütung`) werden nicht gemappt.

### Mapping Logik
Join Keys:
- `Mitarbeiter.TrfGr` (z.B. E10, E9C)
- `Mitarbeiter.St` (1-6)
auf
- `TVÖD.xlsx` Entgeltgruppe/Stufe → Grundentgelt (Monat)

### Berechnungen
- Vollzeit Grundentgelt: direkt aus Tabelle
- **FTE gewichtete Grundentgeltsumme:** Grundentgelt × (BsGrd/100)

**Validierte Werte (TVÖD Mitarbeitende)**
| Kennzahl | Wert |
|---|---:|
| MA mit Gehalt ermittelbar (TVÖD) | 1088 |
| MA ohne Gehalt (Azubis + Vorstand) | 134 |
| Ø Monatsgehalt (Vollzeit) | 4,973.26 € |
| Median Monatsgehalt | 4,746.88 € |
| Monats Gehaltssumme (FTE gewichtet) | 4,558,390.88 € |
| Jahres Gehaltssumme (FTE gewichtet) | 54,700,690.52 € |

**WICHTIGER LIMITIERUNGSHINWEIS:**  
`BsGrd = 0` bedeutet **nicht**, dass Kosten = 0 (z.B. ATZ, Freistellungen). Die Kennzahl ist daher eine **theoretische Grundentgelt Summe nach hinterlegtem Beschäftigungsgrad**, nicht Ist Personalkosten.

---

## 8. Tenure (Betriebszugehörigkeit)

### Berechnung (verbindlich)
Tenure nur für Mitarbeitende mit `Eintritt <= Stichtag`. Für spätere Eintritte wird Tenure negativ und muss als „geplanter Zugang“ behandelt werden.

```python
STICHTAG = pd.Timestamp("2025-01-30")
df_ma["Eintritt"] = pd.to_datetime(df_ma["Eintritt"])

bestand = df_ma[df_ma["Eintritt"] <= STICHTAG].copy()
bestand["Tenure_Jahre"] = (STICHTAG - bestand["Eintritt"]).dt.days / 365.25
```

**Validierte Zusatzinfo:** 86 Personen haben Eintritt **nach** dem Stichtag.

---

## 9. Data Quality Checks (Minimal Set, automatisierbar)

1. **Stichtag hard coded / parametriert**
   - Erwartung: STICHTAG = 2025-01-30
2. **ATZ FR am Stichtag**
   - Erwartung: 24 Personen
3. **FTE effektiv gesamt**
   - Erwartung: 993.23
4. **Ruhende**
   - Erwartung: 54
5. **Planstellen Summenzeile entfernt**
   - Erwartung: Planstellenzeilen nach Filter = 1728
6. **Sollarbeitszeit Platzhalter**
   - Erwartung: 0,01 gesamt 737, davon außerhalb 9910 529
7. **Vakanzen**
   - Erwartung: 481 unbesetzte Zeilen, Vakanz FTE bekannt 151.38
8. **BsGrd = 0 Verteilung**
   - Erwartung: BsGrd=0 gesamt 32, davon ATZ FR 24, davon ATZ AR 8

---

## Zusammenfassung: Kennzahlen Übersicht (Kernauswahl)

| Kennzahl | Formel (Kurz) | Quelle |
|---|---|---|
| Headcount | COUNT(PersNr) | Mitarbeiter.xlsx |
| FTE roh | SUM(BsGrd/100) | Mitarbeiter.xlsx |
| FTE effektiv (MAK) | SUM(FTE_roh × Faktor) | Mitarbeiter.xlsx + ATZ.xlsx |
| Teilzeitquote | COUNT(0<BsGrd<100 & nicht Ruhend & nicht ATZ-FR) / Headcount | Mitarbeiter.xlsx + ATZ.xlsx |
| ATZ Quote (B) | COUNT(unique ATZ PersNr) / COUNT(ohne Azubis) | ATZ.xlsx + Mitarbeiter.xlsx |
| Besetzungsquote | COUNT(Personalnummer not null) / COUNT(Planstellenzeilen) | Planstellen.XLSX |
| Vakanz FTE bekannt | SUM(Sollarbeitszeit_corr vacant)/39 | Planstellen.XLSX |
| Ø Alter | MEAN((Stichtag-GebDatum)/365.25) | Mitarbeiter.xlsx |
| Rente in 10 Jahren | COUNT(GebDatum+67y <= 2035-12-31) | Mitarbeiter.xlsx |
| TVÖD Grundentgelt Summe | SUM(Grundentgelt × BsGrd/100) | Mitarbeiter.xlsx + TVÖD.xlsx |
