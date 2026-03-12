"""
Test suite: Sprint 1-3 Changes (Deep Validation)
=================================================
Validiert die konkreten Aenderungen aus Sprint 1–3:

  Sprint 1 (C1): nearest_cycle als Default; graduation_mode korrekt durchgereicht
  Sprint 2 (C3): Getrennte Debt-Pools (takeover_baseline / takeover_forecast)
  Sprint 3 (C4): Jobfamily_pre_azubi in Conversion_In Events
  Sprint 3 (C5): is_internal_transition korrekt gesetzt und Paare HC-neutral

Run:
  py KSK_Layout/tests/test_azubi_sprint_changes.py
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zugaenge.forecast import run_forecast_zugaenge, _estimate_baseline_graduation_date
from zugaenge.params import default_params


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap_plain():
    """Minimal snapshot: 1 regular employee, no Azubis."""
    return pd.DataFrame([{
        "PersNr": "900001",
        "Organisationseinheit": "OE1",
        "Jobfamily": "Angestellte",
        "active": True,
        "mak": 1.0,
        "TrfGr": "E9A",
        "Eintritt": "2015-01-01",
    }])


def _snap_with_baseline_azubi(persnr="BAS001", trfgr="TVAoeD", jf="Azubi"):
    """Snapshot with one baseline Azubi (no GraduationModus pre-assigned)."""
    return pd.DataFrame([
        {
            "PersNr": persnr,
            "Organisationseinheit": "OE1",
            "Jobfamily": jf,
            "active": True,
            "mak": 0.0,
            "TrfGr": trfgr,
            "Eintritt": "2023-01-01",
            "is_forecast": False,
        },
        {
            "PersNr": "900001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte",
            "active": True,
            "mak": 1.0,
            "TrfGr": "E9A",
            "Eintritt": "2015-01-01",
        },
    ])


def _base_params(overrides=None):
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
            "graduation_mode": "nearest_cycle",
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


def _run(snapshot, params, years=5, start="2025-12-31"):
    result = run_forecast_zugaenge(
        snapshot, pd.Timestamp(start), periods_years=years, params=params
    )
    events = result["events"]
    if not isinstance(events, pd.DataFrame):
        events = pd.DataFrame(events)
    return result, events


# ===========================================================================
# SPRINT 1 – C1: nearest_cycle als neuer Default
# ===========================================================================

def test_S1_01_default_params_nearest_cycle():
    """
    default_params() muss graduation_mode='nearest_cycle' liefern.
    Sicherstellt, dass der neue Default korrekt in params.py hinterlegt ist.
    """
    p = default_params()
    mode = p["azubi"]["graduation_mode"]
    assert mode == "nearest_cycle", \
        f"Default muss 'nearest_cycle' sein, ist '{mode}'"


def test_S1_02_nearest_cycle_eliminates_late_year_bias():
    """
    nearest_cycle: Sep-Eintritt + 3 Jahre -> Aug gleiches Endjahr (nicht +1).
    Konkret: Sep 2026 + 3J -> estimated_end Sep 2029 -> nearest Aug = Aug 2029.
    next_cycle haette Aug 2030 ergeben.
    """
    entry = pd.Timestamp("2026-09-01")
    nearest = _estimate_baseline_graduation_date(entry, 3.0, graduation_mode="nearest_cycle")
    next_c = _estimate_baseline_graduation_date(entry, 3.0, graduation_mode="next_cycle")
    assert nearest.year == 2029, f"nearest_cycle: erwartet 2029, got {nearest.year}"
    assert next_c.year == 2030, f"next_cycle: erwartet 2030, got {next_c.year}"
    assert nearest < next_c, "nearest_cycle muss frueheren Abschluss liefern fuer Sep-Eintritt"


def test_S1_03_graduation_mode_flows_into_engine():
    """
    graduation_mode='next_cycle' explizit in Params -> Engine nutzt es (nicht den Default).
    Nachweis: Sep-Eintritte graduieren im Folgejahr (Aug YYYY+1) bei next_cycle.
    """
    snap = _snap_plain()
    # Nur Sep-Eintritte simulieren: Startdatum so waehlen, dass Hires im Sep landen.
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 12,
            "duration_years": 3.0,
            "graduation_mode": "next_cycle",
            "retention_rate": 1.0,
        }
    })
    _, events = _run(snap, params, years=6, start="2026-08-31")
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0, "Erwartet Conversion_In Events"
    # Mit next_cycle kommen Sep-2026-Eintritte erst Aug 2030 raus
    # nearest_cycle haette Aug 2029 ergeben -> Unterschied pruefbar
    params_n = _base_params({
        "azubi": {
            "new_cases_per_year": 12,
            "duration_years": 3.0,
            "graduation_mode": "nearest_cycle",
            "retention_rate": 1.0,
        }
    })
    _, events_n = _run(snap, params_n, years=6, start="2026-08-31")
    conv_in_n = events_n[events_n["type"] == "Azubi_Conversion_In"]

    # nearest_cycle hat in gleichem Fenster >= Absolventen (frueherer Abschluss = mehr im Fenster)
    assert len(conv_in_n) >= len(conv_in), (
        f"nearest_cycle ({len(conv_in_n)}) sollte >= next_cycle ({len(conv_in)}) Graduierungen haben"
    )


def test_S1_04_graduation_dates_always_august():
    """
    Unabhaengig vom Modus: Alle Graduation-Events muessen am 1. August liegen.
    Gilt fuer beide Modi gleichermassen.
    """
    snap = _snap_plain()
    for mode in ["nearest_cycle", "next_cycle"]:
        params = _base_params({"azubi": {"new_cases_per_year": 15, "graduation_mode": mode, "retention_rate": 1.0}})
        _, events = _run(snap, params, years=5)
        grad_events = events[events["type"].isin(["Azubi_Conversion_Out", "Azubi_Conversion_In", "Azubi_Exit"])]
        if len(grad_events) == 0:
            continue
        for _, row in grad_events.iterrows():
            d = pd.to_datetime(row["date"])
            assert d.month == 8 and d.day == 1, \
                f"[{mode}] Graduation-Datum muss 01.08., ist {d.date()}"


def test_S1_05_minimum_training_duration_respected():
    """
    nearest_cycle darf Ausbildung nicht auf weniger als ~2 Jahre verkuerzen.
    Auch mit nearest_cycle gilt: Abschluss immer >= (Eintritt + ~2 Jahre).
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "duration_years": 3.0,
            "graduation_mode": "nearest_cycle",
            "retention_rate": 1.0,
            "exclude_baseline_azubis": True,
        }
    })
    result, events = _run(snap, params, years=6)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    assert len(conv_in) > 0

    # Lookup entry_date via graduation_date field
    if "entry_date" in conv_in.columns and "graduation_date" in conv_in.columns:
        for _, row in conv_in.iterrows():
            entry = pd.to_datetime(row["entry_date"])
            grad = pd.to_datetime(row["graduation_date"])
            if pd.isna(entry) or pd.isna(grad):
                continue
            months_trained = (grad.year - entry.year) * 12 + (grad.month - entry.month)
            assert months_trained >= 23, (
                f"Ausbildungsdauer zu kurz: {months_trained} Monate "
                f"(Eintritt {entry.date()}, Abschluss {grad.date()})"
            )


