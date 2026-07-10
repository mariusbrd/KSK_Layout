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
    SPECIAL_GROUPS,
)
from dataloader.kpi_engine import get_unique_employees
from utils.settings_loader import DEFAULT_EXCLUSIONS, get_setting, set_setting
from utils.cache_utils import bump_cache_version
from utils.plot_helpers import AGE_COHORT_ORDER, apply_legend_bottom, get_age_cohort_color_map
from utils.i18n import t
from components.ui_shell import render_context_box, render_page_header, render_section_intro


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


def _persist_exclusions(
    vorstand: bool,
    ruhend_bv: bool,
    org_units: list,
    special_groups: list | None = None,
    planstellen_follow_person: bool = False,
):
    """Speichert Exklusions-Einstellungen in Settings und Session State."""
    # dict.fromkeys: dedupliziert und erhält Reihenfolge (verhindert T3c-Inkonsistenz)
    org_units_clean = list(dict.fromkeys(org_units))
    special_groups_clean = list(dict.fromkeys(special_groups or []))
    updated = {
        "vorstand": vorstand,
        "ruhend_bv": ruhend_bv,
        "planstellen_follow_person": planstellen_follow_person,
        "org_units": org_units_clean,
        "special_groups": special_groups_clean,
    }
    set_setting("exclusions", updated)
    st.session_state["exclude_vorstand"] = vorstand
    st.session_state["exclude_ruhend"] = ruhend_bv
    st.session_state["exclude_org_units"] = org_units_clean
    st.session_state["exclude_special_groups"] = special_groups_clean
    # Cache leeren: prepare_compact_data() ist @st.cache_data und würde sonst veraltete
    # Exklusions-Stände zurückliefern, da apply_exclusions() in load_and_prepare_data()
    # neue Settings erst nach Cache-Invalidierung wirksam werden.
    bump_cache_version("data_prep")


def _load_current_exclusions() -> dict:
    return get_setting("exclusions", dict(DEFAULT_EXCLUSIONS))


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
    "ausbildung_nachwuchs": "exclusion.group.ausbildung_nachwuchs",
    "jobfamily_validation_special_positions": "exclusion.group.jobfamily_validation_special_positions",
    "sollarbeitszeit_001_positions": "exclusion.group.sollarbeitszeit_001_positions",
}


def _group_label(key: str, fallback: str) -> str:
    label_key = _GROUP_LABEL_KEYS.get(key)
    if not label_key:
        return fallback
    return t(label_key)


# ---------------------------------------------------------------------------
# Strukturelles Profil – Hilfsfunktionen
# ---------------------------------------------------------------------------

_TOP_N_STRUCT = 10
_MAX_LABEL_LEN = 35


def _clean_category_series(series: pd.Series, fallback: str = "Nicht zugeordnet") -> pd.Series:
    """Normalisiert eine kategorische Serie: strip, NaN/leer/nan-String → fallback."""
    cleaned = series.fillna(fallback).astype(str).str.strip()
    return cleaned.replace({"": fallback, "nan": fallback, "None": fallback, "<NA>": fallback})


def _build_top_category_df(
    group_df: pd.DataFrame,
    col: str,
    top_n: int = _TOP_N_STRUCT,
    soll_col: str | None = None,
) -> pd.DataFrame:
    """Aggregiert Top-N Kategorien nach Anzahl, optional mit Soll-MAK-Summe.

    Rückgabe-Columns: 'kategorie', 'anzahl', optional 'soll_mak'.
    Labels werden auf _MAX_LABEL_LEN Zeichen gekürzt (erst nach Aggregation).
    """
    s = _clean_category_series(group_df[col])
    counts = s.value_counts().head(top_n).reset_index()
    counts.columns = ["kategorie", "anzahl"]

    if soll_col and soll_col in group_df.columns:
        soll_num = pd.to_numeric(group_df[soll_col], errors="coerce").fillna(0.0)
        soll_agg = (
            pd.DataFrame({"_cat": s, "_soll": soll_num})
            .groupby("_cat")["_soll"]
            .sum()
            .round(2)
        )
        counts["soll_mak"] = counts["kategorie"].map(soll_agg).fillna(0.0)

    # Labels kürzen (nach Mapping, damit Soll-MAK-Lookup auf Volllabels basiert)
    counts["kategorie"] = counts["kategorie"].apply(
        lambda v: v[: _MAX_LABEL_LEN - 1] + "…" if len(v) > _MAX_LABEL_LEN else v
    )
    return counts


