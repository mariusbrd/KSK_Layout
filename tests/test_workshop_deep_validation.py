from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from abgaenge.forecast import run_forecast_abgaenge
from abgaenge.schemas import (
    REASON_ATZ_AR_TO_FR,
    REASON_ATZ_END,
    REASON_QUIT,
    REASON_RETIREMENT,
    REASON_RUHEND_RETURN,
    REASON_RUHEND_START,
)
from abgaenge.visuals import build_charts
from components.sidebar import apply_event_filters_with_state, get_effective_metric_view
from config.settings import BASE_SALARY, EMPLOYER_COST_FACTOR, STEP_MULTIPLIER
from dataloader.cluster_manager import enrich_jf_clusters
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.loader import enrich_snapshot_data
from utils.compact_page_loader import load_compact_page_module


BASE_DATE = pd.Timestamp("2025-12-31")


@pytest.fixture(scope="module")
def compact():
    return load_compact_page_module()


@pytest.fixture(autouse=True)
def _clear_streamlit_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


def _snapshot_row(
    persnr: str | None,
    *,
    org: str,
    jobfamily: str,
    planstelle: str,
    mak: float,
    eur: float,
    soll_fte: float,
    soll_eur: float,
    is_vacant: bool = False,
    is_excluded: bool = False,
    exclusion_group: str | None = None,
    soll_hours: float = 39.0,
) -> dict:
    return {
        "PersNr": persnr,
        "Personalnummer": persnr,
        "Is_Vacant": bool(is_vacant),
        "Is_Excluded": bool(is_excluded),
        "Exclusion_Group": exclusion_group,
        "GebDatum": pd.Timestamp("1980-01-01"),
        "Eintritt": pd.Timestamp("2010-01-01"),
        "Austritt": pd.NaT,
        "Status kundenindividuell": "Aktives Beschaeftigungsverhaeltnis",
        "Sollarbeitszeit": soll_hours,
        "Soll_FTE": soll_fte,
        "FTE_person": mak,
        "FTE_assigned": mak,
        "BsGrd": mak * 100.0,
        "MAK_Calculated": mak,
        "MAK_Reporting": mak,
        "MAK": mak,
        "mak": mak,
        "EUR_Reporting": eur,
        "Total_Cost_Year": eur,
        "Soll_Cost_Year": soll_eur,
        "Organisationseinheit": org,
        "K\u00fcrzel OrgEinheit": org,
        "OE-Cluster": f"{org}-Cluster",
        "JF-Cluster": f"{jobfamily}-Cluster" if jobfamily else "Sonstiges",
        "Planstellennr": planstelle,
        "Planstelle": planstelle,
        "Jobfamily": jobfamily,
        "TrfGr": "E9A",
        "St": 3,
        "Geschlecht": "w",
        "Text Gsch": "weiblich",
        "Vertragsart": "Unbefristet",
        "MitarbGruppenbez.": "Beschaeftigte",
        "Ausbildung": "Bankberufsabschluss",
        "ATZ_Status": "Kein ATZ",
    }


def _workshop_snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _snapshot_row(
                "000001",
                org="OE_A",
                jobfamily="JF_A",
                planstelle="P1A",
                mak=0.6,
                eur=60.0,
                soll_fte=0.6,
                soll_eur=72.0,
            ),
            _snapshot_row(
                "000001",
                org="OE_A",
                jobfamily="JF_A",
                planstelle="P1B",
                mak=0.4,
                eur=40.0,
                soll_fte=0.4,
                soll_eur=48.0,
            ),
            _snapshot_row(
                "000002",
                org="OE_A",
                jobfamily="JF_B",
                planstelle="P2",
                mak=0.5,
                eur=50.0,
                soll_fte=1.0,
                soll_eur=80.0,
            ),
            _snapshot_row(
                "000003",
                org="OE_B",
                jobfamily="UNMAPPED",
                planstelle="P3",
                mak=1.0,
                eur=90.0,
                soll_fte=1.0,
                soll_eur=90.0,
            ),
            _snapshot_row(
                None,
                org="OE_B",
                jobfamily="JF_A",
                planstelle="P4",
                mak=0.0,
                eur=0.0,
                soll_fte=1.0,
                soll_eur=100.0,
                is_vacant=True,
            ),
            _snapshot_row(
                None,
                org="OE_EXCL",
                jobfamily="JF_X",
                planstelle="P5",
                mak=0.0,
                eur=0.0,
                soll_fte=1.0,
                soll_eur=200.0,
                is_vacant=True,
                is_excluded=True,
                exclusion_group="Governance",
            ),
            _snapshot_row(
                "000004",
                org="OE_T",
                jobfamily="JF_A",
                planstelle="P6",
                mak=0.1,
                eur=10.0,
                soll_fte=0.01,
                soll_eur=1.0,
                soll_hours=0.01,
            ),
        ]
    )


