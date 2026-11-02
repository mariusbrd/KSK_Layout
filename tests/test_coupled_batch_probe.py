"""Exact seed coupling of departure events, vacancies and new hires."""
from __future__ import annotations

import pandas as pd

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.params import default_params as departure_defaults
from dataloader.compact_simulation_engine import (
    _apply_attrition_events_to_employee_state, _build_vacancies_from_attrition,
    _prepare_employee_forecast_base,
)
from zugaenge.coupled_batch_probe import (
    _donor_lookup, prepare_coupled_batch, iter_coupled_seed_records,
    replay_final_employee_state, vacancies_from_departure_records,
)
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.params import default_params as hire_defaults
from test_kuendigung_forecast import _make_employee, EMPTY_ATZ


def test_departure_subtracts_person_ist_mak_independent_of_position_soll() -> None:
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-31")
    person = _make_employee("101", age_years=70, bsgrd=50.0, mak=0.5)
    rows = []
    for org, soll in (("OE-Null", 0.0), ("OE-Regular", 1.0)):
        rows.append({**person, "Austritt": pd.NaT,
                     "Organisationseinheit": org, "Soll_FTE": soll,
                     "Sollarbeitszeit": soll * 39.0, "MAK_Reporting": 0.5,
                     "Personen_MAK_Source": 0.5})
    people = _prepare_employee_forecast_base(pd.DataFrame(rows), EMPTY_ATZ, start)
    assert people.loc[0, "mak"] == 1.0
    params = departure_defaults()
    params["components"].update(atz=False, retirement=True, quit=False,
                                ruhend=False)
    params["retirement"].update(rent_rate_65=1.0, rent_rate_60_65=0.0)
    result = run_forecast_abgaenge(people, EMPTY_ATZ, start, end, "M", params,
                                   include_diagnostics=False)
    departures = result["events_person_level"]
    retirement = departures.loc[departures["reason_code"].eq("RETIREMENT")]
    assert len(retirement) == 1
    assert retirement.iloc[0]["headcount_change"] == -1
    assert retirement.iloc[0]["mak_change"] == -1.0


def test_compact_vacancies_preserve_missing_atz_org_and_person_normalization() -> None:
    people = pd.DataFrame([
        {"PersNr": "101.0", "Jobfamily": "JF-A", "OE-Cluster": "C-A"},
        {"PersNr": "102", "Jobfamily": "JF-B", "OE-Cluster": "C-B"},
    ])
    records = [
        {"persnr": "101.0", "event_date": pd.Timestamp("2026-06-30"),
         "headcount_change": -1, "reason_code": "ATZ_END"},
        {"persnr": "102", "event_date": pd.Timestamp("2026-07-31"),
         "headcount_change": -1, "reason_code": "QUIT",
         "Organisationseinheit": "OE-B"},
    ]
    expected = _build_vacancies_from_attrition(
        people, {"events_person_level": pd.DataFrame(records)})
    actual = vacancies_from_departure_records(records, _donor_lookup(people))
    pd.testing.assert_frame_equal(pd.DataFrame(actual), pd.DataFrame(expected),
                                  check_exact=True)


def test_coupled_seed_events_and_vacancies_match_reference() -> None:
    people = pd.DataFrame([
        {**_make_employee(str(101 + i), age_years=29 + i * 6,
                          mak=0.6 + i / 20, org=f"OE-{i % 3}",
                          jobfamily=f"JF-{i % 2}"),
         "mak": 0.6 + i / 20, "Geschlecht": "m" if i % 2 else "w",
         "Ausbildung": "Bachelor FH" if i % 2 else "kfm Berufsabschluss",
         "OE-Cluster": f"C-{i % 2}"}
        for i in range(10)
    ])
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31")
    departure_params = departure_defaults()
    departure_params["quit"].update(quit_rate_base=0.8, use_quit_matrix=False)
    departure_params["atz"].update(new_atz_rate=0.0, use_atz_matrix=False)
    hire_params = hire_defaults()
    hire_params["new_hires"].update(active=True, count_per_year=24,
                                    strategy="Fill Vacancies")
    prepared = prepare_coupled_batch(people, EMPTY_ATZ, start, end,
                                     departure_params, hire_params)
    for seed, departure_records, vacancies, hire_records, new_people in (
            iter_coupled_seed_records(prepared, (7, 42, 123))):
        reference_departure = run_forecast_abgaenge(
            people, EMPTY_ATZ, start, end, "M",
            {**departure_params, "random_seed": seed},
            include_diagnostics=False)
        pd.testing.assert_frame_equal(
            pd.DataFrame(departure_records),
            reference_departure["events_person_level"], check_exact=True)
        expected_vacancies = _build_vacancies_from_attrition(
            people, reference_departure)
        pd.testing.assert_frame_equal(pd.DataFrame(vacancies),
                                      pd.DataFrame(expected_vacancies), check_exact=True)
        after_departure = _apply_attrition_events_to_employee_state(
            people, reference_departure["events_person_level"])
        reference_hires = run_forecast_zugaenge(
            after_departure, start, end_date=end,
            params={**hire_params, "random_seed": seed},
            vacancies=expected_vacancies)
        pd.testing.assert_frame_equal(pd.DataFrame(hire_records),
                                      reference_hires["events"], check_exact=True)
        assert len(new_people) == len(reference_hires["events"])
        pd.testing.assert_frame_equal(
            replay_final_employee_state(people, departure_records, new_people),
            reference_hires["final_state"], check_exact=True)
