import pandas as pd
import pytest

from components.org_retirement_evaluation import build_retirement_evaluation, build_retirement_chart_data


def _summary():
    return {
        "metadata": {"base_date": "2027-01-01", "target_date": "2027-12-31"},
        "abgaenge_params": {"components": {"retirement": True}, "retirement": {
            "early_retirement_age": 60, "retirement_age": 65,
            "rent_rate_60_65": 0.1, "rent_rate_65": 0.9}},
        "stock_basis": {"baseline_snapshot": pd.DataFrame({
            "PersNr": ["000001", "000002", "000003"],
            "GebDatum": ["1965-01-01", "1959-01-01", "1959-01-01"],
            "Organisationseinheit": ["A", "A", "B"], "Is_Vacant": [False] * 3})},
        "atz_person_ids": {"000003"},
        "events": pd.DataFrame(columns=["persnr", "event_date", "Jahr", "Organisationseinheit",
                                       "Ereignisart", "Köpfe-Veränderung", "age"]),
    }


def test_retirement_expectations_use_annual_probability_once_per_person():
    result = build_retirement_evaluation(_summary(), dimension="Organisationseinheit", years=[2027], organizations=["A", "B"])
    assert result is not None
    assert result["cohorts"]["Frührente"]["expected"] == pytest.approx(0.096814, abs=0.0001)
    assert result["cohorts"]["Reguläre Rente"]["expected"] == pytest.approx(0.891971, abs=0.0001)
    assert result["annual"].Organisationseinheit.tolist() == ["A", "A"]
    assert result["cohorts"]["Reguläre Rente"]["upper"] == 1
    chart = build_retirement_chart_data(result, dimension="Organisationseinheit", years=[2027], organizations=["A", "B"])
    assert len(chart) == 8
    assert chart.loc[chart.Serie.eq("B · Reguläre Rente · erwartet"), "Anzahl"].iloc[0] == 0


def test_observed_retirement_does_not_truncate_its_own_annual_probability():
    summary = _summary()
    summary["events"] = pd.DataFrame([{
        "persnr": "000002", "event_date": pd.Timestamp("2027-06-30"), "Jahr": 2027,
        "Organisationseinheit": "A", "Ereignisart": "RETIREMENT",
        "Köpfe-Veränderung": -1, "age": 68.0}])
    result = build_retirement_evaluation(summary, dimension="Organisationseinheit", years=[2027], organizations=["A"])
    assert result["cohorts"]["Reguläre Rente"]["observed"] == 1
    assert result["cohorts"]["Reguläre Rente"]["expected"] == pytest.approx(0.891971, abs=0.0001)
