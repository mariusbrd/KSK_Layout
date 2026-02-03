"""
Forecast engine for Abgaenge.
"""

from __future__ import annotations

from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schemas import (
    COL_PERSNR,
    COL_GEB,
    COL_EINTRITT,
    COL_AUSTRITT,
    COL_BSGRD,
    COL_STATUS,
    COL_ATZ_PHASE,
    COL_ATZ_BEGINN,
    COL_ATZ_ENDE,
    COL_ATZ_VERTRAG_ENDE,
    REASON_ATZ_AR_TO_FR,
    REASON_ATZ_END,
    REASON_RETIREMENT,
    REASON_QUIT,
    REASON_RUHEND_START,
    REASON_RUHEND_RETURN,
    REASON_LABELS,
)


@dataclass
class PeriodInfo:
    start: pd.Timestamp
    end: pd.Timestamp
    label: str


def _annual_to_period_rate(annual_rate: float, period_days: float) -> float:
    annual_rate = max(0.0, float(annual_rate))
    if annual_rate >= 1.0:
        return 1.0
    return 1.0 - (1.0 - annual_rate) ** (period_days / 365.25)


def _periods(start_date: pd.Timestamp, end_date: pd.Timestamp, freq: str) -> List[PeriodInfo]:
    if freq not in {"M", "Q"}:
        raise ValueError("freq muss 'M' (Monat) oder 'Q' (Quartal) sein.")

    period_range = pd.period_range(start=start_date, end=end_date, freq=freq)
    periods: List[PeriodInfo] = []

    for p in period_range:
        start = p.start_time
        end = p.end_time
        if freq == "M":
            label = p.strftime("%Y-%m")
        else:
            label = f"{p.year}-Q{p.quarter}"
        periods.append(PeriodInfo(start=start, end=end, label=label))

    return periods


def _calc_age(gbd: pd.Series, ref_date: pd.Timestamp) -> pd.Series:
    return (ref_date - pd.to_datetime(gbd, errors="coerce")).dt.days / 365.25


def _calc_tenure(eintritt: pd.Series, ref_date: pd.Timestamp) -> pd.Series:
    return (ref_date - pd.to_datetime(eintritt, errors="coerce")).dt.days / 365.25


def _build_atz_pivot(df_atz: pd.DataFrame) -> pd.DataFrame:
    if df_atz.empty:
        return pd.DataFrame(columns=[
            COL_PERSNR, "ar_begin", "ar_end", "fr_begin", "fr_end", "contract_end"
        ])

    df = df_atz.copy()

    ar = df[df[COL_ATZ_PHASE] == "AR"][[COL_PERSNR, COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE]].copy()
    fr = df[df[COL_ATZ_PHASE] == "FR"][[COL_PERSNR, COL_ATZ_BEGINN, COL_ATZ_ENDE, COL_ATZ_VERTRAG_ENDE]].copy()

    ar = ar.rename(columns={COL_ATZ_BEGINN: "ar_begin", COL_ATZ_ENDE: "ar_end"})
    fr = fr.rename(columns={COL_ATZ_BEGINN: "fr_begin", COL_ATZ_ENDE: "fr_end"})

    merged = pd.merge(ar, fr, on=COL_PERSNR, how="outer", suffixes=("", "_fr"))

    if COL_ATZ_VERTRAG_ENDE in merged.columns:
        merged["contract_end"] = merged[COL_ATZ_VERTRAG_ENDE]
    elif f"{COL_ATZ_VERTRAG_ENDE}_fr" in merged.columns:
        merged["contract_end"] = merged[f"{COL_ATZ_VERTRAG_ENDE}_fr"]
    else:
        merged["contract_end"] = merged["fr_end"]

    return merged[[COL_PERSNR, "ar_begin", "ar_end", "fr_begin", "fr_end", "contract_end"]]


