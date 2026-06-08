"""
Streamlit page: Kompakt plus Simulation.
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

BASE_PATH = Path(__file__).resolve().parents[1]
SRC_PATH = BASE_PATH / "src"
if SRC_PATH.exists():
    sys.path.append(str(SRC_PATH))
else:
    sys.path.append(str(BASE_PATH))

from components.sidebar import (
    filter_dataframe_by_view_filters,
    get_active_view_filters,
    get_filter_summary,
    get_global_metric_view,
    normalize_global_metric_view,
    render_global_filters,
    set_metric_page_hint,
)
from components.ui_compat import button_compat, download_button_compat
from components.ui_shell import render_active_filter_banner, render_context_box, render_section_intro
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.cluster_manager import load_cluster_mappings_from_source
from dataloader.cluster_resolver import deserialize_active_cluster_source, get_active_cluster_source
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from utils.compact_simulation_export import (
    EXCEL_MIME,
    build_compact_simulation_export_bytes,
)
from utils.status_quo_baseline import (
    build_forecast_vs_status_quo_jobfamily,
    build_status_quo_snapshot,
)
from utils.i18n import t
from utils.simulation_params import get_compact_plus_params
from utils.settings_loader import get_setting


def _get_atz_input():
    global_uploads = st.session_state.get("global_uploads", {})
    up_ma_arg = global_uploads.get("Mitarbeiter")
    up_atz_arg = global_uploads.get("ATZ")
    up_pl_arg = global_uploads.get("Planstellen")

    if up_ma_arg:
        up_ma_arg.seek(0)
    if up_atz_arg:
        up_atz_arg.seek(0)
    if up_pl_arg:
        up_pl_arg.seek(0)

    return load_atz_data_cached(str(BASE_PATH), up_ma_arg, up_atz_arg, up_pl_arg)


def _build_simulation_signature(
    *,
    target_date: pd.Timestamp,
    base_date: pd.Timestamp,
    abgaenge_params: dict,
    zugaenge_params: dict,
    cluster_source_signature: str | None,
) -> str:
    payload = {
        "target_date": str(pd.Timestamp(target_date).date()),
        "base_date": str(pd.Timestamp(base_date).date()),
        "abgaenge_params": abgaenge_params,
        "zugaenge_params": zugaenge_params,
        "exclusions": get_setting("exclusions", {}),
        "include_future_hires": get_setting("include_future_hires", False),
        "cluster_source_signature": cluster_source_signature,
    }
    return json.dumps(payload, sort_keys=True, default=str)


def _clear_simulation_cache():
    for key in [
        "compact_sim_signature",
        "compact_sim_cluster_source_signature",
        "compact_sim_prepared_df",
        "compact_sim_status_quo_df",
        "compact_sim_status_quo_signature",
        "compact_sim_metadata",
        "compact_sim_audit_tables",
        "compact_sim_target_date_cached",
        "compact_sim_abgaenge_params",
        "compact_sim_zugaenge_params",
        "compact_sim_active_cluster_source",
        "compact_sim_export_context",
        "compact_sim_export_signature",
        "compact_sim_export_bytes",
    ]:
        st.session_state.pop(key, None)


def _get_simulation_cluster_context(summary):
    summary = summary or {}
    active_cluster_source = deserialize_active_cluster_source(summary.get("active_cluster_source"))
    if active_cluster_source is None:
        active_cluster_source = get_active_cluster_source(session_state=st.session_state)

    cluster_source_signature = (
        summary.get("active_cluster_source_signature")
        or getattr(active_cluster_source, "source_signature", None)
    )
    cluster_mapping_bundle = load_cluster_mappings_from_source(active_cluster_source)
    return active_cluster_source, cluster_mapping_bundle, cluster_source_signature


def _segmented_single(label: str, options: list[str], value: str, key: str):
    if hasattr(st, "segmented_control"):
        widget_kwargs = {
            "key": key,
            "label_visibility": "collapsed",
        }
        if key not in st.session_state and value in options:
            widget_kwargs["default"] = value
        return st.segmented_control(
            label,
            options=options,
            **widget_kwargs,
        )

    radio_kwargs = {
        "horizontal": True,
        "key": key,
        "label_visibility": "collapsed",
    }
    if key not in st.session_state and value in options:
        radio_kwargs["index"] = options.index(value)
    return st.radio(
        label,
        options=options,
        **radio_kwargs,
    )


def _inject_page_styles():
    st.markdown(
        """
        <style>
        /* ── Minimal page header ─────────────────────────────────────── */
        .sim-header {
            margin: 0 0 18px 0;
            padding-bottom: 14px;
            border-bottom: 1px solid #e2e8f0;
        }
        .sim-header-title-row {
            display: flex;
            align-items: baseline;
            gap: 8px;
            margin-bottom: 3px;
        }
        .sim-header-icon {
            font-size: 1.25rem;
            line-height: 1;
            color: #f97316;
        }
        .sim-header-title {
            color: #0f172a;
            font-size: 1.35rem;
            font-weight: 700;
            line-height: 1.2;
        }
        .sim-header-sub {
            color: #64748b;
            font-size: 0.88rem;
            line-height: 1.4;
            margin: 3px 0 6px 0;
        }
        .sim-header-meta {
            color: #94a3b8;
            font-size: 0.82rem;
            line-height: 1.4;
        }
        .sim-header-meta .sim-meta-sep {
            margin: 0 5px;
            color: #cbd5e1;
        }
        .sim-header-meta .sim-meta-ok  { color: #16a34a; }
        .sim-header-meta .sim-meta-warn { color: #b45309; }
        .sim-header-logic {
            color: #b0bec5;
            font-size: 0.78rem;
            margin-top: 5px;
            font-style: italic;
        }
        .sim-header-source {
            color: #64748b;
            font-size: 0.8rem;
            margin-top: 3px;
        }

        /* ── Status boxes ────────────────────────────────────────────── */
        .sim-status {
            border-radius: 10px;
            padding: 11px 14px;
            margin: 2px 0 14px 0;
            border: 1px solid transparent;
            font-size: 0.9rem;
        }
        .sim-status.ok {
            background: #f0fdf4;
            border-color: #bbf7d0;
            color: #166534;
        }
        .sim-status.warn {
            background: #fffbeb;
            border-color: #fde68a;
            color: #92400e;
        }
        .sim-status strong {
            display: block;
            margin-bottom: 3px;
            font-size: 0.88rem;
        }

        /* ── Summary grid ────────────────────────────────────────────── */
        .sim-summary-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
            margin-top: 6px;
        }
        .sim-summary-card {
            background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
            border: 1px solid #dce8f5;
            border-radius: 12px;
            padding: 12px 14px;
        }
        .sim-summary-label {
            color: #64748b;
            font-size: 0.76rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }
        .sim-summary-value {
            color: #0f172a;
            font-size: 1.2rem;
            font-weight: 700;
            line-height: 1.2;
        }
        .sim-summary-note {
            color: #64748b;
            font-size: 0.82rem;
            margin-top: 4px;
        }
        @media (max-width: 980px) {
            .sim-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (max-width: 640px) {
            .sim-summary-grid { grid-template-columns: 1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_page_header(
    base_date: pd.Timestamp,
    target_date: pd.Timestamp,
    has_result: bool,
    is_stale: bool,
    metric_view: str,
    mode: str = "simulation",
) -> None:
    sep = '<span class="sim-meta-sep">·</span>'
    metric_esc = _html.escape(metric_view)

    if mode == "actual":
        if is_stale:
            status_token = '<span class="sim-meta-warn">⚠ Nicht aktuell</span>'
            meta_line = (
                f"Ist-Stand{sep}Basis {_html.escape(base_date.strftime('%d.%m.%Y'))}"
                f"{sep}{metric_esc}{sep}{status_token}"
            )
        elif has_result:
            meta_line = (
                f"Ist-Stand{sep}Basis {_html.escape(base_date.strftime('%d.%m.%Y'))}"
                f"{sep}{metric_esc}{sep}<span class=\"sim-meta-ok\">Aktiv</span>"
            )
        else:
            meta_line = (
                f"Ist-Stand{sep}Basis {_html.escape(base_date.strftime('%d.%m.%Y'))}"
                f"{sep}{metric_esc}{sep}Bereit"
            )
        logic_text = "Inputdatenbestand wird ohne Fortschreibung ausgewertet."
    else:
        if is_stale:
            status_token = '<span class="sim-meta-warn">⚠ Nicht aktuell</span>'
        elif has_result:
            status_token = '<span class="sim-meta-ok">Berechnet</span>'
        else:
            status_token = "Bereit"
        meta_line = (
            f"Simulation{sep}Basis {_html.escape(base_date.strftime('%d.%m.%Y'))}"
            f"{sep}Ziel {_html.escape(target_date.strftime('%d.%m.%Y'))}"
            f"{sep}{metric_esc}{sep}{status_token}"
        )
        logic_text = "Fortschreibung auf Basis bestehender Prognose-Logik."

    st.markdown(
        f"""
        <div class="sim-header">
          <div class="sim-header-title-row">
            <span class="sim-header-icon">⚡</span>
            <span class="sim-header-title">{_html.escape(t("sim.title"))}</span>
          </div>
          <div class="sim-header-sub">{_html.escape(t("sim.subtitle"))}</div>
          <div class="sim-header-meta">{meta_line}</div>
          <div class="sim-header-logic">{_html.escape(logic_text)}</div>
          <div class="sim-header-source">Parameterquelle: Simulationsparameter</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_status_box(is_stale: bool, target_date: pd.Timestamp, cached_target: pd.Timestamp | None):
    if is_stale:
        cached_label = cached_target.strftime("%d.%m.%Y") if cached_target is not None else "unbekannt"
        st.markdown(
            f"""
            <div class="sim-status warn">
                <strong>{t('sim.status.stale.title')}</strong>
                {t('sim.status.stale.text', cached_date=cached_label, target_date=target_date.strftime("%d.%m.%Y"))}
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="sim-status ok">
                <strong>Simulation aktiv</strong>
                Fortgeschriebener Bestand zum Ziel-Stichtag {target_date.strftime("%d.%m.%Y")}.
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_summary_cards(base_date: pd.Timestamp, display_target: pd.Timestamp, meta: dict, mode: str):
    delta_days = max(0, (display_target - base_date).days)
    if mode == "actual":
        date_label = "AUSWERTUNGSSTICHTAG"
        date_note = "Inputdatenbestand"
        horizon_note = "Keine Fortschreibung"
        events_note = "Keine Forecast-Ereignisse"
    else:
        date_label = t("sim.summary.target_date")
        date_note = t("sim.summary.target_note")
        horizon_note = t("sim.summary.horizon_note")
        events_note = t("sim.summary.events_note")
    st.markdown(
        f"""
        <div class="sim-summary-grid">
            <div class="sim-summary-card">
                <div class="sim-summary-label">{date_label}</div>
                <div class="sim-summary-value">{display_target.strftime("%d.%m.%Y")}</div>
                <div class="sim-summary-note">{date_note}</div>
            </div>
            <div class="sim-summary-card">
                <div class="sim-summary-label">{t('sim.summary.horizon')}</div>
                <div class="sim-summary-value">{t('sim.summary.horizon_value', days=delta_days)}</div>
                <div class="sim-summary-note">{horizon_note}</div>
            </div>
            <div class="sim-summary-card">
                <div class="sim-summary-label">{t('sim.summary.attrition')}</div>
                <div class="sim-summary-value">{meta.get('abgaenge_events', 0)}</div>
                <div class="sim-summary-note">{events_note}</div>
            </div>
            <div class="sim-summary-card">
                <div class="sim-summary-label">{t('sim.summary.hiring')}</div>
                <div class="sim-summary-value">{meta.get('zugaenge_events', 0)}</div>
                <div class="sim-summary-note">{events_note}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metric_totals(df: pd.DataFrame) -> dict[str, float]:
    active = df.copy()
    if "Is_Vacant" in active.columns:
        active = active[active["Is_Vacant"] != True].copy()
    heads = int(active["PersNr"].nunique()) if "PersNr" in active.columns else int(len(active))
    mak_col = "MAK_Reporting" if "MAK_Reporting" in active.columns else "MAK_Calculated"
    eur_col = "EUR_Reporting" if "EUR_Reporting" in active.columns else "Total_Cost_Year"
    return {
        "heads": heads,
        "mak": float(pd.to_numeric(active.get(mak_col, 0), errors="coerce").fillna(0).sum()),
        "eur": float(pd.to_numeric(active.get(eur_col, 0), errors="coerce").fillna(0).sum()),
    }


def _render_status_quo_comparison(status_quo_df: pd.DataFrame, forecast_df: pd.DataFrame, base_date: pd.Timestamp):
    status_totals = _metric_totals(status_quo_df)
    forecast_totals = _metric_totals(forecast_df)
    cols = st.columns(3)
    cols[0].metric("Köpfe", f"{forecast_totals['heads']:,.0f}".replace(",", "."), f"{forecast_totals['heads'] - status_totals['heads']:+,.0f}".replace(",", "."))
    cols[1].metric("MAK", f"{forecast_totals['mak']:,.1f}".replace(",", "X").replace(".", ",").replace("X", "."), f"{forecast_totals['mak'] - status_totals['mak']:+.1f}".replace(".", ","))
    cols[2].metric("EUR", f"{forecast_totals['eur']:,.0f} €".replace(",", "."), f"{forecast_totals['eur'] - status_totals['eur']:+,.0f} €".replace(",", "."))

    comparison = build_forecast_vs_status_quo_jobfamily(status_quo_df, forecast_df, base_date)
    if not comparison.empty:
        display_cols = [
            "Jobfamily",
            "Köpfe_StatusQuo",
            "Köpfe_Forecast",
            "Delta_Köpfe",
            "MAK_StatusQuo",
            "MAK_Forecast",
            "Delta_MAK",
            "Interpretation_Flag",
        ]
        st.dataframe(
            comparison[display_cols].sort_values("Delta_Köpfe", ascending=False),
            use_container_width=True,
            hide_index=True,
        )


def main():
    _inject_page_styles()

    compact = load_compact_page_module()
    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    abgaenge_params, zugaenge_params = get_compact_plus_params()

    default_target = st.session_state.get(
        "compact_sim_target_date",
        (base_date + pd.Timedelta(days=365)).date(),
    )

    # Mode reflects the last confirmed user action ("actual" or "simulation")
    mode = st.session_state.get("compact_plus_mode", "simulation")

    # Determine header state from session before rendering
    has_result = "compact_sim_prepared_df" in st.session_state
    cached_target_ts = st.session_state.get("compact_sim_target_date_cached")
    header_target = (
        pd.Timestamp(cached_target_ts).normalize()
        if cached_target_ts is not None
        else pd.Timestamp(default_target).normalize()
    )
    metric_view_header = normalize_global_metric_view(get_global_metric_view())

    # is_stale is not yet known (need signature); status section below handles it
    _render_page_header(
        base_date=base_date,
        target_date=header_target,
        has_result=has_result,
        is_stale=False,
        metric_view=metric_view_header,
        mode=mode,
    )

    # ── Controls section ──────────────────────────────────────────────────
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 1, 1])
    with ctrl_col1:
        target_date_input = st.date_input(
            t("sim.controls.date_label"),
            value=default_target,
            min_value=base_date.date(),
            key="compact_sim_target_date",
            help=t("sim.controls.date_caption"),
        )
    with ctrl_col2:
        st.markdown("<div style='padding-top:1.6rem'></div>", unsafe_allow_html=True)
        _ist_stand_clicked = st.button(
            "Ist-Stand auswerten",
            use_container_width=True,
            type="secondary",
            help="Zeigt den aktuellen Inputdatenbestand ohne Fortschreibung (Horizont = 0 Tage).",
        )
    with ctrl_col3:
        st.markdown("<div style='padding-top:1.6rem'></div>", unsafe_allow_html=True)
        sim_btn_label = (
            "Simulation aktualisieren"
            if has_result and mode == "simulation"
            else t("sim.controls.compute_button")
        )
        recalc_clicked = st.button(
            sim_btn_label,
            use_container_width=True,
            type="primary",
            help=t("sim.controls.compute_help"),
        )
    st.caption(
        "Ist-Stand = Basisdaten ohne Fortschreibung. "
        "Simulation = Fortschreibung bis zum gewählten Ziel-Datum."
    )

    # Commit mode from this run's button clicks (persists for next rerun)
    if _ist_stand_clicked:
        st.session_state["compact_plus_mode"] = "actual"
        mode = "actual"
    elif recalc_clicked:
        st.session_state["compact_plus_mode"] = "simulation"
        mode = "simulation"

    # Actual mode: target == base → engine short-circuits, produces 0 events automatically
    target_date = base_date if mode == "actual" else pd.Timestamp(target_date_input).normalize()
    is_stale = False

    try:
        snapshot_df, history_df, _, summary = load_and_prepare_data()
        (
            active_cluster_source,
            cluster_mapping_bundle,
            cluster_source_signature,
        ) = _get_simulation_cluster_context(summary)
        draft_signature = _build_simulation_signature(
            target_date=target_date,
            base_date=base_date,
            abgaenge_params=abgaenge_params,
            zugaenge_params=zugaenge_params,
            cluster_source_signature=cluster_source_signature,
        )

        computed_signature = st.session_state.get("compact_sim_signature")
        has_cached_result = (
            "compact_sim_prepared_df" in st.session_state and
            "compact_sim_metadata" in st.session_state
        )
        simulation_stale = has_cached_result and (computed_signature != draft_signature)
        needs_recompute = not has_cached_result

        if recalc_clicked or _ist_stand_clicked:
            _clear_simulation_cache()
            computed_signature = None
            simulation_stale = False
            needs_recompute = True

        just_computed = False
        if needs_recompute:
            df_atz = _get_atz_input()
            _progress_placeholder = st.empty()
            progress_init_text = (
                "Ist-Stand wird geladen ..."
                if mode == "actual"
                else "Simulation wird vorbereitet ..."
            )
            _progress_bar = _progress_placeholder.progress(0, text=progress_init_text)

            def _update_progress(_step: str, label: str, value: float) -> None:
                pct = max(0, min(100, int(round(value * 100))))
                _progress_bar.progress(pct, text=label)

            spinner_text = "Ist-Stand wird aufbereitet ..." if mode == "actual" else t("sim.controls.spinner")
            try:
                with st.spinner(spinner_text):
                    sim_result = simulate_compact_snapshot(
                        snapshot_df=snapshot_df,
                        df_atz=df_atz,
                        target_date=target_date,
                        base_date=base_date,
                        abgaenge_params=abgaenge_params,
                        zugaenge_params=zugaenge_params,
                        active_cluster_source=active_cluster_source,
                        cluster_mapping_bundle=cluster_mapping_bundle,
                        cluster_source_signature=cluster_source_signature,
                        progress_callback=_update_progress,
                    )

                _progress_bar.progress(97, text="Schritt 9/9: Kompaktansicht wird vorbereitet ...")
                prepared_df = compact.prepare_compact_data(sim_result.future_snapshot_df)
                status_quo_prepared_df = compact.prepare_compact_data(
                    build_status_quo_snapshot(snapshot_df, base_date)
                )
                computed_signature = draft_signature
                st.session_state["compact_sim_signature"] = computed_signature
                st.session_state["compact_sim_cluster_source_signature"] = cluster_source_signature
                st.session_state["compact_sim_prepared_df"] = prepared_df
                st.session_state["compact_sim_status_quo_df"] = status_quo_prepared_df
                st.session_state["compact_sim_status_quo_signature"] = computed_signature
                st.session_state["compact_sim_metadata"] = sim_result.metadata
                st.session_state["compact_sim_audit_tables"] = sim_result.audit_tables
                st.session_state["compact_sim_target_date_cached"] = target_date
                st.session_state["compact_sim_abgaenge_params"] = abgaenge_params
                st.session_state["compact_sim_zugaenge_params"] = zugaenge_params
                st.session_state["compact_sim_active_cluster_source"] = active_cluster_source
                st.session_state["compact_sim_export_context"] = {
                    "exclusions": get_setting("exclusions", {}),
                    "include_future_hires": get_setting("include_future_hires", False),
                    "salary_automation": get_setting("salary_automation", {}),
                }
                st.session_state.pop("compact_sim_export_signature", None)
                st.session_state.pop("compact_sim_export_bytes", None)
                simulation_stale = False
                just_computed = True
                _progress_bar.progress(100, text="Abgeschlossen.")
                _progress_placeholder.empty()
            except Exception:
                _progress_placeholder.empty()
                raise
        else:
            prepared_df = st.session_state["compact_sim_prepared_df"]
            status_quo_prepared_df = (
                st.session_state.get("compact_sim_status_quo_df")
                if st.session_state.get("compact_sim_status_quo_signature") == computed_signature
                else None
            )
            if status_quo_prepared_df is None:
                status_quo_prepared_df = compact.prepare_compact_data(
                    build_status_quo_snapshot(snapshot_df, base_date)
                )
                st.session_state["compact_sim_status_quo_df"] = status_quo_prepared_df
                st.session_state["compact_sim_status_quo_signature"] = computed_signature

        cached_target = st.session_state.get("compact_sim_target_date_cached")
        display_target = pd.Timestamp(cached_target).normalize() if cached_target is not None else target_date

        # ── Status section ────────────────────────────────────────────────
        if mode == "actual":
            render_section_intro(
                "Auswertungsstatus",
                "Aktualität des ausgewerteten Bestands und Kernergebnisse.",
            )
        else:
            render_section_intro(
                t("sim.status.section"),
                t("sim.status.subtitle"),
            )

        if mode == "actual":
            if just_computed:
                st.success(
                    f"Ist-Stand aktiv — Inputdatenbestand zum Basis-Stichtag **{display_target.strftime('%d.%m.%Y')}**."
                )
            else:
                st.markdown(
                    f"""
                    <div class="sim-status ok">
                        <strong>Ist-Stand aktiv</strong>
                        Inputdatenbestand zum Basis-Stichtag {display_target.strftime("%d.%m.%Y")}.
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            if just_computed:
                st.success(
                    f"Simulation erfolgreich berechnet — Bestand für **{display_target.strftime('%d.%m.%Y')}** ist aktuell."
                )
            else:
                _render_status_box(
                    is_stale=simulation_stale,
                    target_date=target_date,
                    cached_target=display_target if simulation_stale else None,
                )

        _render_summary_cards(
            base_date=base_date,
            display_target=display_target,
            meta=st.session_state.get("compact_sim_metadata", {}),
            mode=mode,
        )

        export_context = st.session_state.get("compact_sim_export_context") or {
            "exclusions": get_setting("exclusions", {}),
            "include_future_hires": get_setting("include_future_hires", False),
            "salary_automation": get_setting("salary_automation", {}),
        }
        export_metadata = {
            **st.session_state.get("compact_sim_metadata", {}),
            **export_context,
        }
        export_signature = computed_signature
        export_bytes = (
            st.session_state.get("compact_sim_export_bytes")
            if export_signature is not None
            and st.session_state.get("compact_sim_export_signature") == export_signature
            else None
        )
        if mode == "actual":
            export_prepare_label = "Excel-Export des Ist-Stands vorbereiten"
        elif simulation_stale:
            export_prepare_label = "Excel-Export der zuletzt berechneten Simulation vorbereiten"
        else:
            export_prepare_label = "Excel-Export der Simulation vorbereiten"
        if export_bytes is None and button_compat(
            export_prepare_label,
            key="compact_plus_simulation_prepare_export",
            use_container_width=True,
        ):
            with st.spinner("Excel-Export wird vorbereitet..."):
                export_bytes = build_compact_simulation_export_bytes(
                    prepared_df=prepared_df,
                    abgaenge_params=st.session_state.get("compact_sim_abgaenge_params", abgaenge_params),
                    zugaenge_params=st.session_state.get("compact_sim_zugaenge_params", zugaenge_params),
                    metadata=export_metadata,
                    active_cluster_source=st.session_state.get("compact_sim_active_cluster_source", active_cluster_source),
                    audit_tables=st.session_state.get("compact_sim_audit_tables", {}),
                    status_quo_df=status_quo_prepared_df,
                    status_quo_date=base_date,
                )
            st.session_state["compact_sim_export_signature"] = export_signature
            st.session_state["compact_sim_export_bytes"] = export_bytes

        if export_bytes is not None:
            if mode == "actual":
                export_download_label = "Ist-Stand als Excel exportieren"
                export_file_name = f"KompaktPlus_IstStand_{display_target:%Y%m%d}.xlsx"
            elif simulation_stale:
                export_download_label = "Zuletzt berechnete Simulation als Excel exportieren"
                export_file_name = f"KompaktPlus_Simulation_{display_target:%Y%m%d}.xlsx"
            else:
                export_download_label = "Simulationsergebnisse als Excel exportieren"
                export_file_name = f"KompaktPlus_Simulation_{display_target:%Y%m%d}.xlsx"
            download_button_compat(
                export_download_label,
                data=export_bytes,
                file_name=export_file_name,
                mime=EXCEL_MIME,
                key="compact_plus_simulation_export",
                use_container_width=True,
            )
        set_metric_page_hint(None)
        render_global_filters(prepared_df, history_df)

        filtered_df = filter_dataframe_by_view_filters(prepared_df, get_active_view_filters())
        filter_summary = get_filter_summary()
        render_active_filter_banner(filter_summary)

        if filtered_df.empty:
            st.warning(t("sim.warning.no_data"))
            return

        render_section_intro(
            t("sim.evaluation.section"),
            t("sim.evaluation.subtitle"),
        )

        metric_view = normalize_global_metric_view(get_global_metric_view())
        render_context_box(
            t("sim.metric_view.label"),
            t("sim.metric_view.text", metric_view=metric_view),
            tone="neutral",
            compact=True,
        )

        ist_tab, ist_soll_tab = st.tabs([
            t("sim.tabs.ist"),
            t("sim.tabs.ist_soll"),
        ])

        with ist_tab:
            if metric_view == "Köpfe":
                compact.render_ist_koepfe_tab(filtered_df)
            elif metric_view == "MAK":
                compact.render_ist_mak_tab(filtered_df)
            else:
                compact.render_ist_eur_tab(filtered_df)

        with ist_soll_tab:
            if metric_view == "Köpfe":
                compact.render_ist_soll_koepfe_tab(prepared_df)
            elif metric_view == "MAK":
                compact.render_ist_vs_soll_mak_tab(filtered_df)
            else:
                compact.render_ist_vs_soll_eur_tab(filtered_df)
        return

        if main_view == "IST-Analyse":
            ist_options = ["Köpfe", "MAK", "EUR"]
            if "compact_sim_ist_view" not in st.session_state:
                st.session_state["compact_sim_ist_view"] = ist_options[0]

            ist_view = _segmented_single(
                "IST-Unteransicht",
                options=ist_options,
                value=st.session_state["compact_sim_ist_view"],
                key="compact_sim_ist_view",
            )
            if ist_view == "Köpfe":
                compact.render_ist_koepfe_tab(filtered_df)
            elif ist_view == "MAK":
                compact.render_ist_mak_tab(filtered_df)
            else:
                compact.render_ist_eur_tab(filtered_df)
            return

        soll_options = ["Köpfe", "MAK", "EUR"]
        if "compact_sim_soll_view" not in st.session_state:
            st.session_state["compact_sim_soll_view"] = soll_options[0]

        soll_view = _segmented_single(
            "SOLL-Unteransicht",
            options=soll_options,
            value=st.session_state["compact_sim_soll_view"],
            key="compact_sim_soll_view",
        )
        if soll_view == "Köpfe":
            compact.render_ist_soll_koepfe_tab(prepared_df)
        elif soll_view == "MAK":
            compact.render_ist_vs_soll_mak_tab(filtered_df)
        else:
            compact.render_ist_vs_soll_eur_tab(filtered_df)

    except FileNotFoundError as exc:
        st.error(t("sim.error.data", error=exc))
    except Exception as exc:
        st.error(t("sim.error.general", error=exc))
        st.exception(exc)


if __name__ == "__main__":
    main()
