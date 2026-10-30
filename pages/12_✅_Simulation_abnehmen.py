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
from utils.simulation_adjustments import (
    derive_combined_adjustment,
    eligible_departures, eligible_timing_shifts,
)
from utils.org_simulation_reconciliation import raw_stock_mak
from utils.simulation_validation import (
    DRAFT_KEY, HISTORY_KEY, approve_adjustment, approve_original, clear_adjustment,
    current_view, stage_adjustment, value_fingerprint,
)
from utils.settings_loader import get_setting


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
    def totals(snapshot: pd.DataFrame) -> pd.DataFrame:
        column = next((col for col in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if col in snapshot), None)
        if "PersNr" not in snapshot or column is None:
            # Keep the original KPI fallback for legacy snapshot schemas.
            return pd.DataFrame({name: {"Köpfe": compute_headcount(snapshot.loc[
                snapshot["Organisationseinheit"].astype(str).eq(name)]), "MAK": raw_stock_mak(snapshot.loc[
                snapshot["Organisationseinheit"].astype(str).eq(name)])} for name in names}).T
        occupied = snapshot.loc[snapshot["PersNr"].notna() &
                                (~snapshot["Is_Vacant"].fillna(False).astype(bool) if "Is_Vacant" in snapshot else True),
                                ["Organisationseinheit", "PersNr", column]].copy()
        occupied = occupied.loc[occupied["Organisationseinheit"].notna()]
        occupied["Organisationseinheit"] = occupied["Organisationseinheit"].astype(str)
        occupied["_mak"] = pd.to_numeric(occupied[column], errors="coerce").fillna(0)
        grouped = occupied.groupby("Organisationseinheit", sort=False)
        return pd.DataFrame({"Köpfe": grouped["PersNr"].nunique(), "MAK": grouped["_mak"].sum()})

    before, after = totals(baseline), totals(final)
    rows = []
    for name in names:
        rows.append({"Ebene": "Organisationseinheit", "Organisation": name,
                     "Ausgangs-Köpfe": int(before.at[name, "Köpfe"]) if name in before.index else 0,
                     "Ausgangs-MAK": float(before.at[name, "MAK"]) if name in before.index else 0.0,
                     "Finale MAK": float(after.at[name, "MAK"]) if name in after.index else 0.0,
                     "Finale Köpfe": int(after.at[name, "Köpfe"]) if name in after.index else 0})
    return pd.DataFrame(rows)


def _cached_candidates(view, shifting: bool) -> pd.DataFrame:
    """Reuse eligibility on selector reruns, invalidating when saved inputs change."""
    key = (view.signature, view.instance, id(view.event_context), shifting,
           value_fingerprint(get_setting("exclusions", {})),
           value_fingerprint(get_setting("salary_automation", {})),
           value_fingerprint(st.session_state.get("tvoed_lookup", {})))
    cached = st.session_state.get("simulation_approval_candidates_cache")
    if isinstance(cached, dict) and cached.get("key") == key:
        return cached["value"]
    result = eligible_timing_shifts(view) if shifting else eligible_departures(view)
    st.session_state["simulation_approval_candidates_cache"] = {"key": key, "value": result}
    return result


def _selection_basket(state, view, draft=None) -> dict:
    key = (view.signature, view.instance)
    saved = state.get("simulation_approval_selection")
    if not isinstance(saved, dict) or saved.get("run") != key:
        saved = {"run": key, "retained": (), "shifts": {}, "reason": ""}
        if draft is not None and draft.signature == view.signature and draft.instance == view.instance:
            rule = draft.metadata.get("adjustment", {})
            saved["retained"] = tuple(rule.get("retained", rule.get("persons", ())
                                          if rule.get("kind") == "retain_departure" else ()))
            saved["shifts"] = {item["person"]: pd.Timestamp(item["new_date"]).year
                               for item in rule.get("shifts", [])}
            if rule.get("kind") == "shift_departure" and not saved["shifts"] and rule.get("new_date"):
                saved["shifts"] = {person: pd.Timestamp(rule["new_date"]).year
                                   for person in rule.get("persons", ())}
            saved["reason"] = rule.get("reason", "")
        state["simulation_approval_selection"] = saved
    return saved


def _clear_selection(state, view, run_id: str) -> None:
    state["simulation_approval_selection"] = {"run": (view.signature, view.instance),
                                              "retained": (), "shifts": {}, "reason": ""}
    for key in list(state):
        if key.startswith((f"approval_rows_{view.instance}_{run_id}_",
                           f"approval_shift_rows_{view.instance}_{run_id}_",
                           f"approval_shift_reason_{view.instance}_{run_id}_")):
            del state[key]


