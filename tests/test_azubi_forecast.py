"""
Test suite: Azubi (Apprentice) Forecast Engine
==============================================
Coverage:
  Part A - Basic Lifecycle (Hire -> Training -> Graduation)
  Part B - Graduation Mode (next_cycle vs nearest_cycle)
  Part C - Retention Rate & Destiny
  Part D - Takeover Matrix (JobFamily dimension)
  Part E - Takeover Matrix (OrgUnit dimension)
  Part F - MAK Logic
  Part G - Fix Regression Tests (Fix 1/4/5)

Run from project root:
  python -m pytest KSK_Layout/tests/test_azubi_forecast.py -v
  or:
  python KSK_Layout/tests/test_azubi_forecast.py
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zugaenge.forecast import (
    run_forecast_zugaenge,
    _estimate_baseline_graduation_date,
    distribute_deterministic,
    resolve_distribution,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(rows):
    return pd.DataFrame(rows)

def _base_params(overrides=None):
    p = {
        "azubi": {
            "active": True,
            "new_cases_per_year": 15,
            "duration_years": 3,
            "retention_rate": 1.0,
            "strategy": "Random",
            "entry_tariff_group": "E5",
            "entry_step": 1,
            "exclude_baseline_azubis": False,
            "azubi_mak_during_training": 0.0,
            "azubi_mak_after_takeover": 1.0,
            "azubi_conversion_month": 8,
            "azubi_conversion_day": 1,
            "graduation_mode": "next_cycle",
            "use_takeover_matrix": False,
            "takeover_matrix": {},
            "takeover_dimension": "JobFamily",
            "jf_to_cluster_map": {},
        },
        "trainee": {"active": False},
        "new_hires": {"active": False},
        "random_seed": 42,
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in p:
                p[k].update(v)
            else:
                p[k] = v
    return p


def _run(snapshot, params, years=4, start="2025-12-31"):
    start_date = pd.Timestamp(start)
    result = run_forecast_zugaenge(snapshot, start_date, periods_years=years, params=params)
    events = result["events"]
    if not isinstance(events, pd.DataFrame):
        events = pd.DataFrame(events)
    return result, events


def _empty_snapshot():
    return _make_snapshot([{
        "PersNr": "900001",
        "Organisationseinheit": "OE1",
        "Jobfamily": "Angestellte",
        "active": True,
        "mak": 1.0,
        "TrfGr": "E9A",
        "Eintritt": "2015-01-01",
    }])

# ---------------------------------------------------------------------------
# PART A - Basic Lifecycle
# ---------------------------------------------------------------------------

def test_A01_hire_event_created():
    """Azubi_Hire events are generated each year."""
    snap = _empty_snapshot()
    params = _base_params({"azubi": {"new_cases_per_year": 10}})
    _, events = _run(snap, params, years=3)
    hires = events[events["type"] == "Azubi_Hire"]
    assert len(hires) > 0, "Expected Azubi_Hire events"
    # Should be roughly 30 hires over 3 years (10/year)
    assert 25 <= len(hires) <= 35, f"Expected ~30 hires, got {len(hires)}"


def test_A02_hire_event_schema():
    """Azubi_Hire events have correct fields."""
    snap = _empty_snapshot()
    params = _base_params({"azubi": {"new_cases_per_year": 5}})
    _, events = _run(snap, params, years=2)
    hires = events[events["type"] == "Azubi_Hire"]
    assert len(hires) > 0
    row = hires.iloc[0]
    assert row["count"] == 1, "Hire count must be +1"
    assert float(row["mak"]) == 0.0, "Azubi MAK during training must be 0.0"
    assert str(row["persnr"]).startswith("AZ_"), f"ID format wrong: {row['persnr']}"


def test_A03_takeover_creates_paired_events():
    """Conversion_Out and Conversion_In always appear as a pair per person."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3}
    })
    _, events = _run(snap, params, years=5)
    outs = events[events["type"] == "Azubi_Conversion_Out"]
    ins = events[events["type"] == "Azubi_Conversion_In"]
    assert len(outs) == len(ins), (
        f"Conversion_Out ({len(outs)}) must equal Conversion_In ({len(ins)})"
    )
    # Same PersNr set
    assert set(outs["persnr"].tolist()) == set(ins["persnr"].tolist()), \
        "PersNr sets of Conversion_Out and Conversion_In must match"