def _as_value_dict(df: pd.DataFrame, key_col: str) -> dict[str, float]:
    return {str(row[key_col]): float(row["IST"]) for _, row in df.iterrows()}


def test_uc01_bestand_structure_by_oe_and_jobfamily_against_manual_oracle(compact):
    prepared = compact.prepare_compact_data(_workshop_snapshot())

    oe_heads = compact.create_breakdown_table(prepared, "Organisationseinheit", "Headcount")
    jf_heads = compact.create_breakdown_table(prepared, "Jobfamily", "Headcount")
    oe_mak = compact.create_breakdown_table(prepared, "Organisationseinheit", "MAK_Reporting")
    jf_eur = compact.create_breakdown_table(prepared, "Jobfamily", "EUR_Reporting")

    assert _as_value_dict(oe_heads, "Organisationseinheit") == {
        "OE_A": 2.0,
        "OE_B": 1.0,
        "OE_T": 1.0,
    }
    assert _as_value_dict(jf_heads, "Jobfamily") == {
        "JF_A": 2.0,
        "JF_B": 1.0,
        "UNMAPPED": 1.0,
    }
    assert oe_heads["IST"].sum() == 4
    assert jf_heads["IST"].sum() == 4
    assert oe_mak["IST"].sum() == pytest.approx(2.6)
    assert jf_eur["IST"].sum() == pytest.approx(250.0)

    combined = prepared[
        (prepared["Organisationseinheit"] == "OE_A")
        & (prepared["Jobfamily"] == "JF_A")
    ]
    combined_heads = compact.create_breakdown_table(combined, "Jobfamily", "Headcount")
    assert combined_heads["IST"].sum() == 1

    sequential = prepared[prepared["Organisationseinheit"] == "OE_A"]
    sequential = sequential[sequential["Jobfamily"] == "JF_A"]
    combined_once = prepared[
        (prepared["Organisationseinheit"] == "OE_A")
        & (prepared["Jobfamily"] == "JF_A")
    ]
    pd.testing.assert_frame_equal(
        sequential.reset_index(drop=True),
        combined_once.reset_index(drop=True),
    )


def test_uc02_metric_mapping_and_attrition_metric_scope_are_consistent(compact):
    assert compact._compensation_metric_columns("Koepfe", "IST") == (
        "IST_Kopf",
        None,
        None,
        "K\u00f6pfe",
    )
    assert compact._compensation_metric_columns("Koepfe", "Delta")[0] == "DELTA_Koepfe_View"
    assert compact._compensation_metric_columns("MAK", "Delta")[0] == "DELTA_MAK_View"
    assert compact._compensation_metric_columns("EUR", "Delta")[0] == "DELTA_EUR_View"

    # EUR-Ansicht wurde aus der Sidebar-Pille entfernt: ein stale "EUR"-Session-Wert
    # (z. B. aus einer Session vor der Umstellung) wird schon bei der Initialisierung
    # defensiv auf MAK zurueckgesetzt, statt erst als Seiten-Fallback-Hinweis sichtbar
    # zu werden - es gibt daher keinen Mismatch-Hinweis mehr zu pruefen.
    st.session_state["global_metric_view"] = "EUR"
    effective, hint = get_effective_metric_view(["K\u00f6pfe", "MAK"], fallback="MAK")
    assert effective == "MAK"
    assert hint is None


