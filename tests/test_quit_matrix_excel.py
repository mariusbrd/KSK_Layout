from copy import deepcopy
from io import BytesIO
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from abgaenge.params import build_params_from_ui, default_params
from abgaenge.forecast import run_forecast_abgaenge, _select_quit_probs_frame
from utils.quit_matrix_excel import (
    COHORTS, AGE_COLUMNS, RATE_COLUMN, as_table, export_quit_template,
    has_age_differences, matrix_from_table, merge_quit_updates,
    parse_quit_upload, preview_quit_updates,
)

ROOT = Path(__file__).resolve().parents[1]


def frame(dimension="OrgUnit", rates=(3, 3, 3, 3)):
    return pd.DataFrame([
        {dimension: name, **dict(zip(COHORTS, map(float, rates)))}
        for name in ("Default", "0010", "IT")
    ])


def workbook(table, sheet="Quoten"):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        table.to_excel(writer, sheet_name=sheet, index=False)
    return output.getvalue()


@pytest.mark.parametrize("dimension", ["OrgUnit", "JobFamily"])
@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("rates", [(0, 0, 0, 0), (.5, .5, .5, .5), (3, 3, 3, 3), (0, .5, 12, 100)])
def test_export_import_preserves_exact_matrix(dimension, indexed, rates):
    original = frame(dimension, rates)
    if indexed:
        original = original.set_index(dimension)
    detailed = has_age_differences(original, dimension)
    updates = parse_quit_upload(export_quit_template(original, dimension, detailed), dimension,
                                ["Default", "0010", "IT"])
    result = merge_quit_updates(original, updates, dimension)
    pd.testing.assert_frame_equal(result, original, check_dtype=False)
    assert preview_quit_updates(original, updates, dimension).empty


def test_partial_simple_import_preserves_other_rows_and_supports_german_decimal():
    original = frame(rates=(12, 8, 5, 2))
    before = original.copy(deep=True)
    updates = parse_quit_upload(workbook(pd.DataFrame({"OrgUnit": ["0010"], RATE_COLUMN: ["0,5"]})),
                                "OrgUnit", ["0010"])
    result = merge_quit_updates(original, updates, "OrgUnit")
    assert result.loc[1, list(COHORTS)].tolist() == [.5] * 4
    pd.testing.assert_frame_equal(result.iloc[[0, 2]], original.iloc[[0, 2]])
    pd.testing.assert_frame_equal(original, before)
    preview = preview_quit_updates(original, updates, "OrgUnit")
    assert len(preview) == 4
    assert set(preview["Bisher (%)"]) == {12, 8, 5, 2}


@pytest.mark.parametrize("value", ["", "abc", "5%", -1, 101, "nan", "inf", True])
def test_invalid_rates_reject_entire_file(value):
    with pytest.raises(ValueError, match="zwischen 0 und 100"):
        parse_quit_upload(workbook(pd.DataFrame({"OrgUnit": ["IT", "0010"], RATE_COLUMN: [3, value]})),
                          "OrgUnit", ["IT", "0010"])


@pytest.mark.parametrize("names,message", [(["IT", "IT"], "mehrfach"), (["Unknown"], "Unbekannte"), ([""], "Gruppe fehlt")])
def test_invalid_names(names, message):
    with pytest.raises(ValueError, match=message):
        parse_quit_upload(workbook(pd.DataFrame({"OrgUnit": names, RATE_COLUMN: [3] * len(names)})),
                          "OrgUnit", ["IT"])


def test_wrong_dimension_and_missing_sheet_and_mixed_format():
    data = export_quit_template(frame(), "OrgUnit", False)
    with pytest.raises(ValueError, match="Spalten"):
        parse_quit_upload(data, "JobFamily", ["IT"])
    with pytest.raises(ValueError, match="Tabellenblatt"):
        parse_quit_upload(workbook(frame(), "Andere"), "OrgUnit", ["IT"])
    mixed = pd.DataFrame({"OrgUnit": ["IT"], RATE_COLUMN: [3], **{c: [3] for c in AGE_COLUMNS}})
    with pytest.raises(ValueError, match="Spalten"):
        parse_quit_upload(workbook(mixed), "OrgUnit", ["IT"])
    with pytest.raises(ValueError):
        parse_quit_upload(b"not excel", "OrgUnit", ["IT"])
    with pytest.raises(ValueError, match="keine Quoten"):
        parse_quit_upload(workbook(pd.DataFrame(columns=["OrgUnit", RATE_COLUMN])), "OrgUnit", ["IT"])


def test_excel_percentage_format_is_read_as_displayed():
    from openpyxl import load_workbook
    data = workbook(pd.DataFrame({"OrgUnit": ["0010", "IT"], RATE_COLUMN: [.005, .5]}))
    book = load_workbook(BytesIO(data))
    book["Quoten"]["B2"].number_format = "0.0%"
    output = BytesIO()
    book.save(output)
    updates = parse_quit_upload(output.getvalue(), "OrgUnit", ["0010", "IT"])
    assert updates[COHORTS[0]].tolist() == [.5, .5]


def test_simple_export_does_not_silently_flatten_age_differences():
    data = export_quit_template(frame(rates=(12, 8, 5, 2)), "OrgUnit", False)
    table = pd.read_excel(BytesIO(data), sheet_name="Quoten")
    assert table[RATE_COLUMN].isna().all()
    with pytest.raises(ValueError, match="zwischen 0 und 100"):
        parse_quit_upload(data, "OrgUnit", ["Default", "0010", "IT"])


