"""
Validation for Abgaenge forecast outputs.
"""

from typing import Dict, Any
import numpy as np
import pandas as pd


def validate_outputs(result_dict: Dict[str, Any]) -> Dict[str, Any]:
    checks = {}

    kpis = result_dict.get("forecast_kpis", pd.DataFrame())
    events = result_dict.get("events_person_level", pd.DataFrame())

    if kpis.empty:
        checks["kpis_empty"] = True
        return checks

    checks["headcount_non_negative"] = bool((kpis["headcount_end"] >= 0).all())
    checks["mak_non_negative"] = bool((kpis["mak_end"] >= 0).all())
    checks["abgangsquote_range"] = bool(((kpis["abgangsquote"] >= 0) & (kpis["abgangsquote"] <= 1)).all())

    # P11: Balance equation – headcount_start[t] must equal headcount_end[t-1]
    if len(kpis) > 1:
        starts = kpis["headcount_start"].iloc[1:].values
        prev_ends = kpis["headcount_end"].iloc[:-1].values
        checks["headcount_continuity"] = bool((starts == prev_ends).all())

        mak_starts = kpis["mak_start"].iloc[1:].values
        mak_prev_ends = kpis["mak_end"].iloc[:-1].values
        checks["mak_continuity"] = bool(np.allclose(mak_starts, mak_prev_ends, atol=0.01))

    if not events.empty:
        checks["events_have_reason"] = bool(events["reason_code"].notna().all())
        checks["events_have_persnr"] = bool(events["persnr"].notna().all())
    else:
        checks["events_have_reason"] = True
        checks["events_have_persnr"] = True

    return checks
