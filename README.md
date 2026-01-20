# 📊 HR Pulse Dashboard

> Ein modernes, interaktives HR-Analytics-Dashboard für Personalplanung und Workforce-Analysen

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.36+-red.svg)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

![HR Pulse Dashboard Preview](assets/screenshot.png)

## 🎯 Über das Projekt

**HR Pulse** ist ein umfassendes HR-Analytics-Dashboard, entwickelt für die Personalplanung in Banken und Sparkassen. Es bietet tiefgreifende Einblicke in Kapazitätssteuerung, demografische Entwicklungen, Altersteilzeit-Management und Workforce-Forecasting.

### ✨ Hauptfunktionen

- 📈 **6 Analytics-Module**: Überblick, Demografie, Altersteilzeit, Org-Einheiten, Jobfamilies, Simulation
- 🔄 **MAK ↔ Euro Toggle**: Dynamischer Wechsel zwischen Kapazitäts- und Kostenansicht
- 🎨 **12+ Visualisierungen**: Population Pyramids, Heatmaps, Sunbursts, Gantt-Charts, uvm.
- 🎯 **Flexible Filter**: Zeit, Organisation, Alter, Geschlecht, Qualifikation, ATZ-Status
- 🔮 **Monte-Carlo Simulation**: Workforce-Forecasting mit Unsicherheitsmodellierung
- 💼 **Jobfamily-Mapping**: Pattern-basierte Zuordnung mit Wildcard-Support
- 📊 **Export-Funktionen**: CSV-Export für alle Detailtabellen

## 🚀 Schnellstart

### Voraussetzungen

- Python 3.11 oder höher
- pip (Python Package Manager)

### Installation

1. **Repository klonen**
   ```bash
   git clone https://github.com/IhrUsername/hr-pulse-dashboard.git
   cd hr-pulse-dashboard
   ```

2. **Dependencies installieren**
   ```bash
   pip install -r requirements.txt
   ```

3. **Dashboard starten**
   ```bash
   streamlit run app.py
   ```

Das Dashboard öffnet sich automatisch im Browser unter `http://localhost:8501`

> **💡 Für Cloud-Deployment**: Die Testdaten werden automatisch beim ersten Start generiert. Siehe [DEPLOYMENT.md](DEPLOYMENT.md) für Details.

### Testdaten generieren

Das Projekt enthält einen Generator für synthetische HR-Daten:

```bash
python data/synthetic.py
```

Erstellt ~1.200 Mitarbeitende und ~1.700 Planstellen mit realistischen Verteilungen.

## 📁 Projektstruktur

```
hr_pulse/
├── app.py                      # Haupteinstiegspunkt
├── requirements.txt            # Python-Dependencies
├── .streamlit/
│   └── config.toml             # Streamlit Theme-Konfiguration
├── config/
│   ├── settings.py             # Globale Konstanten & Farben
│   ├── cohorts.json            # Alterskohorten-Definitionen
│   └── jobfamilies.json        # Jobfamily-Mappings
├── data/
│   ├── loader.py               # Daten-Loader mit Caching
│   ├── synthetic.py            # Testdaten-Generator
│   └── sample_data/
│       └── hr_data.xlsx        # Generierte HR-Daten
├── components/
│   ├── sidebar.py              # Globale Filter-Sidebar
│   ├── kpi_card.py             # KPI-Card Komponente
│   ├── charts.py               # Chart-Factory (Plotly)
│   └── toggle.py               # MAK/Euro Toggle
├── pages/
│   ├── 1_🏠_Uebersicht.py
│   ├── 2_👥_Demografie.py
│   ├── 3_🔄_Altersteilzeit.py
│   ├── 4_🏢_Organisationseinheiten.py
│   ├── 5_💼_Jobfamilies.py
│   └── 6_📈_Simulation.py
├── utils/
│   ├── calculations.py         # KPI-Berechnungen
│   ├── simulation.py           # Workforce-Simulation
│   └── jobfamily_matcher.py    # Pattern-Matching
└── assets/
    ├── style.css               # Custom CSS
    └── logo.svg                # Dashboard Logo
```

## 📊 Module im Detail

### 1. 🏠 Überblick
Zentrale KPIs und Dashboard-Zusammenfassung
- Gesamt-MAK/Kosten, Besetzungsgrad, Vakanzen, ATZ-Quote
- Kapazitätsentwicklung nach Alterskohorten
- Verteilungen nach Geschlecht, Arbeitszeit, Top-Organisationen

### 2. 👥 Demografie
Detaillierte demografische Analysen
- **Alter**: Population Pyramid, Kohorten-Tabellen
- **Geschlecht**: Verteilungen mit Benchmarks, Heatmaps
- **Qualifikation**: Treemaps, Qualifikations-Mix
- **Arbeitszeit**: VZ/TZ-Analysen, Scatter Alter vs. Beschäftigungsgrad