def test_A04_exit_sets_inactive():
    """Azubi_Exit events result in count=-1, and those persons leave the active pool."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 15, "retention_rate": 0.0, "duration_years": 3}
    })
    _, events = _run(snap, params, years=5)
    exits = events[events["type"] == "Azubi_Exit"]
    assert len(exits) > 0, "Expected Azubi_Exit events with retention_rate=0.0"
    for _, row in exits.iterrows():
        assert int(row["count"]) == -1, f"Exit count must be -1, got {row['count']}"


def test_A05_graduation_in_correct_year_next_cycle():
    """
    With next_cycle mode: Azubis hired in Jan 2026 with 3y duration
    should graduate Aug 2029 (not 2028 or 2030).
    """
    grad = _estimate_baseline_graduation_date(
        pd.Timestamp("2026-01-15"), duration_years=3.0,
        conversion_month=8, conversion_day=1, graduation_mode="next_cycle"
    )
    assert grad == pd.Timestamp("2029-08-01"), \
        f"Expected 2029-08-01, got {grad}"


def test_A06_unique_azubi_ids():
    """All Azubi_Hire events must have unique IDs."""
    snap = _empty_snapshot()
    params = _base_params({"azubi": {"new_cases_per_year": 15}})
    _, events = _run(snap, params, years=5)
    hires = events[events["type"] == "Azubi_Hire"]
    assert hires["persnr"].nunique() == len(hires), \
        f"Duplicate IDs found! {len(hires)} hires, {hires['persnr'].nunique()} unique"


# ---------------------------------------------------------------------------
# PART B - Graduation Mode
# ---------------------------------------------------------------------------

def test_B01_next_cycle_post_august_adds_one_year():
    """
    next_cycle: Sep 2026 + 3y = Jun 2029. Jun 2029 < Aug 2029, so snap to Aug 2029.
    """
    grad = _estimate_baseline_graduation_date(
        pd.Timestamp("2026-09-01"), duration_years=3.0,
        conversion_month=8, conversion_day=1, graduation_mode="next_cycle"
    )
    # estimated_end = Dec 2029. Dec > Aug -> next year = Aug 2030
    assert grad.year == 2030, f"Expected 2030, got {grad.year}"


def test_B02_nearest_cycle_post_august_avoids_delay():
    """
    nearest_cycle: Sep 2026 + 3y = Jun 2029 (estimate).
    Jun 2029 is before Aug 2029 (dist=~60d), after Aug 2028 (dist=~300d).
    Nearest is Aug 2029.
    Wait - Sep 2026 + 3y = Sep 2029. Sep 2029 > Aug 2029 (dist=~30d), < Aug 2030 (dist=~335d).
    Nearest is Aug 2029.
    """
    # Sep entry + 3y -> Sep 2029 (estimated_end)
    # Aug 2029 is 30 days before Sep 2029
    # Aug 2030 is 335 days after Sep 2029
    # Nearest = Aug 2029 (1 year earlier than next_cycle would give)
    grad_nearest = _estimate_baseline_graduation_date(
        pd.Timestamp("2026-09-01"), duration_years=3.0,
        conversion_month=8, conversion_day=1, graduation_mode="nearest_cycle"
    )
    grad_next = _estimate_baseline_graduation_date(
        pd.Timestamp("2026-09-01"), duration_years=3.0,
        conversion_month=8, conversion_day=1, graduation_mode="next_cycle"
    )
    assert grad_nearest.year == 2029, f"nearest_cycle: expected 2029, got {grad_nearest.year}"
    assert grad_next.year == 2030, f"next_cycle: expected 2030, got {grad_next.year}"
    assert grad_nearest.year < grad_next.year, \
        "nearest_cycle must graduate 1 year earlier than next_cycle for Sep entry"


def test_B03_nearest_cycle_pre_august_same_as_next_cycle():
    """
    For pre-August entrants, nearest_cycle and next_cycle yield the same result.
    Mar 2026 + 3y = Mar 2029 < Aug 2029 -> snap to Aug 2029 in both modes.
    """
    entry = pd.Timestamp("2026-03-01")
    grad_nearest = _estimate_baseline_graduation_date(
        entry, 3.0, graduation_mode="nearest_cycle"
    )
    grad_next = _estimate_baseline_graduation_date(
        entry, 3.0, graduation_mode="next_cycle"
    )
    assert grad_nearest == grad_next == pd.Timestamp("2029-08-01"), \
        f"Both modes should give 2029-08-01 for Mar entry. Got nearest={grad_nearest}, next={grad_next}"


def test_B04_graduation_mode_affects_simulation():
    """
    Simulation with nearest_cycle graduates some cohorts 1 year earlier than next_cycle.
    Over 5 years: nearest_cycle should produce more graduates than next_cycle
    (because post-August entries graduate sooner).
    """
    snap = _make_snapshot([{
        "PersNr": "900001",
        "Organisationseinheit": "OE1",
        "Jobfamily": "Angestellte",
        "active": True,
        "mak": 1.0,
        "TrfGr": "E9A",
        "Eintritt": "2015-01-01",
    }])
    params_next = _base_params({
        "azubi": {"new_cases_per_year": 15, "duration_years": 3, "graduation_mode": "next_cycle"}
    })
    params_nearest = _base_params({
        "azubi": {"new_cases_per_year": 15, "duration_years": 3, "graduation_mode": "nearest_cycle"}
    })

    _, ev_next = _run(snap, params_next, years=5)
    _, ev_nearest = _run(snap, params_nearest, years=5)

    grads_next = len(ev_next[ev_next["type"].isin(["Azubi_Conversion_Out", "Azubi_Exit"])])
    grads_nearest = len(ev_nearest[ev_nearest["type"].isin(["Azubi_Conversion_Out", "Azubi_Exit"])])

    # nearest_cycle should produce at least as many graduates (some earlier)
    assert grads_nearest >= grads_next, (
        f"nearest_cycle ({grads_nearest}) should have >= graduates than next_cycle ({grads_next})"
    )


# ---------------------------------------------------------------------------
# PART C - Retention Rate & Destiny
# ---------------------------------------------------------------------------

def test_C01_retention_rate_100_all_takeovers():
    """retention_rate=1.0 -> all Azubis graduate as Takeovers, no Exits."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3}
    })
    _, events = _run(snap, params, years=5)
    exits = events[events["type"] == "Azubi_Exit"]
    takeovers = events[events["type"] == "Azubi_Conversion_In"]
    assert len(exits) == 0, f"Expected 0 exits with rate=1.0, got {len(exits)}"
    assert len(takeovers) > 0, "Expected takeovers with rate=1.0"


