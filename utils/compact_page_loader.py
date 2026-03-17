"""
Helpers to load the existing Kompakt page as a reusable module.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


@lru_cache(maxsize=1)
def load_compact_page_module():
    page_path = Path(__file__).resolve().parents[1] / "pages" / "1_⚡_Kompakt.py"
    spec = spec_from_file_location("compact_page_module", page_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Kompakt-Seite konnte nicht geladen werden: {page_path}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
