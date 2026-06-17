"""
analyse_planstellen_duplikate.py
================================
Prüft, ob mehrfach vorkommende Planstellennr im Snapshot konsistente
SOLL-relevante Attribute aufweisen.

Fragestellung:
  Darf die aktuelle Deduplizierung ("erste Zeile zählt, weitere SOLL=0")
  bedenkenlos angewendet werden — oder weichen SOLL-Werte / Dimensionen
  innerhalb derselben Planstellennr voneinander ab?

Run:
    py -X utf8 KSK_Layout/tests/analyse_planstellen_duplikate.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: F401  (benoetigt fuer Loader-Caching)

from dataloader.loader import load_original_data, combine_to_snapshot
from dataloader.mak_allocation import apply_person_mak_allocation
from kpi_reference import get_current_stichtag

SEP  = "=" * 70
SEP2 = "-" * 50

SOLL_ATTRS = [
    "Soll_FTE",
    "Soll_Cost_Year",
    "Bewertung Tarifgruppe",
    "Text Gehaltsband",
    "Organisationseinheit",
    "Kürzel OrgEinheit",
    "OE-Cluster",
    "Jobfamily",
    "Planebene",
    "Planstelle",
    "Planstellenkürzel",
]


def _nunique_str(series: pd.Series) -> int:
    """Anzahl eindeutiger nicht-leerer String-Repräsentationen."""
    return series.fillna("").astype(str).str.strip().nunique()


def main() -> int:
    print(SEP)
    print("  DUPLIKAT-KONSISTENZPRÜFUNG: SOLL-Attribute je Planstellennr")
    print(SEP)

    # ── Daten laden ──────────────────────────────────────────────────────────
    print("\nLade Snapshot ...")
    raw      = load_original_data()
    stichtag = get_current_stichtag()
    snap     = combine_to_snapshot(
        mitarbeiter=raw["mitarbeiter"],
        planstellen=raw["planstellen"],
        atz=raw["atz"],
        ausbildung=raw["ausbildung"],
        stichtag=stichtag,
    )
    if "MAK_Reporting" not in snap.columns:
        snap = apply_person_mak_allocation(snap)

    print(f"Snapshot: {len(snap)} Zeilen, Stichtag {stichtag.date()}")

    # ── Planstellennr normalisieren ───────────────────────────────────────────
    if "Planstellennr" not in snap.columns:
        print("\nFEHLER: Spalte 'Planstellennr' nicht im Snapshot vorhanden.")
        return 1

    snap["_pl_norm"] = snap["Planstellennr"].fillna("").astype(str).str.strip()
    has_pl  = snap["_pl_norm"].ne("")
    snap_pl = snap[has_pl].copy()

    # ── Nur tatsächlich vorhandene SOLL-Attribute prüfen ─────────────────────
    avail_attrs = [c for c in SOLL_ATTRS if c in snap_pl.columns]
    missing     = [c for c in SOLL_ATTRS if c not in snap_pl.columns]

    if missing:
        print(f"\nHinweis: Folgende Spalten fehlen im Snapshot (werden übersprungen):")
        for m in missing:
            print(f"  - {m}")

    # ── Duplikat-Analyse ──────────────────────────────────────────────────────
    counts = snap_pl.groupby("_pl_norm")["_pl_norm"].transform("count")
    dup_pl = snap_pl[counts > 1].copy()

    n_total_pl   = snap_pl["_pl_norm"].nunique()
    n_dup_pl     = dup_pl["_pl_norm"].nunique()
    n_dup_rows   = len(dup_pl)

    print(f"\nEindeutige Planstellennr gesamt: {n_total_pl}")
    print(f"Planstellennr mit > 1 Zeile:     {n_dup_pl}")
    print(f"Betroffene Zeilen:               {n_dup_rows}")

    if n_dup_pl == 0:
        print("\nKeine doppelten Planstellennr — Deduplizierung nicht notwendig.")
        return 0

    # ── Konsistenz je Planstellennr prüfen ────────────────────────────────────
    print(f"\n{SEP2}")
    print("  KONSISTENZPRÜFUNG je Planstellennr")
    print(SEP2)

    inconsistent: dict[str, list[str]] = {}  # pl_nr -> Liste inkonsistenter Spalten

    grp = dup_pl.groupby("_pl_norm")
    for pl_nr, gdf in grp:
        bad_cols = [
            col for col in avail_attrs
            if _nunique_str(gdf[col]) > 1
        ]
        if bad_cols:
            inconsistent[pl_nr] = bad_cols

    n_incons = len(inconsistent)
    n_consit = n_dup_pl - n_incons

    print(f"\nPlanstellennr mit konsistenten SOLL-Attributen: {n_consit}")
    print(f"Planstellennr mit Abweichungen:                 {n_incons}")

    if not inconsistent:
        print("\n" + SEP)
        print("  ERGEBNIS: Alle SOLL-Attribute sind je Planstellennr IDENTISCH.")
        print("  Die Deduplizierung ('erste Zeile zählt') ist fachlich akzeptabel.")
        print(SEP)
        _print_dup_summary(dup_pl, avail_attrs, grp)
        return 0

    # ── Detailausgabe der Abweichungen ────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  DETAIL: Planstellennr mit Abweichungen")
    print(SEP2)

    id_cols = [c for c in ["Planstellennr", "PersNr", "Personalnummer", "Is_Vacant",
                            "Planebene", "Organisationseinheit"] if c in dup_pl.columns]

    for pl_nr, bad_cols in sorted(inconsistent.items()):
        gdf = grp.get_group(pl_nr)
        print(f"\nPlanstellennr: {pl_nr!r}  ({len(gdf)} Zeilen)")
        print(f"  Inkonsistente Spalten: {', '.join(bad_cols)}")
        show_cols = list(dict.fromkeys(id_cols + bad_cols))
        show_cols = [c for c in show_cols if c in gdf.columns]
        print(gdf[show_cols].fillna("").to_string(index=False))

    # ── Risikobewertung ───────────────────────────────────────────────────────
    print(f"\n{SEP2}")
    print("  RISIKOBEWERTUNG")
    print(SEP2)

    dim_cols   = {"Organisationseinheit", "Kürzel OrgEinheit", "OE-Cluster",
                  "Jobfamily", "Planebene", "Planstelle", "Planstellenkürzel"}
    value_cols = {"Soll_FTE", "Soll_Cost_Year", "Bewertung Tarifgruppe", "Text Gehaltsband"}

    dim_issues   = {pl: cols for pl, cols in inconsistent.items() if set(cols) & dim_cols}
    value_issues = {pl: cols for pl, cols in inconsistent.items() if set(cols) & value_cols}

    if dim_issues:
        print(f"\n  RISIKO HOCH — Dimensionsattribute variieren ({len(dim_issues)} Planstellen):")
        print("  Die aktuelle Methode ordnet SOLL-Werte der Dimension der *ersten* Zeile zu.")
        print("  Das kann dazu führen, dass SOLL falsch in Planebene / OE / Jobfamily erscheint.")
        for pl, cols in sorted(dim_issues.items()):
            dim_bad = [c for c in cols if c in dim_cols]
            print(f"    {pl!r}: {', '.join(dim_bad)}")
    else:
        print("\n  Dimensionsattribute (Planebene, OE, Jobfamily usw.): KONSISTENT")
        print("  -> SOLL-Zuordnung zu Aggregationsdimensionen ist korrekt.")

    if value_issues:
        print(f"\n  HINWEIS — Wertattribute variieren ({len(value_issues)} Planstellen):")
        print("  Soll_FTE / Soll_Cost_Year / Tarifgruppe unterscheiden sich je Zeile.")
        print("  Die erste Zeile bestimmt den SOLL-Wert — prüfen ob das fachlich korrekt ist.")
        for pl, cols in sorted(value_issues.items()):
            val_bad = [c for c in cols if c in value_cols]
            print(f"    {pl!r}: {', '.join(val_bad)}")
    else:
        print("\n  Wertattribute (Soll_FTE, Soll_Cost_Year, Tarifgruppe): KONSISTENT")
        print("  -> SOLL-Betragsberechnung ist nicht durch Reihenfolge beeinflusst.")

    # ── Zusammenfassung Duplikat-Muster (immer) ───────────────────────────────
    _print_dup_summary(dup_pl, avail_attrs, grp)

    print(f"\n{SEP}")
    if dim_issues:
        print("  GESAMTERGEBNIS: Abweichungen in Dimensionsattributen gefunden.")
        print("  Empfehlung: Planstellen-Stammdaten vor Deduplizierung bereinigen")
        print("  oder SOLL auf Planstellen-Ebene (nicht Snapshot-Ebene) auswerten.")
    else:
        print("  GESAMTERGEBNIS: Keine Dimensionsabweichungen.")
        print("  Die Deduplizierung ('erste Zeile zählt, weitere SOLL=0') ist")
        print("  fachlich akzeptabel — SOLL-Zuordnung zu Dimensionen ist stabil.")
    print(SEP)

    return 1 if dim_issues else 0


def _print_dup_summary(dup_pl: pd.DataFrame, avail_attrs: list[str], grp) -> None:
    """Kurze Übersicht: wie viele Zeilen je doppelter Planstelle, welche Muster."""
    print(f"\n{SEP2}")
    print("  MUSTER: Zeilenanzahl je doppelter Planstellennr")
    print(SEP2)
    counts_series = dup_pl.groupby("_pl_norm").size().sort_values(ascending=False)
    tbl = counts_series.reset_index()
    tbl.columns = ["Planstellennr", "Anzahl_Zeilen"]

    # Is_Vacant-Verteilung hinzufügen falls verfügbar
    if "Is_Vacant" in dup_pl.columns:
        vac_counts = dup_pl.groupby("_pl_norm")["Is_Vacant"].apply(
            lambda s: f"{s.sum()}V/{(~s).sum()}B"  # V=vakant, B=besetzt
        )
        tbl["Besetzt/Vakant"] = tbl["Planstellennr"].map(vac_counts)

    print(tbl.to_string(index=False))

    # Welches Attribute variieren am häufigsten?
    attr_variance_count = {}
    for pl_nr, gdf in grp:
        for col in avail_attrs:
            if _nunique_str(gdf[col]) > 1:
                attr_variance_count[col] = attr_variance_count.get(col, 0) + 1

    if attr_variance_count:
        print(f"\n  Häufigkeit von Abweichungen je Attribut (über alle Duplikat-Planstellen):")
        for col, cnt in sorted(attr_variance_count.items(), key=lambda x: -x[1]):
            print(f"    {col}: {cnt}x")


if __name__ == "__main__":
    sys.exit(main())
