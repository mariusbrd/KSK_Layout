from pathlib import Path
import random
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dataloader.soll_ist_koepfe_engine import (
    build_soll_ist_koepfe_result,
    load_soll_ist_koepfe_basis,
)
from config.settings import TARIFF_GROUPS
from utils.settings_loader import get_setting


def _reference_result_from_basis(basis: pd.DataFrame, exclusions: dict, use_max_eg: bool):
    work = basis.copy()

    org = (
        work["Kürzel OrgEinheit"]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
    )

    ex_units = exclusions.get("org_units", []) or []
    if ex_units:
        explicit = [unit for unit in ex_units if unit != "99XX"]
        mask_oe = org.isin(explicit)
        if "99XX" in ex_units:
            mask_oe = mask_oe | (org.str.startswith("99") & ~org.isin(set(explicit)))
        work = work.loc[~mask_oe].copy()

    person_mask = pd.Series(False, index=work.index)
    if exclusions.get("vorstand"):
        person_mask |= work["MitarbGruppenbez."].astype(str).str.strip().eq("Vorstand")
    if exclusions.get("ruhend_bv"):
        person_mask |= work["Status kundenindividuell"].astype(str).str.strip().eq(
            "Ruhendes Beschäftigungsverhältnis"
        )
    work = work.loc[~person_mask].copy()

    work["_Soll_EG"] = work["_Soll_EG_I"] if use_max_eg else work["_Soll_EG_H"]

    regular_df = work.loc[~work["__is_001__"]].copy()
    technical_df = work.loc[work["__is_001__"]].copy()
    invalid = {"", "NAN", "NONE"}
    no_soll_df = regular_df.loc[regular_df["_Soll_EG"].isin(invalid)].copy()
    matrix_df = regular_df.loc[~regular_df["_Soll_EG"].isin(invalid)].copy()
    no_soll_occupied = no_soll_df.loc[
        ~no_soll_df["_Ist_EG"].isin(["Unbesetzt", "Nicht gefunden"])
    ]
    no_soll_eg_row = no_soll_occupied["_Ist_EG"].value_counts()

    pivot = matrix_df.groupby(["_Soll_EG", "_Ist_EG"]).size().unstack(fill_value=0)
    eg_order = {group: idx for idx, group in enumerate(TARIFF_GROUPS)}
    ist_eg_cols = sorted(
        [col for col in pivot.columns if col not in ("Unbesetzt", "Nicht gefunden")],
        key=lambda group: eg_order.get(group, 999),
    )
    special_cols = [col for col in ("Unbesetzt", "Nicht gefunden") if col in pivot.columns]
    if not pivot.empty:
        pivot = pivot[ist_eg_cols + special_cols]
        pivot["Gesamt"] = pivot.sum(axis=1)
        soll_order = sorted(pivot.index.tolist(), key=lambda group: eg_order.get(group, 999))
        pivot = pivot.loc[soll_order]
    else:
        pivot["Gesamt"] = pd.Series(dtype=int)
        soll_order = []
    pivot.index.name = "Soll-EG"

    summary = {
        "rows_total_after_exclusions": int(len(work)),
        "regular_total": int(len(regular_df)),
        "regular_occupied": int(regular_df["__has_pnr__"].sum()),
        "regular_vacant": int((~regular_df["__has_pnr__"]).sum()),
        "regular_no_soll_eg_total": int(len(no_soll_df)),
        "regular_no_soll_eg_occupied": int(no_soll_occupied.shape[0]),
        "matrix_total": int(len(matrix_df)),
        "matrix_occupied": int((~matrix_df["_Ist_EG"].isin(["Unbesetzt", "Nicht gefunden"])).sum()),
        "matrix_unbesetzt": int((matrix_df["_Ist_EG"] == "Unbesetzt").sum()),
        "matrix_not_found": int((matrix_df["_Ist_EG"] == "Nicht gefunden").sum()),
        "technical_total": int(len(technical_df)),
        "technical_9xxx_total": int((technical_df["__is_9xxx__"]).sum()),
        "technical_9xxx_occupied": int((technical_df["__is_9xxx__"] & technical_df["__has_pnr__"]).sum()),
        "technical_non9xxx_total": int((~technical_df["__is_9xxx__"]).sum()),
        "technical_non9xxx_occupied": int((~technical_df["__is_9xxx__"] & technical_df["__has_pnr__"]).sum()),
        "technical_9910_total": int((org.loc[technical_df.index] == "9910").sum()) if not technical_df.empty else 0,
    }
    return summary, pivot, no_soll_eg_row


