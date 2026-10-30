"""Read-only yearly job-family views of one saved CompactPlus run.

This module never invokes a forecast and never mutates the OE event summary.
"""
from __future__ import annotations

import hashlib
from io import BytesIO

import pandas as pd
import streamlit as st

from components.ui_compat import lazy_excel_download_button_compat
from components.org_simulation_yearly import LABELS, yearly_periods, render_stacked_event_chart
from dataloader.jobfamily_service import normalize_jobfamily_series
from utils.org_simulation_events import DEPARTURES, METRICS
from utils.org_simulation_reconciliation import (
    raw_stock_mak, reporting_departure_events, reporting_projection_base,
)
from abgaenge.schemas import normalize_persnr
from dataloader.kpi_engine import compute_headcount
from utils.jobfamily_yearly_stock_export import build_jobfamily_stock_export_bytes


DIMENSIONS = ("Jobfamily", "JF-Cluster")
UNASSIGNED = "UNMAPPED"
YEARLY_VIEW_CACHE_VERSION = 2


def _dimension_values(frame: pd.DataFrame, dimension: str) -> pd.Series:
    if dimension == "Jobfamily":
        return normalize_jobfamily_series(frame.get("Jobfamily", pd.Series(index=frame.index, dtype=object)))
    values = frame.get("JF-Cluster", pd.Series(index=frame.index, dtype=object))
    return values.fillna(UNASSIGNED).astype(str).str.strip().replace("", UNASSIGNED)


def _baseline_assignments(baseline: pd.DataFrame, dimension: str) -> dict[str, str]:
    occupied = baseline.loc[~baseline.get("Is_Vacant", pd.Series(False, index=baseline.index)).fillna(False).astype(bool)].copy()
    occupied["_person"] = normalize_persnr(occupied["PersNr"])
    occupied["_group"] = _dimension_values(occupied, dimension)
    groups = occupied.dropna(subset=["_person"]).groupby("_person")["_group"].agg(lambda x: set(x))
    return {person: next(iter(values)) for person, values in groups.items() if len(values) == 1}


def _allocate_original_events_to_groups(
    events: pd.DataFrame, baseline: pd.DataFrame, dimension: str,
) -> pd.DataFrame:
    """Split a person's capacity event across their original position groups.

    Group headcounts are intentionally not additive when one person occupies
    positions in more than one group. The company event remains unchanged.
    """
    if events.empty or "persnr" not in events:
        return events.copy()
    occupied = baseline.loc[
        ~baseline.get("Is_Vacant", pd.Series(False, index=baseline.index)).fillna(False).astype(bool)
    ].copy()
    occupied["_person"] = normalize_persnr(occupied["PersNr"])
    occupied["_group"] = _dimension_values(occupied, dimension)
    capacity_col = next((c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in occupied), None)
    occupied["_capacity"] = pd.to_numeric(occupied[capacity_col], errors="coerce").fillna(0.0) if capacity_col else 0.0
    weight_col = next((c for c in ("Allocation_Weight", "Soll_FTE") if c in occupied), None)
    occupied["_weight"] = pd.to_numeric(occupied[weight_col], errors="coerce").fillna(0.0) if weight_col else 1.0
    grouped = occupied.groupby(["_person", "_group"], dropna=False).agg(
        capacity=("_capacity", "sum"), weight=("_weight", "sum"), rows=("_person", "size")
    )
    members: dict[str, list[tuple[str, float]]] = {}
    for person, part in grouped.groupby(level=0):
        if len(part) <= 1:
            continue
        weights = part["capacity"]
        if float(weights.sum()) <= 1e-12:
            weights = part["weight"]
        if float(weights.sum()) <= 1e-12:
            weights = part["rows"].astype(float)
        members[person] = [(str(group), float(value / weights.sum()))
                           for (_, group), value in weights.items()]
    if not members:
        return events.copy()
    output = []
    persons = normalize_persnr(events["persnr"])
    for index, event in events.iterrows():
        person = persons.loc[index]
        origin = event.get("Ereignisquelle") == "departure_events" or event.get("Ereignisart") == "Azubi_Conversion_Out"
        groups = members.get(person) if origin else None
        if not groups:
            output.append(event.copy())
            continue
        for group, ratio in groups:
            row = event.copy()
            row[dimension] = group
            row["MAK-Veränderung"] = float(event["MAK-Veränderung"]) * ratio
            # One employee disappears from each group they occupied. These
            # group headcounts must not be summed as company headcounts.
            output.append(row)
    return pd.DataFrame(output, columns=events.columns).reset_index(drop=True)


