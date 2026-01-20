# 🚀 Deployment Guide

## Streamlit Cloud (Empfohlen)

Das HR Pulse Dashboard kann kostenlos auf Streamlit Cloud gehostet werden.

### Voraussetzungen
- GitHub Repository (bereits vorhanden ✅)
- Streamlit Cloud Account (kostenlos)

### Schritt-für-Schritt Anleitung

#### 1. Streamlit Cloud Account erstellen
1. Besuche [share.streamlit.io](https://share.streamlit.io/)
2. Melde dich mit deinem GitHub Account an
3. Autorisiere Streamlit den Zugriff auf deine Repositories

#### 2. App deployen
1. Klicke auf "New app"
2. Wähle dein Repository: `mariusbrd/KSK_Layout`
3. Branch: `main`
4. Main file path: `app.py`
5. App URL (optional): Wähle einen benutzerdefinierten Namen
6. Klicke auf "Deploy!"

#### 3. Automatische Testdaten-Generierung
Das Dashboard erkennt automatisch, dass keine Testdaten vorhanden sind und generiert sie beim ersten Start. Du siehst die Meldung:

```
⚠️ Testdaten nicht gefunden. Generiere automatisch synthetische Daten...
✅ Testdaten erfolgreich generiert!
```

**Wichtig:** Der erste Start dauert ca. 1-2 Minuten, da die synthetischen Daten generiert werden müssen.

#### 4. Fertig!
Deine App ist jetzt live unter: `https://[dein-app-name].streamlit.app`

### Umgebungsvariablen (Optional)

Falls du echte HR-Daten verwenden möchtest (nicht empfohlen für öffentliche Deployments):

1. Gehe zu deiner App auf Streamlit Cloud
2. Klicke auf "Settings" → "Secrets"
3. Füge Secrets im TOML-Format hinzu:

```toml
# Secrets für HR-Datenbank (Beispiel)
[database]
host = "your-db-host.com"
port = 5432
database = "hr_database"
username = "hr_user"
password = "your-secure-password"
```

4. Passe `data/loader.py` an, um Secrets zu lesen

### Ressourcen-Limits

Streamlit Cloud Free Tier:
- **RAM**: 1 GB
- **CPU**: Shared
- **Storage**: Ephemeral (kein persistenter Speicher)

Das HR Pulse Dashboard passt problemlos in diese Limits mit ~1.200 synthetischen Mitarbeitenden.

---

## Andere Hosting-Optionen

### Heroku

1. **Procfile erstellen:**
   ```
   web: sh setup.sh && streamlit run app.py
   ```

2. **setup.sh anpassen:**
   ```bash
   mkdir -p ~/.streamlit/
   echo "\
   [server]\n\
   headless = true\n\
   port = $PORT\n\
   enableCORS = false\n\
   \n\
   " > ~/.streamlit/config.toml
   ```

3. **Deployen:**
   ```bash
   heroku create your-app-name
   git push heroku main
   ```

### Docker

1. **Dockerfile erstellen:**
   ```dockerfile
   FROM python:3.11-slim

   WORKDIR /app

   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt

   COPY . .

   EXPOSE 8501

   CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
   ```

2. **Build & Run:**
   ```bash
   docker build -t hr-pulse .
   docker run -p 8501:8501 hr-pulse
   ```

### AWS / Azure / GCP

Für Enterprise-Deployments siehe die jeweilige Cloud-Provider-Dokumentation:
- **AWS**: EC2 + ALB oder ECS
- **Azure**: App Service oder Container Instances
- **GCP**: Cloud Run oder App Engine

---

## Troubleshooting

### Problem: "ModuleNotFoundError"
**Lösung**: Stelle sicher, dass alle Dependencies in `requirements.txt` aufgelistet sind.

### Problem: "Memory Limit Exceeded"
**Lösung**:
- Reduziere die Anzahl der synthetischen Datensätze in `data/synthetic.py`
- Oder upgrade zu Streamlit Cloud Pro

### Problem: App lädt sehr langsam
**Ursachen**:
- Testdaten werden beim ersten Start generiert (normal)
- Zu viele Daten im Cache

**Lösung**:
- Warte 1-2 Minuten beim ersten Start
- Nutze `@st.cache_data` Decorators (bereits implementiert ✅)

### Problem: Testdaten werden nicht generiert
**Lösung**:
- Prüfe, ob `faker` in requirements.txt vorhanden ist
- Prüfe Logs auf Streamlit Cloud Dashboard

---

## Performance-Optimierung

### Caching
Das Dashboard nutzt bereits Streamlit's Caching:
- `@st.cache_data` für Datenlade-Operationen
- Automatisches Invalidieren bei Code-Änderungen

### Daten-Größe reduzieren
Für schnellere Ladezeiten in `data/synthetic.py`:

```python
# Statt 1.200 Mitarbeitende:
generate_synthetic_data(
    n_employees=600,    # Reduziert
    n_planstellen=850   # Reduziert
)
```

---

## Support

Bei Problemen:
1. Prüfe [Streamlit Cloud Docs](https://docs.streamlit.io/streamlit-community-cloud)
2. Öffne ein Issue auf GitHub
3. Prüfe die App-Logs auf Streamlit Cloud

---

**Live-Demo**: Coming soon...
**GitHub**: https://github.com/mariusbrd/KSK_Layout
