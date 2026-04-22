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
