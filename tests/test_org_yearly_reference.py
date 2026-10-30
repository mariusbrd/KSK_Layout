"""Frozen synthetic references before adding annual event aggregation.

Capture explicitly once: py tests/test_org_yearly_reference.py --capture
Normal pytest runs only compare; they never regenerate references.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_compact_simulation_engine import _base_snapshot
from abgaenge.params import default_params as attrition_defaults
from zugaenge.params import default_params as accession_defaults
from dataloader.cluster_resolver import ClusterMappingBundle
from dataloader.compact_simulation_engine import simulate_compact_snapshot

REFERENCE = ROOT / "tests" / "fixtures" / "org_yearly_reference.json"
SCENARIOS = ("identity", "no_events", "departures", "entries", "atz_ruhend", "azubi_transfer", "combined")


def _run(name, *, seed=42, include_audits=True):
    rows = []
    for i, age in enumerate((66, 58, 42, 25, 38, 21), 1):
        row = _base_snapshot(age).iloc[0].to_dict()
        row.update(PersNr=f"{i:06d}", Personalnummer=f"{i:06d}",
                   Organisationseinheit="OE1" if i <= 3 else "OE2")
        if i == 6:
            row.update(Jobfamily="Azubi", **{"MitarbGruppenbez.": "Auszubildende",
                       "Eintritt": pd.Timestamp("2023-08-01"), "TrfGr": "E1"})
        rows.append(row)
    snapshot = pd.DataFrame(rows)
    before = snapshot.copy(deep=True)
    abg, zug = attrition_defaults(), accession_defaults()
    abg["random_seed"] = seed
    zug["random_seed"] = seed
    abg["components"] = dict.fromkeys(("atz", "retirement", "quit", "ruhend"), False)
    for section in ("azubi", "trainee", "new_hires"):
        zug[section]["active"] = False
    if name in ("departures", "combined"):
        abg["components"].update(retirement=True, quit=True)
        abg["retirement"].update(rent_rate_65=1.0, rent_rate_60_65=0.0)
        abg["quit"].update(use_quit_matrix=False, quit_rate_base=0.25)
    atz = pd.DataFrame()
    if name in ("atz_ruhend", "combined"):
        abg["components"].update(atz=True, ruhend=True)
        abg["atz"]["new_atz_rate"] = 0.0
        abg["ruhend"].update(ruhend_new_cases_per_year=1, ruhend_return_rate=1.0,
                              ruhend_avg_duration_months=3)
        atz = pd.DataFrame([{"PersNr": "000002", "Phase": "AR", "Beginn": pd.Timestamp("2025-01-01"),
                            "Ende": pd.Timestamp("2026-07-01"),
                            "Ende ATZ Vertrag": pd.Timestamp("2027-07-01")}])
    if name in ("entries", "combined"):
        zug["new_hires"].update(active=True, count_per_year=3, strategy="OrgUnit", target_org_unit="OE2")
    if name in ("azubi_transfer", "combined"):
        zug["azubi"].update(active=True, new_cases_per_year=2, retention_rate=1.0,
                            strategy="OrgUnit", target_org_unit="OE1", duration_years=1.0)
    source = SimpleNamespace(mode="ui_upload", subtype="synthetic_reference", status="active",
                             source_signature="org-yearly-reference-v1", source_path=None, is_valid=True)
    bundle = ClusterMappingBundle(oe_map={"OE1": "Cluster A", "OE2": "Cluster B"},
                                  jf_map={"Angestellte": "Regular", "Azubi": "Training"},
                                  source_signature=source.source_signature)
    # Isolate user settings without changing their file; all real forecast functions run.
    with patch("utils.settings_loader.load_user_settings", return_value={}):
        result = simulate_compact_snapshot(
            snapshot, atz, pd.Timestamp("2025-12-31" if name == "identity" else "2027-12-31"),
            base_date=pd.Timestamp("2025-12-31"), abgaenge_params=deepcopy(abg),
            zugaenge_params=deepcopy(zug), active_cluster_source=source,
            cluster_mapping_bundle=bundle, cluster_source_signature=source.source_signature,
            include_audits=include_audits)
    pd.testing.assert_frame_equal(snapshot, before, check_exact=True)
    return result


def _frame(df):
    return {"dtypes": [str(t) for t in df.dtypes],
            "data": json.loads(df.to_json(orient="split", date_format="iso", double_precision=15,
                                         default_handler=str))}


def _payload(result):
    frames = {"future_snapshot": result.future_snapshot_df, "final_people": result.future_employee_df,
              "departures": result.abgaenge_result.get("events_person_level", pd.DataFrame()),
              "entries": result.zugaenge_result.get("events", pd.DataFrame())}
    for prefix, outputs in (("abg", result.abgaenge_result), ("zug", result.zugaenge_result)):
        frames[prefix + "_kpis"] = outputs.get("forecast_kpis", pd.DataFrame())
    return {key: _frame(df) for key, df in frames.items()}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_frozen_simulation_result(scenario):
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert _payload(_run(scenario)) == reference["scenarios"][scenario]


if __name__ == "__main__":
    if sys.argv[1:] != ["--capture"]:
        raise SystemExit("Use --capture only for the initial reference capture.")
    if REFERENCE.exists():
        raise SystemExit("Reference exists; refusing to overwrite.")
    payload = {"description": "Synthetic full-run baseline; fixed dates, seed 42, isolated user settings.",
               "engine_sha256": hashlib.sha256((ROOT / "dataloader/compact_simulation_engine.py").read_bytes()).hexdigest(),
               "scenarios": {name: _payload(_run(name)) for name in SCENARIOS}}
    REFERENCE.parent.mkdir(exist_ok=True)
    REFERENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Captured {len(SCENARIOS)} scenarios: {REFERENCE.name}")
