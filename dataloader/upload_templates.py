"""
Vorlagen-Generator fuer die Original-Daten-Uploads.

Erzeugt leere, aber vorformatierte Excel-Vorlagen (Kopfzeile + Dropdown-
Validierung) fuer Mitarbeiter.xlsx, Planstellen.xlsx, ATZ.xlsx,
Ausbildung.xlsx und TVOED.xlsx. Spalten und Wertebereiche sind auf die
tatsaechlich von dataloader/loader.py und dataloader/tvoed_loader.py
gelesenen Spaltennamen abgestimmt.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List

import streamlit as st
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from config.settings import EDUCATION_GROUPS, TARIFF_GROUPS

DATE_FORMAT = "dd.mm.yyyy"

# strict=True -> Excel blockiert Werte ausserhalb der Liste (harte Systemlogik,
# z.B. ATZ-Phase steuert MAK-Berechnung direkt).
# strict=False (Default) -> nur Warnhinweis, Eingabe bleibt moeglich (Datenbestand
# kann in der Praxis von der dokumentierten Werteliste abweichen, siehe
# _manual_notes/Readme_*.md).
TEMPLATE_SPECS: Dict[str, Dict[str, Any]] = {
    "Mitarbeiter": {
        "download_filename": "Mitarbeiter_Template.xlsx",
        "sheet_name": "Mitarbeiter",
        "row_count": 1500,
        "columns": [
            {"name": "PersNr", "type": "int"},
            {"name": "Vorname", "type": "text"},
            {"name": "Nachname", "type": "text"},
            {"name": "GebDatum", "type": "date"},
            {"name": "Text Gsch", "type": "choice", "choices": ["männlich", "weiblich"], "strict": True},
            {"name": "Eintritt", "type": "date"},
            {"name": "Austritt", "type": "date"},
            {"name": "BsGrd", "type": "float"},
            {
                "name": "Vertragsart",
                "type": "choice",
                "choices": [
                    "Unbefristet", "Zeitvertrag", "Altersteilzeit",
                    "Ausbildung", "Trainee", "Werkstudentenvertrag",
                ],
            },
            {"name": "MitarbKreisbez.", "type": "text"},
            {
                "name": "MitarbGruppenbez.",
                "type": "choice",
                "choices": ["Angestellte", "Auszubildende", "Vorstand"],
            },
            {
                "name": "Bankspezifisch",
                "type": "choice",
                "choices": ["bankspezifisch", "nicht bankspezifisch"],
            },
            {"name": "Bezeichnung", "type": "choice", "choices": ["aktiv"]},
            {
                "name": "Status kundenindividuell",
                "type": "choice",
                "choices": ["Aktives Beschäftigungsverhältnis", "Ruhendes Beschäftigungsverhältnis"],
                "strict": True,
            },
            {
                "name": "Tarifarttext",
                "type": "choice",
                "choices": ["TVÖD", "Auszubildende-VKA", "Vorstandsvergütung"],
            },
            {"name": "Tarifgebiettext", "type": "choice", "choices": ["Westdeutschland"]},
            {"name": "TrfGr", "type": "choice", "choices": TARIFF_GROUPS},
            {"name": "St", "type": "choice", "choices": ["1", "2", "3", "4", "5", "6"]},
        ],
    },
    "Planstellen": {
        "download_filename": "Planstellen_Template.xlsx",
        "sheet_name": "Planstellen",
        "row_count": 2000,
        "columns": [
            {"name": "Kürzel OrgEinheit", "type": "text"},
            {"name": "OrgEinheitNr", "type": "int"},
            {"name": "Organisationseinheit", "type": "text"},
            {"name": "Planstellennr", "type": "int"},
            {"name": "Planstellenkürzel", "type": "text"},
            {"name": "Planstelle", "type": "text"},
            {"name": "Sollarbeitszeit", "type": "float"},
            {"name": "Bewertung Tarifgruppe", "type": "choice", "choices": TARIFF_GROUPS},
            {"name": "Text Gehaltsband", "type": "text"},
            {"name": "Personalnummer", "type": "int"},
        ],
    },
    "ATZ": {
        "download_filename": "ATZ_Template.xlsx",
        "sheet_name": "ATZ",
        "row_count": 300,
        "columns": [
            {"name": "PersNr", "type": "int"},
            {"name": "Beginn", "type": "date"},
            {"name": "Ende", "type": "date"},
            {"name": "Ende ATZ Vertrag", "type": "date"},
            {"name": "Modell", "type": "choice", "choices": ["OAT5"]},
            {"name": "Phase", "type": "choice", "choices": ["AR", "FR"], "strict": True},
        ],
    },
    "Ausbildung": {
        "download_filename": "Ausbildung_Template.xlsx",
        "sheet_name": "Ausbildung",
        "row_count": 1500,
        "columns": [
            {"name": "Personalnummer", "type": "int"},
            {"name": "Ausbildungsgruppe", "type": "int"},
            {"name": "BV Ausbildungsgruppentext", "type": "choice", "choices": EDUCATION_GROUPS},
            {"name": "Betriebsvergleich Ausbildung", "type": "int"},
        ],
    },
}


def _column_width(name: str) -> float:
    return max(12.0, min(40.0, len(name) + 4))


@st.cache_data(show_spinner=False)
def generate_upload_template_bytes(spec_key: str) -> bytes:
    """Generates an empty, pre-formatted Excel template for one of the Original-Daten uploads."""
    spec = TEMPLATE_SPECS[spec_key]
    columns: List[Dict[str, Any]] = spec["columns"]
    row_count = spec.get("row_count", 1000)

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    header_format = workbook.add_format({
        "bold": True,
        "bg_color": "#0088DE",
        "font_color": "#FFFFFF",
        "border": 1,
        "align": "left",
        "valign": "vcenter",
    })
    date_format = workbook.add_format({"num_format": DATE_FORMAT})

    sheet = workbook.add_worksheet(spec["sheet_name"])

    choice_columns = [c for c in columns if c["type"] == "choice" and c.get("choices")]
    lists_sheet = None
    if choice_columns:
        lists_sheet = workbook.add_worksheet("Listen")
        lists_sheet.hide()

    list_col_by_name = {c["name"]: idx for idx, c in enumerate(choice_columns)}

    for col_idx, col in enumerate(columns):
        sheet.write(0, col_idx, col["name"], header_format)
        width = _column_width(col["name"])

        if col["type"] == "date":
            sheet.set_column(col_idx, col_idx, width, date_format)
        else:
            sheet.set_column(col_idx, col_idx, width)

        if col["type"] == "choice" and col.get("choices"):
            choices = col["choices"]
            list_col_idx = list_col_by_name[col["name"]]
            lists_sheet.write(0, list_col_idx, col["name"])
            for i, value in enumerate(choices, start=1):
                lists_sheet.write(i, list_col_idx, value)

            list_col_letter = xl_col_to_name(list_col_idx)
            source_formula = f"=Listen!${list_col_letter}$2:${list_col_letter}${len(choices) + 1}"
            strict = bool(col.get("strict"))
            sheet.data_validation(
                1, col_idx, row_count, col_idx,
                {
                    "validate": "list",
                    "source": source_formula,
                    "input_title": col["name"],
                    "input_message": "Bitte einen Wert aus der Liste wählen.",
                    "error_type": "stop" if strict else "warning",
                    "error_title": "Ungültiger Wert" if strict else "Hinweis",
                    "error_message": (
                        "Dieser Wert wird vom System nicht erkannt."
                        if strict
                        else "Dieser Wert weicht von den bekannten Standardwerten ab. Trotzdem übernehmen?"
                    ),
                },
            )

    sheet.freeze_panes(1, 0)
    workbook.close()
    return output.getvalue()


@st.cache_data(show_spinner=False)
def generate_tvoed_template_bytes() -> bytes:
    """Generates an empty TVÖD-Entgelttabelle template matching tvoed_loader's expected layout."""
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    sheet = workbook.add_worksheet("Entgelttabelle")

    title_format = workbook.add_format({"bold": True, "font_size": 12})
    header_format = workbook.add_format({
        "bold": True,
        "bg_color": "#0088DE",
        "font_color": "#FFFFFF",
        "border": 1,
        "align": "center",
    })
    group_format = workbook.add_format({"bold": True, "border": 1})
    money_format = workbook.add_format({"border": 1, "num_format": "#,##0.00"})

    sheet.write(0, 0, "Entgelttabelle TVÖD (Monatsgehälter in EUR)", title_format)

    sheet.write(1, 0, "€", header_format)
    for step in range(1, 7):
        sheet.write(1, step, step, header_format)

    for row_idx, group in enumerate(TARIFF_GROUPS, start=2):
        sheet.write(row_idx, 0, group, group_format)
        for step in range(1, 7):
            sheet.write_blank(row_idx, step, None, money_format)

    sheet.set_column(0, 0, 14)
    sheet.set_column(1, 6, 12)
    sheet.freeze_panes(2, 1)

    workbook.close()
    return output.getvalue()


__all__ = [
    "TEMPLATE_SPECS",
    "generate_tvoed_template_bytes",
    "generate_upload_template_bytes",
]
