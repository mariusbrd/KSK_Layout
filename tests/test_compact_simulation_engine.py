import pandas as pd
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
from dataloader.compact_simulation_engine import _vacate_rows, simulate_compact_snapshot


def _base_snapshot(age_years: int = 45) -> pd.DataFrame:
    base_date = pd.Timestamp("2025-12-31")
    geb = base_date - pd.DateOffset(years=age_years)

    return pd.DataFrame([
        {
            "PersNr": "000001",
            "Personalnummer": "000001",
            "Is_Vacant": False,
            "GebDatum": geb,
            "Eintritt": pd.Timestamp("2015-01-01"),
            "Austritt": pd.NaT,
            "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "Sollarbeitszeit": 39.0,
            "Soll_FTE": 1.0,
            "FTE_person": 1.0,
            "FTE_assigned": 1.0,
            "BsGrd": 100.0,
            "MAK_Calculated": 1.0,
            "MAK": 1.0,
            "mak": 1.0,
            "Organisationseinheit": "OE1",
            "Kürzel OrgEinheit": "1000",
            "Planstelle": "Beratung",
            "Jobfamily": "Angestellte",
            "TrfGr": "E9A",
            "St": 3,
            "Geschlecht": "w",
            "Text Gsch": "w",
            "Vertragsart": "Unbefristet",
            "MitarbGruppenbez.": "Beschäftigte",
        }
    ])


def _inactive_zugaenge_params():
    return {
        "azubi": {"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        "trainee": {"active": False, "new_cases_per_year": 0},
        "new_hires": {"active": False, "count_per_year": 0},
        "random_seed": 42,
    }


def test_compact_simulation_reages_snapshot_without_events():
    base_date = pd.Timestamp("2025-12-31")
    target_date = pd.Timestamp("2026-12-31")

    result = simulate_compact_snapshot(
        snapshot_df=_base_snapshot(age_years=45),
        df_atz=pd.DataFrame(),
        target_date=target_date,
        base_date=base_date,
        abgaenge_params={
            "components": {
                "atz": False,
                "retirement": False,
                "quit": False,
                "ruhend": False,
            },
            "random_seed": 42,
        },
        zugaenge_params=_inactive_zugaenge_params(),
    )

    future = result.future_snapshot_df[result.future_snapshot_df["Is_Vacant"] == False].copy()

    assert len(future) == 1
    assert result.metadata["used_simulation"] is True
    assert future["Alter_Jahre"].iloc[0] > 45.9
    assert future["PersNr"].iloc[0] == "000001"


def test_compact_simulation_vacates_row_for_certain_retirement():
    base_date = pd.Timestamp("2025-12-31")
    target_date = pd.Timestamp("2026-01-31")

    result = simulate_compact_snapshot(
        snapshot_df=_base_snapshot(age_years=66),
        df_atz=pd.DataFrame(),
        target_date=target_date,
        base_date=base_date,
        abgaenge_params={
            "components": {
                "atz": False,
                "retirement": True,
                "quit": False,
                "ruhend": False,
            },
            "retirement": {
                "rent_rate_65": 1.0,
                "rent_rate_60_65": 0.0,
            },
            "random_seed": 42,
        },
        zugaenge_params=_inactive_zugaenge_params(),
    )

    future = result.future_snapshot_df

    assert future["Is_Vacant"].all()
    assert future["MAK_Calculated"].sum() == 0.0


def test_vacate_rows_handles_bool_and_integer_person_fields_without_type_error():
    df = pd.DataFrame(
        {
            "PersNr": ["000001"],
            "Personalnummer": ["000001"],
            "ATZ_Status": ["Freistellungsphase"],
            "ist_atz_fr": pd.Series([True], dtype=bool),
            "GebDatum": [pd.Timestamp("1980-01-01")],
            "Eintritt": [pd.Timestamp("2010-01-01")],
            "Austritt": [pd.NaT],
            "Alter": pd.Series([46], dtype="int64"),
            "Alter_Jahre": pd.Series([46], dtype="int64"),
            "MAK_Calculated": [1.0],
            "Is_Vacant": [False],
        }
    )

    out = _vacate_rows(df, pd.Series([True], index=df.index))

    assert bool(out["Is_Vacant"].iloc[0]) is True
    assert pd.isna(out["PersNr"].iloc[0])
    assert pd.isna(out["Personalnummer"].iloc[0])
    assert pd.isna(out["ATZ_Status"].iloc[0])
    assert bool(out["ist_atz_fr"].iloc[0]) is False
    assert pd.isna(out["GebDatum"].iloc[0])
    assert pd.isna(out["Eintritt"].iloc[0])
    assert pd.isna(out["Austritt"].iloc[0])
    assert pd.isna(out["Alter"].iloc[0])
    assert pd.isna(out["Alter_Jahre"].iloc[0])
    assert out["Alter"].dtype == "Int64"
    assert out["Alter_Jahre"].dtype == "Int64"
    assert out["MAK_Calculated"].iloc[0] == 0.0