### 3. 🔄 Altersteilzeit
ATZ-Management und Planung
- Funnel-Analyse (Berechtigt → In ATZ → Phasen)
- Gantt-Timeline für ATZ-Verläufe
- ATZ-Entwicklung nach Organisationen
- Export-Funktionen für Planungsdaten

### 4. 🏢 Organisationseinheiten
Hierarchische Org-Analysen
- Sunburst-Visualisierung der Struktur
- Soll/Ist-Vergleich mit Waterfall-Charts
- Drill-down in einzelne Einheiten
- Varianz-Analysen und Besetzungsgrade

### 5. 💼 Jobfamilies
Jobfamily-Definitionen und Gap-Analysen
- Pattern-basiertes Mapping (Wildcards)
- Treemap und Heatmap Visualisierungen
- Interaktiver Editor für Definitionen
- Qualifikations-Gap-Analyse

### 6. 📈 Simulation
Workforce-Forecasting mit Monte-Carlo
- Szenario-Editor (Renteneintritte, Fluktuation, Hiring)
- Konfidenzbänder (10.-90. Perzentil)
- A/B Szenariovergleich
- Export für Prognose-Daten

## 🎨 Features

### Globale Filter (Sidebar)
- **Zeitraum**: Flexible Datumsauswahl
- **Organisationseinheiten**: Multi-Select mit Suche
- **Alterskohorten**: Anpassbare Definitionen
- **Geschlecht, Arbeitszeit, Qualifikation, ATZ-Status**
- **Filter zurücksetzen**: Ein-Klick Reset

### MAK/Euro Toggle
Dynamischer Wechsel zwischen:
- **MAK-Ansicht**: FTE-Werte (Mitarbeiterkapazität)
- **Euro-Ansicht**: Gesamtkosten in €

Alle KPIs und Charts passen sich automatisch an!

## 🛠 Technologie-Stack

- **[Python](https://www.python.org/)** 3.11+ - Programmiersprache
- **[Streamlit](https://streamlit.io/)** 1.36+ - Web-Framework
- **[Pandas](https://pandas.pydata.org/)** 2.0+ - Datenverarbeitung
- **[Plotly](https://plotly.com/)** 5.18+ - Interaktive Visualisierungen
- **[NumPy](https://numpy.org/)** 1.24+ - Numerische Berechnungen

## ⚙️ Konfiguration

### Theme anpassen

Theme-Einstellungen in `.streamlit/config.toml`:
```toml
[theme]
primaryColor = "#0088DE"        # Blue Cola
backgroundColor = "#FFFFFF"      # Weiß
secondaryBackgroundColor = "#F5F5F5"  # Helles Grau
textColor = "#757575"           # Sonic Silver
```

### Farben und Konstanten

Anpassungen in `config/settings.py`:
- Farbpalette (COLORS dict)
- Alterskohorten (DEFAULT_COHORTS)
- Tarifgruppen (TARIFF_GROUPS)
- Formatierungs-Funktionen

## 📝 Verwendung mit echten Daten

Das Dashboard ist für synthetische Testdaten vorkonfiguriert. Für den Einsatz mit echten HR-Daten:

1. **Datenformat anpassen**: Excel/CSV mit erforderlichen Spalten (siehe `CLAUDE.md`)
2. **Loader anpassen**: `data/loader.py` für Ihr Datenmodell konfigurieren
3. **Datenschutz beachten**: Sensible HR-Daten niemals in Git committen!

## 🤝 Beitragen

Contributions sind willkommen! Bitte beachten Sie:

1. Fork des Repositories
2. Feature-Branch erstellen (`git checkout -b feature/AmazingFeature`)
3. Änderungen committen (`git commit -m 'Add AmazingFeature'`)
4. Branch pushen (`git push origin feature/AmazingFeature`)
5. Pull Request erstellen

## 📄 Lizenz

Dieses Projekt ist unter der MIT-Lizenz lizenziert - siehe [LICENSE](LICENSE) für Details.

## 👨‍💻 Entwickelt mit

- Claude Code (Anthropic)
- VS Code
- Git & GitHub

## 📚 Dokumentation

Weitere technische Details finden Sie in:
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Cloud-Deployment (Streamlit Cloud, Heroku, Docker)
- **[CLAUDE.md](CLAUDE.md)** - Technische Spezifikation & Architektur
- **[QUICKSTART.md](QUICKSTART.md)** - Erweiterte Setup-Anleitung
- Inline-Dokumentation in allen Modulen

## 🎯 Roadmap

Mögliche zukünftige Erweiterungen:
- [ ] Integration mit HR-Datenbanken (SAP, etc.)
- [ ] Benutzer-Authentifizierung & Rollen
- [ ] Automatisierte E-Mail-Reports
- [ ] Dashboard-Historie & Snapshots
- [ ] Advanced Analytics (ML, Predictive Modeling)
- [ ] Mobile-Optimierung
- [ ] Multi-Language Support

---

**Entwickelt für die moderne Personalplanung** | *HR Pulse Dashboard © 2026*
