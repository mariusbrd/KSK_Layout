"""Check presentation blocks against complete saved seed trajectories."""
from copy import deepcopy
from io import BytesIO
import pickle
import zlib
from zipfile import ZipFile

import pandas as pd
import pytest

from test_org_simulation_reconciliation import _bridge_context
from test_org_simulation_stock import _app
from utils.monte_carlo_departure_rate import departure_rate_input
from utils.monte_carlo_representative_runs import select_representative_runs
from utils.org_simulation_events import build_simulation_event_summary
from utils.org_yearly_scenario_export import build_scenario_stock_slide
from utils.org_yearly_summary_export import build_oe_summary, _oe_multiple_counts
from utils.org_yearly_view_export import (build_stock_slide_table, build_yearly_oe_bulk_zip,
                                          prepare_yearly_view_export)
from utils.org_simulation_export import build_org_simulation_export_bytes


def _case():
    summary = build_simulation_event_summary(
        _bridge_context("atz_ruhend"), expected_run_signature="a")
    view = _app(summary).session_state["view"].copy()
    annual = summary["stock_reconciliation"]["annual"]
    reported = summary["stock_reconciliation"]["reporting_events"]
    source = {}
    for level in ("Organisationseinheit", "OE-Cluster"):
        opening = annual.loc[
            annual.Ebene.eq(level) & annual.Art.eq("Ausgangsstichtag"),
            ["Organisation", "Bestand-Köpfe", "Bestand-MAK"]].rename(columns={
                "Organisation": level, "Bestand-Köpfe": "Köpfe", "Bestand-MAK": "MAK"})
        source[level] = departure_rate_input(
            level=level, opening=opening, reporting_events=reported)
    first_year = int(annual.loc[
        annual.Art.eq("Jahresstichtag")
        & pd.to_datetime(annual.Stichtag).gt(pd.Timestamp(summary["metadata"]["base_date"])),
        "Jahr"].min())
    first_org = next(iter(source["Organisationseinheit"]["opening"]))
    rate_sources = []
    for index in range(3):
        entry = deepcopy(source)
        if index:
            entry["Organisationseinheit"]["losses"].append(
                (first_year, first_org, "QUIT", float(index), float(index)))
        rate_sources.append(entry)
    stocks, selection_stocks = [], []
    for _, row in annual.loc[annual.Art.eq("Jahresstichtag")].iterrows():
        date = pd.Timestamp(row["Stichtag"]).date().isoformat()
        for code, column in (("heads", "Bestand-Köpfe"), ("mak", "Bestand-MAK")):
            value = float(row[column])
            record = (date, row["Ebene"], row["Organisation"], code,
                      [value, value - 0.5, value - 1.0])
            stocks.append(record)
            if row["Ebene"] == "Organisationseinheit":
                selection_stocks.append(record)
    quit_reference = sum(value[3] for value in source["Organisationseinheit"]["losses"]
                         if value[2] == "QUIT")
    ensemble = {
        "status": "complete", "count": 3, "band_level": 0.8,
        "seed_pairs": ((42, 42), (43, 43), (44, 44)),
        "org_filter_snapshot_version": 1,
        "org_filter_snapshots": [zlib.compress(pickle.dumps(
            summary["stock_basis"]["final_snapshot"], protocol=pickle.HIGHEST_PROTOCOL))] * 3,
        "statistics": {
            "status": "complete", "requested_count": 3,
            "departure_stock_samples": stocks,
            "departure_rate_inputs_by_seed": rate_sources,
            "annual_stock_samples": selection_stocks,
            "annual_event_samples": [
                (first_year, "Organisationseinheit", first_org, "QUIT", "Abgänge",
                 [quit_reference + index for index in range(3)])],
        },
    }
    view["monte_carlo_bands"] = pd.DataFrame({
        "Stichtag": [str(first_year)], "Organisation": ["Gesamt"],
        "Referenzlauf": [0.0], "mean": [0.0], "lower": [0.0], "upper": [0.0],
    })
    view["monte_carlo_metadata"] = {"Anzahl Läufe": 3, "Band": "P10–P90"}
    return summary, view, ensemble, first_year