def _render_structural_profile(group_df: pd.DataFrame) -> None:
    """Rendert OE- und Planstellen-Charts für die gewählte Gruppe.

    group_df: rohe Snapshot-Zeilen der Gruppe (alle Spalten, inkl. vakante Planstellen).
    Keine Is_Vacant-Filterung: hier geht es um Planstellenstruktur, nicht Personendemografie.
    """
    if group_df.empty:
        return

    st.subheader("Strukturelles Profil")
    soll_col = "Soll_FTE" if "Soll_FTE" in group_df.columns else None
    show_soll = soll_col and float(group_df[soll_col].fillna(0).sum()) > 0

    col_oe_chart, col_ps_chart = st.columns(2)

    # ── Organisationseinheiten ─────────────────────────────────────────────
    with col_oe_chart:
        oe_col = next(
            (c for c in ("Organisationseinheit", "Kürzel OrgEinheit") if c in group_df.columns),
            None,
        )
        if oe_col is None:
            st.caption("Organisationseinheiten-Daten nicht verfügbar.")
        else:
            agg = _build_top_category_df(
                group_df, oe_col, top_n=_TOP_N_STRUCT,
                soll_col=soll_col if show_soll else None,
            )
            if agg.empty:
                st.caption("Keine Organisationseinheiten-Daten vorhanden.")
            else:
                chart_order = agg["kategorie"].tolist()[::-1]
                hover = (
                    [f"Soll-MAK: {_fmt_mak(v)}" for v in agg["soll_mak"]]
                    if "soll_mak" in agg.columns
                    else None
                )
                fig = go.Figure(go.Bar(
                    x=agg["anzahl"],
                    y=agg["kategorie"],
                    orientation="h",
                    marker_color=COLORS["accent_blue"],
                    text=[_fmt_int(v) for v in agg["anzahl"]],
                    textposition="outside",
                    hovertext=hover,
                    hovertemplate="<b>%{y}</b><br>Anzahl: %{x}<br>%{hovertext}<extra></extra>" if hover else None,
                ))
                fig.update_layout(
                    title="Top-Organisationseinheiten nach Planstellen",
                    xaxis_title="Anzahl",
                    yaxis_title=None,
                    height=max(240, len(agg) * 34 + 80),
                    margin=dict(l=10, r=60, t=40, b=20),
                    plot_bgcolor="white",
                    paper_bgcolor="white",
                    font=dict(color=COLORS["text_primary"]),
                    xaxis=dict(gridcolor="#F0F0F0"),
                    yaxis=dict(categoryorder="array", categoryarray=chart_order),
                )
                st.plotly_chart(fig, use_container_width=True)

    # ── Planstellen ────────────────────────────────────────────────────────
    with col_ps_chart:
        ps_col = next(
            (c for c in ("Planstelle", "Planstellennr") if c in group_df.columns),
            None,
        )
        if ps_col is None:
            st.caption("Planstellen-Daten nicht verfügbar.")
        else:
            agg_ps = _build_top_category_df(
                group_df, ps_col, top_n=_TOP_N_STRUCT,
                soll_col=soll_col if show_soll else None,
            )
            if agg_ps.empty:
                st.caption("Keine Planstellen-Daten vorhanden.")
            else:
                chart_order_ps = agg_ps["kategorie"].tolist()[::-1]
                hover_ps = (
                    [f"Soll-MAK: {_fmt_mak(v)}" for v in agg_ps["soll_mak"]]
                    if "soll_mak" in agg_ps.columns
                    else None
                )
                fig_ps = go.Figure(go.Bar(
                    x=agg_ps["anzahl"],
                    y=agg_ps["kategorie"],
                    orientation="h",
                    marker_color=COLORS["accent_blue"],
                    text=[_fmt_int(v) for v in agg_ps["anzahl"]],
                    textposition="outside",
                    hovertext=hover_ps,
                    hovertemplate="<b>%{y}</b><br>Anzahl: %{x}<br>%{hovertext}<extra></extra>" if hover_ps else None,
                ))
                fig_ps.update_layout(
                    title="Top-Planstellen nach Anzahl",
                    xaxis_title="Anzahl",
                    yaxis_title=None,
                    height=max(240, len(agg_ps) * 34 + 80),
                    margin=dict(l=10, r=60, t=40, b=20),
                    plot_bgcolor="white",
                    paper_bgcolor="white",
                    font=dict(color=COLORS["text_primary"]),
                    xaxis=dict(gridcolor="#F0F0F0"),
                    yaxis=dict(categoryorder="array", categoryarray=chart_order_ps),
                )
                st.plotly_chart(fig_ps, use_container_width=True)