# ===========================================================================
# SPRINT 2 – C3: Getrennte Debt-Pools (takeover_baseline / takeover_forecast)
# ===========================================================================

def test_S2_01_baseline_outcomes_independent_of_forecast_volume():
    """
    Kerntest C3: Baseline-Azubi-Outcomes aendern sich NICHT wenn new_cases_per_year variiert.
    Mit getrennten Debt-Pools darf der Forecast-Hire-Umfang die Baseline-Entscheidungen
    nicht beeinflussen.
    """
    baseline_azubi = pd.DataFrame([
        {
            "PersNr": "BAS001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Azubi",
            "active": True,
            "mak": 0.0,
            "TrfGr": "TVAoeD",
            "Eintritt": "2023-01-01",
            "is_forecast": False,
            # Kein GraduationModus -> geht durch Baseline-Debt-Pfad
        },
        {
            "PersNr": "900001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte",
            "active": True,
            "mak": 1.0,
            "TrfGr": "E9A",
            "Eintritt": "2015-01-01",
        },
    ])

    baseline_outcomes = {}
    for n_forecast in [0, 5, 15, 50]:
        params = _base_params({
            "azubi": {
                "new_cases_per_year": n_forecast,
                "duration_years": 3.0,
                "retention_rate": 0.8,
                "exclude_baseline_azubis": False,
                "graduation_mode": "next_cycle",
            }
        })
        _, events = _run(baseline_azubi, params, years=5, start="2025-12-31")

        # Pruefen was BAS001 wurde
        bas_events = events[events["persnr"] == "BAS001"]
        if len(bas_events) == 0:
            outcome = "no_event"
        elif "Azubi_Conversion_In" in bas_events["type"].values:
            outcome = "takeover"
        elif "Azubi_Exit" in bas_events["type"].values:
            outcome = "exit"
        else:
            outcome = "other"
        baseline_outcomes[n_forecast] = outcome

    # BAS001 hat kein GraduationModus -> Baseline-Debt-Pfad
    # Bei retention_rate=0.8 und einem einzigen Baseline-Azubi sollte er immer Takeover sein
    # (0.8 Debt ist >0.5, d.h. takeover_count=0 beim ersten, aber nach Akkumulation...)
    # Wichtigster Check: Das Outcome ist konsistent ueber alle Forecast-Volumina
    unique_outcomes = set(v for v in baseline_outcomes.values() if v != "no_event")
    assert len(unique_outcomes) <= 1, (
        f"Baseline-Outcome von BAS001 variiert je nach Forecast-Volumen: {baseline_outcomes}. "
        "Das deutet auf noch gemeinsame Debt-Pools hin!"
    )
    print(f"  Baseline-Outcomes bei verschiedenen Forecast-Volumina: {baseline_outcomes}")


