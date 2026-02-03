"""
Validierungsskript fuer forecast_abgaenge.py

Testet systematisch alle Kernfunktionen des Forecast-Moduls:
  - Datenvorbereitung und Flags
  - ATZ-Ereignisse (deterministisch)
  - Renten-Abgaenge (probabilistisch)
  - Kuendigungen (stochastisch)
  - Ruhend-Dynamik
  - Neue ATZ-Vertraege
  - Prioritaetsregeln
  - Bilanzgleichungen (End-to-End)
  - Reproduzierbarkeit und Sensitivitaet
  - Perioden-Korrektheit

Aufruf:
    python validate_forecast.py
"""

import os
import sys
import numpy as np
import pandas as pd

# Sicherstellen, dass Importe funktionieren
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forecast_abgaenge import (
    ForecastParams,
    prepare_workforce,
    get_atz_events,
    get_retirement_exits,
    get_quit_exits,
    process_ruhend,
    generate_new_atz_contracts,
    run_forecast,
    run_forecast_from_files,
    _annual_to_monthly,
    _age_group,
    _tenure_group,
)


# =============================================================================
# TEST-INFRASTRUKTUR
# =============================================================================

_passed = 0
_failed = 0
_errors = []


def check(test_id: str, description: str, condition: bool, detail: str = ""):
    """Einzelnen Test auswerten und loggen."""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {test_id}: {description}")
    else:
        _failed += 1
        msg = f"{test_id}: {description}"
        if detail:
            msg += f" -- {detail}"
        _errors.append(msg)
        print(f"  [FAIL] {test_id}: {description}")
        if detail:
            print(f"         Detail: {detail}")


def print_summary():
    """Gibt Zusammenfassung aus und setzt Exit-Code."""
    print(f"\n{'=' * 60}")
    print(f"ERGEBNIS: {_passed} PASS / {_failed} FAIL / {_passed + _failed} TOTAL")
    print(f"{'=' * 60}")
    if _errors:
        print("\nFehlgeschlagene Tests:")
        for e in _errors:
            print(f"  x {e}")
    return 0 if _failed == 0 else 1


# =============================================================================
# DATEN LADEN
# =============================================================================

