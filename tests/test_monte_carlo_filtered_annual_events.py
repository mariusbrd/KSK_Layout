import pandas as pd
import pytest

from utils.monte_carlo_annual_event_rows import VERSION, encode_annual_event_rows
from utils.monte_carlo_filtered_annual_events import filtered_annual_event_view
from utils.org_simulation_events import METRICS, aggregate_simulation_events, normalize_simulation_events


def _seed(gender):
    context = {
        "run_signature": "filtered-year",
        "cluster_mapping": {"oe_map": {"A": "C"}},
        "accession_events": pd.DataFrame({
            "date": [pd.Timestamp("2027-07-31")], "type": ["New_Hire"],
            "count": [1], "mak": [0.75], "org_unit": ["A"],
            "persnr": ["2"], "Geschlecht": [gender],
        }),
    }
    events = normalize_simulation_events(context)
    packet_rows = []
    for level in ("Organisationseinheit", "OE-Cluster"):
        table = aggregate_simulation_events(events, dimension=level)
        for values in table.itertuples(index=False, name=None):
            row = dict(zip(table.columns, values))
            packet_rows.extend((row["Jahr"], level, row[level], row["Kategorie"],
                                row["Ereignisart"], metric, row[metric]) for metric in METRICS)
    return events, encode_annual_event_rows(context, {"annual_values": packet_rows})


def test_filtered_annual_events_use_each_seed_and_zero_fill():
    reference, first = _seed("w")
    _, second = _seed("m")
    original = []
    for level, group in (("Organisationseinheit", "A"), ("OE-Cluster", "C")):
        for metric, value in (("Köpfe-Veränderung", 1), ("MAK-Veränderung", .75),
                              ("Abgänge", 0), ("Zugänge", 1)):
            original.append((2027, level, group, "New_Hire", metric, [value, value]))
    summary = {"events": reference,
               "annual_oe": aggregate_simulation_events(reference),
               "annual_cluster": aggregate_simulation_events(reference, dimension="OE-Cluster")}
    ensemble = {"count": 2, "annual_event_rows_version": VERSION,
                "annual_event_rows": [first, second],
                "statistics": {"annual_event_samples": original}}
    filtered, adjusted = filtered_annual_event_view(
        summary, ensemble, {"selected_genders": ["w"]})
    assert len(filtered["events"]) == 1
    assert filtered["annual_oe"]["Zugänge"].iloc[0] == 1
    assert next(values for year, level, group, kind, metric, values
                in adjusted["statistics"]["annual_event_samples"]
                if level == "Organisationseinheit" and metric == "Zugänge") == [1.0, 0.0]
    assert ensemble["statistics"]["annual_event_samples"] == original


def test_filtered_annual_events_reject_certified_drift():
    reference, blob = _seed("w")
    ensemble = {"count": 1, "annual_event_rows_version": VERSION,
                "annual_event_rows": [blob],
                "statistics": {"annual_event_samples": [
                    (2027, "Organisationseinheit", "A", "New_Hire", "Zugänge", [99])]}}
    with pytest.raises(ValueError, match="zertifizierten Lauf"):
        filtered_annual_event_view({"events": reference}, ensemble, {})
