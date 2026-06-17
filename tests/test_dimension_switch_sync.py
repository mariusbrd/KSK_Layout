"""Test: Dimensionswechsel ohne Clusterwechsel – ist der Sync-Guard ausreichend?

Szenarien:
  A. JF→OrgUnit ohne neue Cluster-Signatur: Sync wird NICHT ausgelöst,
     aber die Display-Fallback-Logik des Editors zeigt korrekte OrgUnit-Zeilen.
  B. _sync_params_matrix_dimensions mit geänderter Dimension baut Matrix korrekt um.
  C. Default-Zeile bleibt bei Dimensionswechsel erhalten.
  D. Widget-Key enthält keine Dimension → Rerun mit gleicher Signatur aber
     gewechselter Dimension liefert denselben Key → stale-delta-Risiko ist
     dokumentiert.
"""
from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from abgaenge.params import default_params


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _load_abgaenge_module():
    page_path = next((ROOT / "pages").glob("*_Prognose_Abg*.py"))
    spec = importlib.util.spec_from_file_location("abgaenge_page_dim_sync_test", page_path)
    mod = importlib.util.module_from_spec(spec)
    mod._UNIT_TESTING = True
    spec.loader.exec_module(mod)
    return mod


def _make_jf_params(jf_groups: list[str]) -> Dict[str, Any]:
    """Params mit gefüllter JF-ATZ-Matrix und JF-Kündigung-Matrix."""
    params = default_params()
    params["atz"]["atz_dimension"] = "JobFamily"
    params["atz"]["atz_matrix"] = {"Default": 0.05}
    for grp in jf_groups:
        params["atz"]["atz_matrix"][grp] = 0.08

    params["quit"]["quit_dimension"] = "JobFamily"
    params["quit"]["quit_matrix"] = {
        "alter_unter_30": {"Default": 0.12},
        "alter_30_45":    {"Default": 0.08},
        "alter_45_55":    {"Default": 0.05},
        "alter_55_plus":  {"Default": 0.02},
    }
    for cohort in params["quit"]["quit_matrix"]:
        for grp in jf_groups:
            params["quit"]["quit_matrix"][cohort][grp] = 0.09
    return params


# ---------------------------------------------------------------------------
# Szenario A: Sync-Guard reagiert NICHT auf reinen Dimensionswechsel
# ---------------------------------------------------------------------------

def test_sync_guard_does_not_fire_on_dimension_change_alone():
    """cluster_source_signature unveränderter → _last_params_sig == sig → kein Sync."""
    CLUSTER_SIG = "sha256-abc123"
    import streamlit as st

    st.session_state.clear()
    jf_groups = ["Retail", "IT", "Operations"]
    params = _make_jf_params(jf_groups)
    params["atz"]["atz_dimension"] = "JobFamily"

    # Simuliere: User hat vorher mit JF submitted
    st.session_state["abgaenge_params"] = deepcopy(params)
    st.session_state["abgaenge_params_cluster_signature"] = CLUSTER_SIG

    # Jetzt kommt ein rerun: User wechselt zu OrgUnit – Signatur gleich
    _last_params_sig = st.session_state.get("abgaenge_params_cluster_signature")
    assert _last_params_sig == CLUSTER_SIG  # Guard würde NICHT feuern

    # Lade params aus session state (so wie die Seite es tut)
    loaded_params = st.session_state.get("abgaenge_params", default_params())

    # Kein Sync wäre ausgelöst worden – Dimension im params ist noch "JobFamily"
    assert loaded_params["atz"]["atz_dimension"] == "JobFamily", (
        "Ohne Sync-Auslösung ist atz_dimension noch 'JobFamily' in session state"
    )

    # Die ATZ-Matrix hat noch JF-Schlüssel
    assert "Retail" in loaded_params["atz"]["atz_matrix"]
    assert "Berlin" not in loaded_params["atz"]["atz_matrix"]


# ---------------------------------------------------------------------------
# Szenario B: _sync_params_matrix_dimensions baut korrekt um
# ---------------------------------------------------------------------------