def test_four_blocks_keep_mean_bands_and_complete_selected_paths():
    summary, view, ensemble, year = _case()
    choices = select_representative_runs(ensemble)
    assert {key: choice["index"] for key, choice in choices.items()} == {
        "best": 0, "typical": 1, "worst": 2}
    tables, context = prepare_yearly_view_export(
        summary, view, stock=True, monte_carlo_ensemble=ensemble)
    assert list(tables)[:2] == ["Bestand_Folienvorlage_MAK", "Bestand_Folienvorlage_Koepfe"]
    assert "Ref_Bestand_Folienvorlage_MAK" in tables
    for sheet in ("Bestand_Folienvorlage_MAK", "Bestand_Folienvorlage_Koepfe"):
        frame = tables[sheet]
        base_year = pd.Timestamp(summary["metadata"]["base_date"]).year
        opening = frame.loc[frame.Kennzahl.eq("Verbleibender heutiger Bestand")
                            & frame.Ausprägung.eq("Mittelwert"), "Start"].iloc[0]
        assert frame.loc[frame.Kennzahl.eq("Verbleibender heutiger Bestand")
                         & frame.Ausprägung.eq("Mittelwert"), base_year].iloc[0] == opening
        assert frame.Szenario.dropna().drop_duplicates().tolist() == [
            "Mittelwert aller Läufe", "Fluktuation max",
            "Fluktuation Mean: typischer Einzellauf", "Fluktuation min"]
        assert frame.loc[frame.Szenario.eq("Fluktuation max"), "Laufindex"].eq(3).all()
        assert frame.loc[frame.Szenario.eq("Fluktuation min"), "Laufindex"].eq(1).all()
        assert frame.loc[frame.Szenario.eq("Fluktuation Mean: typischer Einzellauf"),
                         "Laufindex"].eq(2).all()
        band = frame.loc[frame.Kennzahl.eq("Abgänge durch Fluktuation")
                         & frame.Szenario.eq("Mittelwert aller Läufe")].set_index("Ausprägung")
        assert band.loc["P10", year] <= band.loc["Mittelwert", year] <= band.loc["P90", year]
        assert frame.loc[frame.Kennzahl.eq("Verbleibender heutiger Bestand"), year].notna().all()
        for label in ("Renteneintritte (inkl. ATZ)", "Rente (direkt)",
                      "Rente nach ATZ-Vertragsende", "ATZ-Freistellung"):
            selected = frame.loc[frame.Kennzahl.eq(label)]
            assert selected.Szenario.dropna().nunique() == 4
            assert selected[year].notna().all()
            assert selected.loc[selected.Szenario.eq("Fluktuation min"), year].iloc[0] == pytest.approx(
                build_stock_slide_table(summary, view, metric=sheet.rsplit("_", 1)[-1]
                                        if sheet.endswith("_MAK") else "Köpfe")
                .set_index("Kennzahl").loc[label, year])
    book = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(tables, context)))
    assert book.sheet_names[1:3] == ["01_Bestand_Folienvorlage_MAK",
                                     "02_Bestand_Folienvorlage_Koepfe"]


def test_missing_per_seed_reporting_values_fail_closed():
    summary, view, ensemble, _ = _case()
    ensemble["statistics"]["departure_rate_inputs_by_seed"] = None
    with pytest.raises(ValueError, match="Fluktuationswerte"):
        build_scenario_stock_slide(summary, view, ensemble, metric="MAK")


