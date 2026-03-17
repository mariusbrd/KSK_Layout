from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from zugaenge.forecast import run_forecast_zugaenge


def _base_snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "PersNr": "000001",
                "active": True,
                "Geschlecht": "w",
                "Text Gsch": "weiblich",
                "Organisationseinheit": "OE1",
                "Jobfamily": "Angestellte",
                "OE-Cluster": "Markt",
                "JF-Cluster": "Vertrieb",
                "TrfGr": "E9A",
                "St": 3,
                "Eintritt": pd.Timestamp("2019-01-01"),
                "GebDatum": pd.Timestamp("1990-01-01"),
                "mak": 1.0,
            },
            {
                "PersNr": "000002",
                "active": True,
                "Geschlecht": "m",
                "Text Gsch": "männlich",
                "Organisationseinheit": "OE2",
                "Jobfamily": "Angestellte",
                "OE-Cluster": "Marktfolge",
                "JF-Cluster": "Backoffice",
                "TrfGr": "E9A",
                "St": 3,
                "Eintritt": pd.Timestamp("2020-01-01"),
                "GebDatum": pd.Timestamp("1988-01-01"),
                "mak": 1.0,
            },
        ]
    )


def test_forecast_assigns_gender_to_new_accessions():
    result = run_forecast_zugaenge(
        df_snapshot=_base_snapshot(),
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-12-31"),
        freq="M",
        params={
            "random_seed": 42,
            "azubi": {
                "active": True,
                "new_cases_per_year": 2,
                "retention_rate": 0.0,
            },
            "trainee": {
                "active": True,
                "new_cases_per_year": 1,
            },
            "new_hires": {
                "active": True,
                "count_per_year": 1,
                "strategy": "Random",
            },
        },
        vacancies=[],
    )

    final_state = result["final_state"].reset_index(drop=True)
    new_people = final_state[final_state["PersNr"].astype(str).str.startswith(("NH_", "AZ_", "TR_"))].copy()

    assert not new_people.empty
    assert new_people["Geschlecht"].notna().all()
    assert new_people["Geschlecht"].isin(["m", "w", "d"]).all()
    assert new_people["Text Gsch"].notna().all()
