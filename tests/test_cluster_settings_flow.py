from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys

import pandas as pd
import pytest
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def _load_settings_module():
    page_path = next((ROOT / "pages").glob("*_Einstellungen.py"))
    spec = importlib.util.spec_from_file_location("settings_page_cluster_flow_test", page_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    module._UNIT_TESTING = True
    spec.loader.exec_module(module)
    return module


class DummyUpload:
    def __init__(self, data: bytes, name: str = "cluster.xlsx"):
        self._data = data
        self.name = name

    def getvalue(self) -> bytes:
        return self._data


class BrokenUpload:
    name = "broken.xlsx"

    def getvalue(self) -> bytes:
        raise RuntimeError("boom")


class DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _build_cluster_workbook_bytes(
    *,
    oe_cluster: str = "OE-Cluster-A",
    jf_cluster: str = "JF-Cluster-A",
) -> bytes:
    payload = io.BytesIO()
    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Cluster": [oe_cluster],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Planstelle": ["P1"],
                "Jobfamily Cluster": [jf_cluster],
            }
        ).to_excel(writer, sheet_name="JobFamilies", index=False)
    return payload.getvalue()


@pytest.fixture(autouse=True)
def clean_session_state(monkeypatch):
    monkeypatch.setattr(st, "session_state", {})


