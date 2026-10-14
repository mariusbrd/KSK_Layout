"""Indicative age-cohort check for the saved retirement forecast."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from abgaenge.forecast import _periods
from abgaenge.params import get_retirement_ages
from abgaenge.schemas import normalize_persnr
from components.org_quit_evaluation import summarize_quit_evaluation


def build_retirement_evaluation(summary: dict, *, dimension: str, years: list[int], organizations: list[str]) -> dict | None:
    params = summary.get("abgaenge_params")
    basis = summary.get("stock_basis", {}).get("baseline_snapshot")
    atz_ids = summary.get("atz_person_ids")
    if (not isinstance(params, dict) or basis is None or atz_ids is None or
            not {"PersNr", "GebDatum", "Organisationseinheit"}.issubset(basis.columns)):
        return None
    if params.get("components", {}).get("retirement", True) is False:
        return None
    retirement = params.get("retirement", {})
    regular_age, early_age = get_retirement_ages(retirement)
    rates = {"Frührente": float(retirement.get("rent_rate_60_65", 0.1)),
             "Reguläre Rente": float(retirement.get("rent_rate_65", 0.9))}
    if any(not 0 <= rate <= 1 for rate in rates.values()):
        return None
    people = basis.copy()
    if "Is_Vacant" in people:
        people = people.loc[~people.Is_Vacant.fillna(True).astype(bool)]
    people["PersNr"] = normalize_persnr(people.PersNr)
    people = people.dropna(subset=["PersNr"]).drop_duplicates("PersNr", keep="first")
    people["GebDatum"] = pd.to_datetime(people.GebDatum, errors="coerce")
    people = people.dropna(subset=["GebDatum"])
    if dimension == "OE-Cluster":
        people[dimension] = people.Organisationseinheit.map(summary.get("cluster_mapping", {}).get("oe_map", {})).fillna("Nicht zugeordnet")
    people = people.loc[people[dimension].isin(organizations) & ~people.PersNr.isin(atz_ids)]
    events = summary["events"]
    departures = events.loc[events["Köpfe-Veränderung"].lt(0)].copy()
    departures["_person"] = normalize_persnr(departures.get("persnr", pd.Series(pd.NA, index=departures.index)))
    first_departure = departures.dropna(subset=["_person"]).groupby("_person").event_date.min()
    other_departure = (departures.loc[~departures.Ereignisart.eq("RETIREMENT")]
                       .dropna(subset=["_person"]).groupby("_person").event_date.min())
    start = pd.Timestamp(summary["metadata"]["base_date"])
    end = pd.Timestamp(summary["metadata"]["target_date"])
    periods = [period for period in _periods(start, end, "M") if period.start.year in years]
    exposure_rows = []
    for person in people.itertuples(index=False):
        pid = person.PersNr
        birth = person.GebDatum
        org = getattr(person, dimension) if dimension.isidentifier() else person[people.columns.get_loc(dimension)]
        for year in years:
            year_start = pd.Timestamp(year, 1, 1)
            if pd.notna(first_departure.get(pid, pd.NaT)) and first_departure[pid] < year_start:
                continue
            exit_date = other_departure.get(pid, pd.NaT)
            survival = 1.0
            probabilities = {"Frührente": 0.0, "Reguläre Rente": 0.0}
            eligible_months = {"Frührente": 0, "Reguläre Rente": 0}
            for period in periods:
                if period.start.year != year or (pd.notna(exit_date) and exit_date < period.start):
                    continue
                age = (period.start - birth).days / 365.25
                cohort = "Reguläre Rente" if age >= regular_age else ("Frührente" if age >= early_age else None)
                if cohort is None:
                    continue
                eligible_months[cohort] += 1
                p = 1 - (1 - rates[cohort]) ** (max(1, (period.end - period.start).days) / 365.25)
                probabilities[cohort] += survival * p
                survival *= 1 - p
            for cohort, probability in probabilities.items():
                if eligible_months[cohort]:
                    exposure_rows.append({dimension: org, "Jahr": year, "Altersgruppe": cohort,
                                          "PersNr": pid, "Berechtigte Monate": eligible_months[cohort],
                                          "Individuelle Jahreswahrscheinlichkeit": probability})
    exposure = pd.DataFrame(exposure_rows)
    retirement_events = events.loc[events.Ereignisart.eq("RETIREMENT") & events.Jahr.isin(years) & events[dimension].isin(organizations)].copy()
    if not retirement_events.empty:
        ages = pd.to_numeric(retirement_events.get("age", pd.Series(float("nan"), index=retirement_events.index)), errors="coerce")
        retirement_events["Altersgruppe"] = "Frührente"
        retirement_events.loc[ages.ge(regular_age), "Altersgruppe"] = "Reguläre Rente"
        observed = retirement_events.groupby([dimension, "Jahr", "Altersgruppe"]).size().rename("Beobachtete Renteneintritte")
    else:
        observed = pd.Series(dtype=int, name="Beobachtete Renteneintritte")
    annual = (exposure.groupby([dimension, "Jahr", "Altersgruppe"], as_index=False)
              .agg(**{"Personenmonate": ("Berechtigte Monate", "sum"),
                      "Erwartete Renteneintritte": ("Individuelle Jahreswahrscheinlichkeit", "sum")})) if not exposure.empty else pd.DataFrame(columns=[dimension, "Jahr", "Altersgruppe", "Personenmonate", "Erwartete Renteneintritte"])
    if not observed.empty:
        annual = annual.set_index([dimension, "Jahr", "Altersgruppe"]).join(observed, how="outer").fillna(0).reset_index()
    else:
        annual["Beobachtete Renteneintritte"] = 0
    annual["Beobachtete Renteneintritte"] = annual["Beobachtete Renteneintritte"].astype(int)
    results = {}
    for cohort, rate in rates.items():
        cohort_exposure = exposure.loc[exposure.Altersgruppe.eq(cohort)] if not exposure.empty else pd.DataFrame()
        count = int(annual.loc[annual.Altersgruppe.eq(cohort), "Beobachtete Renteneintritte"].sum())
        if cohort_exposure.empty:
            results[cohort] = {"observed": count, "expected": 0.0, "lower": 0, "upper": 0, "within_interval": count == 0, "rate": rate}
        else:
            check = cohort_exposure.rename(columns={"Individuelle Jahreswahrscheinlichkeit": "Erwartete Kündigungen bei Basisquote"}).copy()
            check["Köpfe zu Jahresbeginn (Näherung)"] = 1
            check["Beobachtete Kündigungen"] = 0
            check.loc[check.index[0], "Beobachtete Kündigungen"] = count
            results[cohort] = {**summarize_quit_evaluation(check), "rate": rate}
    return {"annual": annual, "cohorts": results, "ages": (early_age, regular_age)}


def build_retirement_chart_data(result: dict, *, dimension: str, years: list[int], organizations: list[str]) -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [years, organizations, ["Frührente", "Reguläre Rente"]],
        names=["Jahr", dimension, "Altersgruppe"])
    annual = result["annual"].set_index(["Jahr", dimension, "Altersgruppe"])
    annual = annual.reindex(index, fill_value=0).reset_index()
    frames = []
    for column, label in (("Beobachtete Renteneintritte", "beobachtet"),
                          ("Erwartete Renteneintritte", "erwartet")):
        work = annual[["Jahr", dimension, "Altersgruppe", column]].rename(columns={column: "Anzahl"}).copy()
        work["Serie"] = work[dimension].astype(str) + " · " + work.Altersgruppe + " · " + label
        frames.append(work[["Jahr", "Serie", "Anzahl"]])
    return pd.concat(frames, ignore_index=True)


def render_retirement_evaluation(summary: dict, *, dimension: str, years: list[int], organizations: list[str], event_types: list[str]) -> dict | None:
    if "RETIREMENT" not in event_types:
        return None
    st.subheader("Einordnung der Renteneintritte")
    result = build_retirement_evaluation(summary, dimension=dimension, years=years, organizations=organizations)
    if result is None:
        st.info("Für die Renteneinordnung bitte eine neue Simulation mit gespeicherter Alters- und ATZ-Basis berechnen.")
        return None
    early_age, regular_age = result["ages"]
    st.caption(f"Frührente: {early_age} bis unter {regular_age} Jahre; reguläre Rente: ab {regular_age} Jahren. Die Berechnung verwendet die gespeicherten Jahresquoten und dieselbe Umrechnung auf Monatsperioden wie die Simulations-Engine; dadurch kann die effektive Jahreswahrscheinlichkeit geringfügig unter der angezeigten Jahresquote liegen.")
    from components.org_evaluation_chart import render_comparison_bar_chart
    render_comparison_bar_chart(build_retirement_chart_data(
        result, dimension=dimension, years=years, organizations=organizations))
    for cohort, values in result["cohorts"].items():
        st.markdown(f"**{cohort}**")
        cols = st.columns(4)
        cols[0].metric("Beobachtet", values["observed"])
        cols[1].metric("Erwartet (Näherung)", f'{values["expected"]:.1f}')
        cols[2].metric("Rentenquote/Jahr", f'{values["rate"]:.1%}')
        cols[3].metric("Vergleichsbereich (99 %)", f'{values["lower"]}–{values["upper"]}')
        if values["within_interval"]:
            st.success(f"{cohort}: Die beobachtete Zahl liegt im 99-%-Bereich der Näherung.")
        else:
            st.error(f"{cohort}: Die beobachtete Zahl liegt außerhalb des 99-%-Bereichs der Näherung.")
    st.caption("Die Erwartung berücksichtigt Personen des Ausgangsbestands und ihre monatlichen Altersgruppen. Personen mit ATZ-Eintrag sind ausgeschlossen; spätere Neueinstellungen fehlen in der Näherung. Der Vergleichsbereich behandelt den beobachteten Bestand als fest und bildet Abhängigkeiten zwischen Jahren sowie neu geplante ATZ-Fälle nicht exakt ab.")
    st.dataframe(result["annual"], hide_index=True, use_container_width=True)
    return result
