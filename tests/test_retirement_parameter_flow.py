from copy import deepcopy
from io import BytesIO
import importlib.util
from pathlib import Path
import subprocess
import types

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from abgaenge.params import default_params
from utils.audit_helpers import build_65plus_audit
from utils.compact_simulation_export import build_parameter_sheet, _readme_sheet, build_compact_simulation_export_bytes
from utils.simulation_params import save_simulation_params, get_compact_plus_params, get_simulation_params

ROOT = Path(__file__).resolve().parents[1]


def load_page(pattern, name):
    spec = importlib.util.spec_from_file_location(name, next((ROOT / "pages").glob(pattern)))
    module = importlib.util.module_from_spec(spec)
    module._UNIT_TESTING = True
    spec.loader.exec_module(module)
    return module


def test_new_ages_follow_existing_save_load_export_and_cache_paths(monkeypatch):
    monkeypatch.setattr(st, "session_state", {})
    current = get_simulation_params()
    current["abgaenge"]["retirement"].update({"retirement_age": 67, "early_retirement_age": 62})
    saved = save_simulation_params(current)
    abg, zug = get_compact_plus_params()
    assert get_simulation_params()["abgaenge"]["retirement"] == abg["retirement"]
    assert abg["retirement"]["retirement_age"] == 67
    assert abg["retirement"]["early_retirement_age"] == 62
    assert saved["abgaenge"]["retirement"] == abg["retirement"]
    parameters = build_parameter_sheet(abgaenge_params=abg, zugaenge_params=zug, metadata={})
    values = parameters.set_index("Parameter")["Wert"]
    assert values["retirement.retirement_age"] == "67"
    assert values["retirement.early_retirement_age"] == "62"
    page = load_page("7_*", "retirement_cache_page")
    args = {"base_date": pd.Timestamp("2026-01-01"), "target_date": pd.Timestamp("2027-01-01"),
            "abgaenge_params": abg, "zugaenge_params": zug, "cluster_source_signature": "test"}
    before = page._build_simulation_signature(**args)
    for key in ("retirement_age", "early_retirement_age"):
        different = deepcopy(abg)
        different["retirement"][key] += 1
        assert page._build_simulation_signature(**{**args, "abgaenge_params": different}) != before


def audit_population():
    return pd.DataFrame({"PersNr": ["1", "2", "3", "4"], "Alter_Jahre": [64., 65., 67., 70.],
                         "Is_Vacant": [False, False, False, True], "MAK_Calculated": [1., .5, 1., 0.]})


@pytest.mark.parametrize("explicit", [False, True])
def test_default_audit_matches_original_exactly(explicit):
    source = subprocess.check_output(["git", "show", "b0046ce:utils/audit_helpers.py"], cwd=ROOT).decode("utf-8-sig")
    module = types.ModuleType("original_audit")
    exec(compile(source, "original_audit", "exec"), module.__dict__)
    params = {"retirement": {"rent_rate_65": .7}}
    if explicit:
        params["retirement"].update({"retirement_age": 65, "early_retirement_age": 60})
    expected = module.build_65plus_audit(audit_population(), params)
    pd.testing.assert_frame_equal(build_65plus_audit(audit_population(), params), expected, check_exact=True)


def test_audit_and_export_explanation_use_configured_retirement_age():
    params = {"retirement": {"retirement_age": 67, "early_retirement_age": 62, "rent_rate_65": .8}}
    audit = build_65plus_audit(audit_population(), params)
    assert audit["PersNr"].tolist() == ["3"]
    assert audit["Rentenwahrscheinlichkeit"].tolist() == [.8]
    readme = _readme_sheet({}, None, params)
    text = " ".join(readme["Beschreibung"])
    assert "62–66" in text and "67 Jahren" in text


def test_new_ages_appear_in_actual_excel_export():
    params = {"retirement": {"retirement_age": 67, "early_retirement_age": 62, "rent_rate_65": .8}}
    prepared = audit_population().assign(Jobfamily="JF-Test", Total_Cost_Year=0.)
    data = build_compact_simulation_export_bytes(prepared_df=prepared, abgaenge_params=params,
                                                zugaenge_params={}, metadata={}, lineage_ids=())
    workbook = pd.ExcelFile(BytesIO(data))
    values = pd.read_excel(workbook, "Parameter").set_index("Parameter")["Wert"]
    assert str(values["retirement.retirement_age"]) == "67"
    assert str(values["retirement.early_retirement_age"]) == "62"
    audit = pd.read_excel(workbook, "65plus_Audit")
    assert audit["Alter"].tolist() == [67]
    readme = pd.read_excel(workbook, "00_Readme")
    assert "62–66" in " ".join(readme["Beschreibung"].astype(str))


