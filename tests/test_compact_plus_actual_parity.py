"""
Parity tests: "Ist-Stand auswerten" on Kompakt-plus-Simulation must produce
the same snapshot as the plain Kompakt page.

The test bypasses Streamlit (no st.cache_data, no st.session_state) and
compares dataframes directly after the engine short-circuit.

Key invariant after the fix:
    simulate_compact_snapshot(df, target_date=base_date, base_date=base_date).future_snapshot_df
    is a clean copy of df  (no second MAK / cost / exclusion pass).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from dataloader.compact_simulation_engine import simulate_compact_snapshot


# ---------------------------------------------------------------------------
# Synthetic snapshot builder
# ---------------------------------------------------------------------------

def _make_snapshot(rows: list[dict] | None = None) -> pd.DataFrame:
    """Minimal but complete snapshot that mirrors what load_and_prepare_data() returns."""
    base_date = pd.Timestamp("2025-12-31")
    if rows is not None:
        return pd.DataFrame(rows)

    return pd.DataFrame([
        {
            # identity
            "PersNr": "000001",
            "Personalnummer": "000001",
            "Is_Vacant": False,
            # dates
            "GebDatum": base_date - pd.DateOffset(years=42),
            "Eintritt": pd.Timestamp("2010-06-01"),
            "Austritt": pd.NaT,
            "Betriebszugehörigkeit_Jahre": 15,
            # capacity (already finalised by loader)
            "Sollarbeitszeit": 39.0,
            "Soll_FTE": 1.0,
            "BsGrd": 100.0,
            "FTE_person": 1.0,
            "FTE_assigned": 1.0,
            "MAK_Calculated": 1.0,
            "MAK": 1.0,
            "mak": 1.0,
            "MAK_Reporting": 0.85,   # set by apply_person_mak_allocation in loader
            # costs (set by loader's calculate_cost_vectorized)
            "Total_Cost_Year": 55_000.0,
            "EUR_Reporting": 55_000.0,
            "Soll_Cost_Year": 60_000.0,
            # organisational
            "Organisationseinheit": "Vertrieb",
            "Kürzel OrgEinheit": "1000",
            "Jobfamily": "Berater",
            "OE-Cluster": "Vertrieb",
            "JF-Cluster": "Beratung",
            "Planstelle": "Senior Berater",
            # classification
            "TrfGr": "E9A",
            "St": 3,
            "Vertragsart": "Unbefristet",
            "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Beschäftigte",
            "Ausbildung": "Bachelor",
            "ATZ_Status": "Kein ATZ",
            # demographics
            "Geschlecht": "w",
            "Text Gsch": "w",
            "Alterskohorte": "40–49",
            "Bewertung Tarifgruppe": "E9A",
        },
        {
            "PersNr": "000002",
            "Personalnummer": "000002",
            "Is_Vacant": False,
            "GebDatum": base_date - pd.DateOffset(years=58),
            "Eintritt": pd.Timestamp("1995-03-01"),
            "Austritt": pd.NaT,
            "Betriebszugehörigkeit_Jahre": 30,
            "Sollarbeitszeit": 19.5,
            "Soll_FTE": 0.5,
            "BsGrd": 50.0,
            "FTE_person": 0.5,
            "FTE_assigned": 0.25,
            "MAK_Calculated": 0.5,
            "MAK": 0.5,
            "mak": 0.5,
            "MAK_Reporting": 0.45,
            "Total_Cost_Year": 30_000.0,
            "EUR_Reporting": 30_000.0,
            "Soll_Cost_Year": 32_000.0,
            "Organisationseinheit": "Controlling",
            "Kürzel OrgEinheit": "2000",
            "Jobfamily": "Controlling",
            "OE-Cluster": "Stab",
            "JF-Cluster": "Finance",
            "Planstelle": "Controller",
            "TrfGr": "E11",
            "St": 5,
            "Vertragsart": "Unbefristet",
            "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Beschäftigte",
            "Ausbildung": "Master",
            "ATZ_Status": "Arbeitsphase",
            "Geschlecht": "m",
            "Text Gsch": "m",
            "Alterskohorte": "55–64",
            "Bewertung Tarifgruppe": "E11",
        },
        {
            # vacant position — should not count in headcount KPIs
            "PersNr": np.nan,
            "Personalnummer": np.nan,
            "Is_Vacant": True,
            "GebDatum": pd.NaT,
            "Eintritt": pd.NaT,
            "Austritt": pd.NaT,
            "Betriebszugehörigkeit_Jahre": np.nan,
            "Sollarbeitszeit": 39.0,
            "Soll_FTE": 1.0,
            "BsGrd": 0.0,
            "FTE_person": 0.0,
            "FTE_assigned": 0.0,
            "MAK_Calculated": 0.0,
            "MAK": 0.0,
            "mak": 0.0,
            "MAK_Reporting": 0.0,
            "Total_Cost_Year": 0.0,
            "EUR_Reporting": 0.0,
            "Soll_Cost_Year": 58_000.0,
            "Organisationseinheit": "Vertrieb",
            "Kürzel OrgEinheit": "1000",
            "Jobfamily": "Berater",
            "OE-Cluster": "Vertrieb",
            "JF-Cluster": "Beratung",
            "Planstelle": "Junior Berater",
            "TrfGr": np.nan,
            "St": np.nan,
            "Vertragsart": np.nan,
            "Status kundenindividuell": np.nan,
            "MitarbGruppenbez.": np.nan,
            "Ausbildung": np.nan,
            "ATZ_Status": np.nan,
            "Geschlecht": np.nan,
            "Text Gsch": np.nan,
            "Alterskohorte": np.nan,
            "Bewertung Tarifgruppe": "E9A",
        },
    ])


def _inactive_params():
    zugaenge = {
        "azubi": {"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        "trainee": {"active": False, "new_cases_per_year": 0},
        "new_hires": {"active": False, "count_per_year": 0},
        "random_seed": 42,
    }
    abgaenge = {
        "atz": {"active": False},
        "retirement": {"active": False},
        "quit": {"active": False},
        "random_seed": 42,
        "forecast_end_date": "2026-12-31",
    }
    return abgaenge, zugaenge


# ---------------------------------------------------------------------------
# Core parity assertions
# ---------------------------------------------------------------------------

NUMERIC_COLS = [
    "MAK_Reporting",
    "MAK_Calculated",
    "MAK",
    "mak",
    "FTE_person",
    "FTE_assigned",
    "Soll_FTE",
    "Total_Cost_Year",
    "EUR_Reporting",
    "Soll_Cost_Year",
    "BsGrd",
]

CATEGORICAL_COLS = [
    "Is_Vacant",
    "Organisationseinheit",
    "Kürzel OrgEinheit",
    # Jobfamily, OE-Cluster and JF-Cluster are intentionally excluded here:
    # apply_clusters_to_snapshot_from_source() is called in the zero-horizon short-circuit
    # to honour any active cluster source.  If the source differs from the one used in
    # load_and_prepare_data(), these columns will legitimately change.  The numeric KPI
    # parity guarantee (MAK_Reporting, EUR_Reporting, FTE_assigned, …) is unaffected.
    "TrfGr",
    "Geschlecht",
    "ATZ_Status",
    "Ausbildung",
    "Alterskohorte",
    "Vertragsart",
]


def _assert_snapshot_parity(original: pd.DataFrame, from_sim: pd.DataFrame) -> None:
    assert len(original) == len(from_sim), (
        f"Row count differs: original={len(original)}, sim={len(from_sim)}"
    )

    for col in NUMERIC_COLS:
        if col not in original.columns:
            continue
        if col not in from_sim.columns:
            pytest.fail(f"Column '{col}' missing from sim result")
        orig_vals = pd.to_numeric(original[col], errors="coerce").fillna(0.0).reset_index(drop=True)
        sim_vals  = pd.to_numeric(from_sim[col],  errors="coerce").fillna(0.0).reset_index(drop=True)
        np.testing.assert_allclose(
            sim_vals.values, orig_vals.values, rtol=1e-9, atol=1e-9,
            err_msg=f"Numeric column '{col}' differs between original snapshot and IST-mode sim result",
        )

    for col in CATEGORICAL_COLS:
        if col not in original.columns:
            continue
        orig_s = original[col].reset_index(drop=True).astype(str)
        sim_s  = from_sim[col].reset_index(drop=True).astype(str)
        diff = orig_s != sim_s
        assert not diff.any(), (
            f"Categorical column '{col}' differs at rows: {diff[diff].index.tolist()}\n"
            f"  original: {orig_s[diff].tolist()}\n"
            f"  sim:      {sim_s[diff].tolist()}"
        )

    # PersNr — compare normalised (strip .0 suffix)
    if "PersNr" in original.columns and "PersNr" in from_sim.columns:
        orig_pn = original["PersNr"].astype(str).str.replace(r"\.0$", "", regex=True)
        sim_pn  = from_sim["PersNr"].astype(str).str.replace(r"\.0$", "", regex=True)
        pd.testing.assert_series_equal(
            orig_pn.reset_index(drop=True),
            sim_pn.reset_index(drop=True),
            check_names=False,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestActualModeSnapshotParity:
    """future_snapshot_df must equal the input snapshot when target == base."""

    def test_future_snapshot_equals_input_snapshot(self):
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        _assert_snapshot_parity(snapshot, result.future_snapshot_df)

    def test_metadata_zero_horizon(self):
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        meta = result.metadata
        assert meta["horizon_days"] == 0, f"horizon_days={meta['horizon_days']}"
        assert meta["abgaenge_events"] == 0, f"abgaenge_events={meta['abgaenge_events']}"
        assert meta["zugaenge_events"] == 0, f"zugaenge_events={meta['zugaenge_events']}"
        assert meta["used_simulation"] is False

    def test_no_rows_added_or_removed(self):
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        assert len(result.future_snapshot_df) == len(snapshot)

    def test_mak_reporting_unchanged(self):
        """MAK_Reporting must not be recalculated in actual mode."""
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        orig_mak = snapshot["MAK_Reporting"].fillna(0.0).sum()
        sim_mak  = result.future_snapshot_df["MAK_Reporting"].fillna(0.0).sum()
        assert abs(orig_mak - sim_mak) < 1e-9, (
            f"Total MAK_Reporting changed: original={orig_mak}, sim={sim_mak}"
        )

    def test_eur_reporting_unchanged(self):
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        orig_eur = snapshot["EUR_Reporting"].fillna(0.0).sum()
        sim_eur  = result.future_snapshot_df["EUR_Reporting"].fillna(0.0).sum()
        assert abs(orig_eur - sim_eur) < 1e-9

    def test_headcount_unchanged(self):
        """Non-vacant unique PersNr count must be identical."""
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        active_orig = snapshot[snapshot["Is_Vacant"] != True]
        active_sim  = result.future_snapshot_df[result.future_snapshot_df["Is_Vacant"] != True]

        orig_heads = active_orig["PersNr"].nunique()
        sim_heads  = active_sim["PersNr"].nunique()
        assert orig_heads == sim_heads, f"Headcount: original={orig_heads}, sim={sim_heads}"

    def test_is_vacant_flags_unchanged(self):
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        orig_vacant = snapshot["Is_Vacant"].fillna(False).reset_index(drop=True)
        sim_vacant  = result.future_snapshot_df["Is_Vacant"].fillna(False).reset_index(drop=True)
        pd.testing.assert_series_equal(orig_vacant, sim_vacant, check_names=False)

    def test_fte_assigned_unchanged(self):
        """FTE_assigned must not be recalculated (loader uses no fillna, finalize did)."""
        snapshot = _make_snapshot()
        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        orig = snapshot["FTE_assigned"].fillna(0.0).reset_index(drop=True)
        sim  = result.future_snapshot_df["FTE_assigned"].fillna(0.0).reset_index(drop=True)
        np.testing.assert_allclose(sim.values, orig.values, rtol=1e-9, atol=1e-9)

    def test_actual_mode_does_not_affect_simulation_mode(self):
        """A subsequent simulation call (target > base) must still produce forecast events."""
        snapshot = _make_snapshot()
        base_date  = pd.Timestamp("2025-12-31")
        target_date = pd.Timestamp("2026-12-31")
        abg_params, zug_params = _inactive_params()

        # IST-mode
        result_actual = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )
        assert result_actual.metadata["used_simulation"] is False

        # Simulation mode (same snapshot, future target)
        result_sim = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=target_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )
        assert result_sim.metadata["used_simulation"] is True
        # The simulation result should have gone through _finalize_future_snapshot,
        # so MAK and MAK_Calculated columns exist.
        assert "MAK_Calculated" in result_sim.future_snapshot_df.columns


class TestActualModeWithPartialSnapshot:
    """Edge cases: NaN in numeric columns, mixed vacancy snapshots."""

    def test_snapshot_with_nan_fte(self):
        """NaN FTE_person / FTE_assigned must survive unchanged (no fillna promotion).

        Uses the standard 3-row snapshot from _make_snapshot() but overrides
        the first row's FTE_person and FTE_assigned with NaN.  The full snapshot
        is required because the audit table builder needs at least one employee
        row to avoid a pd.concat-on-empty-list issue in build_azubi_flow_audit.
        """
        snapshot = _make_snapshot()
        # Inject NaN into the first (non-vacant) row
        snapshot.loc[0, "FTE_person"] = np.nan
        snapshot.loc[0, "FTE_assigned"] = np.nan

        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        # NaN must be preserved, not coerced to 0.0 by a fillna pass
        assert pd.isna(result.future_snapshot_df.loc[0, "FTE_person"]), \
            "FTE_person NaN was coerced to a non-NaN value"
        assert pd.isna(result.future_snapshot_df.loc[0, "FTE_assigned"]), \
            "FTE_assigned NaN was coerced to a non-NaN value"

    def test_mostly_vacant_snapshot(self):
        """Snapshot with one employee and several vacant rows must preserve all values."""
        snapshot = _make_snapshot()
        # Keep only one employee (index 1) and the vacant row (index 2)
        snapshot = snapshot.iloc[1:].reset_index(drop=True)

        base_date = pd.Timestamp("2025-12-31")
        abg_params, zug_params = _inactive_params()

        result = simulate_compact_snapshot(
            snapshot_df=snapshot,
            df_atz=pd.DataFrame(),
            target_date=base_date,
            base_date=base_date,
            abgaenge_params=abg_params,
            zugaenge_params=zug_params,
        )

        assert len(result.future_snapshot_df) == len(snapshot)
        # Vacant row still vacant
        assert result.future_snapshot_df.iloc[1]["Is_Vacant"] is True or \
               result.future_snapshot_df.iloc[1]["Is_Vacant"] == True
        # Employee row: MAK_Reporting unchanged
        assert abs(
            result.future_snapshot_df.iloc[0]["MAK_Reporting"]
            - snapshot.iloc[0]["MAK_Reporting"]
        ) < 1e-9