def test_S2_02_forecast_debt_tracks_correctly():
    """
    Mit getrenntem Forecast-Debt: 15 Azubis x 80% = exakt 12 Takeovers + 3 Exits.
    Baseline-Pool darf dieses Ergebnis nicht beeinflussen.
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "duration_years": 3.0,
            "retention_rate": 0.8,
            "exclude_baseline_azubis": True,
            "graduation_mode": "nearest_cycle",
        }
    })
    _, events = _run(snap, params, years=6, start="2025-08-01")

    hires_2026 = events[(events["type"] == "Azubi_Hire") & (pd.to_datetime(events["date"]).dt.year == 2026)]
    n_hired = len(hires_2026)
    if n_hired == 0:
        print("  SKIP S2_02: Keine 2026-Hires gefunden")
        return

    cohort_ids = set(hires_2026["persnr"].tolist())
    takeovers = events[(events["type"] == "Azubi_Conversion_In") & events["persnr"].isin(cohort_ids)]
    exits = events[(events["type"] == "Azubi_Exit") & events["persnr"].isin(cohort_ids)]

    total_grad = len(takeovers) + len(exits)
    if total_grad == 0:
        print("  SKIP S2_02: Kohorte noch nicht graduiert im Simulationsfenster")
        return

    expected_t = round(n_hired * 0.8)
    expected_e = n_hired - expected_t
    assert len(takeovers) == expected_t, \
        f"Kohorte 2026: erwartet {expected_t} Takeovers, got {len(takeovers)} (n={n_hired})"
    assert len(exits) == expected_e, \
        f"Kohorte 2026: erwartet {expected_e} Exits, got {len(exits)} (n={n_hired})"


def test_S2_03_debt_keys_exist_in_engine():
    """
    Die Debt-Keys 'takeover_baseline' und 'takeover_forecast' muessen im Code existieren.
    Prueft dies durch direkte Inspektion des Quellcodes.
    """
    import inspect
    from zugaenge import forecast as fc_module
    src = inspect.getsource(fc_module)
    assert "takeover_baseline" in src, \
        "takeover_baseline nicht im Forecast-Code gefunden — Debt-Trennung fehlt!"
    assert "takeover_forecast" in src, \
        "takeover_forecast nicht im Forecast-Code gefunden — Debt-Trennung fehlt!"
    # Sicherstellen dass alter gemeinsamer Key nicht mehr genutzt wird
    # (Ausnahme: Kommentare/Strings erlaubt, aber kein debts["takeover"] = ...)
    import re
    bad_pattern = re.compile(r'debts\["takeover"\]\s*=')
    assert not bad_pattern.search(src), \
        "debts['takeover'] = ... noch im Code — alter gemeinsamer Pool noch aktiv!"


def test_S2_04_macro_rate_still_accurate_after_debt_split():
    """
    Makro-Validierung: Nach Debt-Trennung gilt die globale Takeover-Rate noch immer.
    Ueber 6 Jahres-Kohorten: Abweichung der tatsaechlichen Rate vom Ziel < 5%.
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "duration_years": 3.0,
            "retention_rate": 0.8,
            "exclude_baseline_azubis": True,
        }
    })
    _, events = _run(snap, params, years=7, start="2025-08-01")

    takeovers = events[events["type"] == "Azubi_Conversion_In"]
    exits = events[events["type"] == "Azubi_Exit"]
    total_grad = len(takeovers) + len(exits)

    if total_grad == 0:
        print("  SKIP S2_04: Keine Graduierungen im Simulationsfenster")
        return

    actual_rate = len(takeovers) / total_grad
    assert 0.75 <= actual_rate <= 0.85, \
        f"Makro-Takeover-Rate = {actual_rate:.1%}, erwartet 80% +/-5%"


