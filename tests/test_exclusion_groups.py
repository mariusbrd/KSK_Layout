"""
Tests für utils/exclusion_groups.py

Testet Gruppenmasken, Aggregation und Drilldown ohne Streamlit.

Run:
  py KSK_Layout/tests/test_exclusion_groups.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np
from utils.exclusion_groups import (
    build_group_masks,
    aggregate_group,
    get_all_group_stats,
    get_group_detail,
    VORSTAND_KEY,
    RUHEND_KEY,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_df(**rows_by_oe) -> pd.DataFrame:
    """
    Erstellt einen minimalen Snapshot.
    Jeder Schlüssel ist ein OE-Kürzel, Wert = Anzahl Zeilen.
    """
    records = []
    for oe_code, count in rows_by_oe.items():
        for i in range(count):
            records.append({
                "PersNr": f"{oe_code}_{i:03d}",
                "Kürzel OrgEinheit": oe_code,
                "Organisationseinheit": f"OE {oe_code}",
                "MitarbGruppenbez.": "Angestellte",
                "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
                "Jobfamily": "Markt",
                "TrfGr": "E9A",
                "St": 3,
                "Sollarbeitszeit": 39.0,
                "Soll_FTE": 1.0,
                "MAK_Calculated": 1.0,
                "Is_Vacant": False,
            })
    return pd.DataFrame(records)


def _add_row(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    defaults = {
        "PersNr": "X001", "Kürzel OrgEinheit": "9999",
        "Organisationseinheit": "Personalrat",
        "MitarbGruppenbez.": "Angestellte",
        "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
        "Jobfamily": "Stab", "TrfGr": "E9A", "St": 3,
        "Sollarbeitszeit": 39.0, "Soll_FTE": 1.0,
        "MAK_Calculated": 1.0, "Is_Vacant": False,
    }
    defaults.update(kwargs)
    return pd.concat([df, pd.DataFrame([defaults])], ignore_index=True)


PASS = []
FAIL = []

def _check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# ===========================================================================
# Block M: Masken-Korrektheit
# ===========================================================================
print("\n--- M: Masken ---")

# M01: Vorstand wird erkannt
df_v = _make_df(**{"OE1": 3})
df_v.loc[0, "MitarbGruppenbez."] = "Vorstand"
masks_v = build_group_masks(df_v)
_check("M01_vorstand_mask_hits_one", masks_v[VORSTAND_KEY].sum() == 1,
       f"got {masks_v[VORSTAND_KEY].sum()}")

# M02: Ruhendes BV wird erkannt
df_r = _make_df(**{"OE1": 4})
df_r.loc[1, "Status kundenindividuell"] = "Ruhendes Beschäftigungsverhältnis"
df_r.loc[2, "Status kundenindividuell"] = "Ruhendes Beschäftigungsverhältnis"
masks_r = build_group_masks(df_r)
_check("M02_ruhend_mask_hits_two", masks_r[RUHEND_KEY].sum() == 2,
       f"got {masks_r[RUHEND_KEY].sum()}")

# M03: Spezifischer 99er OE-Code
df_9971 = _make_df(**{"OE1": 2, "9971": 3})
masks_9971 = build_group_masks(df_9971)
_check("M03_pa_elternzeit_mask", masks_9971["9971"].sum() == 3,
       f"got {masks_9971['9971'].sum()}")

# M04: 9900 als Ruhendes-BV OE-Kürzel (getrennt vom Status-Feld!)
df_9900 = _make_df(**{"OE1": 2, "9900": 2})
masks_9900 = build_group_masks(df_9900)
_check("M04_9900_oe_mask", masks_9900["9900"].sum() == 2,
       f"got {masks_9900['9900'].sum()}")

# M05: 99XX Catch-all (OE startet mit 99, aber nicht explizit gelistet)
df_99zz = _make_df(**{"OE1": 1, "9998": 2})  # 9998 nicht in expliziter Liste
masks_99zz = build_group_masks(df_99zz)
_check("M05_99xx_catchall", masks_99zz["99XX"].sum() == 2,
       f"got {masks_99zz['99XX'].sum()}")

# M06: Kein False-Positive – reguläre OE nicht in PA-Masken
df_reg = _make_df(**{"OE1": 5})
masks_reg = build_group_masks(df_reg)
total_pa_hits = sum(masks_reg[code].sum() for code, _ in
                    [("9900",""),("9910",""),("9971",""),("9990",""),("9999","")])
_check("M06_no_false_positives_in_regular_oe", total_pa_hits == 0,
       f"PA-Masken treffen {total_pa_hits} reguläre Zeilen")

# M07: OE-Kürzel mit Float-Suffix (z. B. aus Excel: "9971.0")
df_float = _make_df(**{"OE1": 2})
df_float = _add_row(df_float, **{"PersNr": "F001", "Kürzel OrgEinheit": "9971.0",
                                  "MitarbGruppenbez.": "Angestellte",
                                  "Status kundenindividuell": "Aktives Beschäftigungsverhältnis"})
masks_float = build_group_masks(df_float)
_check("M07_float_suffix_normalized", masks_float["9971"].sum() == 1,
       f"got {masks_float['9971'].sum()}")

# M08: Leerer DataFrame kein Crash
masks_empty = build_group_masks(pd.DataFrame())
_check("M08_empty_df_no_crash", isinstance(masks_empty, dict))

# M09: Fehlende Spalten kein Crash (partieller Snapshot)
df_partial = pd.DataFrame([{"PersNr": "X", "Organisationseinheit": "OE1"}])
masks_partial = build_group_masks(df_partial)
_check("M09_missing_columns_no_crash", VORSTAND_KEY in masks_partial)

# M10: Vorstand und Ruhendes BV können dieselbe Person NICHT doppelt treffen
#       (Status ≠ MitarbGruppenbez. - verschiedene Dimensionen)
df_combo = _make_df(**{"OE1": 3})
df_combo.loc[0, "MitarbGruppenbez."] = "Vorstand"
df_combo.loc[0, "Status kundenindividuell"] = "Ruhendes Beschäftigungsverhältnis"
masks_combo = build_group_masks(df_combo)
_check("M10_vorstand_and_ruhend_can_overlap",
       masks_combo[VORSTAND_KEY].sum() == 1 and masks_combo[RUHEND_KEY].sum() == 1)


# ===========================================================================
# Block A: Aggregation
# ===========================================================================
print("\n--- A: Aggregation ---")

df_agg = _make_df(**{"9971": 5, "OE1": 3})
# 2 Zeilen bereits exkludiert (Is_Vacant=True, MAK=0)
df_agg.loc[df_agg["Kürzel OrgEinheit"] == "9971", "Is_Vacant"] = True
df_agg.loc[df_agg["Kürzel OrgEinheit"] == "9971", "MAK_Calculated"] = 0.0
masks_agg = build_group_masks(df_agg)
row = aggregate_group(df_agg, masks_agg["9971"], "9971", "PA Elternzeit")

_check("A01_planstellen_correct", row["planstellen"] == 5, f"got {row['planstellen']}")
_check("A02_davon_exkludiert_correct", row["davon_exkludiert"] == 5,
       f"got {row['davon_exkludiert']}")
_check("A03_davon_besetzt_correct", row["davon_besetzt"] == 0,
       f"got {row['davon_besetzt']}")
_check("A04_soll_mak_preserved", row["soll_mak"] == 5.0,
       f"got {row['soll_mak']} (Sollarbeitszeit erhalten nach Exklusion)")
_check("A05_ist_mak_zeroed", row["ist_mak"] == 0.0,
       f"got {row['ist_mak']}")
_check("A06_gruppe_key_correct", row["gruppe_key"] == "9971")

# A07: Leere Gruppe gibt Nullwerte zurück, kein Crash
row_empty = aggregate_group(df_agg, pd.Series(False, index=df_agg.index), "X", "Leer")
_check("A07_empty_group_returns_zeros", row_empty["planstellen"] == 0)


# ===========================================================================
# Block S: get_all_group_stats
# ===========================================================================
print("\n--- S: get_all_group_stats ---")

df_full = _make_df(**{"OE1": 10, "9971": 3, "9999": 2})
df_full = _add_row(df_full, **{
    "PersNr": "V001", "MitarbGruppenbez.": "Vorstand",
    "Kürzel OrgEinheit": "OE1", "Sollarbeitszeit": 39.0, "Soll_FTE": 1.0,
    "MAK_Calculated": 1.0, "Is_Vacant": False,
    "Status kundenindividuell": "Aktives Beschäftigungsverhältnis"
})

stats = get_all_group_stats(df_full)
_check("S01_returns_dataframe", isinstance(stats, pd.DataFrame))
_check("S02_has_required_columns",
       all(c in stats.columns for c in ["gruppe_key", "gruppe_name", "planstellen", "soll_mak", "ist_mak"]))
_check("S03_vorstand_in_stats", (stats["gruppe_key"] == VORSTAND_KEY).any())
_check("S04_9971_has_correct_count",
       stats.loc[stats["gruppe_key"] == "9971", "planstellen"].iloc[0] == 3,
       f"got {stats.loc[stats['gruppe_key'] == '9971', 'planstellen'].values}")
_check("S05_9999_has_correct_count",
       stats.loc[stats["gruppe_key"] == "9999", "planstellen"].iloc[0] == 2)
_check("S06_vorstand_count_one",
       stats.loc[stats["gruppe_key"] == VORSTAND_KEY, "planstellen"].iloc[0] == 1)
_check("S07_empty_groups_included",
       len(stats) > 5,  # auch leere Gruppen sind drin
       f"only {len(stats)} rows")

# S08: Leerer Input kein Crash
stats_empty = get_all_group_stats(pd.DataFrame())
_check("S08_empty_input_returns_empty_df", isinstance(stats_empty, pd.DataFrame))

# S09: Soll-MAK-Summe korrekt
expected_soll = stats.loc[stats["gruppe_key"] == "9971", "soll_mak"].iloc[0]
_check("S09_soll_mak_correct_9971", expected_soll == 3.0, f"got {expected_soll}")


# ===========================================================================
# Block D: Drilldown / get_group_detail
# ===========================================================================
print("\n--- D: Drilldown ---")

df_drill = _make_df(**{"9975": 4, "OE1": 2})
detail = get_group_detail(df_drill, "9975")
_check("D01_returns_correct_rows", len(detail) == 4, f"got {len(detail)}")
_check("D02_no_sensitive_columns",
       "Personalnachname" not in detail.columns and "GebDatum" not in detail.columns)
_check("D03_unknown_group_returns_empty",
       len(get_group_detail(df_drill, "UNKNOWN_KEY")) == 0)


# ===========================================================================
# Ergebnis
# ===========================================================================
print(f"\n{'='*50}")
total = len(PASS) + len(FAIL)
print(f"Ergebnis: {len(PASS)}/{total} PASS")
if FAIL:
    print(f"\nFEHLGESCHLAGEN ({len(FAIL)}):")
    for f in FAIL:
        print(f"  - {f}")
print(f"{'='*50}")
