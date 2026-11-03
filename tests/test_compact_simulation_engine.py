import pandas as pd
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
from dataloader.compact_simulation_engine import _vacate_rows, _update_existing_rows, simulate_compact_snapshot


def test_atz_fr_keeps_headcount_on_technical_position_only_in_forecast():
    from dataloader.loader import apply_exclusions

    rows = pd.DataFrame([
        {"PersNr": "A", "Personalnummer": "A", "Is_Vacant": False,
         "Planstellennr": "50001903", "Sollarbeitszeit": 0.01, "BsGrd": 0.0,
         "MAK_Calculated": 0.0, "ist_atz_fr": True,
         "Status kundenindividuell": "Aktives Beschäftigungsverhältnis"},
        {"PersNr": "B", "Personalnummer": "B", "Is_Vacant": False,
         "Planstellennr": "50001903", "Sollarbeitszeit": 0.01, "BsGrd": 0.0,
         "MAK_Calculated": 0.0, "ist_atz_fr": False,
         "Status kundenindividuell": "Aktives Beschäftigungsverhältnis"},
        {"PersNr": "C", "Personalnummer": "C", "Is_Vacant": False,
         "Planstellennr": "50001903", "Sollarbeitszeit": 0.01, "BsGrd": 0.0,
         "MAK_Calculated": 0.0, "ist_atz_fr": True,
         "Status kundenindividuell": "Ruhendes Beschäftigungsverhältnis"},
    ])
    exclusions = {"ruhend_bv": True, "special_groups": [
        "jobfamily_validation_special_positions", "sollarbeitszeit_001_positions"]}
    old = apply_exclusions(rows, exclusions, record_counts=False)
    assert old["Is_Vacant"].eq(True).all()

    future = apply_exclusions(rows, exclusions, record_counts=False,
                              preserve_atz_fr_occupancy=True)
    assert future["Is_Vacant"].tolist() == [False, True, True]
    assert future.loc[0, "PersNr"] == "A"
    assert future.loc[0, "MAK_Calculated"] == 0.0
    assert bool(future.loc[0, "Is_Excluded"])
    assert bool(future.loc[0, "Is_IST_Preserved_Despite_Exclusion"])
    assert pd.isna(future.loc[1, "PersNr"])
    assert pd.isna(future.loc[2, "PersNr"])


def test_existing_person_keeps_two_position_assignments():
    baseline = pd.DataFrame([
        {"PersNr": "000001", "Is_Vacant": False, "Organisationseinheit": "OE Leitung",
         "Planstelle": "Leitung", "Jobfamily": "Führung Vertrieb", "JF-Cluster": "Führung Vertrieb",
         "Soll_FTE": 0.4, "BsGrd": 100.0, "MAK_Reporting": 0.4},
        {"PersNr": "000001", "Is_Vacant": False, "Organisationseinheit": "OE Beratung",
         "Planstelle": "Beratung", "Jobfamily": "Qualitative Beratung", "JF-Cluster": "Qualitative Beratung",
         "Soll_FTE": 0.6, "BsGrd": 100.0, "MAK_Reporting": 0.6},
    ])
    employee = pd.DataFrame([{"PersNr": "000001", "active": True, "mak": 1.0,
                              "Organisationseinheit": "OE Leitung", "Jobfamily": "Führung Vertrieb",
                              "JF-Cluster": "Führung Vertrieb"}])
    future = _update_existing_rows(baseline, employee, pd.Timestamp("2026-12-31"), pd.DataFrame())
    assert future["Jobfamily"].tolist() == baseline["Jobfamily"].tolist()
    assert future["Organisationseinheit"].tolist() == baseline["Organisationseinheit"].tolist()
    assert future["JF-Cluster"].tolist() == baseline["JF-Cluster"].tolist()
    assert future["MAK_Calculated"].tolist() == [0.4, 0.6]
    departed = _update_existing_rows(baseline, employee.assign(active=False, mak=0.0),
                                     pd.Timestamp("2026-12-31"), pd.DataFrame())
    assert departed["Is_Vacant"].all()


def test_existing_person_keeps_ist_mak_on_zero_soll_position():
    from dataloader.loader import calculate_mak_vectorized, apply_exclusions
    from dataloader.mak_allocation import apply_person_mak_allocation

    baseline = pd.DataFrame([
        {"PersNr": "000001", "Personalnummer": "000001", "Is_Vacant": False,
         "Organisationseinheit": "OE-A", "Planstelle": "A", "Soll_FTE": 0.0,
         "Sollarbeitszeit": 0.01, "BsGrd": 50.0, "MAK_Reporting": 0.5,
         "MAK_Calculated": 0.5, "Personen_MAK_Source": 0.5},
        {"PersNr": "000001", "Personalnummer": "000001", "Is_Vacant": False,
         "Organisationseinheit": "OE-B", "Planstelle": "B", "Soll_FTE": 0.4,
         "Sollarbeitszeit": 15.6, "BsGrd": 50.0, "MAK_Reporting": 0.5,
         "MAK_Calculated": 0.5, "Personen_MAK_Source": 0.5},
    ])
    employee = pd.DataFrame([{"PersNr": "000001", "active": True, "mak": 1.0}])
    updated = _update_existing_rows(baseline, employee, pd.Timestamp("2026-12-31"),
                                    pd.DataFrame())
    assert updated["MAK_Calculated"].tolist() == [0.5, 0.5]
    assert updated["BsGrd"].tolist() == [50.0, 50.0]
    recalculated = calculate_mak_vectorized(updated)
    excluded = apply_exclusions(
        recalculated, {"special_groups": ["sollarbeitszeit_001_positions"]},
        record_counts=False)
    reported = apply_person_mak_allocation(excluded)
    assert reported["PersNr"].tolist() == ["000001", "000001"]
    assert reported["MAK_Reporting"].tolist() == [0.5, 0.5]

    departed = _update_existing_rows(
        baseline, employee.assign(active=False, mak=0.0),
        pd.Timestamp("2026-12-31"), pd.DataFrame())
    assert departed["Is_Vacant"].all()
    assert departed["MAK_Calculated"].sum() == 0.0


