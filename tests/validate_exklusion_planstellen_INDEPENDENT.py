from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_DIR = Path(__file__).resolve().parent
KSK_LAYOUT_DIR = SCRIPT_DIR.parent
BASE_DIR = KSK_LAYOUT_DIR.parent
ORIG_DIR = BASE_DIR / "Original-Daten"
SETTINGS_PATH = KSK_LAYOUT_DIR / "config" / "user_settings.json"
PLANSTELLEN_PATH = ORIG_DIR / "Planstellen.XLSX"
MITARBEITER_PATH = ORIG_DIR / "Mitarbeiter.xlsx"


REFERENCE = {
    "raw_total": 1729,
    "oe_removed": 397,
    "oe_remaining": 1332,
    "vorstand_non99xx": 3,
    "vorstand_oe": "800",
    "ruhend_non99xx": 0,
    "oe800_collateral_vacant": 2,
    "toggle_delta": 3,
    "toggle_remaining": 1329,
}


PERSON_FIELDS = [
    "Personalnummer",
    "PersNr",
    "Personalnachname",
    "Personalvorname",
    "Name",
    "Vorname",
    "Nachname",
    "GebDatum",
    "Eintritt",
    "Austritt",
    "Alter",
    "Alter_Jahre",
    "Ist_Azubi",
    "MAK_raw",
    "mak_raw",
    "MAK_Calculated_raw",
    "BsGrd_raw",
]


IST_METRICS = [
    "MAK",
    "mak",
    "MAK_Calculated",
    "BsGrd",
    "FTE_person",
    "FTE_assigned",
    "Total_Cost_Year",
]


@dataclass
class TestOutcome:
    name: str
    passed: bool
    reason: str


def banner(title: str) -> None:
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


def find_col(df: pd.DataFrame, *candidates: str) -> str:
    lower = {str(col).lower(): col for col in df.columns}
    for candidate in candidates:
        needle = candidate.lower()
        if needle in lower:
            return lower[needle]
        for key, value in lower.items():
            if needle in key:
                return value
    raise KeyError(f"Spalte nicht gefunden: {candidates}")


def normalize_text_id(series: pd.Series) -> pd.Series:
    out = series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    lower = out.str.lower()
    out = out.mask(lower.isin(["nan", "none", "<na>"]), pd.NA)
    return out


