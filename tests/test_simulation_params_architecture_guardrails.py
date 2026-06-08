from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIMULATION_PARAMS_KEY = "simulation_params"
HELPER_NAMES = ("get_simulation_params", "get_compact_plus_params")

INDEPENDENT_FORECAST_PAGES = [
    ROOT / "pages" / "3_📉_Prognose_Abgänge.py",
    ROOT / "pages" / "4_📈_Prognose_Zugänge.py",
    ROOT / "pages" / "5_🏢_Prognose_Hybrid.py",
]

ALLOWED_PRODUCTIVE_FILES = {
    (ROOT / "pages" / "7_⚡_Kompakt_plus_Simulation.py").resolve(),
    (ROOT / "pages" / "8_⚙️_Simulationsparameter.py").resolve(),
    (ROOT / "utils" / "simulation_params.py").resolve(),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_forecast_pages_do_not_depend_on_central_simulation_params() -> None:
    """Forecast pages remain independent analysis pages, not simulation_params consumers."""
    for path in INDEPENDENT_FORECAST_PAGES:
        source = _read(path)
        for helper_name in HELPER_NAMES:
            assert helper_name not in source, f"{path.name} must not import or call {helper_name}"
        assert SIMULATION_PARAMS_KEY not in source, f"{path.name} must not read st.session_state['simulation_params']"


def test_only_compact_plus_and_parameter_page_use_simulation_params_productively() -> None:
    """Only CompactPlus, the parameter page, and the central helper may reference simulation_params."""
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        resolved = path.resolve()
        if ".git" in path.parts or "__pycache__" in path.parts or "tests" in path.parts:
            continue
        if resolved in ALLOWED_PRODUCTIVE_FILES:
            continue

        source = _read(path)
        if SIMULATION_PARAMS_KEY in source or any(helper_name in source for helper_name in HELPER_NAMES):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_allowed_pages_and_helper_keep_expected_responsibilities() -> None:
    compact_plus_source = _read(ROOT / "pages" / "7_⚡_Kompakt_plus_Simulation.py")
    parameter_page_source = _read(ROOT / "pages" / "8_⚙️_Simulationsparameter.py")
    helper_source = _read(ROOT / "utils" / "simulation_params.py")

    assert "get_compact_plus_params" in compact_plus_source
    assert "st.date_input" in compact_plus_source
    assert "number_input" not in compact_plus_source
    assert "slider" not in compact_plus_source
    assert "selectbox" not in compact_plus_source
    assert "checkbox" not in compact_plus_source

    assert "get_simulation_params" in parameter_page_source
    assert "save_simulation_params" in parameter_page_source

    assert f'SESSION_KEY = "{SIMULATION_PARAMS_KEY}"' in helper_source
    assert "def get_compact_plus_params" in helper_source
    assert "def get_simulation_params" in helper_source
