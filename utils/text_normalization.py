"""Helpers for repairing mojibake and normalizing dashboard UI text."""

from __future__ import annotations

import unicodedata


_SUSPICIOUS_MOJIBAKE_MARKERS = (
    "\u00c3\u0192",
    "\u00c3\u201a",
    "\u00c3\u00a2\u201a\u00ac",
    "\u00c3\u00af\u00c2\u00bf\u00c2\u00bd",
    "\u00c3",
    "\u00e2",
    "\ufffd",
)
_QUESTION_MARK = "?"
_CANONICAL_REPLACEMENTS = (
    ("gem??", "gemäß"),
    (f"K{_QUESTION_MARK}pfe", "Köpfe"),
    (f"Abg{_QUESTION_MARK}nge", "Abgänge"),
    (f"Zug{_QUESTION_MARK}nge", "Zugänge"),
    ("Erf?llungsgrad", "Erfüllungsgrad"),
    ("Mitarbeiterkapazit?t", "Mitarbeiterkapazität"),
    ("FTE-?quivalent", "MAK-Äquivalent"),
    ("Sollkapazit?t", "Sollkapazität"),
    ("Besch?ftigung", "Beschäftigung"),
    ("Besch?ftigungs", "Beschäftigungs"),
    ("Verg?tung", "Vergütung"),
    ("K?rzel", "Kürzel"),
    ("TV?D", "TVöD"),
    ("Betriebszugeh?rigkeit", "Betriebszugehörigkeit"),
    ("Unternehmenszugeh?rigkeit", "Unternehmenszugehörigkeit"),
    ("Spalten-Erkl?rungen", "Spalten-Erklärungen"),
    ("SPALTEN-ERKL?RUNG", "SPALTEN-ERKLÄRUNG"),
    ("tats?chlicher", "tatsächlicher"),
    ("urspr?nglichen", "ursprünglichen"),
    ("schlie?en", "schließen"),
    ("Budget-?berschreitung", "Budget-Überschreitung"),
    ("Kapazit?t", "Kapazität"),
    ("Pr?fen", "Prüfen"),
    ("pr?fen", "prüfen"),
    ("gel?scht", "gelöscht"),
    ("f?r", "für"),
    ("m?chten", "möchten"),
    ("Zus?tzlich", "Zusätzlich"),
    ("zus?tzlich", "zusätzlich"),
    ("regul?ren", "regulären"),
    ("verf?gbar", "verfügbar"),
    ("einf?gen", "einfügen"),
    ("f?hren", "führen"),
    ("Seitenumbr?chen", "Seitenumbrüchen"),
    ("Mio. ?", "Mio. €"),
)


def normalize_display_text(text: str | None, *, max_passes: int = 6) -> str | None:
    """
    Normalize UI text to NFC and repair common multi-encoded UTF-8 mojibake.

    The function is intentionally conservative:
    - it only attempts cp1252 -> utf-8 repair when known mojibake markers exist
    - it stops as soon as the text stabilizes or no suspicious markers remain
    """
    if text is None or not isinstance(text, str):
        return text

    normalized = unicodedata.normalize("NFC", text)

    if any(marker in normalized for marker in _SUSPICIOUS_MOJIBAKE_MARKERS):
        for _ in range(max_passes):
            if not any(marker in normalized for marker in _SUSPICIOUS_MOJIBAKE_MARKERS):
                break
            try:
                repaired = normalized.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            if repaired == normalized:
                break
            normalized = repaired

    normalized = unicodedata.normalize("NFC", normalized)
    for broken, clean in _CANONICAL_REPLACEMENTS:
        normalized = normalized.replace(broken, clean)
    return normalized


def normalize_dashboard_text(text: str | None) -> str | None:
    """Normalize dashboard-facing text and logic-relevant UI labels."""
    return normalize_display_text(text)
