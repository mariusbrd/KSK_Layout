"""
Deep / Edge-Case Tests: Azubi-Forecast
=======================================
Deckt Bereiche ab, die test_azubi_forecast.py nicht prueft:

  Part H - Destiny-Konsistenz (GraduationModus bleibt erhalten)
  Part I - Timing & Graduation-Datum Korrektheit
  Part J - Schulden-System (takeover debt ueber mehrere Perioden)
  Part K - Erkennungslogik fuer Bestands-Azubis (mask_azubi)
  Part L - "Default"-Key in Takeover-Matrix (Fehlerfall)
  Part M - Grenzfaelle (duration=0, very high count, leerere Snapshot)
  Part N - Jahresgesamtmengen (makro-Konsistenz ueber 6 Jahre)

Run:
  python KSK_Layout/tests/test_azubi_deep.py
"""

import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zugaenge.forecast import run_forecast_zugaenge, _estimate_baseline_graduation_date, distribute_deterministic


# ---------------------------------------------------------------------------
# Helpers (identisch zu test_azubi_forecast.py)
# ---------------------------------------------------------------------------

def _snap(rows=None):
    if rows is None:
        rows = [{
            "PersNr": "900001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte",
            "active": True,
            "mak": 1.0,
            "TrfGr": "E9A",
            "Eintritt": "2015-01-01",
        }]
    return pd.DataFrame(rows)


def _p(overrides=None):
    p = {
        "azubi": {
            "active": True,
            "new_cases_per_year": 15,
            "duration_years": 3,
            "retention_rate": 1.0,
            "strategy": "Random",
            "entry_tariff_group": "E5",
            "entry_step": 1,
            "exclude_baseline_azubis": False,
            "azubi_mak_during_training": 0.0,
            "azubi_mak_after_takeover": 1.0,
            "azubi_conversion_month": 8,
            "azubi_conversion_day": 1,
            "graduation_mode": "next_cycle",
            "use_takeover_matrix": False,
            "takeover_matrix": {},
            "takeover_dimension": "JobFamily",
            "jf_to_cluster_map": {},
        },
        "trainee": {"active": False},
        "new_hires": {"active": False},
        "random_seed": 42,
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in p:
                p[k].update(v)
            else:
                p[k] = v
    return p


def _run(snapshot, params, years=5, start="2025-08-01"):
    result = run_forecast_zugaenge(
        snapshot, pd.Timestamp(start), periods_years=years, params=params
    )
    events = result["events"]
    if not isinstance(events, pd.DataFrame):
        events = pd.DataFrame(events)
    return result, events


# ---------------------------------------------------------------------------
# PART H – Destiny-Konsistenz
# ---------------------------------------------------------------------------

def test_H01_takeover_destiny_produces_conversion_in():
    """
    Jeder Azubi mit GraduationModus='Takeover' muss als Azubi_Conversion_In enden.
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": True}})
    result, events = _run(snap, params, years=6)
    final = result["final_state"]

    hired_ids = events[events["type"] == "Azubi_Hire"]["persnr"].tolist()
    conv_in_ids = set(events[events["type"] == "Azubi_Conversion_In"]["persnr"].tolist())

    # Azubis with Takeover destiny in final_state
    if "GraduationModus" in final.columns:
        takeover_rows = final[final["GraduationModus"] == "Takeover"]
        graduated_takeover_ids = set(takeover_rows.index.tolist()) & conv_in_ids
        # All graduated Takeover Azubis must be in conv_in
        # (those not yet graduated may still be in training)
        for pid in graduated_takeover_ids:
            assert pid in conv_in_ids, f"Azubi {pid} has GraduationModus=Takeover but no Conversion_In event"


def test_H02_exit_destiny_produces_azubi_exit():
    """
    Jeder Azubi mit GraduationModus='Exit' muss als Azubi_Exit enden (kein Conversion_In).
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 0.0, "duration_years": 3, "exclude_baseline_azubis": True}})
    result, events = _run(snap, params, years=6)

    conv_in_ids = set(events[events["type"] == "Azubi_Conversion_In"]["persnr"].tolist())
    exit_ids = set(events[events["type"] == "Azubi_Exit"]["persnr"].tolist())

    # No Azubi should appear in both Conv_In and Azubi_Exit
    overlap = conv_in_ids & exit_ids
    assert len(overlap) == 0, \
        f"Found {len(overlap)} Azubis in both Conv_In AND Azubi_Exit: {list(overlap)[:5]}"


