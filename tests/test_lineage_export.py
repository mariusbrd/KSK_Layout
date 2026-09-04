from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import zipfile

import pandas as pd
from openpyxl import Workbook
import utils.lineage_export as lineage_export

from utils.input_lineage import INPUT_LINEAGE_SHEET_NAME, build_input_lineage_dataframe
from utils.lineage_export import (
    LINEAGE_SHEET_NAME,
    append_lineage_sheet_to_workbook,
    build_lineage_export_dataframe,
    write_lineage_sheet,
)
from utils.transformation_lineage import (
    TRANSFORMATION_LINEAGE_SHEET_NAME,
    build_transformation_lineage_dataframe,
)


ROOT = Path(__file__).resolve().parents[1]


def _xlsx_with_columns(columns: list[str]) -> bytes:
    output = io.BytesIO()
    pd.DataFrame(columns=columns).to_excel(output, index=False)
    return output.getvalue()


def _load_compact_module():
    path = next((ROOT / "pages").glob("*_Kompakt.py"))
    spec = importlib.util.spec_from_file_location("compact_page_for_lineage_export_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_lineage_export_dataframe_contains_code_status():
    input_lineage = pd.DataFrame(
        [
            {
                "Input-Rolle": "Mitarbeiter",
                "Quelle-Typ": "Upload",
                "Datei": "Mitarbeiter_Test.xlsx",
                "Pfad": "",
                "Sheet": "Sheet1",
                "Header-Zeile": 1,
                "Spaltenanzahl": 2,
                "Spalten": "PersNr; GebDatum",
                "Dateisignatur": "sha256=test",
                "Ermittlungsstatus": "ok",
                "Hinweis": "",
            }
        ]
    )
    df = build_lineage_export_dataframe(
        ["9-14"],
        export_context={"Tabelle": "Rangliste Organisationseinheiten", "Zeilen": 2},
        input_lineage_df=input_lineage,
    )

    assert len(df) == 1
    row = df.iloc[0]
    assert row["Lineage-ID"] == "9-14"
    assert row["Label"] == "Organisationseinheiten Rangliste"
    assert "pages/" in row["Code-Hashes"]
    assert "#".encode().decode() in row["Code-Hashes"]
    assert "=ok" in row["Code-Drift"]
    assert "Tabelle=Rangliste Organisationseinheiten" in row["Export-Kontext"]
    assert "Mitarbeiter_Test.xlsx" in row["Excel-Input-Dateien"]
    assert "PersNr; GebDatum" in row["Excel-Input-Spalten"]


def test_input_lineage_captures_uploaded_file_name_and_columns():
    state = {
        "global_uploads": {
            "Mitarbeiter": io.BytesIO(_xlsx_with_columns(["PersNr", "GebDatum", "BsGrd"])),
        },
        "global_upload_metadata": {
            "Mitarbeiter": {"file_name": "Mitarbeiter_Upload.xlsx"},
        },
    }

    df = build_input_lineage_dataframe(session_state=state)

    row = df[df["Input-Rolle"] == "Mitarbeiter"].iloc[0]
    assert row["Quelle-Typ"] == "Upload"
    assert row["Datei"] == "Mitarbeiter_Upload.xlsx"
    assert row["Ermittlungsstatus"] == "ok"
    assert row["Spalten"] == "PersNr; GebDatum; BsGrd"


def test_input_lineage_reuses_active_cluster_source_from_session(monkeypatch):
    import dataloader.cluster_resolver as cluster_resolver

    def _unexpected_discovery(*_args, **_kwargs):
        raise AssertionError("Cluster source discovery should not run when session source is available")

    monkeypatch.setattr(cluster_resolver, "get_active_cluster_source", _unexpected_discovery)
    state = {
        "active_cluster_source": {
            "mode": cluster_resolver.MODE_SYNTHETIC,
            "subtype": cluster_resolver.SUBTYPE_SYNTHETIC_FALLBACK,
            "status": cluster_resolver.STATUS_FALLBACK,
            "is_active": True,
            "is_valid": True,
            "display_label": "Synthetisch / Fallback",
            "source_signature": "session-signature",
            "content_hash": "synthetic.default_fallback",
        }
    }

    df = build_input_lineage_dataframe(session_state=state)

    cluster_row = df[df["Input-Rolle"] == "Cluster"].iloc[0]
    assert cluster_row["Quelle-Typ"] == "Synthetischer/Fallback-Cluster"
    assert cluster_row["Dateisignatur"] == "session-signature"


def test_transformation_lineage_explains_ranking_calculation_for_laypeople():
    df = build_transformation_lineage_dataframe(
        ["9-14"],
        export_context={"Tabelle": "Rangliste Organisationseinheiten", "Kennzahl": "MAK"},
    )

    assert not df.empty
    assert df["Lineage-ID"].unique().tolist() == ["9-14"]
    assert "Excel-Eingaben erfassen" in set(df["Schritt"])
    assert "Planstellen mit Mitarbeitenden verbinden" in set(df["Schritt"])
    assert "Kennzahl je Kategorie zusammenfassen" in set(df["Schritt"])
    assert "Sortierung, Mindestgroesse und Top-N anwenden" in set(df["Schritt"])
    assert df["Erklaerung fuer Fachanwender"].str.len().min() > 30
    assert df["Eingaben"].str.len().min() > 0
    assert "Kennzahl=MAK" in df.loc[0, "Export-Kontext"]


def test_lineage_static_rows_are_cached_but_context_stays_dynamic(monkeypatch):
    lineage_export._static_lineage_rows.cache_clear()
    call_count = {"compare": 0}
    original_compare = lineage_export.compare_hash_baseline_for_refs

    def _counting_compare_hash_baseline_for_refs(refs):
        call_count["compare"] += 1
        return original_compare(refs)

    monkeypatch.setattr(lineage_export, "compare_hash_baseline_for_refs", _counting_compare_hash_baseline_for_refs)

    first = build_lineage_export_dataframe(["9-14"], export_context={"Tabelle": "A"})
    second = build_lineage_export_dataframe(["9-14"], export_context={"Tabelle": "B"})

    assert call_count["compare"] == 1
    assert "Tabelle=A" in first.loc[0, "Export-Kontext"]
    assert "Tabelle=B" in second.loc[0, "Export-Kontext"]


def test_write_lineage_sheet_appends_report_to_workbook():
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame({"Wert": [1]}).to_excel(writer, sheet_name="Daten", index=False)
        write_lineage_sheet(writer, ["8-14"], export_context={"Tabelle": "Rangliste Jobgruppen"})

    workbook = pd.ExcelFile(io.BytesIO(output.getvalue()))
    assert workbook.sheet_names == [
        "Daten",
        LINEAGE_SHEET_NAME,
        INPUT_LINEAGE_SHEET_NAME,
        TRANSFORMATION_LINEAGE_SHEET_NAME,
    ]

    lineage = pd.read_excel(workbook, sheet_name=LINEAGE_SHEET_NAME)
    input_lineage = pd.read_excel(workbook, sheet_name=INPUT_LINEAGE_SHEET_NAME)
    transformation_lineage = pd.read_excel(workbook, sheet_name=TRANSFORMATION_LINEAGE_SHEET_NAME)
    assert lineage.loc[0, "Lineage-ID"] == "8-14"
    assert lineage.loc[0, "Label"] == "Jobgruppen Rangliste"
    assert "Excel-Input-Dateien" in lineage.columns
    assert "Excel-Input-Spalten" in lineage.columns
    assert {"Input-Rolle", "Datei", "Spalten"}.issubset(set(input_lineage.columns))
    assert {"Schritt", "Erklaerung fuer Fachanwender", "Transformation / Formel"}.issubset(
        set(transformation_lineage.columns)
    )


def test_lineage_sheet_sanitizes_formula_like_context_values():
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame({"Wert": [1]}).to_excel(writer, sheet_name="Daten", index=False)
        write_lineage_sheet(writer, ["8-14"], export_context={"=Tabelle": "HYPERLINK(\"http://example\")"})

    payload = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        sheet_xml = [
            archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ]
    assert sheet_xml
    assert all("<f>" not in xml and "<f " not in xml for xml in sheet_xml)

    workbook = pd.ExcelFile(io.BytesIO(payload))
    lineage = pd.read_excel(workbook, sheet_name=LINEAGE_SHEET_NAME)
    assert ' =Tabelle=HYPERLINK("http://example")' in lineage.loc[0, "Export-Kontext"]


def test_append_lineage_sheet_to_openpyxl_workbook():
    workbook = Workbook()
    workbook.active.title = "Daten"

    append_lineage_sheet_to_workbook(
        workbook,
        ["2-01"],
        export_context={"Exporttyp": "Datenintegritaets-Evaluation"},
    )

    assert workbook.sheetnames == [
        "Daten",
        LINEAGE_SHEET_NAME,
        INPUT_LINEAGE_SHEET_NAME,
        TRANSFORMATION_LINEAGE_SHEET_NAME,
    ]
    sheet = workbook[LINEAGE_SHEET_NAME]
    headers = [cell.value for cell in sheet[1]]
    values = [cell.value for cell in sheet[2]]
    row = dict(zip(headers, values, strict=False))

    assert row["Lineage-ID"] == "2-01"
    assert row["Label"] == "Datenintegritaets-Evaluation"
    assert "Exporttyp=Datenintegritaets-Evaluation" in row["Export-Kontext"]


def test_compact_export_to_excel_can_include_lineage_report():
    compact = _load_compact_module()

    payload = compact.export_to_excel(
        pd.DataFrame({"Organisationseinheit": ["A", "B"], "Simulation": [1.0, 2.0]}),
        key_prefix="org_rangliste",
        dimension_name="Organisationseinheiten",
        value_type="MAK",
        table_title="Rangliste Organisationseinheiten",
        lineage_ids=["9-14"],
    )

    workbook = pd.ExcelFile(io.BytesIO(payload))
    assert workbook.sheet_names == [
        "Daten",
        "Dokumentation",
        LINEAGE_SHEET_NAME,
        INPUT_LINEAGE_SHEET_NAME,
        TRANSFORMATION_LINEAGE_SHEET_NAME,
    ]

    lineage = pd.read_excel(workbook, sheet_name=LINEAGE_SHEET_NAME)
    assert lineage["Lineage-ID"].tolist() == ["9-14"]
