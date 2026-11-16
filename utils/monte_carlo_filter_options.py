"""Sidebar choices present in any saved Monte Carlo annual stock run."""
from __future__ import annotations


def filter_options_from_ensemble(ensemble: dict | None) -> dict[str, list[str]]:
    if ensemble is None:
        return {}
    rows = ensemble.get("statistics", {}).get("annual_stock_samples") or []
    options = {"Organisationseinheit": set(), "Jobfamily": set()}
    for row in rows:
        if len(row) != 5:
            raise ValueError("Ungültige Jahresbestandswerte für Filteroptionen")
        _, level, group, metric, samples = row
        if level in options and metric == "heads" and any(float(value) > 0 for value in samples):
            label = str(group).strip()
            if label and label != "UNMAPPED":
                options[level].add(label)
    return {level: sorted(values) for level, values in options.items()}
