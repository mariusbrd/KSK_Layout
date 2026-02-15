"""
Tests for JF-Cluster mapping guarantee: NO "Unclustered" values allowed.

Definition of Done:
- Unmapped Jobfamily falls back to "Sonstiges" (never "Unclustered")
- Mapped Jobfamily keeps its cluster
- Übernahmen pipeline produces zero "Unclustered" entries
"""
import pytest
import pandas as pd
import numpy as np

from dataloader.cluster_manager import enrich_jf_clusters, apply_clusters_to_snapshot
from zugaenge.forecast import _simulate_azubis, _simulate_new_azubis, PeriodInfo


# ── 1. Unmapped Jobfamily → "Sonstiges" ──────────────────────────────────

class TestUnmappedFallback:
    def test_unmapped_jobfamily_returns_sonstiges(self):
        """Jobfamily set but mapping returns None → JF-Cluster = 'Sonstiges'."""
        df = pd.DataFrame({"Jobfamily": ["Compliance & Recht", "IT & Digitalisierung", "Controlling & Finanzen"]})
        res = enrich_jf_clusters(df, {}, None)
        assert (res == "Sonstiges").all(), f"Expected all 'Sonstiges', got: {res.tolist()}"

    def test_unmapped_single_jobfamily(self):
        """Single unmapped Jobfamily → 'Sonstiges'."""
        df = pd.DataFrame({"Jobfamily": ["UnknownFamily123"]})
        res = enrich_jf_clusters(df, {}, None)
        assert res.iloc[0] == "Sonstiges"

    def test_no_jobfamily_column(self):
        """DataFrame without Jobfamily column → all 'Sonstiges'."""
        df = pd.DataFrame({"Name": ["Alice"]})
        res = enrich_jf_clusters(df, {}, None)
        assert res.iloc[0] == "Sonstiges"

    def test_empty_jobfamily_string(self):
        """Empty string Jobfamily → 'Sonstiges'."""
        df = pd.DataFrame({"Jobfamily": ["", " "]})
        res = enrich_jf_clusters(df, {}, None)
        assert (res == "Sonstiges").all()

    def test_nan_jobfamily(self):
        """NaN Jobfamily → 'Sonstiges'."""
        df = pd.DataFrame({"Jobfamily": [None, float("nan")]})
        res = enrich_jf_clusters(df, {}, None)
        assert (res == "Sonstiges").all()


# ── 2. Mapped Jobfamily stays mapped ─────────────────────────────────────

class TestMappedPreservation:
    def test_mapped_jobfamily_keeps_cluster(self):
        """Jobfamily with valid mapping → cluster preserved, fallback does NOT override."""
        df = pd.DataFrame({"Jobfamily": ["IT & Digitalisierung"]})
        jf_map = {"it & digitalisierung": "IT-Cluster"}
        res = enrich_jf_clusters(df, jf_map, None)
        assert res.iloc[0] == "IT-Cluster"

    def test_mapped_via_snapshot(self):
        """Jobfamily mapped via snapshot_jf_map → cluster preserved."""
        df = pd.DataFrame({"Jobfamily": ["Compliance & Recht"]})
        snap_map = {"Compliance & Recht": "Legal-Cluster"}
        res = enrich_jf_clusters(df, {}, snap_map)
        assert res.iloc[0] == "Legal-Cluster"

    def test_mixed_mapped_and_unmapped(self):
        """Mix of mapped and unmapped → mapped keep cluster, unmapped get 'Sonstiges'."""
        df = pd.DataFrame({"Jobfamily": ["IT & Digitalisierung", "UnknownFamily", "Sonstige"]})
        jf_map = {"it & digitalisierung": "IT-Cluster"}
        res = enrich_jf_clusters(df, jf_map, None)
        assert res.iloc[0] == "IT-Cluster"
        assert res.iloc[1] == "Sonstiges"
        assert res.iloc[2] == "Sonstiges"

    def test_alias_map_still_works(self):
        """Alias entries (Sonstige, Trainee, Azubi) → 'Sonstiges'."""
        df = pd.DataFrame({"Jobfamily": ["Sonstige", "Trainee", "Azubi", "sonstiges"]})
        res = enrich_jf_clusters(df, {}, None)
        assert (res == "Sonstiges").all()

    def test_case_insensitive_mapping(self):
        """Case should not matter for mapping lookup."""
        df = pd.DataFrame({"Jobfamily": ["IT & DIGITALISIERUNG", "  it & digitalisierung  "]})
        jf_map = {"it & digitalisierung": "IT-Cluster"}
        res = enrich_jf_clusters(df, jf_map, None)
        assert (res == "IT-Cluster").all()


# ── 3. Übernahmen Pipeline: no Unclustered ────────────────────────────────

