"""Summarize productive OE stock gaps without printing employee identifiers."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_page_rerun_paths import (
    FROZEN_SETTINGS, FROZEN_STICHTAG, _freeze_context,
    _load_page_module, _reset_state,
)
from abgaenge.params import default_params as departure_defaults
from dataloader import loader
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from utils.org_simulation_events import build_simulation_event_summary
from utils.status_quo_baseline import build_status_quo_snapshot
from zugaenge.params import default_params as hire_defaults


def main() -> None:
    _freeze_context()
    _reset_state()
    compact = _load_page_module("*_Kompakt.py", "oe_gap_compact")
    page = _load_page_module("*_Kompakt_plus_Simulation.py", "oe_gap_page")
    snapshot, _, _, source_summary = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(ROOT), None, None, None)
    source, bundle, signature = page._get_simulation_cluster_context(source_summary)
    baseline = compact.prepare_compact_data(
        build_status_quo_snapshot(snapshot, FROZEN_STICHTAG))
    dep, hire = departure_defaults(), hire_defaults()
    dep["random_seed"] = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    dep["atz"].update(new_atz_rate=0.35, use_atz_matrix=False,
                      atz_duration_ar_years=0.5, atz_duration_fr_years=0.5)
    dep["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    target = pd.Timestamp("2028-12-31")
    result = simulate_compact_snapshot(
        snapshot, atz, target, base_date=FROZEN_STICHTAG,
        abgaenge_params=deepcopy(dep), zugaenge_params=deepcopy(hire),
        active_cluster_source=source, cluster_mapping_bundle=bundle,
        cluster_source_signature=signature, include_audits=False)
    final = compact.prepare_compact_data(result.future_snapshot_df)
    context = page._build_simulation_event_context(
        result, bundle, "oe-gap", baseline_snapshot=baseline,
        final_snapshot=final, zugaenge_params=hire, abgaenge_params=dep,
        df_atz=atz, run_settings={"exclusions": deepcopy(FROZEN_SETTINGS["exclusions"])},
        source_snapshot=snapshot)
    summary = build_simulation_event_summary(context,
                                             expected_run_signature="oe-gap")
    bridge = summary["stock_reconciliation"]
    checks = bridge["checks"]
    failed = checks.loc[~checks["Abgestimmt"]].copy()
    print(f"seed={dep['random_seed']}; status={bridge['status']}; reason={bridge['reason']}")
    print(f"checks={len(checks)}; failed={len(failed)}")
    if not failed.empty:
        group = failed.groupby("Ebene", dropna=False).agg(
            cases=("Abgestimmt", "size"),
            head_gap=("Differenz Köpfe", "sum"),
            abs_head_gap=("Differenz Köpfe", lambda x: x.abs().sum()),
            mak_gap=("Differenz MAK", "sum"),
            abs_mak_gap=("Differenz MAK", lambda x: x.abs().sum()),
        )
        print(group.to_string())
        print(failed.sort_values("Differenz MAK", key=lambda x: x.abs(),
                                 ascending=False).head(12).to_string(index=False))
    diagnostics = bridge["person_diagnostics"]
    print(f"person_diagnostics={len(diagnostics)}")
    if not diagnostics.empty:
        causes = Counter(part.strip() for text in diagnostics["Diagnose"]
                         for part in str(text).split(";") if part.strip())
        print(f"causes={dict(causes)}")
        for column in ("Unerklärte Kopfänderung", "Unerklärte MAK-Änderung"):
            print(f"{column}: sum={diagnostics[column].sum():.6f}; "
                  f"abs={diagnostics[column].abs().sum():.6f}")
        public = diagnostics.drop(columns=["PersNr"])
        print(public.to_string(index=False))
        from utils.org_simulation_reconciliation import reporting_projection_base
        projection = reporting_projection_base(context, baseline)
        for case, person in enumerate(diagnostics["PersNr"], 1):
            print(f"case={case}")
            for stage, frame in (("baseline", baseline), ("projection", projection),
                                 ("raw_final", result.future_snapshot_df),
                                 ("final", final)):
                rows = frame.loc[frame["PersNr"].astype(str).eq(str(person))]
                columns = [col for col in (
                    "Organisationseinheit", "Planstelle", "Soll_FTE", "BsGrd",
                    "MAK_Calculated", "MAK_Reporting", "Is_Vacant", "Is_Excluded",
                    "ATZ_Status", "ist_atz_fr") if col in rows]
                print(f"{stage}: {rows[columns].to_dict('records')}")
            person_events = summary["events"].loc[
                summary["events"]["persnr"].astype(str).eq(str(person))]
            columns = [col for col in ("Jahr", "Ereignisart", "Organisationseinheit",
                                       "Köpfe-Veränderung", "MAK-Veränderung")
                       if col in person_events]
            print(f"events: {person_events[columns].to_dict('records')}")
    for key in ("basis_effects", "documented_effects"):
        table = bridge.get(key, pd.DataFrame())
        print(f"{key}: rows={len(table)}; columns={list(table.columns)}")
        if not table.empty:
            mak_col = next((c for c in table if c in ("MAK-Effekt", "MAK-Veränderung")), None)
            if mak_col:
                print(f"{key} MAK sum={pd.to_numeric(table[mak_col], errors='coerce').sum():.6f}")
    events = summary["events"]
    print(events.groupby(["Jahr", "Ereignisart"], dropna=False).size().to_string())


if __name__ == "__main__":
    main()