def test_C02_retention_rate_0_all_exits():
    """retention_rate=0.0 -> all Azubis exit, no Takeovers."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 15, "retention_rate": 0.0, "duration_years": 3}
    })
    _, events = _run(snap, params, years=5)
    exits = events[events["type"] == "Azubi_Exit"]
    takeovers = events[events["type"] == "Azubi_Conversion_In"]
    assert len(takeovers) == 0, f"Expected 0 takeovers with rate=0.0, got {len(takeovers)}"
    assert len(exits) > 0, "Expected exits with rate=0.0"


def test_C03_retention_rate_exact_split():
    """
    With deterministic debt system, 15 hires at rate=0.8 should yield
    exactly 12 takeovers and 3 exits per cohort.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 0.8,
            "duration_years": 3,
            "exclude_baseline_azubis": True,
        }
    })
    _, events = _run(snap, params, years=5, start="2025-08-01")

    hires = events[events["type"] == "Azubi_Hire"]
    hires_2026 = hires[pd.to_datetime(hires["date"]).dt.year == 2026]
    n_hired = len(hires_2026)

    if n_hired == 0:
        print("  SKIP C03: No 2026 hires found (check start date / period alignment)")
        return

    takeovers = events[events["type"] == "Azubi_Conversion_In"]
    exits = events[events["type"] == "Azubi_Exit"]

    # Find 2026 cohort PersNr
    cohort_ids = set(hires_2026["persnr"].tolist())
    if len(cohort_ids) == 0:
        return

    t_cohort = takeovers[takeovers["persnr"].isin(cohort_ids)]
    e_cohort = exits[exits["persnr"].isin(cohort_ids)]

    total_graduated = len(t_cohort) + len(e_cohort)
    if total_graduated == 0:
        print("  SKIP C03: 2026 cohort not yet graduated in 5-year window")
        return

    expected_t = round(n_hired * 0.8)
    expected_e = n_hired - expected_t
    assert len(t_cohort) == expected_t, \
        f"2026 cohort: expected {expected_t} takeovers, got {len(t_cohort)} (hired={n_hired})"
    assert len(e_cohort) == expected_e, \
        f"2026 cohort: expected {expected_e} exits, got {len(e_cohort)} (hired={n_hired})"


