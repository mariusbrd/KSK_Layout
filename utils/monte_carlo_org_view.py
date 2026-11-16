"""Certified annual OE bands for a saved, approved reference simulation."""
from __future__ import annotations

from collections.abc import Mapping
import math

import pandas as pd

from utils.monte_carlo_ensemble import _quantile, _statistics


def _distribution(values):
    numeric = [float(value) for value in values]
    return {"p25": _quantile(numeric, .25), "median": _quantile(numeric, .5),
            "p75": _quantile(numeric, .75)}


def approved_org_ensemble(state: Mapping, view) -> dict | None:
    """Never attach an original-run distribution to an adjusted result."""
    saved = state.get("monte_carlo_ensemble_result")
    if (not isinstance(saved, dict) or saved.get("status") != "complete"
            or saved.get("count", 0) < 2
            or saved.get("reference_signature") != view.signature
            or saved.get("reference_instance") != view.instance
            or view.version != f"{view.signature}::{view.instance}::original"
            or not view.approved):
        return None
    statistics = saved.get("statistics")
    if (not isinstance(statistics, dict) or statistics.get("status") != "complete"
            or statistics.get("requested_count") != saved["count"]
            or statistics.get("annual_event_samples") is None
            or statistics.get("annual_stock_samples") is None):
        return None
    return saved


def stock_band_table(chart: pd.DataFrame, *, ensemble: Mapping, level: str,
                     metric: str, base_date=None) -> pd.DataFrame:
    """Match each displayed reference bar to the same annual group and date."""
    stats = ensemble["statistics"]
    stock_samples = stats.get("departure_stock_samples")
    if stock_samples is None:
        raise ValueError("Für die Abgangsüberleitung fehlen Laufwerte; bitte die Monte-Carlo-Simulation neu berechnen")
    samples = {}
    for date, record_level, group, record_metric, values in stock_samples:
        # The stock worker labels unmapped cluster rows "Unclustered";
        # the approved OE bridge displays the same group as "Nicht zugeordnet".
        if record_level == "OE-Cluster" and group in {"Unclustered", "UNMAPPED", "Unmapped"}:
            group = "Nicht zugeordnet"
        key = (date, record_level, group, record_metric)
        if key in samples:
            samples[key] = [left + right for left, right in zip(samples[key], values)]
        else:
            samples[key] = values
    rows = []
    run_samples = {}
    opening_date = pd.Timestamp(base_date).date().isoformat() if base_date is not None else None
    for _, row in chart.iterrows():
        date = pd.Timestamp(row["Stichtag"]).date().isoformat()
        group = str(row["Organisation"])
        key = (date, level, group, "heads" if metric == "Köpfe" else "mak")
        reference = float(row[f"Bestand-{metric}"])
        if key in samples:
            values = samples[key]
        elif date == opening_date:
            # The opening stock is common to every seed and need not be sent
            # as an annual worker record.
            values = [reference] * ensemble["count"]
        else:
            values = [0.0] * ensemble["count"]
        if len(values) != ensemble["count"]:
            raise ValueError("Unvollständige OE-Jahresbestandswerte")
        expected = round(float(values[0]), 2) if metric == "MAK" else float(values[0])
        if not math.isclose(reference, expected, rel_tol=0, abs_tol=1e-6):
            raise ValueError(
                f"OE-Jahresbestand weicht vom Referenzlauf ab: {date}, {level}, "
                f"{group}, {metric}: sichtbar={reference}, Seed 0={expected}")
        rows.append({"Stichtag": date, "Organisation": group,
                     "Referenzlauf": reference,
                     **_statistics(values, ensemble["band_level"]),
                     **_distribution(values)})
        run_samples[(date, group)] = list(values)
    result = pd.DataFrame(rows)
    result.attrs["run_samples"] = run_samples
    return result


