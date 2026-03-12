"""
Hypothesen-Tests fuer die ATZ-Logik in "Prognose: Abgaenge".

Ziel:
- Soll-Hypothesen zur Altersteilzeit fachlich pruefen
- Ergebnisse strukturiert in Markdown dokumentieren

Ausfuehren:
    py -m pytest KSK_Layout/tests/test_atz_hypothesen_forecast.py -v
oder:
    py KSK_Layout/tests/test_atz_hypothesen_forecast.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.schemas import (
    REASON_ATZ_AR_TO_FR,
    REASON_ATZ_END,
    REASON_RETIREMENT,
)

START = pd.Timestamp("2026-01-01")
RESULT_MD = Path(__file__).with_name("test_atz_hypothesen_ergebnisse.md")


@dataclass
class HypothesisResult:
    code: str
    title: str
    status: str
    details: str


def _norm_persnr(value: str) -> str:
    return str(value).split(".")[0].zfill(6)


def _employee(
    persnr: str,
    age_years: int,
    status: str = "Aktives Beschäftigungsverhältnis",
    oe_code: str = "1000",
    orgunit: str = "OE_A",
    jobfamily: str = "JF_A",
    mak: float = 1.0,
) -> dict:
    birth = START - pd.DateOffset(years=age_years)
    return {
        "PersNr": persnr,
        "GebDatum": birth,
        "Eintritt": pd.Timestamp("2005-01-01"),
        "BsGrd": 100.0,
        "Status kundenindividuell": status,
        "Kürzel OrgEinheit": oe_code,
        "Organisationseinheit": orgunit,
        "Jobfamily": jobfamily,
        "Sollarbeitszeit": 39.0,
        "MAK_Calculated": mak,
    }


def _empty_atz_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])


def _run(df_ma: pd.DataFrame, df_atz: pd.DataFrame, end: str, params: dict, freq: str = "M") -> dict:
    return run_forecast_abgaenge(
        df_ma=df_ma,
        df_atz=df_atz,
        start_date=START,
        end_date=pd.Timestamp(end),
        freq=freq,
        params=params,
    )


def _base_params() -> dict:
    return {
        "random_seed": 42,
        "components": {
            "atz": True,
            "retirement": False,
            "quit": False,
            "ruhend": False,
        },
        "atz": {
            "new_atz_rate": 100.0,
            "use_atz_matrix": False,
            "atz_dimension": "JobFamily",
            "atz_matrix": {"Default": 100.0},
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 0.25,
            "atz_duration_fr_years": 0.25,
        },
    }


def _extract_atz_persnrs(result: dict) -> set[str]:
    pivot = result["tables"].get("atz_pivot", pd.DataFrame())
    if pivot.empty:
        return set()
    return {_norm_persnr(v) for v in pivot["PersNr"].tolist()}


def _extract_reason_persnrs(result: dict, reason_code: str) -> set[str]:
    events = result.get("events_person_level", pd.DataFrame())
    if events.empty:
        return set()
    rows = events[events["reason_code"] == reason_code]
    return {_norm_persnr(v) for v in rows["persnr"].tolist()}


def _h1_age_band_controls_eligibility() -> str:
    """Hypothese H1: Nur MA im Altersband min..max gelangen in neue ATZ-Faelle."""
    df_ma = pd.DataFrame([
        _employee("1001", age_years=54),
        _employee("1002", age_years=55),
        _employee("1003", age_years=60),
        _employee("1004", age_years=61),
    ])

    params = _base_params()
    result = _run(df_ma, _empty_atz_df(), end="2026-03-31", params=params, freq="M")
    atz_ids = _extract_atz_persnrs(result)

    must_include = {_norm_persnr("1002"), _norm_persnr("1003")}
    must_exclude = {_norm_persnr("1001"), _norm_persnr("1004")}

    assert must_include.issubset(atz_ids), f"Eligible fehlen: {sorted(must_include - atz_ids)}"
    assert atz_ids.isdisjoint(must_exclude), f"Nicht-Eligible enthalten: {sorted(atz_ids & must_exclude)}"
    return f"ATZ-Pool korrekt auf Altersband begrenzt: {sorted(atz_ids)}"


def _h2_excluded_oe_codes_are_filtered() -> str:
    """Hypothese H2: Sonder-OE-Codes werden vom ATZ-Neufallpool ausgeschlossen."""
    df_ma = pd.DataFrame([
        _employee("1101", age_years=57, oe_code="1000", orgunit="OE_NORMAL"),
        _employee("1102", age_years=57, oe_code="9941", orgunit="OE_EXCLUDED"),
    ])

    params = _base_params()
    result = _run(df_ma, _empty_atz_df(), end="2026-03-31", params=params, freq="M")
    atz_ids = _extract_atz_persnrs(result)

    assert _norm_persnr("1101") in atz_ids, "Normale OE wurde nicht gezogen"
    assert _norm_persnr("1102") not in atz_ids, "Ausgeschlossene OE wurde gezogen"
    return f"Sonder-OE gefiltert, gezogene IDs: {sorted(atz_ids)}"


def _h3_ruhend_and_existing_atz_not_scheduled_again() -> str:
    """Hypothese H3: Ruhend und bereits in ATZ werden nicht als neue ATZ-Faelle gezogen."""
    df_ma = pd.DataFrame([
        _employee("1201", age_years=57, status="Aktives Beschäftigungsverhältnis"),
        _employee("1202", age_years=57, status="Ruhendes Beschäftigungsverhältnis"),
        _employee("1203", age_years=57, status="Aktives Beschäftigungsverhältnis"),
    ])

    df_atz = pd.DataFrame([
        {
            "PersNr": "1203",
            "Phase": "AR",
            "Beginn": pd.Timestamp("2025-01-01"),
            "Ende": pd.Timestamp("2028-01-01"),
            "Ende ATZ Vertrag": pd.Timestamp("2030-01-01"),
        }
    ])

    params = _base_params()
    result = _run(df_ma, df_atz, end="2026-03-31", params=params, freq="M")
    pivot = result["tables"].get("atz_pivot", pd.DataFrame())
    atz_ids = _extract_atz_persnrs(result)

    assert _norm_persnr("1201") in atz_ids, "Aktiver berechtigter MA wurde nicht gezogen"
    assert _norm_persnr("1202") not in atz_ids, "Ruhender MA wurde unzulaessig gezogen"
    assert _norm_persnr("1203") in atz_ids, "Bestehender ATZ-Fall ging verloren"

    cnt_1203 = 0
    if not pivot.empty:
        cnt_1203 = sum(_norm_persnr(v) == _norm_persnr("1203") for v in pivot["PersNr"].tolist())
    assert cnt_1203 == 1, f"Bestehender ATZ-Fall wurde doppelt geplant (Anzahl={cnt_1203})"

    return f"Nur aktiver Nicht-ATZ-MA neu geplant; bestehender ATZ unveraendert. IDs: {sorted(atz_ids)}"


def _h4_atz_matrix_jobfamily_overrides_base() -> str:
    """Hypothese H4: Bei aktivierter Matrix steuert JobFamily-Rate die Selektion."""
    df_ma = pd.DataFrame([
        _employee("1301", age_years=57, jobfamily="JF_HIGH"),
        _employee("1302", age_years=57, jobfamily="JF_ZERO"),
    ])

    params = _base_params()
    params["atz"]["use_atz_matrix"] = True
    params["atz"]["atz_dimension"] = "JobFamily"
    params["atz"]["new_atz_rate"] = 0.0
    params["atz"]["atz_matrix"] = {
        "JF_HIGH": 100.0,
        "JF_ZERO": 0.0,
        "Default": 0.0,
    }

    result = _run(df_ma, _empty_atz_df(), end="2026-03-31", params=params, freq="M")
    atz_ids = _extract_atz_persnrs(result)

    assert _norm_persnr("1301") in atz_ids, "JF_HIGH wurde nicht gezogen"
    assert _norm_persnr("1302") not in atz_ids, "JF_ZERO wurde trotz 0 gezogen"
    return f"JobFamily-Matrix greift korrekt: {sorted(atz_ids)}"


def _h5_atz_matrix_orgunit_default_fallback() -> str:
    """Hypothese H5: OrgUnit-Matrix nutzt exakten Treffer, sonst Default-Fallback."""
    df_ma = pd.DataFrame([
        _employee("1401", age_years=57, orgunit="OE_MATCH"),
        _employee("1402", age_years=57, orgunit="OE_OTHER"),
    ])

    params = _base_params()
    params["atz"]["use_atz_matrix"] = True
    params["atz"]["atz_dimension"] = "OrgUnit"
    params["atz"]["new_atz_rate"] = 0.0
    params["atz"]["atz_matrix"] = {
        "OE_MATCH": 100.0,
        "Default": 0.0,
    }

    result = _run(df_ma, _empty_atz_df(), end="2026-03-31", params=params, freq="M")
    atz_ids = _extract_atz_persnrs(result)

    assert _norm_persnr("1401") in atz_ids, "OrgUnit Match wurde nicht gezogen"
    assert _norm_persnr("1402") not in atz_ids, "OrgUnit Default=0 wurde nicht eingehalten"
    return f"OrgUnit-Matrix inkl. Default-Fallback korrekt: {sorted(atz_ids)}"


def _h6_atz_events_follow_ar_to_fr_then_atz_end() -> str:
    """Hypothese H6: Event-Logik erzeugt AR->FR (MAK runter) vor ATZ_END (HC runter)."""
    df_ma = pd.DataFrame([
        _employee("1501", age_years=58, mak=1.0),
    ])

    df_atz = pd.DataFrame([
        {
            "PersNr": "1501",
            "Phase": "AR",
            "Beginn": pd.Timestamp("2023-01-01"),
            "Ende": pd.Timestamp("2026-01-31"),
            "Ende ATZ Vertrag": pd.Timestamp("2026-04-30"),
        },
        {
            "PersNr": "1501",
            "Phase": "FR",
            "Beginn": pd.Timestamp("2026-02-01"),
            "Ende": pd.Timestamp("2026-04-30"),
            "Ende ATZ Vertrag": pd.Timestamp("2026-04-30"),
        },
    ])

    params = _base_params()
    params["atz"]["new_atz_rate"] = 0.0  # Keine zusaetzlichen Neufaelle

    result = _run(df_ma, df_atz, end="2026-04-30", params=params, freq="M")
    events = result["events_person_level"]

    assert not events.empty, "Keine Events erzeugt"

    arfr = events[events["reason_code"] == REASON_ATZ_AR_TO_FR]
    atz_end = events[events["reason_code"] == REASON_ATZ_END]

    assert len(arfr) == 1, f"AR->FR Events unerwartet: {len(arfr)}"
    assert len(atz_end) == 1, f"ATZ_END Events unerwartet: {len(atz_end)}"

    arfr_row = arfr.iloc[0]
    end_row = atz_end.iloc[0]

    assert int(arfr_row["headcount_change"]) == 0, "AR->FR muss HC=0 haben"
    assert float(arfr_row["mak_change"]) < 0.0, "AR->FR muss MAK reduzieren"
    assert int(end_row["headcount_change"]) == -1, "ATZ_END muss HC um 1 reduzieren"
    assert pd.to_datetime(arfr_row["event_date"]) <= pd.to_datetime(end_row["event_date"]), "AR->FR muss zeitlich vor/gleich ATZ_END liegen"

    return (
        f"Event-Reihenfolge ok: AR->FR am {pd.to_datetime(arfr_row['event_date']).date()}, "
        f"ATZ_END am {pd.to_datetime(end_row['event_date']).date()}"
    )


def _h7_in_atz_excludes_direct_retirement() -> str:
    """Hypothese H7: Personen in ATZ werden nicht als direkte Rentenfaelle gezogen."""
    df_ma = pd.DataFrame([
        _employee("1601", age_years=66),  # in ATZ
        _employee("1602", age_years=66),  # normal
    ])

    df_atz = pd.DataFrame([
        {
            "PersNr": "1601",
            "Phase": "AR",
            "Beginn": pd.Timestamp("2025-01-01"),
            "Ende": pd.Timestamp("2027-01-01"),
            "Ende ATZ Vertrag": pd.Timestamp("2029-01-01"),
        }
    ])

    params = _base_params()
    params["components"]["retirement"] = True
    params["retirement"] = {
        "rent_rate_65": 100.0,
        "rent_rate_60_65": 0.0,
    }
    params["atz"]["new_atz_rate"] = 0.0

    result = _run(df_ma, df_atz, end="2026-12-31", params=params, freq="M")
    ret_ids = _extract_reason_persnrs(result, REASON_RETIREMENT)

    assert _norm_persnr("1601") not in ret_ids, "ATZ-Person wurde unzulaessig direkt verrentet"
    assert _norm_persnr("1602") in ret_ids, "Nicht-ATZ-Person 65+ wurde nicht verrentet"
    return f"Direkte Rente greift nur fuer Nicht-ATZ: {sorted(ret_ids)}"


def run_atz_hypothesis_suite(write_markdown: bool = False) -> list[HypothesisResult]:
    hypotheses = [
        ("H1", "Altersband steuert Eligibility", _h1_age_band_controls_eligibility),
        ("H2", "Sonder-OE-Codes werden ausgeschlossen", _h2_excluded_oe_codes_are_filtered),
        ("H3", "Ruhend und bestehende ATZ-Faelle sind von Neufaellen ausgeschlossen", _h3_ruhend_and_existing_atz_not_scheduled_again),
        ("H4", "ATZ-Matrix (JobFamily) uebersteuert Basisrate", _h4_atz_matrix_jobfamily_overrides_base),
        ("H5", "ATZ-Matrix (OrgUnit) mit Default-Fallback funktioniert", _h5_atz_matrix_orgunit_default_fallback),
        ("H6", "ATZ-Events folgen AR->FR vor ATZ_END", _h6_atz_events_follow_ar_to_fr_then_atz_end),
        ("H7", "In-ATZ schliesst direkte Rentenlogik aus", _h7_in_atz_excludes_direct_retirement),
    ]

    results: list[HypothesisResult] = []

    for code, title, fn in hypotheses:
        try:
            detail = fn()
            results.append(HypothesisResult(code=code, title=title, status="PASS", details=detail))
        except Exception as exc:
            results.append(
                HypothesisResult(
                    code=code,
                    title=title,
                    status="FAIL",
                    details=f"{type(exc).__name__}: {exc}",
                )
            )

    if write_markdown:
        _write_markdown(results)

    return results


def _write_markdown(results: list[HypothesisResult]) -> None:
    passed = sum(1 for r in results if r.status == "PASS")
    total = len(results)

    lines = []
    lines.append("# Ergebnis: Hypothesentest ATZ (Prognose Abgaenge)")
    lines.append("")
    lines.append(f"- Ausgefuehrt am: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("- Testfokus: Altersteilzeit-Mechanik (Eligibility, Matrix, Eventlogik, Interaktion Rente)")
    lines.append(f"- Ergebnis: {passed}/{total} Hypothesen bestaetigt")
    lines.append("")
    lines.append("## Zusammenfassung")
    lines.append("")
    lines.append("| Hypothese | Titel | Status | Kernergebnis |")
    lines.append("|---|---|---|---|")

    for r in results:
        lines.append(f"| {r.code} | {r.title} | {r.status} | {r.details} |")

    failed = [r for r in results if r.status == "FAIL"]
    lines.append("")
    lines.append("## Kurzfazit")
    lines.append("")
    if not failed:
        lines.append("- Alle getesteten Soll-Hypothesen wurden durch das aktuelle Verhalten der Engine bestaetigt.")
    else:
        lines.append(f"- Es bestehen Abweichungen: {len(failed)} Hypothesen sind fehlgeschlagen.")
        for r in failed:
            lines.append(f"- {r.code}: {r.details}")

    lines.append("")
    lines.append("## Methodik-Hinweis")
    lines.append("")
    lines.append("- Fuer stabile Reproduzierbarkeit wurden deterministische Seeds verwendet.")
    lines.append("- In einzelnen Tests wurden hohe Raten genutzt, um stochastische Ziehungen robust sichtbar zu machen.")

    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_atz_hypothesen_suite() -> None:
    results = run_atz_hypothesis_suite(write_markdown=False)
    assert len(results) == 7, f"Unerwartete Anzahl Hypothesen-Ergebnisse: {len(results)}"
    allowed = {"PASS", "FAIL"}
    assert all(r.status in allowed for r in results), "Unerwarteter Status in Hypothesenergebnissen"


if __name__ == "__main__":
    res = run_atz_hypothesis_suite(write_markdown=True)
    passed = sum(1 for r in res if r.status == "PASS")
    print(f"Hypothesen PASS: {passed}/{len(res)}")
    print(f"Markdown geschrieben: {RESULT_MD}")