def test_sync_rebuilds_atz_matrix_for_new_dimension():
    """Wenn Sync manuell ausgelöst wird (z. B. mit erweiterter Signatur),
    muss die Matrix die OrgUnit-Gruppen enthalten, nicht die JF-Gruppen."""
    mod = _load_abgaenge_module()
    sync = mod._sync_params_matrix_dimensions

    jf_groups = ["Retail", "IT"]
    org_units = ["Berlin", "Hamburg", "Stuttgart"]
    params = _make_jf_params(jf_groups)

    # Simluiere: User hat Dimension auf OrgUnit gestellt (in params)
    params["atz"]["atz_dimension"] = "OrgUnit"
    params["quit"]["quit_dimension"] = "OrgUnit"

    result = sync(params, valid_jfs=jf_groups, active_org_units=org_units)

    atz_matrix = result["atz"]["atz_matrix"]

    # Default muss erhalten bleiben
    assert "Default" in atz_matrix, "Default-Zeile fehlt nach Sync"

    # Neue OrgUnit-Gruppen müssen vorhanden sein
    for ou in org_units:
        assert ou in atz_matrix, f"OrgUnit '{ou}' fehlt nach Sync"

    # JF-Gruppen dürfen NICHT mehr vorhanden sein (außer Default)
    for jf in jf_groups:
        assert jf not in atz_matrix, (
            f"JF-Gruppe '{jf}' ist noch in ATZ-Matrix – obwohl Dimension OrgUnit ist"
        )


def test_sync_rebuilds_quit_matrix_for_new_dimension():
    """Kündigung-Matrix: Analog zu ATZ – nach Sync nur noch OrgUnit-Schlüssel."""
    mod = _load_abgaenge_module()
    sync = mod._sync_params_matrix_dimensions

    jf_groups = ["Retail", "IT"]
    org_units = ["Berlin", "Hamburg"]
    params = _make_jf_params(jf_groups)
    params["atz"]["atz_dimension"] = "OrgUnit"
    params["quit"]["quit_dimension"] = "OrgUnit"

    result = sync(params, valid_jfs=jf_groups, active_org_units=org_units)

    for cohort in ["alter_unter_30", "alter_30_45", "alter_45_55", "alter_55_plus"]:
        cohort_matrix = result["quit"]["quit_matrix"][cohort]

        assert "Default" in cohort_matrix, f"Default fehlt in Kündigung-Cohort '{cohort}'"
        for ou in org_units:
            assert ou in cohort_matrix, f"OrgUnit '{ou}' fehlt in Cohort '{cohort}'"
        for jf in jf_groups:
            assert jf not in cohort_matrix, (
                f"JF-Gruppe '{jf}' in Cohort '{cohort}' – Dimension-Wechsel nicht korrekt"
            )


# ---------------------------------------------------------------------------
# Szenario C: Default-Wert bleibt korrekt erhalten
# ---------------------------------------------------------------------------

def test_default_row_preserved_and_value_carried_over():
    """Default-Zeile erhält den alten Default-Wert, nicht den Fallback."""
    mod = _load_abgaenge_module()
    sync = mod._sync_params_matrix_dimensions

    params = default_params()
    params["atz"]["atz_dimension"] = "OrgUnit"
    params["atz"]["atz_matrix"] = {"Default": 0.07, "OldJF": 0.09}

    result = sync(params, valid_jfs=[], active_org_units=["Berlin"])

    assert result["atz"]["atz_matrix"]["Default"] == 0.07, (
        "Default-Wert soll aus altem Matrix übernommen werden, nicht überschrieben"
    )
    assert result["atz"]["atz_matrix"]["Berlin"] == 0.07, (
        "Berlin (neu) soll den Default-Wert erhalten"
    )
    assert "OldJF" not in result["atz"]["atz_matrix"], (
        "OldJF-Gruppe soll nach Sync entfernt werden"
    )


# ---------------------------------------------------------------------------
# Szenario E: Display-Fallback – Editor zeigt IMMER die richtige Dimension
# ---------------------------------------------------------------------------