def event_band_table(view: Mapping, *, ensemble: Mapping) -> pd.DataFrame:
    """Sum selected event types within each seed before calculating quantiles."""
    stats = ensemble["statistics"]
    dimension, metric = view["dimension"], view["metric"]
    selected = set(view["event_types"])
    lookup = {}
    for year, level, group, event_type, record_metric, values in stats["annual_event_samples"]:
        if level == dimension and record_metric == metric and event_type in selected:
            key = (int(year), str(group))
            previous = lookup.setdefault(key, [0.0] * ensemble["count"])
            if len(values) != ensemble["count"]:
                raise ValueError("Unvollständige OE-Jahresereignisse")
            for index, value in enumerate(values):
                previous[index] += value
    rows = []
    run_samples = {}
    for year, group in view["chart"][["Jahr", dimension]].itertuples(index=False, name=None):
        values = lookup.get((int(year), str(group)), [0.0] * ensemble["count"])
        reference = view["chart"].loc[
            view["chart"]["Jahr"].eq(year) & view["chart"][dimension].eq(group), metric].iloc[0]
        if not math.isclose(float(reference), float(values[0]), rel_tol=0, abs_tol=1e-6):
            raise ValueError("OE-Jahresereignisse weichen vom Referenzlauf ab")
        rows.append({"Jahr": int(year), dimension: str(group),
                     "Referenzlauf": float(reference),
                     **_statistics(values, ensemble["band_level"]),
                     **_distribution(values)})
        run_samples[(int(year), str(group))] = list(values)
    result = pd.DataFrame(rows)
    result.attrs["run_samples"] = run_samples
    return result


def event_detail_band_table(view: Mapping, *, ensemble: Mapping) -> pd.DataFrame:
    """Selected annual event types and all four metrics, with zero-filled seeds."""
    from utils.org_simulation_events import METRICS

    count = ensemble["count"]
    dimension = view["dimension"]
    years = sorted(int(year) for year in view["periods"]["Jahr"])
    groups = list(view["table"].index.astype(str))
    types = list(view["event_types"])
    selected = {(year, group, kind) for year in years for group in groups for kind in types}
    samples = {}
    for year, level, group, kind, metric, values in ensemble["statistics"]["annual_event_samples"]:
        key = (int(year), str(group), str(kind))
        if level != dimension or key not in selected or metric not in METRICS:
            continue
        if len(values) != count:
            raise ValueError("Unvollständige OE-Jahresereignisse")
        target = samples.setdefault((*key, metric), [0.0] * count)
        for index, value in enumerate(values):
            target[index] += float(value)
    reference = view["details"].groupby(
        ["Jahr", dimension, "Ereignisart"], observed=True)[list(METRICS)].sum()
    rows = []
    for year in years:
        for group in groups:
            for kind in types:
                key = (year, group, kind)
                record = {"Jahr": year, dimension: group, "Ereignisart": kind}
                for metric in METRICS:
                    values = samples.get((*key, metric), [0.0] * count)
                    seed_zero = float(reference.get(metric, pd.Series(dtype=float)).get(key, 0.0))
                    if not math.isclose(seed_zero, values[0], rel_tol=0, abs_tol=1e-6):
                        raise ValueError(f"OE-Jahresereignisse weichen vom Referenzlauf ab: {key}, {metric}")
                    stats = _statistics(values, ensemble["band_level"])
                    record[metric] = stats["mean"]
                    record[f"{metric} P10"] = stats["lower"]
                    record[f"{metric} P90"] = stats["upper"]
                rows.append(record)
    return pd.DataFrame(rows)