def test_uc03_soll_ist_deltas_exclusions_and_zero_soll_against_oracle(compact):
    prepared = compact.prepare_compact_data(_workshop_snapshot())
    comp_df = compact.build_compact_compensation_planlevel_df(prepared)

    assert (comp_df["IST_MAK"] - comp_df["SOLL_MAK_View"]).equals(comp_df["DELTA_MAK_View"])
    assert (comp_df["IST_EUR"] - comp_df["SOLL_EUR_View"]).equals(comp_df["DELTA_EUR_View"])
    assert (comp_df["IST_Kopf"] - comp_df["SOLL_Planstellen_View"]).equals(
        comp_df["DELTA_Koepfe_View"]
    )

    excluded = comp_df[comp_df["Exclusion_Group"] == "Governance"].iloc[0]
    assert excluded["SOLL_MAK"] == pytest.approx(1.0)
    assert excluded["SOLL_EUR"] == pytest.approx(200.0)
    assert excluded["SOLL_MAK_View"] == pytest.approx(0.0)
    assert excluded["SOLL_EUR_View"] == pytest.approx(0.0)

    assert comp_df["SOLL_MAK_View"].sum() <= comp_df["SOLL_MAK"].sum()
    assert comp_df["SOLL_EUR_View"].sum() <= comp_df["SOLL_EUR"].sum()

    technical = comp_df[comp_df["PersNr"] == "000004"].iloc[0]
    assert bool(technical["Is_Technical_Position"]) is True
    assert technical["SOLL_Kopf"] == 0
    assert technical["SOLL_Planstellen_View"] == 0

    manual = pd.DataFrame(
        [
            {"Dim": "A", "IST": 2.0, "SOLL": 4.0},
            {"Dim": "B", "IST": 1.0, "SOLL": 0.0},
        ]
    )
    result = compact.create_breakdown_table(manual, "Dim", "IST", include_soll=True, soll_col="SOLL")
    by_dim = {row["Dim"]: row for _, row in result.iterrows()}
    assert by_dim["A"]["Delta"] == pytest.approx(-2.0)
    assert by_dim["A"]["Erf\u00fcllungsgrad"] == pytest.approx(0.5)
    assert by_dim["B"]["Delta"] == pytest.approx(1.0)
    assert by_dim["B"]["Erf\u00fcllungsgrad"] == pytest.approx(0.0)


def _employee_for_attrition(
    persnr: str,
    age: int,
    *,
    org: str,
    jobfamily: str,
    oe_cluster: str,
    jf_cluster: str,
    mak: float,
) -> dict:
    if age < 30:
        cohort = "25-29"
    elif age < 45:
        cohort = "35-39"
    elif age < 55:
        cohort = "45-49"
    elif age < 60:
        cohort = "55-59"
    elif age < 65:
        cohort = "60-64"
    else:
        cohort = "65-69"

    return {
        "PersNr": persnr,
        "GebDatum": BASE_DATE - pd.DateOffset(years=age),
        "Eintritt": BASE_DATE - pd.DateOffset(years=8),
        "BsGrd": mak * 100.0,
        "Status kundenindividuell": "Aktives Beschaeftigungsverhaeltnis",
        "Sollarbeitszeit": 39.0,
        "MAK_Calculated": mak,
        "MAK": mak,
        "mak": mak,
        "Organisationseinheit": org,
        "K\u00fcrzel OrgEinheit": org,
        "Jobfamily": jobfamily,
        "OE-Cluster": oe_cluster,
        "JF-Cluster": jf_cluster,
        "Alterskohorte": cohort,
        "Geschlecht": "w",
        "Arbeitszeit": "Vollzeit" if mak >= 1.0 else "Teilzeit",
        "Ausbildung": "Bankberufsabschluss",
        "ATZ_Status": "Kein ATZ",
        "Planstelle": jobfamily,
        "TrfGr": "E9A",
        "St": 3,
    }


def _attrition_population() -> tuple[pd.DataFrame, pd.DataFrame]:
    df_ma = pd.DataFrame(
        [
            _employee_for_attrition(
                "001001",
                66,
                org="OE_RET",
                jobfamily="JF_RET",
                oe_cluster="OC_1",
                jf_cluster="JC_RET",
                mak=1.0,
            ),
            _employee_for_attrition(
                "001002",
                35,
                org="OE_QUIT",
                jobfamily="JF_QUIT",
                oe_cluster="OC_2",
                jf_cluster="JC_QUIT",
                mak=0.5,
            ),
            _employee_for_attrition(
                "001003",
                58,
                org="OE_ATZ",
                jobfamily="JF_ATZ",
                oe_cluster="OC_1",
                jf_cluster="JC_ATZ",
                mak=0.75,
            ),
        ]
    )
    df_atz = pd.DataFrame(
        [
            {
                "PersNr": "001003",
                "Phase": "AR",
                "Beginn": pd.Timestamp("2024-01-01"),
                "Ende": pd.Timestamp("2026-01-31"),
                "Ende ATZ Vertrag": pd.Timestamp("2026-04-30"),
            },
            {
                "PersNr": "001003",
                "Phase": "FR",
                "Beginn": pd.Timestamp("2026-02-01"),
                "Ende": pd.Timestamp("2026-04-30"),
                "Ende ATZ Vertrag": pd.Timestamp("2026-04-30"),
            },
        ]
    )
    return df_ma, df_atz


