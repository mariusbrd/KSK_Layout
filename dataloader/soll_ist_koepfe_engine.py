"""
Roh-nahe Fachlogik fuer Kompakt -> Ist vs. Soll -> Koepfe.

Wichtig:
- Arbeitet auf der ungefilterten Planstellen-Excel als fachlicher Basis.
- Laesst Sollarbeitszeit == 0.01 unveraendert.
- Wendet Deep-Dive-Exklusionen fuer diesen Tab in Variante B an:
  ausgeschlossene Personen/OEs fallen samt Planstelle komplett heraus.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

from abgaenge.schemas import normalize_persnr
from config.settings import TARIFF_GROUPS
from dataloader.loader import load_original_data
from kpi_reference import get_current_stichtag
from utils.settings_loader import get_setting

IST_UNBESETZT = "Unbesetzt"
IST_NOT_FOUND = "Nicht gefunden"
_INVALID_VALUES = {"", "NAN", "NONE"}


def _normalize_eg_raw(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().upper().replace(" ", "")
    if text.startswith("BIS"):
        text = text[3:].strip()
    return text


def _normalize_org_unit_series(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
        .replace({"NAN": "", "NONE": "", "<NA>": ""})
    )


def _filter_mitarbeiter_for_stichtag(mitarbeiter: pd.DataFrame) -> pd.DataFrame:
    stichtag = get_current_stichtag()
    include_future = get_setting("include_future_hires", False)
    result = mitarbeiter.copy()

    if "Eintritt" in result.columns:
        result["Eintritt"] = pd.to_datetime(result["Eintritt"], errors="coerce")
        if not include_future:
            result = result[result["Eintritt"].isna() | (result["Eintritt"] <= stichtag)]

    if "Austritt" in result.columns:
        result["Austritt"] = pd.to_datetime(result["Austritt"], errors="coerce")
        result = result[result["Austritt"].isna() | (result["Austritt"] >= stichtag)]

    return result


@st.cache_data(show_spinner=False)
def load_soll_ist_koepfe_basis() -> pd.DataFrame:
    data = load_original_data()

    planstellen = data["planstellen"].copy()
    mitarbeiter = _filter_mitarbeiter_for_stichtag(data["mitarbeiter"])

    # Summen-/Artefaktzeilen entfernen, 0.01 explizit NICHT transformieren.
    if "Kürzel OrgEinheit" in planstellen.columns:
        org = _normalize_org_unit_series(planstellen["Kürzel OrgEinheit"])
        planstellen = planstellen.loc[org.ne("")].copy()
        planstellen["Kürzel OrgEinheit"] = org.loc[planstellen.index]

    if "Personalnummer" in planstellen.columns:
        planstellen["Personalnummer"] = normalize_persnr(planstellen["Personalnummer"])

    merge_cols = ["PersNr", "TrfGr", "MitarbGruppenbez.", "Status kundenindividuell"]
    available_cols = [col for col in merge_cols if col in mitarbeiter.columns]
    merged = planstellen.merge(
        mitarbeiter[available_cols],
        left_on="Personalnummer",
        right_on="PersNr",
        how="left",
        suffixes=("", "_ma"),
    )

    merged["__has_pnr__"] = merged["Personalnummer"].notna()
    merged["Is_Vacant"] = ~merged["__has_pnr__"]

    soll = pd.to_numeric(merged.get("Sollarbeitszeit"), errors="coerce")
    merged["__is_001__"] = soll.eq(0.01)
    merged["__is_regular__"] = ~merged["__is_001__"]
    merged["__is_9xxx__"] = (
        _normalize_org_unit_series(merged["Kürzel OrgEinheit"]).str.fullmatch(r"9\d\d\d").fillna(False)
        | _normalize_org_unit_series(merged["Kürzel OrgEinheit"]).eq("99XX")
    )

    col_h = merged.get("Bewertung Tarifgruppe", pd.Series("", index=merged.index)).map(_normalize_eg_raw)
    col_i = merged.get("Text Gehaltsband", pd.Series("", index=merged.index)).map(_normalize_eg_raw)
    merged["_Soll_EG_H"] = col_h
    merged["_Soll_EG_I"] = col_i.where(~col_i.isin(_INVALID_VALUES), other=col_h)

    def _ist_kat(row: pd.Series) -> str:
        if not row.get("__has_pnr__", False):
            return IST_UNBESETZT
        trfgr = row.get("TrfGr")
        if pd.isna(trfgr) or str(trfgr).strip().lower() in ("", "nan"):
            return IST_NOT_FOUND
        return str(trfgr).strip().upper().replace(" ", "")

    merged["_Ist_EG"] = merged.apply(_ist_kat, axis=1)
    return merged


def _apply_variant_b_exclusions(df: pd.DataFrame, exclusions: Dict[str, Any]) -> pd.DataFrame:
    result = df.copy()

    ex_units = exclusions.get("org_units", []) or []
    if ex_units and "Kürzel OrgEinheit" in result.columns:
        s_ou = _normalize_org_unit_series(result["Kürzel OrgEinheit"])
        explicit = [unit for unit in ex_units if unit != "99XX"]
        mask_excl = s_ou.isin(explicit)
        if "99XX" in ex_units:
            mask_excl = mask_excl | (s_ou.str.startswith("99") & ~s_ou.isin(set(explicit)))
        result = result.loc[~mask_excl].copy()

    person_excl = pd.Series(False, index=result.index)
    if exclusions.get("vorstand") and "MitarbGruppenbez." in result.columns:
        person_excl |= result["MitarbGruppenbez."].astype(str).str.strip() == "Vorstand"
    if exclusions.get("ruhend_bv") and "Status kundenindividuell" in result.columns:
        person_excl |= (
            result["Status kundenindividuell"].astype(str).str.strip()
            == "Ruhendes Beschäftigungsverhältnis"
        )

    if person_excl.any():
        result = result.loc[~person_excl].copy()

    return result


def build_soll_ist_koepfe_result(
    use_max_eg: bool = True,
    exclusions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    work = load_soll_ist_koepfe_basis()
    ex = exclusions if exclusions is not None else get_setting("exclusions", {})
    work = _apply_variant_b_exclusions(work, ex)

    work = work.copy()
    work["_Soll_EG"] = work["_Soll_EG_I"] if use_max_eg else work["_Soll_EG_H"]

    regular_df = work.loc[work["__is_regular__"]].copy()
    technical_df = work.loc[work["__is_001__"]].copy()

    no_soll_mask = regular_df["_Soll_EG"].isin(_INVALID_VALUES)
    no_soll_df = regular_df.loc[no_soll_mask].copy()
    matrix_df = regular_df.loc[~no_soll_mask].copy()

    no_soll_occupied = no_soll_df.loc[
        ~no_soll_df["_Ist_EG"].isin([IST_UNBESETZT, IST_NOT_FOUND])
    ]
    no_soll_eg_row = no_soll_occupied["_Ist_EG"].value_counts()

    pivot = matrix_df.groupby(["_Soll_EG", "_Ist_EG"]).size().unstack(fill_value=0)

    eg_order = {group: idx for idx, group in enumerate(TARIFF_GROUPS)}
    ist_eg_cols = sorted(
        [col for col in pivot.columns if col not in (IST_UNBESETZT, IST_NOT_FOUND)],
        key=lambda group: eg_order.get(group, 999),
    )
    special_cols = [col for col in (IST_UNBESETZT, IST_NOT_FOUND) if col in pivot.columns]
    if not pivot.empty:
        pivot = pivot[ist_eg_cols + special_cols]
        pivot["Gesamt"] = pivot.sum(axis=1)
        soll_order = sorted(pivot.index.tolist(), key=lambda group: eg_order.get(group, 999))
        pivot = pivot.loc[soll_order]
    else:
        pivot["Gesamt"] = pd.Series(dtype=int)
        soll_order = []

    pivot.index.name = "Soll-EG"

    summary = {
        "rows_total_after_exclusions": int(len(work)),
        "regular_total": int(len(regular_df)),
        "regular_occupied": int(regular_df["__has_pnr__"].sum()),
        "regular_vacant": int((~regular_df["__has_pnr__"]).sum()),
        "regular_no_soll_eg_total": int(len(no_soll_df)),
        "regular_no_soll_eg_occupied": int(no_soll_occupied.shape[0]),
        "matrix_total": int(len(matrix_df)),
        "matrix_occupied": int((~matrix_df["_Ist_EG"].isin([IST_UNBESETZT, IST_NOT_FOUND])).sum()),
        "matrix_unbesetzt": int((matrix_df["_Ist_EG"] == IST_UNBESETZT).sum()),
        "matrix_not_found": int((matrix_df["_Ist_EG"] == IST_NOT_FOUND).sum()),
        "technical_total": int(len(technical_df)),
        "technical_9xxx_total": int((technical_df["__is_9xxx__"]).sum()),
        "technical_9xxx_occupied": int((technical_df["__is_9xxx__"] & technical_df["__has_pnr__"]).sum()),
        "technical_non9xxx_total": int((~technical_df["__is_9xxx__"]).sum()),
        "technical_non9xxx_occupied": int((~technical_df["__is_9xxx__"] & technical_df["__has_pnr__"]).sum()),
        "technical_9910_total": int(
            (_normalize_org_unit_series(technical_df["Kürzel OrgEinheit"]) == "9910").sum()
        ) if not technical_df.empty else 0,
    }

    return {
        "pivot": pivot,
        "soll_order": soll_order,
        "ist_eg_cols": ist_eg_cols,
        "IST_UNBESETZT": IST_UNBESETZT,
        "IST_NOT_FOUND": IST_NOT_FOUND,
        "work_df": matrix_df,
        "summary": summary,
        "n_no_soll_eg": int(no_soll_mask.sum()),
        "no_soll_eg_row": no_soll_eg_row,
        "regular_no_soll_df": no_soll_df,
        "technical_df": technical_df,
    }