def test_upload_is_validated_but_remains_staged(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    before = module._refresh_active_cluster_source_state(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    result = module._stage_cluster_upload(
        DummyUpload(_build_cluster_workbook_bytes(oe_cluster="Staged", jf_cluster="Staged-JF"), "staged.xlsx"),
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    after = module._refresh_active_cluster_source_state(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert before.subtype == "input_folder.external_file"
    assert result["status"] == "staged"
    assert st.session_state["cluster_upload_staged_valid"] is True
    assert st.session_state["cluster_upload_staged_filename"] == "staged.xlsx"
    assert st.session_state["cluster_upload_staged_oe_mapping_count"] == 1
    assert st.session_state["cluster_upload_staged_jf_mapping_count"] == 1
    assert persisted.exists() is False
    assert after.subtype == "input_folder.external_file"
    assert st.session_state["active_cluster_source_subtype"] == "input_folder.external_file"


def test_apply_now_persists_upload_and_activates_user_override(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    module._refresh_active_cluster_source_state(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    module._stage_cluster_upload(
        DummyUpload(_build_cluster_workbook_bytes(oe_cluster="Persisted", jf_cluster="Persisted-JF"), "persisted.xlsx"),
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    result = module._apply_staged_cluster_upload(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert result["success"] is True
    assert persisted.exists() is True
    assert st.session_state["cluster_override_active"] is True
    assert st.session_state["active_cluster_source_subtype"] == "ui_upload.persisted_local_copy"
    assert st.session_state["active_cluster_source_mode"] == "ui_upload"
    assert "Cluster" not in st.session_state.get("global_uploads", {})
    assert st.session_state.get("cluster_upload_staged_bytes") is None


def test_delete_uploads_removes_staged_and_persisted_and_falls_back_to_input_folder(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    module._refresh_active_cluster_source_state(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    module._stage_cluster_upload(
        DummyUpload(_build_cluster_workbook_bytes(oe_cluster="Persisted", jf_cluster="Persisted-JF"), "persisted.xlsx"),
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    module._apply_staged_cluster_upload(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    result = module._delete_cluster_uploads(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert result["success"] is True
    assert persisted.exists() is False
    assert "Cluster" not in st.session_state.get("global_uploads", {})
    assert st.session_state["cluster_override_active"] is False
    assert st.session_state["active_cluster_source_subtype"] == "input_folder.external_file"
    assert st.session_state.get("cluster_upload_staged_filename") is None


def test_delete_uploads_falls_back_to_synthetic_when_no_external_file_exists(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "missing_external.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"

    module._refresh_active_cluster_source_state(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    module._stage_cluster_upload(
        DummyUpload(_build_cluster_workbook_bytes(oe_cluster="Persisted", jf_cluster="Persisted-JF"), "persisted.xlsx"),
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    module._apply_staged_cluster_upload(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    result = module._delete_cluster_uploads(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert result["success"] is True
    assert st.session_state["active_cluster_source_subtype"] == "synthetic.default_fallback"
    assert st.session_state["active_cluster_source_mode"] == "synthetic"


def test_active_source_and_staged_state_are_kept_consistent_in_session(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    module._refresh_active_cluster_source_state(
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    module._stage_cluster_upload(
        DummyUpload(_build_cluster_workbook_bytes(oe_cluster="Staged", jf_cluster="Staged-JF"), "staged.xlsx"),
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert st.session_state["active_cluster_source_subtype"] == "input_folder.external_file"
    assert st.session_state["cluster_upload_staged_valid"] is True
    assert st.session_state["cluster_upload_staged_hash"]
    assert st.session_state["cluster_upload_staged_uploaded_at"]


def test_same_upload_is_not_restaged_on_every_run(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    upload = DummyUpload(_build_cluster_workbook_bytes(oe_cluster="Staged", jf_cluster="Staged-JF"), "staged.xlsx")
    first = module._stage_cluster_upload(
        upload,
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    first_uploaded_at = st.session_state["cluster_upload_staged_uploaded_at"]

    second = module._stage_cluster_upload(
        upload,
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert first["status"] == "staged"
    assert second["status"] == "already_staged_valid"
    assert st.session_state["cluster_upload_staged_uploaded_at"] == first_uploaded_at


def test_same_as_active_upload_is_ignored_after_first_match(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"
    payload = _build_cluster_workbook_bytes(oe_cluster="Active", jf_cluster="Active-JF")
    persisted.write_bytes(payload)

    upload = DummyUpload(payload, "active.xlsx")
    first = module._stage_cluster_upload(
        upload,
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    second = module._stage_cluster_upload(
        upload,
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert first["status"] == "matches_active"
    assert second["status"] == "ignored_same_upload"
    assert st.session_state["cluster_upload_ignore_hash"] == first["validation"].content_hash
    assert st.session_state.get("cluster_upload_staged_filename") is None


def test_stage_cluster_upload_surfaces_real_exception_and_clears_staged_state(tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    persisted = tmp_path / "cluster_mapping.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))
    st.session_state["cluster_upload_staged_filename"] = "old.xlsx"

    result = module._stage_cluster_upload(
        BrokenUpload(),
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert result["status"] == "exception"
    assert "RuntimeError" in result["message"]
    assert "boom" in result["message"]
    assert st.session_state.get("cluster_upload_staged_filename") is None


def test_cluster_uploader_widget_key_can_be_rotated():
    module = _load_settings_module()

    first = module._get_cluster_uploader_key()
    module._reset_cluster_uploader_widget()
    second = module._get_cluster_uploader_key()

    assert first == "up_cluster_mappings_0"
    assert second == "up_cluster_mappings_1"


def test_render_settings_page_does_not_rerun_on_plain_cluster_upload(monkeypatch, tmp_path):
    module = _load_settings_module()
    external = tmp_path / "OE_Cluster.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External", jf_cluster="External-JF"))

    import utils.settings_loader as settings_loader

    upload = DummyUpload(_build_cluster_workbook_bytes(oe_cluster="Staged", jf_cluster="Staged-JF"), "staged.xlsx")
    reruns: list[tuple] = []
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

    for fn in ["title", "subheader", "caption", "divider", "markdown", "info", "warning", "success", "error", "write", "dataframe", "download_button"]:
        monkeypatch.setattr(module.st, fn, lambda *args, **kwargs: None)
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
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: reruns.append((args, kwargs)))
    monkeypatch.setattr(
        module.st,
        "file_uploader",
        lambda label, *args, **kwargs: upload if "cluster_upload_mapping" in str(label) else None,
    )

    module.render_settings_page()
    module.render_settings_page()

    assert reruns == []
    history = st.session_state.get("cluster_upload_debug_history", [])
    assert any(entry["event"] == "staging_result" and entry.get("status") == "staged" for entry in history)
    assert not any(entry["event"] == "rerun_called" and entry.get("reason") == "cluster_upload_staged" for entry in history)


def test_render_settings_page_does_not_rerun_on_cluster_apply(monkeypatch):
    module = _load_settings_module()

    import utils.settings_loader as settings_loader

    reruns: list[tuple] = []
    payload = _build_cluster_workbook_bytes(oe_cluster="Staged", jf_cluster="Staged-JF")
    st.session_state.update(
        {
            "global_uploads": {},
            "show_reload_success": False,
            "tvoed_available": False,
            "tvoed_lookup": {},
            "cluster_upload_staged_bytes": payload,
            "cluster_upload_staged_filename": "staged.xlsx",
            "cluster_upload_staged_hash": "abc123",
            "cluster_upload_staged_valid": True,
            "cluster_upload_staged_errors": [],
            "cluster_upload_staged_oe_mapping_count": 1,
            "cluster_upload_staged_jf_mapping_count": 1,
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
    module.bump_cache_version = lambda *args, **kwargs: None

    def fake_apply(*args, **kwargs):
        module._clear_staged_cluster_state()
        return {"success": True, "message": "ok"}

    module._apply_staged_cluster_upload = fake_apply
    monkeypatch.setattr(settings_loader, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(settings_loader, "set_setting", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "save_user_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_loader, "load_user_settings", lambda *args, **kwargs: {})

    for fn in ["title", "subheader", "caption", "divider", "markdown", "info", "warning", "success", "error", "write", "dataframe", "download_button"]:
        monkeypatch.setattr(module.st, fn, lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "container", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "expander", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(module.st, "form", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(
        module.st,
        "columns",
        lambda spec: [DummyContext() for _ in range(spec if isinstance(spec, int) else len(spec))],
    )
    monkeypatch.setattr(module.st, "spinner", lambda *args, **kwargs: DummyContext())
    monkeypatch.setattr(
        module.st,
        "button",
        lambda *args, **kwargs: kwargs.get("key") == "cluster_apply_now_button",
    )
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
    monkeypatch.setattr(module.st, "rerun", lambda *args, **kwargs: reruns.append((args, kwargs)))
    monkeypatch.setattr(module.st, "file_uploader", lambda *args, **kwargs: None)

    module.render_settings_page()

    assert reruns == []
    history = st.session_state.get("cluster_upload_debug_history", [])
    assert not any(entry["event"] == "rerun_called" and entry.get("reason") == "cluster_apply_now" for entry in history)
