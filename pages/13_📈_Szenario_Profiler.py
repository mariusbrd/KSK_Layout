"""Temporary, read-only profiler for complete Monte Carlo stock paths."""
from __future__ import annotations

import streamlit as st

from utils.monte_carlo_app_run import REQUEST_KEY, current_ensemble
from utils.scenario_profiler import highlighted_indices, stock_trajectories, trajectory_frequencies
from utils.scenario_profiler_chart import CHART_TYPES, build_profiler_figure
from utils.scenario_profiler_stats import (
    annual_distribution, change_distribution, endpoint_convergence,
    highlighted_yearly_ranks, run_changes,
)
from utils.simulation_validation import current_view


def main() -> None:
    st.title("Szenario-Profiler")
    st.caption("Verbleibender heutiger Bestand je Jahresscheibe, wie auf der OE-Seite. Diese Seite startet keine Simulation.")
    view = current_view(st.session_state, use_approved=False)
    request = st.session_state.get(REQUEST_KEY)
    ensemble = current_ensemble(st.session_state, request) if view is not None and request is not None else None
    if ensemble is None:
        st.info("Bitte zuerst eine Monte-Carlo-Simulation berechnen.")
        return
    samples = ensemble["statistics"].get("departure_stock_samples")
    if not isinstance(samples, list) or not samples:
        st.warning("Für diesen Lauf sind keine geprüften Jahresscheiben des verbleibenden heutigen Bestands gespeichert. Die Positionsbestände werden hier nicht als Ersatz angezeigt.")
        return

    metric_label = st.radio("Bestandskennzahl", ["MAK", "Köpfe"], horizontal=True)
    metric = "mak" if metric_label == "MAK" else "heads"
    level = st.selectbox("Ebene", ["Gesamt", "Organisationseinheit", "OE-Cluster"])
    group = None
    if level != "Gesamt":
        groups = sorted({str(row[2]) for row in samples
                         if row[1] == level and row[3] == metric})
        if not groups:
            st.warning("Für diese Ebene sind keine Jahreswerte gespeichert.")
            return
        group = st.selectbox("Einheit", groups)

    try:
        table = stock_trajectories(ensemble, metric, level, group)
    except ValueError as exc:
        st.error(str(exc))
        return
    if table.empty:
        st.warning("Für diese Auswahl sind keine Jahreswerte gespeichert.")
        return
    if level != "Gesamt":
        st.caption(
            "Die farbigen Läufe werden nach Kündigungen und Bestandsverlauf der Gesamtorganisation "
            "ausgewählt. Bei dieser einzelnen Einheit kann der rot markierte Lauf deshalb "
            "einen hohen oder unveränderten Bestand haben."
        )
    frequencies = trajectory_frequencies(table)
    count = ensemble["count"]
    st.info(
        f"{count} Einzelläufe geladen · {len(frequencies)} unterschiedliche Bestandsverläufe. "
        "Läufe mit identischen Jahreswerten liegen im Diagramm exakt übereinander."
    )
    highlights = highlighted_indices(ensemble)
    chart_type = st.radio("Grafikart", CHART_TYPES, horizontal=True, key="scenario_profiler_chart_type")
    fig = build_profiler_figure(table, highlights, chart_type, metric_label)
    st.plotly_chart(fig, use_container_width=True)
    if chart_type == "Punktewolke":
        st.caption("Die Punkte sind nur horizontal innerhalb ihres Jahres verteilt, damit gleiche Werte sichtbar werden. Ihre Höhe und damit der Bestand bleibt unverändert.")
    pairs = ensemble["seed_pairs"]
    st.caption(
        f"{count} Läufe; graue Elemente zeigen die übrigen Ergebnisse. Min/Max beziehen sich auf die "
        "gesamten Kündigungsabgänge, nicht auf den kleinsten/größten Jahresbestand. "
        "Der Medianlauf ist ein vollständiger Lauf nahe den jahresweisen Medianen von Köpfen und MAK. "
        "Die bestehende typische OE-Auswahl misst dagegen die Nähe zum Mittelwert einschließlich Kündigungen."
    )
    st.dataframe([
        {"Markierung": label, "Lauf": index + 1,
         "Seed Abgänge": pairs[index][0], "Seed Zugänge": pairs[index][1]}
        for label, index in highlights.items()
    ], hide_index=True, use_container_width=True)
    with st.expander("Übereinanderliegende Läufe prüfen"):
        st.dataframe([
            {"Anzahl identischer Läufe": int(row["Läufe"]),
             "Laufnummern": ", ".join(str(index + 1) for index in row["Laufindizes"])}
            for _, row in frequencies.iterrows()
        ], hide_index=True, use_container_width=True)

    st.subheader("Verteilung je Jahresscheibe")
    st.caption("P10–P90 beschreibt den Bereich der simulierten Bestände; es ist kein Konfidenzintervall des Mittelwerts.")
    st.dataframe(annual_distribution(table), hide_index=True, use_container_width=True)

    st.subheader("Position der markierten Läufe")
    ranks = highlighted_yearly_ranks(table, highlights)
    selected_date = st.selectbox("Jahresscheibe für Szenariovergleich", sorted(ranks["Stichtag"].unique()),
                                 index=len(ranks["Stichtag"].unique()) - 1)
    st.dataframe(ranks.loc[ranks["Stichtag"].eq(selected_date)],
                 hide_index=True, use_container_width=True)
    st.caption("Der Perzentilrang bezieht sich auf die Bestandswerte aller Läufe dieser Auswahl. Bei Gleichständen erhalten die Läufe denselben mittleren Rang.")
    with st.expander("Perzentilränge aller Jahresscheiben"):
        st.dataframe(ranks, hide_index=True, use_container_width=True)

    st.subheader("Veränderung vom ersten bis zum letzten gespeicherten Stichtag")
    changes = run_changes(table)
    st.dataframe(change_distribution(changes), hide_index=True, use_container_width=True)
    st.caption("Bei einem Startbestand von 0 bleibt die prozentuale Veränderung undefiniert; die absolute Veränderung wird weiterhin ausgewertet.")
    with st.expander("Veränderung je Einzellauf"):
        st.dataframe(changes, hide_index=True, use_container_width=True)

    st.subheader("Stabilität bei wachsender Laufzahl")
    convergence = endpoint_convergence(table)
    st.caption(f"Bestand am letzten gespeicherten Stichtag ({max(table['Datum'])}); "
               "jeweils die ersten 10, 20, 30 … Läufe in fester Seed-Reihenfolge.")
    st.line_chart(convergence.set_index("Läufe")[["Mittelwert", "P10", "P90"]])
    st.dataframe(convergence, hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