def build_jobfamily_yearly_view(context: dict, summary: dict, *, dimension: str) -> dict:
    """Aggregate saved events; certify departure-only stock slices separately."""
    if dimension not in DIMENSIONS:
        raise ValueError("Ungültige Jobgruppenebene.")
    signature = context.get("run_signature")
    if not signature or summary.get("run_signature") != signature:
        raise ValueError("Ereignisse und Simulation gehören zu unterschiedlichen Läufen.")
    baseline, final = context.get("baseline_snapshot"), context.get("final_snapshot")
    if baseline is None or final is None:
        raise ValueError("Gespeicherter Ausgangs- oder Zielbestand fehlt.")
    assignments = _baseline_assignments(baseline, dimension)
    final_assignments = _baseline_assignments(final, dimension)
    events = summary["events"].copy(deep=True)
    if not events.empty:
        source = events.get(dimension, pd.Series(index=events.index, dtype=object))
        source = source.astype("string").str.strip()
        missing = source.isna() | source.eq("") | source.isin(("nan", "None", "UNMAPPED"))
        persons = normalize_persnr(events.get("persnr", pd.Series(index=events.index, dtype=object)))
        fallback = persons.map(assignments).astype("string")
        # Departures and conversion-out use the original assignment. New
        # accessions and conversion-in use the saved final person assignment.
        departure = events["Ereignisquelle"].eq("departure_events")
        conversion_out = events["Ereignisart"].eq("Azubi_Conversion_Out")
        origin = departure | conversion_out
        # The forecast employee row may carry a pre-cluster Jobfamily while
        # both saved stock snapshots carry the run's mapped Jobfamily. The
        # saved person assignment is authoritative for original employees.
        source = source.where(~(origin & fallback.notna()), fallback)
        source = source.where(~(missing & ~origin), persons.map(final_assignments).astype("string"))
        if dimension == "JF-Cluster" and "Jobfamily" in events:
            mapping_rows = pd.concat([baseline[["Jobfamily", "JF-Cluster"]], final[["Jobfamily", "JF-Cluster"]]], ignore_index=True)
            mapping_rows = mapping_rows.dropna().drop_duplicates()
            unique_names = mapping_rows.groupby("Jobfamily")["JF-Cluster"].agg(lambda x: set(x))
            name_map = {name: next(iter(values)) for name, values in unique_names.items() if len(values) == 1}
            missing = source.isna() | source.eq("") | source.eq(UNASSIGNED)
            source = source.where(~missing, events["Jobfamily"].map(name_map).astype("string"))
        events[dimension] = source.fillna(UNASSIGNED).replace("", UNASSIGNED)
        person_events = events.copy(deep=True)
        events = _allocate_original_events_to_groups(person_events, baseline, dimension)
        events["Zuordnung_geklärt"] = events[dimension].ne(UNASSIGNED)
        annual = events.groupby(["Jahr", dimension, "Kategorie", "Ereignisart"], dropna=False)[list(METRICS)].sum().reset_index()
    else:
        events[dimension] = pd.Series(dtype=str)
        annual = pd.DataFrame(columns=["Jahr", dimension, "Kategorie", "Ereignisart", *METRICS])

    stock = pd.DataFrame()
    stock_checks = pd.DataFrame()
    reported = pd.DataFrame()
    stock_reason = "Bestandsverlauf nur bei ausdrücklich deaktivierten Zugängen verfügbar."
    oe_bridge = summary.get("stock_reconciliation", {})
    params = context.get("zugaenge_params")
    no_entries = isinstance(params, dict) and all(params.get(k, {}).get("active") is False for k in ("azubi", "trainee", "new_hires"))
    if no_entries and oe_bridge.get("status") == "Nicht verfügbar":
        stock_reason = "Bestandsverlauf für diesen Lauf nicht verfügbar: " + str(oe_bridge.get("reason") or "OE-Bestandsüberleitung nicht verfügbar")
    elif no_entries and (events.empty or events["Ereignisquelle"].eq("departure_events").all()):
        if not events.empty and events[dimension].eq(UNASSIGNED).any():
            stock_reason = "Ereignisse ohne eindeutige Jobgruppenzuordnung verhindern den Bestandsabgleich."
        else:
            projection = reporting_projection_base(context, baseline) if pd.Timestamp(summary["metadata"]["target_date"]) > pd.Timestamp(summary["metadata"]["base_date"]) else baseline.copy(deep=True)
            reported = reporting_departure_events(projection, person_events if not events.empty else events, context)
            reported = _allocate_original_events_to_groups(reported, baseline, dimension)
            stock, stock_reason, stock_checks = _build_certified_stock(baseline, projection, final, reported, dimension, summary["metadata"])
    return {"run_signature": signature, "dimension": dimension, "events": events,
            "annual": annual, "stock": stock, "stock_reason": stock_reason, "stock_checks": stock_checks,
            "reporting_events": reported,
            "abgaenge_params": summary.get("abgaenge_params", {}),
            "oe_bridge_status": oe_bridge.get("status"), "oe_bridge_reason": oe_bridge.get("reason", ""),
            "periods": yearly_periods(summary["metadata"])}