# ---------------------------------------------------------------------------
# Demografisches Profil – Hilfsfunktionen
# ---------------------------------------------------------------------------

def _prepare_demo_df(group_df: pd.DataFrame) -> pd.DataFrame:
    """Gibt Zeilen mit auswertbaren Demografiedaten zurück.

    Primär: Is_Vacant == False (Planstellen mit aktiver Person).
    Fallback: Zeilen mit bekannter Alterskohorte, weil apply_exclusions() alle
    Mitglieder aktiver Exklusionsgruppen auf Is_Vacant=True setzt — ein reiner
    Is_Vacant==False-Filter würde sonst alle PA-Gruppen auf n=0 reduzieren.
    """
    if "Is_Vacant" in group_df.columns:
        occupied = group_df[group_df["Is_Vacant"] == False].copy()  # noqa: E712
        if not occupied.empty:
            return occupied
    # Fallback: Alterskohorte != "Unbekannt" als Proxy für "Person ist zugeordnet"
    if "Alterskohorte" in group_df.columns:
        return group_df[
            group_df["Alterskohorte"].notna() & (group_df["Alterskohorte"] != "Unbekannt")
        ].copy()
    return group_df.copy()


def _normalize_gender_label(value) -> str:
    """Normalisiert Geschlechts-Kürzel auf deutsche Langform."""
    v = str(value).strip().lower()
    if v in ("m", "maennlich", "männlich"):
        return "männlich"
    if v in ("w", "weiblich"):
        return "weiblich"
    return str(value)


def _render_demo_profile(group_df: pd.DataFrame) -> None:
    """Rendert Alterskohorten- und Geschlechtsverteilung für die gewählte Gruppe.

    group_df: rohe Snapshot-Zeilen der Gruppe (alle Spalten).
    Intern gefiltert auf besetzte Planstellen vor der Chart-Erstellung.
    Datenschutz: Charts nur bei >= 5 auswertbaren Zeilen.
    """
    demo_df = _prepare_demo_df(group_df)

    st.subheader("Demografisches Profil")
    if len(demo_df) < 5:
        st.caption(
            "Für diese Gruppe werden aus Datenschutzgründen keine "
            "demografischen Detailcharts angezeigt."
        )
        return

    col_age, col_gender = st.columns(2)

    # ── Alterskohorten ─────────────────────────────────────────────────────
    with col_age:
        if "Alterskohorte" not in demo_df.columns:
            st.caption("Alterskohorten-Daten nicht verfügbar.")
        else:
            age_series = demo_df["Alterskohorte"].dropna()
            age_series = age_series[age_series != "Unbekannt"]
            if age_series.empty:
                st.caption("Keine auswertbaren Alterskohorten vorhanden.")
            else:
                age_counts = age_series.value_counts()
                known = [c for c in AGE_COHORT_ORDER if c in age_counts.index]
                unknown = sorted(c for c in age_counts.index if c not in AGE_COHORT_ORDER)
                ordered = known + unknown
                age_counts = age_counts.reindex(ordered).dropna().astype(int)

                cdm = get_age_cohort_color_map(list(age_counts.index))
                bar_colors = [cdm.get(c, "#B8C0CC") for c in age_counts.index]

                fig = go.Figure(go.Bar(
                    x=age_counts.values,
                    y=age_counts.index.tolist(),
                    orientation="h",
                    marker_color=bar_colors,
                    text=[_fmt_int(v) for v in age_counts.values],
                    textposition="outside",
                ))
                fig.update_layout(
                    title="Alterskohorten der besetzten Planstellen",
                    xaxis_title="Anzahl",
                    yaxis_title=None,
                    height=max(220, len(age_counts) * 32 + 80),
                    margin=dict(l=10, r=50, t=40, b=20),
                    plot_bgcolor="white",
                    paper_bgcolor="white",
                    font=dict(color=COLORS["text_primary"]),
                    xaxis=dict(gridcolor="#F0F0F0"),
                    yaxis=dict(
                        categoryorder="array",
                        categoryarray=list(reversed(ordered)),
                    ),
                )
                st.plotly_chart(fig, use_container_width=True)

    # ── Geschlechtsverteilung ──────────────────────────────────────────────
    with col_gender:
        gender_series = None
        if "Text Gsch" in demo_df.columns:
            gs = demo_df["Text Gsch"].dropna()
            gs = gs[gs.astype(str).str.strip() != ""]
            if not gs.empty:
                gender_series = gs
        if gender_series is None and "Geschlecht" in demo_df.columns:
            gs = demo_df["Geschlecht"].dropna()
            gs = gs[gs.astype(str).str.strip() != ""]
            if not gs.empty:
                gender_series = gs

        if gender_series is None or len(gender_series) == 0:
            st.caption("Keine auswertbaren Geschlechtswerte vorhanden.")
        else:
            gender_series = gender_series.apply(_normalize_gender_label)
            gender_counts = gender_series.value_counts()

            bar_colors = [
                COLORS.get("gender_female", "#E94D3A") if g == "weiblich"
                else COLORS.get("gender_male", "#0088DE") if g == "männlich"
                else COLORS.get("text_secondary", "#A9A9A9")
                for g in gender_counts.index
            ]

            fig = go.Figure(go.Bar(
                x=gender_counts.values,
                y=gender_counts.index.tolist(),
                orientation="h",
                marker_color=bar_colors,
                text=[_fmt_int(v) for v in gender_counts.values],
                textposition="outside",
            ))
            fig.update_layout(
                title="Geschlechtsverteilung der besetzten Planstellen",
                xaxis_title="Anzahl",
                yaxis_title=None,
                height=max(200, len(gender_counts) * 60 + 80),
                margin=dict(l=10, r=50, t=40, b=20),
                plot_bgcolor="white",
                paper_bgcolor="white",
                font=dict(color=COLORS["text_primary"]),
                xaxis=dict(gridcolor="#F0F0F0"),
            )
            st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Haupt-Rendering
