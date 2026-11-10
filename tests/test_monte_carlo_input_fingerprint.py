"""Workers must agree on the same complete input, not merely cluster files."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from utils.monte_carlo_input_fingerprint import (
    fingerprint_simulation_inputs, frame_fingerprint,
)


def _inputs():
    snapshot = pd.DataFrame({
        "PersNr": ["001", "002"],
        "MAK": [0.6, 0.4],
        "Details": [["A", "B"], {"phase": 2, "active": True}],
    }, index=pd.Index([7, 9], name="position"))
    atz = pd.DataFrame({"PersNr": ["001"], "Beginn": [pd.Timestamp("2026-01-01")]})
    return snapshot, atz


def test_fingerprint_detects_data_order_schema_and_auxiliary_changes():
    snapshot, atz = _inputs()
    baseline = fingerprint_simulation_inputs(
        snapshot, atz, salary_automation={"enabled": True},
        cost_lookup={"A": 100}, cluster_mapping={"A": "B"})
    assert baseline == fingerprint_simulation_inputs(
        snapshot.copy(), atz.copy(), salary_automation={"enabled": True},
        cost_lookup={"A": 100}, cluster_mapping={"A": "B"})
    changed = snapshot.copy(deep=True)
    changed.loc[7, "MAK"] = 0.7
    assert frame_fingerprint(changed) != frame_fingerprint(snapshot)
    assert frame_fingerprint(snapshot.iloc[::-1]) != frame_fingerprint(snapshot)
    assert frame_fingerprint(snapshot.reset_index(drop=True)) != frame_fingerprint(snapshot)
    assert frame_fingerprint(snapshot.astype({"MAK": "object"})) != frame_fingerprint(snapshot)
    assert fingerprint_simulation_inputs(snapshot, atz, cost_lookup={"A": 101}) != (
        fingerprint_simulation_inputs(snapshot, atz, cost_lookup={"A": 100}))
    altered_atz = atz.copy()
    altered_atz.loc[0, "Beginn"] = pd.Timestamp("2026-02-01")
    assert fingerprint_simulation_inputs(snapshot, altered_atz) != (
        fingerprint_simulation_inputs(snapshot, atz))
    assert baseline != fingerprint_simulation_inputs(
        snapshot, atz, salary_automation={"enabled": True},
        cost_lookup={"A": 100}, cluster_mapping={"A": "C"})


def test_mapping_order_and_nested_numpy_values_are_process_stable():
    snapshot, atz = _inputs()
    left = fingerprint_simulation_inputs(
        snapshot, atz, cost_lookup={"a": np.array([1, 2]), "b": 3})
    right = fingerprint_simulation_inputs(
        snapshot, atz, cost_lookup={"b": 3, "a": np.array([1, 2])})
    assert left == right
    code = (
        "from test_monte_carlo_input_fingerprint import _inputs; "
        "from utils.monte_carlo_input_fingerprint import fingerprint_simulation_inputs; "
        "import numpy as np; "
        "s,a=_inputs(); "
        "print(fingerprint_simulation_inputs(s,a,cost_lookup={'a':np.array([1,2]),'b':3}))"
    )
    env_path = str(Path(__file__).resolve().parent)
    output = subprocess.run([sys.executable, "-c",
                             f"import sys; sys.path.insert(0, {env_path!r}); {code}"],
                            capture_output=True, text=True, check=True)
    assert output.stdout.strip() == left


def test_unsupported_objects_fail_instead_of_using_unstable_repr():
    snapshot, atz = _inputs()
    with pytest.raises(TypeError, match="Unsupported fingerprint value"):
        fingerprint_simulation_inputs(snapshot, atz, cost_lookup={"x": object()})


def test_current_productive_snapshot_and_atz_have_stable_fingerprint():
    from benchmark_page_rerun_paths import _freeze_context, _reset_state
    from dataloader import loader

    _freeze_context()
    _reset_state()
    root = Path(__file__).resolve().parents[1]
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(root), None, None, None)
    first = fingerprint_simulation_inputs(snapshot, atz)
    assert first == fingerprint_simulation_inputs(snapshot.copy(), atz.copy())
    assert len(first) == 64


def test_current_productive_input_fingerprint_matches_fresh_process():
    from benchmark_page_rerun_paths import _freeze_context, _reset_state
    from dataloader import loader

    _freeze_context()
    _reset_state()
    root = Path(__file__).resolve().parents[1]
    snapshot, _, _, _ = loader.load_and_prepare_data()
    atz = loader.load_atz_data_cached(str(root), None, None, None)
    expected = fingerprint_simulation_inputs(snapshot, atz)
    code = (
        "import logging,sys; from pathlib import Path; "
        "logging.disable(logging.CRITICAL); "
        f"sys.path.insert(0,{str(root / 'tests')!r}); "
        "from benchmark_page_rerun_paths import _freeze_context,_reset_state; "
        "from dataloader import loader; "
        "from utils.monte_carlo_input_fingerprint import fingerprint_simulation_inputs; "
        "_freeze_context(); _reset_state(); "
        "snapshot,_,_,_=loader.load_and_prepare_data(); "
        f"atz=loader.load_atz_data_cached({str(root)!r},None,None,None); "
        "print(fingerprint_simulation_inputs(snapshot,atz))"
    )
    output = subprocess.run([sys.executable, "-c", code], cwd=root,
                            capture_output=True, text=True, check=True)
    assert output.stdout.strip() == expected
