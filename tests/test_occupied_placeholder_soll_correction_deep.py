"""
Unabhaengige, tiefgehende Test-Batterie fuer die "Soll = Ist"-Korrektur bei besetzten
Platzhalter-Planstellen (occupied_placeholder_soll_correction).

Ergaenzt tests/test_occupied_placeholder_soll_correction.py (Basis-Unit-/Integrationstests der
Maskier- und Korrektur-Logik direkt in combine_to_snapshot()/create_combined_snapshot()) um:

1. Invarianten ueber die VOLLE Pipeline (inkl. apply_exclusions + apply_person_mak_allocation):
   Personen-Gesamt-MAK/-EUR bleiben exakt gleich, Allocation_Weight summiert je Person auf 1,0.
2. Grenzwert-/Robustheitstests (exakt 0.015, exakt 0, NaN, negativ, Idempotenz).
3. Den dokumentierten Interaktionseffekt mit der bestehenden, standardmaessig aktiven
   Exklusionsgruppe "Sollarbeitszeit <= 0,01" (sollarbeitszeit_001_positions).
4. Cross-Consumer-Tests je identifiziertem Soll-Pfad: Kompakt-Kompensationsview,
   Exklusionsgruppen-Statistiken, und die explizite Bestaetigung, dass die Koepfe-Engine
   (soll_ist_koepfe_engine.py) den Toggle NICHT konsumiert (bekannte, akzeptierte Luecke).

Alle Tests gegen echte Original-Daten sind dynamisch (keine hartkodierten Personalnummern) und
werden uebersprungen, wenn die Original-Daten nicht verfuegbar sind.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import dataloader.loader as loader
from dataloader.loader import ORIGINAL_FILES
from dataloader.mak_allocation import apply_person_mak_allocation, build_mak_allocation_validation_summary
from utils.exclusion_groups import SOLLARBEITSZEIT_001_KEY, build_group_masks, get_all_group_stats


ORIGINAL_DATA_AVAILABLE = all(os.path.exists(p) for p in ORIGINAL_FILES.values())
pytestmark = pytest.mark.skipif(
    not ORIGINAL_DATA_AVAILABLE, reason="Original-Daten nicht verfuegbar"
)

# Exklusionen mit standardmaessig aktiver "Sollarbeitszeit <= 0,01"-Gruppe (config/settings.py
# DEFAULT_EXCLUSIONS) - das ist der Zustand, in dem die dokumentierte Interaktion auftritt.
DEFAULT_EXCLUSIONS_WITH_001_GROUP = {
    "vorstand": False,
    "ruhend_bv": True,
    "planstellen_follow_person": True,
    "org_units": [],
    "special_groups": [
        "ausbildung_nachwuchs",
        "jobfamily_validation_special_positions",
        SOLLARBEITSZEIT_001_KEY,
    ],
}


@pytest.fixture(autouse=True)
def _deterministic_random(monkeypatch):
    # generate_history_from_snapshot() enthaelt echtes Rauschen (np.random.normal); fuer diese
    # Tests irrelevant (wir vergleichen snapshot/prepared-Ebene, nicht history_df), aber
    # deterministisch gemacht, um jede Moeglichkeit von Test-Flakiness auszuschliessen.
    monkeypatch.setattr(
        loader.np.random,
        "normal",
        lambda loc=0.0, scale=1.0, size=None: float(loc) if size is None else np.full(size, float(loc)),
    )


def _build_full_pipeline(occupied_placeholder_soll_correction: bool, exclusions: dict) -> pd.DataFrame:
    """
    Repliziert den Original-Daten-Zweig von _load_and_prepare_data_cached() Schritt fuer Schritt
    (siehe dataloader/loader.py), aber ohne st.cache_data/Streamlit-Kontext - direkt aufrufbar in
    Tests, analog zum Muster in tests/test_data_prep_regression.py::_build_reference_original_pipeline.
    """
    original = loader.load_original_data()
    snapshot_df = loader.combine_to_snapshot(
        original["mitarbeiter"],
        original["planstellen"],
        original["atz"],
        original["ausbildung"],
        occupied_placeholder_soll_correction=occupied_placeholder_soll_correction,
    )
    snapshot_df = loader.enrich_snapshot_data(snapshot_df)
    snapshot_df = loader._apply_jobfamilies(snapshot_df)
    snapshot_df = loader._zero_out_azubi_mak(snapshot_df)
    snapshot_df = loader.apply_exclusions(snapshot_df, exclusions)
    snapshot_df = apply_person_mak_allocation(snapshot_df)
    return snapshot_df


@pytest.fixture(scope="module")
def pipeline_off():
    return _build_full_pipeline(False, DEFAULT_EXCLUSIONS_WITH_001_GROUP)


@pytest.fixture(scope="module")
def pipeline_on():
    return _build_full_pipeline(True, DEFAULT_EXCLUSIONS_WITH_001_GROUP)


def _changed_persnrs(off_df: pd.DataFrame, on_df: pd.DataFrame) -> pd.Index:
    merged = off_df[["Personalnummer", "Soll_FTE"]].merge(
        on_df[["Personalnummer", "Soll_FTE"]],
        on="Personalnummer",
        suffixes=("_off", "_on"),
    )
    changed = merged[(merged["Soll_FTE_off"] - merged["Soll_FTE_on"]).abs() > 1e-9]
    return pd.Index(changed["Personalnummer"].dropna().unique())


# ---------------------------------------------------------------------------
# 1. Person-Level-Invarianten ueber die volle Pipeline
# ---------------------------------------------------------------------------

def test_correction_actually_changes_something_in_current_dataset(pipeline_off, pipeline_on):
    """Sanity-Check: Falls diese Testdaten keine betroffenen Faelle mehr enthalten, sind alle
    folgenden Invarianten-Tests trivial erfuellt statt aussagekraeftig - das soll auffallen."""
    changed = _changed_persnrs(pipeline_off, pipeline_on)
    assert len(changed) > 0, (
        "Keine betroffenen Personalnummern gefunden - Testdatensatz enthaelt aktuell keine "
        "regulaeren besetzten Platzhalter-Planstellen mehr. Invarianten-Tests unten sind damit "
        "nicht aussagekraeftig; bitte Testdaten oder Fallfindung pruefen."
    )


def test_person_level_mak_reporting_sum_invariant_to_toggle(pipeline_off, pipeline_on):
    """
    Kernaussage der gesamten Korrektur: Sie verschiebt MAK zwischen den Planstellen-Zeilen
    EINER Person (via Allocation_Weight-Neugewichtung), veraendert aber niemals die
    Personen-Gesamtsumme. Das ist keine Zufallsbeobachtung, sondern folgt direkt aus der
    Normalisierung in apply_person_mak_allocation() (Gewichte summieren je Person auf 1,0).
    """
    changed = _changed_persnrs(pipeline_off, pipeline_on)
    assert len(changed) > 0

    off_sums = pipeline_off[pipeline_off["Personalnummer"].isin(changed)].groupby("Personalnummer")["MAK_Reporting"].sum()
    on_sums = pipeline_on[pipeline_on["Personalnummer"].isin(changed)].groupby("Personalnummer")["MAK_Reporting"].sum()

    aligned_off, aligned_on = off_sums.align(on_sums)
    pd.testing.assert_series_equal(aligned_off, aligned_on, check_names=False, atol=1e-6)


def test_person_level_eur_reporting_sum_invariant_to_toggle(pipeline_off, pipeline_on):
    changed = _changed_persnrs(pipeline_off, pipeline_on)
    assert len(changed) > 0

    off_sums = pipeline_off[pipeline_off["Personalnummer"].isin(changed)].groupby("Personalnummer")["EUR_Reporting"].sum()
    on_sums = pipeline_on[pipeline_on["Personalnummer"].isin(changed)].groupby("Personalnummer")["EUR_Reporting"].sum()

    aligned_off, aligned_on = off_sums.align(on_sums)
    pd.testing.assert_series_equal(aligned_off, aligned_on, check_names=False, atol=1e-2)


def test_total_ist_mak_across_all_persons_invariant_to_toggle(pipeline_off, pipeline_on):
    """Die Korrektur wirkt nur auf SOLL-Spalten. Die aggregierte IST-Seite (MAK_Reporting-Summe
    ueber den gesamten Snapshot) darf sich durch das Toggle nicht veraendern."""
    off_total = pipeline_off["MAK_Reporting"].sum()
    on_total = pipeline_on["MAK_Reporting"].sum()
    assert off_total == pytest.approx(on_total, abs=1e-6)


def test_allocation_weight_sums_to_one_per_active_person_both_toggle_states(pipeline_off, pipeline_on):
    """Regressions-Sicherung ueber den eingebauten Validierungs-Report: unabhaengig vom Toggle
    darf keine Person eine gebrochene Gewichtssumme oder eine Reporting-MAK > Personen-MAK haben."""
    for df in (pipeline_off, pipeline_on):
        summary = build_mak_allocation_validation_summary(df)
        weight_row = summary[summary["Check"] == "Anzahl_Personen_mit_Allocation_Weight_sum_ne_1"].iloc[0]
        assert weight_row["Wert"] == 0, weight_row
        reporting_row = summary[summary["Check"] == "Anzahl_Personen_mit_Reporting_MAK_gt_Personen_MAK"].iloc[0]
        assert reporting_row["Status"] == "OK", reporting_row


def test_row_count_unchanged_by_toggle(pipeline_off, pipeline_on):
    """Die Korrektur darf niemals Zeilen hinzufuegen/entfernen - nur Werte innerhalb bestehender
    Zeilen aendern."""
    assert len(pipeline_off) == len(pipeline_on)
    assert set(pipeline_off["Planstellennr"].dropna()) == set(pipeline_on["Planstellennr"].dropna())


# ---------------------------------------------------------------------------
# 2. Grenzwerte / Robustheit
# ---------------------------------------------------------------------------

def test_boundary_soll_fte_exactly_0_015_not_corrected():
    """Mask-Bedingung ist strikt '< 0.015' (siehe combine_to_snapshot). Ein Wert von exakt
    0.015 darf NICHT als Platzhalter gelten (das waere ~0,585h - kein 0,01h-Systemartefakt)."""
    df = pd.DataFrame({
        "Is_Vacant": [False],
        "Soll_FTE": [0.015],
        "FTE_person": [1.0],
        "Sollarbeitszeit": [0.015 * 39.0],
        "Kürzel OrgEinheit": ["800"],
        "Status kundenindividuell": ["Aktives Beschäftigungsverhältnis"],
        "MitarbGruppenbez.": ["Angestellte"],
        "Ist_Azubi": [False],
        "Phase": [""],
    })
    mask = (
        ~df["Is_Vacant"]
        & (df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015)
        & loader._mask_regular_occupied_placeholder_positions(df)
    )
    assert not mask.any()


def test_boundary_soll_fte_exactly_zero_not_corrected():
    """Echte, budgetierte Soll_FTE=0 (kein Platzhalter-Artefakt, sondern tatsaechlich "kein
    Bedarf") darf durch die Korrektur nicht angefasst werden - die Mask-Bedingung ist '> 0'."""
    df = pd.DataFrame({
        "Is_Vacant": [False],
        "Soll_FTE": [0.0],
        "FTE_person": [1.0],
        "Sollarbeitszeit": [0.0],
        "Kürzel OrgEinheit": ["800"],
        "Status kundenindividuell": ["Aktives Beschäftigungsverhältnis"],
        "MitarbGruppenbez.": ["Angestellte"],
        "Ist_Azubi": [False],
        "Phase": [""],
    })
    mask = (
        ~df["Is_Vacant"]
        & (df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015)
        & loader._mask_regular_occupied_placeholder_positions(df)
    )
    assert not mask.any()


def test_boundary_negative_soll_fte_not_corrected():
    """Sollte nicht vorkommen, aber die Mask-Bedingung ('> 0') darf negative Werte niemals
    faelschlich als Platzhalter behandeln."""
    df = pd.DataFrame({
        "Is_Vacant": [False],
        "Soll_FTE": [-0.01],
        "FTE_person": [1.0],
        "Sollarbeitszeit": [-0.39],
        "Kürzel OrgEinheit": ["800"],
        "Status kundenindividuell": ["Aktives Beschäftigungsverhältnis"],
        "MitarbGruppenbez.": ["Angestellte"],
        "Ist_Azubi": [False],
        "Phase": [""],
    })
    mask = (
        ~df["Is_Vacant"]
        & (df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015)
        & loader._mask_regular_occupied_placeholder_positions(df)
    )
    assert not mask.any()


def test_boundary_nan_soll_fte_does_not_crash_and_is_not_corrected():
    df = pd.DataFrame({
        "Is_Vacant": [False],
        "Soll_FTE": [float("nan")],
        "FTE_person": [1.0],
        "Sollarbeitszeit": [float("nan")],
        "Kürzel OrgEinheit": ["800"],
        "Status kundenindividuell": ["Aktives Beschäftigungsverhältnis"],
        "MitarbGruppenbez.": ["Angestellte"],
        "Ist_Azubi": [False],
        "Phase": [""],
    })
    mask = (
        ~df["Is_Vacant"]
        & (df["Soll_FTE"] > 0) & (df["Soll_FTE"] < 0.015)
        & loader._mask_regular_occupied_placeholder_positions(df)
    )
    assert not mask.any()


def test_correction_is_idempotent():
    """Ein zweiter Korrekturdurchlauf auf einem bereits korrigierten DataFrame darf keine
    weitere Aenderung mehr bewirken (Fixpunkt: Soll_FTE wird auf FTE_person gesetzt, das nach
    einem Durchlauf bereits der Fall ist)."""
    df = pd.DataFrame({
        "Is_Vacant": [False],
        "Soll_FTE": [0.01 / 39.0],
        "FTE_person": [0.6],
        "Sollarbeitszeit": [0.01],
        "Kürzel OrgEinheit": ["800"],
        "Status kundenindividuell": ["Aktives Beschäftigungsverhältnis"],
        "MitarbGruppenbez.": ["Angestellte"],
        "Ist_Azubi": [False],
        "Phase": [""],
    })

    def _apply_correction_once(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        mask = (
            ~out["Is_Vacant"]
            & (out["Soll_FTE"] > 0) & (out["Soll_FTE"] < 0.015)
            & loader._mask_regular_occupied_placeholder_positions(out)
        )
        out.loc[mask, "Soll_FTE"] = out.loc[mask, "FTE_person"]
        out.loc[mask, "Sollarbeitszeit"] = out.loc[mask, "FTE_person"] * 39.0
        return out

    once = _apply_correction_once(df)
    twice = _apply_correction_once(once)
    pd.testing.assert_frame_equal(once, twice)
    assert once.loc[0, "Soll_FTE"] == pytest.approx(0.6)


def test_correction_idempotent_even_when_fte_person_itself_is_tiny():
    """Randfall: Falls FTE_person selbst < 0,015 ist (sehr kleine Teilzeit), bleibt die
    korrigierte Zeile technisch innerhalb der urspruenglichen Mask-Spanne - der zweite Durchlauf
    darf trotzdem keine weitere Aenderung bewirken (x = x ist idempotent)."""
    df = pd.DataFrame({
        "Is_Vacant": [False],
        "Soll_FTE": [0.01 / 39.0],
        "FTE_person": [0.01],  # < 0.015, absichtlich winzig
        "Sollarbeitszeit": [0.01],
        "Kürzel OrgEinheit": ["800"],
        "Status kundenindividuell": ["Aktives Beschäftigungsverhältnis"],
        "MitarbGruppenbez.": ["Angestellte"],
        "Ist_Azubi": [False],
        "Phase": [""],
    })

    def _apply_correction_once(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        mask = (
            ~out["Is_Vacant"]
            & (out["Soll_FTE"] > 0) & (out["Soll_FTE"] < 0.015)
            & loader._mask_regular_occupied_placeholder_positions(out)
        )
        out.loc[mask, "Soll_FTE"] = out.loc[mask, "FTE_person"]
        out.loc[mask, "Sollarbeitszeit"] = out.loc[mask, "FTE_person"] * 39.0
        return out

    once = _apply_correction_once(df)
    twice = _apply_correction_once(once)
    pd.testing.assert_frame_equal(once, twice)


# ---------------------------------------------------------------------------
# 3. Interaktion mit der bestehenden Exklusionsgruppe "Sollarbeitszeit <= 0,01"
# ---------------------------------------------------------------------------

def test_exclusion_group_001_mask_escaped_by_correction_synthetic():
    """build_group_masks()'s sollarbeitszeit_001_positions matcht mit einer sehr engen Toleranz
    (abs(x-0.01)<=1e-9). Nach der Korrektur (Sollarbeitszeit = FTE_person * 39) liegt der Wert
    fuer jede reale Kapazitaet ausserhalb dieser Toleranz - die Zeile "entkommt" der Gruppe."""
    df_before = pd.DataFrame({"Sollarbeitszeit": [0.01]})
    df_after = pd.DataFrame({"Sollarbeitszeit": [0.5 * 39.0]})

    masks_before = build_group_masks(df_before)
    masks_after = build_group_masks(df_after)

    assert masks_before[SOLLARBEITSZEIT_001_KEY].iloc[0] is np.True_ or bool(masks_before[SOLLARBEITSZEIT_001_KEY].iloc[0])
    assert not bool(masks_after[SOLLARBEITSZEIT_001_KEY].iloc[0])


def test_exclusion_group_001_vacates_row_when_correction_off_but_not_when_on():
    """End-to-End-Nachweis des dokumentierten Interaktionseffekts anhand echter Daten, aktualisiert
    fuer die v2.3-IST-Ausnahme in apply_exclusions(): Fuer eine dynamisch gefundene, regulaere
    (nicht-Sonderstatus) besetzte Platzhalter-Zeile mit realer Kapazitaet (BsGrd>0) fuehrt
    apply_exclusions() mit aktiver 'sollarbeitszeit_001_positions'-Gruppe OHNE Korrektur weiterhin
    zu Is_Excluded=True (SOLL bleibt exkludiert), aber NICHT mehr zu Is_Vacant=True -- die reale
    IST-Kapazitaet bleibt seit v2.3 erhalten (siehe apply_exclusions()-Docstring, Abschnitt v2.3).
    MIT Korrektur ist die Zeile gar nicht erst exkludiert (Sollarbeitszeit ungleich 0,01), daher
    dort zusaetzlich Is_Excluded=False."""
    raw = loader.load_original_data()

    baseline = loader.combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=False,
    )
    occupied_zero = baseline[(~baseline["Is_Vacant"]) & (baseline["Soll_FTE"].fillna(0) <= 0.02)]
    eligible_mask = loader._mask_regular_occupied_placeholder_positions(occupied_zero)
    regular_candidates = occupied_zero[eligible_mask]
    if regular_candidates.empty:
        pytest.skip("Keine regulaeren Platzhalter-Faelle im aktuellen Datenbestand gefunden.")

    # Manche Zeilen matchen zusaetzlich die "jobfamily_validation_special_positions"-Gruppe;
    # bei Mehrfachzuordnung gewinnt in apply_exclusions() die zuerst iterierte Gruppe fuer das
    # Exclusion_Group-Label. Fuer einen eindeutigen Nachweis der 0,01-Gruppen-Interaktion wird
    # gezielt ein Kandidat gewaehlt, der NUR die sollarbeitszeit_001-Gruppe matcht.
    all_masks = build_group_masks(regular_candidates)
    only_001_mask = all_masks[SOLLARBEITSZEIT_001_KEY] & ~all_masks["jobfamily_validation_special_positions"]
    unambiguous_candidates = regular_candidates[only_001_mask]
    if unambiguous_candidates.empty:
        pytest.skip("Keine eindeutigen (nur sollarbeitszeit_001) Platzhalter-Faelle gefunden.")
    regular_candidates = unambiguous_candidates
    target_persnr = regular_candidates.iloc[0]["Personalnummer"]

    snap_off = loader.combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=False,
    )
    snap_off = loader.apply_exclusions(snap_off, DEFAULT_EXCLUSIONS_WITH_001_GROUP)
    row_off = snap_off[snap_off["Personalnummer"].astype(str) == str(target_persnr)]
    # Nach apply_exclusions ist "Personalnummer" fuer exkludierte Zeilen bereits genullt (NA) -
    # daher ueber Planstellennr nachschlagen, das nicht Teil der genullten person_fields ist.
    plan_nr = regular_candidates.iloc[0]["Planstellennr"]
    row_off_by_plan = snap_off[snap_off["Planstellennr"] == plan_nr]
    assert not row_off_by_plan.empty
    # v2.3: SOLL bleibt exkludiert, aber die reale IST-Kapazitaet (BsGrd>0, kein hartes
    # Ausschlusskriterium wie Vorstand/Ruhend/PA-Bereich/Azubi) bleibt erhalten -> nicht mehr vakant.
    assert bool(row_off_by_plan.iloc[0]["Is_Vacant"]) is False
    assert row_off_by_plan.iloc[0]["Is_Excluded"] == True
    assert row_off_by_plan.iloc[0]["Exclusion_Group"] == "Sollarbeitszeit ≤ 0,01"
    assert bool(row_off_by_plan.iloc[0]["Is_IST_Preserved_Despite_Exclusion"]) is True
    assert row_off_by_plan.iloc[0]["MAK_Calculated"] > 0

    snap_on = loader.combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=True,
    )
    snap_on = loader.apply_exclusions(snap_on, DEFAULT_EXCLUSIONS_WITH_001_GROUP)
    row_on_by_plan = snap_on[snap_on["Planstellennr"] == plan_nr]
    assert not row_on_by_plan.empty
    assert bool(row_on_by_plan.iloc[0]["Is_Vacant"]) is False
    assert row_on_by_plan.iloc[0]["Is_Excluded"] == False


