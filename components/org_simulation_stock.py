"""Display only certified, saved departure-only stock bridges."""
import hashlib

import streamlit as st


def render_departure_stock(summary, *, run_signature, level=None, years=None, organizations=None, embedded=False):
    if not embedded:
        st.subheader("Verbleibender Bestand aus Abgangsprognose")
    if not summary or not run_signature or summary.get("run_signature") != run_signature:
        st.info("Für den Bestandsverlauf bitte die Simulation neu berechnen.")
        return None
    bridge = summary.get("stock_reconciliation")
    if not bridge or bridge.get("run_signature") != run_signature:
        st.info("Für den Bestandsverlauf bitte die Simulation neu berechnen.")
        return None
    if bridge["status"] == "Nicht verfügbar":
        st.info(bridge["reason"])
        return None
    if bridge["status"] != "Abgestimmt":
        st.warning("Bestandsüberleitung nicht abgestimmt. " + bridge["reason"])
        st.dataframe(bridge["checks"], hide_index=True, use_container_width=True)
        diagnostics = bridge.get("person_diagnostics")
        if diagnostics is not None and not diagnostics.empty:
            with st.expander("Personenbezogene Ursachenanalyse", expanded=True):
                st.caption("Zusätzliche Effekte ohne belegten Zeitpunkt werden nicht auf Jahre verteilt.")
                st.dataframe(diagnostics, hide_index=True, use_container_width=True)
        return None
    st.success("Mit dem finalen Simulationsbestand abgestimmt: Köpfe exakt, MAK intern ungerundet geprüft.")
    st.caption("Ausgangs-IST plus technische Prognosebasis-Aufbereitung und Berichtswirkung der Abgangsereignisse, einschließlich ATZ-Freistellung "
               "und ruhender Beschäftigung. Originalereignisse bleiben unverändert. Keine Zugänge. Eigene Organisationsauswahl, unabhängig von "
               "Bestandsfiltern und der Auswahl einzelner Ereignisarten. MAK werden wie die Bestandskennzahl auf zwei Nachkommastellen dargestellt.")
    suffix = hashlib.sha256(run_signature.encode()).hexdigest()[:12]
    if level is None:
        level = st.radio("Ebene des Bestandsverlaufs", ["Gesamt", "Organisationseinheit", "OE-Cluster"],
                         horizontal=True, key="org_stock_level")
    metric_column, export_column = st.columns([3, 1])
    with metric_column:
        metric = st.radio("Kennzahl des Bestandsverlaufs", ["Köpfe", "MAK"], horizontal=True, key="org_stock_metric")
    rows = bridge["annual"].loc[bridge["annual"].Ebene == level].copy()
    options = sorted(rows.Organisation.unique())
    orgs = organizations if organizations is not None else st.multiselect("Organisationen im Bestandsverlauf", options, default=options,
                          key=f"org_stock_orgs_{level}_{suffix}")
    if not orgs:
        st.info("Bitte mindestens eine Organisation auswählen.")
        return None
    rows = rows.loc[rows.Organisation.isin(orgs)].copy()
    if years is not None:
        requested = set(years)
        available = set(rows.loc[rows.Art == "Jahresstichtag", "Jahr"])
        if not requested.issubset(available) and summary["metadata"]["base_date"] != summary["metadata"]["target_date"]:
            st.warning("Im gespeicherten Verlauf fehlen Jahresscheiben. Bitte die Simulation neu berechnen.")
            return None
        rows = rows.loc[rows.Jahr.isin(years) | rows.Art.eq("Ausgangsstichtag")].copy()
    chart = rows.drop_duplicates(["Organisation", "Stichtag"], keep="last").copy()
    chart["Bestand-MAK"] = chart["Bestand-MAK"].round(2)
    value = f"Bestand-{metric}"
    chart = chart[["Stichtag", "Organisation", value]]
    table = chart.pivot(index="Organisation", columns="Stichtag", values=value)
    view = {"run_signature": run_signature, "level": level, "metric": metric,
            "organizations": list(orgs), "years": list(years) if years is not None else sorted(rows.Jahr.unique()),
            "chart": chart, "table": table}
    if level != "Gesamt":
        from components.org_simulation_yearly import LABELS
        departure_types = sorted(summary["events"].loc[
            summary["events"]["Ereignisquelle"].eq("departure_events"), "Ereignisart"].unique())
        with st.expander("Abgangsarten im Excel-Export"):
            view["event_types"] = st.multiselect(
                "Abgangsarten für zusätzliche Matrixblätter", departure_types, default=departure_types,
                format_func=lambda value: LABELS.get(value, value), key=f"org_stock_export_types_{suffix}")
            st.caption("Jährliche Originalereignisse mit Vorzeichen; technische Bestandsaufbereitung ist separat im Export dokumentiert.")
    with export_column:
        from utils.org_yearly_view_export import render_yearly_view_export
        render_yearly_view_export(summary, view, stock=True)
    # Categorical dates expose every yearly slice; time axes can hide middle ticks.
    plotted = chart.copy()
    plotted["Stichtag"] = plotted["Stichtag"].dt.strftime("%Y-%m-%d")
    st.bar_chart(plotted, x="Stichtag", y=value, color="Organisation", stack=False)
    st.dataframe(table, use_container_width=True)
    with st.expander("Bestandsüberleitung und Endwertabgleich"):
        st.caption(f"MAK-Toleranz im ungerundeten Vergleich: {bridge['mak_tolerance']} MAK. Rundung nur für die Anzeige; keine Korrekturwerte.")
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.dataframe(bridge["checks"], hide_index=True, use_container_width=True)
        effects = bridge.get("basis_effects")
        if effects is not None and not effects.empty:
            st.caption("Technischer Übergang vom IST zur Prognosebasis: gleiche OE-/Planstellenaufbereitung wie in der Simulation, kein tatsächlicher Personalwechsel.")
            st.dataframe(effects, hide_index=True, use_container_width=True)
        reported = bridge.get("reporting_events")
        if reported is not None and "Original MAK-Veränderung" in reported:
            changed = reported.loc[(reported["MAK-Veränderung"] - reported["Original MAK-Veränderung"]).abs() > bridge["mak_tolerance"]]
            if not changed.empty:
                st.caption("Abweichende Kapazitätswirkung für den Bericht; Originalereignisse bleiben unverändert.")
                st.dataframe(changed, hide_index=True, use_container_width=True)
    return view
