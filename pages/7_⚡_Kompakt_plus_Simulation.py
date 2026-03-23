"""
Streamlit page: Kompakt plus Simulation.
"""

from __future__ import annotations

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

from abgaenge.params import default_params as default_abgaenge_params
from components.sidebar import apply_filters, get_filter_summary, render_global_filters, get_global_metric_view, set_metric_page_hint
from components.ui_shell import render_active_filter_banner, render_context_box, render_section_intro
from dataloader.compact_simulation_engine import simulate_compact_snapshot
from dataloader.loader import load_and_prepare_data, load_atz_data_cached
from kpi_reference import get_current_stichtag
from utils.compact_page_loader import load_compact_page_module
from utils.settings_loader import get_setting
from zugaenge.params import default_params as default_zugaenge_params


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


def _build_upload_signature() -> dict:
    uploads = st.session_state.get("global_uploads", {})
    signature = {}
    for key, value in uploads.items():
        if value is None:
            continue
        signature[key] = {
            "name": getattr(value, "name", key),
            "size": getattr(value, "size", None),
        }
    return signature


def _build_simulation_signature(
    *,
    target_date: pd.Timestamp,
    base_date: pd.Timestamp,
    abgaenge_params: dict,
    zugaenge_params: dict,
) -> str:
    payload = {
        "target_date": str(pd.Timestamp(target_date).date()),
        "base_date": str(pd.Timestamp(base_date).date()),
        "abgaenge_params": abgaenge_params,
        "zugaenge_params": zugaenge_params,
        "exclusions": get_setting("exclusions", {}),
        "include_future_hires": get_setting("include_future_hires", False),
        "uploads": _build_upload_signature(),
    }
    return json.dumps(payload, sort_keys=True, default=str)


