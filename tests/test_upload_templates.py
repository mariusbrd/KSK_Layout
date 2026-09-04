from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys

import openpyxl
import pandas as pd
import pytest
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dataloader.upload_templates import (
    TEMPLATE_SPECS,
    generate_tvoed_template_bytes,
    generate_upload_template_bytes,
)


def _load_settings_module():
    page_path = next((ROOT / "pages").glob("*_Einstellungen.py"))
    spec = importlib.util.spec_from_file_location("settings_page_upload_templates_test", page_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    module._UNIT_TESTING = True
    spec.loader.exec_module(module)
    return module


class DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def clean_session_state(monkeypatch):
    monkeypatch.setattr(st, "session_state", {})


@pytest.mark.parametrize("spec_key", list(TEMPLATE_SPECS.keys()))
def test_generate_upload_template_has_expected_columns_and_hidden_lists_sheet(spec_key):
    spec = TEMPLATE_SPECS[spec_key]
    data = generate_upload_template_bytes(spec_key)
    assert data

    xls = pd.ExcelFile(io.BytesIO(data))
    assert spec["sheet_name"] in xls.sheet_names

    df = pd.read_excel(xls, sheet_name=spec["sheet_name"])
    assert list(df.columns) == [col["name"] for col in spec["columns"]]
    assert len(df) == 0  # header-only template, no example rows

    choice_columns = [c for c in spec["columns"] if c["type"] == "choice" and c.get("choices")]
    if choice_columns:
        assert "Listen" in xls.sheet_names
        wb = openpyxl.load_workbook(io.BytesIO(data))
        assert wb["Listen"].sheet_state == "hidden"

        ws = wb[spec["sheet_name"]]
        validated_cols = {list(dv.sqref.ranges)[0].min_col for dv in ws.data_validations.dataValidation}
        for idx, col in enumerate(spec["columns"], start=1):
            if col["type"] == "choice" and col.get("choices"):
                assert idx in validated_cols, f"{spec_key}: missing dropdown validation on '{col['name']}'"


@pytest.mark.parametrize("spec_key", list(TEMPLATE_SPECS.keys()))
def test_upload_templates_contain_lineage_report(spec_key):
    data = generate_upload_template_bytes(spec_key)
    xls = pd.ExcelFile(io.BytesIO(data))

    assert "Lineage_Report" in xls.sheet_names
    lineage = pd.read_excel(xls, sheet_name="Lineage_Report")
    assert lineage["Lineage-ID"].tolist() == ["2-02"]
    assert f"Template={spec_key}" in lineage.loc[0, "Export-Kontext"]


def test_strict_choice_columns_use_stop_validation_and_soft_ones_use_warning():
    data = generate_upload_template_bytes("ATZ")
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb["ATZ"]

    dv_by_col = {}
    for dv in ws.data_validations.dataValidation:
        dv_by_col[list(dv.sqref.ranges)[0].min_col] = dv

    # Modell (col E = 5) is non-strict -> warning
    assert dv_by_col[5].errorStyle == "warning"
    # Phase (col F = 6) is strict -> default/stop (openpyxl omits explicit "stop")
    assert dv_by_col[6].errorStyle in (None, "stop")


def test_generate_tvoed_template_matches_loader_layout():
    from config.settings import TARIFF_GROUPS

    data = generate_tvoed_template_bytes()
    assert data

    xls = pd.ExcelFile(io.BytesIO(data))
    assert "Entgelttabelle" in xls.sheet_names

    # tvoed_loader.load_tvoed_table() reads with header=1 (row 2 = column headers)
    df = pd.read_excel(xls, sheet_name="Entgelttabelle", header=1)
    assert list(df.columns) == ["€", 1, 2, 3, 4, 5, 6]
    assert list(df[df.columns[0]]) == TARIFF_GROUPS
    assert df.drop(columns=[df.columns[0]]).isna().all().all()


def test_tvoed_template_contains_lineage_report():
    data = generate_tvoed_template_bytes()
    xls = pd.ExcelFile(io.BytesIO(data))

    assert "Lineage_Report" in xls.sheet_names
    lineage = pd.read_excel(xls, sheet_name="Lineage_Report")
    assert lineage["Lineage-ID"].tolist() == ["2-03"]
    assert "Exporttyp=TVOED Upload-Template" in lineage.loc[0, "Export-Kontext"]


def test_render_settings_page_offers_a_download_button_for_every_upload_template(monkeypatch):
    module = _load_settings_module()

    import utils.settings_loader as settings_loader

    st.session_state.update(
        {
            "global_uploads": {},
            "show_reload_success": False,
            "tvoed_available": False,
            "tvoed_lookup": {},
        }
    )

    module.render_metric_selector_only = lambda *args, **kwargs: None
    module.set_metric_page_hint = lambda *args, **kwargs: None
    module.t = lambda key, **kwargs: key
    module.load_and_prepare_data = lambda *args, **kwargs: (
        pd.DataFrame({"Organisationseinheit": ["OE1"], "Planstelle": ["P1"]}),
        pd.DataFrame(),
        pd.DataFrame(),
        {},
    )
    module.load_jobfamily_definitions = lambda *args, **kwargs: {}
    module.SourceService.GROUPS = {}
    monkeypatch.setattr(settings_loader, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(settings_loader, "set_setting", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "save_user_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "load_user_settings", lambda *args, **kwargs: {})

    download_calls: list[dict] = []

    def _fake_download_button(label, *args, **kwargs):
        download_calls.append({
            "label": label,
            "data": kwargs.get("data"),
            "file_name": kwargs.get("file_name"),
            "key": kwargs.get("key"),
        })
        return False

    for fn in ["title", "subheader", "caption", "divider", "markdown", "info", "warning", "success", "error", "write", "dataframe"]:
        monkeypatch.setattr(module.st, fn, lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "download_button", _fake_download_button)
    monkeypatch.setattr(
        module,
        "lazy_excel_download_button_compat",
        lambda **kwargs: download_calls.append({
            "label": kwargs.get("label"),
            "data": kwargs["data_builder"](),
            "file_name": kwargs.get("file_name"),
            "key": kwargs.get("key"),
        }),
    )
    monkeypatch.setattr(module.st, "container", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(
        module.st,
        "columns",
        lambda spec: [DummyContext() for _ in range(spec if isinstance(spec, int) else len(spec))],
    )
    monkeypatch.setattr(module.st, "spinner", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "form_submit_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "checkbox", lambda label, value=False, **kwargs: value)
    monkeypatch.setattr(
        module.st,
        "number_input",
        lambda label, value=None, min_value=None, **kwargs: value if value is not None else min_value,
    )
    monkeypatch.setattr(module.st, "selectbox", lambda label, options, index=0, **kwargs: options[index])
    monkeypatch.setattr(module.st, "text_input", lambda label, value="", **kwargs: value)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "file_uploader", lambda label, *args, **kwargs: None)

    module.render_settings_page()

    template_keys = {call["key"] for call in download_calls}
    expected_keys = {
        "dl_template_mitarbeiter",
        "dl_template_planstellen",
        "dl_template_atz",
        "dl_template_ausbildung",
        "dl_template_tvoed",
    }
    assert expected_keys.issubset(template_keys)

    file_names = {call["key"]: call["file_name"] for call in download_calls}
    assert file_names["dl_template_mitarbeiter"] == "Mitarbeiter_Template.xlsx"
    assert file_names["dl_template_planstellen"] == "Planstellen_Template.xlsx"
    assert file_names["dl_template_atz"] == "ATZ_Template.xlsx"
    assert file_names["dl_template_ausbildung"] == "Ausbildung_Template.xlsx"
    assert file_names["dl_template_tvoed"] == "TVOED_Template.xlsx"

    expected_lineage = {
        "dl_template_mitarbeiter": "2-02",
        "dl_template_planstellen": "2-02",
        "dl_template_atz": "2-02",
        "dl_template_ausbildung": "2-02",
        "dl_template_tvoed": "2-03",
    }
    for key, lineage_id in expected_lineage.items():
        payload = next(call["data"] for call in download_calls if call["key"] == key)
        lineage = pd.read_excel(io.BytesIO(payload), sheet_name="Lineage_Report")
        assert lineage["Lineage-ID"].tolist() == [lineage_id]


def test_render_settings_page_cluster_template_download_contains_lineage_report(monkeypatch):
    module = _load_settings_module()

    import utils.settings_loader as settings_loader

    st.session_state.update(
        {
            "global_uploads": {},
            "show_reload_success": False,
            "tvoed_available": False,
            "tvoed_lookup": {},
        }
    )

    module.render_metric_selector_only = lambda *args, **kwargs: None
    module.set_metric_page_hint = lambda *args, **kwargs: None
    module.t = lambda key, **kwargs: key
    module.load_and_prepare_data = lambda *args, **kwargs: (
        pd.DataFrame({
            "Kürzel OrgEinheit": ["100", "200"],
            "OrgEinheitNr": [100, 200],
            "Organisationseinheit": ["Markt", "IT"],
            "Planstelle": ["Berater/in", "Administrator/in"],
        }),
        pd.DataFrame(),
        pd.DataFrame(),
        {},
    )
    module.load_jobfamily_definitions = lambda *args, **kwargs: {}
    module.SourceService.GROUPS = {}
    monkeypatch.setattr(settings_loader, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(settings_loader, "set_setting", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "save_user_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "load_user_settings", lambda *args, **kwargs: {})

    download_calls: list[dict] = []

    def _fake_download_button(label, *args, **kwargs):
        download_calls.append({
            "label": label,
            "data": kwargs.get("data"),
            "file_name": kwargs.get("file_name"),
            "key": kwargs.get("key"),
        })
        return False

    def _fake_button(label, *args, **kwargs):
        return "settings.cluster_generate_template" in str(label)

    for fn in ["title", "subheader", "caption", "divider", "markdown", "info", "warning", "success", "error", "write", "dataframe"]:
        monkeypatch.setattr(module.st, fn, lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "download_button", _fake_download_button)
    monkeypatch.setattr(
        module,
        "lazy_excel_download_button_compat",
        lambda **kwargs: download_calls.append({
            "label": kwargs.get("label"),
            "data": None,
            "file_name": kwargs.get("file_name"),
            "key": kwargs.get("key"),
        }),
    )
    monkeypatch.setattr(module.st, "container", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(
        module.st,
        "columns",
        lambda spec: [DummyContext() for _ in range(spec if isinstance(spec, int) else len(spec))],
    )
    monkeypatch.setattr(module.st, "spinner", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "button", _fake_button)
    monkeypatch.setattr(module.st, "form_submit_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "checkbox", lambda label, value=False, **kwargs: value)
    monkeypatch.setattr(
        module.st,
        "number_input",
        lambda label, value=None, min_value=None, **kwargs: value if value is not None else min_value,
    )
    monkeypatch.setattr(module.st, "selectbox", lambda label, options, index=0, **kwargs: options[index])
    monkeypatch.setattr(module.st, "text_input", lambda label, value="", **kwargs: value)
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "file_uploader", lambda label, *args, **kwargs: None)

    module.render_settings_page()

    cluster_download = next(call for call in download_calls if call["key"] == "dl_cluster_template")
    assert cluster_download["file_name"] == "Cluster-Template.xlsx"

    lineage = pd.read_excel(io.BytesIO(cluster_download["data"]), sheet_name="Lineage_Report")
    assert lineage["Lineage-ID"].tolist() == ["2-04"]
