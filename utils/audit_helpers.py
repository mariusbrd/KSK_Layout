"""
Audit helpers for Kompakt plus simulation quality checks.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import numpy as np
import pandas as pd


MAK_CANDIDATES = ("MAK_Calculated", "mak", "MAK", "FTE_person", "BsGrd")


def select_mak_column(df: pd.DataFrame) -> str | None:
    """Return the preferred MAK/FTE column available in a frame."""
    for col in MAK_CANDIDATES:
        if col in df.columns:
            return col
    return None


def _series_text(series: pd.Series, fallback: str = "") -> str:
    values = [
        str(value).strip()
        for value in series.dropna().unique().tolist()
        if str(value).strip() and str(value).strip().lower() != "nan"
    ]
    return " | ".join(values) if values else fallback


def _first_value(rows: pd.DataFrame, column: str, fallback: Any = "") -> Any:
    if column not in rows.columns or rows.empty:
        return fallback
    non_empty = rows[column].dropna()
    if non_empty.empty:
        return fallback
    return non_empty.iloc[0]


def _numeric(series: pd.Series | Any) -> pd.Series:
    if isinstance(series, pd.Series):
        return pd.to_numeric(series, errors="coerce").fillna(0.0)
    return pd.Series([series]).pipe(pd.to_numeric, errors="coerce").fillna(0.0)


def _norm_text(value: Any) -> str:
    text = "" if pd.isna(value) else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.strip().lower())


def _build_final_snapshot_person_lookup(final_snapshot_df: pd.DataFrame | None) -> pd.DataFrame:
    if final_snapshot_df is None or final_snapshot_df.empty or "PersNr" not in final_snapshot_df.columns:
        return pd.DataFrame()
    work = final_snapshot_df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    work = work[work["PersNr"].notna()].copy()
    if work.empty:
        return pd.DataFrame()
    work["PersNr"] = work["PersNr"].astype(str)
    mak_col = select_mak_column(work)
    if mak_col == "BsGrd":
        work["_final_mak"] = _numeric(work[mak_col]) / 100.0
    elif mak_col:
        work["_final_mak"] = _numeric(work[mak_col])
    else:
        work["_final_mak"] = 0.0
    work["_final_eur"] = _numeric(work["Total_Cost_Year"]) if "Total_Cost_Year" in work.columns else 0.0

    agg = {
        "_final_mak": "sum",
        "_final_eur": "sum",
    }
    for col in [
        "Vertragsart",
        "MitarbGruppenbez.",
        "Jobfamily",
        "JF-Cluster",
        "OE-Cluster",
        "Planstelle",
        "Job",
        "Ausbildung",
        "ATZ_Status",
    ]:
        if col in work.columns:
            agg[col] = "first"
    return work.groupby("PersNr", as_index=False).agg(agg).set_index("PersNr")


def aggregate_person_capacity(rows: pd.DataFrame, policy: str = "audit_only") -> dict[str, Any]:
    """Aggregate a person's capacity rows without silently fixing anomalies."""
    if rows is None or rows.empty:
        return {
            "MAK_sum": 0.0,
            "MAK_max": 0.0,
            "MAK_person": 0.0,
            "Quelle_Logik": policy,
            "Auffaelligkeit": "keine_zeilen",
        }

    mak_col = select_mak_column(rows)
    if mak_col is None:
        mak_values = pd.Series(0.0, index=rows.index)
    elif mak_col == "BsGrd":
        mak_values = _numeric(rows[mak_col]) / 100.0
    else:
        mak_values = _numeric(rows[mak_col])

    mak_sum = float(mak_values.sum())
    mak_max = float(mak_values.max()) if not mak_values.empty else 0.0
    has_exception = bool(_first_value(rows, "Allow_MAK_gt_1", False))

    if policy == "max":
        mak_person = mak_max
    elif policy == "cap_sum_1":
        mak_person = min(mak_sum, 1.0)
    elif policy == "sum_with_documented_exception":
        mak_person = mak_sum if mak_sum <= 1.0 or has_exception else mak_max
    else:
        mak_person = mak_sum

    if mak_max > 1.000001:
        flag = "datenfehler_beschaeftigungsgrad"
    elif mak_sum > 1.000001 and has_exception:
        flag = "mehrfachbeschaeftigung_dokumentiert"
    elif mak_sum > 1.000001 and rows["Planstelle"].nunique(dropna=True) <= 1 if "Planstelle" in rows.columns else False:
        flag = "doppelte_planstellenzuordnung_wahrscheinlich"
    elif mak_sum > 1.000001:
        flag = "mehrfachplanstelle_fachlich_pruefen"
    else:
        flag = "unauffaellig"

    return {
        "MAK_sum": mak_sum,
        "MAK_max": mak_max,
        "MAK_person": float(mak_person),
        "Quelle_Logik": f"{policy}:{mak_col or 'keine_mak_spalte'}",
        "Auffaelligkeit": flag,
    }