def _attrition_params() -> dict:
    return {
        "random_seed": 42,
        "components": {
            "atz": True,
            "retirement": True,
            "quit": True,
            "ruhend": False,
        },
        "atz": {
            "new_atz_rate": 0.0,
            "use_atz_matrix": False,
            "atz_eligible_age_min": 55,
            "atz_eligible_age_max": 60,
            "atz_duration_ar_years": 0.25,
            "atz_duration_fr_years": 0.25,
        },
        "retirement": {"rent_rate_65": 1.0, "rent_rate_60_65": 0.0},
        "quit": {"quit_rate_base": 1.0, "use_quit_matrix": False},
        "ruhend": {"ruhend_new_cases_per_year": 99, "ruhend_return_rate": 1.0},
    }


def _run_attrition_oracle_case() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_ma, df_atz = _attrition_population()
    result = run_forecast_abgaenge(
        df_ma=df_ma,
        df_atz=df_atz,
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-12-31"),
        freq="M",
        params=_attrition_params(),
    )
    return df_ma, df_atz, result["events_person_level"]


def test_uc04_abgangsarten_zeitraum_mak_loss_and_reason_charts_against_oracle():
    _, _, events = _run_attrition_oracle_case()

    observed_reasons = set(events["reason_code"])
    assert REASON_RETIREMENT in observed_reasons
    assert REASON_QUIT in observed_reasons
    assert REASON_ATZ_AR_TO_FR in observed_reasons
    assert REASON_ATZ_END in observed_reasons
    assert REASON_RUHEND_START not in observed_reasons
    assert REASON_RUHEND_RETURN not in observed_reasons

    assert pd.to_datetime(events["event_date"]).between(
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-12-31 23:59:59"),
    ).all()

    ret = events[events["reason_code"] == REASON_RETIREMENT].iloc[0]
    quit_ev = events[events["reason_code"] == REASON_QUIT].iloc[0]
    atz_ar = events[events["reason_code"] == REASON_ATZ_AR_TO_FR].iloc[0]
    atz_end = events[events["reason_code"] == REASON_ATZ_END].iloc[0]

    assert ret["headcount_change"] == -1
    assert ret["mak_change"] == pytest.approx(-1.0)
    assert quit_ev["headcount_change"] == -1
    assert quit_ev["mak_change"] == pytest.approx(-0.5)
    assert atz_ar["headcount_change"] == 0
    assert atz_ar["mak_change"] == pytest.approx(-0.75)
    assert atz_end["headcount_change"] == -1
    assert atz_end["mak_change"] == pytest.approx(0.0)

    mak_loss = events.loc[events["mak_change"] < 0, "mak_change"].abs().sum()
    assert mak_loss == pytest.approx(2.25)

    charts = build_charts(pd.DataFrame({"period_label": ["2026-01"], "headcount_end": [1], "mak_end": [0.0]}), events, metric_type="MAK")
    reason_fig = charts["bar_reasons_total"]
    plotted_labels = set(reason_fig.data[0].y)
    assert "Rente (direkt)" in plotted_labels
    assert "K\u00fcndigung" in plotted_labels
    assert "ATZ: AR \u2192 FR" in plotted_labels


