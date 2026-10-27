"""Export a certified job-family stock selection without rerunning the forecast."""
from __future__ import annotations

from io import BytesIO

import pandas as pd

from components.org_simulation_yearly import LABELS
from utils.org_simulation_events import DEPARTURES


def _slide_table(stock: pd.DataFrame, events: pd.DataFrame, *, dimension: str,
                 groups: list[str], years: list[int], metric: str) -> pd.DataFrame:
    scoped = stock.loc[stock[dimension].isin(groups) & stock.Kennzahl.eq(metric)]
    opening = float(scoped.loc[scoped.Art.eq("Ausgangsstichtag"), "Bestand"].sum())
    annual = scoped.loc[scoped.Art.eq("Jahresstichtag")].groupby(scoped.Stichtag.dt.year).Bestand.sum()
    scoped_events = events.loc[events[dimension].isin(groups) & events.Jahr.isin(years)]
    change = f"{metric}-Veränderung"
    losses = (-scoped_events.groupby(["Jahr", "Ereignisart"])[change].sum()
              if not scoped_events.empty else pd.Series(dtype=float))

    def loss(year: int, *kinds: str) -> float:
        return float(sum(losses.get((year, kind), 0.0) for kind in kinds))

    def row(section: str, label: str, values: dict[int, float], start: float = 0.0) -> dict:
        return {"Bereich": section, "Kennzahl": label, "Start": start,
                **{year: values.get(year, 0.0) for year in years}}

    stocks = {year: float(annual.get(year, 0.0)) for year in years}
    rows = [row("Bestand", "Verbleibender heutiger Bestand", stocks, opening)]
    rows.append(row("Bestand", "Rückgang gegenüber Vorjahresstichtag",
                    {year: float(annual.get(year - 1, opening)) - stocks[year] for year in years}))
    retirement = ("RETIREMENT", "ATZ_END") if metric == "Köpfe" else ("RETIREMENT", "ATZ_END", "ATZ_AR_TO_FR")
    categories = [
        ("Fluktuation", "Abgänge durch Fluktuation", ("QUIT",)),
        ("Renteneintritte (inkl. ATZ)", "Renteneintritte (inkl. ATZ)", retirement),
        ("Renteneintritte (inkl. ATZ)", "Rente (direkt)", ("RETIREMENT",)),
        ("Renteneintritte (inkl. ATZ)", "Rente nach ATZ-Vertragsende", ("ATZ_END",)),
        ("Weitere Abgänge", LABELS.get("Azubi_Exit", "Azubi: Nichtübernahme"), ("Azubi_Exit",)),
        ("Kapazitätsänderungen", "ATZ-Freistellung", ("ATZ_AR_TO_FR",)),
        ("Kapazitätsänderungen", "Ruhend: Beginn", ("RUHEND_START",)),
        ("Kapazitätsänderungen", "Ruhend: Rückkehr (negativer Verlust)", ("RUHEND_RETURN",)),
    ]
    rows.extend(row(section, label, {year: loss(year, *kinds) for year in years})
                for section, label, kinds in categories)
    return pd.DataFrame(rows)


