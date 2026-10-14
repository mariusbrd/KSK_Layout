"""
Deep Dive: Exklusionsgruppen

Kombinierte Transparenz- und Steuerungsseite.
Zeigt alle exkludierbaren Personengruppen mit Kennzahlen und ermöglicht
deren Aktivierung/Deaktivierung direkt im Kontext der Datenlage.

Optionale MA-Typ-Auswahl verwendet dieselbe Exklusionswirkung wie die Gruppenregeln.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataloader.loader import load_and_prepare_data, apply_exclusions
from components.sidebar import render_global_filters, apply_filters, set_metric_page_hint
from config.settings import COLORS, EXCLUSION_ORG_UNITS
from utils.exclusion_groups import (
    build_group_masks,
    selected_group_keys, ma_typ_series,
    get_all_group_stats,
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
    selection_basis: str = "legacy",
    ma_typ_values: list | None = None,
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
        "selection_basis": selection_basis,
        "ma_typ_values": list(dict.fromkeys(ma_typ_values or [])),
    }
    if not set_setting("exclusions", updated):
        st.error(t("exclusion.selection.save_failed"))
        return False
    st.session_state["exclude_vorstand"] = vorstand
    st.session_state["exclude_ruhend"] = ruhend_bv
    st.session_state["exclude_org_units"] = org_units_clean
    st.session_state["exclude_special_groups"] = special_groups_clean
    # Cache leeren: prepare_compact_data() ist @st.cache_data und würde sonst veraltete
    # Exklusions-Stände zurückliefern, da apply_exclusions() in load_and_prepare_data()
    # neue Settings erst nach Cache-Invalidierung wirksam werden.
    bump_cache_version("data_prep")
    return True


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


def _numeric_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    """Return the first available numeric column without altering the snapshot."""
    for column in candidates:
        if column in df.columns:
            return pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=df.index)


def _build_group_entry_analysis(group_df: pd.DataFrame, group_key: str) -> pd.DataFrame:
    """Create a row-level explanation of capacity and exclusion treatment.

    This is display-only. In particular, ``SOLL-MAK nach Ausschluss`` describes
    the row-level exclusion effect, not a replacement for the dashboard's later
    duplicate-plan-position and sidebar-filter calculations.
    """
    work = group_df.copy(deep=True)
    if work.empty:
        return pd.DataFrame()

    soll = _numeric_column(work, ("Soll_FTE",))
    soll_hours = _numeric_column(work, ("Sollarbeitszeit",))
    technical_mak = _numeric_column(work, ("MAK_Calculated", "mak", "MAK"))
    reporting_mak = _numeric_column(work, ("MAK_Reporting",))
    source_capacity = _numeric_column(work, ("BsGrd_Source", "BsGrd", "Personen_MAK_Source"))
    excluded = work.get("Is_Excluded", pd.Series(False, index=work.index)).fillna(False).astype(bool)
    preserved = work.get("Is_IST_Preserved_Despite_Exclusion", pd.Series(False, index=work.index)).fillna(False).astype(bool)
    vacant = work.get("Is_Vacant", pd.Series(False, index=work.index)).fillna(False).astype(bool)

    work["Soll-Typ"] = "Ohne hinterlegten Soll-Bedarf"
    work.loc[soll_hours.sub(0.01).abs().le(1e-9), "Soll-Typ"] = "Technische Platzhalterstelle (0,01 Std.)"
    work.loc[soll.gt(0), "Soll-Typ"] = "Reguläre Sollstelle"

    work["Beschäftigungsstatus"] = "Besetzt"
    work.loc[vacant, "Beschäftigungsstatus"] = "Unbesetzt"
    work.loc[excluded & ~preserved & source_capacity.gt(0), "Beschäftigungsstatus"] = "Ausgeschlossen – IST entfernt"
    work.loc[excluded & preserved, "Beschäftigungsstatus"] = "Besetzt – IST trotz Ausschluss erhalten"

    work["Ausschlusswirkung"] = "Nicht ausgeschlossen"
    work.loc[excluded, "Ausschlusswirkung"] = "SOLL nach Ausschluss: 0"
    work.loc[excluded & ~preserved, "Ausschlusswirkung"] = "SOLL: 0 · IST: 0"
    work.loc[excluded & preserved, "Ausschlusswirkung"] = "SOLL: 0 · IST wird weitergeführt"
    work["SOLL-MAK (roh)"] = soll
    work["SOLL-MAK nach Ausschluss"] = soll.where(~excluded, 0.0)
    work["IST-MAK technisch"] = technical_mak
    work["IST-MAK Reporting"] = reporting_mak

    work["Prüfhinweis"] = ""
    work.loc[work["Soll-Typ"].eq("Technische Platzhalterstelle (0,01 Std.)"), "Prüfhinweis"] = (
        "Technischer Platzhalter; Soll-Bedarf ist nicht belastbar."
    )
    if group_key == "jobfamily_validation_special_positions":
        work.loc[work["Soll-Typ"].eq("Reguläre Sollstelle"), "Prüfhinweis"] = (
            "Reguläre Sollstelle in historischer Sonderstellenliste – fachlich prüfen."
        )
    work.loc[excluded & preserved, "Prüfhinweis"] = (
        "Technische Sonderstelle mit realer Kapazität; IST bleibt gemäß Regel erhalten."
    )

    column_map = {
        "Planstellennr": "Planstellennummer", "Planstelle": "Planstelle",
        "Kürzel OrgEinheit": "OE-Kürzel", "Organisationseinheit": "Organisationseinheit",
        "OE-Cluster": "OE-Cluster", "Jobfamily": "Jobgruppe", "MA-Typ": "MA-Typ",
        "Vertragsart": "Vertragsart", "MitarbGruppenbez.": "Mitarbeitergruppe",
        "TrfGr": "Tarifgruppe", "ATZ_Status": "ATZ-Status", "BsGrd_Source": "Beschäftigungsgrad (%)",
        "Sollarbeitszeit": "Sollstunden/Woche", "Exclusion_Group": "Ausschlussgrund",
    }
    columns = [source for source in column_map if source in work.columns]
    columns += ["Soll-Typ", "Beschäftigungsstatus", "SOLL-MAK (roh)", "SOLL-MAK nach Ausschluss",
                "IST-MAK technisch", "IST-MAK Reporting", "Ausschlusswirkung", "Prüfhinweis"]
    result = work[columns].rename(columns=column_map)
    numeric = ["Sollstunden/Woche", "Beschäftigungsgrad (%)", "SOLL-MAK (roh)", "SOLL-MAK nach Ausschluss",
               "IST-MAK technisch", "IST-MAK Reporting"]
    for column in numeric:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce").round(2)
    return result


def _render_group_entry_analysis(group_df: pd.DataFrame, group_key: str) -> None:
    """Render an explainable, filterable view of the selected group's entries."""
    detail = _build_group_entry_analysis(group_df, group_key)
    if detail.empty:
        st.info(t("exclusion.info.no_rows"))
        return

    regular = detail["Soll-Typ"].eq("Reguläre Sollstelle")
    preserved = detail["Beschäftigungsstatus"].eq("Besetzt – IST trotz Ausschluss erhalten")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Betroffene Planstellen", _fmt_int(len(detail)))
    k2.metric("Zeilen mit IST-MAK", _fmt_int(int(detail["IST-MAK Reporting"].gt(0).sum())))
    k3.metric("IST-MAK Reporting", _fmt_mak(float(detail["IST-MAK Reporting"].sum())))
    k4.metric("SOLL-MAK nach Ausschluss", _fmt_mak(float(detail["SOLL-MAK nach Ausschluss"].sum())))
    st.caption("Zeilenansicht: IST-MAK Reporting ist die berichtete Kapazität je Planstelle. "
               "Die Soll-Spalte zeigt ausschließlich die Ausschlusswirkung; weitere Dashboard-Filter oder Planstellen-Deduplizierung sind hier nicht enthalten.")
    if group_key == "jobfamily_validation_special_positions" and regular.any():
        st.warning(f"{int(regular.sum())} reguläre Sollstellen liegen in der historischen Sonderstellenliste. "
                   "Diese Fälle sind fachlich prüfbedürftig.")

    filters = ["Alle", "Nur besetzt", "Nur technische Platzhalter", "Nur reguläre Sollstellen",
               "IST trotz Ausschluss erhalten", "Nur prüfbedürftige Fälle"]
    selected_filter = st.radio("Einträge filtern", filters, horizontal=True, key=f"ex_entry_filter_{group_key}")
    visible = detail.copy()
    if selected_filter == "Nur besetzt":
        visible = visible.loc[~visible["Beschäftigungsstatus"].eq("Unbesetzt")]
    elif selected_filter == "Nur technische Platzhalter":
        visible = visible.loc[visible["Soll-Typ"].eq("Technische Platzhalterstelle (0,01 Std.)")]
    elif selected_filter == "Nur reguläre Sollstellen":
        visible = visible.loc[visible["Soll-Typ"].eq("Reguläre Sollstelle")]
    elif selected_filter == "IST trotz Ausschluss erhalten":
        visible = visible.loc[visible["Beschäftigungsstatus"].eq("Besetzt – IST trotz Ausschluss erhalten")]
    elif selected_filter == "Nur prüfbedürftige Fälle":
        visible = visible.loc[visible["Prüfhinweis"].str.contains("prüfen", case=False, na=False)]
    st.dataframe(visible, use_container_width=True, hide_index=True)
    st.caption(f"{len(visible)} von {len(detail)} betroffenen Planstellen angezeigt. "
               f"{int(preserved.sum())} Zeilen behalten ihre IST-Kapazität trotz Ausschluss.")


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

