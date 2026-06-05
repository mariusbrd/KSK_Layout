"""
Real-Daten-Shadow 8d — MAK/FTE/BsGrd-Vektorisierung vs. Loop.

Ziel
----
Verifikation auf echten Produktionsdaten (Planstellen.XLSX + Mitarbeiter.xlsx):
- 0 Differenzen zwischen Loop-Ergebnis und vektorisierter Shadow-Formel
- Zeitmessung: Loop vs. Shadow (Orientierungswert, kein Benchmark)

Kein Produktivcode geändert. Reine Vergleichs-/Timing-Analyse.
Übersprungen automatisch wenn Original-Daten nicht vorhanden.

Aufbau
------
1. Planstellen.XLSX  →  snapshot_df  (eine Zeile pro Position)
2. Mitarbeiter.xlsx  →  final_employee_df  (eine Zeile pro Person, BsGrd/100 als mak)
3. ATZ: leer (isolierter Test der MAK-Verteilungslogik)
4. _update_existing_rows (Loop) vs. _shadow_mak_per_row (vektorisiert)
5. Zellweiser Vergleich + Timing-Report
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from dataloader.compact_simulation_engine import _normalize_persnr_series, _update_existing_rows

# ── Pfade ─────────────────────────────────────────────────────────────────────

_ORIG = Path(__file__).resolve().parents[2] / "Original-Daten"
_PLANSTELLEN = _ORIG / "Planstellen.XLSX"
_MITARBEITER = _ORIG / "Mitarbeiter.xlsx"
TARGET_DATE = pd.Timestamp("2026-06-30")
VOLLZEIT_H = 39.0

pytestmark = pytest.mark.skipif(
    not (_PLANSTELLEN.exists() and _MITARBEITER.exists()),
    reason="Original-Daten nicht vorhanden — Shadow übersprungen",
)


# ── Daten-Aufbau ──────────────────────────────────────────────────────────────


def _load_planstellen() -> pd.DataFrame:
    pl = pd.read_excel(_PLANSTELLEN)
    # Personalnummer → PersNr (normalisiert)
    raw_pnr = pl["Personalnummer"].fillna("").astype(str)
    pl["PersNr"] = _normalize_persnr_series(raw_pnr)
    return pl


def _load_mitarbeiter() -> pd.DataFrame:
    ma = pd.read_excel(_MITARBEITER)
    ma["PersNr"] = _normalize_persnr_series(ma["PersNr"].fillna("").astype(str))
    return ma


def _build_snapshot(pl: pd.DataFrame, ma: pd.DataFrame) -> pd.DataFrame:
    """
    Minimales aber realistisches snapshot_df für _update_existing_rows.
    Enthält alle Pflicht-Spalten; keine fachliche Anreicherung (Jobfamilies, Cluster etc.).
    """
    ma_sub = ma[
        ["PersNr", "BsGrd", "TrfGr", "St", "GebDatum", "Eintritt", "Austritt",
         "Vertragsart", "MitarbGruppenbez.", "Text Gsch", "Status kundenindividuell"]
    ].copy()

    snap = pl.merge(ma_sub, on="PersNr", how="left")
    snap.index = range(len(snap))

    snap["Is_Vacant"] = snap["PersNr"].eq("")
    snap["Personalnummer"] = snap["PersNr"]

    soll_h = pd.to_numeric(snap["Sollarbeitszeit"], errors="coerce").fillna(0.0)
    snap["Soll_FTE"] = soll_h / VOLLZEIT_H        # nicht geclipt — echte Werte

    bsgrd = pd.to_numeric(snap["BsGrd"], errors="coerce").fillna(0.0)
    snap["BsGrd"]          = bsgrd
    snap["mak"]            = bsgrd / 100.0
    snap["MAK"]            = snap["mak"]
    snap["MAK_Calculated"] = snap["mak"]
    snap["FTE_person"]     = snap["mak"]
    snap["FTE_assigned"]   = snap["mak"] * snap["Soll_FTE"]
    snap["ATZ_Status"]     = "Kein ATZ"
    snap["ist_atz_fr"]     = False

    return snap


def _build_final_df(ma: pd.DataFrame) -> pd.DataFrame:
    """
    final_employee_df: eine Zeile pro aktivem Mitarbeiter.
    mak = BsGrd / 100 (Gesamt-Beschäftigungsgrad der Person aus Mitarbeiter.xlsx).
    Keine Summierung über Planstellen-Zeilen (korrekt: eine Zeile pro Person).
    """
    ma_active = ma[ma["PersNr"].ne("") & ma["PersNr"].notna()].copy()
    ma_active = ma_active.drop_duplicates(subset=["PersNr"], keep="last")
    bsgrd = pd.to_numeric(ma_active["BsGrd"], errors="coerce").fillna(0.0)
    return pd.DataFrame({
        "PersNr": ma_active["PersNr"].values,
        "mak":    bsgrd.values / 100.0,
        "active": True,
    })


# ── Shadow-Formel (identisch mit test_shadow_mak_8d.py) ──────────────────────


def _shadow_mak_per_row(
    snapshot_df: pd.DataFrame,
    final_employee_df: pd.DataFrame,
) -> pd.Series:
    """
    Vektorisierte Entsprechung des Per-Person-Loops in _update_existing_rows.
    Selbe Logik wie in test_shadow_mak_8d.py — hier dupliziert für Unabhängigkeit.
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
        fte_sum = ae.groupby("_pnr")["Soll_FTE"].transform(
            lambda x: float(pd.to_numeric(x, errors="coerce").fillna(0.0).sum())
        )
        count = ae.groupby("_pnr")["_pnr"].transform("count").astype(float)
        # Mirrors _distribute_value: sum <= 0 → equal fallback
        ratio = weights / fte_sum.where(fte_sum > 0, np.nan)
        ratio = ratio.fillna(1.0 / count)
        mak_per_row = total_mak * ratio
    else:
        count = ae.groupby("_pnr")["_pnr"].transform("count").astype(float)
        mak_per_row = total_mak / count

    return mak_per_row


