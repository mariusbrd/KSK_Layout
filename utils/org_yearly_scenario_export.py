"""Presentation tables from certified whole-run OE stock and departure samples."""
from __future__ import annotations

import math

import pandas as pd

from utils.monte_carlo_ensemble import _statistics
from utils.monte_carlo_representative_runs import select_representative_runs


def build_scenario_stock_slide(summary: dict, view: dict, ensemble: dict, *, metric: str) -> pd.DataFrame:
    """Stack ensemble statistics and three real seed paths for one stock metric."""
    if metric not in {"MAK", "Köpfe"} or ensemble.get("count", 0) < 2:
        raise ValueError("Ungültiger Mehrlauf für die Bestandsfolienvorlage")
    from utils.org_yearly_view_export import build_stock_slide_table

    count = ensemble["count"]
    level = view["level"]
    groups = set(view["organizations"])
    code = "mak" if metric == "MAK" else "heads"
    reference = build_stock_slide_table(summary, view, metric=metric).set_index("Kennzahl")
    annual = summary["stock_reconciliation"]["annual"]
    annual = annual.loc[annual.Ebene.eq(level) & annual.Organisation.isin(groups)]
    opening_date = pd.Timestamp(summary["metadata"]["base_date"]).normalize()
    dates = {int(year): pd.Timestamp(frame.Stichtag.iloc[0]).date().isoformat()
             for year, frame in annual.loc[annual.Art.eq("Jahresstichtag")].groupby("Jahr")}
    years = sorted({int(year) for year in view["years"]} & set(dates))
    if not years:
        raise ValueError("Keine gemeinsamen Jahresscheiben für die Bestandsfolienvorlage")

    stock_samples = ensemble.get("statistics", {}).get("departure_stock_samples")
    rate_sources = ensemble.get("statistics", {}).get("departure_rate_inputs_by_seed")
    if not isinstance(stock_samples, list) or not isinstance(rate_sources, list) or len(rate_sources) != count:
        raise ValueError("Zertifizierte Bestands- oder Fluktuationswerte fehlen")
    stock = {}
    for date, row_level, group, row_metric, values in stock_samples:
        if row_level != level or row_metric != code:
            continue
        if level == "OE-Cluster" and group in {"Unclustered", "UNMAPPED", "Unmapped"}:
            group = "Nicht zugeordnet"
        if len(values) != count:
            raise ValueError("Unvollständiger Jahresbestand je Lauf")
        key = (str(date), str(group))
        target = stock.setdefault(key, [0.0] * count)
        for index, value in enumerate(values):
            target[index] += float(value)

    opening = float(reference.loc["Verbleibender heutiger Bestand", "Start"])
    yearly_stock = {}
    for year, date in dates.items():
        yearly_stock[year] = ([opening] * count if pd.Timestamp(date) == opening_date else
                              [math.fsum(stock.get((date, group), [0.0] * count)[index]
                                         for group in sorted(groups)) for index in range(count)])
    kinds = ("QUIT", "RETIREMENT", "ATZ_END", "ATZ_AR_TO_FR", "Azubi_Exit")
    annual_losses = {(year, kind): [0.0] * count for year in dates for kind in kinds}
    rate_level = "Organisationseinheit" if level == "Gesamt" else level
    for index, by_level in enumerate(rate_sources):
        source = by_level.get(rate_level) if isinstance(by_level, dict) else None
        if not isinstance(source, dict) or not isinstance(source.get("losses"), list):
            raise ValueError("Berichtswirksame Fluktuation je Lauf fehlt")
        for year, group, kind, heads, mak in source["losses"]:
            if (kind in kinds and (level == "Gesamt" or group in groups)
                    and (int(year), kind) in annual_losses
                    and pd.Timestamp(dates[int(year)]) > opening_date):
                annual_losses[(int(year), kind)][index] += float(mak if code == "mak" else heads)

    def category(*selected_kinds):
        return {year: [math.fsum(annual_losses[(year, kind)][index] for kind in selected_kinds)
                       for index in range(count)] for year in dates}

    annual_quit = category("QUIT")
    direct_retirement = category("RETIREMENT")
    atz_end = category("ATZ_END")
    atz_release = category("ATZ_AR_TO_FR")
    retirement = category("RETIREMENT", "ATZ_END", *(
        ("ATZ_AR_TO_FR",) if metric == "MAK" else ()))
    azubi_exit = category("Azubi_Exit")

    for year in years:
        expected_stock = (opening if pd.Timestamp(dates[year]) == opening_date else
                          float(reference.loc["Verbleibender heutiger Bestand", year]))
        if not math.isclose(yearly_stock[year][0], expected_stock, rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"Jahresbestand {year} weicht vom freigegebenen Referenzlauf ab")
        if not math.isclose(annual_quit[year][0], float(reference.loc[
                "Abgänge durch Fluktuation", year]), rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"Fluktuation {year} weicht vom freigegebenen Referenzlauf ab")
        checks = (
            ("Renteneintritte (inkl. ATZ)", retirement),
            ("Rente (direkt)", direct_retirement),
            ("Rente nach ATZ-Vertragsende", atz_end),
            ("Azubi: Nichtübernahme", azubi_exit),
            ("ATZ-Freistellung", atz_release),
        )
        for label, values in checks:
            if label in reference.index and not math.isclose(
                    values[year][0], float(reference.loc[label, year]), rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"{label} {year} weicht vom freigegebenen Referenzlauf ab")

    decline = {year: [
        (yearly_stock.get(year - 1, [opening] * count)[index] - yearly_stock[year][index])
        for index in range(count)] for year in years}
    cumulative = {year: [math.fsum(annual_quit[previous][index]
                                    for previous in sorted(annual_quit) if previous <= year)
                         for index in range(count)] for year in years}
    series = (
        ("Bestand", "Verbleibender heutiger Bestand", yearly_stock, opening),
        ("Bestand", "Rückgang gegenüber Vorjahresstichtag", decline, 0.0),
        ("Fluktuation", "Abgänge durch Fluktuation", annual_quit, 0.0),
        ("Fluktuation", "Kumulierte Fluktuationsverluste", cumulative, 0.0),
        ("Renteneintritte (inkl. ATZ)", "Renteneintritte (inkl. ATZ)", retirement, 0.0),
        ("Renteneintritte (inkl. ATZ)", "Rente (direkt)", direct_retirement, 0.0),
        ("Renteneintritte (inkl. ATZ)", "Rente nach ATZ-Vertragsende", atz_end, 0.0),
        ("Weitere Abgänge", "Azubi: Nichtübernahme", azubi_exit, 0.0),
        ("Kapazitätsänderungen", "ATZ-Freistellung", atz_release, 0.0),
    )
    selections = select_representative_runs(ensemble)
    rows = []

    def add_block(label: str, index: int | None):
        if rows:
            rows.append({"Szenario": None})
        pair = ensemble["seed_pairs"][index] if index is not None else (None, None)
        for section, name, values, start in series:
            bands = ("Mittelwert", "P10", "P90") if index is None else ("Laufwert",)
            for band in bands:
                annual_values = {}
                for year in years:
                    if index is None:
                        stats = _statistics(values[year], ensemble["band_level"])
                        annual_values[year] = stats[{
                            "Mittelwert": "mean", "P10": "lower", "P90": "upper"}[band]]
                    else:
                        annual_values[year] = values[year][index]
                rows.append({"Szenario": label, "Ausprägung": band,
                             "Laufindex": index + 1 if index is not None else None,
                             "Seed Abgänge": pair[0], "Seed Zugänge": pair[1],
                             "Bereich": section, "Kennzahl": name, "Start": start,
                             **annual_values})

    add_block("Mittelwert aller Läufe", None)
    for label, key in (("Fluktuation max", "worst"),
                       ("Fluktuation Mean: typischer Einzellauf", "typical"),
                       ("Fluktuation min", "best")):
        add_block(label, selections[key]["index"])
    return pd.DataFrame(rows)
