"""Spawn-based, input-checked execution of independent Monte Carlo packets.

The worker function must be a module-level callable. It receives frozen input
data and independent copies of the product parameters for exactly one seed.
The legacy N=1 product call deliberately stays outside this module.
"""
from __future__ import annotations

import ctypes
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Callable

from utils.monte_carlo_run_contract import RunPlan, finalize_run_packets


_WORKER_PLAN: RunPlan | None = None
_WORKER_INPUT: Any = None
_WORKER_RUNNER: Callable[..., dict] | None = None
_WORKER_INPUT_ERROR: str | None = None


@contextmanager
def keep_system_awake():
    """Prevent Windows idle sleep only during active calculation, then release."""
    if sys.platform != "win32":
        yield
        return
    continuous = 0x80000000
    system_required = 0x00000001
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    call = kernel.SetThreadExecutionState
    call.argtypes = [ctypes.c_uint]
    call.restype = ctypes.c_uint
    if not call(continuous | system_required):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not call(continuous):
            raise ctypes.WinError(ctypes.get_last_error())


def _init_worker(plan: RunPlan, frozen_input: Any,
                 input_fingerprint: Callable[[Any], str],
                 run_seed: Callable[..., dict]) -> None:
    global _WORKER_PLAN, _WORKER_INPUT, _WORKER_RUNNER, _WORKER_INPUT_ERROR
    _WORKER_PLAN = plan
    _WORKER_INPUT = frozen_input
    _WORKER_RUNNER = run_seed
    try:
        actual = input_fingerprint(frozen_input)
        _WORKER_INPUT_ERROR = None if actual == plan.input_fingerprint else (
            "Worker input fingerprint differs from run plan")
    except Exception as exc:
        _WORKER_INPUT_ERROR = f"Worker input fingerprint failed: {type(exc).__name__}: {exc}"


def _execute_index(index: int) -> dict[str, Any]:
    plan = _WORKER_PLAN
    if plan is None or _WORKER_RUNNER is None:
        raise RuntimeError("Monte Carlo worker was not initialized")
    envelope = {"index": index, "signature": plan.signature,
                "input_fingerprint": plan.input_fingerprint,
                "seed_pair": plan.seed_pairs[index]}
    if _WORKER_INPUT_ERROR is not None:
        return {**envelope, "status": "failed", "error": _WORKER_INPUT_ERROR,
                "seconds": 0.0}
    started = perf_counter()
    try:
        attrition, accession = plan.params_for_index(index)
        packet = _WORKER_RUNNER(index, attrition, accession, _WORKER_INPUT)
        return {**envelope, "status": "ok", "packet": packet,
                "seconds": perf_counter() - started}
    except Exception as exc:
        return {**envelope, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "seconds": perf_counter() - started}


def execute_ensemble(plan: RunPlan, *, frozen_input: Any,
                     input_fingerprint: Callable[[Any], str],
                     run_seed: Callable[..., dict], workers: int = 3,
                     require_ist_charts: bool = False,
                     on_progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Return complete statistics or an explicit incomplete result, never partial means.

    ``run_seed`` must consume only the passed input/parameters. It must return
    one packet compatible with ``aggregate_packets``. The parent and every
    spawned process independently verify the same frozen input fingerprint.
    """
    if not isinstance(plan, RunPlan):
        raise TypeError("plan must be a RunPlan")
    if plan.count < 2:
        raise ValueError("N=1 must use the unchanged CompactPlus product path")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    actual = input_fingerprint(frozen_input)
    if actual != plan.input_fingerprint:
        raise ValueError("Parent input fingerprint differs from run plan")
    started = perf_counter()
    outcomes: list[dict[str, Any]] = []
    with keep_system_awake():
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
                max_workers=min(workers, plan.count), mp_context=context,
                initializer=_init_worker,
                initargs=(plan, frozen_input, input_fingerprint, run_seed)) as pool:
            pending = {pool.submit(_execute_index, index): index
                       for index in range(plan.count)}
            for future in as_completed(pending):
                index = pending[future]
                try:
                    outcomes.append(future.result())
                except Exception as exc:
                    outcomes.append({"index": index, "signature": plan.signature,
                                     "input_fingerprint": plan.input_fingerprint,
                                     "seed_pair": plan.seed_pairs[index],
                                     "status": "failed",
                                     "error": f"Worker process failed: {type(exc).__name__}: {exc}"})
                if on_progress is not None:
                    on_progress(len(outcomes), plan.count)
    result = finalize_run_packets(plan, outcomes)
    if result["status"] == "complete" and require_ist_charts:
        from utils.monte_carlo_chart_values import aggregate_ist_chart_values
        try:
            ordered = sorted(outcomes, key=lambda outcome: outcome["index"])
            result["chart_statistics"] = aggregate_ist_chart_values(
                [outcome["packet"] for outcome in ordered], band_level=plan.band_level)
        except (KeyError, TypeError, ValueError) as exc:
            result["status"] = "incomplete"
            result["statistics"] = None
            result["failures"] = [{"index": None, "error": f"CompactPlus chart contract: {exc}"}]
    result["seconds"] = perf_counter() - started
    result["workers"] = min(workers, plan.count)
    result["run_seconds"] = {outcome["index"]: outcome.get("seconds")
                             for outcome in outcomes}
    return result