def test_uc04_atz_end_survives_jobfamily_filter():
    df_ma, _, events = _run_attrition_oracle_case()

    filtered, _, _ = apply_event_filters_with_state(
        events,
        df_ma,
        active_filters={"selected_jobfamilies": ["JF_ATZ"]},
        mode="attrition",
    )

    expected_reasons = {REASON_ATZ_AR_TO_FR, REASON_ATZ_END}
    observed_reasons = set(filtered["reason_code"])
    assert expected_reasons.issubset(observed_reasons)
    assert len(filtered[filtered["reason_code"].isin(expected_reasons)]) == 2

    atz_events = filtered[filtered["reason_code"].isin(expected_reasons)]
    assert set(atz_events["Jobfamily"]) == {"JF_ATZ"}
    assert set(atz_events["Organisationseinheit"]) == {"OE_ATZ"}
    assert set(atz_events["Alterskohorte"]) == {"55-59"}
    assert atz_events["headcount_change"].sum() == -1
    assert atz_events.loc[atz_events["mak_change"] < 0, "mak_change"].abs().sum() == pytest.approx(0.75)

    combined, _, _ = apply_event_filters_with_state(
        events,
        df_ma,
        active_filters={
            "selected_org_units": ["OE_ATZ"],
            "selected_jobfamilies": ["JF_ATZ"],
            "selected_cohorts": ["55-59"],
        },
        mode="attrition",
    )
    assert set(combined["reason_code"]) == expected_reasons
    assert len(combined) == 2


def test_attrition_events_have_required_filter_dimensions():
    df_ma, _, events = _run_attrition_oracle_case()
    enriched, _, _ = apply_event_filters_with_state(
        events,
        df_ma,
        active_filters={},
        mode="attrition",
    )

    required_columns = [
        "persnr",
        "Organisationseinheit",
        "OE-Cluster",
        "Jobfamily",
        "JF-Cluster",
        "Alterskohorte",
        "MAK",
        "reason_code",
        "event_date",
    ]
    for col in required_columns:
        assert col in enriched.columns
        assert not enriched[col].isna().any(), col
        if col not in {"MAK", "event_date"}:
            assert not enriched[col].astype(str).str.strip().isin(["", "nan", "None", "<NA>"]).any(), col

    assert set(enriched["reason_code"]).issuperset(
        {REASON_ATZ_AR_TO_FR, REASON_ATZ_END, REASON_RETIREMENT, REASON_QUIT}
    )


