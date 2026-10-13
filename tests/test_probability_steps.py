"""Uniform quarter-percentage-point controls and unrounded parameter forwarding."""
import ast
from pathlib import Path

import pytest

from test_retirement_parameter_flow import make_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("pattern", ["3_*", "*Simulationsparameter.py"])
def test_all_probability_and_percentage_controls_have_uniform_steps(pattern):
    tree = ast.parse(next((ROOT / "pages").glob(pattern)).read_text(encoding="utf-8-sig"))
    sliders = 0
    columns = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        kwargs = {item.arg: item.value for item in node.keywords}
        if node.func.attr == "slider":
            sliders += 1
            step = kwargs.get("step", node.args[4] if len(node.args) > 4 else None)
            assert ast.literal_eval(step) == .0025
            assert ast.literal_eval(kwargs["format"]) == "%.4f"
        elif node.func.attr == "NumberColumn":
            columns += 1
            assert ast.literal_eval(kwargs["step"]) == .25
            assert ast.literal_eval(kwargs["format"]) == "%.2f"
    assert sliders >= 4 and columns >= 1


@pytest.mark.parametrize("style", ["forecast", "simulation"])
@pytest.mark.parametrize("value", [.0025, .0275, .9975])
def test_quarter_percent_inputs_survive_compute_and_save(style, value):
    at = make_app(style)
    if style == "forecast":
        keys = {"atz": "slide_atz_base", "regular": "slide_rent_65", "early": "slide_rent_60", "quit": "slide_quit_base_live"}
    else:
        keys = {"atz": "sim_params_abgaenge_atz_rate", "regular": "sim_params_abgaenge_rent65",
                "early": "sim_params_abgaenge_rent60", "quit": "sim_params_abgaenge_quit_base"}
    expected = {}
    for name, key in keys.items():
        slider = at.slider(key=key)
        assert slider.proto.step == .0025
        assert slider.proto.format == "%.4f"
        chosen = min(value, .4975) if name in ("atz", "quit") else value
        expected[name] = chosen
        slider.set_value(chosen).run()
        assert not at.exception
        assert at.slider(key=key).value == pytest.approx(chosen)
    label = "Parameter speichern" if style == "simulation" else "🚀 Prognose mit diesen Parametern berechnen"
    next(button for button in at.button if button.label == label).click().run()
    assert not at.exception
    params = at.session_state["simulation_params"]["abgaenge"] if style == "simulation" else at.session_state["abgaenge_params"]
    assert params["atz"]["new_atz_rate"] == pytest.approx(expected["atz"])
    assert params["retirement"]["rent_rate_65"] == pytest.approx(expected["regular"])
    assert params["retirement"]["rent_rate_60_65"] == pytest.approx(expected["early"])
    assert params["quit"]["quit_rate_base"] == pytest.approx(expected["quit"])
    if style == "forecast":
        assert at.session_state["abgaenge_global_result"]["assumptions"] == params
