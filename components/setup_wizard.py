"""
Setup-Wizard fuer Job Families.

Gefuehrter Erststart-Prozess fuer Nutzer ohne Job Family Definitionen.
Ermoeglicht Auswahl zwischen Templates, Auto-Erkennung und eigener Definition.
"""

import streamlit as st
import pandas as pd
import json
import os
import sys
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataloader.jobfamily_matcher import (
    load_jobfamily_definitions,
    save_jobfamily_definitions,
    assign_jobfamilies,
    get_jobfamily_stats
)
from utils.template_loader import (
    get_available_templates,
    load_template,
    get_template_preview,
    apply_template,
    get_matching_preview,
    ensure_v2_compatibility
)
from utils.auto_classifier import (
    analyze_job_titles,
    convert_to_definitions,
    merge_similar_families,
    ClusteringConfig,
    SKLEARN_AVAILABLE
)
from utils.import_export import (
    import_from_excel,
    import_from_csv,
    import_from_json,
    import_mapping_list,
    export_to_excel,
    export_to_json,
    export_mapping_report,
    detect_file_format,
    get_column_suggestions,
    validate_import_file,
    OPENPYXL_AVAILABLE
)


# =============================================================================
# KONSTANTEN
# =============================================================================

WIZARD_STEPS = [
    "Willkommen",
    "Datenquelle waehlen",
    "Job Families definieren",
    "Zuordnung pruefen",
    "Abschluss"
]

# Basis-Templates (werden in Phase 2 erweitert)
BASE_TEMPLATES = {
    "sparkassen": {
        "name": "Sparkassen & Banken",
        "description": "Vorgefertigte Job Families fuer Sparkassen und Genossenschaftsbanken",
        "icon": "🏦",
        "family_count": 10,
        "jobfamilies": {
            "Kundenberatung Privat": {
                "description": "Privatkundenberater, Service, Kasse",
                "patterns": ["*Kundenberater*Privat*", "*Privatkundenberater*", "*Serviceberater*", "*Service*Center*"],
                "min_qualification": "Bankkaufmann/-frau",
                "manual_assignments": []
            },
            "Kundenberatung Firmen": {
                "description": "Firmenkundenberater, Gewerbekunden",
                "patterns": ["*Firmenkundenberater*", "*Gewerbekundenberater*", "*Unternehmenskundenberater*"],
                "min_qualification": "Bankfachwirt/-in",
                "manual_assignments": []
            },
            "Vermögensberatung": {
                "description": "Private Banking, Vermoegensmanagement",
                "patterns": ["*Vermoegensberater*", "*Private*Banking*", "*Wealth*", "*Anlageberater*"],
                "min_qualification": "Bankbetriebswirt/-in",
                "manual_assignments": []
            },
            "Baufinanzierung": {
                "description": "Immobilienfinanzierung, Baufinanzierungsberater",
                "patterns": ["*Baufinanzierung*", "*Immobilienfinanzierung*", "*Baufi*"],
                "min_qualification": "Bankfachwirt/-in",
                "manual_assignments": []
            },
            "Marktfolge Kredit": {
                "description": "Kreditanalyse, Marktfolge, Sachbearbeitung Kredit",
                "patterns": ["*Marktfolge*", "*Kreditanalyse*", "*Kreditsachbearbeitung*"],
                "min_qualification": "Bankkaufmann/-frau",
                "manual_assignments": []
            },
            "IT & Digitalisierung": {
                "description": "IT-Administration, Entwicklung, Digitalisierung",
                "patterns": ["*IT-*", "*IT *", "*Administrator*", "*Entwickler*", "*Digital*", "*Software*"],
                "min_qualification": "Fachinformatiker/-in",
                "manual_assignments": []
            },
            "Personal & Organisation": {
                "description": "Personalwesen, HR, Organisation",
                "patterns": ["*Personal*", "*HR*", "*Organisation*", "*Recruiting*"],
                "min_qualification": "Bankkaufmann/-frau",
                "manual_assignments": []
            },
            "Controlling & Finanzen": {
                "description": "Finanzen, Controlling, Buchhaltung",
                "patterns": ["*Controlling*", "*Controller*", "*Buchhaltung*", "*Rechnungswesen*", "*Bilanz*"],
                "min_qualification": "Bankfachwirt/-in",
                "manual_assignments": []
            },
            "Compliance & Recht": {
                "description": "Compliance, Recht, Revision",
                "patterns": ["*Compliance*", "*Recht*", "*Revision*", "*Audit*", "*Geldwaesche*"],
                "min_qualification": "Bankfachwirt/-in",
                "manual_assignments": []
            },
            "Fuehrungskraefte": {
                "description": "Teamleiter, Abteilungsleiter, Geschaeftsfuehrung",
                "patterns": ["*Leiter*", "*Vorstand*", "*Direktor*", "*Geschaeftsfuehr*", "*Teamleiter*"],
                "min_qualification": "Bankbetriebswirt/-in",
                "manual_assignments": []
            }
        }
    },
    "universal": {
        "name": "Universal / Allgemein",
        "description": "Branchenuebergreifende Job Families fuer alle Unternehmen",
        "icon": "🏢",
        "family_count": 8,
        "jobfamilies": {
            "Vertrieb & Kundenservice": {
                "description": "Vertrieb, Kundenbetreuung, Service",
                "patterns": ["*Vertrieb*", "*Kundenberater*", "*Kundenservice*", "*Sales*", "*Account*"],
                "min_qualification": "Kaufmann/-frau",
                "manual_assignments": []
            },
            "IT & Technik": {
                "description": "IT, Entwicklung, technische Berufe",
                "patterns": ["*IT-*", "*IT *", "*Entwickler*", "*Techniker*", "*Software*", "*Admin*"],
                "min_qualification": "Fachinformatiker/-in",
                "manual_assignments": []
            },
            "Finanzen & Controlling": {
                "description": "Buchhaltung, Controlling, Finanzen",
                "patterns": ["*Controlling*", "*Buchhaltung*", "*Finanz*", "*Bilanz*"],
                "min_qualification": "Kaufmann/-frau",
                "manual_assignments": []
            },
            "Personal & HR": {
                "description": "Personalwesen, Recruiting, HR",
                "patterns": ["*Personal*", "*HR*", "*Recruiting*", "*Human*Resources*"],
                "min_qualification": "Kaufmann/-frau",
                "manual_assignments": []
            },
            "Marketing & Kommunikation": {
                "description": "Marketing, PR, Kommunikation",
                "patterns": ["*Marketing*", "*Kommunikation*", "*PR*", "*Presse*"],
                "min_qualification": "Kaufmann/-frau",
                "manual_assignments": []
            },
            "Verwaltung & Assistenz": {
                "description": "Verwaltung, Assistenz, Sekretariat",
                "patterns": ["*Assistenz*", "*Sekretariat*", "*Verwaltung*", "*Sachbearbeiter*"],
                "min_qualification": "Kaufmann/-frau",
                "manual_assignments": []
            },
            "Produktion & Logistik": {
                "description": "Produktion, Lager, Logistik",
                "patterns": ["*Produktion*", "*Lager*", "*Logistik*", "*Fertigung*"],
                "min_qualification": "Facharbeiter/-in",
                "manual_assignments": []
            },
            "Management & Fuehrung": {
                "description": "Fuehrungskraefte, Management",
                "patterns": ["*Leiter*", "*Manager*", "*Direktor*", "*Geschaeftsfuehr*", "*Head*of*"],
                "min_qualification": "Bachelor",
                "manual_assignments": []
            }
        }
    }
}


