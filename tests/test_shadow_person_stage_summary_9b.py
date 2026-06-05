"""
Shadow-Test Hotspot 9b: _person_stage_summary (utils/audit_helpers.py)

Vergleicht die bestehende Python-Loop-Implementierung gegen eine
teilvektorisierte Shadow-Variante auf:
  - 7 echten Lineage-Stages aus Produktionsdaten
  - 11 synthetischen Edge-Case-Szenarien

Kein Produktivcode veraendert.

Ausfuehren:
  py -m pytest tests/test_shadow_person_stage_summary_9b.py -v -s -q
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
    _event_lookup,
    _normalize_persnr_for_audit,
    _numeric,
    _person_stage_summary,
    _series_text,
    select_mak_column,
)

_ORIG = Path(__file__).resolve().parents[2] / "Original-Daten"
_HAS_REAL_DATA = (
    (_ORIG / "Planstellen.XLSX").exists() and (_ORIG / "Mitarbeiter.xlsx").exists()
)


# ============================================================
# Exakte Dokumentation _person_stage_summary
# ============================================================
#
# Inputs:
#   df             - DataFrame mit einer Zeile pro Position/Row
#   stage          - Stufenname (Konstante im Output)
#   source_dataframe - Quellname (Konstante im Output)
#   base_ids       - set[str] normalisierter PersNr aus Basisbestand
#   abgang_events  - DataFrame (persnr, type/reason_code)
#   zugang_events  - DataFrame (persnr, type)
#
# Filterung vor Aggregation:
#   - active != True wird gefiltert (falls Spalte vorhanden)
#   - Is_Vacant == True wird gefiltert (falls Spalte vorhanden)
#   - PersNr isna() wird gefiltert
#   - PersNr wird normalisiert (_normalize_persnr_for_audit)
#   - Planstelle <- Job falls Planstelle fehlt (und umgekehrt)
#
# Output: 1 Zeile pro Person, 26 Spalten, sortiert nach PersNr
#
# Spalten und ihre Aggregationslogik:
#
# Spalte                    Quelle/Logik
# ─────────────────────────────────────────────────────────────
# processing_stage          Konstante = stage
# PersNr                    Normalisierter Gruppierungsschluessel
# is_existing_employee      pid in base_ids
# is_forecast_employee      pid.startswith(AZ_/TR_/NH_) OR _first_value(is_forecast, False)
# is_new_hire               pid.startswith(NH_/TR_/AZ_)
# is_azubi                  bool(_first_value(Ist_Azubi, False)) OR "azubi" in _series_text(Planstelle).lower()
# is_azubi_takeover         pid.startswith(AZ_) AND sum(mak) > 0
# has_abgang_event          bool(abg_lookup.get(pid, ""))
# has_zugang_event          bool(zug_lookup.get(pid, ""))
# event_types               " | ".join([abg_types, zug_types] if truthy)
# Jobfamily                 _first_value(Jobfamily, "")  = first non-NaN, else ""
# Planstelle                _series_text(Planstelle)  = " | ".join(unique non-NaN stripped, no "nan")
# Job                       _series_text(Job)
# Rollenliste               _series_text(Planstelle)  (== Planstelle, IDENTISCH)
# Anzahl_Zeilen             int(len(rows_df))  = Zeilenanzahl dieser Person
# Anzahl_Planstellen        Planstelle.dropna().astype(str).str.strip().nunique()
# MAK_column_used           mak_col or ""  (Konstante pro Stage-Aufruf)
# MAK_je_Zeile              " | ".join(f"{float(v):.4f}" for v in _mak_values)  [Row-Reihenfolge]
# MAK_sum                   float(sum(_mak_values))
# MAK_max                   float(max(_mak_values)) or 0.0
# MAK_min                   float(min(_mak_values)) or 0.0
# MAK_person_policy_current "sum_over_rows"  (Konstante)
# EUR_sum                   float(sum(_eur_values))
# Vertragsart               _first_value(Vertragsart, "")
# MitarbGruppenbez.         _first_value(MitarbGruppenbez., "")
# source_dataframe          Konstante = source_dataframe
#
# NaN/None/Leerstring:
#   - _first_value: ignoriert NaN, gibt fallback wenn alle NaN
#   - _series_text: ignoriert NaN und "nan"-Strings, gibt "" wenn leer
#   - Anzahl_Planstellen: zaehlt nur non-NaN stripped values
#   - MAK: numeric-Konvertierung mit fillna(0.0)
#
# Sortierung Output: sortiert nach PersNr (groupby sort=True Default)
# ============================================================


# ── Shadow-Implementierung ────────────────────────────────────────────────────

_COL_ORDER = [
    "processing_stage", "PersNr", "is_existing_employee", "is_forecast_employee",
    "is_new_hire", "is_azubi", "is_azubi_takeover",
    "has_abgang_event", "has_zugang_event", "event_types",
    "Jobfamily", "Planstelle", "Job", "Rollenliste",
    "Anzahl_Zeilen", "Anzahl_Planstellen", "MAK_column_used",
    "MAK_je_Zeile", "MAK_sum", "MAK_max", "MAK_min",
    "MAK_person_policy_current", "EUR_sum", "Vertragsart", "MitarbGruppenbez.",
    "source_dataframe",
]


def _series_text_agg(s: pd.Series) -> str:
    """Exact equivalent of _series_text for groupby.apply."""
    values = [
        str(v).strip()
        for v in s.dropna().unique().tolist()
        if str(v).strip() and str(v).strip().lower() != "nan"
    ]
    return " | ".join(values) if values else ""


def _person_stage_summary_shadow(
    df: pd.DataFrame,
    stage: str,
    source_dataframe: str,
    base_ids: set,
    abg_lookup: dict,
    zug_lookup: dict,
) -> pd.DataFrame:
    """
    Shadow: teilweise vektorisierte Entsprechung von _person_stage_summary.

    Aenderungen gegenueber Original:
    A. Rollenliste = Planstelle-Text (einmal berechnet, nicht doppelt)
    B. Anzahl_Planstellen per groupby.nunique (kein Python-Loop)
    C. MAK_sum/max/min/EUR_sum per groupby.agg (kein Python-Loop)
    D. first-value-Felder (Jobfamily, Vertragsart, MitarbGruppenbez.,
       is_forecast, Ist_Azubi) per groupby.first() statt _first_value-Loop
    E. Flags (is_existing, is_new_hire, etc.) per Series-Operationen
    F. abg_lookup/zug_lookup kommen von aussen (pre-computed, nicht 14x rebuilt)
    G. event_types vektorisiert (kein apply)
    H. Planstelle-Text und MAK_je_Zeile bleiben groupby.apply (Reihenfolge!)
    """
    if df is None or df.empty or "PersNr" not in df.columns:
        return pd.DataFrame()

    work = df.copy().reset_index(drop=True)
    if "active" in work.columns:
        work = work[work["active"] == True].copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    work = work[work["PersNr"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    work["PersNr"] = _normalize_persnr_for_audit(work["PersNr"])

    # MAK-Wert ermitteln (identisch mit Original)
    mak_col = select_mak_column(work)
    if mak_col == "BsGrd":
        work["_mak"] = _numeric(work[mak_col]) / 100.0
    elif mak_col:
        work["_mak"] = _numeric(work[mak_col])
    else:
        work["_mak"] = 0.0

    work["_eur"] = (
        _numeric(work["Total_Cost_Year"]) if "Total_Cost_Year" in work.columns else 0.0
    )

    # Planstelle/Job-Fallback (identisch mit Original)
    if "Planstelle" not in work.columns and "Job" in work.columns:
        work["Planstelle"] = work["Job"]
    if "Job" not in work.columns and "Planstelle" in work.columns:
        work["Job"] = work["Planstelle"]

    # Vorberechnung: stripped Planstelle fuer Anzahl_Planstellen
    if "Planstelle" in work.columns:
        notna_pl = work["Planstelle"].notna()
        work["_pl_s"] = pd.NA
        if notna_pl.any():
            work.loc[notna_pl, "_pl_s"] = (
                work.loc[notna_pl, "Planstelle"].astype(str).str.strip()
            )
    else:
        work["_pl_s"] = pd.NA

    # ── Groupby (sortiert, identisch mit Original sort=True) ──────────────────
    g = work.groupby("PersNr", sort=True, dropna=False)

    # C. Numerische Aggregationen (ersetzt Loop-Berechnungen)
    agg = g.agg(
        MAK_sum=("_mak", "sum"),
        MAK_max=("_mak", "max"),
        MAK_min=("_mak", "min"),
        EUR_sum=("_eur", "sum"),
        Anzahl_Zeilen=("_mak", "count"),   # _mak hat kein NaN -> count == size
    ).reset_index()

    # B. Anzahl_Planstellen per nunique (kein Python-Loop)
    agg["Anzahl_Planstellen"] = g["_pl_s"].nunique(dropna=True).values

    # D. First-value-Felder (ersetzt _first_value-Aufrufe im Loop)
    for col, fallback in [
        ("Jobfamily", ""),
        ("Vertragsart", ""),
        ("MitarbGruppenbez.", ""),
    ]:
        if col in work.columns:
            agg[col] = g[col].first().fillna(fallback).values
        else:
            agg[col] = fallback

    # is_forecast: first non-NaN, fallback False
    if "is_forecast" in work.columns:
        agg["_is_fc_first"] = g["is_forecast"].first().fillna(False).astype(bool).values
    else:
        agg["_is_fc_first"] = False

    # Ist_Azubi: first non-NaN, fallback False
    if "Ist_Azubi" in work.columns:
        agg["_ist_az_first"] = g["Ist_Azubi"].first().fillna(False).astype(bool).values
    else:
        agg["_ist_az_first"] = False

    # E. Flags per Series-Operationen (kein Python-Loop)
    pnr_str = agg["PersNr"].astype(str)
    agg["is_existing_employee"] = agg["PersNr"].isin(base_ids)
    agg["is_new_hire"] = pnr_str.str.startswith(("NH_", "TR_", "AZ_"))
    _starts_az_tr_nh = pnr_str.str.startswith(("AZ_", "TR_", "NH_"))
    agg["is_forecast_employee"] = _starts_az_tr_nh | agg["_is_fc_first"]
    agg["is_azubi_takeover"] = pnr_str.str.startswith("AZ_") & (agg["MAK_sum"] > 0)

    # F. Event-Lookups (pre-computed, keine Neuberechnung pro Stage)
    agg["_abg"] = agg["PersNr"].map(lambda p: abg_lookup.get(p, ""))
    agg["_zug"] = agg["PersNr"].map(lambda p: zug_lookup.get(p, ""))
    agg["has_abgang_event"] = agg["_abg"].astype(bool)
    agg["has_zugang_event"] = agg["_zug"].astype(bool)

    # G. event_types vektorisiert (kein apply)
    _abg_mask = agg["_abg"].astype(bool)
    _zug_mask = agg["_zug"].astype(bool)
    agg["event_types"] = ""
    agg.loc[_abg_mask & ~_zug_mask, "event_types"] = agg.loc[_abg_mask & ~_zug_mask, "_abg"]
    agg.loc[~_abg_mask & _zug_mask, "event_types"] = agg.loc[~_abg_mask & _zug_mask, "_zug"]
    agg.loc[_abg_mask & _zug_mask, "event_types"] = (
        agg.loc[_abg_mask & _zug_mask, "_abg"]
        + " | "
        + agg.loc[_abg_mask & _zug_mask, "_zug"]
    )

    # H. String-Listen per groupby.apply (Reihenfolge beachten!)
    if "Planstelle" in work.columns:
        pl_text = g["Planstelle"].apply(_series_text_agg)
        agg["Planstelle"] = pl_text.values
        agg["Rollenliste"] = pl_text.values          # A. Identisch, einmal berechnet
    else:
        agg["Planstelle"] = ""
        agg["Rollenliste"] = ""

    if "Job" in work.columns:
        agg["Job"] = g["Job"].apply(_series_text_agg).values
    else:
        agg["Job"] = ""

    # is_azubi: _first_value(Ist_Azubi) OR "azubi" in Planstelle-Text
    agg["is_azubi"] = agg["_ist_az_first"] | (
        agg["Planstelle"].str.lower().str.contains("azubi", na=False)
    )

    # MAK_je_Zeile: Reihenfolge aus DataFrame (groupby.apply erhalt Reihenfolge)
    agg["MAK_je_Zeile"] = g["_mak"].apply(
        lambda s: " | ".join(f"{float(v):.4f}" for v in s.tolist())
    ).values

    # Konstanten
    agg["processing_stage"] = stage
    agg["MAK_column_used"] = mak_col or ""
    agg["MAK_person_policy_current"] = "sum_over_rows"
    agg["source_dataframe"] = source_dataframe

    return agg[_COL_ORDER].reset_index(drop=True)


# ── Vergleichsfunktion ────────────────────────────────────────────────────────

def _compare(old: pd.DataFrame, new: pd.DataFrame, label: str) -> list[str]:
    """
    Vollstaendiger Zellwertvergleich. Gibt Liste der Abweichungen zurueck.
    Leere Liste = identisch.
    """
    diffs = []

    if list(old.columns) != list(new.columns):
        diffs.append(f"[{label}] Spaltenliste verschieden:\n  alt={list(old.columns)}\n  neu={list(new.columns)}")
        return diffs

    if len(old) != len(new):
        diffs.append(f"[{label}] Zeilenanzahl: alt={len(old)}, neu={len(new)}")
        return diffs

    if list(old.index) != list(new.index):
        diffs.append(f"[{label}] Index verschieden")

    for col in old.columns:
        o = old[col]
        n = new[col]
        # Numerische Spalten: exakter Vergleich (float == float)
        if pd.api.types.is_numeric_dtype(o) or pd.api.types.is_numeric_dtype(n):
            o_num = pd.to_numeric(o, errors="coerce")
            n_num = pd.to_numeric(n, errors="coerce")
            diff_mask = (o_num != n_num) & ~(o_num.isna() & n_num.isna())
            if diff_mask.any():
                n_diff = int(diff_mask.sum())
                ex = old.loc[diff_mask, "PersNr"].tolist()[:3]
                diffs.append(f"[{label}] {col}: {n_diff} num. Abweichungen, z.B. PersNr={ex}")
        else:
            # String/Bool-Spalten: str-Vergleich
            o_s = o.fillna("__NA__").astype(str)
            n_s = n.fillna("__NA__").astype(str)
            diff_mask = o_s != n_s
            if diff_mask.any():
                n_diff = int(diff_mask.sum())
                ex_idx = old.index[diff_mask][:2]
                for i in ex_idx:
                    diffs.append(
                        f"[{label}] {col} row {i}: alt={repr(o.iloc[i])!r}, neu={repr(n.iloc[i])!r}"
                    )

    return diffs


# ── Synthetische Edge-Cases ───────────────────────────────────────────────────

def _make_row(**kw) -> dict:
    base = {
        "PersNr": "000001",
        "Is_Vacant": False,
        "MAK_Calculated": 1.0,
        "Planstelle": "Beratung",
        "Job": "Beratung",
        "Jobfamily": "Angestellte",
        "Vertragsart": "Unbefristet",
        "MitarbGruppenbez.": "Beschaeftigte",
        "Total_Cost_Year": 50000.0,
        "Ist_Azubi": False,
        "is_forecast": False,
    }
    base.update(kw)
    return base


def _snap(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).reset_index(drop=True)


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame()


_BASE_IDS = {"000001", "000002", "000003"}


EDGE_CASES = {
    "S01_single_person_1row": _snap([
        _make_row(PersNr="000001", MAK_Calculated=0.8, Planstelle="Beratung"),
    ]),
    "S02_multi_row_2planstellen": _snap([
        _make_row(PersNr="000001", MAK_Calculated=0.5, Planstelle="Beratung"),
        _make_row(PersNr="000001", MAK_Calculated=0.3, Planstelle="Vertrieb"),
    ]),
    "S03_planstelle_all_nan": _snap([
        _make_row(PersNr="000001", MAK_Calculated=0.8, Planstelle=None, Job=None),
    ]),
    "S04_nan_jobfamily": _snap([
        _make_row(PersNr="000001", MAK_Calculated=0.8, Jobfamily=None),
        _make_row(PersNr="000001", MAK_Calculated=0.2, Jobfamily="IT"),
    ]),
    "S05_azubi_in_planstelle": _snap([
        _make_row(PersNr="000002", MAK_Calculated=0.5, Planstelle="Ausbildung Azubi"),
    ]),
    "S06_ist_azubi_true": _snap([
        _make_row(PersNr="000003", MAK_Calculated=0.5, Ist_Azubi=True, Planstelle="Sonstiges"),
    ]),
    "S07_forecast_prefix_AZ": _snap([
        _make_row(PersNr="AZ_12345", MAK_Calculated=0.5, Planstelle="Azubi"),
    ]),
    "S08_forecast_prefix_NH": _snap([
        _make_row(PersNr="NH_67890", MAK_Calculated=1.0, Planstelle="Beratung"),
    ]),
    "S09_mak_zero": _snap([
        _make_row(PersNr="000001", MAK_Calculated=0.0),
    ]),
    "S10_3_unique_planstellen": _snap([
        _make_row(PersNr="000001", MAK_Calculated=0.3, Planstelle="A"),
        _make_row(PersNr="000001", MAK_Calculated=0.3, Planstelle="B"),
        _make_row(PersNr="000001", MAK_Calculated=0.2, Planstelle="C"),
    ]),
    "S11_two_persons_mixed": _snap([
        _make_row(PersNr="000001", MAK_Calculated=0.8, Planstelle="Beratung"),
        _make_row(PersNr="000002", MAK_Calculated=0.4, Planstelle="IT"),
        _make_row(PersNr="000002", MAK_Calculated=0.4, Planstelle="IT"),
    ]),
}

_ABG_EVENTS = pd.DataFrame([
    {"persnr": "000002", "type": "Rente"},
    {"persnr": "AZ_12345", "type": "Kündigung"},
])
_ZUG_EVENTS = pd.DataFrame([
    {"persnr": "NH_67890", "type": "Neueinstellung"},
    {"persnr": "000003", "type": "Trainee"},
])


# ── Tests: Synthetische Edge-Cases ───────────────────────────────────────────


def _run_both(df: pd.DataFrame, label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    abg_lk = _event_lookup(_ABG_EVENTS, "persnr")
    zug_lk = _event_lookup(_ZUG_EVENTS, "persnr")

    old = _person_stage_summary(df, label, label, _BASE_IDS, _ABG_EVENTS, _ZUG_EVENTS)
    new = _person_stage_summary_shadow(df, label, label, _BASE_IDS, abg_lk, zug_lk)
    return old, new


@pytest.mark.parametrize("scenario,df", list(EDGE_CASES.items()))
def test_shadow_edge_case(scenario: str, df: pd.DataFrame):
    old, new = _run_both(df, scenario)
    diffs = _compare(old, new, scenario)
    assert not diffs, "\n".join(diffs)


# ── Tests: Realdaten ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_lineage_stages():
    """Baut echte Lineage-Stages aus Original-Daten."""
    pytest.importorskip("dataloader.compact_simulation_engine",
                        reason="compact_simulation_engine nicht verfuegbar")
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
    ma_sub = ma[["PersNr", "BsGrd", "TrfGr", "St", "GebDatum", "Eintritt", "Austritt",
                 "Vertragsart", "MitarbGruppenbez.", "Text Gsch", "Status kundenindividuell"]].copy()
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
    abg_params = default_abgaenge_params()
    zug_params  = default_zugaenge_params()
    emp_base = _prepare_employee_forecast_base(cs, df_atz, BASE_DATE)
    abg_res  = run_forecast_abgaenge(df_ma=emp_base, df_atz=df_atz,
                                      start_date=BASE_DATE, end_date=TARGET_DATE,
                                      freq="M", params=abg_params)
    emp_abg  = _apply_attrition_events_to_employee_state(
        emp_base, abg_res.get("events_person_level", pd.DataFrame()))
    vac      = _build_vacancies_from_attrition(emp_base, abg_res)
    rp_zug   = {**zug_params}
    rp_zug.setdefault("azubi", {})
    rp_zug["azubi"]["jf_to_cluster_map"] = build_jf_to_cluster_map(cs)
    zug_res  = run_forecast_zugaenge(df_snapshot=emp_abg, start_date=BASE_DATE,
                                      end_date=TARGET_DATE, freq="M",
                                      params=rp_zug, vacancies=vac)
    zug_ev = zug_res.get("events", pd.DataFrame())
    if not zug_ev.empty:
        zug_res["events"] = enrich_zugaenge_events(zug_ev, cs, rp_zug)
    final_emp = zug_res.get("final_state", emp_abg).copy()
    if "PersNr" in final_emp.columns:
        final_emp["PersNr"] = _normalize_persnr_series(final_emp["PersNr"])
    final_emp = _apply_salary_automation_to_employee_state(
        final_emp, target_date=TARGET_DATE,
        events_df=zug_res.get("events", pd.DataFrame()))
    atz_piv    = abg_res.get("tables", {}).get("atz_pivot", pd.DataFrame())
    atz_status = _resolve_atz_status_map(atz_piv, TARGET_DATE)
    snap_upd   = _update_existing_rows(cs, final_emp, TARGET_DATE, atz_status)
    snap_app   = _append_new_people_rows(snap_upd.copy(), cs, final_emp, zug_res)
    snap_fin   = _finalize_future_snapshot(snap_app.copy(), TARGET_DATE)

    return {
        "stages": {
            "01_rohdaten_ausgangsbestand":     cs,
            "02_forecast_base":                emp_base,
            "03_nach_abgaenge":                emp_abg,
            "04_nach_zugaenge":                final_emp,
            "05_nach_update_existing_rows":    snap_upd,
            "06_nach_append_new_people_rows":  snap_app,
            "07_final_future_snapshot":        snap_fin,
        },
        "abg_events": abg_res.get("events_person_level", pd.DataFrame()),
        "zug_events": zug_res.get("events", pd.DataFrame()),
        "base_ids": set(_normalize_persnr_for_audit(cs["PersNr"].dropna())),
    }


@pytest.mark.parametrize("stage_key", [
    "01_rohdaten_ausgangsbestand",
    "02_forecast_base",
    "03_nach_abgaenge",
    "04_nach_zugaenge",
    "05_nach_update_existing_rows",
    "06_nach_append_new_people_rows",
    "07_final_future_snapshot",
])
def test_shadow_real_stage(stage_key: str, real_lineage_stages):
    data = real_lineage_stages
    df        = data["stages"][stage_key]
    base_ids  = data["base_ids"]
    abg_ev    = data["abg_events"]
    zug_ev    = data["zug_events"]

    abg_lk = _event_lookup(abg_ev, "persnr")
    zug_lk = _event_lookup(zug_ev, "persnr")

    old = _person_stage_summary(df, stage_key, stage_key, base_ids, abg_ev, zug_ev)
    new = _person_stage_summary_shadow(df, stage_key, stage_key, base_ids, abg_lk, zug_lk)

    diffs = _compare(old, new, stage_key)
    assert not diffs, "\n".join(diffs)


# ── Performance-Messung (separates Modul, kein pytest) ───────────────────────

def _benchmark(real_lineage_stages: dict) -> None:
    data      = real_lineage_stages
    base_ids  = data["base_ids"]
    abg_ev    = data["abg_events"]
    zug_ev    = data["zug_events"]
    abg_lk    = _event_lookup(abg_ev, "persnr")
    zug_lk    = _event_lookup(zug_ev, "persnr")

    print("\n--- Timing: alt vs. Shadow pro Stage ---")
    print(f"  {'Stage':<40} {'alt (s)':>8}  {'shadow (s)':>10}  {'Speedup':>8}  {'Output-Zeilen':>14}")
    print("-" * 90)

    t_old_total = 0.0
    t_new_total = 0.0

    for stage_key, df in data["stages"].items():
        t0 = time.perf_counter()
        old = _person_stage_summary(df, stage_key, stage_key, base_ids, abg_ev, zug_ev)
        t_old = time.perf_counter() - t0

        t1 = time.perf_counter()
        new = _person_stage_summary_shadow(df, stage_key, stage_key, base_ids, abg_lk, zug_lk)
        t_new = time.perf_counter() - t1

        t_old_total += t_old
        t_new_total += t_new
        speedup = t_old / t_new if t_new > 0 else float("inf")
        n_out = len(old)
        print(f"  {stage_key:<40} {t_old:>8.4f}  {t_new:>10.4f}  {speedup:>7.1f}x  {n_out:>14}")

    # event_lookup Redundanz
    t0 = time.perf_counter()
    for _ in range(7):
        _event_lookup(abg_ev, "persnr")
        _event_lookup(zug_ev, "persnr")
    t_14lk = time.perf_counter() - t0

    t0 = time.perf_counter()
    _event_lookup(abg_ev, "persnr")
    _event_lookup(zug_ev, "persnr")
    t_2lk = time.perf_counter() - t0

    print("-" * 90)
    print(f"  {'GESAMT':.<40} {t_old_total:>8.4f}  {t_new_total:>10.4f}  {t_old_total/t_new_total:>7.1f}x")
    print(f"\n  _event_lookup 14x: {t_14lk:.4f}s  |  2x: {t_2lk:.4f}s  |  Einsparung: {t_14lk-t_2lk:.4f}s")
    print(f"  Gesamteinsparung geschaetzt: {t_old_total - t_new_total:.2f}s")
    print(f"  (build_mak_lineage_audit alt ~18.83s vs shadow ~{t_new_total:.1f}s)")


if __name__ == "__main__":
    print("Ausfuehren mit: py -m pytest tests/test_shadow_person_stage_summary_9b.py -v -s")