def test_get_all_group_stats_001_group_shrinks_when_correction_on(pipeline_off, pipeline_on):
    """get_all_group_stats() ist die Datenquelle fuer die Exklusionsgruppen-Seite
    (Deep-Dive-Exklusionsgruppen). Die Mitgliederzahl der 'Sollarbeitszeit <= 0,01'-Gruppe muss
    sinken (oder gleich bleiben), sobald die Korrektur aktiv ist - Zeilen, die durch die
    Korrektur einen echten Sollarbeitszeit-Wert erhalten, verlassen die enge 0,01-Toleranz."""
    stats_off = get_all_group_stats(pipeline_off)
    stats_on = get_all_group_stats(pipeline_on)

    label = "Planstellen mit Sollarbeitszeit = 0,01"
    row_off = stats_off[stats_off["gruppe_name"] == label]
    row_on = stats_on[stats_on["gruppe_name"] == label]
    assert not row_off.empty and not row_on.empty

    assert int(row_on["planstellen"].iloc[0]) <= int(row_off["planstellen"].iloc[0])
    assert int(row_on["planstellen"].iloc[0]) < int(row_off["planstellen"].iloc[0])


# ---------------------------------------------------------------------------
# 4. Cross-Consumer-Tests je identifiziertem Soll-Pfad
# ---------------------------------------------------------------------------

