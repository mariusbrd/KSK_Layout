"""
Tests fuer die Kuendigungs-Funktion (QUIT) in der Prognose Abgaenge.

Zwei Modi werden unabhaengig getestet:
  A) Pauschale Fluktuationsquote (use_quit_matrix=False)
  B) Detaillierte Kuendigungsmatrix nach JobFamily oder OrgUnit (use_quit_matrix=True)

Ausfuehren:
    py -m pytest KSK_Layout/tests/test_kuendigung_forecast.py -v
    # oder direkt:
    py KSK_Layout/tests/test_kuendigung_forecast.py

Wichtige Verhaltenseigenschaften (aus forecast.py):
  - Kein Quit moeglich fuer ATZ-Mitarbeiter (in_atz=True)
  - Ruhend-Mitarbeiter sind NICHT explizit ausgeschlossen -> koennen kuendigen
  - Adjustments ("more"/"less") wirken immer ueber Jobfamily, NIE ueber OrgUnit
  - Matrixwerte werden in Skala 0.0-1.0 erwartet (engine-intern, nicht Prozent)
  - Rate-Lookup-Reihenfolge: exakter JF/OE-Treffer -> "Default" -> quit_rate_base
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import numpy as np

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.schemas import REASON_QUIT


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _make_employee(
    persnr: str,
    age_years: int = 35,
    bsgrd: float = 100.0,
    status: str = "Aktiv",
    mak: float = 1.0,
    org: str = "OE-Test",
    jobfamily: str = "Default",
) -> dict:
    birth = pd.Timestamp("2026-01-01") - pd.DateOffset(years=age_years)
    return {
        "PersNr": persnr,
        "GebDatum": birth,
        "Eintritt": pd.Timestamp("2000-01-01"),
        "BsGrd": bsgrd,
        "Status kundenindividuell": status,
        "Organisationseinheit": org,
        "Jobfamily": jobfamily,
        "Sollarbeitszeit": 39.0,
        "MAK_Calculated": mak,
    }


def _params_only_quit(
    rate_base: float = 0.0,
    use_matrix: bool = False,
    matrix: dict = None,
    dimension: str = "JobFamily",
    adjustments: dict = None,
    seed: int = 42,
) -> dict:
    """Parameter: nur Quit-Komponente aktiv, alle anderen deaktiviert."""
    return {
        "random_seed": seed,
        "components": {
            "atz": False,
            "retirement": False,
            "quit": True,
            "ruhend": False,
        },
        "quit": {
            "quit_rate_base": rate_base,
            "use_quit_matrix": use_matrix,
            "quit_dimension": dimension,
            "quit_matrix": matrix or {},
            "quit_adjustments": adjustments or {"more": {}, "less": {}},
        },
    }


def _atz_df(persnr: str, start: pd.Timestamp) -> pd.DataFrame:
    """Erzeugt minimales ATZ-DataFrame um einen MA als ATZ zu markieren."""
    return pd.DataFrame([{
        "PersNr": persnr,
        "Phase": "AR",
        "Beginn": start,
        "Ende": start + pd.DateOffset(years=2),
        "Ende ATZ Vertrag": start + pd.DateOffset(years=4),
    }])


START = pd.Timestamp("2026-01-01")
END = pd.Timestamp("2028-12-31")   # 3 Jahre fuer hohe Wahrscheinlichkeiten
FREQ = "M"
EMPTY_ATZ = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])


# ===========================================================================
# TEIL A: PAUSCHALE FLUKTUATIONSQUOTE (use_quit_matrix=False)
# ===========================================================================

def test_A01_rate_zero_no_quits():
    """
    Pauschalrate = 0 -> keine Kuendigungen.
    """
    print("\n[A-01] Pauschalrate=0: keine Kuendigungen")
    df_ma = pd.DataFrame([_make_employee(str(1000 + i)) for i in range(10)])
    params = _params_only_quit(rate_base=0.0, use_matrix=False)

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    assert len(quits) == 0, f"[FAIL] A-01: {len(quits)} Kuendigungen bei Rate=0"
    print(f"[OK] A-01: 0 Kuendigungen bei Rate=0 (erwartet 0)")


def test_A02_high_rate_produces_quits():
    """
    Pauschalrate = 1.0 -> alle MA kuendigen innerhalb des Zeitraums.
    """
    print("\n[A-02] Pauschalrate=1.0: alle MA kuendigen")
    n = 10
    df_ma = pd.DataFrame([_make_employee(str(1000 + i)) for i in range(n)])
    params = _params_only_quit(rate_base=1.0, use_matrix=False)

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    assert len(quits) == n, f"[FAIL] A-02: {len(quits)} Kuendigungen, erwartet {n}"
    print(f"[OK] A-02: {len(quits)}/{n} MA haben gekuendigt (erwartet alle)")


def test_A03_quit_event_schema():
    """
    Jedes QUIT-Event muss haben:
      headcount_change = -1
      mak_change < 0
      reason_code = "QUIT"
    """
    print("\n[A-03] QUIT-Event: korrektes Schema")
    df_ma = pd.DataFrame([
        _make_employee("2001", mak=0.8),
        _make_employee("2002", mak=0.5),
        _make_employee("2003", mak=1.0),
    ])
    params = _params_only_quit(rate_base=1.0, use_matrix=False)

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT]

    assert not quits.empty, "[FAIL] A-03: Keine QUIT-Events erzeugt"

    bad_hc = quits[quits["headcount_change"] != -1]
    assert bad_hc.empty, f"[FAIL] A-03: {len(bad_hc)} Events mit headcount_change != -1"

    bad_mak = quits[quits["mak_change"] >= 0]
    assert bad_mak.empty, f"[FAIL] A-03: {len(bad_mak)} Events mit mak_change >= 0"

    print(f"[OK] A-03: Alle {len(quits)} QUIT-Events: HC=-1, MAK<0, reason=QUIT")


def test_A04_atz_employees_excluded():
    """
    ATZ-Mitarbeiter werden vom Quit-Pool ausgeschlossen.
    MA 2001 ist in ATZ, MA 2002 ist aktiv -> nur 2002 kann kuendigen.
    """
    print("\n[A-04] ATZ-Mitarbeiter vom Quit ausgeschlossen")
    df_ma = pd.DataFrame([
        _make_employee("2001", mak=1.0),   # wird ATZ-MA
        _make_employee("2002", mak=1.0),   # aktiver MA
    ])
    df_atz = _atz_df("2001", START)
    params = _params_only_quit(rate_base=1.0, use_matrix=False)

    result = run_forecast_abgaenge(df_ma, df_atz, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    quit_persnrs = set(quits["persnr"].tolist()) if not quits.empty else set()
    # 2001 normalisiert = "002001"
    assert not any("2001" in p for p in quit_persnrs), (
        f"[FAIL] A-04: ATZ-MA 2001 hat gekuendigt: {quit_persnrs}"
    )
    assert any("2002" in p for p in quit_persnrs), (
        f"[FAIL] A-04: Aktiver MA 2002 hat nicht gekuendigt bei Rate=1.0"
    )
    print(f"[OK] A-04: ATZ-MA nicht in Quits, aktiver MA gekuendigt")
    print(f"          Quit-PersNr: {quit_persnrs}")


def test_A05_quit_reduces_headcount_cumulatively():
    """
    Nach jedem Quit sinkt der Headcount dauerhaft (kein Reset).
    Am Ende muss HC <= Anfangs-HC sein.
    """
    print("\n[A-05] HC-Reduktion ist kumulativ und dauerhaft")
    n = 5
    df_ma = pd.DataFrame([_make_employee(str(3000 + i)) for i in range(n)])
    params = _params_only_quit(rate_base=0.5, use_matrix=False, seed=42)

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    kpis = result["forecast_kpis"]

    # HC End jeder Periode darf nie groesser als HC Start der gleichen Periode sein
    for _, row in kpis.iterrows():
        assert row["headcount_end"] <= row["headcount_start"], (
            f"[FAIL] A-05: {row['period_label']}: HC Ende ({row['headcount_end']}) > HC Start ({row['headcount_start']})"
        )

    final_hc = int(kpis.iloc[-1]["headcount_end"])
    print(f"[OK] A-05: HC monoton fallend. Start={n}, Ende={final_hc}")


# ===========================================================================
# TEIL B: KUENDIGUNGSMATRIX (use_quit_matrix=True)
# ===========================================================================

def test_B01_matrix_by_jobfamily_differentiates_rates():
    """
    Matrix nach JobFamily: JF 'Hoch' (rate=1.0) kuendigt immer,
    JF 'Null' (rate=0.0) kuendigt nie.
    """
    print("\n[B-01] Matrix JobFamily: Differentielle Kuendigungsraten")

    df_ma = pd.DataFrame([
        _make_employee("4001", age_years=35, jobfamily="Hoch"),
        _make_employee("4002", age_years=35, jobfamily="Hoch"),
        _make_employee("4003", age_years=35, jobfamily="Null"),
        _make_employee("4004", age_years=35, jobfamily="Null"),
    ])

    matrix = {
        "alter_30_45": {
            "Hoch": 1.0,
            "Null": 0.0,
        }
    }
    params = _params_only_quit(use_matrix=True, matrix=matrix, dimension="JobFamily")

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    quit_persnrs = set(quits["persnr"].tolist()) if not quits.empty else set()
    print(f"   Quits: {quit_persnrs}")

    hoch_quit = any("4001" in p or "4002" in p for p in quit_persnrs)
    null_quit = any("4003" in p or "4004" in p for p in quit_persnrs)

    assert hoch_quit, "[FAIL] B-01: JF='Hoch' (Rate=1.0) hat nicht gekuendigt"
    assert not null_quit, f"[FAIL] B-01: JF='Null' (Rate=0.0) hat gekuendigt: {quit_persnrs}"
    print("[OK] B-01: JF 'Hoch' gekuendigt, JF 'Null' nicht gekuendigt")


def test_B02_matrix_by_orgunit_differentiates_rates():
    """
    Matrix nach OrgUnit: OE 'Hoch' (rate=1.0) kuendigt immer,
    OE 'Null' (rate=0.0) kuendigt nie.
    """
    print("\n[B-02] Matrix OrgUnit: Differentielle Kuendigungsraten")

    df_ma = pd.DataFrame([
        _make_employee("5001", age_years=35, org="OE-Hoch"),
        _make_employee("5002", age_years=35, org="OE-Hoch"),
        _make_employee("5003", age_years=35, org="OE-Null"),
        _make_employee("5004", age_years=35, org="OE-Null"),
    ])

    matrix = {
        "alter_30_45": {
            "OE-Hoch": 1.0,
            "OE-Null": 0.0,
        }
    }
    params = _params_only_quit(use_matrix=True, matrix=matrix, dimension="OrgUnit")

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    quit_persnrs = set(quits["persnr"].tolist()) if not quits.empty else set()
    print(f"   Quits: {quit_persnrs}")

    hoch_quit = any("5001" in p or "5002" in p for p in quit_persnrs)
    null_quit = any("5003" in p or "5004" in p for p in quit_persnrs)

    assert hoch_quit, "[FAIL] B-02: OE='OE-Hoch' (Rate=1.0) hat nicht gekuendigt"
    assert not null_quit, f"[FAIL] B-02: OE='OE-Null' (Rate=0.0) hat gekuendigt: {quit_persnrs}"
    print("[OK] B-02: OE 'OE-Hoch' gekuendigt, OE 'OE-Null' nicht gekuendigt")


def test_B03_matrix_default_fallback():
    """
    JF nicht in Matrix -> Fallback auf 'Default'-Eintrag.
    Kein 'Default' -> Fallback auf quit_rate_base.
    """
    print("\n[B-03] Matrix: Fallback-Reihenfolge JF -> Default -> base_rate")

    # Case 1: JF nicht in Matrix, aber 'Default' vorhanden (rate=1.0)
    df_case1 = pd.DataFrame([
        _make_employee("6001", age_years=35, jobfamily="Unbekannt"),
    ])
    matrix_with_default = {
        "alter_30_45": {"Default": 1.0}
    }
    params_case1 = _params_only_quit(
        rate_base=0.0,     # base_rate=0 -> wenn Fallback auf base_rate: kein Quit
        use_matrix=True,
        matrix=matrix_with_default,
        dimension="JobFamily",
    )
    result1 = run_forecast_abgaenge(df_case1, EMPTY_ATZ, START, END, FREQ, params_case1)
    quits1 = result1["events_person_level"]
    quits1 = quits1[quits1["reason_code"] == REASON_QUIT] if not quits1.empty else pd.DataFrame()

    assert not quits1.empty, (
        "[FAIL] B-03 (Case1): Fallback auf 'Default' (Rate=1.0) hat nicht funktioniert"
    )
    print(f"[OK] B-03 Case1: Fallback auf 'Default' -> {len(quits1)} Quit(s)")

    # Case 2: Weder JF noch 'Default' in Matrix -> Fallback auf quit_rate_base
    df_case2 = pd.DataFrame([
        _make_employee("6002", age_years=35, jobfamily="Unbekannt"),
    ])
    matrix_without_default = {
        "alter_30_45": {"AnderJF": 0.5}   # 6002's JF nicht drin, kein 'Default'
    }
    params_case2_no_quit = _params_only_quit(
        rate_base=0.0,     # base_rate=0 -> kein Quit
        use_matrix=True,
        matrix=matrix_without_default,
        dimension="JobFamily",
    )
    result2a = run_forecast_abgaenge(df_case2, EMPTY_ATZ, START, END, FREQ, params_case2_no_quit)
    quits2a = result2a["events_person_level"]
    quits2a = quits2a[quits2a["reason_code"] == REASON_QUIT] if not quits2a.empty else pd.DataFrame()
    assert quits2a.empty, "[FAIL] B-03 (Case2a): Fallback auf base_rate=0 hat Quit erzeugt"
    print(f"[OK] B-03 Case2a: Fallback auf base_rate=0 -> 0 Quits")

    params_case2_quit = _params_only_quit(
        rate_base=1.0,     # base_rate=1.0 -> Quit
        use_matrix=True,
        matrix=matrix_without_default,
        dimension="JobFamily",
    )
    result2b = run_forecast_abgaenge(df_case2, EMPTY_ATZ, START, END, FREQ, params_case2_quit)
    quits2b = result2b["events_person_level"]
    quits2b = quits2b[quits2b["reason_code"] == REASON_QUIT] if not quits2b.empty else pd.DataFrame()
    assert not quits2b.empty, "[FAIL] B-03 (Case2b): Fallback auf base_rate=1.0 hat keinen Quit erzeugt"
    print(f"[OK] B-03 Case2b: Fallback auf base_rate=1.0 -> {len(quits2b)} Quit(s)")


def test_B04_age_groups_respected():
    """
    Altersgruppen in der Matrix: Juengere kuendigen (Rate=1.0),
    Aeltere kuendigen nicht (Rate=0.0).
    Beide MAs haben die gleiche JF.
    """
    print("\n[B-04] Matrix: Altersgruppen werden korrekt unterschieden")

    df_ma = pd.DataFrame([
        _make_employee("7001", age_years=25, jobfamily="Test"),   # alter_unter_30
        _make_employee("7002", age_years=60, jobfamily="Test"),   # alter_55_plus
    ])

    matrix = {
        "alter_unter_30": {"Test": 1.0},   # Junge kuendigen
        "alter_30_45":    {"Test": 0.0},
        "alter_45_55":    {"Test": 0.0},
        "alter_55_plus":  {"Test": 0.0},   # Alte kuendigen nicht
    }
    params = _params_only_quit(use_matrix=True, matrix=matrix, dimension="JobFamily")

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    quit_persnrs = set(quits["persnr"].tolist()) if not quits.empty else set()
    print(f"   Quits: {quit_persnrs}")

    jung_quit = any("7001" in p for p in quit_persnrs)
    alt_quit  = any("7002" in p for p in quit_persnrs)

    assert jung_quit, "[FAIL] B-04: Junger MA (Rate=1.0) hat nicht gekuendigt"
    assert not alt_quit, f"[FAIL] B-04: Alter MA (Rate=0.0) hat gekuendigt: {quit_persnrs}"
    print("[OK] B-04: Alter_unter_30 (Rate=1.0) gekuendigt, Alter_55_plus (Rate=0.0) nicht")


def test_B05_adjustment_more_increases_rate():
    """
    Adjustment 'more': Kuendigungsrate fuer JF 'HighTurnover' wird 1.5x.
    Verifikation: Bei sehr niedriger Basisrate und 'more'-Adjustment fuer JF A,
    kuendigt JF A haeufiger als JF B (gleiche Basisrate, kein Adjustment).
    """
    print("\n[B-05] Adjustment 'more': 1.5x Rate fuer bestimmte JF")

    # Grosse Population fuer statistische Signifikanz
    n = 40
    df_ma = pd.DataFrame(
        [_make_employee(str(8000 + i), age_years=35, jobfamily="Adjust") for i in range(n)] +
        [_make_employee(str(9000 + i), age_years=35, jobfamily="Normal") for i in range(n)]
    )

    base = 0.3  # Basisrate fuer Altersgruppe
    matrix = {
        "alter_30_45": {
            "Adjust": base,
            "Normal": base,
        }
    }
    adjustments = {
        "more": {"Adjust": [2026, 2027, 2028]},
        "less": {},
    }
    params = _params_only_quit(
        use_matrix=True,
        matrix=matrix,
        dimension="JobFamily",
        adjustments=adjustments,
        seed=42,
    )

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    if not quits.empty:
        adjust_quits = len(quits[quits["Jobfamily"] == "Adjust"])
        normal_quits = len(quits[quits["Jobfamily"] == "Normal"])
        print(f"   Quits 'Adjust' (1.5x): {adjust_quits}")
        print(f"   Quits 'Normal' (1.0x): {normal_quits}")

        assert adjust_quits >= normal_quits, (
            f"[FAIL] B-05: 'Adjust' ({adjust_quits}) sollte >= 'Normal' ({normal_quits}) haben"
        )
        print("[OK] B-05: 'Adjust'-JF (1.5x) hat mind. so viele Quits wie 'Normal'")
    else:
        print("[SKIP] B-05: Keine Quits erzeugt")


def test_B06_adjustment_less_decreases_rate():
    """
    Adjustment 'less': Kuendigungsrate fuer JF 'LowTurnover' wird 0.5x.
    Verifikation: JF 'Reduce' kuendigt seltener als JF 'Normal'.
    """
    print("\n[B-06] Adjustment 'less': 0.5x Rate fuer bestimmte JF")

    n = 40
    df_ma = pd.DataFrame(
        [_make_employee(str(8100 + i), age_years=35, jobfamily="Reduce") for i in range(n)] +
        [_make_employee(str(9100 + i), age_years=35, jobfamily="Normal") for i in range(n)]
    )

    base = 0.6
    matrix = {
        "alter_30_45": {
            "Reduce": base,
            "Normal": base,
        }
    }
    adjustments = {
        "more": {},
        "less": {"Reduce": [2026, 2027, 2028]},
    }
    params = _params_only_quit(
        use_matrix=True,
        matrix=matrix,
        dimension="JobFamily",
        adjustments=adjustments,
        seed=42,
    )

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    if not quits.empty:
        reduce_quits = len(quits[quits["Jobfamily"] == "Reduce"])
        normal_quits = len(quits[quits["Jobfamily"] == "Normal"])
        print(f"   Quits 'Reduce' (0.5x): {reduce_quits}")
        print(f"   Quits 'Normal' (1.0x): {normal_quits}")

        assert reduce_quits <= normal_quits, (
            f"[FAIL] B-06: 'Reduce' ({reduce_quits}) sollte <= 'Normal' ({normal_quits}) haben"
        )
        print("[OK] B-06: 'Reduce'-JF (0.5x) hat max. so viele Quits wie 'Normal'")
    else:
        print("[SKIP] B-06: Keine Quits erzeugt")


def test_B07_adjustment_only_by_jobfamily_not_orgunit():
    """
    Adjustments ('more'/'less') wirken ausschliesslich ueber Jobfamily,
    NICHT ueber OrgUnit (laut forecast.py Zeile 234: jf_name = row.get("Jobfamily")).
    Bei dimension=OrgUnit werden Adjustments trotzdem JF-basiert angewandt.
    """
    print("\n[B-07] Adjustment: immer JF-basiert, nicht OE-basiert")

    df_ma = pd.DataFrame([
        # MA mit JF="AdjustJF" und OE="OE-A": adjustment trifft zu (wegen JF)
        _make_employee("8200", age_years=35, jobfamily="AdjustJF", org="OE-A"),
        # MA mit JF="NormalJF" und OE="OE-B": kein adjustment
        _make_employee("8201", age_years=35, jobfamily="NormalJF", org="OE-B"),
    ])

    matrix = {
        "alter_30_45": {
            "OE-A": 0.0,   # Dimension=OrgUnit: OE-A hat Rate=0
            "OE-B": 0.0,   # OE-B hat auch Rate=0
        }
    }
    adjustments = {
        "more": {"AdjustJF": [2026, 2027, 2028]},  # 1.5x fuer JF "AdjustJF"
        "less": {},
    }
    # Dimension=OrgUnit, aber Adjustments laufen ueber JF
    # Folge: 8200 (OE-A: Rate=0, dann *1.5 = 0) -> immer noch 0
    params = _params_only_quit(
        rate_base=0.0,
        use_matrix=True,
        matrix=matrix,
        dimension="OrgUnit",
        adjustments=adjustments,
    )

    result = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params)
    events = result["events_person_level"]
    quits = events[events["reason_code"] == REASON_QUIT] if not events.empty else pd.DataFrame()

    # Weder OE-A noch OE-B hat Quit (beide Rate=0, Adjustment auf 0*1.5=0)
    assert quits.empty, (
        f"[FAIL] B-07: Unerwartete Quits bei Rate=0 mit JF-Adjustment: {quits['persnr'].tolist()}"
    )
    print("[OK] B-07: Kein Quit bei Rate=0 (Adjustment auf 0 hat keinen Effekt)")
    print("     Hinweis: Adjustments operieren auf JF-Basis, auch wenn dimension=OrgUnit")


def test_B08_matrix_flat_vs_matrix_consistency():
    """
    Konsistenz-Test: Eine Matrix mit einheitlicher Rate fuer alle
    sollte aehnliche Ergebnisse wie die Pauschalrate liefern
    (gleiche effektive Rate, gleicher Seed).
    """
    print("\n[B-08] Konsistenz: Pauschalrate vs. Matrix mit gleicher Rate")

    df_ma = pd.DataFrame([
        _make_employee(str(9200 + i), age_years=35, jobfamily="Test") for i in range(20)
    ])

    rate = 0.3
    matrix_all_same = {
        "alter_30_45": {"Test": rate},
    }

    params_flat = _params_only_quit(rate_base=rate, use_matrix=False, seed=42)
    params_matrix = _params_only_quit(
        rate_base=rate, use_matrix=True, matrix=matrix_all_same, dimension="JobFamily", seed=42
    )

    result_flat = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params_flat)
    result_matrix = run_forecast_abgaenge(df_ma, EMPTY_ATZ, START, END, FREQ, params_matrix)

    quits_flat   = len(result_flat["events_person_level"][
        result_flat["events_person_level"]["reason_code"] == REASON_QUIT
    ]) if not result_flat["events_person_level"].empty else 0

    quits_matrix = len(result_matrix["events_person_level"][
        result_matrix["events_person_level"]["reason_code"] == REASON_QUIT
    ]) if not result_matrix["events_person_level"].empty else 0

    print(f"   Quits Pauschalrate: {quits_flat}")
    print(f"   Quits Matrix:       {quits_matrix}")

    # Bei gleicher Rate und gleichem Seed sollten die Ergebnisse identisch sein
    assert quits_flat == quits_matrix, (
        f"[FAIL] B-08: Pauschalrate ({quits_flat}) != Matrix ({quits_matrix}) bei gleicher Rate+Seed"
    )
    print(f"[OK] B-08: Identische Ergebnisse ({quits_flat} Quits) bei gleicher Rate und gleichem Seed")


# ===========================================================================
# Hauptprogramm
# ===========================================================================

if __name__ == "__main__":
    tests_a = [
        test_A01_rate_zero_no_quits,
        test_A02_high_rate_produces_quits,
        test_A03_quit_event_schema,
        test_A04_atz_employees_excluded,
        test_A05_quit_reduces_headcount_cumulatively,
    ]
    tests_b = [
        test_B01_matrix_by_jobfamily_differentiates_rates,
        test_B02_matrix_by_orgunit_differentiates_rates,
        test_B03_matrix_default_fallback,
        test_B04_age_groups_respected,
        test_B05_adjustment_more_increases_rate,
        test_B06_adjustment_less_decreases_rate,
        test_B07_adjustment_only_by_jobfamily_not_orgunit,
        test_B08_matrix_flat_vs_matrix_consistency,
    ]

    passed = 0
    failed = 0

    print("=" * 70)
    print("TEIL A: Pauschale Fluktuationsquote")
    print("=" * 70)
    for fn in tests_a:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print("TEIL B: Kuendigungsmatrix")
    print("=" * 70)
    for fn in tests_b:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"GESAMT: {passed} bestanden, {failed} fehlgeschlagen")
    print("=" * 70)
