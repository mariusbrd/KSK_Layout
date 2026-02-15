"""
Tests for Hybrid Page alignment with Zugaenge Page on cluster mapping.

Verifies that the shared enrichment pipeline works correctly when
invoked with Hybrid-specific parameters (same engine, same logic).

Definition of Done:
- build_jf_to_cluster_map excludes Unclustered/empty
- Hybrid with JobFamily matrix: correct clusters, no Unclustered
- Hybrid with OrgUnit matrix: deliberate Sonstiges, _jf_orgunit_mode flag
- Trainee events: JF-Cluster Sonstiges
- End-to-end: no Unclustered after enrichment
"""
import pytest
import pandas as pd
import numpy as np

from zugaenge.forecast import run_forecast_zugaenge, _simulate_azubis, PeriodInfo
from zugaenge.enrichment import (
    build_jf_to_cluster_map,
    enrich_zugaenge_events,
    build_known_jf_keys,
)
from utils.ui_helpers import is_orgunit_mode_active, ORGUNIT_MODE_HINT


# ── 1. build_jf_to_cluster_map ──────────────────────────────────────────

class TestBuildJfToClusterMap:
    def test_basic_mapping(self):
        """Valid entries produce correct mapping."""
        df = pd.DataFrame({
            "Jobfamily": ["IT", "Beratung", "IT"],
            "JF-Cluster": ["IT-Cluster", "Beratungs-Cluster", "IT-Cluster"],
        })
        result = build_jf_to_cluster_map(df)
        assert result["IT"] == "IT-Cluster"
        assert result["Beratung"] == "Beratungs-Cluster"

    def test_excludes_unclustered(self):
        """Unclustered values are excluded from the map."""
        df = pd.DataFrame({
            "Jobfamily": ["IT", "Unknown"],
            "JF-Cluster": ["IT-Cluster", "Unclustered"],
        })
        result = build_jf_to_cluster_map(df)
        assert "IT" in result
        assert "Unknown" not in result

    def test_excludes_empty_and_nan(self):
        """Empty strings and nan values are excluded."""
        df = pd.DataFrame({
            "Jobfamily": ["IT", "Empty", "NanVal"],
            "JF-Cluster": ["IT-Cluster", "", "nan"],
        })
        result = build_jf_to_cluster_map(df)
        assert "IT" in result
        assert "Empty" not in result
        assert "NanVal" not in result

    def test_missing_columns_returns_empty(self):
        """Missing Jobfamily or JF-Cluster columns return empty dict."""
        df = pd.DataFrame({"Name": ["Alice"]})
        assert build_jf_to_cluster_map(df) == {}


# ── 2. build_known_jf_keys ──────────────────────────────────────────────

class TestBuildKnownJfKeys:
    def test_includes_aliases(self):
        """Always includes sonstige, sonstiges, trainee, azubi."""
        result = build_known_jf_keys({}, pd.DataFrame())
        assert "sonstige" in result
        assert "trainee" in result
        assert "azubi" in result

    def test_includes_param_keys(self):
        """Keys from jf_to_cluster_map in params are included."""
        params = {"azubi": {"jf_to_cluster_map": {"Beratung": "B-Cluster"}}}
        result = build_known_jf_keys(params, pd.DataFrame())
        assert "beratung" in result

    def test_includes_snapshot_keys(self):
        """Non-Sonstiges JF from snapshot are included."""
        df = pd.DataFrame({
            "Jobfamily": ["IT", "Sonstige"],
            "JF-Cluster": ["IT-Cluster", "Sonstiges"],
        })
        result = build_known_jf_keys({}, df)
        assert "it" in result
        # Sonstige with Sonstiges cluster is excluded (it's an alias, not a real mapping)
        # but "sonstige" is in alias_keys anyway


# ── 3. enrich_zugaenge_events ────────────────────────────────────────────

