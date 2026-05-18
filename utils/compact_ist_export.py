"""
Excel export helpers for the Kompakt IST demographic export.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from dataloader.kpi_engine import get_unique_employees
from utils.compact_simulation_export import (
    DEFAULT_DEMOGRAPHIC_DIMENSIONS,
    EXCEL_MIME,
    build_jobfamily_demographics,
    build_jobfamily_summary,
)


def _display_value(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%d.%m.%Y")
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
        return ""
    return str(value)


def _employee_view(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    active = df[df["Is_Vacant"] != True].copy() if "Is_Vacant" in df.columns else df.copy()
    if "PersNr" not in active.columns:
        return active
    active = active[active["PersNr"].notna() & active["PersNr"].astype(str).str.strip().ne("")]
    return get_unique_employees(active)


def _first_existing_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _numeric_sum(series: pd.Series | None) -> float:
    if series is None:
        return 0.0
    return float(pd.to_numeric(series, errors="coerce").fillna(0).sum())


def _dimension_values(series: pd.Series) -> pd.Series:
    return series.astype(object).where(series.notna(), "(unbekannt)").astype(str)


def build_company_summary(prepared_df: pd.DataFrame) -> pd.DataFrame:
    """Builds the overall IST company KPI sheet."""
    emp = _employee_view(prepared_df)
    mak_col = _first_existing_column(emp, ("MAK_Reporting", "MAK_Calculated", "mak", "MAK", "FTE_person"))
    eur_col = _first_existing_column(emp, ("EUR_Reporting", "Total_Cost_Year"))

    heads = int(emp["PersNr"].nunique()) if "PersNr" in emp.columns and not emp.empty else int(len(emp))
    mak = _numeric_sum(emp[mak_col]) if mak_col else 0.0
    eur = _numeric_sum(emp[eur_col]) if eur_col else 0.0

    rows = [
        {"Kennzahl": "Köpfe", "Wert": heads, "Beschreibung": "Anzahl eindeutiger aktiver Mitarbeitender im IST."},
        {"Kennzahl": "MAK", "Wert": mak, "Beschreibung": "Summe der Management-MAK/FTE im IST."},
        {"Kennzahl": "EUR", "Wert": eur, "Beschreibung": "Jahreskosten im IST, sofern im Kompakt-Datensatz verfügbar."},
        {
            "Kennzahl": "Kosten pro Kopf",
            "Wert": eur / heads if heads else 0.0,
            "Beschreibung": "EUR geteilt durch Köpfe.",
        },
        {
            "Kennzahl": "Kosten pro MAK",
            "Wert": eur / mak if mak else 0.0,
            "Beschreibung": "EUR geteilt durch MAK.",
        },
    ]
    return pd.DataFrame(rows)


def build_company_demographics(
    prepared_df: pd.DataFrame,
    dimensions: Iterable[str] = DEFAULT_DEMOGRAPHIC_DIMENSIONS,
) -> pd.DataFrame:
    """Builds a long-format demographic IST breakdown for the full company."""
    emp = _employee_view(prepared_df)
    columns = [
        "Scope",
        "Dimension",
        "Ausprägung",
        "Köpfe",
        "MAK",
        "EUR",
        "Köpfe Anteil Unternehmen",
        "MAK Anteil Unternehmen",
        "EUR Anteil Unternehmen",
    ]
    if emp.empty:
        return pd.DataFrame(columns=columns)

    mak_col = _first_existing_column(emp, ("MAK_Reporting", "MAK_Calculated", "mak", "MAK", "FTE_person"))
    eur_col = _first_existing_column(emp, ("EUR_Reporting", "Total_Cost_Year"))
    total_heads = int(emp["PersNr"].nunique()) if "PersNr" in emp.columns else int(len(emp))
    total_mak = _numeric_sum(emp[mak_col]) if mak_col else 0.0
    total_eur = _numeric_sum(emp[eur_col]) if eur_col else 0.0

    rows: list[dict[str, Any]] = []
    for dimension in [dim for dim in dimensions if dim in emp.columns]:
        work = emp.copy()
        work[dimension] = _dimension_values(work[dimension])
        for value, group in work.groupby(dimension, dropna=False, observed=True):
            heads = int(group["PersNr"].nunique()) if "PersNr" in group.columns else int(len(group))
            mak = _numeric_sum(group[mak_col]) if mak_col else 0.0
            eur = _numeric_sum(group[eur_col]) if eur_col else 0.0
            rows.append(
                {
                    "Scope": "Unternehmen",
                    "Dimension": dimension,
                    "Ausprägung": value,
                    "Köpfe": heads,
                    "MAK": mak,
                    "EUR": eur,
                    "Köpfe Anteil Unternehmen": heads / total_heads if total_heads else 0.0,
                    "MAK Anteil Unternehmen": mak / total_mak if total_mak else 0.0,
                    "EUR Anteil Unternehmen": eur / total_eur if total_eur else 0.0,
                }
            )

    if not rows:
        return pd.DataFrame(columns=columns)

    return pd.DataFrame(rows).sort_values(
        ["Dimension", "Köpfe", "MAK", "EUR"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)


def build_documentation_sheet(
    *,
    stichtag: Any,
    prepared_df: pd.DataFrame,
    dimensions: Iterable[str] = DEFAULT_DEMOGRAPHIC_DIMENSIONS,
) -> pd.DataFrame:
    available_dimensions = [dim for dim in dimensions if dim in prepared_df.columns]
    rows = [
        ("Exportzeitpunkt", datetime.now().strftime("%d.%m.%Y %H:%M:%S")),
        ("Exporttyp", "Kompakt IST Demografie"),
        ("Stichtag", _display_value(stichtag)),
        ("Scope", "Ungefilterter IST-Bestand der Kompakt-Seite nach globaler Datenaufbereitung und Exklusionslogik."),
        ("Dimensionen", ", ".join(available_dimensions)),
        ("01_Unternehmen", "KPI-Gesamtsicht für das Unternehmen."),
        ("02_Unternehmen_Demografie", "Demografische Verteilung auf Unternehmensebene."),
        ("03_Jobfamily_Summary", "Köpfe, MAK und EUR je Jobfamily."),
        ("04_Jobfamily_Demografie", "Demografische Verteilung je Jobfamily."),
    ]
    return pd.DataFrame(rows, columns=["Feld", "Wert"])


def build_compact_ist_demographics_export_bytes(
    *,
    prepared_df: pd.DataFrame,
    stichtag: Any,
    dimensions: Iterable[str] = DEFAULT_DEMOGRAPHIC_DIMENSIONS,
) -> bytes:
    """Returns an XLSX workbook with IST demographics for company and jobfamilies."""
    documentation_df = build_documentation_sheet(
        stichtag=stichtag,
        prepared_df=prepared_df,
        dimensions=dimensions,
    )
    company_summary_df = build_company_summary(prepared_df)
    company_demographics_df = build_company_demographics(prepared_df, dimensions=dimensions)
    jobfamily_summary_df = build_jobfamily_summary(prepared_df)
    jobfamily_demographics_df = build_jobfamily_demographics(prepared_df, dimensions=dimensions)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sheets = [
            ("00_Dokumentation", documentation_df),
            ("01_Unternehmen", company_summary_df),
            ("02_Unternehmen_Demografie", company_demographics_df),
            ("03_Jobfamily_Summary", jobfamily_summary_df),
            ("04_Jobfamily_Demografie", jobfamily_demographics_df),
        ]
        for sheet_name, table in sheets:
            table.to_excel(writer, sheet_name=sheet_name[:31], index=False)

        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for col_cells in worksheet.columns:
                max_len = max((len(str(cell.value or "")) for cell in col_cells), default=10)
                worksheet.column_dimensions[col_cells[0].column_letter].width = min(max_len + 3, 60)

    return output.getvalue()

