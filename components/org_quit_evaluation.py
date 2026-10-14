"""Illustrative binomial evaluation of selected yearly quit counts."""
from __future__ import annotations

from math import comb, ceil, floor, sqrt

import numpy as np
import pandas as pd
import streamlit as st

from components.org_simulation_yearly import yearly_periods


def _tail_probability(n: int, p: float, observed: int) -> float:
    if observed <= 0:
        return 1.0
    if observed > n:
        return 0.0
    return sum(comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(observed, n + 1))


def summarize_quit_evaluation(frame: pd.DataFrame) -> dict:
    """Indicative distribution conditional on the displayed yearly starting pools."""
    observed = int(frame["Beobachtete Kündigungen"].sum())
    if "Erwartete Kündigungen (Simulationsquoten)" in frame:
        expected = float(frame["Erwartete Kündigungen (Simulationsquoten)"].sum())
        variance = float(frame["Varianz"].sum())
        # Conditional normal approximation to the sum of the simulated monthly draws.
        margin = 2.576 * sqrt(max(variance, 0.0))
        lower = max(0, ceil(expected - margin - 0.5))
        upper = max(lower, floor(expected + margin + 0.5))
        return {"observed": observed, "expected": expected, "lower": lower, "upper": upper,
                "within_interval": lower <= observed <= upper}
    expected = float(frame["Erwartete Kündigungen bei Basisquote"].sum())
    distribution = np.array([1.0])
    for n_value, expected_value in zip(frame["Köpfe zu Jahresbeginn (Näherung)"], frame["Erwartete Kündigungen bei Basisquote"]):
        n = int(n_value)
        p = 0.0 if n == 0 else float(expected_value) / n
        one_year = np.array([comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1)])
        distribution = np.convolve(distribution, one_year)
    cdf = np.cumsum(distribution)
    lower = int(np.searchsorted(cdf, 0.005))
    upper = int(np.searchsorted(cdf, 0.995))
    return {"observed": observed, "expected": expected, "lower": lower, "upper": upper,
            "within_interval": lower <= observed <= upper}


