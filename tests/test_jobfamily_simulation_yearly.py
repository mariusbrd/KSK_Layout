"""Job-family yearly views must be derived without changing the OE run."""
from copy import deepcopy
from io import BytesIO

import pandas as pd

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view, _cached_yearly_view
from utils.jobfamily_yearly_stock_export import (
    build_jobfamily_stock_export_bytes, build_jobfamily_stock_export_tables,
)
from test_org_simulation_reconciliation import _bridge_context
from utils.org_simulation_events import METRICS, build_simulation_event_summary


def test_departure_is_reported_in_both_original_jobfamilies():
    baseline = pd.DataFrame([
        {"PersNr": "000001", "Is_Vacant": False, "Organisationseinheit": "OE",
         "Jobfamily": "Führung Vertrieb", "JF-Cluster": "Führung Vertrieb",
         "MAK_Reporting": 0.4, "Allocation_Weight": 0.4},
        {"PersNr": "000001", "Is_Vacant": False, "Organisationseinheit": "OE",
         "Jobfamily": "Qualitative Beratung", "JF-Cluster": "Qualitative Beratung",
         "MAK_Reporting": 0.6, "Allocation_Weight": 0.6},
    ])
    final = baseline.copy()
    final["Is_Vacant"] = True
    final["MAK_Reporting"] = 0.0
    event = pd.DataFrame([{"persnr": "000001", "event_date": pd.Timestamp("2026-12-01"),
                           "reason_code": "QUIT", "headcount_change": -1, "mak_change": -1.0,
                           "Organisationseinheit": "OE", "Jobfamily": "Führung Vertrieb"}])
    context = {"run_signature": "dual", "metadata": {"base_date": pd.Timestamp("2026-09-01"),
                   "target_date": pd.Timestamp("2026-12-31")},
               "baseline_snapshot": baseline, "final_snapshot": final,
               "zugaenge_params": {k: {"active": False} for k in ("azubi", "trainee", "new_hires")},
               "cluster_mapping": {"oe_map": {"OE": "Cluster"}},
               "departure_events": event, "accession_events": pd.DataFrame()}
    summary = build_simulation_event_summary(context, expected_run_signature="dual")
    view = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")
    quits = view["annual"].loc[view["annual"].Ereignisart.eq("QUIT")].set_index("Jobfamily")
    assert quits.loc["Führung Vertrieb", "MAK-Veränderung"] == -0.4
    assert quits.loc["Qualitative Beratung", "MAK-Veränderung"] == -0.6
    assert quits["MAK-Veränderung"].sum() == -1.0
    assert quits["Köpfe-Veränderung"].to_dict() == {
        "Führung Vertrieb": -1, "Qualitative Beratung": -1,
    }
    assert view["stock_checks"]["Abgestimmt"].all()
    assert summary["events"]["Abgänge"].sum() == 1


def test_jobfamily_view_preserves_saved_oe_outputs_and_event_totals():
    context = _bridge_context("departures")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    before_context = deepcopy(context)
    before_oe = deepcopy(summary["annual_oe"])
    before_bridge = deepcopy(summary["stock_reconciliation"]["annual"])

    view = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")

    assert view["run_signature"] == "a"
    assert view["annual"]["Abgänge"].sum() == summary["events"]["Abgänge"].sum()
    assert set(view["annual"]["Jobfamily"]) == {"Regular"}
    assert not view["stock"].empty
    assert view["stock_reason"] == ""
    jf_year_type = view["annual"].groupby(["Jahr", "Ereignisart"])[list(METRICS)].sum().sort_index()
    oe_year_type = summary["annual_oe"].groupby(["Jahr", "Ereignisart"])[list(METRICS)].sum().sort_index()
    pd.testing.assert_frame_equal(jf_year_type, oe_year_type, check_exact=True)
    target = pd.Timestamp(summary["metadata"]["target_date"])
    jf_closing = view["stock"].loc[view["stock"].Stichtag.eq(target)].groupby("Kennzahl").Bestand.sum()
    oe_closing = summary["stock_reconciliation"]["annual"].loc[
        lambda frame: frame.Ebene.eq("Gesamt") & frame.Stichtag.eq(target) & frame.Art.eq("Jahresstichtag")
    ].iloc[-1]
    assert jf_closing["Köpfe"] == oe_closing["Bestand-Köpfe"]
    assert abs(jf_closing["MAK"] - oe_closing["Bestand-MAK"]) < 1e-6
    pd.testing.assert_frame_equal(summary["annual_oe"], before_oe, check_exact=True)
    pd.testing.assert_frame_equal(summary["stock_reconciliation"]["annual"], before_bridge, check_exact=True)
    for key in ("baseline_snapshot", "final_snapshot", "departure_events", "accession_events"):
        pd.testing.assert_frame_equal(context[key], before_context[key], check_exact=True)


def test_jobfamily_stock_requires_reconciliation_and_disabled_accessions():
    context = _bridge_context("no_events")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    view = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")
    assert not view["stock"].empty
    assert view["stock_reason"] == ""

    with_hires = deepcopy(context)
    with_hires["zugaenge_params"]["new_hires"]["active"] = True
    view_with_hires = build_jobfamily_yearly_view(with_hires, summary, dimension="Jobfamily")
    assert view_with_hires["stock"].empty
    assert "deaktivierten Zugängen" in view_with_hires["stock_reason"]