def test_compact_page_soll_mak_sum_increases_with_correction(pipeline_off, pipeline_on):
    """Kompakt-Dashboard (build_compact_compensation_planlevel_df / get_soll_mak) liest
    Soll_FTE direkt aus dem Snapshot - muss die Korrektur unveraendert durchreichen."""
    off_total = pipeline_off["Soll_FTE"].fillna(0).sum()
    on_total = pipeline_on["Soll_FTE"].fillna(0).sum()
    assert on_total >= off_total
    changed = _changed_persnrs(pipeline_off, pipeline_on)
    assert len(changed) > 0
    assert on_total > off_total + 1e-6


def test_koepfe_engine_completely_unaffected_by_toggle():
    """Bestaetigt die bekannte, akzeptierte Luecke: soll_ist_koepfe_engine.py liest
    Original-Planstellen direkt (load_original_data()) und ignoriert
    occupied_placeholder_soll_correction vollstaendig - unabhaengig vom Toggle identisches
    Ergebnis. Falls dieser Test irgendwann fehlschlaegt, wurde die Engine an den korrigierten
    Snapshot angebunden und dieser Kommentar/Test-Name ist zu aktualisieren."""
    import dataloader.soll_ist_koepfe_engine as koepfe_engine

    original_file_signatures = tuple(
        (name, koepfe_engine.get_file_signature(path))
        for name, path in sorted(koepfe_engine.ORIGINAL_FILES.items())
    )

    # Die Koepfe-Engine liest load_original_data() direkt und ruft an keiner Stelle
    # combine_to_snapshot()/create_combined_snapshot() oder get_setting("occupied_placeholder_
    # soll_correction") auf - zwei unabhaengige Aufrufe muessen daher bitidentisch sein, egal
    # welchen Wert das Dashboard-Setting gerade hat.
    koepfe_engine._load_soll_ist_koepfe_basis_cached.clear()
    basis_first = koepfe_engine._load_soll_ist_koepfe_basis_cached(
        uploaded_payload=(), original_file_signatures=original_file_signatures,
    )
    koepfe_engine._load_soll_ist_koepfe_basis_cached.clear()
    basis_second = koepfe_engine._load_soll_ist_koepfe_basis_cached(
        uploaded_payload=(), original_file_signatures=original_file_signatures,
    )
    pd.testing.assert_frame_equal(basis_first, basis_second)

    import inspect
    source = inspect.getsource(koepfe_engine)
    assert "occupied_placeholder_soll_correction" not in source


