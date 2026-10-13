"""Frozen exclusion behavior from commit 18ddb23; used only for compatibility tests."""

import pandas as pd
from typing import Dict, Any

from dataloader.soll_ist_koepfe_engine import _normalize_org_unit_series

def apply_exclusions(df: pd.DataFrame, exclusions: Dict[str, Any]) -> pd.DataFrame:
    """
    Wendet Gruppen-Ausschlüsse auf den Snapshot an.
    Exkludierte Personen werden als Vakanz markiert (Ist entfernt, Soll bleibt).

    ACHTUNG (v2.1):
    - Ist-Werte (PersNr, MAK, BsGrd) werden genullt/leert.
    - Soll-Werte (Soll_FTE, Sollarbeitszeit) bleiben erhalten (Bedarf).

    v2.2: Is_Excluded-Flag und Exclusion_Group werden ergaenzt (immer, auch ohne
    aktive Exklusion). Echte Vakanzen (Is_Vacant=True aus Snapshot, nicht durch
    diese Funktion gesetzt) erhalten Is_Excluded=False.

    v2.3: Besetzte Planstellen, die NUR ueber einen technischen Platzhaltergrund
    (Sollarbeitszeit ≈ 0,01 bzw. die feste Jobfamily-Validierungs-Liste) exkludiert
    wuerden, behalten ihre reale IST-Kapazitaet, wenn die besetzende Person einen
    Beschaeftigungsgrad (BsGrd) > 0 hat. Hintergrund: diese Planstellen sind zwar als
    "keine echte Stelle" markiert, koennen aber trotzdem mit einer real arbeitenden
    Person belegt sein (Analyse: reports/20260714_Sollkapazitaet_Ist_Abgleich_MB.xlsx,
    Blatt "Sonderfall 0,01-Planstellen" — 87 betroffene Planstellen, 67,5 FTE reale
    Kapazitaet). SOLL bleibt fuer diese Zeilen weiterhin exkludiert (Is_Excluded=True,
    SOLL_MAK_View=0) — nur IST wird nicht mehr blind genullt.
    Personenbezogene "harte" Ausschluesse (Vorstand, Ruhendes BV, PA-Bereiche,
    Azubi/Nachwuchs) sind von dieser Ausnahme NICHT betroffen: diese Gruppen werden
    unabhaengig vom BsGrd weiterhin vollstaendig aus IST entfernt, weil sie eine
    bewusste fachliche Entscheidung abbilden (z. B. "nicht aktiv arbeitend"), waehrend
    der 0,01-Fall ein reines Datenartefakt der Sollarbeitszeit-Spalte ist.

    Criteria (UI-konform):
    1. Vorstand: MitarbGruppenbez. == "Vorstand"
    2. Ruhendes BV: Status kundenindividuell == "Ruhendes Beschäftigungsverhältnis"
    3. PA-Bereiche: Kürzel OrgEinheit in org_units OR starts with "99" (if 99XX selected)
    4. Spezielle Gruppen: via build_group_masks()
    """
    if df.empty: return df

    df_out = df.copy()
    exclusion_mask = pd.Series(False, index=df_out.index)
    hard_exclusion_mask = pd.Series(False, index=df_out.index)
    _sub_masks: list[tuple[str, pd.Series]] = []

    # Gruppen, deren Exklusionsgrund ein reines Datenartefakt der Sollarbeitszeit-Spalte
    # ist (keine fachliche "Person ist nicht aktiv"-Entscheidung) -- fuer diese greift die
    # IST-Ausnahme bei realer Kapazitaet (siehe v2.3 oben).
    _TECHNICAL_PLACEHOLDER_GROUPS = {
        "jobfamily_validation_special_positions",
        "sollarbeitszeit_001_positions",
    }

    _EXCL_GROUP_LABELS = {
        "ausbildung_nachwuchs":                  "Azubi / Nachwuchs",
        "jobfamily_validation_special_positions": "Jobfamily Validierung",
        "sollarbeitszeit_001_positions":          "Sollarbeitszeit ≤ 0,01",
    }

    # 1. Vorstand
    if exclusions.get("vorstand"):
        if "MitarbGruppenbez." in df_out.columns:
            _m = df_out["MitarbGruppenbez."] == "Vorstand"
            exclusion_mask |= _m
            hard_exclusion_mask |= _m
            _sub_masks.append(("Vorstand", _m))

    # 2. Ruhendes BV (via Status-Feld — identisch mit build_group_masks() in exclusion_groups.py)
    # Fruehhere Version nutzte OE=="9900"; das wich von build_group_masks() ab und schloss
    # Personen mit Status "Ruhendes BV" in anderen OEs nicht aus.
    # OE 9900 wird weiterhin separat ueber org_units erfasst (PA Ruhendes BV).
    if exclusions.get("ruhend_bv"):
        if "Status kundenindividuell" in df_out.columns:
            _m = (
                df_out["Status kundenindividuell"].astype(str).str.strip()
                == "Ruhendes Beschäftigungsverhältnis"
            )
            exclusion_mask |= _m
            hard_exclusion_mask |= _m
            _sub_masks.append(("Ruhendes BV", _m))

    # 3. Spezifische PA-Bereiche (und 99XX-Logik)
    ex_org_units = exclusions.get("org_units", [])
    if ex_org_units and "Kürzel OrgEinheit" in df_out.columns:
        s_ou = df_out["Kürzel OrgEinheit"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        _m_ou = s_ou.isin(ex_org_units)
        # 99XX Spezialregel: Alles was mit 99 beginnt, aber NICHT bereits explizit gelistete Codes
        # (konsistent mit build_group_masks() in exclusion_groups.py)
        if "99XX" in ex_org_units:
            explicit_pa_codes = {c for c in ex_org_units if c != "99XX"}
            _m_ou = _m_ou | (s_ou.str.startswith("99") & ~s_ou.isin(explicit_pa_codes))
        exclusion_mask |= _m_ou
        hard_exclusion_mask |= _m_ou
        _sub_masks.append(("PA-Bereich", _m_ou))

    special_groups = exclusions.get("special_groups") or []
    if special_groups:
        from utils.exclusion_groups import build_group_masks

        group_masks = build_group_masks(df_out)
        for group_key in special_groups:
            if group_key in group_masks:
                _m = group_masks[group_key]
                exclusion_mask |= _m
                if group_key not in _TECHNICAL_PLACEHOLDER_GROUPS:
                    hard_exclusion_mask |= _m
                _sub_masks.append((_EXCL_GROUP_LABELS.get(group_key, group_key), _m))

    # Exklusionsflag — immer initialisieren (auch wenn keine Exklusion aktiv)
    df_out["Is_Excluded"] = False
    df_out["Exclusion_Group"] = ""

    # v2.3: reale IST-Kapazitaet ermitteln, BEVOR Personendaten geleert werden.
    # Nur Zeilen, die ausschliesslich ueber einen technischen Platzhaltergrund
    # exkludiert wuerden (nicht zusaetzlich Vorstand/Ruhend/PA-Bereich/Azubi) UND
    # real mit einer Person mit BsGrd>0 besetzt sind, behalten ihre IST-Werte.
    has_real_person = (
        df_out["Personalnummer"].notna() if "Personalnummer" in df_out.columns else pd.Series(False, index=df_out.index)
    )
    has_positive_bsgrd = (
        pd.to_numeric(df_out["BsGrd"], errors="coerce").fillna(0) > 0
        if "BsGrd" in df_out.columns else pd.Series(False, index=df_out.index)
    )
    ist_preserve_mask = exclusion_mask & ~hard_exclusion_mask & has_real_person & has_positive_bsgrd
    df_out["Is_IST_Preserved_Despite_Exclusion"] = ist_preserve_mask

    # --- Anwendung der Exklusion (Vacating) ---
    if exclusion_mask.any():
        try:
            import streamlit as st
            st.session_state["last_exclusion_count"] = int(exclusion_mask.sum())
            st.session_state["last_ist_preserved_count"] = int(ist_preserve_mask.sum())
        except Exception:
            pass

        # Felder, die zur PERSON gehören (Ist-Identifier)
        person_fields = [
            "Personalnummer", "PersNr", "Personalnachname", "Personalvorname",
            "Name", "Vorname", "Nachname", "GebDatum", "Eintritt", "Austritt",
            "Alter", "Alter_Jahre", "Ist_Azubi",
            "MAK_raw", "mak_raw", "MAK_Calculated_raw", "BsGrd_raw"
        ]

        # Kennzahlen, die das IST-Volumen beschreiben.
        # MAK_Reporting und EUR_Reporting entstehen erst spaeter in
        # apply_person_mak_allocation() (laeuft NACH apply_exclusions()) und existieren
        # hier daher noch nicht als Spalten -- der Schutz vor "excluded rows leaken in
        # MAK_Reporting" erfolgt stattdessen ueber das Is_Vacant-Flag unten, das
        # apply_person_mak_allocation() respektiert (inaktive Zeilen -> MAK_Reporting=0).
        ist_metrics = [
            "MAK", "mak", "MAK_Calculated", "BsGrd",
            "FTE_person", "FTE_assigned", "Total_Cost_Year",
            "MAK_Reporting", "EUR_Reporting",
            "Allocation_Weight", "Personen_MAK",
        ]

        # Zeilen, die tatsaechlich genullt/geleert werden (Exklusion MINUS IST-geschuetzte Zeilen)
        zero_out_mask = exclusion_mask & ~ist_preserve_mask

        # Markiere als Vakanz (IST-geschuetzte Zeilen bleiben besetzt, damit
        # apply_person_mak_allocation() sie als aktiv behandelt)
        if "Is_Vacant" not in df_out.columns:
            df_out["Is_Vacant"] = False

        df_out["Is_Vacant"] = df_out["Is_Vacant"].astype("boolean")
        df_out.loc[zero_out_mask, "Is_Vacant"] = True

        # Is_Excluded und Exclusion_Group setzen (erste Gruppe gewinnt bei Mehrfachzuordnung)
        # -- gilt fuer ALLE exkludierten Zeilen (auch IST-geschuetzte), da SOLL weiterhin
        # exkludiert bleibt und die Transparenz in Deep-Dive-Auswertungen erhalten bleiben soll.
        df_out.loc[exclusion_mask, "Is_Excluded"] = True
        for _grp_label, _grp_mask in _sub_masks:
            _new = _grp_mask & df_out["Exclusion_Group"].eq("")
            df_out.loc[_new, "Exclusion_Group"] = _grp_label

        # Leere Personendaten (nur bei tatsaechlich genullten Zeilen)
        existing_person_fields = [f for f in person_fields if f in df_out.columns]
        # Bool-Spalten (z.B. Ist_Azubi) muessen vor der pd.NA-Zuweisung auf
        # nullable "boolean" gecastet werden — sonst FutureWarning "incompatible dtype".
        for f in existing_person_fields:
            if df_out[f].dtype == bool:
                df_out[f] = df_out[f].astype("boolean")
        df_out.loc[zero_out_mask, existing_person_fields] = pd.NA

        # Nulle Ist-Volumen (nur bei tatsaechlich genullten Zeilen)
        existing_ist_metrics = [f for f in ist_metrics if f in df_out.columns]
        df_out.loc[zero_out_mask, existing_ist_metrics] = 0.0

        # HINWEIS: Soll_FTE und Sollarbeitszeit werden NICHT angefasst.

    return df_out

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