def _with_scope_selection(basket: dict, candidates: pd.DataFrame, organization: str | None,
                          shifting: bool, selected: list[str], shifts: dict, reason: str) -> dict:
    updated = {**basket, "retained": tuple(basket["retained"]), "shifts": dict(basket["shifts"])}
    if organization is None or candidates.empty:
        return updated
    scoped_ids = set(candidates.loc[candidates["Organisationseinheit"].eq(organization), "PersNr"].astype(str))
    if shifting:
        updated["shifts"] = {person: year for person, year in updated["shifts"].items() if person not in scoped_ids}
        updated["shifts"].update(shifts)
        updated["reason"] = reason.strip()
    else:
        updated["retained"] = tuple(sorted((set(updated["retained"]) - scoped_ids) | set(selected)))
    return updated


def _basket_matches_draft(basket: dict, draft) -> bool:
    if draft is None:
        return False
    rule = draft.metadata.get("adjustment", {})
    retained = set(rule.get("retained", ()))
    shifts = {item["person"]: pd.Timestamp(item["new_date"]).year for item in rule.get("shifts", [])}
    if rule.get("kind") == "retain_departure":
        retained = set(rule.get("persons", ()))
    elif rule.get("kind") == "shift_departure" and not shifts and rule.get("new_date"):
        shifts = {person: pd.Timestamp(rule["new_date"]).year for person in rule.get("persons", ())}
    return (bool(basket["retained"] or basket["shifts"])
            and set(basket["retained"]) == retained and basket["shifts"] == shifts
            and (not shifts or basket["reason"] == rule.get("reason", "")))


@st.fragment
def _render_selection_panel(original, draft, run_id: str) -> str | None:
    """Keep edits across mode/OE changes without rerunning the whole dashboard."""
    st.subheader("2. Änderung auswählen")
    basket = _selection_basket(st.session_state, original, draft)
    mode = st.radio("Art der Änderung", ("Abgang zurücknehmen", "Kündigung später ansetzen"),
                    horizontal=True, key=f"approval_mode_{original.instance}_{run_id}")
    shifting = mode == "Kündigung später ansetzen"
    candidates = _cached_candidates(original, shifting)
    selected_org = None
    selected = []
    shifts = {}
    reason = basket["reason"]
    if candidates.empty:
        zugaenge = original.event_context.get("accession_events")
        if shifting and original.event_summary.get("stock_reconciliation", {}).get("status") != "Abgestimmt":
            st.info("Terminverschiebungen sind nur bei abgestimmter OE-Bestandsüberleitung verfügbar.")
        elif isinstance(zugaenge, pd.DataFrame) and not zugaenge.empty:
            st.info("Diese Änderung ist für Läufe mit Zugängen noch nicht verfügbar. Der Originallauf kann freigegeben werden.")
        else:
            st.info("Für diesen Lauf gibt es keine eindeutig geeigneten Abgänge für diese Änderung.")
    else:
        selected_org = st.selectbox("Andere OE auswählen", sorted(candidates["Organisationseinheit"].unique()),
                                    key=f"approval_org_{mode}_{original.instance}_{run_id}")
        scoped = candidates.loc[candidates["Organisationseinheit"].eq(selected_org)].reset_index(drop=True)
        if shifting:
            editor = scoped.assign(**{"Bisheriges Jahr": pd.to_datetime(scoped["Ereignisdatum"]).dt.year,
                                      "Zulässige Jahre": scoped["Zulässige Jahre"].map(lambda years: ", ".join(map(str, years))),
                                      "Neues Jahr": scoped["PersNr"].map(basket["shifts"]).astype(float)})
            edited = st.data_editor(
                editor, hide_index=True, use_container_width=True, num_rows="fixed",
                disabled=[column for column in editor if column != "Neues Jahr"],
                column_order=["PersNr", "Ereignisdatum", "Bisheriges Jahr", "Personen-MAK in OE",
                              "Zulässige Jahre", "Neues Jahr"],
                column_config={"Neues Jahr": st.column_config.NumberColumn(
                    "Neues Jahr", help="Leer lassen, wenn dieser Abgang unverändert bleiben soll", step=1)},
                key=f"approval_shift_rows_{original.instance}_{run_id}_{selected_org}",
            )
            changed = edited.loc[edited["Neues Jahr"].notna()]
            shifts = dict(zip(changed["PersNr"].astype(str), changed["Neues Jahr"], strict=True))
            selected = list(shifts)
            st.caption("Das neue Datum ist jeweils das Ende des bisherigen Abgangsmonats im eingegebenen Jahr.")
            reason = st.text_input("Begründung der Terminverschiebung", value=basket["reason"],
                                   key=f"approval_shift_reason_{original.instance}_{run_id}_{selected_org}")
        else:
            editor = scoped.assign(Zurücknehmen=scoped["PersNr"].isin(basket["retained"]))
            edited = st.data_editor(editor, hide_index=True, use_container_width=True,
                                    disabled=list(scoped.columns), num_rows="fixed",
                                    column_order=["Zurücknehmen", *scoped.columns],
                                    column_config={"Zurücknehmen": st.column_config.CheckboxColumn(
                                        "Zurücknehmen", help="Diesen simulierten Abgang rückgängig machen")},
                                    key=f"approval_rows_{original.instance}_{run_id}_{selected_org}")
            selected = edited.loc[edited["Zurücknehmen"].fillna(False), "PersNr"].astype(str).tolist()
        basket = _with_scope_selection(basket, candidates, selected_org, shifting, selected, shifts, reason)
        st.session_state["simulation_approval_selection"] = basket
    st.info(f"Vorgemerkt: {len(basket['retained'])} Rücknahmen und {len(basket['shifts'])} Terminverschiebungen.")
    overlap = set(basket["retained"]) & set(basket["shifts"])
    if overlap:
        st.error("Diese Personen sind zugleich zur Rücknahme und Verschiebung markiert: " + ", ".join(sorted(overlap)))
    if draft is not None and not _basket_matches_draft(basket, draft):
        st.warning("Die Eingaben weichen vom geprüften Entwurf ab. Bitte alle Änderungen erneut prüfen.")
    if st.button("Alle Änderungen prüfen", key=f"approval_check_all_{original.instance}_{run_id}"):
        if overlap:
            st.error("Bitte die doppelt ausgewählten Personen zuerst bereinigen.")
        elif not basket["retained"] and not basket["shifts"]:
            st.warning("Bitte zuerst mindestens eine Änderung auswählen.")
        elif basket["shifts"] and len(basket["reason"]) < 5:
            st.warning("Bitte eine kurze Begründung für die Terminverschiebung eingeben.")
        elif not _basket_matches_draft(basket, draft):
            try:
                with st.spinner("Korrigierten Ergebnisstand und Bestandsprüfungen erstellen ..."):
                    result = derive_combined_adjustment(original, list(basket["retained"]),
                                                        basket["shifts"], basket["reason"])
                stage_adjustment(st.session_state, result)
                st.rerun(scope="app")
            except (ValueError, KeyError) as exc:
                st.error(str(exc))
    if draft is not None and st.button("Geprüften Entwurf verwerfen", key=f"approval_discard_{original.instance}_{run_id}"):
        clear_adjustment(st.session_state)
        _clear_selection(st.session_state, original, run_id)
        st.rerun(scope="app")
    return selected_org


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


