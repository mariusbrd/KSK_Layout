from __future__ import annotations

import utils.lineage as lineage
from utils import input_lineage, lineage_code, lineage_export, lineage_glossary, lineage_registry, transformation_lineage


def test_lineage_package_exposes_stable_public_api() -> None:
    assert lineage.LINEAGE_SHEET_NAME == "Lineage_Report"
    assert lineage.INPUT_LINEAGE_SHEET_NAME == "Input_Lineage"
    assert lineage.TRANSFORMATION_LINEAGE_SHEET_NAME == "Transformations_Lineage"

    assert callable(lineage.write_lineage_sheet)
    assert callable(lineage.append_lineage_sheet_to_workbook)
    assert callable(lineage.add_lineage_worksheet)
    assert callable(lineage.build_complete_glossary_workbook_bytes)
    assert callable(lineage.build_transformation_lineage_dataframe)
    assert callable(lineage.iter_lineage_specs)
    assert callable(lineage.validate_lineage_coverage)

    validation = lineage.validate_lineage_coverage()
    assert validation.is_valid


def test_legacy_lineage_import_paths_remain_compatible() -> None:
    assert lineage_registry.iter_lineage_specs is lineage.iter_lineage_specs
    assert lineage_export.write_lineage_sheet is lineage.write_lineage_sheet
    assert input_lineage.build_input_lineage_dataframe is lineage.build_input_lineage_dataframe
    assert transformation_lineage.build_transformation_lineage_dataframe is lineage.build_transformation_lineage_dataframe
    assert lineage_glossary.build_complete_glossary_workbook_bytes is lineage.build_complete_glossary_workbook_bytes
    assert hasattr(lineage_code, "build_function_lineage_index")
