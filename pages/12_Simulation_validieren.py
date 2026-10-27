"""Review and approve the exact saved simulation result."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pandas as pd
import streamlit as st

from components.ui_shell import render_context_box, render_page_header
from dataloader.kpi_engine import compute_headcount
from utils.simulation_adjustments import derive_retained_departures, eligible_departures
from utils.org_simulation_reconciliation import raw_stock_mak
from utils.simulation_validation import (
    DRAFT_KEY, HISTORY_KEY, approve_adjustment, approve_original, clear_adjustment,
    current_view, stage_adjustment,
)


def _annual_comparison(original, adjusted) -> pd.DataFrame:
    parts = []
    persons = set(adjusted.metadata.get("adjustment", {}).get("persons", []))
    candidates = eligible_departures(original)
    affected = set(candidates.loc[candidates["PersNr"].isin(persons), "Organisationseinheit"]) if not candidates.empty else set()
    for label, version in (("Original", original), ("Justierung", adjusted)):
        annual = version.event_summary.get("stock_reconciliation", {}).get("annual")
        if not isinstance(annual, pd.DataFrame) or annual.empty:
            return pd.DataFrame()
        rows = annual.loc[
            (annual["Ebene"].eq("Gesamt")
             | (annual["Ebene"].eq("Organisationseinheit") & annual["Organisation"].isin(affected)))
            & annual["Art"].eq("Jahresstichtag")
        ]
        parts.append(rows[["Ebene", "Organisation", "Jahr", "Bestand-Köpfe", "Bestand-MAK"]]
                     .rename(columns={"Bestand-Köpfe": f"Köpfe {label}", "Bestand-MAK": f"MAK {label}"}))
    joined = parts[0].merge(parts[1], on=["Ebene", "Organisation", "Jahr"], how="outer", validate="one_to_one")
    joined["Delta Köpfe"] = joined["Köpfe Justierung"] - joined["Köpfe Original"]
    joined["Delta MAK"] = joined["MAK Justierung"] - joined["MAK Original"]
    return joined.sort_values(["Ebene", "Organisation", "Jahr"])


def main() -> None:
    render_page_header(
        "Simulation validieren",
        "Berechneten Lauf prüfen und als gemeinsamen Ergebnisstand freigeben.",
    )
    original = current_view(st.session_state, use_approved=False)
    if original is None:
        render_context_box(
            "Keine Simulation verfügbar",
            "Bitte zuerst auf 'Kompakt plus Simulation' einen Lauf berechnen.",
            tone="warning",
        )
        st.page_link("pages/7_⚡_Kompakt_plus_Simulation.py", label="Zur Simulation")
        return
    active = current_view(st.session_state)
    draft = st.session_state.get(DRAFT_KEY)
    view = draft if draft is not None else active

    metadata = view.metadata
    run_id = hashlib.sha256(original.signature.encode("utf-8")).hexdigest()[:12]
    result_id = hashlib.sha256(view.version.encode("utf-8")).hexdigest()[:12]
    attrition_seed = original.event_context.get("abgaenge_params", {}).get("random_seed", "–")
    accession_seed = original.event_context.get("zugaenge_params", {}).get("random_seed", "–")
    st.caption(
        f"Lauf {run_id} · Ergebnisversion {result_id} · Instanz {view.instance} · Ausgangsstichtag {metadata.get('base_date', '–')} "
        f"· Zielstichtag {metadata.get('target_date', '–')} · Seed Abgänge {attrition_seed} "
        f"· Seed Zugänge {accession_seed}"
    )
    if active.approved:
        approval = st.session_state["simulation_validation_approval"]
        label = "Justierter Ergebnisstand" if approval.get("kind") == "adjusted" else "Unveränderter Originallauf"
        st.success(f"{label} freigegeben am {approval['approved_at']}.")
    else:
        st.info("Dieser Lauf ist vorläufig. OE- und Jobgruppen-Analyse sind bis zur Freigabe gesperrt.")

    baseline = view.event_context.get("baseline_snapshot")
    final = view.event_context.get("final_snapshot")
    if isinstance(baseline, pd.DataFrame) and isinstance(final, pd.DataFrame):
        start_heads, end_heads = compute_headcount(baseline), compute_headcount(final)
        start_mak, end_mak = raw_stock_mak(baseline), raw_stock_mak(final)
        columns = st.columns(4)
        columns[0].metric("Ausgang · Köpfe", f"{start_heads:,}")
        columns[1].metric("Ziel · Köpfe", f"{end_heads:,}", delta=f"{end_heads - start_heads:+,}")
        columns[2].metric("Ausgang · MAK", f"{start_mak:,.2f}")
        columns[3].metric("Ziel · MAK", f"{end_mak:,.2f}", delta=f"{end_mak - start_mak:+,.2f}")

    bridge = view.event_summary.get("stock_reconciliation", {})
    status = bridge.get("status", "Nicht verfügbar")
    st.subheader("Bestandsprüfung")
    st.write(f"OE-Bestandsüberleitung: **{status}**")
    if bridge.get("reason"):
        st.caption(str(bridge["reason"]))
    checks = bridge.get("checks")
    if isinstance(checks, pd.DataFrame) and not checks.empty:
        st.dataframe(checks, use_container_width=True, hide_index=True)
    annual = bridge.get("annual")
    if isinstance(annual, pd.DataFrame) and not annual.empty:
        with st.expander("Jahresscheiben und Bestandsverlauf"):
            st.dataframe(annual, use_container_width=True, hide_index=True)

    st.subheader("Justierung")
    st.caption("Aktuell unterstützt: einzelne Kündigungs- oder Rentenabgänge aus einer eindeutig zugeordneten OE zurücknehmen. Andere Fälle bleiben zur fachlichen Prüfung gesperrt.")
    candidates = eligible_departures(original)
    if candidates.empty:
        zugaenge = original.event_context.get("accession_events")
        if isinstance(zugaenge, pd.DataFrame) and not zugaenge.empty:
            st.info("Die Rücknahme von Abgängen ist derzeit nur für Läufe ohne Zugänge verfügbar. Der Originallauf kann dennoch freigegeben werden.")
        else:
            st.info("Für diesen Lauf gibt es keine eindeutig geeigneten Abgänge zur Rücknahme.")
    else:
        org = st.selectbox("Organisationseinheit", sorted(candidates["Organisationseinheit"].unique()))
        scoped = candidates.loc[candidates["Organisationseinheit"].eq(org)]
        st.dataframe(scoped, use_container_width=True, hide_index=True)
        selected = st.multiselect(
            "Abgänge in dieser OE zurücknehmen · Personalnummer",
            scoped["PersNr"].tolist(),
            default=scoped["PersNr"].tolist(),
        )
        if st.button("Justierung prüfen", disabled=not selected):
            try:
                with st.spinner("Korrigierten Ergebnisstand und Bestandsprüfung erstellen ..."):
                    result = derive_retained_departures(original, selected)
                stage_adjustment(st.session_state, result)
                st.rerun()
            except (ValueError, KeyError) as exc:
                st.error(str(exc))
    if draft is not None:
        if not active.approved:
            st.info("Justierungsentwurf vorhanden. Die Werte oben zeigen den Entwurf; bis zur Freigabe bleiben die Analyse-Seiten gesperrt.")
        comparison = _annual_comparison(original, draft)
        if not comparison.empty:
            st.markdown("**Jahresendbestand: Original, Justierung und Differenz**")
            st.dataframe(comparison, use_container_width=True, hide_index=True)
        original_final = original.event_context.get("final_snapshot")
        adjusted_final = draft.event_context.get("final_snapshot")
        if isinstance(original_final, pd.DataFrame) and isinstance(adjusted_final, pd.DataFrame):
            st.dataframe(pd.DataFrame([
                {"Stand": "Original", "Köpfe": compute_headcount(original_final), "MAK": raw_stock_mak(original_final)},
                {"Stand": "Justierung", "Köpfe": compute_headcount(adjusted_final), "MAK": raw_stock_mak(adjusted_final)},
            ]), use_container_width=True, hide_index=True)
        if st.button("Justierung verwerfen"):
            clear_adjustment(st.session_state)
            st.rerun()

    st.subheader("Freigabe")
    st.caption("Ohne Justierung wird genau der gespeicherte Originallauf freigegeben. Es erfolgt keine Neuberechnung der Simulation.")
    if status == "Abweichung":
        st.error("Die OE-Bestandsüberleitung ist nicht abgestimmt. Dieser Ergebnisstand kann noch nicht freigegeben werden.")
    if status == "Nicht verfügbar":
        st.warning("Eine vollständige OE-Bestandsüberleitung ist für diesen Lauf nicht verfügbar. Die Freigabe bestätigt den gespeicherten Originallauf.")
    original_status = original.event_summary.get("stock_reconciliation", {}).get("status")
    if st.button("Unveränderten Lauf freigeben", type="primary", disabled=original_status == "Abweichung" or (active.approved and active.version == original.version)):
        try:
            approve_original(st.session_state, approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if draft is not None and st.button("Justierten Ergebnisstand freigeben", type="primary", disabled=status != "Abgestimmt" or (active.approved and active.version == draft.version)):
        try:
            approve_adjustment(st.session_state, approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if active.approved:
        st.page_link("pages/7_⚡_Kompakt_plus_Simulation.py", label="Kompakt plus Simulation")
        st.page_link("pages/10_🏢_Organisationseinheiten_Simulation.py", label="OE-Analyse Simulation")
        st.page_link("pages/11_💼_Jobfamily_Simulation.py", label="Jobgruppen-Analyse Simulation")
    history = [entry for entry in st.session_state.get(HISTORY_KEY, [])
               if entry.get("signature") == original.signature and entry.get("instance") == original.instance]
    if history:
        with st.expander("Freigabehistorie"):
            st.dataframe(pd.DataFrame([
                {"Freigabezeit": entry["approved_at"],
                 "Art": "Justierung" if entry["kind"] == "adjusted" else "Original",
                 "Betroffene Personen": ", ".join(entry.get("adjustment", {}).get("persons", [])) if entry.get("adjustment") else "–"}
                for entry in history
            ]), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
