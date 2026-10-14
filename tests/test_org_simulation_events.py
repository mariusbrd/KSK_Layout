from copy import deepcopy

import pandas as pd
import pytest

from utils.org_simulation_events import normalize_simulation_events, aggregate_simulation_events, METRICS
from utils.org_simulation_events import build_simulation_event_summary
from test_org_yearly_reference import _run, _payload, SCENARIOS


def _context():
    return {"run_signature": "a", "cluster_mapping": {"oe_map": {"OE1": "A", "OE2": "A"}},
            "departure_events": pd.DataFrame([
                {"event_date": "2026-06-01", "Organisationseinheit": "OE1", "reason_code": "ATZ_AR_TO_FR", "headcount_change": 0, "mak_change": -1},
                {"event_date": "2027-06-01", "Organisationseinheit": "OE1", "reason_code": "ATZ_END", "headcount_change": -1, "mak_change": 0},
                {"event_date": "2026-07-01", "Organisationseinheit": "OE2", "reason_code": "QUIT", "headcount_change": -1, "mak_change": -0.5},
                {"event_date": "2026-08-01", "Organisationseinheit": "OE3", "reason_code": "RETIREMENT", "headcount_change": -1, "mak_change": -1},
            ]), "accession_events": pd.DataFrame([
                {"date": "2026-08-01", "org_unit": "OE1", "type": "Azubi_Conversion_Out", "count": -1, "mak": 0},
                {"date": "2026-08-01", "org_unit": "OE2", "type": "Azubi_Conversion_In", "count": 1, "mak": 1},
                {"date": "2026-09-01", "org_unit": "OE2", "type": "Azubi_Exit", "count": -1, "mak": 0},
                {"date": "2027-01-01", "org_unit": "OE1", "type": "New_Hire", "count": 1, "mak": 0.75},
            ])}


def test_classification_and_calendar_years():
    events = normalize_simulation_events(_context(), expected_run_signature="a")
    assert events["Abgänge"].sum() == 4
    assert events["Zugänge"].sum() == 1
    assert events.loc[events.Kategorie == "Interner Wechsel", "Köpfe-Veränderung"].sum() == 0
    assert events.loc[events.Ereignisart == "ATZ_AR_TO_FR", "Abgänge"].sum() == 0
    assert set(events.Jahr) == {2026, 2027}
    assert events.loc[events.Organisationseinheit == "OE3", "OE-Cluster"].tolist() == ["Nicht zugeordnet"]


@pytest.mark.parametrize("dimension", ["Organisationseinheit", "OE-Cluster"])
def test_annual_and_overall_totals_preserve_every_event(dimension):
    events = normalize_simulation_events(_context())
    annual = aggregate_simulation_events(events, dimension=dimension)
    total = aggregate_simulation_events(events, dimension=dimension, by_year=False)
    for metric in METRICS:
        assert annual[metric].sum() == pytest.approx(events[metric].sum())
        assert total[metric].sum() == pytest.approx(events[metric].sum())
    pd.testing.assert_frame_equal(
        annual.groupby([dimension, "Kategorie", "Ereignisart"])[list(METRICS)].sum(),
        total.set_index([dimension, "Kategorie", "Ereignisart"])[list(METRICS)])


def test_no_mutation_and_saved_mapping_wins():
    context = _context()
    context["departure_events"]["OE-Cluster"] = "wrong draft mapping"
    before = deepcopy(context)
    events = normalize_simulation_events(context)
    assert "wrong draft mapping" not in events["OE-Cluster"].tolist()
    for key in ("departure_events", "accession_events"):
        pd.testing.assert_frame_equal(context[key], before[key], check_exact=True)


def test_cluster_departures_by_year_and_type_have_expected_values():
    table = aggregate_simulation_events(normalize_simulation_events(_context()), dimension="OE-Cluster")
    counts = table.groupby(["Jahr", "OE-Cluster"])["Abgänge"].sum().to_dict()
    assert counts == {(2026, "A"): 2, (2026, "Nicht zugeordnet"): 1, (2027, "A"): 1}
    by_type = table.groupby("Ereignisart")["Abgänge"].sum().to_dict()
    assert by_type["QUIT"] == 1
    assert by_type["Azubi_Exit"] == 1
    assert by_type["ATZ_END"] == 1
    assert by_type["Azubi_Conversion_Out"] == 0


