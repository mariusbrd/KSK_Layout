"""Person-level MAK allocation for reporting views.

The raw snapshot is plan-position based. Person attributes from Mitarbeiter.xlsx
can appear on multiple Planstellen rows after the join. This helper preserves
the technical row-level MAK and adds reporting columns that allocate one
person's capacity across their active Planstellen rows.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


VOLLZEIT_REFERENZ = 39.0


def _numeric(value: Any, index: pd.Index) -> pd.Series:
    if isinstance(value, pd.Series):
        return pd.to_numeric(value, errors="coerce")
    return pd.Series(value, index=index).pipe(pd.to_numeric, errors="coerce")


def _technical_mak_column(df: pd.DataFrame) -> str | None:
    for col in ("MAK_Calculated", "mak", "MAK", "FTE_person", "BsGrd"):
        if col in df.columns:
            return col
    return None


def _planstellen_soll_mak(df: pd.DataFrame) -> pd.Series:
    if "Soll_FTE" in df.columns:
        return _numeric(df["Soll_FTE"], df.index).fillna(0.0).clip(lower=0.0)
    if "Sollarbeitszeit" in df.columns:
        return (_numeric(df["Sollarbeitszeit"], df.index).fillna(0.0) / VOLLZEIT_REFERENZ).clip(lower=0.0)
    return pd.Series(0.0, index=df.index)


def _person_mak(
    df: pd.DataFrame,
    person_mak_source_col: str,
    source_bsgrd_col: str,
    snapshot_bsgrd_col: str,
    technical: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    source = pd.Series("fallback_mak_column", index=df.index, dtype=object)
    if person_mak_source_col in df.columns:
        source_person_mak = _numeric(df[person_mak_source_col], df.index)
    else:
        source_person_mak = pd.Series(float("nan"), index=df.index, dtype="float64")

    if source_bsgrd_col in df.columns:
        source_bsgrd_mak = _numeric(df[source_bsgrd_col], df.index) / 100.0
    else:
        source_bsgrd_mak = pd.Series(float("nan"), index=df.index, dtype="float64")

    if snapshot_bsgrd_col in df.columns:
        snapshot_bsgrd_mak = _numeric(df[snapshot_bsgrd_col], df.index) / 100.0
    else:
        snapshot_bsgrd_mak = pd.Series(float("nan"), index=df.index, dtype="float64")

    if "Personen_MAK" in df.columns:
        existing_person_mak = _numeric(df["Personen_MAK"], df.index)
    else:
        existing_person_mak = pd.Series(float("nan"), index=df.index, dtype="float64")

    source_available = source_person_mak.notna()
    source_bsgrd_available = (~source_available) & source_bsgrd_mak.notna()
    existing_available = (~source_available) & (~source_bsgrd_available) & existing_person_mak.notna()
    snapshot_bsgrd_available = (
        (~source_available) & (~source_bsgrd_available) & (~existing_available) & snapshot_bsgrd_mak.notna()
    )

    person_mak = source_person_mak.copy()
    person_mak = person_mak.where(source_available, source_bsgrd_mak)
    person_mak = person_mak.where(source_available | source_bsgrd_available, existing_person_mak)
    person_mak = person_mak.where(source_available | source_bsgrd_available | existing_available, snapshot_bsgrd_mak)
    person_mak = person_mak.fillna(technical.fillna(0.0)).clip(lower=0.0)
    source.loc[source_available] = person_mak_source_col
    source.loc[source_bsgrd_available] = source_bsgrd_col
    source.loc[existing_available] = "existing_Personen_MAK"
    source.loc[snapshot_bsgrd_available] = snapshot_bsgrd_col
    fallback_used = ~(source_available | source_bsgrd_available | existing_available | snapshot_bsgrd_available)
    if "Is_Vacant" in df.columns:
        person_mak = person_mak.mask(df["Is_Vacant"] == True, 0.0)
    if "ist_atz_fr" in df.columns:
        person_mak = person_mak.mask(df["ist_atz_fr"] == True, 0.0)
    if "Status kundenindividuell" in df.columns:
        status = df["Status kundenindividuell"].astype(str).str.strip().str.lower()
        person_mak = person_mak.mask(status.eq("ruhendes beschäftigungsverhältnis"), 0.0)
    if "Ist_Azubi" in df.columns:
        person_mak = person_mak.mask(df["Ist_Azubi"].fillna(False).astype(bool), 0.0)
    if "Vertragsart" in df.columns:
        vertragsart = df["Vertragsart"].astype(str).str.strip().str.lower()
        person_mak = person_mak.mask(vertragsart.eq("auszubildende"), 0.0)
    if "MitarbGruppenbez." in df.columns:
        gruppe = df["MitarbGruppenbez."].astype(str).str.strip().str.lower()
        person_mak = person_mak.mask(gruppe.eq("auszubildende"), 0.0)
    return person_mak, fallback_used, source


def apply_person_mak_allocation(
    df: pd.DataFrame,
    person_id_col: str = "PersNr",
    person_mak_source_col: str = "Personen_MAK_Source",
    source_bsgrd_col: str = "BsGrd_Source",
    snapshot_bsgrd_col: str = "BsGrd",
    cost_col: str = "Total_Cost_Year",
    mode: str = "reporting",
) -> pd.DataFrame:
    """Add person-capped MAK reporting columns without overwriting raw MAK.

    ``mode`` is reserved for future modes. The current production behavior is
    the conservative reporting allocation without automatic exceptions.
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    out = df.copy()
    if person_id_col not in out.columns:
        out["MAK_Technical_Uncorrected"] = 0.0
        out["Personen_MAK"] = 0.0
        out["Planstellen_Soll_MAK"] = 0.0
        out["Allocation_Weight"] = 0.0
        out["MAK_Reporting"] = 0.0
        out["EUR_Reporting"] = _numeric(out[cost_col], out.index).fillna(0.0) if cost_col in out.columns else 0.0
        out["MAK_Adjustment_Delta"] = 0.0
        out["MAK_Allocation_Flag"] = "validation_error"
        out["MAK_Allocation_Comment"] = f"missing person id column: {person_id_col}"
        return out

    technical_col = _technical_mak_column(out)
    if technical_col == "BsGrd":
        technical = (_numeric(out[technical_col], out.index).fillna(0.0) / 100.0).clip(lower=0.0)
    elif technical_col:
        technical = _numeric(out[technical_col], out.index).fillna(0.0).clip(lower=0.0)
    else:
        technical = pd.Series(0.0, index=out.index)

    out["MAK_Technical_Uncorrected"] = technical
    person_mak_row, fallback_used, person_mak_source = _person_mak(
        out,
        person_mak_source_col,
        source_bsgrd_col,
        snapshot_bsgrd_col,
        technical,
    )
    out["Personen_MAK"] = person_mak_row
    out["person_mak_source"] = person_mak_source
    out["Planstellen_Soll_MAK"] = _planstellen_soll_mak(out)

    active = pd.Series(True, index=out.index)
    if "Is_Vacant" in out.columns:
        active &= out["Is_Vacant"] != True
    active &= out[person_id_col].notna()
    active &= out[person_id_col].astype(str).str.strip().ne("")

    out["_active_allocation_row"] = active
    group = out.loc[active].groupby(person_id_col, dropna=False)
    row_count = group[person_id_col].transform("size")
    soll_sum = group["Planstellen_Soll_MAK"].transform("sum")
    person_mak = group["Personen_MAK"].transform("max")
    technical_sum = group["MAK_Technical_Uncorrected"].transform("sum")

    out["Anzahl_Planstellen"] = 0
    out.loc[active, "Anzahl_Planstellen"] = row_count.astype(int)

    out["Allocation_Weight"] = 0.0
    by_soll = active.copy()
    by_soll.loc[active] = soll_sum.gt(0.0).values
    out.loc[by_soll, "Allocation_Weight"] = (
        out.loc[by_soll, "Planstellen_Soll_MAK"] / soll_sum.loc[by_soll]
    )
    equal = active & ~by_soll
    out.loc[equal, "Allocation_Weight"] = 1.0 / row_count.loc[equal]

    out["MAK_Reporting"] = 0.0
    out.loc[active, "MAK_Reporting"] = person_mak.loc[active] * out.loc[active, "Allocation_Weight"]

    # Multiple active Planstellen rows normally mean one person's BsGrd was
    # duplicated onto every row by the Mitarbeiter/Planstellen join (the person
    # capping above prevents double-counting that duplication). But when the
    # combined raw row-level MAK stays within a plausible full-time bound
    # (<=100%), the rows more likely represent two genuinely distinct partial
    # engagements that the single person-level BsGrd/Personen_MAK value
    # understates. In that case trust each row's own technical MAK instead of
    # capping to Personen_MAK.
    realistic_row_level = active.copy()
    realistic_row_level.loc[active] = (
        row_count.gt(1)
        & technical_sum.le(1.0 + 1e-6)
        & technical_sum.gt(person_mak + 1e-6)
    ).values
    technical_sum_full = technical_sum.reindex(out.index)
    out.loc[realistic_row_level, "Allocation_Weight"] = (
        out.loc[realistic_row_level, "MAK_Technical_Uncorrected"]
        / technical_sum_full.loc[realistic_row_level]
    )
    out.loc[realistic_row_level, "MAK_Reporting"] = out.loc[realistic_row_level, "MAK_Technical_Uncorrected"]

    if cost_col in out.columns:
        out["EUR_Reporting"] = _numeric(out[cost_col], out.index).fillna(0.0) * out["Allocation_Weight"]
        out["EUR_Allocation_Comment"] = "Total_Cost_Year treated as person-level cost and allocated by Allocation_Weight"
    else:
        out["EUR_Reporting"] = 0.0
        out["EUR_Allocation_Comment"] = f"missing cost column: {cost_col}"

    out["MAK_Adjustment_Delta"] = out["MAK_Technical_Uncorrected"] - out["MAK_Reporting"]

    out["MAK_Allocation_Flag"] = "validation_error"
    out.loc[active & row_count.eq(1).reindex(out.index, fill_value=False), "MAK_Allocation_Flag"] = "single_position"
    out.loc[by_soll & row_count.gt(1).reindex(out.index, fill_value=False), "MAK_Allocation_Flag"] = "multi_position_allocated_by_planstellen_soll"
    out.loc[equal & row_count.gt(1).reindex(out.index, fill_value=False), "MAK_Allocation_Flag"] = "multi_position_allocated_equal_weight"
    out.loc[fallback_used, "MAK_Allocation_Flag"] = "missing_person_mak_fallback_used"

    allow = out.get("Allow_MAK_gt_1", pd.Series(False, index=out.index)).fillna(False).astype(bool)
    approval = out.get("Business_Approval_Status", pd.Series("", index=out.index)).astype(str).str.lower().eq("approved")
    approved_exception = allow & approval
    person_gt_one = person_mak.gt(1.0 + 1e-6).reindex(out.index, fill_value=False)
    technical_gt_person = technical_sum.gt(person_mak + 1e-6).reindex(out.index, fill_value=False)
    exception_required = active & (technical_gt_person | person_gt_one) & ~approved_exception
    out.loc[exception_required, "MAK_Allocation_Flag"] = "exception_required_mak_gt_1"
    out.loc[realistic_row_level, "MAK_Allocation_Flag"] = "multi_position_row_level_realistic_total"

    out["MAK_Allocation_Comment"] = "person MAK allocated for reporting"
    out.loc[out["MAK_Allocation_Flag"].eq("single_position"), "MAK_Allocation_Comment"] = "single active Planstellen row; reporting MAK equals person MAK"
    out.loc[out["MAK_Allocation_Flag"].eq("multi_position_allocated_by_planstellen_soll"), "MAK_Allocation_Comment"] = "person MAK distributed by Planstellen_Soll_MAK"
    out.loc[out["MAK_Allocation_Flag"].eq("multi_position_allocated_equal_weight"), "MAK_Allocation_Comment"] = "person MAK distributed equally because no usable Planstellen_Soll_MAK exists"
    out.loc[out["MAK_Allocation_Flag"].eq("missing_person_mak_fallback_used"), "MAK_Allocation_Comment"] = "Personen_MAK fallback used because BsGrd/Personen_MAK was missing"
    out.loc[out["MAK_Allocation_Flag"].eq("exception_required_mak_gt_1"), "MAK_Allocation_Comment"] = "technical MAK exceeds person MAK; exception approval required for reporting above person capacity"
    out.loc[out["MAK_Allocation_Flag"].eq("multi_position_row_level_realistic_total"), "MAK_Allocation_Comment"] = "multiple active Planstellen rows with plausible combined raw MAK (<=100%); each row's own technical MAK trusted instead of capping to Personen_MAK"
    out.loc[~active, "MAK_Allocation_Comment"] = "inactive or vacant row excluded from person MAK allocation"

    return out.drop(columns=["_active_allocation_row"], errors="ignore")


