"""Pure, versioned contract for a CompactPlus multi-run request and result.

This module does not start simulations or change the existing single-run path.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from utils.monte_carlo_ensemble import aggregate_packets


CONTRACT_VERSION = 1


def _date(value: Any, name: str) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _seed(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Run inputs must be JSON-serializable and finite") from exc


@dataclass(frozen=True)
class RunPlan:
    signature: str
    input_fingerprint: str
    base_date: str
    target_date: str
    count: int
    seed_pairs: tuple[tuple[int, int], ...]
    band_level: float
    cluster_source_signature: str | None
    _attrition_params: dict[str, Any] = field(repr=False, compare=False)
    _accession_params: dict[str, Any] = field(repr=False, compare=False)

    def params_for_index(self, index: int) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return independent params; index zero preserves both original seeds."""
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.count:
            raise IndexError("Run index is outside the plan")
        attrition = deepcopy(self._attrition_params)
        accession = deepcopy(self._accession_params)
        if index:
            attrition["random_seed"], accession["random_seed"] = self.seed_pairs[index]
        return attrition, accession


def build_run_plan(*, base_date: Any, target_date: Any,
                   attrition_params: Mapping[str, Any],
                   accession_params: Mapping[str, Any],
                   exclusions: Mapping[str, Any], include_future_hires: bool,
                   input_fingerprint: str, cluster_source_signature: str | None,
                   count: int = 1, band_level: float = 0.8) -> RunPlan:
    """Freeze the intended seed pair sequence and all effective input options."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    if not isinstance(input_fingerprint, str) or not input_fingerprint.strip():
        raise ValueError("input_fingerprint is required")
    if not isinstance(include_future_hires, bool):
        raise ValueError("include_future_hires must be boolean")
    if isinstance(band_level, bool) or not isinstance(band_level, (int, float)) or (
            not math.isfinite(float(band_level)) or not 0 < band_level < 1):
        raise ValueError("band_level must be between zero and one")
    if not isinstance(attrition_params, Mapping) or not isinstance(accession_params, Mapping):
        raise ValueError("Both forecast parameter mappings are required")
    if not isinstance(exclusions, Mapping):
        raise ValueError("exclusions must be a mapping")
    first_attrition = _seed(attrition_params.get("random_seed"), "attrition seed")
    first_accession = _seed(accession_params.get("random_seed"), "accession seed")
    opening, target = _date(base_date, "base_date"), _date(target_date, "target_date")
    if target < opening:
        raise ValueError("target_date precedes base_date")
    attrition = deepcopy(dict(attrition_params))
    accession = deepcopy(dict(accession_params))
    seed_pairs = tuple((first_attrition + i, first_accession + i)
                       for i in range(count))
    payload = {
        "contract_version": CONTRACT_VERSION,
        "input_fingerprint": input_fingerprint,
        "base_date": opening,
        "target_date": target,
        "attrition_params": attrition,
        "accession_params": accession,
        "exclusions": dict(exclusions),
        "include_future_hires": include_future_hires,
        "cluster_source_signature": cluster_source_signature,
        "count": count,
        "band_level": float(band_level),
        "seed_pairs": seed_pairs,
    }
    signature = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return RunPlan(signature, input_fingerprint, opening, target, count,
                   seed_pairs, float(band_level), cluster_source_signature,
                   attrition, accession)


def finalize_run_packets(plan: RunPlan, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Publish statistics only after every indexed run matches the request."""
    if not isinstance(plan, RunPlan):
        raise TypeError("plan must be a RunPlan")
    if not isinstance(runs, Sequence):
        raise TypeError("runs must be a sequence")
    by_index: dict[int, Mapping[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, Mapping):
            failures.append({"index": None, "error": "Invalid run envelope"})
            continue
        index = run.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < plan.count:
            failures.append({"index": index, "error": "Unexpected run index"})
            continue
        if index in by_index:
            failures.append({"index": index, "error": "Duplicate run index"})
            continue
        by_index[index] = run
    for index in range(plan.count):
        run = by_index.get(index)
        if run is None:
            failures.append({"index": index, "error": "Missing run"})
        elif run.get("signature") != plan.signature:
            failures.append({"index": index, "error": "Run signature differs"})
        elif run.get("input_fingerprint") != plan.input_fingerprint:
            failures.append({"index": index, "error": "Input fingerprint differs"})
        elif not isinstance(run.get("seed_pair"), (tuple, list)) or (
                tuple(run["seed_pair"]) != plan.seed_pairs[index]):
            failures.append({"index": index, "error": "Seed pair differs"})
        elif run.get("status") != "ok":
            failures.append({"index": index,
                             "error": str(run.get("error", "Run did not complete"))})
    result = {"contract_version": CONTRACT_VERSION, "signature": plan.signature,
              "input_fingerprint": plan.input_fingerprint,
              "requested_count": plan.count, "seed_pairs": plan.seed_pairs,
              "status": "incomplete", "failures": failures, "statistics": None}
    if failures:
        return result
    ordered = [by_index[index] for index in range(plan.count)]
    aggregated = aggregate_packets(
        base_seed=plan.seed_pairs[0][0], count=plan.count,
        runs=[{"seed": plan.seed_pairs[index][0], "status": "ok",
               "packet": run.get("packet")}
              for index, run in enumerate(ordered)],
        band_level=plan.band_level)
    if aggregated["status"] != "complete":
        result["failures"] = aggregated["failures"]
        return result
    result["status"] = "complete"
    result["statistics"] = aggregated
    return result
