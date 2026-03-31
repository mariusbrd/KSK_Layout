"""
Tests for the centralised sidebar filter functions.

Verifies:
- apply_robust_filter: type normalisation, missing columns, empty selection
- apply_event_filters: attrition vs accession mode, demographic PersNr matching
- get_active_view_filters: pure-function extraction
- Recompute protection: filter keys never appear in forecast param dicts
"""
import pytest
import pandas as pd

from components.sidebar import (
    _apply_event_filters_uncached,
    _apply_filters_uncached,
    apply_robust_filter,
    apply_event_filters,
    apply_event_filters_with_state,
    filter_dataframe_by_view_filters,
    get_active_view_filters,
)


# ── 1. apply_robust_filter ──────────────────────────────────────────────

class TestApplyRobustFilter:
    def _sample_df(self):
        return pd.DataFrame({
            "OE-Cluster": ["Filial-Cluster", "Zentral-Cluster", "Filial-Cluster"],
            "JF-Cluster": ["IT-Cluster", "Beratungs-Cluster", "IT-Cluster"],
            "Organisationseinheit": ["Filiale A", "Zentrale", "Filiale B"],
            "Jobfamily": ["IT", "Beratung", "IT"],
            "value": [10, 20, 30],
        })

    def test_oe_cluster_filter(self):
        df = self._sample_df()
        result = apply_robust_filter(df, "OE-Cluster", ["Filial-Cluster"])
        assert len(result) == 2
        assert set(result["OE-Cluster"]) == {"Filial-Cluster"}

    def test_jf_cluster_filter(self):
        df = self._sample_df()
        result = apply_robust_filter(df, "JF-Cluster", ["IT-Cluster"])
        assert len(result) == 2

    def test_combined_filters(self):
        df = self._sample_df()
        step1 = apply_robust_filter(df, "OE-Cluster", ["Filial-Cluster"])
        step2 = apply_robust_filter(step1, "JF-Cluster", ["IT-Cluster"])
        assert len(step2) == 2

    def test_no_filters(self):
        df = self._sample_df()
        result = apply_robust_filter(df, "OE-Cluster", [])
        assert len(result) == len(df)
        pd.testing.assert_frame_equal(result, df)

    def test_missing_column(self):
        df = self._sample_df()
        result = apply_robust_filter(df, "NonExistentCol", ["anything"])
        assert len(result) == len(df)
        pd.testing.assert_frame_equal(result, df)

    def test_float_normalization(self):
        """612 and 612.0 should match each other."""
        df = pd.DataFrame({
            "Kürzel": ["612.0", "613", "614.0"],
            "val": [1, 2, 3],
        })
        result = apply_robust_filter(df, "Kürzel", ["612", "614"])
        assert len(result) == 2
        assert set(result["val"]) == {1, 3}

    def test_float_normalization_reverse(self):
        """Values in DF without .0, filter values with .0."""
        df = pd.DataFrame({
            "Kürzel": ["612", "613"],
            "val": [1, 2],
        })
        result = apply_robust_filter(df, "Kürzel", ["612.0"])
        assert len(result) == 1
        assert result.iloc[0]["val"] == 1

    def test_whitespace_handling(self):
        """Leading/trailing whitespace is stripped."""
        df = pd.DataFrame({
            "Name": ["  Alice ", "Bob"],
            "val": [1, 2],
        })
        result = apply_robust_filter(df, "Name", ["Alice"])
        assert len(result) == 1


# ── 2. apply_event_filters ──────────────────────────────────────────────

