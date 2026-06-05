from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

import streamlit as st

from abgaenge.params import default_params as default_abgaenge_params
from zugaenge.params import default_params as default_zugaenge_params


SESSION_KEY = "simulation_params"
SOURCE_PAGE = "Simulationsparameter"


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base)
    if not isinstance(overlay, Mapping):
        return result
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _default_abgaenge_ui() -> dict[str, Any]:
    return {
        "ist_stichtag": None,
        "forecast_end_date": None,
        "freq": "M",
    }


def _default_zugaenge_ui() -> dict[str, Any]:
    return {
        "start_date": None,
        "end_date": None,
        "use_azubis": True,
        "use_trainees": True,
        "use_newhires": True,
    }


def _default_hybrid_ui() -> dict[str, Any]:
    return {
        "ist_stichtag": None,
        "forecast_end_date": None,
        "freq": "M",
        "components": {
            "atz": True,
            "retirement": True,
            "quit": True,
            "ruhend": True,
            "azubi": True,
            "trainee": True,
            "new_hires": True,
        },
    }


def default_hybrid_zugaenge_params() -> dict[str, Any]:
    params = default_zugaenge_params()
    return {
        "azubi": {
            **params["azubi"],
            "active": True,
        },
        "trainee": {
            **params["trainee"],
            "active": True,
        },
        "new_hires": {
            **params["new_hires"],
            "active": True,
            "distribution": [],
        },
        "available_org_units": [],
        "org_unit_to_cluster_map": {},
        "random_seed": params.get("random_seed", 42),
    }


def default_simulation_params() -> dict[str, Any]:
    return {
        "scenario": {
            "name": "Basisszenario",
            "description": "",
            "version": 1,
            "last_modified": None,
            "source_page": SOURCE_PAGE,
        },
        "compact_plus": {
            "target_date": None,
            "mode": "simulation",
        },
        "abgaenge": {
            **default_abgaenge_params(),
            "_ui": _default_abgaenge_ui(),
        },
        "zugaenge": {
            **default_zugaenge_params(),
            "_ui": _default_zugaenge_ui(),
        },
        "hybrid": {
            "_ui": _default_hybrid_ui(),
            "abgaenge": default_abgaenge_params(),
            "zugaenge": default_hybrid_zugaenge_params(),
        },
    }


def initialize_simulation_params(*, prefer_existing_session_values: bool = True) -> dict[str, Any]:
    params = default_simulation_params()

    if prefer_existing_session_values:
        params["abgaenge"] = _deep_merge(
            params["abgaenge"],
            st.session_state.get("abgaenge_params"),
        )
        params["zugaenge"] = _deep_merge(
            params["zugaenge"],
            st.session_state.get("zugaenge_params"),
        )
        params["hybrid"]["abgaenge"] = _deep_merge(
            params["hybrid"]["abgaenge"],
            st.session_state.get("hybrid_abg_params"),
        )
        params["hybrid"]["zugaenge"] = _deep_merge(
            params["hybrid"]["zugaenge"],
            st.session_state.get("hybrid_zug_params"),
        )
        if "compact_sim_target_date" in st.session_state:
            params["compact_plus"]["target_date"] = st.session_state["compact_sim_target_date"]
        if "compact_plus_mode" in st.session_state:
            params["compact_plus"]["mode"] = st.session_state["compact_plus_mode"]

    st.session_state[SESSION_KEY] = params
    return st.session_state[SESSION_KEY]


def get_simulation_params() -> dict[str, Any]:
    current = st.session_state.get(SESSION_KEY)
    if not isinstance(current, dict):
        return initialize_simulation_params()
    normalized = _deep_merge(default_simulation_params(), current)
    st.session_state[SESSION_KEY] = normalized
    return normalized


def save_simulation_params(params: Mapping[str, Any]) -> dict[str, Any]:
    current = _deep_merge(default_simulation_params(), params)
    scenario = current.setdefault("scenario", {})
    scenario["source_page"] = SOURCE_PAGE
    scenario["last_modified"] = datetime.now().isoformat(timespec="seconds")
    scenario["version"] = int(scenario.get("version", 0) or 0) + 1
    st.session_state[SESSION_KEY] = current
    return current


def reset_simulation_params() -> dict[str, Any]:
    return initialize_simulation_params(prefer_existing_session_values=False)
