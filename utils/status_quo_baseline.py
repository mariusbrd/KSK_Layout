from __future__ import annotations

import pandas as pd
import streamlit as st

from dataloader.mak_allocation import (
    apply_person_mak_allocation,
    build_mak_allocation_audit,
    build_mak_allocation_validation_summary,
)


def _normalize_persnr(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
        .where(series.notna(), pd.NA)
    )


def _active(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "Is_Vacant" in out.columns:
        out = out[out["Is_Vacant"] != True].copy()
    if "PersNr" in out.columns:
        out = out[out["PersNr"].notna() & out["PersNr"].astype(str).str.strip().ne("")].copy()
    return out


def _first_existing(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _date_filter_active(mitarbeiter_df: pd.DataFrame, snapshot_date: pd.Timestamp) -> pd.DataFrame:
    if mitarbeiter_df is None or mitarbeiter_df.empty:
        return pd.DataFrame()
    work = mitarbeiter_df.copy()
    if "PersNr" in work.columns:
        work["PersNr"] = _normalize_persnr(work["PersNr"])
    snapshot_date = pd.Timestamp(snapshot_date).normalize()
    if "Eintritt" in work.columns:
        work["Eintritt"] = pd.to_datetime(work["Eintritt"], errors="coerce")
        work = work[work["Eintritt"].isna() | work["Eintritt"].le(snapshot_date)].copy()
    if "Austritt" in work.columns:
        work["Austritt"] = pd.to_datetime(work["Austritt"], errors="coerce")
        austritt_year = pd.DatetimeIndex(work["Austritt"]).year
        work.loc[austritt_year == 9999, "Austritt"] = pd.NaT
        work = work[work["Austritt"].isna() | work["Austritt"].ge(snapshot_date)].copy()
    return work


def _missing_category(row: pd.Series, plan_hits: int) -> tuple[str, str, str, str]:
    text = " ".join(
        str(row.get(col, ""))
        for col in (
            "Vertragsart",
            "MitarbGruppenbez.",
            "Beschäftigungsstatus",
            "Status kundenindividuell",
            "Ausbildung",
            "Planstelle",
            "Job",
        )
    ).strip().lower()
    if any(token in text for token in ("auszubild", "azubi", "trainee", "nachwuchs")):
        return (
            "ausbildung_oder_nachwuchs",
            "Mitarbeiter ist aktiv, aber ohne aktive Planstellenzeile im Snapshot; Nachwuchs-/Ausbildungshinweis vorhanden.",
            "Separat als Nachwuchs/Ausbildung ohne Planstellenzuordnung ausweisen oder fachlich Zielplanstelle pflegen.",
            "ausbildungs- oder nachwuchsbezogene Merkmale gefunden",
        )
    if any(token in text for token in ("ruh", "freigest", "elternzeit", "erziehungszeit", "beurlaub")):
        return (
            "ruhende_oder_freigestellte_person",
            "Mitarbeiter ist aktiv nach Stichtagslogik, hat aber ruhenden/freigestellten Status und keine aktive Planstellenzeile.",
            "Nicht in Job-Family-Baseline mischen; separat als ruhend/freigestellt ausweisen.",
            "ruhende oder freigestellte Statusmerkmale gefunden",
        )
    if "atz" in text and ("freist" in text or "fr" in text):
        return (
            "atz_freistellung",
            "ATZ-Freistellungsbezug ohne aktive Planstellenzeile.",
            "Separat als ATZ-Freistellung ausweisen und nicht künstlich mappen.",
            "ATZ-/Freistellungsmerkmal gefunden",
        )
    if any(token in text for token in ("werkstudent", "praktik", "zeitvertrag")) or " befristet " in f" {text} ":
        return (
            "sondervertrag",
            "Sondervertrag ohne aktive Planstellenzuordnung.",
            "Separat als Sondervertrag prüfen; nur mappen, wenn fachliche Planstellenzuordnung existiert.",
            "Sondervertragsmerkmal gefunden",
        )
    if not str(row.get("PersNr", "")).strip():
        return (
            "fehlender_join_key",
            "PersNr fehlt oder ist nicht nutzbar.",
            "Join-Key in Mitarbeiterdaten korrigieren.",
            "fehlende PersNr",
        )
    if plan_hits > 0:
        return (
            "planstelle_inaktiv",
            "Es gibt Planstellenzeilen zur PersNr, aber keine aktive Snapshot-Zuordnung.",
            "Planstellenstatus/Gültigkeit prüfen; nicht automatisch in Job Family aufnehmen.",
            "Planstellen-Treffer in Rohdatei, aber nicht im aktiven Snapshot",
        )
    return (
        "planstelle_nicht_in_planstellen_datei",
        "PersNr kommt in Mitarbeiterdaten vor, aber nicht in Planstellen.XLSX.",
        "Als 'Ohne aktive Planstellenzuordnung' sichtbar machen; Fachbereich entscheidet über Ergänzung.",
        "kein Planstellen-Treffer zur PersNr",
    )


@st.cache_data(show_spinner=False, max_entries=8)
def build_status_quo_missing_persons_audit(
    mitarbeiter_df: pd.DataFrame,
    planstellen_df: pd.DataFrame,
    status_quo_snapshot_df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    """Audit active Mitarbeiter rows that are outside the planstellen-led status quo snapshot."""
    columns = [
        "PersNr",
        "Name",
        "Eintritt",
        "Austritt",
        "active_status",
        "Vertragsart",
        "MitarbGruppenbez.",
        "Beschäftigungsstatus",
        "BsGrd",
        "Personen_MAK_Source",
        "Total_Cost_Year",
        "Geschlecht",
        "Alter",
        "Alterskohorte",
        "Betriebszugehörigkeit",
        "Organisationseinheit aus Mitarbeiterdatei",
        "Planstelle aus Mitarbeiterdatei",
        "Job aus Mitarbeiterdatei",
        "Kostenstelle",
        "OE oder OrgUnit",
        "Grund_fehlt_im_Snapshot",
        "missing_category",
        "recommended_action",
        "join_key_mitarbeiter",
        "join_key_planstellen",
        "mögliche_Planstellen_Treffer",
        "comment",
    ]
    if mitarbeiter_df is None or mitarbeiter_df.empty or "PersNr" not in mitarbeiter_df.columns:
        return pd.DataFrame(columns=columns)

    active_mitarbeiter = _date_filter_active(mitarbeiter_df, snapshot_date)
    if active_mitarbeiter.empty:
        return pd.DataFrame(columns=columns)

    snapshot = _active(status_quo_snapshot_df)
    snapshot_ids = (
        set(_normalize_persnr(snapshot["PersNr"]).dropna().astype(str))
        if "PersNr" in snapshot.columns and not snapshot.empty
        else set()
    )
    active_mitarbeiter = active_mitarbeiter[~active_mitarbeiter["PersNr"].astype(str).isin(snapshot_ids)].copy()
    if active_mitarbeiter.empty:
        return pd.DataFrame(columns=columns)

    plan = planstellen_df.copy() if planstellen_df is not None else pd.DataFrame()
    plan_key_col = _first_existing(plan, ("Personalnummer", "PersNr", "PersNr_Plan"))
    if plan_key_col:
        plan["_join_key"] = _normalize_persnr(plan[plan_key_col])
    else:
        plan["_join_key"] = pd.Series(pd.NA, index=plan.index)

    planstelle_col = _first_existing(plan, ("Planstelle", "Planstellenbezeichnung", "Stellenbezeichnung"))
    job_col = _first_existing(plan, ("Job", "Rolle", "Planstelle"))
    plan_lookup: dict[str, str] = {}
    if plan_key_col:
        display_cols = [col for col in (planstelle_col, job_col) if col and col in plan.columns]
        if display_cols:
            for persnr, group in plan.dropna(subset=["_join_key"]).groupby("_join_key"):
                values = []
                for col in display_cols:
                    values.extend(group[col].dropna().astype(str).str.strip().tolist())
                plan_lookup[str(persnr)] = " | ".join(sorted(set(v for v in values if v)))[:500]

    name_cols = tuple(col for col in ("Name", "Nachname", "Vorname", "Mitarbeiter", "MA Name") if col in active_mitarbeiter.columns)
    org_col = _first_existing(active_mitarbeiter, ("Organisationseinheit", "OrgUnit", "OE", "Kürzel OrgEinheit"))
    planstelle_ma_col = _first_existing(active_mitarbeiter, ("Planstelle", "Planstellenbezeichnung"))
    job_ma_col = _first_existing(active_mitarbeiter, ("Job", "Rolle", "Funktion"))
    kosten_col = _first_existing(active_mitarbeiter, ("Kostenstelle", "KST", "Kostenst."))
    status_col = _first_existing(active_mitarbeiter, ("Beschäftigungsstatus", "Status kundenindividuell", "Status"))

    rows = []
    for _, row in active_mitarbeiter.iterrows():
        persnr = str(row.get("PersNr", "")).strip()
        plan_hits = int(plan["_join_key"].astype(str).eq(persnr).sum()) if "_join_key" in plan.columns else 0
        category, reason, action, comment = _missing_category(row, plan_hits)
        bsgrd = pd.to_numeric(pd.Series([row.get("BsGrd")]), errors="coerce").iloc[0]
        person_mak = float(bsgrd) / 100.0 if pd.notna(bsgrd) else pd.NA
        name = " ".join(str(row.get(col, "")).strip() for col in name_cols if pd.notna(row.get(col, pd.NA))).strip()
        out_row = {
            "PersNr": persnr,
            "Name": name,
            "Eintritt": row.get("Eintritt", pd.NA),
            "Austritt": row.get("Austritt", pd.NA),
            "active_status": "active_at_stichtag",
            "Vertragsart": row.get("Vertragsart", pd.NA),
            "MitarbGruppenbez.": row.get("MitarbGruppenbez.", pd.NA),
            "Beschäftigungsstatus": row.get(status_col, pd.NA) if status_col else pd.NA,
            "BsGrd": bsgrd,
            "Personen_MAK_Source": person_mak,
            "Total_Cost_Year": row.get("Total_Cost_Year", pd.NA),
            "Geschlecht": row.get("Geschlecht", row.get("Text Gsch", pd.NA)),
            "Alter": row.get("Alter", pd.NA),
            "Alterskohorte": row.get("Alterskohorte", pd.NA),
            "Betriebszugehörigkeit": row.get("Betriebszugehörigkeit", row.get("Betriebszugehoerigkeit", pd.NA)),
            "Organisationseinheit aus Mitarbeiterdatei": row.get(org_col, pd.NA) if org_col else pd.NA,
            "Planstelle aus Mitarbeiterdatei": row.get(planstelle_ma_col, pd.NA) if planstelle_ma_col else pd.NA,
            "Job aus Mitarbeiterdatei": row.get(job_ma_col, pd.NA) if job_ma_col else pd.NA,
            "Kostenstelle": row.get(kosten_col, pd.NA) if kosten_col else pd.NA,
            "OE oder OrgUnit": row.get(org_col, pd.NA) if org_col else pd.NA,
            "Grund_fehlt_im_Snapshot": reason,
            "missing_category": category,
            "recommended_action": action,
            "join_key_mitarbeiter": persnr,
            "join_key_planstellen": plan_key_col or "(kein Planstellen-Key)",
            "mögliche_Planstellen_Treffer": plan_lookup.get(persnr, ""),
            "comment": comment,
        }
        rows.append(out_row)
    return pd.DataFrame(rows, columns=columns)


def build_status_quo_missing_category_summary(missing_audit_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["missing_category", "Köpfe", "Personen_MAK_Source", "EUR", "Anteil_an_missing", "empfohlene_Behandlung"]
    if missing_audit_df is None or missing_audit_df.empty:
        return pd.DataFrame(columns=columns)
    work = missing_audit_df.copy()
    work["Personen_MAK_Source"] = pd.to_numeric(work.get("Personen_MAK_Source"), errors="coerce").fillna(0.0)
    work["Total_Cost_Year"] = pd.to_numeric(work.get("Total_Cost_Year"), errors="coerce").fillna(0.0)
    summary = work.groupby("missing_category", dropna=False).agg(
        Köpfe=("PersNr", "nunique"),
        Personen_MAK_Source=("Personen_MAK_Source", "sum"),
        EUR=("Total_Cost_Year", "sum"),
        empfohlene_Behandlung=("recommended_action", "first"),
    ).reset_index()
    total = float(summary["Köpfe"].sum())
    summary["Anteil_an_missing"] = summary["Köpfe"] / total if total else 0.0
    return summary[columns].sort_values("Köpfe", ascending=False).reset_index(drop=True)


def build_status_quo_baseline_scope_check(
    status_quo_snapshot_df: pd.DataFrame,
    forecast_snapshot_df: pd.DataFrame,
    missing_audit_df: pd.DataFrame,
) -> pd.DataFrame:
    status = _active(status_quo_snapshot_df)
    forecast = _active(forecast_snapshot_df)
    missing = missing_audit_df.copy() if missing_audit_df is not None else pd.DataFrame()
    missing_heads = int(missing["PersNr"].nunique()) if "PersNr" in missing.columns and not missing.empty else 0
    missing_mak = _sum_col(missing, ("Personen_MAK_Source",))
    missing_eur = _sum_col(missing, ("Total_Cost_Year",))
    status_heads = int(status["PersNr"].nunique()) if "PersNr" in status.columns and not status.empty else 0
    status_mak = _sum_col(status, ("MAK_Reporting",))
    status_eur = _sum_col(status, ("EUR_Reporting", "Total_Cost_Year"))
    forecast_heads = int(forecast["PersNr"].nunique()) if "PersNr" in forecast.columns and not forecast.empty else 0
    forecast_mak = _sum_col(forecast, ("MAK_Reporting",))
    forecast_eur = _sum_col(forecast, ("EUR_Reporting", "Total_Cost_Year"))
    full_heads = status_heads + missing_heads
    rows = [
        {
            "Variante": "Planstellen_Snapshot_Baseline",
            "Köpfe": status_heads,
            "MAK_Reporting": status_mak,
            "EUR_Reporting": status_eur,
            "Abdeckungsquote": status_heads / full_heads if full_heads else 1.0,
            "Sonstiges_oder_Unassigned_Köpfe": 0,
            "Management_Eignung": "Standard für Job-Family-Analyse mit Abdeckungshinweis",
            "Risiken": "Aktive Personen ohne Planstellenzuordnung fehlen im Scope",
        },
        {
            "Variante": "Full_Employee_Baseline_with_Unassigned",
            "Köpfe": full_heads,
            "MAK_Reporting": status_mak + missing_mak,
            "EUR_Reporting": status_eur + missing_eur,
            "Abdeckungsquote": 1.0,
            "Sonstiges_oder_Unassigned_Köpfe": missing_heads,
            "Management_Eignung": "Gesamtpersonen-Scope; Job-Family-Struktur nur eingeschränkt vergleichbar",
            "Risiken": "Unassigned hat keine belastbare Job-Family-Zuordnung",
        },
        {
            "Variante": "Full_Employee_Baseline_with_Classified_Missing",
            "Köpfe": full_heads,
            "MAK_Reporting": status_mak + missing_mak,
            "EUR_Reporting": status_eur + missing_eur,
            "Abdeckungsquote": 1.0,
            "Sonstiges_oder_Unassigned_Köpfe": missing_heads,
            "Management_Eignung": "Scope-Transparenz und Fachklärung der fehlenden Personen",
            "Risiken": "Kategorien sind Audit-Klassen, keine produktive Job-Family-Zuordnung",
        },
        {
            "Variante": "Forecast_Comparison_Current",
            "Köpfe": forecast_heads,
            "MAK_Reporting": forecast_mak,
            "EUR_Reporting": forecast_eur,
            "Abdeckungsquote": pd.NA,
            "Sonstiges_oder_Unassigned_Köpfe": pd.NA,
            "Management_Eignung": "Vergleichbar mit Planstellen_Snapshot_Baseline",
            "Risiken": "Nicht direkt mit Full-Employee-Baseline vergleichen",
        },
    ]
    return pd.DataFrame(rows)


def _sum_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> float:
    for col in candidates:
        if col in df.columns:
            return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())
    return 0.0


def build_status_quo_snapshot(snapshot_df: pd.DataFrame, snapshot_date: pd.Timestamp) -> pd.DataFrame:
    """Return the current baseline snapshot with the validated MAK reporting columns."""
    if snapshot_df is None or snapshot_df.empty:
        return pd.DataFrame()
    out = snapshot_df.copy()
    if "MAK_Reporting" not in out.columns:
        out = apply_person_mak_allocation(out)
    out["Snapshot_Date"] = pd.Timestamp(snapshot_date).normalize()
    out["is_status_quo"] = True
    return out


def build_status_quo_jobfamily_summary(
    status_quo_snapshot_df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    active = _active(status_quo_snapshot_df)
    columns = [
        "Snapshot_Date",
        "Jobfamily",
        "Köpfe_StatusQuo",
        "MAK_StatusQuo",
        "EUR_StatusQuo",
        "Anteil_Köpfe_StatusQuo",
        "Anteil_MAK_StatusQuo",
        "MAK_je_Kopf_StatusQuo",
        "EUR_je_MAK_StatusQuo",
        "Technical_MAK_StatusQuo",
        "MAK_Adjustment_StatusQuo",
        "Personen_mit_Mehrfachplanstellen_StatusQuo",
    ]
    if active.empty:
        return pd.DataFrame(columns=columns)

    if "Jobfamily" not in active.columns:
        active["Jobfamily"] = "(ohne Job-Family)"
    active["Jobfamily"] = active["Jobfamily"].fillna("(ohne Job-Family)").astype(str)

    multi_by_person = pd.DataFrame(columns=["PersNr", "Jobfamily", "_is_multi"])
    if "PersNr" in active.columns:
        multi_by_person = active.groupby("PersNr", dropna=False).agg(
            Jobfamily=("Jobfamily", "first"),
            _rows=("PersNr", "size"),
        ).reset_index()
        multi_by_person["_is_multi"] = multi_by_person["_rows"].gt(1)

    rows = []
    for jobfamily, group in active.groupby("Jobfamily", dropna=False, observed=True):
        heads = int(group["PersNr"].nunique()) if "PersNr" in group.columns else int(len(group))
        mak = _sum_col(group, ("MAK_Reporting", "MAK_Calculated", "mak", "MAK"))
        eur = _sum_col(group, ("EUR_Reporting", "Total_Cost_Year"))
        technical = _sum_col(group, ("MAK_Technical_Uncorrected", "MAK_Calculated", "mak", "MAK"))
        adjustment = _sum_col(group, ("MAK_Adjustment_Delta",))
        multi = 0
        if not multi_by_person.empty:
            multi = int(multi_by_person.loc[
                multi_by_person["Jobfamily"].astype(str).eq(str(jobfamily)),
                "_is_multi",
            ].sum())
        rows.append(
            {
                "Snapshot_Date": pd.Timestamp(snapshot_date).normalize(),
                "Jobfamily": jobfamily,
                "Köpfe_StatusQuo": heads,
                "MAK_StatusQuo": mak,
                "EUR_StatusQuo": eur,
                "Technical_MAK_StatusQuo": technical,
                "MAK_Adjustment_StatusQuo": adjustment,
                "Personen_mit_Mehrfachplanstellen_StatusQuo": multi,
            }
        )

    summary = pd.DataFrame(rows)
    total_heads = float(summary["Köpfe_StatusQuo"].sum())
    total_mak = float(summary["MAK_StatusQuo"].sum())
    summary["Anteil_Köpfe_StatusQuo"] = summary["Köpfe_StatusQuo"] / total_heads if total_heads else 0.0
    summary["Anteil_MAK_StatusQuo"] = summary["MAK_StatusQuo"] / total_mak if total_mak else 0.0
    summary["MAK_je_Kopf_StatusQuo"] = summary.apply(
        lambda row: row["MAK_StatusQuo"] / row["Köpfe_StatusQuo"] if row["Köpfe_StatusQuo"] else 0.0,
        axis=1,
    )
    summary["EUR_je_MAK_StatusQuo"] = summary.apply(
        lambda row: row["EUR_StatusQuo"] / row["MAK_StatusQuo"] if row["MAK_StatusQuo"] else 0.0,
        axis=1,
    )
    total = {
        "Snapshot_Date": pd.Timestamp(snapshot_date).normalize(),
        "Jobfamily": "Gesamt",
        "Köpfe_StatusQuo": int(summary["Köpfe_StatusQuo"].sum()),
        "MAK_StatusQuo": float(summary["MAK_StatusQuo"].sum()),
        "EUR_StatusQuo": float(summary["EUR_StatusQuo"].sum()),
        "Anteil_Köpfe_StatusQuo": 1.0,
        "Anteil_MAK_StatusQuo": 1.0,
        "MAK_je_Kopf_StatusQuo": (
            float(summary["MAK_StatusQuo"].sum()) / float(summary["Köpfe_StatusQuo"].sum())
            if summary["Köpfe_StatusQuo"].sum()
            else 0.0
        ),
        "EUR_je_MAK_StatusQuo": (
            float(summary["EUR_StatusQuo"].sum()) / float(summary["MAK_StatusQuo"].sum())
            if summary["MAK_StatusQuo"].sum()
            else 0.0
        ),
        "Technical_MAK_StatusQuo": float(summary["Technical_MAK_StatusQuo"].sum()),
        "MAK_Adjustment_StatusQuo": float(summary["MAK_Adjustment_StatusQuo"].sum()),
        "Personen_mit_Mehrfachplanstellen_StatusQuo": int(summary["Personen_mit_Mehrfachplanstellen_StatusQuo"].sum()),
    }
    summary = pd.concat([summary, pd.DataFrame([total])], ignore_index=True)
    return summary[columns].sort_values(["Jobfamily"], key=lambda s: s.eq("Gesamt")).reset_index(drop=True)


def _forecast_summary(forecast_df: pd.DataFrame) -> pd.DataFrame:
    active = _active(forecast_df)
    if active.empty:
        return pd.DataFrame(columns=["Jobfamily", "Köpfe_Forecast", "MAK_Forecast", "EUR_Forecast"])
    if "Jobfamily" not in active.columns:
        active["Jobfamily"] = "(ohne Job-Family)"
    active["Jobfamily"] = active["Jobfamily"].fillna("(ohne Job-Family)").astype(str)
    rows = []
    for jobfamily, group in active.groupby("Jobfamily", dropna=False, observed=True):
        rows.append(
            {
                "Jobfamily": jobfamily,
                "Köpfe_Forecast": int(group["PersNr"].nunique()) if "PersNr" in group.columns else int(len(group)),
                "MAK_Forecast": _sum_col(group, ("MAK_Reporting", "MAK_Calculated", "mak", "MAK")),
                "EUR_Forecast": _sum_col(group, ("EUR_Reporting", "Total_Cost_Year")),
            }
        )
    summary = pd.DataFrame(rows)
    total_heads = float(summary["Köpfe_Forecast"].sum())
    total_mak = float(summary["MAK_Forecast"].sum())
    summary["Anteil_Köpfe_Forecast"] = summary["Köpfe_Forecast"] / total_heads if total_heads else 0.0
    summary["Anteil_MAK_Forecast"] = summary["MAK_Forecast"] / total_mak if total_mak else 0.0
    summary["MAK_je_Kopf_Forecast"] = summary.apply(
        lambda row: row["MAK_Forecast"] / row["Köpfe_Forecast"] if row["Köpfe_Forecast"] else 0.0,
        axis=1,
    )
    return summary


def _pct(delta: pd.Series, base: pd.Series) -> pd.Series:
    return delta.where(base.ne(0), 0.0) / base.where(base.ne(0), 1.0)


def _interpretation(row: pd.Series) -> str:
    if str(row.get("Jobfamily", "")).lower() == "sonstiges":
        return "sonstiges_mapping_pruefen"
    if abs(float(row.get("Delta_MAK_je_Kopf", 0.0))) > 0.15:
        return "mak_pro_kopf_auffaellig"
    if float(row.get("Delta_Köpfe_%", 0.0)) >= 0.15 and float(row.get("Delta_Köpfe", 0.0)) >= 3:
        return "starkes_wachstum"
    if float(row.get("Delta_Köpfe_%", 0.0)) <= -0.15 and float(row.get("Delta_Köpfe", 0.0)) <= -3:
        return "starker_rueckgang"
    if float(row.get("Delta_Anteil_MAK_pp", 0.0)) >= 3.0:
        return "strukturanteil_steigt"
    if float(row.get("Delta_Anteil_MAK_pp", 0.0)) <= -3.0:
        return "strukturanteil_sinkt"
    return "unauffaellig"


def build_forecast_vs_status_quo_jobfamily(
    status_quo_snapshot_df: pd.DataFrame,
    forecast_snapshot_df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    status = build_status_quo_jobfamily_summary(status_quo_snapshot_df, snapshot_date)
    status = status[status["Jobfamily"] != "Gesamt"].copy()
    forecast = _forecast_summary(forecast_snapshot_df)
    out = status.merge(forecast, on="Jobfamily", how="outer").fillna(0)
    for col in ("Köpfe_StatusQuo", "Köpfe_Forecast"):
        out[col] = out[col].astype(int)
    out["Delta_Köpfe"] = out["Köpfe_Forecast"] - out["Köpfe_StatusQuo"]
    out["Delta_Köpfe_%"] = _pct(out["Delta_Köpfe"], out["Köpfe_StatusQuo"])
    out["Delta_MAK"] = out["MAK_Forecast"] - out["MAK_StatusQuo"]
    out["Delta_MAK_%"] = _pct(out["Delta_MAK"], out["MAK_StatusQuo"])
    out["Delta_EUR"] = out["EUR_Forecast"] - out["EUR_StatusQuo"]
    out["Delta_EUR_%"] = _pct(out["Delta_EUR"], out["EUR_StatusQuo"])
    out["Delta_Anteil_Köpfe_pp"] = (out["Anteil_Köpfe_Forecast"] - out["Anteil_Köpfe_StatusQuo"]) * 100.0
    out["Delta_Anteil_MAK_pp"] = (out["Anteil_MAK_Forecast"] - out["Anteil_MAK_StatusQuo"]) * 100.0
    out["Delta_MAK_je_Kopf"] = out["MAK_je_Kopf_Forecast"] - out["MAK_je_Kopf_StatusQuo"]
    out["Interpretation_Flag"] = out.apply(_interpretation, axis=1)
    columns = [
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
        "MAK_je_Kopf_StatusQuo",
        "MAK_je_Kopf_Forecast",
        "Delta_MAK_je_Kopf",
        "Interpretation_Flag",
    ]
    return out[columns].sort_values(["Delta_Köpfe", "Delta_MAK"], ascending=False).reset_index(drop=True)


def build_status_quo_validation_checks(status_quo_snapshot_df: pd.DataFrame) -> pd.DataFrame:
    checks = build_mak_allocation_validation_summary(status_quo_snapshot_df)
    if checks.empty:
        return checks
    checks.insert(0, "Scope", "StatusQuo")
    return checks


def build_status_quo_mak_allocation_audit(status_quo_snapshot_df: pd.DataFrame) -> pd.DataFrame:
    return build_mak_allocation_audit(status_quo_snapshot_df)
