"""The statistics workbook must match the certified ensemble and contain no people."""
from io import BytesIO

from openpyxl import load_workbook
import pytest

from utils.monte_carlo_export import build_monte_carlo_export_bytes


def _ensemble():
    return {
        "status": "complete", "count": 2, "seed_pairs": ((42, 17), (43, 18)),
        "plan_signature": "plan-123", "input_fingerprint": "input-123",
        "reference_signature": "reference-123", "reference_instance": 1,
        "statistics": {
            "status": "complete", "band_kind": "empirical_quantile", "band_level": 0.8,
            "target": {
                "heads": {"mean": 10.5, "lower": 10.1, "upper": 10.9},
                "mak": {"mean": 8.25, "lower": 8.05, "upper": 8.45},
            },
            "annual_values": [
                (2027, "org_unit", "=1+1", "Zugänge", "Hire", "mak",
                 {"mean": 1.5, "lower": 1.1, "upper": 1.9}),
            ],
            "annual_stock_values": [
                ("2027-06-15", "org_unit", "Service", "heads",
                 {"mean": 5.5, "lower": 5.1, "upper": 5.9}),
            ],
        },
    }


def test_workbook_has_matching_statistics_seeds_and_no_person_rows():
    content = build_monte_carlo_export_bytes(
        ensemble=_ensemble(), base_date="2026-09-01", target_date="2027-06-15")
    book = load_workbook(BytesIO(content), read_only=True, data_only=False)
    assert book.sheetnames == ["Metadaten", "Zielwerte", "Jahresereignisse", "Jahresbestaende", "Seedpaare"]
    target = list(book["Zielwerte"].values)
    assert target[1] == ("Köpfe", "Personen", 10.5, 10.1, 10.9)
    assert target[2] == ("MAK", "MAK", 8.25, 8.05, 8.45)
    assert list(book["Seedpaare"].values)[1:] == [(0, 42, 17), (1, 43, 18)]
    event = list(book["Jahresereignisse"].values)[1]
    assert event[2] == "'=1+1"
    assert event[-3:] == (1.5, 1.1, 1.9)
    assert book["Jahresereignisse"]["C2"].data_type != "f"
    assert list(book["Jahresbestaende"].values)[1][-3:] == (5.5, 5.1, 5.9)
    metadata = dict(list(book["Metadaten"].values)[1:])
    assert metadata["Ziel-Stichtag"] == "2027-06-15"
    assert metadata["Anzahl Läufe"] == 2
    assert "kein Konfidenzintervall" in metadata["Interpretation"]


def test_incomplete_ensemble_cannot_be_exported():
    ensemble = _ensemble()
    ensemble["status"] = "incomplete"
    with pytest.raises(ValueError, match="complete certified"):
        build_monte_carlo_export_bytes(
            ensemble=ensemble, base_date="2026-09-01", target_date="2027-06-15")
