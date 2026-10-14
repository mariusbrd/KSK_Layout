"""Pure aggregation of saved simulation events; no forecast or state replay."""
from __future__ import annotations

from copy import deepcopy
import numpy as np
import pandas as pd

UNASSIGNED = "Nicht zugeordnet"
RECONCILIATION_VERSION = 4
DEPARTURES = {"QUIT", "RETIREMENT", "ATZ_END", "Azubi_Exit"}
ENTRIES = {"New_Hire", "Trainee_Hire", "Azubi_Hire"}
INTERNAL = {"Azubi_Conversion_In", "Azubi_Conversion_Out"}
CAPACITY = {"ATZ_AR_TO_FR", "RUHEND_START", "RUHEND_RETURN"}
DIMENSIONS = ("Organisationseinheit", "OE-Cluster")
METRICS = ("Köpfe-Veränderung", "MAK-Veränderung", "Abgänge", "Zugänge")


def normalize_simulation_events(context: dict, *, expected_run_signature: str | None = None) -> pd.DataFrame:
    """Preserve every event row and sign; cluster mapping always comes from this run."""
    if not context.get("run_signature"):
        raise ValueError("Keine Laufkennung für die Ereignisauswertung vorhanden.")
    if expected_run_signature is not None and context["run_signature"] != expected_run_signature:
        raise ValueError("Ereignisse und Simulation gehören zu unterschiedlichen Läufen.")
    mapping = context.get("cluster_mapping", {}).get("oe_map", {})
    mapping = {str(k).strip(): str(v).strip() if pd.notna(v) and str(v).strip() else UNASSIGNED
               for k, v in mapping.items()}
    parts = []
    for key, date_col, type_col, hc_col, mak_col in (
        ("departure_events", "event_date", "reason_code", "headcount_change", "mak_change"),
        ("accession_events", "date", "type", "count", "mak"),
    ):
        raw = context.get(key)
        if raw is None or raw.empty:
            continue
        required = {date_col, type_col, hc_col, mak_col}
        if not required.issubset(raw.columns):
            raise ValueError(f"Unvollständige Ereignisdaten: {key}.")
        work = raw.copy(deep=True).reset_index(drop=True)
        if key == "departure_events" and context.get("baseline_snapshot") is not None:
            from utils.org_simulation_reconciliation import complete_departure_org_assignments
            work = complete_departure_org_assignments(work, context["baseline_snapshot"])
        work["event_date"] = pd.to_datetime(work[date_col], errors="coerce")
        if work["event_date"].isna().any():
            raise ValueError(f"Ungültiges Ereignisdatum: {key}.")
        work["Ereignisart"] = work[type_col].fillna("Unbekannt").astype(str)
        work["Ereignisquelle"] = key
        org = work.get("Organisationseinheit", pd.Series(pd.NA, index=work.index))
        if "org_unit" in work:
            org = org.where(org.notna() & org.astype(str).str.strip().ne(""), work["org_unit"])
        work["Organisationseinheit"] = org.fillna(UNASSIGNED).astype(str).str.strip().replace("", UNASSIGNED)
        work["OE-Cluster"] = work["Organisationseinheit"].map(mapping).fillna(UNASSIGNED)
        for original, output in ((hc_col, "Köpfe-Veränderung"), (mak_col, "MAK-Veränderung")):
            work[output] = pd.to_numeric(work[original], errors="coerce")
            if not np.isfinite(work[output].to_numpy(dtype=float)).all():
                raise ValueError(f"Ungültige Ereigniswerte: {key}/{original}.")
        kind = work["Ereignisart"]
        work["Kategorie"] = "Sonstige Veränderungen"
        for codes, label in ((DEPARTURES, "Personalabgang"), (ENTRIES, "Externer Zugang"),
                             (INTERNAL, "Interner Wechsel"), (CAPACITY, "Kapazitätsänderung")):
            work.loc[kind.isin(codes), "Kategorie"] = label
        # Unknown types remain visible but never silently count as company leavers.
        work["Abgänge"] = (-work["Köpfe-Veränderung"]).clip(lower=0).where(kind.isin(DEPARTURES), 0)
        work["Zugänge"] = work["Köpfe-Veränderung"].clip(lower=0).where(kind.isin(ENTRIES), 0)
        work["Jahr"] = work["event_date"].dt.year
        parts.append(work)
    columns = ["event_date", "Jahr", *DIMENSIONS, "Ereignisart", "Kategorie", "Ereignisquelle", *METRICS]
    return pd.concat(parts, ignore_index=True, sort=False) if parts else pd.DataFrame(columns=columns)


def aggregate_simulation_events(events: pd.DataFrame, *, dimension: str = "Organisationseinheit",
                                by_year: bool = True) -> pd.DataFrame:
    """Sum event changes, not year-end populations or reporting MAK stocks."""
    if dimension not in DIMENSIONS:
        raise ValueError("Ungültige Organisationsebene.")
    groups = (["Jahr"] if by_year else []) + [dimension, "Kategorie", "Ereignisart"]
    if events.empty:
        return pd.DataFrame(columns=[*groups, *METRICS])
    return events.groupby(groups, dropna=False, sort=True)[list(METRICS)].sum().reset_index()


def build_simulation_event_summary(context: dict, *, expected_run_signature: str) -> dict:
    """Materialize all views once from the same saved run, before publishing state."""
    events = normalize_simulation_events(context, expected_run_signature=expected_run_signature)
    summary = {"run_signature": context["run_signature"],
               "reconciliation_version": RECONCILIATION_VERSION,
               "cluster_mapping": deepcopy(context.get("cluster_mapping", {})),
               "metadata": deepcopy(context.get("metadata", {})), "events": events}
    for level, dimension in (("oe", "Organisationseinheit"), ("cluster", "OE-Cluster")):
        summary[f"annual_{level}"] = aggregate_simulation_events(events, dimension=dimension)
        summary[f"total_{level}"] = aggregate_simulation_events(events, dimension=dimension, by_year=False)
    from utils.org_simulation_reconciliation import build_departure_stock_bridge
    summary["stock_reconciliation"] = build_departure_stock_bridge(
        context, events, expected_run_signature=expected_run_signature)
    columns = ["PersNr", "Organisationseinheit", "Planstelle", "Planstellennr", "Is_Vacant", "Is_Excluded",
               "Exclusion_Group", "MAK_Reporting", "MAK_Calculated", "Personen_MAK_Source", "Personen_MAK",
               "BsGrd_Source", "BsGrd", "Allocation_Weight", "Soll_FTE", "Status kundenindividuell",
               "Ist_Azubi", "ATZ_Status", "ist_atz_fr"]
    summary["stock_basis"] = {key: context[key][[c for c in columns if c in context[key]]].copy(deep=True)
                              for key in ("baseline_snapshot", "final_snapshot") if context.get(key) is not None}
    return summary