class TestEnrichZugaengeEvents:
    def test_no_unclustered_after_enrichment(self):
        """End-to-end: enriched events must NEVER contain 'Unclustered'."""
        events = pd.DataFrame([{
            "persnr": "NH_001",
            "type": "New_Hire",
            "date": pd.Timestamp("2025-06-15"),
            "Organisationseinheit": "Unknown_OE",
            "Jobfamily": "Unknown_JF",
            "mak": 1.0,
            "count": 1,
            "source": "NewHire",
        }])
        snapshot = pd.DataFrame([{
            "PersNr": "EMP_01",
            "Organisationseinheit": "Filiale A",
            "Jobfamily": "IT",
            "JF-Cluster": "IT-Cluster",
            "OE-Cluster": "Filial-Cluster",
        }])
        params = {"azubi": {"jf_to_cluster_map": {}}}

        enriched = enrich_zugaenge_events(events, snapshot, params)
        assert (enriched["JF-Cluster"] != "Unclustered").all()
        assert (enriched["OE-Cluster"] != "Unclustered").all()

    def test_preserves_existing_valid_clusters(self):
        """Events with valid JF-Cluster keep their value."""
        events = pd.DataFrame([{
            "persnr": "EMP_01",
            "type": "New_Hire",
            "date": pd.Timestamp("2025-06-15"),
            "Organisationseinheit": "Filiale A",
            "Jobfamily": "IT",
            "JF-Cluster": "IT-Cluster",
            "OE-Cluster": "Filial-Cluster",
            "mak": 1.0,
            "count": 1,
            "source": "NewHire",
        }])
        snapshot = pd.DataFrame([{
            "PersNr": "EMP_01",
            "Organisationseinheit": "Filiale A",
            "Jobfamily": "IT",
            "JF-Cluster": "IT-Cluster",
            "OE-Cluster": "Filial-Cluster",
        }])

        enriched = enrich_zugaenge_events(events, snapshot, {})
        assert enriched.iloc[0]["JF-Cluster"] == "IT-Cluster"

    def test_adds_diagnose_quelle(self):
        """Enrichment adds Diagnose-Quelle column."""
        events = pd.DataFrame([{
            "persnr": "NH_01",
            "type": "New_Hire",
            "date": pd.Timestamp("2025-06-15"),
            "mak": 1.0,
            "count": 1,
            "source": "NewHire",
        }])
        snapshot = pd.DataFrame([{
            "PersNr": "EMP_01",
            "Organisationseinheit": "X",
            "Jobfamily": "IT",
            "JF-Cluster": "IT-Cluster",
            "OE-Cluster": "X-Cluster",
        }])

        enriched = enrich_zugaenge_events(events, snapshot, {})
        assert "Diagnose-Quelle" in enriched.columns
        assert enriched.iloc[0]["Diagnose-Quelle"] == "Neueinstellung"

    def test_fallback_flag_set_for_unmapped(self):
        """_jf_fallback is True for unmapped Jobfamilies."""
        events = pd.DataFrame([{
            "persnr": "NH_01",
            "type": "New_Hire",
            "date": pd.Timestamp("2025-06-15"),
            "Jobfamily": "TotallyUnknownJF",
            "mak": 1.0,
            "count": 1,
            "source": "NewHire",
        }])
        snapshot = pd.DataFrame([{
            "PersNr": "EMP_01",
            "Organisationseinheit": "X",
            "Jobfamily": "IT",
            "JF-Cluster": "IT-Cluster",
            "OE-Cluster": "X-Cluster",
        }])
        params = {"azubi": {"jf_to_cluster_map": {}}}

        enriched = enrich_zugaenge_events(events, snapshot, params)
        assert enriched.iloc[0]["_jf_fallback"] == True
        assert enriched.iloc[0]["JF-Cluster"] == "Sonstiges"

    def test_orgunit_mode_excluded_from_fallback(self):
        """_jf_orgunit_mode entries are NOT counted as fallback."""
        events = pd.DataFrame([{
            "persnr": "AZ_01",
            "type": "Azubi_Conversion_In",
            "date": pd.Timestamp("2025-08-15"),
            "Jobfamily": "Sonstige",
            "JF-Cluster": "Sonstiges",
            "mak": 1.0,
            "count": 1,
            "source": "Regular",
            "_jf_orgunit_mode": True,
        }])
        snapshot = pd.DataFrame([{
            "PersNr": "AZ_01",
            "Organisationseinheit": "Filiale A",
            "Jobfamily": "Azubi",
            "JF-Cluster": "Sonstiges",
            "OE-Cluster": "Filial-Cluster",
        }])

        enriched = enrich_zugaenge_events(events, snapshot, {})
        assert enriched.iloc[0]["_jf_fallback"] == False

    def test_empty_events_returns_empty(self):
        """Empty events_df passes through unchanged."""
        events = pd.DataFrame()
        snapshot = pd.DataFrame({"PersNr": ["X"], "Organisationseinheit": ["Y"]})
        result = enrich_zugaenge_events(events, snapshot, {})
        assert result.empty


# ── 4. Hybrid Forecast Integration Tests ─────────────────────────────────

class TestHybridWithJobFamilyMatrix:
    """Verify Hybrid forecast with JobFamily matrix produces correct clusters."""

    def test_jf_matrix_produces_correct_clusters(self):
        snapshot = pd.DataFrame([{
            "PersNr": f"AZ_{i:02d}",
            "Organisationseinheit": "Filiale A",
            "Jobfamily": "Azubi",
            "TrfGr": "TVAöD",
            "active": True,
            "Eintritt": pd.Timestamp("2022-08-01"),
            "GraduationDate": pd.Timestamp("2025-08-15"),
            "mak": 0.0,
            "OE-Cluster": "Filial-Cluster",
            "JF-Cluster": "Sonstiges",
        } for i in range(3)])

        params = {
            "azubi": {
                "active": True,
                "new_cases_per_year": 0,
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "use_takeover_matrix": True,
                "takeover_dimension": "JobFamily",
                "takeover_matrix": {"Beratung": 2, "IT": 1},
                "jf_to_cluster_map": {
                    "Beratung": "Beratungs-Cluster",
                    "IT": "IT-Cluster",
                },
                "entry_tariff_group": "E5",
                "entry_step": 1,
            },
            "trainee": {"active": False, "new_cases_per_year": 0},
            "new_hires": {"active": False, "count_per_year": 0},
            "random_seed": 42,
        }

        result = run_forecast_zugaenge(
            df_snapshot=snapshot,
            start_date=pd.Timestamp("2025-07-01"),
            end_date=pd.Timestamp("2025-09-30"),
            params=params,
        )

        events = result["events"]
        conv_in = events[events["type"] == "Azubi_Conversion_In"]
        assert len(conv_in) == 3

        for _, ev in conv_in.iterrows():
            assert ev["JF-Cluster"] != "Unclustered"
            assert ev["JF-Cluster"] in ("Beratungs-Cluster", "IT-Cluster")


