"""
Deep-dive tests for Azubi forecast logic on page "Prognose: Zugaenge".

Focus areas:
1) August conversion rule bias for post-August entries
2) HC/MAK mismatch for Azubi_Hire by design
3) Shared takeover debt interaction (baseline vs new Azubi hires)
4) Jobfamily overwrite to "Sonstige"
5) Same-month Azubi_Conversion_Out/In net logic

Run:
    py -m pytest KSK_Layout/tests/test_azubi_forecast_deepdive.py -v
or:
    py KSK_Layout/tests/test_azubi_forecast_deepdive.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from zugaenge.forecast import (
    run_forecast_zugaenge,
    _estimate_baseline_graduation_date,
)

RESULT_MD = Path(__file__).with_name("test_azubi_forecast_deepdive_ergebnisse.md")


@dataclass
class CheckResult:
    code: str
    title: str
    status: str
    evidence: str


def _snapshot_for_azubi(persnr: str = "BAS1", entry: str = "2024-02-01", trfgr: str = "TVA") -> pd.DataFrame:
    return pd.DataFrame([
        {
            "PersNr": persnr,
            "Organisationseinheit": "OE_A",
            "Jobfamily": "Azubi Spezial",
            "TrfGr": trfgr,
            "active": True,
            "mak": 0.0,
            "Eintritt": pd.Timestamp(entry),
            "OE-Cluster": "Cluster_A",
            "JF-Cluster": "Cluster_JF_A",
        },
        {
            "PersNr": "REF1",
            "Organisationseinheit": "OE_A",
            "Jobfamily": "Angestellte",
            "TrfGr": "E9A",
            "active": True,
            "mak": 1.0,
            "Eintritt": pd.Timestamp("2019-01-01"),
            "OE-Cluster": "Cluster_A",
            "JF-Cluster": "Cluster_JF_B",
        },
    ])


def _base_params(retention_rate: float = 0.8, new_cases_per_year: float = 0.0) -> dict:
    return {
        "azubi": {
            "active": True,
            "new_cases_per_year": float(new_cases_per_year),
            "duration_years": 3.0,
            "retention_rate": float(retention_rate),
            "strategy": "Random",
            "entry_tariff_group": "E5",
            "entry_step": 1,
            "azubi_mak_during_training": 0.0,
            "azubi_mak_after_takeover": 1.0,
            "azubi_conversion_month": 8,
            "azubi_conversion_day": 1,
            "use_takeover_matrix": False,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {},
            "jf_to_cluster_map": {},
            "exclude_baseline_azubis": False,
        },
        "trainee": {"active": False},
        "new_hires": {"active": False},
        "random_seed": 42,
    }


def _check_c1_august_bias() -> CheckResult:
    entry_early = pd.Timestamp("2026-02-01")
    entry_late = pd.Timestamp("2026-09-01")

    grad_early = _estimate_baseline_graduation_date(entry_early, 3.0, 8, 1)
    grad_late = _estimate_baseline_graduation_date(entry_late, 3.0, 8, 1)

    # Expected by current implementation:
    # 2026-02 + 3y -> 2029-08-01
    # 2026-09 + 3y -> 2030-08-01 (extra ~11 months due cycle snap)
    assert grad_early == pd.Timestamp("2029-08-01")
    assert grad_late == pd.Timestamp("2030-08-01")

    delta_months = round((grad_late - entry_late).days / 30.44, 1)
    return CheckResult(
        code="C1",
        title="August cycle causes post-August extension",
        status="PASS",
        evidence=f"Entry 2026-09-01 -> Graduation {grad_late.date()} (~{delta_months} months total)",
    )


def _check_c2_hc_mak_mismatch_by_design() -> CheckResult:
    df_snapshot = _snapshot_for_azubi()
    params = _base_params(retention_rate=1.0, new_cases_per_year=12)

    res = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-12-31"),
        freq="M",
        params=params,
    )
    events = res["events"]
    hires = events[events["type"] == "Azubi_Hire"].copy()

    assert not hires.empty, "No Azubi_Hire events generated"
    assert (hires["count"] == 1).all(), "Azubi_Hire count expected to be +1"
    assert (hires["mak"] == 0.0).all(), "Azubi_Hire MAK expected to be 0.0"

    mismatches = int((hires["count"].abs() != hires["mak"].abs()).sum())
    assert mismatches == len(hires), "Expected full HC/MAK mismatch for Azubi_Hire"

    return CheckResult(
        code="C2",
        title="HC/MAK mismatch on Azubi_Hire is intentional",
        status="PASS",
        evidence=f"Azubi_Hire events={len(hires)}, all count=+1 with mak=0.0",
    )


def _baseline_outcome_with_new_hires(new_cases_per_year: int) -> str:
    df_snapshot = _snapshot_for_azubi(persnr="BAS1", entry="2024-02-01", trfgr="TVA")
    params = _base_params(retention_rate=0.2, new_cases_per_year=new_cases_per_year)

    res = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=pd.Timestamp("2027-01-01"),
        end_date=pd.Timestamp("2027-12-31"),
        freq="M",
        params=params,
    )
    events = res["events"]
    bas = events[events["persnr"] == "BAS1"]

    if (bas["type"] == "Azubi_Conversion_In").any():
        return "takeover"
    if (bas["type"] == "Azubi_Exit").any():
        return "exit"
    return "none"


def _check_c3_shared_takeover_debt_interaction() -> CheckResult:
    # Scan multiple new_cases_per_year values to detect whether baseline outcome changes.
    outcomes = {}
    for n in range(0, 31):
        outcomes[n] = _baseline_outcome_with_new_hires(n)

    unique_outcomes = sorted(set(outcomes.values()))
    assert len(unique_outcomes) >= 2, "No observable baseline-outcome change across new hire intensities"

    sample = [f"{k}:{v}" for k, v in outcomes.items() if v != "none"][:8]
    return CheckResult(
        code="C3",
        title="Shared takeover debt influences baseline decisions",
        status="PASS",
        evidence=f"Observed outcomes={unique_outcomes}; sample={', '.join(sample)}",
    )


def _check_c4_jobfamily_overwrite() -> CheckResult:
    original_jf = "Azubi Spezial"
    df_snapshot = _snapshot_for_azubi(persnr="BAS1", entry="2024-02-01", trfgr="TVA")
    assert df_snapshot.loc[df_snapshot["PersNr"] == "BAS1", "Jobfamily"].iloc[0] == original_jf

    params = _base_params(retention_rate=1.0, new_cases_per_year=0)
    res = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=pd.Timestamp("2027-01-01"),
        end_date=pd.Timestamp("2027-01-31"),
        freq="M",
        params=params,
    )

    final_state = res["final_state"]
    current_jf = str(final_state.loc["BAS1", "Jobfamily"])

    assert current_jf == "Sonstige", f"Expected overwrite to 'Sonstige', got {current_jf}"
    assert "Jobfamily_raw" not in final_state.columns, "Unexpected raw backup column exists"

    return CheckResult(
        code="C4",
        title="Jobfamily is overwritten to Sonstige in training phase",
        status="PASS",
        evidence=f"BAS1 Jobfamily: '{original_jf}' -> '{current_jf}'",
    )


def _check_c5_conversion_pair_same_month_net_zero() -> CheckResult:
    df_snapshot = _snapshot_for_azubi(persnr="BAS1", entry="2024-02-01", trfgr="TVA")
    params = _base_params(retention_rate=1.0, new_cases_per_year=0)

    res = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=pd.Timestamp("2027-08-01"),
        end_date=pd.Timestamp("2027-08-31"),
        freq="M",
        params=params,
    )
    events = res["events"]
    bas = events[events["persnr"] == "BAS1"].copy()

    out_events = bas[bas["type"] == "Azubi_Conversion_Out"]
    in_events = bas[bas["type"] == "Azubi_Conversion_In"]

    assert len(out_events) == 1, "Expected exactly one Azubi_Conversion_Out"
    assert len(in_events) == 1, "Expected exactly one Azubi_Conversion_In"
    assert pd.to_datetime(out_events.iloc[0]["date"]).date() == pd.to_datetime(in_events.iloc[0]["date"]).date()

    net_count = int(out_events["count"].sum() + in_events["count"].sum())
    assert net_count == 0, f"Expected net count 0, got {net_count}"

    return CheckResult(
        code="C5",
        title="Conversion Out/In appears as two raw events but nets to zero HC",
        status="PASS",
        evidence=f"Out={int(out_events['count'].sum())}, In={int(in_events['count'].sum())}, Net={net_count}",
    )


def run_deepdive(write_markdown: bool = False) -> list[CheckResult]:
    checks = [
        _check_c1_august_bias,
        _check_c2_hc_mak_mismatch_by_design,
        _check_c3_shared_takeover_debt_interaction,
        _check_c4_jobfamily_overwrite,
        _check_c5_conversion_pair_same_month_net_zero,
    ]

    results: list[CheckResult] = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as exc:
            name = fn.__name__.replace("_check_", "").upper()
            results.append(
                CheckResult(
                    code=name,
                    title=fn.__doc__.strip() if fn.__doc__ else fn.__name__,
                    status="FAIL",
                    evidence=f"{type(exc).__name__}: {exc}",
                )
            )

    if write_markdown:
        _write_markdown(results)

    return results


def _write_markdown(results: list[CheckResult]) -> None:
    passed = sum(1 for r in results if r.status == "PASS")
    total = len(results)

    lines = []
    lines.append("# Testergebnisse: Azubi-Forecast (Prognose Zugaenge Seite)")
    lines.append("")
    lines.append(f"- Ausgefuehrt am: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- Ergebnis: {passed}/{total} Checks bestanden")
    lines.append("")
    lines.append("| Check | Thema | Status | Evidenz |")
    lines.append("|---|---|---|---|")
    for r in results:
        lines.append(f"| {r.code} | {r.title} | {r.status} | {r.evidence} |")

    lines.append("")
    lines.append("## Kurzfazit")
    lines.append("")
    if passed == total:
        lines.append("- Alle Deep-Dive-Checks konnten reproduzierbar bestaetigt werden.")
    else:
        lines.append(f"- {total - passed} Checks sind fehlgeschlagen und sollten priorisiert analysiert werden.")

    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_azubi_forecast_deepdive() -> None:
    results = run_deepdive(write_markdown=False)
    failed = [r for r in results if r.status != "PASS"]
    assert not failed, "Deep-dive failed: " + "; ".join(f"{r.code}: {r.evidence}" for r in failed)


if __name__ == "__main__":
    res = run_deepdive(write_markdown=True)
    passed = sum(1 for r in res if r.status == "PASS")
    print(f"Deep-dive PASS: {passed}/{len(res)}")
    print(f"Markdown geschrieben: {RESULT_MD}")