def build_quit_chart_data(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    expected_column = ("Erwartete Kündigungen (Simulationsquoten)" if
                       "Erwartete Kündigungen (Simulationsquoten)" in frame else
                       "Erwartete Kündigungen bei Basisquote")
    observed = frame[[dimension, "Jahr", "Beobachtete Kündigungen"]].rename(
        columns={"Beobachtete Kündigungen": "Anzahl"}).copy()
    observed["Serie"] = observed[dimension].astype(str) + " · beobachtet"
    expected = frame[[dimension, "Jahr", expected_column]].rename(columns={expected_column: "Anzahl"}).copy()
    expected["Serie"] = expected[dimension].astype(str) + " · erwartet"
    return pd.concat([observed, expected], ignore_index=True)[["Jahr", "Serie", "Anzahl"]]


def build_quit_evaluation(summary: dict, *, dimension: str, years: list[int], organizations: list[str]) -> pd.DataFrame:
    """Use saved run data; counts are an approximation to the eligible quit pool."""
    audit = summary.get("quit_probability_audit")
    if isinstance(audit, pd.DataFrame) and not audit.empty:
        work = audit.copy()
        if dimension == "OE-Cluster":
            mapping = summary.get("cluster_mapping", {}).get("oe_map", {})
            work[dimension] = work["Organisationseinheit"].map(mapping).fillna("Nicht zugeordnet")
        work = work.loc[work["Jahr"].isin(years) & work[dimension].isin(organizations)]
        if work.empty:
            return pd.DataFrame()
        group = work.groupby([dimension, "Jahr"], as_index=False)[
            ["exposures", "expected", "variance", "annual_rate_exposure", "person_years", "observed"]].sum()
        group = group.rename(columns={"exposures": "Kündigungsprüfungen", "expected": "Erwartete Kündigungen (Simulationsquoten)",
                                      "variance": "Varianz", "observed": "Beobachtete Kündigungen"})
        group["Gewichtete Jahresquote"] = group["annual_rate_exposure"].div(
            group["person_years"].where(group["person_years"].ne(0))).fillna(0.0)
        return group
    params = summary.get("abgaenge_params")
    baseline = summary.get("stock_basis", {}).get("baseline_snapshot")
    if not isinstance(params, dict) or baseline is None or "PersNr" not in baseline:
        return pd.DataFrame()
    rate = float(params.get("quit", {}).get("quit_rate_base", 0.05))
    if not 0 <= rate <= 1:
        return pd.DataFrame()
    periods = yearly_periods(summary["metadata"])
    events = summary["events"]
    base = baseline.copy()
    if dimension == "OE-Cluster":
        mapping = summary.get("cluster_mapping", {}).get("oe_map", {})
        base[dimension] = base.Organisationseinheit.map(mapping).fillna("Nicht zugeordnet")
    if "Is_Vacant" in base:
        base = base.loc[~base.Is_Vacant.fillna(True).astype(bool)]
    base = base.dropna(subset=["PersNr"])
    rows = []
    for org in organizations:
        initial = int(base.loc[base[dimension].eq(org), "PersNr"].astype(str).nunique())
        org_events = events.loc[events[dimension].eq(org)]
        for period in periods.itertuples(index=False):
            if period.Jahr not in years:
                continue
            earlier = org_events.loc[org_events.Jahr.lt(period.Jahr), "Köpfe-Veränderung"].sum()
            at_risk = max(0, int(round(initial + earlier)))
            observed = int(org_events.loc[org_events.Jahr.eq(period.Jahr) & org_events.Ereignisart.eq("QUIT"), "Abgänge"].sum())
            days = (period.Bis - period.Von).days + 1
            annual_probability = 1 - (1 - rate) ** (days / 365.25)
            exact = (comb(at_risk, observed) * annual_probability ** observed *
                     (1 - annual_probability) ** (at_risk - observed)) if observed <= at_risk else 0.0
            rows.append({dimension: org, "Jahr": period.Jahr, "Köpfe zu Jahresbeginn (Näherung)": at_risk,
                         "Beobachtete Kündigungen": observed,
                         "Erwartete Kündigungen bei Basisquote": at_risk * annual_probability,
                         "P(genau so viele) bei Basisquote": exact,
                         "P(mindestens so viele) bei Basisquote": _tail_probability(at_risk, annual_probability, observed),
                         "Betrachtete Tage": days})
    return pd.DataFrame(rows)


def render_quit_evaluation(summary: dict, *, dimension: str, years: list[int], organizations: list[str], event_types: list[str]) -> pd.DataFrame | None:
    st.subheader("Einordnung der Kündigungen")
    if "QUIT" not in event_types:
        st.info("Für die Einordnung bitte die Ereignisart Kündigung auswählen.")
        return None
    frame = build_quit_evaluation(summary, dimension=dimension, years=years, organizations=organizations)
    if frame.empty:
        st.info("Die gespeicherten Abgangsparameter oder die Ausgangsbasis fehlen. Bitte die Simulation neu berechnen.")
        return None
    params = summary["abgaenge_params"]["quit"]
    rate = float(params.get("quit_rate_base", 0.05))
    if "Erwartete Kündigungen (Simulationsquoten)" in frame:
        st.caption("Auswertung der im gespeicherten Lauf tatsächlich verwendeten individuellen Kündigungswahrscheinlichkeiten nach OE, Alter und Jahresanpassung. Bei Clustern werden die Werte der zugehörigen OEs summiert.")
        from components.org_evaluation_chart import render_comparison_bar_chart
        render_comparison_bar_chart(build_quit_chart_data(frame, dimension))
        overall = summarize_quit_evaluation(frame)
        person_years = float(frame["person_years"].sum())
        weighted_rate = float(frame["annual_rate_exposure"].sum()) / person_years if person_years else 0.0
        cols = st.columns(4)
        cols[0].metric("Kündigungen in der Auswahl", overall["observed"])
        cols[1].metric("Erwartet (Simulationsquoten)", f'{overall["expected"]:.1f}')
        cols[2].metric("Gewichtete Jahresquote", f"{weighted_rate:.1%}")
        cols[3].metric("Vergleichsbereich (99 %, Näherung)", f'{overall["lower"]}–{overall["upper"]}')
        st.caption("Die gewichtete Jahresquote mittelt die individuellen Quoten nach kündigungsberechtigter Personenzeit. Erwartet ist die Summe der Monatswahrscheinlichkeiten; der Vergleichsbereich verwendet deren Varianz und eine Normalnäherung. Durch Austritte und andere Ereignisse sind die Monatsprüfungen voneinander abhängig.")
        if overall["within_interval"]:
            st.success("Die beobachtete Gesamtzahl liegt im näherungsweisen 99-%-Bereich der verwendeten Simulationsquoten.")
        else:
            st.error("Die beobachtete Gesamtzahl liegt außerhalb des näherungsweisen 99-%-Bereichs der verwendeten Simulationsquoten.")
        display = frame.drop(columns=["Varianz", "annual_rate_exposure", "person_years"]).copy()
        display["Erwartete Kündigungen (Simulationsquoten)"] = display["Erwartete Kündigungen (Simulationsquoten)"].round(2)
        display["Gewichtete Jahresquote"] = display["Gewichtete Jahresquote"].map(lambda x: f"{x:.1%}")
        st.dataframe(display, hide_index=True, use_container_width=True)
        return frame
    st.caption(f"Vereinfachte Wahrscheinlichkeit mit {rate:.1%} Basisquote pro Person und Jahr. Die Zahl zu Jahresbeginn wird aus Ausgangsbestand und bisherigen Kopfereignissen geschätzt. Tatsächlich kündigungsberechtigte Personen können wegen ATZ und anderer Ereignisse abweichen.")
    if params.get("use_quit_matrix", True) or params.get("quit_adjustments", {}).get("more") or params.get("quit_adjustments", {}).get("less"):
        st.warning("Im gespeicherten Lauf können Matrixquoten oder Jahresanpassungen die individuelle Kündigungswahrscheinlichkeit ändern. Die Wahrscheinlichkeiten unten sind eine Orientierung mit der Basisquote, keine exakte Bewertung des Simulationslaufs.")
    from components.org_evaluation_chart import render_comparison_bar_chart
    render_comparison_bar_chart(build_quit_chart_data(frame, dimension))
    overall = summarize_quit_evaluation(frame)
    cols = st.columns(4)
    cols[0].metric("Kündigungen in der Auswahl", overall["observed"])
    cols[1].metric("Erwartet bei Basisquote", f'{overall["expected"]:.1f}')
    cols[2].metric("Verwendete Basisquote/Jahr", f"{rate:.1%}")
    cols[3].metric("Vergleichsbereich (99 %)", f'{overall["lower"]}–{overall["upper"]}')
    st.caption("Nachrechnung: Je OE und Jahr gilt Erwartungswert = Köpfe zu Jahresbeginn × [1 − (1 − Basisquote)^(betrachtete Tage / 365,25)]. Die Kachel ‚Erwartet‘ summiert diese Werte über alle ausgewählten OEs und Jahre. Die Basisquote selbst wird dabei nicht addiert oder gemittelt.")
    if overall["within_interval"]:
        edge = " Sie liegt am Rand dieses Bereichs." if overall["observed"] in (overall["lower"], overall["upper"]) else ""
        st.success("Die beobachtete Gesamtzahl liegt im 99-%-Bereich des vereinfachten Modells. Das ist mit der Basisquote vereinbar." + edge)
    else:
        st.error("Die beobachtete Gesamtzahl liegt außerhalb des 99-%-Bereichs des vereinfachten Modells. Prüfen Sie die tatsächlichen Matrixquoten und den kündigungsberechtigten Bestand.")
    st.caption("Der Vergleichsbereich setzt je Jahr den angezeigten Anfangsbestand und eine einheitliche Basisquote voraus. Er ist kein Konfidenzintervall für die tatsächliche Kündigungsquote und berücksichtigt keine Abhängigkeiten zwischen Jahren.")
    display = frame.copy()
    display["Erwartete Kündigungen bei Basisquote"] = display["Erwartete Kündigungen bei Basisquote"].round(2)
    display["P(genau so viele) bei Basisquote"] = (100 * display["P(genau so viele) bei Basisquote"]).round(1).map(lambda x: f"{x:.1f} %")
    display["P(mindestens so viele) bei Basisquote"] = (100 * display["P(mindestens so viele) bei Basisquote"]).round(1).map(lambda x: f"{x:.1f} %")
    st.dataframe(display, hide_index=True, use_container_width=True)
    return frame