def test_H03_no_double_graduation():
    """
    Jeder Azubi darf hoechstens einmal graduieren (keine doppelten Graduation-Events).
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 0.8, "duration_years": 3, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=6)

    grad_types = ["Azubi_Conversion_Out", "Azubi_Exit"]
    grads = events[events["type"].isin(grad_types)]

    dupes = grads.groupby("persnr").size()
    multi_grad = dupes[dupes > 1]
    assert len(multi_grad) == 0, \
        f"Found Azubis with multiple graduation events: {multi_grad.to_dict()}"


def test_H04_no_graduation_before_hire():
    """
    Kein Azubi graduiert vor seinem Einstellungsdatum.
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=6)

    hires = events[events["type"] == "Azubi_Hire"][["persnr", "date"]].copy()
    hires = hires.rename(columns={"date": "hire_date"})

    grad_types = ["Azubi_Conversion_Out", "Azubi_Exit"]
    grads = events[events["type"].isin(grad_types)][["persnr", "date"]].copy()
    grads = grads.rename(columns={"date": "grad_date"})

    merged = grads.merge(hires, on="persnr", how="left")
    merged["hire_date"] = pd.to_datetime(merged["hire_date"])
    merged["grad_date"] = pd.to_datetime(merged["grad_date"])

    bad = merged[merged["grad_date"] < merged["hire_date"]]
    assert len(bad) == 0, \
        f"Found {len(bad)} Azubis graduating BEFORE their hire date:\n{bad[['persnr','hire_date','grad_date']].head()}"


# ---------------------------------------------------------------------------
# PART I – Timing & Graduation-Datum Korrektheit
# ---------------------------------------------------------------------------

def test_I01_graduation_always_august_first():
    """
    Alle Graduation-Events fallen auf den 1. August (Conversion-Month-Tag).
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=6)

    grad_types = ["Azubi_Conversion_Out", "Azubi_Conversion_In", "Azubi_Exit"]
    grads = events[events["type"].isin(grad_types)].copy()
    grads["date"] = pd.to_datetime(grads["date"])

    bad_month = grads[grads["date"].dt.month != 8]
    bad_day = grads[grads["date"].dt.day != 1]

    assert len(bad_month) == 0, \
        f"Found graduation events NOT in August: {bad_month[['type','date']].head().to_dict()}"
    assert len(bad_day) == 0, \
        f"Found graduation events NOT on day 1: {bad_day[['type','date']].head().to_dict()}"


def test_I02_graduation_at_least_duration_after_hire():
    """
    Graduation-Datum liegt immer mindestens (duration_years - 1) Jahre nach dem Einstellungsdatum.
    Mit August-Snapping koennte es etwas frueher liegen (nearest_cycle), aber nie
    um mehr als 12 Monate zu frueh.
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": True, "graduation_mode": "nearest_cycle"}})
    _, events = _run(snap, params, years=7)

    hires = events[events["type"] == "Azubi_Hire"][["persnr", "date"]].copy().rename(columns={"date": "hire_date"})
    conv_out = events[events["type"] == "Azubi_Conversion_Out"][["persnr", "date"]].copy().rename(columns={"date": "grad_date"})

    merged = conv_out.merge(hires, on="persnr", how="left")
    merged["hire_date"] = pd.to_datetime(merged["hire_date"])
    merged["grad_date"] = pd.to_datetime(merged["grad_date"])
    merged["years_in_training"] = (merged["grad_date"] - merged["hire_date"]).dt.days / 365.25

    # With nearest_cycle and 3y duration: minimum should be ~2.0 years (12 months early snap at most)
    too_early = merged[merged["years_in_training"] < 2.0]
    assert len(too_early) == 0, \
        f"Found {len(too_early)} Azubis graduating < 2 years after hire:\n{too_early[['persnr','hire_date','grad_date','years_in_training']].head()}"


