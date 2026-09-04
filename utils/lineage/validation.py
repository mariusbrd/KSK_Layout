"""Validation helpers for lineage registry and transformation coverage."""

from __future__ import annotations

from dataclasses import dataclass

from utils.lineage.registry import iter_lineage_specs
from utils.lineage.transformations import TRACE_STEP_IDS_BY_LINEAGE, TRANSFORMATION_STEPS


@dataclass(frozen=True)
class LineageValidationResult:
    """Summary of structural lineage coverage checks."""

    missing_trace_ids: tuple[str, ...]
    extra_trace_ids: tuple[str, ...]
    unknown_step_ids: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.missing_trace_ids and not self.extra_trace_ids and not self.unknown_step_ids


def validate_lineage_coverage() -> LineageValidationResult:
    """Check that every registry entry has a valid explicit transformation trace."""

    registry_ids = {spec.lineage_id for spec in iter_lineage_specs()}
    trace_ids = set(TRACE_STEP_IDS_BY_LINEAGE)
    unknown_step_ids = {
        step_id
        for step_ids in TRACE_STEP_IDS_BY_LINEAGE.values()
        for step_id in step_ids
        if step_id not in TRANSFORMATION_STEPS
    }
    return LineageValidationResult(
        missing_trace_ids=tuple(sorted(registry_ids - trace_ids)),
        extra_trace_ids=tuple(sorted(trace_ids - registry_ids)),
        unknown_step_ids=tuple(sorted(unknown_step_ids)),
    )
