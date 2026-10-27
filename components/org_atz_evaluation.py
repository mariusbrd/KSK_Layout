"""ATZ controls derived from the saved forecast, schedule and event rows."""
from __future__ import annotations

from math import exp, sqrt
from statistics import NormalDist

import pandas as pd
import streamlit as st

from abgaenge.forecast import _periods
from abgaenge.schemas import normalize_persnr
from components.org_evaluation_chart import render_comparison_bar_chart


PHASES = (("ATZ_AR_TO_FR", "Freistellungsbeginn", "fr_begin"),
          ("ATZ_END", "Vertragsende", "contract_end"))


def _poisson_quantile(rate: float, probability: float) -> int:
    """Exact CDF for ordinary pools; normal fallback avoids exp underflow."""
    if rate <= 0:
        return 0
    if rate > 600:
        z = NormalDist().inv_cdf(probability)
        return max(0, int(round(rate + z * sqrt(rate) - 0.5)))
    term = exp(-rate)
    cumulative = term
    count = 0
    while cumulative < probability:
        count += 1
        term *= rate / count
        cumulative += term
    return count


def build_atz_evaluation(summary: dict, *, dimension: str, years: list[int],
                         organizations: list[str]) -> dict | None:
    """Keep the engine's monthly exposure and scheduled person events separate."""
    params = summary.get("abgaenge_params")
    pivot = summary.get("atz_pivot")
    audit = summary.get("atz_entry_audit")
    if not isinstance(params, dict) or params.get("components", {}).get("atz", True) is False:
        return None
    if not isinstance(pivot, pd.DataFrame) or not isinstance(audit, pd.DataFrame):
        return None
    if not {"PersNr", "atz_origin", "Organisationseinheit", "fr_begin", "contract_end"}.issubset(pivot):
        return None
    if not {"Jahr", "Organisationseinheit", "Erwartete neue ATZ-Fälle", "Neue ATZ-Fälle"}.issubset(audit) and not audit.empty:
        return None

    mapping = summary.get("cluster_mapping", {}).get("oe_map", {})
    def add_scope(frame: pd.DataFrame) -> pd.DataFrame:
        work = frame.copy()
        if dimension == "OE-Cluster":
            work[dimension] = work["Organisationseinheit"].map(mapping).fillna("Nicht zugeordnet")
        return work

    annual_index = pd.MultiIndex.from_product([organizations, sorted(years)], names=[dimension, "Jahr"])
    annual = annual_index.to_frame(index=False)
    audit_scoped = add_scope(audit) if not audit.empty else pd.DataFrame()
    if not audit_scoped.empty:
        exposure = audit_scoped.groupby([dimension, "Jahr"], as_index=False).agg({
            "Berechtigte Personen": "sum", "Berechtigte mit positiver Quote": "sum",
            "Erwartete neue ATZ-Fälle": "sum", "Neue ATZ-Fälle": "sum"})
        annual = annual.merge(exposure, on=[dimension, "Jahr"], how="left")
    for column in ("Berechtigte Personen", "Berechtigte mit positiver Quote",
                   "Erwartete neue ATZ-Fälle", "Neue ATZ-Fälle"):
        if column not in annual:
            annual[column] = 0
        annual[column] = annual[column].fillna(0)

    schedule = add_scope(pivot)
    schedule["PersNr"] = normalize_persnr(schedule.PersNr)
    schedule = schedule.dropna(subset=["PersNr"]).drop_duplicates("PersNr", keep="first")
    schedule_audit = schedule.loc[schedule[dimension].isin(organizations)].copy()
    selected_new = schedule.loc[schedule.atz_origin.eq("Simulation")].copy()
    selected_new["Jahr"] = pd.to_datetime(
        selected_new.get("ar_begin", pd.Series(pd.NaT, index=selected_new.index)), errors="coerce").dt.year
    scheduled_entries = selected_new.groupby([dimension, "Jahr"]).size()
    entry_mismatches = int((annual.set_index([dimension, "Jahr"])["Neue ATZ-Fälle"] -
                            scheduled_entries.reindex(annual.set_index([dimension, "Jahr"]).index, fill_value=0)).abs().sum())
    atz_params = params.get("atz", {})
    ar_months = round(float(atz_params.get("atz_duration_ar_years", 2.5)) * 12)
    fr_months = round(float(atz_params.get("atz_duration_fr_years", 2.5)) * 12)
    duration_errors = 0
    for row in schedule.loc[schedule.atz_origin.eq("Simulation")].itertuples(index=False):
        scope = getattr(row, dimension) if dimension.isidentifier() else row[schedule.columns.get_loc(dimension)]
        if scope not in organizations:
            continue
        ar_begin = pd.to_datetime(row.ar_begin, errors="coerce")
        if pd.notna(ar_begin) and ar_begin.year not in years:
            continue
        ar_end = pd.to_datetime(row.ar_end, errors="coerce")
        fr_begin = pd.to_datetime(row.fr_begin, errors="coerce")
        fr_end = pd.to_datetime(row.fr_end, errors="coerce")
        contract_end = pd.to_datetime(row.contract_end, errors="coerce")
        if (pd.isna(ar_begin) or pd.isna(ar_end) or pd.isna(fr_begin) or pd.isna(fr_end) or
                pd.isna(contract_end) or ar_end != ar_begin + pd.DateOffset(months=ar_months) or
                fr_begin != ar_end + pd.Timedelta(days=1) or
                fr_end != fr_begin + pd.DateOffset(months=fr_months) or contract_end != fr_end):
            duration_errors += 1
    events = summary["events"].copy()
    events["PersNr"] = normalize_persnr(events.get("persnr", pd.Series(pd.NA, index=events.index)))
    events["event_date"] = pd.to_datetime(events.event_date, errors="coerce")
    departures = events.loc[events["Köpfe-Veränderung"].lt(0) & events.PersNr.notna()]
    first_departure = departures.groupby("PersNr").event_date.min().to_dict()
    first_departure_type = (departures.sort_values("event_date").drop_duplicates("PersNr")
                            .set_index("PersNr").Ereignisart.to_dict())
    periods = _periods(pd.Timestamp(summary["metadata"]["base_date"]),
                       pd.Timestamp(summary["metadata"]["target_date"]), "M")
    details = []
    for row in schedule.itertuples(index=False):
        pid = row.PersNr
        scope = getattr(row, dimension) if dimension.isidentifier() else row[schedule.columns.get_loc(dimension)]
        origin = row.atz_origin
        if scope not in organizations:
            continue
        for kind, label, date_col in PHASES:
            planned_date = pd.to_datetime(getattr(row, date_col), errors="coerce")
            if pd.isna(planned_date):
                continue
            period = next((p for p in periods if p.start <= planned_date <= p.end), None)
            if period is None or period.start.year not in years:
                continue
            earlier_exit = first_departure.get(pid, pd.NaT)
            competing_exit = (pd.notna(earlier_exit) and earlier_exit < period.start and
                              first_departure_type.get(pid) != kind)
            actual = events.loc[events.PersNr.eq(pid) & events.Ereignisart.eq(kind) &
                                events.event_date.eq(period.end)]
            released_at_start = getattr(row, "atz_already_fr_at_start", False)
            already_released = (kind == "ATZ_AR_TO_FR" and pd.notna(released_at_start)
                                and bool(released_at_start))
            details.append({dimension: scope, "Jahr": period.start.year, "PersNr": pid,
                            "Herkunft": origin, "Phase": label, "Geplanter Termin": planned_date,
                            "Ereignisdatum": actual.event_date.iloc[0] if not actual.empty else pd.NaT,
                            "Status": "Bereits freigestellt am Ausgangsstichtag" if already_released else
                                      "Vorher ausgeschieden" if competing_exit else
                                      ("Eingetreten" if not actual.empty else "Fehlt"),
                            "Köpfe-Veränderung": float(actual["Köpfe-Veränderung"].sum()),
                            "MAK-Veränderung": float(actual["MAK-Veränderung"].sum())})
    details = pd.DataFrame(details)
    for _, label, _ in PHASES:
        if details.empty:
            annual[f"Geplant: {label}"] = 0
            annual[f"Beobachtet: {label}"] = 0
            continue
        phase = details.loc[details.Phase.eq(label)]
        planned = phase.loc[~phase.Status.isin(("Vorher ausgeschieden", "Bereits freigestellt am Ausgangsstichtag"))].groupby([dimension, "Jahr"]).size()
        actual = phase.loc[phase.Status.eq("Eingetreten")].groupby([dimension, "Jahr"]).size()
        annual[f"Geplant: {label}"] = annual.set_index([dimension, "Jahr"]).index.map(planned).fillna(0).to_numpy()
        annual[f"Beobachtet: {label}"] = annual.set_index([dimension, "Jahr"]).index.map(actual).fillna(0).to_numpy()

    atz_events = events.loc[events.Ereignisart.isin([p[0] for p in PHASES]) &
                             events.Jahr.isin(years) & events[dimension].isin(organizations)]
    annual["ATZ-Kopfveränderung"] = annual.set_index([dimension, "Jahr"]).index.map(
        atz_events.groupby([dimension, "Jahr"])["Köpfe-Veränderung"].sum()).fillna(0).to_numpy()
    annual["ATZ-MAK-Veränderung"] = annual.set_index([dimension, "Jahr"]).index.map(
        atz_events.groupby([dimension, "Jahr"])["MAK-Veränderung"].sum()).fillna(0).to_numpy()

    matched = set()
    if not details.empty:
        for detail in details.loc[details.Status.eq("Eingetreten")].itertuples(index=False):
            kind = "ATZ_AR_TO_FR" if detail.Phase == "Freistellungsbeginn" else "ATZ_END"
            matched.add((detail.PersNr, kind, detail.Ereignisdatum))
    unexpected = int(sum((row.PersNr, row.Ereignisart, row.event_date) not in matched
                         for row in atz_events.itertuples(index=False)))

    expected = float(annual["Erwartete neue ATZ-Fälle"].sum())
    observed = int(annual["Neue ATZ-Fälle"].sum())
    eligible = int(annual["Berechtigte Personen"].sum())
    positive = int(annual["Berechtigte mit positiver Quote"].sum())
    lower = _poisson_quantile(expected, 0.005)
    upper = min(positive, _poisson_quantile(expected, 0.995))
    lower = min(lower, positive)
    issues = details.loc[details.Status.eq("Fehlt")] if not details.empty else details
    mismatches = int(len(issues))
    expected_heads = atz_events.Ereignisart.eq("ATZ_END").map({True: -1, False: 0})
    effect_errors = int((atz_events["Köpfe-Veränderung"].to_numpy() != expected_heads.to_numpy()).sum())
    effect_errors += int(atz_events["MAK-Veränderung"].gt(0).sum())
    baseline = summary.get("stock_basis", {}).get("baseline_snapshot")
    already_released_transitions = 0
    if isinstance(baseline, pd.DataFrame) and {"PersNr", "ist_atz_fr"}.issubset(baseline):
        released_ids = set(normalize_persnr(baseline.loc[baseline.ist_atz_fr.fillna(False).astype(bool), "PersNr"]).dropna())
        already_released_transitions = int((atz_events.Ereignisart.eq("ATZ_AR_TO_FR") &
                                            atz_events.PersNr.isin(released_ids)).sum())
        effect_errors += already_released_transitions
    exposure_years = 0.0
    if not audit_scoped.empty:
        scoped_audit = audit_scoped.loc[audit_scoped[dimension].isin(organizations) & audit_scoped.Jahr.isin(years)]
        exposure_years = float((scoped_audit["Berechtigte Personen"] *
                                ((pd.to_datetime(scoped_audit.period_end) -
                                  pd.to_datetime(scoped_audit.period_start)).dt.days / 365.25)).sum())
    totals = annual.groupby(dimension, as_index=False).sum(numeric_only=True).drop(columns="Jahr", errors="ignore")
    return {"annual": annual, "totals": totals, "details": details,
            "schedule_audit": schedule_audit, "expected": expected, "observed": observed,
            "eligible": eligible, "lower": lower, "upper": upper, "within_interval": lower <= observed <= upper,
            "rate": expected / exposure_years if exposure_years else 0.0,
            "mismatches": mismatches, "unexpected": unexpected, "effect_errors": effect_errors,
            "entry_mismatches": entry_mismatches,
            "already_released_transitions": already_released_transitions,
            "duration_errors": duration_errors, "ar_months": ar_months, "fr_months": fr_months,
            "undated_effects": summary.get("stock_reconciliation", {}).get("documented_effects", pd.DataFrame())}