def test_excel_rounding_does_not_change_existing_parameters():
    original = frame(rates=(.07 * 100, .03 * 100, .07 * 100, .03 * 100))
    updates = parse_quit_upload(export_quit_template(original, "OrgUnit", True), "OrgUnit",
                                original["OrgUnit"].tolist())
    result = merge_quit_updates(original, updates, "OrgUnit")
    assert matrix_from_table(result, "OrgUnit") == matrix_from_table(original, "OrgUnit")
    assert preview_quit_updates(original, updates, "OrgUnit").empty


def test_numeric_org_units_match_excel_text_names():
    original = frame()
    original.loc[1, "OrgUnit"] = 1000
    updates = parse_quit_upload(workbook(pd.DataFrame({"OrgUnit": ["1000"], RATE_COLUMN: [.5]})),
                                "OrgUnit", ["Default", "1000", "IT"])
    assert len(preview_quit_updates(original, updates, "OrgUnit")) == 4
    merged = merge_quit_updates(original, updates, "OrgUnit")
    assert matrix_from_table(merged, "OrgUnit")[COHORTS[0]]["1000"] == .5


@pytest.fixture(scope="module")
def original_builder():
    # Compare against the actual pre-feature implementation, not a recreated algorithm.
    source = subprocess.check_output(["git", "show", "b0046ce:abgaenge/params.py"], cwd=ROOT).decode("utf-8-sig")
    scope = {}
    exec(compile(source, "original_abgaenge_params", "exec"), scope)
    return scope["build_params_from_ui"]


@pytest.mark.parametrize("dimension", ["OrgUnit", "JobFamily"])
@pytest.mark.parametrize("use_matrix", [False, True])
@pytest.mark.parametrize("rates", [(0, 0, 0, 0), (3, 3, 3, 3), (12, 8, 5, 2), (0, 5, 20, 100)])
def test_existing_ui_results_equal_original_builder(original_builder, dimension, use_matrix, rates):
    ui = {"quit": {"quit_dimension": dimension, "use_quit_matrix": use_matrix,
                   "quit_matrix": matrix_from_table(frame(dimension, rates), dimension)}}
    original = original_builder(deepcopy(ui))
    # Retirement adds two explicit defaults; all pre-existing values remain equal.
    original["retirement"].update({"retirement_age": 65, "early_retirement_age": 60})
    # Existing callers without an explicit scale retain exactly the previous behavior.
    assert build_params_from_ui(deepcopy(ui)) == original
    ui["quit"]["_matrix_scale"] = "percent"
    assert build_params_from_ui(ui) == original


@pytest.mark.parametrize("dimension", ["OrgUnit", "JobFamily"])
@pytest.mark.parametrize("rates", [(0, 0, 0, 0), (.5, .5, .5, .5), (3, 3, 3, 3), (0, .5, 20, 100)])
@pytest.mark.parametrize("seed", [7, 42])
def test_import_and_manual_matrix_produce_identical_forecasts(dimension, rates, seed):
    original = frame(dimension, rates)
    imported = parse_quit_upload(export_quit_template(original, dimension, has_age_differences(original, dimension)),
                                 dimension, original[dimension].tolist())
    ui = {"random_seed": seed, "components": {"atz": False, "retirement": False, "quit": True, "ruhend": False},
          "quit": {"quit_dimension": dimension, "quit_matrix": matrix_from_table(imported, dimension),
                   "_matrix_scale": "percent"}}
    actual = build_params_from_ui(ui)
    manual = default_params()
    manual["random_seed"] = seed
    manual["components"] = ui["components"]
    manual["quit"]["quit_dimension"] = dimension
    manual["quit"]["quit_matrix"] = {
        c: {name: rate / 100 for name, rate in entries.items()}
        for c, entries in matrix_from_table(original, dimension).items()
    }
    assert actual == manual
    employees = pd.DataFrame([{
        "PersNr": str(i), "GebDatum": pd.Timestamp("2026-01-01") - pd.DateOffset(years=[25, 35, 50, 60][i % 4]),
        "Eintritt": pd.Timestamp("2000-01-01"), "BsGrd": 100., "Status kundenindividuell": "Aktiv",
        "Organisationseinheit": ["0010", "IT", "Unknown"][i % 3],
        "Jobfamily": ["0010", "IT", "Unknown"][i % 3], "JF-Cluster": "",
        "Sollarbeitszeit": 39., "MAK_Calculated": 1.,
    } for i in range(48)])
    atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])
    args = (employees, atz, pd.Timestamp("2026-01-01"), pd.Timestamp("2027-12-31"), "M")
    actual_result, expected_result = run_forecast_abgaenge(*args, actual), run_forecast_abgaenge(*args, manual)
    for name, expected in expected_result.items():
        if isinstance(expected, pd.DataFrame):
            pd.testing.assert_frame_equal(actual_result[name], expected)


def test_explicit_small_rates_and_cluster_fallbacks():
    ui = {"quit": {"_matrix_scale": "percent", "quit_matrix": {
        c: {"Default": .5, "Cluster": .25, "Family": .75} for c in COHORTS
    }}}
    params = build_params_from_ui(ui)
    assert "_matrix_scale" not in params["quit"]
    assert ui["quit"]["_matrix_scale"] == "percent"
    people = pd.DataFrame({"age": [25, 35, 50, 60], "JF-Cluster": ["Cluster", "", "Unknown", ""],
                           "Jobfamily": ["Family", "Family", "Unknown", "Unknown"]})
    np.testing.assert_array_equal(_select_quit_probs_frame(people, params, 2026), [.0025, .0075, .005, .005])
