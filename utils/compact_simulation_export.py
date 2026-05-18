"""
Excel export helpers for the Kompakt plus simulation page.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from dataloader.kpi_engine import get_unique_employees
from dataloader.mak_allocation import (
    build_mak_allocation_audit,
    build_mak_allocation_validation_summary,
)
from utils.audit_helpers import (
    build_65plus_audit,
    build_65plus_decision_list,
    build_azubi_takeover_decision_list,
    build_azubi_takeover_target_audit,
    build_mak_decision_list,
    build_mak_person_audit,
    build_mak_policy_impact,
    build_sonstiges_audit,
    build_validation_checks,
)
from utils.status_quo_baseline import (
    build_forecast_vs_status_quo_jobfamily,
    build_status_quo_baseline_scope_check,
    build_status_quo_jobfamily_summary,
    build_status_quo_mak_allocation_audit,
    build_status_quo_missing_category_summary,
    build_status_quo_missing_persons_audit,
    build_status_quo_snapshot,
    build_status_quo_validation_checks,
)


EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DEFAULT_DEMOGRAPHIC_DIMENSIONS = (
    "Geschlecht",
    "Alterskohorte",
    "Beschäftigungsstatus",
    "Beschäftigungsgrad_Kat",
    "Ausbildung",
    "Betriebszugehörigkeit_Bin",
    "ATZ_Status",
)


def _display_value(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, dict):
        return f"{len(value)} Einträge"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else ""
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
        return ""
    return str(value)


def _flatten_rows(section: str, value: Any, prefix: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, dict):
        if not value:
            rows.append({"Bereich": section, "Parameter": prefix or "(leer)", "Wert": ""})
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten_rows(section, child, child_prefix))
        return rows

    if isinstance(value, (list, tuple, set)):
        rows.append({"Bereich": section, "Parameter": prefix, "Wert": _display_value(list(value))})
        return rows

    rows.append({"Bereich": section, "Parameter": prefix, "Wert": _display_value(value)})
    return rows


def build_parameter_sheet(
    *,
    abgaenge_params: dict[str, Any] | None,
    zugaenge_params: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    active_cluster_source: Any | None = None,
) -> pd.DataFrame:
    """Builds the parameter/documentation sheet for the simulation export."""
    metadata = metadata or {}
    rows: list[dict[str, str]] = [
        {
            "Bereich": "Export",
            "Parameter": "export_zeitpunkt",
            "Wert": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        }
    ]

    rows.extend(_flatten_rows("Simulation", metadata))

    if active_cluster_source is not None:
        cluster_rows = {
            "mode": getattr(active_cluster_source, "mode", ""),
            "subtype": getattr(active_cluster_source, "subtype", ""),
            "status": getattr(active_cluster_source, "status", ""),
            "display_label": getattr(active_cluster_source, "display_label", ""),
            "source_path": getattr(active_cluster_source, "source_path", ""),
            "persisted_local_path": getattr(active_cluster_source, "persisted_local_path", ""),
            "source_signature": getattr(active_cluster_source, "source_signature", ""),
            "oe_mapping_count": getattr(active_cluster_source, "oe_mapping_count", ""),
            "jf_mapping_count": getattr(active_cluster_source, "jf_mapping_count", ""),
        }
        rows.extend(_flatten_rows("OE-Cluster", cluster_rows))

    rows.extend(_flatten_rows("Abgänge", abgaenge_params or {}))
    rows.extend(_flatten_rows("Zugänge", zugaenge_params or {}))
    return pd.DataFrame(rows, columns=["Bereich", "Parameter", "Wert"])


def _employee_view(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if "MAK_Reporting" in df.columns or "EUR_Reporting" in df.columns:
        emp = df[df["Is_Vacant"] != True].copy() if "Is_Vacant" in df.columns else df.copy()
    else:
        emp = get_unique_employees(df).copy()
    if "Jobfamily" not in emp.columns and "JF-Cluster" in emp.columns:
        emp["Jobfamily"] = emp["JF-Cluster"]
    if "Jobfamily" not in emp.columns:
        emp["Jobfamily"] = "(ohne Job-Family)"
    emp["Jobfamily"] = emp["Jobfamily"].fillna("(ohne Job-Family)").astype(str)
    return emp


def _mak_column(emp: pd.DataFrame) -> str | None:
    for col in ("MAK_Reporting", "MAK_Calculated", "mak", "MAK", "FTE_person"):
        if col in emp.columns:
            return col
    return None


def _eur_column(emp: pd.DataFrame) -> str | None:
    for col in ("EUR_Reporting", "Total_Cost_Year"):
        if col in emp.columns:
            return col
    return None


def _numeric_sum(series: pd.Series | None) -> float:
    if series is None:
        return 0.0
    return float(pd.to_numeric(series, errors="coerce").fillna(0).sum())


def _sum_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> float:
    for col in candidates:
        if col in df.columns:
            return _numeric_sum(df[col])
    return 0.0


def _dimension_values_for_export(series: pd.Series) -> pd.Series:
    """Normalizes dimension values without mutating categorical categories."""
    return series.astype(object).where(series.notna(), "(unbekannt)").astype(str)


def build_jobfamily_summary(prepared_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregates heads, MAK and EUR by Jobfamily on unique employee level."""
    emp = _employee_view(prepared_df)
    if emp.empty:
        return pd.DataFrame(
            columns=["Jobfamily", "Köpfe", "MAK", "EUR", "Köpfe Anteil", "MAK Anteil", "EUR Anteil"]
        )

    mak_col = _mak_column(emp)
    eur_col = _eur_column(emp)
    rows = []
    for jobfamily, group in emp.groupby("Jobfamily", dropna=False, observed=True):
        heads = int(group["PersNr"].nunique()) if "PersNr" in group.columns else int(len(group))
        mak = _numeric_sum(group[mak_col]) if mak_col else 0.0
        eur = _numeric_sum(group[eur_col]) if eur_col else 0.0
        rows.append({"Jobfamily": jobfamily, "Köpfe": heads, "MAK": mak, "EUR": eur})

    summary = pd.DataFrame(rows).sort_values(["Köpfe", "MAK", "EUR"], ascending=False)
    total_heads = float(summary["Köpfe"].sum())
    total_mak = float(summary["MAK"].sum())
    total_eur = float(summary["EUR"].sum())
    summary["Köpfe Anteil"] = summary["Köpfe"] / total_heads if total_heads else 0.0
    summary["MAK Anteil"] = summary["MAK"] / total_mak if total_mak else 0.0
    summary["EUR Anteil"] = summary["EUR"] / total_eur if total_eur else 0.0
    return summary.reset_index(drop=True)