class TestUebernahmenNoUnclustered:
    def _make_azubi_snapshot(self, persnr="AZ_01", grad_date="2025-08-15"):
        return pd.DataFrame([{
            "PersNr": persnr,
            "Organisationseinheit": "Filiale A",
            "Jobfamily": "Azubi",
            "TrfGr": "TVAöD",
            "active": True,
            "Eintritt": pd.Timestamp("2022-08-01"),
            "GraduationDate": pd.Timestamp(grad_date),
            "mak": 0.0,
            "OE-Cluster": "Filial-Cluster",
            "JF-Cluster": "Sonstiges",
        }]).set_index("PersNr", drop=False)

    def test_takeover_unmapped_jf_gets_sonstiges(self):
        """Takeover with JF not in map → JF-Cluster = 'Sonstiges' (not 'Unclustered')."""
        df = self._make_azubi_snapshot()
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "JobFamily",
                "takeover_matrix": {"NewFamily_NoMapping": 1.0},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "jf_to_cluster_map": {},  # Empty map → fallback must be Sonstiges
            }
        }

        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 1
        ev = in_events[0]
        assert ev["JF-Cluster"] == "Sonstiges", f"Expected 'Sonstiges', got '{ev['JF-Cluster']}'"
        assert ev["JF-Cluster"] != "Unclustered"

    def test_takeover_mapped_jf_preserves_cluster(self):
        """Takeover with JF in map → cluster from map preserved."""
        df = self._make_azubi_snapshot()
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "JobFamily",
                "takeover_matrix": {"Beratung": 1.0},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "jf_to_cluster_map": {"Beratung": "Beratungs-Cluster"},
            }
        }

        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 1
        assert in_events[0]["JF-Cluster"] == "Beratungs-Cluster"

    def test_new_azubi_hire_no_unclustered(self):
        """New Azubi hire with empty mapping → 'Sonstiges' (not 'Unclustered')."""
        df = pd.DataFrame(columns=[
            "PersNr", "Organisationseinheit", "Jobfamily", "TrfGr", "active"
        ])

        params = {
            "azubi": {
                "new_cases_per_year": 12,
                "duration_years": 3,
                "retention_rate": 1.0,
                "strategy": "Random",
                "jf_to_cluster_map": {},  # Empty → must fallback to Sonstiges
            }
        }

        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_new_azubis(df, params, period, rng, events, ["Filiale A"], num_cases=3)

        hire_events = [e for e in events if e["type"] == "Azubi_Hire"]
        assert len(hire_events) == 3
        for ev in hire_events:
            assert ev["JF-Cluster"] == "Sonstiges", f"Expected 'Sonstiges', got '{ev['JF-Cluster']}'"
            assert ev["JF-Cluster"] != "Unclustered"

    def test_multiple_takeovers_mixed_mapping(self):
        """Multiple Azubis: some mapped, some not → none get 'Unclustered'."""
        rows = []
        for i in range(5):
            rows.append({
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
            })
        df = pd.DataFrame(rows).set_index("PersNr", drop=False)

        # Matrix distributes to 3 JFs, only 2 are in the map
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "JobFamily",
                "takeover_matrix": {"Beratung": 2, "IT": 2, "UnmappedJF": 1},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "jf_to_cluster_map": {
                    "Beratung": "Beratungs-Cluster",
                    "IT": "IT-Cluster",
                    # "UnmappedJF" deliberately missing
                },
            }
        }

        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 5

        for ev in in_events:
            assert ev["JF-Cluster"] != "Unclustered", \
                f"PersNr {ev['persnr']}: JF={ev['Jobfamily']} got 'Unclustered'"
            assert ev["JF-Cluster"] in ("Beratungs-Cluster", "IT-Cluster", "Sonstiges")


# ── 4. OrgUnit Mode: deliberate Sonstiges, not fallback ──────────────────