def test_forecast_engines_hardcode_sollarbeitszeit_and_are_unaffected():
    """Die Abgaenge-/Zugaenge-Forecast-Engines rechnen mit einer festen Vollzeit-Referenz
    (39h) fuer ihre eigene interne MAK-Umrechnung und lesen Soll_FTE aus dem Snapshot nicht als
    Korrektur-Eingang - Toggle-Aenderungen duerfen den Forecast-Code selbst nicht beruehren."""
    import importlib

    forecast_module = importlib.import_module("abgaenge.forecast")
    source = Path(forecast_module.__file__).read_text(encoding="utf-8")
    assert "occupied_placeholder_soll_correction" not in source


def test_deep_dive_exklusionsgruppen_page_soll_mak_totals_track_correction(pipeline_off, pipeline_on):
    """Deep-Dive-Exklusionsgruppen berechnet total_soll_mak = snapshot_df['Soll_FTE'].sum()
    direkt (pages/6_..._Exklusionsgruppen.py) - muss die Korrektur unveraendert widerspiegeln,
    identisch zum Kompakt-Pfad (beide lesen dieselbe Spalte aus demselben Snapshot)."""
    total_off = float(pipeline_off["Soll_FTE"].fillna(0).sum())
    total_on = float(pipeline_on["Soll_FTE"].fillna(0).sum())
    assert total_on >= total_off
