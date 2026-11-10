"""The compact ratio inputs reproduce the existing annual tile arithmetic."""
from __future__ import annotations

import pandas as pd
import pytest

from components.org_simulation_yearly import build_yearly_headline_metrics
from utils.monte_carlo_departure_rate import (
    certified_rate_inputs, departure_rate_input, departure_share,
)
from utils.monte_carlo_ensemble import aggregate_departure_share, aggregate_packets


@pytest.mark.parametrize("metric", [("Köpfe", "heads"), ("MAK", "mak")])
def test_oe_compact_rate_matches_existing_tile(metric: tuple[str, str]) -> None:
    events = pd.DataFrame([
        {"Jahr": 2027, "Organisationseinheit": "A", "Ereignisart": "QUIT",
         "Köpfe-Veränderung": -1, "MAK-Veränderung": -0.6},
        {"Jahr": 2027, "Organisationseinheit": "A", "Ereignisart": "ATZ_AR_TO_FR",
         "Köpfe-Veränderung": 0, "MAK-Veränderung": -0.4},
        {"Jahr": 2028, "Organisationseinheit": "A", "Ereignisart": "RETIREMENT",
         "Köpfe-Veränderung": -1, "MAK-Veränderung": -1.0},
    ])
    annual = pd.DataFrame([
        {"Ebene": "Organisationseinheit", "Organisation": "A", "Jahr": 2026,
         "Art": "Ausgangsstichtag", "Bestand-Köpfe": 4, "Bestand-MAK": 3.0},
        {"Ebene": "Organisationseinheit", "Organisation": "A", "Jahr": 2027,
         "Art": "Jahresstichtag", "Bestand-Köpfe": 3, "Bestand-MAK": 2.0},
    ])
    summary = {"stock_reconciliation": {"status": "Abgestimmt", "annual": annual,
                                         "reporting_events": events}}
    label, compact_metric = metric
    expected = build_yearly_headline_metrics(
        summary, dimension="Organisationseinheit", organizations=["A"],
        end_year=2027, metric=label)
    source = departure_rate_input(
        level="Organisationseinheit",
        opening=pd.DataFrame([{"Organisationseinheit": "A", "Köpfe": 4, "MAK": 3.0}]),
        reporting_events=events)
    actual = departure_share(source, groups=["A"], end_year=2027,
                             metric=compact_metric)
    assert actual == {"numerator": expected["departures"],
                      "denominator": expected["initial"],
                      "share": expected["departure_share"]}


def test_jf_group_selection_and_zero_opening() -> None:
    source = departure_rate_input(
        level="Jobfamily",
        opening=pd.DataFrame([
            {"Jobfamily": "A", "Köpfe": 2, "MAK": 1.5},
            {"Jobfamily": "B", "Köpfe": 0, "MAK": 0.0}]),
        reporting_events=pd.DataFrame([
            {"Jahr": 2027, "Jobfamily": "A", "Ereignisart": "QUIT",
             "Köpfe-Veränderung": -1, "MAK-Veränderung": -0.5},
            {"Jahr": 2028, "Jobfamily": "B", "Ereignisart": "QUIT",
             "Köpfe-Veränderung": -1, "MAK-Veränderung": -1.0}]))
    assert departure_share(source, groups=["A"], end_year=2027, metric="mak") == {
        "numerator": 0.5, "denominator": 1.5, "share": 0.5 / 1.5}
    assert departure_share(source, groups=["B"], end_year=2027, metric="heads") == {
        "numerator": 0.0, "denominator": 0.0, "share": None}


def test_certified_inputs_match_real_departure_only_views() -> None:
    from test_org_simulation_reconciliation import _bridge_context
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    from utils.org_simulation_events import build_simulation_event_summary

    context = _bridge_context("atz_ruhend", seed=42)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    sources = certified_rate_inputs(context, summary)
    # This small reference run has certified OE stocks but no certified JF
    # stock view. The extractor must not fabricate JF ratios from OE data.
    assert set(sources) == {"Organisationseinheit", "OE-Cluster"}
    end_year = pd.Timestamp(context["metadata"]["target_date"]).year
    for level in ("Organisationseinheit", "OE-Cluster"):
        groups = list(sources[level]["opening"])
        for label, metric in (("Köpfe", "heads"), ("MAK", "mak")):
            expected = build_yearly_headline_metrics(
                summary, dimension=level, organizations=groups,
                end_year=end_year, metric=label)
            actual = departure_share(sources[level], groups=groups,
                                     end_year=end_year, metric=metric)
            assert actual["numerator"] == expected["departures"]
            assert actual["denominator"] == expected["initial"]
            assert actual["share"] == expected["departure_share"]