def test_full_snapshot_keeps_unequal_ist_split_when_soll_split_differs():
    from dataloader.cluster_resolver import ActiveClusterSource, ClusterMappingBundle

    first = _base_snapshot()
    first["Organisationseinheit"] = "OE-A"
    first["Planstelle"] = "A"
    first["Soll_FTE"] = 0.8
    first["Sollarbeitszeit"] = 31.2
    first["BsGrd"] = 30.0
    first["MAK_Reporting"] = 0.3
    first["Personen_MAK_Source"] = 1.0
    second = first.copy()
    second["Organisationseinheit"] = "OE-B"
    second["Planstelle"] = "B"
    second["Soll_FTE"] = 0.2
    second["Sollarbeitszeit"] = 7.8
    second["BsGrd"] = 70.0
    second["MAK_Reporting"] = 0.7
    baseline = pd.concat([first, second], ignore_index=True)
    source = ActiveClusterSource(mode="synthetic", subtype="synthetic.default_fallback",
                                 source_signature="unequal-ist")
    mapping = ClusterMappingBundle(oe_map={"OE-A": "A", "OE-B": "B"},
                                   jf_map={}, source_signature="unequal-ist")
    result = simulate_compact_snapshot(
        baseline, pd.DataFrame(), pd.Timestamp("2026-12-31"),
        base_date=pd.Timestamp("2025-12-31"),
        abgaenge_params={"components": {key: False for key in
                          ("atz", "retirement", "quit", "ruhend")},
                          "random_seed": 42},
        zugaenge_params=_inactive_zugaenge_params(),
        active_cluster_source=source, cluster_mapping_bundle=mapping,
        cluster_source_signature="unequal-ist", include_audits=False)
    future = result.future_snapshot_df.set_index("Planstelle")
    assert future.loc[["A", "B"], "MAK_Reporting"].tolist() == [0.3, 0.7]
    assert future.loc[["A", "B"], "Is_Vacant"].eq(False).all()


def test_full_simulation_keeps_two_jobfamily_shares_without_events():
    from dataloader.cluster_resolver import ActiveClusterSource, ClusterMappingBundle

    first = _base_snapshot()
    first["Organisationseinheit"] = "OE1"
    first["Planstelle"] = "Leitung"
    first["Jobfamily"] = "Führung Vertrieb"
    first["JF-Cluster"] = "Führung Vertrieb"
    first["Sollarbeitszeit"] = 15.6
    first["Soll_FTE"] = 0.4
    first["MAK_Reporting"] = 0.4
    first["Personen_MAK_Source"] = 1.0
    first["BsGrd_Source"] = 100.0
    second = first.copy()
    second["Organisationseinheit"] = "OE2"
    second["Planstelle"] = "Beratung"
    second["Jobfamily"] = "Qualitative Beratung"
    second["JF-Cluster"] = "Qualitative Beratung"
    second["Sollarbeitszeit"] = 23.4
    second["Soll_FTE"] = 0.6
    second["MAK_Reporting"] = 0.6
    baseline = pd.concat([first, second], ignore_index=True)
    source = ActiveClusterSource(mode="synthetic", subtype="synthetic.default_fallback",
                                 source_signature="dual")
    mapping = ClusterMappingBundle(oe_map={"OE1": "A", "OE2": "B"},
                                   jf_map={"Leitung": "Führung Vertrieb",
                                           "Beratung": "Qualitative Beratung"},
                                   source_signature="dual")
    result = simulate_compact_snapshot(
        baseline, pd.DataFrame(), pd.Timestamp("2026-12-31"),
        base_date=pd.Timestamp("2025-12-31"),
        abgaenge_params={"components": {k: False for k in ("atz", "retirement", "quit", "ruhend")},
                         "random_seed": 42},
        zugaenge_params=_inactive_zugaenge_params(),
        active_cluster_source=source, cluster_mapping_bundle=mapping,
        cluster_source_signature="dual",
    )
    future = result.future_snapshot_df.sort_values("Planstelle")
    assert set(future["Jobfamily"]) == {"Führung Vertrieb", "Qualitative Beratung"}
    shares = future.groupby("Jobfamily")["MAK_Reporting"].sum().to_dict()
    assert shares == {"Führung Vertrieb": 0.4, "Qualitative Beratung": 0.6}


