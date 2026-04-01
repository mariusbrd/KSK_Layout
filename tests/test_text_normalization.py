from __future__ import annotations

from pathlib import Path

import pytest

from components.sidebar import normalize_global_metric_view
from utils.text_normalization import normalize_dashboard_text


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("K?pfe", "Köpfe"),
        ("KÃ¶pfe", "Köpfe"),
        ("KÃƒÂ¶pfe", "Köpfe"),
        ("Abg?nge", "Abgänge"),
        ("AbgÃ¤nge", "Abgänge"),
        ("Zug?nge", "Zugänge"),
        ("ZugÃ¤nge", "Zugänge"),
        ("Besch?ftigungsstatus", "Beschäftigungsstatus"),
        ("BeschÃ¤ftigung", "Beschäftigung"),
        ("Verg?tung", "Vergütung"),
        ("VergÃ¼tung", "Vergütung"),
        ("Erf?llungsgrad", "Erfüllungsgrad"),
        ("ErfÃ¼llungsgrad", "Erfüllungsgrad"),
    ],
)
def test_normalize_dashboard_text_repairs_critical_terms(raw_text, expected):
    assert normalize_dashboard_text(raw_text) == expected


@pytest.mark.parametrize(
    "canonical_text",
    [
        "Köpfe",
        "MAK",
        "EUR",
        "Abgänge",
        "Zugänge",
        "Beschäftigung",
        "Beschäftigungsstatus",
        "Vergütung",
        "Erfüllungsgrad",
    ],
)
def test_normalize_dashboard_text_preserves_canonical_terms(canonical_text):
    assert normalize_dashboard_text(canonical_text) == canonical_text


@pytest.mark.parametrize(
    ("raw_metric", "expected"),
    [
        ("Köpfe", "Köpfe"),
        ("K?pfe", "Köpfe"),
        ("KÃ¶pfe", "Köpfe"),
        ("Koepfe", "Köpfe"),
        ("MAK", "MAK"),
        ("EUR", "EUR"),
        ("Euro", "EUR"),
    ],
)
def test_normalize_global_metric_view_canonicalizes_sidebar_values(raw_metric, expected):
    assert normalize_global_metric_view(raw_metric) == expected


def test_dashboard_source_files_are_text_clean():
    root = Path(__file__).resolve().parents[1]
    source_files = [
        root / "app.py",
        *sorted((root / "components").glob("*.py")),
        *sorted((root / "pages").glob("*.py")),
        *sorted((root / "utils").glob("*.py")),
    ]
    excluded = {root / "utils" / "text_normalization.py"}

    for path in source_files:
        if path in excluded:
            continue
        text = path.read_text(encoding="utf-8")
        assert "�" not in text, path.as_posix()
        changed_lines = [
            line_no
            for line_no, line in enumerate(text.splitlines(), start=1)
            if normalize_dashboard_text(line) != line
        ]
        assert not changed_lines, f"{path.as_posix()} :: {changed_lines[:10]}"
