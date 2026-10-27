import pandas as pd
import pytest

from components.org_atz_evaluation import build_atz_evaluation, build_atz_chart_data
from test_atz_hypothesen_forecast import _base_params, _empty_atz_df, _employee, _run


def _summary():
    return {
        "metadata": {"base_date": "2027-01-01", "target_date": "2028-12-31"},
        "abgaenge_params": {"components": {"atz": True}, "atz": {
            "atz_duration_ar_years": 1, "atz_duration_fr_years": 2}},
        "cluster_mapping": {"oe_map": {"A": "X", "B": "X"}},
        "atz_pivot": pd.DataFrame([
            {"PersNr": "000001", "Organisationseinheit": "A", "atz_origin": "Ausgangsbestand",
             "fr_begin": "2027-06-15", "contract_end": "2028-06-15"},
            {"PersNr": "000002", "Organisationseinheit": "B", "atz_origin": "Simulation",
             "ar_begin": "2027-06-14", "ar_end": "2028-06-14", "fr_begin": "2028-06-15",
             "fr_end": "2030-06-15", "contract_end": "2030-06-15"},
        ]),
        "atz_entry_audit": pd.DataFrame([
            {"period_start": "2027-01-01", "period_end": "2027-01-31", "Jahr": 2027,
             "Organisationseinheit": "A", "Berechtigte Personen": 10,
             "Berechtigte mit positiver Quote": 10, "Erwartete neue ATZ-Fälle": 0.4,
             "Neue ATZ-Fälle": 0},
            {"period_start": "2027-01-01", "period_end": "2027-01-31", "Jahr": 2027,
             "Organisationseinheit": "B", "Berechtigte Personen": 10,
             "Berechtigte mit positiver Quote": 10, "Erwartete neue ATZ-Fälle": 0.4,
             "Neue ATZ-Fälle": 1},
        ]),
        "events": pd.DataFrame([
            {"persnr": "000001", "event_date": pd.Period("2027-06").end_time, "Jahr": 2027,
             "Organisationseinheit": "A", "OE-Cluster": "X", "Ereignisart": "ATZ_AR_TO_FR",
             "Köpfe-Veränderung": 0, "MAK-Veränderung": -1.0},
            {"persnr": "000001", "event_date": pd.Period("2028-06").end_time, "Jahr": 2028,
             "Organisationseinheit": "A", "OE-Cluster": "X", "Ereignisart": "ATZ_END",
             "Köpfe-Veränderung": -1, "MAK-Veränderung": 0.0},
            {"persnr": "000002", "event_date": pd.Period("2028-06").end_time, "Jahr": 2028,
             "Organisationseinheit": "B", "OE-Cluster": "X", "Ereignisart": "ATZ_AR_TO_FR",
             "Köpfe-Veränderung": 0, "MAK-Veränderung": -0.8},
        ]),
        "stock_reconciliation": {"documented_effects": pd.DataFrame()},
    }


def test_atz_control_uses_saved_exposure_and_separates_existing_cases():
    result = build_atz_evaluation(_summary(), dimension="Organisationseinheit",
                                  years=[2027, 2028], organizations=["A", "B"])
    assert result["expected"] == pytest.approx(0.8)
    assert result["observed"] == 1
    assert result["mismatches"] == result["unexpected"] == result["effect_errors"] == 0
    assert result["duration_errors"] == 0
    assert result["annual"]["ATZ-Kopfveränderung"].sum() == -1
    assert result["annual"]["ATZ-MAK-Veränderung"].sum() == pytest.approx(-1.8)
    assert set(result["details"].Herkunft) == {"Ausgangsbestand", "Simulation"}
    chart = build_atz_chart_data(result, dimension="Organisationseinheit")
    assert len(chart) == 24


def test_atz_control_detects_missing_schedule_event_and_wrong_head_effect():
    summary = _summary()
    summary["events"] = summary["events"].iloc[:2].copy()
    summary["events"].loc[summary["events"].Ereignisart.eq("ATZ_END"), "Köpfe-Veränderung"] = 0
    result = build_atz_evaluation(summary, dimension="OE-Cluster", years=[2027, 2028], organizations=["X"])
    assert result["mismatches"] == 1
    assert result["effect_errors"] == 1
    assert result["unexpected"] == 0


def test_forecast_persists_exact_monthly_atz_exposure_without_changing_legacy_pivot():
    people = pd.DataFrame([_employee("1", 57, orgunit="A"), _employee("2", 58, orgunit="B")])
    result = _run(people, _empty_atz_df(), "2026-01-31", _base_params())
    tables = result["tables"]
    audit = tables["atz_entry_audit"]
    assert audit["Berechtigte Personen"].sum() == 2
    assert audit["Neue ATZ-Fälle"].sum() == 2
    assert set(tables["atz_schedule_audit"].atz_origin) == {"Simulation"}
    assert "atz_origin" not in tables["atz_pivot"]
    assert set(tables["atz_schedule_audit"].Organisationseinheit) == {"A", "B"}