def _base_snapshot(age_years: int = 45) -> pd.DataFrame:
    base_date = pd.Timestamp("2025-12-31")
    geb = base_date - pd.DateOffset(years=age_years)

    return pd.DataFrame([
        {
            "PersNr": "000001",
            "Personalnummer": "000001",
            "Is_Vacant": False,
            "GebDatum": geb,
            "Eintritt": pd.Timestamp("2015-01-01"),
            "Austritt": pd.NaT,
            "Status kundenindividuell": "Aktives Beschäftigungsverhältnis",
            "Sollarbeitszeit": 39.0,
            "Soll_FTE": 1.0,
            "FTE_person": 1.0,
            "FTE_assigned": 1.0,
            "BsGrd": 100.0,
            "MAK_Calculated": 1.0,
            "MAK": 1.0,
            "mak": 1.0,
            "Organisationseinheit": "OE1",
            "Kürzel OrgEinheit": "1000",
            "Planstelle": "Beratung",
            "Jobfamily": "Angestellte",
            "TrfGr": "E9A",
            "St": 3,
            "Geschlecht": "w",
            "Text Gsch": "w",
            "Vertragsart": "Unbefristet",
            "MitarbGruppenbez.": "Beschäftigte",
        }
    ])


def _inactive_zugaenge_params():
    return {
        "azubi": {"active": False, "new_cases_per_year": 0, "retention_rate": 0.0},
        "trainee": {"active": False, "new_cases_per_year": 0},
        "new_hires": {"active": False, "count_per_year": 0},
        "random_seed": 42,
    }


def test_compact_simulation_reages_snapshot_without_events():
    base_date = pd.Timestamp("2025-12-31")
    target_date = pd.Timestamp("2026-12-31")

    result = simulate_compact_snapshot(
        snapshot_df=_base_snapshot(age_years=45),
        df_atz=pd.DataFrame(),
        target_date=target_date,
        base_date=base_date,
        abgaenge_params={
            "components": {
                "atz": False,
                "retirement": False,
                "quit": False,
                "ruhend": False,
            },
            "random_seed": 42,
        },
        zugaenge_params=_inactive_zugaenge_params(),
    )

    future = result.future_snapshot_df[result.future_snapshot_df["Is_Vacant"] == False].copy()

    assert len(future) == 1
    assert result.metadata["used_simulation"] is True
    assert future["Alter_Jahre"].iloc[0] > 45.9
    assert future["PersNr"].iloc[0] == "000001"


def test_compact_simulation_vacates_row_for_certain_retirement():
    base_date = pd.Timestamp("2025-12-31")
    target_date = pd.Timestamp("2026-01-31")

    result = simulate_compact_snapshot(
        snapshot_df=_base_snapshot(age_years=66),
        df_atz=pd.DataFrame(),
        target_date=target_date,
        base_date=base_date,
        abgaenge_params={
            "components": {
                "atz": False,
                "retirement": True,
                "quit": False,
                "ruhend": False,
            },
            "retirement": {
                "rent_rate_65": 1.0,
                "rent_rate_60_65": 0.0,
            },
            "random_seed": 42,
        },
        zugaenge_params=_inactive_zugaenge_params(),
    )

    future = result.future_snapshot_df

    assert future["Is_Vacant"].all()
    assert future["MAK_Calculated"].sum() == 0.0


def test_vacate_rows_handles_bool_and_integer_person_fields_without_type_error():
    df = pd.DataFrame(
        {
            "PersNr": ["000001"],
            "Personalnummer": ["000001"],
            "ATZ_Status": ["Freistellungsphase"],
            "ist_atz_fr": pd.Series([True], dtype=bool),
            "GebDatum": [pd.Timestamp("1980-01-01")],
            "Eintritt": [pd.Timestamp("2010-01-01")],
            "Austritt": [pd.NaT],
            "Alter": pd.Series([46], dtype="int64"),
            "Alter_Jahre": pd.Series([46], dtype="int64"),
            "MAK_Calculated": [1.0],
            "Is_Vacant": [False],
        }
    )

    out = _vacate_rows(df, pd.Series([True], index=df.index))

    assert bool(out["Is_Vacant"].iloc[0]) is True
    assert pd.isna(out["PersNr"].iloc[0])
    assert pd.isna(out["Personalnummer"].iloc[0])
    assert pd.isna(out["ATZ_Status"].iloc[0])
    assert bool(out["ist_atz_fr"].iloc[0]) is False
    assert pd.isna(out["GebDatum"].iloc[0])
    assert pd.isna(out["Eintritt"].iloc[0])
    assert pd.isna(out["Austritt"].iloc[0])
    assert pd.isna(out["Alter"].iloc[0])
    assert pd.isna(out["Alter_Jahre"].iloc[0])
    assert out["Alter"].dtype == "Int64"
    assert out["Alter_Jahre"].dtype == "Int64"
    assert out["MAK_Calculated"].iloc[0] == 0.0
