import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from dataloader.cluster_resolver import ClusterMappingBundle
import dataloader.compact_simulation_engine as engine


ROOT = Path(__file__).resolve().parents[1]


class DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyColumn(DummyContext):
    def markdown(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def date_input(self, label, value=None, **kwargs):
        return value


def _dummy_columns(spec):
    count = spec if isinstance(spec, int) else len(spec)
    return [DummyColumn() for _ in range(count)]


def _load_page7_module():
    page_path = next((ROOT / "pages").glob("*_Kompakt_plus_Simulation.py"))
    spec = importlib.util.spec_from_file_location("compact_sim_cluster_test", page_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _active_source(signature: str):
    return SimpleNamespace(
        mode="ui_upload",
        subtype="ui_upload.persisted_local_copy",
        status="active",
        source_signature=signature,
        source_path="cluster.xlsx",
        is_valid=True,
    )


def test_finalize_future_snapshot_uses_explicit_source_and_bundle(monkeypatch):
    calls = {}
    source = _active_source("cluster-sig-a")
    bundle = ClusterMappingBundle(
        oe_map={"OE1": "OE-Cluster-1"},
        jf_map={"Angestellte": "JF-Cluster-1"},
        source_signature="cluster-sig-a",
    )
    future_df = pd.DataFrame(
        {
            "PersNr": ["000001"],
            "Organisationseinheit": ["OE1"],
            "Jobfamily": ["Angestellte"],
            "Sollarbeitszeit": [39.0],
            "BsGrd": [100.0],
            "MAK_Calculated": [1.0],
        }
    )

    monkeypatch.setattr(engine, "calculate_mak_vectorized", lambda df, *_args, **_kwargs: df.assign(MAK_Calculated=df.get("MAK_Calculated", 1.0)))
    monkeypatch.setattr(engine, "normalize_education_series", lambda series: series)
    monkeypatch.setattr(engine, "_zero_out_azubi_mak", lambda df: df)
    monkeypatch.setattr(engine, "calculate_cost_vectorized", lambda df, *_args, **_kwargs: df)
    monkeypatch.setattr(engine, "enrich_snapshot_data", lambda df, **_kwargs: df)
    monkeypatch.setattr(engine, "apply_exclusions", lambda df, *_args, **_kwargs: df)
    monkeypatch.setattr(
        engine,
        "apply_clusters_to_snapshot_from_source",
        lambda df, active_cluster_source, mapping_bundle=None: (
            calls.update(
                {
                    "active_cluster_source": active_cluster_source,
                    "mapping_bundle": mapping_bundle,
                }
            )
            or df.assign(OE_Cluster_Test="ok")
        ),
    )

    result = engine._finalize_future_snapshot(
        future_df,
        pd.Timestamp("2026-12-31"),
        active_cluster_source=source,
        cluster_mapping_bundle=bundle,
    )

    assert calls["active_cluster_source"] is source
    assert calls["mapping_bundle"] is bundle
    assert "OE_Cluster_Test" in result.columns


def test_simulate_compact_snapshot_passes_explicit_cluster_context(monkeypatch):
    source = _active_source("cluster-sig-a")
    bundle = ClusterMappingBundle(source_signature="cluster-sig-a")
    snapshot_df = pd.DataFrame({"PersNr": ["000001"], "Organisationseinheit": ["OE1"], "Jobfamily": ["Angestellte"]})
    employee_base = pd.DataFrame({"PersNr": ["000001"], "mak": [1.0], "active": [True]})
    zug_events = pd.DataFrame({"persnr": ["NH_001"], "type": ["New_Hire"], "date": [pd.Timestamp("2026-06-01")]})
    calls = {"enrich": None, "finalize": []}

    monkeypatch.setattr(engine, "_prepare_employee_forecast_base", lambda *_args, **_kwargs: employee_base.copy())
    monkeypatch.setattr(engine, "run_forecast_abgaenge", lambda **_kwargs: {"events_person_level": pd.DataFrame(), "tables": {"atz_pivot": pd.DataFrame()}})
    monkeypatch.setattr(engine, "_apply_attrition_events_to_employee_state", lambda *_args, **_kwargs: employee_base.copy())
    monkeypatch.setattr(engine, "_build_vacancies_from_attrition", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        engine,
        "run_forecast_zugaenge",
        lambda **_kwargs: {"events": zug_events.copy(), "final_state": employee_base.copy()},
    )
    monkeypatch.setattr(
        engine,
        "enrich_zugaenge_events",
        lambda events_df, snapshot_df, run_params, cluster_mapping_bundle=None, active_cluster_source_signature=None: (
            calls.update(
                {
                    "enrich": {
                        "cluster_mapping_bundle": cluster_mapping_bundle,
                        "active_cluster_source_signature": active_cluster_source_signature,
                    }
                }
            )
            or events_df.assign(JF_Cluster_Test="ok")
        ),
    )
    monkeypatch.setattr(engine, "_apply_salary_automation_to_employee_state", lambda df, **_kwargs: df)
    monkeypatch.setattr(engine, "_resolve_atz_status_map", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(engine, "_update_existing_rows", lambda snapshot_df, **_kwargs: snapshot_df.copy())
    monkeypatch.setattr(engine, "_append_new_people_rows", lambda future_df, **_kwargs: future_df.copy())
    monkeypatch.setattr(
        engine,
        "_finalize_future_snapshot",
        lambda future_df, target_date, *, active_cluster_source=None, cluster_mapping_bundle=None: (
            calls["finalize"].append((active_cluster_source, cluster_mapping_bundle)) or future_df
        ),
    )

    result = engine.simulate_compact_snapshot(
        snapshot_df=snapshot_df,
        df_atz=pd.DataFrame(),
        target_date=pd.Timestamp("2026-12-31"),
        base_date=pd.Timestamp("2025-12-31"),
        abgaenge_params={"components": {"atz": False, "retirement": False, "quit": False, "ruhend": False}},
        zugaenge_params={"azubi": {"active": False}, "trainee": {"active": False}, "new_hires": {"active": False}},
        active_cluster_source=source,
        cluster_mapping_bundle=bundle,
        cluster_source_signature="cluster-sig-a",
    )

    assert calls["enrich"]["cluster_mapping_bundle"] is bundle
    assert calls["enrich"]["active_cluster_source_signature"] == "cluster-sig-a"
    assert calls["finalize"] == [(source, bundle)]
    assert result.metadata["cluster_source_signature"] == "cluster-sig-a"


def test_page7_simulation_signature_changes_with_cluster_signature():
    module = _load_page7_module()

    sig_a = module._build_simulation_signature(
        target_date=pd.Timestamp("2026-12-31"),
        base_date=pd.Timestamp("2025-12-31"),
        abgaenge_params={},
        zugaenge_params={},
        cluster_source_signature="cluster-sig-a",
    )
    sig_b = module._build_simulation_signature(
        target_date=pd.Timestamp("2026-12-31"),
        base_date=pd.Timestamp("2025-12-31"),
        abgaenge_params={},
        zugaenge_params={},
        cluster_source_signature="cluster-sig-b",
    )

    assert sig_a != sig_b


def test_page7_recomputes_when_cluster_signature_changes(monkeypatch):
    module = _load_page7_module()
    st.session_state.clear()
    st.session_state.update(
        {
            "compact_sim_signature": "stale-signature",
            "compact_sim_prepared_df": pd.DataFrame({"PersNr": ["old"]}),
            "compact_sim_metadata": {},
            "compact_sim_target_date_cached": pd.Timestamp("2026-03-27"),
        }
    )
    calls = {"simulate": 0}

    class CompactDummy:
        @staticmethod
        def prepare_compact_data(df):
            return df

        @staticmethod
        def render_ist_koepfe_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_mak_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_eur_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_soll_koepfe_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_vs_soll_mak_tab(*args, **kwargs):
            return None

        @staticmethod
        def render_ist_vs_soll_eur_tab(*args, **kwargs):
            return None

    monkeypatch.setattr(module, "_inject_page_styles", lambda: None)
    monkeypatch.setattr(module, "_render_hero", lambda: None)
    monkeypatch.setattr(module, "load_compact_page_module", lambda: CompactDummy())
    monkeypatch.setattr(module, "get_current_stichtag", lambda: "2026-03-27")
    monkeypatch.setattr(
        module,
        "load_and_prepare_data",
        lambda: (
            pd.DataFrame({"PersNr": ["1"], "Organisationseinheit": ["A"]}),
            pd.DataFrame({"Date": pd.to_datetime(["2026-03-27"])}),
            None,
            None,
        ),
    )
    monkeypatch.setattr(module, "_get_simulation_cluster_context", lambda summary: (_active_source("cluster-sig-new"), ClusterMappingBundle(), "cluster-sig-new"))
    monkeypatch.setattr(module, "_get_atz_input", lambda: pd.DataFrame())
    monkeypatch.setattr(
        module,
        "simulate_compact_snapshot",
        lambda **kwargs: (
            calls.__setitem__("simulate", calls["simulate"] + 1)
            or SimpleNamespace(
                future_snapshot_df=pd.DataFrame({"PersNr": ["1"], "Organisationseinheit": ["A"]}),
                metadata={},
            )
        ),
    )
    monkeypatch.setattr(module, "render_section_intro", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_global_filters", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "filter_dataframe_by_view_filters", lambda df, *_args, **_kwargs: df)
    monkeypatch.setattr(module, "get_filter_summary", lambda: "filters")
    monkeypatch.setattr(module, "render_active_filter_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "render_context_box", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "set_metric_page_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "get_global_metric_view", lambda: "Köpfe")
    monkeypatch.setattr(module, "_render_status_box", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_render_summary_cards", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "columns", _dummy_columns)
    monkeypatch.setattr(module.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "date_input", lambda label, value=None, **kwargs: value)
    monkeypatch.setattr(module.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.st, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.st, "tabs", lambda labels, **kwargs: [DummyContext() for _ in labels])

    module.main()

    assert calls["simulate"] == 1
    assert st.session_state["compact_sim_cluster_source_signature"] == "cluster-sig-new"