def normalize_oe(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def normalize_eg_raw(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().upper().replace(" ", "")
    if text.startswith("BIS"):
        text = text[3:].strip()
    return text


def read_settings() -> dict[str, Any]:
    with SETTINGS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_raw_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not PLANSTELLEN_PATH.exists() or not MITARBEITER_PATH.exists():
        raise FileNotFoundError(
            f"Original-Daten fehlen. Erwartet: {PLANSTELLEN_PATH} und {MITARBEITER_PATH}"
        )
    return pd.read_excel(PLANSTELLEN_PATH), pd.read_excel(MITARBEITER_PATH)


def build_snapshot(planstellen: pd.DataFrame, mitarbeiter: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    pl = planstellen.copy()
    ma = mitarbeiter.copy()

    cols = {
        "pl_persnr": find_col(pl, "personalnummer", "persnr"),
        "pl_oe": find_col(pl, "kürzel", "orgeinheit"),
        "pl_org_name": find_col(pl, "organisationseinheit"),
        "pl_soll": find_col(pl, "sollarbeitszeit"),
        "pl_soll_basis": find_col(pl, "bewertung tarifgruppe"),
        "pl_soll_band": find_col(pl, "text gehaltsband"),
        "pl_planstelle": find_col(pl, "planstelle"),
        "ma_persnr": find_col(ma, "persnr", "personalnummer"),
        "ma_group": find_col(ma, "mitarbgruppenbez"),
        "ma_status": find_col(ma, "status kundenindividuell"),
        "ma_bsgrd": find_col(ma, "bsgrd"),
        "ma_trfgr": find_col(ma, "trfgr"),
        "ma_vorname": find_col(ma, "vorname"),
        "ma_nachname": find_col(ma, "nachname"),
    }

    pl["_persnr_norm"] = normalize_text_id(pl[cols["pl_persnr"]])
    ma["_persnr_norm"] = normalize_text_id(ma[cols["ma_persnr"]])

    merged = pl.merge(ma, on="_persnr_norm", how="left", suffixes=("", "_ma"))
    merged["Is_Vacant"] = merged["_persnr_norm"].isna()
    merged["FTE_person"] = pd.to_numeric(merged[cols["ma_bsgrd"]], errors="coerce").fillna(0.0) / 100.0
    merged["Soll_FTE"] = pd.to_numeric(merged[cols["pl_soll"]], errors="coerce").fillna(0.0) / 39.0
    merged.loc[(merged["Soll_FTE"] > 0.0) & (merged["Soll_FTE"] < 0.015), "Soll_FTE"] = 0.0
    merged["FTE_assigned"] = merged["FTE_person"] * merged["Soll_FTE"]
    merged["MAK"] = merged["FTE_person"]
    ruhend_mask = (
        merged[cols["ma_status"]].astype("string").str.strip()
        == "Ruhendes Beschäftigungsverhältnis"
    )
    merged.loc[ruhend_mask.fillna(False), "MAK"] = 0.0
    merged["Total_Cost_Year"] = 0.0
    return merged, cols


def manual_apply_exclusions(df: pd.DataFrame, exclusions: dict[str, Any], cols: dict[str, str]) -> tuple[pd.DataFrame, pd.Series]:
    out = df.copy()
    exclusion_mask = pd.Series(False, index=out.index)

    if exclusions.get("vorstand"):
        exclusion_mask |= (
            out[cols["ma_group"]].astype("string").str.strip() == "Vorstand"
        ).fillna(False)

    if exclusions.get("ruhend_bv"):
        exclusion_mask |= (
            out[cols["ma_status"]].astype("string").str.strip()
            == "Ruhendes Beschäftigungsverhältnis"
        ).fillna(False)

    ex_org_units = exclusions.get("org_units", [])
    if ex_org_units:
        s_ou = normalize_oe(out[cols["pl_oe"]])
        explicit = [code for code in ex_org_units if code != "99XX"]
        exclusion_mask |= s_ou.isin(explicit).fillna(False)
        if "99XX" in ex_org_units:
            exclusion_mask |= (s_ou.str.startswith("99") & ~s_ou.isin(set(explicit))).fillna(False)

    out["Is_Vacant"] = out["Is_Vacant"].astype("boolean")
    out.loc[exclusion_mask, "Is_Vacant"] = True

    existing_person_fields = [field for field in PERSON_FIELDS if field in out.columns]
    for field in existing_person_fields:
        if out[field].dtype == bool:
            out[field] = out[field].astype("boolean")
    out.loc[exclusion_mask, existing_person_fields] = pd.NA

    existing_ist_metrics = [field for field in IST_METRICS if field in out.columns]
    out.loc[exclusion_mask, existing_ist_metrics] = 0.0

    return out, exclusion_mask


def apply_oe_filter(df: pd.DataFrame, exclusions: dict[str, Any], cols: dict[str, str]) -> tuple[pd.DataFrame, pd.Series]:
    ex_org_units = exclusions.get("org_units", [])
    if not ex_org_units:
        empty = pd.Series(False, index=df.index)
        return df.copy(), empty

    s_ou = normalize_oe(df[cols["pl_oe"]])
    explicit = [code for code in ex_org_units if code != "99XX"]
    mask = s_ou.isin(explicit).fillna(False)
    if "99XX" in ex_org_units:
        mask |= (s_ou.str.startswith("99") & ~s_ou.isin(set(explicit))).fillna(False)
    return df.loc[~mask].copy(), mask


def apply_person_plan_filter(df: pd.DataFrame, exclusions: dict[str, Any], cols: dict[str, str]) -> tuple[pd.DataFrame, pd.Series]:
    mask = pd.Series(False, index=df.index)
    if exclusions.get("vorstand"):
        mask |= (
            df[cols["ma_group"]].astype("string").str.strip() == "Vorstand"
        ).fillna(False)
    if exclusions.get("ruhend_bv"):
        mask |= (
            df[cols["ma_status"]].astype("string").str.strip()
            == "Ruhendes Beschäftigungsverhältnis"
        ).fillna(False)
    return df.loc[~mask].copy(), mask


def build_soll_ist_projection(df: pd.DataFrame, cols: dict[str, str], use_max_eg: bool = True) -> dict[str, Any]:
    work = df.copy()
    invalid = {"", "NAN", "NONE"}

    col_h = work[cols["pl_soll_basis"]].map(normalize_eg_raw)
    col_i = work[cols["pl_soll_band"]].map(normalize_eg_raw)
    work["_Soll_EG_H"] = col_h
    work["_Soll_EG_I"] = col_i.where(~col_i.isin(invalid), other=col_h)
    work["_Soll_EG"] = work["_Soll_EG_I"] if use_max_eg else col_h

    work["_Ist_EG"] = work.apply(
        lambda row: (
            "Unbesetzt"
            if bool(row.get("Is_Vacant", True))
            else (
                "Nicht gefunden"
                if pd.isna(row.get(cols["ma_trfgr"]))
                or str(row.get(cols["ma_trfgr"])).strip().lower() in ("", "nan")
                else str(row.get(cols["ma_trfgr"])).strip().upper().replace(" ", "")
            )
        ),
        axis=1,
    )

    no_soll_mask = work["_Soll_EG"].isin(invalid)
    no_soll_occupied = work[no_soll_mask & ~work["_Ist_EG"].isin(["Unbesetzt", "Nicht gefunden"])]
    return {
        "work": work,
        "no_soll_mask": no_soll_mask,
        "no_soll_occupied": no_soll_occupied,
        "no_soll_eg_row": no_soll_occupied["_Ist_EG"].value_counts(),
    }


def hash_dataframe(df: pd.DataFrame) -> int:
    normalized = df.copy()
    for col in normalized.columns:
        normalized[col] = normalized[col].astype("string").fillna("<NA>")
    return int(pd.util.hash_pandas_object(normalized, index=True).sum())


def occupancy_stats(df: pd.DataFrame) -> tuple[int, int, int]:
    occupied = int((df["Is_Vacant"] == False).sum())  # noqa: E712
    vacant = int((df["Is_Vacant"] == True).sum())  # noqa: E712
    return len(df), occupied, vacant


def print_table(df: pd.DataFrame) -> None:
    if df.empty:
        print("(leer)")
        return
    print(df.to_string(index=False))


def main() -> int:
    banner("VALIDIERUNG: Planstellen-Exklusion via Personengruppen (Standalone, ohne Streamlit)")

    settings = read_settings()
    exclusions = settings.get("exclusions", {})
    planstellen, mitarbeiter = load_raw_data()
    snapshot, cols = build_snapshot(planstellen, mitarbeiter)

    print(f"Planstellen-Datei : {PLANSTELLEN_PATH}")
    print(f"Mitarbeiter-Datei : {MITARBEITER_PATH}")
    print(f"user_settings.json: {SETTINGS_PATH}")
    print(f"Rohdaten-Planstellen: {len(planstellen)}")
    print(f"Rohdaten-Mitarbeiter: {len(mitarbeiter)}")
    print(f"Snapshot nach LEFT JOIN: {len(snapshot)}")
    print(f"Aktive Exklusions-Settings: {json.dumps(exclusions, ensure_ascii=False)}")

    outcomes: list[TestOutcome] = []

    # TEST 1
    banner("TEST 1 – Datenmapping nach manueller apply_exclusions-Logik")
    person_only_vorstand = {"vorstand": True, "ruhend_bv": False, "org_units": []}
    snapshot_vorstand, vorstand_mask = manual_apply_exclusions(snapshot, person_only_vorstand, cols)
    vorstand_rows_after = snapshot_vorstand.loc[vorstand_mask, [
        cols["ma_group"], cols["ma_status"], "PersNr", cols["ma_vorname"], cols["ma_nachname"], cols["ma_bsgrd"], "Is_Vacant"
    ]].copy()

    person_only_ruhend = {"vorstand": False, "ruhend_bv": True, "org_units": []}
    snapshot_ruhend, ruhend_mask = manual_apply_exclusions(snapshot, person_only_ruhend, cols)
    ruhend_rows_after = snapshot_ruhend.loc[ruhend_mask, [cols["ma_status"], "PersNr", cols["ma_bsgrd"], "Is_Vacant"]].copy()

    print(f"Vorstand-Zeilen vor Exklusion: {int(vorstand_mask.sum())}")
    print(f"Ruhend-BV-Zeilen vor Exklusion: {int(ruhend_mask.sum())}")
    print("Vorstand-Zeilen nach manueller Person-Exklusion:")
    print_table(vorstand_rows_after.head(10))
    print("Ruhend-BV-Zeilen nach manueller Person-Exklusion:")
    print_table(ruhend_rows_after.head(10))

    mitarb_gruppe_preserved = (
        cols["ma_group"] not in PERSON_FIELDS
        and int(vorstand_mask.sum()) == 3
        and snapshot_vorstand.loc[vorstand_mask, cols["ma_group"]].astype("string").str.strip().eq("Vorstand").all()
    )
    status_preserved = cols["ma_status"] not in PERSON_FIELDS
    if int(ruhend_mask.sum()) > 0:
        status_preserved = status_preserved and (
            snapshot_ruhend.loc[ruhend_mask, cols["ma_status"]]
            .astype("string")
            .str.strip()
            .eq("Ruhendes Beschäftigungsverhältnis")
            .all()
        )

    test1_pass = (
        mitarb_gruppe_preserved
        and status_preserved
        and snapshot_vorstand.loc[vorstand_mask, "PersNr"].isna().all()
        and (snapshot_vorstand.loc[vorstand_mask, cols["ma_bsgrd"]] == 0.0).all()
    )
    test1_reason = (
        f"MitarbGruppenbez. bleibt bei {int(vorstand_mask.sum())} Vorstand-Zeilen erhalten; "
        f"PersNr/Vorname/Nachname/BsGrd werden wie erwartet geleert bzw. genullt. "
        f"Status kundenindividuell ist nicht in PERSON_FIELDS und bleibt strukturell unangetastet"
        + (
            "; in diesem direkten LEFT JOIN existieren jedoch 0 Ruhend-BV-Zeilen."
            if int(ruhend_mask.sum()) == 0
            else "; vorhandene Ruhend-BV-Zeilen behalten ihren Statuswert."
        )
    )
    outcomes.append(TestOutcome("TEST 1 – Datenmapping", test1_pass, test1_reason))

    # TEST 2
    banner("TEST 2 – Planstellen-Filter mit Toggle OFF vs. ON")
    after_oe, oe_mask = apply_oe_filter(snapshot, exclusions, cols)
    after_toggle, person_plan_mask = apply_person_plan_filter(
        after_oe,
        {
            "vorstand": exclusions.get("vorstand", False),
            "ruhend_bv": exclusions.get("ruhend_bv", False),
        },
        cols,
    )
    removed_by_toggle = int(person_plan_mask.sum())
    toggle_oes = normalize_oe(after_oe.loc[person_plan_mask, cols["pl_oe"]]).value_counts().rename_axis("OE").reset_index(name="Anzahl")

    print(f"Rohdaten gesamt: {len(snapshot)}")
    print(f"Nach OE-Exklusion: {len(after_oe)}")
    print(f"Durch OE-Exklusion entfernt: {int(oe_mask.sum())}")
    print(f"Nach Toggle ON: {len(after_toggle)}")
    print(f"Zusätzlich durch Toggle entfernt: {removed_by_toggle}")
    print("OEs der zusätzlich entfernten Planstellen:")
    print_table(toggle_oes)

    test2_pass = (
        len(snapshot) == REFERENCE["raw_total"]
        and int(oe_mask.sum()) == REFERENCE["oe_removed"]
        and len(after_oe) == REFERENCE["oe_remaining"]
        and removed_by_toggle == REFERENCE["toggle_delta"]
        and len(after_toggle) == REFERENCE["toggle_remaining"]
        and toggle_oes.to_dict("records") == [{"OE": REFERENCE["vorstand_oe"], "Anzahl": REFERENCE["vorstand_non99xx"]}]
    )
    test2_reason = (
        f"OE-Filter entfernt {int(oe_mask.sum())} Zeilen auf {len(after_oe)}; "
        f"Toggle ON entfernt weitere {removed_by_toggle} Zeilen. "
        f"Alle Zusatzentfernungen liegen in OE {REFERENCE['vorstand_oe']}."
    )
    outcomes.append(TestOutcome("TEST 2 – Toggle-Filterlogik", test2_pass, test2_reason))

    # TEST 3
    banner("TEST 3 – Kollateralschaden in denselben OEs")
    affected_oes = set(toggle_oes["OE"].tolist())
    after_oe_norm = normalize_oe(after_oe[cols["pl_oe"]])
    same_oe_rows = after_oe.loc[after_oe_norm.isin(affected_oes)].copy()
    same_oe_target_mask = (
        same_oe_rows[cols["ma_group"]].astype("string").str.strip() == "Vorstand"
    ).fillna(False)
    same_oe_collateral = same_oe_rows.loc[~same_oe_target_mask].copy()
    collateral_vacant = same_oe_collateral.loc[same_oe_collateral["Is_Vacant"] == True].copy()  # noqa: E712
    ruhend_non99xx = snapshot.loc[
        (
            snapshot[cols["ma_status"]].astype("string").str.strip()
            == "Ruhendes Beschäftigungsverhältnis"
        ).fillna(False)
        & ~normalize_oe(snapshot[cols["pl_oe"]]).str.startswith("99").fillna(False)
    ].copy()

    print(f"Betroffene OEs durch Toggle: {sorted(affected_oes)}")
    print(f"Alle Planstellen in diesen OEs: {len(same_oe_rows)}")
    print(f"Davon Ziel-Planstellen (Vorstand): {int(same_oe_target_mask.sum())}")
    print(f"Davon Kollateral außerhalb Vorstand: {len(same_oe_collateral)}")
    print(f"Davon vakante Nicht-Vorstand-Planstellen: {len(collateral_vacant)}")
    print(f"Ruhend-BV-Planstellen in non-99xx-OEs: {len(ruhend_non99xx)}")

    test3_pass = (
        affected_oes == {REFERENCE["vorstand_oe"]}
        and len(same_oe_rows) == 5
        and int(same_oe_target_mask.sum()) == REFERENCE["vorstand_non99xx"]
        and len(collateral_vacant) == REFERENCE["oe800_collateral_vacant"]
        and len(ruhend_non99xx) == REFERENCE["ruhend_non99xx"]
    )
    test3_reason = (
        f"In OE {REFERENCE['vorstand_oe']} liegen 5 Planstellen: 3 Vorstand-Zeilen plus "
        f"{len(collateral_vacant)} vakante Nicht-Vorstand-Planstellen. "
        f"Der personenscharfe Toggle entfernt nur die 3 Zielzeilen; Ruhend-BV erzeugt kein zusätzliches non-99xx-Risiko."
    )
    outcomes.append(TestOutcome("TEST 3 – Kollateralanalyse", test3_pass, test3_reason))

    # TEST 4
    banner("TEST 4 – Cache-Hash-Differenz bei Exklusionswechsel")
    snapshot_hash_on, _ = manual_apply_exclusions(snapshot, {"vorstand": True, "ruhend_bv": False, "org_units": []}, cols)
    snapshot_hash_off, _ = manual_apply_exclusions(snapshot, {"vorstand": False, "ruhend_bv": False, "org_units": []}, cols)
    hash_on = hash_dataframe(snapshot_hash_on)
    hash_off = hash_dataframe(snapshot_hash_off)
    changed_columns = [
        column
        for column in snapshot_hash_on.columns
        if not snapshot_hash_on[column].astype("string").fillna("<NA>").equals(
            snapshot_hash_off[column].astype("string").fillna("<NA>")
        )
    ]

    print(f"Hash mit Vorstand-Exklusion ON : {hash_on}")
    print(f"Hash mit Vorstand-Exklusion OFF: {hash_off}")
    print(f"Hashes unterschiedlich          : {hash_on != hash_off}")
    print(f"Geänderte Spalten              : {changed_columns}")

    required_changed = {"Personalnummer", "PersNr", "BsGrd", "Is_Vacant", "FTE_person", "FTE_assigned", "MAK"}
    test4_pass = hash_on != hash_off and required_changed.issubset(set(changed_columns))
    test4_reason = (
        f"Die Snapshot-Hashes unterscheiden sich klar. Betroffen sind mindestens {sorted(required_changed)}; "
        f"damit ist die Exklusionsänderung datenwirksam und cache-relevant."
    )
    outcomes.append(TestOutcome("TEST 4 – Cache-Hash-Differenz", test4_pass, test4_reason))

    # TEST 5
    banner("TEST 5 – End-to-End Konsistenzprüfung")
    scenario_rows: list[dict[str, Any]] = []

    def add_scenario(name: str, frame: pd.DataFrame, base_total: int | None = None) -> None:
        total, occupied, vacant = occupancy_stats(frame)
        delta = "—" if base_total is None else total - base_total
        scenario_rows.append(
            {
                "Szenario": name,
                "Planstellen gesamt": total,
                "Besetzt": occupied,
                "Unbesetzt": vacant,
                "Δ zu Basis": delta,
            }
        )

    base = snapshot.copy()
    add_scenario("Keine Exklusion", base)

    oe_only, _ = apply_oe_filter(base, exclusions, cols)
    add_scenario("OE-only (99xx)", oe_only, len(base))

    oe_vor_person, _ = apply_oe_filter(
        manual_apply_exclusions(base, {"vorstand": True, "ruhend_bv": False, "org_units": []}, cols)[0],
        exclusions,
        cols,
    )
    add_scenario("OE + Vorstand als Person", oe_vor_person, len(base))

    oe_vor_plan, _ = apply_person_plan_filter(
        oe_only,
        {"vorstand": True, "ruhend_bv": False},
        cols,
    )
    add_scenario("OE + Vorstand als Planstelle (Toggle ON)", oe_vor_plan, len(base))

    oe_all_plan, _ = apply_person_plan_filter(
        apply_oe_filter(
            manual_apply_exclusions(base, {"vorstand": True, "ruhend_bv": True, "org_units": []}, cols)[0],
            exclusions,
            cols,
        )[0],
        {"vorstand": True, "ruhend_bv": True},
        cols,
    )
    add_scenario("OE + alle Personen-Exkl. als Planstellen", oe_all_plan, len(base))

    scenario_df = pd.DataFrame(scenario_rows)
    print_table(scenario_df)

    expected_scenarios = {
        "Keine Exklusion": (1729, 1247, 482),
        "OE-only (99xx)": (1332, 986, 346),
        "OE + Vorstand als Person": (1332, 983, 349),
        "OE + Vorstand als Planstelle (Toggle ON)": (1329, 983, 346),
        "OE + alle Personen-Exkl. als Planstellen": (1329, 983, 346),
    }
    scenario_ok = True
    for _, row in scenario_df.iterrows():
        expected = expected_scenarios[row["Szenario"]]
        actual = (int(row["Planstellen gesamt"]), int(row["Besetzt"]), int(row["Unbesetzt"]))
        if actual != expected:
            scenario_ok = False
            break

    test5_reason = (
        "Die fünf Szenarien reproduzieren die Referenzwerte exakt. "
        "Wichtigster Effekt: 'Vorstand als Person' verschiebt nur Besetzt/Unbesetzt, "
        "während 'Vorstand als Planstelle' die Matrix um 3 Zeilen verkleinert."
    )
    outcomes.append(TestOutcome("TEST 5 – End-to-End Pipeline", scenario_ok, test5_reason))

    # TEST 6
    banner("TEST 6 – Vorstand-Planstellen und '(Keine Soll-EG)'")
    vorstand_non99xx = after_oe.loc[
        (after_oe[cols["ma_group"]].astype("string").str.strip() == "Vorstand").fillna(False)
    ].copy()
    projection = build_soll_ist_projection(vorstand_non99xx, cols, use_max_eg=True)
    no_soll_occupied = projection["no_soll_occupied"]
    no_soll_row = projection["no_soll_eg_row"]

    print("Vorstand-Planstellen in non-99xx-OEs:")
    print_table(
        projection["work"][[
            cols["pl_oe"],
            cols["pl_org_name"],
            cols["pl_planstelle"],
            cols["pl_soll_basis"],
            cols["pl_soll_band"],
            cols["ma_trfgr"],
            "_Soll_EG",
            "_Ist_EG",
            "Is_Vacant",
        ]]
    )
    print("Verteilung für '(Keine Soll-EG)':")
    print(no_soll_row.to_string() if not no_soll_row.empty else "(leer)")

    basis_missing = projection["work"][cols["pl_soll_basis"]].isna().all()
    band_missing = projection["work"][cols["pl_soll_band"]].isna().all()
    trfgr_all_one = projection["work"][cols["ma_trfgr"]].astype("string").str.strip().eq("1").all()
    test6_pass = (
        len(vorstand_non99xx) == REFERENCE["vorstand_non99xx"]
        and basis_missing
        and band_missing
        and trfgr_all_one
        and len(no_soll_occupied) == REFERENCE["vorstand_non99xx"]
        and int(no_soll_row.get("1", 0)) == REFERENCE["vorstand_non99xx"]
    )
    test6_reason = (
        "Alle 3 Vorstand-Planstellen haben weder Bewertung Tarifgruppe (Spalte H) noch Text Gehaltsband (Spalte I), "
        "aber Ist-EG/TrfGr='1'. Dadurch ist _Soll_EG leer, _Ist_EG jedoch besetzt, und die Zeilen landen korrekt in '(Keine Soll-EG)'."
    )
    outcomes.append(TestOutcome("TEST 6 – Soll-EG Vorstand", test6_pass, test6_reason))

    # SUMMARY
    banner("ABSCHLUSSBERICHT")
    failed = [outcome for outcome in outcomes if not outcome.passed]
    for outcome in outcomes:
        status = "PASS" if outcome.passed else "FAIL"
        print(f"{outcome.name:<35} [{status}] – {outcome.reason}")

    if not failed:
        overall = "BESTANDEN"
        critical = "Keine kritischen Abweichungen gegen die referenzierten Originaldaten."
        recommendations = (
            "Implementierung beibehalten. Optional: diesen Standalone-Test in die Regression aufnehmen und "
            "Ruhend-BV künftig zusätzlich mit einem Datensatzfall außerhalb 99xx absichern."
        )
    elif len(failed) <= 2 and any(outcome.name == "TEST 1 – Datenmapping" for outcome in failed):
        overall = "BEDINGT BESTANDEN"
        critical = "Teilweise Abweichungen in der Datenabbildung; Kernzählwerte können dennoch korrekt sein."
        recommendations = "Abweichende Teilpfade gezielt nachziehen und Test erneut ausführen."
    else:
        overall = "NICHT BESTANDEN"
        critical = "; ".join(f"{outcome.name}: {outcome.reason}" for outcome in failed)
        recommendations = "Produktcode gegen die im Skript belegten Abweichungen korrigieren und die Matrixlogik erneut validieren."

    print()
    print(f"GESAMTURTEIL: {overall}")
    print(f"Kritische Befunde: {critical}")
    print(f"Empfehlungen: {recommendations}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
