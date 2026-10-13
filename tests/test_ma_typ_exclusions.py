"""Compatibility and optional employee-type exclusions, without private input data."""
import copy
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from dataloader.loader import apply_exclusions
from dataloader.soll_ist_koepfe_engine import _apply_variant_b_exclusions
from utils.exclusion_groups import effective_exclusions, get_all_group_stats, get_group_detail

reference_path = Path(__file__).parent / "fixtures" / "exclusions_legacy_reference.py"
spec = importlib.util.spec_from_file_location("legacy_exclusions", reference_path)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


@pytest.fixture
def snapshot():
    return pd.DataFrame({
        "PersNr": ["1", "2", "3", "4", "5", "6", None],
        "Personalnummer": ["1", "2", "3", "4", "5", "6", None],
        "Planstellennr": ["100", "101", "102", "103", "104", "50001903", "106"],
        "Kürzel OrgEinheit": ["1000", "9971", "9900", "9988", "9910", "1000", "9971"],
        "Organisationseinheit": ["Team"] * 7,
        "MitarbGruppenbez.": ["Vorstand", "Angestellte", "Angestellte", "Angestellte", "Auszubildende", "Angestellte", None],
        "Status kundenindividuell": ["Aktiv", "Aktiv", "Ruhendes Beschäftigungsverhältnis", "Aktiv", "Aktiv", "Aktiv", None],
        "MA-Typ": [" Vorstand ", "Trainee", "Angestellte", "Trainee", "Ausbildung", "Trainee", None],
        "BsGrd": [100., 80., 0., 50., 0., 100., 0.],
        "MAK": [1., .8, 0., .5, 0., 1., 0.],
        "Sollarbeitszeit": [39., 39., 39., .01, 39., 39., 39.],
        "Soll_FTE": [1., 1., 1., 0., 1., 1., 1.],
        "Is_Vacant": [False] * 6 + [True],
    })


OLD_CONFIGS = [
    {}, {"vorstand": True}, {"ruhend_bv": True},
    {"org_units": ["9971"]}, {"org_units": ["99XX"]},
    {"special_groups": ["ausbildung_nachwuchs"]},
    {"special_groups": ["sollarbeitszeit_001_positions", "jobfamily_validation_special_positions"]},
    {"vorstand": True, "ruhend_bv": True, "org_units": ["9900", "9971", "99XX"],
     "special_groups": ["ausbildung_nachwuchs", "sollarbeitszeit_001_positions", "jobfamily_validation_special_positions"]},
]


@pytest.mark.parametrize("config", OLD_CONFIGS)
@pytest.mark.parametrize("follow", [False, True])
@pytest.mark.parametrize("with_types", [False, True])
def test_legacy_matches_frozen_engine(snapshot, config, follow, with_types):
    if not with_types:
        snapshot = snapshot.drop(columns="MA-Typ")
    config = dict(config, planstellen_follow_person=follow)
    for additions in [{}, {"selection_basis": "legacy", "ma_typ_values": ["Trainee"]},
                      {"selection_basis": "combined", "ma_typ_values": []}]:
        pd.testing.assert_frame_equal(apply_exclusions(snapshot, dict(config, **additions)),
                                      legacy.apply_exclusions(snapshot, config))
        pd.testing.assert_frame_equal(_apply_variant_b_exclusions(snapshot, dict(config, **additions)),
                                      legacy._apply_variant_b_exclusions(snapshot, config))


@pytest.mark.parametrize("basis, expected", [("legacy", {0}), ("ma_typ", {1, 3, 5}), ("combined", {0, 1, 3, 5})])
def test_modes_use_same_vacating_semantics(snapshot, basis, expected):
    config = {"vorstand": True, "selection_basis": basis, "ma_typ_values": [" Trainee ", "Trainee", ""]}
    before = copy.deepcopy(config)
    result = apply_exclusions(snapshot, config)
    assert set(result.index[result.Is_Excluded]) == expected
    assert result.loc[list(expected), "PersNr"].isna().all()
    assert result.loc[list(expected), "MAK"].eq(0).all()
    pd.testing.assert_series_equal(result.Soll_FTE, snapshot.Soll_FTE)
    pd.testing.assert_series_equal(result["MA-Typ"], snapshot["MA-Typ"])
    assert config == before


def test_type_exclusion_overrides_technical_ist_preservation(snapshot):
    config = {"special_groups": ["sollarbeitszeit_001_positions", "jobfamily_validation_special_positions"]}
    old = apply_exclusions(snapshot, config)
    assert old.loc[[3, 5], "Is_IST_Preserved_Despite_Exclusion"].all()
    new = apply_exclusions(snapshot, dict(config, selection_basis="combined", ma_typ_values=["Trainee"]))
    assert not new.loc[[3, 5], "Is_IST_Preserved_Despite_Exclusion"].any()
    assert new.loc[[3, 5], "MAK"].eq(0).all()


def test_missing_column_does_not_fall_back_to_inactive_groups(snapshot):
    config = {"selection_basis": "ma_typ", "vorstand": True, "ma_typ_values": ["Trainee"]}
    result = apply_exclusions(snapshot.drop(columns="MA-Typ"), config)
    assert not result.Is_Excluded.any()
    combined = dict(config, selection_basis="combined")
    assert apply_exclusions(snapshot.drop(columns="MA-Typ"), combined).Is_Excluded.sum() == 1