def _annual_comparison(original, adjusted, candidates: pd.DataFrame | None = None) -> pd.DataFrame:
    parts = []
    persons = set(adjusted.metadata.get("adjustment", {}).get("persons", []))
    if candidates is None:
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

    view = active
    metadata = view.metadata
    result_id = hashlib.sha256(view.version.encode("utf-8")).hexdigest()[:12]
    st.caption(f"Aktuell angezeigte Ergebnisversion: {result_id}")

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
    st.subheader("1. Bestandsprüfung")
    if active.metadata.get("adjustment"):
        st.caption("Aktuell freigegebener justierter Stand. Eine neue Auswahl in Schritt 2 ersetzt die bisherige Justierung.")
    else:
        st.caption("Originallauf vor einer Rücknahme. Die Wirkung einer geprüften Korrektur erscheint in Schritt 3.")
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
        if st.toggle("Details der OE-Bestandsüberleitung anzeigen", key=f"approval_checks_{result_id}"):
            st.dataframe(_mark_zero_mak_rows(checks, zero_orgs), use_container_width=True, hide_index=True)
    annual = bridge.get("annual")
    if isinstance(annual, pd.DataFrame) and not annual.empty:
        if st.toggle("Jahresscheiben und Bestandsverlauf anzeigen", key=f"approval_annual_{result_id}"):
            st.dataframe(_mark_zero_mak_rows(annual, zero_orgs, target_date=metadata.get("target_date")),
                         use_container_width=True, hide_index=True)

    selected_org = _render_selection_panel(original, draft, run_id)
    basket = _selection_basket(st.session_state, original, draft)
    draft_rule = draft.metadata.get("adjustment", {}) if draft is not None else {}
    draft_matches = _basket_matches_draft(basket, draft)
    with st.form(key=f"approval_review_{original.instance}_{run_id}", border=False):
        st.subheader("3. Wirkung der Änderung prüfen")
        if draft_matches:
            retained_ids = draft_rule.get("retained", ())
            if draft_rule.get("kind") == "retain_departure":
                retained_ids = draft_rule.get("persons", ())
            shift_details = draft_rule.get("shifts") or ([
                {"person": person, "old_date": draft_rule.get("old_date"),
                 "new_date": draft_rule.get("new_date")} for person in draft_rule.get("persons", ())]
                if draft_rule.get("kind") == "shift_departure" else [])
            st.success(f"Gemeinsam geprüft: {len(retained_ids)} Rücknahmen und {len(shift_details)} Terminverschiebungen.")
            if retained_ids:
                st.caption("Zurückgenommen: " + ", ".join(retained_ids))
            if shift_details:
                st.dataframe(pd.DataFrame(shift_details)[["person", "old_date", "new_date"]].rename(columns={
                        "person": "Personalnummer", "old_date": "Bisheriges Datum", "new_date": "Neues Datum"}),
                    use_container_width=True, hide_index=True)
                st.caption(f"Begründung: {draft_rule['reason']}")
                st.caption("Für verschobene Abgänge sind Kosten der zusätzlichen Beschäftigungszeit in den Jahresscheiben nicht neu berechnet.")
            comparison = _annual_comparison(original, draft, _cached_candidates(original, False))
            if not comparison.empty:
                st.markdown(f"**Jahresscheiben für {selected_org}: Original und korrigierter Bestand**")
                _render_annual_change_chart(comparison, selected_org)
                if st.toggle("Jahreswerte und Differenzen als Tabelle anzeigen", key=f"approval_comparison_{draft.version}"):
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
            if st.form_submit_button("Justierung verwerfen"):
                clear_adjustment(st.session_state)
                _clear_selection(st.session_state, original, run_id)
                st.rerun()
        elif basket["retained"] or basket["shifts"]:
            st.info("Die vorgemerkten Änderungen sind noch nicht gemeinsam geprüft. Bitte 'Alle Änderungen prüfen' klicken.")
        else:
            st.caption("Keine Änderung ausgewählt. Der Originallauf kann unverändert freigegeben werden.")

        st.subheader("4. Ergebnisstand freigeben")
        st.caption("Der blaue Knopf gibt alle gemeinsam geprüften Änderungen frei. Der helle Knopf gibt den Originallauf unverändert frei.")
        review_status = draft.event_summary.get("stock_reconciliation", {}).get("status") if draft_matches else status
        if review_status == "Abweichung":
            st.error("Die OE-Bestandsüberleitung ist nicht abgestimmt. Dieser Ergebnisstand kann noch nicht freigegeben werden.")
        if review_status == "Nicht verfügbar":
            st.warning("Eine vollständige OE-Bestandsüberleitung ist für den Originallauf nicht verfügbar. Änderungen werden vor ihrer Freigabe gesondert geprüft.")
        original_status = original.event_summary.get("stock_reconciliation", {}).get("status")
        original_col, adjusted_col = st.columns(2)
        with original_col:
            approve_original_clicked = st.form_submit_button(
                "Lauf unverändert freigeben", type="secondary", use_container_width=True,
                disabled=original_status == "Abweichung" or (active.approved and active.version == original.version),
            )
        with adjusted_col:
            approve_adjustment_clicked = st.form_submit_button(
                "Lauf mit Änderungen freigeben", type="primary", use_container_width=True,
                disabled=not draft_matches or review_status != "Abgestimmt" or
                         (active.approved and active.version == draft.version),
            )
        if approve_adjustment_clicked:
            if not draft_matches or review_status != "Abgestimmt":
                st.error("Die aktuelle Auswahl weicht von der geprüften Justierung ab. Bitte erneut prüfen.")
            else:
                try:
                    approve_adjustment(st.session_state, approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                    st.rerun()
                except (ValueError, KeyError) as exc:
                    st.error(str(exc))
        if approve_original_clicked:
            try:
                approve_original(st.session_state, approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                _clear_selection(st.session_state, original, run_id)
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
                 "Art": ("Rücknahme und Terminverschiebung" if entry.get("adjustment", {}).get("kind") == "combined_departures"
                         else "Terminverschiebung" if entry.get("adjustment", {}).get("kind") == "shift_departure"
                         else "Rücknahme" if entry["kind"] == "adjusted" else "Original"),
                 "Betroffene Personen": ", ".join(entry.get("adjustment", {}).get("persons", [])) if entry.get("adjustment") else "–",
                 "Alter Termin": ", ".join(f"{item['person']}: {item['old_date']}" for item in entry.get("adjustment", {}).get("shifts", []))
                 or entry.get("adjustment", {}).get("old_date", "–"),
                 "Neuer Termin": ", ".join(f"{item['person']}: {item['new_date']}" for item in entry.get("adjustment", {}).get("shifts", []))
                 or entry.get("adjustment", {}).get("new_date", "–"),
                 "Begründung": entry.get("adjustment", {}).get("reason", "–")}
                for entry in history
            ]), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