def build_mak_person_audit(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "PersNr" not in df.columns:
        return pd.DataFrame()

    work = df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    work = work[work["PersNr"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    mak_col = select_mak_column(work)
    if mak_col is None:
        work["_audit_mak"] = 0.0
    elif mak_col == "BsGrd":
        work["_audit_mak"] = _numeric(work[mak_col]) / 100.0
    else:
        work["_audit_mak"] = _numeric(work[mak_col])

    eur_col = "Total_Cost_Year" if "Total_Cost_Year" in work.columns else None
    work["_eur"] = _numeric(work[eur_col]) if eur_col else 0.0

    g = work.groupby("PersNr", sort=True, dropna=False)

    agg = g.agg(
        MAK_sum=("_audit_mak", "sum"),
        MAK_max=("_audit_mak", "max"),
        EUR_sum=("_eur", "sum"),
        Anzahl_Zeilen=("_audit_mak", "count"),
    ).reset_index()

    agg["PersNr"] = agg["PersNr"].astype(str)

    if "Planstelle" in work.columns:
        agg["Anzahl_Planstellen"] = g["Planstelle"].nunique(dropna=True).values
        _planst_le1 = agg["Anzahl_Planstellen"] <= 1
    else:
        agg["Anzahl_Planstellen"] = 0
        _planst_le1 = pd.Series(False, index=agg.index)

    if "Jobfamily" in work.columns:
        _jf = g["Jobfamily"].first()
        if "JF-Cluster" in work.columns:
            agg["Jobfamily"] = _jf.fillna(g["JF-Cluster"].first().fillna("")).fillna("").values
        else:
            agg["Jobfamily"] = _jf.fillna("").values
    elif "JF-Cluster" in work.columns:
        agg["Jobfamily"] = g["JF-Cluster"].first().fillna("").values
    else:
        agg["Jobfamily"] = ""

    for _src, _out in [
        ("Vertragsart", "Vertragsart"),
        ("MitarbGruppenbez.", "MitarbGruppenbez."),
        ("Beschäftigungsgrad_Kat", "Beschaeftigungsgrad_Kat"),
    ]:
        if _src in work.columns:
            agg[_out] = g[_src].first().fillna("").values
        else:
            agg[_out] = ""

    if "Allow_MAK_gt_1" in work.columns:
        _allow_gt1 = g["Allow_MAK_gt_1"].first().fillna(False).astype(bool).values
    else:
        _allow_gt1 = False

    agg["Auffaelligkeit"] = np.select(
        [
            agg["MAK_max"] > 1.000001,
            (agg["MAK_sum"] > 1.000001) & _allow_gt1,
            (agg["MAK_sum"] > 1.000001) & _planst_le1,
            agg["MAK_sum"] > 1.000001,
        ],
        [
            "datenfehler_beschaeftigungsgrad",
            "mehrfachbeschaeftigung_dokumentiert",
            "doppelte_planstellenzuordnung_wahrscheinlich",
            "mehrfachplanstelle_fachlich_pruefen",
        ],
        default="unauffaellig",
    )

    agg["Quelle_Logik"] = f"audit_only:{mak_col or 'keine_mak_spalte'}"

    if "Planstelle" in work.columns:
        agg["Planstellenliste"] = g["Planstelle"].apply(_series_text).values
    else:
        agg["Planstellenliste"] = ""

    agg["MAK_je_Zeile"] = g["_audit_mak"].apply(
        lambda s: " | ".join(f"{value:.4f}" for value in s.tolist())
    ).values

    return (
        agg[[
            "PersNr", "Jobfamily", "Anzahl_Zeilen", "Anzahl_Planstellen",
            "Planstellenliste", "MAK_je_Zeile", "MAK_sum", "MAK_max", "EUR_sum",
            "Vertragsart", "MitarbGruppenbez.", "Beschaeftigungsgrad_Kat",
            "Quelle_Logik", "Auffaelligkeit",
        ]]
        .sort_values(["MAK_sum", "Anzahl_Zeilen"], ascending=[False, False])
        .reset_index(drop=True)
    )


def build_azubi_flow_audit(
    final_employee_df: pd.DataFrame,
    events_df: pd.DataFrame,
    target_date: pd.Timestamp,
    final_snapshot_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    events = events_df.copy() if events_df is not None else pd.DataFrame()
    final = final_employee_df.copy() if final_employee_df is not None else pd.DataFrame()
    if events.empty and final.empty:
        return pd.DataFrame()

    target_date = pd.Timestamp(target_date)
    snapshot_lookup = _build_final_snapshot_person_lookup(final_snapshot_df)
    if not events.empty:
        events["persnr"] = events["persnr"].astype(str)
        events["date"] = pd.to_datetime(events.get("date"), errors="coerce")
        event_ids = set(events.loc[events["type"].astype(str).str.contains("Azubi", na=False), "persnr"])
    else:
        event_ids = set()

    if not final.empty and "PersNr" in final.columns:
        final["PersNr"] = final["PersNr"].astype(str)
        final_ids = set(final.loc[final["PersNr"].str.startswith("AZ_", na=False), "PersNr"])
    else:
        final_ids = set()

    rows = []
    for persnr in sorted(event_ids | final_ids):
        ev = events[events["persnr"].eq(persnr)] if not events.empty else pd.DataFrame()
        fin = final[final["PersNr"].eq(persnr)].tail(1) if not final.empty and "PersNr" in final.columns else pd.DataFrame()
        fin_row = fin.iloc[0] if not fin.empty else pd.Series(dtype=object)
        snap_row = snapshot_lookup.loc[persnr] if not snapshot_lookup.empty and persnr in snapshot_lookup.index else pd.Series(dtype=object)

        hire_dates = ev.loc[ev["type"].eq("Azubi_Hire"), "date"] if not ev.empty else pd.Series(dtype="datetime64[ns]")
        conv_dates = ev.loc[ev["type"].eq("Azubi_Conversion_In"), "date"] if not ev.empty else pd.Series(dtype="datetime64[ns]")
        exit_dates = ev.loc[ev["type"].eq("Azubi_Exit"), "date"] if not ev.empty else pd.Series(dtype="datetime64[ns]")
        graduation_dates = pd.to_datetime(ev.get("graduation_date"), errors="coerce") if "graduation_date" in ev.columns else pd.Series(dtype="datetime64[ns]")

        value_source = "final_snapshot_df" if not snap_row.empty else "final_employee_df"
        final_active = bool(fin_row.get("active", False)) if not fin_row.empty else False
        final_vertragsart = str(snap_row.get("Vertragsart", fin_row.get("Vertragsart", "")))
        final_mitarb = snap_row.get("MitarbGruppenbez.", fin_row.get("MitarbGruppenbez.", "")) if not fin_row.empty or not snap_row.empty else ""
        final_jobfamily = snap_row.get("Jobfamily", fin_row.get("Jobfamily", "")) if not fin_row.empty or not snap_row.empty else ""
        final_jf_cluster = snap_row.get("JF-Cluster", fin_row.get("JF-Cluster", "")) if not fin_row.empty or not snap_row.empty else ""
        if not snap_row.empty:
            final_mak = float(snap_row.get("_final_mak", 0.0) or 0.0)
            final_eur = float(snap_row.get("_final_eur", 0.0) or 0.0)
        else:
            final_mak = float(pd.to_numeric(pd.Series([fin_row.get("mak", fin_row.get("MAK_Calculated", 0.0))]), errors="coerce").fillna(0.0).iloc[0]) if not fin_row.empty else 0.0
            final_eur = float(pd.to_numeric(pd.Series([fin_row.get("Total_Cost_Year", 0.0)]), errors="coerce").fillna(0.0).iloc[0]) if not fin_row.empty else 0.0

        last_grad = graduation_dates.dropna().max() if not graduation_dates.dropna().empty else pd.NaT
        if pd.notna(last_grad) and last_grad <= target_date:
            expected = "uebernommen_oder_ausgeschieden"
        else:
            expected = "aktive_ausbildung"

        has_conversion = not conv_dates.dropna().empty
        if final_active and final_vertragsart == "Auszubildende" and expected != "aktive_ausbildung":
            flag = "azubi_nach_abschluss_weiter_als_azubi"
        elif final_active and final_vertragsart == "Auszubildende" and final_mak != 0:
            flag = "azubi_in_ausbildung_mit_mak"
        elif has_conversion and final_mak > 0 and final_eur == 0:
            flag = "uebernommener_azubi_mit_mak_aber_ohne_eur"
        elif not final_active and expected == "aktive_ausbildung":
            flag = "azubi_vor_abschluss_inaktiv"
        else:
            flag = "unauffaellig"

        event_types = ev["type"].dropna().astype(str).unique().tolist() if not ev.empty else []
        rows.append(
            {
                "PersNr": persnr,
                "Eventjahr": int(hire_dates.dropna().min().year) if not hire_dates.dropna().empty else "",
                "Eventtyp": " | ".join(event_types),
                "Hire_Date": hire_dates.dropna().min() if not hire_dates.dropna().empty else pd.NaT,
                "GraduationDate": last_grad,
                "Conversion_Date": conv_dates.dropna().max() if not conv_dates.dropna().empty else pd.NaT,
                "Exit_Date": exit_dates.dropna().max() if not exit_dates.dropna().empty else pd.NaT,
                "final_active": final_active,
                "final_Vertragsart": final_vertragsart,
                "final_MitarbGruppenbez.": final_mitarb,
                "final_Jobfamily": final_jobfamily,
                "final_JF_Cluster": final_jf_cluster,
                "final_MAK": final_mak,
                "final_EUR": final_eur,
                "audit_value_source": value_source,
                "Soll_Status_zum_2030_12_31": expected,
                "Ist_Status_zum_2030_12_31": "aktiv" if final_active else "inaktiv",
                "Auffaelligkeit": flag,
            }
        )

    return pd.DataFrame(rows)


def classify_sonstiges_cases(df: pd.DataFrame) -> pd.Series:
    classified = _classify_sonstiges_cases_with_reason(df)
    return classified["Sonderfall_Kategorie"] if not classified.empty else pd.Series(dtype="object")


def _classify_sonstiges_cases_with_reason(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "Sonderfall_Kategorie",
            "classification_reason",
            "is_azubi_takeover",
            "is_active_azubi",
            "mapping_problem_type",
        ])

    vertragsart = df.get("Vertragsart", pd.Series("", index=df.index)).map(_norm_text)
    mitarb = df.get("MitarbGruppenbez.", pd.Series("", index=df.index)).map(_norm_text)
    ausbildung = df.get("Ausbildung", pd.Series("", index=df.index)).map(_norm_text)
    status = df.get("Status kundenindividuell", pd.Series("", index=df.index)).map(_norm_text)
    planstelle = df.get("Planstelle", pd.Series("", index=df.index)).map(_norm_text)
    persnr = df.get("PersNr", pd.Series("", index=df.index)).astype(str).str.strip()
    jobfamily = df.get("Jobfamily", df.get("JF-Cluster", pd.Series("", index=df.index))).astype(str).str.strip()
    jf_cluster = df.get("JF-Cluster", pd.Series("", index=df.index)).astype(str).str.strip()
    mak_col = select_mak_column(df)
    if mak_col == "BsGrd":
        final_mak = _numeric(df[mak_col]) / 100.0
    elif mak_col:
        final_mak = _numeric(df[mak_col])
    else:
        final_mak = pd.Series(0.0, index=df.index)
    final_eur = _numeric(df["Total_Cost_Year"]) if "Total_Cost_Year" in df.columns else pd.Series(0.0, index=df.index)
    azubi_flag = df.get("Ist_Azubi", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    is_forecast_azubi_person = persnr.str.match(r"^AZ_", na=False)
    is_sonstiges = jobfamily.isin(["Sonstiges", "Sonstige", ""]) | jf_cluster.eq("Sonstiges")

    result = pd.DataFrame(index=df.index)
    result["Sonderfall_Kategorie"] = "unklar"
    result["classification_reason"] = "keine_regel_getroffen"
    result["is_azubi_takeover"] = False
    result["is_active_azubi"] = False
    result["mapping_problem_type"] = "unklar"

    azubi_indicator_mask = (
        vertragsart.eq("auszubildende")
        | mitarb.str.contains(r"\bauszubild", regex=True, na=False)
        | ausbildung.str.contains(r"\bberufsausbildung\b", regex=True, na=False)
        | planstelle.str.contains(r"\b(?:azubi|auszubild)", regex=True, na=False)
        | azubi_flag
        | is_forecast_azubi_person
    )
    active_azubi_mask = (
        (vertragsart.eq("auszubildende") | azubi_flag)
        & (final_mak.fillna(0.0) <= 0.000001)
    )
    takeover_mask = (
        (azubi_indicator_mask | is_forecast_azubi_person)
        & ~vertragsart.eq("auszubildende")
        & (final_mak.fillna(0.0) > 0.000001)
        & (final_eur.fillna(0.0) > 0.000001)
        & is_sonstiges
    )

    result.loc[active_azubi_mask, ["Sonderfall_Kategorie", "classification_reason", "mapping_problem_type"]] = [
        "aktive_auszubildende",
        "vertragsart_oder_flag_azubi_und_mak_null",
        "kein_mappingproblem_aktive_ausbildung",
    ]
    result.loc[active_azubi_mask, "is_active_azubi"] = True

    result.loc[takeover_mask, ["Sonderfall_Kategorie", "classification_reason", "mapping_problem_type"]] = [
        "azubi_uebernahme_ohne_zielzuordnung",
        "azubi_forecast_oder_conversion_indiz_mit_regulaerem_status_und_sonstiges",
        "azubi_takeover_target_missing",
    ]
    result.loc[takeover_mask, "is_azubi_takeover"] = True

    werk_mask = vertragsart.str.contains(r"\b(?:werkstudent|praktik)", regex=True, na=False)
    result.loc[werk_mask & ~active_azubi_mask & ~takeover_mask, ["Sonderfall_Kategorie", "classification_reason", "mapping_problem_type"]] = [
        "werkstudenten_und_praktikanten",
        "vertragsart_werkstudent_praktikum",
        "sonderfall_kein_regulaeres_mapping",
    ]

    ruhend_mask = status.str.contains(r"\b(?:ruhend|freistellung)", regex=True, na=False)
    result.loc[ruhend_mask & ~active_azubi_mask & ~takeover_mask & ~werk_mask, ["Sonderfall_Kategorie", "classification_reason", "mapping_problem_type"]] = [
        "freistellung_ruhend",
        "status_ruhend_freistellung",
        "ruhend_oder_freigestellt",
    ]

    regular_mitarb_mask = mitarb.str.contains(r"\b(?:angestellte|mitarbeiter|beschaeftigte|tarif|at)\b", regex=True, na=False)
    unbefristet_mask = (
        (vertragsart.eq("unbefristet") | regular_mitarb_mask)
        & ~active_azubi_mask
        & ~takeover_mask
        & ~werk_mask
        & ~ruhend_mask
    )
    result.loc[unbefristet_mask, ["Sonderfall_Kategorie", "classification_reason", "mapping_problem_type"]] = [
        "nicht_gemappte_regulaere_beschaeftigte",
        "vertragsart_unbefristet_oder_regulaere_mitarbeitergruppe_ohne_azubi_takeover",
        "regular_employee_missing_mapping",
    ]

    zeitvertrag_mask = vertragsart.eq("zeitvertrag") & ~active_azubi_mask & ~takeover_mask & ~werk_mask & ~ruhend_mask
    result.loc[zeitvertrag_mask, ["Sonderfall_Kategorie", "classification_reason", "mapping_problem_type"]] = [
        "zeitvertrag_sonderfaelle",
        "vertragsart_exakt_zeitvertrag",
        "zeitvertrag_sonderfall",
    ]

    befristet_mask = (
        vertragsart.str.contains(r"(?<!un)befristet\b", regex=True, na=False)
        & ~unbefristet_mask
        & ~active_azubi_mask
        & ~takeover_mask
        & ~werk_mask
        & ~ruhend_mask
    )
    result.loc[befristet_mask, ["Sonderfall_Kategorie", "classification_reason", "mapping_problem_type"]] = [
        "zeitvertrag_sonderfaelle",
        "vertragsart_befristet_ohne_unbefristet",
        "befristeter_sonderfall",
    ]
    return result


def build_sonstiges_audit(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    jf = work.get("Jobfamily", work.get("JF-Cluster", pd.Series("", index=work.index))).astype(str)
    work = work[jf.eq("Sonstiges") | jf.eq("Sonstige")].copy()
    if work.empty:
        return pd.DataFrame()

    mak_col = select_mak_column(work)
    if mak_col == "BsGrd":
        work["MAK"] = _numeric(work[mak_col]) / 100.0
    elif mak_col:
        work["MAK"] = _numeric(work[mak_col])
    else:
        work["MAK"] = 0.0
    work["EUR"] = _numeric(work["Total_Cost_Year"]) if "Total_Cost_Year" in work.columns else 0.0
    classified = _classify_sonstiges_cases_with_reason(work)
    work["Sonderfall_Kategorie"] = classified["Sonderfall_Kategorie"]
    work["classification_reason"] = classified["classification_reason"]
    work["is_azubi_takeover"] = classified["is_azubi_takeover"]
    work["is_active_azubi"] = classified["is_active_azubi"]
    work["mapping_problem_type"] = classified["mapping_problem_type"]

    cols = [
        "PersNr",
        "Vertragsart",
        "MitarbGruppenbez.",
        "Jobfamily",
        "Planstelle",
        "Organisationseinheit",
        "OE-Cluster",
        "JF-Cluster",
        "Ausbildung",
        "MAK",
        "EUR",
        "Sonderfall_Kategorie",
        "classification_reason",
        "is_azubi_takeover",
        "is_active_azubi",
        "mapping_problem_type",
    ]
    for col in cols:
        if col not in work.columns:
            work[col] = pd.NA
    return work[cols].sort_values(["Sonderfall_Kategorie", "MAK"], ascending=[True, False]).reset_index(drop=True)


def build_azubi_takeover_target_audit(
    final_employee_df: pd.DataFrame,
    events_df: pd.DataFrame,
    target_date: pd.Timestamp,
    final_snapshot_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    flow = build_azubi_flow_audit(final_employee_df, events_df, target_date, final_snapshot_df=final_snapshot_df)
    if flow.empty:
        return pd.DataFrame()
    converted = flow[flow["Eventtyp"].astype(str).str.contains("Azubi_Conversion_In", na=False)].copy()
    if converted.empty:
        return pd.DataFrame()
    snapshot_lookup = _build_final_snapshot_person_lookup(final_snapshot_df)
    rows = []
    for _, row in converted.iterrows():
        pid = str(row["PersNr"])
        snap = snapshot_lookup.loc[pid] if not snapshot_lookup.empty and pid in snapshot_lookup.index else pd.Series(dtype=object)
        final_jobfamily = row.get("final_Jobfamily", "")
        final_jf_cluster = row.get("final_JF_Cluster", "")
        final_oe_cluster = snap.get("OE-Cluster", "")
        planstelle = snap.get("Planstelle", "")
        job = snap.get("Job", planstelle)
        if str(final_jobfamily).strip() in {"Sonstiges", "Sonstige", ""} or str(final_jf_cluster).strip() == "Sonstiges":
            auff = "takeover_ziel_in_sonstiges"
            action = "fachliche_zielverteilung_fuer_azubi_uebernahmen_definieren"
            reason = "keine_aktive_takeover_matrix_oder_default_ziel_sonstiges"
        else:
            auff = "unauffaellig"
            action = "keine"
            reason = "finale_jobfamily_aus_uebernahmelogik"
        rows.append(
            {
                "PersNr": pid,
                "Conversion_Date": row.get("Conversion_Date", pd.NaT),
                "final_Vertragsart": row.get("final_Vertragsart", ""),
                "final_MitarbGruppenbez.": row.get("final_MitarbGruppenbez.", ""),
                "final_Jobfamily": final_jobfamily,
                "final_JF_Cluster": final_jf_cluster,
                "final_OE_Cluster": final_oe_cluster,
                "final_Planstelle": planstelle,
                "final_Job": job,
                "final_MAK": row.get("final_MAK", 0.0),
                "final_EUR": row.get("final_EUR", 0.0),
                "target_assignment_source": "azubi_takeover_logic",
                "target_assignment_reason": reason,
                "Auffaelligkeit": auff,
                "recommended_action": action,
            }
        )
    return pd.DataFrame(rows)


def build_azubi_takeover_decision_list(takeover_audit: pd.DataFrame) -> pd.DataFrame:
    if takeover_audit is None or takeover_audit.empty:
        return pd.DataFrame()
    work = takeover_audit.copy()
    final_jobfamily = work.get("final_Jobfamily", "").astype(str).str.strip()
    final_jf_cluster = work.get("final_JF_Cluster", "").astype(str).str.strip()
    needs_target = final_jobfamily.isin(["", "Sonstiges", "Sonstige"]) | final_jf_cluster.isin(["", "Sonstiges"])
    work["current_assignment_status"] = needs_target.map(
        {True: "sonstiges_ohne_fachliche_zielzuordnung", False: "ziel_jobfamily_vorhanden"}
    )
    work["suggested_target_option_1"] = "Option A: feste Azubi-Uebernahme-Matrix nach Jahr und Ziel-Jobfamily"
    work["suggested_target_option_2"] = "Option B: Verteilung nach geplanten Vakanzen oder Ersatzbedarf"
    work["suggested_target_option_3"] = "Option C: proportionale Verteilung nach aktueller Jobfamily-Struktur"
    work["business_decision_needed"] = needs_target
    work.loc[needs_target, "recommended_action"] = "Zielverteilungsregel fuer Azubi-Uebernahmen fachlich festlegen"
    work.loc[~needs_target, "recommended_action"] = "Zielzuordnung pruefen und dokumentieren"
    cols = [
        "PersNr",
        "Conversion_Date",
        "final_Vertragsart",
        "final_MitarbGruppenbez.",
        "final_Jobfamily",
        "final_JF_Cluster",
        "final_OE_Cluster",
        "final_Planstelle",
        "final_Job",
        "final_MAK",
        "final_EUR",
        "current_assignment_status",
        "target_assignment_source",
        "target_assignment_reason",
        "suggested_target_option_1",
        "suggested_target_option_2",
        "suggested_target_option_3",
        "business_decision_needed",
        "recommended_action",
    ]
    for col in cols:
        if col not in work.columns:
            work[col] = pd.NA
    return work[cols].reset_index(drop=True)


def _interpret_mak_case(row: pd.Series) -> tuple[str, str, str]:
    flag = str(row.get("Auffaelligkeit", ""))
    mak_values = [
        float(v.strip())
        for v in str(row.get("MAK_je_Zeile", "")).split("|")
        if v.strip()
    ]
    planstellen = str(row.get("Planstellenliste", ""))
    if flag == "doppelte_planstellenzuordnung_wahrscheinlich":
        return (
            "wahrscheinlich_doppelzaehlung",
            "Dubletten-/Planstellenzuordnung pruefen",
            "gleiche oder kaum trennbare Planstellenzuordnung mit MAK_sum > 1",
        )
    if flag == "datenfehler_beschaeftigungsgrad":
        return (
            "datenfehler_beschaeftigungsgrad",
            "Beschaeftigungsgrad je Zeile und Quelldaten korrigieren",
            "mindestens eine Zeile hat MAK_max > 1 oder auffaellige Zeilenkapazitaet",
        )
    if len(mak_values) >= 2 and all(0 < v <= 1.0 for v in mak_values) and any(token in planstellen.lower() for token in ["stv", "leiter", "+"]):
        return (
            "wahrscheinlich_planstellen_split",
            "Fachentscheidung: Split als eine Personalkapazitaet modellieren",
            "mehrere Rollen/Planstellen mit zusammen mehr als 1 MAK",
        )
    if str(row.get("Vertragsart", "")).lower().strip() not in {"", "unbefristet"}:
        return (
            "echte_mehrfachbeschaeftigung_moeglich",
            "Ausnahme dokumentieren oder Kapazitaet fachlich splitten",
            "abweichende Vertrags-/Statuslage kann Mehrfachbeschaeftigung anzeigen",
        )
    return (
        "nicht_entscheidbar",
        "Fachbereich muss Kapazitaetsregel je Person entscheiden",
        "aus Auditdaten nicht eindeutig entscheidbar",
    )


def build_mak_decision_list(mak_person_audit: pd.DataFrame) -> pd.DataFrame:
    if mak_person_audit is None or mak_person_audit.empty:
        return pd.DataFrame()
    work = mak_person_audit[pd.to_numeric(mak_person_audit.get("MAK_sum", 0), errors="coerce").fillna(0) > 1.000001].copy()
    if work.empty:
        return pd.DataFrame()
    rows = []
    for _, row in work.iterrows():
        decision, action, interpretation = _interpret_mak_case(row)
        mak_sum = float(row.get("MAK_sum", 0.0) or 0.0)
        mak_max = float(row.get("MAK_max", 0.0) or 0.0)
        cap_value = min(mak_sum, 1.0)
        effect_if_cap = cap_value - mak_sum
        effect_if_max = mak_max - mak_sum
        if decision == "datenfehler_beschaeftigungsgrad":
            question = "Ist die Kombination der MAK-Werte je Zeile fachlich intendiert oder ein Beschaeftigungsgrad-Fehler?"
            proposed_policy = "Quelldaten korrigieren; bis zur Klaerung keine automatische Kappung"
        elif decision == "wahrscheinlich_doppelzaehlung":
            question = "Handelt es sich um zwei echte Beschaeftigungsverhaeltnisse oder um eine doppelte Planstellenzuordnung?"
            proposed_policy = "Dubletten bereinigen oder explizite Ausnahme dokumentieren"
        elif decision == "wahrscheinlich_planstellen_split":
            question = "Soll die Personalkapazitaet dieser Person im Zielbestand maximal 1,0 betragen und nur auf Rollen verteilt werden?"
            proposed_policy = "Planstellen-Split mit Personenkapazitaet maximal 1,0 modellieren"
        else:
            question = "Soll diese Person mehrere volle Personalkapazitaeten erhalten oder ist eine fachliche Ausnahme zu dokumentieren?"
            proposed_policy = "Fachentscheidung einholen und Ausnahme-/Splitregel dokumentieren"
        rows.append(
            {
                "PersNr": row.get("PersNr", ""),
                "Jobfamily": row.get("Jobfamily", ""),
                "Anzahl_Zeilen": row.get("Anzahl_Zeilen", 0),
                "Planstellenliste": row.get("Planstellenliste", ""),
                "Rollenliste": row.get("Planstellenliste", ""),
                "MAK_je_Zeile": row.get("MAK_je_Zeile", ""),
                "MAK_sum": mak_sum,
                "MAK_max": mak_max,
                "EUR_sum": row.get("EUR_sum", 0.0),
                "capacity_flag": row.get("Auffaelligkeit", ""),
                "recommended_action": action,
                "interpretation": decision,
                "option_sum": mak_sum,
                "option_max": mak_max,
                "option_cap_at_1": cap_value,
                "option_proportional_cap": cap_value,
                "business_decision_needed": True,
                "interpretation_detail": interpretation,
                "proposed_policy": proposed_policy,
                "effect_if_cap_at_1": effect_if_cap,
                "effect_if_max": effect_if_max,
                "effect_if_keep_sum": 0.0,
                "recommended_business_question": question,
                "decision_owner": "Fachbereich Personalcontrolling / Organisationsverantwortliche",
                "decision_status": "offen",
            }
        )
    return pd.DataFrame(rows).sort_values(["MAK_sum", "EUR_sum"], ascending=[False, False]).reset_index(drop=True)


def build_mak_policy_impact(future_snapshot_df: pd.DataFrame, mak_decision_list: pd.DataFrame) -> pd.DataFrame:
    policies = ["current_sum", "max_per_person", "cap_at_1", "proportional_cap_at_1", "exception_based"]
    if future_snapshot_df is None or future_snapshot_df.empty:
        return pd.DataFrame({"Policy": policies})

    work = future_snapshot_df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    mak_col = select_mak_column(work)
    if mak_col == "BsGrd":
        work["_MAK"] = _numeric(work[mak_col]) / 100.0
    elif mak_col:
        work["_MAK"] = _numeric(work[mak_col])
    else:
        work["_MAK"] = 0.0
    current_total = float(work["_MAK"].sum())
    current_fv = float(work.loc[work.get("Jobfamily", "").astype(str).eq("Führung Vertrieb"), "_MAK"].sum())
    if mak_decision_list is None or mak_decision_list.empty:
        return pd.DataFrame(
            [{
                "Policy": policy,
                "MAK gesamt": current_total,
                "Delta zu aktuell": 0.0,
                "betroffene Job Families": "",
                "Führung Vertrieb MAK": current_fv,
                "Führung Vertrieb Delta": 0.0,
                "Anzahl betroffene Personen": 0,
                "Kommentar": "keine MAK > 1 Faelle",
            } for policy in policies]
        )

    decision = mak_decision_list.copy()
    affected_current = float(decision["option_sum"].sum())
    affected_jfs = " | ".join(sorted(decision["Jobfamily"].dropna().astype(str).unique()))
    fv_decision = decision[decision["Jobfamily"].astype(str).eq("Führung Vertrieb")]
    fv_current = float(fv_decision["option_sum"].sum())
    affected_persons = int(decision["PersNr"].nunique())

    replacements = {
        "current_sum": float(decision["option_sum"].sum()),
        "max_per_person": float(decision["option_max"].sum()),
        "cap_at_1": float(decision["option_cap_at_1"].sum()),
        "proportional_cap_at_1": float(decision["option_proportional_cap"].sum()),
        "exception_based": float(decision["option_sum"].sum()),
    }
    fv_replacements = {
        "current_sum": fv_current,
        "max_per_person": float(fv_decision["option_max"].sum()),
        "cap_at_1": float(fv_decision["option_cap_at_1"].sum()),
        "proportional_cap_at_1": float(fv_decision["option_proportional_cap"].sum()),
        "exception_based": fv_current,
    }
    comments = {
        "current_sum": "Status quo; fachlich nur tragfaehig bei dokumentierten Mehrfachkapazitaeten",
        "max_per_person": "nimmt je Person die groesste Einzelzeile; reduziert offensichtliche Dopplungen, kann Splitanteile verlieren",
        "cap_at_1": "begrenzt jede betroffene Person auf 1,0 MAK; rechnerisch plausibel, fachlich erst nach Entscheidung anwenden",
        "proportional_cap_at_1": "begrenzt auf 1,0 und verteilt proportional auf Zeilen; geeignet fuer Rollen-Splits",
        "exception_based": "empfohlenes Zielbild: Cap/Split als Standard, dokumentierte Ausnahmen bleiben Summe",
    }
    rows = []
    for policy in policies:
        mak_total = current_total - affected_current + replacements[policy]
        fv_mak = current_fv - fv_current + fv_replacements[policy]
        rows.append(
            {
                "Policy": policy,
                "MAK gesamt": mak_total,
                "Delta zu aktuell": mak_total - current_total,
                "betroffene Job Families": affected_jfs,
                "Führung Vertrieb MAK": fv_mak,
                "Führung Vertrieb Delta": fv_mak - current_fv,
                "Anzahl betroffene Personen": affected_persons,
                "Kommentar": comments[policy],
            }
        )
    return pd.DataFrame(rows)


def _normalize_persnr_for_audit(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _person_stage_summary(
    df: pd.DataFrame,
    stage: str,
    source_dataframe: str,
    base_ids: set[str],
    abgang_events: pd.DataFrame,
    zugang_events: pd.DataFrame,
    *,
    _abg_lookup: dict | None = None,
    _zug_lookup: dict | None = None,
) -> pd.DataFrame:
    if df is None or df.empty or "PersNr" not in df.columns:
        return pd.DataFrame()
    work = df.copy().reset_index(drop=True)
    if "active" in work.columns:
        work = work[work["active"] == True].copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    work = work[work["PersNr"].notna()].copy()
    if work.empty:
        return pd.DataFrame()
    work["PersNr"] = _normalize_persnr_for_audit(work["PersNr"])
    mak_col = select_mak_column(work)
    if mak_col == "BsGrd":
        work["_mak_value"] = _numeric(work[mak_col]) / 100.0
    elif mak_col:
        work["_mak_value"] = _numeric(work[mak_col])
    else:
        work["_mak_value"] = 0.0
    work["_eur_value"] = _numeric(work["Total_Cost_Year"]) if "Total_Cost_Year" in work.columns else 0.0
    if "Planstelle" not in work.columns and "Job" in work.columns:
        work["Planstelle"] = work["Job"]
    if "Job" not in work.columns and "Planstelle" in work.columns:
        work["Job"] = work["Planstelle"]

    abg_lookup = _abg_lookup if _abg_lookup is not None else _event_lookup(abgang_events, "persnr")
    zug_lookup = _zug_lookup if _zug_lookup is not None else _event_lookup(zugang_events, "persnr")

    # Stripped Planstelle fuer Anzahl_Planstellen (ohne NaN-Einfuehrung)
    if "Planstelle" in work.columns:
        _notna_pl = work["Planstelle"].notna()
        work["_pl_s"] = pd.NA
        if _notna_pl.any():
            work.loc[_notna_pl, "_pl_s"] = (
                work.loc[_notna_pl, "Planstelle"].astype(str).str.strip()
            )
    else:
        work["_pl_s"] = pd.NA

    # Groupby sortiert (sort=True: identisch mit alter for-Loop-Reihenfolge)
    g = work.groupby("PersNr", sort=True, dropna=False)

    # Numerische Aggregationen ersetzen den per-Person-Loop
    agg = g.agg(
        MAK_sum=("_mak_value", "sum"),
        MAK_max=("_mak_value", "max"),
        MAK_min=("_mak_value", "min"),
        EUR_sum=("_eur_value", "sum"),
        Anzahl_Zeilen=("_mak_value", "count"),
    ).reset_index()

    agg["Anzahl_Planstellen"] = g["_pl_s"].nunique(dropna=True).values

    # First-value-Felder (aequivalent zu _first_value mit leerem Fallback)
    for _col, _fb in [("Jobfamily", ""), ("Vertragsart", ""), ("MitarbGruppenbez.", "")]:
        if _col in work.columns:
            agg[_col] = g[_col].first().fillna(_fb).values
        else:
            agg[_col] = _fb

    if "is_forecast" in work.columns:
        agg["_is_fc"] = g["is_forecast"].first().fillna(False).astype(bool).values
    else:
        agg["_is_fc"] = False

    if "Ist_Azubi" in work.columns:
        agg["_ist_az"] = g["Ist_Azubi"].first().fillna(False).astype(bool).values
    else:
        agg["_ist_az"] = False

    # Flags per Series-Operationen auf dem Ergebnis-DataFrame
    _pnr_str = agg["PersNr"].astype(str)
    agg["is_existing_employee"] = agg["PersNr"].isin(base_ids)
    agg["is_new_hire"] = _pnr_str.str.startswith(("NH_", "TR_", "AZ_"))
    agg["is_forecast_employee"] = _pnr_str.str.startswith(("AZ_", "TR_", "NH_")) | agg["_is_fc"]
    agg["is_azubi_takeover"] = _pnr_str.str.startswith("AZ_") & (agg["MAK_sum"] > 0)

    # Event-Felder aus vorberechneten Lookup-Dicts
    agg["_abg"] = agg["PersNr"].map(lambda p: abg_lookup.get(p, ""))
    agg["_zug"] = agg["PersNr"].map(lambda p: zug_lookup.get(p, ""))
    agg["has_abgang_event"] = agg["_abg"].astype(bool)
    agg["has_zugang_event"] = agg["_zug"].astype(bool)
    _abg_m = agg["_abg"].astype(bool)
    _zug_m = agg["_zug"].astype(bool)
    agg["event_types"] = ""
    agg.loc[_abg_m & ~_zug_m, "event_types"] = agg.loc[_abg_m & ~_zug_m, "_abg"]
    agg.loc[~_abg_m & _zug_m, "event_types"] = agg.loc[~_abg_m & _zug_m, "_zug"]
    agg.loc[_abg_m & _zug_m, "event_types"] = (
        agg.loc[_abg_m & _zug_m, "_abg"] + " | " + agg.loc[_abg_m & _zug_m, "_zug"]
    )

    # String-Listen per groupby.apply — Reihenfolge aus DataFrame erhalten
    if "Planstelle" in work.columns:
        _pl_text = g["Planstelle"].apply(_series_text)
        agg["Planstelle"] = _pl_text.values
        agg["Rollenliste"] = _pl_text.values   # identisch mit Planstelle
    else:
        agg["Planstelle"] = ""
        agg["Rollenliste"] = ""

    if "Job" in work.columns:
        agg["Job"] = g["Job"].apply(_series_text).values
    else:
        agg["Job"] = ""

    # is_azubi: Ist_Azubi-first OR "azubi" im Planstelle-Text
    agg["is_azubi"] = agg["_ist_az"] | agg["Planstelle"].str.lower().str.contains("azubi", na=False)

    # MAK_je_Zeile: Float-Format mit Zeilenreihenfolge erhalten
    agg["MAK_je_Zeile"] = g["_mak_value"].apply(
        lambda s: " | ".join(f"{float(v):.4f}" for v in s.tolist())
    ).values

    agg["processing_stage"] = stage
    agg["MAK_column_used"] = mak_col or ""
    agg["MAK_person_policy_current"] = "sum_over_rows"
    agg["source_dataframe"] = source_dataframe

    return agg[[
        "processing_stage", "PersNr", "is_existing_employee", "is_forecast_employee",
        "is_new_hire", "is_azubi", "is_azubi_takeover",
        "has_abgang_event", "has_zugang_event", "event_types",
        "Jobfamily", "Planstelle", "Job", "Rollenliste",
        "Anzahl_Zeilen", "Anzahl_Planstellen", "MAK_column_used",
        "MAK_je_Zeile", "MAK_sum", "MAK_max", "MAK_min",
        "MAK_person_policy_current", "EUR_sum", "Vertragsart", "MitarbGruppenbez.",
        "source_dataframe",
    ]].reset_index(drop=True)


def _event_lookup(events_df: pd.DataFrame, persnr_col: str = "persnr") -> dict[str, str]:
    if events_df is None or events_df.empty or persnr_col not in events_df.columns:
        return {}
    events = events_df.copy()
    events[persnr_col] = _normalize_persnr_for_audit(events[persnr_col])
    type_col = "type" if "type" in events.columns else ("reason_code" if "reason_code" in events.columns else "")
    if not type_col:
        return {}
    return events.groupby(persnr_col)[type_col].apply(lambda s: " | ".join(sorted(set(s.dropna().astype(str))))).to_dict()


def build_mak_lineage_audit(
    stages: dict[str, pd.DataFrame],
    base_snapshot_df: pd.DataFrame,
    abgaenge_events: pd.DataFrame,
    zugaenge_events: pd.DataFrame,
) -> pd.DataFrame:
    if not stages:
        return pd.DataFrame()
    base_ids = set()
    if base_snapshot_df is not None and not base_snapshot_df.empty and "PersNr" in base_snapshot_df.columns:
        base_ids = set(_normalize_persnr_for_audit(base_snapshot_df["PersNr"].dropna()))
    stage_order = list(stages.keys())
    abg_lookup = _event_lookup(abgaenge_events, "persnr")
    zug_lookup = _event_lookup(zugaenge_events, "persnr")
    frames = [
        _person_stage_summary(
            df, stage, stage, base_ids, abgaenge_events, zugaenge_events,
            _abg_lookup=abg_lookup, _zug_lookup=zug_lookup,
        )
        for stage, df in stages.items()
    ]
    lineage = pd.concat([frame for frame in frames if frame is not None and not frame.empty], ignore_index=True) if frames else pd.DataFrame()
    if lineage.empty:
        return lineage
    first_gt1 = (
        lineage[pd.to_numeric(lineage["MAK_sum"], errors="coerce").fillna(0.0) > 1.000001]
        .sort_values("processing_stage", key=lambda s: s.map({stage: idx for idx, stage in enumerate(stage_order)}))
        .drop_duplicates("PersNr")
        .set_index("PersNr")["processing_stage"]
        .to_dict()
    )
    lineage["first_stage_with_MAK_gt_1"] = lineage["PersNr"].map(first_gt1).fillna("")
    lineage["mak_problem_origin"] = lineage.apply(_classify_mak_origin_from_stage, axis=1)
    lineage["comment"] = lineage.apply(
        lambda row: "MAK_sum_gt_1" if float(row.get("MAK_sum", 0.0) or 0.0) > 1.000001 else "unauffaellig",
        axis=1,
    )
    return lineage


def _classify_mak_origin_from_stage(row: pd.Series) -> str:
    first_stage = str(row.get("first_stage_with_MAK_gt_1", ""))
    if not first_stage:
        return ""
    if first_stage in {"01_rohdaten_ausgangsbestand", "02_forecast_base"}:
        return "bereits_im_ausgangsbestand"
    if first_stage == "03_nach_abgaenge":
        return "entsteht_durch_abgangslogik"
    if first_stage == "04_nach_zugaenge":
        return "entsteht_durch_zugangslogik"
    if first_stage == "05_nach_update_existing_rows" or first_stage == "06_nach_append_new_people_rows":
        return "entsteht_durch_snapshot_append"
    if first_stage == "07_final_future_snapshot":
        return "entsteht_durch_mak_spaltenlogik"
    return "nicht_entscheidbar"


def build_mak_origin_classification(lineage_audit: pd.DataFrame) -> pd.DataFrame:
    if lineage_audit is None or lineage_audit.empty:
        return pd.DataFrame()
    final = lineage_audit[lineage_audit["processing_stage"].eq("07_final_future_snapshot")].copy()
    final = final[pd.to_numeric(final["MAK_sum"], errors="coerce").fillna(0.0) > 1.000001].copy()
    if final.empty:
        return pd.DataFrame()
    final["Jobfamily_final"] = final["Jobfamily"]
    final["MAK_sum_final"] = final["MAK_sum"]
    final["evidence"] = (
        "first_gt1=" + final["first_stage_with_MAK_gt_1"].astype(str)
        + "; mak_values=" + final["MAK_je_Zeile"].astype(str)
        + "; rows=" + final["Anzahl_Zeilen"].astype(str)
    )
    final["relevant_event_types"] = final["event_types"]
    final["recommended_next_check"] = final["mak_problem_origin"].map({
        "bereits_im_ausgangsbestand": "Quelldaten und Mehrfachplanstellen im Startbestand pruefen",
        "entsteht_durch_abgangslogik": "Abgangsevents und Aktiv-/MAK-Status je Person pruefen",
        "entsteht_durch_zugangslogik": "Zugangsevents und interne Conversion pruefen",
        "entsteht_durch_snapshot_append": "Append-/Update-Logik auf doppelte Zeilen pruefen",
        "entsteht_durch_mak_spaltenlogik": "MAK-Spaltenprioritaet und Fallback pruefen",
    }).fillna("fehlende Zwischenstufe identifizieren")
    final["business_decision_needed"] = True
    cols = [
        "PersNr",
        "Jobfamily_final",
        "MAK_sum_final",
        "first_stage_with_MAK_gt_1",
        "mak_problem_origin",
        "evidence",
        "has_abgang_event",
        "has_zugang_event",
        "relevant_event_types",
        "recommended_next_check",
        "business_decision_needed",
    ]
    return final[cols].reset_index(drop=True)


def build_mak_deep_dive_15_cases(lineage_audit: pd.DataFrame, mak_origin: pd.DataFrame) -> pd.DataFrame:
    if lineage_audit is None or lineage_audit.empty or mak_origin is None or mak_origin.empty:
        return pd.DataFrame()
    stage_value = lineage_audit.pivot_table(index="PersNr", columns="processing_stage", values="MAK_sum", aggfunc="first")
    final = lineage_audit[lineage_audit["processing_stage"].eq("07_final_future_snapshot")].set_index("PersNr")
    rows = []
    for _, origin in mak_origin.iterrows():
        pid = origin["PersNr"]
        row = final.loc[pid]
        rows.append({
            "PersNr": pid,
            "final_Jobfamily": row.get("Jobfamily", ""),
            "final_MAK_sum": row.get("MAK_sum", 0.0),
            "final_MAK_max": row.get("MAK_max", 0.0),
            "final_EUR_sum": row.get("EUR_sum", 0.0),
            "Anzahl_Zeilen_final": row.get("Anzahl_Zeilen", 0),
            "Planstellenliste_final": row.get("Planstelle", ""),
            "Rollenliste_final": row.get("Rollenliste", ""),
            "MAK_je_Zeile_final": row.get("MAK_je_Zeile", ""),
            "Ausgangsbestand_MAK_sum": stage_value.get("01_rohdaten_ausgangsbestand", pd.Series(dtype=float)).get(pid, 0.0),
            "Nach_Abgaenge_MAK_sum": stage_value.get("03_nach_abgaenge", pd.Series(dtype=float)).get(pid, 0.0),
            "Nach_Zugaenge_MAK_sum": stage_value.get("04_nach_zugaenge", pd.Series(dtype=float)).get(pid, 0.0),
            "Final_Snapshot_MAK_sum": row.get("MAK_sum", 0.0),
            "has_abgang_event": origin.get("has_abgang_event", False),
            "abgang_event_types": row.get("event_types", "") if origin.get("has_abgang_event", False) else "",
            "has_zugang_event": origin.get("has_zugang_event", False),
            "zugang_event_types": row.get("event_types", "") if origin.get("has_zugang_event", False) else "",
            "first_stage_with_MAK_gt_1": origin.get("first_stage_with_MAK_gt_1", ""),
            "mak_problem_origin": origin.get("mak_problem_origin", ""),
            "wahrscheinliche_Ursache": _deep_dive_likely_cause(row),
            "fachliche_Interpretation": "Bestands-Mehrfachplanstelle/Datenfehler fachlich pruefen",
            "empfohlene_Prüffrage": "Ist MAK_sum > 1 fuer diese Person fachlich erlaubt oder eine doppelte Planstellen-/Beschaeftigungsgradzaehlung?",
            "empfohlene_Behandlung": "keine automatische Korrektur; Fachentscheid zu Cap/Split/Ausnahme",
            "effect_if_cap_at_1": min(float(row.get("MAK_sum", 0.0)), 1.0) - float(row.get("MAK_sum", 0.0)),
            "effect_if_max": float(row.get("MAK_max", 0.0)) - float(row.get("MAK_sum", 0.0)),
            "effect_if_keep_sum": 0.0,
        })
    out = pd.DataFrame(rows)
    out["_prio"] = out["final_Jobfamily"].astype(str).ne("Führung Vertrieb")
    return out.sort_values(["_prio", "final_MAK_sum", "final_EUR_sum"], ascending=[True, False, False]).drop(columns="_prio").reset_index(drop=True)


def _deep_dive_likely_cause(row: pd.Series) -> str:
    values = [float(v.strip()) for v in str(row.get("MAK_je_Zeile", "")).split("|") if v.strip()]
    if any(v > 1.000001 for v in values):
        return "datenfehler_beschaeftigungsgrad_oder_mak_zeile_gt_1"
    if len(values) > 1 and sum(values) > 1.000001:
        return "mehrfachplanstelle_oder_rollensplit_im_bestand"
    return "nicht_entscheidbar"


def build_mak_column_consistency_check(final_snapshot_df: pd.DataFrame) -> pd.DataFrame:
    if final_snapshot_df is None or final_snapshot_df.empty:
        return pd.DataFrame()
    work = final_snapshot_df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    work = work[work.get("PersNr", pd.Series(index=work.index)).notna()].copy()
    if work.empty:
        return pd.DataFrame()
    selected = select_mak_column(work)
    numeric_cols = {}
    for col in ["MAK_Calculated", "mak", "MAK", "FTE_person"]:
        if col in work.columns:
            numeric_cols[col] = _numeric(work[col])
        else:
            numeric_cols[col] = pd.Series(0.0, index=work.index)
    if "BsGrd" in work.columns:
        numeric_cols["BsGrd"] = _numeric(work["BsGrd"]) / 100.0
    else:
        numeric_cols["BsGrd"] = pd.Series(0.0, index=work.index)
    values_df = pd.DataFrame(numeric_cols)
    work["selected_MAK_column"] = selected or ""
    work["selected_MAK_value"] = numeric_cols.get(selected, pd.Series(0.0, index=work.index)) if selected else 0.0
    work["max_difference_between_mak_columns"] = values_df.max(axis=1) - values_df.min(axis=1)
    work["inconsistency_flag"] = work["max_difference_between_mak_columns"].gt(0.0001)
    work["recommended_action"] = work["inconsistency_flag"].map({
        True: "MAK-Spalten angleichen oder fuehrende Spalte dokumentieren",
        False: "keine",
    })
    cols = [
        "PersNr", "Jobfamily", "Planstelle", "MAK_Calculated", "mak", "MAK", "BsGrd", "FTE_person",
        "selected_MAK_column", "selected_MAK_value", "max_difference_between_mak_columns",
        "inconsistency_flag", "recommended_action",
    ]
    for col in cols:
        if col not in work.columns:
            work[col] = pd.NA
    return work[cols].reset_index(drop=True)


def _stage_totals(lineage_audit: pd.DataFrame, stage: str) -> tuple[int, float, float]:
    part = lineage_audit[lineage_audit["processing_stage"].eq(stage)]
    if part.empty:
        return 0, 0.0, 0.0
    return (
        int(part["PersNr"].nunique()),
        float(pd.to_numeric(part["MAK_sum"], errors="coerce").fillna(0).sum()),
        float(pd.to_numeric(part["EUR_sum"], errors="coerce").fillna(0).sum()),
    )


def build_mak_reconciliation_bridge(
    lineage_audit: pd.DataFrame,
    abgaenge_events: pd.DataFrame,
    zugaenge_events: pd.DataFrame,
) -> pd.DataFrame:
    if lineage_audit is None or lineage_audit.empty:
        return pd.DataFrame()
    start = _stage_totals(lineage_audit, "01_rohdaten_ausgangsbestand")
    after_abg = _stage_totals(lineage_audit, "03_nach_abgaenge")
    after_zug = _stage_totals(lineage_audit, "04_nach_zugaenge")
    after_append = _stage_totals(lineage_audit, "06_nach_append_new_people_rows")
    final = _stage_totals(lineage_audit, "07_final_future_snapshot")

    def event_delta(events: pd.DataFrame, pattern: str) -> tuple[int, float]:
        if events is None or events.empty:
            return 0, 0.0
        type_col = "type" if "type" in events.columns else ("reason_code" if "reason_code" in events.columns else "")
        if not type_col:
            return 0, 0.0
        part = events[events[type_col].astype(str).str.contains(pattern, case=False, na=False)].copy()
        if part.empty:
            return 0, 0.0
        head_series = part["headcount_change"] if "headcount_change" in part.columns else pd.Series(0, index=part.index)
        mak_series = part["mak_change"] if "mak_change" in part.columns else pd.Series(0.0, index=part.index)
        head = int(pd.to_numeric(head_series, errors="coerce").fillna(0).sum())
        mak = float(pd.to_numeric(mak_series, errors="coerce").fillna(0).sum())
        return head, mak

    rows = []
    rows.append({"Schritt": "Startbestand", "Köpfe_delta": start[0], "MAK_delta": start[1], "EUR_delta": start[2], "Erklärung": "Rohdaten Ausgangsbestand", "Quelle": "01_rohdaten_ausgangsbestand", "Plausibilitätsstatus": "Basis"})
    rows.append({"Schritt": "Abgänge gesamt", "Köpfe_delta": after_abg[0] - start[0], "MAK_delta": after_abg[1] - start[1], "EUR_delta": after_abg[2] - start[2], "Erklärung": "Differenz nach Anwendung Abgänge", "Quelle": "03_nach_abgaenge minus Start", "Plausibilitätsstatus": "OK" if after_abg[1] <= start[1] else "WARN"})
    for label, pattern in [("davon Rente", "RET|Rente|Pension"), ("davon Fluktuation", "FLUCT|Fluktuation|Kuend|Künd"), ("davon ATZ oder sonstige Abgänge", "ATZ|RUHEND|SONST|OTHER")]:
        head, mak = event_delta(abgaenge_events, pattern)
        rows.append({"Schritt": label, "Köpfe_delta": head, "MAK_delta": mak, "EUR_delta": 0.0, "Erklärung": "Eventbasierter Teilbetrag, informativ", "Quelle": "abgaenge events", "Plausibilitätsstatus": "INFO"})
    rows.append({"Schritt": "Zugänge gesamt", "Köpfe_delta": after_zug[0] - after_abg[0], "MAK_delta": after_zug[1] - after_abg[1], "EUR_delta": after_zug[2] - after_abg[2], "Erklärung": "Differenz nach Zugangs-Final-State", "Quelle": "04_nach_zugaenge minus 03_nach_abgaenge", "Plausibilitätsstatus": "OK"})
    for label, pattern in [("davon New_Hire", "New_Hire"), ("davon Azubi_Hire", "Azubi_Hire"), ("davon Azubi_Conversion_In", "Azubi_Conversion_In"), ("davon Azubi_Conversion_Out", "Azubi_Conversion_Out"), ("davon Trainee_Hire", "Trainee_Hire")]:
        head, mak = event_delta(zugaenge_events, pattern)
        rows.append({"Schritt": label, "Köpfe_delta": head, "MAK_delta": mak, "EUR_delta": 0.0, "Erklärung": "Eventbasierter Teilbetrag, Conversions koennen ohne Netto-Kopfeffekt sein", "Quelle": "zugaenge events", "Plausibilitätsstatus": "INFO"})
    rows.append({"Schritt": "Interne Umbuchungen oder Conversions ohne Netto Kopf Effekt", "Köpfe_delta": 0, "MAK_delta": event_delta(zugaenge_events, "Conversion")[1], "EUR_delta": 0.0, "Erklärung": "Azubi Conversion MAK-Aufbau ohne doppelte Kopfzählung", "Quelle": "zugaenge events", "Plausibilitätsstatus": "INFO"})
    rows.append({"Schritt": "Snapshot Append Effekt", "Köpfe_delta": after_append[0] - after_zug[0], "MAK_delta": after_append[1] - after_zug[1], "EUR_delta": after_append[2] - after_zug[2], "Erklärung": "Differenz nach Aufbau finaler Snapshot-Zeilen", "Quelle": "06_nach_append_new_people_rows minus 04_nach_zugaenge", "Plausibilitätsstatus": "OK"})
    rows.append({"Schritt": "Finaler Zielbestand", "Köpfe_delta": final[0], "MAK_delta": final[1], "EUR_delta": final[2], "Erklärung": "Finale Snapshot-Summe", "Quelle": "07_final_future_snapshot", "Plausibilitätsstatus": "Final"})
    explained_final = after_append[1]
    rows.append({"Schritt": "Differenz unerklärt", "Köpfe_delta": final[0] - after_append[0], "MAK_delta": final[1] - explained_final, "EUR_delta": final[2] - after_append[2], "Erklärung": "Finalisierung/Kosten/Exclusions nach Append", "Quelle": "07_final minus 06_append", "Plausibilitätsstatus": "OK" if abs(final[1] - explained_final) <= 0.01 else "WARN"})
    return pd.DataFrame(rows)


def build_mak_fuehrung_vertrieb_check(lineage_audit: pd.DataFrame, mak_decision_list: pd.DataFrame) -> pd.DataFrame:
    if lineage_audit is None or lineage_audit.empty:
        return pd.DataFrame()
    rows = []
    for stage in [
        "01_rohdaten_ausgangsbestand",
        "03_nach_abgaenge",
        "04_nach_zugaenge",
        "07_final_future_snapshot",
    ]:
        part = lineage_audit[(lineage_audit["processing_stage"].eq(stage)) & (lineage_audit["Jobfamily"].astype(str).eq("Führung Vertrieb"))].copy()
        anomalous = part[pd.to_numeric(part["MAK_sum"], errors="coerce").fillna(0) > 1.000001]
        rows.append({
            "Stufe": stage,
            "Köpfe": int(part["PersNr"].nunique()),
            "MAK": float(pd.to_numeric(part["MAK_sum"], errors="coerce").fillna(0).sum()),
            "Anzahl Personen mit MAK_sum > 1": int(anomalous["PersNr"].nunique()),
            "PersNr der betroffenen Personen": " | ".join(anomalous["PersNr"].astype(str).tolist()),
        })
    out = pd.DataFrame(rows)
    final_mak = float(out.loc[out["Stufe"].eq("07_final_future_snapshot"), "MAK"].iloc[0]) if not out.empty else 0.0
    fv_dec = mak_decision_list[mak_decision_list["Jobfamily"].astype(str).eq("Führung Vertrieb")] if mak_decision_list is not None and not mak_decision_list.empty else pd.DataFrame()
    affected_sum = float(fv_dec["option_sum"].sum()) if not fv_dec.empty else 0.0
    cap_sum = float(fv_dec["option_cap_at_1"].sum()) if not fv_dec.empty else 0.0
    max_sum = float(fv_dec["option_max"].sum()) if not fv_dec.empty else 0.0
    out["Anteil MAK Problem an finaler MAK"] = affected_sum / final_mak if final_mak else 0.0
    out["MAK final current_sum"] = final_mak
    out["MAK final cap_at_1 für auffällige Personen"] = final_mak - affected_sum + cap_sum
    out["MAK final max_per_person"] = final_mak - affected_sum + max_sum
    out["Interpretation"] = "Auffaelligkeit ist bereits im Ausgangsbestand sichtbar; Zugänge/Abgänge sind nicht Hauptursache"
    return out


def build_mak_abgaenge_check(lineage_audit: pd.DataFrame, abgaenge_events: pd.DataFrame) -> pd.DataFrame:
    if abgaenge_events is None or abgaenge_events.empty:
        return pd.DataFrame()
    before = lineage_audit[lineage_audit["processing_stage"].eq("02_forecast_base")].set_index("PersNr") if lineage_audit is not None and not lineage_audit.empty else pd.DataFrame()
    after = lineage_audit[lineage_audit["processing_stage"].eq("03_nach_abgaenge")].set_index("PersNr") if lineage_audit is not None and not lineage_audit.empty else pd.DataFrame()
    events = abgaenge_events.copy()
    events["persnr"] = _normalize_persnr_for_audit(events["persnr"])
    rows = []
    for _, event in events.iterrows():
        pid = event["persnr"]
        before_mak = float(before.loc[pid, "MAK_sum"]) if pid in before.index else 0.0
        after_mak = float(after.loc[pid, "MAK_sum"]) if pid in after.index else 0.0
        head_change = int(event.get("headcount_change", 0) or 0)
        expected = 0.0 if head_change < 0 else max(0.0, before_mak + float(event.get("mak_change", 0.0) or 0.0))
        issue = (head_change < 0 and after_mak > 0.000001)
        rows.append({
            "PersNr": pid,
            "Abgang_Event_Type": event.get("reason_code", event.get("type", "")),
            "Abgang_Date": event.get("event_date", pd.NaT),
            "Jobfamily_before": before.loc[pid, "Jobfamily"] if pid in before.index else "",
            "MAK_before": before_mak,
            "MAK_after_abgang": after_mak,
            "active_after_abgang": after_mak > 0,
            "row_removed_or_deactivated": after_mak <= 0.000001,
            "expected_MAK_after_abgang": expected,
            "actual_MAK_after_abgang": after_mak,
            "deviation": after_mak - expected,
            "issue_flag": issue,
        })
    return pd.DataFrame(rows)


def build_mak_zugaenge_check(lineage_audit: pd.DataFrame, zugaenge_events: pd.DataFrame, base_snapshot_df: pd.DataFrame) -> pd.DataFrame:
    if zugaenge_events is None or zugaenge_events.empty:
        return pd.DataFrame()
    base_ids = set(_normalize_persnr_for_audit(base_snapshot_df["PersNr"].dropna())) if base_snapshot_df is not None and not base_snapshot_df.empty and "PersNr" in base_snapshot_df.columns else set()
    after = lineage_audit[lineage_audit["processing_stage"].eq("04_nach_zugaenge")].set_index("PersNr") if lineage_audit is not None and not lineage_audit.empty else pd.DataFrame()
    final = lineage_audit[lineage_audit["processing_stage"].eq("07_final_future_snapshot")].set_index("PersNr") if lineage_audit is not None and not lineage_audit.empty else pd.DataFrame()
    events = zugaenge_events.copy()
    events["persnr"] = _normalize_persnr_for_audit(events["persnr"])
    rows = []
    for _, event in events.iterrows():
        pid = event["persnr"]
        event_type = str(event.get("type", ""))
        is_conversion = "Conversion" in event_type
        already = pid in base_ids
        created = float(after.loc[pid, "MAK_sum"]) if pid in after.index else 0.0
        expected = float(event.get("mak_change", event.get("mak", 0.0)) or 0.0)
        duplicate_created = pid in final.index and int(final.loc[pid, "Anzahl_Zeilen"]) > 1 and not already and not is_conversion
        rows.append({
            "PersNr": pid,
            "Zugang_Event_Type": event_type,
            "Zugang_Date": event.get("event_date", pd.NaT),
            "is_new_person": not already and not is_conversion,
            "is_internal_conversion": is_conversion,
            "Jobfamily_after": after.loc[pid, "Jobfamily"] if pid in after.index else "",
            "MAK_created": created,
            "expected_MAK_created": expected,
            "active_after_zugang": pid in after.index and created > 0,
            "already_existed_before": already,
            "duplicate_created": duplicate_created,
            "issue_flag": (created > 1.000001 and not is_conversion) or duplicate_created,
        })
    return pd.DataFrame(rows)


def build_mak_source_row_audit_15(base_snapshot_df: pd.DataFrame, mak_origin: pd.DataFrame) -> pd.DataFrame:
    if base_snapshot_df is None or base_snapshot_df.empty or mak_origin is None or mak_origin.empty:
        return pd.DataFrame()
    target_ids = set(mak_origin["PersNr"].astype(str))
    work = base_snapshot_df.copy().reset_index(names="Source_Row_ID")
    work["PersNr"] = _normalize_persnr_for_audit(work["PersNr"])
    work = work[work["PersNr"].isin(target_ids)].copy()
    if work.empty:
        return pd.DataFrame()
    mak_col = select_mak_column(work)
    if mak_col == "BsGrd":
        selected_mak = _numeric(work[mak_col]) / 100.0
    elif mak_col:
        selected_mak = _numeric(work[mak_col])
    else:
        selected_mak = pd.Series(0.0, index=work.index)
    planstelle = work.get("Planstelle", work.get("Job", pd.Series("", index=work.index))).astype(str).str.strip()
    job = work.get("Job", planstelle).astype(str).str.strip()
    rolle = work.get("Rolle", job).astype(str).str.strip()
    work["Beschäftigungsgrad roh"] = work.get("Beschäftigungsgrad", work.get("BsGrd", pd.NA))
    work["duplicate_key"] = (
        work["PersNr"].astype(str)
        + "|" + planstelle
        + "|" + rolle
        + "|" + selected_mak.round(6).astype(str)
    )
    dup = work["duplicate_key"].duplicated(keep=False)
    work["row_pattern"] = "einzelzeile"
    work.loc[dup, "row_pattern"] = "potenzielle_doppelzeile"
    work.loc[selected_mak.gt(1.000001), "row_pattern"] = "mak_zeile_gt_1"
    work["source_issue_flag"] = dup | selected_mak.gt(1.000001)
    work["comment"] = work["source_issue_flag"].map({
        True: "Quellzeile fuer MAK > 1 fachlich pruefen",
        False: "Quellzeile Teil der Personensumme > 1",
    })
    defaults = {
        "Source_File": pd.NA,
        "Source_Sheet": pd.NA,
        "Planstellen_ID": pd.NA,
        "Rolle": rolle,
        "Gueltig_ab": pd.NA,
        "Gueltig_bis": pd.NA,
        "active": True,
        "MAK": selected_mak,
        "mak": selected_mak,
        "MAK_Calculated": selected_mak,
        "FTE_person": selected_mak,
    }
    for col, value in defaults.items():
        if col not in work.columns:
            work[col] = value
    cols = [
        "PersNr", "Source_Row_ID", "Source_File", "Source_Sheet", "Jobfamily", "JF-Cluster", "OE-Cluster",
        "Organisationseinheit", "Planstelle", "Planstellen_ID", "Job", "Rolle", "Vertragsart", "MitarbGruppenbez.",
        "Beschäftigungsgrad roh", "BsGrd", "MAK", "mak", "MAK_Calculated", "FTE_person", "Total_Cost_Year",
        "Gueltig_ab", "Gueltig_bis", "active", "duplicate_key", "row_pattern", "source_issue_flag", "comment",
    ]
    for col in cols:
        if col not in work.columns:
            work[col] = pd.NA
    return work[cols].sort_values(["PersNr", "Source_Row_ID"]).reset_index(drop=True)


def _classify_source_pattern(rows: pd.DataFrame) -> tuple[str, str, str, str, str]:
    mak_values = pd.to_numeric(rows["MAK_Calculated"], errors="coerce").fillna(0.0).tolist()
    planstellen = rows.get("Planstelle", pd.Series("", index=rows.index)).astype(str).str.strip()
    rollen = rows.get("Rolle", rows.get("Job", pd.Series("", index=rows.index))).astype(str).str.strip()
    unique_plan = planstellen.nunique()
    evidence = f"rows={len(rows)}; planstellen={unique_plan}; mak=" + " | ".join(f"{v:.4f}" for v in mak_values)
    text = " ".join(planstellen.tolist() + rollen.tolist()).lower()
    if len(rows) == 1 and any(abs(v - 2.0) < 0.0001 for v in mak_values):
        return (
            "einzelzeile_mit_mak_2",
            evidence,
            "eine Quellzeile weist MAK 2,0 aus",
            "Quellwert korrigieren oder Ausnahme dokumentieren",
            "Warum hat eine einzelne Person in einer Quellzeile 2,0 MAK?",
        )
    if any(abs(v - 2.0) < 0.0001 for v in mak_values):
        return (
            "einzelzeile_mit_mak_2",
            evidence,
            "dominante Quellzeile mit MAK 2,0",
            "Quellwert korrigieren oder Ausnahme dokumentieren",
            "Ist die 2,0-MAK-Zeile ein Datenfehler oder eine echte Mehrfachbeschaeftigung?",
        )
    if len(mak_values) == 2 and sorted(round(v, 4) for v in mak_values) in ([0.98, 1.02], [0.9899, 1.0101], [0.9772, 1.0228]):
        return (
            "beschaeftigungsgrad_102_98_problem",
            evidence,
            "zwei aktive Quellzeilen summieren sich ueber 1 durch 102/98-Muster",
            "Personenkapazitaet auf 1,0 begrenzen oder Quelle korrigieren",
            "Ist 1,02 + 0,98 eine intendierte Uebergabe oder ein Beschaeftigungsgrad-Fehler?",
        )
    duplicate_like = len(rows) > 1 and unique_plan == 1 and rollen.nunique() <= 1 and len(set(round(v, 6) for v in mak_values)) <= 1
    if duplicate_like:
        return (
            "identische_doppelzeile",
            evidence,
            "gleiche Person mit identischer Planstelle/Rolle mehrfach aktiv",
            "Doppelzeile entfernen oder nur einmal als Personalkapazitaet zaehlen",
            "Handelt es sich um eine echte zweite Beschaeftigung oder eine doppelte Quellzeile?",
        )
    if any(token in text for token in ["nachfolge", "stv", "stellvertret", "uebergabe", "übergabe", "+"]):
        return (
            "uebergabe_oder_nachfolge_doppelt_aktiv",
            evidence,
            "Planstellen-/Rollentext deutet auf Nachfolge, Stellvertretung oder temporaere Doppelrolle",
            "Fachlich entscheiden: temporaer zulassen oder als Split mit max. 1,0 modellieren",
            "Soll diese Uebergabe/Nachfolge mehr als 1,0 Personalkapazitaet erzeugen?",
        )
    if len(rows) > 1 and unique_plan > 1:
        return (
            "planstellen_split_summe_ueber_1",
            evidence,
            "mehrere Planstellen/Rollen einer Person summieren sich ueber 1,0",
            "Personalkapazitaet auf 1,0 begrenzen und proportional verteilen",
            "Ist das ein Planstellensplit einer Person oder eine echte Mehrfachbeschaeftigung?",
        )
    return (
        "nicht_entscheidbar",
        evidence,
        "Rohzeilen reichen nicht fuer eindeutige Klassifikation",
        "Fachentscheidung und ggf. Quelleinsicht erforderlich",
        "Welche fachliche Kapazitaet soll fuer diese Person gelten?",
    )


def build_mak_source_pattern_classification(source_rows_15: pd.DataFrame) -> pd.DataFrame:
    if source_rows_15 is None or source_rows_15.empty:
        return pd.DataFrame()
    rows = []
    for pid, grp in source_rows_15.groupby("PersNr", dropna=False):
        mak_values = pd.to_numeric(grp["MAK_Calculated"], errors="coerce").fillna(0.0)
        pattern, evidence, cause, treatment, question = _classify_source_pattern(grp)
        rows.append({
            "PersNr": pid,
            "Jobfamily": _first_value(grp, "Jobfamily", ""),
            "MAK_sum": float(mak_values.sum()),
            "Anzahl_Rohzeilen": int(len(grp)),
            "Anzahl_Planstellen": int(grp["Planstelle"].dropna().astype(str).str.strip().nunique()),
            "Planstellenliste": _series_text(grp["Planstelle"]),
            "Rollenliste": _series_text(grp["Rolle"]),
            "MAK_je_Rohzeile": " | ".join(f"{v:.4f}" for v in mak_values.tolist()),
            "Muster": pattern,
            "evidence": evidence,
            "wahrscheinlichste_Ursache": cause,
            "empfohlene_Behandlung": treatment,
            "business_decision_needed": True,
            "decision_question": question,
        })
    return pd.DataFrame(rows).sort_values(["Jobfamily", "PersNr"]).reset_index(drop=True)


def build_mak_correction_decision_15(patterns: pd.DataFrame) -> pd.DataFrame:
    if patterns is None or patterns.empty:
        return pd.DataFrame()
    rows = []
    for _, row in patterns.iterrows():
        current = float(row.get("MAK_sum", 0.0) or 0.0)
        pattern = str(row.get("Muster", ""))
        if pattern == "echte_mehrfachbeschaeftigung_moeglich":
            default = current
            required_exception = True
            source_correction = False
            policy = "Ausnahme nur mit dokumentiertem Mehrfachbeschaeftigungsverhaeltnis"
        elif pattern == "nicht_entscheidbar":
            default = current
            required_exception = True
            source_correction = False
            policy = "Validation Error bis Fachentscheidung"
        else:
            default = 1.0
            required_exception = False
            source_correction = pattern in {"identische_doppelzeile", "einzelzeile_mit_mak_2", "beschaeftigungsgrad_102_98_problem"}
            policy = "Personenkapazitaet standardmaessig max. 1,0"
        rows.append({
            "PersNr": row.get("PersNr", ""),
            "Jobfamily": row.get("Jobfamily", ""),
            "current_MAK": current,
            "proposed_MAK_default": default,
            "proposed_MAK_if_exception": current,
            "proposed_MAK_if_source_error": 1.0,
            "proposed_MAK_if_planstellen_split": 1.0,
            "recommended_default_policy": policy,
            "recommended_value": default,
            "delta_MAK_recommended": default - current,
            "effect_on_jobfamily": default - current,
            "effect_on_total_MAK": default - current,
            "required_source_correction": source_correction,
            "required_exception_flag": required_exception,
            "required_business_approval": True,
            "decision_status": "offen",
        })
    return pd.DataFrame(rows).reset_index(drop=True)


def build_mak_target_value_scenarios(
    final_snapshot_df: pd.DataFrame,
    correction_decision_15: pd.DataFrame,
) -> pd.DataFrame:
    if final_snapshot_df is None or final_snapshot_df.empty:
        return pd.DataFrame()
    work = final_snapshot_df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    mak_col = select_mak_column(work)
    if mak_col == "BsGrd":
        work["_mak"] = _numeric(work[mak_col]) / 100.0
    elif mak_col:
        work["_mak"] = _numeric(work[mak_col])
    else:
        work["_mak"] = 0.0
    current_total = float(work["_mak"].sum())
    current_fv = float(work.loc[work.get("Jobfamily", "").astype(str).eq("Führung Vertrieb"), "_mak"].sum())
    decisions = correction_decision_15.copy() if correction_decision_15 is not None else pd.DataFrame()
    affected = int(decisions["PersNr"].nunique()) if not decisions.empty else 0
    current_affected = float(decisions["current_MAK"].sum()) if not decisions.empty else 0.0
    default_affected = float(decisions["recommended_value"].sum()) if not decisions.empty else 0.0
    fv_dec = decisions[decisions["Jobfamily"].astype(str).eq("Führung Vertrieb")] if not decisions.empty else pd.DataFrame()
    fv_current_affected = float(fv_dec["current_MAK"].sum()) if not fv_dec.empty else 0.0
    fv_default_affected = float(fv_dec["recommended_value"].sum()) if not fv_dec.empty else 0.0

    scenarios = [
        ("current_uncorrected", current_total, current_fv, affected, affected, affected, "nicht geeignet ohne Ausnahmefreigabe", "Keine Korrektur; enthaelt unkommentierte MAK > 1"),
        ("strict_person_capacity", current_total - current_affected + affected, current_fv - fv_current_affected + len(fv_dec), affected, 0, affected, "geeignet als vorsichtiger Arbeitswert", "Jede betroffene Person maximal 1,0 MAK"),
        ("source_pattern_default", current_total - current_affected + default_affected, current_fv - fv_current_affected + fv_default_affected, affected, int((decisions["recommended_value"] > 1.000001).sum()) if not decisions.empty else 0, affected, "geeignet nach Fachreview der Muster", "Empfohlene Default-Behandlung je Quellmuster"),
        ("exception_whitelist", current_total - current_affected + affected, current_fv - fv_current_affected + len(fv_dec), affected, 0, affected, "geeignet, wenn Ausnahmen separat gepflegt werden", "Ohne Exception-Flag gilt max. 1,0"),
        ("manual_business_decision", current_total, current_fv, affected, affected, affected, "erst nach manueller Freigabe geeignet", "Platzhalter fuer dokumentierte Einzelentscheidungen"),
    ]
    rows = []
    for scenario, total, fv_mak, affected_persons, remaining, required, suitability, comment in scenarios:
        rows.append({
            "scenario": scenario,
            "total_MAK": total,
            "delta_vs_current": total - current_total,
            "Führung_Vertrieb_MAK": fv_mak,
            "Führung_Vertrieb_delta": fv_mak - current_fv,
            "affected_persons": affected_persons,
            "remaining_MAK_gt_1_persons": remaining,
            "required_business_decisions": required,
            "suitability_for_reporting": suitability,
            "comment": comment,
        })
    return pd.DataFrame(rows)


def build_mak_fuehrung_vertrieb_decision(
    source_rows_15: pd.DataFrame,
    patterns: pd.DataFrame,
    correction_decision_15: pd.DataFrame,
) -> pd.DataFrame:
    if patterns is None or patterns.empty:
        return pd.DataFrame()
    fv_patterns = patterns[patterns["Jobfamily"].astype(str).eq("Führung Vertrieb")].copy()
    if fv_patterns.empty:
        return pd.DataFrame()
    dec = correction_decision_15.set_index("PersNr") if correction_decision_15 is not None and not correction_decision_15.empty else pd.DataFrame()
    rows = []
    for _, row in fv_patterns.iterrows():
        pid = row["PersNr"]
        src = source_rows_15[source_rows_15["PersNr"].astype(str).eq(str(pid))] if source_rows_15 is not None and not source_rows_15.empty else pd.DataFrame()
        current_eur = float(pd.to_numeric(src.get("Total_Cost_Year", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not src.empty else 0.0
        recommended = float(dec.loc[pid, "recommended_value"]) if not dec.empty and pid in dec.index else 1.0
        current = float(row["MAK_sum"])
        rows.append({
            "PersNr": pid,
            "Rolle": row.get("Rollenliste", ""),
            "Planstelle": row.get("Planstellenliste", ""),
            "MAK_je_Rohzeile": row.get("MAK_je_Rohzeile", ""),
            "Muster": row.get("Muster", ""),
            "current_MAK": current,
            "recommended_MAK": recommended,
            "delta_MAK": recommended - current,
            "current_EUR": current_eur,
            "recommended_EUR_logic": "EUR nach finaler MAK-Policy neu proportional/regelbasiert berechnen",
            "recommended_action": row.get("empfohlene_Behandlung", ""),
            "decision_question": row.get("decision_question", ""),
        })
    return pd.DataFrame(rows).reset_index(drop=True)


def build_65plus_decision_list(age65_audit: pd.DataFrame) -> pd.DataFrame:
    if age65_audit is None or age65_audit.empty:
        return pd.DataFrame()
    work = age65_audit.copy()
    work["Regelaltersgrenze_erreicht"] = True
    for col in ["Weiterbeschaeftigung_Flag", "explicit_extension_flag"]:
        if col in work.columns:
            work["explicit_extension_flag"] = work[col].fillna(False).astype(bool)
            break
    if "explicit_extension_flag" not in work.columns:
        work["explicit_extension_flag"] = False
    work["extension_until"] = work.get("Weiterbeschaeftigung_bis", pd.NA)
    work["recommended_policy"] = work["explicit_extension_flag"].map(
        {True: "weiterbeschaeftigung_dokumentiert_pruefen", False: "fachentscheidung_weiterbeschaeftigung_erforderlich"}
    )
    work["business_decision_needed"] = ~work["explicit_extension_flag"]
    work["proposed_policy"] = work["explicit_extension_flag"].map(
        {True: "weiterbeschaeftigung_bis_extension_until_zulassen", False: "ohne_explizites_flag_fachlich_pruefen"}
    )
    work["required_flag"] = "explicit_extension_flag"
    work["decision_owner"] = "Fachbereich Personal / HR Governance"
    work["decision_status"] = work["explicit_extension_flag"].map({True: "dokumentiert_pruefen", False: "offen"})
    work["recommended_business_question"] = (
        "Soll diese Person trotz erreichter Regelaltersgrenze aktiv bleiben; "
        "gibt es eine dokumentierte Weiterbeschaeftigung und bis wann gilt sie?"
    )
    return work.reset_index(drop=True)


def build_65plus_audit(df: pd.DataFrame, params: dict[str, Any] | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()

    age = pd.to_numeric(work.get("Alter_Jahre", work.get("Alter", pd.Series(0, index=work.index))), errors="coerce")
    work = work[age >= 65].copy()
    if work.empty:
        return pd.DataFrame()

    params = params or {}
    annual_rate = float(params.get("retirement", {}).get("rent_rate_65", 0.9)) if isinstance(params, dict) else 0.9
    work["Rentenwahrscheinlichkeit"] = annual_rate
    work["Grund_fuer_Verbleib"] = "probabilistische_rentenlogik_oder_fachliche_weiterbeschaeftigung_pruefen"
    mak_col = select_mak_column(work)
    work["MAK"] = _numeric(work[mak_col]) if mak_col and mak_col != "BsGrd" else (_numeric(work[mak_col]) / 100.0 if mak_col == "BsGrd" else 0.0)
    work["EUR"] = _numeric(work["Total_Cost_Year"]) if "Total_Cost_Year" in work.columns else 0.0
    work["Alter"] = age.loc[work.index]

    cols = [
        "PersNr",
        "Alter",
        "Jobfamily",
        "MAK",
        "EUR",
        "Vertragsart",
        "ATZ_Status",
        "Rentenwahrscheinlichkeit",
        "Grund_fuer_Verbleib",
    ]
    for col in cols:
        if col not in work.columns:
            work[col] = pd.NA
    return work[cols].sort_values("Alter", ascending=False).reset_index(drop=True)


def build_validation_checks(
    summary_df: pd.DataFrame,
    future_snapshot_df: pd.DataFrame,
    audit_tables: dict[str, pd.DataFrame] | None,
    params: dict[str, Any] | None,
) -> pd.DataFrame:
    audit_tables = audit_tables or {}
    rows: list[dict[str, Any]] = []

    mak_audit = audit_tables.get("MAK_Personen_Audit", pd.DataFrame())
    if not mak_audit.empty:
        without_exception = mak_audit[
            (pd.to_numeric(mak_audit.get("MAK_sum", 0), errors="coerce").fillna(0) > 1.000001)
            & ~mak_audit.get("Auffaelligkeit", "").astype(str).str.contains("dokumentiert", na=False)
        ]
        count = int(len(without_exception))
    else:
        count = 0
    rows.append({"Check": "personen_mit_mak_sum_gt_1_ohne_ausnahme", "Wert": count, "Status": "WARN" if count else "OK"})

    if summary_df is not None and not summary_df.empty and {"Jobfamily", "MAK"}.issubset(summary_df.columns):
        head_col = "Köpfe" if "Köpfe" in summary_df.columns else "Koepfe"
        if head_col in summary_df.columns:
            bad_jf = summary_df[pd.to_numeric(summary_df["MAK"], errors="coerce").fillna(0) > pd.to_numeric(summary_df[head_col], errors="coerce").fillna(0)]
            value = int(len(bad_jf))
            detail = " | ".join(bad_jf["Jobfamily"].astype(str).tolist())
        else:
            value, detail = 0, ""
    else:
        value, detail = 0, ""
    rows.append({"Check": "jobfamilies_mit_mak_gt_koepfe", "Wert": value, "Status": "WARN" if value else "OK", "Details": detail})

    snapshot = future_snapshot_df.copy() if future_snapshot_df is not None else pd.DataFrame()
    if not snapshot.empty:
        if "Is_Vacant" in snapshot.columns:
            snapshot = snapshot[snapshot["Is_Vacant"] != True].copy()
        mak_col = select_mak_column(snapshot)
        if mak_col == "BsGrd":
            snapshot_mak = _numeric(snapshot[mak_col]) / 100.0
        elif mak_col:
            snapshot_mak = _numeric(snapshot[mak_col])
        else:
            snapshot_mak = pd.Series(0.0, index=snapshot.index)
        active_azubi_mask = (
            (
                snapshot.get("Vertragsart", pd.Series("", index=snapshot.index)).astype(str).str.contains("Auszubild", case=False, na=False)
                | snapshot.get("Ist_Azubi", pd.Series(False, index=snapshot.index)).fillna(False).astype(bool)
            )
            & (snapshot_mak.fillna(0.0) <= 0.000001)
        )
        active_azubis = int(snapshot.loc[active_azubi_mask, "PersNr"].nunique()) if "PersNr" in snapshot.columns else 0
    else:
        active_azubis = 0
    rows.append({"Check": "aktive_azubis_im_zielbestand", "Wert": active_azubis, "Status": "INFO"})

    azubi_audit = audit_tables.get("Azubi_Flow_Audit", pd.DataFrame())
    if not azubi_audit.empty:
        bad_converted = azubi_audit[
            azubi_audit.get("Eventtyp", "").astype(str).str.contains("Azubi_Conversion_In", na=False)
            & azubi_audit.get("final_Vertragsart", "").astype(str).str.contains("Auszubild", case=False, na=False)
        ]
        value = int(len(bad_converted))
    else:
        value = 0
    rows.append({"Check": "uebernommene_azubis_weiterhin_auszubildende", "Wert": value, "Status": "FAIL" if value else "OK"})

    sonstiges_audit = audit_tables.get("Sonstiges_Audit", pd.DataFrame())
    if not sonstiges_audit.empty:
        regular = sonstiges_audit[sonstiges_audit.get("Sonderfall_Kategorie", "").eq("nicht_gemappte_regulaere_beschaeftigte")]
        value = int(regular["PersNr"].nunique()) if "PersNr" in regular.columns else int(len(regular))
    else:
        value = 0
    rows.append({"Check": "unbefristete_regulaere_in_sonstiges", "Wert": value, "Status": "WARN" if value else "OK"})

    age_audit = audit_tables.get("65plus_Audit", pd.DataFrame())
    age_decision = audit_tables.get("65plus_Decision_List", pd.DataFrame())
    if not age_audit.empty:
        age_work = age_decision.copy() if not age_decision.empty else build_65plus_decision_list(age_audit)
        active_65plus_total = int(age_work["PersNr"].nunique()) if "PersNr" in age_work.columns else int(len(age_work))
        active_65plus_mak = float(pd.to_numeric(age_work.get("MAK", 0), errors="coerce").fillna(0).sum())
        active_65plus_eur = float(pd.to_numeric(age_work.get("EUR", 0), errors="coerce").fillna(0).sum())
        has_flag = age_work.get("explicit_extension_flag", pd.Series(False, index=age_work.index)).fillna(False).astype(bool)
        extension_until = age_work.get("extension_until", pd.Series(pd.NA, index=age_work.index))
        documented_reason = (
            age_work.get("Weiterbeschaeftigung_Grund", pd.Series("", index=age_work.index))
            .astype(str)
            .str.strip()
            .ne("")
        )
        has_documented_extension = has_flag | extension_until.notna() | documented_reason
        without_extension = int((~has_documented_extension).sum())
        with_extension = int(has_documented_extension.sum())
        business_review = without_extension
    else:
        active_65plus_total = 0
        active_65plus_mak = 0.0
        active_65plus_eur = 0.0
        with_extension = 0
        without_extension = 0
        business_review = 0
    rows.append({"Check": "aktive_65plus_ohne_erklaerenden_status", "Wert": business_review, "Status": "WARN" if business_review else "OK"})
    rows.append({"Check": "active_65plus_total", "Wert": active_65plus_total, "Status": "WARN" if active_65plus_total else "OK"})
    rows.append({"Check": "active_65plus_mak", "Wert": active_65plus_mak, "Status": "INFO"})
    rows.append({"Check": "active_65plus_eur", "Wert": active_65plus_eur, "Status": "INFO"})
    rows.append({"Check": "active_65plus_with_extension_flag", "Wert": with_extension, "Status": "INFO"})
    rows.append({"Check": "active_65plus_without_extension_flag", "Wert": without_extension, "Status": "WARN" if without_extension else "OK"})
    rows.append({"Check": "active_65plus_business_review_required", "Wert": business_review, "Status": "WARN" if business_review else "OK"})

    demographics = audit_tables.get("Jobfamily_Demografie", pd.DataFrame())
    if summary_df is not None and not summary_df.empty and not demographics.empty:
        head_col = "Köpfe" if "Köpfe" in summary_df.columns else "Koepfe"
        metric_cols = [col for col in (head_col, "MAK", "EUR") if col in summary_df.columns and col in demographics.columns]
        max_diff = 0.0
        if {"Jobfamily", "Dimension"}.issubset(demographics.columns) and metric_cols:
            summary_index = summary_df.set_index("Jobfamily")
            for _, detail_group in demographics.groupby("Dimension", dropna=False, observed=True):
                detail_sums = detail_group.groupby("Jobfamily", dropna=False, observed=True)[metric_cols].sum()
                joined = detail_sums.join(summary_index[metric_cols], lsuffix="_detail", rsuffix="_summary", how="inner")
                for metric in metric_cols:
                    diff = (
                        pd.to_numeric(joined[f"{metric}_detail"], errors="coerce").fillna(0.0)
                        - pd.to_numeric(joined[f"{metric}_summary"], errors="coerce").fillna(0.0)
                    ).abs()
                    if not diff.empty:
                        max_diff = max(max_diff, float(diff.max()))
        rows.append({
            "Check": "detailtabellen_gegen_summary",
            "Wert": max_diff,
            "Status": "WARN" if max_diff > 0.01 else "OK",
        })
    else:
        rows.append({"Check": "detailtabellen_gegen_summary", "Wert": "keine_detailtabelle_uebergeben", "Status": "INFO"})

    return pd.DataFrame(rows)