def test_matrix_reuses_selection_and_missing_values_are_not_categories(snapshot):
    config = {"selection_basis": "ma_typ", "vorstand": True, "ma_typ_values": ["Trainee", "", None]}
    assert set(_apply_variant_b_exclusions(snapshot, config).index) == {0, 2, 4, 6}
    stats = get_all_group_stats(snapshot)
    assert stats.loc[stats.gruppe_key.eq("ma_typ:Trainee"), "planstellen"].item() == 3
    assert len(get_group_detail(snapshot, "ma_typ:Trainee")) == 3
    assert effective_exclusions(effective_exclusions(config)) == effective_exclusions(config)


def test_preview_does_not_modify_input_or_counts(snapshot):
    import streamlit as st
    before = snapshot.copy(deep=True)
    st.session_state["last_exclusion_count"] = 123
    apply_exclusions(snapshot, {"selection_basis": "ma_typ", "ma_typ_values": ["Trainee"]}, record_counts=False)
    pd.testing.assert_frame_equal(snapshot, before)
    assert st.session_state["last_exclusion_count"] == 123


@pytest.fixture
def exclusion_app(snapshot, monkeypatch, tmp_path):
    import json
    import sys
    from streamlit.testing.v1 import AppTest
    from utils import settings_loader

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"exclusions": dict(vorstand=True, ruhend_bv=False,
        org_units=[], special_groups=[], planstellen_follow_person=False)}), encoding="utf-8")
    monkeypatch.setattr(settings_loader, "SETTINGS_FILE", settings_path)
    path = next((Path(__file__).parents[1] / "pages").glob("*_Deep_Dive_Exklusionsgruppen.py"))
    spec = importlib.util.spec_from_file_location("exclusion_ui_test_page", path)
    page = importlib.util.module_from_spec(spec)
    page._UNIT_TESTING = True
    spec.loader.exec_module(page)
    monkeypatch.setitem(sys.modules, "exclusion_ui_test_page", page)
    monkeypatch.setattr(page, "load_and_prepare_data", lambda: (
        apply_exclusions(snapshot, settings_loader.get_setting("exclusions", {})), pd.DataFrame(), pd.DataFrame(), {}))
    monkeypatch.setattr(page, "render_global_filters", lambda *args: None)
    monkeypatch.setattr(page, "_render_structural_profile", lambda *args: None)
    monkeypatch.setattr(page, "_render_demo_profile", lambda *args: None)
    app = AppTest.from_string("from exclusion_ui_test_page import main\nmain()", default_timeout=20)
    return app, settings_path, snapshot


def test_ui_draft_switching_and_apply(exclusion_app):
    import json
    app, path, _ = exclusion_app
    original = path.read_bytes()
    app.run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Ausschlüsse festlegen", "Auswirkungen", "Gruppen untersuchen"]
    assert len(app.tabs[1].metric) == 3
    applied_metrics = [metric.value for metric in app.tabs[1].metric]
    app.radio(key="ex_selection_basis").set_value("ma_typ").run()
    assert not app.exception
    assert not any(w.key == "ex_chk_vorstand" for w in app.checkbox)
    app.multiselect(key="ex_ma_typ_values").set_value(["Trainee"]).run()
    assert path.read_bytes() == original
    assert [metric.value for metric in app.tabs[1].metric] == applied_metrics
    assert len(app.tabs[0].button) == 1
    app.radio(key="ex_selection_basis").set_value("legacy").run()
    assert app.checkbox(key="ex_chk_vorstand").value is True
    app.radio(key="ex_selection_basis").set_value("combined").run()
    assert app.multiselect(key="ex_ma_typ_values").value == ["Trainee"]
    assert path.read_bytes() == original
    app.button(key="btn_apply_exclusions").click().run()
    assert not app.exception
    config = json.loads(path.read_text(encoding="utf-8"))["exclusions"]
    assert config["selection_basis"] == "combined"
    assert config["ma_typ_values"] == ["Trainee"]
    assert config["vorstand"] is True
    assert [metric.value for metric in app.tabs[1].metric] != applied_metrics
    app.radio(key="ex_impact_measure").set_value("mak").run()
    assert not app.exception
    assert not any("Noch nicht angewendet" in item.value for item in app.info)


def test_ui_missing_column_preserves_saved_selection(exclusion_app):
    import json
    app, path, snapshot = exclusion_app
    config = json.loads(path.read_text(encoding="utf-8"))
    config["exclusions"].update(selection_basis="ma_typ", ma_typ_values=["Trainee"])
    path.write_text(json.dumps(config), encoding="utf-8")
    snapshot.drop(columns="MA-Typ", inplace=True)
    app.run()
    assert not app.exception
    assert app.multiselect(key="ex_ma_typ_values").disabled
    assert app.multiselect(key="ex_ma_typ_values").value == ["Trainee"]
    assert any("keine Spalte MA-Typ" in warning.value for warning in app.warning)


def test_ui_file_switch_retains_draft_and_reports_missing_types(exclusion_app):
    app, _, snapshot = exclusion_app
    app.run()
    app.radio(key="ex_selection_basis").set_value("ma_typ").run()
    app.multiselect(key="ex_ma_typ_values").set_value(["Trainee"]).run()
    original_types = snapshot["MA-Typ"].copy()
    snapshot.drop(columns="MA-Typ", inplace=True)
    app.run()
    assert not app.exception
    assert app.multiselect(key="ex_ma_typ_values").value == ["Trainee"]
    assert app.multiselect(key="ex_ma_typ_values").disabled
    snapshot["MA-Typ"] = original_types
    app.run()
    assert not app.exception
    assert not app.multiselect(key="ex_ma_typ_values").disabled
    assert app.multiselect(key="ex_ma_typ_values").value == ["Trainee"]