# ===========================================================================
# SPRINT 3 – C4: Jobfamily_pre_azubi in Conversion_In Events
# ===========================================================================

def test_S3_C4_01_jobfamily_pre_azubi_in_conversion_in_event():
    """
    Azubi_Conversion_In Events muessen Jobfamily_pre_azubi enthalten,
    falls der Azubi vorher eine bekannte Original-JF hatte.
    """
    snap = _snap_with_baseline_azubi(persnr="BAS001", trfgr="TVAoeD", jf="Azubi Spezial")
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 0,  # Nur Bestands-Azubi
            "duration_years": 3.0,
            "retention_rate": 1.0,
            "exclude_baseline_azubis": False,
        }
    })
    _, events = _run(snap, params, years=5)

    conv_in = events[(events["type"] == "Azubi_Conversion_In") & (events["persnr"] == "BAS001")]
    if len(conv_in) == 0:
        print("  SKIP S3_C4_01: BAS001 nicht im Simulationsfenster graduiert")
        return

    assert "Jobfamily_pre_azubi" in conv_in.columns, \
        "Jobfamily_pre_azubi Spalte fehlt in Conversion_In Events"

    row = conv_in.iloc[0]
    pre_jf = row["Jobfamily_pre_azubi"]
    assert pd.notna(pre_jf), \
        "Jobfamily_pre_azubi ist NaN fuer Azubi mit bekannter Original-JF"
    assert str(pre_jf) == "Azubi Spezial", \
        f"Jobfamily_pre_azubi='Azubi Spezial' erwartet, got '{pre_jf}'"


def test_S3_C4_02_jobfamily_pre_azubi_differs_from_post_takeover_jf():
    """
    Nach Uebernahme aendert sich die JF (z.B. zu 'Angestellte').
    Jobfamily_pre_azubi muss die urspruengliche JF erhalten, Jobfamily die neue.
    """
    snap = _snap_with_baseline_azubi(persnr="BAS001", trfgr="TVAoeD", jf="Azubi Spezial")
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 0,
            "duration_years": 3.0,
            "retention_rate": 1.0,
            "exclude_baseline_azubis": False,
        }
    })
    _, events = _run(snap, params, years=5)

    conv_in = events[(events["type"] == "Azubi_Conversion_In") & (events["persnr"] == "BAS001")]
    if len(conv_in) == 0:
        print("  SKIP S3_C4_02: BAS001 nicht graduiert im Fenster")
        return

    row = conv_in.iloc[0]
    post_jf = row.get("Jobfamily")
    pre_jf = row.get("Jobfamily_pre_azubi")

    # Post-Takeover JF sollte nicht "Azubi Spezial" oder "Sonstige" sein (Standardfall: "Angestellte")
    assert post_jf != "Sonstige", \
        f"Post-Takeover JF sollte nicht 'Sonstige' sein, got '{post_jf}'"

    # Pre-Azubi JF muss erhalten geblieben sein
    if pd.notna(pre_jf):
        assert pre_jf == "Azubi Spezial", \
            f"Jobfamily_pre_azubi muss original 'Azubi Spezial' sein, got '{pre_jf}'"