# ── Diagnose-Helfer ───────────────────────────────────────────────────────────


def _describe_snapshot(snap: pd.DataFrame, final: pd.DataFrame) -> None:
    total_rows   = len(snap)
    occupied     = snap[~snap["Is_Vacant"] & snap["PersNr"].ne("")]
    n_occupied   = len(occupied)
    n_vacant     = total_rows - n_occupied
    n_persons    = occupied["PersNr"].nunique()
    row_counts   = occupied.groupby("PersNr").size()
    multi_row    = (row_counts > 1).sum()
    max_rows     = int(row_counts.max())
    zero_fte     = (pd.to_numeric(occupied["Soll_FTE"], errors="coerce").fillna(0.0) <= 0).sum()
    n_final      = len(final)

    print("\n--- Real-Daten-Struktur ---")
    print(f"  Planstellen gesamt:            {total_rows}")
    print(f"  Davon besetzt (PersNr != ''):  {n_occupied}")
    print(f"  Davon vacant:                  {n_vacant}")
    print(f"  Unique Personen (besetzt):     {n_persons}")
    print(f"  Personen mit > 1 Planst.:      {multi_row}  (max {max_rows} Zeilen)")
    print(f"  Zeilen mit Soll_FTE <= 0:      {zero_fte}")
    print(f"  final_employee_df Zeilen:      {n_final}")
    print(f"  Active in final & snap:        "
          f"{len(set(occupied['PersNr']) & set(final[final['active']]['PersNr']))}")