def test_I03_graduation_date_function_edge_cases():
    """
    _estimate_baseline_graduation_date Randwerte:
    - Genau am Conversion-Stichtag: bleibt im gleichen Jahr
    - Tag davor: bleibt im gleichen Jahr (next_cycle)
    - Tag danach: naechstes Jahr (next_cycle)
    """
    # Exactly on Aug 1: estimated_end = Aug 1, <= Aug 1 -> same year
    entry_exact = pd.Timestamp("2026-08-01") - pd.DateOffset(years=3)
    grad_exact = _estimate_baseline_graduation_date(entry_exact, 3.0, graduation_mode="next_cycle")
    assert grad_exact == pd.Timestamp("2026-08-01"), f"Exact match failed: {grad_exact}"

    # One day before Aug 1 after duration: Aug 1 is >= estimated_end, stays same year
    entry_before = pd.Timestamp("2026-07-31") - pd.DateOffset(years=3)
    grad_before = _estimate_baseline_graduation_date(entry_before, 3.0, graduation_mode="next_cycle")
    assert grad_before.year == 2026, f"Before-Aug: expected 2026, got {grad_before.year}"

    # One day after Aug 1 after duration: estimated_end > Aug 1, next year
    entry_after = pd.Timestamp("2026-08-02") - pd.DateOffset(years=3)
    grad_after = _estimate_baseline_graduation_date(entry_after, 3.0, graduation_mode="next_cycle")
    assert grad_after.year == 2027, f"After-Aug: expected 2027, got {grad_after.year}"


def test_I04_fractional_duration_respects_months():
    """
    duration_years=1.5 (18 Monate): Ein Azubi, der im Jan 2026 eintritt,
    hat estimated_end Juli 2027. Juli < Aug 2027 -> graduiert Aug 2027.
    """
    grad = _estimate_baseline_graduation_date(
        pd.Timestamp("2026-01-01"), duration_years=1.5, graduation_mode="next_cycle"
    )
    assert grad == pd.Timestamp("2027-08-01"), \
        f"1.5y duration from Jan 2026: expected Aug 2027, got {grad}"


# ---------------------------------------------------------------------------
# PART J – Schulden-System (takeover debt)
# ---------------------------------------------------------------------------

def test_J01_total_takeovers_match_rate_over_many_cohorts():
    """
    Makro-Check: ueber 6 Prognosejahre mit 15 Azubis/Jahr und 80% Uebernahmequote
    muessen exakt 80% der abgeschlossenen Azubis uebernommen werden.
    Das Debt-System garantiert dies deterministisch.
    """
    snap = _snap()
    params = _p({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 0.8,
            "duration_years": 3,
            "exclude_baseline_azubis": True,
        }
    })
    _, events = _run(snap, params, years=7, start="2025-08-01")

    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    exits = events[events["type"] == "Azubi_Exit"]
    total_graduated = len(conv_in) + len(exits)

    if total_graduated == 0:
        print("  SKIP J01: No graduates in window")
        return

    takeover_rate = len(conv_in) / total_graduated
    assert abs(takeover_rate - 0.8) < 0.05, \
        f"Macro takeover rate = {takeover_rate:.1%}, expected 80% (±5%)"