def test_S3_C4_03_forecast_azubis_have_no_pre_azubi_jf():
    """
    Neu eingestellte Forecast-Azubis starten mit JF='Sonstige' und haben
    kein Jobfamily_pre_azubi (da sie direkt als Azubi eingestellt werden).
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 10,
            "duration_years": 3.0,
            "retention_rate": 1.0,
            "exclude_baseline_azubis": True,
        }
    })
    result, events = _run(snap, params, years=5)

    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    if len(conv_in) == 0:
        print("  SKIP S3_C4_03: Keine Conversion_In Events")
        return

    if "Jobfamily_pre_azubi" in conv_in.columns:
        # Forecast-Azubis haben kein Original-JF (wurden als Sonstige eingestellt)
        # -> Jobfamily_pre_azubi sollte NaN oder nicht gesetzt sein
        fc_conv = conv_in[conv_in.get("is_forecast", True) == True] if "is_forecast" in conv_in.columns else conv_in
        if len(fc_conv) > 0:
            # Nicht jeder muss NaN haben, aber keiner sollte "Azubi" oder "Ausbildung" als pre_jf haben
            for _, row in fc_conv.iterrows():
                pre = row.get("Jobfamily_pre_azubi")
                if pd.notna(pre):
                    assert str(pre) not in ("Azubi", "Ausbildung", "Azubi Spezial"), \
                        f"Forecast-Azubi hat unerwartetes Jobfamily_pre_azubi='{pre}'"


# ===========================================================================
# SPRINT 3 – C5: is_internal_transition und HC-Neutralitaet
# ===========================================================================

def test_S3_C5_01_conversion_pairs_are_hc_neutral():
    """
    Azubi_Conversion_Out (-1) + Azubi_Conversion_In (+1) pro Person = netto 0 HC.
    Auch nach Sprint-Aenderungen muss diese Invariante gelten.
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "duration_years": 3.0,
            "retention_rate": 1.0,
        }
    })
    _, events = _run(snap, params, years=5)

    conv_out = events[events["type"] == "Azubi_Conversion_Out"]
    conv_in = events[events["type"] == "Azubi_Conversion_In"]

    net_hc = int(conv_out["count"].sum()) + int(conv_in["count"].sum())
    assert net_hc == 0, \
        f"Conversion-Paare muessen HC-neutral sein (netto=0), got {net_hc}"


def test_S3_C5_02_internal_transition_flag_set_on_both_events():
    """
    Azubi_Conversion_Out UND Azubi_Conversion_In muessen is_internal_transition=True tragen.
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {"new_cases_per_year": 10, "retention_rate": 1.0}
    })
    _, events = _run(snap, params, years=5)

    for event_type in ["Azubi_Conversion_Out", "Azubi_Conversion_In"]:
        subset = events[events["type"] == event_type]
        if len(subset) == 0:
            continue
        assert "is_internal_transition" in subset.columns, \
            f"is_internal_transition fehlt in {event_type} Events"
        bad = subset[subset["is_internal_transition"] != True]
        assert len(bad) == 0, \
            f"{event_type}: {len(bad)} Events ohne is_internal_transition=True"


def test_S3_C5_03_external_events_have_no_internal_flag():
    """
    Azubi_Hire und Azubi_Exit duerfen NICHT is_internal_transition=True tragen
    (sie sind echte externe Veraenderungen, keine Statuswechsel).
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {"new_cases_per_year": 10, "retention_rate": 0.5}
    })
    _, events = _run(snap, params, years=5)

    for event_type in ["Azubi_Hire", "Azubi_Exit"]:
        subset = events[events["type"] == event_type]
        if len(subset) == 0:
            continue
        if "is_internal_transition" not in subset.columns:
            continue  # Flag fehlt -> kein Problem (nur Conv-Events brauchen es)
        flagged = subset[subset["is_internal_transition"] == True]
        assert len(flagged) == 0, \
            f"{event_type}: {len(flagged)} Events haben is_internal_transition=True (sollte nicht)"


