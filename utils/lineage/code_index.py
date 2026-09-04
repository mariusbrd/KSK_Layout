"""Code-level lineage helpers.

This module turns registry code references into stable source hashes without
importing the referenced Streamlit page modules.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from utils.lineage.registry import CodeReference, iter_lineage_specs


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_PATH = ROOT / "utils" / "lineage_function_hashes.json"


@dataclass(frozen=True)
class FunctionLineage:
    """Resolved source metadata for one referenced function."""

    file_path: str
    file_glob: str
    function_name: str
    start_line: int
    end_line: int
    source_hash: str


def _normalize_source(source: str) -> str:
    return "\n".join(line.rstrip() for line in source.replace("\r\n", "\n").split("\n")).strip() + "\n"


def resolve_code_reference(ref: CodeReference, root: Path = ROOT) -> Path:
    """Resolve a registry file glob to exactly one source file."""

    matches = sorted(root.glob(ref.file_glob))
    if not matches:
        raise FileNotFoundError(f"No file matches lineage reference glob: {ref.file_glob}")
    if len(matches) > 1:
        raise ValueError(f"Lineage reference glob must resolve to one file: {ref.file_glob} -> {matches}")
    return matches[0]


def extract_function_source(path: Path, function_name: str) -> tuple[str, int, int]:
    """Return normalized function source plus start/end lines."""

    text = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(text, filename=str(path))
    lines = text.replace("\r\n", "\n").split("\n")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            if node.end_lineno is None:
                raise ValueError(f"Cannot determine end line for {function_name} in {path}")
            source = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            return _normalize_source(source), int(node.lineno), int(node.end_lineno)

    raise ValueError(f"Function {function_name} not found in {path}")


def function_source_hash(source: str) -> str:
    """Return a short, stable hash for normalized function source."""

    digest = hashlib.sha256(_normalize_source(source).encode("utf-8")).hexdigest()
    return digest[:16]


def build_function_lineage(ref: CodeReference, root: Path = ROOT) -> FunctionLineage:
    """Resolve one code reference into source metadata and source hash."""

    path = resolve_code_reference(ref, root=root)
    source, start_line, end_line = extract_function_source(path, ref.function_name)
    return FunctionLineage(
        file_path=path.relative_to(root).as_posix(),
        file_glob=ref.file_glob,
        function_name=ref.function_name,
        start_line=start_line,
        end_line=end_line,
        source_hash=function_source_hash(source),
    )


def iter_unique_code_references() -> tuple[CodeReference, ...]:
    """Return unique registry code references in stable order."""

    return _unique_code_references(
        ref
        for spec in iter_lineage_specs()
        for ref in spec.calculations
    )


def _unique_code_references(refs: Iterable[CodeReference]) -> tuple[CodeReference, ...]:
    """Return unique code references in stable order."""

    seen = set()
    unique_refs = []
    for ref in refs:
        key = (ref.file_glob, ref.function_name)
        if key not in seen:
            unique_refs.append(ref)
            seen.add(key)
    return tuple(sorted(unique_refs, key=lambda ref: (ref.file_glob, ref.function_name)))


def lineage_reference_signature(root: Path = ROOT) -> tuple[tuple[str, str, int, int], ...]:
    """Return a cheap invalidation signature for referenced source functions."""

    return lineage_reference_signature_for_refs(iter_unique_code_references(), root=root)


def lineage_reference_signature_for_refs(
    refs: Iterable[CodeReference],
    root: Path = ROOT,
) -> tuple[tuple[str, str, int, int], ...]:
    """Return a cheap invalidation signature for selected source functions."""

    parts = []
    for ref in _unique_code_references(refs):
        path = resolve_code_reference(ref, root=root)
        stat = path.stat()
        parts.append((
            path.relative_to(root).as_posix(),
            ref.function_name,
            stat.st_mtime_ns,
            stat.st_size,
        ))
    return tuple(parts)


@lru_cache(maxsize=8)
def _build_function_lineage_index_cached(
    root_str: str,
    signature: tuple[tuple[str, str, int, int], ...],
) -> tuple[tuple[str, FunctionLineage], ...]:
    """Build source metadata for all registry functions."""

    root = Path(root_str)
    _ = signature

    out: dict[str, FunctionLineage] = {}
    for ref in iter_unique_code_references():
        lineage = build_function_lineage(ref, root=root)
        out[f"{lineage.file_path}:{lineage.function_name}"] = lineage
    return tuple(out.items())


def build_function_lineage_index(root: Path = ROOT) -> dict[str, FunctionLineage]:
    """Build source metadata for all registry functions, cached by file signature."""

    root = root.resolve()
    return dict(_build_function_lineage_index_cached(str(root), lineage_reference_signature(root=root)))


@lru_cache(maxsize=128)
def _build_function_lineage_index_for_refs_cached(
    root_str: str,
    refs_key: tuple[tuple[str, str], ...],
    signature: tuple[tuple[str, str, int, int], ...],
) -> tuple[tuple[str, FunctionLineage], ...]:
    """Build source metadata for selected registry functions."""

    root = Path(root_str)
    _ = signature

    out: dict[str, FunctionLineage] = {}
    for file_glob, function_name in refs_key:
        lineage = build_function_lineage(CodeReference(file_glob, function_name), root=root)
        out[f"{lineage.file_path}:{lineage.function_name}"] = lineage
    return tuple(out.items())


def build_function_lineage_index_for_refs(
    refs: Iterable[CodeReference],
    root: Path = ROOT,
) -> dict[str, FunctionLineage]:
    """Build source metadata only for selected registry functions."""

    root = root.resolve()
    unique_refs = _unique_code_references(refs)
    refs_key = tuple((ref.file_glob, ref.function_name) for ref in unique_refs)
    signature = lineage_reference_signature_for_refs(unique_refs, root=root)
    return dict(_build_function_lineage_index_for_refs_cached(str(root), refs_key, signature))


def function_lineage_dataframe(root: Path = ROOT) -> pd.DataFrame:
    """Build a dataframe for export or debugging."""

    rows = [
        {
            "Code-Key": key,
            "Datei": lineage.file_path,
            "Funktion": lineage.function_name,
            "Startzeile": lineage.start_line,
            "Endzeile": lineage.end_line,
            "Source-Hash": lineage.source_hash,
        }
        for key, lineage in build_function_lineage_index(root=root).items()
    ]
    return pd.DataFrame(rows)


def build_hash_baseline(root: Path = ROOT) -> dict[str, str]:
    """Return the current function hash baseline mapping."""

    return {
        key: lineage.source_hash
        for key, lineage in build_function_lineage_index(root=root).items()
    }


def load_hash_baseline(path: Path = DEFAULT_BASELINE_PATH) -> dict[str, str]:
    """Load the committed function hash baseline."""

    return json.loads(path.read_text(encoding="utf-8"))


def compare_hash_baseline(
    *,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    root: Path = ROOT,
) -> pd.DataFrame:
    """Compare committed baseline hashes with current source hashes."""

    expected = load_hash_baseline(baseline_path)
    actual = build_hash_baseline(root=root)
    keys = sorted(set(expected) | set(actual))
    rows = []
    for key in keys:
        expected_hash = expected.get(key, "")
        actual_hash = actual.get(key, "")
        if key not in expected:
            status = "new"
        elif key not in actual:
            status = "missing"
        elif expected_hash != actual_hash:
            status = "changed"
        else:
            status = "ok"
        rows.append(
            {
                "Code-Key": key,
                "Expected-Hash": expected_hash,
                "Actual-Hash": actual_hash,
                "Status": status,
            }
        )
    return pd.DataFrame(rows)


def compare_hash_baseline_for_refs(
    refs: Iterable[CodeReference],
    *,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    root: Path = ROOT,
) -> pd.DataFrame:
    """Compare committed baseline hashes with current source hashes for selected refs."""

    expected = load_hash_baseline(baseline_path)
    actual = {
        key: lineage.source_hash
        for key, lineage in build_function_lineage_index_for_refs(refs, root=root).items()
    }
    keys = sorted(actual)
    rows = []
    for key in keys:
        expected_hash = expected.get(key, "")
        actual_hash = actual.get(key, "")
        if key not in expected:
            status = "new"
        elif expected_hash != actual_hash:
            status = "changed"
        else:
            status = "ok"
        rows.append(
            {
                "Code-Key": key,
                "Expected-Hash": expected_hash,
                "Actual-Hash": actual_hash,
                "Status": status,
            }
        )
    return pd.DataFrame(rows)
