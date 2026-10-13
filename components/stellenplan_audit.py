"""Read-only input reconciliation for the settings page."""
import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dataloader.stellenplan_audit import build_stellenplan_audit, stellenplan_matrix


@st.cache_data(show_spinner=False)
def _audit_from_bytes(ma_bytes: bytes, pl_bytes: bytes):
    return build_stellenplan_audit(pd.read_excel(io.BytesIO(ma_bytes)), pd.read_excel(io.BytesIO(pl_bytes)))


def _waterfall(labels, values, title, unit):
    fig = go.Figure(go.Waterfall(
        x=labels, y=values,
        measure=["absolute"] + ["relative"] * (len(values) - 2) + ["total"],
        text=[str(abs(int(value))) for value in values], textposition="outside",
        decreasing={"marker": {"color": "#D68A64"}},
        totals={"marker": {"color": "#004B6B"}},
        increasing={"marker": {"color": "#639FAD"}},
        connector={"line": {"color": "#CCD8DE"}},
        hovertemplate="%{x}: %{text}<extra></extra>",
    ))
    fig.update_layout(title=title, yaxis_title=unit, showlegend=False,
                      height=430, margin=dict(t=65, b=100, l=35, r=25))
    return fig


def render_stellenplan_audit(ma_bytes: bytes, pl_bytes: bytes):
    try:
        audit = _audit_from_bytes(ma_bytes, pl_bytes)
    except (ValueError, KeyError) as exc:
        st.warning(f"Stellenplan-Auswertung nicht verfügbar: {exc}")
        return
    positions = audit["positions"]
    counts = positions["Zuordnung"].value_counts()
    st.caption("Basis: hochgeladene Originaldateien, vor Stichtagsprüfung, Statuskorrekturen und "
               "Gruppenausschlüssen. Diese Prüfung verändert keine Dashboard-Daten.")
    overview, reconciliation, details = st.tabs([
        "Stellenplan", "Zuordnung nachvollziehen", "Details untersuchen"])
    with overview:
        cols = st.columns(3)
        cols[0].metric("Planstellenzeilen", len(positions))
        cols[1].metric("Eindeutig besetzt", int(counts.get("Eindeutig besetzt", 0)))
        cols[2].metric("Unbesetzt", int(counts.get("Unbesetzt", 0)))
        st.dataframe(stellenplan_matrix(positions), hide_index=True, use_container_width=True)
        st.caption("Je Zelle: Anzahl Planstellenzeilen · Ist-MAK (39h). Stellenarten nach Sollarbeitszeit: "
                   "39h = Vollzeit, mehr als 0 und weniger als 39h = Teilzeit, 0h = keine Soll-Kapazität. "
                   "Andere oder fehlende Sollstunden werden separat ausgewiesen.")
        with st.expander("Wie werden Köpfe und MAK gezählt?"):
            st.write("Eine Person kann mehrere Planstellen besetzen. Deshalb sind besetzte Stellen und "
                     "eindeutige Personen unterschiedliche Größen. Ist-MAK = Beschäftigungsgrad (BsGrd) / 100 "
                     "auf 39-Stunden-Basis. Bei mehreren Stellen wird die Personenkapazität mit der bestehenden "
                     "MAK-Verteilung nach Sollstunden aufgeteilt, bei ausschließlich 0 Sollstunden gleichmäßig. "
                     "Unbesetzte Stellen tragen 0 Ist-MAK bei. Fehlende oder mehrdeutige Mitarbeiterzuordnungen "
                     "und ungültige Beschäftigungsgrade bleiben als ungeklärte MAK sichtbar. "
                     "Die Summen enthalten nur bekannte MAK; sie sind keine Soll-Kapazität.")
        issues = positions["Planstellennummer fehlt"] | positions["Planstellennummer mehrfach"]
        if issues.any():
            st.warning(f"{int(issues.sum())} Zeilen mit fehlender oder mehrfacher Planstellennummer. "
                       "Die Übersicht zählt Eingabezeilen; die Zahl eindeutiger Stellen muss geprüft werden.")
        if positions["MAK ungeklärt"].any():
            st.warning("Die MAK-Summen sind unvollständig. Ungeklärte Kapazitäten sind in der Matrix markiert.")
    with reconciliation:
        labels = ["Planstellen gesamt", "Unbesetzt", "Mitarbeiter fehlt", "Mehrdeutig", "Eindeutig besetzt"]
        values = [len(positions), -counts.get("Unbesetzt", 0),
                  -counts.get("Mitarbeiterdatensatz fehlt", 0),
                  -counts.get("Mitarbeiterzuordnung mehrdeutig", 0), counts.get("Eindeutig besetzt", 0)]
        st.plotly_chart(_waterfall(labels, values, "Von Planstellen zur eindeutigen Besetzung", "Planstellenzeilen"),
                        use_container_width=True, key="integrity_positions_waterfall")
        people = audit["employee_counts"]
        labels = list(people)
        values = [value if i in (0, len(people) - 1) else -value for i, value in enumerate(people.values())]
        st.plotly_chart(_waterfall(labels, values, "Von Mitarbeiterzeilen zu zugeordneten Personen", "Zeilen / eindeutige Personen"),
                        use_container_width=True, key="integrity_people_waterfall")
        st.info("Personen ohne Planstellenzuordnung können beim Zusammenführen aus dem Dashboard fallen. "
                "Die Wasserfälle enden bei der Zuordnung der Originaldaten. Anschließende Stichtags-, Status- "
                "und Gruppenausschlüsse sind in diesem ersten Entwurf noch nicht Teil des Wasserfalls.")
    with details:
        st.markdown("**Planstellen prüfen**")
        selected = positions.copy()
        filter_columns = [c for c in ["Kürzel OrgEinheit", "Organisationseinheit", "OrgEinheit", "OE", "MA-Typ", "Zuordnung"] if c in selected]
        for column in filter_columns:
            choices = sorted(positions[column].dropna().astype(str).unique())
            chosen = st.multiselect(column, choices, key=f"integrity_filter_{column}")
            if chosen:
                selected = selected[selected[column].astype(str).isin(chosen)]
        st.caption(f"{len(selected)} Planstellenzeilen · {selected.loc[selected['Zuordnung'].eq('Eindeutig besetzt'), 'Personalnummer'].nunique()} "
                   "eindeutig zugeordnete Personen. Leere Filter zeigen alle Zeilen.")
        st.dataframe(selected, hide_index=True, use_container_width=True)
        unassigned = audit["unassigned_employees"]
        with st.expander(f"Personen ohne Planstellenzuordnung ({unassigned['PersNr'].nunique()})", expanded=not unassigned.empty):
            st.caption("Diese Personalnummern kommen in der Mitarbeiterdatei vor, aber in keiner Planstellenzeile.")
            if "MitarbGruppenbez." in unassigned and not unassigned.empty:
                grouped = unassigned.groupby("MitarbGruppenbez.", dropna=False)["PersNr"].nunique().rename("Köpfe").reset_index()
                st.dataframe(grouped, hide_index=True, use_container_width=True)
            st.dataframe(unassigned, hide_index=True, use_container_width=True)
        with st.expander("Weitere Datenprobleme"):
            problems = positions[positions["MAK ungeklärt"] | positions["Planstellennummer fehlt"] |
                                 positions["Planstellennummer mehrfach"] |
                                 positions["Stellenart"].eq("Sollstunden unklar / abweichend")]
            st.write("Auffällige Planstellenzeilen")
            st.dataframe(problems, hide_index=True, use_container_width=True)
            st.write("Mehrfache Personalnummern in der Mitarbeiterdatei")
            st.dataframe(audit["ambiguous_employees"], hide_index=True, use_container_width=True)
            st.write("Mitarbeiterzeilen ohne Personalnummer")
            st.dataframe(audit["employees"][audit["employees"]["PersNr"].isna()], hide_index=True, use_container_width=True)