def build_jobfamily_stock_export_tables(view: dict, *, dimension: str, groups: list[str],
                                        years: list[int], metric: str,
                                        event_types: list[str]) -> dict[str, pd.DataFrame]:
    """Keep the visible selection separate from full-run audit and original events."""
    if dimension not in ("Jobfamily", "JF-Cluster") or dimension != view.get("dimension") or metric not in ("Köpfe", "MAK"):
        raise ValueError("Ungültige Bestandsauswahl.")
    stock = view["stock"]
    if stock.empty or view["stock_checks"].empty or not view["stock_checks"]["Abgestimmt"].all():
        raise ValueError("Der Jobgruppen-Bestandsverlauf ist nicht abgestimmt.")
    years = sorted(set(int(year) for year in years))
    groups = list(dict.fromkeys(groups))
    if not years or not groups:
        raise ValueError("Bitte mindestens ein Jahr und eine Jobgruppe auswählen.")
    rows = stock.loc[stock[dimension].isin(groups) &
                     (stock.Art.eq("Ausgangsstichtag") |
                      (stock.Art.eq("Jahresstichtag") & stock.Stichtag.dt.year.isin(years)))].copy()
    chart = rows.loc[rows.Kennzahl.eq(metric)].drop_duplicates([dimension, "Stichtag"], keep="last")
    chart = chart[["Stichtag", dimension, "Bestand"]].copy()
    if chart.empty:
        raise ValueError("Für die Auswahl sind keine Bestandswerte verfügbar.")
    matrix = chart.pivot(index=dimension, columns="Stichtag", values="Bestand")
    reported = view["reporting_events"]
    reported = reported.loc[reported[dimension].isin(groups) & reported.Jahr.isin(years)]
    departures = reported.loc[reported.Ereignisart.isin(DEPARTURES)]
    heads = departures.groupby(dimension)["Abgänge"].sum() if not departures.empty else pd.Series(dtype=float)
    mak = (-departures["MAK-Veränderung"].clip(upper=0)).groupby(departures[dimension]).sum() if not departures.empty else pd.Series(dtype=float)
    totals = matrix.copy()
    totals.insert(0, "Abgehende MAK", mak.reindex(matrix.index, fill_value=0))
    totals.insert(0, "Abgehende Köpfe", heads.reindex(matrix.index, fill_value=0))
    totals.insert(0, f"Bestand letzter Stichtag ({metric})", matrix.iloc[:, -1])

    original = view["annual"]
    original = original.loc[original.Jahr.isin(years) & original[dimension].isin(groups) &
                            original.Ereignisart.isin(event_types)].copy()
    tables = {
        "Bestand_Folienvorlage_MAK": _slide_table(stock, view["reporting_events"], dimension=dimension,
                                                   groups=groups, years=years, metric="MAK"),
        "Bestand_Folienvorlage_Koepfe": _slide_table(stock, view["reporting_events"], dimension=dimension,
                                                      groups=groups, years=years, metric="Köpfe"),
        "Bestand_Auswahl_Grafik": chart,
        "Bestand_Auswahl_Matrix": matrix.reset_index(),
        "Bestand_Auswahl_Summen": totals.reset_index(),
        "Bestand_Auswahl_Jahre": rows,
        "Abgang_Auswahl_Ereignisse": original,
        "Bestand_Vollstaendig": stock.copy(),
        "Bestand_Endwertabgleich": view["stock_checks"].copy(),
        "Bestand_Reportingereignisse": view["reporting_events"].copy(),
        "Jahres_Ereignisbasis": view["events"].copy(),
    }
    for kind in event_types:
        if kind not in set(original.Ereignisart):
            continue
        selected = original.loc[original.Ereignisart.eq(kind)]
        tables[f"Abgang_{kind}"] = selected.pivot_table(
            index=dimension, columns="Jahr", values=f"{metric}-Veränderung", aggfunc="sum", fill_value=0).reset_index()
    return tables


def build_jobfamily_stock_export_bytes(view: dict, *, dimension: str, groups: list[str],
                                       years: list[int], metric: str,
                                       event_types: list[str]) -> bytes:
    tables = build_jobfamily_stock_export_tables(
        view, dimension=dimension, groups=groups, years=years,
        metric=metric, event_types=event_types)
    context = pd.DataFrame([
        {"Feld": "Laufkennung", "Wert": view["run_signature"]},
        {"Feld": "Ebene", "Wert": dimension},
        {"Feld": "Kennzahl der Grafik", "Wert": metric},
        {"Feld": "Jahre", "Wert": ", ".join(map(str, sorted(years)))},
        {"Feld": "Jobgruppen", "Wert": " | ".join(groups)},
        {"Feld": "Abgangsarten", "Wert": ", ".join(event_types)},
        {"Feld": "Bestandsabgleich", "Wert": "Abgestimmt"},
        {"Feld": "Hinweis", "Wert": "Bestand aus gespeicherter Jobgruppenprüfung; Abgangsmatrizen enthalten originale, vorzeichenbehaftete Ereignisse. Technische und Reportingeffekte stehen im abgestimmten Bestand. Personen mit mehreren Jobgruppen können in Gruppensummen mehrfach zählen."},
    ])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, frame in tables.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
        context.to_excel(writer, sheet_name="Kontext", index=False)
    return output.getvalue()