def _compare_and_report(
    result_loop: pd.DataFrame,
    shadow: pd.Series,
    snapshot_df: pd.DataFrame,
) -> int:
    """
    Zellweiser Vergleich: Loop-Ergebnis vs. Shadow-Formel.
    Gibt Anzahl Differenzen zurück (erwartet: 0).
    """
    # Shadow ist nur für active_existing_rows — dieselbe Maske ableiten
    snap_norm = snapshot_df.copy()
    snap_norm["_pnr"] = _normalize_persnr_series(snap_norm["PersNr"].fillna(""))
    ae_index = shadow.index   # Index der aktiven Zeilen im original snap

    total_diffs = 0
    tol = 1e-10

    cols_to_check = {
        "mak":            shadow,
        "MAK_Calculated": shadow,
        "MAK":            shadow,
        "BsGrd":          np.round(shadow * 100.0, 6),
        "FTE_person":     shadow,
    }

    print("\n--- Zellweiser Vergleich (Loop vs. Shadow) ---")
    for col, expected_series in cols_to_check.items():
        if col not in result_loop.columns:
            print(f"  {col}: Spalte nicht im Ergebnis - uebersprungen")
            continue
        loop_vals   = result_loop.loc[ae_index, col].values
        shadow_vals = expected_series.values
        diffs = np.where(
            np.abs(loop_vals - shadow_vals) > tol,
            1, 0
        )
        n_diff = int(diffs.sum())
        total_diffs += n_diff
        status = "OK" if n_diff == 0 else f"ABWEICHUNG ({n_diff})"
        print(f"  {col:<22} {status}")

    # FTE_assigned: mak_per_row * snapshot Soll_FTE
    if "FTE_assigned" in result_loop.columns and "Soll_FTE" in snapshot_df.columns:
        soll_fte = snapshot_df.loc[ae_index, "Soll_FTE"].fillna(0.0).values
        expected_fte = shadow.values * soll_fte
        loop_fte = result_loop.loc[ae_index, "FTE_assigned"].values
        n_diff_fte = int(np.sum(np.abs(loop_fte - expected_fte) > tol))
        total_diffs += n_diff_fte
        status = "OK" if n_diff_fte == 0 else f"ABWEICHUNG ({n_diff_fte})"
        print(f"  {'FTE_assigned':<22} {status}")

    print(f"\n  Gesamt-Differenzen: {total_diffs}  (erwartet: 0)")
    return total_diffs


# ── Test ──────────────────────────────────────────────────────────────────────


def test_shadow_realdata_mak_loop_vs_vectorised(capsys):
    """
    Real-Daten-Shadow: Loop vs. vektorisierte Formel auf echten Produktionsdaten.
    Erwartet: 0 Differenzen, identische Werte auf rtol=1e-10.
    """
    # 1. Daten laden
    pl = _load_planstellen()
    ma = _load_mitarbeiter()
    snap = _build_snapshot(pl, ma)
    final = _build_final_df(ma)

    _describe_snapshot(snap, final)

    # 2. Loop (_update_existing_rows) — Zeitmessung
    t0 = time.perf_counter()
    result_loop = _update_existing_rows(
        snapshot_df=snap.copy(),
        final_employee_df=final.copy(),
        target_date=TARGET_DATE,
        atz_status_df=pd.DataFrame(),   # ATZ isoliert — kein Override
    )
    t_loop = time.perf_counter() - t0

    # 3. Shadow (vektorisiert) — Zeitmessung
    t1 = time.perf_counter()
    shadow = _shadow_mak_per_row(snap.copy(), final.copy())
    t_shadow = time.perf_counter() - t1

    print(f"\n--- Timing ---")
    print(f"  Loop  (_update_existing_rows):  {t_loop:.4f} s")
    print(f"  Shadow (vektorisiert):          {t_shadow:.4f} s")
    print(f"  Faktor:                         {t_loop/max(t_shadow, 1e-9):.1f}x")

    # 4. Vergleich
    n_diffs = _compare_and_report(result_loop, shadow, snap)

    # 5. Assertion
    assert n_diffs == 0, (
        f"Shadow-Vergleich: {n_diffs} Abweichungen zwischen Loop und "
        f"vektorisierter Formel. Vektorisierung NICHT sicher."
    )

    print("\n--- Ergebnis ---")
    print("  Shadow-Vergleich: PASS - 0 Differenzen auf allen MAK/FTE/BsGrd-Spalten.")
    print("  Vektorisierung faechlich aequivalent. Bereit fuer Produktivvorschlag.")