def _get_atz_events_from_schedule(
    atz_pivot: pd.DataFrame, period_start: pd.Timestamp, period_end: pd.Timestamp
) -> Tuple[List[str], List[str]]:
    if atz_pivot.empty:
        return [], []

    ar_to_fr = atz_pivot[
        (atz_pivot["fr_begin"] >= period_start) & (atz_pivot["fr_begin"] <= period_end)
    ][COL_PERSNR].dropna().astype(str).unique().tolist()

    atz_end = atz_pivot[
        (atz_pivot["contract_end"] >= period_start) & (atz_pivot["contract_end"] <= period_end)
    ][COL_PERSNR].dropna().astype(str).unique().tolist()

    return ar_to_fr, atz_end


def _select_quit_prob(age: float, tenure: float, params: Dict[str, Any]) -> float:
    quit_params = params.get("quit", {})
    base = float(quit_params.get("quit_rate_base", 0.05))
    if not quit_params.get("use_quit_matrix", True):
        return base

    matrix = quit_params.get("quit_rate_matrix", {})

    if age < 30:
        age_key = "alter_unter_30"
    elif age < 45:
        age_key = "alter_30_45"
    else:
        age_key = "alter_ueber_45"

    if tenure < 2:
        ten_key = "tenure_unter_2"
    elif tenure < 5:
        ten_key = "tenure_2_5"
    else:
        ten_key = "tenure_ueber_5"

    return float(matrix.get(age_key, {}).get(ten_key, base))


def _schedule_new_atz_cases(
    df_state: pd.DataFrame,
    atz_pivot: pd.DataFrame,
    params: Dict[str, Any],
    period: PeriodInfo,
    rng: np.random.RandomState,
) -> pd.DataFrame:
    atz_params = params.get("atz", {})
    expected = float(atz_params.get("new_atz_cases_per_year", 0)) * ((period.end - period.start).days / 365.25)
    if expected <= 0:
        return atz_pivot

    count = int(rng.poisson(lam=expected))
    if count <= 0:
        return atz_pivot

    eligible_age_min = int(atz_params.get("atz_eligible_age_min", 55))
    ar_months = int(round(float(atz_params.get("atz_duration_ar_years", 2.5)) * 12))
    fr_months = int(round(float(atz_params.get("atz_duration_fr_years", 2.5)) * 12))

    eligible = df_state[
        (df_state["active"] == True) &
        (~df_state["in_atz"]) &
        (~df_state["status_ruhend"]) &
        (df_state["age"] >= eligible_age_min)
    ]

    if eligible.empty:
        return atz_pivot

    chosen = eligible.sample(n=min(count, len(eligible)), random_state=rng).index.tolist()

    new_rows = []
    for persnr in chosen:
        offset_days = rng.randint(0, max(1, (period.end - period.start).days + 1))
        ar_start = period.start + pd.Timedelta(days=int(offset_days))
        ar_end = ar_start + pd.DateOffset(months=ar_months)
        fr_start = ar_end + pd.Timedelta(days=1)
        fr_end = fr_start + pd.DateOffset(months=fr_months)
        contract_end = fr_end
        new_rows.append({
            COL_PERSNR: persnr,
            "ar_begin": ar_start,
            "ar_end": ar_end,
            "fr_begin": fr_start,
            "fr_end": fr_end,
            "contract_end": contract_end,
        })

    if new_rows:
        atz_pivot = pd.concat([atz_pivot, pd.DataFrame(new_rows)], ignore_index=True)

    return atz_pivot


