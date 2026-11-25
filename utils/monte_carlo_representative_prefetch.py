"""Prepare selected complete seed details in isolated background processes."""
from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
import multiprocessing
from threading import Lock

from utils.monte_carlo_representative_data import (
    materialize_prepared_representative_seed_detail,
    prepare_representative_seed_detail,
)


PREFETCH_KEY = "org_sim_representative_prefetch"
_executor: ProcessPoolExecutor | None = None
_executor_lock = Lock()
_preparation_threads = ThreadPoolExecutor(max_workers=2)


def _pool() -> ProcessPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ProcessPoolExecutor(
                max_workers=2, mp_context=multiprocessing.get_context("spawn"))
        return _executor


def _prepare_and_submit(request, ensemble: dict, index: int):
    # Decoding and certification read immutable inputs only. The simulation
    # itself runs in a separate process because it temporarily patches module
    # globals, which must never happen alongside Streamlit rendering.
    saved = prepare_representative_seed_detail(request, ensemble, index)
    return _pool().submit(materialize_prepared_representative_seed_detail,
                          request, saved).result()


def submit_detail(request, ensemble: dict, index: int) -> Future:
    """Prepare one path without copying the entire ensemble to the process."""
    return _preparation_threads.submit(_prepare_and_submit, request, ensemble, index)