def test_existing_release_phase_only_exits_heads_without_repeated_mak_loss():
    summary = _summary()
    summary["atz_pivot"] = pd.DataFrame([{
        "PersNr": "000001", "Organisationseinheit": "A", "atz_origin": "Ausgangsbestand",
        "fr_begin": "2026-06-15", "contract_end": "2028-06-15"}])
    summary["events"] = summary["events"].loc[summary["events"].Ereignisart.eq("ATZ_END")].copy()
    summary["atz_entry_audit"] = summary["atz_entry_audit"].iloc[0:0].copy()
    result = build_atz_evaluation(summary, dimension="Organisationseinheit",
                                  years=[2027, 2028], organizations=["A"])
    assert result["mismatches"] == result["effect_errors"] == 0
    assert result["annual"]["ATZ-Kopfveränderung"].sum() == -1
    assert result["annual"]["ATZ-MAK-Veränderung"].sum() == 0


def test_release_already_active_on_base_date_is_not_a_missing_event():
    summary = _summary()
    summary["metadata"]["base_date"] = "2027-06-15"
    summary["atz_pivot"] = pd.DataFrame([{
        "PersNr": "000001", "Organisationseinheit": "A", "atz_origin": "Ausgangsbestand",
        "fr_begin": "2027-06-15", "contract_end": "2028-06-15",
        "atz_already_fr_at_start": True,
    }])
    summary["events"] = summary["events"].loc[
        summary["events"].Ereignisart.eq("ATZ_END")].copy()
    summary["atz_entry_audit"] = summary["atz_entry_audit"].iloc[0:0].copy()

    result = build_atz_evaluation(summary, dimension="Organisationseinheit",
                                  years=[2027, 2028], organizations=["A"])

    assert result["mismatches"] == 0
    assert result["unexpected"] == 0
    assert result["details"].loc[result["details"].Phase.eq("Freistellungsbeginn"),
                                 "Status"].tolist() == ["Bereits freigestellt am Ausgangsstichtag"]
    assert result["annual"]["Geplant: Freistellungsbeginn"].sum() == 0


def test_transition_for_person_already_released_at_start_is_flagged():
    summary = _summary()
    summary["stock_basis"] = {"baseline_snapshot": pd.DataFrame([{
        "PersNr": "000001", "ist_atz_fr": True}])}
    result = build_atz_evaluation(summary, dimension="Organisationseinheit",
                                  years=[2027, 2028], organizations=["A", "B"])
    assert result["already_released_transitions"] == 1
    assert result["effect_errors"] == 1


def test_existing_release_keeps_contract_end_and_never_loses_mak_twice():
    people = pd.DataFrame([_employee("1", 57, mak=1.0)])
    contract_end = pd.Timestamp("2026-04-30")
    atz = pd.DataFrame([
        {"PersNr": "1", "Phase": "AR", "Beginn": pd.Timestamp("2025-01-01"),
         "Ende": pd.Timestamp("2025-06-30"), "Ende ATZ Vertrag": contract_end},
        {"PersNr": "1", "Phase": "FR", "Beginn": pd.Timestamp("2025-07-01"),
         "Ende": contract_end, "Ende ATZ Vertrag": contract_end},
    ])
    params = _base_params()
    params["atz"]["new_atz_rate"] = 0.0
    result = _run(people, atz, "2026-04-30", params)
    events = result["events_person_level"]
    assert events.reason_code.tolist() == ["ATZ_END"]
    assert events.headcount_change.tolist() == [-1]
    assert events.mak_change.tolist() == [0.0]
    audit = result["tables"]["atz_schedule_audit"].iloc[0]
    assert audit.atz_already_fr_at_start
    assert audit.atz_original_fr_begin == audit.fr_begin == pd.Timestamp("2025-07-01")
    assert audit.atz_original_contract_end == audit.contract_end == contract_end


def test_working_phase_with_zero_mak_does_not_get_fallback_loss():
    people = pd.DataFrame([_employee("2", 57, mak=0.0)])
    atz = pd.DataFrame([
        {"PersNr": "2", "Phase": "AR", "Beginn": pd.Timestamp("2022-01-01"),
         "Ende": pd.Timestamp("2026-01-31"), "Ende ATZ Vertrag": pd.Timestamp("2026-04-30")},
        {"PersNr": "2", "Phase": "FR", "Beginn": pd.Timestamp("2026-02-01"),
         "Ende": pd.Timestamp("2026-04-30"), "Ende ATZ Vertrag": pd.Timestamp("2026-04-30")},
    ])
    params = _base_params()
    params["atz"]["new_atz_rate"] = 0.0
    events = _run(people, atz, "2026-04-30", params)["events_person_level"]
    transition = events.loc[events.reason_code.eq("ATZ_AR_TO_FR")]
    assert len(transition) == 1
    assert transition.mak_change.iloc[0] == 0.0
    assert events.loc[events.reason_code.eq("ATZ_END"), "headcount_change"].tolist() == [-1]