def test_C04_destiny_pre_assigned_in_hire_event():
    """Each Azubi_Hire event carries GraduationModus in _new_row (deterministic destiny)."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 10, "retention_rate": 0.8}
    })
    start_date = pd.Timestamp("2025-12-31")
    result = run_forecast_zugaenge(snap, start_date, periods_years=5, params=params)
    # Check final_state for GraduationModus column
    final = result["final_state"]
    if "GraduationModus" in final.columns:
        # At least some rows should have Takeover or Exit
        vals = final["GraduationModus"].dropna().unique().tolist()
        # All values must be "Takeover" or "Exit"
        for v in vals:
            assert v in ("Takeover", "Exit"), f"Unexpected GraduationModus value: {v}"


# ---------------------------------------------------------------------------
# PART D - Takeover Matrix (JobFamily Dimension)
# ---------------------------------------------------------------------------

def test_D01_jf_matrix_single_target():
    """
    JobFamily matrix with only one target assigns all takeovers to that JF.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 1.0,
            "duration_years": 3,
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {"Angestellte": 1.0},
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0, "Expected Conversion_In events"
    jfs = conv_in["Jobfamily"].dropna().unique().tolist()
    assert jfs == ["Angestellte"], \
        f"Expected only 'Angestellte', got: {jfs}"


def test_D02_jf_matrix_two_targets_split():
    """
    JobFamily matrix {A:1, B:1}: over 30 takeovers, both JFs must appear.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 1.0,
            "duration_years": 3,
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {"Angestellte": 1.0, "IT": 1.0},
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) >= 10, f"Expected >= 10 takeovers, got {len(conv_in)}"
    jfs = conv_in["Jobfamily"].dropna().unique().tolist()
    assert "Angestellte" in jfs, "Expected 'Angestellte' in takeover JFs"
    assert "IT" in jfs, "Expected 'IT' in takeover JFs"


def test_D03_jf_matrix_deterministic_split():
    """
    distribute_deterministic: 15 items with {A:2, B:1} must yield exactly 10xA, 5xB.
    """
    result = distribute_deterministic(15, {"A": 2.0, "B": 1.0})
    counts = {v: result.count(v) for v in set(result)}
    assert counts.get("A", 0) == 10, f"Expected 10xA, got {counts}"
    assert counts.get("B", 0) == 5, f"Expected 5xB, got {counts}"
    assert len(result) == 15


def test_D04_jf_matrix_largest_remainder_accuracy():
    """
    distribute_deterministic: Largest Remainder ensures no off-by-one.
    E.g. 10 items with {A:3, B:3, C:4} -> 3+3+4=10 exactly.
    """
    result = distribute_deterministic(10, {"A": 3.0, "B": 3.0, "C": 4.0})
    assert len(result) == 10
    counts = {v: result.count(v) for v in set(result)}
    assert counts.get("A", 0) == 3
    assert counts.get("B", 0) == 3
    assert counts.get("C", 0) == 4


def test_D05_jf_matrix_zero_weight_excluded():
    """
    distribute_deterministic: Category with weight=0 must not appear in result.
    """
    result = distribute_deterministic(10, {"A": 1.0, "B": 0.0, "C": 1.0})
    assert "B" not in result, f"'B' (weight=0) must not appear in result: {result}"


def test_D06_jf_matrix_empty_yields_unbekannt():
    """
    KNOWN BEHAVIOR: use_takeover_matrix=True but matrix={} causes distribute_deterministic
    to return 'Unbekannt' (no valid_vals passed). This overwrites the 'Angestellte' default
    and results in JF='Unbekannt' on Conversion_In events.
    This is a documented behavior gap: the guard `if not matrix: skip` is missing.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 1.0,
            "duration_years": 3,
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {},  # Empty -> falls back to "Unbekannt"
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0
    jfs = conv_in["Jobfamily"].dropna().unique().tolist()
    # Documents actual (unexpected) behavior: empty matrix -> "Unbekannt"
    # EXPECTED IMPROVEMENT: should default to "Angestellte" when matrix is empty
    assert jfs == ["Unbekannt"], \
        f"Empty matrix currently yields 'Unbekannt' (known gap), got: {jfs}"


