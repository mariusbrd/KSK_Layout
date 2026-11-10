"""OE detail bands retain the reference and each selected annual interval."""
import pandas as pd

from components.org_monte_carlo_yearly import build_annual_band_figure


def test_annual_detail_uses_only_chosen_oe_and_all_its_years():
    bands = pd.DataFrame({
        "Jahr": [2027, 2028, 2027], "Organisationseinheit": ["A", "A", "B"],
        "Referenzlauf": [7, 6, 100], "mean": [8, 7, 101],
        "lower": [6, 5, 99], "upper": [10, 9, 103],
    })
    fig = build_annual_band_figure(
        bands, group_col="Organisationseinheit", group="A", x_col="Jahr", unit="MAK")
    assert list(fig.data[0].x) == [2027, 2028]
    assert list(fig.data[0].y) == [10, 9]
    assert list(fig.data[1].y) == [6, 5]
    assert fig.data[1].fill == "tonexty"
    assert list(fig.data[2].y) == [8, 7]
    assert list(fig.data[3].y) == [7, 6]
    assert fig.data[3].name == "Referenzlauf"


def test_single_year_still_has_visible_range():
    bands = pd.DataFrame({
        "Stichtag": ["2027-12-31"], "Organisation": ["A"],
        "Referenzlauf": [7], "mean": [8], "lower": [6], "upper": [10],
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
