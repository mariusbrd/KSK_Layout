from __future__ import annotations

import ast
from copy import deepcopy
from datetime import date
from pathlib import Path
import sys
from typing import Any

import pandas as pd
import streamlit as st

BASE_PATH = Path(__file__).resolve().parent.parent
if str(BASE_PATH) not in sys.path:
    sys.path.append(str(BASE_PATH))

from abgaenge.params import build_params_from_ui
from components.ui_shell import render_context_box, render_page_header, render_section_intro
from config.settings import TARIFF_GROUPS
from kpi_reference import get_current_stichtag
from utils.matrix_helpers import migrate_to_percent, percent_to_weights
from utils.simulation_params import (
    get_simulation_params,
    save_simulation_params,
)


AGE_COHORTS = ["alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus"]
AGE_LABELS = {
    "alter_unter_30": "u30 (%)",
    "alter_30_45": "30-45 (%)",
    "alter_45_55": "45-55 (%)",
    "alter_55_plus": "ue55 (%)",
}


def _as_date(value: Any, fallback: date) -> date:
    if value is None:
        return fallback
    try:
        return pd.Timestamp(value).date()
    except Exception:
        return fallback


def _optional_text(label: str, value: Any, key: str, help: str | None = None) -> str | None:
    raw = st.text_input(label, value="" if value is None else str(value), key=key, help=help)
    return raw.strip() or None


def _matrix_flat_editor(
    *,
    label: str,
    dimension: str,
    matrix_weights: dict[str, Any],
    default_percent: float,
    key: str,
    disabled: bool = False,
) -> dict[str, float]:
    st.caption(label)
    matrix_pct = migrate_to_percent(matrix_weights or {})
    if not matrix_pct:
        matrix_pct = {"Default": default_percent}
    rows = [
        {dimension: str(name), "Wahrscheinlichkeit (%)": float(value)}
        for name, value in matrix_pct.items()
        if not isinstance(value, dict)
    ]
    if not rows:
        rows = [{dimension: "Default", "Wahrscheinlichkeit (%)": default_percent}]
    edited = st.data_editor(
        pd.DataFrame(rows),
        use_container_width=True,
        num_rows="dynamic",
        disabled=disabled,
        key=key,
        column_config={
            dimension: st.column_config.TextColumn(dimension, required=True),
            "Wahrscheinlichkeit (%)": st.column_config.NumberColumn(
                "Wahrscheinlichkeit (%)",
                min_value=0.0,
                max_value=100.0,
                step=0.5,
                format="%.1f",
                required=True,
            ),
        },
    )
    return {
        str(row[dimension]): float(row["Wahrscheinlichkeit (%)"])
        for _, row in edited.dropna(subset=[dimension]).iterrows()
    }


def _quit_matrix_editor(
    *,
    dimension: str,
    matrix_weights: dict[str, Any],
    default_percent: float,
    key: str,
    disabled: bool = False,
) -> dict[str, dict[str, float]]:
    st.caption(f"Matrix: {dimension} x Alterskohorte. Werte werden als Prozentangaben gepflegt.")
    matrix_pct = migrate_to_percent(matrix_weights or {})
    names = ["Default"]
    for cohort in AGE_COHORTS:
        for name in (matrix_pct.get(cohort) or {}).keys():
            if name not in names:
                names.append(name)
    rows = []
    for name in names:
        row = {dimension: name}
        for cohort in AGE_COHORTS:
            row[cohort] = float((matrix_pct.get(cohort) or {}).get(name, default_percent))
        rows.append(row)
    edited = st.data_editor(
        pd.DataFrame(rows),
        use_container_width=True,
        num_rows="dynamic",
        disabled=disabled,
        key=key,
        column_config={
            dimension: st.column_config.TextColumn(dimension, required=True),
            **{
                cohort: st.column_config.NumberColumn(
                    AGE_LABELS[cohort],
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    format="%.1f",
                    required=True,
                )
                for cohort in AGE_COHORTS
            },
        },
    )
    result = {cohort: {} for cohort in AGE_COHORTS}
    for _, row in edited.dropna(subset=[dimension]).iterrows():
        name = str(row[dimension])
        for cohort in AGE_COHORTS:
            result[cohort][name] = float(row[cohort])
    return result