class TestApplyEventFilters:
    """Test apply_event_filters using a mocked st.session_state."""

    @pytest.fixture(autouse=True)
    def _patch_session_state(self, monkeypatch):
        """Provide a clean session_state dict for each test."""
        self._state = {
            "selected_org_units": [],
            "selected_jobfamilies": [],
            "selected_oe_clusters": [],
            "selected_jf_clusters": [],
            "selected_genders": ["m", "w"],
            "selected_employment": ["Vollzeit", "Teilzeit", "Inaktiv"],
            "selected_atz_status": ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
            "selected_cohorts": [],
            "selected_education": [],
        }
        # Mock st.session_state
        import streamlit as st
        monkeypatch.setattr(st, "session_state", self._state)

    def _snapshot(self):
        return pd.DataFrame({
            "PersNr": ["E01", "E02", "E03"],
            "Organisationseinheit": ["Filiale A", "Zentrale", "Filiale B"],
            "Jobfamily": ["IT", "Beratung", "IT"],
            "OE-Cluster": ["Filial-Cluster", "Zentral-Cluster", "Filial-Cluster"],
            "JF-Cluster": ["IT-Cluster", "Beratungs-Cluster", "IT-Cluster"],
            "Geschlecht": ["m", "w", "m"],
            "Arbeitszeit": ["Vollzeit", "Teilzeit", "Vollzeit"],
            "ATZ_Status": ["Kein ATZ", "Kein ATZ", "Kein ATZ"],
        })

    def _events(self, include_new_hire=False):
        rows = [
            {"persnr": "E01", "type": "ATZ_Exit", "mak_change": -1, "headcount_change": -1,
             "Organisationseinheit": "Filiale A", "Jobfamily": "IT",
             "OE-Cluster": "Filial-Cluster", "JF-Cluster": "IT-Cluster"},
            {"persnr": "E02", "type": "Retirement", "mak_change": -1, "headcount_change": -1,
             "Organisationseinheit": "Zentrale", "Jobfamily": "Beratung",
             "OE-Cluster": "Zentral-Cluster", "JF-Cluster": "Beratungs-Cluster"},
        ]
        if include_new_hire:
            rows.append({
                "persnr": "NH_99", "type": "New_Hire", "mak_change": 1, "headcount_change": 1,
                "Organisationseinheit": "Zentrale", "Jobfamily": "IT",
                "OE-Cluster": "Zentral-Cluster", "JF-Cluster": "IT-Cluster",
            })
        return pd.DataFrame(rows)

    def test_no_filters_returns_all(self):
        events = self._events()
        result, n_before, n_after = apply_event_filters(events, self._snapshot(), mode="attrition")
        assert n_before == 2
        assert n_after == 2

    def test_orgunit_filter_attrition(self):
        self._state["selected_org_units"] = ["Filiale A"]
        events = self._events()
        result, _, n_after = apply_event_filters(events, self._snapshot(), mode="attrition")
        assert n_after == 1
        assert result.iloc[0]["persnr"] == "E01"

    def test_jf_cluster_filter(self):
        self._state["selected_jf_clusters"] = ["IT-Cluster"]
        events = self._events()
        result, _, n_after = apply_event_filters(events, self._snapshot(), mode="attrition")
        assert n_after == 1

    def test_accession_preserves_new_hires(self):
        """In accession mode, new hires (not in snapshot) survive demographic filters."""
        self._state["selected_org_units"] = ["Zentrale"]
        events = self._events(include_new_hire=True)
        result, _, n_after = apply_event_filters(events, self._snapshot(), mode="accession")
        # E02 (Zentrale) + NH_99 (new hire, Zentrale)
        assert n_after == 2
        assert "NH_99" in result["persnr"].values

    def test_attrition_excludes_unknown(self):
        """In attrition mode, events for unknown PersNr are excluded."""
        events = self._events(include_new_hire=True)
        result, _, n_after = apply_event_filters(events, self._snapshot(), mode="attrition")
        # NH_99 not in snapshot → excluded
        assert "NH_99" not in result["persnr"].values
        assert n_after == 2

    def test_empty_events(self):
        result, n_before, n_after = apply_event_filters(pd.DataFrame(), self._snapshot(), mode="attrition")
        assert result.empty
        assert n_before == 0
        assert n_after == 0

    def test_cached_event_filters_match_uncached_reference(self):
        self._state["selected_org_units"] = ["Filiale A"]
        self._state["selected_jf_clusters"] = ["IT-Cluster"]
        filters = get_active_view_filters(self._state)
        events = self._events(include_new_hire=True)
        expected = _apply_event_filters_uncached(events, self._snapshot(), active_filters=filters, mode="accession")
        actual = apply_event_filters_with_state(events, self._snapshot(), active_filters=filters, mode="accession")
        pd.testing.assert_frame_equal(actual[0], expected[0], check_dtype=True, check_like=False)
        assert actual[1:] == expected[1:]


