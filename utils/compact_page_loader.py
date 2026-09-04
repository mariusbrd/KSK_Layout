"""
Helpers to load the existing Kompakt page as a reusable module.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


_COMPACT_PAGE_CACHE: tuple[tuple[int, int], ModuleType] | None = None


def _compact_page_path() -> Path:
    return Path(__file__).resolve().parents[1] / "pages" / "1_⚡_Kompakt.py"


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def clear_compact_page_module_cache() -> None:
    """Clear the dynamic page-module cache, primarily for tests and dev reloads."""

    global _COMPACT_PAGE_CACHE
    _COMPACT_PAGE_CACHE = None


def load_compact_page_module():
    global _COMPACT_PAGE_CACHE

    page_path = _compact_page_path()
    signature = _file_signature(page_path)
    if _COMPACT_PAGE_CACHE is not None and _COMPACT_PAGE_CACHE[0] == signature:
        return _COMPACT_PAGE_CACHE[1]

    spec = spec_from_file_location("compact_page_module", page_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Kompakt-Seite konnte nicht geladen werden: {page_path}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    _COMPACT_PAGE_CACHE = (signature, module)
    return module
