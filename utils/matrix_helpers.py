"""
Pure-function helpers for percent-based distribution matrices.

All functions are Streamlit-free and fully unit-testable.

Conventions
-----------
- **percent scale** (0–100): what the user sees / edits in the UI.
- **weight scale**  (0–1):   what the simulation engines consume.

The "Default" key is always preserved but excluded from sum validation
(it serves as a per-row fallback, not a share in the distribution).
"""

from __future__ import annotations

from typing import Dict, Tuple


# ---------------------------------------------------------------------------
# 1. Scale conversion
# ---------------------------------------------------------------------------

def percent_to_weights(matrix_pct: dict) -> dict:
    """Convert every numeric value from percent (0–100) to weight (0–1).

    Handles nested dicts (e.g. quit_matrix with age-cohort sub-dicts).
    """
    result: dict = {}
    for k, v in matrix_pct.items():
        if isinstance(v, dict):
            result[k] = percent_to_weights(v)
        elif isinstance(v, (int, float)):
            result[k] = float(v) / 100.0
        else:
            result[k] = v
    return result


def weights_to_percent(matrix_wt: dict) -> dict:
    """Convert every numeric value from weight (0–1) to percent (0–100).

    Handles nested dicts (e.g. quit_matrix with age-cohort sub-dicts).
    """
    result: dict = {}
    for k, v in matrix_wt.items():
        if isinstance(v, dict):
            result[k] = weights_to_percent(v)
        elif isinstance(v, (int, float)):
            result[k] = float(v) * 100.0
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# 2. Validation (share matrices only – where sum should ≈ 100 %)
# ---------------------------------------------------------------------------

def validate_percent_matrix(
    matrix_pct: dict,
    tolerance: float = 1.0,
) -> Tuple[bool, str]:
    """Check whether the non-Default entries sum to ≈ 100 %.

    Returns (is_valid, human-readable message).
    Tolerance is in percentage-points (default ±1 pp).
    """
    vals = {k: float(v) for k, v in matrix_pct.items()
            if k != "Default" and isinstance(v, (int, float))}
    if not vals:
        return True, ""
    total = sum(vals.values())
    if abs(total - 100.0) <= tolerance:
        return True, f"Summe: {total:.1f} %"
    return False, f"Summe: {total:.1f} % (Ziel: 100 %)"


def auto_normalize_percent(matrix_pct: dict) -> dict:
    """Scale non-Default values so they sum to exactly 100 %.

    The "Default" entry (if present) is kept unchanged.
    Returns a new dict (original is not mutated).
    """
    default_val = matrix_pct.get("Default")
    vals = {k: float(v) for k, v in matrix_pct.items()
            if k != "Default" and isinstance(v, (int, float))}
    if not vals:
        return dict(matrix_pct)

    total = sum(vals.values())
    if total == 0:
        return dict(matrix_pct)

    result = {k: v / total * 100.0 for k, v in vals.items()}
    if default_val is not None:
        result["Default"] = default_val
    return result


# ---------------------------------------------------------------------------
# 3. Backward-compatibility helpers (old 0–1 → new 0–100)
# ---------------------------------------------------------------------------

def is_old_scale(matrix: dict) -> bool:
    """Heuristic: all numeric values ≤ 1.0 → probably old 0–1 format.

    Returns False for empty matrices or matrices that already look
    like percent values (any value > 1).
    """
    nums = _extract_numeric_values(matrix)
    if not nums:
        return False
    return all(v <= 1.0 for v in nums) and any(v > 0 for v in nums)


def migrate_to_percent(matrix: dict) -> dict:
    """If *matrix* is in old 0–1 scale, multiply all values × 100.

    If already in percent scale (any value > 1), return a shallow copy
    unchanged.  Handles nested dicts (quit_matrix).
    """
    if not is_old_scale(matrix):
        return _copy_deep(matrix)
    return _multiply_values(matrix, 100.0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_numeric_values(d: dict) -> list[float]:
    """Recursively collect all numeric leaf values."""
    values: list[float] = []
    for v in d.values():
        if isinstance(v, dict):
            values.extend(_extract_numeric_values(v))
        elif isinstance(v, (int, float)):
            values.append(float(v))
    return values


def _multiply_values(d: dict, factor: float) -> dict:
    """Recursively multiply all numeric leaf values by *factor*."""
    result: dict = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _multiply_values(v, factor)
        elif isinstance(v, (int, float)):
            result[k] = float(v) * factor
        else:
            result[k] = v
    return result


def _copy_deep(d: dict) -> dict:
    """Simple recursive copy for dicts with numeric/string leaves."""
    result: dict = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _copy_deep(v)
        else:
            result[k] = v
    return result
