from pathlib import Path
import sys

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def test_missing_synthetic_data_is_generated_in_memory(monkeypatch, tmp_path):
    import dataloader.loader as loader
    import dataloader.synthetic as synthetic

    missing_file = tmp_path / "sample_data" / "hr_data.xlsx"
    generated = {
        "snapshot_detail": pd.DataFrame(
            {
                "PersNr": ["000001"],
                "GebDatum": pd.to_datetime(["1990-01-01"]),
                "Eintritt": pd.to_datetime(["2020-01-01"]),
                "Austritt": pd.to_datetime([None]),
                "BsGrd": [100],
                "ist_atz_fr": [False],
                "MitarbGruppenbez.": ["Aktive"],
                "Phase": [""],
                "Ist_Azubi": [False],
            }
        ),
        "history_cube": pd.DataFrame({"PersNr": ["000001"]}),
        "org_structure": pd.DataFrame({"Organisationseinheit": ["OE1"]}),
    }

    monkeypatch.setattr(st, "session_state", {"suppress_data_status_messages": True})
    monkeypatch.setattr(synthetic, "generate_synthetic_data", lambda: generated)
    loader.load_hr_data.clear()

    result = loader.load_hr_data(filepath=str(missing_file), auto_generate=True)

    assert result is generated
    assert missing_file.exists() is False
