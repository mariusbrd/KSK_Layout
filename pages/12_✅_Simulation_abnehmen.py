"""Review and approve the exact saved simulation result."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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


def _new_zero_mak_orgs(stock: pd.DataFrame, tolerance: float) -> set[str]:
    if "Ausgangs-MAK" not in stock:
        return set()
    start = pd.to_numeric(stock["Ausgangs-MAK"], errors="coerce")
    return _zero_mak_orgs(stock.loc[start.gt(tolerance)], tolerance)


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
                     "Ausgangs-Köpfe": compute_headcount(before), "Ausgangs-MAK": raw_stock_mak(before), "Finale MAK": raw_stock_mak(after),
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


def _render_annual_change_chart(comparison: pd.DataFrame, organization: str) -> None:
    rows = comparison.loc[comparison["Ebene"].eq("Organisationseinheit")
                          & comparison["Organisation"].eq(organization)].sort_values("Jahr")
    if rows.empty:
        st.info("Für diese OE sind keine abgestimmten Jahresscheiben verfügbar.")
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.13,
                        subplot_titles=("Köpfe", "MAK"))
    for index, metric in enumerate(("Köpfe", "MAK"), start=1):
        for label, color, dash in (("Original", "#7c8da5", "dash"), ("Nach Rücknahme", "#0088de", "solid")):
            column = f"{metric} {'Justierung' if label == 'Nach Rücknahme' else 'Original'}"
            fig.add_trace(go.Scatter(x=rows["Jahr"], y=rows[column], mode="lines+markers",
                                     name=label, legendgroup=label, showlegend=index == 1,
                                     line=dict(color=color, width=3, dash=dash),
                                     hovertemplate=f"%{{x}}<br>{metric}: %{{y:,.2f}}<extra>{label}</extra>"),
                          row=index, col=1)
    fig.update_xaxes(dtick=1)
    fig.update_layout(height=540, margin=dict(l=20, r=20, t=65, b=25),
                      legend=dict(orientation="h", y=1.14, x=0))
    st.plotly_chart(fig, use_container_width=True, key=f"approval_yearly_{organization}")


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
    run_id = hashlib.sha256(original.signature.encode("utf-8")).hexdigest()[:12]
    attrition_seed = original.event_context.get("abgaenge_params", {}).get("random_seed", "–")
    accession_seed = original.event_context.get("zugaenge_params", {}).get("random_seed", "–")
    st.caption(
        f"Ausgangsstichtag {original.metadata.get('base_date', '–')} · "
        f"Zielstichtag {original.metadata.get('target_date', '–')}"
    )
    with st.expander("Laufdetails und Zufalls-Seeds"):
        st.caption(f"Lauf {run_id} · Instanz {original.instance} · Seed Abgänge {attrition_seed} · Seed Zugänge {accession_seed}")
    if active.approved:
        approval = st.session_state["simulation_validation_approval"]
        label = "Justierter Ergebnisstand" if approval.get("kind") == "adjusted" else "Unveränderter Originallauf"
        st.success(f"{label} freigegeben am {approval['approved_at']}.")
    else:
        st.info("Dieser Lauf ist vorläufig. OE- und Jobgruppen-Analyse sind bis zur Freigabe gesperrt.")

    st.subheader("1. Rücknahmen auswählen")
    st.caption("Eine markierte Zeile bedeutet: Dieser bereits simulierte Abgang soll zurückgenommen werden.")
    candidates = eligible_departures(original)
    selected = []
    selected_org = None
    if candidates.empty:
        zugaenge = original.event_context.get("accession_events")
        if isinstance(zugaenge, pd.DataFrame) and not zugaenge.empty:
            st.info("Rücknahmen sind für Läufe mit Zugängen noch nicht verfügbar. Der Originallauf kann freigegeben werden.")
        else:
            st.info("Für diesen Lauf gibt es keine eindeutig geeigneten Abgänge zur Rücknahme.")
    else:
        selected_org = st.selectbox("Organisationseinheit", sorted(candidates["Organisationseinheit"].unique()),
                                    key=f"approval_org_{original.instance}_{run_id}")
        scoped = candidates.loc[candidates["Organisationseinheit"].eq(selected_org)].reset_index(drop=True)
        state = st.dataframe(scoped, hide_index=True, use_container_width=True,
                             on_select="rerun", selection_mode="multi-row",
                             key=f"approval_rows_{original.instance}_{run_id}_{selected_org}")
        selected = scoped.iloc[state.selection.rows]["PersNr"].astype(str).tolist()
        st.caption(f"{len(selected)} von {len(scoped)} Abgängen zur Rücknahme markiert.")

    draft_ids = set(draft.metadata.get("adjustment", {}).get("persons", ())) if draft is not None else set()
    draft_matches = draft is not None and bool(selected) and set(selected) == draft_ids
    if draft is not None and not draft_matches:
        st.warning("Die Auswahl hat sich seit der letzten Prüfung geändert. Bitte die aktuelle Auswahl erneut prüfen.")
        if st.button("Vorherigen Entwurf verwerfen"):
            clear_adjustment(st.session_state)
            st.rerun()
    if selected and not draft_matches and st.button("Ausgewählte Rücknahmen prüfen"):
        try:
            with st.spinner("Korrigierten Ergebnisstand und Bestandsprüfungen erstellen ..."):
                result = derive_retained_departures(original, selected)
            stage_adjustment(st.session_state, result)
            st.rerun()
        except (ValueError, KeyError) as exc:
            st.error(str(exc))

    view = draft if draft_matches else active
    metadata = view.metadata
    result_id = hashlib.sha256(view.version.encode("utf-8")).hexdigest()[:12]
    st.caption(f"Angezeigte Ergebnisversion: {result_id}" + (" · geprüfte Rücknahme" if draft_matches else ""))

    baseline = view.event_context.get("baseline_snapshot")
    final = view.event_context.get("final_snapshot")
    if isinstance(baseline, pd.DataFrame) and isinstance(final, pd.DataFrame):
        start_heads, end_heads = compute_headcount(baseline), compute_headcount(final)
        start_mak, end_mak = raw_stock_mak(baseline), raw_stock_mak(final)
        st.markdown("""<style>
        div[data-testid="stMetric"] { min-height: 8.5rem; width: 100%; }
        </style>""", unsafe_allow_html=True)
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
    tolerance = bridge.get("mak_tolerance", 1e-6)
    zero_orgs = _new_zero_mak_orgs(final_stock, tolerance) if not final_stock.empty else set()
    if zero_orgs:
        st.warning("OE fällt bis zum Zielstichtag auf 0 MAK: " + ", ".join(sorted(zero_orgs)))
    if not final_stock.empty:
        st.caption(f"{len(final_stock)} OEs geprüft · {len(zero_orgs)} fallen auf 0 MAK · "
                   f"{int((final_stock['Finale MAK'] < -tolerance).sum())} mit negativem Endbestand")
        st.markdown("**OE-Endbestand am Zielstichtag**")
        st.dataframe(_mark_zero_mak_rows(final_stock, zero_orgs), use_container_width=True,
                     hide_index=True, column_order=["Organisation", "Ausgangs-Köpfe", "Finale Köpfe", "Ausgangs-MAK", "Finale MAK"])
    checks = bridge.get("checks")
    if isinstance(checks, pd.DataFrame) and not checks.empty:
        with st.expander("Details der OE-Bestandsüberleitung"):
            st.dataframe(_mark_zero_mak_rows(checks, zero_orgs), use_container_width=True, hide_index=True)
    annual = bridge.get("annual")
    if isinstance(annual, pd.DataFrame) and not annual.empty:
        with st.expander("Jahresscheiben und Bestandsverlauf"):
            st.dataframe(_mark_zero_mak_rows(annual, zero_orgs, target_date=metadata.get("target_date")),
                         use_container_width=True, hide_index=True)

    st.subheader("2. Wirkung der Rücknahme prüfen")
    if draft_matches:
        st.success(f"Geprüfte Rücknahme in {selected_org}: {len(selected)} Abgänge · Personalnummern {', '.join(sorted(selected))}")
        comparison = _annual_comparison(original, draft)
        if not comparison.empty:
            st.markdown(f"**Jahresscheiben für {selected_org}: Original und korrigierter Bestand**")
            _render_annual_change_chart(comparison, selected_org)
            with st.expander("Jahreswerte und Differenzen als Tabelle"):
                st.dataframe(comparison.loc[comparison["Organisation"].eq(selected_org)],
                             use_container_width=True, hide_index=True)
        original_final = original.event_context.get("final_snapshot")
        adjusted_final = draft.event_context.get("final_snapshot")
        if isinstance(original_final, pd.DataFrame) and isinstance(adjusted_final, pd.DataFrame):
            old_oe = original_final.loc[original_final["Organisationseinheit"].eq(selected_org)]
            new_oe = adjusted_final.loc[adjusted_final["Organisationseinheit"].eq(selected_org)]
            st.dataframe(pd.DataFrame([{
                "OE": selected_org, "Köpfe Original": compute_headcount(old_oe),
                "Köpfe korrigiert": compute_headcount(new_oe),
                "Köpfe Änderung": compute_headcount(new_oe) - compute_headcount(old_oe),
                "MAK Original": raw_stock_mak(old_oe), "MAK korrigiert": raw_stock_mak(new_oe),
                "MAK Änderung": raw_stock_mak(new_oe) - raw_stock_mak(old_oe),
            }]), use_container_width=True, hide_index=True)
            with st.expander("Gesamtbestand: Original und Korrektur"):
                st.dataframe(pd.DataFrame([
                    {"Stand": "Original", "Köpfe": compute_headcount(original_final), "MAK": raw_stock_mak(original_final)},
                    {"Stand": "Justierung", "Köpfe": compute_headcount(adjusted_final), "MAK": raw_stock_mak(adjusted_final)},
                ]), use_container_width=True, hide_index=True)
        if st.button("Justierung verwerfen"):
            clear_adjustment(st.session_state)
            st.rerun()
    elif selected:
        st.info("Die aktuelle Auswahl ist noch nicht geprüft. Bitte zuerst 'Ausgewählte Rücknahmen prüfen' klicken.")
    else:
        st.caption("Keine Rücknahme ausgewählt. Der Originallauf kann unverändert freigegeben werden.")

    st.subheader("3. Ergebnisstand freigeben")
    st.caption("Der blaue Knopf gibt ausschließlich die oben geprüfte Auswahl frei. Der helle Knopf gibt den Originallauf ohne Rücknahmen frei.")
    if status == "Abweichung":
        st.error("Die OE-Bestandsüberleitung ist nicht abgestimmt. Dieser Ergebnisstand kann noch nicht freigegeben werden.")
    if status == "Nicht verfügbar":
        st.warning("Eine vollständige OE-Bestandsüberleitung ist für den Originallauf nicht verfügbar. Ausgewählte Rücknahmen werden vor ihrer Freigabe gesondert geprüft.")
    original_status = original.event_summary.get("stock_reconciliation", {}).get("status")
    original_col, adjusted_col = st.columns(2)
    with original_col:
        approve_original_clicked = st.button(
            "Lauf unverändert freigeben", type="secondary", use_container_width=True,
            disabled=original_status == "Abweichung" or (active.approved and active.version == original.version),
        )
    with adjusted_col:
        approve_adjustment_clicked = st.button(
            "Lauf mit Änderungen freigeben", type="primary", use_container_width=True,
            disabled=not draft_matches or status != "Abgestimmt" or
                     (active.approved and active.version == draft.version),
        )
    if approve_adjustment_clicked:
        try:
            approve_adjustment(st.session_state, approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            st.rerun()
        except (ValueError, KeyError) as exc:
            st.error(str(exc))
    if approve_original_clicked:
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