def load_test_data():
    """Laedt die echten Excel-Daten."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(os.path.dirname(base), "Original-Daten")

    ma_path = os.path.join(data_dir, "Mitarbeiter.xlsx")
    atz_path = os.path.join(data_dir, "ATZ.xlsx")

    if not os.path.exists(ma_path):
        print(f"FEHLER: Mitarbeiter.xlsx nicht gefunden: {ma_path}")
        sys.exit(1)
    if not os.path.exists(atz_path):
        print(f"FEHLER: ATZ.xlsx nicht gefunden: {atz_path}")
        sys.exit(1)

    df_ma = pd.read_excel(ma_path)
    df_atz = pd.read_excel(
        atz_path, parse_dates=["Beginn", "Ende", "Ende ATZ Vertrag"]
    )
    return df_ma, df_atz, ma_path, atz_path


# =============================================================================
# BLOCK 1: DATEN-VORBEREITUNG
# =============================================================================

def test_block_1(wf: pd.DataFrame, atz_agg: pd.DataFrame):
    print("\n=== Block 1: Daten-Vorbereitung (prepare_workforce) ===")

    # V1.1 PersNr-Normalisierung
    all_str = wf["PersNr"].apply(lambda x: isinstance(x, str) and len(x) == 6).all()
    no_nan = wf["PersNr"].notna().all()
    check("V1.1", "PersNr 6-stellig, keine NaN", all_str and no_nan)

    # V1.2 Alter/Tenure plausibel
    alter_ok = wf["Alter"].between(14, 80).all()
    tenure_ok = wf["Tenure"].between(-1, 55).all()
    check(
        "V1.2",
        "Alter plausibel [14, 80], Tenure plausibel [-1, 55]",
        alter_ok and tenure_ok,
        detail=f"Alter min={wf['Alter'].min():.1f} max={wf['Alter'].max():.1f}, "
        f"Tenure min={wf['Tenure'].min():.1f} max={wf['Tenure'].max():.1f}",
    )

    # V1.3 ATZ-Flags konsistent
    fr_subset_atz = wf.loc[wf["ist_atz_fr"], "ist_atz"].all() if wf["ist_atz_fr"].any() else True
    ar_subset_atz = wf.loc[wf["ist_atz_ar"], "ist_atz"].all() if wf["ist_atz_ar"].any() else True
    ar_fr_disjoint = not (wf["ist_atz_fr"] & wf["ist_atz_ar"]).any()
    check(
        "V1.3",
        "ATZ-Flags: FR subset ATZ, AR subset ATZ, AR disjoint FR",
        fr_subset_atz and ar_subset_atz and ar_fr_disjoint,
    )

    # V1.4 Ruhend und ATZ-FR disjunkt
    overlap = (wf["ist_ruhend"] & wf["ist_atz_fr"]).sum()
    check("V1.4", "Ruhend und ATZ-FR disjunkt", overlap == 0, f"Overlap: {overlap}")

    # V1.5 MAK-Initialwerte
    ruhend_mak_zero = (wf.loc[wf["ist_ruhend"], "MAK"] == 0).all()
    fr_mak_zero = (wf.loc[wf["ist_atz_fr"], "MAK"] == 0).all()
    aktiv_mask = ~wf["ist_ruhend"] & ~wf["ist_atz_fr"]
    aktiv_mak_positive = (wf.loc[aktiv_mask, "MAK"] >= 0).all()
    aktiv_mak_max_1 = (wf.loc[aktiv_mask, "MAK"] <= 1.0).all()
    check(
        "V1.5",
        "MAK: Ruhend=0, ATZ-FR=0, Aktive in [0, 1]",
        ruhend_mak_zero and fr_mak_zero and aktiv_mak_positive and aktiv_mak_max_1,
    )

    # V1.6 Headcount Initial
    hc = int(wf["im_bestand"].sum())
    total_rows = len(wf)
    check("V1.6", f"im_bestand == Zeilenanzahl ({total_rows})", hc == total_rows)


# =============================================================================
# BLOCK 2: ATZ-EREIGNISSE
# =============================================================================

def test_block_2(atz_agg: pd.DataFrame):
    print("\n=== Block 2: ATZ-Ereignisse (get_atz_events) ===")

    # V2.1 Bekannte AR->FR Uebergaenge
    # Finde einen bekannten FR-Beginn in den Daten
    fr_rows = atz_agg[atz_agg["Phase"] == "FR"].copy()
    future_fr = fr_rows[fr_rows["Beginn"] > pd.Timestamp("2025-01-30")]
    if not future_fr.empty:
        test_row = future_fr.iloc[0]
        test_persnr = test_row["PersNr"]
        test_date = test_row["Beginn"]
        p_start = test_date.replace(day=1)
        p_end = p_start + pd.DateOffset(months=1) - pd.Timedelta(days=1)
        events = get_atz_events(atz_agg, p_start, p_end)
        check(
            "V2.1",
            f"Bekannter AR->FR (PersNr {test_persnr}, {test_date:%Y-%m})",
            test_persnr in events["ar_to_fr"],
            f"ar_to_fr={events['ar_to_fr']}",
        )
    else:
        check("V2.1", "Kein zukuenftiger AR->FR-Uebergang in Daten", True)

    # V2.2 Bekannte FR-Enden
    future_exits = fr_rows[fr_rows["Ende"] > pd.Timestamp("2025-01-30")]
    if not future_exits.empty:
        test_row = future_exits.iloc[0]
        test_persnr = test_row["PersNr"]
        test_date = test_row["Ende"]
        p_start = test_date.replace(day=1)
        p_end = p_start + pd.DateOffset(months=1) - pd.Timedelta(days=1)
        events = get_atz_events(atz_agg, p_start, p_end)
        check(
            "V2.2",
            f"Bekanntes FR-Ende (PersNr {test_persnr}, {test_date:%Y-%m})",
            test_persnr in events["exits"],
            f"exits={events['exits']}",
        )
    else:
        check("V2.2", "Kein zukuenftiges FR-Ende in Daten", True)

    # V2.3 Leere ATZ -> 0 Events
    empty_atz = pd.DataFrame(columns=atz_agg.columns)
    events_empty = get_atz_events(
        empty_atz,
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-01-31"),
    )
    check(
        "V2.3",
        "Leere ATZ-Daten -> 0 Events",
        len(events_empty["ar_to_fr"]) == 0 and len(events_empty["exits"]) == 0,
    )

    # V2.4 Keine Doppelzaehlung AR->FR und Exit in gleicher Periode
    # Teste alle Monate von 2025 bis 2032
    doppel_gefunden = False
    for year in range(2025, 2033):
        for month in range(1, 13):
            p_start = pd.Timestamp(year, month, 1)
            p_end = p_start + pd.DateOffset(months=1) - pd.Timedelta(days=1)
            ev = get_atz_events(atz_agg, p_start, p_end)
            overlap = set(ev["ar_to_fr"]) & set(ev["exits"])
            if overlap:
                doppel_gefunden = True
                break
        if doppel_gefunden:
            break
    check("V2.4", "ar_to_fr und exits disjunkt (alle Perioden)", not doppel_gefunden)


# =============================================================================
# BLOCK 3: RENTEN-ABGAENGE
# =============================================================================

def test_block_3(wf: pd.DataFrame, atz_agg: pd.DataFrame):
    print("\n=== Block 3: Renten-Abgaenge (get_retirement_exits) ===")

    params = ForecastParams()
    stichtag = pd.Timestamp("2026-03-01")

    # V3.1 Nur Personen >= 60
    rng = np.random.default_rng(42)
    exits = get_retirement_exits(wf, stichtag, params, set(), rng)
    if exits:
        ages = wf.loc[wf["PersNr"].isin(exits), "GebDatum"].apply(
            lambda gd: (stichtag - gd).days / 365.25
        )
        all_over_60 = (ages >= 59.5).all()  # 59.5 wegen Rundung
        check("V3.1", f"Alle Renten-Exits >= 60 Jahre (n={len(exits)})", all_over_60,
              f"Min Alter: {ages.min():.1f}")
    else:
        check("V3.1", "Keine Renten-Exits (Rate zu niedrig oder kein Kandidat)", True)

    # V3.2 ATZ-Personen ausgeschlossen
    rng2 = np.random.default_rng(42)
    exits2 = get_retirement_exits(wf, stichtag, params, set(), rng2)
    atz_set = set(wf.loc[wf["ist_atz"], "PersNr"])
    overlap = set(exits2) & atz_set
    check("V3.2", "Keine ATZ-Personen in Renten-Exits", len(overlap) == 0,
          f"Overlap: {overlap}")

    # V3.3 Rate plausibel (statistisch, ueber viele Draws)
    grp_65 = wf[wf["im_bestand"] & ~wf["ist_atz"] & (wf["Alter"] >= 65)]
    if len(grp_65) > 0:
        monthly_rate = _annual_to_monthly(params.rent_rate_65)
        n_draws = 1000
        exit_counts = []
        for i in range(n_draws):
            rng_i = np.random.default_rng(i)
            ex = get_retirement_exits(wf, stichtag, params, set(), rng_i)
            # Nur 65+ zaehlen
            ex_65 = [p for p in ex if p in set(grp_65["PersNr"])]
            exit_counts.append(len(ex_65))
        empirical_rate = np.mean(exit_counts) / len(grp_65)
        rate_ok = abs(empirical_rate - monthly_rate) < monthly_rate * 0.5
        check(
            "V3.3",
            f"Empirische Rate 65+ ({empirical_rate:.4f}) nahe Zielrate ({monthly_rate:.4f})",
            rate_ok,
        )
    else:
        check("V3.3", "Keine Personen >= 65 (uebersprungen)", True)

    # V3.4 Seed-Reproduzierbarkeit
    rng_a = np.random.default_rng(123)
    rng_b = np.random.default_rng(123)
    exits_a = get_retirement_exits(wf, stichtag, params, set(), rng_a)
    exits_b = get_retirement_exits(wf, stichtag, params, set(), rng_b)
    check("V3.4", "Gleicher Seed -> identische Exits", exits_a == exits_b)


# =============================================================================
# BLOCK 4: KUENDIGUNGEN
# =============================================================================

def test_block_4(wf: pd.DataFrame):
    print("\n=== Block 4: Kuendigungen (get_quit_exits) ===")

    params = ForecastParams()
    stichtag = pd.Timestamp("2026-03-01")

    # V4.1 Keine Azubi-Exits
    rng = np.random.default_rng(42)
    exits = get_quit_exits(wf, stichtag, params, set(), rng)
    if exits and "MitarbGruppenbez." in wf.columns:
        azubi_exits = wf.loc[
            wf["PersNr"].isin(exits) & (wf["MitarbGruppenbez."] == "Auszubildende")
        ]
        check("V4.1", "Keine Azubis in Quit-Exits", len(azubi_exits) == 0)
    else:
        check("V4.1", "Keine Quit-Exits oder kein Azubi-Feld", True)

    # V4.2 Keine ATZ/Ruhend-Exits
    rng2 = np.random.default_rng(42)
    exits2 = get_quit_exits(wf, stichtag, params, set(), rng2)
    atz_ruhend = set(wf.loc[wf["ist_atz"] | wf["ist_ruhend"], "PersNr"])
    overlap = set(exits2) & atz_ruhend
    check("V4.2", "Keine ATZ/Ruhend in Quit-Exits", len(overlap) == 0,
          f"Overlap: {overlap}")

    # V4.3 Matrix: Junge/kurze Tenure hoehere Rate
    n_iter = 500
    young_short_exits = 0
    young_short_count = 0
    old_long_exits = 0
    old_long_count = 0

    active_base = wf[
        wf["im_bestand"] & ~wf["ist_atz"] & ~wf["ist_ruhend"]
    ].copy()
    if "MitarbGruppenbez." in active_base.columns:
        active_base = active_base[active_base["MitarbGruppenbez."] != "Auszubildende"]

    young_short = active_base[(active_base["Alter"] < 30) & (active_base["Tenure"] < 2)]
    old_long = active_base[(active_base["Alter"] >= 45) & (active_base["Tenure"] >= 5)]
    young_short_count = len(young_short)
    old_long_count = len(old_long)

    if young_short_count > 0 and old_long_count > 0:
        ys_rates = []
        ol_rates = []
        for i in range(n_iter):
            rng_i = np.random.default_rng(i + 1000)
            ex = get_quit_exits(wf, stichtag, params, set(), rng_i)
            ex_set = set(ex)
            ys_n = len(set(young_short["PersNr"]) & ex_set)
            ol_n = len(set(old_long["PersNr"]) & ex_set)
            ys_rates.append(ys_n / young_short_count)
            ol_rates.append(ol_n / old_long_count)

        ys_mean = np.mean(ys_rates)
        ol_mean = np.mean(ol_rates)
        check(
            "V4.3",
            f"Jung/kurz ({ys_mean:.4f}) > Alt/lang ({ol_mean:.4f})",
            ys_mean > ol_mean,
        )
    else:
        check("V4.3", "Nicht genug Daten fuer Matrix-Test", True)

    # V4.4 Fallback-Rate
    params_flat = ForecastParams(use_quit_matrix=False, quit_rate_base=0.05)
    flat_exits_counts = []
    for i in range(200):
        rng_i = np.random.default_rng(i + 2000)
        ex = get_quit_exits(wf, stichtag, params_flat, set(), rng_i)
        flat_exits_counts.append(len(ex))
    flat_mean = np.mean(flat_exits_counts)
    expected_monthly = _annual_to_monthly(0.05)
    n_eligible = len(active_base)
    expected_exits = n_eligible * expected_monthly
    ratio = flat_mean / expected_exits if expected_exits > 0 else 1.0
    check(
        "V4.4",
        f"Fallback-Rate: empirisch {flat_mean:.1f} vs erwartet {expected_exits:.1f}",
        0.5 < ratio < 2.0,
    )

    # V4.5 Seed-Reproduzierbarkeit
    rng_a = np.random.default_rng(777)
    rng_b = np.random.default_rng(777)
    exits_a = get_quit_exits(wf, stichtag, params, set(), rng_a)
    exits_b = get_quit_exits(wf, stichtag, params, set(), rng_b)
    check("V4.5", "Gleicher Seed -> identische Quit-Exits", exits_a == exits_b)


# =============================================================================
# BLOCK 5: RUHEND-DYNAMIK
# =============================================================================

def test_block_5(wf: pd.DataFrame):
    print("\n=== Block 5: Ruhend-Dynamik (process_ruhend) ===")

    params = ForecastParams(
        ruhend_return_rate=1.0,  # 100% Rueckkehr fuer deterministischen Test
        avg_ruhend_duration_months=3,
        new_ruhend_rate=0.0,  # Keine neuen Faelle
    )
    rng = np.random.default_rng(42)
    stichtag = pd.Timestamp("2026-03-01")

    # V5.1 Countdown funktioniert
    tracker_init = {"000001": 3, "000002": 1}
    _, _, tracker_after = process_ruhend(wf, stichtag, params, tracker_init, rng)
    # PersNr 000002 hatte 1 -> sollte bei Rueckkehr (rate=1.0) entfernt sein
    # PersNr 000001 hatte 3 -> sollte jetzt 2 haben
    check(
        "V5.1",
        "Countdown: 3->2 nach einer Periode",
        tracker_after.get("000001") == 2,
        f"tracker_after={tracker_after}",
    )

    # V5.2 Rueckkehr bei Countdown=0
    rng2 = np.random.default_rng(42)
    rueckkehrer, _, _ = process_ruhend(wf, stichtag, params, {"000002": 1}, rng2)
    check(
        "V5.2",
        "Rueckkehr bei Countdown=1 (nach Dekrement -> 0, rate=1.0)",
        "000002" in rueckkehrer,
        f"rueckkehrer={rueckkehrer}",
    )

    # V5.3 Neue Ruhend-Faelle bei Rate > 0
    params_new = ForecastParams(
        ruhend_return_rate=1.0,
        new_ruhend_rate=0.10,  # Hohe Rate fuer sicheren Test
    )
    rng3 = np.random.default_rng(42)
    _, neue, _ = process_ruhend(wf, stichtag, params_new, {}, rng3)
    check("V5.3", f"Neue Ruhend-Faelle bei Rate 10% (n={len(neue)})", len(neue) > 0)

    # V5.4 Keine Doppel-Ruhend
    params_dbl = ForecastParams(new_ruhend_rate=0.10)
    rng4 = np.random.default_rng(42)
    existing_ruhend = set(wf.loc[wf["ist_ruhend"], "PersNr"])
    tracker_existing = {p: 6 for p in existing_ruhend}
    _, neue_dbl, _ = process_ruhend(wf, stichtag, params_dbl, tracker_existing, rng4)
    overlap = set(neue_dbl) & existing_ruhend
    check("V5.4", "Keine bereits Ruhenden werden erneut ruhend", len(overlap) == 0)


# =============================================================================
# BLOCK 6: NEUE ATZ-VERTRAEGE
# =============================================================================

def test_block_6(wf: pd.DataFrame, atz_agg: pd.DataFrame):
    print("\n=== Block 6: Neue ATZ-Vertraege (generate_new_atz_contracts) ===")

    params = ForecastParams(new_atz_cases_per_year=60)  # Hoch fuer sicheren Test
    rng = np.random.default_rng(42)
    p_start = pd.Timestamp("2026-03-01")

    new_atz = generate_new_atz_contracts(wf, atz_agg, p_start, params, rng)

    if new_atz.empty:
        check("V6.1", "Keine neuen Vertraege erzeugt (unerwartet bei hoher Rate)", False)
        check("V6.2", "SKIP", True)
        check("V6.3", "SKIP", True)
    else:
        # V6.1 Eligible-Filter
        new_persnr = set(new_atz["PersNr"])
        eligible_persons = wf[wf["PersNr"].isin(new_persnr)]
        alters_ok = eligible_persons["Alter"].between(
            params.atz_eligible_age_min - 0.5, params.atz_eligible_age_max + 0.5
        ).all()
        not_atz = (~eligible_persons["ist_atz"]).all()
        if "MitarbGruppenbez." in eligible_persons.columns:
            not_azubi = (eligible_persons["MitarbGruppenbez."] != "Auszubildende").all()
        else:
            not_azubi = True
        check(
            "V6.1",
            "Eligible: Alter 55-60, nicht ATZ, nicht Azubi",
            alters_ok and not_atz and not_azubi,
        )

        # V6.2 AR+FR-Paar
        for persnr in new_persnr:
            rows = new_atz[new_atz["PersNr"] == persnr]
            phases = set(rows["Phase"])
            if phases != {"AR", "FR"} or len(rows) != 2:
                check("V6.2", f"PersNr {persnr}: exakt AR+FR", False, f"Phases={phases}")
                break
        else:
            check("V6.2", f"Alle {len(new_persnr)} Personen haben AR+FR-Paar", True)

        # V6.3 Zeitliche Konsistenz
        consistent = True
        for persnr in new_persnr:
            rows = new_atz[new_atz["PersNr"] == persnr]
            ar = rows[rows["Phase"] == "AR"].iloc[0]
            fr = rows[rows["Phase"] == "FR"].iloc[0]
            gap = (fr["Beginn"] - ar["Ende"]).days
            fr_end_match = fr["Ende"] == fr["Ende_ATZ_Vertrag"]
            if gap != 1 or not fr_end_match:
                consistent = False
                break
        check("V6.3", "AR-Ende + 1 Tag = FR-Beginn, FR-Ende = Ende_ATZ_Vertrag", consistent)

    # V6.4 Poisson-Verteilung
    params_poisson = ForecastParams(new_atz_cases_per_year=12)  # -> lambda=1/Monat
    n_draws = 1000
    counts = []
    for i in range(n_draws):
        rng_i = np.random.default_rng(i + 5000)
        new = generate_new_atz_contracts(wf, atz_agg, p_start, params_poisson, rng_i)
        counts.append(len(new) // 2 if not new.empty else 0)
    mean_count = np.mean(counts)
    expected = 12 / 12.0  # = 1.0
    check(
        "V6.4",
        f"Poisson: Mittelwert {mean_count:.2f} nahe Lambda {expected:.1f}",
        abs(mean_count - expected) < 0.5,
    )


# =============================================================================
# BLOCK 7: PRIORITAETSREGELN
# =============================================================================

def test_block_7(df_ma: pd.DataFrame, df_atz: pd.DataFrame):
    print("\n=== Block 7: Prioritaetsregeln (Integrations-Check) ===")

    params = ForecastParams(forecast_months=12, random_seed=42)
    stichtag = pd.Timestamp("2026-02-01")
    forecast_df, meta = run_forecast(df_ma, df_atz, params, stichtag)

    # V7.1 Keine Doppel-Exits ueber Forecast hinweg pruefen
    # (innerhalb einer Periode durch excluded_persnr geregelt)
    # Hier pruefen: Summe der Einzel-Exits == exits_total pro Zeile
    forecast_df["_sum_exits"] = (
        forecast_df["exits_atz_end"]
        + forecast_df["exits_retirement"]
        + forecast_df["exits_quit"]
        + forecast_df["exits_other"]
    )
    sum_ok = (forecast_df["_sum_exits"] == forecast_df["exits_total"]).all()
    check("V7.1", "Summe Einzel-Exits == exits_total (alle Perioden)", sum_ok)

    # V7.2 ATZ vor Rente: Wir koennen nicht direkt pruefen, aber indirekt:
    # Wenn eine Person einen ATZ-Vertrag hat und austritt, wird sie als ATZ gezaehlt,
    # nicht als Rente. Pruefen via Meta-Summen.
    total_from_meta = meta["total_exits"]
    total_from_df = int(forecast_df["exits_total"].sum())
    check(
        "V7.2",
        f"Meta-Total ({total_from_meta}) == DF-Total ({total_from_df})",
        total_from_meta == total_from_df,
    )

    # V7.3 Rente vor Kuendigung (implizit durch excluded_persnr)
    # Konsistenzcheck: kein Warning im Meta
    n_warnings = len(meta["warnings"])
    check("V7.3", f"Keine Warnungen im Forecast (n={n_warnings})", n_warnings == 0,
          f"Warnings: {meta['warnings'][:3]}")


# =============================================================================
# BLOCK 8: BILANZGLEICHUNG (End-to-End)
# =============================================================================

def test_block_8(df_ma: pd.DataFrame, df_atz: pd.DataFrame):
    print("\n=== Block 8: Bilanzgleichung (End-to-End) ===")

    params = ForecastParams(forecast_months=60, random_seed=42)
    stichtag = pd.Timestamp("2026-02-01")
    forecast_df, meta = run_forecast(df_ma, df_atz, params, stichtag)

    # V8.1 HC-Bilanz pro Periode
    hc_bilanz_ok = True
    fail_period = None
    for _, row in forecast_df.iterrows():
        expected_end = row["headcount_start"] - row["exits_total"]
        if expected_end != row["headcount_end"]:
            hc_bilanz_ok = False
            fail_period = row["period_start"]
            break
    check(
        "V8.1",
        "hc_start - exits_total == hc_end (alle Perioden)",
        hc_bilanz_ok,
        f"Fehler in Periode {fail_period}" if fail_period else "",
    )

    # V8.2 HC-Monotonie
    hc_mono = (forecast_df["headcount_end"] <= forecast_df["headcount_start"]).all()
    check("V8.2", "HC monoton fallend (hc_end <= hc_start)", hc_mono)

    # V8.3 HC-Verkettung
    if len(forecast_df) > 1:
        hc_ends = forecast_df["headcount_end"].values[:-1]
        hc_starts = forecast_df["headcount_start"].values[1:]
        verkettung_ok = (hc_ends == hc_starts).all()
        check("V8.3", "hc_end(t) == hc_start(t+1)", verkettung_ok)
    else:
        check("V8.3", "Nur eine Periode (SKIP)", True)

    # V8.4 MAK <= HC
    mak_le_hc = (forecast_df["mak_end"] <= forecast_df["headcount_end"]).all()
    check("V8.4", "MAK <= Headcount (alle Perioden)", mak_le_hc)

    # V8.5 MAK >= 0
    mak_ge_0 = (forecast_df["mak_end"] >= 0).all()
    check("V8.5", "MAK >= 0 (alle Perioden)", mak_ge_0)

    # V8.6 Summe Exits = Meta
    sum_exits_df = int(forecast_df["exits_total"].sum())
    sum_exits_meta = meta["total_exits"]
    check(
        "V8.6",
        f"DF-Summe ({sum_exits_df}) == Meta ({sum_exits_meta})",
        sum_exits_df == sum_exits_meta,
    )


# =============================================================================
# BLOCK 9: REPRODUZIERBARKEIT & SENSITIVITAET
# =============================================================================

def test_block_9(df_ma: pd.DataFrame, df_atz: pd.DataFrame):
    print("\n=== Block 9: Reproduzierbarkeit & Sensitivitaet ===")

    stichtag = pd.Timestamp("2026-02-01")

    # V9.1 Seed-Determinismus
    params_a = ForecastParams(forecast_months=12, random_seed=42)
    params_b = ForecastParams(forecast_months=12, random_seed=42)
    df_a, _ = run_forecast(df_ma, df_atz, params_a, stichtag)
    df_b, _ = run_forecast(df_ma, df_atz, params_b, stichtag)
    check(
        "V9.1",
        "Gleicher Seed -> identischer Forecast",
        df_a.equals(df_b),
    )

    # V9.2 Anderer Seed -> anderes Ergebnis
    params_c = ForecastParams(forecast_months=12, random_seed=99)
    df_c, _ = run_forecast(df_ma, df_atz, params_c, stichtag)
    check(
        "V9.2",
        "Seed 42 vs 99 -> unterschiedliche Ergebnisse",
        not df_a.equals(df_c),
    )

    # V9.3 Null-Raten -> nur ATZ-Exits
    params_null = ForecastParams(
        forecast_months=12,
        random_seed=42,
        rent_rate_65=0.0,
        rent_rate_60_65=0.0,
        quit_rate_base=0.0,
        use_quit_matrix=False,
        new_ruhend_rate=0.0,
        new_atz_cases_per_year=0,
    )
    df_null, meta_null = run_forecast(df_ma, df_atz, params_null, stichtag)
    no_retirement = int(df_null["exits_retirement"].sum()) == 0
    no_quit = int(df_null["exits_quit"].sum()) == 0
    check(
        "V9.3",
        "Null-Raten: 0 Rente, 0 Kuendigung",
        no_retirement and no_quit,
        f"Rente={df_null['exits_retirement'].sum()}, Quit={df_null['exits_quit'].sum()}",
    )

    # V9.4 Maximale Raten -> hoher Abgang
    params_max = ForecastParams(
        forecast_months=12,
        random_seed=42,
        rent_rate_65=1.0,
        rent_rate_60_65=1.0,
        quit_rate_base=0.50,
        use_quit_matrix=False,
    )
    df_max, meta_max = run_forecast(df_ma, df_atz, params_max, stichtag)
    # Bei sehr hohen Raten muss mehr Abgang kommen als bei Default
    default_params = ForecastParams(forecast_months=12, random_seed=42)
    _, meta_default = run_forecast(df_ma, df_atz, default_params, stichtag)
    check(
        "V9.4",
        f"Max-Raten ({meta_max['total_exits']}) > Default ({meta_default['total_exits']})",
        meta_max["total_exits"] > meta_default["total_exits"],
    )


# =============================================================================
# BLOCK 10: PERIODEN-KORREKTHEIT
# =============================================================================

def test_block_10(df_ma: pd.DataFrame, df_atz: pd.DataFrame):
    print("\n=== Block 10: Perioden-Korrektheit ===")

    stichtag = pd.Timestamp("2026-02-15")  # Bewusst Mitte des Monats
    n_months = 24
    params = ForecastParams(forecast_months=n_months, random_seed=42)
    forecast_df, _ = run_forecast(df_ma, df_atz, params, stichtag)

    # V10.1 Erste Periode beginnt am 1. des Folgemonats
    expected_first = pd.Timestamp("2026-03-01")
    actual_first = forecast_df.iloc[0]["period_start"]
    check(
        "V10.1",
        f"Erste Periode: {actual_first:%Y-%m-%d} == {expected_first:%Y-%m-%d}",
        actual_first == expected_first,
    )

    # V10.2 Anzahl Perioden
    check(
        "V10.2",
        f"Anzahl Perioden: {len(forecast_df)} == {n_months}",
        len(forecast_df) == n_months,
    )

    # V10.3 Lueckenlos
    if len(forecast_df) > 1:
        starts = pd.to_datetime(forecast_df["period_start"])
        diffs = starts.diff().dropna()
        # Alle Differenzen sollten 28-31 Tage sein (Monatswechsel)
        all_monthly = diffs.apply(lambda d: 27 <= d.days <= 32).all()
        check("V10.3", "Perioden lueckenlos (28-32 Tage Abstand)", all_monthly)
    else:
        check("V10.3", "Nur eine Periode (SKIP)", True)


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 60)
    print("VALIDIERUNG: forecast_abgaenge.py")
    print("=" * 60)

    # Daten laden
    print("\nLade Daten...")
    df_ma, df_atz, ma_path, atz_path = load_test_data()
    print(f"  Mitarbeiter: {len(df_ma)} Zeilen")
    print(f"  ATZ: {len(df_atz)} Zeilen")

    # Vorbereiten (fuer Unit-Tests der Einzelfunktionen)
    stichtag = pd.Timestamp("2026-02-01")
    wf, atz_agg = prepare_workforce(df_ma, df_atz, stichtag)

    # Tests ausfuehren
    test_block_1(wf, atz_agg)
    test_block_2(atz_agg)
    test_block_3(wf, atz_agg)
    test_block_4(wf)
    test_block_5(wf)
    test_block_6(wf, atz_agg)
    test_block_7(df_ma, df_atz)
    test_block_8(df_ma, df_atz)
    test_block_9(df_ma, df_atz)
    test_block_10(df_ma, df_atz)

    exit_code = print_summary()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
