"""Exact pre-feature comparisons and configurable retirement-age scenarios."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import types
import math

import pandas as pd
import pytest

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params
from abgaenge.params import build_params_from_ui, get_retirement_ages
from abgaenge.schemas import REASON_RETIREMENT, REASON_ATZ_END

ROOT = Path(__file__).resolve().parents[1]
START = pd.Timestamp("2026-01-01")
EMPTY_ATZ = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])


@pytest.fixture(scope="module")
def original_forecast():
    source = subprocess.check_output(["git", "show", "b0046ce:abgaenge/forecast.py"], cwd=ROOT).decode("utf-8-sig")
    module = types.ModuleType("abgaenge._retirement_reference")
    sys.modules[module.__name__] = module
    exec(compile(source, "pre_feature_forecast", "exec"), module.__dict__)
    return module.run_forecast_abgaenge


def population():
    ages = [25, 55, 59.8, 60, 61.8, 62, 64.8, 65, 66.8, 67, 70, 75]
    return pd.DataFrame([{
        "PersNr": str(1000 + i), "GebDatum": START - pd.Timedelta(days=age * 365.25),
        "Eintritt": pd.Timestamp("2000-01-01"), "BsGrd": 50. if i % 2 else 100.,
        "Status kundenindividuell": "Aktiv", "Organisationseinheit": "OE-Test",
        "Jobfamily": "JF-Test", "Sollarbeitszeit": 39., "MAK_Calculated": .5 if i % 2 else 1.,
    } for i, age in enumerate(ages * 2)])


def atz_cases():
    return pd.DataFrame([{
        "PersNr": "1009", "Phase": "AR", "Beginn": pd.Timestamp("2022-01-01"),
        "Ende": pd.Timestamp("2024-12-31"), "Ende ATZ Vertrag": pd.Timestamp("2026-06-30"),
    }, {
        "PersNr": "1009", "Phase": "FR", "Beginn": pd.Timestamp("2025-01-01"),
        "Ende": pd.Timestamp("2026-06-30"), "Ende ATZ Vertrag": pd.Timestamp("2026-06-30"),
    }])


def assert_result_equal(actual, expected):
    assert actual.keys() == expected.keys()
    for key in actual:
        if isinstance(expected[key], pd.DataFrame):
            pd.testing.assert_frame_equal(actual[key], expected[key], check_exact=True)
        elif isinstance(expected[key], dict):
            assert_result_equal(actual[key], expected[key])
        else:
            assert actual[key] == expected[key]


SCENARIOS = [
    ("usual", .9, .1, False, True),
    ("zero", 0., 0., False, True),
    ("all", 1., 1., False, True),
    ("regular_only", 1., 0., False, True),
    ("early_only", 0., 1., False, True),
    ("combined", .9, .1, True, True),
    ("disabled", .9, .1, True, False),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
@pytest.mark.parametrize("freq", ["M", "Q"])
@pytest.mark.parametrize("seed", [7, 42])
@pytest.mark.parametrize("months", [12, 36])
@pytest.mark.parametrize("explicit", [False, True], ids=["legacy_missing_ages", "explicit_65_60"])
def test_default_age_results_equal_actual_original_code(original_forecast, scenario, freq, seed, months, explicit):
    _, regular_rate, early_rate, combined, enabled = scenario
    params = default_params()
    params["random_seed"] = seed
    params["components"] = {"atz": combined, "quit": combined, "retirement": enabled, "ruhend": False}
    params["atz"]["new_atz_rate"] = 0.
    params["quit"].update({"use_quit_matrix": False, "quit_rate_base": .15})
    params["retirement"] = {"rent_rate_65": regular_rate, "rent_rate_60_65": early_rate}
    if explicit:
        params["retirement"].update({"retirement_age": 65, "early_retirement_age": 60})
    end = START + pd.DateOffset(months=months) - pd.Timedelta(days=1)
    args = (population(), atz_cases(), START, end, freq)
    expected = original_forecast(*args, deepcopy(params))
    actual = run_forecast_abgaenge(*args, deepcopy(params))
    assert_result_equal(actual, expected)


def dynamic_population(regular=67, early=62):
    people = population().iloc[:5].copy()
    people["GebDatum"] = [START - pd.Timedelta(days=math.ceil(age * 365.25)) for age in [early - .1, early, regular - .1, regular, regular + 3]]
    return people


@pytest.mark.parametrize("freq", ["M", "Q"])
@pytest.mark.parametrize("regular,early", [(67, 62), (63, 58), (70, 65)])
@pytest.mark.parametrize("regular_rate,early_rate,expected", [(1., 0., {"001003", "001004"}), (0., 1., {"001001", "001002"}), (1., 1., {"001001", "001002", "001003", "001004"}), (0., 0., set())])
def test_new_age_limits_select_correct_people_in_first_period(freq, regular, early, regular_rate, early_rate, expected):
    params = default_params()
    params["components"] = {"atz": False, "retirement": True, "quit": False, "ruhend": False}
    params["retirement"].update({"retirement_age": regular, "early_retirement_age": early,
                                 "rent_rate_65": regular_rate, "rent_rate_60_65": early_rate})
    end = pd.Timestamp("2026-01-31") if freq == "M" else pd.Timestamp("2026-03-31")
    result = run_forecast_abgaenge(dynamic_population(regular, early), EMPTY_ATZ, START, end, freq, params)
    assert set(result["tables"]["retirement"].get("persnr", [])) == expected


def test_people_age_into_new_retirement_range():
    params = default_params()
    params["components"] = {"atz": False, "retirement": True, "quit": False, "ruhend": False}
    params["retirement"].update({"retirement_age": 67, "early_retirement_age": 62,
                                 "rent_rate_65": 1., "rent_rate_60_65": 0.})
    result = run_forecast_abgaenge(dynamic_population(), EMPTY_ATZ, START, pd.Timestamp("2026-12-31"), "M", params)
    retired = result["tables"]["retirement"]
    crossing = retired.loc[retired["persnr"] == "001002"].iloc[0]
    assert crossing["age"] >= 67
    assert crossing["event_date"] > pd.Timestamp("2026-01-31")
    assert "001000" not in set(retired["persnr"])


def test_atz_contract_end_remains_separate_from_new_age_limits():
    params = default_params()
    params["components"] = {"atz": True, "retirement": True, "quit": False, "ruhend": False}
    params["atz"]["new_atz_rate"] = 0.
    params["retirement"].update({"retirement_age": 67, "early_retirement_age": 62,
                                 "rent_rate_65": 1., "rent_rate_60_65": 1.})
    people = dynamic_population().iloc[[3]].copy()
    people["PersNr"] = "1009"
    result = run_forecast_abgaenge(people, atz_cases(), START, pd.Timestamp("2026-12-31"), "M", params)
    events = result["events_person_level"]
    assert not (events["reason_code"] == REASON_RETIREMENT).any()
    assert (events["reason_code"] == REASON_ATZ_END).sum() == 1


@pytest.mark.parametrize("regular,early", [(65, 65), (60, 65), (0, 60), (121, 60), (65.5, 60), (True, 60), (65, None)])
def test_invalid_age_limits_are_rejected(regular, early):
    retirement = {"retirement_age": regular, "early_retirement_age": early}
    with pytest.raises(ValueError):
        get_retirement_ages(retirement)
    with pytest.raises(ValueError):
        build_params_from_ui({"retirement": retirement})
    params = default_params()
    params["retirement"].update(retirement)
    with pytest.raises(ValueError):
        run_forecast_abgaenge(dynamic_population(), EMPTY_ATZ, START, pd.Timestamp("2026-12-31"), "M", params)


def test_legacy_parameter_blocks_receive_default_ages_without_mutation():
    legacy = {"retirement": {"rent_rate_65": .7, "rent_rate_60_65": .2}}
    original = deepcopy(legacy)
    params = build_params_from_ui(legacy)
    assert params["retirement"] == {**legacy["retirement"], "retirement_age": 65, "early_retirement_age": 60}
    assert legacy == original
