"""The actual parameter and CompactPlus pages use one saved run setting."""
from __future__ import annotations

from pathlib import Path
from io import BytesIO

from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from utils.monte_carlo_export import build_monte_carlo_export_bytes
from utils.monte_carlo_chart_values import extract_ist_chart_values
from utils.monte_carlo_product_worker import _compact_module
from utils.simulation_params import default_simulation_params


ROOT = Path(__file__).resolve().parents[1]


def test_parameter_page_saves_count_without_duplicate_target_date():
    page = next((ROOT / "pages").glob("8_*_Simulationsparameter.py"))
    app = AppTest.from_file(str(page), default_timeout=90).run()
    assert not app.exception
    app.selectbox(key="sim_params_run_count").set_value(2).run()
    assert not app.exception
    next(button for button in app.button if button.label == "Parameter speichern").click().run()
    assert not app.exception
    saved = app.session_state["simulation_params"]
    assert saved["compact_plus"]["run_count"] == 2
    assert saved["abgaenge"]["random_seed"] == 42
    assert saved["zugaenge"]["random_seed"] == 42
    assert "compact_sim_signature" not in app.session_state
    assert not any(item.key == "sim_params_target_date" for item in app.date_input)
    app.run()
    assert app.selectbox(key="sim_params_run_count").value == 2


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
    assert not any("Monte-Carlo-Kennzahlen" in item.value for item in app.caption)
    assert any(button.label == "Simulationsergebnisse als Excel exportieren" for button in app.button)
    assert not any(button.label == "Monte-Carlo-Kennzahlen als Excel exportieren" for button in app.button)


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
    from components.org_simulation_yearly import build_yearly_view, yearly_periods
    from utils.monte_carlo_org_view import event_band_table, stock_band_table
    summary = app.session_state["compact_sim_event_summary"]
    for level, source_key in (("Organisationseinheit", "annual_oe"),
                              ("OE-Cluster", "annual_cluster")):
        source = summary[source_key]
        view = build_yearly_view(
            summary, dimension=level,
            years=yearly_periods(summary["metadata"])["Jahr"].tolist(),
            organizations=sorted(source[level].unique().tolist()),
            event_types=sorted(source["Ereignisart"].unique().tolist()), metric="Abgänge")
        assert not event_band_table(view, ensemble=saved).empty
        annual = summary["stock_reconciliation"]["annual"]
        stock = annual.loc[annual["Ebene"].eq(level)].drop_duplicates(
            ["Organisation", "Stichtag"], keep="last")
        stock = stock[["Stichtag", "Organisation", "Bestand-MAK"]]
        stock["Bestand-MAK"] = stock["Bestand-MAK"].round(2)
        assert not stock_band_table(
            stock, ensemble=saved, level=level, metric="MAK",
            base_date=summary["metadata"]["base_date"]).empty
    chart_values = {(metric, dimension, group): values for metric, dimension, group, values
                    in saved["chart_statistics"]["values"]}
    reference_chart_values = extract_ist_chart_values(
        app.session_state["compact_sim_prepared_df"], _compact_module())
    assert reference_chart_values
    for metric, dimension, group, value in reference_chart_values:
        assert chart_values[metric, dimension, group]["samples"][0] == value
    workbook = load_workbook(BytesIO(build_monte_carlo_export_bytes(
        ensemble=saved, base_date="2026-09-01", target_date="2026-12-31")),
        read_only=True, data_only=True)
    assert list(workbook["Zielwerte"].values)[1][2:] == tuple(
        saved["statistics"]["target"]["heads"][key] for key in ("mean", "lower", "upper"))
    assert workbook["Jahresereignisse"].max_row == len(saved["statistics"]["annual_values"]) + 1
    assert workbook["Jahresbestaende"].max_row == len(saved["statistics"]["annual_stock_values"]) + 1
    assert any("Ungefilterter Zielbestand aller Läufe" in item.value for item in app.markdown)
    assert any("Vergütung bleibt ohne Fehlerbänder" in item.value for item in app.caption)
    assert any("Zu- und Abgangszahlen stammen aus dem konkreten Referenzlauf" in item.value for item in app.caption)
    assert any(button.label == "Referenzlauf als Excel exportieren" for button in app.button)
    assert any(button.label == "Monte-Carlo-Kennzahlen als Excel exportieren" for button in app.button)
    app.session_state["test_start_simulation"] = False
    before_instance = app.session_state["simulation_validation_instance"]
    next(button for button in app.button if button.label == "Monte-Carlo-Kennzahlen als Excel exportieren").click().run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert app.session_state["compact_plus_monte_carlo_export__lazy_payload"][:2] == b"PK"
    app.run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert app.session_state["monte_carlo_ensemble_result"]["plan_signature"] == saved["plan_signature"]
    app.date_input(key="compact_sim_target_date").set_value("2027-06-15").run()
    assert not app.exception
    assert str(app.session_state["compact_sim_target_date_cached"].date()) == "2027-06-15"
    assert app.session_state["monte_carlo_ensemble_result"]["status"] == "complete"
    assert app.session_state["monte_carlo_ensemble_result"]["plan_signature"] != saved["plan_signature"]
    assert app.session_state["simulation_validation_instance"] != before_instance
    before_instance = app.session_state["simulation_validation_instance"]
    settings = dict(app.session_state["simulation_params"])
    settings["compact_plus"] = {**settings["compact_plus"], "run_count": 10}
    app.session_state["simulation_params"] = settings
    app.run()
    assert not app.exception
    assert app.session_state["simulation_validation_instance"] == before_instance
    assert any("noch nicht berechnet" in item.value for item in app.info)
    assert not any(button.label == "Monte-Carlo-Kennzahlen als Excel exportieren" for button in app.button)