def event_summary_band_table(view: Mapping, details: pd.DataFrame, *, ensemble: Mapping) -> pd.DataFrame:
    """Sum each seed across selected years before calculating summary bands."""
    from utils.org_simulation_events import DEPARTURES

    dimension = view["dimension"]
    metric = view["metric"]
    years = [int(year) for year in view["periods"]["Jahr"]]
    groups = list(view["table"].index.astype(str))
    types = set(view["event_types"])
    count = ensemble["count"]
    lookup = {}
    for year, level, group, kind, record_metric, values in ensemble["statistics"]["annual_event_samples"]:
        if (level != dimension or int(year) not in years or str(group) not in groups
                or kind not in types or record_metric not in {metric, "Abgänge", "MAK-Veränderung"}):
            continue
        if len(values) != count:
            raise ValueError("Unvollständige OE-Jahresereignisse")
        lookup[(int(year), str(group), kind, record_metric)] = [float(value) for value in values]

    def samples(group, record_metric, *, year=None, departures_only=False):
        result = [0.0] * count
        for (record_year, record_group, kind, code), values in lookup.items():
            if (record_group == group and code == record_metric
                    and (year is None or year == record_year)
                    and (not departures_only or kind in DEPARTURES)):
                for index, value in enumerate(values):
                    result[index] += value
        return result

    reference = view["summary_table"]
    rows = []
    for group in groups:
        row = {dimension: group}
        for year in years:
            values = samples(group, metric, year=year)
            stats = _statistics(values, ensemble["band_level"])
            visible = float(details.loc[
                details["Jahr"].eq(year) & details[dimension].eq(group), metric].sum())
            if not math.isclose(visible, stats["mean"], rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"OE-Jahresdetails und Jahressumme weichen ab: {year}, {group}")
            row[year] = stats["mean"]
            row[f"{year} P10"] = stats["lower"]
            row[f"{year} P90"] = stats["upper"]
        all_values = samples(group, metric)
        head_values = samples(group, "Abgänge", departures_only=True)
        mak_values = [-value for value in samples(group, "MAK-Veränderung", departures_only=True)]
        for name, values in (("Summe ausgewählte Kennzahl", all_values),
                             ("Abgehende Köpfe", head_values), ("Abgehende MAK", mak_values)):
            expected = float(reference.loc[group, name])
            if not math.isclose(expected, values[0], rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"OE-Jahressumme weicht vom Referenzlauf ab: {group}, {name}")
            stats = _statistics(values, ensemble["band_level"])
            row[name] = stats["mean"]
            row[f"{name} P10"] = stats["lower"]
            row[f"{name} P90"] = stats["upper"]
        rows.append(row)
    return pd.DataFrame(rows).set_index(dimension)


def mean_stacked_event_chart(view: Mapping, *, ensemble: Mapping) -> pd.DataFrame:
    """Calculate each visible stack segment from the same per-seed event values."""
    dimension, metric = view["dimension"], view["metric"]
    years = set(view["periods"]["Jahr"].astype(int))
    groups = set(view["table"].index.astype(str))
    event_types = set(view["event_types"])
    rows = []
    for year, level, group, event_type, record_metric, values in ensemble["statistics"]["annual_event_samples"]:
        if (level == dimension and record_metric == metric and int(year) in years
                and str(group) in groups and event_type in event_types):
            if len(values) != ensemble["count"]:
                raise ValueError("Unvollständige OE-Jahresereignisse")
            rows.append({"Jahr": int(year), dimension: str(group),
                         "Ereignisart": event_type,
                         metric: _statistics(values, ensemble["band_level"])["mean"]})
    index = pd.MultiIndex.from_product(
        [sorted(years), sorted(groups), sorted(event_types)],
        names=["Jahr", dimension, "Ereignisart"])
    if not rows:
        return index.to_frame(index=False).assign(**{metric: 0.0})
    return (pd.DataFrame(rows).set_index(["Jahr", dimension, "Ereignisart"])[metric]
            .reindex(index, fill_value=0.0).reset_index())