def test_soll_ist_koepfe_engine_reproduces_raw_counts_without_exclusions():
    result = build_soll_ist_koepfe_result(exclusions={})
    summary = result["summary"]

    assert summary["rows_total_after_exclusions"] == 1728
    assert summary["regular_total"] == 991
    assert summary["regular_occupied"] == 903
    assert summary["regular_vacant"] == 88
    assert summary["technical_total"] == 737
    assert summary["technical_9xxx_total"] == 393
    assert summary["technical_9xxx_occupied"] == 257
    assert summary["technical_non9xxx_total"] == 344
    assert summary["technical_non9xxx_occupied"] == 87
    assert summary["technical_9910_total"] == 208
    assert summary["regular_no_soll_eg_total"] == 6
    assert summary["regular_no_soll_eg_occupied"] == 6
    assert summary["matrix_total"] == 985
    assert summary["matrix_occupied"] == 897
    assert summary["matrix_unbesetzt"] == 88
    assert summary["matrix_not_found"] == 0


def test_soll_ist_koepfe_engine_applies_variant_b_exclusions():
    exclusions = {
        "org_units": ["99XX", "9975"],
        "vorstand": True,
        "ruhend_bv": True,
    }
    result = build_soll_ist_koepfe_result(exclusions=exclusions)
    summary = result["summary"]

    basis = load_soll_ist_koepfe_basis().copy()
    org = (
        basis["Kürzel OrgEinheit"]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
    )
    mask_oe = org.str.startswith("99") | org.eq("9975")
    mask_person = (
        basis["MitarbGruppenbez."].astype(str).str.strip().eq("Vorstand")
        | basis["Status kundenindividuell"].astype(str).str.strip().eq("Ruhendes Beschäftigungsverhältnis")
    )
    expected = basis.loc[~(mask_oe | mask_person)].copy()
    expected_regular = expected.loc[~expected["__is_001__"]]
    expected_technical = expected.loc[expected["__is_001__"]]

    assert summary["rows_total_after_exclusions"] == len(expected)
    assert summary["regular_total"] == len(expected_regular)
    assert summary["regular_occupied"] == int(expected_regular["__has_pnr__"].sum())
    assert summary["technical_total"] == len(expected_technical)
    assert summary["technical_non9xxx_occupied"] == int(
        ((~expected_technical["__is_9xxx__"]) & expected_technical["__has_pnr__"]).sum()
    )

    # Mit 99XX-Exklusion muss 9910 in Variante B vollstaendig entfallen.
    assert summary["technical_9910_total"] == 0


def test_soll_ist_koepfe_engine_matches_reference_in_30_random_runs():
    basis = load_soll_ist_koepfe_basis().copy()
    rng = random.Random(42)

    org_units = sorted(
        basis["Kürzel OrgEinheit"]
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
        .unique()
        .tolist()
    )
    org_candidates = [unit for unit in org_units if unit]

    summary_keys = [
        "rows_total_after_exclusions",
        "regular_total",
        "regular_occupied",
        "regular_vacant",
        "regular_no_soll_eg_total",
        "regular_no_soll_eg_occupied",
        "matrix_total",
        "matrix_occupied",
        "matrix_unbesetzt",
        "matrix_not_found",
        "technical_total",
        "technical_9xxx_total",
        "technical_9xxx_occupied",
        "technical_non9xxx_total",
        "technical_non9xxx_occupied",
        "technical_9910_total",
    ]

    for _ in range(30):
        sample_size = rng.randint(0, min(8, len(org_candidates)))
        sampled_units = rng.sample(org_candidates, sample_size)
        exclusions = {
            "org_units": sampled_units,
            "vorstand": rng.choice([True, False]),
            "ruhend_bv": rng.choice([True, False]),
        }
        use_max_eg = rng.choice([True, False])

        actual = build_soll_ist_koepfe_result(use_max_eg=use_max_eg, exclusions=exclusions)
        expected_summary, expected_pivot, expected_no_soll_eg_row = _reference_result_from_basis(
            basis,
            exclusions=exclusions,
            use_max_eg=use_max_eg,
        )

        for key in summary_keys:
            assert actual["summary"][key] == expected_summary[key], (key, exclusions, use_max_eg)

        pd.testing.assert_frame_equal(actual["pivot"], expected_pivot)
        pd.testing.assert_series_equal(
            actual["no_soll_eg_row"].sort_index(),
            expected_no_soll_eg_row.sort_index(),
            check_names=False,
        )