def test_year_boundary_and_multiple_events_for_one_person_are_preserved():
    context = _context()
    context["departure_events"].loc[0, "event_date"] = "2026-12-31"
    context["departure_events"].loc[1, "event_date"] = "2027-01-01"
    context["departure_events"]["persnr"] = "000001"
    events = normalize_simulation_events(context)
    assert events.loc[events.Ereignisart == "ATZ_AR_TO_FR", "Jahr"].tolist() == [2026]
    assert events.loc[events.Ereignisart == "ATZ_END", "Jahr"].tolist() == [2027]
    assert len(events) == 8


@pytest.mark.parametrize("field,value", [("event_date", "invalid"), ("mak_change", None), ("headcount_change", float("inf"))])
def test_invalid_input_is_not_silently_dropped(field, value):
    context = _context()
    context["departure_events"].loc[0, field] = value
    with pytest.raises(ValueError):
        normalize_simulation_events(context)


def test_run_mismatch_and_empty_results():
    with pytest.raises(ValueError):
        normalize_simulation_events(_context(), expected_run_signature="other")
    assert aggregate_simulation_events(normalize_simulation_events({"run_signature": "empty"})).empty


def test_unknown_event_is_visible_and_not_assumed_to_be_departure():
    context = _context()
    context["departure_events"].loc[0, "reason_code"] = "FUTURE_TYPE"
    context["departure_events"].loc[0, "headcount_change"] = -1
    events = normalize_simulation_events(context)
    unknown = events.loc[events.Ereignisart == "FUTURE_TYPE"].iloc[0]
    assert unknown.Kategorie == "Sonstige Veränderungen"
    assert unknown["Abgänge"] == 0
    assert unknown["Köpfe-Veränderung"] == -1


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_real_single_run_aggregation_does_not_change_outputs(scenario):
    result = _run(scenario)
    before = _payload(result)
    context = {"run_signature": "real", "cluster_mapping": {"oe_map": {"OE1": "A", "OE2": "B"}},
               "departure_events": result.abgaenge_result.get("events_person_level", pd.DataFrame()),
               "accession_events": result.zugaenge_result.get("events", pd.DataFrame())}
    events = normalize_simulation_events(context)
    assert len(events) == len(context["departure_events"]) + len(context["accession_events"])
    for dimension in ("Organisationseinheit", "OE-Cluster"):
        table = aggregate_simulation_events(events, dimension=dimension)
        for metric in METRICS:
            assert table[metric].sum() == pytest.approx(events[metric].sum())
    assert _payload(result) == before


def test_summary_contains_consistent_views_of_one_run():
    context = _context()
    context["metadata"] = {"base_date": pd.Timestamp("2026-03-01"),
                           "target_date": pd.Timestamp("2027-09-30")}
    saved = build_simulation_event_summary(context, expected_run_signature="a")
    assert saved["run_signature"] == "a"
    for key in ("annual_oe", "annual_cluster", "total_oe", "total_cluster"):
        for metric in METRICS:
            assert saved[key][metric].sum() == pytest.approx(saved["events"][metric].sum())
    context["metadata"]["target_date"] = pd.Timestamp("2030-01-01")
    context["cluster_mapping"]["oe_map"]["OE1"] = "new draft"
    assert saved["metadata"]["target_date"] == pd.Timestamp("2027-09-30")
    assert "new draft" not in saved["annual_cluster"]["OE-Cluster"].tolist()


def test_summary_empty_run_and_mismatch():
    saved = build_simulation_event_summary({"run_signature": "empty"}, expected_run_signature="empty")
    assert all(saved[k].empty for k in ("events", "annual_oe", "annual_cluster", "total_oe", "total_cluster"))
    with pytest.raises(ValueError):
        build_simulation_event_summary(_context(), expected_run_signature="wrong")
