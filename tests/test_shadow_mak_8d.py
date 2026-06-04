"""
Shadow test 8d — MAK/FTE/BsGrd vectorisation equivalence.

Purpose
-------
Verify that a vectorised groupby.transform formula produces values
*identical* to the per-person _distribute_value loop inside
_update_existing_rows (lines 758-789 of compact_simulation_engine.py).

No production code is changed. Pure read-only comparison.

Scenarios
---------
S1  Single person, 1 row,  Soll_FTE=1.0           — trivial distribution
S2  Single person, 2 rows, Soll_FTE=[0.6, 0.4]    — proportional split
S3  Single person, 2 rows, Soll_FTE=[0.0, 0.0]    — zero-weight fallback
S4  Two persons, each 1 row                         — independence / no cross-contamination
S5  Person in atz_map                               — ATZ override; MAK unaffected
S6  mak=0.0                                         — all-zero columns
S7  Non-trivial Soll_FTE (0.75)                     — FTE_assigned formula
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from dataloader.compact_simulation_engine import (
    _normalize_persnr_series,
    _update_existing_rows,
)

TARGET_DATE = pd.Timestamp("2026-06-04")


# ── Shadow formula (vectorised, no production change) ────────────────────────


def _shadow_mak_per_row(
    snapshot_df: pd.DataFrame,
    final_employee_df: pd.DataFrame,
) -> pd.Series:
    """
    Vectorised equivalent of the per-person loop block in _update_existing_rows.

    Replicates exactly:
        weights = future_df.loc[row_indices, "Soll_FTE"]
        mak_values = _distribute_value(float(emp.get("mak", 0.0)), weights)

    where _distribute_value is:
        weights = to_numeric(weights).fillna(0)
        if sum(weights) <= 0: replace all weights with 1.0  (equal fallback)
        return (weights / sum(weights)) * total_mak

    Returns a Series indexed by snapshot_df row index,
    covering only the active-existing rows (same population as the loop).
    """
    snap = snapshot_df.copy()
    snap["_pnr"] = _normalize_persnr_series(snap["PersNr"].fillna(""))

    final = final_employee_df.copy()
    final["PersNr"] = _normalize_persnr_series(final["PersNr"])
    final = final.drop_duplicates(subset=["PersNr"], keep="last").set_index("PersNr")

    occupied = snap["_pnr"].ne("")
    existing_ids = set(snap.loc[occupied, "_pnr"])
    active_ids = set(final[final.get("active", False) == True].index)
    ae_mask = snap["_pnr"].isin(existing_ids & active_ids)
    ae = snap.loc[ae_mask].copy()
    if ae.empty:
        return pd.Series(dtype=float)

    mak_lookup = (
        final["mak"].fillna(0.0)
        if "mak" in final.columns
        else pd.Series(0.0, index=final.index)
    )
    total_mak = ae["_pnr"].map(mak_lookup).fillna(0.0)

    if "Soll_FTE" in snap.columns:
        weights = pd.to_numeric(ae["Soll_FTE"], errors="coerce").fillna(0.0)
        # Per-person Soll_FTE sum (mirrors _distribute_value sum check)
        fte_sum = ae.groupby("_pnr")["Soll_FTE"].transform(
            lambda x: float(pd.to_numeric(x, errors="coerce").fillna(0.0).sum())
        )
        count = ae.groupby("_pnr")["_pnr"].transform("count").astype(float)

        # Proportional where sum > 0; equal fallback where sum <= 0
        # (mirrors: if sum <= 0: weights = Series(1.0, ...) → ratio = 1/n)
        ratio = weights / fte_sum.replace(0.0, np.nan)   # NaN where sum=0
        ratio = ratio.fillna(1.0 / count)                 # equal split fallback
        mak_per_row = total_mak * ratio
    else:
        count = ae.groupby("_pnr")["_pnr"].transform("count").astype(float)
        mak_per_row = total_mak / count

    return mak_per_row


# ── Test data helpers ─────────────────────────────────────────────────────────


def _snap_row(pid: str, mak: float = 1.0, soll_fte: float = 1.0) -> dict:
    """Minimal snapshot row with all MAK/FTE/ATZ columns."""
    return {
        "PersNr": pid,
        "Personalnummer": pid,
        "Is_Vacant": False,
        "ATZ_Status": "Kein ATZ",
        "ist_atz_fr": False,
        "Soll_FTE": soll_fte,
        "MAK_Calculated": mak,
        "mak": mak,
        "MAK": mak,
        "BsGrd": round(mak * 100.0, 6),
        "FTE_person": mak,
        "FTE_assigned": mak * soll_fte,
    }


def _final_row(pid: str, mak: float = 1.0) -> dict:
    """Minimal final_employee_df row (active, no sync_cols → loop MAK-only path)."""
    return {"PersNr": pid, "active": True, "mak": mak}


def _run(snap: pd.DataFrame, final: pd.DataFrame, atz: pd.DataFrame | None = None) -> pd.DataFrame:
    return _update_existing_rows(
        snapshot_df=snap,
        final_employee_df=final,
        target_date=TARGET_DATE,
        atz_status_df=atz if atz is not None else pd.DataFrame(),
    )


def _assert_mak_match(result: pd.DataFrame, shadow: pd.Series, *, label: str = "") -> None:
    """Compare all MAK/BsGrd/FTE columns in loop result against shadow values."""
    prefix = f"[{label}] " if label else ""
    sv = shadow.values
    np.testing.assert_allclose(result["mak"].values,            sv,                        rtol=1e-10, err_msg=f"{prefix}mak")
    np.testing.assert_allclose(result["MAK_Calculated"].values,  sv,                        rtol=1e-10, err_msg=f"{prefix}MAK_Calculated")
    np.testing.assert_allclose(result["MAK"].values,             sv,                        rtol=1e-10, err_msg=f"{prefix}MAK")
    np.testing.assert_allclose(result["BsGrd"].values,           np.round(sv * 100.0, 6),   rtol=1e-10, err_msg=f"{prefix}BsGrd")
    if "FTE_person" in result.columns:
        np.testing.assert_allclose(result["FTE_person"].values,  sv,                        rtol=1e-10, err_msg=f"{prefix}FTE_person")


# ── Scenario tests ────────────────────────────────────────────────────────────


def test_shadow_s1_single_row():
    """S1: 1 person, 1 row, Soll_FTE=1.0 → mak passes through unchanged."""
    snap  = pd.DataFrame([_snap_row("000001", mak=0.8, soll_fte=1.0)])
    final = pd.DataFrame([_final_row("000001", mak=0.8)])

    result = _run(snap, final)
    shadow = _shadow_mak_per_row(snap, final)

    assert len(shadow) == 1, "expected 1 active row"
    np.testing.assert_allclose(shadow.values, [0.8], rtol=1e-10, err_msg="S1 shadow value")
    _assert_mak_match(result, shadow, label="S1")


def test_shadow_s2_two_rows_proportional():
    """S2: 1 person, 2 rows, Soll_FTE=[0.6, 0.4] → 48% / 32% proportional split."""
    snap = pd.DataFrame([
        _snap_row("000001", mak=0.8, soll_fte=0.6),
        _snap_row("000001", mak=0.8, soll_fte=0.4),
    ])
    final = pd.DataFrame([_final_row("000001", mak=0.8)])

    result = _run(snap, final)
    shadow = _shadow_mak_per_row(snap, final)

    assert len(shadow) == 2, "expected 2 active rows"
    # Expected: 0.8 * 0.6/1.0 = 0.48 and 0.8 * 0.4/1.0 = 0.32
    np.testing.assert_allclose(shadow.values, [0.48, 0.32], rtol=1e-10, err_msg="S2 shadow split")
    _assert_mak_match(result, shadow, label="S2")
    # FTE_assigned = mak_per_row * Soll_FTE (snapshot value, not final_df)
    expected_fte = shadow.values * np.array([0.6, 0.4])  # [0.288, 0.128]
    np.testing.assert_allclose(result["FTE_assigned"].values, expected_fte, rtol=1e-10, err_msg="S2 FTE_assigned")


def test_shadow_s3_zero_soll_fte_fallback():
    """S3: Soll_FTE=[0, 0] → equal distribution fallback (mirrors _distribute_value)."""
    snap = pd.DataFrame([
        _snap_row("000002", mak=0.6, soll_fte=0.0),
        _snap_row("000002", mak=0.6, soll_fte=0.0),
    ])
    final = pd.DataFrame([_final_row("000002", mak=0.6)])

    result = _run(snap, final)
    shadow = _shadow_mak_per_row(snap, final)

    assert len(shadow) == 2, "expected 2 active rows"
    np.testing.assert_allclose(shadow.values, [0.3, 0.3], rtol=1e-10, err_msg="S3 shadow zero-weight fallback")
    _assert_mak_match(result, shadow, label="S3")


def test_shadow_s4_two_persons_independent():
    """S4: 2 persons, each 1 row → each gets own mak, no cross-contamination."""
    snap = pd.DataFrame([
        _snap_row("000001", mak=1.0, soll_fte=1.0),
        _snap_row("000002", mak=1.0, soll_fte=0.5),
    ])
    final = pd.DataFrame([
        _final_row("000001", mak=0.9),
        _final_row("000002", mak=0.5),
    ])

    result = _run(snap, final)
    shadow = _shadow_mak_per_row(snap, final)

    assert len(shadow) == 2, "expected 2 active rows"
    # Row 0 → person 000001; row 1 → person 000002 (snapshot order preserved)
    np.testing.assert_allclose(shadow.iloc[0], 0.9, rtol=1e-10, err_msg="S4 person 000001 shadow")
    np.testing.assert_allclose(shadow.iloc[1], 0.5, rtol=1e-10, err_msg="S4 person 000002 shadow")
    _assert_mak_match(result, shadow, label="S4")


def test_shadow_s5_atz_override_mak_unchanged():
    """S5: Person in atz_map → ATZ columns overridden; MAK values unaffected by ATZ."""
    snap  = pd.DataFrame([_snap_row("000003", mak=1.0, soll_fte=1.0)])
    final = pd.DataFrame([_final_row("000003", mak=0.7)])
    atz   = pd.DataFrame([{
        "PersNr": "000003",
        "ATZ_Status": "Freistellungsphase",
        "ist_atz_fr": True,
    }])

    result = _run(snap, final, atz)
    shadow = _shadow_mak_per_row(snap, final)

    np.testing.assert_allclose(shadow.values, [0.7], rtol=1e-10, err_msg="S5 shadow mak")
    _assert_mak_match(result, shadow, label="S5")
    # ATZ override applied by loop (not in shadow formula — checked directly)
    assert result["ATZ_Status"].iloc[0] == "Freistellungsphase", "S5: ATZ_Status not overridden"
    assert bool(result["ist_atz_fr"].iloc[0]) is True,            "S5: ist_atz_fr not overridden"


def test_shadow_s6_mak_zero():
    """S6: mak=0.0 in final_df → all MAK/BsGrd/FTE columns must be zero."""
    snap  = pd.DataFrame([_snap_row("000001", mak=1.0, soll_fte=1.0)])
    final = pd.DataFrame([_final_row("000001", mak=0.0)])

    result = _run(snap, final)
    shadow = _shadow_mak_per_row(snap, final)

    np.testing.assert_allclose(shadow.values, [0.0], rtol=1e-10, err_msg="S6 shadow zero")
    _assert_mak_match(result, shadow, label="S6")
    np.testing.assert_allclose(result["FTE_assigned"].values, [0.0], rtol=1e-10, err_msg="S6 FTE_assigned zero")


def test_shadow_s7_fte_assigned_uses_snapshot_soll_fte():
    """S7: FTE_assigned = mak_per_row × Soll_FTE from snapshot (not final_df)."""
    # mak_per_row = 0.6 (1 row, full weight)
    # FTE_assigned = 0.6 × 0.75 = 0.45
    snap  = pd.DataFrame([_snap_row("000001", mak=1.0, soll_fte=0.75)])
    final = pd.DataFrame([_final_row("000001", mak=0.6)])

    result = _run(snap, final)
    shadow = _shadow_mak_per_row(snap, final)

    np.testing.assert_allclose(shadow.values, [0.6], rtol=1e-10, err_msg="S7 shadow mak")
    _assert_mak_match(result, shadow, label="S7")
    np.testing.assert_allclose(
        result["FTE_assigned"].values, [0.6 * 0.75], rtol=1e-10,
        err_msg="S7 FTE_assigned = mak * snapshot_Soll_FTE",
    )