def _clear_simulation_cache():
    for key in [
        "compact_sim_signature",
        "compact_sim_prepared_df",
        "compact_sim_metadata",
        "compact_sim_target_date_cached",
    ]:
        st.session_state.pop(key, None)


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
        .sim-panel {
            background: #ffffff;
            border: 1px solid #dce8f5;
            border-radius: 14px;
            padding: 16px 18px;
            margin-bottom: 16px;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.05);
        }
        .sim-panel-title {
            color: #0f172a;
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 4px;
        }
        .sim-panel-subtitle {
            color: #64748b;
            font-size: 0.9rem;
            margin-bottom: 14px;
            line-height: 1.4;
        }
        .sim-control-card {
            background: #f8fbff;
            border: 1px solid #dce8f5;
            border-radius: 12px;
            padding: 14px;
            min-height: 112px;
        }
        .sim-label {
            color: #64748b;
            font-size: 0.76rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }
        .sim-value {
            color: #0f172a;
            font-size: 1.18rem;
            font-weight: 700;
            line-height: 1.2;
        }
        .sim-note {
            color: #64748b;
            font-size: 0.84rem;
            line-height: 1.35;
            margin-top: 6px;
        }
        .sim-status {
            border-radius: 14px;
            padding: 13px 14px;
            margin: 2px 0 14px 0;
            border: 1px solid transparent;
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
        }
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
        .sim-filter-box {
            background: #f8fbff;
            border: 1px solid #e2e8f0;
            border-radius: 14px;
            padding: 11px 14px;
            margin: 12px 0 18px 0;
        }
        .sim-filter-title {
            color: #475569;
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 3px;
        }
        .sim-filter-text {
            color: #0f172a;
            font-size: 0.92rem;
            line-height: 1.35;
        }
        .sim-view-note {
            color: #64748b;
            font-size: 0.9rem;
            line-height: 1.4;
            margin: 2px 0 14px 0;
        }
        @media (max-width: 980px) {
            .sim-summary-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 640px) {
            .sim-summary-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero():
    st.title("⚡ Kompakt plus Simulation")
    st.caption("Kompakt-Auswertung auf einem bis zum Zukunftsdatum fortgeschriebenen Personalbestand.")
    render_context_box(
        "Simulationslogik",
        "Fortschreibung des Personalbestands bis zu einem gewaehlten Zukunftsdatum auf Basis der bestehenden Prognose-Logik fuer Abgaenge und Zugaenge.",
        tone="info",
    )


def _render_info_card(label: str, value: str, note: str):
    st.markdown(
        f"""
        <div class="sim-control-card">
            <div class="sim-label">{label}</div>
            <div class="sim-value">{value}</div>
            <div class="sim-note">{note}</div>
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
                <strong>Simulation nicht aktuell</strong>
                Angezeigt wird noch der zuletzt berechnete Stand fuer <strong>{cached_label}</strong>.
                Mit <strong>Simulation berechnen</strong> aktualisieren Sie den Bestand fuer
                <strong>{target_date.strftime("%d.%m.%Y")}</strong>.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="sim-status ok">
                <strong>Simulation aktuell</strong>
                Der angezeigte Bestand ist fuer den Ziel-Stichtag
                <strong>{target_date.strftime("%d.%m.%Y")}</strong> berechnet.
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_summary_cards(base_date: pd.Timestamp, display_target: pd.Timestamp, meta: dict):
    delta_days = max(0, (display_target - base_date).days)
    st.markdown(
        f"""
        <div class="sim-summary-grid">
            <div class="sim-summary-card">
                <div class="sim-summary-label">Ziel-Stichtag</div>
                <div class="sim-summary-value">{display_target.strftime("%d.%m.%Y")}</div>
                <div class="sim-summary-note">Berechneter Zukunftsstand</div>
            </div>
            <div class="sim-summary-card">
                <div class="sim-summary-label">Horizont</div>
                <div class="sim-summary-value">{delta_days} Tage</div>
                <div class="sim-summary-note">Differenz zum Basis-Stichtag</div>
            </div>
            <div class="sim-summary-card">
                <div class="sim-summary-label">Abgaenge</div>
                <div class="sim-summary-value">{meta.get('abgaenge_events', 0)}</div>
                <div class="sim-summary-note">Erfasste Forecast-Ereignisse</div>
            </div>
            <div class="sim-summary-card">
                <div class="sim-summary-label">Zugaenge</div>
                <div class="sim-summary-value">{meta.get('zugaenge_events', 0)}</div>
                <div class="sim-summary-note">Erfasste Forecast-Ereignisse</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main():
    _inject_page_styles()
    _render_hero()

    compact = load_compact_page_module()
    base_date = pd.Timestamp(get_current_stichtag()).normalize()
    abgaenge_params = st.session_state.get("abgaenge_params", default_abgaenge_params())
    zugaenge_params = st.session_state.get("zugaenge_params", default_zugaenge_params())

    default_target = st.session_state.get(
        "compact_sim_target_date",
        (base_date + pd.Timedelta(days=365)).date(),
    )

    render_section_intro(
        "Simulation steuern",
        "Ziel-Datum waehlen, Bestand berechnen und anschliessend wie in der Kompaktseite auswerten.",
    )

    control_col1, control_col2, control_col3 = st.columns([2.2, 1.1, 1.15])
    with control_col1:
        st.markdown("**Simulationsdatum**")
        st.caption("Dieses Datum wird auf der Seite als zukuenftiger Stichtag behandelt.")
        target_date_input = st.date_input(
            "Simulationsdatum",
            value=default_target,
            min_value=base_date.date(),
            key="compact_sim_target_date",
            label_visibility="collapsed",
        )
    with control_col2:
        _render_info_card(
            "Basis-Stichtag",
            base_date.strftime("%d.%m.%Y"),
            "Ausgangspunkt der Fortschreibung",
        )
    with control_col3:
        st.markdown("**Berechnung**")
        st.caption("Neu rechnen nur bei Bedarf. Filterwechsel bleiben schnell.")
        recalc_clicked = st.button(
            "Simulation berechnen",
            use_container_width=True,
            type="primary",
            help="Berechnet den Zukunftsbestand nur bei Bedarf neu.",
        )

    target_date = pd.Timestamp(target_date_input).normalize()
    current_signature = _build_simulation_signature(
        target_date=target_date,
        base_date=base_date,
        abgaenge_params=abgaenge_params,
        zugaenge_params=zugaenge_params,
    )

    cached_signature = st.session_state.get("compact_sim_signature")
    has_cached_result = (
        "compact_sim_prepared_df" in st.session_state and
        "compact_sim_metadata" in st.session_state
    )
    needs_recompute = (not has_cached_result) or (cached_signature != current_signature)
    is_stale = False

    try:
        snapshot_df, history_df, _, _ = load_and_prepare_data()

        if recalc_clicked:
            _clear_simulation_cache()
            needs_recompute = True

        if needs_recompute:
            if has_cached_result and not recalc_clicked:
                is_stale = True
                prepared_df = st.session_state["compact_sim_prepared_df"]
            else:
                df_atz = _get_atz_input()
                with st.spinner("Simuliere zukuenftigen Personalbestand..."):
                    sim_result = simulate_compact_snapshot(
                        snapshot_df=snapshot_df,
                        df_atz=df_atz,
                        target_date=target_date,
                        base_date=base_date,
                        abgaenge_params=abgaenge_params,
                        zugaenge_params=zugaenge_params,
                    )

                prepared_df = compact.prepare_compact_data(sim_result.future_snapshot_df)
                st.session_state["compact_sim_signature"] = current_signature
                st.session_state["compact_sim_prepared_df"] = prepared_df
                st.session_state["compact_sim_metadata"] = sim_result.metadata
                st.session_state["compact_sim_target_date_cached"] = target_date
        else:
            prepared_df = st.session_state["compact_sim_prepared_df"]

        cached_target = st.session_state.get("compact_sim_target_date_cached")
        display_target = pd.Timestamp(cached_target).normalize() if cached_target is not None else target_date

        render_section_intro(
            "Simulationsstatus",
            "Aktualitaet der Berechnung und Kernergebnisse des fortgeschriebenen Bestands.",
        )
        _render_status_box(
            is_stale=is_stale,
            target_date=target_date,
            cached_target=display_target if is_stale else None,
        )
        _render_summary_cards(
            base_date=base_date,
            display_target=display_target,
            meta=st.session_state.get("compact_sim_metadata", {}),
        )
        set_metric_page_hint(None)
        render_global_filters(prepared_df, history_df)

        filtered_df = apply_filters(prepared_df)
        filter_summary = get_filter_summary()
        render_active_filter_banner(filter_summary)

        if filtered_df.empty:
            st.warning("Keine Daten fuer die gewaehlten Filter.")
            return

        render_section_intro(
            "Auswertung",
            "Zwischen Analysebereich und Kennzahlensicht wechseln. Die Auswertungen darunter reagieren auf die aktiven Sichtfilter.",
        )

        metric_view = get_global_metric_view()
        render_context_box(
            "Kennzahlensicht",
            f"Steuerung ueber die Sidebar: {metric_view}",
            tone="neutral",
            compact=True,
        )

        ist_tab, ist_soll_tab = st.tabs([
            "IST-Analyse",
            "IST vs SOLL",
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
        st.error(f"Datenfehler: {exc}")
    except Exception as exc:
        st.error(f"Fehler in Kompakt plus Simulation: {exc}")
        st.exception(exc)


if __name__ == "__main__":
    main()