def build_jobfamily_demographics(
    prepared_df: pd.DataFrame,
    dimensions: Iterable[str] = DEFAULT_DEMOGRAPHIC_DIMENSIONS,
) -> pd.DataFrame:
    """Builds a long-format demographic breakdown by Jobfamily."""
    emp = _employee_view(prepared_df)
    if emp.empty:
        return pd.DataFrame(
            columns=[
                "Jobfamily",
                "Dimension",
                "Ausprägung",
                "Köpfe",
                "MAK",
                "EUR",
                "Köpfe Anteil in Jobfamily",
                "MAK Anteil in Jobfamily",
                "EUR Anteil in Jobfamily",
            ]
        )

    mak_col = _mak_column(emp)
    eur_col = _eur_column(emp)
    available_dimensions = [dim for dim in dimensions if dim in emp.columns]
    rows = []

    for jobfamily, jf_group in emp.groupby("Jobfamily", dropna=False, observed=True):
        total_heads = int(jf_group["PersNr"].nunique()) if "PersNr" in jf_group.columns else int(len(jf_group))
        total_mak = _numeric_sum(jf_group[mak_col]) if mak_col else 0.0
        total_eur = _numeric_sum(jf_group[eur_col]) if eur_col else 0.0

        for dimension in available_dimensions:
            work = jf_group.copy()
            work[dimension] = _dimension_values_for_export(work[dimension])
            for value, group in work.groupby(dimension, dropna=False, observed=True):
                heads = int(group["PersNr"].nunique()) if "PersNr" in group.columns else int(len(group))
                mak = _numeric_sum(group[mak_col]) if mak_col else 0.0
                eur = _numeric_sum(group[eur_col]) if eur_col else 0.0
                rows.append(
                    {
                        "Jobfamily": jobfamily,
                        "Dimension": dimension,
                        "Ausprägung": value,
                        "Köpfe": heads,
                        "MAK": mak,
                        "EUR": eur,
                        "Köpfe Anteil in Jobfamily": heads / total_heads if total_heads else 0.0,
                        "MAK Anteil in Jobfamily": mak / total_mak if total_mak else 0.0,
                        "EUR Anteil in Jobfamily": eur / total_eur if total_eur else 0.0,
                    }
                )

    if not rows:
        return pd.DataFrame(
            columns=[
                "Jobfamily",
                "Dimension",
                "Ausprägung",
                "Köpfe",
                "MAK",
                "EUR",
                "Köpfe Anteil in Jobfamily",
                "MAK Anteil in Jobfamily",
                "EUR Anteil in Jobfamily",
            ]
        )

    return pd.DataFrame(rows).sort_values(
        ["Jobfamily", "Dimension", "Köpfe", "MAK", "EUR"],
        ascending=[True, True, False, False, False],
    ).reset_index(drop=True)


def _active_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "Is_Vacant" in out.columns:
        out = out[out["Is_Vacant"] != True].copy()
    if "PersNr" in out.columns:
        out = out[out["PersNr"].notna() & out["PersNr"].astype(str).str.strip().ne("")].copy()
    return out


def _total_metrics(df: pd.DataFrame) -> dict[str, float]:
    active = _active_rows(df)
    heads = int(active["PersNr"].nunique()) if "PersNr" in active.columns and not active.empty else 0
    return {
        "Köpfe": heads,
        "MAK_Reporting": _sum_col(active, ("MAK_Reporting", "MAK_Calculated", "mak", "MAK")),
        "MAK_Technical": _sum_col(active, ("MAK_Technical_Uncorrected", "MAK_Calculated", "mak", "MAK")),
        "EUR_Reporting": _sum_col(active, ("EUR_Reporting", "Total_Cost_Year")),
    }


def _readme_sheet(metadata: dict[str, Any] | None, status_quo_date: pd.Timestamp | None) -> pd.DataFrame:
    metadata = metadata or {}
    rows = [
        ("Status quo Stichtag", _display_value(status_quo_date or metadata.get("base_date", ""))),
        ("Forecast Zielstichtag", _display_value(metadata.get("target_date", ""))),
        ("Planstellenbasierte Steuerungssicht", "Status quo und Forecast auf aktivem Planstellen-Snapshot; Standard für reguläre Job-Family-Steuerung."),
        ("Vollständige Belegschaftssicht", "Status quo inklusive aktiver Personen aus Mitarbeiter.xlsx außerhalb des aktiven Planstellen-Snapshots."),
        ("MAK_Technical_Uncorrected", "Technische, zeilenbasierte MAK vor personenseitiger Planstellen-Allokation."),
        ("MAK_Reporting", "Management-Sicht: Personen-MAK wird über Planstellen verteilt und nicht mehrfach gezählt."),
        ("Management KPIs", "Alle Management-Auswertungen im Export nutzen MAK_Reporting."),
        ("264 Personen außerhalb Steuerungsscope", "Diese Personen werden nicht künstlich regulären Job Families zugeordnet, sondern separat auditiert."),
        ("EUR für Missing Persons", "EUR wird für diese Personen nur verwendet, wenn belastbar ableitbar; sonst bleibt die Sicht gekennzeichnet."),
    ]
    return pd.DataFrame(rows, columns=["Thema", "Beschreibung"])


