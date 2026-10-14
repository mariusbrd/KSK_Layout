"""Annual event changes from the saved simulation summary only."""
from __future__ import annotations

import hashlib
import altair as alt
import pandas as pd
import streamlit as st

from abgaenge.schemas import REASON_LABELS
from utils.org_simulation_events import METRICS, DEPARTURES

LABELS = {**REASON_LABELS, "Azubi_Exit": "Azubi: Nichtübernahme",
          "Azubi_Conversion_In": "Azubi: Übernahme (Ziel-OE)",
          "Azubi_Conversion_Out": "Azubi: Übernahme (Herkunfts-OE)",
          "Azubi_Hire": "Azubi: Neueinstellung", "Trainee_Hire": "Trainee: Neueinstellung",
          "New_Hire": "Einstellung / Nachbesetzung"}


def yearly_periods(metadata: dict) -> pd.DataFrame:
    start, end = pd.Timestamp(metadata["base_date"]).normalize(), pd.Timestamp(metadata["target_date"]).normalize()
    if pd.isna(start) or pd.isna(end) or end < start:
        raise ValueError("Ungültiger Simulationszeitraum.")
    rows = []
    for year in range(start.year, end.year + 1):
        first, last = max(start, pd.Timestamp(year, 1, 1)), min(end, pd.Timestamp(year, 12, 31))
        rows.append({"Jahr": year, "Von": first, "Bis": last,
                     "Teiljahr": first != pd.Timestamp(year, 1, 1) or last != pd.Timestamp(year, 12, 31)})
    return pd.DataFrame(rows)


def build_yearly_view(summary: dict, *, dimension: str, years: list[int],
                      organizations: list[str], event_types: list[str], metric: str) -> dict:
    if dimension not in ("Organisationseinheit", "OE-Cluster") or metric not in METRICS:
        raise ValueError("Ungültige Auswahl für die Jahresauswertung.")
    source = summary["annual_oe" if dimension == "Organisationseinheit" else "annual_cluster"]
    details = source.loc[source["Jahr"].isin(years) & source[dimension].isin(organizations)
                         & source["Ereignisart"].isin(event_types)].copy()
    grouped = details.groupby(["Jahr", dimension])[metric].sum()
    index = pd.MultiIndex.from_product([sorted(years), organizations], names=["Jahr", dimension])
    chart = grouped.reindex(index, fill_value=0).rename(metric).reset_index()
    table = chart.pivot(index=dimension, columns="Jahr", values=metric) if not chart.empty else pd.DataFrame()
    periods = yearly_periods(summary["metadata"])
    return {"details": details, "chart": chart, "table": table,
            "periods": periods[periods.Jahr.isin(years)].copy(), "metric": metric,
            "dimension": dimension, "run_signature": summary["run_signature"], "event_types": list(event_types)}


def build_yearly_summary_table(view: dict) -> pd.DataFrame:
    table = view["table"].copy()
    if table.empty:
        return table
    details, dimension = view["details"], view["dimension"]
    departures = details.loc[details.Ereignisart.isin(DEPARTURES)]
    heads = departures.groupby(dimension)["Abgänge"].sum()
    mak_loss = (-pd.to_numeric(departures["MAK-Veränderung"], errors="coerce").clip(upper=0))
    mak_loss = mak_loss.groupby(departures[dimension]).sum()
    table["Summe ausgewählte Kennzahl"] = table.sum(axis=1)
    table["Abgehende Köpfe"] = heads.reindex(table.index, fill_value=0)
    table["Abgehende MAK"] = mak_loss.reindex(table.index, fill_value=0)
    return table


def build_stacked_event_chart(view: dict) -> pd.DataFrame:
    source = view["details"]
    metric, dimension = view["metric"], view["dimension"]
    years = view["periods"].Jahr.tolist()
    orgs = view["table"].index.tolist()
    types = view["event_types"]
    grouped = source.groupby(["Jahr", dimension, "Ereignisart"])[metric].sum()
    index = pd.MultiIndex.from_product([years, orgs, types], names=["Jahr", dimension, "Ereignisart"])
    return grouped.reindex(index, fill_value=0).rename(metric).reset_index()