def test_display_fallback_shows_orgunit_rows_when_jf_matrix_stored():
    """Simuliert die Editor-Daten-Aufbau-Logik (Seite Zeilen 446-464).

    Nach Dimensionswechsel (JF→OrgUnit) ohne Cluster-Wechsel steht in
    params["atz"]["atz_matrix"] noch die JF-Matrix. Der Editor muss trotzdem
    korrekte OrgUnit-Zeilen mit Fallback-Werten (nicht JF-Zeilen) anzeigen.
    """
    from utils.matrix_helpers import migrate_to_percent

    # JF-Matrix in session state (noch nicht via sync/submit aktualisiert)
    jf_matrix = {"Default": 0.05, "Retail": 0.08, "IT": 0.03}
    current_atz_matrix_percent = migrate_to_percent(jf_matrix)

    # Neue Dimension wurde gewählt
    atz_dim = "OrgUnit"
    atz_unique_vals = ["Berlin", "Hamburg", "Stuttgart"]
    new_atz_base = 0.05  # used only for final fallback (Default * 100)

    atz_dim_items = ["Default"] + atz_unique_vals
    atz_editor_data = []
    for val in atz_dim_items:
        rate = current_atz_matrix_percent.get(str(val))
        if rate is None:
            rate = current_atz_matrix_percent.get("alter_55_plus", {}).get(str(val))
        if rate is None:
            rate = current_atz_matrix_percent.get("Default", new_atz_base * 100)
        atz_editor_data.append({"group": val, "rate": float(rate)})

    rows = {r["group"]: r["rate"] for r in atz_editor_data}

    # Default-Zeile korrekt
    assert "Default" in rows
    assert rows["Default"] == pytest.approx(5.0)

    # OrgUnit-Gruppen vorhanden, JF-Gruppen NICHT
    for ou in atz_unique_vals:
        assert ou in rows, f"OrgUnit '{ou}' fehlt im Editor"
        # Fallback: OrgUnit-Gruppen erhalten den Default-Wert (5.0%)
        assert rows[ou] == pytest.approx(5.0), (
            f"OrgUnit '{ou}' soll Default-Wert erhalten, nicht JF-spezifischen Wert"
        )

    for jf in ["Retail", "IT"]:
        assert jf not in rows, f"JF-Gruppe '{jf}' darf nicht im Editor sichtbar sein"


# ---------------------------------------------------------------------------
# Szenario D: Widget-Key enthält Dimension (verhindert stale-delta-Übertragung)
# ---------------------------------------------------------------------------

def test_widget_key_includes_dimension_to_prevent_stale_position_deltas():
    """Gleiche Cluster-Signatur + andere Dimension → anderer Widget-Key.

    Sicherstellung: Streamlit-positionsbasierte Edits aus einer Dimension
    können nicht auf eine andere Dimension übertragen werden, da der Key
    eine neue Session für den data_editor erzwingt.
    """
    mod = _load_abgaenge_module()
    sig = "sha256abc"

    key_jf = mod._cluster_widget_key("atz_matrix_editor_live", f"{sig}_JobFamily")
    key_ou = mod._cluster_widget_key("atz_matrix_editor_live", f"{sig}_OrgUnit")

    # Gleiche Sig + gleiche Dimension → gleicher Key (stabile Reruns)
    key_jf_again = mod._cluster_widget_key("atz_matrix_editor_live", f"{sig}_JobFamily")
    assert key_jf == key_jf_again

    # Gleiche Sig + andere Dimension → anderer Key (kein stale-delta-Transfer)
    assert key_jf != key_ou, (
        "ATZ-Editor-Key muss sich bei Dimensionswechsel ändern, "
        "sonst überträgt Streamlit positionale Edits auf falsche Zeilen"
    )

    # Gleiche Logik für Kündigung-Matrix
    key_quit_jf = mod._cluster_widget_key("quit_matrix_editor_live_fixed", f"{sig}_JobFamily")
    key_quit_ou = mod._cluster_widget_key("quit_matrix_editor_live_fixed", f"{sig}_OrgUnit")
    assert key_quit_jf != key_quit_ou, (
        "Kündigung-Editor-Key muss sich bei Dimensionswechsel ändern"
    )
