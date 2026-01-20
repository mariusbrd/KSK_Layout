# HR Pulse Dashboard

## Projektübersicht

**HR Pulse** ist ein Streamlit-basiertes HR-Analytics-Dashboard für eine süddeutsche Sparkasse/Bank. Es bietet umfassende Personalanalysen mit Fokus auf Kapazitätssteuerung, Demografie, Altersteilzeit und Forecasting.

### Kernfeatures
- 6 Module: Überblick, Demografie, Altersteilzeit, Organisationseinheiten, Jobfamilies, Simulation
- Globaler MAK ↔ Euro Toggle (beeinflusst alle Visuals)
- Flexible Filter (Zeit, Alter, OrgEinheit, Geschlecht, etc.)
- Frei definierbare Alterskohorten
- Segment-Builder für Schnittmengen-Analysen
- Forecast/Simulation mit Szenariovergleich

---

## Tech Stack

```
Python 3.11+
streamlit >= 1.36.0
pandas >= 2.0
plotly >= 5.18
openpyxl >= 3.1
numpy >= 1.24
```

### Styling
- Helles modernes Theme (Navy/Charcoal Basis)
- Akzentfarben: Blue Cola (#196DA1), Persian Red (#CA3433)
- Konsistente Farbkodierung: Grün=gut, Amber=Warnung, Rot=kritisch

---

## Projektstruktur

```
hr_pulse/
├── app.py                      # Haupteinstieg, st.navigation Setup
├── requirements.txt
├── .streamlit/
│   └── config.toml             # Theme-Konfiguration
├── config/
│   ├── settings.py             # Globale Konstanten, Defaults
│   ├── cohorts.json            # Persistierte Kohorten-Definitionen
│   └── segments.json           # Gespeicherte Segmente
├── data/
│   ├── loader.py               # Excel/CSV Loader mit @st.cache_data
│   ├── synthetic.py            # Generator für synthetische Testdaten
│   └── sample_data/
│       └── hr_data.xlsx        # Generierte Testdaten
├── components/
│   ├── __init__.py
│   ├── sidebar.py              # Globale Filter-Sidebar
│   ├── kpi_card.py             # Wiederverwendbare KPI-Card Komponente
│   ├── charts.py               # Chart-Factory (alle Plotly-Charts)
│   ├── toggle.py               # MAK/Euro Toggle Komponente
│   └── segment_builder.py      # Schnittmengen-Tool UI
├── pages/
│   ├── 1_🏠_Uebersicht.py
│   ├── 2_👥_Demografie.py
│   ├── 3_🔄_Altersteilzeit.py
│   ├── 4_🏢_Organisationseinheiten.py
│   ├── 5_💼_Jobfamilies.py
│   └── 6_📈_Simulation.py
├── utils/
│   ├── __init__.py
│   ├── calculations.py         # MAK/Euro Umrechnung, KPI-Berechnungen
│   ├── forecast.py             # Simulationslogik, Monte-Carlo
│   ├── filters.py              # Filter-Logik, Query-Building
│   └── export.py               # CSV/Excel Export-Funktionen
└── assets/
    ├── style.css               # Custom CSS
    └── logo.svg                # Dashboard Logo
```

---

## Datenmodell

### Primäre Datenquellen (aus Excel)

#### 1. Snapshot_Detail (Haupttabelle)
Die zentrale Fakten-Tabelle mit einer Zeile pro Planstelle.

| Spalte | Typ | Beschreibung |
|--------|-----|--------------|
| Kürzel OrgEinheit | str | Org-Einheit Kürzel (z.B. "826") |
| OrgEinheitNr | float | Numerische ID |
| Organisationseinheit | str | Name der Einheit |
| Planstellennr | float | Eindeutige Planstellen-ID |
| Planstelle | str | Bezeichnung der Stelle |
| Sollarbeitszeit | float | Wochenstunden Soll |
| Bewertung Tarifgruppe | str | Tarifgruppe der Stelle (E6-E15) |
| Personalnummer | float | MA-ID (NaN = vakant) |
| Soll_FTE | float | Soll-Kapazität in FTE |
| PersNr | float | Verknüpfung zu Person |
| GebDatum | datetime | Geburtsdatum |
| Text Gsch | str | Geschlecht (männlich/weiblich) |
| Eintritt | datetime | Eintrittsdatum |
| Austritt | datetime | Austrittsdatum (falls vorhanden) |
| BsGrd | float | Beschäftigungsgrad (0-100) |
| Vertragsart | str | Unbefristet/Zeitvertrag/Altersteilzeit |
| Status kundenindividuell | str | Aktiv/Ruhend |
| Tarifarttext | str | TVÖD/Auszubildende-VKA/etc. |
| TrfGr | str | Aktuelle Tarifgruppe Person |
| St | str | Tarifstufe (1-6) |
| FTE_person | float | Ist-FTE der Person |
| Total_Cost_Year | float | Jahreskosten in € |
| Is_Vacant | bool | True wenn Stelle unbesetzt |
| FTE_assigned | float | Zugewiesene FTE |

#### 2. History_Cube (Zeitreihen)
Monatliche Snapshots pro OrgEinheit.

| Spalte | Typ | Beschreibung |
|--------|-----|--------------|
| Kürzel OrgEinheit | str | Org-Einheit |
| Headcount | int | Anzahl Köpfe |
| Total_Cost | float | Gesamtkosten € |
| FTE | float | FTE Summe |
| Vacancy_Count | int | Anzahl Vakanzen |
| Date | datetime | Monatsstichtag |

#### 3. Organisationsstruktur (Hierarchie)
Mapping der Org-Einheiten.

| Spalte | Typ | Beschreibung |
|--------|-----|--------------|
| Kürzel OrgEinheit | str | Kürzel |
| OrgEinheitNr | float | ID |
| Organisationseinheit | str | Name |

### Berechnete Metriken

```python
# MAK (Mitarbeiterkapazität) = FTE
MAK = df['FTE_assigned'].sum()

# Headcount = Anzahl Personen (nicht Stellen)
HC = df[df['PersNr'].notna()]['PersNr'].nunique()

# Besetzungsgrad
Besetzungsgrad = besetzte_stellen / alle_stellen

# Teilzeitquote
TZ_Quote = df[df['FTE_person'] < 0.95].shape[0] / HC

# ATZ-Quote
ATZ_Quote = df[df['Vertragsart'] == 'Altersteilzeit'].shape[0] / HC

# Durchschnittskosten pro FTE
Avg_Cost_FTE = df['Total_Cost_Year'].sum() / MAK
```

---

## Globale Filter (Session State)

```python
# In st.session_state zu speichern:
{
    "view_mode": "MAK",  # oder "Euro"
    "date_range": (date_min, date_max),
    "selected_org_units": [],  # Liste von Kürzeln
    "selected_cohorts": [],  # Liste von Kohorten-Namen
    "selected_genders": ["m", "w"],
    "selected_employment": ["Vollzeit", "Teilzeit"],
    "selected_education": [],
    "selected_atz_status": ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
    "cohort_definitions": {
        "Azubis": (16, 19),
        "Young Professionals": (20, 29),
        "Mid Career": (30, 44),
        "Senior": (45, 54),
        "Pre-Retirement": (55, 62),
        "Retirement Ready": (63, 99)
    }
}
```

---

## Module (Kurzübersicht)

Die 6 Hauptmodule sind vollständig implementiert. Details siehe Code in `pages/`:

- **Überblick** (`pages/1_🏠_Uebersicht.py`): Gesamt-KPIs, Kapazitätstrends nach Kohorte, Verteilungen (Arbeitszeit, Geschlecht), Top OrgEinheiten, Alert-Banner
- **Demografie** (`pages/2_👥_Demografie.py`): 4 Tabs (Alter, Geschlecht, Qualifikation, Arbeitszeit) mit Population Pyramid, Heatmaps, Treemaps, Scatter-Plots
- **Altersteilzeit** (`pages/3_🔄_Altersteilzeit.py`): ATZ-KPIs, Funnel-Visualisierung, Gantt-Timeline, Trendcharts, Detail-Tabelle
- **Organisationseinheiten** (`pages/4_🏢_Organisationseinheiten.py`): Sunburst Org-Hierarchie, KPI-Vergleiche, Waterfall Soll→Ist, Drill-down Logik
- **Jobfamilies** (`pages/5_💼_Jobfamilies.py`): Treemap, Radar-Profile, Heatmap Jobfamily×Org, Editor für Zuordnungen, Gap-Analyse
- **Simulation** (`pages/6_📈_Simulation.py`): Personalvorausschau 1-10 Jahre, Szenario A/B Vergleich, Monte-Carlo für Konfidenzbänder

---

## Synthetische Testdaten

Testdaten für ~1.200 MA werden via `data/synthetic.py` generiert:
- Realistische Alters-/Geschlechterverteilungen (Bankenkontext)
- Tarifgruppen E6-E15, Gehälter nach TVöD-S mit Stufenzuschlägen
- 12 Organisationseinheiten mit verschiedenen Größen
- Vakanzrate 15-35% pro OrgEinheit
- ATZ-Quote 23% (55+), 50/50 Arbeits-/Freistellungsphase
- Qualifikationsverteilungen (Azubis bis Master)

Details siehe Implementierung in `data/synthetic.py`.

---

## Coding-Standards

### Streamlit Best Practices
- `@st.cache_data` für alle Datenlade-Funktionen
- `st.session_state` für alle Filter und Zustände
- Keine globalen Variablen außerhalb von Session State
- `st.fragment` für unabhängige UI-Bereiche (Performance)

### Chart-Standards
- Alle Charts via Plotly Graph Objects (nicht Express)
- Konsistentes Farbschema aus `config/settings.py`
- Hover-Templates mit deutschen Beschriftungen
- Responsive: `fig.update_layout(autosize=True)`
- Legenden: horizontal unterhalb, weißer Hintergrund mit Border

### Daten-Standards
- Alle Datumsfelder als `pd.Timestamp`
- Alle Geldbeträge in Euro (float)
- Alle FTE-Werte als float mit 4 Dezimalstellen
- Fehlende Werte: `np.nan`, nicht None oder ""

### Dokumentation
- Docstrings für alle Funktionen (Google-Style)
- Type Hints für alle Parameter und Returns
- Kommentare nur wo Logik nicht offensichtlich

---

## Wichtige Hinweise

1. **MAK/Euro Toggle**: Muss ALLE Visuals auf ALLEN Seiten beeinflussen. Zentrale Umrechnung in `utils/calculations.py`.

2. **Alterskohorten**: Müssen dynamisch sein und aus `st.session_state['cohort_definitions']` gelesen werden. Alle Berechnungen müssen diese verwenden.

3. **Jobfamily-Mapping**: Initial sind alle als "UNMAPPED" markiert. Der Editor muss Pattern-Matching (Wildcards) unterstützen.

4. **Simulation**: Muss deterministisch sein (Seed für Reproduzierbarkeit), aber auch Monte-Carlo für Konfidenzbänder unterstützen.

5. **Performance**: Bei ~1.700 Zeilen ist Performance unkritisch, aber Caching trotzdem sauber implementieren für Skalierbarkeit.