class TestOrgUnitMode:
    """
    When takeover_dimension == 'OrgUnit', there is no JF distribution intent.
    All Conversion-In events must:
    - Have Jobfamily = 'Sonstige'
    - Have JF-Cluster = 'Sonstiges' (deliberately, not as fallback)
    - Carry _jf_orgunit_mode = True flag for audit exclusion
    """

    def _make_azubi_snapshot(self, count=1):
        rows = []
        for i in range(count):
            rows.append({
                "PersNr": f"AZ_OU_{i:02d}",
                "Organisationseinheit": "Filiale A",
                "Jobfamily": "Azubi",
                "TrfGr": "TVAöD",
                "active": True,
                "Eintritt": pd.Timestamp("2022-08-01"),
                "GraduationDate": pd.Timestamp("2025-08-15"),
                "mak": 0.0,
                "OE-Cluster": "Filial-Cluster",
                "JF-Cluster": "Sonstiges",
            })
        return pd.DataFrame(rows).set_index("PersNr", drop=False)

    def test_orgunit_mode_sets_sonstige_jf(self):
        """OrgUnit mode: Conversion-In gets Jobfamily='Sonstige'."""
        df = self._make_azubi_snapshot()
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "OrgUnit",
                "takeover_matrix": {"Filiale B": 1.0},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "jf_to_cluster_map": {"Beratung": "Beratungs-Cluster"},
            }
        }
        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A", "Filiale B"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 1
        ev = in_events[0]
        assert ev["Jobfamily"] == "Sonstige", f"Expected 'Sonstige', got '{ev['Jobfamily']}'"

    def test_orgunit_mode_sets_sonstiges_cluster(self):
        """OrgUnit mode: JF-Cluster = 'Sonstiges' (explicit, not 'Unclustered')."""
        df = self._make_azubi_snapshot()
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "OrgUnit",
                "takeover_matrix": {"Filiale B": 1.0},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "jf_to_cluster_map": {"Beratung": "Beratungs-Cluster"},
            }
        }
        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A", "Filiale B"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 1
        ev = in_events[0]
        assert ev["JF-Cluster"] == "Sonstiges", f"Expected 'Sonstiges', got '{ev['JF-Cluster']}'"
        assert ev["JF-Cluster"] != "Unclustered"

    def test_orgunit_mode_sets_audit_flag(self):
        """OrgUnit mode: Conversion-In carries _jf_orgunit_mode=True for audit."""
        df = self._make_azubi_snapshot()
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "OrgUnit",
                "takeover_matrix": {"Filiale B": 1.0},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
            }
        }
        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A", "Filiale B"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 1
        assert in_events[0].get("_jf_orgunit_mode") is True

    def test_orgunit_mode_distributes_org_units(self):
        """OrgUnit mode: OrgUnit assignment follows the matrix distribution."""
        df = self._make_azubi_snapshot(count=4)
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "OrgUnit",
                "takeover_matrix": {"Filiale B": 3, "Filiale C": 1},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
            }
        }
        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A", "Filiale B", "Filiale C"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 4

        org_units = [e["org_unit"] for e in in_events]
        assert org_units.count("Filiale B") == 3
        assert org_units.count("Filiale C") == 1

        # All must have Sonstige JF and audit flag
        for ev in in_events:
            assert ev["Jobfamily"] == "Sonstige"
            assert ev["JF-Cluster"] == "Sonstiges"
            assert ev.get("_jf_orgunit_mode") is True

    def test_jobfamily_mode_no_orgunit_flag(self):
        """Regression: JobFamily mode does NOT set _jf_orgunit_mode flag."""
        df = self._make_azubi_snapshot()
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "JobFamily",
                "takeover_matrix": {"Beratung": 1.0},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "jf_to_cluster_map": {"Beratung": "Beratungs-Cluster"},
            }
        }
        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 1
        ev = in_events[0]
        # JobFamily mode: no orgunit flag present
        assert "_jf_orgunit_mode" not in ev
        # JF from matrix, not Sonstige
        assert ev["Jobfamily"] == "Beratung"
        assert ev["JF-Cluster"] == "Beratungs-Cluster"

    def test_jobfamily_mode_distributes_jfs(self):
        """Regression: JobFamily mode distributes according to matrix (not all Sonstige)."""
        df = self._make_azubi_snapshot(count=3)
        params = {
            "azubi": {
                "use_takeover_matrix": True,
                "takeover_dimension": "JobFamily",
                "takeover_matrix": {"Beratung": 2, "IT": 1},
                "retention_rate": 1.0,
                "duration_years": 3.0,
                "strategy": "Random",
                "jf_to_cluster_map": {
                    "Beratung": "Beratungs-Cluster",
                    "IT": "IT-Cluster",
                },
            }
        }
        period = PeriodInfo(pd.Timestamp("2025-08-01"), pd.Timestamp("2025-08-31"), "Aug 2025")
        rng = np.random.default_rng(42)
        events = []

        _simulate_azubis(df, params, period, rng, events, ["Filiale A"])

        in_events = [e for e in events if e["type"] == "Azubi_Conversion_In"]
        assert len(in_events) == 3

        jfs = [e["Jobfamily"] for e in in_events]
        clusters = [e["JF-Cluster"] for e in in_events]
        assert jfs.count("Beratung") == 2
        assert jfs.count("IT") == 1
        assert clusters.count("Beratungs-Cluster") == 2
        assert clusters.count("IT-Cluster") == 1

        # No orgunit flags
        for ev in in_events:
            assert "_jf_orgunit_mode" not in ev


# ── 5. apply_clusters_to_snapshot safety-net ──────────────────────────────

class TestApplyClustersToSnapshot:
    def test_no_unclustered_after_apply(self):
        """After apply_clusters_to_snapshot, no JF-Cluster = 'Unclustered'."""
        df = pd.DataFrame({
            "PersNr": ["P1", "P2", "P3"],
            "Organisationseinheit": ["OE1", "OE2", "OE3"],
            "Planstelle": ["Berater", "IT-Admin", "Unknown"],
            "Jobfamily": ["Beratung", "IT", "SomeRareFamily"],
        })
        # No uploaded file → uses file-based mapping (may be empty in test env)
        # The safety-net in apply_clusters_to_snapshot should catch everything
        result = apply_clusters_to_snapshot(df, uploaded_file=None)
        assert "JF-Cluster" in result.columns
        uncl = (result["JF-Cluster"] == "Unclustered").sum()
        assert uncl == 0, f"Found {uncl} 'Unclustered' entries after apply_clusters_to_snapshot"
