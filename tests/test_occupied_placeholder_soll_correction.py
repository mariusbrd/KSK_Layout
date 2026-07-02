"""
Tests fuer die optionale "Soll = Ist"-Korrektur bei besetzten Platzhalter-Planstellen
(Sollarbeitszeit ~ 0.01) ausserhalb bekannter Exklusions-/Sonderstatus-Gruppen.

Siehe Einstellungen > Sonderfaelle > "Soll = Ist setzen fuer besetzte Platzhalter-Planstellen".
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import dataloader.loader as loader
from dataloader.loader import (
    ORIGINAL_FILES,
    _mask_regular_occupied_placeholder_positions,
    combine_to_snapshot,
    create_combined_snapshot,
    load_original_data,
)


# ---------------------------------------------------------------------------
# 1. Reine Unit-Tests der Maskier-Logik (synthetisch, deterministisch)
# ---------------------------------------------------------------------------

def _synthetic_positions_df() -> pd.DataFrame:
    return pd.DataFrame([
        {  # regulaer -> Zielgruppe (True)
            "Kürzel OrgEinheit": "800", "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Angestellte", "Ist_Azubi": False, "Phase": "",
        },
        {  # Exklusions-OE (PA Elternzeit) -> False
            "Kürzel OrgEinheit": "9971", "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Angestellte", "Ist_Azubi": False, "Phase": "",
        },
        {  # Ruhendes Beschaeftigungsverhaeltnis -> False
            "Kürzel OrgEinheit": "800", "Status kundenindividuell": "Ruhendes Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Angestellte", "Ist_Azubi": False, "Phase": "",
        },
        {  # Vorstand -> False
            "Kürzel OrgEinheit": "800", "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Vorstand", "Ist_Azubi": False, "Phase": "",
        },
        {  # Azubi -> False
            "Kürzel OrgEinheit": "800", "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Angestellte", "Ist_Azubi": True, "Phase": "",
        },
        {  # ATZ-Freizeitphase -> False
            "Kürzel OrgEinheit": "800", "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "MitarbGruppenbez.": "Angestellte", "Ist_Azubi": False, "Phase": "FR",
        },
    ])


def test_mask_regular_occupied_placeholder_positions_covers_all_exclusion_reasons():
    df = _synthetic_positions_df()
    mask = _mask_regular_occupied_placeholder_positions(df)
    assert mask.tolist() == [True, False, False, False, False, False]


def test_mask_regular_occupied_placeholder_positions_99xx_catch_all():
    df = pd.DataFrame([{
        "Kürzel OrgEinheit": "9988", "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
        "MitarbGruppenbez.": "Angestellte", "Ist_Azubi": False, "Phase": "",
    }])
    assert _mask_regular_occupied_placeholder_positions(df).tolist() == [False]


def test_mask_regular_occupied_placeholder_positions_missing_columns_defaults_to_regular():
    # Fehlende Spalten duerfen die Funktion nicht zum Absturz bringen; fehlende Signale
    # gelten als "kein Sonderstatus" (konservativ in Richtung "regulaer").
    df = pd.DataFrame([{"Kürzel OrgEinheit": "800"}])
    assert _mask_regular_occupied_placeholder_positions(df).tolist() == [True]


# ---------------------------------------------------------------------------
# 2. Integrationstests gegen echte Original-Daten (dynamisch ermittelte Faelle,
#    keine hartkodierten Personalnummern - robust gegen Datenaenderungen)
# ---------------------------------------------------------------------------

ORIGINAL_DATA_AVAILABLE = all(os.path.exists(p) for p in ORIGINAL_FILES.values())
pytestmark = pytest.mark.skipif(
    not ORIGINAL_DATA_AVAILABLE, reason="Original-Daten nicht verfuegbar"
)


def _load_raw():
    return load_original_data()


def _find_dynamic_target_rows(raw):
    """Findet je einen realen Beispiel-Fall (regulaer / exklusions-behaftet), besetzt mit Soll~0."""
    baseline = combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=False,
    )
    occupied_zero = baseline[(~baseline["Is_Vacant"]) & (baseline["Soll_FTE"].fillna(0) <= 0.02)]
    eligible_mask = _mask_regular_occupied_placeholder_positions(occupied_zero)
    regular_candidates = occupied_zero[eligible_mask]
    excluded_candidates = occupied_zero[~eligible_mask]
    vacant_placeholder = baseline[baseline["Is_Vacant"] & (baseline["Soll_FTE"].fillna(0) <= 0.02)]
    return regular_candidates, excluded_candidates, vacant_placeholder


def test_combine_to_snapshot_corrects_regular_occupied_placeholder_when_enabled():
    raw = _load_raw()
    regular_candidates, _, _ = _find_dynamic_target_rows(raw)
    if regular_candidates.empty:
        pytest.skip("Keine regulaeren Platzhalter-Faelle im aktuellen Datenbestand gefunden.")
    target_persnr = regular_candidates.iloc[0]["Personalnummer"]

    corrected = combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=True,
    )
    row = corrected[corrected["Personalnummer"] == target_persnr].iloc[0]
    assert row["Soll_FTE"] == pytest.approx(row["FTE_person"])
    assert row["Soll_FTE"] > 0
    assert row["Sollarbeitszeit"] == pytest.approx(row["FTE_person"] * 39.0)


def test_combine_to_snapshot_leaves_excluded_occupied_placeholder_at_zero_even_when_enabled():
    raw = _load_raw()
    _, excluded_candidates, _ = _find_dynamic_target_rows(raw)
    if excluded_candidates.empty:
        pytest.skip("Keine ausgeschlossenen Platzhalter-Faelle im aktuellen Datenbestand gefunden.")
    target_persnr = excluded_candidates.iloc[0]["Personalnummer"]

    corrected = combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=True,
    )
    row = corrected[corrected["Personalnummer"] == target_persnr].iloc[0]
    assert row["Soll_FTE"] == 0.0


def test_combine_to_snapshot_does_not_touch_vacant_placeholder_rows():
    raw = _load_raw()
    _, _, vacant_placeholder = _find_dynamic_target_rows(raw)
    if vacant_placeholder.empty:
        pytest.skip("Keine vakanten Platzhalter-Faelle im aktuellen Datenbestand gefunden.")

    corrected = combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=True,
    )
    for plan_nr in vacant_placeholder["Planstellennr"].dropna().unique():
        rows = corrected[corrected["Planstellennr"] == plan_nr]
        assert (rows["Soll_FTE"] == 0.0).all()


def test_combine_to_snapshot_explicit_false_matches_current_behaviour():
    raw = _load_raw()
    regular_candidates, _, _ = _find_dynamic_target_rows(raw)
    if regular_candidates.empty:
        pytest.skip("Keine regulaeren Platzhalter-Faelle im aktuellen Datenbestand gefunden.")
    target_persnr = regular_candidates.iloc[0]["Personalnummer"]

    result = combine_to_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=False,
    )
    row = result[result["Personalnummer"] == target_persnr].iloc[0]
    assert row["Soll_FTE"] == 0.0


def test_combine_to_snapshot_none_param_falls_back_to_get_setting(monkeypatch):
    raw = _load_raw()
    regular_candidates, _, _ = _find_dynamic_target_rows(raw)
    if regular_candidates.empty:
        pytest.skip("Keine regulaeren Platzhalter-Faelle im aktuellen Datenbestand gefunden.")
    target_persnr = regular_candidates.iloc[0]["Personalnummer"]

    monkeypatch.setattr(
        loader,
        "get_setting",
        lambda key, default=None: True if key == "occupied_placeholder_soll_correction" else default,
    )

    result = combine_to_snapshot(raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"])
    row = result[result["Personalnummer"] == target_persnr].iloc[0]
    assert row["Soll_FTE"] == pytest.approx(row["FTE_person"])


def test_create_combined_snapshot_corrects_regular_occupied_placeholder_when_enabled():
    raw = _load_raw()
    regular_candidates, _, _ = _find_dynamic_target_rows(raw)
    if regular_candidates.empty:
        pytest.skip("Keine regulaeren Platzhalter-Faelle im aktuellen Datenbestand gefunden.")
    target_persnr = regular_candidates.iloc[0]["Personalnummer"]

    corrected = create_combined_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=True,
    )
    matches = corrected[corrected["Personalnummer"] == target_persnr]
    assert not matches.empty, "Zeile muss im Upload-Pfad-Snapshot auffindbar sein"
    row = matches.iloc[0]
    assert row["Soll_FTE"] == pytest.approx(row["FTE_person"])


def test_create_combined_snapshot_default_matches_current_behaviour():
    raw = _load_raw()
    regular_candidates, _, _ = _find_dynamic_target_rows(raw)
    if regular_candidates.empty:
        pytest.skip("Keine regulaeren Platzhalter-Faelle im aktuellen Datenbestand gefunden.")
    target_persnr = regular_candidates.iloc[0]["Personalnummer"]

    result = create_combined_snapshot(
        raw["mitarbeiter"], raw["planstellen"], raw["atz"], raw["ausbildung"],
        occupied_placeholder_soll_correction=False,
    )
    matches = result[result["Personalnummer"] == target_persnr]
    assert not matches.empty
    assert matches.iloc[0]["Soll_FTE"] == 0.0