def _executive_summary_sheet(status_quo_snapshot: pd.DataFrame, forecast_df: pd.DataFrame, missing_audit: pd.DataFrame) -> pd.DataFrame:
    status = _total_metrics(status_quo_snapshot)
    forecast = _total_metrics(forecast_df)
    missing_heads = int(missing_audit["PersNr"].nunique()) if missing_audit is not None and not missing_audit.empty else 0
    status_heads = int(status["Köpfe"])
    full_heads = status_heads + missing_heads
    rows = [
        ("A. Status quo planstellenbasiert", "Köpfe", status_heads),
        ("A. Status quo planstellenbasiert", "MAK Reporting", status["MAK_Reporting"]),
        ("A. Status quo planstellenbasiert", "MAK Technical", status["MAK_Technical"]),
        ("A. Status quo planstellenbasiert", "MAK Delta Technical minus Reporting", status["MAK_Technical"] - status["MAK_Reporting"]),
        ("A. Status quo planstellenbasiert", "EUR Reporting", status["EUR_Reporting"]),
        ("B. Forecast planstellenbasiert", "Köpfe", forecast["Köpfe"]),
        ("B. Forecast planstellenbasiert", "MAK Reporting", forecast["MAK_Reporting"]),
        ("B. Forecast planstellenbasiert", "MAK Technical", forecast["MAK_Technical"]),
        ("B. Forecast planstellenbasiert", "MAK Delta Technical minus Reporting", forecast["MAK_Technical"] - forecast["MAK_Reporting"]),
        ("B. Forecast planstellenbasiert", "EUR Reporting", forecast["EUR_Reporting"]),
        ("C. Veränderung planstellenbasiert", "Delta Köpfe", forecast["Köpfe"] - status_heads),
        ("C. Veränderung planstellenbasiert", "Delta MAK Reporting", forecast["MAK_Reporting"] - status["MAK_Reporting"]),
        ("C. Veränderung planstellenbasiert", "Delta EUR Reporting", forecast["EUR_Reporting"] - status["EUR_Reporting"]),
        ("D. Full Employee Scope", "Aktive Personen laut Mitarbeiterdaten", full_heads),
        ("D. Full Employee Scope", "Davon im planstellenbasierten Steuerungsbestand", status_heads),
        ("D. Full Employee Scope", "Separat auditierte Personen ohne aktive Planstellenzuordnung", missing_heads),
        ("D. Full Employee Scope", "Abdeckungsquote", status_heads / full_heads if full_heads else 1.0),
        ("E. Offene Fachentscheidungen", "Azubi Übernahmen ohne Ziel Jobfamily", "prüfen"),
        ("E. Offene Fachentscheidungen", "Sonstiges Mapping", "prüfen"),
        ("E. Offene Fachentscheidungen", "65+ Weiterbeschäftigungsflag", "prüfen"),
        ("E. Offene Fachentscheidungen", "Personen außerhalb Steuerungsscope", "fachliche Behandlung festlegen"),
    ]
    return pd.DataFrame(rows, columns=["Block", "Kennzahl", "Wert"])


