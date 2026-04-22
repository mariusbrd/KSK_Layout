import io

import pandas as pd

from dataloader.cluster_resolver import (
    STATUS_FALLBACK,
    STATUS_UPLOADED_NOT_APPLIED,
    SUBTYPE_INPUT_EXTERNAL,
    SUBTYPE_SYNTHETIC_FALLBACK,
    SUBTYPE_UI_UPLOAD_PERSISTED,
    SUBTYPE_UI_UPLOAD_SESSION,
    discover_cluster_sources,
    get_active_cluster_source,
    invalidate_cluster_dependent_state,
    resolve_active_cluster_source,
)


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


def test_ui_upload_session_has_highest_priority(tmp_path):
    persisted = tmp_path / "cluster_mapping.xlsx"
    external = tmp_path / "OE_Cluster.xlsx"
    persisted.write_bytes(_build_cluster_workbook_bytes(oe_cluster="Persisted"))
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External"))

    session_state = {
        "cluster_upload_active_bytes": _build_cluster_workbook_bytes(oe_cluster="Session"),
        "cluster_upload_active_filename": "session.xlsx",
    }

    active = get_active_cluster_source(
        session_state=session_state,
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert active.subtype == SUBTYPE_UI_UPLOAD_SESSION
    assert active.status == "active"
    assert active.is_active is True
    assert active.oe_mapping_count == 1


def test_persisted_local_copy_has_priority_over_external_file(tmp_path):
    persisted = tmp_path / "cluster_mapping.xlsx"
    external = tmp_path / "OE_Cluster.xlsx"
    persisted.write_bytes(_build_cluster_workbook_bytes(oe_cluster="Persisted"))
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External"))

    active = get_active_cluster_source(
        session_state={},
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )

    assert active.subtype == SUBTYPE_UI_UPLOAD_PERSISTED
    assert active.status == "active"


def test_resolver_falls_back_to_external_input_folder_file(tmp_path):
    external = tmp_path / "OE_Cluster.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External"))

    active = get_active_cluster_source(
        session_state={},
        persisted_local_path=str(tmp_path / "missing_cluster_mapping.xlsx"),
        external_file_path=str(external),
    )

    assert active.subtype == SUBTYPE_INPUT_EXTERNAL
    assert active.status == "active"


def test_resolver_falls_back_to_synthetic_when_no_real_source_exists(tmp_path):
    active = get_active_cluster_source(
        session_state={},
        persisted_local_path=str(tmp_path / "missing_cluster_mapping.xlsx"),
        external_file_path=str(tmp_path / "missing_external.xlsx"),
    )

    assert active.subtype == SUBTYPE_SYNTHETIC_FALLBACK
    assert active.status == STATUS_FALLBACK
    assert active.is_active is True


def test_invalid_source_is_skipped_in_resolution(tmp_path):
    persisted = tmp_path / "cluster_mapping.xlsx"
    external = tmp_path / "OE_Cluster.xlsx"
    persisted.write_text("not an excel file", encoding="utf-8")
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External"))

    discovered = discover_cluster_sources(
        session_state={},
        persisted_local_path=str(persisted),
        external_file_path=str(external),
    )
    persisted_source = next(src for src in discovered if src.subtype == SUBTYPE_UI_UPLOAD_PERSISTED)
    assert persisted_source.is_valid is False
    assert persisted_source.status == "invalid"

    active = resolve_active_cluster_source(discovered)
    assert active.subtype == SUBTYPE_INPUT_EXTERNAL


def test_staged_upload_is_not_automatically_activated(tmp_path):
    external = tmp_path / "OE_Cluster.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External"))

    session_state = {
        "cluster_upload_staged_bytes": _build_cluster_workbook_bytes(oe_cluster="Staged"),
        "cluster_upload_staged_filename": "staged.xlsx",
    }

    discovered = discover_cluster_sources(
        session_state=session_state,
        persisted_local_path=str(tmp_path / "missing_cluster_mapping.xlsx"),
        external_file_path=str(external),
    )
    staged_source = next(src for src in discovered if src.subtype == SUBTYPE_UI_UPLOAD_SESSION)
    assert staged_source.status == STATUS_UPLOADED_NOT_APPLIED

    active = resolve_active_cluster_source(discovered)
    assert active.subtype == SUBTYPE_INPUT_EXTERNAL


def test_legacy_global_upload_alias_is_ignored_for_cluster_discovery(tmp_path):
    external = tmp_path / "OE_Cluster.xlsx"
    external.write_bytes(_build_cluster_workbook_bytes(oe_cluster="External"))

    session_state = {
        "global_uploads": {
            "Cluster": _build_cluster_workbook_bytes(oe_cluster="Legacy-Session"),
        }
    }

    discovered = discover_cluster_sources(
        session_state=session_state,
        persisted_local_path=str(tmp_path / "missing_cluster_mapping.xlsx"),
        external_file_path=str(external),
    )

    assert all(src.session_key != "global_uploads.Cluster" for src in discovered)

    active = resolve_active_cluster_source(discovered)
    assert active.subtype == SUBTYPE_INPUT_EXTERNAL


def test_invalidate_cluster_dependent_state_removes_expected_keys():
    session_state = {
        "abgaenge_results": object(),
        "abgaenge_global_result": object(),
        "abgaenge_params": object(),
        "abgaenge_ui_state": object(),
        "abgaenge_timestamp": object(),
        "abgaenge_cluster_source_signature": object(),
        "zugaenge_global_result": object(),
        "zugaenge_vacancies": object(),
        "zugaenge_start_date": object(),
        "zugaenge_end_date": object(),
        "zugaenge_use_azubis": object(),
        "zugaenge_use_trainees": object(),
        "zugaenge_use_newhires": object(),
        "zugaenge_cluster_source_signature": object(),
        "hybrid_abg_res": object(),
        "hybrid_abg_params": object(),
        "hybrid_zug_res": object(),
        "hybrid_zug_params": object(),
        "hybrid_cluster_source_signature": object(),
        "compact_sim_signature": object(),
        "compact_sim_cluster_source_signature": object(),
        "compact_sim_prepared_df": object(),
        "compact_sim_metadata": object(),
        "compact_sim_target_date_cached": object(),
        "ui_matrix_snapshot": object(),
        "untouched_key": "keep-me",
    }

    result = invalidate_cluster_dependent_state(session_state, reason="test")

    assert result["reason"] == "test"
    assert result["removed_count"] == len(result["removed_keys"])
    assert "abgaenge_results" not in session_state
    assert "compact_sim_signature" not in session_state
    assert "compact_sim_cluster_source_signature" not in session_state
    assert session_state["untouched_key"] == "keep-me"
