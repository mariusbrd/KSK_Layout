"""
Planstellen-Auswertung nach Job Family.

Repliziert die manuelle Auswertung "Auswertung Soll-Kapazität" aus
Cluster-Daten/OE_Cluster_Update_raw_backup_20260511_193851.xlsx.

Mapping-Quelle: Cluster-Daten/OE_Cluster_Update_raw_backup_20260511_193851.xlsx
  Sheet "Planstellen", Spalte "Jobfamily Jobgruppe" (positional join auf Planstellen.XLSX)

Stichtag:     get_current_stichtag() aus kpi_reference.py
              (nur für Logging; Besetzt-Flag basiert direkt auf Personalnummer in
              Planstellen.XLSX, kein zeitlicher Mitarbeiter-Filter nötig)

Sollarbeitszeit: RAW aus Planstellen.XLSX ohne Azubi-Korrektur (entspricht Referenz).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd

# ─────────────────────────────────────────────
# Pfade relativ zu KSK_Layout/
# ─────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent  # …/Version 2 - Focus/KSK_Layout
_ORIGINAL_DATA_DIR = _BASE_DIR.parent / "Original-Daten"
_CLUSTER_DATA_DIR = _BASE_DIR.parent / "Cluster-Daten"

PLANSTELLEN_FILE = _ORIGINAL_DATA_DIR / "Planstellen.XLSX"
BACKUP_CLUSTER_FILE = (
    _CLUSTER_DATA_DIR
    / "OE_Cluster_Update_raw_backup_20260511_193851.xlsx"
)
CURRENT_CLUSTER_FILE = _CLUSTER_DATA_DIR / "OE_Cluster_Update.xlsx"

JF_FALLBACK = "OHNE JOBFAMILY / NICHT ZUGEORDNET"

# Name-Mapping: aktuelle OE_Cluster_Update.xlsx → Referenz-Namen
# (nur für Fallback, falls Backup nicht vorhanden)
_CURRENT_CLUSTER_NAME_MAP: dict[str, str] = {
    "Sonstiges": JF_FALLBACK,
    "Standardisierte Beratung Vertrieb": "Qualitative Beratung",
    "Qualifizierte Sachbearbeitung": "Qualitative Tätigkeit Betrieb",
    "Komplexe Beratung Vertrieb": "Komplexe Beratung",
    "Komplexe Tätigkeiten Betrieb": "Komplexe Tätigkeit Betrieb",
}


# ─────────────────────────────────────────────
# Hilfsfunktion: normalize_persnr
# Kopiert aus abgaenge/schemas.py – kein Import nötig, da standalone-Modul
# ─────────────────────────────────────────────
def _normalize_persnr(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
        .where(series.notna(), pd.NA)
    )


def _is_besetzt(persnr_norm: pd.Series) -> pd.Series:
    """True wenn Personalnummer belegt (nicht leer, nicht '000000')."""
    invalid = persnr_norm.astype(str).str.strip().isin(
        {"000000", "", "nan", "<NA>", "pd.na"}
    )
    return persnr_norm.notna() & ~invalid


# ─────────────────────────────────────────────
# Mapping laden
# ─────────────────────────────────────────────

def _load_jf_mapping_from_backup(planstellen_len: int) -> Optional[pd.Series]:
    """
    Lädt die Jobfamily-Zuweisung aus dem Backup-Sheet 'Planstellen'
    (Spalte 'Jobfamily Jobgruppe', positional join).
    Gibt None zurück falls Backup nicht vorhanden oder Zeilenzahl abweicht.
    """
    if not BACKUP_CLUSTER_FILE.exists():
        return None
    df_bk = pd.read_excel(BACKUP_CLUSTER_FILE, sheet_name="Planstellen")
    if len(df_bk) != planstellen_len:
        return None
    return df_bk["Jobfamily Jobgruppe"]


def _load_jf_mapping_from_current_cluster(
    df_pl: pd.DataFrame,
) -> pd.Series:
    """
    Fallback: Mapping via OE_Cluster_Update.xlsx (JobFamilies-Sheet).
    Positional join auf (Organisationseinheit, Planstelle).
    Wendet _CURRENT_CLUSTER_NAME_MAP an.
    """
    if not CURRENT_CLUSTER_FILE.exists():
        return pd.Series(JF_FALLBACK, index=df_pl.index)

    df_jf = pd.read_excel(CURRENT_CLUSTER_FILE, sheet_name="JobFamilies")
    df_pl_r = df_pl.reset_index(drop=True)
    df_jf_r = df_jf.reset_index(drop=True)

    if len(df_pl_r) == len(df_jf_r):
        jf_raw = df_jf_r["Jobfamily Cluster"]
    else:
        # Key-basierter Fallback
        jf_r = df_jf.copy()
        jf_r = jf_r.drop_duplicates(
            subset=["Organisationseinheit", "Planstelle"], keep="first"
        )
        merged = df_pl_r.merge(
            jf_r[["Organisationseinheit", "Planstelle", "Jobfamily Cluster"]],
            on=["Organisationseinheit", "Planstelle"],
            how="left",
        )
        jf_raw = merged["Jobfamily Cluster"]

    jf_mapped = jf_raw.map(
        lambda v: _CURRENT_CLUSTER_NAME_MAP.get(str(v).strip(), str(v).strip())
        if pd.notna(v) else JF_FALLBACK
    )
    return jf_mapped.fillna(JF_FALLBACK)


# ─────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────

def compute_planstellen_jobfamily_auswertung(
    planstellen_path: Optional[str | Path] = None,
    stichtag: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """
    Gibt eine Auswertung der Planstellen nach Job Family zurück.

    Spalten:
        Job Family, Anzahl Planstellen, Summe Sollarbeitszeit,
        Besetzte Planstellen, Besetzte Sollarbeitszeit,
        Unbesetzte Planstellen, Unbesetzte Sollarbeitszeit

    Parameters
    ----------
    planstellen_path:
        Pfad zu Planstellen.XLSX. Standardmäßig Original-Daten/Planstellen.XLSX.
    stichtag:
        Wird nur für Logging verwendet; hat keinen Einfluss auf das Ergebnis,
        da Besetzt-Flag direkt aus Personalnummer in Planstellen.XLSX stammt.

    Returns
    -------
    pd.DataFrame – eine Zeile pro Job Family, sortiert nach Anzahl (OHNE zuerst).
    """
    # Stichtag laden
    if stichtag is None:
        try:
            from kpi_reference import get_current_stichtag
            stichtag = get_current_stichtag()
        except Exception:
            stichtag = pd.Timestamp("2025-12-31")

    # Planstellen laden – ALLE Zeilen inkl. Summenzeile (wie Referenz)
    pl_path = Path(planstellen_path) if planstellen_path else PLANSTELLEN_FILE
    df_pl = pd.read_excel(pl_path)

    # Personalnummer normalisieren
    df_pl["_pnr"] = _normalize_persnr(df_pl["Personalnummer"])
    df_pl["_besetzt"] = _is_besetzt(df_pl["_pnr"])

    # Sollarbeitszeit: RAW (ohne Azubi-Korrektur, weil Referenz dies so nutzt)
    df_pl["_soll"] = pd.to_numeric(df_pl["Sollarbeitszeit"], errors="coerce").fillna(0)

    # Job Family Mapping laden
    jf_series = _load_jf_mapping_from_backup(len(df_pl))
    if jf_series is not None:
        df_pl["_jf"] = jf_series.values
        df_pl["_jf"] = df_pl["_jf"].fillna(JF_FALLBACK)
    else:
        df_pl["_jf"] = _load_jf_mapping_from_current_cluster(df_pl)

    # Aggregation
    rows = []
    for jf, grp in df_pl.groupby("_jf", observed=True, dropna=False):
        jf_label = str(jf) if not pd.isna(jf) else JF_FALLBACK
        bes = grp["_besetzt"]
        rows.append(
            {
                "Job Family": jf_label,
                "Anzahl Planstellen": len(grp),
                "Summe Sollarbeitszeit": grp["_soll"].sum(),
                "Besetzte Planstellen": int(bes.sum()),
                "Besetzte Sollarbeitszeit": grp.loc[bes, "_soll"].sum(),
                "Unbesetzte Planstellen": int((~bes).sum()),
                "Unbesetzte Sollarbeitszeit": grp.loc[~bes, "_soll"].sum(),
            }
        )

    result = pd.DataFrame(rows)

    # Sortierung: OHNE JOBFAMILY zuerst, dann absteigend nach Anzahl
    result["_sort_key"] = result["Anzahl Planstellen"].where(
        result["Job Family"] != JF_FALLBACK, other=10**9
    )
    result = (
        result.sort_values("_sort_key", ascending=False)
        .drop(columns=["_sort_key"])
        .reset_index(drop=True)
    )

    return result


def validate_planstellen_auswertung(result: pd.DataFrame) -> dict:
    """
    Prüft die erzeugte Tabelle auf Konsistenz und gibt ein Dict mit
    True/False-Checks zurück.
    """
    checks = {}
    # V1: Besetzt + Unbesetzt = Anzahl (alle Zeilen)
    row_sum = result["Besetzte Planstellen"] + result["Unbesetzte Planstellen"]
    checks["v1_besetzt_plus_unbesetzt_eq_anzahl"] = bool(
        (row_sum == result["Anzahl Planstellen"]).all()
    )
    # V2: Besetzt_Soll + Unbesetzt_Soll ≈ Summe_Soll (alle Zeilen, Toleranz 0.05h)
    soll_sum = result["Besetzte Sollarbeitszeit"] + result["Unbesetzte Sollarbeitszeit"]
    diff = (soll_sum - result["Summe Sollarbeitszeit"]).abs()
    checks["v2_soll_sums_consistent"] = bool((diff < 0.05).all())
    # V3: Gesamtsummen
    checks["v3_gesamt_anzahl"] = int(result["Anzahl Planstellen"].sum())
    checks["v3_gesamt_soll"] = float(result["Summe Sollarbeitszeit"].sum())
    checks["v3_gesamt_besetzt"] = int(result["Besetzte Planstellen"].sum())
    checks["v3_gesamt_unbesetzt"] = int(result["Unbesetzte Planstellen"].sum())
    return checks