def _disabled_zugaenge_params() -> dict:
    return {
        "azubi": {"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        "trainee": {"active": False, "new_cases_per_year": 0},
        "new_hires": {"active": False, "count_per_year": 0},
        "random_seed": 42,
    }


def _simulation_snapshot() -> pd.DataFrame:
    rows = []
    for persnr, age, org, jf, mak in [
        ("002001", 66, "OE_RET", "JF_RET", 1.0),
        ("002002", 45, "OE_KEEP", "JF_KEEP", 0.8),
        ("002003", 35, "OE_KEEP", "JF_KEEP", 0.6),
    ]:
        rows.append(
            {
                "PersNr": persnr,
                "Personalnummer": persnr,
                "Is_Vacant": False,
                "GebDatum": BASE_DATE - pd.DateOffset(years=age),
                "Eintritt": BASE_DATE - pd.DateOffset(years=5),
                "Austritt": pd.NaT,
                "Status kundenindividuell": "Aktives Beschaeftigungsverhaeltnis",
                "Sollarbeitszeit": 39.0,
                "Soll_FTE": mak,
                "FTE_person": mak,
                "FTE_assigned": mak,
                "BsGrd": mak * 100.0,
                "MAK_Calculated": mak,
                "MAK": mak,
                "mak": mak,
                "Organisationseinheit": org,
                "K\u00fcrzel OrgEinheit": org,
                "Planstelle": jf,
                "Jobfamily": jf,
                "OE-Cluster": f"{org}-Cluster",
                "JF-Cluster": f"{jf}-Cluster",
                "TrfGr": "E9A",
                "St": 3,
                "Geschlecht": "w",
                "Text Gsch": "weiblich",
                "Vertragsart": "Unbefristet",
                "MitarbGruppenbez.": "Beschaeftigte",
                "Ausbildung": "Bankberufsabschluss",
            }
        )
    return pd.DataFrame(rows)


def test_uc05_future_snapshot_without_zugaenge_has_zero_accession_effects():
    snapshot = _simulation_snapshot()
    result = simulate_compact_snapshot(
        snapshot_df=snapshot,
        df_atz=pd.DataFrame(),
        target_date=pd.Timestamp("2026-01-31"),
        base_date=BASE_DATE,
        abgaenge_params={
            "components": {
                "atz": False,
                "retirement": True,
                "quit": False,
                "ruhend": False,
            },
            "retirement": {"rent_rate_65": 1.0, "rent_rate_60_65": 0.0},
            "random_seed": 42,
        },
        zugaenge_params=_disabled_zugaenge_params(),
    )

    assert result.metadata["used_simulation"] is True
    assert result.metadata["zugaenge_events"] == 0
    assert len(result.zugaenge_result.get("events", pd.DataFrame())) == 0
    assert result.metadata["abgaenge_events"] == 1

    future_active = result.future_employee_df[result.future_employee_df["active"] == True]
    active_existing_ids = set(future_active["PersNr"].astype(str))
    assert "002001" not in active_existing_ids
    assert active_existing_ids == {"002002", "002003"}
    assert not future_active.get("is_forecast", pd.Series(False, index=future_active.index)).fillna(False).any()


def test_uc06_governance_costs_stichtag_clusters_and_exclusions_against_oracle(compact):
    st.session_state["employer_cost_factor"] = 1.2
    st.session_state["tvoed_lookup"] = {("E9A", 3): 1000.0}

    tvoed_cost = compact.calculate_soll_cost(
        pd.Series({"Soll_FTE": 0.5, "TrfGr": "E9A", "St": 3})
    )
    assert tvoed_cost == pytest.approx(1000.0 * 0.5 * 1.2)

    fallback_cost = compact.calculate_soll_cost(
        pd.Series({"Soll_FTE": 1.0, "TrfGr": "MISSING", "St": 4})
    )
    assert fallback_cost == pytest.approx(50000 * STEP_MULTIPLIER.get(4, 1.0) * 1.2)
    assert EMPLOYER_COST_FACTOR != 0
    assert BASE_SALARY

    base = pd.DataFrame(
        [
            {
                "PersNr": "7",
                "GebDatum": pd.Timestamp("2000-01-01"),
                "Eintritt": pd.Timestamp("2020-01-01"),
            }
        ]
    )
    early = enrich_snapshot_data(base, stichtag=pd.Timestamp("2025-01-01"))
    late = enrich_snapshot_data(base, stichtag=pd.Timestamp("2026-01-01"))
    assert late["Alter_Jahre"].iloc[0] > early["Alter_Jahre"].iloc[0] + 0.99

    clusters = enrich_jf_clusters(
        pd.DataFrame({"Jobfamily": ["Known", "Missing", ""]}),
        {"Known": "Cluster A"},
    )
    assert clusters.tolist() == ["Cluster A", "Sonstiges", "Sonstiges"]

    comp_df = compact.build_compact_compensation_planlevel_df(
        compact.prepare_compact_data(_workshop_snapshot())
    )
    excluded = comp_df[comp_df["Exclusion_Group"] == "Governance"].iloc[0]
    assert excluded["SOLL_MAK_View"] == 0.0
    assert excluded["SOLL_EUR_View"] == 0.0


def test_metamorphic_longer_forecast_horizon_does_not_reduce_retirement_events():
    df_ma = pd.DataFrame(
        [
            _employee_for_attrition(
                "003001",
                66,
                org="OE_RET",
                jobfamily="JF_RET",
                oe_cluster="OC",
                jf_cluster="JC",
                mak=1.0,
            ),
            _employee_for_attrition(
                "003002",
                67,
                org="OE_RET",
                jobfamily="JF_RET",
                oe_cluster="OC",
                jf_cluster="JC",
                mak=0.8,
            ),
        ]
    )
    params = {
        "random_seed": 42,
        "components": {"atz": False, "retirement": True, "quit": False, "ruhend": False},
        "retirement": {"rent_rate_65": 1.0, "rent_rate_60_65": 0.0},
    }
    short = run_forecast_abgaenge(
        df_ma,
        pd.DataFrame(),
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-06-30"),
        "M",
        params,
    )["events_person_level"]
    long = run_forecast_abgaenge(
        df_ma,
        pd.DataFrame(),
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-12-31"),
        "M",
        params,
    )["events_person_level"]

    short_count = int((short["reason_code"] == REASON_RETIREMENT).sum())
    long_count = int((long["reason_code"] == REASON_RETIREMENT).sum())
    assert long_count >= short_count