def render_stacked_event_chart(data: pd.DataFrame, *, dimension: str, metric: str) -> None:
    labels = sorted(data.Ereignisart.unique())
    display = data.copy()
    display["Ereignisart"] = display.Ereignisart.map(lambda value: LABELS.get(value, value))
    display_labels = [LABELS.get(value, value) for value in labels]
    chart = (alt.Chart(display).mark_bar()
             .encode(x=alt.X("Jahr:O", title="Jahr"),
                     xOffset=alt.XOffset(f"{dimension}:N"),
                     y=alt.Y(f"{metric}:Q", title=metric, stack="zero"),
                     color=alt.Color("Ereignisart:N", scale=alt.Scale(domain=display_labels),
                                     legend=alt.Legend(title="Ereignisart")),
                     tooltip=["Jahr:O", f"{dimension}:N", "Ereignisart:N", alt.Tooltip(f"{metric}:Q", format=".2f")])
             .properties(height=360))
    st.altair_chart(chart, use_container_width=True)


def build_yearly_headline_metrics(summary: dict, *, dimension: str, organizations: list[str],
                                  end_year: int, metric: str) -> dict | None:
    """Use the certified stock bridge and its reporting-adjusted departure effects."""
    bridge = summary.get("stock_reconciliation", {})
    if bridge.get("status") != "Abgestimmt" or metric not in ("Köpfe", "MAK") or not organizations:
        return None
    annual = bridge["annual"]
    scoped = annual.loc[annual.Ebene.eq(dimension) & annual.Organisation.isin(organizations)]
    start = scoped.loc[scoped.Art.eq("Ausgangsstichtag")].drop_duplicates("Organisation", keep="last")
    end = scoped.loc[scoped.Jahr.eq(end_year) & scoped.Art.ne("Ausgangsstichtag")]
    end = end.drop_duplicates("Organisation", keep="last")
    if set(start.Organisation) != set(organizations) or set(end.Organisation) != set(organizations):
        return None
    initial = float(start[f"Bestand-{metric}"].sum())
    final = float(end[f"Bestand-{metric}"].sum())
    events = bridge.get("reporting_events")
    if events is None or events.empty:
        retirement = quit_loss = other_departures = 0.0
    else:
        events = events.loc[events[dimension].isin(organizations) & events.Jahr.le(end_year)]
        if metric == "Köpfe":
            loss = -pd.to_numeric(events["Köpfe-Veränderung"], errors="coerce").clip(upper=0)
            retirement_types = {"RETIREMENT", "ATZ_END"}
        else:
            loss = -pd.to_numeric(events["MAK-Veränderung"], errors="coerce").clip(upper=0)
            retirement_types = {"RETIREMENT", "ATZ_AR_TO_FR", "ATZ_END"}
        retirement = float(loss.loc[events.Ereignisart.isin(retirement_types)].sum())
        quit_loss = float(loss.loc[events.Ereignisart.eq("QUIT")].sum())
        other_departures = float(loss.loc[events.Ereignisart.isin(DEPARTURES - retirement_types - {"QUIT"})].sum())
    departures = retirement + quit_loss + other_departures
    return {"initial": initial, "final": final, "retirement_atz": retirement,
            "quit": quit_loss, "departures": departures,
            "departure_share": departures / initial if initial > 0 else None,
            "metric": metric, "end_year": end_year}


def render_yearly_headline_metrics(metrics: dict) -> None:
    unit = metrics["metric"]
    def display(value: float) -> str:
        formatted = f"{value:,.0f}" if unit == "Köpfe" else f"{value:,.1f}"
        return formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    first = st.columns(3)
    first[0].metric("Ausgangsbestand", display(metrics["initial"]))
    first[1].metric(f"Bestand Ende {metrics['end_year']}", display(metrics["final"]))
    share = metrics["departure_share"]
    first[2].metric("Abgänge / Ausgangsbestand", "–" if share is None else f"{share:.1%}".replace(".", ","))
    second = st.columns(2)
    second[0].metric(f"Rente inkl. ATZ bis {metrics['end_year']}", display(metrics["retirement_atz"]))
    second[1].metric(f"Kündigungen bis {metrics['end_year']}", display(metrics["quit"]))
    st.caption(f"Alle Werte in {unit} für die gewählten Organisationen. Abgänge kumuliert vom Ausgangsstichtag bis Ende {metrics['end_year']}; die Auswahl einzelner Zwischenjahre und Ereignisarten ändert diese Kacheln nicht. Bei MAK zählt die ATZ-Freistellung als Kapazitätsabgang, bei Köpfen erst der ATZ-Austritt. Die Quote umfasst auch weitere Personalabgänge; technische Bestandseffekte sind ausgeschlossen.")


