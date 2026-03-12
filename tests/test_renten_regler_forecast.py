"""
Tests fuer die Renten-Regler in der Seite "Prognose: Abgaenge".

Abgedeckt werden die beiden UI-Regler:
- rent_rate_65 ("Renteneintritt 65+")
- rent_rate_60_65 ("Fruehverrentung 60-64")

Dieses Skript prueft die Prognosefunktion unter mehreren Einstellungen
und erstellt bei direktem Aufruf eine kurze Markdown-Ergebnisdatei.

Ausfuehren:
    py -m pytest KSK_Layout/tests/test_renten_regler_forecast.py -v
oder:
    py KSK_Layout/tests/test_renten_regler_forecast.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import os

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.schemas import REASON_RETIREMENT

START = pd.Timestamp("2026-01-01")
END = pd.Timestamp("2026-12-31")
FREQ = "M"

RESULT_MD = Path(__file__).with_name("test_renten_regler_ergebnisse.md")


@dataclass
class Scenario:
    name: str
    rent_rate_65: float
    rent_rate_60_65: float


def _norm_persnr(value: str) -> str:
    return str(value).split(".")[0].zfill(6)


def _make_employee(persnr: str, age_years: int) -> dict:
    geb = START - pd.DateOffset(years=age_years)
    return {
        "PersNr": persnr,
        "GebDatum": geb,
        "Eintritt": pd.Timestamp("2005-01-01"),
        "BsGrd": 100.0,
        "Status kundenindividuell": "Aktiv",
        "Organisationseinheit": "OE-Test",
        "Jobfamily": "JF-Test",
        "Sollarbeitszeit": 39.0,
        "MAK_Calculated": 1.0,
    }


def _build_population() -> pd.DataFrame:
    rows = []

    # Gruppe A: 65+
    rows.extend([
        _make_employee("1001", age_years=66),
        _make_employee("1002", age_years=67),
    ])

    # Gruppe B: 60-64
    rows.extend([
        _make_employee("2001", age_years=62),
        _make_employee("2002", age_years=64),
    ])

    # Gruppe C: <60 (darf durch Rentenlogik nicht gezogen werden)
    rows.extend([
        _make_employee("3001", age_years=58),
        _make_employee("3002", age_years=45),
    ])

    return pd.DataFrame(rows)


def _build_params(rent_rate_65: float, rent_rate_60_65: float) -> dict:
    return {
        "random_seed": 42,
        "components": {
            "atz": False,
            "retirement": True,
            "quit": False,
            "ruhend": False,
        },
        "retirement": {
            "rent_rate_65": float(rent_rate_65),
            "rent_rate_60_65": float(rent_rate_60_65),
        },
    }


def _expected_retired_ids(scenario: Scenario) -> set[str]:
    ids_65 = {_norm_persnr("1001"), _norm_persnr("1002")}
    ids_60_64 = {_norm_persnr("2001"), _norm_persnr("2002")}

    expected: set[str] = set()
    if scenario.rent_rate_65 == 1.0:
        expected |= ids_65
    if scenario.rent_rate_60_65 == 1.0:
        expected |= ids_60_64
    return expected


def _run_scenario(df_ma: pd.DataFrame, scenario: Scenario) -> tuple[set[str], pd.DataFrame]:
    result = run_forecast_abgaenge(
        df_ma=df_ma,
        df_atz=pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"]),
        start_date=START,
        end_date=END,
        freq=FREQ,
        params=_build_params(scenario.rent_rate_65, scenario.rent_rate_60_65),
    )

    events = result["events_person_level"]
    if events.empty:
        return set(), events

    ret = events[events["reason_code"] == REASON_RETIREMENT]
    retired_ids = {_norm_persnr(x) for x in ret["persnr"].tolist()}
    return retired_ids, ret


def run_renten_regler_tests(write_markdown: bool = False) -> list[dict]:
    df_ma = _build_population()

    scenarios = [
        Scenario("S1_00_00", 0.0, 0.0),
        Scenario("S2_10_00", 1.0, 0.0),
        Scenario("S3_00_10", 0.0, 1.0),
        Scenario("S4_10_10", 1.0, 1.0),
    ]

    out_rows: list[dict] = []

    for sc in scenarios:
        observed_ids, ret_events = _run_scenario(df_ma, sc)
        expected_ids = _expected_retired_ids(sc)

        # Kernvalidierung der zwei Regler
        assert observed_ids == expected_ids, (
            f"{sc.name}: retired_ids mismatch. expected={sorted(expected_ids)}, "
            f"observed={sorted(observed_ids)}"
        )

        # Zusaetzliche Schutzpruefung: U60 darf nie in Rente gehen
        invalid_under_60 = {_norm_persnr("3001"), _norm_persnr("3002")} & observed_ids
        assert not invalid_under_60, (
            f"{sc.name}: U60 unexpectedly retired: {sorted(invalid_under_60)}"
        )

        out_rows.append(
            {
                "scenario": sc.name,
                "rent_rate_65": sc.rent_rate_65,
                "rent_rate_60_65": sc.rent_rate_60_65,
                "expected_count": len(expected_ids),
                "observed_count": len(observed_ids),
                "expected_ids": ", ".join(sorted(expected_ids)) if expected_ids else "-",
                "observed_ids": ", ".join(sorted(observed_ids)) if observed_ids else "-",
                "event_rows": len(ret_events),
                "status": "PASS",
            }
        )

    if write_markdown:
        _write_markdown(out_rows)

    return out_rows


def _write_markdown(rows: list[dict]) -> None:
    lines = []
    lines.append("# Ergebnis: Renten-Regler Tests (Prognose Abgaenge)")
    lines.append("")
    lines.append(f"- Ausgefuehrt am: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("- Engine: `abgaenge.forecast.run_forecast_abgaenge`")
    lines.append("- Fokus: Regler `rent_rate_65` und `rent_rate_60_65`")
    lines.append("")
    lines.append("## Szenario-Ergebnisse")
    lines.append("")
    lines.append("| Szenario | rent_rate_65 | rent_rate_60_65 | Erwartet | Beobachtet | Status |")
    lines.append("|---|---:|---:|---|---|---|")

    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['rent_rate_65']:.2f} | {r['rent_rate_60_65']:.2f} | "
            f"{r['expected_count']} ({r['expected_ids']}) | {r['observed_count']} ({r['observed_ids']}) | {r['status']} |"
        )

    lines.append("")
    lines.append("## Kurzfazit")
    lines.append("")
    lines.append("- Die Rentenfunktion reagiert erwartungsgemaess auf beide Regler in den getesteten Grenzfaellen.")
    lines.append("- Bei `rent_rate_65=1.0` werden ausschliesslich 65+ Faelle gezogen (wenn `rent_rate_60_65=0.0`).")
    lines.append("- Bei `rent_rate_60_65=1.0` werden ausschliesslich 60-64 Faelle gezogen (wenn `rent_rate_65=0.0`).")
    lines.append("- Mitarbeiter unter 60 wurden in keinem Szenario als Rentenabgang verarbeitet.")

    RESULT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_renten_regler_szenarien() -> None:
    run_renten_regler_tests(write_markdown=False)


if __name__ == "__main__":
    run_renten_regler_tests(write_markdown=True)
    print(f"Markdown geschrieben: {RESULT_MD}")
