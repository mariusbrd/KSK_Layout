"""Excel export of certified, unfiltered Monte Carlo statistics only."""
from __future__ import annotations

from io import BytesIO
from collections.abc import Mapping

import pandas as pd


def _safe_text(value: object) -> str:
    """Keep source group labels as text, including Excel formula prefixes."""
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _stats_row(values: Mapping) -> dict[str, float]:
    if not isinstance(values, Mapping) or set(values) != {"mean", "lower", "upper"}:
        raise ValueError("Incomplete Monte Carlo statistics")
    return {
        "Mittelwert": float(values["mean"]),
        "P10": float(values["lower"]),
        "P90": float(values["upper"]),
    }


def build_monte_carlo_export_bytes(*, ensemble: Mapping, base_date: object,
                                   target_date: object) -> bytes:
    """Export the complete ensemble contract without person or position rows."""
    stats = ensemble.get("statistics")
    count = ensemble.get("count")
    seed_pairs = tuple(map(tuple, ensemble.get("seed_pairs", ())))
    if (ensemble.get("status") != "complete" or not isinstance(stats, Mapping)
            or stats.get("status") != "complete" or not isinstance(count, int)
            or count < 2 or len(seed_pairs) != count
            or stats.get("target") is None
            or stats.get("annual_values") is None
            or stats.get("annual_stock_values") is None
            or stats.get("band_kind") != "empirical_quantile"
            or abs(float(stats.get("band_level", 0)) - 0.8) > 1e-9):
        raise ValueError("Only a complete certified 80-percent ensemble can be exported")

    target_rows = [
        {"Kennzahl": label, "Einheit": unit, **_stats_row(stats["target"][metric])}
        for metric, label, unit in (("heads", "Köpfe", "Personen"), ("mak", "MAK", "MAK"))
    ]
    annual_rows = [
        {"Jahr": year, "Ebene": _safe_text(level), "Gruppe": _safe_text(group),
         "Kategorie": _safe_text(category), "Ereignis": _safe_text(event_type),
         "Kennzahl": metric, **_stats_row(values)}
        for year, level, group, category, event_type, metric, values
        in stats["annual_values"]
    ]
    stock_rows = [
        {"Stichtag": date, "Ebene": _safe_text(level), "Gruppe": _safe_text(group),
         "Kennzahl": metric, **_stats_row(values)}
        for date, level, group, metric, values in stats["annual_stock_values"]
    ]
    metadata = [
        ("Exporttyp", "Monte-Carlo-Kennzahlen, ungefilterter Gesamtbestand"),
        ("Basis-Stichtag", str(pd.Timestamp(base_date).date())),
        ("Ziel-Stichtag", str(pd.Timestamp(target_date).date())),
        ("Anzahl Läufe", count),
        ("Band", "Empirischer 80-%-Ergebnisbereich (P10–P90)"),
        ("Interpretation", "Streuung der Einzelläufe, kein Konfidenzintervall des Mittelwerts"),
        ("Abgangs-Startseed", seed_pairs[0][0]),
        ("Zugangs-Startseed", seed_pairs[0][1]),
        ("Laufplan-Signatur", ensemble.get("plan_signature", "")),
        ("Eingabefingerprint", ensemble.get("input_fingerprint", "")),
        ("Referenzsignatur", ensemble.get("reference_signature", "")),
        ("Referenzinstanz", ensemble.get("reference_instance", "")),
    ]
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(metadata, columns=["Merkmal", "Wert"]).to_excel(writer, sheet_name="Metadaten", index=False)
        pd.DataFrame(target_rows).to_excel(writer, sheet_name="Zielwerte", index=False)
        pd.DataFrame(annual_rows, columns=["Jahr", "Ebene", "Gruppe", "Kategorie", "Ereignis", "Kennzahl", "Mittelwert", "P10", "P90"]).to_excel(writer, sheet_name="Jahresereignisse", index=False)
        pd.DataFrame(stock_rows, columns=["Stichtag", "Ebene", "Gruppe", "Kennzahl", "Mittelwert", "P10", "P90"]).to_excel(writer, sheet_name="Jahresbestaende", index=False)
        pd.DataFrame(
            [(index, departure, accession) for index, (departure, accession) in enumerate(seed_pairs)],
            columns=["Laufindex", "Abgangsseed", "Zugangsseed"],
        ).to_excel(writer, sheet_name="Seedpaare", index=False)
    return output.getvalue()