# =============================================================================
# SESSION STATE HELPERS
# =============================================================================

def init_wizard_state():
    """Initialisiert Wizard Session State."""
    if "wizard_step" not in st.session_state:
        st.session_state["wizard_step"] = 0
    if "wizard_source" not in st.session_state:
        st.session_state["wizard_source"] = None
    if "wizard_definitions" not in st.session_state:
        st.session_state["wizard_definitions"] = None
    if "skip_wizard" not in st.session_state:
        st.session_state["skip_wizard"] = False


def reset_wizard():
    """Setzt Wizard zurueck."""
    st.session_state["wizard_step"] = 0
    st.session_state["wizard_source"] = None
    st.session_state["wizard_definitions"] = None


# =============================================================================
# WIZARD STEPS
# =============================================================================

def render_welcome_step():
    """Rendert den Willkommens-Schritt."""

    st.markdown("## Willkommen beim HR Pulse Dashboard")

    st.markdown("""
    Um Ihre Personalanalysen optimal zu strukturieren, benötigen wir
    **Job Family Definitionen** - Gruppierungen ähnlicher Tätigkeiten.
    """)

    # Info-Box
    with st.container(border=True):
        st.markdown("### Was sind Job Families?")
        st.markdown("""
        Job Families gruppieren ähnliche Positionen in Ihrem Unternehmen:

        - **IT & Digitalisierung**: Entwickler, Administratoren, Data Scientists
        - **Beratung & Vertrieb**: Kundenberater, Vertriebsmitarbeiter
        - **Verwaltung**: Sachbearbeiter, Assistenz, Controlling

        Diese Gruppierung ermöglicht tiefere Analysen und bessere Planung.
        """)

    st.markdown("---")

    st.markdown("### Dieser Wizard hilft Ihnen:")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("""
        **1. Template laden**

        Vorgefertigte Branchenlösungen für einen schnellen Start.
        """)

    with col2:
        st.markdown("""
        **2. Auto-Erkennung**

        KI-basierte Vorschläge aus Ihren vorhandenen Job-Titeln.
        """)

    with col3:
        st.markdown("""
        **3. Selbst definieren**

        Eigene Job Families von Grund auf anlegen.
        """)

    st.markdown("---")

    # Navigation
    col1, col2 = st.columns([3, 1])

    with col2:
        if st.button("Weiter", type="primary", use_container_width=True):
            st.session_state["wizard_step"] = 1
            st.rerun()

    with col1:
        if st.button("Wizard überspringen", use_container_width=True):
            st.session_state["skip_wizard"] = True
            # Leere Definitionen erstellen
            empty_definitions = {
                "jobfamilies": {},
                "manual_overrides": {},
                "metadata": {
                    "version": "2.0",
                    "last_modified": datetime.now().isoformat()
                }
            }
            save_jobfamily_definitions(empty_definitions)
            st.rerun()


def render_source_selection_step():
    """Rendert den Datenquellen-Auswahl-Schritt."""

    st.markdown("## Wie möchten Sie starten?")
    st.markdown("Wählen Sie eine Option für die Erstellung Ihrer Job Families:")

    st.markdown("---")

    col1, col2, col3 = st.columns(3)

    with col1:
        with st.container(border=True):
            st.markdown("### 📋 Branchen-Template")
            st.markdown("""
            Vorgefertigte Job Families für:
            - Sparkassen & Banken
            - Versicherungen
            - Universal (branchenübergreifend)

            **Empfohlen für:** Schnellen Start mit bewährten Definitionen
            """)
            if st.button("Template wählen", key="btn_template", use_container_width=True, type="primary"):
                st.session_state["wizard_source"] = "template"
                st.session_state["wizard_step"] = 2
                st.rerun()

    with col2:
        with st.container(border=True):
            st.markdown("### 🤖 Auto-Erkennung")
            st.markdown("""
            Intelligente Analyse Ihrer Job-Titel:
            - Clustering ähnlicher Bezeichnungen
            - Pattern-Vorschläge
            - Manuelle Feinabstimmung

            **Empfohlen für:** Individuelle Anpassung
            """)
            if not SKLEARN_AVAILABLE:
                st.warning("Benötigt scikit-learn")
            if st.button("Automatisch erkennen", key="btn_auto", use_container_width=True,
                        type="primary", disabled=not SKLEARN_AVAILABLE):
                st.session_state["wizard_source"] = "auto"
                st.session_state["wizard_step"] = 2
                st.rerun()

    with col3:
        with st.container(border=True):
            st.markdown("### 📁 Datei importieren")
            st.markdown("""
            Aus vorhandener Datei laden:
            - Excel (.xlsx)
            - CSV
            - JSON

            **Empfohlen für:** Migration bestehender Daten
            """)
            if st.button("Datei importieren", key="btn_import", use_container_width=True):
                st.session_state["wizard_source"] = "import"
                st.session_state["wizard_step"] = 2
                st.rerun()

    st.markdown("---")

    # Zusätzliche Option: Selbst definieren
    with st.expander("✏️ Oder: Komplett selbst definieren"):
        st.markdown("""
        Starten Sie mit einer leeren Vorlage und definieren Sie
        alle Job Families manuell von Grund auf.
        """)
        if st.button("Leere Vorlage starten", key="btn_custom", use_container_width=True):
            st.session_state["wizard_source"] = "custom"
            st.session_state["wizard_definitions"] = {
                "jobfamilies": {},
                "manual_overrides": {},
                "metadata": {"version": "2.0"}
            }
            st.session_state["wizard_step"] = 2
            st.rerun()

    st.markdown("---")

    # Zurück-Button
    if st.button("Zurück"):
        st.session_state["wizard_step"] = 0
        st.rerun()


