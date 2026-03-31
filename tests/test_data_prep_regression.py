import copy
import importlib.util
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st

import kpi_reference
from config.settings import DEFAULT_COHORTS, DEFAULT_AZUBI_SALARIES
from dataloader import cluster_manager, loader


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "data_prep_golden_master.json"

FROZEN_SETTINGS = {
    "stichtag": "2025-12-31",
    "include_future_hires": True,
    "exclusions": {
        "vorstand": True,
        "ruhend_bv": True,
        "planstellen_follow_person": True,
        "org_units": [
            "9900",
            "9910",
            "9920",
            "9921",
            "9940",
            "9941",
            "9945",
            "9960",
            "9970",
            "9971",
            "9972",
            "9973",
            "9975",
            "9980",
            "9981",
            "9990",
            "9999",
            "99XX",
        ],
    },
}
FROZEN_STICHTAG = pd.Timestamp(FROZEN_SETTINGS["stichtag"])


def _load_compact_page_module():
    compact_path = next((ROOT / "pages").glob("*_Kompakt.py"))
    spec = importlib.util.spec_from_file_location("compact_page_regression", compact_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_golden():
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _df_manifest(df: pd.DataFrame) -> dict:
    hashed = pd.util.hash_pandas_object(df, index=True, categorize=False)
    payload = hashed.to_numpy().tobytes()
    payload += ("|".join(map(str, df.columns))).encode("utf-8")
    payload += ("|".join(map(str, df.dtypes.astype(str)))).encode("utf-8")
    return {
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": list(map(str, df.columns)),
        "dtypes": {str(k): str(v) for k, v in df.dtypes.items()},
        "null_counts": {str(k): int(v) for k, v in df.isna().sum().items()},
        "hash": __import__("hashlib").sha256(payload).hexdigest(),
    }


def _summary_manifest(summary: dict) -> dict:
    text = json.dumps(summary, sort_keys=True, default=str, ensure_ascii=False)
    return {
        "keys": sorted(summary.keys()),
        "hash": __import__("hashlib").sha256(text.encode("utf-8")).hexdigest(),
    }


def _frozen_get_setting(key: str, default=None):
    value = FROZEN_SETTINGS.get(key, default)
    return copy.deepcopy(value)


@pytest.fixture(autouse=True)
def _freeze_data_prep_context(monkeypatch):
    monkeypatch.setattr(loader, "get_setting", _frozen_get_setting)
    monkeypatch.setattr(kpi_reference, "get_setting", _frozen_get_setting)
    monkeypatch.setattr(loader, "get_current_stichtag", lambda: FROZEN_STICHTAG)
    monkeypatch.setattr(kpi_reference, "get_current_stichtag", lambda: FROZEN_STICHTAG)
    monkeypatch.setattr(
        loader.np.random,
        "normal",
        lambda loc=0.0, scale=1.0, size=None: float(loc) if size is None else np.full(size, float(loc)),
    )


def _reset_data_prep_state():
    st.cache_data.clear()
    st.session_state.clear()
    st.session_state["cohort_definitions"] = DEFAULT_COHORTS.copy()
    st.session_state["azubi_salaries"] = DEFAULT_AZUBI_SALARIES.copy()
    st.session_state["vorstand_jahresgehalt"] = 200000.0
    st.session_state["employer_cost_factor"] = loader.EMPLOYER_COST_FACTOR
    st.session_state["_cache_version_data_prep"] = 0


def _build_reference_original_pipeline():
    original_file_signatures = tuple(
        (name, loader.get_file_signature(path))
        for name, path in sorted(loader.ORIGINAL_FILES.items())
    )
    original = loader.load_original_data(file_signatures=original_file_signatures)
    tvoed_lookup = loader._load_tvoed_lookup_cached(
        None,
        loader.get_file_signature(loader.TVOED_FILE),
    )

    snapshot_df = loader.combine_to_snapshot(
        original["mitarbeiter"],
        original["planstellen"],
        original["atz"],
        original["ausbildung"],
        stichtag=FROZEN_STICHTAG,
        tvoed_lookup=tvoed_lookup,
        include_future_hires=FROZEN_SETTINGS["include_future_hires"],
        employer_factor=loader.EMPLOYER_COST_FACTOR,
        azubi_salaries=DEFAULT_AZUBI_SALARIES,
        vorstand_salary=200000.0,
    )
    snapshot_df = loader.enrich_snapshot_data(
        snapshot_df,
        stichtag=FROZEN_STICHTAG,
        cohort_definitions=DEFAULT_COHORTS,
    )
    snapshot_df = loader._apply_jobfamilies(snapshot_df)
    snapshot_df = loader.apply_clusters_to_snapshot(snapshot_df)
    snapshot_df = loader._zero_out_azubi_mak(snapshot_df)
    snapshot_df = loader.apply_exclusions(snapshot_df, FROZEN_SETTINGS["exclusions"])

    history_df = loader.generate_history_from_snapshot(snapshot_df)
    org_df = loader.create_org_structure(original["planstellen"])
    summary = loader.get_data_summary(snapshot_df)
    summary["data_source_type"] = "Original-Daten"
    return snapshot_df, history_df, org_df, summary


def test_load_and_prepare_data_matches_reference_pipeline_and_golden_master():
    golden = _load_golden()
    _reset_data_prep_state()

    expected_snapshot, expected_history, expected_org, expected_summary = _build_reference_original_pipeline()
    snapshot_df, history_df, org_df, summary = loader.load_and_prepare_data()

    pd.testing.assert_frame_equal(snapshot_df, expected_snapshot, check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(history_df, expected_history, check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(org_df, expected_org, check_dtype=True, check_like=False)
    assert summary == expected_summary

    assert _df_manifest(snapshot_df) == golden["loader"]["snapshot"]
    assert _df_manifest(history_df) == golden["loader"]["history"]
    assert _df_manifest(org_df) == golden["loader"]["org"]
    assert _summary_manifest(summary) == golden["loader"]["summary"]


def test_prepare_compact_data_matches_golden_master():
    golden = _load_golden()
    _reset_data_prep_state()
    compact = _load_compact_page_module()

    snapshot_df, _, _, _ = loader.load_and_prepare_data()
    prepared_df = compact.prepare_compact_data(snapshot_df)

    assert _df_manifest(prepared_df) == golden["compact"]["prepared"]


def test_prepare_compact_data_skips_jobfamily_reassignment_when_present(monkeypatch):
    compact = _load_compact_page_module()
    calls = {"count": 0}

    def _unexpected_assign(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("assign_jobfamilies should not be called when Jobfamily is already present")

    monkeypatch.setattr(compact, "load_jobfamily_definitions", lambda: {"dummy": []})
    monkeypatch.setattr(compact, "assign_jobfamilies", _unexpected_assign)

    prepared = compact._prepare_compact_data_clean(
        pd.DataFrame(
            {
                "Planstelle": ["P-1"],
                "Jobfamily": ["Bestehend"],
                "Betriebszugehörigkeit_Jahre": [4.0],
                "TrfGr": ["9"],
                "St": ["2+"],
                "FTE_person": [1.0],
                "Vertragsart": ["Unbefristet"],
                "Soll_Cost_Year": [123.0],
            }
        )
    )

    assert calls["count"] == 0
    assert prepared.loc[0, "Jobfamily"] == "Bestehend"


def test_load_and_prepare_data_reuses_cached_prepared_bundle(monkeypatch):
    _reset_data_prep_state()
    calls = {"jobfamily": 0}

    base_snapshot = pd.DataFrame(
        {
            "PersNr": ["000001"],
            "Planstelle": ["P-1"],
            "Organisationseinheit": ["OE"],
            "Kürzel OrgEinheit": ["001"],
            "Soll_FTE": [1.0],
            "FTE_assigned": [1.0],
            "Is_Vacant": [False],
            "MAK": [1.0],
            "Total_Cost_Year": [100.0],
        }
    )

    monkeypatch.setattr(
        loader,
        "load_hr_data",
        lambda *args, **kwargs: {
            "snapshot_detail": base_snapshot.copy(),
            "history_cube": pd.DataFrame({"Date": pd.to_datetime(["2026-01-01"])}),
            "org_structure": pd.DataFrame({"Organisationseinheit": ["OE"]}),
        },
    )
    monkeypatch.setattr(loader, "enrich_snapshot_data", lambda df, stichtag=None, cohort_definitions=None: df.copy())

    def _apply_jobfamilies(df):
        calls["jobfamily"] += 1
        out = df.copy()
        out["Jobfamily"] = "JF"
        return out

    monkeypatch.setattr(loader, "_apply_jobfamilies", _apply_jobfamilies)
    monkeypatch.setattr(
        loader,
        "apply_clusters_to_snapshot",
        lambda df, uploaded_file=None: df.assign(**{"OE-Cluster": "OE-C", "JF-Cluster": "JF-C"}),
    )
    monkeypatch.setattr(loader, "_zero_out_azubi_mak", lambda df: df)
    monkeypatch.setattr(loader, "apply_exclusions", lambda df, exclusions: df)
    monkeypatch.setattr(loader, "get_data_summary", lambda df: {"rows": int(len(df))})
    monkeypatch.setattr(loader, "_load_tvoed_lookup_cached", lambda uploaded_tvoed_bytes, tvoed_file_signature: {})

    first = loader.load_and_prepare_data(use_original=False)
    second = loader.load_and_prepare_data(use_original=False)

    assert calls["jobfamily"] == 1
    pd.testing.assert_frame_equal(first[0], second[0], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(first[1], second[1], check_dtype=True, check_like=False)
    pd.testing.assert_frame_equal(first[2], second[2], check_dtype=True, check_like=False)
    assert first[3] == second[3]


def test_load_cluster_mappings_caches_repeated_reads(monkeypatch):
    st.cache_data.clear()
    payload = io.BytesIO()

    with pd.ExcelWriter(payload, engine="xlsxwriter") as writer:
        pd.DataFrame(
            {
                "Organisationseinheit": ["OE1"],
                "Cluster": ["Cluster-A"],
            }
        ).to_excel(writer, sheet_name="OrgUnits", index=False)
        pd.DataFrame(
            {
                "Planstelle": ["P-1"],
                "Jobfamily Cluster": ["JF-Cluster-A"],
            }
        ).to_excel(writer, sheet_name="JobFamilies", index=False)

    real_read_excel = cluster_manager.pd.read_excel
    calls = {"count": 0}

    def _counting_read_excel(*args, **kwargs):
        calls["count"] += 1
        return real_read_excel(*args, **kwargs)

    monkeypatch.setattr(cluster_manager.pd, "read_excel", _counting_read_excel)

    first = cluster_manager.load_cluster_mappings(payload.getvalue())
    second = cluster_manager.load_cluster_mappings(payload.getvalue())

    assert calls["count"] == 2
    assert first == second


def test_exclusion_persist_uses_targeted_invalidation(monkeypatch):
    exclusion_page_path = next((ROOT / "pages").glob("*_Deep_Dive_Exklusionsgruppen.py"))
    spec = importlib.util.spec_from_file_location("exclusion_page_regression", exclusion_page_path)
    exclusion_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exclusion_page)

    called = {"cleared": False}
    monkeypatch.setattr(exclusion_page, "set_setting", lambda *args, **kwargs: True)
    monkeypatch.setattr(exclusion_page.st.cache_data, "clear", lambda: called.__setitem__("cleared", True))
    st.session_state["_cache_version_data_prep"] = 0

    exclusion_page._persist_exclusions(True, True, ["9900"])

    assert st.session_state["_cache_version_data_prep"] == 1
    assert called["cleared"] is False