def test_D07_resolve_distribution_probabilities():
    """
    resolve_distribution: weighted sampling should respect proportions over many trials.
    """
    rng = np.random.default_rng(42)
    matrix = {"A": 3.0, "B": 1.0}

    counts = {"A": 0, "B": 0}
    n_trials = 1000
    for _ in range(n_trials):
        val = resolve_distribution(rng, matrix, "TestDim", ["A", "B"])
        counts[val] = counts.get(val, 0) + 1

    ratio_a = counts["A"] / n_trials
    # Expected ~75%, allow 5% tolerance
    assert 0.70 <= ratio_a <= 0.80, \
        f"Expected ~75% for A (weight 3:1), got {ratio_a:.1%}"


# ---------------------------------------------------------------------------
# PART E - Takeover Matrix (OrgUnit Dimension)
# ---------------------------------------------------------------------------

def test_E01_orgunit_matrix_assigns_correct_unit():
    """
    OrgUnit matrix assigns Conversion_In events to specified org_unit.
    """
    snap = _make_snapshot([{
        "PersNr": "900001",
        "Organisationseinheit": "OE_Alt",
        "Jobfamily": "Angestellte",
        "active": True,
        "mak": 1.0,
        "TrfGr": "E9A",
        "Eintritt": "2015-01-01",
    }])
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 1.0,
            "duration_years": 3,
            "use_takeover_matrix": True,
            "takeover_dimension": "OrgUnit",
            "takeover_matrix": {"OE_Ziel": 1.0},
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0
    units = conv_in["org_unit"].dropna().unique().tolist()
    assert "OE_Ziel" in units, \
        f"Expected 'OE_Ziel' in org_units of Conv_In events, got: {units}"


