import pandas as pd
import pytest

from utils.monte_carlo_annual_event_rows import (
    _with_person_dimensions, decode_annual_event_rows, encode_annual_event_rows,
)
from utils.org_simulation_events import METRICS, normalize_simulation_events, aggregate_simulation_events


def _case():
    context = {
        "run_signature": "annual-test",
        "cluster_mapping": {"oe_map": {"A": "C"}},
        "departure_events": pd.DataFrame({
            "event_date": [pd.Timestamp("2027-06-01")],
            "reason_code": ["QUIT"], "headcount_change": [-1],
            "mak_change": [-0.8], "org_unit": ["A"], "persnr": ["1"],
            "age": [64],
        }),
        "accession_events": pd.DataFrame({
            "date": [pd.Timestamp("2027-07-01")], "type": ["New_Hire"],
            "count": [1], "mak": [1.0], "org_unit": ["A"],
            "persnr": ["2"], "Geschlecht": ["W"],
        }),
    }
    events = normalize_simulation_events(context)
    rows = []
    for level in ("Organisationseinheit", "OE-Cluster"):
        frame = aggregate_simulation_events(events, dimension=level)
        for values in frame.itertuples(index=False, name=None):
            row = dict(zip(frame.columns, values))
            for metric in METRICS:
                rows.append((row["Jahr"], level, row[level], row["Kategorie"],
                             row["Ereignisart"], metric, row[metric]))
    return context, {"annual_values": rows}


def test_annual_event_rows_roundtrip_and_exact_aggregate():
    context, packet = _case()
    decoded = decode_annual_event_rows(encode_annual_event_rows(context, packet))
    assert len(decoded) == 2
    assert set(decoded["persnr"]) == {"1", "2"}
    assert decoded.loc[decoded["persnr"] == "1", "age"].iloc[0] == 64
    assert decoded.loc[decoded["persnr"] == "2", "Geschlecht"].iloc[0] == "W"
    assert aggregate_simulation_events(decoded).equals(
        aggregate_simulation_events(normalize_simulation_events(context)))


def test_annual_event_rows_reject_drift():
    context, packet = _case()
    packet["annual_values"][0] = (*packet["annual_values"][0][:-1], 999)
    with pytest.raises(ValueError, match="zertifizierten Lauf"):
        encode_annual_event_rows(context, packet)


def test_conversion_out_does_not_inherit_regular_target_jobfamily():
    events = pd.DataFrame({
        "Jahr": [2029, 2029],
        "event_date": pd.to_datetime(["2029-08-01", "2029-08-01"]),
        "Ereignisart": ["Azubi_Conversion_Out", "Azubi_Conversion_In"],
        "persnr": ["1", "2"],
        "Jobfamily": [pd.NA, pd.NA],
        "JF-Cluster": [pd.NA, pd.NA],
    })
    positions = [pd.DataFrame({
        "Stichtag": pd.to_datetime(["2029-12-31", "2029-12-31"]),
        "PersNr": ["1", "2"],
        "Jobfamily": ["Regular", "Regular"],
        "JF-Cluster": ["Operations", "Operations"],
    })]
    source = pd.DataFrame(columns=["PersNr"])
    result = _with_person_dimensions(events, positions, source, None)
    assert result.loc[0, "Jobfamily"] == "UNMAPPED"
    assert result.loc[0, "JF-Cluster"] == "UNMAPPED"
    assert result.loc[1, "Jobfamily"] == "Regular"
    assert result.loc[1, "JF-Cluster"] == "Operations"