def build_mak_allocation_audit(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = apply_person_mak_allocation(df) if "MAK_Reporting" not in df.columns else df.copy()
    cols = [
        "PersNr", "Jobfamily", "Planstelle", "Job", "BsGrd", "BsGrd_Source",
        "snapshot_BsGrd", "Personen_MAK_Source", "Personen_MAK",
        "person_mak_source", "bsgrd_lineage_flag", "Planstellen_Soll_MAK", "Allocation_Weight",
        "MAK_Technical_Uncorrected", "MAK_Reporting", "MAK_Adjustment_Delta", "Anzahl_Planstellen",
        "MAK_Allocation_Flag", "MAK_Allocation_Comment",
    ]
    for col in cols:
        if col not in work.columns:
            work[col] = pd.NA
    return work[cols].reset_index(drop=True)


def build_mak_allocation_validation_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = apply_person_mak_allocation(df) if "MAK_Reporting" not in df.columns else df.copy()
    if "Is_Vacant" in work.columns:
        work = work[work["Is_Vacant"] != True].copy()
    if "PersNr" not in work.columns or work.empty:
        return pd.DataFrame()
    grouped = work.groupby("PersNr", dropna=False).agg(
        Personen_MAK=("Personen_MAK", "max"),
        MAK_Technical_Total=("MAK_Technical_Uncorrected", "sum"),
        MAK_Reporting_Total=("MAK_Reporting", "sum"),
        Allocation_Weight_Total=("Allocation_Weight", "sum"),
        Anzahl_Planstellen=("Anzahl_Planstellen", "max"),
    )
    tol = 1e-6
    multi = grouped["Anzahl_Planstellen"].gt(1)
    technical_gt_person = grouped["MAK_Technical_Total"].gt(grouped["Personen_MAK"] + tol)
    reporting_gt_person = grouped["MAK_Reporting_Total"].gt(grouped["Personen_MAK"] + tol)
    weight_bad = grouped["Allocation_Weight_Total"].sub(1.0).abs().gt(tol)
    jobfamily = work.get("Jobfamily", pd.Series("", index=work.index)).astype(str)
    fv = work[jobfamily.isin(["Führung Vertrieb", "FÃ¼hrung Vertrieb"])]
    return pd.DataFrame([
        {"Check": "MAK_Technical_Total", "Wert": float(grouped["MAK_Technical_Total"].sum()), "Status": "INFO"},
        {"Check": "MAK_Reporting_Total", "Wert": float(grouped["MAK_Reporting_Total"].sum()), "Status": "INFO"},
        {"Check": "MAK_Adjustment_Total", "Wert": float((grouped["MAK_Technical_Total"] - grouped["MAK_Reporting_Total"]).sum()), "Status": "INFO"},
        {"Check": "Anzahl_Personen_mit_Mehrfachplanstellen", "Wert": int(multi.sum()), "Status": "INFO"},
        {"Check": "Anzahl_Personen_mit_Technical_MAK_gt_Personen_MAK", "Wert": int(technical_gt_person.sum()), "Status": "WARN" if technical_gt_person.any() else "OK"},
        {"Check": "Anzahl_Personen_mit_Reporting_MAK_gt_Personen_MAK", "Wert": int(reporting_gt_person.sum()), "Status": "FAIL" if reporting_gt_person.any() else "OK"},
        {"Check": "Anzahl_Personen_mit_Allocation_Weight_sum_ne_1", "Wert": int(weight_bad.sum()), "Status": "FAIL" if weight_bad.any() else "OK"},
        {"Check": "Führung_Vertrieb_Technical_MAK", "Wert": float(fv["MAK_Technical_Uncorrected"].sum()) if not fv.empty else 0.0, "Status": "INFO"},
        {"Check": "Führung_Vertrieb_Reporting_MAK", "Wert": float(fv["MAK_Reporting"].sum()) if not fv.empty else 0.0, "Status": "INFO"},
        {"Check": "Führung_Vertrieb_Adjustment", "Wert": float(fv["MAK_Adjustment_Delta"].sum()) if not fv.empty else 0.0, "Status": "INFO"},
    ])
