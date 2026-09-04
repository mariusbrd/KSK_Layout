from __future__ import annotations

import io

import pandas as pd

from dataloader.cluster_manager import generate_template_bytes


def test_cluster_template_contains_lineage_report():
    df_ma = pd.DataFrame(
        [
            {
                "Kürzel OrgEinheit": "100",
                "OrgEinheitNr": 100,
                "Organisationseinheit": "Markt",
                "Planstelle": "Berater/in",
            },
            {
                "Kürzel OrgEinheit": "200",
                "OrgEinheitNr": 200,
                "Organisationseinheit": "IT",
                "Planstelle": "Administrator/in",
            },
        ]
    )

    payload = generate_template_bytes(df_ma, jf_definitions={})
    workbook = pd.ExcelFile(io.BytesIO(payload))

    assert {"OrgUnits", "JobFamilies", "Lineage_Report"}.issubset(set(workbook.sheet_names))
    lineage = pd.read_excel(workbook, sheet_name="Lineage_Report")

    assert lineage["Lineage-ID"].tolist() == ["2-04"]
    assert "Organisationseinheiten=2" in lineage.loc[0, "Export-Kontext"]
