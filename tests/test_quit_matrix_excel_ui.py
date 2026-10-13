from io import BytesIO
from pathlib import Path
import json
import subprocess

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from utils.quit_matrix_excel import COHORTS, RATE_COLUMN

ROOT = Path(__file__).resolve().parents[1]


def upload(rate=.5, name="0010", dimension="OrgUnit"):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        pd.DataFrame({dimension: [name], RATE_COLUMN: [rate]}).to_excel(writer, sheet_name="Quoten", index=False)
    return output.getvalue()


def app_script(style, original_path=None):
    # AppTest does not expose file_uploader interactions; inject file bytes only.
    # All editor, button and rerun interactions still use the real Streamlit runtime.
    setup = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
from io import BytesIO
import importlib.util
import pandas as pd
import streamlit as st
from abgaenge.params import default_params, build_params_from_ui
from utils.quit_matrix_excel import COHORTS, matrix_from_table
from utils.matrix_helpers import weights_to_percent
from components.quit_matrix_excel import render_quit_matrix_with_excel
st.file_uploader = lambda *args, **kwargs: BytesIO(st.session_state["upload_bytes"]) if st.session_state.get("upload_bytes") else None
dimension = st.radio("Dimension testen", ["OrgUnit", "JobFamily"], key="test_dimension")
disabled = st.checkbox("Matrix deaktivieren", key="test_disabled")
if st.session_state.get("away"):
    st.stop()
current = st.session_state.get("saved_params", default_params())
'''
    if style == "simulation":
        page_path = original_path or next((ROOT / "pages").glob("*Simulationsparameter.py"))
        setup += f'''
spec = importlib.util.spec_from_file_location("parameter_page", {str(page_path)!r})
page = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page)
matrix = page._quit_matrix_editor(dimension=dimension, matrix_weights=current["quit"]["quit_matrix"],
    default_percent=5., key="matrix_" + dimension, disabled=disabled, dimension_values=["0010", "IT"])
'''
    else:
        setup += '''
percent = weights_to_percent(current["quit"]["quit_matrix"])
frame = pd.DataFrame([{dimension: name, **{c: percent[c].get(name, percent[c]["Default"]) for c in COHORTS}} for name in ["Default", "0010", "IT"]]).set_index(dimension)
edited = render_quit_matrix_with_excel(frame, dimension=dimension, key="matrix_" + dimension, disabled=disabled)
matrix = matrix_from_table(edited, dimension)
'''
    setup += '''
st.session_state["observed_matrix"] = matrix
if st.button("Speichern testen"):
    st.session_state["saved_params"] = build_params_from_ui({"quit": {"quit_dimension": dimension, "quit_matrix": matrix, "_matrix_scale": "percent"}})
'''
    return setup


def click(at, label):
    next(button for button in at.button if button.label == label).click().run()
    assert not at.exception


def make_app(style, original_path=None):
    # Streamlit's from_string writes with the Windows default encoding.
    source = app_script(style, original_path).encode("ascii", "backslashreplace").decode("ascii")
    return AppTest.from_string(source, default_timeout=30).run()


@pytest.mark.parametrize("style", ["forecast", "simulation"])
def test_preview_apply_repeated_upload_and_save(style):
    at = make_app(style)
    assert not at.exception
    before = at.session_state["observed_matrix"]
    at.session_state["upload_bytes"] = upload()
    at.run()
    assert not at.exception
    assert at.session_state["observed_matrix"] == before
    assert any(b.label == "Quoten übernehmen" for b in at.button)
    click(at, "Quoten übernehmen")
    observed = at.session_state["observed_matrix"]
    for cohort in COHORTS:
        assert observed[cohort]["0010"] == .5
        assert observed[cohort]["Default"] == before[cohort]["Default"]
        assert observed[cohort]["IT"] == before[cohort]["IT"]
    at.run()
    assert not any(b.label == "Quoten übernehmen" for b in at.button)
    assert at.session_state["observed_matrix"] == observed
    click(at, "Speichern testen")
    for cohort in COHORTS:
        assert at.session_state["saved_params"]["quit"]["quit_matrix"][cohort]["0010"] == .005
    at.session_state["upload_bytes"] = upload(1.)
    at.run()
    click(at, "Quoten übernehmen")
    assert at.session_state["observed_matrix"][COHORTS[0]]["0010"] == 1.
    # A manual edit after import survives subsequent reruns and the next download.
    # AppTest has no public data_editor setter: send the same JSON widget state as the browser.
    states = at._tree.get_widget_states()
    state = states.widgets.add()
    state.id = at.get("arrow_data_frame")[0].proto.id
    state.string_value = json.dumps({"edited_rows": {1: {COHORTS[0]: .25}}, "added_rows": [], "deleted_rows": []})
    at._run(states)
    assert not at.exception
    assert at.session_state["observed_matrix"][COHORTS[0]]["0010"] == .25
    at._run(states)
    assert at.session_state["observed_matrix"][COHORTS[0]]["0010"] == .25
    # Save with the same editor state that the browser sends, then leave and return.
    next(button for button in at.button if button.label == "Speichern testen").click()
    save_states = at._tree.get_widget_states()
    save_states.widgets.add().CopyFrom(state)
    at._run(save_states)
    assert at.session_state["saved_params"]["quit"]["quit_matrix"][COHORTS[0]]["0010"] == .0025
    at.session_state["away"] = True
    at.run()
    at.session_state["away"] = False
    at.run()
    assert not at.exception
    assert at.session_state["observed_matrix"][COHORTS[0]]["0010"] == .25


@pytest.mark.parametrize("style", ["forecast", "simulation"])
def test_invalid_upload_and_dimension_switch_do_not_apply(style):
    at = make_app(style)
    before = at.session_state["observed_matrix"]
    at.session_state["upload_bytes"] = upload(name="Unknown")
    at.run()
    assert not at.exception
    assert len(at.error) == 1
    assert "Unbekannte" in at.error[0].value
    assert at.session_state["observed_matrix"] == before
    assert not any(b.label == "Quoten übernehmen" for b in at.button)
    at.radio(key="test_dimension").set_value("JobFamily").run()
    assert not at.exception
    assert "Spalten" in at.error[0].value
    assert at.session_state["observed_matrix"] == before
    at.checkbox(key="test_disabled").check().run()
    assert not at.exception
    assert not any(b.label == "Quoten übernehmen" for b in at.button)


def test_simulation_editor_without_upload_matches_original(tmp_path):
    original = tmp_path / "original_parameter_page.py"
    source = subprocess.check_output(["git", "show", "b0046ce:pages/8_⚙️_Simulationsparameter.py"], cwd=ROOT)
    original.write_bytes(source)
    old = make_app("simulation", original)
    new = make_app("simulation")
    assert not old.exception and not new.exception
    assert old.session_state["observed_matrix"] == new.session_state["observed_matrix"]
