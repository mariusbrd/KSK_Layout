"""
Validation for Abgaenge forecast outputs.
"""

from typing import Dict, Any
import pandas as pd


def validate_outputs(result_dict: Dict[str, Any]) -> Dict[str, Any]:
    checks = {}

    kpis = result_dict.get("forecast_kpis", pd.DataFrame())
    events = result_dict.get("events_person_level", pd.DataFrame())

    if kpis.empty:
        checks["kpis_empty"] = True
        return checks

    checks["headcount_non_negative"] = (kpis["headcount_end"] >= 0).all()
    checks["mak_non_negative"] = (kpis["mak_end"] >= 0).all()
    checks["abgangsquote_range"] = ((kpis["abgangsquote"] >= 0) & (kpis["abgangsquote"] <= 1)).all()

    if not events.empty:
        checks["events_have_reason"] = events["reason_code"].notna().all()
        checks["events_have_persnr"] = events["persnr"].notna().all()
    else:
        checks["events_have_reason"] = True
        checks["events_have_persnr"] = True

    return checks
