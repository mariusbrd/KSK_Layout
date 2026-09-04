"""Human-readable transformation lineage for dashboard Excel exports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from utils.lineage.registry import LineageSpec, get_lineage_specs


TRANSFORMATION_LINEAGE_SHEET_NAME = "Transformations_Lineage"


@dataclass(frozen=True)
class TransformationStep:
    """Reusable explanation of one calculation/transformation step."""

    step_id: str
    title: str
    plain_language: str
    input_fields: tuple[str, ...]
    transformation: str
    output_fields: tuple[str, ...]
    code_reference: str
    quality_check: str = ""


def _step(
    step_id: str,
    title: str,
    plain_language: str,
    input_fields: Iterable[str],
    transformation: str,
    output_fields: Iterable[str],
    code_reference: str,
    quality_check: str = "",
) -> TransformationStep:
    return TransformationStep(
        step_id=step_id,
        title=title,
        plain_language=plain_language,
        input_fields=tuple(input_fields),
        transformation=transformation,
        output_fields=tuple(output_fields),
        code_reference=code_reference,
        quality_check=quality_check,
    )


TRANSFORMATION_STEPS: dict[str, TransformationStep] = {
    "input.excel_headers": _step(
        "input.excel_headers",
        "Excel-Eingaben erfassen",
        "Das Dashboard liest die aktiven Excel-Quellen und dokumentiert Datei, Tabellenblatt und Spaltennamen. Dadurch ist sichtbar, aus welchen Rohdaten die Auswertung stammt.",
        ("Mitarbeiter.xlsx", "Planstellen.XLSX", "ATZ.xlsx", "Ausbildung.xlsx", "TVÖD.xlsx", "Clusterdatei"),
        "Nur Metadaten und Kopfzeilen werden fuer den Lineage-Report gelesen; die fachliche Berechnung nutzt die bereits geladene Dashboard-Datenbasis.",
        ("Input_Lineage",),
        "utils/lineage/inputs.py:build_input_lineage_dataframe",
        "Jede gefundene Quelle erhaelt Ermittlungsstatus, Spaltenanzahl und Dateisignatur.",
    ),
    "prep.normalize_keys": _step(
        "prep.normalize_keys",
        "Personalnummern und Datumsfelder vereinheitlichen",
        "Personalnummern werden als einheitliche sechsstellige Texte behandelt. Datumsfelder werden in echte Datumswerte umgewandelt, damit Joins, Stichtagsfilter und Simulationen korrekt funktionieren.",
        ("Mitarbeiter.PersNr", "Planstellen.Personalnummer", "ATZ.PersNr", "Ausbildung.Personalnummer", "GebDatum", "Eintritt", "Austritt"),
        "IDs normalisieren, Austritt 9999-xx-xx als offen behandeln, ATZ- und Ausbildungsdaten auf denselben Personen-Key bringen.",
        ("PersNr", "Personalnummer", "GebDatum", "Eintritt", "Austritt"),
        "dataloader/loader.py:normalize_persnr; dataloader/loader.py:safe_parse_austritt; dataloader/loader.py:normalize_atz",
        "Tests und Datenqualitaetschecks pruefen Dubletten, fehlende Matches und Pflichtspalten.",
    ),
    "prep.combine_snapshot": _step(
        "prep.combine_snapshot",
        "Planstellen mit Mitarbeitenden verbinden",
        "Planstellen bilden die Grundstruktur. Mitarbeiter-, Ausbildungs- und ATZ-Informationen werden ueber die Personalnummer daran angehaengt.",
        ("Planstellen.Personalnummer", "Mitarbeiter.PersNr", "Ausbildung.Personalnummer", "ATZ.PersNr"),
        "Left Join Planstellen -> Mitarbeiter, danach Ausbildung und aktuelle ATZ-Phase ergaenzen.",
        ("snapshot_df", "Is_Vacant", "Ausbildung", "Phase", "ATZ_Status"),
        "dataloader/loader.py:combine_to_snapshot",
        "Planstellen ohne passende Person bleiben als Vakanz erhalten; Personen ohne Planstelle werden in gesonderten Pruefungen sichtbar.",
    ),
    "prep.capacity_cost": _step(
        "prep.capacity_cost",
        "MAK und Kosten berechnen",
        "Aus Beschaeftigungsgrad, Sollarbeitszeit, ATZ-Status, Exklusionslogik und TVÖD-Tabelle entstehen die zentralen Kennzahlen fuer Kapazitaet und Kosten.",
        ("BsGrd", "Sollarbeitszeit", "TrfGr", "St", "Phase", "Status kundenindividuell", "TVÖD.xlsx"),
        "MAK = wirksame Mitarbeiterkapazitaet; SOLL-MAK = Sollarbeitszeit / 39; EUR = Tarifwert mal Kapazitaet und Arbeitgeberfaktor.",
        ("MAK", "MAK_Calculated", "MAK_Reporting", "SOLL_MAK", "EUR_Reporting", "Total_Cost_Year"),
        "dataloader/loader.py:calculate_mak_vectorized; dataloader/loader.py:calculate_cost_vectorized; pages/1_⚡_Kompakt.py:prepare_compact_data",
        "ATZ-Freistellung, ruhende Beschaeftigung und Azubis werden gesondert behandelt und ueber Tests abgesichert.",
    ),
    "prep.apply_filters": _step(
        "prep.apply_filters",
        "Dashboard-Filter anwenden",
        "Vor der Darstellung werden die vom Nutzer gewaehlten Filter angewendet, zum Beispiel Organisationseinheit, Jobgruppe, Cluster, Geschlecht, Alterskohorte oder ATZ-Status.",
        ("snapshot_df", "Sidebar-Filter", "Top-Filter"),
        "Zeilen ausserhalb des aktuellen Filterkontexts werden entfernt; die Berechnung nutzt danach nur noch den sichtbaren Kontext.",
        ("filtered_df",),
        "components/sidebar.py; pages/1_⚡_Kompakt.py:apply_filters",
        "Der Export-Kontext im Lineage_Report dokumentiert die fuer den Download bekannten Einstellungen.",
    ),
    "compact.prepare_reporting": _step(
        "compact.prepare_reporting",
        "Reporting-Sicht vorbereiten",
        "Das Dashboard erzeugt aus dem Snapshot eine einheitliche Auswertungstabelle mit Kopf-, MAK- und EUR-Spalten. Diese Tabelle ist die gemeinsame Grundlage vieler Grafiken.",
        ("snapshot_df", "PersNr", "Organisationseinheit", "Jobfamily", "MAK", "Total_Cost_Year"),
        "Spalten umbenennen, Reporting-Kennzahlen ableiten, Vakanzen und Exklusionssicht konsistent markieren.",
        ("prepared_df", "Headcount", "MAK_Reporting", "EUR_Reporting"),
        "pages/1_⚡_Kompakt.py:prepare_compact_data",
        "Mehrere Seiten verwenden diese Funktion, damit Kompakt-, OE- und Jobgruppenanalyse dieselbe Zahlenbasis haben.",
    ),
    "analysis.group_metric": _step(
        "analysis.group_metric",
        "Kennzahl je Kategorie zusammenfassen",
        "Fuer Balkendiagramme und Ranglisten werden die Daten nach einer Kategorie gruppiert. Danach wird die aktuell gewaehlte Kennzahl addiert oder, bei Koepfen, als eindeutige Personenanzahl gezaehlt.",
        ("filtered_df", "Kategorie", "PersNr", "MAK_Reporting", "EUR_Reporting"),
        "GroupBy je Analyse-Dimension; Koepfe = eindeutige Personalnummern, MAK/EUR = Summe der Kennzahlspalte.",
        ("Kategorie", "IST", "Simulation", "Anteil"),
        "pages/1_⚡_Kompakt.py:create_breakdown_table; pages/9_🏢_Organisationseinheiten_Analyse.py:_build_orgunit_ranking_frame; pages/8_💼_Jobfamily_Analyse.py:_build_jobfamily_ranking_frame",
        "Tests pruefen, dass sichtbare Grafik, sichtbare Tabelle und Excel-Export dieselbe Aggregation verwenden.",
    ),
    "analysis.sort_top": _step(
        "analysis.sort_top",
        "Sortierung, Mindestgroesse und Top-N anwenden",
        "Nach der Aggregation sortiert das Dashboard die Kategorien absteigend nach der gewaehlten Sortierung, entfernt optional zu kleine Einheiten und zeigt die Top-N-Auswahl.",
        ("ranking_df", "Sortierung", "Mindestgroesse", "Top-N"),
        "Mindestgroesse zuerst, danach absteigende Sortierung, danach Top-N-Begrenzung.",
        ("display_categories", "ranking_export_df"),
        "pages/9_🏢_Organisationseinheiten_Analyse.py:_apply_orgunit_top_filters; pages/8_💼_Jobfamily_Analyse.py:_apply_jobfamily_top_filters",
        "Regressionstests pruefen, dass Top-X nicht faelschlich Min-X zeigt.",
    ),
    "analysis.split_composition": _step(
        "analysis.split_composition",
        "Zusammensetzung innerhalb der Kategorie berechnen",
        "Fuer Strukturdiagramme wird innerhalb jeder OE oder Jobgruppe nach Geschlecht, Alter, Beschaeftigungsstatus oder anderen Dimensionen weiter unterteilt.",
        ("mapped_df", "Organisationseinheit", "Jobfamily", "Split-Dimension", "MAK_Reporting", "EUR_Reporting"),
        "Zweistufige Gruppierung nach Hauptkategorie und Split-Dimension; danach Pivot fuer Tabelle und gestapeltes Diagramm.",
        ("chart_df", "pivot_df"),
        "pages/9_🏢_Organisationseinheiten_Analyse.py:_aggregate_org_split; pages/8_💼_Jobfamily_Analyse.py:_aggregate_jobfamily_split",
        "Tests pruefen, dass Pivot-Tabelle und Export dieselben Werte wie das Diagramm enthalten.",
    ),
    "simulation.future_snapshot": _step(
        "simulation.future_snapshot",
        "Zukunftsbild simulieren",
        "Die Simulation startet beim aktuellen Bestand und verarbeitet geplante Abgaenge und Zugaenge bis zum Zielstichtag. Das Ergebnis ist ein Future-Snapshot mit derselben Struktur wie der Ist-Snapshot.",
        ("prepared_df", "Abgangsparameter", "Zugangsparameter", "Ziel-Stichtag", "Clusterquelle"),
        "Abgaenge entfernen oder reduzieren Kapazitaet; Zugaenge ergaenzen Personen/MAK; danach werden Reporting-Spalten neu aufgebaut.",
        ("compact_sim_prepared_df", "compact_sim_metadata", "compact_sim_audit_tables"),
        "dataloader/compact_simulation_engine.py:simulate_compact_snapshot",
        "Audit-Tabellen dokumentieren Ereignisse, betroffene Personen, MAK-Aenderungen und Parameter.",
    ),
    "simulation.compare_status_quo": _step(
        "simulation.compare_status_quo",
        "Simulation mit Ist vergleichen",
        "Wenn der Ist-Vergleich aktiv ist, werden Ist- und Future-Werte je OE oder Jobgruppe nebeneinander gelegt. Daraus entstehen Delta und prozentuale Veraenderung.",
        ("compact_sim_status_quo_df", "compact_sim_prepared_df"),
        "Outer Merge von Ist- und Simulationsergebnis; Delta = Simulation - Ist; Delta % = Delta / Ist, wenn Ist > 0.",
        ("IST", "Simulation", "Delta", "Delta %"),
        "pages/9_🏢_Organisationseinheiten_Analyse.py:_build_org_metric_comparison",
        "Vergleichs-Exports enthalten numerische Rohwerte, damit Rundungen der UI nicht die Nachpruefung stoeren.",
    ),
    "simulation.departure_events": _step(
        "simulation.departure_events",
        "Abgaenge aus Simulation ableiten",
        "Das Dashboard nutzt die Audit-Ereignisse der Simulation und betrachtet nur Ereignisse, bei denen Personen das Unternehmen verlassen oder Kapazitaet verloren geht.",
        ("compact_sim_audit_tables.Abgaenge_Events_Raw", "headcount_change", "mak_change", "Organisationseinheit", "Jobfamily"),
        "Filter auf negative Kopf- oder MAK-Aenderungen; Abgaenge = Betrag der negativen Kopfveraenderung; MAK-Verlust = Betrag der negativen MAK-Veraenderung.",
        ("Abgaenge", "MAK-Verlust", "departure_summary_df"),
        "dataloader/compact_simulation_engine.py:simulate_compact_snapshot; pages/9_Organisationseinheiten_Analyse.py:_build_departure_org_summary",
        "Regressionstests pruefen, dass nur echte Unternehmensabgaenge in die Abgangsdarstellung laufen.",
    ),
    "compensation.planlevel_base": _step(
        "compensation.planlevel_base",
        "Verguetungsbasis je Planstelle aufbauen",
        "Aus der vorbereiteten Kompakt-Tabelle wird eine planstellennahe Verguetungstabelle erzeugt. Sie enthaelt je Zeile die Ist-Eingruppierung der Person, die Soll-Bewertung der Stelle sowie Kopf-, MAK- und Euro-Werte.",
        ("prepared_df", "TrfGr", "St", "Bewertung Tarifgruppe", "Text Gehaltsband", "Soll_FTE", "Soll_Cost_Year", "MAK_Reporting", "EUR_Reporting"),
        "Ist-EG/Stufe bereinigen; Soll-EG aus Bewertung Tarifgruppe und Text Gehaltsband bilden; IST/SOLL fuer Koepfe, MAK und EUR nebeneinanderstellen.",
        ("comp_df", "Ist_Entgeltgruppe", "Soll_Entgeltgruppe_H", "Soll_Entgeltgruppe_I", "IST_MAK", "SOLL_MAK_View", "IST_EUR", "SOLL_EUR_View"),
        "pages/1_Kompakt.py:build_compact_compensation_planlevel_df",
        "Tests sichern Duplikatbereinigung, technische Mini-Planstellen und exklusionsbereinigte View-Spalten ab.",
    ),
    "compensation.unassigned_findings": _step(
        "compensation.unassigned_findings",
        "IST ohne Plan-SOLL identifizieren",
        "Besetzte Zeilen ohne belastbares Plan-SOLL werden in fachliche Kategorien sortiert, zum Beispiel ruhendes Beschaeftigungsverhaeltnis, Pool-/Sammelplanstelle, ATZ/Freistellung oder regulaere aktive Stelle ohne Soll_MAK.",
        ("comp_df", "Ist_ohne_Plan_Soll_Kategorie", "PersNr", "IST_MAK", "IST_EUR", "SOLL_MAK"),
        "Filter Ist_ohne_Plan_Soll_Kategorie != leer; danach Summen fuer Zeilen, eindeutige Personen, IST_MAK und IST_EUR je Kategorie.",
        ("Warn-KPI", "Kategorie-Uebersicht", "Detail-Excel"),
        "pages/1_Kompakt.py:_classify_ist_ohne_plan_soll; pages/1_Kompakt.py:_build_ist_ohne_plan_soll_summary",
        "Der Detaildownload enthaelt dieselben Kategorien und relevante Planstellen-/Personenkontextspalten.",
    ),
    "compensation.band_fit": _step(
        "compensation.band_fit",
        "Verguetungs-Fit je Entgeltgruppen-Spanne berechnen",
        "Jede Planstelle wird ihrer Soll-Entgeltgruppen-Spanne zugeordnet. Das Dashboard prueft dann, ob die Ist-Eingruppierung der besetzten Person in diese Spanne faellt.",
        ("comp_df", "Soll_Entgeltgruppe_H", "Soll_Entgeltgruppe_I", "Ist_Entgeltgruppe", "Is_Vacant", "IST_MAK/IST_EUR", "SOLL_MAK_View/SOLL_EUR_View"),
        "Je Soll-EG-Spanne: SOLL summieren; IST in Passend, Abweichend, Vakanz und Nicht gefunden aufteilen; Kapazitaetsluecke = SOLL - Summe dieser Kategorien; Passquote = Passend / SOLL.",
        ("Fit-Uebersicht", "SOLL", "Passend", "Abweichend", "Vakanz", "Nicht gefunden", "Kapazitaetsluecke", "Passquote"),
        "pages/1_Kompakt.py:_build_compensation_band_fit_summary",
        "test_build_compensation_band_fit_summary_matches_hand_calculation prueft die Logik gegen eine manuelle Beispielrechnung.",
    ),
    "compensation.planlevel_aggregation": _step(
        "compensation.planlevel_aggregation",
        "Verguetung nach Plan- oder Entgeltgruppe aggregieren",
        "Fuer die Verguetungsanalyse werden die planstellennahen Werte nach der sichtbaren Dimension zusammengefasst und als IST, SOLL und Delta dargestellt.",
        ("comp_df", "Planebene", "Ist_Entgeltgruppe", "Soll_Entgeltgruppe", "IST_MAK", "SOLL_MAK_View", "IST_EUR", "SOLL_EUR_View"),
        "GroupBy nach aktueller Verguetungsdimension; IST und SOLL addieren; Delta = IST - SOLL; Delta % = Delta / SOLL, wenn SOLL > 0.",
        ("chart_source_df", "summary_table_df", "Excel-Datenblatt"),
        "pages/1_Kompakt.py:_build_compensation_chart_source; pages/1_Kompakt.py:_build_eg_summary_table",
        "Export-Contract-Tests pruefen, dass alle Kompakt-Downloads Lineage-IDs deklarieren.",
    ),
    "soll_ist.clean_bands": _step(
        "soll_ist.clean_bands",
        "Soll- und Ist-Entgeltgruppen standardisieren",
        "Die Kopf-Auswertung uebersetzt Stellenbewertung und Ist-Eingruppierung in vergleichbare Entgeltgruppen. Falls nur eine Soll-Grenze gepflegt ist, wird daraus eine Ein-Wert-Spanne.",
        ("work_df", "_Soll_EG_H", "_Soll_EG_I", "_Ist_EG", "Planstellennr", "Personalnummer"),
        "Soll-EG-Spanne = H, I oder H-I; fehlende Ist-EG wird als Nicht gefunden, unbesetzte Stelle als Unbesetzt gefuehrt.",
        ("_Soll_EG_Band", "_Ist_EG", "Unbesetzt", "Nicht gefunden"),
        "dataloader/soll_ist_koepfe_engine.py:build_soll_ist_koepfe_result; pages/1_Kompakt.py:_soll_eg_band_label",
        "Tests pruefen Sonderfaelle fuer Unbesetzt, Nicht gefunden und fehlende Soll-EG.",
    ),
    "soll_ist.band_matrix": _step(
        "soll_ist.band_matrix",
        "Soll-Ist-Matrix zaehlen",
        "Die Matrix zaehlt, wie viele Planstellen je Soll-Entgeltgruppen-Spanne in welcher tatsaechlichen Ist-Entgeltgruppe liegen.",
        ("work_df", "_Soll_EG_Band", "_Ist_EG"),
        "Pivot: Zeilen = Soll-EG-Spanne, Spalten = Ist-EG inklusive Unbesetzt und Nicht gefunden; Gesamt = Summe der Zeile.",
        ("Soll-Ist-Koepfe-Spannen", "Gesamt"),
        "pages/1_Kompakt.py:_build_soll_ist_band_pivot",
        "Tests validieren Matrixstruktur, Sonderzeilen und Exportinhalt.",
    ),
    "soll_ist.fit_summary": _step(
        "soll_ist.fit_summary",
        "Passung der Kopfbesetzung berechnen",
        "Aus der Matrix wird eine einfach lesbare Fit-Uebersicht. Sie zeigt je Soll-Spanne, wie viele Planstellen passend, abweichend, unbesetzt oder nicht gefunden sind.",
        ("band_pivot", "TARIFF_GROUPS", "Unbesetzt", "Nicht gefunden"),
        "Passend = Ist-EG liegt innerhalb der Soll-Spanne; Abweichend = Ist-EG liegt ausserhalb; Passquote = Passend / Planstellen.",
        ("Fit-Uebersicht", "Passend", "Abweichend", "Unbesetzt", "Nicht gefunden", "Planstellen", "Passquote"),
        "pages/1_Kompakt.py:_build_soll_ist_band_fit_summary",
        "test_build_soll_ist_band_fit_summary_splits_passend_and_abweichend prueft die Kategorien.",
    ),
    "soll_ist.detail_classification": _step(
        "soll_ist.detail_classification",
        "Detailspanne klassifizieren",
        "Fuer die ausgewaehlte Soll-Entgeltgruppen-Spanne wird jede Planstelle einzeln klassifiziert: passend, uebergruppiert, untergruppiert, unbesetzt oder nicht gefunden.",
        ("detail", "_Soll_EG_H", "_Soll_EG_I", "_Ist_EG", "TARIFF_GROUPS"),
        "Vergleich der Rangpositionen: Ist-EG < untere Soll-Grenze = untergruppiert; Ist-EG > obere Soll-Grenze = uebergruppiert; sonst passend.",
        ("_Klasse", "Detail-KPI", "Donut", "Ist-Eingruppierung"),
        "pages/1_Kompakt.py:render_ist_soll_koepfe_tab",
        "Regressionstests pruefen die Prozent-Nenner und die Uebereinstimmung zwischen KPI, Donut und Export.",
    ),
    "soll_ist.detail_breakdowns": _step(
        "soll_ist.detail_breakdowns",
        "Detailaufschluesselung aggregieren",
        "Der Detailbereich zeigt nicht einzelne Personen, sondern zaehlt die klassifizierten Planstellen nach Organisationseinheit und Planstellentyp. Dadurch sieht man, wo Abweichungen entstehen.",
        ("breakdown_subset", "_Klasse", "Organisationseinheit", "Planstelle"),
        "Ausgewaehlte Teilmenge filtern; Top-N-Kategorien behalten, Rest als Sonstige; Kreuztabelle Planstelle x Organisationseinheit; bei Abweichungen Split in ueber- und untergruppiert.",
        ("Uebersicht", "Ist-Eingruppierung", "Organisationseinheiten", "Planstellentypen", "Planstellentyp Richtung je OE"),
        "pages/1_Kompakt.py:_aggregate_detail_breakdown; pages/1_Kompakt.py:_aggregate_detail_breakdown_stacked; pages/1_Kompakt.py:_aggregate_direction_split",
        "test_render_ist_soll_koepfe_tab_detail_excel_export_matches_charts_and_hover prueft Export, Chart und Hover-Werte gemeinsam.",
    ),
    "education.ordinal_mapping": _step(
        "education.ordinal_mapping",
        "Ausbildung in Rangfolge uebersetzen",
        "Damit Qualifikationen vergleichbar sind, werden Ausbildungsabschluesse in eine fachliche Reihenfolge uebersetzt. Unbekannte oder nicht gemappte Werte werden fuer die Spannweite ausgeschlossen.",
        ("Ausbildung", "Bildungskategorie", "Bildungsrang"),
        "Mapping Ausbildung -> Bildungsrang; nur bekannte Bildungsraenge gehen in Minimum, Mittelwert und Maximum ein.",
        ("Bildungsrang", "bekannte Ausbildungswerte"),
        "dataloader/loader.py:EDUCATION_MAPPING; dataloader/loader.py:EDUCATION_RANKING; pages/1_Kompakt.py:create_education_range_data",
        "Der Export weist die Zahl ausgeschlossener unbekannter Ausbildungswerte als Hinweis in der Anzeige aus.",
    ),
    "education.range_by_position": _step(
        "education.range_by_position",
        "Qualifikationsspannweite je Planstelle berechnen",
        "Je Planstelle wird betrachtet, welche niedrigste, mittlere und hoechste bekannte Qualifikation bei den dort eingesetzten Personen vorkommt.",
        ("Planstelle", "Bildungsrang", "Ausbildung"),
        "Filter auf Planstellen mit mindestens zwei bekannten Ausbildungswerten; je Planstelle min, mean, max und Haeufigkeiten der Randwerte berechnen.",
        ("range_df", "min_label", "mean_label", "max_label", "n_min", "n_max", "count"),
        "pages/1_Kompakt.py:create_education_range_data; pages/1_Kompakt.py:create_education_range_chart",
        "Export-Contract-Tests stellen sicher, dass Grafik, Tabelle und Download dieselbe range_df verwenden.",
    ),
    "settings.integrity_check": _step(
        "settings.integrity_check",
        "Upload-Datenintegritaet pruefen",
        "Die Einstellungen pruefen, ob Mitarbeiter- und Planstellen-Uploads ueber Personalnummern zusammenpassen. Dadurch werden fehlende Matches und doppelte Nummern sichtbar, bevor sie Auswertungen verzerren.",
        ("Mitarbeiter.xlsx.PersNr", "Planstellen.xlsx.Personalnummer"),
        "Personalnummern normalisieren; besetzte Planstellen ohne Mitarbeiter-Match, Mitarbeiter ohne Planstelle und doppelte PersNr zaehlen und in Detailtabellen schreiben.",
        ("DataIntegrityReport", "Besetzte Planstellen ohne Match", "Mitarbeiter ohne Planstelle", "Dubletten"),
        "dataloader/data_integrity.py:check_mitarbeiter_planstellen_integrity; dataloader/data_integrity.py:build_integrity_report_excel",
        "Tests pruefen Match-Luecken, Dubletten und Schutz vor Excel-Formelinjektion.",
    ),
    "settings.export_context": _step(
        "settings.export_context",
        "Exportumfang und Datenbasis bestimmen",
        "Vor einem Settings- oder Setup-Download wird festgelegt, welche aktuelle Konfiguration, welche Uploads oder welche Definitionen in den Export gehoeren.",
        ("st.session_state", "Upload-Dateien", "Definitionsobjekte", "Template-Auswahl"),
        "Aktuellen App-Zustand lesen; relevante Datenbasis und Exporttyp festhalten; Kontext fuer den Lineage_Report vorbereiten.",
        ("Export-Kontext", "ausgewaehlte Datenbasis"),
        "pages/2_Einstellungen.py; components/setup_wizard.py",
        "Coverage-Tests pruefen, dass Settings- und Setup-Downloads Lineage-Informationen deklarieren.",
    ),
    "settings.upload_template": _step(
        "settings.upload_template",
        "Upload-Template aus Spezifikation erzeugen",
        "Die Excel-Vorlagen werden nicht manuell gepflegt. Sie entstehen aus einer zentralen Template-Spezifikation mit Pflichtspalten, optionalen Spalten und erlaubten Auswahlwerten.",
        ("TEMPLATE_SPECS", "columns", "choices", "strict", "row_count", "sheet_name"),
        "Headerzeile aus TEMPLATE_SPECS schreiben; fuer Choice-Spalten Datenvalidierung auf ein verstecktes Listenblatt setzen; Spaltenbreiten aus Inhaltslaenge ableiten.",
        ("Upload-Template.xlsx", "verstecktes Listenblatt", "Lineage_Report"),
        "dataloader/upload_templates.py:generate_upload_template_bytes; dataloader/upload_templates.py:_column_width",
        "Tests pruefen erwartete Spalten, versteckte Listenblaetter und Lineage_Report in Templates.",
    ),
    "settings.tvoed_template": _step(
        "settings.tvoed_template",
        "TVOED-Template aus Tarifgruppen erzeugen",
        "Die TVOED-Vorlage bildet die im Dashboard konfigurierten Tarifgruppen und Erfahrungsstufen ab. Sie ist die Eingabestruktur fuer spaetere Euro-Berechnungen.",
        ("TARIFF_GROUPS", "Stufe 1", "Stufe 2", "Stufe 3", "Stufe 4", "Stufe 5", "Stufe 6"),
        "Eine Zeile je Entgeltgruppe erzeugen; Stufenspalten 1 bis 6 anlegen; Workbook mit Lineage_Report ausgeben.",
        ("TVOED_Template.xlsx", "Entgeltgruppen", "Stufen 1-6"),
        "dataloader/upload_templates.py:generate_tvoed_template_bytes",
        "test_generate_tvoed_template_matches_loader_layout prueft Layout-Kompatibilitaet zum Loader.",
    ),
    "settings.cluster_template": _step(
        "settings.cluster_template",
        "Cluster-Mapping-Vorlage aus Datenstand erzeugen",
        "Aus dem aktuellen Datenstand werden eindeutige Organisationseinheiten sowie Kombinationen aus Organisationseinheit und Planstelle in eine Mapping-Vorlage geschrieben.",
        ("df_ma", "Organisationseinheit", "OrgEinheitNr", "Planstelle", "jf_definitions"),
        "Eindeutige Mapping-Zeilen bestimmen; bestehende Jobfamily-Definitionen als Auswahl-/Kontextbasis nutzen; Excel-Template mit Lineage_Report erzeugen.",
        ("Cluster-Template.xlsx", "OE-Mapping", "Planstellen-/Jobfamily-Mapping"),
        "dataloader/cluster_manager.py:generate_template_bytes",
        "test_cluster_template_contains_lineage_report prueft, dass das Template Lineage enthaelt.",
    ),
    "setup.definitions_export": _step(
        "setup.definitions_export",
        "Jobfamily-Definitionen exportieren",
        "Der Setup-Wizard schreibt die aktuell gepflegten Jobfamily-Definitionen als nachvollziehbares Mehrblatt-Workbook heraus.",
        ("definitions.jobfamilies", "definitions.patterns", "definitions.manual_assignments", "definitions.manual_overrides", "definitions.metadata"),
        "Definitionsobjekt in Uebersicht, Patterns, manuelle Zuordnungen und Metadata aufteilen; anschliessend Lineage_Report ergaenzen.",
        ("jobfamilies_export.xlsx", "Uebersicht", "Patterns", "Manuelle Zuordnungen", "Metadata"),
        "utils/import_export.py:export_to_excel",
        "test_jobfamily_definition_excel_contains_lineage_report prueft den Lineage-Export.",
    ),
    "setup.mapping_report": _step(
        "setup.mapping_report",
        "Jobfamily-Mapping-Report erzeugen",
        "Der Mapping-Report macht sichtbar, welche Planstellen welcher Jobfamily zugeordnet wurden und ob die Zuordnung manuell, per Pattern oder nicht erfolgt ist.",
        ("df.Planstelle", "df.Jobfamily", "df.Jobfamily_match_type", "definitions.jobfamilies.patterns"),
        "Je Planstelle Jobfamily und Match-Typ exportieren; erstes passendes Pattern ausweisen; Statistik-Sheet mit Mapping-Quote und Verteilungen berechnen.",
        ("mapping_report.xlsx", "Detail-Mapping", "Mapping-Statistik"),
        "utils/import_export.py:export_mapping_report",
        "test_mapping_report_excel_contains_lineage_report prueft den Lineage-Export.",
    ),
    "export.documentation_sheets": _step(
        "export.documentation_sheets",
        "Dokumentationsblaetter anhaengen",
        "Jeder Excel-Export ergaenzt neben den Datenblaettern auch technische und fachliche Nachweisblaetter. Dadurch bleiben Quelle, Spalten, Codebezug und Berechnungsweg gemeinsam im Download.",
        ("lineage_ids", "Export-Kontext", "Input_Lineage", "Transformations_Lineage"),
        "Lineage_Report aus Registry bauen; Input_Lineage aus Upload-Metadaten bauen; Transformations_Lineage aus Schrittketten bauen; alle Blaetter an dasselbe Workbook anhaengen.",
        ("Lineage_Report", "Input_Lineage", "Transformations_Lineage"),
        "utils/lineage/excel.py:write_lineage_sheet; utils/lineage/excel.py:append_lineage_sheet_to_workbook; utils/lineage/excel.py:add_lineage_worksheet",
        "test_lineage_export prueft alle unterstuetzten Excel-Schreibwege.",
    ),
    "analysis.tariff_structure": _step(
        "analysis.tariff_structure",
        "Tarifstruktur je Analysegruppe berechnen",
        "Die Tarifstruktur zeigt die Personalstruktur innerhalb einer Organisationseinheit oder Jobgruppe nach Tarifgruppe. Sie ist eine Struktur- und Kapazitaetsanalyse, keine Euro-Auswertung.",
        ("mapped_df", "Organisationseinheit/Jobfamily", "TrfGr", "PersNr", "MAK_Reporting", "Is_Vacant"),
        "Vakanzen ausschliessen; EUR-Sicht auf MAK zurueckfuehren; je Analysegruppe Koepfe, MAK und Durchschnitts-MAK berechnen; Tarifgruppen als Strukturspalten aggregieren.",
        ("Tarifstruktur-Tabelle", "Koepfe", "MAK", "Durchschnitts-MAK"),
        "pages/9_Organisationseinheiten_Analyse.py:_render_role_breakdown_block; pages/8_Jobfamily_Analyse.py:_render_role_breakdown_block",
        "test_role_summary_tables_exclude_vacancies_and_fallback_eur_to_mak prueft Vakanz-Ausschluss und EUR-Fallback.",
    ),
    "analysis.data_quality": _step(
        "analysis.data_quality",
        "Nicht zugeordnete Daten ausweisen",
        "Die Datenqualitaet trennt den sichtbaren Filterkontext in zugeordnete und nicht zugeordnete Zeilen. Dadurch sieht der Nutzer, welcher Anteil nicht sauber einer OE oder Jobgruppe zugeordnet werden kann.",
        ("filtered_df", "Organisationseinheit/Jobfamily", "Headcount", "MAK_Reporting", "EUR_Reporting"),
        "Zuordnungsspalte normalisieren; mapped_df und unmapped_df bilden; Restvolumen nach aktueller Kennzahl und Anteil am Filterkontext berechnen.",
        ("Datenqualitaets-KPIs", "unmapped_df", "Restvolumen"),
        "pages/9_Organisationseinheiten_Analyse.py:_render_data_quality_block; pages/8_Jobfamily_Analyse.py:_render_data_quality_block",
        "test_lineage_registry_contains_data_quality_specs dokumentiert die registrierten Datenqualitaets-IDs.",
    ),
    "export.same_dataframe": _step(
        "export.same_dataframe",
        "Grafik und Excel aus derselben Tabelle erzeugen",
        "Die Excel-Datei wird aus demselben aggregierten DataFrame erzeugt, der auch fuer Grafik und sichtbare Tabelle genutzt wird.",
        ("chart_df", "table_df", "export_df"),
        "Anzeige formatiert Werte fuer den Bildschirm; Excel erhaelt die numerische Tabelle plus Dokumentationsblaetter.",
        ("Excel-Datenblatt", "Lineage_Report", "Input_Lineage", TRANSFORMATION_LINEAGE_SHEET_NAME),
        "utils/lineage/excel.py:write_lineage_sheet",
        "Tests pruefen fuer zentrale Grafiken, dass sichtbare Werte und Exportwerte zusammenpassen.",
    ),
}


TRACE_STEP_IDS_BY_LINEAGE: dict[str, tuple[str, ...]] = {
    "1-01": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "compensation.planlevel_base", "compensation.unassigned_findings", "export.same_dataframe"),
    "1-02": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "compensation.planlevel_base", "compensation.band_fit", "export.same_dataframe"),
    "1-03": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "soll_ist.clean_bands", "soll_ist.band_matrix", "export.same_dataframe"),
    "1-04": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "soll_ist.clean_bands", "soll_ist.band_matrix", "soll_ist.fit_summary", "export.same_dataframe"),
    "1-05": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "soll_ist.clean_bands", "soll_ist.detail_classification", "soll_ist.detail_breakdowns", "export.same_dataframe"),
    "1-06": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "compensation.planlevel_base", "compensation.planlevel_aggregation", "export.same_dataframe"),
    "1-07": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "education.ordinal_mapping", "education.range_by_position", "export.same_dataframe"),
    "1-08": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "export.same_dataframe"),
    "1-09": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "export.same_dataframe"),
    "1-10": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "export.same_dataframe"),
    "1-11": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "export.same_dataframe"),
    "7-01": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "export.same_dataframe"),
    "2-01": ("input.excel_headers", "settings.export_context", "settings.integrity_check", "export.documentation_sheets", "export.same_dataframe"),
    "2-02": ("settings.export_context", "settings.upload_template", "export.documentation_sheets", "export.same_dataframe"),
    "2-03": ("settings.export_context", "settings.tvoed_template", "export.documentation_sheets", "export.same_dataframe"),
    "2-04": ("input.excel_headers", "settings.export_context", "settings.cluster_template", "export.documentation_sheets", "export.same_dataframe"),
    "2-05": ("settings.export_context", "setup.definitions_export", "export.documentation_sheets", "export.same_dataframe"),
    "2-06": ("settings.export_context", "setup.mapping_report", "export.documentation_sheets", "export.same_dataframe"),
    "8-13": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top"),
    "8-14": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top", "export.same_dataframe"),
    "8-15": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.split_composition", "export.same_dataframe"),
    "8-16": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.tariff_structure", "export.same_dataframe"),
    "8-17": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.data_quality", "export.same_dataframe"),
    "9-13": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top"),
    "9-14": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top", "export.same_dataframe"),
    "9-15": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.split_composition", "export.same_dataframe"),
    "9-16": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.tariff_structure", "export.same_dataframe"),
    "9-17": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "prep.apply_filters", "analysis.data_quality", "export.same_dataframe"),
    "10-01": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "export.same_dataframe"),
    "10-02": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top", "export.same_dataframe"),
    "10-03": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "simulation.compare_status_quo", "export.same_dataframe"),
    "10-04": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "simulation.departure_events", "analysis.group_metric", "export.same_dataframe"),
    "10-05": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.split_composition", "export.same_dataframe"),
    "10-06": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top", "simulation.departure_events", "export.same_dataframe"),
    "10-07": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.group_metric", "analysis.split_composition", "simulation.compare_status_quo", "simulation.departure_events", "export.same_dataframe"),
    "11-01": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "export.same_dataframe"),
    "11-02": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top", "export.same_dataframe"),
    "11-03": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.split_composition", "export.same_dataframe"),
    "11-04": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.group_metric", "analysis.sort_top", "export.same_dataframe"),
    "11-05": ("input.excel_headers", "prep.normalize_keys", "prep.combine_snapshot", "prep.capacity_cost", "compact.prepare_reporting", "simulation.future_snapshot", "prep.apply_filters", "analysis.group_metric", "analysis.split_composition", "export.same_dataframe"),
}


def _join(values: Iterable[Any]) -> str:
    return "; ".join(str(value) for value in values if str(value).strip())


def _source_columns(spec: LineageSpec) -> str:
    parts = []
    for source in spec.sources:
        if source.columns:
            parts.append(f"{source.table}: {', '.join(source.columns)}")
        else:
            parts.append(source.table)
    return _join(parts)


def _fallback_steps(spec: LineageSpec) -> tuple[TransformationStep, ...]:
    documented_inputs = tuple(_source_columns(spec).split("; ")) if _source_columns(spec) else ("Arbeitsdaten",)
    return (
        _step(
            f"{spec.lineage_id}.basis",
            "Datenbasis auswaehlen",
            f"Dieses Element nutzt die Datenbasis: {spec.data_basis}.",
            tuple(source.table for source in spec.sources),
            "Die fuer dieses Dashboard-Element relevanten Daten werden aus der vorbereiteten Datenbasis gelesen.",
            ("Arbeitsdaten fuer " + spec.label,),
            _join(f"{ref.file_glob}:{ref.function_name}" for ref in spec.calculations),
            "Die beteiligten logischen Quellen stehen im Lineage_Report.",
        ),
        _step(
            f"{spec.lineage_id}.filter",
            "Filter anwenden",
            "Alle im Dashboard aktiven Filter werden vor der finalen Anzeige beruecksichtigt.",
            ("Arbeitsdaten", *documented_inputs),
            _join(spec.filters) or "Keine gesonderten Filter dokumentiert.",
            ("gefilterte Arbeitsdaten",),
            "siehe Berechnungsfunktionen im Lineage_Report",
            "Der Export-Kontext dokumentiert die konkret bekannten Exportparameter.",
        ),
        _step(
            f"{spec.lineage_id}.calculation",
            "Kennzahl berechnen",
            spec.formula,
            ("gefilterte Arbeitsdaten",),
            spec.data_lineage,
            (spec.unit,),
            _join(f"{ref.file_glob}:{ref.function_name}" for ref in spec.calculations),
            _join(spec.tests),
        ),
        TRANSFORMATION_STEPS["export.same_dataframe"],
    )


def _steps_for_spec(spec: LineageSpec) -> tuple[TransformationStep, ...]:
    step_ids = TRACE_STEP_IDS_BY_LINEAGE.get(spec.lineage_id)
    if not step_ids:
        return _fallback_steps(spec)
    return tuple(TRANSFORMATION_STEPS[step_id] for step_id in step_ids)


def build_transformation_lineage_dataframe(
    lineage_ids: Iterable[str],
    *,
    export_context: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Build a layperson-readable transformation trace for selected lineage ids."""

    context = _join(f"{key}={value}" for key, value in (export_context or {}).items())
    rows: list[dict[str, Any]] = []
    for spec in get_lineage_specs(lineage_ids):
        for order, step in enumerate(_steps_for_spec(spec), start=1):
            rows.append(
                {
                    "Lineage-ID": spec.lineage_id,
                    "Element": spec.label,
                    "Seite": spec.page,
                    "Reihenfolge": order,
                    "Schritt-ID": step.step_id,
                    "Schritt": step.title,
                    "Erklaerung fuer Fachanwender": step.plain_language,
                    "Eingaben": _join(step.input_fields),
                    "Transformation / Formel": step.transformation,
                    "Ergebnisse": _join(step.output_fields),
                    "Code-Referenz": step.code_reference,
                    "Pruefung / Nachweis": step.quality_check,
                    "Export-Kontext": context,
                }
            )
    return pd.DataFrame(rows)
