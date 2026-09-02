from pathlib import Path
import sys

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))

from abgaenge.params import default_params as default_abgaenge_params
from utils.simulation_params import (
    SESSION_KEY,
    get_compact_plus_params,
)
from zugaenge.params import default_params as default_zugaenge_params


def _clear_relevant_state():
    for key in [
        SESSION_KEY,
        "abgaenge_params",
        "zugaenge_params",
        "hybrid_abg_params",
        "hybrid_zug_params",
        "compact_sim_target_date",
        "compact_plus_mode",
    ]:
        st.session_state.pop(key, None)


def test_compact_plus_params_fall_back_to_defaults_without_simulation_params():
    _clear_relevant_state()

    abgaenge_params, zugaenge_params = get_compact_plus_params()

    assert abgaenge_params == default_abgaenge_params()
    assert zugaenge_params == default_zugaenge_params()
    assert "_ui" not in abgaenge_params
    assert "_ui" not in zugaenge_params


def test_compact_plus_params_use_simulation_params_before_legacy_keys():
    _clear_relevant_state()
    st.session_state["abgaenge_params"] = {
        **default_abgaenge_params(),
        "random_seed": 11,
    }
    st.session_state["zugaenge_params"] = {
        **default_zugaenge_params(),
        "random_seed": 12,
    }
    st.session_state[SESSION_KEY] = {
        "abgaenge": {
            **default_abgaenge_params(),
            "_ui": {"freq": "M"},
            "random_seed": 101,
        },
        "zugaenge": {
            **default_zugaenge_params(),
            "_ui": {"start_date": None},
            "random_seed": 102,
        },
    }

    abgaenge_params, zugaenge_params = get_compact_plus_params()

    assert abgaenge_params["random_seed"] == 101
    assert zugaenge_params["random_seed"] == 102
    assert "_ui" not in abgaenge_params
    assert "_ui" not in zugaenge_params
    assert st.session_state["abgaenge_params"]["random_seed"] == 11
    assert st.session_state["zugaenge_params"]["random_seed"] == 12


def test_compact_plus_params_migrate_from_legacy_keys_when_missing():
    _clear_relevant_state()
    st.session_state["abgaenge_params"] = {
        **default_abgaenge_params(),
        "random_seed": 201,
    }
    st.session_state["zugaenge_params"] = {
        **default_zugaenge_params(),
        "random_seed": 202,
    }

    abgaenge_params, zugaenge_params = get_compact_plus_params()

    assert abgaenge_params["random_seed"] == 201
    assert zugaenge_params["random_seed"] == 202
    assert st.session_state["abgaenge_params"]["random_seed"] == 201
    assert st.session_state["zugaenge_params"]["random_seed"] == 202


def test_compact_plus_params_accept_legacy_percent_quit_matrix():
    _clear_relevant_state()
    st.session_state[SESSION_KEY] = {
        "abgaenge": {
            **default_abgaenge_params(),
            "quit": {
                **default_abgaenge_params()["quit"],
                "quit_matrix": {
                    "alter_unter_30": {"Default": 12.0},
                    "alter_30_45": {"Default": 8.0},
                    "alter_45_55": {"Default": 5.0},
                    "alter_55_plus": {"Default": 2.0},
                },
            },
        },
        "zugaenge": default_zugaenge_params(),
    }

    abgaenge_params, _ = get_compact_plus_params()

    matrix = abgaenge_params["quit"]["quit_matrix"]
    assert matrix["alter_unter_30"]["Default"] == 0.12
    assert matrix["alter_30_45"]["Default"] == 0.08
    assert matrix["alter_45_55"]["Default"] == 0.05
    assert matrix["alter_55_plus"]["Default"] == 0.02


def test_compact_plus_params_leave_weight_quit_matrix_unchanged():
    _clear_relevant_state()
    st.session_state[SESSION_KEY] = {
        "abgaenge": default_abgaenge_params(),
        "zugaenge": default_zugaenge_params(),
    }

    abgaenge_params, _ = get_compact_plus_params()

    assert abgaenge_params["quit"]["quit_matrix"] == default_abgaenge_params()["quit"]["quit_matrix"]
