from __future__ import annotations

from utils.lineage_registry import iter_lineage_specs
from utils.transformation_lineage import (
    TRACE_STEP_IDS_BY_LINEAGE,
    TRANSFORMATION_STEPS,
    build_transformation_lineage_dataframe,
)


def test_all_configured_transformation_step_ids_exist() -> None:
    missing = {
        step_id
        for step_ids in TRACE_STEP_IDS_BY_LINEAGE.values()
        for step_id in step_ids
        if step_id not in TRANSFORMATION_STEPS
    }

    assert missing == set()


def test_every_lineage_id_has_explicit_transformation_trace() -> None:
    lineage_ids = {spec.lineage_id for spec in iter_lineage_specs()}

    assert set(TRACE_STEP_IDS_BY_LINEAGE) == lineage_ids


def test_every_lineage_id_exports_human_readable_transformation_steps() -> None:
    lineage_ids = [spec.lineage_id for spec in iter_lineage_specs()]

    df = build_transformation_lineage_dataframe(lineage_ids)

    assert set(df["Lineage-ID"]) == set(lineage_ids)
    assert df.groupby("Lineage-ID").size().min() >= 4
    assert df["Erklaerung fuer Fachanwender"].str.len().min() > 25
    assert df["Transformation / Formel"].str.len().min() > 0
    assert df["Code-Referenz"].str.len().min() > 0


def test_simulation_lineage_contains_future_snapshot_step() -> None:
    df = build_transformation_lineage_dataframe(["10-02", "11-02"])

    assert "Zukunftsbild simulieren" in set(df["Schritt"])
    assert "compact_sim_prepared_df" in " ".join(df["Ergebnisse"].astype(str))


def test_compensation_lineage_contains_domain_specific_steps() -> None:
    df = build_transformation_lineage_dataframe(["1-01", "1-02", "1-06"])

    steps = set(df["Schritt"])
    assert "Verguetungsbasis je Planstelle aufbauen" in steps
    assert "IST ohne Plan-SOLL identifizieren" in steps
    assert "Verguetungs-Fit je Entgeltgruppen-Spanne berechnen" in steps
    assert "Verguetung nach Plan- oder Entgeltgruppe aggregieren" in steps

    explanation = " ".join(df["Transformation / Formel"].astype(str))
    assert "Kapazitaetsluecke = SOLL - Summe" in explanation
    assert "Delta = IST - SOLL" in explanation


def test_soll_ist_koepfe_lineage_contains_matrix_fit_and_detail_steps() -> None:
    df = build_transformation_lineage_dataframe(["1-03", "1-04", "1-05"])

    steps = set(df["Schritt"])
    assert "Soll- und Ist-Entgeltgruppen standardisieren" in steps
    assert "Soll-Ist-Matrix zaehlen" in steps
    assert "Passung der Kopfbesetzung berechnen" in steps
    assert "Detailspanne klassifizieren" in steps
    assert "Detailaufschluesselung aggregieren" in steps

    explanation = " ".join(df["Transformation / Formel"].astype(str))
    assert "Ist-EG < untere Soll-Grenze = untergruppiert" in explanation
    assert "Ist-EG > obere Soll-Grenze = uebergruppiert" in explanation


def test_education_range_lineage_contains_ordinal_mapping_steps() -> None:
    df = build_transformation_lineage_dataframe(["1-07"])

    steps = set(df["Schritt"])
    assert "Ausbildung in Rangfolge uebersetzen" in steps
    assert "Qualifikationsspannweite je Planstelle berechnen" in steps

    explanation = " ".join(df["Transformation / Formel"].astype(str))
    assert "Mapping Ausbildung -> Bildungsrang" in explanation
    assert "mindestens zwei bekannten Ausbildungswerten" in explanation


def test_departure_lineage_contains_audit_event_step() -> None:
    df = build_transformation_lineage_dataframe(["10-04"])

    assert "Abgaenge aus Simulation ableiten" in set(df["Schritt"])
    assert "headcount_change" in " ".join(df["Eingaben"].astype(str))
    assert "MAK-Verlust" in " ".join(df["Ergebnisse"].astype(str))


def test_settings_and_analysis_quality_lineage_are_not_generic_fallbacks() -> None:
    df = build_transformation_lineage_dataframe(["2-01", "2-02", "2-06", "8-16", "8-17", "9-13"])

    steps = set(df["Schritt"])
    assert "Upload-Datenintegritaet pruefen" in steps
    assert "Upload-Template aus Spezifikation erzeugen" in steps
    assert "Jobfamily-Mapping-Report erzeugen" in steps
    assert "Tarifstruktur je Analysegruppe berechnen" in steps
    assert "Nicht zugeordnete Daten ausweisen" in steps
    assert "Sortierung, Mindestgroesse und Top-N anwenden" in steps

    assert not df["Schritt-ID"].str.endswith(".basis").any()
    assert not df["Schritt-ID"].str.endswith(".calculation").any()
