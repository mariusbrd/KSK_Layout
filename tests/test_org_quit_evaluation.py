import pandas as pd
import pytest

from components.org_quit_evaluation import build_quit_evaluation, build_quit_chart_data, summarize_quit_evaluation


def test_saved_individual_rates_are_aggregated_for_selected_cluster():
    audit = pd.DataFrame({
        "Jahr": [2029, 2029, 2030],
        "Organisationseinheit": ["A", "B", "A"],
        "exposures": [10, 20, 10],
        "expected": [0.2, 1.4, 0.8],
        "variance": [0.19, 1.2, 0.7],
        "annual_rate_exposure": [0.2, 1.4, 0.8],
        "person_years": [1.0, 2.0, 1.0],
        "observed": [0, 2, 1],
    })
    summary = {"quit_probability_audit": audit,
               "cluster_mapping": {"oe_map": {"A": "Cluster 1", "B": "Cluster 1"}}}
    frame = build_quit_evaluation(summary, dimension="OE-Cluster", years=[2029], organizations=["Cluster 1"])
    assert len(frame) == 1
    assert frame.iloc[0]["Erwartete Kündigungen (Simulationsquoten)"] == pytest.approx(1.6)
    assert frame.iloc[0]["Gewichtete Jahresquote"] == pytest.approx(1.6 / 3)
    assert frame.iloc[0]["Beobachtete Kündigungen"] == 2
    assert summarize_quit_evaluation(frame)["expected"] == pytest.approx(1.6)


def test_adjusted_quit_count_uses_approved_events_and_preserves_original_draw_audit():
    audit = pd.DataFrame({
        "Jahr": [2029], "Organisationseinheit": ["A"],
        "exposures": [10], "expected": [0.8], "variance": [0.7],
        "annual_rate_exposure": [0.8], "person_years": [1.0], "observed": [2],
    })
    events = pd.DataFrame({
        "Jahr": [2029], "Organisationseinheit": ["A"],
        "Ereignisart": ["QUIT"], "Abgänge": [1],
    })
    summary = {"quit_probability_audit": audit, "events": events,
               "adjustment": {"kind": "retain_departure"}}
    result = build_quit_evaluation(summary, dimension="Organisationseinheit", years=[2029], organizations=["A"])
    assert result.iloc[0]["Beobachtete Kündigungen"] == 1
    assert result.iloc[0]["Erwartete Kündigungen (Simulationsquoten)"] == pytest.approx(0.8)
    assert audit.iloc[0]["observed"] == 2


def test_quit_evaluation_uses_prior_departures_and_keeps_last_year_plausible():
    events = pd.DataFrame({
        "Organisationseinheit": ["IC BB/Hbg"] * 5,
        "Jahr": [2032, 2033, 2035, 2036, 2036],
        "Ereignisart": ["QUIT"] * 5,
        "Köpfe-Veränderung": [-1] * 5,
        "Abgänge": [1] * 5,
    })
    summary = {
        "metadata": {"base_date": "2026-09-01", "target_date": "2036-12-31"},
        "abgaenge_params": {"quit": {"quit_rate_base": 0.05, "use_quit_matrix": False}},
        "stock_basis": {"baseline_snapshot": pd.DataFrame({
            "PersNr": [f"{i:06d}" for i in range(23)],
            "Organisationseinheit": ["IC BB/Hbg"] * 23, "Is_Vacant": [False] * 23})},
        "events": events,
    }
    result = build_quit_evaluation(summary, dimension="Organisationseinheit", years=[2036], organizations=["IC BB/Hbg"])
    assert result.iloc[0]["Köpfe zu Jahresbeginn (Näherung)"] == 20
    assert result.iloc[0]["Beobachtete Kündigungen"] == 2
    assert result.iloc[0]["P(mindestens so viele) bei Basisquote"] == pytest.approx(0.264, abs=0.002)
    overall = summarize_quit_evaluation(result)
    assert overall["observed"] == 2
    assert overall["expected"] == pytest.approx(1.0, abs=0.01)
    assert (overall["lower"], overall["upper"]) == (0, 4)
    assert overall["within_interval"]
    chart = build_quit_chart_data(result, "Organisationseinheit")
    assert set(chart.Serie) == {"IC BB/Hbg · beobachtet", "IC BB/Hbg · erwartet"}
    assert chart.loc[chart.Serie.str.endswith("beobachtet"), "Anzahl"].sum() == 2


def test_overall_evaluation_flags_a_clear_outlier():
    frame = pd.DataFrame({"Beobachtete Kündigungen": [10],
                          "Köpfe zu Jahresbeginn (Näherung)": [20],
                          "Erwartete Kündigungen bei Basisquote": [1.0]})
    overall = summarize_quit_evaluation(frame)
    assert not overall["within_interval"]
    assert overall["upper"] < 10