# ---------------------------------------------------------------------------

def main():
    render_page_header(
        t("exclusion.title"),
        "Definiere den Analyse-Scope des Dashboards: Exklusionsgruppen entfernen ausgewählte "
        "Planstellen- und Personengruppen aus Mitarbeiter-, Planstellen- und Prognoseanalysen.",
    )

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
    ex_special_groups: list = current_ex.get("special_groups") or []

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
    ex_keys.update(ex_special_groups)

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

    # ── Header-Meta-Zeile ─────────────────────────────────────────────────────
    scope_label = (
        t("exclusion.scope.dashboard_full")
        if current_ex.get("planstellen_follow_person")
        else t("exclusion.scope.employees_only")
    )
    st.caption(
        f"Vollpopulation · {len(all_masks)} Exklusionsgruppen · "
        f"{_fmt_int(ex_planstellen)} ausgeschlossen · {_fmt_int(active_planstellen)} aktiv · "
        f"Scope: {scope_label} · "
        "Datenbasis: ungefilterter Snapshot, Sidebar-Filter nicht angewendet"
    )

    # ── BEREICH 1: Globale KPIs ───────────────────────────────────────────────
    render_section_intro("Übersicht", "Planstellen und MAK-Kapazität im Gesamtüberblick.")
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

    st.divider()

    # ── BEREICH 2: Gruppen-Tabelle mit Checkboxen ────────────────────────────
    render_section_intro("Gruppen-Ausschlüsse", t("exclusion.group_exclusions.caption"))

    # Bulk-Aktionen (nur session state — kein Persist, Nutzer bestätigt unten)
    bulk_col1, bulk_col2, bulk_col3 = st.columns(3)
    if bulk_col1.button(t("exclusion.action.exclude_all"), key="btn_ex_all", use_container_width=True):
        for k, _l, _c in [(VORSTAND_KEY, "", "special"), (RUHEND_KEY, "", "special")] + \
                         [(code, "", "pa") for code, _ in PA_GROUPS] + \
                         [(key, "", "special_group") for key, _ in SPECIAL_GROUPS]:
            st.session_state[f"ex_chk_{k}"] = True
        st.rerun()
    if bulk_col2.button(t("exclusion.action.include_all"), key="btn_in_all", use_container_width=True):
        for k, _l, _c in [(VORSTAND_KEY, "", "special"), (RUHEND_KEY, "", "special")] + \
                         [(code, "", "pa") for code, _ in PA_GROUPS] + \
                         [(key, "", "special_group") for key, _ in SPECIAL_GROUPS]:
            st.session_state[f"ex_chk_{k}"] = False
        st.rerun()
    if bulk_col3.button(t("exclusion.action.jobfamily_validation_preset"), key="btn_jf_validation_preset", use_container_width=True):
        pa_validation_codes = {code for code, _ in PA_GROUPS if code not in {"9999", "99XX"}}
        special_validation_keys = {key for key, _ in SPECIAL_GROUPS}
        for k, _l, cat in [(VORSTAND_KEY, "", "special"), (RUHEND_KEY, "", "special")] + \
                          [(code, "", "pa") for code, _ in PA_GROUPS] + \
                          [(key, "", "special_group") for key, _ in SPECIAL_GROUPS]:
            if k == VORSTAND_KEY:
                st.session_state[f"ex_chk_{k}"] = False
            elif k == RUHEND_KEY:
                st.session_state[f"ex_chk_{k}"] = True
            elif cat == "pa":
                st.session_state[f"ex_chk_{k}"] = k in pa_validation_codes
            else:
                st.session_state[f"ex_chk_{k}"] = k in special_validation_keys
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
    ] + [(code, _group_label(code, label), "pa") for code, label in PA_GROUPS] + [
        (key, _group_label(key, label), "special_group") for key, label in SPECIAL_GROUPS
    ]

    # Session-State für Checkboxen initialisieren (nur beim ersten Laden)
    for key, _label, _cat in GROUP_ORDER:
        ck = f"ex_chk_{key}"
        if ck not in st.session_state:
            if key == VORSTAND_KEY:
                st.session_state[ck] = current_ex.get("vorstand", False)
            elif key == RUHEND_KEY:
                st.session_state[ck] = current_ex.get("ruhend_bv", False)
            elif _cat == "special_group":
                st.session_state[ck] = key in ex_special_groups
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
            if category in {"special", "special_group"}:
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
    pending_special = [key for key, _l, cat in GROUP_ORDER
                       if cat == "special_group" and st.session_state.get(f"ex_chk_{key}", False)]

    current_follow = current_ex.get("planstellen_follow_person", False)

    has_pending = (
        pending_vorstand != current_ex.get("vorstand", False)
        or pending_ruhend != current_ex.get("ruhend_bv", False)
        or set(pending_org) != set(current_ex.get("org_units", []))
        or set(pending_special) != set(current_ex.get("special_groups") or [])
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
            _persist_exclusions(
                pending_vorstand,
                pending_ruhend,
                pending_org,
                pending_special,
                planstellen_follow_person=False,
            )
            st.rerun()

    with btn_col_b:
        if st.button(
            t("exclusion.action.apply_dashboard"),
            type="primary" if (has_pending or not current_follow) else "secondary",
            use_container_width=True,
            key="btn_apply_excl_all",
            help=t("exclusion.action.apply_dashboard.help"),
        ):
            _persist_exclusions(
                pending_vorstand,
                pending_ruhend,
                pending_org,
                pending_special,
                planstellen_follow_person=True,
            )
            st.rerun()

    st.divider()

    # ── BEREICH 3: Charts ─────────────────────────────────────────────────────
    render_section_intro("Visualisierung", "Vergleich exkludierter und aktiver Gruppen nach Planstellen und Soll-MAK.")

    # Aktuelle ex_keys neu berechnen (nach möglichem rerun)
    fresh_ex = _load_current_exclusions()
    fresh_ex_keys: set = set()
    if fresh_ex.get("vorstand"):
        fresh_ex_keys.add(VORSTAND_KEY)
    if fresh_ex.get("ruhend_bv"):
        fresh_ex_keys.add(RUHEND_KEY)
    fresh_ex_keys.update(fresh_ex.get("org_units", []))
    fresh_ex_keys.update(fresh_ex.get("special_groups") or [])

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

    st.divider()

    # ── BEREICH 4: Drilldown ──────────────────────────────────────────────────
    render_section_intro("Drilldown", "Wähle eine Gruppe für das strukturelle und demografische Profil.")

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
            render_context_box(
                t("exclusion.top_org_units"),
                r["top_oes"] if r["top_oes"] != "—" else t("exclusion.info.none"),
                tone="info",
                compact=True,
            )
        with col_jf:
            render_context_box(
                t("exclusion.top_jobfamilies"),
                r["top_jfs"] if r["top_jfs"] != "—" else t("exclusion.info.none"),
                tone="info",
                compact=True,
            )

        group_mask = all_masks.get(selected_key, pd.Series(False, index=snapshot_df.index))
        _render_structural_profile(snapshot_df[group_mask])
        _render_demo_profile(snapshot_df[group_mask])

        with st.expander(t("exclusion.expander.rows"), expanded=False):
            detail = get_group_detail(snapshot_df, selected_key)
            if detail.empty:
                st.info(t("exclusion.info.no_rows"))
            else:
                # Anzeige-Umbenennung: interner Spaltenname Soll_FTE -> MAK-Terminologie
                display_detail = detail.rename(columns={"Soll_FTE": "Soll_MAK"})
                st.dataframe(display_detail, use_container_width=True, hide_index=True)
                st.caption(t("exclusion.caption.rows", count=len(detail)))


if not globals().get("_UNIT_TESTING", False):
    main()
