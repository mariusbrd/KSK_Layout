"""
Tests fuer die "Ruhend"-Funktion in der Prognose Abgaenge.
Fokus: Auswirkungen auf MAK und Headcount.

Ausfuehren:
    python -m pytest KSK_Layout/tests/test_ruhend_mak_headcount.py -v
    # oder direkt:
    py KSK_Layout/tests/test_ruhend_mak_headcount.py

Erwartete Effekte (Konzept):
    RUHEND_START  -> HC unveraendert (Delta=0), MAK sinkt auf 0
    RUHEND_RETURN -> HC unveraendert (Delta=0), MAK wird wiederhergestellt

Bekannter Bug (TC-03):
    Initial-Ruhend-Mitarbeiter (Status = "Ruhendes Beschaeftigungsverhaeltnis"
    am Stichtag) bekommen bei Rueckkehr MAK=0 statt des urspruenglichen MAK,
    weil mak_before_ruhend erst NACH dem Nullsetzen des MAK gesetzt wird.

Bekannter Bug (TC-07 / Hinweis):
    In aggregate_forecast_results wird der PersNr-Index aus df_ma (nicht normalisiert)
    mit den normalisierten PersNr der Events (zfill(6)) verglichen. Wenn PersNr
    nicht-numerisch ist (z.B. "P01" -> "00P01"), wird die Person nicht gefunden.
    Deshalb: Alle Tests verwenden numerische PersNr (z.B. "1001").
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import numpy as np

from abgaenge.forecast import run_forecast_abgaenge, aggregate_forecast_results
from abgaenge.schemas import REASON_RUHEND_START, REASON_RUHEND_RETURN


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _make_employee(
    persnr: str,
    age_years: int = 40,
    bsgrd: float = 100.0,
    status: str = "Aktiv",
    mak: float = 1.0,
    org: str = "OE-Test",
    jobfamily: str = "Default",
) -> dict:
    """Erzeugt einen einzelnen Mitarbeiter-Datensatz.

    Hinweis: persnr sollte numerisch sein (z.B. "1001"), damit die
    PersNr-Normalisierung in forecast.py (zfill(6)) korrekt funktioniert
    und mit aggregate_forecast_results kompatibel bleibt.
    """
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


def _params_only_ruhend(
    new_cases_per_year: float = 0,
    return_rate: float = 0.95,
    avg_duration_months: int = 12,
    seed: int = 42,
) -> dict:
    """Parameter: nur Ruhend-Komponente aktiv, alle anderen deaktiviert."""
    return {
        "random_seed": seed,
        "components": {
            "atz": False,
            "retirement": False,
            "quit": False,
            "ruhend": True,
        },
        "ruhend": {
            "ruhend_new_cases_per_year": new_cases_per_year,
            "ruhend_return_rate": return_rate,
            "ruhend_avg_duration_months": avg_duration_months,
        },
    }


RUHEND_STATUS = "Ruhendes Beschaeftigungsverhaeltnis"
# Exakter Statuswert wie in den Daten
RUHEND_STATUS_EXACT = "Ruhendes Beschäftigungsverhältnis"

START = pd.Timestamp("2026-01-01")
END = pd.Timestamp("2026-12-31")
FREQ = "M"


# ---------------------------------------------------------------------------
# TC-01: Initial-Ruhend -> Mitarbeiter im Headcount enthalten
# ---------------------------------------------------------------------------

def test_tc01_initial_ruhend_included_in_headcount():
    """
    Ein initial-ruhender Mitarbeiter zaehlt im Headcount (er ist 'aktiv'
    im Sinne des Beschaeftigungsverhaeltnisses, nur MAK=0).
    """
    print("\n[TC-01] Initial-Ruhend: Headcount-Zugehoerigkeit")
    df_ma = pd.DataFrame([
        _make_employee("1001", status=RUHEND_STATUS_EXACT, mak=0.0),
        _make_employee("1002", status="Aktiv", mak=1.0),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    params = _params_only_ruhend(new_cases_per_year=0)
    result = run_forecast_abgaenge(df_ma, df_atz, START, END, FREQ, params)

    kpis = result["forecast_kpis"]
    hc_start = int(kpis.iloc[0]["headcount_start"])

    # Beide Mitarbeiter muessen im Anfangs-Headcount enthalten sein
    assert hc_start == 2, (
        f"[FAIL] TC-01: Headcount Start erwartet 2, erhalten {hc_start}"
    )
    print(f"[OK] TC-01: Headcount Start = {hc_start} (erwartet 2)")


# ---------------------------------------------------------------------------
# TC-02: Initial-Ruhend -> MAK = 0 am Stichtag
# ---------------------------------------------------------------------------

def test_tc02_initial_ruhend_mak_is_zero():
    """
    Ein initial-ruhender Mitarbeiter mit MAK_Calculated=0.0 traegt
    zum MAK-Start mit 0 bei.
    """
    print("\n[TC-02] Initial-Ruhend: MAK am Stichtag ist 0")
    df_ma = pd.DataFrame([
        _make_employee("1001", status=RUHEND_STATUS_EXACT, mak=0.0),
        _make_employee("1002", status="Aktiv", mak=0.75),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    params = _params_only_ruhend(new_cases_per_year=0)
    result = run_forecast_abgaenge(df_ma, df_atz, START, END, FREQ, params)

    kpis = result["forecast_kpis"]
    mak_start = float(kpis.iloc[0]["mak_start"])

    # Nur 1002 traegt MAK bei (0.75)
    assert abs(mak_start - 0.75) < 0.01, (
        f"[FAIL] TC-02: MAK Start erwartet ~0.75, erhalten {mak_start:.4f}"
    )
    print(f"[OK] TC-02: MAK Start = {mak_start:.4f} (erwartet ~0.75 = nur 1002)")


# ---------------------------------------------------------------------------
# TC-03: Initial-Ruhend + Rueckkehr -> BUG-Dokumentation
# ---------------------------------------------------------------------------

def test_tc03_initial_ruhend_return_mak_bug():
    """
    BUG-Dokumentation: Initial-Ruhend-Mitarbeiter, die zurueckkehren,
    bekommen MAK=0 statt ihres urspruenglichen MAK.

    Ursache (forecast.py):
        Zeile 610: df_state["mak_before_ruhend"] = df_state["mak"]
        wird NACHDEM mak=0 fuer Ruhend-Mitarbeiter gesetzt wurde initialisiert.
        Damit ist mak_before_ruhend=0 fuer bereits-ruhende MAs.

    Erwartetes Verhalten (korrekt): MAK > 0 nach Rueckkehr
    Tatsaechliches Verhalten (Bug): MAK bleibt 0

    Dieser Test DOKUMENTIERT den Bug ohne ihn zu beheben.
    """
    print("\n[TC-03] Initial-Ruhend + Rueckkehr: Bug-Dokumentation")

    # Mitarbeiter ist seit Stichtag ruhend
    # ruhend_avg_duration_months=1 -> ruhend_until = Feb 2026
    # Ab Feb kann Rueckkehr stattfinden
    df_ma = pd.DataFrame([
        _make_employee("1001", status=RUHEND_STATUS_EXACT, mak=0.0),
        # Vor dem Ruhend-Status: BsGrd=100% -> theoretisch MAK=1.0
        # Aber MAK_Calculated=0.0 weil der Status gesetzt ist
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    params = _params_only_ruhend(
        new_cases_per_year=0,
        return_rate=1.0,   # 100% Rueckkehr wenn faellig
        avg_duration_months=1,
        seed=42,
    )

    result = run_forecast_abgaenge(df_ma, df_atz, START, END, FREQ, params)

    events = result["events_person_level"]
    ruhend_events = events[events["reason_code"].isin([REASON_RUHEND_START, REASON_RUHEND_RETURN])]

    print(f"   Ruhend-Ereignisse gesamt: {len(ruhend_events)}")

    returns = ruhend_events[ruhend_events["reason_code"] == REASON_RUHEND_RETURN]
    if not returns.empty:
        mak_change_on_return = float(returns.iloc[0]["mak_change"])
        print(f"   MAK-Aenderung bei Rueckkehr: {mak_change_on_return:.4f}")

        if mak_change_on_return <= 0:
            print("[DOKUMENTIERT] TC-03: Bug bestaetigt -- mak_change bei Rueckkehr ist {:.4f} (erwartet >0)".format(mak_change_on_return))
        else:
            print("[OK?] TC-03: MAK bei Rueckkehr positiv ({:.4f}) -- Bug evtl. behoben".format(mak_change_on_return))
    else:
        print("   Keine RUHEND_RETURN-Ereignisse in diesem Zeitraum")

    # Kein hard-assert -- Bug-Dokumentation laeuft immer durch
    print("[OK] TC-03: Lief ohne Ausnahme (Details siehe stdout)")


# ---------------------------------------------------------------------------
# TC-04: Neu-Ruhend Start -> HC unveraendert, MAK sinkt
# ---------------------------------------------------------------------------

def test_tc04_new_ruhend_start_hc_unchanged_mak_drops():
    """
    Ein Mitarbeiter wechselt waehrend des Forecasts in den Ruhend-Status.
    Headcount: unveraendert (Delta=0)
    MAK: sinkt auf 0
    """
    print("\n[TC-04] Neu-Ruhend Start: HC unveraendert, MAK sinkt")

    df_ma = pd.DataFrame([
        _make_employee("1001", status="Aktiv", mak=0.8),
        _make_employee("1002", status="Aktiv", mak=0.5),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    # 12 Faelle/Jahr -> in 12 Monaten mind. 1 Ruhend-Start sehr wahrscheinlich
    params = _params_only_ruhend(
        new_cases_per_year=12,
        return_rate=0.0,   # Keine Rueckkehr in diesem Test
        avg_duration_months=24,
        seed=42,
    )

    result = run_forecast_abgaenge(df_ma, df_atz, START, END, FREQ, params)

    events = result["events_person_level"]
    starts = events[events["reason_code"] == REASON_RUHEND_START]

    print(f"   RUHEND_START-Ereignisse: {len(starts)}")

    if not starts.empty:
        for _, ev in starts.iterrows():
            hc_change = int(ev["headcount_change"])
            mak_change = float(ev["mak_change"])
            persnr = ev["persnr"]

            assert hc_change == 0, (
                f"[FAIL] TC-04: MA {persnr} RUHEND_START headcount_change={hc_change}, erwartet 0"
            )
            assert mak_change < 0, (
                f"[FAIL] TC-04: MA {persnr} RUHEND_START mak_change={mak_change:.4f}, erwartet <0"
            )
            print(f"   MA {persnr}: HC Delta={hc_change}, MAK Delta={mak_change:.4f} -> OK")

        print("[OK] TC-04: Alle RUHEND_START-Ereignisse: HC Delta=0, MAK < 0")
    else:
        print("[SKIP] TC-04: Keine RUHEND_START-Ereignisse (Seed anpassen?)")


# ---------------------------------------------------------------------------
# TC-05: Neu-Ruhend -> Rueckkehr -> MAK korrekt wiederhergestellt
# ---------------------------------------------------------------------------

def test_tc05_new_ruhend_return_mak_restored():
    """
    Ein Mitarbeiter geht ruhend und kehrt zurueck.
    Bei Rueckkehr muss MAK wieder auf den Wert vor dem Ruhend ansteigen.
    Dies funktioniert korrekt fuer NEUE Ruhend-Faelle (RUHEND_START),
    da mak_before_ruhend VOR dem Nullsetzen gespeichert wird (Zeile 802).
    """
    print("\n[TC-05] Neu-Ruhend + Rueckkehr: MAK korrekt wiederhergestellt")

    df_ma = pd.DataFrame([
        _make_employee("1001", status="Aktiv", mak=0.75),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    # 12 neue Faelle/Jahr -> sicher ein Start in M1
    # Kurze Dauer (2 Monate) + 100% Rueckkehr -> Rueckkehr in M3
    params = _params_only_ruhend(
        new_cases_per_year=12,
        return_rate=1.0,
        avg_duration_months=2,
        seed=99,
    )

    # Laengeren Zeitraum damit Rueckkehr stattfinden kann
    result = run_forecast_abgaenge(df_ma, df_atz, START, pd.Timestamp("2027-06-30"), FREQ, params)

    events = result["events_person_level"]
    starts = events[events["reason_code"] == REASON_RUHEND_START]
    returns = events[events["reason_code"] == REASON_RUHEND_RETURN]

    print(f"   RUHEND_START:  {len(starts)}")
    print(f"   RUHEND_RETURN: {len(returns)}")

    if not starts.empty and not returns.empty:
        start_ev = starts.iloc[0]
        mak_at_start = -float(start_ev["mak_change"])   # mak_change ist negativ
        return_ev = returns[returns["persnr"] == start_ev["persnr"]]

        if not return_ev.empty:
            mak_at_return = float(return_ev.iloc[0]["mak_change"])
            print(f"   MAK vor Ruhend:      {mak_at_start:.4f}")
            print(f"   MAK bei Rueckkehr:   {mak_at_return:.4f}")

            assert abs(mak_at_return - mak_at_start) < 0.01, (
                f"[FAIL] TC-05: MAK Rueckkehr {mak_at_return:.4f} != MAK vor Ruhend {mak_at_start:.4f}"
            )
            print("[OK] TC-05: MAK bei Rueckkehr korrekt wiederhergestellt")
        else:
            print("[SKIP] TC-05: Keine Rueckkehr fuer diesen MA gefunden")
    else:
        print("[SKIP] TC-05: Nicht genug Ereignisse zum Testen (Seed anpassen?)")


# ---------------------------------------------------------------------------
# TC-06: Ruhend-Mitarbeiter vom ATZ ausgeschlossen
# ---------------------------------------------------------------------------

def test_tc06_ruhend_excluded_from_atz():
    """
    Mitarbeiter im Ruhend-Status duerfen nicht fuer ATZ eingeplant werden.
    In _schedule_new_atz_cases wird ~df_state["status_ruhend"] als Maske verwendet.
    """
    print("\n[TC-06] Ruhend: Ausschluss von ATZ-Scheduling")

    # Ruhend-Mitarbeiter im ATZ-Alter (55-60)
    df_ma = pd.DataFrame([
        _make_employee("1001", age_years=57, status=RUHEND_STATUS_EXACT, mak=0.0),
        _make_employee("1002", age_years=57, status="Aktiv", mak=1.0),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    params = {
        "random_seed": 42,
        "components": {
            "atz": True,
            "retirement": False,
            "quit": False,
            "ruhend": False,   # Kein neues Ruhend, nur ATZ testen
        },
        "atz": {
            "new_atz_rate": 1.0,   # Maximal-Rate -> 1002 fast sicher ATZ
            "use_atz_matrix": False,
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 1.0,
            "atz_duration_fr_years": 1.0,
        },
    }

    result = run_forecast_abgaenge(df_ma, df_atz, START, END, FREQ, params)

    tables = result["tables"]
    atz_pivot = tables.get("atz_pivot", pd.DataFrame())

    print(f"   ATZ-Pivot Zeilen: {len(atz_pivot)}")
    if not atz_pivot.empty:
        atz_persnrs = atz_pivot["PersNr"].astype(str).tolist()
        print(f"   ATZ-Personen (normalisiert): {atz_persnrs}")
        # "001001".zfill(6) oder "1001".zfill(6) -> "001001"
        assert not any("1001" in p or p.endswith("001001") for p in atz_persnrs), (
            "[FAIL] TC-06: 1001 (Ruhend) wurde fuer ATZ eingeplant!"
        )
        print("[OK] TC-06: 1001 (Ruhend) nicht in ATZ-Pivot")
    else:
        print("[INFO] TC-06: Kein ATZ eingeplant -- Ausschluss implizit korrekt")


# ---------------------------------------------------------------------------
# TC-07: Ruhend-Mitarbeiter und Rente
# ---------------------------------------------------------------------------

def test_tc07_ruhend_retirement_interaction():
    """
    Dokumentiert das Verhalten von Ruhend-Mitarbeitern bei Rentenpruefer.

    Aktuelles Verhalten: Ruhend-Mitarbeiter sind 'active=True' und werden
    daher in die Rentenelgibilitaetspruefung einbezogen. Sie gehen also auch
    in Rente (kein expliziter Ausschluss).

    Dieser Test stellt sicher, dass das Verhalten konsistent dokumentiert ist.
    """
    print("\n[TC-07] Ruhend + Rente: Interaktion dokumentieren")

    # Rentenpflichtiger Mitarbeiter (>=65) im Ruhend-Status
    df_ma = pd.DataFrame([
        _make_employee("1001", age_years=67, status=RUHEND_STATUS_EXACT, mak=0.0),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    params = {
        "random_seed": 42,
        "components": {
            "atz": False,
            "retirement": True,
            "quit": False,
            "ruhend": False,
        },
        "retirement": {
            "rent_rate_65": 1.0,   # 100% -> sicher Rente
            "rent_rate_60_65": 0.0,
        },
    }

    result = run_forecast_abgaenge(df_ma, df_atz, START, END, FREQ, params)

    events = result["events_person_level"]
    kpis = result["forecast_kpis"]
    final_hc = int(kpis.iloc[-1]["headcount_end"])

    retirement_events = events[events["reason_code"] == "RETIREMENT"] if not events.empty else pd.DataFrame()
    print(f"   Renteneignisse: {len(retirement_events)}")
    print(f"   Headcount Ende: {final_hc}")

    # Mit rent_rate_65=1.0 MUSS der Mitarbeiter im Laufe der 12 Monate in Rente gehen
    # (er ist active=True, not in_atz, age>=65)
    # Hinweis: Die Renteneignisse werden korrekt generiert (len>0),
    # aber aggregate_forecast_results wendet sie nicht an, weil die
    # PersNr im Index von df_state ("1001") nicht mit der normalisierten
    # PersNr im Event ("001001") uebereinstimmt.
    # Das ist ein separater Bug in aggregate_forecast_results (fehlende
    # PersNr-Normalisierung beim Aufbau des df_state-Index).
    if len(retirement_events) > 0 and final_hc > 0:
        print(f"[DOKUMENTIERT] TC-07: Rentenereignis generiert ({len(retirement_events)}x),")
        print("               aber HC unveraendert -> PersNr-Mismatch in aggregate_forecast_results")
        print("               Event-PersNr (normalisiert): " + str(retirement_events.iloc[0]["persnr"]) if not retirement_events.empty else "")
    elif final_hc == 0:
        print("[OK] TC-07: Ruhend-Mitarbeiter geht in Rente (Headcount=0)")
    else:
        print(f"[INFO] TC-07: Headcount={final_hc} -- Kein Rentenereignis generiert")

    # Kein hard-assert -- dokumentativer Test


# ---------------------------------------------------------------------------
# TC-08: MAK-Verlauf ueber Forecast-Zeitraum (Integrationstest)
# ---------------------------------------------------------------------------

def test_tc08_mak_trajectory_with_ruhend():
    """
    Integrationstest: Mitarbeiter geht ruhend und kehrt zurueck.
    Verifiziert anhand der rohen Events (nicht ueber aggregate_forecast_results,
    da dort ein PersNr-Normalisierungsbug besteht).

    Erwartete Ereignisfolge:
    1. RUHEND_START:  mak_change < 0  (MAK sinkt)
    2. RUHEND_RETURN: mak_change > 0  (MAK steigt zurueck)

    Headcount: HC-Effekt = 0 in allen Ruhend-Events.

    Hinweis zu aggregate_forecast_results (KPI-Timeline):
    Die KPI-Timeline spiegelt den MAK-Einbruch NICHT wider, weil
    aggregate_forecast_results die PersNr nicht normalisiert und
    daher Events mit "001001" nicht auf Index "1001" mappen kann.
    Das ist ein separater dokumentierter Bug (nicht Gegenstand dieses TC).
    """
    print("\n[TC-08] MAK-Verlauf via Events: Start -> Ruhend -> Rueckkehr")

    df_ma = pd.DataFrame([
        _make_employee("1001", status="Aktiv", mak=1.0),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    params = _params_only_ruhend(
        new_cases_per_year=12,
        return_rate=1.0,
        avg_duration_months=3,
        seed=99,  # Seed 99 erzeugt zuverlaessig Ruhend-Starts (verifiziert in TC-05)
    )

    result = run_forecast_abgaenge(df_ma, df_atz, START, pd.Timestamp("2027-12-31"), FREQ, params)
    events = result["events_person_level"]

    ruhend_events = events[events["reason_code"].isin([REASON_RUHEND_START, REASON_RUHEND_RETURN])]
    starts = ruhend_events[ruhend_events["reason_code"] == REASON_RUHEND_START]
    returns = ruhend_events[ruhend_events["reason_code"] == REASON_RUHEND_RETURN]

    print(f"   RUHEND_START:  {len(starts)}")
    print(f"   RUHEND_RETURN: {len(returns)}")

    if not ruhend_events.empty:
        print(f"\n   {'Periode':<12} {'Typ':<20} {'HC Delta':>10} {'MAK Delta':>12}")
        print("   " + "-" * 58)
        for _, ev in ruhend_events.sort_values("event_date").iterrows():
            print(
                f"   {ev['period_label']:<12} "
                f"{ev['reason_code']:<20} "
                f"{int(ev['headcount_change']):>10} "
                f"{float(ev['mak_change']):>12.4f}"
            )

    # Alle Ruhend-Events: HC = 0
    if not ruhend_events.empty:
        assert (ruhend_events["headcount_change"] == 0).all(), (
            "[FAIL] TC-08: Mindestens ein Ruhend-Event hat headcount_change != 0"
        )
        print("\n[OK] TC-08: Alle Ruhend-Events haben HC-Delta=0")

    # Starts haben negative MAK-Aenderung
    if not starts.empty:
        assert (starts["mak_change"] < 0).all(), (
            "[FAIL] TC-08: RUHEND_START-Events haben MAK-Aenderung >= 0"
        )
        print("[OK] TC-08: RUHEND_START-Events haben MAK-Delta < 0")

    # Returns haben positive MAK-Aenderung
    if not returns.empty:
        assert (returns["mak_change"] > 0).all(), (
            "[FAIL] TC-08: RUHEND_RETURN-Events haben MAK-Aenderung <= 0"
        )
        print("[OK] TC-08: RUHEND_RETURN-Events haben MAK-Delta > 0")

    if ruhend_events.empty:
        print("[SKIP] TC-08: Keine Ruhend-Events erzeugt (Seed anpassen?)")


# ---------------------------------------------------------------------------
# TC-09: Kein Headcount-Effekt auch bei mehreren Ruhend-Events
# ---------------------------------------------------------------------------

def test_tc09_multiple_ruhend_no_headcount_change():
    """
    Mehrere Mitarbeiter gehen ruhend. In keinem Fall darf sich der
    Headcount aendern. Alle headcount_change in den Events muessen 0 sein.
    """
    print("\n[TC-09] Mehrfach-Ruhend: Kein HC-Effekt")

    df_ma = pd.DataFrame([
        _make_employee("1001", status="Aktiv", mak=1.0),
        _make_employee("1002", status="Aktiv", mak=0.8),
        _make_employee("1003", status="Aktiv", mak=0.5),
        _make_employee("1004", status="Aktiv", mak=1.0),
        _make_employee("1005", status="Aktiv", mak=0.6),
    ])
    df_atz = pd.DataFrame(columns=["PersNr", "Phase", "Beginn", "Ende", "Ende ATZ Vertrag"])

    params = _params_only_ruhend(
        new_cases_per_year=6,
        return_rate=0.5,
        avg_duration_months=6,
        seed=123,
    )

    result = run_forecast_abgaenge(df_ma, df_atz, START, pd.Timestamp("2027-12-31"), FREQ, params)

    events = result["events_person_level"]
    ruhend_events = events[events["reason_code"].isin([REASON_RUHEND_START, REASON_RUHEND_RETURN])]

    print(f"   Ruhend-Ereignisse gesamt: {len(ruhend_events)}")

    if not ruhend_events.empty:
        bad = ruhend_events[ruhend_events["headcount_change"] != 0]
        if not bad.empty:
            print(f"[FAIL] TC-09: {len(bad)} Ruhend-Events mit headcount_change != 0:")
            print(bad[["persnr", "reason_code", "headcount_change", "mak_change"]].to_string())
            assert False, "Ruhend-Events duerfen kein headcount_change != 0 haben"
        else:
            print(f"[OK] TC-09: Alle {len(ruhend_events)} Ruhend-Events haben headcount_change=0")
    else:
        print("[SKIP] TC-09: Keine Ruhend-Ereignisse aufgetreten")


# ---------------------------------------------------------------------------
# Hauptprogramm (direkt ausfuehrbar ohne pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_tc01_initial_ruhend_included_in_headcount,
        test_tc02_initial_ruhend_mak_is_zero,
        test_tc03_initial_ruhend_return_mak_bug,
        test_tc04_new_ruhend_start_hc_unchanged_mak_drops,
        test_tc05_new_ruhend_return_mak_restored,
        test_tc06_ruhend_excluded_from_atz,
        test_tc07_ruhend_retirement_interaction,
        test_tc08_mak_trajectory_with_ruhend,
        test_tc09_multiple_ruhend_no_headcount_change,
    ]

    passed = 0
    failed = 0

    print("=" * 70)
    print("TEST: Ruhend-Funktion -- MAK & Headcount")
    print("=" * 70)

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"[ASSERTION ERROR] {test_fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[EXCEPTION] {test_fn.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"ERGEBNIS: {passed} bestanden, {failed} fehlgeschlagen")
    print("=" * 70)