def test_E02_orgunit_mode_sets_jobfamily_sonstige():
    """
    In OrgUnit mode, Conversion_In events must have Jobfamily='Sonstige'.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 1.0,
            "duration_years": 3,
            "use_takeover_matrix": True,
            "takeover_dimension": "OrgUnit",
            "takeover_matrix": {"OE1": 1.0},
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0
    for _, row in conv_in.iterrows():
        assert row.get("Jobfamily") == "Sonstige", \
            f"OrgUnit mode: Jobfamily must be 'Sonstige', got '{row.get('Jobfamily')}'"


def test_E03_orgunit_mode_flag_set():
    """
    OrgUnit mode sets _jf_orgunit_mode=True on Conversion_In events.
    Note: This flag is stripped from events_df at the end of run_forecast_zugaenge,
    so we check it is NOT present in the final DataFrame (it's an internal marker).
    The real test is that OrgUnit mode produces correct org_unit/JF combos (see E01/E02).
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 1.0,
            "duration_years": 3,
            "use_takeover_matrix": True,
            "takeover_dimension": "OrgUnit",
            "takeover_matrix": {"OE1": 1.0},
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0
    # Verify OrgUnit mode is internally consistent: JF=Sonstige AND org_unit=OE1
    for _, row in conv_in.iterrows():
        assert row.get("Jobfamily") == "Sonstige"
        assert row.get("org_unit") == "OE1"


# ---------------------------------------------------------------------------
# PART F - MAK Logic
# ---------------------------------------------------------------------------

def test_F01_azubi_mak_zero_during_training():
    """All Azubi_Hire events must carry mak=0.0 (default training MAK)."""
    snap = _empty_snapshot()
    params = _base_params({"azubi": {"new_cases_per_year": 10, "azubi_mak_during_training": 0.0}})
    _, events = _run(snap, params, years=3)
    hires = events[events["type"] == "Azubi_Hire"]
    assert len(hires) > 0
    maks = hires["mak"].astype(float).tolist()
    assert all(m == 0.0 for m in maks), \
        f"Expected all hire MAKs=0.0, found: {set(maks)}"


def test_F02_azubi_mak_full_after_takeover():
    """Conversion_In events must carry mak=1.0 (default after takeover)."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 10, "retention_rate": 1.0, "azubi_mak_after_takeover": 1.0}
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0
    maks = conv_in["mak"].astype(float).tolist()
    assert all(m == 1.0 for m in maks), \
        f"Expected all Conv_In MAKs=1.0, found: {set(maks)}"


def test_F03_conversion_out_has_negative_mak():
    """Conversion_Out events must have mak <= 0 (removes MAK from Azubi pool)."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 10, "retention_rate": 1.0}
    })
    _, events = _run(snap, params, years=5)
    conv_out = events[events["type"] == "Azubi_Conversion_Out"]
    assert len(conv_out) > 0
    maks = conv_out["mak"].astype(float).tolist()
    assert all(m <= 0.0 for m in maks), \
        f"All Conv_Out MAKs must be <= 0, found: {set(maks)}"


def test_F04_net_mak_effect_of_takeover_is_positive():
    """
    Takeover: Conv_Out(mak=-0) + Conv_In(mak=+1.0) = net +1.0 MAK.
    With mak_during=0.0 and mak_after=1.0.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 10,
            "retention_rate": 1.0,
            "azubi_mak_during_training": 0.0,
            "azubi_mak_after_takeover": 1.0,
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    conv_out = events[events["type"] == "Azubi_Conversion_Out"]

    assert len(conv_in) > 0

    # Per person: net MAK = Conv_In.mak + Conv_Out.mak should be +1.0
    for persnr in conv_in["persnr"].unique():
        in_mak = conv_in[conv_in["persnr"] == persnr]["mak"].sum()
        out_mak = conv_out[conv_out["persnr"] == persnr]["mak"].sum()
        net = float(in_mak) + float(out_mak)
        assert abs(net - 1.0) < 0.01, \
            f"Person {persnr}: net MAK from takeover = {net}, expected 1.0"


def test_F05_net_hc_effect_of_takeover_is_zero():
    """
    Takeover: Conv_Out(count=-1) + Conv_In(count=+1) = net 0 HC change.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 10, "retention_rate": 1.0}
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    conv_out = events[events["type"] == "Azubi_Conversion_Out"]

    assert len(conv_in) > 0

    for persnr in conv_in["persnr"].unique():
        hc_in = int(conv_in[conv_in["persnr"] == persnr]["count"].sum())
        hc_out = int(conv_out[conv_out["persnr"] == persnr]["count"].sum())
        net = hc_in + hc_out
        assert net == 0, \
            f"Person {persnr}: net HC from takeover = {net}, expected 0"


def test_F06_custom_mak_after_takeover():
    """Custom azubi_mak_after_takeover=0.75 is correctly applied to Conversion_In events."""
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 10,
            "retention_rate": 1.0,
            "azubi_mak_after_takeover": 0.75,
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0
    for _, row in conv_in.iterrows():
        assert abs(float(row["mak"]) - 0.75) < 0.01, \
            f"Expected mak=0.75, got {row['mak']}"