def _scope_check_sheet(scope_df: pd.DataFrame, missing_category_summary: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if scope_df is not None and not scope_df.empty:
        for _, row in scope_df.iterrows():
            rows.append({"Abschnitt": "Baseline Varianten", **row.to_dict()})
    if missing_category_summary is not None and not missing_category_summary.empty:
        for _, row in missing_category_summary.iterrows():
            rows.append({
                "Abschnitt": "Missing Kategorien",
                "Kategorie": row.get("missing_category"),
                "Köpfe": row.get("Köpfe"),
                "MAK Source": row.get("Personen_MAK_Source"),
                "Anteil": row.get("Anteil_an_missing"),
                "empfohlene Behandlung": row.get("empfohlene_Behandlung"),
                "in Jobfamily Vergleich enthalten": "nein",
            })
    return pd.DataFrame(rows)


def _full_workforce_comparison(status_quo_snapshot: pd.DataFrame, forecast_df: pd.DataFrame, missing_audit: pd.DataFrame) -> pd.DataFrame:
    status = _active_rows(status_quo_snapshot)
    forecast = _active_rows(forecast_df)
    if status.empty:
        status_full = pd.DataFrame(columns=["Vergleichs_Jobfamily", "Köpfe_StatusQuo_Full", "MAK_StatusQuo_Full", "EUR_StatusQuo_Full"])
    else:
        status["_Headcount_Reporting"] = 1.0 / status.groupby("PersNr")["PersNr"].transform("size").replace(0, 1)
        if "MAK_Reporting" not in status.columns:
            status["MAK_Reporting"] = status[_mak_column(status)] if _mak_column(status) else 0.0
        if "EUR_Reporting" not in status.columns:
            status["EUR_Reporting"] = status[_eur_column(status)] if _eur_column(status) else 0.0
        status_full = (
            status.assign(Vergleichs_Jobfamily=status["Jobfamily"].fillna("(ohne Job-Family)").astype(str))
            .groupby("Vergleichs_Jobfamily", dropna=False)
            .agg(Köpfe_StatusQuo_Full=("_Headcount_Reporting", "sum"), MAK_StatusQuo_Full=("MAK_Reporting", "sum"), EUR_StatusQuo_Full=("EUR_Reporting", "sum"))
            .reset_index()
        )
    if missing_audit is not None and not missing_audit.empty:
        miss = missing_audit.copy()
        miss["Vergleichs_Jobfamily"] = "Ohne Planstellenzuordnung: " + miss["missing_category"].astype(str)
        miss["Personen_MAK_Source"] = pd.to_numeric(miss["Personen_MAK_Source"], errors="coerce").fillna(0.0)
        missing_part = miss.groupby("Vergleichs_Jobfamily", dropna=False).agg(
            Köpfe_StatusQuo_Full=("PersNr", "nunique"),
            MAK_StatusQuo_Full=("Personen_MAK_Source", "sum"),
        ).reset_index()
        missing_part["EUR_StatusQuo_Full"] = pd.NA
        status_full = pd.concat([status_full, missing_part], ignore_index=True)
    if forecast.empty:
        forecast_summary = pd.DataFrame(columns=["Vergleichs_Jobfamily", "Köpfe_Forecast", "MAK_Forecast", "EUR_Forecast"])
    else:
        forecast["_Headcount_Reporting"] = 1.0 / forecast.groupby("PersNr")["PersNr"].transform("size").replace(0, 1)
        if "MAK_Reporting" not in forecast.columns:
            forecast["MAK_Reporting"] = forecast[_mak_column(forecast)] if _mak_column(forecast) else 0.0
        if "EUR_Reporting" not in forecast.columns:
            forecast["EUR_Reporting"] = forecast[_eur_column(forecast)] if _eur_column(forecast) else 0.0
        forecast_summary = (
            forecast.assign(Vergleichs_Jobfamily=forecast["Jobfamily"].fillna("(ohne Job-Family)").astype(str))
            .groupby("Vergleichs_Jobfamily", dropna=False)
            .agg(Köpfe_Forecast=("_Headcount_Reporting", "sum"), MAK_Forecast=("MAK_Reporting", "sum"), EUR_Forecast=("EUR_Reporting", "sum"))
            .reset_index()
        )
    out = status_full.merge(forecast_summary, on="Vergleichs_Jobfamily", how="outer").fillna(
        {"Köpfe_StatusQuo_Full": 0, "MAK_StatusQuo_Full": 0, "Köpfe_Forecast": 0, "MAK_Forecast": 0, "EUR_Forecast": 0}
    )
    out["Scope_Status"] = out["Vergleichs_Jobfamily"].astype(str).str.startswith("Ohne Planstellenzuordnung").map({True: "außerhalb_planstellen_scope", False: "planstellen_scope"})
    out["Delta_Köpfe"] = out["Köpfe_Forecast"] - out["Köpfe_StatusQuo_Full"]
    out["Delta_MAK"] = out["MAK_Forecast"] - out["MAK_StatusQuo_Full"]
    out["Delta_EUR"] = out["EUR_Forecast"] - pd.to_numeric(out["EUR_StatusQuo_Full"], errors="coerce")
    out["Vergleichbarkeit_Flag"] = out["Scope_Status"].eq("planstellen_scope").map({True: "vergleichbar", False: "nur_scope_audit"})
    out["Scope_Interpretation"] = out["Scope_Status"].map({
        "planstellen_scope": "Reguläre planstellenbasierte Vergleichssicht.",
        "außerhalb_planstellen_scope": "Status-quo-Sonderkategorie ohne Forecast-Gegenstück; keine reguläre Job-Family-Zuordnung.",
    })
    columns = [
        "Vergleichs_Jobfamily", "Scope_Status", "Köpfe_StatusQuo_Full", "Köpfe_Forecast", "Delta_Köpfe",
        "MAK_StatusQuo_Full", "MAK_Forecast", "Delta_MAK", "EUR_StatusQuo_Full", "EUR_Forecast",
        "Delta_EUR", "Vergleichbarkeit_Flag", "Scope_Interpretation",
    ]
    return out[columns].reset_index(drop=True)


def _jobfamily_reporting_summary(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    active = _active_rows(df)
    columns = [
        "Jobfamily",
        f"Köpfe_{suffix}",
        f"MAK_{suffix}",
        f"EUR_{suffix}",
        f"Anteil_Köpfe_{suffix}",
        f"Anteil_MAK_{suffix}",
    ]
    if active.empty:
        return pd.DataFrame(columns=columns)
    work = active.copy()
    work["Jobfamily"] = work.get("Jobfamily", pd.Series("(ohne Job-Family)", index=work.index)).fillna("(ohne Job-Family)").astype(str)
    if "PersNr" not in work.columns:
        work["PersNr"] = work.index.astype(str)
    row_counts = work.groupby("PersNr")["PersNr"].transform("size").replace(0, 1)
    work["_Headcount_Reporting"] = 1.0 / row_counts
    if "MAK_Reporting" not in work.columns:
        work["MAK_Reporting"] = work[_mak_column(work)] if _mak_column(work) else 0.0
    if "EUR_Reporting" not in work.columns:
        work["EUR_Reporting"] = work[_eur_column(work)] if _eur_column(work) else 0.0
    out = work.groupby("Jobfamily", dropna=False).agg(
        **{
            f"Köpfe_{suffix}": ("_Headcount_Reporting", "sum"),
            f"MAK_{suffix}": ("MAK_Reporting", "sum"),
            f"EUR_{suffix}": ("EUR_Reporting", "sum"),
        }
    ).reset_index()
    total_heads = out[f"Köpfe_{suffix}"].sum()
    total_mak = out[f"MAK_{suffix}"].sum()
    out[f"Anteil_Köpfe_{suffix}"] = out[f"Köpfe_{suffix}"] / total_heads if total_heads else 0.0
    out[f"Anteil_MAK_{suffix}"] = out[f"MAK_{suffix}"] / total_mak if total_mak else 0.0
    return out[columns]


def _comparison_interpretation(row: pd.Series) -> str:
    if str(row.get("Jobfamily", "")).lower() == "sonstiges":
        return "sonstiges_mapping_pruefen"
    if float(row.get("Delta_Köpfe_%", 0.0)) >= 0.15 and float(row.get("Delta_Köpfe", 0.0)) >= 3:
        return "starkes_wachstum"
    if float(row.get("Delta_Köpfe_%", 0.0)) <= -0.15 and float(row.get("Delta_Köpfe", 0.0)) <= -3:
        return "starker_rueckgang"
    if float(row.get("Delta_Anteil_MAK_pp", 0.0)) >= 3.0:
        return "strukturanteil_steigt"
    if float(row.get("Delta_Anteil_MAK_pp", 0.0)) <= -3.0:
        return "strukturanteil_sinkt"
    return "unauffaellig"


def _jobfamily_reporting_comparison(status_quo_snapshot: pd.DataFrame, forecast_df: pd.DataFrame) -> pd.DataFrame:
    status = _jobfamily_reporting_summary(status_quo_snapshot, "StatusQuo")
    forecast = _jobfamily_reporting_summary(forecast_df, "Forecast")
    out = status.merge(forecast, on="Jobfamily", how="outer").fillna(0)
    out["Delta_Köpfe"] = out["Köpfe_Forecast"] - out["Köpfe_StatusQuo"]
    out["Delta_Köpfe_%"] = out["Delta_Köpfe"] / out["Köpfe_StatusQuo"].where(out["Köpfe_StatusQuo"].ne(0), 1)
    out["Delta_MAK"] = out["MAK_Forecast"] - out["MAK_StatusQuo"]
    out["Delta_MAK_%"] = out["Delta_MAK"] / out["MAK_StatusQuo"].where(out["MAK_StatusQuo"].ne(0), 1)
    out["Delta_EUR"] = out["EUR_Forecast"] - out["EUR_StatusQuo"]
    out["Delta_EUR_%"] = out["Delta_EUR"] / out["EUR_StatusQuo"].where(out["EUR_StatusQuo"].ne(0), 1)
    out["Delta_Anteil_Köpfe_pp"] = (out["Anteil_Köpfe_Forecast"] - out["Anteil_Köpfe_StatusQuo"]) * 100.0
    out["Delta_Anteil_MAK_pp"] = (out["Anteil_MAK_Forecast"] - out["Anteil_MAK_StatusQuo"]) * 100.0
    out["Interpretation_Flag"] = out.apply(_comparison_interpretation, axis=1)
    cols = [
        "Jobfamily",
        "Köpfe_StatusQuo",
        "Köpfe_Forecast",
        "Delta_Köpfe",
        "Delta_Köpfe_%",
        "MAK_StatusQuo",
        "MAK_Forecast",
        "Delta_MAK",
        "Delta_MAK_%",
        "EUR_StatusQuo",
        "EUR_Forecast",
        "Delta_EUR",
        "Delta_EUR_%",
        "Anteil_Köpfe_StatusQuo",
        "Anteil_Köpfe_Forecast",
        "Delta_Anteil_Köpfe_pp",
        "Anteil_MAK_StatusQuo",
        "Anteil_MAK_Forecast",
        "Delta_Anteil_MAK_pp",
        "Interpretation_Flag",
    ]
    return out[cols].sort_values("Delta_Köpfe", ascending=False).reset_index(drop=True)


def _demography_compare(status_df: pd.DataFrame, forecast_df: pd.DataFrame, *, view_name: str, group_col: str = "Jobfamily", missing_audit: pd.DataFrame | None = None) -> pd.DataFrame:
    dims = ["Geschlecht", "Alterskohorte", "Beschäftigungsgrad_Kat", "Betriebszugehörigkeit_Bin", "Ausbildung"]
    status = _active_rows(status_df)
    forecast = _active_rows(forecast_df)
    if group_col not in status.columns and "Jobfamily" in status.columns:
        status[group_col] = status["Jobfamily"]
    if group_col not in forecast.columns and "Jobfamily" in forecast.columns:
        forecast[group_col] = forecast["Jobfamily"]
    if missing_audit is not None and not missing_audit.empty and group_col == "Vergleichs_Jobfamily":
        miss = missing_audit.copy()
        miss["Vergleichs_Jobfamily"] = "Ohne Planstellenzuordnung: " + miss["missing_category"].astype(str)
        miss["MAK_Reporting"] = pd.to_numeric(miss["Personen_MAK_Source"], errors="coerce").fillna(0.0)
        status = pd.concat([status, miss], ignore_index=True, sort=False)
    parts = []
    available_dims = [d for d in dims if d in status.columns or d in forecast.columns]
    for dim in available_dims:
        for frame_name, frame in (("StatusQuo", status), ("Forecast", forecast)):
            if frame.empty:
                continue
            work = frame.copy()
            if dim not in work.columns:
                work[dim] = "(unbekannt)"
            if group_col not in work.columns:
                work[group_col] = "(ohne Job-Family)"
            work[dim] = _dimension_values_for_export(work[dim])
            work[group_col] = work[group_col].fillna("(ohne Job-Family)").astype(str)
            mak_col = "MAK_Reporting" if "MAK_Reporting" in work.columns else _mak_column(work)
            grouped = work.groupby([group_col, dim], dropna=False).agg(
                Köpfe=("PersNr", "nunique"),
                MAK=(mak_col, "sum") if mak_col else ("PersNr", "size"),
            ).reset_index()
            grouped["Vergleichssicht"] = view_name
            grouped["Dimension"] = dim
            grouped["Ausprägung"] = grouped[dim]
            grouped["Frame"] = frame_name
            parts.append(grouped[["Vergleichssicht", group_col, "Dimension", "Ausprägung", "Frame", "Köpfe", "MAK"]])
    if not parts:
        return pd.DataFrame()
    long = pd.concat(parts, ignore_index=True)
    pivot = long.pivot_table(index=["Vergleichssicht", group_col, "Dimension", "Ausprägung"], columns="Frame", values=["Köpfe", "MAK"], aggfunc="sum", fill_value=0)
    pivot.columns = [f"{metric}_{frame}" for metric, frame in pivot.columns]
    out = pivot.reset_index()
    for col in ("Köpfe_StatusQuo", "Köpfe_Forecast", "MAK_StatusQuo", "MAK_Forecast"):
        if col not in out.columns:
            out[col] = 0.0
    out["Delta_Köpfe"] = out["Köpfe_Forecast"] - out["Köpfe_StatusQuo"]
    out["Delta_MAK"] = out["MAK_Forecast"] - out["MAK_StatusQuo"]
    totals = out.groupby(["Vergleichssicht", group_col, "Dimension"], dropna=False).agg(
        total_heads_sq=("Köpfe_StatusQuo", "sum"), total_heads_fc=("Köpfe_Forecast", "sum"),
        total_mak_sq=("MAK_StatusQuo", "sum"), total_mak_fc=("MAK_Forecast", "sum"),
    ).reset_index()
    out = out.merge(totals, on=["Vergleichssicht", group_col, "Dimension"], how="left")
    out["Anteil_Köpfe_StatusQuo"] = out["Köpfe_StatusQuo"] / out["total_heads_sq"].where(out["total_heads_sq"].ne(0), 1)
    out["Anteil_Köpfe_Forecast"] = out["Köpfe_Forecast"] / out["total_heads_fc"].where(out["total_heads_fc"].ne(0), 1)
    out["Delta_Anteil_Köpfe_pp"] = (out["Anteil_Köpfe_Forecast"] - out["Anteil_Köpfe_StatusQuo"]) * 100.0
    out["Anteil_MAK_StatusQuo"] = out["MAK_StatusQuo"] / out["total_mak_sq"].where(out["total_mak_sq"].ne(0), 1)
    out["Anteil_MAK_Forecast"] = out["MAK_Forecast"] / out["total_mak_fc"].where(out["total_mak_fc"].ne(0), 1)
    out["Delta_Anteil_MAK_pp"] = (out["Anteil_MAK_Forecast"] - out["Anteil_MAK_StatusQuo"]) * 100.0
    out["Auffälligkeitsflag"] = "unauffällig"
    out.loc[out["Delta_Köpfe"].abs().ge(10) | out["Delta_Anteil_Köpfe_pp"].abs().ge(10), "Auffälligkeitsflag"] = "auffällig"
    cols = [
        "Vergleichssicht", group_col, "Dimension", "Ausprägung", "Köpfe_StatusQuo", "Köpfe_Forecast",
        "Delta_Köpfe", "MAK_StatusQuo", "MAK_Forecast", "Delta_MAK", "Anteil_Köpfe_StatusQuo",
        "Anteil_Köpfe_Forecast", "Delta_Anteil_Köpfe_pp", "Anteil_MAK_StatusQuo",
        "Anteil_MAK_Forecast", "Delta_Anteil_MAK_pp", "Auffälligkeitsflag",
    ]
    return out[cols].reset_index(drop=True)


def _demography_findings(*tables: pd.DataFrame) -> pd.DataFrame:
    frames = [t[t["Auffälligkeitsflag"].eq("auffällig")].copy() for t in tables if t is not None and not t.empty and "Auffälligkeitsflag" in t.columns]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["_score"] = out["Delta_Köpfe"].abs() + out["Delta_Anteil_Köpfe_pp"].abs()
    return out.sort_values("_score", ascending=False).drop(columns=["_score"]).head(100).reset_index(drop=True)


def _mak_allocation_sheet(status_quo_snapshot: pd.DataFrame, forecast_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scope, df in (("Status quo", status_quo_snapshot), ("Forecast", forecast_df)):
        checks = build_mak_allocation_validation_summary(df)
        for _, row in checks.iterrows():
            rows.append({"Scope": scope, "Typ": "Summary", **row.to_dict()})
        audit = build_mak_allocation_audit(df)
        if not audit.empty:
            detail = audit[pd.to_numeric(audit["MAK_Adjustment_Delta"], errors="coerce").fillna(0).gt(1e-6)].copy()
            for _, row in detail.iterrows():
                rows.append({
                    "Scope": scope, "Typ": "Detail Technical > Reporting", "PersNr": row.get("PersNr"),
                    "Jobfamily": row.get("Jobfamily"), "Planstelle": row.get("Planstelle"),
                    "Personen_MAK": row.get("Personen_MAK"),
                    "MAK_Technical_Uncorrected": row.get("MAK_Technical_Uncorrected"),
                    "MAK_Reporting": row.get("MAK_Reporting"),
                    "MAK_Adjustment_Delta": row.get("MAK_Adjustment_Delta"),
                    "MAK_Allocation_Flag": row.get("MAK_Allocation_Flag"),
                })
    return pd.DataFrame(rows)


def _missing_persons_sheet(missing_audit: pd.DataFrame) -> pd.DataFrame:
    if missing_audit is None or missing_audit.empty:
        return pd.DataFrame()
    cols = ["PersNr", "Vertragsart", "MitarbGruppenbez.", "BsGrd", "Personen_MAK_Source", "missing_category", "mögliche_Planstellen_Treffer", "Grund_fehlt_im_Snapshot", "recommended_action"]
    return missing_audit[[col for col in cols if col in missing_audit.columns]].copy()


def _combined_audit_sheet(*named_tables: tuple[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for scope, table in named_tables:
        if table is not None and not table.empty:
            part = table.copy()
            part.insert(0, "Quelle", scope)
            frames.append(part)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _validation_sheet(validation_df: pd.DataFrame, status_quo_snapshot: pd.DataFrame, forecast_df: pd.DataFrame, scope_df: pd.DataFrame, missing_audit: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if validation_df is not None and not validation_df.empty:
        v = validation_df.copy()
        v.insert(0, "Scope", "Forecast")
        frames.append(v)
    for scope, df in (("Status quo", status_quo_snapshot), ("Forecast allocation", forecast_df)):
        checks = build_mak_allocation_validation_summary(df)
        if not checks.empty:
            checks.insert(0, "Scope", scope)
            frames.append(checks)
    rows = []
    if scope_df is not None and not scope_df.empty:
        baseline = scope_df[scope_df["Variante"].eq("Planstellen_Snapshot_Baseline")]
        if not baseline.empty:
            rows.append({"Scope": "Scope Check", "Check": "Abdeckungsquote", "Wert": baseline.iloc[0].get("Abdeckungsquote"), "Status": "WARN"})
    rows.append({"Scope": "Scope Check", "Check": "Missing Persons Count", "Wert": int(missing_audit["PersNr"].nunique()) if missing_audit is not None and not missing_audit.empty else 0, "Status": "WARN" if missing_audit is not None and not missing_audit.empty else "OK"})
    frames.append(pd.DataFrame(rows))
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def build_compact_simulation_export_bytes(
    *,
    prepared_df: pd.DataFrame,
    abgaenge_params: dict[str, Any] | None,
    zugaenge_params: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    active_cluster_source: Any | None = None,
    audit_tables: dict[str, pd.DataFrame] | None = None,
    status_quo_df: pd.DataFrame | None = None,
    status_quo_date: pd.Timestamp | None = None,
    status_quo_mitarbeiter_df: pd.DataFrame | None = None,
    status_quo_planstellen_df: pd.DataFrame | None = None,
) -> bytes:
    """Returns an XLSX workbook with parameters and Jobfamily result tables."""
    parameter_df = build_parameter_sheet(
        abgaenge_params=abgaenge_params,
        zugaenge_params=zugaenge_params,
        metadata=metadata,
        active_cluster_source=active_cluster_source,
    )
    summary_df = build_jobfamily_summary(prepared_df)
    demographics_df = build_jobfamily_demographics(prepared_df)
    status_quo_snapshot = (
        build_status_quo_snapshot(status_quo_df, status_quo_date or pd.Timestamp.today())
        if status_quo_df is not None and not status_quo_df.empty
        else pd.DataFrame()
    )
    status_quo_summary = (
        build_status_quo_jobfamily_summary(status_quo_snapshot, status_quo_date or pd.Timestamp.today())
        if not status_quo_snapshot.empty
        else pd.DataFrame()
    )
    forecast_vs_status = (
        _jobfamily_reporting_comparison(status_quo_snapshot, prepared_df)
        if not status_quo_snapshot.empty
        else pd.DataFrame()
    )
    audit_tables = dict(audit_tables or {})
    audit_tables.setdefault("MAK_Personen_Audit", build_mak_person_audit(prepared_df))
    audit_tables.setdefault("MAK_Allocation_Audit", build_mak_allocation_audit(prepared_df))
    audit_tables.setdefault("Sonstiges_Audit", build_sonstiges_audit(prepared_df))
    audit_tables.setdefault("65plus_Audit", build_65plus_audit(prepared_df, abgaenge_params))
    audit_tables.setdefault("MAK_Decision_List", build_mak_decision_list(audit_tables["MAK_Personen_Audit"]))
    audit_tables.setdefault("MAK_Policy_Impact", build_mak_policy_impact(prepared_df, audit_tables["MAK_Decision_List"]))
    audit_tables.setdefault(
        "Azubi_Takeover_Decision_List",
        build_azubi_takeover_decision_list(audit_tables.get("Azubi_Takeover_Target_Audit", pd.DataFrame())),
    )
    audit_tables.setdefault("65plus_Decision_List", build_65plus_decision_list(audit_tables["65plus_Audit"]))
    if not status_quo_snapshot.empty:
        audit_tables.setdefault("StatusQuo_MAK_Allocation_Audit", build_status_quo_mak_allocation_audit(status_quo_snapshot))
        audit_tables.setdefault("StatusQuo_Validation_Checks", build_status_quo_validation_checks(status_quo_snapshot))
        audit_tables.setdefault("StatusQuo_Sonstiges_Audit", build_sonstiges_audit(status_quo_snapshot))
        audit_tables.setdefault("StatusQuo_65plus_Audit", build_65plus_audit(status_quo_snapshot, abgaenge_params))
        mitarbeiter_for_scope = status_quo_mitarbeiter_df
        planstellen_for_scope = status_quo_planstellen_df
        if mitarbeiter_for_scope is None or planstellen_for_scope is None:
            try:
                from dataloader.loader import load_original_data

                original_data = load_original_data()
                mitarbeiter_for_scope = mitarbeiter_for_scope if mitarbeiter_for_scope is not None else original_data.get("mitarbeiter")
                planstellen_for_scope = planstellen_for_scope if planstellen_for_scope is not None else original_data.get("planstellen")
            except Exception:
                mitarbeiter_for_scope = mitarbeiter_for_scope if mitarbeiter_for_scope is not None else pd.DataFrame()
                planstellen_for_scope = planstellen_for_scope if planstellen_for_scope is not None else pd.DataFrame()
        missing_audit = build_status_quo_missing_persons_audit(
            mitarbeiter_for_scope,
            planstellen_for_scope,
            status_quo_snapshot,
            status_quo_date or pd.Timestamp.today(),
        )
        audit_tables.setdefault("StatusQuo_Missing_Persons_Audit", missing_audit)
        audit_tables.setdefault("StatusQuo_Missing_Category_Summary", build_status_quo_missing_category_summary(missing_audit))
        audit_tables.setdefault(
            "StatusQuo_Baseline_Scope_Check",
            build_status_quo_baseline_scope_check(status_quo_snapshot, prepared_df, missing_audit),
        )
    missing_audit = audit_tables.get("StatusQuo_Missing_Persons_Audit", pd.DataFrame())
    missing_category_summary = audit_tables.get("StatusQuo_Missing_Category_Summary", pd.DataFrame())
    scope_check = audit_tables.get("StatusQuo_Baseline_Scope_Check", pd.DataFrame())
    audit_tables["Jobfamily_Demografie"] = demographics_df
    validation_df = build_validation_checks(
        summary_df=summary_df,
        future_snapshot_df=prepared_df,
        audit_tables=audit_tables,
        params={"abgaenge": abgaenge_params or {}, "zugaenge": zugaenge_params or {}},
    )
    allocation_validation = build_mak_allocation_validation_summary(prepared_df)
    if not allocation_validation.empty:
        validation_df = pd.concat([validation_df, allocation_validation], ignore_index=True)

    readme_df = _readme_sheet(metadata, status_quo_date)
    executive_df = _executive_summary_sheet(status_quo_snapshot, prepared_df, missing_audit)
    scope_export_df = _scope_check_sheet(scope_check, missing_category_summary)
    full_workforce_df = _full_workforce_comparison(status_quo_snapshot, prepared_df, missing_audit)
    demografie_jf_df = _demography_compare(status_quo_snapshot, prepared_df, view_name="Planstellenbasierte Steuerungssicht")
    status_full = status_quo_snapshot.copy()
    if not status_full.empty:
        status_full["Vergleichs_Jobfamily"] = status_full["Jobfamily"].fillna("(ohne Job-Family)").astype(str)
    forecast_full = prepared_df.copy()
    if not forecast_full.empty:
        forecast_full["Vergleichs_Jobfamily"] = forecast_full["Jobfamily"].fillna("(ohne Job-Family)").astype(str)
    demografie_full_df = _demography_compare(
        status_full,
        forecast_full,
        view_name="Vollständige Belegschaftssicht",
        group_col="Vergleichs_Jobfamily",
        missing_audit=missing_audit,
    )
    demografie_auff_df = _demography_findings(demografie_jf_df, demografie_full_df)
    mak_allocation_df = _mak_allocation_sheet(status_quo_snapshot, prepared_df)
    missing_persons_export_df = _missing_persons_sheet(missing_audit)
    sonstiges_audit_export_df = _combined_audit_sheet(
        ("Status quo", audit_tables.get("StatusQuo_Sonstiges_Audit", pd.DataFrame())),
        ("Forecast", audit_tables.get("Sonstiges_Audit", pd.DataFrame())),
    )
    azubi_audit_export_df = _combined_audit_sheet(
        ("Azubi_Flow_Audit", audit_tables.get("Azubi_Flow_Audit", pd.DataFrame())),
        ("Azubi_Takeover_Target_Audit", audit_tables.get("Azubi_Takeover_Target_Audit", pd.DataFrame())),
        ("Azubi_Takeover_Decision_List", audit_tables.get("Azubi_Takeover_Decision_List", pd.DataFrame())),
    )
    age65_audit_export_df = _combined_audit_sheet(
        ("Status quo", audit_tables.get("StatusQuo_65plus_Audit", pd.DataFrame())),
        ("Forecast", audit_tables.get("65plus_Audit", pd.DataFrame())),
        ("65plus_Decision_List", audit_tables.get("65plus_Decision_List", pd.DataFrame())),
    )
    validation_export_df = _validation_sheet(validation_df, status_quo_snapshot, prepared_df, scope_check, missing_audit)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        ordered_sheets = [
            ("00_Readme", readme_df),
            ("01_Executive_Summary", executive_df),
            ("02_Scope_Check", scope_export_df),
            ("03_JF_Vergleich", forecast_vs_status),
            ("04_Full_Workforce_Vgl", full_workforce_df),
            ("05_Demografie_JF", demografie_jf_df),
            ("06_Demografie_Full", demografie_full_df),
            ("07_Demografie_Auff", demografie_auff_df),
            ("08_MAK_Allocation", mak_allocation_df),
            ("09_Missing_Persons", missing_persons_export_df),
            ("10_Sonstiges_Audit", sonstiges_audit_export_df),
            ("11_Azubi_Audit", azubi_audit_export_df),
            ("12_65plus_Audit", age65_audit_export_df),
            ("13_Validation", validation_export_df),
        ]
        for sheet_name, table in ordered_sheets:
            (table if table is not None else pd.DataFrame()).to_excel(writer, sheet_name=sheet_name[:31], index=False)

        parameter_df.to_excel(writer, sheet_name="Parameter", index=False)
        summary_df.to_excel(writer, sheet_name="Jobfamily_Summary", index=False)
        demographics_df.to_excel(writer, sheet_name="Jobfamily_Demografie", index=False)
        if not status_quo_summary.empty:
            status_quo_summary.to_excel(writer, sheet_name="StatusQuo_Jobfamily_Summary", index=False)
        if not forecast_vs_status.empty:
            forecast_vs_status.to_excel(writer, sheet_name="Forecast_vs_StatusQuo_JF", index=False)
        for sheet_name in (
            "MAK_Personen_Audit",
            "MAK_Allocation_Audit",
            "Azubi_Flow_Audit",
            "Sonstiges_Audit",
            "65plus_Audit",
            "Azubi_Takeover_Target_Audit",
            "Azubi_Takeover_Decision_List",
            "MAK_Decision_List",
            "MAK_Policy_Impact",
            "MAK_Lineage_Audit",
            "MAK_Origin_Classification",
            "MAK_Reconciliation_Bridge",
            "MAK_Deep_Dive_15_Cases",
            "MAK_Fuehrung_Vertrieb_Check",
            "MAK_Column_Consistency_Check",
            "MAK_Abgaenge_Check",
            "MAK_Zugaenge_Check",
            "MAK_Source_Row_Audit_15",
            "MAK_BsGrd_Lineage_15",
            "MAK_Source_Pattern_Classif",
            "MAK_Source_Pattern_Classification",
            "MAK_Correction_Decision_15",
            "MAK_Target_Value_Scenarios",
            "MAK_Fuehrung_Vertrieb_Decision",
            "65plus_Decision_List",
            "StatusQuo_MAK_Allocation_Audit",
            "StatusQuo_Validation_Checks",
            "StatusQuo_Baseline_Scope_Check",
            "StatusQuo_Missing_Persons_Audit",
            "StatusQuo_Missing_Category_Summary",
            "StatusQuo_Sonstiges_Audit",
            "StatusQuo_65plus_Audit",
        ):
            table = audit_tables.get(sheet_name)
            if table is not None and not table.empty:
                table.to_excel(writer, sheet_name=sheet_name[:31], index=False)
        validation_df.to_excel(writer, sheet_name="Validation_Checks", index=False)

        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for col_cells in worksheet.columns:
                max_len = max((len(str(cell.value or "")) for cell in col_cells), default=10)
                worksheet.column_dimensions[col_cells[0].column_letter].width = min(max_len + 3, 55)

    return output.getvalue()