def build_atz_chart_data(result: dict, *, dimension: str) -> pd.DataFrame:
    annual = result["annual"]
    frames = []
    for label, observed, expected in (("Neue ATZ-Fälle", "Neue ATZ-Fälle", "Erwartete neue ATZ-Fälle"),
                                      ("Freistellungsbeginn", "Beobachtet: Freistellungsbeginn", "Geplant: Freistellungsbeginn"),
                                      ("Vertragsende", "Beobachtet: Vertragsende", "Geplant: Vertragsende")):
        for column, suffix in ((observed, "beobachtet"), (expected, "erwartet")):
            frame = annual[[dimension, "Jahr", column]].rename(columns={column: "Anzahl"}).copy()
            frame["Serie"] = frame[dimension].astype(str) + " · " + label + " · " + suffix
            frames.append(frame[["Jahr", "Serie", "Anzahl"]])
    return pd.concat(frames, ignore_index=True)


def render_atz_evaluation(summary: dict, *, dimension: str, years: list[int],
                          organizations: list[str], event_types: list[str]) -> dict | None:
    if not set(event_types) & {"ATZ_AR_TO_FR", "ATZ_END"}:
        return None
    st.subheader("Einordnung der Altersteilzeit")
    result = build_atz_evaluation(summary, dimension=dimension, years=years, organizations=organizations)
    if result is None:
        st.info("Für die ATZ-Einordnung bitte eine neue Simulation mit gespeicherten ATZ-Zeitplänen und Monatsquoten berechnen.")
        return None
    st.caption("Neue ATZ-Fälle, Freistellungsbeginn und Vertragsende sind unterschiedliche Vorgänge. Grau zeigt Erwartung beziehungsweise Zeitplan; farbig zeigt das Ergebnis des gespeicherten Laufs.")
    render_comparison_bar_chart(build_atz_chart_data(result, dimension=dimension))
    cols = st.columns(5)
    cols[0].metric("Neue ATZ-Fälle", result["observed"])
    cols[1].metric("Erwartet", f'{result["expected"]:.2f}')
    cols[2].metric("Berechtigte Personenperioden", result["eligible"])
    cols[3].metric("Wirksame Quote/Jahr", f'{result["rate"]:.1%}')
    cols[4].metric("Vergleichsbereich (99 %)", f'{result["lower"]}–{result["upper"]}')
    if result["within_interval"]:
        st.success("Die Zahl neuer ATZ-Fälle liegt im 99-%-Vergleichsbereich der gespeicherten Monatsquoten.")
    else:
        st.error("Die Zahl neuer ATZ-Fälle liegt außerhalb des 99-%-Vergleichsbereichs der gespeicherten Monatsquoten.")
    st.caption("Erwartung = Summe der individuellen Monatsquoten der tatsächlich berechtigten Personen. Der Vergleichsbereich nutzt eine Poisson-Näherung; die monatliche Obergrenze und die Abhängigkeit der Folgeperioden werden nur näherungsweise berücksichtigt.")
    cols = st.columns(5)
    cols[0].metric("Geplante Freistellungsbeginne", int(result["annual"]["Geplant: Freistellungsbeginn"].sum()))
    cols[1].metric("Geplante Vertragsenden", int(result["annual"]["Geplant: Vertragsende"].sum()))
    cols[2].metric("Arbeitsphase", f'{result["ar_months"]} Monate')
    cols[3].metric("Freistellungsphase", f'{result["fr_months"]} Monate')
    cols[4].metric("Fehlende Zeitplanereignisse", result["mismatches"])
    if (result["mismatches"] or result["unexpected"] or result["effect_errors"] or
            result["duration_errors"] or result["entry_mismatches"]):
        st.error(f'{result["entry_mismatches"]} neue Fälle weichen zwischen Auswahlprotokoll und Zeitplan ab; {result["mismatches"]} fällige ATZ-Ereignisse fehlen; {result["unexpected"]} Ereignisse sind keinem fälligen Zeitplan zugeordnet; {result["effect_errors"]} Ereignisse haben einen abweichenden Kopf- oder MAK-Effekt (davon {result["already_released_transitions"]} bereits freigestellte Personen mit erneutem Phasenwechsel); {result["duration_errors"]} neue Zeitpläne weichen von den Dauern ab.')
    else:
        st.success("Fällige ATZ-Zeitplanereignisse und ihre Kopf-Effekte stimmen überein.")
    effects = result["undated_effects"]
    if isinstance(effects, pd.DataFrame) and not effects.empty:
        scoped = effects.loc[effects[dimension].isin(organizations)] if dimension in effects else effects
        if not scoped.empty:
            st.warning("ATZ-Freistellungsstatus endet bei Bestandsfällen ohne datiertes ATZ-Ereignis. Diese MAK-Effekte sind nur bis zum Zielstichtag belegt und werden keinem Jahr zugeteilt.")
            st.dataframe(scoped, hide_index=True, use_container_width=True)
    st.markdown("**Gesamtsumme je Organisation**")
    st.dataframe(result["totals"], hide_index=True, use_container_width=True)
    st.markdown("**Jahreswerte**")
    st.dataframe(result["annual"], hide_index=True, use_container_width=True)
    with st.expander("ATZ-Zeitpläne je Person"):
        st.dataframe(result["details"], hide_index=True, use_container_width=True)
    with st.expander("ATZ-Ausgangstermine und verwendete Zeitpläne"):
        st.dataframe(result["schedule_audit"], hide_index=True, use_container_width=True)
    return result


def render_atz_baseline_effects(summary: dict, *, dimension: str, organizations: list[str]) -> None:
    """Expose saved undated ATZ recovery even when the ATZ component was disabled."""
    effects = summary.get("stock_reconciliation", {}).get("documented_effects")
    if not isinstance(effects, pd.DataFrame) or effects.empty or dimension not in effects:
        return
    scoped = effects.loc[effects[dimension].isin(organizations)]
    if scoped.empty:
        return
    st.subheader("ATZ-Freistellungsstatus im Ausgangsbestand")
    st.warning("Bei diesen Bestandsfällen endet der Freistellungsstatus ohne datiertes ATZ-Ereignis. Der MAK-Rückgewinn ist nur bis zum Zielstichtag belegt und wird keinem Jahr zugeordnet.")
    cols = st.columns(2)
    cols[0].metric("Betroffene Personen", scoped.PersNr.nunique())
    cols[1].metric("Undatierter MAK-Effekt", f'{scoped["MAK-Effekt"].sum():.2f}')
    st.dataframe(scoped, hide_index=True, use_container_width=True)