def _section_title(key: str) -> str:
    return t(key).replace("### ", "", 1).strip()


def main():
    render_page_header(
        t("exclusion.title"),
        t("exclusion.subtitle"),
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
    ex_keys = selected_group_keys(current_ex)

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
    # Berechnungspfad wie compute_fte_effektiv(): dedup via get_unique_employees(), dann MAK-Summe
    # mit Prioritaet MAK_Reporting -> MAK_Calculated -> mak -> MAK (identisch zu compute_fte_effektiv,
    # NICHT identisch zur resolve_mak_series()-Standardprioritaet -- siehe unten).
    #
    # v2.3: union_ex_mask markiert reine Gruppen-Mitgliedschaft (fuer SOLL/Transparenz weiterhin
    # korrekt), deckt aber nicht ab, dass apply_exclusions() fuer besetzte Planstellen mit
    # ausschliesslich technischem Platzhaltergrund (Sollarbeitszeit ≈ 0,01 bzw.
    # Jobfamily-Validierungsliste) und realer Kapazitaet (BsGrd>0) die IST-Werte NICHT mehr nullt
    # (Is_Vacant bleibt False). Diese Zeilen muessen daher trotz Gruppen-Mitgliedschaft weiterhin
    # als "aktiv" fuer die IST-Bruecke zaehlen, sonst weicht dieser Wert von der Kompakt-Seite ab.
    #
    # MAK_Reporting statt resolve_mak_series()-Default (MAK_Calculated zuerst) noetig, weil einige
    # der neu einbezogenen Personen zusaetzlich eine zweite, regulaere Planstelle halten:
    # MAK_Calculated ist der rohe Zeilenwert je Planstelle (get_unique_employees summiert ihn nur
    # ueber alle Zeilen der Person, ohne Personen-Obergrenze) und wuerde deren Kapazitaet doppelt
    # zaehlen; MAK_Reporting ist bereits ueber apply_person_mak_allocation() personenscharf auf die
    # reale Kapazitaet begrenzt (Allocation_Weight je Person summiert sich auf 1,0).
    ist_preserved_mask = snapshot_df.get(
        "Is_IST_Preserved_Despite_Exclusion", pd.Series(False, index=snapshot_df.index)
    ).fillna(False)
    active_df = snapshot_df[~union_ex_mask | ist_preserved_mask]
    emp_active = get_unique_employees(active_df)  # dedupliziert auf Mitarbeiterebene
    _active_ist_col = next(
        (c for c in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK") if c in emp_active.columns),
        None,
    )
    active_ist_mak = (
        float(emp_active[_active_ist_col].fillna(0).sum())
        if _active_ist_col
        else float(resolve_mak_series(emp_active).sum())
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

    # Detach hidden widgets from cleanup so switching modes preserves draft choices.
    for draft_key in list(st.session_state):
        if draft_key.startswith("ex_chk_") or draft_key == "ex_ma_typ_values":
            st.session_state[draft_key] = st.session_state[draft_key]
    basis_labels = {key: t("exclusion.selection." + key) for key in ("legacy", "ma_typ", "combined")}
    active_basis = current_ex.get("selection_basis", "legacy")
    if active_basis not in basis_labels:
        active_basis = "legacy"
    st.caption(t("exclusion.selection.active", basis=basis_labels[active_basis],
              types=", ".join(current_ex.get("ma_typ_values") or []) if active_basis != "legacy" else "—"))
    pending_notice = st.empty()
    configure_tab, impact_tab, detail_tab = st.tabs([
        t("exclusion.layout.configure"), t("exclusion.layout.impact"), t("exclusion.layout.detail")])
    with configure_tab:
        if "ex_selection_basis" not in st.session_state:
            st.session_state["ex_selection_basis"] = active_basis
        with st.container():
            st.markdown("### " + t("exclusion.selection.heading"))
            pending_basis = st.radio(t("exclusion.selection.basis"), list(basis_labels),
                                     format_func=basis_labels.get, horizontal=True, key="ex_selection_basis")
            st.caption(t("exclusion.selection.help_" + pending_basis))
            available_types = sorted(set(ma_typ_series(snapshot_df)) - {""})
            if "ex_ma_typ_values" not in st.session_state:
                st.session_state["ex_ma_typ_values"] = list(current_ex.get("ma_typ_values") or [])
            if "MA-Typ" not in snapshot_df.columns:
                if pending_basis != "legacy" or active_basis != "legacy":
                    st.warning(t("exclusion.selection.missing"))
                else:
                    st.caption(t("exclusion.selection.missing"))
            if pending_basis != "legacy":
                options = sorted(set(available_types) | set(st.session_state["ex_ma_typ_values"]))
                counts = ma_typ_series(snapshot_df).value_counts()
                st.multiselect(t("exclusion.selection.types"), options, key="ex_ma_typ_values",
                               format_func=lambda value: t("exclusion.layout.type_count", label=value, count=int(counts.get(value, 0))),
                               disabled="MA-Typ" not in snapshot_df.columns)
                st.caption(t("exclusion.selection.empty", count=int(ma_typ_series(snapshot_df).eq("").sum())))
                unavailable = set(st.session_state["ex_ma_typ_values"]) - set(available_types)
                if unavailable:
                    st.warning(t("exclusion.selection.unavailable", types=", ".join(sorted(unavailable))))
            pending_types = list(st.session_state["ex_ma_typ_values"])

        if pending_basis != "ma_typ":
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

            st.caption(t("exclusion.layout.checked_help"))
            for categories, title in [({"special"}, "status"), ({"pa"}, "org"), ({"special_group"}, "special")]:
                with st.expander(t("exclusion.layout." + title), expanded=categories != {"pa"}):
                    for key, label, category in GROUP_ORDER:
                        if category not in categories:
                            continue
                        group_mask = all_masks.get(key, pd.Series(False, index=snapshot_df.index))
                        count = int(group_mask.sum())
                        group_df = snapshot_df.loc[group_mask]
                        heads = len(get_unique_employees(group_df)) if "PersNr" in group_df.columns else None
                        stats = df_stats.loc[df_stats["gruppe_key"].eq(key)].iloc[0]
                        display_label = f"{key} · {label}" if category == "pa" else label
                        if key == "99XX":
                            display_label = f"99XX · {t('exclusion.selection.99xx_label')}"
                        st.checkbox(display_label, key=f"ex_chk_{key}",
                                    help=t("exclusion.selection.99xx_help") if key == "99XX"
                                    else t("exclusion.layout.position_count", count=count))
                        st.caption(t("exclusion.layout.group_numbers", positions=_fmt_int(count),
                                     heads=_fmt_int(heads) if heads is not None else "—",
                                     actual=_fmt_mak(stats["ist_mak"]), target=_fmt_mak(stats["soll_mak"])))

        # ── Bestätigungs-Buttons ──────────────────────────────────────────────────
        # Aktuelle Checkbox-Zustände aus session_state lesen
        pending_vorstand = st.session_state.get(f"ex_chk_{VORSTAND_KEY}", current_ex.get("vorstand", False))
        pending_ruhend = st.session_state.get(f"ex_chk_{RUHEND_KEY}", current_ex.get("ruhend_bv", False))
        pending_org = [key for key, _l, cat in GROUP_ORDER
                       if cat == "pa" and st.session_state.get(f"ex_chk_{key}", False)]
        pending_special = [key for key, _l, cat in GROUP_ORDER
                           if cat == "special_group" and st.session_state.get(f"ex_chk_{key}", False)]

        current_follow = current_ex.get("planstellen_follow_person", False)
        with st.container(border=True):
            st.markdown("### " + t("exclusion.selection.review"))
            if "ex_scope" not in st.session_state:
                st.session_state["ex_scope"] = bool(current_follow)
            pending_follow = st.radio(t("exclusion.selection.scope"), [False, True],
                                     format_func=lambda value: t("exclusion.scope.dashboard_full" if value else "exclusion.scope.employees_only"),
                                     horizontal=True, key="ex_scope")
            st.caption(t("exclusion.selection.scope_help"))
            pending = dict(current_ex, vorstand=pending_vorstand, ruhend_bv=pending_ruhend,
                           org_units=pending_org, special_groups=pending_special,
                           selection_basis=pending_basis, ma_typ_values=pending_types,
                           planstellen_follow_person=pending_follow)
            has_pending = any(pending.get(key) != current_ex.get(key, default) for key, default in
                              [("vorstand", False), ("ruhend_bv", False), ("org_units", []),
                               ("special_groups", []), ("selection_basis", "legacy"),
                               ("ma_typ_values", []), ("planstellen_follow_person", False)])
            # Reuse the engine for previews, including its historical 99XX semantics.
            preview = apply_exclusions(snapshot_df, pending, record_counts=False)
            preview_mask = preview["Is_Excluded"].fillna(False)
            legacy_preview = apply_exclusions(snapshot_df, dict(pending, selection_basis="legacy"), record_counts=False)
            type_preview = apply_exclusions(snapshot_df, dict(pending, selection_basis="ma_typ"), record_counts=False)
            legacy_mask = legacy_preview["Is_Excluded"].fillna(False)
            type_mask = type_preview["Is_Excluded"].fillna(False)
            cols = st.columns(3 if pending_basis == "combined" else 1)
            cols[0].metric(t("exclusion.selection.affected"), int(preview_mask.sum()))
            if pending_basis == "combined":
                cols[1].metric(t("exclusion.selection.overlap"), int((legacy_mask & type_mask).sum()))
                cols[2].metric(t("exclusion.selection.additional"), int((~legacy_mask & type_mask).sum()))
            st.caption(t("exclusion.selection.preview_help"))
            with st.expander(t("exclusion.selection.inspect")):
                st.caption("Vorschau der noch nicht gespeicherten Auswahl. Die Tabelle zeigt, welche SOLL- und IST-Wirkung "
                           "jede betroffene Planstelle nach dem Anwenden hätte.")
                _render_group_entry_analysis(preview.loc[preview_mask], "preview")
            if has_pending:
                pending_notice.info(t("exclusion.selection.unsaved"))
            if st.button(t("exclusion.selection.apply"), type="primary", key="btn_apply_exclusions",
                         use_container_width=True):
                if _persist_exclusions(pending_vorstand, pending_ruhend, pending_org, pending_special,
                                       planstellen_follow_person=pending_follow,
                                       selection_basis=pending_basis, ma_typ_values=pending_types):
                    st.rerun()

    with impact_tab:
        st.caption(t("exclusion.layout.applied_only"))
        columns = st.columns(3)
        columns[0].metric(t("exclusion.metric.excluded_positions"), _fmt_int(ex_planstellen))
        columns[1].metric(t("exclusion.metric.excluded_target_fte"), _fmt_mak(ex_soll_mak))
        columns[2].metric(t("exclusion.metric.active_current_fte"), _fmt_mak(active_ist_mak))
        st.markdown("### " + t("exclusion.layout.comparison"))
        measure = st.radio(t("exclusion.layout.measure"), ["positions", "mak"],
                           format_func=lambda value: t("exclusion.layout." + value),
                           horizontal=True, key="ex_impact_measure")
        kept, removed = (active_planstellen, ex_planstellen) if measure == "positions" else (active_soll_mak, ex_soll_mak)
        chart = go.Figure()
        for label, value, color in [(t("exclusion.layout.included"), kept, COLORS["accent_blue"]),
                                     (t("exclusion.layout.excluded"), removed, "#94A3B8")]:
            chart.add_trace(go.Bar(name=label, x=[value], y=[t("exclusion.layout." + measure)],
                                  orientation="h", marker_color=color,
                                  text=[_fmt_int(int(value)) if measure == "positions" else _fmt_mak(value)],
                                  textposition="auto", hovertemplate="%{x}<extra>%{fullData.name}</extra>"))
        chart.update_layout(barmode="stack", height=240, margin=dict(l=0, r=0, t=15, b=0),
                            legend=dict(orientation="h", y=-0.25), paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)", xaxis_title=t("exclusion.layout." + measure))
        st.plotly_chart(chart, use_container_width=True)
        if ist_preserved_mask.any():
            st.info(t("exclusion.layout.preserved", count=int(ist_preserved_mask.sum())))
        selected_stats = df_stats[df_stats["gruppe_key"].isin(ex_keys)]
        if not selected_stats.empty:
            with st.expander(t("exclusion.layout.group_effects")):
                st.caption(t("exclusion.layout.overlap_note"))
                st.dataframe(selected_stats[["gruppe_name", "planstellen", "soll_mak", "ist_mak"]].rename(columns={
                    "gruppe_name": t("exclusion.table.group"), "planstellen": t("exclusion.layout.positions"),
                    "soll_mak": t("exclusion.table.target_fte"), "ist_mak": t("exclusion.table.current_fte")}),
                    hide_index=True, use_container_width=True)

    with detail_tab:
        st.caption(t("exclusion.layout.applied_only"))
        all_group_opts = dict(zip(df_stats["gruppe_name"], df_stats["gruppe_key"]))
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
                status = t("exclusion.layout.selected") if selected_key in ex_keys else t("exclusion.layout.not_selected")
                status_color = COLORS["accent_red"] if selected_key in ex_keys else COLORS["accent_green"]
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
            if selected_key in ex_keys:
                st.caption(t("exclusion.layout.demo_note"))
            _render_demo_profile(snapshot_df[group_mask])

            with st.expander(t("exclusion.expander.rows"), expanded=False):
                _render_group_entry_analysis(snapshot_df[group_mask], selected_key)


if not globals().get("_UNIT_TESTING", False):
    main()