def _group_totals(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    work = frame.copy(deep=True)
    work[dimension] = _dimension_values(work, dimension)
    rows = []
    for name, group in work.groupby(dimension):
        rows.append({dimension: name, "Köpfe": float(compute_headcount(group)), "MAK": raw_stock_mak(group)})
    return pd.DataFrame(rows).set_index(dimension) if rows else pd.DataFrame(columns=["Köpfe", "MAK"])


def _build_certified_stock(baseline, projection, final, events, dimension, metadata):
    initial = _group_totals(baseline, dimension)
    projected = _group_totals(projection, dimension)
    target = _group_totals(final, dimension)
    names = sorted(set(initial.index) | set(projected.index) | set(target.index) | set(events.get(dimension, pd.Series(dtype=str))))
    start, end = pd.Timestamp(metadata["base_date"]).normalize(), pd.Timestamp(metadata["target_date"]).normalize()
    rows = []
    checks = []
    for name in names:
        for metric in ("Köpfe", "MAK"):
            tolerance = 0.0 if metric == "Köpfe" else 1e-6
            opening = float(initial[metric].get(name, 0.0))
            technical = float(projected[metric].get(name, 0.0)) - opening
            scoped = events.loc[events[dimension].eq(name)] if not events.empty else events
            delta = scoped.groupby("Jahr")[f"{metric}-Veränderung"].sum() if not scoped.empty else pd.Series(dtype=float)
            cumulative = technical if end > start else 0.0
            rows.append({dimension: name, "Stichtag": start, "Art": "Ausgangsstichtag", "Kennzahl": metric, "Bestand": opening})
            if end > start:
                for year in range(start.year, end.year + 1):
                    cumulative += float(delta.get(year, 0.0))
                    year_end = min(pd.Timestamp(year, 12, 31), end)
                    if year_end > start:
                        rows.append({dimension: name, "Stichtag": year_end,
                                     "Art": "Jahresstichtag", "Kennzahl": metric, "Bestand": opening + cumulative})
                    if opening + cumulative < -tolerance:
                        checks.append({dimension: name, "Kennzahl": metric, "Prüfung": "Zwischenbestand",
                                       "Berechnet": opening + cumulative, "Zielbestand": pd.NA,
                                       "Differenz": pd.NA, "Abgestimmt": False})
            actual = float(target[metric].get(name, 0.0))
            calculated = opening + cumulative
            checks.append({dimension: name, "Kennzahl": metric, "Prüfung": "Endwert",
                           "Berechnet": calculated, "Zielbestand": actual, "Differenz": calculated - actual,
                           "Abgestimmt": abs(calculated - actual) <= tolerance})
    check_frame = pd.DataFrame(checks)
    failed = check_frame.loc[~check_frame["Abgestimmt"]] if not check_frame.empty else check_frame
    if not failed.empty:
        return pd.DataFrame(), f"Jobgruppen-Bestandsüberleitung nicht abgestimmt ({len(failed)} Prüfungen abweichend).", check_frame
    return pd.DataFrame(rows), "", check_frame


def _cached_yearly_view(context: dict, summary: dict, dimension: str) -> dict:
    """Keep expensive reporting projection stable across Streamlit filter reruns."""
    key = (YEARLY_VIEW_CACHE_VERSION, context["run_signature"], id(context), dimension)
    cache = st.session_state.get("jf_yearly_view_cache")
    if not isinstance(cache, dict) or key not in cache:
        view = build_jobfamily_yearly_view(context, summary, dimension=dimension)
        cache = {item: value for item, value in cache.items() if item[:3] == key[:3]} if isinstance(cache, dict) else {}
        cache[key] = view
        st.session_state["jf_yearly_view_cache"] = cache
        return view
    return cache[key]


def _render_departure_evaluations(view: dict, *, dimension: str, years: list[int],
                                  groups: list[str], event_types: list[str]) -> None:
    """Use the same section sequence as the OE view for saved JF events."""
    events = view["events"]
    scoped = events.loc[events.Jahr.isin(years) & events[dimension].isin(groups)]
    if "QUIT" in event_types:
        st.subheader("Einordnung der Kündigungen")
        quits = scoped.loc[scoped.Ereignisart.eq("QUIT")]
        st.metric("Kündigungen in der Auswahl", int(quits["Abgänge"].sum()))
        st.caption("Beobachtete Kündigungen des gespeicherten Laufs je Jobgruppe und Jahr. Die gespeicherte Wahrscheinlichkeitsprüfung liegt nur nach OE vor; ein JF-Erwartungswert wäre daraus nicht belastbar ableitbar.")
        quit_table = quits.groupby([dimension, "Jahr"], as_index=False)[["Abgänge", "MAK-Veränderung"]].sum()
        if not quit_table.empty:
            st.bar_chart(quit_table, x="Jahr", y="Abgänge", color=dimension, stack=False)
        st.dataframe(quit_table, hide_index=True, use_container_width=True)
    if "RETIREMENT" in event_types:
        st.subheader("Einordnung der Renteneintritte")
        retirement = scoped.loc[scoped.Ereignisart.eq("RETIREMENT")].copy()
        st.metric("Renteneintritte in der Auswahl", int(retirement["Abgänge"].sum()))
        if not retirement.empty:
            age = pd.to_numeric(retirement.get("age", pd.Series(index=retirement.index, dtype=float)), errors="coerce")
            regular_age = float(((view.get("abgaenge_params") or {}).get("retirement") or {})
                                .get("retirement_age", 65))
            retirement["Altersgruppe"] = "Altersangabe fehlt"
            retirement.loc[age.lt(regular_age), "Altersgruppe"] = "Frührente"
            retirement.loc[age.ge(regular_age), "Altersgruppe"] = "Reguläre Rente"
            retirement_table = retirement.groupby([dimension, "Jahr", "Altersgruppe"], as_index=False)[
                ["Abgänge", "MAK-Veränderung"]].sum()
            st.bar_chart(retirement_table, x="Jahr", y="Abgänge", color="Altersgruppe", stack=False)
            st.dataframe(retirement_table, hide_index=True, use_container_width=True)
        else:
            st.info("Für diese Auswahl liegen keine Renteneintritte vor.")
    if set(event_types) & {"ATZ_AR_TO_FR", "ATZ_END"}:
        st.subheader("Einordnung der Altersteilzeit")
        atz = scoped.loc[scoped.Ereignisart.isin({"ATZ_AR_TO_FR", "ATZ_END"})].copy()
        cols = st.columns(2)
        cols[0].metric("Freistellungsbeginne", int(atz.Ereignisart.eq("ATZ_AR_TO_FR").sum()))
        cols[1].metric("ATZ-Vertragsenden", int(atz.Ereignisart.eq("ATZ_END").sum()))
        st.caption("Der Freistellungsbeginn verändert die MAK ohne Kopf-Abgang. Der Kopf-Abgang entsteht erst am Vertragsende.")
        if not atz.empty:
            atz["Ereignisart"] = atz.Ereignisart.map(lambda value: LABELS.get(value, value))
            atz_table = atz.groupby([dimension, "Jahr", "Ereignisart"], as_index=False)[
                ["Köpfe-Veränderung", "MAK-Veränderung"]].sum()
            st.bar_chart(atz_table, x="Jahr", y="MAK-Veränderung", color="Ereignisart", stack=False)
            st.dataframe(atz_table, hide_index=True, use_container_width=True)
        else:
            st.info("Für diese Auswahl liegen keine ATZ-Ereignisse vor.")


def _export_yearly_view(selected: pd.DataFrame, stock: pd.DataFrame, *, dimension: str,
                        groups: list[str], years: list[int], run_signature: str,
                        stock_reason: str) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        selected.to_excel(writer, sheet_name="Ereignisse_Jahre", index=False)
        if not stock.empty:
            stock.loc[stock[dimension].isin(groups) & stock.Stichtag.dt.year.isin(years)].to_excel(
                writer, sheet_name="Bestand_Jahre", index=False)
        pd.DataFrame([{"Laufkennung": run_signature, "Ebene": dimension,
                       "Bestandsabgleich": "Abgestimmt" if not stock.empty else stock_reason}]).to_excel(
                           writer, sheet_name="Kontext", index=False)
    return output.getvalue()


@st.fragment
def render_jobfamily_simulation_yearly(context: dict | None, summary: dict | None, *, run_signature: str | None) -> None:
    st.subheader("Veränderungen pro Jahr")
    if not context or not summary or not run_signature or context.get("run_signature") != run_signature:
        st.info("Bitte die Simulation auf 'Kompakt plus Simulation' neu berechnen.")
        return
    dimension = st.radio("Jobgruppenebene", DIMENSIONS, horizontal=True, key="jf_yearly_dimension")
    try:
        view = _cached_yearly_view(context, summary, dimension)
    except (KeyError, ValueError, TypeError) as exc:
        st.warning(f"Jahresauswertung nicht verfügbar: {exc}")
        return
    periods = view["periods"]
    years = st.multiselect("Jahre", periods.Jahr.tolist(), default=periods.Jahr.tolist(), key="jf_yearly_years")
    options = sorted(set(view["annual"].get(dimension, pd.Series(dtype=str)).dropna()) |
                     set(_dimension_values(context["baseline_snapshot"], dimension)) |
                     set(_dimension_values(context["final_snapshot"], dimension)))
    if options == ["Sonstiges"]:
        st.warning("Alle Daten dieses Laufs sind 'Sonstiges' zugeordnet. Bitte die aktive Jobgruppen-Clusterdatei prüfen; ein Vergleich einzelner Jobgruppen ist so nicht aussagekräftig.")
    groups = st.multiselect("Jobgruppen", options, default=options, key=f"jf_yearly_groups_{dimension}")
    st.caption("Die Ereignisauswahl filtert nur Veränderungen. Der verbleibende Bestand umfasst stets den vollständigen Simulationslauf und ist unabhängig von den Filtern der Bestandsansicht.")
    st.caption("Personen mit Planstellen in mehreren Jobgruppen zaehlen in jeder betroffenen Jobgruppe als Kopf. Gruppenkopfzahlen sind daher nicht zur Unternehmenssumme addierbar; MAK werden nach Planstellenanteilen verteilt.")
    st.markdown("#### Verbleibender Bestand")
    stock = view["stock"]
    if stock.empty:
        st.info(view["stock_reason"])
        if not view["stock_checks"].empty:
            with st.expander("Abweichungen im Jobgruppen-Endwertabgleich", expanded=True):
                st.dataframe(view["stock_checks"].loc[lambda frame: ~frame["Abgestimmt"]],
                             hide_index=True, use_container_width=True)
    else:
        metric_column, export_column = st.columns([3, 1])
        with metric_column:
            stock_metric = st.radio("Kennzahl des Bestandsverlaufs", ["Köpfe", "MAK"], horizontal=True, key="jf_yearly_stock_metric")
        visible = stock.loc[stock[dimension].isin(groups) & stock.Kennzahl.eq(stock_metric) &
                            (stock.Stichtag.dt.year.isin(years) | stock.Art.eq("Ausgangsstichtag"))].copy()
        if years and not visible.empty:
            departure_types = sorted(view["annual"].loc[
                view["annual"].Ereignisart.isin(DEPARTURES), "Ereignisart"].unique())
            suffix = hashlib.sha256(run_signature.encode()).hexdigest()[:12]
            with st.expander("Abgangsarten im Excel-Export"):
                export_types = st.multiselect(
                    "Abgangsarten für zusätzliche Matrixblätter", departure_types,
                    default=departure_types, format_func=lambda value: LABELS.get(value, value),
                    key=f"jf_stock_export_types_{dimension}_{suffix}")
                st.caption("Originale Jahresereignisse mit Vorzeichen; technische Bestandsaufbereitung ist separat dokumentiert.")
            with export_column:
                lazy_excel_download_button_compat(
                    label="Excel exportieren",
                    data_builder=lambda: build_jobfamily_stock_export_bytes(
                        view, dimension=dimension, groups=groups, years=years,
                        metric=stock_metric, event_types=export_types),
                    file_name="Jobgruppen_Bestandsverlauf.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="jf_yearly_selection_export_stock",
                    fingerprint=(run_signature, id(view), dimension, tuple(groups),
                                 tuple(years), stock_metric, tuple(export_types)),
                    help="Aktuelle Bestandsauswahl mit Folienvorlagen für MAK und Köpfe, Jahreswerten und Endwertabgleich.",
                )
            opening = visible.loc[visible.Art.eq("Ausgangsstichtag")].Bestand.sum()
            closing_rows = visible.loc[visible.Art.eq("Jahresstichtag")]
            closing = closing_rows.loc[closing_rows.Stichtag.eq(closing_rows.Stichtag.max()), "Bestand"].sum() if not closing_rows.empty else opening
            reported = view["reporting_events"]
            reported = reported.loc[reported[dimension].isin(groups) & reported.Jahr.le(max(years))] if years and not reported.empty else reported.iloc[:0]
            loss_column = f"{stock_metric}-Veränderung"
            retirement_types = {"RETIREMENT", "ATZ_END"} if stock_metric == "Köpfe" else {"RETIREMENT", "ATZ_AR_TO_FR", "ATZ_END"}
            loss = -pd.to_numeric(reported[loss_column], errors="coerce").clip(upper=0) if not reported.empty else pd.Series(dtype=float)
            retirement_loss = float(loss.loc[reported.Ereignisart.isin(retirement_types)].sum()) if not reported.empty else 0.0
            quit_loss = float(loss.loc[reported.Ereignisart.eq("QUIT")].sum()) if not reported.empty else 0.0
            departure_loss = float(loss.loc[reported.Ereignisart.isin(DEPARTURES | {"ATZ_AR_TO_FR"})].sum()) if not reported.empty else 0.0
            first = st.columns(3)
            first[0].metric("Ausgangsbestand", f"{opening:.1f}" if stock_metric == "MAK" else f"{opening:.0f}")
            first[1].metric(f"Bestand Ende {max(years)}", f"{closing:.1f}" if stock_metric == "MAK" else f"{closing:.0f}")
            first[2].metric("Abgänge / Ausgangsbestand", "–" if opening <= 0 else f"{departure_loss / opening:.1%}")
            second = st.columns(2)
            second[0].metric(f"Rente inkl. ATZ bis {max(years)}", f"{retirement_loss:.1f}" if stock_metric == "MAK" else f"{retirement_loss:.0f}")
            second[1].metric(f"Kündigungen bis {max(years)}", f"{quit_loss:.1f}" if stock_metric == "MAK" else f"{quit_loss:.0f}")
            if stock_metric == "Köpfe" and set(groups) == set(options):
                unique_opening = compute_headcount(context["baseline_snapshot"])
                if opening != unique_opening:
                    st.caption(f"Die Summe der Jobgruppen-Ausgangsbestände ({opening:.0f}) zählt Personen mit mehreren Jobgruppen mehrfach. Im Gesamtbestand sind es {unique_opening:.0f} eindeutige Personen.")
            st.success("Jahresverlauf mit dem finalen Jobgruppenbestand abgestimmt.")
            plotted = visible.drop_duplicates([dimension, "Stichtag"], keep="last").copy()
            plotted["Stichtag"] = plotted["Stichtag"].dt.strftime("%Y-%m-%d")
            st.bar_chart(plotted, x="Stichtag", y="Bestand", color=dimension, stack=False)
            st.dataframe(visible, hide_index=True, use_container_width=True)
    st.markdown("#### Veränderungen")
    metric = st.radio("Kennzahl der Veränderung", list(METRICS[2:]) + list(METRICS[:2]), horizontal=True, key="jf_yearly_metric")
    types = sorted(view["annual"].Ereignisart.dropna().unique())
    selected_types = st.multiselect("Ereignisarten", types, default=types,
                                    format_func=lambda x: LABELS.get(x, x), key="jf_yearly_types")
    selected = view["annual"].loc[view["annual"].Jahr.isin(years) &
                                  view["annual"][dimension].isin(groups) &
                                  view["annual"].Ereignisart.isin(selected_types)].copy()
    if not selected.empty:
        chart = selected.groupby(["Jahr", dimension], as_index=False)[metric].sum()
        if len(set(selected_types) & DEPARTURES) > 1:
            stacked = selected.groupby(["Jahr", dimension, "Ereignisart"], as_index=False)[metric].sum()
            render_stacked_event_chart(stacked, dimension=dimension, metric=metric)
        else:
            st.bar_chart(chart, x="Jahr", y=metric, color=dimension, stack=False)
        table = chart.pivot(index=dimension, columns="Jahr", values=metric).fillna(0)
        table["Summe ausgewählte Kennzahl"] = table.sum(axis=1)
        departures = selected.loc[selected.Ereignisart.isin(DEPARTURES)]
        table["Abgehende Köpfe"] = departures.groupby(dimension)["Abgänge"].sum().reindex(table.index, fill_value=0)
        mak_loss = -pd.to_numeric(departures["MAK-Veränderung"], errors="coerce").clip(upper=0)
        table["Abgehende MAK"] = mak_loss.groupby(departures[dimension]).sum().reindex(table.index, fill_value=0)
        st.dataframe(table, use_container_width=True)
    else:
        st.info("Für diese Auswahl liegen keine Ereignisse vor.")
    for period in periods.loc[periods.Jahr.isin(years)].itertuples(index=False):
        if period.Teiljahr:
            st.caption(f"{period.Jahr}: Teiljahr {period.Von:%d.%m.%Y} bis {period.Bis:%d.%m.%Y}.")
    _render_departure_evaluations(view, dimension=dimension, years=years,
                                  groups=groups, event_types=selected_types)
    with st.expander("Aufschlüsselung nach Ereignisart"):
        detail = selected.copy()
        detail["Ereignisart"] = detail["Ereignisart"].map(lambda value: LABELS.get(value, value))
        st.dataframe(detail, hide_index=True, use_container_width=True)
    unresolved = view["events"].loc[view["events"].get(dimension, pd.Series(dtype=str)).eq(UNASSIGNED)]
    if not unresolved.empty:
        st.warning(f"{len(unresolved)} Ereignisse haben keine eindeutige {dimension}-Zuordnung; sie stehen unter UNMAPPED.")
    st.caption("Ereignisse des gespeicherten Laufs. Die Auswahl hier ist unabhängig von den Sidebar-Filtern der Bestandsansicht. Interne Jobgruppenwechsel sind keine externen Zugänge.")
    if years and groups:
        # Streamlit evaluates widgets on every rerun; delay workbook construction
        # until the user explicitly requests an export.
        export_key = (run_signature, dimension, tuple(years), tuple(groups), tuple(selected_types))
        if st.button("Excel-Export vorbereiten", key="jf_yearly_prepare_export"):
            data = _export_yearly_view(
                selected, stock, dimension=dimension, groups=groups, years=years,
                run_signature=run_signature, stock_reason=view["stock_reason"])
            st.session_state["jf_yearly_prepared_export"] = (export_key, data)
        prepared_export = st.session_state.get("jf_yearly_prepared_export")
        if prepared_export and prepared_export[0] == export_key:
            st.download_button("Jahresscheiben als Excel exportieren", prepared_export[1],
                           file_name="Jobgruppen_Jahresscheiben.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="jf_yearly_export")
