"""Job-family yearly views must be derived without changing the OE run."""
from copy import deepcopy
from io import BytesIO

import pandas as pd
import pytest

from components.jobfamily_simulation_yearly import build_jobfamily_yearly_view, _cached_yearly_view
from utils.jobfamily_yearly_stock_export import (
    build_jobfamily_stock_export_bytes, build_jobfamily_stock_export_tables,
)
from test_org_simulation_reconciliation import _bridge_context
from utils.org_simulation_events import METRICS, build_simulation_event_summary


def test_monte_carlo_position_stock_frame_and_export_keep_yearly_bands(monkeypatch):
    import components.jobfamily_simulation_yearly as yearly

    samples = [
        ("2026-12-31", "Jobfamily", "Beratung", "mak", [1.0, 3.0],
         {"mean": 2.0, "lower": 1.2, "upper": 2.8}),
        ("2027-12-31", "Jobfamily", "Beratung", "mak", [2.0, 4.0],
         {"mean": 3.0, "lower": 2.2, "upper": 3.8}),
        ("2027-12-31", "JF-Cluster", "Kunden", "heads", [2.0, 4.0],
         {"mean": 3.0, "lower": 2.2, "upper": 3.8}),
        ("2027-12-31", "Organisationseinheit", "OE", "mak", [2.0, 4.0],
         {"mean": 3.0, "lower": 2.2, "upper": 3.8}),
    ]
    seen_filters = []
    def filtered(_ensemble, filters):
        seen_filters.append(filters)
        return samples
    monkeypatch.setattr(yearly, "filtered_annual_position_samples", filtered)
    frame = yearly._monte_carlo_position_stock_frame({"count": 2}, {"selected_jobfamilies": ["Beratung"]})
    assert seen_filters == [{"selected_jobfamilies": ["Beratung"]}]
    assert len(frame) == 3
    selected = frame.loc[frame["Ebene"].eq("Jobfamily") & frame["Stichtag"].dt.year.eq(2027)]
    assert selected.iloc[0]["Mittelwert"] == 3.0
    assert selected.iloc[0]["P10"] == 2.2
    data = yearly._export_monte_carlo_position_stock(selected, {"count": 2})
    with pd.ExcelFile(BytesIO(data)) as book:
        exported = pd.read_excel(book, "Positionsbestand_Jahre")
        assert exported.loc[0, "P90"] == 3.8
        assert pd.read_excel(book, "Kontext").loc[0, "Bestandsart"] == "Zertifizierter Positionsbestand"


def test_old_annual_event_contract_is_rejected_before_rendering():
    import components.jobfamily_simulation_yearly as yearly

    ensemble = {"count": 2, "annual_event_rows_version": 2,
                "annual_event_rows": [b"old", b"old"],
                "annual_positions_version": yearly.ANNUAL_POSITIONS_VERSION,
                "annual_positions": [b"position", b"position"]}
    assert yearly.ANNUAL_EVENTS_VERSION > 2
    with pytest.raises(ValueError, match="Jahresereignis- oder Positionszeilen"):
        yearly._monte_carlo_jobfamily_event_frame(
            ensemble, {}, {}, "Jobfamily", {})