def _distribution_editor(
    *,
    label: str,
    matrix_weights: dict[str, Any],
    dimension: str,
    key: str,
    disabled: bool = False,
) -> dict[str, float]:
    st.caption(label)
    matrix_pct = migrate_to_percent(matrix_weights or {})
    rows = [
        {dimension: str(name), "Anteil (%)": float(value)}
        for name, value in matrix_pct.items()
        if not isinstance(value, dict)
    ]
    if not rows:
        rows = [{dimension: "Default", "Anteil (%)": 100.0}]
    edited = st.data_editor(
        pd.DataFrame(rows),
        use_container_width=True,
        num_rows="dynamic",
        disabled=disabled,
        key=key,
        column_config={
            dimension: st.column_config.TextColumn(dimension, required=True),
            "Anteil (%)": st.column_config.NumberColumn(
                "Anteil (%)",
                min_value=0.0,
                max_value=100.0,
                step=0.5,
                format="%.1f",
                required=True,
            ),
        },
    )
    return {
        str(row[dimension]): float(row["Anteil (%)"])
        for _, row in edited.dropna(subset=[dimension]).iterrows()
    }


def _hire_distribution_editor(value: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows = value or [{"Jobfamily": "Default", "OE-Cluster": "Default", "Share %": 1.0}]
    edited = st.data_editor(
        pd.DataFrame(rows),
        use_container_width=True,
        num_rows="dynamic",
        key=key,
        column_config={
            "Jobfamily": st.column_config.TextColumn("Jobfamily"),
            "OE-Cluster": st.column_config.TextColumn("OE-Cluster"),
            "Share %": st.column_config.NumberColumn(
                "Anteil (0.0 - 1.0)",
                min_value=0.0,
                max_value=1.0,
                step=0.01,
                format="%.2f",
            ),
        },
    )
    return edited.to_dict("records")


def _render_abgaenge(group: dict[str, Any], key_prefix: str, *, include_ruhend: bool) -> dict[str, Any]:
    params = deepcopy(group)
    ui = deepcopy(params.pop("_ui", {}))
    base_date = get_current_stichtag().date()
    default_end = date(base_date.year + 2, base_date.month, base_date.day)

    c1, c2, c3, c4 = st.columns(4)
    ui["ist_stichtag"] = c1.date_input(
        "Ist-Stichtag",
        value=_as_date(ui.get("ist_stichtag"), base_date),
        key=f"{key_prefix}_abg_start",
    )
    ui["forecast_end_date"] = c2.date_input(
        "Forecast-Ende",
        value=_as_date(ui.get("forecast_end_date"), default_end),
        key=f"{key_prefix}_abg_end",
    )
    freq_options = {"Monatlich": "M", "Quartalsweise": "Q"}
    ui["freq"] = freq_options[
        c3.selectbox(
            "Frequenz",
            options=list(freq_options.keys()),
            index=0 if ui.get("freq", "M") == "M" else 1,
            key=f"{key_prefix}_abg_freq",
        )
    ]
    random_seed = c4.number_input(
        "Random Seed",
        value=int(params.get("random_seed", 42)),
        step=1,
        key=f"{key_prefix}_abg_seed",
    )

    st.markdown("**Komponenten**")
    comp = params.get("components", {})
    cols = st.columns(4 if include_ruhend else 3)
    comp_atz = cols[0].checkbox("Altersteilzeit", value=bool(comp.get("atz", True)), key=f"{key_prefix}_abg_comp_atz")
    comp_ret = cols[1].checkbox("Rente", value=bool(comp.get("retirement", True)), key=f"{key_prefix}_abg_comp_ret")
    comp_quit = cols[2].checkbox("Kündigung", value=bool(comp.get("quit", True)), key=f"{key_prefix}_abg_comp_quit")
    comp_ruhend = bool(comp.get("ruhend", True))
    if include_ruhend:
        comp_ruhend = cols[3].checkbox("Ruhend", value=comp_ruhend, key=f"{key_prefix}_abg_comp_ruhend")

    with st.expander("ATZ-Parameter", expanded=False):
        atz = params.get("atz", {})
        a1, a2, a3, a4, a5 = st.columns(5)
        new_atz_rate = a1.slider("Neue Fälle (Basis)", 0.0, 0.5, float(atz.get("new_atz_rate", 0.05)), 0.005, format="%.3f", key=f"{key_prefix}_atz_rate")
        age_min = a2.number_input("Mindestalter", value=int(atz.get("atz_eligible_age_min", 55)), step=1, key=f"{key_prefix}_atz_age_min")
        age_max = a3.number_input("Höchstalter", value=int(atz.get("atz_eligible_age_max", 60)), step=1, key=f"{key_prefix}_atz_age_max")
        ar_years = a4.number_input("AR-Dauer (Jahre)", value=float(atz.get("atz_duration_ar_years", 2.5)), step=0.5, key=f"{key_prefix}_atz_ar")
        fr_years = a5.number_input("FR-Dauer (Jahre)", value=float(atz.get("atz_duration_fr_years", 2.5)), step=0.5, key=f"{key_prefix}_atz_fr")
        use_atz_matrix = False
        atz_dimension = atz.get("atz_dimension", "JobFamily")
        atz_matrix_pct = atz.get("atz_matrix", {})

    with st.expander("Renten-Parameter", expanded=False):
        retirement = params.get("retirement", {})
        r1, r2 = st.columns(2)
        rent65 = r1.slider("Renteneintritt 65+", 0.0, 1.0, float(retirement.get("rent_rate_65", 0.9)), 0.05, key=f"{key_prefix}_rent65")
        rent60 = r2.slider("Frühverrentung 60-64", 0.0, 1.0, float(retirement.get("rent_rate_60_65", 0.1)), 0.05, key=f"{key_prefix}_rent60")

    with st.expander("Kündigungs-Parameter", expanded=False):
        quit_params = params.get("quit", {})
        q1, q2, q3 = st.columns([3, 3, 2])
        quit_base = q1.slider("Basisrate p.a.", 0.0, 0.5, float(quit_params.get("quit_rate_base", 0.05)), 0.01, key=f"{key_prefix}_quit_base")
        use_quit_matrix = q2.checkbox("Detaillierte Kündigungsmatrix verwenden", value=bool(quit_params.get("use_quit_matrix", True)), key=f"{key_prefix}_quit_use_matrix")
        quit_dimension = q3.radio("Dimension", ["JobFamily", "OrgUnit"], index=0 if quit_params.get("quit_dimension", "JobFamily") == "JobFamily" else 1, horizontal=True, key=f"{key_prefix}_quit_dimension")
        quit_matrix_pct = _quit_matrix_editor(
            dimension=quit_dimension,
            matrix_weights=quit_params.get("quit_matrix", {}),
            default_percent=float(quit_base) * 100.0,
            key=f"{key_prefix}_quit_matrix",
            disabled=not use_quit_matrix,
        )
        st.caption("Optionale Zuordnung von Jobfamilien zu Jahren mit höherer oder niedrigerer Kündigungsannahme.")
        adjustments = quit_params.get("quit_adjustments", {"more": {}, "less": {}})
        adj_more = st.text_area("Mehr Kündigungen", value=str(adjustments.get("more", {})), key=f"{key_prefix}_quit_more")
        adj_less = st.text_area("Weniger Kündigungen", value=str(adjustments.get("less", {})), key=f"{key_prefix}_quit_less")
        quit_adjustments = {
            "more": adjustments.get("more", {}),
            "less": adjustments.get("less", {}),
        }
        try:
            quit_adjustments["more"] = ast.literal_eval(adj_more) if adj_more.strip() else {}
            quit_adjustments["less"] = ast.literal_eval(adj_less) if adj_less.strip() else {}
        except Exception:
            st.warning("Anpassungen konnten nicht gelesen werden; bisherige Struktur bleibt erhalten.")

    ruhend = params.get("ruhend", {})
    if include_ruhend:
        with st.expander("Ruhend-Parameter", expanded=False):
            h1, h2, h3 = st.columns(3)
            ruhend_new = h1.number_input("Neue Fälle / Jahr", value=int(ruhend.get("ruhend_new_cases_per_year", 0)), step=1, key=f"{key_prefix}_ruh_new")
            ruhend_return = h2.slider("Rückkehrquote p.a.", 0.0, 1.0, float(ruhend.get("ruhend_return_rate", 0.95)), 0.05, key=f"{key_prefix}_ruh_return")
            ruhend_duration = h3.number_input("Durchschnittliche Dauer (Monate)", value=int(ruhend.get("ruhend_avg_duration_months", 12)), step=1, key=f"{key_prefix}_ruh_duration")
    else:
        ruhend_new = int(ruhend.get("ruhend_new_cases_per_year", 0))
        ruhend_return = float(ruhend.get("ruhend_return_rate", 0.95))
        ruhend_duration = int(ruhend.get("ruhend_avg_duration_months", 12))

    ui_state = {
        "components": {"atz": comp_atz, "retirement": comp_ret, "quit": comp_quit, "ruhend": comp_ruhend},
        "atz": {
            "new_atz_rate": new_atz_rate,
            "use_atz_matrix": use_atz_matrix,
            "atz_dimension": atz_dimension,
            "atz_matrix": atz_matrix_pct,
            "atz_eligible_age_min": age_min,
            "atz_eligible_age_max": age_max,
            "atz_duration_ar_years": ar_years,
            "atz_duration_fr_years": fr_years,
        },
        "retirement": {"rent_rate_65": rent65, "rent_rate_60_65": rent60},
        "quit": {
            "quit_rate_base": quit_base,
            "use_quit_matrix": use_quit_matrix,
            "quit_dimension": quit_dimension,
            "quit_matrix": quit_matrix_pct,
            "quit_adjustments": quit_adjustments,
        },
        "ruhend": {
            "ruhend_new_cases_per_year": ruhend_new,
            "ruhend_return_rate": ruhend_return,
            "ruhend_avg_duration_months": ruhend_duration,
        },
        "random_seed": random_seed,
    }
    result = build_params_from_ui(ui_state)
    result["_ui"] = ui
    return result


def _render_zugaenge(group: dict[str, Any], key_prefix: str, *, hybrid: bool = False) -> dict[str, Any]:
    params = deepcopy(group)
    ui = deepcopy(params.pop("_ui", {}))
    base_date = get_current_stichtag().date()
    default_end = date(base_date.year + (2 if hybrid else 3), base_date.month, base_date.day)

    d1, d2, d3 = st.columns(3)
    ui["start_date"] = d1.date_input("Startdatum", value=_as_date(ui.get("start_date"), base_date), key=f"{key_prefix}_zug_start")
    ui["end_date"] = d2.date_input("Enddatum", value=_as_date(ui.get("end_date"), default_end), key=f"{key_prefix}_zug_end")
    random_seed = d3.number_input("Random Seed", value=int(params.get("random_seed", 42)), step=1, key=f"{key_prefix}_zug_seed")

    comp_cols = st.columns(3)
    use_azubis = comp_cols[0].checkbox("Azubis", value=bool(params.get("azubi", {}).get("active", ui.get("use_azubis", True))), key=f"{key_prefix}_zug_use_azubi")
    use_trainees = comp_cols[1].checkbox("Trainees", value=bool(params.get("trainee", {}).get("active", ui.get("use_trainees", True))), key=f"{key_prefix}_zug_use_trainee")
    use_newhires = comp_cols[2].checkbox("Neueinstellungen", value=bool(params.get("new_hires", {}).get("active", ui.get("use_newhires", True))), key=f"{key_prefix}_zug_use_hires")
    ui.update({"use_azubis": use_azubis, "use_trainees": use_trainees, "use_newhires": use_newhires})

    with st.expander("Azubi-Parameter", expanded=False):
        az = params.get("azubi", {})
        a1, a2, a3, a4 = st.columns(4)
        azubi_count = a1.number_input("Neue Azubis pro Jahr", 0, 100, int(az.get("new_cases_per_year", 15)), key=f"{key_prefix}_az_count")
        retention = a2.slider("Azubi-Übernahme", 0.0, 1.0, float(az.get("retention_rate", 1.0)), 0.05, key=f"{key_prefix}_az_retention")
        duration = a3.number_input("Ausbildungsdauer (Jahre)", 1.0, 5.0, float(az.get("duration_years", 3.0)), 0.5, key=f"{key_prefix}_az_duration")
        strategy_options = ["Random", "OrgUnit"]
        az_strategy = a4.selectbox("Azubi-Verteilung", strategy_options, index=strategy_options.index(az.get("strategy", "Random")) if az.get("strategy", "Random") in strategy_options else 0, key=f"{key_prefix}_az_strategy")
        target_org = _optional_text("Ziel-Einheit (Azubi)", az.get("target_org_unit"), f"{key_prefix}_az_target")
        s1, s2 = st.columns(2)
        tariff = s1.selectbox("Übernahme-Tarif", TARIFF_GROUPS, index=TARIFF_GROUPS.index(az.get("entry_tariff_group", "E5")) if az.get("entry_tariff_group", "E5") in TARIFF_GROUPS else 0, key=f"{key_prefix}_az_tariff")
        step = s2.number_input("Übernahme-Stufe", 1, 6, int(az.get("entry_step", 1)), key=f"{key_prefix}_az_step")
        g1, g2 = st.columns(2)
        graduation_mode = g1.radio("Abschlussmodus", ["nearest_cycle", "next_cycle"], index=0 if az.get("graduation_mode", "nearest_cycle") == "nearest_cycle" else 1, horizontal=True, key=f"{key_prefix}_az_grad")
        g2.caption("Der Abschlussmodus entspricht der bestehenden Zugänge-Seite.")
        m1, m2 = st.columns(2)
        use_matrix = m1.checkbox("Detailmatrix statt pauschaler Verteilung verwenden", value=bool(az.get("use_takeover_matrix", False)), key=f"{key_prefix}_az_use_matrix")
        takeover_dimension = m2.radio("Dimension für Übernahme", ["JobFamily", "OrgUnit"], index=0 if az.get("takeover_dimension", "JobFamily") == "JobFamily" else 1, horizontal=True, key=f"{key_prefix}_az_dimension")
        takeover_matrix_pct = _distribution_editor(
            label="Übernahme-Matrix. Werte werden als Prozentangaben gepflegt.",
            matrix_weights=az.get("takeover_matrix", {}),
            dimension=takeover_dimension,
            key=f"{key_prefix}_az_matrix",
            disabled=not use_matrix,
        )

    with st.expander("Trainee-Parameter", expanded=False):
        tr = params.get("trainee", {})
        t1, t2, t3, t4 = st.columns(4)
        trainee_count = t1.number_input("Neue Trainees pro Jahr", 0, 100, int(tr.get("new_cases_per_year", 5)), key=f"{key_prefix}_tr_count")
        trainee_dur = t2.number_input("Dauer (Jahre)", 0.5, 3.0, float(tr.get("duration_years", 1.5)), 0.5, key=f"{key_prefix}_tr_duration")
        salary_group = t3.selectbox("Einstiegsgehalt", TARIFF_GROUPS, index=TARIFF_GROUPS.index(tr.get("salary_group", "E13")) if tr.get("salary_group", "E13") in TARIFF_GROUPS else 0, key=f"{key_prefix}_tr_salary")
        tr_strategy = t4.selectbox("Verteilung", ["Random", "OrgUnit"], index=0 if tr.get("strategy", "Random") == "Random" else 1, key=f"{key_prefix}_tr_strategy")
        tr_target = _optional_text("Ziel-Einheit (Trainee)", tr.get("target_org_unit"), f"{key_prefix}_tr_target")

    with st.expander("Neueinstellungen", expanded=False):
        hires = params.get("new_hires", {})
        h1, h2 = st.columns(2)
        hire_count = h1.number_input("Einstellungen pro Jahr", 0, 500, int(hires.get("count_per_year", 10)), key=f"{key_prefix}_hire_count")
        hire_strat = h2.selectbox("Strategie", ["Random", "OrgUnit", "Fill Vacancies"], index=["Random", "OrgUnit", "Fill Vacancies"].index(hires.get("strategy", "Fill Vacancies")) if hires.get("strategy", "Fill Vacancies") in ["Random", "OrgUnit", "Fill Vacancies"] else 2, key=f"{key_prefix}_hire_strategy")
        hire_target = _optional_text("Ziel-Einheit (Hire)", hires.get("target_org_unit"), f"{key_prefix}_hire_target")
        distribution = _hire_distribution_editor(hires.get("distribution", []), f"{key_prefix}_hire_dist")

    result = {
        "azubi": {
            **params.get("azubi", {}),
            "active": use_azubis,
            "new_cases_per_year": azubi_count,
            "retention_rate": retention,
            "duration_years": duration,
            "strategy": az_strategy,
            "target_org_unit": target_org,
            "entry_tariff_group": tariff,
            "entry_step": step,
            "graduation_mode": graduation_mode,
            "nearest_cycle_grace_days": az.get("nearest_cycle_grace_days"),
            "exclude_baseline_azubis": az.get("exclude_baseline_azubis", False),
            "azubi_mak_during_training": az.get("azubi_mak_during_training", 0.0),
            "azubi_mak_after_takeover": az.get("azubi_mak_after_takeover", 1.0),
            "azubi_conversion_month": az.get("azubi_conversion_month", 8),
            "azubi_conversion_day": az.get("azubi_conversion_day", 1),
            "use_takeover_matrix": use_matrix,
            "takeover_dimension": takeover_dimension,
            "takeover_matrix": percent_to_weights(takeover_matrix_pct),
            "jf_to_cluster_map": az.get("jf_to_cluster_map", {}),
        },
        "trainee": {
            **params.get("trainee", {}),
            "active": use_trainees,
            "new_cases_per_year": trainee_count,
            "duration_years": trainee_dur,
            "salary_group": salary_group,
            "strategy": tr_strategy,
            "target_org_unit": tr_target,
        },
        "new_hires": {
            **params.get("new_hires", {}),
            "active": use_newhires,
            "count_per_year": hire_count,
            "strategy": hire_strat,
            "target_org_unit": hire_target,
        },
        "random_seed": random_seed,
        "_ui": ui,
    }
    result["new_hires"]["distribution"] = distribution
    if "available_org_units" in params:
        result["available_org_units"] = params.get("available_org_units", [])
    if "org_unit_to_cluster_map" in params:
        result["org_unit_to_cluster_map"] = params.get("org_unit_to_cluster_map", {})
    return result


def main() -> None:
    st.set_page_config(page_title="Simulationsparameter", page_icon="⚙️", layout="wide")
    current = get_simulation_params()
    draft = deepcopy(current)

    render_page_header(
        "Simulationsparameter",
        "Zentrale Annahmen für Prognoseabgänge und Prognosezugänge.",
    )
    render_context_box(
        "Integrationsstand",
        "Diese Parameter steuern die Annahmen für Abgänge und Zugänge. Die Hybrid-Prognose kombiniert diese Logiken. Die CompactPlus-Simulation nutzt die Abgänge- und Zugänge-Parameter, behält ihre eigene Simulationssteuerung jedoch auf der CompactPlus-Seite.",
        tone="neutral",
        compact=True,
    )

    tabs = st.tabs(["Prognose Abgänge"])

    with tabs[0]:
        render_section_intro("Prognose Abgänge", "Spiegelt die bestehende Abgänge-Seite; Ruhend bleibt hier wie dort fachlich deaktiviert.")
        draft["abgaenge"] = _render_abgaenge(draft["abgaenge"], "sim_params_abgaenge", include_ruhend=False)

    st.divider()
    c_save, c_note = st.columns([1, 3])
    if c_save.button("Parameter speichern", type="primary", use_container_width=True):
        save_simulation_params(draft)
        st.success("Parameter wurden aktualisiert.")
    c_note.caption("CompactPlus Simulation nutzt diese Abgänge- und Zugänge-Parameter bereits. Prognose Abgänge, Prognose Zugänge und Prognose Hybrid nutzen weiterhin ihre bisherigen Parameterquellen.")


if __name__ == "__main__":
    main()