def render_org_simulation_yearly(summary: dict | None, *, run_signature: str | None) -> dict | None:
    st.subheader("Veränderungen pro Jahr")
    if not summary:
        st.info("Bitte die Simulation auf 'Kompakt plus Simulation' neu berechnen, um die Jahresauswertung bereitzustellen.")
        return None
    if not run_signature or summary.get("run_signature") != run_signature:
        st.warning("Die Jahresauswertung gehört nicht zum angezeigten Simulationslauf. Bitte die Simulation neu berechnen.")
        return None
    try:
        periods = yearly_periods(summary["metadata"])
    except (KeyError, ValueError, TypeError):
        st.warning("Der gespeicherte Simulationszeitraum ist unvollständig. Bitte die Simulation neu berechnen.")
        return None
    if not summary["events"].empty and summary["events"]["OE-Cluster"].eq("Nicht zugeordnet").all():
        st.warning("Die gespeicherte OE-Cluster-Zuordnung passt zu keiner Organisationseinheit der Ereignisse. "
                   "Bitte die passende OE-Cluster-Datei im Dashboard aktivieren und danach die Simulation neu berechnen. "
                   "Die Jahresauswertung übernimmt ausschließlich die Zuordnung des gespeicherten Laufs.")
    st.caption("Die Ereignisauswahl filtert nur Veränderungen. Der verbleibende Bestand umfasst stets den vollständigen Simulationslauf und belegte Aufbereitungseffekte. Beide Ansichten sind unabhängig von den Filtern der Bestandsansicht.")
    level = st.radio("Organisationsebene", ["Organisationseinheiten", "OE-Cluster"],
                     horizontal=True, key="org_yearly_level")
    dimension = "OE-Cluster" if level == "OE-Cluster" else "Organisationseinheit"
    source = summary["annual_cluster" if dimension == "OE-Cluster" else "annual_oe"]
    # Key controls by run: old choices must not hide a freshly computed result.
    suffix = hashlib.sha256(run_signature.encode()).hexdigest()[:12]
    years = st.multiselect("Jahre", periods.Jahr.tolist(), default=periods.Jahr.tolist(),
                           key=f"org_yearly_years_{suffix}")
    options = sorted(source[dimension].unique().tolist())
    retirement_params = summary.get("abgaenge_params")
    retirement_active = isinstance(retirement_params, dict) and retirement_params.get("components", {}).get("retirement", True)
    atz_active = isinstance(retirement_params, dict) and retirement_params.get("components", {}).get("atz", True)
    if retirement_active or atz_active:
        baseline = summary.get("stock_basis", {}).get("baseline_snapshot")
        if baseline is not None and "Organisationseinheit" in baseline:
            if dimension == "Organisationseinheit":
                options = sorted(set(options) | set(baseline.Organisationseinheit.dropna().astype(str)))
            else:
                mapping = summary.get("cluster_mapping", {}).get("oe_map", {})
                options = sorted(set(options) | set(baseline.Organisationseinheit.map(mapping).fillna("Nicht zugeordnet")))
    bridge = summary.get("stock_reconciliation", {})
    documented_atz = bridge.get("documented_effects")
    if isinstance(documented_atz, pd.DataFrame) and dimension in documented_atz:
        options = sorted(set(options) | set(documented_atz[dimension].dropna().astype(str)))
    if bridge.get("status") == "Abgestimmt":
        options = sorted(set(options) | set(bridge["annual"].loc[
            bridge["annual"].Ebene == dimension, "Organisation"].unique().tolist()))
    organizations = st.multiselect(level, options, default=options,
                                   key=f"org_yearly_orgs_{dimension}_{suffix}")
    if bridge.get("status") == "Abgestimmt" and years and organizations:
        stock_metric = st.radio("Kennzahl des Bestandsverlaufs", ["Köpfe", "MAK"],
                                horizontal=True, key="org_stock_metric")
        headline = build_yearly_headline_metrics(summary, dimension=dimension,
                                                 organizations=organizations, end_year=max(years), metric=stock_metric)
        if headline is not None:
            render_yearly_headline_metrics(headline)
    else:
        stock_metric = None
    st.markdown("#### Verbleibender Bestand")
    from components.org_simulation_stock import render_departure_stock
    if years and organizations:
        stock_view = render_departure_stock(summary, run_signature=run_signature, level=dimension,
                                           years=years, organizations=organizations, embedded=True,
                                           metric=stock_metric)
    else:
        st.info("Bitte mindestens ein Jahr und eine Organisation auswählen.")
        stock_view = None
    st.markdown("#### Veränderungen")
    if summary["events"].empty:
        st.info("Im gespeicherten Simulationslauf gibt es keine Ereignisse; die Rentenparameter können dennoch eingeordnet werden.")
    st.caption("Originale Prognoseereignisse; keine Jahresendbestände. Der Bestandsverlauf berücksichtigt zusätzlich technische Aufbereitung und effektive Reporting-Kapazität; diese Effekte sind im Endwertabgleich ausgewiesen.")
    metric_column, export_column = st.columns([3, 1])
    with metric_column:
        metric = st.radio("Kennzahl der Veränderung", list(METRICS[2:]) + list(METRICS[:2]),
                          horizontal=True, key="org_yearly_metric")
    types = sorted(set(source["Ereignisart"].unique().tolist()) |
                   ({"RETIREMENT"} if retirement_active else set()) |
                   ({"ATZ_AR_TO_FR"} if atz_active else set()))
    event_types = st.multiselect("Ereignisarten", types, default=types,
                                 format_func=lambda value: LABELS.get(value, value),
                                 key=f"org_yearly_types_{suffix}")
    if not years or not organizations or not event_types:
        st.info("Bitte mindestens ein Jahr, eine Organisation und eine Ereignisart auswählen.")
        if years and organizations:
            from components.org_atz_evaluation import render_atz_baseline_effects
            render_atz_baseline_effects(summary, dimension=dimension, organizations=organizations)
        return {"stock_view": stock_view} if stock_view is not None else None
    view = build_yearly_view(summary, dimension=dimension, years=years,
                             organizations=organizations, event_types=event_types, metric=metric)
    view["summary_table"] = build_yearly_summary_table(view)
    if len(set(event_types) & DEPARTURES) > 1:
        view["stacked_chart"] = build_stacked_event_chart(view)
    if "QUIT" in event_types:
        from components.org_quit_evaluation import build_quit_evaluation, build_quit_chart_data
        view["quit_evaluation"] = build_quit_evaluation(
            summary, dimension=dimension, years=years, organizations=organizations)
        if not view["quit_evaluation"].empty:
            view["quit_chart"] = build_quit_chart_data(view["quit_evaluation"], dimension)
    if "RETIREMENT" in event_types:
        from components.org_retirement_evaluation import build_retirement_evaluation, build_retirement_chart_data
        view["retirement_evaluation"] = build_retirement_evaluation(
            summary, dimension=dimension, years=years, organizations=organizations)
        if view["retirement_evaluation"] is not None:
            view["retirement_chart"] = build_retirement_chart_data(
                view["retirement_evaluation"], dimension=dimension, years=years, organizations=organizations)
    if set(event_types) & {"ATZ_AR_TO_FR", "ATZ_END"}:
        from components.org_atz_evaluation import build_atz_evaluation, build_atz_chart_data
        view["atz_evaluation"] = build_atz_evaluation(
            summary, dimension=dimension, years=years, organizations=organizations)
        if view["atz_evaluation"] is not None:
            view["atz_chart"] = build_atz_chart_data(view["atz_evaluation"], dimension=dimension)
    with export_column:
        from utils.org_yearly_view_export import render_yearly_view_export
        render_yearly_view_export(summary, view)
    for row in view["periods"].itertuples(index=False):
        if row.Teiljahr:
            st.caption(f"{row.Jahr}: Teiljahr {row.Von:%d.%m.%Y} bis {row.Bis:%d.%m.%Y}.")
    if "stacked_chart" in view:
        render_stacked_event_chart(view["stacked_chart"], dimension=dimension, metric=metric)
    else:
        st.bar_chart(view["chart"], x="Jahr", y=metric, color=dimension, stack=False)
    st.caption("Die Summen beziehen sich auf die ausgewählten Jahre und Ereignisarten. Abgehende MAK zeigen den positiven Kapazitätsverlust aus ausgewählten Personalabgängen.")
    st.dataframe(view["summary_table"], use_container_width=True)
    from components.org_quit_evaluation import render_quit_evaluation
    render_quit_evaluation(summary, dimension=dimension, years=years,
                           organizations=organizations, event_types=event_types)
    if "RETIREMENT" in event_types:
        from components.org_retirement_evaluation import render_retirement_evaluation
        render_retirement_evaluation(summary, dimension=dimension, years=years,
                                     organizations=organizations, event_types=event_types)
    if set(event_types) & {"ATZ_AR_TO_FR", "ATZ_END"}:
        from components.org_atz_evaluation import render_atz_evaluation
        render_atz_evaluation(summary, dimension=dimension, years=years,
                              organizations=organizations, event_types=event_types)
    else:
        from components.org_atz_evaluation import render_atz_baseline_effects
        render_atz_baseline_effects(summary, dimension=dimension, organizations=organizations)
    with st.expander("Aufschlüsselung nach Ereignisart"):
        details = view["details"].copy()
        details["Ereignisart"] = details["Ereignisart"].map(lambda value: LABELS.get(value, value))
        st.dataframe(details, hide_index=True, use_container_width=True)
    view["stock_view"] = stock_view
    return view