def test_oe_summary_has_all_four_views_and_matching_reference_values():
    summary, _, ensemble, _ = _case()
    annual = summary["stock_reconciliation"]["annual"]
    oe = annual.loc[annual.Ebene.eq("Organisationseinheit"), "Organisation"].iloc[0]
    view = {"level": "Organisationseinheit", "organizations": [oe]}
    matrix = build_oe_summary(summary, view, ensemble)
    assert list(matrix.columns) == [
        "Kennzahl / Information", "Einheit", "Mittelwert", "P10", "P90",
        "Geringste Fluktuation", "Typischer Einzellauf", "Höchste Fluktuation"]
    assert matrix.loc[matrix["Kennzahl / Information"].eq("Laufindex"),
                      "Geringste Fluktuation"].iloc[0] == 1
    assert matrix.loc[matrix["Kennzahl / Information"].eq("Laufindex"),
                      "Höchste Fluktuation"].iloc[0] == 3
    assert matrix["Kennzahl / Information"].eq("Durchschnittsalter heute").sum() == 2
    assert matrix["Kennzahl / Information"].eq("Durchschnittsalter am Zielstichtag").sum() == 2
    assert matrix.loc[matrix["Kennzahl / Information"].eq("Durchschnittsalter heute"),
                      "Mittelwert"].notna().all()
    assert matrix.loc[matrix["Kennzahl / Information"].eq("Durchschnittsalter am Zielstichtag"),
                      "Mittelwert"].notna().all()
    assert matrix.loc[matrix["Kennzahl / Information"].eq("Kündigungen bis Zielstichtag"),
                      "Geringste Fluktuation"].notna().all()
    single = build_oe_summary(summary, view)
    assert list(single.columns) == ["Kennzahl / Information", "Einheit", "Einzellauf"]


def test_empty_seed_does_not_erase_age_of_typical_complete_run():
    summary, _, ensemble, _ = _case()
    oe = summary["stock_reconciliation"]["annual"].loc[
        lambda frame: frame.Ebene.eq("Organisationseinheit"), "Organisation"].iloc[0]
    empty_target = summary["stock_basis"]["final_snapshot"].iloc[0:0].copy()
    ensemble["org_filter_snapshots"][2] = zlib.compress(
        pickle.dumps(empty_target, protocol=pickle.HIGHEST_PROTOCOL))
    matrix = build_oe_summary(summary, {
        "level": "Organisationseinheit", "organizations": [oe]}, ensemble)
    age = matrix.loc[matrix["Kennzahl / Information"].eq(
        "Durchschnittsalter am Zielstichtag")].iloc[0]
    from utils.org_yearly_summary_export import _age
    from utils.monte_carlo_filtered_org import decode_snapshot
    typical_index = select_representative_runs(ensemble)["typical"]["index"]
    expected_age = _age(
        decode_snapshot(ensemble["org_filter_snapshots"][typical_index]),
        "Organisationseinheit", {oe}, summary["metadata"]["target_date"])
    assert age["Typischer Einzellauf"] == pytest.approx(expected_age)
    assert pd.notna(age["Mittelwert"])
    assert pd.isna(age["Höchste Fluktuation"])
    count = matrix.loc[matrix["Kennzahl / Information"].eq(
        "Läufe mit berechenbarem Zielalter")].iloc[0]
    assert count["Mittelwert"] == 2


def test_oe_multiple_count_explains_cross_oe_stock_loss_without_changing_departures():
    baseline = pd.DataFrame({
        "PersNr": [1, 1, 2], "Organisationseinheit": ["A", "B", "B"],
        "Is_Vacant": [False, False, False],
    })
    final = baseline.loc[baseline.PersNr.eq(2)].copy()
    selected = {"A", "B"}
    initial_extra = _oe_multiple_counts(baseline, "Organisationseinheit", selected)
    final_extra = _oe_multiple_counts(final, "Organisationseinheit", selected)
    assert (initial_extra, final_extra) == (1, 0)
    assert 3 - 1 == 1 + (initial_extra - final_extra)