def test_jf_certification_is_independent_of_oe_bridge() -> None:
    from test_org_simulation_reconciliation import _bridge_context
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    from utils.org_simulation_events import DEPARTURES, build_simulation_event_summary

    context = _bridge_context("departures", seed=42)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    assert summary["stock_reconciliation"]["status"] == "Abgestimmt"
    assert set(certified_rate_inputs(context, summary)) == {
        "Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"}
    summary["stock_reconciliation"]["status"] = "Abweichung"
    sources = certified_rate_inputs(context, summary)
    assert set(sources) == {"Jobfamily", "JF-Cluster"}
    end_year = pd.Timestamp(context["metadata"]["target_date"]).year
    for level in ("Jobfamily", "JF-Cluster"):
        view = build_jobfamily_yearly_view(context, summary, dimension=level)
        assert not view["stock"].empty
        groups = list(sources[level]["opening"])
        for label, metric in (("Köpfe", "heads"), ("MAK", "mak")):
            visible = view["stock"].loc[
                view["stock"].Art.eq("Ausgangsstichtag") &
                view["stock"].Kennzahl.eq(label) &
                view["stock"][level].isin(groups)]
            opening = visible.Bestand.sum()
            reported = view["reporting_events"]
            selected = reported.loc[reported[level].isin(groups) & reported.Jahr.le(end_year)]
            loss = -pd.to_numeric(selected[f"{label}-Veränderung"], errors="coerce").clip(upper=0)
            expected_loss = float(loss.loc[
                selected.Ereignisart.isin(DEPARTURES | {"ATZ_AR_TO_FR"})].sum())
            actual = departure_share(sources[level], groups=groups,
                                     end_year=end_year, metric=metric)
            assert actual["numerator"] == expected_loss
            assert actual["denominator"] == opening
            assert actual["share"] == (expected_loss / opening if opening > 0 else None)


def test_multi_run_rate_uses_seed_ratios_and_rejects_missing_source() -> None:
    from utils.monte_carlo_metric_packet import LEVELS

    def run(seed: int, opening: float, loss: float) -> dict:
        source = departure_rate_input(
            level="Jobfamily",
            opening=pd.DataFrame([{"Jobfamily": "A", "Köpfe": 2,
                                   "MAK": opening}]),
            reporting_events=pd.DataFrame([{
                "Jahr": 2027, "Jobfamily": "A", "Ereignisart": "QUIT",
                "Köpfe-Veränderung": -1, "MAK-Veränderung": -loss}]))
        return {"seed": seed, "status": "ok", "packet": {
            "version": 1, "target": {"heads": 2, "mak": opening, "eur": 0},
            "annual_values": [], "stock_status": {level: "Abgestimmt" for level in LEVELS},
            "departure_rate_inputs": {"Jobfamily": source}}}

    runs = [run(7, 1.0, 0.5), run(8, 2.0, 0.5)]
    ensemble = aggregate_packets(base_seed=7, count=2, runs=runs)
    result = aggregate_departure_share(
        ensemble=ensemble, runs=runs, level="Jobfamily", groups=["A"],
        end_year=2027, metric="mak")
    assert result["status"] == "complete"
    assert result["share"]["mean"] == 0.375
    assert result["share"]["mean"] != 1.0 / 3.0  # pooled loss / pooled opening
    assert [row["share"] for row in result["per_seed"]] == [0.5, 0.25]
    del runs[1]["packet"]["departure_rate_inputs"]
    unavailable = aggregate_departure_share(
        ensemble=ensemble, runs=runs, level="Jobfamily", groups=["A"],
        end_year=2027, metric="mak")
    assert unavailable["status"] == "unavailable"
    assert unavailable["seed"] == 8


def test_real_certified_summary_enters_complete_n1_packet() -> None:
    from test_org_simulation_reconciliation import _bridge_context
    from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
    from utils.monte_carlo_complete_packet import build_complete_metric_packet
    from utils.org_simulation_events import build_simulation_event_summary

    context = _bridge_context("departures", seed=42)
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    bridge = summary["stock_reconciliation"]["annual"]
    stocks = {}
    for level in ("Organisationseinheit", "OE-Cluster"):
        rows = bridge.loc[bridge.Ebene.eq(level) & bridge.Art.isin(
            ["Ausgangsstichtag", "Jahresstichtag"])]
        stocks[level] = rows[["Stichtag", "Organisation", "Bestand-Köpfe",
                              "Bestand-MAK"]].rename(columns={
            "Organisation": level, "Bestand-Köpfe": "Köpfe", "Bestand-MAK": "MAK"})
        stocks[level] = stocks[level].drop_duplicates(["Stichtag", level], keep="first")
    for level in ("Jobfamily", "JF-Cluster"):
        view = build_jobfamily_yearly_view(context, summary, dimension=level)
        stock = view["stock"].loc[view["stock"].Art.isin(
            ["Ausgangsstichtag", "Jahresstichtag"])]
        heads = stock.loc[stock.Kennzahl.eq("Köpfe"), ["Stichtag", level, "Bestand"]]
        mak = stock.loc[stock.Kennzahl.eq("MAK"), ["Stichtag", level, "Bestand"]]
        stocks[level] = heads.merge(mak, on=["Stichtag", level], suffixes=("-Köpfe", "-MAK"))
        stocks[level] = stocks[level].rename(columns={
            "Bestand-Köpfe": "Köpfe", "Bestand-MAK": "MAK"})
        stocks[level] = stocks[level].drop_duplicates(["Stichtag", level], keep="first")
    packet = build_complete_metric_packet(
        context, target_totals={"heads": 0, "mak": 0.0, "eur": 0.0},
        annual_stock_tables=stocks, certified_rate_summary=summary)
    assert set(packet["departure_rate_inputs"]) == {
        "Organisationseinheit", "OE-Cluster", "Jobfamily", "JF-Cluster"}
    runs = [{"seed": 42, "status": "ok", "packet": packet}]
    ensemble = aggregate_packets(base_seed=42, count=1, runs=runs)
    assert ensemble["status"] == "complete"
    end_year = pd.Timestamp(context["metadata"]["target_date"]).year
    for level, source in packet["departure_rate_inputs"].items():
        groups = list(source["opening"])
        for metric in ("heads", "mak"):
            raw = departure_share(source, groups=groups, end_year=end_year,
                                  metric=metric)
            if raw["share"] is None:
                continue
            result = aggregate_departure_share(
                ensemble=ensemble, runs=runs, level=level, groups=groups,
                end_year=end_year, metric=metric)
            assert result["share"] == {"mean": raw["share"],
                                       "lower": raw["share"], "upper": raw["share"]}


