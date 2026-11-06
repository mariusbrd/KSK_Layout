"""Regular-only compact hire decisions match the fixed-plan product path."""
from __future__ import annotations

import pandas as pd
import pytest

from utils.fixed_plan_vacancies import FixedPlanVacancyPool, available_position_vacancies
from zugaenge.forecast import run_forecast_zugaenge
from zugaenge.hire_batch_probe import prepare_hire_batch, iter_hire_seed_records
from zugaenge.params import default_params


def test_productive_regular_batch_events_match_fixed_plan_forecast() -> None:
    from pathlib import Path
    from benchmark_page_rerun_paths import FROZEN_STICHTAG, _freeze_context, _reset_state
    from dataloader import loader
    from dataloader.compact_simulation_engine import (
        _apply_attrition_events_to_employee_state, _build_fixed_plan_vacancy_context,
        _prepare_employee_forecast_base,
    )
    from abgaenge.forecast import run_forecast_abgaenge
    from abgaenge.params import default_params as departure_defaults

    _freeze_context()
    _reset_state()
    root = Path(__file__).resolve().parents[1]
    positions, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(root), None, None, None)
    start, end = pd.Timestamp(FROZEN_STICHTAG), pd.Timestamp("2026-12-31")
    people = _prepare_employee_forecast_base(positions, atz, start)
    departure_params = departure_defaults()
    departure_params["quit"].update(quit_rate_base=0.12, use_quit_matrix=False)
    params = default_params()
    params["azubi"]["active"] = False
    params["trainee"]["active"] = False
    params["new_hires"].update(active=True, count_per_year=20,
                                strategy="Fill Vacancies")
    for seed in (7, 42):
        departures = run_forecast_abgaenge(
            people, atz, start, end, "M",
            {**departure_params, "random_seed": seed}, include_diagnostics=False)
        events = departures["events_person_level"]
        after_departures = _apply_attrition_events_to_employee_state(people, events)
        pool, original, vacancies = _build_fixed_plan_vacancy_context(
            positions, events, start)
        expected = run_forecast_zugaenge(
            after_departures, start, end_date=end,
            params={**params, "random_seed": seed},
            vacancy_pool=pool, original_positions=original)
        prepared = prepare_hire_batch(after_departures, start, end, params)
        _, compact_events, _ = next(iter_hire_seed_records(
            prepared, [seed], fixed_plan_vacancy_provider=lambda _: vacancies))
        pd.testing.assert_frame_equal(
            pd.DataFrame(compact_events), expected["events"], check_exact=True)
        assert len(compact_events) > 0


@pytest.mark.parametrize("annual_hires", [12, 48])
def test_fixed_plan_regular_batch_events_match_product(annual_hires: int) -> None:
    start, end = pd.Timestamp("2025-12-31"), pd.Timestamp("2027-12-31")
    positions = pd.DataFrame([
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 0.6,
         "Kürzel OrgEinheit": "1000", "Organisationseinheit": "A",
         "Planstelle": "Beratung", "Planstellennr": "A-1"},
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 0.8,
         "Kürzel OrgEinheit": "1001", "Organisationseinheit": "B",
         "Planstelle": "Markt", "Planstellennr": "B-1"},
        {"PersNr": pd.NA, "Is_Plan_Vacant": True, "Soll_FTE": 1.0,
         "Kürzel OrgEinheit": "9910", "Organisationseinheit": "Ausbildung",
         "Planstelle": "Bankkaufleute", "Planstellennr": "AZ-1"},
    ])
    people = pd.DataFrame([{
        "PersNr": "100", "active": True, "Organisationseinheit": "A",
        "OE-Cluster": "C-A", "Jobfamily": "Angestellte",
        "Ausbildung": "Bachelor FH", "Geschlecht": "m",
        "Eintritt": pd.Timestamp("2020-01-01"), "mak": 1.0,
    }])
    params = default_params()
    params["azubi"]["active"] = False
    params["trainee"]["active"] = False
    params["new_hires"].update(active=True, count_per_year=annual_hires,
                                strategy="Fill Vacancies")
    vacancies = available_position_vacancies(
        positions, pd.DataFrame(), start, include_report_excluded=True)
    prepared = prepare_hire_batch(people, start, end, params)
    stream = iter_hire_seed_records(
        prepared, [7, 42], fixed_plan_vacancy_provider=lambda seed: vacancies)
    for seed, compact_events, new_people in stream:
        expected = run_forecast_zugaenge(
            people, start, end_date=end,
            params={**params, "random_seed": seed},
            vacancy_pool=FixedPlanVacancyPool(vacancies))
        pd.testing.assert_frame_equal(
            pd.DataFrame(compact_events), expected["events"], check_exact=True)
        assert len(new_people) == len(compact_events) == 2
        assert sorted(event["mak"] for event in compact_events) == [0.6, 0.8]