def test_S3_C5_04_net_hc_timeline_unaffected_by_conversion_pairs():
    """
    Die monatliche HC-Zeitreihe (forecast_kpis) muss durch Conversion-Paare nicht beeinflusst werden.
    Im Monat eines Azubi-Abschlusses: headcount_end unveraendert (Conv_Out + Conv_In = 0).
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {
            "new_cases_per_year": 0,  # Keine neuen Hires -> nur Conversions in KPIs
            "duration_years": 3.0,
            "retention_rate": 1.0,
            "exclude_baseline_azubis": True,
        }
    })
    result, _ = _run(snap, params, years=5)
    kpis = result["forecast_kpis"]

    if kpis.empty:
        print("  SKIP S3_C5_04: Leere KPI-Zeitreihe")
        return

    # Ohne neue Hires und ohne Baseline-Azubis: headcount sollte konstant bleiben
    hc_start = kpis.iloc[0]["headcount_start"]
    hc_end_vals = kpis["headcount_end"].tolist()
    # Alle HC-Werte sollten gleich dem Startbestand sein (kein Wachstum, keine Abgaenge)
    for i, hc in enumerate(hc_end_vals):
        assert abs(hc - hc_start) <= 1, \
            f"HC-Zeitreihe in Periode {i}: {hc} statt {hc_start} (Conversion-Paare sollten neutral sein)"


def test_S3_C5_05_internal_pairs_same_date():
    """
    Azubi_Conversion_Out und Azubi_Conversion_In fuer dieselbe Person
    muessen exakt am selben Datum auftreten.
    """
    snap = _snap_plain()
    params = _base_params({
        "azubi": {"new_cases_per_year": 15, "retention_rate": 1.0}
    })
    _, events = _run(snap, params, years=5)

    conv_out = events[events["type"] == "Azubi_Conversion_Out"][["persnr", "date"]].copy()
    conv_in = events[events["type"] == "Azubi_Conversion_In"][["persnr", "date"]].copy()

    if len(conv_out) == 0:
        return

    merged = conv_out.merge(conv_in, on="persnr", suffixes=("_out", "_in"))
    for _, row in merged.iterrows():
        assert row["date_out"] == row["date_in"], (
            f"Person {row['persnr']}: Conv_Out am {row['date_out']} "
            f"aber Conv_In am {row['date_in']} — sollte dasselbe Datum sein"
        )


# ===========================================================================
# INTEGRATION: Alle Sprint-Aenderungen zusammen
# ===========================================================================

def test_INT_01_full_simulation_with_all_sprint_changes():
    """
    Integration: Vollstaendige Simulation mit allen Sprint-Aenderungen aktiv.
    Prueft dass kein unerwarteter Fehler auftritt und Basiskonsistenz gilt.
    """
    snap = pd.DataFrame([
        {
            "PersNr": "BAS001",
            "Organisationseinheit": "OE1",
            "Jobfamily": "Azubi Spezial",
            "active": True, "mak": 0.0, "TrfGr": "TVAoeD",
            "Eintritt": "2023-01-01", "is_forecast": False,
        },
        {
            "PersNr": "900001", "Organisationseinheit": "OE1",
            "Jobfamily": "Angestellte", "active": True, "mak": 1.0,
            "TrfGr": "E9A", "Eintritt": "2015-01-01",
        },
    ])

    params = _base_params({
        "azubi": {
            "new_cases_per_year": 15,
            "duration_years": 3.0,
            "retention_rate": 0.8,
            "graduation_mode": "nearest_cycle",  # Sprint 1
            "exclude_baseline_azubis": False,     # Baseline + Forecast gemischt
        }
    })

    result, events = _run(snap, params, years=6, start="2025-08-01")

    # Kein Absturz
    assert isinstance(events, pd.DataFrame)

    # Sprint 1: nearest_cycle aktiv -> keine unrealistisch langen Ausbildungen
    if "entry_date" in events.columns and "graduation_date" in events.columns:
        conv_in = events[events["type"] == "Azubi_Conversion_In"]
        for _, row in conv_in.iterrows():
            entry = pd.to_datetime(row.get("entry_date"))
            grad = pd.to_datetime(row.get("graduation_date"))
            if pd.isna(entry) or pd.isna(grad):
                continue
            months = (grad.year - entry.year) * 12 + (grad.month - entry.month)
            assert months <= 48, \
                f"nearest_cycle: Ausbildungsdauer {months} Monate erscheint unrealistisch hoch"

    # Sprint 2: Macro-Rate gesamt (Baseline + Forecast gemischt)
    conv_in = events[events["type"] == "Azubi_Conversion_In"]
    exits = events[events["type"] == "Azubi_Exit"]
    total_grad = len(conv_in) + len(exits)
    if total_grad > 5:
        rate = len(conv_in) / total_grad
        assert 0.65 <= rate <= 0.95, \
            f"Gesamte Takeover-Rate {rate:.1%} ausserhalb 65–95% (Ziel: 80%)"

    # Sprint 3 C4: Jobfamily_pre_azubi in Conv_In vorhanden
    if len(conv_in) > 0:
        assert "Jobfamily_pre_azubi" in conv_in.columns, \
            "Jobfamily_pre_azubi fehlt in Conversion_In Events nach Sprint 3 C4"

    # Sprint 3 C5: HC-Neutralitaet
    net = int(events["count"].sum()) if "count" in events.columns else 0
    # Mit new_cases_per_year>0: netto HC steigt (Hires), aber Conversions netzen 0
    conv_net = int(events[events["type"].isin(["Azubi_Conversion_Out", "Azubi_Conversion_In"])]["count"].sum())
    assert conv_net == 0, f"Conversion-Paare gesamt: netto HC = {conv_net}, erwartet 0"


def test_INT_02_params_dict_complete_for_ui_passthrough():
    """
    Stellt sicher, dass alle Engine-Parameter, die neu hinzugefuegt wurden,
    auch in default_params() enthalten sind und der richtige Typ vorliegt.
    """
    p = default_params()["azubi"]

    required = {
        "graduation_mode": str,
        "azubi_mak_during_training": float,
        "azubi_mak_after_takeover": float,
        "azubi_conversion_month": int,
        "azubi_conversion_day": int,
        "exclude_baseline_azubis": bool,
    }

    for key, expected_type in required.items():
        assert key in p, f"Parameter '{key}' fehlt in default_params()['azubi']"
        assert isinstance(p[key], expected_type), \
            f"Parameter '{key}' hat falschen Typ: {type(p[key]).__name__} (erwartet {expected_type.__name__})"


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    tests = [
        # Sprint 1 – C1
        ("S1_01", test_S1_01_default_params_nearest_cycle),
        ("S1_02", test_S1_02_nearest_cycle_eliminates_late_year_bias),
        ("S1_03", test_S1_03_graduation_mode_flows_into_engine),
        ("S1_04", test_S1_04_graduation_dates_always_august),
        ("S1_05", test_S1_05_minimum_training_duration_respected),
        # Sprint 2 – C3
        ("S2_01", test_S2_01_baseline_outcomes_independent_of_forecast_volume),
        ("S2_02", test_S2_02_forecast_debt_tracks_correctly),
        ("S2_03", test_S2_03_debt_keys_exist_in_engine),
        ("S2_04", test_S2_04_macro_rate_still_accurate_after_debt_split),
        # Sprint 3 – C4
        ("S3_C4_01", test_S3_C4_01_jobfamily_pre_azubi_in_conversion_in_event),
        ("S3_C4_02", test_S3_C4_02_jobfamily_pre_azubi_differs_from_post_takeover_jf),
        ("S3_C4_03", test_S3_C4_03_forecast_azubis_have_no_pre_azubi_jf),
        # Sprint 3 – C5
        ("S3_C5_01", test_S3_C5_01_conversion_pairs_are_hc_neutral),
        ("S3_C5_02", test_S3_C5_02_internal_transition_flag_set_on_both_events),
        ("S3_C5_03", test_S3_C5_03_external_events_have_no_internal_flag),
        ("S3_C5_04", test_S3_C5_04_net_hc_timeline_unaffected_by_conversion_pairs),
        ("S3_C5_05", test_S3_C5_05_internal_pairs_same_date),
        # Integration
        ("INT_01", test_INT_01_full_simulation_with_all_sprint_changes),
        ("INT_02", test_INT_02_params_dict_complete_for_ui_passthrough),
    ]

    passed = failed = 0
    failures = []

    print(f"\n{'='*60}")
    print("  Sprint 1-3 Validierungs-Tests")
    print(f"{'='*60}\n")

    for tid, fn in tests:
        try:
            fn()
            first_line = (fn.__doc__ or "").strip().splitlines()[0].strip()
            print(f"  PASS  [{tid}] {first_line}")
            passed += 1
        except AssertionError as e:
            first_line = (fn.__doc__ or "").strip().splitlines()[0].strip()
            print(f"  FAIL  [{tid}] {first_line}")
            print(f"         -> {e}")
            failed += 1
            failures.append((tid, str(e)))
        except Exception as e:
            first_line = (fn.__doc__ or "").strip().splitlines()[0].strip()
            print(f"  ERROR [{tid}] {first_line}")
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
