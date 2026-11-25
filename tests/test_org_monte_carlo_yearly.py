"""OE detail bands retain the reference and each selected annual interval."""
from io import BytesIO

import pandas as pd

from components.org_monte_carlo_yearly import (
    annual_band_bulk_export_bytes, annual_band_export_bytes,
    annual_band_export_tables, build_annual_band_figure,
)
from test_monte_carlo_representative_runs import _ensemble


def test_thinkcell_export_matches_plotted_values_and_keeps_other_groups_out():
    bands = pd.DataFrame({
        "Stichtag": ["2026-09-01", "2026-12-31", "2027-12-31", "2027-12-31"],
        "Organisation": ["A", "A", "A", "B"],
        "Referenzlauf": [10, 8, 6, 100], "mean": [10, 9, 7, 100],
        "lower": [10, 7, 5, 99], "upper": [10, 10, 9, 101],
        "p25": [10, 8, 6, 99], "median": [10, 9, 7, 100],
        "p75": [10, 9.5, 8, 101],
    })
    matrix, info = annual_band_export_tables(
        bands, group_col="Organisation", group="A", x_col="Stichtag", unit="MAK")
    assert list(matrix) == ["Datenreihe", "Start", 2026, 2027]
    assert matrix.loc[matrix.Datenreihe.eq("Mittelwert"), ["Start", 2026, 2027]].iloc[0].tolist() == [10, 9, 7]
    assert matrix.loc[matrix.Datenreihe.eq("P90"), 2027].iloc[0] == 9
    assert info.loc[info.Merkmal.eq("Auswahl"), "Wert"].iloc[0] == "A"
    book = pd.ExcelFile(BytesIO(annual_band_export_bytes(
        bands, group_col="Organisation", group="A", x_col="Stichtag", unit="MAK")))
    assert book.sheet_names == ["01_Liniendiagramm", "02_Informationen"]
    assert pd.read_excel(book, "01_Liniendiagramm").loc[0, 2027] == 7


def test_thinkcell_export_uses_same_complete_run_indices_as_zip_export():
    ensemble = _ensemble()
    bands = pd.DataFrame({
        "Stichtag": ["2026-12-31", "2027-12-31"], "Organisation": ["A", "A"],
        "Referenzlauf": [80, 60], "mean": [70, 70],
        "lower": [60, 60], "upper": [80, 80],
        "p25": [65, 65], "median": [70, 70], "p75": [75, 75],
    })
    bands.attrs["run_samples"] = {
        ("2026-12-31", "A"): [80, 60, 70],
        ("2027-12-31", "A"): [60, 80, 70],
    }
    matrix, metadata = annual_band_export_tables(
        bands, group_col="Organisation", group="A", x_col="Stichtag",
        unit="MAK", ensemble=ensemble)
    assert matrix.loc[matrix.Datenreihe.eq("Typischer Einzellauf (Lauf 3)"),
                      [2026, 2027]].iloc[0].tolist() == [70, 70]
    assert matrix.loc[matrix.Datenreihe.eq("Höchste Fluktuation (Lauf 1)"),
                      [2026, 2027]].iloc[0].tolist() == [80, 60]
    assert metadata.loc[metadata.Merkmal.eq("Typischer Einzellauf"),
                        "Wert"].iloc[0] == "Lauf 3; Seeds 44/73"


def test_bulk_thinkcell_export_has_all_groups_and_per_run_total():
    ensemble = _ensemble()
    ensemble["band_level"] = 0.8
    ensemble["statistics"]["departure_stock_samples"] = [
        (date, "Organisationseinheit", group, "mak", values)
        for date, a, b in (("2026-12-31", [80, 60, 70], [20, 30, 40]),
                           ("2027-12-31", [60, 80, 70], [10, 20, 30]))
        for group, values in (("A", a), ("B", b))
    ]
    # The visible chart may contain only A; the workbook must still include B.
    visible = pd.DataFrame({
        "Stichtag": ["2026-12-31", "2027-12-31"], "Organisation": ["A", "A"],
        "Referenzlauf": [80, 60], "mean": [70, 70], "lower": [60, 60],
        "upper": [80, 80], "p25": [65, 65], "median": [70, 70], "p75": [75, 75],
    })
    book = pd.ExcelFile(BytesIO(annual_band_bulk_export_bytes(
        visible, group_col="Organisation", x_col="Stichtag", unit="Bestand-MAK",
        ensemble=ensemble, level="Organisationseinheit")))
    assert book.sheet_names[:3] == ["01_Gesamt", "02_A", "03_B"]
    total = pd.read_excel(book, "01_Gesamt").set_index("Datenreihe")
    assert total.loc["Typischer Einzellauf (Lauf 3)", [2026, 2027]].tolist() == [110, 100]
    assert total.loc["Höchste Fluktuation (Lauf 1)", [2026, 2027]].tolist() == [100, 70]
    # Aggregate quantiles come from [100, 90, 110], not from summed group quantiles.
    assert total.loc["Mittelwert", 2026] == 100


