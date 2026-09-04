"""Generate a complete calculation glossary from the code-owned lineage registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from io import BytesIO
from typing import Any

import pandas as pd

from utils.lineage.registry import LineageSpec, iter_lineage_specs
from utils.lineage.transformations import build_transformation_lineage_dataframe


GLOSSARY_SHEET_NAME = "KPI_Glossar"
GLOSSARY_TRANSFORMATION_SHEET_NAME = "Berechnungsschritte"
GLOSSARY_SOURCE_SHEET_NAME = "Quellen_und_Spalten"
GLOSSARY_PARAMETER_SHEET_NAME = "Parameter"


def _join(values: Iterable[Any]) -> str:
    return "; ".join(str(value) for value in values if str(value).strip())


def _specs_by_requested_ids(lineage_ids: Iterable[str] | None = None) -> tuple[LineageSpec, ...]:
    specs = iter_lineage_specs()
    if lineage_ids is None:
        return specs

    requested = list(lineage_ids)
    requested_set = set(requested)
    selected = tuple(spec for spec in specs if spec.lineage_id in requested_set)
    found = {spec.lineage_id for spec in selected}
    missing = [lineage_id for lineage_id in requested if lineage_id not in found]
    if missing:
        raise KeyError(f"Unknown lineage ids: {', '.join(missing)}")
    return selected


def build_glossary_dataframe(lineage_ids: Iterable[str] | None = None) -> pd.DataFrame:
    """Build one layperson-readable glossary row per registered dashboard element."""

    rows: list[dict[str, Any]] = []
    for spec in _specs_by_requested_ids(lineage_ids):
        transformation_df = build_transformation_lineage_dataframe([spec.lineage_id])
        rows.append(
            {
                "Metrik-ID": spec.lineage_id,
                "Seite / Bereich": f"{spec.page} / {spec.section}",
                "Element": spec.label,
                "Darstellungstyp": spec.display_type,
                "Kurzbeschreibung": spec.formula,
                "Einheit": spec.unit,
                "Datenbasis": spec.data_basis,
                "Quellen": _join(source.table for source in spec.sources),
                "Quellspalten": _join(
                    f"{source.table}: {', '.join(source.columns)}"
                    for source in spec.sources
                    if source.columns
                ),
                "Parameter": _join(
                    f"{parameter.name} ({parameter.source})"
                    for parameter in spec.parameters
                ),
                "Code-Referenz": _join(
                    f"{ref.file_glob}:{ref.function_name}"
                    for ref in spec.calculations
                ),
                "Formel / Berechnungslogik": spec.formula,
                "Transformationsschritte": " -> ".join(
                    transformation_df["Schritt"].astype(str).tolist()
                ),
                "Filterwirkung": _join(spec.filters),
                "Data Lineage": spec.data_lineage,
                "Testnachweis": _join(spec.tests),
                "Validierungsstatus": spec.validation_status,
                "Offene Punkte": spec.notes,
            }
        )
    return pd.DataFrame(rows)


def build_source_glossary_dataframe(lineage_ids: Iterable[str] | None = None) -> pd.DataFrame:
    """Build one row per logical source table used by a lineage element."""

    rows: list[dict[str, Any]] = []
    for spec in _specs_by_requested_ids(lineage_ids):
        for source in spec.sources:
            rows.append(
                {
                    "Metrik-ID": spec.lineage_id,
                    "Element": spec.label,
                    "Seite": spec.page,
                    "Quelle": source.table,
                    "Spalten": _join(source.columns),
                }
            )
    return pd.DataFrame(rows)


def build_parameter_glossary_dataframe(lineage_ids: Iterable[str] | None = None) -> pd.DataFrame:
    """Build one row per runtime parameter documented by the registry."""

    rows: list[dict[str, Any]] = []
    for spec in _specs_by_requested_ids(lineage_ids):
        if not spec.parameters:
            rows.append(
                {
                    "Metrik-ID": spec.lineage_id,
                    "Element": spec.label,
                    "Parameter": "",
                    "Quelle": "",
                    "Pflicht": "",
                }
            )
            continue
        for parameter in spec.parameters:
            rows.append(
                {
                    "Metrik-ID": spec.lineage_id,
                    "Element": spec.label,
                    "Parameter": parameter.name,
                    "Quelle": parameter.source,
                    "Pflicht": "ja" if parameter.required else "nein",
                }
            )
    return pd.DataFrame(rows)


def build_complete_glossary_frames(
    lineage_ids: Iterable[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Return all glossary sheets derived from the same registry used by exports."""

    specs = _specs_by_requested_ids(lineage_ids)
    ids = [spec.lineage_id for spec in specs]
    return {
        GLOSSARY_SHEET_NAME: build_glossary_dataframe(ids),
        GLOSSARY_TRANSFORMATION_SHEET_NAME: build_transformation_lineage_dataframe(ids),
        GLOSSARY_SOURCE_SHEET_NAME: build_source_glossary_dataframe(ids),
        GLOSSARY_PARAMETER_SHEET_NAME: build_parameter_glossary_dataframe(ids),
    }


def build_complete_glossary_workbook_bytes(
    lineage_ids: Iterable[str] | None = None,
    *,
    export_context: Mapping[str, Any] | None = None,
) -> bytes:
    """Build an Excel workbook with the complete dynamic calculation glossary."""

    frames = build_complete_glossary_frames(lineage_ids)
    context = _join(f"{key}={value}" for key, value in (export_context or {}).items())
    if context:
        for dataframe in frames.values():
            dataframe["Export-Kontext"] = context

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, dataframe in frames.items():
            dataframe.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    buffer.seek(0)
    return buffer.getvalue()
