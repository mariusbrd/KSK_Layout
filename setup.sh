#!/bin/bash
# HR Pulse Dashboard - Projekt-Setup Script
# Führe dieses Script im Zielverzeichnis aus

echo "🚀 HR Pulse Dashboard Setup"
echo "=========================="

# Projektstruktur erstellen
echo "📁 Erstelle Projektstruktur..."

mkdir -p hr_pulse/{data/sample_data,config,components,pages,utils,assets,.streamlit}

# Dateien kopieren (falls im gleichen Verzeichnis)
if [ -f "CLAUDE.md" ]; then
    cp CLAUDE.md hr_pulse/
    echo "   ✓ CLAUDE.md kopiert"
fi

if [ -f "requirements.txt" ]; then
    cp requirements.txt hr_pulse/
    echo "   ✓ requirements.txt kopiert"
fi

if [ -f "config/settings.py" ]; then
    cp config/settings.py hr_pulse/config/
    echo "   ✓ config/settings.py kopiert"
fi

if [ -f ".streamlit/config.toml" ]; then
    cp .streamlit/config.toml hr_pulse/.streamlit/
    echo "   ✓ .streamlit/config.toml kopiert"
fi

# __init__.py Dateien erstellen
touch hr_pulse/components/__init__.py
touch hr_pulse/utils/__init__.py
touch hr_pulse/config/__init__.py
echo "   ✓ __init__.py Dateien erstellt"

# Leere JSON-Config-Dateien
echo '{}' > hr_pulse/config/cohorts.json
echo '{}' > hr_pulse/config/segments.json
echo '{}' > hr_pulse/config/jobfamilies.json
echo "   ✓ Config-Dateien initialisiert"

# Virtual Environment (optional)
echo ""
read -p "🐍 Virtual Environment erstellen? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    cd hr_pulse
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    echo "   ✓ Virtual Environment erstellt und Dependencies installiert"
    cd ..
fi

echo ""
echo "✅ Setup abgeschlossen!"
echo ""
echo "Nächste Schritte:"
echo "  1. cd hr_pulse"
echo "  2. claude                    # Claude Code starten"
echo "  3. Kopiere den Prompt aus QUICKSTART.md"
echo ""
echo "Oder manuell:"
echo "  1. cd hr_pulse"
echo "  2. pip install -r requirements.txt"
echo "  3. streamlit run app.py      # (nachdem app.py erstellt wurde)"