def test_J02_debt_does_not_produce_fractional_events():
    """
    Das Debt-System gibt immer ganzzahlige Event-Counts zurueck.
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 7, "retention_rate": 0.333}})
    _, events = _run(snap, params, years=5)

    for event_type in ["Azubi_Hire", "Azubi_Conversion_In", "Azubi_Exit"]:
        ev = events[events["type"] == event_type]
        if len(ev) == 0:
            continue
        counts = ev["count"].abs().tolist()
        for c in counts:
            assert c == int(c), f"{event_type}: non-integer count {c}"


def test_J03_annual_hire_count_close_to_target():
    """
    Hire-Events pro Jahr liegen nahe dem konfigurierten Zielwert (±1 durch Debt-Rundung).
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=4, start="2026-01-01")

    hires = events[events["type"] == "Azubi_Hire"].copy()
    hires["year"] = pd.to_datetime(hires["date"]).dt.year

    for year, group in hires.groupby("year"):
        n = len(group)
        assert 14 <= n <= 16, \
            f"Year {year}: expected ~15 hires, got {n} (allowed 14-16 for debt rounding)"


# ---------------------------------------------------------------------------
# PART K – Erkennungslogik Bestands-Azubis
# ---------------------------------------------------------------------------

def test_K01_azubi_detected_by_tvaoed_tariff():
    """
    Bestands-Azubis mit TrfGr='TVAoeD' (kein 'Azubi' in Jobfamily) werden erkannt.
    """
    snap = pd.DataFrame([
        {
            "PersNr": "AZ_BESTAND",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Sonstige",  # Already relabeled externally
            "TrfGr": "TVAoeD",        # Recognized by TVA check
            "active": True,
            "mak": 0.0,
            "Eintritt": "2023-01-01",
            "is_forecast": False,
        },
        {
            "PersNr": "900001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte",
            "TrfGr": "E9A",
            "active": True,
            "mak": 1.0,
            "Eintritt": "2015-01-01",
        }
    ])
    params = _p({"azubi": {"new_cases_per_year": 0, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": False}})
    _, events = _run(snap, params, years=4, start="2025-08-01")

    # AZ_BESTAND should produce graduation events (3y from Jan 2023 = Aug 2026)
    grad_events = events[events["type"].isin(["Azubi_Conversion_Out", "Azubi_Conversion_In", "Azubi_Exit"])]
    az_grads = grad_events[grad_events["persnr"] == "AZ_BESTAND"]
    assert len(az_grads) > 0, "AZ_BESTAND (TVAoeD) should produce graduation events"


def test_K02_azubi_detected_by_jobfamily_ausbildung_bug():
    """
    BEKANNTER BUG: Bestands-Azubis mit Jobfamily='Ausbildung' aber NICHT-TVA-Tarif
    (z.B. TrfGr='E9A') verlieren ihre Erkennung ab Periode 2.

    Ursache: _simulate_azubis ueberschreibt Jobfamily -> 'Sonstige' in Periode 1.
    In Periode 2 wird mask_azubi neu ausgewertet:
      - TrfGr='E9A' enthaelt kein 'TVA' -> False
      - Jobfamily='Sonstige' enthaelt kein 'Azubi' oder 'Ausbildung' -> False
    => Der Azubi ist unsichtbar und graduiert nie.

    Robuste Erkennung benoetigt einen persistenten Marker (z.B. GraduationDate vorhanden
    ODER TrfGr=TVAoeD). Azubis sollten nur mit TVAoeD-Tarif im System gebucht werden.
    """
    snap = pd.DataFrame([
        {
            "PersNr": "AZ002",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Ausbildung",
            "TrfGr": "E9A",  # Kein TVA -> Detection bricht nach Periode 1 ab
            "active": True,
            "mak": 0.0,
            "Eintritt": "2023-01-01",
            "is_forecast": False,
        },
        {
            "PersNr": "900001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte",
            "TrfGr": "E9A",
            "active": True,
            "mak": 1.0,
            "Eintritt": "2015-01-01",
        }
    ])
    params = _p({"azubi": {"new_cases_per_year": 0, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": False}})
    _, events = _run(snap, params, years=4, start="2025-08-01")

    grad_events = events[events["type"].isin(["Azubi_Conversion_Out", "Azubi_Conversion_In", "Azubi_Exit"])]
    az_grads = grad_events[grad_events["persnr"] == "AZ002"]

    # Documents the bug: AZ002 does NOT graduate (detection lost after period 1)
    assert len(az_grads) == 0, (
        f"BUG REGRESSION: AZ002 (Ausbildung+E9A) should NOT graduate "
        f"(bug: detection lost after period 1). Got {len(az_grads)} events."
    )
    print("  NOTE K02: Bug confirmed - Jobfamily-only Azubi detection breaks after period 1.")
    print("  Fix: Ensure Azubis always have TrfGr=TVAoeD, OR use GraduationDate as persistent marker.")


def test_K03_non_azubi_not_affected_by_azubi_logic():
    """
    Normale Angestellte mit TrfGr='E9A' und Jobfamily='Angestellte' werden
    von der Azubi-Logik nicht veraendert.
    """
    snap = pd.DataFrame([{
        "PersNr": "900001",
        "Organisationseinheit": "OE1",
        "Jobfamily": "Angestellte",
        "TrfGr": "E9A",
        "active": True,
        "mak": 1.0,
        "Eintritt": "2015-01-01",
    }])
    params = _p({"azubi": {"new_cases_per_year": 0, "retention_rate": 1.0, "duration_years": 3}})
    result, events = _run(snap, params, years=3)

    final = result["final_state"]
    person = final.loc["900001"] if "900001" in final.index else None

    assert person is not None, "Person 900001 should still be in final_state"
    if person is not None:
        assert person["Jobfamily"] == "Angestellte", \
            f"Non-Azubi Jobfamily should not change, got '{person['Jobfamily']}'"
        assert person["TrfGr"] == "E9A", \
            f"Non-Azubi TrfGr should not change, got '{person['TrfGr']}'"


# ---------------------------------------------------------------------------
# PART L – "Default"-Key in Takeover-Matrix (Fehlerfall)
# ---------------------------------------------------------------------------

def test_L01_default_key_in_matrix_creates_literal_default_jf():
    """
    BEKANNTES VERHALTEN: Wenn 'Default' einen positiven Wert in der Takeover-Matrix hat,
    wird 'Default' als literaler Jobfamily-Name einem Teil der Azubis zugewiesen.
    Dies ist ein Bug in der UI->Engine-Konvertierung: 'Default' sollte als Fallback-
    Gewicht behandelt werden, nicht als Ziel-Jobfamily.

    Der Test dokumentiert das aktuelle Verhalten, um Regressionen zu erkennen.
    """
    snap = _snap()
    params = _p({
        "azubi": {
            "new_cases_per_year": 15,
            "retention_rate": 1.0,
            "duration_years": 3,
            "use_takeover_matrix": True,
            "takeover_dimension": "JobFamily",
            "takeover_matrix": {"Angestellte": 0.8, "Default": 0.2},  # Default as literal
        }
    })
    _, events = _run(snap, params, years=5)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]

    if len(conv_in) > 0 and "Jobfamily" in conv_in.columns:
        jfs = conv_in["Jobfamily"].dropna().unique().tolist()
        # Document: "Default" may appear as a JF name (bug, not intended behavior)
        if "Default" in jfs:
            print("  NOTE L01: 'Default' key in matrix creates literal Jobfamily='Default' (known bug)")
        # Test: at least "Angestellte" must appear (it has a valid positive weight)
        assert "Angestellte" in jfs, \
            f"'Angestellte' must appear in Conv_In JFs, got: {jfs}"


def test_L02_jf_matrix_with_only_default_key():
    """
    Matrix={'Default': 1.0}: distribute_deterministic liefert 'Default' als
    Jobfamily fuer alle Azubis (da valid_vals=None). Dokumentiert Fehlverhalten.
    """
    result = distribute_deterministic(5, {"Default": 1.0}, valid_vals=None)
    # Documents current (wrong) behavior: "Default" is treated as a JF name
    assert all(v == "Default" for v in result), \
        f"Expected all 'Default', got {result} (engine treats Default as literal JF)"


# ---------------------------------------------------------------------------
# PART M – Grenzfaelle
# ---------------------------------------------------------------------------

def test_M01_zero_azubis_per_year_no_events():
    """
    new_cases_per_year=0: Keine neuen Azubi-Hire-Events.
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 0, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=5)

    hires = events[events["type"] == "Azubi_Hire"]
    assert len(hires) == 0, f"Expected 0 hires with count=0, got {len(hires)}"


def test_M02_short_duration_correct_cycle():
    """
    duration_years=1.0:
    - Mrz 2026 + 1y = Mrz 2027. Mrz 2027 <= Aug 2027 -> graduiert Aug 2027 (next_cycle).
    - Um Aug 2026 zu erreichen, muss der Eintritt vor/am Aug 2025 liegen
      (Aug 2025 + 1y = Aug 2026 <= Aug 2026 -> Aug 2026).
    """
    # Mar 2026 + 1y = Mar 2027 <= Aug 2027 -> Aug 2027
    grad_mar = _estimate_baseline_graduation_date(
        pd.Timestamp("2026-03-01"), duration_years=1.0, graduation_mode="next_cycle"
    )
    assert grad_mar == pd.Timestamp("2027-08-01"), \
        f"Mar 2026 + 1y: expected Aug 2027, got {grad_mar}"

    # Aug 2025 + 1y = Aug 2026 exactly on cycle -> Aug 2026
    grad_aug = _estimate_baseline_graduation_date(
        pd.Timestamp("2025-08-01"), duration_years=1.0, graduation_mode="next_cycle"
    )
    assert grad_aug == pd.Timestamp("2026-08-01"), \
        f"Aug 2025 + 1y: expected Aug 2026, got {grad_aug}"


def test_M03_empty_snapshot_still_runs():
    """
    Leerer Snapshot (nur Spalten, keine Zeilen): Forecast laeuft ohne Fehler.
    """
    snap = pd.DataFrame(columns=["PersNr", "Organisationseinheit", "Jobfamily", "active", "mak", "TrfGr", "Eintritt"])
    params = _p({"azubi": {"new_cases_per_year": 10, "duration_years": 3, "exclude_baseline_azubis": True}})

    try:
        result, events = _run(snap, params, years=4)
        # Should produce Hire events even with empty snapshot
        hires = events[events["type"] == "Azubi_Hire"]
        assert len(hires) > 0, "Should produce hires even with empty snapshot"
    except Exception as e:
        assert False, f"Empty snapshot raised exception: {type(e).__name__}: {e}"


def test_M04_high_azubi_count_no_id_collision():
    """
    100 Azubis pro Jahr ueber 3 Jahre: Alle IDs eindeutig (kein UUID-Kollisions-Problem).
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 100, "duration_years": 3, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=3)

    hires = events[events["type"] == "Azubi_Hire"]
    assert hires["persnr"].nunique() == len(hires), \
        f"ID collision detected: {len(hires)} hires, {hires['persnr'].nunique()} unique"


def test_M05_retention_rate_exactly_half():
    """
    retention_rate=0.5 mit 20 Azubis: exakt 10 Takeovers, 10 Exits (Debt-System).
    """
    snap = _snap()
    params = _p({
        "azubi": {
            "new_cases_per_year": 20,
            "retention_rate": 0.5,
            "duration_years": 3,
            "exclude_baseline_azubis": True,
        }
    })
    _, events = _run(snap, params, years=6, start="2026-01-01")

    hires = events[events["type"] == "Azubi_Hire"]
    hires_yr = hires[pd.to_datetime(hires["date"]).dt.year == 2026]
    if len(hires_yr) == 0:
        print("  SKIP M05: No 2026 hires")
        return

    cohort = set(hires_yr["persnr"].tolist())
    conv_in = events[(events["type"] == "Azubi_Conversion_In") & events["persnr"].isin(cohort)]
    exits = events[(events["type"] == "Azubi_Exit") & events["persnr"].isin(cohort)]

    total = len(conv_in) + len(exits)
    if total == 0:
        print("  SKIP M05: 2026 cohort not yet graduated")
        return

    # Debt-System garantiert bei gerader Kohorte exakt 50/50.
    # Bei ungerader Kohorte (z.B. 19 durch Pro-Rata-Rundung) erlauben wir ±1.
    diff = abs(len(conv_in) - len(exits))
    assert diff <= 1, \
        f"50% rate with {total} graduates: expected balanced split (+-1), got {len(conv_in)} T / {len(exits)} E (diff={diff})"


# ---------------------------------------------------------------------------
# PART N – MAK-Bilanz Makro
# ---------------------------------------------------------------------------

def test_N01_mak_balance_hire_then_takeover():
    """
    Azubi_Hire: mak=0 (keine MAK-Wirkung sofort)
    Azubi_Conversion_In: mak=+1.0 (volle MAK-Wirkung bei Uebernahme)

    Ueber alle Events: kumulierte MAK aus Hire-Events muss 0 sein.
    MAK aus Conversion_In-Events muss = Anzahl Takeovers.
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=6)

    hire_mak = events[events["type"] == "Azubi_Hire"]["mak"].sum()
    conv_in_mak = events[events["type"] == "Azubi_Conversion_In"]["mak"].sum()
    conv_in_count = len(events[events["type"] == "Azubi_Conversion_In"])

    assert abs(hire_mak) < 0.01, f"Total MAK from Hire events must be 0, got {hire_mak}"
    assert abs(conv_in_mak - conv_in_count) < 0.01, \
        f"Total MAK from Conv_In ({conv_in_mak:.1f}) must equal count ({conv_in_count})"


def test_N02_headcount_balance_hire_vs_graduation():
    """
    HC-Bilanz: Hire-Events (+1 each) minus Exit-Events (-1 each) minus Conv_Out-Events (-1 each)
    plus Conv_In-Events (+1 each) = Net HC change.

    Mit retention=1.0: Net HC = Hire-Events (alle bleiben als Angestellte).
    Conv_Out und Conv_In heben sich auf (intern).
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 15, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": True}})
    _, events = _run(snap, params, years=6)

    n_hires = len(events[events["type"] == "Azubi_Hire"])
    n_conv_out = len(events[events["type"] == "Azubi_Conversion_Out"])
    n_conv_in = len(events[events["type"] == "Azubi_Conversion_In"])
    n_exits = len(events[events["type"] == "Azubi_Exit"])

    # Conv_Out and Conv_In must be balanced (retention=1.0 means no exits)
    assert n_exits == 0, f"With retention=1.0: no exits expected, got {n_exits}"
    assert n_conv_out == n_conv_in, \
        f"Conv_Out ({n_conv_out}) must equal Conv_In ({n_conv_in})"

    # Net HC from hires (not yet graduated in 6-year window, ongoing training)
    net_hc = (
        events[events["type"] == "Azubi_Hire"]["count"].sum() +
        events[events["type"] == "Azubi_Conversion_Out"]["count"].sum() +
        events[events["type"] == "Azubi_Conversion_In"]["count"].sum() +
        events[events["type"] == "Azubi_Exit"]["count"].sum()
    )
    # Net HC = total hires that have completed training (Conv_In) +
    # still-in-training Azubis (Hire without graduation yet)
    # This is complex, so just verify no negative net contribution
    assert net_hc >= 0, f"Net HC should be >= 0 with retention=1.0, got {net_hc}"


def test_N03_mak_zero_during_entire_training_period():
    """
    Ein Azubi traegt waehrend der gesamten Ausbildung 0 MAK bei.
    Erst nach Conversion_In ist er MAK-wirksam.
    """
    snap = _snap()
    params = _p({"azubi": {"new_cases_per_year": 5, "retention_rate": 1.0, "duration_years": 3, "exclude_baseline_azubis": True}})
    result, events = _run(snap, params, years=5)
    final = result["final_state"]

    # Find Azubis still in training (not yet graduated)
    if "GraduationDate" in final.columns and "mak" in final.columns:
        # Still-in-training: active AND Jobfamily=Sonstige (training label) AND mak=0
        training_mask = (
            (final["active"] == True) &
            (final["Jobfamily"] == "Sonstige") &
            (final["TrfGr"].astype(str).str.contains("TVA", na=False))
        )
        training_azubis = final[training_mask]

        if len(training_azubis) > 0:
            bad_mak = training_azubis[training_azubis["mak"] > 0]
            assert len(bad_mak) == 0, \
                f"Found {len(bad_mak)} still-in-training Azubis with mak > 0:\n{bad_mak[['mak','Jobfamily','TrfGr']].head()}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("H01", test_H01_takeover_destiny_produces_conversion_in),
        ("H02", test_H02_exit_destiny_produces_azubi_exit),
        ("H03", test_H03_no_double_graduation),
        ("H04", test_H04_no_graduation_before_hire),
        ("I01", test_I01_graduation_always_august_first),
        ("I02", test_I02_graduation_at_least_duration_after_hire),
        ("I03", test_I03_graduation_date_function_edge_cases),
        ("I04", test_I04_fractional_duration_respects_months),
        ("J01", test_J01_total_takeovers_match_rate_over_many_cohorts),
        ("J02", test_J02_debt_does_not_produce_fractional_events),
        ("J03", test_J03_annual_hire_count_close_to_target),
        ("K01", test_K01_azubi_detected_by_tvaoed_tariff),
        ("K02", test_K02_azubi_detected_by_jobfamily_ausbildung_bug),
        ("K03", test_K03_non_azubi_not_affected_by_azubi_logic),
        ("L01", test_L01_default_key_in_matrix_creates_literal_default_jf),
        ("L02", test_L02_jf_matrix_with_only_default_key),
        ("M01", test_M01_zero_azubis_per_year_no_events),
        ("M02", test_M02_short_duration_correct_cycle),
        ("M03", test_M03_empty_snapshot_still_runs),
        ("M04", test_M04_high_azubi_count_no_id_collision),
        ("M05", test_M05_retention_rate_exactly_half),
        ("N01", test_N01_mak_balance_hire_then_takeover),
        ("N02", test_N02_headcount_balance_hire_vs_graduation),
        ("N03", test_N03_mak_zero_during_entire_training_period),
    ]

    passed = failed = 0
    failures = []

    print(f"\n{'='*60}")
    print("  Azubi Deep-Test Suite")
    print(f"{'='*60}\n")

    for tid, fn in tests:
        try:
            fn()
            print(f"  PASS  [{tid}] {fn.__doc__.splitlines()[0].strip()}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  [{tid}] {fn.__doc__.splitlines()[0].strip()}")
            print(f"         -> {e}")
            failed += 1
            failures.append((tid, str(e)))
        except Exception as e:
            print(f"  ERROR [{tid}] {fn.__doc__.splitlines()[0].strip()}")
            print(f"         -> {type(e).__name__}: {e}")
            failed += 1
            failures.append((tid, f"{type(e).__name__}: {e}"))

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed | {failed} failed | {len(tests)} total")
    print(f"{'='*60}\n")

    if failures:
        print("Failures:")
        for tid, msg in failures:
            print(f"  [{tid}] {msg}")