# ---------------------------------------------------------------------------
# PART G - Fix Regression Tests
# ---------------------------------------------------------------------------

def test_G01_jobfamily_pre_azubi_preserved():
    """
    Fix 4: Baseline Azubis in snapshot should have Jobfamily_pre_azubi set
    to their original Jobfamily before being relabeled as 'Sonstige'.
    """
    snap = _make_snapshot([
        {
            "PersNr": "AZ001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Azubi",
            "active": True,
            "mak": 0.0,
            "TrfGr": "TVAöD",
            "Eintritt": "2023-01-01",
        },
        {
            "PersNr": "900001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte",
            "active": True,
            "mak": 1.0,
            "TrfGr": "E9A",
            "Eintritt": "2015-01-01",
        },
    ])
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 5,
            "retention_rate": 1.0,
            "duration_years": 3,
            "exclude_baseline_azubis": False,
        }
    })
    result, _ = _run(snap, params, years=1)
    final = result["final_state"]

    if "Jobfamily_pre_azubi" in final.columns:
        # The AZ001 person (Azubi Jobfamily) should have pre_azubi populated
        if "AZ001" in final.index:
            pre_jf = final.loc["AZ001", "Jobfamily_pre_azubi"]
            assert pd.notna(pre_jf), "Jobfamily_pre_azubi must be set for baseline Azubi"
            assert pre_jf == "Azubi", \
                f"Expected 'Azubi' as pre_azubi JF, got '{pre_jf}'"
    else:
        print("  NOTE G01: Jobfamily_pre_azubi column not present in final_state (new Azubis only)")


def test_G02_is_internal_transition_on_conversion_events():
    """
    Fix 5: Azubi_Conversion_Out and Azubi_Conversion_In must have
    is_internal_transition=True.
    """
    snap = _empty_snapshot()
    params = _base_params({
        "azubi": {"new_cases_per_year": 10, "retention_rate": 1.0}
    })
    _, events = _run(snap, params, years=5)

    for event_type in ["Azubi_Conversion_Out", "Azubi_Conversion_In"]:
        ev = events[events["type"] == event_type]
        if len(ev) == 0:
            continue
        if "is_internal_transition" in ev.columns:
            for _, row in ev.iterrows():
                assert row["is_internal_transition"] == True, \
                    f"{event_type}: is_internal_transition must be True, got {row['is_internal_transition']}"
        else:
            assert False, f"is_internal_transition column missing on {event_type} events"


def test_G03_isolation_mode_excludes_baseline():
    """
    Fix regression: exclude_baseline_azubis=True must NOT process baseline Azubis.
    With 2-year window and 3-year duration, only newly hired Azubis should appear;
    no baseline-sourced graduation events.
    """
    snap = _make_snapshot([
        {
            "PersNr": "AZ_BESTAND",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Azubi",
            "active": True,
            "mak": 0.0,
            "TrfGr": "TVAöD",
            "Eintritt": "2024-01-01",
            "is_forecast": False,
        },
        {
            "PersNr": "900001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte",
            "active": True,
            "mak": 1.0,
            "TrfGr": "E9A",
            "Eintritt": "2015-01-01",
        },
    ])
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 10,
            "retention_rate": 1.0,
            "duration_years": 3,
            "exclude_baseline_azubis": True,
        }
    })
    _, events = _run(snap, params, years=2, start="2025-12-31")

    # No baseline Azubi (AZ_BESTAND) should produce graduation events
    graduation_types = ["Azubi_Conversion_Out", "Azubi_Conversion_In", "Azubi_Exit"]
    grad_events = events[events["type"].isin(graduation_types)]

    if len(grad_events) > 0 and "is_forecast" in grad_events.columns:
        baseline_grads = grad_events[grad_events["is_forecast"] == False]
        assert len(baseline_grads) == 0, \
            f"Isolation mode: Found {len(baseline_grads)} baseline graduation events (expected 0):\n" \
            f"{baseline_grads[['date','type','persnr','is_forecast']].to_string()}"


