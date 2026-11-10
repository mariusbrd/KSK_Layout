"""The actual parameter and CompactPlus pages use one saved run setting."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from utils.simulation_params import default_simulation_params


ROOT = Path(__file__).resolve().parents[1]


def test_parameter_page_saves_count_target_and_distinct_seeds():
    page = next((ROOT / "pages").glob("8_*_Simulationsparameter.py"))
    app = AppTest.from_file(str(page), default_timeout=90).run()
    assert not app.exception
    app.date_input(key="sim_params_target_date").set_value("2027-06-15").run()
    app.selectbox(key="sim_params_run_count").set_value(2).run()
    assert not app.exception
    next(button for button in app.button if button.label == "Parameter speichern").click().run()
    assert not app.exception
    saved = app.session_state["simulation_params"]
    assert str(saved["compact_plus"]["target_date"]) == "2027-06-15"
    assert saved["compact_plus"]["run_count"] == 2
    assert saved["abgaenge"]["random_seed"] == 42
    assert saved["zugaenge"]["random_seed"] == 42
    assert "compact_sim_signature" not in app.session_state
    app.run()
    assert app.selectbox(key="sim_params_run_count").value == 2
    assert str(app.date_input(key="sim_params_target_date").value) == "2027-06-15"


def _compact_app(count):
    page = next((ROOT / "pages").glob("7_*_Kompakt_plus_Simulation.py"))
    source = (
        "import importlib.util\nimport streamlit as st\n"
        "st.page_link = lambda *args, **kwargs: None\n"
        f"spec = importlib.util.spec_from_file_location('compact_plus_app_test', {str(page)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "real_button = st.button\n"
        "def test_button(label, *args, **kwargs):\n"
        "    if 'Simulation' in label and ('berechnen' in label or 'aktualisieren' in label):\n"
        "        return bool(st.session_state.get('test_start_simulation', False))\n"
        "    return real_button(label, *args, **kwargs)\n"
        "st.button = test_button\n"
        "module.render_global_filters = lambda *a, **k: None\n"
        "module.get_active_view_filters = lambda: {}\n"
        "module.filter_dataframe_by_view_filters = lambda df, filters: df\n"
        "module.get_filter_summary = lambda: ''\n"
        "module.render_active_filter_banner = lambda *a, **k: None\n"
        "module.set_metric_page_hint = lambda *a, **k: None\n"
        "module.get_global_metric_view = lambda: 'MAK'\n"
        "from types import SimpleNamespace\n"
        "from utils.monte_carlo_product_worker import _compact_prepare\n"
        "module.load_compact_page_module = lambda: SimpleNamespace(\n"
        "    prepare_compact_data=_compact_prepare(),\n"
        "    render_ist_mak_tab=lambda *a, **k: None,\n"
        "    render_ist_koepfe_tab=lambda *a, **k: None,\n"
        "    render_ist_vs_soll_mak_tab=lambda *a, **k: None,\n"
        "    render_ist_soll_koepfe_tab=lambda *a, **k: None)\n"
        "module.main()\n"
    )
    app = AppTest.from_string(source.encode("ascii", "backslashreplace").decode(),
                              default_timeout=180)
    settings = default_simulation_params()
    settings["compact_plus"].update(target_date="2026-12-31", run_count=count)
    app.session_state["simulation_params"] = settings
    return app.run()


def test_compact_plus_single_run_uses_saved_target_without_monte_carlo():
    app = _compact_app(1)
    assert not app.exception
    assert str(app.session_state["compact_sim_target_date_cached"].date()) == "2026-12-31"
    assert "monte_carlo_ensemble_result" not in app.session_state


def test_compact_plus_two_runs_create_complete_bound_ensemble():
    app = _compact_app(2)
    assert not app.exception
    app.session_state["test_start_simulation"] = True
    app.run()
    assert not app.exception
    assert "monte_carlo_ensemble_result" in app.session_state, (
        [item.value for item in app.error], [item.value for item in app.info],
        [item.value for item in app.success])
    saved = app.session_state["monte_carlo_ensemble_result"]
    assert saved["status"] == "complete"
    assert saved["count"] == 2
    assert saved["statistics"]["annual_stock_values"]
    app.session_state["test_start_simulation"] = False
    before_instance = app.session_state["simulation_validation_instance"]
    app.run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert app.session_state["monte_carlo_ensemble_result"]["plan_signature"] == saved["plan_signature"]
    settings = dict(app.session_state["simulation_params"])
    settings["compact_plus"] = {**settings["compact_plus"], "run_count": 10}
    app.session_state["simulation_params"] = settings
    app.run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert any("noch nicht berechnet" in item.value for item in app.info)