# ── 3. get_active_view_filters ───────────────────────────────────────────

class TestGetActiveViewFilters:
    def test_pure_function_extracts_filters(self):
        state = {
            "selected_org_units": ["Filiale A"],
            "selected_jobfamilies": [],
            "selected_oe_clusters": [],
            "selected_jf_clusters": ["IT-Cluster"],
            "selected_genders": ["m", "w"],
            "selected_employment": ["Vollzeit", "Teilzeit", "Inaktiv"],
            "selected_atz_status": ["Kein ATZ", "Arbeitsphase", "Freistellungsphase"],
            "selected_cohorts": [],
            "selected_education": [],
        }
        result = get_active_view_filters(state)
        assert result["selected_org_units"] == ["Filiale A"]
        assert result["selected_jf_clusters"] == ["IT-Cluster"]
        assert result["selected_jobfamilies"] == []

    def test_missing_keys_use_defaults(self):
        result = get_active_view_filters({})
        assert result["selected_org_units"] == []
        assert result["selected_genders"] == []

    def test_cached_dataframe_filters_match_uncached_reference(self):
        state = {
            "selected_org_units": ["Filiale A"],
            "selected_jobfamilies": ["IT"],
            "selected_oe_clusters": ["Filial-Cluster"],
            "selected_jf_clusters": ["IT-Cluster"],
            "selected_genders": ["m"],
            "selected_employment": ["Vollzeit"],
            "selected_atz_status": ["Kein ATZ"],
            "selected_cohorts": [],
            "selected_education": [],
        }
        filters = get_active_view_filters(state)
        df = pd.DataFrame({
            "PersNr": ["E01", "E02"],
            "Organisationseinheit": ["Filiale A", "Zentrale"],
            "Jobfamily": ["IT", "Beratung"],
            "OE-Cluster": ["Filial-Cluster", "Zentral-Cluster"],
            "JF-Cluster": ["IT-Cluster", "Beratungs-Cluster"],
            "Geschlecht": ["m", "w"],
            "Arbeitszeit": ["Vollzeit", "Teilzeit"],
            "ATZ_Status": ["Kein ATZ", "Kein ATZ"],
            "Alterskohorte": ["30-40 Jahre", "40-50 Jahre"],
            "Ausbildung": ["Bachelor", "Master"],
        })
        expected = _apply_filters_uncached(df, filters)
        actual = filter_dataframe_by_view_filters(df, filters)
        pd.testing.assert_frame_equal(actual, expected, check_dtype=True, check_like=False)


# ── 4. Recompute Protection ─────────────────────────────────────────────

class TestRecomputeProtection:
    """Verify that filter session-state keys are NOT part of forecast params."""

    FILTER_KEYS = {
        "selected_org_units", "selected_jobfamilies",
        "selected_oe_clusters", "selected_jf_clusters",
        "selected_genders", "selected_employment",
        "selected_atz_status", "selected_cohorts",
        "selected_education",
    }

    def test_abgaenge_params_exclude_filters(self):
        from abgaenge.params import default_params
        params = default_params()
        flat_keys = _flatten_keys(params)
        assert self.FILTER_KEYS.isdisjoint(flat_keys)

    def test_zugaenge_params_exclude_filters(self):
        from zugaenge.params import default_params
        params = default_params()
        flat_keys = _flatten_keys(params)
        assert self.FILTER_KEYS.isdisjoint(flat_keys)


def _flatten_keys(d: dict, prefix: str = "") -> set:
    """Recursively collect all keys from a nested dict."""
    keys = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        keys.add(k)
        if isinstance(v, dict):
            keys |= _flatten_keys(v, full)
    return keys
