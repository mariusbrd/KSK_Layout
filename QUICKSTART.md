# HR Pulse Dashboard - Claude Code Startanleitung

## Quick Start

### 1. Projektordner erstellen und CLAUDE.md kopieren

```bash
mkdir hr_pulse
cd hr_pulse
# CLAUDE.md in diesen Ordner kopieren
```

### 2. Claude Code starten

```bash
claude
```

### 3. Initialer Prompt

Kopiere diesen Prompt in Claude Code:

---

## 🚀 INITIALER PROMPT FÜR CLAUDE CODE

```
Ich möchte ein HR-Analytics-Dashboard "HR Pulse" für eine süddeutsche Bank entwickeln. 

Bitte lies zuerst die CLAUDE.md Datei im aktuellen Verzeichnis - sie enthält die vollständige Spezifikation inkl. Architektur, Datenmodell, Module und Coding-Standards.

Starte mit Phase 1 (Foundation):

1. Erstelle die komplette Projektstruktur gemäß CLAUDE.md
2. Erstelle requirements.txt mit allen Dependencies
3. Erstelle .streamlit/config.toml mit dunklem Theme
4. Implementiere data/synthetic.py - den Generator für realistische Testdaten (~1.200 MA, ~1.700 Planstellen)
5. Implementiere data/loader.py mit Caching
6. Erstelle config/settings.py mit allen Konstanten (Farben, Defaults)
7. Erstelle app.py mit st.navigation Setup für alle 6 Module

Generiere die synthetischen Testdaten und speichere sie als data/sample_data/hr_data.xlsx.

Zeige mir am Ende die lauffähige Grundstruktur, die ich mit `streamlit run app.py` starten kann.
```

---

## 📋 FOLLOW-UP PROMPTS (der Reihe nach)

### Phase 2: Komponenten
```
Implementiere jetzt die wiederverwendbaren Komponenten:

1. components/sidebar.py - Globale Filter mit allen Filtern aus der Spec
2. components/toggle.py - MAK/Euro Toggle der in session_state speichert
3. components/kpi_card.py - Gestylte KPI-Cards mit Trend und Sparkline
4. components/charts.py - Chart-Factory mit folgenden Funktionen:
   - create_population_pyramid
   - create_sunburst
   - create_waterfall
   - create_diverging_bar
   - create_heatmap
   - create_donut
   - create_stacked_area
   - create_gauge
5. assets/style.css - Custom CSS für das dunkle Theme

Teste die Komponenten in app.py mit Beispieldaten.
```

### Phase 3: Überblick-Seite
```
Implementiere pages/1_🏠_Uebersicht.py vollständig:

- 4 KPI-Cards in einer Row (Gesamt-MAK, Besetzungsgrad als Gauge, Vakanz, ATZ-Quote)
- Stacked Area Chart: Kapazität nach Alterskohorte über Zeit
- 2-Column Layout: Donut Arbeitszeit | Donut Geschlecht
- Horizontal Bar: Top 5 OrgEinheiten
- Alert-Banner mit automatischen Warnungen

Alle Charts müssen den MAK/Euro Toggle respektieren!
Integriere die globalen Filter aus der Sidebar.
```

### Phase 4: Demografie
```
Implementiere pages/2_👥_Demografie.py mit 4 Tabs:

Tab "Alter":
- KPI-Row: Ø Alter, Median, Spanne, 55+ Anteil
- Population Pyramid nach Geschlecht
- Stacked Bar: Qualifikation je Alterskohorte
- Tabelle: Kohortendetails

Tab "Geschlecht":
- Donut mit Benchmark-Ring
- Grouped Bar: Geschlecht × Kohorte
- Heatmap: Geschlecht × Ausbildung

Tab "Qualifikation":
- Treemap: Qualifikationsgruppen
- Stacked Bar: Qualifikation × Alterskohorte

Tab "Arbeitszeit":
- KPIs: Vollzeit, Teilzeit, Ø TZ-Grad
- Grouped Bar: VZ/TZ nach Alterskohorte
- Scatter: Alter vs. Beschäftigungsgrad
```

### Phase 5: ATZ + Org
```
Implementiere zwei Seiten:

1. pages/3_🔄_Altersteilzeit.py:
- KPI-Row: ATZ Gesamt, Arbeitsphase, Freistellungsphase
- Funnel: Belegschaft → ATZ-berechtigt → In ATZ → Phasen
- Timeline/Gantt für ATZ-Verläufe (vereinfacht)
- Stacked Bar: ATZ nach OrgEinheit
- Line Chart: ATZ-Quote über Zeit
- Detail-Tabelle mit Export

2. pages/4_🏢_Organisationseinheiten.py:
- Breadcrumb-Navigation
- Sunburst: Org-Hierarchie (klickbar)
- KPI-Row für gewählte Einheit: Soll, Ist, Varianz abs/rel
- Waterfall: Soll → Ist Brücke
- Diverging Lollipop: Varianz je Untereinheit
- Detail-Tabelle mit Drill-through
```