def test_selected_single_path_export_still_contains_four_view_overview():
    summary, _, ensemble, _ = _case()
    app = _app(summary)
    app.radio(key="org_stock_level").set_value("Organisationseinheit").run(timeout=15)
    view = app.session_state["view"]
    tables, _ = prepare_yearly_view_export(
        summary, view, stock=True, overview_summary=summary, overview_ensemble=ensemble)
    overview = tables["Zusammenfassung"]
    assert "Geringste Fluktuation" in overview
    assert "Höchste Fluktuation" in overview
    book = pd.ExcelFile(BytesIO(build_org_simulation_export_bytes(tables, {})))
    assert book.sheet_names[:3] == ["00_Zusammenfassung", "01_Bestand_Folienvorlage_MAK",
                                   "02_Bestand_Folienvorlage_Koepfe"]


def test_selected_single_path_export_uses_original_ensemble_for_annual_slides():
    original, view, ensemble, _ = _case()
    selected = deepcopy(original)
    selected["run_signature"] = "selected-worst-path"
    selected_annual = selected["stock_reconciliation"]["annual"]
    selected_annual.loc[selected_annual["Art"].eq("Jahresstichtag"), "Bestand-MAK"] += 7.0
    view = dict(view)
    view["run_signature"] = selected["run_signature"]
    view.pop("monte_carlo_bands")
    view.pop("monte_carlo_metadata")
    tables, _ = prepare_yearly_view_export(
        selected, view, stock=True, overview_summary=original,
        overview_ensemble=ensemble)
    slide = tables["Bestand_Folienvorlage_MAK"]
    assert set(slide["Szenario"].dropna()) == {
        "Mittelwert aller Läufe", "Fluktuation max",
        "Fluktuation Mean: typischer Einzellauf", "Fluktuation min"}
    assert slide.loc[slide["Szenario"].eq("Fluktuation Mean: typischer Einzellauf"),
                     "Laufindex"].eq(select_representative_runs(ensemble)["typical"]["index"] + 1).all()
    assert slide.loc[slide["Szenario"].eq("Fluktuation Mean: typischer Einzellauf"),
                     "Seed Abgänge"].eq(43).all()
    assert "Ref_Bestand_Folienvorlage_MAK" in tables


def test_bulk_zip_keeps_four_complete_path_blocks_per_oe():
    summary, _, ensemble, _ = _case()
    app = _app(summary)
    app.radio(key="org_stock_level").set_value("Organisationseinheit").run()
    view = app.session_state["view"].copy()
    view["monte_carlo_bands"] = pd.DataFrame({
        "Stichtag": view["chart"]["Stichtag"].astype(str),
        "Organisation": view["chart"]["Organisation"],
        "Referenzlauf": view["chart"][f"Bestand-{view['metric']}"],
        "mean": view["chart"][f"Bestand-{view['metric']}"],
        "lower": view["chart"][f"Bestand-{view['metric']}"],
        "upper": view["chart"][f"Bestand-{view['metric']}"],
    })
    view["monte_carlo_metadata"] = {"Anzahl Läufe": 3, "Band": "P10–P90"}
    with ZipFile(BytesIO(build_yearly_oe_bulk_zip(
            summary, view, stock=True, monte_carlo_ensemble=ensemble))) as archive:
        assert archive.namelist()[0] == "00_Gesamt_ausgewaehlte_Einheiten.xlsx"
        assert len(archive.namelist()) == len(view["organizations"]) + 1
        for oe, name in zip(view["organizations"], archive.namelist()[1:]):
            assert oe in name
            book = pd.ExcelFile(BytesIO(archive.read(name)))
            assert book.sheet_names[0] == "00_Zusammenfassung"
            overview = pd.read_excel(book, "00_Zusammenfassung")
            assert overview.loc[overview["Kennzahl / Information"].eq("Organisation(en)"),
                                "Mittelwert"].iloc[0] == oe
            assert overview["Kennzahl / Information"].eq("Durchschnittsalter am Zielstichtag").sum() == 2
            for label in ("Zusätzliche OE-Zählungen im Ausgangsbestand",
                          "Zusätzliche OE-Zählungen am Zielstichtag",
                          "Rückgang zusätzlicher OE-Zählungen"):
                row = overview.loc[overview["Kennzahl / Information"].eq(label)]
                assert len(row) == 1
                assert row["Mittelwert"].notna().all()
            slide = pd.read_excel(book, "01_Bestand_Folienvorlage_MAK")
            assert slide.Szenario.dropna().drop_duplicates().tolist() == [
                "Mittelwert aller Läufe", "Fluktuation max",
                "Fluktuation Mean: typischer Einzellauf", "Fluktuation min"]
            for scenario in slide.Szenario.dropna().unique():
                labels = set(slide.loc[slide.Szenario.eq(scenario), "Kennzahl"])
                assert {"Renteneintritte (inkl. ATZ)", "Rente (direkt)",
                        "Rente nach ATZ-Vertragsende", "ATZ-Freistellung"} <= labels


