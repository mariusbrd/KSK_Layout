from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dataloader.compact_simulation_engine import _apply_salary_automation_to_employee_state


def test_salary_automation_advances_only_forecast_entries_in_new_hires_scope():
    df = pd.DataFrame(
        [
            {
                "PersNr": "TR_10001",
                "TrfGr": "E13",
                "St": 1,
                "Eintritt": pd.Timestamp("2026-01-15"),
            },
            {
                "PersNr": "000123",
                "TrfGr": "E13",
                "St": 3,
                "Eintritt": pd.Timestamp("2015-06-01"),
            },
        ]
    )
    events = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-01-15"),
                "type": "Trainee_Hire",
                "persnr": "TR_10001",
                "TrfGr": "E13",
                "St": 1,
            }
        ]
    )
    settings = {
        "enabled": True,
        "scope": "new_hires_only",
        "fallback_step": 4,
        "e1_entry_step": 2,
        "e2_plus_default_entry_step": 1,
        "e1_progression_years": [4, 4, 4, 4],
        "e2_plus_progression_years": [1, 2, 3, 4, 5],
        "use_tenure_as_step_proxy_for_existing_staff": False,
    }

    with patch(
        "dataloader.compact_simulation_engine._get_salary_automation_settings",
        return_value=settings,
    ):
        result = _apply_salary_automation_to_employee_state(
            df,
            target_date=pd.Timestamp("2027-02-01"),
            events_df=events,
        )

    by_pid = result.set_index("PersNr")
    assert int(by_pid.loc["TR_10001", "St"]) == 2
    assert int(by_pid.loc["000123", "St"]) == 3


def test_salary_automation_can_use_tenure_proxy_for_existing_staff_without_downgrade():
    df = pd.DataFrame(
        [
            {
                "PersNr": "000123",
                "TrfGr": "E10",
                "St": 3,
                "Eintritt": pd.Timestamp("2018-01-01"),
            },
            {
                "PersNr": "000124",
                "TrfGr": "E10",
                "St": 5,
                "Eintritt": pd.Timestamp("2018-01-01"),
            },
            {
                "PersNr": "000125",
                "TrfGr": "E1",
                "St": 1,
                "Eintritt": pd.Timestamp("2020-01-01"),
            },
        ]
    )
    settings = {
        "enabled": True,
        "scope": "all_staff",
        "fallback_step": 4,
        "e1_entry_step": 2,
        "e2_plus_default_entry_step": 1,
        "e1_progression_years": [4, 4, 4, 4],
        "e2_plus_progression_years": [1, 2, 3, 4, 5],
        "use_tenure_as_step_proxy_for_existing_staff": True,
    }

    with patch(
        "dataloader.compact_simulation_engine._get_salary_automation_settings",
        return_value=settings,
    ):
        result = _apply_salary_automation_to_employee_state(
            df,
            target_date=pd.Timestamp("2026-12-31"),
            events_df=pd.DataFrame(),
        )

    by_pid = result.set_index("PersNr")
    assert int(by_pid.loc["000123", "St"]) == 4
    assert int(by_pid.loc["000124", "St"]) == 5
    assert int(by_pid.loc["000125", "St"]) == 3