def render_definition_step():
    """Rendert den Definitions-Schritt basierend auf gewaehlter Quelle."""

    source = st.session_state.get("wizard_source")

    if source == "template":
        render_template_selection()
    elif source == "auto":
        render_auto_detection()
    elif source == "import":
        render_import_step()
    elif source == "custom":
        render_custom_definition()
    else:
        st.error("Keine Datenquelle ausgewählt.")
        if st.button("Zurück zur Auswahl"):
            st.session_state["wizard_step"] = 1
            st.rerun()


def render_template_selection():
    """Rendert Template-Auswahl mit Vorschau und Matching-Statistiken."""

    st.markdown("## Branchen-Template wählen")
    st.markdown("Wählen Sie ein vorgefertigtes Template für Ihre Branche:")

    st.markdown("---")

    # Lade verfügbare Templates
    templates = get_available_templates()

    if not templates:
        st.warning("Keine Templates gefunden. Bitte prüfen Sie das Verzeichnis config/templates/")
        if st.button("Zurück"):
            st.session_state["wizard_step"] = 1
            st.rerun()
        return

    # Lade Daten für Matching-Vorschau
    try:
        from dataloader.loader import load_and_prepare_data
        snapshot_df, _, _, _ = load_and_prepare_data()
        has_data = True
    except Exception:
        snapshot_df = None
        has_data = False

    # Template-Cards (3 Spalten für 3 Templates)
    num_cols = min(3, len(templates))
    cols = st.columns(num_cols)

    for i, template in enumerate(templates):
        with cols[i % num_cols]:
            with st.container(border=True):
                st.markdown(f"### {template['icon']} {template['name']}")
                st.caption(template['description'])

                # Metriken
                col1, col2 = st.columns(2)
                col1.metric("Job Families", template['family_count'])

                # Matching-Vorschau wenn Daten vorhanden
                if has_data and snapshot_df is not None:
                    _, report = apply_template(template['id'], snapshot_df, "Planstelle")
                    rate = report.get('matching_rate', 0)
                    col2.metric("Matching-Quote", f"{rate:.0%}")

                # Vorschau der enthaltenen Families
                preview = get_template_preview(template['id'])
                with st.expander(f"Enthaltene Job Families ({template['family_count']})"):
                    for family in preview.get('families', []):
                        color = family.get('color', '#808080')
                        st.markdown(
                            f"<span style='color:{color};'>●</span> **{family['name']}** "
                            f"<small>({family['pattern_count']} Patterns)</small>",
                            unsafe_allow_html=True
                        )
                        if family.get('description'):
                            st.caption(f"  {family['description']}")

                # Matching-Vorschau wenn Daten vorhanden
                if has_data and snapshot_df is not None:
                    with st.expander("Matching-Vorschau"):
                        matching_preview = get_matching_preview(template['id'], snapshot_df, "Planstelle", limit=10)

                        matched = [m for m in matching_preview if m['is_matched']]
                        unmatched = [m for m in matching_preview if not m['is_matched']]

                        if matched:
                            st.markdown("**Zugeordnete Jobs:**")
                            for m in matched[:5]:
                                st.markdown(f"- {m['job_title']} → *{m['matched_family']}*")

                        if unmatched:
                            st.markdown("**Nicht zugeordnet:**")
                            for m in unmatched[:3]:
                                st.markdown(f"- {m['job_title']}")

                # Auswahl-Button
                if st.button(
                    f"'{template['name']}' verwenden",
                    key=f"tpl_{template['id']}",
                    use_container_width=True,
                    type="primary"
                ):
                    # Template laden und anwenden
                    definitions, _ = apply_template(template['id'], snapshot_df, "Planstelle")

                    if definitions:
                        st.session_state["wizard_definitions"] = definitions
                        st.session_state["wizard_step"] = 3
                        st.rerun()
                    else:
                        st.error(f"Fehler beim Laden des Templates '{template['name']}'")

    st.markdown("---")

    # Zurück-Button
    if st.button("Zurück"):
        st.session_state["wizard_step"] = 1
        st.rerun()