def test_cluster_bulk_zip_keeps_monte_carlo_summary_and_paths():
    summary, _, ensemble, _ = _case()
    app = _app(summary)
    app.radio(key="org_stock_level").set_value("OE-Cluster").run()
    assert not app.exception
    view = app.session_state["view"]
    view["monte_carlo_bands"] = pd.DataFrame({
        "Stichtag": view["chart"]["Stichtag"].astype(str),
        "Organisation": view["chart"]["Organisation"],
        "Referenzlauf": view["chart"][f"Bestand-{view['metric']}"],
        "mean": view["chart"][f"Bestand-{view['metric']}"],
        "lower": view["chart"][f"Bestand-{view['metric']}"],
        "upper": view["chart"][f"Bestand-{view['metric']}"],
    })
    view["monte_carlo_metadata"] = {"Anzahl Läufe": 3, "Band": "P10–P90"}
    with ZipFile(BytesIO(build_yearly_oe_bulk_zip(
            summary, view, stock=True, monte_carlo_ensemble=ensemble))) as archive:
        assert archive.namelist()[0] == "00_Gesamt_ausgewaehlte_Einheiten.xlsx"
        assert len(archive.namelist()) == len(view["organizations"]) + 1
        for cluster, name in zip(view["organizations"], archive.namelist()[1:]):
            assert cluster in name
            book = pd.ExcelFile(BytesIO(archive.read(name)))
            overview = pd.read_excel(book, "00_Zusammenfassung")
            assert overview.loc[overview["Kennzahl / Information"].eq("Organisation(en)"),
                                "Mittelwert"].iloc[0] == cluster
            slide = pd.read_excel(book, "01_Bestand_Folienvorlage_MAK")
            assert slide.Szenario.dropna().nunique() == 4


def test_bulk_zip_total_sums_same_complete_paths_as_individual_workbooks():
    summary, _, ensemble, _ = _case()
    app = _app(summary)
    app.radio(key="org_stock_level").set_value("Organisationseinheit").run()
    view = app.session_state["view"]
    selected = dict(view)
    selected["organizations"] = view["organizations"][:2]
    with ZipFile(BytesIO(build_yearly_oe_bulk_zip(
            summary, selected, stock=True, monte_carlo_ensemble=ensemble))) as archive:
        assert len(archive.namelist()) == 3
        books = [pd.ExcelFile(BytesIO(archive.read(name))) for name in archive.namelist()]
        slides = [pd.read_excel(book, "01_Bestand_Folienvorlage_MAK") for book in books]
        years = [column for column in slides[0] if isinstance(column, int)]
        for scenario in ("Mittelwert aller Läufe", "Fluktuation max",
                         "Fluktuation Mean: typischer Einzellauf", "Fluktuation min"):
            def value(slide, year):
                row = slide.loc[slide.Szenario.eq(scenario) &
                                slide.Kennzahl.eq("Verbleibender heutiger Bestand")]
                if scenario == "Mittelwert aller Läufe":
                    row = row.loc[row["Ausprägung"].eq("Mittelwert")]
                return float(row[year].iloc[0])
            for year in years:
                assert value(slides[0], year) == pytest.approx(
                    value(slides[1], year) + value(slides[2], year))
        year = years[-1]
        overview = pd.read_excel(books[0], "00_Zusammenfassung")
        assert overview.loc[overview["Kennzahl / Information"].eq("Organisation(en)"),
                            "Mittelwert"].iloc[0] == ", ".join(selected["organizations"])
        assert overview.loc[overview["Kennzahl / Information"].eq("Bestand am Zielstichtag")
                            & overview.Einheit.eq("MAK"),
                            "Typischer Einzellauf"].iloc[0] == pytest.approx(float(slides[0].loc[
                                slides[0].Szenario.eq("Fluktuation Mean: typischer Einzellauf") &
                                slides[0].Kennzahl.eq("Verbleibender heutiger Bestand"), year].iloc[0]))


