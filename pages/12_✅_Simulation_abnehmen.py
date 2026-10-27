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


def _zero_mak_orgs(checks: pd.DataFrame, tolerance: float) -> set[str]:
    """Use the actual final stock, not a projected annual intermediate value."""
    if not {"Ebene", "Organisation", "Finale MAK"}.issubset(checks.columns):
        return set()
    final_mak = pd.to_numeric(checks["Finale MAK"], errors="coerce")
    mask = checks["Ebene"].eq("Organisationseinheit") & final_mak.notna() & final_mak.abs().le(tolerance)
    return set(checks.loc[mask, "Organisation"].astype(str))


def _oe_final_stock(baseline: pd.DataFrame, final: pd.DataFrame) -> pd.DataFrame:
    """Include OEs that existed at the start even if no final row remains."""
    if "Organisationseinheit" not in baseline or "Organisationseinheit" not in final:
        return pd.DataFrame()
    names = sorted(set(baseline["Organisationseinheit"].dropna().astype(str))
                   | set(final["Organisationseinheit"].dropna().astype(str)))
    rows = []
    for name in names:
        before = baseline.loc[baseline["Organisationseinheit"].astype(str).eq(name)]
        after = final.loc[final["Organisationseinheit"].astype(str).eq(name)]
        rows.append({"Ebene": "Organisationseinheit", "Organisation": name,
                     "Ausgangs-MAK": raw_stock_mak(before), "Finale MAK": raw_stock_mak(after),
                     "Finale Köpfe": compute_headcount(after)})
    return pd.DataFrame(rows)


def _mark_zero_mak_rows(frame: pd.DataFrame, orgs: set[str], *, target_date=None):
    """Color only the affected OE's final row in the displayed table."""
    def row_style(row):
        affected = row["Ebene"] == "Organisationseinheit" and str(row["Organisation"]) in orgs
        if "Art" in row.index:
            affected = (affected and target_date is not None and row["Art"] == "Jahresstichtag"
                        and pd.Timestamp(row["Stichtag"]) == pd.Timestamp(target_date))
        color = "background-color: #f8d7da; color: #842029" if affected else ""
        return [color] * len(row)

    return frame.style.apply(row_style, axis=1)


def _annual_comparison(original, adjusted) -> pd.DataFrame:
    parts = []
    persons = set(adjusted.metadata.get("adjustment", {}).get("persons", []))
    candidates = eligible_departures(original)
    selected = []
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
        "Simulation abnehmen",
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
    final_stock = _oe_final_stock(baseline, final) if isinstance(baseline, pd.DataFrame) and isinstance(final, pd.DataFrame) else pd.DataFrame()
    zero_orgs = _zero_mak_orgs(final_stock, bridge.get("mak_tolerance", 1e-6)) if not final_stock.empty else set()
    if zero_orgs:
        st.warning("OE mit 0 MAK am Zielstichtag: " + ", ".join(sorted(zero_orgs)))
    if not final_stock.empty:
        st.markdown("**OE-Endbestand am Zielstichtag**")
        st.dataframe(_mark_zero_mak_rows(final_stock, zero_orgs), use_container_width=True, hide_index=True)
    checks = bridge.get("checks")
    if isinstance(checks, pd.DataFrame) and not checks.empty:
        st.dataframe(_mark_zero_mak_rows(checks, zero_orgs), use_container_width=True, hide_index=True)
    annual = bridge.get("annual")
    if isinstance(annual, pd.DataFrame) and not annual.empty:
        with st.expander("Jahresscheiben und Bestandsverlauf"):
            st.dataframe(_mark_zero_mak_rows(annual, zero_orgs, target_date=metadata.get("target_date")),
                         use_container_width=True, hide_index=True)

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
            "Diese Abgänge zurücknehmen · Personalnummer (nur ausgewählte Personen)",
            scoped["PersNr"].tolist(),
            default=[],
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
    st.caption("Ausgewählte Abgänge werden vor der Freigabe zurückgenommen und geprüft. Ohne Auswahl kann ein bereits geprüfter Entwurf freigegeben werden.")
    if status == "Abweichung":
        st.error("Die OE-Bestandsüberleitung ist nicht abgestimmt. Dieser Ergebnisstand kann noch nicht freigegeben werden.")
    if status == "Nicht verfügbar":
        st.warning("Eine vollständige OE-Bestandsüberleitung ist für den Originallauf nicht verfügbar. Ausgewählte Rücknahmen werden vor ihrer Freigabe gesondert geprüft.")
    pending_adjustment = bool(selected) or draft is not None
    if pending_adjustment and st.button(
        "Ausgewählte Abgänge zurücknehmen und freigeben" if selected else "Geprüfte Justierung freigeben",
        type="primary",
        disabled=not selected and (status != "Abgestimmt" or (active.approved and active.version == draft.version)),
    ):
        try:
            selected_ids = tuple(sorted(set(selected)))
            draft_ids = tuple(sorted(draft.metadata.get("adjustment", {}).get("persons", ()))) if draft is not None else ()
            if selected_ids and selected_ids != draft_ids:
                with st.spinner("Ausgewählte Rücknahmen und Bestandsprüfungen erstellen ..."):
                    result = derive_retained_departures(original, selected)
                stage_adjustment(st.session_state, result)
            approve_adjustment(st.session_state, approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            st.rerun()
        except (ValueError, KeyError) as exc:
            st.error(str(exc))
    original_status = original.event_summary.get("stock_reconciliation", {}).get("status")
    if st.button("Originallauf ohne Rücknahme freigeben" if pending_adjustment else "Unveränderten Lauf freigeben",
                 type="secondary" if pending_adjustment else "primary",
                 disabled=original_status == "Abweichung" or (active.approved and active.version == original.version)):
        try:
            approve_original(st.session_state, approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
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
