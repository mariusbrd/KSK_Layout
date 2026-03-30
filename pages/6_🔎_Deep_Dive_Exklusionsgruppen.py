"""
Deep Dive: Exklusionsgruppen

Kombinierte Transparenz- und Steuerungsseite.
Zeigt alle exkludierbaren Personengruppen mit Kennzahlen und ermöglicht
deren Aktivierung/Deaktivierung direkt im Kontext der Datenlage.

Keine Änderung der Exclusions-Engine – nur UI-Migration aus Einstellungen.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataloader.loader import load_and_prepare_data
from components.sidebar import render_global_filters, apply_filters, set_metric_page_hint
from config.settings import COLORS, EXCLUSION_ORG_UNITS
from utils.exclusion_groups import (
    build_group_masks,
    get_all_group_stats,
    get_group_detail,
    resolve_mak_series,
    VORSTAND_KEY,
    RUHEND_KEY,
    PA_GROUPS,
)
from dataloader.kpi_engine import get_unique_employees
from utils.settings_loader import get_setting, set_setting
from utils.plot_helpers import apply_legend_bottom
from utils.i18n import t


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _metric_card(label: str, value: str, sub: str = "", color: str = "#0088DE"):
    st.markdown(
        f"""
        <div style="background:linear-gradient(180deg,#f8fbff 0%,#ffffff 100%);
                    border-radius:14px;padding:14px 16px;border:1px solid #dce8f5;
                    border-left:4px solid {color};margin-bottom:8px;box-shadow:0 6px 18px rgba(15,23,42,0.05);">
            <div style="font-size:0.74rem;color:#64748b;margin-bottom:4px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;">{label}</div>
            <div style="font-size:1.55rem;font-weight:700;color:#0f172a;line-height:1.15;">{value}</div>
            {"<div style='font-size:0.82rem;color:#64748b;margin-top:4px;line-height:1.35;'>" + sub + "</div>" if sub else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _fmt_mak(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_int(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def _fmt_pct(v: float) -> str:
    return f"{v:.1f} %"


def _persist_exclusions(vorstand: bool, ruhend_bv: bool, org_units: list, planstellen_follow_person: bool = False):
    """Speichert Exklusions-Einstellungen in Settings und Session State."""
    # dict.fromkeys: dedupliziert und erhält Reihenfolge (verhindert T3c-Inkonsistenz)
    org_units_clean = list(dict.fromkeys(org_units))
    updated = {
        "vorstand": vorstand,
        "ruhend_bv": ruhend_bv,
        "planstellen_follow_person": planstellen_follow_person,
        "org_units": org_units_clean,
    }
    set_setting("exclusions", updated)
    st.session_state["exclude_vorstand"] = vorstand
    st.session_state["exclude_ruhend"] = ruhend_bv
    st.session_state["exclude_org_units"] = org_units_clean
    # Cache leeren: prepare_compact_data() ist @st.cache_data und würde sonst veraltete
    # Exklusions-Stände zurückliefern, da apply_exclusions() in load_and_prepare_data()
    # neue Settings erst nach Cache-Invalidierung wirksam werden.
    st.cache_data.clear()


def _load_current_exclusions() -> dict:
    all_pa_codes = [code for code, _ in PA_GROUPS]
    return get_setting("exclusions", {
        "vorstand": True,
        "ruhend_bv": True,
        "org_units": all_pa_codes,
    })


def _bar_chart(df_stats: pd.DataFrame, y_col: str, title: str, y_label: str,
               active_color: str, inactive_color: str, ex_keys: set) -> go.Figure | None:
    df_plot = df_stats.sort_values(y_col, ascending=True)
    if df_plot.empty:
        return None
    colors = [
        inactive_color if k in ex_keys else active_color
        for k in df_plot["gruppe_key"]
    ]
    text_vals = df_plot[y_col].apply(
        lambda v: _fmt_mak(v) if "mak" in y_col else _fmt_int(int(v))
    )
    fig = go.Figure(go.Bar(
        x=df_plot[y_col],
        y=df_plot["gruppe_name"],
        orientation="h",
        marker_color=colors,
        text=text_vals,
        textposition="outside",
    ))
    fig.update_layout(
        title=title,
        xaxis_title=y_label,
        yaxis_title=None,
        height=max(340, len(df_plot) * 34 + 80),
        margin=dict(l=10, r=70, t=40, b=20),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(color=COLORS["text_primary"]),
        xaxis=dict(gridcolor="#F0F0F0"),
    )
    return fig


_GROUP_LABEL_KEYS = {
    VORSTAND_KEY: "exclusion.group.vorstand",
    RUHEND_KEY: "exclusion.group.ruhend_bv",
    "9900": "exclusion.group.9900",
    "9910": "exclusion.group.9910",
    "9920": "exclusion.group.9920",
    "9921": "exclusion.group.9921",
    "9940": "exclusion.group.9940",
    "9941": "exclusion.group.9941",
    "9945": "exclusion.group.9945",
    "9960": "exclusion.group.9960",
    "9970": "exclusion.group.9970",
    "9971": "exclusion.group.9971",
    "9972": "exclusion.group.9972",
    "9973": "exclusion.group.9973",
    "9975": "exclusion.group.9975",
    "9980": "exclusion.group.9980",
    "9981": "exclusion.group.9981",
    "9990": "exclusion.group.9990",
    "9999": "exclusion.group.9999",
    "99XX": "exclusion.group.99xx",
}


def _group_label(key: str, fallback: str) -> str:
    label_key = _GROUP_LABEL_KEYS.get(key)
    if not label_key:
        return fallback
    return t(label_key)


# ---------------------------------------------------------------------------
# Haupt-Rendering
# ---------------------------------------------------------------------------

def main():
    st.title(t("exclusion.title"))
    st.caption(t("exclusion.subtitle"))

    # ── Daten laden ──────────────────────────────────────────────────────────
    set_metric_page_hint(t("exclusion.metric_hint"))

    try:
        snapshot_df, history_df, org_df, summary = load_and_prepare_data()
        render_global_filters(snapshot_df, history_df)
    except Exception as exc:
        st.error(t("exclusion.error.load", error=exc))
        return

    if snapshot_df is None or snapshot_df.empty:
        st.info(t("exclusion.info.no_data"))
        return

    # HINWEIS: apply_filters() wird hier NICHT auf snapshot_df angewendet.
    # Die Deep-Dive-Seite ist eine Transparenzseite für Exklusionsgruppen — sie zeigt
    # bewusst ALLE Gruppen aus dem vollständigen Snapshot. Würden Sidebar-Filter greifen,
    # würden 99XX-OE-Zeilen (z. B. "PA Elternzeit") herausgefiltert, bevor build_group_masks()
    # sie erkennen kann → fälschlicherweise 0 exkludierte Planstellen.
    # Der Drilldown-Bereich nutzt ebenfalls den ungefilterten Snapshot für Konsistenz.

    # ── Aktuelle Exklusions-Einstellungen ────────────────────────────────────
    current_ex = _load_current_exclusions()
    ex_org_units: list = current_ex.get("org_units", [])

    # ── Gruppen-Stats berechnen ───────────────────────────────────────────────
    df_stats = get_all_group_stats(snapshot_df)
    if df_stats.empty:
        st.warning(t("exclusion.warning.no_groups"))
        return
    df_stats["gruppe_name"] = df_stats.apply(
        lambda row: _group_label(str(row["gruppe_key"]), str(row["gruppe_name"])),
        axis=1,
    )

    # Soll-MAK Gesamt (Planstellen-Bedarf, immer erhalten)
    if "Soll_FTE" in snapshot_df.columns:
        total_soll_mak = float(snapshot_df["Soll_FTE"].fillna(0).sum())
    else:
        total_soll_mak = float(
            snapshot_df.get("Sollarbeitszeit", pd.Series(dtype=float)).fillna(0).sum() / 39.0
        )
    total_planstellen = len(snapshot_df)

    # Aktuell exkludierte Keys zusammenstellen
    ex_keys: set = set()
    if current_ex.get("vorstand"):
        ex_keys.add(VORSTAND_KEY)
    if current_ex.get("ruhend_bv"):
        ex_keys.add(RUHEND_KEY)
    ex_keys.update(ex_org_units)

    # Exkludierte Kennzahlen via Union-Maske (verhindert Doppelzählung bei Mehrfachzuordnung,
    # z. B. Zeilen die gleichzeitig Ruhendes BV UND OE 9900 sind)
    all_masks = build_group_masks(snapshot_df)
    union_ex_mask = pd.Series(False, index=snapshot_df.index)
    for key in ex_keys:
        if key in all_masks:
            union_ex_mask = union_ex_mask | all_masks[key]

    ex_planstellen = int(union_ex_mask.sum())
    if "Soll_FTE" in snapshot_df.columns:
        ex_soll_mak = float(snapshot_df.loc[union_ex_mask, "Soll_FTE"].fillna(0).sum())
    elif "Sollarbeitszeit" in snapshot_df.columns:
        ex_soll_mak = float(
            snapshot_df.loc[union_ex_mask, "Sollarbeitszeit"].fillna(0).sum() / 39.0
        )
    else:
        ex_soll_mak = 0.0
    active_planstellen = total_planstellen - ex_planstellen
    active_soll_mak = total_soll_mak - ex_soll_mak
    anteil_ex_pct = ex_soll_mak / total_soll_mak * 100 if total_soll_mak > 0 else 0.0

    # Aktiv IST-MAK: Effektive Kapazität aktiver, besetzter Mitarbeitender (Brücke zur Kompakt-Seite)
    # Identischer Berechnungspfad wie compute_fte_effektiv(): dedup via get_unique_employees(),
    # dann MAK-Summe via resolve_mak_series() (Priorität: MAK_Calculated → mak → MAK).
    active_df = snapshot_df[~union_ex_mask]
    emp_active = get_unique_employees(active_df)  # dedupliziert auf Mitarbeiterebene
    active_ist_mak = float(resolve_mak_series(emp_active).sum())

    # ── BEREICH 1: Globale KPIs ───────────────────────────────────────────────
    st.markdown(t("exclusion.section.overview"))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _metric_card(t("exclusion.metric.total_positions"), _fmt_int(total_planstellen),
                     sub=t("exclusion.metric.total_positions.sub"),
                     color=COLORS["accent_blue"])
    with c2:
        _metric_card(t("exclusion.metric.total_target_fte"), _fmt_mak(total_soll_mak),
                     sub=t("exclusion.metric.total_target_fte.sub"),
                     color=COLORS["accent_blue"])
    with c3:
        _metric_card(t("exclusion.metric.excluded_positions"), _fmt_int(ex_planstellen),
                     sub=t("exclusion.metric.excluded_positions.sub", share=f"{ex_planstellen / total_planstellen * 100:.1f} %") if total_planstellen else "",
                     color=COLORS["accent_amber"])
    with c4:
        _metric_card(t("exclusion.metric.excluded_target_fte"), _fmt_mak(ex_soll_mak),
                     sub=t("exclusion.metric.excluded_target_fte.sub", share=_fmt_pct(anteil_ex_pct)),
                     color=COLORS["accent_amber"])

    c5, c6, c7, _ = st.columns(4)
    with c5:
        _metric_card(t("exclusion.metric.active_positions"), _fmt_int(active_planstellen),
                     sub=t("exclusion.metric.active_positions.sub"),
                     color=COLORS["accent_green"])
    with c6:
        _metric_card(t("exclusion.metric.active_target_fte"), _fmt_mak(active_soll_mak),
                     sub=t("exclusion.metric.active_target_fte.sub"),
                     color=COLORS["accent_green"])
    with c7:
        _metric_card(t("exclusion.metric.active_current_fte"), _fmt_mak(active_ist_mak),
                     sub=t("exclusion.metric.active_current_fte.sub"),
                     color=COLORS["accent_green"])

    st.markdown("---")

    # ── BEREICH 2: Gruppen-Tabelle mit Checkboxen ────────────────────────────
    st.markdown(t("exclusion.section.group_exclusions"))
    st.caption(t("exclusion.group_exclusions.caption"))

    # Bulk-Aktionen (nur session state — kein Persist, Nutzer bestätigt unten)
    bulk_col1, bulk_col2 = st.columns(2)
    if bulk_col1.button(t("exclusion.action.exclude_all"), key="btn_ex_all", use_container_width=True):
        for k, _l, _c in [(VORSTAND_KEY, "", "special"), (RUHEND_KEY, "", "special")] + \
                         [(code, "", "pa") for code, _ in PA_GROUPS]:
            st.session_state[f"ex_chk_{k}"] = True
        st.rerun()
    if bulk_col2.button(t("exclusion.action.include_all"), key="btn_in_all", use_container_width=True):
        for k, _l, _c in [(VORSTAND_KEY, "", "special"), (RUHEND_KEY, "", "special")] + \
                         [(code, "", "pa") for code, _ in PA_GROUPS]:
            st.session_state[f"ex_chk_{k}"] = False
        st.rerun()

    st.markdown("&nbsp;", unsafe_allow_html=True)

    # Anteil je Gruppe berechnen
    df_stats["anteil_pct"] = (
        df_stats["soll_mak"] / total_soll_mak * 100
        if total_soll_mak > 0
        else 0.0
    )

    # ── Feste Gruppen-Definitionen ────────────────────────────────────────────
    # Reihenfolge: Vorstand, Ruhendes BV, dann alle PA-Bereiche alphabetisch nach Code
    GROUP_ORDER = [
        (VORSTAND_KEY, _group_label(VORSTAND_KEY, "Vorstand"), "special"),
        (RUHEND_KEY, _group_label(RUHEND_KEY, "Ruhendes Beschäftigungsverhältnis"), "special"),
    ] + [(code, _group_label(code, label), "pa") for code, label in PA_GROUPS]

    # Session-State für Checkboxen initialisieren (nur beim ersten Laden)
    for key, _label, _cat in GROUP_ORDER:
        ck = f"ex_chk_{key}"
        if ck not in st.session_state:
            if key == VORSTAND_KEY:
                st.session_state[ck] = current_ex.get("vorstand", False)
            elif key == RUHEND_KEY:
                st.session_state[ck] = current_ex.get("ruhend_bv", False)
            else:
                st.session_state[ck] = key in ex_org_units

    # Tabellen-Header
    hdr = st.columns([3, 1, 1, 1, 1, 1, 1])
    hdr[0].markdown(f"**{t('exclusion.table.group')}**")
    hdr[1].markdown(f"**{t('exclusion.table.positions')}**")
    hdr[2].markdown(f"**{t('exclusion.table.target_fte')}**")
    hdr[3].markdown(f"**{t('exclusion.table.current_fte')}**")
    hdr[4].markdown(f"**{t('exclusion.table.share')}**")
    hdr[5].markdown(f"**{t('exclusion.table.filled')}**")
    hdr[6].markdown(f"**{t('exclusion.table.exclude')}**")
    st.markdown(
        "<hr style='margin:4px 0 8px 0;border-color:#E0E0E0;'>",
        unsafe_allow_html=True,
    )

    for key, label, category in GROUP_ORDER:
        stats_row = df_stats[df_stats["gruppe_key"] == key]
        if stats_row.empty:
            planst, soll_mak, ist_mak, besetzt, anteil = 0, 0.0, 0.0, 0, 0.0
        else:
            r = stats_row.iloc[0]
            planst = int(r["planstellen"])
            soll_mak = float(r["soll_mak"])
            ist_mak = float(r["ist_mak"])
            besetzt = int(r["davon_besetzt"])
            anteil = float(r["anteil_pct"])

        ck = f"ex_chk_{key}"
        is_excluded = st.session_state.get(ck, False)

        # Zeilenfarbe: gedämpft wenn exkludiert, amber-Warnung bei 0 IST-MAK ohne Exklusion
        if is_excluded:
            row_style = "color:#A9A9A9;"
        elif planst > 0 and ist_mak == 0.0 and not is_excluded:
            row_style = "color:#b45309;"  # Amber – natürliche Null
        else:
            row_style = ""

        cols = st.columns([3, 1, 1, 1, 1, 1, 1])

        # Gruppe Label
        with cols[0]:
            if category == "special":
                st.markdown(f"<span style='{row_style}font-weight:600'>{label}</span>",
                            unsafe_allow_html=True)
            else:
                code = key if key != "99XX" else "99XX"
                st.markdown(
                    f"<span style='{row_style}'>{label}</span> "
                    f"<span style='color:#A9A9A9;font-size:0.78rem;'>{code}</span>",
                    unsafe_allow_html=True,
                )

        with cols[1]:
            st.markdown(f"<span style='{row_style}'>{_fmt_int(planst) if planst else '—'}</span>",
                        unsafe_allow_html=True)
        with cols[2]:
            st.markdown(f"<span style='{row_style}'>{_fmt_mak(soll_mak) if planst else '—'}</span>",
                        unsafe_allow_html=True)
        with cols[3]:
            st.markdown(f"<span style='{row_style}'>{_fmt_mak(ist_mak) if planst else '—'}</span>",
                        unsafe_allow_html=True)
        with cols[4]:
            st.markdown(
                f"<span style='{row_style}'>{_fmt_pct(anteil) if planst else '—'}</span>",
                unsafe_allow_html=True,
            )
        with cols[5]:
            st.markdown(f"<span style='{row_style}'>{_fmt_int(besetzt) if planst else '—'}</span>",
                        unsafe_allow_html=True)
        with cols[6]:
            st.checkbox(label, key=ck, label_visibility="collapsed")

    st.markdown(
        "<hr style='margin:8px 0 4px 0;border-color:#E0E0E0;'>",
        unsafe_allow_html=True,
    )
    st.caption(t("exclusion.table.legend"))

    # ── Bestätigungs-Buttons ──────────────────────────────────────────────────
    # Aktuelle Checkbox-Zustände aus session_state lesen
    pending_vorstand = st.session_state.get(f"ex_chk_{VORSTAND_KEY}", current_ex.get("vorstand", False))
    pending_ruhend = st.session_state.get(f"ex_chk_{RUHEND_KEY}", current_ex.get("ruhend_bv", False))
    pending_org = [key for key, _l, cat in GROUP_ORDER
                   if cat == "pa" and st.session_state.get(f"ex_chk_{key}", False)]

    current_follow = current_ex.get("planstellen_follow_person", False)

    has_pending = (
        pending_vorstand != current_ex.get("vorstand", False)
        or pending_ruhend != current_ex.get("ruhend_bv", False)
        or set(pending_org) != set(current_ex.get("org_units", []))
    )

    if has_pending:
        st.info(t("exclusion.info.unsaved"))

    # Aktuellen Scope anzeigen
    scope_label = t("exclusion.scope.dashboard_full") if current_follow else t("exclusion.scope.employees_only")
    scope_color = "#0d6efd" if current_follow else "#6c757d"
    st.markdown(
        f"<div style='margin:8px 0 12px 0;font-size:0.85rem;color:#757575;'>"
        f"{t('exclusion.scope.active')} <span style='background:{scope_color};color:#fff;"
        f"padding:2px 8px;border-radius:4px;font-size:0.8rem;'>{scope_label}</span></div>",
        unsafe_allow_html=True,
    )

    btn_col_a, btn_col_b = st.columns(2, gap="medium")
    with btn_col_a:
        if st.button(
            t("exclusion.action.apply_employees"),
            type="primary" if (has_pending or current_follow) else "secondary",
            use_container_width=True,
            key="btn_apply_excl_ma",
            help=t("exclusion.action.apply_employees.help"),
        ):
            _persist_exclusions(pending_vorstand, pending_ruhend, pending_org, planstellen_follow_person=False)
            st.rerun()

    with btn_col_b:
        if st.button(
            t("exclusion.action.apply_dashboard"),
            type="primary" if (has_pending or not current_follow) else "secondary",
            use_container_width=True,
            key="btn_apply_excl_all",
            help=t("exclusion.action.apply_dashboard.help"),
        ):
            _persist_exclusions(pending_vorstand, pending_ruhend, pending_org, planstellen_follow_person=True)
            st.rerun()

    st.markdown("---")

    # ── BEREICH 3: Charts ─────────────────────────────────────────────────────
    st.markdown(t("exclusion.section.visualization"))

    # Aktuelle ex_keys neu berechnen (nach möglichem rerun)
    fresh_ex = _load_current_exclusions()
    fresh_ex_keys: set = set()
    if fresh_ex.get("vorstand"):
        fresh_ex_keys.add(VORSTAND_KEY)
    if fresh_ex.get("ruhend_bv"):
        fresh_ex_keys.add(RUHEND_KEY)
    fresh_ex_keys.update(fresh_ex.get("org_units", []))

    tab_planst, tab_mak = st.tabs([t("exclusion.tab.positions"), t("exclusion.tab.target_fte")])

    with tab_planst:
        fig_k = _bar_chart(
            df_stats, "planstellen",
            t("exclusion.chart.positions.title"),
            t("exclusion.chart.positions.axis"),
            COLORS["accent_blue"], "#C0C0C0", fresh_ex_keys,
        )
        if fig_k:
            st.plotly_chart(fig_k, use_container_width=True)
            st.caption(t("exclusion.chart.caption.blue"))
        else:
            st.info(t("exclusion.chart.no_data"))

    with tab_mak:
        fig_m = _bar_chart(
            df_stats, "soll_mak",
            t("exclusion.chart.target_fte.title"),
            t("exclusion.chart.target_fte.axis"),
            COLORS["accent_amber"], "#C0C0C0", fresh_ex_keys,
        )
        if fig_m:
            st.plotly_chart(fig_m, use_container_width=True)
            st.caption(t("exclusion.chart.caption.amber"))
        else:
            st.info(t("exclusion.chart.no_data"))

    st.markdown("---")

    # ── BEREICH 4: Drilldown ──────────────────────────────────────────────────
    st.markdown(t("exclusion.section.drilldown"))

    all_group_opts = {label: key for key, label, _ in GROUP_ORDER}
    selected_label = st.selectbox(t("exclusion.select.group"), options=list(all_group_opts.keys()),
                                  key="ex_drilldown_group")
    selected_key = all_group_opts[selected_label]

    dd_row = df_stats[df_stats["gruppe_key"] == selected_key]
    if dd_row.empty or dd_row.iloc[0]["planstellen"] == 0:
        st.info(t("exclusion.info.no_people"))
    else:
        r = dd_row.iloc[0]
        dc1, dc2, dc3, dc4 = st.columns(4)
        with dc1:
            _metric_card(t("exclusion.table.positions"), _fmt_int(int(r["planstellen"])),
                         color=COLORS["accent_blue"])
        with dc2:
            _metric_card(t("exclusion.table.target_fte"), _fmt_mak(r["soll_mak"]),
                         color=COLORS["accent_amber"])
        with dc3:
            _metric_card(t("exclusion.table.current_fte"), _fmt_mak(r["ist_mak"]),
                         color=COLORS["accent_green"])
        with dc4:
            status = t("exclusion.status.excluded") if selected_key in fresh_ex_keys else t("exclusion.status.active")
            status_color = COLORS["accent_red"] if selected_key in fresh_ex_keys else COLORS["accent_green"]
            _metric_card(t("exclusion.metric.status"), status, color=status_color)

        col_oe, col_jf = st.columns(2)
        with col_oe:
            st.markdown(f"**{t('exclusion.top_org_units')}**")
            st.caption(r["top_oes"] if r["top_oes"] != "—" else t("exclusion.info.none"))
        with col_jf:
            st.markdown(f"**{t('exclusion.top_jobfamilies')}**")
            st.caption(r["top_jfs"] if r["top_jfs"] != "—" else t("exclusion.info.none"))

        with st.expander(t("exclusion.expander.rows"), expanded=False):
            detail = get_group_detail(snapshot_df, selected_key)
            if detail.empty:
                st.info(t("exclusion.info.no_rows"))
            else:
                st.dataframe(detail, use_container_width=True, hide_index=True)
                st.caption(t("exclusion.caption.rows", count=len(detail)))


if not globals().get("_UNIT_TESTING", False):
    main()