def test_soll_ist_koepfe_engine_matches_current_deep_dive_settings():
    exclusions = get_setting("exclusions", {})
    assert exclusions.get("vorstand") is True
    assert exclusions.get("ruhend_bv") is True
    assert exclusions.get("planstellen_follow_person") is True

    result = build_soll_ist_koepfe_result()
    summary = result["summary"]

    expected = {
        "rows_total_after_exclusions": 1328,
        "regular_total": 984,
        "regular_occupied": 896,
        "regular_vacant": 88,
        "regular_no_soll_eg_total": 1,
        "regular_no_soll_eg_occupied": 1,
        "matrix_total": 983,
        "matrix_occupied": 895,
        "matrix_unbesetzt": 88,
        "matrix_not_found": 0,
        "technical_total": 344,
        "technical_9xxx_total": 0,
        "technical_9xxx_occupied": 0,
        "technical_non9xxx_total": 344,
        "technical_non9xxx_occupied": 87,
        "technical_9910_total": 0,
    }

    assert summary == expected


def test_load_soll_ist_koepfe_basis_falls_back_to_synthetic_raw_data(monkeypatch):
    import dataloader.soll_ist_koepfe_engine as engine

    engine._load_raw_source_data_cached.clear()
    engine._load_soll_ist_koepfe_basis_cached.clear()

    monkeypatch.setattr(engine.st, "session_state", {})

    def fail_original(*args, **kwargs):
        raise FileNotFoundError("Original-Daten nicht gefunden:\nmissing")

    synthetic_files = {
        "Mitarbeiter": pd.DataFrame(
            [
                {
                    "PersNr": 1,
                    "Eintritt": pd.Timestamp("2020-01-01"),
                    "Austritt": pd.NaT,
                    "TrfGr": "E9A",
                    "MitarbGruppenbez.": "Angestellte",
                    "Status kundenindividuell": "",
                }
            ]
        ),
        "Planstellen": pd.DataFrame(
            [
                {
                    "Kürzel OrgEinheit": "300",
                    "Personalnummer": 1.0,
                    "Sollarbeitszeit": 39.0,
                    "Bewertung Tarifgruppe": "E9A",
                    "Text Gehaltsband": "bis E9A",
                },
                {
                    "Kürzel OrgEinheit": "300",
                    "Personalnummer": pd.NA,
                    "Sollarbeitszeit": 39.0,
                    "Bewertung Tarifgruppe": "E10",
                    "Text Gehaltsband": "bis E10",
                },
            ]
        ),
        "ATZ": pd.DataFrame(columns=["PersNr", "Beginn", "Ende", "Ende ATZ Vertrag"]),
        "Ausbildung": pd.DataFrame(columns=["PersNr"]),
    }

    monkeypatch.setattr(engine, "load_original_data", fail_original)
    monkeypatch.setattr(engine, "get_file_signature", lambda path: None)
    monkeypatch.setattr(engine, "generate_all_files", None, raising=False)

    from dataloader import synthetic as synthetic_module

    monkeypatch.setattr(synthetic_module, "generate_all_files", lambda: synthetic_files)

    basis = engine.load_soll_ist_koepfe_basis()

    assert len(basis) == 2
    assert basis["__has_pnr__"].tolist() == [True, False]
    assert basis["_Ist_EG"].tolist() == ["E9A", "Unbesetzt"]
