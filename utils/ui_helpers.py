"""
UI Helper Functions für HR Pulse Dashboard.

Wiederverwendbare Funktionen für Tooltips, Hilfe-Texte und UI-Elemente.
"""

import streamlit as st
from typing import Optional


def metric_info(label: str, description: str, icon: str = "ℹ️"):
    """
    Rendert einen Info-Text mit Icon für Metrik-Erläuterungen.

    Args:
        label: Überschrift der Metrik
        description: Erläuterungstext
        icon: Emoji-Icon (default: ℹ️)
    """
    st.markdown(
        f"""
        <div style='
            background: #F5F5F5;
            border-left: 3px solid #0088DE;
            padding: 0.75rem 1rem;
            margin: 0.5rem 0 1rem 0;
            border-radius: 0 4px 4px 0;
        '>
            <p style='margin: 0; color: #A9A9A9; font-size: 0.9rem;'>
                <span style='font-size: 1rem;'>{icon}</span>
                <strong style='color: #757575;'>{label}:</strong> {description}
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )


def section_header(title: str, description: Optional[str] = None, icon: Optional[str] = None):
    """
    Rendert eine konsistente Section-Überschrift mit optionaler Beschreibung.

    Args:
        title: Titel der Section
        description: Optionale Beschreibung
        icon: Optionales Icon (Emoji)
    """
    icon_html = f"{icon} " if icon else ""
    desc_html = f"<p style='color: #A9A9A9; font-size: 0.95rem; margin-top: 0.5rem;'>{description}</p>" if description else ""

    st.markdown(
        f"""
        <div style='margin: 1.5rem 0 1rem 0;'>
            <h3 style='color: #757575; margin-bottom: 0;'>{icon_html}{title}</h3>
            {desc_html}
        </div>
        """,
        unsafe_allow_html=True
    )


def help_tooltip(text: str):
    """
    Rendert einen kleinen Hilfe-Tooltip.

    Args:
        text: Hilfetext
    """
    st.markdown(
        f"""
        <span style='
            color: #0088DE;
            cursor: help;
            font-size: 0.9rem;
            margin-left: 0.25rem;
        ' title='{text}'>ⓘ</span>
        """,
        unsafe_allow_html=True
    )


def explanation_box(title: str, content: str, box_type: str = "info"):
    """
    Rendert eine Erläuterungs-Box.

    Args:
        title: Titel der Box
        content: Inhalt
        box_type: "info", "warning", "success" (default: "info")
    """
    colors = {
        "info": {"border": "#0088DE", "bg": "rgba(0, 136, 222, 0.1)"},
        "warning": {"border": "#f59e0b", "bg": "rgba(245, 158, 11, 0.1)"},
        "success": {"border": "#10b981", "bg": "rgba(16, 185, 129, 0.1)"},
    }

    color = colors.get(box_type, colors["info"])

    st.markdown(
        f"""
        <div style='
            background: {color["bg"]};
            border: 1px solid {color["border"]};
            border-radius: 6px;
            padding: 1rem;
            margin: 1rem 0;
        '>
            <h4 style='color: #f1f5f9; margin: 0 0 0.5rem 0; font-size: 1rem;'>{title}</h4>
            <p style='color: #94a3b8; margin: 0; font-size: 0.9rem;'>{content}</p>
        </div>
        """,
        unsafe_allow_html=True
    )


# Metrik-Erläuterungen (zentrale Definitionen)
METRIC_EXPLANATIONS = {
    # Kapazität
    "mak": "Mitarbeiterkapazität (MAK) misst die Gesamtkapazität in Vollzeitäquivalenten (FTE). 1 MAK = 1 Vollzeitkraft.",
    "headcount": "Anzahl der beschäftigten Personen unabhängig vom Beschäftigungsumfang. Teilzeit- und Vollzeitkräfte werden gleich gezählt.",
    "fte": "Full-Time Equivalent - Die Summe aller Beschäftigungsgrade umgerechnet auf Vollzeitstellen. Z.B. 2 × 50% = 1.0 FTE.",

    # Besetzung
    "besetzungsgrad": "Anteil der besetzten Planstellen an allen Planstellen. Ein hoher Wert (>85%) zeigt gute Personalverfügbarkeit.",
    "vakanzen": "Anzahl unbesetzter Planstellen. Hohe Vakanzen können auf Rekrutierungsprobleme oder strukturelle Engpässe hinweisen.",
    "vakanzquote": "Prozentualer Anteil der Vakanzen an allen Planstellen. Zeigt die relative Besetzungslücke.",

    # Kosten
    "gesamtkosten": "Summe aller Personalkosten inkl. Sozialabgaben und Arbeitgeberkosten. Basis für Budgetplanung.",
    "kosten_pro_fte": "Durchschnittliche Jahreskosten pro Vollzeitäquivalent. Vergleichskennzahl für Personaleffizienz.",
    "kosten_pro_kopf": "Durchschnittliche Jahreskosten pro beschäftigter Person. Berücksichtigt auch Teilzeitkräfte.",

    # Demografie
    "durchschnittsalter": "Arithmetisches Mittel des Alters aller Beschäftigten. Indikator für Überalterung oder Verjüngung.",
    "altersstruktur": "Verteilung der Belegschaft über Alterskohorten. Zeigt demografische Risiken und Nachfolgebedarfe.",
    "55plus_anteil": "Anteil der Beschäftigten ab 55 Jahren. Wichtig für Ruhestandsplanung und Wissenstransfer.",

    # Arbeitszeit
    "vollzeitquote": "Anteil der Vollzeitbeschäftigten (≥95% FTE) an allen Beschäftigten.",
    "teilzeitquote": "Anteil der Teilzeitbeschäftigten (<95% FTE) an allen Beschäftigten. Zeigt Flexibilisierungsgrad.",
    "durchschnittlicher_beschaeftigungsgrad": "Durchschnittlicher FTE-Wert pro Kopf. Liegt typisch bei 0.75-0.95 je nach Teilzeitanteil.",

    # Geschlecht
    "geschlechterverteilung": "Verhältnis männlich/weiblich/divers in der Belegschaft. Wichtig für Diversity-Monitoring.",
    "gender_pay_gap": "Durchschnittliche Entgeltdifferenz zwischen Geschlechtern. Kennzahl für Gleichstellung.",

    # ATZ
    "atz_quote": "Anteil der Beschäftigten in Altersteilzeit an der Gesamtbelegschaft. Hohe Werte (>5%) belasten Kapazität.",
    "atz_arbeitsphase": "Anzahl ATZ-Beschäftigte in der Arbeitsphase (aktiv arbeitend, volle Vergütung).",
    "atz_freistellung": "Anzahl ATZ-Beschäftigte in der Freistellungsphase (nicht mehr arbeitend, halbe Vergütung).",
    "atz_berechtigt": "Anzahl Beschäftigte, die die Voraussetzungen für Altersteilzeit erfüllen (typisch ab 55 Jahre).",

    # Fluktuation
    "fluktuationsrate": "Abgangsquote pro Jahr. Zeigt Mitarbeiterbindung. Werte >10% können problematisch sein.",
    "austritte": "Anzahl der Beschäftigten, die das Unternehmen verlassen haben (inkl. Rente, Kündigung).",
    "eintritte": "Anzahl der Neueinstellungen im Betrachtungszeitraum.",

    # Qualifikation
    "qualifikationsstruktur": "Verteilung nach Bildungsabschlüssen. Zeigt Akademisierungsgrad und Fachkräfteausstattung.",
    "qualifikationsluecke": "Differenz zwischen erforderlicher und vorhandener Qualifikation für eine Stelle.",

    # Jobfamilies
    "jobfamily": "Gruppierung ähnlicher Stellen nach Tätigkeitsfeld (z.B. Kundenberatung, IT, Compliance).",
    "unmapped": "Planstellen ohne Zuordnung zu einer Jobfamily. Sollten regelmäßig geprüft und zugeordnet werden.",

    # Simulation
    "prognose_horizont": "Zeitraum der Vorausberechnung. Längere Horizonte (>3 Jahre) haben höhere Unsicherheit.",
    "monte_carlo": "Statistische Simulation mit Zufallsvariationen zur Modellierung von Unsicherheiten.",
    "konfidenzintervall": "Bereich, in dem der wahre Wert mit hoher Wahrscheinlichkeit liegt (hier: 10.-90. Perzentil = 80% Sicherheit).",
    "szenario": "Was-wäre-wenn-Analyse mit alternativen Annahmen (z.B. höhere Fluktuation, mehr Neueinstellungen).",
}


def get_metric_explanation(metric_key: str) -> str:
    """
    Gibt die Erläuterung für eine Metrik zurück.

    Args:
        metric_key: Schlüssel der Metrik

    Returns:
        Erläuterungstext
    """
    return METRIC_EXPLANATIONS.get(metric_key, "")
