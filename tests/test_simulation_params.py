from pathlib import Path
import sys

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1]))

from abgaenge.params import default_params as default_abgaenge_params
from utils.simulation_params import (
    SESSION_KEY,
    get_compact_plus_params,
    get_compact_plus_run_options,
    get_simulation_params,
    save_simulation_params,
    RUN_COUNTS,
)
from zugaenge.params import default_params as default_zugaenge_params


def test_new_accession_defaults_are_disabled():
    defaults = default_zugaenge_params()
    assert all(not defaults[key]["active"] for key in ("azubi", "trainee", "new_hires"))


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


def test_run_options_save_and_preserve_distinct_single_run_seeds():
    _clear_relevant_state()
    params = get_simulation_params()
    params["abgaenge"]["random_seed"] = 7
    params["zugaenge"]["random_seed"] = 42
    params["compact_plus"].update(target_date="2028-06-15", run_count=2)
    save_simulation_params(params)
    assert get_compact_plus_run_options("2025-12-31") == ("2028-06-15", 2)
    assert get_compact_plus_params()[0]["random_seed"] == 7
    assert get_compact_plus_params()[1]["random_seed"] == 42
    params = get_simulation_params()
    params["compact_plus"]["run_count"] = 1
    save_simulation_params(params)
    assert get_compact_plus_run_options("2025-12-31")[1] == 1
    assert get_compact_plus_params()[0]["random_seed"] == 7
    assert get_compact_plus_params()[1]["random_seed"] == 42


def test_run_count_choices_and_invalid_save():
    assert RUN_COUNTS[:4] == (1, 2, 10, 20)
    assert RUN_COUNTS[-1] == 1000
    _clear_relevant_state()
    params = get_simulation_params()
    params["compact_plus"]["run_count"] = 3
    import pytest
    with pytest.raises(ValueError, match="Anzahl"):
        save_simulation_params(params)