def make_app(style):
    suffix = "3_*" if style == "forecast" else "*Simulationsparameter.py"
    page_path = next((ROOT / "pages").glob(suffix))
    source = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
import importlib.util
import pandas as pd
import streamlit as st
from abgaenge.params import default_params
spec = importlib.util.spec_from_file_location("retirement_page", {str(page_path)!r})
page = importlib.util.module_from_spec(spec)
page._UNIT_TESTING = True
spec.loader.exec_module(page)
snapshot = pd.DataFrame([{{"PersNr": "1000", "GebDatum": pd.Timestamp("1960-01-01"),
    "Eintritt": pd.Timestamp("2000-01-01"), "Austritt": pd.NaT, "BsGrd": 100.,
    "Status kundenindividuell": "Aktiv", "Organisationseinheit": "OE-Test", "Jobfamily": "JF-Test",
    "Sollarbeitszeit": 39., "MAK_Calculated": 1., "Geschlecht": "w", "ATZ_Status": "Kein ATZ"}}])
empty = pd.DataFrame()
page.load_and_prepare_data = lambda: (snapshot.copy(), empty.copy(), empty.copy(), {{}})
page.get_current_stichtag = lambda: pd.Timestamp("2026-01-01")
if "abgaenge_params" not in st.session_state:
    base = default_params()
    base["components"].update({{"atz": False, "quit": False, "ruhend": False}})
    st.session_state["abgaenge_params"] = base
if st.session_state.get("away"):
    st.stop()
'''
    if style == "forecast":
        source += '''
page.load_atz_data_cached = lambda *args: pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])
page.render_global_filters = lambda *args: None
page._get_attrition_cluster_context = lambda *args: (None, None, "test", False)
page._get_param_dimension_values = lambda *args: (["OE-Test"], ["JF-Test"])
page.apply_robust_filter = lambda df, *args, **kwargs: df
page.apply_filters = lambda df, *args, **kwargs: df
page.apply_event_filters = lambda df, *args, **kwargs: (df, len(df), len(df))
'''
    source += "page.main()\n"
    return AppTest.from_string(source.encode("ascii", "backslashreplace").decode("ascii"), default_timeout=40).run()


@pytest.mark.parametrize("style,prefix", [("forecast", "abgaenge"), ("simulation", "sim_params_abgaenge")])
def test_real_pages_save_age_inputs_and_preserve_them_after_navigation(style, prefix):
    at = make_app(style)
    assert not at.exception
    assert at.number_input(key=f"{prefix}_retirement_age").value == 65
    assert at.number_input(key=f"{prefix}_early_retirement_age").value == 60
    rate_key = "slide_rent_65" if style == "forecast" else f"{prefix}_rent65"
    at.slider(key=rate_key).set_value(.7).run()
    at.number_input(key=f"{prefix}_retirement_age").set_value(67).run()
    at.number_input(key=f"{prefix}_early_retirement_age").set_value(62).run()
    assert not at.exception
    assert at.slider(key=rate_key).value == .7
    assert any("62–66" in item.value and "67 Jahren" in item.value for item in at.caption)
    label = "Parameter speichern" if style == "simulation" else "🚀 Prognose mit diesen Parametern berechnen"
    buttons = [button for button in at.button if button.label == label]
    assert len(buttons) == 1, [button.label for button in at.button]
    buttons[0].click().run()
    assert not at.exception
    if style == "simulation":
        retirement = at.session_state["simulation_params"]["abgaenge"]["retirement"]
    else:
        retirement = at.session_state["abgaenge_params"]["retirement"]
        assert at.session_state["abgaenge_global_result"]["assumptions"]["retirement"] == retirement
        assert at.session_state["abgaenge_ui_state"]["retirement"] == retirement
    assert retirement["retirement_age"] == 67 and retirement["early_retirement_age"] == 62
    assert retirement["rent_rate_65"] == .7
    at.session_state["away"] = True
    at.run()
    at.session_state["away"] = False
    at.run()
    assert not at.exception
    assert at.number_input(key=f"{prefix}_retirement_age").value == 67
    assert at.number_input(key=f"{prefix}_early_retirement_age").value == 62


@pytest.mark.parametrize("style,prefix", [("forecast", "abgaenge"), ("simulation", "sim_params_abgaenge")])
def test_real_pages_reject_overlapping_age_ranges(style, prefix):
    at = make_app(style)
    at.number_input(key=f"{prefix}_early_retirement_age").set_value(65).run()
    assert not at.exception
    assert any("Frühverrentung muss vor" in item.value for item in at.error)
    assert not any(button.label == "Parameter speichern" or "Parametern berechnen" in button.label for button in at.button)
    at.number_input(key=f"{prefix}_early_retirement_age").set_value(60).run()
    assert not at.exception
    assert not at.error
