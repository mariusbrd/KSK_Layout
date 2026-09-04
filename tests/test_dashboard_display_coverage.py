from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
INVENTORY_DIR = WORKSPACE_ROOT / "docs" / "inventory"

VISIBLE_PAGES = {
    "Kompakt",
    "Organisationseinheiten-Analyse",
    "Jobgruppen-Analyse",
    "Prognose: Abgänge",
    "Simulationsparameter",
    "Kompakt plus Simulation",
    "Organisationseinheiten-Analyse Simulation",
    "Jobgruppen-Analyse Simulation",
    "Einstellungen",
    "Exklusionsgruppen",
}

ANALYSIS_SURFACES = {
    "Organisationseinheiten-Analyse",
    "Organisationseinheiten-Analyse Simulation",
    "Jobgruppen-Analyse",
    "Jobgruppen-Analyse Simulation",
}

SIMULATION_DELEGATES = {
    "Organisationseinheiten-Analyse Simulation": "Organisationseinheiten-Analyse",
    "Jobgruppen-Analyse Simulation": "Jobgruppen-Analyse",
}


def _read_inventory(name: str) -> pd.DataFrame:
    path = INVENTORY_DIR / name
    assert path.exists(), f"Missing generated inventory file: {path}"
    return pd.read_csv(path, encoding="utf-8-sig")


def test_dashboard_inventory_lists_all_visible_navigation_pages():
    pages = _read_inventory("pages_inventory.csv")
    visible = set(pages.loc[pages["Visible in Navigation"] == "Ja", "Page Title"])

    assert visible == VISIBLE_PAGES


def test_analysis_pages_have_display_inventory_rows():
    charts = _read_inventory("charts_inventory.csv")
    tables = _read_inventory("tables_inventory.csv")
    kpis = _read_inventory("kpis_inventory.csv")
    downloads = _read_inventory("downloads_inventory.csv")
    filters = _read_inventory("filters_inventory.csv")

    for page in ANALYSIS_SURFACES:
        assert not charts[charts["Page"] == page].empty, f"{page} has no chart inventory"
        assert not tables[tables["Page"] == page].empty, f"{page} has no table inventory"
        assert not kpis[kpis["Page"] == page].empty, f"{page} has no KPI inventory"
        assert not downloads[downloads["Page"] == page].empty, f"{page} has no download inventory"
        assert not filters[filters["Page / Global"] == page].empty, f"{page} has no filter inventory"


def test_simulation_analysis_inventory_documents_delegated_renderers():
    for filename, page_col in [
        ("charts_inventory.csv", "Page"),
        ("tables_inventory.csv", "Page"),
        ("kpis_inventory.csv", "Page"),
        ("downloads_inventory.csv", "Page"),
        ("filters_inventory.csv", "Page / Global"),
    ]:
        inventory = _read_inventory(filename)
        assert "Delegated From Page" in inventory.columns

        for simulation_page, source_page in SIMULATION_DELEGATES.items():
            delegated = inventory[
                (inventory[page_col] == simulation_page)
                & (inventory["Delegated From Page"] == source_page)
            ]
            assert not delegated.empty, (
                f"{filename} does not expose delegated rows for "
                f"{simulation_page} from {source_page}"
            )


def test_inventory_validation_summary_confirms_current_static_scan():
    validation = (INVENTORY_DIR / "validation_summary.txt").read_text(encoding="utf-8")

    assert "Visible pages included: 10" in validation
    assert "All plotly_chart calls included or exceeded: True" in validation
    assert "Issues has no-critical marker: True" in validation
