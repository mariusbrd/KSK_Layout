"""
Exclusion Groups Helper – utils/exclusion_groups.py

Baut Gruppen-Masken für alle exkludierbaren Personengruppen auf und
aggregiert KPIs pro Gruppe. Arbeitet ausschließlich auf Spalten, die
apply_exclusions() nicht nullt/zeroed:
  - Kürzel OrgEinheit  (OE-Code, immer erhalten)
  - MitarbGruppenbez.  (Mitarbeitergruppe, immer erhalten)
  - Status kundenindividuell (immer erhalten)
  - Sollarbeitszeit / Soll_FTE (Soll-Seite, immer erhalten)
  - Is_Vacant          (bool, nach Exklusion True)

Kapazität wird über Soll_FTE = Sollarbeitszeit/39 gemessen – das ist
die Planstellen-Nachfrage und bleibt unabhängig vom Exklusions-Status.
"""

from __future__ import annotations

import pandas as pd
from typing import Dict, List


# ---------------------------------------------------------------------------
# Gruppe-Definitionen: (key, label, beschreibung)
# Diese Liste spiegelt 1:1 die Exklusions-Optionen in Einstellungen wider.
# ---------------------------------------------------------------------------

VORSTAND_KEY = "vorstand"
RUHEND_KEY = "ruhend_bv"
AUSBILDUNG_NACHWUCHS_KEY = "ausbildung_nachwuchs"
JOBFAMILY_VALIDATION_SPECIAL_KEY = "jobfamily_validation_special_positions"
SOLLARBEITSZEIT_001_KEY = "sollarbeitszeit_001_positions"

# Alle 99XX-Gruppen als separate Einträge
PA_GROUPS: List[tuple[str, str]] = [
    ("9900", "PA Ruhendes Beschäftigungsverhältnis"),
    ("9910", "PA Auszubildende"),
    ("9920", "PE bereichsübergreifend / Trainee-Planstellen"),
    ("9921", "PA Praktikum"),
    ("9940", "PA Dauerkranke"),
    ("9941", "PA Rente auf Zeit"),
    ("9945", "PA Beurlaubt Pflegezeit"),
    ("9960", "PA Bundeswehr/Zivildienst"),
    ("9970", "PA Mutterschutz"),
    ("9971", "PA Elternzeit"),
    ("9972", "PA Sonderurlaub § 28 TVöD"),
    ("9973", "PA Beschäftigungsverbot"),
    ("9975", "PA Erziehungszeit"),
    ("9980", "PA Rückkehrer"),
    ("9981", "Aushilfen"),
    ("9990", "PA Freistellung (ATZ-FR, Beurlaubung, Turbo-TZ)"),
    ("9999", "Personalrat"),
    ("99XX", "Sonstige 99XX (Dummy/Versorgungsbezüge)"),
]

JOBFAMILY_VALIDATION_SPECIAL_PLAN_IDS = {
    "50001903", "50002084", "50002093", "50002126", "50002400",
    "50010382", "50010385", "50010394", "50010613", "50010614",
    "50010749", "50011753", "50014256", "50014311", "50016270",
    "50028579", "50028681", "50028683", "50028689", "50028692",
    "50029046", "50029163", "50029197", "50029199", "50029201",
    "50029202", "50029239", "50029240", "50029245", "50029246",
    "50029247", "50029856", "50029895", "50030165", "50030360",
    "50030437", "50031295", "50034246", "50035886", "50039021",
    "50040886", "50041219", "50041859", "50041920", "50041956",
    "50046321", "50046421", "50046547", "50046548", "50046549",
    "50046551", "50046592", "50046593", "50046878", "50048341",
    "50048342", "50048764", "50048766", "50048809", "50048965",
    "50049158", "50049545", "50049595", "50049701", "50049901",
    "50049902", "50050763", "50050776", "50051622", "50054377",
    "50054682", "50055993", "50056337", "50056772", "50062368",
    "50062459", "50063611", "50064630", "50065990", "50066581",
    "50066920", "50066921",
}

SPECIAL_GROUPS: List[tuple[str, str]] = [
    (AUSBILDUNG_NACHWUCHS_KEY, "Ausbildung / Nachwuchs"),
    (JOBFAMILY_VALIDATION_SPECIAL_KEY, "Jobfamily-Validierung: Sonderplanstellen"),
    (SOLLARBEITSZEIT_001_KEY, "Planstellen mit Sollarbeitszeit = 0,01"),
]


