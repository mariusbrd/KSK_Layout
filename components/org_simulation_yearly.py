"""Annual event changes from the saved simulation summary only."""
from __future__ import annotations

import hashlib
import pandas as pd
import streamlit as st

from abgaenge.schemas import REASON_LABELS
from utils.org_simulation_events import METRICS

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
    st.caption("Gemeinsame Jahres- und Organisationsauswahl für Ereignisveränderungen und den abgestimmten Abgänge-only-Bestand. Unabhängig von den Filtern der Bestandsansicht.")
    level = st.radio("Organisationsebene", ["Organisationseinheiten", "OE-Cluster"],
                     horizontal=True, key="org_yearly_level")
    dimension = "OE-Cluster" if level == "OE-Cluster" else "Organisationseinheit"
    mode = st.radio("Darstellung", ["Veränderungen", "Verbleibender Bestand"], horizontal=True, key="org_yearly_mode")
    source = summary["annual_cluster" if dimension == "OE-Cluster" else "annual_oe"]
    # Key controls by run: old choices must not hide a freshly computed result.
    suffix = hashlib.sha256(run_signature.encode()).hexdigest()[:12]
    years = st.multiselect("Jahre", periods.Jahr.tolist(), default=periods.Jahr.tolist(),
                           key=f"org_yearly_years_{suffix}")
    options = sorted(source[dimension].unique().tolist())
    bridge = summary.get("stock_reconciliation", {})
    if mode == "Verbleibender Bestand" and bridge.get("status") == "Abgestimmt":
        options = sorted(bridge["annual"].loc[bridge["annual"].Ebene == dimension, "Organisation"].unique().tolist())
    organizations = st.multiselect(level, options, default=options,
                                   key=f"org_yearly_orgs_{dimension}_{mode}_{suffix}")
    if mode == "Verbleibender Bestand":
        from components.org_simulation_stock import render_departure_stock
        if bridge.get("status") == "Abgestimmt" and (not years or not organizations):
            st.info("Bitte mindestens ein Jahr und eine Organisation auswählen.")
            return None
        stock_view = render_departure_stock(summary, run_signature=run_signature, level=dimension,
                                           years=years, organizations=organizations, embedded=True)
        return {"stock_view": stock_view} if stock_view is not None else None
    if summary["events"].empty:
        st.info("Im gespeicherten Simulationslauf gibt es keine Zu-, Abgangs- oder Kapazitätsereignisse.")
        return None
    st.caption("Originale Prognoseereignisse; keine Jahresendbestände. Der Bestandsverlauf berücksichtigt zusätzlich technische Aufbereitung und effektive Reporting-Kapazität; diese Effekte sind im Endwertabgleich ausgewiesen.")
    metric_column, export_column = st.columns([3, 1])
    with metric_column:
        metric = st.radio("Kennzahl der Veränderung", list(METRICS[2:]) + list(METRICS[:2]),
                          horizontal=True, key="org_yearly_metric")
    types = sorted(source["Ereignisart"].unique().tolist())
    event_types = st.multiselect("Ereignisarten", types, default=types,
                                 format_func=lambda value: LABELS.get(value, value),
                                 key=f"org_yearly_types_{suffix}")
    if not years or not organizations or not event_types:
        st.info("Bitte mindestens ein Jahr, eine Organisation und eine Ereignisart auswählen.")
        return None
    view = build_yearly_view(summary, dimension=dimension, years=years,
                             organizations=organizations, event_types=event_types, metric=metric)
    with export_column:
        from utils.org_yearly_view_export import render_yearly_view_export
        render_yearly_view_export(summary, view)
    for row in view["periods"].itertuples(index=False):
        if row.Teiljahr:
            st.caption(f"{row.Jahr}: Teiljahr {row.Von:%d.%m.%Y} bis {row.Bis:%d.%m.%Y}.")
    st.bar_chart(view["chart"], x="Jahr", y=metric, color=dimension, stack=False)
    st.dataframe(view["table"], use_container_width=True)
    with st.expander("Aufschlüsselung nach Ereignisart"):
        details = view["details"].copy()
        details["Ereignisart"] = details["Ereignisart"].map(lambda value: LABELS.get(value, value))
        st.dataframe(details, hide_index=True, use_container_width=True)
    return view