def annual_headline_statistics(summary: Mapping, *, ensemble: Mapping,
                               dimension: str, organizations: list[str],
                               end_year: int, metric: str) -> dict | None:
    """Recompute each displayed OE headline within every seed before quantiles."""
    from utils.org_simulation_events import DEPARTURES

    sources = ensemble["statistics"].get("departure_rate_inputs_by_seed")
    if not sources or len(sources) != ensemble["count"]:
        return None
    code = "heads" if metric == "Köpfe" else "mak"
    retirement_types = {"RETIREMENT", "ATZ_END"}
    if metric == "MAK":
        retirement_types.add("ATZ_AR_TO_FR")
    target_date = pd.Timestamp(summary["metadata"]["target_date"])
    end_date = min(pd.Timestamp(end_year, 12, 31), target_date).date().isoformat()
    selected = set(organizations)
    departure_samples = ensemble["statistics"].get("departure_stock_samples")
    if departure_samples is None:
        return None
    stock = {}
    for date, level, group, record_metric, values in departure_samples:
        if level == dimension and record_metric == code and date == end_date:
            if dimension == "OE-Cluster" and group in {"Unclustered", "UNMAPPED", "Unmapped"}:
                group = "Nicht zugeordnet"
            previous = stock.setdefault(group, [0.0] * ensemble["count"])
            for index, value in enumerate(values):
                previous[index] += value
    per_seed = {name: [] for name in
                ("initial", "final", "retirement_atz", "quit", "departures", "departure_share")}
    for index, by_level in enumerate(sources):
        source = by_level.get(dimension)
        if source is None:
            return None
        opening = sum(source["opening"].get(group, {}).get(code, 0.0)
                      for group in selected)
        final = sum(stock.get(group, [0.0] * ensemble["count"])[index] for group in selected)
        retirement = quit_loss = other = 0.0
        for year, group, kind, heads, mak in source["losses"]:
            if group not in selected or year > end_year:
                continue
            value = heads if code == "heads" else mak
            if kind in retirement_types:
                retirement += value
            elif kind == "QUIT":
                quit_loss += value
            elif kind in DEPARTURES:
                other += value
        departures = retirement + quit_loss + other
        per_seed["initial"].append(opening)
        per_seed["final"].append(final)
        per_seed["retirement_atz"].append(retirement)
        per_seed["quit"].append(quit_loss)
        per_seed["departures"].append(departures)
        per_seed["departure_share"].append(departures / opening if opening > 0 else None)
    from components.org_simulation_yearly import build_yearly_headline_metrics
    reference = build_yearly_headline_metrics(
        summary, dimension=dimension, organizations=organizations,
        end_year=end_year, metric=metric)
    if reference is None:
        return None
    for name, values in per_seed.items():
        expected, observed = reference[name], values[0]
        if (expected is None) != (observed is None):
            raise ValueError(f"OE-Jahreskennzahl {name} weicht vom Referenzlauf ab")
        if expected is not None and not math.isclose(
                float(expected), float(observed), rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"OE-Jahreskennzahl {name} weicht vom Referenzlauf ab")
    return {name: (_statistics(values, ensemble["band_level"])
                   if all(value is not None for value in values) else None)
            for name, values in per_seed.items()}


def target_rank_band_stats(chart: pd.DataFrame, *, ensemble: Mapping,
                           target_date, metric: str) -> dict | None:
    """Use certified final OE stock only when it equals every reference bar."""
    from utils.monte_carlo_chart_values import CHART_VERSION

    code = {"Köpfe": "heads", "MAK": "mak"}.get(metric)
    if code is None or target_date is None:
        return None
    date = pd.Timestamp(target_date).date().isoformat()
    samples = {(record_date, level, group, record_metric): values
               for record_date, level, group, record_metric, values
               in ensemble["statistics"]["annual_stock_samples"]}
    rows = []
    for group, reference in chart[["Organisationseinheit", "Wert"]].itertuples(index=False, name=None):
        values = samples.get((date, "Organisationseinheit", str(group), code))
        if (values is None or len(values) != ensemble["count"]
                or not math.isclose(float(reference), float(values[0]), rel_tol=0, abs_tol=1e-6)):
            return None
        rows.append((code, "Organisationseinheit", str(group),
                     _statistics(values, ensemble["band_level"])))
    return {"version": CHART_VERSION, "values": rows}