def test_jobfamily_cluster_view_keeps_oe_summary_unchanged():
    context = _bridge_context("departures")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    before = deepcopy(summary["annual_cluster"])
    view = build_jobfamily_yearly_view(context, summary, dimension="JF-Cluster")
    assert view["annual"]["Abgänge"].sum() == summary["events"]["Abgänge"].sum()
    pd.testing.assert_frame_equal(summary["annual_cluster"], before, check_exact=True)


def test_jobfamily_stock_follows_oe_reconciliation_gate():
    context = _bridge_context("departures")
    context["metadata"]["target_date"] = pd.Timestamp("2026-01-01")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    assert summary["stock_reconciliation"]["status"] == "Nicht verfügbar"
    view = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")
    assert view["stock"].empty
    assert "Laufzeitraums" in view["stock_reason"]
    assert view["annual"]["Abgänge"].sum() == summary["annual_oe"]["Abgänge"].sum()


def test_oe_group_deviation_does_not_replace_jobfamily_reconciliation():
    context = _bridge_context("departures")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    oe_deviation = deepcopy(summary)
    oe_deviation["stock_reconciliation"]["status"] = "Abweichung"
    oe_deviation["stock_reconciliation"]["reason"] = "Drei OE-Endwertprüfungen abweichend."
    view = build_jobfamily_yearly_view(context, oe_deviation, dimension="Jobfamily")
    assert view["oe_bridge_status"] == "Abweichung"
    assert not view["stock"].empty
    assert view["stock_checks"]["Abgestimmt"].all()
    pd.testing.assert_frame_equal(summary["annual_oe"], oe_deviation["annual_oe"], check_exact=True)


def test_yearly_view_is_reused_for_filter_reruns_but_rebuilt_for_new_context(monkeypatch):
    from components import jobfamily_simulation_yearly as module

    context = _bridge_context("departures")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    session = {}
    monkeypatch.setattr(module.st, "session_state", session)
    calls = []
    original = module.build_jobfamily_yearly_view

    def counted(*args, **kwargs):
        calls.append(kwargs["dimension"])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "build_jobfamily_yearly_view", counted)
    first = _cached_yearly_view(context, summary, "Jobfamily")
    assert _cached_yearly_view(context, summary, "Jobfamily") is first
    assert calls == ["Jobfamily"]
    _cached_yearly_view(context, summary, "JF-Cluster")
    assert _cached_yearly_view(context, summary, "Jobfamily") is first
    assert calls == ["Jobfamily", "JF-Cluster"]
    new_context = deepcopy(context)
    _cached_yearly_view(new_context, summary, "Jobfamily")
    assert calls == ["Jobfamily", "JF-Cluster", "Jobfamily"]


def test_stock_export_matches_selected_view_and_keeps_both_metric_templates():
    context = _bridge_context("departures")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    view = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")
    before = deepcopy(view["stock"])
    group = sorted(view["stock"].Jobfamily.unique())[0]
    year = int(view["periods"].Jahr.iloc[0])
    kinds = sorted(view["annual"].Ereignisart.unique())

    tables = build_jobfamily_stock_export_tables(
        view, dimension="Jobfamily", groups=[group], years=[year],
        metric="MAK", event_types=kinds)
    expected = view["stock"].loc[
        view["stock"].Jobfamily.eq(group) & view["stock"].Kennzahl.eq("MAK") &
        (view["stock"].Art.eq("Ausgangsstichtag") |
         (view["stock"].Art.eq("Jahresstichtag") & view["stock"].Stichtag.dt.year.eq(year)))
    ][["Stichtag", "Jobfamily", "Bestand"]].drop_duplicates(["Jobfamily", "Stichtag"], keep="last")
    pd.testing.assert_frame_equal(tables["Bestand_Auswahl_Grafik"], expected, check_exact=True)
    assert {"Bestand_Folienvorlage_MAK", "Bestand_Folienvorlage_Koepfe",
            "Bestand_Endwertabgleich"}.issubset(tables)
    for metric_name, sheet_name in (("MAK", "Bestand_Folienvorlage_MAK"),
                                    ("Köpfe", "Bestand_Folienvorlage_Koepfe")):
        scoped = view["stock"].loc[
            view["stock"].Jobfamily.eq(group) & view["stock"].Kennzahl.eq(metric_name)]
        slide = tables[sheet_name].iloc[0]
        assert slide["Start"] == scoped.loc[scoped.Art.eq("Ausgangsstichtag"), "Bestand"].sum()
        assert slide[year] == scoped.loc[
            scoped.Art.eq("Jahresstichtag") & scoped.Stichtag.dt.year.eq(year), "Bestand"].sum()
    pd.testing.assert_frame_equal(view["stock"], before, check_exact=True)

    payload = build_jobfamily_stock_export_bytes(
        view, dimension="Jobfamily", groups=[group], years=[year],
        metric="MAK", event_types=kinds)
    book = pd.ExcelFile(BytesIO(payload))
    assert set(tables).issubset(book.sheet_names)
    assert "Kontext" in book.sheet_names