### Phase 6: Jobfamilies
```
Implementiere pages/5_💼_Jobfamilies.py mit 3 Tabs:

Tab "Analyse":
- Treemap: Jobfamilies nach Kapazität
- Radar: Profil der gewählten Jobfamily
- KPI-Cards: MAK, Euro, Ø Alter, Frauenanteil
- Heatmap: Jobfamily × OrgEinheit

Tab "Definition":
- Editor für Jobfamily-Zuordnungen (Pattern-basiert)
- Liste der UNMAPPED Planstellen
- Speichern in config/jobfamilies.json
- Versionierung (valid_from Datum)

Tab "Qualifikationen":
- Tabelle: Mindestqualifikation je Jobfamily
- Gap-Analyse: Ist vs. Soll Qualifikation
- Warnung bei Unterschreitung
```

### Phase 7: Simulation
```
Implementiere das Simulations-Modul:

1. utils/forecast.py:
- SimulationParams Dataclass
- simulate_workforce() Funktion
- Renten-, Fluktuations-, Azubi-, Hiring-Logik
- Monte-Carlo für Konfidenzbänder

2. pages/6_📈_Simulation.py:
- Parameter-Panel mit allen Slidern
- Szenario A/B Vergleich
- Area Chart: Forecast mit Konfidenzband
- Stacked Area: Kapazität nach Kohorte über Zeit
- Waterfall: Veränderungstreiber
- Tabelle: Szenariovergleich
- Heatmap: Prognose Abgänge je OrgEinheit × Jahr
```

### Phase 8: Segment-Builder + Polish
```
Finalisiere das Dashboard:

1. components/segment_builder.py:
- UI für Schnittmengen-Analysen
- Beliebige Merkmalskombinationen (UND-Verknüpfung)
- Ergebnis: Anzahl, MAK, Euro
- Speichern/Laden von Segmenten in config/segments.json

2. utils/export.py:
- CSV-Export für Tabellen
- Excel-Export mit Formatierung

3. Polish:
- Konsistentes Styling auf allen Seiten
- Loading-States für lange Berechnungen
- Error-Handling
- Responsive Layout
- Finale Tests aller Filter und Interaktionen
```

---

## 🔧 HILFREICHE EINZELBEFEHLE

```bash
# Einzelne Datei erstellen/ändern
claude "Erstelle components/kpi_card.py mit einer modernen Card-Komponente"

# Bug fixen
claude "In der Demografie-Seite werden die Alterskohorten nicht richtig gefiltert"

# Feature hinzufügen
claude "Füge einen CSV-Export Button zur Tabelle in der Org-Seite hinzu"

# Styling anpassen
claude "Mache die KPI-Cards größer und füge einen Hover-Effekt hinzu"

# Testen
claude "Teste die Simulation mit verschiedenen Szenarien und zeige mir die Ergebnisse"

# Code erklären
claude "Erkläre mir die Logik in utils/forecast.py"
```

---

## 🎨 DESIGN-REFERENZEN

### Farbpalette (für Prompts)

```
Primär:     #0f172a (Slate 900) - Hintergrund
Sekundär:   #1e293b (Slate 800) - Cards
Akzent 1:   #14b8a6 (Teal 500) - Positive Werte
Akzent 2:   #f59e0b (Amber 500) - Warnungen
Akzent 3:   #f87171 (Red 400) - Kritisch
Text:       #f1f5f9 (Slate 100) - Haupttext
Subtext:    #94a3b8 (Slate 400) - Sekundärtext
```

### Chart-Farbsequenz

```python
COLOR_SEQUENCE = [
    "#14b8a6",  # Teal
    "#3b82f6",  # Blue
    "#8b5cf6",  # Violet
    "#ec4899",  # Pink
    "#f59e0b",  # Amber
    "#10b981",  # Emerald
    "#6366f1",  # Indigo
    "#f43f5e",  # Rose
]
```

---

## ⚠️ HÄUFIGE PROBLEME & LÖSUNGEN

### Problem: Charts reagieren nicht auf Filter
```
claude "Die Charts auf der Überblick-Seite ignorieren die Filter. Stelle sicher, dass apply_filters() vor jeder Chart-Erstellung aufgerufen wird und die gefilterten Daten verwendet werden."
```

### Problem: MAK/Euro Toggle funktioniert nicht überall
```
claude "Der MAK/Euro Toggle wird auf der Demografie-Seite ignoriert. Implementiere eine zentrale get_value_column() Funktion die basierend auf st.session_state['view_mode'] entweder 'FTE_assigned' oder 'Total_Cost_Year' zurückgibt."
```

### Problem: Session State geht verloren
```
claude "Die Filter-Einstellungen gehen beim Seitenwechsel verloren. Stelle sicher dass alle Filter in st.session_state initialisiert werden mit default values in app.py."
```

### Problem: Performance bei großen Daten
```
claude "Die Seite lädt langsam. Füge @st.cache_data Decorator zu allen Datenlade- und Berechnungsfunktionen hinzu. Nutze st.fragment für unabhängige Chart-Bereiche."
```