def render_import_step():
    """Rendert UI fuer Datei-Import."""

    st.markdown("## 📁 Job Families importieren")
    st.markdown("Laden Sie Ihre Job Family Definitionen aus einer vorhandenen Datei:")

    st.markdown("---")

    # Import-Typ Auswahl
    import_type = st.radio(
        "Import-Format wählen",
        ["Excel (.xlsx)", "CSV", "JSON"],
        horizontal=True
    )

    st.markdown("---")

    # File Uploader
    if import_type == "Excel (.xlsx)":
        uploaded_file = st.file_uploader(
            "Excel-Datei hochladen",
            type=["xlsx", "xls"],
            help="Unterstützte Formate: .xlsx, .xls"
        )
        file_type = "excel"
    elif import_type == "CSV":
        uploaded_file = st.file_uploader(
            "CSV-Datei hochladen",
            type=["csv"],
            help="Unterstützt verschiedene Trennzeichen (;, , Tab)"
        )
        file_type = "csv"
    else:  # JSON
        uploaded_file = st.file_uploader(
            "JSON-Datei hochladen",
            type=["json"],
            help="Unterstützt v1 und v2 Format"
        )
        file_type = "json"

    if uploaded_file:
        # Validiere Datei
        is_valid, message, preview_df = validate_import_file(uploaded_file, file_type)

        if not is_valid:
            st.error(f"Fehler: {message}")
        else:
            st.success(f"✅ {message}")

            # JSON hat kein Spalten-Mapping
            if file_type == "json":
                st.markdown("### Vorschau")

                # Lade und zeige JSON-Vorschau
                uploaded_file.seek(0)
                definitions, report = import_from_json(uploaded_file)

                if report.get("error"):
                    st.error(report["error"])
                else:
                    st.info(f"**{report['families_created']} Job Families** mit "
                           f"**{report['patterns_imported']} Patterns** gefunden")

                    # Zeige Families
                    with st.expander("Enthaltene Job Families", expanded=True):
                        for family_name in list(definitions.get("jobfamilies", {}).keys())[:10]:
                            family = definitions["jobfamilies"][family_name]
                            st.markdown(f"- **{family_name}** ({len(family.get('patterns', []))} Patterns)")

                    if st.button("✅ Importieren", type="primary", use_container_width=True):
                        st.session_state["wizard_definitions"] = definitions
                        st.session_state["wizard_step"] = 3
                        st.rerun()

            else:  # Excel oder CSV
                st.markdown("### Vorschau der Daten")
                st.dataframe(preview_df, use_container_width=True)

                st.markdown("---")
                st.markdown("### Spalten zuordnen")

                # Hole Spalten-Vorschläge
                uploaded_file.seek(0)
                if file_type == "excel":
                    df_full = pd.read_excel(uploaded_file)
                else:
                    df_full = pd.read_csv(uploaded_file, delimiter=";")
                    if len(df_full.columns) == 1:
                        uploaded_file.seek(0)
                        df_full = pd.read_csv(uploaded_file, delimiter=",")

                columns = ["---"] + list(df_full.columns)
                suggestions = get_column_suggestions(df_full)

                col1, col2 = st.columns(2)

                with col1:
                    # Family-Spalte (Pflicht)
                    default_family = suggestions["family"][0] if suggestions["family"] else columns[1] if len(columns) > 1 else "---"
                    family_col = st.selectbox(
                        "Job Family Spalte *",
                        columns,
                        index=columns.index(default_family) if default_family in columns else 0,
                        help="Pflichtfeld: Spalte mit Job Family Namen"
                    )

                    # Pattern-Spalte (Optional)
                    default_pattern = suggestions["pattern"][0] if suggestions["pattern"] else "---"
                    pattern_col = st.selectbox(
                        "Pattern Spalte (optional)",
                        columns,
                        index=columns.index(default_pattern) if default_pattern in columns else 0,
                        help="Spalte mit Wildcard-Patterns wie *IT-*"
                    )

                with col2:
                    # Beschreibung-Spalte (Optional)
                    default_desc = suggestions["description"][0] if suggestions["description"] else "---"
                    desc_col = st.selectbox(
                        "Beschreibung Spalte (optional)",
                        columns,
                        index=columns.index(default_desc) if default_desc in columns else 0
                    )

                    # Qualifikation-Spalte (Optional)
                    default_qual = suggestions["qualification"][0] if suggestions["qualification"] else "---"
                    qual_col = st.selectbox(
                        "Qualifikation Spalte (optional)",
                        columns,
                        index=columns.index(default_qual) if default_qual in columns else 0
                    )

                # CSV-spezifische Optionen
                if file_type == "csv":
                    with st.expander("CSV-Optionen"):
                        delimiter = st.selectbox(
                            "Trennzeichen",
                            [";", ",", "Tab"],
                            index=0
                        )
                        if delimiter == "Tab":
                            delimiter = "\t"

                st.markdown("---")

                # Import-Button
                if family_col != "---":
                    if st.button("✅ Importieren", type="primary", use_container_width=True):
                        uploaded_file.seek(0)

                        if file_type == "excel":
                            definitions, report = import_from_excel(
                                uploaded_file,
                                family_column=family_col,
                                pattern_column=pattern_col if pattern_col != "---" else None,
                                description_column=desc_col if desc_col != "---" else None,
                                qualification_column=qual_col if qual_col != "---" else None
                            )
                        else:  # CSV
                            definitions, report = import_from_csv(
                                uploaded_file,
                                family_column=family_col,
                                pattern_column=pattern_col if pattern_col != "---" else None,
                                description_column=desc_col if desc_col != "---" else None,
                                qualification_column=qual_col if qual_col != "---" else None,
                                delimiter=delimiter if file_type == "csv" else ";"
                            )

                        if report.get("error"):
                            st.error(report["error"])
                        else:
                            st.success(
                                f"✅ **{report['families_created']} Job Families** mit "
                                f"**{report['patterns_imported']} Patterns** importiert"
                            )

                            if report.get("warnings"):
                                for warning in report["warnings"]:
                                    st.warning(warning)

                            st.session_state["wizard_definitions"] = definitions
                            st.session_state["wizard_step"] = 3
                            st.rerun()
                else:
                    st.warning("Bitte wählen Sie mindestens die Job Family Spalte aus.")

    st.markdown("---")

    # Zurück-Button
    if st.button("Zurück"):
        st.session_state["wizard_step"] = 1
        st.rerun()