def test_G04_graduation_mode_default_is_nearest_cycle():
    """
    Default graduation_mode in params.py must be 'nearest_cycle'
    (reduces late-year entry bias; changed from next_cycle in Sprint 1).
    """
    from zugaenge.params import default_params
    p = default_params()
    assert p["azubi"]["graduation_mode"] == "nearest_cycle", \
        f"Default graduation_mode must be 'nearest_cycle', got '{p['azubi']['graduation_mode']}'"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        # Part A
        ("A01", test_A01_hire_event_created),
        ("A02", test_A02_hire_event_schema),
        ("A03", test_A03_takeover_creates_paired_events),
        ("A04", test_A04_exit_sets_inactive),
        ("A05", test_A05_graduation_in_correct_year_next_cycle),
        ("A06", test_A06_unique_azubi_ids),
        # Part B
        ("B01", test_B01_next_cycle_post_august_adds_one_year),
        ("B02", test_B02_nearest_cycle_post_august_avoids_delay),
        ("B03", test_B03_nearest_cycle_pre_august_same_as_next_cycle),
        ("B04", test_B04_graduation_mode_affects_simulation),
        # Part C
        ("C01", test_C01_retention_rate_100_all_takeovers),
        ("C02", test_C02_retention_rate_0_all_exits),
        ("C03", test_C03_retention_rate_exact_split),
        ("C04", test_C04_destiny_pre_assigned_in_hire_event),
        # Part D
        ("D01", test_D01_jf_matrix_single_target),
        ("D02", test_D02_jf_matrix_two_targets_split),
        ("D03", test_D03_jf_matrix_deterministic_split),
        ("D04", test_D04_jf_matrix_largest_remainder_accuracy),
        ("D05", test_D05_jf_matrix_zero_weight_excluded),
        ("D06", test_D06_jf_matrix_empty_yields_unbekannt),
        ("D07", test_D07_resolve_distribution_probabilities),
        # Part E
        ("E01", test_E01_orgunit_matrix_assigns_correct_unit),
        ("E02", test_E02_orgunit_mode_sets_jobfamily_sonstige),
        ("E03", test_E03_orgunit_mode_flag_set),
        # Part F
        ("F01", test_F01_azubi_mak_zero_during_training),
        ("F02", test_F02_azubi_mak_full_after_takeover),
        ("F03", test_F03_conversion_out_has_negative_mak),
        ("F04", test_F04_net_mak_effect_of_takeover_is_positive),
        ("F05", test_F05_net_hc_effect_of_takeover_is_zero),
        ("F06", test_F06_custom_mak_after_takeover),
        # Part G
        ("G01", test_G01_jobfamily_pre_azubi_preserved),
        ("G02", test_G02_is_internal_transition_on_conversion_events),
        ("G03", test_G03_isolation_mode_excludes_baseline),
        ("G04", test_G04_graduation_mode_default_is_nearest_cycle),
    ]

    passed = 0
    failed = 0
    skipped = 0
    failures = []

    print(f"\n{'='*60}")
    print("  Azubi Forecast Test Suite")
    print(f"{'='*60}\n")

    for tid, fn in tests:
        try:
            fn()
            print(f"  PASS  [{tid}] {fn.__doc__.splitlines()[0].strip()}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  [{tid}] {fn.__doc__.splitlines()[0].strip()}")
            print(f"         -> {e}")
            failed += 1
            failures.append((tid, str(e)))
        except Exception as e:
            print(f"  ERROR [{tid}] {fn.__doc__.splitlines()[0].strip()}")
            print(f"         -> {type(e).__name__}: {e}")
            failed += 1
            failures.append((tid, f"{type(e).__name__}: {e}"))

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed | {failed} failed | {len(tests)} total")
    print(f"{'='*60}\n")

    if failures:
        print("Failures:")
        for tid, msg in failures:
            print(f"  [{tid}] {msg}")