def _normalize_oe(series: pd.Series) -> pd.Series:
    """Normalisiert OE-Kürzel: strip, .0-Suffix entfernen (z. B. '9900.0' → '9900')."""
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _normalize_code(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _soll_mak(df: pd.DataFrame) -> pd.Series:
    """
    Gibt Soll-FTE je Zeile zurück.
    Priorität: vorberechnete Soll_FTE-Spalte > Sollarbeitszeit/39.
    """
    if "Soll_FTE" in df.columns:
        return df["Soll_FTE"].fillna(0.0)
    if "Sollarbeitszeit" in df.columns:
        return (df["Sollarbeitszeit"].fillna(0.0) / 39.0)
    return pd.Series(0.0, index=df.index)


def resolve_mak_series(df: pd.DataFrame) -> pd.Series:
    """
    Gibt IST-MAK je Zeile zurück (zentrale Hilfsfunktion, systemweit genutzt).
    Spalten-Priorität: MAK_Calculated → mak → MAK → 0.0
    """
    for col in ("MAK_Calculated", "mak", "MAK"):
        if col in df.columns:
            return df[col].fillna(0.0)
    return pd.Series(0.0, index=df.index)


# Interner Alias für Rückwärtskompatibilität innerhalb dieses Moduls
_ist_mak = resolve_mak_series


# ---------------------------------------------------------------------------
# Masken-Builder
# ---------------------------------------------------------------------------

def build_group_masks(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """
    Gibt ein Dict {group_key: bool-Maske} zurück.
    Jede Maske identifiziert Zeilen die zur Gruppe gehören –
    unabhängig davon ob sie bereits exkludiert sind.
    """
    masks: Dict[str, pd.Series] = {}
    false_mask = pd.Series(False, index=df.index)

    # 1. Vorstand
    if "MitarbGruppenbez." in df.columns:
        masks[VORSTAND_KEY] = df["MitarbGruppenbez."].astype(str).str.strip() == "Vorstand"
    else:
        masks[VORSTAND_KEY] = false_mask.copy()

    # 2. Ruhendes BV (via Status-Feld, nicht OE-Code)
    if "Status kundenindividuell" in df.columns:
        masks[RUHEND_KEY] = (
            df["Status kundenindividuell"].astype(str).str.strip()
            == "Ruhendes Beschäftigungsverhältnis"
        )
    else:
        masks[RUHEND_KEY] = false_mask.copy()

    # 3. PA-Bereiche via OE-Kürzel
    if "Kürzel OrgEinheit" in df.columns:
        s_ou = _normalize_oe(df["Kürzel OrgEinheit"])
        for code, _label in PA_GROUPS:
            if code == "99XX":
                # Alle 99XX die nicht explizit gelistet sind
                explicit_codes = {c for c, _ in PA_GROUPS if c != "99XX"}
                masks[code] = s_ou.str.startswith("99") & ~s_ou.isin(explicit_codes)
            else:
                masks[code] = s_ou == code
    else:
        for code, _ in PA_GROUPS:
            masks[code] = false_mask.copy()

    ausbildung_mask = false_mask.copy()
    if "MitarbGruppenbez." in df.columns:
        ausbildung_mask = ausbildung_mask | (
            df["MitarbGruppenbez."].astype(str).str.strip() == "Auszubildende"
        )
    if "Vertragsart" in df.columns:
        ausbildung_mask = ausbildung_mask | (
            df["Vertragsart"].astype(str).str.strip() == "Ausbildung"
        )
    if "TrfGr" in df.columns:
        ausbildung_mask = ausbildung_mask | (
            df["TrfGr"].astype(str).str.contains("TVA", case=False, na=False)
        )
    if "Organisationseinheit" in df.columns:
        ausbildung_mask = ausbildung_mask | (
            df["Organisationseinheit"].astype(str).str.contains(
                "Bankkaufleute|Finanzassistenten|Duale Studenten|Auszubildende|Büromanagement|Versicherungen",
                case=False,
                na=False,
            )
        )
    masks[AUSBILDUNG_NACHWUCHS_KEY] = ausbildung_mask

    if "Planstellennr" in df.columns:
        masks[JOBFAMILY_VALIDATION_SPECIAL_KEY] = (
            _normalize_code(df["Planstellennr"]).isin(JOBFAMILY_VALIDATION_SPECIAL_PLAN_IDS)
        )
    else:
        masks[JOBFAMILY_VALIDATION_SPECIAL_KEY] = false_mask.copy()

    if "Sollarbeitszeit" in df.columns:
        sollarbeitszeit = pd.to_numeric(df["Sollarbeitszeit"], errors="coerce")
        masks[SOLLARBEITSZEIT_001_KEY] = sollarbeitszeit.sub(0.01).abs().le(1e-9)
    else:
        masks[SOLLARBEITSZEIT_001_KEY] = false_mask.copy()

    return masks


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _top_values(series: pd.Series, n: int = 5) -> str:
    """Gibt Top-N Werte als komma-getrennten String zurück."""
    counts = series.dropna().astype(str).value_counts().head(n)
    return ", ".join(f"{v} ({c})" for v, c in counts.items()) if len(counts) > 0 else "—"


def aggregate_group(
    df: pd.DataFrame,
    mask: pd.Series,
    group_key: str,
    group_label: str,
) -> dict:
    """
    Aggregiert KPIs für eine Gruppe.

    Rückgabe-Dict-Keys:
      gruppe_key, gruppe_name, planstellen, davon_besetzt, davon_exkludiert,
      soll_mak, ist_mak, top_oes, top_jfs
    """
    sub = df[mask]
    if sub.empty:
        return {
            "gruppe_key": group_key,
            "gruppe_name": group_label,
            "planstellen": 0,
            "davon_besetzt": 0,
            "davon_exkludiert": 0,
            "soll_mak": 0.0,
            "ist_mak": 0.0,
            "top_oes": "—",
            "top_jfs": "—",
        }

    planstellen = len(sub)

    # Besetzte Planstellen: Is_Vacant == False
    if "Is_Vacant" in sub.columns:
        davon_besetzt = int((sub["Is_Vacant"] == False).sum())  # noqa: E712
        davon_exkludiert = int((sub["Is_Vacant"] == True).sum())  # noqa: E712
    else:
        davon_besetzt = planstellen
        davon_exkludiert = 0

    soll_mak = float(_soll_mak(sub).sum())
    ist_mak = float(_ist_mak(sub).sum())

    # Top OEs
    oe_col = next((c for c in ("Organisationseinheit", "Kürzel OrgEinheit") if c in sub.columns), None)
    top_oes = _top_values(sub[oe_col]) if oe_col else "—"

    # Top Jobfamilies
    jf_col = "Jobfamily" if "Jobfamily" in sub.columns else None
    top_jfs = _top_values(sub[jf_col]) if jf_col else "—"

    return {
        "gruppe_key": group_key,
        "gruppe_name": group_label,
        "planstellen": planstellen,
        "davon_besetzt": davon_besetzt,
        "davon_exkludiert": davon_exkludiert,
        "soll_mak": round(soll_mak, 2),
        "ist_mak": round(ist_mak, 2),
        "top_oes": top_oes,
        "top_jfs": top_jfs,
    }


def get_all_group_stats(df: pd.DataFrame) -> pd.DataFrame:
    """
    Gibt einen DataFrame mit KPIs für alle exkludierbaren Gruppen zurück.
    Gruppen ohne Mitglieder werden NICHT ausgefiltert (Transparenz).
    """
    if df is None or df.empty:
        return pd.DataFrame()

    masks = build_group_masks(df)

    # Gruppen-Definition: (key, label)
    group_defs = [
        (VORSTAND_KEY, "Vorstand"),
        (RUHEND_KEY, "Ruhendes Beschäftigungsverhältnis"),
    ] + [(code, label) for code, label in PA_GROUPS] + [(key, label) for key, label in SPECIAL_GROUPS]

    rows = []
    for key, label in group_defs:
        mask = masks.get(key, pd.Series(False, index=df.index))
        rows.append(aggregate_group(df, mask, key, label))

    return pd.DataFrame(rows)


def get_group_detail(df: pd.DataFrame, group_key: str) -> pd.DataFrame:
    """
    Gibt die Roh-Zeilen einer Gruppe zurück (für Drilldown).
    Gibt nur nicht-sensitive Spalten zurück (kein Name, kein Geburtsdatum).
    """
    masks = build_group_masks(df)
    mask = masks.get(group_key, pd.Series(False, index=df.index))
    sub = df[mask].copy()

    # Nicht-sensitive Spalten (kein Klarname, kein GebDatum)
    safe_cols = [
        c for c in [
            "PersNr", "Kürzel OrgEinheit", "Organisationseinheit",
            "MitarbGruppenbez.", "Jobfamily", "TrfGr", "St",
            "Status kundenindividuell", "Soll_FTE", "MAK_Calculated",
            "Is_Vacant", "ATZ_Status", "Vertragsart", "Planstellennr", "Planstelle",
        ]
        if c in sub.columns
    ]
    return sub[safe_cols] if safe_cols else sub
