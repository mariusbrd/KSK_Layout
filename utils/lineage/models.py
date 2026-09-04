"""Shared data models for dashboard lineage contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CodeReference:
    """Reference to code that contributes to a lineage calculation."""

    file_glob: str
    function_name: str


@dataclass(frozen=True)
class SourceSpec:
    """Logical source table and relevant source columns."""

    table: str
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParameterSpec:
    """Runtime parameter that should be captured with an export."""

    name: str
    source: str
    required: bool = False


@dataclass(frozen=True)
class LineageSpec:
    """Lineage contract for one exported dashboard metric or display block."""

    lineage_id: str
    label: str
    page: str
    section: str
    display_type: str
    unit: str
    data_basis: str
    sources: tuple[SourceSpec, ...]
    calculations: tuple[CodeReference, ...]
    formula: str
    filters: tuple[str, ...]
    data_lineage: str
    tests: tuple[str, ...]
    validation_status: str = "technisch bestaetigt"
    notes: str = ""
    parameters: tuple[ParameterSpec, ...] = ()