def render_auto_detection():
    """Rendert UI fuer KI-basierte Auto-Erkennung."""

    st.markdown("## 🤖 Automatische Job Family Erkennung")
    st.markdown("Analysiere Ihre Job-Titel und schlage automatisch Gruppierungen vor:")

    st.markdown("---")

    # Lade Daten
    try:
        from dataloader.loader import load_and_prepare_data
        snapshot_df, _, _, _ = load_and_prepare_data()
        has_data = True
    except Exception as e:
        snapshot_df = None
        has_data = False

    if not has_data or snapshot_df is None:
        st.error("Keine Daten verfügbar. Bitte laden Sie zuerst Daten hoch.")
        if st.button("Zurück"):
            st.session_state["wizard_step"] = 1
            st.rerun()
        return

    # Konfiguration
    with st.expander("⚙️ Erweiterte Einstellungen", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            min_cluster_size = st.slider(
                "Minimale Cluster-Größe",
                min_value=2, max_value=10, value=3,
                help="Mindestanzahl Jobs pro Family"
            )
            max_families = st.slider(
                "Maximale Anzahl Families",
                min_value=5, max_value=30, value=15,
                help="Obergrenze für erkannte Families"
            )
        with col2:
            distance_threshold = st.slider(
                "Ähnlichkeitsschwelle",
                min_value=0.5, max_value=2.0, value=1.2, step=0.1,
                help="Höhere Werte = mehr Cluster"
            )

    # Session State für Analyse-Ergebnisse
    if "auto_analysis_results" not in st.session_state:
        st.session_state["auto_analysis_results"] = None

    # Analyse starten
    st.markdown("### Analyse starten")

    unique_titles = snapshot_df["Planstelle"].dropna().nunique()
    total_jobs = len(snapshot_df["Planstelle"].dropna())

    col1, col2 = st.columns(2)
    col1.metric("Einzigartige Job-Titel", unique_titles)
    col2.metric("Gesamtanzahl Jobs", total_jobs)

    if st.button("🔍 Analyse starten", type="primary", use_container_width=True):
        with st.spinner("Analysiere Job-Titel..."):
            config = ClusteringConfig(
                min_cluster_size=min_cluster_size,
                max_families=max_families,
                distance_threshold=distance_threshold
            )
            results = analyze_job_titles(snapshot_df, "Planstelle", config)

            # Ähnliche Families zusammenführen
            if results.get("suggested_families"):
                results["suggested_families"] = merge_similar_families(
                    results["suggested_families"],
                    similarity_threshold=0.7
                )

            st.session_state["auto_analysis_results"] = results
            st.rerun()

    # Ergebnisse anzeigen
    results = st.session_state.get("auto_analysis_results")

    if results:
        if results.get("error"):
            st.error(f"Fehler bei der Analyse: {results['error']}")
        else:
            st.markdown("---")
            st.markdown("### Analyseergebnisse")

            # Statistiken
            stats = results.get("statistics", {})
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Erkannte Families", stats.get("family_count", 0))
            col2.metric("Geclusterte Jobs", stats.get("clustered", 0))
            col3.metric("Nicht zugeordnet", stats.get("unclustered_count", 0))
            col4.metric("Clustering-Rate", f"{stats.get('clustering_rate', 0):.0%}")

            st.markdown("---")

            # Vorgeschlagene Families
            st.markdown("### Vorgeschlagene Job Families")
            st.markdown("Bearbeiten Sie die Vorschläge nach Bedarf:")

            families = results.get("suggested_families", [])

            # Session State für bearbeitete Families
            if "edited_families" not in st.session_state:
                st.session_state["edited_families"] = {}

            for i, family in enumerate(families):
                # Eindeutiger Key für diese Family
                family_key = f"family_{i}"

                # Konfidenz-Farbe
                confidence = family.get("confidence", 0)
                if confidence >= 0.8:
                    conf_color = "#10b981"  # Grün
                    conf_label = "Hoch"
                elif confidence >= 0.6:
                    conf_color = "#f59e0b"  # Orange
                    conf_label = "Mittel"
                else:
                    conf_color = "#ef4444"  # Rot
                    conf_label = "Niedrig"

                with st.expander(
                    f"**{family['name']}** - {family['job_count']} Jobs "
                    f"({conf_label}: {confidence:.0%})",
                    expanded=i < 3
                ):
                    col1, col2 = st.columns([2, 1])

                    with col1:
                        # Editierbarer Name
                        edited_name = st.text_input(
                            "Family-Name",
                            value=st.session_state.get(f"{family_key}_name", family["name"]),
                            key=f"name_{family_key}"
                        )
                        st.session_state[f"{family_key}_name"] = edited_name

                        # Patterns
                        st.markdown("**Vorgeschlagene Patterns:**")
                        patterns = family.get("suggested_patterns", [])
                        edited_patterns = st.text_area(
                            "Patterns (ein Pattern pro Zeile)",
                            value=st.session_state.get(
                                f"{family_key}_patterns",
                                "\n".join(patterns)
                            ),
                            key=f"patterns_{family_key}",
                            height=100
                        )
                        st.session_state[f"{family_key}_patterns"] = edited_patterns

                    with col2:
                        # Konfidenz-Anzeige
                        st.markdown(
                            f"<div style='text-align:center;padding:10px;'>"
                            f"<div style='font-size:2em;color:{conf_color};'>{confidence:.0%}</div>"
                            f"<div style='color:{conf_color};'>Konfidenz</div>"
                            f"</div>",
                            unsafe_allow_html=True
                        )

                        # Checkbox zum Übernehmen
                        include = st.checkbox(
                            "Übernehmen",
                            value=st.session_state.get(f"{family_key}_include", True),
                            key=f"include_{family_key}"
                        )
                        st.session_state[f"{family_key}_include"] = include

                    # Job-Beispiele
                    with st.container():
                        st.markdown("**Beispiel-Jobs:**")
                        jobs_preview = family.get("jobs", [])[:5]
                        for job in jobs_preview:
                            st.markdown(f"- {job}")
                        if len(family.get("jobs", [])) > 5:
                            st.caption(f"... und {len(family['jobs']) - 5} weitere")

            # Nicht zugeordnete Jobs
            unclustered = results.get("unclustered", [])
            if unclustered:
                with st.expander(f"⚠️ Nicht zugeordnete Jobs ({len(unclustered)})", expanded=False):
                    st.markdown(
                        "Diese Job-Titel konnten keinem Cluster zugeordnet werden. "
                        "Sie können später manuell zugewiesen werden."
                    )
                    for job in unclustered[:20]:
                        st.markdown(f"- {job}")
                    if len(unclustered) > 20:
                        st.caption(f"... und {len(unclustered) - 20} weitere")

            st.markdown("---")

            # Definitionen übernehmen
            if st.button("✅ Vorschläge übernehmen", type="primary", use_container_width=True):
                # Sammle bearbeitete Families
                selected_families = []

                for i, family in enumerate(families):
                    family_key = f"family_{i}"

                    if st.session_state.get(f"{family_key}_include", True):
                        edited_family = family.copy()
                        edited_family["name"] = st.session_state.get(
                            f"{family_key}_name", family["name"]
                        )
                        patterns_text = st.session_state.get(
                            f"{family_key}_patterns", "\n".join(family.get("suggested_patterns", []))
                        )
                        edited_family["suggested_patterns"] = [
                            p.strip() for p in patterns_text.split("\n") if p.strip()
                        ]
                        selected_families.append(edited_family)

                # Konvertiere zu v2 Definitionen
                definitions = convert_to_definitions(
                    selected_families,
                    include_unclustered=False,
                    unclustered=unclustered
                )

                st.session_state["wizard_definitions"] = definitions
                st.session_state["wizard_step"] = 3

                # Bereinige Session State
                st.session_state["auto_analysis_results"] = None
                st.session_state["edited_families"] = {}

                st.rerun()

    st.markdown("---")

    # Zurück-Button
    if st.button("Zurück"):
        st.session_state["auto_analysis_results"] = None
        st.session_state["wizard_step"] = 1
        st.rerun()


def render_custom_definition():
    """Rendert UI fuer eigene Definition."""

    st.markdown("## Eigene Job Families definieren")
    st.markdown("Erstellen Sie Ihre eigenen Job Family Definitionen:")

    definitions = st.session_state.get("wizard_definitions", {"jobfamilies": {}, "metadata": {"version": "2.0"}})

    st.markdown("---")

    # Bestehende Families anzeigen
    if definitions.get("jobfamilies"):
        st.markdown("### Definierte Job Families")

        for family_name, family_def in list(definitions["jobfamilies"].items()):
            with st.expander(f"**{family_name}** - {len(family_def.get('patterns', []))} Patterns"):
                # Patterns anzeigen
                st.markdown("**Patterns:**")
                for pattern in family_def.get('patterns', []):
                    st.code(pattern)

                # Löschen-Button
                if st.button(f"'{family_name}' löschen", key=f"del_{family_name}"):
                    del definitions["jobfamilies"][family_name]
                    st.session_state["wizard_definitions"] = definitions
                    st.rerun()

    st.markdown("---")

    # Neue Family hinzufügen
    st.markdown("### Neue Job Family hinzufügen")

    with st.form("new_family_form"):
        new_name = st.text_input("Name der Job Family", placeholder="z.B. IT & Digitalisierung")
        new_description = st.text_input("Beschreibung", placeholder="z.B. IT-Administration, Entwicklung")
        new_patterns = st.text_area(
            "Patterns (ein Pattern pro Zeile)",
            placeholder="*IT-*\n*Entwickler*\n*Administrator*",
            help="Verwenden Sie * als Wildcard. Beispiel: *Berater* matcht alle Titel mit 'Berater'."
        )

        submitted = st.form_submit_button("Job Family hinzufügen", type="primary")

        if submitted and new_name:
            patterns = [p.strip() for p in new_patterns.split("\n") if p.strip()]
            definitions["jobfamilies"][new_name] = {
                "description": new_description,
                "patterns": patterns,
                "min_qualification": "",
                "manual_assignments": []
            }
            st.session_state["wizard_definitions"] = definitions
            st.success(f"'{new_name}' hinzugefügt!")
            st.rerun()

    st.markdown("---")

    # Navigation
    col1, col2 = st.columns(2)

    with col1:
        if st.button("Zurück"):
            st.session_state["wizard_step"] = 1
            st.rerun()

    with col2:
        if definitions.get("jobfamilies"):
            if st.button("Weiter zur Überprüfung", type="primary", use_container_width=True):
                st.session_state["wizard_step"] = 3
                st.rerun()
        else:
            st.info("Fügen Sie mindestens eine Job Family hinzu.")


def calculate_preview_stats(df: Optional[pd.DataFrame], definitions: Dict) -> Dict:
    """
    Berechnet Vorschau-Statistiken fuer die Definitionen.

    Args:
        df: DataFrame mit Job-Daten
        definitions: Job Family Definitionen

    Returns:
        {
            "family_count": 10,
            "total_patterns": 45,
            "total_jobs": 500,
            "unique_titles": 120,
            "mapped": 450,
            "unmapped": 50,
            "mapping_rate": 0.9,
            "by_family": {"IT": 45, ...},
            "unmapped_jobs": ["Job1", "Job2", ...]
        }
    """
    import fnmatch

    stats = {
        "family_count": len(definitions.get("jobfamilies", {})),
        "total_patterns": sum(len(f.get("patterns", [])) for f in definitions.get("jobfamilies", {}).values()),
        "total_jobs": 0,
        "unique_titles": 0,
        "mapped": 0,
        "unmapped": 0,
        "mapping_rate": 0.0,
        "by_family": {},
        "unmapped_jobs": []
    }

    if df is None or "Planstelle" not in df.columns:
        return stats

    # Unique Job-Titel
    job_titles = df["Planstelle"].dropna().unique()
    stats["total_jobs"] = len(df["Planstelle"].dropna())
    stats["unique_titles"] = len(job_titles)

    # Berechne Matching
    matched_titles = set()
    family_counts = {family: 0 for family in definitions.get("jobfamilies", {}).keys()}

    for job_title in job_titles:
        job_matched = False

        # Pruefe Manual Overrides
        if job_title in definitions.get("manual_overrides", {}):
            matched_titles.add(job_title)
            family_name = definitions["manual_overrides"][job_title]
            if family_name in family_counts:
                family_counts[family_name] += 1
            continue

        # Pruefe Manual Assignments und Patterns
        for family_name, family_def in definitions.get("jobfamilies", {}).items():
            # Manual Assignments
            if job_title in family_def.get("manual_assignments", []):
                matched_titles.add(job_title)
                family_counts[family_name] += 1
                job_matched = True
                break

            # Patterns
            for pattern in family_def.get("patterns", []):
                if fnmatch.fnmatch(str(job_title).lower(), pattern.lower()):
                    matched_titles.add(job_title)
                    family_counts[family_name] += 1
                    job_matched = True
                    break

            if job_matched:
                break

    # Statistiken
    stats["mapped"] = len(matched_titles)
    stats["unmapped"] = len(job_titles) - len(matched_titles)
    stats["mapping_rate"] = len(matched_titles) / len(job_titles) if len(job_titles) > 0 else 0.0
    stats["by_family"] = {k: v for k, v in family_counts.items() if v > 0}
    stats["unmapped_jobs"] = [t for t in job_titles if t not in matched_titles][:50]

    return stats


def render_review_step(df: Optional[pd.DataFrame] = None):
    """Rendert den Review-Schritt mit vollstaendiger Bearbeitungsmoeglichkeit."""

    st.markdown("## Zusammenfassung prüfen")
    st.markdown("Überprüfen Sie Ihre Job Family Definitionen vor dem Speichern:")

    definitions = st.session_state.get("wizard_definitions", {})

    if not definitions or not definitions.get("jobfamilies"):
        st.warning("Keine Definitionen vorhanden.")
        if st.button("Zurück"):
            st.session_state["wizard_step"] = 2
            st.rerun()
        return

    # Lade Daten fuer Statistiken
    try:
        from dataloader.loader import load_and_prepare_data
        snapshot_df, _, _, _ = load_and_prepare_data()
    except Exception:
        snapshot_df = None

    # Berechne Statistiken
    stats = calculate_preview_stats(snapshot_df, definitions)

    st.markdown("---")

    # Statistiken mit Mapping-Quote
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Job Families", stats["family_count"])
    col2.metric("Patterns gesamt", stats["total_patterns"])

    if snapshot_df is not None:
        col3.metric("Zugeordnete Jobs", stats["mapped"])
        col4.metric(
            "Mapping-Quote",
            f"{stats['mapping_rate']:.0%}",
            delta=f"{stats['unmapped']} nicht zugeordnet" if stats["unmapped"] > 0 else None,
            delta_color="off"
        )

        # Warnung bei niedriger Quote
        if stats["mapping_rate"] < 0.7:
            st.warning(
                f"⚠️ Nur **{stats['mapping_rate']:.0%}** der Jobs sind zugeordnet. "
                f"Erwägen Sie, weitere Patterns hinzuzufügen oder die Auto-Erkennung zu nutzen."
            )
        elif stats["mapping_rate"] >= 0.9:
            st.success(f"✅ Ausgezeichnete Abdeckung: {stats['mapping_rate']:.0%} der Jobs sind zugeordnet.")
    else:
        col3.metric("Durchschnitt Patterns/Family", f"{stats['total_patterns']/max(stats['family_count'],1):.1f}")

    st.markdown("---")

    # Verteilung nach Family (wenn Daten vorhanden)
    if snapshot_df is not None and stats["by_family"]:
        with st.expander("📊 Verteilung nach Job Family", expanded=False):
            for family_name, count in sorted(stats["by_family"].items(), key=lambda x: -x[1]):
                color = definitions["jobfamilies"].get(family_name, {}).get("color", "#808080")
                pct = count / stats["unique_titles"] * 100 if stats["unique_titles"] > 0 else 0
                st.markdown(
                    f"<div style='display:flex;align-items:center;margin:5px 0;'>"
                    f"<span style='width:200px;'><span style='color:{color};'>●</span> {family_name}</span>"
                    f"<div style='flex:1;background:#e5e7eb;border-radius:4px;height:20px;margin:0 10px;'>"
                    f"<div style='width:{pct}%;background:{color};height:100%;border-radius:4px;'></div></div>"
                    f"<span style='width:80px;text-align:right;'>{count} ({pct:.0f}%)</span></div>",
                    unsafe_allow_html=True
                )

    st.markdown("---")

    # Family-Übersicht mit vollständiger Bearbeitung
    st.markdown("### Definierte Job Families")

    # Kopiere Definitionen für Bearbeitung
    families_list = list(definitions.get("jobfamilies", {}).items())

    for family_name, family_def in families_list:
        # Zähle Jobs für diese Family
        family_job_count = stats["by_family"].get(family_name, 0) if snapshot_df is not None else 0

        with st.expander(
            f"**{family_name}** - {len(family_def.get('patterns', []))} Patterns"
            + (f" ({family_job_count} Jobs)" if snapshot_df is not None else ""),
            expanded=False
        ):
            col1, col2 = st.columns([3, 1])

            with col1:
                # Editierbarer Name
                new_name = st.text_input(
                    "Family-Name",
                    value=family_name,
                    key=f"review_name_{family_name}"
                )

                # Editierbare Beschreibung
                new_desc = st.text_area(
                    "Beschreibung",
                    value=family_def.get("description", ""),
                    key=f"review_desc_{family_name}",
                    height=68
                )

            with col2:
                # Farbe (Color Picker)
                current_color = family_def.get("color", "#808080")
                new_color = st.color_picker(
                    "Farbe",
                    value=current_color,
                    key=f"review_color_{family_name}"
                )

                # Min. Qualifikation
                new_qual = st.text_input(
                    "Min. Qualifikation",
                    value=family_def.get("min_qualification", ""),
                    key=f"review_qual_{family_name}"
                )

            # Änderungen speichern wenn Name/Beschreibung/Farbe geändert
            if (new_name != family_name or
                new_desc != family_def.get("description", "") or
                new_color != current_color or
                new_qual != family_def.get("min_qualification", "")):

                if st.button("💾 Änderungen speichern", key=f"save_changes_{family_name}"):
                    # Update Family
                    if new_name != family_name:
                        # Rename: alte löschen, neue erstellen
                        del definitions["jobfamilies"][family_name]
                        definitions["jobfamilies"][new_name] = family_def

                    target_name = new_name if new_name != family_name else family_name
                    definitions["jobfamilies"][target_name]["description"] = new_desc
                    definitions["jobfamilies"][target_name]["color"] = new_color
                    definitions["jobfamilies"][target_name]["min_qualification"] = new_qual

                    st.session_state["wizard_definitions"] = definitions
                    st.rerun()

            st.markdown("---")

            # Patterns mit Lösch-Option
            st.markdown("**Patterns:**")
            patterns = family_def.get("patterns", [])

            if patterns:
                for i, pattern in enumerate(patterns):
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.code(pattern, language=None)
                    with col2:
                        if st.button("🗑️", key=f"del_pattern_{family_name}_{i}", help="Pattern löschen"):
                            family_def["patterns"].remove(pattern)
                            st.session_state["wizard_definitions"] = definitions
                            st.rerun()
            else:
                st.info("Keine Patterns definiert")

            # Neues Pattern hinzufügen
            col1, col2 = st.columns([4, 1])
            with col1:
                new_pattern = st.text_input(
                    "Neues Pattern",
                    key=f"new_p_{family_name}",
                    placeholder="z.B. *Sachbearbeiter*",
                    label_visibility="collapsed"
                )
            with col2:
                if st.button("➕", key=f"add_p_{family_name}", help="Pattern hinzufügen"):
                    if new_pattern and new_pattern not in family_def.get("patterns", []):
                        family_def["patterns"].append(new_pattern)
                        st.session_state["wizard_definitions"] = definitions
                        st.rerun()

            st.markdown("---")

            # Family löschen
            if st.button("❌ Diese Family löschen", key=f"del_f_{family_name}", type="secondary"):
                del definitions["jobfamilies"][family_name]
                st.session_state["wizard_definitions"] = definitions
                st.rerun()

    st.markdown("---")

    # Neue Family hinzufügen
    with st.expander("➕ Neue Job Family hinzufügen", expanded=False):
        with st.form("quick_add_family"):
            col1, col2 = st.columns(2)
            with col1:
                qf_name = st.text_input("Name *", placeholder="z.B. IT & Digitalisierung")
                qf_patterns = st.text_area(
                    "Patterns (ein Pattern pro Zeile)",
                    placeholder="*IT-*\n*Administrator*\n*Entwickler*",
                    height=100
                )
            with col2:
                qf_desc = st.text_input("Beschreibung", placeholder="z.B. IT-Administration, Entwicklung")
                qf_color = st.color_picker("Farbe", value="#808080")
                qf_qual = st.text_input("Min. Qualifikation", placeholder="z.B. Fachinformatiker/-in")

            if st.form_submit_button("✅ Family hinzufügen", type="primary"):
                if qf_name and qf_name not in definitions.get("jobfamilies", {}):
                    patterns = [p.strip() for p in qf_patterns.split("\n") if p.strip()]
                    definitions["jobfamilies"][qf_name] = {
                        "description": qf_desc,
                        "patterns": patterns,
                        "min_qualification": qf_qual,
                        "color": qf_color,
                        "manual_assignments": []
                    }
                    st.session_state["wizard_definitions"] = definitions
                    st.success(f"'{qf_name}' hinzugefügt!")
                    st.rerun()
                elif qf_name in definitions.get("jobfamilies", {}):
                    st.error(f"Eine Family mit dem Namen '{qf_name}' existiert bereits.")
                else:
                    st.error("Bitte geben Sie einen Namen ein.")

    # Nicht zugeordnete Jobs anzeigen
    if snapshot_df is not None and stats["unmapped"] > 0:
        st.markdown("---")
        st.markdown("### ⚠️ Nicht zugeordnete Jobs")

        st.info(
            f"**{stats['unmapped']} Job-Titel** ({100 - stats['mapping_rate']*100:.0f}%) konnten nicht zugeordnet werden. "
            "Sie können diese später über die **Matrix-UI** manuell zuweisen."
        )

        with st.expander(f"Nicht zugeordnete Jobs anzeigen ({min(50, stats['unmapped'])} von {stats['unmapped']})", expanded=False):
            # Zeige als Liste
            unmapped_df = pd.DataFrame({"Job-Titel": stats["unmapped_jobs"]})
            st.dataframe(unmapped_df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # Export-Option
    with st.expander("📥 Definitionen exportieren", expanded=False):
        st.markdown("Speichern Sie Ihre Definitionen zur Sicherung oder Weitergabe:")

        col1, col2, col3 = st.columns(3)

        with col1:
            json_export = export_to_json(definitions)
            st.download_button(
                "📄 JSON herunterladen",
                data=json_export,
                file_name="jobfamilies_export.json",
                mime="application/json",
                use_container_width=True
            )

        with col2:
            if OPENPYXL_AVAILABLE:
                excel_export = export_to_excel(definitions)
                st.download_button(
                    "📊 Excel herunterladen",
                    data=excel_export,
                    file_name="jobfamilies_export.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            else:
                st.button("📊 Excel (nicht verfügbar)", disabled=True, use_container_width=True)

        with col3:
            if snapshot_df is not None and OPENPYXL_AVAILABLE:
                # Mapping Report erstellen (erfordert assign_jobfamilies)
                try:
                    temp_df = snapshot_df.copy()
                    temp_df = assign_jobfamilies(temp_df, definitions, "Planstelle")
                    mapping_report = export_mapping_report(temp_df, definitions)
                    st.download_button(
                        "📋 Mapping-Report",
                        data=mapping_report,
                        file_name="mapping_report.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                except Exception:
                    st.button("📋 Mapping-Report (Fehler)", disabled=True, use_container_width=True)
            else:
                st.button("📋 Mapping-Report", disabled=True, use_container_width=True,
                         help="Benötigt Daten und openpyxl")

    st.markdown("---")

    # Navigation
    col1, col2 = st.columns(2)

    with col1:
        if st.button("⬅️ Zurück", use_container_width=True):
            st.session_state["wizard_step"] = 2
            st.rerun()

    with col2:
        if st.button("✅ Speichern & Abschließen", type="primary", use_container_width=True):
            # Metadata aktualisieren
            definitions["metadata"] = definitions.get("metadata", {})
            definitions["metadata"]["version"] = "2.0"
            definitions["metadata"]["last_modified"] = datetime.now().isoformat()

            if "manual_overrides" not in definitions:
                definitions["manual_overrides"] = {}

            # Speichern
            save_jobfamily_definitions(definitions)
            st.session_state["wizard_step"] = 4
            st.rerun()


def render_finish_step():
    """Rendert den Abschluss-Schritt."""

    st.balloons()

    st.markdown("## Setup abgeschlossen!")

    definitions = st.session_state.get("wizard_definitions", {})
    family_count = len(definitions.get("jobfamilies", {}))

    st.success(f"**{family_count} Job Families** wurden erfolgreich konfiguriert!")

    st.markdown("---")

    st.markdown("""
    ### Ihre Job Families sind eingerichtet:

    - Die Definitionen wurden gespeichert
    - Sie können jederzeit Änderungen über die **Jobfamilies-Seite** vornehmen
    - Nutzen Sie die **Matrix-UI** für Feinabstimmungen einzelner Zuordnungen

    ### Nächste Schritte

    1. **Dashboard erkunden** - Sehen Sie Ihre Daten nach Job Families aufgeschlüsselt
    2. **Filter nutzen** - Job Families als Filter in der Sidebar verwenden
    3. **Anpassen** - Matrix-UI für manuelle Zuordnungen nutzen
    """)

    st.markdown("---")

    if st.button("Zum Dashboard", type="primary", use_container_width=True):
        # Wizard abschließen
        st.session_state["skip_wizard"] = True
        st.session_state["jobfamily_definitions"] = definitions
        reset_wizard()
        st.rerun()


# =============================================================================
# HAUPT-WIZARD-FUNKTION
# =============================================================================

def render_setup_wizard(df: Optional[pd.DataFrame] = None) -> bool:
    """
    Rendert den Setup-Wizard.

    Args:
        df: Optional DataFrame mit Job-Daten fuer Vorschau/Statistiken

    Returns:
        True wenn Setup abgeschlossen, False wenn noch in Bearbeitung
    """

    # Session State initialisieren
    init_wizard_state()

    # Wizard-Header
    st.markdown("""
    <style>
    .wizard-header {
        background: linear-gradient(135deg, #0088DE 0%, #14b8a6 100%);
        padding: 1.5rem;
        border-radius: 10px;
        margin-bottom: 2rem;
    }
    .wizard-header h1 {
        color: white;
        margin: 0;
    }
    .wizard-header p {
        color: rgba(255,255,255,0.9);
        margin: 0.5rem 0 0 0;
    }
    </style>
    <div class="wizard-header">
        <h1>🚀 Job Families Setup</h1>
        <p>Konfigurieren Sie Ihre Job Families in wenigen Schritten</p>
    </div>
    """, unsafe_allow_html=True)

    # Progress
    current_step = st.session_state.get("wizard_step", 0)
    progress = current_step / (len(WIZARD_STEPS) - 1)

    # Progress-Bar
    st.progress(progress)

    # Step-Indicator
    cols = st.columns(len(WIZARD_STEPS))
    for i, step_name in enumerate(WIZARD_STEPS):
        with cols[i]:
            if i < current_step:
                st.markdown(f"✅ ~~{step_name}~~")
            elif i == current_step:
                st.markdown(f"**➤ {step_name}**")
            else:
                st.markdown(f"○ {step_name}")

    st.markdown("---")

    # Step-Content rendern
    if current_step == 0:
        render_welcome_step()
    elif current_step == 1:
        render_source_selection_step()
    elif current_step == 2:
        render_definition_step()
    elif current_step == 3:
        render_review_step(df)
    elif current_step == 4:
        render_finish_step()
        return True

    return False


def needs_setup() -> bool:
    """
    Prueft ob Setup-Wizard benoetigt wird.

    Returns:
        True wenn keine Definitionen vorhanden und Wizard nicht uebersprungen
    """
    if st.session_state.get("skip_wizard", False):
        return False

    try:
        definitions = load_jobfamily_definitions()

        # Prüfe ob Definitionen vorhanden und nicht leer
        if not definitions:
            return True

        jobfamilies = definitions.get("jobfamilies", {})

        # Leere Definitionen = Setup benötigt
        if not jobfamilies or len(jobfamilies) == 0:
            return True

        return False

    except Exception:
        return True