def test_ten_real_departure_runs_reconcile_with_reporting_settings() -> None:
    from test_org_simulation_reconciliation import _bridge_context
    from utils.monte_carlo_metric_packet import LEVELS
    from utils.org_simulation_events import build_simulation_event_summary

    runs = []
    certified = []
    for seed in range(10):
        context = _bridge_context("departures", seed=seed)
        # The productive page always saves these settings in the event
        # context; the older synthetic helper omits them by default.
        context["run_settings"] = {"exclusions": {}}
        summary = build_simulation_event_summary(context, expected_run_signature="a")
        sources = certified_rate_inputs(context, summary)
        assert set(sources) == {"Organisationseinheit", "OE-Cluster", "Jobfamily"}
        if "Organisationseinheit" in sources:
            certified.append(seed)
            groups = list(sources["Organisationseinheit"]["opening"])
            year = pd.Timestamp(context["metadata"]["target_date"]).year
            expected = build_yearly_headline_metrics(
                summary, dimension="Organisationseinheit", organizations=groups,
                end_year=year, metric="MAK")
            actual = departure_share(sources["Organisationseinheit"], groups=groups,
                                     end_year=year, metric="mak")
            assert actual["numerator"] == expected["departures"]
            assert actual["denominator"] == expected["initial"]
            assert actual["share"] == expected["departure_share"]
            from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view
            from utils.org_simulation_events import DEPARTURES
            jf_view = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")
            jf_groups = list(sources["Jobfamily"]["opening"])
            jf_events = jf_view["reporting_events"]
            selected = jf_events.loc[jf_events.Jobfamily.isin(jf_groups) &
                                     jf_events.Jahr.le(year)]
            loss = -pd.to_numeric(selected["MAK-Veränderung"], errors="coerce").clip(upper=0)
            jf_loss = float(loss.loc[selected.Ereignisart.isin(
                DEPARTURES | {"ATZ_AR_TO_FR"})].sum())
            jf_opening = jf_view["stock"].loc[
                jf_view["stock"].Art.eq("Ausgangsstichtag") &
                jf_view["stock"].Kennzahl.eq("MAK") &
                jf_view["stock"].Jobfamily.isin(jf_groups), "Bestand"].sum()
            jf_actual = departure_share(sources["Jobfamily"], groups=jf_groups,
                                        end_year=year, metric="mak")
            assert jf_actual["numerator"] == jf_loss
            assert jf_actual["denominator"] == jf_opening
            assert jf_actual["share"] == (jf_loss / jf_opening if jf_opening > 0 else None)
        runs.append({"seed": seed, "status": "ok", "packet": {
            "version": 1, "target": {"heads": 0, "mak": 0.0, "eur": 0.0},
            "annual_values": [],
            "stock_status": {level: ("Abgestimmt" if level in sources
                                    else "Nicht verfügbar") for level in LEVELS},
            "departure_rate_inputs": sources}})
    assert certified == list(range(10))
    ensemble = aggregate_packets(base_seed=0, count=10, runs=runs)
    assert ensemble["status"] == "complete"
    assert all("Organisationseinheit" in run["packet"]["departure_rate_inputs"]
               for run in runs)
    result = aggregate_departure_share(
        ensemble=ensemble, runs=runs, level="Organisationseinheit",
        groups=["OE1"], end_year=2026, metric="mak")
    assert result["status"] == "complete"
    assert result["count"] == 10
    assert result["share"]["mean"] == sum(
        row["share"] for row in result["per_seed"]) / 10
    jf_result = aggregate_departure_share(
        ensemble=ensemble, runs=runs, level="Jobfamily",
        groups=["Regular"], end_year=2026, metric="mak")
    assert jf_result["status"] == "complete"
    assert jf_result["count"] == 10
    jf_cluster = aggregate_departure_share(
        ensemble=ensemble, runs=runs, level="JF-Cluster",
        groups=["Regular"], end_year=2026, metric="mak")
    assert jf_cluster["status"] == "unavailable"