class TestHybridWithOrgUnitMatrix:
    """Verify Hybrid forecast with OrgUnit matrix produces 'Sonstiges' deliberately."""

    def test_orgunit_matrix_produces_sonstiges(self):
        snapshot = pd.DataFrame([{
            "PersNr": "AZ_OU_01",
            "Organisationseinheit": "Filiale A",
            "Jobfamily": "Azubi",
            "TrfGr": "TVAöD",
            "active": True,
            "Eintritt": pd.Timestamp("2022-08-01"),
            "GraduationDate": pd.Timestamp("2025-08-15"),
            "mak": 0.0,
            "OE-Cluster": "Filial-Cluster",
            "JF-Cluster": "Sonstiges",
        }])

        params = {
            "azubi": {
                "active": True,
                "new_cases_per_year": 0,
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "use_takeover_matrix": True,
                "takeover_dimension": "OrgUnit",
                "takeover_matrix": {"Filiale B": 1.0},
                "jf_to_cluster_map": {"Beratung": "Beratungs-Cluster"},
                "entry_tariff_group": "E5",
                "entry_step": 1,
            },
            "trainee": {"active": False, "new_cases_per_year": 0},
            "new_hires": {"active": False, "count_per_year": 0},
            "random_seed": 42,
        }

        result = run_forecast_zugaenge(
            df_snapshot=snapshot,
            start_date=pd.Timestamp("2025-07-01"),
            end_date=pd.Timestamp("2025-09-30"),
            params=params,
        )

        events = result["events"]
        conv_in = events[events["type"] == "Azubi_Conversion_In"]
        assert len(conv_in) == 1
        ev = conv_in.iloc[0]
        assert ev["JF-Cluster"] == "Sonstiges"
        assert ev["Jobfamily"] == "Sonstige"
        assert ev.get("_jf_orgunit_mode") == True


class TestTraineeGetsSonstiges:
    """Verify Trainee events always get JF-Cluster 'Sonstiges'."""

    def test_trainee_events_sonstiges_via_enrichment(self):
        snapshot = pd.DataFrame([{
            "PersNr": "EMP_01",
            "Organisationseinheit": "Zentrale",
            "Jobfamily": "Beratung",
            "TrfGr": "E9A",
            "active": True,
            "Eintritt": pd.Timestamp("2020-01-01"),
            "mak": 1.0,
            "OE-Cluster": "Zentral-Cluster",
            "JF-Cluster": "Beratungs-Cluster",
        }])

        params = {
            "azubi": {"active": False, "new_cases_per_year": 0},
            "trainee": {
                "active": True,
                "new_cases_per_year": 12,
                "duration_years": 1.5,
                "salary_group": "E13",
                "strategy": "Random",
            },
            "new_hires": {"active": False, "count_per_year": 0},
            "random_seed": 42,
        }

        result = run_forecast_zugaenge(
            df_snapshot=snapshot,
            start_date=pd.Timestamp("2025-01-01"),
            end_date=pd.Timestamp("2025-12-31"),
            params=params,
        )

        events = result["events"]
        enriched = enrich_zugaenge_events(events, snapshot, params)
        trainee_events = enriched[enriched["type"] == "Trainee_Hire"]
        assert not trainee_events.empty
        for _, ev in trainee_events.iterrows():
            assert ev["JF-Cluster"] == "Sonstiges"


# ── 5. OrgUnit-Modus Hint Condition ───────────────────────────────────────

class TestOrgunitModeHint:
    def test_orgunit_active(self):
        """Hint active when matrix=True and dimension=OrgUnit."""
        assert is_orgunit_mode_active(True, "OrgUnit") is True

    def test_jobfamily_inactive(self):
        """Hint not active when dimension=JobFamily."""
        assert is_orgunit_mode_active(True, "JobFamily") is False

    def test_matrix_off_inactive(self):
        """Hint not active when matrix is disabled."""
        assert is_orgunit_mode_active(False, "OrgUnit") is False

    def test_both_off_inactive(self):
        """Hint not active when both off."""
        assert is_orgunit_mode_active(False, "JobFamily") is False

    def test_hint_text_not_empty(self):
        """Shared hint constant is non-empty."""
        assert len(ORGUNIT_MODE_HINT) > 0