def test_real_compact_plus_multirun_slide_matches_approved_seed_zero():
    from test_monte_carlo_parameter_ui import _compact_app
    from utils.org_yearly_view_export import build_stock_slide_table

    app = _compact_app(2)
    app.session_state["test_start_simulation"] = True
    app.run()
    assert not app.exception
    summary = app.session_state["compact_sim_event_summary"]
    ensemble = app.session_state["monte_carlo_ensemble_result"]
    annual = summary["stock_reconciliation"]["annual"]
    view = {
        "level": "Gesamt", "organizations": ["Gesamt"],
        "years": sorted(annual.loc[annual.Art.eq("Jahresstichtag"), "Jahr"].unique()),
        "metric": "MAK", "run_signature": summary["run_signature"],
    }
    for metric in ("MAK", "Köpfe"):
        slide = build_scenario_stock_slide(summary, view, ensemble, metric=metric)
        reference = build_stock_slide_table(summary, view, metric=metric).set_index("Kennzahl")
        selected = slide.loc[slide.Szenario.eq("Fluktuation min")]
        if select_representative_runs(ensemble)["best"]["index"] == 0:
            for year in slide.columns[8:]:
                actual = selected.loc[
                    selected.Kennzahl.eq("Verbleibender heutiger Bestand"), year].iloc[0]
                assert actual == pytest.approx(reference.loc[
                    "Verbleibender heutiger Bestand", year])
    oe = annual.loc[annual.Ebene.eq("Organisationseinheit"), "Organisation"].iloc[0]
    overview = build_oe_summary(
        summary, {"level": "Organisationseinheit", "organizations": [oe]}, ensemble)
    assert overview["Kennzahl / Information"].eq("Durchschnittsalter am Zielstichtag").sum() == 2
    assert overview.loc[overview["Kennzahl / Information"].eq("Bestand am Zielstichtag")
                        & overview.Einheit.eq("MAK"), "Mittelwert"].notna().all()


