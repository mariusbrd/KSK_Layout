"""Read-only reconciliation of input rows, unique people and 39-hour capacity."""
from __future__ import annotations

import pandas as pd

from abgaenge.schemas import normalize_persnr
from dataloader.mak_allocation import apply_person_mak_allocation


CAPACITY_GROUPS = ["Vollzeit-Stelle", "Teilzeit-Stelle", "Keine Soll-Kapazität", "Sollstunden unklar / abweichend"]
ASSIGNMENT_GROUPS = ["Eindeutig besetzt", "Unbesetzt", "Mitarbeiterdatensatz fehlt", "Mitarbeiterzuordnung mehrdeutig"]


def build_stellenplan_audit(mitarbeiter: pd.DataFrame, planstellen: pd.DataFrame) -> dict:
    """Never multiply position rows through duplicate employee IDs or discard bad rows.

    Capacity is the input employment percentage on a 39-hour basis, before date,
    status or user exclusions. Only unambiguous employees with valid percentages
    contribute known capacity; missing capacity is explicitly counted separately.
    """
    for frame, column in [(mitarbeiter, "PersNr"), (planstellen, "Personalnummer")]:
        if column not in frame:
            raise ValueError(f"Pflichtspalte fehlt: {column}")
    ma = mitarbeiter.copy().reset_index(drop=True)
    pl = planstellen.copy().reset_index(drop=True)
    ma["PersNr"] = normalize_persnr(ma["PersNr"])
    pl["Personalnummer"] = normalize_persnr(pl["Personalnummer"])
    counts = ma["PersNr"].dropna().value_counts()
    unique = ma[ma["PersNr"].map(counts).eq(1)].set_index("PersNr")
    match_count = pl["Personalnummer"].map(counts).fillna(0)
    pl["Zuordnung"] = "Eindeutig besetzt"
    pl.loc[pl["Personalnummer"].isna(), "Zuordnung"] = "Unbesetzt"
    pl.loc[pl["Personalnummer"].notna() & match_count.eq(0), "Zuordnung"] = "Mitarbeiterdatensatz fehlt"
    pl.loc[match_count.gt(1), "Zuordnung"] = "Mitarbeiterzuordnung mehrdeutig"
    hours = pd.to_numeric(pl.get("Sollarbeitszeit", pd.Series(index=pl.index, dtype=float)), errors="coerce")
    pl["Stellenart"] = CAPACITY_GROUPS[3]
    pl.loc[hours.sub(39).abs().le(1e-6), "Stellenart"] = CAPACITY_GROUPS[0]
    pl.loc[hours.gt(0) & hours.lt(39 - 1e-6), "Stellenart"] = CAPACITY_GROUPS[1]
    pl.loc[hours.eq(0), "Stellenart"] = CAPACITY_GROUPS[2]
    bsgrd = pl["Personalnummer"].map(pd.to_numeric(unique.get("BsGrd", pd.Series(dtype=float)), errors="coerce"))
    matched = pl["Zuordnung"].eq("Eindeutig besetzt")
    valid_capacity = bsgrd.between(0, 100) & matched
    # The input percentage is a PERSON attribute. Supply no duplicated technical
    # row MAK, so the existing allocator uses its person cap and Soll weights.
    allocation_input = pd.DataFrame({
        "PersNr": pl["Personalnummer"].where(valid_capacity),
        "Personen_MAK_Source": (bsgrd / 100).where(valid_capacity),
        "Sollarbeitszeit": hours,
        "Is_Vacant": ~valid_capacity,
    })
    allocated = apply_person_mak_allocation(allocation_input)
    pl["Ist-MAK (39h)"] = allocated.get("MAK_Reporting", pd.Series(0., index=pl.index))
    unknown_capacity = ~valid_capacity & ~pl["Zuordnung"].eq("Unbesetzt")
    pl.loc[unknown_capacity, "Ist-MAK (39h)"] = float("nan")
    pl["MAK ungeklärt"] = unknown_capacity
    for column in ["MitarbGruppenbez.", "Status kundenindividuell", "Eintritt", "Austritt", "BsGrd"]:
        if column in unique and column not in pl:
            pl[column] = pl["Personalnummer"].map(unique[column])
    pl["Eingabezeile"] = pl.index + 2
    plan_ids = normalize_persnr(pl.get("Planstellennr", pd.Series(None, index=pl.index, dtype=object)))
    pl["Planstellennummer fehlt"] = plan_ids.isna()
    pl["Planstellennummer mehrfach"] = plan_ids.notna() & plan_ids.duplicated(keep=False)
    references = set(pl["Personalnummer"].dropna())
    unique_ids = set(ma["PersNr"].dropna())
    assigned_ids = set(pl.loc[matched, "Personalnummer"])
    return {
        "positions": pl,
        "employees": ma,
        "unassigned_employees": ma[ma["PersNr"].notna() & ~ma["PersNr"].isin(references)].copy(),
        "ambiguous_employees": ma[ma["PersNr"].map(counts).gt(1)].copy(),
        "assigned_ids": assigned_ids,
        "employee_counts": {
            "Eingabezeilen": len(ma),
            "Ohne Personalnummer": int(ma["PersNr"].isna().sum()),
            "Zusätzliche Dublettenzeilen": int(ma["PersNr"].dropna().duplicated().sum()),
            "Ohne Planstellenzuordnung": len(unique_ids - references),
            "Mehrdeutig zugeordnet": len((unique_ids & references) - assigned_ids),
            "Eindeutig zugeordnete Personen": len(assigned_ids),
        },
    }


def stellenplan_matrix(positions: pd.DataFrame) -> pd.DataFrame:
    """Each source row appears in exactly one assignment and one capacity category."""
    groups = CAPACITY_GROUPS[:3] + ([CAPACITY_GROUPS[3]] if positions["Stellenart"].eq(CAPACITY_GROUPS[3]).any() else [])
    result = []
    for status in ["Stellen gesamt"] + ASSIGNMENT_GROUPS:
        selected = positions if status == "Stellen gesamt" else positions[positions["Zuordnung"].eq(status)]
        row = {"Stellenplan": status}
        for group in groups + ["Summe"]:
            part = selected if group == "Summe" else selected[selected["Stellenart"].eq(group)]
            capacity = f"{part['Ist-MAK (39h)'].sum():.2f}".replace(".", ",")
            missing = int(part["MAK ungeklärt"].sum())
            row[group] = f"{len(part)} · {capacity} MAK" + (f" + {missing} ungeklärt" if missing else "")
        result.append(row)
    return pd.DataFrame(result)