def test_bulk_event_export_respects_selected_event_types_for_all_groups():
    ensemble = _ensemble()
    ensemble["band_level"] = 0.8
    ensemble["statistics"]["annual_event_samples"].extend([
        (2026, "Organisationseinheit", "B", "QUIT", "Abgänge", [1, 2, 3]),
        (2027, "Organisationseinheit", "B", "QUIT", "Abgänge", [4, 5, 6]),
    ])
    visible = pd.DataFrame({
        "Jahr": [2026, 2027], "Organisationseinheit": ["A", "A"],
        "Referenzlauf": [0, 9], "mean": [0, 9], "lower": [0, 0],
        "upper": [8, 9], "p25": [0, 0], "median": [0, 9], "p75": [8, 9],
    })
    book = pd.ExcelFile(BytesIO(annual_band_bulk_export_bytes(
        visible, group_col="Organisationseinheit", x_col="Jahr", unit="Abgänge",
        ensemble=ensemble, level="Organisationseinheit", event_types=("QUIT",))))
    total = pd.read_excel(book, "01_Gesamt").set_index("Datenreihe")
    assert total.loc["Typischer Einzellauf (Lauf 3)", [2026, 2027]].tolist() == [6, 9]


def test_bulk_uses_saved_global_total_when_heads_overlap_across_oes():
    ensemble = _ensemble()
    ensemble["band_level"] = 0.8
    ensemble["statistics"]["departure_stock_samples"] = [
        ("2026-12-31", "Organisationseinheit", "A", "heads", [5, 5, 5]),
        ("2026-12-31", "Organisationseinheit", "B", "heads", [5, 5, 5]),
        ("2026-12-31", "Gesamt", "Gesamt", "heads", [9, 9, 9]),
    ]
    visible = pd.DataFrame({
        "Stichtag": ["2026-12-31"], "Organisation": ["A"],
        "Referenzlauf": [5], "mean": [5], "lower": [5],
        "upper": [5], "p25": [5], "median": [5], "p75": [5],
    })
    book = pd.ExcelFile(BytesIO(annual_band_bulk_export_bytes(
        visible, group_col="Organisation", x_col="Stichtag", unit="Bestand-Köpfe",
        ensemble=ensemble, level="Organisationseinheit")))
    total = pd.read_excel(book, "01_Gesamt").set_index("Datenreihe")
    assert total.loc["Typischer Einzellauf (Lauf 3)", 2026] == 9


def test_annual_detail_uses_only_chosen_oe_and_all_its_years():
    bands = pd.DataFrame({
        "Jahr": [2027, 2028, 2027], "Organisationseinheit": ["A", "A", "B"],
        "Referenzlauf": [7, 6, 100], "mean": [8, 7, 101],
        "lower": [6, 5, 99], "upper": [10, 9, 103],
        "p25": [7, 6, 100], "median": [8, 7, 101], "p75": [9, 8, 102],
    })
    fig = build_annual_band_figure(
        bands, group_col="Organisationseinheit", group="A", x_col="Jahr", unit="MAK")
    assert list(fig.data[0].x) == [2027, 2028]
    assert list(fig.data[0].y) == [10, 9]
    assert list(fig.data[1].y) == [6, 5]
    assert fig.data[1].fill == "tonexty"
    assert list(fig.data[2].y) == [9, 8]
    assert list(fig.data[3].y) == [7, 6]
    assert list(fig.data[4].y) == [8, 7]
    assert list(fig.data[5].y) == [8, 7]
    assert list(fig.data[6].y) == [7, 6]
    assert fig.data[6].name == "Referenzlauf"


def test_single_year_still_has_visible_range():
    bands = pd.DataFrame({
        "Stichtag": ["2027-12-31"], "Organisation": ["A"],
        "Referenzlauf": [7], "mean": [8], "lower": [6], "upper": [10],
        "p25": [7], "median": [8], "p75": [9],
    })
    fig = build_annual_band_figure(
        bands, group_col="Organisation", group="A", x_col="Stichtag", unit="Bestand-MAK")
    assert list(fig.data[-1].y) == [6, 10]
    assert fig.data[-1].line.color == "#6b7280"


def test_streamlit_detail_selector_switches_oe_without_changing_year_rows():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string('''
import pandas as pd
from components.org_monte_carlo_yearly import render_annual_band_detail
bands = pd.DataFrame({
    "Jahr": [2027, 2028, 2027, 2028],
    "Organisationseinheit": ["A", "A", "B", "B"],
    "Referenzlauf": [7, 6, 4, 3], "mean": [8, 7, 5, 4],
    "lower": [6, 5, 3, 2], "upper": [10, 9, 7, 6],
    "p25": [7, 6, 4, 3], "median": [8, 7, 5, 4], "p75": [9, 8, 6, 5],
})
render_annual_band_detail(bands, group_col="Organisationseinheit",
                          x_col="Jahr", unit="MAK", key="test_oe_year_detail")
''').run()
    assert not app.exception
    assert app.dataframe[0].value["Jahr"].tolist() == [2027, 2028]
    assert app.dataframe[0].value["Organisationseinheit"].tolist() == ["A", "A"]
    app.selectbox(key="test_oe_year_detail").set_value("B").run()
    assert not app.exception
    assert app.dataframe[0].value["Organisationseinheit"].tolist() == ["B", "B"]