def run_forecast_abgaenge(
    df_ma: pd.DataFrame,
    df_atz: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    freq: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run Abgaenge forecast.

    Returns:
        dict with keys: forecast_kpis, events_person_level, assumptions, tables
    """
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    if end_date <= start_date:
        raise ValueError("forecast_end_date muss nach ist_stichtag liegen.")

    # Validate required columns
    required_cols = [COL_PERSNR, COL_GEB, COL_EINTRITT, COL_BSGRD, COL_STATUS]
    missing = [c for c in required_cols if c not in df_ma.columns]
    if missing:
        raise ValueError(f"Mitarbeiter.xlsx fehlt Spalten: {', '.join(missing)}")

    rng = np.random.RandomState(int(params.get("random_seed", 42)))

    # Build baseline state
    df_state = df_ma.copy()
    df_state = df_state.set_index(COL_PERSNR, drop=True)
    df_state["age"] = _calc_age(df_state[COL_GEB], start_date)
    df_state["tenure"] = _calc_tenure(df_state[COL_EINTRITT], start_date)
    df_state["status_ruhend"] = df_state[COL_STATUS] == "Ruhendes Beschäftigungsverhältnis"

    # ATZ current FR at start
    atz_fr_active = set()
    if not df_atz.empty and COL_ATZ_PHASE in df_atz.columns:
        fr_rows = df_atz[df_atz[COL_ATZ_PHASE] == "FR"]
        fr_active = fr_rows[
            (fr_rows[COL_ATZ_BEGINN] <= start_date) & (fr_rows[COL_ATZ_ENDE] >= start_date)
        ]
        atz_fr_active = set(fr_active[COL_PERSNR].dropna().astype(str))

    df_state["in_atz"] = df_state.index.isin(df_atz[COL_PERSNR].dropna().astype(str).unique()) if not df_atz.empty else False
    df_state["atz_fr_active"] = df_state.index.isin(atz_fr_active)
    df_state["active"] = True

    bsgrd = pd.to_numeric(df_state[COL_BSGRD], errors="coerce").fillna(0.0)
    df_state["mak"] = np.where(
        (df_state["status_ruhend"] | df_state["atz_fr_active"]),
        0.0,
        bsgrd / 100.0,
    )

    # Ruhend return schedule
    ruhend_months = int(params.get("ruhend", {}).get("ruhend_avg_duration_months", 12))
    df_state["ruhend_until"] = pd.NaT
    df_state.loc[df_state["status_ruhend"], "ruhend_until"] = start_date + pd.DateOffset(months=ruhend_months)

    atz_pivot = _build_atz_pivot(df_atz)

    periods = _periods(start_date, end_date, freq)

    events: List[Dict[str, Any]] = []
    kpis: List[Dict[str, Any]] = []

    for period in periods:
        period_days = max(1, (period.end - period.start).days)
        headcount_start = int(df_state["active"].sum())
        mak_start = float(df_state.loc[df_state["active"], "mak"].sum())

        # Refresh ages/tenure each period
        df_state["age"] = _calc_age(df_state[COL_GEB], period.start)
        df_state["tenure"] = _calc_tenure(df_state[COL_EINTRITT], period.start)

        # Schedule new ATZ cases for this period
        if params.get("components", {}).get("atz", True):
            atz_pivot = _schedule_new_atz_cases(df_state, atz_pivot, params, period, rng)
            df_state["in_atz"] = df_state.index.isin(atz_pivot[COL_PERSNR].dropna().astype(str).unique())

        # ATZ events
        if params.get("components", {}).get("atz", True):
            ar_to_fr, atz_end = _get_atz_events_from_schedule(atz_pivot, period.start, period.end)

            for persnr in ar_to_fr:
                if persnr in df_state.index and df_state.loc[persnr, "active"]:
                    if df_state.loc[persnr, "mak"] > 0:
                        mak_change = -float(df_state.loc[persnr, "mak"])
                        df_state.loc[persnr, "mak"] = 0.0
                    else:
                        mak_change = 0.0
                    df_state.loc[persnr, "atz_fr_active"] = True
                    events.append({
                        "period_label": period.label,
                        "period_start": period.start,
                        "period_end": period.end,
                        "event_date": period.end,
                        "persnr": persnr,
                        "reason_code": REASON_ATZ_AR_TO_FR,
                        "reason_label": REASON_LABELS[REASON_ATZ_AR_TO_FR],
                        "headcount_change": 0,
                        "mak_change": mak_change,
                        "age": float(df_state.loc[persnr, "age"]),
                        "tenure": float(df_state.loc[persnr, "tenure"]),
                    })

            for persnr in atz_end:
                if persnr in df_state.index and df_state.loc[persnr, "active"]:
                    mak_change = -float(df_state.loc[persnr, "mak"])
                    df_state.loc[persnr, "mak"] = 0.0
                    df_state.loc[persnr, "active"] = False
                    events.append({
                        "period_label": period.label,
                        "period_start": period.start,
                        "period_end": period.end,
                        "event_date": period.end,
                        "persnr": persnr,
                        "reason_code": REASON_ATZ_END,
                        "reason_label": REASON_LABELS[REASON_ATZ_END],
                        "headcount_change": -1,
                        "mak_change": mak_change,
                        "age": float(df_state.loc[persnr, "age"]),
                        "tenure": float(df_state.loc[persnr, "tenure"]),
                    })

        # Retirement events (non-ATZ only)
        if params.get("components", {}).get("retirement", True):
            eligible = df_state[(df_state["active"]) & (~df_state["in_atz"])].copy()
            if not eligible.empty:
                ages = eligible["age"]
                p65 = _annual_to_period_rate(params.get("retirement", {}).get("rent_rate_65", 0.9), period_days)
                p60 = _annual_to_period_rate(params.get("retirement", {}).get("rent_rate_60_65", 0.1), period_days)
                probs = np.where(ages >= 65, p65, np.where((ages >= 60) & (ages < 65), p60, 0.0))
                draws = rng.random(len(eligible))
                retire_ids = eligible.index[draws < probs].tolist()

                for persnr in retire_ids:
                    mak_change = -float(df_state.loc[persnr, "mak"])
                    df_state.loc[persnr, "mak"] = 0.0
                    df_state.loc[persnr, "active"] = False
                    events.append({
                        "period_label": period.label,
                        "period_start": period.start,
                        "period_end": period.end,
                        "event_date": period.end,
                        "persnr": persnr,
                        "reason_code": REASON_RETIREMENT,
                        "reason_label": REASON_LABELS[REASON_RETIREMENT],
                        "headcount_change": -1,
                        "mak_change": mak_change,
                        "age": float(df_state.loc[persnr, "age"]),
                        "tenure": float(df_state.loc[persnr, "tenure"]),
                    })

        # Quit events (non-ATZ only)
        if params.get("components", {}).get("quit", True):
            eligible = df_state[(df_state["active"]) & (~df_state["in_atz"])].copy()
            if not eligible.empty:
                probs = []
                for _, row in eligible.iterrows():
                    annual = _select_quit_prob(row["age"], row["tenure"], params)
                    probs.append(_annual_to_period_rate(annual, period_days))
                probs = np.array(probs)
                draws = rng.random(len(eligible))
                quit_ids = eligible.index[draws < probs].tolist()

                for persnr in quit_ids:
                    if df_state.loc[persnr, "active"]:
                        mak_change = -float(df_state.loc[persnr, "mak"])
                        df_state.loc[persnr, "mak"] = 0.0
                        df_state.loc[persnr, "active"] = False
                        events.append({
                            "period_label": period.label,
                            "period_start": period.start,
                            "period_end": period.end,
                            "event_date": period.end,
                            "persnr": persnr,
                            "reason_code": REASON_QUIT,
                            "reason_label": REASON_LABELS[REASON_QUIT],
                            "headcount_change": -1,
                            "mak_change": mak_change,
                            "age": float(df_state.loc[persnr, "age"]),
                            "tenure": float(df_state.loc[persnr, "tenure"]),
                        })

        # Ruhend return events
        if params.get("components", {}).get("ruhend", True):
            return_rate = float(params.get("ruhend", {}).get("ruhend_return_rate", 0.95))
            return_prob = _annual_to_period_rate(return_rate, period_days)
            eligible = df_state[(df_state["active"]) & (df_state["status_ruhend"])].copy()
            if not eligible.empty:
                can_return = eligible[eligible["ruhend_until"].notna() & (eligible["ruhend_until"] <= period.start)]
                if not can_return.empty:
                    draws = rng.random(len(can_return))
                    return_ids = can_return.index[draws < return_prob].tolist()

                    for persnr in return_ids:
                        df_state.loc[persnr, "status_ruhend"] = False
                        df_state.loc[persnr, "mak"] = float(pd.to_numeric(df_state.loc[persnr, COL_BSGRD], errors="coerce") or 0) / 100.0
                        df_state.loc[persnr, "ruhend_until"] = pd.NaT
                        events.append({
                            "period_label": period.label,
                            "period_start": period.start,
                            "period_end": period.end,
                            "event_date": period.end,
                            "persnr": persnr,
                            "reason_code": REASON_RUHEND_RETURN,
                            "reason_label": REASON_LABELS[REASON_RUHEND_RETURN],
                            "headcount_change": 0,
                            "mak_change": float(df_state.loc[persnr, "mak"]),
                            "age": float(df_state.loc[persnr, "age"]),
                            "tenure": float(df_state.loc[persnr, "tenure"]),
                        })

        # Ruhend new cases
        if params.get("components", {}).get("ruhend", True):
            expected = float(params.get("ruhend", {}).get("ruhend_new_cases_per_year", 0)) * (period_days / 365.25)
            if expected > 0:
                count = int(rng.poisson(lam=expected))
                if count > 0:
                    eligible = df_state[(df_state["active"]) & (~df_state["status_ruhend"]) & (~df_state["in_atz"])].copy()
                    if not eligible.empty:
                        chosen = eligible.sample(n=min(count, len(eligible)), random_state=rng).index.tolist()
                        for persnr in chosen:
                            mak_change = -float(df_state.loc[persnr, "mak"])
                            df_state.loc[persnr, "status_ruhend"] = True
                            df_state.loc[persnr, "mak"] = 0.0
                            df_state.loc[persnr, "ruhend_until"] = period.start + pd.DateOffset(months=ruhend_months)
                            events.append({
                                "period_label": period.label,
                                "period_start": period.start,
                                "period_end": period.end,
                                "event_date": period.end,
                                "persnr": persnr,
                                "reason_code": REASON_RUHEND_START,
                                "reason_label": REASON_LABELS[REASON_RUHEND_START],
                                "headcount_change": 0,
                                "mak_change": mak_change,
                                "age": float(df_state.loc[persnr, "age"]),
                                "tenure": float(df_state.loc[persnr, "tenure"]),
                            })

        headcount_end = int(df_state["active"].sum())
        mak_end = float(df_state.loc[df_state["active"], "mak"].sum())

        period_events = [e for e in events if e["period_label"] == period.label]
        exit_count = sum(1 for e in period_events if e["headcount_change"] < 0)
        mak_loss_gross = sum(-e["mak_change"] for e in period_events if e["mak_change"] < 0)
        abgaenge_total = sum(1 for e in period_events if e["headcount_change"] < 0 or e["mak_change"] < 0)

        avg_headcount = (headcount_start + headcount_end) / 2 if (headcount_start + headcount_end) > 0 else 0
        abgangsquote = (exit_count / avg_headcount) if avg_headcount > 0 else 0.0

        kpis.append({
            "period_label": period.label,
            "period_start": period.start,
            "period_end": period.end,
            "headcount_start": headcount_start,
            "headcount_end": headcount_end,
            "headcount_delta": headcount_end - headcount_start,
            "mak_start": mak_start,
            "mak_end": mak_end,
            "mak_delta": mak_end - mak_start,
            "abgaenge_total": abgaenge_total,
            "exit_count": exit_count,
            "mak_loss_gross": mak_loss_gross,
            "abgangsquote": abgangsquote,
        })

    forecast_kpis = pd.DataFrame(kpis)
    events_df = pd.DataFrame(events)

    tables = {
        "atz": events_df[events_df["reason_code"].isin([REASON_ATZ_AR_TO_FR, REASON_ATZ_END])] if not events_df.empty else pd.DataFrame(),
        "retirement": events_df[events_df["reason_code"] == REASON_RETIREMENT] if not events_df.empty else pd.DataFrame(),
        "quit": events_df[events_df["reason_code"] == REASON_QUIT] if not events_df.empty else pd.DataFrame(),
        "ruhend": events_df[events_df["reason_code"].isin([REASON_RUHEND_START, REASON_RUHEND_RETURN])] if not events_df.empty else pd.DataFrame(),
    }

    return {
        "forecast_kpis": forecast_kpis,
        "events_person_level": events_df,
        "assumptions": params,
        "tables": tables,
    }
