"""Excel exchange for annual quit rates. All public values are percentages."""
from __future__ import annotations

from io import BytesIO
import math

import pandas as pd

COHORTS = ("alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus")
RATE_COLUMN = "Kündigungsquote p.a. (%)"
AGE_COLUMNS = ("Unter 30 (%)", "30 bis unter 45 (%)", "45 bis unter 55 (%)", "Ab 55 (%)")


def as_table(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    table = frame.copy() if dimension in frame.columns else frame.reset_index()
    return table[[dimension, *COHORTS]].copy()


def has_age_differences(frame: pd.DataFrame, dimension: str) -> bool:
    return bool((as_table(frame, dimension)[list(COHORTS)].nunique(axis=1) > 1).any())


def export_quit_template(frame: pd.DataFrame, dimension: str, detailed: bool) -> bytes:
    if dimension not in ("OrgUnit", "JobFamily"):
        raise ValueError("Unbekannte Dimension.")
    table = as_table(frame, dimension)
    table[dimension] = table[dimension].astype(str)
    if detailed:
        table = table.rename(columns=dict(zip(COHORTS, AGE_COLUMNS)))
    else:
        different = table[list(COHORTS)].nunique(axis=1) > 1
        table = table[[dimension, COHORTS[0]]].rename(columns={COHORTS[0]: RATE_COLUMN})
        # Never guess a single rate when the current four age rates differ.
        table[RATE_COLUMN] = table[RATE_COLUMN].astype(float).mask(different)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        table.to_excel(writer, sheet_name="Quoten", index=False)
        workbook = writer.book
        sheet = writer.sheets["Quoten"]
        sheet.freeze_panes(1, 1)
        sheet.autofilter(0, 0, len(table), len(table.columns) - 1)
        sheet.set_column(0, 0, 35, workbook.add_format({"num_format": "@"}))
        sheet.set_column(1, len(table.columns) - 1, 24, workbook.add_format({"num_format": "0.00"}))
        sheet.data_validation(1, 1, len(table), len(table.columns) - 1, {
            "validate": "decimal", "criteria": "between", "minimum": 0, "maximum": 100,
            "input_title": "Jahresquote in Prozent", "input_message": "3 = 3 %, 0,5 = 0,5 %.",
        })
        notes = pd.DataFrame({"Hinweise": [
            f"Dimension: {dimension}", "Alle Quoten gelten pro Jahr. 3 bedeutet 3 %, 0,5 bedeutet 0,5 %.",
            "Gruppennamen und Organisationsnummern unverändert lassen; führende Nullen erhalten.",
            "Einfaches Format: Die Quote gilt für alle vier Altersgruppen.",
            "Bei unterschiedlichen Altersquoten bleibt die einfache Quote leer. Bitte selbst eine Quote eintragen.",
            "Detailliertes Format: Jede Altersgruppe erhält ihre eigene Quote.",
            "Nur enthaltene Gruppen werden geändert. Nicht benötigte Zeilen vollständig entfernen.",
            "Leere Quoten, doppelte oder unbekannte Gruppen verhindern die Übernahme.",
            "Default ist der Rückfallwert für Gruppen ohne eigenen Eintrag.",
        ]})
        notes.to_excel(writer, sheet_name="Hinweise", index=False)
        writer.sheets["Hinweise"].set_column(0, 0, 110)
    return output.getvalue()


def parse_quit_upload(data: bytes, dimension: str, allowed_names: list[str]) -> pd.DataFrame:
    """Validate the entire file before returning any updates (no partial application)."""
    try:
        book = pd.ExcelFile(BytesIO(data), engine="openpyxl")
        if "Quoten" not in book.sheet_names:
            raise ValueError("Das Tabellenblatt 'Quoten' fehlt. Bitte die Vorlage verwenden.")
        table = pd.read_excel(book, sheet_name="Quoten", dtype=object, keep_default_na=False)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Die Datei konnte nicht als Excel-Datei gelesen werden.") from exc
    simple_columns = [dimension, RATE_COLUMN]
    detailed_columns = [dimension, *AGE_COLUMNS]
    if set(table.columns) == set(simple_columns):
        detailed = False
    elif set(table.columns) == set(detailed_columns):
        detailed = True
    else:
        raise ValueError(f"Spalten passen nicht zu {dimension}. Bitte die passende Vorlage verwenden.")
    table = table.loc[~table.apply(lambda row: all(str(v).strip() == "" for v in row), axis=1)]
    if table.empty:
        raise ValueError("Die Datei enthält keine Quoten.")
    names = table[dimension].map(lambda v: str(v).strip())
    if (names == "").any():
        raise ValueError("Eine Gruppe fehlt. Bitte jede Zeile einer Gruppe zuordnen.")
    duplicate = names[names.duplicated()].tolist()
    if duplicate:
        raise ValueError("Gruppen mehrfach enthalten: " + ", ".join(duplicate))
    unknown = sorted(set(names) - set(map(str, allowed_names)))
    if unknown:
        raise ValueError("Unbekannte Gruppen: " + ", ".join(unknown))
    result = pd.DataFrame({dimension: names.to_list()})
    for cohort, column in zip(COHORTS, AGE_COLUMNS):
        source = column if detailed else RATE_COLUMN
        values = []
        for row_index, name, raw in zip(table.index, names, table[source]):
            try:
                if isinstance(raw, bool):
                    raise ValueError
                value = float(str(raw).strip().replace(",", "."))
                cell = book.book["Quoten"].cell(row=int(row_index) + 2, column=table.columns.get_loc(source) + 1)
                if "%" in cell.number_format:
                    value *= 100.0
                if not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError(f"{name}: '{source}' muss eine Zahl zwischen 0 und 100 sein (3 = 3 %).") from None
            values.append(value)
        result[cohort] = values
    return result


def merge_quit_updates(frame: pd.DataFrame, updates: pd.DataFrame, dimension: str) -> pd.DataFrame:
    result = as_table(frame, dimension).set_index(dimension)
    result[list(COHORTS)] = result[list(COHORTS)].astype(float)
    updates = updates.set_index(dimension)
    result.index = result.index.astype(str)
    for name, row in updates.iterrows():
        if name not in result.index:
            raise ValueError(f"Unbekannte Gruppe: {name}")
        for cohort in COHORTS:
            before, after = float(result.loc[name, cohort]), float(row[cohort])
            if not math.isclose(before, after, rel_tol=0, abs_tol=1e-10):
                result.loc[name, cohort] = after
    return result.reset_index() if dimension in frame.columns else result.rename_axis(dimension)


def matrix_from_table(frame: pd.DataFrame, dimension: str) -> dict:
    table = as_table(frame, dimension).set_index(dimension)
    return {cohort: {str(name): float(value) for name, value in table[cohort].items()} for cohort in COHORTS}


def preview_quit_updates(frame: pd.DataFrame, updates: pd.DataFrame, dimension: str) -> pd.DataFrame:
    old = as_table(frame, dimension).set_index(dimension)
    old.index = old.index.astype(str)
    rows = []
    for _, row in updates.iterrows():
        for cohort, label in zip(COHORTS, AGE_COLUMNS):
            before, after = float(old.loc[row[dimension], cohort]), float(row[cohort])
            if not math.isclose(before, after, rel_tol=0, abs_tol=1e-10):
                rows.append({dimension: row[dimension], "Altersgruppe": label.replace(" (%)", ""),
                             "Bisher (%)": before, "Neu (%)": after})
    return pd.DataFrame(rows, columns=[dimension, "Altersgruppe", "Bisher (%)", "Neu (%)"])