def test_monte_carlo_position_export_fingerprint_changes_with_person_filter(monkeypatch):
    import components.jobfamily_simulation_yearly as yearly

    class Capture:
        def radio(self, *_args, **_kwargs):
            return "MAK"

        def __getattr__(self, name):
            return lambda *_args, **_kwargs: None

    captured = []
    monkeypatch.setattr(yearly, "st", Capture())
    monkeypatch.setattr(yearly, "_monte_carlo_position_headlines",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(yearly, "lazy_excel_download_button_compat",
                        lambda **kwargs: captured.append(kwargs))
    stock = pd.DataFrame([{"Stichtag": pd.Timestamp("2027-12-31"),
                           "Ebene": "Jobfamily", "Jobgruppe": "A",
                           "Kennzahl": "MAK", "Mittelwert": 1.5,
                           "P10": 1.1, "P90": 1.9, "samples": [1.0, 2.0]}])
    ensemble = {"count": 2, "band_level": 0.8, "reference_instance": 1}
    for filters in ({"selected_genders": ["w"]},
                    {"selected_genders": ["m"]}):
        yearly._render_monte_carlo_position_stock(
            stock, dimension="Jobfamily", groups=["A"], years=[2027],
            metric="MAK",
            ensemble=ensemble, baseline=pd.DataFrame(), active_filters=filters)
    assert captured[0]["fingerprint"] != captured[1]["fingerprint"]


def test_monte_carlo_position_headlines_sum_each_seed_before_quantiles():
    import components.jobfamily_simulation_yearly as yearly

    baseline = pd.DataFrame([
        {"PersNr": "1", "Is_Vacant": False, "Jobfamily": "A", "JF-Cluster": "X",
         "MAK_Reporting": 0.4},
        {"PersNr": "2", "Is_Vacant": False, "Jobfamily": "B", "JF-Cluster": "X",
         "MAK_Reporting": 0.6},
    ])
    stock = pd.DataFrame([
        {"Stichtag": pd.Timestamp("2027-12-31"), "Ebene": "Jobfamily",
         "Jobgruppe": "A", "Kennzahl": "MAK", "samples": [0.2, 1.0]},
        {"Stichtag": pd.Timestamp("2027-12-31"), "Ebene": "Jobfamily",
         "Jobgruppe": "B", "Kennzahl": "MAK", "samples": [1.0, 0.2]},
    ])
    result = yearly._monte_carlo_position_headlines(
        stock, baseline, dimension="Jobfamily", groups=["A", "B"], years=[2027],
        metric="MAK", count=2, band_level=0.8, active_filters={})
    assert result["opening"]["mean"] == pytest.approx(1.0)
    assert result["closing"]["mean"] == pytest.approx(1.2)
    assert result["closing"]["lower"] == pytest.approx(1.2)
    assert result["closing"]["upper"] == pytest.approx(1.2)


def test_monte_carlo_position_headlines_use_reporting_losses_per_seed():
    import components.jobfamily_simulation_yearly as yearly

    baseline = pd.DataFrame([
        {"PersNr": "1", "Is_Vacant": False, "Jobfamily": "A", "JF-Cluster": "X",
         "MAK_Reporting": 1.0},
    ])
    stock = pd.DataFrame([
        {"Stichtag": pd.Timestamp("2027-12-31"), "Ebene": "Jobfamily",
         "Jobgruppe": "A", "Kennzahl": "MAK", "samples": [0.5, 0.0]},
    ])
    def reported(quit_loss, retirement_loss):
        return pd.DataFrame([
            {"Jahr": 2027, "Jobfamily": "A", "Ereignisart": "QUIT",
             "MAK-Veränderung": -quit_loss, "Köpfe-Veränderung": -1},
            {"Jahr": 2027, "Jobfamily": "A", "Ereignisart": "RETIREMENT",
             "MAK-Veränderung": -retirement_loss, "Köpfe-Veränderung": -1},
        ])
    result = yearly._monte_carlo_position_headlines(
        stock, baseline, dimension="Jobfamily", groups=["A"], years=[2027],
        metric="MAK", count=2, band_level=0.8, active_filters={},
        reported_runs=[reported(0.2, 0.3), reported(0.4, 0.6)])
    assert result["departure_loss"]["mean"] == pytest.approx(0.75)
    assert result["departure_share"]["mean"] == pytest.approx(0.75)
    assert result["retirement_atz"]["mean"] == pytest.approx(0.45)
    assert result["quit_loss"]["mean"] == pytest.approx(0.3)
    assert result["departure_share"]["lower"] == pytest.approx(0.55)
    with pd.ExcelFile(BytesIO(yearly._export_monte_carlo_position_stock(
            stock, {"count": 2}, result))) as book:
        exported = pd.read_excel(book, "Kennzahlen").set_index("Kennzahl")
        assert exported.loc["Abgänge / Ausgangsbestand", "Mittelwert"] == pytest.approx(0.75)
        assert exported.loc["Rente inkl. ATZ", "P10"] == pytest.approx(
            result["retirement_atz"]["lower"])
        assert "samples" not in pd.read_excel(book, "Positionsbestand_Jahre")


def test_monte_carlo_reported_jf_losses_match_existing_single_run(monkeypatch):
    import components.jobfamily_simulation_yearly as yearly
    from utils.monte_carlo_filtered_annual_events import _samples
    from utils.org_simulation_events import normalize_simulation_events

    context = _bridge_context("departures")
    summary = build_simulation_event_summary(
        context, expected_run_signature=context["run_signature"])
    view = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")
    assert not view["reporting_events"].empty
    raw = normalize_simulation_events(context)
    monkeypatch.setattr(yearly, "decode_annual_event_rows", lambda _blob: raw.copy())
    monkeypatch.setattr(yearly, "decode_annual_positions",
                        lambda _blob: [context["final_snapshot"].copy()])
    certified = [(*key, [value, value]) for key, value in _samples(raw).items()]
    ensemble = {"count": 2, "band_level": 0.8,
                "annual_event_rows_version": yearly.ANNUAL_EVENTS_VERSION,
                "annual_positions_version": yearly.ANNUAL_POSITIONS_VERSION,
                "annual_event_rows": [b"one", b"two"],
                "annual_positions": [b"one", b"two"],
                "statistics": {"annual_event_samples": certified}}
    rows = yearly._monte_carlo_jobfamily_event_frame(
        ensemble, context, view, "Jobfamily", {})
    reported = rows.attrs["reported_runs"][0]
    actual = reported.groupby(["Jahr", "Jobfamily", "Ereignisart"])[
        ["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
    expected = view["reporting_events"].groupby(["Jahr", "Jobfamily", "Ereignisart"])[
        ["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
    pd.testing.assert_frame_equal(actual.sort_index(), expected.sort_index())


def test_monte_carlo_reference_view_does_not_build_single_run_stock_bridge(monkeypatch):
    import components.jobfamily_simulation_yearly as yearly

    context = _bridge_context("departures")
    summary = build_simulation_event_summary(
        context, expected_run_signature=context["run_signature"])
    full = build_jobfamily_yearly_view(context, summary, dimension="Jobfamily")
    monkeypatch.setattr(yearly, "reporting_projection_base",
                        lambda *_args, **_kwargs: pytest.fail("single-run bridge must not run"))
    reference = yearly._cached_yearly_view(
        context, summary, "Jobfamily", include_stock=False)
    pd.testing.assert_frame_equal(reference["annual"], full["annual"])
    assert reference["stock"].empty
    assert yearly._cached_yearly_view(
        context, summary, "Jobfamily", include_stock=False) is reference


def test_monte_carlo_headlines_separate_atz_head_and_capacity_effect():
    import components.jobfamily_simulation_yearly as yearly

    baseline = pd.DataFrame([{"PersNr": "1", "Is_Vacant": False,
                              "Jobfamily": "A", "JF-Cluster": "X", "MAK_Reporting": 1.0}])
    stock = pd.DataFrame([{"Stichtag": pd.Timestamp("2027-12-31"),
                           "Ebene": "Jobfamily", "Jobgruppe": "A",
                           "Kennzahl": "Köpfe", "samples": [1.0, 1.0]}])
    atz = pd.DataFrame([{"Jahr": 2027, "Jobfamily": "A",
                         "Ereignisart": "ATZ_AR_TO_FR", "Köpfe-Veränderung": 0.0,
                         "MAK-Veränderung": -0.5}])
    result = yearly._monte_carlo_position_headlines(
        stock, baseline, dimension="Jobfamily", groups=["A"], years=[2027],
        metric="Köpfe", count=2, band_level=0.8, active_filters={},
        reported_runs=[atz, atz])
    assert result["retirement_atz"]["mean"] == 0
    assert result["departure_share"]["mean"] == 0
    mak_stock = stock.assign(Kennzahl="MAK", samples=[[0.5, 0.5]])
    mak_result = yearly._monte_carlo_position_headlines(
        mak_stock, baseline, dimension="Jobfamily", groups=["A"], years=[2027],
        metric="MAK", count=2, band_level=0.8, active_filters={},
        reported_runs=[atz, atz])
    assert mak_result["retirement_atz"]["mean"] == pytest.approx(0.5)
    assert mak_result["departure_share"]["mean"] == pytest.approx(0.5)


def test_monte_carlo_year_sum_and_unassigned_quality_use_run_samples():
    import components.jobfamily_simulation_yearly as yearly

    selected = pd.DataFrame([
        {"Jahr": 2027, "Jobfamily": "A", "Kennzahl": "MAK-Veränderung",
         "Ereignisart": "QUIT", "samples": [1.0, 3.0], "Seed 0": 1.0},
        {"Jahr": 2028, "Jobfamily": "A", "Kennzahl": "MAK-Veränderung",
         "Ereignisart": "QUIT", "samples": [3.0, 1.0], "Seed 0": 3.0},
    ])
    year_sum = yearly._monte_carlo_event_year_sum(
        selected, dimension="Jobfamily", band_level=0.8)
    assert year_sum.loc[0, "Summe ausgewählte Kennzahl"] == pytest.approx(4.0)
    assert year_sum.loc[0, "P10"] == pytest.approx(4.0)
    assert year_sum.loc[0, "P90"] == pytest.approx(4.0)

    departures = pd.DataFrame([
        {"Jahr": 2027, "Jobfamily": "A", "Kennzahl": "Abgänge",
         "Ereignisart": "QUIT", "samples": [1.0, 3.0]},
        {"Jahr": 2028, "Jobfamily": "A", "Kennzahl": "Abgänge",
         "Ereignisart": "QUIT", "samples": [3.0, 1.0]},
        {"Jahr": 2027, "Jobfamily": "A", "Kennzahl": "MAK-Veränderung",
         "Ereignisart": "QUIT", "samples": [-0.5, -1.5]},
        {"Jahr": 2028, "Jobfamily": "A", "Kennzahl": "MAK-Veränderung",
         "Ereignisart": "QUIT", "samples": [-1.5, -0.5]},
    ])
    departure_sum = yearly._monte_carlo_departure_year_sum(
        departures, dimension="Jobfamily", groups=["A", "B"], count=2,
        band_level=0.8)
    assert departure_sum.loc[0, "Abgehende Köpfe"] == pytest.approx(4.0)
    assert departure_sum.loc[0, "Abgehende Köpfe P10"] == pytest.approx(4.0)
    assert departure_sum.loc[0, "Abgehende MAK"] == pytest.approx(2.0)
    assert departure_sum.loc[1, "Abgehende Köpfe"] == 0

    event_runs = [
        pd.DataFrame([{"Jahr": 2027, "Jobfamily": "UNMAPPED", "Ereignisart": "QUIT"}]),
        pd.DataFrame([{"Jahr": 2027, "Jobfamily": "A", "Ereignisart": "QUIT"}]),
    ]
    quality = yearly._monte_carlo_unassigned_events(
        event_runs, dimension="Jobfamily", years=[2027],
        event_types=["QUIT"], band_level=0.8)
    assert quality["mean"] == pytest.approx(0.5)
    assert quality["lower"] == pytest.approx(0.1)
    assert quality["upper"] == pytest.approx(0.9)
    assert yearly._monte_carlo_unassigned_events(
        event_runs, dimension="Jobfamily", years=[2028],
        event_types=["QUIT"], band_level=0.8)["mean"] == 0

    data = yearly._export_monte_carlo_events(
        selected, pd.DataFrame(), {"count": 2},
        year_sum=year_sum, unassigned=quality)
    with pd.ExcelFile(BytesIO(data)) as book:
        exported_sum = pd.read_excel(book, "Ereignisse_Summe")
        pd.testing.assert_frame_equal(exported_sum, year_sum, check_dtype=False)
        exported_quality = pd.read_excel(book, "Datenqualitaet")
        assert exported_quality.loc[0, "Mittelwert"] == pytest.approx(0.5)


def test_monte_carlo_yearly_options_do_not_inherit_reference_only_final_group():
    import components.jobfamily_simulation_yearly as yearly

    baseline = pd.DataFrame([{"PersNr": "1", "Jobfamily": "Start", "JF-Cluster": "X",
                              "Is_Vacant": False, "MAK_Reporting": 1.0}])
    stock = pd.DataFrame([{"Ebene": "Jobfamily", "Jobgruppe": "Späterer Lauf"}])
    events = pd.DataFrame([{"Jobfamily": "Ereignisgruppe", "Ereignisart": "QUIT"}])
    options = yearly._monte_carlo_yearly_group_options(
        baseline, stock, events, dimension="Jobfamily", active_filters={})
    assert options == ["Ereignisgruppe", "Späterer Lauf", "Start"]


def test_monte_carlo_event_export_marks_reference_sheets():
    import components.jobfamily_simulation_yearly as yearly

    selected = pd.DataFrame([{"Jahr": 2027, "Jobfamily": "Beratung", "Abgänge": 2}])
    stock = pd.DataFrame([{"Stichtag": pd.Timestamp("2027-12-31"),
                           "Jobfamily": "Beratung", "Bestand": 4}])
    kwargs = dict(dimension="Jobfamily", groups=["Beratung"], years=[2027],
                  run_signature="test", stock_reason="")
    multi = yearly._export_yearly_view(selected, stock, reference_only=True, **kwargs)
    with pd.ExcelFile(BytesIO(multi)) as book:
        assert "Ref_Ereignisse_Jahre" in book.sheet_names
        assert "Ref_Bestand_Jahre" in book.sheet_names
        assert pd.read_excel(book, "Kontext").loc[0, "Ansicht"] == "Referenzlauf (Seed 0)"
    single = yearly._export_yearly_view(selected, stock, **kwargs)
    with pd.ExcelFile(BytesIO(single)) as book:
        assert "Ereignisse_Jahre" in book.sheet_names
        assert "Ansicht" not in pd.read_excel(book, "Kontext")


def test_prepared_jf_yearly_export_is_bound_to_ensemble_and_selection():
    import components.jobfamily_simulation_yearly as yearly

    first = {"count": 2, "plan_signature": "plan", "input_fingerprint": "input",
             "reference_instance": 1,
             "annual_event_rows_version": yearly.ANNUAL_EVENTS_VERSION,
             "annual_positions_version": 1}
    second = first.copy()
    filters = {"selected_jobfamilies": ["Beratung"]}
    arguments = ("reference", "Jobfamily", [2027], ["Beratung"], ["QUIT"])
    key = yearly._yearly_export_key(*arguments, metric="MAK-Veränderung",
                                    active_filters=filters, ensemble=first)
    assert key == yearly._yearly_export_key(*arguments, metric="MAK-Veränderung",
                                            active_filters=filters, ensemble=first)
    assert key != yearly._yearly_export_key(*arguments, metric="MAK-Veränderung",
                                            active_filters=filters, ensemble=second)
    filters["selected_jobfamilies"].append("Service")
    assert key != yearly._yearly_export_key(*arguments, metric="MAK-Veränderung",
                                            active_filters=filters, ensemble=first)
    assert key != yearly._yearly_export_key(*arguments, metric="Köpfe-Veränderung",
                                            active_filters={"selected_jobfamilies": ["Beratung"]},
                                            ensemble=first)
    assert yearly._yearly_export_key(*arguments) == (*arguments[:2], (2027,),
                                                     ("Beratung",), ("QUIT",), None, None)


def test_monte_carlo_jf_annual_events_match_seed_zero_and_band_later_run(monkeypatch):
    import components.jobfamily_simulation_yearly as yearly
    from utils.monte_carlo_filtered_annual_events import _samples

    baseline = pd.DataFrame([{"PersNr": "1", "Is_Vacant": False,
                              "Jobfamily": "Beratung", "JF-Cluster": "Kunden",
                              "MAK_Reporting": 1.0}])
    def event(change):
        return pd.DataFrame([{"event_date": pd.Timestamp("2027-06-01"), "Jahr": 2027,
                              "persnr": "1", "Organisationseinheit": "OE",
                              "OE-Cluster": "Cluster", "Ereignisart": "QUIT",
                              "Kategorie": "Abgang", "Ereignisquelle": "departure_events",
                              "Abgänge": -change, "Zugänge": 0,
                              "Köpfe-Veränderung": change,
                              "MAK-Veränderung": change}])
    first, second = event(-1.0), event(-2.0)
    first["Geschlecht"] = "w"
    second["Geschlecht"] = "m"
    final = baseline.copy()
    final["Is_Vacant"] = True
    final["MAK_Reporting"] = 0.0
    monkeypatch.setattr(yearly, "decode_annual_event_rows",
                        lambda blob: first.copy() if blob == b"first" else second.copy())
    monkeypatch.setattr(yearly, "decode_annual_positions", lambda _blob: [final.copy()])
    certified = []
    for key in sorted(set(_samples(first)) | set(_samples(second))):
        certified.append((*key, [_samples(first).get(key, 0.0), _samples(second).get(key, 0.0)]))
    ensemble = {"count": 2, "band_level": 0.8,
                "annual_event_rows_version": yearly.ANNUAL_EVENTS_VERSION,
                "annual_positions_version": yearly.ANNUAL_POSITIONS_VERSION,
                "annual_event_rows": [b"first", b"second"],
                "annual_positions": [b"a", b"b"],
                "statistics": {"annual_event_samples": certified}}
    reference = yearly._group_run_jobfamily_events(first, baseline, final, "Jobfamily")
    view = {"events": reference, "annual": reference.groupby(
        ["Jahr", "Jobfamily", "Kategorie", "Ereignisart"], as_index=False)[list(METRICS)].sum()}
    rows = yearly._monte_carlo_jobfamily_event_frame(
        ensemble, {"baseline_snapshot": baseline}, view, "Jobfamily", {})
    mak = rows.loc[rows["Kennzahl"].eq("MAK-Veränderung")].iloc[0]
    assert mak["Mittelwert"] == -1.5
    assert mak["P10"] == -1.9
    assert mak["P90"] == -1.1
    totals = yearly._monte_carlo_event_totals(
        rows.loc[rows["Kennzahl"].eq("MAK-Veränderung")],
        dimension="Jobfamily", metric="MAK-Veränderung", band_level=0.8)
    assert totals.loc[0, "Mittelwert"] == -1.5
    filtered_rows = yearly._monte_carlo_jobfamily_event_frame(
        ensemble, {"baseline_snapshot": baseline}, view, "Jobfamily",
        {"selected_genders": ["w"]})
    filtered_mak = filtered_rows.loc[
        filtered_rows["Kennzahl"].eq("MAK-Veränderung")].iloc[0]
    assert filtered_mak["Mittelwert"] == pytest.approx(-0.5)
    assert filtered_mak["P10"] == pytest.approx(-0.9)
    assert filtered_mak["P90"] == pytest.approx(-0.1)
    data = yearly._export_monte_carlo_events(
        rows.loc[rows["Kennzahl"].eq("MAK-Veränderung")], totals, ensemble,
        {"QUIT": yearly._monte_carlo_evaluation_frame(
            rows.attrs["event_runs"], dimension="Jobfamily", years=[2027],
            groups=["Beratung"], kinds={"QUIT"}, retirement_age=65, band_level=0.8)})
    with pd.ExcelFile(BytesIO(data)) as book:
        exported_total = pd.read_excel(book, "Ereignisse_Jahre")
        assert exported_total.loc[0, "P10"] == -1.9
        pd.testing.assert_frame_equal(exported_total, totals, check_dtype=False)
        exported_detail = pd.read_excel(book, "Ereignisarten_Jahre")
        pd.testing.assert_frame_equal(
            exported_detail,
            rows.loc[rows["Kennzahl"].eq("MAK-Veränderung")].drop(columns=["samples", "Seed 0"])
                .reset_index(drop=True), check_dtype=False)
        assert "Einordnung_Kuendigung" in book.sheet_names

    view["annual"].loc[0, "MAK-Veränderung"] = 0.0
    with pytest.raises(ValueError, match="Seed 0"):
        yearly._monte_carlo_jobfamily_event_frame(
            ensemble, {"baseline_snapshot": baseline}, view, "Jobfamily", {})


def test_monte_carlo_retirement_and_atz_evaluations_use_run_values():
    import components.jobfamily_simulation_yearly as yearly

    first = pd.DataFrame([
        {"Jahr": 2027, "Jobfamily": "Beratung", "Ereignisart": "RETIREMENT",
         "age": 64, "Abgänge": 1, "Köpfe-Veränderung": -1, "MAK-Veränderung": -0.8},
        {"Jahr": 2027, "Jobfamily": "Beratung", "Ereignisart": "ATZ_AR_TO_FR",
         "age": None, "Abgänge": 0, "Köpfe-Veränderung": 0, "MAK-Veränderung": -0.5},
    ])
    second = pd.DataFrame([
        {"Jahr": 2027, "Jobfamily": "Beratung", "Ereignisart": "RETIREMENT",
         "age": 66, "Abgänge": 1, "Köpfe-Veränderung": -1, "MAK-Veränderung": -1.0},
    ])
    retirement = yearly._monte_carlo_evaluation_frame(
        [first, second], dimension="Jobfamily", years=[2027], groups=["Beratung"],
        kinds={"RETIREMENT"}, retirement_age=65, band_level=0.8)
    early = retirement.loc[retirement["Altersgruppe"].eq("Frührente")
                           & retirement["Kennzahl"].eq("Abgänge")].iloc[0]
    assert early["Mittelwert"] == 0.5
    assert early["P10"] == pytest.approx(0.1)
    regular = retirement.loc[retirement["Altersgruppe"].eq("Reguläre Rente")
                             & retirement["Kennzahl"].eq("Abgänge")].iloc[0]
    assert regular["Mittelwert"] == 0.5
    atz = yearly._monte_carlo_evaluation_frame(
        [first, second], dimension="Jobfamily", years=[2027], groups=["Beratung"],
        kinds={"ATZ_AR_TO_FR", "ATZ_END"}, retirement_age=65, band_level=0.8)
    starts = atz.loc[atz["Kennzahl"].eq("Ereignisse")].iloc[0]
    assert starts["Mittelwert"] == 0.5
    assert starts["P90"] == 0.9


def test_jobfamily_levels_share_projection_without_changing_results(monkeypatch):
    import components.jobfamily_simulation_yearly as yearly

    context = _bridge_context("departures")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    expected = {dimension: build_jobfamily_yearly_view(
        context, summary, dimension=dimension)
        for dimension in ("Jobfamily", "JF-Cluster")}
    original = yearly.reporting_projection_base
    calls = []

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(yearly, "reporting_projection_base", counted)
    shared = {}
    for dimension in ("Jobfamily", "JF-Cluster"):
        actual = build_jobfamily_yearly_view(
            context, summary, dimension=dimension, projection_cache=shared)
        for key, value in expected[dimension].items():
            if isinstance(value, pd.DataFrame):
                pd.testing.assert_frame_equal(actual[key], value, check_exact=True)
            else:
                assert actual[key] == value
    assert len(calls) == 1
    assert "projection" in shared


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


def test_yearly_display_follows_sidebar_unit_without_local_metric_controls():
    from streamlit.testing.v1 import AppTest

    context = _bridge_context("departures")
    summary = build_simulation_event_summary(context, expected_run_signature="a")
    app = AppTest.from_string('''
import streamlit as st
from components.jobfamily_simulation_yearly import render_jobfamily_simulation_yearly
render_jobfamily_simulation_yearly(st.session_state["context"], st.session_state["summary"], run_signature="a")
''')
    app.session_state["context"] = context
    app.session_state["summary"] = summary
    app.session_state["global_metric_view"] = "MAK"
    app.run()
    assert not app.exception
    assert not any(item.label in {"Kennzahl des Bestandsverlaufs", "Kennzahl der Veränderung"}
                   for item in app.radio)
    assert any("Abgehende MAK" in frame.value.columns for frame in app.dataframe)
    assert not any("Abgehende Köpfe" in frame.value.columns for frame in app.dataframe)
    app.session_state["global_metric_view"] = "Köpfe"
    app.run()
    assert not app.exception
    assert any("Abgehende Köpfe" in frame.value.columns for frame in app.dataframe)
    assert not any("Abgehende MAK" in frame.value.columns for frame in app.dataframe)


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
    projection_calls = []
    original_projection = module.reporting_projection_base

    def counted_projection(*args):
        projection_calls.append(1)
        return original_projection(*args)

    monkeypatch.setattr(module, "reporting_projection_base", counted_projection)

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
    assert len(projection_calls) == 1
    new_context = deepcopy(context)
    _cached_yearly_view(new_context, summary, "Jobfamily")
    assert calls == ["Jobfamily", "JF-Cluster", "Jobfamily"]
    assert len(projection_calls) == 2


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
