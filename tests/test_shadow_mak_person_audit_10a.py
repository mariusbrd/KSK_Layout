"""
Shadow-Test Hotspot 10a: build_mak_person_audit (utils/audit_helpers.py)

Vergleicht die bestehende Python-Groupby-Loop-Implementierung gegen
eine teilvektorisierte Shadow-Variante auf:
  - echten Produktionsdaten (future_snapshot_df aus Simulation)
  - 13 synthetischen Edge-Case-Szenarien

Kein Produktivcode veraendert.

Ausfuehren:
  py -m pytest tests/test_shadow_mak_person_audit_10a.py -v -s -q
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.audit_helpers import (
    _first_value,
    _numeric,
    _series_text,
    build_mak_person_audit,
    select_mak_column,
)

_ORIG = Path(__file__).resolve().parents[2] / "Original-Daten"
_HAS_REAL_DATA = (
    (_ORIG / "Planstellen.XLSX").exists() and (_ORIG / "Mitarbeiter.xlsx").exists()
)


# ============================================================
# Exakte Dokumentation build_mak_person_audit
# ============================================================
#
# Input:  df (DataFrame mit einer Zeile pro Planstelle/Row)
# Filter: Is_Vacant == True entfernt; PersNr isna() entfernt
#
# Gruppierung: groupby("PersNr", dropna=False, observed=True)
#              -> Sortierung im Output: MAK_sum desc, Anzahl_Zeilen desc
#
# Output: 14 Spalten, eine Zeile pro Person
#
# Spalte                Quelle / Semantik
# ─────────────────────────────────────────────────────────────────────
# PersNr                str(persnr) — Typ object
# Jobfamily             _first_value(Jobfamily, fallback=_first_value(JF-Cluster,""))
# Anzahl_Zeilen         int(len(group))  — alle Zeilen der Person
# Anzahl_Planstellen    int(Planstelle.nunique(dropna=True)) oder 0
# Planstellenliste      _series_text(Planstelle)  = " | ".join(unique non-NaN stripped)
# MAK_je_Zeile          " | ".join(f"{value:.4f}" for value in _audit_mak.tolist())
#                        (KEIN float()-Cast, Reihenfolge erhalten)
# MAK_sum               float(mak_values.sum())    aus aggregate_person_capacity
# MAK_max               float(mak_values.max())    aus aggregate_person_capacity
# EUR_sum               float(_numeric(Total_Cost_Year).sum())
# Vertragsart           _first_value(Vertragsart, "")
# MitarbGruppenbez.     _first_value(MitarbGruppenbez., "")
# Beschaeftigungsgrad_Kat  _first_value("Beschäftigungsgrad_Kat", "")  [Quelle: Umlaut!]
# Quelle_Logik          f"audit_only:{mak_col}" — Konstante pro Aufruf
# Auffaelligkeit        Flags (priorisiert):
#   mak_max > 1.000001                    -> "datenfehler_beschaeftigungsgrad"
#   mak_sum > 1 AND Allow_MAK_gt_1 first  -> "mehrfachbeschaeftigung_dokumentiert"
#   mak_sum > 1 AND Planstelle.nunique<=1 -> "doppelte_planstellenzuordnung_wahrscheinlich"
#   mak_sum > 1                           -> "mehrfachplanstelle_fachlich_pruefen"
#   else                                  -> "unauffaellig"
#   NB: Planstelle-nunique-Bedingung gilt nur wenn "Planstelle" in columns
#
# Downstream: mak_audit -> build_mak_decision_list -> MAK_Decision_List
#             mak_audit -> build_mak_decision_list -> build_mak_policy_impact
#             mak_audit -> MAK_Personen_Audit (Export-Sheet)
# ============================================================


# ── Shadow-Implementierung ────────────────────────────────────────────────────

_COL_ORDER = [
    "PersNr", "Jobfamily", "Anzahl_Zeilen", "Anzahl_Planstellen",
    "Planstellenliste", "MAK_je_Zeile", "MAK_sum", "MAK_max", "EUR_sum",
    "Vertragsart", "MitarbGruppenbez.", "Beschaeftigungsgrad_Kat",
    "Quelle_Logik", "Auffaelligkeit",
]

# Source column name (with umlaut) differs from output column name (ASCII)
_BSGRD_KAT_SRC = "Beschäftigungsgrad_Kat"
_BSGRD_KAT_OUT = "Beschaeftigungsgrad_Kat"


def _build_mak_person_audit_shadow(df: pd.DataFrame) -> pd.DataFrame:
    """
    Teilvektorisierte Shadow-Variante von build_mak_person_audit.

    Optimierungen gegenueber Original:
    A. MAK_sum, MAK_max, EUR_sum, Anzahl_Zeilen: groupby.agg statt Loop
    B. Anzahl_Planstellen: groupby.nunique statt Loop
    C. First-value-Felder: groupby.first() statt _first_value-Loop
    D. Auffaelligkeit: np.select statt if-elif-Kette pro Person
    E. Quelle_Logik: Konstante (mak_col identisch fuer alle Personen)
    F. Planstellenliste, MAK_je_Zeile: groupby.apply (Reihenfolge!)

    Unveraendert:
    - Filterung (Is_Vacant, PersNr.notna)
    - _numeric-Semantik
    - _series_text-Semantik
    - Sortierung: MAK_sum desc, Anzahl_Zeilen desc
    - Spaltenreihenfolge
    - NaN/None/Leerstring-Verhalten
    - Auffaelligkeit-Logik (exakt repliziert)
    """
    if df is None or df.empty or "PersNr" not in df.columns:
        return pd.DataFrame()

    work = df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    work = work[work["PersNr"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    mak_col = select_mak_column(work)
    if mak_col is None:
        work["_audit_mak"] = 0.0
    elif mak_col == "BsGrd":
        work["_audit_mak"] = _numeric(work[mak_col]) / 100.0
    else:
        work["_audit_mak"] = _numeric(work[mak_col])

    eur_col = "Total_Cost_Year" if "Total_Cost_Year" in work.columns else None
    work["_eur"] = _numeric(work[eur_col]) if eur_col else 0.0

    g = work.groupby("PersNr", sort=True, dropna=False)

    # A. Numerische Aggregationen
    agg = g.agg(
        MAK_sum=("_audit_mak", "sum"),
        MAK_max=("_audit_mak", "max"),
        EUR_sum=("_eur", "sum"),
        Anzahl_Zeilen=("_audit_mak", "count"),
    ).reset_index()

    agg["PersNr"] = agg["PersNr"].astype(str)

    # B. Anzahl_Planstellen + planstelle_le1 fuer Auffaelligkeit-Flag
    if "Planstelle" in work.columns:
        agg["Anzahl_Planstellen"] = g["Planstelle"].nunique(dropna=True).values
        _planst_le1 = agg["Anzahl_Planstellen"] <= 1
    else:
        agg["Anzahl_Planstellen"] = 0
        # Original: "Planstelle" not in rows.columns -> condition False
        _planst_le1 = pd.Series(False, index=agg.index)

    # C. Jobfamily mit JF-Cluster-Fallback
    if "Jobfamily" in work.columns:
        _jf = g["Jobfamily"].first()
        if "JF-Cluster" in work.columns:
            _jfc = g["JF-Cluster"].first().fillna("")
            agg["Jobfamily"] = _jf.fillna(_jfc).fillna("").values
        else:
            agg["Jobfamily"] = _jf.fillna("").values
    elif "JF-Cluster" in work.columns:
        agg["Jobfamily"] = g["JF-Cluster"].first().fillna("").values
    else:
        agg["Jobfamily"] = ""

    # C. First-value-Felder
    for _src, _out, _fb in [
        ("Vertragsart", "Vertragsart", ""),
        ("MitarbGruppenbez.", "MitarbGruppenbez.", ""),
        (_BSGRD_KAT_SRC, _BSGRD_KAT_OUT, ""),
    ]:
        if _src in work.columns:
            agg[_out] = g[_src].first().fillna(_fb).values
        else:
            agg[_out] = _fb

    # C. Allow_MAK_gt_1 fuer Auffaelligkeit-Flag
    if "Allow_MAK_gt_1" in work.columns:
        _allow_gt1 = g["Allow_MAK_gt_1"].first().fillna(False).astype(bool).values
    else:
        _allow_gt1 = False  # skalarer False, wird via np.select gebroadcasted

    # D. Auffaelligkeit vektorisiert (exakte Replikation der if-elif-Kette)
    _cond = [
        agg["MAK_max"] > 1.000001,
        (agg["MAK_sum"] > 1.000001) & _allow_gt1,
        (agg["MAK_sum"] > 1.000001) & _planst_le1,
        agg["MAK_sum"] > 1.000001,
    ]
    _choices = [
        "datenfehler_beschaeftigungsgrad",
        "mehrfachbeschaeftigung_dokumentiert",
        "doppelte_planstellenzuordnung_wahrscheinlich",
        "mehrfachplanstelle_fachlich_pruefen",
    ]
    agg["Auffaelligkeit"] = np.select(_cond, _choices, default="unauffaellig")

    # E. Quelle_Logik: Konstante pro Aufruf (mak_col identisch fuer alle Personen)
    agg["Quelle_Logik"] = f"audit_only:{mak_col or 'keine_mak_spalte'}"

    # F. String-Listen via groupby.apply (Reihenfolge der Zeilen erhalten)
    if "Planstelle" in work.columns:
        agg["Planstellenliste"] = g["Planstelle"].apply(_series_text).values
    else:
        agg["Planstellenliste"] = ""

    agg["MAK_je_Zeile"] = g["_audit_mak"].apply(
        lambda s: " | ".join(f"{value:.4f}" for value in s.tolist())
    ).values

    return (
        agg[_COL_ORDER]
        .sort_values(["MAK_sum", "Anzahl_Zeilen"], ascending=[False, False])
        .reset_index(drop=True)
    )


# ── Vergleichsfunktion ────────────────────────────────────────────────────────

def _compare(old: pd.DataFrame, new: pd.DataFrame, label: str) -> list[str]:
    diffs = []
    if list(old.columns) != list(new.columns):
        diffs.append(
            f"[{label}] Spaltenliste:\n  alt={list(old.columns)}\n  neu={list(new.columns)}"
        )
        return diffs
    if len(old) != len(new):
        diffs.append(f"[{label}] Zeilenanzahl: alt={len(old)}, neu={len(new)}")
        return diffs
    for col in old.columns:
        o, n = old[col], new[col]
        if pd.api.types.is_numeric_dtype(o) or pd.api.types.is_numeric_dtype(n):
            o_n = pd.to_numeric(o, errors="coerce")
            n_n = pd.to_numeric(n, errors="coerce")
            mask = (o_n != n_n) & ~(o_n.isna() & n_n.isna())
            if mask.any():
                diffs.append(
                    f"[{label}] {col}: {int(mask.sum())} num. Diff(s), "
                    f"z.B. PersNr={old.loc[mask, 'PersNr'].tolist()[:3]}"
                )
        else:
            o_s = o.fillna("__NA__").astype(str)
            n_s = n.fillna("__NA__").astype(str)
            mask = o_s != n_s
            if mask.any():
                for i in old.index[mask][:2]:
                    diffs.append(
                        f"[{label}] {col} idx={i}: alt={o.iloc[i]!r}, neu={n.iloc[i]!r}"
                    )
    return diffs


# ── Synthetische Edge-Cases ───────────────────────────────────────────────────

def _row(**kw) -> dict:
    base = {
        "PersNr": "000001",
        "Is_Vacant": False,
        "MAK_Calculated": 1.0,
        "Planstelle": "Beratung",
        "Jobfamily": "Angestellte",
        "JF-Cluster": "Angestellte",
        "Vertragsart": "Unbefristet",
        "MitarbGruppenbez.": "Beschaeftigte",
        "Total_Cost_Year": 50000.0,
    }
    base.update(kw)
    return base


def _snap(rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows).reset_index(drop=True)


EDGE_CASES = {
    "S01_single_person_1row": _snap([
        _row(PersNr="000001", MAK_Calculated=0.8),
    ]),
    "S02_multi_row_2planstellen": _snap([
        _row(PersNr="000001", MAK_Calculated=0.5, Planstelle="A"),
        _row(PersNr="000001", MAK_Calculated=0.3, Planstelle="B"),
    ]),
    "S03_mak_zero": _snap([
        _row(PersNr="000001", MAK_Calculated=0.0),
    ]),
    "S04_mak_sum_gt1_multi_planstelle": _snap([
        # MAK_sum > 1, zwei verschiedene Planstellen -> mehrfachplanstelle_fachlich_pruefen
        _row(PersNr="000001", MAK_Calculated=0.7, Planstelle="A"),
        _row(PersNr="000001", MAK_Calculated=0.6, Planstelle="B"),
    ]),
    "S05_mak_max_gt1_datenfehler": _snap([
        # MAK_max > 1 -> datenfehler_beschaeftigungsgrad
        _row(PersNr="000001", MAK_Calculated=1.2, Planstelle="A"),
    ]),
    "S06_mak_sum_gt1_same_planstelle": _snap([
        # MAK_sum > 1, gleiche Planstelle -> doppelte_planstellenzuordnung
        _row(PersNr="000001", MAK_Calculated=0.6, Planstelle="Beratung"),
        _row(PersNr="000001", MAK_Calculated=0.6, Planstelle="Beratung"),
    ]),
    "S07_mak_sum_gt1_with_exception": _snap([
        # MAK_sum > 1 mit Allow_MAK_gt_1=True -> mehrfachbeschaeftigung_dokumentiert
        _row(PersNr="000001", MAK_Calculated=0.7, Planstelle="A", Allow_MAK_gt_1=True),
        _row(PersNr="000001", MAK_Calculated=0.6, Planstelle="B", Allow_MAK_gt_1=True),
    ]),
    "S08_vacant_filtered": _snap([
        _row(PersNr="000001", MAK_Calculated=0.8, Is_Vacant=True),
        _row(PersNr="000002", MAK_Calculated=0.5, Is_Vacant=False),
    ]),
    "S09_no_planstelle_col": _snap([
        {
            "PersNr": "000001", "Is_Vacant": False,
            "MAK_Calculated": 0.8, "Jobfamily": "IT",
            "Vertragsart": "Unbefristet", "MitarbGruppenbez.": "Desc",
            "Total_Cost_Year": 40000.0,
        }
    ]),
    "S10_no_jobfamily_fallback_to_jfcluster": _snap([
        {
            "PersNr": "000001", "Is_Vacant": False, "MAK_Calculated": 0.8,
            "Jobfamily": None, "JF-Cluster": "Angestellte-Cluster",
            "Planstelle": "X", "Vertragsart": "Unbefristet",
            "MitarbGruppenbez.": "Desc", "Total_Cost_Year": 30000.0,
        }
    ]),
    "S11_two_persons_mixed": _snap([
        _row(PersNr="000001", MAK_Calculated=0.9, Planstelle="A"),
        _row(PersNr="000002", MAK_Calculated=0.5, Planstelle="B"),
        _row(PersNr="000002", MAK_Calculated=0.6, Planstelle="C"),
    ]),
    "S12_nan_vertragsart": _snap([
        _row(PersNr="000001", MAK_Calculated=0.8, Vertragsart=None),
        _row(PersNr="000001", MAK_Calculated=0.2, Vertragsart="Befristet"),
    ]),
    "S13_bsgrd_as_mak_source": _snap([
        {
            "PersNr": "000001", "Is_Vacant": False,
            "BsGrd": 80.0,  # no MAK_Calculated -> uses BsGrd/100
            "Planstelle": "X", "Jobfamily": "IT",
            "Vertragsart": "Unbefristet", "MitarbGruppenbez.": "B",
            "Total_Cost_Year": 50000.0,
        }
    ]),
}


@pytest.mark.parametrize("scenario,df", list(EDGE_CASES.items()))
def test_shadow_edge_case(scenario: str, df: pd.DataFrame):
    old = build_mak_person_audit(df)
    new = _build_mak_person_audit_shadow(df)
    diffs = _compare(old, new, scenario)
    assert not diffs, "\n".join(diffs)


# ── Echtdaten-Test ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_future_snapshot():
    """Baut future_snapshot_df aus echter Simulation."""
    if not _HAS_REAL_DATA:
        pytest.skip("Original-Daten nicht vorhanden")

    from dataloader.compact_simulation_engine import (
        _apply_attrition_events_to_employee_state,
        _apply_salary_automation_to_employee_state,
        _append_new_people_rows,
        _build_vacancies_from_attrition,
        _finalize_future_snapshot,
        _normalize_persnr_series,
        _prepare_employee_forecast_base,
        _resolve_atz_status_map,
        _update_existing_rows,
        build_jf_to_cluster_map,
        default_abgaenge_params,
        default_zugaenge_params,
    )
    from abgaenge.forecast import run_forecast_abgaenge
    from zugaenge.forecast import run_forecast_zugaenge
    from zugaenge.enrichment import enrich_zugaenge_events
    from dataloader.loader import load_original_data, normalize_atz

    BASE_DATE   = pd.Timestamp("2025-12-31")
    TARGET_DATE = pd.Timestamp("2026-12-31")

    raw = load_original_data()
    pl, ma, atz_raw = raw["planstellen"], raw["mitarbeiter"], raw["atz"]
    pl["PersNr"] = _normalize_persnr_series(pl["Personalnummer"].fillna("").astype(str))
    ma_sub = ma[["PersNr","BsGrd","TrfGr","St","GebDatum","Eintritt","Austritt",
                 "Vertragsart","MitarbGruppenbez.","Text Gsch","Status kundenindividuell"]].copy()
    snap = pl.merge(ma_sub, on="PersNr", how="left")
    snap.index = range(len(snap))
    snap["Is_Vacant"]      = snap["PersNr"].eq("")
    snap["Personalnummer"] = snap["PersNr"]
    soll_h = pd.to_numeric(snap["Sollarbeitszeit"], errors="coerce").fillna(0.0)
    snap["Soll_FTE"]       = soll_h / 39.0
    bsgrd = pd.to_numeric(snap["BsGrd"], errors="coerce").fillna(0.0)
    snap["BsGrd"]          = bsgrd
    snap["MAK_Calculated"] = bsgrd / 100.0
    snap["mak"]            = snap["MAK_Calculated"]
    snap["MAK"]            = snap["MAK_Calculated"]
    snap["FTE_person"]     = snap["MAK_Calculated"]
    snap["FTE_assigned"]   = snap["MAK_Calculated"] * snap["Soll_FTE"]
    snap["ATZ_Status"]     = "Kein ATZ"
    snap["ist_atz_fr"]     = False
    snap["Sollarbeitszeit"] = soll_h
    snap["Personen_MAK"]   = bsgrd / 100.0
    snap["Personen_MAK_Source"] = bsgrd / 100.0
    snap["BsGrd_Source"]   = bsgrd
    snap["snapshot_BsGrd"] = bsgrd
    snap["person_mak_source"] = "Personen_MAK_Source"
    snap["bsgrd_lineage_flag"] = "ok_source_bsgrd_used"
    snap["MAK_Technical_Uncorrected"] = bsgrd / 100.0
    snap["MAK_Reporting"]  = bsgrd / 100.0
    snap["Ausbildung"]     = pd.NA
    snap["Jobfamily"]      = "Angestellte"
    snap["OE-Cluster"]     = "Sonstiges"
    snap["JF-Cluster"]     = "Sonstiges"
    snap["Alter_Jahre"]    = 45.0
    df_atz = normalize_atz(atz_raw)

    cs = snap.copy()
    abg_p = default_abgaenge_params(); zug_p = default_zugaenge_params()
    emp_b = _prepare_employee_forecast_base(cs, df_atz, BASE_DATE)
    abg_r = run_forecast_abgaenge(df_ma=emp_b, df_atz=df_atz, start_date=BASE_DATE,
                                   end_date=TARGET_DATE, freq="M", params=abg_p)
    emp_a = _apply_attrition_events_to_employee_state(
        emp_b, abg_r.get("events_person_level", pd.DataFrame()))
    vac   = _build_vacancies_from_attrition(emp_b, abg_r)
    rp    = {**zug_p}; rp.setdefault("azubi", {})
    rp["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(cs)
    zug_r = run_forecast_zugaenge(df_snapshot=emp_a, start_date=BASE_DATE,
                                   end_date=TARGET_DATE, freq="M", params=rp, vacancies=vac)
    ze    = zug_r.get("events", pd.DataFrame())
    if not ze.empty: zug_r["events"] = enrich_zugaenge_events(ze, cs, rp)
    final = zug_r.get("final_state", emp_a).copy()
    if "PersNr" in final.columns:
        final["PersNr"] = _normalize_persnr_series(final["PersNr"])
    final = _apply_salary_automation_to_employee_state(
        final, target_date=TARGET_DATE, events_df=zug_r.get("events", pd.DataFrame()))
    ap    = abg_r.get("tables", {}).get("atz_pivot", pd.DataFrame())
    ats   = _resolve_atz_status_map(ap, TARGET_DATE)
    su    = _update_existing_rows(cs, final, TARGET_DATE, ats)
    sa    = _append_new_people_rows(su.copy(), cs, final, zug_r)
    sf    = _finalize_future_snapshot(sa.copy(), TARGET_DATE)
    return sf


def test_shadow_realdata(real_future_snapshot, capsys):
    df = real_future_snapshot

    t0 = time.perf_counter()
    old = build_mak_person_audit(df)
    t_old = time.perf_counter() - t0

    t1 = time.perf_counter()
    new = _build_mak_person_audit_shadow(df)
    t_new = time.perf_counter() - t1

    with capsys.disabled():
        print(f"\n--- Timing build_mak_person_audit ---")
        print(f"  Alt (Loop):   {t_old:.4f}s")
        print(f"  Shadow:       {t_new:.4f}s")
        print(f"  Speedup:      {t_old/t_new:.1f}x")
        print(f"  Personen:     {len(old)}")
        print(f"  Multi-Row:    {(old['Anzahl_Zeilen']>1).sum()}")
        print(f"  Auffaelligkeit unique: {sorted(old['Auffaelligkeit'].unique())}")

    diffs = _compare(old, new, "Echtdaten")
    assert not diffs, "\n".join(diffs)


# ── Downstream-Test: build_mak_decision_list ─────────────────────────────────

def test_shadow_downstream_decision_list(real_future_snapshot):
    """Sicherstellen dass build_mak_decision_list mit Shadow-Output identisch laeuft."""
    from utils.audit_helpers import build_mak_decision_list
    df = real_future_snapshot
    old_audit = build_mak_person_audit(df)
    new_audit  = _build_mak_person_audit_shadow(df)

    old_decision = build_mak_decision_list(old_audit)
    new_decision  = build_mak_decision_list(new_audit)

    diffs = _compare(old_decision, new_decision, "MAK_Decision_List")
    assert not diffs, "\n".join(diffs)


if __name__ == "__main__":
    print("Ausfuehren mit: py -m pytest tests/test_shadow_mak_person_audit_10a.py -v -s")