def test_real_ten_run_oe_summary_reconciles_every_path():
    from test_monte_carlo_parameter_ui import _compact_app
    from utils.monte_carlo_filtered_org import decode_snapshot
    from utils.org_yearly_summary_export import _age, _oe_multiple_counts, _seed_headlines

    app = _compact_app(10, "2031-12-31")
    app.session_state["test_start_simulation"] = True
    app.run()
    assert not app.exception
    summary = app.session_state["compact_sim_event_summary"]
    ensemble = app.session_state["monte_carlo_ensemble_result"]
    assert ensemble["status"] == "complete" and ensemble["count"] == 10
    level = "Organisationseinheit"
    groups = sorted(summary["stock_reconciliation"]["annual"].loc[
        lambda frame: frame.Ebene.eq(level) & frame.Art.eq("Ausgangsstichtag"),
        "Organisation"].tolist())
    view = {"level": level, "organizations": groups}
    matrix = build_oe_summary(summary, view, ensemble)
    choice = select_representative_runs(ensemble)
    columns = {"best": "Geringste Fluktuation", "typical": "Typischer Einzellauf",
               "worst": "Höchste Fluktuation"}
    baseline = summary["stock_basis"]["baseline_snapshot"]
    initial_extra = _oe_multiple_counts(baseline, level, set(groups))
    frames = [decode_snapshot(blob) for blob in ensemble["org_filter_snapshots"]]
    future_extra = [_oe_multiple_counts(frame, level, set(groups)) for frame in frames]

    def cell(label, metric, column, section="Köpfe"):
        rows = matrix.loc[matrix["Kennzahl / Information"].eq(label)
                          & matrix.Einheit.eq(metric), column]
        if len(rows) == 2 and metric in {"%", "Jahre"}:
            rows = rows.iloc[[0 if section == "MAK" else 1]]
        assert len(rows) == 1
        return float(rows.iloc[0])

    for metric in ("Köpfe", "MAK"):
        samples = _seed_headlines(summary, ensemble, level, groups, 2031, metric)
        for key, label in (("initial", "Ausgangsbestand"),
                           ("final", "Bestand am Zielstichtag"),
                           ("retirement_atz", "Rente inkl. ATZ bis Zielstichtag"),
                           ("quit", "Kündigungen bis Zielstichtag"),
                           ("departure_share", "Abgänge / Ausgangsbestand")):
            values = samples[key]
            unit = "%" if key == "departure_share" else metric
            assert cell(label, unit, "Mittelwert", metric) == pytest.approx(sum(values) / 10)
            assert cell(label, unit, "P10", metric) == pytest.approx(pd.Series(values).quantile(.1))
            assert cell(label, unit, "P90", metric) == pytest.approx(pd.Series(values).quantile(.9))
            for name, column in columns.items():
                assert cell(label, unit, column, metric) == pytest.approx(values[choice[name]["index"]])
        if metric == "Köpfe":
            for index in range(10):
                # The OE stock counts one person in each occupied OE, while
                # departure events count the person once companywide.
                stock_loss = samples["initial"][index] - samples["final"][index]
                reported_loss = (samples["departure_share"][index]
                                 * samples["initial"][index])
                assert stock_loss - reported_loss == pytest.approx(
                    initial_extra - future_extra[index], abs=1e-6)
            assert cell("Rückgang zusätzlicher OE-Zählungen", metric, "Mittelwert") == pytest.approx(
                sum(initial_extra - value for value in future_extra) / 10)
            for label, values in (
                ("Zusätzliche OE-Zählungen am Zielstichtag", future_extra),
                ("Rückgang zusätzlicher OE-Zählungen",
                 [initial_extra - value for value in future_extra]),
            ):
                assert cell(label, metric, "P10") == pytest.approx(pd.Series(values).quantile(.1))
                assert cell(label, metric, "P90") == pytest.approx(pd.Series(values).quantile(.9))
                for name, column in columns.items():
                    assert cell(label, metric, column) == pytest.approx(values[choice[name]["index"]])
    target = pd.Timestamp(summary["metadata"]["target_date"])
    ages = [_age(frame, level, set(groups), target) for frame in frames]
    assert cell("Durchschnittsalter am Zielstichtag", "Jahre", "Mittelwert") == pytest.approx(
        sum(ages) / 10)
    assert cell("Durchschnittsalter am Zielstichtag", "Jahre", "P10") == pytest.approx(
        pd.Series(ages).quantile(.1))
    assert cell("Durchschnittsalter am Zielstichtag", "Jahre", "P90") == pytest.approx(
        pd.Series(ages).quantile(.9))
    for name, column in columns.items():
        assert cell("Durchschnittsalter am Zielstichtag", "Jahre", column) == pytest.approx(
            ages[choice[name]["index"]])


